# OmniCare MVP 验证报告

**报告日期：** 2026-11-22（v0.2.0，分支 `feat/mcp-auth-provenance`）
**交付状态：** Local MVP / Architecture Baseline（**不是** Production Ready）

> 本文件取代 2026-11-11 的 v0.1.2 版本。规则不变：只有在本机或 CI 真实执行过、并能指出
> 命令与结果的项目才写 VERIFIED；其余一律留在 NOT VERIFIED，不拿推断代替证据。

## 本轮变化（摘要）

1. **JWT/OIDC 身份核心**：生产模式不再接受自签 HMAC（`AUTH_MODE=token`）或 `dev` 身份；
   JWT 校验支持 HS256/384/512、RS256/ES256、JWKS/kid 轮换、iss/aud、租户白名单、jti 撤销、
   scope 与时钟偏移；启动自检拒绝不安全组合。
2. **MCP 可信认证上下文**：MCP 传输层强制 `Authorization: Bearer <JWT>`；两个工具不再接收
   `user_token`/`gateway_token`/`user_id` 参数，身份来自令牌；`check_logistics` 只查令牌
   主体自己的订单；只读 annotations 在线上可见。
3. **记忆溯源审计链**：偏好记忆写入时生成 HMAC attestation（绑定 memory_id、backend、user、
   session、run、source_ref），检索时校验；缺失/被篡改的记忆在进入治理层之前被隔离；管理员
   可查 `GET /api/v1/memories/{id}/provenance`。
4. **NanoMemory 契约补齐**：NanoMemory 0.2.1 的 long-term create 接受 `provenance` 与
   `user_id`；OmniCare adapter 通过 `GET /api/v1/version` 协商单/多 session 模式，多 session
   模式由服务端创建并缓存 NanoMemory session（不再把 OmniCare session id 当 NanoMemory id）。
5. **HTTP 表面**：`/sessions`、`/chat` 的 body `user_id` 改为可选断言；`/health` 恢复无前缀
   别名（Docker HEALTHCHECK 依赖）。
6. **Web 控制台重设计**：去掉 Three.js/3D 场景，改为无构建、无外部字体的白色极简控制台（Synthelix
   品牌色：黑→`#6a00ff` 渐变 + `#654321` 棕）；实时事件流、五项 KPI、Agent 与治理状态；
   WebSocket 不可用时明确标注演示模式。

## VERIFIED（本次实际执行，可复跑）

环境：Windows，Python 3.12.15（`E:\n8\.venv312`），仓库工作副本。

| 检查 | 结果 | 证据 |
|---|---|---|
| 契约测试（无 DB） | **129 passed** | `python -m pytest contract_tests -q`（omnicare） |
| 完整测试套件（SQLite） | **43 passed, 3 skipped** | `python -m pytest tests -q`（omnicare）；3 skipped 为仅 PostgreSQL 的行锁/并发测试 |
| ruff check + format | clean | `ruff check .` / `ruff format --check .`（omnicare） |
| Web 控制台静态契约 | **4 passed** | `pytest contract_tests/test_ui_assets.py -q`；Edge headless 截图 `docs/ui-preview.png` |
| NanoMemory 完整套件 | **293 passed, 2 skipped** | `python -m pytest tests -q`（nanomemory）；2 skipped 为平台 symlink 限制 |
| mem-guard 完整套件 | **33 passed** | `python -m pytest tests -q`（mem-guard） |
| 启动自检矩阵 | PASS | production+jwt 成功启动；production+dev / production+token / staging+dev / oidc 缺 issuer 均抛 `ConfigurationError` |
| HTTP API JWT 验收 | **13/13 PASS** | 真实 uvicorn（AUTH_MODE=jwt）：无 token→401、仅 X-User-Id→401、JWT→201、伪造 body user_id→403、chat 无 body user_id→200、user 访问 admin→403、admin→200、垃圾 token→401、memory_written 事件含 source_ref |
| MCP 真实服务验收 | **15/15 PASS** | `python -m app.mcp_server`（AUTH_MODE=jwt）：无/坏/过期/缺 scope token→401、合法 token→200+session、tools/list 两个只读工具、annotations 正确、`check_logistics` 参数仅 `order_id`、Alice 查自己订单 ok、Bob 查 Alice 订单被拒、响应不含原始 token |
| NanoMemory 真实服务 + OmniCare adapter | PASS | 真实 NanoMemory 服务（single-session）上验证：POST provenance 往返、adapter 写偏好返回 source_ref、recall(user_id) 命中本人记录、不会把 OmniCare session 当作 NanoMemory session |
| 溯源管理接口 | PASS | 偏好聊天后 `GET /api/v1/memories/{id}/provenance`（admin）返回 `verified: true`、source_ref、session_id、run_id、tenant_id |
| 认证 CLI | PASS | `python -m app.auth_cli issue-token ...` 产出 3 段 JWT；`APP_ENV=production` 时拒绝签发 |
| CI 阻断 | 待 CI run | 本轮提交后以 GitHub Actions 结果为准；本地与 CI 使用同一套 contract/tests/lint 命令 |

