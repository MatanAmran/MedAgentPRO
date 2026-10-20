"""
tools/real_eval.py and the held-out set (docs/notes/75_OFFSCRIPT_SET.md, chat 18.10, 12:30), with no model:
the tool runs on the scripted model, and on the real path with the fake provider (tools/fake_real_server.py)
where the budget, the rate and the run's cap are under test. Every exchange of the set loads, every SQL
runs, and none of its questions is in a prompt.
"""

import json
import re
import sqlite3

import pytest

import main
from demo_data import seed
from model_client.budget import BudgetConfig, BudgetedGateway, SpendLedger
from model_client.client import LiteLLMGateway
from query_assistance.compiler import HERE as QA_HERE
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, MemoryStore
from tools import real_eval as ev
from tools.fake_real_server import CONFIG, FakeProvider

NOTE = ev.REPO / "docs" / "notes" / "75_OFFSCRIPT_SET.md"
CATALOG = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")

LATE_1_13 = "SELECT DISTINCT supplier_id FROM commitments WHERE week BETWEEN 1 AND 13 AND delivered_at > due_date"
SMALL = [
    {"id": "S1", "user": "AP-01", "question": "Which suppliers were late in weeks 1 to 13?",
     "expect": {"status": ["answered"], "period": {"col": "week", "from": "1", "to": "13"},
                "metric": ["late_by_delivery"]},
     "sql": [{"query": LATE_1_13, "compare": "total"}]},
    {"id": "S2", "user": "AP-01", "after": "S1", "question": "And in weeks 14 to 26?",
     "expect": {"status": ["answered"], "period": {"col": "week", "from": "14", "to": "26"},
                "forbid_in_examined": ["between 1 and 13"]}},
    {"id": "S3", "user": "AP-01", "question": "Why is S-014 late so often?",
     "expect": {"status": ["referred_to_investigation"]}},
    {"id": "S4", "user": "AP-01", "question": "Update the promised date of PO-87213 to 20 October.",
     "expect": {"status": ["action_declined"]}},
    {"id": "S5", "user": "AP-01", "question": "What did S-099 supply?",
     "expect": {"status": ["answered"], "not_held": ["S-099"]}},
]


def write_set(tmp_path, items):
    path = tmp_path / "set.json"
    path.write_text(json.dumps({"exchanges": items}), encoding="utf-8")
    return path


@pytest.fixture
def conn(db_path):
    c = sqlite3.connect(db_path)
    yield c
    c.close()


def fake_real(db_path, tmp_path, monkeypatch, clock=None, **override):
    """The real path's dependencies over the fake provider: no key, no network, a ledger of its own."""
    monkeypatch.setenv("MEDAGENTPRO_CHAIN_KEY", "test-chain-key")   # never backend/.env
    cfg = BudgetConfig.from_env({**CONFIG, "MODEL_MAX_USD_PER_EXCHANGE": "0.25", **override})
    inner = LiteLLMGateway(CircuitBreaker("model_provider", MemoryStore()), completion=FakeProvider(),
                           api_key=None, provider="anthropic")
    kw = {"clock": clock} if clock else {}
    gateway = BudgetedGateway(inner, cfg, SpendLedger(tmp_path / "spend.json"), **kw)
    return main.build_deps("real", db_path, gateway=gateway)


# --------------------------------------------------------------------------
# the set
# --------------------------------------------------------------------------

def test_the_set_loads_with_31_exchanges():
    items = ev.load_set(ev.SET_FILE, CATALOG)
    assert len(items) == 31
    assert [i["id"] for i in items if i.get("after")] == ["F1b", "F1c", "F2"]


def test_the_set_holds_the_notes_questions_as_written():
    """The questions are the chat's, word for word, and in the note's order."""
    rows = re.findall(r"^\| \*\*(\w+)\*\* \| [^|]+ \| (.+?) \| ", NOTE.read_text(encoding="utf-8"), re.M)
    assert [(i["id"], i["question"]) for i in ev.load_set()] == rows


