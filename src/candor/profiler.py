"""Turn a Table into a DatasetProfile: per-column statistics and shape facts.

This module only *measures*. Judgement about what counts as a defect lives in
issues.py, and scoring lives in grading.py, so the thresholds stay in one place
and the measurements stay reusable.
"""

from __future__ import annotations

import hashlib
import statistics
from collections import Counter
from datetime import UTC, datetime, timedelta

from . import detect
from .models import ColumnProfile, DatasetProfile, NumericStats, TemporalStats, TextStats
from .sources import Table, load

TOP_VALUES = 10
MAX_DISTINCT_TRACKED = 50_000

# A week of consecutive missing days is a stopped pipeline; two quiet Tuesdays
# are not. Only runs at least this long are reported as gaps.
MIN_DAILY_GAP_RUN = 7

# Below this many distinct values a column is a dimension, and candor keeps
# every value so it can answer "does this category exist?" exactly.
MAX_ENUMERATED_VALUES = 300


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


def _period_key(value: datetime, granularity: str) -> str:
    if granularity == "year":
        return f"{value.year:04d}"
    if granularity == "month":
        return f"{value.year:04d}-{value.month:02d}"
    return value.date().isoformat()


def _expected_periods(start: datetime, end: datetime, granularity: str) -> list[str]:
    """Every period key that *should* exist between start and end, inclusive."""
    keys: list[str] = []
    if granularity == "year":
        for year in range(start.year, end.year + 1):
            keys.append(f"{year:04d}")
    elif granularity == "month":
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            keys.append(f"{year:04d}-{month:02d}")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    else:
        cursor = start.date()
        last = end.date()
        while cursor <= last and len(keys) <= 20_000:
            keys.append(cursor.isoformat())
            cursor += timedelta(days=1)
    return keys


def _typical_interval_days(parsed: list[datetime]) -> int:
    """Median days between consecutive distinct dates in the series."""
    days = sorted({d.date() for d in parsed})
    if len(days) < 3:
        return 1
    intervals = [(b - a).days for a, b in zip(days, days[1:], strict=False)]
    return max(1, int(statistics.median(intervals)))


def _gap_grain(parsed: list[datetime], granularity: str) -> str:
    """The cadence the series is actually recorded at, not the one it is written at.

    A monthly report stamped 2024-01-15, 2024-02-15, ... is written with day
    precision but is monthly data. Judging its gaps daily would report 30 days
    missing between every pair of rows, which is noise, not a defect.
    """
    if granularity in ("month", "year"):
        return granularity
    days = {d.date() for d in parsed}
    months = {(d.year, d.month) for d in parsed}
    years = {d.year for d in parsed}
    if len(months) >= 2 and len(days) <= len(months) * 3:
        if len(years) >= 2 and len(months) <= len(years) * 2:
            return "year"
        return "month"
    return "day"


def _contiguous_runs(missing: list[str], expected: list[str]) -> list[tuple[str, str, int]]:
    """Group missing period keys into (start, end, length) consecutive runs."""
    position = {key: i for i, key in enumerate(expected)}
    runs: list[tuple[str, str, int]] = []
    start = previous = None
    for key in missing:
        if start is None:
            start = previous = key
            continue
        if position[key] == position[previous] + 1:
            previous = key
            continue
        runs.append((start, previous, position[previous] - position[start] + 1))
        start = previous = key
    if start is not None:
        runs.append((start, previous, position[previous] - position[start] + 1))
    return runs


def _case_variant_groups(counts: Counter[str]) -> int:
    """How many distinct values differ only by case or surrounding whitespace."""
    folded: Counter[str] = Counter()
    for value in counts:
        folded[value.strip().lower()] += 1
    return sum(1 for n in folded.values() if n > 1)


