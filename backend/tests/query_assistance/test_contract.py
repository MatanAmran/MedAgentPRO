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
        "WHERE": {"AND": [{"BETWEEN": {"col": "commitments.delivered_at", "from": {"value": "2026-09-01"},
                                       "to": {"value": "2026-09-30"}}},
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
    """Task 7 review, item 2: labels from schema.json, aggregates named from their expression, never
    the model's alias."""
    text = k.render_checked(LATE, CAT, "APPROVER")
    assert text == ("Examined: purchase order lines where delivery date between '2026-09-01' and '2026-09-30' "
                    "and delivered after the requested date · grouped by supplier · "
                    "computed the number of records")
    assert "late_lines" not in text and "commitments" not in text


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
    assert text.startswith("Examined: suppliers with purchase order lines") and "limited" in text


def test_checked_covers_each_clause():
    plan = {"SELECT": [{"expr": {"col": "commitments.part_id"}, "as": "part_id"},
                       {"expr": {"func": "SUM", "arg": {"col": "commitments.line_value"}}, "as": "value"}],
            "FROM": "commitments",
            "JOIN": [{"rel": "commitment_order", "type": "INNER"}, {"rel": "order_supplier", "type": "LEFT"}],
            "WHERE": {"AND": [{"OR": [{"IN": {"col": "commitments.supplier_id",
                                              "values": [{"value": "S-001"}, {"value": "S-002"}]}},
                                      {"GT": [{"col": "commitments.quantity"}, {"value": 100}]}]},
                              {"IS_NULL": {"col": "commitments.delivered_at"}},
                              {"EQ": [{"metric": "late_by_promise"}, {"value": False}]}]},
            "GROUP_BY": ["part_id"], "HAVING": [{"agg": "value", "op": "GTE", "value": 1000}]}
    text = k.render_checked(plan, CAT, "APPROVER")
    assert "with purchase orders with suppliers where held" in text
    assert "(supplier in ('S-001', 'S-002') or ordered quantity > 100)" in text
    assert "delivery date not recorded" in text
    assert "the supplier committed to a date on or before the requested date" in text
    assert "not the supplier" not in text
    assert "grouped by part" in text
    assert "computed the total line value" in text and "kept where total line value ≥ 1000" in text


def test_order_caption():
    assert k.render_order(LATE, CAT) == "ordered by number of records, highest first"
    assert k.render_order(LATE) == "ordered by late_lines, highest first"   # no catalog: the alias
    assert k.render_order({"SELECT": [], "FROM": "x"}) is None


def test_headers_name_each_column_from_the_catalog_or_its_expression():
    plan = {"SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "s"},
                       {"expr": {"func": "COUNT", "arg": "*"}, "as": "n"},
                       {"expr": {"func": "COUNT", "arg": {"col": "commitments.po_id"}}, "as": "c"},
                       {"expr": {"func": "MAX", "arg": {"col": "commitments.quantity"}}, "as": "m"},
                       {"expr": {"metric": "days_late"}, "as": "d"}], "FROM": "commitments"}
    assert k.headers(plan, CAT) == {"s": "supplier", "n": "number of records", "c": "count of purchase order",
                                    "m": "highest ordered quantity", "d": "days late"}


# --------------------------------------------------------------------------
# a reading as presented
# --------------------------------------------------------------------------

def test_view_carries_the_records_and_the_ordering(db):
    res = run(db, LATE)
    v = k.reading_view("late", LATE, res, CAT, "APPROVER")
    assert v["result"] == "rows_returned" and v["rows"] == res.rows and v["row_count"] == len(res.rows)
    assert v["columns"] == ["supplier_id", "late_lines"]
    assert v["headers"] == {"supplier_id": "supplier", "late_lines": "number of records"}
    assert v["ordered_by"].startswith("ordered by number of records")
    assert "answer" not in v and "notice" not in v


def test_view_says_n_of_total_when_cut(db):
    plan = {"SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "cid"}], "FROM": "commitments", "LIMIT": 5}
    v = k.reading_view("all", plan, run(db, plan), CAT, "APPROVER")
    assert v["row_count"] == 5 and v["total"] > 5 and v["notice"] == f"Showing 5 of {v['total']}."


def _plan(table: str, cols: list[str], joins: tuple[str, ...] = ()) -> dict:
    plan = {"SELECT": [{"expr": {"col": c}, "as": c.replace(".", "_")} for c in cols], "FROM": table}
    return {**plan, "JOIN": [{"rel": r, "type": "INNER"} for r in joins]} if joins else plan


@pytest.mark.parametrize("plan, entity", [
    (_plan("parts", ["parts.part_id"]), "parts"),
    (_plan("commitments", ["commitments.commitment_id", "suppliers.name"], ("commitment_supplier",)),
     "purchase order lines"),
    (_plan("receipts", ["receipts.receipt_id", "purchase_orders.po_id"], ("receipt_commitment", "commitment_order")),
     "goods receipts"),
    # one-to-many: one part, a forecast row for each week
    (_plan("parts", ["parts.part_id", "forecast.week"], ("part_forecast",)), None),
    # many-to-one in the data, but no key declared on (part_id, week)
    (_plan("commitments", ["commitments.commitment_id", "forecast.demand"], ("commitment_forecast",)), None),
    # the "one" side first: each supplier once per commitment
    (_plan("suppliers", ["suppliers.supplier_id"], ("commitment_supplier",)), None),
    ({"SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "cid"},
                 {"expr": {"metric": "days_late"}, "as": "late"}], "FROM": "commitments"}, None),
    # D10: grouped by one identifier column, one row per record of the table it identifies
    (LATE, "suppliers"),
    ({**LATE, "SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "cid"},
                         {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_lines"}], "GROUP_BY": ["cid"],
      "ORDER_BY": [{"by": "late_lines", "dir": "DESC"}]}, "purchase order lines"),
    ({**LATE, "SELECT": [{"expr": {"col": "commitments.week"}, "as": "week"},
                         {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_lines"}], "GROUP_BY": ["week"],
      "ORDER_BY": [{"by": "late_lines", "dir": "DESC"}]}, None),
])
def test_entity_only_where_each_row_is_a_row_of_the_from_table(db, plan, entity):
    """Long list, L4 [demo extension]: plain columns, no GROUP_BY, every JOIN many-to-one by declared keys
    (references in schema.json); otherwise "rows" on the screen (chat, 18.10). D10: or grouped by exactly
    one identifier column."""
    assert k.reading_entity(plan, CAT) == entity
    v = k.reading_view("r", plan, run(db, plan), CAT, "APPROVER")
    assert v.get("entity") == entity


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
    ("no_such_column", "parts", "who is the buyer contact: there is no data. No column in parts records it."),
    ("source_outside_system", "the warehouse system", "who is the buyer contact: not held by the system."),
    ("unspecified", None, "who is the buyer contact: no data could be located."),
])
def test_gap_is_filled_from_its_template(kind, entity, text):
    needs = {"kind": kind} if entity is None else {"kind": kind, "entity": entity}
    g = k.render_gap({"asked": "who is the buyer contact", "needs": needs})
    assert g["text"] == text and "because" not in g["text"]


def test_gap_text_names_the_table_or_column_by_its_label():
    """Task 7 review, item 2, applied to gaps (task 8): the text reads the label; missing keeps the name."""
    g = k.render_gap({"asked": "who approved them", "needs": {"kind": "no_such_column", "entity": "purchase_orders"}},
                     CAT)
    assert g["text"] == "who approved them: there is no data. No column in purchase orders records it."
    assert g["missing"] == {"kind": "no_such_column", "entity": "purchase_orders"}
    col = k.render_gap({"asked": "x", "needs": {"kind": "value_not_present",
                                                "entity": "commitments.delay_reason"}}, CAT)
    assert col["text"] == "x: does not appear in delay reason."


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


def test_a_reading_is_titled_by_its_one_metric_else_numbered(db):
    """Task 7, Q2: the title comes from schema.json, never from a model. One metric: its description.
    None, or more than one: the reading's place in the answer."""
    none = {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
            "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-001"}]}}
    both = dict(LATE, WHERE={"AND": [LATE["WHERE"]["AND"][1],
                                     {"EQ": [{"metric": "late_by_promise"}, {"value": True}]}]})
    assert k.reading_title(LATE, CAT) == "Delivered after the requested date"
    assert k.reading_title(none, CAT) is None and k.reading_title(both, CAT) is None
    views = [k.reading_view("a", LATE, run(db, LATE), CAT, "APPROVER"),
             k.reading_view("b", both, run(db, both), CAT, "APPROVER")]
    resp = k.answered(views, ambiguous_terms=["late"])
    assert [r["title"] for r in resp["readings"]] == ["Delivered after the requested date", "Reading 2"]
    assert resp["ambiguous_terms"] == ["late"]


