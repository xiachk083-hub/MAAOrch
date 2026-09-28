# MAA 活动/限时刷取 Playbook —— Fight 字段 · 活动关写法 · 剿灭 · 生息演算 · 仓库维护

> 任务：kanban `t_b8476841`（board `maa-parallel-benchmark-20260926`，create 者 dsh-lead）
> 撰写：mas（Hermes），2026-09-27。**只读调查**：未改任何配置、未发车、未动实例；下文所有远端行号/日志为调查时刻实测。
> 版本锚点：**MAA v6.18.0**（GitHub `ref=v6.18.0`）、**AUTO-MAS v5.4.0**（远端 `E:\AUTO-MAS`，含 2026-09-27 在线热修，行号按热修后文件）、本仓 `D:\1-2\MAAOrch`、MAA 实例 `E:\MAA-Multi\MAA_1..MAA_12`。

来源速查：
- MAA WPF 源码：`https://raw.githubusercontent.com/MaaAssistantArknights/MaaAssistantArknights/v6.18.0/src/MaaWpfGui/...`
- AUTO-MAS 源码（远端）：`E:\AUTO-MAS\app\controllers\auto_proxy.py`、`app\utils\constants.py`、`app\core\config.py`、`app\models\config.py`
- 本仓：`data/stage_apcost_v3.json` / `stage_apcost_v4.json`、`tools/mas_dispatch.py`、`docs/ROGUE_WATCH.md`、`docs/DEPOT_PLANNING.md`
- MAA 活动数据源（公共 API）：`https://api.maa.plus/MaaAssistantArknights/api/gui/StageActivityV2.json`

---

## 0. TL;DR（给编排人）

1. **活动关刷取有三条实现路径**（按顺位选用）：
   ① 计划表 `Stage` = 活动关代码（全队现状：`SR-7`，46 个号）；② `Task.IfActivityFirst=true` + `ActivityStageIndex=N` → AUTO-MAS 生成独立「活动关优先」任务（有独立理智药额度 `Task.ActivityMedicineNumb`）；③ 库存保持（`DepotMaintain`）计划，按 `DropId/DropCount` 缺口自动补。
2. **活动关闭后不改 Stage = 理智作战静默跳过**。MAA 选关是"从上往下第一个**开放**关卡"，活动过期后该关 `IsStageOpen=false` → 整条理智作战被跳过（信息级日志 `FightSkippedNoOpenStage`），只剩「剩余理智」任务在刷 1-7。
   - **现状正是如此（2026-09-27）**：`SR`（月行水上）9/18 03:59 已结束，MAA 服务器对 Official/txwy 的进行中活动列表为空（`Info=0`）；全队 `IfActivityFirst=false`，46 个号 `Stage=SR-x` 仍挂着 → 4 台实例 gui.log 实测 `GetFightStage: from ["SR-7"], selected null`（MAA_1/6/9/12）。理智仍被「剩余理智」1-7 消耗，无停机事故，但理智作战产出为 0。
3. **剿灭不是独立发车**：它是每个用户代理链的**前置模式**（先 `Annihilation` 后 `Routine`）；是否跑由 `Info.Annihilation`（`Close` 则不跑）+ `AnnihilationStartWeekday` + `Data.AnnihilationCompletedWeek`（ISO 周）决定。周内完成自动记账，下周自动恢复。
4. **限时活动插什么位置**：日常链内部不需要新增发车 —— 「活动关优先」被 AUTO-MAS 插在 `StartUp` 之后、`理智作战` 之前（AutoProxy L907-908）；若确需"冲活动"额外发车，见 §5（12 槽位 + 让位区）。
5. 红线：**不改配置、不发车、不动实例**（本 playbook 只授权阅读与编排建议；任何改动走受控流程：备份 → 改 → gui.log 复核）。

---

## 1. FightTask（理智作战）字段表 —— MAA GUI v6.18.0

> 定义：`src/MaaWpfGui/Configuration/Single/MaaTask/FightTask.cs` L28-164（字段 + `OnDeserialized`）；
> 序列化：`ViewModels/UserControl/Settings/FightSettingsUserControlModel.cs` L1662-1784（`ISerialize.Serialize`）；
> 下发模型：`src/MaaWpfGui/Models/AsstTasks/AsstFightTask.cs` L24-131（JSON 字段名以这里为准）。

