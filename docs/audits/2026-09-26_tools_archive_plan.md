# tools/ 归档分层清单（第一阶段：只出清单，不删任何文件）

- 快照：**2026-09-26**（工作区实时状态；本次未删除/移动/改动 `tools/` 下任何文件）
- 范围：`tools/` 全树 + 全仓（services / network / ui / server / app / manager / infrastructure / config / strategy / knowledge / docs / plans + 根启动器）
- 方法（四条叠加 + 不动点迭代）：
  1. `ast.parse` 全量 .py/.pyw 的 `Import` / `ImportFrom`（含 `level>0` 与逗号式），并区分 **顶层 import** 与 **函数内惰性 import**；
  2. 模块名三键索引（**basename / stem / 仓库相对路径**）—— 覆盖 `import viz_plan`、`from mt_nav import ...` 这类**裸模块名导入**（按文件名扫引用完全看不见）；
  3. .py 的**字符串常量**（排除 docstring）+ 全部 .mjs/.js/.bat/.ps1/.vbs/.sh 原文 → 引号路径正则 —— 覆盖 `subprocess.run([PY, "tools/xxx.py"])` 与 `Path(PROJ,"tools","x.py")` 这类子进程字符串调用；
  4. 由产品入口 / 计划任务 / 常驻服务出发做强引用闭包（顶层 import + 子进程路径），再单独跑一遍**惰性 import** 闭包。
- `docs/`、`plans/` 里的路径提及**不计入**依赖（只作旁证）；**注释里的文件名不计入** —— 这是与上一轮方法最大的差别。

## 0. 实测基数（与任务书数字的差异）

| 项 | 实测 | 说明 |
|---|---|---|
| `tools/` 全部文件 | **506** | `os.walk` 排除 `__pycache__`；含 248 张 jpg、8 个 `.bak*`、8 个日志、26 个 json；目录共 86.65 MB |
| 代码文件 | **204** | .py 165 / .mjs 22 / .ps1 8 / .bat 6 / .vbs 1 / .sh 1 / .js 1 |
| `.py` 行数 | **42,422**（165 个） | 任务书写 约 240 个 .py，实测**只有 165 个**（`os.walk` 与 `Get-ChildItem -Filter *.py` 双口径一致，差在任务书按旧快照估） |
| 代码总行数 | **50,206** | .py 42,422 + .mjs/.bat/.ps1/... 7,784 |

## 1. 三层总览

| 层 | 文件数 | 总行数 | 其中 .py 行 | 判据 |
|---|---|---|---|---|
| **(a) 产品/粘合剂/在跑** | **76** | **21227** | 18907 | 被产品代码（services/network/ui/server）进程内 import 或子进程路径直接/间接调用，或由计划任务、常驻服务启动 |
| **(b) 研发工作台（人工按需）** | **86** | **23092** | 18297 | 不属 (a)，但在 tools/ 内有活引用链（被别的脚本 import / 子进程调用），或有明确的人工运维入口 |
| **(c) 纯研发/一次性** | **42** | **5887** | 5218 | 产品代码、其他 tools 代码、计划任务、启动器**全部零强引用**，且用途为特定关卡 / 特定实验 / 已被通用版取代 |
| 合计 | 204 | 50,206 | 42,422 | |

> 结论先行：按零引用标准可整体迁出的只有 **(c) 42 个文件 / 5887 行（占 .py 的 12.3%）**。(a) 因为存在 **3 处进程内 import + 1 处 importlib 文件加载 + 多处子进程路径调用**，整体迁出必断；(b) 迁出需同步改脚本内路径。

## 2. (a) 产品/粘合剂/在跑 —— 76 文件 / 21227 行（.py 18907 行）

### 2.1 a-硬（顶层 import / 子进程路径 / 计划任务 / 常驻服务）—— 55 文件 / 13263 行

> 调用方列的 `[import]`=顶层 import、`[lazy]`=函数内惰性 import、`[path]`=子进程/路径字符串；标 `启动器/计划任务` 的表示由 tools/ 下的 .bat/.ps1/.vbs/.sh 或 Windows 计划任务直接调用（见 §5.5）。

