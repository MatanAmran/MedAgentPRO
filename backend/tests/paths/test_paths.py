"""
Every path an exchange can take, end to end, with the same invariants checked on each.

Part A drives pipeline.run through every branch (scripted model, real demo store) and checks, on
every path: run() never raises; the response is a valid exchange_response with one of the five
outcomes; recorded matches the outcome; every model call made is in the audit trail; the trail
verifies and ends at the contract; no model stage is resent with an identical prompt; the number of
model calls stays within the design bound.

Part B wires the real-model path with a fake provider behind BudgetedGateway and Cancellable, as
the server does: budget, exchange cap, rate limit, cancellation.

Part C drives the HTTP server: every outcome over the stream, conversations that continue after
every kind of ending, and the states that could leave a conversation stuck.

No test reaches a real model (6.1).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import sqlite3
import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api.routes import router
from api.state import AppState, Cancellable
from demo_data import seed
from model_client import budget as b
from model_client.client import ModelCallError, ModelConfig, ModelGateway, ModelResponse
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import compiler as c
from query_assistance import contract
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from shared.circuit_breaker import CircuitBreaker, MemoryStore

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
INTENT_PROMPT = p.load(seed.HERE / "intent_analysis.md")
BUNDLE = json.loads((c.HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))["schemas"]
RESPONSE_V = Draft202012Validator(BUNDLE["exchange_response"]["schema"])
OUTCOMES = {"answered", "referred_to_investigation", "no_supported_assertion", "source_unreachable",
            "request_not_completed"}
MAX_CALLS = 12   # task 5: the most model calls one exchange can make, by design

APPROVER = {"role": "APPROVER", "user_id": "AP-01"}
PLANNER = {"role": "PLANNER", "user_id": "PL-02"}
Q = "Which suppliers delivered late in weeks 1 to 13?"

LATE = {"label": "late", "plan": {
    "SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
               {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}],
    "FROM": "commitments",
    "WHERE": {"AND": [{"BETWEEN": {"col": "commitments.week", "from": {"value": 1}, "to": {"value": 13}}},
                      {"EQ": [{"metric": "late_by_delivery"}, {"value": True}]}]},
    "GROUP_BY": ["supplier_id"], "ORDER_BY": [{"by": "late_count", "dir": "DESC"}], "LIMIT": 5}}
PROMISE = {"label": "promise", "plan": dict(LATE["plan"], WHERE={"AND": [
    {"BETWEEN": {"col": "commitments.week", "from": {"value": 1}, "to": {"value": 13}}},
    {"EQ": [{"metric": "late_by_promise"}, {"value": True}]}]})}
INTENT = {"disposition": "retrieve", "readings": [LATE]}
INTENT_TWO = {"disposition": "retrieve", "ambiguous": [{"term": "late", "alternatives": [
    {"metric": "late_by_delivery"}, {"metric": "late_by_promise"}]}], "readings": [LATE, PROMISE]}
REFER = {"disposition": "refer_to_investigation"}
ACTION = {"disposition": "out_of_scope_action"}
CLARIFY = {"disposition": "needs_clarification"}
Q_FOLLOW_UP = "And in weeks 14 to 26?"
EMPTY = {"disposition": "retrieve", "readings": [{"label": "s40", "plan": {
    "SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
    "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-040"}]}}}]}
ABSENT = {"disposition": "retrieve", "readings": [{"label": "s999", "plan": {
    "SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
    "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-999"}]}}}]}
BAD_COLUMN = {"disposition": "retrieve", "readings": [{"label": "r", "plan": {
    "SELECT": [{"expr": {"col": "commitments.nope"}, "as": "x"}], "FROM": "commitments"}}]}
BAD_SCHEMA = {"disposition": "retrieve"}   # readings missing

ONE = "{late.supplier_id#1} had the most late lines, {late.late_count#1}."
TWO_S = "{late.supplier_id#2} followed with {late.late_count#2}."
SYN = {"readings": [{"label": "late", "sentences": [{"text": ONE}, {"text": TWO_S}]}]}
SYN_ONE = {"readings": [{"label": "late", "sentences": [{"text": ONE}]}]}
SYN_TWO_READINGS = {"readings": [
    {"label": "late", "sentences": [{"text": ONE}]},
    {"label": "promise", "sentences": [{"text": "{promise.supplier_id#1} had {promise.late_count#1}."}]}]}
SYN_TYPED_DIGIT = {"readings": [{"label": "late",
                                  "sentences": [{"text": "7 suppliers, led by {late.supplier_id#1}."}]}]}
SYN_NO_REFERENCE = {"readings": [{"label": "late", "sentences": [{"text": "Several suppliers were late."}]}]}


def gate_says(*verdicts):
    """A verdict per numbered sentence in the gate prompt, the last one repeating; every sentence covered."""
    def answer(prompt):
        body = prompt.split("Answer:\n")[-1].split("\nRecords:")[0]
        claims = []
        for i, (n, label, text) in enumerate(re.findall(r"^(\d+)\. \[(\w+)\] (.*)$", body, re.M)):
            v = verdicts[min(i, len(verdicts) - 1)]
            claims.append({"sentence": int(n), "text": text, "verdict": v,
                           "evidence": [] if v == "uncovered" else [f"{label}.supplier_id"]})
        return {"claims": claims}
    return answer


# Chat, 18.10, 09:40: words that describe another metric than the reading's examined line (question 3
# on the real model). The scripted gate contradicts them only where the examined line it was given
# says the reading is not about promised dates, so the path also shows that line reaches the gate.
OTHER_METRIC = "{late.supplier_id#2} had {late.late_count#2} lines whose promised date was after the requested date."
SYN_OTHER_METRIC = {"readings": [{"label": "late", "sentences": [{"text": ONE}, {"text": OTHER_METRIC}]}]}
SYN_ONLY_OTHER_METRIC = {"readings": [{"label": "late", "sentences": [{"text": OTHER_METRIC}]}]}


def gate_reads_examined(prompt):
    out = gate_says("supported")(prompt)
    tail = prompt.split("Answer:\n")[-1]   # past the prompt's own examples
    examined = dict(re.findall(r"^\[(\w+)\] examined: (.*)$", tail, re.M))
    for claim in out["claims"]:
        label = re.search(rf"^{claim['sentence']}\. \[(\w+)\]", tail, re.M).group(1)
        if "promised date" in claim["text"] and "committed" not in examined[label]:
            claim["verdict"] = "contradicted"
    return out


def gate_skips(prompt):
    out = gate_says("supported")(prompt)
    out["claims"] = out["claims"][:-1] if len(out["claims"]) > 1 else []
    return out


class Raw:
    """A response with no usable output, or one that is not JSON."""
    def __init__(self, text, finish_reason="stop"):
        self.text, self.finish_reason = text, finish_reason


class Script(ModelGateway):
    """Answers by prompt name, in order. An entry is an output, a callable on the prompt, a Raw, or an
    exception to raise. A prompt name with no entries left is a test bug, reported as such."""

    def __init__(self, **by_prompt):
        self.queues = {k: list(v) for k, v in by_prompt.items()}
        self.calls: list[tuple[str, ModelConfig]] = []
        self.lock = threading.Lock()

    def invoke(self, prompt, config):
        name = config.prompt_identity.split("@")[0]
        with self.lock:
            self.calls.append((prompt, config))
            queue = self.queues.get(name)
            if not queue:
                raise AssertionError(f"unexpected model call: {name}")
            out = queue.pop(0)
        if isinstance(out, BaseException):
            raise out
        if callable(out):
            out = out(prompt)
        if isinstance(out, Raw):
            return ModelResponse(out.text, "scripted", len(prompt) // 4 + 1, 5, config.prompt_identity,
                                 finish_reason=out.finish_reason)
        text = json.dumps(out)
        return ModelResponse(text, "scripted", len(prompt) // 4 + 1, len(text) // 4 + 1, config.prompt_identity,
                             finish_reason="stop")


def make_deps(gw, db_path, storage=None, connect=None, audit=None):
    storage = storage or MemoryStorage()
    audit = audit or AuditRepository(storage, CircuitBreaker("system_store", MemoryStore()), b"test-key")
    return pl.Deps(gw, "scripted", CAT, connect or pl.connect_readonly(db_path), audit, INTENT_PROMPT)


def earlier_exchange(db_path):
    """A first exchange that answered, to ask follow-ups after."""
    gw = Script(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    return pl.run(Q, [], APPROVER, make_deps(gw, db_path)).exchange


def check_invariants(out, gw, d, question, *, outcome, recorded, calls=None, notice=None):
    """What every path must hold, whatever it is."""
    r = out.response
    assert r["outcome"] in OUTCOMES and r["outcome"] == outcome, r
    errors = sorted(RESPONSE_V.iter_errors(r), key=str)
    assert not errors, errors[0].message
    assert out.recorded is recorded
    assert out.exchange.posed == question
    made = len(gw.calls) if gw is not None else 0
    assert made <= MAX_CALLS
    if calls is not None:
        assert made == calls, [cfg.prompt_identity for _, cfg in gw.calls]
    # no model stage resent with an identical prompt (task 5 review: no retry without a change in input)
    if gw is not None:
        for (p1, c1), (p2, c2) in itertools.pairwise(gw.calls):
            if c1.prompt_identity == c2.prompt_identity:
                assert p1 != p2, f"{c1.prompt_identity} resent unchanged"
    storage = d.audit.storage
    doc = storage.get_investigation(out.exchange_id)
    if recorded:
        assert doc and doc["kind"] == "exchange" and doc["outcome"] == outcome and doc["response"] == r
        trail = sorted((e for e in storage.audit_log if e["investigation_id"] == out.exchange_id),
                       key=lambda e: e["seq"])
        assert trail[-1]["agent"] == "output_contract"
        model_entries = [e for e in trail if e["event_type"].startswith("model_")]
        assert len(model_entries) == made, "every model call is in the audit trail"
        assert d.audit.verify_chain(out.exchange_id) == len(trail)
        if outcome == "request_not_completed":
            assert doc.get("failure")
    else:
        assert doc is None
    if notice is not None:
        assert r.get("notice") == notice


# ==========================================================================
# Part A: every branch of one exchange
# ==========================================================================

A_PATHS = [
    # id, question, user, history?, script, outcome, recorded, calls, extra check
    ("answered_all_supported", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")]),
     "answered", True, 3, lambda r: "withheld" not in r and r["readings"][0]["answer"]),
    ("answered_two_readings", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT_TWO], synthesis=[SYN_TWO_READINGS], support_scoring=[gate_says("supported")]),
     "answered", True, 3, lambda r: [v["label"] for v in r["readings"]] == ["late", "promise"]
                                    and all(v["checked"].startswith("Examined") for v in r["readings"])
                                    and [v["title"] for v in r["readings"]] == [
                                        "Delivered after the requested date",
                                        "The supplier committed to a date after the requested date"]
                                    and r["ambiguous_terms"] == ["late"]),
    # Task 7, Q4: a term found only in the rewrite (model text) compiles, but is never named on screen
    ("ambiguous_term_only_in_rewrite_not_named", "And in weeks 1 to 13?", APPROVER, True,
     dict(question_rewriting=[{"question": Q, "carried_from_history": True}], intent_analysis_demo=[INTENT_TWO],
          synthesis=[SYN_TWO_READINGS], support_scoring=[gate_says("supported")]),
     "answered", True, 4, lambda r: "ambiguous_terms" not in r and r["understood_as"] == Q
                                    and len(r["readings"]) == 2),
    # Task 8, A1: a request to change records retrieves nothing and is answered by a fixed sentence
    ("action_declined", "Update the promised date of PO-87006 to 20 October.", APPROVER, False,
     dict(intent_analysis_demo=[ACTION]), "answered", True, 1,
     lambda r: r["readings"] == [] and r["gaps"] == [] and r["notice"] == contract.ACTION_DECLINED),
    ("needs_clarification", Q_FOLLOW_UP, APPROVER, False,
     dict(intent_analysis_demo=[CLARIFY]), "answered", True, 1,
     lambda r: r["readings"] == [] and r["gaps"] == [] and r["notice"] == contract.NEEDS_CLARIFICATION),
    ("planner_sees_own_rows_only", Q, PLANNER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")]),
     "answered", True, 3, lambda r: "limited to the records you hold" in r["readings"][0]["checked"]),
    ("referred", "Why is S-014 late so often?", APPROVER, False,
     dict(intent_analysis_demo=[REFER]), "referred_to_investigation", True, 1,
     lambda r: r["offer_investigation"]),
    ("empty_result_no_synthesis", "What did S-040 supply?", APPROVER, False,
     dict(intent_analysis_demo=[EMPTY]), "answered", True, 1,
     lambda r: r["readings"][0]["notice"] == "No records matched."),
    ("identifier_not_held", "What did S-999 supply?", APPROVER, False,
     dict(intent_analysis_demo=[ABSENT]), "answered", True, 1,
     lambda r: "S-999" in json.dumps(r["readings"][0])),
    ("redraft_from_kept", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN], synthesis_redraft=[SYN_ONE],
          support_scoring=[gate_says("supported", "uncovered"), gate_says("supported")]),
     "answered", True, 5, lambda r: r.get("withheld")),
    ("contradicted_like_unsupported", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN], synthesis_redraft=[SYN_ONE],
          support_scoring=[gate_says("supported", "contradicted"), gate_says("supported")]),
     "answered", True, 5, lambda r: r.get("withheld")),
    ("gate_removes_other_metric_wording", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN_OTHER_METRIC], synthesis_redraft=[SYN_ONE],
          support_scoring=[gate_reads_examined, gate_reads_examined]),
     "answered", True, 5, lambda r: r.get("withheld") and "promised" not in r["readings"][0]["answer"]),
    ("gate_removes_only_sentence_other_metric", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN_ONLY_OTHER_METRIC, SYN_ONLY_OTHER_METRIC],
          support_scoring=[gate_reads_examined, gate_reads_examined]),
     "no_supported_assertion", True, 5, None),
    ("fresh_draft_then_supported", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN, SYN_ONE],
          support_scoring=[gate_says("uncovered"), gate_says("supported")]),
     "answered", True, 5, lambda r: r.get("withheld")),
    ("fresh_draft_then_nothing", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN, SYN_ONE],
          support_scoring=[gate_says("uncovered"), gate_says("uncovered")]),
     "no_supported_assertion", True, 5, lambda r: r["readings"] == [] or all(not v.get("rows") for v in r["readings"])),
    ("redraft_then_nothing", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN], synthesis_redraft=[SYN_ONE],
          support_scoring=[gate_says("supported", "uncovered"), gate_says("uncovered")]),
     "no_supported_assertion", True, 5, None),
    ("intent_fixed_by_feedback", Q, APPROVER, False,
     dict(intent_analysis_demo=[BAD_COLUMN, INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")]),
     "answered", True, 4, None),
    ("intent_schema_breach_fixed_by_feedback", Q, APPROVER, False,
     dict(intent_analysis_demo=[BAD_SCHEMA, INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")]),
     "answered", True, 4, None),
    ("intent_rejected_twice", Q, APPROVER, False,
     dict(intent_analysis_demo=[BAD_COLUMN, BAD_COLUMN]), "request_not_completed", True, 2, None),
    ("intent_not_json", Q, APPROVER, False,
     dict(intent_analysis_demo=[Raw("this is not json")]), "request_not_completed", True, 1, None),
    ("synthesis_fixed_by_feedback", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN_TYPED_DIGIT, SYN_ONE],
          support_scoring=[gate_says("supported")]),
     "answered", True, 4, None),
    ("synthesis_rejected_twice", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN_TYPED_DIGIT, SYN_TYPED_DIGIT]),
     "request_not_completed", True, 3, None),
    ("synthesis_no_reference_fixed_by_feedback", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN_NO_REFERENCE, SYN], support_scoring=[gate_says("supported")]),
     "answered", True, 4, None),
    ("synthesis_no_reference_twice", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN_NO_REFERENCE, SYN_NO_REFERENCE]),
     "request_not_completed", True, 3, None),
    ("synthesis_not_json_not_retried", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[Raw("this is not json")]), "request_not_completed", True, 2, None),
    ("gate_provider_breach_fixed_by_feedback", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[{"verdicts": []}, gate_says("supported")]),
     "answered", True, 4, None),
    ("gate_skips_then_covers", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_skips, gate_says("supported")]),
     "answered", True, 4, None),
    ("gate_skips_twice", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_skips, gate_skips]),
     "request_not_completed", True, 4, None),
    ("refusal_not_retried", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[Raw("I can't help with that", "content_filter")]),
     "request_not_completed", True, 2, None),
    ("truncated_not_retried", Q, APPROVER, False,
     dict(intent_analysis_demo=[Raw('{"disposition": "retr', "length")]), "request_not_completed", True, 1, None),
    ("empty_not_retried", Q, APPROVER, False,
     dict(intent_analysis_demo=[Raw("   ")]), "request_not_completed", True, 1, None),
    ("fenced_json_accepted", Q, APPROVER, False,
     dict(intent_analysis_demo=[Raw("```json\n" + json.dumps(REFER) + "\n```")]),
     "referred_to_investigation", True, 1, None),
    ("timeout_not_retried", Q, APPROVER, False,
     dict(intent_analysis_demo=[ModelCallError("timeout", "timed out", outage=True)]),
     "request_not_completed", True, 1, None),
    ("client_error_not_retried", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[ModelCallError("client_error", "400 bad schema")]),
     "request_not_completed", True, 2, None),
    ("unexpected_exception", Q, APPROVER, False,
     dict(intent_analysis_demo=[ModelCallError("unexpected", "RuntimeError: boom")]),
     "request_not_completed", True, 1, None),
    ("gate_raises_after_synthesis", Q, APPROVER, False,
     dict(intent_analysis_demo=[INTENT], synthesis=[SYN],
          support_scoring=[ModelCallError("unexpected", "RuntimeError: boom")]),
     "request_not_completed", True, 3, None),
    ("follow_up_rewritten", "And by promised date?", APPROVER, True,
     dict(question_rewriting=[{"question": "Which suppliers were late by promised date in weeks 1 to 13?",
                               "carried_from_history": True}],
          intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")]),
     "answered", True, 4, lambda r: r["understood_as"].startswith("Which suppliers were late by promised")),
    ("follow_up_unchanged_no_understood_as", Q, APPROVER, True,
     dict(question_rewriting=[{"question": Q, "carried_from_history": False}],
          intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")]),
     "answered", True, 4, lambda r: not r.get("understood_as")),
    ("follow_up_rewrite_rejected_twice", "And the other one?", APPROVER, True,
     dict(question_rewriting=[{"question": "What changed?", "carried_from_history": False},
                              {"question": "What changed?", "carried_from_history": False}]),
     "request_not_completed", True, 2, None),
    ("follow_up_identifier_not_in_conversation", "And the other one?", APPROVER, True,
     dict(question_rewriting=[{"question": "What did S-031 supply?", "carried_from_history": True}],
          intent_analysis_demo=[{"disposition": "retrieve", "readings": [{"label": "r", "plan": {
              "SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
              "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-031"}]}}}]}] * 2),
     "request_not_completed", True, 3, None),
]


@pytest.mark.parametrize("name,question,user,follow,script,outcome,recorded,calls,extra",
                         A_PATHS, ids=[x[0] for x in A_PATHS])
def test_path(db_path, name, question, user, follow, script, outcome, recorded, calls, extra):
    history = [earlier_exchange(db_path)] if follow else []
    gw = Script(**script)
    d = make_deps(gw, db_path)
    stages: list[str] = []
    out = pl.run(question, history, user, d, on_stage=stages.append)
    check_invariants(out, gw, d, question, outcome=outcome, recorded=recorded, calls=calls)
    assert stages[-1] == "contract"
    if extra:
        assert extra(out.response), out.response
    leftover = {k: len(v) for k, v in gw.queues.items() if v}
    assert not leftover, f"scripted outputs not used: {leftover}"


def test_feedback_attempt_carries_the_rejection_and_is_recorded(db_path):
    gw = Script(intent_analysis_demo=[BAD_COLUMN, INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    d = make_deps(gw, db_path)
    out = pl.run(Q, [], APPROVER, d)
    first, second = [pr for pr, cfg in gw.calls if cfg.prompt_identity.startswith("intent")]
    assert "rejected" in second.lower() and "rejected" not in first.split("Output:")[-1].lower()
    trail = [e for e in d.audit.storage.audit_log if e["investigation_id"] == out.exchange_id]
    fb = [e for e in trail if e.get("feedback_attempt")]
    assert len(fb) == 1 and fb[0]["agent"] == "intent_analysis"


# --- the source -------------------------------------------------------------

def test_source_missing_is_unreachable_and_not_recorded(db_path, tmp_path):
    gw = Script(intent_analysis_demo=[INTENT])
    d = make_deps(gw, db_path, connect=pl.connect_readonly(tmp_path / "gone.db"))
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, gw, d, Q, outcome="source_unreachable", recorded=False, calls=1)
    assert not (tmp_path / "gone.db").exists()


def test_source_breaker_open_is_unreachable_without_trying(db_path):
    tried = []
    gw = Script(intent_analysis_demo=[INTENT])
    d = make_deps(gw, db_path, connect=lambda: tried.append(1) or sqlite3.connect(db_path))
    d.source_breaker = CircuitBreaker("company_store", MemoryStore(), threshold=1)
    d.source_breaker.record_failure()
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, gw, d, Q, outcome="source_unreachable", recorded=False, calls=1)
    assert tried == []


def test_one_of_two_readings_unreachable_ends_the_exchange(db_path):
    n = {"i": 0}
    lock = threading.Lock()

    def connect():
        with lock:
            n["i"] += 1
            k = n["i"]
        if k == 2:
            raise sqlite3.OperationalError("unable to open database file")
        return sqlite3.connect(db_path)
    gw = Script(intent_analysis_demo=[INTENT_TWO])
    d = make_deps(gw, db_path, connect=connect)
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, gw, d, Q, outcome="source_unreachable", recorded=False, calls=1)


def test_retrieval_system_fault_is_recorded(db_path):
    gw = Script(intent_analysis_demo=[INTENT])
    d = make_deps(gw, db_path, connect=lambda: sqlite3.connect(":memory:"))
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, gw, d, Q, outcome="request_not_completed", recorded=True, calls=1)
    assert d.source_breaker.failure_count == 0


def test_record_that_cannot_be_written_is_not_presented(db_path):
    storage = MemoryStorage()
    audit = AuditRepository(storage, CircuitBreaker("system_store", MemoryStore(), threshold=1), b"k")
    audit.breaker.record_failure()
    gw = Script(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    d = make_deps(gw, db_path, storage, audit=audit)
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, gw, d, Q, outcome="request_not_completed", recorded=False, calls=3)
    assert out.response["readings"] == []


@pytest.mark.parametrize("ending", ["unreachable", "not_completed", "referred", "no_supported"])
def test_conversation_continues_after_every_ending(db_path, tmp_path, ending):
    """A follow-up after each kind of ending must still run: the history it leaves behind is usable."""
    if ending == "unreachable":
        d0 = make_deps(Script(intent_analysis_demo=[INTENT]), db_path, connect=pl.connect_readonly(tmp_path / "x.db"))
    elif ending == "not_completed":
        d0 = make_deps(Script(intent_analysis_demo=[BAD_COLUMN, BAD_COLUMN]), db_path)
    elif ending == "referred":
        d0 = make_deps(Script(intent_analysis_demo=[REFER]), db_path)
    else:
        d0 = make_deps(Script(intent_analysis_demo=[INTENT], synthesis=[SYN, SYN_ONE],
                              support_scoring=[gate_says("uncovered"), gate_says("uncovered")]), db_path)
    first = pl.run(Q, [], APPROVER, d0)
    gw = Script(question_rewriting=[{"question": "Which suppliers delivered late in weeks 1 to 13 by promised date?",
                                     "carried_from_history": True}],
                intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    d = make_deps(gw, db_path)
    out = pl.run("And by promised date?", [first.exchange], APPROVER, d)
    check_invariants(out, gw, d, "And by promised date?", outcome="answered", recorded=True, calls=4)


def test_long_conversation_keeps_history_within_its_budget(db_path):
    """Twelve answered exchanges, then a follow-up: the rewriting prompt stays bounded (layers 2 and 3)."""
    history = []
    for _ in range(12):
        history.append(earlier_exchange(db_path))
    gw = Script(question_rewriting=[{"question": Q, "carried_from_history": True}],
                intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    d = make_deps(gw, db_path)
    out = pl.run("Again, please?", history, APPROVER, d)
    check_invariants(out, gw, d, "Again, please?", outcome="answered", recorded=True, calls=4)
    rewrite_prompt = gw.calls[0][0]
    assert len(rewrite_prompt) < 40_000


# ==========================================================================
# Part B: the real-model wiring, with a fake provider behind the budget
# ==========================================================================

ENV = {"ANTHROPIC_API_KEY": "test-key-not-real", "MODEL_ID": "scripted", "MODEL_PRICE_IN": "2",
       "MODEL_PRICE_OUT": "10", "MODEL_BUDGET_USD": "3.5", "MODEL_MAX_USD_PER_EXCHANGE": "0.25",
       "MODEL_MAX_OUTPUT_TOKENS": "2000", "MODEL_MAX_EXCHANGES_PER_MINUTE": "10"}


def budgeted(inner, ledger, **over):
    return b.BudgetedGateway(inner, b.BudgetConfig.from_env({**ENV, **over}), ledger)


def happy():
    return Script(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])


def test_real_wiring_answers_and_reports_cost(db_path, ledger):
    inner = happy()
    gw = Cancellable(budgeted(inner, ledger), threading.Event())
    d = make_deps(gw, db_path)
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, inner, d, Q, outcome="answered", recorded=True, calls=3)
    assert out.cost and out.cost["exchange_usd"] > 0 and "cost" not in out.response
    assert abs(ledger.total - out.cost["exchange_usd"]) < 1e-9
    assert d.audit.storage.get_investigation(out.exchange_id)["cost"] == out.cost


def test_total_budget_used_up_before_any_call(db_path, ledger):
    ledger.add("scripted", 3.49)
    inner = happy()
    d = make_deps(Cancellable(budgeted(inner, ledger), threading.Event()), db_path)
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, inner, d, Q, outcome="request_not_completed", recorded=True, calls=0,
                     notice=contract.BUDGET_USED_UP)


def test_exchange_cap_stops_before_synthesis(db_path, ledger):
    inner = Script(intent_analysis_demo=[INTENT])
    d = make_deps(Cancellable(budgeted(inner, ledger, MODEL_MAX_USD_PER_EXCHANGE="0.08"), threading.Event()), db_path)
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, inner, d, Q, outcome="request_not_completed", recorded=True, calls=1,
                     notice=contract.EXCHANGE_CAP)


def test_rate_limit_refuses_before_any_call(db_path, ledger):
    gw = budgeted(Script(intent_analysis_demo=[REFER] * 3), ledger, MODEL_MAX_EXCHANGES_PER_MINUTE="2")
    outs = [pl.run("Why is S-014 late so often?", [], APPROVER, make_deps(Cancellable(gw, threading.Event()), db_path))
            for _ in range(3)]
    assert [o.response["outcome"] for o in outs] == ["referred_to_investigation"] * 2 + ["request_not_completed"]
    notice = outs[2].response.get("notice")
    assert notice.startswith(contract.RATE_LIMITED.split("{seconds}")[0]) and notice.endswith(" seconds.")


def test_timeout_keeps_the_worst_case_charged_and_refused_connection_releases_it(db_path, ledger):
    gw = budgeted(Script(intent_analysis_demo=[ModelCallError("timeout", "timed out", outage=True),
                                               ModelCallError("connection", "refused", reached_provider=False)]),
                  ledger)
    pl.run(Q, [], APPROVER, make_deps(Cancellable(gw, threading.Event()), db_path))
    charged = ledger.total
    assert charged > 0
    pl.run(Q, [], APPROVER, make_deps(Cancellable(gw, threading.Event()), db_path))
    assert ledger.total == charged


def test_cancelled_before_synthesis_makes_no_further_call(db_path, ledger):
    cancelled = threading.Event()
    inner = Script(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    d = make_deps(Cancellable(budgeted(inner, ledger), cancelled), db_path)
    out = pl.run(Q, [], APPROVER, d, on_stage=lambda s: s == "synthesis" and cancelled.set())
    check_invariants(out, inner, d, Q, outcome="request_not_completed", recorded=True, calls=1)
    assert "cancelled" in d.audit.storage.get_investigation(out.exchange_id)["failure"]


def test_concurrent_exchanges_never_cross_the_total(db_path, ledger):
    """Twenty exchanges at once on a budget for a few: the ledger never ends above the cap."""
    cfg_budget = "0.02"
    gw = budgeted(Script(intent_analysis_demo=[INTENT] * 20, synthesis=[SYN] * 20,
                         support_scoring=[gate_says("supported")] * 20), ledger,
                  MODEL_BUDGET_USD=cfg_budget, MODEL_MAX_EXCHANGES_PER_MINUTE="100")
    outs: list = []

    def one():
        outs.append(pl.run(Q, [], APPROVER, make_deps(Cancellable(gw, threading.Event()), db_path)))
    threads = [threading.Thread(target=one) for _ in range(20)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert len(outs) == 20
    assert ledger.total <= float(cfg_budget) + 1e-9
    assert {o.response["outcome"] for o in outs} <= {"answered", "request_not_completed"}


# ==========================================================================
# Part C: the server
# ==========================================================================

AP, PL = {"X-Demo-User": "AP-01"}, {"X-Demo-User": "PL-02"}


class Server:
    """The app as main.create_app builds it, with the model chosen per question by the test."""

    def __init__(self, db_path, by_question: dict[str, Script] | None = None, gateway_for=None,
                 wait_seconds: float = 10.0):
        self.storage = MemoryStorage()
        audit = AuditRepository(self.storage, CircuitBreaker("system_store", MemoryStore()), b"test-key")
        self.by_question = by_question or {}
        deps = pl.Deps(None, "scripted", CAT, pl.connect_readonly(db_path), audit, INTENT_PROMPT)
        self.state = AppState(deps, "scripted", gateway_for or (lambda q: self.by_question[q]),
                              wait_seconds=wait_seconds)
        app = FastAPI()
        app.include_router(router(self.state))
        self.client = TestClient(app)

    def conversation(self, headers=AP) -> str:
        r = self.client.post("/api/conversations", headers=headers)
        assert r.status_code == 201
        return r.json()["conversation_id"]

    def ask(self, cid, question, headers=AP, request_id=None):
        return self.client.post(f"/api/conversations/{cid}/exchanges", headers=headers,
                                json={"question": question, "request_id": request_id or uuid.uuid4().hex})


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def check_stream(r) -> dict:
    """Exactly one terminal event, last; stages before it; the conversation is free afterwards."""
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events(r.text)
    kinds = [k for k, _ in evs]
    assert kinds.count("result") + kinds.count("error") == 1 and kinds[-1] in ("result", "error")
    assert all(k == "stage" for k in kinds[:-1])
    return evs[-1][1]


C_PATHS = [
    ("answered", dict(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")]),
     "answered", True),
    ("referred", dict(intent_analysis_demo=[REFER]), "referred_to_investigation", True),
    ("no_supported", dict(intent_analysis_demo=[INTENT], synthesis=[SYN, SYN_ONE],
                          support_scoring=[gate_says("uncovered"), gate_says("uncovered")]),
     "no_supported_assertion", True),
    ("not_completed", dict(intent_analysis_demo=[BAD_COLUMN, BAD_COLUMN]), "request_not_completed", True),
    ("model_raises", dict(intent_analysis_demo=[RuntimeError("boom")]), "request_not_completed", True),
]


@pytest.mark.parametrize("name,script,outcome,recorded", C_PATHS, ids=[x[0] for x in C_PATHS])
def test_server_path_then_the_conversation_goes_on(db_path, name, script, outcome, recorded):
    follow = "And by promised date?"
    s = Server(db_path, {Q: Script(**script),
                         follow: Script(question_rewriting=[{"question": Q + " By promised date.",
                                                             "carried_from_history": True}],
                                        intent_analysis_demo=[INTENT], synthesis=[SYN],
                                        support_scoring=[gate_says("supported")])})
    cid = s.conversation()
    res = check_stream(s.ask(cid, Q))
    assert res["response"]["outcome"] == outcome and res["recorded"] is recorded
    res2 = check_stream(s.ask(cid, follow))
    assert res2["response"]["outcome"] == "answered"
    conv = s.client.get(f"/api/conversations/{cid}", headers=AP).json()
    assert [e["posed"] for e in conv["exchanges"]] == [Q, follow]


def test_server_source_unreachable_then_goes_on(db_path, tmp_path):
    s = Server(db_path, {Q: Script(intent_analysis_demo=[INTENT]), "Again?": Script(
        question_rewriting=[{"question": Q, "carried_from_history": True}], intent_analysis_demo=[INTENT],
        synthesis=[SYN], support_scoring=[gate_says("supported")])})
    good = s.state.deps.connect
    s.state.deps.connect = pl.connect_readonly(tmp_path / "gone.db")
    cid = s.conversation()
    res = check_stream(s.ask(cid, Q))
    assert res["response"]["outcome"] == "source_unreachable" and res["recorded"] is False
    s.state.deps.connect = good
    assert check_stream(s.ask(cid, "Again?"))["response"]["outcome"] == "answered"


def test_server_bug_in_the_worker_ends_with_error_and_frees_the_conversation(db_path, monkeypatch):
    s = Server(db_path, {Q: Script(intent_analysis_demo=[REFER])})
    real = pl.run
    monkeypatch.setattr(pl, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("server bug")))
    cid = s.conversation()
    res = check_stream(s.ask(cid, Q))
    assert "error" in res
    monkeypatch.setattr(pl, "run", real)
    assert check_stream(s.ask(cid, Q))["response"]["outcome"] == "referred_to_investigation"


def test_server_gateway_for_raising_frees_the_conversation(db_path):
    """deps_for runs after begin(): if building the question's gateway fails, the conversation must
    not stay busy (B1)."""
    calls = {"n": 0}

    def gateway_for(q):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("could not build the gateway")
        return Script(intent_analysis_demo=[REFER])
    s = Server(db_path, gateway_for=gateway_for)
    cid = s.conversation()
    try:
        s.ask(cid, Q)
    except RuntimeError:
        pass
    r = s.ask(cid, Q)
    assert r.status_code == 200, f"conversation stuck: {r.status_code} {r.text}"


def test_server_stream_never_read_frees_the_conversation(db_path):
    """B1: a client that leaves before the first event. The generator never starts, so nothing runs
    and nothing is paid; the conversation must still take the next question."""
    import api.routes as routes
    s = Server(db_path, {Q: Script(intent_analysis_demo=[REFER, REFER])})
    cid = s.conversation()
    # what Starlette does when the client is gone before the body is sent: the response is
    # dropped without iterating its body
    captured = {}
    real = routes.StreamingResponse

    class Dropped(real):
        def __init__(self, content, *a, **k):
            captured["gen"] = content
            super().__init__(iter([b""]), *a, **k)
    routes.StreamingResponse = Dropped
    try:
        s.ask(cid, Q)
    finally:
        routes.StreamingResponse = real
    gen = captured["gen"]
    asyncio.run(gen.aclose())   # what garbage collection would do; an unstarted generator runs no finally
    r = s.ask(cid, Q)
    assert r.status_code == 200, f"conversation stuck after a dropped stream: {r.status_code} {r.text}"


def test_server_disconnect_mid_exchange_stops_paying(db_path):
    """S1: a client gone after the first stage. The worker carries on to the next model call, which is
    refused; the exchange is recorded as request_not_completed and the conversation is free."""
    from api.routes import exchange_events
    script = Script(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    storage = MemoryStorage()
    d = make_deps(None, db_path, storage)
    cancelled = threading.Event()
    d = pl.Deps(**{**d.__dict__, "gateway": Cancellable(script, cancelled)})
    done = threading.Event()
    result = {}

    def work(on_stage):
        out = pl.run(Q, [], APPROVER, d, on_stage)
        result["out"] = out
        done.set()
        return {"response": out.response}

    async def client_leaves():
        gone = {"v": False}

        async def is_disconnected():
            return gone["v"]
        agen = exchange_events(work, is_disconnected, cancelled)
        first = await agen.__anext__()   # the first stage event
        assert first.startswith("event: stage")
        gone["v"] = True
        await agen.aclose()              # the client is gone: finally sets cancelled
    asyncio.run(client_leaves())
    assert done.wait(10)
    out = result["out"]
    names = [cfg.prompt_identity.split("@")[0] for _, cfg in script.calls]
    assert out.response["outcome"] == "request_not_completed" and out.recorded, (out.response, names)
    assert "synthesis" not in names and "support_scoring" not in names, names
    assert "cancelled" in storage.get_investigation(out.exchange_id)["failure"]


def test_server_refuses_what_it_should(db_path):
    s = Server(db_path, {Q: Script(intent_analysis_demo=[REFER])})
    cid = s.conversation()
    assert s.client.post("/api/conversations", headers={}).status_code == 401
    assert s.client.post("/api/conversations", headers={"X-Demo-User": "XX"}).status_code == 401
    assert s.client.get(f"/api/conversations/{cid}", headers=PL).status_code == 403
    assert s.client.get("/api/conversations/CV-nope", headers=AP).status_code == 404
    assert s.ask(cid, Q, headers=PL).status_code == 403
    for body in ({"question": "", "request_id": "abcdefgh"}, {"question": "x" * 1001, "request_id": "abcdefgh"},
                 {"question": "ok", "request_id": "short"}, {"question": "ok", "request_id": "bad id!!"},
                 {"question": "ok"}):
        assert s.client.post(f"/api/conversations/{cid}/exchanges", headers=AP, json=body).status_code == 422
    rid = uuid.uuid4().hex
    assert s.ask(cid, Q, request_id=rid).status_code == 200
    assert s.ask(cid, Q, request_id=rid).status_code == 409


def test_server_conversation_update(db_path):
    """UI refresh, phase B [demo extension]: a conversation's title, pin and archive, by its own user only. An
    archived conversation leaves the list; its exchanges stay in the audit record, and the chain holds."""
    s = Server(db_path, {Q: Script(intent_analysis_demo=[REFER])})
    cid = s.conversation()
    eid = check_stream(s.ask(cid, Q))["exchange_id"]
    patch = lambda body, h=AP, c=cid: s.client.patch(f"/api/conversations/{c}", headers=h, json=body)  # noqa: E731
    assert patch({"title": "  Late suppliers  "}).json()["title"] == "Late suppliers"
    assert patch({"pinned": True}).json()["pinned"] is True
    assert patch({"archived": True}, PL).status_code == 403
    assert patch({"archived": True}, c="CV-nope").status_code == 404
    for bad in ({}, {"title": "   "}, {"title": "x" * 201}, {"colour": "red"}, {"pinned": "yes"}, {"pinned": None}):
        assert patch(bad).status_code == 422, bad
    before = s.storage.audit_trail(eid)
    assert patch({"archived": True}).json()["archived"] is True
    assert cid not in [c["conversation_id"] for c in s.client.get("/api/conversations", headers=AP).json()]
    assert s.client.get(f"/api/conversations/{cid}", headers=AP).json()["exchanges"][0]["exchange_id"] == eid
    assert s.storage.audit_trail(eid) == before and s.state.deps.audit.verify_chain(eid) == len(before)


def test_server_archived_listing(db_path):
    """Long list, L5 [demo extension]: ?archived=true lists the asker's archived conversations only; the
    default list is unchanged, another user never sees them, and unarchiving brings one back."""
    s = Server(db_path, {Q: Script(intent_analysis_demo=[REFER])})
    kept, gone = s.conversation(), s.conversation()
    theirs = s.client.post("/api/conversations", headers=PL).json()["conversation_id"]
    for cid, h in ((gone, AP), (theirs, PL)):
        assert s.client.patch(f"/api/conversations/{cid}", headers=h, json={"archived": True}).status_code == 200
    ids = lambda h, q="": [c["conversation_id"] for c in s.client.get(f"/api/conversations{q}", headers=h).json()]  # noqa
    assert ids(AP) == [kept] and ids(AP, "?archived=false") == [kept]
    assert ids(AP, "?archived=true") == [gone]
    assert ids(PL, "?archived=true") == [theirs] and gone not in ids(PL) + ids(PL, "?archived=true")
    assert s.client.get("/api/conversations?archived=maybe", headers=AP).status_code == 422
    back = s.client.patch(f"/api/conversations/{gone}", headers=AP, json={"archived": False})
    assert back.json()["archived"] is False
    assert sorted(ids(AP)) == sorted([kept, gone]) and ids(AP, "?archived=true") == []


def test_server_rating_rules(db_path):
    s = Server(db_path, {Q: Script(intent_analysis_demo=[INTENT], synthesis=[SYN],
                                   support_scoring=[gate_says("supported")]),
                         "Why?": Script(intent_analysis_demo=[REFER]),
                         "Change PO-87006.": Script(intent_analysis_demo=[ACTION])})
    cid = s.conversation()
    ex = check_stream(s.ask(cid, Q))["exchange_id"]
    ref = check_stream(s.ask(cid, "Why?"))["exchange_id"]
    declined = check_stream(s.ask(s.conversation(), "Change PO-87006."))
    assert declined["response"]["outcome"] == "answered" and declined["response"]["readings"] == []
    rate = lambda eid, h=AP, v="up": s.client.post(f"/api/exchanges/{eid}/rating", headers=h, json={"rating": v})  # noqa
    assert rate(ex).status_code == 200
    assert rate(ex, PL).status_code == 403
    assert rate(ref).status_code == 422
    assert rate(declined["exchange_id"]).status_code == 422   # task 8 review: no records, nothing to rate
    assert rate("EX-nope").status_code == 404
    assert rate(ex, v="sideways").status_code == 422


def test_server_one_question_at_a_time_per_conversation(db_path):
    gate_open = threading.Event()

    def slow_intent(prompt):
        gate_open.wait(10)
        return REFER
    s = Server(db_path, {
        Q: Script(intent_analysis_demo=[slow_intent]),
        "Other?": Script(intent_analysis_demo=[REFER, REFER],
                         question_rewriting=[{"question": "Other?", "carried_from_history": False}]),
    })
    cid = s.conversation()
    first: dict = {}
    t = threading.Thread(target=lambda: first.setdefault("r", s.ask(cid, Q)))
    t.start()
    wait_until_busy(s, cid)
    # T7-B1: the second question waits for the conversation instead of a 409, and runs after the first
    second: dict = {}
    t2 = threading.Thread(target=lambda: second.setdefault("r", s.ask(cid, "Other?")))
    t2.start()
    cid2 = s.conversation()
    assert check_stream(s.ask(cid2, "Other?"))["response"]["outcome"] == "referred_to_investigation"
    assert t2.is_alive()                       # still waiting: the first holds the conversation
    gate_open.set()
    t.join(10)
    t2.join(10)
    assert check_stream(first["r"])["response"]["outcome"] == "referred_to_investigation"
    assert check_stream(second["r"])["response"]["outcome"] == "referred_to_investigation"
    assert ("stage", {"stage": "waiting"}) in events(second["r"].text)
    held = s.client.get(f"/api/conversations/{cid}", headers=AP).json()["exchanges"]
    assert [x["posed"] for x in held] == [Q, "Other?"]   # one after the other, never interleaved


def wait_until_busy(s, cid):
    for _ in range(100):
        if s.state.conversation(cid, {"user_id": "AP-01"}).busy:
            return
        threading.Event().wait(0.02)
    raise AssertionError("the conversation never became busy")


def test_server_waits_for_abandoned_exchange_then_refuses_when_the_wait_runs_out(db_path):
    """T7-B1 (c): the wait is bounded. When it runs out the stream ends with an error event carrying
    409, the conversation is untouched, and the first exchange still ends and frees it."""
    gate_open = threading.Event()

    def slow_intent(prompt):
        gate_open.wait(10)
        return REFER
    s = Server(db_path, {Q: Script(intent_analysis_demo=[slow_intent]),
                         "Other?": Script(intent_analysis_demo=[REFER], question_rewriting=[
                             {"question": "Other?", "carried_from_history": False}])}, wait_seconds=0.3)
    cid = s.conversation()
    first: dict = {}
    t = threading.Thread(target=lambda: first.setdefault("r", s.ask(cid, Q)))
    t.start()
    wait_until_busy(s, cid)
    refused = s.ask(cid, "Other?")
    assert check_stream(refused) == {"error": "a question in this conversation is still being answered",
                                     "status": 409}
    assert ("stage", {"stage": "waiting"}) in events(refused.text)
    assert s.state.conversation(cid, {"user_id": "AP-01"}).busy   # still the first one's
    gate_open.set()
    t.join(10)
    assert check_stream(first["r"])["response"]["outcome"] == "referred_to_investigation"
    assert check_stream(s.ask(cid, "Other?"))["response"]["outcome"] == "referred_to_investigation"


def test_raw_exception_from_a_gateway_still_ends_the_exchange(db_path):
    """A gateway that raises something classify() never saw (only a test gateway can): the exchange
    still ends as request_not_completed, recorded. The call itself is not in the trail, since no
    response and no classified failure exists for it."""
    gw = Script(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[RuntimeError("boom")])
    d = make_deps(gw, db_path)
    out = pl.run(Q, [], APPROVER, d)
    assert out.response["outcome"] == "request_not_completed" and out.recorded
