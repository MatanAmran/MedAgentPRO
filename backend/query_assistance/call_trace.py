"""
MedAgentPro - one model call as metadata only. [operational, not in the book]

The R9 smoke test reads V02 (no temperature, the max_tokens sent) and V04 (the feedback attempt rate,
R4) from one line per model call on the operational channel. Task 10's "How this was answered" panel is
meant to show the same fields, so both are built from this one record (chat decisions, 14.10, 15:30).

A CallTrace is built from a Call and holds names, numbers and fixed words only: never the prompt, the
model's output, a value from a record (NFR 21) or the API key (G3). A Call's rejection can hold
model-written text, so only the name of the check that failed is kept.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .calls import Call

PROVIDER_DEFAULT = "provider default"


@dataclass(frozen=True)
class CallTrace:
    stage: str
    prompt_identity: str
    model: str
    attempt_number: int
    feedback_attempt: bool
    status: str                       # accepted | rejected | failed
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    temperature: float | None         # what was sent; None where nothing was (R1)
    max_tokens: int | None            # what was sent; None where nothing was
    duration_ms: float | None
    check: str | None                 # the failure kind or the rejecting check's name, never its message

    @property
    def temperature_sent(self) -> str:
        return PROVIDER_DEFAULT if self.temperature is None else str(self.temperature)

    def as_dict(self) -> dict:
        return asdict(self)

    def log_line(self, **context: object) -> str:
        """`model call: key=value ...`, context first (exchange, request, conversation). A missing
        value is written as `-`, and the temperature as sent, so the line reads alone."""
        fields = {**context, **self.as_dict(), "temperature": self.temperature_sent}
        return "model call: " + " ".join(f"{k}={'-' if v is None else v}" for k, v in fields.items())


def traces(stage: str, made: list[Call], model: str) -> list[CallTrace]:
    """One trace per call, in the order made; the attempt number is the position."""
    out = []
    for n, c in enumerate(made, 1):
        r = c.response
        out.append(CallTrace(
            stage=stage, prompt_identity=c.prompt_identity, model=r.model if r else model, attempt_number=n,
            feedback_attempt=c.feedback_attempt,
            status="failed" if r is None else "rejected" if c.rejection else "accepted",
            finish_reason=r.finish_reason if r else None,
            input_tokens=r.input_tokens if r else None, output_tokens=r.output_tokens if r else None,
            temperature=r.temperature if r else None, max_tokens=r.max_tokens if r else None,
            duration_ms=c.duration_ms, check=c.check))
    return out
