"""Command line interface.

Exit codes are meant for CI and for agent harnesses that shell out:

    0   fine
    1   usable but with caveats (or: answer needs revision)
    2   insufficient / rejected — do not answer from this data
    3   the source could not be read at all
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, truth_kit
from .assess import assess
from .improve import plan
from .models import BuildVerdict, Verdict, to_json
from .profiler import profile as build_profile
from .render import (
    render_claims,
    render_plan,
    render_profile,
    render_spec_assessment,
    render_sufficiency,
)
from .sources import SourceError, list_sqlite_tables
from .spec import assess_spec, resolve_spec
from .verify import verify

EXIT_OK = 0
EXIT_CAVEATS = 1
EXIT_INSUFFICIENT = 2
EXIT_UNREADABLE = 3

GRADE_ORDER = ["F", "D", "C", "B", "A"]


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source", help="path to a csv, tsv, json, jsonl, sqlite or parquet file")
    parser.add_argument("--max-rows", type=int, default=200_000,
                        help="stop reading after this many rows (default: 200000)")
    parser.add_argument("--table", help="table name, for sqlite sources")
    parser.add_argument("--query", help="SQL to run instead of reading a table, for sqlite sources")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    parser.add_argument("--as-of", metavar="DATE",
                        help="judge freshness against this date (YYYY-MM-DD) instead of today; "
                             "use it for snapshot data so a run is reproducible")


def _as_of(args) -> datetime | None:
    """`--as-of` as a datetime.

    Staleness is the one measurement that changes when nothing else does: the
    same file scores worse tomorrow. Pinning the date is what makes a run on a
    fixed snapshot reproducible — and what keeps a committed example from
    rotting into a failing build.
    """
    raw = getattr(args, "as_of", None)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d")  # noqa: DTZ007 - a naive wall-clock date, as profiles use
    except ValueError:
        raise SystemExit(f"candor: --as-of must be YYYY-MM-DD, got {raw!r}") from None


def _read_answer(args) -> str:
    if args.answer_file:
        if args.answer_file == "-":
            return sys.stdin.read()
        return Path(args.answer_file).read_text(encoding="utf-8")
    if args.answer:
        return args.answer
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("candor verify: provide --answer, --answer-file, or pipe the answer on stdin")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="candor",
        description="Tell an agent when the data cannot support an answer — and what would fix it.",
    )
    parser.add_argument("--version", action="version", version=f"candor {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_profile = sub.add_parser("profile", help="measure the quality of a dataset")
    _add_source_args(p_profile)
    p_profile.add_argument("-v", "--verbose", action="store_true",
                           help="include impact narrative and clean dimensions")

    p_assess = sub.add_parser("assess", help="can this question be answered from this data?")
    _add_source_args(p_assess)
    p_assess.add_argument("-q", "--question", required=True, help="the question to test")

    p_verify = sub.add_parser("verify", help="check a draft answer against the data")
    _add_source_args(p_verify)
    p_verify.add_argument("-q", "--question", help="the question the answer responds to")
    p_verify.add_argument("-a", "--answer", help="the draft answer text")
    p_verify.add_argument("--answer-file", help="read the draft answer from a file, or - for stdin")

    p_improve = sub.add_parser("improve", help="ranked plan for making the data better")
    _add_source_args(p_improve)
    p_improve.add_argument("-q", "--question", action="append", default=[],
                           help="a question the data must answer; repeatable")

    p_kit = sub.add_parser("kit", help="compact JSON block to inject into an agent's context")
    _add_source_args(p_kit)
    p_kit.add_argument("-q", "--question", required=True)

    p_gate = sub.add_parser("gate", help="CI gate: fail when data or answer is not good enough")
    _add_source_args(p_gate)
    p_gate.add_argument("-q", "--question", help="gate on sufficiency for this question")
    p_gate.add_argument("--min-grade", default="C", choices=GRADE_ORDER,
                        help="minimum acceptable data grade (default: C)")
    p_gate.add_argument("--max-severity", default="high",
                        choices=["critical", "high", "medium", "low"],
                        help="fail if any issue is at or above this severity (default: high)")

    p_tables = sub.add_parser("tables", help="list the tables in a sqlite database")
    p_tables.add_argument("source")

    p_spec = sub.add_parser("spec", help="can the app be built from this logic without guessing?")
    p_spec.add_argument("source", help="path to a spec file, or inline spec text")
    p_spec.add_argument("-a", "--answer", action="append", default=[],
                        help="answer one open question as topic=answer; repeatable")
    p_spec.add_argument("--json", action="store_true", help="emit JSON instead of a report")

    sub.add_parser("mcp", help="run the MCP server on stdio")
    return parser


def _load(args):
    return build_profile(args.source, max_rows=args.max_rows,
                         table=getattr(args, "table", None),
                         query=getattr(args, "query", None),
                         now=_as_of(args))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        return mcp_main()

    if args.command == "tables":
        try:
            for name in list_sqlite_tables(args.source):
                print(name)
        except Exception as exc:  # sqlite3 raises several different types
            print(f"candor: {exc}", file=sys.stderr)
            return EXIT_UNREADABLE
        return EXIT_OK

    if args.command == "spec":
        answers: dict[str, str] = {}
        for item in args.answer:
            if "=" not in item:
                raise SystemExit("candor spec: answers must be topic=answer")
            topic, _, answer = item.partition("=")
            answers[topic.strip()] = answer.strip()
        result = resolve_spec(args.source, answers) if answers else assess_spec(args.source)
        print(to_json(result) if args.json else render_spec_assessment(result))
        return {
            BuildVerdict.BUILDABLE: EXIT_OK,
            BuildVerdict.PARTIAL: EXIT_CAVEATS,
            BuildVerdict.INSUFFICIENT: EXIT_INSUFFICIENT,
        }[result.verdict]

    try:
        profile = _load(args)
    except SourceError as exc:
        print(f"candor: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    if args.command == "profile":
        if args.json:
            print(to_json(profile))
        else:
            print(render_profile(profile, verbose=args.verbose))
        if profile.grade in ("F", "D"):
            return EXIT_INSUFFICIENT
        return EXIT_CAVEATS if profile.issues else EXIT_OK

    if args.command == "assess":
        result = assess(profile, args.question)
        print(to_json(result) if args.json else render_sufficiency(result, profile=profile))
        return {
            Verdict.ANSWERABLE: EXIT_OK,
            Verdict.PARTIAL: EXIT_CAVEATS,
            Verdict.INSUFFICIENT: EXIT_INSUFFICIENT,
        }[result.verdict]

    if args.command == "verify":
        answer = _read_answer(args)
        sufficiency = assess(profile, args.question) if args.question else None
        report = verify(profile, answer, sufficiency=sufficiency)
        print(to_json(report) if args.json else render_claims(report))
        return {"pass": EXIT_OK, "revise": EXIT_CAVEATS, "reject": EXIT_INSUFFICIENT}[report.verdict]

    if args.command == "improve":
        result = plan(profile, args.question)
        print(to_json(result) if args.json else render_plan(result))
        return EXIT_CAVEATS if result.steps else EXIT_OK

    if args.command == "kit":
        import json

        print(json.dumps(truth_kit(args.source, args.question, max_rows=args.max_rows,
                                   table=args.table), indent=2, ensure_ascii=False))
        return EXIT_OK

    if args.command == "gate":
        return _gate(profile, args)

    return EXIT_OK


def _gate(profile, args) -> int:
    from .models import Severity

    failures: list[str] = []

    if GRADE_ORDER.index(profile.grade) < GRADE_ORDER.index(args.min_grade):
        failures.append(f"data grade {profile.grade} is below the required {args.min_grade} "
                        f"(score {profile.score}/100)")

    threshold = Severity(args.max_severity).rank
    offending = [i for i in profile.issues if i.severity.rank >= threshold]
    if offending:
        failures.append(f"{len(offending)} issue(s) at severity {args.max_severity} or above:")
        failures.extend(f"    - [{i.severity.value}] {i.message}" for i in offending[:10])

    verdict = None
    if args.question:
        result = assess(profile, args.question)
        verdict = result.verdict
        if result.verdict is not Verdict.ANSWERABLE:
            failures.append(f"question is {result.verdict.value}: "
                            + (result.blockers[0].message if result.blockers
                               else f"confidence ceiling {result.confidence_ceiling:.0%}"))

    if args.json:
        print(to_json({
            "source": profile.source,
            "grade": profile.grade,
            "score": profile.score,
            "verdict": verdict.value if verdict else None,
            "passed": not failures,
            "failures": failures,
        }))
    elif failures:
        print(f"candor gate FAILED for {profile.source}", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
    else:
        print(f"candor gate passed for {profile.source} "
              f"(grade {profile.grade}, score {profile.score}/100)")

    if not failures:
        return EXIT_OK
    return EXIT_INSUFFICIENT if verdict is Verdict.INSUFFICIENT or profile.grade == "F" else EXIT_CAVEATS


if __name__ == "__main__":
    raise SystemExit(main())
