"""
The demo store: built against demo_data/schema.json, deterministic, holds the edge
cases the demo relies on, and works with the compiler and the prompt rendering unchanged.
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from demo_data import seed
from query_assistance import compiler as c
from query_assistance import prompts as p

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
PLANNER = {"role": "PLANNER", "user_id": "PL-02"}
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.db"
    seed.build(path)
    conn = sqlite3.connect(path)
    yield conn
    conn.close()


def one(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def dump(path):
    conn = sqlite3.connect(path)
    h = hashlib.sha256()
    for line in conn.iterdump():
        h.update(line.encode())
    conn.close()
    return h.hexdigest()


# --------------------------------------------------------------------------
# the store matches its schema
# --------------------------------------------------------------------------

def test_tables_and_columns_match_schema(db):
    for table, ent in CAT.entities.items():
        cols = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
        assert cols == list(ent["columns"]), table


def test_every_relation_joins_on_existing_columns():
    for name, rel in CAT.relations.items():
        for a, b in rel["on"]:
            assert CAT.column(a) and CAT.column(b), name


def test_every_metric_is_on_a_declared_entity():
    for name, m in CAT.metrics.items():
        assert m["entity"] in CAT.entities, name


def test_values_have_their_declared_types(db):
    for table, ent in CAT.entities.items():
        for col, spec in ent["columns"].items():
            for (v,) in db.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT 200'):
                assert c._literal_fits(spec["type"], v), (table, col, v)


def test_build_is_deterministic(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    seed.build(a)
    seed.build(b)
    assert dump(a) == dump(b)


# --------------------------------------------------------------------------
# what the demo relies on is actually in the data
# --------------------------------------------------------------------------

def test_two_readings_of_late_diverge(db):
    by_delivery = one(db, "SELECT COUNT(*) FROM commitments WHERE delivered_at > due_date")
    by_promise = one(db, "SELECT COUNT(*) FROM commitments WHERE promised_date > due_date")
    assert by_delivery > 0 and by_promise > 0 and by_delivery != by_promise


def test_delay_reason_is_rare(db):
    late = one(db, "SELECT COUNT(*) FROM commitments WHERE delivered_at > due_date")
    reasons = one(db, "SELECT COUNT(*) FROM commitments WHERE delay_reason IS NOT NULL")
    assert 0 < reasons <= 0.12 * late
    assert one(db, "SELECT COUNT(*) FROM commitments WHERE delay_reason IS NOT NULL "
                   "AND NOT (delivered_at > due_date)") == 0


@pytest.mark.parametrize("what, sql", [
    ("orders open past due", "SELECT COUNT(*) FROM commitments c JOIN purchase_orders o USING(po_id) "
                             "WHERE o.status = 'open' AND c.due_date < '2026-10-09' AND c.received_qty < c.quantity"),
    ("closed with no receipt held", "SELECT COUNT(*) FROM commitments c JOIN purchase_orders o USING(po_id) "
                                    "WHERE o.status = 'closed' AND c.received_qty = 0"),
    ("received short", "SELECT COUNT(*) FROM commitments WHERE received_qty BETWEEN 1 AND quantity - 1"),
    ("split receipts", "SELECT COUNT(*) FROM (SELECT commitment_id FROM receipts GROUP BY 1 HAVING COUNT(*) > 1)"),
    ("cancelled orders", "SELECT COUNT(*) FROM purchase_orders WHERE status = 'cancelled'"),
    ("no promised date", "SELECT COUNT(*) FROM commitments WHERE promised_date IS NULL"),
    ("order notes", "SELECT COUNT(*) FROM purchase_orders WHERE notes IS NOT NULL"),
    ("orders without notes", "SELECT COUNT(*) FROM purchase_orders WHERE notes IS NULL"),
    ("zero stock", "SELECT COUNT(*) FROM parts WHERE on_hand = 0"),
    ("below reorder point", "SELECT COUNT(*) FROM parts WHERE on_hand < reorder_point"),
    ("no bin location", "SELECT COUNT(*) FROM parts WHERE bin_location IS NULL"),
    ("parts with no forecast", "SELECT COUNT(*) FROM parts p WHERE NOT EXISTS "
                               "(SELECT 1 FROM forecast f WHERE f.part_id = p.part_id)"),
    ("supplier with no orders", "SELECT COUNT(*) FROM suppliers s WHERE NOT EXISTS "
                                "(SELECT 1 FROM commitments c WHERE c.supplier_id = s.supplier_id)"),
])
def test_edge_case_present(db, what, sql):
    assert one(db, sql) > 0, what


def test_received_qty_matches_receipts(db):
    bad = one(db, "SELECT COUNT(*) FROM commitments c WHERE received_qty <> "
                  "(SELECT COALESCE(SUM(quantity), 0) FROM receipts r WHERE r.commitment_id = c.commitment_id)")
    assert bad == 0


def test_line_value_is_quantity_times_price(db):
    assert one(db, "SELECT COUNT(*) FROM commitments WHERE ABS(line_value - quantity * unit_price) > 0.01") == 0


def test_lines_carry_their_order_supplier_and_planner(db):
    assert one(db, "SELECT COUNT(*) FROM commitments c JOIN purchase_orders o USING(po_id) "
                   "WHERE c.supplier_id <> o.supplier_id OR c.planner_id <> o.planner_id") == 0


def test_every_planner_holds_lines(db):
    assert one(db, "SELECT COUNT(DISTINCT planner_id) FROM commitments") == len(seed.PLANNERS)


# --------------------------------------------------------------------------
# the pipeline runs on it unchanged
# --------------------------------------------------------------------------

def run(db, plan, user=APPROVER, question="Which suppliers were late in September?", cat=None):
    intent = {"disposition": "retrieve", "readings": [{"label": "r", "plan": plan}]}
    compiled = c.compile_intent(intent, question, user, cat or CAT)
    return c.execute(compiled.readings[0], db)


LATE_IN_SEPTEMBER = {
    "SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
               {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}],
    "FROM": "commitments",
    "WHERE": {"AND": [{"BETWEEN": {"col": "commitments.delivered_at", "from": {"value": "2026-09-01"},
                                   "to": {"value": "2026-09-30"}}},
                      {"EQ": [{"metric": "late_by_delivery"}, {"value": True}]}]},
    "GROUP_BY": ["supplier_id"],
    "ORDER_BY": [{"by": "late_count", "dir": "DESC"}],
}


def test_compiler_runs_a_grouped_metric_query(db):
    res = run(db, LATE_IN_SEPTEMBER)
    assert res.outcome == "rows_returned" and res.rows[0]["late_count"] >= res.rows[-1]["late_count"]


def test_entitlement_restricts_a_planner_to_own_lines(db):
    """On the restricted test catalog (D6): the store holds lines of several planners, so the mechanism
    narrows a planner's count. In the demo itself both roles count every line."""
    restricted = c.Catalog.load(Path(__file__).parents[1] / "fixtures" / "restricted_schema.json",
                                seed.HERE / "relations.json")
    plan = {"SELECT": [{"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}], "FROM": "commitments"}
    own = run(db, plan, user=PLANNER, cat=restricted).rows[0]["n"]
    assert own == one(db, "SELECT COUNT(*) FROM commitments WHERE planner_id = 'PL-02'")
    assert own < run(db, plan).rows[0]["n"] == run(db, plan, user=PLANNER).rows[0]["n"]


def test_join_through_order_to_status(db):
    plan = {"SELECT": [{"expr": {"func": "SUM", "arg": {"col": "commitments.line_value"}}, "as": "open_value"}],
            "FROM": "commitments",
            "JOIN": [{"rel": "commitment_order", "type": "INNER"}],
            "WHERE": {"EQ": [{"col": "purchase_orders.status"}, {"value": "open"}]}}
    assert run(db, plan, question="What is the value of open orders?").rows[0]["open_value"] > 0


def test_unknown_supplier_is_reported_missing(db):
    plan = {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
            "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-999"}]}}
    res = run(db, plan, question="What did S-999 deliver?")
    assert res.outcome == "empty_unknown_entity"


