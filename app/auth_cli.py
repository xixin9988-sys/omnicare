"""Issue a short-lived bearer token for local / manual testing.

Usage::

    python -m app.auth_cli issue-token --user alice --role user \
        --scopes "omnicare.read products:read" --ttl 3600

The command reads the same settings as the API (environment variables and .env). It refuses to
run when APP_ENV=production: in production tokens are minted by the identity provider, never by
a CLI on the application host. The token is the only thing printed to stdout; errors go to
stderr, and the token itself is never logged.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from app.core.config import get_settings
from app.core.jwt import JwtError, issue_jwt
from app.core.security import AuthError, issue_token

_PRODUCTION_ENVS = frozenset({"production", "prod"})
_JWT_MODES = frozenset({"jwt", "oidc"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.auth_cli",
        description="Issue a short-lived bearer token for OmniCare (never run in production).",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    issue = subcommands.add_parser(
        "issue-token", help="issue a bearer token for local or manual testing"
    )
    issue.add_argument("--user", required=True, help="subject (user id) the token identifies")
    issue.add_argument("--role", default="user", choices=("user", "admin"))
    issue.add_argument("--scopes", default="", help="space/comma separated scopes")
    issue.add_argument(
        "--tenant", default="", help="tenant id (default: settings.default_tenant_id)"
    )
    issue.add_argument("--ttl", type=int, default=None, help="token lifetime in seconds")
    issue.add_argument("--jti", default="", help="explicit jti (default: generated)")
    return parser


def _scopes(raw: str) -> list[str]:
    return [item for item in raw.replace(",", " ").split() if item]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()

    if str(settings.app_env or "").strip().lower() in _PRODUCTION_ENVS:
        print(
            "refusing to issue tokens when APP_ENV=production; use the identity provider",
            file=sys.stderr,
        )
        return 2

    mode = (str(settings.auth_mode or "dev")).strip().lower() or "dev"
    scopes = _scopes(args.scopes)
    try:
        if mode in _JWT_MODES:
            token = issue_jwt(
                args.user,
                settings=settings,
                role=args.role,
                tenant_id=args.tenant or None,
                scopes=scopes,
                ttl_seconds=args.ttl,
                jti=args.jti or None,
            )
        else:
            # dev / legacy token mode: the API still verifies self-issued HMAC tokens.
            token = issue_token(
                args.user,
                secret=settings.auth_secret,
                role=args.role,
                tenant_id=args.tenant or settings.default_tenant_id,
                ttl_seconds=args.ttl or settings.auth_token_ttl_seconds,
            )
    except (JwtError, AuthError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
