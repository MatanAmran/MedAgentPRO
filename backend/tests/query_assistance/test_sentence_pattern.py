"""
G-SENTPAT (task 2, step 0; chat 21.10, 16:40): the pattern schema 3 puts on a sentence and the shape synthesis.check
accepts are one rule. Step 0 found that the pattern required a row reference ({label.alias#row}) while D5 lets a
sentence hold {label.total} alone, so the schema refused sentences the contract allows (all 12 breaches recorded on
20.10). A kind (L2), both ways, over sentences built from every form of a reference: where check accepts the shape,
the pattern passes, and where check refuses it for its shape, the pattern refuses it, in the full schema and in the
schema as LiteLLM reduces it for the provider. No model.
"""

import itertools
import json

import pytest
from jsonschema import Draft202012Validator

from demo_data import seed
from query_assistance import calls, synthesis
from query_assistance.compiler import Catalog

CAT = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
PLAN = {"SELECT": [{"expr": {"col": "commitments.week"}, "as": "week"},
                   {"expr": {"col": "commitments.quantity"}, "as": "qty"}], "FROM": "commitments"}
MATERIAL = synthesis.material("r", PLAN, [{"week": 3, "qty": 40}, {"week": 5, "qty": 60}], cat=CAT, total=2)

# every form a reference can take: the two the contract allows, and the near misses
FORMS = {
    "{r.week#1}": True, "{r.qty#2}": True, "{r.total}": True,
    "{r.week}": False, "{r.week#}": False, "{r.week#x}": False, "{R.week#1}": False, "{r .week#1}": False,
    "{r.Total}": False, "{r.totals}": False, "r.week#1": False, "r.total": False, "{r}": False, "{}": False,
}
WORDS = ["In week", "the reading shows", "lines."]


def sentences():
    """Every form alone, and every pair of forms, between plain words."""
    for a in FORMS:
        yield f"{WORDS[0]} {a} {WORDS[2]}"
    for a, b in itertools.product(FORMS, repeat=2):
        yield f"{WORDS[0]} {a} {WORDS[1]} {b} {WORDS[2]}"


def check_says(text: str) -> bool | None:
    """What synthesis.check decides on the sentence's shape: True accepted, False refused for holding no
    reference, None refused for another reason (not the shape: this test does not judge those)."""
    try:
        synthesis.check({"readings": [{"label": "r", "sentences": [{"text": text}]}]}, [MATERIAL], "Q?")
        return True
    except synthesis.SynthesisOutputError as e:
        return False if "holds no reference" in str(e.feedback if hasattr(e, "feedback") else e) \
            or "references no field" in str(e) else None


FULL = synthesis.validator().schema
TEXT = {"full": FULL, "reduced": calls.provider_schema(FULL)}


def text_schema(schema: dict) -> dict:
    return schema["properties"]["readings"]["items"]["properties"]["sentences"]["items"]["properties"]["text"]


@pytest.mark.parametrize("which", sorted(TEXT))
def test_the_pattern_and_check_agree_on_every_shape_of_a_sentence(which):
    pattern_only = {"type": "string", "pattern": text_schema(TEXT[which])["pattern"]}
    v = Draft202012Validator(pattern_only)
    judged = 0
    for text in sentences():
        says = check_says(text)
        if says is None:
            continue
        judged += 1
        assert v.is_valid(text) is says, (which, text, says)
    assert judged > len(FORMS)


def test_a_sentence_with_only_the_total_is_valid_in_both_schemas():
    """The case step 0 found: 12 of 12 breaches on 20.10 were this shape."""
    doc = {"readings": [{"label": "r", "sentences": [{"text": "The reading matched {r.total} lines."}]}]}
    assert check_says(doc["readings"][0]["sentences"][0]["text"]) is True
    for schema in TEXT.values():
        assert not [e for e in Draft202012Validator(schema).iter_errors(doc) if e.validator == "pattern"]


def test_the_pattern_reaches_the_provider_as_written():
    """Step 0: LiteLLM 1.105 sends the pattern in output_format as it stands (lengths move to the description)."""
    assert text_schema(TEXT["reduced"])["pattern"] == text_schema(FULL)["pattern"]
    bundle = json.loads((synthesis.HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))
    assert bundle["schemas"]["synthesis"]["schema"] == FULL
