"""
MedAgentPro - the scripted model the server runs on by default (task 6, S5).

For each question there is a script in scripted_exchanges.json, keyed by the question as posed. The
script replaces the model only: every output it gives goes through the schema, the compiler, the
references, the gate and the contract like a real one. The gate's claims are built from the numbered
sentences in the gate prompt, so they quote the answer as filled, as a real gate does.

A question with no script gets no plausible answer to another question: the call fails as a client
error before anything is sent, and the exchange ends as request_not_completed.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from pathlib import Path

from model_client.client import ModelCallError, ModelConfig, ModelGateway, ModelResponse, ScriptedModelGateway

HERE = Path(__file__).parent
SCRIPT_FILE = HERE / "scripted_exchanges.json"

# prompt name -> key in a script entry
_STAGE = {"question_rewriting": "rewrite", "intent_analysis": "intent", "intent_analysis_demo": "intent",
          "synthesis": "synthesis", "synthesis_redraft": "redraft", "support_scoring": "gate",
          "question_classification": "classify", "coverage_check": "coverage"}


def _key(question: str) -> str:
    return " ".join(question.casefold().split())


def load_scripts(path: Path = SCRIPT_FILE) -> dict[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {_key(e["question"]): e for e in data["exchanges"]}


def gate_claims(prompt: str, gate: dict) -> dict:
    """One claim per numbered sentence, copied from the gate prompt. verdicts are given per sentence in
    order, the last one repeating; evidence names the aliases cited for each reading."""
    body = prompt.split("Answer:\n")[-1].split("\nRecords:")[0]
    verdicts = gate.get("verdicts") or ["supported"]
    claims = []
    for i, (n, label, text) in enumerate(re.findall(r"^(\d+)\. \[(\w+)\] (.*)$", body, re.M)):
        verdict = verdicts[min(i, len(verdicts) - 1)]
        cited = [] if verdict == "uncovered" else [f"{label}.{a}" for a in gate.get("evidence", {}).get(label, [])]
        claims.append({"sentence": int(n), "text": text, "verdict": verdict, "evidence": cited})
    return {"claims": claims}


def coverage_output(prompt: str, given: dict | None) -> dict:
    """Task 4, B (91_COVERAGE_PLAN): built from the parts and the numbered sentences in the prompt, as the gate's
    claims are. By default every part is answered by every sentence, a part marked (declared) is declared, and
    every sentence states values (no_relation). An entry may give, by the part's words, "not_answered" with the
    message and the follow-up, and "kinds", one per sentence in order."""
    given = given or {}
    parts_text = prompt.split("\nParts:\n")[-1].split("\nTables:")[0]
    answer = prompt.split("\nAnswer:\n")[-1].split("\nOutput:")[0]
    numbers = [int(n) for n in re.findall(r"^(\d+)\. \[\w+\] ", answer, re.M)]
    kinds = given.get("kinds") or []
    parts = []
    for asked in re.findall(r"^\d+\. (.*)$", parts_text, re.M):
        declared = asked.endswith(" (declared)")
        asked = asked.removesuffix(" (declared)")
        missing = (given.get("not_answered") or {}).get(asked)
        if declared:
            parts.append({"asked": asked, "status": "declared", "sentences": []})
        elif missing is not None:
            parts.append({"asked": asked, "status": "not_answered", "sentences": [], **missing})
        else:
            parts.append({"asked": asked, "status": "answered", "sentences": numbers})
    return {"parts": parts, "sentences": [{"n": n, "kind": kinds[i] if i < len(kinds) else "no_relation"}
                                          for i, n in enumerate(numbers)]}


class ScriptedDemoGateway(ModelGateway):
    """Bound to one question for one exchange. Each stage takes the next output its script holds: one
    output, or a list where the stage runs more than once (the gate before and after a redraft)."""

    def __init__(self, question: str, scripts: dict[str, dict], delay: float = 0.0):
        self.question = question
        self.delay = delay              # seconds before each output: lets end-to-end tests act mid-stream
        self.script = scripts.get(_key(question))
        self._served: dict[str, int] = {}   # how many outputs each stage has taken
        self._inner = ScriptedModelGateway(self._answer)

    @property
    def calls(self):
        return self._inner.calls

    def _answer(self, prompt: str, config: ModelConfig):
        name = config.prompt_identity.split("@")[0]
        stage = _STAGE.get(name)
        out = self.script.get(stage) if self.script and stage else None
        if isinstance(out, list):   # a stage called more than once takes its outputs in order, the last repeating
            n = self._served.get(stage, 0)
            self._served[stage] = n + 1
            out = out[min(n, len(out) - 1)] if out else None
        if out is None and self.script and stage == "rewrite":
            # a scripted question stands on its own unless its entry says otherwise
            out = {"question": self.question, "carried_from_history": False}
        if out is None and self.script and stage == "classify":
            # task 4, C: a scripted question asks for recorded values, as one part, unless its entry says otherwise
            out = {"parts": [{"asked": self.question, "asks": "recorded"}]}
        if self.script and stage == "coverage":
            return coverage_output(prompt, out)
        if out is None:
            raise ModelCallError("client_error", f"no scripted output for this question at {name}",
                                 reached_provider=False)
        return gate_claims(prompt, out) if stage == "gate" else out

    def invoke(self, prompt: str, config: ModelConfig) -> ModelResponse:
        if self.delay:
            time.sleep(self.delay)
        return replace(self._inner.invoke(prompt, config), cost_usd=0.0)   # the scripted model costs nothing
