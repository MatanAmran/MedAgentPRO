"""
Task 3 (docs/notes/92_TASK3_PLAN.md; chat 21.10, 12:50) [proposal, not in the book; Test 48 is a proposal]: every
path a reading that reads another reading takes through an exchange, and every limit of the plan language that
leaves a reading out with its part said (GAP-07). The compiler's own rules are in
tests/query_assistance/test_reading_refs.py. Scripted models only.
"""

from __future__ import annotations

import copy
import re
import sqlite3

import pytest

from demo_data.scripted import coverage_output, gate_claims
from model_client.client import ScriptedModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import compiler as c
from query_assistance import contract, coverage
from query_assistance import pipeline as pl
from shared.circuit_breaker import CircuitBreaker, MemoryStore
from tests.query_assistance.test_reading_refs import (
    APPROVER,
    CAT,
    FIRST,
    H4,
    INTENT_PROMPT,
    LATE,
    PLANNER,
    READINGS,
    SECOND,
    _planner_held,
    intent,
    open_value,
    ref,
    weeks,
)

# --------------------------------------------------------------------------
# the exchange, on the scripted model
# --------------------------------------------------------------------------

def scripted(*intents, sentences=None):
    """Every stage answered from what its prompt holds: synthesis one sentence per reading with rows, the gate
    supported, C every part recorded, B every part answered (coverage_output)."""
    plans = [intents[min(i, len(intents) - 1)] for i in range(4)]
    seen = {"intent": 0}

    def answer(prompt, cfg):
        stage = cfg.prompt_identity.split("@")[0]
        if stage.startswith("intent_analysis"):
            seen["intent"] += 1
            return plans[seen["intent"] - 1]
        if stage == "question_classification":
            return {"parts": [{"asked": r["asked"], "asks": "recorded"} for r in plans[0].get("readings", [])]}
        if stage == "synthesis":
            ours = {r["label"] for i in intents for r in i.get("readings", [])}   # not the prompt's examples
            labels = list(dict.fromkeys(m.group(1) for m in re.finditer(r"^\[(\w+)\] \1\.total = (\d+)", prompt,
                                                                        re.M)
                                        if int(m.group(2)) > 0 and m.group(1) in ours))

            def first(x):   # a sentence holds a row reference (schema 3): the first alias of row 1
                return re.search(r"^\[" + x + r"\] row 1: " + x + r"\.([a-z][a-z0-9_]*) = ", prompt, re.M).group(1)
            return {"readings": [{"label": x, "sentences": [{"text": (sentences or {}).get(
                x, "{" + x + ".total} rows matched, among them {" + x + "." + first(x) + "#1}.")}]}
                for x in labels]}
        if stage == "support_scoring":
            labels = re.findall(r"^\d+\. \[(\w+)\]", prompt.split("Answer:\n")[-1], re.M)
            return gate_claims(prompt, {"verdicts": ["supported"], "evidence": {x: ["total"] for x in labels}})
        if stage == "coverage_check":
            return coverage_output(prompt, None)
        raise AssertionError(stage)
    return ScriptedModelGateway(answer)


def run(db_path, question, *intents, user=APPROVER, cat=CAT, coverage_on=True):
    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pl.Deps(scripted(*intents), "demo", cat, lambda: sqlite3.connect(db_path), audit, INTENT_PROMPT,
                   coverage=coverage_on)
    return pl.run(question, [], user, deps)


def test_h4_is_answered_whole_with_examined_in_the_persons_words(db_path):
    """Test 48 [proposal]: the second part is answered over the result of the first, Examined says so in the
    person's words, and A finds "their" covered: no line about a part not answered."""
    out = run(db_path, H4, intent(LATE, open_value(ref())))
    r = out.response
    assert r["outcome"] == "answered" and [v["label"] for v in r["readings"]] == ["late_sep", "open_value"]
    assert f"supplier is one of all those in “{FIRST}”" in r["readings"][1]["checked"]
    assert not r.get("unanswered_parts")
    hist = next(n for n in out.exchange.readings if n.label == "open_value")
    assert FIRST in hist.examined


