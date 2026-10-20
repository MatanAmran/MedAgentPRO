"""
MedAgentPro - Phase B, item B1.
Conformance suite for the JSON schemas in medagentpro_schemas.json.

Each case states what the schema is claimed to do and asserts that it does it.
Run:  pytest test_schemas.py -q
"""

import json
import sys
from pathlib import Path

import pytest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    sys.exit("pip install jsonschema")

BUNDLE = Path(__file__).resolve().parents[2] / "query_assistance" / "medagentpro_schemas.json"


def load(name):
    b = json.loads(BUNDLE.read_text(encoding="utf-8"))
    s = b["schemas"][name]["schema"]
    Draft202012Validator.check_schema(s)
    return Draft202012Validator(s)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def plan(**over):
    p = {
        "SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "cid"}],
        "FROM": "commitments",
    }
    p.update(over)
    return p


def intent(readings=None, **over):
    d = {"disposition": "retrieve",
         "readings": readings if readings is not None
         else [{"label": "r1", "asked": "q", "plan": plan()}]}
    d.update(over)
    return d


CASES = []


def case(schema, name, instance, valid, why):
    CASES.append((schema, name, instance, valid, why))


# ==========================================================================
# 1. INTENT ANALYSIS
# ==========================================================================

case("intent_analysis", "minimal retrieve plan", intent(), True,
     "A reading is a label and a plan; nothing else is required.")

case("intent_analysis", "join, nested predicate, ordering, limit",
     intent([{"label": "commitment_below_forecast", "asked": "q", "plan": plan(
         SELECT=[{"expr": {"col": "commitments.item_id"}, "as": "item_id"},
                 {"expr": {"col": "commitments.quantity"}, "as": "committed"},
                 {"expr": {"col": "forecast.required"}, "as": "required"}],
         JOIN=[{"rel": "commitments__forecast", "type": "INNER"}],
         WHERE={"AND": [{"EQ": [{"col": "commitments.week"}, {"value": 12}]},
                        {"LT": [{"col": "commitments.quantity"},
                                {"col": "forecast.required"}]}]},
         ORDER_BY=[{"by": "committed", "dir": "ASC"}],
         LIMIT=20)}]), True,
     "The full clause set of the permitted subset parses.")

