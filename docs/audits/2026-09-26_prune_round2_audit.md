# 第二轮削减 · 独立验证审计报告

- 审计对象：第二轮代码量削减（A 删 `tools/emu_maa_tool/`；B 删导航实现；C 删 viz_/annotate_/ce6_/bi_/stage_cut 簇；D 清 `services/runner.py` / `services/launch_queue.py` / `main_web.pyw` 死分支死变量）
- 审计员：独立验证员（只读；除本报告外未增删改任何文件）
- 快照时间：**2026-09-26 14:41–14:45 (+08:00)** —— 审计期间第二轮删除**仍在进行中**，本报告对已删文件按实际状态判定，对尚未删除但在名单内的文件**按名称预判**（任务书明确允许，结论同样有效）
- 上一轮回滚锚点：`git tag pre-prune-20260926`（e71c14e）；第一轮成果 = HEAD `15b872e`
- 独立方法（与执行者不同）：
  1. 先用 Node 建 **basename / stem / 点分相对路径** 三键索引 + 全仓词边界扫描（含唯一性消歧，避免 `state.py`/`config.py` 之类同名碰撞）；
  2. 再用 **纯 `ast.parse` 重实现**：解析全部 .py/.pyw 的 `Import` / `ImportFrom`（含 `level>0` 相对导入与 `from X import a, b` 逗号式），并用 byDotted/byFlat 双索引解析模块名；
  3. 对 .py/.mjs/.js/.bat/.ps1/.vbs/.sh 做**引号路径**正则扫描，逐条与 AST 结果交叉；
  4. 对每条引用判定**是否顶层（indent 0）**、**是否在 try 守卫内**，以区分"硬断链"与"静默降级"；
  5. 对 .bat/.ps1/.mjs 里的 python 脚本字符串路径**逐个 Test-Path**。

---

## 一、结论

**总体：不安全（有条件放行）。**

