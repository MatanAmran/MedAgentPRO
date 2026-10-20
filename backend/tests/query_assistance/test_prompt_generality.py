"""
The generic prompts (chat 20.10, task 5; 80_GENERALITY): the domain's words and examples live with the
catalog (demo_data/prompt_text.json, demo_data/examples/), the templates (query_assistance/templates/generic/)
hold none. Filled from the demo catalog, each generic prompt is the frozen version it follows, character
for character, so the model reads the same prompt. The server and the demo still run the frozen versions.
No model.
"""

import re
import sqlite3

import pytest

from demo_data import seed
from demo_data.scripted import ScriptedDemoGateway, load_scripts
from model_client.client import ModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from query_assistance.compiler import Catalog
from shared.circuit_breaker import CircuitBreaker, MemoryStore

CAT = Catalog.load(seed.HERE / "schema.json", seed.HERE / "relations.json")

# generic file -> (the frozen prompt it follows, the identity it carries)
LINEAGE = {
    "intent_analysis.md": (seed.HERE / "intent_analysis_demo_v10.md", "intent_analysis@12"),
    "rewrite.md": ("rewrite.md", "question_rewriting@4"),
    "synthesis.md": ("synthesis.md", "synthesis@12"),
    "synthesis_redraft.md": ("synthesis_redraft.md", "synthesis_redraft@9"),
    "gate.md": ("gate.md", "support_scoring@7"),
}


@pytest.mark.parametrize("generic", sorted(LINEAGE))
def test_filled_from_the_demo_catalog_each_is_the_frozen_prompt_exactly(generic):
    frozen, identity = LINEAGE[generic]
    built = p.for_catalog(generic, seed.HERE)
    before = p.load(frozen)
    assert built.body == before.body
    assert built.identity == identity and built.output_schema == before.output_schema


# words of the demo's domain: a list over text we wrote, never over model text (CLAUDE.md, task 10a)
DOMAIN = re.compile(r"suppl|commitment|purchase order|delay|deliver|late_by|on_hand|receipt|stock|\blate\b"
                    r"|\b[A-Z]{1,2}-\d+", re.I)


@pytest.mark.parametrize("generic", sorted(LINEAGE))
def test_a_generic_template_holds_no_word_of_the_demo_domain(generic):
    body = p.load(p.GENERIC / generic).body
    assert not DOMAIN.findall(body), DOMAIN.findall(body)


def test_a_mark_the_catalog_does_not_fill_is_refused():
    template = p.load(p.GENERIC / "rewrite.md")
    with pytest.raises(ValueError, match="subject_records"):
        p.specialize(template, {}, "")


def test_a_value_is_not_scanned_again():
    """As fill (R6): a catalog's text holding [[examples]] stays as written."""
    template = p.load(p.GENERIC / "rewrite.md")
    built = p.specialize(template, {"subject_records": "[[examples]] records"}, "EX")
    assert "a conversation about [[examples]] records" in built.body


def test_the_server_runs_the_frozen_set():
    deps = pl.Deps(None, "scripted", CAT, lambda: None, None, p.load(seed.HERE / "intent_analysis.md"))
    assert deps.prompts == p.PromptSet.frozen()
    assert [x.identity for x in deps.prompts.__dict__.values()] == [
        "question_rewriting@3", "synthesis@11", "synthesis_redraft@8", "support_scoring@6"]


class Recording(ModelGateway):
    def __init__(self, inner):
        self.inner, self.sent = inner, []

    def invoke(self, prompt, config):
        self.sent.append((config.prompt_identity, str(prompt)))
        return self.inner.invoke(prompt, config)


@pytest.mark.parametrize("question", ["Which parts are below their reorder point?",
                                      "Which suppliers were late in weeks 1 to 13?"])
def test_a_run_on_the_generic_prompts_sends_the_same_prompts(db_path, question):
    """The demo question with a redraft (below reorder) and one with two readings, on the frozen prompts
    (intent @10) and on the generic ones: the same text at every call, only the identities differ."""
    scripts = load_scripts()

    def run(intent, prompt_set):
        gw = Recording(ScriptedDemoGateway(question, scripts))
        audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
        deps = pl.Deps(gw, "scripted", CAT, lambda: sqlite3.connect(db_path), audit, intent, prompt_set=prompt_set)
        out = pl.run(question, [], {"role": "APPROVER", "user_id": "AP-01"}, deps)
        return out, gw.sent

    frozen, sent_frozen = run(p.load(seed.HERE / "intent_analysis_demo_v10.md"), None)
    generic, sent_generic = run(p.for_catalog("intent_analysis.md", seed.HERE), p.PromptSet.generic(seed.HERE))
    assert [t for _, t in sent_generic] == [t for _, t in sent_frozen] and len(sent_frozen) >= 3
    assert {i for i, _ in sent_generic} <= {"intent_analysis@12", "synthesis@12", "synthesis_redraft@9",
                                           "support_scoring@7"}
    assert generic.response == frozen.response