case("intent_analysis", "aggregate with group by and having",
     intent([{"label": "late_by_supplier", "asked": "q", "plan": plan(
         SELECT=[{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
                 {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}],
         WHERE={"BETWEEN": {"col": "commitments.week", "from": {"value": 1}, "to": {"value": 13}}},
         GROUP_BY=["supplier_id"],
         HAVING=[{"agg": "late_count", "op": "GT", "value": 3}],
         ORDER_BY=[{"by": "late_count", "dir": "DESC"}],
         LIMIT=50)}]), True,
     "Counts come from the store. GROUP_BY and HAVING name SELECT aliases.")

case("intent_analysis", "metric in SELECT",
     intent([{"label": "r", "asked": "q", "plan": plan(
         SELECT=[{"expr": {"metric": "late_count"}, "as": "n"}])}]), True,
     "A declared metric turns schema linking into a lookup.")

case("intent_analysis", "metric in WHERE",
     intent([{"label": "r", "asked": "q", "plan": plan(
         WHERE={"GT": [{"metric": "late_count"}, {"value": 3}]})}]), True,
     "A metric is an operand wherever a column is.")

case("intent_analysis", "IS_NULL",
     intent([{"label": "r", "asked": "q", "plan": plan(
         WHERE={"IS_NULL": {"col": "commitments.due_date"}})}]), True,
     "Absence is testable. Without it the model emits EQ against an empty "
     "string, which compiles and returns nothing for every such question.")

case("intent_analysis", "IS_NOT_NULL nested in AND",
     intent([{"label": "r", "asked": "q", "plan": plan(
         WHERE={"AND": [{"EQ": [{"col": "commitments.week"}, {"value": 12}]},
                        {"IS_NOT_NULL": {"col": "commitments.due_date"}}]})}]), True,
     "Unary operators compose with the boolean forms.")

case("intent_analysis", "unresolved as structured absence",
     intent(unresolved=[{"asked": "ולמה",
                         "needs": {"kind": "no_such_column",
                                   "entity": "commitments.delay_reason"}}]), True,
     "FR 11, second half. The model names a kind of absence, not a sentence.")

case("intent_analysis", "unresolved unspecified carries no entity",
     intent(unresolved=[{"asked": "ולמה", "needs": {"kind": "unspecified"}}]), True,
     "Where the model cannot identify what is missing it says only that.")

case("intent_analysis", "declared ambiguity",
     intent(ambiguous=[{"term": "late", "alternatives": [
         {"col": "commitments.delivered_at"},
         {"col": "commitments.promised_date"}]}]), True,
     "Single-call ambiguity signal. Feeds the multi-reading case of FR 11.")

case("intent_analysis", "referral carries no readings",
     {"disposition": "refer_to_investigation"}, True,
     "FR 14. A question requiring a cause is routed before any retrieval.")

case("intent_analysis", "a question that cannot be interpreted asks for it in full",
     {"disposition": "needs_clarification"}, True,
     "Chat, 18.10, 11:35 [demo extension]: a follow-up at the start of a conversation retrieves nothing.")

case("intent_analysis", "AVG over a column",
     intent([{"label": "r", "asked": "q", "plan": plan(
         SELECT=[{"expr": {"func": "AVG", "arg": {"col": "a.b"}}, "as": "q"}])}]),
     True, "Computed in the query and rounded there, so it is a field of a row like COUNT (chat, 18.10, 13:05).")

# --- rejections ---

case("intent_analysis", "raw SQL smuggled in",
     intent([{"label": "r", "asked": "q", "plan": dict(plan(), RAW="DROP TABLE commitments")}]),
     False, "additionalProperties is closed; the model cannot add a key.")

case("intent_analysis", "AVG over a metric",
     intent([{"label": "r", "asked": "q", "plan": plan(
         SELECT=[{"expr": {"func": "AVG", "arg": {"metric": "days_late"}}, "as": "q"}])}]),
     True, "Rule 4 sends the model to a metric, so it can be aggregated (chat, 18.10, 13:15; C4 of the "
           "held-out set). A boolean one is refused by the compiler, not the schema.")

case("intent_analysis", "COUNT over a metric",
     intent([{"label": "r", "asked": "q", "plan": plan(
         SELECT=[{"expr": {"func": "COUNT", "arg": {"metric": "late_by_delivery"}}, "as": "q"}])}]),
     False, "A boolean metric is counted with COUNT(*) and WHERE; COUNT takes a column or the star.")

case("intent_analysis", "SUM over star",
     intent([{"label": "r", "asked": "q", "plan": plan(
         SELECT=[{"expr": {"func": "SUM", "arg": "*"}, "as": "q"}])}]),
     False, "Only COUNT takes the star form.")

case("intent_analysis", "param emitted by the model",
     intent([{"label": "r", "asked": "q", "plan": plan(
         WHERE={"EQ": [{"col": "commitments.supplier_id"}, {"param": "USER"}]})}]),
     False, "NFR 17. Entitlement is injected during compilation, never by the model.")

case("intent_analysis", "SELECT item without an alias",
     intent([{"label": "r", "asked": "q", "plan": plan(
         SELECT=[{"col": "commitments.quantity"}])}]),
     False, "The alias is the second half of every downstream reference.")

case("intent_analysis", "LIMIT above the ceiling",
     intent([{"label": "r", "asked": "q", "plan": plan(LIMIT=100000)}]),
     False, "Test 13 rejects a value outside the permitted range.")

case("intent_analysis", "JOIN carrying its own condition",
     intent([{"label": "r", "asked": "q", "plan": plan(
         JOIN=[{"on": "a.id = b.id", "type": "INNER"}])}]),
     False, "Relations are declared in relations.json, never written by the model.")

case("intent_analysis", "subquery in IN",
     intent([{"label": "r", "asked": "q", "plan": plan(
         WHERE={"IN": {"col": "commitments.item_id", "values": {"SELECT": []}}})}]),
     False, "Subqueries are outside the permitted subset.")

case("intent_analysis", "predicate nested three deep",
     intent([{"label": "r", "asked": "q", "plan": plan(
         WHERE={"AND": [{"OR": [{"AND": [{"EQ": [{"col": "a.b"}, {"value": 1}]},
                                         {"EQ": [{"col": "a.b"}, {"value": 2}]}]},
                                {"EQ": [{"col": "a.b"}, {"value": 1}]}]},
                        {"EQ": [{"col": "a.b"}, {"value": 1}]}]})}]),
     False, "Nesting is bounded at two levels of grouping.")

case("intent_analysis", "IS_NULL applied to a value",
     intent([{"label": "r", "asked": "q", "plan": plan(WHERE={"IS_NULL": {"value": 3}})}]),
     False, "Absence is a property of a column.")

case("intent_analysis", "IS_NULL beside another operator",
     intent([{"label": "r", "asked": "q", "plan": plan(
         WHERE={"IS_NULL": {"col": "a.b"}, "EQ": [{"col": "a.b"}, {"value": 1}]})}]),
     False, "A comparison object carries exactly one operator.")

case("intent_analysis", "needs as free text",
     intent(unresolved=[{"asked": "ולמה",
                         "needs": "probably a supplier problem"}]),
     False, "NFR 20. A free-text field is a field a cause can be written into.")

case("intent_analysis", "unrecognised kind of absence",
     intent(unresolved=[{"asked": "x", "needs": {"kind": "maybe"}}]),
     False, "The kinds are a closed set.")

case("intent_analysis", "ambiguity with one alternative",
     intent(ambiguous=[{"term": "late", "alternatives": [{"col": "a.b"}]}]),
     False, "One alternative is not an ambiguity.")

case("intent_analysis", "ambiguity alternatives as strings",
     intent(ambiguous=[{"term": "late", "alternatives": ["delivered_at",
                                                         "promised_date"]}]),
     False, "Alternatives resolve against schema.json, so they are structured.")

case("intent_analysis", "referral carrying readings",
     {"disposition": "refer_to_investigation",
      "readings": [{"label": "r", "asked": "q", "plan": plan()}]},
     False, "A referred question plans no retrieval.")

case("intent_analysis", "clarification carrying readings",
     {"disposition": "needs_clarification", "readings": [{"label": "r", "asked": "q", "plan": plan()}]},
     False, "A question that could not be interpreted plans no retrieval: nothing is guessed.")

case("intent_analysis", "clarification carrying an ambiguity",
     {"disposition": "needs_clarification", "ambiguous": [{"term": "late", "alternatives": [
         {"col": "commitments.delivered_at"}, {"col": "commitments.promised_date"}]}]},
     False, "A term with more than one reading is declared ambiguous and answered, not sent back.")

case("intent_analysis", "retrieve with no readings",
     {"disposition": "retrieve", "readings": []},
     False, "Something must be retrieved for an answer to rest on.")

case("intent_analysis", "target left over from the two-store design",
     intent([{"label": "r", "asked": "q", "plan": dict(plan(), target="company")}]),
     False, "Query assistance reaches the company database only.")

case("intent_analysis", "kind left over from the two-retrieval design",
     intent([{"label": "r", "asked": "q", "plan": dict(plan(), kind="structured")}]),
     False, "Retrieval by meaning is not expressed, as stated in 3.1.6.")

# ==========================================================================
# 4. SUPPORT SCORING
# ==========================================================================

case("support_scoring", "supported and uncovered claims",
     {"claims": [
         {"sentence": 1, "text": "three suppliers were late",
          "verdict": "supported", "evidence": ["late.late_count"]},
         {"sentence": 2, "text": "the delays come from a raw material shortage",
          "verdict": "uncovered", "evidence": []}]}, True,
     "A verdict per assertion. uncovered carries no evidence by definition.")

case("support_scoring", "contradicted claim cites the field that refutes it",
     {"claims": [{"sentence": 1, "text": "SUP-114 was late 5 times",
                  "verdict": "contradicted",
                  "evidence": ["late.late_count"]}]}, True,
     "Altering material seen is distinct from asserting material absent.")

case("support_scoring", "model returns a score",
     {"claims": [], "score": 0.8}, False,
     "The score is computed in code. A model that scores itself is not a gate.")

case("support_scoring", "reasoning field smuggled in",
     {"claims": [{"sentence": 1, "text": "x", "verdict": "supported",
                  "evidence": ["a.b"], "reason": "likely supply chain"}]}, False,
     "NFR 20 begins here. A reason field leaks into the redraft.")

case("support_scoring", "unrecognised verdict",
     {"claims": [{"sentence": 1, "text": "x", "verdict": "partly", "evidence": []}]}, False,
     "Three categories, because 34 measured calls showed a number does not separate.")

case("support_scoring", "malformed reference",
     {"claims": [{"sentence": 1, "text": "x", "verdict": "supported",
                  "evidence": ["late_count"]}]}, False,
     "A reference is label.alias. A bare name resolves to nothing.")

case("support_scoring", "claim names no sentence",
     {"claims": [{"text": "x", "verdict": "uncovered", "evidence": []}]}, False,
     "[demo extension] Without the sentence number code cannot check that no sentence was skipped.")

case("support_scoring", "no claims at all",
     {"claims": []}, False,
     "An answer whose claims do not cover it has not been examined.")

# ==========================================================================
# EXCHANGE RESPONSE
# ==========================================================================

case("exchange_response", "answer with a gap",
     {"outcome": "answered",
      "readings": [{"label": "late", "title": "Reading 1", "row_count": 3,
                    "checked": "commitments in weeks 1 to 13 where the delivery "
                               "date is later than the due date",
                    "answer": "Three suppliers were late.",
                    "evidence": ["late.late_count"]}],
      "gaps": [{"asked": "ולמה",
                "missing": {"kind": "no_such_column",
                            "entity": "commitments.delay_reason"}}]}, True,
     "FR 11. An answer and a gap travel together.")

case("exchange_response", "three readings presented",
     {"outcome": "answered",
      "readings": [{"label": "a", "title": "Reading 1", "checked": "x", "row_count": 2},
                   {"label": "b", "title": "Reading 2", "checked": "y", "row_count": 5},
                   {"label": "c", "title": "Reading 3", "checked": "z", "row_count": 0}],
      "gaps": []}, True,
     "Test 32. Each reading is presented rather than one being selected.")

case("exchange_response", "empty retrieval is an answer",
     {"outcome": "answered",
      "readings": [{"label": "a", "title": "Reading 1", "checked": "x", "row_count": 0,
                    "answer": "No supplier was late."}],
      "gaps": []}, True,
     "Test 35. Emptiness is a finding about the data, not a failure.")

case("exchange_response", "reading without title",
     {"outcome": "answered",
      "readings": [{"label": "a", "checked": "x", "row_count": 1}],
      "gaps": []}, False,
     "Task 7, Q2 [demo extension]: every reading carries a title built in code.")

case("exchange_response", "ambiguous terms named",
     {"outcome": "answered",
      "readings": [{"label": "a", "title": "Reading 1", "checked": "x", "row_count": 1},
                   {"label": "b", "title": "Reading 2", "checked": "y", "row_count": 1}],
      "gaps": [], "ambiguous_terms": ["late"]}, True,
     "Task 7, Q4 [demo extension]: the term read in more than one way, a span of the question.")

case("exchange_response", "referral to investigation",
     {"outcome": "referred_to_investigation", "readings": [], "gaps": [],
      "offer_investigation": True}, True,
     "FR 14, carried through from the intent analysis disposition.")

case("exchange_response", "free-text gap",
     {"outcome": "answered", "readings": [],
      "gaps": [{"asked": "q", "missing": "no data, probably a supplier issue"}]},
     False, "The interface renders the sentence from a template keyed on kind.")

case("exchange_response", "reason field at the top level",
     {"outcome": "answered", "readings": [], "gaps": [],
      "reason": "likely supply chain"}, False,
     "Test 22. No field of this structure can carry a cause.")

case("exchange_response", "unrecognised outcome",
     {"outcome": "failed", "readings": [], "gaps": []}, False,
     "Five outcomes, determined in code from the retrieval classification.")

case("exchange_response", "four readings",
     {"outcome": "answered",
      "readings": [{"label": "a", "title": "Reading 1", "checked": "x", "row_count": 1}] * 4,
      "gaps": []}, False,
     "More parallel result sets than a person reads on one screen.")

case("exchange_response", "reading without checked",
     {"outcome": "answered",
      "readings": [{"label": "a", "title": "Reading 1", "row_count": 3, "answer": "Three."}],
      "gaps": []}, False,
     "FR 11. checked is the only exposure of a plan that addressed the wrong "
     "column, which no gate detects.")


# ==========================================================================
# runner
# ==========================================================================

_VALIDATORS = {}


def validator(schema):
    if schema not in _VALIDATORS:
        _VALIDATORS[schema] = load(schema)
    return _VALIDATORS[schema]


@pytest.mark.parametrize("schema", sorted({c[0] for c in CASES}))
def test_schema_is_well_formed(schema):
    validator(schema)   # load() runs check_schema


@pytest.mark.parametrize(
    "schema, name, instance, valid, why", CASES,
    ids=[f"{c[0]}/{c[1]}" for c in CASES])
def test_case(schema, name, instance, valid, why):
    errors = list(validator(schema).iter_errors(instance))
    if valid:
        assert not errors, f"rejected but should parse. {why} First error: {errors[0].message}"
    else:
        assert errors, f"parsed but should be rejected. {why}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
