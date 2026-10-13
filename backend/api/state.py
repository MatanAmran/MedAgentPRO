"""
MedAgentPro - what the chat server holds in memory (task 6).

  demo users      S2: a fixed list, chosen by the X-Demo-User header. Demo only, no authentication;
                  the server listens on 127.0.0.1 only
  conversations   S3: held here, by id and owner. The history the rewriting stage and the identifier
                  check (NFR 27) read never comes from the client, which could forge it
  request ids     R8: one exchange per client request id; a repeated id is refused
  one in flight   one question at a time per conversation, so its history is never read half written
  cancellation    S1: a gateway wrapper that refuses the next model call once nobody is waiting

Deps is built once per server (N12) and shared by every request. A request gets a shallow copy whose
gateway is wrapped for cancellation, and, on the scripted model, bound to its question.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable

from model_client.client import ModelConfig, ModelGateway, ModelResponse
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


class ConflictError(Exception):
    pass


class AppState:
    def __init__(self, deps: Deps, mode: str, gateway_for: Callable[[str], ModelGateway]):
        self.deps = deps
        self.mode = mode                       # scripted | real
        self.gateway_for = gateway_for         # the question's gateway: bound on scripted, shared on real
        self._lock = threading.Lock()
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
        with self._lock:
            return [c for c in self._conversations.values() if c.owner == user["user_id"]]

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

    def begin(self, conv: Conversation, request_id: str) -> list[prompts.Exchange]:
        """R8: a request id answers once. One question at a time per conversation. Returns the history
        as it stands, which the exchange reads."""
        with self._lock:
            if request_id in self._request_ids:
                raise ConflictError("this request id was already sent")
            if conv.busy:
                raise ConflictError("a question in this conversation is still being answered")
            self._request_ids.add(request_id)
            conv.busy = True
            return list(conv.history)

    def deps_for(self, question: str, cancelled: threading.Event) -> Deps:
        return replace(self.deps, gateway=Cancellable(self.gateway_for(question), cancelled))

    def finish(self, conv: Conversation, question: str, outcome: Outcome | None):
        with self._lock:
            if outcome is not None:
                conv.history.append(outcome.exchange)
                conv.items.append({"exchange_id": outcome.exchange_id, "posed": question,
                                   "recorded": outcome.recorded, "response": outcome.response,
                                   "cost": outcome.cost, "at": _now()})
            conv.busy = False
