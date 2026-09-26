# 选配作业（推关）线勘定 —— 现状 · 数据模型 · 人机入口 · 最小可用路径

> 任务：task-19（node-repointer）。**只读分析，未改任何代码**；未执行任何 git 命令。
> 日期：2026-09-26。定位前提：MAAOrch = Harness/MAA/MAS 之间的过渡层/粘合剂；前端整体退役，Node 只留最小运维页 `/status`；保留三块能力 = 调配核心、**选配作业(推关)**、地图编辑/标注。
> 所有结论带 `文件:行号`；所有「实测」都是本次真跑出来的，命令见 §6。

## 0. 一句话结论

**这条线的后端是活的，人机入口是断的。**

- 执行链完整且今天仍可用：`prts 作业站(HTTP) → 筛选(缺口/练度) → 本地作业文件 → MaaCore 跑 → 判 verdict → 截图核三星 → 落盘报告`；三个纯离线脚本 `job_dp_check.py` / `job_fit.py` / `job_diff.py` **本次实测 exit 0**，作业站 `prts.maa.plus` **实测 200（约 1~2 秒）**。
- 但：① 唯一的图形入口 `ui/web/copilot_lab.js` 在退役清单里；② Python 侧 `/api/copilot/*` **从未注册**（`register()` 只写在没人调用的 `start_server` 里）；③ Node 门面把 5 个端点**原生重写**了（活的），但 **`/status` 页面里没有任何入口**；④ 41 个 MCP 工具里 **0 个**与作业/copilot/prts 相关。
- 所以「用户明确要保留」的这条能力，今天只有**会敲命令行的人**能用。§4 给三个人机入口方案与推荐（推荐 **C：/status 加一块** 近期落地 + **B：CLI 保底**；**A：收进 MCP** 排在 G3/G4 之后，并附 A 的一个硬前置）。

## 1. 这条线现在能跑到哪一步（逐脚本判定）

判定图例：✅ 离线可独立跑通（本次实测 exit 0）｜🌐 依赖外部网络（prts.maa.plus）｜📦 依赖干员箱 box｜🎮 依赖模拟器在线｜⚠️ 能跑但有硬编码，泛用性受限。

