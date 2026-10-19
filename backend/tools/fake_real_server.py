"""
MedAgentPro - the chat server on the real-model path, with a fake provider (task 9, review addition 2).

Everything the demo runs with --real runs here: BudgetedGateway (prices, caps, the spend ledger) over
LiteLLMGateway (the breaker, the request, the response it parses), the cost beside the answer (T3).
Only LiteLLM's completion() is replaced: it answers from the scripted exchanges, as JSON text with token
counts, the way the provider would. No key is read, none is sent, and nothing leaves the machine.

    python -m tools.fake_real_server            # http://127.0.0.1:8000, for the end-to-end tests

Never the demo's spend file and never backend/.env: the ledger lives in a temporary directory and the
configuration below is fixed. The prices are Claude Sonnet 5.5's from LiteLLM's map (2 and 10 USD per
million tokens), so the costs shown are of the right size.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from demo_data import seed
from demo_data.scripted import ScriptedDemoGateway, _key, load_scripts
from model_client.budget import BudgetConfig, BudgetedGateway, SpendLedger
from model_client.client import LiteLLMGateway, ModelConfig
from shared.circuit_breaker import CircuitBreaker, MemoryStore

# The configuration of the fake run. MODEL_MAX_USD_PER_EXCHANGE is set so that every demo question passes
# but the one with the largest records (question 3, 30 rows), which is stopped before synthesis (R7a): the
# end-to-end test shows both the cost line and the cap's notice. With what intent analysis already spent,
# that question needs about 0.151 of worst-case reservation and the next largest (question 5) about 0.145
# (fix round prompts, 19.10: synthesis@11, gate@6 and the count line of D5; before them 0.140 and 0.134).
# With the intent and gate output caps at 3000, as in the demo's .env, they are about 0.171 and 0.165.
# tests/api/test_real_path.py pins both sides, and
# feasibility row L05 that the demo's default cap (0.25) passes every question
CONFIG = {
    "ANTHROPIC_API_KEY": "fake-key-never-sent", "MODEL_ID": "claude-sonnet-5-5",
    "MODEL_PRICE_IN": "2", "MODEL_PRICE_OUT": "10", "MODEL_BUDGET_USD": "5",
    "MODEL_MAX_USD_PER_EXCHANGE": "0.148", "MODEL_MAX_OUTPUT_TOKENS": "2000",
    "MODEL_MAX_EXCHANGES_PER_MINUTE": "30",
}

# the stage each request is for: the structured-output name LiteLLMGateway sends, and none for intent
_BY_FORMAT = {"question_rewriting": "question_rewriting@1", "synthesis": "synthesis@1",
              "synthesis_redraft": "synthesis_redraft@1", "support_scoring": "support_scoring@1"}


# Exchanges for the end-to-end tests only, beside the demo's scripts (chat 20.10: a name the user wrote)
EXTRA_SCRIPTS = Path(__file__).with_name("fake_extra_exchanges.json")


class FakeProvider:
    """LiteLLM's completion(), answering from the scripts. One exchange at a time, as the end-to-end
    tests run: the question is read from the prompt's own markers (the gate's prompt holds none, by
    design, so the gate answers for the exchange under way), and each exchange gets a fresh scripted
    gateway at its first stage."""

    _MARKERS = ("Question as posed: ", "Latest question: ", "Question: ")

    def __init__(self):
        self.scripts = {**load_scripts(), **load_scripts(EXTRA_SCRIPTS)}
        self._lock = threading.Lock()
        self._gateway: ScriptedDemoGateway | None = None
        self._question: str | None = None
        self._last: str | None = None
        self.requests: list[dict] = []

    def _posed(self, prompt: str) -> str | None:
        """The question as posed: the first marker found, its last occurrence (the intent prompt's
        examples hold "Question:" lines too; the question block comes last)."""
        for marker in self._MARKERS:
            lines = [ln[len(marker):].strip() for ln in prompt.splitlines() if ln.startswith(marker)]
            if lines:
                return lines[-1]
        return None

    def __call__(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        fmt = (kwargs.get("response_format") or {}).get("json_schema", {}).get("name")
        identity = _BY_FORMAT.get(fmt, "intent_analysis_demo@4")
        stage = identity.split("@")[0]
        with self._lock:
            if stage != "support_scoring":
                posed = self._posed(prompt)
                entry = self.scripts.get(_key(posed)) if posed else None
                question = entry["question"] if entry else None
                first = stage == "question_rewriting" or (stage.startswith("intent")
                                                          and self._last != "question_rewriting")
                if first or question != self._question:
                    self._question = question
                    self._gateway = ScriptedDemoGateway(question, self.scripts) if question else None
            self._last = stage
            self.requests.append({"model": kwargs["model"], "stage": stage, "question": self._question})
            if self._gateway is None:
                text = "{}"   # not a scripted question: the stage rejects it, as it would a real answer
            else:
                text = self._gateway.invoke(prompt, ModelConfig(model="fake", prompt_identity=identity)).text
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
            usage={"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(text) // 4)},
            model=kwargs["model"])


def build_app(db_path: Path = seed.DEFAULT_OUT, spend_dir: Path | None = None, panel: bool = False, **override):
    """The app as main.create_app("real") builds it, with the fake provider. Returns (app, provider)."""
    import main
    spend_dir = Path(spend_dir or tempfile.mkdtemp(prefix="medagentpro-fake-spend-"))
    cfg = BudgetConfig.from_env({**CONFIG, **override})
    provider = FakeProvider()
    inner = LiteLLMGateway(CircuitBreaker("model_provider", MemoryStore()), completion=provider,
                           api_key=None, provider="anthropic")
    gateway = BudgetedGateway(inner, cfg, SpendLedger(spend_dir / "spend.json"))
    # a chain key of its own, so backend/.env is never read; set only while the app is built
    previous = os.environ.get("MEDAGENTPRO_CHAIN_KEY")
    os.environ["MEDAGENTPRO_CHAIN_KEY"] = secrets.token_hex(32)
    try:
        app = main.create_app("real", db_path, gateway=gateway, panel=panel)
    finally:
        if previous is None:
            del os.environ["MEDAGENTPRO_CHAIN_KEY"]
        else:
            os.environ["MEDAGENTPRO_CHAIN_KEY"] = previous
    return app, provider


def main_(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.fake_real_server")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--panel", action="store_true", help='"How this was answered" under each answer (task 10)')
    args = ap.parse_args(argv)
    import uvicorn
    app, _ = build_app(panel=args.panel)
    print(json.dumps({"fake_real_server": True, "model": CONFIG["MODEL_ID"]}))
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
