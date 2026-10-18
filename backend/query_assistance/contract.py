"""
MedAgentPro - query assistance pipeline. The output contract (exchange_response).

Everything a person reads, except the wording of a drafted answer, is assembled here in
code and never by a model (4.9, NFR 20):

  render_checked()   what was examined, from the executed plan, by a fixed mapping from
                     clause to phrase (FR 11, Test 34). Present on every reading, since a plan
                     that addressed a plausible but wrong column produces no other signal (8.1)
  reading_view()     the records retrieved under a reading, their total where LIMIT cut them,
                     the ordering, and what an empty result means (Test 35)
  render_gap()       a part of the question no reading covers, from a template (FR 11, NFR 20)
  answered() ...     one constructor per outcome. Each validates against the schema

The interface language is English, as in the book's screens.
"""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from .compiler import HERE, Catalog, RetrievalResult
from .synthesis import format_value

CMP = {"EQ": "=", "NE": "≠", "GT": ">", "GTE": "≥", "LT": "<", "LTE": "≤"}
AGG = {"SUM": "total", "MIN": "lowest", "MAX": "highest"}

_BUNDLE = json.loads((HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))
_ENTRY = _BUNDLE["schemas"]["exchange_response"]
TEMPLATES = _ENTRY["templates"]
_VALIDATOR = Draft202012Validator(_ENTRY["schema"])


class ContractError(Exception):
    """An assembled response breaks its own schema. A bug in this module, never a model fault."""


# --------------------------------------------------------------------------
# what was examined (FR 11, Test 34)
# --------------------------------------------------------------------------

# Readable names (task 7 review, item 2): every table, column and metric of schema.json carries a
# label, and "Examined", the column headers and the order caption use it. The name an aggregate is
# shown by comes from its expression, here; never from the alias the model chose, nor any model text.
# A catalog without labels (the compiler tests' placeholder) falls back to the technical names.

def _table(name: str, cat: Catalog) -> str:
    return cat.entities[name].get("label", name)


def _column(qualified: str, cat: Catalog) -> str:
    return (cat.column(qualified) or {}).get("label", qualified)


def _metric(name: str, cat: Catalog) -> str:
    return cat.metrics[name].get("label", name)


def _operand(node: dict, cat: Catalog) -> str:
    if "col" in node:
        return _column(node["col"], cat)
    if "metric" in node:
        return _metric(node["metric"], cat)
    if "func" in node:
        return _func(node, cat)
    return format_value(node["value"]) if not isinstance(node["value"], str) else f"'{node['value']}'"


def _func(node: dict, cat: Catalog) -> str:
    if node["arg"] == "*":
        return "number of records"
    col = _column(node["arg"]["col"], cat)
    return f"count of {col}" if node["func"] == "COUNT" else f"{AGG[node['func']]} {col}"


def headers(plan: dict, cat: Catalog) -> dict[str, str]:
    """The name each output column is shown by, from the catalog and the expression."""
    return {i["as"]: _operand(i["expr"], cat) for i in plan["SELECT"]}


def _metric_phrase(name: str, cat: Catalog, negate: bool = False) -> str:
    desc = cat.metrics[name]["description"].rstrip(".")
    desc = desc[0].lower() + desc[1:]
    return f"{'not ' if negate else ''}{desc}"


def _predicate(node: dict, cat: Catalog) -> str:
    (op, arg), = node.items()
    if op in ("AND", "OR"):
        parts = [_predicate(p, cat) for p in arg]
        joined = f" {op.lower()} ".join(parts)
        return f"({joined})" if op == "OR" else joined
    if op in CMP:
        a, b = arg
        # a boolean metric compared with true or false reads as the business term it declares
        for m, v in ((a, b), (b, a)):
            if "metric" in m and "value" in v and isinstance(v["value"], bool) and op in ("EQ", "NE"):
                return _metric_phrase(m["metric"], cat, negate=(v["value"] is False) != (op == "NE"))
        return f"{_operand(a, cat)} {CMP[op]} {_operand(b, cat)}"
    if op == "IN":
        vals = ", ".join(_operand(v, cat) for v in arg["values"])
        return f"{_column(arg['col'], cat)} in ({vals})"
    if op == "BETWEEN":
        return f"{_column(arg['col'], cat)} between {_operand(arg['from'], cat)} and {_operand(arg['to'], cat)}"
    if op == "IS_NULL":
        return f"{_column(arg['col'], cat)} not recorded"
    if op == "IS_NOT_NULL":
        return f"{_column(arg['col'], cat)} recorded"
    raise AssertionError(op)


