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
from typing import Any

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
CMP_SQL = {"EQ": "=", "NE": "<>", "GT": ">", "GTE": ">=", "LT": "<", "LTE": "<="}


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


@dataclass
class CompiledIntent:
    disposition: str
    readings: list[CompiledReading] = field(default_factory=list)
    unresolved: list = field(default_factory=list)


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
        self.relations = relations["relations"]
        for table, ent in self.entities.items():
            for col, spec in ent["columns"].items():
                ref = spec.get("references")
                if ref is None:
                    continue
                target = self.column(ref)
                if not spec.get("identifier") or target is None or not target.get("identifier"):
                    raise ValueError(f"schema.json: {table}.{col} references '{ref}', which must be "
                                     f"an identifier column, and so must {table}.{col}")

    @classmethod
    def load(cls, schema_path=HERE / "schema.json", relations_path=HERE / "relations.json"):
        return cls(json.loads(Path(schema_path).read_text(encoding="utf-8")),
                   json.loads(Path(relations_path).read_text(encoding="utf-8")))

    def column(self, qualified: str) -> dict | None:
        table, _, col = qualified.partition(".")
        ent = self.entities.get(table)
        return ent["columns"].get(col) if ent else None

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
    def __init__(self, cat: Catalog, user: dict, reading: dict, row_limit: int):
        self.cat = cat
        self.user = user
        self.label = reading["label"]
        self.plan = reading["plan"]
        self.row_limit = row_limit
        self.scope: dict[str, str] = {}          # table -> FROM | INNER | LEFT
        self.join_sql: list[tuple[str, list]] = []
        self.aliases: dict[str, Expr] = {}
        self.identities: list[tuple[str, Any]] = []   # probed when the result is empty
        self.named_ids: list[tuple[str, Any]] = []    # every identifier literal, for NFR 27

    def fail(self, code, reason, **detail):
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
        if not _literal_fits(side.type, lit):
            self.fail("type_mismatch",
                      f"{op} compares {side.column or 'a metric'} ({side.type}) with {lit!r}.",
                      value=lit, against=side.column)

    def _is_identifier(self, side: Expr) -> bool:
        return bool(side.column and self.cat.column(side.column).get("identifier"))

    def _note_identity(self, side: Expr, values: list):
        if self._is_identifier(side):
            for v in values:
                self.identities.append((side.column, v))

    def _note_named(self, side: Expr, values: list):
        if self._is_identifier(side):
            for v in values:
                self.named_ids.append((side.column, v))

    def predicate(self, node: dict, top: bool = True) -> tuple[str, list]:
        (op, arg), = node.items()
        if op in ("AND", "OR"):
            # An identifier under AND still restricts the result. Under OR it does not.
            parts = [self.predicate(p, top=top and op == "AND") for p in arg]
            sql = f" {op} ".join(f"({s})" for s, _ in parts)
            return sql, [p for _, ps in parts for p in ps]
        if op in CMP_SQL:
            a, b = (self.operand(x) for x in arg)
            if a.is_literal and b.is_literal:
                self.fail("no_column_in_comparison", f"{op} compares two values and no column.")
            if a.is_me or b.is_me:
                side = b if a.is_me else a
                if op not in ("EQ", "NE") or not side.column or not self.cat.column(side.column).get("user_column"):
                    self.fail("me_not_allowed", f"The current user may be compared only for equality with a "
                                                f"column that holds a user, not with {side.column or 'a value'}.",
                              against=side.column)
                return f"{a.sql} {CMP_SQL[op]} {b.sql}", a.params + b.params
            if a.is_literal or b.is_literal:
                side, lit = (b, a.literal) if a.is_literal else (a, b.literal)
                self._check_literal(side, lit, op)
                self._note_named(side, [lit])
                if op == "EQ" and top:
                    self._note_identity(side, [lit])
            elif not _types_match(a.type, b.type):
                self.fail("type_mismatch", f"{op} compares {a.type} with {b.type}.")
            return f"{a.sql} {CMP_SQL[op]} {b.sql}", a.params + b.params
        if op == "IN":   # an object, not a tuple (task 10a, 15.10): {"col", "values"}
            c = self.col(arg["col"])
            vals = [v["value"] for v in arg["values"]]
            for v in vals:
                self._check_literal(c, v, "IN")
            self._note_named(c, vals)
            if top:
                self._note_identity(c, vals)
            return f"{c.sql} IN ({', '.join('?' for _ in vals)})", vals
        if op == "BETWEEN":   # an object, not a tuple (task 10a, 15.10): {"col", "from", "to"}
            c = self.col(arg["col"])
            lo, hi = arg["from"]["value"], arg["to"]["value"]
            self._check_literal(c, lo, "BETWEEN")
            self._check_literal(c, hi, "BETWEEN")
            self._note_named(c, [lo, hi])
            return f"{c.sql} BETWEEN ? AND ?", [lo, hi]
        if op in ("IS_NULL", "IS_NOT_NULL"):
            c = self.col(arg["col"])
            if c.column.split(".")[0] != self.plan["FROM"]:
                self.fail("null_test_on_joined",
                          f"{op} on '{c.column}', which is not a column of the FROM table.", col=c.column)
            return f"{c.sql} {'IS NULL' if op == 'IS_NULL' else 'IS NOT NULL'}", []
        raise AssertionError(op)  # schema 1 admits no other operator

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
            e = self.aliases.get(h["agg"])
            if e is None or not e.is_agg:
                self.fail("having_not_aggregate", f"HAVING names '{h['agg']}', which is not an aggregate alias.",
                          alias=h["agg"])
            if e.type not in NUMERIC:
                self.fail("type_mismatch", f"HAVING compares '{h['agg']}' ({e.type}) with a number.")
            having_sql.append(f"{e.sql} {CMP_SQL[h['op']]} ?")
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
        return CompiledReading(self.label, sql, params + [limit], count_sql, list(params),
                               limit, limit_injected, self._probes())

    def _probe_target(self, column: str) -> tuple[str, tuple[str, list] | None]:
        """Where an identifier's existence is checked, and the entitlement condition there.

        A column that declares references (commitments.supplier_id -> suppliers.supplier_id)
        is checked where the identifier is held, so a supplier with no lines reads as held
        with nothing matching, not as absent. The reference is used only where the user may
        read its table. Otherwise the column itself is checked, as before."""
        ref = self.cat.column(column).get("references")
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
            out.append(Probe(column, value, sql, params, target))
        return out


