# OmniCare MVP 验证报告

**报告日期：** 2026-11-11  
**项目版本：** 0.1.0  
**交付状态：** Local MVP / Architecture Baseline（不是 Production Ready）

## VERIFIED（本次环境已实际执行）

| 检查 | 结果 | 证据 |
|---|---|---|
| Python 语法编译 | PASS | `python -m compileall -q app tests` |
| LocalPolicyGuard 独立单元测试 | 5 passed | 在隔离目录运行 `test_local_guard_unit.py`，避免加载需要 aiosqlite 的 API fixture |
| 核心路由烟雾检查 | PASS | logistics / refund / product 三类意图分别路由至预期专家 Agent |
| 输入治理烟雾检查 | PASS | prompt injection、免费赠品、直接辱骂、明确矛盾退款意图均被拒绝；正常物流查询被放行 |
| 无金额退款行为 | PASS | 独立异步 smoke test 确认 Agent 先询问金额，且不会调用退款写工具 |
| Mock usage source | PASS | 明确标记为 `mock`，不伪装为 provider 精确 usage |
| 核心 Python 模块导入 | PASS | 路由、治理、SQLAlchemy entity、Mock Provider 可导入 |

## NOT VERIFIED（本次环境无法执行）

| 项目 | 原因 / 下一步 |
|---|---|
| FastAPI 集成与端到端 pytest | 当前容器缺少 `aiosqlite`；在安装项目依赖后运行 `pytest -q` |
| SQLite 服务启动与 API HTTP 调用 | 依赖 `aiosqlite`；执行 README 的本地启动和 curl 步骤 |
| DeepSeek 真实调用 | 当前环境无 `openai` SDK 与 API Key；配置真实密钥后验证，勿提交密钥 |
| MCP Server 运行 / Inspector 验收 | 当前环境缺少 `mcp` SDK；安装依赖后启动独立 MCP 进程并使用 MCP Inspector 检查 tools |
| Docker build / Compose | 当前环境无 Docker CLI/daemon；执行 `docker compose up --build` |
| NanoMemory REST 契约 | 适配器 DTO 映射基于 v1.6 已记录路由与 Session Header；必须对照实际部署版本 OpenAPI/SDK 做契约测试 |
| mem-guard 真实 SDK 契约 | 本仓库仅提供 GovernancePort + LocalPolicyGuard；尚未连接真实包/API |
| n8n SMTP/飞书外部工作流 | 仅实现可配置 webhook client，未配置外部 webhook 或凭据 |

## NOT IMPLEMENTED（明确留到后续 Phase）

- Vue/React 用户聊天前端、Three.js 3D 运营大屏。
- OIDC/JWT、RBAC/ABAC、租户隔离与多角色管理。
- PostgreSQL + Alembic、Transactional Outbox、Redis/NATS/Kafka 多实例事件总线。
- 真实支付退款执行、退款审批、支付账本和 pending refund reserve。
- Three.js 浏览器 E2E、OpenTelemetry / Prometheus、压力测试与备份恢复演练。

## 本地验收命令

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check app tests
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

只有在目标机上真实执行并留存命令退出码、JUnit XML、coverage XML、容器构建结果和 SDK 契约测试结果后，才可把对应项从 NOT VERIFIED 改为 VERIFIED。

## Contract alignment with NanoMemory 0.2.0 and mem-guard 0.1.0 (2026-11-11)

Both adapters were rewritten against the real public surfaces and covered by contract tests that
run without the database/model stack.

- Memory adapter (app/memory/nanomemory_http.py) now uses the real routes: GET
  /api/v1/memory/recall for governed retrieval and GET /api/v1/memory/long-term for trust
  enrichment; POST /api/v1/memory/long-term for preference writes with source_type=user.
- Governance adapter (app/governance/memguard_adapter.py) uses the real mem-guard SDK (Guard,
  RuleCompiler, TrustLevel) for input/output rules and trust vocabulary; business authorization
  that needs the database remains in LocalPolicyGuard and is composed inside the adapter.
  GOVERNANCE_BACKEND=memguard selects it.
- Fixtures were captured from a real NanoMemory TestClient run:
  tests/fixtures/nanomemory_contract.json.

Verified in this environment: python -m pytest contract_tests -q -> 8 passed.

Remaining, explicitly not verified: full FastAPI + SQLite integration (no sqlalchemy/aiosqlite),
real DeepSeek calls, MCP Inspector, Docker and n8n. Trust minting is still adapter-attested
because mem-guard 0.1.0 has no issuer registry; tracked for mem-guard v0.1.1.