def _restricted(plan: dict, cat: Catalog, role: str) -> bool:
    """Whether the entitlement injected at compilation narrows any table in the query."""
    tables = [plan["FROM"]]
    for j in plan.get("JOIN", []):
        rel = cat.relations[j["rel"]]
        tables += [rel["from"], rel["to"]]
    for t in set(tables):
        rule = (cat.entities[t].get("entitlement") or {}).get(role) or {}
        if not rule.get("all"):
            return True
    return False


def render_checked(plan: dict, cat: Catalog, role: str) -> str:
    """Fixed mapping from clause to phrase. No model writes it (exchange_response assembly rules)."""
    named = headers(plan, cat)
    parts = [f"Examined: {_table(plan['FROM'], cat)}"]
    scope = {plan["FROM"]}
    for j in plan.get("JOIN", []):
        rel = cat.relations[j["rel"]]
        other = rel["to"] if rel["from"] in scope else rel["from"]
        scope.add(other)
        other_name = _table(other, cat)
        parts[0] += f" with {other_name}" if j["type"] == "INNER" else f" with {other_name} where held"
    if "WHERE" in plan:
        parts[0] += f" where {_predicate(plan['WHERE'], cat)}"
    if plan.get("GROUP_BY"):
        parts.append("grouped by " + ", ".join(named.get(g, g) for g in plan["GROUP_BY"]))
    aggs = [_func(i["expr"], cat) for i in plan["SELECT"] if "func" in i["expr"]]
    if aggs:
        parts.append("computed the " + ", the ".join(aggs))
    for h in plan.get("HAVING", []):
        parts.append(f"kept where {named.get(h['agg'], h['agg'])} {CMP[h['op']]} {format_value(h['value'])}")
    if _restricted(plan, cat, role):
        parts.append("limited to the records you hold")
    return " · ".join(parts)


def render_order(plan: dict, cat: Catalog | None = None) -> str | None:
    order = plan.get("ORDER_BY")
    if not order:
        return None
    named = headers(plan, cat) if cat else {}
    return "ordered by " + ", then ".join(
        f"{named.get(o['by'], o['by'])}, {'highest' if o['dir'] == 'DESC' else 'lowest'} first" for o in order)


# --------------------------------------------------------------------------
# a reading as presented
# --------------------------------------------------------------------------

def _cell(v: Any) -> Any:
    return v if v is None or isinstance(v, (int, float, str)) else str(v)


