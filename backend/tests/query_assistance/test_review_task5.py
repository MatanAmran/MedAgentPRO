"""
Tests for the task 5 review, R1-R7 and P1-P10 (CLAUDE.md, "Task 5 review"). Run:  pytest -q

No test calls a real model (6.1) or reads backend/.env. The values below are made up.
"""

import json
import re
from pathlib import Path

import pytest

from demo_data import seed
from model_client import budget as b
from model_client.client import (
    LiteLLMGateway,
    ModelCallError,
    ModelConfig,
    ModelResponse,
    ScriptedModelGateway,
    classify,
)
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import calls as mc
from query_assistance import compiler as c
from query_assistance import contract
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from query_assistance import synthesis as s
from shared.circuit_breaker import CircuitBreaker, MemoryStore

HERE = Path(__file__).parent
BUNDLE = json.loads((c.HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))["schemas"]
CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}
Q = "Which suppliers delivered late in weeks 1 to 13?"
ENV = {"ANTHROPIC_API_KEY": "test-key-not-real", "MODEL_ID": "test-model", "MODEL_PRICE_IN": "1",
       "MODEL_PRICE_OUT": "10", "MODEL_BUDGET_USD": "1", "MODEL_MAX_USD_PER_EXCHANGE": "0.5",
       "MODEL_MAX_OUTPUT_TOKENS": "1000", "MODEL_MAX_EXCHANGES_PER_MINUTE": "30"}

LATE = {"label": "late", "plan": {
    "SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
               {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}],
    "FROM": "commitments",
    "WHERE": {"AND": [{"BETWEEN": {"col": "commitments.week", "from": {"value": 1}, "to": {"value": 13}}},
                      {"EQ": [{"metric": "late_by_delivery"}, {"value": True}]}]},
    "GROUP_BY": ["supplier_id"], "ORDER_BY": [{"by": "late_count", "dir": "DESC"}], "LIMIT": 5}}
INTENT = {"disposition": "retrieve", "readings": [LATE]}
ONE = "{late.supplier_id#1} had the most late lines, {late.late_count#1}."
SYN = {"readings": [{"label": "late", "sentences": [{"text": ONE}]}]}


def gate_ok(prompt):
    body = prompt.split("Answer:\n")[-1].split("\nRecords:")[0]
    return {"claims": [{"sentence": int(n), "text": t, "verdict": "supported", "evidence": [f"{lab}.supplier_id"]}
                       for n, lab, t in re.findall(r"^(\d+)\. \[(\w+)\] (.*)$", body, re.M)]}


def model(**by_prompt):
    queues = {k: list(v) for k, v in by_prompt.items()}

    def answer(prompt, cfg):
        out = queues[cfg.prompt_identity.split("@")[0]].pop(0)
        return out(prompt) if callable(out) else out
    return ScriptedModelGateway(answer)


def prompts_of(gw, name):
    return [pr for pr, cfg in gw.calls if cfg.prompt_identity.startswith(name + "@")]


@pytest.fixture
def storage():
    return MemoryStorage()


def deps(gw, db_path, storage, model_id="demo"):
    return pl.Deps(gw, model_id, CAT, pl.connect_readonly(db_path),
                   AuditRepository(storage, CircuitBreaker("system_store", MemoryStore()), b"k"),
                   p.load(seed.HERE / "intent_analysis.md"))


def trail(storage, eid):
    return sorted((r for r in storage.audit_log if r["investigation_id"] == eid), key=lambda r: r["seq"])


def run(gw, db_path, storage, question=Q, history=(), model_id="demo"):
    return pl.run(question, list(history), APPROVER, deps(gw, db_path, storage, model_id))


class _Status(Exception):
    def __init__(self, status, message="x"):
        super().__init__(message)
        self.status_code = status


class Timeout(Exception):   # LiteLLM's name
    pass


class APIConnectionError(Exception):   # LiteLLM's name
    pass


class UnsupportedParamsError(Exception):   # LiteLLM's name
    pass


# --------------------------------------------------------------------------
# R1: temperature only where configured; the audit records what was sent
# --------------------------------------------------------------------------

def _litellm(seen, finish="stop", content="{}"):
    class R:
        model, usage = "anthropic/test-model", {"prompt_tokens": 3, "completion_tokens": 2}
        choices = [type("C", (), {"finish_reason": finish,
                                  "message": type("M", (), {"content": content})()})()]

    def completion(**kw):
        seen.update(kw)
        return R()
    return completion


