"""
MedAgentPro - the generality check on a BIRD database (task 11b, chat 20.10), with no model.

    python -m tools.bird_eval --plans

Each question of bird/superhero/questions.json has a hand-written plan. The plan goes through the schema, the
compiler (with the catalog's entitlement) and the store, exactly as on the demo's catalog; its rows must equal
the rows of BIRD's own SQL on the same database. The rows compared are every row the user can reach: the
reading shows ROW_LIMIT of them and "Show all records" the rest, up to SHOW_ALL_LIMIT. Nothing in the
pipeline is changed for it: only the catalog (bird/superhero/schema.json, relations.json) is new. A question
the plan language cannot state is a gap with its reason; where a plan for it exists, the compiler must refuse
it with the code recorded.
The database is outside the repository (tools/bird_fetch.py).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from query_assistance.compiler import SHOW_ALL_LIMIT, Catalog, CompileError, compile_intent, execute

HERE = Path(__file__).resolve().parents[1] / "bird" / "superhero"
USER = {"role": "APPROVER", "user_id": "AP-01"}
AVG_TOLERANCE = 0.005   # the compiler rounds an average to two decimals (compiler.AVG_DECIMALS)


def catalog() -> Catalog:
    return Catalog.load(HERE / "schema.json", HERE / "relations.json")


def load() -> dict:
    return json.loads((HERE / "questions.json").read_text(encoding="utf-8"))


def load_examples() -> list[dict]:
    """The BIRD questions behind the catalog's prompt examples (chat 20.10, B1(b)), outside the set."""
    return json.loads((HERE / "example_questions.json").read_text(encoding="utf-8"))["examples"]


@dataclass
class Checked:
    id: int
    ok: bool
    ours: Any = None
    gold: Any = None
    error: str | None = None


def _project(rows: list[dict], columns: list[str]) -> list[tuple]:
    return [tuple(r[c] for c in columns) for r in rows]


def _same(ours: list[tuple], gold: list[tuple], mode: str) -> bool:
    if mode == "value":
        a, b = (ours[0][0] if ours else None), (gold[0][0] if gold else None)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return abs(a - b) <= AVG_TOLERANCE
        return a == b
    if mode == "set":
        return set(ours) == set(gold)
    if mode == "bag":
        return sorted(ours, key=repr) == sorted(gold, key=repr)
    return ours == gold   # ordered


def check(q: dict, cat: Catalog, conn: sqlite3.Connection) -> Checked:
    try:
        # every row the user can reach: the reading shows ROW_LIMIT, and "Show all records" up to SHOW_ALL_LIMIT
        compiled = compile_intent(q["plan"], q["question"], USER, cat, row_limit=SHOW_ALL_LIMIT)
    except CompileError as e:
        return Checked(q["id"], False, error=f"CompileError {e.code}: {e.reason}")
    rows = execute(compiled.readings[0], conn).rows
    ours = _project(rows, q["compare"]["columns"])
    gold = [tuple(r) for r in conn.execute(q["sql"]).fetchall()]
    return Checked(q["id"], _same(ours, gold, q["compare"]["mode"]), ours[:5], gold[:5])


def check_gap(g: dict, cat: Catalog) -> Checked:
    """A gap holds if its plan, where one is recorded, is refused with the code recorded."""
    if "plan" not in g:
        return Checked(g["id"], True)
    try:
        compile_intent(g["plan"], g["question"], USER, cat)
    except CompileError as e:
        return Checked(g["id"], e.code == g["compile"], error=e.code)
    return Checked(g["id"], False, error="compiled: the gap may be closed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.bird_eval")
    ap.add_argument("--plans", action="store_true", required=True, help="each hand-written plan against BIRD's SQL")
    ap.parse_args(argv)
    from tools.bird_fetch import superhero_db
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8")
    cat, data, examples = catalog(), load(), load_examples()
    conn = sqlite3.connect(f"file:{superhero_db().as_posix()}?mode=ro", uri=True)
    try:
        results = [check(q, cat, conn) for q in data["questions"]]
        behind = [check(q, cat, conn) for q in examples]
    finally:
        conn.close()
    print("| BIRD id | question | result | ours | BIRD |\n|---|---|---|---|---|")
    for q, r in zip(data["questions"], results, strict=True):
        print(f"| {r.id} | {q['question'][:60]} | {'✅' if r.ok else '❌'} | {r.error or r.ours} | {r.gold} |")
    gaps = [check_gap(g, cat) for g in data["gaps"]]
    print("\n| BIRD id | gap | holds |\n|---|---|---|")
    for g, r in zip(data["gaps"], gaps, strict=True):
        print(f"| {r.id} | {g['reason'][:90]} | {'✅' if r.ok else '❌'} {r.error or ''} |")
    print("\n| BIRD id | behind a prompt example | result |\n|---|---|---|")
    for q, r in zip(examples, behind, strict=True):
        print(f"| {r.id} | {q['question'][:60]} | {'✅' if r.ok else '❌ ' + (r.error or str(r.ours))} |")
    passed = sum(r.ok for r in results)
    print(f"\n{passed} of {len(results)} plans give BIRD's result; {len(gaps)} gaps recorded; "
          f"{sum(r.ok for r in behind)} of {len(behind)} example plans give BIRD's result")
    return 0 if passed == len(results) and all(r.ok for r in gaps) and all(r.ok for r in behind) else 1


if __name__ == "__main__":
    sys.exit(main())
