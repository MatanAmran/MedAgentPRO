"""
Claude Haiku 5.5 for development rounds (chat 19.10, Phase B task 1), priced by a manual entry: LiteLLM
1.105, pinned, does not hold the model. No model is called here.
"""

import json
import subprocess  # nosec B404  runs this test's own probe
import sys
from pathlib import Path

import pytest

from model_client import budget as b
from model_client.client import BudgetExceeded, ModelConfig, ScriptedModelGateway
from tools import dry_run

ENV = {"ANTHROPIC_API_KEY": "test-key-not-real", "MODEL_ID": "claude-haiku-5-5", "MODEL_PRICE_IN": "0.10",
       "MODEL_PRICE_OUT": "0.50", "MODEL_BUDGET_USD": "1", "MODEL_MAX_USD_PER_EXCHANGE": "0.25",
       "MODEL_MAX_OUTPUT_TOKENS": "2000", "MODEL_MAX_EXCHANGES_PER_MINUTE": "10"}


def test_the_manual_entry_carries_the_card_prices_and_limits():
    e = b.map_entry("claude-haiku-5-5")
    assert e["input_cost_per_token"] * 1e6 == pytest.approx(0.10)
    assert e["output_cost_per_token"] * 1e6 == pytest.approx(0.50)
    assert e["max_output_tokens"] == 128_000 and e["supports_sampling_params"] is False
    assert e["supports_native_structured_output"] is True and e["supports_forced_tool_use"] is False
    assert b.map_prices("claude-haiku-5-5") == pytest.approx((0.10, 0.50))
    assert b.map_entry("anthropic/claude-haiku-5-5") == e


def test_litellms_own_entry_wins_where_it_has_one(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "model_cost", {"claude-haiku-5-5": {"input_cost_per_token": 9e-06}})
    assert b.map_entry("claude-haiku-5-5") == {"input_cost_per_token": 9e-06}


def test_the_budget_starts_on_haiku_and_reserves_at_its_price(ledger):
    cfg = b.BudgetConfig.from_env(ENV)
    b.check_against_provider_map(cfg)
    gw = b.BudgetedGateway(ScriptedModelGateway(lambda p, c: "{}"), cfg, ledger)
    prompt = "x" * 20_000                      # 10,000 tokens as estimated (bytes over two)
    want = 10_000 * 0.10 / 1e6 + 2000 * 0.50 / 1e6
    assert gw.worst_case(10_000) == pytest.approx(want)
    gw.invoke(prompt, ModelConfig("claude-haiku-5-5", prompt_identity="synthesis@11"))


def test_a_prompt_above_the_price_tier_is_not_sent(ledger):
    """Above 100K input tokens Haiku 5.5 costs five times as much: the .env price would undercount."""
    inner = ScriptedModelGateway(lambda p, c: "{}")
    gw = b.BudgetedGateway(inner, b.BudgetConfig.from_env({**ENV, "MODEL_MAX_USD_PER_EXCHANGE": "5",
                                                            "MODEL_BUDGET_USD": "5"}), ledger)
    with pytest.raises(BudgetExceeded, match="100000 input tokens") as e:
        gw.invoke("x" * 200_002, ModelConfig("claude-haiku-5-5", prompt_identity="synthesis@11"))
    assert e.value.scope == "model" and inner.calls == []


# register_model changes LiteLLM's process-wide state (the map, its provider model sets and the list it
# replays on a reload), which no fixture undoes reliably: these two run in a process of their own.
_PROBE = """
import json, os
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm
from litellm.llms.anthropic.chat.transformation import AnthropicConfig
from model_client import budget as b
rf = {"type": "json_schema", "json_schema": {"name": "synthesis", "schema": {"type": "object"}}}
def mapped():
    return sorted(AnthropicConfig().map_openai_params({"response_format": rf, "max_tokens": 100}, {},
                                                      "claude-haiku-5-5", False))
out = {"held_before": "claude-haiku-5-5" in litellm.model_cost, "keys_before": mapped(),
       "added": b.register_manual_models(), "again": b.register_manual_models()}
e = litellm.model_cost["claude-haiku-5-5"]
out.update(out_price=e["output_cost_per_token"], native=e["supports_native_structured_output"],
           sampling=e["supports_sampling_params"], keys_after=mapped(),
           info_in=litellm.get_model_info("anthropic/claude-haiku-5-5")["input_cost_per_token"])
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def registered():
    run = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=120,
                         cwd=Path(__file__).parents[2], check=True)   # noqa: S603  our own script
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_registering_adds_the_entry_to_litellms_map_once(registered):
    """What the real gateway does before its first call."""
    assert registered["held_before"] is False
    assert registered["added"] == ["claude-haiku-5-5"] and registered["again"] == []
    assert registered["out_price"] == 5e-07 and registered["sampling"] is False
    assert registered["info_in"] == 1e-07


def test_registered_haiku_gets_native_structured_output_and_no_tools(registered):
    """R2 on Haiku 5.5: unregistered, LiteLLM 1.105 maps response_format to a forced json_tool_call (tools
    are never sent, R2); registered, to Anthropic's native output_format, as on Sonnet 5.5. Request
    mapping only, nothing is sent."""
    assert "tools" in registered["keys_before"] and "tool_choice" in registered["keys_before"]
    assert registered["native"] is True
    assert "output_format" in registered["keys_after"]
    assert "tools" not in registered["keys_after"] and "tool_choice" not in registered["keys_after"]


def test_the_dry_run_prices_haiku_from_the_manual_entry(capsys):
    assert dry_run.main(argv=["--model", "claude-haiku-5-5"]) == 0
    assert "claude-haiku-5-5" in capsys.readouterr().out
