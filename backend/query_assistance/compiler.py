"""
MedAgentPro - query assistance pipeline. Plan compiler.

Turns the output of the intent analysis stage (schema 1) into parameterised
SQLite queries. The model never writes a query (book 3.1.6, Table 7). This
module validates what the model returned and builds the query in code.

What it does, in order:
  1. Validates the intent against schema 1 (Test 13). Nothing is built if it fails.
  2. Runs the compiler checks listed for schema 1 in medagentpro_schemas.json.
  3. Injects the entitlement condition per role (NFR 17, Test 18).
  4. Injects LIMIT where the plan omits it (Table 15, "Rows a retrieval returns").
  5. Rejects an identifier the conversation never named (NFR 27, Test 45).
  6. Builds, per reading, the main query, a count query that tells whether the
     result was cut by LIMIT, and existence probes for the identifiers the plan
     names (Test 35), checked where each identifier is held (its declared references).
  7. Normalises a value compared with a column that declares its values to the declared
     form ('Open' -> 'open'), and rejects any other value (NFR 6, chat 19.10, A2).

Before retrieval the caller may resolve an identifier written in another case to the one
form the store holds (resolve_identifiers, chat 19.10, A1), and compile again.

Every rejection raises CompileError with a code and a reason. The caller records
it and tells the user the request could not be completed (Test 13).
"""

from __future__ import annotations

import copy
import functools
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

from jsonschema import Draft202012Validator

HERE = Path(__file__).parent

# PROVISIONAL. Table 15: the prompt budget of the synthesis call divided by the
# token cost of a row. Set from measurement at M2. 30 since the task 5 review (R7a,
# 12.10): about 75 tokens a row measured on the demo store, and up to three readings
# enter both the synthesis and the gate prompts. intent_validator() applies it to the
# schema bound, so the bound and the default stay one value.
ROW_LIMIT = 30

NUMERIC = {"integer", "real"}
AVG_DECIMALS = 2   # an average is rounded to this many decimals, in the query
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A name the user wrote (MATCHES, chat 20.10) [proposal, not in the book]: the "contains" stage runs only
# for a term this long or longer (V3), and the catalog says how many candidates are shown (V8)
MATCH_CONTAINS_MIN = 3
MAX_CANDIDATES_DEFAULT = 5
CMP_SQL = {"EQ": "=", "NE": "<>", "GT": ">", "GTE": ">=", "LT": "<", "LTE": "<="}
# Task 3 (92_TASK3_PLAN, chat 21.10 12:50) [proposal, not in the book]: a condition on another reading's result,
# through a declared relation, compiled over that reading's own query (QPL: each reading runs on its own)
READING_REFS = {"IN_READING": "EXISTS", "NOT_IN_READING": "NOT EXISTS"}
# Task 3, GAP-07: what the plan language cannot express. A reading that fails on one of these on the last attempt
# is left out and its part said, with what it asks for; any other code is a fault of the plan (Test 13)
LANGUAGE_LIMITS = ("reading_ref_depth", "in_reading_under_or", "duplicate_table", "in_values_limit",
                   "readings_limit")

# Task 4, A (91_COVERAGE_PLAN, chat 20.10, 22:30) [proposal, not in the book]: the words of a question that no part
# has to quote, connecting and question words, English and Hebrew (decision 7). A closed list over the question
# the person wrote, never over model text. Hebrew prefixes are not split: a span quotes them anyway
FUNCTION_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for", "from", "by", "with", "about", "as",
    "between", "is", "are", "was", "were", "be", "been", "do", "does", "did", "have", "has", "had", "which", "what",
    "who", "whom", "whose", "when", "where", "how", "i", "me", "we", "us", "you", "it", "its", "they", "them",
    "their", "this", "that", "these", "those", "there", "show", "list", "give", "tell", "please", "can", "could",
    "would", "also", "any", "each", "so", "if",
    "של", "את", "מה", "מי", "אילו", "איזה", "איזו", "האם", "כמה", "עם", "על", "גם", "או", "ו",
})
_WORD = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*")   # a word, with an inner hyphen or apostrophe: S-014, S-038's


# --------------------------------------------------------------------------
# errors and results
# --------------------------------------------------------------------------

class CompileError(Exception):
    """A plan that is not run. code is stable for tests and the audit record."""

    def __init__(self, code: str, reason: str, **detail: Any):
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.detail = detail


@dataclass
class Probe:
    """Checks whether an identifier the plan names is held at all.

    column      the column the plan filtered on
    checked_in  where existence is checked: the column's declared reference where there
                is one the user may read, otherwise the column itself
    """
    column: str
    value: Any
    sql: str
    params: list
    checked_in: str = ""
    resolve_sql: str = ""     # the stored forms equal to value ignoring case, at most two (A1)
    resolve_params: list = field(default_factory=list)


@dataclass
class NameProbe:
    """A name the user wrote, matched against a searchable column (MATCHES, chat 20.10, 81_VALUE_MATCH).

    column     the searchable column the plan named
    term       the name as the user wrote it, a span of the question
    id_column  the entity's own identifier: candidates are counted and named by it (B), never by the name
    stages     (stage, values_sql, values_params, count_sql, count_params): 1 equal ignoring case,
               2 starts with, 3 contains. Each with the user's entitlement (NFR 17, V6), the term a parameter
    """
    column: str
    term: str
    path: str
    id_column: str
    stages: list[tuple[int, str, list, str, list]] = field(default_factory=list)


@dataclass
class NameMatch:
    """How a name was resolved: the first stage that found anything, how many entities, and up to
    max_candidates of them as (identifier, name). The values are for the screen only (V5)."""
    stage: int | None
    count: int
    candidates: list[tuple[Any, Any]] = field(default_factory=list)


class UnresolvedName(Exception):
    """A reading still holding MATCHES is never run (chat 20.10, A): the name must be resolved first."""


@dataclass
class Conversation:
    """What a plan may draw identifiers from (NFR 27).

    question   the question as posed now
    rewritten  the self-contained question from the rewriting stage. Spans may come
               from it, identifiers may not, since the model wrote it
    earlier    earlier questions as posed and earlier answers as presented.
               A summary of reduced exchanges is never passed here
    presented  identifiers shown in earlier answers
    """
    question: str
    rewritten: str | None = None
    earlier: list[str] = field(default_factory=list)
    presented: set = field(default_factory=set)


@dataclass
class CompiledReading:
    label: str
    sql: str
    params: list
    count_sql: str
    count_params: list
    limit: int
    limit_injected: bool
    probes: list[Probe] = field(default_factory=list)
    names: list[NameProbe] = field(default_factory=list)   # MATCHES not yet resolved: never run (chat 20.10, A)
    # task 3: this reading's query as another reading's condition reads it, the LIMIT kept only where the plan
    # wrote one ("the five ... and their ..."); and the column behind each plain alias
    inner_sql: str = ""
    inner_params: list = field(default_factory=list)
    columns: dict[str, str | None] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)   # task 3: the readings its conditions read


