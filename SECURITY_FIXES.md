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

Residual: SQLite ignores `FOR UPDATE` (locks are database-wide); concurrent correctness should be
re-verified on PostgreSQL. The approval state machine (`pending_review → approved/rejected`) is
still not implemented.

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

- `app/mcp_server.py`: `check_logistics(order_id, user_id, auth_token)`; the order must belong to
  `user_id`, and `auth_token` must match `MCP_AUTH_TOKEN` in constant time. If the token is unset,
  the tool refuses outside `APP_ENV=local`.

Residual: passing the token as a tool argument is weaker than transport-level auth; production
should terminate auth at the MCP gateway and forward a verified identity.

## V-07 — WebSocket broadcast (P2)

- `app/api/routes.py`: `/ws/monitor` authenticates before connecting (bearer token or admin key in
  dev) and closes with `1008` otherwise; `app/core/events.py` caps connections at 50 and
  `connect()` returns `False` when full (`1013`).

Residual: one admin channel, no per-user subscription filtering. Multi-instance needs Redis/NATS.

## V-08 — CI hid failures (P1, quality gate)

- `.github/workflows/ci.yml`: `continue-on-error` removed from the full-suite job; it is now
  blocking. `contracts` and `ruff` were already blocking.

Residual: the full suite has not been executed in this environment (no
`sqlalchemy`/`aiosqlite`); the first CI run after this change is the verification.

## New tests

| Test file | Runs where | Covers |
| --- | --- | --- |
| `contract_tests/test_security_units.py` | no DB needed | token round-trip/tamper/expiry, WS auth matrix, memory-path collision |
| `tests/test_security_api.py` | CI (full deps) | session/chat user-id spoofing rejected, admin endpoints return 403 |

Local evidence for this change set: `pytest contract_tests -q` → **17 passed**; `ruff check .`
clean (both blocking in CI).
