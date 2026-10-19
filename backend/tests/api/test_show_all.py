"""
"Show all records" (Phase B task 5, chat 19.10) [demo extension]: for a reading that was cut, the server
compiles again the plan it kept when the exchange ran, with the asking user's entitlement and a cap of
SHOW_ALL_LIMIT, and returns the rows. No model, no client-sent query, nothing into the conversation's
history (NFR 21). The scripted model throughout.
"""

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from api.routes import router
from api.state import AppState
from query_assistance import compiler as c
from query_assistance import pipeline

Q1 = "Which suppliers were late in weeks 1 to 13?"     # cut: 10 of 28 by the scripted plan's LIMIT
Q14 = "Which of my lines were delivered late in September?"   # all rows shown
AP, PL = {"X-Demo-User": "AP-01"}, {"X-Demo-User": "PL-02"}


@pytest.fixture
def state(db_path):
    deps, gateway_for = main.build_deps("scripted", db_path)
    return AppState(deps, "scripted", gateway_for)


@pytest.fixture
def client(state):
    app = FastAPI()
    app.include_router(router(state))
    return TestClient(app)


def ask(client, question, headers=AP):
    cid = client.post("/api/conversations", headers=headers).json()["conversation_id"]
    r = client.post(f"/api/conversations/{cid}/exchanges", headers=headers,
                    json={"question": question, "request_id": uuid.uuid4().hex})
    last = r.text.strip().split("\n\n")[-1]
    return cid, json.loads(last.split("data: ", 1)[1])


def records(client, exchange_id, label, headers=AP):
    return client.get(f"/api/exchanges/{exchange_id}/readings/{label}/records", headers=headers)


def test_a_cut_reading_returns_every_record_the_plan_matches(client, db_path):
    _, res = ask(client, Q1)
    shown = res["response"]["readings"][0]
    assert shown["row_count"] == 10 and shown["total"] == 28
    r = records(client, res["exchange_id"], shown["label"])
    assert r.status_code == 200
    every = r.json()
    want = sqlite3.connect(db_path).execute(
        "SELECT COUNT(DISTINCT supplier_id) FROM commitments WHERE week BETWEEN 1 AND 13 "
        "AND delivered_at > due_date").fetchone()[0]
    assert every["row_count"] == every["total"] == want == 28
    assert "notice" not in every and every["checked"] == shown["checked"]
    assert every["rows"][:10] == shown["rows"]       # the rows shown come first: references still point right
    assert "answer" not in every


def test_nothing_reaches_the_history_or_the_items(client, state):
    """NFR 21: what rewriting and NFR 27 read next (the history) and what the screen reloads (the items)
    are as they were before all the records were shown."""
    cid, res = ask(client, Q1)
    before = client.get(f"/api/conversations/{cid}", headers=AP).json()
    history = [vars(ex).copy() for ex in state._conversations[cid].history]
    assert records(client, res["exchange_id"], res["response"]["readings"][0]["label"]).json()["row_count"] == 28
    assert client.get(f"/api/conversations/{cid}", headers=AP).json() == before
    assert [vars(ex) for ex in state._conversations[cid].history] == history


def test_only_the_asker_may_ask_for_all_records(client):
    _, res = ask(client, Q1)
    assert records(client, res["exchange_id"], res["response"]["readings"][0]["label"], PL).status_code == 403


def test_an_unknown_exchange_or_reading_is_not_found(client):
    _, res = ask(client, Q1)
    assert records(client, "EX-000000000000", "late_by_delivery").status_code == 404
    assert records(client, res["exchange_id"], "no_such_label").status_code == 404
    assert records(client, res["exchange_id"], "late_by_delivery", {}).status_code == 401


def test_the_cap_is_show_all_limit_and_the_plans_limit_is_left_out(db_path):
    cat = c.Catalog.load(main.seed.HERE / "schema.json", main.seed.HERE / "relations.json")
    plan = {"SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "commitment_id"}],
            "FROM": "commitments", "LIMIT": 5}
    reading = c.compile_all("all", plan, {"role": "APPROVER", "user_id": "AP-01"}, cat)
    assert reading.params[-1] == c.SHOW_ALL_LIMIT == 1000
    res = c.execute(reading, sqlite3.connect(db_path))
    assert len(res.rows) == 1000 and res.total > 1000 and res.truncated


def test_entitlement_is_injected_afresh_for_the_user_asking(db_path):
    """On a catalog where a planner reads only their own lines (NFR 17): the planner's condition is in."""
    fixtures = Path(__file__).parents[1] / "fixtures"
    cat = c.Catalog.load(fixtures / "restricted_schema.json", main.seed.HERE / "relations.json")
    plan = {"SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "commitment_id"}], "FROM": "commitments"}
    reading = c.compile_all("all", plan, {"role": "PLANNER", "user_id": "PL-02"}, cat)
    assert '"commitments"."planner_id" = ?' in reading.sql and "PL-02" in reading.params


def test_a_source_that_cannot_be_reached_is_503(client, monkeypatch):
    _, res = ask(client, Q1)

    def unreachable(*args, **kwargs):
        raise pipeline._Unreachable("unable to open database file")
    monkeypatch.setattr(pipeline, "_retrieve", unreachable)
    r = records(client, res["exchange_id"], res["response"]["readings"][0]["label"])
    assert r.status_code == 503 and "could not be reached" in r.json()["detail"]


def test_a_reading_shown_whole_returns_the_same_rows(client):
    """Q14 as PL-02: nothing was cut; all records are the rows already shown."""
    _, res = ask(client, Q14, PL)
    for shown in res["response"]["readings"]:
        every = records(client, res["exchange_id"], shown["label"], PL).json()
        assert every["rows"] == shown["rows"] and every["total"] == shown["total"]