def test_temperature_is_not_sent_unless_configured():
    seen = {}
    r = LiteLLMGateway(CircuitBreaker("m", MemoryStore()), completion=_litellm(seen)).invoke("x", ModelConfig("m"))
    assert "temperature" not in seen and r.temperature is None


def test_configured_temperature_is_sent_and_recorded():
    seen = {}
    r = LiteLLMGateway(CircuitBreaker("m", MemoryStore()), completion=_litellm(seen), temperature=0.3) \
        .invoke("x", ModelConfig("m"))
    assert seen["temperature"] == 0.3 and r.temperature == 0.3 and seen["timeout"] == 120.0


def test_audit_records_the_temperature_that_was_sent(db_path, storage):
    def sent(prompt, cfg):
        return ModelResponse(json.dumps(INTENT), "m", 10, 10, cfg.prompt_identity, finish_reason="stop",
                             temperature=0.2)
    gw = ScriptedModelGateway(lambda pr, cfg: sent(pr, cfg) if "intent" in cfg.prompt_identity
                              else (SYN if "synthesis" in cfg.prompt_identity else gate_ok(pr)))
    out = run(gw, db_path, storage)
    entries = [e for e in trail(storage, out.exchange_id) if e["event_type"].startswith("model_")]
    assert "temperature 0.2" in entries[0]["model_identity"]
    assert "temperature provider default" in entries[1]["model_identity"]


@pytest.mark.parametrize("value, ok", [("0", True), ("0.7", True), ("1.5", False), ("hot", False)])
def test_model_temperature_in_env(value, ok):
    if ok:
        assert b.BudgetConfig.from_env({**ENV, "MODEL_TEMPERATURE": value}).temperature == float(value)
    else:
        with pytest.raises(b.BudgetConfigError, match="MODEL_TEMPERATURE"):
            b.BudgetConfig.from_env({**ENV, "MODEL_TEMPERATURE": value})


# --------------------------------------------------------------------------
# P1: the real gateway refuses to start on a model or price it cannot vouch for
# --------------------------------------------------------------------------

INFO = {"input_cost_per_token": 2e-06, "output_cost_per_token": 1e-05, "supports_sampling_params": False}


def cfg(**over):
    return b.BudgetConfig.from_env({**ENV, "MODEL_PRICE_IN": "2", "MODEL_PRICE_OUT": "10", **over})


def test_model_not_in_the_map_refuses_to_start():
    def missing(name):
        raise Exception("not mapped")
    with pytest.raises(b.BudgetConfigError, match="not in LiteLLM's model map"):
        b.check_against_provider_map(cfg(), missing)


def test_temperature_for_a_model_that_takes_none_refuses_to_start():
    with pytest.raises(b.BudgetConfigError, match="takes no temperature"):
        b.check_against_provider_map(cfg(MODEL_TEMPERATURE="0"), lambda n: INFO)


@pytest.mark.parametrize("var, value", [("MODEL_PRICE_IN", "0.2"), ("MODEL_PRICE_OUT", "1")])
def test_a_price_below_the_map_refuses_to_start(var, value):
    with pytest.raises(b.BudgetConfigError, match=var):
        b.check_against_provider_map(cfg(**{var: value}), lambda n: INFO)


@pytest.mark.parametrize("var", ["MODEL_MAX_OUTPUT_TOKENS", "MODEL_MAX_OUTPUT_TOKENS_INTENT"])
def test_an_output_cap_above_the_models_own_limit_refuses_to_start(var):
    """Task 10a (chat 01:15): the intent cap is checked as the general one is."""
    info = {**INFO, "max_output_tokens": 2500}
    b.check_against_provider_map(cfg(MODEL_MAX_OUTPUT_TOKENS_INTENT="2500"), lambda n: info)
    with pytest.raises(b.BudgetConfigError, match=var + " in"):
        b.check_against_provider_map(cfg(**{var: "3000"}), lambda n: info)


def test_a_price_above_the_map_is_allowed():
    b.check_against_provider_map(cfg(MODEL_PRICE_IN="3", MODEL_PRICE_OUT="15"), lambda n: INFO)


def test_litellm_uses_the_local_map_of_the_pinned_version():
    """H1: no map downloaded on import, so prices and P1 do not change with the network."""
    import os

    import litellm
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert "claude-sonnet-5-5" in litellm.model_cost and "claude-haiku-5-5" not in litellm.model_cost


