"""
The checks before a demo (task 9, review addition 3): each one passes on a ready setup and fails, saying
what to do, on a broken one. The HTTP goes to the app in process; no server, no model, no network.
"""

import io
import shutil
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

import main
from tools import preflight as pf
from tools.fake_real_server import CONFIG, build_app

SCREEN_PAGE = (200, "<html><title>MedAgentPro</title></html>")


def fetch_for(app, screen=SCREEN_PAGE):
    client = TestClient(app)

    def fetch(method, url, headers, body):
        if url.startswith(pf.SCREEN):
            return screen
        path = url[len(pf.API):]
        r = client.request(method, path, headers=headers, content=body)
        return r.status_code, r.text
    return fetch


def test_scripted_setup_passes_every_check(db_path):
    checks = pf.run("scripted", fetch_for(main.create_app("scripted", db_path)), db_path=db_path)
    assert all(c.ok for c in checks), [str(c) for c in checks]
    assert any("examples: 14 listed" in c.text for c in checks)
    assert any("runs end to end" in c.text for c in checks)


def test_nothing_running_fails_and_says_what_to_start(db_path):
    checks = pf.run("scripted", lambda *a: (0, ""), db_path=db_path)
    failed = [c.text for c in checks if not c.ok]
    assert any("python -m main" in t for t in failed) and any("npm run start" in t for t in failed)


def test_a_server_on_the_wrong_model_fails(db_path):
    checks = pf.run("real", fetch_for(main.create_app("scripted", db_path)), env=dict(CONFIG), db_path=db_path)
    assert any(not c.ok and "runs the scripted model" in c.text for c in checks)


def test_a_database_missing_a_column_fails(db_path, tmp_path):
    broken = tmp_path / "demo.db"
    shutil.copy(db_path, broken)
    conn = sqlite3.connect(broken)
    conn.execute("ALTER TABLE commitments DROP COLUMN delay_reason")
    conn.commit()
    conn.close()
    c = pf.check_database(broken)
    assert not c.ok and "commitments.delay_reason" in c.text
    assert not pf.check_database(tmp_path / "none.db").ok


def test_real_setup_checks_env_map_key_and_budget_without_asking_the_model(db_path, tmp_path):
    app, provider = build_app(db_path, tmp_path)
    env = {**CONFIG, "MEDAGENTPRO_CHAIN_KEY": "k"}
    checks = pf.run("real", fetch_for(app), env=env, db_path=db_path)
    assert all(c.ok for c in checks), [str(c) for c in checks]
    left = next(c for c in checks if "worst-case exchanges left" in c.text)
    assert "33" in left.text and not left.warn   # 5 / 0.148, the fake run's caps
    assert all("5.0" not in c.text and "0.148" not in c.text for c in checks)   # a count, never an amount
    assert provider.requests == []   # nothing was sent to the model


@pytest.mark.parametrize("drop", ["ANTHROPIC_API_KEY", "MODEL_BUDGET_USD"])
def test_an_incomplete_env_names_what_is_missing_and_never_a_value(drop):
    env = {k: v for k, v in CONFIG.items() if k != drop}
    c, cfg = pf.check_env(env)
    assert not c.ok and cfg is None and drop in c.text
    assert all(v not in c.text for k, v in CONFIG.items() if k != drop and len(v) > 4)


def test_a_missing_chain_key_fails():
    assert not pf.check_chain_key({}).ok and pf.check_chain_key({"MEDAGENTPRO_CHAIN_KEY": "k"}).ok


# T12 on the configuration in use (chat, R9, 14.10, 17:50): exchanges at their whole cap that still fit.
CAP = type("Cfg", (), {"max_usd_per_exchange": 0.25})()


@pytest.mark.parametrize("left, ok, warn, says", [
    (0.2, False, False, "Not even one exchange fits"),
    (2.0, True, True, "8: enough for a smoke test, not for a demo"),
    (3.75, True, False, "left in the budget: 15"),
])
def test_worst_case_exchanges_left_fail_warn_or_pass(left, ok, warn, says):
    c = pf.check_worst_case_left(left, CAP)
    assert (c.ok, c.warn) == (ok, warn) and says in c.text
    assert str(left) not in c.text and "0.25" not in c.text
    assert str(c).startswith("❌" if not ok else "⚠️" if warn else "✅")


def test_an_unreported_budget_fails():
    assert not pf.check_worst_case_left(None, CAP).ok


def test_the_marks_print_on_a_console_without_them(monkeypatch):
    """R9: printed through a pipe on a cp1255 console, ✅ raised UnicodeEncodeError before the fix."""
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1255"))
    monkeypatch.setattr(pf, "run", lambda mode: [pf.Check(True, "a"), pf.Check(True, "b", warn=True),
                                                 pf.Check(False, "c")])
    assert pf.main([]) == 1
    sys.stdout.flush()
    out = raw.getvalue().decode("utf-8")
    assert "✅ a" in out and "⚠️ b" in out and "❌ c" in out


@pytest.mark.parametrize("intent, says", [("", "intent analysis: the same"), ("3000", "intent analysis: 3000 tokens")])
def test_the_output_caps_are_shown_as_numbers(intent, says):
    """Task 10a (chat 23:35): preflight shows the intent cap, a number only."""
    _, cfg = pf.check_env({**CONFIG, "MODEL_MAX_OUTPUT_TOKENS_INTENT": intent})
    c = pf.check_output_caps(cfg)
    assert c.ok and "output cap per call: 2000 tokens" in c.text and says in c.text


@pytest.mark.parametrize("gate, says", [("", "the gate: the same"), ("4000", "the gate: 4000 tokens")])
def test_the_gate_cap_is_shown_as_a_number(gate, says):
    """Chat, 18.10, 13:50: preflight shows the gate's cap, a number only."""
    _, cfg = pf.check_env({**CONFIG, "MODEL_MAX_OUTPUT_TOKENS_GATE": gate})
    assert says in pf.check_output_caps(cfg).text


def test_the_real_setup_shows_the_output_caps(db_path, tmp_path):
    app, _ = build_app(db_path, tmp_path)
    checks = pf.run("real", fetch_for(app), env={**CONFIG, "MEDAGENTPRO_CHAIN_KEY": "k",
                                                  "MODEL_MAX_OUTPUT_TOKENS_INTENT": "3000"}, db_path=db_path)
    assert any("intent analysis: 3000 tokens" in c.text for c in checks)
