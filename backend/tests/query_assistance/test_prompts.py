"""
Tests for prompts.py, gate.py and the prompt files in templates/. Run:  pytest -q

The examples inside each prompt are checked against the same schemas and code the
model's real output goes through, so an example cannot teach something the system rejects.
"""

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from demo_data import seed
from query_assistance import compiler as c
from query_assistance import gate
from query_assistance import prompts as p

FIXTURES = Path(__file__).parents[1] / "fixtures"
CAT = c.Catalog.load(FIXTURES / "placeholder_schema.json", FIXTURES / "placeholder_relations.json")
DEMO = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
INTENT = p.load(seed.HERE / "intent_analysis.md")
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

@pytest.mark.parametrize("file", ["rewrite.md", seed.HERE / "intent_analysis.md", "gate.md"])
def test_prompt_has_version_and_schema(file):
    pr = p.load(file)
    assert pr.version and pr.output_schema in BUNDLE["schemas"]
    assert pr.identity == f"{pr.name}@{pr.version}"


@pytest.mark.parametrize("file", ["rewrite.md", seed.HERE / "intent_analysis.md", "gate.md"])
def test_prompt_text_has_no_semicolons_or_dashes(file):
    body = p.load(file).body
    assert "—" not in body and "–" not in body


def test_rewrite_examples_validate_and_name_no_new_identifier():
    pr = p.load("rewrite.md")
    exs = examples(pr)
    assert len(exs) == 6   # v3 (D1): a result fully shown is named, a cut one is stated by its condition
    v = validator("question_rewriting")
    for inp, out in exs:
        assert not list(v.iter_errors(out))
        posed = re.search(r"Latest question: (.*)", inp).group(1)
        p.check_rewrite(out, posed)
        ids = set(re.findall(r"\b[A-Z]{1,2}-\d+\b", out["question"]))
        assert ids <= set(re.findall(r"\b[A-Z]{1,2}-\d+\b", inp)), out["question"]


def test_gate_examples_validate_and_evaluate():
    """v4 (task 10a): one claim per sentence, and a place named between tied rows is contradicted.
    v5 (chat, 18.10, 09:40): a sentence that describes its records unlike their examined line is contradicted.
    v6 (fix round, D4 and D5): rows described by anything but their condition are uncovered, and a stated
    count is supported by label.total."""
    pr = p.load("gate.md")
    (inp, out), (tie_in, tie_out), (list_in, list_out) = examples(pr)
    assert pr.version == "6"
    for o in (out, tie_out):
        assert not list(validator("support_scoring").iter_errors(o))
    sentences = [(m.group(1), m.group(2)) for m in re.finditer(r"^\d+\. \[(\w+)\] (.*)$", inp, re.M)]
    assert gate.render_answer(sentences) in inp
    res = gate.evaluate(out, sentences, {"late_suppliers": {"supplier_id", "late_count"}})
    assert res.decision == "keep" and len(res.kept) == 1 and len(res.removed) == 1
    assert res.supported == [True, False]
    tied = [(m.group(1), m.group(2)) for m in re.finditer(r"^\d+\. \[(\w+)\] (.*)$", tie_in, re.M)]
    assert "(same late_by_delivery.late_lines as row 1)" in tie_in
    res = gate.evaluate(tie_out, tied, {"late_by_delivery": {"supplier_id", "late_lines"}})
    assert res.supported == [False, True] and [c["verdict"] for c in tie_out["claims"]] == ["contradicted", "supported"]
    listed = [(m.group(1), m.group(2)) for m in re.finditer(r"^\d+\. \[(\w+)\] (.*)$", list_in, re.M)]
    assert "[late_lines] late_lines.total = 64 rows matched, 30 shown" in list_in
    res = gate.evaluate(list_out, listed, {"late_lines": {"commitment_id", "delivered_at", "total"}})
    assert res.supported == [True, False] and "late_lines.total" in list_out["claims"][0]["evidence"]
    assert all(len([c for c in o["claims"] if c["sentence"] == n]) == 1      # one claim per sentence
               for o in (out, tie_out) for n in {c["sentence"] for c in o["claims"]})

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
    one = p.render_intent(DEMO, "PLANNER", "Which suppliers were late?", prompt=INTENT)
    assert "Question as posed" not in one and one.rstrip().endswith("Output:")
    two = p.render_intent(DEMO, "PLANNER", "And in October?", "Which suppliers were late in October?", INTENT)
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
SENTENCES = [("late", "S-031 had 7 late commitments."), ("late", "S-044 had 2.")]


