# Feasibility Study 3.3 — Prompt Engineering for Structured Output

Measures whether a prompt can be made to produce output the deterministic
stages of the pipeline can act on, and whether the evidence it cites can
be verified.

Reported in section 3.3 of the project book.

## Input

The structured summary each sample received in study 3.2, with every
element assigned an identifier `E1` to `En`, held in `evidence.json`.

Five of the six samples are used. The structured output for the sixth
reached the output limit in study 3.2 before its object closed, so no
identified evidence list can be derived from it.

## Method

Two conditions over the same inputs. The first states the task in
natural language and constrains nothing; it was written before this
study was designed. The second states the same task and adds the
required structure. Instruction content is identical.

Four properties of each response are checked in code: whether it parses
as an object, whether the five named fields are present, whether the
confidence score is a number in range, and whether every identifier
cited appears in the evidence list supplied.

Identifier counts reported in the book are produced by analyze_references.py, which reads the retained response files. run_study.py records them only for a response that parses as an object, and no conversational response does. A conversational response may write a span such as E8-E18, which names eleven identifiers in two tokens; spans are expanded, and --no-ranges reports the literal tokens instead.

## Runs

| Directory | Contents | Status |
|---|---|---|
| `results` | Five samples, two conditions, five repetitions | Primary record |
| `results-weak` | Two degraded inputs, structured condition | Not reported in the book |
| `results-calibrated` | Four inputs, score criterion stated explicitly | Not reported in the book |
| `results-exemplified` | Four inputs, two worked examples added | Not reported in the book |
| `results-screenshot` | Single run of LOG-01 under both conditions | Source of the console screenshot in 3.3.2 |

The last three measure whether the confidence score separates adequate
from inadequate evidence. It does not, under any of the three phrasings.
They are not reported in the book because the book makes no claim that
the score is calibrated. They are retained because they identify what
needs testing in phase B.

`evidence_weak.json` and `evidence_calib.json` belong to those runs.

## Run it

    pip install -r requirements.txt
    export ANTHROPIC_API_KEY="..."        # PowerShell: $env:ANTHROPIC_API_KEY="..."
    python run_study.py --repeat 5

Options: `--only SAMPLE`, `--conditions conversational|structured|calibrated|exemplified`,
`--evidence FILE`, `--repeat N`, `--out DIR`.

Every `results.json` records the model version, temperature, output
limit and the full text of every prompt used.

python analyze_references.py          # identifier counts, no API calls