"""G-TOTAL and R-3 (chat 20.10, 17:10): what one row of a reading is, said beside its count where it is not a
record. One function, contract.row_unit, from the plan and the catalog; the records synthesis and the gate
see, the history and Examined's "grouped by" use it. No model."""

import json

import pytest

from demo_data import seed
from query_assistance import contract
from query_assistance import prompts as p
from query_assistance.compiler import Catalog

CAT = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
SCRIPTS = json.loads((seed.HERE / "scripted_exchanges.json").read_text(encoding="utf-8"))["exchanges"]

GROUPED = {"SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
                      {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}],
           "FROM": "commitments", "GROUP_BY": ["supplier_id"]}
COMPUTED = {"SELECT": [{"expr": {"func": "SUM", "arg": {"col": "commitments.line_value"}}, "as": "open_value"}],
            "FROM": "commitments"}
PLAIN = {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments"}


def test_each_kind_of_reading_says_what_a_row_is():
    assert contract.row_unit(GROUPED, CAT) == p.RowUnit("group", "groups", "one per supplier")
    assert contract.row_unit(COMPUTED, CAT) == p.RowUnit("computed row", "computed rows")
    assert contract.row_unit(PLAIN, CAT) is None


def test_examined_says_grouped_by_from_the_same_names():
    assert "grouped by supplier" in contract.render_checked(GROUPED, CAT, "APPROVER")
    assert contract.row_unit(GROUPED, CAT).per == "one per " + ", ".join(contract.group_names(GROUPED, CAT))


def test_fu4_the_count_line_of_a_grouped_reading_counts_groups():
    """FU4 (stage, 20.10): one supplier's late lines, grouped by supplier. The line says one group, so the
    count in its row is not read against it."""
    rows = [{"supplier_id": "S-014", "late_count": 2}]
    text = p.render_records([("late", rows)], totals={"late": 1}, units={"late": contract.row_unit(GROUPED, CAT)})
    assert "[late] late.total = 1 group matched (one per supplier), 1 shown" in text
    assert "late.late_count = 2" in text


def test_a_plain_reading_keeps_rows_matched():
    text = p.render_records([("lines", [{"po_id": "PO-1"}])], totals={"lines": 64},
                           units={})
    assert "[lines] lines.total = 64 rows matched, 1 shown" in text


@pytest.mark.parametrize("shown, total, says", [(2, 2, "all 2 groups shown"), (30, 64, "30 of 64 groups shown"),
                                                (0, 0, "no rows"), (1, 1, "all 1 group shown")])
def test_the_history_says_groups(shown, total, says):
    note = p.ReadingNote("late", "purchase order lines · grouped by supplier", shown, total,
                         contract.row_unit(GROUPED, CAT))
    assert note.render().endswith(says)
    assert p.ReadingNote("lines", "purchase order lines", 2, 2).render().endswith("all 2 shown")


def _sent(question, history, db_path):
    import sqlite3

    from demo_data.scripted import ScriptedDemoGateway, load_scripts
    from persistence.audit_repository import AuditRepository, MemoryStorage
    from query_assistance import pipeline as pl
    from shared.circuit_breaker import CircuitBreaker, MemoryStore
    from tests.query_assistance.test_prompt_generality import Recording
    gw = Recording(ScriptedDemoGateway(question, load_scripts()))
    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pl.Deps(gw, "scripted", CAT, lambda: sqlite3.connect(db_path), audit,
                   p.load(seed.HERE / "intent_analysis.md"))
    out = pl.run(question, history, {"role": "APPROVER", "user_id": "AP-01"}, deps)
    return out, [t for _, t in gw.sent]


def test_only_grouped_or_computed_readings_change_what_the_demo_sends(db_path, monkeypatch):
    """Every demo question, with row_unit off (as before G-TOTAL) and on: the same calls and the same text,
    but for the count and history lines of grouped or computed readings, and the gate's rule 11."""
    by_question = {e["question"]: e for e in SCRIPTS}
    changed = set()
    for e in SCRIPTS:
        follows = e["expect"].get("follows")
        history = [_sent(follows, [], db_path)[0].exchange] if follows else []
        out, now = _sent(e["question"], history, db_path)
        with monkeypatch.context() as m:
            m.setattr(contract, "row_unit", lambda plan, cat: None)
            hist_before = [_sent(follows, [], db_path)[0].exchange] if follows else []
            out_before, before = _sent(e["question"], hist_before, db_path)
        assert out.response == out_before.response, e["question"]
        assert len(now) == len(before)
        kinds = {r["label"]: contract.row_unit(r["plan"], CAT) for r in (e.get("intent") or {}).get("readings", [])}
        if follows:
            kinds |= {r["label"]: contract.row_unit(r["plan"], CAT)
                      for r in (by_question[follows].get("intent") or {}).get("readings", [])}
        for a, b in zip(now, before, strict=True):
            if a == b:
                continue
            changed.add(e["question"])
            assert any(kinds.values()), e["question"]   # only where a reading is grouped or computed
            diff = [(x, y) for x, y in zip(a.splitlines(), b.splitlines(), strict=True) if x != y]
            assert all(("groups" in x or "group " in x or "computed row" in x) for x, _ in diff), diff
    assert changed, "no demo question has a grouped reading: G-TOTAL would change nothing"