def claims(*cs):
    return {"claims": [{"sentence": n, "text": t, "verdict": v, "evidence": e} for n, t, v, e in cs]}


S1 = (1, "S-031 had 7 late commitments.", "supported", ["late.late_count"])


def test_paraphrase_rejected():
    with pytest.raises(gate.GateOutputError, match="verbatim"):
        gate.evaluate(claims((1, "S-031 was late seven times.", "supported", ["late.late_count"]),
                             (2, "S-044 had 2.", "uncovered", [])), SENTENCES, ALIASES)


def test_claim_in_another_sentence_rejected():
    with pytest.raises(gate.GateOutputError, match="verbatim in sentence 2"):
        gate.evaluate(claims(S1, (2, "S-031 had 7 late commitments.", "supported", ["late.late_count"])),
                      SENTENCES, ALIASES)


def test_sentence_number_out_of_range_rejected():
    with pytest.raises(gate.GateOutputError, match="sentence 3"):
        gate.evaluate(claims(S1, (2, "S-044 had 2.", "uncovered", []), (3, "x", "uncovered", [])),
                      SENTENCES, ALIASES)


def test_skipped_sentence_rejected():
    """Decision 12 (12.10): nothing unexamined reaches presentation."""
    with pytest.raises(gate.GateOutputError, match=r"\[2\] not examined"):
        gate.evaluate(claims(S1), SENTENCES, ALIASES)


def test_unknown_reference_rejected():
    with pytest.raises(gate.GateOutputError):
        gate.evaluate(claims((1, "S-031 had 7 late commitments.", "supported", ["late.nope"]),
                             (2, "S-044 had 2.", "uncovered", [])), SENTENCES, ALIASES)


def test_supported_without_reference_rejected():
    with pytest.raises(gate.GateOutputError):
        gate.evaluate(claims((1, "S-031 had 7 late commitments.", "supported", []),
                             (2, "S-044 had 2.", "uncovered", [])), SENTENCES, ALIASES)


def test_uncovered_with_reference_rejected():
    with pytest.raises(gate.GateOutputError):
        gate.evaluate(claims(S1, (2, "S-044 had 2.", "uncovered", ["late.late_count"])), SENTENCES, ALIASES)


def test_contradiction_is_removed_like_an_unsupported_assertion():
    """Decision 3 (12.10): contradicted no longer brings the whole answer down."""
    r = gate.evaluate(claims(S1, (2, "S-044 had 2.", "contradicted", ["late.late_count"])), SENTENCES, ALIASES)
    assert r.decision == "keep" and r.kept == ["S-031 had 7 late commitments."] and r.removed == ["S-044 had 2."]
    assert r.supported == [True, False]


def test_unsupported_removed_rest_kept():
    r = gate.evaluate(claims(S1, (2, "S-044 had 2.", "uncovered", [])), SENTENCES, ALIASES)
    assert r.decision == "keep" and r.kept == ["S-031 had 7 late commitments."] and r.removed == ["S-044 had 2."]
    assert r.kept_by_sentence == {1: ["S-031 had 7 late commitments."]}


def test_sentence_with_one_unsupported_claim_is_not_supported():
    sentences = [("late", "S-031 had 7 late commitments, more than anyone.")]
    r = gate.evaluate(claims((1, "S-031 had 7 late commitments", "supported", ["late.late_count"]),
                             (1, "more than anyone", "uncovered", [])), sentences, ALIASES)
    assert r.decision == "keep" and r.supported == [False]


def test_nothing_kept_is_a_request_for_input():
    r = gate.evaluate(claims((1, "S-031 had 7 late commitments.", "uncovered", []),
                             (2, "S-044 had 2.", "uncovered", [])), SENTENCES, ALIASES)
    assert r.decision == "request_input" and r.supported == [False, False]

