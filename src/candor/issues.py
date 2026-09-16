"""The defect catalogue.

Every rule answers three questions: what is wrong, what it breaks for an agent
trying to answer a question, and what a human would do to fix it. A finding
without a fix is just a complaint, so `fix` is mandatory on every rule.
"""

from __future__ import annotations

from .detect import STRONG_NUMERIC_SENTINELS
from .models import ColumnProfile, DatasetProfile, Dimension, Effort, Issue, Severity

# Question intents a defect can block. assess.py consumes these.
AGGREGATE = "aggregate"
TREND = "trend"
COMPARISON = "comparison"
LOOKUP = "lookup"
RANKING = "ranking"
FORECAST = "forecast"
CAUSAL = "causal"
DISTINCT_COUNT = "distinct_count"
JOIN = "join"

# Thresholds live here so a team can tune one file to their own standards.
NULL_WARN = 0.05
NULL_HIGH = 0.20
NULL_CRITICAL = 0.50
PLACEHOLDER_WARN = 0.02
MISMATCH_WARN = 0.02
MISMATCH_HIGH = 0.10
SMALL_SAMPLE = 30
TINY_SAMPLE = 10
STALE_DAYS = 90
VERY_STALE_DAYS = 365
OUTLIER_WARN = 0.01
DUPLICATE_WARN = 0.001


def _pct(part: int, whole: int) -> float:
    return 0.0 if not whole else part / whole


