"""Answer verification.

Two properties matter more than any individual rule: a dishonest answer must be
rejected, and an honest one must pass cleanly. A verifier that punishes hedging
teaches the agent to hedge less, which is worse than having no verifier at all.
"""

import re

import pytest

from candor import assess, profile, verify
from candor.models import Severity
from helpers import NOW


@pytest.fixture
def prof(clean_csv):
    return profile(clean_csv, now=NOW)


def codes(report):
    return {f.code for f in report.findings}


def test_a_supported_answer_passes(prof):
    report = verify(prof, "Revenue totalled 39,318 across 36 orders in three regions.")
    assert report.verdict == "pass"
    assert report.honesty_score == 100.0


def test_count_above_the_row_count_is_critical(prof):
    report = verify(prof, "We processed 5,000 orders last year.")
    assert "count_exceeds_data" in codes(report)
    assert report.verdict == "reject"


def test_a_large_figure_that_is_not_a_count_is_not_flagged(prof):
    # "1,250,000 by Q2. All 4,500 orders..." must not read as 1.25M orders.
    report = verify(prof, "Revenue reached 1,250,000 by Q2. Separately, 12 orders shipped.")
    assert "count_exceeds_data" not in codes(report)


def test_a_field_that_does_not_exist_is_critical(prof):
    report = verify(prof, "The `customer_tier` field shows growth.")
    assert "phantom_field" in codes(report)


def test_a_year_outside_the_data_is_flagged(prof):
    report = verify(prof, "Revenue in 2019 was strong.")
    assert "out_of_range_period" in codes(report)


def test_causal_language_is_flagged(prof):
    report = verify(prof, "The decline was caused by the pricing change.")
    assert "causal_claim" in codes(report)


def test_causal_language_inside_a_hedge_is_not_flagged(prof):
    report = verify(prof, "This may have been caused by the pricing change, "
                          "though the data cannot establish that.")
    assert "causal_claim" not in codes(report)


def test_forecasts_are_flagged(prof):
    assert "unbacked_forecast" in codes(verify(prof, "Revenue will recover to 50,000 next year."))


def test_overclaimed_certainty_is_flagged(prof):
    assert "overclaimed_certainty" in codes(verify(prof, "This clearly proves the strategy worked."))


def test_at_all_is_an_idiom_not_a_universal_claim(write):
    path = write("n.csv", "a,b\n1,\n2,x\n")
    prof = profile(path, now=NOW)
    assert "unsupported_absolute" not in codes(verify(prof, "July contains no rows at all."))
    assert "unsupported_absolute" not in codes(verify(prof, "This is not all the data."))


def test_a_real_universal_claim_is_flagged(write):
    path = write("n.csv", "region,v\nEMEA,1\n,2\nAPAC,3\n")
    prof = profile(path, now=NOW)
    assert "unsupported_absolute" in codes(verify(prof, "Every account reduced its spend."))


def test_most_recent_is_a_date_not_a_ranking(write):
    path = write("n.csv", "region,v\nEMEA,1\n,2\nAPAC,3\n")
    prof = profile(path, now=NOW)
    assert "unstable_ranking" not in codes(verify(prof, "The most recent record is from June."))


def test_false_precision_on_a_small_sample(write):
    path = write("s.csv", "outcome\n" + "\n".join(["yes", "no"] * 6) + "\n")
    report = verify(profile(path, now=NOW), "62.5% of respondents said yes.")
    assert "false_precision" in codes(report)


def test_impossible_percentage(prof):
    assert "impossible_percentage" in codes(verify(prof, "That accounts for 140% of revenue."))


def test_missing_caveat_is_detected_by_topic(messy_csv):
    prof = profile(messy_csv, now=NOW)
    suff = assess(prof, "What was total revenue by region?", now=NOW)
    report = verify(prof, "Revenue was 549 across three regions.", sufficiency=suff)
    assert "omitted_disclosure" in codes(report)


