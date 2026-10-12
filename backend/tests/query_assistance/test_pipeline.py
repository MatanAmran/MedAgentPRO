"""
Tests for pipeline.py: one exchange end to end on the demo store, with the scripted model (6.1).
Run:  pytest -q

The scripted model answers by prompt. The gate's verdicts are written per sentence number, read
from the numbered answer the gate prompt carries, so a test states only which sentences hold.
"""

import logging
import re
import sqlite3
import threading

import pytest

from demo_data import seed
from model_client.client import ScriptedModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import compiler as c
from query_assistance import contract
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from shared.circuit_breaker import CircuitBreaker, MemoryStore

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
INTENT_PROMPT = p.load(seed.HERE / "intent_analysis.md")
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}
Q = "Which suppliers delivered late in weeks 1 to 13?"

LATE = {"label": "late", "plan": {
    "SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
               {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}],
    "FROM": "commitments",
    "WHERE": {"AND": [{"BETWEEN": [{"col": "commitments.week"}, {"value": 1}, {"value": 13}]},
                      {"EQ": [{"metric": "late_by_delivery"}, {"value": True}]}]},
    "GROUP_BY": ["supplier_id"], "ORDER_BY": [{"by": "late_count", "dir": "DESC"}], "LIMIT": 5}}
PROMISE = {"label": "promise", "plan": dict(LATE["plan"], WHERE={"AND": [
    {"BETWEEN": [{"col": "commitments.week"}, {"value": 1}, {"value": 13}]},
    {"EQ": [{"metric": "late_by_promise"}, {"value": True}]}]})}
INTENT = {"disposition": "retrieve", "readings": [LATE]}
TWO = {"disposition": "retrieve", "ambiguous": [{"term": "late", "alternatives": [
    {"metric": "late_by_delivery"}, {"metric": "late_by_promise"}]}], "readings": [LATE, PROMISE]}

ONE = "{late.supplier_id#1} had the most late lines, {late.late_count#1}."
TWO_S = "{late.supplier_id#2} followed with {late.late_count#2}."
SYN = {"readings": [{"label": "late", "sentences": [{"text": ONE}, {"text": TWO_S}]}]}
SYN_ONE = {"readings": [{"label": "late", "sentences": [{"text": ONE}]}]}


def gate_says(*verdicts, alias="supplier_id"):
    """Verdict per sentence, in order. The last one repeats. Every sentence is covered."""
    def answer(prompt):
        body = prompt.split("Answer:\n")[-1].split("\nRecords:")[0]
        claims = []
        for i, (n, label, text) in enumerate(re.findall(r"^(\d+)\. \[(\w+)\] (.*)$", body, re.M)):
            v = verdicts[min(i, len(verdicts) - 1)]
            claims.append({"sentence": int(n), "text": text, "verdict": v,
                           "evidence": [] if v == "uncovered" else [f"{label}.{alias}"]})
        return {"claims": claims}
    return answer


def skips_last(prompt):
    out = gate_says("supported")(prompt)
    out["claims"] = out["claims"][:-1]
    return out


def model(**by_prompt):
    """Scripted outputs per prompt name, used in order. A callable receives the prompt."""
    queues = {k: list(v) for k, v in by_prompt.items()}

    def answer(prompt, cfg):
        out = queues[cfg.prompt_identity.split("@")[0]].pop(0)
        return out(prompt) if callable(out) else out
    return ScriptedModelGateway(answer)


def called(gw, name):
    return [cfg.prompt_identity for _, cfg in gw.calls if cfg.prompt_identity.startswith(name + "@")]


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.db"
    seed.build(path)
    return path


@pytest.fixture
def storage():
    return MemoryStorage()


def deps(gw, db_path, storage, connect=None, audit=None):
    audit = audit or AuditRepository(storage, CircuitBreaker("system_store", MemoryStore()), b"test-key")
    return pl.Deps(gw, "demo", CAT, connect or (lambda: sqlite3.connect(db_path)), audit, INTENT_PROMPT)


def trail(storage, eid):
    return sorted((r for r in storage.audit_log if r["investigation_id"] == eid), key=lambda r: r["seq"])


# --------------------------------------------------------------------------
# answered
# --------------------------------------------------------------------------

