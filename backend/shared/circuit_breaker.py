"""
MedAgentPro - circuit breaker (Figure 6, NFR 9, 30_ARCHITECTURE 7.6).

One breaker per dependency, its state shared by every worker through a store
(Redis in deployment). It opens after a configured number of consecutive
failures counted across all workers, fails fast while open, and after the
cooldown lets exactly one probe through. Every transition and failure is logged
to a channel that does not depend on the database (7.6).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Protocol

log = logging.getLogger("medagentpro.breaker")   # stdout handler in deployment, never MongoDB

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

# PROVISIONAL. Table 15: "Consecutive failures before writes are suspended" and
# "Cooldown before recovery is tested". Set from the deployed environment.
FAILURE_THRESHOLD = 5
COOLDOWN_SECONDS = 30.0


class CircuitOpen(Exception):
    """The dependency is treated as down. The caller defers its task rather than blocking."""


class StateStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def incr(self, key: str) -> int: ...
    def set_if_absent(self, key: str, value: str, ttl_seconds: float) -> bool: ...
    def delete(self, key: str) -> None: ...


class MemoryStore:
    """Shared state for tests and a single process. Same semantics as RedisStore."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.data: dict[str, tuple[str, float | None]] = {}
        self.clock = clock

    def _live(self, key):
        v = self.data.get(key)
        if v and v[1] is not None and self.clock() >= v[1]:
            del self.data[key]
            return None
        return v

    def get(self, key):
        v = self._live(key)
        return v[0] if v else None

    def set(self, key, value):
        self.data[key] = (value, None)

    def incr(self, key):
        n = int(self.get(key) or 0) + 1
        self.data[key] = (str(n), None)
        return n

    def set_if_absent(self, key, value, ttl_seconds):
        if self._live(key):
            return False
        self.data[key] = (value, self.clock() + ttl_seconds)
        return True

    def delete(self, key):
        self.data.pop(key, None)


class RedisStore:
    """Deployment store. Redis is already the Celery broker (3.1.3), so nothing new is added."""

    def __init__(self, client: Any):
        self.r = client

    def get(self, key):
        v = self.r.get(key)
        return v.decode() if isinstance(v, bytes) else v

    def set(self, key, value):
        self.r.set(key, value)

    def incr(self, key):
        return int(self.r.incr(key))

    def set_if_absent(self, key, value, ttl_seconds):
        return bool(self.r.set(key, value, nx=True, px=int(ttl_seconds * 1000)))

    def delete(self, key):
        self.r.delete(key)


class CircuitBreaker:
    def __init__(self, dependency: str, store: StateStore,
                 threshold: int = FAILURE_THRESHOLD, cooldown: float = COOLDOWN_SECONDS,
                 clock: Callable[[], float] = time.time):
        self.dependency = dependency
        self.store = store
        self.threshold = threshold
        self.cooldown = cooldown
        self.clock = clock
        k = f"breaker:{dependency}:"
        self.k_state, self.k_fail = k + "state", k + "failures"
        self.k_opened, self.k_probe = k + "opened_at", k + "probe"

    # --- Figure 6 ----------------------------------------------------------

    @property
    def failure_count(self) -> int:
        return int(self.store.get(self.k_fail) or 0)

    def get_state(self) -> str:
        state = self.store.get(self.k_state) or CLOSED
        if state == OPEN and self.clock() - float(self.store.get(self.k_opened) or 0) >= self.cooldown:
            return HALF_OPEN
        return state

    def record_failure(self) -> str:
        n = self.store.incr(self.k_fail)
        log.warning("dependency=%s failure consecutive=%d", self.dependency, n)
        if self.get_state() == HALF_OPEN or n >= self.threshold:
            self._open()
        return self.get_state()

    def record_success(self) -> str:
        was = self.store.get(self.k_state) or CLOSED
        self.store.set(self.k_fail, "0")
        self.store.set(self.k_state, CLOSED)
        self.store.delete(self.k_probe)
        if was != CLOSED:
            log.warning("dependency=%s state %s -> closed", self.dependency, was)
        return CLOSED

    def call(self, operation: Callable[[], Any]) -> Any:
        state = self.get_state()
        if state == OPEN:
            raise CircuitOpen(self.dependency)
        if state == HALF_OPEN and not self.store.set_if_absent(self.k_probe, "1", self.cooldown):
            raise CircuitOpen(self.dependency)   # another worker holds the single probe
        try:
            result = operation()
        except CircuitOpen:
            raise
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    # ------------------------------------------------------------------------

    def _open(self):
        self.store.set(self.k_state, OPEN)
        self.store.set(self.k_opened, str(self.clock()))
        self.store.delete(self.k_probe)
        log.warning("dependency=%s state -> open", self.dependency)
