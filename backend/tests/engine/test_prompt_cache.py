"""
Prompt caching (Phase B task 7, chat 19.10): the stable prefix of each prompt (its instructions, the
catalog's schema, the examples) is marked with cache_control for Anthropic's cache. Only what is sent is
checked here; the saving needs real calls and is not measured. Off unless MODEL_PROMPT_CACHE is set (T1).
"""

import pytest

from demo_data import seed
from model_client import budget as b
from model_client.client import LiteLLMGateway, ModelConfig, ScriptedModelGateway
from query_assistance import calls
from query_assistance import prompts as p
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, MemoryStore

CAT = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
INTENT = p.load(seed.HERE / "intent_analysis.md")
Q1, Q2 = "Which suppliers were late in weeks 1 to 13?", "What did S-040 supply?"
ENV = {"ANTHROPIC_API_KEY": "test-key-not-real", "MODEL_ID": "claude-sonnet-5-5", "MODEL_PRICE_IN": "2",
       "MODEL_PRICE_OUT": "10", "MODEL_BUDGET_USD": "1", "MODEL_MAX_USD_PER_EXCHANGE": "0.25",
       "MODEL_MAX_OUTPUT_TOKENS": "2000", "MODEL_MAX_EXCHANGES_PER_MINUTE": "10"}


def test_the_intent_prompts_prefix_is_its_instructions_schema_and_examples():
    one = p.render_intent(CAT, "APPROVER", Q1, prompt=INTENT)
    two = p.render_intent(CAT, "APPROVER", Q2, prompt=INTENT)
    assert one.startswith(one.cache_prefix) and two.startswith(two.cache_prefix)
    assert one.cache_prefix == two.cache_prefix                    # the same for every question
    assert "CREATE TABLE commitments" in one.cache_prefix          # the catalog's schema
    assert Q1 not in one.cache_prefix and Q1 in one[len(one.cache_prefix):]
    assert "Output:" in one.cache_prefix                           # the examples


def test_the_prefix_holds_no_user_and_no_entitlement_rule():
    """What is cached is shared by everyone with the role: it names no user, and the entitlement rules
    themselves are never shown (prompts.visible_tables), so no planner's condition is cached for another."""
    restricted = Catalog.load(seed.HERE.parent / "tests" / "fixtures" / "restricted_schema.json",
                              seed.HERE / "relations.json")
    prefix = p.render_intent(restricted, "PLANNER", Q1, prompt=INTENT).cache_prefix
    assert "PL-0" not in prefix and "AP-0" not in prefix and "planner_id = " not in prefix
    assert "equals" not in prefix and "entitlement" not in prefix


@pytest.mark.parametrize("name, volatile", [("rewrite.md", "{{history}}"), ("gate.md", "{{answer}}"),
                                            ("synthesis.md", "{{question_block}}"),
                                            ("synthesis_redraft.md", "{{question_block}}")])
def test_every_template_has_a_prefix_up_to_its_first_request_part(name, volatile):
    pr = p.load(name)
    head = pr.body.split(volatile)[0]
    values = {k: f"<{k}>" for k in p._PLACEHOLDER.findall(pr.body)}
    out = p.fill(pr, **values)
    assert out.cache_prefix == p._PLACEHOLDER.sub(lambda m: values[m.group(1)], head)
    assert not any(f"<{k}>" in out.cache_prefix for k in p.VOLATILE)


def test_a_value_holding_a_placeholder_does_not_move_the_prefix():
    """R6 still holds: the prefix is cut on the template, never on a filled value."""
    out = p.render_intent(CAT, "APPROVER", "What about {{schema}}?", prompt=INTENT)
    assert "What about {{schema}}?" not in out.cache_prefix and out.startswith(out.cache_prefix)


class _Response:
    model, usage = "anthropic/claude-sonnet-5-5", {"prompt_tokens": 3, "completion_tokens": 2}
    choices = [type("C", (), {"message": type("M", (), {"content": "{}"})()})()]


