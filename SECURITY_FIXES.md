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


# Third review round (S-01 … S-06)

Source: the 2026-11-22 external review of the MCP/identity/governance story. The two P0s were
(a) the MCP server had no authenticated transport and made callers pass customer tokens as
tool arguments, and (b) identity was self-issued HMAC only, with a tenant claim that was
carried but never enforced. The P1s were memory provenance that could not be audited and
NanoMemory integration assumptions that had never been exercised against the real service.

## S-01 — MCP transport authentication and trusted identity (P0)

- `app/mcp_server.py` now builds the MCP server through `create_server(settings)`:
  - `AUTH_MODE=jwt|oidc` → `MCPServer(..., auth=AuthSettings(required_scopes=[...]),
    token_verifier=OmniCareTokenVerifier(settings))`. The MCP SDK's bearer middleware verifies
    `Authorization: Bearer <JWT>` on **every** request and rejects missing/invalid/insufficient-
    scope tokens with 401 before any tool runs.
  - `AUTH_MODE=dev|token` keeps the tokenless local demo, only for `APP_ENV in local/dev/test`;
    any other deployment fails closed at server construction (`mcp_requires_jwt_auth`).
- Tools take **no** token, `user_token` or `user_id` arguments: `search_products(query)` and
  `check_logistics(order_id)`. Identity comes from the verified token (`get_access_token()`)
  and is re-checked in `app/mcp_token.py` (`trusted_identity_from_access_token`: subject,
  tenant allowlist, required scopes). `check_logistics` filters `Order.id + Order.user_id`;
  another customer's order returns `{"ok": false, "error": "order_not_found_or_not_owned"}`.
- `app/mcp_auth.py` is the pure identity layer (no MCP import): `TrustedIdentity`,
  `identity_from_verified_token`, `resolve_identity_from_authorization`, `resolve_dev_identity`,
  `require_scope`, `require_owner`. Error messages never echo the bearer token or headers.
- Tool annotations (`readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true`,
  `openWorldHint=false`) are published on both tools and asserted over the wire.
- Residual: no OAuth authorization server is bundled. The deployment points at an IdP
  (`AUTH_JWKS_URL`) or uses a strong HS256 secret; token issuance for demos is
  `python -m app.auth_cli issue-token`. MCP session ownership is enforced by the SDK's
  principal binding, not by OmniCare.

## S-02 — JWT/OIDC identity core, tenant allowlist and revocation (P0)

- `app/core/jwt.py`: `issue_jwt` / `verify_jwt` / `VerifiedToken` / `JwtError`.
  - Algorithm is pinned by `AUTH_JWT_ALGORITHM`; `alg=none` and algorithm confusion are refused
    before key material is touched. HS256/384/512 use `AUTH_SECRET`; RS256/ES256 use
    `AUTH_JWT_PUBLIC_KEY`, `AUTH_JWT_PUBLIC_KEY_PATH` or `AUTH_JWKS_URL` (PyJWKClient with a
    process cache and kid support).
  - `exp`/`nbf`/`iat` are checked with `AUTH_JWT_LEEWAY_SECONDS`; `iss`/`aud` are enforced when
    configured; `sub` is mandatory; the tenant claim is checked against `AUTH_ALLOWED_TENANTS`
    (empty allowlist = any signed tenant); `jti` is checked against `AUTH_REVOKED_JTIS`;
    `required_scopes` fails closed.
  - No logging of tokens; error messages carry stable codes (`expired_token`, `bad_signature`,
    `tenant_not_allowed`, `token_revoked`, `insufficient_scope`, …) and never the token.
- `app/api/deps.py`: mode dispatch — `dev` unchanged, legacy `token` unchanged, `jwt/oidc` verify a
  JWT and map `VerifiedToken → Principal` (user, role, tenant, scopes). 401s keep
  `WWW-Authenticate`; admin endpoints require the token role `admin`; the dev-only
  `X-Admin-Key` elevation is gone from every other mode.
- `app/core/preflight.py`: production rejects `AUTH_MODE=dev` (`insecure_auth_mode`) and legacy
  `AUTH_MODE=token` (`legacy_token_mode_in_production`); `jwt` needs strong HMAC key material or
  a public key/JWKS; `oidc` additionally needs issuer and audience; unknown modes/algorithm
  are refused. Startup fails before the first request.
- `app/auth_cli.py`: `python -m app.auth_cli issue-token --user alice --role user --scopes omnicare.read`
  is a local/dev helper only (refused when `APP_ENV=production`); the token is printed to stdout,
  never logged.
