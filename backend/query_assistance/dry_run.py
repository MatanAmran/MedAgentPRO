"""
MedAgentPro - what an exchange would cost, before a real one (task 5).

Runs two exchanges on the demo store with the scripted model, prices every call at the values in
backend/.env, and prints them. No model is called and no key is needed.

  a first question                 intent, synthesis, gate: 3 calls
  a follow-up with a regeneration  rewrite, intent, synthesis, gate, regeneration, gate: 6 calls

Input tokens are shown two ways: about four characters a token, which is near what a provider
counts for English, and the deliberately high estimate the budget reserves. Output tokens come
from the scripted outputs, which are near the size of real ones. The worst case is what the
budget reserves before each call: the high input estimate plus the full output cap.

Run from backend/:  python -m query_assistance.dry_run
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from demo_data import seed
from model_client.budget import BudgetConfig, BudgetConfigError, estimate_input_tokens
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

    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse:
        resp = self.inner.invoke(prompt, config)
        typical_in, high_in = len(prompt) // 4 + 1, estimate_input_tokens(prompt)
        out = len(resp.text) // 4 + 1
        self.rows.append((config.prompt_identity, typical_in, high_in, out, self.cfg.cost(typical_in, out),
                          self.cfg.cost(high_in, self.cfg.max_output_tokens)))
        return resp


def _exchange(cfg: BudgetConfig, db: Path, gw: ModelGateway, question: str, history) -> tuple[list[tuple], pipeline.Outcome]:
    meter = _Meter(gw, cfg)
    deps = pipeline.Deps(meter, cfg.model_id, Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json"),
                         pipeline.connect_readonly(db),
                         AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"dry-run"),
                         prompts.load(seed.HERE / "intent_analysis.md"))
    out = pipeline.run(question, history, {"role": "APPROVER", "user_id": "AP-01"}, deps)
    return meter.rows, out


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


def main(env=None) -> int:
    try:
        cfg = BudgetConfig.from_env(env, need_key=False)
    except BudgetConfigError as e:
        print(e)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "demo.db"
        seed.build(db)
        rows1, first = _exchange(cfg, db, _script(intent_analysis_demo=[INTENT], synthesis=[SYN],
                                                  support_scoring=[_gate("supported")]), Q, [])
        follow = _script(question_rewriting=[{"question": "Which suppliers delivered late by promised date "
                                                          "in weeks 1 to 13?", "carried_from_history": True}],
                         intent_analysis_demo=[INTENT], synthesis=[SYN], synthesis_redraft=[SYN_ONE],
                         support_scoring=[_gate("supported", "uncovered"), _gate("supported")])
        rows2, _ = _exchange(cfg, db, follow, "And by promised date?", [first.exchange])

    print(f"Model {cfg.model_id}. Prices from backend/.env, no model called.")
    print("The scripted outputs here are accepted at once, so no feedback attempt is priced. Each content "
          "failure adds one call of about the same size as the one it repeats.")
    t1, w1 = _print("A first question (3 calls)", rows1, cfg)
    t2, w2 = _print("A follow-up with one regeneration (6 calls)", rows2, cfg)
    print(f"\nCap per exchange {cfg.max_usd_per_exchange:.4f}. The worst case above "
          f"{'fits' if max(w1, w2) <= cfg.max_usd_per_exchange else 'DOES NOT FIT: calls would be refused'}.")
    print(f"Total cap {cfg.budget_usd:.2f}: about {int(cfg.budget_usd // max(t1, 1e-9))} first questions "
          f"or {int(cfg.budget_usd // max(t2, 1e-9))} follow-ups at the ~ cost.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
