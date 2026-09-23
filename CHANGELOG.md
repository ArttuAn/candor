# Changelog

All notable changes to candor are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- The MCP server now declares all four side-effect hints
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) on
  every tool. Every candor tool is read-only: it reads a file the caller passes
  and returns a verdict, never writes or takes an irreversible action, and
  answers the same way every run — so the hints are `(true, false, true,
  false)` and platforms that warn before invoking or cache by side effect now
  know it. Hosts that reject tools with missing or non-boolean hints no longer
  drop the whole server.

## [0.2.0] — 2026-09-19

### Changed

- The distribution is now **`candor-gate`** on PyPI. The bare name `candor` is
  taken by an unrelated project, so the documented `pip install candor` fetched
  a stranger's package and publishing under that name was never possible. The
  command, the import and the repository are unchanged: `pip install
  candor-gate`, then `candor gate ...` or `import candor`.

### Added

- **`--as-of DATE`** on every command that reads a source. Judges freshness
  against the given date instead of today, so a run over a fixed snapshot is
  reproducible. Staleness is the one measurement that changes while the file
  does not.

### Fixed

- The `examples` CI job failed on every run since the first release. The
  committed `clean_orders.csv` snapshot ends 2025-08-22, so the freshness rule
  correctly flagged it as stale and the documented `gate` command exited 1 —
  a build that rotted by the calendar rather than by any change. The example is
  now pinned with `--as-of`.

## [0.1.0] — 2026-09-16

First release.

### Added

- **`assess`** — decides whether a question can be answered from a dataset.
  Returns a verdict, a confidence ceiling, the caveats the answer must state,
  the claims it must not make, and a written `honest_response` for when the
  answer is no.
- **`verify`** — checks a draft answer against the data. Catches counts above
  the row count, false precision, phantom fields and values, periods outside
  the data's range, causal language over observational data, unbacked
  forecasts, absolutes contradicted by missing values, and required caveats
  that were dropped from the prose.
- **`profile`** — eight independently scored quality dimensions, with every
  defect carrying its impact on answering and its remediation.
- **`improve`** — remediation ranked by value over effort, annotated with which
  of your questions each fix unblocks.
- **`truth_kit`** — the whole honesty block as one dict, sized for a system
  prompt.
- **MCP server** exposing five tools plus instructions telling the model when
  to refuse. Supports both the 1.x `FastMCP` and 2.x `MCPServer` SDK APIs.
- **CLI** with CI-shaped exit codes (`0` fine, `1` caveats, `2` insufficient,
  `3` unreadable) and a `candor gate` subcommand.
- Loaders for CSV, TSV, JSON, JSONL and SQLite using only the standard library;
  Parquet behind the `parquet` extra.

### Notable detection behaviour

- Time-series gaps are reported as contiguous runs, with the threshold scaled
  to how often records actually arrive — so a weekly series does not report a
  gap every six days.
- Staleness is judged against the data's own cadence: a monthly series ending
  last month is current.
- Outlier detection falls back from Tukey fences to median absolute deviation,
  and then to a minority-value check, so a column that is 98% one value still
  surfaces the two rogue millions in it.
- Placeholders (`unknown`, `TBD`, `-`) are counted separately from nulls,
  because every aggregation counts them as real.
- Day/month-ambiguous dates are rated critical rather than resolved by
  guessing a locale.

[Unreleased]: https://github.com/ArttuAn/candor/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ArttuAn/candor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ArttuAn/candor/releases/tag/v0.1.0