def test_a_model_with_no_price_in_the_map_refuses_to_start():
    with pytest.raises(b.BudgetConfigError, match="holds no price"):
        b.check_against_provider_map(cfg(), lambda n: {"output_cost_per_token": 1e-05})


def test_against_litellms_own_map():
    """In the local map of LiteLLM 1.105: Sonnet 5.5 at 2 / 10 passes, Haiku 5.5 is not held (R1, H1)."""
    b.check_against_provider_map(cfg(MODEL_ID="claude-sonnet-5-5"))
    for missing in ("claude-haiku-5-5", "claude-not-a-model-9-9"):
        with pytest.raises(b.BudgetConfigError, match="not in LiteLLM's model map"):
            b.check_against_provider_map(cfg(MODEL_ID=missing))
    with pytest.raises(b.BudgetConfigError, match="takes no temperature"):   # read from the raw map entry
        b.check_against_provider_map(cfg(MODEL_ID="claude-sonnet-5-5", MODEL_TEMPERATURE="0"))
    with pytest.raises(b.BudgetConfigError, match="MODEL_PRICE_IN"):
        b.check_against_provider_map(cfg(MODEL_ID="claude-opus-5-5"))   # 2 in .env, 4 in the map


# --------------------------------------------------------------------------
# R2: structured output for rewriting, synthesis and the gate, never for intent analysis
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["question_rewriting", "synthesis", "support_scoring"])
def test_reduced_schema_matches_its_snapshot(name):
    """A LiteLLM upgrade that changes what is sent shows here (litellm pinned at 1.105.0)."""
    snap = json.loads((HERE / "snapshots" / f"{name}.provider.json").read_text(encoding="utf-8"))
    assert mc.provider_schema(BUNDLE[name]["schema"]) == snap


def test_intent_schema_unpacked_exceeds_what_the_provider_accepts():
    """Why intent analysis sends no response_format: Anthropic accepts 24 optional properties and 16
    anyOf per request."""
    def count(node, acc):
        if isinstance(node, dict):
            acc["anyOf"] += "anyOf" in node
            if isinstance(node.get("properties"), dict):
                acc["optional"] += len(set(node["properties"]) - set(node.get("required", [])))
            for v in node.values():
                count(v, acc)
        elif isinstance(node, list):
            for v in node:
                count(v, acc)
        return acc
    n = count(mc.provider_schema(c.intent_validator().schema), {"anyOf": 0, "optional": 0})
    assert n["optional"] > 24 and n["anyOf"] > 16


def test_stages_send_their_schema_except_intent(db_path, storage):
    gw = model(question_rewriting=[{"question": Q, "carried_from_history": False}],
               intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_ok])
    run(gw, db_path, storage, history=[p.Exchange("Earlier?", None, None, [])])
    sent = {cfg.prompt_identity.split("@")[0]: cfg.output_schema for _, cfg in gw.calls}
    assert sent["intent_analysis_demo"] is None
    for name, key in (("question_rewriting", "question_rewriting"), ("synthesis", "synthesis"),
                      ("support_scoring", "support_scoring")):
        assert sent[name] == BUNDLE[key]["schema"]


def test_breach_of_the_schema_sent_is_a_content_failure_with_feedback(db_path, storage):
    """A gate output without claims breaks `required` in the schema sent: the provider did not hold to it,
    so it is not assumed to (chat, 18.10, 05:10, 1). One feedback attempt, path and keyword only."""
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[{}, gate_ok])
    out = run(gw, db_path, storage)
    assert out.response["outcome"] == "answered"
    first, second = prompts_of(gw, "support_scoring")
    assert "breaks the schema rule 'required'" in second and "breaks the schema rule" not in first


def test_breach_of_the_schema_sent_twice_ends_the_exchange(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[{"verdicts": []}, {"verdicts": []}])
    out = run(gw, db_path, storage)
    assert out.response["outcome"] == "request_not_completed" and len(prompts_of(gw, "support_scoring")) == 2


def test_additional_property_in_the_schema_sent_gets_feedback(db_path, storage):
    def extra(prompt):
        o = gate_ok(prompt)
        o["secret_key"] = "S-SECRET-4711"
        return o
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[extra, gate_ok])
    out = run(gw, db_path, storage)
    assert out.response["outcome"] == "answered"
    second = prompts_of(gw, "support_scoring")[1]
    assert "'additionalProperties'" in second and "S-SECRET-4711" not in second and "secret_key" not in second


