# MAS 按场景发车 —— 受控调配器（草案）

> 卡片：kanban `t_6871efaa`（board `maa-parallel-benchmark-20260926`）| 生成：2026-09-27 | 性质：**草案**
> 工具：`tools/mas_scenario_dispatch.py`（`--selftest` 44/44；三场景 dry-run 实测 GO）
> **默认 dry-run，零写请求；真发车双闸闭合（`--apply` ＋ `MAAORCH_SCENARIO_APPLY=1`），待用户批准。**

## 1. 一句话

把「按场景发一局肉鸽 / 一轮活动刷取 / 剿灭」变成一条受控命令：
**只读规划 → 备份 → 写场景 → 发车（AutoProxy）→ 审计 → 按时还原**，全程可审计、可回滚、默认不写。

```bash
# 只看计划（默认；不触达任何写请求）
python tools/mas_scenario_dispatch.py --script 明日方舟-官-1-1-7 --scenario 肉鸽 --times 1 --window 21:30-22:30
python tools/mas_scenario_dispatch.py --instance 13 --scenario 活动 --set Task.ActivityStageIndex=3 --window 21:30-22:30
python tools/mas_scenario_dispatch.py --scenario 剿灭 --instance 13 --window 21:30-22:30 --json   # 机读输出
python tools/mas_scenario_dispatch.py --status                 # 运行台账
python tools/mas_scenario_dispatch.py --reconcile              # 到期该还原谁（dry）
python tools/mas_scenario_dispatch.py --list-scripts           # 脚本名 ↔ 实例号
```

真发车 = 在窗口内再加 `--apply`，且环境变量 `MAAORCH_SCENARIO_APPLY=1`（**草案阶段两者缺一即拒**，exit=1）。

## 2. 目标与范围

- **目标**：AUTO-MAS 路线 B（54 号日常机队）——“按场景发一局/一轮”的受控入口。
- **不做**：MAAOrch 队列任务调度（已有 `tools/mas_dispatch.py`）、fz-maa 肉鸽机队看守/发车（已有 `tools/rogue_fleet_watch.mjs`）、无人批准的自动发车。
- **目标用户** = 目标脚本的**全部启用用户**（AutoProxy 以 scriptId 发车，无单用户入口；要只跑一个号须另建/改用单号脚本，或先停用其余号——另行批准）。
- 场景别名：`rogue|肉鸽|rl`、`event|活动|activity`、`annihilation|剿灭|anni`。

## 3. 写面（三写一车）

| # | 写面 | 位置 | 内容 | 证据 |
|---|---|---|---|---|
| 1 | 场景开关 | `POST /api/scripts/user/update`（data.`Task`） | 10 键显式全写：场景开关=1、其余=0（`TASK_KEYS` 见 `tools/mas_startup_verify.py`） | `AutoProxy.py` L417-425（Routine 按 `Task.If*` 取；Annihilation 轮仅 StartUp+Fight）、L837-844（`set_maa` 强制 StartUp=true 并逐项应用 `IsEnable`） |
| 2 | 配置任务项 | `data/<sid>/<uid｜Default>/ConfigFile/gui.new.json` | 备份（scp 下载 + 主机侧 `.bak-*`）→ JSON 合并 → 回传 | 布局 `AutoProxy.py` L273/L555/L563（详细=per-uid；简洁=Default） |
| 3 | 发车 | `POST /api/dispatch/start` | `{"taskId": <scriptId>, "mode": "AutoProxy"}`（只认队列 uid 或脚本 uid） | 手册 §3.3；主循环自开模拟器→`set_maa`→跑 MAA→等待（`AutoProxy.py` L411-499 一带） |
| 4 | 还原 | 到期 `reconcile` | `Task` 写回原值 + ConfigFile scp 回传；运行中被锁 ⇒ 拒绝并重试（默认 12×30s） | 本工具 |

**ConfigFile 合并规则**（`merge_taskqueue`）：

1. 命中：模板项 `Name` 非空 ⇒ 先按 `(TaskType, Name)` 精确命中（同类型多条靠 Name 定位，如 Fight 型的「剿灭作战/理智作战/活动关优先」）；未命中退回首条同 `TaskType`（摘要记 `how`）。
2. **不改的不写、缺项不新增**（不臆造字段）；命中项 `IsEnable` 强制 `true`。
3. **切号字段绝不注入**：`AccountName` / `AccountSwitchEnabled` 一律跳过并记审计（`skipped_policy`）——切号语义归 AutoProxy 注入策略 + `tools/mas_switch_gate.py` 闸门（官 masked+true / 非官 empty+false）。

## 4. 闸门（apply 前必须全 GO）

