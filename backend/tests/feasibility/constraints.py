"""
What the system can actually do, as opposed to what it is designed to do.

Each constraint is a limit of the provider, a library or the data that the design must fit inside,
with where it was learned. test_feasibility.py checks each one without the network and without a
model call; "live" constraints can only be confirmed against the real model, in the smoke test (R9),
and are listed so the map shows them as not yet confirmed.

When a limit surprises us (as Claude 5 refusing temperature did), it becomes a row here first, then a
check. test_feasibility fails if a row has no check or a check has no row.

kind:  technical  a provider or library limit
       logical    the design holding together on the demo data
       live       only the real model can confirm it (R9)
needs: map        needs LiteLLM's local model map of the pinned version (skipped where it is absent)

A live row records what the smoke test saw: `observed` says what, when and where it is written up, and
`result` is "confirmed", "partial" (seen, but not all it claims) or "" (not yet run against the model).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Constraint:
    id: str
    kind: str
    statement: str
    source: str
    needs: str = ""
    result: str = ""      # live rows only: confirmed | partial | "" (not yet)
    observed: str = ""    # live rows only: what the smoke test saw


CONSTRAINTS: list[Constraint] = [
    # --- technical: the model and the provider ------------------------------------------------
    Constraint("T01", "technical", "The configured model is in the local map of the pinned LiteLLM, with prices.",
               "LiteLLM 1.105 local map (H1, 12.10)", "map"),
    Constraint("T02", "technical", "No temperature is configured for a model that takes none.",
               "Claude 5: supports_sampling_params false (R1, 12.10)", "map"),
    Constraint("T03", "technical",
               "The model supports native structured output, which rewriting, synthesis and the gate use.",
               "LiteLLM map supports_native_structured_output (R2)", "map"),
    Constraint("T04", "technical", "MODEL_MAX_OUTPUT_TOKENS, and intent analysis's own cap where set, are within "
               "the model's own output limit.",
               "LiteLLM map max_output_tokens", "map"),
    Constraint("T05", "technical", "The largest prompt the pipeline can build fits the model's context window.",
               "LiteLLM map max_input_tokens · ROW_LIMIT · history budget", "map"),
    Constraint("T06", "technical", "Every schema sent to the provider, as LiteLLM reduces it, is within Anthropic's "
               "limits: at most 24 optional properties, at most 16 anyOf or type arrays, no recursion, "
               "additionalProperties false on every object.", "Anthropic docs, Structured outputs, complexity limits"),
    Constraint("T07", "technical", "Regex patterns sent to the provider use only supported features: no lookaround, "
               "no backreferences, no \\b.", "Anthropic docs, Structured outputs, pattern support"),
    Constraint("T08", "technical",
               "No enum in a schema sent to the provider holds two values that differ only in case.",
               "Anthropic docs: enum casing is not guaranteed"),
    Constraint("T09", "technical", "The intent schema is not sent as structured output while it exceeds the limits.",
               "Review of 12.10 (P2): unpacked it exceeds 24 / 16"),
    Constraint("T10", "technical",
               "LiteLLM is pinned to the version the checks were made against, and uses its local map.",
               "H1, 12.10 · requirements.txt"),
    Constraint("T11", "technical",
               "The worst path after intent (synthesis, gate, redraft, gate) fits the per-exchange cap "
               "at the model's prices, for the largest material the pipeline can retrieve.", "R7a", "map"),
    Constraint("T12", "technical", "The total budget covers at least 10 exchanges each spending its whole cap.",
               "Task 5 budget (USD 10 total)"),
    Constraint("T13", "technical", "Every metric and every table compiles to SQL the installed SQLite runs.",
               "compiler.py · sqlite3"),
    Constraint("T14", "technical", "LiteLLM's own retries are off; the only retries are counted ones.",
               "Task 5 'no hidden retries'"),
    Constraint("T15", "technical",
               "The model thinks always and cannot be told not to; its thinking is billed inside output_tokens and "
               "counts against max_tokens. We send nothing that sets it, and the per-call line counts it.",
               "LiteLLM 1.105 map: thinking_always_on (task 10a, chat 22:40); R9: ~1 output token per visible "
               "character", "map"),
    Constraint("T16", "technical",
               "No schema sent to the provider carries a $comment on an object type.",
               "Anthropic API, 15.10: 400 \"For 'object' type, property '$comment' is not supported\" on the intent "
               "schema (00_STATUS, task 10a, step 3; chat 18.10, 03:25)"),
    # --- logical: the design on the demo data ------------------------------------------------------
    Constraint("L01", "logical",
               "Every scripted demo question runs through the real checks to the outcome its script means.",
               "S5"),
    Constraint("L02", "logical", "Every table a role is shown in the intent prompt is one it may read, and every "
               "table it may read is shown.", "NFR 17 · prompts.visible_tables"),
    Constraint("L03", "logical", "Every metric in schema.json compiles for every role, with entitlement applied.",
               "NFR 17 · 40_CHAT §7"),
    Constraint("L04", "logical", "Every example in the intent prompt passes the same checks as real output.",
               "40_CHAT §15 · tests/demo_data/test_demo_prompt.py"),
    Constraint("L05", "logical",
               "On the real-model path (budget, LiteLLM gateway, fake provider), every demo question fits the "
               "default per-exchange cap (USD 0.25) at Sonnet 5.5's prices; the largest needs about 0.14. So "
               "with intent analysis's own cap (MODEL_MAX_OUTPUT_TOKENS_INTENT=3000).",
               "Task 9 · R7a · tools/fake_real_server.py"),
    # --- live: only the real model can confirm (R9) -------------------------------------------
    Constraint("V01", "live", "The provider accepts the three reduced schemas (no 400).", "R2 · R9",
               result="confirmed",
               observed="R9, 14.10: rewriting (question 2), synthesis and the gate (both) accepted, no 400 "
                        "(00_STATUS, R9)"),
    Constraint("V02", "live", "The provider accepts a request with no temperature and the configured max_tokens.",
               "R1 · R9", result="confirmed",
               observed="R9, 14.10: all 7 calls sent no temperature (provider default) and max_tokens=2000, "
                        "per the model call line (00_STATUS, R9)"),
    Constraint("V03", "live", "The cost recorded for one exchange matches Usage in the Console.", "R9",
               result="confirmed",
               observed="R9, 14.10: the tokens are the provider's own usage report on each response; priced at "
                        "the card they give USD 0.0943, and the credit balance fell 3.82 -> 3.72, consistent at "
                        "cent precision. Usage and the key's Last used showed nothing yet (reporting lag)"),
    Constraint("V04", "live", "Real outputs pass the checks often enough: the feedback attempt rate is measured.",
               "R4 · R9", result="partial",
               observed="R9, 14.10: 0/7 calls needed feedback. Task 10a, question 3 (14.10): 1/1 feedback attempt "
                        "passed the checks; its output dropped the date filter and declared a false gap. On intent v5 "
                        "(18.10) the same question needed none. No claim on a rate (chat, 23:35)"),
]
