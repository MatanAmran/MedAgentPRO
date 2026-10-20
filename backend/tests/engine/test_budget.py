"""
Tests for model_client/budget.py and the money handling in LiteLLMGateway (task 5). Run:  pytest -q

No test calls a real model (6.1) or reads backend/.env: every configuration is passed in, and the
values here are made up for the tests.
"""

import json
import sys
import threading

import pytest

from demo_data import seed
from model_client import budget as b
from model_client.client import (
    BudgetExceeded,
    LiteLLMGateway,
    ModelCallError,
    ModelConfig,
    RateLimited,
    ScriptedModelGateway,
)
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import contract, pipeline, prompts
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, CircuitOpen, MemoryStore
from tools import dry_run

ENV = {"ANTHROPIC_API_KEY": "test-key-not-real", "MODEL_ID": "test-model", "MODEL_PRICE_IN": "1",
       "MODEL_PRICE_OUT": "10", "MODEL_BUDGET_USD": "1", "MODEL_MAX_USD_PER_EXCHANGE": "0.5",
       "MODEL_MAX_OUTPUT_TOKENS": "1000", "MODEL_MAX_EXCHANGES_PER_MINUTE": "3"}
CFG = ModelConfig("test-model", prompt_identity="x@1")


def config(**over):
    return b.BudgetConfig.from_env({**ENV, **over})


def gateway(ledger, inner=None, clock=None, **over):
    return b.BudgetedGateway(inner or ScriptedModelGateway(lambda p, c: "{}"), config(**over), ledger,
                             **({"clock": clock} if clock else {}))


# --------------------------------------------------------------------------
# configuration: nothing has a default
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", b.REQUIRED)
def test_a_missing_value_refuses_to_start_and_names_only_the_variable(name):
    with pytest.raises(b.BudgetConfigError, match=name) as e:
        b.BudgetConfig.from_env({k: v for k, v in ENV.items() if k != name})
    assert "test-key-not-real" not in str(e.value)


@pytest.mark.parametrize("value", ["abc", "0", "-1", "nan"])
def test_a_money_value_must_be_a_positive_number(value):
    with pytest.raises(b.BudgetConfigError, match="MODEL_BUDGET_USD"):
        config(MODEL_BUDGET_USD=value)


def test_the_key_never_appears_in_the_config_repr():
    assert "test-key-not-real" not in repr(config())


def test_env_file_wins_over_the_process_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in ENV.items()), encoding="utf-8")
    monkeypatch.setenv("MODEL_ID", "some-other-model")
    assert b.BudgetConfig.from_env(env_file=env_file).model_id == "test-model"


def test_the_dry_run_needs_no_key():
    assert b.BudgetConfig.from_env({k: v for k, v in ENV.items() if k != "ANTHROPIC_API_KEY"},
                                   need_key=False).api_key == ""


# --------------------------------------------------------------------------
# the caps, checked before the call is sent
# --------------------------------------------------------------------------

def test_a_call_that_would_cross_the_total_cap_is_not_sent(ledger):
    inner = ScriptedModelGateway(lambda p, c: "{}")
    gw = gateway(ledger, inner, MODEL_BUDGET_USD="0.005")   # 1000 output tokens alone cost 0.01
    with pytest.raises(BudgetExceeded, match="used up"):
        gw.invoke("x", CFG)
    assert inner.calls == [] and ledger.total == 0


def test_the_exchange_cap(ledger):
    prompt = "x" * 4000                           # reserved as 2000 input tokens: worst 0.002 + 0.01
    gw = gateway(ledger, MODEL_MAX_USD_PER_EXCHANGE="0.0125")
    with gw.exchange() as spend:
        gw.invoke(prompt, CFG)                    # worst 0.012 fits, actual about 0.001
        assert 0.0009 < spend.spent < 0.0012
        with pytest.raises(BudgetExceeded, match="its own cap"):
            gw.invoke(prompt, CFG)                # spent + worst is about 0.013
    with gw.exchange():
        gw.invoke(prompt, CFG)                    # a new exchange starts from zero


def test_output_is_capped_on_every_call(ledger):
    inner = ScriptedModelGateway(lambda p, c: "{}")
    gateway(ledger, inner).invoke("x", ModelConfig("test-model", max_tokens=50_000))
    assert inner.calls[0][1].max_tokens == 1000


# --------------------------------------------------------------------------
# intent analysis's own output cap (task 10a, chat 23:35 and 01:15)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("identity, sent", [("intent_analysis_demo@5", 3000), ("intent_analysis@3", 3000),
                                             ("synthesis@1", 1000), ("support_scoring@1", 1000),
                                             ("question_rewriting@1", 1000)])
