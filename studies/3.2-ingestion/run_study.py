"""
MedAgentPro - Feasibility Study 3.2
Measures the token compression achieved by the summarization stage,
and checks whether facts designated as critical before the run survive it.

Two conditions are run over the same samples and the same fact list:
  baseline - free prose output, no imposed structure
  schema   - output must match a defined structure, with one repair attempt
Only the output format differs. The instruction content is identical.

Options:
  --only LOG-01           restrict the run to one sample
  --conditions schema     restrict the run to one condition
  --repeat 5              run each combination N times, to measure stability
  --out results-stability write to a different directory

The API key is read from the ANTHROPIC_API_KEY environment variable.
It is never written into this file.

Usage:
    export ANTHROPIC_API_KEY="..."      # PowerShell: $env:ANTHROPIC_API_KEY="..."
    python run_study.py
"""

import json
import os
import argparse
import re
import pathlib
import time
import datetime

from anthropic import Anthropic

# ----------------------------------------------------------------------
# Configuration. Every sample is processed with these exact settings.
# ----------------------------------------------------------------------

MODEL = "claude-sonnet-4-5-20250929"
TEMPERATURE = 0.0          # deterministic, so the run is reproducible
MAX_TOKENS = 2000
CONTEXT_LIMIT = 200_000    # model input ceiling; samples above it are recorded,
                           # not attempted, since exceeding it is itself a result

SAMPLES_DIR = pathlib.Path("samples")
FACTS_FILE = pathlib.Path("facts.json")
OUTPUT_DIR = pathlib.Path("results")

# The identical prompt used for every sample. Do not vary it between runs.
SUMMARIZATION_PROMPT = """You are the summarization stage of an automated \
root cause analysis pipeline. You will be given raw operational log data.

Produce a condensed summary that a later reasoning stage will use to \
determine why something failed. The later stage will not see the raw data, \
so anything you omit is lost.

Retain:
- identifiers of entities that failed
- error codes and error types
- timestamps of anomalous events
- sources that recur across failures
- how many times each anomaly occurred

Discard repetitive routine entries that carry no diagnostic value.

Write plain prose and lists. Do not add commentary about your own process.

Raw data:
---
{data}
---"""

# The same instruction content as above, with a defined output structure.
SCHEMA_PROMPT = """You are the summarization stage of an automated \
root cause analysis pipeline. You will be given raw operational log data.

Produce a condensed summary that a later reasoning stage will use to \
determine why something failed. The later stage will not see the raw data, \
so anything you omit is lost.

Retain:
- identifiers of entities that failed
- error codes and error types
- timestamps of anomalous events
- sources that recur across failures
- how many times each anomaly occurred

Discard repetitive routine entries that carry no diagnostic value.

Respond with a single JSON object and nothing else. No prose before or \
after it, no code fences. The object must have exactly these keys:

{{
  "failed_entities": [string],
  "error_codes": [string],
  "anomaly_timestamps": [string],
  "recurring_sources": [string],
  "occurrence_counts": [{{"item": string, "count": integer}}],
  "narrative": string
}}

Every list must contain at least one element. "narrative" is a short \
factual account of what went wrong.

Raw data:
---
{data}
---"""

# Deterministic validation of the structured output. This is the schema gate
# described in section 4.7, reproduced here in its simplest form.
REQUIRED_LIST_FIELDS = [
    "failed_entities",
    "error_codes",
    "anomaly_timestamps",
    "recurring_sources",
]


