"""MCP surface.

The server itself needs a live client, so these tests pin the contract the
harness actually consumes: the tool set, and the shape of each payload.
"""

import json

import pytest

mcp = pytest.importorskip("mcp", reason="the mcp extra is not installed")

from candor import assess, profile  # noqa: E402
from candor.mcp_server import _summarise_profile, _summarise_sufficiency, build_server  # noqa: E402
from helpers import NOW  # noqa: E402


def _payload(result):
    """Unwrap a tool result across MCP SDK versions (CallToolResult vs a list)."""
    content = getattr(result, "content", result)
    if isinstance(content, tuple):
        content = content[0]
    block = content[0] if isinstance(content, list) else content
    return json.loads(block.text)


EXPECTED_TOOLS = {
    "candor_assess", "candor_verify", "candor_profile", "candor_improve", "candor_kit",
}


@pytest.mark.anyio
async def test_the_advertised_tool_set_is_stable():
    tools = await build_server().list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


@pytest.mark.anyio
async def test_every_tool_documents_itself():
    for tool in await build_server().list_tools():
        assert tool.description and len(tool.description) > 80, tool.name
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", {})
        assert "source" in (schema.get("properties") or {}), tool.name


@pytest.mark.anyio
async def test_assess_tool_returns_the_honest_response(clean_csv):
    result = await build_server().call_tool(
        "candor_assess",
        {"source": str(clean_csv), "question": "What is the average NPS score?"},
    )
    payload = _payload(result)
    assert payload["verdict"] == "insufficient"
    assert payload["honest_response"]
    assert payload["to_make_answerable"]


@pytest.mark.anyio
async def test_an_unreadable_source_is_an_answer_not_a_crash():
    result = await build_server().call_tool(
        "candor_assess", {"source": "/nonexistent/nope.csv", "question": "anything?"},
    )
    payload = _payload(result)
    assert payload["verdict"] == "insufficient"
    assert "could not read" in payload["honest_response"]


def test_profile_summary_is_small_enough_to_spend_context_on(messy_csv):
    summary = _summarise_profile(profile(messy_csv, now=NOW))
    assert set(summary) >= {"rows", "columns", "issues", "grade", "score"}
    # No raw rows, no per-value dumps — an agent pays for every token of this.
    assert "rows_data" not in summary
    assert len(json.dumps(summary)) < 20_000


def test_sufficiency_summary_names_the_fields_an_agent_acts_on(clean_csv):
    prof = profile(clean_csv, now=NOW)
    summary = _summarise_sufficiency(assess(prof, "Why did revenue fall?", now=NOW))
    assert set(summary) >= {
        "verdict", "confidence_ceiling", "must_say", "must_not_claim",
        "cannot_answer", "to_make_answerable", "honest_response",
    }


def test_instructions_tell_the_agent_when_to_refuse():
    from candor.mcp_server import INSTRUCTIONS

    assert "insufficient" in INSTRUCTIONS
    assert "honest_response" in INSTRUCTIONS
