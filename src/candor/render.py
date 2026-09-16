"""Human-readable output for the CLI.

Colour is opt-out (NO_COLOR, or a non-tty stdout). Everything here is
presentation only — no thresholds, no judgement.
"""

from __future__ import annotations

import os
import shutil
import sys

from .grading import trust_level
from .models import (
    ClaimReport,
    DatasetProfile,
    ImprovementPlan,
    Severity,
    Sufficiency,
    Verdict,
)

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

RESET = "\033[0m" if _COLOR else ""
BOLD = "\033[1m" if _COLOR else ""
DIM = "\033[2m" if _COLOR else ""
RED = "\033[31m" if _COLOR else ""
YELLOW = "\033[33m" if _COLOR else ""
GREEN = "\033[32m" if _COLOR else ""
BLUE = "\033[34m" if _COLOR else ""
MAGENTA = "\033[35m" if _COLOR else ""

SEVERITY_COLOR = {
    Severity.CRITICAL: RED,
    Severity.HIGH: RED,
    Severity.MEDIUM: YELLOW,
    Severity.LOW: DIM,
    Severity.INFO: DIM,
}

VERDICT_COLOR = {
    Verdict.ANSWERABLE: GREEN,
    Verdict.PARTIAL: YELLOW,
    Verdict.INSUFFICIENT: RED,
}


def width() -> int:
    return min(shutil.get_terminal_size((88, 24)).columns, 100)


def rule(title: str = "") -> str:
    total = width()
    if not title:
        return DIM + "-" * total + RESET
    return f"{DIM}-- {RESET}{BOLD}{title}{RESET} {DIM}{'-' * max(0, total - len(title) - 4)}{RESET}"


def wrap(text: str, indent: int = 4, first_prefix: str = "") -> str:
    import textwrap

    return textwrap.fill(
        text,
        width=width(),
        initial_indent=" " * indent + first_prefix,
        subsequent_indent=" " * (indent + len(first_prefix)),
    )


def bar(value: float, size: int = 20) -> str:
    filled = int(round(value / 100 * size))
    colour = GREEN if value >= 80 else YELLOW if value >= 60 else RED
    return f"{colour}{'#' * filled}{DIM}{'.' * (size - filled)}{RESET}"


# --------------------------------------------------------------------------- #


def render_profile(profile: DatasetProfile, *, verbose: bool = False) -> str:
    out: list[str] = []
    grade_colour = GREEN if profile.grade in "AB" else YELLOW if profile.grade == "C" else RED

    out.append("")
    out.append(rule("data quality"))
    out.append(f"  {BOLD}{profile.source}{RESET}  {DIM}({profile.format}){RESET}")
    out.append(f"  {profile.row_count:,} rows x {profile.column_count} columns"
               + (f"  {RED}[TRUNCATED]{RESET}" if profile.truncated else ""))
    out.append(f"  score {BOLD}{profile.score}{RESET}/100   grade {grade_colour}{BOLD}{profile.grade}{RESET}"
               f"   {DIM}{trust_level(profile.score)}{RESET}")
    out.append("")

    for dimension, score in sorted(profile.scores.items(), key=lambda kv: kv[1]):
        if score >= 100 and not verbose:
            continue
        out.append(f"    {dimension:<14} {bar(score)} {score:>5.1f}")
    out.append("")

    if profile.notes:
        out.append(rule("how it was read"))
        for note in profile.notes:
            out.append(wrap(note, first_prefix="- "))
        out.append("")

    out.append(rule("columns"))
    header = f"  {'column':<24} {'type':<10} {'fill':>6} {'distinct':>9}  grade"
    out.append(f"{DIM}{header}{RESET}")
    for col in profile.columns:
        fill = f"{col.completeness:.0%}"
        colour = GREEN if col.grade in "AB" else YELLOW if col.grade == "C" else RED
        semantic = f" {DIM}({col.semantic_type}){RESET}" if col.semantic_type else ""
        out.append(f"  {col.name[:24]:<24} {col.inferred_type:<10} {fill:>6} "
                   f"{col.distinct:>9,}  {colour}{col.grade}{RESET}{semantic}")
    out.append("")

    if profile.issues:
        out.append(rule(f"issues ({len(profile.issues)})"))
        for issue in profile.issues:
            colour = SEVERITY_COLOR[issue.severity]
            tag = f"{colour}{issue.severity.value.upper():<8}{RESET}"
            out.append(f"  {tag} {BOLD}{issue.message}{RESET}")
            if verbose:
                out.append(wrap(issue.impact, indent=13, first_prefix="why it matters: "))
            out.append(wrap(issue.fix, indent=13, first_prefix="fix: "))
            out.append("")
    else:
        out.append(f"  {GREEN}No quality issues found.{RESET}")
        out.append("")
    return "\n".join(out)