def test_answer_with_every_sentence_supported(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    out = pl.run(Q, [], APPROVER, deps(gw, db_path, storage))
    r = out.response
    assert r["outcome"] == "answered" and "withheld" not in r
    view, = r["readings"]
    assert view["answer"].startswith(view["rows"][0]["supplier_id"] + " had the most late lines, ")
    assert view["checked"].startswith("Examined: commitments") and view["evidence"] == ["late.supplier_id",
                                                                                         "late.late_count"]
    assert len(gw.calls) == 3 and out.recorded


def test_exchange_record_follows_the_declared_stage_order(db_path, storage):
    """Test 21: every stage in order, a timestamp on each, model and tokens where a model was called."""
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    d = deps(gw, db_path, storage)
    out = pl.run(Q, [], APPROVER, d)
    t = trail(storage, out.exchange_id)
    assert [e["agent"] for e in t] == ["intent_analysis", "compilation", "retrieval", "synthesis",
                                       "support_gate", "output_contract"]
    assert [e["step_ordinal"] for e in t] == sorted(e["step_ordinal"] for e in t)
    for e in t:
        assert e["timestamp"] and e["role"] == "APPROVER" and e["actor_id"] == "AP-01" and "on_behalf_of" not in e
        if e["event_type"].startswith("model_output"):
            assert "scripted:demo" in e["model_identity"] and "temperature provider default" in e["model_identity"]
            assert e["input_tokens"] and e["output_tokens"]
        else:
            assert "input_tokens" not in e
    assert "SELECT" in t[1]["evidence_reviewed"][0]["sql"]
    doc = storage.investigations[out.exchange_id]
    assert doc["posed"] == Q and doc["response"] == out.response and doc["rating"] is None
    assert doc["sources"][0]["evidence"] == ["late.supplier_id", "late.late_count"]
    assert d.audit.verify_chain(out.exchange_id) == len(t)


def test_stage_progress_is_reported_and_the_answer_only_at_the_end(db_path, storage):
    seen = []
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    pl.run(Q, [], APPROVER, deps(gw, db_path, storage), on_stage=seen.append)
    assert seen == ["intent", "retrieval", "synthesis", "gate", "contract"]


def test_history_form_of_the_answer(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    out = pl.run(Q, [], APPROVER, deps(gw, db_path, storage))
    rows = out.response["readings"][0]["rows"]
    assert out.exchange.answer.startswith("[late] " + rows[0]["supplier_id"])
    # review F5: every identifier in the table, in display order, not only those in the answer text
    assert len(rows) == 5 and out.exchange.presented == [r["supplier_id"] for r in rows]


# --------------------------------------------------------------------------
# the gate and the single regeneration (NFR 19, decisions 3 to 5, 12)
# --------------------------------------------------------------------------

def test_unsupported_assertion_is_removed_and_the_answer_redrafted_from_the_rest(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], synthesis_redraft=[SYN_ONE],
               support_scoring=[gate_says("supported", "uncovered"), gate_says("supported")])
    r = pl.run(Q, [], APPROVER, deps(gw, db_path, storage)).response
    assert r["outcome"] == "answered" and r["withheld"] == contract.WITHHELD
    redraft_prompt = next(pr for pr, cfg in gw.calls if cfg.prompt_identity.startswith("synthesis_redraft@"))
    assert "Kept:\n[late] " in redraft_prompt and "followed with" not in redraft_prompt.split("Kept:")[-1]
    assert "followed" not in r["readings"][0]["answer"]


def test_contradiction_is_removed_like_an_unsupported_assertion(db_path, storage):
    """Decision 3: a contradiction no longer brings the answer down. It is redrafted from the rest."""
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], synthesis_redraft=[SYN_ONE],
               support_scoring=[gate_says("supported", "contradicted"), gate_says("supported")])
    r = pl.run(Q, [], APPROVER, deps(gw, db_path, storage)).response
    assert r["outcome"] == "answered" and r["readings"][0]["answer"]