| 脚本（行数） | 干什么 | 输入 | 输出 | 硬依赖 | 判定 |
|---|---|---|---|---|---|
| `tools/prts_job_finder.py` (521) | **主线筛选器**：按 stage_name 拉候选作业，对 box 求缺人/练度缺口，排序出首选 | `--stages actXXside_exYY`（或 `--display`）+ `--operbox box.json`；`--top/--full` | `<out>.json`（候选数组）、`<out>.md`（推荐表）、`<out>_malist.json`（MAA 多作业 TaskList 片段）、`--dump-dir` 落首选作业正文 | 🌐📦 | 未实跑（会写缓存，见 §6 纪律）；`--help` exit 0；契约由 Node `screen()` 与 `data/copilot/screening/*` 产物双向印证 |
| `tools/job_fit.py` (166) | 把「作业要求」与某个号的 box 对一遍（缺人/组空/空壳/非标准字段） | `--uid` + `--stage EX-N` | stdout 排序表 | 📦 | ✅ 实测 exit 0；**关卡前缀写死 `act54side`**（`job_fit.py:147-148`）⇒ 只适用于 event54 那一期 |
| `tools/job_judge.py` (231) | 判漏三分类（确定漏/守得住/存疑）：射程表+费用+伤害模型 | **无 CLI**；stage 写死在函数默认值 | stdout 分类统计 | 本地 5 个模型模块 `viz_plan/enemy_speed/cost_model/combat_model/duel_model`（均存在） | ⚠️ 能跑：`--help` 被忽略、直接跑完整分析（**54.5 s，输出「阶段 act16side_ex02 ｜ 作业 33 份 ｜ 条目 792」**）。要换关只能改文件或 `import job_judge; job_judge.judge_full(stage=...)`（`job_judge.py:31, 229-231`） |
| `tools/job_diff.py` (285) | 两份作业的结构化差异（step/type/from/to）= 标注信号 | 两个作业 json，或 `--stage` + `--a/--b` | stdout / `--json` / `-o` 文件 | 无（纯本地） | ✅ 实测 exit 0（两份 `BI-EX-6_cm_v*.json` → 「21 处差异」） |
| `tools/job_dp_check.py` (108) | 费用可行性：按回费曲线模拟每个 Deploy 是否付得起 | `<job.json> --stage --raid [--kill-dp]` | stdout 逐条 ✓/✗ + 结论 | 写死 `data/_character_table_full.json`、`data/_skill_table.json`、`data/operbox_batch/emu2_9036_flat.json`（均存在） | ✅ 实测 exit 0（「全部付得起 ✓」）；box 写死 9036 ⇒ 对别的号只是近似 |
| `tools/pick_copilot.py` (179) | 早期单关筛选器（核精英化/等级），可 `--dump` 作业正文 | `--display 15-14` 或 `--stage main_15-13 [--hard]` | stdout / dump 文件 | 🌐📦 | ⚠️ **干员箱与 battle_data 是写死常量**（`pick_copilot.py:29-30`，box=`data/operbox_emu0914_2301.json`、battle_data 指向某个 MAA 版本的 resource）⇒ 路径一变就静默用错箱 |
| `tools/prts_event_pick.py` (419) | **精英化感知**筛选器：一次挑完整个活动（普通+突袭），输出字段与 fallback 约定一致 | `--event act54side`（自动展开）或 `--stages` + `--operbox` | 报告 json / dump 目录 | 🌐📦 | 未实跑；`--help` exit 0；按 docstring（`prts_event_pick.py:11-19`）它是 finder 的**改进版**（finder 只看有无人，会把精0Lv1 判成能跑） |
| `tools/maa_job_fallback.py` (251) | **全链路执行器**：按筛选结果排序，一份跑不了就换下一份直到三星 | `--stage --screening <json>` 或 `--codes` 或 `--local` + `--emu/--instance/--touch/--max-tries` | `--out data/copilot/fallback_report.json`（tries+result）、`logs/fallback_*.log`；拉下来的正文落 `data/copilot/fallback_jobs/job_<id>.json`（`maa_job_fallback.py:54-68`） | 🌐🎮 + `services/maa/instances/<n>/{Python/asst,MaaCore.dll}`（存在）+ `find_adb/detect_emu_adb`（`maa_job_fallback.py:40,73-75`）+ asst.log 关键字判定（`:45-51,111-126`）+ 核三星要截图与视觉模型（`:129-148`，`cv2` + `services.ai_agent`，兜底还会调 `vision_locate` ⇒ 依赖 AI key） | 未实跑（会动模拟器与 MAA，本次刻意不跑）；依赖齐全 |
| `tools/run_job.py` (286) | 极简执行器第③步：人先把游戏停在关卡详情页，脚本只负责起 MAA 跑指定作业 | `--emu --job [--ignore-requirements]` | 退出码/用时；可回写 `push_state_<uid>.json`（`:130-144`） | 🎮 + adb `exec-out screencap` + OCR（`run_job.py:28-29,57-84`）+ `infrastructure.ocr` | 未实跑（**有副作用**：按端口清孤儿 MAA，`:7-11`）；依赖齐全 |
| `tools/own_axis_job.py` (132) | 把「我们自己算的落点」生成 MAA copilot 作业（不抄作业站） | `--plan plans/*.json --box` | 作业 json（`--out`） | 📦 + Tile-Pos（`own_axis_job.py:31-34`；`D:/Xiach/M-A/MAA/MAA-v6.12.2-win-x64-1/resource/Arknights-Tile-Pos` 与 `services/maa/v6.11.1/resource/Arknights-Tile-Pos` 均存在）；`plans/` 33 份存在 | `--help` exit 0；未被任何自动化调用（纯人工生成环） |
| `tools/enter_event.py` (406) | 从大厅一路点到活动关卡地图（可切 EX 区） | `--name 尘影余音 --prefix LE [--zone-ex]` | stdout 最后一行 JSON | 🎮（`from mt_nav import ADB, DEV, hits_of, shot, tap`，`enter_event.py:29`） | 未实跑（纯点屏，需模拟器在线） |
| 附带：`list_jobs.py` (96) ✅📦 极简执行器第②步排序；`maa_copilot_run.py` (136) 🎮 另一条跑作业的路；`job_meta.py` (19) 作业元数据单一来源（库）；`job_add_skill.py` (55) 给生成作业补 `skill/skill_usage` |