@dataclass
class CompiledIntent:
    disposition: str
    readings: list[CompiledReading] = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    intent: dict = field(default_factory=dict)   # the intent compiled, values normalised (A2)
    uncovered: list[str] = field(default_factory=list)   # task 4, A: parts of the question no asked quotes
    # task 3, GAP-07: readings left out on the last attempt for a limit of the language: {label, asked, code}
    dropped: list[dict] = field(default_factory=list)


@dataclass
class RetrievalResult:
    label: str
    outcome: str            # rows_returned | empty_no_match | empty_unknown_entity
    rows: list[dict]
    total: int              # rows the query matches before LIMIT
    truncated: bool         # total > len(rows). Shown as "N of total"
    missing: list = field(default_factory=list)   # identifiers not held


# --------------------------------------------------------------------------
# catalog: schema.json and relations.json
# --------------------------------------------------------------------------

class Catalog:
    def __init__(self, schema: dict, relations: dict):
        self.entities = schema["entities"]
        self.metrics = schema.get("metrics", {})
        # The date the data runs to (YYYY-MM-DD), where the store declares one: the demo's reference date
        self.data_as_of: str | None = schema.get("data_as_of")
        # What the screen says the store is about (chat 19.10, C) [demo extension]: name, description,
        # placeholder, can_ask. Text for the interface only; no stage reads it
        self.domain: dict = {k: v for k, v in schema.get("domain", {}).items() if not k.startswith("$")}
        self.relations = relations["relations"]
        # How many candidates a name with several matches shows (chat 20.10, V8)
        self.max_candidates: int = schema.get("max_candidates", MAX_CANDIDATES_DEFAULT)
        for table, ent in self.entities.items():
            for col, spec in ent["columns"].items():
                if spec.get("searchable"):
                    # B (chat 20.10): matches are counted by the entity's own identifier. A searchable
                    # column in a table with none is an open question (00_STATUS), so it is refused here
                    if spec.get("type") != "text" or len(self._own_ids(table)) != 1:
                        raise ValueError(f"schema.json: {table}.{col} is searchable, so it must be text and "
                                         f"{table} must hold exactly one identifier of its own")
                ref = spec.get("references")
                if ref is None:
                    continue
                target = self.column(ref)
                if not spec.get("identifier") or target is None or not target.get("identifier"):
                    raise ValueError(f"schema.json: {table}.{col} references '{ref}', which must be "
                                     f"an identifier column, and so must {table}.{col}")

    @classmethod
    def load(cls, schema_path: str | Path, relations_path: str | Path) -> Catalog:
        """The catalog of one store. No default: a catalog is the store's, never the code's (chat 19.10, C)."""
        return cls(json.loads(Path(schema_path).read_text(encoding="utf-8")),
                   json.loads(Path(relations_path).read_text(encoding="utf-8")))

    def column(self, qualified: str) -> dict | None:
        table, _, col = qualified.partition(".")
        ent = self.entities.get(table)
        return ent["columns"].get(col) if ent else None

    def _own_ids(self, table: str) -> list[str]:
        return [c for c, s in self.entities[table]["columns"].items()
                if s.get("identifier") and not s.get("references")]

    def entity_id(self, table: str) -> str:
        """The identifier a table holds of its own (suppliers.supplier_id), qualified."""
        return f"{table}.{self._own_ids(table)[0]}"

    def resolves(self, name: str) -> bool:
        """A table name or table.column held in schema.json."""
        if "." in name:
            return self.column(name) is not None
        return name in self.entities


@functools.lru_cache(maxsize=None)
def intent_validator(bundle_path=HERE / "medagentpro_schemas.json", row_limit=ROW_LIMIT):
    """Built once per bundle and row limit: building it costs more than compiling a plan. The
    validator is shared, so its schema is read, never changed."""
    bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
    schema = copy.deepcopy(bundle["schemas"]["intent_analysis"]["schema"])
    schema["$defs"]["plan"]["properties"]["LIMIT"]["maximum"] = row_limit
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _q(name: str) -> str:
    """Quote an identifier. Names are already restricted by schema 1."""
    return '"' + name.replace('"', '""') + '"'


def _literal_fits(col_type: str, v: Any) -> bool:
    if col_type == "boolean":
        return isinstance(v, bool)
    if isinstance(v, bool):
        return False
    if col_type == "integer":
        return isinstance(v, int) or (isinstance(v, float) and v.is_integer())
    if col_type == "real":
        return isinstance(v, (int, float))
    if col_type == "text":
        return isinstance(v, str)
    if col_type == "date":
        if not isinstance(v, str) or not DATE_RE.match(v):
            return False
        try:
            date.fromisoformat(v)
            return True
        except ValueError:
            return False
    return False


def _types_match(a: str, b: str) -> bool:
    return a == b or (a in NUMERIC and b in NUMERIC)


@dataclass
class Expr:
    sql: str
    type: str | None          # None for a literal, which takes its type from the other side
    params: list
    is_agg: bool = False
    literal: Any = None
    is_literal: bool = False
    column: str | None = None  # set when the expression is a plain column
    is_me: bool = False        # D7: the asker's own user_id, filled here, never written by the model


# --------------------------------------------------------------------------
# compiling one reading
# --------------------------------------------------------------------------

