"""
The set by pipeline stage (task 14, chat 20.10, docs/notes/82_STAGE_SET.md) and what tools/real_eval.py does
with it, with no model: references into the other sets resolve to their questions and plans, every row of
the set holds questions, a plan written to meet a rejection passes when it meets it, a known gap is counted
as one, and the report goes by category.
"""

import json
import sqlite3

import pytest

from demo_data import seed
from query_assistance.compiler import HERE as QA_HERE
from query_assistance.compiler import Catalog
from tools import real_eval as ev

CATALOG = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
NOTE = ev.REPO / "docs" / "notes" / "82_STAGE_SET.md"


@pytest.fixture(scope="module")
def conn(db_path):
    c = sqlite3.connect(db_path)
    yield c
    c.close()


@pytest.fixture(scope="module")
def items():
    return ev.load_stage_set(ev.STAGE_SET_FILE, CATALOG)


def raw():
    return json.loads(ev.STAGE_SET_FILE.read_text(encoding="utf-8"))


def write(tmp_path, exchanges):
    path = tmp_path / "stage.json"
    path.write_text(json.dumps({"exchanges": exchanges}), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# the set
# --------------------------------------------------------------------------

def test_every_row_holds_about_three_questions(items):
    counts = {c: sum(it["category"] == c for it in items) for c in ev.CATEGORIES}
    assert all(3 <= n <= 7 for n in counts.values()), counts
    # MP4 (H4 by ref) joined in task 4 (chat 20.10, 22:30); MP5, MP6 (H1a, H1b) and QB4 (H7) in task 3 (chat 21.10,
    # 12:50), and MP7 (H4 with no ambiguous term, chat 15:50), so the measurement runs them
    assert len(items) == 43


def test_a_reference_takes_its_question_plan_and_expectations_from_its_own_set(items):
    by_id = {it["id"]: it for it in items}
    q01 = next(it for it in ev.demo_set() if it["id"] == "Q01")
    assert by_id["CA1"]["question"] == q01["question"] and by_id["CA1"]["plan"] == q01["plan"]
    assert by_id["CA1"]["expect"]["status"] == q01["expect"]["status"]
    assert by_id["CA1"]["expect"]["readings"] == 2   # the stage set's own expectation, added
    stored = next(e for e in raw()["exchanges"] if e["id"] == "CA1")
    assert set(stored) == {"id", "category", "ref", "expect"}   # never a copy (decision 3)


def test_a_followed_question_is_followed_here(items):
    by_id = {it["id"]: it for it in items}
    assert by_id["FU2"]["after"] == "FU1" and by_id["FU3"]["after"] == "FU2"
    assert by_id["FU4"]["after"] == "TR1"   # offscript F2 follows A1, held here as TR1


@pytest.mark.parametrize("entry, says", [
    ({"id": "X1", "category": "row_limit", "ref": "offscript:Z9"}, "no question offscript:Z9"),
    ({"id": "X1", "category": "follow_ups", "ref": "offscript:F1b"}, "which this set does not hold before it"),
    ({"id": "X1", "category": "nowhere", "ref": "demo:Q01"}, "is not a row of the stage set"),
    ({"id": "X1", "category": "row_limit", "ref": "demo:Q01", "expected_gap": {"id": "g", "where": "prompt"}},
     "expected_gap needs"),
    ({"id": "X1", "category": "row_limit", "ref": "demo:Q01", "expect": {"result": ["rows_found"]}}, "expect.result"),
])
def test_a_broken_stage_set_is_refused(tmp_path, entry, says):
    with pytest.raises(ev.SetError, match=says):
        ev.load_stage_set(write(tmp_path, [entry]), CATALOG)


def test_no_new_question_of_the_set_is_in_a_prompt():
    """The held-out rule (11:40) holds here too, the intent prompt for tests (@10) included."""
    texts = [p.read_text(encoding="utf-8").casefold() for p in (QA_HERE / "templates").glob("*.md")]
    texts += [(seed.HERE / f).read_text(encoding="utf-8").casefold()
              for f in ("intent_analysis.md", "intent_analysis_demo_v10.md")]
    for e in raw()["exchanges"]:
        if "question" in e:
            assert not any(e["question"].casefold() in t for t in texts), e["id"]


def test_the_note_names_every_question_and_every_row(items):
    note = NOTE.read_text(encoding="utf-8")
    for it in items:
        assert f"`{it['id']}`" in note, it["id"]
    for c in ev.CATEGORIES:
        assert f"`{c}`" in note, c


# --------------------------------------------------------------------------
# --plans on the set
# --------------------------------------------------------------------------

def test_every_question_has_a_plan_that_passes_but_the_known_plan_gap(items, conn):
    results = ev.check_plans(items, CATALOG, conn)
    assert ev.without_plan(items, results) == [], ev.plans_table(results)
    failing = sorted({r.id for r in results} - {r.id for r in results if r.passed})
    assert failing == []   # task 3 closed the last known gap in the plan language, QB3 (NOT_IN_READING)


def test_a_plan_written_to_meet_a_rejection_passes_when_it_meets_it(items, conn):
    vr1 = next(it for it in items if it["id"] == "VR1")
    r = ev.check_plans([vr1], CATALOG, conn)[0]
    assert r.passed and r.status == "compile:unknown_column"
    other = dict(vr1, expect={**vr1["expect"], "compile": "match_not_top_level"})
    wrong = ev.check_plans([other], CATALOG, conn)[0]
    assert not wrong.passed and wrong.error.startswith("CompileError unknown_column")


def test_a_name_is_resolved_as_the_pipeline_resolves_it(items, conn):
    """--plans goes through pipeline._resolve: MATCHES runs (VM1), a name with several matches stops its
    reading (VM2), and s-001 is read as S-001 (VM4)."""
    by_id = {it["id"]: it for it in items}
    for i, result in (("VM1", "rows_returned"), ("VM2", "name_ambiguous"), ("VM4", "rows_returned")):
        resp = ev.plan_response(by_id[i], by_id[i]["plan"][0], [], CATALOG, conn)
        assert [rd["result"] for rd in resp["readings"]] == [result], i
    vm1 = ev.plan_response(by_id["VM1"], by_id["VM1"]["plan"][0], [], CATALOG, conn)
    assert "supplier name 'apex' matched Apex Polymers (by prefix)" in vm1["readings"][0]["checked"]


def test_a_stage_that_does_not_hold_fails_and_is_named(items, conn):
    sl1 = next(it for it in items if it["id"] == "SL1")
    wrong = dict(sl1, expect={**sl1["expect"], "readings": 2, "result": ["empty_no_match"]})
    r = ev.check_plans([wrong], CATALOG, conn)[0]
    assert not r.passed and r.stages["readings"] is False and r.stages["result"] is False
    assert "❌ readings, result" in ev.plans_table([r])


def test_the_report_goes_by_category(items, conn):
    table = ev.category_table(items, ev.check_plans(items, CATALOG, conn))
    row = {line.split(" | ")[0].strip("| "): line for line in table.splitlines()[2:]}
    assert set(row) == set(ev.CATEGORIES)
    assert "QB3" not in row["query_building"] and "VM1, VM2" in row["value_mapping"]   # task 3 closed QB3
    assert "MP3" in row["multi_part"]


def test_the_plans_command_on_the_stage_set(db_path, capsys):
    assert ev.main(["--plans", "--set", "stage", "--db", str(db_path)]) == 0
    out = capsys.readouterr().out
    assert "43 of 43 questions have a plan that passes" in out
    assert "| category | questions | pass |" in out
