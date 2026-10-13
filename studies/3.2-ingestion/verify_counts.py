"""
MedAgentPro - Feasibility Study 3.2, occurrence count verification

Table 8 of the book reports, for each sample, how many of the occurrence counts
the summarization stage produced are correct. That table needs a true value for
every count the model reported, and this script produces those values.

For each {item, count} entry in a structured summary, it counts how many times
the item string occurs in the log the summary was produced from, and prints the
true count beside the reported one. It makes no API calls: it reads a run that
already happened and counts strings in the source files.

An item the model paraphrased will not occur literally in the log. Those are
reported as "not literal" rather than as zero, since a phrase the source does
not contain is not the same as a phrase that occurs zero times, and the
difference decides whether the count can be checked at all.

Usage:
    python verify_counts.py
    python verify_counts.py --results results --samples samples
    python verify_counts.py --csv counts.csv
"""

import argparse
import csv
import json
import pathlib
import re
import sys

SCHEMA_CONDITION = "schema"


def load_summary(results_dir, sample_id, label):
    """The structured summary as the run wrote it, or None if it is unusable."""
    path = results_dir / ("%s_%s.txt" % (sample_id, label))
    if not path.exists():
        return None, "no output file: %s" % path.name
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as error:
        return None, "output is not valid JSON: %s" % error
    if not isinstance(obj, dict):
        return None, "output is not a JSON object"
    return obj, None


def count_in_source(haystack_lower, item):
    """Occurrences of one item string in the log, case-insensitively."""
    needle = str(item).strip().lower()
    if not needle:
        return None
    return haystack_lower.count(needle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="results")
    parser.add_argument("--samples", default="samples")
    parser.add_argument("--csv", default=None)
    args = parser.parse_args()

    results_dir = pathlib.Path(args.results)
    samples_dir = pathlib.Path(args.samples)
    for d in (results_dir, samples_dir):
        if not d.is_dir():
            raise SystemExit("no such directory: %s" % d)

    record_path = results_dir / "results.json"
    if not record_path.exists():
        raise SystemExit("no results.json in %s" % results_dir)
    record = json.loads(record_path.read_text(encoding="utf-8"))

    rows = []
    for run in record["runs"]:
        if run.get("condition") != SCHEMA_CONDITION:
            continue
        if run.get("outcome") != "summarized":
            continue
        sample_id = run["sample_id"]
        label = run.get("label") or run["condition"]

        log_path = samples_dir / ("%s.log" % sample_id)
        if not log_path.exists():
            print("skipped %s: %s not found" % (sample_id, log_path.name), file=sys.stderr)
            continue
        source = log_path.read_text(encoding="utf-8", errors="replace").lower()

        summary, problem = load_summary(results_dir, sample_id, label)
        if summary is None:
            print("skipped %s: %s" % (sample_id, problem), file=sys.stderr)
            continue

        entries = summary.get("occurrence_counts")
        if not isinstance(entries, list):
            print("skipped %s: no occurrence_counts list" % sample_id, file=sys.stderr)
            continue

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = entry.get("item")
            reported = entry.get("count")
            actual = count_in_source(source, item)
            if actual is None:
                verdict = "empty item"
            elif actual == 0:
                verdict = "not literal"
            elif actual == reported:
                verdict = "correct"
            elif isinstance(reported, int) and reported > actual:
                verdict = "over"
            else:
                verdict = "under"
            rows.append({
                "sample_id": sample_id,
                "item": item,
                "reported": reported,
                "in_source": actual,
                "verdict": verdict,
            })

    if not rows:
        raise SystemExit("no structured summaries with occurrence_counts were found")

    by_sample = {}
    for r in rows:
        by_sample.setdefault(r["sample_id"], []).append(r)

    print("Counts reported by the structured summaries, against the source logs\n")
    print("  %-10s%9s%9s%9s%9s%11s" % ("sample", "checked", "correct", "over", "under", "not literal"))
    totals = {"checked": 0, "correct": 0, "over": 0, "under": 0, "not literal": 0}
    for sample_id in sorted(by_sample):
        group = by_sample[sample_id]
        counts = {k: sum(1 for r in group if r["verdict"] == k)
                  for k in ("correct", "over", "under", "not literal")}
        totals["checked"] += len(group)
        for k in counts:
            totals[k] += counts[k]
        print("  %-10s%9d%9d%9d%9d%11d" % (sample_id, len(group), counts["correct"],
                                           counts["over"], counts["under"], counts["not literal"]))
    print("\n  %-10s%9d%9d%9d%9d%11d" % ("total", totals["checked"], totals["correct"],
                                         totals["over"], totals["under"], totals["not literal"]))

    print("\nOne example per sample, as Table 8 reports it:")
    for sample_id in sorted(by_sample):
        checkable = [r for r in by_sample[sample_id] if r["verdict"] != "not literal"]
        if not checkable:
            print("  %-10s no count could be checked literally" % sample_id)
            continue
        r = checkable[0]
        print("  %-10s %s - %s / %s" % (sample_id, r["item"], r["in_source"], r["reported"]))

    notliteral = [r for r in rows if r["verdict"] == "not literal"]
    if notliteral:
        print("\nItems that do not occur literally in the source, so no count could be checked:")
        for r in notliteral:
            print("  %-10s %r (reported %s)" % (r["sample_id"], r["item"], r["reported"]))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["sample_id", "item", "reported",
                                               "in_source", "verdict"])
            w.writeheader()
            w.writerows(rows)
        print("\n  wrote %s" % args.csv)


if __name__ == "__main__":
    main()
