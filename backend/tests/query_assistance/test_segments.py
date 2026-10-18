"""
answer_segments (UI refresh, phase C, chat 18.10) [demo extension]: the answer of a reading cut where code
filled a reference. Joined, the texts are the answer exactly; a ref names the alias and the 1-based row it was
filled from; a value from a free-text column is never a ref (NFR 21).
"""

import json
from pathlib import Path

import pytest

from demo_data import seed
from query_assistance import synthesis
from query_assistance.compiler import Catalog
from query_assistance.synthesis import Draft, Sentence

CAT = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
SNAPSHOTS = Path(__file__).parents[1] / "golden" / "snapshots"
GOLDEN = sorted(SNAPSHOTS.glob("response__*.json"))

PLAN = {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"},
                   {"expr": {"col": "commitments.delay_reason"}, "as": "delay_reason"},
                   {"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}],
        "FROM": "commitments"}
ROWS = [{"po_id": "PO-1", "delay_reason": "Quality hold", "n": 3}, {"po_id": "PO-2", "delay_reason": None, "n": 1.5}]


def draft(*templates: str, label: str = "r") -> Draft:
    m = synthesis.material(label, PLAN, ROWS, cat=CAT)
    sentences = [Sentence(t, [(mt.group(1), mt.group(2), int(mt.group(3))) for mt in synthesis.REF_RE.finditer(t)])
                 for t in templates]
    return Draft({label: sentences}, {label: m})


def test_joined_segments_are_the_answer():
    d = draft("{r.po_id#1} had {r.n#1} lines.", "{r.po_id#2} had {r.n#2}.")
    segs = d.segments("r", CAT)
    assert "".join(s["text"] for s in segs) == d.answer("r", CAT) == "PO-1 had 3 lines. PO-2 had 1.5."
    assert [s.get("ref") for s in segs if "ref" in s] == [
        {"alias": "po_id", "row": 1}, {"alias": "n", "row": 1}, {"alias": "po_id", "row": 2}, {"alias": "n", "row": 2}]


def test_a_free_text_value_is_never_a_ref():
    d = draft("{r.po_id#1}: Reason recorded: {r.delay_reason#1}.")
    segs = d.segments("r", CAT)
    assert segs == [{"ref": {"alias": "po_id", "row": 1}, "text": "PO-1"},
                    {"text": ": Reason recorded: Quality hold."}]


def test_adjacent_texts_merge_and_nothing_is_empty():
    segs = draft("{r.po_id#1}{r.n#1}", "No value here.").segments("r", CAT)
    assert all(s["text"] for s in segs)
    assert segs[-1] == {"text": " No value here."}


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_every_golden_answer_is_its_segments(path):
    resp = json.loads(path.read_text(encoding="utf-8"))
    for r in resp["readings"]:
        if "answer" not in r:
            assert "answer_segments" not in r
            continue
        segs = r["answer_segments"]
        assert "".join(s["text"] for s in segs) == r["answer"]
        for s in segs:
            if "ref" in s:
                ref = s["ref"]
                assert ref["alias"] in r["columns"] and 1 <= ref["row"] <= r["row_count"]
                assert synthesis.format_value(r["rows"][ref["row"] - 1][ref["alias"]]) == s["text"]
