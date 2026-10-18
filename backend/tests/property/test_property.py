"""
Property-based tests (classic set, item 4). Run:  pytest -q        (heavier:  pytest -m stress)

Thousands of random valid plans from the schema 1 grammar over the demo catalog, and random text where
the system takes text. What must hold for every one of them:

  every SQL a plan produces (the query, the count, the existence probes) holds no quote, and every
  literal of the plan is a parameter · for a planner, the entitlement is a parameter once per restricted
  table, and in the ON clause of a LEFT JOIN · LIMIT never above ROW_LIMIT · the plan runs · the same
  plan compiles to the same SQL twice · fewer than 30% of generated plans are refused, or the generator
  tests nothing · prompts.fill never fills a value · a reference is filled once, never from a row's text
  · reading_view on any rows is a valid exchange_response

The generator is a plain function of a Random, so hypothesis drives it (st.randoms) and the refusal-rate
check runs it on fixed seeds. The question it builds names every identifier the plan names (NFR 27).
No test reaches a real model (6.1).
"""

import random
import re
import sqlite3

import pytest
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st

from demo_data import seed
from query_assistance import compiler as c
from query_assistance import contract
from query_assistance import prompts as p
from query_assistance import synthesis as s

pytestmark = pytest.mark.property

CAT = c.Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
ROLES = {"APPROVER": {"role": "APPROVER", "user_id": "AP-01"}, "PLANNER": {"role": "PLANNER", "user_id": "PL-02"}}
TEXTS = ["late", "open item", "x' OR '1'='1", "'; DROP TABLE suppliers; --", "שלום", "", "{{records}}"]
NUMERIC = ("integer", "real")


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "demo.db"
    seed.build(path)
    cn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    yield cn
    cn.close()


# --------------------------------------------------------------------------
# the generator
# --------------------------------------------------------------------------

def _value(rnd: random.Random, spec: dict, named: list):
    t = spec["type"]
    if spec.get("values"):
        return rnd.choice(spec["values"])
    if t == "text":
        if spec.get("identifier"):
            v = f"{rnd.choice('SPC')}-{rnd.randint(1, 120):03d}"
            named.append(v)
            return v
        return rnd.choice(TEXTS)
    if t == "integer":
        return rnd.randint(-3, 60)
    if t == "real":
        return round(rnd.uniform(-5, 500), 2)
    if t == "date":
        return f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
    return rnd.choice([True, False])


def _comparison(rnd, cols, metrics, from_table, named):
    kind = rnd.random()
    if metrics and kind < 0.2:
        m = rnd.choice(metrics)
        if CAT.metrics[m]["type"] == "boolean":
            return {rnd.choice(["EQ", "NE"]): [{"metric": m}, {"value": rnd.choice([True, False])}]}
        return {rnd.choice(["GT", "LT", "GTE"]): [{"metric": m}, {"value": rnd.randint(0, 20)}]}
    col = rnd.choice(cols)
    spec = CAT.column(col)
    if kind < 0.3 and col.startswith(from_table + "."):
        return {rnd.choice(["IS_NULL", "IS_NOT_NULL"]): {"col": col}}
    if kind < 0.45:
        return {"IN": {"col": col, "values": [{"value": _value(rnd, spec, named)} for _ in range(rnd.randint(1, 3))]}}
    if kind < 0.55 and spec["type"] in NUMERIC + ("date",):
        lo, hi = sorted([_value(rnd, spec, named), _value(rnd, spec, named)])
        return {"BETWEEN": {"col": col, "from": {"value": lo}, "to": {"value": hi}}}
    ops = ["EQ", "NE"] if spec["type"] in ("text", "boolean") else ["EQ", "NE", "GT", "GTE", "LT", "LTE"]
    return {rnd.choice(ops): [{"col": col}, {"value": _value(rnd, spec, named)}]}


