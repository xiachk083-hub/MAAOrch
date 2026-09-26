# task-5 第二阶段：逐路由消费方矩阵 + 分档修正（承重错误更正）

> 产出人 api-curator，2026-09-26。**本阶段仍不改 `network/api_fastapi.py`**（只写 docs/audits）。
> 本文**取代** `2026-09-26_api_trim_plan.md` 的分档结论（第一阶段文件保留作过程记录，其 §4.2 U1 与 §6 情形A 的判死口径已作废）。

## 0. 三处更正（Lead 实测）

| # | 第一阶段写法 | 更正后 | 后果 |
|---|---|---|---|
| 1 | 「Node 门面已原生实现（U1）→ Python 侧可删」 | **Node 原生不构成删除理由**：MCP 用 `_call_api` 直连 Python 19997，**M∩N = 5 条**（/api/status、/api/node/dashboard、/api/account_status、/api/health、/api/queue）删了 MCP 直接坏 | 删除判据改为：**零消费方** 或 （**Node 原生 且 ¬M 且 ¬S 且 ¬T 且 前端不再从 Python 提供**） |
| 2 | 「U2 仅前端在用、靠门面透传 → 可整档判死（76 条）」 | **前端是两边共用资产**：Node 的 `/` 就是 `readFileSync('ui/web/index.html')`（mjs:899），静态资源 Node 直接读磁盘（mjs:918-943）；Node 对未实现路由有 `proxyTo` 兜底（POST mjs:886、GET mjs:891，注释「迁移期零 404」） | U2 那 76 条**仍在链路上**，只能**按前端功能逐项退役**，不能整档删 |
| 3 | 「Python UI 退役 = 删前端相关路由」 | Python UI 退役只意味着**不再需要 Python 的 catch-all 静态服务**（`GET /{full_path:path}`，32 行，Node 已自服务）；前端本身照样由 Node 提供、照样调用那 76 条 | 情形划分改为三档（§4） |

## 1. 矩阵的五列定义

| 列 | 含义 | 判定依据 |
|---|---|---|
| **M** | MCP 工具后端（`_call_api` 白名单） | `network/mcp_server.py` 实际调用路径：18 个 distinct 路径 → 20 条路由（`/api/queue/{action}` 展开 pause+resume；`/api/activity/stages` 的 GET/POST 同路径） |
| **P** | 前端 `ui/web`（app.js / auto_mas.js / index.html）调用 | 含 `apiGet/apiPost`、`fetch`、`window.open`、`<img src>`、`<a href download>`、模板拼接与 `'/stats/'+period` 动态路径；**经 19999 被 Node 截胡也算在用** |
| **N原生** | Node 门面原生应答（不 proxyTo） | `server/maaorch-web.mjs`：779（health）、793-808（13 条 GET）、814（SSE）、862-872（POST mas/rerun|cleanup）；copilot 四路不在本文件 |
| **N透传** | Node 未原生实现 → `proxyTo` 到 Python 19997 | mjs:886（POST 兜底）、mjs:891（GET 兜底）、mjs:943（静态回落） |
| **T** | tools/、server 非原生、外部系统 | `tools/deploy.py:193`；外部 MAA 入站 `/api/maa_remote/*`（`services/config_injector.py:366-367` 注入，每秒轮询） |
| **S** | 保留判据外延（补充列，非 MCP） | 任务判据钉死的 `probe/*`、`queue*`、`services/depot_scheduler.py:195`；`debug/state`（调试必需）；MAS 控制面 `/api/mas/*` |

## 2. 逐路由消费方矩阵（139 条）

### 2.1 A 组：M 列 = MCP 后端（20 条 / 881 行）——**永不删**

