# Licensing model — open core, private enterprise

This repository is the **open base** of OmniCare. It is licensed under the MIT License
(`LICENSE`). The intent is to keep the platform usable and reviewable while advanced,
enterprise-only capabilities live in a separate private repository.

## What is open (this repository)

- Supervisor / Router and the expert-agent interfaces
- `MemoryPort`, `GovernancePort` and the local/demo adapters
- The NanoMemory and mem-guard **adapters** and their contract tests
- The Three.js realtime UI prototype
- Documentation, audit and engineering plan

## What stays private (separate repository, commercial licence)

- Production identity: OIDC/JWT, RBAC, tenant isolation
- Enterprise storage adapters: PostgreSQL, Redis, message bus
- Enterprise policy packs and the hosted governance control plane
- Refund approval workflow, ledger and payment-gateway integration
- Audit export, SLA monitoring, on-call tooling

The open repository depends on the same **interfaces** (`ports.py`), so a private adapter can
be dropped in without forking the base.

## Upstream projects

| Project | Licence | Note |
| --- | --- | --- |
| NanoMemory | MIT (published `v0.2.0`) | already open; used as a service/HTTP dependency |
| mem-guard | MIT (published `v0.1.0`, `v0.1.1`) | already open SDK |

**Boundary caveat:** because mem-guard was already published under MIT, its existing code cannot
be made private retroactively. "Core governance stays private" must therefore mean *new*
enterprise components (policy packs, hosted control plane, attestation service), not the
published SDK. Any other interpretation would contradict the existing grant and confuse users.

## If you need a patent grant

MIT does not include an explicit patent licence. If enterprise customers require one, switch
this repository to Apache-2.0 (the code is compatible) before the first public release; the
choice must be made **before** accepting outside contributions.
