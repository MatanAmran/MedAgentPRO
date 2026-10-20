"""
The generality check on BIRD's superhero database (task 11b, chat 20.10): a second catalog, nothing else
new. Without the database (CI, a clean clone) every plan still compiles on the catalog and every gap is
refused as recorded; with it (tools/bird_fetch.py), every plan's rows equal BIRD's SQL. No model, no network.
"""

import hashlib

import pytest

from query_assistance.compiler import SHOW_ALL_LIMIT, CompileError, compile_intent
from tools import bird_eval as be
from tools import bird_fetch as bf

CAT = be.catalog()
DATA = be.load()


def test_the_catalog_declares_every_tables_entitlement_for_every_role():
    """'Everyone sees everything' is said, never assumed: a table with no rule is refused at compilation."""
    for table, ent in CAT.entities.items():
        assert ent["entitlement"] == {"PLANNER": {"all": True}, "APPROVER": {"all": True}}, table


def test_ten_questions_and_the_gaps_with_their_reasons():
    assert len(DATA["questions"]) == 10 and len({q["id"] for q in DATA["questions"]}) == 10
    assert all(g["reason"] for g in DATA["gaps"]) and len(DATA["gaps"]) >= 3


@pytest.mark.parametrize("q", DATA["questions"], ids=[str(q["id"]) for q in DATA["questions"]])
def test_every_plan_compiles_on_the_bird_catalog(q):
    ci = compile_intent(q["plan"], q["question"], be.USER, CAT, row_limit=SHOW_ALL_LIMIT)
    assert len(ci.readings) == 1 and ci.readings[0].sql.startswith("SELECT ")


@pytest.mark.parametrize("g", [g for g in DATA["gaps"] if "plan" in g], ids=lambda g: str(g["id"]))
def test_a_gap_with_a_plan_is_refused_as_recorded(g):
    with pytest.raises(CompileError) as e:
        compile_intent(g["plan"], g["question"], be.USER, CAT)
    assert e.value.code == g["compile"]


def test_every_plan_gives_birds_result_where_the_database_is_present():
    try:
        db = bf.superhero_db()
    except FileNotFoundError:
        pytest.skip("BIRD superhero is not here: python -m tools.bird_fetch --download")
    import sqlite3
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        failed = [r for r in (be.check(q, CAT, conn) for q in DATA["questions"]) if not r.ok]
    finally:
        conn.close()
    assert failed == [], failed


@pytest.mark.parametrize("mode, ours, gold, same", [
    ("value", [(347.96,)], [(347.9590163934426,)], True),
    ("value", [(347.9,)], [(347.9590163934426,)], False),
    ("set", [("a",), ("b",)], [("b",), ("a",), ("a",)], True),
    ("bag", [("a",), ("b",)], [("b",), ("a",), ("a",)], False),
    ("ordered", [("a",), ("b",)], [("b",), ("a",)], False),
])
def test_the_comparison(mode, ours, gold, same):
    assert be._same(ours, gold, mode) is same


def test_fetch_downloads_nothing_without_asking(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEDAGENTPRO_BIRD_DIR", str(tmp_path))
    monkeypatch.setattr(bf.urllib.request, "urlretrieve", lambda *a: pytest.fail("downloaded without --download"))
    assert bf.main([]) == 2 and "--download" in capsys.readouterr().out


def test_fetch_refuses_a_zip_that_is_not_the_recorded_one(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDAGENTPRO_BIRD_DIR", str(tmp_path))
    (tmp_path / "dev.zip").write_bytes(b"not BIRD")
    assert bf.main([]) == 1 and not (tmp_path / "superhero.sqlite").exists()


def test_a_database_that_is_not_the_recorded_one_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDAGENTPRO_BIRD_DIR", str(tmp_path))
    (tmp_path / "superhero.sqlite").write_bytes(b"x")
    assert hashlib.sha256(b"x").hexdigest() != bf.SUPERHERO_SHA256
    with pytest.raises(ValueError, match="SHA-256"):
        bf.superhero_db()


# --------------------------------------------------------------------------
# what BIRD found (chat 20.10): "the period", and the relation a table was joined through
# --------------------------------------------------------------------------

def _demo():
    from demo_data import seed
    from query_assistance.compiler import Catalog
    return Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")


def test_the_clarification_names_the_period_only_where_the_store_holds_a_date():
    from query_assistance import contract
    assert contract.needs_clarification_notice(_demo()) == (
        "The question could not be interpreted on its own. Ask it in full, naming what it concerns, such as "
        "the period, supplier or part.")   # the demo's notice, word for word as before
    notice = contract.needs_clarification_notice(CAT)
    assert "the period" not in notice and notice.endswith("such as superhero or alignment.")


def test_examined_names_the_key_where_two_relations_reach_the_same_table():
    from query_assistance import contract
    q721 = next(q for q in DATA["questions"] if q["id"] == 721)
    line = contract.render_checked(q721["plan"]["readings"][0]["plan"], CAT, "APPROVER")
    assert "superheroes with colours (eye colour) where colour = 'Blue'" in line


def test_each_reading_names_its_own_relation():
    """A reading joins a table once (duplicate_table), so eye and hair colour are two readings: each
    names the relation it joined through."""
    from query_assistance import contract
    lines = {}
    for rel, label in (("superhero_eye_colour", "eye"), ("superhero_hair_colour", "hair")):
        plan = {"SELECT": [{"expr": {"col": "superhero.superhero_name"}, "as": "superhero_name"}], "FROM": "superhero",
                "JOIN": [{"rel": rel, "type": "LEFT"}],
                "WHERE": {"EQ": [{"col": "colour.colour"}, {"value": "Blue"}]}}
        compile_intent({"disposition": "retrieve", "readings": [{"label": label, "plan": plan}]}, "Blue?", be.USER, CAT)
        lines[label] = contract.render_checked(plan, CAT, "APPROVER")
    assert "with colours (eye colour) where held" in lines["eye"]
    assert "with colours (hair colour) where held" in lines["hair"]


def test_no_demo_relation_shares_its_tables_so_the_demos_examined_is_unchanged():
    from query_assistance import contract
    demo = _demo()
    assert all(contract._join_role(name, demo) == "" for name in demo.relations)
