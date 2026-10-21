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
K = "test_coverage.py"


def _a(pid, covers, refs, note=""):
    return Path(pid, f"{A}[{pid}]", tuple(covers), refs, note)


PATHS: list[Path] = [
    # --- one exchange: answers ------------------------------------------------
    _a("answered_all_supported", ["outcome:answered", "result:rows_returned", "disposition:retrieve",
                                  "verdict:supported",
                                  "stage:intent_analysis", "stage:compilation", "stage:retrieval",
                                  "stage:synthesis", "stage:support_gate", "stage:output_contract", "role:APPROVER"],
       "4.9 · Test 21 · NFR 3, NFR 5"),
    _a("answered_two_readings", ["outcome:answered"], "FR 11 · FR 16 · Test 32, 34 · task 7 Q2, Q4"),
    _a("ambiguous_term_only_in_rewrite_not_named", [], "task 7 Q4 · FR 25",
       "a term the compiler accepts from the rewrite is not shown: only spans of the question as posed"),
    _a("planner_reads_every_row", ["role:PLANNER"], "D6 (fix round) · NFR 17",
       "the demo opens all data to both roles; the mechanism is tested on tests/fixtures/restricted_schema.json"),
    _a("referred", ["outcome:referred_to_investigation", "disposition:refer_to_investigation"], "FR 14 · Test 22"),
    _a("action_declined", ["disposition:out_of_scope_action", "notice:ACTION_DECLINED"],
       "task 8 A1 · FR 14 (extension)",
       "a request to change records: nothing retrieved, a fixed sentence, outcome answered"),
    _a("needs_clarification", ["disposition:needs_clarification", "notice:NEEDS_CLARIFICATION"],
       "chat 18.10 11:35 (extension of FR 14)",
       "a question that cannot be interpreted, such as a follow-up opening a conversation: nothing retrieved, "
       "a fixed request to ask it in full, outcome answered, recorded"),
    _a("empty_result_no_synthesis", ["result:empty_no_match"], "Test 35"),
    _a("identifier_not_held", ["result:empty_unknown_entity"], "Test 35 · 40_CHAT §6"),
    _a("redraft_from_kept", ["verdict:uncovered", "stage:regeneration", "notice:WITHHELD"], "NFR 19 · decision 4, 6"),
    _a("contradicted_like_unsupported", ["verdict:contradicted"], "decision 3"),
    _a("gate_removes_other_metric_wording", [], "chat 18.10 09:40 · NFR 19",
       "a sentence worded as another metric than its examined line is contradicted; the rest is kept"),
    _a("gate_removes_only_sentence_other_metric", [], "chat 18.10 09:40 · NFR 19 strict",
       "the only sentence worded as another metric, twice: no_supported_assertion"),
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
    _a("synthesis_no_reference_fixed_by_feedback", [], "R4 · chat 18.10 05:10 (1)",
       "a breach of the schema sent to the provider is a content failure: the provider did not enforce it (T17)"),
    _a("synthesis_no_reference_twice", [], "R4 · chat 18.10 05:10 (1)"),
    _a("synthesis_not_json_not_retried", [], "R4 format"),
    _a("gate_provider_breach_fixed_by_feedback", [], "R4 · chat 18.10 05:10 (1)"),
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
                                        "refusal", "server_error", "timeout", "truncated", "unexpected"))
         + ("notice:MODEL_UNAVAILABLE",),
         "R4 · P9 · P10 · D9", "one case per kind, generated; the provider's silence has its own notice"),
    Path("plan_limit", f"{P}::test_plan_limit_notice_only_where_every_attempt_broke_a_size_limit",
         ("notice:PLAN_LIMIT",), "D3", "every intent attempt broke a size limit of the plan language"),
    Path("failed_call_max_tokens", f"{P}::test_failed_call_records_the_max_tokens_it_was_sent_with", (), "U-4"),
    Path("every_failure_kind_late", f"{B}::test_every_model_failure_kind_at_the_gate_after_paying_for_synthesis",
         (), "R4 · NFR 3"),
    Path("every_gap_kind", f"{B}::test_every_gap_kind_is_rendered_without_a_cause",
         tuple(f"gap:{k}" for k in ("cross_record_condition", "no_such_column", "no_such_table",
                                    "source_outside_system", "unspecified", "value_not_present")),
         "FR 11 · NFR 20 · D1", "one case per kind, generated"),
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
         ("endpoint:POST /api/exchanges/{exchange_id}/rating", "stage:rating"), "FR 12 · K4 · Test 16 · task 8 review",
         "only the asker rates, only an answer with at least one reading"),
    Path("server_one_at_a_time", f"{P}::test_server_one_question_at_a_time_per_conversation", (),
         "task 6 · T7-B1", "a second question waits for the conversation, then runs after the first"),
    Path("server_waits_for_abandoned_exchange",
         f"{P}::test_server_waits_for_abandoned_exchange_then_refuses_when_the_wait_runs_out", (),
         "T7-B1 (c) · R8", "the wait is bounded: error event with status 409, the conversation untouched"),
    Path("server_conversation_update", f"{P}::test_server_conversation_update",
         ("endpoint:PATCH /api/conversations/{conversation_id}",), "S3 · FR 13 · NFR 5 · UI refresh B",
         "[demo extension] title, pin, archive by the owner only; archiving hides, the record stays"),
    Path("server_archived_listing", f"{P}::test_server_archived_listing",
         ("endpoint:GET /api/conversations",), "S3 · long list L5",
         "[demo extension] ?archived=true lists the asker's archived conversations only; another user never"
         " sees them; unarchive through PATCH"),
    Path("server_listing_and_health", f"{B}::test_server_listing_and_health",
         ("endpoint:GET /api/conversations", "endpoint:GET /api/health"), "S3"),
    Path("server_examples", f"{B}::test_server_examples_on_the_scripted_model_only", ("endpoint:GET /api/examples",),
         "task 8 A4", "the scripted questions for the empty screen; none on the real model"),
    Path("server_show_all_records", f"{P}::test_server_show_all_records",
         ("endpoint:GET /api/exchanges/{exchange_id}/readings/{label}/records", "stage:show_all_records"),
         "Phase B task 5 · NFR 17 · NFR 21",
         "[demo extension] the plan the server kept, compiled again for the asker, up to 1000 rows; no model; "
         "each viewing a records_viewed entry (label, user, row count, no record content)"),
    Path("server_name_ambiguous", f"{P}::test_server_name_ambiguous", ("result:name_ambiguous", "http:409"),
         "chat 20.10, V2 · V4 · V5 · A · B · 81_VALUE_MATCH",
         "[proposal] a name held by two suppliers: nothing retrieved, the candidates on the screen only, never "
         "in the record; Show all records refuses the reading (409)"),
    Path("server_name_not_matched", f"{P}::test_server_name_not_matched", ("result:name_not_matched",),
         "chat 20.10, V2 · V4 · 81_VALUE_MATCH", "[proposal] a name no supplier holds: a finding, nothing retrieved"),
    Path("server_show_all_not_recorded", f"{P}::test_server_show_all_not_recorded", ("http:503",),
         "Phase B task 5 · R1", "the viewing could not be recorded: 503, nothing shown"),
    Path("server_show_all_unreachable", f"{P}::test_server_show_all_unreachable",
         ("http:503",), "Phase B task 5 · FR 15", "the source could not be reached: 503, nothing recorded"),
    # --- task 4, the coverage check (91_COVERAGE_PLAN; chat 20.10, 22:30 to 22:50) [proposal] ---------------
    Path("coverage_part_left_out", f"{K}::test_a_part_left_out_is_said_in_the_models_words_with_codes_facts",
         ("stage:question_classification", "stage:coverage_check"), "FR 11 · Test 47 [proposal] · GAP-03 · GAP-07",
         "A sends the plan back once naming the person's words it left out, then takes it; B words the line, "
         "code fills {part} and {reason}; no ending in not completed"),
    Path("coverage_message_template", f"{K}::test_a_message_that_fails_a_check_falls_back_to_the_template",
         ("notice:UNANSWERED",), "chat 22:40 · 22:50 · NFR 27",
         "negation or a causal word outside {reason}, a slot missing, a follow-up with an unknown identifier: "
         "the fixed template, and the part as written"),
    Path("coverage_cause_referred", f"{K}::test_a_cause_part_is_referred_with_what_was_recorded_a_click_away",
         ("notice:REFERRED",), "FR 14 · NFR 20 · Test 47 [proposal] · GAP-05 · E1",
         "C, independent of the plan: the reading of a part asking for a cause is not answered"),
    Path("coverage_cause_over_nothing", f"{K}::test_a_cause_part_over_records_that_hold_nothing_is_referred", (),
         "FR 14 · Test 35", "an empty result to a cause question is referred, not a finding"),
    Path("coverage_cause_unmapped",
         f"{K}::test_a_cause_part_that_falls_on_no_reading_leaves_no_sentence_from_the_readings", (),
         "FR 14 · NFR 20 · E1 · F-A (chat 21.10, 11:10)",
         "a cause part mapped to no part hides every reading it might mean; recorded as classification_unmapped"),
    Path("coverage_recorded_reason_kept", f"{K}::test_a_recorded_reason_asked_for_stays_answered", (),
         "FR 14 · 89 section 3.5", "the other direction: Q06 and D4 stay answered, every sentence kept"),
    Path("coverage_causal_replaced", f"{K}::test_a_causal_sentence_is_replaced_by_the_value_recorded",
         ("notice:REPLACED",), "NFR 20 · decision 4", "B: a sentence presenting a recorded value as the cause"),
    Path("coverage_classify_failed", f"{K}::test_classification_not_completed_states_recorded_reasons_as_recorded",
         ("notice:CLASSIFY_FAILED", "handler:join_classification.StageRejected",
          "handler:join_classification.StageFailed", "handler:join_classification.Exception"),
         "decision 8 · GAP-07", "the cautious side, and a notice"),
    Path("coverage_check_failed", f"{K}::test_coverage_check_not_completed_keeps_the_answer_and_says_so",
         ("notice:COVERAGE_FAILED", "handler:covered.StageRejected", "handler:covered.StageFailed",
          "handler:covered.Exception"), "decision 8 · GAP-07", "the cautious side, and a notice"),
    Path("coverage_cancelled", f"{K}::test_a_cancellation_during_either_check_ends_the_exchange",
         ("handler:join_classification.ExchangeCancelled", "handler:covered.ExchangeCancelled"), "R8 · T7-B1"),
    Path("coverage_cost_on_early_stop",
         f"{K}::test_an_exchange_stopped_before_synthesis_counts_what_the_classification_spent", (),
         "task 5 · T3 · NFR 3", "C is joined before the cost is taken, whichever way the exchange ends"),
    Path("coverage_skipped", f"{K}::test_one_plain_part_skips_the_coverage_check", (), "chat 21:01, decision 2",
         "S1 to S4 all hold: no call"),
    Path("coverage_server_part_on_its_own", f"{K}::test_server_part_asked_on_its_own_sends_the_checked_question",
         ("http:404",), "chat 22:50 · FR 25 · NFR 27",
         "the person's words posed, the checked question as Understood as, no rewriting call"),
    Path("coverage_server_show_recorded", f"{K}::test_server_shows_what_was_recorded_for_a_referred_part",
         ("notice:SHOW_RECORDED",), "chat 22:30, decision 2 · NFR 17 · NFR 21",
         "the plan the server kept, no model, the records say they establish no cause"),
]