| 行数 | 文件 | 用途 | 调用方（证据） |
|---|---|---|---|
| 1378 | `tools/push/auto_push.py` | 全自动推图 —— 读节点 → 进关 → 抄作业 → 清剧情/结算/弹窗 → 下一关。 | ui/control_server.py[path] |
| 1276 | `tools/maaorch_service.py` | MAAOrch 常驻查询服务 —— 把"看屏幕/看状态"从冷启动脚本变成毫秒级 HTTP 调用。 | tools 内: event_sweep.mjs[path], page_recog_selftest.py[import+path], pages.py[lazy], panel_gate.py[lazy] |
| 837 | `tools/gen_push_ui.py` | 生成推图 Agent 的界面（单文件 HTML，仿方舟官方进度页）。 | ui/control_server.py[path] |
| 575 | `tools/rogue_fleet_watch.mjs` | * rogue_fleet_watch.mjs —— 肉鸽机队看守（本机跑；l-1/l-2/l-4/l-5/l-7 @ MRFZ-0000） | tools 内: rogue_acceptance.mjs[path], rogue_fleet_launch.ps1[path] |
| 521 | `tools/prts_job_finder.py` | prts 作业筛选器 —— 按账号干员范围（含精英化/等级），从作业站挑出「能直接跑」的作业。 | network/copilot_api.py[path], server/copilot_node.mjs[path], ui/control_server.py[path] |
| 477 | `tools/clear_runner.py` | 打关 MVP 循环（单一控制者 + 状态驱动 + 暂停中观察）。 | tools 内: _launch.py[path], batch_run.py[lazy], read_nodes.py[lazy], run_trace_accept.py[path] |
| 372 | `tools/auto_mas_archiver.py` | AUTO-MAS 历史数据本地存档（防目标机数据丢失 + 前端增量读取）。 | tools 内: auto_mas_hook.py[import] |
| 347 | `tools/mas_dashboard.py` | MAS 终端看板 — 汇总 AUTO-MAS 的运行 / 剿灭 / 刷关 / 耗时 / 失败数据。 | 启动器/计划任务 |
| 340 | `tools/screen_understand.py` | 画面理解 — 给一张游戏截图，像人一样"看懂"当前是什么界面。 | services/ai_agent/percept.py[lazy] |
| 325 | `tools/rogue_plan_selftest.mjs` | * rogue_plan_selftest.mjs —— 机队看守"决策"自测（发车之前就把闸门与阶梯验穿） | tools 内: lib/rogue_plan.mjs[path], rogue_acceptance.mjs[path], rogue_fleet_watch.mjs[path], rogue_report.mjs[path] |
| 323 | `tools/mt_nav.py` | MT（act42side 众生行记）关卡导航 + **身份核实** + 难度切换。 | tools 内: enter_event.py[import], event_sweep.mjs[path], maaorch_service.py[path] |
| 313 | `tools/task_graph.py` | MAA 任务转移图工具 — 解析 tasks.json，展开继承，输出链路图/查询。 | services/ai_agent/percept.py[lazy] |
| 313 | `tools/mas_accounts.py` | AUTO-MAS 账号清单导出（服 / 模拟器 / ADB 口 / 切换关键字）—— 现取现盘，不赌快照。 | 启动器/计划任务 |
| 291 | `tools/lib/rogue_plan.mjs` | * lib/rogue_plan.mjs —— 机队看守的"决策"内核（与"动手"分开，好让它在机器发车之前就能自测） | tools 内: rogue_report.mjs[path] |
| 286 | `tools/run_job.py` | 执行 —— 极简执行器第 ③ 步：人已把游戏停在**该关详情页**，这里只负责起 MAA 跑指定作业。 | ui/control_server.py[path] |
| 281 | `tools/mas_watch.py` | MAAOrch L3 守护进程：日报 + 告警 + 失败自动补跑（工作流层第一个闭环）。 | services/mas_l3.py[path] |
| 265 | `tools/read_nodes.py` | 读活动地图的关卡节点坐标 → 落 `data/stage_nodes.json`（批量执行器 C 的第一版）。 | tools 内: push/auto_push.py[path] |
| 264 | `tools/host_perf.py` | 主机指标采样（从本机 SSH 拉一次，写本地日志）。 | tools 内: pit_check.py[path], scheduler.py[lazy] |
| 263 | `tools/rogue_fleet_probe.mjs` | * rogue_fleet_probe.mjs —— 肉鸽机队"盯号"采样器（本机跑，SSH 只读取证） | tools 内: rogue_fleet_watch.mjs[path], rogue_host_probe.ps1[path] |
| 251 | `tools/maa_job_fallback.py` | 作业自动降级重试 —— 一份跑不了就换下一份，直到三星为止。 | network/copilot_api.py[path], server/copilot_node.mjs[path] |
| 246 | `tools/rogue_judge_selftest.mjs` | * rogue_judge_selftest.mjs —— 判读内核自测：拿 **2026-09-23 真实日志片段**当夹具 | tools 内: lib/rogue_judge.mjs[path], rogue_acceptance.mjs[path], rogue_report.mjs[path] |
| 232 | `tools/host_watchdog.py` | 主机看门狗 —— 生产主机失联就发通知。 | tools 内: host_perf.py[import] |
| 226 | `tools/auto_mas_rerun.py` | 临时起队列补跑（AUTO-MAS）：把指定账号塞进临时队列并发车，跑完即弃。 | server/maaorch-web.mjs[path], services/mas_adapter.py[path], services/mas_l3.py[path] |
| 222 | `tools/auto_mas_toggle_user.py` | AUTO-MAS 账号启用/停用工具（锁感知）。 | server/maaorch-web.mjs[path] |
| 200 | `tools/auto_mas_flatten.py` | AUTO-MAS 队列扁平化：把"特别长"的队列里的脚本项，匀给早收工的队列。 | tools 内: auto_mas_add_slots.py[path] |
| 171 | `tools/lib/rogue_judge.mjs` | * lib/rogue_judge.mjs —— 肉鸽"卡住"判读内核（被两个看守共用，单独成文件是为了能自测） | tools 内: rogue_fleet_probe.mjs[path], rogue_report.mjs[path] |
| 166 | `tools/job_fit.py` | 把「作业要求」和「这个号的 box」对一遍 —— 挑作业不再靠试。 | tools 内: gen_mini_ui.py[import], gen_push_ui.py[lazy], list_jobs.py[import], pit_check.py[path] |
| 159 | `tools/screen_state.py` | 实时画面状态判别 —— 给"盯屏→立刻反应"用。 | tools 内: _launch.py[import+lazy], _read_node.py[import], ai_navigate.py[import+lazy], clear_runner.py[import] |
| 147 | `tools/box_read_when_free.py` | 等模拟器空闲就自动读一次干员仓库（MAA OperBox），落盘缓存。 | ui/control_server.py[path] |
| 147 | `tools/scan_box.py` | 扫 box —— 极简执行器第 ① 步：连模拟器 → 读 UID → 起 MAA 读 box → 存盘。 | ui/control_server.py[path] |
| 146 | `tools/screen_watch.py` | 三台模拟器的**画面监控**：定时抓帧 + OCR 摘要，落盘给人看，也给 agent 查。 | ui/control_server.py[path] |
| 142 | `tools/maa_frame.py` | 真·最快取帧：直接用 ctypes 调 MaaCore 的 **BGR 原始帧**接口 + 强制新鲜截图。 | tools 内: maaorch_service.py[path] |
| 141 | `tools/auto_mas_queue_times.py` | AUTO-MAS 队列定时项配置 — 每队列一天两轮（早班+晚班，错峰启动）。 | 启动器/计划任务 |
| 136 | `tools/maa_copilot_run.py` | 跑一份抄作业（MAA Copilot）—— 把作业站的作业真正执行掉。 | tools 内: clear_runner.py[path], plan_loop.py[path], push/README.md[path], push/auto_push.py[path] |
| 136 | `tools/maa_operbox.py` | 干员仓库读取（MAA OperBox 任务）—— 判断账号能跑哪份作业的前提。 | network/copilot_api.py[path] |
| 130 | `tools/rogue_host_probe.ps1` | function TailBytes([string]$p, [int]$n) { | tools 内: deploy_host_file.mjs[path], rogue_fleet_probe.mjs[path] |
| 129 | `tools/gen_mini_ui.py` | 极简 UI —— 只干"看候选 + 点执行"，别的都不要。 | ui/control_server.py[path] |
| 119 | `tools/trace_observer.py` | 旁挂观察者：只读日志事件 + 采样画面 → trace.jsonl + notify + 心跳。不干预 runner。 | services/ai_agent/meta_agent.py[path] |
| 113 | `tools/gen_push_index.py` | 多账号总览页：三个号一眼看全（进度/理智/最近事件 + 跳各自详情）。 | ui/control_server.py[path] |
| 111 | `tools/auto_mas_export.py` | AUTO-MAS 账号信息一键导出表格。 | tools 内: 导出账号信息.bat[path] |
| 109 | `tools/auto_mas_hook.py` | AUTO-MAS 完成钩子 — 账号运行一完成就抓取数据入本地存档。 | tools 内: hook.bat[path] |
| 102 | `tools/run_trace_accept.py` | 第 1 步：纯 trace 验收（观察者旁挂，不干预 runner）。 | services/ai_agent/meta_agent.py[lazy] |
| 93 | `tools/deploy_host_file.mjs` | * deploy_host_file.mjs —— 把本地文件搬到 MRFZ-0000 上（scp/sftp 不通时的正解） | tools 内: rogue_fleet_probe.mjs[path] |
| 90 | `tools/auto_mas_add_slots.py` | 给 AUTO-MAS 扩/减跑位：确保存在 N 组「日常定时N / 晚班定时N」队列并配好错峰定时。 | 启动器/计划任务 |
| 88 | `tools/maa_channel/mcp_server.py` | maa-channel MCP server — 目标机远程控制（stdio 模式，经 SSH 使用）。 | 启动器/计划任务 |
| 80 | `tools/maaorch_web_launch.ps1` | ﻿# MAAOrch 看板自启（无窗口）—— 计划任务 MAAOrch-WebUI 每 5 分钟调一次 | 启动器/计划任务 |
| 38 | `tools/_dev.py` | _dev.py —— 模拟器 adb 地址的**共享动态解析**（2026-09-23 新写） | tools 内: bench_service.py[import], fastnav.py[import], look.py[import], maa_frame.py[import] |
| 37 | `tools/run_hidden.vbs` | ' run_hidden.vbs �� ��"�ƻ��������� .ps1/.bat"��ɳ����޴��ڣ����治�����ڴ� | tools 内: maaorch_web_launch.ps1[path], mpelb_launch.ps1[path], rogue0_watch.mjs[path], rogue0_watch_launch.ps1[path] |
| 29 | `tools/rogue_fleet_launch.ps1` | Set-Location $root | 启动器/计划任务 |
| 27 | `tools/mas_dash.sh` | set -u | 启动器/计划任务 |
| 19 | `tools/maaorch_platform.bat` | cd /d "%~dp0.." | 启动器/计划任务 |
| 12 | `tools/导出账号信息.bat` | chcp 65001 >nul | 启动器/计划任务 |
| 8 | `tools/mas_web_ui.bat` | cd /d "%~dp0.." | tools 内: run_hidden.vbs[path] |
| 7 | `tools/hook.bat` | chcp 65001 >nul | 启动器/计划任务 |
| 7 | `tools/start_python_upstream.bat` | cd /d "%~dp0.." | tools 内: maaorch_platform.bat[path] |

### 2.2 a-惰性（只被函数内 import 摸到）—— 21 文件 / 7964 行

| 行数 | 文件 | 用途 | 谁在函数内 import 它 |
|---|---|---|---|
| 1464 | `tools/viz_plan.py` | 排轴可视化：单文件 HTML（底图 + 敌人路径(BFS) + 干员位置/朝向 + 动作列表 + 冲突标注）。 | combat_model.py, cost_model.py, duel_model.py, enemy_speed.py, operator_template.py, workspace.py |
| 1195 | `tools/workspace.py` | 工作区：按「账号 × 活动 × 关卡」组织候选作业与最终通关（一次生成整个活动）。 | box_groups.py, job_diff.py, viz_plan.py, ws_server.py |
| 793 | `tools/main_nav.py` | main_nav.py —— **主线（主题曲）关卡导航 + 关卡身份核实**（2026-09-23 新写） | panel_gate.py |
| 480 | `tools/discover_stages.py` | 运行时"自己发现关卡" —— 让项目**不携带任何游戏内数据**（用户 2026-09-24 的要求）。 | main_nav.py |
| 419 | `tools/cost_model.py` | 简化费用模型：算「第 N 个部署最早什么时候能落地」，用来反推"这份作业来不来得及"。 | (间接) |
| 390 | `tools/combat_model.py` | 敌方攻击机制：生存时间 / 阻挡延后 → 把到门时刻从「无阻挡下界」往真实推一档。 | duel_model.py, operator_template.py |
| 327 | `tools/panel_gate.py` | panel_gate.py —— **面板判据的唯一实现**（2026-09-24 审计后重构） | maaorch_service.py |
| 285 | `tools/job_diff.py` | 两份作业的结构化差异 —— 同一关不同账号的 `cleared.json` 放一起就是标注数据。 | (间接) |
| 274 | `tools/enemy_speed.py` | 敌人移速 → 到达时间。 | cost_model.py |
| 271 | `tools/batch_run.py` | 批量执行器（按活动）：**一次跑完一个活动的所有关卡** —— 队列 + 结果检测 + 失败处理 + 实时流。 | read_nodes.py |
| 258 | `tools/versions.py` | 修改树：每关一条版本链（`v1_ai` → `v2_human` → `v3_human`，可分支 `v2b_human`）。 | workspace.py, ws_server.py |
| 251 | `tools/fastnav.py` | 快导航内核（2026-09-24）—— 把"点一下 / 滑一下 / 等画面"全部压到最快。 | maaorch_service.py, mt_nav.py |
| 236 | `tools/stage_boxes.py` | **关卡按钮识别** —— 用户 2026-09-24 给的判据： | maaorch_service.py |
| 235 | `tools/operator_template.py` | 干员输出模板：输入「干员 + 时刻」→ 输出这一刻的 DPS。 | duel_model.py |
| 212 | `tools/ws_server.py` | 工作区可写服务：托管 `data/workspaces/` 并接收「保存为 cleared / 保存备注」。 | (间接) |
| 209 | `tools/batch_index.py` | 账号池 + 作业池索引（本地跑；目标机定时导出 box 后拉回）。 | workspace.py |
| 171 | `tools/box_groups.py` | 账号分组：按 box 指纹算相似度归组，**同组账号共享匹配结果**（省掉重复的池子排序）。 | workspace.py |
| 165 | `tools/duel_model.py` | 1v1 决斗模拟器：一个挡位 × 一个敌人，逐秒推进到一方死。 | (间接) |
| 161 | `tools/match_candidates.py` | box × 作业池 → candidates（缺 0 直跑 / 缺 1-2 半自动替代 / 缺 3+ 进队列）。 | workspace.py |
| 87 | `tools/pages.py` | 页面指纹入口 —— **薄壳**，唯一实现是 `tools/maaorch_service.py::page_of`。 | (间接) |
| 81 | `tools/vision.py` | 外部视觉模型通道 —— **不必用 OCR**（用户 2026-09-24 提醒）。 | stage_boxes.py |

## 3. (b) 研发工作台（人工偶尔用）—— 86 文件 / 23092 行

| 行数 | 文件 | 用途 | tools 内调用方 |
|---|---|---|---|
| 2420 | `tools/event_sweep.mjs` | * 活动关卡横扫器 —— 「普通 + 突袭 全清」（2026-09-22 新写，Node） | pending_reverify.py[path], run_campaign.ps1[path] |
| 1527 | `tools/stage_attempt.py` | 通用跑局：**假定游戏已经停在目标关卡的详情页**（不做关卡导航），只负责： | event_sweep.mjs[path], oper_gate_itest.py[path], run_campaign.ps1[path] |
| 786 | `tools/enter_by_template.py` | 按**卡片模板**进活动 —— 替掉"在网格里 OCR 找乐章名"那条又慢又不可靠的路。 | event_sweep.mjs[path] |
| 785 | `tools/scheduler.py` | 调度器（叫号模型）—— 步骤 1-6，默认 dry-run。 | （无——靠人直接敲命令） |
| 771 | `tools/rogue0_watch.mjs` | * rogue0_watch.mjs —— 本机 #0「日服 · MuMu 实例 0 · emulator-5554」水月肉鸽保活看守 | rogue0_watch_launch.ps1[path], rogue_acceptance.mjs[path] |
| 679 | `tools/annotate_live.py` | 战斗画面标注器（贴合版）—— 所有框都是**量出来的紧贴外框**，不是估的。 | annotate_view.py[import], battle_sampler.py[import], pit_check.py[lazy+path] |
| 642 | `tools/pending_reverify.py` | pending_reverify.py —— **⊘（报酬无源石 ⇒ 不打）待重验单位查询器** | event_sweep.mjs[path] |
| 552 | `tools/battle_loop.py` | 最小战斗循环 — 暂停→截图→提取状态→(规则)决策→执行→步进→再暂停。 | annotate_live.py[import], annotate_view.py[import], battle_sampler.py[import] |
| 507 | `tools/stage_logic.py` | 关卡逻辑：**多作业 → 单逻辑**（位置/功能/时间三路对齐 → 共性 + 灵活性 + 时机窗）。 | v1_quality.py[import+path] |
| 502 | `tools/annotate_view.py` | 战斗画面标注 · 可视化界面（本地网页） | （无——靠人直接敲命令） |
| 484 | `tools/ui_layout.py` | UI 静态布局表（Layer 1）+ 锚点（Layer 2）+ 映射（Layer 3）。 | annotate_live.py[lazy], annotate_view.py[lazy] |
| 432 | `tools/battle_sampler.py` | 肉鸽战斗画面 · **只读**采样器（把战斗帧按帧存下来喂识别/标注）。 | pit_check.py[path] |
| 419 | `tools/prts_event_pick.py` | 活动作业筛选（精英化感知）—— 一次把一个活动的「普通 + 突袭」全挑完。 | _event_user_pick.py[import] |
| 416 | `tools/approval_preflight.py` | 审批单「执行前预检」——执行端（MAAOrch rig）侧，**只读，不驱动游戏**。 | （无——靠人直接敲命令） |
| 406 | `tools/enter_event.py` | 进活动 —— 从大厅一路点到「活动的关卡地图」，可顺手切到 EX 区。 | enter_by_template.py[lazy] |
| 373 | `tools/axis_rank.py` | 作业排序器 —— 把"选作业"从**热度排序**升级成 **P(3星) 排序**（2026-09-24 按用户指导实现） | event_sweep.mjs[path], training_report.py[path] |
| 355 | `tools/stage_cut.py` | 通用版「封锁线最小割」：给任意 stageId，算出**最少挡哪几格**能封住全部官方路线。 | stage_arrivals.py[import], stage_plan.py[import] |
| 352 | `tools/tilepos_audit.py` | TilePos 数据体检器：TileCalc2 预测的格网几何 vs 游戏高亮观测到的几何。 | annotate_live.py[lazy], annotate_view.py[lazy], battle_loop.py[lazy] |
| 347 | `tools/pit_check.py` | 坑的回归哨兵 —— 把"踩过的坑"变成能重跑的断言。 | event_sweep.mjs[path] |
| 343 | `tools/scan_map_panels.py` | scan_map_panels.py —— 地图**面板预扫**：一次判清"这张图上哪几关真有矿"。 | _patch_scan.py[path] |
| 341 | `tools/feedback.py` | 反哺生成：把入库的规则**应用**回一份作业，产出下一版（闭环的最后一环）。 | loop_check.py[lazy+path], stage_logic.py[lazy] |
| 292 | `tools/rogue_routes.py` | 肉鸽图：出怪路线 / 到达时刻 / 堵点 —— 全部来自关卡数据（不靠拟合）。 | pit_check.py[path], rogue_cut.py[import] |
| 284 | `tools/mastery_scan.py` | 干员「专精 / 模组」图像识别 —— 用 MAA 自带模板读界面上的标记，不依赖任何登录。 | training_report.py[path] |
| 278 | `tools/rogue_watch.mjs` | * rogue_watch.mjs —— 肉鸽机队看守（fz-maa l-1/l-2/l-4/l-5/l-7） | （无——靠人直接敲命令） |
| 277 | `tools/audit_done_units.py` | **审计"标了 done 的单位"到底有没有凭据** —— 判错的代价不对称，所以判据要分层。 | （无——靠人直接敲命令） |
| 272 | `tools/plan_loop.py` | 打关迭代循环：编排 → 尝试 → 失败分析 → 改招 → 再编排 → … 直到三星。 | （无——靠人直接敲命令） |
| 262 | `tools/fleet_status.py` | MAAOrch 机队一览（只读一屏）—— 设备占用 / 队列 / 计划任务 / 肉鸽机队 / 机器人席位 / 房间最后几条。 | （无——靠人直接敲命令） |
| 259 | `tools/enemy_timeline.py` | 敌人位置时间轴 —— 「算」侧的产物：第 N 秒谁在第几格。 | annotate_live.py[lazy], battle_loop.py[import], rogue_routes.py[import] |
| 251 | `tools/plan_mines.py` | 挖矿概率排产器 —— 把"还有哪些石头 + 大概能不能拿到"算成一个期望值清单。 | rank_service.mjs[path] |
| 250 | `tools/material_stages.py` | 蓝材料（T3）刷取地点查询 — 基于 MAA stages.json 掉落表 + 企鹅物流真实掉率。 | （无——靠人直接敲命令） |
| 245 | `tools/param_calibrate_asst.py` | 参数校准（asst.log 版）— 用 maa_history 全量 asst.log 校准固定时间参数。 | （无——靠人直接敲命令） |
| 242 | `tools/v1_quality.py` | AI 选配质量体检：**这一关的 `v1_ai` 是"能改"还是"得重做"**（供人决定下一步）。 | （无——靠人直接敲命令） |
| 242 | `tools/stage_value.py` | 关卡数值顾问 —— 给「关卡 + 账号」算承受账/输出账。 | derive_plan.py[import], ga_batch_value.py[import], plan_loop.py[import] |
| 236 | `tools/deploy.py` | MAAOrch 一键部署工具 — 本地代码 → 目标机同步 + 重启 + 验证。 | （无——靠人直接敲命令） |
| 223 | `tools/prime_sweep.mjs` | * prime_sweep —— 「首通源石」横扫器（Node，2026-09-23 新写） | （无——靠人直接敲命令） |
| 222 | `tools/axis_viz.py` | 作业轴可视化：把 MAA copilot 作业画成方格地图，逐格标出干员/箱子/朝向/技能， | （无——靠人直接敲命令） |
| 212 | `tools/redact_scriptconfig.py` | raw ScriptConfig.json → 脱敏版（任何 agent 一键可用）。 | （无——靠人直接敲命令） |
| 209 | `tools/maa_mastery_ledger.py` | 练度台账 —— 把 MAA 抄作业编队时报的「干员不可用」攒成证据库。 | _event_user_pick.py[path] |
| 205 | `tools/vision_translate.py` | 图片转译工具 — 把 MAA 模板图/截图转成文本描述，供无视觉的 LLM（DeepSeek）分析。 | scripts/task_explainer.py[lazy] |
| 203 | `tools/push_driver.py` | 推图驱动器：哪个号空着就给它派下一关，**打完立刻读结果、立刻派下一关**（不用人盯）。 | （无——靠人直接敲命令） |
| 198 | `tools/training_report.py` | 训练结果报告 —— 把"这个号的打关学习成果"汇成一页 HTML。 | （无——靠人直接敲命令） |
| 197 | `tools/learn_map.py` | **一次问视觉模型，拿全一张地图的关卡清单 + 点击坐标** —— 泛化/免维护的收口。 | scan_map_panels.py[path] |
| 194 | `tools/maafw_capture.py` | MaaFramework Win32 高速取帧（FramePool）→ 直接给 1920×1080 的设备帧。 | annotate_view.py[lazy], battle_sampler.py[import] |
| 194 | `tools/rules_db.py` | 规则入库：把**修改树里人改出来的变化**聚合成"这一关该怎么打"的规则，供反哺生成消费。 | feedback.py[import], loop_check.py[import] |
| 186 | `tools/page_recog_selftest.py` | page_recog_selftest.py —— **页面识别离线回归**（2026-09-24 独立审计产出） | （无——靠人直接敲命令） |
| 182 | `tools/rank_service.mjs` | * rank_service —— **与模型无关**的作业排序接口层（2026-09-24） | laya_shadow.mjs[path] |
| 182 | `tools/nav_map.py` | 坐标导航（"用眼睛代替 OCR"）—— 2026-09-24 | event_sweep.mjs[path] |
| 179 | `tools/pick_copilot.py` | 按「本号干员/练度」筛选作业站（ZOOT/prts）作业。 | （无——靠人直接敲命令） |
| 176 | `tools/push_schedule.py` | 多账号推图调度：每个号差多少理智、要等多久、来不来得及、先打哪个。 | （无——靠人直接敲命令） |
| 166 | `tools/bi_state.py` | BI 关卡状态探针：进关卡详情页、看突袭开关能不能切 —— 判断「普通是否已通关」。 | bi_goto.py[path], event_sweep.mjs[path] |
| 155 | `tools/rogue_report.mjs` | * rogue_report.mjs —— 肉鸽"盯号"日报：把逐轮采样变成"谁在跑、谁挂了、怎么救的" | rogue_acceptance.mjs[path] |
| 152 | `tools/loop_check.py` | 闭环体检：**一圈到底转了没有** —— 一条命令看三件事（修改树 / 规则库 / 反哺能不能套上）。 | （无——靠人直接敲命令） |
| 143 | `tools/scripts/backfill_account_state.py` | Backfill AccountState from archived MAA logs (maa_history/{aid}/). | （无——靠人直接敲命令） |
| 139 | `tools/push/calibrate.py` | 以「游戏原话」校准状态文件：地图节点右侧的实心黄点 = 该关已通关。 | push/README.md[path] |
| 133 | `tools/scripts/task_explainer.py` | Task explainer — 用 DeepSeek 分析 MAA 任务链/图片转译，解释逻辑并辅助设计自定义任务。 | （无——靠人直接敲命令） |
| 131 | `tools/run_campaign.ps1` | ﻿# 无人值守跑队列（Lead 专用）。纪律：同一模拟器只能有一个 MAA。 | （无——靠人直接敲命令） |
| 121 | `tools/ai_navigate.py` | AI 导航：读图 → 判断页面与下一步按钮 → 点一次 → 双重验证。只验证**导航**，不打关。 | _launch.py[lazy] |
| 113 | `tools/prime_plan.mjs` | * prime_plan —— 「首通源石」计划表（Node，2026-09-23 新写） | prime_sweep.mjs[path] |
| 108 | `tools/ce6_star_count.py` | 数结算页的星星（1~3）—— 终于能把"三星"这件事自动化判定了。 | stage_attempt.py[lazy] |
| 107 | `tools/convert_macore_task.mjs` | * MaaCore 任务 JSON → MaaFramework 流水线 JSON 转换器（2026-09-22） | （无——靠人直接敲命令） |
| 107 | `tools/bi_goto.py` | 稳健地回到某个关卡的详情页（BI-EX-*）。 | bi_home.py[path], stage_attempt.py[path] |
| 105 | `tools/look.py` | 一眼看清屏幕 —— **单进程**版快照工具（2026-09-24，为"查得慢"而写） | event_sweep.mjs[path] |
| 101 | `tools/page_memory.py` | 页面纪要：第一次读图识别→验证→存；第二次查表→直接用（0 延迟、0 token）。 | ai_navigate.py[lazy] |
| 101 | `tools/bench_service.py` | 把"一次查看"整条链路的**每一个点**都量出来（2026-09-24，回答"每个点的延迟是多少"）。 | （无——靠人直接敲命令） |
| 100 | `tools/maa_all/mmui2.js` | let copies=[], emus=[], favs=[], showfav=false, emuSel=new Set(), sortKey= | （无——靠人直接敲命令） |
| 96 | `tools/list_jobs.py` | 筛作业 —— 极简执行器第 ② 步：按 box 给某一关的作业排序（缺几个）。 | push/README.md[path] |
| 96 | `tools/scripts/backfill_stats.py` | Backfill stats.json from events.log — recover run records for days when | （无——靠人直接敲命令） |
| 96 | `tools/bi_home.py` | bi_home.py —— 可靠的"回到关卡详情页"流程（2026-09-22 新增） | event_sweep.mjs[path] |
| 93 | `tools/story_skip.py` | **剧情跳过看门狗** —— 2026-09-24 实测得出：EA-1（从没打过的关）点「开始行动」后会先播**剧情**， | event_sweep.mjs[path] |
| 91 | `tools/state.py` | 账号状态层：每个账号「现在在哪一步、跑过什么、结果如何」。 | orchestrator.py[import] |
| 89 | `tools/dashboard.py` | 一行命令看全局：源石账本 / 待打的矿 / 下一步排产 / 团队任务板提示。 | （无——靠人直接敲命令） |
| 83 | `tools/event_templates.py` | 活动卡片用**封面模板**认，不用 OCR 认艺术字（用户 2026-09-24 的建议）。 | enter_by_template.py[path] |
| 81 | `tools/rogue_freeze_replay.mjs` | * rogue_freeze_replay.mjs —— 用**真实日志里的现场图 md5 序列**重放"画面冻结阶梯" | rogue_acceptance.mjs[path] |
| 79 | `tools/rogue0_history.mjs` | * rogue0_history.mjs —— 从 #0 自己的 MAA 日志里挖"它历史上都是怎么卡的" | （无——靠人直接敲命令） |
| 76 | `tools/aggregate.py` | 第二层：纯统计聚合（不需要 AI）——失败模式 / 成功规律 / 替代效果。 | （无——靠人直接敲命令） |
| 67 | `tools/mpelb_launch.ps1` | ﻿# MPE LocalBridge 自启（无窗口）—— 计划任务 MAAOrch-MpeLocalBridge 登录时调一次 | （无——靠人直接敲命令） |
| 58 | `tools/box_accept.py` | box 验收清单（box 到位后**第一件事**，不过就停，不往下跑 candidates）。 | （无——靠人直接敲命令） |
| 56 | `tools/tap.py` | tap / swipe 一条命令搞定（自动解析 MuMu 的 adb 端口）。 | event_sweep.mjs[path] |
| 54 | `tools/runs.py` | 第一层：每局一条结构化记录（runs.jsonl）。没有它，后面的聚合/提炼全是空的。 | aggregate.py[import] |
| 51 | `tools/spot_check.py` | 人工抽查用：给定账号（本地 box 或目标机导出），输出候选作业的缺员/软需求明细与建议。 | （无——靠人直接敲命令） |
| 46 | `tools/fetch_enemy_db.py` | 下载敌人属性库（levels/enemydata/enemy_database.json）与敌人名表，存到 data/。 | （无——靠人直接敲命令） |
| 33 | `tools/rogue0_watch_launch.ps1` | Set-Location $root | （无——靠人直接敲命令） |
| 21 | `tools/mpelb_start.bat` | setlocal | mpelb_debug.mjs[path], mpelb_launch.ps1[path] |
| 19 | `tools/job_meta.py` | 作业元数据的**单一来源** —— 别再各处硬编码。 | derive_plan.py[import], plan_loop.py[import], stage_logic.py[import] |
| 18 | `tools/crop_template.ps1` | param([string]$Image,[string]$Out,[int]$X,[int]$Y,[int]$W,[int]$H,[double] | （无——靠人直接敲命令） |
| 16 | `tools/_kill_my_drivers.ps1` | Where-Object { $_.CommandLine -match $mine } | （无——靠人直接敲命令） |

## 4. (c) 纯研发/一次性 —— 完整清单（42 文件 / 5887 行，其中 .py 5218 行）

判据：产品代码（services/network/ui/server/app/manager/infrastructure）、其他 tools 代码、计划任务、启动器 —— **全部零强引用**（导入或子进程路径）。逐个反扫后，剩余提及一律是注释/docstring。

| # | 行数 | 文件 | 用途 | 唯一还在被提到的地方（都是注释/文档，不是调用） |
|---|---|---|---|---|
| 1 | 381 | `tools/ce6_attempt.py` | CE-6 一局完整尝试：导航 → 验「代理指挥」关 → 录屏 → 跑我们的作业 → 出证据报告。 | docs/plans:2 处 |
| 2 | 381 | `tools/_event_user_pick.py` | 活动作业筛选（缺口优先 + 突袭覆盖）—— 一次性脚本，活动前缀可换。 | docs/plans:1 处 |
| 3 | 271 | `tools/ai_agent_offline_check.py` | 离线回归 — 用历史截图验收感知层（不连模拟器、不发任何输入）。 | docs/plans:1 处 |
| 4 | 266 | `tools/rogue_cut.py` | 实验 5：出怪路线的**最小割**（外援 2026-09-19 方案 B1~B5 的落地）。 | docs/plans:2 处 |
| 5 | 236 | `tools/ai_agent_play_plan.py` | 按计划打一局 — AI 自己执行"从人工作业学来的"部署计划。 | docs/plans:2 处 |
| 6 | 231 | `tools/job_judge.py` | 完整判漏：把三个模型合起来，给每份作业的每条出怪分类。 | docs/plans:2 处 |
| 7 | 207 | `tools/_axis_check.py` | 这份作业"算过没有"——把排轴可视化的三条判据写成可计算检查。 | docs/plans:2 处 |
| 8 | 205 | `tools/mpelb_debug.mjs` | * MPE LocalBridge 流程级调试器（无浏览器）—— 直接跑 `/mpe/debug/run/start`。 | docs/plans:1 处 |
| 9 | 203 | `tools/derive_plan.py` | 按「地图 choke + 敌人数值 + 本号练度」自动生成打法（GA-8 那套的通用版）。 | docs/plans:3 处 |
| 10 | 163 | `tools/rogue_acceptance.mjs` | * rogue_acceptance.mjs —— 肉鸽看守「验收一页纸」：一条命令把"能不能信"摊开 | docs/plans:2 处 |
| 11 | 157 | `tools/ai_agent_drill.py` | 演习陪练 — 一键把角色送进"零理智成本的练习战斗"，供战斗模块反复迭代。 | docs/plans:1 处 |
| 12 | 156 | `tools/ce6_cut_check.py` | 算 CE-6 的「敌人真实走位」与「能封住所有路的落点集合」—— 排轴前必须先过这一关。 | docs/plans:5 处 |
| 13 | 154 | `tools/maa_form_probe2.py` | 探「MAA 自动编队能不能把某人编进队」+ A/B 组名形式 —— 编队失败在开打前 ⇒ **不花理智** ✓。 | docs/plans:2 处 |
| 14 | 149 | `tools/stage_plan.py` | BI 关卡选点规划器：算出「堵哪几格 / 高台站哪几格」的候选排名。 | docs/plans:2 处 |
| 15 | 148 | `tools/ai_agent_live_probe.py` | 实机"看一帧" — 只读感知探针（不发任何输入，抢不到 MAA 的操作）。 | docs/plans:1 处 |
| 16 | 141 | `tools/ai_agent_stage_plan.py` | 从人工作业生成"AI 自己能执行的部署计划"+ 把关卡格点渲染成图。 | docs/plans:1 处 |
| 17 | 135 | `tools/mpelb_probe.mjs` | * MPE LocalBridge (mpelb) 探针 —— 不走浏览器，直接从协议层验证本地服务。 | 零提及 |
| 18 | 132 | `tools/own_axis_job.py` | 把我们自己算出来的打法变成一份 MAA copilot 作业（本地文件，不抄别人）。 | docs/plans:1 处 |
| 19 | 121 | `tools/story_nav_selftest.py` | task-12 自证（离线、确定性）：主线接入的每一条都能被判据证明。 | docs/plans:2 处 |
| 20 | 120 | `tools/analyze_run_video.py` | 从整局录屏里读出「第几秒发生了什么」—— 判据是 HUD 上数字的变化时刻。 | docs/plans:1 处 |
| 21 | 114 | `tools/box_uid_stamp.py` | 给 MAA 干员盒快照盖上**本号 uid**（sidecar），让"并集"这件事可证伪。 | 零提及 |
| 22 | 114 | `tools/ce6_map_verify.py` | 校验「MAA 的格子 -> 屏幕坐标」映射对不对 —— 用游戏自己画的红门/蓝门做地面真值。 | docs/plans:2 处 |
| 23 | 112 | `tools/stage_arrivals.py` | 给某个 stageId 排出「哪条路、几点出怪、几点到门」——决定开局能慢到什么程度。 | docs/plans:3 处 |
| 24 | 111 | `tools/maa_form_probe.py` | 测「MAA 的自动编队能不能把某个人编进队」—— 失败在开打之前 ⇒ **不花理智**，可以放心批量探。 | docs/plans:1 处 |
| 25 | 111 | `tools/mpelb_vision.mjs` | * MPE LocalBridge 固定图识别（离线视觉校验）—— 不连设备，拿一张 PNG 当底图验识别。 | docs/plans:1 处 |
| 26 | 109 | `tools/ai_agent_think.py` | 让 AI 看一眼并决定下一步 — "教它玩"的实际入口。 | docs/plans:1 处 |
| 27 | 108 | `tools/job_dp_check.py` | 作业费用可行性检查：按关卡的费用回复速度模拟一条时间轴，判断作业里每个 Deploy | docs/plans:2 处 |
| 28 | 107 | `tools/ga_batch_value.py` | GA 系列批量数值体检 —— 对每一关跑一遍「地图 + 波次 + 敌人数值 + 本号输出账」。 | docs/plans:1 处 |
| 29 | 106 | `tools/_launch.py` | 清场回详情页 → 起指定方案。用法: python tools/_launch.py <plan.json> | docs/plans:2 处 |
| 30 | 100 | `tools/oper_gate_itest.py` | **干员拥有性闸的调用方集成回归**（离线；不连 adb / 不 spawn MAA；2026-09-24 夜）。 | docs/plans:1 处 |
| 31 | 98 | `tools/ai_agent_discover.py` | 教 agent 认界面 — 用 MAA 任务库扫当前画面，把认出来的元素学成这个界面的锚点。 | docs/plans:1 处 |
| 32 | 95 | `tools/ai_agent_actuator_selftest.py` | 执行层自检 — 离线跑通"定位 → 点击 → 验证 → 留证 → 可重放"闭环。 | docs/plans:1 处 |
| 33 | 91 | `tools/node_shape.py` | **按节点形状判断"这关打过没有"** —— 用户 2026-09-24 给的关键判据： | docs/plans:1 处 |
| 34 | 90 | `tools/ce6_spawn_order.py` | 算 CE-6 的「出怪顺序 + 到达封口格的时刻」—— 回答"敌人先从哪儿出来"。 | docs/plans:3 处 |
| 35 | 85 | `tools/bi_back_to_stage.py` | 把游戏弄回「目标关卡的详情页」——用于掐局/失败之后自动回到可再跑的状态。 | docs/plans:1 处 |
| 36 | 73 | `tools/video_filmstrip.py` | 把整局录像按固定间隔抽帧拼成"胶片"——人眼一眼看完一局，定位崩点。 | docs/plans:1 处 |
| 37 | 68 | `tools/roster_scan.py` | 按关键词扫本号 391 个干员的天赋/技能 —— 用来"了解这个账号上每个干员适合什么"。 | docs/plans:1 处 |
| 38 | 55 | `tools/laya_shadow.mjs` | * laya_shadow —— **影子对比**：同一关，规则排序 vs Laya 排序，两条都算、都记账， | 零提及 |
| 39 | 55 | `tools/job_add_skill.py` | 给生成好的作业补 `skill` / `skill_usage`（own_axis_job.py 只写干员名，不写技能）。 | docs/plans:1 处 |
| 40 | 49 | `tools/_read_node.py` | (无 docstring) | docs/plans:1 处 |
| 41 | 44 | `tools/orchestrator.py` | 编排器（20 行量级）：串起 状态层 + 匹配层 + 执行层。 | docs/plans:1 处 |
| 42 | 35 | `tools/_patch_scan.py` | (无 docstring) | docs/plans:1 处 |

**小计：42 个文件 / 5887 行**（上图即完整清单，可直接据此搬迁，无需再逐个评审）。

## 5. 若整体迁出 `tools/`（或整体 git mv）会打断哪些调用方 —— 逐条

> 以下每条都是**代码级**调用（不是文档提及）。整体迁出 = 全部同时断。

### 5.1 进程内 import（迁出即 ImportError，3 处）

| tools 文件 | 调用方:行 | 代码 | 后果 |
|---|---|---|---|
| `tools/task_graph.py` | `services/ai_agent/percept.py:92`、`:121` | `import task_graph`（`percept.py:77` 先 `sys.path.insert(0, tools_dir)`） | Harness 大脑的感知层**顶不住就直接 ImportError**，`percept` 整模块倒 |
| `tools/screen_understand.py` | `services/ai_agent/percept.py:762` | `import screen_understand as su`（`:733` 注释自述依赖 tools/ 在 sys.path） | 同上；画面→语义状态表失效 |
| `tools/run_trace_accept.py` | `services/ai_agent/meta_agent.py:41` | `from tools.run_trace_accept import last_log_mark, lines_since`（**包式导入，写死了 tools 目录名**） | AI 自我改造循环（meta_agent）直接崩 |

补充（非 import 但同为文件级依赖）：`services/ai_agent/meta_agent.py:24`、`:142` 把 `tools/trace_observer.py` 写进**可自动修改文件白名单**并读取它。

### 5.2 importlib 按文件路径加载（1 处）

| tools 文件 | 调用方:行 | 代码 | 后果 |
|---|---|---|---|
| `tools/mas_watch.py` | `services/mas_l3.py:49-58` | `spec_from_file_location('_mas_watch', os.path.join(ROOT, 'tools', 'mas_watch.py'))` → `:71 _watch_mod().fetch()` | `host_snapshot()` 抛错；`services/mas_adapter.py` 会兜成 `{ok:False}`，但 **MAS 状态半边全瞎**（不是崩，是静默降级） |

### 5.3 产品侧 python 子进程字符串路径

| tools 文件 | 调用方:行 | 后果 |
|---|---|---|
| `tools/auto_mas_rerun.py` | `services/mas_l3.py:257`、`:290`；`services/mas_adapter.py:205` | MAS 补跑 / 清理失效（Harness 的 mas_rerun / mas_cleanup 工具全废） |
| `tools/prts_job_finder.py` | `network/copilot_api.py:27`(FINDER)、`:51`；`server/copilot_node.mjs:34`、`:98`；`ui/control_server.py:581` | 抄作业筛选链断（HTTP + Node 门面 + UI 三处） |
| `tools/maa_job_fallback.py` | `network/copilot_api.py:28`(RUNNER)、`:72`；`server/copilot_node.mjs:35`、`:115` | 降级重试链断 |
| `tools/maa_operbox.py` | `network/copilot_api.py:40` | 只是报错文案里提了一句（**非程序依赖**，如实标注） |
| `tools/gen_push_ui.py` | `ui/control_server.py:42`、`:704` | 推图页面生成器死 |
| `tools/gen_mini_ui.py` | `ui/control_server.py:470` | 极简 UI 死 |
| `tools/gen_push_index.py` | `ui/control_server.py:685` | 多账号总览页死 |
| `tools/push/auto_push.py` | `ui/control_server.py:67-68` | 全自动推图主体死 |
| `tools/box_read_when_free.py` | `ui/control_server.py:190` | 读干员箱死 |
| `tools/scan_box.py` | `ui/control_server.py:496` | 扫 box 死 |
| `tools/run_job.py` | `ui/control_server.py:519` | 起 MAA 跑作业死 |
| `tools/screen_watch.py` | `ui/control_server.py:656`、`:666`（并读 `tools/watch/<账号>/latest.png`，`:661`） | 最新画面接口 404 |
| `tools/mas_dashboard.py` | `tools/mas_dash.sh:16`（`SRC='$HERE/mas_dashboard.py'`）→ `:27` base64 上传到**目标机** `E:\mas_dashboard.py` 执行 | MAS 看板在目标机跑不起来 |
| `tools/auto_mas_export.py` | `tools/导出账号信息.bat:7` | 账号导出表格死 |
| `tools/auto_mas_hook.py` | `tools/hook.bat:6`（MAA 完成钩子） | MAS 完成钩子静默失效（存档断） |
| `tools/rogue_host_probe.ps1`、`tools/deploy_host_file.mjs` | `tools/rogue_fleet_probe.mjs:81-84` | 主机只读采样脚本无法现部署 |

### 5.4 Node 侧子进程字符串路径

| tools 文件 | 调用方:行 | 后果 |
|---|---|---|
| `tools/auto_mas_rerun.py` | `server/maaorch-web.mjs:864`（--accounts）、`:870`（--cleanup） | Web 门面的 MAS 补跑/清理 500 |
| `tools/auto_mas_toggle_user.py` | `server/maaorch-web.mjs:877` | 账号启停 500 |
| `tools/prts_job_finder.py` | `server/copilot_node.mjs:34`、`:98`、`:222`（existsSync 自检） | 作业筛选 500 |
| `tools/maa_job_fallback.py` | `server/copilot_node.mjs:35`、`:115`、`:223` | 降级重试 500 |
| `tools/event_sweep.mjs` 等 27 个脚本路径 | `tools/event_sweep.mjs`、`tools/prime_sweep.mjs`、`tools/rank_service.mjs`、`tools/run_campaign.ps1` 内的 `run(PY,['tools/xxx.py'])` | 战役链整体断（详见 5.6） |

### 5.5 Windows 计划任务（实测 `Get-ScheduledTask`，Actions 里写死 D:\1-2\MAAOrch\tools\...）

| 任务 | 状态 | Action | 牵动的 tools 文件 |
|---|---|---|---|
| `MAAOrch-RogueFleet` | **Ready**（最近运行 2026-09-26 15:24，下次 15:26） | `wscript run_hidden.vbs rogue_fleet_launch.ps1` → `node tools\rogue_fleet_watch.mjs --apply` | `run_hidden.vbs`、`rogue_fleet_launch.ps1`、`rogue_fleet_watch.mjs`、`lib/rogue_plan.mjs`、`lib/rogue_judge.mjs`、`rogue_fleet_probe.mjs`、`rogue_host_probe.ps1`、`deploy_host_file.mjs`、`rogue_plan_selftest.mjs` |
| `MAAOrch-MasWatch` | Disabled | `pythonw tools\mas_watch.py` | `mas_watch.py` |
| `MAAOrch-WebUI` | Disabled | `wscript run_hidden.vbs maaorch_web_launch.ps1` | `maaorch_web_launch.ps1`、`run_hidden.vbs`、`mas_web_ui.bat` |
| `MAAOrch-Rogue0Watch` | Disabled | `wscript run_hidden.vbs rogue0_watch_launch.ps1` | `rogue0_watch_launch.ps1`、`rogue0_watch.mjs` |
| `MAAOrch-MpeLocalBridge` | Disabled | `wscript run_hidden.vbs mpelb_launch.ps1` | `mpelb_launch.ps1`、`mpelb_debug.mjs` |
| `MAAOrch-HostPerf` | Disabled | `pythonw tools\host_perf.py --once` | `host_perf.py` |
| `MAAOrch-HostWatchdog` | Disabled | `pythonw tools\host_watchdog.py --once` | `host_watchdog.py` |

⚠️ 这些任务是 `pythonw.exe` / `wscript.exe` 无窗口启动：**路径写错不会报错，只会静默不干活**。迁目录必须同步改 7 个任务的 Actions。

### 5.6 常驻进程与无人值守入口

- `tools/maaorch_service.py --port 8765`：**本快照时正在运行**（PID 8240，`python tools/maaorch_service.py --port 8765`）；文档 `docs/UNATTENDED_RUN.md:11` 把它列为前置条件；`tools/run_campaign.ps1:36` 负责拉起。它 import `_dev / fastnav / panel_gate / stage_boxes` 并 spawn `maa_frame`、`mt_nav`，闭包见 (a)。
- `ui/control_server.py`：由 `server/maaorch-web.mjs` 按需拉起（Python 上游），5.3 表里 9 个 tools 脚本是它的硬依赖。
- `tools/event_sweep.mjs`（2420 行）+ `tools/prime_sweep.mjs` + `tools/run_campaign.ps1`：无人值守打关链，内部用 `run(PY,['tools/xxx.py'])` 子进程调用 **27 个** tools 脚本（名单见上一轮审计 `docs/audits/2026-09-26_prune_round2_audit.md` §5.1，本轮复核仍然成立）。

### 5.7 裸模块名 import 面（上一轮'零引用'判定失真的根因）

实测：**96 个 tools 脚本里有 158 处 `sys.path.insert/append`**，绝大多数是把 `tools/` 自己塞进 `sys.path`，随后用**裸模块名**互相 import（`import viz_plan`、`from mt_nav import ADB, DEV`）。

本次扫描共解析出 **152 条** tools→tools 的 import 边，其中 **73 处是函数内惰性 import**（只在特定分支触发）。按'文件名/路径扫引用'的方法，这些边**一条都看不见** —— 这正是上一轮把 `viz_plan / annotate_live / stage_cut / main_nav / mt_nav` 判成'零引用'的原因。

### 5.8 数据/产物路径依赖

- `tools/watch/<账号>/latest.png`、`tools/watch/<账号>/log.txt`：`screen_watch.py` 写、`ui/control_server.py:661` 读。
- `tools/push/push_state_*.json`：推图状态，`tools/push/auto_push.py` / `gen_push_ui.py` 读写。

### 5.9 历史悬空引用（非本次引入，登记备查）

- `tools/rank_service.mjs:130,132` 引用 `tools/train_ranker.py` —— 该文件**不存在**。
- `infrastructure/utils.py:1` 有 UTF-8 BOM，`ast.parse` 报 invalid non-printable character（`compileall` 通过，非本轮引入）。

## 6. 必须与代码分开处置的非代码产物（共 302 个文件 / 80.6 MB）

| 类别 | 个数 | 体积 | 主要成员 | 处置建议 |
|---|---|---|---|---|
| 截图 .jpg/.png | 251 | 74.8 MB | tools/watch/<账号>/hist/*.jpg（248 张）、tools/watch/<账号>/latest.png | 是 screen_watch.py 的**产物**不是源码；建议把输出根改到 data/watch/（见 §7.4），历史件归档 |
| .bak / .bak-* | 8 | 0.5 MB | event_sweep.mjs.bak(2157 行)、.bak-settle-*(1613/2157)、.bak-mas-20260924_1516(1358)、rogue_plan.mjs.bak-mas-1814、rogue_plan_selftest.mjs.bak-mas-1814、run_campaign.ps1.bak*×2 | 纯历史快照。唯一例外是 rogue_plan.mjs.bak-mas-1814（docs/ROGUE_WATCH.md:479 登记为回滚点）——保留它 |
| 日志 | 8 | 4.1 MB | _asst9.log（24,782 行 / 4.2 MB）、_aaa.log、_mk.log、watch/*/log.txt | 归档或删除，可再生产 |
| 数据 json dump | 26 | 0.5 MB | _queueconfig.json(2,050 行)、_scripts_table.json(1,106)、_history_*.json×10、_queue_full_dump.json、_restore_map.json、_emustatus.json | 多数是一次性 dump。注意：tools/push/push_state_*.json 是推图状态（auto_push.py/gen_push_ui.py 读写），属 (a) 数据，别清 |
| 文本/表格 | 9 | 0.7 MB | _scripts_full_table.md、maa_task_times_7d.csv、maa_channel/deploy_one_liner.txt、maa_all/README.md、push/README.md | push/README.md 被 push/auto_push.py 等引用，保留；其余归档 |

## 7. 建议的迁移/兼容方案

### 7.0 前置结论

**不要整体迁出 tools/，也不要整体 git mv。** 一次移动会同时打断：3 处进程内 import + 1 处 importlib 文件加载 + 16 处产品侧 python 子进程路径 + 4 处 node 子进程路径 + 7 个计划任务 + 1 个**正在运行**的常驻服务（8765）的全部依赖。

### 7.1 步骤 1 —— (c) 42 文件 / 5,887 行整体下沉到 tools/_archive/（低风险，可先做）

- (c) 的定义就是：产品代码、其他 tools 代码、计划任务、启动器全部零强引用，剩余提及都是注释 —— **纯移动即可，不需要垫片**。
- 保持相对结构（_archive/push/...、_archive/scripts/...），便于回滚。
- 唯一要同步的：docs/、plans/ 里对这些脚本的路径提及（只影响人照着敲命令；本报告 §4 已给出每个文件的唯一提及处，可照改）。
- 不要动 rogue_plan.mjs.bak-mas-1814（回滚点）与 push/push_state_*.json（数据）。

### 7.2 步骤 2 —— (b) 86 文件下沉到 tools/workbench/（中风险，必须配垫片）

(b) 内部仍有**裸模块名 import**（import workspace、import viz_plan …）。一旦它们不在 tools/ 根，sys.path.insert(0, tools/) 那条老路就找不到，必须二选一：

**方案 B1（推荐，改动最小）**：为每个被 (a)/(b) 内部裸名 import 的 (b) 文件，在 tools/ 根留一个**两行垫片**，例如 tools/viz_plan.py：

```python
from workbench.viz_plan import *          # noqa: F401,F403
from workbench.viz_plan import __doc__    # noqa: F401
```

需要垫片的文件 = (b) 中被 (a)/(b) 裸名 import 的那些（本次扫描可精确给出名单）；其余 (b) 文件可直接移动。

**方案 B2（更干净但改动大）**：把所有裸名 import 改成包式 from tools.workbench import viz_plan，并删掉 158 处 sys.path.insert 里的 tools/ 条目。工作量大、易漏，建议作为后续独立任务。

⚠️ 垫片救不了 .mjs/.bat/.ps1/.sh 里的**子进程路径**（run(PY,["tools/nav_map.py"]) 仍指旧路径），必须逐条改 —— 名单见 §5.3/§5.4。

### 7.3 步骤 3 —— (a) 原地不动，只做可搬迁化改造

1. services/mas_l3.py:49-58 的 tools/mas_watch.py 路径改成可配置（env MAAORCH_MAS_WATCH 优先，缺省仍指 tools/mas_watch.py）—— services/mas_adapter.py:5 已声明将来物理搬迁只动这一层。
2. services/ai_agent/percept.py:77/761 的 sys.path.insert(0, tools_dir) + import task_graph / screen_understand 是最脆的一环：建议由 tools/ 提供稳定包名，或在 services/ai_agent/ 下做薄封装，避免 Harness 直接依赖 tools/ 这个目录名。
3. server/maaorch-web.mjs:864/870/877、server/copilot_node.mjs:34/35 把脚本写成相对 ROOT 的字符串；建议收敛到文件顶部常量（copilot_node.mjs 已这么做），便于一次性改路径。

### 7.4 步骤 4 —— 产物与数据出 tools/

- screen_watch.py 的输出根由 tools/watch/ 改为 data/watch/（同时改 ui/control_server.py:661 的读取路径）；248 张 jpg 与日志从此不再污染源码树。
- tools/_asst9.log、_aaa.log、_mk.log、_history_*.json 一并清出。

### 7.5 步骤 5 —— 计划任务与启动器

- 若 run_hidden.vbs / *_launch.ps1 位置不变（建议不动），7 个计划任务无需改；MAAOrch-MasWatch / HostPerf / HostWatchdog 直接写了 tools\xxx.py 绝对路径，但脚本都在 (a) 原地不动，因此也不受影响。
- 建议把这三个任务也改成走 wscript run_hidden.vbs <launcher>.ps1 间接层（与 RogueFleet 一致），以后路径改动只动 ps1。

### 7.6 验收清单（每步落盘后跑）

```powershell
cd D:\1-2\MAAOrch
python -m compileall -q .        # 必须 0 error
python -c "import network.mcp_server; print(1)"
python -c "import services.mas_l3, services.mas_adapter; print(services.mas_adapter.snapshot())"
python -c "import services.ai_agent.percept, services.ai_agent.meta_agent; print(1)"   # 进程内 import 面
Get-ScheduledTask -TaskName MAAOrch* | ForEach-Object { $_.TaskName + " " + $_.State }
```

- 另外各起一次：python tools/maaorch_service.py --port 8765、node server/maaorch-web.mjs（或只确认 PID 8240 仍在）。

## 8. 方法学、已知假阳性与遗留风险

1. **上一轮失真的根因已消除**：96 个 tools 脚本 / 158 处 sys.path.insert + 裸模块名 import，本轮用 AST + 三键索引（basename/stem/相对路径）全部吃到；只按文件名扫引用会漏掉全部这类边。
2. **已剔除的假阳性**：tools/state.py、tools/runs.py、tools/dashboard.py 在 ui/web/*.js 与 services/*.py 里的命中几乎全是同名变量（state / runs / dashboard）——本轮要求路径边或导入边才算引用。
3. **已知漏报风险（本文可能低估依赖）**：
   - 动态拼接：os.path.join(ROOT, "tools", name) 中 name 来自变量或配置，正则与 AST 都看不见；
   - 远端执行：deploy_host_file.mjs / mas_dash.sh 把本地文件搬到目标机再执行，目标机上的路径依赖不在本仓库；
   - runtime/、.opencode/、.mimosa/、logs/、data/、debug/ 被排除在扫描外（生成物/第三方），其中的引用未纳入；
   - tools/maa_channel/deploy_one_liner.txt 是 base64 载荷（内嵌另一份 channel 源码），未解码比对。
4. **本文只出清单，未删任何文件**：快照时刻 tools/ 全树 506 文件、代码文件 204、.py 165 个 / 42,422 行；除本文件外未对仓库做任何增删改。

## 9. 一句话给 Lead

- **(a) 76 文件 / 21,227 行**：不能动 —— 动了就是 ImportError / 子进程 500 / 计划任务静默失效。
- **(b) 86 文件 / 23,092 行**：可下沉到 tools/workbench/，但必须配裸名 import 垫片 + 改子进程路径。
- **(c) 42 文件 / 5,887 行（.py 5,218 行）**：真零引用，可以今天就整体下沉，回收 tools .py 行数的 12.3%，风险接近 0。
- 另有零风险回收：251 张截图 + 8 个 .bak + 8 个日志 + 26 个 json dump（≈ 80.0 MB，共 302 个文件），以及 2 处登记项（tools/train_ranker.py 悬空引用、infrastructure/utils.py 的 BOM）。