def test_breach_of_the_full_schema_only_is_a_content_failure_with_feedback(db_path, storage):
    """maxLength was dropped before sending, so the model can fix it from feedback."""
    def long_claim(prompt):
        out = gate_ok(prompt)
        out["claims"][0]["text"] = out["claims"][0]["text"] + " " + "x" * 300
        return out
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[long_claim, gate_ok])
    out = run(gw, db_path, storage)
    second = prompts_of(gw, "support_scoring")[1]
    assert "Your previous output was rejected: the value at claims/0/text breaks the schema rule 'maxLength'" \
           in second
    assert out.response["outcome"] == "answered"


# --------------------------------------------------------------------------
# R3: one enclosing fence is removed, nothing else
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text, ok", [
    ('```json\n{"a": 1}\n```', True),
    ('```\n{"a": 1}\n```\n', True),
    ('{"a": 1}', True),
    ('Here it is:\n```json\n{"a": 1}\n```', False),
    ('```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```', False),
])
def test_fence_strip(text, ok):
    r = ModelResponse(text, "m", 1, 1)
    if ok:
        assert r.json() == {"a": 1}
    else:
        with pytest.raises(ValueError):
            r.json()


# --------------------------------------------------------------------------
# R4: every failure classified; P9, P10
# --------------------------------------------------------------------------

@pytest.mark.parametrize("finish, content, kind", [
    ("content_filter", "{}", "refusal"), ("length", '{"a"', "truncated"), ("stop", "", "empty"),
    ("stop", None, "empty")])
def test_a_response_with_a_problem_is_not_retried(db_path, storage, finish, content, kind):
    resp = ModelResponse(content, "m", 100, 50, "synthesis@6", finish_reason=finish)
    gw = model(intent_analysis_demo=[INTENT], synthesis=[resp, SYN], support_scoring=[gate_ok])
    out = run(gw, db_path, storage)
    assert out.response["outcome"] == "request_not_completed" and len(prompts_of(gw, "synthesis")) == 1
    entry = [e for e in trail(storage, out.exchange_id) if e["agent"] == "synthesis"][0]
    assert entry["rejection_reason"] == kind and entry["input_tokens"] == 100   # billed and recorded (P10)


@pytest.mark.parametrize("error, kind, outage", [
    (_Status(400, "invalid_request_error: bad schema"), "client_error", False),
    (_Status(404), "client_error", False),
    (_Status(429, "rate_limit_error"), "rate_limited", True),
    (_Status(500), "server_error", True),
    (_Status(529, "overloaded_error"), "server_error", True),
    (Timeout("read timed out"), "timeout", True),
    (APIConnectionError("reset"), "connection", True),
    (UnsupportedParamsError("temperature"), "client_error", False),
    (KeyError("odd"), "unexpected", False),
])
def test_classification(error, kind, outage):
    got = classify(error)
    assert got.kind == kind and got.outage == outage


@pytest.mark.parametrize("error, counted", [(_Status(400), 0), (_Status(429, "rate_limit_error"), 1),
                                            (_Status(529), 1), (UnsupportedParamsError("t"), 0)])
def test_only_outages_count_against_the_model_breaker(error, counted):
    breaker = CircuitBreaker("model_provider", MemoryStore(), threshold=5)
    calls = []

    def completion(**kw):
        calls.append(1)
        raise error
    with pytest.raises(ModelCallError):
        LiteLLMGateway(breaker, completion=completion).invoke("x", ModelConfig("m"))
    assert breaker.failure_count == counted and len(calls) == 1


def test_a_failed_call_ends_the_exchange_without_retry_and_is_recorded(db_path, storage):
    """P9: 429, 5xx, 529 are not retried; the person sends again. P6: the failed call is an entry."""
    gw = model(intent_analysis_demo=[ModelCallError("server_error", "529 overloaded", outage=True), INTENT])
    out = run(gw, db_path, storage)
    assert out.response["outcome"] == "request_not_completed" and len(gw.calls) == 1
    entry = trail(storage, out.exchange_id)[0]
    assert entry["event_type"] == "model_call_failed" and "server_error" in entry["rejection_reason"]
    assert "input_tokens" not in entry


