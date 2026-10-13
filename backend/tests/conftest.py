"""Shared test setup.

LiteLLM uses the map shipped with the pinned version, never one downloaded on import (task 5, H1),
whichever module a test imports first.

Property tests (marker "property") run in the ordinary run with hypothesis's default number of
examples. Under -m stress they run again, with the "stress" profile: many more examples, no deadline
(classic test set, item 4).
"""

import os

import pytest
from hypothesis import HealthCheck, settings

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

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