@pytest.mark.parametrize("text, says", [
    ("Just a body, no header.\n", "no header"),
    ("---\nprompt: x\nversion 1\noutput_schema: y\n---\nbody\n", "is not 'key: value'"),
    ("---\nprompt: x\nversion: 1\n---\nbody\n", "header lacks output_schema"),
])
def test_a_malformed_prompt_file_names_the_file_and_what_is_wrong(tmp_path, text, says):
    f = tmp_path / "broken.md"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=re.escape(says)) as e:
        p.load(f)
    assert "broken.md" in str(e.value)


# --------------------------------------------------------------------------
# D1 and D2 (fix round, chat 18.10): what the history says of each reading, and the rewrite guard
# --------------------------------------------------------------------------

def _late(shown: int, total: int, ids: list[str]) -> p.Exchange:
    return p.Exchange("Which suppliers were late in weeks 1 to 13?", None, "S-038 had 10.", ids,
                      readings=[p.ReadingNote("late_by_delivery", "purchase order lines where week between 1 and 13",
                                              shown, total)],
                      cut=ids if shown < total else [])


def test_history_carries_each_readings_condition_and_how_much_was_shown():
    h = p.build_history([_late(30, 39, ["S-038"]), _late(3, 3, ["S-001"])])
    assert "Q1 reading [late_by_delivery] examined: purchase order lines where week between 1 and 13 · 30 of 39 shown" \
        in h.text
    assert "Q2 reading [late_by_delivery] examined: purchase order lines where week between 1 and 13 · all 3 shown" \
        in h.text


def test_layer_two_drops_old_answers_one_at_a_time_and_keeps_the_conditions():
    big = [p.Exchange(f"Question {i}?", None, "x" * 900, [f"S-{i:03d}"],
                      readings=[p.ReadingNote("r", f"condition {i}", 1, 1)]) for i in range(1, 6)]
    h = p.build_history(big, budget=1000)
    assert h.layer == 2
    assert "A1:" not in h.text and "A5:" in h.text           # the oldest went first, the last stays whole
    assert "A4:" in h.text or "A3:" in h.text                 # not every older answer at once
    assert all(f"condition {i}" in h.text for i in range(1, 6))


@pytest.mark.parametrize("question, history, rejected", [
    ("What are the open values of S-001, S-008 and S-019?", [_late(3, 3, ["S-001", "S-008", "S-019"])], False),
    ("What are the open values of S-038?", [_late(30, 39, ["S-038", "S-005"])], False),      # one, as by position
    ("What are the open values of S-038 and S-005?", [_late(30, 39, ["S-038", "S-005"])], True),   # a list, cut
    ("What are the open values of the suppliers late in weeks 1 to 13?",
     [_late(30, 39, ["S-038", "S-005"])], False),                                               # the condition
    ("What are the open values of " + ", ".join(f"S-{i:03d}" for i in range(1, 22)) + "?",
     [_late(21, 21, [f"S-{i:03d}" for i in range(1, 22)])], True),                             # 21 added
])
def test_rewrite_guard_names_ids_only_from_a_whole_result_of_at_most_twenty(question, history, rejected):
    """D1 and D1-POS (chat 19.10): one identifier from a cut reading is a row the person pointed at; two or
    more are a list that is not the whole result."""
    out = {"question": question, "carried_from_history": True}
    if rejected:
        with pytest.raises(p.RewriteError) as e:
            p.check_rewrite(out, "What are their open values?", history)
        assert "S-0" not in str(e.value)   # the feedback gives counts, never a value (NFR 21, R4)
    else:
        assert p.check_rewrite(out, "What are their open values?", history) == question


def test_rewrite_guard_lets_the_person_name_what_they_typed():
    """Identifiers the latest question holds are the person's, cut reading or not."""
    out = {"question": "What are the open values of S-038?", "carried_from_history": True}
    assert p.check_rewrite(out, "And S-038's open values?", [_late(30, 39, ["S-038"])])