def _column_issues(col: ColumnProfile, profile: DatasetProfile) -> list[Issue]:
    found: list[Issue] = []
    n = col.count

    if col.inferred_type == "empty":
        return [Issue(
            code="empty_column",
            dimension=Dimension.COMPLETENESS,
            severity=Severity.HIGH,
            column=col.name,
            message=f"{col.name!r} has no values at all",
            evidence={"rows": n},
            impact="Any question that depends on this field is unanswerable.",
            fix=f"Backfill {col.name!r} at the source, or drop the column so it stops "
                "implying data exists.",
            effort=Effort.MEDIUM,
            blocks=[AGGREGATE, TREND, COMPARISON, LOOKUP, RANKING, FORECAST, CAUSAL],
        )]

    # -- completeness ------------------------------------------------------- #
    null_rate = _pct(col.nulls, n)
    if null_rate >= NULL_WARN:
        severity = (
            Severity.CRITICAL if null_rate >= NULL_CRITICAL
            else Severity.HIGH if null_rate >= NULL_HIGH
            else Severity.MEDIUM
        )
        found.append(Issue(
            code="high_null_rate",
            dimension=Dimension.COMPLETENESS,
            severity=severity,
            column=col.name,
            message=f"{col.name!r} is missing in {null_rate:.1%} of rows ({col.nulls:,} of {n:,})",
            evidence={"nulls": col.nulls, "rows": n, "rate": round(null_rate, 4)},
            impact="Aggregates over this field silently describe only the rows that "
                   "happen to have it, which is not the same population.",
            fix=f"Find out why {col.name!r} is missing. If it is missing-not-at-random "
                "(e.g. only for one source system), fixing ingestion matters more than "
                "imputing; if it is genuinely optional, record that explicitly.",
            effort=Effort.MEDIUM,
            blocks=[AGGREGATE, TREND, COMPARISON, RANKING] if null_rate >= NULL_HIGH else [],
        ))

    placeholder_rate = _pct(col.placeholders, max(col.non_null, 1))
    if placeholder_rate >= PLACEHOLDER_WARN:
        found.append(Issue(
            code="placeholder_values",
            dimension=Dimension.VALIDITY,
            severity=Severity.HIGH if placeholder_rate >= 0.10 else Severity.MEDIUM,
            column=col.name,
            message=f"{col.name!r} contains {col.placeholders:,} placeholder values "
                    f"({placeholder_rate:.1%}) such as {', '.join(repr(t) for t in col.placeholder_tokens)}",
            evidence={"count": col.placeholders, "tokens": col.placeholder_tokens},
            impact="These read as real values, so they inflate counts and appear as "
                   "legitimate categories in any breakdown.",
            fix=f"Map {', '.join(repr(t) for t in col.placeholder_tokens)} to NULL at "
                "ingestion, and add a constraint so they cannot come back.",
            effort=Effort.LOW,
            blocks=[DISTINCT_COUNT, RANKING, COMPARISON],
        ))

    # -- validity ----------------------------------------------------------- #
    mismatch_rate = _pct(col.type_mismatches, max(col.non_null, 1))
    if mismatch_rate >= MISMATCH_WARN:
        found.append(Issue(
            code="mixed_types",
            dimension=Dimension.VALIDITY,
            severity=Severity.HIGH if mismatch_rate >= MISMATCH_HIGH else Severity.MEDIUM,
            column=col.name,
            message=f"{col.name!r} looks like {col.inferred_type} but {col.type_mismatches:,} "
                    f"values ({mismatch_rate:.1%}) do not parse as one",
            evidence={"mismatches": col.type_mismatches, "inferred_type": col.inferred_type},
            impact="Whatever reads this column has to guess: skip the odd rows, coerce "
                   "them, or crash. All three change the answer.",
            fix=f"Enforce a single type for {col.name!r} at write time and quarantine "
                "rows that fail, rather than letting them through as text.",
            effort=Effort.MEDIUM,
            blocks=[AGGREGATE, TREND, RANKING],
        ))

    # -- consistency -------------------------------------------------------- #
    if col.temporal is not None:
        date_formats = {k: v for k, v in col.format_variants.items() if k != "ambiguous_day_month"}
        if len(date_formats) > 1:
            found.append(Issue(
                code="mixed_date_formats",
                dimension=Dimension.CONSISTENCY,
                severity=Severity.HIGH,
                column=col.name,
                message=f"{col.name!r} mixes {len(date_formats)} date formats: "
                        + ", ".join(f"{k} ({v:,})" for k, v in sorted(date_formats.items(), key=lambda kv: -kv[1])),
                evidence={"formats": date_formats},
                impact="Sorting and range filters are wrong for whichever format loses, "
                       "so a time series can be silently out of order.",
                fix=f"Normalise {col.name!r} to ISO-8601 (YYYY-MM-DD) on ingest.",
                effort=Effort.LOW,
                blocks=[TREND, FORECAST, COMPARISON],
            ))
        if col.format_variants.get("ambiguous_day_month"):
            count = col.format_variants["ambiguous_day_month"]
            found.append(Issue(
                code="ambiguous_dates",
                dimension=Dimension.CONSISTENCY,
                severity=Severity.CRITICAL,
                column=col.name,
                message=f"{col.name!r} has {count:,} dates where day and month are "
                        "interchangeable (e.g. 03/04/2025)",
                evidence={"count": count},
                impact="There is no way to know whether these are March or April. Any "
                       "monthly figure derived from them is unverifiable.",
                fix="Recover the intended locale from the source system and re-ingest as "
                    "ISO-8601. Do not infer it from the data.",
                effort=Effort.HIGH,
                blocks=[TREND, FORECAST, COMPARISON, AGGREGATE],
            ))
        if col.temporal.future_count:
            found.append(Issue(
                code="future_dates",
                dimension=Dimension.ACCURACY,
                severity=Severity.HIGH,
                column=col.name,
                message=f"{col.name!r} has {col.temporal.future_count:,} dates in the future "
                        f"(latest {col.temporal.max})",
                evidence={"count": col.temporal.future_count, "max": col.temporal.max},
                impact="Future-dated rows distort 'latest', 'to date' and any trailing "
                       "window calculation.",
                fix="Check for timezone handling or placeholder far-future dates "
                    "(9999-12-31) and exclude or correct them.",
                effort=Effort.LOW,
                blocks=[TREND, FORECAST],
            ))
        if col.temporal.gap_periods:
            shown = ", ".join(col.temporal.gap_periods[:6])
            more = len(col.temporal.gap_periods) - 6
            found.append(Issue(
                code="time_gaps",
                dimension=Dimension.COVERAGE,
                severity=Severity.HIGH,
                column=col.name,
                message=f"{col.name!r} has no rows for {col.temporal.gap_count:,} "
                        f"period(s) inside its own range: {shown}" + (f" (+{more} more)" if more > 0 else ""),
                evidence={"gaps": col.temporal.gap_periods, "count": col.temporal.gap_count,
                          "granularity": col.temporal.granularity},
                impact="A gap reads as a zero. Trends drawn across it are wrong in a way "
                       "that looks perfectly plausible.",
                fix="Determine whether the gap is a real absence of events or a missed "
                    "load. If it is a missed load, backfill before answering anything "
                    "about that period.",
                effort=Effort.MEDIUM,
                blocks=[TREND, FORECAST, COMPARISON],
            ))
        if col.temporal.trailing_period_partial:
            found.append(Issue(
                code="partial_trailing_period",
                dimension=Dimension.TIMELINESS,
                severity=Severity.MEDIUM,
                column=col.name,
                message=f"the most recent period in {col.name!r} has far fewer rows than "
                        "the ones before it and is probably still filling",
                evidence={"max": col.temporal.max},
                impact="Reporting the latest period as complete shows a fake decline.",
                fix="Exclude the trailing period from comparisons, or label it partial.",
                effort=Effort.LOW,
                blocks=[TREND, COMPARISON, FORECAST],
            ))

    if col.text is not None:
        if col.text.case_variant_groups:
            found.append(Issue(
                code="case_variants",
                dimension=Dimension.CONSISTENCY,
                severity=Severity.MEDIUM,
                column=col.name,
                message=f"{col.name!r} has {col.text.case_variant_groups} value(s) that differ "
                        "only by case or whitespace",
                evidence={"groups": col.text.case_variant_groups},
                impact="The same real-world thing is counted as two categories, which "
                       "splits totals and corrupts any group-by.",
                fix=f"Trim and case-normalise {col.name!r}, then deduplicate. Add a "
                    "controlled vocabulary if it is a category field.",
                effort=Effort.LOW,
                blocks=[DISTINCT_COUNT, COMPARISON, RANKING],
            ))
        if col.text.untrimmed:
            found.append(Issue(
                code="untrimmed_whitespace",
                dimension=Dimension.CONSISTENCY,
                severity=Severity.LOW,
                column=col.name,
                message=f"{col.name!r} has {col.text.untrimmed:,} values with leading or "
                        "trailing whitespace",
                evidence={"count": col.text.untrimmed},
                impact="Exact-match joins and filters miss these rows.",
                fix="Trim on ingest.",
                effort=Effort.LOW,
                blocks=[JOIN, LOOKUP],
            ))
        if col.text.mojibake:
            found.append(Issue(
                code="encoding_damage",
                dimension=Dimension.VALIDITY,
                severity=Severity.HIGH,
                column=col.name,
                message=f"{col.name!r} has {col.text.mojibake:,} values with mangled "
                        "characters (mis-decoded UTF-8)",
                evidence={"count": col.text.mojibake},
                impact="Names and labels are corrupted; they will be quoted back to the "
                       "user wrong.",
                fix="Re-ingest from source with the correct encoding. Repairing in place "
                    "is lossy.",
                effort=Effort.MEDIUM,
                blocks=[LOOKUP, JOIN],
            ))

    # -- uniqueness / cardinality ------------------------------------------- #
    if col.non_null and col.distinct == 1 and col.count > 1:
        found.append(Issue(
            code="constant_column",
            dimension=Dimension.COVERAGE,
            severity=Severity.MEDIUM,
            column=col.name,
            message=f"{col.name!r} has exactly one value for every row "
                    f"({col.top_values[0][0]!r})",
            evidence={"value": col.top_values[0][0]},
            impact="It cannot explain, split or compare anything. If a question asks to "
                   "break down by this field, the answer is that the data cannot.",
            fix="Confirm this is not an extraction bug that collapsed a real dimension.",
            effort=Effort.LOW,
            blocks=[COMPARISON, RANKING, CAUSAL],
        ))

    if col.semantic_type in ("email", "uuid") or col.name.lower().endswith(("_id", "id")):
        if col.non_null and 0.5 < col.unique_ratio < 1.0:
            duplicates = col.non_null - col.distinct
            found.append(Issue(
                code="duplicate_identifiers",
                dimension=Dimension.UNIQUENESS,
                severity=Severity.HIGH,
                column=col.name,
                message=f"{col.name!r} looks like an identifier but {duplicates:,} values "
                        "repeat",
                evidence={"duplicates": duplicates, "distinct": col.distinct},
                impact="Counting rows and counting entities give different answers, and it "
                       "is not obvious which one a question is asking for.",
                fix=f"Decide whether {col.name!r} is a key or an attribute. If it is a key, "
                    "deduplicate and add a uniqueness constraint.",
                effort=Effort.MEDIUM,
                blocks=[DISTINCT_COUNT, AGGREGATE, JOIN],
            ))

    # -- accuracy ----------------------------------------------------------- #
    if col.numeric is not None and col.non_null:
        num = col.numeric
        if _pct(num.outlier_count, col.non_null) >= OUTLIER_WARN and num.outlier_count:
            examples = ", ".join(f"{v:,.4g}" for v in num.outlier_examples[:3])
            found.append(Issue(
                code="extreme_outliers",
                dimension=Dimension.ACCURACY,
                severity=Severity.MEDIUM,
                column=col.name,
                message=f"{col.name!r} has {num.outlier_count:,} values far outside the "
                        f"normal range (e.g. {examples}; median {num.median:,.4g})",
                evidence={"count": num.outlier_count, "examples": num.outlier_examples,
                          "median": num.median},
                impact="The mean is pulled away from anything typical, so an 'average' "
                       "answer describes no actual row.",
                fix="Check whether these are data entry errors, unit mix-ups (cents vs "
                    "euros) or genuine extremes. Report the median alongside the mean.",
                effort=Effort.MEDIUM,
                blocks=[],
            ))
        sentinels = [v for v in (num.min, num.max) if v in STRONG_NUMERIC_SENTINELS]
        if sentinels:
            found.append(Issue(
                code="numeric_sentinels",
                dimension=Dimension.VALIDITY,
                severity=Severity.HIGH,
                column=col.name,
                message=f"{col.name!r} reaches {sentinels[0]:,.0f}, a classic stand-in for "
                        "'missing'",
                evidence={"values": sentinels},
                impact="Sentinels are counted as real measurements by every aggregation.",
                fix="Replace sentinel values with NULL at ingestion.",
                effort=Effort.LOW,
                blocks=[AGGREGATE, RANKING],
            ))
        negative_rate = _pct(num.negatives, col.non_null)
        if negative_rate > 0 and any(
            token in col.name.lower()
            for token in ("count", "qty", "quantity", "amount", "total", "age", "duration",
                          "price", "seats", "units", "items", "volume", "weight", "size",
                          "stock", "inventory", "revenue", "sales")
        ):
            found.append(Issue(
                code="impossible_negatives",
                dimension=Dimension.ACCURACY,
                severity=Severity.MEDIUM,
                column=col.name,
                message=f"{col.name!r} has {num.negatives:,} negative values, which its name "
                        "suggests should not happen",
                evidence={"negatives": num.negatives, "min": num.min},
                impact="Either the name is misleading or the values are wrong; both make "
                       "the field unsafe to quote.",
                fix="Confirm whether negatives mean refunds/reversals. If so, say that in "
                    "the schema; if not, fix the source.",
                effort=Effort.MEDIUM,
                blocks=[],
            ))

    return found