def test_ambiguous_terms_only_where_more_than_one_reading_is_shown(db):
    resp = k.answered([k.reading_view("a", LATE, run(db, LATE), CAT, "APPROVER")], ambiguous_terms=["late"])
    assert "ambiguous_terms" not in resp


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


def test_every_gap_template_opens_with_what_was_asked():
    """Task 10a (chat 23:35): "On {asked} ..." read wrongly whenever asked was not a noun phrase ("On who
    approved them"). Every template now opens "{asked}: ", whatever grammatical form asked takes."""
    assert all(t.startswith("{asked}: ") for t in k.TEMPLATES.values()), k.TEMPLATES


def test_the_rate_notice_carries_a_whole_number_of_seconds_and_nothing_else():
    """Chat, 18.10, 04:45: the rate refusal says how long to wait, a number from the window only."""
    assert k.request_not_completed(None, k.rate_limited_notice(7))["notice"].endswith("Try again in 7 seconds.")
    for bad in (0, -1, 2.5, True, "7"):
        with pytest.raises(k.ContractError):
            k.rate_limited_notice(bad)
    for bad in (k.RATE_LIMITED, k.RATE_LIMITED.format(seconds="soon"), k.RATE_LIMITED.format(seconds="07")):
        with pytest.raises(k.ContractError):
            k.request_not_completed(None, bad)