# --------------------------------------------------------------------------
# intent-level checks and entry points
# --------------------------------------------------------------------------

def _check_span(text: str, conv: Conversation, what: str):
    sources = [conv.question] + ([conv.rewritten] if conv.rewritten else [])
    if not any(_norm(text) in _norm(src) for src in sources):
        raise CompileError("span_not_in_question",
                           f"{what} '{text}' is not a span of the question.", span=text)


def _mentioned(value: Any, texts: list[str]) -> bool:
    """Whole-token match, so that S-03 is not found inside S-031."""
    pat = re.compile(r"(?<![0-9a-z_-])" + re.escape(str(value).casefold()) + r"(?![0-9a-z_-])")
    return any(pat.search(t.casefold()) for t in texts)


def _check_provenance(named: list, conv: Conversation, label: str):
    """NFR 27, Test 45. The rewritten question is not a source: the model wrote it."""
    texts = [conv.question] + list(conv.earlier)
    shown = {str(v).casefold() for v in conv.presented}
    for column, value in named:
        if str(value).casefold() in shown or _mentioned(value, texts):
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


def compile_intent(intent: dict, question: "str | Conversation", user: dict, cat: Catalog,
                   validator: Draft202012Validator | None = None,
                   row_limit: int = ROW_LIMIT) -> CompiledIntent:
    conv = question if isinstance(question, Conversation) else Conversation(question)
    validator = validator or intent_validator(row_limit=row_limit)
    errors = sorted(validator.iter_errors(intent), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        raise CompileError("schema_violation", e.message, path="/".join(map(str, e.path)))

    if intent["disposition"] in ("refer_to_investigation", "out_of_scope_action", "needs_clarification"):
        return CompiledIntent(intent["disposition"])

    for u in intent.get("unresolved", []):
        _check_span(u["asked"], conv, "unresolved.asked")
        _check_gap_entity(cat, u["needs"])

    readings = intent["readings"]
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

    compiled = []
    for r in readings:
        rc = _ReadingCompiler(cat, user, r, row_limit)
        compiled.append(rc.compile())
        _check_provenance(rc.named_ids, conv, r["label"])
    return CompiledIntent("retrieve", compiled, intent.get("unresolved", []))


def execute(reading: CompiledReading, conn: sqlite3.Connection) -> RetrievalResult:
    """Runs one reading. No model call. Empty is a finding, not a failure (Test 35)."""
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(reading.sql, reading.params)]
    total = conn.execute(reading.count_sql, reading.count_params).fetchone()[0]
    if rows:
        return RetrievalResult(reading.label, "rows_returned", rows, total, total > len(rows))
    missing = [{"column": p.column, "value": p.value, "checked_in": p.checked_in}
               for p in reading.probes if conn.execute(p.sql, p.params).fetchone() is None]
    outcome = "empty_unknown_entity" if missing else "empty_no_match"
    return RetrievalResult(reading.label, outcome, [], 0, False, missing)
