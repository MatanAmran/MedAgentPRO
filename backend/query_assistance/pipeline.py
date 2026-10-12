"""
MedAgentPro - query assistance pipeline. Orchestration of one exchange (4.9, Figure 10).

  rewriting          model, only where the conversation has earlier exchanges (FR 25)
  intent analysis    model, schema 1. A plan that fails compilation is a rejected output (Test 13)
  compilation        code: entitlement, LIMIT, identifier provenance (NFR 17, NFR 27)
  retrieval          code, every reading at once (FR 16), through the source's circuit breaker
                     (NFR 9). A source that cannot be reached ends the exchange, which is not
                     recorded (FR 15, Test 23). Any other database error is a system fault:
                     request_not_completed, recorded (review F1 and F9 of 12.10)
  synthesis          model, schema 5 (demo), over the readings that returned rows
  gate               model, schema 4, one call over the whole answer (Test 17)
  regeneration       at most once (NFR 19), then the gate once more
  output contract    code (contract.py)

Every model call is retried within MODEL_CALL_ATTEMPTS. Output rejected in every attempt ends
the exchange as request_not_completed. Each call, rejected ones included, is an audit entry
(NFR 3), and a stage with no model is one entry without tokens. The entries and the exchange
document are held in memory and written in one transaction when the exchange ends (NFR 5).
If that write fails, the answer is not presented (FR 13).

The decisions of 12.10 this follows are listed in CLAUDE.md, "Task 4 decisions".
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from jsonschema import Draft202012Validator

from model_client.budget import estimate_input_tokens
from model_client.client import BudgetExceeded, ModelGateway, RateLimited, redact
from persistence.audit_repository import AuditEntry, AuditRepository
from shared.circuit_breaker import CircuitBreaker, CircuitOpen, MemoryStore
from shared.circuit_breaker import log as operational_log

from . import calls as model_calls
from . import contract, gate, prompts, synthesis
from .calls import MODEL_CALL_ATTEMPTS, Call, StageFailed, StageRejected
from .compiler import HERE, Catalog, CompileError, CompiledReading, RetrievalResult, _norm, compile_intent, \
    execute, intent_validator
from .synthesis import Draft

_BUNDLE = json.loads((HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))["schemas"]
_REWRITE_V = Draft202012Validator(_BUNDLE["question_rewriting"]["schema"])
_GATE_V = Draft202012Validator(_BUNDLE["support_scoring"]["schema"])
_SYNTHESIS_SCHEMA = _BUNDLE["synthesis"]["schema"]

# The order stages are declared in, which the audit trail follows (Test 21).
REWRITING, INTENT, COMPILATION, RETRIEVAL, SYNTHESIS, GATE, REGENERATION, REGATE, CONTRACT, RATING = range(1, 11)
STAGE_NAMES = {REWRITING: "question_rewriting", INTENT: "intent_analysis", COMPILATION: "compilation",
               RETRIEVAL: "retrieval", SYNTHESIS: "synthesis", GATE: "support_gate",
               REGENERATION: "regeneration", REGATE: "support_gate", CONTRACT: "output_contract",
               RATING: "rating"}

# SQLite primary result codes that mean the source cannot be reached, not that the query is wrong
# (review F1): busy, locked, cannot open. A failed connection counts as well.
UNREACHABLE_CODES = {5: "SQLITE_BUSY", 6: "SQLITE_LOCKED", 14: "SQLITE_CANTOPEN"}


def connect_readonly(path, timeout: float = 5.0) -> Callable[[], sqlite3.Connection]:
    """The chat only reads the company store. Read-only mode also makes a missing file a failure
    to open, where a plain connect() would create an empty database."""
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    return lambda: sqlite3.connect(uri, uri=True, timeout=timeout)


@dataclass
class Deps:
    gateway: ModelGateway
    model: str
    catalog: Catalog
    connect: Callable[[], sqlite3.Connection]   # a new connection per call, one per retrieval thread
    audit: AuditRepository
    intent_prompt: prompts.Prompt | None = None   # defaults to templates/intent_analysis.md
    source_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("company_store", MemoryStore()))
    attempts: int = MODEL_CALL_ATTEMPTS
    source: str = "The company database"


@dataclass
class Outcome:
    response: dict                       # exchange_response, as presented
    exchange: prompts.Exchange           # what the next question's rewriting and NFR 27 check see
    exchange_id: str
    recorded: bool
    cost: dict | None = None             # exchange_usd, remaining_usd. Outside exchange_response (T3)


class _Rejected(Exception):
    """A model stage ended without a usable output: content rejected in every attempt, or a failure
    with no further attempt (R4). Its calls are already recorded."""


class ExchangeCancelled(Exception):
    """Nobody is waiting for the answer any more (task 6, S1): raised before the next model call, so an
    exchange that no one will read stops paying. It ends as request_not_completed, recorded."""


class _Unreachable(Exception):
    pass


class _SystemFault(Exception):
    """A database error that is not a failure to reach the source, such as a malformed query."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# the record of one exchange, held in memory until it ends
