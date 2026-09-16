"""The sufficiency gate: can this question be answered honestly from this data?

The output is built to be pasted straight into an agent's context. Three parts
matter most:

  verdict            answerable | partial | insufficient
  caveats            sentences the answer must contain
  honest_response    what to say instead, when the honest answer is "I can't"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from . import question as qparse
from .grading import trust_level
from .issues import (
    AGGREGATE,
    CAUSAL,
    COMPARISON,
    DISTINCT_COUNT,
    FORECAST,
    RANKING,
    TREND,
)
from .models import Caveat, ColumnProfile, DatasetProfile, Gap, Severity, Sufficiency, Verdict
from .question import QuestionSpec

# Business vocabulary -> the column names it usually hides behind.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "sales", "amount", "turnover", "gmv", "income", "net", "gross", "total", "price", "value"),
    "sales": ("sales", "revenue", "amount", "orders", "units", "quantity"),
    "customer": ("customer", "client", "account", "user", "buyer", "member", "subscriber",
                 "respondent", "contact", "lead"),
    "user": ("user", "customer", "account", "member", "subscriber", "visitor"),
    "churn": ("churn", "churned", "cancelled", "canceled", "status", "active", "retention", "lost"),
    "signup": ("signup", "signed", "registration", "created", "joined", "onboard"),
    "region": ("region", "country", "market", "geo", "territory", "location", "area", "state", "city"),
    "country": ("country", "region", "market", "geo", "nation", "location"),
    "segment": ("segment", "tier", "plan", "category", "type", "class", "group", "cohort"),
    "product": ("product", "sku", "item", "service", "plan", "offering"),
    "channel": ("channel", "source", "medium", "campaign", "referrer", "utm"),
    "price": ("price", "amount", "cost", "value", "rate", "fee", "mrr", "arr"),
    "cost": ("cost", "expense", "spend", "price", "cogs"),
    "profit": ("profit", "margin", "net", "earnings", "ebitda"),
    "order": ("order", "transaction", "purchase", "invoice", "booking", "sale"),
    "date": ("date", "time", "timestamp", "day", "month", "created", "occurred", "at"),
    "age": ("age", "years", "birth", "dob"),
    "status": ("status", "state", "stage", "phase", "active", "result"),
    "conversion": ("conversion", "converted", "convert", "funnel", "won", "closed"),
    "engagement": ("engagement", "activity", "sessions", "visits", "events", "usage", "active"),
    "satisfaction": ("satisfaction", "csat", "nps", "score", "rating", "review", "feedback"),
    "retention": ("retention", "retained", "churn", "active", "renewal"),
    "traffic": ("traffic", "visits", "sessions", "pageviews", "views", "clicks"),
    "employee": ("employee", "staff", "headcount", "worker", "person", "agent"),
    "salary": ("salary", "compensation", "pay", "wage", "income"),
}

CONFIDENCE_FLOOR = 0.05


def _normalise(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


def _expand(term: str) -> set[str]:
    """A term plus its plural/singular forms plus their business synonyms.

    Two passes, because the synonym table is keyed on singulars: expanding
    'customers' has to reach 'customer' first or it finds nothing.
    """
    words = set(_normalise(term))
    for word in list(words):
        words.add(word[:-1] if word.endswith("s") and len(word) > 3 else word + "s")
    for word in list(words):
        words.update(SYNONYMS.get(word, ()))
    return words


def _match_score(term: str, column: ColumnProfile) -> float:
    """How strongly a question term names this column. 0..1."""
    term_l = term.lower().strip()
    col_l = column.name.lower().strip()
    if term_l == col_l:
        return 1.0

    col_tokens = set(_normalise(column.name))
    term_tokens = set(_normalise(term))
    if not col_tokens or not term_tokens:
        return 0.0
    if term_tokens <= col_tokens:
        return 0.9
    if term_l.replace(" ", "") == col_l.replace("_", "").replace(" ", ""):
        return 0.95

    expanded = _expand(term)
    overlap = expanded & col_tokens
    if not overlap:
        return 0.0

    # A synonym hit is weaker than a literal one. Extra words in the *column*
    # name only dilute it a little — 'amount_eur' is still the revenue column
    # even though 'eur' matches nothing in the question.
    direct = bool(term_tokens & col_tokens)
    col_coverage = len(overlap) / len(col_tokens)

    # Extra words in the *term* are decisive, though. "customer satisfaction"
    # finding only 'customer' in customer_id is a half-match, and treating it
    # as a hit is how an agent ends up answering a question about satisfaction
    # from a column of IDs.
    matched = sum(1 for token in term_tokens if _expand(token) & col_tokens)
    term_coverage = matched / len(term_tokens)

    return (0.88 if direct else 0.72) * (0.65 + 0.35 * col_coverage) * term_coverage


def _resolve_terms(spec: QuestionSpec, profile: DatasetProfile) -> tuple[dict[str, str], list[str]]:
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for term in spec.terms:
        best, best_score = None, 0.0
        for column in profile.columns:
            score = _match_score(term, column)
            if score > best_score:
                best, best_score = column, score
        if best is not None and best_score >= 0.45:
            resolved[term] = best.name
            continue
        # Not a field name — but it may be a value inside one.
        holder = _find_value(profile, term)
        if holder is not None:
            resolved[term] = f"{holder} (value)"
        elif " " not in term:
            unresolved.append(term)
    # Drop single words that a matched two-word phrase already covers.
    covered = {w for phrase in resolved if " " in phrase for w in phrase.split()}
    return resolved, [t for t in unresolved if t not in covered]


def _find_value(profile: DatasetProfile, literal: str) -> str | None:
    """Which column, if any, actually contains this literal value.

    Checks the full value set of dimension columns and the common values of the
    rest, so "enterprise" resolves to the segment column rather than being
    reported as a field the data is missing.
    """
    needle = literal.strip().lower()
    for column in profile.columns:
        candidates = column.value_set or [v for v, _ in column.top_values]
        for value in candidates:
            if str(value).strip().lower() == needle:
                return column.name
    return None


def _fully_enumerated(profile: DatasetProfile) -> bool:
    """True when every categorical column's values are known exhaustively."""
    return all(c.value_set or c.inferred_type not in ("string", "boolean")
               for c in profile.columns)


