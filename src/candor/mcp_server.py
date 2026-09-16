"""MCP server — the interface an existing harness plugs into.

Register it once and any MCP-capable agent (Claude Code, Claude Desktop, Cline,
Continue, an Agent SDK loop) gets five tools that make dishonesty harder:

    candor_assess   before answering: may I answer this at all?
    candor_verify   before sending: does my draft go beyond the data?
    candor_profile  what is actually in this file, and how good is it?
    candor_improve  what should be fixed first?
    candor_kit      the compact context block, for prompt injection

Run with `candor mcp`, or `candor-mcp`.
"""

from __future__ import annotations

import functools
import json
from typing import Any

from . import truth_kit as _truth_kit
from .assess import assess as _assess
from .improve import plan as _plan
from .profiler import profile as _profile
from .sources import SourceError
from .verify import verify as _verify

INSTRUCTIONS = """\
Use candor whenever you are about to answer a question from a data file.

Call candor_assess BEFORE you analyse. If the verdict is 'insufficient', reply
with the honest_response it gives you rather than producing a number. If the
verdict is 'partial', you may answer, but every sentence in must_say has to
appear in your answer, in your own words, in the body — not as a footnote — and
you must make none of the claims in must_not_claim.

Call candor_verify on your draft before you send it. If the verdict is
'reject', do not send that draft.
"""


def _summarise_profile(profile) -> dict[str, Any]:
    """Trim a profile down to what is worth spending an agent's context on."""
    return {
        "source": profile.source,
        "format": profile.format,
        "rows": profile.row_count,
        "columns": [
            {
                "name": c.name,
                "type": c.inferred_type,
                "semantic_type": c.semantic_type,
                "fill_rate": round(c.completeness, 3),
                "distinct": c.distinct,
                "grade": c.grade,
                "examples": [v for v, _ in c.top_values[:3]],
                **({"range": [c.temporal.min, c.temporal.max]} if c.temporal else {}),
                **({"min": c.numeric.min, "max": c.numeric.max, "median": c.numeric.median}
                   if c.numeric else {}),
            }
            for c in profile.columns
        ],
        "duplicate_rows": profile.duplicate_rows,
        "truncated": profile.truncated,
        "score": profile.score,
        "grade": profile.grade,
        "dimension_scores": profile.scores,
        "issues": [
            {
                "severity": i.severity.value,
                "dimension": i.dimension.value,
                "column": i.column,
                "problem": i.message,
                "why_it_matters": i.impact,
                "fix": i.fix,
                "effort": i.effort.value,
            }
            for i in profile.issues
        ],
        "read_notes": profile.notes,
    }


def _summarise_sufficiency(result) -> dict[str, Any]:
    return {
        "verdict": result.verdict.value,
        "confidence_ceiling": result.confidence_ceiling,
        "usable_rows": result.usable_rows,
        "question_read_as": result.intents,
        "fields_matched": result.resolved,
        "terms_with_no_field": result.unresolved_terms,
        "cannot_answer": [
            {"problem": g.message, "needs": g.need, "fix": g.fix} for g in result.blockers
        ],
        "must_say": result.caveat_texts,
        "must_not_claim": result.forbidden_claims,
        "to_make_answerable": result.unlock,
        "honest_response": result.honest_response,
    }


def _server_class():
    """The SDK renamed FastMCP to MCPServer in 2.0; support both."""
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP

        return FastMCP
    except ImportError as exc:
        raise SystemExit(
            "the MCP server needs the 'mcp' package — install candor with the mcp extra:\n"
            "    uv pip install 'candor[mcp]'"
        ) from exc


