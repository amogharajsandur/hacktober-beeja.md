"""Real MCP round-trips: in-process through the SDK client, and over stdio as a subprocess."""

import json
import sys

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import Client, StdioServerParameters  # noqa: E402

from beeja import storage  # noqa: E402
from beeja.mcp_server import build_server  # noqa: E402
from tests.test_retrieval_diff import DIFF_RELATED, DIFF_UNRELATED, _rec  # noqa: E402


@pytest.fixture
def store(tmp_path):
    storage.save(_rec(), tmp_path)
    return tmp_path


def _payload(result):
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


async def _calls(target):
    out = {}
    async with Client(target) as c:
        tools = await c.list_tools()
        out["tools"] = sorted(t.name for t in tools.tools)
        out["read_only"] = all(t.annotations and t.annotations.read_only_hint for t in tools.tools)
        out["search"] = _payload(await c.call_tool("search_decisions", {"query": "shared buffer"}))
        out["get"] = _payload(await c.call_tool("get_decision", {"ref": "dec-768#alt-1"}))
        out["missing"] = _payload(await c.call_tool("get_decision", {"ref": "dec-nope"}))
        out["why"] = _payload(await c.call_tool("why_not", {"idea": "single shared buffer"}))
        out["diff"] = _payload(await c.call_tool("check_diff", {"diff": DIFF_RELATED}))
        out["nodiff"] = _payload(await c.call_tool("check_diff", {"diff": DIFF_UNRELATED}))
    return out


def _check(out):
    assert out["tools"] == ["check_diff", "get_decision", "search_decisions", "why_not"]
    assert out["read_only"]
    assert out["search"]["results"][0]["alternative_id"] == "alt-1"
    assert "untrusted" in out["search"]["note"].lower()
    assert out["get"]["alternative"]["status"] == "rejected"
    assert "error" in out["missing"]
    assert out["why"]["answers"][0]["reasons"][0]["verified"] is True
    assert out["diff"]["advisories"][0]["decision_id"] == "dec-768"
    assert out["nodiff"]["advisories"] == []


def test_mcp_in_process(store):
    _check(anyio.run(_calls, build_server(store)))


def test_mcp_over_stdio_subprocess(store):
    params = StdioServerParameters(command=sys.executable, args=["-m", "beeja.mcp_server", "--store", str(store)])
    _check(anyio.run(_calls, params))
