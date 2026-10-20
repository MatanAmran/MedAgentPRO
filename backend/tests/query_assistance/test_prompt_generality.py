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
    "intent_analysis.md": (seed.HERE / "intent_analysis_demo_v10.md", "intent_analysis@14"),
    "rewrite.md": ("rewrite.md", "question_rewriting@6"),
    "synthesis.md": ("synthesis.md", "synthesis@15"),
    "synthesis_redraft.md": ("synthesis_redraft.md", "synthesis_redraft@12"),
    "gate.md": ("gate.md", "support_scoring@9"),
}


# intent@14 (chat 20.10, 16:25): what it changes from the frozen v10, and nothing else. E1: "why" and "what caused"
# are referred even where a column records a reason. T17: the label's pattern, said. One example more
INTENT_14 = [
    ("2. If answering requires attributing a cause or judging whether a value is anomalous, set disposition to "
     "refer_to_investigation and return no readings. Retrieving a reason that a record already holds, such as a "
     "recorded delay reason, is not attributing a cause.",
     "2. If the question asks why something happened or what caused it, or answering requires attributing a cause "
     "or judging whether a value is anomalous, set disposition to refer_to_investigation and return no readings. "
     "This holds even where a column records a reason: retrieve a recorded reason, such as a recorded delay reason, "
     "only when the question asks what reason was recorded."),
    ("6. Give every SELECT item an alias. GROUP_BY, HAVING and ORDER_BY name aliases.\n",
     "6. Give every SELECT item an alias. GROUP_BY, HAVING and ORDER_BY name aliases.\n6a. A reading's label starts "
     "with a lowercase letter, followed only by lowercase letters, digits and underscores, at most 40 characters in "
     "all, such as reading_1.\n"),
    # R-2 (chat 20.10, 17:10): the date slots name the date columns, checked as type date
    ("(delivered, received, ordered)", "(delivered_at, received_on, order_date)"),
    ("(due, promised)", "(due_date, promised_date)"),
    ('Question: Why is S-014 late so often?\nOutput: {"disposition": "refer_to_investigation"}\n',
     'Question: Why is S-014 late so often?\nOutput: {"disposition": "refer_to_investigation"}\n\n'
     'Question: What caused PO-87006 to arrive late?\nOutput: {"disposition": "refer_to_investigation"}\n'),
]
CHANGED = {"intent_analysis.md": INTENT_14}


@pytest.mark.parametrize("generic", sorted(LINEAGE))
def test_filled_from_the_demo_catalog_each_is_the_frozen_prompt_exactly(generic):
    """Each generic prompt, filled from the demo catalog, is the frozen one it follows, character for character,
    but for the changes listed in CHANGED, each made exactly once."""
    frozen, identity = LINEAGE[generic]
    built = p.for_catalog(generic, seed.HERE)
    before = p.load(frozen)
    expected = before.body.replace("\r\n", "\n")
    for old, new in CHANGED.get(generic, []):
        assert expected.count(old) == 1, old[:60]
        expected = expected.replace(old, new)
    assert built.body.replace("\r\n", "\n") == expected
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
        "question_rewriting@5", "synthesis@14", "synthesis_redraft@11", "support_scoring@8"]


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
    def as_changed(identity, text):   # the intent call carries @14's listed changes, and only them
        text = text.replace("\r\n", "\n")
        if identity.startswith("intent_analysis"):
            for old, new in INTENT_14:
                text = text.replace(old, new)
        return text
    assert [t.replace("\r\n", "\n") for _, t in sent_generic] == [as_changed(i, t) for i, t in sent_frozen]
    assert len(sent_frozen) >= 3
    assert {i for i, _ in sent_generic} <= {"intent_analysis@14", "synthesis@15", "synthesis_redraft@12",
                                           "support_scoring@9"}
    assert generic.response == frozen.response
