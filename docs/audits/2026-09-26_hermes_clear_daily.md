# 2026-09-26 Hermes 调用 MAA「清日常」：接线、白名单与证据

> 背景：用户要求「让 hermes 调用 maa 清理日常」。本文件记录**做了什么、证据是什么、边界在哪、怎么回滚**。
> 所有写操作只发生在两处 Hermes 配置（config.yaml / jobs.json）+ 一份 .env 追加；未发任何真车（见 §6）。

## 1. 先说结论：为什么不是「直接调 MAA 本家」

**MaaAssistantArknights（MAA 本家）本身没有 MCP，也没有对外 HTTP 服务端。**

| 检查 | 结果 | 证据 |
|---|---|---|
| 二进制字符串扫 MCP | 0 命中 | `MAA.dll` / `MaaUtils.dll` / `MAA.exe` / `MaaWin32ControlUnit.dll` 对 `Mcp`/`mcp`/`HttpListener`/`TcpListener`/`WebSocket` 全 0 |
| `MAA.exe` 监听的 127.0.0.1 随机口 | 不是 API | 对端进程是 `MuMuVMMHeadless`（模拟器回连），属 MuMu12 extras 截图通道；HTTP 探测全部超时 |
| 官方 release / 组织仓库 / 文档站 | 无 MCP 条目 | v6.18.0 release body、`MAA_1\filelist.txt`（`mcp` 0 条）、org 仓库列表、docs 首页均无 MCP |
| MAA 本家唯一的对外控制面 | 拉模型、未启用 | `Gui.RemoteControl`：MAA POST `{user,device}` 到调度方 URL 取任务；本机配置全空 |

⇒ 能被 Hermes 调用的、真正让 MAA 干活的入口是**调度层**：

```
Hermes(profile mas, cron 52b88b0ffca0)
  └─ MCP maaorch-daily  →  http://127.0.0.1:19997/mcp/   (MAAOrch FastAPI, 41 工具)
       ├─ 只读：mas_status / mas_snapshot / mas_rerun_state / account_status / queue
       └─ 写：  mas_rerun(accounts, dry_run=True|False)
                 └─ services/mas_adapter → tools/auto_mas_rerun.py --apply
                      └─ 远端 AUTO-MAS(100.79.173.69:36163) 建「临时-补跑-*」队列 → dispatch/start
                           └─ MAA.exe（MaaAssistantArknights 本家）
```

## 2. 落盘的改动（3 处，均有备份）

| # | 文件 | 改动 | 备份 |
|---|---|---|---|
| 1 | `profiles\mas\config.yaml` | 新增 `mcp_servers.maaorch-daily`，**`tools.include` 硬白名单 6 个工具**；`platform_toolsets.cron` 追加 `maaorch-daily` | 见下表 |

该 server 的形态经历过一次升级（**最终形态 = stdio 代理**）：

| 阶段 | 形态 | 备份 |
|---|---|---|
| 20:0x 首版 | `url: http://127.0.0.1:19997/mcp/` + `headers.x-agent-token`（要求上游常驻） | `config.yaml.bak-cleardaily-20260926-195400` |
| 21:2x 终版 | `command: ...python.exe` + `args: [tools\mas_maaorch_daily_proxy.py]`（**代理按需拉起上游**，不再要求常驻） | `config.yaml.bak-stdio-20260926-212x` |
| 2 | `profiles\mas\.env` | 追加 `MAAORCH_API_TOKEN=<models/config.json 的 api_token>`（值不落文档） | `.env.bak-clear-daily` |
| 3 | `profiles\mas\cron\jobs.json` | 唯一任务 `52b88b0ffca0` 的 `prompt`：613 字只读巡检 → 1358 字「只读巡检 + 清日常补漏」；`enabled_toolsets` 保持 `null` | `jobs.json.bak-cleardaily-20260926-200135` |

白名单（注册期硬闸，`tools/mcp_tool_registration.py:211-225`；`include: []` 表示一个都不注册）：

```
mas_status, mas_snapshot, mas_rerun_state, account_status, queue, mas_rerun
```

**刻意不在名单里**：`smart_dispatch`（无 `dry_run`，一调即真下发；且走 MAAOrch 自研 runner，只覆盖本机 3 个账号，**不是产线 54 账号的路径**）、
`account_stop` / `maa_stop_all` / `maa_launch_source` / `queue_control` / `enqueue` / `dequeue` /
`emulator_*` / `kill_audit_decide` / `mas_cleanup`（后者会删掉正在跑的「临时-补跑-*」队列）。

## 3. 清日常补漏策略（写进那一个 cron 任务的提示词）

三个硬条件**同时**满足才发车，任一不满足即写明未发车原因：

