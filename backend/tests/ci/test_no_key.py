"""In CI there is no API key at all (classic test set, item 7; book 6.1). The workflow checks it as its
first step; this checks it again from inside the run that the tests make."""

import os

import pytest


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="checked in CI only")
def test_no_api_key_in_ci():
    assert "ANTHROPIC_API_KEY" not in os.environ, "ANTHROPIC_API_KEY must not exist in CI"


def test_the_workflow_sets_no_secret():
    """The workflow file names no secret and sets no key, whatever the environment it runs in."""
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "secrets." not in workflow and "ANTHROPIC_API_KEY:" not in workflow