def validate_schema(text):
    """Return (parsed_object, None) on success, or (None, reason) on failure."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-z]*\n|\n```$", "", stripped)
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as error:
        return None, f"output is not valid JSON: {error}"
    if not isinstance(obj, dict):
        return None, "output is not a JSON object"
    for field in REQUIRED_LIST_FIELDS:
        if field not in obj:
            return None, f"missing field: {field}"
        if not isinstance(obj[field], list) or not obj[field]:
            return None, f"field is not a non-empty list: {field}"
    if "occurrence_counts" not in obj or not isinstance(
        obj["occurrence_counts"], list
    ):
        return None, "missing or malformed field: occurrence_counts"
    for entry in obj["occurrence_counts"]:
        if not isinstance(entry, dict) or "item" not in entry or "count" not in entry:
            return None, "occurrence_counts entry lacks item or count"
        if not isinstance(entry["count"], int):
            return None, "occurrence_counts count is not an integer"
    if not isinstance(obj.get("narrative"), str) or not obj["narrative"].strip():
        return None, "narrative is missing or empty"
    return obj, None


def count_tokens(client, text):
    """Exact token count from the model's own tokenizer."""
    response = client.messages.count_tokens(
        model=MODEL,
        messages=[{"role": "user", "content": text}],
    )
    return response.input_tokens


def check_facts(summary, facts):
    """Literal check of each designated fact against the summary text.

    Returns a per-fact result. Matches are case-insensitive.
    Facts of type 'count' are matched literally but flagged for review,
    because a bare number can appear by coincidence.
    """
    lowered = summary.lower()
    results = []
    for fact in facts:
        found = fact["needle"].lower() in lowered
        results.append(
            {
                "type": fact["type"],
                "needle": fact["needle"],
                "note": fact["note"],
                "auto_found": found,
                "needs_review": fact["type"] == "count",
            }
        )
    return results


def write_record(runs):
    """Persist after every run, so a later failure cannot erase earlier work."""
    record = {
        "run_timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "context_limit": CONTEXT_LIMIT,
        "prompts": {"baseline": SUMMARIZATION_PROMPT, "schema": SCHEMA_PROMPT},
        "runs": runs,
    }
    (OUTPUT_DIR / "results.json").write_text(
        json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8"
    )


def call_model(client, prompt):
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    return text, response.usage.output_tokens, response.stop_reason


