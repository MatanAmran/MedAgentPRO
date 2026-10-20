"""Task 4 (91_COVERAGE_PLAN; chat 20.10, 22:30 to 22:50), the coverage check's rules one at a time, as kinds and
not as cases (L2): every word of each closed list, every slot, every skip condition, every outcome C and B can
give. No model, no store."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from demo_data import seed
from query_assistance import compiler as c
from query_assistance import contract, coverage
from query_assistance.compiler import FUNCTION_WORDS, Conversation, uncovered
from query_assistance.synthesis import Draft, Material, Sentence

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
SPAN = "which parts are below their reorder point"
GOOD = "Your question also asked {part}; that part {reason}."
_BUNDLE_ENUMS = (coverage.CLASSIFY_V.schema["properties"]["parts"]["items"]["properties"]["asks"]["enum"],
                 coverage.CHECK_V.schema["properties"]["sentences"]["items"]["properties"]["kind"]["enum"])


# --------------------------------------------------------------------------
# A: every content word quoted
# --------------------------------------------------------------------------

WORDS = st.sampled_from(["suppliers", "late", "September", "orders", "value", "S-014", "parts", "week", "7"])
FILLER = st.sampled_from(sorted(w for w in FUNCTION_WORDS if w.isascii()))


@pytest.mark.property
@given(st.lists(st.one_of(WORDS, FILLER), min_size=1, max_size=12), st.data())
def test_a_word_is_left_out_exactly_where_no_span_quotes_it(words, data):
    question = " ".join(words)
    quoted = data.draw(st.lists(st.sampled_from(words), max_size=len(words)))
    left = " ".join(uncovered(question, [" ".join(quoted)])).casefold().split()
    for w in words:
        if w.casefold() in FUNCTION_WORDS:
            continue
        assert (w.casefold() in left) == (w.casefold() not in {q.casefold() for q in quoted})


def test_function_words_alone_are_never_a_part():
    assert uncovered("which of the and or their", []) == []
    assert uncovered("אילו של עם", []) == []   # the Hebrew ones too (decision 7)


@pytest.mark.asked_as_written
def test_a_readings_asked_must_be_the_persons_own_words():
    intent = {"disposition": "retrieve", "readings": [{"label": "r", "asked": "something never asked", "plan": {
        "SELECT": [{"expr": {"col": "suppliers.supplier_id"}, "as": "s"}], "FROM": "suppliers"}}]}
    with pytest.raises(c.CompileError, match="span_not_in_question"):
        c.compile_intent(intent, "Which suppliers are there?", {"role": "APPROVER", "user_id": "AP-01"}, CAT)


# --------------------------------------------------------------------------
# the model's message: every slot, every word of each closed list
# --------------------------------------------------------------------------

def test_a_good_message_is_filled_by_code():
    assert coverage.message(GOOD, SPAN, "uncovered", CAT) == (
        f"Your question also asked “{SPAN}”; that part {coverage.REASONS['uncovered']}.")


@pytest.mark.parametrize("table", sorted(CAT.entities))
def test_an_entity_slot_is_filled_with_the_catalogs_label_and_its_raw_name_is_refused(table):
    filled = coverage.message("{part} {reason}, as the {entity:" + table + "} were read.", SPAN, "uncovered", CAT)
    assert filled and CAT.entities[table].get("label", table) in filled
    assert coverage.message("{part} {reason}, as the " + table + " were read.", SPAN, "uncovered", CAT) is None


@pytest.mark.parametrize("word", sorted(coverage.NEGATION) + ["isn't", "wasn't"])
def test_every_negation_outside_the_reason_slot_is_refused(word):
    assert coverage.message(f"{{part}} {{reason}}, {word} more.", SPAN, "uncovered", CAT) is None


@pytest.mark.parametrize("word", sorted(coverage.CAUSAL))
def test_every_causal_word_is_refused(word):
    assert coverage.message(f"{{part}} {{reason}}, {word} the records.", SPAN, "uncovered", CAT) is None


@pytest.mark.parametrize("bad", [
    "Your question also asked; that part {reason}.",                    # {part} missing
    f"Your question also asked {SPAN}; that part {{reason}}.",          # the part's value without its slot
    "{part} and again {part} {reason}.",                                # {part} twice
    "Your question also asked {part}.",                                 # {reason} missing
    "{part} {reason} in week 7.",                                       # a digit
    "{part} {reason} in {entity:nope}.",                                # an entity the catalog lacks
    "{part} {reason} {other}.",                                         # a slot code does not know
    "{part} {reason} " + "x" * coverage.MAX_MESSAGE,                    # too long
    "{part} {reason}: see delay_reason.",                               # a column's raw name
])
def test_a_message_that_breaks_a_rule_is_refused(bad):
    assert coverage.message(bad, SPAN, "uncovered", CAT) is None


def test_every_reason_code_gives_is_a_closed_sentence():
    for key, text in coverage.REASONS.items():
        assert coverage.message(GOOD, SPAN, key, CAT).endswith(text + ".")
    assert coverage.message(GOOD, SPAN, "made up", CAT) is None


# --------------------------------------------------------------------------
# the model's follow-up question
# --------------------------------------------------------------------------

CONV = Conversation("Which suppliers were late in week 7, and what did S-014 deliver?",
                    earlier=["Which suppliers were late in weeks 1 to 13?"], presented=["S-038"])


@pytest.mark.parametrize("text, ok", [
    ("What did S-014 deliver?", True),                      # an identifier the question holds
    ("What did S-038 deliver?", True),                      # one an earlier answer presented
    ("What did S-099 deliver?", False),                     # one from nowhere (NFR 27)
    ("What did S-014 deliver in week 9?", False),           # a digit the question does not hold
    ("What did S-014 deliver", False),                      # not a question
    ("What did S-014 deliver" + "?" * coverage.MAX_FOLLOW_UP, False),
])
def test_a_follow_up_is_sent_only_where_its_checks_hold(text, ok):
    assert (coverage.follow_up(text, CONV.question, CONV) is not None) == ok


# --------------------------------------------------------------------------
# when B is skipped, and what C's and B's outcomes do
# --------------------------------------------------------------------------

def _draft(cites_reason: bool) -> Draft:
    m = Material("r", [{"po_id": "PO-1", "delay_reason": "x"}], {"po_id": "commitments.po_id",
                                                                   "delay_reason": "commitments.delay_reason"})
    refs = [("r", "delay_reason" if cites_reason else "po_id", 1)]
    return Draft({"r": [Sentence("{r.po_id#1}" if not cites_reason else "{r.delay_reason#1}", refs)]}, {"r": m})


def test_the_skip_list_is_the_closed_list_and_each_condition_alone_makes_b_run():
    one = [coverage.Part("q", ["r"])]
    held = coverage.skip_conditions(one, _draft(False), CAT, carried=False)
    assert tuple(held) == coverage.COVERAGE_SKIP and all(held.values())
    breaks = {
        "S1_one_part": ([coverage.Part("q", ["r"]), coverage.Part("p", ["s"])], False, False),
        "S2_nothing_unresolved": ([coverage.Part("q", ["r"]), coverage.Part("p", declared=True)], False, False),
        "S3_no_recorded_reason": (one, True, False),
        "S4_not_from_history": (one, False, True),
    }
    assert set(breaks) == set(coverage.COVERAGE_SKIP)
    for name, (parts, reason, carried) in breaks.items():
        now = coverage.skip_conditions(parts, _draft(reason), CAT, carried)
        assert [k for k, v in now.items() if not v] == [name]


def test_every_outcome_c_and_b_can_give_has_an_action():
    asks, kinds = _BUNDLE_ENUMS
    assert set(asks) - {"recorded"} == set(contract.REFERRED_PART)
    assert set(coverage.CAUSAL_KINDS) < set(kinds) and "no_relation" in kinds


def test_a_cause_part_falls_on_the_part_it_overlaps_most_and_a_recorded_part_on_none():
    parts = [coverage.Part("Which loans were returned after their due date", ["late"]),
             coverage.Part("why were they returned so late", ["why"])]
    got = coverage.referred([{"asked": "why were they returned so late", "asks": "cause"},
                             {"asked": "Which loans were returned after their due date", "asks": "recorded"}], parts)
    assert [(r["asked"], r["labels"]) for r in got] == [("why were they returned so late", ["why"])]


def test_a_causal_sentence_without_a_recorded_reason_is_removed_not_rewritten():
    d, replaced, removed = coverage.replace_reason_sentences(_draft(False), CAT, {1})
    assert (replaced, removed) == (0, 1) and not d.sentences
    d, replaced, removed = coverage.replace_reason_sentences(_draft(True), CAT, {1})
    assert (replaced, removed) == (1, 0)
    assert d.sentences["r"][0].template.endswith("Why it happened is for an investigation.")


def test_what_b_reads_says_what_each_field_is():
    """L4 and GAP-13: the parts numbered, a declared part marked, each sentence numbered with its reading, as
    references, never a value."""
    text = coverage.render_check("Q?", None, [coverage.Part("a", ["r"]), coverage.Part("b", declared=True)],
                                 _draft(True), CAT, coverage.prompts.load("coverage_check.md"))
    tail = text.split("\nParts:\n")[-1]
    assert "1. a\n2. b (declared)" in tail and "1. [r] {r.delay_reason#1}" in tail and "x" not in tail.split(
        "Answer:\n")[-1].split("Output:")[0].replace("examined", "")
