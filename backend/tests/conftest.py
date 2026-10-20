"""Shared test setup.

LiteLLM uses the map shipped with the pinned version, never one downloaded on import (task 5, H1),
whichever module a test imports first.

Property tests (marker "property") run in the ordinary run with hypothesis's default number of
examples. Under -m stress they run again, with the "stress" profile: many more examples, no deadline
(classic test set, item 4).

The fixtures shared across test files live here, so no test file imports them from another.

Task 4, A (chat 20.10, 22:30): schema 1 requires "asked" on every reading. The intent outputs the tests wrote
by hand before it have none, so the fixture whole_question_asked gives each such reading the whole question as
posed, the widest span there is, which covers every word. It acts only where "asked" is absent, at the two
places an intent output enters: what any test gateway returns for an intent prompt (wrapped in calls.ask),
and compile_intent called directly. The data files (the demo, the sets, BIRD) carry their own "asked", and the
tests of A itself are marked asked_as_written, which turns the fixture off.
"""

import json
import os
import sys
from dataclasses import replace

import pytest
from hypothesis import HealthCheck, settings

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from demo_data import seed  # noqa: E402  (after the cost-map switch: imports reach LiteLLM)
from model_client import budget as b  # noqa: E402

settings.register_profile("stress", max_examples=2000, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])


def _stress_requested(config) -> bool:
    expr = config.getoption("-m") or ""
    return "stress" in expr and "not stress" not in expr


def pytest_configure(config):
    if _stress_requested(config):
        settings.load_profile("stress")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Under -m stress, property tests are selected as well, before the marker filter runs."""
    if _stress_requested(config):
        for item in items:
            if item.get_closest_marker("property"):
                item.add_marker(pytest.mark.stress)


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    """A freshly seeded demo store, one per test module."""
    path = tmp_path_factory.mktemp("demo") / "demo.db"
    seed.build(path)
    return path


@pytest.fixture
def ledger(tmp_path):
    """An empty spend ledger in the test's own directory."""
    lg = b.SpendLedger(tmp_path / "spend.json")
    yield lg
    lg.close()


def _with_whole_question(intent, question: str):
    if not isinstance(intent, dict) or not isinstance(intent.get("readings"), list):
        return intent
    if all(not isinstance(r, dict) or "asked" in r for r in intent["readings"]):
        return intent
    return {**intent, "readings": [{**r, "asked": question} if isinstance(r, dict) and "asked" not in r else r
                                   for r in intent["readings"]]}


def _posed(prompt: str) -> str | None:
    """The question as posed, from the last question block of an intent prompt (prompts.question_block)."""
    lines = str(prompt).splitlines()
    for key in ("Question as posed: ", "Question: "):
        found = [ln[len(key):] for ln in lines if ln.startswith(key)]
        if found:
            return found[-1]
    return None


@pytest.fixture(autouse=True)
def whole_question_asked(request, monkeypatch):
    """See the module docstring. Off under @pytest.mark.asked_as_written."""
    if request.node.get_closest_marker("asked_as_written"):
        return
    from query_assistance import calls, compiler

    class Filled:
        """The test's gateway, with each reading of an intent output given the whole question."""

        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def invoke(self, prompt, config):
            resp = self.inner.invoke(prompt, config)
            if not str(config.prompt_identity or "").startswith("intent_analysis") or not getattr(resp, "text", None):
                return resp
            posed = _posed(prompt)
            try:
                out = json.loads(resp.text)
            except ValueError:
                return resp
            new = _with_whole_question(out, posed) if posed else out
            return resp if new is out else replace(resp, text=json.dumps(new))

    ask = calls.ask
    monkeypatch.setattr(calls, "ask", lambda gateway, *a, **k: ask(Filled(gateway), *a, **k))
    original = compiler.compile_intent

    def filled_compile(intent, question, *args, **kwargs):
        posed = question if isinstance(question, str) else question.question
        return original(_with_whole_question(intent, posed), question, *args, **kwargs)

    for module in list(sys.modules.values()):
        if module is not None and getattr(module, "compile_intent", None) is original:
            monkeypatch.setattr(module, "compile_intent", filled_compile)