def make_plan(rnd: random.Random) -> tuple[dict, str]:
    """A plan valid under schema 1, and a question naming every identifier it names."""
    table = rnd.choice(list(CAT.entities))
    scope, joins = [table], []
    for _ in range(rnd.randint(0, 2)):
        options = [(name, r) for name, r in CAT.relations.items()
                   if (r["from"] in scope) != (r["to"] in scope)]
        if not options:
            break
        name, r = rnd.choice(options)
        joins.append({"rel": name, "type": rnd.choice(["INNER", "LEFT"])})
        scope.append(r["to"] if r["from"] in scope else r["from"])
    cols = [f"{t}.{col}" for t in scope for col in CAT.entities[t]["columns"]]
    metrics = [m for m, spec in CAT.metrics.items() if spec["entity"] in scope]
    numeric = [col for col in cols if CAT.column(col)["type"] in NUMERIC]
    named: list = []
    plan: dict = {"FROM": table}
    if joins:
        plan["JOIN"] = joins

    if numeric and rnd.random() < 0.4:      # grouped: plain fields, and aggregates over them
        plain = rnd.sample(cols, rnd.randint(1, 2))
        select = [{"expr": {"col": col}, "as": f"g{i}"} for i, col in enumerate(plain)]
        aggs = [{"expr": {"func": "COUNT", "arg": "*"}, "as": "n"}]
        if rnd.random() < 0.6:
            aggs.append({"expr": {"func": rnd.choice(["SUM", "MIN", "MAX"]), "arg": {"col": rnd.choice(numeric)}},
                         "as": "agg"})
        plan["SELECT"] = select + aggs
        plan["GROUP_BY"] = [i["as"] for i in select]
        if rnd.random() < 0.4:
            plan["HAVING"] = [{"agg": "n", "op": rnd.choice(["GT", "GTE", "LT"]), "value": rnd.randint(0, 5)}]
    else:
        picked = rnd.sample(cols, rnd.randint(1, min(4, len(cols))))
        plan["SELECT"] = [{"expr": {"col": col}, "as": f"c{i}"} for i, col in enumerate(picked)]
        if metrics and rnd.random() < 0.3:
            plan["SELECT"].append({"expr": {"metric": rnd.choice(metrics)}, "as": "m"})

    if rnd.random() < 0.7:
        terms = [_comparison(rnd, cols, metrics, table, named) for _ in range(rnd.randint(1, 3))]
        plan["WHERE"] = terms[0] if len(terms) == 1 else {rnd.choice(["AND", "OR"]): terms}
    if rnd.random() < 0.5:
        plan["ORDER_BY"] = [{"by": rnd.choice([i["as"] for i in plan["SELECT"]]),
                             "dir": rnd.choice(["ASC", "DESC"])}]
    if rnd.random() < 0.5:
        plan["LIMIT"] = rnd.randint(1, c.ROW_LIMIT)
    return plan, "q " + " ".join(dict.fromkeys(named))


VALIDATOR = c.intent_validator()   # built once: building it costs more than compiling a plan


def _compile(plan: dict, question: str, role: str) -> c.CompiledReading:
    intent = {"disposition": "retrieve", "readings": [{"label": "r", "plan": plan}]}
    return c.compile_intent(intent, question, ROLES[role], CAT, VALIDATOR).readings[0]


def _literals(node) -> list:
    if isinstance(node, dict):
        return ([node["value"]] if "value" in node and not isinstance(node["value"], (dict, list)) else []) + \
            [v for k, n in node.items() if k != "value" for v in _literals(n)]
    if isinstance(node, list):
        return [v for n in node for v in _literals(n)]
    return []


def _restricted(plan: dict) -> list[str]:
    tables = {plan["FROM"]}
    for j in plan.get("JOIN", []):
        r = CAT.relations[j["rel"]]
        tables |= {r["from"], r["to"]}
    return [t for t in tables if not CAT.entities[t]["entitlement"].get("PLANNER", {}).get("all")]


# --------------------------------------------------------------------------
# plans
# --------------------------------------------------------------------------