def test_the_intent_cap_applies_to_intent_analysis_only(ledger, identity, sent):
    inner = ScriptedModelGateway(lambda p, c: "{}")
    gw = gateway(ledger, inner, MODEL_MAX_OUTPUT_TOKENS_INTENT="3000", MODEL_MAX_USD_PER_EXCHANGE="0.5")
    gw.invoke("x", ModelConfig("test-model", prompt_identity=identity))
    assert inner.calls[0][1].max_tokens == sent


def test_without_the_intent_cap_intent_gets_the_general_one(ledger):
    inner = ScriptedModelGateway(lambda p, c: "{}")
    assert config().max_output_tokens_intent is None and config(MODEL_MAX_OUTPUT_TOKENS_INTENT="").output_cap(
        "intent_analysis_demo@5") == 1000
    gateway(ledger, inner).invoke("x", ModelConfig("test-model", prompt_identity="intent_analysis_demo@5"))
    assert inner.calls[0][1].max_tokens == 1000


def test_the_intent_cap_is_reserved_at_its_own_worst_case(ledger):
    """3000 output tokens at 10 per million is 0.03: over an exchange cap of 0.025, under one of 0.035."""
    intent = ModelConfig("test-model", prompt_identity="intent_analysis_demo@5")
    tight = gateway(ledger, MODEL_MAX_OUTPUT_TOKENS_INTENT="3000", MODEL_MAX_USD_PER_EXCHANGE="0.025")
    with tight.exchange():
        tight.invoke("x", ModelConfig("test-model", prompt_identity="synthesis@1"))   # 0.01 at the general cap
        with pytest.raises(BudgetExceeded, match="its own cap"):
            tight.invoke("x", intent)
    roomy = gateway(ledger, MODEL_MAX_OUTPUT_TOKENS_INTENT="3000", MODEL_MAX_USD_PER_EXCHANGE="0.035")
    with roomy.exchange():
        roomy.invoke("x", intent)


@pytest.mark.parametrize("value", ["abc", "0", "-5", "2.5"])
def test_an_intent_cap_that_is_set_must_be_a_positive_integer(value):
    with pytest.raises(b.BudgetConfigError, match="MODEL_MAX_OUTPUT_TOKENS_INTENT"):
        config(MODEL_MAX_OUTPUT_TOKENS_INTENT=value)


def test_the_dry_run_prices_intent_at_its_own_cap(db_path):
    cfg = config(MODEL_MAX_OUTPUT_TOKENS_INTENT="3000")
    meter = dry_run._Meter(ScriptedModelGateway(lambda p, c: "{}"), cfg)
    meter.invoke("x", ModelConfig("test-model", prompt_identity="intent_analysis_demo@5"))
    meter.invoke("x", ModelConfig("test-model", prompt_identity="synthesis@1"))
    assert [round(r[-1], 6) for r in meter.rows] == [round(cfg.cost(1, 3000), 6), round(cfg.cost(1, 1000), 6)]


def test_a_model_not_priced_in_env_is_refused(ledger):
    with pytest.raises(BudgetExceeded, match="not the one priced"):
        gateway(ledger).invoke("x", ModelConfig("another-model"))


def test_actual_cost_is_recorded_and_the_reservation_released(ledger):
    gw = gateway(ledger)
    resp = gw.invoke("x" * 400, CFG)
    expected = (resp.input_tokens * 1 + resp.output_tokens * 10) / 1_000_000
    assert ledger.total == pytest.approx(expected) and gw._reserved == 0


def test_cached_tokens_are_never_counted_below_the_input_price():
    """T1: cached tokens at the write price, or twice the input price."""
    assert config().cost(0, 0, cache_read=1_000_000) == pytest.approx(2.0)
    assert config(MODEL_PRICE_CACHE_WRITE="1.25").cost(0, 0, cache_write=1_000_000) == pytest.approx(1.25)


def test_a_failed_call_is_charged_its_worst_case_and_an_unsent_one_nothing(ledger):
    def boom(p, c):
        raise ConnectionError("reset")
    with pytest.raises(ConnectionError):
        gateway(ledger, ScriptedModelGateway(boom)).invoke("x", CFG)
    charged = ledger.total
    assert charged >= 0.01

    def down(p, c):
        raise CircuitOpen("model_provider")
    with pytest.raises(CircuitOpen):
        gateway(ledger, ScriptedModelGateway(down)).invoke("x", CFG)
    assert ledger.total == charged