| 字段（配置键） | 默认 | 语义 / 取值 | 下发（core JSON） |
|---|---|---|---|
| `StagePlan` | `[""]` | 关卡串，**从上往下第一个开放关**被选中；`""`=当前/上次 | `stage` |
| `UseOptionalStage` | false | 备选关卡：允许关卡列表保留失效关卡（配合 `StageResetMode=Ignore`）；false 时 `OnDeserialized` 把 StagePlan 收窄为 1 项 | — |
| `IsStageManually` | false | 手动输入关卡（跳过列表联动） | — |
| `StageResetMode` | `Current` | 列表里不存在的关卡：`Current`=重置为"" / `Ignore`=保留 | — |
| `HideUnavailableStage` | false | 隐藏当日不可用关卡（UI 列表） | — |
| `Series` | 0 | 连战次数；`0`=自动。仓库/库存等用 1 | `series` |
| `EnableTimesLimit` / `TimesLimit` | false / `int.MaxValue` | 次数上限；`EnableTimesLimit==false` → `times=int.MaxValue`（不限） | `times` |
| `UseMedicine` / `MedicineCount` | false / 0 | 用药开关与**最多**吃药数；`UseMedicine!=false ? MedicineCount : 0` | `medicine` |
| `UseExpiringMedicine` / `MedicineExpireDays` | false / 2 | 只吃 N 天内过期药（默认 2 天） | `medicine_expire_days` |
| `UseExpireMedicineForActivity` | false | 活动 <2 天结束且勾选时，临期药窗口**拉长到本周结束（鹰历）**：`max(expireDays, daysUntilEndOfWeek)`（L1324-1327、L1817-1823） | `medicine_expire_days`（取 max） |
| `UseStone` / `StoneCount` | false / 0 | 碎石上限；`UseStoneAllowSave=false` 时反序列化强写 `UseStone=false`（L156-159） | `stone` |
| `UseStoneAllowSave` | false | 允许保存碎石状态（UI 弹高风险确认，L1056-1078） | — |
| `EnableTargetDrop` / `DropId` / `DropCount` | false / ""/ 0 | 指定材料与目标数量；仅在启用且 `DropId` 非空且有效数量>0 时加入 `drops` | `drops: {itemId: qty}` |
| `IsInventoryTarget` | false | 指定材料**按库存目标计算**：运行时用最新仓库数重算缺口 `need=DropCount-当前库存` | （影响 `drops`/`times`） |
| `IsDrGrandet` | false | 博朗台模式（理智用尽前省药留最后 1 点血量用药的"抠门"模式） | `DrGrandet` |
| `UseCustomAnnihilation` / `AnnihilationStage` | false / "Annihilation" | 剿灭关卡自定；仅当 `stage=="Annihilation"` 且开关=true 时替换（L1344-1347） | `stage` |
| `UseWeeklySchedule` / `WeeklySchedule` | false / 全 true | 周计划：当日未勾 → 整任务跳过（L1671-1675） | — |
| （上报）`report_to_penguin`/`report_to_yituliu` | false | 企鹅物流/一图流掉落上报（全局设置驱动） | `report_to_penguin`、`penguin_id`、`report_to_yituliu`、`yituliu_id`、`server`、`client_type` |

**运行期行为（关键）**：
- 选关：`GetFightStage` = `list.FirstOrDefault(IsStageOpen(stage, 今日))`（L1197-1202），日志 `GetFightStage: from [...], selected ...`。
  - 关卡开放判定（`Models/StageInfo.cs` L138-174）：有活动且活动进行中→开放；活动过期且非资源收集→**关闭**；资源关看 `OpenDaysOfWeek`；普通关常开。
- **常驻关/空串阻断**：选中的是常驻关（剿灭/1-7 等）或 `""` 且它后面还有关卡时，打 Warning `PermanentStageBlocksStages`——后续关卡**永远不会被选中**（L1692-1707）。
- 找不到开放关：`FightSkippedNoOpenStage`（Info，静默跳过）/ StagePlan 空报 Error（L1677-1690）。
- 库存目标（`IsInventoryTarget`）：序列化时用 `Toolbox.DepotResult` 重算；达到目标 → `times=0` 跳过；缺 `>=1` → `drops={id: need}`（L1709-1743）；任务**进行中**重算（taskId>0）时若有效数量≤0 → `times=0`。
- 理智不足跳过（库存保持/指定掉落场景）：`Medicine<=0 && Stone<=0` 且临期药窗口已证明耗尽 → 用 `StageApCostHelper.GetApCost(stage)` + "上报时间起 6 分钟 1 点"估算自然回复，不足一次入场 → `times=0`（L1271-1298）。**手输活动关/复刻前缀查不到 apCost 时跳过此判定并记日志**（L1277-1281）。
- 次数与连战不整除警告：`effectiveMaxTimes % Series != 0` → Warning `FightTimesMayNotExhausted`（L1760-1763）。
- 用药上报：core 发 `SubTaskExtraInfo/UseMedicine`（普通/临期分开计数，L1786-1840）。

---

## 2. 关卡书写约定（写入配置的 `code` vs 表内 `stageId`）

两套标识要分清：
- **写入 MAA 配置的是「关卡代码」`code`** = 与游戏内 OCR 显示一致（大小写严格）：`1-7`、`R8-11`、`12-17-HARD`、`SR-7`、`AD-EX-1`、`CE-6`、`GT-1`……核心按 OCR 匹配（例：`Stages/SR.json` 里 `SR-7@SideStoryStage` 的 `text=["SR-7"]` 且有 `ocrReplace: [["5R-","SR-"]]`）。
- **本仓 `data/stage_apcost_v3/v4.json` 记录的是 `stageId`**（游戏内部 ID）+ `ap`/`code`/`type`/`difficulty`/`zone`，用于 AP 计算。`v4` 结构统计（实测）：
  - `type`：`ACTIVITY 2419`、`MAIN 709`、`CLIMB_TOWER 239`、`SUB 87`、`CAMPAIGN 36`、`DAILY 35`、`SPECIAL_STORY 11`、`GUIDE 2`；
  - `difficulty`：`NORMAL 2719`、`FOUR_STAR 774`、`SIX_STAR 45`；
  - `no_cost`：619 组 stageId→消耗映射，取值全为 `0` 或 `-1`。

