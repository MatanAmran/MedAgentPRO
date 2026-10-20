"""
Tests for call_trace.py and the per-call line on the operational channel (R9; chat decisions, 14.10,
15:30). Run:  pytest -q

The line carries metadata only: never the question, the model's output, a record value or the key.
The real-model path runs through BudgetedGateway and LiteLLMGateway with a fake completion and a fake
key, as in the security tests (6.1: no real model).
"""

import json
import logging
import sqlite3

import pytest

import main
from model_client import budget as b
from model_client.client import LiteLLMGateway, ModelCallError
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import call_trace
from query_assistance import pipeline as pl
from query_assistance.calls import Call
from shared.circuit_breaker import CircuitBreaker, MemoryStore
from shared.circuit_breaker import log as operational_log
from tests.query_assistance.test_pipeline import (
    APPROVER,
    CAT,
    INTENT,
    INTENT_PROMPT,
    ONE,
    SYN,
    Q,
    gate_says,
    model,
    skips_last,
)

FAKE_KEY = "sk-ant-test-0123456789abcdefSECRET"
CONTEXT = {"request_id": "req-TRACE-0001", "conversation_id": "C-trace-01"}
ENV = {"ANTHROPIC_API_KEY": FAKE_KEY, "MODEL_ID": "test-model", "MODEL_PRICE_IN": "1", "MODEL_PRICE_OUT": "10",
       "MODEL_BUDGET_USD": "1", "MODEL_MAX_USD_PER_EXCHANGE": "0.5", "MODEL_MAX_OUTPUT_TOKENS": "1000",
       "MODEL_MAX_EXCHANGES_PER_MINUTE": "3"}


def lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("model call: ")]


def fields(line: str) -> dict:
    """key=value pairs; the temperature's value is the one with a space."""
    body = line.removeprefix("model call: ").replace("temperature=provider default", "temperature=provider_default")
    return dict(pair.split("=", 1) for pair in body.split(" "))


def run(gw, db_path, caplog, storage=None, model_name="demo", question=Q):
    storage = storage if storage is not None else MemoryStorage()
    audit = AuditRepository(storage, CircuitBreaker("system_store", MemoryStore()), b"test-key")
    d = pl.Deps(gw, model_name, CAT, lambda: sqlite3.connect(db_path), audit, INTENT_PROMPT, coverage=False)
    with caplog.at_level(logging.INFO, logger="medagentpro.breaker"):
        return pl.run(question, [], APPROVER, d, context=CONTEXT)


# --------------------------------------------------------------------------
# the trace itself
# --------------------------------------------------------------------------

def test_a_failed_call_is_traced_by_its_kind_alone():
    e = ModelCallError("timeout", "the provider said: something long and specific")
    t = call_trace.traces("support_gate", [Call("gate@3", None, True, str(e), e.kind, 120000.0)], "m")[0]
    assert (t.status, t.check, t.model, t.attempt_number, t.feedback_attempt) == ("failed", "timeout", "m", 1, True)
    assert "something long" not in t.log_line() and "temperature=provider default" in t.log_line()


def test_the_line_writes_context_first_and_a_dash_for_a_missing_value():
    t = call_trace.traces("rewriting", [Call("question_rewriting@2", None, False, "x", "connection")], "m")[0]
    line = t.log_line(exchange="EX-1", request=None)
    assert line.startswith("model call: exchange=EX-1 request=- stage=rewriting ")
    assert fields(line)["input_tokens"] == "-" and fields(line)["duration_ms"] == "-"


# --------------------------------------------------------------------------
# through the pipeline, scripted
# --------------------------------------------------------------------------

def test_one_line_per_call_with_its_attempt_feedback_and_check(db_path, caplog):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[skips_last, gate_says("supported")])
    out = run(gw, db_path, caplog)
    got = [fields(x) for x in lines(caplog)]
    assert [(f["stage"], f["attempt_number"], f["feedback_attempt"], f["status"]) for f in got] == [
        ("intent_analysis", "1", "False", "accepted"), ("synthesis", "1", "False", "accepted"),
        ("support_gate", "1", "False", "rejected"), ("support_gate", "2", "True", "accepted")]
    assert got[2]["check"] == "GateOutputError" and got[3]["check"] == "-"
    # task 10a: the output's length, and for the gate its claim count (the first skipped one of two)
    assert [f["claims"] for f in got] == ["-", "-", "1", "2"]
    assert all(int(f["output_chars"]) > 0 for f in got)
    for f in got:
        assert (f["exchange"], f["request"], f["conversation"]) == (out.exchange_id, *CONTEXT.values())
        assert f["finish_reason"] == "stop" and float(f["duration_ms"]) >= 0 and int(f["input_tokens"]) > 0


def test_the_line_holds_no_question_model_text_or_record_value(db_path, caplog):
    gw = model(intent_analysis_demo=[INTENT], synthesis=[SYN], support_scoring=[skips_last, gate_says("supported")])
    storage = MemoryStorage()
    out = run(gw, db_path, caplog, storage)
    logged = "\n".join(lines(caplog))
    assert out.response["outcome"] == "answered" and logged
    values = {str(v) for r in out.response["readings"] for row in r["rows"] for v in row.values()
              if isinstance(v, str)}
    assert values, "the rows hold string values to look for"
    for text in (Q, "had the most late lines", ONE, *values):
        assert text not in logged, f"{text!r} reached the log line"
    # the first gate output was rejected with a message; the line carries the check's class only
    rejection = next(r["rejection_reason"] for r in storage.audit_log if r.get("rejection_reason"))
    assert rejection not in logged and "check=GateOutputError" in logged


# --------------------------------------------------------------------------
# through the real-model path: max_tokens and temperature as sent, and no key (V02)
# --------------------------------------------------------------------------

