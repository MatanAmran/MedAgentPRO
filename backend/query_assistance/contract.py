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
AGG = {"SUM": "total", "AVG": "average", "MIN": "lowest", "MAX": "highest"}

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
    if "me" in node:   # D7 (fix round): the asker, named as the person reading the answer
        return "you"
    return format_value(node["value"]) if not isinstance(node["value"], str) else f"'{node['value']}'"


def _metric_columns(node: dict) -> list[str]:
    """The columns a metric's expression reads, in order, from schema.json."""
    if "col" in node:
        return [node["col"]]
    return [c for a in node.get("args", []) for c in _metric_columns(a)]


def _func(node: dict, cat: Catalog, examined: bool = False) -> str:
    """examined: the Examined line adds, for an aggregate over a metric, the records it was taken over: a
    record where a column of the metric is empty has no value, and the query leaves it out (chat, 18.10,
    13:15). From the metric's expression in schema.json, never from the model."""
    arg = node["arg"]
    if arg == "*":
        return "number of records"
    if "metric" in arg:
        name = f"{AGG[node['func']]} {_metric(arg['metric'], cat)}"
        if not examined:
            return name
        m = cat.metrics[arg["metric"]]
        cols = list(dict.fromkeys(_column(c, cat) for c in _metric_columns(m["expr"])))
        return f"{name}, over {_table(m['entity'], cat)} with {' and '.join(cols)} recorded"
    col = _column(arg["col"], cat)
    return f"count of {col}" if node["func"] == "COUNT" else f"{AGG[node['func']]} {col}"


def headers(plan: dict, cat: Catalog) -> dict[str, str]:
    """The name each output column is shown by, from the catalog and the expression."""
    return {i["as"]: _operand(i["expr"], cat) for i in plan["SELECT"]}


def _negated(name: str, cat: Catalog) -> str:
    """A boolean metric asked not to hold reads as schema.json words it (chat, 18.10, 05:00): "not" put
    before the description gave "not the supplier committed to ..." for three of the five."""
    return cat.metrics[name]["description_negated"].rstrip(".")


def _metric_phrase(name: str, cat: Catalog, negate: bool = False) -> str:
    desc = _negated(name, cat) if negate else cat.metrics[name]["description"].rstrip(".")
    return desc[0].lower() + desc[1:]


MATCHED_BY = {1: "", 2: " (by prefix)", 3: " (by contains)"}   # chat 20.10, C


def matched_phrases(matched: list[dict] | None, cat: Catalog, by_identifier: bool = False) -> dict:
    """Chat 20.10, C: a name resolved to one entity reads as one line in Examined, "supplier name 'Apex'
    matched Apex Polymers (by prefix)", keyed by the EQ the plan now holds on the entity's identifier.
    by_identifier names the entity by its identifier instead: the line every model and the history
    receive, since a name is free text held in a record and what they get is built from the plan and
    the catalog only (NFR 21, render_records). The name is for the screen."""
    out = {}
    for m in matched or []:
        if m["count"] != 1:
            continue
        ident, name = m["candidates"][0]
        shown = ident if by_identifier else name
        out[(m["id_column"], ident)] = (f"{_column(m['column'], cat)} '{m['term']}' matched {shown}"
                                        f"{MATCHED_BY[m['stage']]}")
    return out


def _predicate(node: dict, cat: Catalog, phrases: dict | None = None) -> str:
    (op, arg), = node.items()
    if op in ("AND", "OR"):
        parts = [_predicate(p, cat, phrases) for p in arg]
        joined = f" {op.lower()} ".join(parts)
        return f"({joined})" if op == "OR" else joined
    if op in CMP:
        a, b = arg
        # a boolean metric compared with true or false reads as the business term it declares
        for m, v in ((a, b), (b, a)):
            if "metric" in m and "value" in v and isinstance(v["value"], bool) and op in ("EQ", "NE"):
                return _metric_phrase(m["metric"], cat, negate=(v["value"] is False) != (op == "NE"))
            if phrases and op == "EQ" and "col" in m and "value" in v and (m["col"], v["value"]) in phrases:
                return phrases[(m["col"], v["value"])]
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
    if op == "MATCHES":   # a name not resolved to one entity (chat 20.10)
        return f"{_column(arg['col'], cat)} matches '{arg['term']}'"
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


