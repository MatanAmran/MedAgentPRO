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
from typing import Any, Callable, Iterator

from shared.circuit_breaker import CircuitBreaker, log

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
    max_tokens: int | None = None             # what was sent, None where nothing was (R9, V02)
    # Of output_tokens, how many the model spent thinking, as the provider reports it (task 10a, chat 22:40).
    # A count only: the thinking itself (reasoning_content, thinking blocks) is never read, kept or shown
    reasoning_tokens: int | None = None
    # What this call cost, set by the gateway that knows the price (task 10): BudgetedGateway from the tokens
    # the provider reported, the scripted demo model 0. None where no one priced it: unknown, never 0
    cost_usd: float | None = None

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
    """Too many exchanges in the last minute (task 5). Refused before any model call. `retry_after` is
    how many whole seconds until the window takes one more (chat, 18.10, 04:45): a number only."""

    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


class ModelCallError(Exception):
    """A call that failed, classified (R4).

    kind              timeout | connection | rate_limited | server_error | client_error | unexpected
    reached_provider  False only where the request is known not to have left (R5, P8)
    outage            counted by the breaker (P9)
    max_tokens        what the call was sent with, where it was sent (U-4): set by the gateway that sent it"""

    def __init__(self, kind: str, message: str, reached_provider: bool = True, outage: bool = False):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.reached_provider = reached_provider
        self.outage = outage
        self.max_tokens: int | None = None


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


def _chain(e: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    cur: BaseException | None = e
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or cur.__context__


def _never_connected(e: BaseException) -> bool:
    """P8, structurally: the connection was refused or never established, so nothing was sent."""
    return any(isinstance(x, (ConnectionRefusedError, socket.gaierror)) or type(x).__name__ == "ConnectError"
               for x in _chain(e))


_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")


def redact(text: str, key: str | None = None) -> str:
    """G3: the configured key, any prefix of it of 8 characters or more, and anything shaped like an
    Anthropic key, masked. For the operational channel only; the audit never holds provider text."""
    if key:
        for n in range(len(key), 7, -1):
            text = text.replace(key[:n], "[redacted]")
    return _KEY_PATTERN.sub("[redacted]", text)


def _structured(e: Exception) -> str:
    """G3: what a failure records in the audit and the exchange document. The exception type and the
    HTTP status only, never the provider's text, which may quote the key and is kept for good (NFR 2)."""
    status = getattr(e, "status_code", None)
    return type(e).__name__ + (f", HTTP {status}" if isinstance(status, int) else "")


def classify(e: Exception) -> Exception:
    """The provider's exception, as BudgetExceeded or ModelCallError. Read from types and status codes.
    The result carries structured fields only (G3)."""
    if isinstance(e, (BudgetExceeded, ModelCallError)):
        return e
    what = _structured(e)
    if is_money_refusal(e):
        return BudgetExceeded(f"provider refused for credit or spend limit ({what})", scope="provider")
    names = {cls.__name__ for cls in type(e).__mro__}
    status = getattr(e, "status_code", None)
    if "UnsupportedParamsError" in names:
        return ModelCallError("client_error", what, reached_provider=False)
    if names & {"Timeout", "APITimeoutError", "TimeoutError", "ReadTimeout"}:
        return ModelCallError("timeout", what, outage=True)
    if names & {"APIConnectionError", "ConnectionError", "ConnectError"}:
        return ModelCallError("connection", what, reached_provider=not _never_connected(e), outage=True)
    if status == 429:
        return ModelCallError("rate_limited", what, outage=True)
    if isinstance(status, int) and status >= 500:
        return ModelCallError("server_error", what, outage=True)
    if isinstance(status, int) and 400 <= status < 500:
        return ModelCallError("client_error", what)
    return ModelCallError("unexpected", what)


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
                             config.prompt_identity, finish_reason="stop", temperature=config.temperature,
                             max_tokens=config.max_tokens or None)


class _Passed:
    """A failure that is not an outage, carried out of the breaker as a value (as F1)."""

    def __init__(self, error: Exception):
        self.error = error


def _reasoning_tokens(details: Any) -> int | None:
    """usage.completion_tokens_details.reasoning_tokens, as LiteLLM 1.105 fills it for Anthropic: the
    provider's thinking count, an estimate where only thinking text came back, None where a thinking
    block came back with no count, 0 where there was none. Only an int is taken (never a bool)."""
    v = details.get("reasoning_tokens") if isinstance(details, dict) else getattr(details, "reasoning_tokens", None)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


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
            # G3: LiteLLM's own logging stays quiet, so a request or an error never reaches a log through it
            litellm.suppress_debug_info = True
            litellm.set_verbose = False
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
                # G3: the provider's own text goes to the operational channel, masked, and nowhere else.
                # The classified error carries structured fields only, and not the original as its cause
                log.warning("model provider error: %s; %s", err, redact(str(e), self._api_key))
                if getattr(err, "outage", False):   # the breaker counts it
                    if err is e:
                        raise
                    raise err from None
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
                             getattr(choice, "finish_reason", None), temperature, kwargs.get("max_tokens"),
                             _reasoning_tokens(get("completion_tokens_details")))
