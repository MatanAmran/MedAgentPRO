"""
The real-model path through the server, with a fake provider (task 9, review addition 2): the budget,
the LiteLLM gateway and the cost beside the answer, as `python -m main --real` runs them, with only
LiteLLM's completion() replaced. No key is read or sent; the ledger is in a temporary directory.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from model_client.budget import SPEND_FILE
from query_assistance import contract
from tools.fake_real_server import CONFIG, build_app

AP = {"X-Demo-User": "AP-01"}
SMALL = "Which suppliers were late in weeks 1 to 13?"
LARGE = "Which purchase orders were delivered late in September?"   # 30 rows: the largest records


@pytest.fixture
def real(db_path, tmp_path):
    app, provider = build_app(db_path, tmp_path)
    with TestClient(app) as c:
        yield c, provider, tmp_path


def ask(c, question):
    cid = c.post("/api/conversations", headers=AP).json()["conversation_id"]
    r = c.post(f"/api/conversations/{cid}/exchanges", headers=AP,
               json={"question": question, "request_id": uuid.uuid4().hex})
    return json.loads(r.text.strip().split("\n\n")[-1].split("data: ", 1)[1])


def test_health_says_real_with_the_budget_left_and_lists_no_examples(real):
    c, _, _ = real
    h = c.get("/api/health").json()
    assert h["mode"] == "real" and h["model"] == CONFIG["MODEL_ID"]
    assert h["budget_remaining_usd"] == float(CONFIG["MODEL_BUDGET_USD"])
    assert c.get("/api/examples").json() == []


def test_an_answer_carries_its_cost_beside_it_and_the_budget_goes_down(real):
    c, provider, tmp = real
    out = ask(c, SMALL)
    assert out["response"]["outcome"] == "answered" and "cost" not in out["response"]   # T3
    assert 0 < out["cost"]["exchange_usd"] < float(CONFIG["MODEL_MAX_USD_PER_EXCHANGE"])
    budget = float(CONFIG["MODEL_BUDGET_USD"])
    assert out["cost"]["remaining_usd"] == pytest.approx(budget - out["cost"]["exchange_usd"])
    assert c.get("/api/health").json()["budget_remaining_usd"] == pytest.approx(out["cost"]["remaining_usd"])
    assert {r["model"] for r in provider.requests} == {"anthropic/claude-sonnet-5-5"}
    assert (tmp / "spend.json").exists()


def test_the_exchange_cap_stops_the_largest_question_before_synthesis(real):
    c, provider, _ = real
    out = ask(c, LARGE)
    assert out["response"]["outcome"] == "request_not_completed"
    assert out["response"]["notice"] == contract.EXCHANGE_CAP
    assert [r["stage"] for r in provider.requests] == ["intent_analysis_demo"]   # nothing after intent


def test_the_fake_run_never_touches_the_demo_spend_file_or_the_key(db_path, tmp_path, monkeypatch):
    before = SPEND_FILE.read_bytes() if SPEND_FILE.exists() else None
    monkeypatch.delenv("MEDAGENTPRO_CHAIN_KEY", raising=False)
    app, provider = build_app(db_path, tmp_path)
    with TestClient(app) as c:
        ask(c, SMALL)
    assert (SPEND_FILE.read_bytes() if SPEND_FILE.exists() else None) == before
    import os
    assert "MEDAGENTPRO_CHAIN_KEY" not in os.environ   # set only while the app was built