def test_no_supported_assertion_is_regenerated_once_then_replaced(db_path, storage):
    """Test 17: two answers generated and no more, each examined, neither presented."""
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN, SYN], support_scoring=[gate_says("uncovered")] * 2)
    out = pl.run(Q, [], APPROVER, deps(gw, db_path, storage))
    r = out.response
    assert len(called(gw, "synthesis")) == 2 and len(called(gw, "support_scoring")) == 2
    assert called(gw, "synthesis_redraft") == []
    assert r["outcome"] == "no_supported_assertion" and r["readings"] == []
    assert r["request_for_input"]["readings"][0]["label"] == "late"
    assert out.recorded and out.exchange.answer is None


def test_fresh_draft_is_told_what_was_rejected(db_path, storage):
    """Decision 4: nothing kept, so a fresh draft over all the material, with the rejected statements."""
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN, SYN_ONE],
               support_scoring=[gate_says("uncovered"), gate_says("supported")])
    r = pl.run(Q, [], APPROVER, deps(gw, db_path, storage)).response
    fresh = [pr for pr, cfg in gw.calls if cfg.prompt_identity.startswith("synthesis@")][1]
    assert "Do not state them again" in fresh and "followed with" in fresh.split("Rejected.")[1]
    assert r["outcome"] == "answered" and r["withheld"] == contract.WITHHELD


def test_after_the_last_gate_only_sentences_wholly_supported_are_presented(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], synthesis_redraft=[SYN],
               support_scoring=[gate_says("supported", "uncovered"), gate_says("supported", "uncovered")])
    r = pl.run(Q, [], APPROVER, deps(gw, db_path, storage)).response
    assert len(called(gw, "support_scoring")) == 2
    assert "followed" not in r["readings"][0]["answer"] and r["withheld"]
    assert r["readings"][0]["evidence"] == ["late.supplier_id", "late.late_count"]


def test_gate_that_skips_a_sentence_is_retried(db_path, storage):
    """Decision 12: output leaving a sentence unexamined is rejected and retried."""
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[skips_last, gate_says("supported")])
    out = pl.run(Q, [], APPROVER, deps(gw, db_path, storage))
    assert out.response["outcome"] == "answered" and "withheld" not in out.response
    gates = [e for e in trail(storage, out.exchange_id) if e["agent"] == "support_gate"]
    assert [e["event_type"] for e in gates] == ["model_output_rejected", "model_output_accepted"]
    assert gates[1]["attempt_number"] == 2 and "not examined" in gates[1]["superseded_reason"]


def test_gate_that_skips_in_every_attempt_ends_the_exchange(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[skips_last, skips_last])
    out = pl.run(Q, [], APPROVER, deps(gw, db_path, storage))
    assert out.response["outcome"] == "request_not_completed" and out.recorded
    assert "not examined" in storage.investigations[out.exchange_id]["failure"]


# --------------------------------------------------------------------------
# outcomes without a drafted answer
# --------------------------------------------------------------------------

def test_rejected_plan_is_not_run_and_ends_as_request_not_completed(db_path, storage):
    """Test 13: rejected before any retrieval, within the attempts."""
    bad = {"disposition": "retrieve", "readings": [{"label": "r", "plan": {
        "SELECT": [{"expr": {"col": "commitments.nope"}, "as": "x"}], "FROM": "commitments"}}]}
    gw = model(intent_analysis_demo=[bad, bad])
    out = pl.run(Q, [], APPROVER, deps(gw, db_path, storage))
    assert out.response["outcome"] == "request_not_completed" and len(gw.calls) == 2
    t = trail(storage, out.exchange_id)
    assert [e["agent"] for e in t] == ["intent_analysis", "intent_analysis", "output_contract"]
    assert "unknown_column" in t[1]["superseded_reason"]


def test_question_asking_for_a_cause_is_referred(db_path, storage):
    """Test 22: no retrieval, no cause, an offer to open an investigation."""
    gw = model(intent_analysis_demo=[{"disposition": "refer_to_investigation"}])
    out = pl.run("Why is S-014 late so often?", [], APPROVER, deps(gw, db_path, storage))
    assert out.response["outcome"] == "referred_to_investigation" and out.response["offer_investigation"]
    assert len(gw.calls) == 1 and out.recorded