def test_two_concurrent_calls_cannot_pass_on_the_same_remaining_amount(ledger):
    """T2: check, reservation and record under one lock."""
    inside, release = threading.Event(), threading.Event()

    def slow(p, c):
        inside.set()
        release.wait(5)
        return "{}"
    gw = gateway(ledger, ScriptedModelGateway(slow), MODEL_BUDGET_USD="0.015")
    t = threading.Thread(target=gw.invoke, args=("x", CFG))
    t.start()
    assert inside.wait(5)
    with pytest.raises(BudgetExceeded):
        gw.invoke("x", CFG)   # the first call's worst case is still reserved
    release.set()
    t.join()


# --------------------------------------------------------------------------
# spend kept across restarts, and one process only
# --------------------------------------------------------------------------

def test_spend_survives_a_restart(tmp_path):
    path = tmp_path / "spend.json"
    lg = b.SpendLedger(path)
    lg.add("test-model", 0.25)
    lg.close()
    again = b.SpendLedger(path)
    assert again.total == pytest.approx(0.25) and again.data["by_model"]["test-model"] == pytest.approx(0.25)
    assert len(again.data["by_day"]) == 1
    again.close()


def test_a_second_holder_of_the_spend_file_is_refused(tmp_path):
    path = tmp_path / "spend.json"
    first = b.SpendLedger(path)
    with pytest.raises(b.BudgetConfigError, match="one server process"):
        b.SpendLedger(path)
    first.close()
    b.SpendLedger(path).close()   # free again once the holder is gone


# A damaged spend file fails when it is loaded, before any call, never after a paid one (chat review,
# 14.10, 14:15). The file format is unchanged.
DAMAGED = {
    "string total": '{"total_usd": "0.5", "by_day": {}, "by_model": {}}',
    "string in by_model": '{"total_usd": 0.5, "by_day": {}, "by_model": {"m": "0.5"}}',
    "string in by_day": '{"total_usd": 0.5, "by_day": {"2026-10-14": "0.5"}, "by_model": {}}',
    "bool total": '{"total_usd": true, "by_day": {}, "by_model": {}}',
    "NaN total": '{"total_usd": NaN, "by_day": {}, "by_model": {}}',
    "negative total": '{"total_usd": -5, "by_day": {}, "by_model": {}}',
    "by_model a list": '{"total_usd": 0.5, "by_day": {}, "by_model": [0.5]}',
    "not an object": '[0.5]',
    "not JSON": '{"total_usd": 0.5',
}


@pytest.mark.parametrize("text", DAMAGED.values(), ids=DAMAGED.keys())
def test_a_damaged_spend_file_refuses_to_load(tmp_path, text):
    path = tmp_path / "spend.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(b.BudgetConfigError, match=r"spend\.json is damaged") as e:
        b.SpendLedger(path)
    assert "0.5" not in str(e.value)        # names the field, never a value
    assert path.read_text(encoding="utf-8") == text
    b.SpendLedger(path.with_name("other.json")).close()
    path.unlink()
    b.SpendLedger(path).close()              # the lock was released on the refusal


def test_a_damaged_spend_file_sends_no_call(tmp_path, monkeypatch):
    """Through the real builder: the ledger refuses before any gateway is returned, so nothing is sent."""
    path = tmp_path / "spend.json"
    path.write_text('{"total_usd": 0.0, "by_day": {}, "by_model": {"m": "x"}}', encoding="utf-8")
    sent = []
    monkeypatch.setattr(b.LiteLLMGateway, "invoke", lambda self, *a, **k: sent.append(a))
    monkeypatch.delitem(sys.modules, "pytest")
    monkeypatch.delenv("CI", raising=False)   # the CI guard would refuse first (Phase B, 19.10)
    monkeypatch.setattr(b, "check_against_provider_map", lambda cfg: None)
    with pytest.raises(b.BudgetConfigError, match="damaged"):
        b.real_gateway(ENV, spend_file=path)
    assert sent == []


def test_a_sound_spend_file_loads_as_numbers(tmp_path):
    path = tmp_path / "spend.json"
    path.write_text('{"total_usd": 1, "by_day": {"2026-10-14": 1}, "by_model": {"m": 1}}', encoding="utf-8")
    lg = b.SpendLedger(path)
    assert lg.total == 1.0 and isinstance(lg.total, float)
    lg.add("m", 0.5)
    lg.close()
    assert json.loads(path.read_text(encoding="utf-8"))["by_model"] == {"m": 1.5}


