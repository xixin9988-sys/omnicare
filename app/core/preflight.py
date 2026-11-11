"""Startup configuration self-check.

The V-01 review found that a deployment could look "hardened" in code while still running with
``AUTH_MODE=dev`` (identity taken from ``X-User-Id``) because nothing refused that combination
at boot. This module turns those combinations into a hard failure before the first request.

``check_settings`` is pure and returns a report; ``enforce`` raises. Callers (``app.main``)
only need :func:`enforce`.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Minimum length for a shared HMAC secret in a non-local deployment.
MIN_SECRET_LENGTH = 32

_DEV_SAFE_ENVS = frozenset({"local", "dev", "test"})


class ConfigurationError(RuntimeError):
    """Raised when the process must refuse to start."""

    def __init__(self, report: PreflightReport) -> None:
        super().__init__("; ".join(f"{issue.code}: {issue.message}" for issue in report.errors))
        self.report = report


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    level: str  # "error" | "warning"
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    issues: tuple[PreflightIssue, ...] = ()

    @property
    def errors(self) -> tuple[PreflightIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "error")

    @property
    def warnings(self) -> tuple[PreflightIssue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors


def check_settings(settings) -> PreflightReport:
    """Return every problem with the auth/database configuration (never raises)."""
    issues: list[PreflightIssue] = []
    app_env = str(getattr(settings, "app_env", "") or "").lower() or "local"
    auth_mode = str(getattr(settings, "auth_mode", "") or "").lower() or "dev"
    secret = str(getattr(settings, "auth_secret", "") or "")
    database_url = str(getattr(settings, "database_url", "") or "")
    mcp_token = str(getattr(settings, "mcp_auth_token", "") or "")
    production = app_env in {"production", "prod"}

    if auth_mode not in {"dev", "token"}:
        issues.append(
            PreflightIssue(
                "error",
                "unknown_auth_mode",
                f"AUTH_MODE={auth_mode!r} is not supported; use 'dev' or 'token'.",
            )
        )

    if production and auth_mode != "token":
        issues.append(
            PreflightIssue(
                "error",
                "insecure_auth_mode",
                "APP_ENV=production requires AUTH_MODE=token; X-User-Id must never be trusted.",
            )
        )

    if auth_mode == "dev" and app_env not in _DEV_SAFE_ENVS:
        issues.append(
            PreflightIssue(
                "error",
                "dev_auth_outside_local",
                f"AUTH_MODE=dev is only allowed for {sorted(_DEV_SAFE_ENVS)}, not {app_env!r}.",
            )
        )

    if auth_mode == "token" and len(secret) < MIN_SECRET_LENGTH:
        level = "error" if production else "warning"
        issues.append(
            PreflightIssue(
                level,
                "weak_auth_secret",
                f"AUTH_SECRET must be at least {MIN_SECRET_LENGTH} characters when AUTH_MODE=token.",
            )
        )

    if production and database_url.startswith("sqlite"):
        issues.append(
            PreflightIssue(
                "warning",
                "sqlite_in_production",
                "SQLite has no row locks: concurrent refund approvals are only serialised by "
                "the transaction. Use PostgreSQL for production refunds.",
            )
        )

    if production and not mcp_token:
        issues.append(
            PreflightIssue(
                "warning",
                "mcp_gateway_token_missing",
                "MCP_AUTH_TOKEN is empty; the MCP endpoint relies on user tokens alone.",
            )
        )

    if auth_mode == "dev" and not getattr(settings, "admin_api_key", ""):
        issues.append(
            PreflightIssue(
                "warning",
                "demo_admin_without_key",
                "AUTH_MODE=dev with an empty ADMIN_API_KEY lets anyone on the loopback "
                "interface open the admin monitor socket.",
            )
        )

    return PreflightReport(tuple(issues))


def enforce(settings) -> PreflightReport:
    """Raise :class:`ConfigurationError` when the configuration is unsafe for this env."""
    report = check_settings(settings)
    if not report.ok:
        raise ConfigurationError(report)
    return report


__all__ = [
    "MIN_SECRET_LENGTH",
    "ConfigurationError",
    "PreflightIssue",
    "PreflightReport",
    "check_settings",
    "enforce",
]
