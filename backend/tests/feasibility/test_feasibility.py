"""
Feasibility checks: one test per row of constraints.py. No network, no model call.

Checks marked needs="map" read LiteLLM's local model map of the pinned version; where LiteLLM or its
map is absent they are skipped, and say so. The model, caps and prices are the defaults below, in
code: no test reads backend/.env, so CI and this machine run the same checks and a failure never prints
a local value (chat, R9, 14.10, 17:50). The configured values are checked by tools.preflight --real.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import json
import os
import re
import sqlite3

import pytest
from constraints import CONSTRAINTS

from api import routes  # noqa: F401  (imports the server's modules, as the server does)
from demo_data import seed
from demo_data.scripted import ScriptedDemoGateway, load_scripts
from model_client import budget as b
from model_client import client as client_mod
from model_client.client import ModelConfig, ModelGateway, ModelResponse
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import calls, compiler, contract, prompts, synthesis
from query_assistance import pipeline as pl
from query_assistance.compiler import ROW_LIMIT, Catalog, CompileError, compile_intent, execute, intent_validator
from shared.circuit_breaker import CircuitBreaker, MemoryStore

CAT = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
BUNDLE = json.loads((compiler.HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))["schemas"]
SENT = {"question_rewriting": BUNDLE["question_rewriting"]["schema"],
        "synthesis": BUNDLE["synthesis"]["schema"],
        "support_scoring": BUNDLE["support_scoring"]["schema"]}
ROLES = sorted({r for e in CAT.entities.values() for r in (e.get("entitlement") or {})})
USERS = {"APPROVER": {"role": "APPROVER", "user_id": "AP-01"}, "PLANNER": {"role": "PLANNER", "user_id": "PL-02"}}

ANTHROPIC_MAX_OPTIONAL = 24
ANTHROPIC_MAX_UNION = 16
MIN_WORST_CASE_EXCHANGES = 10

DEFAULTS = {"MODEL_ID": "claude-sonnet-5-5", "MODEL_MAX_OUTPUT_TOKENS": "2000", "MODEL_MAX_USD_PER_EXCHANGE": "0.25",
            "MODEL_BUDGET_USD": "3.5", "MODEL_TEMPERATURE": ""}


def settings() -> dict:
    """The demo defaults, never backend/.env (chat, R9, 14.10, 17:50)."""
    return dict(DEFAULTS)


def model_entry() -> dict:
    try:
        import litellm
    except ImportError:
        pytest.skip("needs LiteLLM's local map: LiteLLM is not installed here")
    if not getattr(litellm, "model_cost", None):
        pytest.skip("needs LiteLLM's local map: the map is empty here")
    name = settings()["MODEL_ID"]
    try:
        return b.map_entry(name if "/" in name else f"anthropic/{name}")
    except KeyError:
        pytest.fail(f"{name} is not in LiteLLM's local map")


MAX_SELECT = BUNDLE["intent_analysis"]["schema"]["$defs"]["plan"]["properties"]["SELECT"].get("maxItems", 99)


def _plan_all_columns(table: str) -> dict:
    """As many columns as a plan may select, widest first by their stored text."""
    cols = list(CAT.entities[table]["columns"])[:MAX_SELECT]
    return {"SELECT": [{"expr": {"col": f"{table}.{c}"}, "as": c} for c in cols], "FROM": table, "LIMIT": ROW_LIMIT}


def worst_materials(db_path) -> tuple[str, list]:
    """The largest material retrieval can hand to synthesis: three readings, ROW_LIMIT rows each, every
    column of the widest table."""
    q = "Show me everything."
    conn = sqlite3.connect(db_path)
    best = None
    for table in CAT.entities:
        intent = {"disposition": "retrieve", "readings": [
            {"label": f"r{i}", "plan": _plan_all_columns(table)} for i in range(1, 4)]}
        compiled = compile_intent(intent, prompts.compiler_conversation([], q, None), USERS["APPROVER"], CAT,
                                  intent_validator())
        mats = []
        for reading, r in zip(intent["readings"], compiled.readings, strict=True):
            res = execute(r, conn)
            mats.append(synthesis.material(r.label, reading["plan"], res.rows, res.truncated, CAT))
        size = sum(len(json.dumps(m.rows, default=str)) for m in mats)
        if best is None or size > best[0]:
            best = (size, mats)
    conn.close()
    return q, best[1]


def walk(node, fn, path=()):
    fn(node, path)
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, fn, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, fn, path + (i,))


def complexity(schema: dict) -> dict:
    out = {"optional": 0, "union": 0, "refs": 0, "objects_open": [], "patterns": [], "enum_case": []}

    def f(n, path):
        if not isinstance(n, dict):
            return
        if "properties" in n:
            req = set(n.get("required", []))
            out["optional"] += sum(1 for p in n["properties"] if p not in req)
        if "anyOf" in n or isinstance(n.get("type"), list):
            out["union"] += 1
        if "$ref" in n:
            out["refs"] += 1
        if n.get("type") == "object" and n.get("additionalProperties") is not False:
            out["objects_open"].append("/".join(map(str, path)))
        if isinstance(n.get("pattern"), str):
            out["patterns"].append(n["pattern"])
        if isinstance(n.get("enum"), list):
            folded = [str(v).casefold() for v in n["enum"]]
            if len(folded) != len(set(folded)):
                out["enum_case"].append("/".join(map(str, path)))
    walk(schema, f)
    return out


# --------------------------------------------------------------------------
# technical
# --------------------------------------------------------------------------

def test_T01_model_is_in_the_local_map_with_prices():
    e = model_entry()
    assert e.get("input_cost_per_token") and e.get("output_cost_per_token")


def test_T02_no_temperature_for_a_model_that_takes_none():
    e = model_entry()
    if e.get("supports_sampling_params") is False:
        assert not settings()["MODEL_TEMPERATURE"], "MODEL_TEMPERATURE is set; this model takes no temperature"


def test_T03_model_supports_native_structured_output():
    assert model_entry().get("supports_native_structured_output") is True


def test_T04_output_cap_within_the_models_limit():
    limit = model_entry().get("max_output_tokens")
    assert limit and int(settings()["MODEL_MAX_OUTPUT_TOKENS"]) <= limit


def test_T05_largest_prompt_fits_the_context_window(db_path):
    window = model_entry().get("max_input_tokens")
    q, mats = worst_materials(db_path)
    largest = max(pl._path_inputs(q, None, mats))
    intent = b.estimate_input_tokens(prompts.render_intent(CAT, "APPROVER", q, None,
                                                           prompts.load(seed.HERE / "intent_analysis.md")))
    rewrite = prompts.HISTORY_BUDGET_TOKENS * 2 + b.estimate_input_tokens(prompts.load("rewrite.md").body)
    worst = max(largest, intent, rewrite) + int(settings()["MODEL_MAX_OUTPUT_TOKENS"])
    assert worst <= window, f"worst prompt ~{worst} tokens > window {window}"


@pytest.mark.parametrize("stage", sorted(SENT))
def test_T06_sent_schemas_within_the_providers_limits(stage):
    c = complexity(calls.provider_schema(SENT[stage]))
    assert c["optional"] <= ANTHROPIC_MAX_OPTIONAL, c
    assert c["union"] <= ANTHROPIC_MAX_UNION, c
    assert c["refs"] == 0, "a $ref left after LiteLLM unpacks: recursive?"
    assert not c["objects_open"], c["objects_open"]


@pytest.mark.parametrize("stage", sorted(SENT))
def test_T07_patterns_use_supported_features(stage):
    bad = re.compile(r"\(\?[=!<]|\\[1-9]|\\b|\\B")
    for pat in complexity(calls.provider_schema(SENT[stage]))["patterns"]:
        assert not bad.search(pat), f"{stage}: unsupported regex feature in {pat!r}"


@pytest.mark.parametrize("stage", sorted(SENT))
def test_T08_no_enum_values_differing_only_in_case(stage):
    assert not complexity(calls.provider_schema(SENT[stage]))["enum_case"]


def test_T09_intent_schema_not_sent_while_it_exceeds(db_path):
    c = complexity(calls.provider_schema(BUNDLE["intent_analysis"]["schema"]))
    exceeds = c["optional"] > ANTHROPIC_MAX_OPTIONAL or c["union"] > ANTHROPIC_MAX_UNION
    seen: list[ModelConfig] = []

    class Capture(ModelGateway):
        def invoke(self, prompt, config):
            seen.append(config)
            return ModelResponse(json.dumps({"disposition": "refer_to_investigation"}), "x", 1, 1,
                                 config.prompt_identity)
    audit = AuditRepository(MemoryStorage(), CircuitBreaker("s", MemoryStore()), b"k")
    pl.run("Why?", [], USERS["APPROVER"], pl.Deps(Capture(), "x", CAT, pl.connect_readonly(db_path), audit,
                                                  prompts.load(seed.HERE / "intent_analysis.md")))
    intent_cfg = [cfg for cfg in seen if cfg.prompt_identity.startswith("intent")][0]
    if exceeds:
        assert intent_cfg.output_schema is None
    else:
        pytest.xfail("the intent schema now fits the provider's limits: structured output could be turned on")


def test_T10_litellm_pinned_and_local_map():
    req = (b.BACKEND / "requirements.txt").read_text(encoding="utf-8")
    pinned = re.search(r"^litellm==([\d.]+)\s*$", req, re.M)
    assert pinned, "litellm is not pinned to an exact version in requirements.txt"
    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"
    try:
        installed = importlib.metadata.version("litellm")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("LiteLLM is not installed here")
    assert installed == pinned.group(1)


def test_T11_worst_path_fits_the_exchange_cap(db_path):
    e = model_entry()
    s = settings()
    price_in, price_out = e["input_cost_per_token"], e["output_cost_per_token"]
    q, mats = worst_materials(db_path)
    out_tokens = int(s["MODEL_MAX_OUTPUT_TOKENS"])
    cost = sum(n * price_in + out_tokens * price_out for n in pl._path_inputs(q, None, mats))
    cap = float(s["MODEL_MAX_USD_PER_EXCHANGE"])
    assert cost <= cap, (f"the largest retrieval needs ~${cost:.3f} for synthesis to the second gate, over the "
                         f"${cap} cap: such a question always ends with the exchange-cap notice. Lower ROW_LIMIT "
                         f"or raise the cap")


def test_T12_budget_covers_enough_worst_case_exchanges():
    s = settings()
    assert float(s["MODEL_BUDGET_USD"]) / float(s["MODEL_MAX_USD_PER_EXCHANGE"]) >= MIN_WORST_CASE_EXCHANGES


@pytest.mark.parametrize("role", ROLES)
def test_T13_every_table_and_metric_runs_on_the_installed_sqlite(db_path, role):
    conn = sqlite3.connect(db_path)
    q = "Show me everything."
    conv = prompts.compiler_conversation([], q, None)
    for table in prompts.visible_tables(CAT, role):
        intent = {"disposition": "retrieve", "readings": [{"label": "t", "plan": _plan_all_columns(table)}]}
        r, = compile_intent(intent, conv, USERS[role], CAT, intent_validator()).readings
        execute(r, conn)
    for name, m in CAT.metrics.items():
        if m["entity"] not in prompts.visible_tables(CAT, role):
            continue
        value = {"value": True} if m["type"] == "boolean" else {"value": 0}
        op = "EQ" if m["type"] == "boolean" else "GT"
        plan = {"SELECT": [{"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}], "FROM": m["entity"],
                "WHERE": {op: [{"metric": name}, value]}}
        r, = compile_intent({"disposition": "retrieve", "readings": [{"label": "m", "plan": plan}]}, conv,
                            USERS[role], CAT, intent_validator()).readings
        execute(r, conn)
    conn.close()


def test_T15_the_model_thinks_always_and_we_send_nothing_that_sets_it():
    """Found in task 10a: output_tokens near one per visible character in every stage. The map says the
    model always thinks, so the hidden share is not something a prompt or a parameter of ours removes."""
    assert model_entry().get("thinking_always_on") is True
    sent = {}

    class Resp:
        class _C:
            class message:
                content = "{}"
            finish_reason = "stop"
        choices = [_C]
        usage = {"prompt_tokens": 1, "completion_tokens": 3, "completion_tokens_details": {"reasoning_tokens": 2}}
        model = "m"
    gw = client_mod.LiteLLMGateway(CircuitBreaker("m", MemoryStore()), completion=lambda **k: sent.update(k) or Resp())
    r = gw.invoke("hi", ModelConfig("m", prompt_identity="x@1", max_tokens=2000))
    assert not {"thinking", "reasoning_effort"} & set(sent) and r.reasoning_tokens == 2 and r.output_tokens == 3


def test_T14_no_hidden_retries_in_litellm():
    sent = {}

    class Resp:
        class _C:
            class message:
                content = "{}"
            finish_reason = "stop"
        choices = [_C]
        usage = {"prompt_tokens": 1, "completion_tokens": 1}
        model = "m"
    gw = client_mod.LiteLLMGateway(CircuitBreaker("m", MemoryStore()), completion=lambda **k: sent.update(k) or Resp())
    gw.invoke("hi", ModelConfig("m", prompt_identity="x@1"))
    assert sent.get("num_retries") == 0 and sent.get("max_retries") == 0
    assert "temperature" not in sent, "temperature sent though none was configured (R1)"
    assert sent.get("timeout") == client_mod.MODEL_REQUEST_TIMEOUT_SECONDS


# --------------------------------------------------------------------------
# logical
# --------------------------------------------------------------------------

def _grouped(r: dict) -> bool:
    """Task 7, Q3: the first identifier repeats and another identifier exists (as the screen decides)."""
    ids, rows = r.get("identifiers", []), r.get("rows", [])
    keys = [row.get(ids[0]) for row in rows] if ids else []
    return len(ids) >= 2 and len(set(keys)) < len(keys)


# Task 8: what each behaviour named in a script's expect.shows means in the response
SHOWS = {
    "two_readings": lambda r: len(r["readings"]) == 2
    and all(x["checked"].startswith("Examined") for x in r["readings"]),
    "one_reading": lambda r: len(r["readings"]) == 1 and r["readings"][0]["checked"].startswith("Examined"),
    "understood_as": lambda r: bool(r.get("understood_as")),
    "grouped": lambda r: _grouped(r["readings"][0]),
    "gap": lambda r: bool(r["gaps"]) and all(g.get("text") for g in r["gaps"]),
    "recorded_reason": lambda r: "recorded as" in r["readings"][0]["answer"]
    and "because" not in r["readings"][0]["answer"],
    "referral": lambda r: r.get("offer_investigation") is True,
    "no_match": lambda r: r["readings"][0].get("notice") == "No records matched.",
    "not_held": lambda r: bool(r["readings"][0].get("not_held")),
    "withheld": lambda r: bool(r.get("withheld")),
    "request_for_input": lambda r: bool(r.get("request_for_input")) and r["readings"] == [],
    "action_declined": lambda r: r["readings"] == [] and r.get("notice") == contract.ACTION_DECLINED,
}


def _run_scripted(db_path, scripts, question, history):
    audit = AuditRepository(MemoryStorage(), CircuitBreaker("s", MemoryStore()), b"k")
    d = pl.Deps(ScriptedDemoGateway(question, scripts), "scripted-demo", CAT,
                pl.connect_readonly(db_path), audit, prompts.load(seed.HERE / "intent_analysis.md"))
    return pl.run(question, history, USERS["APPROVER"], d), audit


def test_L01_every_scripted_demo_question_runs_through_the_real_checks(db_path):
    """Task 8: each scripted question ends exactly as its expect says, through every real check, and
    shows each behaviour it is in the set for. A follow-up runs after the question it follows."""
    scripts = load_scripts()
    assert len(scripts) >= 12
    for entry in scripts.values():
        expect = entry["expect"]
        history = []
        if expect.get("follows"):
            before, _ = _run_scripted(db_path, scripts, expect["follows"], [])
            history = [before.exchange]
        out, audit = _run_scripted(db_path, scripts, entry["question"], history)
        r = out.response
        assert r["outcome"] == expect["outcome"], (
            entry["question"], r["outcome"], audit.storage.get_investigation(out.exchange_id).get("failure"))
        for shown in expect["shows"]:
            assert SHOWS[shown](r), (entry["question"], shown, r)


def test_L01b_every_behaviour_named_is_one_the_test_knows():
    named = {s for e in load_scripts().values() for s in e["expect"]["shows"]}
    assert named <= set(SHOWS), named - set(SHOWS)


@pytest.mark.parametrize("role", ROLES)
def test_L02_tables_shown_to_a_role_are_exactly_those_it_may_read(db_path, role):
    q = "Show me everything."
    conv = prompts.compiler_conversation([], q, None)
    shown = set(prompts.visible_tables(CAT, role))
    schema_text = prompts.render_schema(CAT, role)
    for table in CAT.entities:
        intent = {"disposition": "retrieve", "readings": [{"label": "t", "plan": _plan_all_columns(table)}]}
        try:
            compile_intent(intent, conv, USERS[role], CAT, intent_validator())
            readable = True
        except CompileError as e:
            assert e.code.startswith("entitlement"), e
            readable = False
        assert readable == (table in shown), table
        assert (table in schema_text) == readable or not readable and table not in schema_text


@pytest.mark.parametrize("role", ROLES)
def test_L03_every_metric_compiles_for_every_role(role):
    conv = prompts.compiler_conversation([], "Show me everything.", None)
    for name, m in CAT.metrics.items():
        if m["entity"] not in prompts.visible_tables(CAT, role):
            continue
        value = {"value": True} if m["type"] == "boolean" else {"value": 0}
        op = "EQ" if m["type"] == "boolean" else "GT"
        plan = {"SELECT": [{"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}], "FROM": m["entity"],
                "WHERE": {op: [{"metric": name}, value]}}
        compile_intent({"disposition": "retrieve", "readings": [{"label": "m", "plan": plan}]}, conv,
                       USERS[role], CAT, intent_validator())


def test_L04_prompt_examples_are_checked_elsewhere():
    """The check itself lives in tests/demo_data/test_demo_prompt.py; this keeps it from disappearing."""
    path = b.BACKEND / "tests" / "demo_data" / "test_demo_prompt.py"
    assert "def test_example_validates_compiles_and_runs" in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# the table and the checks stay in step
# --------------------------------------------------------------------------

def test_every_constraint_has_a_check_and_every_check_a_constraint():
    here = {n.split("_")[1] for n, f in globals().items() if n.startswith("test_") and inspect.isfunction(f)
            and re.match(r"test_[TLV]\d\d_", n)}
    rows = {c.id for c in CONSTRAINTS if c.kind != "live"}
    assert rows == here, {"rows without a check": sorted(rows - here), "checks without a row": sorted(here - rows)}
    ids = [c.id for c in CONSTRAINTS]
    assert len(ids) == len(set(ids))


def test_a_live_row_records_what_the_smoke_test_saw_and_only_a_live_row_does():
    """R9: a live row marked confirmed or partial says what was observed; a row not yet run says nothing."""
    for c in CONSTRAINTS:
        if c.kind != "live":
            assert not c.result and not c.observed, c.id
            continue
        assert c.result in ("", "confirmed", "partial"), c.id
        assert bool(c.result) == bool(c.observed), c.id


def test_L05_every_demo_question_fits_the_default_cap_on_the_real_path(db_path, tmp_path):
    """Task 9: found while building the fake provider. The largest demo question (30 rows) reserves
    about USD 0.14 for its worst path (0.135 before the task 10a prompts); the demo's default cap is 0.25.
    Tighten the cap, or grow a question's records, and this fails before the demo does."""
    from fastapi.testclient import TestClient

    from tools.fake_real_server import build_app
    app, _ = build_app(db_path, tmp_path, MODEL_MAX_USD_PER_EXCHANGE="0.25")
    ap = {"X-Demo-User": "AP-01"}
    with TestClient(app) as c:
        cid = None
        for entry in load_scripts().values():
            if not entry["expect"].get("follows"):
                cid = c.post("/api/conversations", headers=ap).json()["conversation_id"]
            r = c.post(f"/api/conversations/{cid}/exchanges", headers=ap,
                       json={"question": entry["question"], "request_id": os.urandom(8).hex()})
            last = json.loads(r.text.strip().split("\n\n")[-1].split("data: ", 1)[1])
            assert last["response"]["outcome"] == entry["expect"]["outcome"], (entry["question"], last)
            assert last["cost"]["exchange_usd"] > 0