# --------------------------------------------------------------------------
# the rate across users
# --------------------------------------------------------------------------

def test_rate_limit(ledger):
    now = [1000.0]
    gw = gateway(ledger, clock=lambda: now[0])
    for _ in range(3):
        with gw.exchange():
            pass
    with pytest.raises(RateLimited):
        with gw.exchange():
            pass
    now[0] += 60
    with gw.exchange():
        pass


def test_a_rate_refusal_says_how_long_to_wait(ledger):
    """Chat, 18.10, 04:45: the wait is computed from the window, until its oldest start is 60 s old."""
    now = [1000.0]
    gw = gateway(ledger, clock=lambda: now[0])
    for step in (0.0, 10.0, 5.0):          # starts at 1000, 1010 and 1015
        now[0] += step
        with gw.exchange():
            pass
    for later, wait in ((0.0, 45), (44.5, 1), (20.2, 25)):
        now[0] = 1015.0 + later
        with pytest.raises(RateLimited) as e:
            with gw.exchange():
                pass
        assert e.value.retry_after == wait
    now[0] = 1060.0                        # the first start has left the window
    with gw.exchange():
        pass


# --------------------------------------------------------------------------
# LiteLLM: no hidden retries, money refusals are not outages
# --------------------------------------------------------------------------

class _Refusal(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status


@pytest.mark.parametrize("status, message", [
    (429, '{"error": {"error_code": "enforced_spend_limit_reached"}}'),
    (400, "invalid_request_error: Your credit balance is too low to access the API."),
])
def test_provider_money_refusal_is_not_retried_and_not_an_outage(status, message):
    calls = []

    def completion(**kw):
        calls.append(kw)
        raise _Refusal(status, message)
    breaker = CircuitBreaker("model_provider", MemoryStore(), threshold=1)
    gw = LiteLLMGateway(breaker, completion=completion)
    with pytest.raises(BudgetExceeded, match="credit or spend limit"):
        gw.invoke("x", CFG)
    assert len(calls) == 1 and breaker.failure_count == 0 and breaker.get_state() == "closed"


def test_other_provider_errors_still_count_as_failures():
    def completion(**kw):
        raise _Refusal(429, "rate_limit_error: too many requests")
    breaker = CircuitBreaker("model_provider", MemoryStore(), threshold=5)
    with pytest.raises(ModelCallError) as e:
        LiteLLMGateway(breaker, completion=completion).invoke("x", CFG)
    assert e.value.kind == "rate_limited" and e.value.outage and breaker.failure_count == 1


def test_litellm_retries_are_off_and_the_provider_is_prefixed():
    seen = {}

    class R:
        model, usage = "anthropic/test-model", {"prompt_tokens": 3, "completion_tokens": 2}
        choices = [type("C", (), {"message": type("M", (), {"content": "{}"})()})()]

    def completion(**kw):
        seen.update(kw)
        return R()
    gw = LiteLLMGateway(CircuitBreaker("model_provider", MemoryStore()), completion=completion,
                        api_key="test-key-not-real", provider="anthropic")
    gw.invoke("x", CFG)
    assert seen["num_retries"] == 0 and seen["max_retries"] == 0
    assert seen["model"] == "anthropic/test-model" and seen["api_key"] == "test-key-not-real"


def test_real_gateway_is_never_built_under_pytest(tmp_path):
    """R8. Even with every value present, before any is read. A missing value is tested on from_env."""
    with pytest.raises(b.BudgetConfigError, match="never built under pytest"):
        b.real_gateway(ENV, tmp_path / "spend.json")
    assert not (tmp_path / "spend.lock").exists()


# --------------------------------------------------------------------------
# in the pipeline
# --------------------------------------------------------------------------

def deps(gw, db_path, storage):
    return pipeline.Deps(gw, "test-model", Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json"),
                         pipeline.connect_readonly(db_path),
                         AuditRepository(storage, CircuitBreaker("system_store", MemoryStore()), b"k"),
                         prompts.load(seed.HERE / "intent_analysis.md"))


def test_exhausted_budget_ends_the_exchange_with_its_notice(ledger, db_path):
    inner = ScriptedModelGateway(lambda p, c: "{}")
    storage = MemoryStorage()
    out = pipeline.run(dry_run.Q, [], {"role": "APPROVER", "user_id": "AP-01"},
                       deps(gateway(ledger, inner, MODEL_BUDGET_USD="0.001"), db_path, storage))
    assert out.response["outcome"] == "request_not_completed"
    assert out.response["notice"] == contract.BUDGET_USED_UP and inner.calls == [] and out.recorded
    assert storage.investigations[out.exchange_id]["failure"].startswith("budget (total):")
    assert out.cost == {"exchange_usd": 0, "remaining_usd": 0.001}


def test_rate_limited_exchange_is_refused_before_any_model_call(ledger, db_path):
    inner = ScriptedModelGateway(lambda p, c: "{}")
    gw = gateway(ledger, inner, MODEL_MAX_EXCHANGES_PER_MINUTE="1")
    with gw.exchange():
        pass
    out = pipeline.run(dry_run.Q, [], {"role": "APPROVER", "user_id": "AP-01"}, deps(gw, db_path, MemoryStorage()))
    assert out.response["notice"] == contract.rate_limited_notice(60) and inner.calls == [] and out.recorded


def test_an_answered_exchange_reports_its_cost(ledger, db_path):
    script = dry_run._script(intent_analysis_demo=[dry_run.INTENT], synthesis=[dry_run.SYN],
                             support_scoring=[dry_run._gate("supported")])
    storage = MemoryStorage()
    out = pipeline.run(dry_run.Q, [], {"role": "APPROVER", "user_id": "AP-01"},
                       deps(gateway(ledger, script), db_path, storage))
    assert out.response["outcome"] == "answered"
    assert out.cost["exchange_usd"] == pytest.approx(ledger.total, abs=1e-6)
    assert out.cost["remaining_usd"] == pytest.approx(1 - ledger.total, abs=1e-6)
    assert storage.investigations[out.exchange_id]["cost"] == out.cost
    assert "cost" not in out.response   # T3: the answer contract is unchanged


def test_dry_run_prices_two_exchanges_without_a_key(capsys):
    assert dry_run.main({k: v for k, v in ENV.items() if k != "ANTHROPIC_API_KEY"}) == 0
    text = capsys.readouterr().out
    assert "A first question (3 calls)" in text and "(6 calls)" in text and "fits" in text


def test_dry_run_with_a_model_prices_from_the_local_map_and_reads_no_env(capsys, monkeypatch):
    """No .env before the smoke test: --model takes the prices from LiteLLM's local map."""
    def no_env(*a, **k):
        raise AssertionError(".env was read")
    monkeypatch.setattr(b.BudgetConfig, "from_env", no_env)
    assert dry_run.main(argv=["--model", "claude-sonnet-5-5"]) == 0
    text = capsys.readouterr().out
    assert "2 in / 10 out per million, from LiteLLM's local map" in text and "R7a reserves" in text
    assert dry_run.main(argv=["--model", "claude-not-a-model-9-9"]) == 1


# --------------------------------------------------------------------------
# the gate's own output cap (chat, 18.10, 13:50; D4 of the held-out set)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("identity, sent", [("support_scoring@5", 4000), ("intent_analysis_demo@8", 3000),
                                             ("synthesis@10", 1000), ("synthesis_redraft@7", 1000),
                                             ("question_rewriting@1", 1000)])