def run_one(client, sample_id, raw, facts, condition, label=None):
    """Run a single sample under a single condition."""
    label = label or condition
    template = SUMMARIZATION_PROMPT if condition == "baseline" else SCHEMA_PROMPT
    prompt = template.format(data=raw)

    input_tokens = count_tokens(client, prompt)
    if input_tokens > CONTEXT_LIMIT:
        print(
            f"{sample_id} [{label}]: {input_tokens:,} tokens exceeds the "
            f"{CONTEXT_LIMIT:,} limit, not attempted"
        )
        return {
            "sample_id": sample_id,
            "condition": condition,
            "input_tokens": input_tokens,
            "outcome": "exceeds_context_limit",
            "summary_tokens": None,
            "compression_ratio": None,
            "facts": [],
        }

    print(f"{sample_id} [{label}]: summarizing ({input_tokens:,} tokens in)...")
    started = time.time()
    try:
        text, out_tokens, stop_reason = call_model(client, prompt)
    except Exception as error:
        print(f"{sample_id} [{label}]: call failed, recorded and skipped")
        return {
            "sample_id": sample_id,
            "condition": condition,
            "input_tokens": input_tokens,
            "outcome": "call_failed",
            "error": str(error),
            "summary_tokens": None,
            "compression_ratio": None,
            "facts": [],
        }

    # The schema gate. On failure the output is returned once for repair,
    # which is the smallest form of the loop described in section 4.7.
    schema_valid = None
    repair_used = False
    repair_reason = None
    if condition == "schema":
        parsed, reason = validate_schema(text)
        if parsed is None:
            repair_used = True
            repair_reason = reason
            print(f"{sample_id} [{label}]: rejected ({reason}), one repair attempt")
            repair = (
                f"{prompt}\n\nYour previous response was rejected because "
                f"{reason}. Respond again with a single valid JSON object "
                f"matching the required keys, and nothing else."
            )
            try:
                text, extra, stop_reason = call_model(client, repair)
                out_tokens += extra
                parsed, reason = validate_schema(text)
            except Exception as error:
                reason = f"repair call failed: {error}"
        schema_valid = parsed is not None
        if not schema_valid:
            print(f"{sample_id} [{label}]: still invalid after repair ({reason})")

    elapsed = time.time() - started
    (OUTPUT_DIR / f"{sample_id}_{label}.txt").write_text(text, encoding="utf-8")

    # Fact checking runs on the raw output text in both conditions, so the
    # measurement itself is identical and only the format differs.
    fact_results = check_facts(text, facts)
    found = sum(1 for f in fact_results if f["auto_found"])

    print(
        f"{sample_id} [{label}]: {input_tokens:,} -> {out_tokens:,} tokens "
        f"({input_tokens / out_tokens:.1f}:1), facts {found}/{len(fact_results)} "
        f"(auto), {elapsed:.1f}s"
    )
    return {
        "sample_id": sample_id,
        "condition": condition,
        "label": label,
        "input_tokens": input_tokens,
        "outcome": "summarized",
        "output_truncated": stop_reason == "max_tokens",
        "schema_valid": schema_valid,
        "repair_used": repair_used,
        "repair_reason": repair_reason,
        "summary_tokens": out_tokens,
        "compression_ratio": round(input_tokens / out_tokens, 1),
        "facts_total": len(fact_results),
        "facts_found_auto": found,
        "elapsed_seconds": round(elapsed, 1),
        "facts": fact_results,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Feasibility study 3.2 run")
    parser.add_argument("--only", default=None, help="restrict to one sample id")
    parser.add_argument(
        "--conditions",
        default="baseline,schema",
        help="comma separated list of conditions to run",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run each combination this many times, for stability measurement",
    )
    parser.add_argument("--out", default="results", help="output directory")
    return parser.parse_args()


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set in this shell.")

    args = parse_args()
    global OUTPUT_DIR
    OUTPUT_DIR = pathlib.Path(args.out)
    OUTPUT_DIR.mkdir(exist_ok=True)

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    for condition in conditions:
        if condition not in ("baseline", "schema"):
            raise SystemExit(f"unknown condition: {condition}")

    client = Anthropic()
    facts_by_sample = json.loads(FACTS_FILE.read_text(encoding="utf-8"))
    sample_ids = sorted(facts_by_sample)
    if args.only:
        if args.only not in facts_by_sample:
            raise SystemExit(f"unknown sample: {args.only}")
        sample_ids = [args.only]

    runs = []
    for sample_id in sample_ids:
        path = SAMPLES_DIR / f"{sample_id}.log"
        if not path.exists():
            print(f"{sample_id}: file not found, skipped")
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        for condition in conditions:
            for repetition in range(1, args.repeat + 1):
                label = condition if args.repeat == 1 else f"{condition}-{repetition}"
                result = run_one(
                    client, sample_id, raw, facts_by_sample[sample_id],
                    condition, label,
                )
                result["source_file"] = path.name
                result["input_bytes"] = path.stat().st_size
                result["repetition"] = repetition
                runs.append(result)
                write_record(runs)

    if args.repeat > 1:
        report_stability(runs)

    print(f"\nWrote {OUTPUT_DIR / 'results.json'} and one output file per run.")
    print(
        "Auto fact matching is literal substring matching. Review the output "
        "files before reporting these counts."
    )


def report_stability(runs):
    """Spread across repetitions of an identical call, printed for convenience.

    The same numbers are in results.json. Nothing here is measured a second
    time, so this cannot disagree with the record.
    """
    groups = {}
    for run in runs:
        if run.get("outcome") != "summarized":
            continue
        key = (run["sample_id"], run["condition"])
        groups.setdefault(key, []).append(run)

    print("\nStability across identical repetitions:")
    for (sample_id, condition), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        ratios = [r["compression_ratio"] for r in group]
        facts = [r["facts_found_auto"] for r in group]
        tokens = [r["summary_tokens"] for r in group]
        spread = (max(ratios) - min(ratios)) / min(ratios) * 100
        print(
            f"  {sample_id} [{condition}] n={len(group)}  "
            f"ratio {min(ratios)}-{max(ratios)} (spread {spread:.0f}%)  "
            f"output tokens {min(tokens)}-{max(tokens)}  "
            f"facts {min(facts)}-{max(facts)}"
        )


if __name__ == "__main__":
    main()