**判定汇总**：11 个主脚本 `--help` **全部 exit 0**（能 import、CLI 正常）。真正的「已损坏」一个都没有；坏的是**泛用性与入口**：3 处硬编码（`job_fit` 前缀、`pick_copilot` 常量、`job_judge` 无 CLI）、1 处契约缺口（`potion` 传不进去，见 §3）、1 处入口消失（`copilot_lab.js` 退役）。

## 2. data/copilot/ 的数据模型

体量（本次实测）：顶层 **358** 个文件；含子目录共 **971** 个文件（**912** json / 56 md / 2 txt / 1 log）；**26** 个子目录。
按**角色**分三层：

### 2.1 输入：作业正文（从作业站拉下来的 MAA copilot json）
- 结构：`stage_name / minimum_required / doc{title,details} / opers[] / groups[] / actions[]`（实测样本 `data/copilot/event54_picked/act54side_01_101884.json`）。
- 典型位置：`event54_picked/`(20)、`event54_751737728/`(36)、`event54_394162955/`(20)、`event54_b/`(29)、`event54_d/`(29)、`event54_user/`(17)、`event54_prts/`(8)、`ga16_picked/`(18)、`sr54_picked/`(19)、`fallback_jobs/job_<id>.json`(118，`maa_job_fallback.fetch_job` 拉的)。
- 命名：`<stage_name>_<作业号>.json`。**作业号是作业站 id**（可拼 `prts://<id>`）。

### 2.2 中间产物：我们自己派生/改过的作业与筛选输出
- 派生作业：顶层 `BI-EX-6_cm_v*.json`（46）、`BI-EX-6_v*.json`（104）、`CE-6_ours_v*.json`（32）、`MT-EX-*`（约 45）…… 实测最新到 `BI-EX-6_cm_v62.json`。这些是 `own_axis_job.py` 生成 + `job_add_skill.py` 补技能 + 人手改的**版本序列**（`*_ours_*`/`*_cm_*`/`*_突袭_*` 可区分来源）。
- 筛选输出：`data/copilot/screening/<stage_name>.json`（**149** 文件）= `prts_job_finder` 的候选数组（字段 `id/rating/like/views/title/missing/under/requirements/used/warn/actions/can_run/clean/difficulty`，实测样本 `screening/act42side_ex06.json`）；同名 `.md` 是推荐表；`<stage>_malist.json` 是 MAA 多作业片段（实测 236 B，`FilePath` 指向 `data/copilot/picked/act42side_ex06_52811.json`）。
- 别的实验目录：`event54/`、`sr_ex/`、`ga_derived/`、`ga16_adapted/`、`sr54_adapted/`、`ex2_jobs/`、`local/`、`picked/`、`act51side_01/`、`act16side_ex02/` —— 是历次活动的**工作集快照**（输入+派生的混合）。

