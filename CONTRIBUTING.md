# Contributing to candor

The project has one governing constraint: **an honest answer must pass
cleanly.** A check that fires on prose which already hedged correctly teaches
the agent to hedge less, which is worse than having no check at all. Every
change is judged against that first.

## Getting set up

```bash
uv sync --extra mcp
uv run pytest
uv run ruff check src tests
```

## Adding a quality rule

Rules live in `src/candor/issues.py`. A rule answers three questions, and the
`Issue` it produces has a field for each:

| Field | What it must say |
|---|---|
| `message` | What is wrong, with the numbers that prove it |
| `impact` | What it breaks for someone trying to answer a question |
| `fix` | What a person would actually do about it |

`fix` and `impact` are not optional — there is a test that fails the build if
any rule ships without them. A finding without a remediation is a complaint.

Also set:

- `dimension` — which of the eight scores it debits
- `severity` — `critical` only when the defect makes a figure *unverifiable*,
  not merely worse. Ambiguous dates are critical; a 6% null rate is not.
- `effort` — drives the ranking in `improve.py`
- `blocks` — the question intents this defect makes unanswerable

Thresholds belong in the constants at the top of `issues.py`, never inline, so
a team can retune the whole catalogue in one place.

## Adding a caveat

Caveats are emitted in `assess.py` as `Caveat(topic, text)`. **Every new topic
needs a matching entry in `DISCLOSURE_PATTERNS` in `verify.py`** — the pattern
that recognises an answer having addressed that subject in its own words.
`test_every_caveat_topic_has_a_disclosure_pattern` enforces this, because a
caveat the verifier cannot recognise gets reported as missing even when the
answer stated it perfectly.

Write the pattern against natural prose, not against the caveat's own wording,
and add a case to `test_disclosure_patterns_recognise_natural_prose`.

## Adding a verify check

Anything that flags a phrase must first ask `_hedged_at(text, position)`. If
the sentence around the match is already qualified, the claim is a hypothesis,
not an assertion, and it is not a finding.

Watch for idioms. `"no rows at all"` is not a universal quantifier. `"the most
recent record"` is not a ranking. Test both the true positive and the idiom.

## Tests

- Pin the clock. Use `now=NOW` from `tests/helpers.py`, or the `recent_csv`
  fixture for anything that goes through the CLI, which has no `--now`.
- Assert on the *behaviour a harness depends on* — the verdict, the exit code,
  the topic — not on exact prose, which should be free to improve.
- New rules need a fixture that fires them and, where there is a plausible
  false positive, a fixture that does not.

## Style

`ruff check src tests` is the linter and the formatter's opinion. Line length
is 100. Comments explain *why a threshold is what it is*, not what the line
does.
