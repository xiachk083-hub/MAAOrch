# 交接验证：Hermes 巡检是否真的只读且被隔离

日期：2026-09-26 ｜ 结论：**隔离成立（配置级 + 行为级双重证据）**；但发现两个必须修的缺陷

## 1. 被验证的对象

- Hermes profile `mas`，MCP 条目：
  - `auto-mas-scrubbed`：全量 **118** 工具（含 44 个写/危险，如 `close_api_core_close_post`）
  - `auto-mas-monitor`：`MAAORCH_MAS_READONLY=1` 只读硬闸，**10** 工具
- 隔离配置 `profiles/mas/config.yaml` → `platform_toolsets.cron: [auto-mas-monitor]`
- 巡检任务 `52b88b0ffca0`（每 30 分钟），execution `e5215e48c2db4f7386cfce7b7eae9cff`

## 2. 证据一：Hermes 实际加载（自家客户端）

```powershell
hermes -p mas mcp test auto-mas-monitor    # ✓ Connected (3829ms)  Tools discovered: 10
hermes -p mas mcp test auto-mas-scrubbed   # ✓ Connected (3768ms)  Tools discovered: 118
```

## 3. 证据二：真实运行的调用前缀统计

对该次运行（日志 tag `cron_52b88b0ffca0_20260926_165057`）的 agent.log 统计：

| 前缀 | 出现次数 |
|---|---|
| `mcp__auto_mas_monitor__` | **48+（全部实际调用）** |
| `mcp__auto_mas_scrubbed__` | **0（无任何实际调用）** |

写类关键字（dispatch/start/stop/close/add/update/delete/import/export/reorder/cancel）在该运行日志中**未出现任何一次工具调用**。

## 4. 关键甄别：注册 ≠ 可调用

启动瞬间（16:51:02）日志里有：

```
INFO tools.mcp_tool: MCP server 'auto-mas-scrubbed' (stdio): registered 118 tool(s): ...
WARNING tools.mcp_tool: MCP tool name 'mcp__auto_mas_scrubbed__...' exceeds the 64-char provider limit
```

这是**进程级 MCP 注册**（所有 enabled server 都会注册），**不等于**该运行的 agent 可调用。
判定依据必须用「该运行是否出现过 scrubbed 前缀的**实际 tool 调用**」——结论是 **0 次**。

旁证：该运行持久化的 285KB `tool_describe` 结果中**不含** `auto_mas_scrubbed`。

## 5. 缺陷一（已修）：巡检提示词过重导致长跑

首次运行从 16:50:57 起持续 **14+ 分钟**、13+ 次 LLM 调用、48 次工具调用仍未收敛，
原因是提示词让模型逐队列遍历 25 个队列。

**已修**：`hermes -p mas cron edit 52b88b0ffca0 --prompt <lean>`
新提示词硬性限制「最多 8 次工具调用、禁止逐队列遍历、禁止 tool_search/tool_call 探索」，
只做四件事（健康、总览、今日 history、队列列表）。

## 6. 缺陷二（重要运行发现）：今日产线基本没跑

用只读 `/api/history/search`（mode=DAILY）逐日对比：

| 日期 | 有记录的账号数 | 有 DONE |
|---|---|---|
| 2026-09-23 | 53 | 51 |
| 2026-09-24 | 53 | 52 |
| 2026-09-25 | 53 | 50 |
| **2026-09-26（今天）** | **2** | **2** |

而 MAS 的 **24 个定时项全部 `Enabled: true`**（早班 05:00→05:33、晚班 19:30→20:03）。
即：**定时表是 armed 的，但今天只有 2 个账号跑了**。

账号名映射已排除误报：schedule 的 58 个账号与 history 的账号**完全对得上**
（今日 2 个账号都在 schedule 内，不存在"只在 history"的孤立名）。

这不是本交接造成的（本交接全程只读），但需要你确认原因（主机重启？MAS 被停？）。

## 7. 未被改动

MAS 原定时 before/after 逐字段比对：**DIFF = 0**
（25 队列、24 定时项、24 启用、时间点集合一致、每队列 item 数一致）。