**stageId 前缀命名族（表内实测）**：
| 前缀 | 含义 | 例 |
|---|---|---|
| `main_XX-YY` | 主线普通关 | `main_01-07`（1-7，ap 6） |
| `easy_XX-YY` | 主线「简化/剧情」模式（无理智） | `easy_10-05`（no_cost 0） |
| `tough_XX-YY` | 主线**磨难**关（85 个） | `tough_10-05`（同 code "10-6"） |
| `hard_XX-YY` | 主线 H 高难关（53 个） | `hard_05-01`（H5-1，ap 10） |
| `st_XX-YY` | 剧情/教学（no_cost 0） | `st_10-01` |
| `sub_XX-Y-Z` | 支线（S 关） | `sub_04-1-1`（S4-1） |
| `aNNN_YY` / `aNNN_fYY` | 早期活动关 | `a001_01`（GT-1）、`a003_f04`（OF-F4，**ap -1**） |
| `actNNside_YY` | 活动正篇关 | `act54side_07`（**SR-7**，ap 21） |
| `actNNside_exYY` 等 | 活动 EX 关 | `act43side_ex01`（AD-EX-1） |
| `actNNsre_*` / `actNNdN_*` | 复刻区 / 复刻（不同 zone） | `act18d3_05`（SV-5 复刻）vs `act10d5_05`（SV-5 原版） |
| `wk_*` / `pro_*` | 资源关 | `wk_melee_6`（CE-6，ap 36）、`pro_a_1`（PR-A-1） |
| `lt_*` / `spst_*` | 爬塔 / 特殊剧情 | `lt_01_01`（LT-1）、`spst_08-01`（EG-3） |

**书写约定要点**：
1. `code` 不唯一：`10-6` → `easy_10-05` / `main_10-05` / `tough_10-05` 三条（普通+磨难同 code）；`SV-5` → 原版+复刻两条。**用 code 查 AP 时要按 zone/difficulty 消歧**。
2. **突袭/额外难度**在表内 = `stageId#f#`（`difficulty=FOUR_STAR`），AP 与普通相同（如 `main_01-07#f#`、`act43side_ex01#f#`）。MAA 的 StagePlan **不接受 `#f#` 后缀**（它是本仓/统计侧的写法），写关仍用 `1-7`、`AD-EX-1`。
3. **磨难关的书写**：MAA GUI 常驻列表里的条目就叫 **`12-17-HARD`**（`StageManager.cs` L675 硬编码；AUTO-MAS 计划表下拉同字符 `constants.py` L437）；本仓表里对应 `code="12-17"` 的 `tough_12-15`（**表内没有 `-HARD` 后缀**，跨工具用时要映射/剥离）。
4. `no_cost` 规则：`ap=0` = 不消耗理智（教学 `st_*`、简化 `easy_*`、`guide_*`、部分 `main_*` 剧情节点的首通）；`ap=-1` = 特殊机制关（如 OF-F4"竞技场"消耗的是活动代币）。**理智作战/库存保持不要选这类关**；用表做耗时估算时按 0 处理。
5. 活动关生命周期：活动结束后该 `code` 在 MAA 侧判为"过期关"（列表画删除线 `IsOutdated`，L1495-1499），序列化选不到 → 整任务跳过（见 §0.2）。**换活动必须同步改 Stage**。
6. AP 数据和 MAA 资源包不同源：MAA GUI 自带的 `resource/stages.json`（实测 2026-09-23 版，803KB）只有 `{apCost, code, dropInfos}`，**grep 不到 `SR-7`/`AT-8`/`HARD` 等新活动关与磨难关后缀**；`StageApCostHelper` 注释明确「手输活动关、复刻前缀返回 null」→ 新活动关的 AP 估算只能依赖本仓 `stage_apcost_v4.json`（这正是本仓表的价值）。

---

## 3. 剿灭（Annihilation）

### 3.1 MAA 侧怎么表达
- **必须是 `FightTask`，`StagePlan=["Annihilation"]`**（选关列表里的"剿灭作战"项）。自定义关卡：`UseCustomAnnihilation=true` + `AnnihilationStage` ∈ 四选一（`FightSettingsUserControlModel.cs` L980-984）：
  - `Annihilation`（当期剿灭）、`Chernobog@Annihilation`（切尔诺伯格）、`LungmenOutskirts@Annihilation`（龙门外环）、`LungmenDowntown@Annihilation`（龙门市区）。
- 替换逻辑：序列化时 `stage=="Annihilation" && UseCustomAnnihilation` → 用 `AnnihilationStage`（L1344-1347）。
- core 资源（`resource/tasks/tasks.json` 实测）：`Annihilation` 任务 doc =「剿灭作战，一般本周的满了也不会有人再刷的，所以只识别右上角 ToDoList 里的剿灭就可以了」，其 `next` 链含 `Annihilation@UnableToAgent2`（**代理不可用 = 直接结束**）；进度口头禅 `剿灭模式 X/Y`。
- 剿灭**按周计额度**；外观面：MAA 每次剿灭结束都会打印「完成任务: 剿灭作战」。