1. 本地时间**不在批次守卫窗**内（AUTO-MAS 早班 05:00–05:33、晚班 19:30–20:03 ⇒ 守卫窗 05:00–05:40 与 19:20–20:20）；
2. 只读 `mas_status(days=1)` 显示**今日确有失败账号**（`today.failed_names` 非空）；
3. 只读预检显示**当前无正在运行的批次**。

动作顺序：`mas_rerun(accounts=<今日失败账号，≤8>, dry_run=True)` 预演 → 预演清单非空 → 同批 `dry_run=False` 真发车。
每轮最多一次；发车后只做只读复核、不等待不轮询；发车不可撤销。

## 4. 证据（可复现）

| 事实 | 证据 |
|---|---|
| MAAOrch MCP 真端点 | `POST http://127.0.0.1:19997/mcp/` → 200 + `mcp-session-id`；`/mcp` → 307；`tools/list` = **41**，与源码 AST 枚举逐项一致（missing/unexpected 均为空） |
| Hermes 已连上并真实调用 | `logs/agent.log`：20:02:48 `Job '52b88b0ffca0': ... MCP tool(s) available`；20:06:49–20:06:56 `mcp__auto_mas_monitor__get_health…` / `get_overview…` / `search_history…` 与 `mcp__maaorch_daily__mas_status`（7403 chars）、`mas_snapshot`（2731 chars）全部 completed |
| 白名单真的生效 | `cache/mcp_schema_cache.json` 的 `maaorch-daily` 键下只出现白名单内的工具名；`smart_dispatch` 不在其中 |
| 任务的工具面来自平台白名单 | `cron/scheduler.py:487-510`：per-job 为空 → `_get_platform_tools(cfg,"cron")` = `[auto-mas-monitor, maaorch-daily]`；`_merge_mcp_into_per_job_toolsets` 的"未点名就把全部 MCP 并进来"分支在 per-job 为 null 时不触发 |
| 写路径自带闸 | `network/mcp_server.py:280-287`（`mas_rerun(dry_run=True)` 默认）/ `:104-124` `_gate()`：`dry_run=True` 直接返回 `would_call`，**不下发请求** |

## 5. 生产真实状态（只读实测，2026-09-26 20:0x）

- 今日：`runs=5, ok=3, retry=1, fail=0`；首条 05:56、末条 19:33；`waiting=55`；远端 `MAA.exe` **10** 个（晚班批次正在跑）。
- 早班 05:00–05:33 的记录稀疏（05:56 才出现首条）⇒ 守卫窗按实测而不是按名义时刻放宽。
- `mas_rerun(accounts=[], dry_run=True)` 预演返回 `would_start: []`（当时无失败账号）—— 即"无缺口则不发车"。

## 5.5 冷启动实测（stdio 代理终版，2026-09-26 21:2x）

| 步骤 | 观测 |
|---|---|
| 杀掉上游 | `Stop-Process` 掉 `python main_web.pyw`，`listen19997=0` |
| Hermes CLI 直调 | `hermes -p mas -z` 让 agent 调 `mas_status` ⇒ **成功返回真实数据**（`today: accounts=31, ok=21, retry=10, fail=0, runs=41`），代理现场拉起上游 |
| 上游不在时自动触发 | 21:24:11 `builtin`（触发前 `listen19997=0`）⇒ 21:28:34 完成，**运行后 `listen19997=1`**，报告 `MCP_API_TOOL_COUNT=16（MAAOrch 6 + AUTO-MAS monitor 10）`、`ISOLATION=PASS`、`CLEAR_DISPATCH=未发车（今日无失败账号；晚班批次运行中，不叠发）` |
| 成本 | 262.8 s / 4 次 LLM / 6 次工具 / 182,342 token（比 url 形态的 374 s 更省） |

⇒ **上游不再需要常驻**：Hermes 调 MCP 时由代理按需拉起（首次 ≤45 s，MCP 工具调用超时默认 300 s，足够）。

## 6. 未做的事（诚实边界）

1. **本轮没有真发车**：唯一一次真实发车动作是 `dry_run=True` 预演（零远端副作用）；生产清日常由 AUTO-MAS 自己的批次在跑。
2. **上游需要常驻**：MAAOrch Python 上游（19997）按设计不常驻（空转约占一个核）。Hermes 调 MCP 前它必须在跑，否则 MCP 调用失败 ⇒ 任务只报"后端不可达"，**fail-closed 不发车**。
   启动：`tools\start_python_upstream.bat`（或 `pythonw main_web.pyw`）；本次以 PID 24252 常驻、19997 已监听。
