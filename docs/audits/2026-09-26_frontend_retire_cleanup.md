# 前端退役后的 4 条清单漂移收尾（task-26）

> 2026-09-26 · api-curator。write scope：`tools/deploy.py`、`tools/auto_mas_archiver.py`、`ui`、`docs/audits`。
> 未执行 git。改完自检 `python -m compileall -q .` = OK（§6）。验证员同批的 task-14 成果：`api_fastapi.py` 140 → **49 路由**（实测），`ui/web/` 除两个死资产外已清空。

## 0. 四条处置摘要

| # | 条目 | 处置 | 证据 |
|---|---|---|---|
| 1 | deploy.py 清单列了 3 份已删前端文件 | **已改**：从 DEFAULT_FILES 移除 | `deploy.py:86-87` 注释；实测 `DEFAULT_FILES`=52 条、`ui/` 条目=0 |
| 2 | archiver 会重建 `ui/web/data/` | **已改**：目标改到 `ui/retired_web_data/` | `auto_mas_archiver.py:59-69`；实测解析路径 = `ui\retired_web_data\auto_mas_history.json`，父目录存在 |
| 3 | 两个 debug 脚本调已删端点 `close_popups` | **未改（出界）**：`debug/` 不在我的 write_scopes；已核实现状并给出可直接套用的补丁 | §3 |
| 4 | `echarts.min.js` + `DSH.apk` 零引用 | **已删**（先四路验证零引用；删除前备份到系统临时目录） | §4：sha256 + 大小 + mjs 退役闸门 |

## 1. 条目 1：tools/deploy.py 清单

**核实**：`DEFAULT_FILES` 第 86-88 行原本列 `ui/web/app.js`、`ui/web/index.html`、`ui/web/style.css`，三份在 task-14 已删（现盘上 `ui/web/` 只剩 vendor 空目录）。`deploy.py:217-219` 对缺失文件只打印 `SKIP 本地不存在` 后 continue，属非致命；但清单与仓库事实不符。

**改动**（`tools/deploy.py:86-88` → 两行注释）：

```python
    "network/mcp_server.py",
    # 2026-09-26 前端退役：原 "ui/web/app.js" / "ui/web/index.html" / "ui/web/style.css"
    # 三份已删除，从清单移除；若再列 ui/* 静态文件，deploy.py:217-219 只会打印 SKIP 后 continue。
    "manager/manager.py",
```

**实测复核**：`DEFAULT_FILES 条数 = 52 | 其中 ui/ 条目 = []`（导入模块后断言，见 §6 命令 3）。

> 附注：`deploy.py:226-229` 的 `_frontend`/`_backend` 分流现在恒为「前端 0 个」。这属于无害死分支（`--file ui/...` 仍可手工指定），本次不动，避免超范围改动。

## 2. 条目 2：tools/auto_mas_archiver.py 的归档副本路径（最关键）

**先查读取方**（task-26 明确要求）：

| grep 模式 | 命中 | 结论 |
|---|---|---|
| `auto_mas_history` 全仓 | 4 处：`tools/auto_mas_hook.py:5`（docstring）、`tools/auto_mas_archiver.py:4,28,60`（自己的常量与复制行） | **没有任何读取方** |
| `retired_web_data` 全仓 | **0 处** | 迁移目标目录当前无人引用 |

**关键事实（两档不要混）**：

- **规范档**（canonical）= `data/auto_mas_history.json`（`auto_mas_archiver.py:28`；实测存在，427,022 B）。一切分析/后续消费都以它为准，**本次未动**。
- **前端副本**（原 `ui/web/data/`）= 只为已退役的前端 `ui/web/auto_mas.js` 静态秒开服务；前端删了 ⇒ 该副本已无读取方。
- `ui/retired_web_data/auto_mas_history.json` 已存在（248,219 B，task-14 迁移时放的旧副本）。写入方只有 archiver 一家 ⇒ **写入路径与读取方一致**（双方都指向同一文件；本副本无读取方，仅作目标机丢失后的历史备份）。

**改动**（`auto_mas_archiver.py:2` 与 `:59-69`）：

```python
    # 归档副本到 ui/retired_web_data/。
    # 2026-09-26（前端整体退役）：旧目标 ui/web/data/ 会把刚清掉的目录重新长出来，故改到本目录。
    # grep 核实（auto_mas_history / retired_web_data）结果：**本副本当前无任何读取方** ——
    # 前端 ui/web/auto_mas.js 已随前端删除；规范档仍是上面的 data/auto_mas_history.json。
    # 因此这里只作「目标机数据丢失后的历史备份」，不参与任何读取链路。
    web_arch = pathlib.Path(__file__).resolve().parent.parent / "ui" / "retired_web_data" / "auto_mas_history.json"
```

同时把文件头 docstring「（防目标机数据丢失 + 前端增量读取）」改为「（防目标机数据丢失；2026-09-26 前端退役后不再有前端读取方）」。