def _dataset_issues(profile: DatasetProfile) -> list[Issue]:
    found: list[Issue] = []
    rows = profile.row_count

    if rows == 0:
        return [Issue(
            code="empty_dataset",
            dimension=Dimension.SCALE,
            severity=Severity.CRITICAL,
            message="the dataset has no rows",
            evidence={"rows": 0},
            impact="Nothing can be answered from it. Any number in an answer would be invented.",
            fix="Check the extraction: a filter, a date range or a failed load is the "
                "usual cause of a zero-row file.",
            effort=Effort.MEDIUM,
            blocks=[AGGREGATE, TREND, COMPARISON, LOOKUP, RANKING, FORECAST, CAUSAL, DISTINCT_COUNT],
        )]

    if rows < TINY_SAMPLE:
        found.append(Issue(
            code="tiny_dataset",
            dimension=Dimension.SCALE,
            severity=Severity.HIGH,
            message=f"only {rows} rows",
            evidence={"rows": rows},
            impact="Percentages computed from this are misleading — one row moves the "
                   "result by more than a percentage point.",
            fix="Widen the extraction window or confirm this really is the whole population.",
            effort=Effort.MEDIUM,
            blocks=[FORECAST, CAUSAL],
        ))
    elif rows < SMALL_SAMPLE:
        found.append(Issue(
            code="small_dataset",
            dimension=Dimension.SCALE,
            severity=Severity.MEDIUM,
            message=f"only {rows} rows",
            evidence={"rows": rows},
            impact="Group-level breakdowns will land on single-digit sample sizes.",
            fix="Widen the extraction window, or answer only at the total level.",
            effort=Effort.MEDIUM,
            blocks=[FORECAST],
        ))

    if profile.truncated:
        found.append(Issue(
            code="truncated_read",
            dimension=Dimension.COVERAGE,
            severity=Severity.HIGH,
            message=f"only the first {rows:,} rows were read; the source has more",
            evidence={"rows_read": rows},
            impact="Every total and every 'no such value' conclusion is about a prefix of "
                   "the data, not the data.",
            fix="Re-run with a higher row limit, or aggregate at the source.",
            effort=Effort.LOW,
            blocks=[AGGREGATE, RANKING, DISTINCT_COUNT],
        ))

    duplicate_rate = _pct(profile.duplicate_rows, rows)
    if profile.duplicate_rows and duplicate_rate > DUPLICATE_WARN:
        found.append(Issue(
            code="duplicate_rows",
            dimension=Dimension.UNIQUENESS,
            severity=Severity.HIGH if duplicate_rate > 0.05 else Severity.MEDIUM,
            message=f"{profile.duplicate_rows:,} fully duplicated rows ({duplicate_rate:.1%})",
            evidence={"duplicates": profile.duplicate_rows, "rate": round(duplicate_rate, 4)},
            impact="Every count, sum and average is inflated by an unknown amount.",
            fix="Find the duplicate source (a re-run load, a fan-out join) and deduplicate "
                "on a real key before aggregating.",
            effort=Effort.MEDIUM,
            blocks=[AGGREGATE, RANKING, DISTINCT_COUNT],
        ))

    if not profile.key_candidates and rows > 1:
        found.append(Issue(
            code="no_unique_key",
            dimension=Dimension.UNIQUENESS,
            severity=Severity.MEDIUM,
            message="no column uniquely identifies a row",
            evidence={},
            impact="Rows cannot be deduplicated or joined reliably, so 'how many X' has "
                   "no defensible answer.",
            fix="Add a primary key, or document the composite key that identifies a row.",
            effort=Effort.MEDIUM,
            blocks=[DISTINCT_COUNT, JOIN],
        ))

    temporal = profile.temporal_columns
    if not temporal:
        found.append(Issue(
            code="no_time_dimension",
            dimension=Dimension.COVERAGE,
            severity=Severity.MEDIUM,
            message="no date or timestamp column was found",
            evidence={},
            impact="Nothing about change, trend, recency or 'last quarter' can be answered.",
            fix="Include the event or record timestamp in the extraction.",
            effort=Effort.MEDIUM,
            blocks=[TREND, FORECAST],
        ))
    else:
        freshest = min(
            (c.temporal.staleness_days for c in temporal
             if c.temporal and c.temporal.staleness_days is not None),
            default=None,
        )
        if freshest is not None and freshest >= STALE_DAYS:
            found.append(Issue(
                code="stale_data",
                dimension=Dimension.TIMELINESS,
                severity=Severity.HIGH if freshest >= VERY_STALE_DAYS else Severity.MEDIUM,
                message=f"the most recent record is {freshest:,} days old",
                evidence={"staleness_days": freshest},
                impact="Any answer phrased in the present tense is a claim about the past.",
                fix="Refresh the extract. If the pipeline stopped, that is the finding.",
                effort=Effort.LOW,
                blocks=[FORECAST],
            ))

    if len(profile.columns) == 1:
        found.append(Issue(
            code="single_column",
            dimension=Dimension.COVERAGE,
            severity=Severity.MEDIUM,
            message="the dataset has a single column",
            evidence={},
            impact="Nothing can be compared, grouped or explained.",
            fix="Include the dimensions you want to slice by in the extraction.",
            effort=Effort.MEDIUM,
            blocks=[COMPARISON, RANKING, CAUSAL],
        ))

    return found


def find_issues(profile: DatasetProfile) -> list[Issue]:
    """All defects in a profiled dataset, worst first."""
    issues = _dataset_issues(profile)
    if profile.row_count:
        for col in profile.columns:
            issues.extend(_column_issues(col, profile))
    issues.sort(key=lambda i: (-i.severity.rank, i.column or "", i.code))
    return issues
