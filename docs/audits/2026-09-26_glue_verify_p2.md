# 独立验证报告（第二轮）：T8 / T9 / T10

> 验证者：glue-verifier（task-11）；验证对象：commit `4afd0e1`（git log -1）与其父 `78ad828`
> 唯一工件：`docs/audits/_verify_stubs_p2.py.txt` —— 一次运行 **36/36 PASS**（含 4 个差分维度）
> 复现：`python docs/audits/_verify_stubs_p2.py.txt`（自足：内部只用只读 `git show` 取 HEAD~1/HEAD 两版到临时目录，跑完删除；不写任何项目文件）

---

## 0. 结论速览

| 项 | 判定 | 关键证据（实测） |
|---|---|---|
| T8 双入队 | ✅ 已修 | 真实 enqueue/on_account_finished：改造前非空 slot → pending **2**；改造后 → **1**（-3 / -8 / exit=1 三条路径全测） |
| T8 回调隔离 | ✅ 已修 | 爆炸订阅者桩：改造前异常冒泡、后续订阅者收不到、release 被跳过；改造后 **不冒泡 + 其余订阅者照常 + release 可达 + warn 留痕** |
| T9 六处杀进程 | ✅ 全闸住 | 闸关杀进程调用 **0**（旧版同场景 **5**）；闸开 **6**，且新旧**命令序列 / counts / issues 逐字一致** |
| T9 留痕 | ✅ | 闸关 6 条“已跳过…”日志，VMM 文案与 G3 逐字相同 |
| T10 四处事实 | ✅ 全部改对 | 168/207、3807、--port 19999 + 默认 19998、ensure_maa_instances_async:203 —— 逐条实测命中（1 处调用行列号 nit） |
| 越界写 | ⚠️ 1 项待确认 | 4afd0e1 里 `docs/audits/2026-09-26_tools_archive_plan.md` 属 task-6（声明 scope 仅 `tools`）；T8/T9/T10 三个 scope 内文件无越界 |
| compileall / 导入 | ✅ | `python -m compileall -q .` → exit 0；核心模块导入 **9/9 OK** |

---

## 1. 方法、冻结与完整性

- **被测对象**：commit `4afd0e1`（工作区 git status 对这三个文件为干净态）。脚本开头做完整性断言：**工作区字节 == HEAD 字节**（按行尾归一后逐字节）：`services/launch_queue.py` 工作区 CRLF 1780 处、`services/runner.py` 3335 处、`services/runtime_health.py` 0 处（纯 LF）。
- **改造前基线**：`git show HEAD~1:<path>`（= `78ad828` 的 T2/G3 版本）。
- **只读 git 子命令白名单**：log / show / status --no-optional-locks / diff / ls-files / rev-parse；**未执行** add / commit / checkout / reset（git 归 Lead）。
- 脚本本体留在 `docs/audits/`（我的 write_scope），`.txt` 后缀，不进源码树、不参与编译；为不再污染提交，本轮**不再留任何冻结副本**（上一轮的 snapshot/ 已由 c6c3160 清理，本轮临时目录跑完即删）。

---

## 2. 先更正我上一轮（p1）的两处错误结论

**① “本仓无任何代码给 _finished_callbacks 注册回调” —— 错。**

    main_web.pyw:183  runner._finished_callbacks.append(launch_queue.on_account_finished)
    main_web.pyw:195  runner._finished_callbacks.append(lambda data: _gantt_add(..., "stop"))

