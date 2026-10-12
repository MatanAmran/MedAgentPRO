"""
MedAgentPro - model gateway (Figure 6, 3.1.4, 6.1).

ModelGateway is the single interface every model stage calls. LiteLLMGateway
reaches the configured provider through the circuit breaker (NFR 9).
ScriptedModelGateway replaces it in tests, so behavior is tested without a model (6.1).
Every response carries the model and the tokens, which the audit entry records (NFR 3).

Every failure is classified before anything acts on it (task 5 review, R4, P8, P9):
  outage            timeout, connection failure, 429 (not for money), 5xx, 529. Counted by the
                    breaker. Not retried: the person sends the question again
  client error      any other 4xx, UnsupportedParamsError. Leaves the breaker as a value, as F1
  money             a refusal for credit or a spend limit: BudgetExceeded, not an outage
  in the response   a refusal, empty content, truncation: the call succeeded and was billed,
                    so the gateway returns it and the caller reads ModelResponse.problem()
"""

from __future__ import annotations

import json
import re
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from shared.circuit_breaker import CircuitBreaker

# PROVISIONAL (review R4, P7). LiteLLM's own default is 6000 seconds. 120 is enough for the output cap,
# and response time is measured to the first token anyway (Table 15).
MODEL_REQUEST_TIMEOUT_SECONDS = 120.0

_FENCE = re.compile(r"\A\s*```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)\r?\n[ \t]*```\s*\Z", re.S)


@dataclass
class ModelConfig:
    model: str
    temperature: float | None = None       # None: not sent, the provider's default applies (R1)
    max_tokens: int | None = None
    output_schema: dict | None = None      # enforced by the provider where it supports it (R2)
    prompt_identity: str = ""              # e.g. intent_analysis@1, recorded with the call


@dataclass
class ModelResponse:
    text: str | None
    model: str
    input_tokens: int | None
    output_tokens: int | None
    prompt_identity: str = ""
    cache_read_tokens: int | None = None      # reported by the provider; prompt caching is not used (T1)
    cache_write_tokens: int | None = None
    finish_reason: str | None = None
    temperature: float | None = None          # what was sent, None where nothing was (R1)

    def problem(self) -> str | None:
        """A call that succeeded and was billed but holds no usable output (R4, P10). LiteLLM maps
        Anthropic's refusal to content_filter and max_tokens to length."""
        if self.finish_reason in ("content_filter", "refusal"):
            return "refusal"
        if self.finish_reason == "length":
            return "truncated"
        if self.text is None or not self.text.strip():
            return "empty"
        return None

    def json(self) -> Any:
        """One code fence enclosing the whole text, and only that, is removed first (R3). Anything
        else that is not JSON is a format failure."""
        if self.text is None:
            raise ValueError("the output is empty")
        m = _FENCE.match(self.text)
        body = m.group(1) if m and "```" not in m.group(1) else self.text
        return json.loads(body)


class ModelGateway(ABC):
    @abstractmethod
    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse: ...


class BudgetExceeded(Exception):
    """The call was not sent, or the provider refused it for money (task 5). Not retried,
    and not an outage: the circuit breaker does not count it.

    scope  total | exchange | provider | model"""

    def __init__(self, message: str, scope: str = "total"):
        super().__init__(message)
        self.scope = scope


class RateLimited(Exception):
    """Too many exchanges in the last minute (task 5). Refused before any model call."""


class ModelCallError(Exception):
    """A call that failed, classified (R4).

    kind              timeout | connection | rate_limited | server_error | client_error | unexpected
    reached_provider  False only where the request is known not to have left (R5, P8)
    outage            counted by the breaker (P9)"""

    def __init__(self, kind: str, message: str, reached_provider: bool = True, outage: bool = False):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.reached_provider = reached_provider
        self.outage = outage


def is_money_refusal(e: Exception) -> bool:
    """The provider refuses for credit or a spend limit: HTTP 400 about credit or usage limits,
    or 429 with enforced_spend_limit_reached. Read from the provider's error, never from model text."""
    status = getattr(e, "status_code", None)
    text = f"{e} {getattr(e, 'body', '')}".casefold()
    if status == 429:
        return "enforced_spend_limit_reached" in text
    if status == 400:
        return any(k in text for k in ("credit balance", "usage limit", "spend limit", "spending limit"))
    return False


def _chain(e: BaseException):
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        yield e
        e = e.__cause__ or e.__context__


def _never_connected(e: BaseException) -> bool:
    """P8, structurally: the connection was refused or never established, so nothing was sent."""
    return any(isinstance(x, (ConnectionRefusedError, socket.gaierror)) or type(x).__name__ == "ConnectError"
               for x in _chain(e))


