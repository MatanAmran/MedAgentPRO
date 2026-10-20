"""
MedAgentPro - record and replay of model calls (Phase B, task 1; chat 19.10).

A ModelGateway layer for evaluation rounds. In record mode it passes each call to the gateway it wraps
and stores the request and the response under a key; in replay mode it serves the stored response and
wraps no gateway at all, so a replay can never fall through to a real call. A call with no stored
response raises ReplayMiss and is listed in .misses, so a replay that is not complete says so.

The key is a hash of what decides the answer: the prompt's name and version, the model, the messages, the
output schema sent (chat 19.10) and the output cap the call is sent with (chat 20.10, 14:50: an answer cut at
2000 tokens must not be served to a run at 4000). A change to any of them, a new prompt version among them,
is a miss. The cap is the one the budgeted gateway behind the record layer sends; a replay reads it from the
manifest of the run it replays (output_cap).
The store is one JSON file per call under eval_cache/ (gitignored): it holds prompts with record values,
so it never goes into git.

Where it sits: record mode wraps the budgeted gateway, so a recorded call is counted and capped like any
other (task 5); replay mode is the whole gateway, and costs nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, fields
from pathlib import Path

from .client import ModelConfig, ModelGateway, ModelResponse

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "eval_cache"
_RESPONSE_FIELDS = {f.name for f in fields(ModelResponse)}


class ReplayMiss(RuntimeError):
    """Replay found no stored response for a call. Nothing was sent anywhere."""

    def __init__(self, key: str, prompt_identity: str):
        super().__init__(f"no recorded response for {prompt_identity or 'a call'} (key {key[:12]}): "
                         f"record it first; replay never calls a model")
        self.key = key
        self.prompt_identity = prompt_identity


def request_key(prompt: str, config: ModelConfig, max_tokens: int | None = None) -> str:
    """sha256 over the prompt's name and version, the model, the messages, the schema and the output cap, in
    a canonical form. The messages are those the real gateway sends: one user message holding the prompt.
    max_tokens: the cap the call is sent with, where the config does not carry it."""
    body = {"prompt": config.prompt_identity, "model": config.model,
            "messages": [{"role": "user", "content": prompt}], "schema": config.output_schema,
            "max_tokens": max_tokens if max_tokens is not None else config.max_tokens}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class RecordReplayGateway(ModelGateway):
    """mode "record": inner is called and its response stored. mode "replay": inner must be None."""

    def __init__(self, mode: str, store: Path = DEFAULT_STORE, inner: ModelGateway | None = None,
                 output_cap: Callable[[str], int | None] | None = None):
        if mode not in ("record", "replay"):
            raise ValueError(f"mode is record or replay, not {mode!r}")
        if mode == "record" and inner is None:
            raise ValueError("record mode needs the gateway whose calls it records")
        if mode == "replay" and inner is not None:
            raise ValueError("replay mode wraps no gateway: a miss must never reach a model")
        self.mode, self.store, self.inner = mode, Path(store), inner
        self.output_cap = output_cap   # prompt identity -> the cap it was recorded at (replay, from the manifest)
        self.misses: list[ReplayMiss] = []
        self.served = 0
        self.recorded = 0

    def __getattr__(self, name: str):
        """Record mode is the gateway it wraps in every other respect: the budget's exchange(), remaining()
        and ensure_path(), its config and ledger (chat 20.10), so each recorded call is capped as any other.
        Replay mode wraps nothing, so it has none of them, as the scripted model has none."""
        inner = self.__dict__.get("inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)

    def sent_cap(self, config: ModelConfig) -> int | None:
        """The max_tokens the call goes out with: as the budgeted gateway behind sets it (the lower of the
        config's and its cap for the prompt), or as the manifest recorded it on replay."""
        if self.output_cap is not None:
            return self.output_cap(config.prompt_identity)
        budget = getattr(self.__dict__.get("inner"), "config", None)
        if budget is not None and hasattr(budget, "output_cap"):
            cap = budget.output_cap(config.prompt_identity)
            return min(config.max_tokens or cap, cap)
        return config.max_tokens

    def _path(self, key: str) -> Path:
        return self.store / f"{key}.json"

    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse:
        cap = self.sent_cap(config)
        key = request_key(prompt, config, cap)
        if self.mode == "replay":
            path = self._path(key)
            if not path.exists():
                miss = ReplayMiss(key, config.prompt_identity)
                self.misses.append(miss)
                raise miss
            saved = json.loads(path.read_text(encoding="utf-8"))["response"]
            self.served += 1
            return ModelResponse(**{k: v for k, v in saved.items() if k in _RESPONSE_FIELDS})
        if self.inner is None:   # record mode; checked in __init__ already
            raise RuntimeError("record mode has no gateway to call")
        resp = self.inner.invoke(prompt, config)   # a failure is raised as it is, and nothing is stored
        self.store.mkdir(parents=True, exist_ok=True)
        record = {"key": key, "request": {"prompt_identity": config.prompt_identity, "model": config.model,
                                          "schema": config.output_schema, "max_tokens": cap,
                                          "temperature": config.temperature, "prompt": prompt},
                  "response": asdict(resp)}
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path(key))
        self.recorded += 1
        return resp

    def assert_complete(self) -> None:
        """For a replay run: raise if any call was not in the store."""
        if self.misses:
            first = self.misses[0]
            raise ReplayMiss(first.key, f"{len(self.misses)} call(s), the first {first.prompt_identity}")
