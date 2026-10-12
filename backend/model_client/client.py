"""
MedAgentPro - model gateway (Figure 6, 3.1.4, 6.1).

ModelGateway is the single interface every model stage calls. LiteLLMGateway
reaches the configured provider through the circuit breaker (NFR 9).
ScriptedModelGateway replaces it in tests, so behavior is tested without a model (6.1).
Every response carries the model and the tokens, which the audit entry records (NFR 3).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from shared.circuit_breaker import CircuitBreaker


@dataclass
class ModelConfig:
    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    output_schema: dict | None = None      # enforced by the provider where it supports it
    prompt_identity: str = ""              # e.g. intent_analysis@1, recorded with the call


@dataclass
class ModelResponse:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    prompt_identity: str = ""
    cache_read_tokens: int | None = None      # reported by the provider; prompt caching is not used (T1)
    cache_write_tokens: int | None = None

    def json(self) -> Any:
        return json.loads(self.text)


class ModelGateway(ABC):
    @abstractmethod
    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse: ...


class BudgetExceeded(Exception):
    """The call was not sent, or the provider refused it for money (task 5). Not retried,
    and not an outage: the circuit breaker does not count it."""


class RateLimited(Exception):
    """Too many exchanges in the last minute (task 5). Refused before any model call."""


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


class ScriptedModelGateway(ModelGateway):
    """Returns scripted outputs in order, or from a function of the prompt. Records every call,
    so a test can count the calls made (Tests 17 and 43)."""

    def __init__(self, script: list[Any] | Callable[[str, ModelConfig], Any]):
        self.script = script
        self.calls: list[tuple[str, ModelConfig]] = []

    def invoke(self, prompt, config):
        self.calls.append((prompt, config))
        out = self.script(prompt, config) if callable(self.script) else self.script[len(self.calls) - 1]
        if isinstance(out, Exception):
            raise out
        text = out if isinstance(out, str) else json.dumps(out)
        return ModelResponse(text, f"scripted:{config.model}", len(prompt) // 4 + 1, len(text) // 4 + 1,
                             config.prompt_identity)


class _Refused:
    def __init__(self, error: Exception):
        self.error = error


class LiteLLMGateway(ModelGateway):
    """No hidden retries: LiteLLM's own are off, so the only retries are MODEL_CALL_ATTEMPTS,
    which are counted (task 5). A refusal for money leaves the breaker as a value, so it is not
    counted as an outage, and becomes BudgetExceeded."""

    def __init__(self, breaker: CircuitBreaker, completion: Callable[..., Any] | None = None,
                 api_key: str | None = None, provider: str | None = None):
        self.breaker = breaker
        self._completion = completion
        self._api_key = api_key
        self._provider = provider   # prefixed to a model id that names none, e.g. anthropic/

    def _litellm(self):
        if self._completion is None:
            import litellm  # imported on first use, so tests never need it
            self._completion = litellm.completion
        return self._completion

    def invoke(self, prompt, config):
        model = config.model if "/" in config.model or not self._provider else f"{self._provider}/{config.model}"
        kwargs: dict[str, Any] = {"model": model, "temperature": config.temperature,
                                  "messages": [{"role": "user", "content": prompt}],
                                  "num_retries": 0, "max_retries": 0}
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
                if is_money_refusal(e):
                    return _Refused(e)
                raise
        resp = self.breaker.call(op)
        if isinstance(resp, _Refused):
            raise BudgetExceeded(f"provider refused for credit or spend limit: {resp.error}") from resp.error
        usage = getattr(resp, "usage", None) or {}
        get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
        return ModelResponse(resp.choices[0].message.content, getattr(resp, "model", config.model),
                             get("prompt_tokens"), get("completion_tokens"), config.prompt_identity,
                             get("cache_read_input_tokens"), get("cache_creation_input_tokens"))