根因：p1 我只用 `-Include *.py` / grep include=*.py` 扫描，**漏掉 .pyw 扩展名**（本轮脚本内已加断言，实测 2 个订阅者）。

影响：p1 §6 的“潜在 / 死路径”结论**作废** —— 队列重试路径在真机是**活的**，改造前的双入队是**真实缺陷**（T8 修得必要）。附带事实：main_web.pyw:195 的 gantt 订阅者内部自带 try/except（185-192），真正会把异常抛出来的是 launch_queue.on_account_finished —— 这正是 T8 隔离要防的。

**② “health 还有 4 处未闸 taskkill” —— 数错了：应为 5 处未闸 + 1 处已闸（共 6 处杀进程路径）。**

漏的是 **L221 的 PowerShell Stop-Process**（adb 僵尸命令进程），它不是 taskkill，我按字面只搜了 taskkill。正确清单见 §4.1。

---

## 3. T8 验证（services/launch_queue.py + services/runner.py）

### 3.1 双入队：真实代码差分（不是推演）

驱动器：真实 `LaunchQueue(ctx)`（stub ctx + patch _save_queue / _has_running_process / _tick，避免落盘与真启动），依次执行两个生产者：
① `on_account_finished(("A1", exit, []))`（队列重试，launch_queue.py:350/373/398）→ ② `enqueue("A1", "auto", priority=0, slot=ac["_slot"])`（runner.py:3248）。

| 启动 slot | exit | 改造前 pending | 改造后 pending |
|---|---|---|---|
| fight | 1 / -3 / -8 | [("A1","fight","auto"), ("A1","","retry")] = **2 条** | [("A1","fight","auto")] = **1 条** |
| ""（定时/手动/理智） | 1 / -3 / -8 | 1 条（同键去重吸收） | 1 条 |

结论：改造后无论顺序，同账号 pending 只有 1 条；去重键 =（account_id, slot），修复 = 重试入队带 `_slot = ac.get("_slot","") or ""`（launch_queue.py:349 新增；3 处 enqueue 各加 slot=_slot）—— 与 docs/AUTO_REQUEUE_LIFECYCLE.md:33-35 待拍板项一致。

### 3.2 emit_finished 异常隔离（爆炸订阅者桩）

桩：`_finished_callbacks = [boom, good]`，boom 抛 RuntimeError；调用真实 `AccountRunner.emit_finished`（runner.py:294-306 新实现）后立即模拟 _cleanup 里的 _release_emu_mark。

| | 是否冒泡 | good 订阅者是否收到 | release 是否可达 | warn 留痕 |
|---|---|---|---|---|
| 改造前（runner.py:291） | **是**（RuntimeError） | 否（循环被打断） | **否** | 无 |
| 改造后（runner.py:294-306） | 否 | 是 | **是** | 是（“[回调] finished 订阅者异常（已隔离）…”） |

结论：逐订阅者 try/except 同时满足三件事 —— ① 异常不外泄，_cleanup 中 emit_finished 之后的 release(3222) / 自动重启(3249) / 统计落盘照常；② 订阅者之间互不牵连；③ 有留痕。附带：同文件 3172-3177 把 remove_dispatch 也包了 try/except（p1 指出的另一处裸调），两块合起来补上 R1。

### 3.3 残留（低危，建议后续拍板）

两个生产者现在**同键**，后写的 ② 胜出：new = [("A1","fight","auto")] —— 即 ① 的指数退避 not_before 被 ② 的“立即重跑”覆盖（① 入队前的副作用，如 ≥3 次失败重启模拟器，仍会执行）。这是**旧行为**（slot 为空时一直如此），不是本轮回归；但“两套重试策略并存”只解决了一半，p1 的 P1 建议（单一重入所有者）仍成立。

---

## 4. T9 验证（services/runtime_health.py 全量收口）

### 4.1 六处杀进程路径与闸门（新版本行号）

| # | 路径 | 闸门 | 杀动作 |
|---|---|---|---|
| ① 僵尸 MAA（.pid 活 + 账号不在 active） | L172 if not _kill_gate_on | L178 taskkill /F /PID |
| ② 幽灵 MAA（.pid 活 + .meta 缺失） | L198 | L204 taskkill /F /PID |
| ③ 无主 MAA 系统级扫描 | L247 | L253 taskkill /F /PID |
| ④ 卡死 adb 命令进程 | L288 | **L297-301 PowerShell Stop-Process**（非 taskkill） |
| ⑤ MuMu 崩溃弹窗 | L326 | L332 taskkill /F /IM MuMuNxCrashReporter.exe |
| ⑥ 僵尸 VMM | L353 | L389 taskkill /F /PID |

闸值在 check_health 顶部**只解析一次**（L129 _kill_gate_on = _kill_gate(mw, lq)；实现 L94-108），六处共用；跳过统一走 _gate_skip（L111-117）。

### 4.2 差分结果（同一触发器同时点燃六条路径）

    old gate=False kills=5  issues=[zombie_maa, ghost_maa, ghost_maa, adb_stuck, emulator_crash]
    old gate=True  kills=6  issues=[... 同上 ... , zombie_vmm]
    new gate=False kills=0  issues=[zombie_maa, ghost_maa, ghost_maa, adb_stuck, emulator_crash]
    new gate=True  kills=6  issues=[... 与 old gate=True 完全相同 ...]

断言（全部 PASS）：新闸关 = **0**；新闸开 = **6**；旧闸关 = **5**（改造前暴露面 = 前 5 处，G3 当时只盖了第 6 处）；**闸开时新旧杀进程命令序列逐字相等、counts 相等、issues.detail 逐字相等**（= 闸开行为与改动前一致）；闸开包含 Stop-Process 路径；无账号映射的 VMM（idx9 / pid222）未被杀。

### 4.3 闸关留痕（6 条原文，实测捕获）

    已跳过僵尸 MAA 清理（实例 1 PID=555）（emu_reclaim_enabled=False）
    已跳过幽灵 MAA 清理（实例 2 PID=666）（emu_reclaim_enabled=False）
    已跳过无主 MAA 清理（PID=777）（emu_reclaim_enabled=False）
    已跳过卡死 adb 命令进程清理 ×7（emu_reclaim_enabled=False）
    已跳过模拟器崩溃弹窗清理 ×1（emu_reclaim_enabled=False）
    已跳过僵尸 VMM 清理（emu_reclaim_enabled=False）

最后一条与 G3 文案**逐字一致**（脚本内断言）。闸关时 issues 仍照常上报（category 不变，detail 改为“未清理”），即“只报告不动作”。

### 4.4 _kill_gate 失效方向（新引入的回退分支）

| 场景 | 旧版杀 | 新版杀 |
|---|---|---|
| lq 缺失 + mw.config 无键 | 5 | **0** |
| lq 缺失 + mw.config=True | 5 | **6**（回退生效） |
| lq 缺失 + mw.config=False | 5 | 0 |
| lq.ctx.config=None + mw.config=True | 5 | 6（回退生效） |
| lq.ctx.config={} + mw.config=True | 5 | **0**（主源是 dict → 不回退，缺键=False） |
| lq.ctx.config=True + mw.config={} | 6 | 6（主源优先） |

结论：回退**只在 lq.ctx.config 不是 dict 时触发**；主源存在（哪怕缺键）就 fail-closed。真机上主源恒等于 mw.config：main_web.pyw:136 config = load_config() → :151 传入 ServiceContext、:215 传入 WebContext（同一对象），且 launch_queue = LaunchQueue(ctx)（:181）在 API 起来之前挂好 —— 故该分支实际不可达。与 G3 的唯一差异是“lq 缺失 + 用户已把闸开到 True”时会杀（用户显式开启，符合语义），可接受。

---

## 5. T10 验证（docs/POSITIONING.md 四处回写）

| # | 回写内容（现行号） | 实测核对 | 判定 |
|---|---|---|---|
| D1 | L28 “mas_adapter.py（**168 非空行** / 物理 207 行）” | 实测 nonblank=**168**、total=**207**；同口径 mas_l3 257/307、mas_watch 242/281 | ✅ |
| D2 | L96 “app.mount("/mcp")…api_fastapi.py:**3807**” | Select-String：network/api_fastapi.py:3807 app.mount("/mcp", _mcp_mounted) | ✅（并新增“按符号检索”提示） |
| D3 | L20/L96 “端口可配：脚本传 --port 19999、mjs:65 默认 19998” | tools/maaorch_platform.bat:16 与 tools/maaorch_web_launch.ps1:58 均实传 --port 19999；server/maaorch-web.mjs:65 const PORT = Number(argOf('--port','19998')) | ✅（三处引用全部命中） |
| D4 | L86 “instance_pool.py:203 ensure_maa_instances_async” + 调用点 2082/3083/3149 | def ensure_maa_instances_async 在 **203**；三处调用点确在 api_fastapi.py（2082/3083/3149 是 **import 行**，调用在 2083/3084/3150） | ✅（nit：建议写成行区间 2082-2083 / 3083-3084 / 3149-3150） |

补充核对：文首 L4 新增“行号会漂移、按符号名检索”的口径说明 ✅；旧文本 “3794” / “19999 已被…占用” / “（207 行” / “:216 ensure…” **已全部不存在**（grep 实测无残留）✅；新增的 http://127.0.0.1:19997/mcp 与 models/config.json 的 api_port=19997 一致 ✅。

---

## 6. 越界检查

**本批（4afd0e1）文件清单**：

    M docs/POSITIONING.md                              → T10 scope ✅
    M services/launch_queue.py, services/runner.py      → T8  scope ✅
    M services/runtime_health.py                        → T9  scope ✅
    A docs/audits/2026-09-26_tools_archive_plan.md      → task-6（声明 scope 只有 tools）⚠️

**78ad828..HEAD 全量**：另有 .gitignore 修改、4 个 .tmp_*.json 删除（c6c3160 提交卫生，含清理我 p1 误入提交的 docs/audits/snapshot/**）、以及 docs/audits/*.md（task-5/7 与 task-4 我方，均在各自 scope）。**除 tools_archive_plan.md 外没有 scope 外源码改动。**

**当前工作区**：只剩 ?? docs/audits/_verify_stubs_p2.py.txt（本轮工件）与 ?? docs/audits/2026-09-26_minimal_ops_gate.md（api-curator 的 task-12，声明“只读分析未改代码”，在 docs/audits 内）。另观察到 server/maaorch-web.mjs 曾短暂出现在 git status、复查时 diff 为空（疑似 touch/行尾抖动，非内容改动）—— T8/T9/T10 结论不受影响。

---

## 7. compileall + 核心模块导入

    python -m compileall -q .   → exit 0，无 error
    核心导入 9/9 OK：services.runner, services.launch_queue, services.runtime_health,
    services.mas_adapter, services.mas_l3, services.event_bus, services.instance_pool,
    network.api_fastapi, network.mcp_server

---

## 8. 残留与建议

1. **T8 只解决了一半**（§3.3）：两个生产者同键后由 ② 覆盖 ①，① 的退避延迟不生效。建议按 p1 的 P1 收权（runner 自动重启分支只管“分类 + 挂起”，重入统一交给队列重试），否则退避策略仍是摆设。
2. **T9 的 ①-⑤ 闸关后“只报告不动作”**：长期看会积压（僵尸 MAA / VMM 不再自动清）。若 Harness 想让子系统回收在受控条件下生效，建议显式文档化 emu_reclaim_enabled=True 的运维含义（它同时管模拟器回收 + health 杀进程）。
3. **T10 nit**：D4 调用点建议写行区间（2082-2083 / 3083-3084 / 3149-3150），避免以后 grep 到 import 行却找不到调用。
4. **越界待确认**：docs/audits/2026-09-26_tools_archive_plan.md（task-6 scope 仅 tools）—— 若是 Lead 事后授权把计划文档落到 docs/audits，请在 task-6 上补 scope 记录，保持“scope 之外即越界”这条规则可判。

---

## 9. 局限

- 未对真机执行 check_health（会真的杀本机进程）：六条路径用 patched subprocess.run + patched psutil 触发，覆盖了分支与参数，**未覆盖** MuMuManager info all 的真实输出解析与真实 taskkill 返回码处理。
- 未跑 MCP / HTTP 端到端（需起真实服务 + token）；T10 的端口类事实用 models/config.json 与启动脚本静态核对，未真起服务。
- 差分对比的是两个已提交版本（见修订记录）；若 78ad828 之前还有未提交的中间态，不在本次对比范围。

---

## 10. 修订记录（报告提交后又落了两个提交）

验证脚本最初用相对引用 HEAD~1 / HEAD 取两版代码；随后 Lead 又提交了 4b507cd、24bd955（前端退役第一步，改了 main_web.pyw / server/maaorch-web.mjs），**相对引用随之失效**（再次运行会把改造后代码当成基线，出现 6 条假 FAIL）。已修正为**钉死版本号**：

    docs/audits/_verify_stubs_p2.py.txt :  BASE_REV = "78ad828"（改造前）  CUR_REV = "4afd0e1"（T8/T9/T10）
    可用环境变量覆盖：VV_BASE=... VV_CUR=...

修正后重跑：**36/36 PASS**（含完整性断言：工作区字节 == CUR_REV 字节，按行尾归一）。同时复核 24bd955 对 main_web.pyw 的改动未移动本报告引用的行：main_web.pyw **136 / 151 / 181 / 183 / 195 / 215** 与 §2、§4.4 引用一致（订阅者仍是 183 与 195）。由此本报告的结论在 HEAD=24bd955 时依然成立（launch_queue.py / runner.py / runtime_health.py 自 4afd0e1 起未再改动，git status 实测干净）。