| 闸门 | 判据 | 备注 |
|---|---|---|
| G1 输入 | 场景可解析；`--script`/`--instance` 能定位脚本；窗口在现在内；脚本有启用用户 | 解析不到 ⇒ 硬拒 |
| G2 模板 | `runtime/templates/maa/<场景>.json` 存在且可解析（或 `--template`） | 肉鸽模板若为 6.17.5（`CoreChar`）形态 ⇒ **WARN**（目标 MAA_1..12 为 6.18.0 `StartingOpers`，直接注入会丢核心干员） |
| G3 双闸 | `--apply` ＋ `MAAORCH_SCENARIO_APPLY=1`；`MAAORCH_SCENARIO_NO_GO=1` 一票否决 | 草案默认关 |
| G4 让位区 | 窗口与 **04:00–05:33 / 18:30–20:30** 无交集 | 闭区间同口径 `tools/lib/rogue_plan.mjs`（20:30 仍在区内，20:31 起出区）；跨零点窗口按天展开 |
| G5 preflight | `tools/mas_preflight.py --script <名> --json`：exit 0=GO / 1=NO-GO / 2=无法判定（**fail-closed**） | 产物未落地默认不过；`--allow-no-preflight` 显式放行（记审计）。`--offline` 时跳过 |
| G6 现场 | AUTO-MAS 可达（`/api/scripts/get`）+ 目标 MAA 目录空闲（Win32_Process 复核） | 目标目录有 MAA 在跑 ⇒ 硬拒（改前它会把旧配置写回盘）；SSH 探测失败在 apply 时 fail-closed |
| G7 单飞 | 同脚本已有 open 运行 ⇒ 拒；同日同场景已跑过 ⇒ 拒（`--force` 显式覆盖） | 台账见 §6 |

## 5. 场景 × 模板 × 次数

| 场景 | 模板（t_c60939cd 产物） | 开关集（显式全写） | `--times` 落点 | 备注 |
|---|---|---|---|---|
| 肉鸽 | `roguelike.json`（StartUp+Roguelike，2 条） | IfStartUp + IfRoguelike | `TaskQueue.Roguelike.StartCount`（**发一局=1**；模板默认 99999=连续刷） | 肉鸽参数只能落在 ConfigFile 条目（AutoProxy 只改开关，不改肉鸽参数） |
| 活动 | `activity_priority.json`（Fight，1 条） | IfStartUp + IfFight + IfActivityFirst | 未定义 ⇒ WARN；用 `--set Task.ActivityStageIndex=N` / `Task.ActivityMedicineNumb=N` | 活动关名由 AUTO-MAS 下拉解析（`AutoProxy.py` L594-604、L830）；生成的「活动关优先」插在 StartUp 之后、理智作战之前（L904-908），药额度与理智作战**互不转移**；**前置条件：存在进行中活动**（MAA `StageActivityV2` 列表非空），活动过期后该任务无意义（t_b8476841 TL;DR #2） |
| 剿灭 | `annihilation.json`（Fight，1 条） | IfStartUp + IfFight | 未定义（**次数由 AUTO-MAS 剿灭轮自行编排**：`Info.Annihilation`（Close 不跑）+ `AnnihilationStartWeekday` + `Data.AnnihilationCompletedWeek`（ISO 周，周内完成自动记账）——本工具不控） | AutoProxy 一次任务=先剿灭轮（若本周未完成）后场景轮——上游行为，窗口要含这轮成本（t_b8476841 TL;DR #3） |

## 6. 状态 / 审计 / 回滚

- 状态：`runtime/scenario_dispatch_state.json`（`runs[run_id]`，status ∈ open/restored/failed）。
- 审计：`runtime/scenario_dispatch_log.jsonl`（plan/backup/write/dispatch/restore/refuse 各一行）。
- 备份：`runtime/scenario_dispatch/backups/<run_id>/`（`task.json`、`users.json`、`configfiles/*.gui.new.json`、`configmap.json`、`meta.json`）；主机侧另有 `gui.new.json.bak-mas-scenario-<ts>`。
- 凭据红线：落盘前 `scrub()` 递归剔 `password/token/secret/cookie/credential/skland`。
- 回滚：① 正常走 `--reconcile --apply`（到期自动写回）；② `--restore-run <run_id> [--apply]` 手动还原；③ 单文件回滚 = 备份目录里那份 + 主机 `.bak`；④ 紧急一票否决 `MAAORCH_SCENARIO_NO_GO=1`（一切写被拒）。

## 7. 已知限制与未验证项（诚实清单）

