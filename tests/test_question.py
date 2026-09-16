"""Question parsing."""

from datetime import datetime

import pytest

from candor import parse_question

NOW = datetime(2025, 1, 15)


@pytest.mark.parametrize("text,intent", [
    ("Why did revenue fall?", "causal"),
    ("What will sales be next quarter?", "forecast"),
    ("Show the trend in signups over time", "trend"),
    ("Compare EMEA versus APAC", "comparison"),
    ("Which region had the highest revenue?", "ranking"),
    ("How many distinct customers are there?", "distinct_count"),
    ("What is the total revenue?", "aggregate"),
])
def test_intent_detection(text, intent):
    assert intent in parse_question(text, now=NOW).intents


def test_narrative_words_are_not_treated_as_fields():
    # "drop", "next", "quarter" describe the question, not a column that must exist.
    terms = parse_question("Why did revenue drop next quarter?", now=NOW).terms
    assert "revenue" in terms
    for noise in ("drop", "next", "quarter", "why"):
        assert noise not in terms


@pytest.mark.parametrize("text,label,start,end", [
    ("revenue in 2023", "2023", "2023-01-01", "2023-12-31"),
    ("revenue in Q3 2023", "Q3 2023", "2023-07-01", "2023-09-30"),
    ("revenue in March 2024", "March 2024", "2024-03-01", "2024-03-31"),
])
def test_time_reference_bounds(text, label, start, end):
    ref = parse_question(text, now=NOW).time_refs[0]
    assert (ref.label, ref.start, ref.end) == (label, start, end)


def test_leap_year_february():
    ref = parse_question("signups in February 2024", now=NOW).time_refs[0]
    assert ref.end == "2024-02-29"


def test_bare_may_is_a_verb_not_a_month():
    assert parse_question("this may be wrong", now=NOW).time_refs == []
    assert parse_question("revenue in May 2024", now=NOW).time_refs != []


def test_group_by_stops_at_the_field_name():
    # "by region in 2024" names the field 'region', not 'region in 2024'.
    assert parse_question("revenue by region in 2024", now=NOW).group_by == ["region"]


def test_multi_word_group_by_is_kept():
    assert parse_question("churn by customer segment", now=NOW).group_by == ["customer segment"]


def test_quoted_literals_are_extracted():
    assert parse_question('revenue for "Acme Corp"', now=NOW).literals == ["Acme Corp"]


def test_precision_demand_is_noticed():
    assert parse_question("exactly how many orders?", now=NOW).demands_precision
    assert not parse_question("roughly how many orders?", now=NOW).demands_precision
