"""
The output contract, assembled in code: what was examined, the records under each reading,
gaps, and one constructor per outcome. Real plans run on the demo store.
"""

import sqlite3

import pytest

from demo_data import seed
from query_assistance import compiler as c
from query_assistance import contract as k

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}
PLANNER = {"role": "PLANNER", "user_id": "PL-02"}

LATE = {"SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
                   {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_lines"}],
        "FROM": "commitments",
        "WHERE": {"AND": [{"BETWEEN": [{"col": "commitments.delivered_at"}, {"value": "2026-09-01"},
                                       {"value": "2026-09-30"}]},
                          {"EQ": [{"metric": "late_by_delivery"}, {"value": True}]}]},
        "GROUP_BY": ["supplier_id"],
        "ORDER_BY": [{"by": "late_lines", "dir": "DESC"}]}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.db"
    seed.build(path)
    conn = sqlite3.connect(path)
    yield conn
    conn.close()


def run(db, plan, user=APPROVER, question="Which suppliers delivered late in September 2026-09-01 2026-09-30?",
        label="r"):
    intent = {"disposition": "retrieve", "readings": [{"label": label, "plan": plan}]}
    r, = c.compile_intent(intent, question, user, CAT).readings
    return c.execute(r, db)


# --------------------------------------------------------------------------
# what was examined (FR 11, Test 34)
# --------------------------------------------------------------------------

def test_checked_reads_the_metric_as_its_business_term():
    text = k.render_checked(LATE, CAT, "APPROVER")
    assert text == ("Examined: commitments where commitments.delivered_at between '2026-09-01' and '2026-09-30' "
                    "and delivered after the requested date (late_by_delivery) · grouped by supplier_id · "
                    "computed the number of records as late_lines")


def test_two_readings_differ_exactly_where_the_term_was_open():
    other = dict(LATE, WHERE={"AND": [LATE["WHERE"]["AND"][0],
                                      {"EQ": [{"metric": "late_by_promise"}, {"value": True}]}]})
    a, b = k.render_checked(LATE, CAT, "APPROVER"), k.render_checked(other, CAT, "APPROVER")
    assert "delivered after the requested date" in a
    assert "the supplier committed to a date after the requested date" in b


def test_checked_states_when_entitlement_narrowed_the_query():
    assert "limited to the records you hold" in k.render_checked(LATE, CAT, "PLANNER")
    assert "limited" not in k.render_checked(LATE, CAT, "APPROVER")


def test_checked_states_a_restricted_joined_table():
    plan = {"SELECT": [{"expr": {"col": "suppliers.name"}, "as": "name"}], "FROM": "suppliers",
            "JOIN": [{"rel": "commitment_supplier", "type": "INNER"}]}
    text = k.render_checked(plan, CAT, "PLANNER")
    assert text.startswith("Examined: suppliers with commitments") and "limited" in text


def test_checked_covers_each_clause():
    plan = {"SELECT": [{"expr": {"col": "commitments.part_id"}, "as": "part_id"},
                       {"expr": {"func": "SUM", "arg": {"col": "commitments.line_value"}}, "as": "value"}],
            "FROM": "commitments",
            "JOIN": [{"rel": "commitment_order", "type": "INNER"}, {"rel": "order_supplier", "type": "LEFT"}],
            "WHERE": {"AND": [{"OR": [{"IN": [{"col": "commitments.supplier_id"},
                                              [{"value": "S-001"}, {"value": "S-002"}]]},
                                      {"GT": [{"col": "commitments.quantity"}, {"value": 100}]}]},
                              {"IS_NULL": {"col": "commitments.delivered_at"}},
                              {"EQ": [{"metric": "late_by_promise"}, {"value": False}]}]},
            "GROUP_BY": ["part_id"], "HAVING": [{"agg": "value", "op": "GTE", "value": 1000}]}
    text = k.render_checked(plan, CAT, "APPROVER")
    assert "with purchase_orders with suppliers where held" in text
    assert "(commitments.supplier_id in ('S-001', 'S-002') or commitments.quantity > 100)" in text
    assert "commitments.delivered_at not recorded" in text
    assert "not the supplier committed to a date after the requested date (late_by_promise)" in text
    assert "computed summed commitments.line_value as value" in text and "kept where value ≥ 1000" in text


def test_order_caption():
    assert k.render_order(LATE) == "ordered by late_lines, highest first"
    assert k.render_order({"SELECT": [], "FROM": "x"}) is None


# --------------------------------------------------------------------------
# a reading as presented
# --------------------------------------------------------------------------

def test_view_carries_the_records_and_the_ordering(db):
    res = run(db, LATE)
    v = k.reading_view("late", LATE, res, CAT, "APPROVER")
    assert v["result"] == "rows_returned" and v["rows"] == res.rows and v["row_count"] == len(res.rows)
    assert v["columns"] == ["supplier_id", "late_lines"] and v["ordered_by"].startswith("ordered by late_lines")
    assert "answer" not in v and "notice" not in v


def test_view_says_n_of_total_when_cut(db):
    plan = {"SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "cid"}], "FROM": "commitments", "LIMIT": 5}
    v = k.reading_view("all", plan, run(db, plan), CAT, "APPROVER")
    assert v["row_count"] == 5 and v["total"] > 5 and v["notice"] == f"Showing 5 of {v['total']}."


def test_view_of_an_empty_result_is_a_finding(db):
    plan = dict(LATE, WHERE={"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-040"}]})
    v = k.reading_view("r", plan, run(db, plan, question="What did S-040 deliver?"), CAT, "APPROVER")
    assert v["result"] == "empty_no_match" and v["notice"] == "No records matched." and v["rows"] == []
    assert "ordered_by" not in v


