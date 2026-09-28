# Hermes 接管 AUTO-MAS 调度（层一）：变更记录与回滚

> 日期：2026-09-27 01:0x–01:5x ｜ 决定人：用户「不用 AUTO-MAS 的定时，直接让 Hermes 来做」
> 结论：**AUTO-MAS 的 24 条定时已全部关停，发车改由 Hermes 的 cron 触发**；发车动作是确定性的（不烧 token），Hermes 负责班次前体检、异常处置与留痕。

## 1. 接管了什么（AUTO-MAS 侧的真相，实测）

24 条班次队列 × 每条 1 个定时项，原本全部 `TimeEnabled=true`、`Enabled=true`、7 天、`StartUpEnabled=false`：

| 班次 | 队列 | 时刻 |
|---|---|---|
| 早班 | 日常定时1–12 | 05:00 / 05:03 / 05:06 / 05:09 / 05:12 / 05:15 / 05:18 / 05:21 / 05:24 / 05:27 / 05:30 / 05:33 |
| 晚班 | 晚班定时1–12 | 19:30 / 19:33 / 19:36 / 19:39 / 19:42 / 19:45 / 19:48 / 19:51 / 19:54 / 19:57 / 20:00 / 20:03 |

全局工具（GameSign / ArknightsPC）本就禁用 ⇒ **要接管的只有这 24 个触发点**，没有别的隐藏副作用。
发车动作 = `POST /api/dispatch/start {"taskId": "<队列uid>", "mode": "AutoProxy"}`。

## 2. 现在谁在发车

```
Hermes cron（mas profile, job 2600f6415f16  mas-shift-dispatch, */3 5,19,20 * * *, no-agent）
  └─ profiles/mas/scripts/mas_shift_reconcile.py        （薄包装，透传 stdout/退出码）
       └─ tools/mas_dispatch.py --reconcile --apply     （确定性对账发车）
            └─ POST {MAAORCH_MAS_API}/api/dispatch/start {taskId, mode:"AutoProxy"}
```

- **班次表由 Hermes 自己持有**（`tools/mas_dispatch.py` 的 `SHIFT_TABLE`），不再依赖 AUTO-MAS 的定时项；
  `--list` 会比对 MAS 侧定时项并报"漂移/未关"。
- `--reconcile` 语义：**到点且今天没发过 ⇒ 发**；已发过 ⇒ 跳过（幂等）；超过追发窗口（默认 30 min）⇒ 只告警不发。
  因此 cron 每 3 分钟一跳，漂移/重启/单次失败都能被下一跳自愈，且**不会重复发车**。

## 3. 闸门（真发车前必须全过，任一不过即拒绝且零上游写请求）

1. 队列名白名单 `^(日常|晚班)定时([1-9]|1[0-2])$`；
2. AUTO-MAS 可达（`POST /api/scripts/get` 探测）——不可达 fail-closed；
3. 单飞：同一队列同一天只发一次（`runtime/mas_dispatch_state.json`）；重复须 `--force`；
4. `MAAORCH_DISPATCH_NO_GO=1` 硬闸；
5. 返回体必须 `code==200` 且带 `taskId`，否则记 `failed`。

审计：`runtime/mas_dispatch_log.jsonl` 每次真发车一行。

## 4. 变更清单

| # | 对象 | 变更 | 回滚 |
|---|---|---|---|
| 1 | AUTO-MAS 24 条定时项 | `Enabled=false`（`Days`/`Time` 原样保留） | `python tools/mas_timers.py --enable` |
| 2 | `tools/mas_dispatch.py`（新） | 确定性派发器（`--list/--check/--reconcile/--shift/--queue`，默认 dry-run） | 删文件即可（不影响 AUTO-MAS） |
| 3 | `tools/mas_timers.py`（新） | 定时开关（`--list/--disable/--enable [--only daily|evening]`） | —— |
| 4 | `profiles/mas/scripts/mas_shift_reconcile.py`（新） | Hermes cron 的脚本入口（薄包装） | 删文件 |
| 5 | Hermes cron 任务 `2600f6415f16` `mas-shift-dispatch` | `*/3 5,19,20 * * *`，no-agent 脚本模式 | `hermes -p mas cron pause 2600f6415f16`（或 remove） |
| 6 | 原巡检任务 `52b88b0ffca0` | **未改**，仍是 every 30m 只读巡检+补漏 | —— |

## 5. 已验证（2026-09-27 01:5x）

