"""Shared test setup.

LiteLLM uses the map shipped with the pinned version, never one downloaded on import (task 5, H1),
whichever module a test imports first.

Property tests (marker "property") run in the ordinary run with hypothesis's default number of
examples. Under -m stress they run again, with the "stress" profile: many more examples, no deadline
(classic test set, item 4).

The fixtures shared across test files live here, so no test file imports them from another.
"""

import os

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