def test_every_sql_runs_and_a_value_is_one_cell(conn):
    for it in ev.load_set():
        for alt in it.get("sql", []):
            rows = conn.execute(alt["query"]).fetchall()
            if alt["compare"] == "value":
                assert len(rows) == 1 and len(rows[0]) == 1 and rows[0][0] is not None, it["id"]
        if it.get("forbid_sql"):
            assert conn.execute(it["forbid_sql"]).fetchall(), it["id"]


def test_the_expected_counts_on_the_seeded_store(conn):
    """Pinned, so a change to seed.py that moves an expectation is seen here first."""
    first = {it["id"]: it["sql"][0] for it in ev.load_set() if it.get("sql")}

    def n(i):
        rows = conn.execute(first[i]["query"]).fetchall()
        return rows[0][0] if first[i]["compare"] == "value" else len(rows)
    assert {i: n(i) for i in ("A1", "A3", "B1", "B4", "C1", "D4", "F1a", "F1b", "F1c", "G2")} == {
        "A1": 28, "A3": 25, "B1": 138, "B4": 0, "C1": 3, "D4": 29, "F1a": 3, "F1b": 4, "F1c": 3, "G2": 256}
    assert round(n("C2"), 2) == 382901.05


def test_no_question_of_the_set_is_in_a_prompt():
    """Rule of 11:40: the held-out questions never become examples."""
    prompts = [p.read_text(encoding="utf-8").casefold() for p in (QA_HERE / "templates").glob("*.md")]
    prompts.append((seed.HERE / "intent_analysis.md").read_text(encoding="utf-8").casefold())
    for it in ev.load_set():
        assert not any(it["question"].casefold() in t for t in prompts), it["id"]


def test_only_f1b_is_also_a_demo_question():
    """F1b is demo question 2 word for word, after another first question (Germany): the chat's choice.
    Any other overlap with the scripted demo would test the script, not the model."""
    scripts = (seed.HERE / "scripted_exchanges.json").read_text(encoding="utf-8").casefold()
    assert [it["id"] for it in ev.load_set() if it["question"].casefold() in scripts] == ["F1b"]


@pytest.mark.parametrize("change, says", [
    (lambda s: s[1].update(after="S9"), "does not come before"),
    (lambda s: s[1].update(user="PL-02"), "another user's"),
    (lambda s: s[0].update(user="XX-01"), "not a demo user"),
    (lambda s: s[0]["expect"].update(status=["fine"]), "expect.status"),
    (lambda s: s[0]["sql"][0].update(compare="sum"), "compare"),
    (lambda s: s[0]["expect"].update(metric=["lateness"]), "no metric"),
    (lambda s: s.append(dict(s[0])), "used twice"),
])
def test_a_broken_set_is_refused(tmp_path, change, says):
    items = json.loads(json.dumps(SMALL))
    change(items)
    with pytest.raises(ev.SetError, match=says):
        ev.load_set(write_set(tmp_path, items), CATALOG)


def test_only_adds_the_exchanges_followed():
    items = ev.load_set()
    assert [i["id"] for i in ev.select(items, ["F1c", "B2"])] == ["B2", "F1a", "F1b", "F1c"]
    assert [i["id"] for i in ev.select(items, ["F2"])] == ["A1", "F2"]
    with pytest.raises(ev.SetError, match="Z9"):
        ev.select(items, ["Z9"])


# --------------------------------------------------------------------------
# the run, on the scripted model
# --------------------------------------------------------------------------

