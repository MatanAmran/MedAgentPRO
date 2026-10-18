"""
MedAgentPro - query assistance pipeline. One model stage's calls, classified (task 5 review, R4).

No retry without a change in input. A call repeated with the same prompt is a paid bet, so:

  a call that failed          timeout, connection, 429, 5xx, a client error: no further attempt
  a response with a problem   refusal, empty content, truncation: billed, recorded, no further attempt
  a format failure            text that is not JSON after the fence is removed (R3), or a breach of
                              what the provider enforced (R2): no further attempt
  a content failure           a breach of the full schema checked in code, or of a stage's own checks
                              (OutputRejected, CompileError): one further attempt whose prompt carries
                              the specific rejection [proposal, not in the book; Table 15 names "Model
                              call attempts" only]

The feedback a further attempt carries is the rejection's `feedback`, built from names, numbers and
the rule broken, never from a value a record holds (NFR 21, P4). Every call is kept, with whether it
was a feedback attempt and why it was rejected, for the audit entry (NFR 3, P6).
"""

from __future__ import annotations

import functools
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from model_client.client import BudgetExceeded, ModelCallError, ModelConfig, ModelGateway, ModelResponse

# PROVISIONAL. Table 15, "Model call attempts": the first, and one feedback attempt on a content failure.
MODEL_CALL_ATTEMPTS = 2


class OutputRejected(Exception):
    """A content failure. `feedback` is what the further attempt is told: names and the rule, no value."""

    def __init__(self, message: str, feedback: str | None = None):
        super().__init__(message)
        self.feedback = feedback


class SchemaBreach(OutputRejected):
    """`check` names the rule and where it broke, `SchemaBreach:<keyword>@<path>` (task 10a, chat 01:15):
    the path is built from keys the schema defines and from item numbers, never from a value or a key
    the model made up (every object is closed, so such a key breaks additionalProperties at its parent)."""

    def __init__(self, message: str, feedback: str | None = None, check: str = "SchemaBreach"):
        super().__init__(message, feedback)
        self.check = check


# An exception ask() does not handle (an open breaker, the budget, a cancellation, a bug) leaves with
# the calls already made attached under this attribute, so the stage can still record them (NFR 3).
# Set with setattr: the exception's type is not ours, and wrapping it would change what callers catch.
_CALLS_MADE = "calls_made"


def calls_made(e: BaseException) -> list[Call]:
    return getattr(e, _CALLS_MADE, [])


@dataclass
class Call:
    prompt_identity: str
    response: ModelResponse | None      # None where the call failed
    feedback_attempt: bool
    rejection: str | None = None        # why the output was rejected, or the call failed
    # A name for what failed, never text: the failure kind (timeout, budget, refusal, format_not_json,
    # ...) or the class of the check that rejected the output (CompileError, GateOutputError, ...).
    # The rejection above may hold model-written text; this never does (R9 log line, P6)
    check: str | None = None
    duration_ms: float | None = None    # the gateway call alone, from sending to the response or the error


class StageFailed(Exception):
    """No further attempt: a call that failed, a response with a problem, or a format failure."""

    def __init__(self, kind: str, message: str, calls: list[Call]):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.calls = calls


class StageRejected(Exception):
    """Content failure in every attempt."""

    def __init__(self, message: str, calls: list[Call]):
        super().__init__(message)
        self.calls = calls

    @property
    def responses(self) -> list[ModelResponse]:
        return [c.response for c in self.calls if c.response is not None]

    @property
    def rejections(self) -> list[str]:
        return [c.rejection for c in self.calls if c.rejection]


def _innermost(error):
    """Where oneOf or anyOf fails, the alternative that came closest, as jsonschema's best_match finds it:
    the deepest error inside it. Without this, every mistake inside a WHERE reads the same, "the value at
    readings/0/plan/WHERE breaks 'oneOf'" (task 10a, chat 01:15). Only the path and the keyword are used."""
    return best_match([error]) if error.validator in ("oneOf", "anyOf") and error.context else error


def _where(error) -> str:
    return "/".join(map(str, error.absolute_path))


def schema_feedback(error) -> str:
    """Where and which rule, never the value that broke it (a gate claim or a sentence may hold one)."""
    error = _innermost(error)
    where = _where(error) or "the top level"
    limit = error.validator_value if isinstance(error.validator_value, (int, float, str, bool)) else None
    return f"the value at {where} breaks the schema rule '{error.validator}'" + (f" ({limit})" if limit is not None
                                                                                   else "")


