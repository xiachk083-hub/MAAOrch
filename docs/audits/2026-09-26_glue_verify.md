# 独立验证报告：T1–T3（冻结快照复跑）

> 验证者：glue-verifier（task-4）；时间：2026-09-26 15:15–15:30（本机）
> 方式：**不采信自述**，全部结论来自读源码 + 桩测试 + 实测命令。行号以本次冻结快照为准。
> 结论：**三批改动方向正确、无越界写、无功能回归证据**；另有 **2 项残留风险**、**4 处文档口径不符**、**1 项提交污染**（我自己的快照被带进提交）。

---

## 0. 结论速览

| 项 | 判定 | 一句话 |
|---|---|---|
| G3 health 闸门 | ✅ 安全 | 闸关时 `taskkill` 桩测调用数 = **0**，且留痕；闸开时只杀「有账号映射」的僵尸 VMM |
| G5 顺序后移 | ✅ 语义成立（附条件） | `_cleanup` 内 release 前无 `return`/`raise`，24 个退出路径全走 `_cleanup`；守卫短路推演证实「改前顺序确实让重试链不可达」 |
| G6 挂载开关 | ✅ 生效且默认不变 | `create_app(mw)`：`mcp_mount_enabled=False` → fastmcp **未导入**、无 `/mcp` 路由、0.10s；`=True` → 1.39s、有 `/mcp` |
| T3 MAS 接入 | ✅ 符合契约 | `@mcp.tool()` **25** 个（新 mas_* **7** 个）；异常场景 **7/7** 返回结构化错误；`dry_run=True` 不 import mas_l3 |
| T1 文档事实 | ⚠️ 基本准确 | 25 个工具名逐名一致；4 处口径/行号需回写（见 §4） |
| 越界写 | ✅ 无 | `git status` + 近 45 分钟 mtime 扫描：仅 T1/T2/T3 scope 内文件 + 我自己的 `docs/audits/**` |
| 提交污染 | ❌ 需处置 | commit `78ad828` 把 `docs/audits/snapshot/**`（10 个冻结副本，约 1.3 万行）与 `.tmp_*.json` 一起提交了 |
| 残留风险 | ⚠️ 2 项 | R1：`emit_finished` 回调抛异常会跳过 release + 自动重启；R2：G5 生效后「runner 自动重启 × 队列重试」双入队（§6） |

---

## 1. 方法与冻结快照

**快照时点**：2026-09-26 15:15:46。副本落在 `docs/audits/snapshot/`（**已改成非 .py 后缀** `.frozen`/`.orig`，避免被 compileall / 路由统计 / import 扫描当成源码）。

| 文件 | 15:15 冻结 hash(SHA256 前16) | 15:27 复核 |
|---|---|---|
| services/runtime_health.py | 8502D8FF804A5DC5 | SAME |
| services/runner.py | 0882ABD588F0ADB9 | SAME |
| network/api_fastapi.py | 63EA3A61C0FE20E4 | SAME |
| network/mcp_server.py | 7257E3C3EC3079F5 | SAME |
| services/mas_adapter.py | 511FE01CDDAB2A65 | SAME |
| docs/POSITIONING.md | 6F45C165D4964150 | SAME |

- 基线：`git show HEAD:<path>`（当时 HEAD=942ad3d）→ `docs/audits/snapshot/base/*.orig`。工作区与基线用 `git diff --no-index` 逐文件比对，结论不随在线工作区变化。
- 期间 api-curator 的 task-5 仍在跑（scope 含 `network/api_fastapi.py`），但其未写该文件：**全程无漂移**（上表 15:27 复核仍 SAME）。
- 复跑：`python -m compileall -q .` → **exit=0，无 error**；核心模块导入 **9/9 OK**（services.runner / launch_queue / runtime_health / mas_adapter / mas_l3 / event_bus / instance_pool / network.api_fastapi / network.mcp_server）。
- 桩测试：`docs/audits/_verify_stubs.py.txt`（24 项断言 **24 PASS / 0 FAIL**）、`docs/audits/_g6_check.py.txt`（G6 端到端）。均为 .txt，不进源码树、不参与模块扫描。
- 本报告未执行任何写状态 git 命令（仅 `log`/`show`/`status --no-optional-locks`/`diff`/`ls-files` 只读）。

---

