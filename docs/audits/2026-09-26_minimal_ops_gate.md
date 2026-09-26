# 前端整体退役前置闸门：/status 够不够用 + 必需路由白名单

> 2026-09-26 · api-curator（task-12）· 基础输入：`docs/audits/2026-09-26_api_trim_plan_p2.md`（task-7 矩阵）
> 只读分析，**未改任何代码**。分析时 19999(Node) 与 19997(Python) 均未监听（§7 命令 1），
> 故 /status 的运行时行为是**静态判定**（由代码推导），第 2 节结论需服务起来后实测复核。

## 0. 结论（直接回答闸门问题）

**不够用。前端全退后 /status 只能支撑 2 个 MAS 写动作，人类会失去 6 类操作能力。**

| 现状 | 证据 |
|---|---|
| /status 是一个「只读展示 + 2 个产线写按钮」页 | PAGE 常量 mjs:257-330；标题自称「只读第一版」mjs:266-267 |
| 展示区**当前就会抛错**（读了不存在的字段） | PAGE mjs:318 `s.queue.count` vs dashboardStatusPayload mjs:503-512 无 `queue` 键 |
| 6 类能力退役后不可达 | 队列入队/出队/暂停/恢复、kill_audit 人工放行、模拟器启停重启、MAA 启动/停止、真实运行态、真队列可见性（§3） |
| 必须先补 | 状态区字段补全 + 真值改透传（G1/G2）+ 展示补齐（G6）+ 放行/启停/队列入口（G3/G4/G5） |

## 1. /status 实际提供什么

路由：`server/maaorch-web.mjs:911-914`（`if (p === '/status')` 返回 PAGE 常量，定义在 mjs:257-330）。

### 1.1 写动作：2 个（唯一真正可操作的部分）

| 按钮 | 前端调用 | Node 处理 | 落点 | 证据 |
|---|---|---|---|---|
| 补跑 | `POST /api/mas/rerun` | **原生分支** | `runAction('mas/rerun', ['tools/auto_mas_rerun.py','--accounts',...])` | PAGE mjs:272,290-302；分支 mjs:862-868；spawn mjs:154-166 |
| 清空临时补跑队列 | `POST /api/mas/cleanup` | **原生分支** | `runAction('mas/cleanup', ['tools/auto_mas_rerun.py','--cleanup'])` | PAGE mjs:273,296-299；分支 mjs:869-872 |

两者默认 dry-run，勾「真执行」才加 `--apply`（mjs:292,298,866-871）；每次调用写 `runtime/node_dashboard_actions.log`（mjs:153-156）。**不依赖 Python**。

### 1.2 只读区：5 个 GET，全部 Node 原生（PAGE mjs:308-328）

| 面板 | 端点 | Node 实现 | 数据真实性 |
|---|---|---|---|
| 账号列表（id/name/client/emu/stages/suspended/failures） | `GET /api/status` | `dashboardStatusPayload()` mjs:503-512 | **running 恒 false**（取 `a.__running`，全仓无写入方，见 §2-4） |
| 队列项数 / parallel_max / 班次 | 同上（`s.queue`、`s.parallel_max`、`s.schedule_mode`） | 同上 | **三个字段不存在 → 抛错**（§2-1） |
| 产线今日（补跑/日报/日报文件） | `GET /api/mas/status` + `GET /api/production` | masPayload mjs:206-218；productionPayload mjs:224-255 | 真（读 `runtime/mas_watch_state.json`、`runtime/reports/*.md`） |
| 最近跑局 | `GET /api/runs` | recentRuns(12) mjs:100+ | 真（解析 `runtime/_ce6/*.attempt.log`） |
| 本进程自检 pid/uptime/RSS | `GET /api/health` | mjs:779-785 | 真，但只是 **Node 进程**自检，不是平台健康 |

刷新方式：整页 `location.reload()` 每 30s（mjs:327）。

### 1.3 /status 没有调用、但 Node 原生已有（可零路由成本补进页面）

`GET /api/queue`（mjs:794，**空壳**，§5-G2）、`GET /api/logs?lines=N`（mjs:801，真读 `logs/debug.log` 尾部）、`GET /api/emulators`（mjs:802 → emulatorsPayload mjs:728-752：真读 MuMu `vm_config.json` + 端口监听）、`GET /api/connect/status`（mjs:803）、`GET /api/node/dashboard`（mjs:804：容量/进程/GPU）、`GET /api/accounts`（mjs:800）、`GET /api/stats`（mjs:799）、`GET /api/stages`（mjs:795）、`GET /api/config`（mjs:797）、`GET /api/settings/smart`（mjs:798）、`GET /api/sse`（mjs:814）、`GET /api/_proxy_stats`（mjs:809-812，透传命中统计）。