def test_the_gate_cap_applies_to_the_gate_only(ledger, identity, sent):
    inner = ScriptedModelGateway(lambda p, c: "{}")
    gw = gateway(ledger, inner, MODEL_MAX_OUTPUT_TOKENS_GATE="4000", MODEL_MAX_OUTPUT_TOKENS_INTENT="3000")
    gw.invoke("x", ModelConfig("test-model", prompt_identity=identity))
    assert inner.calls[0][1].max_tokens == sent


def test_without_the_gate_cap_the_gate_gets_the_general_one(ledger):
    assert config().max_output_tokens_gate is None
    assert config(MODEL_MAX_OUTPUT_TOKENS_GATE="").output_cap("support_scoring@5") == 1000


def test_the_gate_cap_is_reserved_at_its_own_worst_case(ledger):
    """4000 output tokens at 10 per million is 0.04: over an exchange cap of 0.035, under one of 0.045."""
    gate = ModelConfig("test-model", prompt_identity="support_scoring@5")
    tight = gateway(ledger, MODEL_MAX_OUTPUT_TOKENS_GATE="4000", MODEL_MAX_USD_PER_EXCHANGE="0.035")
    with tight.exchange(), pytest.raises(BudgetExceeded, match="its own cap"):
        tight.invoke("x", gate)
    roomy = gateway(ledger, MODEL_MAX_OUTPUT_TOKENS_GATE="4000", MODEL_MAX_USD_PER_EXCHANGE="0.045")
    with roomy.exchange():
        roomy.invoke("x", gate)


