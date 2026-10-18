"""
Synthesis (schema 5) with symbolic references: the prompt files, the structural checks,
filling, the NFR 21 history form, and the calls with their retry and redraft.
All model calls go through ScriptedModelGateway.
"""

import json
import re

import pytest
from jsonschema import Draft202012Validator

from demo_data import seed
from model_client.client import ScriptedModelGateway
from query_assistance import compiler as c
from query_assistance import prompts as p
from query_assistance import synthesis as s
from query_assistance.calls import StageFailed
from query_assistance.compiler import HERE

BUNDLE = json.loads((HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))
DEMO = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
Q = "Which suppliers delivered late in September?"

LATE_PLAN = {"SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
                        {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_lines"}],
             "FROM": "commitments", "ORDER_BY": [{"by": "late_lines", "dir": "DESC"}]}
LATE = s.material("late", LATE_PLAN, [{"supplier_id": "S-038", "late_lines": 6},
                                      {"supplier_id": "S-014", "late_lines": 5}], cat=DEMO)
NOTE_PLAN = {"SELECT": [{"expr": {"col": "purchase_orders.po_id"}, "as": "po_id"},
                        {"expr": {"col": "purchase_orders.status"}, "as": "status"},
                        {"expr": {"col": "purchase_orders.notes"}, "as": "notes"}],
             "FROM": "purchase_orders"}
NOTE = s.material("po", NOTE_PLAN, [{"po_id": "PO-87006", "status": "open",
                                     "notes": "Ask about S-999 and PO-12345 next"},
                                    {"po_id": "PO-87009", "status": "closed", "notes": None}], cat=DEMO)
EMPTY = s.Material("other", [])


def out(*readings):
    return {"readings": [{"label": lab, "sentences": [{"text": t} for t in ts]} for lab, ts in readings]}


def ok(*readings, materials=(LATE,), question=Q):
    return s.check(out(*readings), list(materials), question)


def bad(*readings, materials=(LATE,), question=Q, match=None):
    with pytest.raises(s.SynthesisOutputError, match=match):
        s.check(out(*readings), list(materials), question)


def draft_of(*readings, materials=(LATE,), question=Q):
    return s.Draft(ok(*readings, materials=materials, question=question), {m.label: m for m in materials}, [])


# --------------------------------------------------------------------------
# schema and prompt files
# --------------------------------------------------------------------------

def test_schema_is_valid_and_marked_demo():
    entry = BUNDLE["schemas"]["synthesis"]
    Draft202012Validator.check_schema(entry["schema"])
    assert entry["status"] == "demo"


def test_gate_can_examine_every_sentence_an_answer_may_hold():
    """Table 15: assertions examined by the gate bound the length of an answer."""
    syn = BUNDLE["schemas"]["synthesis"]["schema"]["properties"]["readings"]
    gate_max = BUNDLE["schemas"]["support_scoring"]["schema"]["properties"]["claims"]["maxItems"]
    assert syn["maxItems"] * syn["items"]["properties"]["sentences"]["maxItems"] <= gate_max


def test_sentence_without_reference_fails_the_schema():
    v = s.validator()
    assert list(v.iter_errors(out(("late", ["S-038 was late."]))))


@pytest.mark.parametrize("file", ["synthesis.md", "synthesis_redraft.md"])
def test_prompt_has_version_schema_and_no_dashes(file):
    pr = p.load(file)
    assert pr.output_schema == "synthesis" and pr.version == {"synthesis.md": "9", "synthesis_redraft.md": "6"}[file]
    assert "—" not in pr.body and "–" not in pr.body


def _examples(pr):
    body = pr.body.split("Examples:", 1)[1]
    return [(m.group(1), json.loads(m.group(2)))
            for m in re.finditer(r"((?:.+\n)+?)Output: (\{.*\})\n", body)]


def _materials(block):
    mats = {}
    for line in block.splitlines():
        m = re.match(r"\[(\w+)\] row (\d+): (.*)", line)
        if not m:
            continue
        row = {}
        fields = re.sub(r" \(same [\w.]+ as row \d+\)$", "", m.group(3))   # the tie marker (task 10a)
        for pair in fields.split(", "):
            k, _, v = pair.partition(" = ")
            row[k.split(".", 1)[1]] = None if v == "None" else int(v) if v.isdigit() else v
        mats.setdefault(m.group(1), []).append(row)
    # an alias is taken as an identifier where a demo table holds an identifier column of that name
    return [s.Material(lab, rows, id_aliases={k for k in rows[0] if any(
        (DEMO.column(f"{t}.{k}") or {}).get("identifier") for t in DEMO.entities)})
        for lab, rows in mats.items()]


@pytest.mark.parametrize("file", ["synthesis.md", "synthesis_redraft.md"])
def test_prompt_examples_pass_the_same_checks_as_real_output(file):
    v = s.validator()
    exs = _examples(p.load(file))
    assert exs
    for inp, o in exs:
        assert not list(v.iter_errors(o))
        s.check(o, _materials(inp), re.search(r"Question: (.*)", inp).group(1))


def test_render_fills_question_and_records_only_for_readings_with_rows():
    text = s.render(Q, [LATE, EMPTY])
    assert f"Question: {Q}" in text and "[late] row 1: late.supplier_id = S-038" in text
    assert "[other]" not in text and text.rstrip().endswith("Output:")


def test_first_draft_has_no_rejected_block():
    text = s.render(Q, [LATE])
    assert "Rejected" not in text and "{{" not in text and text.rstrip().endswith("Output:")


def test_fresh_draft_is_told_what_was_rejected():
    """Decision 4 (12.10), a proposal: otherwise temperature 0 returns the same answer."""
    text = s.render(Q, [LATE], rejected=["S-038 was the worst supplier."])
    assert "Do not state them again" in text and "- S-038 was the worst supplier.\nOutput:" in text


def test_render_redraft_lists_kept_assertions():
    assert "Kept:\n[late] S-038 had 6." in s.render_redraft(Q, [LATE], {"late": ["S-038 had 6."]})


# --------------------------------------------------------------------------
# filling: every value presented is the value in the record (NFR 6)
# --------------------------------------------------------------------------

def test_references_are_filled_from_the_row_they_name():
    d = draft_of(("late", ["{late.supplier_id#1} had the most late lines, {late.late_lines#1}.",
                           "{late.supplier_id#2} followed with {late.late_lines#2}."]))
    assert d.answer("late") == "S-038 had the most late lines, 6. S-014 followed with 5."
    assert d.sentences["late"][0].evidence == ["late.supplier_id", "late.late_lines"]


@pytest.mark.parametrize("v, shown", [(12.0, "12"), (1234.567, "1234.57"), (0.5, "0.5"), (7, "7"), ("x", "x")])
def test_value_format(v, shown):
    assert s.format_value(v) == shown


# --------------------------------------------------------------------------
# structural checks
# --------------------------------------------------------------------------

def test_sentence_without_a_reference_rejected():
    bad(("late", ["Several suppliers were late."]), match="references no field")


def test_reference_to_unknown_alias_rejected():
    bad(("late", ["{late.nope#1} was late."]), match="no field nope")


def test_reference_to_a_row_that_does_not_exist_rejected():
    bad(("late", ["{late.supplier_id#3} was late."]), match="has 2 row")


def test_reference_to_another_reading_rejected():
    other = s.Material("other", [{"supplier_id": "S-001"}])
    bad(("late", ["{other.supplier_id#1} was late."]), materials=(LATE, other))


def test_reference_to_an_empty_field_rejected():
    bad(("po", ["Note on {po.po_id#2}: {po.notes#2}."]), materials=(NOTE,), match="empty field")


def test_malformed_reference_rejected():
    bad(("late", ["{late.supplier_id#1} had {late.late_lines}."]), match="malformed")


def test_reading_without_rows_cannot_be_answered_by_the_model():
    bad(("other", ["{other.x#1}"]), materials=(LATE, EMPTY))


def test_reading_with_rows_and_no_sentence_rejected():
    other = s.Material("other", [{"supplier_id": "S-001"}])
    bad(("late", ["{late.supplier_id#1} was late."]), materials=(LATE, other), match="no sentence")


def test_duplicate_reading_rejected():
    bad(("late", ["{late.supplier_id#1}."]), ("late", ["{late.supplier_id#2}."]))


@pytest.mark.parametrize("text", [
    "2 suppliers were late, led by {late.supplier_id#1}.",              # a count
    "Together {late.supplier_id#1} and {late.supplier_id#2} had 11.",   # a sum
    "{late.supplier_id#1} had 6 late lines.",                           # a value typed, not referenced
    "S-099 had {late.late_lines#1}.",                                   # an identifier typed
])
def test_digit_outside_a_reference_rejected(text):
    bad(("late", [text]), match="outside a reference")


def test_identifier_typed_from_the_rows_is_rejected_by_f11():
    bad(("late", ["S-038 had {late.late_lines#1}."]), match="F11")


def test_token_from_the_question_allowed():
    ok(("late", ["In weeks 1 to 13, {late.supplier_id#1} had {late.late_lines#1}."]),
       question="Who was late in weeks 1 to 13?")
    status = s.material("po", {"SELECT": [{"expr": {"col": "purchase_orders.status"}, "as": "status"}],
                               "FROM": "purchase_orders",
                               "WHERE": {"EQ": [{"col": "purchase_orders.po_id"}, {"value": "PO-87006"}]}},
                        [{"status": "open"}], cat=DEMO)
    ok(("po", ["PO-87006 is {po.status#1}."]), materials=(status,), question="What is the status of PO-87006?")


def test_identifier_in_the_rows_must_be_a_reference_even_if_the_question_names_it():
    """F11: typed, S-014 would sit beside S-038's value and look like S-014's own."""
    bad(("late", ["S-014 had {late.late_lines#1}."]), question="Was S-014 late?", match="F11")
    bad(("po", ["PO-87006 is {po.status#1}."]), materials=(NOTE,), question="What is the status of PO-87006?",
        match="F11")
    ok(("late", ["{late.supplier_id#2} had {late.late_lines#2}."]), question="Was S-014 late?")


def test_identifier_in_another_readings_rows_must_be_a_reference_too():
    other = s.material("other", LATE_PLAN, [{"supplier_id": "S-021", "late_lines": 2}], cat=DEMO)
    bad(("late", ["{late.supplier_id#1} and S-021 had {late.late_lines#1}."]),
        ("other", ["{other.supplier_id#1} had {other.late_lines#1}."]),
        materials=(LATE, other), question="Were S-038 and S-021 late?", match="F11")


def test_identifier_in_a_free_text_field_is_not_an_identifier_column():
    """Only identifier columns count. A note that mentions PO-12345 does not make it a row identifier."""
    assert NOTE.id_aliases == {"po_id"} and "po-12345" not in NOTE.identifier_tokens()


def test_words_around_references_are_not_judged_in_code():
    """Naming row 2 as first is a false statement about the ordering. Code does not read
    meaning, so it passes here and is left to the gate (NFR 19)."""
    ok(("late", ["{late.supplier_id#2} had the most late lines, {late.late_lines#2}."]))


# --------------------------------------------------------------------------
# NFR 21: record text does not reach the history or the identifier check
# --------------------------------------------------------------------------

def test_free_text_columns_are_found_from_the_catalog():
    assert s.free_text_aliases(NOTE, DEMO) == {"notes"}       # status declares values, po_id is an identifier


def test_history_form_keeps_free_text_as_a_reference():
    d = draft_of(("po", ["{po.po_id#1} is {po.status#1}. Note: {po.notes#1}."]), materials=(NOTE,))
    assert "S-999" in d.answer("po")                          # the person sees the note
    hist = d.answer("po", DEMO, for_history=True)
    assert hist == "PO-87006 is open. Note: [po.notes]." and "S-999" not in hist


def test_presented_identifiers_come_from_identifier_columns_only():
    d = draft_of(("po", ["{po.po_id#1} is {po.status#1}. Note: {po.notes#1}."]), materials=(NOTE,))
    assert d.presented_identifiers(DEMO) == ["PO-87006"]


def test_identifier_in_a_note_cannot_be_retrieved_in_a_follow_up():
    """End to end through the compiler: the note names S-999, the follow-up asks about it
    without the person ever having typed it. NFR 27 rejects it because the history form
    does not carry the note."""
    d = draft_of(("po", ["{po.po_id#1} is {po.status#1}. Note: {po.notes#1}."]), materials=(NOTE,))
    ex = p.Exchange("What is the status of PO-87006?", None, d.answer("po", DEMO, for_history=True),
                    d.presented_identifiers(DEMO))
    conv = p.compiler_conversation([ex], "And the supplier mentioned there?", "What did S-999 deliver?")
    plan = {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
            "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-999"}]}}
    with pytest.raises(c.CompileError) as e:
        c.compile_intent({"disposition": "retrieve", "readings": [{"label": "r", "plan": plan}]},
                         conv, {"role": "APPROVER"}, DEMO)
    assert e.value.code == "identifier_not_in_conversation"