| 行 | 方法 路径 | 行数 | M | P | N原生 | N透传 | T | 备注 |
|---|---|---|---|---|---|---|---|---|
| 298 | `GET /api/status` | 25 | ✓ | · | ✓ | · | · | MCP status |
| 339 | `GET /api/node/dashboard` | 162 | ✓ | · | ✓ | · | · | MCP dashboard |
| 638 | `GET /api/stats/dashboard` | 39 | ✓ | · | · | ✓ | · | MCP stats_dashboard |
| 741 | `POST /api/probe/stage` | 57 | ✓ | · | · | ✓ | · | MCP probe_stage |
| 879 | `GET /api/probe/status` | 13 | ✓ | · | · | ✓ | · | MCP probe_status |
| 944 | `GET /api/depot/plan` | 13 | ✓ | · | · | ✓ | · | MCP depot_plan |
| 1048 | `GET /api/activity/stages` | 5 | ✓ | · | · | ✓ | · | MCP activity_stages |
| 1067 | `POST /api/activity/stages` | 24 | ✓ | · | · | ✓ | · | MCP activity_stages 的写侧（同路径） |
| 1091 | `GET /api/account_status` | 74 | ✓ | · | ✓ | · | · | MCP account_status |
| 1165 | `GET /api/health` | 6 | ✓ | · | ✓ | · | · | MCP health |
| 1200 | `GET /api/events/list` | 9 | ✓ | · | · | ✓ | · | MCP events |
| 1309 | `GET /api/queue` | 74 | ✓ | · | ✓ | · | · | MCP queue |
| 1906 | `POST /api/queue/enqueue` | 23 | ✓ | · | · | ✓ | · | MCP enqueue |
| 1929 | `POST /api/queue/dequeue` | 20 | ✓ | · | · | ✓ | · | MCP dequeue |
| 1956 | `POST /api/queue/pause` | 8 | ✓ | · | · | ✓ | · | MCP queue_control |
| 1964 | `POST /api/queue/resume` | 8 | ✓ | · | · | ✓ | · | MCP queue_control |
| 3012 | `POST /api/action/smart_selected` | 69 | ✓ | · | · | ✓ | · | MCP smart_dispatch |
| 3572 | `GET /api/inspect/link_map` | 103 | ✓ | · | · | ✓ | · | MCP link_map |
| 3675 | `GET /api/inspect/account/{name}` | 89 | ✓ | · | · | ✓ | · | MCP inspect_account |
| 3764 | `GET /api/inspect/processes` | 60 | ✓ | · | · | ✓ | · | MCP processes |

### 2.2 B 组：S 列 = 保留判据外延（8 条 / 154 行）——**永不删**

