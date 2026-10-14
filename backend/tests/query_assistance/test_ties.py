"""
Ties between ordered rows (task 10a; chat, 14.10, 21:50). R9 found "S-014 had the highest late count, 14.
S-036 followed with 14": a place named between equal values, which the gate let through.

Code marks the tie in the records the model reads, as a fact about the rows: "(same label.alias as row n)",
for the field the rows are ordered by, between rows that are shown. The prompts say what a place may be
named on; whether a model's words fit is the gate's to judge, never code's (CLAUDE.md, decisions in force).
These tests hold the marker and the pipeline's handling of a contradicted place; what a real model writes
is seen only against it (R9 procedure).
"""

import copy

import pytest

from persistence.audit_repository import MemoryStorage
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from query_assistance import synthesis as s
from tests.query_assistance.test_pipeline import APPROVER, LATE, deps, gate_says, model

# --------------------------------------------------------------------------
# the marker
# --------------------------------------------------------------------------

ROWS = [{"supplier_id": "S-014", "late_count": 14}, {"supplier_id": "S-036", "late_count": 14},
        {"supplier_id": "S-008", "late_count": 10}, {"supplier_id": "S-002", "late_count": 10}]


def test_a_row_tied_with_the_row_before_it_on_the_ordering_field_says_so():
    text = p.render_records([("late", ROWS)], {"late": "late_count"})
    lines = text.splitlines()
    assert lines[0].endswith("late.late_count = 14")
    assert lines[1].endswith("late.late_count = 14 (same late.late_count as row 1)")
    assert lines[2].endswith("late.late_count = 10")
    assert lines[3].endswith("(same late.late_count as row 3)")


def test_the_marker_names_a_field_and_a_row_only_and_needs_an_ordering():
    text = p.render_records([("late", ROWS)], {"late": "late_count"})
    markers = [ln.split(" (same ", 1)[1] for ln in text.splitlines() if " (same " in ln]
    assert markers == ["late.late_count as row 1)", "late.late_count as row 3)"]   # no value in it
    assert "(same" not in p.render_records([("late", ROWS)])                        # not ordered: nothing
    assert "(same" not in p.render_records([("late", ROWS)], {"late": "supplier_id"})
    empty = [{"supplier_id": "S-1", "late_count": None}, {"supplier_id": "S-2", "late_count": None}]
    assert "(same" not in p.render_records([("late", empty)], {"late": "late_count"})   # empty is not a tie


def test_the_ordering_field_comes_from_the_plan_that_ran():
    m = s.material("late", LATE["plan"], ROWS)
    assert s.ordered_by([m]) == {"late": "late_count"}
    assert s.ordered_by([s.Material("x", ROWS)]) == {}
    assert "(same late.late_count as row 1)" in s.render("q", [m])


# --------------------------------------------------------------------------
# through the pipeline, on the demo store: weeks 14 to 26, where AP-01's top two by delivery tie
# --------------------------------------------------------------------------

Q = "Which suppliers were late in weeks 14 to 26?"
LATE_14_26 = copy.deepcopy(LATE)
LATE_14_26["plan"]["WHERE"]["AND"][0] = {"BETWEEN": [{"col": "commitments.week"}, {"value": 14}, {"value": 26}]}
INTENT = {"disposition": "retrieve", "readings": [LATE_14_26]}
@pytest.fixture
def storage():
    return MemoryStorage()


PLACE = "{late.supplier_id#1} had the most late lines, {late.late_count#1}."
SHARE = "{late.supplier_id#2} had {late.late_count#2}."
SYN = {"readings": [{"label": "late", "sentences": [{"text": PLACE}, {"text": SHARE}]}]}
REDRAFT = {"readings": [{"label": "late", "sentences": [{"text": SHARE}]}]}


def run_tie(db_path, storage):
    seen: dict[str, list[str]] = {}

    def keep(name, out):
        def answer(prompt):
            seen.setdefault(name, []).append(prompt)
            return out(prompt) if callable(out) else out
        return answer
    gw = model(intent_analysis_demo=[INTENT], synthesis=[keep("synthesis", SYN)],
               support_scoring=[keep("gate", gate_says("contradicted", "supported", alias="late_count")),
                                keep("gate", gate_says("supported"))],
               synthesis_redraft=[keep("redraft", REDRAFT)])
    return pl.run(Q, [], APPROVER, deps(gw, db_path, storage)), seen


def test_the_tie_is_marked_for_synthesis_the_gate_and_the_redraft(db_path, storage):
    out, seen = run_tie(db_path, storage)
    rows = out.response["readings"][0]["rows"]
    assert rows[0]["late_count"] == rows[1]["late_count"], "the demo store holds the tie of R9"
    for name in ("synthesis", "gate", "redraft"):
        assert "[late] row 2: " in seen[name][0] and "(same late.late_count as row 1)" in seen[name][0], name


def test_a_place_the_gate_contradicts_is_withheld_and_the_rest_is_answered(db_path, storage):
    out, _ = run_tie(db_path, storage)
    r = out.response
    assert r["outcome"] == "answered" and r.get("withheld")
    assert "most" not in r["readings"][0]["answer"]


def test_the_marker_reaches_neither_the_history_nor_the_identifier_check(db_path, storage):
    """NFR 21 and NFR 27: the next question's rewriting and identifier check see the answer and the
    identifiers presented, never the records block, so never the marker."""
    out, _ = run_tie(db_path, storage)
    ex = out.exchange
    assert "(same" not in (ex.answer or "") and "same" not in " ".join(ex.presented)
    assert all(i.startswith("S-") for i in ex.presented)
