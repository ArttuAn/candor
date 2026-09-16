"""Remediation planning."""

from candor import plan, profile, quick_wins
from candor.models import Effort
from helpers import NOW


def test_plan_is_ranked_by_value(messy_csv):
    result = plan(profile(messy_csv, now=NOW))
    values = [step.value for step in result.steps]
    assert values == sorted(values, reverse=True)
    assert [s.rank for s in result.steps] == list(range(1, len(result.steps) + 1))


def test_every_step_has_a_fix(messy_csv):
    for step in plan(profile(messy_csv, now=NOW)).steps:
        assert step.fix.strip()


def test_projected_score_beats_the_current_one(messy_csv):
    result = plan(profile(messy_csv, now=NOW))
    assert result.projected_score > result.current_score


def test_questions_annotate_what_each_fix_unblocks(messy_csv):
    result = plan(profile(messy_csv, now=NOW),
                  ["What was revenue by region?", "How many unique customers?"])
    assert any(step.unlocks for step in result.steps)


def test_a_fix_that_unblocks_a_question_outranks_one_that_does_not(messy_csv):
    prof = profile(messy_csv, now=NOW)
    without = {s.title: s.value for s in plan(prof).steps}
    with_q = {s.title: s.value for s in plan(prof, ["What was revenue by region?"]).steps}
    moved = [t for t in with_q if with_q[t] > without[t]]
    assert moved, "supplying questions changed no priorities"


def test_quick_wins_are_low_effort(messy_csv):
    for step in quick_wins(plan(profile(messy_csv, now=NOW))):
        assert step.effort is Effort.LOW


def test_clean_data_needs_no_plan(clean_csv):
    assert plan(profile(clean_csv, now=NOW)).steps == []
