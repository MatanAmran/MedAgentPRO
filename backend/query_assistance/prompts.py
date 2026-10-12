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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compiler import Catalog, Conversation, _norm

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


def load(file: str) -> Prompt:
    text = (PROMPTS / file).read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    meta = dict(line.split(": ", 1) for line in m.group(1).splitlines())
    return Prompt(meta["prompt"], meta["version"], meta["output_schema"], text[m.end():])


def fill(prompt: Prompt, **values: str) -> str:
    out = prompt.body
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{(\w+)\}\}", out)
    if left:
        raise ValueError(f"{prompt.name}: unfilled {left}")
    return out


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
    """
    posed: str
    understood: str | None = None      # the rewritten question, where there was one
    answer: str | None = None
    presented: list[str] = field(default_factory=list)


@dataclass
class History:
    text: str
    layer: int                          # 1 whole, 2 older answers dropped, 3 summary needed
    to_summarize: list[Exchange] = field(default_factory=list)


def _render_exchange(i: int, ex: Exchange, with_answer: bool) -> str:
    lines = [f"Q{i} (posed): {ex.posed}"]
    if ex.understood:
        lines.append(f"Q{i} (understood as): {ex.understood}")
    if with_answer and ex.answer:
        lines.append(f"A{i}: {ex.answer}")
    if with_answer and ex.presented:
        lines.append("Presented identifiers: " + ", ".join(ex.presented))
    return "\n".join(lines)


def build_history(exchanges: list[Exchange], budget: int = HISTORY_BUDGET_TOKENS,
                  summary: str | None = None) -> History:
    """Layer 1: everything. Layer 2: answers of older exchanges are dropped and their
    questions kept, done in code. Layer 3: the oldest exchanges go to a model summary,
    which the caller supplies and which is never a source of identifiers (NFR 27)."""
    if not exchanges:
        return History("", 1)
    last = len(exchanges)
    full = "\n".join(_render_exchange(i, ex, True) for i, ex in enumerate(exchanges, 1))
    if _tokens(full) <= budget:
        return History(full, 1)

    def reduced(start: int) -> str:
        parts = [_render_exchange(i, ex, i == last) for i, ex in enumerate(exchanges, 1) if i >= start]
        return "\n".join(parts)

    text = reduced(1)
    if _tokens(text) <= budget:
        return History(text, 2)

    start = 1
    while start < last and _tokens(reduced(start)) > budget:
        start += 1
    head = ("Summary of earlier exchanges, not a source of identifiers: " + summary + "\n") if summary else ""
    return History(head + reduced(start), 3, exchanges[:start - 1])


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

def render_rewrite(history: History, question: str, prompt: Prompt | None = None) -> str:
    return fill(prompt or load("rewrite.md"), history=history.text, question=question)


def question_block(posed: str, rewritten: str | None = None) -> str:
    """Both forms where the question was rewritten, so a later stage can correct a wrong rewrite
    (4.9, "later stages receive both forms")."""
    if rewritten and _norm(rewritten) != _norm(posed):
        return f"Question as posed: {posed}\nQuestion: {rewritten}"
    return f"Question: {posed}"


def render_intent(cat: Catalog, role: str, posed: str, rewritten: str | None = None,
                  prompt: Prompt | None = None) -> str:
    return fill(prompt or load("intent_analysis.md"), schema=render_schema(cat, role),
                question_block=question_block(posed, rewritten))


def render_records(results: list[tuple[str, list[dict[str, Any]]]]) -> str:
    out = []
    for label, rows in results:
        if not rows:
            out.append(f"[{label}] no rows")
        for n, row in enumerate(rows, 1):
            out.append(f"[{label}] row {n}: " + ", ".join(f"{label}.{k} = {v}" for k, v in row.items()))
    return "\n".join(out)


def render_gate(answer: str, results: list[tuple[str, list[dict[str, Any]]]],
                prompt: Prompt | None = None) -> str:
    """The question is not passed. The gate judges support, not relevance."""
    return fill(prompt or load("gate.md"), answer=answer, records=render_records(results))


# --------------------------------------------------------------------------
# check on the rewriting output
# --------------------------------------------------------------------------

class RewriteError(Exception):
    pass


def check_rewrite(output: dict, posed: str) -> str:
    """Returns the question to plan. Schema validation runs before this."""
    q = output["question"].strip()
    if not q:
        raise RewriteError("empty question")
    if not output["carried_from_history"] and _norm(q) != _norm(posed):
        raise RewriteError("claims to carry nothing from history but changes the question")
    return q
