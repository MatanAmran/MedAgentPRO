"""
Tests for prompts.py, gate.py and the prompt files in templates/. Run:  pytest -q

The examples inside each prompt are checked against the same schemas and code the
model's real output goes through, so an example cannot teach something the system rejects.
"""

import json
import re

import pytest
from jsonschema import Draft202012Validator

from query_assistance import compiler as c
from query_assistance import gate
from query_assistance import prompts as p

CAT = c.Catalog.load()
BUNDLE = json.loads((c.HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))


def validator(name):
    return Draft202012Validator(BUNDLE["schemas"][name]["schema"])


def examples(prompt):
    """(input lines, output dict) for each example in a prompt file."""
    body = prompt.body.split("Examples:", 1)[1]
    out = []
    for m in re.finditer(r"((?:.+\n)+?)Output: (\{.*\})\n", body):
        out.append((m.group(1), json.loads(m.group(2))))
    return out


# --------------------------------------------------------------------------
# prompt files
# --------------------------------------------------------------------------

@pytest.mark.parametrize("file", ["rewrite.md", "intent_analysis.md", "gate.md"])
def test_prompt_has_version_and_schema(file):
    pr = p.load(file)
    assert pr.version and pr.output_schema in BUNDLE["schemas"]
    assert pr.identity == f"{pr.name}@{pr.version}"


@pytest.mark.parametrize("file", ["rewrite.md", "intent_analysis.md", "gate.md"])
def test_prompt_text_has_no_semicolons_or_dashes(file):
    body = p.load(file).body
    assert "—" not in body and "–" not in body


def test_intent_examples_validate_and_compile():
    pr = p.load("intent_analysis.md")
    exs = examples(pr)
    assert len(exs) == 3
    v = c.intent_validator()
    for inp, out in exs:
        q = re.search(r"Question: (.*)", inp).group(1)
        assert not list(v.iter_errors(out)), q
        c.compile_intent(out, q, {"role": "APPROVER"}, CAT, v)


def test_rewrite_examples_validate_and_name_no_new_identifier():
    pr = p.load("rewrite.md")
    exs = examples(pr)
    assert len(exs) == 4
    v = validator("question_rewriting")
    for inp, out in exs:
        assert not list(v.iter_errors(out))
        posed = re.search(r"Latest question: (.*)", inp).group(1)
        p.check_rewrite(out, posed)
        ids = set(re.findall(r"\b[A-Z]{1,2}-\d+\b", out["question"]))
        assert ids <= set(re.findall(r"\b[A-Z]{1,2}-\d+\b", inp)), out["question"]


def test_gate_example_validates_and_evaluates():
    pr = p.load("gate.md")
    (inp, out), = examples(pr)
    assert not list(validator("support_scoring").iter_errors(out))
    answer = re.search(r"Answer: (.*)", inp).group(1)
    res = gate.evaluate(out, answer, {"late_suppliers": {"supplier_id", "late_count"}}, True)
    assert res.decision == "keep" and len(res.kept) == 1 and len(res.removed) == 1


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def test_schema_shows_only_tables_the_role_reads_and_no_rule():
    s = p.render_schema(CAT, "PLANNER")
    assert "CREATE TABLE commitments" in s and "forecast" not in s
    assert "entitlement" not in s and "user_id" not in s
    assert "supplier_id TEXT  -- identifier" in s
    assert "-- relation commitment_supplier" in s and "-- relation part_forecast" not in s
    assert "-- metric late_by_promise" in s


def test_role_with_no_access_sees_no_table():
    assert p.render_schema(CAT, "HTC_BUYER") == ""


def test_intent_prompt_carries_both_forms_only_when_rewritten():
    one = p.render_intent(CAT, "PLANNER", "Which suppliers were late?")
    assert "Question as posed" not in one and one.rstrip().endswith("Output:")
    two = p.render_intent(CAT, "PLANNER", "And in October?", "Which suppliers were late in October?")
    assert "Question as posed: And in October?" in two and "Question: Which suppliers were late in October?" in two


def test_gate_prompt_has_records_and_no_question():
    text = p.render_gate("S-031 had 7.", [("late", [{"supplier_id": "S-031", "n": 7}]), ("other", [])])
    assert "[late] row 1: late.supplier_id = S-031, late.n = 7" in text
    assert "[other] no rows" in text and "Question" not in text


def test_unfilled_placeholder_raises():
    with pytest.raises(ValueError):
        p.fill(p.load("gate.md"), answer="x")


# --------------------------------------------------------------------------
# history and its layers (Table 15, "History carried into rewriting")
# --------------------------------------------------------------------------

