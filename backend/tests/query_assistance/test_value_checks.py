"""
Values as the store holds them (chat 19.10, review of 80_GENERALITY, A1 and A2). Hand-written plans only,
on the demo store; no model.

A1: an identifier written in another case ('s-001') is resolved, where it is held, to the one form the
store holds (S-001), and the query and Examined use that form. Several forms keep the exact match; none
keeps the value, which is then reported absent, as before (Test 35).

A2: a value compared with a column that declares its values ('Open' for purchase_orders.status) is
written in the declared form; any other value is rejected with its path, so the feedback attempt can
correct it (NFR 6).
"""

import sqlite3
from pathlib import Path

import pytest

from demo_data import seed
from demo_data.scripted import gate_claims
from model_client.client import ScriptedModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import compiler as c
from query_assistance import contract
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from shared.circuit_breaker import CircuitBreaker, MemoryStore

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}


def supplied(value):
    return {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}],
            "FROM": "commitments",
            "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": value}]},
            "ORDER_BY": [{"by": "po_id", "dir": "ASC"}]}


def orders(where):
    return {"SELECT": [{"expr": {"col": "purchase_orders.po_id"}, "as": "po_id"}],
            "FROM": "purchase_orders", "WHERE": where}


def status(value):
    return orders({"EQ": [{"col": "purchase_orders.status"}, {"value": value}]})


def intent(*plans):
    return {"disposition": "retrieve", "readings": [{"label": f"r{i}", "plan": pl_} for i, pl_ in enumerate(plans)]}


def compile_(question, *plans):
    return c.compile_intent(intent(*plans), question, APPROVER, CAT, c.intent_validator())


@pytest.fixture(scope="module")
def db(db_path):
    conn = sqlite3.connect(db_path)
    yield conn
    conn.close()


SYNTHESIS = {"readings": [{"label": "r0", "sentences": [
    {"text": "{r0.total} records matched, among them {r0.po_id#1}."}]}]}


def scripted(intents):
    """The scripted model: the plans given in turn, then one sentence and a gate that supports it."""
    def answer(prompt, cfg):
        stage = cfg.prompt_identity.split("@")[0]
        if stage.startswith("intent_analysis"):
            return intents.pop(0) if len(intents) > 1 else intents[0]
        if stage == "synthesis":
            return SYNTHESIS
        return gate_claims(prompt, {"verdicts": ["supported"], "evidence": {"r0": ["total", "po_id"]}})
    return answer


def run(db_path, question, *plans):
    """One exchange on the scripted model whose plan is given."""
    answer = scripted([intent(*plans)])

    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pl.Deps(ScriptedModelGateway(answer), "demo", CAT, lambda: sqlite3.connect(db_path), audit,
                   p.load(seed.HERE / "intent_analysis.md"))
    return pl.run(question, [], APPROVER, deps), audit


# --------------------------------------------------------------------------
# A2: declared values
# --------------------------------------------------------------------------

@pytest.mark.parametrize("written", ["Open", "OPEN", "open"])
def test_a_declared_value_in_another_case_is_compiled_in_the_declared_form(written):
    ci = compile_(f"Which purchase orders are {written}?", status(written))
    assert ci.readings[0].params[0] == "open"
    assert ci.intent["readings"][0]["plan"]["WHERE"]["EQ"][1] == {"value": "open"}
    assert "'open'" in contract.render_checked(ci.intent["readings"][0]["plan"], CAT, "APPROVER")


def test_the_models_output_is_not_changed_in_place():
    out = intent(status("Open"))
    c.compile_intent(out, "Which purchase orders are Open?", APPROVER, CAT, c.intent_validator())
    assert out["readings"][0]["plan"]["WHERE"]["EQ"][1] == {"value": "Open"}


@pytest.mark.parametrize("where, path", [
    ({"EQ": [{"col": "purchase_orders.status"}, {"value": "opened"}]}, "readings/0/plan/WHERE/EQ/1/value"),
    ({"EQ": [{"value": "pending"}, {"col": "purchase_orders.status"}]}, "readings/0/plan/WHERE/EQ/0/value"),
    ({"AND": [{"IS_NOT_NULL": {"col": "purchase_orders.order_date"}},
              {"IN": {"col": "purchase_orders.status", "values": [{"value": "open"}, {"value": "on hold"}]}}]},
     "readings/0/plan/WHERE/AND/1/IN/values/1/value"),
])
def test_a_value_a_column_does_not_take_is_rejected_with_its_path(where, path):
    with pytest.raises(c.CompileError) as e:
        compile_("Which purchase orders are opened, pending or on hold?", orders(where))
    assert e.value.code == "value_not_declared"
    assert e.value.detail["path"] == path
    assert "open, closed, cancelled" in e.value.reason   # what the feedback attempt is told: the catalog's set


def test_a_column_with_no_declared_values_is_left_as_written():
    pl_ = {"SELECT": [{"expr": {"col": "suppliers.supplier_id"}, "as": "supplier_id"}], "FROM": "suppliers",
           "WHERE": {"EQ": [{"col": "suppliers.name"}, {"value": "apex polymers"}]}}
    assert compile_("Is apex polymers a supplier?", pl_).readings[0].params[0] == "apex polymers"


