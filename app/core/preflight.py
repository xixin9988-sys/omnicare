"""Startup configuration self-check.

The V-01 review found that a deployment could look "hardened" in code while still running with
AUTH_MODE=dev (identity taken from X-User-Id) because nothing refused that combination at boot.
This module turns those combinations into a hard failure before the first request.

Modes understood by the runtime:

* dev    - trust X-User-Id; local/test only, refused when APP_ENV=production.
* token  - legacy self-issued HMAC bearer tokens; refused in production (use jwt/oidc).
* jwt    - verify OIDC-style JWTs; needs strong key material (AUTH_SECRET >= MIN_SECRET_LENGTH
           for HS*, or a public key / JWKS URL for RS256/ES256).
* oidc   - like jwt; additionally requires AUTH_JWT_ISSUER and AUTH_JWT_AUDIENCE.

check_settings is pure and returns a report; enforce raises. Callers (app.main) only need enforce.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Minimum length for a shared HMAC secret in a non-local deployment.
MIN_SECRET_LENGTH = 32

_DEV_SAFE_ENVS = frozenset({"local", "dev", "test"})
_PRODUCTION_ENVS = frozenset({"production", "prod"})
#: Modes the runtime implements (mirrors app.core.jwt when crypto is imported).
SUPPORTED_AUTH_MODES = frozenset({"dev", "token", "jwt", "oidc"})
_HMAC_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
_ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "ES256"})
_JWT_ALGORITHMS = _HMAC_ALGORITHMS | _ASYMMETRIC_ALGORITHMS


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


def _text(settings, name: str, default: str = "") -> str:
    value = getattr(settings, name, default)
    return str(value or default).strip()


def _jwt_key_material(settings) -> tuple[str, str, bool]:
    """Return (algorithm, secret, has_asymmetric_material)."""
    algorithm = (_text(settings, "auth_jwt_algorithm", "HS256") or "HS256").upper()
    secret = _text(settings, "auth_secret")
    has_asymmetric_material = bool(
        _text(settings, "auth_jwt_public_key")
        or _text(settings, "auth_jwt_public_key_path")
        or _text(settings, "auth_jwks_url")
    )
    return algorithm, secret, has_asymmetric_material


def check_settings(settings) -> PreflightReport:
    """Return every problem with the auth/database configuration (never raises)."""
    issues: list[PreflightIssue] = []

    def issue(level: str, code: str, message: str) -> None:
        issues.append(PreflightIssue(level, code, message))

    def required_in_production(code: str, message: str) -> None:
        issue("error" if production else "warning", code, message)

    app_env = _text(settings, "app_env", "local").lower() or "local"
    auth_mode = _text(settings, "auth_mode", "dev").lower() or "dev"
    database_url = _text(settings, "database_url")
    production = app_env in _PRODUCTION_ENVS
    algorithm, secret, has_asymmetric_material = _jwt_key_material(settings)

    if auth_mode not in SUPPORTED_AUTH_MODES:
        issue(
            "error",
            "unknown_auth_mode",
            f"AUTH_MODE={auth_mode!r} is not supported; use one of {sorted(SUPPORTED_AUTH_MODES)}.",
        )

    if production and auth_mode == "dev":
        issue(
            "error",
            "insecure_auth_mode",
            "APP_ENV=production requires AUTH_MODE=jwt or oidc; X-User-Id must never be trusted.",
        )

    if production and auth_mode == "token":
        issue(
            "error",
            "legacy_token_mode_in_production",
            "AUTH_MODE=token is the legacy self-issued HMAC mode; production requires "
            "AUTH_MODE=jwt or oidc so tokens come from a verifiable issuer.",
        )

    if auth_mode == "dev" and app_env not in _DEV_SAFE_ENVS:
        issue(
            "error",
            "dev_auth_outside_local",
            f"AUTH_MODE=dev is only allowed for {sorted(_DEV_SAFE_ENVS)}, not {app_env!r}.",
        )

    if auth_mode in {"jwt", "oidc"}:
        if algorithm not in _JWT_ALGORITHMS:
            issue(
                "error",
                "unknown_jwt_algorithm",
                f"AUTH_JWT_ALGORITHM={algorithm!r} is not supported; use one of "
                f"{sorted(_JWT_ALGORITHMS)}.",
            )
        elif algorithm in _HMAC_ALGORITHMS and len(secret) < MIN_SECRET_LENGTH:
            required_in_production(
                "weak_auth_secret",
                f"AUTH_SECRET must be at least {MIN_SECRET_LENGTH} characters when "
                f"AUTH_JWT_ALGORITHM={algorithm}.",
            )
        elif algorithm in _ASYMMETRIC_ALGORITHMS and not has_asymmetric_material:
            required_in_production(
                "jwt_key_material_missing",
                f"AUTH_JWT_ALGORITHM={algorithm} requires AUTH_JWT_PUBLIC_KEY, "
                "AUTH_JWT_PUBLIC_KEY_PATH or AUTH_JWKS_URL.",
            )

        if auth_mode == "oidc":
            if not _text(settings, "auth_jwt_issuer"):
                required_in_production(
                    "oidc_missing_issuer",
                    "AUTH_MODE=oidc requires AUTH_JWT_ISSUER so tokens from other issuers are "
                    "rejected.",
                )
            if not _text(settings, "auth_jwt_audience"):
                required_in_production(
                    "oidc_missing_audience",
                    "AUTH_MODE=oidc requires AUTH_JWT_AUDIENCE so tokens for other audiences are "
                    "rejected.",
                )

    if auth_mode == "token" and len(secret) < MIN_SECRET_LENGTH:
        issue(
            "error" if production else "warning",
            "weak_auth_secret",
            f"AUTH_SECRET must be at least {MIN_SECRET_LENGTH} characters when AUTH_MODE=token.",
        )

    memory_backend = _text(settings, "memory_backend", "local").lower() or "local"
    if production and memory_backend == "local":
        issue(
            "warning",
            "local_memory_in_production",
            "MEMORY_BACKEND=local is the demo JSONL adapter: no tenant isolation, no screening, "
            "no trace ids. Use MEMORY_BACKEND=memguard_http in any real deployment.",
        )
    if memory_backend == "memguard_http" and not _text(settings, "memguard_service_key"):
        required_in_production(
            "memguard_service_key_missing",
            "MEMORY_BACKEND=memguard_http requires MEMGUARD_SERVICE_KEY so the gateway can "
            "authenticate this service.",
        )

    if production and database_url.startswith("sqlite"):
        issue(
            "warning",
            "sqlite_in_production",
            "SQLite has no row locks: concurrent refund approvals are only serialised by "
            "the transaction. Use PostgreSQL for production refunds.",
        )

    if auth_mode == "dev" and not getattr(settings, "admin_api_key", ""):
        issue(
            "warning",
            "demo_admin_without_key",
            "AUTH_MODE=dev with an empty ADMIN_API_KEY lets anyone on the loopback "
            "interface open the admin monitor socket.",
        )

    return PreflightReport(tuple(issues))


def enforce(settings) -> PreflightReport:
    """Raise ConfigurationError when the configuration is unsafe for this env."""
    report = check_settings(settings)
    if not report.ok:
        raise ConfigurationError(report)
    return report


__all__ = [
    "MIN_SECRET_LENGTH",
    "SUPPORTED_AUTH_MODES",
    "ConfigurationError",
    "PreflightIssue",
    "PreflightReport",
    "check_settings",
    "enforce",
]