### 3.2 AUTO-MAS 侧（`app/controllers/auto_proxy.py`，v5.4.0 在线版行号）
- **用户配置**：`Info.Annihilation` ∈ {`Close`, `Annihilation`, `Chernobog@Annihilation`, `LungmenOutskirts@Annihilation`, `LungmenDowntown@Annihilation`}；`Info.AnnihilationStartWeekday`（默认 `Monday`）；`Data.AnnihilationCompletedWeek`（默认 `"2000-W01"`）。
- **本次是否跑**（L353-372）：
  ```python
  self.run_book = {"Annihilation": user.Info.Annihilation == "Close", "Routine": False}
  if not self.run_book["Annihilation"]:
      if not _should_run_annihilation(start_weekday, completed_week, now):  # L94-103
          self.run_book["Annihilation"] = True   # 跳过；日志「本次跳过剿灭：开始日=…，本周记录=…」
  ```
  判定（L94-103）：`completed_week == _current_week_marker(now)` → 不跑；否则 `now.weekday() >= getattr(calendar, start_weekday.upper())`。
  周标记（L86-91）：`f"{iso_year:04d}-W{iso_week:02d}"`（`now.isocalendar()`，时区 **UTC+4**，即 CN 每日 04:00 刷新边界）。
- **执行顺序**（L463-480）：`for self.mode in ["Annihilation", "Routine"]` —— 先剿灭模式再日常模式；剿灭模式 `task_dict` 只有 `StartUp + Fight`。
- **下发参数**（L799-810）：`MAA_ANNIHILATION_FIGHT_BASE`（`constants.py` L150-190：`StagePlan=["Annihilation"]`、`UseCustomAnnihilation=True`、`AnnihilationStage="Annihilation"`、`TimesLimit=999`、`UseExpiringMedicine=True`、`HideUnavailableStage=True`）+ `UseMedicine/MedicineCount` 取计划表 `MedicineNumb` + `AnnihilationStage = Info.Annihilation`。
- **完成记账**（L938-964）：`check_log` 里解析「完成任务: 剿灭作战」+ 理智识别行/进度行（`_has_completed_annihilation_week` L116-135：无理智行=代理开始前已完成；有进度行需 `current >= total` 才算完成）→ 写 `Data.AnnihilationCompletedWeek = %G-W%V(UTC+4)`。**宁可下次重试，不误记**。
- 注意：`Info.Annihilation == "Close"` 时**连剿灭模式都不进**；此模式与日常模式互不阻塞（各自 `run_book`）。

### 3.3 实测快照（2026-09-27 20:2x，全队 60 号）
- 配置分布：`LungmenOutskirts@Annihilation 41`、`Close 9`、`LungmenDowntown@Annihilation 5`、`Annihilation 5`；`StartWeekday` 全 `Monday`。
- 记账：`2026-W39` 完成 49 号；`2000-W01`（从未记录）10 号（多为 `Close` 或从未跑过的号）；`2026-W38` 1 号（`官-5`，最后代理 9/19，久未运行）。
- 结论：剿灭记账机制健康；"Close 用户不跑"与"周内已完成不重复"均符合预期。

---

## 4. 活动类任务

### 4.1 DepotMaintainTask（库存保持）
字段（`DepotMaintainTask.cs` L28-76）：`UpdateDepot`、`IsStageManually`、`SkipDuringActivity`、`SkipDuringResourceCollection`、`OnlyFirstInsufficientPlan`、`UseAutoSeries`、`UseMedicine`(true)、`UseStone`(true)、`UseExpiringMedicine`、`PlanList[]`（`{Stage, DropId, DropCount, UseMedicine, MedicineCount, UseStone, StoneCount}`）。
序列化（`DepotMaintainTaskUserControlModel.cs` L534-663）：
- `SkipDuringActivity && StageManager.IsActivityOpen()` → **整任务跳过**（L544-548）；`SkipDuringResourceCollection && IsResourceCollectionOpen()` → 跳过（L551-555）。这两个开关就是"活动期别抢玩家材料关"的正确姿势。
- `UpdateDepot` → 先追加 core `Depot`（仓库识别）任务刷新库存（L561-571）。
- 逐条计划：无效 `DropId`/`DropCount` 报错跳过；缺口 `need<=0` → 日志"库存充足"跳过；关卡不开放 → `DepotPlanStageNotOpen` 报错跳过（L610-624）；命中 → 追加 `AsstFightTask{Stage, Drops={id:need}, MaxTimes=int.MaxValue, Medicine=…, MedicineExpireDays= UseExpiringMedicine?2:0, Series= UseAutoSeries?0:1}`（L626-639）；`OnlyFirstInsufficientPlan` → 只下发第一个缺口（L651-654）。
- MAA UI 中"活动临期药"固定 2 天阈值（`ExpiringMedicineDays=2`）。
AUTO-MAS 侧（AutoProxy L175-233）：从用户配置 `Task.DepotMaintainPlans`（JSON 字符串）注入 —— **只吃 `Stage/DropId/DropCount` 三键；药/石强制 0**；`UseAutoSeries` 默认 true；且 `Info.StageMode != "Fixed"`（走计划表）的用户**强制关闭库存保持**（L475-476）。
> 全队现状：`IfDepotMaintain=false` ×60 → 库存保持任务不会出现在队列（`DepotMaintain` 且未开启时直接跳过，L665-666），与 MAA_6 gui.json 实测队列一致。

