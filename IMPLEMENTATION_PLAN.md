# OmniCare 实施计划与需求追踪

## 1. 本轮交付边界

- [x] 统一目录结构和配置入口
- [x] FastAPI + SQLAlchemy async + SQLite schema
- [x] 用户会话、消息存储和演示种子数据
- [x] Supervisor + deterministic Router + 三类专家 Agent
- [x] Tool Registry + Pydantic 参数验证 + timeout
- [x] 退款写入前金额/订单归属校验
- [x] NanoMemory MemoryPort + local demo adapter + REST adapter seam
- [x] mem-guard GovernancePort + local demo guard
- [x] AgentEvent 持久化 + WebSocket 推送 + metrics summary
- [x] n8n webhook client
- [x] MCP v2 Streamable HTTP read-only server
- [x] Docker Compose / `.env.example` / README / TAD
- [x] MVP pytest suite（需在目标机器安装依赖后执行）

## 2. 目录结构

```text
omnicare/
├── app/
│   ├── api/                 # FastAPI routes / dependency injection
│   ├── agents/              # Router + domain agents
│   ├── core/                # settings / DB / event broker / logging
│   ├── governance/          # mem-guard Port & demo adapter
│   ├── integrations/        # n8n client
│   ├── memory/              # NanoMemory Port & adapters
│   ├── models/              # SQLAlchemy entities
│   ├── providers/           # Mock / OpenAI-compatible LLM providers
│   ├── schemas/             # Pydantic API contracts
│   ├── services/            # Supervisor orchestration
│   ├── tools/               # ToolSpec / Registry / ecommerce tools
│   ├── main.py
│   ├── seed.py
│   └── mcp_server.py
├── data/                    # local SQLite + demo memory (gitignored)
├── docs/
├── frontend/admin/          # reserved for Three.js dashboard phase
├── tests/
├── ARCHITECTURE.md
├── IMPLEMENTATION_PLAN.md
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## 3. Requirement-to-file-to-test mapping

| Requirement | Implementation | Verification artifact | State |
|---|---|---|---|
| HTTP API + health | `app/main.py`, `app/api/routes.py` | API smoke / OpenAPI | Implemented; execution pending |
| Data schema | `app/models/entities.py` | SQLAlchemy create_all on startup | Implemented; migrations not yet |
| Multi-Agent Supervisor | `app/services/supervisor.py` | `tests/test_chat_flow.py` | Implemented; runtime test pending |
| Intent routing | `app/agents/router.py` | `tests/test_chat_flow.py` + isolated route smoke | Implemented; expanded test coverage needed |
| Logistics flow | `app/agents/logistics.py`, `app/tools/ecommerce.py` | `test_create_session_and_logistics_chat` | Implemented |
| Refund flow | `app/agents/aftersales.py` | valid/invalid refund tests | Implemented |
| Tool allowlist/schema/timeout | `app/tools/registry.py` | add registry-specific tests | Implemented; coverage to expand |
| NanoMemory adapter contract | `app/memory/ports.py`, `nanomemory_http.py` | contract tests against actual release required | Adapter seam implemented; NOT VERIFIED against real NanoMemory |
| Memory provenance filtering | `app/governance/local_guard.py` | `test_local_guard_unit.py` | Demo rule implemented; real mem-guard NOT VERIFIED |
| mem-guard Action gate | `app/tools/registry.py`, `local_guard.py` | `test_refund_over_authoritative_order_amount_is_blocked` | Demo rule implemented; real SDK NOT VERIFIED |
| WebSocket monitor | `app/core/events.py`, `app/api/routes.py` | WS integration test to add | Implemented; browser E2E NOT VERIFIED |
| Metrics | `/metrics/summary` | API test to add | Implemented |
| n8n | `app/integrations/n8n.py` | mocked webhook test to add | Client implemented; external workflow NOT VERIFIED |
| MCP | `app/mcp_server.py` | MCP Inspector integration test required | Code implemented; runtime NOT VERIFIED in this environment |
| Docker | `Dockerfile`, `docker-compose.yml` | `docker compose build/up` | NOT VERIFIED in this environment |
| Three.js UI | `frontend/admin/` | future browser test | NOT IMPLEMENTED |

## 4. 下一阶段分解

### Phase 0 — Contract freeze

- [ ] 获取 NanoMemory 实际发布版本、OpenAPI JSON、SDK package import path、示例请求/响应。
- [ ] 获取 mem-guard 实际 SDK/API；确认签名验证、consumer policy、Action authorization 的方法签名。
- [ ] 固定 `MemoryPort` / `GovernancePort` 映射与错误语义；编写 contract fixtures。
- [ ] 固定登录身份契约（OIDC provider、JWT claims、tenant claim）。

### Phase 1 — Backend hardening

- [ ] PostgreSQL + Alembic migrations。
- [ ] OIDC/JWT authentication；不再接收客户端自选 `user_id`。
- [ ] Request ID / idempotency key / rate limit / global concurrency budgets。
- [ ] 完整 Tool Registry 事件：tool_started / tool_completed / tool_denied；工具成本预算。
- [ ] Refund pending reserve amount 纳入可退金额计算；幂等提交及审批流程。
- [ ] 对话内容脱敏、加密与 retention/deletion。
- [ ] 单元、集成、安全测试补齐至 P0 gate。

### Phase 2 — Memory & governance integration

- [ ] 用真实 NanoMemory SDK 替换 local memory adapter。
- [ ] Working / Long-term / Episodic 分层接口映射；session create/validate。
- [ ] 记忆状态、来源证明与 trust metadata 的端到端验证。
- [ ] 用真实 mem-guard 替换 local guard；治理超时 fail-closed。
- [ ] 污染 benchmark：提示注入记忆、伪造来源、冲突偏好、过期记忆、错误授权。
- [ ] 对照实验：无记忆 / NanoMemory / 未治理 memory / mem-guard governed memory。

### Phase 3 — Automation & observability

- [ ] Transactional Outbox：与业务事务同库提交，dispatcher 幂等投递 n8n。
- [ ] n8n 工作流模板导出：governance.blocked、refund.pending_review、high_value_order。
- [ ] Webhook HMAC 签名、幂等键、retry + dead-letter queue。
- [ ] Redis/NATS 事件总线，支持多 API 实例和事件 replay。
- [ ] OpenTelemetry trace/span；metrics 导出 Prometheus；日志脱敏。
- [ ] 定义 token usage source 和模型成本配置表（价格不硬编码）。

### Phase 4 — Frontend & showcase

- [ ] 用户聊天 UI：流式消息、会话历史、Agent/Tool 状态。
- [ ] 管理后台 RBAC、策略配置审批和审计。
- [ ] Three.js Agent 节点场景；WebSocket event replay。
- [ ] 图表：请求量、P50/P95、Token、exact/estimated 结构、阻断数、工具失败数。
- [ ] Playwright E2E + 录屏脚本。

### Phase 5 — Production deployment

- [ ] Nginx TLS / WAF / request-size limit / rate limit。
- [ ] PostgreSQL、Redis、n8n、API 分服务；健康检查和资源限制。
- [ ] CI：ruff、pytest、coverage、OpenAPI diff、container build、SBOM。
- [ ] secrets manager、备份恢复演练、审计保留与告警。
- [ ] 性能压测与容量规划；所有目标值用实测报告替换。

## 5. 本轮执行状态

代码骨架和文档已生成。本轮环境不保证具备项目全部 Python 依赖、Docker daemon 或 NanoMemory/mem-guard 实际部署，因此没有将安装、容器构建、真实 SDK 连接或运行测试预先标为 VERIFIED。应在本地按 README 执行，并将真实命令输出保存到 `reports/`。