def test_empty_result_is_an_answer_without_a_model_call(db_path, storage):
    """Test 35: a supplier with no orders. No records matched, not absent."""
    plan = {"label": "s40", "plan": {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}],
                                     "FROM": "commitments",
                                     "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-040"}]}}}
    gw = model(intent_analysis_demo=[{"disposition": "retrieve", "readings": [plan]}])
    r = pl.run("What did S-040 supply?", [], APPROVER, deps(gw, db_path, storage)).response
    assert r["outcome"] == "answered" and r["readings"][0]["notice"] == "No records matched."
    assert len(gw.calls) == 1


def test_unreachable_source_is_not_recorded_but_is_logged_without_the_question(db_path, storage, caplog):
    """Test 23, decision 10."""
    def down():
        raise sqlite3.OperationalError("unable to open database file")
    gw = model(intent_analysis_demo=[INTENT])
    with caplog.at_level(logging.ERROR, logger="medagentpro.breaker"):
        out = pl.run(Q, [], APPROVER, deps(gw, db_path, storage, connect=down))
    assert out.response["outcome"] == "source_unreachable" and not out.recorded
    assert storage.audit_log == [] and storage.investigations == {}
    logged = caplog.text
    assert out.exchange_id in logged and "AP-01" in logged and "unable to open" in logged
    assert Q not in logged


def test_answer_not_recorded_is_not_presented(db_path, storage, caplog):
    """R1, decision 9."""
    audit = AuditRepository(storage, CircuitBreaker("system_store", MemoryStore(), threshold=1), b"k")
    audit.breaker.record_failure()
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    with caplog.at_level(logging.ERROR, logger="medagentpro.breaker"):
        out = pl.run(Q, [], APPROVER, deps(gw, db_path, storage, audit=audit))
    assert out.response["outcome"] == "request_not_completed" and not out.recorded
    assert "readings" in out.response and out.response["readings"] == []
    assert "exchange record not written" in caplog.text


# --------------------------------------------------------------------------
# more than one reading (FR 11, FR 16)
# --------------------------------------------------------------------------

def test_readings_are_retrieved_together(db_path, storage):
    """Test 24: neither retrieval started after the other finished."""
    barrier = threading.Barrier(2, timeout=5)

    def connect():
        barrier.wait()   # both retrievals must have started before either runs
        return sqlite3.connect(db_path)
    syn = {"readings": [{"label": "late", "sentences": [{"text": ONE}]},
                        {"label": "promise", "sentences": [{"text": "{promise.supplier_id#1} had "
                                                                    "{promise.late_count#1}."}]}]}
    gw = model(intent_analysis_demo=[TWO], synthesis=[syn], support_scoring=[gate_says("supported")])
    q = "Which suppliers were late in weeks 1 to 13?"
    out = pl.run(q, [], APPROVER, deps(gw, db_path, storage, connect=connect))
    a, b = next(e for e in trail(storage, out.exchange_id) if e["agent"] == "retrieval")["evidence_reviewed"]
    assert a["started"] <= b["finished"] and b["started"] <= a["finished"]
    assert [v["label"] for v in out.response["readings"]] == ["late", "promise"]
    assert all(v["answer"] for v in out.response["readings"])


# --------------------------------------------------------------------------
# a follow-up question (FR 25, NFR 27, Tests 44 and 45)
# --------------------------------------------------------------------------

def test_follow_up_is_rewritten_and_shown_as_understood(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    first = pl.run(Q, [], APPROVER, deps(gw, db_path, storage))
    follow = "And by promised date?"
    gw2 = model(question_rewriting=[{"question": "Which suppliers were late by promised date in weeks 1 to 13?",
                                     "carried_from_history": True}],
                intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    seen = []
    out = pl.run(follow, [first.exchange], APPROVER, deps(gw2, db_path, storage), on_stage=seen.append)
    assert out.response["understood_as"].startswith("Which suppliers were late by promised date")
    assert seen[0] == "rewriting"
    intent_prompt = next(pr for pr, cfg in gw2.calls if cfg.prompt_identity.startswith("intent_analysis_demo@"))
    assert f"Question as posed: {follow}" in intent_prompt
    assert storage.investigations[out.exchange_id]["understood_as"] == out.response["understood_as"]


def test_identifier_introduced_by_the_rewrite_is_rejected(db_path, storage):
    """Test 45: the rewritten question is not a source of identifiers."""
    s031 = {"disposition": "retrieve", "readings": [{"label": "r", "plan": {
        "SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
        "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-031"}]}}}]}
    earlier = p.Exchange("Which suppliers were late?", None, "[late] S-038 had 6.", ["S-038"])
    gw = model(question_rewriting=[{"question": "What did S-031 supply?", "carried_from_history": True}],
               intent_analysis_demo=[s031, s031])
    out = pl.run("And the other one?", [earlier], APPROVER, deps(gw, db_path, storage))
    assert out.response["outcome"] == "request_not_completed"
    assert "S-031" in storage.investigations[out.exchange_id]["failure"]