def profile_column(name: str, index: int, values: list[str], *, today: datetime) -> ColumnProfile:
    column = ColumnProfile(name=name, index=index, count=len(values))

    nulls = blanks = placeholders = 0
    placeholder_tokens: Counter[str] = Counter()
    present: list[str] = []

    for raw in values:
        text = raw if isinstance(raw, str) else detect.coerce_to_str(raw)
        if detect.is_null(text):
            nulls += 1
            if text == "":
                blanks += 1
            continue
        if detect.is_placeholder(text):
            placeholders += 1
            placeholder_tokens[text.strip().lower()] += 1
        present.append(text)

    column.nulls = nulls
    column.blanks = blanks
    column.placeholders = placeholders
    column.placeholder_tokens = [t for t, _ in placeholder_tokens.most_common(5)]
    column.non_null = len(present)

    counts: Counter[str] = Counter(present[:MAX_DISTINCT_TRACKED] if len(present) > MAX_DISTINCT_TRACKED else present)
    column.distinct = len(counts)
    column.unique_ratio = column.distinct / column.non_null if column.non_null else 0.0
    column.top_values = [[value, n] for value, n in counts.most_common(TOP_VALUES)]
    # For a dimension column the full value list is small and worth keeping: it
    # is what lets assess() tell "there is no such field" apart from "there is
    # no such value", which are very different answers to give a user.
    if column.distinct <= MAX_ENUMERATED_VALUES:
        column.value_set = sorted(counts)

    if not present:
        column.inferred_type = "empty"
        return column

    kinds = Counter(detect.detect_kind(v) for v in present)
    dominant, dominant_n = kinds.most_common(1)[0]

    # A column of bare 0/1 is boolean, not a measure to be averaged.
    non_placeholder = [v for v in present if not detect.is_placeholder(v)]
    distinct_lower = {v.strip().lower() for v in non_placeholder}
    if distinct_lower and distinct_lower <= (detect.BOOL_TRUE | detect.BOOL_FALSE) and len(distinct_lower) <= 3:
        dominant = "boolean"
        dominant_n = len(non_placeholder)

    # Integers that all parse as dates are dates (e.g. a bare year column).
    if dominant == "integer" and all(detect.parse_date(v) for v in present[:200]):
        if all(1900 <= int(float(v)) <= 2200 for v in present[:200] if detect.normalise_number(v)):
            dominant = "date"

    column.inferred_type = dominant
    column.type_mismatches = len(present) - dominant_n
    column.semantic_type = detect.detect_semantic(present)

    if dominant in ("integer", "number") and column.semantic_type in (None, "currency", "percentage"):
        column.numeric = _numeric_stats(present)
    elif dominant in ("date", "datetime"):
        column.temporal, column.format_variants = _temporal_stats(present, today=today)
    else:
        column.text = _text_stats(present, counts)
        variants = Counter()
        for value in present[:5000]:
            untrimmed, mojibake, _ = detect.text_flags(value)
            if untrimmed:
                variants["untrimmed"] += 1
            if mojibake:
                variants["mojibake"] += 1
        column.format_variants = dict(variants)

    return column


def _numeric_stats(present: list[str]) -> NumericStats:
    parsed = [n for n in (detect.normalise_number(v) for v in present) if n is not None]
    stats = NumericStats()
    if not parsed:
        return stats
    ordered = sorted(parsed)
    stats.min, stats.max = ordered[0], ordered[-1]
    stats.mean = statistics.fmean(parsed)
    stats.median = statistics.median(ordered)
    stats.stdev = statistics.pstdev(parsed) if len(parsed) > 1 else 0.0
    stats.p05 = _percentile(ordered, 0.05)
    stats.p25 = _percentile(ordered, 0.25)
    stats.p75 = _percentile(ordered, 0.75)
    stats.p95 = _percentile(ordered, 0.95)
    stats.zeros = sum(1 for n in parsed if n == 0)
    stats.negatives = sum(1 for n in parsed if n < 0)
    stats.integral = all(float(n).is_integer() for n in parsed)

    outliers = _outliers(parsed, ordered, stats)
    stats.outlier_count = len(outliers)
    stats.outlier_examples = sorted(outliers, key=lambda n: -abs(n))[:5]
    return stats