| 项 | 判定 | 说明 |
|---|---|---|
| **A** 删除 `tools/emu_maa_tool/` 整目录 | ✅ **安全** | 全仓（存活文件，含代码/配置/启动器）对 `emu_maa_tool` 的引用**仅存在于 docs/**；无任何代码引用 |
| **B** 删 `tools/` 零调用方导航实现 | ❌ **不安全** | 名单中的 `nav_map.py` / `main_nav.py` / `mt_nav.py` / `fastnav.py` **都有存活调用方**；只有 `ai_navigate.py` + `page_memory.py`（+ `_launch.py` 子链）`learn_map.py` `story_nav_selftest.py` 是真死 |
| **C** 删 viz_/annotate_/ce6_/bi_/stage_cut 簇 | ❌ **不安全（按"簇名"整删会误伤）** | 已删的 8 个 `ce6_*` 安全；但 `viz_plan.py` / `annotate_live.py` / `stage_cut.py` / `ce6_star_count.py` **被存活代码引用**；`bi_*` 是真死可删 |
| **D** 清死分支死变量 | ✅ **安全（无控制流破坏）** | 删除的符号全仓零引用；删掉的 `else` 分支均为**可达但仅打日志**，属"可观测性损失"而非断链 |
| 编译 | ✅ `python -m compileall -q .` → **exit 0，0 error** |
| 核心导入 | ✅ **24/24 OK**；`tools.emu_maa_tool` → `ModuleNotFoundError`（符合预期） |

---

## 二、误伤清单（按严重度分级）

> 判定标准：**存活文件**中的引用。同簇内部互相引用不计（同簇一起删则无害）。

### P0 — 硬断链（模块顶层 import，删除即刻 ImportError）

| # | 拟删文件 | 存活引用方（证据） | 后果 |
|---|---|---|---|
| 1 | `tools/viz_plan.py` | `tools/enemy_timeline.py:42` `import viz_plan as V`（indent 0）<br>`tools/stage_logic.py:35` `import viz_plan as VP`（0）<br>`tools/v1_quality.py:34` `import viz_plan as VP`（0）<br>`tools/job_judge.py:22` `import viz_plan as V`（0）<br>`tools/_axis_check.py:16` `import viz_plan as V`（0） | 5 个模块 import 即崩 |
| 2 | `tools/annotate_live.py` | `tools/battle_sampler.py:57` `import annotate_live as AL`（0）<br>`tools/rogue_cut.py:36` `import annotate_live as AL`（0）<br>`tools/rogue_routes.py:30` `import annotate_live as AL`（0）<br>`tools/annotate_view.py:36` `import annotate_live as AL`（0）<br>`tools/pit_check.py:287`（函数内、**有 try 守卫**） | 4 个模块 import 即崩 |
| 3 | `tools/stage_cut.py` | `tools/stage_arrivals.py:17` `from stage_cut import astar, load_tiles`（0）<br>`tools/stage_plan.py:22` `import stage_cut as sc`（0） | 2 个模块 import 即崩 |
| 4 | `tools/main_nav.py` | `tools/panel_gate.py:56` `from main_nav import reward_has_prime, sanity`（indent 0）<br>且 `panel_gate.py` 自身被 `tools/event_sweep.mjs:395` 以子进程调用（存活） | **存活链路**：event_sweep → panel_gate → main_nav |
| 5 | `tools/mt_nav.py` | `tools/enter_event.py:29` `from mt_nav import ADB, DEV, hits_of, shot, tap`（indent 0）<br>`tools/event_sweep.mjs:324`、`:357` `run(PY, ['tools/mt_nav.py', ...])` | 硬断链 + 横扫导航主路径失效 |
| 6 | `tools/ui_layout.py` | `tools/annotate_live.py:92 / 426 / 604 / 614`、`tools/annotate_view.py:179 / 253` `import ui_layout` | 若 `annotate_*` 保留则崩（与 #2 联动） |

### P1 — 子进程字符串调用（删后功能失效；部分可优雅退化）

| # | 拟删文件 | 存活引用方（证据） | 后果 |
|---|---|---|---|
| 7 | `tools/nav_map.py` | **`tools/event_sweep.mjs:314`**<br>`const fast = await run(PY, ['tools/nav_map.py', '--stage', code, '--json'],`<br>`{ timeoutMs: 120000, label: \`nav_map \${code}\` })` | 坐标导航**快路径**（1.1–1.3s）失效，退化到 OCR 的 mt_nav（4.1s）。非崩溃，但**"零调用方"判定为假** |
| 8 | `tools/tap.py` | `tools/event_sweep.mjs:588` `await run(PY, ['tools/tap.py', String(x), ...])` | 点击链路断 |
| 9 | `tools/enter_by_template.py` | `tools/event_sweep.mjs:2132` `run(PY, ['tools/enter_by_template.py', '--event', slug, ...])` | 活动关进图断 |
| 10 | `tools/stage_attempt.py` | `tools/event_sweep.mjs:1026 / 1040 / 1060` `run(PY, ['tools/stage_attempt.py', '--leave-battle' \| '--read-tickets' \| '--job', ...])` | 收尾/读数断 |
| 11 | `tools/main_nav.py` | `tools/prime_sweep.mjs:138` `run(PY, ['tools/main_nav.py', '--code', u.code, '--quiet', ...])` | 与 #4 同源，双重证据 |

### P2 — 守卫式 import（删后静默降级，功能悄悄丢失）

| # | 拟删文件 | 存活引用方（证据） | 后果 |
|---|---|---|---|
| 12 | `tools/ce6_star_count.py` | `tools/stage_attempt.py:1163` `from ce6_star_count import page_kind`（try 内）<br>`tools/stage_attempt.py:1474` `from ce6_star_count import page_kind, star_blobs`（try 内） | 不报错，但**「开局前画面类型自检」永久静默失效**（except 分支只打「界面自检跳过」）。`stage_attempt.py` 是存活脚本（被 event_sweep 调用），故**不满足"零引用"** |
| 13 | `tools/fastnav.py` | `tools/enter_event.py:142`、`:217` `import fastnav`（函数内、无守卫）<br>`tools/maaorch_service.py:739` `import fastnav` | 运行到该分支即 ImportError |

> **方法学警告（本轮最大风险点）**：#1/#2/#3/#12 这些引用全部是 **裸模块名导入**（`import viz_plan`、`from ce6_star_count import ...`），**按文件名/路径扫描完全看不见**。第一轮提交信息已自述过同类缺陷（"from . import a, b, c 逗号式导入未被正则识别"）。本轮若沿用"按文件名扫引用"的方法，必然重犯。

---

## 三、独立复核：确认为"真零引用"、可安全删除

| 文件 | 复核证据 |
|---|---|
| `tools/bi_goto.py` | 仅被 `tools/bi_home.py:89` 以 `subprocess ... "bi_goto.py"` 调用（同簇）；`tools/stage_attempt.py:1257` 只是打印提示串 |
| `tools/bi_home.py` | 存活代码中**只在注释**出现：`tools/event_sweep.mjs:11`、`:305` |
| `tools/bi_state.py` | 同上：`tools/event_sweep.mjs:12`、`:305`；`tools/mt_nav.py:5`（注释）；真实调用方只有 `bi_goto.py:70`（同簇） |
| `tools/bi_back_to_stage.py` | 零引用（仅 `bi_goto.py` 同簇提及） |
| `tools/annotate_view.py` | 外部零调用方（它自己 import `annotate_live`） |
| `tools/ai_navigate.py` + `tools/page_memory.py` + `tools/_launch.py` | 唯一引用链 `_launch.py:62 → ai_navigate.py:46 → page_memory.py`；而 `_launch.py` **无真实调用方**（`tools/orchestrator.py:21` 只在 docstring 提及；`orchestrator.py` 本身也零真实调用）→ **整条子链可一起删** |
| `tools/learn_map.py` | 存活代码中只在注释：`tools/maaorch_service.py:932`、`tools/scan_map_panels.py:318` |
| `tools/story_nav_selftest.py` | 零引用 |
| `tools/ce6_battle_audit.py` ✅已删 / `ce6_candidates.py` ✅ / `ce6_deploy_probe.py` ✅ / `ce6_enemy_plan.py` ✅ / `ce6_star_row.py` ✅ / `ce6_timeline.py` ✅ / `ce6_until.py` ✅ | 复核通过：存活代码零引用，仅 docs 提及 |
| `tools/ce6_attempt.py` / `tools/ce6_cut_check.py` / `tools/ce6_map_verify.py` / `tools/ce6_spawn_order.py` | 存活代码引用仅来自同簇（`ce6_until.py:66`、`ce6_deploy_probe.py`、`stage_cut.py`）；**须与整个 ce6 簇同批删除**，不可只删其一 |
| `tools/bridge.py` | 全仓唯一命中是 `infrastructure/maa_core.py:1` docstring 里的英文 `"ctypes bridge"` → 非引用。✅ 可删（与第一轮审计结论一致） |
| `tools/axis_oper_census.py` ✅已删 | 仅 `docs/audits/20260924_2012_oper_gate.md` 提及 |

---

## 四、高危面复核（逐一确认存在且未被本轮修改）

| 面 | 状态 |
|---|---|
| `main_web.pyw` | ✅ 存在；**被 D 有意修改**（-1 行） |
| `services/` | ✅ 存在（含 runner.py / launch_queue.py，**被 D 有意修改**） |
| `network/` | ✅ 存在，4 文件，**未修改** |
| `models/` | ✅ 存在，20 文件，**未修改** |
| `infrastructure/` | ✅ 存在，9 文件，**未修改** |
| `app/` | ✅ 存在，4 文件，**未修改** |
| `ui/web/` | ✅ 存在，9 文件，**未修改** |
| `tools/push/**` | ✅ 存在，8 文件，**未修改** |
| `ui/control_server.py` | ✅ 存在，**未修改** |
| `tools/gen_push_ui.py` | ✅ 存在，**未修改** |
| `tools/gen_mini_ui.py` | ✅ 存在，**未修改** |
| `tools/lib/*.mjs` | ✅ 存在，3 文件（`rogue_judge.mjs` / `rogue_plan.mjs` / `rogue_plan.mjs.bak-mas-1814`），**未修改** |
| `tools/rogue_plan*.mjs` | ✅ `rogue_plan_selftest.mjs` 及其 .bak 存在，**未修改** |
| `tools/rogue_acceptance.mjs` / `rogue_report.mjs` / `rogue_watch.mjs` | ✅ 存在，**未修改** |

`git status --porcelain` 在上述路径范围内**只有 D 的 3 个文件**为 M，其余全部干净。
ROGUE 证据链（`tools/lib/*.mjs` + `tools/rogue_plan*.mjs` + `rogue_acceptance/report/watch`）**完整无损**。

---

## 五、外部调用面：.bat / .ps1 / .mjs 里的 python 脚本路径（逐个 Test-Path）

扫描范围：全部 `.bat/.ps1/.cmd/.vbs/.mjs/.js/.sh`，提取所有 `*.py/*.pyw` 字符串路径 → **34 条**。

### 5.1 真实调用点（27 条，**全部存在 ✅**）

| 被测路径 | 调用方 |
|---|---|
| `network/api_fastapi.py` | `server/maaorch-web.mjs` |
| `network/copilot_api.py` | `server/copilot_node.mjs`, `server/maaorch-web.mjs` |
| `services/mas_l3.py` | `ui/web/auto_mas.js` |
| `runtime/_audit_settle_fixtures.py` | `tools/event_sweep.mjs` |
| `runtime/_prime_queue3.py` | `tools/event_sweep.mjs`, `tools/run_campaign.ps1` |
| `runtime/_rank_refresh.py` | `tools/run_campaign.ps1` |
| `runtime/plans/_axis_weights.py` | `tools/event_sweep.mjs` |
| `tools/auto_mas_export.py` | `tools/导出账号信息.bat` |
| `tools/auto_mas_rerun.py` | `server/maaorch-web.mjs` |
| `tools/auto_mas_toggle_user.py` | `server/maaorch-web.mjs` |
| `tools/axis_rank.py` | `tools/event_sweep.mjs` |
| `tools/bi_home.py` | `tools/event_sweep.mjs`（**仅注释**，见 §二/§三） |
| `tools/bi_state.py` | `tools/event_sweep.mjs`（**仅注释**） |
| `tools/enter_by_template.py` | `tools/event_sweep.mjs` ⚠️ 真实调用 |
| `tools/look.py` | `tools/event_sweep.mjs`（**仅注释** `:146`） |
| `tools/maa_job_fallback.py` | `server/copilot_node.mjs`, `tools/event_sweep.mjs`, `tools/prime_sweep.mjs` |
| `tools/maaorch_service.py` | `tools/event_sweep.mjs`, `tools/run_campaign.ps1` |
| `tools/main_nav.py` | `tools/prime_sweep.mjs` ⚠️ 真实调用 |
| `tools/mt_nav.py` | `tools/event_sweep.mjs` ⚠️ 真实调用 |
| `tools/nav_map.py` | `tools/event_sweep.mjs` ⚠️ 真实调用 |
| `tools/panel_gate.py` | `tools/event_sweep.mjs` |
| `tools/pending_reverify.py` | `tools/event_sweep.mjs` |
| `tools/plan_mines.py` | `tools/rank_service.mjs` |
| `tools/prts_job_finder.py` | `server/copilot_node.mjs`, `tools/event_sweep.mjs`, `tools/prime_sweep.mjs` |
| `tools/stage_attempt.py` | `tools/event_sweep.mjs` ⚠️ 真实调用 |
| `tools/story_skip.py` | `tools/event_sweep.mjs` |
| `tools/tap.py` | `tools/event_sweep.mjs` ⚠️ 真实调用 |

**结论：无一条因本轮删除而失效（27/27 Test-Path = True）。** ⚠️ 标记者为本轮名单内的潜在误伤对象。

### 5.2 7 条"未命中"逐个裁定 — 全为正则误报

| 匹配串 | 命中处 | 裁定 |
|---|---|---|
| `////mas_dashboard.py` / `//mas_dashboard.py` / `HERE/mas_dashboard.py` | `tools/mas_dash.sh` | shell 变量拼接（`$HERE`），**非静态路径**；实际 `tools/mas_dashboard.py` 存在 |
| `//raw.githubusercontent.com/.../manager/manager.py` | `manager/deploy.ps1` | URL，非本地路径 |
| `/MAAOrch-Manager/manager.py` | `manager/install.bat` | 远端路径 |
| `dir/manager.py` | `manager/deploy.ps1` | 变量拼接 |
| **`tools/train_ranker.py`** | `tools/rank_service.mjs:130,132` | **文件确实不存在** —— 但这是**历史遗留悬空引用**（仅注释 + 报错文案），**非本轮引入**。建议单独登记，不在本轮处置 |

---

## 六、D 改动审查结论（`git diff` 逐条）

### 6.1 `main_web.pyw`（-1 行）
```diff
-    token = config.get("api_token", "")
```
- 删除的是 `main()` 内的**局部变量**。全文（390 行）复扫 `\btoken\b` → **0 命中**，确为死变量。✅
- 鉴权并未削弱：`network/api_fastapi.py:198` 自行读取 `mw.config.get("api_token", "")`，`network/mcp_server.py:28/60` 同理，**不依赖 main_web.pyw 的局部变量**。✅
- **结论：无行为变化。**

### 6.2 `services/launch_queue.py`（-13 行）
- 删除方法 `on_maa_task_finished(self, account_id, status)`。
- 全仓扫描 `on_maa_task_finished` → **0 命中（定义处即唯一出现）**。✅
- **结论：不可达死方法，无行为变化。** 注：其 `consecutive_failures` 自增逻辑已无入口，若该字段仍被别处读取，属既有语义缺失（非本轮引入，本轮只是不再有"永远不会发生的"写入）。

### 6.3 `services/runner.py`（-23 行）
删除项分两类：

**(a) 死回调基础设施 —— ✅ 无行为变化**
- `self._status_msg_callbacks`、`self._error_callbacks`（`__init__`）
- `emit_status()`、`emit_error()`
- 调用点 `self.emit_error(aid, str(e))`（启动失败分支）
- 全仓扫描 `emit_status` / `emit_error` / `_status_msg_callbacks` / `_error_callbacks` → **0 命中**。
- 即：**从来没有订阅者**，`emit_error` 调用是空转。删除后启动失败路径仍保留 `self._log.error(...)` + `self.emit_log(...)` + `self._cleanup(aid, -1, [])`，**日志与清理不受影响**。✅

**(b) 7 处 `else: self._log.info("[守护审计] ... 暂缓（待确认）")` —— ⚠️ 可达，但仅打日志**
- 位置：模拟器失联、心跳断杀、PRTS 卡死、任务卡死、运行超时、连续异常清理、启动无任务、StartUp 卡加载（共 8 处）。
- **这些 `else` 分支不是不可达**：当 `self._guard_kill(...)` 返回 `False` 时它们确实会执行。
- 但其**分支体只有一行 `self._log.info`**，无副作用、无 return、无状态写入；删除后控制流完全一致（`if ...: ...; return` 之后本就继续往下走），后续的 `self._emu_lost_suspect[aid] = time.time()` 等语句缩进与绑定未变（已用 `compileall` + AST 逐块核对）。
- **结论：不是"不可达分支"，而是"可达但零副作用的日志分支"**。删除后功能等价，但**丢失了"守卫暂缓/待确认"的可观测性**——排障时无法再从日志区分"守卫放行"与"根本没触发"。**建议 Lead 知悉并接受该可观测性损失**，或改为 `self._log.debug` 保留。

**D 总评：✅ 未引入控制流破坏，删的都是死符号/死变量/零副作用日志分支。**

---

## 七、运行验证

| 检查 | 结果 |
|---|---|
| `python -m compileall -q .` | **exit 0，无任何 error 输出**（在最新删除落盘后复跑仍然 0） |
| 核心模块导入 | **24/24 OK**：`services.runner`, `services.launch_queue`, `network.api_fastapi`, `models.stats`, `infrastructure.maa_core`, `app.service_context`, `app.web_context`, `ui.control_server`, `tools.state`, `tools.runs`, `tools.aggregate`, `tools.pages`, `tools.screen_state`, `tools.stage_attempt`, `tools.mt_nav`, `tools.main_nav`, `tools.nav_map`, `tools.viz_plan`, `tools.annotate_live`, `tools.stage_cut`, `tools.ce6_star_count`, `tools.panel_gate`, `tools.enter_event`, `tools.tap` |
| `tools.emu_maa_tool` 已删后的导入 | `tools.emu_maa_tool` / `...server` / `...server.main` → **`ModuleNotFoundError`（预期，正是 A 的目标）** |
| AST 语法解析 | 1 处**既有**告警：`infrastructure/utils.py:1` 含 U+FEFF BOM，`ast.parse` 报 invalid non-printable character（Python 解释器用 `utf-8-sig` 读取无碍，`compileall` 通过）。**非本轮引入**，建议顺手清理 |

---

## 八、最终数字

统计口径：`.py` + `.pyw`；**排除** `runtime/` `data/` `debug/` `logs/` `services/maa/` `tools/emu_maa_tool/maa_instances/` `ui/web/vendor/` `__pycache__/`。

| 时点 | 文件数 | 总行数 |
|---|---|---|
| 回滚锚点 `pre-prune-20260926`（第一轮前） | — | 94,391（第一轮提交信息自述） |
| HEAD `15b872e`（第一轮后，本审计用同口径复算） | 258 | **72,009** |
| **当前工作区（第二轮进行中，2026-09-26 14:45）** | **239** | **69,516** |

第二轮至今净变化：**-19 文件 / -2,493 行**（相对 HEAD）。
相对第一轮前：**-24,875 行（-26.4%）**。

按目录分布（当前，行）：

| 目录 | 行数 |
|---|---|
| tools | 42,422 |
| services | 18,153 |
| network | 4,183 |
| infrastructure | 1,199 |
| （仓库根） | 986 |
| manager | 880 |
| models | 832 |
| ui | 778 |
| app | 83 |

本轮已实际删除 31 个路径（含 8 个 `ce6_*.py`、`axis_oper_census.py`、`bridge.py` 与整个 `tools/emu_maa_tool/` 剩余部分），修改 3 个文件（`main_web.pyw`、`services/launch_queue.py`、`services/runner.py`）。
对该 31 个已删路径做**强证据（basename + 相对路径 + 裸模块导入）反扫**：**存活代码零引用**（`app.py`/`state.py`/`index.html` 的若干命中经逐条人工裁定均为同名碰撞误报，例如 `tools/batch_run.py` 里的 `state.py` 实指 `tools/state.py`）。✅

---

## 九、给 Lead 的处置建议

1. **【阻断】不要把 `viz_plan.py` / `annotate_live.py` / `stage_cut.py` / `ui_layout.py` 当"零引用"删除。**
   它们各自被 2–15 个存活模块**顶层 import**，删了就是 ImportError。若确要砍，必须连同其全部引用方（`enemy_timeline` / `stage_logic` / `v1_quality` / `job_judge` / `_axis_check` / `battle_sampler` / `rogue_cut` / `rogue_routes` / `stage_arrivals` / `stage_plan` …）作为**一个闭包整体**评审，并确认这些引用方本身也不在存活链上（`stage_plan`/`stage_arrivals` 在 C 簇描述之外，目前看是活的）。

2. **【阻断】导航簇只可删真死的三个子链：**
   - `ai_navigate.py` + `page_memory.py`（+ `_launch.py`）—— 唯一入口 `_launch.py` 无真实调用方；
   - `learn_map.py`、`story_nav_selftest.py` —— 仅注释提及；
   - **`nav_map.py` / `main_nav.py` / `mt_nav.py` / `fastnav.py` 必须保留**：分别是 `event_sweep.mjs:314`、`prime_sweep.mjs:138` + `panel_gate.py:56`、`event_sweep.mjs:324/357` + `enter_event.py:29`、`enter_event.py:142` + `maaorch_service.py:739` 的活跃依赖。

3. **【建议】`ce6_star_count.py` 要么保留，要么与 `stage_attempt.py` 内的两处 import 一并移除。**
   当前删除只会让存活脚本 `stage_attempt.py` 的"开局前画面自检"静默失效（except 吞掉），属隐性功能回归。

4. **【放行】`bi_*` 簇（`bi_goto`/`bi_home`/`bi_state`/`bi_back_to_stage`）可删。**
   存活代码里对 `bi_home`/`bi_state` 的引用**全是注释**（event_sweep.mjs:11/12/305 明确写着 2026-09-24 已改用 `nav_map`+`mt_nav`），`bi_goto` 只被同簇 `bi_home.py:89` 调用。**建议同批删除**，否则会留下 `bi_home` 调不存在的 `bi_goto`。

5. **【放行】A 整目录已删且安全**；已删的 8 个 `ce6_*` 亦安全。剩 `ce6_attempt` / `ce6_cut_check` / `ce6_map_verify` / `ce6_spawn_order` 必须**成组同删**（互引），且删除前再确认 `stage_cut.py` 是否留（`ce6_cut_check` 被 `stage_cut.py` 引用）。

6. **【改进】修正引用扫描方法。** 本轮最大系统性风险是"按文件名扫引用"看不见**裸模块导入**。建议把验证脚本固化为：`ast.parse` 求 Import/ImportFrom 闭包（含 `level>0` 与逗号式）→ 叠加引号路径正则 → 再做"被字符串救回"的不动点闭包，与第一轮提交信息描述的方法对齐。

7. **【登记】非本轮问题**：`tools/rank_service.mjs:130,132` 引用的 `tools/train_ranker.py` 不存在；`infrastructure/utils.py:1` 有 BOM。建议单独立项处理。

8. **【流程】审计期间工作区仍在变动**（14:36 起持续有 `D` 落地）。本报告为 **14:45 快照**。建议 Lead 在所有删除落盘并 `git commit` 后，让验证员在**冻结快照**上复跑一次（命令与口径见本报告 §七/§八），再做最终放行。

---

*本报告由独立验证员生成；除本文件外未对仓库做任何增删改。*