- `docker-compose.prod.yml` now runs `AUTH_MODE=jwt` (or `oidc` with
  `AUTH_JWKS_URL`/`AUTH_JWT_ISSUER`/`AUTH_JWT_AUDIENCE`) and no longer requires
  `MCP_AUTH_TOKEN`. `.env.example` documents all modes and variables.
- Residual: the revocation list is a static CSV (`AUTH_REVOKED_JTIS`), not a database/IdP
  revocation feed; JWKS fetching is synchronous inside the verifier (PyJWKClient), cached per
  process; the data model remains single-tenant (the tenant claim is allowlist-enforced and
  propagated to provenance, but orders/sessions have no tenant column yet).

## S-03 — Memory provenance audit chain (P1)

- `MemoryRecord` carries `provenance` (`source_kind`, `source_ref`, `created_by`); the supervisor
  writes `source_ref = omnicare://sessions/<session>/runs/<run>` and `created_by`.
- `app/services/attestation.py` (pure, no DB) computes an HMAC-SHA256 digest over
  memory_id + backend + user + session + run + source_ref, with a separator-joined canonical
  payload and `AUTH_SECRET` (documented local fallback). `app/services/memory_provenance.py`
  persists one `memory_provenance` row per written memory in the same transaction, and
  verifies it at retrieval: a row that is missing, tampered with, or points at a different
  `source_ref` is quarantined before the governance check (incident
  `memory_provenance_unverified`, reason detail included).
- `app/governance/local_guard.py` and `memguard_adapter.py` reject user-stated memories with no
  provenance ref (`memory_provenance_missing`).
- `GET /api/v1/memories/{memory_id}/provenance` (admin) returns the audit row and a live
  `verified` flag; the digest itself is never exposed.
- The supervisor passes the token's tenant claim into the provenance row, so a deployment with
  multiple tenants can at least audit which tenant wrote a memory.
- Residual: provenance is only attested for memories written through the supervisor; legacy
  or out-of-band records are quarantined on retrieval (intentional fail-closed).

## S-04 — NanoMemory provenance, user scoping and session contract (P1)

- NanoMemory (0.2.1, separate repo) now accepts optional `provenance` and `user_id` on
  `POST /api/v1/memory/long-term`; provenance is stored and returned unchanged, `user_id` is
  persisted and used by `GET /api/v1/memory/recall?user_id=`, and the trust clamps/secret
  scanning are unchanged. Docs and 6 new integration tests cover it.
- The OmniCare adapter (`app/memory/nanomemory_http.py`) sends `provenance` only when a
  `source_ref` exists (old-server tolerance), sends `user_id`, maps response provenance back
  onto `MemoryRecord.provenance`, and negotiates the session contract instead of assuming
  that an OmniCare session id is a NanoMemory session id:
  - probes `GET /api/v1/version` once; single-session deployments get no
    `X-NanoMemory-Session-ID` header (using the default session), multi-session deployments get
    one server-created NanoMemory session per OmniCare conversation, cached per adapter.
- This was found by the real-service acceptance run, not by a mock: the previous adapter
  returned `404 session_not_found` against a live NanoMemory in single-session mode.

## S-05 — HTTP surface clean-up (P1)

- Session and chat bodies no longer require a `user_id`: the token is the only identity source,
  and a provided value is just an assertion that must match (`403 user_id_mismatch`).
- `GET /health` has an unprefixed alias again (the Docker HEALTHCHECK and deployment probes use
  it); `/api/v1/health` remains.
- MCP tool annotations from PR #2 are part of this branch: the review's "tool annotations"
  recommendation is merged locally (the remote PR is still open, see git log).

## S-06 — Verification (this round)

| Check | Result |
|---|---|
| `pytest contract_tests -q` | **129 passed** |
| `pytest tests -q` (SQLite) | **43 passed, 3 skipped** |
| `ruff check .` / `ruff format --check .` | clean |
| NanoMemory suite | **293 passed, 2 skipped** |
| mem-guard suite | **33 passed** |
| Live MCP server (JWT transport) | 15/15 probes: 401 matrix, annotations, no token args, owner isolation, no token echo |
| Live HTTP API (JWT) | 13/13 probes: 401/403 matrix, session/chat, admin role, provenance event |
| Live NanoMemory + adapter | provenance round-trip, `user_id` recall scoping, single-session negotiation |
| Boot guard matrix | production+jwt boots; production+dev/token and staging+dev refused |
| Provenance admin endpoint | `verified: true` with tenant + conversation/run reference |

Not verified here: PostgreSQL row-lock suite (no local PostgreSQL; CI runs it), Docker image and
compose overlays (no Docker daemon in this environment), OIDC against a real IdP, payment
settlement, DeepSeek/n8n external calls, MCP Inspector UI, distributed event bus.
