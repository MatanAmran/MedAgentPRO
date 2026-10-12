"""
Tests for circuit_breaker.py, model_gateway.py and audit.py. Run:  pytest -q
"""

import logging
from types import SimpleNamespace

import pytest

from persistence import audit_repository as A
from shared import circuit_breaker as cb
from model_client import client as mg


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


@pytest.fixture
def clock():
    return Clock()


def breaker(clock, store=None, **kw):
    return cb.CircuitBreaker("mongo", store or cb.MemoryStore(clock), threshold=3, cooldown=10, clock=clock, **kw)


def boom():
    raise ConnectionError("down")


# --------------------------------------------------------------------------
# circuit breaker, NFR 9 and 7.6
# --------------------------------------------------------------------------

def test_opens_only_after_threshold(clock):
    b = breaker(clock)
    for _ in range(2):
        with pytest.raises(ConnectionError):
            b.call(boom)
    assert b.get_state() == cb.CLOSED and b.failure_count == 2
    with pytest.raises(ConnectionError):
        b.call(boom)
    assert b.get_state() == cb.OPEN


def test_success_resets_the_count(clock):
    b = breaker(clock)
    with pytest.raises(ConnectionError):
        b.call(boom)
    assert b.call(lambda: 7) == 7 and b.failure_count == 0


def test_open_fails_fast_without_calling(clock):
    b = breaker(clock)
    for _ in range(3):
        with pytest.raises(ConnectionError):
            b.call(boom)
    called = []
    with pytest.raises(cb.CircuitOpen):
        b.call(lambda: called.append(1))
    assert called == []


def test_failures_count_across_workers(clock):
    store = cb.MemoryStore(clock)
    w1, w2 = breaker(clock, store), breaker(clock, store)
    for w in (w1, w2, w1):
        with pytest.raises(ConnectionError):
            w.call(boom)
    assert w2.get_state() == cb.OPEN


def test_one_probe_after_cooldown(clock):
    store = cb.MemoryStore(clock)
    w1, w2 = breaker(clock, store), breaker(clock, store)
    for _ in range(3):
        with pytest.raises(ConnectionError):
            w1.call(boom)
    clock.t += 10
    assert w1.get_state() == cb.HALF_OPEN
    store.set_if_absent(w1.k_probe, "1", 10)           # w1 holds the probe
    with pytest.raises(cb.CircuitOpen):
        w2.call(lambda: 1)                              # w2 is refused, not let through


def test_failed_probe_reopens(clock):
    b = breaker(clock)
    for _ in range(3):
        with pytest.raises(ConnectionError):
            b.call(boom)
    clock.t += 10
    with pytest.raises(ConnectionError):
        b.call(boom)
    assert b.get_state() == cb.OPEN


def test_successful_probe_closes(clock):
    b = breaker(clock)
    for _ in range(3):
        with pytest.raises(ConnectionError):
            b.call(boom)
    clock.t += 10
    assert b.call(lambda: "ok") == "ok" and b.get_state() == cb.CLOSED


def test_dependencies_are_tracked_separately(clock):
    store = cb.MemoryStore(clock)
    db = cb.CircuitBreaker("mongo", store, threshold=1, cooldown=10, clock=clock)
    model = cb.CircuitBreaker("model", store, threshold=1, cooldown=10, clock=clock)
    with pytest.raises(ConnectionError):
        db.call(boom)
    assert db.get_state() == cb.OPEN and model.get_state() == cb.CLOSED


def test_transitions_logged_outside_the_database(clock, caplog):
    b = breaker(clock)
    with caplog.at_level(logging.WARNING, logger="medagentpro.breaker"):
        for _ in range(3):
            with pytest.raises(ConnectionError):
                b.call(boom)
    assert any("state -> open" in r.message for r in caplog.records)


# --------------------------------------------------------------------------
# model gateway, 3.1.4 and 6.1
# --------------------------------------------------------------------------

CFG = mg.ModelConfig("test-model", prompt_identity="intent_analysis@1")


def test_scripted_returns_in_order_and_counts_calls():
    g = mg.ScriptedModelGateway([{"a": 1}, "plain"])
    assert g.invoke("p1", CFG).json() == {"a": 1}
    r = g.invoke("p2", CFG)
    assert r.text == "plain" and len(g.calls) == 2
    assert r.prompt_identity == "intent_analysis@1" and r.input_tokens and r.output_tokens


def test_scripted_can_fail():
    g = mg.ScriptedModelGateway([TimeoutError("slow")])
    with pytest.raises(TimeoutError):
        g.invoke("p", CFG)


def fake_completion(**kw):
    fake_completion.kw = kw
    return SimpleNamespace(model="provider/x", usage={"prompt_tokens": 120, "completion_tokens": 30},
                           choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])


def test_litellm_records_model_and_tokens(clock):
    g = mg.LiteLLMGateway(breaker(clock), completion=fake_completion)
    r = g.invoke("prompt", mg.ModelConfig("x", output_schema={"type": "object"}, prompt_identity="support_scoring@1"))
    assert (r.model, r.input_tokens, r.output_tokens) == ("provider/x", 120, 30)
    assert fake_completion.kw["response_format"]["json_schema"]["name"] == "support_scoring"


def test_litellm_goes_through_the_breaker(clock):
    b = breaker(clock)
    g = mg.LiteLLMGateway(b, completion=lambda **kw: boom())
    for _ in range(3):
        with pytest.raises(ConnectionError):
            g.invoke("p", CFG)
    with pytest.raises(cb.CircuitOpen):
        g.invoke("p", CFG)


