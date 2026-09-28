# Hermes 巡检 agent-time 量化（profile=mas，job=52b88b0ffca0）

生成时间：2026-09-26T21:29:19.682486+08:00 ｜ 数据来源：cron/executions.db + cron/usage_audit.jsonl + state.db（只读）

## 1. 结论

- 自动巡检健康：**无人值守路径健康**（最近 3 次自动 fire 全部完成）
- 单次巡检 agent time（中位数，completed 样本）：**339 s**
- 节拍：每 30 分钟一次 ⇒ 48 次/天；据此投影 **4.51 h/天**（占全天 18.8%）
- 单次 LLM/工具调用（中位数）：5 / 6；单次 token（中位数）：233415
- token 投影：**11203920 / 天**
- 样本：共 11 次尝试（完成 8 / 进行中 0 / 失败 3），按来源 {"builtin": 8, "direct": 3}
- 失败分类：运行时环境错配：gateway 的 sys.path 挂着不匹配的 pydantic_core（3.11 venv vs 3.14 解释器） ×3

## 2. 逐次明细

| 开始时间 | 来源 | 结果 | agent time | LLM | 工具 | token | 失败分类 |
|---|---|---|---|---|---|---|---|
| 2026-09-26 21:24:11 | builtin | completed | 262.8 s | 4 | 6 | 182342 |  |
| 2026-09-26 20:54:57 | builtin | completed | 374.2 s | 4 | 5 | 217266 |  |
| 2026-09-26 20:43:04 | builtin | completed | 491.0 s | 5 | 7 | 319715 |  |
| 2026-09-26 20:02:44 | direct | completed | 579.8 s | 5 | 8 | 373157 |  |
| 2026-09-26 19:57:04 | builtin | completed | 279.1 s | 5 | 4 | 237226 |  |
| 2026-09-26 19:23:04 | builtin | completed | 199.8 s | 3 | 4 | 96760 |  |
| 2026-09-26 18:52:33 | builtin | failed | 0.4 s | — | — | — | 运行时环境错配：gateway 的 sys.path 挂着不匹配的 pydantic_core（3.11 venv vs 3.14 解释器） |
| 2026-09-26 18:16:45 | direct | completed | 303.0 s | 6 | 5 | 229604 |  |
| 2026-09-26 18:12:05 | builtin | failed | 0.2 s | — | — | — | 运行时环境错配：gateway 的 sys.path 挂着不匹配的 pydantic_core（3.11 venv vs 3.14 解释器） |
| 2026-09-26 17:41:05 | builtin | failed | 0.4 s | — | — | — | 运行时环境错配：gateway 的 sys.path 挂着不匹配的 pydantic_core（3.11 venv vs 3.14 解释器） |
| 2026-09-26 16:50:57 | direct | completed | 1160.9 s | 20 | 85 | 2591635 |  |

## 3. 口径与边界

- agent_time = executions.finished_at - started_at（该次 fire 的进程存活时长）。
- llm_calls / tool_calls / token 取自 state.db.sessions（id LIKE 'cron_%'），按开始时间就近对齐。
- 失败 fire 的 agent_time 是秒级空转、且没有 session，因此投影只用 completed 样本，避免均值被稀释。
- 估算成本字段（estimated_cost_usd）在当前 provider 下多为 0，只作占位，不作结论。
- 本工具只读：以 mode=ro 打开 Hermes 的 sqlite，不写入 Hermes home。

关联：docs/HERMES_MAS_HANDOVER.md（交接契约）、tools/mas_catchup_check.py（补漏判据，只提议不发车）。
