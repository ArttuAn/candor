"""The sufficiency gate."""


from candor import assess, profile
from candor.models import Verdict
from helpers import NOW


def sufficiency(path, question):
    return assess(profile(path, now=NOW), question, now=NOW)


def test_clean_data_answers_a_simple_question(clean_csv):
    result = sufficiency(clean_csv, "What was total revenue by region?")
    assert result.verdict is Verdict.ANSWERABLE
    assert result.confidence_ceiling == 1.0
    assert result.caveats == []
    assert "supports this question" in result.honest_response


def test_synonyms_map_business_words_to_column_names(clean_csv):
    result = sufficiency(clean_csv, "What was total revenue by region?")
    assert result.resolved["revenue"] == "revenue"
    assert result.resolved["region"] == "region"


def test_unknown_field_blocks_the_answer(clean_csv):
    result = sufficiency(clean_csv, "What is the average customer satisfaction score?")
    assert result.verdict is Verdict.INSUFFICIENT
    assert any(b.code == "unmapped_terms" for b in result.blockers)
    assert "satisfaction" in result.honest_response
    # And it says what would fix it, not just that it failed.
    assert result.unlock


def test_a_value_is_not_a_missing_field(clean_csv):
    # 'EMEA' is a value in region, not a column the data is missing.
    result = sufficiency(clean_csv, "What was revenue in EMEA?")
    assert result.resolved.get("emea") == "region (value)"
    assert not any(b.code == "unmapped_terms" for b in result.blockers)


def test_period_outside_the_range_is_refused(clean_csv):
    result = sufficiency(clean_csv, "What was revenue in 2019?")
    assert result.verdict is Verdict.INSUFFICIENT
    assert any(b.code == "range_not_covered" for b in result.blockers)
    assert "2019" in result.honest_response


def test_a_hole_on_the_period_asked_about_is_caught(write):
    """The dangerous case: the range covers Q3, but Q3 itself is empty."""
    rows = ["date,revenue"]
    for month in (1, 2, 3, 4, 5, 6, 10, 11, 12):     # July-September missing
        for day in (5, 15, 25):
            rows.append(f"2024-{month:02d}-{day:02d},100")
    path = write("hole.csv", "\n".join(rows) + "\n")

    result = sufficiency(path, "Why did revenue drop in Q3 2024?")
    assert result.verdict is Verdict.INSUFFICIENT
    assert any(b.code == "no_rows_in_period" for b in result.blockers)
    assert "not one row falls inside it" in result.honest_response


def test_a_partially_empty_period_is_caveated(write):
    rows = ["date,revenue"]
    for month in range(1, 13):
        days = (5,) if month == 8 else (5, 15, 25)
        if month == 7:
            days = ()                                 # July missing, Aug thin
        for day in days:
            rows.append(f"2024-{month:02d}-{day:02d},100")
    path = write("thin.csv", "\n".join(rows) + "\n")

    result = sufficiency(path, "What happened to revenue in Q3 2024?")
    topics = {c.topic for c in result.caveats}
    assert "missing_period" in topics
    assert "2024-07" in next(c.text for c in result.caveats if c.topic == "missing_period")


def test_causal_questions_always_forbid_causal_claims(clean_csv):
    result = sufficiency(clean_csv, "Why is revenue higher in EMEA?")
    assert "causation" in {c.topic for c in result.caveats}
    assert any("Do not claim causation" in claim for claim in result.forbidden_claims)
    assert result.confidence_ceiling < 1.0


def test_forecast_questions_forbid_stating_a_figure(clean_csv):
    result = sufficiency(clean_csv, "What will revenue be next quarter?")
    assert any("forecast" in claim.lower() for claim in result.forbidden_claims)


def test_trend_needs_more_than_two_periods(write):
    path = write("two.csv", "date,revenue\n2024-01-01,100\n2024-02-01,200\n")
    result = sufficiency(path, "What is the trend in revenue over time?")
    assert any(b.code == "insufficient_periods" for b in result.blockers)


def test_time_question_without_a_date_column_is_refused(write):
    path = write("nodate.csv", "region,revenue\nEMEA,100\nAPAC,200\n")
    result = sufficiency(path, "How did revenue change over time?")
    assert result.verdict is Verdict.INSUFFICIENT
    assert any(b.code == "no_time_dimension" for b in result.blockers)


def test_empty_dataset_refuses_everything(write):
    result = sufficiency(write("e.csv", "a,b\n"), "What is the total?")
    assert result.verdict is Verdict.INSUFFICIENT
    assert result.confidence_ceiling == 0.0
    assert "contains no rows" in result.honest_response


def test_mostly_missing_field_blocks_the_answer(write):
    rows = ["id,revenue"] + [f"{i}," for i in range(9)] + ["9,100"]
    result = sufficiency(write("sparse.csv", "\n".join(rows) + "\n"), "What is total revenue?")
    assert any(b.code == "field_mostly_missing" for b in result.blockers)


def test_small_sample_forbids_precise_percentages(write):
    rows = ["outcome"] + ["yes", "no"] * 5
    result = sufficiency(write("tiny.csv", "\n".join(rows) + "\n"),
                         "What percentage of outcomes are yes?")
    assert "small_sample" in {c.topic for c in result.caveats}
    assert any("precise percentages" in claim for claim in result.forbidden_claims)


def test_constant_column_cannot_be_grouped_by(write):
    rows = ["region,revenue"] + [f"EMEA,{i}" for i in range(20)]
    result = sufficiency(write("const.csv", "\n".join(rows) + "\n"), "revenue by region")
    assert any(b.code == "no_variation" for b in result.blockers)


def test_every_caveat_has_a_topic(messy_csv):
    result = sufficiency(messy_csv, "What was total revenue by region in 2024?")
    assert result.caveats
    for caveat in result.caveats:
        assert caveat.topic and caveat.text


def test_confidence_ceiling_falls_as_problems_accumulate(clean_csv, messy_csv):
    good = sufficiency(clean_csv, "What was total revenue by region?")
    bad = sufficiency(messy_csv, "Why did revenue drop by region in 2024?")
    assert bad.confidence_ceiling < good.confidence_ceiling


def test_honest_response_is_never_empty(messy_csv, clean_csv):
    for path in (messy_csv, clean_csv):
        for question in ("What is the total?", "Why did it change?", "What will happen next year?"):
            assert sufficiency(path, question).honest_response.strip()
