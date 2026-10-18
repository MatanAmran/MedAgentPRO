"""
Tests for the chat server (task 6), on the scripted model. Run:  pytest -q

No test reaches a real model (6.1, R8): the real gateway refuses to build under pytest.
"""

import asyncio
import json
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

import main
from api.routes import exchange_events
from api.state import Cancellable
from demo_data import seed
from demo_data.scripted import ScriptedDemoGateway, load_scripts
from model_client.budget import BudgetConfigError
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import pipeline, prompts
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, MemoryStore

TWO = "Which suppliers were late in weeks 1 to 13?"
WHY = "Why is S-014 late so often?"
NONE = "What did S-040 supply?"
AP, PL = {"X-Demo-User": "AP-01"}, {"X-Demo-User": "PL-02"}


@pytest.fixture
def client(db_path):
    return TestClient(main.create_app("scripted", db_path))


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def conversation(client, headers=AP) -> str:
    r = client.post("/api/conversations", headers=headers)
    assert r.status_code == 201
    return r.json()["conversation_id"]


def ask(client, cid, question, headers=AP, request_id=None):
    return client.post(f"/api/conversations/{cid}/exchanges", headers=headers,
                       json={"question": question, "request_id": request_id or uuid.uuid4().hex})


def result(r) -> dict:
    evs = events(r.text)
    assert evs[-1][0] == "result"
    return evs[-1][1]


# --------------------------------------------------------------------------
# the server
# --------------------------------------------------------------------------

def test_health_says_scripted_and_no_authentication(client):
    h = client.get("/api/health").json()
    assert h == {"mode": "scripted", "model": "scripted-demo", "authentication": "none (demo)", "panel": False}


@pytest.mark.parametrize("headers", [{}, {"X-Demo-User": "XX-99"}])
def test_a_user_outside_the_demo_list_is_refused(client, headers):
    """S2."""
    assert client.post("/api/conversations", headers=headers).status_code == 401


def test_stream_reports_stages_then_the_answer_once(client):
    """S1, decision 8: stage progress only, the response after the gate, once. Cost beside it (T3)."""
    r = ask(client, conversation(client), TWO)
    assert r.headers["content-type"].startswith("text/event-stream")
    evs = events(r.text)
    stages = [d["stage"] for k, d in evs if k == "stage"]
    assert stages == ["intent", "retrieval", "synthesis", "gate", "contract"]
    assert [k for k, _ in evs].count("result") == 1 and evs[-1][0] == "result"
    res = evs[-1][1]
    assert res["response"]["outcome"] == "answered" and res["recorded"]
    assert [v["label"] for v in res["response"]["readings"]] == ["late_by_delivery", "late_by_promise"]
    assert all(v["answer"] for v in res["response"]["readings"])
    assert "cost" in res and res["cost"] is None and "cost" not in res["response"]


def test_scripted_answers_go_through_the_real_checks(client):
    """S5: referral, an empty result, and a question with no script."""
    cid = conversation(client)
    assert result(ask(client, cid, WHY))["response"]["outcome"] == "referred_to_investigation"
    empty = result(ask(client, cid, NONE))["response"]
    assert empty["outcome"] == "answered" and empty["readings"][0]["notice"] == "No records matched."
    other = result(ask(client, cid, "How many parts are there?"))["response"]
    assert other["outcome"] == "request_not_completed"


