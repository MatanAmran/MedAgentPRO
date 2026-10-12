"""
Tests for compiler.py. Run:  pytest test_compiler.py -q

Each test names the check or book test it covers. The database is an in-memory
SQLite built from the placeholder schema.json.
"""

import sqlite3

import pytest

from query_assistance import compiler as c

CAT = c.Catalog.load()
VALIDATOR = c.intent_validator()
PLANNER = {"role": "PLANNER", "user_id": "u-17"}
APPROVER = {"role": "APPROVER", "user_id": "u-90"}
Q = "Which suppliers were late on PO-1 or S-031 in weeks 1 to 13, and what about the forecast?"


# --------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE commitments (commitment_id TEXT, po_id TEXT, supplier_id TEXT, part_id TEXT,
            planner_id TEXT, week INTEGER, quantity INTEGER, due_date TEXT, promised_date TEXT,
            delivered_at TEXT);
        CREATE TABLE suppliers (supplier_id TEXT, name TEXT, region TEXT);
        CREATE TABLE parts (part_id TEXT, description TEXT, on_hand INTEGER);
        CREATE TABLE forecast (part_id TEXT, week INTEGER, demand INTEGER);
        INSERT INTO suppliers VALUES ('S-031','Acme','EMEA'), ('S-040','Beta','US');
    """)
    rows = []
    for i in range(1, 8):   # u-17 holds 7 commitments with S-031, all late by promise
        rows.append((f"C-{i}", f"PO-{i}", "S-031", "P-1", "u-17", i, 10,
                     "2026-09-01", "2026-09-10", "2026-09-12"))
    rows.append(("C-99", "PO-99", "S-040", "P-1", "u-99", 2, 5,     # belongs to another planner
                 "2026-09-01", "2026-09-05", None))
    conn.executemany("INSERT INTO commitments VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    yield conn
    conn.close()


def plan(**over):
    p = {"SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "cid"}], "FROM": "commitments"}
    p.update(over)
    return p


def intent(*plans, **over):
    d = {"disposition": "retrieve",
         "readings": [{"label": f"r{i}", "plan": p} for i, p in enumerate(plans or [plan()])]}
    d.update(over)
    return d


def compile_(d, user=PLANNER, question=Q, row_limit=c.ROW_LIMIT):
    v = VALIDATOR if row_limit == c.ROW_LIMIT else c.intent_validator(row_limit=row_limit)
    return c.compile_intent(d, question, user, CAT, v, row_limit)


def rejects(code, d, **kw):
    with pytest.raises(c.CompileError) as e:
        compile_(d, **kw)
    assert e.value.code == code, e.value
    return e.value


def run(d, db, **kw):
    return [c.execute(r, db) for r in compile_(d, **kw).readings]


# --------------------------------------------------------------------------
# Test 13: parameters validated before anything is built
# --------------------------------------------------------------------------

def test_limit_above_range_is_rejected_not_clamped():
    rejects("schema_violation", intent(plan(LIMIT=c.ROW_LIMIT + 1)))


def test_raw_sql_field_is_rejected():
    rejects("schema_violation", intent(plan(RAW="DROP TABLE commitments")))


def test_refer_to_investigation_builds_nothing():
    out = compile_({"disposition": "refer_to_investigation"})
    assert out.disposition == "refer_to_investigation" and out.readings == []


# --------------------------------------------------------------------------
# LIMIT default and truncation (Table 15)
# --------------------------------------------------------------------------

def test_limit_injected_when_omitted():
    r = compile_(intent()).readings[0]
    assert r.limit_injected and r.limit == c.ROW_LIMIT and r.params[-1] == c.ROW_LIMIT


def test_limit_kept_when_stated():
    r = compile_(intent(plan(LIMIT=5))).readings[0]
    assert not r.limit_injected and r.limit == 5


def test_truncation_is_reported(db):
    res, = run(intent(plan(LIMIT=3)), db)
    assert len(res.rows) == 3 and res.total == 7 and res.truncated


def test_no_truncation_when_all_fit(db):
    res, = run(intent(), db)
    assert res.total == len(res.rows) == 7 and not res.truncated


def test_cut_is_repeatable(db):
    a, = run(intent(plan(LIMIT=3)), db)
    b, = run(intent(plan(LIMIT=3)), db)
    assert a.rows == b.rows


# --------------------------------------------------------------------------
# Entitlement, NFR 17 and Test 18
# --------------------------------------------------------------------------

def test_entitlement_is_a_condition_in_the_query():
    r = compile_(intent()).readings[0]
    assert '"commitments"."planner_id" = ?' in r.sql and "u-17" in r.params


def test_entitled_user_sees_only_own_records(db):
    res, = run(intent(), db)
    assert {row["cid"] for row in res.rows} == {f"C-{i}" for i in range(1, 8)}


def test_count_query_carries_entitlement(db):
    res, = run(intent(plan(LIMIT=1)), db)
    assert res.total == 7          # C-99 of another planner is not counted


def test_role_with_all_sees_everything(db):
    res, = run(intent(), db, user=APPROVER)
    assert res.total == 8


def test_undeclared_entitlement_blocks_with_reason():
    e = rejects("entitlement_undeclared",
                intent(plan(SELECT=[{"expr": {"col": "forecast.demand"}, "as": "d"}], FROM="forecast")))
    assert "forecast" in e.reason and "declares no entitlement rule" in e.reason


def test_undeclared_entitlement_on_joined_table_blocks():
    p = plan(SELECT=[{"expr": {"col": "parts.part_id"}, "as": "p"}], FROM="parts",
             JOIN=[{"rel": "part_forecast", "type": "INNER"}])
    rejects("entitlement_undeclared", intent(p))


def test_role_absent_grants_no_access():
    rejects("entitlement_denied", intent(), user={"role": "HTC_BUYER", "user_id": "x"})


def test_left_join_entitlement_goes_in_on_clause():
    p = plan(FROM="suppliers", SELECT=[{"expr": {"col": "suppliers.supplier_id"}, "as": "s"}],
             JOIN=[{"rel": "commitment_supplier", "type": "LEFT"}])
    r = compile_(intent(p)).readings[0]
    on, _, where = r.sql.partition(" WHERE ")
    assert '"commitments"."planner_id" = ?' in on and "planner_id" not in where


def test_model_cannot_express_entitlement_param():
    p = plan(WHERE={"EQ": [{"col": "commitments.planner_id"}, {"param": "USER"}]})
    rejects("schema_violation", intent(p))


# --------------------------------------------------------------------------
# Scope: columns and relations
# --------------------------------------------------------------------------

def test_unknown_column():
    rejects("unknown_column", intent(plan(SELECT=[{"expr": {"col": "commitments.nope"}, "as": "x"}])))


def test_column_of_table_not_joined():
    rejects("column_out_of_scope", intent(plan(SELECT=[{"expr": {"col": "suppliers.name"}, "as": "x"}])))


def test_unknown_relation():
    rejects("unknown_relation", intent(plan(JOIN=[{"rel": "nope", "type": "INNER"}])))


def test_relation_not_touching_scope():
    rejects("relation_out_of_scope", intent(plan(JOIN=[{"rel": "part_forecast", "type": "INNER"}])))


def test_join_resolves_through_relation(db):
    p = plan(SELECT=[{"expr": {"col": "suppliers.name"}, "as": "name"}],
             JOIN=[{"rel": "commitment_supplier", "type": "INNER"}], LIMIT=1)
    res, = run(intent(p), db)
    assert res.rows == [{"name": "Acme"}]


# --------------------------------------------------------------------------
# Types
# --------------------------------------------------------------------------

def test_date_against_empty_string_rejected():
    rejects("type_mismatch", intent(plan(WHERE={"EQ": [{"col": "commitments.due_date"}, {"value": ""}]})))


def test_integer_against_text_rejected():
    rejects("type_mismatch", intent(plan(WHERE={"EQ": [{"col": "commitments.week"}, {"value": "5"}]})))


def test_column_to_column_types_must_match():
    rejects("type_mismatch",
            intent(plan(WHERE={"GT": [{"col": "commitments.week"}, {"col": "commitments.due_date"}]})))


def test_two_values_and_no_column_rejected():
    rejects("no_column_in_comparison", intent(plan(WHERE={"EQ": [{"value": 1}, {"value": 1}]})))


def test_sum_of_text_rejected():
    p = plan(SELECT=[{"expr": {"func": "SUM", "arg": {"col": "commitments.po_id"}}, "as": "s"}])
    rejects("type_mismatch", intent(p))


def test_value_is_bound_not_interpolated():
    evil = "x' OR '1'='1"
    r = compile_(intent(plan(WHERE={"EQ": [{"col": "commitments.po_id"}, {"value": evil}]})),
                 question=f"What is on order {evil}?").readings[0]
    assert evil not in r.sql and evil in r.params


# --------------------------------------------------------------------------
# NULL tests
# --------------------------------------------------------------------------

def test_is_null_on_from_table(db):
    res, = run(intent(plan(WHERE={"IS_NULL": {"col": "commitments.delivered_at"}})), db, user=APPROVER)
    assert [r["cid"] for r in res.rows] == ["C-99"]


def test_is_null_on_joined_table_rejected():
    p = plan(JOIN=[{"rel": "commitment_supplier", "type": "LEFT"}],
             WHERE={"IS_NULL": {"col": "suppliers.name"}})
    rejects("null_test_on_joined", intent(p))


# --------------------------------------------------------------------------
# GROUP_BY, HAVING, ORDER_BY, aliases, labels
# --------------------------------------------------------------------------

AGG = [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
       {"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}]


def test_mixed_select_without_group_by_rejected():
    rejects("group_by_mismatch", intent(plan(SELECT=AGG)))


def test_group_by_without_aggregate_rejected():
    rejects("group_by_mismatch", intent(plan(GROUP_BY=["cid"])))


def test_group_by_on_aggregate_rejected():
    rejects("group_by_mismatch", intent(plan(SELECT=AGG, GROUP_BY=["n"])))


def test_aggregate_only_needs_no_group_by(db):
    res, = run(intent(plan(SELECT=[{"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}])), db)
    assert res.rows == [{"n": 7}]


def test_having_on_plain_alias_rejected():
    rejects("having_not_aggregate",
            intent(plan(SELECT=AGG, GROUP_BY=["supplier_id"],
                        HAVING=[{"agg": "supplier_id", "op": "GT", "value": 1}])))


def test_order_by_unknown_alias_rejected():
    rejects("unknown_alias", intent(plan(ORDER_BY=[{"by": "nope", "dir": "ASC"}])))


def test_duplicate_alias_rejected():
    rejects("duplicate_alias", intent(plan(SELECT=[{"expr": {"col": "commitments.week"}, "as": "a"},
                                                    {"expr": {"col": "commitments.po_id"}, "as": "a"}])))


def test_duplicate_label_rejected():
    d = intent(plan(), plan())
    d["readings"][1]["label"] = "r0"
    rejects("duplicate_label", d)


def test_grouped_count_with_metric(db):
    p = plan(SELECT=AGG, GROUP_BY=["supplier_id"],
             WHERE={"EQ": [{"metric": "late_by_promise"}, {"value": True}]},
             HAVING=[{"agg": "n", "op": "GT", "value": 3}], ORDER_BY=[{"by": "n", "dir": "DESC"}])
    res, = run(intent(p), db)
    assert res.rows == [{"supplier_id": "S-031", "n": 7}]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def test_unknown_metric_rejected():
    rejects("unknown_metric", intent(plan(WHERE={"EQ": [{"metric": "nope"}, {"value": True}]})))


def test_metric_outside_scope_rejected():
    p = plan(FROM="suppliers", SELECT=[{"expr": {"metric": "days_late"}, "as": "d"}])
    rejects("metric_out_of_scope", intent(p))


def test_metric_expands_in_code(db):
    res, = run(intent(plan(SELECT=[{"expr": {"metric": "days_late"}, "as": "d"}], LIMIT=1)), db)
    assert res.rows == [{"d": 11.0}]


# --------------------------------------------------------------------------
# unresolved and ambiguous
# --------------------------------------------------------------------------

def gap(asked, kind, entity=None):
    needs = {"kind": kind} if entity is None else {"kind": kind, "entity": entity}
    return {"asked": asked, "needs": needs}


def test_asked_must_be_span_of_question():
    rejects("span_not_in_question", intent(unresolved=[gap("because of a strike", "unspecified")]))


def test_asked_span_matches_ignoring_case_and_spacing():
    compile_(intent(unresolved=[gap("WHAT  about the forecast", "unspecified")]))


def test_unspecified_carries_no_entity():
    rejects("gap_entity", intent(unresolved=[gap("the forecast", "unspecified", "forecast")]))


def test_no_such_column_names_an_existing_table():
    compile_(intent(unresolved=[gap("the forecast", "no_such_column", "commitments")]))
    rejects("gap_entity", intent(unresolved=[gap("the forecast", "no_such_column", "nope")]))


def test_no_such_table_names_a_table_that_is_absent():
    compile_(intent(unresolved=[gap("the forecast", "no_such_table", "shipments")]))
    rejects("gap_entity", intent(unresolved=[gap("the forecast", "no_such_table", "suppliers")]))


def test_ambiguity_requires_a_reading_per_alternative():
    amb = [{"term": "late", "alternatives": [{"metric": "late_by_delivery"}, {"metric": "late_by_promise"}]}]
    rejects("ambiguity_collapsed", intent(plan(), ambiguous=amb))
    compile_(intent(plan(), plan(), ambiguous=amb))


def test_ambiguity_term_must_be_span():
    amb = [{"term": "tardy", "alternatives": [{"metric": "late_by_delivery"}, {"metric": "late_by_promise"}]}]
    rejects("span_not_in_question", intent(plan(), plan(), ambiguous=amb))


# --------------------------------------------------------------------------
# Test 35: an empty result is answered as such
# --------------------------------------------------------------------------

def test_empty_with_known_identifier_is_no_match(db):
    p = plan(WHERE={"AND": [{"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-031"}]},
                            {"EQ": [{"col": "commitments.week"}, {"value": 13}]}]})
    res, = run(intent(p), db)
    assert res.outcome == "empty_no_match" and res.missing == []


def test_empty_with_unknown_identifier_names_it(db):
    p = plan(WHERE={"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-777"}]})
    res, = run(intent(p), db, question="Which commitments does S-777 hold?")
    assert res.outcome == "empty_unknown_entity"
    assert res.missing == [{"column": "commitments.supplier_id", "value": "S-777",
                            "checked_in": "suppliers.supplier_id"}]


def test_in_list_reports_only_the_absent_identifier(db):
    p = plan(WHERE={"AND": [{"IN": [{"col": "commitments.po_id"}, [{"value": "PO-1"}, {"value": "PO-404"}]]},
                            {"EQ": [{"col": "commitments.week"}, {"value": 13}]}]})
    res, = run(intent(p), db, question="Were PO-1 and PO-404 late in week 13?")
    assert res.missing == [{"column": "commitments.po_id", "value": "PO-404",
                            "checked_in": "commitments.po_id"}]


def test_identifier_under_or_is_not_probed():
    p = plan(WHERE={"OR": [{"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-777"}]},
                           {"EQ": [{"col": "commitments.week"}, {"value": 2}]}]})
    assert compile_(intent(p), question="What did S-777 commit, or anyone in week 2?").readings[0].probes == []


def test_probe_respects_entitlement(db):
    """C-99 exists but belongs to another planner. To u-17 it is not held."""
    p = plan(WHERE={"EQ": [{"col": "commitments.commitment_id"}, {"value": "C-99"}]})
    res, = run(intent(p), db, question="What is the status of C-99?")
    assert res.outcome == "empty_unknown_entity"


def test_held_identifier_with_no_lines_is_no_match(db):
    """S-040 is a supplier, held in suppliers. u-17 holds no line of it. Checked where the
    supplier is held, so the answer is that nothing matched, not that S-040 is absent."""
    p = plan(WHERE={"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-040"}]})
    res, = run(intent(p), db, question="Which commitments does S-040 hold?")
    assert res.outcome == "empty_no_match" and res.missing == []


def test_reference_target_is_checked_within_entitlement():
    p = plan(WHERE={"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-040"}]})
    probe, = compile_(intent(p), question="Which commitments does S-040 hold?").readings[0].probes
    assert probe.checked_in == "suppliers.supplier_id" and '"suppliers"' in probe.sql


def test_unreadable_reference_falls_back_to_the_column(db):
    """Where the role may not read the referenced table, the column itself is checked,
    as it was before references were declared."""
    import copy, json
    schema = json.loads((c.HERE / "schema.json").read_text(encoding="utf-8"))
    rel = json.loads((c.HERE / "relations.json").read_text(encoding="utf-8"))
    del schema["entities"]["suppliers"]["entitlement"]["PLANNER"]
    cat = c.Catalog(copy.deepcopy(schema), rel)
    p = plan(WHERE={"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-040"}]})
    r, = c.compile_intent(intent(p), "Which commitments does S-040 hold?", PLANNER, cat, VALIDATOR).readings
    assert r.probes[0].checked_in == "commitments.supplier_id"
    assert c.execute(r, db).outcome == "empty_unknown_entity"


@pytest.mark.parametrize("bad", ["suppliers.name", "suppliers.nope"])
def test_reference_must_name_an_identifier(bad):
    import json
    schema = json.loads((c.HERE / "schema.json").read_text(encoding="utf-8"))
    rel = json.loads((c.HERE / "relations.json").read_text(encoding="utf-8"))
    schema["entities"]["commitments"]["columns"]["supplier_id"]["references"] = bad
    with pytest.raises(ValueError):
        c.Catalog(schema, rel)


def test_rows_returned(db):
    res, = run(intent(), db)
    assert res.outcome == "rows_returned"


# --------------------------------------------------------------------------
# FR 11 and FR 16: several readings run together, each under its own label
# --------------------------------------------------------------------------

def test_each_reading_returns_under_its_label(db):
    a = plan(SELECT=[{"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}],
             WHERE={"EQ": [{"metric": "late_by_delivery"}, {"value": True}]})
    b = plan(SELECT=[{"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}],
             WHERE={"EQ": [{"metric": "late_by_promise"}, {"value": True}]})
    res = run(intent(a, b), db)
    assert [r.label for r in res] == ["r0", "r1"]
    assert [r.rows for r in res] == [[{"n": 7}], [{"n": 7}]]
