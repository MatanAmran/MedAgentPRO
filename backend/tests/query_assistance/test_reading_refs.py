"""
Task 3, the round of the plan language (docs/notes/92_TASK3_PLAN.md; chat 21.10, 12:50) [proposal, not in the
book; Test 48 is a proposal]. A reading filters by the result of another reading of the same plan, through a
declared relation (IN_READING, NOT_IN_READING), compiled to EXISTS over that reading's own query; each reading still
runs and is shown on its own (QPL). With it: IN up to ROW_LIMIT values (H7), and a reading at a limit of the
language left out on the last attempt with its part said (GAP-07), never "not completed" for the whole question.
Scripted models only.
"""

from __future__ import annotations

import copy
import itertools
import json
import re
import sqlite3

import pytest

from demo_data import seed
from query_assistance import compiler as c
from query_assistance import prompts as p

SCHEMA = json.loads((seed.HERE / "schema.json").read_text(encoding="utf-8"))
RELATIONS = json.loads((seed.HERE / "relations.json").read_text(encoding="utf-8"))
CAT = c.Catalog(SCHEMA, RELATIONS)
INTENT_PROMPT = p.load(seed.HERE / "intent_analysis.md")
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}
PLANNER = {"role": "PLANNER", "user_id": "PL-02"}

H4 = "Which suppliers were late in September, and what is the value of their open orders?"
FIRST = "Which suppliers were late in September"
SECOND = "what is the value of their open orders"
SEPTEMBER = {"BETWEEN": {"col": "commitments.delivered_at", "from": {"value": "2026-09-01"},
                         "to": {"value": "2026-09-30"}}}