### 2.3 状态：会随运行变化（**别当输入用**）
| 文件/目录 | 是什么 | 谁写 | 实测证据 |
|---|---|---|---|
| `data/copilot/_get_cache.json` | 作业站 HTTP 缓存（652 KB） | `prts_job_finder.py:274-277 cached_get` | mtime **2026-09-26 01:48**（全目录最新 ⇒ 最近一次跑过筛选） |
| `data/copilot/screening/` | 筛选结果缓存，**TTL 6 小时**（Node `copilot_node.mjs:40,90-96`；Python `copilot_api.py:48`） | Node/Python screen | 149 文件，最新 `act42side_ex06.*` @ 09-26 01:48 |
| `data/copilot/fallback_report.json` | **最近一次降级重试的报告** | `maa_job_fallback.py --out` | `{stage: act42side_ex06, tries:[{id:52811, verdict:done, three_star:true}], result:"成功 prts://52811"}`，mtime **09-22 05:25** |
| `logs/fallback_*.log` | 每次重试的完整日志（Node 版用 `-u` 无缓冲写） | `copilot_node.mjs:128-142` | 2 份，最新 `fallback_0921212046..log` @ 09-22 05:25（文件名多一个点，见 §7-5） |
| `data/copilot/sweep_reports/` (43) | 横扫跑局报告（`*__普通/突袭.json`） | event_sweep 侧 | — |
| `data/copilot/picked/` (2) | `--dump-dir` 落的首选作业正文（给 MAA 本地加载） | finder/Node screen | `act42side_ex06_52811.json` |

**判据**：文件名里带日期/时间戳、或顶层含 `tries/result/provider cache` 的是状态；`stage_name + actions[]` 的是输入。

## 3. Python `/api/copilot/*` 未注册的实际后果 + Node 覆盖情况

### 3.1 事实（代码级）
- `copilot_api.register(app)` 的唯一调用点在 `network/api_fastapi.py:3843-3849`（`def start_server`，3839 行开始）；而 `create_app`（`:187`）内部**从不**调用它。
- `start_server` **全仓无调用方**：`main_web.pyw:329` 只 `from network.api_fastapi import create_app, start_server`（**导入了但没用**），真正走的是 `main_web.pyw:331 _fastapi_app = create_app(ctx._mw)`。
- `/api/copilot/*` 在 `network/api_fastapi.py` 里只有那 3 处（3846/3847/3849），没有任何 `@app.get("/api/copilot/...")`。
- FastAPI 的兜底 `@app.get("/{full_path:path}")` 对 `api/` 前缀直接 `HTTPException(404)`（`:3824-3827`）。

### 3.2 实际后果
**走正式入口（main_web.pyw）时，Python 侧 `/api/copilot/*` 一律 404**（GET 命中兜底 404；POST 无匹配路由同样 404）。历史注释里说的「这四条路由实测 503」是「Node 没实现 + 上游没起」的**另一个**现象（`server/copilot_node.mjs:4-8`）。
⇒ 任何**默认走 HTTP 自调用**的调用方都拿不到这四条：典型就是 MCP —— `network/mcp_server.py:40-67 _call_api()` 打的是 `http://127.0.0.1:{config.api_port}`（=19997，就是 create_app 那个 app）。**这是 §4 方案 A 的硬前置。**

### 3.3 Node 原生实现覆盖了哪几条（活的）
`server/copilot_node.mjs`（228 行）实现了 **5 个端点**，契约与 Python 版一致：
| 端点 | Node 实现 | 做什么 | Python 对应 |
|---|---|---|---|
| `GET /api/copilot/info` | `copilot_node.mjs:182-185,219-228` | 自证：python 路径、finder/runner 是否存在、最新 box、是否在跑 | Python **无**（Node 新增） |
| `GET /api/copilot/screen?stage=&top=&refresh=` | `:186-196,85-108` | 起 `prts_job_finder.py`，读/写 `data/copilot/screening/<stage>.json` | `copilot_api.py:117-122` |
| `GET /api/copilot/fallback/status` | `:197-199,151-169` | 进程是否在跑 + 日志尾部 + `fallback_report.json` | `:135-137` |
| `POST /api/copilot/fallback` | `:204-211,111-148` | 起 `maa_job_fallback.py`（独立进程、日志落盘、单例） | `:124-133` |
| `POST /api/copilot/fallback/stop` | `:212-214,171-179` | `taskkill /PID <pid> /T /F` | `:139-141` |
接线：`server/maaorch-web.mjs:26-27`（import）、`:979-984`（GET）、`:1015-1018`（POST）。**实测**：2026-09-26 起一次 Node（19996，`--no-upstream`），`GET /api/copilot/info` → **200**，返回 `{"python":"C:/.../Python312/python.exe","finder":true,"runner":true,"screening_dir":true,"operbox":"operbox_official_1_0923.json","running":false}`。