**实测复核**：`ARCHIVE(canonical) = D:\1-2\MAAOrch\data\auto_mas_history.json`；`retired copy target = D:\1-2\MAAOrch\ui\retired_web_data\auto_mas_history.json`，`parent exists = True`；源码中已无 `ui/web` 字样。

> 为什么不干脆删掉这份副本？因为它承担「目标机数据丢失后仍可复盘」的备份职责（archiver 的设计初衷），删掉会丢掉唯一的前端侧留档；改到 retired 目录即可满足「不再重建 ui/web/」又不损失备份。

## 3. 条目 3：两个 debug 脚本调已删端点（**出界，未改**）

**现状核实**（`api_fastapi.py` 实测 49 路由，`/api/system/*` **已全部不存在**；两个脚本都用 `PROJ = MANAGER.replace(":19998", ":19999")` 打到 Node 门面，Node 透传到 Python 后 FastAPI 返回 404）：

| 文件:行 | 代码 | 行为 |
|---|---|---|
| `debug/scripts/_monitor_floating.py:65-69` | 裸 `urlopen(Request(PROJ + "/api/system/close_popups", ...))` | **未捕获异常 → 脚本直接崩**（HTTPError 404） |
| `debug/scratch_20260814/_monitor_floating.py:68-75` | 同一调用，但包在 `try/except` 里 | 记一行 `close_popups err: HTTP Error 404` 后继续 |

**能不能改调 MCP？不能。** task-17 刻意**不暴露** `/api/system/close_popups`（`network/mcp_server.py:21-22`、`:495` 三处注释声明；实测 41 个工具里含 `/api/system/` 的 = 0）。也就是说这个能力已被有意退役，debug 脚本没有替代端点可调。

**为什么我没改**：`debug/` 不在 task-26 的 write_scopes（只有 `tools/deploy.py`、`tools/auto_mas_archiver.py`、`ui`、`docs/audits`）。按铁律不越界，故只给补丁。

**可直接套用的补丁（两处，均为「标注失效 + 保留窗口探测」）**：

`debug/scripts/_monitor_floating.py` —— 用下面 3 行替换第 64-69 行：

```python
    # 悬浮窗已出现 → 原「调 close_popups」验证已失效：
    # 2026-09-26 前端退役，POST /api/system/close_popups 已删除（api_fastapi.py 现 49 路由，无 /api/system/*），
    # MCP 也刻意不暴露它（mcp_server.py:21-22、:495）⇒ 无替代端点。本脚本只保留窗口探测。
    print("[已跳过] close_popups 端点已退役（404），不再调用")
```

`debug/scratch_20260814/_monitor_floating.py` —— 用下面 1 行替换第 69-75 行（第 68 行的 log 保留）：

```python
                log("close_popups 端点已退役（2026-09-26 前端退役，404），跳过该验证")
```

（第 76 行 `time.sleep(3)` 与之后的存活探测保留不变 —— 那部分与端点无关。）

**建议**：要么把 `debug/` 加进我这条任务的 scope 让我直接改，要么由 Lead 应用上面补丁。两条都不做的话，`debug/scripts/_monitor_floating.py` 下次运行必崩。

## 4. 条目 4：两个零引用资产（已删）

**零引用独立复核（四路）**：

| 路径 | 复核方式 | 结果 |
|---|---|---|
| `ui/web/vendor/echarts.min.js` | 全仓 grep `echarts`（不限扩展名） | 仅 2 处，**都在它自己文件内部**（`ui/web/vendor/echarts.min.js:21,45`）⇒ 0 外部引用 |
| `ui/web/DSH.apk` | 全仓 grep `DSH\.apk` / `(?i)\.apk|dsh` | `DSH.apk` **0 命中**；其余 `.apk` 命中都是无关内容（`docs/architecture.md:161-163` 讲游戏安装包、`server/maaorch-web.mjs:1046-1050` 是通用退役扩展名列表） |
| 两者 | 全仓 grep `vendor/` | 仅 1 处文档行，无代码引用 |
| 两者 | 服务端可达性 | `api_fastapi.py` 已无 catch-all（49 路由，无 `/{full_path:path}`）⇒ Python 不再服务任何静态前端；Node `maaorch-web.mjs:1050-1056` 对 `.js/.css/.html/.apk` 一律 **404**，`:1063-1065` 还有显式闸「`web/` 前缀永不出 Node」⇒ 即便文件还在磁盘也**不可达** |

**处置**：删除两个文件；删除前把内容备份到**仓库外**的系统临时目录（因为本机无 git，删除不可逆）：

