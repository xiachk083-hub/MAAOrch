# 闸门复检（task-18）：/status 够不够用 + 白名单断言

> 2026-09-26 · api-curator · 只读复检 + 报告，**未改任何代码**（write scope 仅 docs/audits）。
> 复检对象：task-13（入口切换）、task-16（/status G1/G2/G5/G6/G7）、task-17（G3/G4 迁 MCP）。
> **不采信 task-16/17 自述**：本文全部结论来自我自己起的服务、我自己点的端点、我自己 monkeypatch 的工具调用。

## 0. 结论：现在可以开始退役前端了吗？

**可以开始，但 task-14 的删除清单必须以「49 条白名单」为排除集，而不是 task-12 的 32 条。**

| # | 约束 | 依据 |
|---|---|---|
| 1 | 白名单从 32 → **49 条**：task-17 的 16 个新工具又挂了 **17 条** Python 路由，**全部不在旧白名单里**（按 task-7 分档：C3 13 条 + C1 2 条 + C2 2 条），旧口径会把它们全删掉 | §4 断言：32/32 在 + 17/17 在 |
| 2 | Python 上游（19997）**必须常驻**：/status 的真值区（账号运行态、队列）与队列四按钮都是透传，上游不可达时降级为「未知」/ 501 | §2-A/B 实测 |
| 3 | 白名单断言单测仍**缺席**（task-12 标准第 9 条）——建议 task-14 同批补上，否则这次是「人工核对过」，下次没人保证 | §3-9 |

8 项运维动作：**全部可达**（在 Python 常驻前提下）；9 条闸门标准：**7 通过 / 1 需运维保证 / 1 未通过**。

## 1. 复检方法与冻结证据

| 文件 | sha256(前12) | 大小 | mtime |
|---|---|---|---|
| `network/mcp_server.py` | 5cd842eb78aa | 29902B | 2026-09-26T07:44:23.701Z |
| `network/api_fastapi.py` | 63ea3a61c0fe | 180130B | 2026-09-26T07:13:48.642Z |
| `server/maaorch-web.mjs` | b02f0e300569 | 57880B | 2026-09-26T07:44:59.778Z |
| `main_web.pyw` | ea4c83e876b8 | 17441B | 2026-09-26T07:42:09.675Z |
| `services/mas_adapter.py` | 511fe01cddab | 9423B | 2026-09-26T07:10:22.271Z |

复检手法（三路互证）：

1. **起真服务**：`node server/maaorch-web.mjs --port 19996 --upstream http://127.0.0.1:19995`（19995 是我写的 echo 上游，用来观察 Node 到底转发了什么）；另起 `--port 19994 --upstream http://127.0.0.1:19993`（死端口）测降级。用完已 job_kill。
2. **直接 httptest 端点**：用 Node http 客户端打上面两个实例，不看代码猜。
3. **monkeypatch FastMCP 做行为测试**：把 `network.mcp_server` 的 `FastMCP` 换成记录型假类，拿到 41 个工具函数直接调用，验证 dry_run 闸门与禁用路由（脚本在系统临时目录，跑完删除，未进仓）。

## 2. 八项运维动作复测（实测）

