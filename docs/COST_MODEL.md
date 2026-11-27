# 单位成本模型（定价与配额的依据）

这份文档回答三个问题：**一个用户一个月花多少钱、钱花在哪、配额该定多少**。所有数字由
`scripts/cost_model.py` 生成，参数都在命令行里，改一个假设就能重算，不靠记忆。

```bash
python scripts/cost_model.py --users 1000
python scripts/cost_model.py --users 10000 --fixed-infra 600 --json
python scripts/cost_model.py --users 100000 --fixed-infra 3000
```

## 1. 成本构成

| 成本项 | 计费单位 | 由谁产生 | 当前默认假设 |
| --- | --- | --- | --- |
| LLM 输入 | token | 每次对话的 system prompt + 记忆上下文 + 用户消息 | 900 token/次 |
| LLM 输出 | token | 模型回复 | 260 token/次 |
| Embedding | token | 记忆写入 + 每次检索的 query 向量 | 写入 180、查询 40 |
| 向量与 trace 存储 | GB·月 | NanoMemory 的 SQLite 文件（向量 + 版本 + 痕迹） | 每条记忆 2 KB |
| 固定基础设施 | 月 | API、mem-guard、NanoMemory、Postgres、监控 | 1k 用户 $120，10k $600，100k $3000 |

单价默认值（**示例输入，不是厂商报价**）：输入 $0.14/1M、输出 $0.28/1M、embedding
$0.02/1M、存储 $0.10/GB·月。换成你的合同价再跑一次脚本即可。

## 2. 单人月成本（轻量使用场景）

每用户每月 120 条消息、20 次记忆写入、200 条记忆：

| 规模 | 可变成本 | 固定成本 | 合计 | 每用户 |
| --- | --- | --- | --- | --- |
| 1,000 用户 | $0.065 | $120 | $120.07 | **$0.120** |
| 10,000 用户 | $0.434 | $600 | $600.43 | **$0.060** |
| 100,000 用户 | $4.12 | $3,000 | $3,004.12 | **$0.030** |

结论：在这个用量下，**LLM token 是唯一显著的可变成本，向量存储几乎免费**（100k 用户
的向量 + trace 也只有约 41 GB / $4）。所以定价的分水岭不是存储，而是"每个用户每月问多少
次、上下文带多少记忆"。

## 3. 三档套餐配额（建议值）

| | Free | Pro（$8/用户/月） | Business（$29/用户/月） |
| --- | --- | --- | --- |
| 消息/月 | 100 | 2,000 | 10,000 |
| 记忆写入/月 | 10 | 500 | 5,000 |
| 记忆条数 | 50 | 5,000 | 50,000 |
| Embedding token/日 | 20,000 | 200,000 | 1,000,000 |
| 检索 token/日 | 50,000 | 500,000 | 2,000,000 |
| 估算成本/用户/月 | ~$0.02 | ~$0.42 | ~$2.91 |
| 毛利率 @ 标价 | — | ~95% | ~90% |

对应到 `mem-guard` 的环境变量：

```bash
MEM_GUARD_DAILY_EMBEDDING_TOKENS=200000
MEM_GUARD_DAILY_RETRIEVAL_TOKENS=500000
MEM_GUARD_RATE_LIMIT_PER_MINUTE=120
```

超限时网关返回 429（`quota_exceeded` 或 `rate_limit_exceeded`，带 `Retry-After`），
而不是让账单悄悄超支。配额是**每租户每用户每日**的账本，跨重启保留在
`state/usage.db`。

## 4. 成本护栏（已实现，不是规划）

| 护栏 | 位置 | 行为 |
| --- | --- | --- |
| 请求级限流 | mem-guard 令牌桶 | 超出即 429 + Retry-After，在调用模型之前 |
| 日预算 | mem-guard SQLite 账本 | 写入/检索/embedding token 分别计量，超限 429 |
| Embedding 缓存 | NanoMemory 进程内 LRU | 相同文本不重复计费 |
| 用量快照 | `GET /v1/guard/quota` | 返回 limits/used/remaining |
| 单次成本留痕 | NanoMemory trace | 每次调用记录 input/output token 与 estimated_cost |

## 5. 什么时候这个模型会失效

1. **长上下文套餐**：如果允许用户一次带 8k token 记忆，输入成本按比例上升，需要按"上下文
   长度"而不是"消息条数"定价。
2. **多模态**：图片/语音的 embedding 与推理成本结构不同，本模型不覆盖。
3. **重试与降级**：网关超时重试会放大成本；当前实现重试次数有上限（embedding 默认 2 次），
   但 LLM 侧的重试策略需要单独核算。
4. **存储增长**：trace 表按天增长，生产环境需要 TTL（建议 30–90 天）与归档，否则存储项会
   从"可忽略"变成"需要关注"。

## 6. 复算方法

```bash
# 只看某一档
python scripts/cost_model.py --users 5000 --messages-per-user 500 \
  --input-tokens 1200 --output-tokens 300 --price-per-user 8

# 换成实际合同价
python scripts/cost_model.py --input-price 0.10 --output-price 0.20 --embedding-price 0.01

# 机器可读
python scripts/cost_model.py --json
```

把输出贴进定价评审，比"感觉差不多"可靠。
