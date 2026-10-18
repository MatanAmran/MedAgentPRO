"""
MedAgentPro - the spending cap on the real model (task 5 requirements in CLAUDE.md,
docs/notes/00_STATUS.md "משימה 5 — תקציב").

BudgetedGateway wraps any ModelGateway, so no stage changes. Before every call it reserves the
worst case (estimated input at the input price, plus the full output cap at the output price)
and refuses the call, unsent, where that would cross the total cap or the exchange's own cap.
After the call it releases the reservation and records what the provider reports. The check,
the reservation and the record happen under one lock, so two concurrent calls never pass on the
same remaining amount (T2). Spend is kept in a file, so a restart does not reset it, and a lock
held on that file for the life of the process refuses a second process (T2).

Every value that bears on money comes from backend/.env. Nothing has a default: a missing value
refuses to start, so nothing is spent on a guess. The values never appear in code, tests or logs.
Prompt caching is not used (T1). Cached tokens a provider reports anyway are counted at the
cache write price, or at twice the input price, never less.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence, TypedDict, cast

from shared.circuit_breaker import CircuitBreaker, CircuitOpen, MemoryStore

from .client import (
    BudgetExceeded,
    LiteLLMGateway,
    ModelCallError,
    ModelConfig,
    ModelGateway,
    ModelResponse,
    RateLimited,
)

BACKEND = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND / ".env"
SPEND_FILE = BACKEND / ".model_spend.json"

REQUIRED = ("ANTHROPIC_API_KEY", "MODEL_ID", "MODEL_PRICE_IN", "MODEL_PRICE_OUT", "MODEL_BUDGET_USD",
            "MODEL_MAX_USD_PER_EXCHANGE", "MODEL_MAX_OUTPUT_TOKENS", "MODEL_MAX_EXCHANGES_PER_MINUTE")
OPTIONAL = ("MODEL_PRICE_CACHE_READ", "MODEL_PRICE_CACHE_WRITE", "MODEL_TEMPERATURE", "MODEL_MAX_OUTPUT_TOKENS_INTENT",
            "MODEL_MAX_OUTPUT_TOKENS_GATE")
# The stage MODEL_MAX_OUTPUT_TOKENS_INTENT applies to, by the prompt identity's name
INTENT_PROMPTS = ("intent_analysis", "intent_analysis_demo")
# The stage MODEL_MAX_OUTPUT_TOKENS_GATE applies to: the support gate, first and second
GATE_PROMPTS = ("support_scoring",)


class BudgetConfigError(Exception):
    """A required value is missing or invalid. Names the variable, never its value."""


@dataclass(frozen=True)
class BudgetConfig:
    model_id: str
    price_in: float                  # USD per million tokens
    price_out: float
    budget_usd: float                # total cap, set below the remaining credit
    max_usd_per_exchange: float
    max_output_tokens: int
    max_exchanges_per_minute: int
    price_cache_read: float | None = None
    price_cache_write: float | None = None
    api_key: str = field(default="", repr=False)
    temperature: float | None = None   # sent only where set (R1)
    # Intent analysis alone, where set (task 10a, chat 23:35): it thinks the longest (question 3 used 90%
    # of 2000, 74% of it thinking). A higher cap only gives room; the effort is not changed
    max_output_tokens_intent: int | None = None
    # The gate alone, where set (chat, 18.10, 13:50): a long answer over two readings makes many claims, and
    # D4 of the held-out set stopped at 2000 (723 of them thinking). The answer's length is not limited
    max_output_tokens_gate: int | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, env_file: Path = ENV_FILE,
                 need_key: bool = True) -> "BudgetConfig":
        """backend/.env wins over the process environment, so the demo key is the one used.
        need_key=False is for the dry run, which prices calls and makes none."""
        if env is None:
            from dotenv import dotenv_values
            env = {**os.environ, **{k: v for k, v in dotenv_values(env_file).items() if v is not None}}
        missing = [k for k in REQUIRED if not str(env.get(k) or "").strip()
                   and (need_key or k != "ANTHROPIC_API_KEY")]
        if missing:
            raise BudgetConfigError(f"the real model does not start: missing in backend/.env: {', '.join(missing)}")

        def number(name: str, kind=float, required=True):
            raw = str(env.get(name) or "").strip()
            if not raw and not required:
                return None
            try:
                v = kind(raw)
            except ValueError:
                raise BudgetConfigError(f"{name} in backend/.env is not a {kind.__name__}") from None
            if not v > 0 or (kind is float and not math.isfinite(v)):
                raise BudgetConfigError(f"{name} in backend/.env must be above zero")
            return v

        raw_t = str(env.get("MODEL_TEMPERATURE") or "").strip()
        try:
            temperature = float(raw_t) if raw_t else None
        except ValueError:
            raise BudgetConfigError("MODEL_TEMPERATURE in backend/.env is not a float") from None
        if temperature is not None and not 0 <= temperature <= 1:
            raise BudgetConfigError("MODEL_TEMPERATURE in backend/.env must be between 0 and 1")

        return cls(env["MODEL_ID"].strip(), number("MODEL_PRICE_IN"), number("MODEL_PRICE_OUT"),
                   number("MODEL_BUDGET_USD"), number("MODEL_MAX_USD_PER_EXCHANGE"),
                   number("MODEL_MAX_OUTPUT_TOKENS", int), number("MODEL_MAX_EXCHANGES_PER_MINUTE", int),
                   number("MODEL_PRICE_CACHE_READ", required=False),
                   number("MODEL_PRICE_CACHE_WRITE", required=False),
                   str(env.get("ANTHROPIC_API_KEY") or "").strip(), temperature,
                   number("MODEL_MAX_OUTPUT_TOKENS_INTENT", int, required=False),
                   number("MODEL_MAX_OUTPUT_TOKENS_GATE", int, required=False))

    def output_cap(self, prompt_identity: str = "") -> int:
        """The max_tokens a call of this prompt is sent: the intent cap for intent analysis and the gate
        cap for the gate, each where set, else the general one."""
        name = prompt_identity.split("@")[0]
        if self.max_output_tokens_intent and name in INTENT_PROMPTS:
            return self.max_output_tokens_intent
        if self.max_output_tokens_gate and name in GATE_PROMPTS:
            return self.max_output_tokens_gate
        return self.max_output_tokens

    def cost(self, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
        cached = self.price_cache_write or 2 * self.price_in
        return (input_tokens * self.price_in + output_tokens * self.price_out
                + (cache_read + cache_write) * cached) / 1_000_000


def estimate_input_tokens(prompt: str) -> int:
    """Deliberately high: UTF-8 bytes over two. English runs near four characters a token, and
    Hebrew takes two bytes a character, so this never undercounts either (a proposal)."""
    return math.ceil(len(prompt.encode("utf-8")) / 2)


# --------------------------------------------------------------------------
# what was spent, kept across restarts
# --------------------------------------------------------------------------

class _ProcessLock:
    """Held on a file for the life of the process. The OS releases it when the process ends, so a
    stale lock never blocks. A second holder, in this process or another, is refused (T2)."""

    def __init__(self, path: Path):
        self.path = path
        self._f = open(path, "a+")
        try:
            self._lock()
        except OSError:
            self._f.close()
            raise BudgetConfigError(f"another process is using {path.name}. The spending cap holds only "
                                    f"with one server process") from None

    def _lock(self):
        self._f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self._f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self):
        if self._f.closed:
            return
        try:
            self._f.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
        finally:
            self._f.close()


class _Spend(TypedDict):
    total_usd: float
    by_day: dict[str, float]
    by_model: dict[str, float]


def _amount(value: object) -> float | None:
    """A recorded amount: a finite, non-negative int or float, never a bool. Else None. A NaN total
    would pass every cap comparison, and a negative one would enlarge the cap."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _checked_spend(path: Path) -> _Spend:
    """The spend file, checked when it is loaded, so a damaged file fails before any money is spent,
    never after a paid call (chat review, 14.10, 14:15). Names the file and the field, never a value."""
    bad = f"{path.name} is damaged"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BudgetConfigError(f"{bad}: not valid JSON") from None
    if not isinstance(raw, dict):
        raise BudgetConfigError(f"{bad}: not a JSON object")
    total = _amount(raw.get("total_usd", 0.0))
    if total is None:
        raise BudgetConfigError(f"{bad}: total_usd is not an amount")
    parts: dict[str, dict[str, float]] = {}
    for key in ("by_day", "by_model"):
        given = raw.get(key, {})
        if not isinstance(given, dict):
            raise BudgetConfigError(f"{bad}: {key} is not a JSON object")
        amounts = {name: _amount(v) for name, v in given.items()}
        if any(v is None for v in amounts.values()):
            raise BudgetConfigError(f"{bad}: a value in {key} is not an amount")
        parts[key] = {name: v for name, v in amounts.items() if v is not None}
    return cast(_Spend, {**raw, "total_usd": total, **parts})