class _ReadingCompiler:
    def __init__(self, cat: Catalog, user: dict, reading: dict, row_limit: int,
                 inner: dict[str, CompiledReading] | None = None, others: frozenset[str] = frozenset()):
        self.cat = cat
        self.user = user
        self.label = reading["label"]
        self.plan = reading["plan"]
        self.row_limit = row_limit
        self.inner = inner or {}          # task 3: the readings this one may read, compiled
        self.others = others              # task 3: every other label of the plan
        self.depends_on: list[str] = []
        self.inner_names: list[NameProbe] = []
        self.scope: dict[str, str] = {}          # table -> FROM | INNER | LEFT
        self.join_sql: list[tuple[str, list]] = []
        self.aliases: dict[str, Expr] = {}
        self.identities: list[tuple[str, Any]] = []   # probed when the result is empty
        self.named_ids: list[tuple[str, Any, str]] = []   # every identifier literal and its path, for NFR 27
        self.names: list[tuple[str, str, str]] = []   # MATCHES: (column, term, path), chat 20.10

    def fail(self, code: str, reason: str, **detail: Any) -> NoReturn:
        raise CompileError(code, reason, label=self.label, **detail)

    # --- scope -----------------------------------------------------------

    def build_scope(self):
        base = self.plan["FROM"]
        if base not in self.cat.entities:
            self.fail("unknown_table", f"Table '{base}' is not in schema.json.", table=base)
        self.scope[base] = "FROM"
        for j in self.plan.get("JOIN", []):
            rel = self.cat.relations.get(j["rel"])
            if rel is None:
                self.fail("unknown_relation", f"Relation '{j['rel']}' is not in relations.json.", rel=j["rel"])
            if rel["from"] in self.scope and rel["to"] not in self.scope:
                new = rel["to"]
            elif rel["to"] in self.scope and rel["from"] not in self.scope:
                new = rel["from"]
            elif rel["from"] in self.scope and rel["to"] in self.scope:
                self.fail("duplicate_table", f"Relation '{j['rel']}' joins a table already in the query.", rel=j["rel"])
            else:
                self.fail("relation_out_of_scope",
                          f"Relation '{j['rel']}' does not connect to a table already in the query.", rel=j["rel"])
            self.scope[new] = j["type"]
            on = " AND ".join(f"{self._colsql(a)} = {self._colsql(b)}" for a, b in rel["on"])
            self.join_sql.append((f"{j['type']} JOIN {_q(new)} ON {on}", []))

    def _colsql(self, qualified: str) -> str:
        t, _, c = qualified.partition(".")
        return f"{_q(t)}.{_q(c)}"

    # --- expressions -----------------------------------------------------

    def col(self, qualified: str) -> Expr:
        spec = self.cat.column(qualified)
        if spec is None:
            self.fail("unknown_column", f"Column '{qualified}' is not in schema.json.", col=qualified)
        table = qualified.split(".")[0]
        if table not in self.scope:
            self.fail("column_out_of_scope",
                      f"Column '{qualified}' belongs to a table that is neither the FROM table nor joined.",
                      col=qualified)
        return Expr(self._colsql(qualified), spec["type"], [], column=qualified)

    def metric(self, name: str) -> Expr:
        m = self.cat.metrics.get(name)
        if m is None:
            self.fail("unknown_metric", f"Metric '{name}' is not declared in schema.json.", metric=name)
        if m["entity"] not in self.scope:
            self.fail("metric_out_of_scope",
                      f"Metric '{name}' is defined on '{m['entity']}', which is not in the query.", metric=name)
        return Expr("(" + self._metric_expr(m["expr"]) + ")", m["type"], [])

    def _metric_expr(self, node: dict) -> str:
        """Metric expressions come from schema.json, never from the model."""
        if "col" in node:
            return self._colsql(node["col"])
        a, b = (self._metric_expr(x) for x in node["args"])
        op = node["op"]
        if op in CMP_SQL:
            return f"{a} {CMP_SQL[op]} {b}"
        if op == "SUB":
            return f"{a} - {b}"
        if op == "DAYS_BETWEEN":
            return f"julianday({b}) - julianday({a})"
        raise ValueError(f"schema.json: unknown metric operator {op}")

    def func(self, node: dict) -> Expr:
        name, arg = node["func"], node["arg"]
        if arg == "*":
            return Expr("COUNT(*)", "integer", [], is_agg=True)
        if "metric" in arg:   # SUM, AVG, MIN, MAX over a numeric metric (chat, 18.10, 13:15)
            inner = self.metric(arg["metric"])
            if inner.type not in NUMERIC:
                self.fail("type_mismatch", f"{name} over metric '{arg['metric']}', which is not numeric. "
                          "Count a boolean metric with COUNT and WHERE.", metric=arg["metric"])
        else:
            inner = self.col(arg["col"])
        if name == "COUNT":
            return Expr(f"COUNT({inner.sql})", "integer", [], is_agg=True)
        if name in ("SUM", "AVG") and inner.type not in NUMERIC:
            self.fail("type_mismatch", f"{name} over '{arg['col']}', which is not numeric.", col=arg["col"])
        if name == "AVG":   # rounded in code, to two decimals (chat, 18.10, 13:05)
            return Expr(f"ROUND(AVG({inner.sql}), {AVG_DECIMALS})", "real", [], is_agg=True)
        return Expr(f"{name}({inner.sql})", inner.type, [], is_agg=True)

    def operand(self, node: dict) -> Expr:
        if "col" in node:
            return self.col(node["col"])
        if "metric" in node:
            return self.metric(node["metric"])
        if "func" in node:
            return self.func(node)
        if "me" in node:   # D7 (fix round): "my", "mine", "I"; the value is the asker's, from the session
            me = self.user.get("user_id")
            return Expr("?", None, [me], literal=me, is_literal=True, is_me=True)
        return Expr("?", None, [node["value"]], literal=node["value"], is_literal=True)

    # --- predicates ------------------------------------------------------

    def _check_literal(self, side: Expr, lit: Any, op: str):
        if side.type is None or not _literal_fits(side.type, lit):
            self.fail("type_mismatch",
                      f"{op} compares {side.column or 'a metric'} ({side.type}) with {lit!r}.",
                      value=lit, against=side.column)

    def _spec(self, qualified: str) -> dict:
        """The column's spec. Only for a column already resolved (col() fails on an unknown one)."""
        spec = self.cat.column(qualified)
        if spec is None:
            self.fail("unknown_column", f"Column '{qualified}' is not in schema.json.", col=qualified)
        return spec

    def _is_identifier(self, side: Expr) -> bool:
        return bool(side.column and self._spec(side.column).get("identifier"))

    def _note_identity(self, side: Expr, values: list):
        if side.column and self._is_identifier(side):
            for v in values:
                self.identities.append((side.column, v))

    def _note_named(self, side: Expr, values: list, path: str):
        if side.column and self._is_identifier(side):
            for v in values:
                self.named_ids.append((side.column, v, path))

    def predicate(self, node: dict, top: bool = True, path: str = "WHERE") -> tuple[str, list]:
        (op, arg), = node.items()
        if op in ("AND", "OR"):
            # An identifier under AND still restricts the result. Under OR it does not.
            parts = [self.predicate(p, top=top and op == "AND", path=f"{path}/{op}/{i}")
                     for i, p in enumerate(arg)]
            sql = f" {op} ".join(f"({s})" for s, _ in parts)
            return sql, [p for _, ps in parts for p in ps]
        if op in CMP_SQL:
            a, b = (self.operand(x) for x in arg)
            if a.is_literal and b.is_literal:
                self.fail("no_column_in_comparison", f"{op} compares two values and no column.")
            if a.is_me or b.is_me:
                side = b if a.is_me else a
                if op not in ("EQ", "NE") or not side.column or not self._spec(side.column).get("user_column"):
                    self.fail("me_not_allowed", f"The current user may be compared only for equality with a "
                                                f"column that holds a user, not with {side.column or 'a value'}.",
                              against=side.column)
                return f"{a.sql} {CMP_SQL[op]} {b.sql}", a.params + b.params
            if a.is_literal or b.is_literal:
                side, lit = (b, a.literal) if a.is_literal else (a, b.literal)
                self._check_literal(side, lit, op)
                self._note_named(side, [lit], path)
                if op == "EQ" and top:
                    self._note_identity(side, [lit])
            elif a.type is None or b.type is None or not _types_match(a.type, b.type):
                self.fail("type_mismatch", f"{op} compares {a.type} with {b.type}.")
            return f"{a.sql} {CMP_SQL[op]} {b.sql}", a.params + b.params
        if op == "IN":   # an object, not a tuple (task 10a, 15.10): {"col", "values"}
            c = self.col(arg["col"])
            vals = [v["value"] for v in arg["values"]]
            for v in vals:
                self._check_literal(c, v, "IN")
            self._note_named(c, vals, path)
            if top:
                self._note_identity(c, vals)
            return f"{c.sql} IN ({', '.join('?' for _ in vals)})", vals
        if op == "BETWEEN":   # an object, not a tuple (task 10a, 15.10): {"col", "from", "to"}
            c = self.col(arg["col"])
            lo, hi = arg["from"]["value"], arg["to"]["value"]
            self._check_literal(c, lo, "BETWEEN")
            self._check_literal(c, hi, "BETWEEN")
            self._note_named(c, [lo, hi], path)
            return f"{c.sql} BETWEEN ? AND ?", [lo, hi]
        if op == "MATCHES":   # a name the user wrote (chat 20.10) [proposal, not in the book]
            c = self.col(arg["col"])
            if not self._spec(arg["col"]).get("searchable"):
                self.fail("match_not_searchable",
                          f"MATCHES at {path} is on '{arg['col']}', which schema.json does not mark searchable. "
                          f"A name may be matched only on a searchable column.", path=path, col=arg["col"])
            # V1 relaxed (task 3, 40_CHAT section 17): anywhere in WHERE. A name resolved to one entity is
            # written as EQ on its identifier, under OR too; one not resolved stops the whole reading
            self.names.append((arg["col"], arg["term"], path))
            # never run as written: execute refuses a reading whose names are not resolved (A)
            return f"{c.sql} = ?", [arg["term"]]
        if op in READING_REFS:
            return self.reading_ref(op, arg, top, path)
        if op in ("IS_NULL", "IS_NOT_NULL"):
            c = self.col(arg["col"])
            if arg["col"].split(".")[0] != self.plan["FROM"]:
                self.fail("null_test_on_joined",
                          f"{op} on '{c.column}', which is not a column of the FROM table.", col=c.column)
            return f"{c.sql} {'IS NULL' if op == 'IS_NULL' else 'IS NOT NULL'}", []
        raise AssertionError(op)  # schema 1 admits no other operator

    def _entity(self, qualified: str) -> str | None:
        """The entity a column identifies: the identifier it declares references to, or its own table's
        identifier where it is that. None for any other column."""
        spec = self.cat.column(qualified) or {}
        if spec.get("references"):
            return spec["references"]
        table = qualified.partition(".")[0]
        if spec.get("identifier") and self.cat._own_ids(table) and self.cat.entity_id(table) == qualified:
            return qualified
        return None

    def reading_ref(self, op: str, arg: dict, top: bool, path: str) -> tuple[str, list]:
        """Task 3: the record's column against the rows of another reading of the plan, through a declared
        relation (both columns identify the same entity), over that reading's own query: its entitlement, its
        parameters, and its LIMIT where the plan wrote one. Depth 1, under AND from the root only."""
        label, alias = arg["reading"], arg["as"]
        if label == self.label:
            self.fail("reading_self", f"{op} at {path} names this reading itself.", path=path)
        if label not in self.others:
            self.fail("unknown_reading", f"{op} at {path} names '{label}', which is not a reading of this plan.",
                      path=path, reading=label)
        inner = self.inner.get(label)
        if inner is None:
            self.fail("reading_ref_depth", f"{op} at {path} names '{label}', which itself reads another reading. "
                      f"A reading may read only a reading that reads none.", path=path, reading=label)
        if not top:
            self.fail("in_reading_under_or", f"{op} at {path} is not under AND from the root of WHERE. A condition "
                      f"on another reading may be only one every record meets.", path=path)
        source = inner.columns.get(alias)
        if source is None:
            self.fail("reading_ref_alias", f"{op} at {path}: '{alias}' is not a plain column of reading '{label}'. "
                      f"Name an alias of '{label}' that selects a column, not an aggregate or a metric.",
                      path=path, reading=label)
        c = self.col(arg["col"])
        mine, theirs = self._entity(arg["col"]), self._entity(source)
        if mine is None or mine != theirs:
            self.fail("reading_ref_relation", f"{op} at {path} compares '{arg['col']}' with '{alias}' of "
                      f"'{label}', which do not identify the same record. Compare columns that hold the same "
                      f"identifier.", path=path, reading=label)
        if label not in self.depends_on:
            self.depends_on.append(label)
            self.inner_names += inner.names   # a name the reading read has not resolved: neither runs (2e)
        # bandit B608: catalog names, quoted; every value a bound parameter (tests/security, tests/property)
        sub = f'SELECT 1 FROM ({inner.inner_sql}) AS "r" WHERE "r".{_q(alias)} = {c.sql}'  # nosec B608
        return f"{READING_REFS[op]} ({sub})", list(inner.inner_params)

    # --- entitlement (NFR 17) --------------------------------------------

    def entitlement(self, table: str) -> tuple[str, list] | None:
        ent = self.cat.entities[table]
        role = self.user.get("role")
        rules = ent.get("entitlement")
        if rules is None:
            self.fail("entitlement_undeclared",
                      f"Table '{table}' declares no entitlement rule in schema.json, so access to it "
                      f"cannot be restricted by user. The request was not run.", table=table)
        rule = rules.get(role)
        if rule is None:
            self.fail("entitlement_denied", f"Role '{role}' has no access to table '{table}'.",
                      table=table, role=role)
        if rule.get("all") is True:
            return None
        attr = rule["equals"]
        if attr not in self.user:
            self.fail("entitlement_unresolved",
                      f"The rule for '{table}' needs the user's '{attr}', which the session does not carry.",
                      table=table)
        return f"{_q(table)}.{_q(rule['col'])} = ?", [self.user[attr]]

    # --- the whole reading -----------------------------------------------

    def compile(self) -> CompiledReading:
        p = self.plan
        self.build_scope()

        # SELECT
        select_sql, select_params = [], []
        for item in p["SELECT"]:
            alias = item["as"]
            if alias in self.aliases:
                self.fail("duplicate_alias", f"Alias '{alias}' is used twice.", alias=alias)
            e = self.operand(item["expr"])
            self.aliases[alias] = e
            select_sql.append(f"{e.sql} AS {_q(alias)}")
            select_params += e.params

        # GROUP_BY: required when aggregates and plain fields are mixed
        aggs = {a for a, e in self.aliases.items() if e.is_agg}
        plain = [a for a, e in self.aliases.items() if not e.is_agg]
        group = p.get("GROUP_BY")
        if group is not None:
            if not aggs:
                self.fail("group_by_mismatch", "GROUP_BY without an aggregate in SELECT.")
            for g in group:
                if g not in self.aliases:
                    self.fail("unknown_alias", f"GROUP_BY names '{g}', which is not a SELECT alias.", alias=g)
                if g in aggs:
                    self.fail("group_by_mismatch", f"GROUP_BY names '{g}', which is an aggregate.", alias=g)
        if aggs and plain and set(group or []) != set(plain):
            missing = sorted(set(plain) - set(group or []))
            self.fail("group_by_mismatch",
                      f"Fields {missing} are neither aggregated nor in GROUP_BY.", aliases=missing)

        # Entitlement: WHERE for FROM and INNER, ON for LEFT so the join stays a left join
        where_parts: list[tuple[str, list]] = []
        joins: list[tuple[str, list]] = []
        for (jsql, jparams), (table, kind) in zip(self.join_sql, list(self.scope.items())[1:], strict=True):
            ent = self.entitlement(table)
            if ent and kind == "LEFT":
                jsql, jparams = f"{jsql} AND {ent[0]}", jparams + ent[1]
            elif ent:
                where_parts.append(ent)
            joins.append((jsql, jparams))
        base_ent = self.entitlement(p["FROM"])

        model_where = self.predicate(p["WHERE"]) if "WHERE" in p else None
        if model_where:
            where_parts.insert(0, model_where)
        if base_ent:
            where_parts.append(base_ent)

        # HAVING
        having_sql, having_params = [], []
        for h in p.get("HAVING", []):
            agg = self.aliases.get(h["agg"])
            if agg is None or not agg.is_agg:
                self.fail("having_not_aggregate", f"HAVING names '{h['agg']}', which is not an aggregate alias.",
                          alias=h["agg"])
            if agg.type not in NUMERIC:
                self.fail("type_mismatch", f"HAVING compares '{h['agg']}' ({agg.type}) with a number.")
            having_sql.append(f"{agg.sql} {CMP_SQL[h['op']]} ?")
            having_params.append(h["value"])

        # ORDER_BY, then every remaining alias, so that a cut by LIMIT is repeatable
        order = []
        for o in p.get("ORDER_BY", []):
            if o["by"] not in self.aliases:
                self.fail("unknown_alias", f"ORDER_BY names '{o['by']}', which is not a SELECT alias.", alias=o["by"])
            order.append(f"{_q(o['by'])} {o['dir']}")
        named = {o["by"] for o in p.get("ORDER_BY", [])}
        order += [f"{_q(a)} ASC" for a in self.aliases if a not in named]

        limit = p.get("LIMIT", self.row_limit)
        limit_injected = "LIMIT" not in p

        # assemble
        # bandit B608: identifiers from the catalog, quoted; every value is a bound parameter
        # (tests/security, tests/property)
        body = f"SELECT {', '.join(select_sql)} FROM {_q(p['FROM'])}"  # nosec B608
        params = list(select_params)
        for jsql, jparams in joins:
            body += f" {jsql}"
            params += jparams
        if where_parts:
            body += " WHERE " + " AND ".join(f"({s})" for s, _ in where_parts)
            params += [x for _, ps in where_parts for x in ps]
        if group:
            body += " GROUP BY " + ", ".join(self.aliases[g].sql for g in group)
        if having_sql:
            body += " HAVING " + " AND ".join(having_sql)
            params += having_params

        sql = f"{body} ORDER BY {', '.join(order)} LIMIT ?"
        # bandit B608: identifiers from the catalog, quoted; every value is a bound parameter
        # (tests/security, tests/property)
        count_sql = f"SELECT COUNT(*) FROM ({body})"  # nosec B608
        # task 3: as another reading reads it. An injected LIMIT is what fits on the screen, not what was asked
        inner_sql, inner_params = (body, list(params)) if limit_injected else (sql, params + [limit])
        columns = {a: (e.column if not e.is_agg else None) for a, e in self.aliases.items()}
        return CompiledReading(self.label, sql, params + [limit], count_sql, list(params),
                               limit, limit_injected, self._probes(), self._name_probes() + self.inner_names,
                               inner_sql, inner_params, columns, list(self.depends_on))

    def _name_probes(self) -> list[NameProbe]:
        """For each MATCHES, the three stages, each counting and naming entities by the table's own
        identifier (B), within what the user may see (NFR 17, V6). The term is always a parameter."""
        out = []
        for column, term, path in self.names:
            table, _, col = column.partition(".")
            id_column = self.cat.entity_id(table)
            qt, qc, qi = _q(table), f"{_q(table)}.{_q(col)}", f"{_q(table)}.{_q(id_column.split('.')[1])}"
            ent = self.entitlement(table)
            like = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds = [(1, f"{qc} = ? COLLATE NOCASE", term),
                     (2, f"{qc} LIKE ? ESCAPE '\\'", like + "%")]
            if len(term) >= MATCH_CONTAINS_MIN:   # V3
                conds.append((3, f"{qc} LIKE ? ESCAPE '\\'", "%" + like + "%"))
            stages = []
            for stage, cond, value in conds:
                where, params = [cond], [value]
                if ent:
                    where.append(ent[0])
                    params += ent[1]
                w = " AND ".join(f"({s})" for s in where)
                # bandit B608: identifiers from the catalog, quoted; every value is a bound parameter
                values_sql = f"SELECT DISTINCT {qi}, {qc} FROM {qt} WHERE {w} ORDER BY {qi} LIMIT ?"  # nosec B608
                count_sql = f"SELECT COUNT(DISTINCT {qi}) FROM {qt} WHERE {w}"  # nosec B608
                stages.append((stage, values_sql, params + [self.cat.max_candidates], count_sql, list(params)))
            out.append(NameProbe(column, term, path, id_column, stages))
        return out

    def _probe_target(self, column: str) -> tuple[str, tuple[str, list] | None]:
        """Where an identifier's existence is checked, and the entitlement condition there.

        A column that declares references (commitments.supplier_id -> suppliers.supplier_id)
        is checked where the identifier is held, so a supplier with no lines reads as held
        with nothing matching, not as absent. The reference is used only where the user may
        read its table. Otherwise the column itself is checked, as before."""
        ref = self._spec(column).get("references")
        if ref:
            try:
                return ref, self.entitlement(ref.split(".")[0])
            except CompileError:
                pass
        return column, self.entitlement(column.split(".")[0])

    def _probes(self) -> list[Probe]:
        """One probe per identifier value: is it held at all, within what the user may see."""
        out = []
        for column, value in self.identities:
            target, ent = self._probe_target(column)
            table, _, col = target.partition(".")
            where = [f"{_q(table)}.{_q(col)} = ?"]
            params = [value]
            if ent:
                where.append(ent[0])
                params += ent[1]
            # bandit B608: identifiers from the catalog, quoted; every value is a bound parameter
            # (tests/security, tests/property)
            sql = f"SELECT 1 FROM {_q(table)} WHERE {' AND '.join(where)} LIMIT 1"  # nosec B608
            # A1 (chat 19.10): the same lookup ignoring case, for the stored forms (ASCII folding,
            # SQLite's NOCASE); the entitlement condition applies as in the probe
            loose = [f"{_q(table)}.{_q(col)} = ? COLLATE NOCASE"] + where[1:]
            resolve_sql = (f"SELECT DISTINCT {_q(table)}.{_q(col)} FROM {_q(table)} "  # nosec B608
                           f"WHERE {' AND '.join(loose)} LIMIT 2")
            out.append(Probe(column, value, sql, params, target, resolve_sql, params))
        return out


