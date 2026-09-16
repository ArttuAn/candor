"""Check a draft answer against the data before it reaches the user.

This is the last gate. assess() runs before the agent writes; verify() runs
after, and catches the two failures assess() cannot prevent: claims that go
beyond what the data holds, and required caveats that got dropped on the way
to prose.
"""

from __future__ import annotations

import re

from .models import ClaimReport, DatasetProfile, Finding, Severity, Sufficiency, Verdict

# A number with optional thousands separators, decimals, percent or currency.
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<cur>[$€£¥])?\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<suffix>%|percent|k\b|m\b|bn?\b|million|billion|thousand)?",
    re.I,
)

# "at all" and "not all" are idioms; a bare "all" only quantifies when a noun
# follows it. Getting this wrong punishes the honest answers hardest, because
# hedged prose is full of "no rows at all" and "not all the rows".
ABSOLUTE_RE = re.compile(
    r"(?<!\bat )(?<!\bnot )\b(?:all(?=\s+(?:of\s+)?(?:the\s+|these\s+|those\s+)?[a-z]{3,})|"
    r"every(?!\s+(?:case|time)\b)|each and every|none of the|no one|nobody|"
    r"never|always|without exception|in every case|universally|100% of)\b", re.I)

CAUSAL_RE = re.compile(
    r"\b(caused? by|causes|causing|because of|due to|drives|driven by|"
    r"led to|leads to|resulted in|results in|responsible for|"
    r"as a result of|the reason (?:is|was|for)|explains? (?:the|why)|"
    r"attributable to|thanks to)\b", re.I)

CERTAINTY_RE = re.compile(
    r"\b(definitely|certainly|clearly|obviously|undoubtedly|without (?:a )?doubt|"
    r"proves?|proven|confirms?|guarantee[sd]?|there is no question|"
    r"the data shows conclusively)\b", re.I)

HEDGE_RE = re.compile(
    r"\b(approximately|roughly|about|around|appears?|seems?|suggests?|indicates?|"
    r"may|might|could|likely|unlikely|estimate[sd]?|uncertain|unclear|caveat|"
    r"limitation|incomplete|missing|cannot|can't|unable|insufficient|"
    r"not enough|only (?:covers|includes)|based on the \d+)\b", re.I)

FORECAST_RE = re.compile(
    r"\b(will (?:be|reach|grow|rise|fall|increase|decrease|hit|recover|climb|drop|"r"improve|decline|continue|remain|stay|exceed|return|double|halve)|"
    r"is going to|expect(?:ed)? to (?:reach|be|grow)|by (?:next|the end of)|"
    r"projected? to|forecast(?:ed)? (?:at|to))\b", re.I)

SUPERLATIVE_RE = re.compile(
    r"\bthe (?:highest|lowest|best|worst|largest|smallest|top|biggest|"
    r"most(?!\s+(?:recent|recently|common|of the))|least(?!\s+of))\b", re.I)

_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


# What it looks like when an answer has genuinely addressed a caveat. Keyed by
# the caveat's topic, so the check tests whether the *subject* was raised —
# not whether the draft happens to reuse the same words.
DISCLOSURE_PATTERNS: dict[str, str] = {
    "missing_period": r"no (?:rows|data|records)|missing data|gap in the data|not loaded|"
                      r"empty month|nothing (?:recorded|logged)|absent",
    "time_gaps": r"gap|no (?:rows|data|records)|missing data|not loaded|discontinu|"
                 r"period[s]? with no",
    "thin_period": r"fewer (?:rows|records)|sparse|thin|under-?loaded|less data|"
                   r"only \d+ (?:rows|records)",
    "partial_period": r"partial|incomplete|only part|covers only|does not cover the (?:whole|full)",
    "partial_trailing": r"incomplete|still (?:filling|being)|partial|not (?:yet )?(?:final|complete)|"
                        r"last (?:month|period|quarter) is",
    "staleness": r"as of|most recent|up to \d{4}|\d{4}-\d{2}-\d{2}|out of date|stale|"
                 r"not current|days? (?:old|ago)|no longer|at the time",
    "nulls": r"missing|not recorded|blank|null|incomplete|only the rows|where .* is recorded|"
             r"unrecorded|absent",
    "placeholders": r"placeholder|unknown|unspecified|not a real value|dummy|filler",
    "outliers": r"outlier|extreme|skew|median|not representative|a few very large",
    "case_variants": r"duplicate label|same (?:value|category) twice|spelling|case|"
                     r"inconsisten|split across",
    "duplicates": r"duplicat|counted twice|inflat|repeated row",
    "truncated": r"truncat|only the first|subset|not the (?:whole|full) (?:file|dataset)|"
                 r"partial read",
    "no_key": r"unique|distinct|identif|count of rows|per row",
    "small_groups": r"small (?:group|sample)|few rows per|not stable|fewer than \d+|"
                    r"per-?group|thin slice",
    "small_sample": r"only \d+ rows|small (?:sample|dataset)|indicative|not measured|"
                    r"\bn\s*=|sample size",
    "causation": r"caus|correlat|associat|hypothes|moved together|not (?:say )?why|"
                 r"cannot explain|does not (?:prove|establish)",
    "low_grade": r"quality|unreliable|not trustworthy|grade|score of \d+|damaged|messy",
}