class SpendLedger:
    """Total, per day and per model, in USD. Written whole to a temporary file and swapped in,
    so a crash mid-write leaves the previous record."""

    def __init__(self, path: Path = SPEND_FILE):
        self.path = Path(path)
        self._lock = _ProcessLock(self.path.with_suffix(".lock"))
        self.data: _Spend = {"total_usd": 0.0, "by_day": {}, "by_model": {}}
        if self.path.exists():
            try:
                self.data = _checked_spend(self.path)
            except BudgetConfigError:
                self._lock.release()
                raise

    @property
    def total(self) -> float:
        return self.data["total_usd"]

    def add(self, model: str, usd: float):
        d = self.data
        day = date.today().isoformat()
        d["total_usd"] = d["total_usd"] + usd
        d["by_day"][day] = d["by_day"].get(day, 0.0) + usd
        d["by_model"][model] = d["by_model"].get(model, 0.0) + usd
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def close(self):
        self._lock.release()


# --------------------------------------------------------------------------
# the gateway
# --------------------------------------------------------------------------

@dataclass
class ExchangeSpend:
    spent: float = 0.0
    reserved: float = 0.0


class BudgetedGateway(ModelGateway):
    def __init__(self, inner: ModelGateway, config: BudgetConfig, ledger: SpendLedger,
                 clock: Callable[[], float] = time.monotonic):
        self.inner = inner
        self.config = config
        self.ledger = ledger
        self.clock = clock
        self._lock = threading.Lock()
        self._reserved = 0.0
        self._starts: deque[float] = deque()
        self._current: ContextVar[ExchangeSpend | None] = ContextVar("exchange", default=None)

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self.config.budget_usd - self.ledger.total)

    @contextmanager
    def exchange(self) -> Iterator[ExchangeSpend]:
        """One exchange. Refused before any model call beyond the rate across all users, so a
        loop in the interface cannot burn the budget. Its own spend is kept for its cap."""
        with self._lock:
            now = self.clock()
            while self._starts and now - self._starts[0] >= 60:
                self._starts.popleft()
            if len(self._starts) >= self.config.max_exchanges_per_minute:
                # the window takes one more once its oldest start is 60 seconds old
                wait = max(1, math.ceil(60 - (now - self._starts[0])))
                raise RateLimited("too many exchanges in the last minute", retry_after=wait)
            self._starts.append(now)
        spend = ExchangeSpend()
        token = self._current.set(spend)
        try:
            yield spend
        finally:
            self._current.reset(token)

    def worst_case(self, input_tokens: int, prompt_identity: str = "") -> float:
        return self.config.cost(input_tokens, self.config.output_cap(prompt_identity))

    def _refuse_if_over(self, amount: float, ex: ExchangeSpend | None):
        """Under the lock. The total cap first: it is the one that ends every exchange."""
        cfg = self.config
        if self.ledger.total + self._reserved + amount > cfg.budget_usd:
            raise BudgetExceeded("the demo budget is used up", scope="total")
        if ex is not None and ex.spent + ex.reserved + amount > cfg.max_usd_per_exchange:
            raise BudgetExceeded("this exchange would cross its own cap", scope="exchange")

    def ensure_path(self, input_tokens: list[int], prompt_names: Sequence[str] = ()):
        """Review R7a: before the first synthesis call, the worst case of the whole remaining path
        (synthesis, gate, redraft, second gate, no feedback attempts) must fit. Nothing is reserved:
        each call still reserves its own worst case when it is made. prompt_names, one per input, price
        each call at its own output cap (the gate's, chat 13:50); without them, at the general one."""
        names = list(prompt_names) or [""] * len(input_tokens)
        with self._lock:
            self._refuse_if_over(sum(self.worst_case(n, p) for n, p in zip(input_tokens, names, strict=True)),
                                 self._current.get())

    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse:
        cfg = self.config
        if config.model != cfg.model_id:
            raise BudgetExceeded(f"model {config.model} is not the one priced in backend/.env", scope="model")
        cap = cfg.output_cap(config.prompt_identity)
        call = replace(config, max_tokens=min(config.max_tokens or cap, cap))
        worst = cfg.cost(estimate_input_tokens(prompt), call.max_tokens)
        ex = self._current.get()
        with self._lock:
            self._refuse_if_over(worst, ex)
            self._reserved += worst
            if ex is not None:
                ex.reserved += worst

        actual = worst   # where the outcome is unknown, the worst case is what is recorded
        try:
            resp = self.inner.invoke(prompt, call)
            if resp.input_tokens is not None and resp.output_tokens is not None:
                actual = cfg.cost(resp.input_tokens, resp.output_tokens,
                                  resp.cache_read_tokens or 0, resp.cache_write_tokens or 0)
            return resp
        except (CircuitOpen, BudgetExceeded):
            actual = 0.0   # nothing was sent, or the provider refused before charging
            raise
        except ModelCallError as e:
            if not e.reached_provider:   # R5: known not to have left, so released
                actual = 0.0
            raise                        # otherwise, as on a timeout, the worst case stays charged
        finally:
            with self._lock:
                self._reserved -= worst
                if ex is not None:
                    ex.reserved -= worst
                    ex.spent += actual
                if actual:
                    self.ledger.add(cfg.model_id, actual)


