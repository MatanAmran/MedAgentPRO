"""
MedAgentPro - the chat API (task 6; 4.9, Figure 8: synchronous, answered while the person waits).

  POST /api/conversations                         a new conversation
  GET  /api/conversations                         the user's conversations
  GET  /api/conversations/{id}                    its exchanges, as presented
  POST /api/conversations/{id}/exchanges          a question: text/event-stream (S1)
  POST /api/exchanges/{id}/rating                 FR 12
  GET  /api/health                                the mode, and the budget left on the real model
  GET  /api/examples                              the scripted demo questions, in order (task 8, A4)

The stream (S1, decision 8): an event per stage as it starts, then one "result" event carrying the
response after the gate, never model text before it. Cost and budget travel beside the response,
not in it (T3). While the exchange runs, the server checks whether the client is still there; once
it is gone, the next model call is refused and the exchange ends as request_not_completed, recorded.

Every request names its demo user in X-Demo-User (S2). Demo only, no authentication.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import AsyncIterator, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from demo_data.scripted import load_scripts
from query_assistance import pipeline
from shared.circuit_breaker import log as operational_log

from . import trace as panel_trace
from .state import BUSY, AppState, ConflictError, Conversation

# PROVISIONAL. How often the stream checks that the client is still there while it waits for a stage.
DISCONNECT_POLL_SECONDS = 0.25


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")   # R8


class RatingBody(BaseModel):
    rating: str = Field(pattern=r"^(up|down)$")


class ConversationBusy(Exception):
    """T7-B1: the wait for the conversation ran out. Sent as an error event with status 409, since the
    stream (and its 200) began when the wait did."""


class _Claim:
    """Taken once, by whichever comes first (B1)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._taken = False

    def take(self) -> bool:
        with self._lock:
            if self._taken:
                return False
            self._taken = True
            return True


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def exchange_events(work: Callable[[Callable[[str], None]], dict],
                          is_disconnected: Callable[[], "asyncio.Future[bool]"],
                          cancelled: threading.Event) -> AsyncIterator[str]:
    """Runs work(on_stage) on a worker thread and turns what it reports into events. If the client
    leaves before the result, cancelled is set, so the next model call is refused (S1)."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def put(item):
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def run():
        try:
            put(("result", work(lambda name: put(("stage", {"stage": name})))))
        except ConversationBusy:
            put(("error", {"error": BUSY, "status": 409}))
        except Exception as e:   # run() ends every exchange itself; this is a bug in the server
            operational_log.error("exchange worker failed: %s: %s", type(e).__name__, e)
            put(("error", {"error": "The request could not be completed."}))

    loop.run_in_executor(None, run)
    done = False
    try:
        while True:
            try:
                kind, data = await asyncio.wait_for(queue.get(), timeout=DISCONNECT_POLL_SECONDS)
            except asyncio.TimeoutError:
                if await is_disconnected():
                    return
                continue
            yield _sse(kind, data)
            if kind in ("result", "error"):
                done = True
                return
    finally:
        if not done:
            cancelled.set()


def router(state: AppState) -> APIRouter:
    r = APIRouter(prefix="/api")

    def demo_user(x_demo_user: str | None = Header(default=None)) -> dict:
        user = state.user(x_demo_user)
        if user is None:
            raise HTTPException(401, "X-Demo-User must name one of the demo users (demo, no authentication)")
        return user

    def owned(conversation_id: str, user: dict) -> Conversation:
        try:
            return state.conversation(conversation_id, user)
        except LookupError:
            raise HTTPException(404, "no such conversation") from None
        except PermissionError:
            raise HTTPException(403, "this conversation belongs to another user") from None

    @r.get("/health")
    def health():
        out = {"mode": state.mode, "model": state.deps.model, "authentication": "none (demo)", "panel": state.panel}
        if hasattr(state.deps.gateway, "remaining"):
            out["budget_remaining_usd"] = round(state.deps.gateway.remaining(), 6)
        return out

    @r.get("/examples")
    def examples():
        """Task 8, A4: the questions the scripted model answers, in the demo's order, for the empty
        screen. None on the real model, whose answers are not scripted."""
        if state.mode != "scripted":
            return []
        return [{"question": e["question"], "follows": e["expect"].get("follows"),
                 "outcome": e["expect"]["outcome"], "shows": e["expect"]["shows"]}
                for e in load_scripts().values()]

    @r.post("/conversations", status_code=201)
    def new_conversation(user: dict = Depends(demo_user)):
        return {"conversation_id": state.new_conversation(user).id}

    @r.get("/conversations")
    def list_conversations(user: dict = Depends(demo_user)):
        return [{"conversation_id": c.id, "created_at": c.created_at,
                 "title": c.items[0]["posed"] if c.items else None, "exchanges": len(c.items)}
                for c in state.conversations(user)]

    @r.get("/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, user: dict = Depends(demo_user)):
        c = owned(conversation_id, user)
        return {"conversation_id": c.id, "created_at": c.created_at, "exchanges": c.items}

    @r.post("/conversations/{conversation_id}/exchanges")
    async def ask(conversation_id: str, body: AskBody, request: Request, user: dict = Depends(demo_user)):
        conv = owned(conversation_id, user)
        try:
            state.claim_request(body.request_id)
        except ConflictError as e:
            raise HTTPException(409, str(e)) from None
        # T7-B1: a busy conversation is waited for on the worker, with a "waiting" stage meanwhile
        history = state.take(conv)
        held = history is not None
        # B1: the conversation is never left busy. Whichever comes first claims the exchange: the worker,
        # which then runs it and frees the conversation when done, or the response's end, which frees it
        # without running anything. Starlette runs the background task after the body ends, even where the
        # client left before the first event and the generator never started (an unstarted async
        # generator runs no finally)
        claim = _Claim()
        try:
            cancelled = threading.Event()
            deps = state.deps_for(body.question, cancelled)

            def work(on_stage):
                nonlocal history
                if not claim.take():
                    return {}                  # the response already ended: nothing runs, nothing is paid
                if history is None:
                    on_stage("waiting")
                    history = state.wait_and_take(conv, cancelled)
                    if history is None:
                        if cancelled.is_set():
                            return {}          # the client left while waiting: nothing ran
                        raise ConversationBusy()
                outcome = None
                trace = None
                # task 10: the calls' traces, collected by a plain append, which cannot raise into the exchange
                calls: list = []
                try:
                    outcome = pipeline.run(body.question, history, user, deps, on_stage,
                                           context={"request_id": body.request_id, "conversation_id": conv.id},
                                           on_call=calls.append if state.panel else None)
                    result = {"exchange_id": outcome.exchange_id, "recorded": outcome.recorded,
                              "response": outcome.response, "cost": outcome.cost}
                    if state.panel:
                        trace = result["trace"] = panel_trace.build(calls, outcome.response, outcome.cost)
                    return result
                finally:
                    state.finish(conv, body.question, outcome, trace)

            def release_if_unstarted():
                if claim.take():
                    cancelled.set()
                    if held:                   # only the conversation this request took, never another's
                        state.finish(conv, body.question, None)

            return StreamingResponse(exchange_events(work, request.is_disconnected, cancelled),
                                     media_type="text/event-stream",
                                     headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                                     background=BackgroundTask(release_if_unstarted))
        except Exception:
            if claim.take() and held:
                state.finish(conv, body.question, None)
            raise

    @r.post("/exchanges/{exchange_id}/rating")
    def rate(exchange_id: str, body: RatingBody, user: dict = Depends(demo_user)):
        try:
            pipeline.rate(exchange_id, body.rating, user, state.deps.audit)
        except LookupError:
            raise HTTPException(404, "no recorded exchange") from None
        except PermissionError:
            raise HTTPException(403, "only the person who asked rates the answer") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        state.rated(exchange_id, user, body.rating)
        return {"exchange_id": exchange_id, "rating": body.rating}

    return r