def test_supplier_with_no_lines_is_no_match(db):
    """S-040 is held in suppliers and has no line. Existence is checked where the supplier
    is held (commitments.supplier_id references suppliers.supplier_id), so nothing matched."""
    plan = {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
            "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-040"}]}}
    res = run(db, plan, question="What did S-040 deliver?")
    assert res.outcome == "empty_no_match" and res.missing == []


def test_every_reference_resolves_in_the_data(db):
    for table, ent in CAT.entities.items():
        for col, spec in ent["columns"].items():
            if "references" in spec:
                t2, _, c2 = spec["references"].partition(".")
                orphans = one(db, f'SELECT COUNT(*) FROM "{table}" a WHERE a."{col}" IS NOT NULL AND NOT EXISTS '
                                  f'(SELECT 1 FROM "{t2}" b WHERE b."{c2}" = a."{col}")')
                assert orphans == 0, (table, col)


def test_schema_renders_for_the_intent_prompt():
    text = p.render_schema(CAT, "PLANNER")
    for table in CAT.entities:
        assert f"CREATE TABLE {table}" in text
    assert "metric late_by_promise" in text and "relation commitment_order" in text


# --------------------------------------------------------------------------
# readable names (task 7 review, item 2)
# --------------------------------------------------------------------------

def test_every_table_column_and_metric_has_a_readable_label():
    """A new column with no label would be shown by its technical name in Examined and the headers."""
    missing = [t for t, e in CAT.entities.items() if not e.get("label")]
    missing += [f"{t}.{col}" for t, e in CAT.entities.items() for col, s in e["columns"].items() if not s.get("label")]
    missing += [f"metric {m}" for m, s in CAT.metrics.items() if not s.get("label")]
    assert not missing, f"add a label in demo_data/schema.json: {missing}"


def test_a_label_is_shared_only_by_columns_of_the_same_name():
    """Two different columns under one label would make Examined ambiguous, the one defence against a
    plausible but wrong column (8.1). supplier_id in several tables may share "supplier"; quantity on an
    order line and on a goods receipt may not share a label."""
    by_label: dict[str, set[str]] = {}
    for e in CAT.entities.values():
        for col, s in e["columns"].items():
            by_label.setdefault(s["label"], set()).add(col)
    assert {lab: cols for lab, cols in by_label.items() if len(cols) > 1} == {}
    tables = [e["label"] for e in CAT.entities.values()]
    assert len(tables) == len(set(tables))