def real_gateway(env: Mapping[str, str] | None = None, spend_file: Path = SPEND_FILE,
                 breaker: CircuitBreaker | None = None) -> BudgetedGateway:
    """The real model behind the spending cap, from backend/.env. Refuses to start where a value is
    missing. Create it once per server process (N12, T2): it holds the spend lock and the breaker.
    Refuses under pytest (R8): tests never reach a real model (6.1)."""
    if "pytest" in sys.modules:
        raise BudgetConfigError("the real model is never built under pytest (book 6.1, R8)")
    cfg = BudgetConfig.from_env(env)
    check_against_provider_map(cfg)
    inner = LiteLLMGateway(breaker or CircuitBreaker("model_provider", MemoryStore()),
                           api_key=cfg.api_key, provider="anthropic", temperature=cfg.temperature)
    return BudgetedGateway(inner, cfg, SpendLedger(spend_file))


def map_entry(name: str) -> dict:
    """The raw entry of LiteLLM's local map (H1). get_model_info is not used: for an Anthropic model the
    map does not hold it builds an entry priced at zero, and it does not pass supports_sampling_params
    on (False on the Claude 5 models in the raw entry). Raises KeyError where the map holds none."""
    import litellm
    for key in (name, name.split("/", 1)[-1]):
        if key in litellm.model_cost:
            return dict(litellm.model_cost[key])
    raise KeyError(name)