| # | 动作 | 结论 | 实测证据 |
|---|---|---|---|
| 1 | 看每台模拟器 / 账号状态 | **可** | `/status` HTML 含 `emuBody`+`loadEmu`（G6）；实测 `GET /api/emulators` → HTTP 200，**emulators=2**（真读 MuMuManager/vm_config）；账号与运行态走 `GET /api/status`→Python 真值，页面标注「真值：Python HTTP 200」（mjs:351） |
| 2 | 看队列 | **可**（需 Python 活） | 页面 `loadQueue()` 打 `GET /api/queue`；实测上游活着时被转发到上游（echo 收到 `GET /api/queue`）；Python 返回键与页面取用完全对齐：`pending/active/pending_count/active_count/paused/avg_duration_min`（api_fastapi.py:1375-1377 vs mjs:363-365） |
| 3 | 看 MAA 是否在跑 | **可**（需 Python 活） | 真值 = Python `/api/status`（tasklist MAA.exe + runner._active）；Node 已改为透传（mjs:927 `proxyTruth`），不再抢答；上游死时页面显示「未知」，**不再显示 false**（实测 B1/B2） |
| 4 | 入队 / 出队 / 暂停 / 恢复 | **可**（需 Python 活） | `/status` 有 4 个按钮（HTML 实测含 `qact(`、`enqueue/dequeue/pause/resume`）；**端到端 body 验证**：POST `/api/queue/enqueue` {"account_index":1} → echo 上游收到 `{"echo":true,...,"body":"{\"account_index\":1}","got_body":true}`；`_proxy_stats` 记录 4 条命中 |
| 5 | kill_audit 人工放行 | **可** | MCP `kill_audit_decide` 存在；行为测试：默认 dry_run → `{ok:true,dry_run:true,would_call:{POST /api/kill_audit/decide}}`（零 HTTP）；`dry_run=False` → 真下发（死端口返回 WinError 10061）；后端 `POST /api/kill_audit/decide` 在（api_fastapi.py:2174） |
| 6 | 启动 / 停止 / 重启模拟器 | **可** | MCP `emulators/emulator_status/emulator_control/emulator_batch/connect_status/emu_connect_stop/emu_connect_restart`（7 个）；4 个写工具 dry_run 行为测试通过；后端 7 条路由全部在（2318/2380/1766/1771/2766/2825/2831） |
| 7 | 启动 / 停止 MAA | **可** | MCP `maa_status/maa_instances/account_launch/account_stop/maa_stop_all/maa_launch_source`（6 个）；3 个写工具行为测试通过；后端 3161/3232/1867/1876/2149/3199 全在 |
| 8 | 看日志 | **可** | `/status` 有 `logN`+`loadLogs`（G6）；实测 `GET /api/logs?lines=3` → HTTP 200，返回 `logs/debug.log` 尾部 3 行真实内容 |

> 唯一前提：Python 上游活着。上游死时（实测）：读端点返回 `200 {ok:false,unknown:true,degraded:python_unreachable}`，页面显示「未知」+ 顶部横幅；队列写按钮返回 `501 not_implemented_in_node`（**不是** task-16 前那种 6s 后 503 挂死）。

## 3. 九条闸门标准逐条判定

| # | task-12 §6 标准 | 判定 | 证据 |
|---|---|---|---|
| 1 | G1：/status 状态区不再抛错、running 反映真值 | **通过** | 实测 `/status` HTML **不含** `s.queue.count`、`parallel_max`、`location.reload`；改为按区块取数（mjs:344-406），运行态来自 Python 真值并在页面标注来源 |
| 2 | G2：显示 Python 真值、Node 不抢答 | **通过** | mjs:927 两条路由改 `proxyTruth`；实测上游活着 → echo 收到请求；上游死 → 200 unknown（不显示 false） |
| 3 | G3：kill_audit 放行有替代入口 | **通过** | 3 个工具（history/pending/decide），decide 默认 dry_run；行为测试见 §2-5 |
| 4 | G4：模拟器/MAA 启停有替代入口 | **通过** | 9 个新写工具（模拟器/连接 4 + MAA 4 + kill_audit 1），dry_run 行为测试全过（§5） |
| 5 | G5：队列四动作在 /status 可点 | **通过** | 按钮在 + body 端到端到达上游（§2-4） |
| 6 | G6：模拟器列表与日志可见 | **通过** | emulators=2 实测；logs 实测返回真行；两块都在 HTML 里 |
| 7 | 入口切换（main_web.pyw → Node /status） | **通过** | main_web.pyw:359 `node_url=http://127.0.0.1:{node_port}/status`、:366 `webbrowser.open(node_url)`、:391 托盘同 URL；Node `/`、`/index.html` 已改 302（mjs:1031-1035 注释+分支） |
| 8 | Python 常驻确认 | **部分通过（运行态前置）** | 代码侧已把真值/写动作都指向 Python；但复检当时 `Test-NetConnection 19997/19999` **均 False**（服务未起）。这是运维状态，不是代码缺陷——但必须写成硬前置 |
| 9 | 白名单固化 + 断言单测 | **未通过** | 仓内无 tests 目录、无路由断言（grep `_call_api`/白名单/routes() 无命中）；且白名单口径已过期（见 §4） |