_GENERIC_WORDS = frozenset({"values", "because", "dataset", "records", "period",
                            "periods", "inside", "around", "before", "should"})

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _sentence_at(text: str, position: int) -> str:
    """The sentence containing a character offset."""
    start = 0
    for match in _SENTENCE_SPLIT.finditer(text):
        if match.end() > position:
            break
        start = match.end()
    end = _SENTENCE_SPLIT.search(text, position)
    return text[start:end.start() if end else len(text)]


def _hedged_at(text: str, position: int) -> bool:
    """Is the claim at this offset already qualified in its own sentence?

    An answer that says "this may be missing data, not a real decline" is doing
    exactly what candor asks for. Flagging it would train the agent to hedge
    less, which is the opposite of the point.
    """
    return bool(HEDGE_RE.search(_sentence_at(text, position)))


def _scale(suffix: str | None) -> float:
    if not suffix:
        return 1.0
    s = suffix.lower().rstrip(".")
    return {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6,
            "b": 1e9, "bn": 1e9, "billion": 1e9}.get(s, 1.0)


def _context(text: str, start: int, end: int, width: int = 60) -> str:
    return " ".join(text[max(0, start - width):min(len(text), end + width)].split())


def _decimals(literal: str) -> int:
    return len(literal.split(".")[1]) if "." in literal else 0


