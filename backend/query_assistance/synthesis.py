"""
MedAgentPro - query assistance pipeline. Synthesis (schema 5).

The synthesis model drafts the answer from the rows retrieved under each reading
(4.9, Table 12). It does not write values. It writes text with symbolic references,
{label.alias#row}, and code fills each one from the row it names (2.6, [25]). So a value
in an answer is the value in the record by construction (NFR 6), and every sentence
references the records it rests on (NFR 4).

What code decides, structurally and without reading meaning:
  - every reference names a reading that returned rows, an alias in it and a row that exists
  - no reference points at an empty field
  - no digit stands outside a reference unless the question as posed holds that token, or a
    literal value of the plan that ran under that reading (WHERE, HAVING), which the compiler
    validated and "Examined" shows. So a count, a sum or an identifier the model typed is
    rejected (NFR 6), and "in week 5" for a plan on week 5 is not (review F7 of 12.10)
  - an identifier that is a value of an identifier column in the rows retrieved is never typed,
    even where the question or the plan holds it. Typed, it loses its row, and "S-002 had
    {late.late_lines#1}" would pair S-002 with another supplier's value (review F11 of 12.10)
What code does not decide: whether the words around the references are true, for example
"had the most". That is the gate's (4.9, NFR 19). The ordering itself is shown by code
beside the answer, from the plan's ORDER_BY.

NFR 21: content held in a record must not alter which retrievals run. A value of a free-text
field (a text column that is neither an identifier nor a closed set of values) reaches the
person in the answer, but the conversation history receives the answer with that value
replaced by its reference, and only identifier columns supply presented identifiers.

STATUS: schema 5 is "demo" in medagentpro_schemas.json. The book keeps it blocked until the
company data structure arrives. The approach is in the book (4.9, [25]). The exact syntax
{label.alias#row} is decided here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from model_client.client import ModelGateway, ModelResponse

from . import calls as model_calls
from . import prompts
from .calls import MODEL_CALL_ATTEMPTS, Call, OutputRejected, StageRejected  # noqa: F401  (re-exported)
from .compiler import HERE, Catalog

REF_RE = re.compile(r"\{([a-z][a-z0-9_]{0,39})\.([a-z][a-z0-9_]{0,39})#(\d+)\}")
# D5 (fix round): how many rows a reading matched, before LIMIT. A reference with no row, filled by code
TOTAL_RE = re.compile(r"\{([a-z][a-z0-9_]{0,39})\.total\}")
TOKEN_RE = re.compile(r"[^\s,;:()\[\]\"']+")


class SynthesisOutputError(OutputRejected):
    """The draft breaks a rule: a content failure (R4). Its feedback names the reading, the sentence
    by number and the rule, and never quotes the sentence or a token in it, which may be a value
    from a record (P4, NFR 21)."""


@dataclass
class Material:
    """What one reading retrieved, as synthesis and its checks see it.

    columns    alias -> the column it selects, or None for a count, a sum or a metric.
               Decides which values are free text (NFR 21) and which are identifiers
    literals   tokens holding a digit among the literal values of the plan (WHERE, HAVING).
               A sentence of this reading may write them outside a reference
    id_aliases aliases that select an identifier column. Their values in the rows are written
               only as references, in any reading (F11)
    examined   what the reading examined, as "Examined" words it, for the records' examined line
               (chat, 18.10, 09:40). From the plan and the catalog only
    total      D5 (fix round): the rows the query matched before LIMIT, the value of {label.total}
    """
    label: str
    rows: list[dict[str, Any]]
    columns: dict[str, str | None] = field(default_factory=dict)
    order_by: list[dict] = field(default_factory=list)
    truncated: bool = False
    literals: set[str] = field(default_factory=set)
    id_aliases: set[str] = field(default_factory=set)
    examined: str | None = None
    total: int | None = None
    unit: prompts.RowUnit | None = None   # G-TOTAL: what a row is, where not a record (contract.row_unit)

    def identifier_tokens(self) -> set[str]:
        return {t for row in self.rows for a in self.id_aliases if row.get(a) is not None
                for t in _tokens_with_digits(format_value(row[a]))}


def _plan_values(node: Any) -> list[Any]:
    """Every {"value": v} under a WHERE or HAVING node."""
    if isinstance(node, dict):
        if "value" in node and not isinstance(node["value"], (dict, list)):
            return [node["value"]] + [v for k, n in node.items() if k != "value" for v in _plan_values(n)]
        return [v for n in node.values() for v in _plan_values(n)]
    if isinstance(node, list):
        return [v for n in node for v in _plan_values(n)]
    return []


def material(label: str, plan: dict, rows: list[dict], truncated: bool = False,
             cat: Catalog | None = None, examined: str | None = None, total: int | None = None,
             unit: prompts.RowUnit | None = None) -> Material:
    """Builds a Material from a reading's plan, so the alias -> column map and the literals
    come from what ran. The catalog says which columns are identifiers."""
    cols = {i["as"]: i["expr"].get("col") for i in plan["SELECT"]}
    values = _plan_values(plan.get("WHERE")) + _plan_values(plan.get("HAVING", []))
    literals = {t for v in values if not isinstance(v, bool) for t in _tokens_with_digits(format_value(v))}
    ids = {a for a, col in cols.items() if col and cat and (cat.column(col) or {}).get("identifier")}
    return Material(label, rows, cols, plan.get("ORDER_BY", []), truncated, literals, ids, examined,
                    len(rows) if total is None else total, unit)


@dataclass
class Sentence:
    template: str                  # as the model wrote it, with references
    refs: list[tuple[str, str, int]]
    totals: list[str] = field(default_factory=list)   # D5: labels whose {label.total} the sentence holds

    @property
    def evidence(self) -> list[str]:
        """label.alias for each field the sentence rests on, for the gate and the audit entry."""
        return list(dict.fromkeys([f"{lab}.{alias}" for lab, alias, _ in self.refs]
                                  + [f"{lab}.total" for lab in self.totals]))


@dataclass
class Draft:
    sentences: dict[str, list[Sentence]]
    materials: dict[str, Material]
    calls: list[Call] = field(default_factory=list)   # every call made, for the audit entry (NFR 3)

    @property
    def responses(self) -> list[ModelResponse]:
        return [c.response for c in self.calls if c.response is not None]

    @property
    def rejections(self) -> list[str]:
        return [c.rejection for c in self.calls if c.rejection]

    def filled(self) -> list[tuple[str, str]]:
        """(label, sentence with references filled), in order. What the gate examines."""
        return [(label, fill(s.template, self.materials)) for label, ss in self.sentences.items() for s in ss]

    def only(self, keep: list[bool]) -> "Draft":
        """The draft with only the sentences marked True, in the order of filled()."""
        flags = iter(keep)
        kept = {label: [s for s in ss if next(flags)] for label, ss in self.sentences.items()}
        return Draft({label: ss for label, ss in kept.items() if ss}, self.materials, self.calls)

    def answer(self, label: str, cat: Catalog | None = None, for_history: bool = False) -> str:
        """The text of a reading: references filled from their rows. The gate examines this.
        With for_history, values of free-text fields stay as references (NFR 21)."""
        m = self.materials[label]
        masked = free_text_aliases(m, cat) if for_history else set()
        return " ".join(fill(s.template, self.materials, masked_aliases={label: masked})
                        for s in self.sentences.get(label, []))

    def segments(self, label: str, cat: Catalog | None = None) -> list[dict]:
        """UI refresh, phase C [demo extension]: the answer of a reading, cut where references were filled.
        Joined, the texts are answer(label) exactly. A filled reference carries {alias, row} (row 1-based in
        the reading's rows), except a value from a free-text column, which is plain text (NFR 21)."""
        m = self.materials[label]
        free = free_text_aliases(m, cat)
        out: list[dict] = []

        def text(t: str):
            if not t:
                return
            if out and "ref" not in out[-1]:
                out[-1] = {"text": out[-1]["text"] + t}
            else:
                out.append({"text": t})

        for i, s in enumerate(self.sentences.get(label, [])):
            if i:
                text(" ")
            at = 0
            tmpl = fill_totals(s.template, self.materials)   # D5: a count has no row, so it is plain text
            for mt in REF_RE.finditer(tmpl):
                text(tmpl[at:mt.start()])
                ref_label, alias, row = mt.group(1), mt.group(2), int(mt.group(3))
                value = format_value(self.materials[ref_label].rows[row - 1][alias])
                if ref_label == label and alias not in free:
                    out.append({"ref": {"alias": alias, "row": row}, "text": value})
                else:
                    text(value)
                at = mt.end()
            text(tmpl[at:])
        return out

    def presented_identifiers(self, cat: Catalog) -> list[str]:
        """Identifiers the answer presents, in order. Only identifier columns supply them,
        never a free-text value that happens to look like one (NFR 21, NFR 27)."""
        out = []
        for label, ss in self.sentences.items():
            m = self.materials[label]
            for s in ss:
                for _lab, alias, row in s.refs:
                    col = m.columns.get(alias)
                    if col and (cat.column(col) or {}).get("identifier"):
                        v = str(m.rows[row - 1][alias])
                        if v not in out:
                            out.append(v)
        return out


def validator() -> Draft202012Validator:
    bundle = json.loads((HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))
    return Draft202012Validator(bundle["schemas"]["synthesis"]["schema"])


# --------------------------------------------------------------------------
# filling references
# --------------------------------------------------------------------------

def format_value(v: Any) -> str:
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def free_text_aliases(m: Material, cat: Catalog | None) -> set[str]:
    """Aliases whose values are free text: a text column that is not an identifier and
    declares no closed set of values. Without a catalog, every text column counts."""
    out = set()
    for alias, col in m.columns.items():
        if not col:
            continue
        spec = cat.column(col) if cat else None
        if spec is None:
            out.add(alias)
        elif spec["type"] == "text" and not spec.get("identifier") and not spec.get("values"):
            out.add(alias)
    return out


def fill_totals(template: str, materials: dict[str, Material]) -> str:
    """D5: {label.total} is the reading's count of matched rows, from the query, never from the model."""
    return TOTAL_RE.sub(lambda mt: format_value(materials[mt.group(1)].total), template)


def fill(template: str, materials: dict[str, Material],
         masked_aliases: dict[str, set[str]] | None = None) -> str:
    masked_aliases = masked_aliases or {}
    template = fill_totals(template, materials)

    def sub(mt):
        label, alias, row = mt.group(1), mt.group(2), int(mt.group(3))
        if alias in masked_aliases.get(label, set()):
            return f"[{label}.{alias}]"
        return format_value(materials[label].rows[row - 1][alias])
    return REF_RE.sub(sub, template)


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------

def _tokens_with_digits(text: str) -> list[str]:
    return [t.strip(".!?").casefold() for t in TOKEN_RE.findall(text) if any(ch.isdigit() for ch in t)]


def check(output: dict, materials: list[Material], question: str) -> dict[str, list[Sentence]]:
    """Schema validation runs before this. Returns the parsed sentences per label."""
    by_label = {m.label: m for m in materials if m.rows}
    question_tokens = set(_tokens_with_digits(question))   # the question as posed, never the rewrite
    row_identifiers = {t for m in by_label.values() for t in m.identifier_tokens()}   # F11, any reading
    result: dict[str, list[Sentence]] = {}
    def reject(message: str, feedback: str):
        raise SynthesisOutputError(message, feedback)

    for r in output["readings"]:
        label = r["label"]
        if label in result:
            reject(f"reading {label} appears twice", f"reading {label} appears twice")
        if label not in by_label:
            reject(f"reading {label} returned no rows or was not retrieved",
                   f"reading {label} returned no rows or was not retrieved, so it gets no entry")
        m = by_label[label]
        sentences = []
        for k, s in enumerate(r["sentences"], 1):
            text = s["text"]
            at = f"reading {label}, sentence {k}"
            refs = [(a, b, int(n)) for a, b, n in REF_RE.findall(text)]
            totals = TOTAL_RE.findall(text)
            if not refs and not totals:
                reject(f"'{text}' references no field (NFR 4)", f"{at} holds no reference. Every sentence holds one")
            if any(lab != label for lab in totals):
                reject(f"'{text}' references another reading's total from reading {label}",
                       f"{at} references another reading's total. A sentence references its own reading only")
            for lab, alias, row in refs:
                ref = f"{{{lab}.{alias}#{row}}}"
                if lab != label:
                    reject(f"'{text}' references reading {lab} from reading {label}",
                           f"{at} references reading {lab}. A sentence references its own reading only")
                if alias not in m.rows[0]:
                    reject(f"{ref}: no field {alias} in reading {label}",
                           f"{at}: {ref} names no field of reading {label}")
                if not 1 <= row <= len(m.rows):
                    reject(f"{ref}: reading {label} has {len(m.rows)} row(s)",
                           f"{at}: {ref} names row {row}, and reading {label} has {len(m.rows)}")
                if m.rows[row - 1][alias] is None:
                    reject(f"{ref} points at an empty field",
                           f"{at}: {ref} is empty. Say that it is not recorded, without referencing it")
            outside = TOTAL_RE.sub(" ", REF_RE.sub(" ", text))
            if "{" in outside or "}" in outside:
                reject(f"'{text}' holds a malformed reference", f"{at} holds a malformed reference")
            typed = _tokens_with_digits(outside)
            ids = [t for t in typed if t in row_identifiers]
            if ids:
                reject(f"'{text}' types {ids[0]!r}, an identifier in the records retrieved. "
                       f"Write it as a reference to its row (F11)",
                       f"{at} types an identifier that appears in the records. Write it as a reference to its row")
            stray = [t for t in typed if t not in question_tokens and t not in m.literals]
            if stray:
                reject(f"'{text}' writes {stray[0]!r} outside a reference. Values, counts "
                       f"and identifiers come from the records (NFR 6)",
                       f"{at} types a digit outside a reference. Values, counts and identifiers come "
                       f"from references only")
            sentences.append(Sentence(text, refs, totals))
        result[label] = sentences
    missing = set(by_label) - set(result)
    if missing:   # [proposal]
        reject(f"no sentence for reading(s) {sorted(missing)}, which returned rows",
               f"reading(s) {sorted(missing)} returned rows and have no sentence")
    return result


# --------------------------------------------------------------------------
# rendering and the calls
# --------------------------------------------------------------------------

def ordered_by(materials: list[Material]) -> dict[str, str]:
    """Each reading's first ORDER_BY alias, for the tie markers in the records (task 10a)."""
    return {m.label: m.order_by[0]["by"] for m in materials if m.order_by and m.order_by[0].get("by")}


def examined_of(materials: list[Material]) -> dict[str, str]:
    """Each reading's examined line, for the records synthesis and the gate see (chat, 18.10, 09:40)."""
    return {m.label: m.examined for m in materials if m.examined}


def totals_of(materials: list[Material]) -> dict[str, int]:
    """D5: each reading's matched rows, for the count line in the records synthesis and the gate see."""
    return {m.label: m.total for m in materials if m.total is not None}


def units_of(materials: list[Material]) -> dict[str, prompts.RowUnit]:
    """G-TOTAL: what each grouped or computed reading's rows are, for its count line."""
    return {m.label: m.unit for m in materials if m.unit is not None}


def _records(materials: list[Material]) -> str:
    return prompts.render_records([(m.label, m.rows) for m in materials if m.rows], ordered_by(materials),
                                  examined_of(materials), totals_of(materials), units_of(materials))


def _rejected(rejected: list[str] | None) -> str:
    """[proposal, decision 4 of 12.10] A fresh draft after nothing survived the gate is told what
    was rejected. Without it, the same input at temperature 0 returns the same answer."""
    if not rejected:
        return ""
    lines = "\n".join(f"- {t}" for t in rejected)
    return ("Rejected. A check found these statements unsupported by the records. "
            f"Do not state them again, in any wording:\n{lines}\n")


def render(question: str, materials: list[Material], prompt: prompts.Prompt | None = None,
           rejected: list[str] | None = None, rewritten: str | None = None, feedback: str | None = None) -> str:
    return prompts.fill(prompt or prompts.load("synthesis.md"),
                        question_block=prompts.question_block(question, rewritten),
                        records=_records(materials), rejected=_rejected(rejected),
                        feedback=prompts.feedback_block(feedback))


def render_redraft(question: str, materials: list[Material], kept: dict[str, list[str]],
                   prompt: prompts.Prompt | None = None, rewritten: str | None = None,
                   feedback: str | None = None) -> str:
    lines = [f"[{label}] {t}" for label, ts in kept.items() for t in ts]
    return prompts.fill(prompt or prompts.load("synthesis_redraft.md"),
                        question_block=prompts.question_block(question, rewritten),
                        records=_records(materials), kept="\n".join(lines), feedback=prompts.feedback_block(feedback))


def _call(gateway: ModelGateway, render_with: Any, prompt: prompts.Prompt, model: str,
          materials: list[Material], question: str, attempts: int) -> Draft:
    """Structured output on (R2): the schema goes to the provider, and the full schema and check()
    run in code. A content failure gets one feedback attempt (R4)."""
    v = validator()
    by_label = {m.label: m for m in materials}
    draft_, made = model_calls.ask(
        gateway, model, prompt.identity, render_with,
        lambda out: Draft(check(out, materials, question), by_label),
        attempts, output_schema=v.schema, validator=v)
    draft_.calls = made
    return draft_


def draft(gateway: ModelGateway, question: str, materials: list[Material], model: str,
          attempts: int = MODEL_CALL_ATTEMPTS, rejected: list[str] | None = None,
          rewritten: str | None = None, prompt: prompts.Prompt | None = None) -> Draft:
    """question is the question as posed. The prompt carries both forms where it was rewritten
    (4.9). A digit outside a reference may come from the question as posed, never from the
    rewrite, which is model text (NFR 6, NFR 27), or from the plan's literals."""
    pr = prompt or prompts.load("synthesis.md")
    return _call(gateway, lambda fb: render(question, materials, pr, rejected, rewritten, fb), pr, model,
                 materials, question, attempts)


def redraft(gateway: ModelGateway, question: str, materials: list[Material], kept: dict[str, list[str]],
            model: str, attempts: int = MODEL_CALL_ATTEMPTS, rewritten: str | None = None,
            prompt: prompts.Prompt | None = None) -> Draft:
    """NFR 19: regenerated at most once, from the assertions the gate kept. The caller runs
    the gate on the result once more and does not redraft again (Test 17)."""
    pr = prompt or prompts.load("synthesis_redraft.md")
    keep = [m for m in materials if m.label in kept]
    return _call(gateway, lambda fb: render_redraft(question, keep, kept, pr, rewritten, fb), pr, model,
                 keep, question, attempts)