def test_scripted_run_passes_its_checks(db_path, conn):
    deps, gateway_for = main.build_deps("scripted", db_path)
    run = ev.run_set(SMALL, deps, gateway_for, conn)
    by = {r.id: r for r in run.results}
    assert [r.verdict for r in run.results] == [ev.OK] * 5, ev.summary_table(run)
    assert by["S1"].counts_ok and by["S1"].counts[0]["expected"] == 28
    assert by["S1"].examined == {"period": True, "metric": True, "forbid": None, "gap": None, "not_held": None}
    assert by["S5"].examined["not_held"] is True
    assert by["S2"].response["understood_as"] and by["S2"].examined["forbid"] is True
    assert by["S4"].status == "action_declined" and not by["S4"].calls[1:]
    assert [c["stage"] for c in by["S1"].calls] == ["intent_analysis", "synthesis", "support_gate",
                                                    "question_classification"]
    assert [s["stage"] for s in by["S1"].stages][:2] == ["intent", "retrieval"]
    assert run.spent == 0 and run.stopped is None


def test_a_set_question_with_no_script_ends_as_not_completed(db_path, conn):
    """The held-out set on the scripted model: nothing is answered, and the tool says so (❌)."""
    deps, gateway_for = main.build_deps("scripted", db_path)
    run = ev.run_set(ev.select(ev.load_set(), ["A1"]), deps, gateway_for, conn)
    assert run.results[0].status == "request_not_completed" and run.results[0].verdict == ev.FAIL


def test_a_wrong_expectation_is_a_failure(db_path, conn):
    items = json.loads(json.dumps(SMALL[:1] + SMALL[2:3]))
    items[0]["sql"] = [{"query": LATE_1_13 + " AND supplier_id <> 'S-001'", "compare": "total"}]
    items[1]["expect"]["status"] = ["answered"]
    deps, gateway_for = main.build_deps("scripted", db_path)
    run = ev.run_set(items, deps, gateway_for, conn)
    assert run.results[0].counts_ok is False and run.results[0].verdict == ev.FAIL
    assert run.results[1].status_ok is False and run.results[1].verdict == ev.FAIL


def test_rows_that_must_not_be_shown_are_caught(db_path, conn):
    items = json.loads(json.dumps(SMALL[:1]))
    items[0]["forbid_sql"] = "SELECT supplier_id FROM suppliers WHERE supplier_id = 'S-038'"
    deps, gateway_for = main.build_deps("scripted", db_path)
    r = ev.run_set(items, deps, gateway_for, conn).results[0]
    assert r.forbidden_ok is False and r.leaked == ["S-038"] and r.verdict == ev.FAIL


def test_a_hint_that_does_not_hold_is_not_a_failure(db_path, conn):
    items = json.loads(json.dumps(SMALL[:1]))
    items[0]["expect"]["period"] = {"col": "week", "from": "14", "to": "26"}
    deps, gateway_for = main.build_deps("scripted", db_path)
    r = ev.run_set(items, deps, gateway_for, conn).results[0]
    assert r.examined["period"] is False and r.verdict == ev.HINT


# --------------------------------------------------------------------------
# the real path, with the fake provider
# --------------------------------------------------------------------------

def test_costs_are_reported_per_call_and_per_exchange(db_path, conn, tmp_path, monkeypatch):
    deps, gateway_for = fake_real(db_path, tmp_path, monkeypatch)
    run = ev.run_set(SMALL[:2], deps, gateway_for, conn, mode="real")
    first = run.results[0]
    assert first.verdict == ev.OK and first.cost["exchange_usd"] > 0
    assert all(c["usd"] and c["input_tokens"] for c in first.calls)
    assert run.spent == pytest.approx(sum(r.cost["exchange_usd"] for r in run.results))
    assert run.remaining == pytest.approx(5 - run.spent)
    assert run.results[1].response.get("understood_as")


def test_the_run_stops_before_it_could_pass_its_cap(db_path, conn, tmp_path, monkeypatch):
    deps, gateway_for = fake_real(db_path, tmp_path, monkeypatch)
    run = ev.run_set(SMALL, deps, gateway_for, conn, mode="real", max_usd=0.26)   # one exchange may take 0.25
    assert [r.id for r in run.results] == ["S1"]
    assert run.stopped.startswith("before S2") and 0 < run.spent <= 0.26
    nothing = ev.run_set(SMALL, deps, gateway_for, conn, mode="real", max_usd=0.2)
    assert nothing.results == [] and nothing.stopped.startswith("before S1")


