"""
A name the user wrote (MATCHES; 81_VALUE_MATCH, chat 20.10, V1-V8 and A-D) [proposal, not in the book].
The model passes the term as written; code finds the record, in three stages (equal ignoring case, starts
with, contains), within the user's entitlement, counting entities by their own identifier. One entity
becomes EQ on its identifier; several ask which (name_ambiguous); none says so (name_not_matched). A
reading still holding MATCHES is never run. The candidates reach the screen only. No model.
"""

import copy
import json
import shutil
import sqlite3

import pytest

from demo_data import seed
from demo_data.scripted import gate_claims
from model_client.client import ScriptedModelGateway
from persistence.audit_repository import AuditRepository, MemoryStorage
from query_assistance import compiler as c
from query_assistance import contract
from query_assistance import pipeline as pl
from query_assistance import prompts as p
from shared.circuit_breaker import CircuitBreaker, MemoryStore

SCHEMA = json.loads((seed.HERE / "schema.json").read_text(encoding="utf-8"))
RELATIONS = json.loads((seed.HERE / "relations.json").read_text(encoding="utf-8"))
CAT = c.Catalog(SCHEMA, RELATIONS)
APPROVER = {"role": "APPROVER", "user_id": "AP-01"}
INTENT_9 = p.load(seed.HERE / "intent_analysis.md")
INTENT_10 = p.load(seed.HERE / "intent_analysis_demo_v10.md")


def by_name(term, *more):
    """Lines of the supplier a name matches; more conditions join it under AND."""
    cond = {"MATCHES": {"col": "suppliers.name", "term": term}}
    return {"SELECT": [{"expr": {"col": "commitments.po_id"}, "as": "po_id"}],
            "FROM": "commitments", "JOIN": [{"rel": "commitment_supplier", "type": "INNER"}],
            "WHERE": {"AND": [cond, *more]} if more else cond, "ORDER_BY": [{"by": "po_id", "dir": "ASC"}]}


def intent(*plans):
    return {"disposition": "retrieve", "readings": [{"label": f"r{i}", "plan": pl_} for i, pl_ in enumerate(plans)]}


def compile_(question, *plans, cat=CAT, user=APPROVER):
    return c.compile_intent(intent(*plans), question, user, cat, c.intent_validator())


def store(tmp_path, db_path, *suppliers):
    """A copy of the demo store with suppliers added: (supplier_id, name, region)."""
    path = tmp_path / "demo.db"
    shutil.copy(db_path, path)
    with sqlite3.connect(path) as conn:
        conn.executemany("INSERT INTO suppliers (supplier_id, name, region, country) VALUES (?, ?, ?, 'XX')",
                         suppliers)
    return path


def resolve(path, ci):
    with sqlite3.connect(path) as conn:
        return c.resolve_names(ci, conn)


SYNTHESIS = {"readings": [{"label": "r0", "sentences": [
    {"text": "{r0.total} lines matched, among them {r0.po_id#1}."}]}]}


def run(path, question, *plans, cat=CAT, user=APPROVER, history=None, seen=None):
    """One exchange on the scripted model whose plan is given. seen collects every prompt sent."""
    def answer(prompt, cfg):
        if seen is not None:
            seen.append(prompt)
        stage = cfg.prompt_identity.split("@")[0]
        if stage.startswith("intent_analysis"):
            return intent(*plans)
        if stage == "question_rewriting":
            return {"rewritten": question}
        if stage == "synthesis":
            return SYNTHESIS
        return gate_claims(prompt, {"verdicts": ["supported"], "evidence": {"r0": ["total", "po_id"]}})
    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pl.Deps(ScriptedModelGateway(answer), "demo", cat, lambda: sqlite3.connect(path), audit, INTENT_9)
    return pl.run(question, history or [], user, deps), audit, deps


# --------------------------------------------------------------------------
# the compiler: where MATCHES may stand (V1), on what (V8), and a span (81)
# --------------------------------------------------------------------------

