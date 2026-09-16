"""The build gate: determining whether logic is determinate enough to build.

The spec gate asks the mirror question of the data gate — not "can this answer
the question" but "can an app be built from this logic without the builder
inventing requirements". A spec that says "scale up when usage is high" leaves
the threshold for the builder to pick, which is a product decision the human
never made.
"""

import pytest

from candor import BuildVerdict, assess_spec, resolve_spec
from candor.spec import _sentences, _topic_fingerprint


def _codes(result) -> list[str]:
    return sorted(g.code for g in result.gaps)


@pytest.fixture
def messy_spec(write):
    return write("messy_spec.md", """\
    Warehouse bot rules:

    When usage is high, scale up the cluster.

    When the temperature is excessive, call the cooling handler.

    The retention policy is TBD — we'll decide later.

    Pass every order to the connector before saving it.

    Notify staff when the queue length is very long ({{threshold}}).

    If over quota, warn the account owner.
    """)


@pytest.fixture
def clean_spec(write):
    return write("clean_spec.md", """\
    Warehouse bot rules:

    When CPU usage exceeds 80% for 5 continuous minutes, scale up.

    When CPU usage stays below 40% for 30 minutes, scale down one node.

    The retention policy is 90 days for events and 3 years for invoices.

    Failed jobs are retried 3 times, then moved to the dead-letter queue.

    When the queue backlog exceeds 5000 messages, notify on-call staff.
    Otherwise, no notification is sent.
    """)


class TestSentences:
    def test_splits_on_real_boundaries_only(self):
        parts = _sentences("When usage is high, scale up. If it stays low, "
                           "shrink. The rule uses 42% as the threshold.")
        assert len(parts) == 3
        assert parts[2] == "The rule uses 42% as the threshold."

    def test_each_line_stays_a_rule(self):
        parts = _sentences("1. When usage is high, scale up the cluster.\n"
                           "2. If it stays low for a while, shrink.\n")
        assert len(parts) == 2
        assert parts[0] == "When usage is high, scale up the cluster."
        assert parts[1] == "If it stays low for a while, shrink."

    def test_collapses_whitespace(self):
        assert _sentences("a\n\n   b.") == ["a", "b."]


class TestTopicFingerprint:
    def test_stable(self):
        a = _topic_fingerprint("When usage is high, scale up the cluster.")
        b = _topic_fingerprint("When usage is high, scale up the cluster.")
        assert a == b

    def test_bounded_and_slugged(self):
        key = _topic_fingerprint("A " + "very long sentence " * 200)
        assert len(key) <= 48
        assert " " not in key


class TestAssess:
    def test_messy_spec_is_insufficient(self, messy_spec):
        result = assess_spec(str(messy_spec))
        assert result.verdict is BuildVerdict.INSUFFICIENT
        assert result.confidence_ceiling < 0.4
        assert "usage is high" in result.honest_response
        assert "Answer these" in result.honest_response

    def test_messy_spec_finds_all_gap_kinds(self, messy_spec):
        result = assess_spec(str(messy_spec))
        codes = _codes(result)
        assert "vague_quantifier" in codes            # usage is high
        assert "one_sided_conditional" in codes       # temperature / queue length
        assert "stated_undecided" in codes            # TBD retention policy
        assert "placeholder_value" in codes           # {{threshold}}
        assert "undefined_entity" in codes            # the connector

    def test_every_gap_carries_a_question(self, messy_spec):
        result = assess_spec(str(messy_spec))
        assert result.gaps
        for gap in result.gaps:
            assert gap.question and "?" in gap.question
            assert gap.topic
            assert gap.message

    def test_one_sided_conditionals_need_a_quantity(self, write):
        spec = write("spec.md", "When the user reports a bug, create a ticket.\n"
                                "When a payment succeeds, mark the order paid.\n")
        result = assess_spec(str(spec))
        assert "one_sided_conditional" not in _codes(result)
        assert result.verdict is BuildVerdict.BUILDABLE

    def test_clean_spec_is_buildable(self, clean_spec):
        result = assess_spec(str(clean_spec))
        assert result.verdict is BuildVerdict.BUILDABLE
        assert result.confidence_ceiling == 1.0
        assert not result.gaps

    def test_inline_text_weighted_across_rules(self):
        inline = ("Admins can edit posts. When CPU usage exceeds 80%, scale up. "
                  "Else below 40%, scale down. There are 3 partners, "
                  "renewing at year end.")
        result = assess_spec(inline)
        assert result.verdict is BuildVerdict.BUILDABLE
        assert result.source == "<inline>"

    def test_assumptions_and_open_questions_surfaced(self, write):
        spec = write("spec.md", "Assume three partners are enough.\n"
                                "Should we send an email first?\n"
                                "When usage is high, scale up.\n")
        result = assess_spec(str(spec))
        assert any("three partners" in a for a in result.assumptions)
        assert any("Should" in q for q in result.open_questions)

    def test_commitment_as_hidden_question(self, write):
        spec = write("spec.md", "If over quota, warn the account owner.\n"
                                "No other behaviour is specified.\n")
        result = assess_spec(str(spec))
        assert result.gaps[0].question == "What should happen in the complementary state of: If over quota, warn the account owner?"
        assert "warn the account owner" in result.honest_response.split("? ")[-1]


class TestResolve:
    def test_topic_key_retires_gap(self, messy_spec):
        first = assess_spec(str(messy_spec))
        undecided = next(g for g in first.gaps if g.code == "stated_undecided")
        resolved = resolve_spec(str(messy_spec), {undecided.topic: "Keep events "
                                                  "90 days and invoices 3 years."})
        assert not any(g.code == "stated_undecided" for g in resolved.gaps)
        assert resolved.verdict is BuildVerdict.INSUFFICIENT

    def test_fully_answered_spec_becomes_buildable(self, messy_spec):
        first = assess_spec(str(messy_spec))
        answers = {gap.topic: _answer_for(gap) for gap in first.gaps}
        resolved = resolve_spec(str(messy_spec), answers)
        assert resolved.verdict is BuildVerdict.BUILDABLE
        assert resolved.confidence_ceiling == 1.0
        assert not resolved.gaps

    def test_unknown_topics_change_nothing(self, messy_spec):
        before = assess_spec(str(messy_spec))
        resolved = resolve_spec(str(messy_spec), {"bogus-topic": "anything"})
        assert _codes(resolved) == _codes(before)
        assert resolved.verdict is before.verdict


def _answer_for(gap):
    """A plausible human answer per gap kind, used to fully unblock a spec."""
    return {
        "vague_quantifier": "Scale at > 80% CPU for 5 minutes; scale down below 40% for 30 minutes.",
        "one_sided_conditional": "Scale at > 80% CPU for 5 minutes; scale down below 40% for 30 minutes.",
        "stated_undecided": "Keep events 90 days and invoices 3 years.",
        "placeholder_value": "5000 messages.",
        "undefined_entity": "The connector is the HTTP gateway that validates and forwards orders.",
        "hedged_rule": "Failed jobs are retried up to 3 times with backoff, then dead-lettered.",
    }[gap.code]


class TestCliBoundaries:
    def test_buildable_exit_ok_matches_data_gate(self, clean_spec):
        from candor.cli import EXIT_OK, main
        assert main(["spec", str(clean_spec)]) == EXIT_OK

    def test_insufficient_exit_matches_data_gate(self, messy_spec):
        from candor.cli import EXIT_INSUFFICIENT, main
        assert main(["spec", str(messy_spec)]) == EXIT_INSUFFICIENT