def _sent(cache_prompts: bool, prompt: str, prefix: str | None) -> dict:
    seen: dict = {}

    def completion(**kw):
        seen.update(kw)
        return _Response()
    gw = LiteLLMGateway(CircuitBreaker("model_provider", MemoryStore()), completion=completion,
                        provider="anthropic", cache_prompts=cache_prompts)
    gw.invoke(prompt, ModelConfig("claude-sonnet-5-5", prompt_identity="synthesis@11", cache_prefix=prefix))
    return seen


def test_the_stable_prefix_is_sent_as_a_cached_block_and_the_rest_after_it():
    msg = _sent(True, "STABLE PART\nquestion part", "STABLE PART\n")["messages"]
    assert len(msg) == 1 and msg[0]["role"] == "user"
    first, rest = msg[0]["content"]
    assert first == {"type": "text", "text": "STABLE PART\n", "cache_control": {"type": "ephemeral"}}
    assert rest == {"type": "text", "text": "question part"} and "cache_control" not in rest
    assert first["text"] + rest["text"] == "STABLE PART\nquestion part"   # the same text, split in two


@pytest.mark.parametrize("cache_prompts, prefix", [
    (False, "STABLE PART\n"),            # off unless MODEL_PROMPT_CACHE is set (T1)
    (True, None), (True, ""),            # no prefix
    (True, "OTHER\n"),                   # a prefix the prompt does not start with
    (True, "STABLE PART\nquestion part"),   # the whole prompt: nothing would follow it
])
def test_otherwise_the_prompt_is_one_string_as_before(cache_prompts, prefix):
    assert _sent(cache_prompts, "STABLE PART\nquestion part", prefix)["messages"] == [
        {"role": "user", "content": "STABLE PART\nquestion part"}]


def test_each_call_carries_its_prompts_prefix():
    gw = ScriptedModelGateway(lambda prompt, cfg: {"ok": True})
    rendered = p.fill(p.Prompt("t", "1", "s", "Fixed text.\n{{question}}\n"), question="Q?")
    calls.ask(gw, "m", "t@1", lambda fb: rendered, lambda out: out)
    assert gw.calls[0][1].cache_prefix == "Fixed text.\n"


def test_the_flag_is_off_by_default_and_read_from_env():
    assert b.BudgetConfig.from_env(ENV).prompt_cache is False
    assert b.BudgetConfig.from_env({**ENV, "MODEL_PROMPT_CACHE": "1"}).prompt_cache is True
    with pytest.raises(b.BudgetConfigError, match="MODEL_PROMPT_CACHE"):
        b.BudgetConfig.from_env({**ENV, "MODEL_PROMPT_CACHE": "maybe"})


def test_with_the_cache_on_the_worst_case_prices_input_as_a_cache_write():
    """A cache write costs more than a plain input token: reserved at the write price, or twice the input
    price where none is set (T1), so the budget never undercounts."""
    off = b.BudgetConfig.from_env(ENV)
    on = b.BudgetConfig.from_env({**ENV, "MODEL_PROMPT_CACHE": "1"})
    priced = b.BudgetConfig.from_env({**ENV, "MODEL_PROMPT_CACHE": "1", "MODEL_PRICE_CACHE_WRITE": "2.5"})
    assert off.worst_cost(1_000_000, 0) == pytest.approx(2.0)
    assert on.worst_cost(1_000_000, 0) == pytest.approx(4.0)
    assert priced.worst_cost(1_000_000, 0) == pytest.approx(2.5)
    assert on.worst_cost(0, 1_000_000) == off.worst_cost(0, 1_000_000) == pytest.approx(10.0)


def test_the_real_gateway_passes_the_flag_to_litellm():
    import inspect
    assert "cache_prompts=cfg.prompt_cache" in inspect.getsource(b.real_gateway)
