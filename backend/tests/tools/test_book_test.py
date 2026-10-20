"""L13 (chat 20.10, 17:35): a question in an evaluation set names the book test it checks (book_test); real_eval
reports by book test, and path_map shows, beside the scripted column, the same book test from the latest recorded
run, replayed with no model. On the scripted model only."""

import pytest

import main
from tools import path_map
from tools import real_eval as ev
from tools.book_tests import BOOK_TESTS


def _item(**kw):
    return {"id": "X1", "user": "AP-01", "question": "q", "expect": {"status": ["answered"]}, **kw}


@pytest.mark.parametrize("value, held", [(22, [22]), ([37, 17], [37, 17]), (None, [])])
def test_book_test_is_a_number_or_a_list(value, held):
    assert ev.book_tests_of(_item(book_test=value)) == held
    if value is not None:
        ev.check_items([_item(book_test=value)])


@pytest.mark.parametrize("value", [99, "22", [22, "37"], [], True])
def test_a_book_test_the_book_table_does_not_hold_is_refused(value):
    with pytest.raises(ev.SetError, match="book_test"):
        ev.check_items([_item(book_test=value)])


def test_every_set_names_only_book_tests_of_the_table():
    """The sets load (load_* checks book_test), and the questions the chat named carry theirs (17:35)."""
    known = {t.number for t in BOOK_TESTS}
    sets = {"offscript": ev.load_set(), "stage": ev.load_stage_set(), "demo": ev.demo_set()}
    tagged = {f"{name}:{it['id']}": ev.book_tests_of(it) for name, items in sets.items() for it in items
              if ev.book_tests_of(it)}
    assert tagged and all(set(v) <= known for v in tagged.values())
    assert 22 in tagged["offscript:E1"] and {37, 17} <= set(tagged["stage:FU4"])
    assert tagged["stage:FU4"] == tagged["offscript:F2"]   # a ref takes its source's


def _result(id_, book, status_ok=True):
    r = ev.Result(id_, "AP-01", "q", None, {"status": ["answered"]}, None, book_test=book)
    r.status_ok = status_ok
    return r


def test_the_report_is_by_book_test_and_one_failure_fails_the_test():
    results = [_result("A", [22]), _result("B", [22, 37], status_ok=False), _result("C", [])]
    assert ev.by_book_test(results) == {22: {"passed": ["A"], "failed": ["B"]}, 37: {"passed": [], "failed": ["B"]}}
    table = ev.book_table(ev.Run("scripted", "m", 1.0, "now", results))
    assert "| 22 | ❌ | A, B | B |" in table and "| 37 | ❌ | B | B |" in table


def test_path_map_replays_the_latest_recorded_run_by_book_test(db_path, tmp_path):
    """A recorded run of Q01 and Q02 (scripted, no model), replayed: 32 and 44 measured, the rest not."""
    build = lambda mode, db: main.build_deps(mode, db)   # noqa: E731
    args = ("--only", "Q01,Q02", "--db", str(db_path))
    assert ev.main(["--set", "demo", *args, "--out", str(tmp_path / "out"), "--cache", str(tmp_path / "cache"),
                    "--record"], build=build) == 0
    by_test, measured = path_map.real_results([("demo", [])], tmp_path / "cache", args, build)
    assert measured == ["demo"]
    assert by_test == {32: {"passed": ["demo:Q01"], "failed": []}, 44: {"passed": ["demo:Q02"], "failed": []}}
    text = "\n".join(path_map.book_section((by_test, measured)))
    assert "✅ 1: demo:Q01" in text and "לא נמדד" in text


def test_nothing_recorded_is_not_measured(tmp_path):
    assert path_map.real_results([("demo", [])], tmp_path / "empty") == ({}, [])


def test_a_partial_run_does_not_hide_the_whole_sets_run(tmp_path):
    """--only reruns (L6) write a newer manifest of a few exchanges; a replay of more takes the older full run."""
    import json
    folder = tmp_path / "manifests"
    folder.mkdir()
    ids = {"intent": "i@1"}
    for stamp, n in (("20261020-100000", 14), ("20261020-110000", 2)):
        (folder / f"{stamp}-demo.json").write_text(json.dumps(
            {"run": f"{stamp}-demo", "set": "demo", "prompts": ids, "exchanges": n}), encoding="utf-8")
    assert ev.latest_manifest(tmp_path, "demo", ids, at_least=14)["run"] == "20261020-100000-demo"
    assert ev.latest_manifest(tmp_path, "demo", ids, at_least=2)["run"] == "20261020-110000-demo"