def test_examined_says_those_where_the_plan_wrote_a_limit_and_none_of_for_the_negation(db_path):
    top = copy.deepcopy(LATE)
    top["plan"]["LIMIT"] = 5
    one = contract.render_checked(open_value(ref())["plan"], CAT, "APPROVER", inner={"late_sep": top})
    assert f"supplier is one of those in “{FIRST}”" in one
    neg = contract.render_checked(open_value(ref("NOT_IN_READING"))["plan"], CAT, "APPROVER",
                                  inner={"late_sep": LATE})
    assert f"supplier is none of all those in “{FIRST}”" in neg


def _late_named(term: str) -> dict:
    late = copy.deepcopy(LATE)
    late["asked"] = f"Which lines of {term} were late in September"
    late["plan"]["JOIN"] = [{"rel": "commitment_supplier", "type": "INNER"}]
    late["plan"]["WHERE"]["AND"].append({"MATCHES": {"col": "suppliers.name", "term": term}})
    return late


@pytest.mark.parametrize("term", ["Zephyr", "Halcyon"])
def test_an_outer_reading_whose_inner_was_not_run_says_so(tmp_path, db_path, term):
    """2e: a name in the inner reading matched nothing or several; neither runs, and the outer says why, without
    the inner's candidates."""
    path = tmp_path / "demo.db"
    path.write_bytes(db_path.read_bytes())
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO suppliers (supplier_id, name, region, country) VALUES "
                     "('S-903', 'Halcyon Steel', 'AMER', 'XX')")
    late = _late_named(term)
    q = f"{late['asked']}, and {SECOND}?"
    r = run(path, q, intent(late, open_value(ref()))).response
    outer = next(v for v in r["readings"] if v["label"] == "open_value")
    assert outer["notice"] == contract.DEPENDS_NOT_RUN.format(asked=late["asked"])
    assert outer["rows"] == [] and "candidates" not in outer


def test_an_outer_reading_over_an_empty_inner_says_why_it_is_empty(db_path):
    empty = copy.deepcopy(LATE)
    empty["plan"]["WHERE"]["AND"][0] = {"BETWEEN": {"col": "commitments.delivered_at",
                                                    "from": {"value": "2030-09-01"}, "to": {"value": "2030-09-30"}}}
    empty["asked"] = "Which suppliers were late in September 2030"
    lines = {"label": "open_lines", "asked": "which of their lines are open", "plan": {
        "SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "commitment_id"}], "FROM": "commitments",
        "WHERE": {"AND": [{"IS_NULL": {"col": "commitments.delivered_at"}}, ref()]}}}
    q = f"{empty['asked']}, and which of their lines are open?"
    r = run(db_path, q, intent(empty, lines)).response
    outer = next(v for v in r["readings"] if v["label"] == "open_lines")
    assert outer["notice"] == contract.NONE_MATCHED_INNER.format(asked=empty["asked"])


@pytest.mark.parametrize("role, says", [("PLANNER", True), ("APPROVER", False)])
def test_an_absence_over_records_the_user_holds_says_so_in_the_answer(db_path, role, says):
    """Chat 21.10, 12:50, question 2: "none of those" under a restriction is a claim of absence (Test 35, F-C),
    so its condition is in the answer, not only in Examined; for each role the inner reading is restricted for."""
    cat = _planner_held()
    user = PLANNER if role == "PLANNER" else APPROVER
    r = run(db_path, H4, intent(LATE, open_value(ref("NOT_IN_READING"))), user=user, cat=cat).response
    outer = next(v for v in r["readings"] if v["label"] == "open_value")
    line = contract.LIMITED_ABSENCE.format(asked=FIRST)
    assert (line in (outer.get("notice") or "")) is says
    assert ("limited to the records you hold" in outer["checked"]) is says