| 行 | 方法 路径 | 行数 | M | P | N原生 | N透传 | T | 备注 |
|---|---|---|---|---|---|---|---|---|
| 722 | `GET /api/mas/status` | 5 | · | · | ✓ | · | · | MAS 控制面（auto_mas.js:455 + Node 原生） |
| 727 | `POST /api/mas/rerun` | 5 | · | · | ✓ | · | · | MAS 控制面写（auto_mas.js:419 + Node 原生 POST） |
| 732 | `POST /api/mas/cleanup` | 9 | · | · | ✓ | · | · | MAS 控制面写（auto_mas.js:419 + Node 原生 POST） |
| 798 | `POST /api/probe/anni_weekly` | 46 | · | · | · | ✓ | · | 判据 probe/* |
| 844 | `GET /api/probe/results` | 5 | · | · | · | ✓ | · | 判据 probe/* |
| 849 | `POST /api/probe/apply_plan` | 30 | · | · | · | ✓ | · | 判据 probe/* |
| 1262 | `GET /api/debug/state` | 47 | · | · | · | ✓ | · | 调试必需（现场状态快照） |
| 1949 | `POST /api/queue/clear` | 7 | · | · | · | ✓ | · | 判据 queue* |

### 2.3 C 组：T 列 = 外部/工具/入站回调（4 条 / 159 行）——**永不删**

| 行 | 方法 路径 | 行数 | M | P | N原生 | N透传 | T | 备注 |
|---|---|---|---|---|---|---|---|---|
| 1576 | `GET /api/accounts` | 36 | · | ✓ | ✓ | · | ✓ | tools/deploy.py:134,193 → 打 19999 门面（Node 原生应答） |
| 2195 | `POST /api/maa_remote/task` | 15 | · | · | · | ✓ | ✓ | 外部 MAA 每秒轮询（config_injector.py:366 注入 URI） |
| 2210 | `GET /api/maa_remote` | 24 | · | · | · | ✓ | ✓ | 外部 MAA RemoteControl 状态视图 |
| 2234 | `POST /api/maa_remote/status` | 84 | · | · | · | ✓ | ✓ | 外部 MAA 上报结果（config_injector.py:367 注入 URI） |

> `GET /api/accounts` 的 T 消费方（`tools/deploy.py`）打的是 **19999 门面**，Node 原生应答，不落 Python；因此在情形 A 下可随 C2 一并退役（见 §4 情形 A'）。保守做法是留到 P4 末尾。

### 2.4 C1 组：零消费方（23 条 / 248 行）——**唯一在现状下就能删的一档**

| 行 | 方法 路径 | 行数 | M | P | N原生 | N透传 | T | 备注 |
|---|---|---|---|---|---|---|---|---|
| 323 | `GET /api/node/info` | 16 | · | · | · | ✓ | · | 多节点节点信息；仓内无调用方 |
| 689 | `GET /api/stats/daily` | 5 | · | · | · | ✓ | · | 前端拼的是 /stats/day（app.js:942-947,976）→ 与 /daily 不符 |
| 694 | `GET /api/stats/weekly` | 5 | · | · | · | ✓ | · | 前端拼 /stats/week |
| 699 | `GET /api/stats/monthly` | 5 | · | · | · | ✓ | · | 前端拼 /stats/month |
| 704 | `GET /api/stats/yearly` | 5 | · | · | · | ✓ | · | 前端拼 /stats/year |
| 714 | `GET /api/stats/periods` | 8 | · | · | · | ✓ | · | 周期枚举；无调用方 |
| 957 | `GET /api/depot/probe` | 13 | · | · | · | ✓ | · | 无调用方 |
| 1233 | `POST /api/announcements/refresh` | 6 | · | · | · | ✓ | · | 无调用方（UI 只用 GET） |
| 1239 | `GET /api/logs/collect_status` | 6 | · | · | · | ✓ | · | 无调用方 |
| 1245 | `GET /api/logs/samples` | 8 | · | · | · | ✓ | · | 无调用方 |
| 1253 | `GET /api/events_bus` | 9 | · | · | · | ✓ | · | 无调用方（bus 由 api 层内部 drain，不走 HTTP） |
| 1771 | `GET /api/emulator/{idx}` | 8 | · | · | · | ✓ | · | 无调用方（UI 只用 POST /emulator/{idx}/{action}） |
| 1841 | `POST /api/sse` | 4 | · | · | · | ✓ | · | 外部发布口；仓内无发布方 |
| 1845 | `POST /api/node/register` | 10 | · | · | · | ✓ | · | 多节点注册协议；无节点侧实现 |
| 1855 | `POST /api/node/heartbeat` | 12 | · | · | · | ✓ | · | 多节点心跳；无节点侧实现 |
| 2057 | `POST /api/screenshots/{aid}/delete` | 11 | · | · | · | ✓ | · | 无调用方 |
| 2165 | `GET /api/kill_audit/pending` | 9 | · | · | · | ✓ | · | ⚠ 安全闸门查询侧（见 §6），虽零调用方但不建议直删 |
| 2433 | `POST /api/system/close_popups` | 6 | · | · | · | ✓ | · | 人工运维入口（runner.py:2024 注释）先落文档 |
| 2462 | `POST /api/system/kill_maa` | 18 | · | · | · | ✓ | · | ⚠ UI 的杀 MAA 走 kill_audit/decide；此路与闸门重复 |
| 3156 | `GET /api/maa/download_status` | 5 | · | · | · | ✓ | · | maa_download.py:17 注释称 UI 用，app.js 无命中 |
| 3264 | `GET /api/maa/instances/{n}/config` | 53 | · | · | · | ✓ | · | 无调用方 |
| 3447 | `POST /api/orch/hot_reload` | 20 | · | · | · | ✓ | · | 人工热重载入口 |
| 3535 | `POST /api/orch/download_update` | 6 | · | · | · | ✓ | · | 无调用方（UI 只用 /maa/download_update） |

### 2.5 C2 组：Node 原生 ∧ ¬M ∧ ¬S ∧ ¬T（8 条 / 139 行）——**仅当「前端不再从 Python 提供」时可删**

| 行 | 方法 路径 | 行数 | M | P | N原生 | N透传 | T | 备注 |
|---|---|---|---|---|---|---|---|---|
| 677 | `GET /api/stats` | 12 | · | ✓ | ✓ | · | · | Node 原生 mjs:799；前端 19997 直开时仍用 |
| 1415 | `GET /api/logs` | 13 | · | ✓ | ✓ | · | · | Node 原生 mjs:801；app.js:2477 |
| 1739 | `GET /api/config` | 23 | · | ✓ | ✓ | · | · | Node 原生 mjs:797（GET）；POST 不走原生 |
| 1762 | `GET /api/settings/smart` | 4 | · | ✓ | ✓ | · | · | Node 原生 mjs:798（GET）；POST 不走原生 |
| 1766 | `GET /api/emulators` | 5 | · | ✓ | ✓ | · | · | Node 原生 mjs:802 |
| 1784 | `GET /api/stages` | 13 | · | ✓ | ✓ | · | · | Node 原生 mjs:795（GET）；POST 不走原生 |
| 1797 | `GET /api/sse` | 44 | · | ✓ | ✓ | · | · | Node 原生 mjs:814；app.js startSSE() |
| 2766 | `GET /api/connect/status` | 25 | · | ✓ | ✓ | · | · | Node 原生 mjs:803 |

### 2.6 C3 组：前端功能在用、Node 无原生 → 透传到 Python（76 条 / 1945 行）——**按功能逐项退役，不能整档删**

| 行 | 方法 路径 | 行数 | M | P | N原生 | N透传 | T | 备注 |
|---|---|---|---|---|---|---|---|---|
| 501 | `GET /api/account/{idx}/status` | 10 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 511 | `GET /api/account/{idx}/stats` | 9 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 520 | `GET /api/account/{idx}/screenshot` | 31 | · | ✓ | · | ✓ | · | app.js 连接墙 <img src> |
| 551 | `GET /api/screenshots/{aid}` | 14 | · | ✓ | · | ✓ | · | app.js:1701 apiGet(/screenshots/+aid) |
| 565 | `GET /api/screenshots/file/{aid}/{run_dir}/{fname:path}` | 7 | · | ✓ | · | ✓ | · | app.js:1712,1722 <img src> |
| 572 | `GET /api/screenshots/{aid}/export/{run_ts}` | 14 | · | ✓ | · | ✓ | · | 前端「统计/截图页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 586 | `GET /api/export/logs` | 31 | · | ✓ | · | ✓ | · | app.js:586 window.open(API+/export/logs) |
| 617 | `GET /api/export/gantt` | 21 | · | ✓ | · | ✓ | · | app.js:617 window.open(API+/export/gantt) |
| 709 | `GET /api/stats/all` | 5 | · | ✓ | · | ✓ | · | app.js:976 动态 /stats/+period（period=all） |
| 892 | `POST /api/depot/scan` | 27 | · | ✓ | · | ✓ | · | 前端「材料/活动页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 919 | `GET /api/depot/status` | 25 | · | ✓ | · | ✓ | · | 前端「材料/活动页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 970 | `GET /api/depot/config` | 9 | · | ✓ | · | ✓ | · | 前端「材料/活动页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 979 | `POST /api/depot/config` | 46 | · | ✓ | · | ✓ | · | 前端「材料/活动页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1025 | `POST /api/penguin/refresh` | 14 | · | ✓ | · | ✓ | · | 前端「材料/活动页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1039 | `GET /api/penguin/status` | 9 | · | ✓ | · | ✓ | · | 前端「材料/活动页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1053 | `POST /api/activity/refresh` | 14 | · | ✓ | · | ✓ | · | 前端「材料/活动页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1171 | `GET /api/emulators/diagnose` | 29 | · | ✓ | · | ✓ | · | 前端「连接/模拟器页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1209 | `GET /api/events/calendar` | 9 | · | ✓ | · | ✓ | · | 前端「材料/活动页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1218 | `GET /api/events/analyze` | 9 | · | ✓ | · | ✓ | · | 前端「材料/活动页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1227 | `GET /api/announcements` | 6 | · | ✓ | · | ✓ | · | 前端「材料/活动页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1383 | `GET /api/shots/list` | 19 | · | ✓ | · | ✓ | · | 前端「统计/截图页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1402 | `GET /api/shots/file` | 13 | · | ✓ | · | ✓ | · | app.js:4535-4536 <img src> |
| 1428 | `GET /api/maa/log` | 72 | · | ✓ | · | ✓ | · | app.js:2482 fetch(API+/maa/log) |
| 1500 | `GET /api/maa/logs_history` | 25 | · | ✓ | · | ✓ | · | 前端「MAA 日志/实例/更新」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1525 | `GET /api/stage_checks` | 51 | · | ✓ | · | ✓ | · | 前端「MAA 日志/实例/更新」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1612 | `GET /api/accounts/export` | 22 | · | ✓ | · | ✓ | · | app.js:1612 <a href download> |
| 1634 | `POST /api/accounts/csv_import` | 58 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1692 | `POST /api/accounts/batch_save` | 43 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1735 | `GET /api/oplog` | 4 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1779 | `GET /api/account/{idx}/config` | 5 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1867 | `POST /api/account/{idx}/launch` | 9 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1876 | `POST /api/account/{idx}/stop` | 8 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1884 | `POST /api/config/sync` | 22 | · | ✓ | · | ✓ | · | 前端「配置导入导出/AI」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1972 | `POST /api/account` | 19 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 1991 | `POST /api/account/{idx}/edit` | 15 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2006 | `POST /api/account/{idx}/delete` | 9 | · | ✓ | · | ✓ | · | app.js:2523,3806 |
| 2015 | `POST /api/account/{idx}/config` | 9 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2024 | `GET /api/account/{idx}/fight_config` | 15 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2039 | `POST /api/account/{idx}/fight_config` | 18 | · | ✓ | · | ✓ | · | 前端「账号管理页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2068 | `POST /api/config` | 18 | · | ✓ | · | ✓ | · | Node 只原生实现 GET；POST 必透传 |
| 2086 | `POST /api/settings/smart` | 8 | · | ✓ | · | ✓ | · | Node 只原生实现 GET；POST 必透传 |
| 2094 | `POST /api/ai/analyze` | 27 | · | ✓ | · | ✓ | · | 前端「配置导入导出/AI」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2121 | `POST /api/stages` | 7 | · | ✓ | · | ✓ | · | Node 只原生实现 GET；POST 必透传 |
| 2128 | `POST /api/stages/apply` | 21 | · | ✓ | · | ✓ | · | 前端「其他」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2149 | `POST /api/action/stop_all` | 8 | · | ✓ | · | ✓ | · | 前端「智能调度动作」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2157 | `GET /api/kill_audit` | 8 | · | ✓ | · | ✓ | · | app.js:2165；⚠ 闸门读侧 |
| 2174 | `POST /api/kill_audit/decide` | 21 | · | ✓ | · | ✓ | · | app.js；⚠ 闸门唯一放行口（见 §6） |
| 2318 | `POST /api/emulator/batch/{action}` | 62 | · | ✓ | · | ✓ | · | app.js 模拟器批量操作 |
| 2380 | `POST /api/emulator/{idx}/{action}` | 53 | · | ✓ | · | ✓ | · | app.js:2070 apiPost(/emulator/idx/action) |
| 2439 | `POST /api/system/open_folder` | 23 | · | ✓ | · | ✓ | · | 前端「系统运维按钮」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2480 | `POST /api/system/restart` | 128 | · | ✓ | · | ✓ | · | app.js 重启应用按钮 |
| 2608 | `POST /api/action/smart_all` | 33 | · | ✓ | · | ✓ | · | 前端「智能调度动作」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2641 | `POST /api/action/smart_maintenance` | 23 | · | ✓ | · | ✓ | · | 前端「智能调度动作」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2664 | `POST /api/action/smart_fight` | 23 | · | ✓ | · | ✓ | · | 前端「智能调度动作」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2687 | `POST /api/action/connect_running_emus` | 79 | · | ✓ | · | ✓ | · | 前端「智能调度动作」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2791 | `POST /api/connect/launch` | 34 | · | ✓ | · | ✓ | · | 前端「连接/模拟器页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2825 | `POST /api/connect/{aid}/stop` | 6 | · | ✓ | · | ✓ | · | 前端「连接/模拟器页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2831 | `POST /api/connect/{aid}/restart` | 22 | · | ✓ | · | ✓ | · | 前端「连接/模拟器页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2853 | `POST /api/connect/batch_stop` | 19 | · | ✓ | · | ✓ | · | 前端「连接/模拟器页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2872 | `POST /api/connect/batch_emu_stop` | 30 | · | ✓ | · | ✓ | · | 前端「连接/模拟器页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2902 | `GET /api/connect/{aid}/log` | 21 | · | ✓ | · | ✓ | · | 前端「连接/模拟器页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2923 | `GET /api/connect/{aid}/screenshot` | 41 | · | ✓ | · | ✓ | · | 前端「连接/模拟器页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2964 | `POST /api/action/smart_login` | 23 | · | ✓ | · | ✓ | · | 前端「智能调度动作」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 2987 | `POST /api/action/smart_annihilation` | 25 | · | ✓ | · | ✓ | · | 前端「智能调度动作」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3081 | `POST /api/instance/rebuild` | 6 | · | ✓ | · | ✓ | · | 前端「MAA 日志/实例/更新」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3087 | `POST /api/maa/check_update` | 48 | · | ✓ | · | ✓ | · | 前端「MAA 日志/实例/更新」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3135 | `POST /api/maa/download_update` | 21 | · | ✓ | · | ✓ | · | 前端「MAA 日志/实例/更新」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3161 | `GET /api/maa/status` | 38 | · | ✓ | · | ✓ | · | 前端「MAA 日志/实例/更新」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3199 | `POST /api/maa/launch_source` | 19 | · | ✓ | · | ✓ | · | 前端「MAA 日志/实例/更新」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3218 | `POST /api/maa/check_source` | 14 | · | ✓ | · | ✓ | · | 前端「MAA 日志/实例/更新」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3232 | `GET /api/maa/instances` | 32 | · | ✓ | · | ✓ | · | 前端「MAA 日志/实例/更新」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3317 | `POST /api/orch/check_update` | 130 | · | ✓ | · | ✓ | · | 前端「项目自更新页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3467 | `POST /api/orch/update` | 55 | · | ✓ | · | ✓ | · | 前端「项目自更新页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3522 | `GET /api/orch/update_result` | 13 | · | ✓ | · | ✓ | · | 前端「项目自更新页」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3541 | `POST /api/config/export` | 10 | · | ✓ | · | ✓ | · | 前端「配置导入导出/AI」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |
| 3551 | `POST /api/config/import` | 21 | · | ✓ | · | ✓ | · | 前端「配置导入导出/AI」在用；Node 无原生 → proxyTo 到 Python（mjs:886/891） |

### 2.7 SPA catch-all

| 行 | 方法 路径 | 行数 | M | P | N原生 | N透传 | T | 备注 |
|---|---|---|---|---|---|---|---|---|
| 3824 | `GET /{full_path:path}` | 32 | · | ✓ | ✓ | · | · | Python 静态服务；Node 已自服务同一份前端（mjs:899、918-943）→ 情形 A 可退 |

## 3. 关键交集

- **M ∩ N原生 = 5 条**：`/api/status`、`/api/node/dashboard`、`/api/account_status`、`/api/health`、`/api/queue` —— **Node 有原生实现 ≠ Python 可删**，这正是第一阶段 U1 判据的承重错误。（Lead 点出 4 条，实测为 5 条：`/api/health` 也是 Node 原生（mjs:779）且是 MCP 工具；它本来就在 ① 里，未受影响。）
- **N原生 ∧ ¬M = 9 条**（含 `/api/accounts`）：这 9 条是「Node 抢答、Python 侧冗余」的**唯一**候选集，且**还要求前端不从 19997 加载**才成立。
- **P ∧ N透传 = 76 条**：这是真正的「前端在链路上」的证据 —— 前端在 19999 上调用它们时，Node 会转发到 Python。

## 4. 三种情形的可删量与前置动作

| 情形 | 可删 | 条数 / 行数 | 前置动作（必须先做完） | 备注 |
|---|---|---|---|---|
| **B. 现状：保留 Python UI 作为默认人工入口** | C1 | **23 条 / 248 行** | ① 把 `close_popups`、`kill_maa`、`orch/hot_reload`、`node/register`、`node/heartbeat` 落进运维文档或 CLI；② 核对 `maa_download.py:17` 注释；③ 确认 `kill_audit` 闸门（§6） | 收益最小。**C2/C3 一条都不能删**：前端仍可能从 19997 加载，命中即 404 |
| **A. 退役 Python UI**：默认入口切到 Node 19999，Python 不再服务 ui/web 静态 | C1 + C2 + catch-all | **32 条 / 419 行**（含 `/api/accounts` 则 33 条 / 455 行） | ① `main_web.pyw:348-352` 的 `url`/webbrowser.open 指向 19999（或取消自动开浏览器，仅保留托盘指向 19999，见 :375）；② 确认 Node 门面常驻（`tools/maaorch_platform.bat:16`）；③ 确认无人直接开 19997 看板（书签/快捷方式/文档）；④ 之后 `GET /{full_path:path}` 可退（Node mjs:899/918-943 已自服务） | **前端照旧、C3 的 76 条照旧在链路上**（Node 透传），所以这一步只回收 C2 的 8 条路由（含 `/api/accounts` 则 9 条）+ 32 行 catch-all |
| **C. 前端功能整体退役**：`ui/web` 这套看板不再作为产品界面（或逐页改打 Node 原生 API） | C1 + C2 + C3 + catch-all | **108 条 / 2364 行** | 在每个功能下线时同步删对应路由与前端代码；**逐功能确认**（§5）；闸门类需先迁到 MCP（§6） | 这是产品决策，不是重构动作；不能一次性批量删 |

> 行数口径：路由体行数（到下一个装饰器为止）是**上界**；共享 helper（`_account_state`、`_shot_cache`、`_lq/_runner`、中间件）不随路由删除而消失。全部删完后文件仍是「中间件 + ① 32 条 + 静态服务」，预计 ≈1300 行。

## 5. C3 按前端功能分组（逐项判定的工作底稿）

| 前端功能 | 条数 | 行数 | 代表路由 | 退役前置 |
|---|---|---|---|---|
| 账号管理页 | 16 | 284 | `/api/account/{idx}/status`、`/api/account/{idx}/stats`、`/api/account/{idx}/screenshot`、`/api/accounts/export` … | 该页/功能确认下线；或改打 Node 原生 API |
| 材料/活动页 | 10 | 168 | `/api/depot/scan`、`/api/depot/status`、`/api/depot/config`、`/api/depot/config` … | 该页/功能确认下线；或改打 Node 原生 API |
| 连接/模拟器页 | 10 | 317 | `/api/emulators/diagnose`、`/api/emulator/batch/{action}`、`/api/emulator/{idx}/{action}`、`/api/connect/launch` … | 该页/功能确认下线；或改打 Node 原生 API |
| MAA 日志/实例/更新 | 10 | 326 | `/api/maa/log`、`/api/maa/logs_history`、`/api/stage_checks`、`/api/instance/rebuild` … | 该页/功能确认下线；或改打 Node 原生 API |
| 统计/截图页 | 8 | 124 | `/api/screenshots/{aid}`、`/api/screenshots/file/{aid}/{run_dir}/{fname:path}`、`/api/screenshots/{aid}/export/{run_ts}`、`/api/export/logs` … | 该页/功能确认下线；或改打 Node 原生 API |
| 智能调度动作 | 7 | 214 | `/api/action/stop_all`、`/api/action/smart_all`、`/api/action/smart_maintenance`、`/api/action/smart_fight` … | 该页/功能确认下线；或改打 Node 原生 API |
| 配置导入导出/AI | 5 | 98 | `/api/config/sync`、`/api/config`、`/api/ai/analyze`、`/api/config/export` … | 该页/功能确认下线；或改打 Node 原生 API |
| 其他 | 3 | 36 | `/api/settings/smart`、`/api/stages`、`/api/stages/apply` | 该页/功能确认下线；或改打 Node 原生 API |
| 项目自更新页 | 3 | 198 | `/api/orch/check_update`、`/api/orch/update`、`/api/orch/update_result` | 该页/功能确认下线；或改打 Node 原生 API |
| 守护审计闸门 | 2 | 29 | `/api/kill_audit`、`/api/kill_audit/decide` | 该页/功能确认下线；或改打 Node 原生 API |
| 系统运维按钮 | 2 | 151 | `/api/system/open_folder`、`/api/system/restart` | 该页/功能确认下线；或改打 Node 原生 API |

## 6. 安全闸门例外（`kill_audit` 家族）——不要跟着前端一起删

实测：`models/kill_audit.py` 是「杀/关动作先确认再执行」的闸门实现；`services/runner.py:665-677` 与 `services/launch_queue.py:1426-1437` **进程内**调用 `request/expire_all`，而**人工/Agent 放行只有一条 HTTP 路**：`POST /api/kill_audit/decide`（api_fastapi.py:2174-2191）。

- `GET /api/kill_audit`（C3）：闸门读侧，前端守护审计页在用。
- `GET /api/kill_audit/pending`（C1，零调用方）：闸门待办读侧——**虽在 C1，但不建议直删**。
- `POST /api/kill_audit/decide`（C3）：**唯一放行口**。前端退役后若直接删，闸门就没有人工放行入口了。

**建议**：情形 C 中先把闸门迁成 MCP 工具（`kill_audit_list` / `kill_audit_decide`，写工具带 `dry_run=True` 默认），再删这三条路由；`POST /api/system/kill_maa`（C1，与闸门重复）也应在闸门迁移确认后才删。

## 7. 结论

1. **现状（情形 B）真正能删的只有 23 条 / 248 行**，占 139 条的 16.5% —— 第一阶段「U2 可整档判死、情形 A 能删 107 条」的结论**作废**。
2. Node 原生实现**只在「前端不再由 Python 提供」时才等于可删**，且必须排除 M（5 条交集）与 S/T（`/api/mas/*`、`/api/accounts`）；满足全部条件的只有 **8 条 / 139 行**。
3. 那 76 条 C3 路由的可删量**等于前端功能下线量**，必须逐功能走（§5 是分组底稿），并且 `kill_audit` 闸门要先有替代入口。
4. **行数收益的现实上限**：不退役前端 ≈ 248 行；退役 Python UI 但保留前端 ≈ 419 行；前端整体退役 ≈ 2364 行（≈ 全文件 3855 行的 61%）。
5. 无论哪个情形，`create_app` 单函数 3637 行的结构问题都不因删路由而解决；建议第二阶段同步按域拆 `APIRouter`（先拆 ① 的 32 条）。