# --------------------------------------------------------------------------

class _Record:
    def __init__(self, exchange_id: str, user: dict):
        self.exchange_id = exchange_id
        self.user = user
        self.entries: list[AuditEntry] = []

    def _entry(self, ordinal: int, event_type: str, **kw) -> AuditEntry:
        e = AuditEntry(self.exchange_id, ordinal, STAGE_NAMES[ordinal], event_type, self.user["role"],
                       actor_id=self.user.get("user_id"), **kw)
        self.entries.append(e)
        return e

    def step(self, ordinal: int, evidence: list | None = None):
        """A stage with no model call: one entry, without tokens (decision 11)."""
        self._entry(ordinal, "stage_completed", evidence_reviewed=evidence)

    def calls(self, ordinal: int, made: list[Call], model: str):
        """One entry per model call: accepted, rejected, or failed with no response. A repeated
        attempt states why it supersedes the earlier one, and whether it carried feedback (NFR 3, P6).
        The temperature recorded is what was sent, or the provider's default where none was (R1)."""
        for n, c in enumerate(made, 1):
            r = c.response
            sent = "provider default" if r is None or r.temperature is None else r.temperature
            event = ("model_call_failed" if r is None else
                     "model_output_rejected" if c.rejection else "model_output_accepted")
            self._entry(ordinal, event, attempt_number=n,
                        superseded_reason=f"attempt {n - 1} rejected: {made[n - 2].rejection}" if n > 1 else None,
                        model_identity=f"{r.model if r else model} · {c.prompt_identity} · temperature {sent}",
                        input_tokens=r.input_tokens if r else None, output_tokens=r.output_tokens if r else None,
                        feedback_attempt=c.feedback_attempt, rejection_reason=c.rejection)


# --------------------------------------------------------------------------
# model stages
# --------------------------------------------------------------------------

def _recorded(rec: _Record, ordinal: int, deps: Deps, run: Callable[[], tuple[Any, list[Call]]]) -> Any:
    """Runs one stage's calls and records every one of them, whatever the result."""
    try:
        result, made = run()
    except (StageRejected, StageFailed) as e:
        rec.calls(ordinal, e.calls, deps.model)
        raise _Rejected(f"{STAGE_NAMES[ordinal]}: {e}") from e
    except Exception as e:
        rec.calls(ordinal, getattr(e, "calls_made", []), deps.model)
        raise
    rec.calls(ordinal, made, deps.model)
    return result


def _ask(deps: Deps, rec: _Record, ordinal: int, prompt: prompts.Prompt, render: Callable[[str | None], str],
         accept: Callable[[Any], Any], output_schema: dict | None = None,
         validator: Draft202012Validator | None = None, rejected: tuple = ()) -> Any:
    return _recorded(rec, ordinal, deps, lambda: model_calls.ask(
        deps.gateway, deps.model, prompt.identity, render, accept, deps.attempts, output_schema, validator, rejected))


def _synthesize(deps: Deps, rec: _Record, ordinal: int, call: Callable[[], Draft]) -> Draft:
    def run():
        d = call()
        return d, d.calls
    return _recorded(rec, ordinal, deps, run)


def _gate(deps: Deps, rec: _Record, ordinal: int, d: Draft) -> gate.GateResult:
    """Structured output on (R2). The full schema and gate.evaluate run in code."""
    sentences = d.filled()
    records = [(label, m.rows) for label, m in d.materials.items() if m.rows]
    aliases = {label: set(m.columns) for label, m in d.materials.items()}
    answer = gate.render_answer(sentences)
    return _ask(deps, rec, ordinal, prompts.load("gate.md"),
                lambda fb: prompts.render_gate(answer, records, feedback=fb),
                lambda out: gate.evaluate(out, sentences, aliases), _GATE_V.schema, _GATE_V)


