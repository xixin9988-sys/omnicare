# Contract tests (real NanoMemory + real mem-guard)

These tests exercise OmniCare's two external adapters against the **actual** public surfaces of
the two projects, instead of mocks that only share their names.

| Adapter | Contract source | How it is tested |
| --- | --- | --- |
| `app/memory/nanomemory_http.py` | NanoMemory 0.2.0 REST (`/api/v1/memory/recall`, `/api/v1/memory/long-term`) | fixtures captured from a real `TestClient` run (`tests/fixtures/nanomemory_contract.json`) replayed through `httpx.MockTransport` |
| `app/governance/memguard_adapter.py` | mem-guard 0.1.0 SDK (`Guard`, `RuleCompiler`, `TrustLevel`) | the real `mem_guard` package is imported; no stub |

## Run

```bash
# from the OmniCare checkout
MEM_GUARD_PATH=/path/to/mem-guard \
PYTHONPATH="$PWD:$MEM_GUARD_PATH" \
python -m pytest contract_tests -q
```

On Windows (PowerShell):

```powershell
$env:PYTHONPATH = "E:\path\to\omnicare;E:\path\to\mem-guard"
$env:MEM_GUARD_PATH = "E:\path\to\mem-guard"
python -m pytest contract_tests -q
```

The full `tests/` suite still needs `sqlalchemy`, `aiosqlite`, `openai` and `mcp`; the contract
tests deliberately avoid the database and model stack so they can run in a minimal environment.

## Verified contract facts (2026-11-11)

* `GET /api/v1/memory/recall` returns `{query, intent, insufficient_context, fallback_reason,
  estimated_tokens, budget, items[]}`; items carry `id, kind, content, score, tokens, reason,
  memory_type, status, signals`.
* `GET /api/v1/memory/long-term?limit=` returns `{count, records[]}`; records carry
  `trust_level`, `status`, `source_type`, `provenance`.
* `POST /api/v1/memory/long-term` accepts `{content, category, source, source_type, tags}` and
  returns the full record; it **does not accept a trust label**.
* mem-guard 0.1.0 exposes `Guard.add/search/explain/stats`, `RuleCompiler` and `TrustLevel`, but
  **no issuer attestation** (that is planned for mem-guard v0.1.1).

## Known gaps, stated explicitly

1. **Trust enrichment costs a second request.** NanoMemory's recall response does not include
   `trust_level`, so the adapter also fetches the long-term listing and joins by id. When
   NanoMemory exposes trust on recall, this join can be removed.
2. **Trust minting is adapter-attested, not issuer-attested.** mem-guard 0.1.0 has no
   `IssuerRegistry`, so OmniCare maps `source_type="user"` to `user_stated` itself. That is
   honest attribution by the adapter, not cryptographic provenance; upgrade to issuer
   attestation when mem-guard v0.1.1 ships.
3. **Business authorization stays local.** mem-guard is used for text rules and trust
   vocabulary; refund limits and order ownership remain in `LocalPolicyGuard` because they need
   the authoritative database.
