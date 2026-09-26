> ⚠️ **本文件的分档结论已被第二阶段更正，看结论请读 [`2026-09-26_api_trim_plan_p2.md`](2026-09-26_api_trim_plan_p2.md)**：
> ① §4.2 的「U1：Node 已原生实现 → 可删」判据作废（Node 原生不构成删除理由）；② §4.3 的 U2「靠门面透传 → 随页面退役」**不能整档判死**（前端是两边共用资产，Node 有 proxyTo 兜底）；
> ③ §6 情形 A 的「107 条可删」作废。修正后的可删量：现状 23 条/248 行；退役 Python UI 32 条/419 行；前端整体退役 108 条/2364 行。
> 本文件其余部分（139 条路由清单、消费方证据、MCP 白名单、风险清单）仍然有效。

# network/api_fastapi.py 收裁清单（2026-09-26 · 第一阶段：只出清单，不删任何路由）

> task-5 第一阶段交付物，产出人 api-curator。**本阶段不改 `network/api_fastapi.py` 一行**，等 Lead 决策后再进入第二阶段。
> 端口口径（Lead 已核实并纠正）：**MCP 在 Python 上游 19997**（`models/config.json → api_port=19997` → `main_web.pyw:343` uvicorn → `network/api_fastapi.py:3807 app.mount("/mcp", …)`）；19999 是 Node 门面（`server/maaorch-web.mjs:18`），它 **不代理 /mcp**。

## 0. 摘要（实测数字，非估算）

| 项 | 数 | 说明 |
|---|---|---|
| 文件总行数 | 3855 | 实测（任务卡写 3841，已过期） |
| 路由装饰器 | 140 | 含 `GET /{full_path:path}` SPA catch-all |
| 业务路由 | 139 | 与任务卡「139」一致 |
| **① 粘合剂必需（建议全留）** | **32** | 含 catch-all，1190 行（含共享 helper，为上界） |
| **② 仅为 Web UI 服务** | **108** | 2368 行（上界） |
| ②-U0 零仓内消费方 | 23 | 248 行 → 第一批退 |
| ②-U1 Node 已原生实现（Python 侧死副本） | 9 | 175 行 → 第二批退 |
| ②-U2 只有前端在用、靠 Node 透传 | 76 | 1945 行 → 随页面退役 |
| 只删 ② 后的预期行数 | ≈1300~1500 | 中间件/helper/静态服务/MCP 挂载保留 |

**结论**：139 条里真正属于粘合剂的只有 **31 条**；其余 **108 条**是旧 Python 看板遗产，其中 23 条连仓内调用方都没有、9 条已被 Node 门面原生实现成死副本。收裁的主要收益是**攻击面与可读性**，不是性能（真正拖慢启动的是 `create_app` 里 fastmcp 挂载，见 task-2/G6，已由 mcp_mount_enabled 开关处理）。

## 1. 决定「谁是消费方」的三条链路（实测）

1. **Harness → MCP（Python 19997，同一进程）**
   `network/mcp_server.py:29` 用 `http://127.0.0.1:{api_port}{path}` 自调用**本文件同一个 app** 的 REST；`_call_api` 是唯一入口。→ MCP 白名单 = 第一优先级（见 §2）。
2. **人 → Node 门面 19999 → 透传 19997**
   `ui/web/index.html` + `app.js`（`const API='/api'`）打 19999。门面 **原生实现** 17 个只读端点 + `/api/sse` + `/api/copilot/*` + `POST /api/mas/rerun|cleanup` + `/api/account/toggle`（mjs:793-812、814-844、862-885），其余 `/api/*` 走 `proxyTo()` 到 19997（mjs:369-441、886、891）。上游未起时按 `MAAORCH_ALLOW_UPSTREAM_SPAWN`（默认关）返回 501（mjs:414-430）。
   → **app.js 的调用里已有一半被 Node 截胡，根本到不了 Python**；这也是 U1 那 9 条的判定依据。
