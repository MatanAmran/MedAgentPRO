"""
MedAgentPro - one model call as metadata only. [operational, not in the book]

The R9 smoke test reads V02 (no temperature, the max_tokens sent) and V04 (the feedback attempt rate,
R4) from one line per model call on the operational channel. Task 10's "How this was answered" panel is
meant to show the same fields, so both are built from this one record (chat decisions, 14.10, 15:30).

A CallTrace is built from a Call and holds names, numbers and fixed words only: never the prompt, the
model's output, a value from a record (NFR 21) or the API key (G3). A Call's rejection can hold
model-written text, so only the name of the check that failed is kept. Of the output itself only its
length and, for the gate, how many claims it made are kept (task 10a, chat 21:50).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .calls import Call

PROVIDER_DEFAULT = "provider default"
GATE_PROMPT = "support_scoring"


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
    output_chars: int | None          # the length of the model's text, never the text
    reasoning_tokens: int | None      # of output_tokens, those spent thinking; never the thinking itself
    claims: int | None                # the gate only: how many claims its output made
    temperature: float | None         # what was sent; None where nothing was (R1)
    max_tokens: int | None            # what was sent; None where nothing was
    duration_ms: float | None
    check: str | None                 # the failure kind or the rejecting check's name, never its message
    cost_usd: float | None = None     # task 10: as the gateway priced it; None is unknown, not free

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


def _claims(c: Call) -> int | None:
    """The gate's claim count, read from its output's shape only; None for any other prompt, or where
    the output holds no list of claims."""
    if c.response is None or c.prompt_identity.split("@")[0] != GATE_PROMPT:
        return None
    try:
        out = c.response.json()
    except (ValueError, TypeError):
        return None
    claims = out.get("claims") if isinstance(out, dict) else None
    return len(claims) if isinstance(claims, list) else None


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
            output_chars=len(r.text) if r and r.text is not None else None,
            reasoning_tokens=r.reasoning_tokens if r else None, claims=_claims(c),
            temperature=r.temperature if r else None, max_tokens=r.max_tokens if r else None,
            duration_ms=c.duration_ms, check=c.check, cost_usd=r.cost_usd if r else None))
    return out
