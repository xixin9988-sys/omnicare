# OmniCare MVP 验证报告

**报告日期：** 2026-11-11（v0.1.2，`main` = `8bbefe5`）
**交付状态：** Local MVP / Architecture Baseline（**不是** Production Ready）

> 本文件取代 0.1.0 时期的版本。规则不变：只有在本机或 CI 真实执行过、并能指出命令与
> 结果的项目才写 VERIFIED；其余一律留在 NOT VERIFIED，不拿推断代替证据。

## VERIFIED（本次实际执行，可复跑）

| 检查 | 结果 | 证据 |
|---|---|---|
| 契约测试（无 DB 依赖） | **48 passed** | `pytest contract_tests -q -s`（本机） |
| 完整测试套件（SQLite） | **24 passed, 2 skipped** | `pytest tests -q`（本机副本 + CI） |
| 完整测试套件（PostgreSQL 16） | **25 passed** | CI `full-suite` job（PG service） |
| 并发退款 / 同笔双审批行锁 | PASS | `tests/test_refund_concurrency_pg.py`（仅 PG） |
| ruff check + format | clean | `ruff check .` / `ruff format --check .`（本机 + CI） |
| 启动自检拒绝不安全配置 | PASS | `APP_ENV=production AUTH_MODE=dev python -c "import app.main"` → `ConfigurationError` |
| V-01..V-08 安全回归 | PASS | `contract_tests/test_security_units.py` |
| R-01..R-04 与退款状态机 | PASS | `test_preflight.py` / `test_mcp_identity.py` / `test_refund_state.py` / `test_refund_approval.py` |
| CI 阻断 | PASS | run for `8bbefe5`：contracts / ruff / full suite 全 success |

## NOT VERIFIED（本次环境无法执行）

| 项目 | 原因 / 下一步 |
|---|---|
| Docker build 与 compose 叠层 | 本机无 Docker CLI/daemon；只验证了 YAML 可解析，未实际起容器 |
| MCP Server 运行时 / Inspector | 本机未装 `mcp` SDK；只对 `app/mcp_auth.py` 做了单元测试 |
| DeepSeek 真实调用 | 无 API Key；`LLM_PROVIDER=mock` 下的路径已验证 |
| n8n 外部工作流交付 | 未配置 webhook / 凭据 |
| 退款 settlement 打款 | `settle` 只记帳到 `orders.refunded_amount`，无支付网关回调 |
| IdP / RBAC / 租户隔离 | 仅有 HMAC bearer + `tenant_id` 透传，未强制 |
| Alembic migration | 仍用 `Base.metadata.create_all` |

## NOT IMPLEMENTED（明确留到后续 Phase）

- Vue/React 用户聊天前端、Three.js 2D 运维页与事件回放。
- OIDC/JWT、RBAC/ABAC、多租户隔离与多角色管理。
- Transactional Outbox、Redis/NATS/Kafka 多实例事件总线。
- 支付网关退款执行、账本与对账。
- OpenTelemetry / Prometheus、压测、备份恢复演练。

## 本地验收命令

```bash
python -m pip install -e ".[dev]"

# 无需数据库栈
python -m pytest contract_tests -q

# 需要 sqlalchemy + aiosqlite（CI 另外用 PostgreSQL 再跑一次）
python -m pytest tests -q
ruff check . && ruff format --check .

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

只有在目标机上真实执行并留存退出码、JUnit XML、容器构建结果与 SDK 契约测试后，才可把对应项
从 NOT VERIFIED 改为 VERIFIED。

## 契约对齐（2026-11-11）

- Memory adapter（`app/memory/nanomemory_http.py`）对齐 NanoMemory 0.2.0 的真实路由：
  `GET /api/v1/memory/recall`（治理检索）、`GET /api/v1/memory/long-term`（信任补全）、
  `POST /api/v1/memory/long-term`（`source_type=user` 的偏好写入）。
- Governance adapter（`app/governance/memguard_adapter.py`）使用真实 mem-guard SDK（Guard、
  RuleCompiler、TrustLevel）；需要数据库权威的业务授权仍在 LocalPolicyGuard，
  `GOVERNANCE_BACKEND=memguard` 切换。
- Fixtures 由一次真实 NanoMemory TestClient 运行抓取：`tests/fixtures/nanomemory_contract.json`。
- 信任铸造仍是 adapter 自证，因为 mem-guard v0.1.1 发布版尚未带 issuer registry。
