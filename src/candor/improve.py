"""What to fix first.

Ranking is value-over-effort, where value is driven by severity, how much of
the data the defect touches, and — when questions are supplied — how many real
questions the fix unblocks. A plan tied to actual questions beats a plan tied
to abstract quality every time, so pass questions whenever you have them.
"""

from __future__ import annotations

from collections import defaultdict

from .assess import assess
from .grading import grade_for
from .models import (
    DatasetProfile,
    Dimension,
    Effort,
    ImprovementPlan,
    Issue,
    Remediation,
    Severity,
    Verdict,
)

SEVERITY_VALUE = {
    Severity.CRITICAL: 40.0,
    Severity.HIGH: 22.0,
    Severity.MEDIUM: 10.0,
    Severity.LOW: 3.0,
    Severity.INFO: 0.0,
}


def _reach(issue: Issue, row_count: int) -> float:
    """0..1 — how much of the dataset this defect touches."""
    rate = issue.evidence.get("rate")
    if isinstance(rate, (int, float)):
        return min(1.0, float(rate))
    count = (issue.evidence.get("count") or issue.evidence.get("nulls")
             or issue.evidence.get("duplicates") or issue.evidence.get("mismatches"))
    if isinstance(count, (int, float)) and row_count:
        return min(1.0, float(count) / row_count)
    return 1.0 if issue.column is None else 0.6


def _title(issue: Issue) -> str:
    where = f"{issue.column}: " if issue.column else ""
    return f"{where}{issue.code.replace('_', ' ')}"


def plan(profile: DatasetProfile, questions: list[str] | None = None) -> ImprovementPlan:
    """Rank the fixes for a dataset, optionally against the questions it must answer."""
    questions = questions or []

    # Which questions are currently not fully answerable, and why.
    blocked_by_code: dict[str, list[str]] = defaultdict(list)
    for question in questions:
        result = assess(profile, question)
        if result.verdict is Verdict.ANSWERABLE:
            continue
        codes = {b.code for b in result.blockers}
        # Map sufficiency gaps back onto the issues that cause them.
        for issue in profile.issues:
            if issue.column and any(issue.column in b.columns for b in result.blockers):
                blocked_by_code[issue.code].append(question)
            elif issue.code in codes:
                blocked_by_code[issue.code].append(question)
            elif issue.blocks and set(issue.blocks) & set(result.intents):
                blocked_by_code[issue.code].append(question)

    steps: list[Remediation] = []
    for issue in profile.issues:
        unlocks = sorted(set(blocked_by_code.get(issue.code, [])))
        value = SEVERITY_VALUE[issue.severity] * (0.4 + 0.6 * _reach(issue, profile.row_count))
        value += len(issue.blocks) * 2.0
        value += len(unlocks) * 15.0
        steps.append(Remediation(
            rank=0,
            title=_title(issue),
            fix=issue.fix,
            columns=[issue.column] if issue.column else [],
            dimension=issue.dimension,
            severity=issue.severity,
            effort=issue.effort,
            value=round(value / issue.effort.cost, 2),
            unlocks=unlocks,
            issue_codes=[issue.code],
        ))

    steps.sort(key=lambda s: (-s.value, -s.severity.rank, s.title))
    for index, step in enumerate(steps, start=1):
        step.rank = index

    projected = _projected_score(profile, steps)
    return ImprovementPlan(
        source=profile.source,
        current_score=profile.score,
        current_grade=profile.grade,
        projected_score=projected,
        steps=steps,
    )


def _projected_score(profile: DatasetProfile, steps: list[Remediation]) -> float:
    """Score if the top fixes (everything critical and high) were done."""
    from copy import deepcopy

    from .grading import score_profile

    survivors = [
        i for i in profile.issues
        if i.severity not in (Severity.CRITICAL, Severity.HIGH)
    ]
    shadow = deepcopy(profile)
    shadow.issues = survivors
    score_profile(shadow)
    return shadow.score


def quick_wins(plan_result: ImprovementPlan, limit: int = 3) -> list[Remediation]:
    """Low-effort, high-value fixes — the ones worth doing this afternoon."""
    return [s for s in plan_result.steps if s.effort is Effort.LOW][:limit]


__all__ = ["plan", "quick_wins", "Dimension", "grade_for"]
