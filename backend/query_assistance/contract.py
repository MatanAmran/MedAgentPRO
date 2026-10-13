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
from typing import Any

from jsonschema import Draft202012Validator

from .compiler import HERE, Catalog, RetrievalResult
from .synthesis import format_value

CMP = {"EQ": "=", "NE": "≠", "GT": ">", "GTE": "≥", "LT": "<", "LTE": "≤"}
AGG = {"SUM": "summed", "MIN": "lowest", "MAX": "highest"}

_BUNDLE = json.loads((HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))
_ENTRY = _BUNDLE["schemas"]["exchange_response"]
TEMPLATES = _ENTRY["templates"]
_VALIDATOR = Draft202012Validator(_ENTRY["schema"])


class ContractError(Exception):
    """An assembled response breaks its own schema. A bug in this module, never a model fault."""


# --------------------------------------------------------------------------
# what was examined (FR 11, Test 34)
# --------------------------------------------------------------------------

def _operand(node: dict, cat: Catalog) -> str:
    if "col" in node:
        return node["col"]
    if "metric" in node:
        return node["metric"]
    if "func" in node:
        return _func(node)
    return format_value(node["value"]) if not isinstance(node["value"], str) else f"'{node['value']}'"


def _func(node: dict) -> str:
    if node["arg"] == "*":
        return "the number of records"
    col = node["arg"]["col"]
    return f"the number of {col}" if node["func"] == "COUNT" else f"{AGG[node['func']]} {col}"


def _metric_phrase(name: str, cat: Catalog, negate: bool = False) -> str:
    desc = cat.metrics[name]["description"].rstrip(".")
    desc = desc[0].lower() + desc[1:]
    return f"{'not ' if negate else ''}{desc} ({name})"


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
        vals = ", ".join(_operand(v, cat) for v in arg[1])
        return f"{arg[0]['col']} in ({vals})"
    if op == "BETWEEN":
        return f"{arg[0]['col']} between {_operand(arg[1], cat)} and {_operand(arg[2], cat)}"
    if op == "IS_NULL":
        return f"{arg['col']} not recorded"
    if op == "IS_NOT_NULL":
        return f"{arg['col']} recorded"
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
    parts = [f"Examined: {plan['FROM']}"]
    scope = {plan["FROM"]}
    for j in plan.get("JOIN", []):
        rel = cat.relations[j["rel"]]
        other = rel["to"] if rel["from"] in scope else rel["from"]
        scope.add(other)
        parts[0] += f" with {other}" if j["type"] == "INNER" else f" with {other} where held"
    if "WHERE" in plan:
        parts[0] += f" where {_predicate(plan['WHERE'], cat)}"
    if plan.get("GROUP_BY"):
        parts.append("grouped by " + ", ".join(plan["GROUP_BY"]))
    aggs = [f"{_func(i['expr'])} as {i['as']}" for i in plan["SELECT"] if "func" in i["expr"]]
    if aggs:
        parts.append("computed " + ", ".join(aggs))
    for h in plan.get("HAVING", []):
        parts.append(f"kept where {h['agg']} {CMP[h['op']]} {format_value(h['value'])}")
    if _restricted(plan, cat, role):
        parts.append("limited to the records you hold")
    return " · ".join(parts)


def render_order(plan: dict) -> str | None:
    order = plan.get("ORDER_BY")
    if not order:
        return None
    return "ordered by " + ", then ".join(
        f"{o['by']}, {'highest' if o['dir'] == 'DESC' else 'lowest'} first" for o in order)


# --------------------------------------------------------------------------
# a reading as presented
# --------------------------------------------------------------------------

def _cell(v: Any) -> Any:
    return v if v is None or isinstance(v, (int, float, str)) else str(v)


def reading_view(label: str, plan: dict, result: RetrievalResult, cat: Catalog, role: str,
                 answer: str | None = None, evidence: list[str] | None = None) -> dict:
    view: dict[str, Any] = {
        "label": label,
        "checked": render_checked(plan, cat, role),
        "result": result.outcome,
        "columns": [i["as"] for i in plan["SELECT"]],
        "rows": [{k: _cell(v) for k, v in r.items()} for r in result.rows],
        "row_count": len(result.rows),
        "total": result.total,
    }
    order = render_order(plan)
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
    return view


def render_gap(gap: dict) -> dict:
    """FR 11, NFR 20: what is missing and what would allow it, never why."""
    kind, entity = gap["needs"]["kind"], gap["needs"].get("entity")
    text = TEMPLATES[kind].format(asked=gap["asked"], entity=entity)
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
             withheld: bool = False) -> dict:
    """withheld: the gate removed a statement and something remains. The notice is fixed and never
    says what was removed ([demo extension], decisions 6 and 13 of 12.10)."""
    resp = {"outcome": "answered", "readings": readings, "gaps": [render_gap(g) for g in gaps or []]}
    if withheld:
        resp["withheld"] = WITHHELD
    return _finish(resp, understood_as)


def referred(understood_as: str | None = None) -> dict:
    """FR 14, Test 22. No reading, no gap, no cause."""
    return _finish({"outcome": "referred_to_investigation", "readings": [], "gaps": [],
                    "offer_investigation": True,
                    "notice": "This asks for a cause, which an investigation determines."}, understood_as)


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
RATE_LIMITED = "Too many questions in the last minute. The question was not answered. Try again shortly."
EXCHANGE_CAP = ("This question needs more of the model than one answer may use. The question was not "
                "answered. Ask about fewer records.")   # R7b: the per-exchange cap, not the total budget


def request_not_completed(understood_as: str | None = None, notice: str = NOT_COMPLETED) -> dict:
    """Test 13: a rejected retrieval parameter set, or a model output rejected within its attempts.
    A system fault, not a request for input. Task 5: the budget, the exchange cap or the rate, each
    with its own fixed notice."""
    if notice not in (NOT_COMPLETED, BUDGET_USED_UP, RATE_LIMITED, EXCHANGE_CAP):
        raise ContractError("a notice is one of the fixed sentences, never free text")
    return _finish({"outcome": "request_not_completed", "readings": [], "gaps": [],
                    "notice": notice}, understood_as)