def render_sufficiency(result: Sufficiency, *, profile: DatasetProfile | None = None) -> str:
    out: list[str] = []
    colour = VERDICT_COLOR[result.verdict]

    out.append("")
    out.append(rule("sufficiency"))
    out.append(f"  {DIM}question:{RESET} {result.question}")
    out.append(f"  {DIM}source:  {RESET} {result.source}")
    out.append("")
    out.append(f"  verdict: {colour}{BOLD}{result.verdict.value.upper()}{RESET}"
               f"    confidence ceiling: {BOLD}{result.confidence_ceiling:.0%}{RESET}"
               + (f"    usable rows: {result.usable_rows:,}" if result.usable_rows is not None else ""))
    out.append(f"  {DIM}read as: {', '.join(result.intents)}{RESET}")
    if result.resolved:
        mapped = ", ".join(f"{k} -> {v}" for k, v in list(result.resolved.items())[:6])
        out.append(f"  {DIM}mapped:  {mapped}{RESET}")
    out.append("")

    if result.blockers:
        out.append(rule("cannot answer"))
        for gap in result.blockers:
            gap_colour = SEVERITY_COLOR[gap.severity]
            out.append(f"  {gap_colour}x{RESET} {gap.message}")
            if gap.fix:
                out.append(wrap(gap.fix, indent=6, first_prefix="-> "))
        out.append("")

    if result.caveats:
        out.append(rule("must be stated in the answer"))
        for caveat in result.caveats:
            out.append(wrap(caveat.text, indent=2, first_prefix=f"{YELLOW}!{RESET} "))
        out.append("")

    if result.forbidden_claims:
        out.append(rule("must not be claimed"))
        for claim in result.forbidden_claims:
            out.append(wrap(claim, indent=2, first_prefix=f"{RED}x{RESET} "))
        out.append("")

    if result.unlock:
        out.append(rule("what would unlock this question"))
        for step in result.unlock:
            out.append(wrap(step, indent=2, first_prefix=f"{BLUE}+{RESET} "))
        out.append("")

    out.append(rule("honest response"))
    out.append("")
    for line in result.honest_response.splitlines():
        out.append(f"  {MAGENTA}|{RESET} {line}")
    out.append("")
    return "\n".join(out)


def render_claims(report: ClaimReport) -> str:
    out: list[str] = []
    colour = {"pass": GREEN, "revise": YELLOW, "reject": RED}[report.verdict]

    out.append("")
    out.append(rule("answer verification"))
    out.append(f"  verdict: {colour}{BOLD}{report.verdict.upper()}{RESET}"
               f"    honesty score: {BOLD}{report.honesty_score}{RESET}/100"
               f"    {DIM}{report.checked_claims} numeric claim(s) checked{RESET}")
    out.append("")

    if not report.findings:
        out.append(f"  {GREEN}Nothing in the answer goes beyond what the data supports.{RESET}")
        out.append("")
        return "\n".join(out)

    for finding in report.findings:
        severity_colour = SEVERITY_COLOR[finding.severity]
        out.append(f"  {severity_colour}{finding.severity.value.upper():<8}{RESET} "
                   f"{BOLD}{finding.message}{RESET}")
        if finding.quote:
            out.append(wrap(f'"{finding.quote}"', indent=13))
        if finding.suggestion:
            out.append(wrap(finding.suggestion, indent=13, first_prefix="-> "))
        out.append("")
    return "\n".join(out)


def render_plan(result: ImprovementPlan) -> str:
    out: list[str] = []
    out.append("")
    out.append(rule("improvement plan"))
    out.append(f"  {result.source}")
    out.append(f"  now: {BOLD}{result.current_score}{RESET}/100 ({result.current_grade})"
               f"   after the critical and high fixes: {GREEN}{BOLD}{result.projected_score}{RESET}/100")
    out.append("")

    if not result.steps:
        out.append(f"  {GREEN}Nothing to fix.{RESET}")
        out.append("")
        return "\n".join(out)

    for step in result.steps:
        colour = SEVERITY_COLOR[step.severity]
        out.append(f"  {BOLD}{step.rank}.{RESET} {colour}[{step.severity.value}]{RESET} "
                   f"{BOLD}{step.title}{RESET}  {DIM}effort {step.effort.value} / "
                   f"value {step.value}{RESET}")
        out.append(wrap(step.fix, indent=6))
        for question in step.unlocks:
            out.append(wrap(f"unblocks: {question}", indent=6, first_prefix=f"{GREEN}+{RESET} "))
        out.append("")
    return "\n".join(out)
