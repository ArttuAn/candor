"""Profiling and the defect catalogue."""

from candor import profile
from candor.models import Severity
from helpers import NOW


def codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_clean_data_is_graded_well(clean_csv):
    result = profile(clean_csv, now=NOW)
    assert result.row_count == 36
    assert result.grade in ("A", "B")
    assert "high_null_rate" not in codes(result)
    assert "duplicate_rows" not in codes(result)


def test_types_are_inferred(clean_csv):
    result = profile(clean_csv, now=NOW)
    assert result.column("date").inferred_type == "date"
    assert result.column("revenue").inferred_type == "integer"
    assert result.column("region").inferred_type == "string"


def test_messy_data_finds_every_planted_defect(messy_csv):
    result = profile(messy_csv, now=NOW)
    found = codes(result)
    for expected in (
        "mixed_date_formats",      # iso + us + euro in one column
        "ambiguous_dates",         # 01/05/2024
        "case_variants",           # 'EMEA' vs 'emea '
        "untrimmed_whitespace",    # 'emea '
        "placeholder_values",      # 'Unknown'
        "duplicate_rows",          # row 6 twice
        "duplicate_identifiers",   # order_id 6 twice
        "high_null_rate",          # blank amount / email
    ):
        assert expected in found, f"missed {expected}: found {sorted(found)}"


def test_ambiguous_dates_are_critical(messy_csv):
    result = profile(messy_csv, now=NOW)
    issue = next(i for i in result.issues if i.code == "ambiguous_dates")
    assert issue.severity is Severity.CRITICAL
    assert issue.fix  # a finding without a fix is just a complaint


def test_every_issue_carries_a_fix_and_an_impact(messy_csv):
    for issue in profile(messy_csv, now=NOW).issues:
        assert issue.fix.strip(), f"{issue.code} has no fix"
        assert issue.impact.strip(), f"{issue.code} has no impact"


def test_placeholders_are_not_counted_as_present(write):
    path = write("p.csv", "status\nactive\nactive\nunknown\nTBD\n")
    column = profile(path, now=NOW).column("status")
    assert column.placeholders == 2
    assert column.non_null == 4        # they are present, and that is the problem


def test_na_is_a_null_not_a_category(write):
    path = write("na.csv", "plan\npro\nN/A\nN/A\nfree\n")
    column = profile(path, now=NOW).column("plan")
    assert column.nulls == 2
    assert column.distinct == 2


def test_constant_column_is_flagged(write):
    path = write("c.csv", "src,v\nsf,1\nsf,2\nsf,3\n")
    assert "constant_column" in codes(profile(path, now=NOW))


def test_empty_dataset_is_critical(write):
    result = profile(write("e.csv", "a,b\n"), now=NOW)
    assert result.row_count == 0
    assert "empty_dataset" in codes(result)
    assert result.grade == "F"


def test_outliers_use_the_median_not_the_mean(write):
    rows = "\n".join(str(v) for v in [10] * 100 + [10_000_000, 9_000_000])
    result = profile(write("o.csv", f"value\n{rows}\n"), now=NOW)
    column = result.column("value")
    assert column.numeric.outlier_count == 2
    assert column.numeric.median == 10
    assert "extreme_outliers" in codes(result)


def test_daily_gaps_report_runs_not_scattered_days(write):
    # 30 consecutive days, then a 20-day hole, then 30 more.
    from datetime import date, timedelta

    start = date(2024, 1, 1)
    days = [start + timedelta(days=i) for i in range(30)]
    days += [start + timedelta(days=i) for i in range(50, 80)]
    path = write("g.csv", "d\n" + "\n".join(d.isoformat() for d in days) + "\n")
    stats = profile(path, now=NOW).column("d").temporal
    assert stats.gap_count == 20
    assert len(stats.gap_periods) == 1
    assert "2024-01-31" in stats.gap_periods[0]


def test_scattered_missing_days_are_not_reported_as_gaps(write):
    # Weekends missing: normal for a business dataset, not a defect.
    from datetime import date, timedelta

    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(60)]
    weekdays = [d for d in days if d.weekday() < 5]
    path = write("w.csv", "d\n" + "\n".join(d.isoformat() for d in weekdays) + "\n")
    assert profile(path, now=NOW).column("d").temporal.gap_count == 0


def test_month_histogram_is_kept(clean_csv):
    counts = profile(clean_csv, now=NOW).column("date").temporal.month_counts
    assert len(counts) == 12
    assert counts["2024-01"] == 3


def test_future_dates_are_flagged(write):
    path = write("f.csv", "d\n2024-01-01\n2099-01-01\n")
    assert "future_dates" in codes(profile(path, now=NOW))


def test_staleness_is_measured_against_now(clean_csv):
    stats = profile(clean_csv, now=NOW).column("date").temporal
    assert stats.staleness_days == (NOW.date() - __import__("datetime").date(2024, 12, 15)).days


def test_score_is_capped_by_the_worst_issue(messy_csv):
    result = profile(messy_csv, now=NOW)
    assert result.score <= 55.0        # a critical issue caps the dataset
    assert result.grade == "F"


def test_dimension_scores_are_reported_separately(messy_csv):
    scores = profile(messy_csv, now=NOW).scores
    assert set(scores) >= {"completeness", "validity", "consistency", "uniqueness"}
    assert scores["consistency"] < 100


def test_profile_is_json_serialisable(messy_csv):
    import json

    from candor import to_json

    payload = json.loads(to_json(profile(messy_csv, now=NOW)))
    assert payload["grade"]
    assert payload["issues"][0]["severity"] in {"critical", "high", "medium", "low", "info"}


def test_value_set_is_kept_for_dimension_columns(clean_csv):
    assert profile(clean_csv, now=NOW).column("region").value_set == ["AMER", "APAC", "EMEA"]


def test_value_set_is_skipped_for_high_cardinality(write):
    path = write("h.csv", "id\n" + "\n".join(f"id-{i}" for i in range(500)) + "\n")
    assert profile(path, now=NOW).column("id").value_set == []