def build_server():
    server = _server_class()("candor", instructions=INSTRUCTIONS)

    def _guard(fn):
        """Turn an unreadable source into an answer, not a stack trace.

        functools.wraps matters here beyond tidiness: the SDK builds each tool's
        input schema from the wrapped function's signature, and a bare
        (*args, **kwargs) wrapper would erase it.
        """

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return json.dumps(fn(*args, **kwargs), indent=2, default=str, ensure_ascii=False)
            except SourceError as exc:
                return json.dumps({
                    "error": str(exc),
                    "verdict": "insufficient",
                    "honest_response": f"I could not read the data: {exc}. I won't guess at an "
                                       "answer without it.",
                }, indent=2)

        return wrapped

    @server.tool()
    @_guard
    def candor_assess(source: str, question: str, max_rows: int = 200_000,
                      table: str | None = None) -> dict:
        """Decide whether a question can be answered honestly from a data file.

        Call this BEFORE analysing. Returns a verdict (answerable / partial /
        insufficient), a confidence ceiling, the caveats the answer must state,
        the claims it must not make, and — when the answer is no — the exact
        words to say instead.

        Args:
            source: path to a csv, tsv, json, jsonl, sqlite or parquet file.
            question: the question, in natural language, exactly as asked.
            max_rows: stop reading after this many rows.
            table: table name, for sqlite sources with more than one table.
        """
        profile = _profile(source, max_rows=max_rows, table=table)
        result = _assess(profile, question)
        return {
            "source": profile.source,
            "rows": profile.row_count,
            "data_grade": profile.grade,
            **_summarise_sufficiency(result),
        }

    @server.tool()
    @_guard
    def candor_verify(source: str, draft_answer: str, question: str | None = None,
                      max_rows: int = 200_000, table: str | None = None) -> dict:
        """Check a draft answer for claims the data does not support.

        Call this BEFORE sending an answer that cites data. Catches invented
        counts, false precision, phantom fields, causal language over
        observational data, unbacked forecasts, absolutes contradicted by
        missing values, and caveats that were dropped from the draft.

        Args:
            source: path to the data file the answer is based on.
            draft_answer: the answer text you are about to send.
            question: the original question, so required caveats can be checked.
            max_rows: stop reading after this many rows.
            table: table name, for sqlite sources.
        """
        profile = _profile(source, max_rows=max_rows, table=table)
        sufficiency = _assess(profile, question) if question else None
        report = _verify(profile, draft_answer, sufficiency=sufficiency)
        return {
            "verdict": report.verdict,
            "honesty_score": report.honesty_score,
            "numeric_claims_checked": report.checked_claims,
            "findings": [
                {
                    "severity": f.severity.value,
                    "problem": f.message,
                    "quote": f.quote,
                    "fix": f.suggestion,
                }
                for f in report.findings
            ],
            "guidance": (
                "Do not send this draft; rewrite it to remove the findings above."
                if report.verdict == "reject"
                else "Revise the flagged sentences before sending."
                if report.verdict == "revise"
                else "Nothing in this draft goes beyond the data."
            ),
        }

    @server.tool()
    @_guard
    def candor_profile(source: str, max_rows: int = 200_000,
                       table: str | None = None) -> dict:
        """Measure what is in a data file and how trustworthy it is.

        Returns the schema as actually observed, per-column fill rates and
        grades, and every quality defect found — each with why it matters for
        answering questions and how to fix it.

        Args:
            source: path to a csv, tsv, json, jsonl, sqlite or parquet file.
            max_rows: stop reading after this many rows.
            table: table name, for sqlite sources.
        """
        return _summarise_profile(_profile(source, max_rows=max_rows, table=table))

    @server.tool()
    @_guard
    def candor_improve(source: str, questions: list[str] | None = None,
                       max_rows: int = 200_000, table: str | None = None) -> dict:
        """Rank what to fix in a dataset, highest value per unit of effort first.

        Pass the questions the data is supposed to answer and each fix is
        annotated with which of them it unblocks, which turns an abstract
        quality backlog into a prioritised one.

        Args:
            source: path to the data file.
            questions: questions the data must be able to answer.
            max_rows: stop reading after this many rows.
            table: table name, for sqlite sources.
        """
        profile = _profile(source, max_rows=max_rows, table=table)
        result = _plan(profile, questions or [])
        return {
            "source": result.source,
            "current_score": result.current_score,
            "current_grade": result.current_grade,
            "projected_score_after_top_fixes": result.projected_score,
            "steps": [
                {
                    "rank": s.rank,
                    "what": s.title,
                    "fix": s.fix,
                    "columns": s.columns,
                    "severity": s.severity.value,
                    "effort": s.effort.value,
                    "value_score": s.value,
                    "unblocks_questions": s.unlocks,
                }
                for s in result.steps
            ],
        }

    @server.tool()
    @_guard
    def candor_kit(source: str, question: str, max_rows: int = 200_000,
                   table: str | None = None) -> dict:
        """The compact honesty block for a question, sized for a system prompt.

        Everything candor_assess returns, trimmed to the fields that change what
        you should say: verdict, confidence ceiling, must_say, must_not_claim,
        and the honest response for when the data cannot carry the question.

        Args:
            source: path to the data file.
            question: the question being asked.
            max_rows: stop reading after this many rows.
            table: table name, for sqlite sources.
        """
        return _truth_kit(source, question, max_rows=max_rows, table=table)

    return server


def main() -> int:  # pragma: no cover - needs a live stdio client
    build_server().run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
