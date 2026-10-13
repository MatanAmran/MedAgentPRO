"""
The demo intent prompt (demo_data/intent_analysis.md). Every example passes the schema,
compiles against the demo catalog and runs on the demo store, so an example cannot teach
something the system rejects or that the demo data contradicts.
"""

import json
import re
import sqlite3

import pytest

from demo_data import seed
from query_assistance import compiler as c
from query_assistance import prompts as p

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
PROMPT = p.load(seed.HERE / "intent_analysis.md")


def examples():
    body = PROMPT.body.split("Examples:", 1)[1]
    return [(re.search(r"Question: (.*)", m.group(1)).group(1), json.loads(m.group(2)))
            for m in re.finditer(r"((?:.+\n)+?)Output: (\{.*\})\n", body)]


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.db"
    seed.build(path)
    conn = sqlite3.connect(path)
    yield conn
    conn.close()


def test_prompt_metadata():
    assert PROMPT.output_schema == "intent_analysis" and PROMPT.identity == "intent_analysis_demo@3"
    assert "—" not in PROMPT.body and "–" not in PROMPT.body


def test_examples_cover_each_kind():
    kinds = {("refer" if o["disposition"] == "refer_to_investigation" else
              "action" if o["disposition"] == "out_of_scope_action" else
              "ambiguous" if o.get("ambiguous") else
              "unresolved" if o.get("unresolved") else
              "join" if any("JOIN" in r["plan"] for r in o["readings"]) else "plain")
             for _, o in examples()}
    assert {"refer", "action", "ambiguous", "unresolved", "join", "plain"} <= kinds


@pytest.mark.parametrize("question, output", examples())
def test_example_validates_compiles_and_runs(db, question, output):
    compiled = c.compile_intent(output, question, {"role": "APPROVER"}, CAT)
    for r in compiled.readings:
        res = c.execute(r, db)
        assert res.outcome != "empty_unknown_entity", (question, res.missing)


def test_an_unresolved_gap_names_no_column_the_demo_holds():
    """The reason the demo needs its own prompt: the original teaches that a shelf location has
    no column, and the demo store has parts.bin_location."""
    for q, o in examples():
        for u in o.get("unresolved", []):
            assert "shelv" not in u["asked"] and "location" not in u["asked"], q


def test_recorded_reason_example_is_retrieval_not_referral():
    (q, o), = [(q, o) for q, o in examples() if "reason was recorded" in q]
    assert o["disposition"] == "retrieve"
    selected = {i["expr"].get("col") for r in o["readings"] for i in r["plan"]["SELECT"]}
    assert "commitments.delay_reason" in selected


def test_schema_shows_closed_value_sets():
    text = p.render_schema(CAT, "PLANNER")
    assert "status TEXT  -- one of: open, closed, cancelled" in text
    assert "region TEXT  -- one of: EMEA, AMER, APAC" in text


def test_render_with_the_demo_prompt():
    text = p.render_intent(CAT, "PLANNER", "Which orders are open?", prompt=PROMPT)
    assert "CREATE TABLE purchase_orders" in text and text.rstrip().endswith("Output:")