def _fake_provider(seen: list):
    outputs = [lambda prompt: INTENT, lambda prompt: SYN, gate_says("supported")]

    def completion(**kw):
        seen.append(kw)
        prompt = kw["messages"][0]["content"]
        content = json.dumps(outputs[len(seen) - 1](prompt))

        class R:
            model, usage = "anthropic/test-model", {"prompt_tokens": 40, "completion_tokens": 9}
            choices = [type("C", (), {"finish_reason": "stop", "message": type("M", (), {"content": content})()})()]
        return R()
    return completion


def test_the_real_path_line_carries_max_tokens_and_no_temperature_and_never_the_key(db_path, caplog, tmp_path):
    seen: list = []
    cfg = b.BudgetConfig.from_env(ENV)
    inner = LiteLLMGateway(CircuitBreaker("model_provider", MemoryStore()), completion=_fake_provider(seen),
                           api_key=cfg.api_key, provider="anthropic")
    ledger = b.SpendLedger(tmp_path / "spend.json")
    try:
        with caplog.at_level(logging.DEBUG):
            out = run(b.BudgetedGateway(inner, cfg, ledger), db_path, caplog, model_name=cfg.model_id)
    finally:
        ledger.close()
    assert out.response["outcome"] == "answered" and len(seen) == 3
    got = [fields(x) for x in lines(caplog)]
    assert len(got) == 3
    for f, sent in zip(got, seen, strict=True):
        assert "temperature" not in sent and f["temperature"] == "provider_default"
        assert int(f["max_tokens"]) == sent["max_tokens"] == 1000
        assert f["model"] == "anthropic/test-model" and (f["input_tokens"], f["output_tokens"]) == ("40", "9")
    everything = caplog.text
    for secret in (FAKE_KEY, FAKE_KEY[:12], "sk-ant", "x-api-key", "Authorization"):
        assert secret not in everything, f"{secret} surfaced"


THOUGHT = "PRIVATE-THINKING-S-014-and-S-036-tie"


def _thinking_provider(seen: list):
    """As the fake provider above, but every response also carries thinking: its count in usage, as
    LiteLLM 1.105 fills completion_tokens_details for Anthropic, and its text, which must go nowhere."""
    outputs = [lambda prompt: INTENT, lambda prompt: SYN, gate_says("supported")]

    def completion(**kw):
        seen.append(kw)
        content = json.dumps(outputs[len(seen) - 1](kw["messages"][0]["content"]))
        message = type("M", (), {"content": content, "reasoning_content": THOUGHT,
                                 "thinking_blocks": [{"type": "thinking", "thinking": THOUGHT}]})()

        class R:
            model = "anthropic/test-model"
            usage = {"prompt_tokens": 40, "completion_tokens": 90,
                     "completion_tokens_details": {"reasoning_tokens": 70}}
            choices = [type("C", (), {"finish_reason": "stop", "message": message})()]
        return R()
    return completion


def test_the_thinking_count_is_logged_and_the_thinking_never_is(db_path, caplog, tmp_path):
    """Task 10a (chat, 22:40): Claude 5 thinks always (LiteLLM map: thinking_always_on). The line carries
    how many output tokens were thinking, never the thinking: not in any log, the audit or the answer."""
    seen: list = []
    cfg = b.BudgetConfig.from_env(ENV)
    inner = LiteLLMGateway(CircuitBreaker("model_provider", MemoryStore()), completion=_thinking_provider(seen),
                           api_key=cfg.api_key, provider="anthropic")
    ledger, storage = b.SpendLedger(tmp_path / "spend.json"), MemoryStorage()
    try:
        with caplog.at_level(logging.DEBUG):
            out = run(b.BudgetedGateway(inner, cfg, ledger), db_path, caplog, storage, model_name=cfg.model_id)
    finally:
        ledger.close()
    assert out.response["outcome"] == "answered"
    got = [fields(x) for x in lines(caplog)]
    assert [f["reasoning_tokens"] for f in got] == ["70", "70", "70"]
    assert all(f["output_tokens"] == "90" for f in got)   # thinking is inside output_tokens, priced as output
    assert all("thinking" not in s for s in seen)            # nothing that turns thinking on or off is sent
    surfaces = caplog.text + json.dumps([storage.audit_log, storage.investigations], default=str) \
        + json.dumps(out.response)
    assert THOUGHT not in surfaces and "PRIVATE-THINKING" not in surfaces


def test_a_count_that_is_not_an_int_is_not_taken():
    from model_client.client import _reasoning_tokens
    assert _reasoning_tokens({"reasoning_tokens": 5}) == 5 and _reasoning_tokens(None) is None
    assert _reasoning_tokens({"reasoning_tokens": True}) is None
    assert _reasoning_tokens({"reasoning_tokens": "5"}) is None
    assert _reasoning_tokens(type("D", (), {"reasoning_tokens": 0})()) == 0


# --------------------------------------------------------------------------
# the server's handler: the operational channel reaches stdout at INFO
# --------------------------------------------------------------------------

@pytest.fixture
def clean_channel():
    before, level = list(operational_log.handlers), operational_log.level
    yield
    for h in operational_log.handlers[:]:
        if h not in before:
            operational_log.removeHandler(h)
    operational_log.setLevel(level)


def test_the_server_writes_the_operational_channel_to_stdout_once(clean_channel, capsys):
    main.log_to_stdout()
    main.log_to_stdout()
    ours = [h for h in operational_log.handlers if getattr(h, "_medagentpro", False)]
    assert len(ours) == 1
    operational_log.info("model call: stage=probe")
    assert capsys.readouterr().out.count("model call: stage=probe") == 1