| 原路径 | 大小 | sha256 | 临时备份 |
|---|---|---|---|
| `ui/web/vendor/echarts.min.js` | 1,029,203 B | `42f8329d989b6f6539dd2b15bbdf0d82025762ac112fbb60dc57b27d7bcf3946` | `C:\Users\xiach\AppData\Local\Temp\maaorch_retired_assets\ui__web__vendor__echarts.min.js` |
| `ui/web/DSH.apk` | 50,032 B | `8f9389b70697bdaf53ffd0ff0325523a4d2bb63742a33df77ec6a57a3b1c93e2` | `C:\Users\xiach\AppData\Local\Temp\maaorch_retired_assets\ui__web__DSH.apk` |

**复核结果**：两文件均已不存在；`ui/` 现存文件 = `control_server.py`、`lastrun.json`、`prefs.json`、`retired_web_data/auto_mas_history.json`、4 个 `search_*.json`。`ui/web/` 只剩空的 `vendor/` 目录（空目录不影响任何服务；mjs 的 `web/` 闸门保证它永不被服务，故未额外 rmdir）。

> 备份位置在系统临时目录，可能被系统清理。**要长期留档请告知**，我可以把它移到仓库内的归档位置（`ui/retired_web_data/` 正是这种定位）——本次按 task-26 的『确认后删除』执行，未擅自留副本在仓内。

## 5. 顺手做的独立扫描（发现的其他漂移，均不在本任务 scope）

我把「现有 49 路由」与全仓文本里的 `/api/...` 引用做了差集扫描，除条目 3 的两处外，**没有其他代码调用已删端点**。但有一批注释/文档漂移值得排期：

| 位置 | 漂移 |
|---|---|
| `docs/http-api.md` | **28 处**仍在讲已删端点（`/api/node/info:84`、`/api/stats:454`、`/api/logs:477`、`/api/system/close_popups:642`、`/api/system/restart:631`、`/api/config:507/526` 等） |
| `docs/dev-guide.md:116-121,163`、`docs/DEPOT_PLANNING.md:56-59`、`docs/scheduling.md:10,176,218,221`、`docs/AI_AGENT.md:40`、`docs/HANDOVER.md:69` | 同上（端点清单/示例） |
| `services/maa_download.py:13,17` | 注释仍称 `/api/maa/download_update`、`/api/maa/download_status` 供 UI 查询（已删） |
| `infrastructure/task_constants.py:186` | 注释引用 `GET /api/account/{idx}/screenshot`（已删） |
| `services/runner.py:2042` | 注释引用 `/api/system/close_popups`（已删） |
| `runtime/_ce6/openapi.json` | 是**旧 139 路由**的 OpenAPI 快照（扫描命中 83 处路径行），属 runtime 产物；建议下次采样时覆盖 |
| `tools/batch_run.py:10` | 注释称「SSE 可后接 `/api/events_bus`」（已删） |

**误报澄清（不是漂移）**：`tools/maa_all/mmui2.js:23,58-90` 调 `/api/state`、`/api/op` —— 那是 mmui 这个独立小工具**自己的**本地服务，不是 MAAOrch 的 API；`manager/manager.py` 与 `tools/deploy.py:140,185,188`、两个 `_monitor_floating.py:24` 的 `/api/exec|start|stop` 都是 **manager（19998）**的端点，仍有效。

## 6. 复现命令与结果

```powershell
# 1) 现状：ui/web 只剩两个死资产；api_fastapi 已 49 路由
Get-ChildItem -Recurse ui\web | Select-Object FullName,Length
(Select-String -Path network/api_fastapi.py -Pattern '^\s*@app\.(get|post|put|delete|patch|websocket)\(').Count   # -> 49
(Select-String -Path network/api_fastapi.py -Pattern '/api/system/').Count                                       # -> 0
# 2) 条目2 的读取方核查（结论：无读取方）
Select-String -Path . -Pattern 'auto_mas_history' -Include *.py,*.mjs,*.js,*.html,*.md   # -> 4 处，均为 archiver 自己 + hook docstring
Select-String -Path . -Pattern 'retired_web_data' -Include *.py,*.mjs,*.js,*.html,*.md   # -> 0
# 3) 改后断言（导入模块，不产生副作用）
python <temp>\maaorch_dep_check.py   # -> DEFAULT_FILES 条数 = 52 | ui/ 条目 = [] | False
python <temp>\maaorch_arch_check.py  # -> ARCHIVE = ...\data\auto_mas_history.json；retired target 父目录存在
# 4) 条目4 零引用核查
Select-String -Path . -Pattern 'echarts'   # -> 2 处，均在 echarts.min.js 自身
Select-String -Path . -Pattern 'DSH\.apk' # -> 0
# 5) 自检
python -m compileall -q .   # -> 无输出（exit 0）
```

## 7. 自检与未做事项

- `python -m compileall -q tools/deploy.py tools/auto_mas_archiver.py` → **COMPILE_OK**；全仓 `python -m compileall -q .` → **无 error**。
- 未做（需授权/需决策）：条目 3 的两个 debug 脚本改动（出界）；`docs/http-api.md` 等文档漂移（§5）；`ui/web/` 空目录清理；两个死资产的长期留档位置。