"""
The scripted answers name no place between rows that hold the same value (task 10a; chat, 14.10, 21:50).

R9 found that the scripted answer to question 2 said "had the most lines" of row 1 where row 1 and row 2
were tied, for AP-01 by delivery and for PL-02 by promise: the scripted demo showed the defect with no
model at all. One scripted text serves both roles, and each role gets other rows, so every scripted
sentence is checked for both, on the demo store.

The word list below reads text **we wrote**: the scripted answers in scripted_exchanges.json. It never
reads model output, so it does not break "Code does not read meaning" (CLAUDE.md, R9 and 10a); whether
a model's words fit the values is the gate's to judge.
"""

import json
import re
import sqlite3

import pytest

from demo_data import seed
from demo_data.scripted import SCRIPT_FILE
from query_assistance import compiler as c

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
USERS = [{"role": "APPROVER", "user_id": "AP-01"}, {"role": "PLANNER", "user_id": "PL-02"}]
EXCHANGES = json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))["exchanges"]

# Words that name a place or an order. Scripted text only (see above).
PLACE = re.compile(r"\b(most|least|highest|lowest|first|top|followed|then|next|ahead|behind)\b", re.I)
REF = re.compile(r"\{(\w+)\.(\w+)#(\d+)\}")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.db"
    seed.build(path)
    conn = sqlite3.connect(path)
    yield conn
    conn.close()


def _listed(v) -> list:
    return v if isinstance(v, list) else [v] if v else []


def sentences(e: dict) -> list[str]:
    """Every scripted sentence the exchange presents: the drafts in the order they are made (synthesis,
    a list where it runs twice, then the redraft), each judged by the scripted gate call of the same
    turn, the last repeating. A sentence the scripted gate does not support is withheld, never shown
    (questions 10 and 11 hold such a judgement on purpose), so it is not checked here."""
    drafts = _listed(e.get("synthesis")) + _listed(e.get("redraft"))
    gates = _listed(e.get("gate")) or [{}]
    out = []
    for k, d in enumerate(drafts):
        verdicts = gates[min(k, len(gates) - 1)].get("verdicts") or ["supported"]
        texts = [s["text"] for r in d.get("readings", []) for s in r["sentences"]]
        out += [t for i, t in enumerate(texts) if verdicts[min(i, len(verdicts) - 1)] == "supported"]
    return out


def test_a_sentence_the_scripted_gate_withholds_is_not_checked():
    q10 = next(e for e in EXCHANGES if e["question"].startswith("Which parts are below"))
    assert not any("most urgent" in t for t in sentences(q10)) and sentences(q10)


def test_the_check_sees_the_scripted_answers():
    assert sum(1 for e in EXCHANGES if sentences(e)) >= 6


@pytest.mark.parametrize("user", USERS, ids=lambda u: u["user_id"])
@pytest.mark.parametrize("e", [e for e in EXCHANGES if sentences(e)], ids=lambda e: e["question"][:40])
def test_no_scripted_place_between_rows_that_share_the_ordering_value(e, user, db):
    question = (e.get("rewrite") or {}).get("question") or e["question"]
    compiled = c.compile_intent(e["intent"], question, user, CAT)
    rows, order = {}, {}
    for r, spec in zip(compiled.readings, e["intent"]["readings"], strict=True):
        rows[r.label] = c.execute(r, db).rows
        by = (spec["plan"].get("ORDER_BY") or [{}])[0].get("by")
        if by:
            order[r.label] = by
    for text in sentences(e):
        if not PLACE.search(text):
            continue
        for label, _, n in REF.findall(text):
            by, got, i = order.get(label), rows.get(label, []), int(n) - 1
            if not by or i >= len(got):
                continue
            tied = [j + 1 for j in (i - 1, i + 1) if 0 <= j < len(got) and got[j].get(by) == got[i].get(by)]
            assert not tied, (f"{user['user_id']}: {text!r} names a place for {label} row {n}, "
                              f"which holds the same {by} as row(s) {tied}")