3. **MAA（游戏引擎）→ 本文件入站回调**
   `services/config_injector.py:356-367` 把 `/api/maa_remote/task`（MAA 每秒轮询 = 心跳）与 `/api/maa_remote/status` 写进 MAA 的 RemoteControl 配置；中间件 api_fastapi.py:216-217 对 `/api/maa_remote` + 本机来源免 token。**删了 MAA 就瞎。**

> 补充事实：**Python 上游已不常驻**（`tools/start_python_upstream.bat:2`、`docs/PLATFORM_NODE_MIGRATION.md:14`）。即 U2 那 76 条端点今天的常态就是 501/404；删它们对当前用户体验几乎无可观测影响，**对 MCP（直连 19997）零影响**。

## 2. MCP 白名单（硬约束 = mcp_server.py 里的 _call_api 路径）

实测 **25 个工具 → 18 个 distinct 路径 / 19 条路由**（MAA 只读 13 + MAA 写 5 + MAS 只读 5 + MAS 写 2）：

| MCP 工具 | 后端端点 | 行号 |
|---|---|---|
| link_map / inspect_account / processes | GET /api/inspect/link_map · /api/inspect/account/{name} · /api/inspect/processes | 3572 / 3675 / 3764 |
| status / dashboard / health | GET /api/status · /api/node/dashboard · /api/health | 298 / 339 / 1165 |
| queue / enqueue / dequeue / queue_control | GET /api/queue · POST /api/queue/enqueue · /dequeue · /pause · /resume | 1309 / 1906 / 1929 / 1956 / 1964 |
| account_status / events / stats_dashboard | GET /api/account_status · /api/events/list · /api/stats/dashboard | 1091 / 1200 / 638 |
| depot_plan / activity_stages / probe_status / probe_stage | GET /api/depot/plan · /api/activity/stages · /api/probe/status · POST /api/probe/stage | 944 / 1048 / 879 / 741 |
| smart_dispatch | POST /api/action/smart_selected | 3012 |
| mas_*（7 个工具） | **不经 REST**，进程内 services/mas_adapter → services/mas_l3 | mcp_server.py:204-260 |

⚠️ 阶段二动手前**重新跑一次** `_call_api` 提取（工具会增加），不要照抄本表；建议顺手加一条「路由白名单守卫」单测（见 §7 风险 1）。

## 3. ① 粘合剂必需 —— 32 条（建议全留）

