"""
Load and concurrency tests. Not in every run:  python -m pytest -m stress

  50 conversations at once, 3 scripted questions each, through the server
  one conversation of 30 follow-ups: the rewriting prompt stays within the history budget
  20 exchanges at once on the budget, one spend file: what the file holds is what was reported

No test reaches a real model (6.1).
"""

import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import main
from demo_data import seed
from demo_data.scripted import ScriptedDemoGateway, load_scripts
from model_client import budget as b
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, MemoryStore

pytestmark = pytest.mark.stress

CAT = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
QUESTIONS = ["Which suppliers were late in weeks 1 to 13?", "Why is S-014 late so often?", "What did S-040 supply?"]
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}

# Measured on Evyatar's machine (13.10), three runs: 11.2, 12.6 and 11.0 s for 50 conversations x 4
# questions (200 exchanges). The bound is three times the middle one, so a regression shows and an
# ordinary slow run does not.
MEASURED_SECONDS = 11.2
BOUND_SECONDS = 3 * MEASURED_SECONDS


def _events(text: str) -> list[str]:
    return [blk.split("\n")[0].removeprefix("event: ") for blk in text.strip().split("\n\n") if blk]


def test_fifty_conversations_at_once(db_path):
    app = main.create_app("scripted", db_path)
    h = {"X-Demo-User": "AP-01"}

    async def conversation(client):
        cid = (await client.post("/api/conversations", headers=h)).json()["conversation_id"]
        ends = []
        for q in QUESTIONS:
            r = await client.post(f"/api/conversations/{cid}/exchanges", headers=h,
                                  json={"question": q, "request_id": uuid.uuid4().hex})
            assert r.status_code == 200
            evs = _events(r.text)
            assert evs.count("result") == 1 and evs[-1] == "result"
            ends.append(json.loads(r.text.strip().split("\n\n")[-1].split("\n")[1][6:])["response"]["outcome"])
        # not left busy: one more question is taken
        r = await client.post(f"/api/conversations/{cid}/exchanges", headers=h,
                              json={"question": QUESTIONS[1], "request_id": uuid.uuid4().hex})
        assert r.status_code == 200
        return ends

    async def everything():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     timeout=120) as client:
            return await asyncio.gather(*(conversation(client) for _ in range(50)))

    start = time.perf_counter()
    results = asyncio.run(everything())
    took = time.perf_counter() - start
    print(f"\n50 conversations x {len(QUESTIONS) + 1} questions: {took:.2f} s")
    assert all(r == ["answered", "referred_to_investigation", "answered"] for r in results)
    assert took < BOUND_SECONDS, f"{took:.1f} s, bound {BOUND_SECONDS:.1f} s (measured {MEASURED_SECONDS} s)"


def test_thirty_follow_ups_keep_the_rewriting_prompt_within_the_history_budget(db_path):
    history, sizes = [], []
    for i in range(30):
        q = QUESTIONS[i % 2]                  # answered and referred, so the history holds both kinds
        gw = ScriptedDemoGateway(q, load_scripts())
        d = pl.Deps(gw, "scripted-demo", CAT, pl.connect_readonly(db_path),
                    AuditRepository(MemoryStorage(), CircuitBreaker("s", MemoryStore()), b"k"),
                    p.load(seed.HERE / "intent_analysis.md"))
        out = pl.run(q, history, APPROVER, d)
        history.append(out.exchange)
        for prompt, cfg in gw.calls:
            if cfg.prompt_identity.startswith("question_rewriting@"):
                part = prompt.rsplit("Conversation:\n", 1)[1].split("\nLatest question:")[0]
                sizes.append(p._tokens(part))
    assert len(sizes) == 29
    assert max(sizes) <= p.HISTORY_BUDGET_TOKENS, f"largest history {max(sizes)} tokens"


def test_twenty_exchanges_at_once_on_one_spend_file(db_path, tmp_path):
    env = {"ANTHROPIC_API_KEY": "test-key-not-real", "MODEL_ID": "test-model", "MODEL_PRICE_IN": "2",
           "MODEL_PRICE_OUT": "10", "MODEL_BUDGET_USD": "0.3", "MODEL_MAX_USD_PER_EXCHANGE": "0.25",
           "MODEL_MAX_OUTPUT_TOKENS": "2000", "MODEL_MAX_EXCHANGES_PER_MINUTE": "1000"}
    ledger = b.SpendLedger(tmp_path / "spend.json")
    q = QUESTIONS[0]
    gw = b.BudgetedGateway(ScriptedDemoGateway(q, load_scripts()), b.BudgetConfig.from_env(env), ledger)
    d = pl.Deps(gw, "test-model", CAT, pl.connect_readonly(db_path),
                AuditRepository(MemoryStorage(), CircuitBreaker("s", MemoryStore()), b"k"),
                p.load(seed.HERE / "intent_analysis.md"))
    try:
        with ThreadPoolExecutor(max_workers=20) as pool:
            outs = list(pool.map(lambda _: pl.run(q, [], APPROVER, d), range(20)))
    finally:
        ledger.close()
    reported = sum(o.cost["exchange_usd"] for o in outs)
    held = json.loads((tmp_path / "spend.json").read_text(encoding="utf-8"))["total_usd"]
    outcomes = [o.response["outcome"] for o in outs]
    print(f"\n20 at once on a 0.30 budget: {outcomes.count('answered')} answered, "
          f"{outcomes.count('request_not_completed')} refused, {held:.4f} spent")
    assert held == pytest.approx(reported, abs=1e-5)       # each exchange rounds its cost to 6 places
    assert held <= 0.3
    assert "request_not_completed" in outcomes            # the budget, not luck, stopped the rest