## 2. T2 三处改动逐条审查

### 2.1 G3 —— health 僵尸 VMM 总闸（services/runtime_health.py）

改动 `+59/-32`（HEAD numstat）。闸读在 **268-271**，`taskkill` 在 **313**，全部位于 `else`（闸开）分支内。

桩测（patch 掉 `subprocess.run`/`psutil`，不碰真实进程）：

    gate=False → taskkill=[]                      issues=[]                log=['已跳过僵尸 VMM 清理（emu_reclaim_enabled=False）']
    gate=True  → taskkill=[['taskkill','/F','/PID','111']]  issues=['zombie_vmm']   # idx9 无账号映射未被杀

**逐条结论**
1. **闸关真的不杀**：`if not _vmm_gate:` 分支只 `mw._log(...)`，`taskkill` 语句不可达 → 桩测调用数 0。**不是「只加判断仍执行」**。✅
2. **失效方向安全**：`_vmm_gate = bool((getattr(getattr(lq,"ctx",None),"config",None) or {}).get("emu_reclaim_enabled", False))`；`lq is None`/config 缺失/异常 → `False` → 不动。默认**不杀**，不会退化成历史行为。✅
3. **与回收器同语义**：键名与缺省值同 `services/launch_queue.py:1089`（`if not self.ctx.config.get("emu_reclaim_enabled", False): return`）一致；另加「idx→账号映射」二次过滤（`_vmm_emu2aid`，290-306），无映射不碰。✅
4. **闸关非静默**：`mw._log` 留痕实测命中。✅
5. **范围提醒（非缺陷）**：health 里**还有 4 处 taskkill 未纳入该闸**：L124（僵尸 MAA）、L143（幽灵 MAA）、L184（无主 MAA.exe 系统级扫描）、L249（MuMuNxCrashReporter 弹窗，按 IM 全杀）。它们杀的是 MAA/弹窗进程而非模拟器 VMM，与「模拟器回收闸」不同域，故 G3 未覆盖属可接受；若 Lead 想要「health 端点绝不动用户手动开的东西」这条更强承诺，需另开一项（L184 最激进）。
6. **注释与代码的细微不符**：L264 注释称「原实现会在这里 taskkill 用户手动开的模拟器」——原实现的杀条件实为「VMM 在 + 该 idx 不在 `_running_emulators()` 里 + 起过 5 分钟」，被杀的是**已停止/残留 VMM**，不是「正在运行的手动模拟器」；真实过杀面是「VMM 残留 + idx 无账号映射」。建议改注释措辞，避免以后按错误前提改代码。

### 2.2 G5 —— `_release_emu_mark` 后移到 `emit_finished` 之后（services/runner.py）

改动 `+7/-3`。release 从 L3121 前移到 **L3222**（`self.emit_finished(...)` 在 **L3215**），所在函数为 `_cleanup`（**3023-3306**）。

**逐条结论**
1. **「任何退出路径都释放」仍在**：`_cleanup` 在 3023-3222 之间**无 `return`、无 `raise`**（Select-String 实测 0 命中）；`_cleanup(` 全仓调用点 **24 处**（runner:465…2570，覆盖 -1/-2/-3/-8/-9/-11/-13/-999/0/rc 各类退出），另有 `stop()` 路径在 **L569** 单独释放。✅
2. **守卫短路是真的（改前的 bug 成立）**：`launch_queue.py:336-339`：`old = self._active_emus.pop(emu_idx, None) ... if old != account_id: return`。`_release_emu_mark`（runner:582-593）先 pop 掉 `_active_emus`/`_active_emus_ts` → `old is None != aid` → **提前 return**，其后 -3 超时重排 / -8 模拟器重启 / 指数退避重试**全部不可达**。桩推演：`release-first → early-return(重试/退避不可达)`、`release-after → proceed`。修复方向正确。✅
3. **改后语义**：`emit_finished` 先跑 → `on_account_finished` 正常 pop（`old == aid`）→ 走策略；随后 L3222 的 release 成为幂等空操作。顺序正确。✅
4. **残留风险 R1（🔴 建议修）**：`emit_finished` 实现是 `for cb in self._finished_callbacks: cb(data)`（runner:286），**无 try/except**；L3154-3155 的 `remove_dispatch()` 也**未包 try**（其内部 `_save()` 已自吞异常，故实际风险低，但 import 失败等会外抛）。任一处抛异常 → 其后所有语句（含 **release 3222** 与 **自动重启 3229**）全跳过 → 语义从「任何退出路径都释放」退化为「任何未抛异常的路径才释放」。改前 release 在更前面，故这是 G5 引入的**覆盖面收窄**。建议：`emit_finished` 内逐回调 try/except，或把 release 挪进 `finally`。
5. **兜底存在（限制影响面）**：`_active_emus_ts` 有 stale 清理（launch_queue:878-1027，阈值 `get_launch_ready_timeout(cfg,150)+30`）；即使漏 release，最坏退化为**≤180s 的旧窗口**（runner 注释写「150s」，实际阈值 +30，口径可修）。