def _covers(column: ColumnProfile, ref: qparse.TimeRef) -> bool | None:
    """True/False if the column's range covers the reference; None if unknowable."""
    if column.temporal is None or not column.temporal.min or not column.temporal.max:
        return None
    if ref.start is None or ref.end is None:
        return None
    return column.temporal.min[:10] <= ref.end and column.temporal.max[:10] >= ref.start


def _months_in(start: str, end: str) -> list[str]:
    """Month keys (YYYY-MM) touched by an inclusive date range."""
    year, month = int(start[:4]), int(start[5:7])
    last = (int(end[:4]), int(end[5:7]))
    keys: list[str] = []
    while (year, month) <= last and len(keys) < 1200:
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return keys


@dataclass
class _Window:
    rows: int
    expected: float
    empty_months: list[str]
    total_months: int


def _window_density(column: ColumnProfile, ref: qparse.TimeRef) -> _Window | None:
    """How much data actually sits inside the period the question asks about.

    This is what separates "the range covers Q3" from "there is actually data
    for Q3". A gap that lands exactly on the period asked about is the most
    dangerous shape a dataset can have, because every other check passes and
    the missing rows read as a real decline.
    """
    stats = column.temporal
    if stats is None or not stats.month_counts or not ref.start or not ref.end:
        return None
    months = _months_in(ref.start, ref.end)
    if not months:
        return None
    inside = sum(stats.month_counts.get(key, 0) for key in months)
    outside = [n for key, n in stats.month_counts.items() if key not in months and n > 0]
    if not outside:
        return None
    typical = sorted(outside)[len(outside) // 2]
    empty = [key for key in months if not stats.month_counts.get(key)]
    return _Window(rows=inside, expected=typical * len(months),
                   empty_months=empty, total_months=len(months))


def _relevant_columns(spec: QuestionSpec, profile: DatasetProfile,
                      resolved: dict[str, str]) -> list[ColumnProfile]:
    names = set(resolved.values())
    return [c for c in profile.columns if c.name in names]


def assess(profile: DatasetProfile, question: str, *,
           spec: QuestionSpec | None = None, now: datetime | None = None) -> Sufficiency:
    """Decide whether `question` can be answered honestly from `profile`."""
    now = (now or datetime.now(UTC)).replace(tzinfo=None)
    spec = spec or qparse.parse(question, now=now)

    result = Sufficiency(question=question.strip(), source=profile.source, intents=list(spec.intents))
    blockers: list[Gap] = []
    caveats: list[str] = []
    forbidden: list[str] = []
    unlock: list[str] = []
    confidence = 1.0

    # ---- the data has to exist at all ------------------------------------- #
    if profile.row_count == 0:
        result.verdict = Verdict.INSUFFICIENT
        result.confidence_ceiling = 0.0
        result.blockers = [Gap(
            code="empty_dataset", severity=Severity.CRITICAL,
            message="the dataset contains no rows",
            need="any rows at all",
            fix="Check the extraction filters and re-run the export.",
        )]
        result.unlock = ["Re-export the data; the current file has zero rows."]
        result.honest_response = (
            f"I can't answer that. The dataset I was given ({profile.source}) contains no rows, "
            "so any figure I gave you would be invented. The export needs to be re-run before "
            "this question can be answered."
        )
        return result

    resolved, unresolved = _resolve_terms(spec, profile)
    result.resolved = resolved
    result.unresolved_terms = unresolved
    relevant = _relevant_columns(spec, profile, resolved)

    # ---- terms the data has no field for ---------------------------------- #
    if unresolved:
        shown = ", ".join(repr(t) for t in unresolved[:5])
        # If at least as many content words went unmatched as matched, the
        # subject of the question is absent — not merely some detail of it.
        severity = Severity.CRITICAL if len(unresolved) >= len(resolved) else Severity.HIGH
        blockers.append(Gap(
            code="unmapped_terms", severity=severity,
            message=f"nothing in the data corresponds to {shown}",
            need=f"a column holding {shown}",
            fix=f"Add the field(s) behind {shown} to the extract, or tell me which existing "
                f"column stands for them (available: {', '.join(c.name for c in profile.columns[:12])}).",
            columns=[],
        ))
        confidence *= 0.35 if severity is Severity.CRITICAL else 0.65

    # ---- literals the data has never seen --------------------------------- #
    for literal in spec.literals:
        if _find_value(profile, literal) is None:
            exact = _fully_enumerated(profile)
            blockers.append(Gap(
                code="value_not_present",
                severity=Severity.HIGH if exact else Severity.MEDIUM,
                message=f"the value {literal!r} does not appear in the data"
                        if exact else
                        f"{literal!r} does not appear in any field I can enumerate; some "
                        "columns have too many distinct values to rule it out",
                need=f"rows where some column equals {literal!r}",
                fix=f"Confirm the spelling of {literal!r}, or widen the extract to include it.",
            ))
            confidence *= 0.5

    # ---- time ------------------------------------------------------------- #
    temporal = profile.temporal_columns
    needs_time = bool({TREND, FORECAST} & set(spec.intents)) or bool(spec.time_refs)

    if needs_time and not temporal:
        blockers.append(Gap(
            code="no_time_dimension", severity=Severity.CRITICAL,
            message="the question is about time, but the data has no date column",
            need="a date or timestamp column",
            fix="Include the event timestamp in the extract.",
        ))
        unlock.append("Add a date/timestamp column; without it nothing time-based is answerable.")
        confidence *= 0.2
    elif temporal:
        primary = max(temporal, key=lambda c: c.temporal.distinct_periods if c.temporal else 0)
        stats = primary.temporal
        assert stats is not None

        for ref in spec.time_refs:
            covered = _covers(primary, ref)
            if covered is False:
                blockers.append(Gap(
                    code="range_not_covered", severity=Severity.CRITICAL,
                    message=f"the question asks about {ref.label}, but {primary.name!r} only "
                            f"covers {stats.min[:10]} to {stats.max[:10]}",
                    need=f"rows from {ref.label}",
                    fix=f"Extend the extract to cover {ref.label}.",
                    columns=[primary.name],
                ))
                unlock.append(f"Extend the date range to include {ref.label}.")
                confidence *= 0.15
            elif covered is True and ref.start and ref.end:
                partial_start = stats.min[:10] > ref.start
                partial_end = stats.max[:10] < ref.end
                if partial_start or partial_end:
                    caveats.append(Caveat(
                        "partial_period",
                        f"The data covers only part of {ref.label} "
                        f"({stats.min[:10]} to {stats.max[:10]}), so figures for that period are incomplete."
                    ))
                    confidence *= 0.7

                window = _window_density(primary, ref)
                if window is not None:
                    if window.rows == 0:
                        blockers.append(Gap(
                            code="no_rows_in_period", severity=Severity.CRITICAL,
                            message=f"the date range spans {ref.label}, but not one row falls "
                                    "inside it",
                            need=f"rows dated within {ref.label}",
                            fix=f"Backfill {ref.label}. Until then any figure for that period "
                                "describes missing data, not the business.",
                            columns=[primary.name],
                        ))
                        confidence *= 0.1
                    elif window.empty_months:
                        caveats.append(Caveat(
                            "missing_period",
                            f"{len(window.empty_months)} of the {window.total_months} months in "
                            f"{ref.label} ({', '.join(window.empty_months)}) "
                            f"{'contains' if len(window.empty_months) == 1 else 'contain'} no rows "
                            f"at all, while the months around them do. A drop in {ref.label} is at "
                            "least as likely to be missing data as a real decline — that has to "
                            "be ruled out before it is explained."
                        ))
                        confidence *= 0.3
                    elif window.expected and window.rows < window.expected * 0.6:
                        caveats.append(Caveat(
                            "thin_period",
                            f"Only {window.rows:,} rows fall inside {ref.label}, against roughly "
                            f"{int(window.expected):,} for a period that length elsewhere in the "
                            f"data, so {ref.label} looks under-loaded rather than quiet."
                        ))
                        confidence *= 0.45

        if TREND in spec.intents and stats.distinct_periods < 3:
            blockers.append(Gap(
                code="insufficient_periods", severity=Severity.HIGH,
                message=f"a trend needs several periods; {primary.name!r} has only "
                        f"{stats.distinct_periods}",
                need="at least 3 distinct time periods",
                fix="Extend the history in the extract.",
                columns=[primary.name],
            ))
            confidence *= 0.3

        if FORECAST in spec.intents:
            forbidden.append(
                "Do not state a forecast as a figure. This is historical data with no model "
                "behind it; describing the past trend is the most that is supported."
            )
            if stats.distinct_periods < 12:
                blockers.append(Gap(
                    code="insufficient_history", severity=Severity.HIGH,
                    message=f"only {stats.distinct_periods} periods of history — far too few to "
                            "project forward, and not enough to see seasonality",
                    need="at least 12 periods, and a stated model",
                    fix="Extend history to 24+ periods and use an explicit forecasting method.",
                    columns=[primary.name],
                ))
            confidence *= 0.35

        # A monthly series that ends last month is current; a daily one is not.
        stale_after = {"year": 500, "month": 75}.get(stats.cadence or "day", 30)
        if stats.staleness_days and stats.staleness_days > stale_after:
            caveats.append(Caveat(
                "staleness",
                f"The most recent record is from {stats.max[:10]} "
                f"({stats.staleness_days:,} days ago), so this describes the situation then, not now."
            ))
            confidence *= 0.95 if stats.staleness_days < 90 else 0.8

        if stats.gap_periods:
            caveats.append(Caveat(
                "time_gaps",
                f"{stats.gap_count:,} period(s) inside the range have no rows at all "
                f"({', '.join(stats.gap_periods[:3])}…) — these read as zero and are probably missing data."
            ))
            confidence *= 0.75

        if stats.trailing_period_partial and {TREND, COMPARISON} & set(spec.intents):
            caveats.append(Caveat(
                "partial_trailing",
                "The most recent period looks incomplete, so the final point will show a "
                "decline that may not be real. It is excluded from any comparison below."
            ))
            confidence *= 0.9

    # ---- grouping --------------------------------------------------------- #
    for group in spec.group_by:
        column_name = resolved.get(group) or resolved.get(group.split()[-1])
        column = profile.column(column_name) if column_name else None
        if column is None:
            blockers.append(Gap(
                code="no_group_column", severity=Severity.HIGH,
                message=f"the question asks to break down by {group!r}, but no such column exists",
                need=f"a {group!r} column",
                fix=f"Add {group!r} to the extract.",
            ))
            confidence *= 0.4
            continue
        if column.distinct <= 1:
            blockers.append(Gap(
                code="no_variation", severity=Severity.HIGH,
                message=f"{column.name!r} has the same value in every row, so it cannot "
                        "split anything",
                need=f"variation in {column.name!r}",
                fix=f"Check whether the extract accidentally filtered {column.name!r} to one value.",
                columns=[column.name],
            ))
            confidence *= 0.4
        elif profile.row_count / max(column.distinct, 1) < 5:
            caveats.append(Caveat(
                "small_groups",
                f"Broken down by {column.name!r}, most groups hold fewer than 5 rows "
                f"({column.distinct:,} groups across {profile.row_count:,} rows), so per-group "
                "figures are not stable."
            ))
            confidence *= 0.7

    # ---- quality of the fields the question actually touches --------------- #
    for column in relevant:
        null_rate = 1 - column.completeness
        if null_rate >= 0.5:
            blockers.append(Gap(
                code="field_mostly_missing", severity=Severity.CRITICAL,
                message=f"{column.name!r} is empty in {null_rate:.0%} of rows",
                need=f"{column.name!r} populated for most rows",
                fix=f"Fix the pipeline that leaves {column.name!r} empty before using it.",
                columns=[column.name],
            ))
            confidence *= 0.25
        elif null_rate >= 0.05:
            caveats.append(Caveat(
                "nulls",
                f"{column.name!r} is missing in {null_rate:.0%} of rows, so this describes only "
                f"the {column.non_null:,} rows that have it — not all {column.count:,}."
            ))
            confidence *= (1 - null_rate * 0.6)

        if column.placeholders:
            caveats.append(Caveat(
                "placeholders",
                f"{column.name!r} contains {column.placeholders:,} placeholder values "
                f"({', '.join(repr(t) for t in column.placeholder_tokens[:3])}) that look like "
                "real data but are not."
            ))
            confidence *= 0.9

        if column.numeric and column.numeric.outlier_count and {AGGREGATE, RANKING} & set(spec.intents):
            caveats.append(Caveat(
                "outliers",
                f"{column.name!r} has {column.numeric.outlier_count:,} extreme outliers "
                f"(median {column.numeric.median:,.4g}, max {column.numeric.max:,.4g}); the mean "
                "is not representative, so the median is reported alongside it."
            ))
            confidence *= 0.9

        if column.text and column.text.case_variant_groups and {COMPARISON, RANKING, DISTINCT_COUNT} & set(spec.intents):
            caveats.append(Caveat(
                "case_variants",
                f"{column.name!r} has {column.text.case_variant_groups} value(s) that differ only "
                "by case or spacing, so some categories are split across two labels and their "
                "totals are understated."
            ))
            confidence *= 0.85

    # ---- structural facts about the whole dataset -------------------------- #
    if profile.duplicate_rows and {AGGREGATE, RANKING, DISTINCT_COUNT} & set(spec.intents):
        rate = profile.duplicate_rows / profile.row_count
        caveats.append(Caveat(
            "duplicates",
            f"{profile.duplicate_rows:,} rows ({rate:.1%}) are exact duplicates, so counts and "
            "totals here are inflated by an unknown amount."
        ))
        confidence *= (1 - min(0.4, rate * 2))

    if profile.truncated:
        caveats.append(Caveat(
            "truncated",
            f"Only the first {profile.row_count:,} rows were read; the source holds more. This "
            "answer is about that prefix, not the full dataset."
        ))
        confidence *= 0.6

    if DISTINCT_COUNT in spec.intents and not profile.key_candidates:
        caveats.append(Caveat(
            "no_key",
            "No column uniquely identifies a row, so a count of distinct entities cannot be "
            "separated from a count of rows."
        ))
        confidence *= 0.8

    if profile.row_count < 30:
        caveats.append(Caveat(
            "small_sample",
            f"The whole dataset is {profile.row_count} rows. At that size a single row moves any "
            "percentage by several points, so treat these as indicative, not measured."
        ))
        forbidden.append("Do not express results from this dataset as precise percentages.")
        confidence *= 0.6 if profile.row_count >= 10 else 0.35

    # ---- causal questions -------------------------------------------------- #
    if CAUSAL in spec.intents:
        forbidden.append(
            "Do not claim causation. This is observational data with no experiment, control "
            "group or treatment assignment, so it can show association only."
        )
        caveats.append(Caveat(
            "causation",
            "This data can show what moved together, not what caused what. Any explanation "
            "offered is a hypothesis to test, not a finding."
        ))
        confidence *= 0.55

    if spec.demands_precision and (profile.duplicate_rows or any(
            1 - c.completeness > 0.01 for c in relevant)):
        forbidden.append(
            "The question asks for an exact figure, but the data has gaps or duplicates. Give "
            "a range and name the uncertainty instead of a single number."
        )
        confidence *= 0.8

    # ---- overall data health ----------------------------------------------- #
    if profile.grade in ("D", "F"):
        caveats.append(Caveat(
            "low_grade",
            f"Overall this dataset scores {profile.score}/100 (grade {profile.grade}) — "
            f"{trust_level(profile.score)}."
        ))
        confidence *= 0.7

    # ---- verdict ----------------------------------------------------------- #
    confidence = max(CONFIDENCE_FLOOR, min(1.0, confidence))
    critical = [b for b in blockers if b.severity == Severity.CRITICAL]

    if critical or confidence < 0.3:
        verdict = Verdict.INSUFFICIENT
    elif blockers or caveats or confidence < 0.8:
        verdict = Verdict.PARTIAL
    else:
        verdict = Verdict.ANSWERABLE

    result.verdict = verdict
    result.confidence_ceiling = round(confidence, 3)
    result.blockers = blockers
    result.caveats = _dedupe(caveats)
    result.forbidden_claims = _dedupe(forbidden)
    result.unlock = _dedupe(unlock + [b.fix for b in blockers if b.fix])
    result.usable_rows = _usable_rows(profile, relevant)
    result.honest_response = _compose_response(result, profile)
    return result


def _dedupe(items):
    """Order-preserving dedupe for strings and for Caveats (by text)."""
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.text if isinstance(item, Caveat) else item
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _usable_rows(profile: DatasetProfile, relevant: list[ColumnProfile]) -> int:
    """Upper bound on rows that have every field the question needs."""
    if not relevant:
        return profile.row_count
    return max(0, min(c.non_null for c in relevant) - profile.duplicate_rows)


def _compose_response(result: Sufficiency, profile: DatasetProfile) -> str:
    """The sentences an agent should actually say. This is the product."""
    source = profile.source

    if result.verdict is Verdict.INSUFFICIENT:
        reasons = [b.message for b in result.blockers] or result.caveat_texts
        lines = [
            f"I can't answer that from {source}, and I don't want to give you a number that "
            "looks solid and isn't. The specific problem:",
            "",
        ]
        lines += [f"  - {reason}" for reason in reasons[:4]]
        if result.unlock:
            lines += ["", "What would make this answerable:"]
            lines += [f"  - {step}" for step in result.unlock[:4]]
        else:
            lines += ["", "What would make this answerable: repair the problems above at the "
                          "source, then ask again."]
        return "\n".join(lines)

    if result.verdict is Verdict.PARTIAL:
        lines = [
            f"I can give you a partial answer from {source}, but it has to come with these "
            "limits stated, not buried:",
            "",
        ]
        lines += [f"  - {caveat.text}" for caveat in result.caveats[:5]]
        if result.blockers:
            lines += ["", "And these parts of the question I genuinely cannot answer:"]
            lines += [f"  - {b.message}" for b in result.blockers[:3]]
        if result.forbidden_claims:
            lines += ["", "Claims to avoid:"]
            lines += [f"  - {claim}" for claim in result.forbidden_claims[:3]]
        return "\n".join(lines)

    return (
        f"The data in {source} supports this question: the fields it needs are present, "
        f"populated, and cover the period asked about ({result.usable_rows:,} usable rows). "
        "Answer normally."
    )


def assess_source(source, question: str, **kwargs) -> Sufficiency:
    """Convenience: profile a file and assess a question against it in one call."""
    from .profiler import profile as build_profile

    return assess(build_profile(source, **kwargs), question)
