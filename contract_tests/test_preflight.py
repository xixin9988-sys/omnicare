"""Startup self-check (R-01): unsafe auth combinations must fail before serving traffic."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.preflight import ConfigurationError, check_settings, enforce

LONG_SECRET = "s" * 64
PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----\nplaceholder\n-----END PUBLIC KEY-----"


def settings(**overrides) -> Settings:
    base = {"_env_file": None, "app_env": "local", "auth_mode": "dev"}
    base.update(overrides)
    return Settings(**base)


def issue_codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def error_codes(report) -> set[str]:
    return {issue.code for issue in report.errors}


def test_local_dev_default_is_allowed() -> None:
    report = check_settings(settings(app_env="local", auth_mode="dev"))
    assert report.ok
    assert not report.errors


def test_production_rejects_dev_identity() -> None:
    report = check_settings(settings(app_env="production", auth_mode="dev"))
    assert not report.ok
    assert "insecure_auth_mode" in error_codes(report)


def test_production_rejects_the_legacy_token_mode() -> None:
    report = check_settings(
        settings(app_env="production", auth_mode="token", auth_secret=LONG_SECRET)
    )
    assert not report.ok
    assert "legacy_token_mode_in_production" in error_codes(report)


def test_production_jwt_requires_a_long_hmac_secret() -> None:
    weak = check_settings(settings(app_env="production", auth_mode="jwt", auth_secret="short"))
    assert "weak_auth_secret" in error_codes(weak)

    strong = check_settings(
        settings(app_env="production", auth_mode="jwt", auth_secret=LONG_SECRET)
    )
    assert strong.ok


def test_production_jwt_asymmetric_requires_key_material() -> None:
    missing = check_settings(
        settings(app_env="production", auth_mode="jwt", auth_jwt_algorithm="RS256")
    )
    assert "jwt_key_material_missing" in error_codes(missing)

    inline = check_settings(
        settings(
            app_env="production",
            auth_mode="jwt",
            auth_jwt_algorithm="RS256",
            auth_jwt_public_key=PUBLIC_KEY,
        )
    )
    assert inline.ok

    jwks = check_settings(
        settings(
            app_env="production",
            auth_mode="jwt",
            auth_jwt_algorithm="RS256",
            auth_jwks_url="https://issuer.example/.well-known/jwks.json",
        )
    )
    assert jwks.ok


def test_production_oidc_requires_issuer_and_audience() -> None:
    incomplete = check_settings(
        settings(
            app_env="production", auth_mode="oidc", auth_secret=LONG_SECRET, auth_jwt_issuer=""
        )
    )
    assert {"oidc_missing_issuer", "oidc_missing_audience"} <= error_codes(incomplete)

    complete = check_settings(
        settings(
            app_env="production",
            auth_mode="oidc",
            auth_secret=LONG_SECRET,
            auth_jwt_issuer="https://issuer.example",
            auth_jwt_audience="omnicare-api",
        )
    )
    assert complete.ok


def test_unknown_auth_mode_is_refused() -> None:
    report = check_settings(settings(app_env="local", auth_mode="oauth"))
    assert "unknown_auth_mode" in error_codes(report)


def test_unknown_jwt_algorithm_is_refused() -> None:
    report = check_settings(
        settings(
            app_env="local", auth_mode="jwt", auth_secret=LONG_SECRET, auth_jwt_algorithm="HS1024"
        )
    )
    assert "unknown_jwt_algorithm" in error_codes(report)


def test_dev_identity_is_refused_outside_local_environments() -> None:
    report = check_settings(settings(app_env="staging", auth_mode="dev"))
    assert "dev_auth_outside_local" in error_codes(report)


def test_local_jwt_without_key_material_only_warns() -> None:
    report = check_settings(settings(app_env="local", auth_mode="jwt", auth_secret=""))
    assert report.ok
    assert "weak_auth_secret" in {issue.code for issue in report.warnings}


def test_local_oidc_missing_issuer_and_audience_only_warns() -> None:
    report = check_settings(settings(app_env="local", auth_mode="oidc", auth_secret=LONG_SECRET))
    assert report.ok
    assert {"oidc_missing_issuer", "oidc_missing_audience"} <= issue_codes(report)


def test_sqlite_in_production_is_a_warning_not_a_blocker() -> None:
    report = check_settings(
        settings(
            app_env="production",
            auth_mode="jwt",
            auth_secret=LONG_SECRET,
            database_url="sqlite+aiosqlite:///./data/omnicare.db",
        )
    )
    assert report.ok
    assert "sqlite_in_production" in {issue.code for issue in report.warnings}


def test_mcp_gateway_token_warning_is_gone() -> None:
    report = check_settings(
        settings(app_env="production", auth_mode="jwt", auth_secret=LONG_SECRET, mcp_auth_token="")
    )
    assert "mcp_gateway_token_missing" not in issue_codes(report)


def test_dev_admin_key_warning_is_kept() -> None:
    report = check_settings(settings(app_env="local", auth_mode="dev", admin_api_key=""))
    assert report.ok
    assert "demo_admin_without_key" in {issue.code for issue in report.warnings}


def test_enforce_raises_with_the_report_attached() -> None:
    with pytest.raises(ConfigurationError) as error:
        enforce(settings(app_env="production", auth_mode="dev"))
    assert "insecure_auth_mode" in {issue.code for issue in error.value.report.errors}


def test_enforce_returns_report_when_safe() -> None:
    report = enforce(settings(app_env="local", auth_mode="dev"))
    assert report.ok