def test_a_rate_refusal_is_waited_out_then_asked_again(db_path, conn, tmp_path, monkeypatch):
    now = [1000.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s
    deps, gateway_for = fake_real(db_path, tmp_path, monkeypatch, clock=lambda: now[0],
                                  MODEL_MAX_EXCHANGES_PER_MINUTE="1")
    run = ev.run_set(SMALL[2:4], deps, gateway_for, conn, mode="real", sleep=sleep, clock=lambda: now[0])
    assert [r.verdict for r in run.results] == [ev.OK, ev.OK]
    assert run.results[0].rate_waits == [] and run.results[1].rate_waits == [60] and slept == [61]


def test_a_rate_refusal_that_does_not_end_is_reported(db_path, conn, tmp_path, monkeypatch):
    deps, gateway_for = fake_real(db_path, tmp_path, monkeypatch, clock=lambda: 0.0,
                                  MODEL_MAX_EXCHANGES_PER_MINUTE="1")
    run = ev.run_set(SMALL[2:4], deps, gateway_for, conn, mode="real", sleep=lambda s: None)
    last = run.results[1]
    assert len(last.rate_waits) == ev.MAX_RATE_WAITS and last.status == "request_not_completed"


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------

def test_real_without_confirm_builds_nothing(tmp_path, capsys):
    def build(*a):
        raise AssertionError("built")
    assert ev.main(["--real", "--out", str(tmp_path / "out")], build=build) == 2
    assert "Nothing was sent" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_a_report_inside_the_repository_is_refused(capsys):
    assert ev.main(["--out", str(ev.REPO / "eval-out")]) == 2
    assert "inside the repository" in capsys.readouterr().out
    assert not (ev.REPO / "eval-out").exists()


def test_the_command_writes_the_report_and_the_log(db_path, tmp_path, capsys):
    out = tmp_path / "out"
    code = ev.main(["--set", str(write_set(tmp_path, SMALL)), "--only", "S2,S5", "--db", str(db_path),
                    "--out", str(out)], build=lambda mode, db: main.build_deps(mode, db))
    assert code == 0
    printed = capsys.readouterr().out
    assert "| S1 |" in printed and "| S5 |" in printed and "| S3 |" not in printed
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## S2" in md and "Understood as" in md and "Examined" in md and "**Answer:**" in md
    data = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in data["results"]] == ["S1", "S2", "S5"]
    assert data["results"][2]["response"]["readings"][0]["not_held"][0]["value"] == "S-099"
    assert "model call:" in (out / "operational.log").read_text(encoding="utf-8")


def test_a_date_period_is_found_quoted_as_examined_renders_it():
    """Real run, 18.10: Examined quotes a date, so the hint read every date period as missing."""
    item = {"expect": {"period": {"col": "delivered_at", "from": "2026-09-01", "to": "2026-09-30"}}}
    seen = {"readings": [{"checked": "Examined: purchase order lines where delivery date between "
                                     "'2026-09-01' and '2026-09-30'"}]}
    assert ev.check_examined(item, seen, CATALOG)["period"] is True
    seen["readings"][0]["checked"] = seen["readings"][0]["checked"].replace("2026", "2025")
    assert ev.check_examined(item, seen, CATALOG)["period"] is False


def test_the_demo_set_is_the_14_questions_with_2_and_13_after_1():
    """Q13 and Q14 (scripted demo, 19.10) are probes H1b and H11; Q14 is asked as PL-02."""
    items = ev.demo_set()
    assert [i["id"] for i in items] == [f"Q{n:02d}" for n in range(1, 15)]
    assert [(i["id"], i["after"]) for i in items if i.get("after")] == [("Q02", "Q01"), ("Q13", "Q01")]
    assert items[6]["expect"]["status"] == ["referred_to_investigation"]
    assert items[11]["expect"]["status"] == ["action_declined"]
    assert [i["user"] for i in items] == ["AP-01"] * 13 + ["PL-02"]


