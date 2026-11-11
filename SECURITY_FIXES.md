# Security fixes — audit response (V-01 … V-08)

Source: external static audit against `main` @ `c26943f`. Each item lists the change, where it
lives, and what remains open. Nothing here is claimed as production-grade authentication.

## V-01 — identity was self-declared (P0)

- `app/core/security.py`: HMAC-SHA256 bearer tokens (`issue_token` / `verify_token`), constant-time
  comparison, expiry; `Principal` carries `user_id`, `role`, `tenant_id`.
- `app/api/deps.py`: `get_principal` verifies the token; `AUTH_MODE=dev` trusts `X-User-Id` only
  when `APP_ENV!=production`; admin elevation needs `X-Admin-Key` matching `ADMIN_API_KEY`.
- `app/api/routes.py`: `/sessions` and `/chat` take the user from the principal and reject a
  mismatched body `user_id` with `403 user_id_mismatch`.

Residual: token issuance/rotation/revocation is not implemented (no IdP); `tenant_id` is carried
but not enforced (single-tenant MVP).

## V-02 / V-04 — refund balance and duplicate requests (P0/P1)

- `app/services/refunds.py`: `available_refund` = `total − refunded − open_requests`, where open
  requests are `pending_review` + `approved`. Used by **both** the guard and the tool so the two
  checks cannot drift.
- `app/governance/local_guard.py`: the order is read `with_for_update()` before the balance check.
- `app/tools/ecommerce.py`: locks the order, re-checks the authoritative balance, supports an
  `idempotency_key`, returns the existing request on a duplicate instead of creating another.
- `app/models/entities.py`: `refund_requests.idempotency_key` unique (nullable).
- `app/services/refund_state.py`: `refund_fingerprint(order, amount, reason)` — the dedupe key is
  derived **server-side**; a retry that omits `idempotency_key` still collapses onto the open
  request. `refund_requests.fingerprint` + partial unique index `uq_refund_open_fingerprint`
  (`status IN ('pending_review','approved')`) makes the database, not the application, win races.

Residual: SQLite ignores `FOR UPDATE` (locks are database-wide); the PostgreSQL path is exercised
by the CI `full-suite` job (PostgreSQL 16 service) and by `tests/test_refund_concurrency_pg.py`.

## V-03 — global monitoring data (P0)

- `/metrics/summary`, `/events`, `/governance/incidents` now require the `admin` role via
  `require_admin`. `/health` and `/products` remain public.

Residual: no per-tenant filtering; a future admin UI must still scope by tenant when multi-tenant
mode lands.

## V-05 — memory file collision (P1)

- `app/memory/local_adapter.py`: file name is `sha256(user_id)[:32].jsonl`; sanitising is gone.

Residual: existing files written under the old sanitised names are not migrated; a one-off
migration is required before deploying over an existing `data/memory/`.

## V-06 — MCP demo identity (P1)

- Superseded by R-02 below: `check_logistics(order_id, user_token, gateway_token)` derives the
  customer from a signed token; there is no `user_id` argument any more.

## V-07 — WebSocket broadcast (P2)

- `app/api/routes.py`: `/ws/monitor` authenticates before connecting (bearer token or admin key in
  dev) and closes with `1008` otherwise; `app/core/events.py` caps connections at 50 and
  `connect()` returns `False` when full (`1013`).

Residual: one admin channel, no per-user subscription filtering. Multi-instance needs Redis/NATS.

## V-08 — CI hid failures (P1, quality gate)

- `.github/workflows/ci.yml`: `continue-on-error` removed from the full-suite job; it is now
  blocking.
- The misleading job name `full suite (non-blocking until Phase 1.5)` is gone: the job is
  `full suite (blocking)` and runs the suite twice, once on SQLite and once against a
  PostgreSQL 16 service (`DATABASE_URL=postgresql+asyncpg://…`).

Residual: CI is the only place the SQLAlchemy-dependent suite runs; the local environment cannot
import it (no `sqlalchemy`/`aiosqlite` in the NanoMemory venv).

# Second review round (R-01 … R-04)

Source: follow-up review of the V-01…V-08 pass. Each item lists the change and what is **not**
verified.

## R-01 — default Docker deployment could still forge identity (P0)

