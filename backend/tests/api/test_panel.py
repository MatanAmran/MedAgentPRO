"""
"How this was answered" (task 10, chat 15:10 and the plan's review): the trace beside each answer. Metadata
only, from a whitelist; never the question, the model's text, a record value, a rejection message or the key.
On the scripted model and on the real path with the fake provider (no key, no network).
"""

import json
import re
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import main
from api import trace as panel_trace
from demo_data import seed
from model_client.client import ScriptedModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import contract
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from query_assistance.call_trace import CallTrace
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, MemoryStore
from tools.fake_real_server import CONFIG, build_app

AP = {"X-Demo-User": "AP-01"}
TWO = "Which suppliers were late in weeks 1 to 13?"
WHY = "Why is S-014 late so often?"
NONE = "What did S-040 supply?"
ACTION = "Update the promised date of PO-87213 to 20 October."
FAKE_KEY = CONFIG["ANTHROPIC_API_KEY"]
CAT = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")


def ask(client, question, cid=None):
    cid = cid or client.post("/api/conversations", headers=AP).json()["conversation_id"]
    r = client.post(f"/api/conversations/{cid}/exchanges", headers=AP,
                    json={"question": question, "request_id": uuid.uuid4().hex})
    return json.loads(r.text.strip().split("\n\n")[-1].split("data: ", 1)[1]), cid


@pytest.fixture
def scripted(db_path):
    return TestClient(main.create_app("scripted", db_path, panel=True))


def fake(db_path, tmp_path, monkeypatch, **override):
    monkeypatch.setenv("MEDAGENTPRO_CHAIN_KEY", "test-chain-key")
    # the demo's per-exchange cap; build_app's own (0.137) stops question 3 on purpose, for another test
    app, _ = build_app(db_path, tmp_path, panel=True, **{"MODEL_MAX_USD_PER_EXCHANGE": "0.25", **override})
    return TestClient(app)


def strings(node) -> list[str]:
    if isinstance(node, dict):
        return [s for v in node.values() for s in strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in strings(v)]
    return [node] if isinstance(node, str) else []


# --------------------------------------------------------------------------
# off by default, as in the book
# --------------------------------------------------------------------------

def test_without_panel_there_is_no_trace(db_path):
    client = TestClient(main.create_app("scripted", db_path))
    assert client.get("/api/health").json()["panel"] is False
    out, cid = ask(client, TWO)
    assert "trace" not in out
    assert "trace" not in client.get(f"/api/conversations/{cid}", headers=AP).json()["exchanges"][0]


def test_panel_and_reload_are_refused_together(capsys):
    assert main.main(["--panel", "--reload"]) == 2
    assert "--panel" in capsys.readouterr().out


# --------------------------------------------------------------------------
# what a row holds
# --------------------------------------------------------------------------

def test_a_row_holds_the_whitelisted_fields_only(scripted):
    out, cid = ask(scripted, TWO)
    t = out["trace"]
    assert scripted.get("/api/health").json()["panel"] is True
    assert [c["stage"] for c in t["calls"]] == ["intent_analysis", "synthesis", "support_gate"]
    assert all(set(c) == set(panel_trace.FIELDS) for c in t["calls"])
    assert set(t) == {"calls", "total", "unpriced_usd", "not_sent", "note"}
    assert scripted.get(f"/api/conversations/{cid}", headers=AP).json()["exchanges"][0]["trace"] == t


def test_the_gate_row_counts_its_claims(scripted):
    gate = ask(scripted, TWO)[0]["trace"]["calls"][-1]
    assert gate["stage"] == "support_gate" and gate["claims"] >= 1
    assert all(c["claims"] is None for c in ask(scripted, TWO)[0]["trace"]["calls"][:-1])


def test_no_question_model_text_record_value_or_key_in_the_trace(db_path, tmp_path, monkeypatch):
    client = fake(db_path, tmp_path, monkeypatch)
    out, _ = ask(client, TWO)
    text = json.dumps(out["trace"])
    shown = {str(v) for r in out["response"]["readings"] for row in r["rows"] for v in row.values()}
    shown |= {r["answer"] for r in out["response"]["readings"] if r.get("answer")}
    for s in strings(out["trace"]):
        assert s not in shown and TWO not in s
    assert FAKE_KEY not in text and "sk-ant" not in text and "Authorization" not in text
    assert not any(re.fullmatch(r"S-\d{3}", s) for s in strings(out["trace"]))


