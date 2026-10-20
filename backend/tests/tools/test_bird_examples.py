"""superhero on the generic prompts (chat 20.10, B1): intent@12 drops what the catalog cannot hold, and the
catalog's own words and examples, none of them from the evaluation set. No model."""

import json
import re
import sqlite3

import pytest

from demo_data import seed
from model_client.client import ScriptedModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import pipeline
from query_assistance import prompts as p
from query_assistance.compiler import compile_intent, intent_validator
from shared.circuit_breaker import CircuitBreaker, MemoryStore
from tools import bird_eval as be

HERE = be.HERE
# the examples of a disposition BIRD has no question for, written by Code (80_GENERALITY)
WRITTEN = {"What is the height of Sauron, and in which year did he first appear?",
           "Which publishers published more than 100 superheroes, and which of their superheroes are bad?",
           "Why are bad superheroes heavier than good ones?", "Change the publisher of Sauron to Marvel Comics.",
           "And for the other one?"}


def _pairs(text: str) -> list[tuple[str, dict]]:
    return [(q, json.loads(o)) for q, o in re.findall(r"^Question: (.*)\nOutput: (.*)$", text, re.M)]


INTENT = _pairs((HERE / "examples" / "intent_analysis.md").read_text(encoding="utf-8"))


def test_superhero_holds_none_of_the_conditions_and_the_demo_all():
    assert p.catalog_holds(HERE) == set()
    assert p.catalog_holds(seed.HERE) == {"dates", "users", "searchable", "reasons"}


def test_intent_12_on_superhero_drops_the_date_the_user_and_the_name_rules():
    body = p.for_catalog("intent_analysis.md", HERE).body
    assert "{{reference_date}}" not in body and "\n12. " not in body and "A period of time" not in body
    assert "\n16. " not in body and "\n17. " not in body and "MATCHES" not in body
    assert "\n11. " in body and "\n13. " in body   # the numbers keep their gap (recorded in 80_GENERALITY)
    assert "[[" not in body


@pytest.mark.parametrize("file", ["rewrite.md", "synthesis.md", "synthesis_redraft.md", "gate.md"])
def test_every_generic_prompt_is_filled_for_superhero(file):
    assert "[[" not in p.for_catalog(file, HERE).body


def test_a_section_with_a_condition_no_catalog_states_is_refused():
    template = p.Prompt("x", 1, "x", "a[[if colours]] b[[end colours]]")
    with pytest.raises(ValueError, match="no condition colours"):
        p.specialize(template, {}, "")


@pytest.mark.parametrize("question,output", INTENT, ids=[q[:40] for q, _ in INTENT])
def test_every_intent_example_compiles_on_superhero(question, output):
    compile_intent(output, question, be.USER, be.catalog(), intent_validator())


def test_every_example_comes_from_bird_outside_the_set_or_is_listed_as_written():
    examples = {q["question"]: q for q in be.load_examples()}
    set_data = be.load()
    in_set = {q["question"] for q in set_data["questions"] + set_data["gaps"]}
    set_ids = {q["id"] for q in set_data["questions"] + set_data["gaps"]}
    assert not set(examples) & in_set and not {q["id"] for q in examples.values()} & set_ids
    for question, _ in INTENT:
        assert question in examples or question in WRITTEN, question
    assert not WRITTEN & in_set


def test_no_example_plan_equals_a_plan_of_the_set():
    set_plans = [json.dumps(q["plan"], sort_keys=True) for q in be.load()["questions"]]
    shown = [json.dumps(o, sort_keys=True) for _, o in INTENT] + \
            [json.dumps(q["plan"], sort_keys=True) for q in be.load_examples()]
    assert not set(shown) & set(set_plans)
    readings = {json.dumps(r["plan"], sort_keys=True) for q in be.load()["questions"] for r in q["plan"]["readings"]}
    assert not {json.dumps(r["plan"], sort_keys=True) for _, o in INTENT for r in o.get("readings", [])} & readings


def test_the_pipeline_runs_on_superhero_without_data_as_of(tmp_path):
    """No {{reference_date}} in the prompt, so the catalog need not hold a date (chat 20.10, B1(a), test 2)."""
    cat = be.catalog()
    assert cat.data_as_of is None
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE superhero (id INTEGER, superhero_name TEXT, full_name TEXT, gender_id INTEGER, "
                       "eye_colour_id INTEGER, hair_colour_id INTEGER, skin_colour_id INTEGER, race_id INTEGER, "
                       "publisher_id INTEGER, alignment_id INTEGER, height_cm INTEGER, weight_kg INTEGER);"
                       "INSERT INTO superhero (id, superhero_name, height_cm) VALUES (1, 'Sauron', 279);")
    conn.commit()
    conn.close()
    plan = {"disposition": "retrieve", "readings": [{"label": "h", "plan": {
        "SELECT": [{"expr": {"col": "superhero.superhero_name"}, "as": "superhero_name"}],
        "FROM": "superhero", "WHERE": {"EQ": [{"col": "superhero.superhero_name"}, {"value": "Sauron"}]}}}]}
    sent = []

    def model(prompt, cfg):
        sent.append(cfg.prompt_identity)
        name = cfg.prompt_identity.split("@")[0]
        if name == "intent_analysis":
            return plan
        if name == "synthesis":
            return {"readings": [{"label": "h", "sentences": [{"text": "{h.superhero_name#1} is listed."}]}]}
        return {"claims": [{"sentence": 1, "text": "Sauron is listed.", "verdict": "supported",
                            "evidence": ["h.superhero_name"]}]}

    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pipeline.Deps(ScriptedModelGateway(model), "scripted", cat, pipeline.connect_readonly(db), audit,
                         p.for_catalog("intent_analysis.md", HERE), prompt_set=p.PromptSet.generic(HERE),
                         coverage=False)
    out = pipeline.run("What is the name of Sauron?", [], be.USER, deps)
    assert out.response["outcome"] == "answered", out.response
    assert sent[0] == "intent_analysis@15"


def test_superhero_is_taught_no_value_as_a_reason():
    """Chat 20.10, 14:00: alignment is a trait, not a reason. superhero marks no column records_reason, so the
    sentences on a recorded reason are dropped; the first sentence of each, refer a cause, never give one, stays."""
    intent = p.for_catalog("intent_analysis.md", HERE).body
    synthesis = p.for_catalog("synthesis.md", HERE).body
    redraft = p.for_catalog("synthesis_redraft.md", HERE).body
    assert "Retrieving a reason" not in intent and "attributing a cause" in intent
    assert "records a reason" not in synthesis and "Reason recorded" not in synthesis
    assert "do not give a cause" in synthesis
    assert "A recorded reason" not in redraft and "do not give a cause" in redraft
    # which column a reason may name is checked against the marks, not by a word (R-2, test_prompt_text.py)
    texts = json.loads((HERE / "prompt_text.json").read_text(encoding="utf-8"))["texts"]
    assert "recorded_reason_example" not in texts and "reason_reference" not in texts


def test_the_demo_marks_its_one_reason_column():
    from query_assistance.compiler import Catalog
    cat = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
    marked = [f"{t}.{c}" for t, e in cat.entities.items() for c, s in e["columns"].items() if s.get("records_reason")]
    assert marked == ["commitments.delay_reason"]
