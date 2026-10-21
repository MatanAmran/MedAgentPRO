"""
MedAgentPro - the checks before a demo (task 9, review addition 3). No model call, ever.

    python -m tools.preflight            # the scripted demo: server and screen already running
    python -m tools.preflight --real     # the real model: also .env, LiteLLM's map, and the budget left

Prints one line per check, ✅, ⚠️ or ❌, and exits non-zero if any failed (a warning does not fail).
On the real model nothing is asked of the model: the question check runs on the scripted model only.
.env values are never printed, only the names of those missing. The budget left is read from the
running server (/api/health), not from the spend file, which the server holds locked (T2), and shown
only as how many exchanges at their whole cap still fit (T12, moved here from the feasibility tests:
chat, R9, 14.10, 17:50), never as an amount.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from demo_data import seed
from demo_data.scripted import load_scripts

API = "http://127.0.0.1:8000"
# T12 on the configuration in use: the 12 demo questions with a margin, each spending its whole cap.
DEMO_WORST_CASE_EXCHANGES = 15
SCREEN = "http://localhost:3000"
# the cheapest scripted question end to end: retrieval and the contract, no synthesis (Test 35)
PROBE = "What did S-040 supply?"

# fetch(method, url, headers, body) -> (status, text); status 0 where nothing answered
Fetch = Callable[[str, str, dict, bytes | None], tuple[int, str]]


def http_fetch(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, str]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        # bandit B310: fixed local URLs only (API, SCREEN), never one from input
        with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return 0, ""


@dataclass
class Check:
    ok: bool
    text: str
    warn: bool = False   # passes, but says what it is not enough for

    def __str__(self) -> str:
        return f"{'❌' if not self.ok else '⚠️' if self.warn else '✅'} {self.text}"


def check_database(db_path: Path = seed.DEFAULT_OUT) -> Check:
    """demo.db exists and holds every table and column of demo_data/schema.json."""
    if not Path(db_path).exists():
        return Check(False, f"demo database missing ({Path(db_path).name}): python -m demo_data.seed")
    schema = json.loads((seed.HERE / "schema.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        missing = []
        for table, entity in schema["entities"].items():
            held = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            missing += [f"{table}.{c}" for c in entity["columns"] if c not in held]
    finally:
        conn.close()
    if missing:
        return Check(False, f"demo database does not match the schema: missing {', '.join(missing[:5])}; "
                            "rebuild it: python -m demo_data.seed")
    return Check(True, "demo database matches the schema")


def check_server(fetch: Fetch, mode: str, api: str = API) -> tuple[Check, dict]:
    status, text = fetch("GET", f"{api}/api/health", {}, None)
    if status != 200:
        start = "python -m main --real" if mode == "real" else "python -m main"
        return Check(False, f"chat server not answering at {api} ({start})"), {}
    health = json.loads(text)
    if health.get("mode") != mode:
        return Check(False, f"chat server runs the {health.get('mode')} model, not the {mode} one"), health
    return Check(True, f"chat server answers, {mode} model ({health.get('model')})"), health


def check_screen(fetch: Fetch, screen: str = SCREEN) -> Check:
    status, text = fetch("GET", f"{screen}/", {}, None)
    if status != 200 or "MedAgentPro" not in text:
        return Check(False, f"screen not answering at {screen} (npm run build; npm run start)")
    return Check(True, "screen answers")


def check_examples(fetch: Fetch, mode: str, api: str = API) -> Check:
    status, text = fetch("GET", f"{api}/api/examples", {}, None)
    if status != 200:
        return Check(False, "examples not listed")
    listed = json.loads(text)
    want = len(load_scripts()) if mode == "scripted" else 0
    if len(listed) != want:
        return Check(False, f"examples: {len(listed)} listed, {want} expected")
    return Check(True, f"examples: {want} listed" if want else "examples: none on the real model, as expected")


def check_question(fetch: Fetch, api: str = API) -> Check:
    """One scripted question end to end, on a conversation of its own."""
    user = {"X-Demo-User": "AP-01", "Content-Type": "application/json"}
    status, text = fetch("POST", f"{api}/api/conversations", user, b"")
    if status != 201:
        return Check(False, "a conversation could not be started")
    cid = json.loads(text)["conversation_id"]
    body = json.dumps({"question": PROBE, "request_id": uuid.uuid4().hex}).encode()
    status, text = fetch("POST", f"{api}/api/conversations/{cid}/exchanges", user, body)
    events = [b for b in text.strip().split("\n\n") if b.startswith("event: result")]
    if status != 200 or not events:
        return Check(False, f"the question {PROBE!r} did not complete")
    outcome = json.loads(events[-1].split("data: ", 1)[1])["response"]["outcome"]
    expected = load_scripts()[" ".join(PROBE.casefold().split())]["expect"]["outcome"]
    if outcome != expected:
        return Check(False, f"the question {PROBE!r} ended as {outcome}, not {expected}")
    return Check(True, f"a scripted question runs end to end ({outcome})")


def check_env(env: dict | None = None) -> tuple[Check, object | None]:
    """.env is complete. Names of missing values only, never a value."""
    from model_client.budget import BudgetConfig, BudgetConfigError
    try:
        return Check(True, "backend/.env is complete"), BudgetConfig.from_env(env)
    except BudgetConfigError as e:
        return Check(False, str(e)), None


def check_provider_map(cfg) -> Check:
    from model_client.budget import BudgetConfigError, check_against_provider_map
    try:
        check_against_provider_map(cfg)
    except BudgetConfigError as e:
        return Check(False, str(e))
    return Check(True, "model and prices agree with LiteLLM's map")


def check_output_caps(cfg) -> Check:
    """The max_tokens each call is sent, numbers only: the general cap, intent analysis's where
    MODEL_MAX_OUTPUT_TOKENS_INTENT is set (task 10a, chat 23:35), and the gate's where
    MODEL_MAX_OUTPUT_TOKENS_GATE is (chat, 18.10, 13:50), and synthesis's where
    MODEL_MAX_OUTPUT_TOKENS_SYNTHESIS is (chat 20.10, 14:50), and the classification's and the coverage check's
    where MODEL_MAX_OUTPUT_TOKENS_CLASSIFY and _COVERAGE are (task 4, chat 20.10, 22:30), and the rewriting
    call's where MODEL_MAX_OUTPUT_TOKENS_REWRITE is (R-6, chat 21.10, 22:10)."""
    intent, gate = cfg.max_output_tokens_intent, cfg.max_output_tokens_gate
    synthesis, rewrite = cfg.max_output_tokens_synthesis, cfg.max_output_tokens_rewrite
    classify, cover = cfg.max_output_tokens_classify, cfg.max_output_tokens_coverage
    return Check(True, f"output cap per call: {cfg.max_output_tokens} tokens; intent analysis: "
                 + (f"{intent} tokens" if intent else "the same")
                 + "; the gate: " + (f"{gate} tokens" if gate else "the same")
                 + "; synthesis: " + (f"{synthesis} tokens" if synthesis else "the same")
                 + "; classification: " + (f"{classify} tokens" if classify else "the same")
                 + "; coverage check: " + (f"{cover} tokens" if cover else "the same")
                 + "; rewriting: " + (f"{rewrite} tokens" if rewrite else "the same"))