BOOLEAN_METRICS = sorted(m for m, spec in CAT.metrics.items() if spec.get("type") == "boolean")


@pytest.mark.parametrize("metric", BOOLEAN_METRICS)
@pytest.mark.parametrize("op, value, swap", [("EQ", True, False), ("EQ", False, False), ("NE", True, False),
                                             ("NE", False, False), ("EQ", False, True), ("NE", True, True)])
def test_the_title_never_contradicts_what_was_examined(metric, op, value, swap):
    """Question 11 on the real model (chat, 18.10, 04:45): "on time" as late_by_delivery = false was
    titled "Delivered after the requested date". The title reads the comparison as Examined does."""
    pair = [{"metric": metric}, {"value": value}]
    table = CAT.metrics[metric].get("table") or next(iter(CAT.metrics[metric].get("tables", ["commitments"])))
    plan = {"SELECT": [{"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}], "FROM": table,
            "WHERE": {op: pair[::-1] if swap else pair}}
    title = k.reading_title(plan, CAT)
    examined = k.render_checked(plan, CAT, "APPROVER")
    negated = (value is False) != (op == "NE")
    said = CAT.metrics[metric]["description_negated" if negated else "description"].rstrip(".")
    other = CAT.metrics[metric]["description" if negated else "description_negated"].rstrip(".")
    lower = lambda t: t[0].lower() + t[1:]   # noqa: E731
    assert lower(said) in examined and lower(other) not in examined
    assert title == said


@pytest.mark.parametrize("metric", BOOLEAN_METRICS)
def test_every_boolean_metric_words_its_negation(metric):
    """Chat, 18.10, 05:00: "not" before the description gave "not the supplier committed to ..."."""
    neg = CAT.metrics[metric].get("description_negated")
    assert neg and not neg.lower().startswith("not") and neg != CAT.metrics[metric]["description"]


def test_a_metric_tested_both_ways_gets_no_title():
    plan = {"SELECT": [{"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}], "FROM": "commitments",
            "WHERE": {"OR": [{"EQ": [{"metric": "late_by_delivery"}, {"value": True}]},
                             {"EQ": [{"metric": "late_by_delivery"}, {"value": False}]}]}}
    assert k.reading_title(plan, CAT) is None
