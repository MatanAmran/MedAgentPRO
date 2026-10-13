"""
MedAgentPro - what an exchange would cost, before a real one (task 5).

Runs two exchanges on the demo store with the scripted model, prices every call, and prints them.
No model is called and no key is needed. Prices come from backend/.env, or, with --model, from
LiteLLM's local map for that model, and then .env is not read at all.

  a first question                 intent, synthesis, gate: 3 calls
  a follow-up with a regeneration  rewrite, intent, synthesis, gate, regeneration, gate: 6 calls

Input tokens are shown two ways: about four characters a token, which is near what a provider
counts for English, and the deliberately high estimate the budget reserves. Output tokens come
from the scripted outputs, which are near the size of real ones. The worst case is what the
budget reserves before each call: the high input estimate plus the full output cap.

Also printed: what R7a reserves for the whole path before the first synthesis call, against the cap
per exchange, and how many exchanges a budget covers, typically and at the worst case.

Run from backend/:
  python -m query_assistance.dry_run                                 prices and caps from backend/.env
  python -m query_assistance.dry_run --model claude-sonnet-5-5       map prices, no .env
      [--budget 3.5] [--cap 0.25] [--max-output 2000]                 what to report against
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

from demo_data import seed
from model_client.budget import BudgetConfig, BudgetConfigError, estimate_input_tokens, map_prices
from model_client.client import ModelConfig, ModelGateway, ModelResponse, ScriptedModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from shared.circuit_breaker import CircuitBreaker, MemoryStore

from . import pipeline, prompts
from .compiler import Catalog

Q = "Which suppliers delivered late in weeks 1 to 13?"
LATE = {"label": "late", "plan": {
    "SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
               {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}],
    "FROM": "commitments",
    "WHERE": {"AND": [{"BETWEEN": [{"col": "commitments.week"}, {"value": 1}, {"value": 13}]},
                      {"EQ": [{"metric": "late_by_delivery"}, {"value": True}]}]},
    "GROUP_BY": ["supplier_id"], "ORDER_BY": [{"by": "late_count", "dir": "DESC"}], "LIMIT": 5}}
INTENT = {"disposition": "retrieve", "readings": [LATE]}
SYN = {"readings": [{"label": "late", "sentences": [
    {"text": "{late.supplier_id#1} had the most lines delivered after the requested date, {late.late_count#1}."},
    {"text": "{late.supplier_id#2} followed with {late.late_count#2}."}]}]}
SYN_ONE = {"readings": [{"label": "late", "sentences": [SYN["readings"][0]["sentences"][0]]}]}


def _gate(*verdicts):
    def answer(prompt):
        body = prompt.split("Answer:\n")[-1].split("\nRecords:")[0]
        lines = re.findall(r"^(\d+)\. \[(\w+)\] (.*)$", body, re.M)
        return {"claims": [{"sentence": int(n), "text": t, "verdict": verdicts[min(i, len(verdicts) - 1)],
                            "evidence": [] if verdicts[min(i, len(verdicts) - 1)] == "uncovered"
                            else [f"{lab}.supplier_id"]} for i, (n, lab, t) in enumerate(lines)]}
    return answer


def _script(**by_prompt):
    queues = {k: list(v) for k, v in by_prompt.items()}

    def answer(prompt, cfg):
        out = queues[cfg.prompt_identity.split("@")[0]].pop(0)
        return out(prompt) if callable(out) else out
    return ScriptedModelGateway(answer)


class _Meter(ModelGateway):
    """Passes calls to the scripted model and keeps what each would cost."""

    def __init__(self, inner: ModelGateway, cfg: BudgetConfig):
        self.inner, self.cfg, self.rows = inner, cfg, []
        self.path_worst: float | None = None

    def ensure_path(self, input_tokens: list[int]):
        """What R7a would reserve before the first synthesis call. Kept, never refused here."""
        self.path_worst = sum(self.cfg.cost(n, self.cfg.max_output_tokens) for n in input_tokens)

    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse:
        resp = self.inner.invoke(prompt, config)
        typical_in, high_in = len(prompt) // 4 + 1, estimate_input_tokens(prompt)
        out = len(resp.text) // 4 + 1
        self.rows.append((config.prompt_identity, typical_in, high_in, out, self.cfg.cost(typical_in, out),
                          self.cfg.cost(high_in, self.cfg.max_output_tokens)))
        return resp


def _exchange(cfg: BudgetConfig, db: Path, gw: ModelGateway, question: str, history) -> tuple[_Meter, pipeline.Outcome]:
    meter = _Meter(gw, cfg)
    deps = pipeline.Deps(meter, cfg.model_id, Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json"),
                         pipeline.connect_readonly(db),
                         AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"dry-run"),
                         prompts.load(seed.HERE / "intent_analysis.md"))
    out = pipeline.run(question, history, {"role": "APPROVER", "user_id": "AP-01"}, deps)
    return meter, out


def feedback_rate(audit_log: list[dict]) -> tuple[int, int]:
    """R4: how many calls were feedback attempts, and how many of them were accepted. If feedback
    attempts rarely succeed, set the attempts to 1. Read from the audit entries, so the smoke test and
    a real record give the same figure."""
    attempts = [e for e in audit_log if e.get("feedback_attempt")]
    return len(attempts), sum(1 for e in attempts if e["event_type"] == "model_output_accepted")


def _print(title: str, rows: list[tuple], cfg: BudgetConfig):
    print(f"\n{title}")
    print(f"  {'call':<26}{'input ~':>9}{'input hi':>10}{'output':>8}{'cost ~ USD':>12}{'worst USD':>11}")
    for ident, ti, hi, o, c, w in rows:
        print(f"  {ident:<26}{ti:>9}{hi:>10}{o:>8}{c:>12.5f}{w:>11.5f}")
    total, worst = sum(r[4] for r in rows), sum(r[5] for r in rows)
    print(f"  {'total':<26}{'':>27}{total:>12.5f}{worst:>11.5f}")
    return total, worst


def _config(env, argv) -> BudgetConfig:
    """With --model, prices from the local map and the reporting values from the command line; .env is
    not read. Without it, everything from .env, as before. No key either way."""
    ap = argparse.ArgumentParser(prog="python -m query_assistance.dry_run")
    ap.add_argument("--model", help="price this model from LiteLLM's local map, without backend/.env")
    ap.add_argument("--budget", type=float, default=3.5, help="total to report against, USD (with --model)")
    ap.add_argument("--cap", type=float, default=0.25, help="cap per exchange to report against, USD (with --model)")
    ap.add_argument("--max-output", type=int, default=2000, help="output tokens per call (with --model)")
    args = ap.parse_args(argv or [])   # never the test runner's own arguments
    if not args.model:
        return BudgetConfig.from_env(env, need_key=False)
    try:
        price_in, price_out = map_prices(args.model)
    except KeyError:
        raise BudgetConfigError(f"{args.model} is not priced in LiteLLM's local map") from None
    return BudgetConfig(args.model, price_in, price_out, args.budget, args.cap, args.max_output, 10)


def main(env=None, argv: list[str] | None = None) -> int:
    try:
        cfg = _config(env, argv)
    except BudgetConfigError as e:
        print(e)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "demo.db"
        seed.build(db)
        m1, first = _exchange(cfg, db, _script(intent_analysis_demo=[INTENT], synthesis=[SYN],
                                               support_scoring=[_gate("supported")]), Q, [])
        follow = _script(question_rewriting=[{"question": "Which suppliers delivered late by promised date "
                                                          "in weeks 1 to 13?", "carried_from_history": True}],
                         intent_analysis_demo=[INTENT], synthesis=[SYN], synthesis_redraft=[SYN_ONE],
                         support_scoring=[_gate("supported", "uncovered"), _gate("supported")])
        m2, _ = _exchange(cfg, db, follow, "And by promised date?", [first.exchange])

    source = "LiteLLM's local map" if argv and "--model" in argv else "backend/.env"
    print(f"Model {cfg.model_id}: {cfg.price_in:g} in / {cfg.price_out:g} out per million, from {source}. "
          f"No model called.")
    print("The scripted outputs here are accepted at once, so no feedback attempt is priced. Each content "
          "failure adds one call of about the same size as the one it repeats.")
    t1, w1 = _print("A first question (3 calls)", m1.rows, cfg)
    t2, w2 = _print("A follow-up with one regeneration (6 calls)", m2.rows, cfg)
    cap = cfg.max_usd_per_exchange
    print(f"\nCap per exchange {cap:.4f}.")
    for title, m in (("first question", m1), ("follow-up", m2)):
        path = m.path_worst or 0.0
        print(f"  R7a reserves {path:.5f} for the path from synthesis on ({title}): "
              f"{'fits' if path <= cap else 'DOES NOT FIT: stopped before synthesis'}")
    print(f"  Every call's worst case together: {max(w1, w2):.5f} "
          f"({'fits' if max(w1, w2) <= cap else 'the cap would refuse the last calls'})")
    budget = cfg.budget_usd
    print(f"\nTotal {budget:.2f} covers about {int(budget // max(t1, 1e-9))} first questions or "
          f"{int(budget // max(t2, 1e-9))} follow-ups at the ~ cost; "
          f"{int(budget // min(cap, max(w1, w2)))} follow-ups if every call of them hit its worst case; "
          f"and {int(budget // cap)} exchanges if each spent its whole cap, which nothing above exceeds.")
    return 0


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv[1:]))
