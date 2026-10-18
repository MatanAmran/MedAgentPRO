"""
AVG beside COUNT, SUM, MIN and MAX (chat, 18.10, 13:05, after C4 of the held-out set): computed in the query,
rounded to two decimals there, named in Examined and in the column header, and carried into the answer as a
reference like any other field. On the demo store, so each value is checked against plain SQL.
"""

import sqlite3

import pytest

from demo_data import seed
from model_client.client import ScriptedModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import compiler as c
from query_assistance import contract
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from shared.circuit_breaker import CircuitBreaker, MemoryStore

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}
Q = "What is the average ordered quantity for S-038?"


def plan(func="AVG", col="commitments.quantity", alias="avg_qty"):
    return {"SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
                       {"expr": {"func": func, "arg": {"col": col}}, "as": alias}],
            "FROM": "commitments",
            "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-038"}]},
            "GROUP_BY": ["supplier_id"]}


def intent(pl_):
    return {"disposition": "retrieve", "readings": [{"label": "avg", "plan": pl_}]}


def compile_(pl_, question=Q):
    return c.compile_intent(intent(pl_), question, APPROVER, CAT, c.intent_validator())


@pytest.fixture(scope="module")
def db(db_path):
    conn = sqlite3.connect(db_path)
    yield conn
    conn.close()


def test_avg_is_valid_in_the_schema():
    assert not list(c.intent_validator().iter_errors(intent(plan())))


@pytest.mark.parametrize("func, sql", [
    ("AVG", "ROUND(AVG(quantity), 2)"), ("MIN", "MIN(quantity)"), ("MAX", "MAX(quantity)"), ("SUM", "SUM(quantity)")])
def test_each_aggregate_matches_plain_sql(db, func, sql):
    reading = compile_(plan(func)).readings[0]
    got = c.execute(reading, db).rows[0]["avg_qty"]
    want = db.execute(f"SELECT {sql} FROM commitments WHERE supplier_id = 'S-038'").fetchone()[0]  # nosec B608
    assert got == want


def test_avg_is_rounded_to_two_decimals_in_the_query(db):
    reading = compile_(plan()).readings[0]
    assert "ROUND(AVG(" in reading.sql and f", {c.AVG_DECIMALS})" in reading.sql
    raw = db.execute("SELECT AVG(quantity) FROM commitments WHERE supplier_id = 'S-038'").fetchone()[0]
    got = c.execute(reading, db).rows[0]["avg_qty"]
    assert got == round(raw, 2) and got != raw   # the demo store's average has more than two decimals


def test_avg_of_a_date_column_is_refused():
    with pytest.raises(c.CompileError) as e:
        compile_(plan(col="commitments.delivered_at"))
    assert e.value.code == "type_mismatch" and "AVG" in str(e.value)


def test_examined_and_the_header_name_the_average():
    pl_ = plan()
    assert "computed the average ordered quantity" in contract.render_checked(pl_, CAT, "APPROVER")
    assert contract.headers(pl_, CAT)["avg_qty"] == "average ordered quantity"


def test_an_average_reaches_the_answer_as_a_reference(db_path):
    """Path: intent with AVG, synthesis references the field, the gate supports it, the value is the code's."""
    syn = {"readings": [{"label": "avg", "sentences": [
        {"text": "{avg.supplier_id#1} had an average ordered quantity of {avg.avg_qty#1}."}]}]}

    def gate(prompt):
        body = prompt.split("Answer:\n")[-1].split("\nRecords:")[0]
        text = body.split(". ", 1)[1].split("] ", 1)[1].strip()
        return {"claims": [{"sentence": 1, "text": text, "verdict": "supported", "evidence": ["avg.avg_qty"]}]}

    outputs = {"intent_analysis_demo": [intent(plan())], "synthesis": [syn], "support_scoring": [gate]}

    def answer(prompt, cfg):
        out = outputs[cfg.prompt_identity.split("@")[0]].pop(0)
        return out(prompt) if callable(out) else out

    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pl.Deps(ScriptedModelGateway(answer), "demo", CAT, lambda: sqlite3.connect(db_path), audit,
                   p.load(seed.HERE / "intent_analysis.md"))
    out = pl.run(Q, [], APPROVER, deps)
    assert out.response["outcome"] == "answered", out.response
    view = out.response["readings"][0]
    want = sqlite3.connect(db_path).execute(
        "SELECT ROUND(AVG(quantity), 2) FROM commitments WHERE supplier_id = 'S-038'").fetchone()[0]
    assert view["rows"][0]["avg_qty"] == want
    assert view["answer"] == f"S-038 had an average ordered quantity of {want:.2f}".rstrip("0").rstrip(".") + "."