### 4.2 ReclamationTask（生息演算）
字段（`ReclamationTask.cs` L23-97）：
- `Theme`：`Fire`（沙中之火）/ `Tales`（沙洲遗闻，默认）/ `RelaunchAnchor`（重启锚点）。
- `Mode`：`ProsperityNoSave(0)` 无存档进出关卡刷生息点 / `ProsperityInSave(1)` 有存档组装支援道具刷 / `RA1`、`RA15`、`RA4`（重启锚点 1 图/15 图/4 图）。
- `ToolToCraft`（要组装的支援道具）、`IncrementMode`、`MaxCraftCountPerRound`（默认 16）、`ClearStore`（默认清商店）。
资源：`resource/tasks/RA/Reclamation2.json`（沙洲遗闻）、`Reclamation3.json`（重启锚点 `RelaunchAnchor@RA@RA1/RA4/RA15` 系列）、`RA/plugin/`。
> 编排注意：**AUTO-MAS 不管理生息演算**（其 `MAA_TASKS` 8 项不含，UI 无对应开关）；环境实测 MAA_6 队列无 `ReclamationTask`。要刷生息演算需单独在 MAA 侧配置/发单（另行评估，不属日常链）。

### 4.3 其它"活动限定"任务（以资源 tasks.json 为准）
| 类型 | 资源任务键（实测 tasks.json） | GUI 开关 |
|---|---|---|
| 剿灭 | `Annihilation*`（含 `@UnableToAgent2`） | FightTask §3 |
| 挖矿合成玉 | `MiningActivitiesBegin/MiningActivities/.../EarnRewards` | `AwardTask.Mining` |
| 幸运墙合成玉 | `OrundumActivitiesBegin/OrundumActivities/...` | `AwardTask.Orundum` |
| 特殊月卡访问 | `SpecialAccessActivitiesBegin/...` | `AwardTask.SpecialAccess` |
| 限时签到 | `SignInEventBegin/SignInEventEnter/SignInEventAllClaimed/...` | `AwardTask.SignInEvent` |
| 每日免费一抽 | （无独立资源键，走寻访界面） | `AwardTask.FreeGacha` |
| 活动商店/无限池 | `SS@Store@*`（`Stages/base.json`；入口 `SS-Open` 同时匹配 `StageSideStory.png`/`StageActivity.png`） | 小游戏入口 `SS@Store@Begin` |
| 绿票/黄票商店 | `GreenTicket@Store@*` / `YellowTicket@Store@*` | 小游戏入口 |
| 生息演算商店 | `RA@Store@*` | 小游戏入口 |
| 常驻"小游戏" | `MiniGame@SecretFront`、`MiniGame@AutoRaisePotential@Begin`、`MiniGame@MaterialSynthesis@Begin`、`MiniGame@CursedRelic@Begin` | 工具页/小游戏入口（`StageManager.cs` L324-341） |

AwardTask 全字段：`Award`（日常）、`Mail`、`FreeGacha`、`Orundum`、`Mining`、`SpecialAccess`、`SignInEvent`（`AwardTask.cs` L19-57）。
队列实测（MAA_6）：`Award=true, Mail=true, FreeGacha=false, Orundum=true, Mining=true, SpecialAccess=true, SignInEvent=false`。
`CustomTask`（`CustomTask.cs` L18-26）：`CustomTaskName` 直接执行任意 core 任务名 —— 需要跑上述资源流程（如 `SS@Store@Begin`）时的通用后门。
> 判定：MAA 不再有"活动限定任务类型"，而是"常驻资源流程 + 活动期才存在的界面"；上表之外都属常驻。活动期间 GUI 靠 `StageManager.IsActivityOpen()` 与活动数据（`StageActivityV2.json`：`sideStoryStage`/`resourceCollection`/`miniGame`）开关提醒与跳过。

---

## 5. 编排建议（12 槽位 + 让位区）

### 5.1 现状编排事实（实测）
- **班次表**（`tools/mas_dispatch.py` L81-88，Hermes 持有；AUTO-MAS 侧定时项 Enabled=false 仅作回滚）：早班 `日常定时1-12 = 05:00/05:03/…/05:33`；晚班 `晚班定时1-12 = 19:30/19:33/…/20:03`（3 分钟错峰）。
- **12 槽位**：`E:\MAA-Multi\MAA_1..MAA_12`（脚本按目录分配：MAA_1:5、MAA_2:2、MAA_3:5、MAA_4:6、MAA_5:5、MAA_6:2、MAA_7:5、MAA_8:6、MAA_9:5、MAA_10:5、MAA_11:4、MAA_12:4，共 54 脚本 / 60 用户（58 enabled））；每个班次队列按 2-6 个脚本串行跑。
- **临时模式**：`临时-补跑-<MMDD>_<HHMM>-N`（现有 9 条）——冲活动/补漏的现成载体。
- **让位区**（肉鸽看守，`docs/ROGUE_WATCH.md` §5.25/§5.26）：`04:00–05:33` 与 `18:30–20:30` 期间**不发车、收工让路**；真正闸门 =「产线在跑 / 连续 ≥6 分钟没槽在写」。肉鸽只在两班空档活动；l-1/l-2/l-7 与产线共用模拟器（emu 25/28/52）——**同一模拟器绝不允许两个 MAA**。

