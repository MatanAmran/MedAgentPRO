"""real_eval --record and --replay (chat 20.10): the calls go into the cache through the budgeted gateway,
a manifest says what made them, and a replay gives the same report with no model behind it. On the
scripted model and the fake provider only."""

import json
import sqlite3
from dataclasses import replace

import main
from model_client.replay import RecordReplayGateway
from tests.tools.test_real_eval import fake_real
from tools import real_eval as ev


def _run(db_path, tmp_path, *extra):
    return ev.main(["--set", "demo", "--only", "Q01,Q02", "--db", str(db_path), "--out", str(tmp_path / "out"),
                    "--cache", str(tmp_path / "cache"), *extra], build=lambda mode, db: main.build_deps(mode, db))


def test_eval_cache_stays_out_of_git():
    lines = (ev.REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "backend/eval_cache/" in lines


def test_a_recorded_run_replays_to_the_same_report(db_path, tmp_path, capsys):
    assert _run(db_path, tmp_path, "--record") == 0
    recorded = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    calls = list((tmp_path / "cache").glob("*-demo/*.json"))
    assert calls and "manifest" in capsys.readouterr().out
    assert _run(db_path, tmp_path, "--replay") == 0
    assert "none missing" in capsys.readouterr().out
    replayed = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    assert [r["response"] for r in replayed["results"]] == [r["response"] for r in recorded["results"]]
    assert replayed["mode"] == "replay"


def test_the_manifest_says_what_made_the_run_and_holds_no_question(db_path, tmp_path):
    assert _run(db_path, tmp_path, "--record", "--prompts", "generic") == 0
    path, = (tmp_path / "cache" / "manifests").glob("*-demo.json")
    m = json.loads(path.read_text(encoding="utf-8"))
    assert set(m) == {"run", "commit", "prompts", "output_caps", "request_settings", "model", "set", "started", "mode",
                    "exchanges", "calls", "spent_usd"}
    assert m["prompts"]["intent"] == "intent_analysis@16" and m["set"] == "demo" and m["exchanges"] == 2
    text = path.read_text(encoding="utf-8")
    assert not any(it["question"] in text for it in ev.demo_set()[:2])
    assert "S-0" not in text and "PO-" not in text


def test_a_replay_fails_on_a_call_not_stored(db_path, tmp_path, capsys):
    assert _run(db_path, tmp_path, "--record") == 0
    next((tmp_path / "cache").glob("*-demo/*.json")).unlink()
    capsys.readouterr()
    assert _run(db_path, tmp_path, "--replay") == 1
    assert "replay incomplete" in capsys.readouterr().out


def test_a_replay_with_nothing_recorded_on_these_prompts_is_refused(db_path, tmp_path, capsys):
    assert _run(db_path, tmp_path, "--record") == 0
    capsys.readouterr()
    assert _run(db_path, tmp_path, "--replay", "--prompts", "generic") == 1
    assert "no recorded run of demo on these prompts" in capsys.readouterr().out


def test_record_and_replay_together_or_replay_with_real_are_refused(db_path, tmp_path):
    assert _run(db_path, tmp_path, "--record", "--replay") == 2
    assert _run(db_path, tmp_path, "--replay", "--real") == 2


def test_recording_keeps_the_budget_in_force(db_path, tmp_path, monkeypatch):
    """Record mode wraps the budgeted gateway: the exchange is priced, its cap holds and the ledger counts."""
    deps, gateway_for = fake_real(db_path, tmp_path, monkeypatch)
    budget = deps.gateway
    recorder = RecordReplayGateway("record", tmp_path / "cache", budget)
    assert recorder.config is budget.config and hasattr(recorder, "exchange") and hasattr(recorder, "ensure_path")
    conn = sqlite3.connect(db_path)
    try:
        run = ev.run_set(ev.demo_set()[:1], replace(deps, gateway=recorder), lambda q: recorder, conn, mode="real")
    finally:
        conn.close()
    assert run.results[0].cost is not None and run.spent > 0
    assert recorder.recorded == len(run.results[0].calls) > 0
    assert list((tmp_path / "cache").glob("*.json"))


def test_a_replay_gateway_has_no_budget_to_reach():
    replaying = RecordReplayGateway("replay", ev.REPO / "nowhere")
    assert not hasattr(replaying, "exchange") and getattr(replaying, "ledger", None) is None


def test_the_bird_set_is_its_ten_questions_and_three_gaps():
    items = ev.bird_set()
    assert len(items) == 13 and all(i["user"] == "AP-01" for i in items)
    assert sum(i["expect"]["status"] == ["answered"] for i in items) == 10
    assert all(i["id"].startswith("B") for i in items)


def test_bird_runs_on_the_generic_prompts_only(db_path, tmp_path, capsys):
    assert ev.main(["--set", "bird", "--prompts", "frozen"]) == 2
    assert ev.main(["--set", "bird", "--plans"]) == 2