def test_matches_on_a_column_not_searchable_is_rejected_with_its_path():
    plan = by_name("Apex")
    plan["WHERE"] = {"MATCHES": {"col": "suppliers.region", "term": "Apex"}}
    with pytest.raises(c.CompileError) as e:
        compile_("What did Apex supply?", plan)
    assert e.value.code == "match_not_searchable" and e.value.detail["path"] == "WHERE"


def test_matches_under_or_is_rejected_with_its_path():
    """V1: a name is a condition every record meets, never one alternative of several."""
    plan = by_name("Apex")
    plan["WHERE"] = {"OR": [{"MATCHES": {"col": "suppliers.name", "term": "Apex"}},
                            {"EQ": [{"col": "commitments.week"}, {"value": 3}]}]}
    with pytest.raises(c.CompileError) as e:
        compile_("What did Apex supply in week 3?", plan)
    assert e.value.code == "match_not_top_level" and e.value.detail["path"] == "WHERE/OR/0"


def test_matches_nested_under_and_from_the_root_is_allowed():
    plan = by_name("Apex")
    plan["WHERE"] = {"AND": [{"AND": [{"MATCHES": {"col": "suppliers.name", "term": "Apex"}},
                                      {"EQ": [{"col": "commitments.week"}, {"value": 3}]}]},
                             {"IS_NOT_NULL": {"col": "commitments.delivered_at"}}]}
    ci = compile_("What did Apex supply in week 3?", plan)
    assert [n.path for n in ci.readings[0].names] == ["WHERE/AND/0/AND/0"]


def test_matches_in_having_is_refused_by_the_schema():
    """HAVING takes an aggregate alias, an operator and a number: there is no place for a name."""
    plan = by_name("Apex")
    plan["SELECT"].append({"expr": {"func": "COUNT", "arg": "*"}, "as": "n"})
    plan["GROUP_BY"] = ["po_id"]
    plan["HAVING"] = [{"MATCHES": {"col": "suppliers.name", "term": "Apex"}}]
    with pytest.raises(c.CompileError) as e:
        compile_("What did Apex supply?", plan)
    assert e.value.code == "schema_violation"


def test_the_term_is_a_span_of_the_question():
    with pytest.raises(c.CompileError) as e:
        compile_("What did our biggest supplier send?", by_name("Apex"))
    assert e.value.code == "span_not_in_question"


def test_the_term_is_always_a_parameter():
    ci = compile_("What did Ap'ex supply?", by_name("Ap'ex"))
    for _, values_sql, values_params, count_sql, _ in ci.readings[0].names[0].stages:
        assert "Ap'ex" not in values_sql and "Ap'ex" not in count_sql
        assert any("Ap'ex" in str(v) for v in values_params)


# --------------------------------------------------------------------------
# A: a reading holding MATCHES is never run as written
# --------------------------------------------------------------------------

def test_a_reading_holding_matches_is_refused_by_execute(db_path):
    ci = compile_("What did Apex supply?", by_name("Apex"))
    with pytest.raises(c.UnresolvedName), sqlite3.connect(db_path) as conn:
        c.execute(ci.readings[0], conn)


def test_a_stored_plan_still_holding_matches_is_not_shown_in_full(db_path):
    """compile_all on a plan kept from a name_ambiguous reading: execute refuses it, and so does show_all."""
    reading = c.compile_all("r0", by_name("Apex"), APPROVER, CAT)
    assert reading.names
    with pytest.raises(c.UnresolvedName), sqlite3.connect(db_path) as conn:
        c.execute(reading, conn)
    audit = AuditRepository(MemoryStorage(), CircuitBreaker("system_store", MemoryStore()), b"test-key")
    deps = pl.Deps(ScriptedModelGateway(lambda p_, c_: {}), "demo", CAT, lambda: sqlite3.connect(db_path), audit,
                   INTENT_9)
    with pytest.raises(pl.NotShowable):
        pl.show_all("EX-x", "r0", by_name("Apex"), APPROVER, deps, [])


