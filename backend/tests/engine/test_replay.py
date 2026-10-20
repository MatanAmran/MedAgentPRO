"""
Record and replay of model calls (Phase B, task 1; chat 19.10). The scripted model only: no test here
calls a real one. Record wraps a gateway and stores each call; replay wraps none and serves the store,
so a call it does not hold fails loudly and nothing is sent.
"""

import json

import pytest

from demo_data import seed
from demo_data.scripted import ScriptedDemoGateway, load_scripts
from model_client.client import ModelCallError, ModelConfig, ScriptedModelGateway
from model_client.replay import RecordReplayGateway, ReplayMiss, request_key
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, MemoryStore

CFG = ModelConfig("claude-haiku-5-5", max_tokens=100, output_schema={"type": "object"},
                  prompt_identity="synthesis@11")
CAT = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}
Q1 = "Which suppliers were late in weeks 1 to 13?"


def test_a_recorded_call_is_served_on_replay_without_the_model(tmp_path):
    inner = ScriptedModelGateway([{"answer": 1}])
    rec = RecordReplayGateway("record", tmp_path, inner)
    first = rec.invoke("the prompt", CFG)
    replay = RecordReplayGateway("replay", tmp_path)
    again = replay.invoke("the prompt", CFG)
    assert again == first and again.text == '{"answer": 1}'
    assert len(inner.calls) == 1 and rec.recorded == 1 and replay.served == 1


def test_a_miss_fails_loudly_and_is_listed(tmp_path):
    replay = RecordReplayGateway("replay", tmp_path)
    with pytest.raises(ReplayMiss, match="synthesis@11"):
        replay.invoke("never recorded", CFG)
    assert len(replay.misses) == 1
    with pytest.raises(ReplayMiss, match="1 call"):
        replay.assert_complete()


def test_replay_wraps_no_gateway_so_it_can_never_reach_one(tmp_path):
    with pytest.raises(ValueError, match="wraps no gateway"):
        RecordReplayGateway("replay", tmp_path, ScriptedModelGateway([]))
    with pytest.raises(ValueError, match="needs the gateway"):
        RecordReplayGateway("record", tmp_path)
    with pytest.raises(ValueError):
        RecordReplayGateway("live", tmp_path)


@pytest.mark.parametrize("change", [
    {"prompt_identity": "synthesis@12"}, {"model": "claude-sonnet-5-5"}, {"output_schema": {"type": "array"}},
])
def test_the_key_follows_the_prompt_version_the_model_and_the_schema(change):
    other = ModelConfig(**{**CFG.__dict__, **change})
    assert request_key("p", other) != request_key("p", CFG)


def test_the_key_follows_the_messages_and_the_output_cap():
    """Chat 20.10, 14:50: the cap is part of what decides the answer. An answer cut at 2000 tokens must be a
    miss for a run at 4000, never served to it."""
    assert request_key("p", CFG) != request_key("q", CFG)
    assert request_key("p", ModelConfig(**{**CFG.__dict__, "max_tokens": 3000})) != request_key("p", CFG)
    assert request_key("p", CFG, 4000) != request_key("p", CFG, 2000)


def test_a_call_recorded_behind_the_budget_is_keyed_and_stored_at_the_cap_it_was_sent(tmp_path):
    """The budget sets max_tokens after the record layer: the layer asks the budget's config for the cap,
    so the key and the record hold the cap sent, not the None the pipeline passes."""
    from model_client.budget import BudgetConfig

    class Budget(ScriptedModelGateway):
        config = BudgetConfig("m", 1, 1, 1, 1, 2000, 10, max_output_tokens_synthesis=4000)

    plain = ModelConfig("claude-haiku-5-5", prompt_identity="synthesis@11")
    RecordReplayGateway("record", tmp_path, Budget(["{}"])).invoke("the prompt", plain)
    (saved,) = tmp_path.glob("*.json")
    record = json.loads(saved.read_text(encoding="utf-8"))
    assert record["request"]["max_tokens"] == 4000
    assert record["key"] == request_key("the prompt", plain, 4000)
    at_old_cap = RecordReplayGateway("replay", tmp_path, output_cap=lambda ident: 2000)
    with pytest.raises(ReplayMiss):
        at_old_cap.invoke("the prompt", plain)
    at_its_cap = RecordReplayGateway("replay", tmp_path, output_cap=lambda ident: 4000)
    assert at_its_cap.invoke("the prompt", plain).text == "{}"


def test_a_failed_call_is_raised_and_not_stored(tmp_path):
    rec = RecordReplayGateway("record", tmp_path, ScriptedModelGateway([ModelCallError("timeout", "slow")]))
    with pytest.raises(ModelCallError):
        rec.invoke("the prompt", CFG)
    assert list(tmp_path.iterdir()) == []


def test_the_store_holds_the_request_and_the_response(tmp_path):
    RecordReplayGateway("record", tmp_path, ScriptedModelGateway(["{}"])).invoke("the prompt", CFG)
    (saved,) = tmp_path.glob("*.json")
    record = json.loads(saved.read_text(encoding="utf-8"))
    assert record["key"] == saved.stem == request_key("the prompt", CFG)
    assert record["request"]["prompt_identity"] == "synthesis@11" and record["request"]["max_tokens"] == 100
    assert record["response"]["text"] == "{}"


def _deps(gateway):
    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    return pl.Deps(gateway, "claude-haiku-5-5", CAT, pl.connect_readonly(seed.DEFAULT_OUT), audit,
                   p.load(seed.HERE / "intent_analysis.md"))


def test_a_whole_exchange_replays_as_it_was_recorded(tmp_path, db_path):
    """Recorded over the scripted demo model, then replayed with no model at all: the same response."""
    rec = RecordReplayGateway("record", tmp_path, ScriptedDemoGateway(Q1, load_scripts()))
    deps = _deps(rec)
    deps.connect = pl.connect_readonly(db_path)
    first = pl.run(Q1, [], APPROVER, deps)
    replay = RecordReplayGateway("replay", tmp_path)
    deps2 = _deps(replay)
    deps2.connect = pl.connect_readonly(db_path)
    again = pl.run(Q1, [], APPROVER, deps2)
    assert first.response["outcome"] == "answered" and again.response == first.response
    assert replay.served == rec.recorded >= 3 and replay.misses == []


def test_a_changed_prompt_misses_and_the_exchange_is_not_answered(tmp_path, db_path):
    """A new intent prompt version is a new key: replay serves nothing, ends the exchange, and says so."""
    deps = _deps(RecordReplayGateway("record", tmp_path, ScriptedDemoGateway(Q1, load_scripts())))
    deps.connect = pl.connect_readonly(db_path)
    pl.run(Q1, [], APPROVER, deps)
    replay = RecordReplayGateway("replay", tmp_path)
    deps2 = _deps(replay)
    deps2.connect = pl.connect_readonly(db_path)
    newer = deps2.intent_prompt
    deps2.intent_prompt = type(newer)(newer.name, str(int(newer.version) + 1), newer.output_schema, newer.body)
    out = pl.run(Q1, [], APPROVER, deps2)
    assert out.response["outcome"] == "request_not_completed"
    with pytest.raises(ReplayMiss):
        replay.assert_complete()
