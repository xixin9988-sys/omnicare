"""Wire-level contract tests for MCP tool behavior annotations."""

import pytest
from mcp import Client

from app.core.config import Settings
from app.mcp_server import create_server

DEV_SETTINGS = {"_env_file": None, "app_env": "local", "auth_mode": "dev"}
JWT_SETTINGS = {
    "_env_file": None,
    "app_env": "test",
    "auth_mode": "jwt",
    "auth_secret": "annotation-test-secret-" + "x" * 32,
}


def test_server_is_built_from_explicit_settings():
    # The factory must build both modes; the annotations below must not depend on ambient env.
    assert create_server(Settings(**DEV_SETTINGS)) is not None
    assert create_server(Settings(**JWT_SETTINGS)) is not None


@pytest.mark.asyncio
async def test_read_only_tools_publish_explicit_behavior_annotations():
    async with Client(create_server(Settings(**DEV_SETTINGS))) as client:
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


@pytest.mark.asyncio
async def test_authenticated_server_keeps_the_same_annotations():
    async with Client(create_server(Settings(**JWT_SETTINGS))) as client:
        result = await client.list_tools()

    tools = {tool.name: tool for tool in result.tools}
    assert set(tools) == {"search_products", "check_logistics"}
    for name, tool in tools.items():
        assert tool.annotations is not None, name
        assert tool.annotations.read_only_hint is True, name
        assert tool.annotations.destructive_hint is False, name
        assert tool.annotations.idempotent_hint is True, name
        assert tool.annotations.open_world_hint is False, name