def test_an_answer_that_states_its_caveats_is_not_nagged(write):
    rows = [f"2020-{m:02d}-{d:02d},{100 + m}" for m in range(1, 13) for d in (5, 15, 25)]
    path = write("stale.csv", "date,revenue\n" + "\n".join(rows) + "\n")
    prof = profile(path, now=NOW)
    suff = assess(prof, "What was total revenue?", now=NOW)
    assert {c.topic for c in suff.caveats} == {"staleness"}

    report = verify(prof, "Revenue totalled 1,278, but the most recent record is from "
                          "December 2020, so this describes the situation then.",
                    sufficiency=suff)
    assert "omitted_disclosure" not in codes(report)


def test_every_caveat_topic_has_a_disclosure_pattern(messy_csv, clean_csv):
    """A caveat the verifier cannot recognise would be reported as missing even
    when the answer states it perfectly, which trains the agent to hedge less."""
    from candor.verify import DISCLOSURE_PATTERNS

    seen = set()
    for path in (messy_csv, clean_csv):
        prof = profile(path, now=NOW)
        for question in (
            "What was total revenue by region?",
            "Why did revenue change over time?",
            "How many unique customers are there?",
            "What will revenue be next year?",
            "Which region had the highest revenue in 2024?",
        ):
            seen |= {c.topic for c in assess(prof, question, now=NOW).caveats}

    assert seen, "no caveats were produced, so this test proves nothing"
    assert seen <= set(DISCLOSURE_PATTERNS), f"no pattern for {seen - set(DISCLOSURE_PATTERNS)}"


@pytest.mark.parametrize("topic,prose", [
    ("staleness", "the most recent record is from June, so this is not current"),
    ("nulls", "the amount is missing for some rows"),
    ("duplicates", "one row is duplicated, which inflates the total"),
    ("causation", "the data can only show what moved together, not what caused it"),
    ("small_sample", "with only 8 rows this is indicative, not measured"),
    ("outliers", "one extreme outlier makes the median more honest than the mean"),
    ("placeholders", "'Unknown' is a placeholder, not a real region"),
    ("missing_period", "July contains no rows, so that may be missing data"),
    ("truncated", "only the first 1,000 rows were read"),
    ("small_groups", "each group holds fewer than 5 rows, so it is not stable"),
    ("case_variants", "two labels differ only by case, so the split is inconsistent"),
])
def test_disclosure_patterns_recognise_natural_prose(topic, prose):
    from candor.verify import DISCLOSURE_PATTERNS

    assert re.search(DISCLOSURE_PATTERNS[topic], prose), f"{topic} not recognised in: {prose}"


def test_answering_at_all_when_insufficient_is_critical(clean_csv):
    prof = profile(clean_csv, now=NOW)
    suff = assess(prof, "What is the average customer satisfaction score?", now=NOW)
    report = verify(prof, "The average satisfaction score is 4.2 out of 5.", sufficiency=suff)
    assert "answered_when_insufficient" in codes(report)
    assert report.verdict == "reject"


def test_refusing_when_insufficient_is_not_flagged(clean_csv):
    prof = profile(clean_csv, now=NOW)
    suff = assess(prof, "What is the average customer satisfaction score?", now=NOW)
    report = verify(prof, "I can't answer that — this data has no satisfaction field at all, "
                          "so any score I gave you would be invented.", sufficiency=suff)
    assert "answered_when_insufficient" not in codes(report)


def test_findings_are_ordered_worst_first(prof):
    report = verify(prof, "This clearly proves 9,000 orders were caused by the change, "
                          "and the `phantom_col` field agrees.")
    ranks = [f.severity.rank for f in report.findings]
    assert ranks == sorted(ranks, reverse=True)
    assert report.findings[0].severity is Severity.CRITICAL


def test_every_finding_carries_a_suggestion(prof):
    report = verify(prof, "This clearly proves 9,000 orders were caused by the change.")
    for finding in report.findings:
        assert finding.suggestion.strip(), f"{finding.code} has no suggestion"
