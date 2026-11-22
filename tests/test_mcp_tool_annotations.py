"""Wire-level contract tests for MCP tool behavior annotations."""

import pytest
from mcp import Client

from app.mcp_server import mcp


@pytest.mark.asyncio
async def test_read_only_tools_publish_explicit_behavior_annotations():
    async with Client(mcp) as client:
        result = await client.list_tools()

    tools = {tool.name: tool for tool in result.tools}
    assert set(tools) == {"search_products", "check_logistics"}

    for name, tool in tools.items():
        annotations = tool.annotations
        assert annotations is not None, name
        assert annotations.read_only_hint is True, name
        assert annotations.destructive_hint is False, name
        assert annotations.idempotent_hint is True, name
        assert annotations.open_world_hint is False, name