# --------------------------------------------------------------------------
# intent-level checks and entry points
# --------------------------------------------------------------------------

def _words(text: str) -> list[re.Match]:
    return list(_WORD.finditer(text))


def uncovered(question: str, spans: list[str]) -> list[str]:
    """Task 4, A: the parts of the question as posed that no span quotes. A word counts unless it is a
    FUNCTION_WORD; it is covered where it is a word of some span. Each part is a run of uncovered words, with the
    function words between them, as the question writes it. Empty where every word is covered."""
    covered = {m.group().casefold() for s in spans for m in _words(s)}
    parts, start, end = [], None, None
    for m in _words(question):
        w = m.group().casefold()
        if w in FUNCTION_WORDS:
            continue
        if w in covered:
            if start is not None:
                parts.append(question[start:end])
                start = None
            continue
        start = m.start() if start is None else start
        end = m.end()
    if start is not None:
        parts.append(question[start:end])
    return parts


def _check_span(text: str, conv: Conversation, what: str):
    sources = [conv.question] + ([conv.rewritten] if conv.rewritten else [])
    if not any(_norm(text) in _norm(src) for src in sources):
        raise CompileError("span_not_in_question",
                           f"{what} '{text}' is not a span of the question.", span=text)


def _mentioned(value: Any, texts: list[str]) -> bool:
    """Whole-token match, so that S-03 is not found inside S-031."""
    pat = re.compile(r"(?<![0-9a-z_-])" + re.escape(str(value).casefold()) + r"(?![0-9a-z_-])")
    return any(pat.search(t.casefold()) for t in texts)