def test_matches_alone_with_no_identifier_in_the_question_runs(db_path):
    """The resolution runs on name probes as well as A1's: a question with a name and nothing else."""
    out, _, _ = run(db_path, "What did Apex supply?", by_name("Apex"))
    reading = out.response["readings"][0]
    assert reading["result"] == "rows_returned"
    assert out.plans["r0"]["WHERE"] == {"EQ": [{"col": "suppliers.supplier_id"}, {"value": "S-001"}]}


def test_a_reading_left_unresolved_ends_the_exchange_as_not_completed(db_path, monkeypatch):
    """Should resolution ever be skipped, the reading is refused, never run as an exact name."""
    monkeypatch.setattr(pl, "_resolve", lambda compiled, *a: (compiled, [], {}))
    out, _, _ = run(db_path, "What did Apex supply?", by_name("Apex"))
    assert out.response["outcome"] == "request_not_completed"


# --------------------------------------------------------------------------
# the three stages, the escape, the short term (V3)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("term, stage, by", [("APEX POLYMERS", 1, ""), ("apex", 2, " (by prefix)"),
                                             ("Polymers", 3, " (by contains)")])
def test_each_stage_finds_the_one_supplier_and_examined_says_how(db_path, term, stage, by):
    out, _, _ = run(db_path, f"What did {term} supply?", by_name(term))
    reading = out.response["readings"][0]
    assert reading["result"] == "rows_returned"
    assert f"supplier name '{term}' matched Apex Polymers{by}" in reading["checked"]   # C
    want = sqlite3.connect(db_path).execute(
        "SELECT COUNT(*) FROM commitments WHERE supplier_id = 'S-001'").fetchone()[0]
    assert reading["total"] == want


def test_a_short_term_stops_after_the_prefix_stage(db_path):
    """V3: "ym" is inside "Apex Polymers", but a term under three characters is never searched within."""
    ci = compile_("What did ym supply?", by_name("ym"))
    assert [s for s, *_ in ci.readings[0].names[0].stages] == [1, 2]
    assert resolve(db_path, ci)[("r0", "suppliers.name", "ym")].count == 0
    long_ = compile_("What did lym supply?", by_name("lym"))
    assert [s for s, *_ in long_.readings[0].names[0].stages] == [1, 2, 3]


@pytest.mark.parametrize("term", ["Ap%", "Ape_", "%"])
def test_like_wildcards_in_the_term_are_matched_literally(db_path, term):
    ci = compile_(f"What did {term} supply?", by_name(term))
    assert resolve(db_path, ci)[("r0", "suppliers.name", term)].count == 0


def test_a_literal_wildcard_in_a_name_is_found(tmp_path, db_path):
    path = store(tmp_path, db_path, ("S-901", "100% Plastics", "EMEA"))
    ci = compile_("What did 100% supply?", by_name("100%"))
    m = resolve(path, ci)[("r0", "suppliers.name", "100%")]
    assert (m.stage, m.count, m.candidates) == (2, 1, [("S-901", "100% Plastics")])


def test_non_ascii_case_is_not_folded(tmp_path, db_path):
    """T21: SQLite folds ASCII letters only, in NOCASE and in LIKE. 'émile' does not find 'Émile Parts';
    'Émile' does."""
    path = store(tmp_path, db_path, ("S-902", "Émile Parts", "EMEA"))
    lower = resolve(path, compile_("What did émile supply?", by_name("émile")))
    same = resolve(path, compile_("What did Émile supply?", by_name("Émile")))
    assert lower[("r0", "suppliers.name", "émile")].count == 0
    assert same[("r0", "suppliers.name", "Émile")].count == 1


# --------------------------------------------------------------------------
# B: entities, by their own identifier
# --------------------------------------------------------------------------

def test_two_suppliers_with_the_same_name_are_two_candidates(tmp_path, db_path):
    path = store(tmp_path, db_path, ("S-903", "Apex Polymers", "AMER"))
    out, audit, _ = run(path, "What did Apex Polymers supply?", by_name("Apex Polymers"))
    reading = out.response["readings"][0]
    assert reading["result"] == "name_ambiguous" and reading["rows"] == []
    assert reading["notice"] == "'Apex Polymers' matches several supplier names. Ask again with the full name."
    assert reading["candidates"] == [{"term": "Apex Polymers", "column": "supplier name",
                                      "values": ["Apex Polymers (S-001)", "Apex Polymers (S-903)"], "more": 0}]
    assert out.response["outcome"] == "answered"


