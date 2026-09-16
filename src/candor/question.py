"""Reading a question well enough to know what the data would have to contain.

No model call: this is deliberately a lexical parser. A harness that wants
smarter intent detection can build a QuestionSpec itself and pass it to
assess(), but the default must work offline and identically every run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .issues import (
    AGGREGATE,
    CAUSAL,
    COMPARISON,
    DISTINCT_COUNT,
    FORECAST,
    LOOKUP,
    RANKING,
    TREND,
)

INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (CAUSAL, re.compile(r"\b(why|because|cause[ds]?|causing|drive[sn]?|driver|"
                        r"impact of|effect of|affect(s|ed)?|due to|lead(s|ing)? to|"
                        r"responsible for|explain(s|ed)? (?:the|why))\b", re.I)),
    (FORECAST, re.compile(r"\b(forecast|predict|projection|project(ed|ing)?|will\b|"
                          r"expect(ed)?|next (?:month|quarter|year|week)|going to|"
                          r"outlook|extrapolat)\w*\b", re.I)),
    (TREND, re.compile(r"\b(trend|over time|month[- ]?over[- ]?month|year[- ]?over[- ]?year|"
                       r"yoy|mom|growth|grew|growing|increase[sd]?|decrease[sd]?|decline[sd]?|"
                       r"chang(?:e|ed|ing)|since|trajector|seasonal|by (?:month|quarter|year|week|day))\b", re.I)),
    (COMPARISON, re.compile(r"\b(compare[sd]?|comparison|versus|vs\.?|between|difference|"
                            r"differ(s|ent)?|breakdown|break down|by (?:region|segment|category|"
                            r"country|product|channel|team|group)|split by|per\b|across)\b", re.I)),
    (RANKING, re.compile(r"\b(top|bottom|best|worst|highest|lowest|most|least|rank(ed|ing)?|"
                         r"leading|biggest|smallest|largest)\b", re.I)),
    (DISTINCT_COUNT, re.compile(r"\b(how many (?:unique|distinct|different)|number of (?:unique|distinct)|"
                                r"unique|distinct)\b", re.I)),
    (AGGREGATE, re.compile(r"\b(total|sum|average|avg|mean|median|count|how many|how much|"
                           r"percentage|percent|share|rate|ratio|proportion)\b", re.I)),
    (LOOKUP, re.compile(r"\b(what is|which|who|where|list|show me|find|lookup|look up|"
                        r"give me|display)\b", re.I)),
]

# Words that carry no information about which field is needed.
STOPWORDS = {
    # articles, pronouns, glue
    "a", "an", "and", "any", "are", "as", "at", "be", "been", "being", "both", "but", "by",
    "can", "could", "did", "do", "does", "each", "for", "from", "had", "has", "have", "in",
    "into", "is", "it", "its", "just", "may", "me", "might", "my", "no", "not", "of", "on",
    "once", "one", "only", "or", "our", "out", "over", "same", "should", "so", "some", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this", "those",
    "through", "to", "under", "up", "us", "was", "we", "well", "were", "what", "when",
    "where", "which", "while", "who", "why", "will", "with", "would", "you", "your", "given",
    "across", "about", "after", "before", "between", "all", "please",
    # aggregation vocabulary — these describe the operation, not a field
    "average", "avg", "count", "mean", "median", "number", "percent", "percentage", "rate",
    "ratio", "share", "sum", "total", "proportion", "amount",
    # question scaffolding
    "compare", "compared", "comparison", "breakdown", "explain", "find", "get", "give",
    "list", "look", "lookup", "show", "see", "tell", "know", "think", "want", "use", "used",
    "make", "made", "display", "summary", "summarise", "summarize", "analysis", "analyse",
    "analyze", "report", "data", "dataset", "figure", "figures", "numbers", "result",
    "results", "insight", "insights", "overview", "split", "rank", "vs",
    # movement and comparison verbs — intent, not a column
    "drop", "drops", "dropped", "decline", "declined", "decrease", "decreased", "fall",
    "fell", "falling", "rise", "rose", "rising", "grow", "grew", "growing", "growth",
    "increase", "increased", "spike", "spiked", "jump", "jumped", "change", "changed",
    "changing", "trend", "trending", "improve", "improved", "better", "worse", "best",
    "worst", "high", "higher", "highest", "low", "lower", "lowest", "big", "bigger",
    "biggest", "small", "smaller", "smallest", "large", "largest", "least", "most", "top",
    "bottom", "much", "many", "how", "significant", "significantly", "really", "lot", "lots",
    # causal vocabulary — captured as intent, not as a required field
    "cause", "causes", "caused", "reason", "reasons", "impact", "effect", "effects",
    "affect", "affects", "driver", "drivers", "difference", "differences", "because",
    "explanation", "responsible", "happen", "happened", "happening", "going",
    # time words — handled by time_refs, not by column matching
    "time", "times", "period", "periods", "date", "dates", "day", "days", "week", "weeks",
    "month", "months", "quarter", "quarters", "year", "years", "ago", "next", "last",
    "previous", "prior", "current", "recent", "recently", "since", "now", "today",
    "yesterday", "ytd", "q1", "q2", "q3", "q4", "per", "every",
}


MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_QUARTER_RE = re.compile(r"\bq([1-4])\s*(?:of\s*)?(\d{4})?\b", re.I)
_MONTH_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s*(\d{4})?\b", re.I)
_RELATIVE_RE = re.compile(
    r"\blast\s+(\d+)?\s*(day|week|month|quarter|year)s?\b|\b(ytd|year to date|"
    r"this (?:month|quarter|year)|last (?:month|quarter|year)|today|yesterday)\b", re.I)
_QUOTED_RE = re.compile(r"['\"]([^'\"]{2,60})['\"]")
_PRECISION_RE = re.compile(r"\b(exact(ly)?|precise(ly)?|to the (?:cent|dollar|euro)|"
                           r"specific number)\b", re.I)
_GROUPBY_RE = re.compile(r"\b(?:by|per|across|for each|grouped by|broken down by)\s+"
                         r"([a-z][a-z0-9_ ]{1,30})", re.I)


@dataclass
class TimeRef:
    kind: str  # year | quarter | month | relative
    label: str
    start: str | None = None
    end: str | None = None


@dataclass
class QuestionSpec:
    text: str
    intents: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    time_refs: list[TimeRef] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    literals: list[str] = field(default_factory=list)
    demands_precision: bool = False

    @property
    def primary_intent(self) -> str:
        return self.intents[0] if self.intents else LOOKUP


def _quarter_bounds(quarter: int, year: int) -> tuple[str, str]:
    start_month = 3 * (quarter - 1) + 1
    end_month = start_month + 2
    last_day = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][end_month - 1]
    if end_month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        last_day = 29
    return f"{year:04d}-{start_month:02d}-01", f"{year:04d}-{end_month:02d}-{last_day:02d}"


def _extract_time_refs(text: str, now: datetime) -> list[TimeRef]:
    refs: list[TimeRef] = []
    consumed_years: set[str] = set()

    for match in _QUARTER_RE.finditer(text):
        quarter = int(match.group(1))
        year = int(match.group(2)) if match.group(2) else now.year
        start, end = _quarter_bounds(quarter, year)
        refs.append(TimeRef("quarter", f"Q{quarter} {year}", start, end))
        consumed_years.add(str(year))

    for match in _MONTH_RE.finditer(text):
        name = match.group(1).lower()
        month = next((v for k, v in MONTHS.items() if k.startswith(name[:3])), None)
        if month is None:
            continue
        # A bare "may" is nearly always the verb, not the month.
        if name == "may" and not match.group(2):
            continue
        year = int(match.group(2)) if match.group(2) else now.year
        last_day = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
        if month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
            last_day = 29
        refs.append(TimeRef("month", f"{match.group(1).title()} {year}",
                            f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"))
        consumed_years.add(str(year))

    for match in _YEAR_RE.finditer(text):
        year = match.group(1)
        if year in consumed_years:
            continue
        refs.append(TimeRef("year", year, f"{year}-01-01", f"{year}-12-31"))

    for match in _RELATIVE_RE.finditer(text):
        refs.append(TimeRef("relative", match.group(0).strip().lower()))

    return refs


def _tokenise(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    terms: list[str] = []
    for word in words:
        if word in STOPWORDS or len(word) < 3 or word.isdigit():
            continue
        if word not in terms:
            terms.append(word)
    # Adjacent survivors often name one field ("customer churn", "order value").
    for a, b in zip(words, words[1:], strict=False):
        if a in STOPWORDS or b in STOPWORDS or len(a) < 3 or len(b) < 3:
            continue
        pair = f"{a} {b}"
        if pair not in terms:
            terms.append(pair)
    return terms


def _group_name(captured: str) -> str | None:
    """Trim a captured group-by phrase back to the field it actually names.

    "by region in 2024" captures "region in 2024"; only the leading run of
    content words is the field.
    """
    words: list[str] = []
    for word in captured.strip().rstrip("?.,").lower().split():
        if word in STOPWORDS or word.isdigit():
            break
        words.append(word)
    return " ".join(words) if words else None


def parse(text: str, *, now: datetime | None = None) -> QuestionSpec:
    """Parse a natural-language question into the requirements it implies."""
    now = now or datetime.now()
    spec = QuestionSpec(text=text.strip())

    for intent, pattern in INTENT_PATTERNS:
        if pattern.search(text):
            spec.intents.append(intent)
    if not spec.intents:
        spec.intents.append(LOOKUP)

    spec.literals = [m.group(1) for m in _QUOTED_RE.finditer(text)]
    spec.time_refs = _extract_time_refs(text, now)
    spec.demands_precision = bool(_PRECISION_RE.search(text))
    spec.group_by = [g for g in (_group_name(m.group(1)) for m in _GROUPBY_RE.finditer(text)) if g]
    spec.terms = _tokenise(text)
    return spec
