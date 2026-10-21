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
  python -m tools.dry_run                                 prices and caps from backend/.env
  python -m tools.dry_run --model claude-sonnet-5-5       map prices, no .env
      [--budget 3.5] [--cap 0.25] [--max-output 2000]                 what to report against
  python -m tools.dry_run --sets demo,stage,offscript,bird   a round, per set and in total

With --sets, a whole evaluation round instead (Phase B task 7, chat 20.10): every question of each set
through the pipeline on its hand-written plan, with the prompts the round runs (the demo on the frozen
prompts, the other sets on the generic ones). The input tokens are those of the prompts each question
really sends, its records included. The expected output is TYPICAL_OUT, the size of real calls; the worst
case prices every call the question can make (each attempt, the regeneration and its gate) at the high
input estimate and its output cap, but never above the cap per exchange, which refuses beyond it.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from demo_data import seed
from model_client.budget import BudgetConfig, BudgetConfigError, estimate_input_tokens, map_prices
from model_client.client import ModelConfig, ModelGateway, ModelResponse, ScriptedModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import pipeline, prompts
from query_assistance.calls import MODEL_CALL_ATTEMPTS
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, MemoryStore

Q = "Which suppliers delivered late in weeks 1 to 13?"
LATE = {"label": "late", "plan": {
    "SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"},
               {"expr": {"func": "COUNT", "arg": "*"}, "as": "late_count"}],
    "FROM": "commitments",
    "WHERE": {"AND": [{"BETWEEN": {"col": "commitments.week", "from": {"value": 1}, "to": {"value": 13}}},
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


def _coverage_answer(name: str, prompt: str) -> dict | None:
    """Task 4: what the stand-in model answers for C and B: the question asks for recorded values, as one part;
    every part is answered by every sentence, each stating values (demo_data.scripted.coverage_output)."""
    from demo_data.scripted import coverage_output
    if name == "question_classification":
        posed = [ln[len(k):] for k in ("Question as posed: ", "Question: ") for ln in prompt.splitlines()
                 if ln.startswith(k)]
        return {"parts": [{"asked": posed[-1], "asks": "recorded"}]}
    if name == "coverage_check":
        return coverage_output(prompt, None)
    return None


def _script(**by_prompt):
    queues = {k: list(v) for k, v in by_prompt.items()}

    def answer(prompt, cfg):
        name = cfg.prompt_identity.split("@")[0]
        if name not in queues and (got := _coverage_answer(name, prompt)) is not None:
            return got
        out = queues[name].pop(0)
        return out(prompt) if callable(out) else out
    return ScriptedModelGateway(answer)


class _Meter(ModelGateway):
    """Passes calls to the scripted model and keeps what each would cost."""

    def __init__(self, inner: ModelGateway, cfg: BudgetConfig):
        self.inner, self.cfg = inner, cfg
        self.rows: list[tuple[str, int, int, int, float, float]] = []
        self.path_worst: float | None = None

    def ensure_path(self, input_tokens: list[int], prompt_names: Sequence[str] = ()):
        """What R7a would reserve before the first synthesis call, each call at its own output cap. Kept,
        never refused here."""
        names = list(prompt_names) or [""] * len(input_tokens)
        self.path_worst = sum(self.cfg.cost(n, self.cfg.output_cap(p))
                              for n, p in zip(input_tokens, names, strict=True))

    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse:
        resp = self.inner.invoke(prompt, config)
        typical_in, high_in = len(prompt) // 4 + 1, estimate_input_tokens(prompt)
        out = len(resp.text or "") // 4 + 1
        self.rows.append((config.prompt_identity, typical_in, high_in, out, self.cfg.cost(typical_in, out),
                          self.cfg.cost(high_in, self.cfg.output_cap(config.prompt_identity))))
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
    ap = argparse.ArgumentParser(prog="python -m tools.dry_run")
    ap.add_argument("--model", help="price this model from LiteLLM's local map, without backend/.env")
    ap.add_argument("--budget", type=float, default=3.5, help="total to report against, USD (with --model)")
    ap.add_argument("--cap", type=float, default=0.25, help="cap per exchange to report against, USD (with --model)")
    ap.add_argument("--max-output", type=int, default=2000, help="output tokens per call (with --model)")
    ap.add_argument("--max-output-intent", type=int, help="intent analysis's own output cap (with --model)")
    ap.add_argument("--max-output-gate", type=int, help="the gate's own output cap (with --model)")
    ap.add_argument("--max-output-synthesis", type=int, help="synthesis's own output cap (with --model)")
    ap.add_argument("--max-output-rewrite", type=int, help="the rewriting call's own output cap (with --model, R-6)")
    ap.add_argument("--sets", help=f"price a round instead: {', '.join(SETS)}, comma separated")
    ap.add_argument("--without-coverage", action="store_true",
                    help="with --sets: price the round without the coverage check (task 4), to compare")
    args = ap.parse_args(argv or [])   # never the test runner's own arguments
    if not args.model:
        return BudgetConfig.from_env(env, need_key=False)
    try:
        price_in, price_out = map_prices(args.model)
    except KeyError:
        raise BudgetConfigError(f"{args.model} is not priced in LiteLLM's local map") from None
    return BudgetConfig(args.model, price_in, price_out, args.budget, args.cap, args.max_output, 10,
                        max_output_tokens_intent=args.max_output_intent, max_output_tokens_gate=args.max_output_gate,
                        max_output_tokens_synthesis=args.max_output_synthesis,
                        max_output_tokens_rewrite=args.max_output_rewrite)


# --------------------------------------------------------------------------
# a round, per set (Phase B task 7, chat 20.10)
# --------------------------------------------------------------------------

SETS = ("demo", "stage", "offscript", "bird")
# Output tokens of an accepted call, thinking included: the mean of Haiku 5.5's calls in the round of 20.10
# (96 questions, 271 calls: intent 765, synthesis 1308, gate 1001, rewriting 444, redraft 882), rounded as the
# chat set them (16:25). R-6 (chat 21.10, 22:10), from all 368 recorded calls of 20.10: intent 856, so 850; the
# rewrite 455, so it stays 450 (the 634 of 20.10 was the demo set alone)
TYPICAL_OUT = {"question_rewriting": 450, "intent_analysis": 850, "intent_analysis_demo": 850, "synthesis": 1300,
               "synthesis_redraft": 900, "support_scoring": 1000,
               # task 4: estimates, not yet measured (91_COVERAGE_PLAN, section 7); set from the next round
               "question_classification": 350, "coverage_check": 600}
# a question that reaches the gate may take the regeneration path once (NFR 19): these two calls more
REGENERATION = ("synthesis_redraft", "support_scoring")


@dataclass
class Priced:
    id: str
    status: str
    calls: int
    input_tokens: int
    expected: float
    worst: float


@dataclass
class SetPrice:
    name: str
    prompts: str
    items: list[Priced]

    @property
    def expected(self) -> float:
        return sum(p.expected for p in self.items)

    @property
    def worst(self) -> float:
        return sum(p.worst for p in self.items)


def _by_plan(item: dict) -> Callable[[str, ModelConfig], dict]:
    """The model a question's hand-written plan stands for: rewriting hands back the rewrite expected,
    intent analysis the plan, synthesis one sentence per reading that reached records, the gate supports."""
    plan = (item.get("plan") or [None])[0]
    alias = {r["label"]: r["plan"]["SELECT"][0]["as"] for r in (plan or {}).get("readings", [])}

    def answer(prompt: str, cfg: ModelConfig) -> dict:
        name = cfg.prompt_identity.split("@")[0]
        if (got := _coverage_answer(name, prompt)) is not None:
            return got
        if name == "question_rewriting":
            return {"question": item.get("rewritten") or item["question"], "carried_from_history": True}
        if name.startswith("intent_analysis"):
            return plan or {"disposition": "needs_clarification", "readings": []}
        if name in ("synthesis", "synthesis_redraft"):
            held = re.findall(r"^\[(\w+)\] examined:", prompt, re.M)
            return {"readings": [{"label": lab, "sentences": [{"text": f"{{{lab}.{alias[lab]}#1}} is listed."}]}
                                 for lab in dict.fromkeys(held) if lab in alias]}
        body = prompt.split("Answer:\n")[-1].split("\nRecords:")[0]
        return {"claims": [{"sentence": int(n), "text": t, "verdict": "supported", "evidence": [f"{lab}.{alias[lab]}"]}
                           for n, lab, t in re.findall(r"^(\d+)\. \[(\w+)\] (.*)$", body, re.M)]}
    return answer


def _worst(rows: list[tuple], cfg: BudgetConfig) -> float:
    """Every call the question made, in all its attempts, and the regeneration path where it reached the
    gate, each at the high input estimate and its own output cap; never above the cap per exchange."""
    per = {r[0].split("@")[0]: r for r in rows}
    total = sum(MODEL_CALL_ATTEMPTS * cfg.worst_cost(r[2], cfg.output_cap(r[0])) for r in rows)
    if "support_scoring" in per and "synthesis_redraft" not in per:
        like = (per.get("synthesis", per["support_scoring"]), per["support_scoring"])
        for name, row in zip(REGENERATION, like, strict=True):
            total += MODEL_CALL_ATTEMPTS * cfg.worst_cost(row[2], cfg.output_cap(name))
    return min(total, cfg.max_usd_per_exchange)


def _round_set(name: str, cfg: BudgetConfig, tmp: Path, coverage: bool = True) -> SetPrice:
    from api.state import DEMO_USERS
    from tools import bird_eval, bird_fetch, real_eval
    catalog = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
    generic = name != "demo"   # the round's plan (chat 20.10): the demo on @9 as the base, the rest on @11
    words = seed.HERE   # the catalog's own prompt words and examples
    if name == "bird":
        catalog, db, words = bird_eval.catalog(), bird_fetch.superhero_db(), bird_eval.HERE
        items = real_eval.bird_set()
    else:
        db = tmp / "demo.db"
        if not db.exists():
            seed.build(db)
        items = (real_eval.demo_set() if name == "demo" else
                 real_eval.load_stage_set(real_eval.STAGE_SET_FILE, catalog) if name == "stage" else
                 real_eval.load_set(real_eval.SET_FILE, catalog))
    intent = (prompts.for_catalog("intent_analysis.md", words) if generic
              else prompts.load(seed.HERE / "intent_analysis.md"))
    deps = pipeline.Deps(ScriptedModelGateway(lambda p, c: {}), cfg.model_id, catalog, pipeline.connect_readonly(db),
                         AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"dry-run"),
                         intent, prompt_set=prompts.PromptSet.generic(words) if generic else None,
                         coverage=coverage)
    out: list[Priced] = []
    histories: dict[str, list] = {}
    for it in items:
        meter = _Meter(ScriptedModelGateway(_by_plan(it)), cfg)
        history = list(histories.get(it["after"], [])) if it.get("after") else []
        user = {"role": DEMO_USERS[it["user"]], "user_id": it["user"]}
        res = pipeline.run(it["question"], history, user, replace(deps, gateway=meter))
        histories[it["id"]] = history + [res.exchange]
        expected = sum(cfg.cost(r[1], TYPICAL_OUT[r[0].split("@")[0]]) for r in meter.rows)
        out.append(Priced(it["id"], real_eval.status_of(res.response), len(meter.rows),
                          sum(r[1] for r in meter.rows), expected, _worst(meter.rows, cfg)))
    return SetPrice(name, f"{intent.identity}, {'generic' if generic else 'frozen'}"
                          + ("" if coverage else ", no coverage check"), out)


def price_round(cfg: BudgetConfig, names: Sequence[str], coverage: bool = True) -> list[SetPrice]:
    with tempfile.TemporaryDirectory() as tmp:
        return [_round_set(n, cfg, Path(tmp), coverage) for n in names]


def print_round(cfg: BudgetConfig, sets: list[SetPrice]) -> None:
    print(f"Model {cfg.model_id}: {cfg.price_in:g} in / {cfg.price_out:g} out per million. No model called.")
    print("expected: the input each question sends, a typical real call's output (TYPICAL_OUT, thinking "
          "included), no feedback attempt. worst: every attempt and the regeneration, at the high input "
          "estimate and the output cap, at most the cap per exchange.")
    print(f"\n  {'set':<10}{'prompts':<34}{'questions':>10}{'calls':>7}{'input ~':>10}{'expected':>10}{'worst':>9}")
    for s in sets:
        print(f"  {s.name:<10}{s.prompts:<34}{len(s.items):>10}{sum(p.calls for p in s.items):>7}"
              f"{sum(p.input_tokens for p in s.items):>10}{s.expected:>10.4f}{s.worst:>9.4f}")
    print(f"  {'total':<44}{sum(len(s.items) for s in sets):>10}{sum(p.calls for s in sets for p in s.items):>7}"
          f"{sum(p.input_tokens for s in sets for p in s.items):>10}"
          f"{sum(s.expected for s in sets):>10.4f}{sum(s.worst for s in sets):>9.4f}")
    for s in sets:
        statuses: dict[str, int] = {}
        for p in s.items:
            statuses[p.status] = statuses.get(p.status, 0) + 1
        print(f"\n{s.name}: " + ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())))


def main(env=None, argv: list[str] | None = None) -> int:
    try:
        cfg = _config(env, argv)
    except BudgetConfigError as e:
        print(e)
        return 1
    if argv and "--sets" in argv:
        names = argv[argv.index("--sets") + 1].split(",")
        unknown = [n for n in names if n not in SETS]
        if unknown:
            print(f"unknown set {', '.join(unknown)}: {', '.join(SETS)}")
            return 2
        print_round(cfg, price_round(cfg, names, coverage="--without-coverage" not in argv))
        return 0
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