def test_one_entity_becomes_eq_on_its_identifier_never_on_the_name(db_path):
    ci = compile_("What did Apex supply?", by_name("Apex"))
    m = resolve(db_path, ci)
    single = {k: ("suppliers.supplier_id", v.candidates[0][0]) for k, v in m.items()}
    again = c.with_names(ci.intent, single)
    assert again["readings"][0]["plan"]["WHERE"] == {"EQ": [{"col": "suppliers.supplier_id"}, {"value": "S-001"}]}
    assert ci.intent["readings"][0]["plan"]["WHERE"] == {"MATCHES": {"col": "suppliers.name", "term": "Apex"}}


def test_more_than_max_candidates_says_how_many_more(tmp_path, db_path):
    path = store(tmp_path, db_path, *[(f"S-91{i}", f"Apex Unit {i}", "EMEA") for i in range(7)])
    out, _, _ = run(path, "What did Apex supply?", by_name("Apex"))
    cand = out.response["readings"][0]["candidates"][0]
    assert len(cand["values"]) == CAT.max_candidates == 5 and cand["more"] == 8 - 5


def test_no_match_is_a_finding(db_path):
    out, _, _ = run(db_path, "What did Zephyr supply?", by_name("Zephyr"))
    reading = out.response["readings"][0]
    assert reading["result"] == "name_not_matched" and reading["notice"] == "No supplier name matches 'Zephyr'."
    assert "candidates" not in reading and out.response["outcome"] == "answered"


def test_several_names_in_one_reading(tmp_path, db_path):
    """V2: each resolved on its own; one with no match wins over one with several, and several ambiguous
    names make one request that names each."""
    path = store(tmp_path, db_path, ("S-903", "Apex Polymers", "AMER"), ("S-904", "Halcyon Wire", "AMER"))
    part = {"MATCHES": {"col": "parts.description", "term": "Zephyr"}}
    plan = by_name("Apex Polymers", part)
    plan["JOIN"].append({"rel": "commitment_part", "type": "INNER"})
    none_wins, _, _ = run(path, "Did Apex Polymers supply Zephyr?", plan)
    assert none_wins.response["readings"][0]["result"] == "name_not_matched"
    both = by_name("Apex Polymers", {"MATCHES": {"col": "suppliers.name", "term": "Halcyon"}})
    two, _, _ = run(path, "Did Apex Polymers and Halcyon supply anything?", both)
    reading = two.response["readings"][0]
    assert reading["result"] == "name_ambiguous"
    assert [cd["term"] for cd in reading["candidates"]] == ["Apex Polymers", "Halcyon"]
    assert reading["notice"].count("matches several") == 2 and reading["notice"].endswith("full name.")


# --------------------------------------------------------------------------
# V6: entitlement inside every stage and in the count
# --------------------------------------------------------------------------

def restricted() -> c.Catalog:
    """Planners see the suppliers of their own region only."""
    schema = copy.deepcopy(SCHEMA)
    schema["entities"]["suppliers"]["entitlement"]["PLANNER"] = {"col": "region", "equals": "region"}
    return c.Catalog(schema, RELATIONS)


def test_a_supplier_the_user_may_not_see_is_neither_a_candidate_nor_counted(tmp_path, db_path):
    path = store(tmp_path, db_path, ("S-903", "Apex Polymers", "AMER"))
    cat = restricted()
    planner = {"role": "PLANNER", "user_id": "PL-02", "region": "AMER"}
    ci = compile_("What did Apex supply?", {"SELECT": [{"expr": {"col": "suppliers.supplier_id"}, "as": "id"}],
                                            "FROM": "suppliers",
                                            "WHERE": {"MATCHES": {"col": "suppliers.name", "term": "Apex"}}},
                  cat=cat, user=planner)
    m = resolve(path, ci)[("r0", "suppliers.name", "Apex")]
    assert (m.count, m.candidates) == (1, [("S-903", "Apex Polymers")])   # S-001 is EMEA
    approver = compile_("What did Apex supply?", {"SELECT": [{"expr": {"col": "suppliers.supplier_id"}, "as": "id"}],
                                                  "FROM": "suppliers",
                                                  "WHERE": {"MATCHES": {"col": "suppliers.name", "term": "Apex"}}},
                        cat=cat)
    assert resolve(path, approver)[("r0", "suppliers.name", "Apex")].count == 2


