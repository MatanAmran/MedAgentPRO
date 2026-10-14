"""
Renders the path map and the feasibility table, with the latest results, into docs/notes/60_PATHS.md.

    python -m tools.path_map            runs tests/paths and tests/feasibility, then writes the map
    python -m tools.path_map --no-run   writes the map from the registry only, without results

No model call and no network: the suites it runs never reach a real model (6.1).
"""

from __future__ import annotations

import argparse
import subprocess  # runs only pytest itself, see run_suites  # nosec B404
import sys
import tempfile
import xml.etree.ElementTree as ET  # parses only the report pytest wrote in this run  # nosec B405
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
OUT = BACKEND.parent / "docs" / "notes" / "60_PATHS.md"
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests" / "paths"), str(BACKEND / "tests" / "feasibility")]

MARK = {"passed": "✅", "failed": "❌", "skipped": "⏭️", "xfailed": "⚠️", "xpassed": "❗", None: "·"}


def run_suites() -> dict[str, str]:
    """{'file.py::name[param]': status} from one pytest run of both suites."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        # bandit B603: this interpreter's pytest, fixed arguments, no shell
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/paths",  # nosec B603
                        "tests/feasibility", f"--junitxml={report}"], cwd=BACKEND)
        out: dict[str, str] = {}
        # bandit B314: the XML pytest wrote in this run, in our temp dir
        for case in ET.parse(report).getroot().iter("testcase"):  # nosec B314
            file = case.get("classname", "").rsplit(".", 1)[-1] + ".py"
            status = "passed"
            for child in case:
                if child.tag in ("failure", "error"):
                    status = "failed"
                elif child.tag == "skipped":
                    status = "xfailed" if "xfail" in (child.get("type", "") + child.get("message", "")).lower() \
                        else "skipped"
            out[f"{file}::{case.get('name')}"] = status
        return out


def status_of(test: str, results: dict[str, str]) -> str | None:
    """A whole parametrized test takes its worst case."""
    if not results:
        return None
    if test in results:
        return results[test]
    found = [s for k, s in results.items() if k.partition("[")[0] == test]
    if not found:
        return "failed"
    for s in ("failed", "xpassed", "xfailed", "skipped"):
        if s in found:
            return s
    return "passed"


def render(results: dict[str, str]) -> str:
    import branches
    from constraints import CONSTRAINTS
    from registry import PATHS

    by_tag: dict[str, list[str]] = defaultdict(list)
    for p in PATHS:
        for t in p.covers:
            by_tag[t].append(p.id)
    when = datetime.now().strftime("%d.%m.%Y %H:%M")
    counts = defaultdict(int)
    for p in PATHS:
        counts[status_of(p.test, results)] += 1
    lines = [
        "# MedAgentPro — מפת המסלולים וההיתכנות",
        "",
        f"**נוצר אוטומטית** ב-`python -m tools.path_map` (מתוך `backend\\`), {when}. **לא לערוך ידנית** — "
        "המקור הוא `backend\\tests\\paths\\registry.py` ו-`backend\\tests\\feasibility\\constraints.py`.",
        "",
        "**איך זה גדל:** `tests\\paths\\branches.py` קורא מהקוד ומהסכמות כל הסתעפות (מצב סיום, שלב, סוג כשל, "
        "`except`, הודעה, נקודת קצה, קוד HTTP, סוג פער, תפקיד). **הסתעפות בלי מסלול מפילה את `pytest`.** "
        "מגבלה שמתגלה (כמו הטמפרטורה) נכנסת כשורה ב-`constraints.py`, ושורה בלי בדיקה מפילה את `pytest`.",
        "",
    ]
    if results:
        lines += [f"**תוצאות:** {counts['passed']} עוברים · {counts['failed']} נכשלים · "
                  f"{counts['xfailed']} פער ידוע · {counts['skipped']} דולגו", ""]
    lines += ["## מסלולים", "", "| | מסלול | מכסה | דרישה / הכרעה | הערה |", "|---|---|---|---|---|"]
    for p in PATHS:
        covers = ", ".join(f"`{t}`" for t in p.covers) or "—"
        lines.append(f"| {MARK[status_of(p.test, results)]} | `{p.id}` | {covers} | {p.refs} | {p.note} |")
    lines += ["", "## הסתעפויות בקוד, ומי מכסה כל אחת", ""]
    for family, names in branches.all_branches().items():
        lines.append(f"**{family}** — " + " · ".join(
            f"`{n}` ({', '.join(by_tag.get(f'{family}:{n}', [])) or '**אין**'})" for n in sorted(names)))
        lines.append("")
    lines += ["## היתכנות", "", "| | # | סוג | מה נבדק | מקור |", "|---|---|---|---|---|"]
    for c in CONSTRAINTS:
        if c.kind == "live":
            mark = "⏳"
        else:
            tests = [k for k in results if k.startswith(f"test_feasibility.py::test_{c.id}_")]
            mark = MARK[status_of(tests[0].partition("[")[0], results)] if tests else MARK[None]
        lines.append(f"| {mark} | {c.id} | {c.kind} | {c.statement} | {c.source} |")
    lines += ["", "✅ עובר · ❌ נכשל · ⚠️ פער ידוע (xfail) · ⏭️ דולג (חסרה הטבלה של LiteLLM בסביבה) · "
              "⏳ רק מול המודל האמיתי, בבדיקת העשן (R9)", ""]
    lines += book_section()
    return "\n".join(lines)


def book_section() -> list[str]:
    """Task 9: each of the book's tests for the chat and its screen, and the tests that answer it, read
    from the tests themselves (tools/book_tests.py). A book test no test answers is marked ❌, and
    tests/paths/test_registry.py fails on it."""
    from tools.book_tests import BOOK_TESTS, coverage
    cov = coverage()
    out = ["## מבחני הספר ← הבדיקות", "",
           "מבחני הצ׳אט והמסך מפרק 6 בספר, ומי עונה על כל אחד. **נקרא מהבדיקות עצמן:** בדיקה שכותבת "
           "\"Test N\" בתיאור, בהערה או בכותרת שלה נחשבת עונה עליו.", "",
           "| | מבחן | דרישה | מה הספר בודק | הבדיקות |", "|---|---|---|---|---|"]
    for t in BOOK_TESTS:
        where = cov.get(t.number, [])
        mark = "⏳" if t.pending else ("✅" if where else "❌")
        said = f"ממתין: {t.pending}" if t.pending else ("<br>".join(f"`{w}`" for w in where) or "**אין**")
        out.append(f"| {mark} | {t.number} | {t.requirement} | {t.title} | {said} |")
    out += ["", "✅ יש בדיקה שעונה עליו · ⏳ ממתין, ומה חסר · ❌ אין בדיקה", ""]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-run", action="store_true")
    args = ap.parse_args()
    results = {} if args.no_run else run_suites()
    OUT.write_text(render(results), encoding="utf-8")
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
