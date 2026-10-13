"""
MedAgentPro - Feasibility Study 3.3, identifier analysis

run_study.py records how many identifiers a response cites only where that
response parses as an object. Under the conversational condition no response
does, so the run record reports zero identifiers cited for it. The responses
themselves cite identifiers in prose, and every response text is retained.

This script reads those retained files and counts the identifiers each one
cites, under both conditions, by the same rule. It makes no API calls and
changes nothing: it reads the outputs of a run that already happened and
reports what they contain.

Three figures reported in section 3.3.2 of the book come from here.

Usage:
    python analyze_references.py
    python analyze_references.py --no-ranges          # count spans as endpoints
    python analyze_references.py --csv references.csv
"""

import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict

# An identifier as the evidence list assigns it: E followed by digits.
# The word boundary at the front prevents matching inside a longer token such
# as CODE12, and the boundary at the back prevents E1 matching inside E19.
TOKEN = re.compile(r"\bE\d+\b")

# A span written as E8-E18, E8 - E18, or E8-18, with a hyphen or an en dash.
# The conversational condition writes these; the structured condition returns
# a list and cannot.
RANGE = re.compile(r"\bE(\d+)\s*[-\u2010-\u2015]\s*E?(\d+)\b")

# A span wider than this is read as two separate identifiers rather than as a
# range, since no evidence list in this study holds enough items for one
# response to cite that many consecutive ones in a single phrase.
MAX_SPAN = 40

# results/LOG-01_conversational_1.txt
FILENAME = re.compile(r"^(?P<sample>[A-Z0-9-]+)_(?P<condition>[a-z]+)_(?P<rep>\d+)\.txt$")


def cited_identifiers(text, valid_ids, expand_ranges=True):
    """Identifiers the response text cites.

    Each identifier is counted once per response. A response may name the same
    one twice, in a list of primary evidence and again in prose, while the
    structured condition returns a list without repetition, so counting once
    keeps the two conditions measured by the same rule.

    A conversational response may also write a span, "E8-E18", which names
    eleven identifiers in two tokens. The structured condition returns a list
    and cannot write one. Reading the span as its endpoints alone would
    therefore undercount one condition and not the other. Spans are expanded
    by default, and --no-ranges reports the literal tokens instead.
    """
    cited = set()

    if expand_ranges:
        for match in RANGE.finditer(text):
            low, high = int(match.group(1)), int(match.group(2))
            if low < high and (high - low) <= MAX_SPAN:
                cited.update("E%d" % n for n in range(low, high + 1))

    cited.update(TOKEN.findall(text))

    resolved = sorted(t for t in cited if t in valid_ids)
    unresolved = sorted(t for t in cited if t not in valid_ids)
    return resolved, unresolved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="results")
    parser.add_argument("--evidence", default="evidence.json")
    parser.add_argument("--csv", default=None, help="write per-response rows here")
    parser.add_argument(
        "--no-ranges",
        action="store_true",
        help="count a span such as E8-E18 as its two endpoints only",
    )
    args = parser.parse_args()
    expand = not args.no_ranges

    results_dir = pathlib.Path(args.results)
    if not results_dir.is_dir():
        raise SystemExit("no such directory: %s" % results_dir)

    evidence = json.loads(pathlib.Path(args.evidence).read_text(encoding="utf-8"))
    valid = {s: {item["id"] for item in v["evidence"]} for s, v in evidence.items()}
    supplied = {s: len(ids) for s, ids in valid.items()}

    rows, spans = [], 0
    for path in sorted(results_dir.glob("*.txt")):
        m = FILENAME.match(path.name)
        if not m:
            continue
        sample, condition = m.group("sample"), m.group("condition")
        if sample not in valid:
            print("skipped %s: no evidence list for %s" % (path.name, sample), file=sys.stderr)
            continue
        text = path.read_text(encoding="utf-8")
        found = len(RANGE.findall(text))
        spans += found
        resolved, unresolved = cited_identifiers(text, valid[sample], expand)
        rows.append({
            "sample_id": sample,
            "condition": condition,
            "repetition": int(m.group("rep")),
            "evidence_supplied": supplied[sample],
            "ranges_written": found,
            "cited": len(resolved) + len(unresolved),
            "resolved": len(resolved),
            "unresolved": len(unresolved),
            "unresolved_ids": unresolved,
            "coverage": round(len(resolved) / supplied[sample], 4),
        })

    if not rows:
        raise SystemExit("no response files matched in %s" % results_dir)

    by_condition = defaultdict(list)
    for r in rows:
        by_condition[r["condition"]].append(r)

    mode = "spans expanded" if expand else "spans as endpoints only"
    print("Read %d response files from %s (%s)" % (len(rows), results_dir, mode))
    print("Spans written across all responses: %d\n" % spans)
    print("  %-16s%6s%8s%12s%11s" % ("condition", "runs", "cited", "unresolved", "coverage"))
    total_cited = total_unresolved = 0
    for condition in sorted(by_condition):
        group = by_condition[condition]
        cited = sum(r["cited"] for r in group)
        unres = sum(r["unresolved"] for r in group)
        cov = sum(r["coverage"] for r in group) / len(group)
        total_cited += cited
        total_unresolved += unres
        print("  %-16s%6d%8d%12d%10.1f%%" % (condition, len(group), cited, unres, cov * 100))

    print("\n  identifiers cited across all responses: %d" % total_cited)
    print("  of which absent from the list supplied:  %d" % total_unresolved)

    offenders = [r for r in rows if r["unresolved"]]
    if offenders:
        print("\n  responses citing an identifier that was not supplied:")
        for r in offenders:
            print("    %s %s %d: %s" % (r["sample_id"], r["condition"],
                                        r["repetition"], ", ".join(r["unresolved_ids"])))

    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=[
                "sample_id", "condition", "repetition", "evidence_supplied",
                "ranges_written", "cited", "resolved", "unresolved", "coverage"],
                extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print("\n  wrote %s" % args.csv)


if __name__ == "__main__":
    main()