- `tools/mas_dispatch.py --check` → OK；`--list` → 24 条班次表，MAS 侧漂移 **无**；
- `python tools/mas_timers.py --list` → 24/24 `Enabled=False`；
- `--shift daily`（dry-run）→ 12 条全部 `would_dispatch`，queue uid 全部解析成功；
- cron 管道：`hermes -p mas cron run 2600f6415f16` → `Ran now: succeeded`，execution `f6882109763b4ec3bc7b2601d40869da`（无事可做 ⇒ 静默）；
- `hermes -p mas cron list` → 该任务 active，**next run 2026-09-27T05:00:00+08:00**。

## 5.5 Agent 配置变更（2026-09-27 01:3x，用户："你配置 Hermes 的 agent 配置"）

原来巡检 agent 只有只读 MCP：**看得见漏发却补不了**。本轮把"调度补位"这一件事补进 agent 工具面：

| # | 对象 | 变更 | 回滚 |
|---|---|---|---|
| 1 | `tools/mas_dispatch_mcp.py`（新） | 窄工具面 MCP，**只有 3 个工具**：`list_schedule`（只读）/ `dispatch_due`（对账发车，默认 dry_run）/ `dispatch_queue`（单条，默认 dry_run）。闸门全部复用 `mas_dispatch.py` | 删文件 + 摘配置 |
| 2 | `profiles/mas/config.yaml` → `mcp_servers.mas-dispatch` | stdio 拉起上面的 MCP（Python312） | 备份 `config.yaml.bak-scheduler-20260927-0130` |
| 3 | `.../config.yaml` → `platform_toolsets.cron` | 追加 `mas-dispatch` ⇒ cron 工具面 = auto-mas-monitor(10) + maaorch-daily(6) + mas-dispatch(3) = **19** | 删该行 |
| 4 | 巡检任务 `52b88b0ffca0` 提示词 | 升级为"**巡检 + 调度补位**"：第二步先 `list_schedule()` 对账，`due_not_sent` 非空时 `dispatch_due(dry_run=True)` 预演 → 确认后真发（每轮 ≤1 次）；`late_beyond_grace` 只告警。原巡检/补漏逻辑保留 | `jobs.json.bak-scheduler-20260927-0130` |
| 5 | 网关 | **必须重启**（platform_toolsets / MCP 变更对 builtin 运行不生效的硬教训）—— 已 `hermes gateway restart`（新 PID 22336，drain 干净） | —— |

验证：`hermes -p mas mcp test mas-dispatch` → **✓ Connected, Tools discovered: 3**；
cron 两条任务在线：`mas-readonly-inspection`（every 30m）+ `mas-shift-dispatch`（`*/3 5,19,20 * * *`，next run 05:00:00）。

设计取舍：**触发用脚本任务（零 LLM、每 3 分钟、幂等），补位用 agent 工具（有判断、可解释、只在漏发时动手）** ——
agent 不承担 3 分钟节拍（那是 480 次/天的 LLM 成本），只承担"漏了没漏、要不要补"的判断。

**端到端验证（2026-09-27 01:51:33 的真实 builtin 巡检回执，未经我改写）**：

```text
MCP_API_TOOL_COUNT=19（auto-mas 10 + maaorch 6 + dispatch 3）
ISOLATION=PASS（19 个工具均无禁用项）
SCHEDULE=无遗漏，无补位（due_not_sent=0、late=0、MAS 定时无漂移、可达；09-27 01:46，首班 05:00 未到点）
```

⇒ 重启网关后，无人值守的 agent **确实看到了新工具面**（19 而非 16）并**实际调用了 `list_schedule()`**；
这正是 RUNBOOK 里"改完 config 必须重启网关"那条教训的闭环验证。

## 6. 风险与兜底（诚实边界）

1. **Hermes 现在是单点**：gateway/本机挂了 ⇒ 当晚没人发车（AUTO-MAS 定时已关）。
   现有 30 分钟巡检任务能**发现**（05:30 前后若 0 个 MAA、今日 0 条记录会报），但不能**补救**。
   建议（未做，等用户点头）：加一个**独立于 Hermes** 的 Windows 计划任务，同样每 3 分钟跑
   `tools/mas_dispatch.py --reconcile --apply` —— 因为状态文件是幂等的，双触发不会双发车，只多一层保险。
2. **机器 04:00 / 19:00 计划重启**：重启后 AUTO-MAS 未就绪时，脚本会 fail-closed 拒绝并在窗口内由下一跳重试（30 分钟追发窗口内），超窗则只告警。
3. **改动只影响下一次发车**（AUTO-MAS 的 `Task.prepare()` 对队列拍快照）。
4. 队列定义、账号、脚本配置**一律未动**（本次只动"何时触发"）。
