# Feasibility Study 3.2 — Data Ingestion and Structuring

Measures the token compression achieved by the summarization stage, and
whether facts designated as critical before the run survive it.

Reported in section 3.2 of the project book.

## Data

Six samples built from five LogHub datasets, plus one oversized sample
retained but not reported. LogHub is a published collection of real
system logs released for log-analytics research.

https://github.com/logpai/loghub

Zhu et al., "Loghub: A Large Collection of System Log Datasets for
AI-driven Log Analytics," ISSRE 2023. Cited as [35] in the book.

## Method

Each sample is summarized under two conditions, free prose and a defined
output structure, using identical instruction content at temperature
zero. Only the required output format differs.

The five facts designated for each sample are fixed in `facts.json`
before any run. Fact presence is matched literally in code and then
reviewed by hand, because a literal match can fall inside an unrelated
string and a fact can be stated in different words than the one
designated.

## Runs

| Directory | Contents | Status |
|---|---|---|
| `results` | Both conditions, LOG-01 to LOG-06 | Primary record, except LOG-06 |
| `results-log06` | LOG-06, corrected fact list | Reported |
| `results-stability` | LOG-01 baseline repeated five times | Reported |
| `results-fill-log06-invalid-facts` | LOG-06 against a fact list containing two facts absent from its input | Superseded |
| `results-run1` | First run, baseline only, ended on an unhandled context limit before `results.json` was written | Superseded |

`facts.json` is the fact list in force. `facts-v1.json` is the list used
by the two superseded runs; its LOG-06 entries name facts that do not
occur in `LOG-06.log`.

The console output of the first run was not retained.

## Run it

    pip install -r requirements.txt
    export ANTHROPIC_API_KEY="..."        # PowerShell: $env:ANTHROPIC_API_KEY="..."
    python run_study.py

Options: `--only SAMPLE`, `--conditions baseline|schema`, `--repeat N`,
`--out DIR`.

Every `results.json` records the model version, temperature, output
limit and the full text of both prompts.
