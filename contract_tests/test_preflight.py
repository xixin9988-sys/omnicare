"""Startup self-check (R-01): unsafe auth combinations must fail before serving traffic."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.preflight import ConfigurationError, check_settings, enforce

LONG_SECRET = "s" * 32


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


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


def test_production_token_mode_requires_a_long_secret() -> None:
    report = check_settings(settings(app_env="production", auth_mode="token", auth_secret="short"))
    assert "weak_auth_secret" in error_codes(report)

    report = check_settings(
        settings(app_env="production", auth_mode="token", auth_secret=LONG_SECRET)
    )
    assert report.ok


def test_dev_identity_is_refused_outside_local_environments() -> None:
    report = check_settings(settings(app_env="staging", auth_mode="dev"))
    assert "dev_auth_outside_local" in error_codes(report)


def test_unknown_auth_mode_is_refused() -> None:
    report = check_settings(settings(app_env="local", auth_mode="oauth"))
    assert "unknown_auth_mode" in error_codes(report)


def test_sqlite_in_production_is_a_warning_not_a_blocker() -> None:
    report = check_settings(
        settings(
            app_env="production",
            auth_mode="token",
            auth_secret=LONG_SECRET,
            database_url="sqlite+aiosqlite:///./data/omnicare.db",
            mcp_auth_token="gateway",
        )
    )
    assert report.ok
    assert "sqlite_in_production" in {issue.code for issue in report.warnings}


def test_enforce_raises_with_the_report_attached() -> None:
    with pytest.raises(ConfigurationError) as error:
        enforce(settings(app_env="production", auth_mode="dev"))
    assert "insecure_auth_mode" in {issue.code for issue in error.value.report.errors}


def test_enforce_returns_report_when_safe() -> None:
    report = enforce(settings(app_env="local", auth_mode="dev"))
    assert report.ok
