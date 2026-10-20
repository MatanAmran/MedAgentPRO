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


def intent(pl_, asked=None):
    """asked is given for a check of the schema alone; compiled, the test fixture gives the whole question."""
    reading = {"label": "avg", "plan": pl_, **({"asked": asked} if asked else {})}
    return {"disposition": "retrieve", "readings": [reading]}


def compile_(pl_, question=Q):
    return c.compile_intent(intent(pl_), question, APPROVER, CAT, c.intent_validator())


@pytest.fixture(scope="module")
def db(db_path):
    conn = sqlite3.connect(db_path)
    yield conn
    conn.close()


def test_avg_is_valid_in_the_schema():
    assert not list(c.intent_validator().iter_errors(intent(plan(), "q")))


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
                   p.load(seed.HERE / "intent_analysis.md"), coverage=False)
    out = pl.run(Q, [], APPROVER, deps)
    assert out.response["outcome"] == "answered", out.response
    view = out.response["readings"][0]
    want = sqlite3.connect(db_path).execute(
        "SELECT ROUND(AVG(quantity), 2) FROM commitments WHERE supplier_id = 'S-038'").fetchone()[0]
    assert view["rows"][0]["avg_qty"] == want
    assert view["answer"] == f"S-038 had an average ordered quantity of {want:.2f}".rstrip("0").rstrip(".") + "."


# --------------------------------------------------------------------------
# over a numeric metric (chat, 18.10, 13:15; C4 of the held-out set)
# --------------------------------------------------------------------------

DAYS = "julianday(delivered_at) - julianday(due_date)"
QM = "Average days late on S-038 lines?"   # not C4 of the held-out set, which never becomes a fixture


def metric_plan(func="AVG", metric="days_late", alias="days"):
    return {"SELECT": [{"expr": {"func": func, "arg": {"metric": metric}}, "as": alias}],
            "FROM": "commitments",
            "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-038"}]}}


@pytest.mark.parametrize("func, sql", [
    ("AVG", f"ROUND(AVG({DAYS}), 2)"), ("MIN", f"MIN({DAYS})"), ("MAX", f"MAX({DAYS})"), ("SUM", f"SUM({DAYS})")])
def test_each_aggregate_over_a_metric_matches_plain_sql(db, func, sql):
    reading = compile_(metric_plan(func), question=QM).readings[0]
    got = c.execute(reading, db).rows[0]["days"]
    want = db.execute(f"SELECT {sql} FROM commitments WHERE supplier_id = 'S-038'").fetchone()[0]  # nosec B608
    assert got == pytest.approx(want)


def test_an_average_over_a_metric_leaves_out_the_records_it_has_no_value_for(db):
    """A line not yet delivered has no days_late; the average is over the delivered lines only."""
    got = c.execute(compile_(metric_plan(), question=QM).readings[0], db).rows[0]["days"]
    delivered = db.execute(f"SELECT AVG({DAYS}) FROM commitments WHERE supplier_id = 'S-038' "  # nosec B608
                           "AND delivered_at IS NOT NULL").fetchone()[0]
    assert db.execute("SELECT COUNT(*) FROM commitments WHERE supplier_id = 'S-038' "
                      "AND delivered_at IS NULL").fetchone()[0] > 0
    assert got == round(delivered, 2)


@pytest.mark.parametrize("func", ["SUM", "AVG", "MIN", "MAX"])
def test_a_boolean_metric_is_not_aggregated(func):
    with pytest.raises(c.CompileError) as e:
        compile_(metric_plan(func, metric="late_by_delivery"), question=QM)
    assert e.value.code == "type_mismatch" and "COUNT and WHERE" in str(e.value)


def test_count_still_takes_no_metric():
    assert list(c.intent_validator().iter_errors(intent(metric_plan("COUNT"), "q")))


def test_examined_says_what_an_aggregate_over_a_metric_was_taken_over():
    pl_ = metric_plan()
    assert contract.render_checked(pl_, CAT, "APPROVER").endswith(
        "computed the average days late, over purchase order lines with requested date and delivery date recorded")
    assert contract.headers(pl_, CAT)["days"] == "average days late"


def test_an_average_over_a_metric_reaches_the_answer_as_a_reference(db_path):
    syn = {"readings": [{"label": "avg", "sentences": [{"text": "The average days late was {avg.days#1}."}]}]}

    def gate(prompt):
        body = prompt.split("Answer:\n")[-1].split("\nRecords:")[0]
        text = body.split(". ", 1)[1].split("] ", 1)[1].strip()
        return {"claims": [{"sentence": 1, "text": text, "verdict": "supported", "evidence": ["avg.days"]}]}

    planned = {"disposition": "retrieve", "readings": [{"label": "avg", "plan": metric_plan()}]}
    outputs = {"intent_analysis_demo": [planned], "synthesis": [syn], "support_scoring": [gate]}

    def answer(prompt, cfg):
        out = outputs[cfg.prompt_identity.split("@")[0]].pop(0)
        return out(prompt) if callable(out) else out

    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pl.Deps(ScriptedModelGateway(answer), "demo", CAT, lambda: sqlite3.connect(db_path), audit,
                   p.load(seed.HERE / "intent_analysis.md"), coverage=False)
    out = pl.run(QM, [], APPROVER, deps)
    assert out.response["outcome"] == "answered", out.response
    view = out.response["readings"][0]
    want = sqlite3.connect(db_path).execute(f"SELECT ROUND(AVG({DAYS}), 2) FROM commitments "  # nosec B608
                                            "WHERE supplier_id = 'S-038'").fetchone()[0]
    assert view["rows"][0]["days"] == want and view["answer"] == f"The average days late was {want}."
    assert "over purchase order lines with requested date and delivery date recorded" in view["checked"]
