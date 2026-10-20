"""The round priced per set before it runs (Phase B task 7, chat 20.10). No model, no .env."""

from model_client.budget import BudgetConfig
from tools import dry_run

CFG = BudgetConfig("claude-haiku-5-5", 0.10, 0.50, 2.0, 0.05, 2000, 10)


def test_each_question_is_priced_on_the_prompts_of_the_round():
    demo, offscript = dry_run.price_round(CFG, ["demo", "offscript"])
    assert demo.prompts.startswith("intent_analysis_demo@") and demo.prompts.endswith("frozen")
    assert offscript.prompts.startswith("intent_analysis@") and offscript.prompts.endswith("generic")
    assert len(demo.items) == 14 and len(offscript.items) == 31
    for s in (demo, offscript):
        for p in s.items:
            assert p.calls >= 1 and p.input_tokens > 0
            assert 0 < p.expected <= p.worst <= CFG.max_usd_per_exchange + 1e-12


def test_the_stand_in_model_reaches_what_the_plans_reach():
    """A plan written to be answered is answered: the stand-in's synthesis and gate pass every check, so
    no question is priced short for a rejection of the stand-in's own making."""
    demo, = dry_run.price_round(CFG, ["demo"])
    assert sum(p.status == "answered" for p in demo.items) == 12
    assert all(p.status != "request_not_completed" for p in demo.items)


def test_an_unknown_set_is_refused(capsys):
    assert dry_run.main(argv=["--model", "claude-haiku-5-5", "--sets", "demo,nope"]) == 2
    assert "unknown set nope" in capsys.readouterr().out
