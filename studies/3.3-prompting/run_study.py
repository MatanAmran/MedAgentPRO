"""
MedAgentPro - Feasibility Study 3.3
Measures whether a prompt can be made to produce output that conforms to a
defined schema, and whether the evidence it cites can be verified.

The input is the structured summary each sample received in study 3.2, with
every element assigned an identifier. The model under test is the Domain
Interpretation stage, which consumes a summary and produces a hypothesis.

Two conditions are run over the same inputs:
  conversational - natural language instructions, no structural constraints
  structured     - the required schema stated explicitly

The API key is read from the ANTHROPIC_API_KEY environment variable.

Usage:
    $env:ANTHROPIC_API_KEY = "..."
    python run_study.py --repeat 5
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import time

from anthropic import Anthropic

MODEL = "claude-sonnet-4-5-20250929"
TEMPERATURE = 0.0
MAX_TOKENS = 1500

EVIDENCE_FILE = pathlib.Path("evidence.json")

REQUIRED_FIELDS = [
    "anomaly_type",
    "causal_hypothesis",
    "confidence_score",
    "evidence_references",
    "recommended_action",
]

CONVERSATIONAL_PROMPT = """You are an expert root cause investigator. Analyze \
this summarized operational evidence and propose a causal explanation for the \
anomaly. Provide your confidence score as a value between 0.0 and 1.0, cite \
the specific evidence IDs that support your conclusion, and suggest \
recommended actions.

Evidence:
{evidence}"""

STRUCTURED_PROMPT = """You are the domain interpretation stage of an \
automated root cause analysis pipeline. Determine the nature of the anomaly \
from the evidence below and propose a causal explanation.

Respond with a single JSON object and nothing else. No prose before or after \
it, no code fences. The object must have exactly these keys:

{{
  "anomaly_type": string,
  "causal_hypothesis": string,
  "confidence_score": number between 0.0 and 1.0,
  "evidence_references": [string],
  "recommended_action": string
}}

"evidence_references" must contain identifiers taken from the evidence list \
below. Do not cite an identifier that does not appear there, and do not \
introduce facts that the evidence does not state.

Evidence:
{evidence}"""

# The structured prompt with the criterion for the score stated explicitly.
# The criterion names properties of the evidence, not of any particular input,
# so it cannot indicate which inputs were degraded.
CALIBRATED_PROMPT = STRUCTURED_PROMPT.replace(
    '"evidence_references" must contain',
    '''"confidence_score" must reflect how far the evidence supports the \
hypothesis. Report a high value when the evidence identifies the component \
that failed, the condition under which it failed, and how often. Report a low \
value when evidence that would decide between explanations is absent, when the \
evidence supports more than one unrelated explanation, or when the hypothesis \
rests on anything the evidence does not state.

"evidence_references" must contain''',
)


# The calibrated prompt with two worked examples placed before the input.
# The examples are drawn from a system that appears in none of the samples,
# so they show the intended scale without indicating which inputs were degraded.
EXAMPLE_BLOCK = """Two worked examples of the intended scale.

Example A. Evidence:
X1. failed_entities: postfix smtpd
X2. error_codes: connection timed out to relay host
X3. anomaly_timestamps: Mar 02 03:14:07
X4. recurring_sources: relay.example.net
X5. occurrence_count: connection timed out = 214

Correct response for Example A, in part:
"causal_hypothesis": "Mail delivery fails because the relay host stopped \
accepting connections, timing out 214 times from a single relay",
"confidence_score": 0.88

The score is high because the evidence names the component, the condition \
under which it failed, and how often.

Example B. Evidence:
Y1. failed_entities: postfix smtpd
Y2. anomaly_timestamps: Mar 02 03:14:07
Y3. anomaly_timestamps: Mar 02 09:41:55
Y4. recurring_sources: relay.example.net

Correct response for Example B, in part:
"causal_hypothesis": "Mail delivery through the relay host failed, for a \
reason the evidence does not state",
"confidence_score": 0.30

The score is low because the evidence names the component and when it failed \
and does not state under what condition, so a delivery fault, a credential \
fault and a network fault remain equally consistent with it.

Now respond for the evidence below, using the same scale.