1. **真发车（apply）从未实跑**——草案双闸默认关；apply 路径经静态走查 + 44 项自测覆盖，未见真实 `POST /api/dispatch/start`。
2. AutoProxy 同一次任务会**先跑剿灭轮**（若本周未完成）再跑场景轮——上游行为，本工具不改变；排窗要含这轮时间/理智成本。
3. 肉鸽模板眼下是 **6.17.5 形态**（`CoreChar`），AUTO-MAS 的 MAA_1..12 为 6.18.0（`StartingOpers`）⇒ G2 会 WARN，注入前按目标版本选形态（防「静默丢核心干员」）。
4. ConfigFile 布局（详细=per-uid / 简洁=Default）源自源码 L273/L555/L563；`Mode` 字段缺失时现场探测（谁存在用谁），探不到则 apply 拒绝（不猜）。
5. 活动轮参数（`ActivityStageIndex`/`ActivityMedicineNumb`）语义引自源码 L594-604/L830；「活动关优先」任务的实际 `StagePlan` 覆盖顺序未在真机验证（模板 README 与 t_b8476841 手册为准）。
6. 还原按**时间窗到期**触发；若运行没跑完，`Task` 写回会被 AUTO-MAS 锁拒绝并重试——窗口请盖住预期运行时长（肉鸽单局参考 10–30 分钟/号，手册 §4.1）。
7. preflight 的第 4 项会对**运行中的实例**截图 OCR；只在准备发车的时刻跑它（G5 未做节流，dry-run 也会跑一次——`--offline` 可跳过）。
8. 本工具只碰 AUTO-MAS；fz-maa 机队（6.17.5）的肉鸽发车仍走 `schtasks`/看守（手册 §4），两边共用模拟器，互斥由让位区 G4 与各自看守保证。
9. **相邻路线（t_b8476841 §5.2/§5.3/§5.4，选用时以其为准）**：日常链内解决首选「计划表 Stage=活动关 / `IfActivityFirst`」不新增发车；确需“冲活动”额外发车时建议用 `临时-补跑-…` 队列挂 `mas_dispatch.py`（**不要给 AUTO-MAS 重开定时项**，回滚链会分叉）。本工具（按需写用户配置 + 到期还原）是并列的第二条路线，适合「就用地利既有脚本/号，短窗跑一发」。硬规则照旧：不抢实例、排在队列之后、让位区不发车。

## 8. 启用清单（用户批准后照此做）

1. 审本文件 + `runtime/templates/maa/*.README.md` + 手册 §3.3/§4。
2. 选一个**测试脚本**（建议先用单号脚本），`--apply` 前先把同一个计划 dry-run 打印给用户过目。
3. 设置 `MAAORCH_SCENARIO_APPLY=1`（仅当次命令生效：bash 下 `MAAORCH_SCENARIO_APPLY=1 python tools/... --apply …`）。
4. 发车后 5 分钟验证（肉鸽）：`gui.log` 出现 `完成任务: 开始唤醒` → `开始任务: 自动肉鸽` → `已开始探索 N 次`；链结局看 `TaskChainCompleted`（手册 §5.1）。
5. 到期 `--reconcile --apply` 还原，再 `--status` 核对 `restored`。
6. 全程留痕在 §6 两个文件里；异常先 `MAAORCH_SCENARIO_NO_GO=1` 止血再查。

## 9. 证据索引

- 本机 AUTO-MAS 源码副本：`D:\MAS\app\task\MAA\AutoProxy.py`（行号见 §3/§5；`MAA_TASKS` 在 `app/utils/constants.py:57`）。
- 模板库：`runtime/templates/maa/README.md` + `roguelike|annihilation|activity_priority|daily_official|expiring_medicine`.README.md（t_c60939cd）。
- 班前自检：`tools/mas_preflight.py`（t_cd11ca49；`PREFLIGHT=GO/NO-GO`、exit 0/1/2）。
- 手册/审计：`docs/MAA_ROGUELIKE_PLAYBOOK.md` §3.3/§4/§5；`docs/audits/2026-09-27_mas_task_modes.md`。
- 既有模式先例：`tools/auto_mas_rerun.py`（临时队列+清理）、`tools/auto_mas_toggle_user.py`（用户配置写面）、`tools/mas_switch_gate.py`（切号口径）、`tools/mas_dispatch.py`（发车/对账/状态骨架）。
- 本工具实测（2026-09-27）：`--selftest` 44/44；`pytest tests/test_mas_scenario_dispatch.py` 14/14（含「dry-run 不触 API」「写端点白名单」「双闸默认关」三条不变量）；三场景 dry-run 全闸门 GO（含 G5 preflight=GO）；`--apply` 无 env ⇒ exit=1「G3 双闸」拒绝，零写请求。