def test_a_rejected_value_gets_one_feedback_attempt_in_the_pipeline(db_path):
    """The existing retry: the second plan, in the declared form, is run."""
    seen = []
    inner = scripted([intent(status("opened")), intent(status("open"))])

    def answer(prompt, cfg):
        if cfg.prompt_identity.startswith("intent_analysis"):
            seen.append(prompt)
        return inner(prompt, cfg)

    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pl.Deps(ScriptedModelGateway(answer), "demo", CAT, lambda: sqlite3.connect(db_path), audit,
                   p.load(seed.HERE / "intent_analysis.md"))
    q = "How many purchase orders are opened?"
    out = pl.run(q, [], APPROVER, deps)
    assert len(seen) == 2 and "is not a value purchase_orders.status takes" in seen[1]
    assert out.response["readings"][0]["total"] == sqlite3.connect(db_path).execute(
        "SELECT COUNT(*) FROM purchase_orders WHERE status = 'open'").fetchone()[0]


# --------------------------------------------------------------------------
# A1: identifiers in another case
# --------------------------------------------------------------------------

def test_an_identifier_in_another_case_is_resolved_to_the_stored_form(db):
    ci = compile_("What did s-001 supply?", supplied("s-001"))
    assert c.resolve_identifiers(ci, db) == {("commitments.supplier_id", "s-001"): "S-001"}


def test_an_identifier_held_as_written_is_not_looked_up_again(db):
    ci = compile_("What did S-001 supply?", supplied("S-001"))
    assert c.resolve_identifiers(ci, db) == {}


def test_s001_lowercase_finds_s001_and_examined_shows_the_stored_form(db_path):
    out, _ = run(db_path, "What did s-001 supply?", supplied("s-001"))
    want = sqlite3.connect(db_path).execute(
        "SELECT COUNT(*) FROM commitments WHERE supplier_id = 'S-001'").fetchone()[0]
    reading = out.response["readings"][0]
    assert reading["result"] == "rows_returned" and reading["total"] == want > 0
    assert "'S-001'" in reading["checked"] and "s-001" not in reading["checked"]


def test_show_all_runs_the_plan_as_resolved(db_path):
    """A1 with Phase B task 5: "Show all records" runs the plan the exchange kept after A1, so 's-001'
    asked for returns the records of S-001, the form held, and never the empty set of 's-001'."""
    answer = scripted([intent(supplied("s-001"))])
    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pl.Deps(ScriptedModelGateway(answer), "demo", CAT, lambda: sqlite3.connect(db_path), audit,
                   p.load(seed.HERE / "intent_analysis.md"))
    out = pl.run("What did s-001 supply?", [], APPROVER, deps)
    assert out.plans["r0"]["WHERE"] == {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-001"}]}
    shown = out.response["readings"][0]
    every = pl.show_all(out.exchange_id, "r0", out.plans["r0"], APPROVER, deps, shown["rows"])
    want = sqlite3.connect(db_path).execute(
        "SELECT COUNT(*) FROM commitments WHERE supplier_id = 'S-001'").fetchone()[0]
    assert every["total"] == want > 0 and "'S-001'" in every["checked"]


def test_an_identifier_that_does_not_exist_is_still_reported_absent(db_path):
    out, _ = run(db_path, "What did s-099 supply?", supplied("s-099"))
    reading = out.response["readings"][0]
    assert reading["result"] == "empty_unknown_entity"
    assert reading["not_held"][0]["value"] == "s-099"


@pytest.fixture
def two_forms(tmp_path):
    """A store that holds AB-1 and ab-1 as two suppliers."""
    path = tmp_path / "two.db"
    seed.build(path)
    conn = sqlite3.connect(path)
    for sid in ("AB-1", "ab-1"):
        conn.execute("INSERT INTO suppliers (supplier_id, name, region, country) VALUES (?, ?, 'EMEA', 'Israel')",
                     (sid, f"Supplier {sid}"))
    conn.commit()
    yield conn
    conn.close()


@pytest.mark.parametrize("written", ["AB-1", "ab-1", "Ab-1", "AB-2"])
def test_several_stored_forms_keep_the_exact_match(two_forms, written):
    """Held as written: nothing to resolve. Two forms, neither as written: no guess between them. Held
    in no form: absent, as before."""
    ci = compile_(f"What did {written} supply?", supplied(written))
    assert c.resolve_identifiers(ci, two_forms) == {}
    if written == "Ab-1":
        probe = ci.readings[0].probes[0]
        assert sorted(r[0] for r in two_forms.execute(probe.resolve_sql, probe.resolve_params)) == ["AB-1", "ab-1"]


def test_the_lookup_carries_the_entitlement_condition():
    """A1 never widens what a user may see: the case-insensitive lookup is the probe with NOCASE."""
    fixtures = Path(__file__).parents[1] / "fixtures"
    cat = c.Catalog.load(fixtures / "restricted_schema.json", seed.HERE / "relations.json")   # PLANNER: own lines
    planner = {"role": "PLANNER", "user_id": "PL-02"}
    pl_ = {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
           "WHERE": {"EQ": [{"col": "commitments.po_id"}, {"value": "po-87213"}]}}
    ci = c.compile_intent(intent(pl_), "What is on po-87213?", planner, cat, c.intent_validator())
    probe = ci.readings[0].probes[0]
    assert "COLLATE NOCASE" in probe.resolve_sql
    def conditions(sql):
        return sql.split(" WHERE ")[1].split(" LIMIT")[0].split(" AND ")[1:]
    assert conditions(probe.sql) == conditions(probe.resolve_sql) == ['"purchase_orders"."planner_id" = ?']
    assert probe.params == probe.resolve_params