## 2. 页面当前是否可用（静态判定）

1. PAGE mjs:318 读 `s.queue.count`，而 `GET /api/status` 返回 `dashboardStatusPayload()` = `{accounts, pipeline_running, running}`（mjs:503-512）——**没有 `queue` 键** ⇒ `TypeError`，被 mjs:328 的 `.catch` 捕获，整个状态区被替换成「加载失败：…」。
2. 同处还引用 `s.parallel_max`、`s.schedule_mode`（mjs:318）与 `s.updated_at`（mjs:326），同样不存在（即使不抛错也会显示 undefined）。
3. 其余引用都在：`m.files[0]`（masPayload mjs:214）、`p.rows`/`p.reports`（mjs:251-252）、`r.runs`、`h.pid/node/uptime_s/rss_mb/heap_mb`（mjs:781-784）。
4. 运行态字段 `__running`/`__queued`/`__elapsed`/`__today_runs`/`__sanity`/`__last_clear` **全仓只有消费方、没有写入方**（grep 命中 4 处，全在 mjs:454/507/538/571）⇒ Node 侧运行态结构性恒 false、电量恒空。
5. 死代码：`statusPayload()`（mjs:178-205）定义后从未被调用（grep 只有定义行）。

**判定**：产线动作区可用；**状态区不可用**。修复成本很小（Node 侧补 3 个字段，或改调 Python 的真 `/api/status`），但必须先修——否则「退役后唯一入口」是一个报错页。

## 3. 八项运维动作逐条判定

| # | 动作 | 退役后能否完成 | 依据 | 缺口 |
|---|---|---|---|---|
| 1 | 看每台模拟器 / 账号状态 | **部分不可** | 账号列表在（Node 读 `models/config.json`，mjs:503-512），但 running 恒 false；**模拟器列表页面完全没有**（Node 其实有真实实现 mjs:728-752、`/api/emulators`） | G1/G6 |
| 2 | 看队列 | **不可** | `s.queue` 不存在（§2-1）；Node `/api/queue` 是硬编码空壳 `{pending:[],active:[],pending_count:0,...}`（mjs:514-517，注释自承「本地无队列 ⇒ 空」）；真队列只在 Python `GET /api/queue`（launch_queue） | G1/G2 |
| 3 | 看 MAA 是否在跑 | **不可** | Node 侧无真值（§2-4）；真值在 Python `GET /api/status`（api_fastapi.py:298-321：tasklist MAA.exe + `runner._active` + elapsed/adb）与 MCP `inspect_processes`（:3764）。且 Node 的 `/api/status` **原生抢答**会遮蔽 Python 真值 | G2 |
| 4 | 入队 / 出队 / 暂停 / 恢复 | **不可（页面无入口）** | 端点齐备但全在 Python：`POST /api/queue/enqueue|dequeue|pause|resume`（api_fastapi.py:1906/1929/1956/1964）；Node 无原生分支 ⇒ 只剩 MCP 工具或 curl | G5 |
| 5 | kill_audit 人工放行 | **不可，且为最高危缺口** | `POST /api/kill_audit/decide`（api_fastapi.py:2174-2191）是唯一人工放行口；闸门本体 `models/kill_audit.py` 的 `request/expire_all` 由 runner.py:665-677、launch_queue.py:1426-1437 进程内调用；/status 无入口、25 个 MCP 工具里也没有 | G3 |
| 6 | 启动 / 停止 / 重启模拟器 | **不可** | 页面无按钮；端点在 Python：`POST /api/emulator/{idx}/{action}`（:2380）、`/api/emulator/batch/{action}`（:2318）、`/api/connect/{aid}/stop|restart`（:2825/2831） | G4 |
| 7 | 启动 / 停止 MAA | **不可** | 同上：`POST /api/account/{idx}/launch`（:1867）、`/stop`（:1876）、`/api/action/stop_all`（:2149）；无 MCP 工具 | G4 |
| 8 | 看日志 | **部分** | 只有「最近跑局」表（attempt log）；无 debug.log tail。Node 原生 `/api/logs` 已实现（mjs:682,801） | G6 |

