"""Core data model.

Everything candor produces is a plain dataclass that round-trips to JSON, so a
harness can hand the output straight to a model or store it in CI.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum, StrEnum
from typing import Any

# --------------------------------------------------------------------------- #
# enums


class Dimension(StrEnum):
    """The quality dimensions candor scores independently."""

    COMPLETENESS = "completeness"
    VALIDITY = "validity"
    CONSISTENCY = "consistency"
    UNIQUENESS = "uniqueness"
    TIMELINESS = "timeliness"
    ACCURACY = "accuracy"
    COVERAGE = "coverage"
    SCALE = "scale"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def weight(self) -> float:
        return {"critical": 1.0, "high": 0.6, "medium": 0.3, "low": 0.12, "info": 0.0}[self.value]

    @property
    def rank(self) -> int:
        return ["info", "low", "medium", "high", "critical"].index(self.value)


class Verdict(StrEnum):
    """Can the agent answer the question from this data?"""

    ANSWERABLE = "answerable"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"

    @property
    def rank(self) -> int:
        return ["insufficient", "partial", "answerable"].index(self.value)


class Effort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def cost(self) -> float:
        return {"low": 1.0, "medium": 2.5, "high": 5.0}[self.value]


# --------------------------------------------------------------------------- #
# profiling


@dataclass
class NumericStats:
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    stdev: float | None = None
    p05: float | None = None
    p25: float | None = None
    p75: float | None = None
    p95: float | None = None
    zeros: int = 0
    negatives: int = 0
    outlier_count: int = 0
    outlier_examples: list[float] = field(default_factory=list)
    integral: bool = False


@dataclass
class TemporalStats:
    min: str | None = None
    max: str | None = None
    span_days: int | None = None
    distinct_periods: int = 0
    granularity: str | None = None  # second | day | month | year — as written
    cadence: str | None = None      # day | month | year — as actually recorded
    future_count: int = 0
    staleness_days: int | None = None
    gap_count: int = 0
    gap_periods: list[str] = field(default_factory=list)
    month_counts: dict[str, int] = field(default_factory=dict)
    trailing_period_partial: bool = False


@dataclass
class TextStats:
    min_length: int = 0
    max_length: int = 0
    mean_length: float = 0.0
    untrimmed: int = 0
    case_variant_groups: int = 0
    mojibake: int = 0
    control_chars: int = 0


@dataclass
class ColumnProfile:
    name: str
    index: int
    inferred_type: str = "string"
    semantic_type: str | None = None
    count: int = 0
    non_null: int = 0
    nulls: int = 0
    blanks: int = 0
    placeholders: int = 0
    placeholder_tokens: list[str] = field(default_factory=list)
    distinct: int = 0
    unique_ratio: float = 0.0
    top_values: list[list[Any]] = field(default_factory=list)
    value_set: list[str] = field(default_factory=list)
    type_mismatches: int = 0
    format_variants: dict[str, int] = field(default_factory=dict)
    numeric: NumericStats | None = None
    temporal: TemporalStats | None = None
    text: TextStats | None = None
    scores: dict[str, float] = field(default_factory=dict)
    score: float = 100.0
    grade: str = "A"

    @property
    def completeness(self) -> float:
        return 0.0 if self.count == 0 else self.non_null / self.count


@dataclass
class Issue:
    """One concrete, fixable defect in the data."""

    code: str
    dimension: Dimension
    severity: Severity
    message: str
    column: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    impact: str = ""
    fix: str = ""
    effort: Effort = Effort.MEDIUM
    blocks: list[str] = field(default_factory=list)


@dataclass
class DatasetProfile:
    source: str
    format: str
    row_count: int = 0
    column_count: int = 0
    truncated: bool = False
    columns: list[ColumnProfile] = field(default_factory=list)
    duplicate_rows: int = 0
    key_candidates: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    score: float = 100.0
    grade: str = "A"
    generated_at: str = ""
    notes: list[str] = field(default_factory=list)

    def column(self, name: str) -> ColumnProfile | None:
        lowered = name.strip().lower()
        for col in self.columns:
            if col.name.strip().lower() == lowered:
                return col
        return None

    @property
    def temporal_columns(self) -> list[ColumnProfile]:
        return [c for c in self.columns if c.inferred_type in ("date", "datetime")]

    @property
    def numeric_columns(self) -> list[ColumnProfile]:
        return [c for c in self.columns if c.inferred_type in ("integer", "number")]

    @property
    def categorical_columns(self) -> list[ColumnProfile]:
        return [c for c in self.columns if c.inferred_type in ("string", "boolean")]


# --------------------------------------------------------------------------- #
# sufficiency


@dataclass
class Gap:
    """Something missing between the question and the data."""

    code: str
    severity: Severity
    message: str
    need: str = ""
    fix: str = ""
    columns: list[str] = field(default_factory=list)


@dataclass
class Caveat:
    """A limit the answer has to state. `topic` lets verify() check whether the
    draft actually addressed it, rather than grepping for shared words."""

    topic: str
    text: str

    def __str__(self) -> str:
        return self.text


@dataclass
class Sufficiency:
    question: str
    source: str
    verdict: Verdict = Verdict.ANSWERABLE
    confidence_ceiling: float = 1.0
    intents: list[str] = field(default_factory=list)
    resolved: dict[str, str] = field(default_factory=dict)
    unresolved_terms: list[str] = field(default_factory=list)
    blockers: list[Gap] = field(default_factory=list)
    caveats: list[Caveat] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    unlock: list[str] = field(default_factory=list)
    honest_response: str = ""
    usable_rows: int | None = None

    @property
    def caveat_texts(self) -> list[str]:
        return [c.text for c in self.caveats]


# --------------------------------------------------------------------------- #
# verification


@dataclass
class Finding:
    """A claim in a draft answer that the data does not back."""

    code: str
    severity: Severity
    message: str
    quote: str = ""
    suggestion: str = ""


@dataclass
class ClaimReport:
    source: str
    findings: list[Finding] = field(default_factory=list)
    honesty_score: float = 100.0
    verdict: str = "pass"  # pass | revise | reject
    checked_claims: int = 0


# --------------------------------------------------------------------------- #
# improvement


@dataclass
class Remediation:
    rank: int
    title: str
    fix: str
    columns: list[str] = field(default_factory=list)
    dimension: Dimension = Dimension.COMPLETENESS
    severity: Severity = Severity.MEDIUM
    effort: Effort = Effort.MEDIUM
    value: float = 0.0
    unlocks: list[str] = field(default_factory=list)
    issue_codes: list[str] = field(default_factory=list)


@dataclass
class ImprovementPlan:
    source: str
    current_score: float
    current_grade: str
    projected_score: float
    steps: list[Remediation] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# serialisation


def _encode(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float):
        return round(obj, 6)
    raise TypeError(f"not JSON serialisable: {type(obj)!r}")


def to_dict(obj: Any) -> Any:
    """Dataclass -> plain dict with enums flattened to their values."""
    return json.loads(to_json(obj))


def to_json(obj: Any, indent: int | None = 2) -> str:
    payload = asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj
    return json.dumps(payload, default=_encode, indent=indent, ensure_ascii=False)