# --------------------------------------------------------------------------
# AuditRepository, NFR 1, 5, 9 and 7.9
# --------------------------------------------------------------------------

KEY = b"test-key-from-environment"


def repo(clock, storage=None, **kw):
    return A.AuditRepository(storage or A.MemoryStorage(), breaker(clock), KEY, **kw)


def entry(i, **kw):
    d = dict(investigation_id="INV-1", step_ordinal=i, agent=f"stage{i}", event_type="completed", role="SYSTEM")
    d.update(kw)
    return A.AuditEntry(**d)


def test_three_writes_land_together(clock):
    st = A.MemoryStorage()
    r = repo(clock, st)
    r.write_event(entry(1, status_after="IN_PROGRESS"), {"domain": "supply_chain"}, step_output={"rows": 3})
    assert len(st.audit_log) == 1
    assert st.investigations["INV-1"]["status"] == "IN_PROGRESS" and st.investigations["INV-1"]["domain"] == "supply_chain"
    assert r.completed_output("INV-1", 1) == {"rows": 3}


def test_failure_inside_leaves_nothing(clock):
    st = A.MemoryStorage()
    st.put_step_output = lambda *a: (_ for _ in ()).throw(IOError("disk"))
    r = repo(clock, st)
    with pytest.raises(IOError):
        r.write_event(entry(1), step_output={"x": 1})
    assert st.audit_log == [] and st.investigations == {}


def test_open_circuit_writes_nothing(clock):
    st = A.MemoryStorage()
    r = repo(clock, st)
    for _ in range(3):
        r.breaker.record_failure()
    with pytest.raises(cb.CircuitOpen):
        r.write_event(entry(1))
    assert st.audit_log == []


def test_write_run_lands_every_entry_and_the_record_together(clock):
    st = A.MemoryStorage()
    r = repo(clock, st)
    written = r.write_run([entry(1), entry(2), entry(3)], {"outcome": "answered"})
    assert [w["seq"] for w in written] == [1, 2, 3] and len(st.audit_log) == 3
    assert st.investigations["INV-1"]["outcome"] == "answered"
    assert r.verify_chain("INV-1") == 3
    r.write_event(entry(4, event_type="answer_rated"), {"rating": "up"})
    assert r.verify_chain("INV-1") == 4 and st.investigations["INV-1"]["rating"] == "up"


def test_write_run_failure_leaves_nothing(clock):
    st = A.MemoryStorage()
    original = st.append_audit
    calls = []

    def flaky(record, tx):
        calls.append(1)
        if len(calls) == 2:
            raise IOError("disk")
        original(record, tx)
    st.append_audit = flaky
    with pytest.raises(IOError):
        repo(clock, st).write_run([entry(1), entry(2)], {"outcome": "answered"})
    assert st.audit_log == [] and st.investigations == {}


def test_write_run_takes_entries_of_one_run(clock):
    with pytest.raises(ValueError):
        repo(clock).write_run([entry(1), entry(2, investigation_id="INV-2")])


def test_chain_verifies(clock):
    r = repo(clock)
    for i in range(1, 5):
        r.write_event(entry(i))
    assert r.verify_chain("INV-1") == 4


def test_edit_in_the_database_is_detected(clock):
    st = A.MemoryStorage()
    r = repo(clock, st)
    for i in range(1, 4):
        r.write_event(entry(i, confidence=0.5))
    st.audit_log[1]["confidence"] = 0.95
    with pytest.raises(A.ChainBroken, match="entry 2 altered"):
        r.verify_chain("INV-1")


def test_removing_the_last_entry_is_detected(clock):
    st = A.MemoryStorage()
    r = repo(clock, st)
    for i in range(1, 4):
        r.write_event(entry(i))
    st.audit_log.pop()
    with pytest.raises(A.ChainBroken, match="head"):
        r.verify_chain("INV-1")


def test_recomputing_without_the_key_fails(clock):
    st = A.MemoryStorage()
    r = repo(clock, st)
    for i in range(1, 3):
        r.write_event(entry(i))
    forged = A.AuditRepository(st, breaker(clock), b"wrong-key")
    with pytest.raises(A.ChainBroken):
        forged.verify_chain("INV-1")


def test_chains_are_per_investigation(clock):
    r = repo(clock)
    r.write_event(entry(1))
    r.write_event(entry(1, investigation_id="INV-2"))
    r.write_event(entry(2))
    assert r.verify_chain("INV-1") == 2 and r.verify_chain("INV-2") == 1


def test_repeated_attempt_must_say_why(clock):
    r = repo(clock)
    with pytest.raises(ValueError):
        r.write_event(entry(1, attempt_number=2))
    r.write_event(entry(1, attempt_number=2, superseded_reason="Planner requested reinvestigation"))


def test_absent_fields_are_not_written(clock):
    st = A.MemoryStorage()
    repo(clock, st).write_event(entry(1))
    assert "input_tokens" not in st.audit_log[0] and "summary_produced" not in st.audit_log[0]


def test_notification_hook_runs_after_commit(clock):
    seen = []
    r = repo(clock, on_written=lambda rec: seen.append(rec["seq"]))
    r.write_event(entry(1))
    assert seen == [1]


def test_key_is_required(clock):
    with pytest.raises(ValueError):
        A.AuditRepository(A.MemoryStorage(), breaker(clock), b"")
