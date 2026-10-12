# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working with Evyatar

- **Reply in Hebrew, always**, including summaries after long tool work. Check before sending. Code, comments and commit messages stay in English.
- Be concise and practical. For non-trivial work, propose a short plan and wait for approval before writing code.
- Point out bugs, risks and better alternatives unprompted. Say which parts of a proposal come from the book and which are your own.
- **The book is the source of truth** (`C:\Users\EvyatarS\project filles\01_Book\`). Where code and book differ, raise it, do not silently pick one.
- Never delete files from `project filles\` (the original research folder). Copy, do not move.

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
| 4 | Pipeline orchestration: rewrite, intent, compile, retrieve, synthesis, gate, contract. Retry within attempts, one redraft (NFR 19), exchange record and rating (FR 12, FR 13) | **next** |
| 5 | Model gateway configuration: LiteLLM for a real model, scripted fallback. The API key comes from Evyatar, kept in `backend/.env` | open |
| 6 | FastAPI endpoint for the chat | open |
| 7 | Chat screen in Next.js, after Figure 15 of the book | open |
| 8 | Demo question set covering each behaviour | open |
| 9 | End-to-end tests on the scripted model | open |

## Decisions in force (details in `docs/notes/40_CHAT.md`)

- **The model never writes a query.** It fills a plan (schema 1); `compiler.py` validates it, injects entitlement and LIMIT, and builds parameterised SQL.
- **The model never writes a value.** Synthesis writes text with symbolic references `{label.alias#row}`; code fills them (book 4.9, [25]). No digit outside a reference unless the question holds that token. Whether the words around references are true is the gate's job, not code's.
- **Code does not read meaning.** Do not add word-list heuristics over model text. Constrain the shape of model output instead.
- **NFR 19, strict reading.** If no assertion survives the gate after the single redraft, the whole answer is replaced by a request for input, records included (Test 17).
- **NFR 21.** Text held in a record never reaches the rewriting call or the identifier check: the history gets free-text values as references (`Draft.answer(..., for_history=True)`), presented identifiers come from identifier columns only.
- **Existence of an identifier** is checked where it is held (`references` in `schema.json`), so a supplier with no orders is "no records matched", not "absent".
- **"Examined" is shown on every reading**, rendered in code. It is the only defence against a plan that used a plausible but wrong column (book 8.1).
- **Streaming:** stream stage progress, show the answer only after the gate (never unchecked model text).
- **Readable names** in "Examined" are to be added to `demo_data/schema.json` (planned, task 4 or 7).
- `project filles\03_Code` is frozen: it keeps the book's state (synthesis blocked). New work happens here only.
- Schemas marked `"status": "demo"` and fields marked `[demo extension]` are not in the book.

## Layout

- **`backend/`** — run everything from here (`pytest.ini` puts it on `pythonpath`).
  - `query_assistance/` — `compiler.py`, `prompts.py` + `templates/`, `gate.py`, `synthesis.py`, `contract.py`. `schema.json`/`relations.json` are the compiler tests' placeholder and stay unchanged. `medagentpro_schemas.json` holds every JSON Schema.
  - `demo_data/` — the demo store: `schema.json`, `relations.json`, `seed.py` (deterministic), `intent_analysis.md` (the demo intent prompt). `demo.db` is generated, not committed.
  - `model_client/client.py` — `ModelGateway`, `ScriptedModelGateway`, `LiteLLMGateway`.
  - `shared/circuit_breaker.py`, `persistence/audit_repository.py` — engine pieces from Phase A design.
  - `tests/` — pytest. Model calls in tests always go through `ScriptedModelGateway`.
  - `main.py`, `api/`, `reasoning/`, `tools/` — still empty.
- **`frontend/`** — Next.js 16 / React 19 / Tailwind 4 scaffold. `frontend/AGENTS.md` warns this Next.js version differs from training data: read `frontend/node_modules/next/dist/docs/` before writing frontend code.
- **`studies/`** — Phase A feasibility studies (book 3.2, 3.3).
- **`docs/notes/`** — project notes, see above.

## Commands

On Evyatar's machine an Application Control policy blocks `.exe` files in the venv (`pytest.exe` fails). Run tools as Python modules.

```powershell
# Backend (from backend\, venv active)
python -m pip install -r requirements.txt
python -m demo_data.seed          # builds demo_data\demo.db
python -m pytest -q               # all tests; 267 at the end of task 3

# Frontend (from frontend\)
npm run dev
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