# --------------------------------------------------------------------------
# rating (FR 12, Test 16)
# --------------------------------------------------------------------------

def test_rating_is_held_with_the_recorded_exchange(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    d = deps(gw, db_path, storage)
    out = pl.run(Q, [], APPROVER, d)
    before = len(trail(storage, out.exchange_id))
    pl.rate(out.exchange_id, "up", APPROVER, d.audit)
    assert storage.investigations[out.exchange_id]["rating"] == "up"
    t = trail(storage, out.exchange_id)
    assert len(t) == before + 1 and t[-1]["event_type"] == "answer_rated"
    assert d.audit.verify_chain(out.exchange_id) == before + 1


def test_rating_rules(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT, {"disposition": "refer_to_investigation"}], synthesis=[SYN],
               support_scoring=[gate_says("supported")])
    d = deps(gw, db_path, storage)
    answered = pl.run(Q, [], APPROVER, d)
    referred = pl.run("Why?", [], APPROVER, d)
    with pytest.raises(ValueError):
        pl.rate(answered.exchange_id, "5 stars", APPROVER, d.audit)
    with pytest.raises(PermissionError):
        pl.rate(answered.exchange_id, "up", {"role": "APPROVER", "user_id": "AP-02"}, d.audit)
    with pytest.raises(ValueError, match="only an answer"):
        pl.rate(referred.exchange_id, "up", APPROVER, d.audit)
    with pytest.raises(LookupError):
        pl.rate("EX-none", "up", APPROVER, d.audit)


# --------------------------------------------------------------------------
# review of 12.10: F1, F3, F5, F7, F9
# --------------------------------------------------------------------------

def test_malformed_query_is_a_system_fault_not_an_unreachable_source(db_path, storage, caplog):
    """F1: our fault. request_not_completed, recorded, logged, and not counted against the source."""
    d = deps(model(intent_analysis_demo=[INTENT]), db_path, storage, connect=lambda: sqlite3.connect(":memory:"))
    with caplog.at_level(logging.ERROR, logger="medagentpro.breaker"):
        out = pl.run(Q, [], APPROVER, d)
    assert out.response["outcome"] == "request_not_completed" and out.recorded
    assert "no such table" in storage.investigations[out.exchange_id]["failure"]
    assert "retrieval fault" in caplog.text and Q not in caplog.text
    assert d.source_breaker.failure_count == 0


def test_missing_store_is_unreachable(db_path, storage, tmp_path):
    """F1: read-only connection, so a missing file fails to open instead of creating an empty store."""
    d = deps(model(intent_analysis_demo=[INTENT]), db_path, storage,
             connect=pl.connect_readonly(tmp_path / "gone.db"))
    out = pl.run(Q, [], APPROVER, d)
    assert out.response["outcome"] == "source_unreachable" and not out.recorded
    assert not (tmp_path / "gone.db").exists() and d.source_breaker.failure_count == 1