### 2.3 G6 —— MCP 挂载开关（network/api_fastapi.py）

改动 `+27/-14`。闸读在 **3793-3797**，`from network.mcp_server import build_mcp_app` 在 **3805**（`else` 分支内）。

端到端实测（`docs/audits/_g6_check.py.txt`，permissive mw stub，全新进程）：

    flag=off  api_fastapi_import=0.54s  create_app=0.10s  fastmcp_in_sys.modules=False  mcp_routes=[]        err=
    flag=on   api_fastapi_import=0.54s  create_app=1.39s  fastmcp_in_sys.modules=True   mcp_routes=['/mcp']  err=

**逐条结论**
1. **开关关闭真能省下导入**：`mcp_mount_enabled=False` 时 `create_app` 不导入 fastmcp、不注册 `/mcp`；`network.api_fastapi` 模块级导入本身**不**拉 fastmcp（0.57s，`fastmcp_loaded=False`）。✅
2. **缺省行为不变**：缺省 `True` → 挂载 `/mcp`；配置读取失败 → `_mcp_on=True`（fail-safe 到「挂载」）✅；跳过时 `mw._log("⏭ 已跳过 MCP 挂载…")` 留痕 ✅。
3. **数字口径**：代码注释称「fastmcp 导入使 create_app 慢约 5-6s」。本机实测增量 **≈1.29s**（1.39-0.10；`import fastmcp` 单独 0.82s、`network.mcp_server` 1.59s，均热 pyc）。冷缓存未测。建议改成「本机实测 ~1.3s（冷启动更久）」，避免以后拿 5-6s 评估部署收益。
4. 开关只在**挂载**层；MCP 端点鉴权（`_TokenAuth`）与 `/api/*` 鉴权不受影响（mcp_server.py:65 / api_fastapi.py:218）。

---

## 3. T3 复核（services/mas_adapter.py + network/mcp_server.py）

- **工具总数**：静态解析 `@mcp.tool()` = **25**；与 POSITIONING 名单互为子集（差集空）。新增 7 个：`mas_snapshot, mas_status, mas_history, mas_ledger, mas_rerun_state, mas_rerun, mas_cleanup`（mcp_server.py:204-260）。✅
- **不依赖 REST 新增路由**：`mas_adapter.py` 全文无 `urllib/requests/socket`（只 import importlib/os/sys/threading）；MCP 的 mas_* 工具是**进程内**调 `services.mas_adapter`（`from services import mas_adapter` ×7），不经过 `/api/*`。✅ MAA 侧旧工具仍走 `_call_api` 复用既有端点，未新增旁路。
- **不可达返回结构化错误（桩测 7/7）**：把 `_MOD` 换成「任何属性都抛 `RuntimeError('ssh timed out unreachable')`」的桩，7 个入口全部返回 dict、`ok=False`、带 `error`，**无一抛异常**；`degraded=True`（`_degrade` 命中 unreachable 指纹）。✅
- **写工具默认预演**：`cleanup(dry_run=True)` → `{dry_run:True, action:'cleanup'}` 且 `services.mas_l3` **未被 import**；`rerun(accounts=['A1','A2'], dry_run=True)` → `would_start=['A1','A2']`，未触达远端。✅
- **门面边界**：`mas_adapter` 依赖的 7 个 mas_l3 接口全部存在且可调用（`host_snapshot/collect_status/history/_ledger_tail/rerun_state/rerun_async/cleanup`）；`_MODULE="services.mas_l3"` 惰性加载 + 补 sys.path 重试。✅
- 契约小瑕疵（非本次改动）：`snapshot()/status()` 的 `_call(...) or _err(...)` 只在 mas_l3 返回 **falsy** 时兜底，返回 `{}` 时走 `or` 分支（可接受）。