### 5.2 建议 A —— 日常链内解决（首选，零新增发车）
活动材料刷取直接放进每个用户的代理链：
- 方式①（现状用法）：计划表 `Stage` = 活动关代码；`Stage_Remain` = `1-7` 兜底。
- 方式②（推荐给"活动关优先"场景）：`Task.IfActivityFirst=true` + `Task.ActivityStageIndex=N`：
  - 活动关卡来自 `Config.get_stage_info("Info", server, refresh=True)["Activity"]`（`app/core/config.py` L2269-2322；数据 `Data.Stage`，缓存 ≤1h，远端刷新 `StageActivityV2.json` L2396-2447）；
  - `_resolve_activity_stage`（AutoProxy L236-250）：`stages[index-1]`，**序号越界回退第一项**；活动列表为空（当前 Official）→ `None` → 不插入任务。
  - 生成的「活动关优先」任务（L253-282）：`StagePlan=[活动关]`、`IsStageManually=true`、`UseOptionalStage=false`、`UseWeeklySchedule=false`、`EnableTargetDrop=false`、`UseMedicine= ActivityMedicineNumb>0`、`ActivityMedicineNumb` 单独药额度（与理智作战互不转移）；插在 `StartUp` 之后（L904-908）。
  - 注意序号语义：序号按 MAA 活动接口顺序（示例 SR：`['SR-8','SR-7','SR-6','SR-5']` → 1=SR-8、2=SR-7），**不确定时先用序号 1 打日志核对**。
- 药量控制建议：活动关优先 `ActivityMedicineNumb` 给 1-2 瓶；理智作战 `MedicineNumb` 保持 0-1；**活动临期药**用 `UseExpireMedicineForActivity=true` 让窗口自动拉到本周结束（药快过期时给活动）。库存保持不参与（全队关）。

### 5.3 建议 B —— 需要"冲活动"额外发车时（12 槽位之外）
- 载体：复制一条 `临时-补跑-…` 队列（模式照旧 AutoProxy），挂到 Hermes 发车表（`mas_dispatch.py`）；**不要给 AUTO-MAS 重开定时项**（回滚链会分叉）。
- 窗口：早班 05:33 之后（等爬坡完 + 产线闸变绿）白天任选；或晚班 20:30 之后（让位区上沿 20:30 + 27 分钟余量）到 04:00 前；**避开 04:00–05:33 / 18:30–20:30**，并以"产线连续 6 分钟空闲"为放行闸。
- 耗时预算（单号）：`次数 ≈ (当前理智 + 药×100) / 关卡AP`；单次战斗 ≈ 2–4 分钟（含连战自动），典型 21AP 活动关 + 2 瓶药 ≈ `(135+200)/21 ≈ 16` 次 ≈ **35–60 分钟**；结束后还要基建/公招等常规尾巴。整队按脚本数 × 单号时长串行估计。
- 队列长度参考：每队 2-6 脚本；早班整体约 05:00 → 08:30-09:00 跑完（ROGUE_WATCH 实测"早班 08:5x 就跑完"），据此推算活动补刷要排到早班之后或晚班之后。

### 5.4 建议 C —— 不抢实例的硬规则
1. 12 个 MAA 槽在班次内被所在队列顺序占用；**同槽第二发 MAA = 撞车**（肉鸽自 04:00/18:30 收工让路就是这个原因）。
2. 判断"槽空了"的可靠信号：该脚本 `gui.log` 出现 `任务已全部完成` / 队列走完，或产线 6 分钟空闲闸；**别只看 MuMuManager/ADB 的'在跑'状态**（历史上有假死）。
3. 专用活动发车优先复用已在班次里的脚本+模拟器，但必须排在队列**之后**（等队列完成）；或退到本机 #0（不占主机槽）。
4. 涉及肉鸽共用模拟器（emu 25/28/52 等）时，先收工让路（肉鸽台账 `wrap-up`），再发活动队列。

### 5.5 建议 D —— 活动上下线核对清单（编排执行面）
- 活动开始前/中：读 `StageActivityV2.json`（或 AUTO-MAS 的 `Data.Stage`）核对 `Info` 列表非空、目标关在列表内；决定走①直接 Stage 还是②IfActivityFirst。
- 活动结束前 1–2 天：检查药库存，必要时临时把药投到活动关（`UseExpireMedicineForActivity`、活动关优先药额度）。
- **活动结束后立刻**：把受影响号的 `Stage` 改回常驻关（或用计划表），否则理智作战静默跳过（本次 SR 事件即是教训：9/18 结束，至 9/27 46 个号白挂）。
- 活动期间开 `DepotMaintain` 的话记得 `SkipDuringActivity=true`，避免库存保持抢活动材料关。
- 每次改动后复核：`gui.log` 里 `GetFightStage: from [...], selected ...` 必须选中预期关卡；剿灭看「完成任务: 剿灭作战」与 `AnnihilationCompletedWeek` 记账。

