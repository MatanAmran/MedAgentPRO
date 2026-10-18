"""
Paths generated from the branch list itself (branches.py): every model failure kind, every gap kind,
every budget scope. A new kind appears here as a new test case without anyone adding it; if the test
does not know how to produce it, the case fails and says what to add.
"""

from __future__ import annotations

import threading

import branches
import pytest
from test_paths import (  # noqa: F401
    AP,
    APPROVER,
    ENV,
    INTENT,
    LATE,
    SYN,
    Q,
    Raw,
    Script,
    Server,
    budgeted,
    check_invariants,
    check_stream,
    gate_says,
    happy,
    make_deps,
)

from api.state import Cancellable
from model_client.client import BudgetExceeded, ModelCallError
from query_assistance import contract
from query_assistance import pipeline as pl

# How to make a model call fail with each kind. A kind missing here fails its case below.
PRODUCE_FAILURE = {
    "client_error": lambda: ModelCallError("client_error", "400"),
    "timeout": lambda: ModelCallError("timeout", "timed out", outage=True),
    "connection": lambda: ModelCallError("connection", "refused", reached_provider=False, outage=True),
    "rate_limited": lambda: ModelCallError("rate_limited", "429", outage=True),
    "server_error": lambda: ModelCallError("server_error", "529 overloaded", outage=True),
    "unexpected": lambda: ModelCallError("unexpected", "SomethingNew: ?"),
    "refusal": lambda: Raw("I can't help with that.", "content_filter"),
    "truncated": lambda: Raw('{"disposition": "retr', "length"),
    "empty": lambda: Raw("   "),
    "format": lambda: Raw("this is not json"),
}

# A valid entity for each gap kind (schema.json decides: some must exist, some must not).
GAP_ENTITY = {
    "no_such_column": "commitments",
    "no_such_table": "shipments",
    "value_not_present": "commitments.supplier_id",
    "source_outside_system": "shipments",
    "unspecified": None,
}


@pytest.mark.parametrize("kind", sorted(branches.failures()))
def test_every_model_failure_kind_ends_the_exchange_once(db_path, kind):
    """Each kind ends the stage with no further attempt: one call, request_not_completed, recorded."""
    assert kind in PRODUCE_FAILURE, f"new failure kind {kind!r}: add how to produce it to PRODUCE_FAILURE"
    gw = Script(intent_analysis_demo=[PRODUCE_FAILURE[kind]()])
    d = make_deps(gw, db_path)
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, gw, d, Q, outcome="request_not_completed", recorded=True, calls=1)
    assert kind in d.audit.storage.get_investigation(out.exchange_id)["failure"] or kind in (
        "client_error", "timeout", "connection", "rate_limited", "server_error", "unexpected")
    # D9: where the provider did not answer, the notice says so; every other failure keeps the general one
    no_response = kind in ("timeout", "connection", "server_error")
    assert out.response["notice"] == (contract.MODEL_UNAVAILABLE if no_response else contract.NOT_COMPLETED)


@pytest.mark.parametrize("kind", sorted(branches.failures()))
def test_every_model_failure_kind_at_the_gate_after_paying_for_synthesis(db_path, kind):
    """The same failure later in the path: what was already called stays in the trail."""
    gw = Script(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[PRODUCE_FAILURE[kind]()])
    d = make_deps(gw, db_path)
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, gw, d, Q, outcome="request_not_completed", recorded=True, calls=3)


@pytest.mark.parametrize("kind", sorted(branches.gap_kinds()))
def test_every_gap_kind_is_rendered_without_a_cause(db_path, kind):
    assert kind in GAP_ENTITY, f"new gap kind {kind!r}: add a valid entity to GAP_ENTITY"
    needs = {"kind": kind} if GAP_ENTITY[kind] is None else {"kind": kind, "entity": GAP_ENTITY[kind]}
    intent = {"disposition": "retrieve", "readings": [LATE],
              "unresolved": [{"asked": "delivered late", "needs": needs}]}
    gw = Script(intent_analysis_demo=[intent], synthesis=[SYN], support_scoring=[gate_says("supported")])
    d = make_deps(gw, db_path)
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, gw, d, Q, outcome="answered", recorded=True, calls=3)
    gap, = out.response["gaps"]
    assert gap["missing"]["kind"] == kind and gap["text"]
    assert "because" not in gap["text"].lower()


def test_budget_scope_provider_refusal_is_not_retried(db_path, ledger):
    """The provider refused for credit or a spend limit (a money refusal): budget used up, no retry."""
    inner = Script(intent_analysis_demo=[BudgetExceeded("credit too low", scope="provider")])
    d = make_deps(Cancellable(budgeted(inner, ledger), threading.Event()), db_path)
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, inner, d, Q, outcome="request_not_completed", recorded=True, calls=1,
                     notice=contract.BUDGET_USED_UP)
    assert ledger.total == 0


def test_budget_scope_model_mismatch_sends_nothing(db_path, ledger):
    """A model other than the one priced in .env is never called. G2: the budget is not used up, so the
    notice is the plain one, not the budget's."""
    inner = Script()
    gw = Cancellable(budgeted(inner, ledger, MODEL_ID="another-model"), threading.Event())
    d = make_deps(gw, db_path)
    out = pl.run(Q, [], APPROVER, d)
    check_invariants(out, inner, d, Q, outcome="request_not_completed", recorded=True, calls=0,
                     notice=contract.NOT_COMPLETED)


def test_server_listing_and_health(db_path):
    s = Server(db_path, {Q: Script(intent_analysis_demo=[{"disposition": "refer_to_investigation"}])})
    h = s.client.get("/api/health").json()
    assert h["mode"] == "scripted" and h["authentication"] == "none (demo)"
    cid = s.conversation()
    check_stream(s.ask(cid, Q))
    listed = s.client.get("/api/conversations", headers=AP).json()
    assert [c["conversation_id"] for c in listed] == [cid] and listed[0]["title"] == Q
    assert s.client.get("/api/conversations", headers={"X-Demo-User": "PL-01"}).json() == []


def test_server_examples_on_the_scripted_model_only(db_path):
    """Task 8, A4: the empty screen offers the scripted questions in the demo's order; the real model,
    whose answers are not scripted, offers none."""
    s = Server(db_path)
    listed = s.client.get("/api/examples").json()
    assert [e["question"] for e in listed][:2] == ["Which suppliers were late in weeks 1 to 13?",
                                                   "And in weeks 14 to 26?"]
    assert listed[1]["follows"] == listed[0]["question"] and all(e["outcome"] for e in listed)
    s.state.mode = "real"
    assert s.client.get("/api/examples").json() == []
