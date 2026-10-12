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
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from jsonschema import Draft202012Validator

from model_client.client import ModelConfig, ModelGateway, ModelResponse
from persistence.audit_repository import AuditEntry, AuditRepository
from shared.circuit_breaker import CircuitBreaker, CircuitOpen, MemoryStore
from shared.circuit_breaker import log as operational_log

from . import contract, gate, prompts, synthesis
from .compiler import HERE, Catalog, CompileError, CompiledReading, RetrievalResult, _norm, compile_intent, \
    execute, intent_validator
from .synthesis import MODEL_CALL_ATTEMPTS, Draft, SynthesisOutputError

_BUNDLE = json.loads((HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))["schemas"]
_REWRITE_V = Draft202012Validator(_BUNDLE["question_rewriting"]["schema"])
_GATE_V = Draft202012Validator(_BUNDLE["support_scoring"]["schema"])

# The order stages are declared in, which the audit trail follows (Test 21).
REWRITING, INTENT, COMPILATION, RETRIEVAL, SYNTHESIS, GATE, REGENERATION, REGATE, CONTRACT, RATING = range(1, 11)
STAGE_NAMES = {REWRITING: "question_rewriting", INTENT: "intent_analysis", COMPILATION: "compilation",
               RETRIEVAL: "retrieval", SYNTHESIS: "synthesis", GATE: "support_gate",
               REGENERATION: "regeneration", REGATE: "support_gate", CONTRACT: "output_contract",
               RATING: "rating"}

REJECTED = (ValueError, prompts.RewriteError, CompileError, gate.GateOutputError, SynthesisOutputError)

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


class _Rejected(Exception):
    """A model stage rejected the output of every attempt. Already recorded."""


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

    def calls(self, ordinal: int, responses: list[ModelResponse], rejections: list[str], model: str):
        """One entry per model call, rejected ones included. A repeated attempt states why it
        supersedes the earlier one (NFR 3, decision 11)."""
        temperature = ModelConfig(model).temperature
        for n, resp in enumerate(responses, 1):
            self._entry(ordinal, "model_output_rejected" if n <= len(rejections) else "model_output_accepted",
                        attempt_number=n,
                        superseded_reason=f"attempt {n - 1} rejected: {rejections[n - 2]}" if n > 1 else None,
                        model_identity=f"{resp.model} · {resp.prompt_identity} · temperature {temperature}",
                        input_tokens=resp.input_tokens, output_tokens=resp.output_tokens)


# --------------------------------------------------------------------------
# model stages
# --------------------------------------------------------------------------

def _ask(deps: Deps, rec: _Record, ordinal: int, prompt: prompts.Prompt, text: str,
         accept: Callable[[Any], Any]) -> Any:
    """Calls the model within the attempts. accept() validates the parsed output and raises one of
    REJECTED where it breaks a rule. Every call is recorded, whatever the result."""
    responses, rejections = [], []
    try:
        for _ in range(deps.attempts):
            resp = deps.gateway.invoke(text, ModelConfig(model=deps.model, prompt_identity=prompt.identity))
            responses.append(resp)
            try:
                return accept(resp.json())
            except REJECTED as e:
                rejections.append(str(e))
        raise _Rejected(f"{STAGE_NAMES[ordinal]} rejected after {deps.attempts} attempt(s): {rejections[-1]}")
    finally:
        rec.calls(ordinal, responses, rejections, deps.model)


def _valid(validator: Draft202012Validator, out: Any) -> Any:
    errors = sorted(validator.iter_errors(out), key=lambda e: list(e.path))
    if errors:
        raise ValueError(f"schema: {errors[0].message}")
    return out


def _synthesize(deps: Deps, rec: _Record, ordinal: int, call: Callable[[], Draft]) -> Draft:
    try:
        d = call()
    except SynthesisOutputError as e:
        rec.calls(ordinal, e.responses, e.rejections, deps.model)
        raise _Rejected(str(e)) from e
    rec.calls(ordinal, d.responses, d.rejections, deps.model)
    return d


def _gate(deps: Deps, rec: _Record, ordinal: int, d: Draft) -> gate.GateResult:
    sentences = d.filled()
    records = [(label, m.rows) for label, m in d.materials.items() if m.rows]
    aliases = {label: set(m.columns) for label, m in d.materials.items()}
    text = prompts.render_gate(gate.render_answer(sentences), records)
    return _ask(deps, rec, ordinal, prompts.load("gate.md"), text,
                lambda out: gate.evaluate(_valid(_GATE_V, out), sentences, aliases))


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

    def finish(response: dict, answer: Draft | None = None, failure: str | None = None,
               presented: list[str] | None = None) -> Outcome:
        stage("contract")
        rec.step(CONTRACT)
        doc = {"kind": "exchange", "user_id": user.get("user_id"), "role": role, "posed": question,
               "understood_as": understood, "outcome": response["outcome"], "response": response,
               "sources": [{"label": r["label"], "checked": r["checked"], "evidence": r["evidence"]}
                           for r in response["readings"] if r.get("answer")],
               "rating": None}
        if failure:
            doc["failure"] = failure
        try:
            deps.audit.write_run(rec.entries, doc)
        except Exception as e:   # R1, decision 9: an answer that was not recorded is not presented
            operational_log.error("exchange record not written: attempt=%s user=%s error=%r",
                                  eid, user.get("user_id"), e)
            return Outcome(contract.request_not_completed(understood), prompts.Exchange(question, understood),
                           eid, recorded=False)
        ex = prompts.Exchange(question, understood, presented=list(presented or []))
        if answer is not None and answer.sentences:
            ex.answer = "\n".join(f"[{label}] {answer.answer(label, cat, for_history=True)}"
                                  for label in answer.sentences)
        return Outcome(response, ex, eid, recorded=True)

    try:
        # rewriting, only with earlier exchanges. Layer 3 drops the oldest with no summary (decision 7)
        if history:
            stage("rewriting")
            text = prompts.render_rewrite(prompts.build_history(history), question)
            rewritten = _ask(deps, rec, REWRITING, prompts.load("rewrite.md"), text,
                             lambda out: prompts.check_rewrite(_valid(_REWRITE_V, out), question))
            if _norm(rewritten) != _norm(question):
                understood = rewritten

        # intent analysis and compilation. A plan the compiler rejects is a rejected output (Test 13)
        stage("intent")
        conv = prompts.compiler_conversation(history, question, rewritten)
        pr = deps.intent_prompt or prompts.load("intent_analysis.md")
        validator = intent_validator()
        intent, compiled = _ask(deps, rec, INTENT, pr, prompts.render_intent(cat, role, question, rewritten, pr),
                                lambda out: (out, compile_intent(out, conv, user, cat, validator)))
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
                           prompts.Exchange(question, understood), eid, recorded=False)
        rec.step(RETRIEVAL, [{"label": res.label, "result": res.outcome, "row_count": len(res.rows),
                              "total": res.total, "started": s, "finished": f} for res, s, f in retrieved])

        plans = {r["label"]: r["plan"] for r in intent["readings"]}
        results = {res.label: res for res, _, _ in retrieved}
        materials = [synthesis.material(label, plans[label], res.rows, res.truncated)
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
