"""R-2 (chat 20.10, 16:40): a catalog's prompt words agree with its column marks. A word for a section the
catalog does not hold, or one that names a column without the section's mark, is refused when the prompts
are built. No model."""

import json
import shutil

import pytest

from demo_data import seed
from query_assistance import prompts as p
from tools import bird_eval

FILES = ("schema.json", "relations.json", "prompt_text.json")


def _catalog(tmp_path, source=seed.HERE):
    shutil.copytree(source / "examples", tmp_path / "examples")
    for f in FILES:
        shutil.copy(source / f, tmp_path / f)
    return tmp_path


def _edit(path, change):
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_each_slot_stands_under_the_sections_it_appears_in():
    conds = p.slot_conditions()
    assert conds["reason_reference"] == conds["recorded_reason_example"] == {"reasons"}
    assert conds["date_column_example"] == conds["event_dates"] == conds["scheduled_dates"] == {"dates"}
    assert conds["named_entities"] == {"searchable"}
    assert conds["subject_records"] == conds["many_rows_example"] == set()


def test_both_catalogs_pass_and_the_words_that_name_no_column_are_listed():
    """The demo's four words under a condition that name no column: a question for the chat (R-2)."""
    assert p.check_prompt_text(seed.HERE) == ["event_dates", "named_entities", "recorded_reason_example",
                                              "scheduled_dates"]
    assert p.check_prompt_text(bird_eval.HERE) == []


def test_a_reason_word_where_no_column_records_a_reason_is_refused(tmp_path):
    cat = _catalog(tmp_path)
    _edit(cat / "schema.json", lambda d: d["entities"]["commitments"]["columns"]["delay_reason"].pop("records_reason"))
    with pytest.raises(ValueError, match="is for reasons, which the catalog does not hold"):
        p.for_catalog("synthesis.md", cat)


def test_a_reason_reference_to_a_column_that_records_none_is_refused(tmp_path):
    """The catalog holds a reason column, but the word points at another one: alignment as a reason, in kind."""
    cat = _catalog(tmp_path)
    _edit(cat / "prompt_text.json", lambda d: d["texts"].update(reason_reference="{r.notes#1}"))
    with pytest.raises(ValueError, match="names notes, which is not a column of the kind reasons"):
        p.check_prompt_text(cat)


def test_a_date_word_naming_a_column_that_is_not_a_date_is_refused(tmp_path):
    cat = _catalog(tmp_path)
    _edit(cat / "prompt_text.json", lambda d: d["texts"].update(date_column_example="supplier_id for a supplier"))
    with pytest.raises(ValueError, match="supplier_id, which is not a column of the kind dates"):
        p.check_prompt_text(cat)


def test_a_reference_to_a_column_no_table_holds_is_refused(tmp_path):
    cat = _catalog(tmp_path)
    _edit(cat / "prompt_text.json", lambda d: d["texts"].update(reason_reference="{r.cause#1}"))
    with pytest.raises(ValueError, match="names cause, which no table of the catalog holds"):
        p.check_prompt_text(cat)


def test_superhero_given_a_reason_word_is_refused(tmp_path):
    cat = _catalog(tmp_path, bird_eval.HERE)
    _edit(cat / "prompt_text.json", lambda d: d["texts"].update(reason_reference="{r.alignment#1}"))
    with pytest.raises(ValueError, match="reason_reference is for reasons"):
        p.check_prompt_text(cat)