3. ~~stdio 代理未接线~~ → **已接线（21:2x）**：`tools/mas_maaorch_daily_proxy.py` 现在是 Hermes 的 `maaorch-daily` server 本体（10 工具 / 134 测试 / `--check` EXIT 0），提供只读转发 + `mas_rerun`（原样转发，上游 `dry_run` 语义不变）+ `today` 缺口判定所需数据，并按需拉起上游。它同时还暴露 `daily_gap`/`daily_clear`（后者走 `smart_dispatch`，仅覆盖 MAAOrch 本机 3 账号，**不是产线路径**）—— 这两个名字**未**写进 Hermes 的 `tools.include`。
4. `auto-mas-monitor` 的 118 工具全量 server（`auto-mas-scrubbed`）依旧**未**被任何 cron 路径点名。

## 7. 回滚（任一条 30 秒内生效）

| 目标 | 操作 |
|---|---|
| 退回纯只读巡检 | 从 `tools.include` 删掉 `mas_rerun`（`platform_toolsets.cron` 的 `maaorch-daily` 保留也无写能力） |
| 完全摘掉 MCP | 删 `mcp_servers.maaorch-daily` 整块 + `platform_toolsets.cron` 里那行；或直接还原 `config.yaml.bak-cleardaily-20260926-195400` |
| 退回旧提示词 | 还原 `jobs.json.bak-cleardaily-20260926-200135` |
| 撤销 token | 从 `profiles\mas\.env` 删 `MAAORCH_API_TOKEN`（还原 `.env.bak-clear-daily`） |
## 8. 两个必须知道的运维事实（2026-09-26 21:0x 实测补记）

1. **改完 Hermes 配置必须重启网关，否则自动路径看不到新工具面。** 证据：
   - 20:43:04 的 `builtin` 运行（网关进程内执行）报告「本环境工具集未包含 mas_status / mas_rerun」，`CLEAR_DISPATCH=未发车（工具集不含 mas_rerun）`；
   - 同一时刻 `direct` 运行（`hermes.exe -p mas cron run`，新起进程）却能看到 `mcp__maaorch_daily__*` ⇒ **网关进程缓存了改动前的 `platform_toolsets.cron`**；
   - `hermes gateway restart`（新 PID 33024）之后，20:54:57 的 `builtin` 运行报告 `MCP_API_TOOL_COUNT=20（auto-mas-monitor 10 + maaorch-daily 10）`，并列出 `mas_status / mas_snapshot / mas_rerun / mas_rerun_state / account_status / queue` 且无任何禁用工具。
   （队友此前「无需重启网关」的判断对 `platform_toolsets` 不成立，以本条实测为准。）

2. **MAAOrch Python 上游（19997）不常驻，需要时先拉起。** 平台设计如此（空转 ~47% CPU）：`tools\maaorch_platform.bat` 只拉起 Node 门面（19999），Python 上游按需启动。
   - 拉起命令：`tools\start_python_upstream.bat`（或 `python main_web.pyw`）。
   - Hermes 侧 fail-closed：上游不可达时 MCP 调用失败，任务只报「后端不可达」，不会发车。
   - 消除该依赖的正解：把 Hermes 的 `maaorch-daily` 从 `url:` 改成 `command/args:` 指向 stdio 代理 `tools/mas_maaorch_daily_proxy.py`（它按需拉起上游）。该升级见 §10。

## 9. 成本与运行观测（同日）

| 运行 | 来源 | agent time | LLM | 工具 | token | 备注 |
|---|---|---|---|---|---|---|
| 20:43:04 | builtin | 491.0 s | 5 | 7 | 319,715 | 旧工具面（未重启网关） |
| 20:54:57 | builtin | 374.2 s | 4 | 5 | 217,266 | **新工具面**，正确判定未发车 |
| 20:02:44 | direct | 579.8 s | 5 | 8 | 373,157 | 含 direct 进程的 MCP 发现开销（约 3.5 分钟） |
| 19:57:04 | builtin | 279.1 s | 5 | 4 | 237,226 | 旧提示词 |

- 自动路径实测 **374 s / 217k token**（30 分钟节拍 ⇒ 约 5 h/天、10.4 M token/天），与收紧后基线（303 s）同量级。
- 20:54:57 运行同时给出运营发现：今日 20 个账号全部 DONE（16 一次过 + 4 重试成功、0 失败）；磁盘 C 剩 30 GB / E 剩 135 GB 低于警戒线；`never_ran(40)` 与晚班待跑(39) 相差 1 个账号，建议下轮复核。

## 10. 上游进程形态与后续硬化

- 2026-09-26 21:0x 实测：`python main_web.pyw`，PID 26588；19997 监听，MCP `initialize` 返回 200 + session。
- 用 WMI 起脱离会话的 `pythonw.exe main_web.pyw` **失败**（spawn_rc=0 但进程秒退，pythonw 无控制台看不到错误），未采用。
- 正解（进行中）：把 Hermes 的 `maaorch-daily` 改成 stdio 代理形态，由代理按需拉起上游 —— 既不改平台设计，也不依赖任何常驻进程。
