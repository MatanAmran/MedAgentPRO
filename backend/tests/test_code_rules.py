"""
The code rules (Evyatar, 21.10 21:25; CLAUDE.md, "Code rules"), as checks on backend/ outside tests/, read from the
AST, so a comment or a string never counts:

  1. no raise of a built-in exception (ValueError, RuntimeError, ...): a project failure is a ProjectError
     (shared/errors.py), or a project class with a code, so the log says what failed, where and why;
  2. no broad except (bare, Exception, BaseException) that neither logs nor raises: a fault is never swallowed.

What the code held when the rules were made is the baseline (tests/fixtures/code_rules_baseline.json), one entry per
file, function and kind, and the same list opens docs/notes/96R_RETROFIT_LOG.md. A new case fails here. A case fixed
must leave the baseline in the same change, so the list never claims more than is left (the kind of check L2 asks for:
every raise and every handler, not a case). UPDATE_CODE_RULES_BASELINE=1 rewrites the baseline, for a reviewed diff.
"""

from __future__ import annotations

import ast
import builtins
import json
import os
import sys
from collections import Counter
from pathlib import Path

import pytest

from shared.errors import ProjectError

BACKEND = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).parent / "fixtures" / "code_rules_baseline.json"
SKIP_DIRS = {"tests", "venv", "__pycache__", "node_modules"}
BUILTIN_ERRORS = {n for n in dir(builtins) if isinstance(getattr(builtins, n), type)
                  and issubclass(getattr(builtins, n), BaseException)}
# A command-line exit is how a tool ends, not a failure of the system
ALLOWED_BUILTINS = {"SystemExit", "KeyboardInterrupt"}
# The base class checks its own arguments: a fault of the code that raises it, found by that code's tests
EXEMPT = {("shared/errors.py", "ProjectError.__init__", "builtin_raise:ValueError")}
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _files() -> list[Path]:
    return sorted(p for p in BACKEND.rglob("*.py")
                  if not (set(p.relative_to(BACKEND).parts) & SKIP_DIRS)
                  and not any(part.startswith(".") for part in p.relative_to(BACKEND).parts))


def _name(node: ast.expr | None) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _logs_or_raises(body: list[ast.stmt]) -> bool:
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Raise):
                return True
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in LOG_METHODS:
                return True
    return False


def _broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(_name(t) in ("Exception", "BaseException") for t in names)


def _visit(node: ast.AST, rel: str, scope: str, out: Counter):
    """Walks one file's tree, naming each case by the function (or class) that holds it."""
    for child in ast.iter_child_nodes(node):
        inner = scope
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = f"{scope}.{child.name}" if scope else child.name
        if isinstance(child, ast.Raise) and child.exc is not None:
            name = _name(child.exc)
            if name in BUILTIN_ERRORS and name not in ALLOWED_BUILTINS:
                key = (rel, scope or "<module>", f"builtin_raise:{name}")
                if key not in EXEMPT:
                    out[key] += 1
        if isinstance(child, ast.ExceptHandler) and _broad(child) and not _logs_or_raises(child.body):
            out[(rel, scope or "<module>", "silent_broad_except")] += 1
        _visit(child, rel, inner, out)


def found() -> Counter:
    """Every violation, as (file, enclosing function, kind), counted."""
    out: Counter = Counter()
    for path in _files():
        _visit(ast.parse(path.read_text(encoding="utf-8")), path.relative_to(BACKEND).as_posix(), "", out)
    return out


def _load() -> Counter:
    return Counter({(e["file"], e["function"], e["kind"]): e["count"]
                    for e in json.loads(BASELINE.read_text(encoding="utf-8"))["entries"]})


def _write(c: Counter):
    entries = [{"file": f, "function": fn, "kind": k, "count": n} for (f, fn, k), n in sorted(c.items())]
    BASELINE.write_text(json.dumps({"$comment": "tests/test_code_rules.py: what the code held when the code rules "
                                                "were made (21.10). Shrinks only.", "entries": entries},
                                   indent=1) + "\n", encoding="utf-8")


def test_no_new_raise_of_a_builtin_exception_and_no_new_silent_broad_except():
    now = found()
    if os.environ.get("UPDATE_CODE_RULES_BASELINE"):
        _write(now)
    base = _load()
    new = {k: n - base.get(k, 0) for k, n in now.items() if n > base.get(k, 0)}
    assert not new, ("new code breaks the code rules (CLAUDE.md): raise a ProjectError with a code, and log or "
                     f"re-raise in a broad except: {sorted(new)}")


def test_a_fixed_case_leaves_the_baseline():
    now, base = found(), _load()
    gone = {k: n - now.get(k, 0) for k, n in base.items() if n > now.get(k, 0)}
    assert not gone, f"fixed, so remove from {BASELINE.name} and from 96R_RETROFIT_LOG.md: {sorted(gone)}"


def test_the_check_finds_what_it_is_for(tmp_path, monkeypatch):
    """The check itself, on a file written here: a ValueError raise and a silent broad except are found; a
    ProjectError, a logged except and a SystemExit are not."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text(
        "import logging\nlog = logging.getLogger('x')\n"
        "def bad():\n    raise ValueError('x')\n"
        "def quiet():\n    try:\n        pass\n    except Exception:\n        pass\n"
        "def good():\n    try:\n        pass\n    except Exception:\n        log.warning('x')\n"
        "    raise ProjectError('a.b', 'c')\n"
        "def cli():\n    raise SystemExit(1)\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "BACKEND", tmp_path)
    assert found() == Counter({("pkg/m.py", "bad", "builtin_raise:ValueError"): 1,
                               ("pkg/m.py", "quiet", "silent_broad_except"): 1})


# --------------------------------------------------------------------------
# the base class every new project exception takes (shared/errors.py)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("code", ["intent.rejected.unknown_name", "catalog.drift.column_missing", "a.b"])
def test_a_project_error_says_its_code_stage_and_identifiers_in_one_line(code):
    e = ProjectError(code, "intent", label="late", attempt=2, exchange="EX-1")
    assert e.code == code and e.stage == "intent" and e.context == {"label": "late", "attempt": 2, "exchange": "EX-1"}
    assert str(e) == e.log_line() == f"error code={code} stage=intent attempt=2 exchange=EX-1 label=late"


@pytest.mark.parametrize("code", ["", "intent", "Intent.Rejected", "intent..x", "intent.rejected-name", "1.a"])
def test_a_code_that_is_not_dotted_lowercase_words_is_refused_where_it_is_written(code):
    with pytest.raises(ValueError):
        ProjectError(code, "intent")


@pytest.mark.parametrize("value", [{"row": 1}, ["S-001"], object()])
def test_context_holds_identifiers_only_never_a_structure_that_could_carry_record_text(value):
    """NFR 21: the log may be read by whoever runs the system, so a row or a list of values never reaches it."""
    with pytest.raises(ValueError):
        ProjectError("retrieval.failed.query", "retrieval", rows=value)
