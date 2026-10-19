"""
MedAgentPro - runs a question set through the pipeline in process and reports, for the chat to judge
(docs/notes/75_OFFSCRIPT_SET.md, chat 18.10, 12:30).

    python -m tools.real_eval                                   the held-out set on the scripted model
    python -m tools.real_eval --real --confirm --out DIR        the same on the real model, paid
        [--set PATH | --set demo] [--only A1,F2] [--max-usd 2]      demo: the 12 demo questions

Every exchange goes through pipeline.run with the dependencies the server builds (main.build_deps), so
the budget, the rate across users, the per-exchange cap and the audit record all hold. On top of them:

  the run's own cap   --max-usd (default 2). An exchange starts only if what the run has spent, plus the
                      most one exchange may spend (MODEL_MAX_USD_PER_EXCHANGE), stays within it. So the run
                      never crosses the cap, and stops a little before it
  the rate            an exchange refused for the rate (its notice names the seconds) is asked again after
                      that wait. Nothing was sent to the model, so nothing was paid
  conversations       an exchange with "after" continues that exchange's conversation; "--only" adds the
                      exchanges it follows

What it checks, in code, beside each answer: the status against the expected ones, the counts against
the set's SQL on the same database (any reading against any alternative), identifiers that must not be
shown, and text Examined must not hold. The period, the metric, the gap kind and the identifiers said
not held are hints only (⚠️, never ❌): a plan can state them in another form, and a gap can be fair.
Whether the words are true is the chat's judgement, not this tool's.

Output, in --out (refused inside the repository: the answers are not committed, chat 12:30):
  report.md        the table, then each exchange in full: Understood as, Examined, rows, gaps, the
                   answer's text, the checks, and per model call its check, feedback, tokens and cost
  results.json     the same, with the responses as presented, rows included
  operational.log  the operational channel: one line per model call, the cost of each exchange
Both reports are rewritten after every exchange, so a run that stops keeps what it did.

--real without --confirm prints what would run and spends nothing. Run it only with Evyatar's approval,
and never while the server runs on the real model: both would use the spend file (T2).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from api.state import DEMO_USERS
from demo_data import seed
from model_client.client import ModelGateway
from query_assistance import contract, pipeline, prompts
from query_assistance.call_trace import CallTrace
from query_assistance.compiler import Catalog, CompileError, compile_intent, execute
from shared.circuit_breaker import log as operational_log

SET_FILE = seed.HERE / "offscript_set.json"
REPO = Path(__file__).resolve().parents[2]
DEFAULT_MAX_USD = 2.0
MAX_RATE_WAITS = 5

STATUSES = {"answered", "referred_to_investigation", "action_declined", "needs_clarification",
            "no_supported_assertion", "request_not_completed", "source_unreachable"}
COMPARES = {"total", "rows", "value"}
OK, FAIL, HINT, NONE = "✅", "❌", "⚠️", "—"


class SetError(Exception):
    pass


# --------------------------------------------------------------------------
# the set
# --------------------------------------------------------------------------

def load_set(path: Path = SET_FILE, catalog: Catalog | None = None) -> list[dict]:
    """The exchanges in order, checked: unique ids, an exchange followed comes earlier and belongs to
    the same user, known users, statuses, comparisons, metrics and gap kinds."""
    items = json.loads(Path(path).read_text(encoding="utf-8"))["exchanges"]
    seen: dict[str, dict] = {}
    for it in items:
        where = it.get("id", "?")
        for key in ("id", "user", "question", "expect"):
            if not it.get(key):
                raise SetError(f"{where}: {key} is missing")
        if it["id"] in seen:
            raise SetError(f"{where}: the id is used twice")
        if it["user"] not in DEMO_USERS:
            raise SetError(f"{where}: {it['user']} is not a demo user")
        after = it.get("after")
        if after is not None:
            if after not in seen:
                raise SetError(f"{where}: follows {after}, which does not come before it")
            if seen[after]["user"] != it["user"]:
                raise SetError(f"{where}: follows {after}, another user's conversation")
        exp = it["expect"]
        bad = set(exp.get("status") or []) - STATUSES
        if not exp.get("status") or bad:
            raise SetError(f"{where}: expect.status {sorted(bad) or 'is empty'}")
        for s in it.get("sql", []):
            if s.get("compare") not in COMPARES or not s.get("query"):
                raise SetError(f"{where}: an sql entry needs a query and a compare in {sorted(COMPARES)}")
        if catalog is not None:
            for m in exp.get("metric", []):
                if m not in catalog.metrics:
                    raise SetError(f"{where}: no metric {m}")
        if exp.get("gap") and exp["gap"] not in contract.TEMPLATES:
            raise SetError(f"{where}: no gap kind {exp['gap']}")
        seen[it["id"]] = it
    return items


def demo_set(path: Path = seed.HERE / "scripted_exchanges.json") -> list[dict]:
    """The demo questions in this tool's form: Q01..Q14, each as the demo user it names, a follow-up after
    the question it follows, the status from the scripted expectation, and the scripted intent as its plan
    (rule 1). No SQL: their counts are the golden tests' business."""
    out: list[dict] = []
    ids: dict[str, str] = {}
    for n, e in enumerate(json.loads(Path(path).read_text(encoding="utf-8"))["exchanges"], 1):
        exp = e["expect"]
        status = ["action_declined" if "action_declined" in exp.get("shows", []) else exp["outcome"]]
        if exp.get("scripted_gate") and status != ["answered"]:
            status.append("answered")   # the scripted gate decides; the real one may support the answer
        item = {"id": f"Q{n:02d}", "user": exp.get("user", "AP-01"), "question": e["question"],
                "expect": {"status": status}, "watch": exp.get("note"),
                "plan": [e["intent"]] if e.get("intent") else []}
        if (e.get("rewrite") or {}).get("question"):
            item["rewritten"] = e["rewrite"]["question"]
        if exp.get("follows"):
            item["after"] = ids[exp["follows"]]
        ids[e["question"]] = item["id"]
        out.append(item)
    return out