def verify(profile: DatasetProfile, answer: str, *,
           sufficiency: Sufficiency | None = None) -> ClaimReport:
    """Find claims in `answer` that `profile` does not support."""
    report = ClaimReport(source=profile.source)
    findings: list[Finding] = []
    text = answer or ""
    lowered = text.lower()
    hedged = bool(HEDGE_RE.search(text))

    column_names = {c.name.lower() for c in profile.columns}
    known_values: set[str] = set()
    for column in profile.columns:
        for value, _ in column.top_values:
            token = str(value).strip().lower()
            if len(token) >= 3:
                known_values.add(token)

    # -- numeric claims ------------------------------------------------------ #
    counted = 0
    for match in _NUMBER_RE.finditer(text):
        literal = match.group("num")
        value = float(literal.replace(",", "")) * _scale(match.group("suffix"))
        suffix = (match.group("suffix") or "").lower()
        is_percent = suffix in ("%", "percent")
        counted += 1
        quote = _context(text, match.start(), match.end())

        if _YEAR_RE.fullmatch(literal):
            continue

        if is_percent and profile.row_count < 100 and _decimals(literal) >= 1:
            findings.append(Finding(
                code="false_precision",
                severity=Severity.HIGH,
                message=f"{match.group(0).strip()} implies precision the sample cannot carry — "
                        f"with {profile.row_count:,} rows, one row is worth "
                        f"{100 / max(profile.row_count, 1):.1f} percentage points",
                quote=quote,
                suggestion=f"Round to a whole percent, or state it as "
                           f"'{round(value)}% ({round(value * profile.row_count / 100)} of "
                           f"{profile.row_count:,} rows)'.",
            ))

        if is_percent and value > 100:
            findings.append(Finding(
                code="impossible_percentage",
                severity=Severity.HIGH,
                message=f"{match.group(0).strip()} is a share above 100%",
                quote=quote,
                suggestion="Check the denominator; this is usually a duplicated-rows or "
                           "fan-out-join artefact.",
            ))

        # "4,500 orders" is a count claim; "1,250,000 by Q2. All 4,500 orders" is
        # not one for the first number, so the noun has to follow immediately and
        # on the same side of any sentence break.
        trailing = re.split(r"[.!?;]", text[match.end():match.end() + 26])[0]
        counts_things = re.match(
            r"^\s*(?:[a-z]+\s+){0,2}"
            r"(rows?|records?|customers?|users?|orders?|entries|items?|transactions?|"
            r"accounts?|respondents?|employees?)\b",
            trailing, re.I,
        )
        if not is_percent and not suffix and counts_things and \
                value > profile.row_count and profile.row_count:
            findings.append(Finding(
                code="count_exceeds_data",
                severity=Severity.CRITICAL,
                message=f"the answer cites {int(value):,} records, but the dataset holds only "
                        f"{profile.row_count:,} rows",
                quote=quote,
                suggestion=f"No count from this data can exceed {profile.row_count:,}.",
            ))

    report.checked_claims = counted

    # -- fields and values that do not exist --------------------------------- #
    for match in re.finditer(r"\b(?:column|field|the)\s+[`'\"]([A-Za-z_][A-Za-z0-9_ ]{1,30})[`'\"]",
                             text, re.I):
        name = match.group(1).strip().lower()
        if name not in column_names:
            findings.append(Finding(
                code="phantom_field",
                severity=Severity.CRITICAL,
                message=f"the answer refers to a field {match.group(1)!r} that is not in the data",
                quote=_context(text, match.start(), match.end()),
                suggestion=f"Available columns: {', '.join(c.name for c in profile.columns[:12])}.",
            ))

    for match in re.finditer(r"[`'\"]([A-Za-z][A-Za-z0-9 _./-]{2,40})[`'\"]", text):
        token = match.group(1).strip().lower()
        if token in column_names or token in known_values:
            continue
        if len(token.split()) > 4 or token.endswith((".", "?", "!")):
            continue  # a quoted sentence, not a data value
        findings.append(Finding(
            code="unverified_value",
            severity=Severity.LOW,
            message=f"the quoted value {match.group(1)!r} does not appear among the most common "
                    "values of any column",
            quote=_context(text, match.start(), match.end()),
            suggestion="Confirm this value exists in the data before quoting it back.",
        ))

    # -- time claims outside the data's range -------------------------------- #
    temporal = profile.temporal_columns
    if temporal:
        stats = max(temporal, key=lambda c: c.temporal.distinct_periods if c.temporal else 0).temporal
        if stats and stats.min and stats.max:
            low, high = int(stats.min[:4]), int(stats.max[:4])
            for match in _YEAR_RE.finditer(text):
                year = int(match.group(1))
                if not (low <= year <= high):
                    findings.append(Finding(
                        code="out_of_range_period",
                        severity=Severity.HIGH,
                        message=f"the answer discusses {year}, but the data only covers "
                                f"{stats.min[:10]} to {stats.max[:10]}",
                        quote=_context(text, match.start(), match.end()),
                        suggestion=f"Say explicitly that {year} is outside the data.",
                    ))

    # -- absolutes ----------------------------------------------------------- #
    incomplete = [c for c in profile.columns if 0 < c.nulls and c.count]
    for match in ABSOLUTE_RE.finditer(text):
        if _hedged_at(text, match.start()):
            continue
        if incomplete or profile.truncated:
            reason = (
                f"{len(incomplete)} column(s) have missing values"
                if incomplete else "only part of the source was read"
            )
            findings.append(Finding(
                code="unsupported_absolute",
                severity=Severity.HIGH,
                message=f"{match.group(0)!r} asserts something about every row, but {reason}, "
                        "so the rows it is based on are not all the rows",
                quote=_context(text, match.start(), match.end()),
                suggestion="Scope it: 'of the rows where this is recorded, ...'.",
            ))
            break

    # -- causation ----------------------------------------------------------- #
    for match in CAUSAL_RE.finditer(text):
        if _hedged_at(text, match.start()):
            continue
        findings.append(Finding(
            code="causal_claim",
            severity=Severity.HIGH,
            message=f"{match.group(0)!r} states causation, which observational data cannot "
                    "establish — there is no control group here",
            quote=_context(text, match.start(), match.end()),
            suggestion="Rephrase as association: 'X and Y moved together', and name it as a "
                       "hypothesis to test.",
        ))
        break

    # -- overclaimed certainty ----------------------------------------------- #
    for match in CERTAINTY_RE.finditer(text):
        if _hedged_at(text, match.start()):
            continue
        findings.append(Finding(
            code="overclaimed_certainty",
            severity=Severity.MEDIUM,
            message=f"{match.group(0)!r} asserts more confidence than a single dataset supports",
            quote=_context(text, match.start(), match.end()),
            suggestion="Drop the intensifier and let the number stand on its own.",
        ))
        break

    # -- forecasting ---------------------------------------------------------- #
    for match in FORECAST_RE.finditer(text):
        findings.append(Finding(
            code="unbacked_forecast",
            severity=Severity.HIGH,
            message=f"{match.group(0)!r} projects forward, but this is historical data with no "
                    "model behind it",
            quote=_context(text, match.start(), match.end()),
            suggestion="State the historical trend and say explicitly that no forecast is implied.",
        ))
        break

    # -- rankings that are not decidable -------------------------------------- #
    superlative = next((m for m in SUPERLATIVE_RE.finditer(text)
                        if not _hedged_at(text, m.start())), None)
    if superlative:
        shaky = [c.name for c in profile.columns if c.nulls / max(c.count, 1) > 0.1]
        if shaky:
            findings.append(Finding(
                code="unstable_ranking",
                severity=Severity.MEDIUM,
                message="the answer names a top/bottom item, but "
                        f"{', '.join(repr(n) for n in shaky[:3])} have >10% missing values, so "
                        "the ranking could change once those rows are filled",
                quote=_context(text, *superlative.span()),
                suggestion="Say the ranking is among rows with complete data.",
            ))

    # -- caveats that were dropped -------------------------------------------- #
    if sufficiency is not None:
        addressed = sum(
            1 for c in sufficiency.caveats
            if (p := DISCLOSURE_PATTERNS.get(c.topic)) and re.search(p, lowered)
        )
        for caveat in sufficiency.caveats:
            pattern = DISCLOSURE_PATTERNS.get(caveat.topic)
            if pattern and re.search(pattern, lowered):
                continue
            # The overall-grade caveat is a summary of the specific ones. An
            # answer that already named several defects has made the point.
            if caveat.topic == "low_grade" and addressed >= 2:
                continue
            if pattern is None:
                # Unknown topic: fall back to distinctive words from the caveat.
                keywords = [w for w in re.findall(r"[a-z]{6,}", caveat.text.lower())
                            if w not in _GENERIC_WORDS][:4]
                if not keywords or any(k in lowered for k in keywords):
                    continue
            findings.append(Finding(
                code="omitted_disclosure",
                severity=Severity.HIGH,
                message=f"the answer never addresses a required caveat ({caveat.topic})",
                quote=caveat.text,
                suggestion=f"Say this in the answer's own words: {caveat.text}",
            ))

        if sufficiency.verdict is Verdict.INSUFFICIENT and not hedged:
            findings.append(Finding(
                code="answered_when_insufficient",
                severity=Severity.CRITICAL,
                message="the data was judged insufficient for this question, but the answer "
                        "contains no hedging or refusal at all",
                quote=text[:160].strip(),
                suggestion="Replace the answer with the honest response from the assessment.",
            ))

    findings.sort(key=lambda f: -f.severity.rank)
    report.findings = findings
    report.honesty_score = _honesty_score(findings)
    report.verdict = (
        "reject" if any(f.severity is Severity.CRITICAL for f in findings)
        else "revise" if any(f.severity in (Severity.HIGH, Severity.MEDIUM) for f in findings)
        else "pass"
    )
    return report


def _honesty_score(findings: list[Finding]) -> float:
    penalty = sum({
        Severity.CRITICAL: 40.0,
        Severity.HIGH: 18.0,
        Severity.MEDIUM: 8.0,
        Severity.LOW: 3.0,
        Severity.INFO: 0.0,
    }[f.severity] for f in findings)
    return round(max(0.0, 100.0 - penalty), 1)