def _check_provenance(named: list, conv: Conversation, label: str, exempt: set[tuple[str, str]] | None = None):
    """NFR 27, Test 45. The rewritten question is not a source: the model wrote it. exempt: the nodes, as
    (label, path), that code wrote from a name the question holds (with_names, chat 20.10): only those
    nodes, never another condition on the same identifier the model wrote itself."""
    texts = [conv.question] + list(conv.earlier)
    shown = {str(v).casefold() for v in conv.presented}
    for column, value, path in named:
        if (label, path) in (exempt or set()) or str(value).casefold() in shown or _mentioned(value, texts):
            continue
        raise CompileError("identifier_not_in_conversation",
                           f"'{value}' for {column} appears neither in the conversation nor among "
                           f"the identifiers presented. The request was not run.",
                           label=label, column=column, value=value)


def _check_gap_entity(cat: Catalog, needs: dict):
    kind, entity = needs["kind"], needs.get("entity")
    if kind == "unspecified":
        if entity is not None:
            raise CompileError("gap_entity", "An unspecified gap carries an entity.")
        return
    if entity is None:
        raise CompileError("gap_entity", f"A '{kind}' gap names no entity.", kind=kind)
    if kind in ("no_such_column", "value_not_present"):
        ok = cat.resolves(entity)
    elif kind == "cross_record_condition":   # D1 (fix round): the table whose other records the part needs
        ok = entity in cat.entities
    else:  # no_such_table, source_outside_system: the name must NOT be something the schema holds
        ok = not cat.resolves(entity)
    if not ok:
        raise CompileError("gap_entity", f"A '{kind}' gap names '{entity}', which contradicts schema.json.",
                           kind=kind, entity=entity)


