"""Task 4 (chat 20.10, 22:50, 3): real_eval counts, per question, the lines about a part not answered that fell back
to the template and the follow-ups that fell back to the part, against the 20% threshold. No model."""

from query_assistance import contract
from tools import real_eval as ev


def _part(asked, text=None, follow=None):
    return contract.unanswered_part(asked, text, follow)


def test_a_line_that_fell_back_is_counted_and_one_the_check_could_not_word_is_not():
    worded = {"unanswered_parts": [_part("a", "Your question also asked “a”; that part was not covered by "
                                               "the records retrieved for this answer.", "A?"), _part("b")]}
    assert ev.coverage_counts(worded) == {"unanswered": 2, "message_template": 1, "worded": 2, "follow_up_part": 1,
                                          "referred": 0, "replaced": False, "notice": None}
    failed = {**worded, "coverage_notice": contract.COVERAGE_FAILED}
    assert ev.coverage_counts(failed)["worded"] == 0 and ev.coverage_counts(failed)["message_template"] == 0


def test_the_table_states_the_rate_against_the_threshold():
    r = ev.Result("X1", "AP-01", "q", None, {"status": ["answered"]}, None)
    r.coverage = ev.coverage_counts({"unanswered_parts": [_part("a"), _part("b")]})
    table = ev.coverage_table(ev.Run("scripted", "m", 1.0, "now", [r]))
    assert "2 of 2 lines fell back to the template (100%)" in table and "20%" in table
    quiet = ev.Result("X2", "AP-01", "q", None, {"status": ["answered"]}, None)
    quiet.coverage = ev.coverage_counts({})
    said = ev.coverage_table(ev.Run("scripted", "m", 1.0, "now", [quiet]))
    assert said == "The coverage check said nothing in this run."