def test_a_rejection_shows_its_check_never_its_message(db_path):
    """A gate output that quotes a sentence it was not given is rejected with a message holding the model's
    words; the feedback attempt passes. The trace names the check, and holds none of those words."""
    secret = "WORDS THE MODEL WROTE"
    intent = {"disposition": "retrieve", "readings": [{"label": "late", "plan": {
        "SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
                   {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}],
        "FROM": "commitments",
        "WHERE": {"AND": [{"BETWEEN": {"col": "commitments.week", "from": {"value": 1}, "to": {"value": 13}}},
                          {"EQ": [{"metric": "late_by_delivery"}, {"value": True}]}]},
        "GROUP_BY": ["supplier_id"], "ORDER_BY": [{"by": "late_count", "dir": "DESC"}], "LIMIT": 5}}]}
    syn = {"readings": [{"label": "late", "sentences": [
        {"text": "{late.supplier_id#1} had the most late lines, {late.late_count#1}."}]}]}

    def gate(bad):
        def answer(prompt):
            body = prompt.split("Answer:\n")[-1].split("\nRecords:")[0]
            text = re.findall(r"^\d+\. \[\w+\] (.*)$", body, re.M)[0]
            return {"claims": [{"sentence": 1, "text": secret if bad else text, "verdict": "supported",
                                "evidence": ["late.supplier_id"]}]}
        return answer

    outputs = {"intent_analysis_demo": [intent], "synthesis": [syn], "support_scoring": [gate(True), gate(False)]}

    def answer(prompt, cfg):
        out = outputs[cfg.prompt_identity.split("@")[0]].pop(0)
        return out(prompt) if callable(out) else out

    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"k")
    deps = pl.Deps(ScriptedModelGateway(answer), "demo", CAT, lambda: sqlite3.connect(db_path), audit,
                   p.load(seed.HERE / "intent_analysis.md"))
    calls: list[CallTrace] = []
    outcome = pl.run(TWO, [], {"role": "APPROVER", "user_id": "AP-01"}, deps, on_call=calls.append)
    t = panel_trace.build(calls, outcome.response, outcome.cost)
    gates = [c for c in t["calls"] if c["stage"] == "support_gate"]
    assert [(g["status"], g["feedback_attempt"], g["check"]) for g in gates] == [
        ("rejected", False, "GateOutputError"), ("accepted", True, None)]
    assert secret not in json.dumps(t)
    assert any(secret in (e.get("rejection_reason") or "") for e in audit.storage.audit_trail(outcome.exchange_id))


# --------------------------------------------------------------------------
# cost: scripted 0, real from the budget, unknown is None and never 0
# --------------------------------------------------------------------------

def test_scripted_calls_cost_zero_not_unknown(scripted):
    t = ask(scripted, TWO)[0]["trace"]
    assert [c["cost_usd"] for c in t["calls"]] == [0.0, 0.0, 0.0]
    assert t["total"]["usd"] == 0.0 and t["unpriced_usd"] is None


def test_an_unpriced_call_is_unknown_not_zero():
    row = CallTrace("synthesis", "synthesis@10", "m", 1, False, "accepted", "stop", 10, 5, 50, 0, None, None,
                    2000, 1.0, None, cost_usd=None)
    priced = CallTrace("support_gate", "support_scoring@5", "m", 1, False, "accepted", "stop", 10, 5, 50, 0, 1,
                       None, 3000, 1.0, None, cost_usd=0.0)
    resp = {"outcome": "answered", "readings": [], "gaps": []}
    t = panel_trace.build([row, priced], resp, None)
    assert [c["cost_usd"] for c in t["calls"]] == [None, 0.0] and t["total"]["usd"] is None