def _outliers(parsed: list[float], ordered: list[float], stats: NumericStats) -> list[float]:
    """Values far enough from the body of the distribution to distort a mean.

    Three fallbacks, because the usual Tukey fences collapse precisely where
    outliers hide best: a column that is 98% one value has an IQR of zero, and
    the two rogue millions in it would score as perfectly normal.
    """
    median = stats.median or 0.0
    iqr = (stats.p75 or 0.0) - (stats.p25 or 0.0)
    if iqr > 0:
        low, high = stats.p25 - 3 * iqr, stats.p75 + 3 * iqr
        return [n for n in parsed if n < low or n > high]

    deviations = sorted(abs(n - median) for n in parsed)
    mad = deviations[len(deviations) // 2]
    if mad > 0:
        # 6x the median absolute deviation is roughly 4 standard deviations for
        # a normal distribution, and far more robust for anything else.
        return [n for n in parsed if abs(n - median) > 6 * mad]

    # Everything but a handful of values is identical. Those few are the story.
    odd = [n for n in parsed if n != median]
    return odd if len(odd) <= len(parsed) * 0.05 else []


def _temporal_stats(present: list[str], *, today: datetime) -> tuple[TemporalStats, dict[str, int]]:
    stats = TemporalStats()
    variants: Counter[str] = Counter()
    parsed: list[datetime] = []
    granularities: Counter[str] = Counter()
    ambiguous = 0

    for value in present:
        result = detect.parse_date(value)
        if result is None:
            continue
        when, fmt_name, granularity = result
        parsed.append(when.replace(tzinfo=None))
        variants[fmt_name] += 1
        granularities[granularity] += 1
        if detect.slash_date_ambiguous(value):
            ambiguous += 1

    if ambiguous:
        variants["ambiguous_day_month"] = ambiguous
    if not parsed:
        return stats, dict(variants)

    parsed.sort()
    stats.min = parsed[0].isoformat(sep=" ")
    stats.max = parsed[-1].isoformat(sep=" ")
    stats.span_days = (parsed[-1] - parsed[0]).days
    stats.granularity = granularities.most_common(1)[0][0]
    stats.future_count = sum(1 for d in parsed if d > today)
    stats.staleness_days = max(0, (today - parsed[-1]).days)

    # A month-level histogram is small enough to keep and is what makes
    # "is there data for the period you asked about?" answerable later.
    month_counts = Counter(_period_key(d, "month") for d in parsed)
    if len(month_counts) <= 3_000:
        stats.month_counts = dict(sorted(month_counts.items()))

    grain = _gap_grain(parsed, stats.granularity or "day")
    stats.cadence = grain
    keys = [_period_key(d, grain) for d in parsed]
    observed = set(keys)
    stats.distinct_periods = len(observed)

    expected = _expected_periods(parsed[0], parsed[-1], grain)
    if len(expected) <= 20_000:
        missing = [k for k in expected if k not in observed]
        if grain == "day":
            # Scattered missing days are just quiet days. What matters is a
            # *run* of them: that is a stopped pipeline or a failed backfill.
            # How long a run has to be depends on how often records normally
            # arrive — in weekly data, six missing days is the normal state.
            runs = _contiguous_runs(missing, expected)
            threshold = max(MIN_DAILY_GAP_RUN, 3 * _typical_interval_days(parsed))
            long_runs = [r for r in runs if r[2] >= threshold]
            stats.gap_count = sum(r[2] for r in long_runs)
            stats.gap_periods = [
                f"{start}..{end} ({length} days)" for start, end, length in long_runs[:12]
            ]
        else:
            stats.gap_count = len(missing)
            stats.gap_periods = missing[:24]

    # A trailing period with far fewer rows than its neighbours is still filling.
    if grain in ("month", "year") and stats.distinct_periods >= 3:
        per_period = Counter(keys)
        ordered = sorted(per_period)
        last = per_period[ordered[-1]]
        earlier = [per_period[k] for k in ordered[:-1]]
        typical = statistics.median(earlier)
        if typical and last < typical * 0.6:
            stats.trailing_period_partial = True

    return stats, dict(variants)


def _text_stats(present: list[str], counts: Counter[str]) -> TextStats:
    lengths = [len(v) for v in present]
    stats = TextStats(
        min_length=min(lengths),
        max_length=max(lengths),
        mean_length=statistics.fmean(lengths),
    )
    for value in present[:20_000]:
        untrimmed, mojibake, control = detect.text_flags(value)
        stats.untrimmed += untrimmed
        stats.mojibake += mojibake
        stats.control_chars += control
    stats.case_variant_groups = _case_variant_groups(counts)
    return stats


def _duplicate_rows(table: Table) -> int:
    seen: set[str] = set()
    duplicates = 0
    for row in table.rows:
        digest = hashlib.blake2b("\x1f".join(row).encode("utf-8", "replace"), digest_size=16).digest()
        if digest in seen:
            duplicates += 1
        else:
            seen.add(digest)
    return duplicates


def _key_candidates(columns: list[ColumnProfile], row_count: int) -> list[str]:
    return [
        c.name for c in columns
        if row_count and c.non_null == row_count and c.distinct == row_count
    ]


def profile_table(table: Table, *, now: datetime | None = None) -> DatasetProfile:
    today = (now or datetime.now(UTC)).replace(tzinfo=None)
    profile = DatasetProfile(
        source=table.source,
        format=table.format,
        row_count=table.row_count,
        column_count=len(table.columns),
        truncated=table.truncated,
        generated_at=today.isoformat(timespec="seconds"),
        notes=list(table.notes),
    )
    profile.columns = [
        profile_column(name, i, table.column_values(i), today=today)
        for i, name in enumerate(table.columns)
    ]
    profile.duplicate_rows = _duplicate_rows(table)
    profile.key_candidates = _key_candidates(profile.columns, profile.row_count)
    return profile


def profile(source, *, max_rows: int = 200_000, table: str | None = None,
            query: str | None = None, now: datetime | None = None) -> DatasetProfile:
    """Profile a data source and attach issues and scores.

    This is the main entry point; `candor.profile(path)` gives you everything.
    """
    from .grading import score_profile
    from .issues import find_issues

    loaded = load(source, max_rows=max_rows, table=table, query=query)
    result = profile_table(loaded, now=now)
    result.issues = find_issues(result)
    score_profile(result)
    return result