def test_locked_store_is_unreachable(db_path, storage, tmp_path):
    copy = tmp_path / "locked.db"
    copy.write_bytes(db_path.read_bytes())
    holder = sqlite3.connect(copy, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        out = pl.run(Q, [], APPROVER, deps(model(intent_analysis_demo=[INTENT]), db_path, storage,
                                           connect=pl.connect_readonly(copy, timeout=0)))
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert out.response["outcome"] == "source_unreachable"


def test_open_breaker_on_the_source_is_unreachable_without_trying(db_path, storage):
    """F9: the company source goes through its own breaker (NFR 9)."""
    tried = []

    def connect():
        tried.append(1)
        return sqlite3.connect(db_path)
    d = deps(model(intent_analysis_demo=[INTENT]), db_path, storage, connect=connect)
    d.source_breaker = CircuitBreaker("company_store", MemoryStore(), threshold=1)
    d.source_breaker.record_failure()
    out = pl.run(Q, [], APPROVER, d)
    assert out.response["outcome"] == "source_unreachable" and tried == [] and not out.recorded


def test_read_only_connection_answers(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    out = pl.run(Q, [], APPROVER, deps(gw, db_path, storage, connect=pl.connect_readonly(db_path)))
    assert out.response["outcome"] == "answered"


def test_follow_up_can_name_an_identifier_shown_only_in_the_table(db_path, storage):
    """F5: the third supplier in the table was never in the answer text, and was presented."""
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_says("supported")])
    first = pl.run(Q, [], APPROVER, deps(gw, db_path, storage))
    third = first.response["readings"][0]["rows"][2]["supplier_id"]
    assert third not in first.response["readings"][0]["answer"]
    plan = {"disposition": "retrieve", "readings": [{"label": "r", "plan": {
        "SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}], "FROM": "commitments",
        "WHERE": {"EQ": [{"col": "commitments.supplier_id"}, {"value": third}]}, "LIMIT": 3}}]}
    syn = {"readings": [{"label": "r", "sentences": [{"text": "One order is {r.po_id#1}."}]}]}
    gw2 = model(question_rewriting=[{"question": f"Which orders did {third} have?", "carried_from_history": True}],
                intent_analysis_demo=[plan], synthesis=[syn], support_scoring=[gate_says("supported", alias="po_id")])
    out = pl.run("And the third one in the table?", [first.exchange], APPROVER, deps(gw2, db_path, storage))
    assert out.response["outcome"] == "answered"


def test_follow_up_answer_may_state_the_week_its_plan_ran_on(db_path, storage):
    """F7 and F3: "in week 5" comes from the plan, and synthesis sees both forms of the question."""
    earlier = p.Exchange(Q, None, "[late] S-038 had 6.", ["S-038"])
    week5 = {"disposition": "retrieve", "readings": [{"label": "late", "plan": dict(LATE["plan"], WHERE={"AND": [
        {"EQ": [{"col": "commitments.week"}, {"value": 5}]},
        {"EQ": [{"metric": "late_by_delivery"}, {"value": True}]}]})}]}
    syn = {"readings": [{"label": "late", "sentences": [
        {"text": "In week 5, {late.supplier_id#1} had {late.late_count#1} late lines."}]}]}
    gw = model(question_rewriting=[{"question": "Which suppliers delivered late in week 5?",
                                    "carried_from_history": True}],
               intent_analysis_demo=[week5], synthesis=[syn], support_scoring=[gate_says("supported")])
    out = pl.run("And in week five?", [earlier], APPROVER, deps(gw, db_path, storage))
    assert out.response["outcome"] == "answered"
    assert out.response["readings"][0]["answer"].startswith("In week 5, ")
    prompt = next(pr for pr, cfg in gw.calls if cfg.prompt_identity.startswith("synthesis@"))
    assert "Question as posed: And in week five?\nQuestion: Which suppliers delivered late in week 5?" in prompt


# --------------------------------------------------------------------------
# second review of 12.10: F11
# --------------------------------------------------------------------------

def test_draft_typing_an_identifier_from_the_rows_is_rejected_and_retried(db_path, storage):
    """F11: "S-xxx had {late.late_count#1}" could pair one supplier with another's value."""
    def typed(prompt):
        first = re.search(r"\[late\] row 1: late\.supplier_id = (\S+),", prompt).group(1)
        return {"readings": [{"label": "late", "sentences": [{"text": f"{first} had {{late.late_count#1}}."}]}]}
    gw = model(intent_analysis_demo=[INTENT], synthesis=[typed, SYN], support_scoring=[gate_says("supported")])
    out = pl.run(Q, [], APPROVER, deps(gw, db_path, storage))
    assert out.response["outcome"] == "answered"
    syn = [e for e in trail(storage, out.exchange_id) if e["agent"] == "synthesis"]
    assert [e["event_type"] for e in syn] == ["model_output_rejected", "model_output_accepted"]
    assert "(F11)" in syn[1]["superseded_reason"]
