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
    assert PROMPT.output_schema == "intent_analysis" and PROMPT.identity == "intent_analysis_demo@7"
    assert "—" not in PROMPT.body and "–" not in PROMPT.body


def test_examples_cover_each_kind():
    kinds = {("refer" if o["disposition"] == "refer_to_investigation" else
              "action" if o["disposition"] == "out_of_scope_action" else
              "clarify" if o["disposition"] == "needs_clarification" else
              "ambiguous" if o.get("ambiguous") else
              "unresolved" if o.get("unresolved") else
              "join" if any("JOIN" in r["plan"] for r in o["readings"]) else "plain")
             for _, o in examples()}
    assert {"refer", "action", "clarify", "ambiguous", "unresolved", "join", "plain"} <= kinds


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


# --------------------------------------------------------------------------
# version 5 (task 10a, chat 01:15): dates, LIMIT, the reference date, the gap rule
# --------------------------------------------------------------------------

def test_the_reference_date_is_the_last_day_the_data_records(db):
    """Derived from the data, never the clock: schema.json's data_as_of, which seed.py builds up to, is
    the latest order, delivery and receipt in the store. Due and promised dates run past it."""
    assert CAT.data_as_of == seed.DATA_CUTOFF.isoformat()
    latest = max(db.execute(f"SELECT MAX({col}) FROM {table}").fetchone()[0] for table, col in
                 (("purchase_orders", "order_date"), ("commitments", "delivered_at"), ("receipts", "received_on")))
    assert latest == CAT.data_as_of
    assert db.execute("SELECT MAX(promised_date) FROM commitments").fetchone()[0] > CAT.data_as_of


def test_the_prompt_carries_the_reference_date_and_the_row_limit():
    text = p.render_intent(CAT, "PLANNER", "Which lines were late in September?", prompt=PROMPT)
    assert f"on or before {CAT.data_as_of}." in text and f"then at most {c.ROW_LIMIT}." in text
    assert "{{" not in text


def test_a_prompt_that_needs_a_reference_date_refuses_a_catalog_without_one():
    bare = c.Catalog({"entities": CAT.entities, "metrics": CAT.metrics}, {"relations": CAT.relations})
    with pytest.raises(ValueError, match="data_as_of"):
        p.render_intent(bare, "PLANNER", "Which lines were late in September?", prompt=PROMPT)


def test_a_month_example_filters_the_date_column_with_full_dates_and_writes_no_limit():
    (q, o), = [(q, o) for q, o in examples() if "in March" in q]
    plan = o["readings"][0]["plan"]
    between = [x["BETWEEN"] for x in plan["WHERE"]["AND"] if "BETWEEN" in x]
    assert between and between[0]["col"] == "commitments.delivered_at"
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", between[0][k]["value"]) for k in ("from", "to"))
    # the month resolves against the reference date, as rule 12 says
    assert between[0]["from"]["value"][:4] == CAT.data_as_of[:4] and "03" < CAT.data_as_of[5:7]
    assert all("LIMIT" not in r["plan"] for _, o in examples() for r in o.get("readings", []))
    assert all(i.get("dir") in ("ASC", "DESC") for _, o in examples() for r in o.get("readings", [])
               for i in r["plan"].get("ORDER_BY", []))


# --------------------------------------------------------------------------
# version 6 (task 10a, chat 04:20): "which X" is one row per X
# --------------------------------------------------------------------------

def test_the_which_rule_is_in_the_prompt():
    rule = "When the question asks which <entities>, return one row per entity: select and group by its identifier."
    assert rule in PROMPT.body


def _identifier_aliases(plan) -> set[str]:
    return {i["as"] for i in plan["SELECT"] if "col" in i["expr"] and (c.Catalog.column(CAT, i["expr"]["col"]) or {})
            .get("identifier")}


@pytest.mark.parametrize("question, output", [(q, o) for q, o in examples() if q.startswith("Which ")])
def test_every_which_example_returns_one_row_per_entity(question, output):
    """Grouped by an identifier it selects, or read from a table whose own identifier it selects."""
    for r in output["readings"]:
        plan = r["plan"]
        ids = _identifier_aliases(plan)
        own = {f'{plan["FROM"]}.{col}' for col, spec in CAT.entities[plan["FROM"]]["columns"].items()
               if spec.get("identifier") and not spec.get("references")}
        selected = {i["expr"].get("col") for i in plan["SELECT"]}
        assert ids & set(plan.get("GROUP_BY", [])) or own & selected, (question, r["label"])
