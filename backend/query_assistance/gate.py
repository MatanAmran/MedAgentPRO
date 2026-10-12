"""
MedAgentPro - query assistance pipeline. Checks on the gate's output (schema 4).

The gate model returns a verdict per assertion. What happens next is decided here,
in code (4.9, NFR 19, NFR 20). The question is never involved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .compiler import _norm


class GateOutputError(Exception):
    """The gate's output breaks a rule. Treated as a failed call, not as a verdict."""


@dataclass
class GateResult:
    decision: str                       # keep | answer_falls | request_input | nothing_kept_no_rows
    kept: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def evaluate(output: dict, answer: str, aliases: dict[str, set[str]], rows_returned: bool) -> GateResult:
    """aliases maps each reading label to the aliases its SELECT produced."""
    norm_answer = _norm(answer)
    for c in output["claims"]:
        if _norm(c["text"]) not in norm_answer:
            raise GateOutputError(f"claim not found verbatim in the answer: {c['text']!r}")
        for ref in c["evidence"]:
            label, _, alias = ref.partition(".")
            if alias not in aliases.get(label, set()):
                raise GateOutputError(f"reference {ref} resolves to no retrieved field")
        if c["verdict"] in ("supported", "contradicted") and not c["evidence"]:
            raise GateOutputError(f"{c['verdict']} with no reference: {c['text']!r}")
        if c["verdict"] == "uncovered" and c["evidence"]:
            raise GateOutputError(f"uncovered with a reference: {c['text']!r}")

    if any(c["verdict"] == "contradicted" for c in output["claims"]):
        return GateResult("answer_falls", removed=[c["text"] for c in output["claims"]])

    kept = [c["text"] for c in output["claims"] if c["verdict"] == "supported"]
    removed = [c["text"] for c in output["claims"] if c["verdict"] != "supported"]
    if kept:
        return GateResult("keep", kept, removed)
    return GateResult("request_input" if rows_returned else "nothing_kept_no_rows", [], removed)
