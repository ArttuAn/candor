"""The whole loop, on the shape of problem candor exists for.

The scenario: someone asks why revenue fell in Q3, and the reason it "fell" is
that nobody loaded July. Every individual check passes — the column exists, the
date range covers Q3, the types are clean — which is why an agent
answers this one confidently and wrong.
"""

import json

import candor
from candor.models import Verdict
from helpers import NOW


def build(write):
    rows = ["order_date,region,revenue"]
    for month in range(1, 13):
        if month == 7:                       # the load that never ran
            continue
        for day in (5, 12, 19, 26):
            for region in ("EMEA", "APAC", "AMER"):
                rows.append(f"2024-{month:02d}-{day:02d},{region},{1000 + month}")
    return write("orders.csv", "\n".join(rows) + "\n")


def test_the_missing_month_is_caught_before_the_answer(write):
    path = build(write)
    profile = candor.profile(path, now=NOW)

    # Every per-field check a normal pipeline runs comes back clean: no nulls,
    # one date format, one type per column. The defect is a whole month that was
    # never loaded, which only a coverage check can see.
    assert profile.column("revenue").completeness == 1.0
    assert profile.column("order_date").completeness == 1.0
    assert "high_null_rate" not in {i.code for i in profile.issues}
    assert "mixed_date_formats" not in {i.code for i in profile.issues}
    gap = next(i for i in profile.issues if i.code == "time_gaps")
    assert gap.evidence["gaps"] == ["2024-06-27..2024-08-04 (39 days)"]
    assert profile.column("order_date").temporal.month_counts.get("2024-07") is None

    # But the question cannot be answered, and candor says why in words.
    result = candor.assess(profile, "Why did revenue drop in Q3 2024?", now=NOW)
    assert result.verdict is not Verdict.ANSWERABLE
    text = " ".join(c.text for c in result.caveats) + result.honest_response
    assert "2024-07" in text
    assert "missing data" in text.lower()


def test_the_confident_wrong_answer_is_rejected(write):
    path = build(write)
    profile = candor.profile(path, now=NOW)
    sufficiency = candor.assess(profile, "Why did revenue drop in Q3 2024?", now=NOW)

    wrong = ("Revenue fell 33% in Q3 2024, caused by weakness in EMEA. "
             "This clearly proves the regional strategy failed.")
    report = candor.verify(profile, wrong, sufficiency=sufficiency)
    assert report.verdict == "reject"
    assert "causal_claim" in {f.code for f in report.findings}
    assert "omitted_disclosure" in {f.code for f in report.findings}


def test_the_honest_answer_passes(write):
    path = build(write)
    profile = candor.profile(path, now=NOW)
    sufficiency = candor.assess(profile, "Why did revenue drop in Q3 2024?", now=NOW)

    honest = (
        "I don't think Q3 2024 shows a real decline. July 2024 has no rows at all "
        "while June and August do, which looks like a load that never ran rather "
        "than a drop in trading. Until July is backfilled I can't separate the two, "
        "and even then this data could only show what moved together, not what "
        "caused what."
    )
    report = candor.verify(profile, honest, sufficiency=sufficiency)
    assert report.verdict == "pass", [f.message for f in report.findings]


def test_truth_kit_is_a_self_contained_context_block(write):
    kit = candor.truth_kit(build(write), "Why did revenue drop in Q3 2024?")
    assert json.dumps(kit)                      # must survive a tool-result round trip
    assert kit["verdict"] in ("partial", "insufficient")
    assert kit["must_say"]
    assert kit["must_not_claim"]
    assert kit["instruction"]
    assert 0.0 <= kit["confidence_ceiling"] <= 1.0


def test_the_plan_names_the_load_that_never_ran(write):
    result = candor.plan(candor.profile(build(write), now=NOW),
                         ["Why did revenue drop in Q3 2024?"])
    assert result.steps
    assert any("gap" in step.fix.lower() or "backfill" in step.fix.lower()
               for step in result.steps)
