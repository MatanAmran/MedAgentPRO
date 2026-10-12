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

    def json(self) -> Any:
        return json.loads(self.text)


class ModelGateway(ABC):
    @abstractmethod
    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse: ...


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


class LiteLLMGateway(ModelGateway):
    def __init__(self, breaker: CircuitBreaker, completion: Callable[..., Any] | None = None):
        self.breaker = breaker
        self._completion = completion

    def _litellm(self):
        if self._completion is None:
            import litellm  # imported on first use, so tests never need it
            self._completion = litellm.completion
        return self._completion

    def invoke(self, prompt, config):
        kwargs: dict[str, Any] = {"model": config.model, "temperature": config.temperature,
                                  "messages": [{"role": "user", "content": prompt}]}
        if config.max_tokens:
            kwargs["max_tokens"] = config.max_tokens
        if config.output_schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": config.prompt_identity.split("@")[0] or "output",
                                                         "schema": config.output_schema}}
        resp = self.breaker.call(lambda: self._litellm()(**kwargs))
        usage = getattr(resp, "usage", None) or {}
        get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
        return ModelResponse(resp.choices[0].message.content, getattr(resp, "model", config.model),
                             get("prompt_tokens"), get("completion_tokens"), config.prompt_identity)