def test_scripted_output_breaking_a_check_is_rejected_like_a_real_one(db_path):
    """S5: the script replaces the model only. A digit typed outside a reference is still a content
    failure, in the first attempt and in the feedback attempt, and the exchange ends as
    request_not_completed. (A sentence with no reference at all would already break the pattern the
    provider enforces: a format failure, not retried.)"""
    key = " ".join(TWO.casefold().split())
    entry = dict(load_scripts()[key])
    entry["synthesis"] = {"readings": [
        {"label": "late_by_delivery", "sentences": [{"text": "7 suppliers, led by {late_by_delivery.supplier_id#1}."}]},
        {"label": "late_by_promise", "sentences": [{"text": "{late_by_promise.supplier_id#1} too."}]}]}
    gw = ScriptedDemoGateway(TWO, {key: entry})
    storage = MemoryStorage()
    deps = pipeline.Deps(gw, "scripted-demo", Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json"),
                         pipeline.connect_readonly(db_path),
                         AuditRepository(storage, CircuitBreaker("s", MemoryStore()), b"k"),
                         prompts.load(seed.HERE / "intent_analysis.md"))
    out = pipeline.run(TWO, [], {"role": "APPROVER", "user_id": "AP-01"}, deps)
    assert out.response["outcome"] == "request_not_completed"
    assert "outside a reference" in storage.investigations[out.exchange_id]["failure"]
    assert [c[1].prompt_identity.split("@")[0] for c in gw.calls].count("synthesis") == 2


def test_planner_sees_only_their_records(client):
    """NFR 17 through the API: the plan is limited to the records the planner holds."""
    ap = result(ask(client, conversation(client), TWO))["response"]["readings"][0]["checked"]
    pl = result(ask(client, conversation(client, PL), TWO, PL))["response"]["readings"][0]["checked"]
    assert "limited to the records you hold" in pl and "limited to the records you hold" not in ap


def test_a_repeated_request_id_is_refused(client):
    """R8: one exchange per request id, even in another conversation."""
    rid = uuid.uuid4().hex
    assert ask(client, conversation(client), WHY, request_id=rid).status_code == 200
    assert ask(client, conversation(client), WHY, request_id=rid).status_code == 409


def test_another_users_conversation_is_refused(client):
    """S3."""
    cid = conversation(client, AP)
    assert ask(client, cid, WHY, headers=PL).status_code == 403
    assert client.get(f"/api/conversations/{cid}", headers=PL).status_code == 403
    assert client.get("/api/conversations/CV-none", headers=AP).status_code == 404
    assert [c["conversation_id"] for c in client.get("/api/conversations", headers=PL).json()] == []


def test_conversation_keeps_what_was_presented_and_the_history_stays_on_the_server(client):
    cid = conversation(client)
    first = result(ask(client, cid, TWO))
    listed = client.get("/api/conversations", headers=AP).json()
    assert listed[0]["title"] == TWO and listed[0]["exchanges"] == 1
    held = client.get(f"/api/conversations/{cid}", headers=AP).json()["exchanges"]
    assert held[0]["exchange_id"] == first["exchange_id"] and held[0]["response"] == first["response"]


def test_rating(client):
    cid = conversation(client)
    eid = result(ask(client, cid, TWO))["exchange_id"]
    assert client.post(f"/api/exchanges/{eid}/rating", headers=AP, json={"rating": "up"}).status_code == 200
    assert client.post(f"/api/exchanges/{eid}/rating", headers=PL, json={"rating": "up"}).status_code == 403
    assert client.post(f"/api/exchanges/{eid}/rating", headers=AP, json={"rating": "5"}).status_code == 422
    referred = result(ask(client, cid, WHY))["exchange_id"]
    assert client.post(f"/api/exchanges/{referred}/rating", headers=AP, json={"rating": "up"}).status_code == 422
    assert client.post("/api/exchanges/EX-none/rating", headers=AP, json={"rating": "up"}).status_code == 404


def test_the_conversation_shows_the_last_rating_after_a_reload(client):
    """Task 7, Q5: the screen reads the rating from the conversation, so a reload keeps it. A refused
    rating changes nothing."""
    cid = conversation(client)
    eid = result(ask(client, cid, TWO))["exchange_id"]

    def shown():
        return client.get(f"/api/conversations/{cid}", headers=AP).json()["exchanges"][0]["rating"]

    assert shown() is None
    client.post(f"/api/exchanges/{eid}/rating", headers=AP, json={"rating": "up"})
    client.post(f"/api/exchanges/{eid}/rating", headers=AP, json={"rating": "down"})
    assert shown() == "down"
    client.post(f"/api/exchanges/{eid}/rating", headers=PL, json={"rating": "up"})
    assert shown() == "down"