def _path_inputs(question: str, rewritten: str | None, materials: list) -> list[int]:
    """R7a: the input of every call left on the path, synthesis to the second gate, before any of it
    is made. The answer is taken at its largest, from the synthesis schema's bounds."""
    s = _SYNTHESIS_SCHEMA
    reading = s["properties"]["readings"]
    sentence = reading["items"]["properties"]["sentences"]
    longest = sentence["items"]["properties"]["text"].get("maxLength", 400)
    n = reading.get("maxItems", 3) * sentence.get("maxItems", 6)
    answer = gate.render_answer([("label", "x" * longest)] * n)
    records = [(m.label, m.rows) for m in materials if m.rows]
    syn = estimate_input_tokens(synthesis.render(question, materials, rewritten=rewritten))
    gate_in = estimate_input_tokens(prompts.render_gate(answer, records))
    redraft_in = estimate_input_tokens(synthesis.render_redraft(question, materials, {"label": [answer]},
                                                                rewritten=rewritten))
    return [syn, gate_in, redraft_in, gate_in]


# --------------------------------------------------------------------------
# retrieval, every reading at once (FR 16)
# --------------------------------------------------------------------------

def _cannot_reach(e: sqlite3.Error) -> bool:
    code = getattr(e, "sqlite_errorcode", None)
    return code is not None and (code & 0xFF) in UNREACHABLE_CODES


def _retrieve(readings: list[CompiledReading], connect: Callable[[], sqlite3.Connection],
              breaker: CircuitBreaker) -> list[tuple[RetrievalResult, str, str]]:
    """Only a failure to reach the source counts against the breaker. A system fault leaves the
    breaker as a value, so a malformed query does not mark the source as down."""
    def op(r: CompiledReading):
        try:
            conn = connect()
        except Exception as e:
            raise _Unreachable(repr(e)) from e
        try:
            return execute(r, conn)
        except sqlite3.Error as e:
            if _cannot_reach(e):
                raise _Unreachable(repr(e)) from e
            return _SystemFault(repr(e))
        finally:
            conn.close()

    def one(r: CompiledReading):
        started = _now()
        try:
            res = breaker.call(lambda: op(r))
        except CircuitOpen as e:
            raise _Unreachable(f"circuit open for {e}") from e
        if isinstance(res, _SystemFault):
            raise res
        return res, started, _now()

    if not readings:
        return []
    with ThreadPoolExecutor(max_workers=len(readings)) as pool:
        futures = [pool.submit(one, r) for r in readings]
    out, errors = [], []
    for f in futures:
        try:
            out.append(f.result())
        except (_Unreachable, _SystemFault) as e:
            errors.append(e)
    unreachable = [e for e in errors if isinstance(e, _Unreachable)]
    if unreachable or errors:   # a source that cannot be reached ends the exchange first (FR 15)
        raise (unreachable or errors)[0]
    return out


def _presented(response: dict, plans: dict[str, dict], cat: Catalog, answer: Draft | None) -> list[str]:
    """NFR 27: the identifier-column values of the rows shown, in display order, then any in the
    answer text. Never a free-text value (NFR 21, review F5)."""
    out: list[str] = []
    for view in response["readings"]:
        cols = {i["as"]: i["expr"].get("col") for i in plans[view["label"]]["SELECT"]}
        ids = [a for a in view["columns"] if cols.get(a) and (cat.column(cols[a]) or {}).get("identifier")]
        for row in view["rows"]:
            for a in ids:
                if row.get(a) is not None and str(row[a]) not in out:
                    out.append(str(row[a]))
    for v in answer.presented_identifiers(cat) if answer is not None else []:
        if v not in out:
            out.append(v)
    return out


# --------------------------------------------------------------------------
# one exchange
# --------------------------------------------------------------------------