def test_view_names_an_identifier_not_held_and_where_it_was_looked_for(db):
    plan = dict(LATE, WHERE={"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-999"}]})
    v = k.reading_view("r", plan, run(db, plan, question="What did S-999 deliver?"), CAT, "APPROVER")
    assert v["not_held"] == [{"value": "S-999", "checked_in": "suppliers.supplier_id"}]
    assert v["notice"] == "S-999 does not appear in suppliers."


def test_view_carries_the_gated_answer_and_its_evidence(db):
    v = k.reading_view("late", LATE, run(db, LATE), CAT, "APPROVER", answer="S-038 had the most.",
                       evidence=["late.supplier_id"])
    assert v["answer"] == "S-038 had the most." and v["evidence"] == ["late.supplier_id"]


# --------------------------------------------------------------------------
# gaps (FR 11, NFR 20)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kind, entity, text", [
    ("no_such_column", "parts", "On who is the buyer contact there is no data. No column in parts records it."),
    ("source_outside_system", "the warehouse system", "who is the buyer contact is not held by the system."),
    ("unspecified", None, "On who is the buyer contact no data could be located."),
])
def test_gap_is_filled_from_its_template(kind, entity, text):
    needs = {"kind": kind} if entity is None else {"kind": kind, "entity": entity}
    g = k.render_gap({"asked": "who is the buyer contact", "needs": needs})
    assert g["text"] == text and "because" not in g["text"]


# --------------------------------------------------------------------------
# outcomes: every constructor validates against the schema
# --------------------------------------------------------------------------

def test_answered_with_two_readings_and_a_gap(db):
    other = dict(LATE, WHERE={"AND": [LATE["WHERE"]["AND"][0],
                                      {"EQ": [{"metric": "late_by_promise"}, {"value": True}]}]})
    views = [k.reading_view("late_by_delivery", LATE, run(db, LATE), CAT, "APPROVER"),
             k.reading_view("late_by_promise", other, run(db, other), CAT, "APPROVER")]
    resp = k.answered(views, [{"asked": "and their buyer", "needs": {"kind": "no_such_column", "entity": "suppliers"}}],
                      understood_as="Which suppliers delivered late in September?")
    assert resp["outcome"] == "answered" and len(resp["readings"]) == 2 and resp["gaps"][0]["text"]
    assert resp["understood_as"]


def test_referred_offers_an_investigation_and_carries_nothing_else():
    resp = k.referred()
    assert resp["offer_investigation"] is True and resp["readings"] == [] and resp["gaps"] == []


def test_no_supported_assertion_replaces_the_records_and_names_the_readings(db):
    """NFR 19 strict reading, Test 17: neither answer nor records is presented."""
    view = k.reading_view("late", LATE, run(db, LATE), CAT, "APPROVER")
    resp = k.no_supported_assertion([view])
    assert resp["readings"] == [] and "rows" not in str(resp)
    assert resp["request_for_input"]["readings"] == [{"label": "late", "checked": view["checked"]}]
    assert "because" not in resp["request_for_input"]["text"]


@pytest.mark.parametrize("make, outcome", [
    (lambda: k.source_unreachable("The company database"), "source_unreachable"),
    (k.request_not_completed, "request_not_completed"),
])
def test_failure_outcomes(make, outcome):
    resp = make()
    assert resp["outcome"] == outcome and resp["readings"] == [] and resp["notice"]


def test_an_invalid_assembly_is_a_bug_not_a_response():
    with pytest.raises(k.ContractError):
        k.answered([{"label": "x"}])
