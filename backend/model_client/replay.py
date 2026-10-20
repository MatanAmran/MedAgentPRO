"""
MedAgentPro - record and replay of model calls (Phase B, task 1; chat 19.10).

A ModelGateway layer for evaluation rounds. In record mode it passes each call to the gateway it wraps
and stores the request and the response under a key; in replay mode it serves the stored response and
wraps no gateway at all, so a replay can never fall through to a real call. A call with no stored
response raises ReplayMiss and is listed in .misses, so a replay that is not complete says so.

The key is a hash of the whole request as it goes to the provider (chat 20.10, 16:40, R-1): what
client.build_request makes of the prompt and the config the gateway behind sends, with that gateway's own
settings (provider, temperature, timeout, prompt cache). Every parameter is in it, a future one too, the API
key alone aside. A change to any of them, a new prompt version or another output cap among them, is a miss.
A replay builds the request again from the settings and caps the run's manifest recorded.
The store is one directory per recorded run under eval_cache/ (gitignored: it holds prompts with record
values). A run that sends the same request twice keeps both answers, in order, and a replay serves them in
that order: two runs, or two asks within one, never share an answer (chat 20.10: stage's EN2 was served the
answer offscript's G1 got later, from one file per key).

Where it sits: record mode wraps the budgeted gateway, so a recorded call is counted and capped like any
other (task 5); replay mode is the whole gateway, and costs nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, fields, replace
from pathlib import Path

from .client import ModelConfig, ModelGateway, ModelResponse, build_request

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "eval_cache"
_RESPONSE_FIELDS = {f.name for f in fields(ModelResponse)}


class ReplayMiss(RuntimeError):
    """Replay found no stored response for a call. Nothing was sent anywhere."""

    def __init__(self, key: str, prompt_identity: str):
        super().__init__(f"no recorded response for {prompt_identity or 'a call'} (key {key[:12]}): "
                         f"record it first; replay never calls a model")
        self.key = key
        self.prompt_identity = prompt_identity


def request_key(prompt: str, config: ModelConfig, settings: dict | None = None) -> str:
    """sha256 over the request as sent (build_request), in a canonical form, with the prompt's identity, which
    the request names only in its schema's title."""
    body = {"identity": config.prompt_identity, "request": build_request(prompt, config, **(settings or {}))}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class RecordReplayGateway(ModelGateway):
    """mode "record": inner is called and its response stored. mode "replay": inner must be None."""

    def __init__(self, mode: str, store: Path = DEFAULT_STORE, inner: ModelGateway | None = None,
                 output_cap: Callable[[str], int | None] | None = None, settings: dict | None = None,
                 asked: Counter[str] | None = None):
        if mode not in ("record", "replay"):
            raise ValueError(f"mode is record or replay, not {mode!r}")
        if mode == "record" and inner is None:
            raise ValueError("record mode needs the gateway whose calls it records")
        if mode == "replay" and inner is not None:
            raise ValueError("replay mode wraps no gateway: a miss must never reach a model")
        self.mode, self.store, self.inner = mode, Path(store), inner
        self.output_cap = output_cap   # prompt identity -> the cap it was recorded at (replay, from the manifest)
        self.settings = settings       # the gateway's request settings it was recorded with (replay, the manifest)
        # how many times each request was sent in this run: shared by every recorder of one run (asked)
        self._asked: Counter[str] = asked if asked is not None else Counter()
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

    def sent(self, config: ModelConfig) -> tuple[ModelConfig, dict]:
        """The config the call goes out with and the gateway's settings: as the gateway behind makes them
        (the budget's caps, LiteLLM's settings), or as the manifest recorded them on replay."""
        inner = self.__dict__.get("inner")
        if self.mode == "replay":
            cap = self.output_cap(config.prompt_identity) if self.output_cap is not None else config.max_tokens
            return replace(config, max_tokens=cap), dict(self.settings or {})
        prepare = getattr(inner, "sent_config", None)
        settings = getattr(inner, "request_settings", None)
        return (prepare(config) if prepare is not None else config), (settings() if settings is not None else {})

    def sent_cap(self, config: ModelConfig) -> int | None:
        return self.sent(config)[0].max_tokens

    def _path(self, key: str) -> Path:
        n = self._asked[key]
        return self.store / (f"{key}.json" if n <= 1 else f"{key}.{n}.json")

    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse:
        sent, settings = self.sent(config)
        key = request_key(prompt, sent, settings)
        self._asked[key] += 1
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
        record = {"key": key, "prompt_identity": config.prompt_identity,
                  "request": build_request(prompt, sent, **settings), "response": asdict(resp)}
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