def map_prices(model_id: str) -> tuple[float, float]:
    """USD per million tokens, input and output, from the local map. For the dry run without .env."""
    info = map_entry(model_id)
    if not info.get("input_cost_per_token") or not info.get("output_cost_per_token"):
        raise KeyError(f"{model_id} has no price in the map")
    return info["input_cost_per_token"] * 1_000_000, info["output_cost_per_token"] * 1_000_000


def check_against_provider_map(cfg: BudgetConfig, model_info: Callable[[str], dict] | None = None):
    """P1: refuse to start where the model is not in LiteLLM's map (Haiku 5.5 in the local map of the
    pinned 1.105, R1, H1), where the map holds no price for it, where a temperature is set for a model
    that takes none, or where a price in .env is lower than the map's (a typo of 0.2 for 2 would
    undercount tenfold). A higher price is allowed. Nor where an output cap, the general one or intent's,
    is above the model's own output limit."""
    model_info = model_info or map_entry
    name = cfg.model_id if "/" in cfg.model_id else f"anthropic/{cfg.model_id}"
    try:
        info = model_info(name)
    except Exception:
        raise BudgetConfigError("MODEL_ID is not in LiteLLM's model map, so its prices and parameters "
                                "cannot be checked") from None
    if cfg.temperature is not None and info.get("supports_sampling_params") is False:
        raise BudgetConfigError("MODEL_TEMPERATURE is set, and this model takes no temperature. Remove it")
    for var, ours, key in (("MODEL_PRICE_IN", cfg.price_in, "input_cost_per_token"),
                           ("MODEL_PRICE_OUT", cfg.price_out, "output_cost_per_token")):
        listed = info.get(key)
        if not listed:   # H1: no price (or zero) in the map is not checked past, it is refused
            raise BudgetConfigError(f"LiteLLM's model map holds no price for this model, so {var} cannot be checked")
        if ours < listed * 1_000_000 - 1e-9:
            raise BudgetConfigError(f"{var} in backend/.env is lower than LiteLLM's price for this model")
    limit = info.get("max_output_tokens")
    for var, cap in (("MODEL_MAX_OUTPUT_TOKENS", cfg.max_output_tokens),
                     ("MODEL_MAX_OUTPUT_TOKENS_INTENT", cfg.max_output_tokens_intent),
                     ("MODEL_MAX_OUTPUT_TOKENS_GATE", cfg.max_output_tokens_gate)):
        if limit and cap and cap > limit:
            raise BudgetConfigError(f"{var} in backend/.env is above this model's own output limit")