def classify(e: Exception) -> Exception:
    """The provider's exception, as BudgetExceeded or ModelCallError. Read from types and status codes."""
    if isinstance(e, (BudgetExceeded, ModelCallError)):
        return e
    if is_money_refusal(e):
        return BudgetExceeded(f"provider refused for credit or spend limit: {e}", scope="provider")
    names = {cls.__name__ for cls in type(e).__mro__}
    status = getattr(e, "status_code", None)
    if "UnsupportedParamsError" in names:
        return ModelCallError("client_error", str(e), reached_provider=False)
    if names & {"Timeout", "APITimeoutError", "TimeoutError", "ReadTimeout"}:
        return ModelCallError("timeout", str(e), outage=True)
    if names & {"APIConnectionError", "ConnectionError", "ConnectError"}:
        return ModelCallError("connection", str(e), reached_provider=not _never_connected(e), outage=True)
    if status == 429:
        return ModelCallError("rate_limited", str(e), outage=True)
    if isinstance(status, int) and status >= 500:
        return ModelCallError("server_error", str(e), outage=True)
    if isinstance(status, int) and 400 <= status < 500:
        return ModelCallError("client_error", str(e))
    return ModelCallError("unexpected", f"{type(e).__name__}: {e}")


class ScriptedModelGateway(ModelGateway):
    """Returns scripted outputs in order, or from a function of the prompt. Records every call,
    so a test can count the calls made (Tests 17 and 43). A scripted ModelResponse is returned as
    it is, so a test can script a refusal, a truncation or empty content."""

    def __init__(self, script: list[Any] | Callable[[str, ModelConfig], Any]):
        self.script = script
        self.calls: list[tuple[str, ModelConfig]] = []

    def invoke(self, prompt, config):
        self.calls.append((prompt, config))
        out = self.script(prompt, config) if callable(self.script) else self.script[len(self.calls) - 1]
        if isinstance(out, Exception):
            raise out
        if isinstance(out, ModelResponse):
            return out
        text = out if isinstance(out, str) else json.dumps(out)
        return ModelResponse(text, f"scripted:{config.model}", len(prompt) // 4 + 1, len(text) // 4 + 1,
                             config.prompt_identity, finish_reason="stop", temperature=config.temperature)


class _Passed:
    """A failure that is not an outage, carried out of the breaker as a value (as F1)."""

    def __init__(self, error: Exception):
        self.error = error


class LiteLLMGateway(ModelGateway):
    """No hidden retries: LiteLLM's own are off, so the only further attempt is the counted
    feedback attempt (task 5, R4). Temperature is sent only where it is configured (R1); the
    response records what was sent."""

    def __init__(self, breaker: CircuitBreaker, completion: Callable[..., Any] | None = None,
                 api_key: str | None = None, provider: str | None = None, temperature: float | None = None,
                 timeout: float = MODEL_REQUEST_TIMEOUT_SECONDS):
        self.breaker = breaker
        self._completion = completion
        self._api_key = api_key
        self._provider = provider   # prefixed to a model id that names none, e.g. anthropic/
        self._temperature = temperature
        self._timeout = timeout

    def _litellm(self):
        if self._completion is None:
            import litellm  # imported on first use, so tests never need it
            self._completion = litellm.completion
        return self._completion

    def invoke(self, prompt, config):
        model = config.model if "/" in config.model or not self._provider else f"{self._provider}/{config.model}"
        kwargs: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}],
                                  "num_retries": 0, "max_retries": 0, "timeout": self._timeout}
        temperature = config.temperature if config.temperature is not None else self._temperature
        if temperature is not None:
            kwargs["temperature"] = temperature
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if config.max_tokens:
            kwargs["max_tokens"] = config.max_tokens
        if config.output_schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": config.prompt_identity.split("@")[0] or "output",
                                                         "schema": config.output_schema}}

        def op():
            try:
                return self._litellm()(**kwargs)
            except Exception as e:
                err = classify(e)
                if getattr(err, "outage", False):   # the breaker counts it
                    if err is e:
                        raise
                    raise err from e
                if err is not e:
                    err.__cause__ = e
                return _Passed(err)
        resp = self.breaker.call(op)
        if isinstance(resp, _Passed):
            raise resp.error
        usage = getattr(resp, "usage", None) or {}
        get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
        choice = resp.choices[0]
        return ModelResponse(choice.message.content, getattr(resp, "model", config.model),
                             get("prompt_tokens"), get("completion_tokens"), config.prompt_identity,
                             get("cache_read_input_tokens"), get("cache_creation_input_tokens"),
                             getattr(choice, "finish_reason", None), temperature)
