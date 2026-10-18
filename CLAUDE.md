# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working with Evyatar

- **Reply in Hebrew, always**, including summaries after long tool work. Check before sending. Code, comments and commit messages stay in English.
- Be concise and practical. For non-trivial work, propose a short plan and wait for approval before writing code.
- Point out bugs, risks and better alternatives unprompted. Say which parts of a proposal come from the book and which are your own.
- **The book is the source of truth** (`C:\Users\EvyatarS\project filles\01_Book\`). Where code and book differ, raise it, do not silently pick one.
- Never delete files from `project filles\` (the original research folder). Copy, do not move.

## When to send Evyatar back to the Claude chat

Architecture decisions and the book are handled in a separate Claude chat (the project "J&J"). Stop and tell Evyatar, in Hebrew, to take it there when:
- **A task from the plan is finished.** End with a short summary for the chat: what was built, which files changed, the test count, and every point where the code goes beyond or differs from the book. Record the same in `docs/notes/00_STATUS.md`. Commit before handing over.
- **An architectural question comes up** that the book and `docs/notes/` do not settle (a new stage, a change to a schema or to the output contract, a security or entitlement question). Write the question in `docs/notes/00_STATUS.md` and stop rather than deciding it.
- **Code and book disagree.** Describe the difference with the section or requirement number, and stop.
Small implementation choices (names, internal structure, test layout) do not need the chat.
**Stop before implementing, not after.** Do not build a "conservative" choice on an architectural question and flag it for review afterwards. Write the question, wait for the answer in the file, then continue.

## What this repository is

MedAgentPro is an LLM-driven root-cause-analysis system for supply-chain cases, developed as a project book in phases. Phase A (the book and feasibility studies) is complete. Phase B is being built. The current goal is a **demo of the query assistance (chat) pipeline** on synthetic data, showing that it answers from records, never infers a cause, and withholds what it cannot support.

## Project notes

`docs/notes/` holds the project notes, in Hebrew. **This is the working copy since 12.10**; the originals in `project filles\04_Project_Notes\` are kept as they were. Read before starting work:
- `00_STATUS.md` — what is open, what blocks what, what was closed
- `40_CHAT.md` — the chat pipeline: stages, schemas, decisions and why
- `30_ARCHITECTURE.md` — the six layers and technical decisions

When a decision is made in a session, record it in the relevant note (and here, briefly, if it changes how code is written).

## Demo plan and state

| # | Task | State |
|---|---|---|
| 0 | Code moved from `project filles\03_Code` into `backend/` | done |
| 1 | Synthetic store `backend/demo_data/` (schema, relations, `seed.py`) | done |
| 2 | Synthesis with symbolic references (`query_assistance/synthesis.py`) | done |
| 3 | Output contract in code (`query_assistance/contract.py`) | done |
| 4 | Pipeline orchestration: rewrite, intent, compile, retrieve, synthesis, gate, contract. Retry within attempts, one redraft (NFR 19), exchange record and rating (FR 12, FR 13). `query_assistance/pipeline.py` | done |
| 5 | Model gateway configuration: LiteLLM for a real model, scripted fallback. The API key comes from Evyatar, kept in `backend/.env` | built (`model_client/budget.py`); review R1–R7, P1–P10, H1–H2 done (12.10); R9 smoke test pending |
| 6 | FastAPI endpoint for the chat | done (12.10): `main.py`, `api/`, `demo_data/scripted*`; S1–S6 in `docs/notes/00_STATUS.md` |
| 7 | Chat screen in Next.js, after Figure 15 of the book | done (13.10), merged in PR #2: `frontend/app`, `frontend/lib`, `frontend/e2e`; Q1–Q5, T7-B1 and the deviations from Figure 15 in `docs/notes/00_STATUS.md` |
| 8 | Demo question set covering each behaviour | done (13.10), merged in PR #3: `demo_data/scripted_exchanges.json` (12 questions, each with `expect`), `out_of_scope_action` (A1), `GET /api/examples` and the examples on screen (A4), `docs/notes/70_DEMO.md` for the presenter |
| 9 | End-to-end tests on the scripted model | done (14.10), merged in PR #4: both roles from golden, three engines in CI, keyboard, contrast (axe), grayscale captures; the real-model path with a fake provider; `tools/preflight.py`; the book-test table in `60_PATHS.md` |

## Task 4 decisions (settled in the Claude chat, 12.10)

Task 4 adds `query_assistance/pipeline.py`: rewrite (only with history), intent and compile, retrieval of all readings in parallel (FR 16), synthesis over readings that returned rows, gate, one regeneration, contract. Model calls are retried within `MODEL_CALL_ATTEMPTS`, and a `CompileError` counts as a failed call. The questions and the reasoning are in `docs/notes/00_STATUS.md`.

1. **Exchange record.** Reuse `persistence/audit_repository.py` ("recorded as any other run", 4.9). Add `write_run(entries, run_update)` so all stage entries and the exchange document are written in one transaction (NFR 5). Entries are held in memory and written at the end, so an exchange that ends on an unreachable source is not recorded (Test 23). For the demo, an in-memory or SQLite store behind the same interface (no MongoDB). The rating is a later `write_event` on the exchange.
2. **One gate call over the whole answer**, each reading's text under its label (Test 17 counts the calls).
3. **`contradicted` is treated like an unsupported assertion**, not as an immediate fall. With symbolic references a contradiction can no longer come from a wrong value, only from the words around references, so NFR 19 applies as written. Update `docs/notes/40_CHAT.md` §5 and the `post_model_checks` of `support_scoring` accordingly.
4. **The single regeneration (NFR 19):**
   - at least one assertion kept: `synthesis.redraft()` from the kept assertions;
   - none kept (including a contradiction): a fresh `synthesis.draft()` over all the material, **with the rejected assertions passed in and an instruction not to state them again** (a new optional block in `synthesis.md`). Without it, the same input at temperature 0 returns the same answer. **In the book since 12.10:** NFR 19 and 4.9, "afresh with the assertions not found supported excluded".
   - The regenerated answer goes through the gate once more and is never regenerated again.
5. **What is presented after the last gate.** A sentence is presented only if the gate examined it and every claim found verbatim inside it is `supported`. A sentence with no claim inside it is not presented, since nothing checked it. A reading left with no sentence is shown with its records and "Examined" only. If no sentence survives in any reading, the outcome is `no_supported_assertion` (NFR 19, strict reading).
6. **Withheld notice.** Where any sentence or assertion was removed and something remains, the response carries a fixed notice from code: "Some statements were withheld because the records do not support them." It never shows what was removed. A `[demo extension]` field in `exchange_response`. **In the book since 12.10:** 4.9, "the answer states that statements were withheld, without stating them".
7. **History layer 3.** For the demo, the oldest exchanges are dropped with no summary. Layers 1 and 2 stay as built. NFR 27 still sees the whole conversation. A deviation from 4.9 ("then summarized"), recorded in `docs/notes/00_STATUS.md`.
8. **Streaming.** Stream stage progress only. The answer is sent once, after the gate.

9. **R1, recording fails.** If `write_run` fails, the answer is not presented: `request_not_completed` (FR 13, NFR 5; in the book since 12.10, 4.9: "an answer that cannot be recorded is not presented"). The failure is logged on the independent channel, the breaker's stdout logger (NFR 11).
10. **R2, what is recorded.** In the audit log (the exchange record): every outcome except `source_unreachable` (Test 23), including `request_not_completed`. **A source that could not be reached is still logged on the operational channel** (the breaker's stdout logger, separate from the store): time, user, which source, the error, and an attempt id. Not the question text, which may hold business data (a proposal). Test 23 forbids recording the attempt as an exchange, not logging the failure; `30_ARCHITECTURE` 3.4 requires every failure to be logged.
11. **R3, granularity.** One entry per model call, rejected attempts included (`attempt_number` > 1, `superseded_reason`). One entry, without tokens, for each stage with no model.
12. **R4, the gate cannot skip a sentence.** Every synthesis sentence holds a reference, so every sentence asserts something and must be examined. The gate receives the sentences numbered, and every claim in its output names the sentence it comes from (a new field in `support_scoring`, and the gate prompt numbers the sentences). `gate.py` rejects gate output that leaves a sentence uncovered, and the call is retried within `MODEL_CALL_ATTEMPTS` like any rejected model output. If every attempt skips, the outcome is `request_not_completed`. This replaces filtering unexamined sentences: nothing unexamined can reach the presentation step.
13. **R5.** The withheld notice is a new top-level field `withheld` in `exchange_response`, separate from `notice`. `[demo extension]`.

## Task 4 review (Claude chat, 12.10) — fixes before task 5

The seven points marked "לבדיקה" in `docs/notes/00_STATUS.md` and three more found in review. Details and reasoning there. **F1, F3, F5, F7, F8 and F9 are done (12.10).**

**Fix:**
- **F1, retrieval errors.** Only a failure to reach the source (connection fails, database locked, file missing, breaker open) is `source_unreachable`. Any other `sqlite3` error during execution is a system fault: `request_not_completed`, recorded, and logged on the operational channel.
- **F3, synthesis receives both forms** of the question, as posed and as rewritten, like intent analysis ("later stages receive both forms", 4.9).
- **F5, presented identifiers** (NFR 27) include the identifier-column values of the rows shown in the table, in display order, plus those in the answer text. Never free-text values (NFR 21).
- **F7, digits allowed outside a reference** come from the question as posed **and from the literal values of the executed plan** (WHERE, HAVING), which the compiler validated and "Examined" shows. Counts and sums are still rejected, since no plan holds them.
- **F8, who acted.** Add `actor_id` to `AuditEntry` and use it for the user. `on_behalf_of` keeps its meaning: an internal user entering information for an external party (`30_ARCHITECTURE` §2).
- **F9, the company source goes through the circuit breaker** (NFR 9, one breaker per dependency). An open breaker is `source_unreachable`.

**Keep as built:**
- **K4.** Only the asker rates, and only an `answered` outcome.
- **K6.** "Understood as" only where the rewrite differs from the question (FR 25, Test 44).

**Keep, and record:**
- **K2.** The exchange record holds the rows presented, since a later re-run would not show what was seen (data changes). Copies company data into the system store: added as a question for the client in `docs/notes/00_STATUS.md`.
- **K10.** Exchanges are stored in the `investigations` collection with `kind: "exchange"`. A temporary demo choice; how many collections is open question 24 (`30_ARCHITECTURE`).

## Second review (Claude chat, 12.10) — before task 5

- **F11, an identifier in the rows must be a reference.** A token outside a reference that equals a value of an identifier column in the rows retrieved (any reading) is rejected, even if the question or the plan holds it: the model must write `{label.alias#row}`. Typed, it loses its row, and "S-002 had {late.late_lines#1}" would pair S-002 with S-001's value (the open "wrong attribution" problem in `docs/notes/00_STATUS.md`). Digits from the question or the plan that are not identifiers in the rows (a week, a date, an identifier used only as a filter) stay allowed. Update `synthesis.md` (and the redraft prompt) to say so. **Done (12.10):** `synthesis.md` v5, `synthesis_redraft.md` v4, `Material.id_aliases`.
- **N12, for task 6.** `Deps` holds the source circuit breaker, so it must be created once at server start and shared by every request. A `Deps` per request gives a new breaker each time, which never opens.

## Task 5 requirements: the real model, with a hard spending cap (Evyatar pays, 12.10)

Evyatar pays for the model himself, with a total budget of **USD 10**. The code must never be able to spend beyond what Evyatar set. Details and reasoning in `docs/notes/00_STATUS.md`, "משימה 5 — תקציב".

**Where things are set.** The model is chosen **per request, in code**: the Console has no default model. The Console holds only the API key, the prepaid credit (auto-reload off) and the usage view. So the model id, prices and caps live in `backend/.env`, and changing the model is a change to `.env` only (NFR 8).

**Layer 1, the provider (Evyatar).** Individual API plan with **prepaid credits, auto-reload off** (USD 3.82 on 12.10, may be topped up to 10 total). When the credit runs out, calls fail. A separate API key for the demo (`medagentpro-demo`) so its usage is visible apart from anything else on the account.

**Layer 2, the code: `BudgetedGateway`**, a `ModelGateway` wrapper. No stage changes.
- **Configuration, `backend/.env` (gitignored), all required.** `ANTHROPIC_API_KEY`, `MODEL_ID` (from the model card in the Console, e.g. `claude-sonnet-5-5`), `MODEL_PRICE_IN` and `MODEL_PRICE_OUT` (USD per million tokens, from the same card), `MODEL_BUDGET_USD` (total cap, set by Evyatar **below the remaining credit**), `MODEL_MAX_USD_PER_EXCHANGE` (default 0.25), `MODEL_MAX_OUTPUT_TOKENS` per call (default 2000), `MODEL_MAX_EXCHANGES_PER_MINUTE` (default 10). **If any required value is missing, the real gateway refuses to start**; there is no built-in budget or price, so nothing is spent on a guess. Never write the values into code, tests or notes.
- **Spend persisted** in `backend/.model_spend.json` (gitignored): total, per day, per model. A restart does not reset it. Each call adds the cost of the tokens the provider reports (NFR 3 already records them).
- **Before every call**: worst case = estimated input tokens × price in + `MODEL_MAX_OUTPUT_TOKENS` × price out. If it would cross the total cap, or the exchange's own cap (`MODEL_MAX_USD_PER_EXCHANGE`), the call is not sent: `BudgetExceeded`. The exchange ends as `request_not_completed` with a fixed notice that the demo budget is used up. Logged on the operational channel. Not retried, and not counted by the circuit breaker as an outage.
- **No hidden retries.** LiteLLM's own retries are turned off (`num_retries=0`); the only retries are `MODEL_CALL_ATTEMPTS`, already counted. The number of model calls in one exchange is bounded by design (at most 12).
- **Provider refusals for money** (credit too low, spend limit reached: HTTP 400 `invalid_request_error` about usage limits or credit, or 429 with `error_code` `enforced_spend_limit_reached`) are treated as `BudgetExceeded`: not retried, not an outage.
- **Rate limit**: at most `MODEL_MAX_EXCHANGES_PER_MINUTE` exchanges per minute, across users, so a loop in the interface cannot burn the budget. Beyond it, the exchange is refused before any model call.
- **Prompt caching: not in the demo (T1).** On Anthropic a cache write costs more than a plain input token (on the Opus 5.5 card, 5 against 4 per million) and the cache lives about five minutes. Demo questions are spread out, so most calls would write, not read, and cost more. Recorded as not done. Any cached tokens the provider reports are counted at the cache write price if set, else at twice the input price, never at the input price, so the budget never undercounts.
- **Transparency (T3)**: cost and budget remaining are **outside `exchange_response`**: on `Outcome` and in the exchange record, and task 6 passes them in the API response beside the answer. The answer contract is unchanged.
- **One server process (T2).** The cap holds only if all spend goes through one counter: task 6 runs `uvicorn` without workers, and `BudgetedGateway` refuses to start if it detects more than one process using the spend file. **Within the process**, checking the cap, reserving the worst case and recording the actual cost happen under one lock, since retrievals run on threads and FastAPI serves requests concurrently: two calls must never both pass on the same remaining amount.
- **A dry run before the real one**: a command that prints what one exchange would cost at the configured prices from a scripted run, without calling the model.
- **Tests never call a real model** (book 6.1). Tests: a call that would cross the total cap is not sent; the exchange cap; spend survives a restart; a missing `.env` value refuses to start; a provider money refusal is not retried; the rate limit.
- **Intent analysis's own output cap (task 10a, chat 23:35 and 01:15):** `MODEL_MAX_OUTPUT_TOKENS_INTENT`, optional; unset, intent gets `MODEL_MAX_OUTPUT_TOKENS`. `BudgetConfig.output_cap(prompt_identity)` picks it, the reservation and the dry run price at it, `real_gateway()` refuses a cap above the model's own output limit, and preflight shows both caps as numbers.
- **Model**: Anthropic through `LiteLLMGateway` (book 3.1.4). Recommended Claude Sonnet 5.5 (USD 2 in / 10 out per million, official pricing page, 12.10), about 4 cents a typical exchange. Haiku 5.5 (0.10 / 0.50) as a cheaper alternative. Opus 5.5 (4 / 20) not needed for the demo.

## Task 5 review (Claude chat, 12.10, revised after an independent check)

**Principle: no retry without a change in input.** A call repeated with the same prompt is a paid bet: a model does not improve on a bare "try again" (Huang et al., ICLR 2024), it improves on specific external feedback. Today `pipeline._ask` (rewriting, intent, gate) and `synthesis._call` (synthesis, redraft) resend the identical prompt: up to 12 calls per exchange, 6 of them identical repeats. The NFR 19 redraft is fine (its input changes).

- **R1 Temperature (was T4).** Send `temperature` only if `MODEL_TEMPERATURE` is set in `.env`; unset, the audit records "provider default". Never `drop_params`. The audit records what was sent, not what was configured. Verified in LiteLLM 1.105's model map: `claude-sonnet-5-5` and `claude-opus-5-5` have `supports_sampling_params: false`; LiteLLM raises `UnsupportedParamsError` client side, before sending (no charge). `claude-haiku-5-5` is **not in** LiteLLM 1.105's map: do not use it.
- **R2 Structured outputs ON (reverses T6).** LiteLLM 1.105 maps `response_format` to Anthropic's native structured output for models with `supports_native_structured_output` (Sonnet 5.5: true), not to forced tool use (Sonnet 5.5: `supports_forced_tool_use: false`; never send tools). Each stage sends its schema reduced to the subset Anthropic accepts; the full schema is still validated in code (rule 3). If the provider rejects a schema, the stage fails without retry and says so. Checked in the smoke test.
- **R3 Fence strip (was T5)** stays as a fallback: one fence enclosing the whole text, nothing else.
- **R4 Classify every failure, then act:**
  - client errors (4xx, `UnsupportedParamsError`), a refusal, empty content: system fault, `request_not_completed`, no retry, **not counted by the model breaker** (it counts timeout, connection failure, 429, 5xx/529 only).
  - `finish_reason == "length"`: no retry; recorded as truncated. `ModelResponse` must carry the finish reason.
  - timeout: an explicit request timeout in code (LiteLLM's default is 6000 s); no retry; the reservation stays charged (unknown whether billed).
  - format failure after R2 and R3: no retry.
  - content failure (`check`, `CompileError`, `GateOutputError`, `RewriteError`): **one more attempt whose prompt carries the specific rejection message** ("Your previous output was rejected: …"), as a new template version `[proposal, not in the book; Table 15 names "Model call attempts" only]`. The message holds model-written text and names only, never a record value (NFR 21): test it.
  - **A schema breach (task 10a, chat 01:15, done 18.10):** where `oneOf`/`anyOf` fails, the feedback and the `check` point at the closest alternative (`jsonschema` `best_match`), path and keyword only, never a value or `message`. The `check` is `SchemaBreach:<keyword>@<path>`. No prompt version changed for this (the feedback is not in the template), but what the model is told did.
  - record per call whether it was a feedback attempt and whether it succeeded; `dry_run` and the smoke test report the rate. If feedback attempts rarely succeed, set attempts to 1.
- **R5 Ledger:** release the reservation when a call failed before reaching the provider (client-side error, connection refused). Keep it on timeout.
- **R6 Input and robustness bugs found in the check:**
  - `prompts.fill` replaces placeholders one after another, so a value holding `{{name}}` (the question, or record text) is filled by a later key or trips "unfilled". Fill in one pass over the template only; values are never rescanned. Tests: question `{{records}}`, record text `{{kept}}`.
  - `ModelResponse.json()` on `None` text raises `TypeError`, which is not in `REJECTED`, so it escapes.
  - `UnsupportedParamsError` and any other unexpected exception escape `run()`. Every exception ends as `request_not_completed`, recorded. Test: a gateway that raises an arbitrary exception.
- **R7 Size caps:** confirm (and report) the cap on history turns sent to rewriting and on rows sent to synthesis.
- **R8 For tasks 6 and 7 (requirements now, code later):** one exchange per client request id (the server refuses a repeated id); the send button is locked while a request runs; no fetch retry; no `uvicorn --reload` with the real key; `real_gateway()` refuses to build under pytest.
- **R9 Smoke test before the demo:** one exchange against the real model, asking before sending; then compare the recorded cost with Usage in the Console, and check that R2's reduced schemas are accepted.
- Prices from LiteLLM's map, for reference: Sonnet 5.5 $2/$10, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5 per million tokens. The studies ran Sonnet 4.5 at temperature 0; the demo does not claim comparability with them.

**Settled on Code's proposals (Claude chat, 12.10 evening; details in `docs/notes/00_STATUS.md`, "R7 ו-P1–P10 — הוכרע"):**
- **Structured outputs only for rewriting, synthesis and the gate.** Intent analysis sends no `response_format`: LiteLLM 1.105 inlines every `$ref` and the intent schema then exceeds Anthropic's limits (24 optional properties, 16 `anyOf`). No reducer of our own: LiteLLM's `filter_anthropic_output_schema` applies anyway. Pin `litellm==1.105.0` and snapshot-test the reduced schema of each of the three stages.
- **Format vs content (replaces P3).** Unparseable JSON or a breach of what the provider enforced: format failure, no retry. A breach of the full schema checked in code: content failure, one feedback attempt. In intent analysis every `schema_violation` is a content failure.
- **R7.** `ROW_LIMIT = 30` (provisional). Before synthesis, reserve the whole path (synthesis, gate, redraft, second gate, no feedback attempts); if it exceeds the per-exchange cap, stop before the first synthesis call, with its own fixed notice (R7b). Presented identifiers in history trimmed in display order (R7c).
- **P1 plus:** `real_gateway()` also refuses to start if a `.env` price is lower than LiteLLM's price for the model.
- **P9:** 429 (non-monetary), 5xx and 529: no retry, `request_not_completed`, counted by the breaker.
- **P10:** a refusal is `finish_reason == "content_filter"` in LiteLLM; it is billed and recorded.
- P4–P8 approved as proposed; P5 after the R6 `fill` fix; P6 is `[demo extension]`.

## GitHub: branches and pull requests (14.10)

The remote is `MedAgentPRO` (`github.com/MatanAmran/MedAgentPRO`, Matan's repository; Evyatar has no admin rights). **`main` stays the default branch** and holds only the initial README; the work lives on `master`. So:
- **Every pull request is opened against `master` explicitly:** `https://github.com/MatanAmran/MedAgentPRO/compare/master...<branch>?expand=1`. GitHub offers `main` as the base; check that it says "base: master" before creating it. Merge with "Create a merge commit".
- **The repository's front page shows `main`**: that does not mean the work was not merged.
- **After a push to `master`, GitHub shows a banner "master had recent pushes — Compare & pull request".** It offers a PR from `master` into `main`: **do not click it.** Likewise "N commits ahead of, 1 commit behind main" is normal, since `main` has a separate history (PR #5 merge, 14.10).
- **`stress` runs on every pull request** (to `master`), blocking like the other jobs (about 45 s locally). `schedule` and `workflow_dispatch` run only where the workflow is on the default branch, and `main` holds only the README, so there is no daily run and no "Run workflow" button (CI maintenance, 14.10). Both stay in the workflow, in case `master` becomes the default.
- **After the demo:** the actions' major versions (`checkout@v4`, `setup-node@v4`, `setup-python@v5`, `upload-artifact@v4`, all forced onto Node 24 by GitHub with a warning) to `@v7`, and the runners from `ubuntu-24.04` back to a current image; mypy made blocking. Deferred on purpose before the demo (CI maintenance decisions, 14.10).
- **After each merge, delete the task branch:** on GitHub with `git push MedAgentPRO --delete <branch>` once `git log master..MedAgentPRO/<branch>` is empty, then `git remote prune MedAgentPRO`; locally with `git branch -d` (never `-D` without a decision). A notes commit made after a PR was pushed is carried to `master` by cherry-pick, so `-d` refuses that branch: check with `git cherry -v master <branch>` before asking.

## R9: `backend/.env` is closed to Code's tools (14.10, Evyatar approved)

- `.claude/settings.json` (project root, gitignored, so local to this clone) denies `Read`, `Edit` and `Write` on `./backend/.env`. Checked: a `Read` is refused. In a new clone, recreate it.
- The rule does not cover shell commands. Never display the file (`type`, `cat`, `Get-Content`, or anything else). Only `python -m tools.preflight --real` checks it, and it prints names only.
- **A metadata-only line per model call on the operational channel** (`model call: exchange=… request=… stage=… attempt_number=… feedback_attempt=… check=… max_tokens=… temperature=…`), built from `query_assistance/call_trace.py` (`CallTrace`), the one source for these fields (the audit entry uses it; task 10's panel is to reuse it). It never holds the question, model output, a record value, a rejection message or the key; `check` is a kind or a class name. Task 10a added `output_chars`, the gate's `claims` count and `reasoning_tokens` (how many output tokens were thinking: Claude 5 thinks always, T15). Thinking text (`reasoning_content`, thinking blocks) is never read, kept or shown. Operational, not a requirement in the book (chat, 14.10, 15:30). `main.log_to_stdout()` gives the channel a stdout handler at INFO. **Fixed in `f74aed7`: until then the channel had no handler, so its INFO lines (the exchange cost among them) reached nowhere, and WARNING and up reached stderr only through Python's last resort.**
- **No test reads `backend/.env`** (chat, 14.10, 17:50): `tests/feasibility` runs on the defaults in code, so CI and this machine run the same checks and no failure prints a local value. The configuration in use is checked by `tools.preflight --real`, which also carries T12 as a count of worst-case exchanges left: ❌ below 1, ⚠️ below 15 ("enough for a smoke test, not for a demo").

## Path map and feasibility (Claude chat, 13.10): a standing rule

- `tests/paths/branches.py` reads every branch from the code and the schemas (outcomes, stages, model failure kinds, `except` clauses in `pipeline.run`, notices, endpoints, HTTP codes, gap kinds, roles). **`tests/paths/test_registry.py` fails when a branch has no path in `tests/paths/registry.py`, or a path names a test or branch that does not exist.** Adding a branch means adding its test and its registry entry in the same change.
- `tests/feasibility/constraints.py` lists the limits the design must fit (provider, LiteLLM, SQLite, the demo data), each with its source; `test_feasibility.py` checks each one offline. **Every limit discovered from now on (like temperature on Claude 5) becomes a row and a check first.** Rows of kind `live` are confirmed only in the smoke test (R9).
- Known gaps are `xfail(strict=True)` with the gap id in the reason, so fixing one forces removing the mark.
- `python -m tools.path_map` (from `backend\`) runs both suites and regenerates `docs/notes/60_PATHS.md`. Run it before handing a task back to the chat.

## Decisions in force (details in `docs/notes/40_CHAT.md`)

- **The model never writes a query.** It fills a plan (schema 1); `compiler.py` validates it, injects entitlement and LIMIT, and builds parameterised SQL.
  - **No tuple in any stage schema (15.10).** `BETWEEN` and `IN` are objects (`{"col", "from", "to"}`, `{"col", "values"}`): Anthropic does not enforce `prefixItems`, and LiteLLM 1.105 drops it but keeps `items: false`. `tests/query_assistance/test_provider_enforced.py` holds every valid output in the set against what the provider would enforce, in both reduced forms.
- **The model never writes a value.** Synthesis writes text with symbolic references `{label.alias#row}`; code fills them (book 4.9, [25]). No digit outside a reference unless the question holds that token. Whether the words around references are true is the gate's job, not code's.
- **Code does not read meaning.** Do not add word-list heuristics over model text. Constrain the shape of model output instead.
  - **Text we wrote is not model text (task 10a, chat 14.10, 21:50).** A word list may check the scripted answers in `demo_data/scripted_exchanges.json` (`tests/demo_data/test_scripted_places.py`: no place word between rows tied on the ordering field). Never extend it to model output: there the gate judges the words.
  - **Code gives facts about the rows, the model words them.** `prompts.render_records` marks a row that holds the same value as the row before it in the field the rows are ordered by, `(same label.alias as row n)`, for synthesis, the redraft and the gate. The marker names a field and a row, never a value, and never reaches the history or the identifier check.
  - **The marker follows the first `ORDER_BY` key only** (chat, 14.10, 22:30). Where a plan orders by two keys and a tie on the first is broken by the second, the row is still marked "same" by the first: right, since the place is stated on the first key. Adjacent rows only, so a run of equal values is marked row by row.
- **NFR 19, strict reading.** If no assertion survives the gate after the single redraft, the whole answer is replaced by a request for input, records included (Test 17).
- **NFR 21.** Text held in a record never reaches the rewriting call or the identifier check: the history gets free-text values as references (`Draft.answer(..., for_history=True)`), presented identifiers come from identifier columns only.
- **Existence of an identifier** is checked where it is held (`references` in `schema.json`), so a supplier with no orders is "no records matched", not "absent".
- **"Examined" is shown on every reading**, rendered in code. It is the only defence against a plan that used a plausible but wrong column (book 8.1).
- **End-to-end tests share one server across engines (task 9, CI fix).** Every engine in a Playwright run talks to the same server and database, in order, so what one engine leaves behind (conversations, ratings) is there for the next. A test that assumes a starting state (an empty list, a count) takes that state from the server, never assumes it.
- **Streaming:** stream stage progress, show the answer only after the gate (never unchecked model text).
- **Readable names** in "Examined" are to be added to `demo_data/schema.json` (planned, task 4 or 7).
- `project filles\03_Code` is frozen: it keeps the book's state (synthesis blocked). New work happens here only.
- Schemas marked `"status": "demo"` and fields marked `[demo extension]` are not in the book.

## Layout

- **`backend/`** — run everything from here (`pytest.ini` puts it on `pythonpath`).
  - `query_assistance/` — `compiler.py`, `prompts.py` + `templates/`, `gate.py`, `synthesis.py`, `contract.py`, `pipeline.py` (one exchange end to end: `run`, `rate`). `schema.json`/`relations.json` are the compiler tests' placeholder and stay unchanged. `medagentpro_schemas.json` holds every JSON Schema.
  - `demo_data/` — the demo store: `schema.json`, `relations.json`, `seed.py` (deterministic), `intent_analysis.md` (the demo intent prompt). `demo.db` is generated, not committed.
  - `model_client/client.py` — `ModelGateway`, `ScriptedModelGateway`, `LiteLLMGateway`. `model_client/budget.py` — `BudgetedGateway`, `SpendLedger`, `real_gateway()` (task 5).
  - `shared/circuit_breaker.py`, `persistence/audit_repository.py` — engine pieces from Phase A design.
  - `tests/` — pytest. Model calls in tests always go through `ScriptedModelGateway`.
  - `main.py` — the chat server: scripted model by default, `--real` for the real one; 127.0.0.1, one process. `api/state.py` (demo users, conversations, request ids, cancellation), `api/routes.py` (SSE).
  - `demo_data/scripted.py` + `scripted_exchanges.json` — the scripted model's answers per question (S5); they pass every real check.
  - `reasoning/`, `tools/` — still empty.
- **`frontend/`** — Next.js 16 / React 19 / Tailwind 4 scaffold. `frontend/AGENTS.md` warns this Next.js version differs from training data: read `frontend/node_modules/next/dist/docs/` before writing frontend code.
- **`studies/`** — Phase A feasibility studies (book 3.2, 3.3).
- **`docs/notes/`** — project notes, see above.

## Commands

On Evyatar's machine an Application Control policy blocks `.exe` files in the venv (`pytest.exe` fails). Run tools as Python modules.

```powershell
# Backend (from backend\, venv active)
python -m pip install -r requirements.txt
python -m demo_data.seed          # builds demo_data\demo.db
python -m pytest -q               # the default run; 857 after the 04:45 fixes (18.10)
python -m tools.path_map          # runs tests/paths and tests/feasibility, writes docs/notes/60_PATHS.md
python -m query_assistance.dry_run   # prices two scripted exchanges at the .env prices, no model call, no key
python -m query_assistance.dry_run --model claude-sonnet-5-5   # the same, prices from LiteLLM's local map, no .env
python -m main                    # the chat server on the scripted model, http://127.0.0.1:8000/api/health
python -m tools.preflight         # before a demo: database, server, screen, examples, one scripted question; --real adds .env, the map, the budget; never a model call
python -m tools.fake_real_server  # the real-model path with a fake provider (no key, no network), for the end-to-end tests
python -m pytest -m stress        # load, concurrency, and the property tests at 2000 examples (not in the default run)
UPDATE_GOLDEN=1 python -m pytest tests/golden   # rewrite golden snapshots, printing the diff for review

# Dev tools (python -m pip install -r requirements-dev.txt; configuration in backend\pyproject.toml)
python -m ruff check .            # style and likely bugs
python -m mypy                    # query_assistance and model_client, gradually
python -m bandit -c pyproject.toml -r . -q
python -m pip_audit               # the installed environment, against known vulnerabilities
python -m pytest --cov --cov-branch
```

**Pre-commit hook (task 8 review).** No commit while `ruff` or `bandit` fails on `backend\`. The hook is not in git; install it once per clone by writing this to `.git\hooks\pre-commit` (Git for Windows runs it with its own `sh`):

```sh
#!/bin/sh
# MedAgentPro: no commit while ruff or bandit fails on backend/ (task 8 review). Not in git; see CLAUDE.md.
root=$(git rev-parse --show-toplevel)
py="$root/backend/venv/Scripts/python.exe"
[ -x "$py" ] || py=python
cd "$root/backend" || exit 1
"$py" -m ruff check . || { echo "pre-commit: ruff failed, commit stopped"; exit 1; }
"$py" -m bandit -c pyproject.toml -r . -q >/dev/null 2>&1 || {
  "$py" -m bandit -c pyproject.toml -r . -q
  echo "pre-commit: bandit failed, commit stopped"; exit 1; }
```

Application Control also blocks compiled (mypyc) extensions from unverified publishers, shown as a Windows Security notice ("Part of this app has been blocked … .pyd"). mypy and tomli (a pip-audit dependency) are installed as pure Python for that reason: `pip install --no-binary mypy mypy==<v>`, and tomli from its `py3-none-any` wheel (`pip download --only-binary :all: --implementation py --platform any`). Do not change the security policy.

```powershell
# The demo: two terminals, the scripted model, no key (task 7 review, item 3)
cd backend; python -m demo_data.seed; python -m main             # the server, http://127.0.0.1:8000
cd frontend; npm run build; npm run start                        # the screen, http://localhost:3000
# (stop both with Ctrl+C. npm run dev works too; devIndicators is off in next.config.ts either way)
```

```powershell
# Frontend (from frontend\)
npm run dev                       # the screen, http://localhost:3000, against the server on 127.0.0.1:8000
npx next typegen; npx tsc --noEmit; npx eslint .   # types (typegen: route types a clean checkout lacks) and lint
npx vitest run                    # component and library tests (fixtures: backend golden snapshots)
npx playwright test               # end to end on the built screen (next build + start) and the scripted server (--scripted-delay)
                                  # itself on 8000 and 3000, so stop any dev server first. Locally on the
                                  # installed Edge (channel msedge), in CI on Chromium, Firefox and WebKit (Test 38)
npx playwright test -c playwright.real.config.ts   # the real-model path end to end, with the fake provider
```

Studies (run from inside the study directory — scripts use relative paths like `samples/`, `results/`):

```bash
# Model-calling runs (need ANTHROPIC_API_KEY; PowerShell: $env:ANTHROPIC_API_KEY="...")
python run_study.py                       # 3.2: options --only LOG-01, --conditions baseline|schema, --repeat N, --out DIR
python run_study.py --repeat 5            # 3.3: options --only, --conditions conversational|structured|calibrated|exemplified, --evidence FILE, --out DIR

# Offline analysis over retained results (no API key, no model call)
python verify_counts.py                   # 3.2, produces Table 8
python analyze_references.py              # 3.3, cited-identifier counts (--no-ranges to skip expanding E8-E18 spans)
```

## Studies: rules that matter

- **Results are an evidentiary record.** `studies/PROVENANCE.md` maps every number/figure in the book to a file and the command that reproduces it. Do not modify or delete anything under `results*/` directories, including superseded runs (`results-run1`, `results-fill-log06-invalid-facts`, etc.) — they are retained deliberately. Write new runs to a new `--out` directory.
- **Don't re-run model-calling scripts casually**: they cost API calls and write into `results/` by default, overwriting the primary record.
- Study scripts pin `MODEL = "claude-sonnet-4-5-20250929"`, temperature 0, and fixed prompts; every `results.json` records the model, temperature, output limit and full prompt text. Changing a prompt or setting invalidates comparison with the recorded runs.
- In 3.2, `facts.json` is the fact list in force; `facts-v1.json` is the older list used by superseded runs. Facts are fixed before any run.
- In 3.3, `evidence.json` holds the 3.2 structured summaries with elements labelled `E1`…`En`; `evidence_weak.json` / `evidence_calib.json` belong to unreported calibration runs.

## Gotchas

- `studies/3.2-ingestion/requirements.txt` is **UTF-16 LE with CRLF** (produced by PowerShell redirection). pip reads it, but grep/sed and some editors won't. `backend/requirements.txt` was converted to UTF-8; when regenerating it from PowerShell use `pip freeze | Out-File -Encoding utf8`.
- `jsonschema` must be 4.x (`Draft202012Validator`); 3.x imports fail.
- The study scripts use the Anthropic SDK while the backend dependencies list the `openai` SDK; the backend's model provider is not yet settled in code.
