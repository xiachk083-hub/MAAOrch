# task-14 前端退役 · 独立验证报告（2026-09-26）

> 独立复核（非 task-14 作者）。只读验证 + 受控变异（备份→试跑→还原），未改任何生产文件。
> 证据形式：命令 + 文件:行号。

## 结论（先行）
- **task-14 的验收可信吗？可信，但只到「路由存在」这一层。** 49 条白名单与代码逐条精确一致（AST 独立复核），91 条被删路由没有生产调用方被打破。
- **那份测试能拦住回归吗？只能拦住结构回归（路由被删/改名/被加）；拦不住行为回归（路由还在但不可用）。** 反例见 §2 + R1。

## 1【最重要】测试有效性：证伪实验（证明非重言）
- 基线：`python -m pytest tests -q` → **123 passed**；单跑 `tests/test_routes_whitelist.py -q` → **71 passed**。
- 变异：注释掉 `network/api_fastapi.py:850` 的 `@app.get("/api/health")`（先备份到 %TEMP%，try/finally 还原）。
- 结果：**3 failed** —— `test_whitelist_route_exists[GET /api/health]`(test_routes_whitelist.py:167)、`test_no_extra_business_routes`(:173)、`test_mcp_referenced_paths_all_reachable`(:275，报 `['/api/health']`)。
- 还原后：SHA256 回到 `4842B842…`，`git diff` 空，**123 passed**。
- 判定：**测试不是同义反复**——删白名单路由它真的会失败。

## 2 已知反例复核（原判断需修正）
- 变异：`network/api_fastapi.py:2082` `@app.get("/api/inspect/account/{name}")` → `{account_name}`，函数签名仍 `name`。
- 行为复现：TestClient `GET /api/inspect/account/someacct` → **422** `{"loc":["query","name"],"msg":"Field required"}`（临时探针，未进仓）。
- **但存在性断言同时报错：2 failed**（`test_whitelist_route_exists[GET /api/inspect/account/{name}]`、`test_no_extra_business_routes`）。
- ⇒ 原说法「存在性断言照样通过」**对最终测试文件不成立**：白名单存的是精确模板 `{name}`，路径参数改名会被逐字比对抓住。mcp_server.py:138 用的是 `{account_name}`，两者本就不同名，靠 `_norm_path`(test:153-156) 折叠 `{}` 才对得上。
- **真正漏网的弱断言（已实测）**：路径/方法都不变，只把 `def handle_health():` 改成 `def handle_health(token: str):`（多一个必填 query 参数）——**71 passed / 全套 123 passed**，而 `GET /api/health` → **422**。即测试只验「路由已注册」，不验「路由可用」。

## 3 49 条白名单独立核对（AST）
- 方法：`ast` 取 `@app.<method>("path")`（FunctionDef.decorator_list），显式排除 `@app.middleware("http")`（network/api_fastapi.py:200；不排除会得 50，正是题述陷阱）。
- 文档取数：gate §4 = **32** 条，recheck §4 = **17** 条，合并去重 = **49**。
- 代码实际：**49** 条。集合差 `WL_MISSING_FROM_CODE=[]`、`CODE_EXTRA_NOT_IN_WL=[]`、`CODE_ROUTES_EQUAL_WL=True`。
- ⇒ 49 条口径与代码**逐条一致**，无多无少。

## 4 MCP 41 工具路径可达性
- `_call_api`/`_gate` 提取到 **35** 个唯一路径（多工具共用同路径）。按题述显式展开 `/api/queue/{action}` → `/api/queue/pause` + `/api/queue/resume`。
- 结果：**MCP_UNREACHABLE=[]**，全部落在 49 条内。
- 弱点：该断言**方法盲**——`route_set = {_norm_path(p) for _, p in ...}`(test:280) 丢弃 HTTP method，且 `_norm_path` 把所有 `{...}` 折叠为 `{}`。单看它，GET↔POST 互换、f-string 参数写错都能过（目前只靠 `test_no_extra_business_routes` 额外钉死 method+path 才兜住）。

## 5 被删 91 条路由的调用方差集
- 旧 140 条（`9f9fab9^`）− 新 49 条 = **91 条被删**（与 commit message 一致）。
- 扫描 .py/.pyw/.mjs/.js/.bat/.ps1 共 **780** 个文件：
  - **无生产调用方被打破。** 主要命中 `server/maaorch-web.mjs:928-931/1007` 是 Node **原生**实现（/api/stages、/api/config、/api/settings/smart、/api/account_status、/api/account/toggle）；被删的是 Python 同名路由，Node 自服务，属设计内。
  - 其余命中为注释/历史说明（mcp_server.py:21-22、services/maa_download.py:13）与测试自身 RETIRED 负例清单。
  - 真实 404 调用方（非生产）：`debug/scripts/_monitor_floating.py:66`、`debug/scratch_20260814/_monitor_floating.py:70` 调 `POST /api/system/close_popups`（已删）。
  - 指向已删 ui/ 的悬挂引用：`ui/control_server.py:690-691` 服务 `ui/index.html`（已删）；`tools/gen_push_index.py:23` 写它；`runtime/_t_mas_render.js:4` 读 `ui/web/auto_mas.js`（已删）；`manager/manager.py:603` 仅存在性守卫。
  - **子串误报提醒**：`/api/config` 会命中 `/api/config_backup`(manager/manager.py:758)，`/api/account` 会命中 `/api/accounts`——差集必须用带边界正则，不能裸 `in`。

## 反例 / 弱点清单
- **R1（测试放过真实回归）**：/api/health 加必填 query 参数 → 71/71 通过与 422 并存。存在性 ≠ 可用性。
- **R2（原判断需修正）**：`{name}`→`{account_name}` 的 422 反例**会**被抓住（2 failed），不是漏网。
- **R3（方法盲 + 模板折叠）**：MCP 可达性断言丢弃 HTTP method、折叠路径参数。
- **R4（可静默跳过）**：test:278 `if not tools: pytest.skip`、test:200 `importorskip("httpx")`。
- **R5（覆盖不全）**：RETIRED_MUST_BE_GONE 只列 16/91 条，其余 75 条无正向「必须不存在」断言，仅靠「恰好 49 条」间接兜住。
- **R6（环境残留）**：debug 脚本与 ui/control_server.py 仍指向已删端点/文件。

## 未改动声明
- 每次变异均先备份 `%TEMP%\api_fastapi.py.verify_bak` 并 try/finally 还原；还原后 `Get-FileHash` 与改动前一致（`4842B842…`）、`git diff` 空、`pytest` **123 passed**；`Select-String "MUTANT|handle_health(token"` 无残留。
- 我自己因 tools.write 相对路径误落在仓内的 `undefined/temp/` 探针目录已删除；临时脚本本体保留在 %TEMP%（未进仓）。
- **并发提示**：验证期间另一会话（父/兄弟 agent）改动了本仓——`network/api_fastapi.py` 新增 P1 路径穿越修复（+14 行）、新增 `tests/test_depot_plan_traversal.py` 等，故最终 `git status` 非干净、全套测试变为 **136 passed**；该改动与本次验证无关，我未触碰亦未还原。