# --------------------------------------------------------------------------
# V4, V5: what the record, the history and every prompt hold
# --------------------------------------------------------------------------

def test_candidates_reach_the_screen_only(tmp_path, db_path):
    path = store(tmp_path, db_path, ("S-903", "Apex Polymers", "AMER"))
    seen: list[str] = []
    out, audit, _ = run(path, "What did Apex supply?", by_name("Apex"), seen=seen)
    assert out.response["readings"][0]["candidates"]
    doc = audit.storage.get_investigation(out.exchange_id)
    assert all("candidates" not in r for r in doc["response"]["readings"])              # V4, K2
    trail = json.dumps(audit.storage.audit_trail(out.exchange_id))
    assert "S-903" not in trail and "(S-001)" not in trail
    retrieval = next(e for e in audit.storage.audit_trail(out.exchange_id) if e["agent"] == "retrieval")
    assert {"matched": [{"label": "r0", "column": "suppliers.name", "term": "Apex", "stage": 2, "count": 2}]} \
        in retrieval["evidence_reviewed"]
    assert out.exchange.presented == []                                                   # NFR 27
    history = json.dumps(vars(out.exchange), default=lambda o: vars(o))
    assert "S-903" not in history and "Apex Polymers" not in history                     # NFR 21
    # the next question's prompts carry none of them either
    seen.clear()
    run(path, "And in March?", by_name("Apex"), history=[out.exchange], seen=seen)
    assert seen and not any("S-903" in s or "Apex Polymers (" in s for s in seen)


def test_a_name_resolved_to_one_entity_reaches_models_and_history_by_its_identifier(db_path):
    """The screen says the name; synthesis, the gate and the history get the identifier: what they are
    given is built from the plan and the catalog, never from a record (NFR 21, render_records)."""
    seen: list[str] = []
    out, _, _ = run(db_path, "What did Apex supply?", by_name("Apex"), seen=seen)
    assert "matched Apex Polymers (by prefix)" in out.response["readings"][0]["checked"]
    assert "supplier name 'Apex' matched S-001 (by prefix)" in out.exchange.readings[0].examined
    model_side = seen[1:]   # synthesis and the gate
    assert model_side and all("Apex Polymers" not in s for s in model_side)
    assert any("matched S-001 (by prefix)" in s for s in model_side)


