"""Scoring.

A single number is a lie by itself, so candor always reports the per-dimension
scores next to the headline. The headline is the weighted mean, with a floor
applied by the worst critical issue — a dataset with ambiguous dates cannot be
a B because everything else is tidy.
"""

from __future__ import annotations

from .models import ColumnProfile, DatasetProfile, Dimension, Issue, Severity

DIMENSION_WEIGHTS: dict[Dimension, float] = {
    Dimension.COMPLETENESS: 1.0,
    Dimension.VALIDITY: 1.0,
    Dimension.CONSISTENCY: 0.8,
    Dimension.UNIQUENESS: 0.8,
    Dimension.ACCURACY: 0.8,
    Dimension.COVERAGE: 0.9,
    Dimension.TIMELINESS: 0.6,
    Dimension.SCALE: 0.5,
}

# A critical defect caps the whole dataset, whatever else is true of it.
SEVERITY_CAP = {Severity.CRITICAL: 55.0, Severity.HIGH: 78.0}

GRADE_BANDS = [(90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F")]


def grade_for(score: float) -> str:
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


def _penalty(issue: Issue, row_count: int) -> float:
    """Points removed from a dimension for one issue, scaled by how much of the
    data it touches."""
    base = {
        Severity.CRITICAL: 45.0,
        Severity.HIGH: 25.0,
        Severity.MEDIUM: 12.0,
        Severity.LOW: 4.0,
        Severity.INFO: 0.0,
    }[issue.severity]

    rate = issue.evidence.get("rate")
    if rate is None:
        count = issue.evidence.get("count") or issue.evidence.get("duplicates") or issue.evidence.get("nulls")
        if isinstance(count, (int, float)) and row_count:
            rate = min(1.0, count / row_count)
    if isinstance(rate, (int, float)):
        # Even a 0.1% defect is worth something: floor the scaling at a third.
        base *= max(0.33, min(1.0, float(rate) ** 0.5))
    return base


def score_profile(profile: DatasetProfile) -> DatasetProfile:
    """Attach dimension scores, an overall score and a letter grade. In place."""
    scores = {dim.value: 100.0 for dim in Dimension}

    for issue in profile.issues:
        key = issue.dimension.value
        scores[key] = max(0.0, scores[key] - _penalty(issue, profile.row_count))

    weighted = sum(scores[d.value] * w for d, w in DIMENSION_WEIGHTS.items())
    total_weight = sum(DIMENSION_WEIGHTS.values())
    overall = weighted / total_weight if total_weight else 100.0

    # Severity is a str Enum, so max() would compare alphabetically. Rank it.
    worst = max((i.severity for i in profile.issues),
                key=lambda sev: sev.rank, default=Severity.INFO)
    if worst in SEVERITY_CAP:
        overall = min(overall, SEVERITY_CAP[worst])

    profile.scores = {k: round(v, 1) for k, v in scores.items()}
    profile.score = round(overall, 1)
    profile.grade = grade_for(profile.score)

    for col in profile.columns:
        _score_column(col, profile)
    return profile


def _score_column(col: ColumnProfile, profile: DatasetProfile) -> None:
    scores = {dim.value: 100.0 for dim in Dimension}
    relevant = [i for i in profile.issues if i.column == col.name]
    for issue in relevant:
        key = issue.dimension.value
        scores[key] = max(0.0, scores[key] - _penalty(issue, profile.row_count))

    weighted = sum(scores[d.value] * w for d, w in DIMENSION_WEIGHTS.items())
    overall = weighted / sum(DIMENSION_WEIGHTS.values())
    worst = max((i.severity for i in relevant),
                key=lambda sev: sev.rank, default=Severity.INFO)
    if worst in SEVERITY_CAP:
        overall = min(overall, SEVERITY_CAP[worst])

    col.scores = {k: round(v, 1) for k, v in scores.items() if v < 100.0}
    col.score = round(overall, 1)
    col.grade = grade_for(col.score)


def trust_level(score: float) -> str:
    """Plain-language reading of a score, for an agent to put in prose."""
    if score >= 90:
        return "solid enough to quote directly"
    if score >= 80:
        return "usable, with the caveats below stated explicitly"
    if score >= 70:
        return "usable only for direction, not for precise figures"
    if score >= 60:
        return "too damaged to quote numbers from without repair"
    return "not fit to answer from"
