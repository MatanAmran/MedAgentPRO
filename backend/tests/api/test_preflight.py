"""
The checks before a demo (task 9, review addition 3): each one passes on a ready setup and fails, saying
what to do, on a broken one. The HTTP goes to the app in process; no server, no model, no network.
"""

import shutil
import sqlite3

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
    assert any("examples: 12 listed" in c.text for c in checks)
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
    assert any("budget left: USD 5.0" in c.text for c in checks)
    assert provider.requests == []   # nothing was sent to the model


@pytest.mark.parametrize("drop", ["ANTHROPIC_API_KEY", "MODEL_BUDGET_USD"])
def test_an_incomplete_env_names_what_is_missing_and_never_a_value(drop):
    env = {k: v for k, v in CONFIG.items() if k != drop}
    c, cfg = pf.check_env(env)
    assert not c.ok and cfg is None and drop in c.text
    assert all(v not in c.text for k, v in CONFIG.items() if k != drop and len(v) > 4)


def test_a_missing_chain_key_fails():
    assert not pf.check_chain_key({}).ok and pf.check_chain_key({"MEDAGENTPRO_CHAIN_KEY": "k"}).ok