---

## 4. T1 文档事实核对（docs/POSITIONING.md，129 行 / 非空 99 行）

**一致项（逐条通过）**：`mcp_server.py:25 _call_api`、`:55-82 _TokenAuth`、`:65 空 token 不鉴权`、`:89-260` 工具区、`:91-260` 25 工具；`mas_adapter.py:83-112`（降级不抛）、`:172-207`（rerun/cleanup）；`launch_queue.py:168 pause / 172 resume / 229 enqueue / 284 dequeue`；`api_fastapi.py:198-228`（token 读取 + 强制校验，实际拦截在 218-228）、`:218`、`:1692 batch_save`、`:1708-1713`（挂起审计 + 字段循环）、`:2068 /api/config` + `:2070 parallel_max`、`:3012 smart_selected`；`mas_l3.py:270 rerun_async`、`HOST_CACHE_TTL=45.0`、`:36 API=http://100.79.173.69:36163`、**mas_l3 对 event_bus 引用数 0**（实测 0）；`event_bus.py:9-14` 域前缀 5 行逐字一致；`instance_pool.py:216` = `desired = ctx.config.get("parallel_max", 1) + 1`；`models/config.json api_port = 19997`；`models/config_manager.py:16` 默认 19999；`server/maaorch-web.mjs` **无 mcp 关键字**（0 命中）；`ui/web/index.html:34` 首屏确实并发请求 `/api/health`（G3 影响面的前提成立）；`services/maa/v6.11.1` + `instances/1..3` 存在；5 个交叉引用文档、`runtime/mas_ledger.jsonl`、`logs/event_bus.jsonl` 均存在；`tools/auto_mas_*.py` 的 `--apply/--to/--name/--enable/--dry-run`、`TAG="临时-补跑"` 均属实。

**不符/需回写（4 处，均低危）**：

| # | 文档 | 实测 | 处置 |
|---|---|---|---|
| D1 | `mas_adapter.py（207 行）`，且文首声明「行数口径 = **非空行**」 | 非空行 = **168**；207 = 总行数 | 改成 168（或改口径声明为「总行数」并统一 mas_l3/mas_watch）。mas_l3=257、mas_watch=242 按非空行口径是对的 |
| D2 | `api_fastapi.py:3794 app.mount("/mcp")`（G5 节） | 现为 **3807**（G6 在挂载块前插了 13 行）；3794 是 G6 之前的行号 | 回写 3807；文首已声明「以代码为准并回写」，属预期漂移 |
| D3 | G5 节「19999 已被 Node 看板**占用**」 | `server/maaorch-web.mjs:65` 默认端口是 **19998**，19999 只在文档示例 `--port 19999` 出现；当前 netstat 无 19999/19997 监听 → 「占用」未复现 | 改成「Node 看板按示例以 --port 19999 启动时占用 19999（默认 19998）」，去掉「实测占用」的确定性 |
| D4 | `services/instance_pool.py:216 ensure_maa_instances` | 函数名为 **`ensure_maa_instances_async`（def 在 203）**；216 是函数体内 `parallel_max+1` 那行 | 函数名写准（行号 216 本身指对了 parallel_max+1） |

---

## 5. 越界与提交污染检查

**越界写：无。** `git status --porcelain`（15:27）只剩 ` D .tmp_*.json` ×4 与 `?? docs/audits/*`；T1/T2/T3 的 6 个目标文件已被 Lead 提交进 `78ad828`，改动范围 = 各自 write_scopes（`docs/POSITIONING.md`；`services/runtime_health.py`、`services/runner.py`、`network/api_fastapi.py`；`network/mcp_server.py`、`services/mas_adapter.py`）。

独立佐证：全仓「近 45 分钟被改动文件」mtime 扫描（排除 `__pycache__`/`.git`/`docs/audits`）只命中上述 6 个目标文件，其余是运行时产物 `runtime/fleet_cases/*.png`、`runtime/rogue_fleet_state.json`、`runtime/rogue_fleet_watch.log`（在线巡检进程写的，非本批次）。**排除误伤**：没有计划外源码文件被改。