def render_checked(plan: dict, cat: Catalog, role: str, matched: list[dict] | None = None,
                   by_identifier: bool = False) -> str:
    """Fixed mapping from clause to phrase. No model writes it (exchange_response assembly rules)."""
    named = headers(plan, cat)
    parts = [f"Examined: {_table(plan['FROM'], cat)}"]
    scope = {plan["FROM"]}
    for j in plan.get("JOIN", []):
        rel = cat.relations[j["rel"]]
        other = rel["to"] if rel["from"] in scope else rel["from"]
        scope.add(other)
        other_name = _table(other, cat) + _join_role(j["rel"], cat)
        parts[0] += f" with {other_name}" if j["type"] == "INNER" else f" with {other_name} where held"
    if "WHERE" in plan:
        parts[0] += f" where {_predicate(plan['WHERE'], cat, matched_phrases(matched, cat, by_identifier))}"
    if plan.get("GROUP_BY"):
        parts.append("grouped by " + ", ".join(named.get(g, g) for g in plan["GROUP_BY"]))
    aggs = [_func(i["expr"], cat, examined=True) for i in plan["SELECT"] if "func" in i["expr"]]
    if aggs:
        parts.append("computed the " + ", the ".join(aggs))
    for h in plan.get("HAVING", []):
        parts.append(f"kept where {named.get(h['agg'], h['agg'])} {CMP[h['op']]} {format_value(h['value'])}")
    if _restricted(plan, cat, role):
        parts.append("limited to the records you hold")
    return " · ".join(parts)


def _join_role(name: str, cat: Catalog) -> str:
    """Chat 20.10 (found on BIRD superhero): where the catalog declares more than one relation between the
    same two tables (eye, hair and skin colour all reach colour), Examined names the key the reading joined
    through, " (eye colour)", so the reader can tell which. Where there is one relation, nothing: the demo's
    Examined, and what synthesis and the gate are given, stay as they were, character for character."""
    rel = cat.relations[name]
    pair = {rel["from"], rel["to"]}
    if sum({r["from"], r["to"]} == pair for r in cat.relations.values()) < 2:
        return ""
    key = rel["on"][0][0]   # the column on the from side: the reference that defines this relation
    return f" ({(cat.column(key) or {}).get('label', key)})"


