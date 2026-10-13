"""
Golden tests: what the system puts out, compared with a reviewed snapshot. Run:  pytest -q

A change to a response, a rendered prompt or the API contract fails here with a diff. A prompt change
is a change in cost and behaviour, so it is seen. Snapshots are rewritten only with UPDATE_GOLDEN=1,
and the diff is printed for review. Fields that vary between runs (ids, times) are never captured.
No test reaches a real model (6.1).
"""

import difflib
import json
import os
from pathlib import Path

import pytest

import main
from demo_data import seed
from demo_data.scripted import ScriptedDemoGateway, load_scripts
from model_client.client import ModelConfig, ModelGateway, ModelResponse
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, MemoryStore

SNAPSHOTS = Path(__file__).parent / "snapshots"
CAT = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
USERS = {"AP-01": {"role": "APPROVER", "user_id": "AP-01"}, "PL-02": {"role": "PLANNER", "user_id": "PL-02"}}
INTENT_PROMPT = p.load(seed.HERE / "intent_analysis.md")
FIRST = json.loads((seed.HERE / "scripted_exchanges.json").read_text(encoding="utf-8"))["exchanges"][0]["question"]


def golden(name: str, actual: str):
    """Compare with snapshots/<name>, or rewrite it under UPDATE_GOLDEN=1 and print the diff."""
    path = SNAPSHOTS / name
    expected = path.read_text(encoding="utf-8") if path.exists() else ""
    if actual == expected:
        return
    diff = "".join(difflib.unified_diff(expected.splitlines(keepends=True), actual.splitlines(keepends=True),
                                        f"snapshots/{name}", "actual"))
    if os.environ.get("UPDATE_GOLDEN") == "1":
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
        print(f"\nUPDATE_GOLDEN: rewrote snapshots/{name}\n{diff}")
        return
    pytest.fail(f"snapshots/{name} differs. Review the diff, then rerun with UPDATE_GOLDEN=1 if it is "
                f"intended:\n{diff}", pytrace=False)


def as_json(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


class _Recording(ModelGateway):
    def __init__(self, inner):
        self.inner, self.prompts = inner, {}

    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse:
        self.prompts.setdefault(config.prompt_identity, prompt)
        return self.inner.invoke(prompt, config)


def run(question, user, db_path):
    gw = _Recording(ScriptedDemoGateway(question, load_scripts()))
    d = pl.Deps(gw, "scripted-demo", CAT, pl.connect_readonly(db_path),
                AuditRepository(MemoryStorage(), CircuitBreaker("s", MemoryStore()), b"k"), INTENT_PROMPT)
    return pl.run(question, [], user, d), gw


def _slug(question: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in question.lower()).strip("_")[:50]


# --------------------------------------------------------------------------
# responses: every scripted question, both roles
# --------------------------------------------------------------------------

@pytest.mark.parametrize("user", list(USERS))
@pytest.mark.parametrize("question", list(load_scripts()), ids=_slug)
def test_response(question, user, db_path):
    """exchange_response as presented, "Examined" included. It holds no id or time by design."""
    out, _ = run(question, USERS[user], db_path)
    golden(f"response__{_slug(question)}__{user}.json", as_json(out.response))


# --------------------------------------------------------------------------
# rendered prompts
# --------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["APPROVER", "PLANNER"])
def test_intent_prompt(role):
    """The schema the model sees differs by role (prompts.visible_tables)."""
    golden(f"prompt__intent__{role}.txt", p.render_intent(CAT, role, FIRST, None, INTENT_PROMPT))


@pytest.mark.parametrize("stage", ["synthesis", "support_scoring"])
def test_synthesis_and_gate_prompts(stage, db_path):
    """As sent in a run of the first demo question, so the records and the numbered answer are real."""
    _, gw = run(FIRST, USERS["AP-01"], db_path)
    sent = next(pr for ident, pr in gw.prompts.items() if ident.split("@")[0] == stage)
    golden(f"prompt__{stage}.txt", sent)


# --------------------------------------------------------------------------
# the API contract the screen (task 7) relies on
# --------------------------------------------------------------------------

def test_openapi(db_path):
    golden("openapi.json", as_json(main.create_app("scripted", db_path).openapi()))