## 4. 白名单断言：32 条 + 新增 17 条 = 49 条

方法：用正则取 `network/mcp_server.py` 全部 `_call_api(mw, path)` 路径（41 个工具），与 `network/api_fastapi.py` 实际路由表（140 条含 catch-all）逐条比对。

**旧 32 条：32/32 全部仍在**（无缺失）。

**task-17 新增工具驱动的 17 条（原 C3 可删档，现必须保留）：**

| 行号 | 方法 路径 | 驱动的工具 |
|---|---|---|
| 2157 | `GET /api/kill_audit` | kill_audit_history |
| 2165 | `GET /api/kill_audit/pending` | kill_audit_pending |
| 2174 | `POST /api/kill_audit/decide` | kill_audit_decide |
| 1766 | `GET /api/emulators` | emulators / emulator_control(preview) / emulator_batch(preview) |
| 1771 | `GET /api/emulator/{idx}` | emulator_status |
| 2318 | `POST /api/emulator/batch/{action}` | emulator_batch |
| 2380 | `POST /api/emulator/{idx}/{action}` | emulator_control |
| 2766 | `GET /api/connect/status` | connect_status / emu_connect_stop(preview) / emu_connect_restart(preview) |
| 2825 | `POST /api/connect/{aid}/stop` | emu_connect_stop |
| 2831 | `POST /api/connect/{aid}/restart` | emu_connect_restart |
| 3161 | `GET /api/maa/status` | maa_status / maa_launch_source(preview) |
| 3232 | `GET /api/maa/instances` | maa_instances |
| 1867 | `POST /api/account/{idx}/launch` | account_launch |
| 1876 | `POST /api/account/{idx}/stop` | account_stop |
| 2149 | `POST /api/action/stop_all` | maa_stop_all |
| 3199 | `POST /api/maa/launch_source` | maa_launch_source |
| 501 | `GET /api/account/{idx}/status` | account_launch/account_stop 的预演读取 |

**断言结果：32/32 + 17/17 全在；合并去重后白名单 = 49 条；api_fastapi.py 共 140 条路由（139 业务 + catch-all）。**

### 4.1 由此产生的删除红线（task-14 必读）

旧 32 条白名单**一条也不含**上面 17 条。而按 task-7 的分档，退役前端时这 17 条分别落在 C1（2 条：2165、1771）、C2（2 条：1766、2766）与 C3（13 条）——**全部会被删**。

后果：**41 个 MCP 工具里的 16 个会因后端 404 而当掉**——kill_audit×3、模拟器/连接×7（emulators、emulator_status、emulator_control、emulator_batch、connect_status、emu_connect_stop、emu_connect_restart）、MAA×6（maa_status、maa_instances、account_launch、account_stop、maa_stop_all、maa_launch_source）。且 `_call_api` 把 404 包成 `{error:HTTP 404}`，属于**沉默失败**（mcp_server.py:45-50）。

## 5. MCP 侧独立断言（41 工具 / dry_run / 禁用路由）

| 断言 | 结果 | 证据（行为测试） |
|---|---|---|
| 工具总数 41 | 通过 | monkeypatch FastMCP 后 `len(registered tools)=41`；分类：原 25 + kill_audit 3 + 模拟器 7 + MAA 6 |
| 9 个新写工具默认 dry_run=True | 通过 | 9/9 调用返回 `{ok:true,dry_run:true,would_call:{...}}`，**零 HTTP**（上游指向死端口也无报错）；另 mas_rerun/mas_cleanup 同样通过（共 11 个写工具） |
| dry_run=False 才真下发 | 通过 | 4 个抽样（emulator_control/account_launch/maa_stop_all/kill_audit_decide）返回 `WinError 10061 目标计算机积极拒绝` → 说明确实发了 HTTP |
| 不暴露 /api/system/{kill_maa,restart,close_popups} | 通过 | 41 个工具源码中含 `/api/system/` 的 = **0**；mcp_server.py:21-22、:495 仅注释声明 |
| 不暴露 /api/config | 通过 | 含 `/api/config` 的工具 = **0** |

## 6. 独立发现的次要问题（不影响本次放行，建议随 task-14 一起处理）