@settings(suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much], deadline=None)
@given(rnd=st.randoms(use_true_random=False), role=st.sampled_from(sorted(ROLES)))
def test_every_valid_plan_compiles_to_safe_bounded_runnable_sql(rnd, role, conn):
    plan, question = make_plan(rnd)
    try:
        r = _compile(plan, question, role)
    except c.CompileError:
        assume(False)
    sqls = [(r.sql, r.params), (r.count_sql, r.count_params)] + [(pr.sql, pr.params) for pr in r.probes]
    for sql, _params in sqls:
        assert "'" not in sql, sql
    for v in _literals(plan.get("WHERE")):
        if isinstance(v, str):           # with no quote in the SQL, a string can only be a parameter
            assert v in r.params
    if role == "PLANNER":
        restricted = _restricted(plan)
        assert r.params.count("PL-02") == len(restricted) and r.count_params.count("PL-02") == len(restricted)
        scope = {plan["FROM"]}
        for j in plan.get("JOIN", []):     # the table each join brings, in order, as the compiler sees it
            rel = CAT.relations[j["rel"]]
            other = rel["to"] if rel["from"] in scope else rel["from"]
            scope.add(other)
            if j["type"] == "LEFT" and other in restricted:
                on = re.search(rf'LEFT JOIN "{other}" ON (.*?)(?= LEFT JOIN| JOIN| WHERE| GROUP BY| ORDER BY|$)',
                               r.sql).group(1)
                assert f'"{other}"."planner_id" = ?' in on
    assert r.limit <= c.ROW_LIMIT and r.params[-1] <= c.ROW_LIMIT
    c.execute(r, conn)                                        # runs, whatever it returns
    again = _compile(plan, question, role)                    # deterministic
    assert (again.sql, again.params, again.count_sql, again.count_params) == \
        (r.sql, r.params, r.count_sql, r.count_params)


def test_the_generator_mostly_makes_plans_the_compiler_takes():
    """A generator whose plans are mostly refused tests nothing. Fixed seeds, so the rate is stable."""
    refused, codes = 0, {}
    n = 400
    for i in range(n):
        plan, question = make_plan(random.Random(i))
        try:
            _compile(plan, question, "PLANNER" if i % 2 else "APPROVER")
        except c.CompileError as e:
            refused += 1
            codes[e.code] = codes.get(e.code, 0) + 1
    rate = refused / n
    print(f"\nrefused {refused} of {n} ({rate:.0%}): {codes}")
    assert rate < 0.30, f"{rate:.0%} refused: {codes}"


# --------------------------------------------------------------------------
# text the system takes
# --------------------------------------------------------------------------

PLACEHOLDER_TEXT = st.text(alphabet=st.characters(codec="utf-8"), max_size=40).map(
    lambda t: t + "{{a}}{{b}} {{records}}")


@given(a=PLACEHOLDER_TEXT, b=PLACEHOLDER_TEXT)
@example(a="{{b}}", b="{{a}}")
def test_fill_never_fills_a_value(a, b):
    prompt = p.Prompt("t", "1", "x", "head {{a}} middle {{b}} tail")
    assert p.fill(prompt, a=a, b=b) == f"head {a} middle {b} tail"


ROW_TEXT = st.one_of(st.none(), st.integers(), st.floats(allow_nan=False, allow_infinity=False),
                     st.text(max_size=30), st.sampled_from(["{m.t#1}", "{m.other#2}", "{{m.t#1}}", "שלום", ""]))


@given(row=st.fixed_dictionaries({"t": ROW_TEXT, "other": ROW_TEXT}))
def test_a_reference_is_filled_once_and_never_from_row_text(row):
    m = s.Material("m", [row])
    out = s.fill("A{m.t#1}B{m.other#1}C", {"m": m})
    assert out == f"A{s.format_value(row['t'])}B{s.format_value(row['other'])}C"


VIEW_VALUE = st.one_of(st.none(), st.integers(min_value=-10**9, max_value=10**9),
                       st.floats(allow_nan=False, allow_infinity=False), st.text(max_size=20),
                       st.sampled_from(["", "שלום", "{x.y#1}", "<b>", "' OR 1=1"]))


@given(rows=st.lists(st.fixed_dictionaries({"a": VIEW_VALUE, "b": VIEW_VALUE}), min_size=1, max_size=8),
       extra=st.integers(min_value=0, max_value=50), role=st.sampled_from(sorted(ROLES)))
def test_reading_view_on_any_rows_is_a_valid_response(rows, extra, role):
    plan = {"SELECT": [{"expr": {"col": "suppliers.supplier_id"}, "as": "a"},
                       {"expr": {"col": "suppliers.name"}, "as": "b"}],
            "FROM": "suppliers", "ORDER_BY": [{"by": "a", "dir": "ASC"}]}
    total = len(rows) + extra
    res = c.RetrievalResult("r", "rows_returned", rows, total, total > len(rows))
    view = contract.reading_view("r", plan, res, CAT, role)
    contract.answered([view])        # validates against exchange_response, or raises ContractError
