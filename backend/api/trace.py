"""
MedAgentPro - "How this was answered": what the panel under each answer shows (task 10). [demo extension]

One row per model call of the exchange, built from CallTrace, the same record as the operational log line
(R9) and the audit entry. Metadata only, from a whitelist of fields: never the prompt, the model's text, a
value from a record (NFR 21), a rejection message or the key. Of a rejection, only `check`, the name of the
check that failed (chat, 03:50 and 15:10).

The trace travels beside exchange_response, as the cost does (T3), and only where the server runs with
--panel. It is not read from the audit log (K2).

  calls     the rows, in the order the calls were made
  total     tokens summed over the rows; usd is the exchange's own cost where the budget kept one (the
            authoritative figure), else the sum of the rows' costs where every one is known
  unpriced  usd charged for calls whose own cost is unknown: a call that timed out keeps its worst case (R5)
  not_sent  no call was made: "rate_limit" or "budget", read from the response's fixed notice
  note      calls were made, but no answer was drafted: "plan_decided" (a referral, a declined action, a
            request to restate) or "no_records" (no reading returned a row). Keyed by the outcome
"""

from __future__ import annotations

from query_assistance import contract
from query_assistance.call_trace import CallTrace

FIELDS = ("stage", "prompt_identity", "model", "attempt_number", "feedback_attempt", "status", "finish_reason",
          "input_tokens", "output_tokens", "reasoning_tokens", "claims", "max_tokens", "duration_ms", "check",
          "cost_usd")
DRAFTING = ("synthesis", "regeneration")
PLAN_DECIDED = {"referred_to_investigation", "action_declined", "needs_clarification"}


def _status(response: dict) -> str:
    notice = response.get("notice")
    if response["outcome"] == "answered" and notice == contract.ACTION_DECLINED:
        return "action_declined"
    if response["outcome"] == "answered" and contract.is_needs_clarification(notice):
        return "needs_clarification"
    return response["outcome"]


def _sum(values: list[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def build(calls: list[CallTrace], response: dict, cost: dict | None) -> dict:
    rows = [{k: getattr(t, k) for k in FIELDS} for t in calls]
    costs = [r["cost_usd"] for r in rows]
    priced = round(sum(c for c in costs if c is not None), 6)
    if cost is not None:
        usd: float | None = cost["exchange_usd"]
    else:
        usd = priced if all(c is not None for c in costs) else None
    unpriced = round(usd - priced, 6) if usd is not None and usd - priced > 1e-9 else None

    not_sent = note = None
    if not rows:
        notice = response.get("notice")
        not_sent = ("rate_limit" if contract.is_rate_limited(notice) is not None
                    else "budget" if contract.is_budget_refused(notice) else None)
    elif not any(r["stage"] in DRAFTING for r in rows):
        status = _status(response)
        if status in PLAN_DECIDED:
            note = "plan_decided"
        elif status == "answered" and response["readings"] and not any(v["rows"] for v in response["readings"]):
            note = "no_records"
    return {"calls": rows,
            "total": {"input_tokens": _sum([r["input_tokens"] for r in rows]),
                      "output_tokens": _sum([r["output_tokens"] for r in rows]),
                      "reasoning_tokens": _sum([r["reasoning_tokens"] for r in rows]),
                      "usd": usd},
            "unpriced_usd": unpriced, "not_sent": not_sent, "note": note}
