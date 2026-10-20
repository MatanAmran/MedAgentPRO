"""
The guard on the path map. It fails when the code and the map part ways:

  - a branch exists in the code (branches.py reads it) that no path covers
  - a path names a branch the code no longer has
  - a path names a test that does not exist, or a test of the path suites belongs to no path
"""

from __future__ import annotations

import importlib
import inspect

import branches
from registry import PATHS

SUITES = ("test_paths", "test_branches")


def test_every_branch_in_the_code_has_a_path():
    covered = {t for p in PATHS for t in p.covers}
    missing = sorted(branches.all_tags() - covered)
    assert not missing, ("branches with no path (add a test, then an entry in tests/paths/registry.py):\n  "
                         + "\n  ".join(missing))


def test_no_path_names_a_branch_the_code_no_longer_has():
    stale = sorted({t for p in PATHS for t in p.covers} - branches.all_tags())
    assert not stale, "registry names branches the code does not have:\n  " + "\n  ".join(stale)


def test_path_ids_are_unique():
    ids = [p.id for p in PATHS]
    assert len(ids) == len(set(ids))


def _tests_of(module_name: str) -> set[str]:
    mod = importlib.import_module(module_name)
    return {name for name, fn in vars(mod).items()
            if name.startswith("test_") and inspect.isfunction(fn) and fn.__module__ == module_name}


def test_every_path_names_a_real_test():
    test_paths = importlib.import_module("test_paths")
    a_ids = {x[0] for x in test_paths.A_PATHS}
    for p in PATHS:
        file, _, node = p.test.partition("::")
        name, _, param = node.partition("[")
        module = file.removesuffix(".py")
        assert module in SUITES, p
        assert name in _tests_of(module), f"{p.id}: no test {p.test}"
        if param:
            assert name == "test_path" and param.rstrip("]") in a_ids, f"{p.id}: no case {p.test}"


def test_every_test_of_the_path_suites_is_on_the_map():
    named = {p.test.partition("[")[0] for p in PATHS}
    for module in SUITES:
        for name in _tests_of(module):
            assert f"{module}.py::{name}" in named, f"{module}.py::{name} is not in tests/paths/registry.py"
    test_paths = importlib.import_module("test_paths")
    cases = {p.test for p in PATHS if "[" in p.test}
    for pid in (x[0] for x in test_paths.A_PATHS):
        assert f"test_paths.py::test_path[{pid}]" in cases, f"test_path[{pid}] is not in tests/paths/registry.py"


def test_every_book_test_for_the_chat_has_a_test():
    """Task 9: each of the book's tests for the chat and its screen is answered by a test that names it,
    except the load-time one, which waits for the company's environment (task 9, B4) and which no test
    here may claim."""
    from tools.book_tests import BOOK_TESTS, coverage, missing
    assert missing() == []
    pending = [t.number for t in BOOK_TESTS if t.pending]
    assert pending == [39] and not coverage().get(39)
    assert [t.number for t in BOOK_TESTS if t.queued] == [46] and not coverage().get(46)   # 17:35: queued