"""

EXEMPLIFIED_PROMPT = CALIBRATED_PROMPT.replace(
    "Evidence:\n{evidence}", EXAMPLE_BLOCK + "Evidence:\n{evidence}"
)



def format_evidence(items):
    return "\n".join(f"{item['id']}. {item['text']}" for item in items)


def validate(text, valid_ids):
    """Deterministic checks. Returns a dict of independent outcomes.

    Each check is reported separately, because a response can satisfy one and
    fail another, and the study is about which of them a prompt secures.
    """
    result = {
        "json_parsed": False,
        "fields_present": False,
        "confidence_valid": False,
        "references_resolve": False,
        "references_total": 0,
        "references_unresolved": [],
        "failure": None,
    }

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-z]*\n|\n```$", "", stripped)
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as error:
        result["failure"] = f"not valid JSON: {error}"
        return result
    if not isinstance(obj, dict):
        result["failure"] = "not a JSON object"
        return result
    result["json_parsed"] = True

    missing = [f for f in REQUIRED_FIELDS if f not in obj]
    result["fields_present"] = not missing
    if missing:
        result["failure"] = f"missing fields: {', '.join(missing)}"

    score = obj.get("confidence_score")
    result["confidence_valid"] = (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and 0.0 <= score <= 1.0
    )

    refs = obj.get("evidence_references")
    if isinstance(refs, list):
        cited = [str(r).strip() for r in refs]
        result["references_total"] = len(cited)
        unresolved = [r for r in cited if r not in valid_ids]
        result["references_unresolved"] = unresolved
        result["references_resolve"] = bool(cited) and not unresolved

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--only", default=None)
    parser.add_argument("--out", default="results")
    parser.add_argument(
        "--conditions",
        default="conversational,structured",
        help="comma separated list of conditions to run",
    )
    parser.add_argument(
        "--evidence",
        default="evidence.json",
        help="evidence file to read",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set in this shell.")

    client = Anthropic()
    evidence = json.loads(
        pathlib.Path(args.evidence).read_text(encoding="utf-8")
    )
    wanted = [c.strip() for c in args.conditions.split(",") if c.strip()]
    for condition in wanted:
        if condition not in (
            "conversational", "structured", "calibrated", "exemplified"
        ):
            raise SystemExit(f"unknown condition: {condition}")
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(exist_ok=True)

    sample_ids = sorted(evidence)
    if args.only:
        sample_ids = [args.only]

    runs = []
    for sample_id in sample_ids:
        items = evidence[sample_id]["evidence"]
        valid_ids = {item["id"] for item in items}
        listing = format_evidence(items)

        templates = {
            "conversational": CONVERSATIONAL_PROMPT,
            "structured": STRUCTURED_PROMPT,
            "calibrated": CALIBRATED_PROMPT,
            "exemplified": EXEMPLIFIED_PROMPT,
        }
        for condition in wanted:
            template = templates[condition]
            prompt = template.format(evidence=listing)
            for repetition in range(1, args.repeat + 1):
                started = time.time()
                try:
                    response = client.messages.create(
                        model=MODEL,
                        max_tokens=MAX_TOKENS,
                        temperature=TEMPERATURE,
                        messages=[{"role": "user", "content": prompt}],
                    )
                except Exception as error:
                    print(f"{sample_id} [{condition} {repetition}]: call failed")
                    runs.append(
                        {
                            "sample_id": sample_id,
                            "condition": condition,
                            "repetition": repetition,
                            "outcome": "call_failed",
                            "error": str(error),
                        }
                    )
                    write_record(out_dir, runs)
                    continue

                text = "".join(
                    b.text for b in response.content if b.type == "text"
                )
                elapsed = time.time() - started
                checks = validate(text, valid_ids)

                name = f"{sample_id}_{condition}_{repetition}.txt"
                (out_dir / name).write_text(text, encoding="utf-8")

                runs.append(
                    {
                        "sample_id": sample_id,
                        "condition": condition,
                        "repetition": repetition,
                        "outcome": "responded",
                        "evidence_items": len(items),
                        "output_tokens": response.usage.output_tokens,
                        "output_truncated": response.stop_reason == "max_tokens",
                        "elapsed_seconds": round(elapsed, 1),
                        **checks,
                    }
                )
                write_record(out_dir, runs)
                flags = "".join(
                    "1" if checks[k] else "0"
                    for k in (
                        "json_parsed",
                        "fields_present",
                        "confidence_valid",
                        "references_resolve",
                    )
                )
                print(
                    f"{sample_id} [{condition} {repetition}]: {flags} "
                    f"refs {checks['references_total']} "
                    f"unresolved {len(checks['references_unresolved'])} "
                    f"{elapsed:.1f}s"
                )

    report(runs)
    print(f"\nWrote {out_dir / 'results.json'} and one output file per run.")


def write_record(out_dir, runs):
    record = {
        "run_timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "prompts": {
            "conversational": CONVERSATIONAL_PROMPT,
            "structured": STRUCTURED_PROMPT,
            "calibrated": CALIBRATED_PROMPT,
            "exemplified": EXEMPLIFIED_PROMPT,
        },
        "runs": runs,
    }
    (out_dir / "results.json").write_text(
        json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8"
    )


def report(runs):
    print("\nPass rate by check, over all runs of each condition:")
    header = f"  {'condition':16}{'n':>4}{'json':>8}{'fields':>8}{'score':>8}{'refs':>8}"
    print(header)
    for condition in ("conversational", "structured"):
        group = [r for r in runs if r.get("condition") == condition
                 and r.get("outcome") == "responded"]
        if not group:
            continue
        n = len(group)
        cells = []
        for key in ("json_parsed", "fields_present",
                    "confidence_valid", "references_resolve"):
            passed = sum(1 for r in group if r[key])
            cells.append(f"{passed}/{n}")
        print(f"  {condition:16}{n:>4}" + "".join(f"{c:>8}" for c in cells))
    unresolved = sum(
        len(r.get("references_unresolved", []))
        for r in runs if r.get("outcome") == "responded"
    )
    cited = sum(r.get("references_total", 0)
                for r in runs if r.get("outcome") == "responded")
    print(f"\n  Identifiers cited: {cited}, of which unresolved: {unresolved}")


if __name__ == "__main__":
    main()