def run(question: str, history: list[prompts.Exchange], user: dict, deps: Deps,
        on_stage: Callable[[str], None] | None = None) -> Outcome:
    """history holds the earlier exchanges of the same conversation, oldest first."""
    stage = on_stage or (lambda name: None)
    eid = "EX-" + uuid.uuid4().hex[:12]
    rec = _Record(eid, user)
    cat, role = deps.catalog, user["role"]
    understood: str | None = None
    rewritten: str | None = None
    spend = None          # this exchange's spend, where the gateway keeps a budget (task 5)
    stack = ExitStack()

    def cost() -> dict | None:
        """Task 5, transparency: what this exchange cost and what remains (T3: not in the response)."""
        if spend is None:
            return None
        c = {"exchange_usd": round(spend.spent, 6), "remaining_usd": round(deps.gateway.remaining(), 6)}
        operational_log.info("exchange cost: attempt=%s usd=%.6f remaining=%.6f",
                             eid, c["exchange_usd"], c["remaining_usd"])
        return c

    def finish(response: dict, answer: Draft | None = None, failure: str | None = None,
               presented: list[str] | None = None) -> Outcome:
        stage("contract")
        rec.step(CONTRACT)
        spent = cost()
        doc = {"kind": "exchange", "user_id": user.get("user_id"), "role": role, "posed": question,
               "understood_as": understood, "outcome": response["outcome"], "response": response,
               "sources": [{"label": r["label"], "checked": r["checked"], "evidence": r["evidence"]}
                           for r in response["readings"] if r.get("answer")],
               "rating": None}
        if failure:
            doc["failure"] = redact(failure)   # G3: in depth, should provider text ever reach it
        if spent:
            doc["cost"] = spent
        try:
            deps.audit.write_run(rec.entries, doc)
        except Exception as e:   # R1, decision 9: an answer that was not recorded is not presented
            operational_log.error("exchange record not written: attempt=%s user=%s error=%r",
                                  eid, user.get("user_id"), e)
            return Outcome(contract.request_not_completed(understood), prompts.Exchange(question, understood),
                           eid, recorded=False, cost=spent)
        ex = prompts.Exchange(question, understood, presented=list(presented or []))
        if answer is not None and answer.sentences:
            ex.answer = "\n".join(f"[{label}] {answer.answer(label, cat, for_history=True)}"
                                  for label in answer.sentences)
        return Outcome(response, ex, eid, recorded=True, cost=spent)

    try:
        # task 5: the rate across users, and this exchange's own cap, before any model call
        if hasattr(deps.gateway, "exchange"):
            spend = stack.enter_context(deps.gateway.exchange())

        # rewriting, only with earlier exchanges. Layer 3 drops the oldest with no summary (decision 7)
        if history:
            stage("rewriting")
            built = prompts.build_history(history)
            rewritten = _ask(deps, rec, REWRITING, prompts.load("rewrite.md"),
                             lambda fb: prompts.render_rewrite(built, question, feedback=fb),
                             lambda out: prompts.check_rewrite(out, question), _REWRITE_V.schema, _REWRITE_V)
            if _norm(rewritten) != _norm(question):
                understood = rewritten

        # intent analysis and compilation. A plan the compiler rejects is a rejected output (Test 13).
        # No structured output here: unpacked, the schema exceeds what the provider accepts (review,
        # "Settled on Code's proposals"), so every schema breach is a content failure
        stage("intent")
        conv = prompts.compiler_conversation(history, question, rewritten)
        pr = deps.intent_prompt or prompts.load("intent_analysis.md")
        validator = intent_validator()
        intent, compiled = _ask(deps, rec, INTENT, pr,
                                lambda fb: prompts.render_intent(cat, role, question, rewritten, pr, fb),
                                lambda out: (out, compile_intent(out, conv, user, cat, validator)),
                                validator=validator, rejected=(CompileError,))
        rec.step(COMPILATION, [{"label": r.label, "sql": r.sql, "params": r.params} for r in compiled.readings])
        if compiled.disposition == "refer_to_investigation":
            return finish(contract.referred(understood))

        # retrieval
        stage("retrieval")
        try:
            retrieved = _retrieve(compiled.readings, deps.connect, deps.source_breaker)
        except _SystemFault as e:   # review F1: our fault, not the source's
            operational_log.error("retrieval fault: attempt=%s user=%s source=%s error=%s",
                                  eid, user.get("user_id"), deps.source, e)
            return finish(contract.request_not_completed(understood), failure=f"retrieval: {e}")
        except _Unreachable as e:
            # R2, decision 10: not an exchange in the audit log, but logged on the operational
            # channel, without the question text
            operational_log.error("source unreachable: attempt=%s user=%s source=%s error=%s",
                                  eid, user.get("user_id"), deps.source, e)
            return Outcome(contract.source_unreachable(deps.source, understood),
                           prompts.Exchange(question, understood), eid, recorded=False, cost=cost())
        rec.step(RETRIEVAL, [{"label": res.label, "result": res.outcome, "row_count": len(res.rows),
                              "total": res.total, "started": s, "finished": f} for res, s, f in retrieved])

        plans = {r["label"]: r["plan"] for r in intent["readings"]}
        results = {res.label: res for res, _, _ in retrieved}
        materials = [synthesis.material(label, plans[label], res.rows, res.truncated, cat)
                     for label, res in results.items()]
        gaps = compiled.unresolved

        def views(d: Draft | None = None) -> list[dict]:
            out = []
            for label, res in results.items():
                said = d is not None and label in d.sentences
                evidence = list(dict.fromkeys(e for s in d.sentences[label] for e in s.evidence)) if said else None
                out.append(contract.reading_view(label, plans[label], res, cat, role,
                                                 d.answer(label) if said else None, evidence))
            return out

        if not any(m.rows for m in materials):   # an empty result is a finding (Test 35), no model call
            return finish(contract.answered(views(), gaps, understood), presented=[])

        # R7a: stop before the first synthesis call if the whole path cannot fit
        if hasattr(deps.gateway, "ensure_path"):
            deps.gateway.ensure_path(_path_inputs(question, rewritten, materials))

        # synthesis, the gate, and the single regeneration (NFR 19, decisions 3 to 5).
        # Synthesis receives both forms of the question (4.9, review F3)
        stage("synthesis")
        first = _synthesize(deps, rec, SYNTHESIS, lambda: synthesis.draft(
            deps.gateway, question, materials, deps.model, deps.attempts, rewritten=rewritten))
        stage("gate")
        g1 = _gate(deps, rec, GATE, first)

        if not g1.removed:
            final, withheld = first, False
        else:
            stage("regeneration")
            if g1.kept:
                labels = [label for label, _ in first.filled()]
                kept: dict[str, list[str]] = {}
                for n, texts in sorted(g1.kept_by_sentence.items()):
                    kept.setdefault(labels[n - 1], []).extend(texts)
                second = _synthesize(deps, rec, REGENERATION, lambda: synthesis.redraft(
                    deps.gateway, question, materials, kept, deps.model, deps.attempts, rewritten=rewritten))
            else:
                second = _synthesize(deps, rec, REGENERATION, lambda: synthesis.draft(
                    deps.gateway, question, materials, deps.model, deps.attempts, rejected=g1.removed,
                    rewritten=rewritten))
            stage("gate")
            g2 = _gate(deps, rec, REGATE, second)
            final, withheld = second.only(g2.supported), True

        if not final.sentences:   # NFR 19, strict reading: records included are replaced (Test 17)
            return finish(contract.no_supported_assertion(views(), understood))
        response = contract.answered(views(final), gaps, understood, withheld)
        return finish(response, final, presented=_presented(response, plans, cat, final))

    except _Rejected as e:
        return finish(contract.request_not_completed(understood), failure=str(e))
    except RateLimited as e:
        operational_log.warning("exchange refused: attempt=%s user=%s reason=%s", eid, user.get("user_id"), e)
        return finish(contract.request_not_completed(understood, contract.RATE_LIMITED), failure=f"rate: {e}")
    except BudgetExceeded as e:   # not retried, not an outage
        operational_log.warning("model call refused: attempt=%s user=%s reason=%s", eid, user.get("user_id"), e)
        notice = {"exchange": contract.EXCHANGE_CAP,                    # R7b
                  "total": contract.BUDGET_USED_UP, "provider": contract.BUDGET_USED_UP,
                  }.get(e.scope, contract.NOT_COMPLETED)                # G2: a model not priced in .env
        return finish(contract.request_not_completed(understood, notice), failure=f"budget ({e.scope}): {e}")
    except ExchangeCancelled as e:
        operational_log.warning("exchange cancelled: attempt=%s user=%s reason=%s", eid, user.get("user_id"), e)
        return finish(contract.request_not_completed(understood), failure=f"cancelled: {e}")
    except Exception as e:        # R6: whatever escapes ends as request_not_completed, recorded
        operational_log.error("exchange failed: attempt=%s user=%s error=%s: %s",
                              eid, user.get("user_id"), type(e).__name__, redact(str(e)))
        return finish(contract.request_not_completed(understood), failure=f"unexpected: {type(e).__name__}: {e}")
    finally:
        stack.close()


# --------------------------------------------------------------------------
# rating (FR 12, Test 16)
# --------------------------------------------------------------------------

def rate(exchange_id: str, rating: str, user: dict, audit: AuditRepository) -> dict:
    """Held with the recorded exchange, as a later entry on its chain. It does not decide whether
    the exchange is kept (4.9). Only an answer is rated, and only by the person who asked."""
    if rating not in ("up", "down"):
        raise ValueError("a rating is up or down")
    doc = audit.storage.get_investigation(exchange_id)
    if not doc or doc.get("kind") != "exchange":
        raise LookupError(f"no recorded exchange {exchange_id}")
    if doc["user_id"] != user.get("user_id"):
        raise PermissionError("only the person who asked rates the answer")
    if doc["outcome"] != "answered":
        raise ValueError("only an answer is rated")
    return audit.write_event(AuditEntry(exchange_id, RATING, STAGE_NAMES[RATING], "answer_rated", user["role"],
                                        actor_id=user.get("user_id")), {"rating": rating})
