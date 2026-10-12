"""
Tests for model_client/budget.py and the money handling in LiteLLMGateway (task 5). Run:  pytest -q

No test calls a real model (6.1) or reads backend/.env: every configuration is passed in, and the
values here are made up for the tests.
"""

import threading

import pytest

from demo_data import seed
from model_client import budget as b
from model_client.client import (BudgetExceeded, LiteLLMGateway, ModelCallError, ModelConfig, RateLimited,
                                 ScriptedModelGateway)
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import contract, dry_run, pipeline, prompts
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, CircuitOpen, MemoryStore

ENV = {"ANTHROPIC_API_KEY": "test-key-not-real", "MODEL_ID": "test-model", "MODEL_PRICE_IN": "1",
       "MODEL_PRICE_OUT": "10", "MODEL_BUDGET_USD": "1", "MODEL_MAX_USD_PER_EXCHANGE": "0.5",
       "MODEL_MAX_OUTPUT_TOKENS": "1000", "MODEL_MAX_EXCHANGES_PER_MINUTE": "3"}
CFG = ModelConfig("test-model", prompt_identity="x@1")


def config(**over):
    return b.BudgetConfig.from_env({**ENV, **over})


@pytest.fixture
def ledger(tmp_path):
    lg = b.SpendLedger(tmp_path / "spend.json")
    yield lg
    lg.close()


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

@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.db"
    seed.build(path)
    return path


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
    assert out.response["notice"] == contract.RATE_LIMITED and inner.calls == [] and out.recorded


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