def _first(validator: Draft202012Validator, out: Any):
    errors = sorted(validator.iter_errors(out), key=lambda e: list(e.absolute_path))
    return errors[0] if errors else None


@functools.lru_cache(maxsize=None)
def _provider_validator(schema_json: str) -> Draft202012Validator:
    """What the provider enforces: the schema as LiteLLM reduces it before sending (R2). A breach of
    it means the provider did not hold to it, which no feedback fixes."""
    from litellm.llms.anthropic.chat.transformation import AnthropicConfig
    reduced = AnthropicConfig().map_response_format_to_anthropic_output_format(
        {"type": "json_schema", "json_schema": {"name": "output", "schema": json.loads(schema_json)}})["schema"]
    return Draft202012Validator(reduced)


def provider_schema(schema: dict) -> dict:
    return _provider_validator(json.dumps(schema, sort_keys=True)).schema


def _ms(since: float | None) -> float | None:
    return None if since is None else round((time.monotonic() - since) * 1000, 1)


def ask(gateway: ModelGateway, model: str, prompt_identity: str, render: Callable[[str | None], str],
        accept: Callable[[Any], Any], attempts: int = MODEL_CALL_ATTEMPTS, output_schema: dict | None = None,
        validator: Draft202012Validator | None = None, rejected: tuple = ()) -> tuple[Any, list[Call]]:
    """render(feedback) builds the prompt. accept(output) runs the stage's own checks and returns the
    result. output_schema is sent for the provider to enforce; validator holds the full schema."""
    content = (OutputRejected,) + tuple(rejected)
    provider = _provider_validator(json.dumps(output_schema, sort_keys=True)) if output_schema else None
    calls: list[Call] = []
    feedback: str | None = None
    for n in range(attempts):
        is_feedback = n > 0
        config = ModelConfig(model=model, prompt_identity=prompt_identity, output_schema=output_schema)
        started: float | None = None
        try:
            prompt = render(feedback)
            started = time.monotonic()   # the call alone, not the rendering
            resp = gateway.invoke(prompt, config)
        except ModelCallError as e:
            calls.append(Call(prompt_identity, None, is_feedback, str(e), e.kind, _ms(started)))
            raise StageFailed(e.kind, str(e), calls) from e
        except Exception as e:
            # G1: a refusal for money came back from the provider, so the call reached it and is
            # recorded (NFR 3). The budget's own refusals, an open breaker and a cancellation sent nothing
            if isinstance(e, BudgetExceeded) and e.scope == "provider":
                calls.append(Call(prompt_identity, None, is_feedback, f"budget: {e}", "budget", _ms(started)))
            setattr(e, _CALLS_MADE, calls)
            raise
        took = _ms(started)
        problem = resp.problem()
        if problem:
            calls.append(Call(prompt_identity, resp, is_feedback, problem, problem, took))
            raise StageFailed(problem, f"the {prompt_identity} call ended with {problem}", calls)
        try:
            out = resp.json()
        except ValueError as e:
            calls.append(Call(prompt_identity, resp, is_feedback, f"format: not JSON ({e})", "format_not_json", took))
            raise StageFailed("format", "the output is not JSON", calls) from e
        breach = _first(provider, out) if provider else None
        if breach is not None:
            calls.append(Call(prompt_identity, resp, is_feedback, f"format: {schema_feedback(breach)}",
                              "format_provider_schema", took))
            raise StageFailed("format", "the output breaks what the provider enforced", calls)
        try:
            breach = _first(validator, out) if validator else None
            if breach is not None:
                inner = _innermost(breach)
                raise SchemaBreach(f"schema: {breach.message}", schema_feedback(breach),
                                   f"SchemaBreach:{inner.validator}@{_where(inner)}")
            result = accept(out)
        except content as e:
            calls.append(Call(prompt_identity, resp, is_feedback, str(e), getattr(e, "check", type(e).__name__),
                              took))
            feedback = getattr(e, "feedback", None) or getattr(e, "reason", None) or str(e)
            continue
        calls.append(Call(prompt_identity, resp, is_feedback, duration_ms=took))
        return result, calls
    raise StageRejected(f"rejected after {attempts} attempt(s): {calls[-1].rejection}", calls)
