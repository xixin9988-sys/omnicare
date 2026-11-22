"""Standalone MCP server for read-only, identity-scoped e-commerce tools.

Run: python -m app.mcp_server
Endpoint: http://127.0.0.1:9000/mcp (Streamable HTTP)

Callers authenticate with an OIDC/JWT access token in the ``Authorization: Bearer`` header;
tools take no token and no user id arguments - identity is supplied by the authenticated
transport and re-checked server-side. ``AUTH_MODE=dev`` (and the legacy ``token`` mode) keep
the unauthenticated local demo, but only while ``APP_ENV`` is local/dev/test; anything else
fails closed at server construction.
"""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import SessionLocal, init_db
from app.core.preflight import ConfigurationError, PreflightIssue, PreflightReport
from app.mcp_auth import (
    JWT_AUTH_MODES,
    LOCAL_FALLBACK_MODES,
    McpAuthError,
    TrustedIdentity,
    app_env,
    auth_mode,
    local_demo_allowed,
    required_mcp_scopes,
    resolve_dev_identity,
)
from app.mcp_token import OmniCareTokenVerifier, trusted_identity_from_access_token
from app.models.entities import Order, Product

SERVER_NAME = "OmniCare Business Tools"
# AuthSettings requires absolute URLs even when the token verifier itself pins the audience.
# Real deployments should set AUTH_JWT_ISSUER / MCP_RESOURCE_URL; these are local defaults.
DEFAULT_ISSUER_URL = "http://127.0.0.1:8000"
DEFAULT_RESOURCE_URL = "http://127.0.0.1:9000/mcp"

# These annotations describe the actual local-database behavior of the tools.
# They are client-facing hints, not authorization controls; server-side checks
# remain authoritative.
READ_ONLY_LOCAL_DATA = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def _configuration_error(code: str, message: str) -> ConfigurationError:
    return ConfigurationError(PreflightReport((PreflightIssue("error", code, message),)))


def _http_url(name: str, value: object, fallback: str) -> AnyHttpUrl:
    """Normalise a settings URL for AuthSettings, failing clearly when it is not a URL."""
    candidate = str(value or "").strip() or fallback
    try:
        return AnyHttpUrl(candidate)
    except (ValidationError, ValueError) as error:
        raise _configuration_error(
            "mcp_invalid_url",
            f"{name}={candidate!r} is not an absolute http(s) URL; set a URL such as {fallback!r}.",
        ) from error


def _authenticated_server(settings: Settings) -> MCPServer:
    """MCP server that authenticates every request as a Bearer JWT (jwt/oidc modes)."""
    server: MCPServer = MCPServer(
        SERVER_NAME,
        auth=AuthSettings(
            issuer_url=_http_url(
                "AUTH_JWT_ISSUER", getattr(settings, "auth_jwt_issuer", ""), DEFAULT_ISSUER_URL
            ),
            resource_server_url=_http_url(
                "MCP_RESOURCE_URL", getattr(settings, "mcp_resource_url", ""), DEFAULT_RESOURCE_URL
            ),
            required_scopes=list(required_mcp_scopes(settings)),
            validate_token_resource=False,
        ),
        token_verifier=OmniCareTokenVerifier(settings),
    )
    _register_tools(server, settings, authenticated=True)
    return server


def _local_demo_server(settings: Settings) -> MCPServer:
    """Unauthenticated MCP server for local demos only (dev/token modes)."""
    server: MCPServer = MCPServer(SERVER_NAME)
    _register_tools(server, settings, authenticated=False)
    return server


def create_server(settings: Settings) -> MCPServer:
    """Build the MCP server for settings.

    jwt/oidc -> the transport verifies a Bearer JWT on every request. dev/token -> the local
    demo identity, allowed only for APP_ENV local/dev/test. Any other combination raises
    ConfigurationError instead of silently serving an open endpoint.
    """
    mode = auth_mode(settings)
    if mode in JWT_AUTH_MODES:
        return _authenticated_server(settings)
    if mode in LOCAL_FALLBACK_MODES:
        if not local_demo_allowed(settings):
            raise _configuration_error(
                "mcp_requires_jwt_auth",
                "Serving MCP without JWT verification is only allowed for AUTH_MODE=dev/token "
                f"in APP_ENV local/dev/test; got AUTH_MODE={mode!r} "
                f"APP_ENV={app_env(settings)!r}. Set AUTH_MODE=jwt or oidc.",
            )
        return _local_demo_server(settings)
    raise _configuration_error(
        "unknown_auth_mode",
        f"AUTH_MODE={mode!r} is not supported by the MCP server; use one of dev, token, jwt, oidc.",
    )


def _register_tools(server: MCPServer, settings: Settings, *, authenticated: bool) -> None:
    """Register the read-only tools and bind them to the caller's trusted identity."""

    def caller_identity() -> TrustedIdentity:
        """Resolve the caller from the transport credential (or the local demo identity)."""
        if authenticated:
            return trusted_identity_from_access_token(get_access_token(), settings)
        return resolve_dev_identity(settings)

    @server.tool(annotations=READ_ONLY_LOCAL_DATA)
    async def search_products(query: str, ctx: Context) -> list[dict]:
        """Search active products by name keyword. Read-only local catalog lookup."""
        try:
            caller_identity()
        except McpAuthError as error:
            # Never echo the bearer token or headers; only the stable error code.
            return [{"ok": False, "error": error.code}]
        await init_db()
        async with SessionLocal() as db:
            rows = (
                await db.scalars(
                    select(Product)
                    .where(Product.active.is_(True), Product.name.contains(query))
                    .limit(8)
                )
            ).all()
        return [
            {"id": x.id, "sku": x.sku, "name": x.name, "price": str(x.price), "stock": x.stock}
            for x in rows
        ]

    @server.tool(annotations=READ_ONLY_LOCAL_DATA)
    async def check_logistics(order_id: str, ctx: Context) -> dict:
        """Check logistics for the authenticated caller's own order.

        Identity comes from the authenticated MCP transport (Authorization: Bearer JWT), never
        from a tool argument: the order is looked up by id and the caller's user id, so a
        caller cannot name another customer. Read-only local order lookup; no external
        carrier API is contacted.
        """
        try:
            identity = caller_identity()
        except McpAuthError as error:
            # Never echo the bearer token or headers; only the stable error code.
            return {"ok": False, "error": error.code}

        await init_db()
        async with SessionLocal() as db:
            order = await db.scalar(
                select(Order).where(Order.id == order_id, Order.user_id == identity.user_id)
            )
        if order is None:
            return {"ok": False, "error": "order_not_found_or_not_owned"}
        return {
            "ok": True,
            "order_id": order.id,
            "carrier": order.carrier,
            "tracking_no": order.tracking_no,
            "status": order.logistics_status,
        }


# Import-time construction must never raise for the default local/dev configuration: the
# annotation test and the CLI entry point both rely on this module-level server.
mcp = create_server(get_settings())


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=9000)