### 3.4 缺口
1. **`potion` 传不进去**：前端/Node/Python 都收这个参数，但下游 `maa_job_fallback.py` 只有 `--potion`（默认 True）没有关闭开关（`copilot_node.mjs:22-24`，Python `copilot_api.py:64-65` 收了也没用）⇒ 页面上取消勾选无效。
2. **状态是进程内单例**：Node `copilot_node.mjs:43,112`、Python `_state`（`copilot_api.py:32,67-69`）⇒ 只看得到**本进程**起的那一次；Node 重启后 `running` 变 false，唯一持久状态是 `fallback_report.json`。
3. **没有列表/浏览能力**：只有「筛这一关」「按筛选结果跑」「按作业号跑」，没有「列出我挑过的作业/历史 run」的端点（`data/copilot/fallback_jobs/` 118 份正文无人可查）。
4. **无鉴权细分**：这四条经 Node 时和其它 `/api/*` 一样，本机放行、非本机要 token（`server/maaorch-web.mjs:916-920`）——不是缺口，但 MCP 方案要注意 token。

## 4. ★前端退役后，这条线的人机入口应该是什么

前提事实：唯一 UI `ui/web/copilot_lab.js`（142 行）只调 4 个端点（`copilot_lab.js:51` screen、`:64` fallback、`:78` stop、`:125` status），它在退役清单里 ⇒ 页面消失后，**端点还在但没人能点**。41 个 MCP 工具里与作业/copilot/prts 相关的 **0 个**（`network/mcp_server.py` 全文 grep `copilot|prts|job` = 0 命中）。

### 方案 A：收进 MCP 工具（Agent 入口）
- **做法**：在 `network/mcp_server.py` 加 4~5 个 `@mcp.tool()`：`job_info()` / `job_screen(stage, top, refresh)` / `job_fallback_start(stage|codes, emu, max_tries, support, dry_run=True)` / `job_fallback_status()` / `job_fallback_stop(dry_run=True)`。
- **成本**：约 120~180 行（含 docstring 与 dry_run 闸）；写入 `network/mcp_server.py` 一处。
- **硬前置（重要）**：**不能**简单写成 `_call_api(mw, "/api/copilot/screen")` —— 那会 404（§3.2）。两条可行路：
  - A1：把 `copilot_api.register(app)` 从 `start_server` 挪进 `create_app`（1 行搬家，属**代码改动**，需另立任务；顺带复活 4 条 Python 路由）。
  - A2：MCP 工具**自己 spawn CLI**（与 `copilot_node.mjs` 同法），不动 Python 路由；代价是同一套编排逻辑出现**第三份**实现（Python 版 / Node 版 / MCP 版）——本仓审计反复抱怨的就是这种漂移。
- **收益**：Agent 可以在对话里驱动整条线（「挑一份能跑的并把 MT-EX-6 打掉」），符合仓内既定方向「前端退役后人工入口整体迁到 MCP」（`mcp_server.py:14`）。
- **风险**：① 长任务（筛选 10~60 s、重试数分钟）在 MCP 里只能 start+poll；② 写操作必须沿用仓内 dry_run 契约（`mcp_server.py:23-27`），否则与安全纪律冲突；③ MCP 依附 Python 进程，Python 不常驻时 Agent 入口同样不可用。