def _metrics_named(node: Any) -> set[str]:
    if isinstance(node, dict):
        found = {node["metric"]} if isinstance(node.get("metric"), str) else set()
        return found.union(*(_metrics_named(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(_metrics_named(v) for v in node))
    return set()


def _metric_tests(node: Any) -> list[bool]:
    """For each comparison of a metric with true or false (EQ or NE), whether it asks for the metric
    not to hold, read as _predicate reads it."""
    out: list[bool] = []
    if isinstance(node, dict):
        for op, arg in node.items():
            if op in ("EQ", "NE") and isinstance(arg, list) and len(arg) == 2:
                for m, v in ((arg[0], arg[1]), (arg[1], arg[0])):
                    if (isinstance(m, dict) and "metric" in m and isinstance(v, dict)
                            and isinstance(v.get("value"), bool)):
                        out.append((v["value"] is False) != (op == "NE"))
                        break
            out.extend(_metric_tests(arg))
    elif isinstance(node, list):
        for v in node:
            out.extend(_metric_tests(v))
    return out


def reading_title(plan: dict, cat: Catalog) -> str | None:
    """Task 7, Q2 [demo extension]: the business description of the one metric the plan names, from
    schema.json. None where the plan names none or several; answered() then numbers the reading.
    A metric the plan asks not to hold is titled as Examined reads it, "Not ..." (question 11 on the
    real model, chat 18.10, 04:45); one tested both ways gets no title."""
    named = _metrics_named(plan)
    if len(named) != 1:
        return None
    phrase = cat.metrics[named.pop()]["description"].rstrip(".")
    tests = set(_metric_tests([plan.get("WHERE"), plan.get("HAVING")]))
    if len(tests) > 1:
        return None
    if tests == {True}:
        return "Not " + phrase[0].lower() + phrase[1:]
    return phrase


def reading_view(label: str, plan: dict, result: RetrievalResult, cat: Catalog, role: str,
                 answer: str | None = None, evidence: list[str] | None = None) -> dict:
    view: dict[str, Any] = {
        "label": label,
        "checked": render_checked(plan, cat, role),
        "result": result.outcome,
        "columns": [i["as"] for i in plan["SELECT"]],
        "headers": headers(plan, cat),   # task 7 review, item 2 [demo extension]
        # task 7, Q3 [demo extension]: which columns select an identifier, from schema.json, so the
        # screen can group and tag without reading meaning
        "identifiers": [i["as"] for i in plan["SELECT"] if "col" in i["expr"]
                        and (cat.column(i["expr"]["col"]) or {}).get("identifier")],
        "rows": [{k: _cell(v) for k, v in r.items()} for r in result.rows],
        "row_count": len(result.rows),
        "total": result.total,
    }
    order = render_order(plan, cat)
    if order and result.rows:
        view["ordered_by"] = order
    if result.outcome == "empty_no_match":
        view["notice"] = "No records matched."
    elif result.outcome == "empty_unknown_entity":
        view["not_held"] = [{"value": str(m["value"]), "checked_in": m.get("checked_in") or m["column"]}
                            for m in result.missing]
        view["notice"] = " ".join(f"{m['value']} does not appear in {m['checked_in'].split('.')[0]}."
                                  for m in view["not_held"])
    elif result.truncated:
        view["notice"] = f"Showing {len(result.rows)} of {result.total}."
    if answer:
        view["answer"] = answer
        view["evidence"] = list(evidence or [])
    title = reading_title(plan, cat)
    if title:
        view["title"] = title
    return view


def _entity(entity: str, cat: Catalog | None) -> str:
    """The readable name of a table or a column a gap names, where the catalog holds it."""
    if cat is None:
        return entity
    if "." in entity:
        return _column(entity, cat) if cat.column(entity) else entity
    return _table(entity, cat) if entity in cat.entities else entity


def render_gap(gap: dict, cat: Catalog | None = None) -> dict:
    """FR 11, NFR 20: what is missing and what would allow it, never why. The text names the table or
    column by its label (task 7 review, item 2); missing keeps the technical name."""
    kind, entity = gap["needs"]["kind"], gap["needs"].get("entity")
    text = TEMPLATES[kind].format(asked=gap["asked"], entity=_entity(entity, cat) if entity else entity)
    missing = {"kind": kind} if entity is None else {"kind": kind, "entity": entity}
    return {"asked": gap["asked"], "missing": missing, "text": text}


# --------------------------------------------------------------------------
# one constructor per outcome
# --------------------------------------------------------------------------

def _finish(resp: dict, understood_as: str | None) -> dict:
    if understood_as:
        resp["understood_as"] = understood_as
    errors = sorted(_VALIDATOR.iter_errors(resp), key=lambda e: list(e.path))
    if errors:
        raise ContractError(f"{'/'.join(map(str, errors[0].path))}: {errors[0].message}")
    return resp


WITHHELD = "Some statements were withheld because the records do not support them."


def answered(readings: list[dict], gaps: list[dict] | None = None, understood_as: str | None = None,
             withheld: bool = False, ambiguous_terms: list[str] | None = None,
             catalog: Catalog | None = None) -> dict:
    """withheld: the gate removed a statement and something remains. The notice is fixed and never
    says what was removed ([demo extension], decisions 6 and 13 of 12.10).
    A reading with no title of its own is numbered by its place (task 7, Q2). ambiguous_terms is kept
    only where more than one reading is shown (Q4); the caller passes spans of the question as posed."""
    for n, r in enumerate(readings, start=1):
        r.setdefault("title", f"Reading {n}")
    resp = {"outcome": "answered", "readings": readings, "gaps": [render_gap(g, catalog) for g in gaps or []]}
    if withheld:
        resp["withheld"] = WITHHELD
    if ambiguous_terms and len(readings) > 1:
        resp["ambiguous_terms"] = list(ambiguous_terms)
    return _finish(resp, understood_as)


def referred(understood_as: str | None = None) -> dict:
    """FR 14, Test 22. No reading, no gap, no cause."""
    return _finish({"outcome": "referred_to_investigation", "readings": [], "gaps": [],
                    "offer_investigation": True,
                    "notice": "This asks for a cause, which an investigation determines."}, understood_as)


ACTION_DECLINED = "This assistant reads records. It does not change them."


def declined_action(understood_as: str | None = None) -> dict:
    """Task 8, A1 [demo extension]: a request to change records. An existing outcome, answered, with no
    reading, no gap and a fixed notice: no new outcome is needed, and nothing was retrieved."""
    return _finish({"outcome": "answered", "readings": [], "gaps": [], "notice": ACTION_DECLINED}, understood_as)


def no_supported_assertion(readings: list[dict], understood_as: str | None = None) -> dict:
    """NFR 19, strict reading, Test 17: the whole answer is replaced, records included.
    NFR 20: what is presented names the reading under which the question was taken, and no cause."""
    named = [{"label": r["label"], "checked": r["checked"]} for r in readings]
    return _finish({"outcome": "no_supported_assertion", "readings": [], "gaps": [],
                    "request_for_input": {
                        "text": "No statement could be established from the records retrieved. "
                                "Which of these did you mean, or what should be looked up instead?",
                        "readings": named}}, understood_as)


def source_unreachable(source: str, understood_as: str | None = None) -> dict:
    """FR 15: the failure is reported and the question returns to the person."""
    return _finish({"outcome": "source_unreachable", "readings": [], "gaps": [],
                    "notice": f"{source} could not be reached. The question was not answered."}, understood_as)


NOT_COMPLETED = "The request could not be completed."
BUDGET_USED_UP = "The demo budget for the model is used up. The question was not answered."
# {seconds} is filled in code from the rate window, a whole number only (chat, 18.10, 04:45)
RATE_LIMITED = "Too many questions in the last minute. The question was not answered. Try again in {seconds} seconds."
EXCHANGE_CAP = ("This question needs more of the model than one answer may use. The question was not "
                "answered. Ask about fewer records.")   # R7b: the per-exchange cap, not the total budget


def rate_limited_notice(seconds: int) -> str:
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
        raise ContractError("the wait is a whole number of seconds")
    return RATE_LIMITED.format(seconds=seconds)


def request_not_completed(understood_as: str | None = None, notice: str = NOT_COMPLETED) -> dict:
    """Test 13: a rejected retrieval parameter set, or a model output rejected within its attempts.
    A system fault, not a request for input. Task 5: the budget, the exchange cap or the rate, each
    with its own fixed notice; the rate's carries only the seconds to wait (rate_limited_notice)."""
    rate = re.fullmatch(re.escape(RATE_LIMITED).replace(re.escape("{seconds}"), r"[1-9]\d*"), notice)
    if notice not in (NOT_COMPLETED, BUDGET_USED_UP, EXCHANGE_CAP) and not rate:
        raise ContractError("a notice is one of the fixed sentences, never free text")
    return _finish({"outcome": "request_not_completed", "readings": [], "gaps": [],
                    "notice": notice}, understood_as)