1. **Node 侧仍有一批「恒 false」的载荷函数**：`dashboardStatusPayload`(mjs:629)、`queuePayload`(mjs:640)、`accountsPayload`(mjs:567)、`accountStatusPayload`、`statsPayload`、`statusPayload`（后者仍是死代码）都读从未被写入的 `__running/__queued/...`。`/status` 已不用它们，但 `GET /api/accounts`、`/api/account_status`、`/api/stats` 等仍在向**外部调用方**返回恒 false 的假运行态。建议比照 G2 给这些响应加 `source:node-readonly-snapshot` 或 `unknown:true` 标记（否则 Harness 误用会拿到假数据）。
2. **501 文案硬编码端口**：上游不可达时 POST 透传的 detail 写着「旧 Python 上游（19997）」（mjs:521-523 区域），而实际端口取 `--upstream`/config；端口一变文案就误导。
3. **/status 未提供 queue/clear 按钮**：与 task-12 G5「四动作」约定一致，不算缺口；但白名单里 `POST /api/queue/clear` 仍按「判据 queue*」保留，属于「无入口但保留」，建议在文档里注明。
4. **白名单断言单测缺失**（闸门标准 9）：建议 task-14 增加一个测试：把 49 条写成常量，逐条断言 `api_fastapi.py` 里存在对应装饰器路由（可以用 3 行正则实现，防下一轮误删）。

## 7. 可复现命令（本次实际执行的）

```powershell
# 0) 冻结证据
Get-FileHash network/mcp_server.py,server/maaorch-web.mjs,network/api_fastapi.py,main_web.pyw -Algorithm SHA256
# 1) 起被测服务（临时端口；echo 上游用来观察 Node 到底转发了什么）
#    node <temp>\maaorch_echo.js                      # 19995
node server/maaorch-web.mjs --port 19996 --upstream http://127.0.0.1:19995
node server/maaorch-web.mjs --port 19994 --upstream http://127.0.0.1:19993   # 死上游
# 2) 实测（节选真实输出）
Invoke-RestMethod http://127.0.0.1:19996/api/status          # -> echo 收到 GET /api/status（证明 G2 透传）
Invoke-RestMethod http://127.0.0.1:19996/api/queue/enqueue -Method POST -Body '{"account_index":1}' -ContentType 'application/json'
#    -> {"echo":true,"method":"POST","path":"/api/queue/enqueue","body":"{\"account_index\":1}","got_body":true}  # task-16 的 body bug 已修
Invoke-RestMethod http://127.0.0.1:19994/api/status           # -> {ok:false,unknown:true,degraded:python_unreachable}
Invoke-RestMethod http://127.0.0.1:19996/api/emulators        # -> emulators=2
Invoke-RestMethod 'http://127.0.0.1:19996/api/logs?lines=3'   # -> logs/debug.log 尾部真内容
(Invoke-WebRequest http://127.0.0.1:19996/status).Content      # -> 含 banner/acctSel/qact/emuBody/loadLogs/未知；不含 s.queue.count/app.js
# 3) MCP 行为测试（脚本在临时目录，跑完删除）
python <temp>\maaorch_gate_test.py    # -> registered tools: 41；11 个写工具 dry_run=True 全部预演、0 HTTP
```

## 8. 给 Lead 的结论

1. **可以开始退役前端**（task-14）。8 项运维动作在 Python 常驻前提下全部可达；/status 从「报错页 + 2 个按钮」变成「真值展示 + 队列四按钮 + 模拟器 + 日志 + 来源标注 + 降级横幅」，且 G3/G4 的动作已由 41 个 MCP 工具覆盖。
2. **task-14 必须先更新白名单到 49 条**（新增 17 条见 §4），并按此作为删除排除集；否则 41 个工具里 9 个会因后端被删而失效（沉默 404）。
3. **把「Python 上游常驻」写成运维硬前置**（main_web.pyw 或等价机制的常驻/自启），并在文档里写明：上游不在时 /status 的真值区显示「未知」、队列四按钮返回 501（可读降级，不是崩溃）。
4. 建议 task-14 同批补上「49 条路由存在」断言单测（闸门标准 9 当前未通过），并把 §6 的 4 条次要问题记入待办。