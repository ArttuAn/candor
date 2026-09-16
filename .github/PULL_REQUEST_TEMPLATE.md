## What this changes

<!-- One or two sentences. -->

## Checklist

- [ ] `uv run pytest` passes
- [ ] `uv run ruff check src tests` passes
- [ ] New rules set `impact`, `fix`, `severity`, `effort` and `blocks`
- [ ] New caveat topics have a matching `DISCLOSURE_PATTERNS` entry in `verify.py`
- [ ] New `verify` checks call `_hedged_at` before flagging
- [ ] Where a false positive was plausible, there is a test asserting it does not fire

## An honest answer still passes

<!-- The governing constraint. If this change adds a check, say how you
     confirmed it stays quiet on prose that already hedged correctly. -->