def test_content_failure_gets_one_feedback_attempt_recorded(db_path, storage):
    bad = {"disposition": "retrieve", "readings": [{"label": "r", "plan": {
        "SELECT": [{"expr": {"col": "commitments.nope"}, "as": "x"}], "FROM": "commitments"}}]}
    gw = model(intent_analysis_demo=[bad, INTENT], synthesis=[SYN], support_scoring=[gate_ok])
    out = run(gw, db_path, storage)
    assert out.response["outcome"] == "answered"
    first, second = prompts_of(gw, "intent_analysis_demo")
    assert "rejected" not in first.split("Question:")[-1]
    assert "Your previous output was rejected:" in second and "commitments.nope" in second
    entries = [e for e in trail(storage, out.exchange_id) if e["agent"] == "intent_analysis"]
    assert [e["feedback_attempt"] for e in entries] == [False, True]
    assert entries[0]["event_type"] == "model_output_rejected" and "unknown_column" in entries[0]["rejection_reason"]
    assert entries[1]["event_type"] == "model_output_accepted" and "rejection_reason" not in entries[1]


def test_intent_schema_breach_is_a_content_failure(db_path, storage):
    """No structured output in intent analysis, so every schema_violation can be fixed from feedback."""
    gw = model(intent_analysis_demo=[{"disposition": "retrieve"}, INTENT], synthesis=[SYN],
               support_scoring=[gate_ok])
    out = run(gw, db_path, storage)
    assert out.response["outcome"] == "answered"
    assert "breaks the schema rule" in prompts_of(gw, "intent_analysis_demo")[1]


def test_feedback_never_carries_a_record_value(db_path, storage):
    """P4, NFR 21. A typed identifier from the rows, and a gate claim copied from the filled answer,
    are rejected without being quoted."""
    def typed(prompt):
        first = re.search(r"\[late\] row 1: late\.supplier_id = (\S+),", prompt).group(1)
        return {"readings": [{"label": "late", "sentences": [{"text": f"{first} had {{late.late_count#1}}."}]}]}

    def paraphrased(prompt):
        out = gate_ok(prompt)
        out["claims"][0]["text"] = "A paraphrase of " + out["claims"][0]["text"]
        return out
    gw = model(intent_analysis_demo=[INTENT], synthesis=[typed, SYN], support_scoring=[paraphrased, gate_ok])
    out = run(gw, db_path, storage)
    assert out.response["outcome"] == "answered"
    ids = {r["supplier_id"] for r in out.response["readings"][0]["rows"]}
    for name in ("synthesis", "support_scoring"):
        feedback = prompts_of(gw, name)[1].split("Your previous output was rejected:")[1]
        assert not any(i in feedback for i in ids), name
    assert "sentence 1 types an identifier" in prompts_of(gw, "synthesis")[1]
    assert "claim 1 is not copied word for word from sentence 1" in prompts_of(gw, "support_scoring")[1]


# --------------------------------------------------------------------------
# R5: the ledger
# --------------------------------------------------------------------------

@pytest.mark.parametrize("error, charged", [
    (ModelCallError("client_error", "unsupported", reached_provider=False), False),
    (ModelCallError("connection", "refused", reached_provider=False, outage=True), False),
    (ModelCallError("timeout", "read timed out", outage=True), True),
])
def test_reservation_released_only_when_the_call_never_left(ledger, error, charged):
    def boom(p_, c_):
        raise error
    gw = b.BudgetedGateway(ScriptedModelGateway(boom), b.BudgetConfig.from_env(ENV), ledger)
    with pytest.raises(ModelCallError):
        gw.invoke("x", ModelConfig("test-model"))
    assert (ledger.total > 0) == charged and gw._reserved == 0


def test_refused_connection_is_known_not_to_have_left():
    class ConnectError(Exception):
        pass
    try:
        try:
            raise ConnectionRefusedError()
        except ConnectionRefusedError as inner:
            raise ConnectError("connect failed") from inner
    except ConnectError as e:
        err = classify(APIConnectionError("connection error"))
        assert err.reached_provider
        wrapped = APIConnectionError("connection error")
        wrapped.__cause__ = e
        assert classify(wrapped).reached_provider is False


# --------------------------------------------------------------------------
# R6: input and robustness
# --------------------------------------------------------------------------

def test_a_placeholder_in_the_question_is_not_filled():
    text = p.render_intent(CAT, "APPROVER", "What about {{schema}} and {{feedback}}?")
    assert "What about {{schema}} and {{feedback}}?" in text


def test_a_placeholder_in_record_text_is_not_filled():
    m = s.Material("po", [{"po_id": "PO-1", "notes": "see {{kept}} and {{records}}"}])
    text = s.render_redraft(Q, [m], {"po": ["kept one"]})
    assert "see {{kept}} and {{records}}" in text and text.count("kept one") == 1