**提交污染：❌ 需 Lead 处置（我的责任）**：commit `78ad828` 的 stat 含
- `docs/audits/snapshot/**` **10 个文件 / 约 13,000 行** —— 我为本任务落的冻结副本（`.frozen`/`.orig`），**不应进产品提交**；
- `.tmp_final.json`、`.tmp_rows.json`、`.tmp_rows2.json`、`.tmp_spans.json`（约 5,400 行）—— 仓库根临时产物同批被 `git add` 进去，现工作区已删（` D`），需下次提交收口。

建议：下次提交 `git rm -r docs/audits/snapshot`（若 Lead 想留证据，则改名为 `docs/audits/snapshot/*.txt` 并标注「审计副本，非产品代码」）；根目录 `.tmp_*.json` 顺手加进 `.gitignore`。

---

## 6. 专项：G5 引出的「runner 自动重启 × 队列重试」双入队交互

> Lead 定向加查。结论基于源码 + 桩推演，未接真实队列。

### 6.1 两个生产者与去重键

| 生产者 | 位置 | 入队参数 |
|---|---|---|
| ① 队列重试（`on_account_finished`） | `launch_queue.py:398-400`（-3 在 350、-8 在 373、≥3 次失败重启模拟器 383-394） | `enqueue(aid, "retry", priority=max+1, not_before=now+delay)` → **slot 缺省 = ""** |
| ② runner 自动重启（`_cleanup`，emit_finished 之后） | `runner.py:3244-3251` | `enqueue(aid, "auto", priority=0, slot=ac.get("_slot",""))` |

去重规则：`launch_queue.py:249` **只删同 (account_id, slot) 的 pending 条目**（「allow different slots」是显式设计）。

### 6.2 回答 1：会不会双入队？哪些 slot 组合会？

**会 / 不会取决于启动时写入 `ac["_slot"]` 的值**（`launch_queue.py:839 found["_slot"] = entry.slot` 在每次 launch 时刷新）：

- **不会**（去重吸收，只剩 1 条）：启动 slot = `""`，即**定时（schedule）/手动（manual）/理智（sanity）** 以及 `api_fastapi.py:3073` 的 `_slot=""` 分支。桩推演：pending = `[('A1','')]`。注意顺序效应：①先入队（带退避 not_before）→ ②再入队（同键 slot=""）会**删掉①**，最终是②的**立即重跑**，即「退避被自动重启吃掉」。
- **会**（残留 2 条）：启动 slot 非空，即 `api_fastapi.py:2624/2656 maintenance`、`2628/2679 fight`、`2633/3004 annihilation`、`2728/2820/2849 connect`、`2979 login`、`3073 roguelike`。桩推演：`[('A1',''), ('A1','fight')]` / `[('A1',''), ('A1','roguelike')]`。

**前置条件（重要）**：生产者①只有在 `runner.emit_finished` 有订阅者时才执行，而**本仓没有任何代码给 `_finished_callbacks` 注册回调**（全仓 grep：仅 runner.py:187 声明 + 286 遍历，0 个 `.append`；`on_account_finished` 也无其它调用点）。即：**当前仓库里这条交互是「潜在」的，实际不会触发**；同时意味着 `launch_queue.on_account_finished` 的整段重试/退避/-8 重启策略在本仓是**死路径**（`launch_queue.py:1476` 注释假定它被 runner 调用）。若宿主 App（本仓外，`WebContext`/`mw` 的装配点不在本仓）注册了 `runner._finished_callbacks.append(lq.on_account_finished)`，双入队立即变为真实缺陷。

### 6.3 回答 2：后果是「多跑一轮」还是更严重？

**是「多跑一轮」，不会并发抢同一模拟器**，依据：
- `_tick` 每轮**只启动一个条目**（`launch_queue.py:750-755` 启动后 `break`，其余推回 pending）；
- 同轮第二个条目命中 `launch_queue.py:693-703`：`emu_idx in self._active_emus` → 跳过并推回；
- `enqueue` 入口 `launch_queue.py:240-244`：账号已有 MAA 进程在跑直接拒收。