### 方案 B：纯 CLI + 一页 runbook（零代码）
- **做法**：什么都不改，把 §5 的命令固化成一页 `docs` runbook（本次报告即可充当），必要时补一个 `tools/job_pipeline.sh|.ps1` 串起三步。
- **成本**：0 行代码（runbook 除外）。
- **收益**：今天就能用（三个离线脚本已实测可跑；作业站实测可达）。符合「过渡层」定位：不新增维护面。
- **风险**：需要有人在机器上开终端；`stage_name` 与显示名的换算是个坑（见 §5-②），且 `job_fit`/`pick_copilot` 的硬编码会误导（§1）。

### 方案 C：Node `/status` 加一块「选配作业」（人工入口）★推荐近期
- **做法**：在 `server/maaorch-web.mjs` 的 `PAGE` 里加一块（输入 stage_name + top + 强制刷新 → 候选表 → 「自动降级跑」按钮（emu/max_tries/support + dry-run 默认）→ 日志尾部轮询）。后端**一行都不用加**：`/api/copilot/info|screen|fallback|status|stop` 已在同文件原生实现并实测 200（§3.3），与我在 task-16 加的队列块是同一套模式（`:271-280` UI、`:367-378 qact()`）。
- **成本**：**只改 `server/maaorch-web.mjs` 一个文件，约 80~110 行**（内联 HTML+JS，无新依赖）。
- **收益**：真实解决「用户要保留这条能力」——`/status` 已经是唯一人工入口，能力留在那里最自然；不违背「只留最小运维页」（它就在最小运维页里）；不需要动 Python 路由/MCP。
- **风险**：把 `/status` 重新养成 UI 的诱惑（缓解：就一块、只读为主、写动作沿用 dry-run 默认 + 明确「会真的开跑」）。

### 推荐
**近期（现在）：C + B 并行** —— C 给人一个能点的入口（成本最低、复用已实测通的后端），B 作为「Node/Python/MCP 全没起时」的保底（今天实测 CLI 就能跑）。
**中期：A（建议选 A1：把 `register(app)` 挪进 `create_app`，MCP 工具复用现成 HTTP 端点）**，排在 task-17（G3/G4 运维动作迁 MCP）之后，理由：A1 只有一次 1 行搬家就能让「MCP + Node + Python」三方共用同一份 Python 实现，避免 A2 的第三份实现；A1 落地后 MCP 工具是薄封装，风险低。
**不推荐**：把 copilot 端点补进 `/status` 之外再造一个独立页面（等于重建被退役的前端）；也不推荐只留 A 不留 C（Agent 入口不能替代「人想自己点一下」的场景，且 MCP/Python 依赖更重）。

## 5. 最小可用路径：从「一个关卡 + 一个账号」到「挑出可用作业并跑起来」