def test_the_demo_set_passes_on_the_scripted_model(db_path, conn):
    deps, gateway_for = main.build_deps("scripted", db_path)
    run = ev.run_set(ev.demo_set(), deps, gateway_for, conn)
    assert [r.status_ok for r in run.results] == [True] * 14, ev.summary_table(run)


# --------------------------------------------------------------------------
# rule 1: a hand-written plan for every question, with no model (chat, 18.10, 13:40)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["offscript", "demo"])
def test_every_question_of_both_sets_has_a_plan_that_passes(conn, which):
    """45 questions: a question no plan passes for is a gap in the plan language, fixed before a real run."""
    items = ev.load_set(ev.SET_FILE, CATALOG) if which == "offscript" else ev.demo_set()
    results = ev.check_plans(items, CATALOG, conn)
    assert ev.without_plan(items, results) == [], ev.plans_table(results)
    assert len(items) == {"offscript": 31, "demo": 14}[which]


def test_c4_has_a_plan_for_each_average(conn):
    results = ev.check_plans(ev.select(ev.load_set(), ["C4"]), CATALOG, conn)
    assert [(r.alternative, r.passed) for r in results] == [(1, True), (2, True)]
    assert [next(c["expected"] for c in r.counts if c["matched"]) for r in results] == [8.25, 3.65]


def test_a_plan_with_the_wrong_year_fails_the_check(conn):
    item = json.loads(json.dumps(ev.select(ev.load_set(), ["B1"])[0]))
    text = json.dumps(item["plan"]).replace("2026-11", "2025-11")
    item["plan"] = json.loads(text)
    r = ev.check_plans([item], CATALOG, conn)[0]
    assert not r.passed and r.counts_ok is False and r.examined["forbid"] is False


def test_a_plan_the_compiler_refuses_fails_the_check(conn):
    item = json.loads(json.dumps(ev.select(ev.load_set(), ["C4"])[0]))
    item["plan"] = [json.loads(json.dumps(item["plan"][0]).replace('"days_late"', '"late_by_delivery"'))]
    r = ev.check_plans([item], CATALOG, conn)[0]
    assert not r.passed and r.error.startswith("CompileError type_mismatch")


def test_a_question_with_no_plan_is_named(conn):
    item = dict(ev.select(ev.load_set(), ["A4"])[0], plan=[])
    results = ev.check_plans([item], CATALOG, conn)
    assert ev.without_plan([item], results) == ["A4"] and results[0].error == "no plan"


def test_a_follow_up_plan_counts_its_expected_rewrite(conn):
    """Q02 declares "late" ambiguous: a span of the rewrite, not of "And in weeks 14 to 26?"."""
    q02 = ev.select(ev.demo_set(), ["Q02"])
    assert q02[1]["rewritten"] == "Which suppliers were late in weeks 14 to 26?"
    assert all(r.passed for r in ev.check_plans(q02, CATALOG, conn))
    bare = [q02[0], {k: v for k, v in q02[1].items() if k != "rewritten"}]
    assert "span_not_in_question" in ev.check_plans(bare, CATALOG, conn)[1].error


def test_the_plans_command_says_how_many_pass(db_path, capsys):
    assert ev.main(["--plans", "--db", str(db_path)]) == 0
    assert "31 of 31 questions have a plan that passes" in capsys.readouterr().out
    assert ev.main(["--plans", "--set", "demo", "--db", str(db_path)]) == 0
    assert "14 of 14" in capsys.readouterr().out


def test_q11_on_the_real_model_may_be_answered():
    """Its no_supported_assertion is the scripted gate's (scripted_gate): the real one may support it."""
    assert ev.select(ev.demo_set(), ["Q11"])[0]["expect"]["status"] == ["no_supported_assertion", "answered"]
