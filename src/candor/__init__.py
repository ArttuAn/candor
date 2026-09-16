"""candor — a sufficiency gate for AI agents.

Two gates, one habit: never invent what the human should decide.

Data gate — can this question be answered from this data?

    import candor

    p = candor.profile("sales.csv")                      # what is in the data
    s = candor.assess(p, "why did revenue drop in Q3?")  # can it answer this?
    r = candor.verify(p, draft_answer, sufficiency=s)    # does the answer hold up?

Build gate — can this app be built from this logic, without guessing?

    b = candor.assess_spec("spec.md")
    if b.verdict is not candor.BuildVerdict.BUILDABLE:
        ask_the_user(b.gaps)   # each gap carries the question

`s.honest_response` and `b.honest_response` are the point of the library: when
the data cannot support the question — or the logic cannot support the build —
they contain the words to say instead of a confident guess.
"""

from __future__ import annotations

from .assess import assess, assess_source
from .grading import grade_for, trust_level
from .improve import plan, quick_wins
from .models import (
    BuildVerdict,
    Caveat,
    ClaimReport,
    ColumnProfile,
    DatasetProfile,
    Dimension,
    Effort,
    Finding,
    Gap,
    ImprovementPlan,
    Issue,
    LogicGap,
    Remediation,
    Severity,
    SpecAssessment,
    Sufficiency,
    Verdict,
    to_dict,
    to_json,
)
from .profiler import profile, profile_table
from .question import QuestionSpec
from .question import parse as parse_question
from .sources import SourceError, Table, load
from .spec import assess_spec, resolve_spec, spec_kit
from .verify import verify

__version__ = "0.2.0"

__all__ = [
    "__version__",
    # data pipeline
    "profile",
    "profile_table",
    "assess",
    "assess_source",
    "verify",
    "plan",
    "quick_wins",
    "truth_kit",
    # build pipeline
    "assess_spec",
    "resolve_spec",
    "spec_kit",
    # inputs
    "load",
    "Table",
    "SourceError",
    "parse_question",
    "QuestionSpec",
    # models
    "DatasetProfile",
    "ColumnProfile",
    "Issue",
    "Sufficiency",
    "Caveat",
    "Gap",
    "ClaimReport",
    "Finding",
    "ImprovementPlan",
    "Remediation",
    "Dimension",
    "Severity",
    "Verdict",
    "BuildVerdict",
    "SpecAssessment",
    "LogicGap",
    "Effort",
    # helpers
    "grade_for",
    "trust_level",
    "to_dict",
    "to_json",
]


def truth_kit(source, question: str, *, max_rows: int = 200_000,
              table: str | None = None) -> dict:
    """Everything an agent needs to answer honestly, as one compact dict.

    Designed to be injected into a system prompt or tool result verbatim. It is
    intentionally small: a verdict, a confidence ceiling, the sentences that
    must be said, and the claims that must not be made.
    """
    data_profile = profile(source, max_rows=max_rows, table=table)
    sufficiency = assess(data_profile, question)
    return {
        "source": data_profile.source,
        "rows": data_profile.row_count,
        "columns": [c.name for c in data_profile.columns],
        "data_grade": data_profile.grade,
        "data_score": data_profile.score,
        "verdict": sufficiency.verdict.value,
        "confidence_ceiling": sufficiency.confidence_ceiling,
        "usable_rows": sufficiency.usable_rows,
        "must_say": sufficiency.caveat_texts,
        "must_not_claim": sufficiency.forbidden_claims,
        "cannot_answer": [b.message for b in sufficiency.blockers],
        "to_make_answerable": sufficiency.unlock,
        "honest_response": sufficiency.honest_response,
        "instruction": (
            "Do not exceed the confidence ceiling. State every line of must_say in your own "
            "words inside the answer itself, not as a footnote. Make none of the claims in "
            "must_not_claim. If verdict is 'insufficient', reply with honest_response instead "
            "of an answer."
        ),
    }