def language_limit(error) -> tuple[str, int | None] | None:
    """Task 3, GAP-07: a schema breach that is a limit of the language, with the reading it falls on: more
    values in an IN than one question holds, or more readings. Looked for inside oneOf and anyOf too."""
    stack = [error]
    while stack:
        e = stack.pop()
        path = list(e.absolute_path)
        if e.validator == "maxItems":
            if path == ["readings"]:
                return "readings_limit", None
            if len(path) > 2 and path[0] == "readings" and path[-2:] == ["IN", "values"]:
                return "in_values_limit", path[1]
        stack += list(e.context or [])
    return None


def _refs(node: dict | None):
    """Every IN_READING and NOT_IN_READING in a WHERE tree, wherever it stands."""
    if not node:
        return
    (op, arg), = node.items()
    if op in ("AND", "OR"):
        for p in arg:
            yield from _refs(p)
    elif op in READING_REFS:
        yield arg


def _keep_within(readings: list[dict], limit: int) -> list[dict]:
    """Task 3 (chat 21.10, 12:50): at most limit readings, by order, never keeping one without a reading it
    reads: such a reading is kept only together with that one, where both fit."""
    by_label = {r["label"]: r for r in readings}
    kept: list[str] = []
    for r in readings:
        if r["label"] in kept:
            continue
        need = [r["label"]] + [a["reading"] for a in _refs(r["plan"].get("WHERE"))
                               if a["reading"] in by_label and a["reading"] not in kept]
        need = list(dict.fromkeys(need))
        if len(kept) + len(need) <= limit:
            kept += need
    return [r for r in readings if r["label"] in kept]