LIMIT_CASES = {
    "reading_ref_depth": (H4, intent(LATE, open_value(ref(), label="middle", asked=FIRST),
                                     open_value(ref(reading="middle"), label="outer"))),
    "in_reading_under_or": (H4, intent(LATE, open_value({"OR": [ref(), {"IS_NULL": {
        "col": "commitments.promised_date"}}]}))),
    "duplicate_table": ("Which lines and suppliers, and what was delivered in those weeks?", intent(
        {"label": "lines", "asked": "Which lines and suppliers", "plan": {
            "SELECT": [{"expr": {"col": "commitments.commitment_id"}, "as": "commitment_id"}], "FROM": "commitments",
            "JOIN": [{"rel": "commitment_order", "type": "INNER"}, {"rel": "order_supplier", "type": "INNER"},
                     {"rel": "commitment_supplier", "type": "INNER"}]}},
        weeks(3) | {"asked": "what was delivered in those weeks"})),
    "in_values_limit": ("Which lines and suppliers, and what was delivered in those weeks?", intent(
        {"label": "lines", "asked": "Which lines and suppliers", "plan": {
            "SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"}], "FROM": "commitments"}},
        weeks(c.ROW_LIMIT + 1) | {"asked": "what was delivered in those weeks"})),
    "readings_limit": ("a b c d", intent(*READINGS)),
}


def test_every_language_limit_has_a_case_here():
    assert set(LIMIT_CASES) == set(c.LANGUAGE_LIMITS)


@pytest.mark.parametrize("code", c.LANGUAGE_LIMITS)
def test_a_reading_at_a_limit_of_the_language_is_said_not_answered_and_the_rest_is_answered(db_path, code):
    """GAP-07 (chat 21.10, 12:50), a kind: every code in LANGUAGE_LIMITS, on the last attempt, leaves its reading
    out and says its part with code's reason; a button only where asking the part alone can work."""
    question, plan = LIMIT_CASES[code]
    r = run(db_path, question, plan).response
    assert r["outcome"] == "answered" and r["readings"]
    part = next(u for u in r["unanswered_parts"] if coverage.REASONS[f"not_expressible:{code}"] in u["text"])
    assert part["text"] == contract.UNEXPRESSIBLE.format(span=part["asked"],
                                                         reason=coverage.REASONS[f"not_expressible:{code}"])
    assert ("ask" in part) is (code in coverage.ASKABLE_LIMITS)


def test_any_other_fault_of_the_plan_still_ends_as_not_completed(db_path):
    """Only LANGUAGE_LIMITS: a plan that compares the wrong columns is the model's fault (Test 13)."""
    r = run(db_path, H4, intent(LATE, open_value(ref(col="commitments.part_id")))).response
    assert r["outcome"] == "request_not_completed"


def test_show_all_of_a_reading_that_reads_another(db_path):
    """Show all records compiles the outer reading with the inner as it ran, entitlement afresh."""
    out = run(db_path, H4, intent(LATE, open_value(ref())))
    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pl.Deps(scripted(intent()), "demo", CAT, lambda: sqlite3.connect(db_path), audit, INTENT_PROMPT)
    inner = {label: {"asked": out.asked[label], "plan": plan} for label, plan in out.plans.items()}
    view = pl.show_all(out.exchange_id, "open_value", out.plans["open_value"], APPROVER, deps, [], inner=inner)
    assert view["rows"] == next(v for v in out.response["readings"] if v["label"] == "open_value")["rows"]
    assert f"one of all those in “{FIRST}”" in view["checked"]


def test_the_last_attempt_is_the_last_sent_even_after_a_plan_the_schema_refused(db_path):
    """Found building task 3: the attempts were counted where a plan is accepted, so a first plan the schema refused
    made the last attempt count as the first. A then sent the last plan back once more and the question ended in
    "not completed" (task 4 promised it never would). Counted where the prompt is sent, the last plan is taken and
    the part it left out is said."""
    broken = {"disposition": "retrieve", "readings": []}   # refused by the schema (minItems)
    r = run(db_path, H4, broken, intent(LATE)).response
    assert r["outcome"] == "answered" and [v["label"] for v in r["readings"]] == ["late_sep"]
    assert [u["asked"] for u in r["unanswered_parts"]] == ["value of their open orders"]
