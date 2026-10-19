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


def test_the_real_gateway_is_never_built_in_ci(monkeypatch, tmp_path):
    """Phase B (chat 19.10): with CI set, the real gateway refuses to build even where pytest is not loaded
    (a script run in CI, outside the tests), and before it reads any value or touches the spend file."""
    import sys

    from model_client import budget as b
    monkeypatch.setenv("CI", "true")
    monkeypatch.delitem(sys.modules, "pytest")
    with pytest.raises(b.BudgetConfigError, match="never built in CI"):
        b.real_gateway({}, tmp_path / "spend.json")
    assert list(tmp_path.iterdir()) == []


def test_mypy_blocks_in_the_workflow():
    """mypy is at zero errors (Phase B, 19.10): the workflow step no longer continues on error."""
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    step = workflow.split("python -m mypy")[0].rsplit("- name:", 1)[1]
    assert "continue-on-error" not in step