def examined_line(plan: dict, cat: Catalog, role: str, matched: list[dict] | None = None,
                  by_identifier: bool = False) -> str:
    """What a reading examined, without the "Examined: " the screen shows: the line synthesis and the
    gate get above the reading's rows (chat, 18.10, 09:40). The same text, from the same code."""
    return render_checked(plan, cat, role, matched, by_identifier).removeprefix("Examined: ")


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
    A metric the plan asks not to hold is titled as Examined reads it, by its description_negated
    (question 11 on the real model, chat 18.10, 04:45 and 05:00); one tested both ways gets no title."""
    named = _metrics_named(plan)
    if len(named) != 1:
        return None
    name = named.pop()
    tests = set(_metric_tests([plan.get("WHERE"), plan.get("HAVING")]))
    if len(tests) > 1:
        return None
    return _negated(name, cat) if tests == {True} else cat.metrics[name]["description"].rstrip(".")


def reading_entity(plan: dict, cat: Catalog) -> str | None:
    """Long list, L4 [demo extension]: the label of the FROM table, where each row is still a row of it.
    Only where every SELECT item is a plain column (a metric or an aggregate gives none), there is no
    GROUP_BY, and every JOIN is many-to-one by declared keys: each column of its `on` on the side already
    in the query references (schema.json) the column on the side it adds. From the catalog only (chat,
    18.10): part_forecast and commitment_forecast declare no such key, and give none.
    D10 (fix round): a reading grouped by exactly one identifier column has one row per record of the table
    that column identifies: its own table for a key, the referenced table for a reference."""
    if plan.get("GROUP_BY"):
        if len(plan["GROUP_BY"]) != 1:
            return None
        expr: dict = next((i["expr"] for i in plan["SELECT"] if i["as"] == plan["GROUP_BY"][0]), {})
        spec = cat.column(expr["col"]) if "col" in expr else None
        if not spec or not spec.get("identifier"):
            return None
        table = (spec.get("references") or expr["col"]).partition(".")[0]
        return cat.entities[table].get("label")
    if any("col" not in i["expr"] for i in plan["SELECT"]):
        return None
    scope = {plan["FROM"]}
    for j in plan.get("JOIN", []):
        rel = cat.relations[j["rel"]]
        new = rel["to"] if rel["from"] in scope else rel["from"]
        for a, b in rel["on"]:
            held, added = (a, b) if a.partition(".")[0] in scope else (b, a)
            if (cat.column(held) or {}).get("references") != added:
                return None
        scope.add(new)
    return cat.entities[plan["FROM"]].get("label")


def reading_view(label: str, plan: dict, result: RetrievalResult, cat: Catalog, role: str,
                 answer: str | None = None, evidence: list[str] | None = None,
                 answer_segments: list[dict] | None = None, matched: list[dict] | None = None) -> dict:
    view: dict[str, Any] = {
        "label": label,
        "checked": render_checked(plan, cat, role, matched),
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
    elif result.outcome == "name_not_matched":   # chat 20.10, V2: a finding, not a failure
        view["notice"] = " ".join(f"No {_column(m['column'], cat)} matches '{m['term']}'."
                                  for m in matched or [] if m["count"] == 0)
    elif result.outcome == "name_ambiguous":
        several = [m for m in matched or [] if m["count"] > 1]
        view["notice"] = " ".join(f"'{m['term']}' matches several {_column(m['column'], cat)}s." for m in several) \
            + " Ask again with the full name."
        # V5: for the screen only. Never in a prompt, the history, the presented identifiers or the record
        view["candidates"] = [{"term": m["term"], "column": _column(m["column"], cat),
                               "values": [f"{name} ({ident})" for ident, name in m["candidates"]],
                               "more": m["count"] - len(m["candidates"])} for m in several]
    elif result.truncated:
        view["notice"] = f"Showing {len(result.rows)} of {result.total}."
    if answer:
        view["answer"] = answer
        view["evidence"] = list(evidence or [])
        if answer_segments:   # UI refresh, phase C [demo extension]: the answer cut at its filled references
            view["answer_segments"] = answer_segments
    title = reading_title(plan, cat)
    if title:
        view["title"] = title
    entity = reading_entity(plan, cat)
    if entity:
        view["entity"] = entity
    return view


def for_record(response: dict) -> dict:
    """The response as the exchange record holds it: without the candidates of a name (chat 20.10, V4,
    K2), which are company data shown on the screen only."""
    if not any("candidates" in r for r in response.get("readings", [])):
        return response
    out = dict(response)
    out["readings"] = [{k: v for k, v in r.items() if k != "candidates"} for r in response["readings"]]
    return out


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


# D8 (fix round, chat 18.10, 23:25): says what the assistant does, and that sending is an action too
ACTION_DECLINED = ("This assistant answers questions from the records. It does not take actions, such as sending "
                   "messages or changing records.")


def declined_action(understood_as: str | None = None) -> dict:
    """Task 8, A1 [demo extension]: a request to change records. An existing outcome, answered, with no
    reading, no gap and a fixed notice: no new outcome is needed, and nothing was retrieved."""
    return _finish({"outcome": "answered", "readings": [], "gaps": [], "notice": ACTION_DECLINED}, understood_as)


NEEDS_CLARIFICATION = "The question could not be interpreted on its own."


def clarification_examples(cat: Catalog | None, n: int = 2) -> list[str]:
    """What a question may name, from the catalog (chat 19.10, C): the label of the identifier the first
    n entities hold as their own (an identifier column that references no other table), in the catalog's
    order. On the demo catalog: supplier, part."""
    out: list[str] = []
    for ent in (cat.entities.values() if cat else []):
        own = next((s for s in ent["columns"].values() if s.get("identifier") and not s.get("references")), None)
        if own and own.get("label") and own["label"] not in out:
            out.append(own["label"])
        if len(out) == n:
            break
    return out


def _has_dates(cat: Catalog | None) -> bool:
    """Whether the store records a date anywhere. Without a catalog, as before: yes."""
    if cat is None:
        return True
    return any(s.get("type") == "date" for ent in cat.entities.values() for s in ent["columns"].values())


def needs_clarification_notice(cat: Catalog | None) -> str:
    """"the period" is named only where the catalog holds a date column (chat 20.10, found on BIRD
    superhero, which records none). On the demo catalog the notice is what it was, word for word."""
    named = clarification_examples(cat)
    parts = (["the period"] if _has_dates(cat) else []) + ([" or ".join(named)] if named else [])
    such = ", ".join(parts) or "what it concerns"
    return f"{NEEDS_CLARIFICATION} Ask it in full, naming what it concerns, such as {such}."


def is_needs_clarification(notice: str | None) -> bool:
    """Whether a notice is the request to ask in full. Code's own words, as is_rate_limited reads them."""
    return (notice or "").startswith(NEEDS_CLARIFICATION)