def test_an_identifier_and_a_name_in_one_reading(db_path):
    """D: s-001 resolves to S-001 (A1) and "Apex" to S-001 (a name), in one compilation; both recorded."""
    plan = by_name("Apex", {"EQ": [{"col": "commitments.supplier_id"}, {"value": "s-001"}]})
    out, audit, _ = run(db_path, "What did Apex, s-001, supply?", plan)
    where = out.plans["r0"]["WHERE"]["AND"]
    assert {"EQ": [{"col": "suppliers.supplier_id"}, {"value": "S-001"}]} in where
    assert {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-001"}]} in where
    retrieval = next(e for e in audit.storage.audit_trail(out.exchange_id) if e["agent"] == "retrieval")
    kinds = {k for item in retrieval["evidence_reviewed"] for k in item}
    assert {"resolved", "matched"} <= kinds


# --------------------------------------------------------------------------
# the prompts: @9 unchanged, @10 for tests
# --------------------------------------------------------------------------

def test_the_demo_intent_prompt_is_unchanged_by_searchable():
    """The demo stays on @9, byte for byte: searchable is marked only for a prompt that teaches MATCHES."""
    plain = json.loads(json.dumps(SCHEMA))
    for ent in plain["entities"].values():
        for spec in ent["columns"].values():
            spec.pop("searchable", None)
    without = c.Catalog(plain, RELATIONS)
    for role in ("APPROVER", "PLANNER"):
        assert (p.render_intent(CAT, role, "Q?", prompt=INTENT_9)
                == p.render_intent(without, role, "Q?", prompt=INTENT_9))
    assert "MATCHES" not in INTENT_9.body and INTENT_9.identity == "intent_analysis_demo@9"


def test_the_v10_prompt_marks_searchable_columns_and_its_example_compiles(db_path):
    assert INTENT_10.identity == "intent_analysis_demo@10"
    text = p.render_intent(CAT, "APPROVER", "Q?", prompt=INTENT_10)
    assert "name TEXT  -- searchable" in text and "description TEXT  -- searchable" in text
    example = next(o for q, o in _examples(INTENT_10) if "MATCHES" in json.dumps(o))
    ci = c.compile_intent(example, "What did Apex deliver in March?", APPROVER, CAT, c.intent_validator())
    assert ci.readings[0].names[0].term == "Apex"


def _examples(pr):
    from tests.query_assistance.test_prompts import examples
    return examples(pr)


def test_the_catalog_refuses_a_searchable_column_without_an_own_identifier():
    """B, open question (00_STATUS): until it is settled, such a catalog does not load."""
    schema = copy.deepcopy(SCHEMA)
    schema["entities"]["forecast"]["columns"]["label"] = {"label": "forecast label", "type": "text",
                                                          "searchable": True}   # forecast holds no id of its own
    with pytest.raises(ValueError, match="searchable"):
        c.Catalog(schema, RELATIONS)


def test_contract_for_record_drops_the_candidates_only():
    response = {"outcome": "answered", "readings": [{"label": "r0", "candidates": [{"term": "x"}], "notice": "n"}]}
    assert contract.for_record(response)["readings"] == [{"label": "r0", "notice": "n"}]
    assert response["readings"][0]["candidates"]   # the response itself is untouched


# --------------------------------------------------------------------------
# NFR 27 per node (chat 20.10, task 4): only the node with_names wrote is exempt
# --------------------------------------------------------------------------

def test_the_node_code_wrote_from_a_name_passes_nfr_27(db_path):
    """S-001 is in no part of the question; the EQ with_names wrote from "Apex" holds it, and passes."""
    ci = compile_("What did Apex supply?", by_name("Apex"))
    single = {k: ("suppliers.supplier_id", v.candidates[0][0]) for k, v in resolve(db_path, ci).items()}
    written: set = set()
    again = c.with_names(ci.intent, single, written)
    assert written == {("r0", "WHERE")}
    out = c.compile_intent(again, "What did Apex supply?", APPROVER, CAT, c.intent_validator(), exempt=written)
    assert out.readings[0].params[0] == "S-001"


def test_an_eq_the_model_wrote_on_the_same_identifier_is_checked_as_any_other(db_path):
    """The model wrote EQ commitments.supplier_id = S-001 beside MATCHES "Apex": the question holds no S-001,
    so NFR 27 refuses it, though code resolves "Apex" to that very S-001 in the same plan. The first
    compilation refuses it already (the model's plan as written); the recompile after with_names, checked
    here directly, refuses it too, since only the node code wrote is exempt."""
    plan = by_name("Apex", {"EQ": [{"col": "commitments.supplier_id"}, {"value": "S-001"}]})
    single = {k: ("suppliers.supplier_id", v.candidates[0][0])
              for k, v in resolve(db_path, compile_("What did Apex supply?", by_name("Apex"))).items()}
    written: set = set()
    again = c.with_names(intent(plan), single, written)
    assert written == {("r0", "WHERE/AND/0")}
    with pytest.raises(c.CompileError) as e:
        c.compile_intent(again, "What did Apex supply?", APPROVER, CAT, c.intent_validator(), exempt=written)
    assert e.value.code == "identifier_not_in_conversation" and e.value.detail["value"] == "S-001"
    out, _, _ = run(db_path, "What did Apex supply?", plan)
    assert out.response["outcome"] == "request_not_completed"   # never answered from the model's S-001