## 4. 必需路由白名单（删路由的唯一依据）

口径：**Python 侧 32 条**（= MCP 20 + 判据外延 8 + 外部/工具 4）。另有 Node 原生端点与 MCP 工具清单（4.4/4.5），它们同样属于「退役后不能少」。

### 4.1 A 组：MCP 后端（20 条）

| 行 | 方法 路径 | 行数 | 保留理由 |
|---|---|---|---|
| 298 | `GET /api/status` | 25 | MCP status；**真运行态唯一来源**（tasklist MAA.exe + runner._active，api_fastapi.py:298-321）；/status 页要它（§5-G2） |
| 339 | `GET /api/node/dashboard` | 162 | MCP dashboard（容量/进程/内存） |
| 638 | `GET /api/stats/dashboard` | 39 | MCP stats_dashboard |
| 741 | `POST /api/probe/stage` | 57 | MCP probe_stage；services/depot_scheduler.py:195 |
| 879 | `GET /api/probe/status` | 13 | MCP probe_status |
| 944 | `GET /api/depot/plan` | 13 | MCP depot_plan |
| 1048 | `GET /api/activity/stages` | 5 | MCP activity_stages |
| 1067 | `POST /api/activity/stages` | 24 | 同路径写侧（MCP 只读；写侧目前只有前端用） |
| 1091 | `GET /api/account_status` | 74 | MCP account_status |
| 1165 | `GET /api/health` | 6 | MCP health（Python 版含 runtime_health 诊断，与 Node 进程自检不同） |
| 1200 | `GET /api/events/list` | 9 | MCP events |
| 1309 | `GET /api/queue` | 74 | MCP queue；**真队列唯一来源**（Node /api/queue 是空壳，§5-G2） |
| 1906 | `POST /api/queue/enqueue` | 23 | MCP enqueue（入队） |
| 1929 | `POST /api/queue/dequeue` | 20 | MCP dequeue（出队） |
| 1956 | `POST /api/queue/pause` | 8 | MCP queue_control("pause")（队列暂停） |
| 1964 | `POST /api/queue/resume` | 8 | MCP queue_control("resume")（队列恢复） |
| 3012 | `POST /api/action/smart_selected` | 69 | MCP smart_dispatch（核心调配动作） |
| 3572 | `GET /api/inspect/link_map` | 103 | MCP link_map |
| 3675 | `GET /api/inspect/account/{name}` | 89 | MCP inspect_account |
| 3764 | `GET /api/inspect/processes` | 60 | MCP processes（「MAA 是否在跑」的第二真源） |

### 4.2 B 组：保留判据外延（8 条）