---

## 6. 风险与红线

| # | 风险 | 证据 / 缓解 |
|---|---|---|
| R1 | **活动关失效静默跳过**（理智作战产出 0，只烧剩余理智） | 2026-09-27 实测 4 台实例 `selected null`；活动结束日提醒改 Stage（§5.5） |
| R2 | StagePlan 里常驻关/空串在中间 → 后续关卡永不选中 | MAA L1692-1707 Warning；排关卡串时把活动关放最前，常驻关放最后 |
| R3 | 手输活动关无 apCost → 理智不足判定跳过（可能白跑） | StageApCostHelper + L1277-1281；活动关 AP 用本仓 v4 表估算 |
| R4 | 同模拟器双 MAA（肉鸽/活动/日常互撞） | 让位区 + 6 分钟空闲闸 + 收工让路；§5.4 |
| R5 | 药/石预算串线：活动关优先、理智作战、库存保持三套额度 | ActivityMedicineNumb / MedicineNumb / Plan 级；库存保持注入时药石强制 0 |
| R6 | 剿灭误记账/漏记账 | AUTO-MAS 只在 `current>=total` 或无理智行时记周完成；异常就下次重试（L116-126） |
| R7 | 计划表 `StageMode != Fixed` 时库存保持被强制关闭 | AutoProxy L475-476（设计如此；编排别依赖它） |
| 红线 | 本 playbook **不授权**改配置/发车/动实例；任何改动必须：备份（`*.bak_*` 惯例）→ 改 → gui.log 复核 → 记账核对 | docs/AUDITS 惯例 |

---

## 7. 附录

### A. 样例 JSON（脱敏；账号已打码）
理智作战（全队现状，计划表 Fixed/简洁）：
```json
{
  "$type": "FightTask",
  "UseMedicine": false, "MedicineCount": 0,
  "UseStone": false, "StoneCount": 0,
  "EnableTargetDrop": false, "DropId": "", "DropCount": 0,
  "IsInventoryTarget": false,
  "EnableTimesLimit": false, "TimesLimit": 2147483647,
  "Series": 0,
  "StagePlan": ["SR-7"],
  "IsDrGrandet": false,
  "UseExpiringMedicine": true, "MedicineExpireDays": 2,
  "UseExpireMedicineForActivity": false,
  "UseCustomAnnihilation": false, "AnnihilationStage": "Annihilation",
  "HideUnavailableStage": false,
  "IsStageManually": true, "UseOptionalStage": true,
  "UseStoneAllowSave": false, "HideSeries": false,
  "StageResetMode": "Current", "UseWeeklySchedule": false,
  "Name": "理智作战", "IsEnable": true, "TaskType": "Fight"
}
```
剩余理智（AUTO-MAS 注入，`MAA_REMAIN_FIGHT_BASE` + `Stage_Remain`）：
```json
{
  "$type": "FightTask", "Name": "剩余理智", "IsEnable": true, "TaskType": "Fight",
  "StagePlan": ["1-7"], "Series": 0, "TimesLimit": 999, "EnableTimesLimit": false,
  "UseMedicine": false, "MedicineCount": 0, "UseStone": false, "StoneCount": 0,
  "UseExpiringMedicine": false, "UseCustomAnnihilation": false,
  "HideUnavailableStage": true, "IsStageManually": true, "UseOptionalStage": false,
  "UseWeeklySchedule": false
}
```
活动关优先（AUTO-MAS 生成样例；软注入到 gui.json 队列）：
```json
{
  "$type": "FightTask", "Name": "活动关优先", "IsEnable": true, "TaskType": "Fight",
  "StagePlan": ["SR-7"], "IsStageManually": true, "UseOptionalStage": false,
  "UseWeeklySchedule": false, "EnableTargetDrop": false, "IsInventoryTarget": false,
  "EnableTimesLimit": false, "UseMedicine": true, "MedicineCount": 2,
  "UseExpiringMedicine": false, "UseExpireMedicineForActivity": false
}
```
剿灭（Annihilation 模式注入）：
```json
{
  "$type": "FightTask", "Name": "理智作战", "TaskType": "Fight",
  "StagePlan": ["Annihilation"], "UseCustomAnnihilation": true,
  "AnnihilationStage": "LungmenOutskirts@Annihilation",
  "UseExpiringMedicine": true, "TimesLimit": 999, "EnableTimesLimit": false,
  "HideUnavailableStage": true, "UseMedicine": false, "MedicineCount": 0
}
```
库存保持（结构示意，字段取自 v6.18.0 类定义）：
```json
{
  "$type": "DepotMaintainTask", "Name": "库存保持", "TaskType": "DepotMaintain",
  "UpdateDepot": true, "SkipDuringActivity": false, "SkipDuringResourceCollection": false,
  "OnlyFirstInsufficientPlan": false, "UseAutoSeries": true,
  "UseMedicine": true, "UseStone": true, "UseExpiringMedicine": false,
  "PlanList": [
    {"Stage": "1-7", "DropId": "30012", "DropCount": 5000, "UseMedicine": false, "MedicineCount": 0, "UseStone": false, "StoneCount": 0}
  ]
}
```
生息演算（结构示意）：
```json
{
  "$type": "ReclamationTask", "Name": "生息演算", "TaskType": "Reclamation",
  "Theme": "Tales", "Mode": "ProsperityInSave", "ToolToCraft": "",
  "IncrementMode": 0, "MaxCraftCountPerRound": 16, "ClearStore": true
}
```