def test_the_gap_exists_when_the_answer_as_presented_enters_the_history():
    """The same follow-up passes NFR 27 if the history holds the note text, which is the
    path NFR 21 closes. Kept to show the test above guards a real path."""
    d = draft_of(("po", ["{po.po_id#1} is {po.status#1}. Note: {po.notes#1}."]), materials=(NOTE,))
    ex = p.Exchange("What is the status of PO-87006?", None, d.answer("po"), ["PO-87006"])
    conv = p.compiler_conversation([ex], "And the supplier mentioned there?", "What did S-999 deliver?")
    plan = {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
            "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-999"}]}}
    c.compile_intent({"disposition": "retrieve", "readings": [{"label": "r", "plan": plan}]},
                     conv, {"role": "APPROVER"}, DEMO)


def test_without_a_catalog_every_text_column_is_masked():
    d = draft_of(("po", ["{po.po_id#1}: {po.notes#1}."]), materials=(NOTE,))
    assert d.answer("po", None, for_history=True) == "[po.po_id]: [po.notes]."


# --------------------------------------------------------------------------
# the calls: retry within MODEL_CALL_ATTEMPTS, and the single redraft (NFR 19)
# --------------------------------------------------------------------------

GOOD = out(("late", ["{late.supplier_id#1} had the most late lines, {late.late_lines#1}."]))
COUNTED = out(("late", ["2 suppliers were late, led by {late.supplier_id#1}."]))


