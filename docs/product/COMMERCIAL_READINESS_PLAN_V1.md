# OmniCare — Commercial Readiness Plan v1

Status: **proposal; pending product and technical owner approval**  
Date: 2026-11-27  
Depends on: NanoMemory vector-memory v1 and mem-guard governance contract v1

## 1. Product definition

OmniCare is a vertical multi-agent commerce service for customer support, shopping, logistics and after-sales workflows. The production promise is not "the agent can chat"; it is that an authenticated customer can complete an authorized business task, understand its status and receive a consistent result across retries, failures and human review.

NanoMemory owns persistent memory records, embeddings and retrieval. mem-guard owns provenance/trust/policy evaluation. OmniCare owns customer identity mapping, commerce records, business authorization, orchestration, payment/refund execution and customer-facing experiences.

## 2. Scope

### v1 in scope
- Secure customer-facing BFF/API; browser never receives LLM or embedding provider secrets.
- Trusted OIDC/JWT identity, tenant membership and role/scope enforcement.
- Dedicated agent contracts for shopping, logistics and after-sales, orchestrated by a bounded supervisor.
- Pydantic input/output schemas for agent decisions and tool arguments; no free-form LLM JSON trusted without runtime validation.
- NanoMemory vector recall using authenticated tenant/user/session/collection filters.
- mem-guard pre-retrieval policy compilation and action authorization.
- Business database remains authoritative for price, stock, order ownership, payment, delivery and refund state.
- Persisted run traces, retrieval references/scores, token and latency metrics, governance decisions, and user feedback.
- Payment/refund provider abstraction with signed webhook verification, idempotency, reconciliation and append-only ledger.
- PostgreSQL schema migrations, backup/restore, transactional outbox and a production event broker.
- Staging and production deployment pipeline with rollback.
- 50+ labelled evaluation cases, including out-of-knowledge, cross-tenant and permission-denied cases.

### Explicit non-goals
- Fully autonomous purchasing or refund payment without explicit user/business authorization.
- A free-form agent that can invent prices, stock, order status or policy.
- Letting model output directly mutate database records.
- Multi-region active-active or complex enterprise workflow designer in v1.
- Replacing deterministic business rules with an agent framework.
- Adopting Google ADK solely for fashion. Keep the current thin orchestrator unless an evaluation demonstrates that a framework materially improves tracing, retries, state management or developer productivity.

## 3. Architecture boundaries

- BFF/API: sessions, authentication, quotas, rate limits, idempotency, response streaming and user-facing errors.
- Supervisor: classify intent, choose a specialized agent, enforce deadlines/budgets and produce a typed response.
- Specialized agents: shopping, logistics and after-sales. Each gets least-privilege tools and a typed input/output contract.
- Tool registry: one authorization check before each side effect; typed arguments; idempotency key; timeout; retry classification.
- Governance adapter: mem-guard contract, retrieval filters, action decisions and audit reason codes.
- Memory adapter: NanoMemory service, tenant/user/collection scope, embedding profile and retrieval trace.
- Domain services: order, payment, shipment and refund state machines; independent from the LLM.
- Persistence: PostgreSQL, migrations, transaction boundaries, outbox and immutable audit trail.
- Operations: structured logs, metrics, distributed traces, alerting, backup/restore and release health checks.

No LLM response is authoritative for inventory, prices, payment, order ownership or refund completion. The domain service and external provider settlement event are authoritative.

## 4. Required state machines and rollback

### Agent run
QUEUED -> RUNNING -> SUCCEEDED | FAILED | TIMED_OUT | CANCELLED | NEEDS_HUMAN_REVIEW

- Retries must be idempotent and bounded.
- Cancellation and timeout are persisted.
- A retry does not duplicate a side effect.
- A terminal failed run may be retried as a new attempt linked to the original run.

### Refund
REQUESTED -> PENDING_REVIEW -> APPROVED | REJECTED
APPROVED -> PAYMENT_PENDING -> SETTLED | PAYMENT_FAILED
PAYMENT_FAILED -> PAYMENT_PENDING only via a bounded retry with the same provider idempotency key, or -> NEEDS_MANUAL_RECONCILIATION
SETTLED is terminal except a separately recorded compensating transaction; never edit or delete the original ledger event.

Every transition stores actor, role, reason, timestamp, request/run ID and provider reference. A UI must distinguish approved from paid/settled. "Settled" cannot be set by the LLM or by an admin-only local balance update; it requires a verified provider event or a documented manual reconciliation procedure.

## 5. Observability and feedback

Persist a structured RunTrace with:
- run, request, tenant, user and session identifiers;
- original user query in a restricted encrypted field with configurable retention;
- selected agent, route decision and prompt/template version;
- memory query, authorized retrieved memory IDs, dense/lexical/fused scores and embedding profile;
- policy version and governance decision IDs;
- references to the exact context blocks supplied to the model (content retention opt-in; otherwise hashes and IDs);
- model provider/model, first-token latency, total latency, input/output/total tokens and usage source;
- tool calls, validated arguments hash, outcome, retries, errors and idempotency keys;
- final response reference and response hash; full response body only under a configurable retention policy.

Default telemetry must not store raw secrets or unrestricted personal data. Content-level trace access is privileged, audited and retention-limited.

Add explicit 👍 / 👎 feedback linked to run ID, optionally with a reason category. Feedback creates a review queue item; it does not automatically train or alter memory. Weekly review converts valid defects into labelled evaluation cases and product fixes.

## 6. Security and tenant isolation

