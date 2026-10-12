"""
MedAgentPro - query assistance pipeline. Checks on the gate's output (schema 4).

The gate model returns a verdict per assertion. What happens next is decided here,
in code (4.9, NFR 19, NFR 20). The question is never involved.

The gate receives the answer as numbered sentences, and every claim names its sentence.
Output that leaves a sentence uncovered is rejected like any other malformed output, so
nothing unexamined can be presented ([demo extension], decision 12 of 12.10).
contradicted is treated as unsupported (decision 3 of 12.10, NFR 19).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .compiler import _norm


class GateOutputError(Exception):
    """The gate's output breaks a rule. Treated as a failed call, not as a verdict."""


@dataclass
class GateResult:
    decision: str                       # keep | request_input
    kept: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    supported: list[bool] = field(default_factory=list)   # per sentence: every claim naming it supported
    kept_by_sentence: dict[int, list[str]] = field(default_factory=dict)


def render_answer(sentences: list[tuple[str, str]]) -> str:
    """The answer as the gate sees it: one numbered line per sentence, under its reading label."""
    return "\n".join(f"{n}. [{label}] {text}" for n, (label, text) in enumerate(sentences, 1))


def evaluate(output: dict, sentences: list[tuple[str, str]], aliases: dict[str, set[str]]) -> GateResult:
    """sentences are (label, text as filled), in the order the gate received them.
    aliases maps each reading label to the aliases its SELECT produced."""
    covered: set[int] = set()
    for c in output["claims"]:
        n = c["sentence"]
        if not 1 <= n <= len(sentences):
            raise GateOutputError(f"claim names sentence {n}, and the answer has {len(sentences)}")
        if _norm(c["text"]) not in _norm(sentences[n - 1][1]):
            raise GateOutputError(f"claim not found verbatim in sentence {n}: {c['text']!r}")
        for ref in c["evidence"]:
            label, _, alias = ref.partition(".")
            if alias not in aliases.get(label, set()):
                raise GateOutputError(f"reference {ref} resolves to no retrieved field")
        if c["verdict"] in ("supported", "contradicted") and not c["evidence"]:
            raise GateOutputError(f"{c['verdict']} with no reference: {c['text']!r}")
        if c["verdict"] == "uncovered" and c["evidence"]:
            raise GateOutputError(f"uncovered with a reference: {c['text']!r}")
        covered.add(n)
    skipped = sorted(set(range(1, len(sentences) + 1)) - covered)
    if skipped:
        raise GateOutputError(f"sentence(s) {skipped} not examined")

    supported = [True] * len(sentences)
    kept, removed, by_sentence = [], [], {}
    for c in output["claims"]:
        if c["verdict"] == "supported":
            kept.append(c["text"])
            by_sentence.setdefault(c["sentence"], []).append(c["text"])
        else:
            removed.append(c["text"])
            supported[c["sentence"] - 1] = False
    return GateResult("keep" if kept else "request_input", kept, removed, supported, by_sentence)