def ex(i):
    return p.Exchange(f"Question {i} about S-0{i:02d}?", f"Understood {i}", "An answer. " * 30, [f"S-0{i:02d}"])


def test_layer_1_keeps_everything():
    h = p.build_history([ex(1), ex(2)], budget=10_000)
    assert h.layer == 1 and "A1:" in h.text and "Q2 (understood as)" in h.text


def test_layer_2_drops_older_answers_and_keeps_their_questions():
    exs = [ex(i) for i in range(1, 6)]
    h = p.build_history(exs, budget=200)
    assert h.layer == 2
    assert "A1:" not in h.text and "Q1 (posed)" in h.text and "A5:" in h.text


def test_layer_3_hands_oldest_to_a_summary():
    exs = [ex(i) for i in range(1, 30)]
    h = p.build_history(exs, budget=200, summary="earlier questions were about late suppliers")
    assert h.layer == 3 and h.to_summarize and h.to_summarize[0].posed.startswith("Question 1 ")
    assert h.text.startswith("Summary of earlier exchanges, not a source of identifiers:")
    assert "Q1 (posed)" not in h.text and "A29:" in h.text


def test_no_history_for_first_question():
    assert p.build_history([]).text == ""


def test_compiler_conversation_excludes_model_text():
    exs = [p.Exchange("Late in September?", "Which suppliers were late in September?", "S-031 was late.", ["S-031"])]
    conv = p.compiler_conversation(exs, "And S-777?", "Was S-777 late in September?")
    assert "Which suppliers were late in September?" not in conv.earlier
    assert conv.presented == {"S-031"} and conv.rewritten == "Was S-777 late in September?"


def test_identifier_reduced_out_of_the_prompt_still_counts():
    exs = [ex(i) for i in range(1, 30)]
    conv = p.compiler_conversation(exs, "And the first one?", None)
    d = {"disposition": "retrieve", "readings": [{"label": "r", "plan": {
        "SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po"}], "FROM": "commitments",
        "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-001"}]}}}]}
    c.compile_intent(d, conv, {"role": "APPROVER"}, CAT)


# --------------------------------------------------------------------------
# rewrite output check
# --------------------------------------------------------------------------

def test_rewrite_passthrough_must_be_unchanged():
    with pytest.raises(p.RewriteError):
        p.check_rewrite({"question": "Which suppliers were late in October?", "carried_from_history": False},
                        "And in October?")


def test_rewrite_blank_rejected():
    with pytest.raises(p.RewriteError):
        p.check_rewrite({"question": "  ", "carried_from_history": True}, "x")


# --------------------------------------------------------------------------
# gate checks (schema 4 post_model_checks)
# --------------------------------------------------------------------------

ALIASES = {"late": {"supplier_id", "late_count"}}
ANSWER = "S-031 had 7 late commitments. S-044 had 2."


def claims(*cs):
    return {"claims": [{"text": t, "verdict": v, "evidence": e} for t, v, e in cs]}


def test_paraphrase_rejected():
    with pytest.raises(gate.GateOutputError):
        gate.evaluate(claims(("S-031 was late seven times.", "supported", ["late.late_count"])), ANSWER, ALIASES, True)


def test_unknown_reference_rejected():
    with pytest.raises(gate.GateOutputError):
        gate.evaluate(claims(("S-031 had 7 late commitments.", "supported", ["late.nope"])), ANSWER, ALIASES, True)


def test_supported_without_reference_rejected():
    with pytest.raises(gate.GateOutputError):
        gate.evaluate(claims(("S-031 had 7 late commitments.", "supported", [])), ANSWER, ALIASES, True)


def test_uncovered_with_reference_rejected():
    with pytest.raises(gate.GateOutputError):
        gate.evaluate(claims(("S-044 had 2.", "uncovered", ["late.late_count"])), ANSWER, ALIASES, True)


def test_one_contradiction_fells_the_answer():
    r = gate.evaluate(claims(("S-031 had 7 late commitments.", "supported", ["late.late_count"]),
                             ("S-044 had 2.", "contradicted", ["late.late_count"])), ANSWER, ALIASES, True)
    assert r.decision == "answer_falls" and r.kept == []


def test_unsupported_removed_rest_kept():
    r = gate.evaluate(claims(("S-031 had 7 late commitments.", "supported", ["late.late_count"]),
                             ("S-044 had 2.", "uncovered", [])), ANSWER, ALIASES, True)
    assert r.decision == "keep" and r.kept == ["S-031 had 7 late commitments."] and r.removed == ["S-044 had 2."]


def test_nothing_kept_with_rows_is_a_request_for_input():
    r = gate.evaluate(claims(("S-044 had 2.", "uncovered", [])), ANSWER, ALIASES, True)
    assert r.decision == "request_input"