@pytest.mark.parametrize("body", [{"question": "", "request_id": "abcdefgh1"},
                                  {"question": "x" * 1001, "request_id": "abcdefgh2"},
                                  {"question": "q", "request_id": "short"},
                                  {"question": "q", "request_id": "has space in it"}])
def test_invalid_input_is_refused(client, body):
    cid = conversation(client)
    assert client.post(f"/api/conversations/{cid}/exchanges", headers=AP, json=body).status_code == 422


# --------------------------------------------------------------------------
# S1: a client that leaves stops the spending
# --------------------------------------------------------------------------

def test_a_client_that_leaves_stops_the_next_model_call(db_path):
    """The client goes while intent analysis runs; synthesis is never called; the exchange is recorded."""
    cancelled, intent_running, finished = threading.Event(), threading.Event(), threading.Event()
    scripted = ScriptedDemoGateway(TWO, load_scripts())

    class Slow:
        def invoke(self, prompt, config):
            if config.prompt_identity.startswith("intent"):
                intent_running.set()
                assert cancelled.wait(5)      # the client leaves while this call runs
            return scripted.invoke(prompt, config)

    storage = MemoryStorage()
    deps = pipeline.Deps(Cancellable(Slow(), cancelled), "scripted-demo",
                         Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json"),
                         pipeline.connect_readonly(db_path),
                         AuditRepository(storage, CircuitBreaker("s", MemoryStore()), b"k"),
                         prompts.load(seed.HERE / "intent_analysis.md"))
    outcome = {}

    def work(on_stage):
        try:
            outcome["out"] = pipeline.run(TWO, [], {"role": "APPROVER", "user_id": "AP-01"}, deps, on_stage)
            return {}
        finally:
            finished.set()

    async def gone():
        return intent_running.is_set()

    async def consume():
        return [e async for e in exchange_events(work, gone, cancelled)]

    got = asyncio.run(consume())
    assert finished.wait(10)
    assert not any("result" in e for e in got)
    assert [c[1].prompt_identity.split("@")[0] for c in scripted.calls] == ["intent_analysis_demo"]
    out = outcome["out"]
    assert out.response["outcome"] == "request_not_completed" and out.recorded
    assert storage.investigations[out.exchange_id]["failure"].startswith("cancelled:")


# --------------------------------------------------------------------------
# R8 and S4: the real model's guards
# --------------------------------------------------------------------------

def test_reload_with_the_real_model_is_refused(capsys):
    assert main.main(["--real", "--reload"]) == 2
    assert "refused" in capsys.readouterr().out


def test_scripted_delay_is_refused_with_the_real_model(capsys):
    """The delay is a test aid for the scripted model (task 7, end-to-end tests); it never slows the
    real one, where waiting holds a budget reservation."""
    assert main.main(["--real", "--scripted-delay", "1"]) == 2
    assert "scripted model only" in capsys.readouterr().out


def test_scripted_delay_waits_before_each_output(db_path, monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr("demo_data.scripted.time.sleep", waits.append)
    app = main.create_app("scripted", db_path, scripted_delay=0.5)
    with TestClient(app) as c:
        cid = c.post("/api/conversations", headers=AP).json()["conversation_id"]
        out = result(c.post(f"/api/conversations/{cid}/exchanges", headers=AP,
                            json={"question": WHY, "request_id": uuid.uuid4().hex}))
    assert out["response"]["outcome"] == "referred_to_investigation" and waits == [0.5]


def test_the_real_model_needs_its_chain_key_and_never_builds_under_pytest(db_path, monkeypatch):
    monkeypatch.delenv("MEDAGENTPRO_CHAIN_KEY", raising=False)
    monkeypatch.setattr(main, "ENV_FILE", db_path.parent / "no.env")
    with pytest.raises(BudgetConfigError, match="MEDAGENTPRO_CHAIN_KEY"):
        main.create_app("real", db_path)
    monkeypatch.setenv("MEDAGENTPRO_CHAIN_KEY", "test-only")
    with pytest.raises(BudgetConfigError, match="never built under pytest"):
        main.create_app("real", db_path)
