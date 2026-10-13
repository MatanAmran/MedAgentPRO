# Feasibility Study 3.2 — Data Ingestion and Structuring

Measures the token compression achieved by the summarization stage,
and whether facts designated as critical before the run survive it.

## Data
Six samples built from five LogHub datasets, plus one oversized sample.
LogHub is a published collection of real system logs released for log
analytics research. https://github.com/logpai/loghub

Zhu et al., "Loghub: A Large Collection of System Log Datasets for
AI-driven Log Analytics," ISSRE 2023.

## Method
Each sample is summarized under two conditions, free prose and a defined
output structure, using an identical instruction and temperature zero.
The facts in facts.json were fixed before any run. Fact presence is
matched literally in code and then reviewed by hand.

## Runs

| Directory | Contents | Status |
|---|---|---|
| results | Both conditions, LOG-01 to LOG-06. The primary record. | Reported, except LOG-06 |
| results-log06 | LOG-06 under both conditions, corrected fact list. | Reported |
| results-stability | LOG-01 baseline repeated five times. | Reported |
| results-fill-log06-invalid-facts | LOG-06 measured against a fact list containing two facts absent from its input. | Superseded by results-log06 |
| results-run1 | First run, baseline only. Ended on an unhandled context limit before results.json was written. | Superseded by results |

facts-v1.json is the fact list used by the two superseded runs. The
LOG-06 entries in it name facts that do not occur in LOG-06.log.

## Run
    pip install -r requirements.txt
    $env:ANTHROPIC_API_KEY = "..."
    python run_study.py

Options: --only SAMPLE, --conditions baseline|schema, --repeat N, --out DIR