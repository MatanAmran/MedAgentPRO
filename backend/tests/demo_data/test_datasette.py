"""The demo data viewer (chat 19.10, 02:00): demo_data/datasette.yml held to its sources.

Each canned query is the plain SQL of a demo question's plan. It must return the rows the compiled plan
matches before LIMIT (real_eval's SQL for that question, `count_sql`), so the presenter's "ground truth"
is the same records the screen drew from. The labels are schema.json's readable names."""
import asyncio
import json
import re
import sqlite3
from collections import Counter

import pytest
import yaml
from datasette.app import Datasette

from api.state import DEMO_USERS
from demo_data import seed
from query_assistance import prompts
from query_assistance.compiler import Catalog, compile_intent
from tools import real_eval as ev

META_FILE = seed.HERE / "datasette.yml"
META = yaml.safe_load(META_FILE.read_text(encoding="utf-8"))
DB = META["databases"]["demo"]
SCHEMA = json.loads((seed.HERE / "schema.json").read_text(encoding="utf-8"))
CATALOG = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
# Questions the screen answers without retrieving (referred, declined): their query shows the records behind them
NO_READING = {"Q07", "Q12"}
COUNT_WRAP = re.compile(r"^SELECT COUNT\(\*\) FROM \((?P<inner>.*)\)$", re.S)


def _readings() -> dict[str, tuple[str, list]]:
    """Every reading of every demo question, as `qNN_label`: the compiled SQL before LIMIT, and its params."""
    items = ev.demo_set()
    by_id = {it["id"]: it for it in items}
    out = {}
    for it in items:
        user = {"user_id": it["user"], "role": DEMO_USERS[it["user"]]}
        for intent in it["plan"]:
            conv = prompts.compiler_conversation(ev._conversation(it, by_id), it["question"], it.get("rewritten"))
            for r in compile_intent(intent, conv, user, CATALOG).readings:
                inner = COUNT_WRAP.match(r.count_sql)
                assert inner, r.count_sql
                out[f"{it['id'].lower()}_{r.label}"] = (inner["inner"], r.count_params)
    return out


READINGS = _readings()


@pytest.fixture(scope="module")
def ro(db_path):
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    yield conn
    conn.close()


def test_the_metadata_file_is_ascii():
    """Datasette reads it in the locale's codepage (cp1255 on the demo machine): a raw dash is garbled."""
    assert META_FILE.read_bytes().isascii()
    assert all(" — " in q["title"] for q in DB["queries"].values())


def test_every_reading_of_every_demo_question_has_a_canned_query_and_nothing_else():
    assert len({k[:3] for k in READINGS} | {q.lower() for q in NO_READING}) == len(ev.demo_set())
    extra = {k for k in DB["queries"] if k not in READINGS}
    assert {k[:3] for k in extra} == {q.lower() for q in NO_READING}
    assert set(READINGS) <= set(DB["queries"])


@pytest.mark.parametrize("name", sorted(READINGS))
def test_a_canned_query_returns_the_rows_the_compiled_plan_matches_before_limit(ro, name):
    sql, params = READINGS[name]
    canned = ro.execute(DB["queries"][name]["sql"]).fetchall()
    compiled = ro.execute(sql, params).fetchall()
    assert len(canned) == ro.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]  # nosec B608
    assert Counter(canned) == Counter(compiled)


@pytest.mark.parametrize("name", sorted(DB["queries"]))
def test_every_canned_query_runs_and_is_titled_by_its_question(ro, name):
    q = DB["queries"][name]
    ro.execute(q["sql"]).fetchall()
    assert q["title"].startswith(f"Q{int(name[1:3])} — ")
    assert "LIMIT" not in q["sql"].upper()


def test_the_readings_returning_rows_are_not_all_empty(ro):
    """A canned query that matches nothing compares equal to a broken plan; most must hold rows."""
    empty = [n for n in READINGS if not ro.execute(DB["queries"][n]["sql"]).fetchall()]
    assert sorted(empty) == ["q08_supplied", "q09_supplied"]


def test_tables_and_columns_carry_the_readable_names_of_the_schema():
    entities = SCHEMA["entities"]
    assert set(DB["tables"]) == set(entities)
    for name, table in DB["tables"].items():
        assert table["description"] == entities[name]["label"]
        assert table["columns"] == {c: spec["label"] for c, spec in entities[name]["columns"].items()}
        assert set(table.get("facets", [])) <= set(entities[name]["columns"])


def test_the_facets_asked_for_are_each_on_some_table():
    facets = {f for t in DB["tables"].values() for f in t.get("facets", [])}
    assert facets == {"supplier_id", "week", "status", "category", "country", "region", "planner_id"}


def _get(path: str, db_path):
    async def go():
        ds = Datasette(immutables=[str(db_path)], metadata=META,
                       static_mounts=[("assets", str(seed.HERE / "datasette"))])
        return await ds.client.get(path)
    return asyncio.run(go())


def test_the_viewer_serves_tables_and_canned_queries_and_refuses_arbitrary_sql(db_path):
    assert db_path.stem == "demo"   # Datasette names the database after the file
    assert _get("/demo/commitments.json?_size=1", db_path).status_code == 200
    assert _get("/demo/q01_late_by_delivery.json", db_path).status_code == 200
    assert _get("/demo.json?sql=select+1", db_path).status_code == 403
    assert _get("/assets/demo.css", db_path).status_code == 200