## 可复跑的真实服务验收脚本

本次把真实服务验收做成了仓库脚本（无硬编码令牌，文档字符串里有启动命令）：

- `scripts/verify_mcp_live.py`：向真实 `python -m app.mcp_server` 发起 15 项检查（401 矩阵、
tools/list + annotations、工具参数无 token、Alice/Bob 订单隔离、响应不含原始 token）。
- `scripts/verify_http_live.py`：向真实 uvicorn（JWT 模式）发起 13 项检查（401/403、session/chat
身份绑定、admin 角色、memory_written 溯源事件）。
- `scripts/verify_nanomemory_live.py`：对真实 NanoMemory 服务验证 REST provenance 往返、adapter
映射、`user_id` recall 过滤与单 session 协商。

2026-11-22 本地真实服务上三个脚本分别以 15/15、13/13、全部通过结束。

## NOT VERIFIED（本次环境无法执行）

| 项目 | 原因 / 下一步 |
|---|---|
| PostgreSQL 行锁与并发退款 | 本机无 PostgreSQL 服务；CI `full-suite` 会用 postgres:16 service 跑同一套测试（`tests/test_refund_concurrency_pg.py`） |
| Docker build 与 compose 叠层 | 本机无 Docker CLI/daemon；已静态更新 `docker-compose.prod.yml` 为 JWT/OIDC 并做 YAML 解析校验，未实际起容器 |
| OIDC 对接真实 IdP | 只验证了本地 HS256 与 RS256 单元测试、JWKS 缓存路径；未接 Keycloak/Auth0 等 |
| MCP Inspector UI | 未安装/未运行 Inspector；自定义真实 MCP 客户端验收已通过 |
| 支付网关打款 | `settle` 仍只记帳；无 payout worker、对账和回调 |
| DeepSeek 真实调用 / n8n 外部工作流 | 无 API Key / 未配置 webhook |
| 分布式事件总线 | 仍是单进程内存 broker；Redis/NATS 尚未接入 |
| 多租户 schema 隔离 | 租户 claim 已做 allowlist 校验并写入溯源行；orders/sessions 尚无 tenant 列 |

## NOT IMPLEMENTED（明确留到后续 Phase）

- 用户端 Vue/React 聊天前台与事件回放；Web 控制台已交付静态版（截图由 Edge headless 生成，未做浏览器交互 E2E）。
- 完整 IdP/密钥轮换/数据库级吊销表、RBAC/ABAC 管理界面。
- Alembic migration、支付账本/对账、Transactional Outbox、OpenTelemetry/Prometheus、压测与备份恢复演练。

## 本地验收命令

```bash
# omnicare
python -m pip install -e ".[dev]"
python -m pytest contract_tests -q
python -m pytest tests -q          # 需要 sqlalchemy + aiosqlite；CI 另外用 PostgreSQL 再跑一次
ruff check . && ruff format --check .

# 本地/dev 启动（默认 AUTH_MODE=dev，仅 loopback）
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# JWT 模式（HTTP API 与 MCP 共用）
export AUTH_MODE=jwt
export AUTH_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
export AUTH_JWT_ISSUER=http://127.0.0.1:8000
export AUTH_JWT_AUDIENCE=omnicare-api
export MCP_REQUIRED_SCOPES=omnicare.read
python -m app.auth_cli issue-token --user demo_user_001 --role user --scopes omnicare.read
uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m app.mcp_server     # MCP 端 http://127.0.0.1:9000/mcp

# nanomemory（真实服务 + OmniCare adapter 验收用）
cd ../nanomemory && python -m pytest tests -q
# mem-guard
cd ../mem-guard && python -m pytest tests -q
```

只有在目标机上真实执行并留存退出码、JUnit XML、容器构建结果与 SDK 契约测试后，才可把对应项
从 NOT VERIFIED 改为 VERIFIED。

## 契约对齐（2026-11-22）

- Memory adapter（`app/memory/nanomemory_http.py`）对齐 NanoMemory 0.2.1：
  `GET /api/v1/version`（session 模式探测）、`POST /api/v1/sessions`（multi-session 时创建）、
  `GET /api/v1/memory/recall`（治理检索 + user_id 过滤）、`GET /api/v1/memory/long-term`（信任补全）、
  `POST /api/v1/memory/long-term`（`source_type=user` + `provenance` + `user_id` 偏好写入）。
- Governance adapter（`app/governance/memguard_adapter.py`）继续使用真实 mem-guard SDK 的
  Guard/RuleCompiler/TrustLevel；需要数据库权威的业务授权仍在 LocalPolicyGuard。
- 溯源 digest 的纯函数拆到 `app/services/attestation.py`，使无 SQLAlchemy 的 contract job
  也能验证；持久化与查询在 `app/services/memory_provenance.py`。
- 信任铸造仍带 adapter 自证成分：mem-guard 0.1.1 没有 issuer registry；本轮用 OmniCare 侧
  HMAC attestation + NanoMemory provenance 双重绑定收敛，未宣称等价于第三方 issuer。
