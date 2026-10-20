"""Test 37, the chat's part (NFR 6): with more records than the rows a retrieval returns, a question that asks
how many is answered with the number of records, not the number of rows returned. {label.total} is the
query's own count before LIMIT (D5), filled by code; a grouped reading says what it counts (G-TOTAL). No model:
the scripted demo model and a plan run on the demo store."""

import json
import sqlite3

from demo_data import seed
from query_assistance import compiler as c
from query_assistance import synthesis as s
from tests.query_assistance.test_row_unit import _sent

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
SCRIPTS = json.loads((seed.HERE / "scripted_exchanges.json").read_text(encoding="utf-8"))["exchanges"]
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}


def test_how_many_over_a_cut_list_counts_the_records_not_the_rows_returned(db_path):
    """Test 37: a plain reading of purchase order lines, cut at LIMIT. The count in the answer is the records'."""
    plan = {"SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "cid"}], "FROM": "commitments",
            "LIMIT": 5}
    intent = {"disposition": "retrieve", "readings": [{"label": "lines", "plan": plan}]}
    r, = c.compile_intent(intent, "How many purchase order lines are there?", APPROVER, CAT).readings
    res = c.execute(r, sqlite3.connect(db_path))
    assert len(res.rows) == 5 and res.total > 5
    m = s.material("lines", plan, res.rows, res.truncated, CAT, total=res.total)
    assert s.fill_totals("{lines.total} lines are held.", {"lines": m}) == f"{res.total} lines are held."


def test_each_cut_demo_reading_states_its_whole_count(db_path):
    """Test 37 over the demo set: wherever a reading returned fewer rows than it matched and the answer gives
    {label.total}, what is presented is the whole count, never the rows returned."""
    seen = 0
    for e in SCRIPTS:
        if e["expect"].get("follows") or not e.get("synthesis"):
            continue
        drafts = e["synthesis"] if isinstance(e["synthesis"], list) else [e["synthesis"]]   # a redraft follows
        written = {rd["label"]: " ".join(x["text"] for x in rd["sentences"]) for rd in drafts[0]["readings"]}
        out, _ = _sent(e["question"], [], db_path)
        for rd in out.response.get("readings", []):
            if rd.get("total", 0) <= rd.get("row_count", 0) or "{%s.total}" % rd["label"] not in written.get(
                    rd["label"], ""):
                continue
            seen += 1
            assert rd["answer"].startswith(f"{rd['total']} "), (e["question"], rd["answer"])
            assert not rd["answer"].startswith(f"{rd['row_count']} ")
    assert seen, "no demo reading is cut below its total"