def compile_intent(intent: dict, question: "str | Conversation", user: dict, cat: Catalog,
                   validator: Draft202012Validator | None = None,
                   row_limit: int = ROW_LIMIT, exempt: set[tuple[str, str]] | None = None,
                   drop_limits: bool = False) -> CompiledIntent:
    """drop_limits (task 3, GAP-07; the last attempt only): a reading that breaks a limit of the language
    (LANGUAGE_LIMITS) is left out, with every reading that reads it, and named in dropped; the rest compile as
    usual. Without it, as without any other fault, the plan is rejected whole."""
    conv = question if isinstance(question, Conversation) else Conversation(question)
    validator = validator or intent_validator(row_limit=row_limit)
    dropped: list[dict] = []
    errors = sorted(validator.iter_errors(intent), key=lambda e: list(e.path))
    if errors and drop_limits and intent.get("disposition") == "retrieve":
        found = [language_limit(e) for e in errors]
        limits = [x for x in found if x is not None]
        if len(limits) == len(found):
            readings = list(intent.get("readings", []))
            out_idx = {i for code, i in limits if code == "in_values_limit" and i is not None}
            for i in sorted(out_idx):
                dropped.append({"label": readings[i]["label"], "asked": readings[i].get("asked", ""),
                                "code": "in_values_limit"})
            readings = [r for i, r in enumerate(readings) if i not in out_idx]
            if any(code == "readings_limit" for code, _ in limits):
                within = _keep_within(readings, validator.schema["properties"]["readings"].get("maxItems", 3))
                dropped += [{"label": r["label"], "asked": r.get("asked", ""), "code": "readings_limit"}
                            for r in readings if r not in within]
                readings = within
            intent = {**intent, "readings": readings}
            # every reading left out: no reading is a finding here, said part by part, not a breach
            errors = sorted((e for e in validator.iter_errors(intent)
                             if not (readings == [] and list(e.absolute_path) == ["readings"]
                                     and e.validator == "minItems")), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        raise CompileError("schema_violation", e.message, path="/".join(map(str, e.path)))

    if intent["disposition"] in ("refer_to_investigation", "out_of_scope_action", "needs_clarification"):
        return CompiledIntent(intent["disposition"], intent=intent)
    intent = normalise_values(intent, cat)

    for u in intent.get("unresolved", []):
        _check_span(u["asked"], conv, "unresolved.asked")
        _check_gap_entity(cat, u["needs"])

    readings = intent["readings"]
    for r in readings:   # task 4, A: the part a reading answers is the person's own words
        _check_span(r["asked"], conv, "reading.asked")
    for r in readings:   # a name the user wrote is a span of the question (chat 20.10)
        for node in _matches(r["plan"].get("WHERE")):
            _check_span(node["term"], conv, "MATCHES term")
    for a in intent.get("ambiguous", []):
        _check_span(a["term"], conv, "ambiguous.term")
        for alt in a["alternatives"]:
            if "col" in alt and cat.column(alt["col"]) is None:
                raise CompileError("unknown_column", f"Alternative '{alt['col']}' is not in schema.json.")
            if "metric" in alt and alt["metric"] not in cat.metrics:
                raise CompileError("unknown_metric", f"Alternative '{alt['metric']}' is not declared.")
        if len(readings) < len(a["alternatives"]):
            raise CompileError("ambiguity_collapsed",
                               f"'{a['term']}' declares {len(a['alternatives'])} alternatives "
                               f"but the plan carries {len(readings)} reading(s).", term=a["term"])

    labels = [r["label"] for r in readings]
    if len(labels) != len(set(labels)):
        raise CompileError("duplicate_label", "Two readings share a label.")
    _check_ambiguous_refs(intent)

    # task 3: a reading another reads is compiled first, so its query is there to read; depth 1, so two passes
    reads = {r["label"]: [a["reading"] for a in _refs(r["plan"].get("WHERE"))] for r in readings}
    order = [r for r in readings if not reads[r["label"]]] + [r for r in readings if reads[r["label"]]]
    done: dict[str, CompiledReading] = {}
    out_labels = {d["label"] for d in dropped}
    for r in order:
        lost = [x for x in reads[r["label"]] if x in out_labels]
        if lost:   # it reads a reading left out: left out too, for the same reason
            code = next(d["code"] for d in dropped if d["label"] == lost[0])
            dropped.append({"label": r["label"], "asked": r["asked"], "code": code})
            out_labels.add(r["label"])
            continue
        inner = {k: v for k, v in done.items() if not v.depends_on}
        rc = _ReadingCompiler(cat, user, r, row_limit, inner, frozenset(labels) - {r["label"]})
        try:
            done[r["label"]] = rc.compile()
            _check_provenance(rc.named_ids, conv, r["label"], exempt)
        except CompileError as e:
            if not (drop_limits and e.code in LANGUAGE_LIMITS):
                raise
            dropped.append({"label": r["label"], "asked": r["asked"], "code": e.code})
            out_labels.add(r["label"])
    for r in readings:   # one that read a reading left out after it was compiled
        if r["label"] in done and any(x in out_labels for x in reads[r["label"]]):
            code = next(d["code"] for d in dropped if d["label"] in reads[r["label"]])
            dropped.append({"label": r["label"], "asked": r["asked"], "code": code})
            out_labels.add(r["label"])
            del done[r["label"]]
    compiled = [done[r["label"]] for r in readings if r["label"] in done]
    kept = {**intent, "readings": [r for r in readings if r["label"] in done]}
    spans = ([r["asked"] for r in readings] + [d["asked"] for d in dropped]
             + [u["asked"] for u in intent.get("unresolved", [])])
    return CompiledIntent("retrieve", compiled, intent.get("unresolved", []), kept, uncovered(conv.question, spans),
                          dropped)


def _named(node: Any):
    """Every column and metric a plan names, as ("col", name) or ("metric", name), wherever it stands."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("col", "metric") and isinstance(v, str):
                yield k, v
            else:
                yield from _named(v)
    elif isinstance(node, list):
        for v in node:
            yield from _named(v)


def _check_ambiguous_refs(intent: dict):
    """Task 3 (chat 21.10, 12:50, question 1): a reading another reading reads is never one of the alternatives
    of an ambiguous term. The plan asks which was meant instead (needs_clarification), so no answer multiplies its
    readings for a term the person did not mean in two ways."""
    alternatives = [(alt.get("col"), alt.get("metric"))
                    for a in intent.get("ambiguous", []) for alt in a["alternatives"]]
    if not alternatives:
        return
    read = {a["reading"] for r in intent.get("readings", []) for a in _refs(r["plan"].get("WHERE"))}
    for r in intent.get("readings", []):
        if r["label"] not in read:
            continue
        used = set(_named(r["plan"]))
        for col, metric in alternatives:
            if ("col", col) in used or ("metric", metric) in used:
                raise CompileError("reading_ref_ambiguous",
                                   f"Reading '{r['label']}' is read by another reading and uses an alternative of "
                                   f"an ambiguous term. A term another reading depends on is not answered in two "
                                   f"ways: set disposition to needs_clarification instead.", label=r["label"])


# --------------------------------------------------------------------------
# values as the store holds them (chat 19.10, A1 and A2)
# --------------------------------------------------------------------------

def _comparisons(node: dict, path: str):
    """Each literal compared with a column in a WHERE tree: (column, the node holding "value", path)."""
    (op, arg), = node.items()
    if op in ("AND", "OR"):
        for i, p in enumerate(arg):
            yield from _comparisons(p, f"{path}/{op}/{i}")
    elif op in CMP_SQL:
        for i, j in ((0, 1), (1, 0)):
            if "col" in arg[i] and "value" in arg[j]:
                yield arg[i]["col"], arg[j], f"{path}/{op}/{j}/value"
    elif op == "IN":
        for i, v in enumerate(arg["values"]):
            yield arg["col"], v, f"{path}/IN/values/{i}/value"
    elif op == "BETWEEN":
        for end in ("from", "to"):
            yield arg["col"], arg[end], f"{path}/BETWEEN/{end}/value"


def _matches(node: dict | None):
    """Every MATCHES in a WHERE tree, wherever it stands (the compiler rejects it outside AND)."""
    if not node:
        return
    (op, arg), = node.items()
    if op in ("AND", "OR"):
        for p in arg:
            yield from _matches(p)
    elif op == "MATCHES":
        yield arg


def _where_comparisons(intent: dict):
    for i, r in enumerate(intent.get("readings", [])):
        where = r["plan"].get("WHERE")
        if where:
            for column, holder, path in _comparisons(where, f"readings/{i}/plan/WHERE"):
                yield r["label"], column, holder, path


def normalise_values(intent: dict, cat: Catalog) -> dict:
    """A2: a value compared with a column that declares its values is one of them. Equal to exactly
    one ignoring case, it is written in the declared form; otherwise the plan is rejected, naming
    the path, so the feedback attempt can correct it. Returns a copy; the model's output is kept."""
    out = copy.deepcopy(intent)
    for label, column, holder, path in _where_comparisons(out):
        spec = cat.column(column) or {}
        declared, value = spec.get("values"), holder["value"]
        if not declared or value in declared:
            continue
        same = [d for d in declared if isinstance(value, str) and d.casefold() == value.casefold()]
        if len(same) == 1:
            holder["value"] = same[0]
            continue
        raise CompileError("value_not_declared",
                           f"{value!r} at {path} is not a value {column} takes. It takes one of: "
                           f"{', '.join(declared)}.", label=label, path=path, column=column)
    return out


def resolve_identifiers(compiled: CompiledIntent, conn: sqlite3.Connection) -> dict[tuple[str, Any], Any]:
    """A1: an identifier the store does not hold as written, but holds in exactly one form ignoring
    case, maps to that form. Several forms keep the exact match; none keeps the value, so the
    reading reports it absent (Test 35)."""
    found: dict[tuple[str, Any], Any] = {}
    for r in compiled.readings:
        for p in r.probes:
            if not p.resolve_sql or not isinstance(p.value, str) or (p.column, p.value) in found:
                continue
            if conn.execute(p.sql, p.params).fetchone() is not None:
                continue
            forms = [row[0] for row in conn.execute(p.resolve_sql, p.resolve_params)]
            if len(forms) == 1 and forms[0] != p.value:
                found[(p.column, p.value)] = forms[0]
    return found


def with_identifiers(intent: dict, found: dict[tuple[str, Any], Any]) -> dict:
    """The intent with each resolved identifier written as the store holds it, where it is compared
    with the column it was probed on. Returns a copy."""
    out = copy.deepcopy(intent)
    for _, column, holder, _ in _where_comparisons(out):
        if (column, holder["value"]) in found:
            holder["value"] = found[(column, holder["value"])]
    return out


def resolve_names(compiled: CompiledIntent, conn: sqlite3.Connection) -> dict[tuple[str, str, str], NameMatch]:
    """Chat 20.10: each name a reading matches, resolved in code. The stages run in order and stop at the
    first that finds anything; the count and the candidates are entities, by their own identifier (B).
    Keyed by (label, column, term). No model; nothing here reaches a prompt (V5)."""
    out: dict[tuple[str, str, str], NameMatch] = {}
    for r in compiled.readings:
        for n in r.names:
            key = (r.label, n.column, n.term)
            if key in out:
                continue
            out[key] = NameMatch(None, 0)
            for stage, values_sql, values_params, count_sql, count_params in n.stages:
                count = conn.execute(count_sql, count_params).fetchone()[0]
                if count:
                    found = [tuple(row) for row in conn.execute(values_sql, values_params)]
                    out[key] = NameMatch(stage, count, found)
                    break
    return out


def with_names(intent: dict, single: dict[tuple[str, str, str], tuple[str, Any]],
               written: set[tuple[str, str]] | None = None) -> dict:
    """The intent with each MATCHES that found one entity written as EQ on that entity's identifier
    (B), never on the name, so two entities sharing a name are never merged. Returns a copy. written, if
    given, receives each node written, as (label, path) in the compiler's form ("WHERE/AND/0"): the only
    nodes NFR 27 lets hold that identifier without the question holding it (chat 20.10, task 4)."""
    out = copy.deepcopy(intent)

    def walk(node: dict, label: str, path: str) -> dict:
        (op, arg), = node.items()
        if op in ("AND", "OR"):
            return {op: [walk(p, label, f"{path}/{op}/{i}") for i, p in enumerate(arg)]}
        if op == "MATCHES" and (label, arg["col"], arg["term"]) in single:
            id_column, value = single[(label, arg["col"], arg["term"])]
            if written is not None:
                written.add((label, path))
            return {"EQ": [{"col": id_column}, {"value": value}]}
        return node

    for r in out.get("readings", []):
        if r["plan"].get("WHERE"):
            r["plan"]["WHERE"] = walk(r["plan"]["WHERE"], r["label"], "WHERE")
    return out


# Phase B task 5 ("Show all records", chat 19.10): the most rows a reading shows when asked for all of them
SHOW_ALL_LIMIT = 1000


def compile_all(label: str, plan: dict, user: dict, cat: Catalog, limit: int = SHOW_ALL_LIMIT,
                others: dict[str, dict] | None = None) -> CompiledReading:
    """A plan already compiled and run in an exchange, compiled again to show all its records: the plan's
    own LIMIT left out and SHOW_ALL_LIMIT injected. Entitlement is injected afresh for the user asking now
    (NFR 17), from the catalog, as on every compilation. The plan is the server's record of the exchange,
    never one a client sent; it passed the schema and NFR 27 when the exchange ran. No model.
    others (task 3): the exchange's other plans, by label. A reading another reads is compiled as it ran, its
    own LIMIT kept where the plan wrote one, since that is part of what the condition means."""
    others = others or {}
    read = [a["reading"] for a in _refs(plan.get("WHERE")) if a["reading"] in others]
    inner = {x: _ReadingCompiler(cat, user, {"label": x, "plan": copy.deepcopy(others[x])}, ROW_LIMIT).compile()
             for x in dict.fromkeys(read)}
    whole = {k: v for k, v in copy.deepcopy(plan).items() if k != "LIMIT"}
    return _ReadingCompiler(cat, user, {"label": label, "plan": whole}, limit, inner,
                            frozenset(others) - {label}).compile()


def execute(reading: CompiledReading, conn: sqlite3.Connection) -> RetrievalResult:
    """Runs one reading. No model call. Empty is a finding, not a failure (Test 35)."""
    if reading.names:   # chat 20.10, A: a name not resolved is never run as written
        raise UnresolvedName(f"{reading.label}: " + ", ".join(f"{n.column} at {n.path}" for n in reading.names))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(reading.sql, reading.params)]
    total = conn.execute(reading.count_sql, reading.count_params).fetchone()[0]
    if rows:
        return RetrievalResult(reading.label, "rows_returned", rows, total, total > len(rows))
    missing = [{"column": p.column, "value": p.value, "checked_in": p.checked_in}
               for p in reading.probes if conn.execute(p.sql, p.params).fetchone() is None]
    outcome = "empty_unknown_entity" if missing else "empty_no_match"
    return RetrievalResult(reading.label, outcome, [], 0, False, missing)