| # | 环节 | 现状 | 具体命令/位置 | 缺什么 |
|---|---|---|---|---|
| ① | **干员箱 box**（没有它一切免谈） | 有：`data/operbox*.json` **19** 份，最新 `operbox_official_1_0923.json`（2026-09-23）；`data/boxes/` 另有 3 份 | 取箱：`tools/maa_operbox.py` / `tools/box_read_when_free.py`（`job_fit.py:142` 提示） | 无（但要人工记得先取箱；Node `screen()` 自动挑最新箱，`copilot_node.mjs:49-57`） |
| ② | **stage_name**（不是游戏显示名） | prts 检索键是 `actXXside_exYY` | 换算：`prts_job_finder.stage_names()`（`:119-127`）**只认 SR-x..SR-y 且前缀写死 `act54side`**；`prts_event_pick --event act54side` 可整活动展开 | **缺通用换算**（别的活动/主线只能手填 stage_name） |
| ③ | **外部网络** | 实测可达：`/copilot/query` 200（2140 B，1.96 s）；`/copilot/get/24159` 200（1461 B，1.05 s） | `https://prts.maa.plus`（`prts_job_finder.py:38`） | 无（注意系统代理；本机 Chrome 走 Clash 是历史坑） |
| ④ | **筛选 + 缺口排序** | 三条路：Node `screen`（finder，快、只看有无）/ `prts_event_pick`（精英化感知，整活动）/ `pick_copilot`（精英化，单关但常量写死） | Node：`GET /api/copilot/screen?stage=act42side_ex06&top=6` → 落 `data/copilot/screening/act42side_ex06.json` | 无（选哪条要人判断；finder 会把「精锐不足」误判成能跑，见 `prts_event_pick.py:4-6`） |
| ⑤ | **跑 + 自动换下一份** | `maa_job_fallback.py --stage --screening --emu`（独立进程、日志落盘、判 verdict、核三星） | Node：`POST /api/copilot/fallback {stage, emu, max_tries, support}` + 轮询 `GET .../status` | 需**模拟器在线** + MaaCore（`services/maa/instances/{1,2,3}` 均在）+ 视觉核星（要 AI key）；`potion` 开关缺失 |
| ⑥ | **只看不跑**（先验一遍） | `maa_job_fallback.py --dry-run`（只打印会依次试哪些）；`job_dp_check` 验费用；`job_fit` 验编队 | `python tools/job_dp_check.py <job> --stage act14side_ex06 --raid`（实测 exit 0） | 无 |
| ⑦ | **结果落盘/可查** | `data/copilot/fallback_report.json`（tries+result）、`logs/fallback_*.log` | — | **缺「历史浏览」**（只有最近一次；§3.4-3） |

**结论**：从「一关 + 一账号」到「挑出来并跑起来」，环**已经齐了**（①→⑦ 全部有实现且有依赖），缺的是 **②通用换算**、**入口（§4）**、**potion 开关**、**历史浏览**。最小可用路径 = 「Node `screen` → Node `fallback`」两步（今天就能用，只是没有页面入口）；纯 CLI 等价路径 = 「`prts_job_finder --stages --operbox --out` → `maa_job_fallback --stage --screening --emu`」。

## 6. 复现清单（本次真跑过的命令与结果）

**纪律说明**：本任务只读分析，**刻意没有**实跑会改状态的脚本 —— `prts_job_finder`/`prts_event_pick`/`pick_copilot`（会写 `_get_cache.json` 和 `screening/` 缓存）、`maa_job_fallback`/`run_job`/`enter_event`（会动模拟器、MAA、adb 点屏）。这些的判定来自代码级证据 + 已有产物 + `--help`。