def check_chain_key(env: dict | None = None) -> Check:
    import os

    from model_client.budget import ENV_FILE
    if env is None:
        from dotenv import dotenv_values
        env = {**os.environ, **{k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}}
    if not str(env.get("MEDAGENTPRO_CHAIN_KEY") or "").strip():
        return Check(False, "MEDAGENTPRO_CHAIN_KEY missing (S4): the real model does not start without it")
    return Check(True, "audit chain key set")


def check_worst_case_left(left: float | None, cfg) -> Check:
    """How many exchanges spending their whole cap still fit in what is left (T12). A count, no amount."""
    if left is None:
        return Check(False, "budget left unknown: the server did not report it")
    if cfg is None:
        return Check(left > 0, "budget left: some" if left > 0 else "budget left: none")
    n = int(left // cfg.max_usd_per_exchange)
    text = f"worst-case exchanges left in the budget: {n}"
    if n < 1:
        return Check(False, f"{text}. Not even one exchange fits")
    if n < DEMO_WORST_CASE_EXCHANGES:
        return Check(True, f"{text}: enough for a smoke test, not for a demo", warn=True)
    return Check(True, text)


def run(mode: str, fetch: Fetch = http_fetch, env: dict | None = None, db_path: Path = seed.DEFAULT_OUT,
        api: str = API, screen: str = SCREEN) -> list[Check]:
    checks = [check_database(db_path)]
    cfg = None
    if mode == "real":
        env_check, cfg = check_env(env)
        checks.append(env_check)
        if cfg is not None:
            checks.append(check_provider_map(cfg))
            checks.append(check_output_caps(cfg))
        checks.append(check_chain_key(env))
    server, health = check_server(fetch, mode, api)
    checks.append(server)
    checks.append(check_screen(fetch, screen))
    if server.ok:
        checks.append(check_examples(fetch, mode, api))
        if mode == "scripted":
            checks.append(check_question(fetch, api))
        else:
            checks.append(check_worst_case_left(health.get("budget_remaining_usd"), cfg))
            checks.append(Check(True, "no question sent: on the real model it would cost"))
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.preflight")
    ap.add_argument("--real", action="store_true", help="the real model: also .env, the map and the budget left")
    args = ap.parse_args(argv)
    # ✅ ⚠️ ❌ on a Windows console whose code page has none of them (cp1255 here), or through a pipe
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    checks = run("real" if args.real else "scripted")
    for c in checks:
        print(c)
    failed = sum(not c.ok for c in checks)
    print(f"\n{'ready' if not failed else f'{failed} check(s) failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