def needs_clarification(understood_as: str | None = None, catalog: Catalog | None = None) -> dict:
    """Chat, 18.10, 11:35 [demo extension]: a question intent analysis could not interpret, such as a
    follow-up at the start of a conversation. As declined_action: answered, no reading, no gap, a fixed
    notice, nothing retrieved. Before it, such a question ended as a failure of the system. What the
    notice suggests naming comes from the catalog (chat 19.10, C)."""
    return _finish({"outcome": "answered", "readings": [], "gaps": [],
                    "notice": needs_clarification_notice(catalog)}, understood_as)


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
# D9 (fix round): the provider did not answer (timeout, connection, 5xx or 529). Behaviour is unchanged:
# no retry, counted by the model breaker (R4, P9); only the sentence says what happened
MODEL_UNAVAILABLE = "The model did not respond. Nothing was answered; try again in a moment."
# D3 (fix round): every intent attempt broke a size limit of the plan language. The limits are the schema's
PLAN_LIMIT = ("The question names more values or conditions than one plan may hold (at most 20 values in a list, "
              "6 conditions in a group). Ask it in smaller parts.")


_RATE_NOTICE = re.compile("^" + r"(\d+)".join(re.escape(p) for p in RATE_LIMITED.split("{seconds}")) + "$")


def is_rate_limited(notice: str | None) -> int | None:
    """The seconds a rate refusal's notice names, or None for any other notice (task 10). The notice is text
    this module wrote, so reading it back is reading code's own words, never a model's."""
    m = _RATE_NOTICE.match(notice or "")
    return int(m.group(1)) if m else None


def is_budget_refused(notice: str | None) -> bool:
    """Whether a notice is one of the budget's: the demo budget used up or the per-exchange cap (task 10)."""
    return notice in (BUDGET_USED_UP, EXCHANGE_CAP)


def rate_limited_notice(seconds: int) -> str:
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
        raise ContractError("the wait is a whole number of seconds")
    return RATE_LIMITED.format(seconds=seconds)


def request_not_completed(understood_as: str | None = None, notice: str = NOT_COMPLETED) -> dict:
    """Test 13: a rejected retrieval parameter set, or a model output rejected within its attempts.
    A system fault, not a request for input. Task 5: the budget, the exchange cap or the rate, each
    with its own fixed notice; the rate's carries only the seconds to wait (rate_limited_notice)."""
    rate = re.fullmatch(re.escape(RATE_LIMITED).replace(re.escape("{seconds}"), r"[1-9]\d*"), notice)
    if notice not in (NOT_COMPLETED, BUDGET_USED_UP, EXCHANGE_CAP, MODEL_UNAVAILABLE, PLAN_LIMIT) and not rate:
        raise ContractError("a notice is one of the fixed sentences, never free text")
    return _finish({"outcome": "request_not_completed", "readings": [], "gaps": [],
                    "notice": notice}, understood_as)
