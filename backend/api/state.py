"""
MedAgentPro - what the chat server holds in memory (task 6).

  demo users      S2: a fixed list, chosen by the X-Demo-User header. Demo only, no authentication;
                  the server listens on 127.0.0.1 only
  conversations   S3: held here, by id and owner. The history the rewriting stage and the identifier
                  check (NFR 27) read never comes from the client, which could forge it
  request ids     R8: one exchange per client request id; a repeated id is refused
  one in flight   one question at a time per conversation, so its history is never read half written.
                  A question for a busy conversation waits for it, up to a bound (T7-B1), then is refused
  cancellation    S1: a gateway wrapper that refuses the next model call once nobody is waiting

Deps is built once per server (N12) and shared by every request. A request gets a shallow copy whose
gateway is wrapped for cancellation, and, on the scripted model, bound to its question.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable

from model_client.client import MODEL_REQUEST_TIMEOUT_SECONDS, ModelConfig, ModelGateway, ModelResponse
from query_assistance import prompts
from query_assistance.pipeline import Deps, ExchangeCancelled, Outcome

# S2: PLANNER sees only the orders whose planner_id is theirs (NFR 17); APPROVER sees everything.
DEMO_USERS = {"PL-01": "PLANNER", "PL-02": "PLANNER", "PL-03": "PLANNER", "PL-04": "PLANNER",
              "PL-05": "PLANNER", "AP-01": "APPROVER"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Cancellable(ModelGateway):
    """Refuses the next model call once the client has gone (S1). Everything else is the inner
    gateway's: the budget's exchange, ensure_path and remaining pass through."""

    def __init__(self, inner: ModelGateway, cancelled: threading.Event):
        self.inner = inner
        self.cancelled = cancelled

    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse:
        if self.cancelled.is_set():
            raise ExchangeCancelled("the client disconnected")
        return self.inner.invoke(prompt, config)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@dataclass
class Conversation:
    id: str
    owner: str
    created_at: str
    items: list[dict] = field(default_factory=list)          # what the screen shows
    history: list[prompts.Exchange] = field(default_factory=list)   # what rewriting and NFR 27 read
    busy: bool = False
    # UI refresh, phase B [demo extension]: how the list shows it. Never the audit record (FR 13, NFR 5)
    title: str | None = None       # a name its user gave; None shows the first question
    pinned: bool = False
    archived: bool = False         # hidden from the list only; still opened by its id, still recorded

    def summary(self) -> dict:
        return {"conversation_id": self.id, "created_at": self.created_at,
                "title": self.title or (self.items[0]["posed"] if self.items else None),
                "exchanges": len(self.items), "pinned": self.pinned, "archived": self.archived}


class ConflictError(Exception):
    pass


BUSY = "a question in this conversation is still being answered"

# T7-B1 (task 7 review, option c): how long a question waits for its conversation to be free. The
# exchange that holds it ends at the latest one model call after its client left (S1), so the
# request timeout of that call, and a margin for the stages around it.
WAIT_FOR_CONVERSATION_SECONDS = MODEL_REQUEST_TIMEOUT_SECONDS + 10


class AppState:
    def __init__(self, deps: Deps, mode: str, gateway_for: Callable[[str], ModelGateway],
                 wait_seconds: float = WAIT_FOR_CONVERSATION_SECONDS, panel: bool = False):
        self.deps = deps
        self.mode = mode                       # scripted | real
        self.panel = panel                     # task 10: "How this was answered" beside each answer (--panel)
        self.gateway_for = gateway_for         # the question's gateway: bound on scripted, shared on real
        self.wait_seconds = wait_seconds
        self._lock = threading.Lock()
        self._freed = threading.Condition(self._lock)   # signalled when a conversation stops being busy
        self._conversations: dict[str, Conversation] = {}
        self._request_ids: set[str] = set()

    # --- users and conversations -------------------------------------------

    @staticmethod
    def user(user_id: str | None) -> dict | None:
        role = DEMO_USERS.get(user_id or "")
        return {"role": role, "user_id": user_id} if role else None

    def new_conversation(self, user: dict) -> Conversation:
        conv = Conversation("CV-" + uuid.uuid4().hex[:12], user["user_id"], _now())
        with self._lock:
            self._conversations[conv.id] = conv
        return conv

    def conversations(self, user: dict) -> list[Conversation]:
        """The user's conversations, archived ones left out (phase B: archiving only hides)."""
        with self._lock:
            return [c for c in self._conversations.values() if c.owner == user["user_id"] and not c.archived]

    def update(self, conversation_id: str, user: dict, changes: dict) -> Conversation:
        """Phase B [demo extension]: title, pinned, archived, by the conversation's own user only. Touches
        nothing of its exchanges, its history or the audit record. LookupError and PermissionError as
        conversation()."""
        conv = self.conversation(conversation_id, user)
        with self._lock:
            for key in ("title", "pinned", "archived"):
                if key in changes:
                    setattr(conv, key, changes[key])
        return conv

    def conversation(self, conversation_id: str, user: dict) -> Conversation:
        """LookupError where there is none, PermissionError where it is someone else's (S3)."""
        with self._lock:
            conv = self._conversations.get(conversation_id)
        if conv is None:
            raise LookupError(conversation_id)
        if conv.owner != user["user_id"]:
            raise PermissionError(conversation_id)
        return conv

    # --- one exchange --------------------------------------------------------

    def claim_request(self, request_id: str):
        """R8: a request id answers once, whether its question runs, waits or is refused."""
        with self._lock:
            if request_id in self._request_ids:
                raise ConflictError("this request id was already sent")
            self._request_ids.add(request_id)

    def take(self, conv: Conversation) -> list[prompts.Exchange] | None:
        """The conversation for this question, and its history as it stands; None if it is busy."""
        with self._lock:
            if conv.busy:
                return None
            conv.busy = True
            return list(conv.history)

    def wait_and_take(self, conv: Conversation, cancelled: threading.Event) -> list[prompts.Exchange] | None:
        """T7-B1: waits for the conversation to be free, then takes it. None if the wait ran out or the
        client left meanwhile. One request, one request id: this is not a retry (R8)."""
        deadline = time.monotonic() + self.wait_seconds
        with self._freed:
            while conv.busy:
                left = deadline - time.monotonic()
                if cancelled.is_set() or left <= 0:
                    return None
                self._freed.wait(min(left, 0.25))
            if cancelled.is_set():
                return None
            conv.busy = True
            return list(conv.history)

    def begin(self, conv: Conversation, request_id: str) -> list[prompts.Exchange]:
        """The request id and the conversation at once, refusing a busy one (no wait)."""
        self.claim_request(request_id)
        history = self.take(conv)
        if history is None:
            raise ConflictError(BUSY)
        return history

    def deps_for(self, question: str, cancelled: threading.Event) -> Deps:
        return replace(self.deps, gateway=Cancellable(self.gateway_for(question), cancelled))

    def finish(self, conv: Conversation, question: str, outcome: Outcome | None, trace: dict | None = None):
        with self._lock:
            if outcome is not None:
                conv.history.append(outcome.exchange)
                item = {"exchange_id": outcome.exchange_id, "posed": question, "recorded": outcome.recorded,
                        "response": outcome.response, "cost": outcome.cost, "at": _now(), "rating": None}
                if trace is not None:
                    item["trace"] = trace
                conv.items.append(item)
            conv.busy = False
            self._freed.notify_all()

    def rated(self, exchange_id: str, user: dict, rating: str):
        """Task 7, Q5: the item shows the rating last given, so the screen shows it after a reload.
        The audit holds every rating (the last one counts); this is only what the screen reads."""
        with self._lock:
            for conv in self._conversations.values():
                if conv.owner != user["user_id"]:
                    continue
                for item in conv.items:
                    if item["exchange_id"] == exchange_id:
                        item["rating"] = rating
