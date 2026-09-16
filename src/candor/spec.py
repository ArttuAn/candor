"""The build gate: can this app be built from this logic, without guessing?

The data gate asks <can the data answer the question>. This gate asks the
reverse question about instructions: <do the rules pin down the behavior
enough to build from>. A spec that says "when usage is high, scale up" has no
implementation until someone decides what "high" means — and an agent that
picks 80% and builds on it has silently made a product decision the human
never approved.

candor finds those seams on purpose. It does not judge whether the spec is
*sound*, only whether it is *determinate enough to build without inventing
requirements*. Where it is not, it hands back the exact questions the human
has to answer — and the agent is expected to ask, not assume.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import BuildVerdict, LogicGap, Severity, SpecAssessment


def _load_text(source) -> str:
    """Read a spec from a file path, or take it as-is when it is inline text.

    A string is treated as a path only when it has no whitespace AND such a
    file actually exists — inline rules are almost always sentences, and a
    sentence is never a path.
    """
    if isinstance(source, (str, Path)):
        text = str(source)
        if "\n" not in text and " " not in text and Path(text).is_file():
            return Path(text).read_text(encoding="utf-8")
        return text
    return str(source)


def _sentences(text: str) -> list[str]:
    """Turn spec text into rule-sized fragments.

    Specs are usually lists: one rule per line. Newlines are therefore real
    boundaries (a prose paragraph wrap is racy to infer, so a wrapped line
    becomes a fragment — still plenty to detect on). List markers and leading
    indentation are stripped; a fragment that is clearly just a heading is
    kept, because headings carry no logic.
    """
    fragments: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line)
        line = re.sub(r"\s+", " ", line)
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9#\"'(])", line):
            part = part.strip()
            if part:
                fragments.append(part)
    return fragments


def _topic_fingerprint(text: str, limit: int = 48) -> str:
    """A stable short key for a piece of text, so answers can be matched."""
    normal = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return normal[:limit] or "rule"


def _ask(template: str, *values, **kwargs) -> str:
    """Build a question that ends in exactly one '?'.

    Embedded sentences may carry a trailing period; strip that, then guarantee
    exactly one question mark at the end — never two.
    """
    text = template.format(*values, **kwargs).rstrip(". ")
    if not text.endswith("?"):
        text += "?"
    return text


# --------------------------------------------------------------------------- #
# detectors — each one looks for one way the logic can be underdetermined
#
# A detector returns LogicGap instances. `topic` is the stable key a human
# answer maps onto; it must be reproducible (no line numbers), so that
# candor_spec_resolve can retire a gap once its question is answered.

# Words that name a quantity a rule can depend on. A vague word only matters
# when it is attached to one of these.
QUANTIFIER_NEAR = re.compile(
    r"\b(usage|traffic|load|volume|count|amount|size|number|latency|requests|"
    r"users|tokens|spend|cost|duration|time|limit|threshold|quota|connections|"
    r"amount|rate|frequency|capacity|temperature|pressure|storage)\b",
    re.I,
)
VAGUE_RE = re.compile(
    r"\b(high|low|large|small|many|few|long|short|fast|slow|great|frequently|"
    r"rarely|often|occasionally|sometimes|significantly|substantially|excessive|"
    r"too (many|large|much)|a lot|a few|a bit)\b",
    re.I,
)
TBD_RE = re.compile(
    r"\b(tbd|todo|tba|to be decided|to be determined|to be confirmed|"
    r"to be filled|placeholder value|undecided|unspecified|"
    r"we('|’)?ll (figure|work|decide)|will (figure|decide|determine)|"
    r"not yet( decided| determined| specified)?|pending( decision)?)\b",
    re.I,
)
MAYBE_RE = re.compile(
    r"\b(possibly|probably|maybe|perhaps|in some cases|could be|might be|"
    r"maybe not|depending on|it depends|not sure|uncertain|ambiguous|"
    r"something like|kind of|sort of|we need to)\b",
    re.I,
)
ASSUMPTION_RE = re.compile(
    r"\b(assume|assumed|assuming|presume|presumed|for simplicity|"
    r"let('|’)s assume|lets assume|assuming that)\b",
    re.I,
)
PLACEHOLDER_RE = re.compile(
    r"(\{\{[^}]{0,40}\}\}|\[\s*(?:\?|x|y|xyz)\s*\]|<[a-z_]{2,40}>|\b\?\b|[X]{3,}|Lorem ipsum)",
    re.I,
)
CONDITIONAL_RE = re.compile(r"\b(if|when|whenever|unless|only if|even if)\b", re.I)
ELSE_RE = re.compile(r"\b(else|otherwise|else if|elif|unless)\b", re.I)
ENTITY_RE = re.compile(
    r"\b(to|through|via|using|against|across|between)\s+"
    r"(?:the|our|an|this)\s+"
    r"(?:[a-z]+\s+){0,2}"
    r"(?P<entity>connector|service|module|component|handler|endpoint|adapter|"
    r"gateway|interface|repository|client|worker|provider|pipeline|system)\b",
    re.I,
)


def _detect_stated_unknowns(text: str, sentences: list[str]) -> list[LogicGap]:
    """Rules that openly say the decision has not been made yet."""
    gaps: list[LogicGap] = []
    for sentence in sentences:
        if not TBD_RE.search(sentence):
            continue
        gaps.append(LogicGap(
            topic="undecided:" + _topic_fingerprint(sentence),
            code="stated_undecided",
            severity=Severity.CRITICAL,
            message=(
                f'"{sentence[:90]}" openly leaves a decision open — a builder '
                "cannot pick the answer on the human's behalf."
            ),
            quote=sentence,
            fix="Decide the open point, or explicitly scope it out of this build.",
            question=_ask(
                "What is the decision for the open point in: {}",
                sentence,
            ),
        ))
    return gaps


def _detect_vague_quantification(text: str, sentences: list[str]) -> list[LogicGap]:
    """Rules whose threshold is a word instead of a number.

    Fires only when a vague word sits in a rule that also names a quantity —
    "when latency is high" is caught, "keep the UI fast" (a goal) is not. A
    rule that already carries a number near the vague word is determinate
    enough and is left alone.
    """
    gaps: list[LogicGap] = []
    for sentence in sentences:
        if re.search(r"\d", sentence):
            continue
        match = VAGUE_RE.search(sentence)
        if match is None or not QUANTIFIER_NEAR.search(sentence):
            continue
        gaps.append(LogicGap(
            topic="vague:" + _topic_fingerprint(sentence),
            code="vague_quantifier",
            severity=Severity.HIGH,
            message=(
                f'"{sentence[:90]}" makes behaviour depend on the word '
                f'"{match.group()}" — a rule like this needs a number, a '
                "threshold, or a formula before it can be built."
            ),
            quote=sentence,
            fix=(
                "Replace the vague word with a concrete threshold, formula or "
                "measurable definition (e.g. '> 80% CPU for 5 minutes')."
            ),
            question=_ask(
                "What number or formula should replace '{}' in: {}",
                match.group(),
                sentence,
            ),
        ))
    return gaps


def _detect_undefined_conditionals(text: str, sentences: list[str]) -> list[LogicGap]:
    """One-sided state rules: "if over quota, warn" with no stated else side.

    A rule is genuinely underdetermined when it describes a state the system
    must react to (usage, quota, latency, temperature...) but says nothing
    about the complementary state. A rule with a concrete threshold and an
    explicit else is left alone — its logic is already pinned down. A line
    with a placeholder is already covered by the placeholder question.
    """
    gaps: list[LogicGap] = []
    for sentence in sentences:
        if not CONDITIONAL_RE.search(sentence):
            continue
        if ELSE_RE.search(sentence):
            continue
        if re.search(r"\d", sentence):
            continue
        if PLACEHOLDER_RE.search(sentence):
            continue
        if not QUANTIFIER_NEAR.search(sentence):
            continue
        # a line already covered by its vague-threshold question should not be
        # asked twice about the same seam
        if VAGUE_RE.search(sentence):
            continue
        gaps.append(LogicGap(
            topic="conditional:" + _topic_fingerprint(sentence),
            code="one_sided_conditional",
            severity=Severity.HIGH,
            message=(
                f'"{sentence[:90]}" describes one state only; the complementary '
                "state is never stated, so its behaviour can only be guessed."
            ),
            quote=sentence,
            fix=(
                "State what happens in the complementary state explicitly (the "
                "else / otherwise / unless case), or confirm nothing should happen."
            ),
            question=_ask(
                "What should happen in the complementary state of: {}",
                sentence,
            ),
        ))
    return gaps


def _detect_placeholder_values(text: str, sentences: list[str]) -> list[LogicGap]:
    """Template placeholders like {{value}} or [x] that read as real logic."""
    gaps: list[LogicGap] = []
    for sentence in sentences:
        for match in PLACEHOLDER_RE.finditer(sentence):
            gaps.append(LogicGap(
                topic="placeholder:" + _topic_fingerprint(sentence),
                code="placeholder_value",
                severity=Severity.HIGH,
                message=(
                    f'"{sentence[:90]}" contains an unfilled placeholder '
                    f"('{match.group()}') that reads like real logic."
                ),
                quote=sentence,
                fix="Replace the placeholder with the concrete value it stands for.",
                question=_ask(
                "What value belongs where '{}' currently sits in: {}",
                match.group(),
                sentence,
            ),
            ))
    return gaps


def _detect_assumed_entities(text: str, sentences: list[str]) -> list[LogicGap]:
    """Wiring references to things the spec never defines.

    The classic seam: "pass it to the connector" is unimplementable until the
    spec says what the connector is. Only prepositional wiring references are
    flagged ("to the connector", "via the gateway") — a thing merely named in
    passing, like the subject of its own description, is left alone.
    """
    gaps: list[LogicGap] = []
    for sentence in sentences:
        match = ENTITY_RE.search(sentence)
        if match is None:
            continue
        entity = match.group("entity")
        if f"define {entity}" in sentence.lower():
            continue
        gaps.append(LogicGap(
            topic="entity:" + entity.lower(),
            code="undefined_entity",
            severity=Severity.HIGH,
            message=(
                f'"{sentence[:90]}" wires something to a thing — "{entity}" — '
                "the spec never defines or describes."
            ),
            quote=sentence,
            fix=(
                "Add a short definition of the entity, what it does, its input "
                "and its output, before wiring anything to it."
            ),
            question=_ask(
                "What is '{}', and what does the system expect of it?",
                entity,
            ),
        ))
    return gaps


def _detect_hedged_rules(text: str, sentences: list[str]) -> list[LogicGap]:
    """Rules hedged so much they no longer determine behaviour.

    "the system should probably notify users" cannot be implemented or tested.
    A rule that has to hold must be stated without the hedge.
    """
    gaps: list[LogicGap] = []
    for sentence in sentences:
        if len(sentence) < 24 or not MAYBE_RE.search(sentence):
            continue
        gaps.append(LogicGap(
            topic="hedged:" + _topic_fingerprint(sentence),
            code="hedged_rule",
            severity=Severity.MEDIUM,
            message=(
                f'"{sentence[:90]}" is hedged ("probably", "depending on"...). '
                "Implementing a hedged rule means inventing its strict form."
            ),
            quote=sentence,
            fix="Restate the rule unambiguously: what must happen, when, always.",
            question=_ask(
                "Can you restate this rule without hedges like 'probably': {}",
                sentence,
            ),
        ))
    return gaps


def _dedupe_gaps(gaps: list[LogicGap]) -> list[LogicGap]:
    seen: set[str] = set()
    out: list[LogicGap] = []
    for gap in gaps:
        if gap.topic in seen:
            continue
        seen.add(gap.topic)
        out.append(gap)
    return out


def assess_spec(source, *, max_examples: int = 40) -> SpecAssessment:
    """Decide whether the app can be built from `source` without guessing.

    `source` is a file path or inline spec text. The result carries a verdict,
    the gaps found (each with the question the human must answer), and an
    honest response for when the build cannot proceed.
    """
    text = _load_text(source)
    is_file = Path(str(source)).is_file() if isinstance(source, (str, Path)) else False
    name = str(source) if is_file else "<inline>"
    sentences = _sentences(text)

    gaps: list[LogicGap] = []
    gaps += _detect_stated_unknowns(text, sentences)
    gaps += _detect_vague_quantification(text, sentences)
    gaps += _detect_undefined_conditionals(text, sentences)
    gaps += _detect_placeholder_values(text, sentences)
    gaps += _detect_assumed_entities(text, sentences)
    gaps += _detect_hedged_rules(text, sentences)
    gaps = _dedupe_gaps(gaps)[:max_examples]

    assumptions = [s for s in sentences if ASSUMPTION_RE.search(s)]
    open_questions = [
        s for s in sentences
        if s.rstrip().endswith("?") and re.search(r"\b(should|can|do|could|would|is|are)\b", s, re.I)
    ]

    confidence = _confidence_for(gaps)
    critical = [g for g in gaps if g.severity is Severity.CRITICAL]
    if critical or confidence < 0.4:
        verdict = BuildVerdict.INSUFFICIENT
    elif gaps:
        verdict = BuildVerdict.PARTIAL
    else:
        verdict = BuildVerdict.BUILDABLE

    result = SpecAssessment(
        source=name,
        verdict=verdict,
        confidence_ceiling=round(confidence, 3),
        rule_count=len(sentences),
        gaps=gaps,
        assumptions=assumptions[:5],
        open_questions=open_questions[:5],
    )
    result.honest_response = _compose_spec_response(result)
    return result


def resolve_spec(source, answers: dict[str, str], *, max_examples: int = 40) -> SpecAssessment:
    """Re-assess a spec after a human answers the open questions.

    `answers` maps a gap's `topic` to the human's answer. Answered topics are
    retired; anything still open stays open, so the verdict can only improve
    as far as the answers actually take it.
    """
    assessment = assess_spec(source, max_examples=max_examples)
    answered = {k.lower().strip() for k, v in answers.items() if v and v.strip()}

    remaining = [gap for gap in assessment.gaps if gap.topic.lower() not in answered]
    assessment.gaps = remaining
    assessment.resolved = [t for t in answered]
    assessment.confidence_ceiling = round(_confidence_for(remaining), 3)

    critical = [g for g in remaining if g.severity is Severity.CRITICAL]
    assessment.verdict = (
        BuildVerdict.INSUFFICIENT if critical or assessment.confidence_ceiling < 0.4
        else BuildVerdict.PARTIAL if remaining
        else BuildVerdict.BUILDABLE
    )
    assessment.honest_response = _compose_spec_response(assessment)
    return assessment


def _confidence_for(gaps: list[LogicGap]) -> float:
    confidence = 1.0
    for gap in gaps:
        confidence *= {Severity.CRITICAL: 0.3, Severity.HIGH: 0.7,
                       Severity.MEDIUM: 0.88, Severity.LOW: 0.95}[gap.severity]
    return max(0.05, min(1.0, confidence))


def _compose_spec_response(result: SpecAssessment) -> str:
    """The words a builder should say when the logic is not determinate."""
    source = result.source
    if result.verdict is BuildVerdict.INSUFFICIENT:
        reasons = [g.message for g in result.gaps[:6]] or result.open_questions
        lines = [
            f"I won't build this from {source} yet, and I don't want to invent "
            "requirements quietly on your behalf. The logic that would have to "
            "be decided before it can be built:",
            "",
        ]
        lines += [f"  - {reason}" for reason in reasons[:4]]
        lines += ["", "Answer these, and I'll re-check:"]
        lines += [f"  ? {g.question}" for g in result.gaps[:4]]
        lines += ["", "I can't proceed by assuming the answers."]
        return "\n".join(lines)

    if result.verdict is BuildVerdict.PARTIAL:
        lines = [
            f"I can build most of {source}, but these decisions are still open "
            "and I won't pick them for you:",
            "",
        ]
        lines += [f"  ? {g.question}" for g in result.gaps[:5]]
        lines += ["", "Answer them and the build can go ahead unguessed."]
        return "\n".join(lines)

    return (
        f"The logic in {source} is determinate enough to build from: "
        f"{result.rule_count} rule(s), none of them underdetermined. Build normally."
    )


def spec_kit(source, *, answered: dict[str, str] | None = None) -> dict:
    """Compact JSON block: what may be built, what must be asked first."""
    result = resolve_spec(source, answered or {}) if answered else assess_spec(source)
    return {
        "source": result.source,
        "verdict": result.verdict.value,
        "confidence_ceiling": result.confidence_ceiling,
        "rules": result.rule_count,
        "must_answer_before_building": [
            {"topic": g.topic, "question": g.question, "why": g.message}
            for g in result.gaps[:12]
        ],
        "assumptions_to_confirm": result.assumptions,
        "open_questions": result.open_questions,
        "resolved": result.resolved,
        "instruction": (
            "Before building, every item in must_answer_before_building must be "
            "answered by the user — not guessed. If verdict is 'insufficient', "
            "refuse to build and present the questions."
        ),
    }