def test_draft_fills_and_records_the_call():
    gw = ScriptedModelGateway([GOOD])
    d = s.draft(gw, Q, [LATE], model="demo")
    assert d.answer("late") == "S-038 had the most late lines, 6."
    assert len(gw.calls) == 1 and d.responses[0].prompt_identity == "synthesis@9"


def test_rejected_draft_is_retried_once_then_accepted():
    gw = ScriptedModelGateway([COUNTED, GOOD])
    assert len(s.draft(gw, Q, [LATE], model="demo").responses) == 2


def test_unparsable_output_is_a_format_failure_and_not_retried():
    """R4: a further attempt with the same prompt is a paid bet. Only a content failure gets one."""
    gw = ScriptedModelGateway(["not json", GOOD])
    with pytest.raises(StageFailed) as e:
        s.draft(gw, Q, [LATE], model="demo")
    assert e.value.kind == "format" and len(gw.calls) == 1


def test_draft_fails_after_the_attempts():
    gw = ScriptedModelGateway([COUNTED, COUNTED, GOOD])
    with pytest.raises(s.StageRejected, match="after 2 attempt"):
        s.draft(gw, Q, [LATE], model="demo")
    assert len(gw.calls) == 2


def test_redraft_uses_its_own_prompt_and_only_readings_with_kept_assertions():
    other = s.Material("other", [{"supplier_id": "S-001"}])
    gw = ScriptedModelGateway([GOOD])
    d = s.redraft(gw, Q, [LATE, other], {"late": ["S-038 had the most late lines, 6."]}, model="demo")
    prompt, cfg = gw.calls[0]
    assert cfg.prompt_identity == "synthesis_redraft@6"
    assert "[other]" not in prompt and d.answer("late")