| 行 | 方法 路径 | 为什么必须留 |
|---|---|---|
| 298 | `GET /api/status` | MCP status（Node 原生也有，但 MCP 直连 19997，必须留） |
| 339 | `GET /api/node/dashboard` | MCP dashboard |
| 638 | `GET /api/stats/dashboard` | MCP stats_dashboard |
| 722 | `GET /api/mas/status` | MAS 控制面：ui/web/auto_mas.js:455 + Node 原生 + 与 MCP mas_status 同源 |
| 727 | `POST /api/mas/rerun` | MAS 控制面：auto_mas.js:419 + Node 原生 POST 分支（写） |
| 732 | `POST /api/mas/cleanup` | MAS 控制面：auto_mas.js:419 + Node 原生 POST 分支（写） |
| 741 | `POST /api/probe/stage` | MCP probe_stage；services/depot_scheduler.py:195 |
| 798 | `POST /api/probe/anni_weekly` | 判据 probe/*（辅助探针写入口） |
| 844 | `GET /api/probe/results` | 判据 probe/*（探针结果查询） |
| 849 | `POST /api/probe/apply_plan` | 判据 probe/*（探针计划落地） |
| 879 | `GET /api/probe/status` | MCP probe_status |
| 944 | `GET /api/depot/plan` | MCP depot_plan；ui app.js:4347 |
| 1048 | `GET /api/activity/stages` | MCP activity_stages；ui app.js:4163 |
| 1067 | `POST /api/activity/stages` | activity 写路径（与 MCP 只读端点同族，写侧封闭在服务端） |
| 1091 | `GET /api/account_status` | MCP account_status |
| 1165 | `GET /api/health` | MCP health；services/runtime_health.py:263、launch_queue.py:781 语义依赖 |
| 1200 | `GET /api/events/list` | MCP events |
| 1262 | `GET /api/debug/state` | 调试必需（现场状态快照，人工排障唯一入口） |
| 1309 | `GET /api/queue` | MCP queue |
| 1906 | `POST /api/queue/enqueue` | MCP enqueue |
| 1929 | `POST /api/queue/dequeue` | MCP dequeue |
| 1949 | `POST /api/queue/clear` | 判据 queue*（清空队列；MCP 未暴露但同族，属安全闸门下游） |
| 1956 | `POST /api/queue/pause` | MCP queue_control |
| 1964 | `POST /api/queue/resume` | MCP queue_control |
| 2195 | `POST /api/maa_remote/task` | 【粘合剂入站】MAA 每秒轮询 GetTask；services/config_injector.py:366 注入该 URI |
| 2210 | `GET /api/maa_remote` | MAA 远程控制状态视图（api_fastapi.py:2210） |
| 2234 | `POST /api/maa_remote/status` | 【粘合剂入站】MAA 上报任务结果；services/config_injector.py:367 注入该 URI |
| 3012 | `POST /api/action/smart_selected` | MCP smart_dispatch |
| 3572 | `GET /api/inspect/link_map` | MCP link_map |
| 3675 | `GET /api/inspect/account/{name}` | MCP inspect_account |
| 3764 | `GET /api/inspect/processes` | MCP processes |
| 3824 | `GET /{full_path:path}` | SPA catch-all（必须最后注册，服务 ui/web） |

## 4. ② 仅为 Web UI 服务 —— 108 条（建议退役）

### 4.1 U0：零仓内消费方 —— 23 条 / 248 行（第一批，风险最低）

判定：`ui/web/*.js`、`ui/web/index.html`、`server/*.mjs`、`tools/*.py`、`services/*.py` 全部无命中；并二次排查了 `href` / `window.open` / `<img src>` / `EventSource` / 前缀拼接。

| 行 | 方法 路径 | 行数 | 备注 |
|---|---|---|---|
| 323 | `GET /api/node/info` | 16 | 多节点模式节点信息；仓内无调用方（与 node/register、node/heartbeat 同族） |
| 689 | `GET /api/stats/daily` | 5 | ⚠ 前端拼的是 /stats/day（app.js:942-947 option value=day/week/month/year）+ app.js:976 → 与后端 /daily 拼写不符，前后端本就不通 |
| 694 | `GET /api/stats/weekly` | 5 | ⚠ 前端拼 /stats/week（同上） |
| 699 | `GET /api/stats/monthly` | 5 | ⚠ 前端拼 /stats/month（同上） |
| 704 | `GET /api/stats/yearly` | 5 | ⚠ 前端拼 /stats/year（同上） |
| 714 | `GET /api/stats/periods` | 8 | 周期枚举；无调用方 |
| 957 | `GET /api/depot/probe` | 13 | 无调用方（与 depot/plan 不同端点） |
| 1233 | `POST /api/announcements/refresh` | 6 | 无调用方（UI 只用 GET /api/announcements） |
| 1239 | `GET /api/logs/collect_status` | 6 | 无调用方 |
| 1245 | `GET /api/logs/samples` | 8 | 无调用方 |
| 1253 | `GET /api/events_bus` | 9 | 无调用方（services/event_bus.py:7 的 SSE 桥由 api 层内部 drain，不走 HTTP） |
| 1771 | `GET /api/emulator/{v}` | 8 | 无调用方（UI 只用 POST /emulator/{idx}/{action} 与 /emulator/batch/*） |
| 1841 | `POST /api/sse` | 4 | 外部事件发布口；仓内无发布方（UI 只用 GET /api/sse，且 Node 已原生） |
| 1845 | `POST /api/node/register` | 10 | 多节点注册协议；仓内无节点侧实现 |
| 1855 | `POST /api/node/heartbeat` | 12 | 多节点心跳；仓内无节点侧实现 |
| 2057 | `POST /api/screenshots/{v}/delete` | 11 | 无调用方（截图视图只读） |
| 2165 | `GET /api/kill_audit/pending` | 9 | 无调用方（UI 只用 GET /api/kill_audit?limit=200） |
| 2433 | `POST /api/system/close_popups` | 6 | 人工运维入口（services/runner.py:2024 注释：只在自有流程内调）→ 先改文档再退 |
| 2462 | `POST /api/system/kill_maa` | 18 | ⚠ UI 的「杀 MAA」实际走 POST /api/kill_audit/decide；此处只剩人工直呼 → 与审计闸门重复，建议退 |
| 3156 | `GET /api/maa/download_status` | 5 | services/maa_download.py:17 注释称 UI 查询它，但 app.js 无命中 → 注释与代码不符，需先确认 |
| 3264 | `GET /api/maa/instances/{v}/config` | 53 | 无调用方 |
| 3447 | `POST /api/orch/hot_reload` | 20 | 人工热重载入口；无调用方 |
| 3535 | `POST /api/orch/download_update` | 6 | 无调用方（UI 只用 POST /api/maa/download_update） |

### 4.2 U1：Node 门面已原生实现（Python 侧是死副本） —— 9 条 / 175 行（第二批）

判定：`server/maaorch-web.mjs` 在该路径**先于 proxyTo 返回**，且该端点**不在 MCP 白名单**里。

| 行 | 方法 路径 | 行数 | 消费方（已由门面截胡） |
|---|---|---|---|
| 677 | `GET /api/stats` | 12 | app.js 15s 轮询；mjs:799 已原生 |
| 1415 | `GET /api/logs` | 13 | app.js:2477 fetch(API+/logs?lines=200)；mjs:801 已原生 |
| 1576 | `GET /api/accounts` | 36 | app.js 24 处；mjs:800 已原生；tools/deploy.py:193 打 19999 门面做健康检查 |
| 1739 | `GET /api/config` | 23 | app.js 3 处；mjs:797 已原生 |
| 1762 | `GET /api/settings/smart` | 4 | app.js 3 处；mjs:798 已原生 |
| 1766 | `GET /api/emulators` | 5 | app.js 2 处；mjs:802 已原生 |
| 1784 | `GET /api/stages` | 13 | app.js 7 处；mjs:795 已原生 |
| 1797 | `GET /api/sse` | 44 | app.js startSSE()；mjs:814 已原生 |
| 2766 | `GET /api/connect/status` | 25 | app.js 2 处；mjs:803 已原生 |

> ⚠️ **只删 GET**。同名 POST（`POST /api/config`、`POST /api/settings/smart`、`POST /api/stages`）Node **没有**原生实现，仍透传到 Python —— 它们留在 U2。

### 4.3 U2：只有前端在用、靠 Node 透传到 Python —— 76 条 / 1945 行（随页面退役）

| 行 | 方法 路径 | 行数 | 消费方（证据） |
|---|---|---|---|
| 501 | `GET /api/account/{v}/status` | 10 | app.js 账号详情 apiGet(/account/+idx+/status) |
| 511 | `GET /api/account/{v}/stats` | 9 | app.js 账号详情统计 |
| 520 | `GET /api/account/{v}/screenshot` | 31 | app.js 连接墙 <img src=API+/account/{idx}/screenshot> |
| 551 | `GET /api/screenshots/{v}` | 14 | app.js:1701 apiGet(/screenshots/+aid) |
| 565 | `GET /api/screenshots/file/{v}/{v}/{v}` | 7 | app.js:1712、1722 <img src=API+/screenshots/file/...> |
| 572 | `GET /api/screenshots/{v}/export/{v}` | 14 | app.js:1707 截图视图导出 <a href> 拼接 |
| 586 | `GET /api/export/logs` | 31 | app.js:586 window.open(API+/export/logs) |
| 617 | `GET /api/export/gantt` | 21 | app.js:617 window.open(API+/export/gantt) |
| 709 | `GET /api/stats/all` | 5 | app.js:976 动态 /stats/+period（period=all） |
| 892 | `POST /api/depot/scan` | 27 | app.js:4335 apiPost(/depot/scan) |
| 919 | `GET /api/depot/status` | 25 | app.js:4163 apiGet(/depot/status) |
| 970 | `GET /api/depot/config` | 9 | app.js:4163 apiGet(/depot/config) |
| 979 | `POST /api/depot/config` | 46 | app.js:4476 apiPost(/depot/config) |
| 1025 | `POST /api/penguin/refresh` | 14 | app.js 材料页刷新按钮 |
| 1039 | `GET /api/penguin/status` | 9 | app.js:4163 apiGet(/penguin/status) |
| 1053 | `POST /api/activity/refresh` | 14 | app.js 活动页刷新按钮 |
| 1171 | `GET /api/emulators/diagnose` | 29 | app.js 环境检测页 apiGet(/emulators/diagnose) |
| 1209 | `GET /api/events/calendar` | 9 | app.js 活动日历 apiGet(/events/calendar?year=) |
| 1218 | `GET /api/events/analyze` | 9 | app.js 活动分析 apiGet(/events/analyze?year=) |
| 1227 | `GET /api/announcements` | 6 | app.js apiGet(/announcements)；提供方 services/announce_collector.py:7 |
| 1383 | `GET /api/shots/list` | 19 | app.js 截图视图 apiGet(/shots/list) |
| 1402 | `GET /api/shots/file` | 13 | app.js:4535-4536 <img src=API+/shots/file?path=...> |
| 1428 | `GET /api/maa/log` | 72 | app.js:2482 fetch(API+/maa/log?aid=...) |
| 1500 | `GET /api/maa/logs_history` | 25 | app.js MAA 日志历史 apiGet(/maa/logs_history?aid=) |
| 1525 | `GET /api/stage_checks` | 51 | app.js apiGet(/stage_checks?aid=) |
| 1612 | `GET /api/accounts/export` | 22 | app.js:1612 <a href=/api/accounts/export download> |
| 1634 | `POST /api/accounts/csv_import` | 58 | app.js 账号 CSV 导入 |
| 1692 | `POST /api/accounts/batch_save` | 43 | app.js 账号批量保存 |
| 1735 | `GET /api/oplog` | 4 | app.js 操作记录弹层 apiGet(/oplog) |
| 1779 | `GET /api/account/{v}/config` | 5 | app.js 账号配置读取 |
| 1867 | `POST /api/account/{v}/launch` | 9 | app.js 账号启动 |
| 1876 | `POST /api/account/{v}/stop` | 8 | app.js 账号停止 |
| 1884 | `POST /api/config/sync` | 22 | app.js 配置下发/同步 |
| 1972 | `POST /api/account` | 19 | app.js 新增账号（Node 无原生；tools/deploy.py 不走此路径） |
| 1991 | `POST /api/account/{v}/edit` | 15 | app.js 编辑账号 |
| 2006 | `POST /api/account/{v}/delete` | 9 | app.js:2523、3806 apiPost(/account/idx/delete) |
| 2015 | `POST /api/account/{v}/config` | 9 | app.js 保存账号配置 |
| 2024 | `GET /api/account/{v}/fight_config` | 15 | app.js 作战配置读取 |
| 2039 | `POST /api/account/{v}/fight_config` | 18 | app.js 作战配置保存 |
| 2068 | `POST /api/config` | 18 | app.js 设置页保存（Node 只原生实现 GET /api/config，POST 仍透传） |
| 2086 | `POST /api/settings/smart` | 8 | app.js 智能设置保存（Node 只原生实现 GET） |
| 2094 | `POST /api/ai/analyze` | 27 | app.js AI 分析按钮 |
| 2121 | `POST /api/stages` | 7 | app.js 关卡库保存（Node 只原生实现 GET /api/stages） |
| 2128 | `POST /api/stages/apply` | 21 | app.js 应用关卡到账号 |
| 2149 | `POST /api/action/stop_all` | 8 | app.js 全部停止 |
| 2157 | `GET /api/kill_audit` | 8 | app.js:2165 apiGet(/kill_audit?limit=200) |
| 2174 | `POST /api/kill_audit/decide` | 21 | app.js 守护审计决策（安全闸门：kill_maa 只能经此路） |
| 2318 | `POST /api/emulator/batch/{v}` | 62 | app.js 模拟器批量操作 |
| 2380 | `POST /api/emulator/{v}/{v}` | 53 | app.js:2070 apiPost(/emulator/idx/action) |
| 2439 | `POST /api/system/open_folder` | 23 | app.js 打开目录按钮 |
| 2480 | `POST /api/system/restart` | 128 | app.js 重启应用按钮 |
| 2608 | `POST /api/action/smart_all` | 33 | app.js 智能调度（全部） |
| 2641 | `POST /api/action/smart_maintenance` | 23 | app.js 智能基建 |
| 2664 | `POST /api/action/smart_fight` | 23 | app.js 智能作战 |
| 2687 | `POST /api/action/connect_running_emus` | 79 | app.js 接管在跑模拟器 |
| 2791 | `POST /api/connect/launch` | 34 | app.js 连接页启动 |
| 2825 | `POST /api/connect/{v}/stop` | 6 | app.js 连接页单停 |
| 2831 | `POST /api/connect/{v}/restart` | 22 | app.js 连接页重启 |
| 2853 | `POST /api/connect/batch_stop` | 19 | app.js 连接页批量停 |
| 2872 | `POST /api/connect/batch_emu_stop` | 30 | app.js 连接页批量关模拟器 |
| 2902 | `GET /api/connect/{v}/log` | 21 | app.js 连接页日志 |
| 2923 | `GET /api/connect/{v}/screenshot` | 41 | app.js 连接墙截图（前缀 /connect/ 拼接） |
| 2964 | `POST /api/action/smart_login` | 23 | app.js 智能登录 |
| 2987 | `POST /api/action/smart_annihilation` | 25 | app.js 智能剿灭 |
| 3081 | `POST /api/instance/rebuild` | 6 | app.js MAA 实例重建 |
| 3087 | `POST /api/maa/check_update` | 48 | app.js MAA 检查更新 |
| 3135 | `POST /api/maa/download_update` | 21 | app.js apiPost(/maa/download_update) |
| 3161 | `GET /api/maa/status` | 38 | app.js MAA 状态页 apiGet(/maa/status) |
| 3199 | `POST /api/maa/launch_source` | 19 | app.js 源码启动 |
| 3218 | `POST /api/maa/check_source` | 14 | app.js 校验源码 |
| 3232 | `GET /api/maa/instances` | 32 | app.js MAA 实例列表 |
| 3317 | `POST /api/orch/check_update` | 130 | app.js 项目自更新检查 |
| 3467 | `POST /api/orch/update` | 55 | app.js 项目自更新 |
| 3522 | `GET /api/orch/update_result` | 13 | app.js 更新结果查询 |
| 3541 | `POST /api/config/export` | 10 | app.js 导出配置 |
| 3551 | `POST /api/config/import` | 21 | app.js 导入配置 |

## 5. 依赖图

```
                        ┌──────────────────────── Harness (DSH Agent) ───────────────────────┐
                        │  MCP 客户端 → http://127.0.0.1:19997/mcp  (streamable-http)        │
                        └───────────────────────────────┬───────────────────────────────────┘
                                                        │ 挂载于同一 app
   MAA(游戏引擎) ──每秒 POST /api/maa_remote/task──────┐  ▼
        └────── POST /api/maa_remote/status ──────────►│ network/api_fastapi.py  (uvicorn :19997)
                                                       │   ├─ ① 31 条：MCP 18 路径 + MAA 回调 3 + MAS 3 + 调试/探针 7
   人 ──► Node 门面 :19999 (server/maaorch-web.mjs)     │   └─ ② 108 条：旧 Web 看板遗产
          ├─ 原生 17 GET + /api/sse + /api/copilot/*    │
          ├─ 原生 POST /api/mas/rerun|cleanup, /api/account/toggle
          └─ 其余 /api/* ── proxyTo() ─────────────────┘
   tools/deploy.py:193 ── GET :19999/api/accounts（门面原生，不碰 Python）

   services/mas_adapter.py ─(进程内)→ services/mas_l3.py ─(SSH/HTTP)→ AUTO-MAS 远端主机
   services/config_injector.py:366-367 ─(写 MAA 配置)→ MAA 反向调用上面两条 /api/maa_remote/*
```

**多消费方端点（必须最后退）**：`GET /api/accounts`（app.js 24 处 + Node 原生 + tools/deploy.py:193）、`GET /api/stages`（app.js 7 处 + Node 原生）、`GET /api/connect/status`（app.js 2 处 + Node 原生）、`POST /api/config`/`POST /api/settings/smart`/`POST /api/stages`（app.js 写路径 + 依赖 Node GET 同名的页面）、`GET /api/health`（MCP + 首页状态条 + Node 原生，已在 ①）。

## 6. 建议退役顺序（四批，每批独立可回滚）

| 批次 | 内容 | 条数/行数 | 前置条件 | 理由 |
|---|---|---|---|---|
| **P1** | U0 全部（§4.1） | 23 / 248 | 先把 close_popups / kill_maa / hot_reload / node/register|heartbeat 写进运维文档或 CLI；确认 maa_download.py:17 注释 | 零仓内消费方；其中 5 条是人工入口，属「先补文档再删」 |
| **P2** | U1 全部（§4.2，**只删 GET**） | 9 / 175 | 确认 Node 门面常驻（`tools/maaorch_platform.bat:16`）+ MCP 白名单复核 | Python 副本对 UI 已是死代码；MCP 不走这些 |
| **P3** | U2 中「账号/连接/模拟器/系统/动作」类：`/api/account*`(10)、`/api/connect*`(8)、`/api/emulator/*`(2)、`/api/system/*`(2)、`/api/action/*`(8)、`/api/kill_audit/*`、`/api/instance/rebuild`、`/api/config/sync` | 34 条 | 前端对应页面下线或改打 19999 原生；确认 kill_audit 闸门不依赖这些路由 | 都是「会改机器」的写端点，留着就是裸攻击面 |
| **P4** | U2 余下只读/导入导出：stats/export/shots/screenshots/maa 日志/update/config import-export/depot/penguin/events/announcements | 42 条 | 逐页确认；多消费方端点（accounts/stages/connect/status）**留到 P4 末尾** | 多消费方，退早了会打断 deploy.py 健康检查与门面 GET |

**绝对不动**：① 31 条 + `GET /{full_path:path}` catch-all + 中间件（鉴权/限流/截图缓存/共享 helper）。

## 7. 风险清单

1. **MCP 与 UI 共用同一 app**：删错一条，MCP 工具不会崩，只会静默返回 `{"error":"HTTP 404"}`（`_call_api` 把 HTTPError 包成 dict，mcp_server.py:45-50）——**沉默失败**。缓解：阶段二加一条单测，按 `_call_api` 白名单逐个断言路由存在。
2. **GET/POST 同名陷阱**：Node 只原生实现 GET（config/settings/smart/stages/…），POST 仍透传。把 GET 和 POST 一起删会打断设置页保存。
3. **拼接式调用扫不到**：`app.js` 用 `<img src>`（screenshots/file、shots/file、account/{idx}/screenshot）、`window.open`（export/logs、export/gantt）、`<a href download>`（accounts/export）、前缀拼接（/connect/、/emulator/、/screenshots/）调用端点，纯字符串扫描必漏。缓解：删前用 `server/maaorch-web.mjs` 的 `/api/_proxy_stats`（mjs:809-812 命中统计）或抓一周 `logs/debug.log` 反证「确实没人调」。
4. **人工运维入口 ≠ 死代码**：close_popups（runner.py:2024 提到）、kill_maa、orch/hot_reload、node/register|heartbeat 是故障时的手。零消费方只说明「没有自动调用方」，需先落到文档/CLI 再删。
5. **前端与后端本就拼写不符**：统计页拼 `/stats/day|week|month|year`（app.js:942-947、976），后端只有 `/stats/daily|weekly|monthly|yearly` → 这两侧现在就是 404。退役时**不要**为「兼容前端」保留 daily/weekly/monthly/yearly。
6. **上游不常驻**：U2 删除后，若有人重新常驻 19997 并依赖这些 UI 功能，会立刻 404。缓解：P3/P4 与「19997 是否继续常驻」的决策绑定；MCP 需要 19997 常驻，所以 19997 不会消失，只是「只服务 MCP + 运维面板」。
7. **单函数 3637 行仍是结构问题**：删路由只减行数，不改 `create_app` 单函数结构。建议第二阶段同批把路由按域拆成 `APIRouter`（inspect/queue/mas/maa_remote 四个模块足以承载 ① 的 31 条），否则下一轮还会长回来。

## 8. 方法与可复现证据

- 路由提取：`python -c` 正则扫 `^\s*@app\.(get|post|...)\(`；139 条业务路由 + 1 条 catch-all。
- MCP 白名单：正则 `_call_api\(mw,\s*f?["']([^"']+)` 扫 `network/mcp_server.py` → 18 条路径（`/api/queue/{action}` 展开为 pause+resume 两条路由）。
- UI 消费方：`ui/web/app.js`（86 个调用点）、`ui/web/auto_mas.js`（3 个）、`ui/web/index.html:34`，手工核对 `href/window.open/img src/EventSource` 与模板字符串拼接。
- 门面原生实现：`server/maaorch-web.mjs:793-812`（GET）、`814-844`（SSE）、`862-885`（POST 分支）、`369-441`（proxyTo）。
- 服务端内部消费方：`services/config_injector.py:356-367`、`services/depot_scheduler.py:195`、`services/announce_collector.py:7`、`services/maa_download.py:13-17`、`services/runner.py:2024`（后三条为注释引用，已在 U0 备注标注）。
- tools 消费方：`tools/deploy.py:193`（打 19999）。其余 tools 里的 `/api/queue/get`、`/api/history/search` 等是 **AUTO-MAS 远端主机 API**，与 MAAOrch 无关，**不构成保留理由**。

## 9. 请 Lead 决策的三点

1. 是否批准 P1+P2（共 32 条 / 423 行）作为第二阶段第一刀？这两批不碰任何 MCP/UI 活路径。
2. 19997 的定位是否定为「MCP + 运维面板」？若是，U2 的 76 条可以整体判死；若仍想保留 Python 看板作为兜底，U2 需按页面分批。
3. 第二阶段是否同批把 `create_app` 拆成 APIRouter（风险 7）？建议是，否则收裁成果下一轮会被重新长回来。