```text
# 1) 11 个脚本都能 import + CLI 正常（全部 exit=0）
for s in prts_job_finder job_fit job_judge job_diff job_dp_check pick_copilot run_job maa_job_fallback own_axis_job prts_event_pick enter_event; do python tools/$s.py --help >/dev/null 2>&1; echo "$s exit=$?"; done
# 实测：11/11 exit=0；其中 job_judge 没有 argparse，--help 被忽略并跑完整分析（54.5s，输出「阶段 act16side_ex02 ｜ 作业 33 份 ｜ 条目 792」）

# 2) 离线脚本真跑（exit 0）
python tools/job_dp_check.py data/copilot/BI-EX-6_cm_v62.json --stage act14side_ex06 --raid
#   → 关卡 act14side_ex06（突袭）：初始 10 费，回复 0.500 费/秒 … 结论：全部付得起 ✓
python tools/job_fit.py --uid 32822410 --stage EX-5
#   → 账号 32822410 ｜ box 229 人 / === EX-5 ｜ 候选 19 份 / 逐份 缺硬需求1（如 圣聆初雪、〈MAA 读不了：cost_changes〉）
python tools/job_diff.py data/copilot/BI-EX-6_cm_v14.json data/copilot/BI-EX-6_cm_v18.json
#   → 21 处差异（operator/location/pre_delay/action_added/action_removed…）

# 3) 作业站可达性（只读 HTTP，不写仓库）
Invoke-WebRequest 'https://prts.maa.plus/copilot/query?limit=1&level_keyword=main_01-07'  # 200, 2140 B, 1960 ms
Invoke-WebRequest 'https://prts.maa.plus/copilot/get/24159'                               # 200, 1461 B, 1046 ms

# 4) Node 侧 copilot 端点活着（临时端口 19996，跑完杀进程；本次已清理）
node server/maaorch-web.mjs --port 19996 --no-upstream
curl http://127.0.0.1:19996/api/copilot/info
#   → 200 {"python":"C:/.../Python312/python.exe","finder":true,"runner":true,"screening_dir":true,"operbox":"operbox_official_1_0923.json","running":false}

# 5) Python 侧未注册（静态证据，3 条 grep）
grep -n 'copilot' network/api_fastapi.py        # 只有 3846/3847/3849（register），没有 @app.* 路由
grep -rn 'start_server' --include=*.py .        # def 在 api_fastapi.py:3839；main_web.pyw:329 只 import 未调用
grep -n 'copilot\|prts\|job' network/mcp_server.py  # 0 命中（41 个 @mcp.tool 里没有作业线）

# 6) 数据体量与角色
ls data/copilot | wc -l                         # 358（顶层文件）
find data/copilot -type f | wc -l               # 971（912 json / 56 md / 2 txt / 1 log）
find data/copilot -maxdepth 1 -type d           # 26 个子目录
```

## 7. 风险与遗留（含我**没有**做的事）

1. **入口断裂是本次唯一「必须决策」的问题**（§4）。其余环节今天都能跑。
2. `job_judge.py` 无 CLI + stage 写死（`job_judge.py:31,229-231`）；`job_fit.py:147-148` 前缀写死 `act54side`；`pick_copilot.py:29-30` 箱/battle_data 写死 —— 三处都会在换活动或换机器时**静默给出错误结论**，比报错更危险。建议后续统一到「参数优先、常量兜底」。
3. `potion` 契约缺口（`copilot_node.mjs:22-24`）：页面上取消勾选无效，属**已知未修**，修法要么给 `maa_job_fallback.py` 加 `--no-potion`，要么页面不显示该选项。
4. 状态是进程内单例 + 只有 `fallback_report.json` 一个持久文件；历史 run 无法回看（`data/copilot/fallback_jobs/` 118 份正文没有索引）。
5. `logs/fallback_0921212046..log` 文件名多一个点（`0921212046..log`），疑似某次时间戳拼接写法变更的残留 —— 只提请注意，未深究，也未改名（只读任务）。
6. **未做**：未跑会动模拟器/MAA/adb 的脚本（`run_job`/`maa_job_fallback`/`enter_event`/`maa_copilot_run`），因此「跑起来」这一环是**代码级 + 依赖存在性**判定，不是实跑判定；要实证需在有模拟器在线时另开一次验收（建议由 Lead 决定时机，因为它会真的开跑并消耗理智）。
7. **环境提醒**（与本次分析无关，属卫生问题）：执行期间发现 **19994 监听中是别人的测试实例**（`node server/maaorch-web.mjs --port 19994 --upstream http://127.0.0.1:19993`）+ **19995** 被 `%TEMP%/maaorch_echo.js` 占着（疑似同伴的 G3/G4 回显上游）。我**没有动**它们；我自己 task-16 遗留的 19996 实例与临时假上游已清理（19996 已释放，`%TEMP%` 无 `maaorch_fake_up.mjs`）。请对应同伴自行收尾，避免端口长期被占。

---
附：本报告对应的任务 task-19；写范围 `docs/audits`，本文件为唯一新建文件，未改任何代码、未执行 git。