- Browser receives an application session, never model provider or embedding credentials.
- BFF derives subject and tenant from a verified identity token; request-body user_id is only a consistency assertion and cannot grant identity.
- Tenant ID is mandatory on every tenant-owned row and query.
- PostgreSQL RLS and application-level authorization are both required for sensitive tables.
- NanoMemory query filters are applied before vector candidate ranking. Cross-tenant canary tests must prove zero leakage.
- mem-guard is used for memory trust and action governance, not as a replacement for database authorization.
- MCP identity is derived from the verified bearer token; tools do not accept caller-supplied user_id/token fields as authority.
- Secret scanning, dependency scanning, security headers, request size limits, CSRF protection where cookie sessions are used, rate limits and audit retention are CI/release requirements.
- Threat model covers prompt injection, tool misuse, cross-tenant retrieval, forged identity, replay, duplicate payments/refunds, provider outage and data exfiltration.

## 7. Evaluation

Maintain at least 50 versioned cases with human-labelled expected actions and evidence:
- common shopping, logistics, order and refund flows;
- paraphrase and ambiguous requests;
- missing knowledge requiring abstention or clarifying question;
- stale and contradictory memory;
- cross-tenant, wrong-owner and unauthorized collection retrieval;
- prompt injection in user text, memory and tool output;
- tool timeout, provider rate limit, duplicate request and webhook replay;
- refund state rollback and payment reconciliation failures.

Proposed release gates:
- Unauthorized data retrievals and unauthorized side effects: 0.
- Correct abstention on out-of-knowledge cases: >= 95%.
- Critical business action schema-validity: 100%.
- Duplicate payment/refund side effects under retry tests: 0.
- All critical workflows pass end-to-end in staging.
- Retrieval Recall@10 and nDCG@10 meet NanoMemory's approved gates.
- Every run is traceable to a model, prompt, policy and memory-index version.

Report results by task category and use confidence intervals where sample size permits. Mock-provider tests are contract tests, not evidence of real provider quality.

## 8. User stories and Given / When / Then

### US-O01 — Customer identity and session
- Given a customer with a valid token for tenant A, when they create or access a session, then the session is bound to the token subject and tenant.
- Given a body containing a different user_id or tenant_id, when the request is submitted, then the API rejects the mismatch and does not switch identity.
- Given an expired, malformed or wrong-audience token, when the request is submitted, then the BFF returns 401 and emits a safe security event.

### US-O02 — Safe vector memory recall
- Given two customers with similar preferences in different tenants, when customer A asks a question, then no customer B memory enters the retrieval candidate set or model context.
- Given a memory marked pending_confirmation or quarantined, when a customer asks for it, then governance excludes it according to policy.
- Given NanoMemory or mem-guard is unavailable, when memory-dependent processing starts, then OmniCare follows an explicit degraded/abstain path and never falls back to unfiltered memory.

### US-O03 — Structured agent execution
- Given a shopping request, when the supervisor routes it, then the shopping agent receives a schema-validated request and least-privilege tools.
- Given malformed model output, when Pydantic validation runs, then the action is rejected and a typed recoverable error is recorded.
- Given an agent timeout or tool failure, when the run terminates, then its state and retryability are persisted without claiming success.

### US-O04 — Refund lifecycle
- Given a valid refund request, when it is approved, then the state becomes approved/payment_pending and no customer-facing text claims that money has arrived.
- Given a duplicate request or webhook replay, when the same idempotency key is processed, then no duplicate refund side effect or ledger posting occurs.
- Given a provider failure after approval, when settlement fails, then the run enters payment_failed or manual reconciliation and preserves the audit history.

### US-O05 — Trace and feedback
- Given a completed agent run, when an authorized operator opens its trace, then route, memory IDs/scores, policy decisions, model usage, tool outcomes and timing are available.
- Given telemetry content retention is disabled, when a run is recorded, then raw prompt and response bodies are not stored in general logs.
- Given a customer submits thumbs-down feedback, when the run is reviewed, then the feedback is linked to a trace and can become a labelled evaluation case.

### US-O06 — Production release
- Given a pull request changes API, memory or governance code, when CI runs, then lint, type/schema checks, unit, contract, PostgreSQL integration, security and evaluation gates are blocking.
- Given staging smoke tests fail, when a release is promoted, then production deployment is blocked.
- Given a production deployment fails health checks, when rollback is initiated, then the previous known-good image and compatible database migration plan are restored.

## 9. UI states

Every customer and operator flow must define loading, empty, validation error, dependency error, permission denied, conflict, retrying, success and needs-human-review states. Refund UI must distinguish requested, approved, payment pending, settled, rejected and manual reconciliation. Memory and trace UIs must not expose records outside the operator's tenant scope.

## 10. Delivery order

1. Freeze contracts between OmniCare, NanoMemory and mem-guard.
2. Add evaluation set and negative/security cases before changing agent prompts.
3. Add tenant-scoped vector memory adapter and pre-retrieval authorization integration.
4. Add structured run trace and feedback loop.
5. Complete payment/refund provider and ledger reconciliation.
6. Complete DB migration, RLS, outbox/event broker and tested backup/restore.
7. Complete customer-facing BFF UX, accessibility and failure states.
8. Complete Docker production run, deployment/rollback drill and cost model.
9. Pilot with a limited number of real users, review feedback weekly and expand only after release gates pass.

## 11. Approval

- Product owner: PENDING
- Technical owner: PENDING
- Commercial/legal owner: PENDING
- Date: PENDING