| 行 | 方法 路径 | 行数 | 保留理由 |
|---|---|---|---|
| 722 | `GET /api/mas/status` | 5 | MAS 控制面读；/status 页产线区（Node 原生分支也实现了一份，mjs:805） |
| 727 | `POST /api/mas/rerun` | 5 | MAS 补跑；/status 页按钮 1（Node 原生分支 mjs:862-868） |
| 732 | `POST /api/mas/cleanup` | 9 | MAS 清临时队列；/status 页按钮 2（Node 原生分支 mjs:869-872） |
| 798 | `POST /api/probe/anni_weekly` | 46 | 保留判据 probe/*（辅助探针写入口） |
| 844 | `GET /api/probe/results` | 5 | 保留判据 probe/*（探针结果查询） |
| 849 | `POST /api/probe/apply_plan` | 30 | 保留判据 probe/*（探针计划落地） |
| 1262 | `GET /api/debug/state` | 47 | 调试必需（现场状态快照，人工排障唯一入口） |
| 1949 | `POST /api/queue/clear` | 7 | 保留判据 queue*（清空队列；与 enqueue/dequeue 同族） |

### 4.3 C 组：外部/工具/入站回调（4 条）

| 行 | 方法 路径 | 行数 | 保留理由 |
|---|---|---|---|
| 1576 | `GET /api/accounts` | 36 | tools/deploy.py:193 健康检查（打 19999 门面） |
| 2195 | `POST /api/maa_remote/task` | 15 | 【MAA 入站】每秒轮询 GetTask（config_injector.py:366 注入该 URI） |
| 2210 | `GET /api/maa_remote` | 24 | MAA 远程控制状态视图（api_fastapi.py:2210） |
| 2234 | `POST /api/maa_remote/status` | 84 | 【MAA 入站】上报任务结果（config_injector.py:367 注入该 URI） |

### 4.4 Node 侧必须保留的原生端点（不在 api_fastapi.py，但同属退役前提）

`GET /api/health`(779)、`/api/status`(793)、`/api/queue`(794,需改成非空壳或改透传)、`/api/stages`(795)、`/api/account_status`(796)、`/api/config`(797)、`/api/settings/smart`(798)、`/api/stats`(799)、`/api/accounts`(800)、`/api/logs`(801)、`/api/emulators`(802)、`/api/connect/status`(803)、`/api/node/dashboard`(804)、`/api/mas/status`(805)、`/api/rogue/status`(806)、`/api/production`(807)、`/api/runs`(808)、`/api/_proxy_stats`(809)、`/api/sse`(814)、`POST /api/mas/rerun`(862)、`POST /api/mas/cleanup`(869)、`POST /api/account/toggle`(874)、`/api/copilot/*`(849/882 → server/copilot_node.mjs)；静态资源与 `/`、`/status`(897-947)。

### 4.5 MCP 工具（25 个，不随前端退役变化）

只读 13（MAA）+ 写 5（MAA）+ 只读 5（MAS）+ 写 2（MAS）；与 4.1 的 20 条路由一一对应（`/api/queue/{action}` 展开为 pause+resume 两条；`mas_*` 7 个走进程内 `services/mas_adapter`，不经 REST）。

### 4.6 SPA catch-all（`GET /{full_path:path}`，api_fastapi.py:3824，32 行）

**可退**：Node 直接 `readFileSync('ui/web/index.html')` 服务 `/`（mjs:899）并自读磁盘服务静态资源（mjs:918-943），退役后 Python 不再需要它。前置：`main_web.pyw:348-352` 的 `webbrowser.open(url)` 与 `:375` 托盘「打开浏览器」指向 Node `/status`（当前指向 Python UI，url 见 main_web.pyw:348）。

### 4.7 闸门驱动的候选追加（决策项）

下列路由**不在**上述 32 条白名单里，但按 §3 的判定，若要求人类仍能完成对应动作，必须二选一（保留路由 或 加 MCP 工具）：

| 动作 | 相关路由 | 建议 |
|---|---|---|
| kill_audit 人工放行 | `POST /api/kill_audit/decide`（2174）、`GET /api/kill_audit`（2157）、`GET /api/kill_audit/pending`（2165） | **优先加 MCP 工具**（kill_audit_list/decide，写工具默认 dry_run）；退而保留这 3 条 + /status 按钮 |
| 模拟器启停重启 | `POST /api/emulator/{idx}/{action}`（2380）、`/api/emulator/batch/{action}`（2318）、`POST /api/connect/{aid}/stop|restart`（2825/2831） | 加 MCP 工具（emu_start/stop/restart，带 dry_run）或 /status 按钮 + 保留 1-2 条 |
| MAA 启动/停止 | `POST /api/account/{idx}/launch`（1867）、`/stop`（1876）、`/api/action/stop_all`（2149） | 同上（maa_start/stop） |
| 队列四动作的人类入口 | 已在白名单（A 组）+ `GET /api/queue` | 只需 /status 加 4 个按钮（Node 透传，因无原生分支） |

## 5. 缺口清单与补法

| 编号 | 缺口 | 补法 | 是否新增 Python 路由 | 成本 |
|---|---|---|---|---|
| G1 | /status 状态区读不存在的字段 → 整块报错；账号 running 恒 false | Node 侧 `dashboardStatusPayload()` 补 `queue/parallel_max/schedule_mode/updated_at` 4 个字段，运行态改由 G2 解决 | 否 | 小 |
| G2 | 真运行态/真队列被 Node 原生**抢答**遮蔽（`/api/status`、`/api/queue`） | 把这两条 Node 分支改为**透传**到 19997（Python 因 MCP 必须常驻），或 Node 直接读 Python | 否（两条路由已在白名单） | 小 |
| G3 | kill_audit 无人工放行入口 | 加 MCP `kill_audit_decide`/`kill_audit_list`（推荐）或 /status 按钮 + 保留 3 条路由 | 建议否；保留则 +3 条 | 中 |
| G4 | 模拟器/MAA 启停无入口 | 加 MCP `emu_start/emu_stop/emu_restart/maa_start/maa_stop`（带 dry_run 与账号映射校验）或 /status 按钮 + 保留路由 | 建议否；保留则 +3~4 条 | 中 |
| G5 | 队列 4 动作无人类入口 | /status 加 4 个按钮（Node 目前无原生分支 → 透传到 Python） | 否 | 小 |
| G6 | 模拟器列表、debug.log 日志未展示 | /status 调 Node 原生 `/api/emulators`、`/api/logs?lines=200` 增加两块 | 否 | 小 |
| G7 | 页面自称「只读」却有 2 个写按钮；30s 整页 reload | 文案改「最小运维页」；reload 改局部刷新 | 否 | 小 |

## 6. 前端退役的前置条件（闸门通过标准）

全部满足才允许进入删路由（task-7 的 P3/P4）：

1. G1 修复：/status 状态区能正常渲染（无 TypeError），账号 running 反映真值；
2. G2 修复：/status 显示的是 Python 真运行态与真队列（Node 不再抢答）；
3. G3 落地：kill_audit 放行有替代入口（MCP 工具或 /status 按钮）；
4. G4 落地：模拟器/MAA 启停有替代入口；
5. G5 落地：队列四动作在 /status 上可点；
6. G6 落地：模拟器列表与日志可见；
7. 入口切换：`main_web.pyw:348-352`（webbrowser.open）与 `:375`（托盘）指向 Node `/status`，确认无人直接开 19997 看板；
8. Python 常驻确认（MCP 挂在本进程，19997 必须活着；/status 若依赖透传也依赖它）；
9. 白名单固化：新增一条按 4.1-4.3 白名单断言「路由存在」的单测（防后续误删）。

## 7. 可复现命令与未实测项

```powershell
# 1) 本次分析时服务未起（两端口均 False）→ §2 为静态判定
Test-NetConnection -ComputerName 127.0.0.1 -Port 19999 -InformationLevel Quiet
Test-NetConnection -ComputerName 127.0.0.1 -Port 19997 -InformationLevel Quiet
# 2) /status 页面与其调用的端点（改为实测时用）
(Invoke-WebRequest 'http://127.0.0.1:19999/status' -UseBasicParsing).Content.Substring(0,400)
Invoke-RestMethod 'http://127.0.0.1:19999/api/status'   # 看有没有 queue 字段
# 3) PAGE 字段引用 vs 载荷键（静态对照）
Select-String -Path server/maaorch-web.mjs -Pattern "s\.(queue|parallel_max|schedule_mode|updated_at)"
Select-String -Path server/maaorch-web.mjs -Pattern 'function dashboardStatusPayload' -Context 0,10
# 4) 运行态字段无写入方
Select-String -Path . -Pattern '__running|__queued' -Include *.mjs,*.py   # 仅 mjs:454/507/538/571 命中
# 5) 白名单计数（应为 32）
Select-String -Path network/api_fastapi.py -Pattern '^\s*@app\.(get|post)\(' | Measure-Object
```

**未实测（必须补测）**：§2 的「状态区报错」是由代码推导的结论，需在 19999 起来后用浏览器打开 `/status` 复核；若届时 Node 版本已改（`dashboardStatusPayload` 增加了 queue 字段），本条结论作废，以实测为准。

## 8. 风险

1. **跳过闸门直接删前端 = 唯一人工入口是个报错页**，且 kill_audit 放行不可达，会破坏「杀/关先确认」的安全语义（`models/kill_audit.py` 的闸门仍在，但没人能放行）。
2. **同名不同真值**是本次最大混淆源：Node 原生与 Python 同路径并存（`/api/status`、`/api/queue`、`/api/account_status`、`/api/stats`），Node 版多为 `models/config.json` 快照或空壳，Python 版才是运行态。删前端时必须同步决定**每条同名路由由谁应答**，否则 agent 与人都看到假数据。
3. 白名单只保护「路由存在」，不保护「行为正确」；G2/G3/G4 的替代入口若只加 UI 不加 MCP 工具，Harness 侧仍无法驱动这些动作（与「粘合剂」定位不符）。
4. `/status` 的 MAS 两按钮走 `spawnSync('python', ...)`（mjs:157）——依赖本机 `python` 在 PATH 且工作目录正确；退役后这是仅存的写路径，建议加超时/失败可读提示（现已有 timeoutMs=300000）。