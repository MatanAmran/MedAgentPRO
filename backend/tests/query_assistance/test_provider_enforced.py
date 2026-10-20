"""
Every valid output in the set also passes the schema the provider would enforce (task 10a; chat, 15.10,
01:40). The full schema is checked in code (rule 3); the provider enforces only what LiteLLM's reduction
leaves. A shape the reduction breaks, as it broke BETWEEN and IN as tuples (prefixItems dropped,
"items": false kept: an array that must be empty), fails here before any model call.

Both reduced forms are checked: (b) what LiteLLM 1.105 sends (every $ref inlined, calls.provider_schema),
and (a) $defs kept, each definition filtered the same way, the form a direct call would send.

Both forms are our estimate of what the provider would enforce, not the provider's own word. Step 3 checked
(a) against the provider itself (chat, 15.10, 02:05): it refused the intent schema, first for a $comment on
an object, then because "the compiled grammar is too large" (00_STATUS, task 10a, 15.10). The one rule the
provider showed is checked below on what is actually sent: no $comment on an object.
"""

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from demo_data import seed
from demo_data.scripted import SCRIPT_FILE
from query_assistance import calls as mc
from query_assistance import compiler as c
from query_assistance import prompts as p
from tests.query_assistance.test_prompts import examples

pytest.importorskip("litellm")
from litellm.llms.anthropic.chat.transformation import AnthropicConfig  # noqa: E402

BUNDLE = json.loads((c.HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))["schemas"]
FULL = {"intent_analysis": c.intent_validator().schema, **{
    k: BUNDLE[k]["schema"] for k in ("question_rewriting", "synthesis", "support_scoring")}}
EXCHANGES = json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))["exchanges"]


def with_defs(schema: dict) -> dict:
    """(a): the reduction applied to the top and to each definition, $ref left as they are."""
    s = copy.deepcopy(schema)
    defs = s.pop("$defs", {})
    s = AnthropicConfig.filter_anthropic_output_schema(s)
    if defs:
        s["$defs"] = {k: AnthropicConfig.filter_anthropic_output_schema(v) for k, v in defs.items()}
    return s


ENFORCED = {name: {"(a) with $defs": Draft202012Validator(with_defs(s)),
                   "(b) inlined, as LiteLLM sends": Draft202012Validator(mc.provider_schema(s))}
            for name, s in FULL.items()}


def _listed(v):
    return v if isinstance(v, list) else [v] if v else []


def outputs() -> list[tuple[str, str, dict]]:
    """(stage schema, where it comes from, output) for every valid output the set holds."""
    out = []
    for e in EXCHANGES:
        q = e["question"][:40]
        out.append(("intent_analysis", f"script: {q}", e["intent"]))
        if e.get("rewrite"):
            out.append(("question_rewriting", f"script: {q}", e["rewrite"]))
        for k, d in enumerate(_listed(e.get("synthesis")) + _listed(e.get("redraft"))):
            out.append(("synthesis", f"script {k}: {q}", d))
    for path, name in ((seed.HERE / "intent_analysis.md", "intent_analysis"),
                       ("generic", "intent_analysis"),   # MATCHES (chat 20.10), and asked (task 4)
                       ("rewrite.md", "question_rewriting"),
                       ("synthesis.md", "synthesis"), ("synthesis_redraft.md", "synthesis"),
                       ("gate.md", "support_scoring")):
        prompt = p.for_catalog("intent_analysis.md", seed.HERE) if path == "generic" else p.load(path)
        for i, (_, o) in enumerate(examples(prompt)):
            out.append((name, f"example {i + 1} of {getattr(path, 'name', path)}", o))
    return out


OUTPUTS = outputs()


def test_the_set_holds_outputs_of_every_stage():
    assert {s for s, _, _ in OUTPUTS} == set(FULL) and len(OUTPUTS) >= 30


@pytest.mark.parametrize("stage, where, out", OUTPUTS, ids=[f"{s}:{w}" for s, w, _ in OUTPUTS])
def test_a_valid_output_passes_what_the_provider_would_enforce(stage, where, out):
    assert not list(Draft202012Validator(FULL[stage]).iter_errors(out)), "not valid against the full schema"
    for form, v in ENFORCED[stage].items():
        errs = list(v.iter_errors(out))
        assert not errs, f"{form}: {errs[0].json_path}: {errs[0].message[:120]}"


@pytest.mark.parametrize("stage", list(FULL))
def test_no_stage_schema_holds_a_tuple(stage):
    """prefixItems is not enforced by Anthropic, and LiteLLM's reduction turns a tuple into an empty array."""
    text = json.dumps(FULL[stage])
    assert '"prefixItems":' not in text and '"items": false' not in text


# The three stages that send a schema today: rewriting, synthesis and the gate (intent sends none, T09)
SENT_NOW = ("question_rewriting", "synthesis", "support_scoring")


def objects_with_comment(schema) -> list[str]:
    """Paths of every object type that carries a $comment. Anthropic refused the intent schema with
    "For 'object' type, property '$comment' is not supported" (400, 15.10). Only this rule is checked:
    it is the one the provider showed; a full allowlist of keywords needs a schema adapter of our own on
    the live path, which belongs to the task after the demo (chat, 18.10, 03:25)."""
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            if "$comment" in node and (node.get("type") == "object"
                                       or isinstance(node.get("type"), list) and "object" in node["type"]):
                found.append("/".join(map(str, path)) or "the top level")
            for k, v in node.items():
                walk(v, path + (k,))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + (i,))
    walk(schema, ())
    return found


@pytest.mark.parametrize("stage", SENT_NOW)
def test_no_comment_on_an_object_in_what_is_sent(stage):
    assert not objects_with_comment(mc.provider_schema(FULL[stage]))


def test_the_comment_rule_finds_what_the_provider_refused():
    """The intent schema, which the provider refused for it, is caught by the same check."""
    assert objects_with_comment(mc.provider_schema(FULL["intent_analysis"]))
    assert objects_with_comment(with_defs(FULL["intent_analysis"]))