所以残留的第二条会在第一轮**跑完后**才启动 → **同一账号多跑一整轮任务**（并额外触发一次 `_maybe_close_idle_emu`/事件/统计）；对剿灭/理智类任务意味着重复消耗。危险性上限：若两条都带重试语义，失败场景会以 2×3 次级别放大重试（②限 3 次 `_auto_restart_count`，①按 `consecutive_failures` 指数退避、≥3 次还会关机重启模拟器 383-394）→ 失败风暴风险上升，但仍是串行，非并发。

### 6.4 回答 3：怎么修？与 docs/AUTO_REQUEUE_LIFECYCLE.md 待拍板项的关系

**相关且高度重合**：该文档「改动点 1」（第 33-35 行）原文就是 `launch_queue.enqueue(aid, "retry", priority=0, slot=原slot)` —— 正是本次双入队的正解；「改动点 2」（36 行）要求保留 slot 语义；「改动点 3」（37 行）失败重入节流是兜住重复轮次的保险丝。**建议把 §6 作为该文档拍板时的输入**。

按推荐优先级：
1. **P0（最小、与待拍板项一致）**：给队列重试的 3 处 `enqueue(...)`（350/373/398）补 `slot=ac.get("_slot","")` → 与②同键，去重天然吸收，任何顺序下只剩 1 条。
2. **P0**：`_finished_callbacks` 加订阅者时，用 `emit_finished` 内**逐回调 try/except**（同时修 R1），避免「回调抛错 → release 与自动重启一起丢」。
3. **P1（收权）**：两个生产者只留一个 —— runner 自动重启分支专注「分类 + 挂起（环境问题）」，重入统一交给队列重试；或反之。避免两套退避/计数并行。
4. **P1（保险丝）**：`enqueue` 对 `Source in ("auto","retry")` 增加**跨 slot 去重**（同账号合并为一条，保留更晚的 `not_before`），防以后新增 slot 再踩。
5. **P2**：把 `launch_queue.on_account_finished` 是否被宿主装配做成**启动自检**（无订阅者时 WARN），否则「重试策略存在但永不执行」会一直隐性存在。

---

## 7. 给 Lead 的处置建议（按优先级）

1. **提交收口**：`git rm -r docs/audits/snapshot`（我的冻结副本误入 `78ad828`，约 1.3 万行）；`.tmp_*.json` 的删除已在工作区，下次提交带上；`.gitignore` 加 `.tmp_*.json`。
2. **修 R1**（§2.2-4）：`emit_finished` 逐回调 try/except，或 `_cleanup` 内把 release 放 `finally` —— 否则「任何退出路径都释放」有缺口。低风险，建议紧随本批。
3. **拍板 §6**：与 `docs/AUTO_REQUEUE_LIFECYCLE.md` 的待拍板项一起决策（P0 的 slot 补齐是三行改动）。
4. **回写文档 D1–D4**（§4），并把 health 注释（§2.1-6）与 fastmcp 耗时注释（§2.3-3）改准。
5. **明确 G3 边界**：health 端点仍有 4 处未闸 `taskkill`（L124/143/184/249）。若目标是「health 绝不杀用户手动起的进程」，需单列一项（L184 系统级 MAA 扫描最激进）。
6. **T3 接入片段**：POSITIONING「接入片段」里的 `19997/mcp`、Bearer token 与实测一致（config.json api_port=19997；`_TokenAuth` 支持 `Authorization: Bearer`），可按文档执行；Hermes `config.yaml` 属用户侧文件，本报告未触碰。

---

## 8. 局限与未覆盖

- **未做 MCP 端到端 HTTP 调用**（需起真实服务 + api_token），只做了模块级 + 进程内 adapter 验证。
- **未跑真实 `check_health`**（会真的 taskkill 本机进程）—— G3 用 patch 掉 `subprocess.run`/`psutil` 的桩验证，覆盖闸门分支与映射过滤，未覆盖 `_running_emulators` 的真实输出解析。
- **G5 的 `emit_finished` 订阅者不在本仓**，「队列重试策略是否真的活着」无法在本仓内证实；§6 的双入队结论在「已装配订阅者」前提下成立。
- **「19999 被占用」「fastmcp 冷启动 5-6s」未复现**（前者当前无监听；后者只测了热 pyc 的 ~1.3s）。
- `docs/audits/snapshot/**`、`_verify_stubs.py.txt`、`_g6_check.py.txt` 为验证工件；除已误入提交的 snapshot 外均为 .txt，不参与编译/导入。
