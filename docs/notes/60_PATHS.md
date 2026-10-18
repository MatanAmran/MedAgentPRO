# MedAgentPro — מפת המסלולים וההיתכנות

**נוצר אוטומטית** ב-`python -m tools.path_map` (מתוך `backend\`), 18.10.2026 04:14. **לא לערוך ידנית** — המקור הוא `backend\tests\paths\registry.py` ו-`backend\tests\feasibility\constraints.py`.

**איך זה גדל:** `tests\paths\branches.py` קורא מהקוד ומהסכמות כל הסתעפות (מצב סיום, שלב, סוג כשל, `except`, הודעה, נקודת קצה, קוד HTTP, סוג פער, תפקיד). **הסתעפות בלי מסלול מפילה את `pytest`.** מגבלה שמתגלה (כמו הטמפרטורה) נכנסת כשורה ב-`constraints.py`, ושורה בלי בדיקה מפילה את `pytest`.

**תוצאות:** 67 עוברים · 0 נכשלים · 0 פער ידוע · 0 דולגו

## מסלולים

| | מסלול | מכסה | דרישה / הכרעה | הערה |
|---|---|---|---|---|
| ✅ | `answered_all_supported` | `outcome:answered`, `disposition:retrieve`, `verdict:supported`, `stage:intent_analysis`, `stage:compilation`, `stage:retrieval`, `stage:synthesis`, `stage:support_gate`, `stage:output_contract`, `role:APPROVER` | 4.9 · Test 21 · NFR 3, NFR 5 |  |
| ✅ | `answered_two_readings` | `outcome:answered` | FR 11 · FR 16 · Test 32, 34 · task 7 Q2, Q4 |  |
| ✅ | `ambiguous_term_only_in_rewrite_not_named` | — | task 7 Q4 · FR 25 | a term the compiler accepts from the rewrite is not shown: only spans of the question as posed |
| ✅ | `planner_sees_own_rows_only` | `role:PLANNER` | NFR 17 · Test 18 |  |
| ✅ | `referred` | `outcome:referred_to_investigation`, `disposition:refer_to_investigation` | FR 14 · Test 22 |  |
| ✅ | `action_declined` | `disposition:out_of_scope_action`, `notice:ACTION_DECLINED` | task 8 A1 · FR 14 (extension) | a request to change records: nothing retrieved, a fixed sentence, outcome answered |
| ✅ | `empty_result_no_synthesis` | — | Test 35 |  |
| ✅ | `identifier_not_held` | — | Test 35 · 40_CHAT §6 |  |
| ✅ | `redraft_from_kept` | `verdict:uncovered`, `stage:regeneration`, `notice:WITHHELD` | NFR 19 · decision 4, 6 |  |
| ✅ | `contradicted_like_unsupported` | `verdict:contradicted` | decision 3 |  |
| ✅ | `fresh_draft_then_supported` | — | NFR 19 · decision 4 |  |
| ✅ | `fresh_draft_then_nothing` | `outcome:no_supported_assertion` | NFR 19 strict · Test 17 |  |
| ✅ | `redraft_then_nothing` | — | NFR 19 strict · Test 17 |  |
| ✅ | `intent_fixed_by_feedback` | — | R4 feedback attempt · Test 13 |  |
| ✅ | `intent_schema_breach_fixed_by_feedback` | — | R4 · P3 (intent breach = content) |  |
| ✅ | `intent_rejected_twice` | `outcome:request_not_completed`, `handler:run._Rejected`, `notice:NOT_COMPLETED` | Test 13 |  |
| ✅ | `intent_not_json` | — | R4 format |  |
| ✅ | `synthesis_fixed_by_feedback` | — | R4 · F7, F11 |  |
| ✅ | `synthesis_rejected_twice` | — | R4 |  |
| ✅ | `synthesis_format_failure_not_retried` | — | R4 · P3 |  |
| ✅ | `gate_skips_then_covers` | — | decision 12 (R4 of task 4) |  |
| ✅ | `gate_skips_twice` | — | decision 12 |  |
| ✅ | `refusal_not_retried` | — | R4 · P10 |  |
| ✅ | `truncated_not_retried` | — | R4 |  |
| ✅ | `empty_not_retried` | — | R4 |  |
| ✅ | `fenced_json_accepted` | — | R3 |  |
| ✅ | `timeout_not_retried` | — | R4 |  |
| ✅ | `client_error_not_retried` | — | R4 |  |
| ✅ | `unexpected_exception` | — | R6 |  |
| ✅ | `gate_raises_after_synthesis` | — | R6 · NFR 3 |  |
| ✅ | `follow_up_rewritten` | `stage:question_rewriting` | FR 25 · Test 44 |  |
| ✅ | `follow_up_unchanged_no_understood_as` | — | K6 |  |
| ✅ | `follow_up_rewrite_rejected_twice` | — | 4.9 rewriting · R4 |  |
| ✅ | `follow_up_identifier_not_in_conversation` | — | NFR 27 · Test 45 |  |
| ✅ | `feedback_recorded` | — | R4 · P6 |  |
| ✅ | `long_conversation` | — | 4.9 history layers · decision 7 · R7c |  |
| ✅ | `continues_after_every_ending` | — | 4.9 conversation |  |
| ✅ | `raw_exception` | `handler:run.Exception` | R6 |  |
| ✅ | `source_missing` | `outcome:source_unreachable`, `handler:run._Unreachable` | FR 15 · Test 23 · decision 10 |  |
| ✅ | `source_breaker_open` | — | NFR 9 · F9 |  |
| ✅ | `one_reading_unreachable` | — | FR 15, FR 16 |  |
| ✅ | `retrieval_system_fault` | `handler:run._SystemFault` | F1 |  |
| ✅ | `record_not_written` | `handler:finish.Exception` | FR 13 · NFR 5 · decision 9 |  |
| ✅ | `every_failure_kind` | `failure:client_error`, `failure:connection`, `failure:empty`, `failure:format`, `failure:rate_limited`, `failure:refusal`, `failure:server_error`, `failure:timeout`, `failure:truncated`, `failure:unexpected` | R4 · P9 · P10 | one case per kind, generated |
| ✅ | `every_failure_kind_late` | — | R4 · NFR 3 |  |
| ✅ | `every_gap_kind` | `gap:no_such_column`, `gap:no_such_table`, `gap:source_outside_system`, `gap:unspecified`, `gap:value_not_present` | FR 11 · NFR 20 | one case per kind, generated |
| ✅ | `real_answers_with_cost` | — | task 5 · T3 |  |
| ✅ | `budget_used_up` | `budget:total`, `notice:BUDGET_USED_UP`, `handler:run.BudgetExceeded` | task 5 |  |
| ✅ | `exchange_cap` | `budget:exchange`, `notice:EXCHANGE_CAP` | R7a · R7b |  |
| ✅ | `budget_provider` | `budget:provider` | task 5 · NFR 3 | G1 closed (13.10): the refused call is recorded as model_call_failed |
| ✅ | `budget_model` | `budget:model` | task 5 · G2 |  |
| ✅ | `rate_limit` | `handler:run.RateLimited`, `notice:RATE_LIMITED` | task 5 rate |  |
| ✅ | `ledger_timeout_and_release` | — | R4 · R5 |  |
| ✅ | `cancelled_before_synthesis` | `handler:run.ExchangeCancelled` | S1 |  |
| ✅ | `concurrent_budget` | — | T2 lock |  |
| ✅ | `server_each_ending_then_goes_on` | `endpoint:POST /api/conversations`, `endpoint:POST /api/conversations/{conversation_id}/exchanges`, `endpoint:GET /api/conversations/{conversation_id}`, `http:200`, `http:201` | S1 · S3 · decision 8 |  |
| ✅ | `server_unreachable_then_goes_on` | — | FR 15 · S3 |  |
| ✅ | `server_worker_bug` | — | S1 |  |
| ✅ | `server_gateway_build_fails` | — | B1 |  |
| ✅ | `server_stream_never_read` | — | B1 |  |
| ✅ | `server_disconnect_mid_exchange` | — | S1 |  |
| ✅ | `server_refusals` | `http:401`, `http:403`, `http:404`, `http:409`, `http:422` | S2 · S3 · R8 |  |
| ✅ | `server_rating` | `endpoint:POST /api/exchanges/{exchange_id}/rating`, `stage:rating` | FR 12 · K4 · Test 16 · task 8 review | only the asker rates, only an answer with at least one reading |
| ✅ | `server_one_at_a_time` | — | task 6 · T7-B1 | a second question waits for the conversation, then runs after the first |
| ✅ | `server_waits_for_abandoned_exchange` | — | T7-B1 (c) · R8 | the wait is bounded: error event with status 409, the conversation untouched |
| ✅ | `server_listing_and_health` | `endpoint:GET /api/conversations`, `endpoint:GET /api/health` | S3 |  |
| ✅ | `server_examples` | `endpoint:GET /api/examples` | task 8 A4 | the scripted questions for the empty screen; none on the real model |

## הסתעפויות בקוד, ומי מכסה כל אחת

**outcome** — `answered` (answered_all_supported, answered_two_readings) · `no_supported_assertion` (fresh_draft_then_nothing) · `referred_to_investigation` (referred) · `request_not_completed` (intent_rejected_twice) · `source_unreachable` (source_missing)

**disposition** — `out_of_scope_action` (action_declined) · `refer_to_investigation` (referred) · `retrieve` (answered_all_supported)

**verdict** — `contradicted` (contradicted_like_unsupported) · `supported` (answered_all_supported) · `uncovered` (redraft_from_kept)

**gap** — `no_such_column` (every_gap_kind) · `no_such_table` (every_gap_kind) · `source_outside_system` (every_gap_kind) · `unspecified` (every_gap_kind) · `value_not_present` (every_gap_kind)

**stage** — `compilation` (answered_all_supported) · `intent_analysis` (answered_all_supported) · `output_contract` (answered_all_supported) · `question_rewriting` (follow_up_rewritten) · `rating` (server_rating) · `regeneration` (redraft_from_kept) · `retrieval` (answered_all_supported) · `support_gate` (answered_all_supported) · `synthesis` (answered_all_supported)

**handler** — `finish.Exception` (record_not_written) · `run.BudgetExceeded` (budget_used_up) · `run.Exception` (raw_exception) · `run.ExchangeCancelled` (cancelled_before_synthesis) · `run.RateLimited` (rate_limit) · `run._Rejected` (intent_rejected_twice) · `run._SystemFault` (retrieval_system_fault) · `run._Unreachable` (source_missing)

**failure** — `client_error` (every_failure_kind) · `connection` (every_failure_kind) · `empty` (every_failure_kind) · `format` (every_failure_kind) · `rate_limited` (every_failure_kind) · `refusal` (every_failure_kind) · `server_error` (every_failure_kind) · `timeout` (every_failure_kind) · `truncated` (every_failure_kind) · `unexpected` (every_failure_kind)

**budget** — `exchange` (exchange_cap) · `model` (budget_model) · `provider` (budget_provider) · `total` (budget_used_up)

**notice** — `ACTION_DECLINED` (action_declined) · `BUDGET_USED_UP` (budget_used_up) · `EXCHANGE_CAP` (exchange_cap) · `NOT_COMPLETED` (intent_rejected_twice) · `RATE_LIMITED` (rate_limit) · `WITHHELD` (redraft_from_kept)

**endpoint** — `GET /api/conversations` (server_listing_and_health) · `GET /api/conversations/{conversation_id}` (server_each_ending_then_goes_on) · `GET /api/examples` (server_examples) · `GET /api/health` (server_listing_and_health) · `POST /api/conversations` (server_each_ending_then_goes_on) · `POST /api/conversations/{conversation_id}/exchanges` (server_each_ending_then_goes_on) · `POST /api/exchanges/{exchange_id}/rating` (server_rating)

**http** — `200` (server_each_ending_then_goes_on) · `201` (server_each_ending_then_goes_on) · `401` (server_refusals) · `403` (server_refusals) · `404` (server_refusals) · `409` (server_refusals) · `422` (server_refusals)

**role** — `APPROVER` (answered_all_supported) · `PLANNER` (planner_sees_own_rows_only)

## היתכנות

| | # | סוג | מה נבדק | מקור |
|---|---|---|---|---|
| ✅ | T01 | technical | The configured model is in the local map of the pinned LiteLLM, with prices. | LiteLLM 1.105 local map (H1, 12.10) |
| ✅ | T02 | technical | No temperature is configured for a model that takes none. | Claude 5: supports_sampling_params false (R1, 12.10) |
| ✅ | T03 | technical | The model supports native structured output, which rewriting, synthesis and the gate use. | LiteLLM map supports_native_structured_output (R2) |
| ✅ | T04 | technical | MODEL_MAX_OUTPUT_TOKENS, and intent analysis's own cap where set, are within the model's own output limit. | LiteLLM map max_output_tokens |
| ✅ | T05 | technical | The largest prompt the pipeline can build fits the model's context window. | LiteLLM map max_input_tokens · ROW_LIMIT · history budget |
| ✅ | T06 | technical | Every schema sent to the provider, as LiteLLM reduces it, is within Anthropic's limits: at most 24 optional properties, at most 16 anyOf or type arrays, no recursion, additionalProperties false on every object. | Anthropic docs, Structured outputs, complexity limits |
| ✅ | T07 | technical | Regex patterns sent to the provider use only supported features: no lookaround, no backreferences, no \b. | Anthropic docs, Structured outputs, pattern support |
| ✅ | T08 | technical | No enum in a schema sent to the provider holds two values that differ only in case. | Anthropic docs: enum casing is not guaranteed |
| ✅ | T09 | technical | The intent schema is not sent as structured output while it exceeds the limits. | Review of 12.10 (P2): unpacked it exceeds 24 / 16 |
| ✅ | T10 | technical | LiteLLM is pinned to the version the checks were made against, and uses its local map. | H1, 12.10 · requirements.txt |
| ✅ | T11 | technical | The worst path after intent (synthesis, gate, redraft, gate) fits the per-exchange cap at the model's prices, for the largest material the pipeline can retrieve. | R7a |
| ✅ | T12 | technical | The total budget covers at least 10 exchanges each spending its whole cap. | Task 5 budget (USD 10 total) |
| ✅ | T13 | technical | Every metric and every table compiles to SQL the installed SQLite runs. | compiler.py · sqlite3 |
| ✅ | T14 | technical | LiteLLM's own retries are off; the only retries are counted ones. | Task 5 'no hidden retries' |
| ✅ | T15 | technical | The model thinks always and cannot be told not to; its thinking is billed inside output_tokens and counts against max_tokens. We send nothing that sets it, and the per-call line counts it. | LiteLLM 1.105 map: thinking_always_on (task 10a, chat 22:40); R9: ~1 output token per visible character |
| ✅ | T16 | technical | No schema sent to the provider carries a $comment on an object type. | Anthropic API, 15.10: 400 "For 'object' type, property '$comment' is not supported" on the intent schema (00_STATUS, task 10a, step 3; chat 18.10, 03:25) |
| ✅ | L01 | logical | Every scripted demo question runs through the real checks to the outcome its script means. | S5 |
| ✅ | L02 | logical | Every table a role is shown in the intent prompt is one it may read, and every table it may read is shown. | NFR 17 · prompts.visible_tables |
| ✅ | L03 | logical | Every metric in schema.json compiles for every role, with entitlement applied. | NFR 17 · 40_CHAT §7 |
| ✅ | L04 | logical | Every example in the intent prompt passes the same checks as real output. | 40_CHAT §15 · tests/demo_data/test_demo_prompt.py |
| ✅ | L05 | logical | On the real-model path (budget, LiteLLM gateway, fake provider), every demo question fits the default per-exchange cap (USD 0.25) at Sonnet 5.5's prices; the largest needs about 0.14. So with intent analysis's own cap (MODEL_MAX_OUTPUT_TOKENS_INTENT=3000). | Task 9 · R7a · tools/fake_real_server.py |
| ✅ | V01 | live | The provider accepts the three reduced schemas (no 400). **נצפה:** R9, 14.10: rewriting (question 2), synthesis and the gate (both) accepted, no 400 (00_STATUS, R9) | R2 · R9 |
| ✅ | V02 | live | The provider accepts a request with no temperature and the configured max_tokens. **נצפה:** R9, 14.10: all 7 calls sent no temperature (provider default) and max_tokens=2000, per the model call line (00_STATUS, R9) | R1 · R9 |
| ✅ | V03 | live | The cost recorded for one exchange matches Usage in the Console. **נצפה:** R9, 14.10: the tokens are the provider's own usage report on each response; priced at the card they give USD 0.0943, and the credit balance fell 3.82 -> 3.72, consistent at cent precision. Usage and the key's Last used showed nothing yet (reporting lag) | R9 |
| ◐ | V04 | live | Real outputs pass the checks often enough: the feedback attempt rate is measured. **נצפה:** R9, 14.10: 0/7 calls needed feedback. Task 10a, question 3 (14.10): 1/1 feedback attempt passed the checks; its output dropped the date filter and declared a false gap. On intent v5 (18.10) the same question needed none. No claim on a rate (chat, 23:35) | R4 · R9 |

✅ עובר · ❌ נכשל · ⚠️ פער ידוע (xfail) · ⏭️ דולג (חסרה הטבלה של LiteLLM בסביבה) · ⏳ רק מול המודל האמיתי, בבדיקת העשן (R9), עוד לא נבדק · ◐ נצפה בבדיקת העשן, אבל לא כל מה שנטען (למשל: אין עדיין שיעור)

## מבחני הספר ← הבדיקות

מבחני הצ׳אט והמסך מפרק 6 בספר, ומי עונה על כל אחד. **נקרא מהבדיקות עצמן:** בדיקה שכותבת "Test N" בתיאור, בהערה או בכותרת שלה נחשבת עונה עליו.

| | מבחן | דרישה | מה הספר בודק | הבדיקות |
|---|---|---|---|---|
| ✅ | 13 | NFR 6 | Retrieval parameters are validated before execution | `backend/tests/paths/registry.py: intent_fixed_by_feedback`<br>`backend/tests/paths/registry.py: intent_rejected_twice`<br>`backend/tests/query_assistance/test_compiler.py: test_limit_above_range_is_rejected_not_clamped`<br>`backend/tests/query_assistance/test_pipeline.py: test_rejected_plan_is_not_run_and_ends_as_request_not_completed`<br>`backend/tests/query_assistance/test_schemas.py: (the file)` |
| ✅ | 16 | FR 11, FR 12, FR 13, NFR 4 | Answer from retrieved material, with attribution and rating | `backend/tests/paths/registry.py: server_rating`<br>`backend/tests/query_assistance/test_pipeline.py: test_rating_is_held_with_the_recorded_exchange`<br>`backend/tests/query_assistance/test_pipeline.py: test_rating_again_the_last_one_counts_and_each_stays_in_the_trail` |
| ✅ | 17 | NFR 19, NFR 20 | An answer with no supported assertion is regenerated once and then replaced | `backend/tests/paths/registry.py: fresh_draft_then_nothing`<br>`backend/tests/paths/registry.py: redraft_then_nothing`<br>`backend/tests/query_assistance/test_contract.py: test_no_supported_assertion_replaces_the_records_and_names_the_readings`<br>`backend/tests/query_assistance/test_pipeline.py: test_no_supported_assertion_is_regenerated_once_then_replaced` |
| ✅ | 21 | FR 13, NFR 3, NFR 4 | Traceability of an answered question | `backend/tests/paths/registry.py: answered_all_supported`<br>`backend/tests/query_assistance/test_pipeline.py: test_exchange_record_follows_the_declared_stage_order` |
| ✅ | 22 | FR 14, NFR 20 | A question requiring interpretation is routed rather than answered | `backend/tests/paths/registry.py: referred`<br>`backend/tests/query_assistance/test_pipeline.py: test_question_asking_for_a_cause_is_referred`<br>`backend/tests/query_assistance/test_schemas.py: (the file)` |
| ✅ | 23 | FR 15 | An unreachable source ends the exchange | `backend/tests/paths/registry.py: source_missing`<br>`backend/tests/query_assistance/test_pipeline.py: test_unreachable_source_is_not_recorded_but_is_logged_without_the_question` |
| ✅ | 32 | FR 11, NFR 6 | A question carrying more than one reading is not answered by selecting one | `backend/tests/paths/registry.py: answered_two_readings`<br>`backend/tests/query_assistance/test_schemas.py: (the file)` |
| ✅ | 33 | NFR 21 | Retrieved content does not redirect execution | `backend/tests/security/test_security.py: test_record_text_is_shown_but_never_steers_the_next_retrieval` |
| ✅ | 34 | FR 11 | What was examined is presented with an answer | `backend/tests/paths/registry.py: answered_two_readings`<br>`backend/tests/query_assistance/test_contract.py: test_checked_reads_the_metric_as_its_business_term` |
| ✅ | 35 | FR 11, FR 15 | A retrieval that matches no record is answered as such | `backend/tests/paths/registry.py: empty_result_no_synthesis`<br>`backend/tests/paths/registry.py: identifier_not_held`<br>`backend/tests/query_assistance/test_compiler.py: test_empty_with_known_identifier_is_no_match`<br>`backend/tests/query_assistance/test_pipeline.py: test_empty_result_is_an_answer_without_a_model_call`<br>`backend/tests/query_assistance/test_schemas.py: (the file)` |
| ✅ | 38 | NFR 22 | User flows complete on each browser engine | `frontend/e2e/demo-set.spec.ts: (the file)` |
| ⏳ | 39 | NFR 23 | Screens presenting stored content load within the configured time | ממתין: in the company's environment, with its records (task 9, B4) |
| ✅ | 40 | NFR 24 | The interface is operable from the keyboard and does not depend on color | `frontend/e2e/demo-set.spec.ts: (the file)`<br>`frontend/e2e/demo-set.spec.ts: the demo set as ${role.user}: every question ends as that role`<br>`frontend/e2e/demo-set.spec.ts: the demo set from the keyboard alone (Test 40)`<br>`frontend/app/components/components.test.tsx: states are told apart by text, not colour (Test 40, task 9 B3)` |
| ✅ | 44 | FR 25 | A follow-up question is answered under the question understood | `backend/tests/paths/registry.py: follow_up_rewritten`<br>`backend/tests/query_assistance/test_pipeline.py: test_follow_up_is_rewritten_and_shown_as_understood` |
| ✅ | 45 | NFR 27 | An identifier from outside the conversation is not retrieved | `backend/tests/paths/registry.py: follow_up_identifier_not_in_conversation`<br>`backend/tests/query_assistance/test_pipeline.py: test_follow_up_is_rewritten_and_shown_as_understood`<br>`backend/tests/query_assistance/test_pipeline.py: test_identifier_introduced_by_the_rewrite_is_rejected` |

✅ יש בדיקה שעונה עליו · ⏳ ממתין, ומה חסר · ❌ אין בדיקה
