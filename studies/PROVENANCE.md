# MedAgentPro — Provenance of Reported Figures

Every number, table and figure the Phase A book reports from a feasibility
study, the file it comes from, and the command that produces it again.

Nothing here is a second measurement. Each entry points at a record a run
already wrote, or at a script that reads those records and counts. No entry
requires an API key, and none of them calls a model.

Kept beside the two study directories so that a figure can be traced without
reading the scripts first.

---

## Section 3.2 — Data Ingestion and Structuring

Directory: `studies/3.2-ingestion`

| Reported in the book | Comes from | How to produce it again |
|---|---|---|
| Compression ratios, 18:1 to 288:1 (3.2.2, Figure 1 upper panel) | `results/results.json`, field `compression_ratio` on each run | Read the record. Ratios are `input_tokens / summary_tokens`, both counted by the model's own tokenizer through `messages.count_tokens` |
| Token counts, 7,442 to 178,280 (3.2.2) | `results/results.json`, field `input_tokens` | As above |
| Facts retained, 10 and 13 of 15 on the small samples, 4 and 5 of 15 on the large (3.2.2, Figure 1 lower panel) | `results/results.json`, field `facts_found_auto` on each run | Read the record. The designated facts are `facts.json`, fixed before any run |
| Facts retained overall, 14 of 30 and 18 of 30 (3.2.3) | Same field, summed across samples | As above |
| LOG-06 results | `results-log06/results.json` | `python run_study.py --only LOG-06` |
| Stability, three byte-identical outputs of five, 13 percent spread, counts 47 and 52 (3.2.2) | `results-stability/results.json` | `python run_study.py --only LOG-01 --repeat 5 --out results-stability` |
| Sample that exceeds the context limit (3.2.2) | `results/results.json`, outcome `exceeds_context_limit` | Read the record. The sample is counted, not attempted |
| Schema rejected twice on the largest input (3.2.2) | `results/results.json`, fields `repair_used` and `repair_reason` | Read the record |
| **Table 8, occurrence counts against the source** | **`verify_counts.py`** | `python verify_counts.py` |

### On Table 8

Table 8 reports how many of the counts a summary produced are correct, which
needs a true value for every count the model reported. Those true values were
not recorded by `run_study.py`, which checks only whether a designated fact
string is present and does not count anything in the source.

`verify_counts.py` produces them. For each `{item, count}` entry in a structured
summary it counts how many times the item string occurs in the log that summary
was produced from, and reports the true count beside the reported one.

An item the model paraphrased does not occur literally in the log. Those are
reported as `not literal` rather than as zero, since a phrase the source does
not contain is not the same as a phrase that occurs zero times, and the
difference decides whether that count can be checked at all.

Run it before quoting Table 8. Where its totals differ from the table, the
table is what changes.

---

## Section 3.3 — Prompt Engineering for Structured Output

Directory: `studies/3.3-prompting`

| Reported in the book | Comes from | How to produce it again |
|---|---|---|
| Table 9, parses as an object, 0 of 25 and 25 of 25 | `results/results.json`, field `json_parsed` | Read the record, or `python run_study.py --repeat 5` |
| Table 9, five fields present, 25 of 25 | Same, field `fields_present` | As above |
| Table 9, confidence score in range, 25 of 25 | Same, field `confidence_valid` | As above |
| Table 9, identifiers all resolve | **`analyze_references.py`** | `python analyze_references.py` |
| 1,532 identifiers cited, none absent (3.3.2) | Same script | Same command |
| 88 percent and 62 percent of the evidence cited (3.3.2) | Same script, column `coverage` | Same command |
| Console screenshot (3.3.2) | `results-screenshot/` | `python run_study.py --only LOG-01 --repeat 1 --out results-screenshot` |
| Five samples rather than six (3.3.1) | `evidence.json` holds five entries. The sixth structured summary in 3.2 reached the output limit before its object closed | Read `evidence.json` |
| 53 numeric values in 20 structured hypotheses, none from outside the input (3.3.2) | Read from the response files in `results/` | Not automated. See the note below |
| One of ten values matches the original log (3.3.2) | Read from the response files against the sample logs | Not automated. See the note below |

### On the identifier counts

`run_study.py` records how many identifiers a response cites only where that
response parses as an object, and no conversational response does. The run
record therefore reports zero identifiers for that whole condition while the
responses themselves cite identifiers in prose.

`analyze_references.py` reads the retained response files and counts under both
conditions by the same rule. Two decisions in it are worth knowing before the
figures are quoted.

An identifier is counted once per response. A conversational response names the
same identifier in a list of primary evidence and again in prose, while a
structured response returns a list without repetition, so counting every
occurrence would not compare like with like.

A span such as `E8-E18` names eleven identifiers in two tokens, and a structured
response returns a list and cannot write one. Spans are expanded, and
`--no-ranges` reports the literal tokens instead. Without expansion the
direction of the result reverses: the structured condition then appears to cite
more than the conversational one.

### On the two figures that are not automated

The count of numeric values in the hypotheses, and the ten values checked
against the original logs, were read from the response files rather than
produced by a script. The response files are retained, so both can be checked,
but neither is reproduced by a command. The book states the second as a limit
on its own scope: the ten were chosen for being countable and are not a sample
of every value reported.

---

## What produces nothing reported

| Directory | Why it exists |
|---|---|
| `3.2-ingestion/results-run1` | First run, ended on an unhandled context limit before `results.json` was written |
| `3.2-ingestion/results-fill-log06-invalid-facts` | LOG-06 against a fact list naming two facts absent from its input. Retained because it records an error that was found and corrected |
| `3.3-prompting/results-weak` | Two degraded inputs. Measures whether the confidence score separates adequate evidence from inadequate |
| `3.3-prompting/results-calibrated` | The same question with the scoring criterion stated explicitly |
| `3.3-prompting/results-exemplified` | The same question again with two worked examples |

The last three are not reported in the book, because the book makes no claim
that the confidence score is calibrated. They are retained because they
identify what phase B has to test.

---

## Scripts

| Script | What it does | Calls a model |
|---|---|---|
| `3.2-ingestion/run_study.py` | Runs the study. Records the model, temperature, output limit and both prompts in full | Yes |
| `3.2-ingestion/verify_counts.py` | Counts reported items in the source logs, for Table 8 | No |
| `3.3-prompting/run_study.py` | Runs the study. Same record, four prompts | Yes |
| `3.3-prompting/analyze_references.py` | Counts cited identifiers in both conditions | No |

Every `results.json` carries the model identifier, the temperature, the output
limit and the full text of every prompt used, so a run can be repeated exactly
as it was made.