def select(items: list[dict], only: list[str] | None) -> list[dict]:
    """The exchanges named, with every exchange each follows, in the set's order."""
    if not only:
        return list(items)
    by_id = {it["id"]: it for it in items}
    unknown = [i for i in only if i not in by_id]
    if unknown:
        raise SetError(f"not in the set: {', '.join(unknown)}")
    wanted: set[str] = set()
    for i in only:
        while i and i not in wanted:
            wanted.add(i)
            i = by_id[i].get("after")
    return [it for it in items if it["id"] in wanted]


# --------------------------------------------------------------------------
# checks, in code, on what was presented
# --------------------------------------------------------------------------

def status_of(response: dict) -> str:
    """The outcome, with the fixed notices that share answered told apart."""
    if response["outcome"] == "answered":
        notice = response.get("notice")
        if notice == contract.ACTION_DECLINED:
            return "action_declined"
        if contract.is_needs_clarification(notice):
            return "needs_clarification"
    return response["outcome"]


def _numbers(row: dict) -> list[float]:
    return [float(v) for v in row.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 0.005 + 1e-9 * abs(b)


def check_counts(item: dict, response: dict, conn: sqlite3.Connection) -> tuple[bool | None, list[dict]]:
    """Each alternative's expected count, and the readings that match it. None where the set holds no
    SQL; True where any reading matches any alternative."""
    out = []
    for alt in item.get("sql", []):
        rows = conn.execute(alt["query"]).fetchall()
        if alt["compare"] == "value":
            expected: Any = rows[0][0] if rows else None
            matched = [r["label"] for r in response["readings"] if expected is not None and r["row_count"] == 1
                       and any(_close(n, float(expected)) for n in _numbers(r["rows"][0]))]
        else:
            expected = len(rows)
            key = "total" if alt["compare"] == "total" else "row_count"
            matched = [r["label"] for r in response["readings"] if r.get(key) == expected]
        out.append({"compare": alt["compare"], "expected": expected, "matched": matched, "query": alt["query"]})
    if not out:
        return None, out
    return any(a["matched"] for a in out), out


def check_forbidden(item: dict, response: dict, conn: sqlite3.Connection) -> tuple[bool | None, list[str]]:
    """Identifiers forbid_sql names that appear in a row shown or in an answer. None where it names none."""
    if not item.get("forbid_sql"):
        return None, []
    forbidden = {str(r[0]) for r in conn.execute(item["forbid_sql"])}
    shown: set[str] = set()
    for r in response["readings"]:
        for row in r["rows"]:
            shown.update(str(v) for v in row.values() if v is not None)
        shown.update(t for t in re.findall(r"[A-Z]+-\d+", r.get("answer") or ""))
    leaked = sorted(forbidden & shown)
    return not leaked, leaked


def check_examined(item: dict, response: dict, catalog: Catalog) -> dict[str, bool | None]:
    """period and metric: hints, read from the Examined lines as code renders them. forbid: text no
    Examined line may hold, a check."""
    exp = item["expect"]
    lines = [r["checked"] for r in response["readings"]]
    low = [s.casefold() for s in lines]
    out: dict[str, bool | None] = {"period": None, "metric": None, "forbid": None}
    if exp.get("period") and lines:
        p = exp["period"]
        # Examined quotes a text or date value ('2026-09-01') and not a number (week between 1 and 13)
        out["period"] = any(f"between {p['from']} and {p['to']}" in s.replace("'", "") for s in lines)
    if exp.get("metric") and lines:
        phrases = []
        for m in exp["metric"]:
            d = catalog.metrics[m]
            phrases += [d["label"].casefold(), d.get("description", "").rstrip(".").casefold()]
        out["metric"] = any(ph and ph in s for ph in phrases for s in low)
    if exp.get("forbid_in_examined"):
        out["forbid"] = not any(f.casefold() in s for f in exp["forbid_in_examined"] for s in low)
    return out


def check_parts(item: dict, response: dict) -> dict[str, bool | None]:
    """gap and not_held: hints, whether the answer holds the gap kind and the identifiers said absent."""
    exp = item["expect"]
    out: dict[str, bool | None] = {"gap": None, "not_held": None}
    if exp.get("gap"):
        out["gap"] = exp["gap"] in {g["missing"]["kind"] for g in response.get("gaps", [])}
    if exp.get("not_held"):
        held = {m["value"] for r in response.get("readings", []) for m in r.get("not_held", [])}
        out["not_held"] = set(exp["not_held"]) <= held
    return out


# --------------------------------------------------------------------------
# one run
# --------------------------------------------------------------------------

@dataclass
class Result:
    id: str
    user: str
    question: str
    after: str | None
    expect: dict
    watch: str | None
    status: str = ""
    status_ok: bool = False
    exchange_id: str | None = None
    recorded: bool | None = None
    response: dict = field(default_factory=dict)
    counts_ok: bool | None = None
    counts: list[dict] = field(default_factory=list)
    forbidden_ok: bool | None = None
    leaked: list[str] = field(default_factory=list)
    examined: dict = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    cost: dict | None = None
    seconds: float = 0.0
    rate_waits: list[int] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """In code only: ❌ where a check failed, ⚠️ where a hint did not hold, else ✅. Not the judgement."""
        if not self.status_ok or False in (self.counts_ok, self.forbidden_ok, self.examined.get("forbid")):
            return FAIL
        if False in (self.examined.get("period"), self.examined.get("metric"), self.examined.get("gap"),
                     self.examined.get("not_held")):
            return HINT
        return OK


@dataclass
class Run:
    mode: str
    model: str
    max_usd: float
    started: str
    results: list[Result] = field(default_factory=list)
    spent: float = 0.0
    remaining: float | None = None
    stopped: str | None = None
    skipped: list[str] = field(default_factory=list)


def _per_exchange_cap(gateway: ModelGateway | None) -> float:
    cfg = getattr(gateway, "config", None)
    return float(getattr(cfg, "max_usd_per_exchange", 0.0) or 0.0)


def _call_row(t: CallTrace, gateway: ModelGateway | None) -> dict:
    row = t.as_dict()
    cfg = getattr(gateway, "config", None)
    row["usd"] = (round(cfg.cost(t.input_tokens or 0, t.output_tokens or 0), 6)
                  if cfg is not None and t.input_tokens is not None else None)
    return row


def run_set(items: list[dict], deps: pipeline.Deps, gateway_for: Callable[[str], ModelGateway],
            conn: sqlite3.Connection, *, mode: str = "scripted", max_usd: float = DEFAULT_MAX_USD,
            sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
            on_result: Callable[[Run], None] | None = None) -> Run:
    """Runs the items in order. An exchange whose conversation did not run is skipped, and the run
    stops before an exchange that could take it past max_usd."""
    run = Run(mode, deps.model, max_usd, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    histories: dict[str, list] = {}
    for it in items:
        if it.get("after") and it["after"] not in histories:
            run.skipped.append(it["id"])
            continue
        gateway = gateway_for(it["question"])
        worst = _per_exchange_cap(gateway)
        if run.spent + worst > max_usd + 1e-9:
            run.stopped = (f"before {it['id']}: spent {run.spent:.4f} + up to {worst:.4f} for one exchange "
                           f"would pass the run's cap of {max_usd:.2f}")
            break
        history = list(histories.get(it["after"], [])) if it.get("after") else []
        user = {"user_id": it["user"], "role": DEMO_USERS[it["user"]]}
        res = Result(it["id"], it["user"], it["question"], it.get("after"), it["expect"], it.get("watch"))
        started = clock()
        while True:
            calls: list[CallTrace] = []
            marks: list[tuple[str, float]] = []
            outcome = pipeline.run(it["question"], history, user, replace(deps, gateway=gateway),
                                   on_stage=lambda name, marks=marks: marks.append((name, clock())),
                                   context={"request_id": f"eval-{it['id']}", "conversation_id": f"eval-{it['id']}"},
                                   on_call=calls.append)
            wait = contract.is_rate_limited(outcome.response.get("notice"))
            if wait is None or len(res.rate_waits) >= MAX_RATE_WAITS:
                break
            res.rate_waits.append(wait)
            sleep(wait + 1)
            gateway = gateway_for(it["question"])
            started = clock()
        ended = clock()
        res.seconds = round(ended - started, 2)
        res.response, res.exchange_id, res.recorded, res.cost = (outcome.response, outcome.exchange_id,
                                                                 outcome.recorded, outcome.cost)
        res.status = status_of(outcome.response)
        res.status_ok = res.status in it["expect"]["status"]
        res.counts_ok, res.counts = check_counts(it, outcome.response, conn)
        res.forbidden_ok, res.leaked = check_forbidden(it, outcome.response, conn)
        res.examined = {**check_examined(it, outcome.response, deps.catalog), **check_parts(it, outcome.response)}
        res.calls = [_call_row(t, gateway) for t in calls]
        res.stages = [{"stage": name, "seconds": round(((marks[i + 1][1] if i + 1 < len(marks) else ended) - at), 2)}
                      for i, (name, at) in enumerate(marks)]
        if outcome.cost:
            run.spent = round(run.spent + outcome.cost["exchange_usd"], 6)
            run.remaining = outcome.cost["remaining_usd"]
        histories[it["id"]] = history + [outcome.exchange]
        run.results.append(res)
        if on_result:
            on_result(run)
    return run


# --------------------------------------------------------------------------
# rule 1: a hand-written plan for every question, with no model (chat, 18.10, 13:40)
# --------------------------------------------------------------------------

@dataclass
class PlanResult:
    id: str
    alternative: int
    status: str
    error: str | None = None
    counts_ok: bool | None = None
    counts: list[dict] = field(default_factory=list)
    forbidden_ok: bool | None = None
    examined: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Every check holds, the hints included: a plan written by hand states the question exactly."""
        held = [self.counts_ok, self.forbidden_ok, *self.examined.values()]
        return self.error is None and False not in held


def _conversation(item: dict, by_id: dict[str, dict]) -> list[prompts.Exchange]:
    """The questions earlier in the item's conversation, oldest first, as the compiler counts them."""
    earlier: list[prompts.Exchange] = []
    at = item.get("after")
    while at:
        earlier.insert(0, prompts.Exchange(by_id[at]["question"], None))
        at = by_id[at].get("after")
    return earlier


def plan_response(item: dict, intent: dict, earlier: list[prompts.Exchange], cat: Catalog,
                  conn: sqlite3.Connection) -> dict:
    """The response the contract would present for this plan: compiled for the item's user, run on the
    store, rendered by contract.py. No model, no synthesis: the readings carry no answer. A follow-up's
    "rewritten" is the rewrite expected, which the compiler counts as rewriting would hand it."""
    user = {"user_id": item["user"], "role": DEMO_USERS[item["user"]]}
    conv = prompts.compiler_conversation(earlier, item["question"], item.get("rewritten"))
    compiled = compile_intent(intent, conv, user, cat)
    if compiled.disposition == "refer_to_investigation":
        return contract.referred()
    if compiled.disposition == "out_of_scope_action":
        return contract.declined_action()
    if compiled.disposition == "needs_clarification":
        return contract.needs_clarification(catalog=cat)
    plans = {r["label"]: r["plan"] for r in intent["readings"]}
    views = [contract.reading_view(r.label, plans[r.label], execute(r, conn), cat, user["role"])
             for r in compiled.readings]
    return contract.answered(views, compiled.unresolved, catalog=cat)


def check_plans(items: list[dict], cat: Catalog, conn: sqlite3.Connection) -> list[PlanResult]:
    """Every plan of every item through the schema, the compiler, the store and the checks of a real run."""
    by_id = {it["id"]: it for it in items}
    out = []
    for it in items:
        if not it.get("plan"):
            out.append(PlanResult(it["id"], 0, "", error="no plan"))
            continue
        for n, intent in enumerate(it["plan"], 1):
            try:
                resp = plan_response(it, intent, _conversation(it, by_id), cat, conn)
            except CompileError as e:
                out.append(PlanResult(it["id"], n, "", error=f"CompileError {e.code}: {e}"))
                continue
            r = PlanResult(it["id"], n, status_of(resp))
            # no_supported_assertion is the gate's to reach; a plan reaches answered at most
            reachable = set(it["expect"]["status"]) | ({"answered"} if "no_supported_assertion" in
                                                       it["expect"]["status"] else set())
            if r.status not in reachable:
                r.error = f"status {r.status}"
            r.counts_ok, r.counts = check_counts(it, resp, conn)
            r.forbidden_ok, _ = check_forbidden(it, resp, conn)
            r.examined = {**check_examined(it, resp, cat), **check_parts(it, resp)}
            out.append(r)
    return out


def without_plan(items: list[dict], results: list[PlanResult]) -> list[str]:
    """The items no plan passes for: a gap in the plan language, fixed before a real run (rule 1)."""
    passing = {r.id for r in results if r.passed}
    return [it["id"] for it in items if it["id"] not in passing]


def plans_table(results: list[PlanResult]) -> str:
    cols = ["#", "plan", "status", "pass", "counts", "period", "metric", "gap", "forbid", "error"]
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in results:
        gap = [v for v in (r.examined.get("gap"), r.examined.get("not_held")) if v is not None]
        forbid = [v for v in (r.forbidden_ok, r.examined.get("forbid")) if v is not None]
        rows.append(f"| {r.id} | {r.alternative} | {r.status} | {OK if r.passed else FAIL} | {_mark(r.counts_ok)} | "
                    f"{_hint(r.examined.get('period'))} | {_hint(r.examined.get('metric'))} | "
                    f"{_hint(all(gap) if gap else None)} | {_mark(all(forbid) if forbid else None)} | "
                    f"{_cell(r.error)} |")
    return "\n".join(rows)


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

def _mark(v: bool | None) -> str:
    return NONE if v is None else OK if v else FAIL


def _hint(v: bool | None) -> str:
    return NONE if v is None else OK if v else HINT


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def summary_table(run: Run) -> str:
    cols = ["#", "user", "expected", "status", "auto", "counts", "period", "metric", "gap", "forbid",
            "Understood as", "usd", "s"]
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols)
    rows = []
    for r in run.results:
        held = [v for v in (r.forbidden_ok, r.examined.get("forbid")) if v is not None]
        forbid = all(held) if held else None
        parts = [v for v in (r.examined.get("gap"), r.examined.get("not_held")) if v is not None]
        parts = [all(parts)] if parts else []
        rows.append(f"| {r.id} | {r.user} | {' / '.join(r.expect['status'])} | {r.status} | {r.verdict} | "
                    f"{_mark(r.counts_ok)} | {_hint(r.examined.get('period'))} | {_hint(r.examined.get('metric'))} | "
                    f"{_hint(parts[0] if parts else None)} | {_mark(forbid)} | "
                    f"{_cell(r.response.get('understood_as'))} | "
                    f"{r.cost['exchange_usd'] if r.cost else NONE} | {r.seconds} |")
    return "\n".join([head, *rows])


def _detail(r: Result) -> list[str]:
    resp = r.response
    out = [f"## {r.id} · {r.user} · {r.status} {r.verdict}", "", f"**Asked:** {r.question}"]
    if r.after:
        out.append(f"**Follows:** {r.after}")
    if r.watch:
        out.append(f"**Watch:** {r.watch}")
    out.append(f"**Expected:** {' / '.join(r.expect['status'])}"
               + (f" · period {r.expect['period']['from']}–{r.expect['period']['to']}"
                  if r.expect.get("period") else "")
               + (f" · metric {', '.join(r.expect['metric'])}" if r.expect.get("metric") else "")
               + (f" · gap {r.expect['gap']}" if r.expect.get("gap") else "")
               + (f" · not held {', '.join(r.expect['not_held'])}" if r.expect.get("not_held") else ""))
    if resp.get("understood_as"):
        out.append(f"**Understood as:** {resp['understood_as']}")
    for key in ("notice", "withheld"):
        if resp.get(key):
            out.append(f"**{key.capitalize()}:** {resp[key]}")
    if resp.get("request_for_input"):
        rfi = resp["request_for_input"]
        out.append(f"**Request for input:** {rfi['text']}")
        out += [f"- [{x['label']}] {x['checked']}" for x in rfi.get("readings", [])]
    if resp.get("ambiguous_terms"):
        out.append(f"**Ambiguous:** {', '.join(resp['ambiguous_terms'])}")
    for v in resp.get("readings", []):
        out += ["", f"### [{v['label']}] {v.get('title', '')}", f"{v['checked']}",
                f"result {v['result']} · shown {v['row_count']} of {v['total']}"
                + (f" · {v['ordered_by']}" if v.get("ordered_by") else "")
                + (f" · notice: {v['notice']}" if v.get("notice") else "")]
        if v.get("answer"):
            out += ["", "**Answer:**", "", v["answer"]]
        if v.get("rows"):
            cols = v["columns"]
            out += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            out += ["| " + " | ".join(_cell(row.get(c)) for c in cols) + " |" for row in v["rows"]]
    if resp.get("gaps"):
        out += ["", "**Gaps:**"] + [f"- {g['missing']['kind']}: {g['text']}" for g in resp["gaps"]]
    out += ["", "**Checks:**"]
    for c in r.counts:
        out.append(f"- {c['compare']} = {c['expected']}: {OK + ' ' + ', '.join(c['matched']) if c['matched'] else FAIL}"
                   f" · `{c['query']}`")
    if r.forbidden_ok is not None:
        out.append(f"- not shown (forbid_sql): {OK if r.forbidden_ok else FAIL + ' ' + ', '.join(r.leaked)}")
    for k, what in (("period", "period in Examined"), ("metric", "metric in Examined"),
                    ("gap", "gap kind in the answer"), ("not_held", "said not held")):
        if r.examined.get(k) is not None:
            out.append(f"- {what}: {_hint(r.examined[k])}")
    if r.examined.get("forbid") is not None:
        out.append(f"- Examined free of {r.expect['forbid_in_examined']}: {_mark(r.examined['forbid'])}")
    if r.rate_waits:
        out.append(f"- waited for the rate: {r.rate_waits} s")
    if r.calls:
        out += ["", "| stage | prompt | # | status | feedback | check | finish | in | out | reasoning | ≈usd | ms |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        out += [f"| {c['stage']} | {c['prompt_identity']} | {c['attempt_number']} | {c['status']} | "
                f"{'yes' if c['feedback_attempt'] else ''} | {_cell(c['check'])} | {_cell(c['finish_reason'])} | "
                f"{_cell(c['input_tokens'])} | {_cell(c['output_tokens'])} | {_cell(c['reasoning_tokens'])} | "
                f"{_cell(c['usd'])} | {_cell(round(c['duration_ms']) if c['duration_ms'] is not None else None)} |"
                for c in r.calls]
    out += ["", "Stages: " + ", ".join(f"{s['stage']} {s['seconds']} s" for s in r.stages)
            + f" · total {r.seconds} s · exchange {r.exchange_id} recorded {r.recorded}"
            + (f" · {r.cost['exchange_usd']} USD" if r.cost else ""), ""]
    return out


def report(run: Run) -> str:
    out = [f"# Evaluation run · {run.started}", "",
           f"mode {run.mode} · model {run.model} · {len(run.results)} exchanges · spent {run.spent:.4f} USD"
           + (f" · remaining {run.remaining:.4f}" if run.remaining is not None else "")
           + f" · run cap {run.max_usd:.2f}", ""]
    if run.stopped:
        out += [f"**Stopped:** {run.stopped}", ""]
    if run.skipped:
        out += [f"**Skipped (their conversation did not run):** {', '.join(run.skipped)}", ""]
    out += ["auto is code's checks only (status, counts, forbidden). ⚠️ is a hint to read, not a failure. "
            "The judgement is the chat's.", "", summary_table(run), ""]
    for r in run.results:
        out += _detail(r)
    return "\n".join(out)


def write(run: Run, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(report(run), encoding="utf-8")
    data = asdict(run)
    for d, r in zip(data["results"], run.results, strict=True):
        d["verdict"] = r.verdict
    (out_dir / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def outside_repo(path: Path) -> Path:
    path = path.resolve()
    if path == REPO or REPO in path.parents:
        raise SetError(f"{path} is inside the repository; the answers are not committed. Use a scratch directory")
    return path


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None, *, build: Callable[..., tuple] | None = None) -> int:
    """build: (mode, db_path) -> (deps, gateway_for); tests pass the scripted model's or a fake provider's."""
    ap = argparse.ArgumentParser(prog="python -m tools.real_eval")
    ap.add_argument("--set", default=str(SET_FILE), help="a set file, or demo for the 12 demo questions")
    ap.add_argument("--only", help="ids, comma separated; the exchanges they follow are added")
    ap.add_argument("--real", action="store_true", help="the real model behind the spending cap (paid)")
    ap.add_argument("--confirm", action="store_true", help="with --real: send. Without it, nothing is sent")
    ap.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD, help="the run's own cap")
    ap.add_argument("--out", type=Path, help="where the report goes; outside the repository")
    ap.add_argument("--db", type=Path, default=seed.DEFAULT_OUT)
    ap.add_argument("--plans", action="store_true",
                    help="rule 1: run each question's hand-written plan, with no model, and stop")
    args = ap.parse_args(argv)
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:   # ✅ and ❌ on a Windows console (as tools/preflight.py)
        reconfigure(encoding="utf-8")
    mode = "real" if args.real else "scripted"
    try:
        catalog = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
        loaded = demo_set() if args.set == "demo" else load_set(Path(args.set), catalog)
        items = select(loaded, args.only.split(",") if args.only else None)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = outside_repo(args.out or Path(tempfile.gettempdir()) / "medagentpro-eval" / f"{stamp}-{mode}")
    except SetError as e:
        print(e)
        return 2
    if args.plans:
        conn = sqlite3.connect(f"file:{Path(args.db).as_posix()}?mode=ro", uri=True)
        try:
            results = check_plans(items, catalog, conn)
        finally:
            conn.close()
        print(plans_table(results))
        missing = without_plan(items, results)
        print(f"\n{len(items) - len(missing)} of {len(items)} questions have a plan that passes"
              + (f"; none for {', '.join(missing)}: a gap in the plan language, fix before a real run" if missing
                 else ""))
        return 1 if missing else 0
    if args.real and not args.confirm:
        print(f"{len(items)} exchanges on the real model, the run capped at {args.max_usd:.2f} USD, "
              f"report to {out_dir}. Nothing was sent. Add --confirm to run")
        return 2
    if build is None:
        import main as server
        build = server.build_deps
    try:
        deps, gateway_for = build(mode, args.db)
    except Exception as e:   # BudgetConfigError and a missing database say what to do
        print(e)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(out_dir / "operational.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    operational_log.addHandler(handler)
    level = operational_log.level
    operational_log.setLevel(logging.INFO)
    conn = sqlite3.connect(f"file:{Path(args.db).as_posix()}?mode=ro", uri=True)
    try:
        run = run_set(items, deps, gateway_for, conn, mode=mode, max_usd=args.max_usd,
                      on_result=lambda r: write(r, out_dir))
        write(run, out_dir)
    finally:
        conn.close()
        operational_log.removeHandler(handler)
        operational_log.setLevel(level)
        handler.close()
        ledger = getattr(deps.gateway, "ledger", None)
        if ledger is not None:
            ledger.close()
    print(summary_table(run))
    if run.stopped:
        print(f"\nStopped: {run.stopped}")
    if run.skipped:
        print(f"Skipped: {', '.join(run.skipped)}")
    print(f"\nspent {run.spent:.4f} USD" + (f", remaining {run.remaining:.4f}" if run.remaining is not None else ""))
    print(f"report: {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
