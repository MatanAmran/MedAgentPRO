"""
MedAgentPro - query assistance pipeline. Prompt rendering.

Three prompts, one file each under templates/, each with a version recorded in
model_identity (NFR 3):
  rewrite.md          question rewriting, runs only where the conversation has earlier exchanges
  intent_analysis.md  schema 1, the plan the compiler builds the query from
  gate.md             schema 4, a verdict per assertion of the drafted answer

Also here: the conversation history given to the rewriting call, reduced in layers
when it outgrows its budget (Table 15, "History carried into rewriting"), and the
Conversation the compiler checks identifiers against (NFR 27).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .calls import OutputRejected
from .compiler import ROW_LIMIT, Catalog, Conversation, _norm

HERE = Path(__file__).parent
PROMPTS = HERE / "templates"

# PROVISIONAL. Table 15: the prompt budget of the rewriting call. Set from measurement.
HISTORY_BUDGET_TOKENS = 2000

SQL_TYPES = {"text": "TEXT", "integer": "INTEGER", "real": "REAL", "date": "DATE", "boolean": "BOOLEAN"}


# --------------------------------------------------------------------------
# prompt files
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    output_schema: str
    body: str

    @property
    def identity(self) -> str:
        """Recorded with each call, so a result can be traced to the prompt that produced it."""
        return f"{self.name}@{self.version}"


_HEADER_KEYS = ("prompt", "version", "output_schema")


def load(file: str | Path) -> Prompt:
    path = PROMPTS / file
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if m is None:
        raise ValueError(f"{path.name}: no header; the file must open with a '---' block holding "
                         f"{', '.join(_HEADER_KEYS)}")
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(": ")
        if not sep:
            raise ValueError(f"{path.name}: header line {line!r} is not 'key: value'")
        meta[key] = value
    missing = [k for k in _HEADER_KEYS if k not in meta]
    if missing:
        raise ValueError(f"{path.name}: header lacks {', '.join(missing)}")
    return Prompt(meta["prompt"], meta["version"], meta["output_schema"], text[m.end():])


def fill(prompt: Prompt, **values: str) -> str:
    """One pass over the template only. A value is never scanned again, so a question or record text
    that holds {{name}} stays as written (review R6)."""
    left = [k for k in re.findall(r"\{\{(\w+)\}\}", prompt.body) if k not in values]
    if left:
        raise ValueError(f"{prompt.name}: unfilled {left}")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], prompt.body)


def feedback_block(feedback: str | None) -> str:
    """Review R4: the one further attempt carries the specific rejection, never a record value. Empty on
    a first attempt. [proposal, not in the book; Table 15 names "Model call attempts" only]"""
    if not feedback:
        return ""
    return (f"Your previous output was rejected: {feedback}\n"
            "Correct it and return the whole output again.\n")


def _tokens(text: str) -> int:
    """Rough count, four characters a token. Used only to compare against the budget."""
    return len(text) // 4 + 1


# --------------------------------------------------------------------------
# the schema as the model sees it
# --------------------------------------------------------------------------

def visible_tables(cat: Catalog, role: str) -> list[str]:
    """Only tables the role may read. Entitlement rules themselves are never shown."""
    return [t for t, e in cat.entities.items() if role in (e.get("entitlement") or {})]


def _column_note(spec: dict) -> str:
    """What the model needs to know about a column beyond its type. Where a text column takes
    a closed set of values, the set is shown, so a filter is not written as 'Open' for 'open'."""
    notes = []
    if spec.get("identifier"):
        notes.append("identifier")
    if spec.get("values"):
        notes.append("one of: " + ", ".join(spec["values"]))
    if spec.get("user_column"):   # D7 (fix round): the only place the model may say "the person asking"
        notes.append('a user; for the person asking, compare with {"me": true}')
    return ("  -- " + "; ".join(notes)) if notes else ""


def render_schema(cat: Catalog, role: str) -> str:
    tables = visible_tables(cat, role)
    out = []
    for t in tables:
        cols = cat.entities[t]["columns"]
        lines = [f"  {c} {SQL_TYPES[s['type']]}" + _column_note(s) for c, s in cols.items()]
        out.append(f"CREATE TABLE {t} (\n" + ",\n".join(lines) + "\n);")
    for name, r in cat.relations.items():
        if r["from"] in tables and r["to"] in tables:
            on = " AND ".join(f"{a} = {b}" for a, b in r["on"])
            out.append(f"-- relation {name}: {on}")
    for name, m in cat.metrics.items():
        if m["entity"] in tables:
            out.append(f"-- metric {name} ({m['type']}, on {m['entity']}): {m['description']}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# conversation history
# --------------------------------------------------------------------------

@dataclass
class Exchange:
    """One earlier exchange, as the rewriting call and the identifier check see it.

    answer     the answer after the gate, with every free-text value left as its reference
               (synthesis.Draft.answer(..., for_history=True)). Text held in a record must not
               reach the rewriting call or count as a source of identifiers (NFR 21)
    presented  identifiers from identifier columns only (synthesis.Draft.presented_identifiers),
               in the order shown
    readings   D2 (fix round): what each reading examined and how many of its rows were shown, from the
               plan, the catalog and the counts only (NFR 21): the condition a later "them" refers to
    cut        D1: identifiers shown by a reading whose rows were cut (shown < total); a rewrite may not
               name them, since they are not the whole result
    """
    posed: str
    understood: str | None = None      # the rewritten question, where there was one
    answer: str | None = None
    presented: list[str] = field(default_factory=list)
    presented_total: int = 0           # set where the history cut the list (R7c)
    readings: list[ReadingNote] = field(default_factory=list)
    cut: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReadingNote:
    label: str
    examined: str
    shown: int
    total: int

    def render(self) -> str:
        count = ("no rows" if self.total == 0 else f"all {self.total} shown" if self.shown >= self.total
                 else f"{self.shown} of {self.total} shown")
        return f"[{self.label}] examined: {self.examined} · {count}"


@dataclass
class History:
    text: str
    layer: int                          # 1 whole, 2 older answers dropped, 3 summary needed
    to_summarize: list[Exchange] = field(default_factory=list)


def _render_exchange(i: int, ex: Exchange, with_answer: bool) -> str:
    lines = [f"Q{i} (posed): {ex.posed}"]
    if ex.understood:
        lines.append(f"Q{i} (understood as): {ex.understood}")
    for r in ex.readings:   # D2: kept when the answer is dropped, so "them" still has its condition
        lines.append(f"Q{i} reading {r.render()}")
    if with_answer and ex.answer:
        lines.append(f"A{i}: {ex.answer}")
    if with_answer and ex.presented:
        lines.append("Presented identifiers: " + ", ".join(ex.presented))
    if with_answer and ex.presented_total > len(ex.presented):
        lines[-1] = (f"Presented identifiers (the first {len(ex.presented)} of {ex.presented_total}, "
                     f"in display order): " + ", ".join(ex.presented))
    return "\n".join(lines)


def _fit_last(i: int, ex: Exchange, budget: int) -> Exchange:
    """Review R7c: the last exchange is always carried whole, and after F5 its presented identifiers
    can outgrow the budget alone. They are cut to the first ones in display order. The identifier
    check (NFR 27) still sees them all, since compiler_conversation does not read the prompt."""
    if not ex.presented or _tokens(_render_exchange(i, ex, True)) <= budget:
        return ex
    keep: list[str] = []
    for v in ex.presented:
        trial = replace(ex, presented=keep + [v], presented_total=len(ex.presented))
        if _tokens(_render_exchange(i, trial, True)) > budget:
            break
        keep.append(v)
    return replace(ex, presented=keep, presented_total=len(ex.presented))


def build_history(exchanges: list[Exchange], budget: int = HISTORY_BUDGET_TOKENS,
                  summary: str | None = None) -> History:
    """Layer 1: everything. Layer 2: answers of older exchanges are dropped, one at a time and the
    oldest first, until the rest fits (D2, fix round); questions and their readings' examined lines are
    kept, done in code. Layer 3: the oldest exchanges go to a model summary, which the caller supplies and
    which is never a source of identifiers (NFR 27)."""
    if not exchanges:
        return History("", 1)
    last = len(exchanges)
    exchanges = exchanges[:-1] + [_fit_last(last, exchanges[-1], budget)]
    full = "\n".join(_render_exchange(i, ex, True) for i, ex in enumerate(exchanges, 1))
    if _tokens(full) <= budget:
        return History(full, 1)

    def reduced(start: int, answers_from: int) -> str:
        parts = [_render_exchange(i, ex, i == last or i >= answers_from)
                 for i, ex in enumerate(exchanges, 1) if i >= start]
        return "\n".join(parts)

    for answers_from in range(2, last + 1):
        text = reduced(1, answers_from)
        if _tokens(text) <= budget:
            return History(text, 2)

    start = 1
    while start < last and _tokens(reduced(start, last)) > budget:
        start += 1
    head = ("Summary of earlier exchanges, not a source of identifiers: " + summary + "\n") if summary else ""
    return History(head + reduced(start, last), 3, exchanges[:start - 1])


def compiler_conversation(exchanges: list[Exchange], posed: str, rewritten: str | None) -> Conversation:
    """Every earlier question and answer counts, including those reduced out of the prompt.
    The rewritten questions and any summary do not: a model wrote them."""
    earlier, shown = [], set()
    for ex in exchanges:
        earlier.append(ex.posed)
        if ex.answer:
            earlier.append(ex.answer)
        shown.update(ex.presented)
    return Conversation(posed, rewritten, earlier, shown)


# --------------------------------------------------------------------------
# rendering each prompt
# --------------------------------------------------------------------------

def render_rewrite(history: History, question: str, prompt: Prompt | None = None,
                   feedback: str | None = None) -> str:
    return fill(prompt or load("rewrite.md"), history=history.text, question=question,
                feedback=feedback_block(feedback))


def question_block(posed: str, rewritten: str | None = None) -> str:
    """Both forms where the question was rewritten, so a later stage can correct a wrong rewrite
    (4.9, "later stages receive both forms")."""
    if rewritten and _norm(rewritten) != _norm(posed):
        return f"Question as posed: {posed}\nQuestion: {rewritten}"
    return f"Question: {posed}"


def render_intent(cat: Catalog, role: str, posed: str, rewritten: str | None = None,
                  prompt: Prompt | None = None, feedback: str | None = None) -> str:
    """The reference date resolves a month or period given without a year (task 10a, chat 01:15). It is
    the date the catalog's data runs to, never the system clock, so the same question plans the same
    way on any day. A prompt that names it, over a catalog that holds none, fails here."""
    pr = prompt or load("intent_analysis.md")
    if "{{reference_date}}" in pr.body and not cat.data_as_of:
        raise ValueError(f"{pr.name}: the catalog holds no data_as_of for {{{{reference_date}}}}")
    return fill(pr, schema=render_schema(cat, role), question_block=question_block(posed, rewritten),
                feedback=feedback_block(feedback), reference_date=cat.data_as_of or "", row_limit=str(ROW_LIMIT))


def render_records(results: list[tuple[str, list[dict[str, Any]]]], ordered_by: dict[str, str] | None = None,
                   examined: dict[str, str] | None = None, totals: dict[str, int] | None = None) -> str:
    """ordered_by maps a reading label to the alias its rows are ordered by (the first ORDER_BY key of
    the plan that ran). A row holding the same value there as the row before it says so, by name and
    row number only: a fact about the rows, given by code, so that no place is named between equal
    values (task 10a, chat 21:50). The marker repeats no value.
    examined maps a label to what the reading examined, as "Examined" shows it (contract.examined_line),
    so the words around references describe that reading and no other (chat, 18.10, 09:40). It is
    built from the plan and the catalog, never from a record (NFR 21)."""
    out, said = [], examined or {}
    for label, rows in results:
        if said.get(label):
            out.append(f"[{label}] examined: {said[label]}")
        if totals and label in totals:   # D5 (fix round): a fact from the query, cited as label.total
            out.append(f"[{label}] {label}.total = {totals[label]} rows matched, {len(rows)} shown")
        if not rows:
            out.append(f"[{label}] no rows")
        by = (ordered_by or {}).get(label)
        for n, row in enumerate(rows, 1):
            line = f"[{label}] row {n}: " + ", ".join(f"{label}.{k} = {v}" for k, v in row.items())
            if by and n > 1 and row.get(by) is not None and row[by] == rows[n - 2].get(by):
                line += f" (same {label}.{by} as row {n - 1})"
            out.append(line)
    return "\n".join(out)


def render_gate(answer: str, results: list[tuple[str, list[dict[str, Any]]]],
                prompt: Prompt | None = None, feedback: str | None = None,
                ordered_by: dict[str, str] | None = None, examined: dict[str, str] | None = None,
                totals: dict[str, int] | None = None) -> str:
    """The question is not passed. The gate judges support, not relevance."""
    return fill(prompt or load("gate.md"), answer=answer,
                records=render_records(results, ordered_by, examined, totals), feedback=feedback_block(feedback))


# --------------------------------------------------------------------------
# check on the rewriting output
# --------------------------------------------------------------------------

class RewriteError(OutputRejected):
    """A content failure of the rewriting output. Its message names the rule only."""


# D1 (fix round, chat 18.10): a rewrite names at most this many identifiers the person did not type
MAX_ADDED_IDENTIFIERS = 20
_TOKEN = re.compile(r"[^\s,;:.!?()\[\]\"']+")


def check_rewrite(output: dict, posed: str, history: list[Exchange] | None = None) -> str:
    """Returns the question to plan. Schema validation runs before this.
    D1, by shape only: an identifier is a whole token the history presented. The rewrite may not add one
    shown by a reading whose rows were cut (Exchange.cut), nor more than MAX_ADDED_IDENTIFIERS in all;
    there it states the condition that produced them. The message gives counts only, never a value."""
    q = output["question"].strip()
    if not q:
        raise RewriteError("empty question")
    if not output["carried_from_history"] and _norm(q) != _norm(posed):
        raise RewriteError("claims to carry nothing from history but changes the question")
    known = {v for ex in history or [] for v in ex.presented}
    cut = {v for ex in history or [] for v in ex.cut}
    typed = set(_TOKEN.findall(posed))
    added = [t for t in dict.fromkeys(_TOKEN.findall(q)) if t in known and t not in typed]
    if any(t in cut for t in added):
        raise RewriteError(f"names {sum(t in cut for t in added)} identifier(s) shown by an earlier reading whose "
                           "rows were cut, so they are not the whole result; state the condition that produced "
                           "them instead")
    if len(added) > MAX_ADDED_IDENTIFIERS:
        raise RewriteError(f"names {len(added)} identifiers the latest question does not hold, more than "
                           f"{MAX_ADDED_IDENTIFIERS}; state the condition that produced them instead")
    return q