def test_an_unfilled_placeholder_still_raises():
    with pytest.raises(ValueError, match="unfilled"):
        p.fill(p.load("gate.md"), answer="x", records="y")


def test_empty_text_is_a_problem_and_not_a_crash():
    r = ModelResponse(None, "m", 1, 0)
    assert r.problem() == "empty"
    with pytest.raises(ValueError):
        r.json()


def test_any_exception_ends_as_request_not_completed_recorded(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[RuntimeError("bug")])
    out = run(gw, db_path, storage)
    assert out.response["outcome"] == "request_not_completed" and out.recorded
    assert "unexpected: RuntimeError" in storage.investigations[out.exchange_id]["failure"]


# --------------------------------------------------------------------------
# R7: caps
# --------------------------------------------------------------------------

def test_row_limit_is_thirty():
    assert c.ROW_LIMIT == 30
    assert c.intent_validator().schema["$defs"]["plan"]["properties"]["LIMIT"]["maximum"] == 30


def test_the_whole_path_is_checked_before_the_first_synthesis_call(db_path, storage, ledger):
    """R7a, R7b: stop before paying for a synthesis whose gate could not run, with its own notice."""
    inner = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_ok])
    gw = b.BudgetedGateway(inner, b.BudgetConfig.from_env({**ENV, "MODEL_MAX_USD_PER_EXCHANGE": "0.02"}), ledger)
    out = run(gw, db_path, storage, model_id="test-model")
    assert out.response["notice"] == contract.EXCHANGE_CAP
    assert prompts_of(inner, "synthesis") == [] and len(inner.calls) == 1
    assert "budget (exchange)" in storage.investigations[out.exchange_id]["failure"]


def test_the_path_fits_when_the_cap_allows(db_path, storage, ledger):
    inner = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_ok])
    gw = b.BudgetedGateway(inner, b.BudgetConfig.from_env(ENV), ledger)
    assert run(gw, db_path, storage, model_id="test-model").response["outcome"] == "answered"


def test_presented_identifiers_are_cut_to_the_history_budget():
    """R7c: first ones in display order, and the identifier check still sees them all."""
    ids = [f"S-{i:03d}" for i in range(400)]
    last = p.Exchange("Which suppliers?", None, "[late] S-000 had 6.", ids)
    h = p.build_history([last], budget=300)
    line = h.text.splitlines()[-1]
    assert line.startswith("Presented identifiers (the first ") and "of 400, in display order)" in line
    assert "S-000, S-001" in line and "S-399" not in line and p._tokens(h.text) <= 300
    assert p.compiler_conversation([last], "And the last one?", None).presented == set(ids)


def test_feedback_rate_is_read_from_the_audit_entries(db_path, storage):
    """R4: the figure dry_run and the smoke test report."""
    from query_assistance import dry_run
    bad = {"disposition": "retrieve"}
    gw = model(intent_analysis_demo=[bad, INTENT], synthesis=[SYN], support_scoring=[gate_ok])
    run(gw, db_path, storage)
    assert dry_run.feedback_rate(storage.audit_log) == (1, 1)


# --------------------------------------------------------------------------
# chat, 18.10, 09:40: each reading's records start with what it examined, for synthesis and the gate
# --------------------------------------------------------------------------

def test_synthesis_and_gate_get_each_readings_examined_line_and_no_record_value(db_path, storage):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[gate_ok])
    out = run(gw, db_path, storage)
    assert out.response["outcome"] == "answered"
    line = "[late] examined: " + contract.examined_line(LATE["plan"], CAT, "APPROVER")
    assert out.response["readings"][0]["checked"] == "Examined: " + line.split("examined: ", 1)[1]
    rows = out.response["readings"][0]["rows"]
    for name in ("synthesis", "support_scoring"):
        prompt, = prompts_of(gw, name)
        said, = [x for x in prompt.splitlines() if x.startswith("[late] examined: ")]
        assert said == line
        # what the reading examined comes from the plan and the catalog: no row value is in it
        assert not {str(v) for r in rows for v in r.values()} & set(re.findall(r"[\w-]+", said)) - {"1", "13"}


def test_the_examined_line_names_the_role_scope_for_a_planner():
    line = contract.examined_line(LATE["plan"], CAT, "PLANNER")
    assert line.endswith("limited to the records you hold") and not line.startswith("Examined")
