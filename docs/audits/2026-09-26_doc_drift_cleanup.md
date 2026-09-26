# 文档/注释端点漂移收尾（task-27）

> 2026-09-26 · api-curator。write scope：docs、services/maa_download.py、infrastructure/task_constants.py、services/runner.py、tools/batch_run.py。未执行 git。
> 事实源：network/api_fastapi.py **AST 导出的 49 条路由**。改完全仓 python -m compileall -q . = OK。

## 0. 摘要

| 项 | 数 |
|---|---|
| 扫描范围 | docs/*.md（不含 docs/audits/**，那是带日期的审计记录）+ 4 个代码文件 |
| 事实源路由 | **49 条**（AST 实测） |
| 复核后的真实漂移 | **55 处 / 14 个文件**（task-26 粗扫报的 http-api.md 28 处，精扫为 **27 处**；另 MAA_DOCS_MAP 那 1 处是扫描误报，见 §6） |
| 处理后 | **未标注的残留漂移 = 0**（验证脚本见 §7） |
| 改动方式 | 失效横幅 23 处（http-api.md）+ 行内标注 32 处 + 非端点事实修正 2 处（端口、catch-all）；**未删任何整节、未改文档结构** |

## 1. 方法（AST 导出，含一个坑）

不加 method 白名单时，AST 会多导出 1 条 —— api_fastapi.py:200 的 @app.middleware("http") 也是 app.<attr>(<str>) 形状，被误当成 MIDDLEWARE http 路由，于是得到 50；加白名单（get/post/put/delete/patch/websocket）后 = **49**，与正则计数一致。

匹配口径：路由 {...} 与文档 {index}/{n}/{aid} 均归一化为 {v}；带 {v} 的路由按前缀匹配；排除他方服务（manager 19998、mmui、AUTO-MAS :36163、penguin-stats、MAA 官方 API）。

## 2. 逐文件处置

| 文件 | 漂移处 | 处置 |
|---|---|---|
| docs/http-api.md | **27** | 23 个整节失效 → 节顶加**失效横幅**（保留历史）；3 处示例代码行加行内标注；另修 2 处非端点事实（默认端口、catch-all 静态服务） |
| docs/dev-guide.md | **7** | 端点表 6 行逐行标注「（2026-09-26 前端退役时删除）」；诊断端点 1 处行内标注 |
| docs/POSITIONING.md | **5** | S2 安全闸门表 1 处 + 动词表 set_slots/toggle_account 2 行标注 |
| docs/DEPOT_PLANNING.md | **3** | §5 API 表加 1 行前言 + 3 行逐行标注（/api/depot/plan 仍有效，未动） |
| docs/EMULATOR_SERVICE.md | **2** | §9.3.2 两行标注「历史规划」 |
| docs/scheduling.md | **2** | 表格行内标注 |
| docs/AI_AGENT.md | **1** | 历史事故段行内标注 |
| docs/HANDOVER.md | **1** | **交接文档**：原行下新增缩进子条，写明**日期 + 原因 + 替代路径**（§4） |
| docs/STRUCTURE.md | **1** | 历史决策段行内标注 |
| docs/maa-config-mapping.md | **1** | §7.1 诊断端点行内标注「以下为历史字段说明」 |
| services/maa_download.py | **2** | 注释修正（并**改指现存端点** GET /api/maa/status） |
| infrastructure/task_constants.py | **1** | 历史事故注释标注 |
| services/runner.py | **1** | 注释标注（保留 stop_all 语义） |
| tools/batch_run.py | **1** | docstring 标注 |

## 3. docs/http-api.md 详解（重点）

失效横幅统一文案（插在 23 个端点标题之后，整节保留）：

> ⚠️ **已于 2026-09-26 前端整体退役时删除**（路由 140→49）：该端点当前不存在，本节仅作历史留存；现行端点见本文档中未标注失效横幅的条目。

23 个加横幅的端点：GET /api/node/info、GET /api/account/{index}/screenshot、POST /api/account、POST /api/account/{index}/edit、POST /api/account/{index}/delete、GET /api/account/{index}/config、POST /api/account/{index}/config、POST /api/action/smart_all、GET /api/account/{index}/stats、GET /api/stats、GET /api/maa/log、GET /api/config、POST /api/config、POST /api/config/sync、POST /api/kill_maa、POST /api/system/restart、POST /api/system/close_popups、GET /api/maa/check_update、POST /api/maa/download_update、POST /api/orch/check_update、GET /api/sse、POST /api/config/export、POST /api/config/import。

另有 5 处示例/小节标注：

- 「### SSE 实时订阅」小节标题下加横幅：GET /api/sse 不存在，示例不可运行。
- SSE 节内 JS 行 const es = new EventSource('/api/sse'); 行尾加标注。
- SSE 订阅示例里 r = requests.get(f"{ORCH}/api/sse", ...) 行尾加标注。
- Python 定时启动示例里 requests.post(.../api/action/smart_all) 上一行加注释 + 本行行尾加标注（并指出等价能力现由 MCP 工具 smart_dispatch 提供）。
- 「技术实现」节 SSE 推送 bullet 行尾加标注。

**两处非端点事实修正**（同一份文档，属同批漂移）：

| 位置 | before | after |
|---|---|---|
| 连接信息·默认端口 | 19999（可在设置中修改） | Python API = models/config.json → api_port（2026-09-26 实测 **19997**）；**19999 是 Node 门面**（透传 /api/*）。历史默认值 19999 已不再指代本 API |
| 技术实现·静态文件 | catch-all 路由兜底到 ui/web/，SPA 单页入口 | 加标注：GET /{full_path:path} 与 ui/web/ 均已不存在，人工入口改为 Node 门面的 /status |

> 说明：「## 集成示例」里的 ORCH = "http://127.0.0.1:19999" 我**没改**。门面确实会把 /api/status 等存活端点透传给 Python，示例对存活端点仍可跑；改端口属于示例环境约定而非端点漂移，4 处一起改会扩大 diff。已列入 §6 存疑项。

## 4. docs/HANDOVER.md（交接文档，按要求标注日期与原因）

原行：- 日志页「📁 历史」面板：选账号 → 文件列表 → 查看内容（GET /api/maa/logs_history）

新增缩进子条：
- **2026-09-26 变更**：GET /api/maa/logs_history 与日志页随**前端整体退役**一并删除（路由 140→49）。归档文件仍落在 logs/maa_history/{aid}/，改由直接读文件或 MCP 查询，不再是 Web 功能。

## 5. 代码注释 4 处（before → after）

| 文件:行 | before（要点） | after（要点） |
|---|---|---|
| services/maa_download.py:12-14 | ensure_maa_available may be called from ... AND the /api/maa/download_update endpoint concurrently | 改指真实调用方 main_web.pyw:162,167；说明 /api/maa/download_update 已于 2026-09-26 删除、锁保留作防御 |
| services/maa_download.py:17-18 | queried by /api/maa/download_status for the UI | **改指现存端点**：经 GET /api/maa/status 回显（api_fastapi.py:1828）；原 /api/maa/download_status 已删除 |
| infrastructure/task_constants.py:186 | 后果: GET /api/account/{idx}/screenshot 拿不到 adb 端口 | 标注「该端点已于 2026-09-26 删除，此处为历史记录」 |
| services/runner.py:2042 | # /api/system/close_popups 手动端点），不做... | 标注该端点已删除，保留 stop_all 语义 |
| tools/batch_run.py:10 | SSE 可后接 /api/events_bus | 标注 POST /api/events_bus 已于 2026-09-26 删除、不再可用 |

> 只动注释/docstring，零行为改动；compileall 通过。

## 6. 没改但存疑 / 误报（按你的要求不动）

| 位置 | 判定 | 依据 |
|---|---|---|
| docs/MAA_DOCS_MAP.md:18 /api/maa_remote/* | **误报，未改** | 通配符写法；GET /api/maa_remote、POST /api/maa_remote/task|status **都还在** 49 条里 |
| docs/MAS_RUNBOOK.md:106,123、docs/PLATFORM_NODE_MIGRATION.md:33、docs/POSITIONING.md:49,101 的 /api/* | 未改 | 泛指写法，非具体端点 |
| tools/maa_all/mmui2.js 的 /api/state|/api/op | 未改 | 该工具自己服务的 API，不是 MAAOrch |
| manager/manager.py、tools/deploy.py:140,185,188、两个 _monitor_floating.py:24 的 /api/exec|start|stop | 未改 | manager(19998) 端点，仍有效 |
| runtime/_ce6/openapi.json（旧 139 路由 OpenAPI 快照） | 未改 | runtime/ 不在 write_scopes，且不受版本控制；建议下次采样覆盖 |
| docs/audits/**（含我自己 5 份报告里的旧端点） | 未改 | 带日期的审计记录，改了破坏历史可追溯性 |
| docs/http-api.md 集成示例的 ORCH = "...:19999"（4 处） | **未改，存疑** | 见 §3；若要统一改成 19997 请示下 |

## 7. 复现命令与验证结果

验证脚本逻辑：对每处「不在 49 条内」的 /api/... 引用，检查**本行或后两行**是否含 删除/退役/不复存在/不再可用/已不存在 标注；不含才算残留漂移。当前输出：

- 权威路由数: 49
- 未标注的残留漂移（期望 0）：未标注合计: **0**
- 全仓 python -m compileall -q . → **COMPILEALL_OK**

## 8. 与 task-26 报告数字的差异（供你更新台账）

| 项 | task-26 粗扫 | task-27 精扫（AST） | 原因 |
|---|---|---|---|
| 漂移总数 | 未给总数（只列文件） | **55 处 / 14 文件** | — |
| docs/http-api.md | 28 | **27** | 粗扫归一化时多算 1 处 |
| docs/MAA_DOCS_MAP.md | 未列 | 1 处（误报） | 通配符 * 被当成路径字符 |
| docs/scheduling.md | 4 | **2** | 粗扫把 /api/action/smart_selected、/api/oplog 误判（两者都还在） |
| 其他 | 大致相同 | dev-guide 7 / POSITIONING 5 / DEPOT 3 / EMULATOR 2 / 其余各 1 | — |

## 9. 未做/待决策

1. docs/http-api.md 集成示例 4 处 19999（见 §6）。
2. runtime/_ce6/openapi.json 旧快照（不在 scope）。
3. 若希望「失效节」将来物理移除（而非横幅留存），需要单独的文档整理任务；本次按要求只加横幅。
