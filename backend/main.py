"""
MedAgentPro - the chat server (task 6).

Run from backend/:
  python -m main                 the scripted model (default): no key, no cost
  python -m main --reload        the same, reloading on code changes
  python -m main --real          the real model behind the spending cap (task 5). Needs backend/.env
                                 and MEDAGENTPRO_CHAIN_KEY. Never with --reload (R8)

One process, no workers (T2: the spending cap holds only with one counter), listening on 127.0.0.1
only (S2: demo users, no authentication). Deps is built once and shared by every request (N12).
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
from api.state import AppState
from demo_data import seed
from demo_data.scripted import ScriptedDemoGateway, load_scripts
from model_client.budget import ENV_FILE, BudgetConfigError, BudgetedGateway, real_gateway
from model_client.client import ModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import pipeline, prompts
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, MemoryStore
from shared.circuit_breaker import log as operational_log

HOST = "127.0.0.1"
FRONTEND_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
SCRIPTED_MODEL = "scripted-demo"


def _chain_key(mode: str) -> bytes:
    """S4: from the environment or backend/.env on the real model, where it is required. On the scripted
    model, a random key per process, since the audit log is in memory and ends with it."""
    key = os.environ.get("MEDAGENTPRO_CHAIN_KEY")
    if not key and ENV_FILE.exists():
        from dotenv import dotenv_values
        key = dotenv_values(ENV_FILE).get("MEDAGENTPRO_CHAIN_KEY")
    if key:
        return key.encode()
    if mode == "real":
        raise BudgetConfigError("MEDAGENTPRO_CHAIN_KEY is required on the real model (S4)")
    operational_log.warning("audit chain key: random for this process (scripted model, audit log in memory)")
    return secrets.token_bytes(32)


def build_deps(mode: str = "scripted", db_path: Path = seed.DEFAULT_OUT, scripted_delay: float = 0.0,
               gateway: BudgetedGateway | None = None) -> tuple[pipeline.Deps, Callable[[str], ModelGateway]]:
    """The pipeline's dependencies, built once (N12), and the gateway for each question: bound to it on
    the scripted model, the one budgeted gateway on the real one. The server and tools/real_eval.py
    build them here, the same way. gateway: see create_app."""
    if not Path(db_path).exists():
        raise SystemExit(f"{db_path} is missing. Build it first: python -m demo_data.seed")
    catalog = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")
    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), _chain_key(mode))  # S6
    if mode == "real":
        gateway = gateway or real_gateway()
        model = gateway.config.model_id
        gateway_for = lambda question: gateway            # noqa: E731  one gateway, one budget
    else:
        scripts = load_scripts()
        gateway, model = None, SCRIPTED_MODEL
        gateway_for = lambda question: ScriptedDemoGateway(question, scripts, scripted_delay)   # noqa: E731
    deps = pipeline.Deps(gateway, model, catalog, pipeline.connect_readonly(db_path), audit,
                         prompts.load(seed.HERE / "intent_analysis.md"))
    return deps, gateway_for


def create_app(mode: str = "scripted", db_path: Path = seed.DEFAULT_OUT, scripted_delay: float = 0.0,
               gateway: BudgetedGateway | None = None, panel: bool = False) -> FastAPI:
    """gateway: on the real model, the budgeted gateway to use instead of real_gateway(). Only the fake
    provider of the end-to-end tests passes one (tools/fake_real_server.py, task 9). panel: the "How this
    was answered" trace beside each answer (task 10), off unless asked for, as in the book."""
    deps, gateway_for = build_deps(mode, db_path, scripted_delay, gateway)
    app = FastAPI(title="MedAgentPro chat (demo)")
    # PATCH: renaming, pinning and archiving a conversation (UI refresh, phase B)
    app.add_middleware(CORSMiddleware, allow_origins=FRONTEND_ORIGINS, allow_methods=["GET", "POST", "PATCH"],
                       allow_headers=["Content-Type", "X-Demo-User"])
    app.include_router(router(AppState(deps, mode, gateway_for, panel=panel)))
    return app


def log_to_stdout() -> None:
    """The operational channel (NFR 11) on stdout, INFO and up, so the per-call line (R9) and the
    exchange cost reach it. Without a handler, Python prints WARNING and up only, on stderr. Once."""
    if any(getattr(h, "_medagentpro", False) for h in operational_log.handlers):
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    h._medagentpro = True   # type: ignore[attr-defined]
    operational_log.addHandler(h)
    operational_log.setLevel(logging.INFO)


def scripted_app() -> FastAPI:
    """The factory --reload imports. Scripted only: the real model never runs under reload (R8)."""
    log_to_stdout()   # the reloader's worker is a new process
    return create_app("scripted")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m main")
    ap.add_argument("--real", action="store_true", help="the real model behind the spending cap")
    ap.add_argument("--reload", action="store_true", help="reload on code changes (scripted model only)")
    ap.add_argument("--panel", action="store_true",
                    help='show "How this was answered" under each answer (task 10, a demo extension)')
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--scripted-delay", type=float, default=0.0, metavar="SECONDS",
                    help="wait before each scripted model output, so end-to-end tests can act mid-stream")
    args = ap.parse_args(argv)
    if args.real and args.scripted_delay:
        print("--scripted-delay applies to the scripted model only")
        return 2
    if args.real and args.reload:
        print("--reload is refused with --real: each reload would start another counter (R8, T2)")
        return 2
    if args.panel and args.reload:
        print("--panel is not passed through --reload; run without --reload to see the panel")
        return 2
    import uvicorn
    log_to_stdout()
    if args.reload:
        uvicorn.run("main:scripted_app", factory=True, host=HOST, port=args.port, reload=True)
        return 0
    try:
        app = create_app("real" if args.real else "scripted", scripted_delay=args.scripted_delay, panel=args.panel)
    except BudgetConfigError as e:
        print(e)
        return 1
    uvicorn.run(app, host=HOST, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
