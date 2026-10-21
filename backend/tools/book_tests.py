"""
MedAgentPro - which test answers each of the book's tests for the chat and its screen (task 9).

The book's test numbers are read from the tests themselves: a docstring, a comment, a test title or a
path's refs that names "Test 32" (or "Tests 44 and 45", "Test 32, 34") says that test answers it. The
list below is the book's (chapter 6, tests for query assistance and the interface), with the requirement
each one checks. One of them waits for the company's environment and is never claimed here (task 9, B4).
One is queued (chat 20.10, 17:35): it has no test yet, is shown as missing, and does not fail the run.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # the repository


@dataclass(frozen=True)
class BookTest:
    number: int
    requirement: str
    title: str
    pending: str = ""   # why it is not checked here, where it is not
    queued: str = ""    # no test yet, and when one is to be built: shown as missing, not failed


BOOK_TESTS = [
    BookTest(13, "NFR 6", "Retrieval parameters are validated before execution"),
    BookTest(16, "FR 11, FR 12, FR 13, NFR 4", "Answer from retrieved material, with attribution and rating"),
    BookTest(17, "NFR 19, NFR 20", "An answer with no supported assertion is regenerated once and then replaced"),
    BookTest(21, "FR 13, NFR 3, NFR 4", "Traceability of an answered question"),
    BookTest(22, "FR 14, NFR 20", "A question requiring interpretation is routed rather than answered"),
    BookTest(23, "FR 15", "An unreachable source ends the exchange"),
    BookTest(32, "FR 11, NFR 6", "A question carrying more than one reading is not answered by selecting one"),
    BookTest(33, "NFR 21", "Retrieved content does not redirect execution"),
    BookTest(34, "FR 11", "What was examined is presented with an answer"),
    BookTest(35, "FR 11, FR 15", "A retrieval that matches no record is answered as such"),
    BookTest(37, "NFR 6", "A count is computed without a model (the chat's part: how many, over more records "
             "than the rows returned)"),
    BookTest(38, "NFR 22", "User flows complete on each browser engine"),
    BookTest(39, "NFR 23", "Screens presenting stored content load within the configured time",
             pending="in the company's environment, with its records (task 9, B4)"),
    BookTest(40, "NFR 24", "The interface is operable from the keyboard and does not depend on color"),
    BookTest(44, "FR 25", "A follow-up question is answered under the question understood"),
    BookTest(45, "NFR 27", "An identifier from outside the conversation is not retrieved"),
    BookTest(46, "NFR 28", "A question posed shows that it is in progress until its response"),
    BookTest(47, "FR 11, FR 14", "A part of a question not answered is said, and a part that asks for a cause is "
             "referred [proposal, not in the book: task 4, 91_COVERAGE_PLAN; the chat writes it after the build]"),
    BookTest(48, "FR 11, 3.1.6", "A question whose second part depends on the result of its first is answered over "
             "that result, and Examined states it [proposal, not in the book: task 3, 92_TASK3_PLAN; the chat writes "
             "it after the build]"),
]

SOURCES = [("backend/tests", "*.py"), ("frontend/e2e", "*.ts"), ("frontend/app", "*.test.tsx"),
           ("frontend/lib", "*.test.ts")]

_REF = re.compile(r"\bTests?\s+(\d+(?:\s*(?:,|and)\s*\d+)*)")
_PY_TEST = re.compile(r"^\s*def (test_\w+)|^\s*_a\(\"(\w+)\"|^\s*Path\(\"(\w+)\"")
_TS_TEST = re.compile(r"""^\s*(?:test|it|describe)(?:\.each\([^)]*\))?\(\s*[`"'](.+?)[`"']""")


def _numbers(text: str) -> list[int]:
    return [int(n) for m in _REF.finditer(text) for n in re.findall(r"\d+", m.group(1))]


def coverage(root: Path = ROOT) -> dict[int, list[str]]:
    """Book test number -> where a test says it answers it, as 'file: test name'. A reference before any
    test in a file (a module docstring) counts for the file as a whole."""
    found: dict[int, list[str]] = defaultdict(list)
    for folder, pattern in SOURCES:
        base = root / folder
        if not base.exists():
            continue
        for f in sorted(base.rglob(pattern)):
            if "node_modules" in f.parts:
                continue
            rel = f.relative_to(root).as_posix()
            lines = f.read_text(encoding="utf-8").splitlines()
            matcher = _PY_TEST if f.suffix == ".py" else _TS_TEST
            current = "(the file)"
            # a test's own docstring or title comes on or after its first line; a comment before it belongs
            # to it too, so a reference is credited to the next test named within a few lines, else the last
            for i, line in enumerate(lines):
                m = matcher.match(line)
                if m:
                    current = next(g for g in m.groups() if g)
                nums = _numbers(line)
                if not nums:
                    continue
                owner = current
                if not m:
                    for ahead in lines[i + 1:i + 10]:
                        n = matcher.match(ahead)
                        if n and not line.strip().startswith(('"""', "'''")) and (
                                line.strip().startswith(("#", "//"))):
                            owner = next(g for g in n.groups() if g)
                            break
                for n in nums:
                    entry = f"{rel}: {owner}"
                    if entry not in found[n]:
                        found[n].append(entry)
    return found


def missing(root: Path = ROOT) -> list[int]:
    """Book tests no test answers, leaving out the one that waits for the company's environment and the
    queued ones."""
    cov = coverage(root)
    return [t.number for t in BOOK_TESTS if not t.pending and not t.queued and not cov.get(t.number)]