LATE = {"label": "late_sep", "asked": FIRST, "plan": {
    "SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
               {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_lines"}],
    "FROM": "commitments",
    "WHERE": {"AND": [SEPTEMBER, {"EQ": [{"metric": "late_by_delivery"}, {"value": True}]}]},
    "GROUP_BY": ["supplier_id"], "ORDER_BY": [{"by": "late_lines", "dir": "DESC"}]}}


def ref(op="IN_READING", col="commitments.supplier_id", reading="late_sep", alias="supplier_id"):
    return {op: {"col": col, "reading": reading, "as": alias}}


def open_value(*conds, label="open_value", asked=SECOND):
    return {"label": label, "asked": asked, "plan": {
        "SELECT": [{"expr": {"func": "SUM", "arg": {"col": "commitments.line_value"}}, "as": "open_value"}],
        "FROM": "commitments",
        "WHERE": {"AND": [{"IS_NULL": {"col": "commitments.delivered_at"}}, *conds]}}}


def intent(*readings, **more):
    return {"disposition": "retrieve", "readings": [copy.deepcopy(r) for r in readings], **more}


def compile_(question, *readings, cat=CAT, user=APPROVER, **kw):
    return c.compile_intent(intent(*readings), question, user, cat, c.intent_validator(), **kw)


def rows(db_path, reading):
    with sqlite3.connect(db_path) as conn:
        return c.execute(reading, conn)


def sql(db_path, query, *params):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(query, params).fetchall()


SEP_LATE = ("SELECT DISTINCT supplier_id FROM commitments WHERE delivered_at BETWEEN '2026-09-01' AND '2026-09-30' "
            "AND delivered_at > due_date")


# --------------------------------------------------------------------------
# the compiler
# --------------------------------------------------------------------------

def test_h4_their_is_the_suppliers_the_first_reading_found(db_path):
    """GAP-04: "their" is those found late in September, not every supplier. The SQL written by hand agrees."""
    ci = compile_(H4, LATE, open_value(ref()))
    got = {r.label: rows(db_path, r) for r in ci.readings}
    want = sql(db_path, f"SELECT SUM(line_value) FROM commitments WHERE delivered_at IS NULL "
                        f"AND supplier_id IN ({SEP_LATE})")[0][0]
    every = sql(db_path, "SELECT SUM(line_value) FROM commitments WHERE delivered_at IS NULL")[0][0]
    assert got["open_value"].rows[0]["open_value"] == pytest.approx(want) and want != pytest.approx(every)
    assert {r["supplier_id"] for r in got["late_sep"].rows} == {s for (s,) in sql(db_path, SEP_LATE)}
    outer = ci.readings[1]
    assert outer.depends_on == ["late_sep"] and "EXISTS (SELECT 1 FROM (" in outer.sql


def _entity(col: str) -> str | None:
    """Written here, apart from the compiler: what a column identifies."""
    spec = CAT.column(col) or {}
    if spec.get("references"):
        return spec["references"]
    table = col.partition(".")[0]
    own = [k for k, s in CAT.entities[table]["columns"].items() if s.get("identifier") and not s.get("references")]
    return col if own and f"{table}.{own[0]}" == col else None


KEYS = sorted(f"{t}.{k}" for t, e in SCHEMA["entities"].items() for k, s in e["columns"].items()
              if s.get("identifier") or s.get("references"))


@pytest.mark.parametrize("op, test", [("IN_READING", "IN"), ("NOT_IN_READING", "NOT IN")])
@pytest.mark.parametrize("inner_col, outer_col", list(itertools.product(KEYS, KEYS)))
def test_every_pair_of_keys_compiles_exactly_where_both_identify_the_same_record(db_path, op, test, inner_col,
                                                                               outer_col):
    """A kind (L2): over every pair of identifier columns in the catalog, the condition compiles where both
    identify the same entity (a declared reference, or the entity's own key), and returns what SQL written by hand
    returns; everywhere else reading_ref_relation."""
    it, ic = inner_col.split(".")
    ot, oc = outer_col.split(".")
    inner = {"label": "found", "asked": "Q", "plan": {"SELECT": [{"expr": {"col": inner_col}, "as": "k"}],
                                                       "FROM": it}}
    outer = {"label": "outer", "asked": "Q", "plan": {"SELECT": [{"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}],
                                                       "FROM": ot, "WHERE": ref(op, outer_col, "found", "k")}}
    same = _entity(inner_col) is not None and _entity(inner_col) == _entity(outer_col)
    if not same:
        with pytest.raises(c.CompileError) as e:
            compile_("Q", inner, outer)
        assert e.value.code == "reading_ref_relation" and e.value.detail["path"] == "WHERE"
        return
    ci = compile_("Q", inner, outer)
    got = rows(db_path, ci.readings[1]).rows[0]["n"]
    nulls = "" if test == "IN" else f" AND {oc} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {it} WHERE {it}.{ic} " \
                                    f"= {ot}.{oc})"
    want = (sql(db_path, f"SELECT COUNT(*) FROM {ot} WHERE {oc} IN (SELECT {ic} FROM {it})")[0][0] if test == "IN"
            else sql(db_path, f"SELECT COUNT(*) FROM {ot} WHERE 1{nulls}")[0][0]
            + sql(db_path, f"SELECT COUNT(*) FROM {ot} WHERE {oc} IS NULL")[0][0])
    assert got == want


def test_a_limit_the_plan_wrote_is_kept_and_an_injected_one_is_not(db_path):
    """"The five suppliers with the most late lines, and their open orders": their are the five. Without a LIMIT
    written, their are every supplier found, beyond the rows shown."""
    top5 = copy.deepcopy(LATE)
    top5["plan"]["LIMIT"] = 5
    top5["plan"]["WHERE"] = {"EQ": [{"metric": "late_by_delivery"}, {"value": True}]}
    every = copy.deepcopy(top5)
    del every["plan"]["LIMIT"]
    five = compile_(H4, top5, open_value(ref()))
    whole = compile_(H4, every, open_value(ref()), row_limit=3)
    shown5 = {r["supplier_id"] for r in rows(db_path, five.readings[0]).rows}
    assert len(shown5) == 5 and "LIMIT ?" in five.readings[0].inner_sql
    assert five.readings[1].sql.count("LIMIT ?") == 2   # the inner reading's own, then the outer's
    assert "LIMIT" not in whole.readings[0].inner_sql and rows(db_path, whole.readings[0]).truncated
    inner_ids = ", ".join(f"'{s}'" for s in shown5)
    want5 = sql(db_path, f"SELECT SUM(line_value) FROM commitments WHERE delivered_at IS NULL "
                         f"AND supplier_id IN ({inner_ids})")[0][0]
    assert rows(db_path, five.readings[1]).rows[0]["open_value"] == pytest.approx(want5)
    want_all = sql(db_path, "SELECT SUM(line_value) FROM commitments WHERE delivered_at IS NULL AND supplier_id IN "
                            "(SELECT supplier_id FROM commitments WHERE delivered_at > due_date)")[0][0]
    assert rows(db_path, whole.readings[1]).rows[0]["open_value"] == pytest.approx(want_all)


def _planner_held():
    schema = copy.deepcopy(SCHEMA)
    schema["entities"]["commitments"]["entitlement"]["PLANNER"] = {"col": "planner_id", "equals": "user_id"}
    return c.Catalog(schema, RELATIONS)


@pytest.mark.parametrize("op", ["IN_READING", "NOT_IN_READING"])
def test_the_condition_reads_the_other_reading_within_what_the_user_holds(db_path, op):
    """NFR 17 inside EXISTS: a planner restricted to their own lines reads the other reading over those only, so
    the result is the same query written by hand with the restriction in both places."""
    cat = _planner_held()
    ci = compile_(H4, LATE, open_value(ref(op)), cat=cat, user=PLANNER)
    got = rows(db_path, ci.readings[1]).rows[0]["open_value"]
    neg = "NOT " if op == "NOT_IN_READING" else ""
    want = sql(db_path, f"SELECT SUM(line_value) FROM commitments WHERE delivered_at IS NULL AND planner_id = ? AND "
                        f"supplier_id {neg}IN ({SEP_LATE} AND planner_id = ?)", "PL-02", "PL-02")[0][0]
    assert got == pytest.approx(want)


@pytest.mark.parametrize("readings, code", [
    ([LATE, open_value(ref(reading="open_value"), label="open_value")], "reading_self"),
    ([LATE, open_value(ref(reading="nowhere"))], "unknown_reading"),
    ([LATE, open_value(ref(), label="middle"), open_value(ref(reading="middle"), label="outer")],
     "reading_ref_depth"),
    ([LATE, open_value({"OR": [ref(), {"EQ": [{"col": "commitments.week"}, {"value": 3}]}]})],
     "in_reading_under_or"),
    ([LATE, open_value(ref(alias="late_lines"))], "reading_ref_alias"),
    ([LATE, open_value(ref(alias="nothing"))], "reading_ref_alias"),
    ([LATE, open_value(ref(col="commitments.part_id"))], "reading_ref_relation"),
])
def test_each_rule_of_the_new_node_rejects_with_its_path_and_no_record_value(readings, code):
    with pytest.raises(c.CompileError) as e:
        compile_(H4, *readings)
    assert e.value.code == code and e.value.detail["path"].startswith("WHERE")
    assert not re.search(r"S-\d|PO-\d|2026-", e.value.reason)   # NFR 21: names and paths only


def test_a_reading_another_reads_is_never_an_alternative_of_an_ambiguous_term():
    """Chat 21.10, 12:50, question 1: the plan asks which was meant (needs_clarification) rather than doubling
    its readings; the same alternatives on a reading nobody reads stay as they were."""
    promise = copy.deepcopy(LATE)
    promise["label"] = "late_promise"
    promise["plan"]["WHERE"]["AND"][1] = {"EQ": [{"metric": "late_by_promise"}, {"value": True}]}
    late = {"ambiguous": [{"term": "late", "alternatives": [{"metric": "late_by_delivery"},
                                                            {"metric": "late_by_promise"}]}]}
    with pytest.raises(c.CompileError) as e:
        c.compile_intent(intent(LATE, promise, open_value(ref()), **late), H4, APPROVER, CAT, c.intent_validator())
    assert e.value.code == "reading_ref_ambiguous" and "needs_clarification" in e.value.reason
    c.compile_intent(intent(LATE, promise, **late), FIRST, APPROVER, CAT, c.intent_validator())


def test_in_holds_up_to_row_limit_values():
    """H7: the bound is ROW_LIMIT, the most identifiers one reading presents; tied in this test."""
    schema = json.loads((c.HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))
    comp = schema["schemas"]["intent_analysis"]["schema"]["$defs"]["comparison"]["oneOf"]
    values = next(o for o in comp if "IN" in o.get("properties", {}))["properties"]["IN"]["properties"]["values"]
    assert values["maxItems"] == c.ROW_LIMIT
    ph = next(x for x in schema["placeholders"] if x["pointer"].endswith("/IN/properties/values/maxItems"))
    assert ph["value"] == c.ROW_LIMIT


def weeks(n: int) -> dict:
    return {"label": "weeks", "asked": "What was delivered in those weeks", "plan": {
        "SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "commitment_id"}], "FROM": "commitments",
        "WHERE": {"IN": {"col": "commitments.week", "values": [{"value": w} for w in range(1, n + 1)]}}}}


def test_thirty_values_compile_and_thirty_one_are_a_limit_of_the_language():
    compile_("What was delivered in those weeks?", weeks(c.ROW_LIMIT))
    with pytest.raises(c.CompileError):
        compile_("What was delivered in those weeks?", weeks(c.ROW_LIMIT + 1))
    out = compile_("What was delivered in those weeks?", weeks(c.ROW_LIMIT + 1), drop_limits=True)
    assert out.readings == [] and out.dropped == [{"label": "weeks", "asked": "What was delivered in those weeks",
                                                   "code": "in_values_limit"}]


READINGS = [{"label": x, "asked": x, "plan": {"SELECT": [{"expr": {"col": "commitments.supplier_id"},
                                                          "as": "supplier_id"}], "FROM": "commitments"}}
            for x in ("a", "b", "c", "d")]


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
def test_readings_beyond_three_are_left_out_by_order_never_an_outer_without_its_inner(order):
    """Chat 21.10, 12:50: by order, but a reading kept always keeps the reading it reads. Over every order of four
    readings, two of which read another."""
    rs = [copy.deepcopy(READINGS[i]) for i in order]
    for r in rs:
        if r["label"] in ("b", "c"):
            r["plan"]["WHERE"] = ref(reading="d" if r["label"] == "b" else "a")
    out = c.compile_intent(intent(*rs), "a b c d", APPROVER, CAT, c.intent_validator(), drop_limits=True)
    kept = [r.label for r in out.readings]
    assert len(kept) <= 3 and {d["label"] for d in out.dropped} == {"a", "b", "c", "d"} - set(kept)
    assert ("b" not in kept or "d" in kept) and ("c" not in kept or "a" in kept)
    assert all(d["code"] == "readings_limit" for d in out.dropped)


def test_a_reading_that_reads_one_left_out_is_left_out_with_it():
    out = compile_(H4, LATE, open_value(ref(), label="middle"), open_value(ref(reading="middle"), label="outer"),
                   drop_limits=True)
    assert [r.label for r in out.readings] == ["late_sep", "middle"]
    assert out.dropped == [{"label": "outer", "asked": SECOND, "code": "reading_ref_depth"}]