- `app/core/preflight.py`: `check_settings` / `enforce`, called at import time from `app/main.py`.
  Hard failures: `APP_ENV=production` + `AUTH_MODE=dev` (`insecure_auth_mode`), `AUTH_MODE=dev`
  outside `local/dev/test` (`dev_auth_outside_local`), `AUTH_MODE=token` with a secret shorter
  than 32 characters in production (`weak_auth_secret`), unknown `AUTH_MODE`.
- `docker-compose.yml`: API published on `127.0.0.1:8000` only — the dev identity header is not
  reachable from the LAN.
- `docker-compose.prod.yml`: `APP_ENV=production`, `AUTH_MODE=token`, and `AUTH_SECRET` /
  `ADMIN_API_KEY` / `MCP_AUTH_TOKEN` / `POSTGRES_PASSWORD` are `${VAR:?…}` so compose refuses to
  start without them; PostgreSQL 16 with a healthcheck.
- `.env.example`: documents that `AUTH_MODE=dev` is loopback-only and that the secret needs 32+
  characters.

Residual: this is configuration hardening, not an IdP — token issuance/rotation/revocation is
still the caller's responsibility.

## R-02 — MCP shared token did not identify a customer (P1)

- `app/mcp_auth.py`: `resolve_identity` derives the customer from a signed user token
  (`verify_token`); the gateway token (`MCP_AUTH_TOKEN`) is transport authentication only and can
  no longer stand in for identity. `require_owner` checks order ownership.
- `app/mcp_server.py`: `check_logistics(order_id, user_token, gateway_token)` — the `user_id`
  parameter is gone, so a caller cannot name another customer.

Residual: the user token still travels as a tool argument; terminating auth at an MCP gateway and
forwarding a verified identity is the next step.

## R-03 — refund idempotency key was optional (P1)

Covered with V-02/V-04 above: the key is server-derived (`refund_fingerprint`) and the partial
unique index is the last line of defence.

## R-04 — PostgreSQL row-lock path was never exercised (P1)

- `tests/test_refund_concurrency_pg.py`: two sessions approve two 60.00 requests on a 100.00
  order concurrently; exactly one must win. Skipped on SQLite, run by CI's PostgreSQL pass.
- `pyproject.toml`: `asyncpg` dependency added; SQLite in production is a preflight warning
  (`sqlite_in_production`).

## Refund approval state machine (P0-1)

- `app/services/refund_state.py` (pure): `pending_review → approved | rejected`,
  `approved → settled`; four-eyes rule (`requester_cannot_decide`), terminal statuses, and the
  decision effects (`books_money` only on settle, `releases_quota` on reject).
- `app/services/refund_approval.py`: locks the request and the order, re-checks the authoritative
  balance excluding the request being decided, writes the status change and a `refund_audit` row
  in the caller's transaction.
- `app/api/routes.py`: `POST /api/v1/refunds/{id}/decision` (admin), `GET /api/v1/refunds`
  (admin), `GET /api/v1/refunds/{id}/audit` (admin), `GET /api/v1/refunds/mine` (own rows only).
- `app/models/entities.py`: `decided_by` / `decided_at` / `decision_reason` / `fingerprint` and the
  append-only `refund_audit` table.

Residual: settling books `orders.refunded_amount` but there is still no payment-gateway call; the
settle step is what a future payout worker would pick up.

## New tests

| Test file | Runs where | Covers |
| --- | --- | --- |
| `contract_tests/test_security_units.py` | no DB | token round-trip/tamper/expiry, WS auth matrix, memory-path collision |
| `contract_tests/test_refund_state.py` | no DB | every legal/illegal refund edge, four-eyes, terminal states, fingerprint |
| `contract_tests/test_preflight.py` | no DB | production+dev refused, weak secret, env matrix, warnings |
| `contract_tests/test_mcp_identity.py` | no DB | identity from signed token, gateway token is not identity, owner check |
| `tests/test_security_api.py` | CI (full deps) | user-id spoofing rejected, admin endpoints 403 |
| `tests/test_refund_approval.py` | SQLite + PG | approve/reject/settle, audit trail, four-eyes, re-check, admin HTTP |
| `tests/test_refund_concurrency_pg.py` | PostgreSQL only | two concurrent approvals cannot double-spend |

Local evidence for this change set: `pytest contract_tests -q` → **48 passed**; `ruff check .` and
`ruff format --check .` clean. Not verified locally: the SQLAlchemy-dependent `tests/` suite
(SQLite/PostgreSQL), Docker image, and the compose overlays.