### B. 源码行号索引（调查时刻实测）
| 文件 | 行 | 内容 |
|---|---|---|
| `FightTask.cs` | L28-164 | FightTask 全字段 + OnDeserialized |
| `AsstFightTask.cs` | L24-131 | 下发 JSON 字段名 |
| `FightSettingsUserControlModel.cs` | L980-984 / L986-1013 | 剿灭四选一 / 自定义替换字段 |
| " | L1080-1105 | 临期药窗口字段 |
| " | L1197-1202 | `GetFightStage` 选关 |
| " | L1242-1302 | 库存目标重算 / 6 分钟回复估算 / times=0 跳过 |
| " | L1312-1350 | `BuildAsstFightTask`（活动临期药 + 剿灭替换） |
| " | L1352-1364 | 常驻关判定 |
| " | L1662-1784 | `Serialize`（周计划/选关/阻断警告/库存/次数/掉落） |
| `StageInfo.cs` | L138-190 | 活动关闭/开放判定 |
| `StageManager.cs` | L44 / L87-100 / L668-685 | StageApi / 活动开放判定 / 常驻关卡表（含 `12-17-HARD`） |
| `StageApCostHelper.cs` | 全文 | `resource/stages.json` → apCost；null 语义 |
| `DepotMaintainTask.cs` / `...UserControlModel.cs` | L28-76 / L534-663 | 库存保持字段 / 序列化 |
| `ReclamationTask.cs` | L23-97 | 生息演算字段与枚举 |
| `AwardTask.cs` / `CustomTask.cs` | L19-57 / L18-26 | 活动奖励开关 / 自定义任务 |
| `app/controllers/auto_proxy.py` | L70-135 | 剿灭正则/周标记/判定/完成解析 |
| " | L175-233 | 库存保持注入（药石清零） |
| " | L236-282 | 活动关解析 + 活动关优先生成 |
| " | L353-372 / L463-480 | run_book / 模式循环 |
| " | L645-666 / L770-794 | 活动关优先解析 / AccountName / plan_data |
| " | L799-831 / L887-928 | 剿灭与日常 task_set / 活动关优先与剩余理智插入 |
| " | L938-999 | check_log 剿灭记账 / 状态映射 |
| `app/utils/constants.py` | L54-121 / L150-230 / L432-473 | MAA_TASKS / 剿灭与剩余理智 base / 计划表关卡表 |
| `app/core/config.py` | L2269-2322 / L2361-2447 | get_stage_info / get_stage + 远端刷新（StageActivityV2） |
| `config/Config.json` | `Data.{Stage,StageData,LastStageUpdated,StageETag}` | 活动数据缓存（ETag 增量） |
| `tools/mas_dispatch.py` | L81-88 | SHIFT_TABLE（12+12 班次表） |
| `docs/ROGUE_WATCH.md` | §5.24-5.26 | 让位区 04:00–05:33 / 18:30–20:30 |

### C. 只读检查命令（可直接复制）
```bash
# 1) 选关实况（某实例今天的战斗选关）
ssh -i ~/.ssh/maaorch_target Administrator@100.79.173.69 \
  'cmd /s /c "findstr /c:GetFightStage E:\MAA-Multi\MAA_12\debug\gui.log"' | tail -5
# 2) AUTO-MAS 侧活动数据新鲜度（StageData 非空、LastStageUpdated）
ssh ... 'cmd /c type E:\AUTO-MAS\config\Config.json'   # 看 Data.Stage 的 Official.Info 是否非空
# 3) 班次表与派车漂移（本地）
python D:/1-2/MAAOrch/tools/mas_dispatch.py --check
# 4) 活动公开数据（本地）
curl -s https://api.maa.plus/MaaAssistantArknights/api/gui/StageActivityV2.json | head -c 400
```

### D. 术语表
- **剩余理智**：AUTO-MAS 注入的第二个 Fight 任务（`Stage_Remain`），把理智作战后剩余理智扔进低耗关（全队=1-7）。
- **活动关优先**：AUTO-MAS 注入的独立 Fight 任务（`Task.IfActivityFirst`），用 `ActivityMedicineNumb` 的独立药额度、在 StartUp 后立即执行。
- **库存保持**：MAA `DepotMaintainTask`——先刷仓库，再按计划表逐项把材料补到目标数量。
- **剿灭模式**：AUTO-MAS 的 `Annihilation` run mode（每人先跑），MAA 侧就是 `StagePlan=["Annihilation"]`。
- **磨难（-HARD）**：主线第 9 章起的困难难度关；MAA 书写 `12-17-HARD`；本仓表内 `tough_*`。
- **突袭**：本仓表内 `stageId#f#`（FOUR_STAR）写法，仅供 AP 统计；MAA StagePlan 不写后缀。
- **让位区**：肉鸽看守让路窗口 `04:00–05:33`、`18:30–20:30`。