def test_failed_draft_carries_every_call_and_why():
    gw = ScriptedModelGateway([COUNTED, COUNTED])
    with pytest.raises(s.StageRejected) as e:
        s.draft(gw, Q, [LATE], model="demo")
    assert len(e.value.responses) == 2 and len(e.value.rejections) == 2
    assert "outside a reference" in e.value.rejections[0]


def test_accepted_draft_carries_the_earlier_rejection():
    d = s.draft(ScriptedModelGateway([COUNTED, GOOD]), Q, [LATE], model="demo")
    assert len(d.responses) == 2 and len(d.rejections) == 1


def test_digit_from_the_rewrite_is_not_allowed():
    """The rewritten question is model text, so a digit in it does not license a digit in the answer."""
    seven = out(("late", ["In week 7, {late.supplier_id#1} had {late.late_lines#1}."]))
    with pytest.raises(s.StageRejected):
        s.draft(ScriptedModelGateway([seven, seven]), "And the week before?", [LATE], model="demo",
                rewritten="Which suppliers were late in week 7?")
    assert s.draft(ScriptedModelGateway([seven]), "Late in week 7?", [LATE], model="demo").answer("late")


def test_digit_from_the_plan_literals_is_allowed():
    """Review F7: a literal of the plan that ran, validated by the compiler and shown in Examined."""
    plan = dict(LATE_PLAN, WHERE={"AND": [{"EQ": [{"col": "commitments.week"}, {"value": 7}]},
                                          {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-038"}]}]},
                HAVING=[{"agg": "late_lines", "op": "GT", "value": 3}])
    m = s.material("late", plan, LATE.rows)
    assert m.literals == {"7", "s-038", "3"}
    seven = out(("late", ["In week 7, {late.supplier_id#1} had {late.late_lines#1}."]))
    assert s.draft(ScriptedModelGateway([seven]), "And the week before?", [m], model="demo",
                   rewritten="Which suppliers were late in week 7?").answer("late")
    counted = out(("late", ["2 suppliers in week 7, led by {late.supplier_id#1}."]))
    with pytest.raises(s.StageRejected, match="'2'"):
        s.draft(ScriptedModelGateway([counted, counted]), "And the week before?", [m], model="demo")


def test_both_forms_of_the_question_reach_synthesis():
    """Review F3, 4.9: later stages receive both forms."""
    text = s.render("And in October?", [LATE], rewritten="Which suppliers were late in October?")
    assert "Question as posed: And in October?\nQuestion: Which suppliers were late in October?" in text
    text = s.render_redraft("And in October?", [LATE], {"late": ["S-038 had 6."]},
                            rewritten="Which suppliers were late in October?")
    assert "Question as posed: And in October?" in text
    assert "Question as posed" not in s.render(Q, [LATE], rewritten=Q)

def test_filled_and_only_keep_sentence_order():
    d = draft_of(("late", ["{late.supplier_id#1} had {late.late_lines#1}.", "{late.supplier_id#2} followed."]))
    assert d.filled() == [("late", "S-038 had 6."), ("late", "S-014 followed.")]
    assert d.only([False, True]).answer("late") == "S-014 followed."
    assert d.only([False, False]).sentences == {}