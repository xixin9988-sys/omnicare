# Engineering audit — OmniCare MVP 0.1.0

> **Status update, 2026-11-11 (`main` = `8bbefe5`, release v0.1.2).** This file is kept as the
> record of the 0.1.0 audit; the findings below are closed or re-scoped as follows.
>
> - **P0-1** full runtime: closed — `tests/` runs green (SQLite locally, PostgreSQL 16 in CI).
> - **P0-2** CI: closed — `.github/workflows/ci.yml` has three blocking jobs
>   (`contracts`, `ruff`, `full suite`), the last one running SQLite *and* PostgreSQL.
> - **P0-3** contradictory verification report: closed — `VERIFICATION_REPORT.md` is now a single
>   status table (48 contract / 24+2 SQLite / 25 PG).
> - **P0-4** `user_id` as identity: closed — HMAC bearer tokens, dev identity only in
>   local/dev/test, and `app/core/preflight.py` refuses to boot an unsafe combination.
> - **P2-3** refund workflow: closed at the application layer — `pending_review → approved |
>   rejected`, `approved → settled`, four-eyes rule, append-only `refund_audit`, admin API. Still
>   open: payment-gateway settlement and ledger reconciliation.
> - **P2-1** Docker: still open (never built; only the YAML is validated).
> - Details and residual risk: [`SECURITY_FIXES.md`](SECURITY_FIXES.md).

Scope: the public checkout at commit `d62c2bd` (private GitHub repo `xixin9988-sys/omnicare`).
Every finding below is tied to a file or command that exists in this repository; nothing is
asserted from memory.

## Evidence collected in this pass

| Check | Result |
| --- | --- |
| `pytest contract_tests -q` | **12 passed** (8 integration contracts + 4 UI contracts) |
| `pytest tests` (full suite) | **not run here** — environment lacks `sqlalchemy`, `aiosqlite`, `mcp` |
| `LICENSE` file | absent |
| `.github/workflows` | absent (GitHub API reports 0 workflows) |
| `pyproject.toml` license | `Proprietary - replace before distribution` |
| Auth | request carries `user_id`; no OIDC/JWT/RBAC; demo-only |
| Refund | creates `pending_review` only; no approval workflow, gateway or ledger |
| `VERIFICATION_REPORT.md` | early rows say "not connected"; later section says contracts passed → **inconsistent** |

## P0 — must close before adding features

| ID | Issue | Evidence | Acceptance criteria |
| --- | --- | --- | --- |
| P0-1 | Full runtime never booted | `tests/conftest.py` imports `app.main` → requires `sqlalchemy`/`aiosqlite`, absent | `pip install -e ".[dev]"`, `pytest tests` green, `uvicorn app.main:app` serves `/health` and one `/api/v1/chat` round trip, output committed as artifact |
| P0-2 | No CI | no `.github/`; GitHub API says 0 workflows | CI runs contracts + full suite + ruff on every push; failing jobs block merge |
| P0-3 | Verification report contradicts itself | rows 28–29 vs section added 2026-11-11 | one status table, one number (12 passed), explicit NOT VERIFIED list |
| P0-4 | `user_id` treated as identity | `app/api/deps.py` / chat schema | requests without a trusted identity are rejected outside `DEV_MODE`; document the boundary |

## P1 — Phase 1.5 integration loop

| ID | Issue | Evidence | Acceptance criteria |
| --- | --- | --- | --- |
| P1-1 | NanoMemory recall has no trust label → adapter does a second lookup | `app/memory/nanomemory_http.py` `_records()` | NanoMemory exposes `trust_level` on recall; adapter drops the join; contract test asserts one request |
| P1-2 | Trust is adapter-attested | mem-guard 0.1.0 has no issuer registry | mem-guard v0.1.1 ships `Issuer` attestation; adapter registers an issuer; contract test asserts unattested claims are quarantined |
| P1-3 | UI not verified against live events | `web/js/app.js` falls back to DEMO; only static preview captured | backend running, `/api/v1/ws/monitor` connected, screenshot shows `LIVE` and non-zero counters |
| P1-4 | Adapter resilience | fixed 8 s timeout, no retry/circuit breaker | timeout/retry policy documented and tested with a flaky transport |
| P1-5 | Contracts not pinned in CI | `pyproject.toml` extras point at tags | CI installs `mem-guard@v0.1.1` / `nanomemory@v0.2.0` and runs contracts |

## P2 — production and enterprise

| ID | Issue | Acceptance criteria |
| --- | --- | --- |
| P2-1 | Docker never built | `docker build` + compose up, healthcheck passes, artifact logged |
| P2-2 | Observability is event-only | token/latency/governance metrics persisted and queryable; dashboard reads them |
| P2-3 | Refund workflow incomplete | state machine `pending_review → approved/rejected → refunded`, audit trail, no gateway |
| P2-4 | Enterprise split | private repo/adapter for OIDC/RBAC, PostgreSQL, policy packs; open repo depends on interfaces only |

## Licensing: hybrid (open core + private enterprise)

The intended model is: **base open, advanced/enterprise and core governance assets private**.

| Layer | Where | License |
| --- | --- | --- |
| Base platform (this repo: orchestrator, ports, local adapters, contract tests, UI prototype) | public repo | open source (MIT proposed; Apache-2.0 if a patent grant is required) |
| Enterprise capabilities (OIDC/RBAC, PostgreSQL/Redis adapters, policy packs, audit export, SLA) | separate private repo | commercial |
| NanoMemory engine | public | MIT (already published) |
| mem-guard governance SDK | public | MIT (already published at `v0.1.1`) |

**Important honesty note:** mem-guard is already public under MIT at `v0.1.0` and `v0.1.1`;
that grant cannot be revoked retroactively. "Core governance stays private" therefore has to
mean *new* enterprise policy packs / hosted control-plane code, not the published SDK. Reusing
the mem-guard name for a closed core would confuse users and contradict the existing license.

## Recommended order

1. P0-1 → P0-3 (runtime + CI + single status table) — one focused change set.
2. P1-1/P1-2 (contract gaps) once NanoMemory 0.2.1 and mem-guard v0.1.1 are tagged.
3. P1-3 (live UI) and P2-1 (Docker) before any public announcement.
4. P2-3/P2-4 only after the base is reproducible in CI.
