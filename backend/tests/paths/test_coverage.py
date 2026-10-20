"""
Task 4 (R-4), the coverage check (docs/notes/91_COVERAGE_PLAN.md; chat 20.10, 22:30 to 22:50) [proposal, not in
the book; Test 47 is a proposal]: A in code, C the classification beside retrieval and synthesis, B the coverage
check after the gate. Every path the three add, on the demo store with scripted models, and none ends in "The
request could not be completed".
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from demo_data import seed
from query_assistance import contract
from query_assistance import pipeline as pl
from tests.paths.test_paths import APPROVER, LATE, SYN_ONE, Q, Raw, Script, Server, check_stream, gate_says, make_deps

H4 = "Which suppliers were late in September, and what is the value of their open orders?"
FIRST = "Which suppliers were late in September"
LEFT = "value of their open orders"   # what A finds no asked quotes: the run of uncovered words
_PROBES = json.loads((seed.HERE / "probe_set.json").read_text(encoding="utf-8"))["exchanges"]
LATE_SEP = next(e for e in _PROBES if e["id"] == "H4")["plan"][0]["readings"][0]
H4_INTENT = {"disposition": "retrieve", "readings": [LATE_SEP]}   # the second part neither read nor declared
H4_SYN = {"readings": [{"label": "late_sep", "sentences": [
    {"text": "{late_sep.supplier_id#1} had the most lines delivered after the requested date in September, "
             "{late_sep.late_lines#1}."}]}]}
H4_PARTS = {"parts": [{"asked": FIRST, "asks": "recorded"},
                      {"asked": "what is the value of their open orders", "asks": "recorded"}]}
MESSAGE = "Your question also asked {part}; that part {reason}."
FOLLOW_UP = "What is the value of the open orders of the suppliers that were late in September?"

REASONS_Q = "What delay reasons were recorded for S-038's late lines?"
CAUSE_Q = "What caused the delays at S-038?"
_SCRIPTS = json.loads((seed.HERE / "scripted_exchanges.json").read_text(encoding="utf-8"))["exchanges"]
_Q06 = next(e for e in _SCRIPTS if e["question"] == REASONS_Q)
REASONS = {**_Q06["intent"]["readings"][0], "asked": REASONS_Q}
REASONS_INTENT = {"disposition": "retrieve", "readings": [REASONS]}
CAUSE_INTENT = {"disposition": "retrieve", "readings": [{**REASONS, "asked": CAUSE_Q}]}
RECORDED = "For that line, the delay is recorded as {recorded_reasons.delay_reason#1}."
CAUSAL = "{recorded_reasons.po_id#1} was late because {recorded_reasons.delay_reason#1}."
REASONS_SYN = {"readings": [{"label": "recorded_reasons", "sentences": [
    {"text": "{recorded_reasons.total} lines of S-038 have a delay reason recorded, among them a line of "
             "{recorded_reasons.po_id#1}."}, {"text": RECORDED}]}]}
CAUSAL_SYN = {"readings": [{"label": "recorded_reasons", "sentences": [{"text": CAUSAL}]}]}


def reason_gate(prompt):
    out = gate_says("supported")(prompt)
    for c in out["claims"]:
        c["evidence"] = ["recorded_reasons.delay_reason"]
    return out


def covering(gw, db_path):
    return replace(make_deps(gw, db_path), coverage=True)


def check(parts: list[dict], kinds: list[str]) -> dict:
    return {"parts": parts, "sentences": [{"n": n, "kind": k} for n, k in enumerate(kinds, 1)]}


def stages(gw) -> list[str]:
    return [cfg.prompt_identity.split("@")[0] for _, cfg in gw.calls]


def h4_script(**over):
    base = {"intent_analysis_demo": [H4_INTENT, H4_INTENT], "question_classification": [H4_PARTS],
            "synthesis": [H4_SYN], "support_scoring": [gate_says("supported")],
            "coverage_check": [check([{"asked": FIRST, "status": "answered", "sentences": [1]},
                                      {"asked": LEFT, "status": "not_answered", "sentences": [],
                                       "message": MESSAGE, "follow_up": FOLLOW_UP}], ["no_relation"])]}
    return Script(**{**base, **over})


# --------------------------------------------------------------------------
# A and B: a part no reading answers is said, in the model's words with code's facts
# --------------------------------------------------------------------------

@pytest.mark.asked_as_written
def test_a_part_left_out_is_said_in_the_models_words_with_codes_facts(db_path):
    gw = h4_script()
    out = pl.run(H4, [], APPROVER, covering(gw, db_path))
    r = out.response
    assert r["outcome"] == "answered" and r["readings"][0]["answer"]
    # A: the plan that left the part unquoted was sent back once, then taken (no "not completed")
    assert stages(gw).count("intent_analysis_demo") == 2
    assert "value of their open orders" in gw.calls[1][0]
    part, = r["unanswered_parts"]
    assert part["text"] == f"Your question also asked “{LEFT}”; that part {coverage_reason()}."
    assert part["ask"] == f'Ask: "{LEFT}"' and part["follow_up"] == FOLLOW_UP
    assert stages(gw)[-2:] == ["support_scoring", "coverage_check"]


def coverage_reason() -> str:
    from query_assistance import coverage
    return coverage.REASONS["uncovered"]


@pytest.mark.asked_as_written
@pytest.mark.parametrize("message, follow_up", [
    ("Your question also asked {part}, which is not in the answer.", "What about S-999?"),   # negation; unknown id
    ("{part} {reason} because of the data.", "no question mark"),                             # causal; not a question
    ("Your question also asked {part}.", FOLLOW_UP + " ?" * 100),                              # no {reason}; too long
])
def test_a_message_that_fails_a_check_falls_back_to_the_template(db_path, message, follow_up):
    gw = h4_script(coverage_check=[check([{"asked": FIRST, "status": "answered", "sentences": [1]},
                                          {"asked": LEFT, "status": "not_answered", "sentences": [],
                                           "message": message, "follow_up": follow_up}], ["no_relation"])])
    part, = pl.run(H4, [], APPROVER, covering(gw, db_path)).response["unanswered_parts"]
    assert part["text"] == contract.UNANSWERED.format(span=LEFT)
    assert part["follow_up"] == LEFT   # the part as the person wrote it


# --------------------------------------------------------------------------
# C: a part that asks for a cause is not answered from the records
# --------------------------------------------------------------------------

@pytest.mark.asked_as_written
def test_a_cause_part_is_referred_with_what_was_recorded_a_click_away(db_path):
    gw = Script(intent_analysis_demo=[CAUSE_INTENT],
                question_classification=[{"parts": [{"asked": CAUSE_Q, "asks": "cause"}]}],
                synthesis=[REASONS_SYN], support_scoring=[reason_gate])
    r = pl.run(CAUSE_Q, [], APPROVER, covering(gw, db_path)).response
    assert r["outcome"] == "referred_to_investigation" and r["readings"] == [] and r["offer_investigation"]
    assert r["notice"] == contract.REFERRED
    part, = r["referred_parts"]
    assert part == contract.referred_part(CAUSE_Q, "cause", ["recorded_reasons"])
    assert "coverage_check" not in stages(gw)   # nothing left to check


@pytest.mark.asked_as_written
def test_a_cause_part_over_records_that_hold_nothing_is_referred(db_path):
    empty = {**CAUSE_INTENT["readings"][0], "plan": {**REASONS["plan"], "WHERE": {"AND": [
        {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-040"}]},
        {"IS_NOT_NULL": {"col": "commitments.delay_reason"}}]}}}
    q = "What caused the delays at S-040?"
    gw = Script(intent_analysis_demo=[{"disposition": "retrieve", "readings": [{**empty, "asked": q}]}],
                question_classification=[{"parts": [{"asked": q, "asks": "cause"}]}])
    r = pl.run(q, [], APPROVER, covering(gw, db_path)).response
    assert r["outcome"] == "referred_to_investigation" and r["referred_parts"][0]["asks"] == "cause"


@pytest.mark.asked_as_written
def test_a_recorded_reason_asked_for_stays_answered(db_path):
    """The other direction (89, section 3.5): a question that asks what reason was recorded is answered, every
    sentence kept. Q06 and D4 are the measured cases."""
    gw = Script(intent_analysis_demo=[REASONS_INTENT],
                question_classification=[{"parts": [{"asked": REASONS_Q, "asks": "recorded"}]}],
                synthesis=[REASONS_SYN], support_scoring=[reason_gate],
                coverage_check=[check([{"asked": REASONS_Q, "status": "answered", "sentences": [1, 2]}],
                                      ["no_relation", "no_relation"])])
    r = pl.run(REASONS_Q, [], APPROVER, covering(gw, db_path)).response
    assert r["outcome"] == "answered" and "replaced" not in r and "referred_parts" not in r
    assert "delay is recorded as" in r["readings"][0]["answer"]
    assert "coverage_check" in stages(gw)   # S3: a sentence cites a recorded reason, so B runs


@pytest.mark.asked_as_written
def test_a_causal_sentence_is_replaced_by_the_value_recorded(db_path):
    gw = Script(intent_analysis_demo=[REASONS_INTENT],
                question_classification=[{"parts": [{"asked": REASONS_Q, "asks": "recorded"}]}],
                synthesis=[CAUSAL_SYN], support_scoring=[reason_gate],
                coverage_check=[check([{"asked": REASONS_Q, "status": "answered", "sentences": [1]}],
                                      ["direct_causal"])])
    r = pl.run(REASONS_Q, [], APPROVER, covering(gw, db_path)).response
    answer = r["readings"][0]["answer"]
    assert "because" not in answer and "recorded as" in answer and "Why it happened is for an investigation" in answer
    assert r["replaced"] == contract.REPLACED and r["offer_investigation"] is True


# --------------------------------------------------------------------------
# a check that could not be completed: the cautious side, and a notice, never "not completed"
# --------------------------------------------------------------------------

FAILURES = [pytest.param([{"parts": [{"asked": "not in the question", "asks": "recorded"}]}] * 2, id="rejected"),
            pytest.param([Raw('{"parts": [', finish_reason="length")], id="failed"),
            pytest.param([RuntimeError("provider down")], id="exception")]


@pytest.mark.asked_as_written
@pytest.mark.parametrize("answers", FAILURES)
def test_classification_not_completed_states_recorded_reasons_as_recorded(db_path, answers):
    gw = Script(intent_analysis_demo=[REASONS_INTENT], question_classification=answers,
                synthesis=[CAUSAL_SYN], support_scoring=[reason_gate],
                coverage_check=[check([{"asked": REASONS_Q, "status": "answered", "sentences": [1]}],
                                      ["no_relation"])])
    r = pl.run(REASONS_Q, [], APPROVER, covering(gw, db_path)).response
    assert r["outcome"] == "answered" and r["coverage_notice"] == contract.CLASSIFY_FAILED
    assert "because" not in r["readings"][0]["answer"] and r["replaced"] == contract.REPLACED


@pytest.mark.asked_as_written
@pytest.mark.parametrize("answers", [
    pytest.param([check([{"asked": "another part", "status": "answered", "sentences": [1]}], ["no_relation"])] * 2,
                 id="rejected"),
    pytest.param([Raw('{"parts": [', finish_reason="length")], id="failed"),
    pytest.param([RuntimeError("provider down")], id="exception")])
def test_coverage_check_not_completed_keeps_the_answer_and_says_so(db_path, answers):
    gw = Script(intent_analysis_demo=[REASONS_INTENT],
                question_classification=[{"parts": [{"asked": REASONS_Q, "asks": "recorded"}]}],
                synthesis=[CAUSAL_SYN], support_scoring=[reason_gate], coverage_check=answers)
    r = pl.run(REASONS_Q, [], APPROVER, covering(gw, db_path)).response
    assert r["outcome"] == "answered" and r["coverage_notice"] == contract.COVERAGE_FAILED
    assert "because" not in r["readings"][0]["answer"]   # the cautious side: a recorded reason as recorded


@pytest.mark.asked_as_written
@pytest.mark.parametrize("stage", ["question_classification", "coverage_check"])
def test_a_cancellation_during_either_check_ends_the_exchange(db_path, stage):
    script = {"intent_analysis_demo": [REASONS_INTENT],
              "question_classification": [{"parts": [{"asked": REASONS_Q, "asks": "recorded"}]}],
              "synthesis": [REASONS_SYN], "support_scoring": [reason_gate], "coverage_check": []}
    script[stage] = [pl.ExchangeCancelled("the client left")]
    r = pl.run(REASONS_Q, [], APPROVER, covering(Script(**script), db_path)).response
    assert r["outcome"] == "request_not_completed"


# --------------------------------------------------------------------------
# when B is skipped: only where every condition of COVERAGE_SKIP holds
# --------------------------------------------------------------------------

def test_one_plain_part_skips_the_coverage_check(db_path):
    gw = Script(intent_analysis_demo=[{"disposition": "retrieve", "readings": [{**LATE, "asked": Q}]}],
        question_classification=[{"parts": [{"asked": Q, "asks": "recorded"}]}],
        synthesis=[SYN_ONE], support_scoring=[gate_says("supported")])
    r = pl.run(Q, [], APPROVER, covering(gw, db_path)).response
    assert r["outcome"] == "answered" and "coverage_check" not in stages(gw)
    assert "unanswered_parts" not in r and "coverage_notice" not in r


# --------------------------------------------------------------------------
# the server: a part asked on its own, and what was recorded for a referred part
# --------------------------------------------------------------------------

@pytest.mark.asked_as_written
def test_server_part_asked_on_its_own_sends_the_checked_question(db_path):
    """Chat 22:50: the button shows the person's words; a click sends the model's question as the server holds it,
    shown as Understood as, with no rewriting call. The client sends no question text of its own."""
    follow = {"disposition": "retrieve", "readings": [{**LATE_SEP, "asked": FOLLOW_UP}]}
    by = {H4: h4_script(),
          LEFT: Script(intent_analysis_demo=[follow],
                       question_classification=[{"parts": [{"asked": FOLLOW_UP, "asks": "recorded"}]}],
                       synthesis=[H4_SYN], support_scoring=[gate_says("supported")])}
    s = Server(db_path, by_question=by)
    cid = s.conversation()
    first = check_stream(s.ask(cid, H4))
    assert first["response"]["unanswered_parts"][0]["follow_up"] == FOLLOW_UP
    r = s.client.post(f"/api/conversations/{cid}/exchanges", headers={"X-Demo-User": "AP-01"},
                      json={"question": "ignored", "request_id": "part-of-0001",
                            "part_of": {"exchange_id": first["exchange_id"], "part": 0}})
    second = check_stream(r)
    assert second["response"]["understood_as"] == FOLLOW_UP and second["response"]["outcome"] == "answered"
    assert "question_rewriting" not in stages(by[LEFT])
    exchanges = s.client.get(f"/api/conversations/{cid}", headers={"X-Demo-User": "AP-01"}).json()["exchanges"]
    assert exchanges[-1]["posed"] == LEFT   # what the person is shown as having asked: their own words
    bad = s.client.post(f"/api/conversations/{cid}/exchanges", headers={"X-Demo-User": "AP-01"},
                        json={"question": "x", "request_id": "part-of-0002",
                              "part_of": {"exchange_id": first["exchange_id"], "part": 3}})
    assert bad.status_code == 404


@pytest.mark.asked_as_written
def test_server_shows_what_was_recorded_for_a_referred_part(db_path):
    by = {CAUSE_Q: Script(intent_analysis_demo=[CAUSE_INTENT],
                          question_classification=[{"parts": [{"asked": CAUSE_Q, "asks": "cause"}]}],
                          synthesis=[REASONS_SYN], support_scoring=[reason_gate])}
    s = Server(db_path, by_question=by)
    res = check_stream(s.ask(s.conversation(), CAUSE_Q))
    label, = res["response"]["referred_parts"][0]["show_recorded"]
    view = s.client.get(f"/api/exchanges/{res['exchange_id']}/readings/{label}/records",
                        headers={"X-Demo-User": "AP-01"}).json()
    assert view["notice"].startswith(contract.SHOW_RECORDED) and view["rows"] and "answer" not in view


@pytest.mark.asked_as_written
def test_an_exchange_stopped_before_synthesis_counts_what_the_classification_spent(db_path, ledger):
    """CI, 21.10 (the stress run): an exchange the budget stops before synthesis while C still runs. C is joined
    before the cost is taken, so what it spent is in the exchange's cost and in the record, never only in the
    ledger."""
    import time

    from model_client import budget as b

    def slow(prompt):
        time.sleep(0.3)
        return {"parts": [{"asked": REASONS_Q, "asks": "recorded"}]}

    env = {"ANTHROPIC_API_KEY": "test-key-not-real", "MODEL_ID": "test-model", "MODEL_PRICE_IN": "2",
           "MODEL_PRICE_OUT": "10", "MODEL_BUDGET_USD": "5", "MODEL_MAX_USD_PER_EXCHANGE": "0.06",
           "MODEL_MAX_OUTPUT_TOKENS": "2000", "MODEL_MAX_EXCHANGES_PER_MINUTE": "1000"}
    gw = b.BudgetedGateway(Script(intent_analysis_demo=[REASONS_INTENT], question_classification=[slow]),
                           b.BudgetConfig.from_env(env), ledger)
    d = replace(make_deps(gw, db_path), model="test-model", coverage=True)
    out = pl.run(REASONS_Q, [], APPROVER, d)
    assert out.response["notice"] == contract.EXCHANGE_CAP
    assert ledger.total == pytest.approx(out.cost["exchange_usd"], abs=1e-6)
    trail = d.audit.storage.audit_trail(out.exchange_id)
    assert [e["agent"] for e in trail].count("question_classification") == 1