def test_real_path_rows_cost_what_the_exchange_cost(db_path, tmp_path, monkeypatch):
    client = fake(db_path, tmp_path, monkeypatch, MODEL_MAX_OUTPUT_TOKENS_INTENT="3000",
                  MODEL_MAX_OUTPUT_TOKENS_GATE="2500")
    out, _ = ask(client, TWO)
    t = out["trace"]
    assert all(c["cost_usd"] and c["cost_usd"] > 0 for c in t["calls"])
    assert sum(c["cost_usd"] for c in t["calls"]) == pytest.approx(out["cost"]["exchange_usd"], abs=2e-6)
    assert t["total"]["usd"] == out["cost"]["exchange_usd"] and t["unpriced_usd"] is None
    assert {c["stage"]: c["max_tokens"] for c in t["calls"]} == {
        "intent_analysis": 3000, "synthesis": 2000, "support_gate": 2500}
    assert t["total"]["input_tokens"] == sum(c["input_tokens"] for c in t["calls"])


def test_a_charge_with_no_priced_row_is_shown_as_unpriced():
    """After a timeout the worst case stays charged (R5): the exchange's cost exceeds the rows'."""
    row = CallTrace("intent_analysis", "intent_analysis_demo@8", "m", 1, False, "failed", None, None, None, None,
                    None, None, None, 3000, 1.0, "timeout")
    t = panel_trace.build([row], contract.request_not_completed(), {"exchange_usd": 0.04, "remaining_usd": 1})
    assert t["total"]["usd"] == 0.04 and t["unpriced_usd"] == 0.04


# --------------------------------------------------------------------------
# no answer drafted, or nothing sent
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question, note", [(WHY, "plan_decided"), (ACTION, "plan_decided"), (NONE, "no_records"),
                                            (TWO, None)])
def test_a_note_says_why_no_answer_was_drafted(scripted, question, note):
    t = ask(scripted, question)[0]["trace"]
    assert t["note"] == note and t["not_sent"] is None
    if note:
        assert [c["stage"] for c in t["calls"]] == ["intent_analysis"]


def test_the_note_is_keyed_by_outcome_not_by_question():
    one = [CallTrace("intent_analysis", "intent_analysis_demo@8", "m", 1, False, "accepted", "stop", 1, 1, 0, 1,
                     None, None, 3000, 1.0, None, cost_usd=0.0)]
    assert panel_trace.build(one, contract.needs_clarification(), None)["note"] == "plan_decided"
    assert panel_trace.build(one, contract.request_not_completed(), None)["note"] is None


def test_a_rate_refusal_is_not_sent(db_path, tmp_path, monkeypatch):
    client = fake(db_path, tmp_path, monkeypatch, MODEL_MAX_EXCHANGES_PER_MINUTE="1")
    first, cid = ask(client, WHY)
    second, _ = ask(client, WHY, cid)
    assert first["trace"]["not_sent"] is None
    assert second["trace"] == {**second["trace"], "calls": [], "not_sent": "rate_limit", "note": None}
    assert contract.is_rate_limited(second["response"]["notice"]) is not None


@pytest.mark.parametrize("override", [{"MODEL_BUDGET_USD": "0.001"}, {"MODEL_MAX_USD_PER_EXCHANGE": "0.001"}])
def test_a_budget_refusal_before_any_call_is_not_sent(db_path, tmp_path, monkeypatch, override):
    t = ask(fake(db_path, tmp_path, monkeypatch, **override), TWO)[0]["trace"]
    assert t["calls"] == [] and t["not_sent"] == "budget"


def test_contract_reads_back_its_own_notices():
    assert contract.is_rate_limited(contract.rate_limited_notice(42)) == 42
    assert contract.is_rate_limited(contract.NOT_COMPLETED) is None and contract.is_rate_limited(None) is None
    assert contract.is_budget_refused(contract.BUDGET_USED_UP) and contract.is_budget_refused(contract.EXCHANGE_CAP)
    assert not contract.is_budget_refused(contract.NOT_COMPLETED)


def test_collecting_traces_never_raises_into_the_exchange(scripted):
    """The route collects with list.append, which cannot raise; the exchange ends as it would without it."""
    out = ask(scripted, TWO)[0]
    assert out["response"]["outcome"] == "answered" and out["recorded"] is True
