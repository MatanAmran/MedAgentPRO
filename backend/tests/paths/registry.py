"""
The path map: every path an exchange or a request can take, what it covers, and what it rests on.

Each entry names the test that runs it, the branch tags it covers (see branches.py), and the book
requirements or project decisions it checks. test_registry.py fails if a branch in the code has no
entry, if an entry names a test that does not exist, or if an entry names a tag the code no longer
has. tools/path_map.py renders this list, with the latest results, into docs/notes/60_PATHS.md.

Adding to the system: a new branch makes test_registry fail; add the path's test, then its entry here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Path:
    id: str
    test: str                      # file::name or file::name[param]
    covers: tuple[str, ...]
    refs: str = ""                 # book requirements and project decisions
    note: str = ""


A = "test_paths.py::test_path"
P = "test_paths.py"
B = "test_branches.py"


def _a(pid, covers, refs, note=""):
    return Path(pid, f"{A}[{pid}]", tuple(covers), refs, note)


PATHS: list[Path] = [
    # --- one exchange: answers ------------------------------------------------
    _a("answered_all_supported", ["outcome:answered", "disposition:retrieve", "verdict:supported",
                                  "stage:intent_analysis", "stage:compilation", "stage:retrieval",
                                  "stage:synthesis", "stage:support_gate", "stage:output_contract", "role:APPROVER"],
       "4.9 · Test 21 · NFR 3, NFR 5"),
    _a("answered_two_readings", ["outcome:answered"], "FR 11 · FR 16 · Test 32, 34 · task 7 Q2, Q4"),
    _a("ambiguous_term_only_in_rewrite_not_named", [], "task 7 Q4 · FR 25",
       "a term the compiler accepts from the rewrite is not shown: only spans of the question as posed"),
    _a("planner_sees_own_rows_only", ["role:PLANNER"], "NFR 17 · Test 18"),
    _a("referred", ["outcome:referred_to_investigation", "disposition:refer_to_investigation"], "FR 14 · Test 22"),
    _a("empty_result_no_synthesis", [], "Test 35"),
    _a("identifier_not_held", [], "Test 35 · 40_CHAT §6"),
    _a("redraft_from_kept", ["verdict:uncovered", "stage:regeneration", "notice:WITHHELD"], "NFR 19 · decision 4, 6"),
    _a("contradicted_like_unsupported", ["verdict:contradicted"], "decision 3"),
    _a("fresh_draft_then_supported", [], "NFR 19 · decision 4"),
    _a("fresh_draft_then_nothing", ["outcome:no_supported_assertion"], "NFR 19 strict · Test 17"),
    _a("redraft_then_nothing", [], "NFR 19 strict · Test 17"),
    # --- one exchange: rejected output and feedback -----------------------------
    _a("intent_fixed_by_feedback", [], "R4 feedback attempt · Test 13"),
    _a("intent_schema_breach_fixed_by_feedback", [], "R4 · P3 (intent breach = content)"),
    _a("intent_rejected_twice", ["outcome:request_not_completed", "handler:run._Rejected", "notice:NOT_COMPLETED"],
       "Test 13"),
    _a("intent_not_json", [], "R4 format"),
    _a("synthesis_fixed_by_feedback", [], "R4 · F7, F11"),
    _a("synthesis_rejected_twice", [], "R4"),
    _a("synthesis_format_failure_not_retried", [], "R4 · P3"),
    _a("gate_skips_then_covers", [], "decision 12 (R4 of task 4)"),
    _a("gate_skips_twice", [], "decision 12"),
    _a("refusal_not_retried", [], "R4 · P10"),
    _a("truncated_not_retried", [], "R4"),
    _a("empty_not_retried", [], "R4"),
    _a("fenced_json_accepted", [], "R3"),
    _a("timeout_not_retried", [], "R4"),
    _a("client_error_not_retried", [], "R4"),
    _a("unexpected_exception", [], "R6"),
    _a("gate_raises_after_synthesis", [], "R6 · NFR 3"),
    # --- follow-ups -------------------------------------------------------------
    _a("follow_up_rewritten", ["stage:question_rewriting"], "FR 25 · Test 44"),
    _a("follow_up_unchanged_no_understood_as", [], "K6"),
    _a("follow_up_rewrite_rejected_twice", [], "4.9 rewriting · R4"),
    _a("follow_up_identifier_not_in_conversation", [], "NFR 27 · Test 45"),
    Path("feedback_recorded", f"{P}::test_feedback_attempt_carries_the_rejection_and_is_recorded", (),
         "R4 · P6"),
    Path("long_conversation", f"{P}::test_long_conversation_keeps_history_within_its_budget", (),
         "4.9 history layers · decision 7 · R7c"),
    Path("continues_after_every_ending", f"{P}::test_conversation_continues_after_every_ending", (),
         "4.9 conversation"),
    Path("raw_exception", f"{P}::test_raw_exception_from_a_gateway_still_ends_the_exchange",
         ("handler:run.Exception",), "R6"),
    # --- the source and the record ------------------------------------------------
    Path("source_missing", f"{P}::test_source_missing_is_unreachable_and_not_recorded",
         ("outcome:source_unreachable", "handler:run._Unreachable"), "FR 15 · Test 23 · decision 10"),
    Path("source_breaker_open", f"{P}::test_source_breaker_open_is_unreachable_without_trying", (), "NFR 9 · F9"),
    Path("one_reading_unreachable", f"{P}::test_one_of_two_readings_unreachable_ends_the_exchange", (), "FR 15, FR 16"),
    Path("retrieval_system_fault", f"{P}::test_retrieval_system_fault_is_recorded",
         ("handler:run._SystemFault",), "F1"),
    Path("record_not_written", f"{P}::test_record_that_cannot_be_written_is_not_presented",
         ("handler:finish.Exception",), "FR 13 · NFR 5 · decision 9"),
    # --- generated from the branch list ----------------------------------------------
    Path("every_failure_kind", f"{B}::test_every_model_failure_kind_ends_the_exchange_once",
         tuple(f"failure:{k}" for k in ("client_error", "connection", "empty", "format", "rate_limited",
                                        "refusal", "server_error", "timeout", "truncated", "unexpected")),
         "R4 · P9 · P10", "one case per kind, generated"),
    Path("every_failure_kind_late", f"{B}::test_every_model_failure_kind_at_the_gate_after_paying_for_synthesis",
         (), "R4 · NFR 3"),
    Path("every_gap_kind", f"{B}::test_every_gap_kind_is_rendered_without_a_cause",
         tuple(f"gap:{k}" for k in ("no_such_column", "no_such_table", "source_outside_system", "unspecified",
                                    "value_not_present")), "FR 11 · NFR 20", "one case per kind, generated"),
    # --- the real-model wiring ---------------------------------------------------------
    Path("real_answers_with_cost", f"{P}::test_real_wiring_answers_and_reports_cost", (), "task 5 · T3"),
    Path("budget_used_up", f"{P}::test_total_budget_used_up_before_any_call",
         ("budget:total", "notice:BUDGET_USED_UP", "handler:run.BudgetExceeded"), "task 5"),
    Path("exchange_cap", f"{P}::test_exchange_cap_stops_before_synthesis",
         ("budget:exchange", "notice:EXCHANGE_CAP"), "R7a · R7b"),
    Path("budget_provider", f"{B}::test_budget_scope_provider_refusal_is_not_retried", ("budget:provider",),
         "task 5 · NFR 3",
         "G1 closed (13.10): the refused call is recorded as model_call_failed"),
    Path("budget_model", f"{B}::test_budget_scope_model_mismatch_sends_nothing", ("budget:model",), "task 5 · G2"),
    Path("rate_limit", f"{P}::test_rate_limit_refuses_before_any_call",
         ("handler:run.RateLimited", "notice:RATE_LIMITED"), "task 5 rate"),
    Path("ledger_timeout_and_release",
         f"{P}::test_timeout_keeps_the_worst_case_charged_and_refused_connection_releases_it",
         (), "R4 · R5"),
    Path("cancelled_before_synthesis", f"{P}::test_cancelled_before_synthesis_makes_no_further_call",
         ("handler:run.ExchangeCancelled",), "S1"),
    Path("concurrent_budget", f"{P}::test_concurrent_exchanges_never_cross_the_total", (), "T2 lock"),
    # --- the server --------------------------------------------------------------------
    Path("server_each_ending_then_goes_on", f"{P}::test_server_path_then_the_conversation_goes_on",
         ("endpoint:POST /api/conversations", "endpoint:POST /api/conversations/{conversation_id}/exchanges",
          "endpoint:GET /api/conversations/{conversation_id}", "http:200", "http:201"), "S1 · S3 · decision 8"),
    Path("server_unreachable_then_goes_on", f"{P}::test_server_source_unreachable_then_goes_on", (), "FR 15 · S3"),
    Path("server_worker_bug", f"{P}::test_server_bug_in_the_worker_ends_with_error_and_frees_the_conversation", (),
         "S1"),
    Path("server_gateway_build_fails", f"{P}::test_server_gateway_for_raising_frees_the_conversation", (), "B1"),
    Path("server_stream_never_read", f"{P}::test_server_stream_never_read_frees_the_conversation", (), "B1"),
    Path("server_disconnect_mid_exchange", f"{P}::test_server_disconnect_mid_exchange_stops_paying", (), "S1"),
    Path("server_refusals", f"{P}::test_server_refuses_what_it_should",
         ("http:401", "http:403", "http:404", "http:409", "http:422"), "S2 · S3 · R8"),
    Path("server_rating", f"{P}::test_server_rating_rules",
         ("endpoint:POST /api/exchanges/{exchange_id}/rating", "stage:rating"), "FR 12 · K4 · Test 16"),
    Path("server_one_at_a_time", f"{P}::test_server_one_question_at_a_time_per_conversation", (), "task 6"),
    Path("server_listing_and_health", f"{B}::test_server_listing_and_health",
         ("endpoint:GET /api/conversations", "endpoint:GET /api/health"), "S3"),
]
