"""
Every branch the code holds, read from the code and the schemas themselves.

The path registry (registry.py) must cover each one. When the system grows a new outcome, stage,
failure kind, exception handler, notice, endpoint, status code, gap kind or role, it appears here
without anyone listing it, and test_registry.py fails until a path covers it.

A tag is "family:name". Families:
  outcome      exchange_response.outcome enum
  disposition  intent_analysis.disposition enum
  verdict      support_scoring claim verdict enum
  gap          intent_analysis missing.kind enum
  stage        pipeline.STAGE_NAMES
  handler      every except clause in pipeline.run, by enclosing function and exception name
  failure      every kind a model call can fail with: classify(), ModelResponse.problem(), calls.ask
  budget       every BudgetExceeded scope raised anywhere
  notice       every fixed notice in contract.py
  endpoint     every route of the API
  http         every status code the API can answer with
  role         every role a demo user holds
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path

from api import routes as api_routes
from api import state as api_state
from model_client import budget as budget_mod
from model_client import client as client_mod
from query_assistance import calls as calls_mod
from query_assistance import contract
from query_assistance import pipeline as pl
from query_assistance.compiler import HERE

BUNDLE = json.loads((HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))["schemas"]


def _tree(obj) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(obj)))


def _module_tree(mod) -> ast.AST:
    return ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def _first_str_arg(node: ast.Call) -> str | None:
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    return None


def outcomes() -> set[str]:
    return set(BUNDLE["exchange_response"]["schema"]["properties"]["outcome"]["enum"])


def dispositions() -> set[str]:
    return set(BUNDLE["intent_analysis"]["schema"]["properties"]["disposition"]["enum"])


def verdicts() -> set[str]:
    found: set[str] = set()

    def walk(n):
        if isinstance(n, dict):
            v = n.get("properties", {}).get("verdict")
            if isinstance(v, dict) and "enum" in v:
                found.update(v["enum"])
            for x in n.values():
                walk(x)
        elif isinstance(n, list):
            for x in n:
                walk(x)
    walk(BUNDLE["support_scoring"]["schema"])
    return found


def gap_kinds() -> set[str]:
    return set(BUNDLE["intent_analysis"]["schema"]["$defs"]["missing"]["properties"]["kind"]["enum"])


def stages() -> set[str]:
    return set(pl.STAGE_NAMES.values())


def handlers() -> set[str]:
    """except clauses in pipeline.run, named by the function that holds them (run, or a nested one)."""
    out: set[str] = set()

    def visit(fn: ast.AST, owner: str):
        for node in ast.walk(fn):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn:
                continue
        for node in ast.iter_child_nodes(fn):
            _visit_node(node, owner)

    def _visit_node(node: ast.AST, owner: str):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            name = getattr(node, "name", "lambda")
            for child in ast.iter_child_nodes(node):
                _visit_node(child, name)
            return
        if isinstance(node, ast.ExceptHandler):
            types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            for t in types:
                n = t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else "bare"
                out.add(f"{owner}.{n}")
        for child in ast.iter_child_nodes(node):
            _visit_node(child, owner)

    tree = _tree(pl.run).body[0]
    for child in ast.iter_child_nodes(tree):
        _visit_node(child, "run")
    return out


def failures() -> set[str]:
    """ModelCallError kinds from classify() and from every place one is raised in the model client,
    the problems a response can carry, and the stage failures calls.ask raises itself."""
    out: set[str] = set()
    for tree in (_module_tree(client_mod), _module_tree(calls_mod)):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in ("ModelCallError", "StageFailed"):
                kind = _first_str_arg(node)
                if kind:
                    out.add(kind)
    for node in ast.walk(_tree(client_mod.ModelResponse.problem)):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value:
            out.add(node.value.value)
    return out


def budget_scopes() -> set[str]:
    out: set[str] = set()
    for mod in (budget_mod, client_mod):
        for node in ast.walk(_module_tree(mod)):
            if isinstance(node, ast.Call) and _call_name(node) == "BudgetExceeded":
                for kw in node.keywords:
                    if kw.arg == "scope" and isinstance(kw.value, ast.Constant):
                        out.add(kw.value.value)
                if not any(kw.arg == "scope" for kw in node.keywords) and len(node.args) < 2:
                    out.add("total")   # the default scope
    return out


def notices() -> set[str]:
    return {name for name, v in vars(contract).items()
            if name.isupper() and isinstance(v, str) and v.endswith(".") and " " in v}


def endpoints() -> set[str]:
    r = api_routes.router(api_state.AppState(None, "scripted", lambda q: None))
    return {f"{m} {route.path}" for route in r.routes for m in sorted(route.methods)}


def http_codes() -> set[str]:
    """Codes the routes raise or declare, the success codes, and 422 from request validation."""
    out = {"200", "422"}
    for node in ast.walk(_module_tree(api_routes)):
        if isinstance(node, ast.Call) and _call_name(node) == "HTTPException" and node.args:
            a = node.args[0]
            if isinstance(a, ast.Constant):
                out.add(str(a.value))
        if isinstance(node, ast.keyword) and node.arg == "status_code" and isinstance(node.value, ast.Constant):
            out.add(str(node.value.value))
    return out


def roles() -> set[str]:
    return set(api_state.DEMO_USERS.values())


FAMILIES = {
    "outcome": outcomes, "disposition": dispositions, "verdict": verdicts, "gap": gap_kinds,
    "stage": stages, "handler": handlers, "failure": failures, "budget": budget_scopes,
    "notice": notices, "endpoint": endpoints, "http": http_codes, "role": roles,
}


def all_branches() -> dict[str, set[str]]:
    return {family: fn() for family, fn in FAMILIES.items()}


def all_tags() -> set[str]:
    return {f"{family}:{name}" for family, names in all_branches().items() for name in names}