def test_r7a_prices_each_gate_call_of_the_path_at_the_gate_cap(ledger):
    """Synthesis, gate, redraft, gate, one input token each: 1000 + 4000 + 1000 + 4000 output tokens is 0.1
    at 10 per million (plus 4e-06 of input). Under a cap of 0.1 it is refused; at the general cap, 0.04, not."""
    from query_assistance.pipeline import PATH_PROMPTS
    gw = gateway(ledger, MODEL_MAX_OUTPUT_TOKENS_GATE="4000", MODEL_MAX_USD_PER_EXCHANGE="0.1")
    with gw.exchange():
        gw.ensure_path([1, 1, 1, 1])                          # without names: the general cap
        with pytest.raises(BudgetExceeded, match="its own cap"):
            gw.ensure_path([1, 1, 1, 1], PATH_PROMPTS)


@pytest.mark.parametrize("value", ["abc", "0", "-5", "2.5"])
def test_a_gate_cap_that_is_set_must_be_a_positive_integer(value):
    with pytest.raises(b.BudgetConfigError, match="MODEL_MAX_OUTPUT_TOKENS_GATE"):
        config(MODEL_MAX_OUTPUT_TOKENS_GATE=value)


# --------------------------------------------------------------------------
# synthesis's own output cap (chat 20.10, 14:50; Haiku's demo round cut 5 of 14 drafts at 2000)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("identity, sent", [("synthesis@11", 4000), ("synthesis_redraft@10", 4000),
                                             ("support_scoring@6", 3000), ("intent_analysis_demo@9", 1000),
                                             ("question_rewriting@3", 1000)])
def test_the_synthesis_cap_applies_to_the_draft_and_the_redraft_only(ledger, identity, sent):
    inner = ScriptedModelGateway(lambda p, c: "{}")
    gw = gateway(ledger, inner, MODEL_MAX_OUTPUT_TOKENS_SYNTHESIS="4000", MODEL_MAX_OUTPUT_TOKENS_GATE="3000")
    gw.invoke("x", ModelConfig("test-model", prompt_identity=identity))
    assert inner.calls[0][1].max_tokens == sent


def test_without_the_synthesis_cap_synthesis_gets_the_general_one():
    assert config().max_output_tokens_synthesis is None
    assert config(MODEL_MAX_OUTPUT_TOKENS_SYNTHESIS="").output_cap("synthesis@11") == 1000


def test_r7a_prices_the_draft_and_the_redraft_at_the_synthesis_cap(ledger):
    """Synthesis, gate, redraft, gate, one input token each: 4000 + 1000 + 4000 + 1000 output tokens is 0.1
    at 10 per million (plus 4e-06 of input). Under a cap of 0.1 it is refused; at the general cap, 0.04, not."""
    from query_assistance.pipeline import PATH_PROMPTS
    gw = gateway(ledger, MODEL_MAX_OUTPUT_TOKENS_SYNTHESIS="4000", MODEL_MAX_USD_PER_EXCHANGE="0.1")
    with gw.exchange():
        gw.ensure_path([1, 1, 1, 1])
        with pytest.raises(BudgetExceeded, match="its own cap"):
            gw.ensure_path([1, 1, 1, 1], PATH_PROMPTS)


@pytest.mark.parametrize("value", ["abc", "0", "-5", "2.5"])
def test_a_synthesis_cap_that_is_set_must_be_a_positive_integer(value):
    with pytest.raises(b.BudgetConfigError, match="MODEL_MAX_OUTPUT_TOKENS_SYNTHESIS"):
        config(MODEL_MAX_OUTPUT_TOKENS_SYNTHESIS=value)


def test_the_dry_run_prices_the_gate_and_the_path_at_the_gate_cap(db_path):
    cfg = config(MODEL_MAX_OUTPUT_TOKENS_GATE="4000")
    meter = dry_run._Meter(ScriptedModelGateway(lambda p, c: "{}"), cfg)
    meter.invoke("x", ModelConfig("test-model", prompt_identity="support_scoring@5"))
    assert round(meter.rows[0][-1], 6) == round(cfg.cost(1, 4000), 6)
    meter.ensure_path([1, 1, 1, 1], ("synthesis", "support_scoring", "synthesis_redraft", "support_scoring"))
    assert meter.path_worst == pytest.approx(cfg.cost(4, 10000))
