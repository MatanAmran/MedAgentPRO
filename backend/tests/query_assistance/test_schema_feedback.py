"""
A schema breach in intent analysis names the rule that broke and where (task 10a, chat 01:15).

Where oneOf or anyOf fails, the feedback and the check point at the alternative that came closest, not
at the WHERE as a whole: the first attempt of question 3 was told only "the value at readings/0/plan/WHERE
breaks the schema rule 'oneOf'". Both carry a path and a keyword, never a value, and never a key the
model made up (NFR 21, P4).
"""

import json

import pytest

from model_client.client import ScriptedModelGateway
from query_assistance import calls as mc
from query_assistance.compiler import intent_validator

V = intent_validator()
LEAF = {"EQ": [{"col": "commitments.week"}, {"value": 1}]}


def intent(where=None, **plan) -> dict:
    p = {"SELECT": [{"expr": {"col": "commitments.supplier_id"}, "as": "supplier_id"}], "FROM": "commitments"}
    if where is not None:
        p["WHERE"] = where
    p.update(plan)
    return {"disposition": "retrieve", "readings": [{"label": "x", "asked": "q", "plan": p}]}


GOOD = intent(LEAF)


def first_attempt(bad: dict) -> mc.Call:
    gw = ScriptedModelGateway([bad, GOOD])
    _, made = mc.ask(gw, "m", "intent_analysis_demo@5", lambda fb: f"prompt {fb}", lambda out: out, validator=V)
    assert [c.check is None for c in made] == [False, True]
    return made[0]


def feedback_sent(bad: dict) -> str:
    gw = ScriptedModelGateway([bad, GOOD])
    mc.ask(gw, "m", "intent_analysis_demo@5", lambda fb: f"prompt {fb}", lambda out: out, validator=V)
    return gw.calls[1][0]


MONTH = {"EQ": [{"func": "MONTH", "arg": {"col": "commitments.delivered_at"}}, {"value": 9}]}
DEEP = {"AND": [{"OR": [{"AND": [LEAF, LEAF]}, LEAF]}, LEAF]}


@pytest.mark.parametrize("where, inner", [
    (MONTH, "readings/0/plan/WHERE/EQ/0"),
    ({"AND": [LEAF, MONTH]}, "readings/0/plan/WHERE/AND/1/EQ/0"),
    (DEEP, "readings/0/plan/WHERE/AND/0/OR/0"),
])
def test_a_breach_inside_where_points_inward(where, inner):
    call = first_attempt(intent(where))
    assert call.check == f"SchemaBreach:oneOf@{inner}"
    assert f"the value at {inner} breaks the schema rule 'oneOf'" in feedback_sent(intent(where))


@pytest.mark.parametrize("bad, check", [
    (intent(LEAF, LIMIT=50), "SchemaBreach:maximum@readings/0/plan/LIMIT"),
    (intent(LEAF, ORDER_BY=[{"by": "supplier_id", "dir": "desc"}]), "SchemaBreach:enum@readings/0/plan/ORDER_BY/0/dir"),
])
def test_a_plain_breach_keeps_its_own_rule_and_place(bad, check):
    assert first_attempt(bad).check == check


def test_neither_the_check_nor_the_feedback_holds_a_value_or_an_invented_key():
    secret_value, secret_key = "S-SECRET-4711", "secret_key_the_model_made_up"
    bad = intent({"EQ": [{"func": "MONTH", "arg": {"col": "commitments.delivered_at"}, secret_key: secret_value},
                         {"value": secret_value}]})
    bad["readings"][0]["plan"][secret_key] = secret_value
    call = first_attempt(bad)
    sent = feedback_sent(bad).split("prompt ", 1)[1]
    for text in (call.check, sent):
        assert secret_value not in text and secret_key not in text and "MONTH" not in text, text
    # the full message stays where it was, in the rejection (the audit entry), as before
    assert call.rejection.startswith("schema: ")


def test_the_feedback_still_names_a_limit_but_not_the_value():
    sent = feedback_sent(intent(LEAF, LIMIT=57)).split("prompt ", 1)[1]
    assert "(30)" in sent and "57" not in sent


def test_the_inner_error_is_what_jsonschema_reports_closest():
    """best_match descends while one alternative is closer than the rest; it is deterministic."""
    e = sorted(V.iter_errors(intent(MONTH)), key=lambda x: list(x.absolute_path))[0]
    assert e.validator == "oneOf" and mc.schema_feedback(e) == mc.schema_feedback(e)
    assert json.dumps(list(mc._innermost(e).absolute_path)) == json.dumps(
        ["readings", 0, "plan", "WHERE", "EQ", 0])


# --------------------------------------------------------------------------
# a breach of what the provider enforced (chat, 18.10, 04:45, 2a): the check names keyword and path too
# --------------------------------------------------------------------------

def _synthesis_schema() -> dict:
    from query_assistance.compiler import HERE
    return json.loads((HERE / "medagentpro_schemas.json").read_text(encoding="utf-8"))["schemas"]["synthesis"]["schema"]


@pytest.mark.parametrize("bad, check", [
    ({"readings": [{"label": "late", "sentences": [{"text": "S-SECRET-4711 had the most."}]}]},
     "format_provider_schema:pattern@readings/0/sentences/0/text"),
    ({"readings": [{"label": "late", "sentences": [{"text": "{late.n#1} rows."}], "secret_key": "S-SECRET-4711"}]},
     "format_provider_schema:additionalProperties@readings/0"),
])
def test_a_provider_schema_breach_names_its_rule_and_place_and_no_value(bad, check):
    """A content failure since 05:10 (1): one feedback attempt, its feedback path and keyword only."""
    pytest.importorskip("litellm")
    gw = ScriptedModelGateway([bad, bad])
    with pytest.raises(mc.StageRejected) as e:
        mc.ask(gw, "m", "synthesis@8", lambda fb: f"prompt {fb}", lambda out: out, output_schema=_synthesis_schema())
    first, second = e.value.calls
    assert first.check == check and "S-SECRET-4711" not in first.check and "secret_key" not in first.check
    assert not first.feedback_attempt and second.feedback_attempt and second.check == check
    sent = gw.calls[1][0]
    where, keyword = check.split(":", 1)[1].split("@")[::-1]
    assert where in sent and f"'{keyword}'" in sent
    assert "S-SECRET-4711" not in sent and "secret_key" not in sent


def test_a_provider_schema_breach_then_a_valid_output_is_accepted():
    pytest.importorskip("litellm")
    bad = {"readings": [{"label": "late", "sentences": [{"text": "The records do not hold who approved."}]}]}
    good = {"readings": [{"label": "late", "sentences": [{"text": "{late.n#1} rows."}]}]}
    gw = ScriptedModelGateway([bad, good])
    result, made = mc.ask(gw, "m", "synthesis@8", lambda fb: "prompt", lambda out: out,
                          output_schema=_synthesis_schema())
    assert result == good and [c.feedback_attempt for c in made] == [False, True]
    assert made[0].check == "format_provider_schema:pattern@readings/0/sentences/0/text" and made[1].check is None


def test_not_json_is_still_a_format_failure_with_no_further_attempt():
    gw = ScriptedModelGateway(["not json", {"readings": []}])
    with pytest.raises(mc.StageFailed) as e:
        mc.ask(gw, "m", "synthesis@8", lambda fb: "prompt", lambda out: out, output_schema=_synthesis_schema())
    assert e.value.kind == "format" and len(gw.calls) == 1
