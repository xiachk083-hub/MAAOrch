# MAS 按需配置发车：需求→字段映射 · 读回校验 · 自动还原（方案）

> 卡片：kanban `t_fee4752d`（board `maa-parallel-benchmark-20260926`）｜生成：2026-09-28 ｜ 性质：**方案 / 机制规范**
> 实施卡片：`t_b2a74be1`（→ `tools/mas_run_profile.py` + `runtime/templates/run_profile.example.json` + `docs/MAS_RUN_PROFILE_TOOL.md`）
> **行号口径 = 宿主线上 `E:\AUTO-MAS` 现状**（2026-09-28 抓取）：`app/task/MAA/AutoProxy.py`（49,293 B / 1153 行，mtime 09-28 02:20）、`app/utils/constants.py`（38,005 B，09-28 02:19）为 09-28 药补丁后版本；`app/models/config.py`、`app/models/ConfigBase.py`、`app/models/schema.py`、`app/task/MAA/manager.py` 与本地 `D:\MAS` v5.4.0 **逐字节一致**（MD5 实测，行号可互用）。本地 `D:\MAS` 的 `AutoProxy.py` 是 1090 行旧版，**行号勿互套**；09-27 的 `MAS_SCENARIO_DISPATCH.md` 行号是药补丁前版本（整体位移 +52～+60），同样勿套。
> 本次为**只读研究 + 写文档**：未改任何配置、未发车。live 样本抓取于 2026-09-28 06:0x。
> 关联文档：`MAA_TASK_CONFIG_REFERENCE.md`（字段证据库）、`PLAN_EDITOR.md`（计划表 UI 语义）、`MAS_SCENARIO_DISPATCH.md`（相邻路线：按场景发车草案）、`audits/2026-09-27_mas_task_modes.md`（任务模型：三模式/MaaManager 流水线）、`audits/2026-09-27_mas_medicine_expiry.md`（药物语义与临期窗口）、`MAS_EMULATORS.md`（实例/ADB 口）。

## 0. TL;DR

1. **改哪一层，先看账号的 `Info.StageMode`**：`Fixed`（线上 58/60）⇒ 写用户配置 `Info.<7 键>`；`<计划表 uuid>`（2 个）⇒ 写那张计划表。两层的键名相同（`MAA_STAGE_KEY` 7 键），AutoProxy 每轮发车时按 `StageMode` 二选一取值，再组装进 MAA。
2. **`<MAA目录>/config/gui.new.json` 是产物不是源头**：每次发车前被覆盖重写（AutoProxy:934），轮末被 `data/<sid>/Temp` 快照整体还原（manager.py:256-260）。直接改 MAA 文件无效；要影响运行，只能改 **AUTO-MAS 两层配置**（跑前写 / 跑后还原）。
3. **"只刷 N 次"没有现成字段**：简洁模式 `EnableTimesLimit` 被硬设 False（:835），计划表/用户配置里也没有次数键 ⇒ 直选路径不存在。可选：`Series`（连战倍数）近似、打补丁放开（可选）、或详细模式下改用户自己的 MAA ConfigFile。语义上 `Series` 是"每组连战几把"，**不是总数上限**（详见 §2 行 3/4 与 §7 勘误）。
4. **读回校验**读 `<MAA目录>/config/gui.new.json`（断言 StagePlan/Series/MedicineCount/UseMedicine 等，§5 L1），核心级证据再看 `debug/asst.log` 的 `append_task Fight` 行（§5 L2），行为级看 `gui.log` 锚点（§5 L3）。不一致 ⇒ 立即 `/api/dispatch/stop` 止损再修。
5. **还原双保险**：MAA 槽位配置由 AUTO-MAS 自己还（Temp 快照）；**我们只负责自己写的 AUTO-MAS 层**（计划表/用户配置）——`try/finally`，失败也还原，跑中被锁要排队重试。

---

## 1. 机制总览

一条流水线（每轮发车重算，`AutoProxy.py`）：

```
 [计划表 PlanConfig]  ─┐
                       ├─(按 Info.StageMode 二选一)→ plan_data(7 键)
 [用户配置 Info.<7键>] ─┘
        ↓ 组装（Routine :812-837 / Annihilation :799-810 / 活动关 :887-894 / 剩余理智 :910-928）
 <MAA目录>/config/gui.new.json（+ gui.json 兼容段 :930-934）
        ↓ MAA 启动读取（:540 set_maa → :544 open_process）
 跑任务链 → gui.log / asst.log
        ↓ 轮末 automatic
 data/<sid>/Temp 快照 → 覆盖还原 <MAA目录>/config（manager.py:139-142 备份、:256-260 还原）
```

层级表（谁能写、何时生效、谁还原）：

| 层 | 位置 / 接口 | 我们可否写 | 生效时机 | 还原责任 |
|---|---|---|---|---|
| 计划表 PlanConfig | `POST /api/plan/{add,get,update,delete,order}` | ✅ | 下一轮发车组装 | **我们**（快照还原 / 删临时表） |
| 用户配置（Info/Task/Data） | `POST /api/scripts/user/update` | ✅ | 下一轮发车组装 | **我们**（快照还原） |
| MAA 槽位配置 `<MAA目录>/config/gui.new.json` | 文件 | ⚠️ 写了也会被覆盖/还原 | MAA 启动读取 | **AUTO-MAS**（Temp 快照，:139-142/:256-260） |
| 脚本/用户 MAA 源配置 `data/<sid>/Default|ConfigFile`、`data/<sid>/<uid>/ConfigFile` | 文件 | ⚠️ 持久层，改它要自担备份（详细模式每轮 copytree 源头 :607-621） | 不自动 |

> 运行中**配置锁定**：任务从 `manager.py:124-125`（prepare 时 lock）到 `:199`（final_task unlock）期间，对该脚本的一切配置写被拒（`配置已锁定, 无法修改`，ConfigBase.py:1099 等；项级另有 `配置项 'x.y' 已锁定` :716-717）。⇒ 只能"跑前写 / 跑后还原"。

---

## 2. 需求→字段映射表（核心）

> 口径：一次"按需发车"的 9 个常见需求。`Info` = 用户配置组（`/api/scripts/user/update` 的 `data.Info`）；`计划表` = `MaaPlanConfig` 的 `ALL`/周几名组（同一批键）。**先读 `Info.StageMode` 再决定写哪边**。
> `MAA_STAGE_KEY` 7 键（constants.py:113-121）：`MedicineNumb / SeriesNumb / Stage / Stage_1 / Stage_2 / Stage_3 / Stage_Remain`。

| # | 口述需求 | 改哪层（Fixed / 计划表） | 字段 | 取值域 | 默认 | 注入到 MAA | 证据（文件:行） |
|---|---|---|---|---|---|---|---|
| 1 | "刷哪个关" | Info.`Stage`~`Stage_3` / 计划表同键 | 关卡候选（最多 4 个，从左到右第一个"当日开放"者出战） | 关卡码（`1-7`/`CE-6`/活动关码如 `SR-7`）；`*`=当前/上次；`-`=空位不参与 | `-` | `Fight.StagePlan=[...]`（`*`→`""`）、`IsStageManually=True`、`UseOptionalStage=True` | AutoProxy:782-794（取键）、:820-831（拼装）；user `config.py:510-518`；plan `config.py:2274-2281` + `schema.py:1692-1696`；MAA 语义 `FightTask.cs:88-90`（"从上往下选择第一个可用关卡"）、`FightSettingsUserControlModel.cs:1197-1202`（GetFightStage） |
| 2 | "剩余理智怎么处理"（刷完主计划后剩下的理智） | Info.`Stage_Remain` / 计划表同键 | 剩余理智关卡 | 同上；`-`=**不挂**"剩余理智"任务 | `-` | 追加第二条 Fight「剩余理智」（`StagePlan=[...]`） | AutoProxy:910-928；门 = `Task.IfFight` ∧ `Stage_Remain != "-"`（:911-916） |
| 3 | "连战几把/一组打几次" | Info.`SeriesNumb` / 计划表同键 | 连战倍数（MAA 叫"代理倍率"） | `"0"`=自动 / `"1".."6"` / `"-1"`=不调整（MAA 本体支持到 10，AUTO-MAS 两层只到 6） | `"0"` | `Fight.Series` | user `config.py:503-508`；plan `config.py:2266-2271` + `schema.py:1689-1691`；注入 :819（主）、:927（剩余）、constants.py:162（剿灭=0）；MAA 语义 `zh-cn.xaml:1010-1021`（SeriesTip）、core `FightTask.cpp:101`（series==0 ⇒ 用药后理智不溢出） |
| 4 | "**只刷 N 次**/一共打 N 把" | ❌ 无现成字段（简洁被硬关；详细=用户 ConfigFile 值） | `EnableTimesLimit` + `TimesLimit` | bool + int；`EnableTimesLimit=False` ⇒ 不限次 | `false` + `2147483647` | `Fight.EnableTimesLimit/TimesLimit` | AutoProxy:835（**仅简洁分支** :834 内硬设 False）、:812-837 不触这两键（详细沿用 ConfigFile）；MAA `FightSettingsUserControlModel.cs:1688`（`effectiveMaxTimes`）、:1703-1705（整除警告 `FightTimesMayNotExhausted`，zh-cn.xaml:1586）、core `FightTimesTaskPlugin.h`（times=最大战斗次数；`finish_fight: m_fight_times += m_series_current`） |
| 5 | "吃不吃理智药 / 吃几瓶" | Info.`MedicineNumb` / 计划表同键 | 理智药额度 | 0–9999；0=不吃 | `0` | `UseMedicine=(MedicineNumb≠0)`、`MedicineCount` | 注入 :804-807（剿灭）、:814-817（日常）；user `config.py:499-501`；plan `config.py:2262-2264` + `schema.py:1688`；剩余理智归零（constants.py:193-194）、活动关独立额度（:276-277） |
| 6 | "优先吃临期药" | ❌ **AUTO-MAS 两层均无字段**（MAA 侧字段） | （MAA）`UseExpiringMedicine` + `MedicineExpireDays` | bool + 天数 N；临期窗口独立于配额 | 模板 true/2；剩余/剿灭/活动 base true（09-28 药补丁）；主战斗=模板继承 | `Fight.UseExpiringMedicine/MedicineExpireDays` | 主战斗不触（:812-837 无此键）；剩余 base `constants.py:206`、剿灭 `:165`、活动 `:278`；MAA 组装 `FightSettingsUserControlModel.cs:1272-1275`（`UseExpiringMedicine?N:0`）；按需开/关 ⇒ 改模板/ConfigFile 或（可选）补丁 |
| 7 | "活动关优先" | Info→Task 组 | `Task.IfActivityFirst`(bool) + `Task.ActivityStageIndex`(1–9999) + `Task.ActivityMedicineNumb`(0–9999) | 序号越界回退**第一项** | `false` / `1` / `0` | StartUp 后插一段「活动关优先」Fight，独立药额度 | 注入 :646-659（需活动列表非空）、:887-894、:253-280；`_resolve_activity_stage` :236-250；字段 `config.py:584-599`（注意 `ActivityMedicineNumb` 带 legacy 别名 `Info.MedicineNumb`，:597-598）；活动源 = `api.maa.plus/…/StageActivityV2.json`（core `config.py:2413-2414`） |
| 8 | "目标掉落" | ❌ **AUTO-MAS 两层均无字段**；简洁被硬关 | （MAA）`EnableTargetDrop/DropId/DropCount/IsInventoryTarget` | bool/string/int | false/""/0 | `Fight.EnableTargetDrop/DropId/DropCount`（+ `drops` 参数） | AutoProxy:836（简洁硬关）、:834（分支条件）；MAA `FightTask.cs:52-70`、`FightSettingsUserControlModel.cs:1696-1701`、`AsstFightTask.cs:123-127`；要用 ⇒ 详细模式改 ConfigFile 或（可选）补丁 |
| 9 | "碎石 / 葛朗台 / 隐藏倍率" | ❌ 同类 MAA 侧字段（不在 AUTO-MAS 两层） | `UseStone/StoneCount/IsDrGrandet/HideSeries` … | 见 MAA 字段表 | false/0/false/false | 同键名 | 模板实测（`template_default_117.json`）；`FightTask.cs:43-50,93-95,136-139` |

**通用注记**：

- **口径 = "按当日生效值全量 + 本次差分"**：`*`/`-`/候选顺序都会影响实际出战的关卡；同样地，**临时接管一个账号时，未请求的键必须复制它"当日生效值"**（含剩余理智/倍率/药量），否则会被默认值静默改写。
- 计划表 `Mode=ALL` 与 `Mode=Weekly` 取值不同：`Weekly` 按 **UTC+4 当日** weekday 组取（`get_current_info`，`config.py:2283-2300`），组缺项回落 `ALL`。
- 剩余理智 merge 基础块会**归零**药/石/掉落/次数（`constants.py:191-228`）；模板中不在 base 的键会继承（实测差异键只有 `MedicineExpireDays`/`StageResetMode`）。
- 任务开关「Task.IfXxx」决定各任务条目的 `IsEnable`（AutoProxy:897-905）；`DepotMaintain` 额外要求 `StageMode==Fixed`（:475-476）。

---

## 3. 注入语义（AutoProxy 精确口径）

**日常轮（Routine）托管键 = 9 个**（:812-837；前 7 个两种模式都写，后 2 个仅简洁）：

| 注入键 | 值 / 来源 | 行 |
|---|---|---|
| `UseMedicine` | `MedicineNumb != 0` | :814-816 |
| `MedicineCount` | `MedicineNumb` | :817 |
| `Series` | `int(SeriesNumb)`（默认 "0"） | :819 |
| `StagePlan` | `[Stage, Stage_1, Stage_2, Stage_3]` 中 `!= "-"` 者，`*`→`""` | :820-828 |
| `IsStageManually` | `True` | :829 |
| `UseOptionalStage` | `True`（候选回退的开关） | :830 |
| `UseWeeklySchedule` | `False` | :831 |
| `EnableTimesLimit` | `False`（**仅简洁** :834-835） | :835 |
| `EnableTargetDrop` | `False`（**仅简洁** :834-836） | :836 |

**不托管（简洁=沿用默认模板；详细=沿用该号自己的 ConfigFile）**：`UseExpiringMedicine`、`MedicineExpireDays`、`UseStone/StoneCount`、`IsDrGrandet`、`TimesLimit`（值）、`HideSeries`、`StageResetMode`、`HideUnavailableStage`、`AnnihilationStage`（日常轮）等。

**三条支线**：

- **剿灭轮（Annihilation）**：任务表只开 StartUp+Fight（:478-480）；Fight = `MAA_ANNIHILATION_FIGHT_BASE` merge（:799-803，`Series=0`、`TimesLimit=999`、`EnableTimesLimit=False`、`UseExpiringMedicine=True`；constants.py:150-187），`AnnihilationStage=Info.Annihilation`（:808-810）；周上限达成自动记账并关 Fight（:945-957）。**每用户先剿灭后日常**（:464 的 `for self.mode in ["Annihilation","Routine"]`）。
- **活动关优先**：仅 `Task.IfActivityFirst` 触发；活动列表空 ⇒ 不插（:646-659, :888）；插入位置 = StartUp 之后（:907-908）；独立药额度（:253-280，`UseExpiringMedicine=True` :278）。
- **剩余理智**：插在理智作战条目**紧跟其后**（:910-928）；`StagePlan=[Stage_Remain]`、`Series=int(SeriesNumb)`；其余字段取 `MAA_REMAIN_FIGHT_BASE`。

**导出**：组装结果写 `<MAA目录>/config/gui.new.json`（`write_file` :934）+ `gui.json` 兼容段（:930-933，注释标注 OLD 即将移除）。`<MAA目录>` = 脚本配置 `Info.Path`（:347-349）。

---

## 4. 时序图与标准动作（`tools/mas_run_profile.py` 的契约）

```
① 备份 ─→ ② 写配置 ─→ ③ preflight ─→ ④ 发车 ─→ ⑤ 读回校验 ─→ ⑥ 等完/超时 ─→ ⑦ 还原 ─→ ⑧ 审计
             ↑_________________失败也还原（try/finally）_______________|
```

| 步 | 动作 | 要点 / 证据 |
|---|---|---|
| ① 备份 | 快照 **计划表**（`/api/plan/get`，含该账号当前指向的表）+ **用户配置**（`/api/scripts/user/get`）+ 原始 `StageMode` + 7 键当日生效值；带时间戳与 sha256；落 `runtime/run_profile/backups/<run_id>/` | 脱敏红线：`password/token/skland` 一律不带出/不落盘；还原凭快照，不凭记忆 |
| ② 写配置 | **默认：新建临时计划表**（`plan/add {"type":"MaaPlan"}` → `plan/update` 写全 7 键"当前值+差分"、`Info.Name="临时-按需-<yyyymmdd_HHMM>"`、`Mode=ALL`）→ `scripts/user/update {"Info":{"StageMode":"<planId>"}}`。**备选（Fixed 直写）**：直接 `user/update {"Info":{…7 键差分…}}`（先例 `tools/mas_stage_fix.py`：最小键写+备份+回读+回滚） | 不改现有计划表（可能多号共用）；`StageMode` 校验器要求目标存在（`config.py:412-419`）；写后**立即读回**核对（API 读回 + 需要时落盘读回：`ScriptConfig.json` 经 SSH 读，注意 cmd 行长 ≤8191，分块 ≤10 用户——`mas_stage_fix.py` 教训） |
| ③ preflight | `python tools/mas_preflight.py --script <名> --json`：REST 可达 / MAA 槽位空闲 / 实例 adb 可连 / 无残留模态（OCR）/ 注入闸门 / 磁盘；**exit 0=GO 1=NO-GO 2=无法判定（fail-closed）**；不过 ⇒ 回滚第②步并退出 | `tools/mas_preflight.py`（770 行）；`MAAORCH_MAS_API` 等 env 可覆盖 |
| ④ 发车 | `POST /api/dispatch/start {"taskId": <脚本uid 或 队列uid>, "mode": "AutoProxy"}`；返回 `taskId`=新 task_uid（**stop 用它**，`dispatch.py:34-70`） | 单号：**没有单号发车入口**（task_manager.py:305-316 只认队列/脚本；用户列表在 manager:146-161 按 `Status ∧ RemainedDay≠0` 取全）——见 §6-6、§7-4 |
| ⑤ 读回校验 | 轮询 `<MAA目录>/config/gui.new.json`，断言 §5 期望值；不一致 ⇒ 报告 + `dispatch/stop`（走 stop 才会触发 unlock+还原，`manager.py:199/:256-260`） | 多号脚本每号启动前重写一次（:540→:544），**按"当前活跃号"读**；先剿灭后日常（:464），首段可能是剿灭配置 |
| ⑥ 等完 / 超时 | 轮询 `gui.log` 任务链结束或 MAA 进程退出；或超时（`restore_after: run_end | timeout:Ns`） | 完成判据（AutoProxy:974-1006）：「完成任务: X」/「X 任务跳过」都算完成；「MAA 在完成任务前中止/退出」算异常。**避开 04:00 / 19:00 宿主计划重启窗口**（重启会中断在跑任务） |
| ⑦ 还原（try/finally） | ① `StageMode` 回写原值 → ② 删临时计划表（`plan/delete`；它会自动把仍指向它的账号改回 `Fixed`，`core/config.py:1651-1681`）→ ③（若临时停用了其它号）`Status` 回写 → ④ 读回校验（API + 需要时落盘） | 跑中被锁 ⇒ 写被拒并退避重试（默认 12×30s 级）；**失败也必须还原**：还原不成功要显式告警（不要静默） |
| ⑧ 审计 | `runtime/mas_ledger.jsonl`（全局）+ `runtime/mas_run_profile_log.jsonl`（请求/实际注入/结果/还原结果），全部脱敏 | 台账字段建议：run_id/ts/target(before→after)/hash/dispatch_task_uid/readback(L1-L3)/result |

**互斥/闸门**：默认 dry-run（只打印将做的改动与将调用的接口）；`--apply` 才执行；同一时刻只允许一个 profile 任务（锁文件）；`MAAORCH_*_NO_GO` 一票否决可按需沿用；**跨族单飞 = G3L 实例租约**（`runtime/instance_leases.json`，被别人占 ⇒ dry-run 也 rc=2 拒发、零写；机制与 owner 口径见 `docs/MAS_INSTANCE_LEASE.md`）。

---

## 5. 读回校验（三级证据）

### 期望值表（主战斗条目；简洁口径）

| 键 | 期望 |
|---|---|
| `Name` / `TaskType` | `理智作战` / `Fight` |
| `StagePlan` | `[v for v in (Stage,Stage_1,Stage_2,Stage_3) if v!="-"]`，`*`→`""` |
| `Series` | `int(SeriesNumb)` |
| `MedicineCount` / `UseMedicine` | `MedicineNumb` / `(MedicineNumb != 0)` |
| `EnableTimesLimit` | `False`（简洁；详细=按该号 ConfigFile） |
| `EnableTargetDrop` | `False`（简洁；详细=按该号 ConfigFile） |
| `UseExpiringMedicine` | 主战斗=模板/ConfigFile 值（08-23 起全队 true）；**跟请求无关，别写进"与请求一致"断言** |
| `IsEnable` | `Task.IfFight` |
| 队列顺序 | `StartUp` → [`活动关优先`] → [`DepotMaintain`] → `理智作战` → [`剩余理智`] → 其余（MAA_TASKS `constants.py:57-66`） |
| 剩余理智条目 | 存在 ⇔ `IfFight ∧ Stage_Remain != "-"`；其 `StagePlan=[Stage_Remain]`、`Series` 同主战斗 |
| 活动关优先条目 | 存在 ⇔ `IfActivityFirst ∧ 活动列表非空`；`StagePlan=[已解析活动关]` |

### 三级证据

- **L1 注入值**：`<MAA目录>/config/gui.new.json` → `Configurations.Default.TaskQueue` 里找 `TaskType=="Fight"` 条目（可能多条：理智作战/剩余理智/剿灭作战/活动关优先，用 `Name` 区分）。
  live 样本（`MAA_6`, 2026-09-28 06:0x）：`理智作战` 条目 `"StagePlan":["SR-7","1-7"], "Series":0, "MedicineCount":0, "UseMedicine":false, "UseExpiringMedicine":true, "EnableTimesLimit":false, "IsEnable":true`（与该号 Fixed 值一致）。
- **L2 核心实收参数**：`<MAA目录>/debug/asst.log` 的 `append_task Fight` 行（MAA 真正下发的 params）。
  live 样本（`MAA_6`, 2026-09-28 06:03:52）：
  `asst::Assistant::append_task Fight {"stage":"1-7","medicine":0,"medicine_expire_days":2,"stone":0,"times":2147483647,"series":0,"DrGrandet":false,"report_to_penguin":true,"report_to_yituliu":true,"server":"CN","client_type":"Official"}`
  ⇒ 可核 `stage/series/times/medicine/medicine_expire_days`；注意 `asst.log` 只保留约 2 天（更长历史看 `asst.bak.log`）。
- **L3 行为锚点**：`<MAA目录>/debug/gui.log`：`开始任务: 理智作战` / `完成任务: 理智作战` / `剩余理智` / `关卡计划内没有当日开放的关卡`（跳过）/ `GetFightStage: from [...], selected <关>`；业务日志在 `history/<date>/<账号名>/*.log`。

**不一致处置**：落差在"请求 vs L1"⇒ 注入问题，stop + 还原 + 修配置；"L1 vs L2"⇒ MAA 读取/版本问题，同样止损；"L2 vs 行为"（如从未 `开始任务`）⇒ 见 §6-11（活动关关闭导致跳过等）。

---

## 6. 陷阱清单

1. **Temp 快照还原**：`<MAA目录>/config` 在轮起被整体备份到 `data/<sid>/Temp`（manager.py:139-142），轮末**覆盖还原**（:256-260，先 rmtree 再 copytree）。改 MAA 文件=白改；连"跑完再看文件"看到的也已是还原态。
2. **运行中配置锁定**：lock 从 prepare（manager.py:124-125）到 final_task（:199）；期间一切写被拒（ConfigBase.py:1099 等；项级 :716-717）。⇒ 还原若撞上锁定 ⇒ 重试；**别 kill MAA**，要停就用 `dispatch/stop`（触发 unlock+还原链）。
3. **`Fixed` vs 计划表**：写错层=无效写。先读 `Info.StageMode`；`Fixed` 读 `Info.<7键>`（:782-786），uuid 读该计划表（:787-794）。计划表可被多号共用 ⇒ 不直接改存量表。
4. **`EnableTimesLimit` 硬关（仅简洁）**：:834-835；"只刷 N 次"没有配置路径（§2 行 4）。`EnableTargetDrop` 同理（:836）。
5. **脚本 ↔ 实例 ↔ 槽位映射**：脚本 `Info.Path`=MAA 目录（槽位，:347-348）；`Emulator.Id/Index`=实例。同目录多脚本互斥（第二个会被拒 `已在运行`，task_manager.py:320-321）；ADB 口换算见 `MAS_EMULATORS.md`。
6. **多号脚本会串行跑全部启用号**：用户列表= `Status ∧ RemainedDay≠0`（manager:146-161）；**发车粒度只到脚本/队列**（task_manager.py:305-316）。"只跑一个号"没有直接入口；队列也救不了（同脚本多号只会折叠成一条脚本项——`tools/auto_mas_rerun.py` 实测口径）。
7. **读回窗口是"当前号"**：多号脚本每号启动前重写 gui.new.json（:540→:544）；读完即改属正常。若目标号不是第一顺位，按号读回要等它的窗口；只读"文件里现在是什么"容易测错人。
8. **先剿灭后日常**：:464；未完成本周剿灭时，第一段注入是剿灭配置（`StagePlan=["Annihilation"]`）——读回断言要区分模式，别把剿灭值当差错。
9. **`Series` 整除警告**：`战斗次数 (N) 无法被 代理倍率 (S) 整除, 可能无法用尽`（zh-cn.xaml:1586）是正常噪音；但拿 `Series` 近似限次时，尾部会以"整组"为粒度多打/收不住（`FightTimesTaskPlugin.h`：`m_fight_times += m_series_current`）。
10. **计划表周模式按日解析**：`Weekly` 用 **UTC+4 当日**取值（config.py:2283-2300）⇒ 同表不同天结果不同；写回时别忘 weekday 组。
11. **主战斗可能被整体跳过**：候选关卡全都"当日不开放"时 MAA 记 `关卡计划内没有当日开放的关卡` → `任务跳过`，当轮只剩剩余理智（09-18 起 SR-7 事故，见药审计 §3.2/§5）。排"只刷 X 关"时给候补（如 `Stage_1=1-7`）或用常驻关。
12. **删表副作用**：`plan/delete` 会把仍指向它的账号自动改回 `Fixed`（core `config.py:1651-1681`）；目标号在跑且被锁时删除会抛错（`正在使用此计划表且被锁定`）。⇒ 先还原 `StageMode` 再删表。
13. **写面校验**：plan/user 更新是"按组按键"写（`ConfigBase.update` → `set`），未知键 `AttributeError`；plan schema `extra="forbid"`（schema.py:1685-1697）⇒ 错键/多键直接 500。键名大小写敏感（`Stage_1` 不是 `stage_1`）。
14. **REST 代理坑**：机内工具写 REST 必须绕系统代理（`ProxyHandler({})`）——内网 IP 走 `127.0.0.1:7890` 会间歇 502（`tools/mas_stage_fix.py` 头注）。
15. **04:00 / 19:00 宿主每日重启**（Windows 计划任务，事件 1074）：在跑任务会被中断（触发 final_task）；排窗避开，跨过 04:00 的补跑会"无法再补"（`t_564593ae` 实测）。
16. **重试循环**：单号未成功时 AutoProxy 会按 `Run.RunTimesLimit` 重试（:486），每次重试都会重新 `set_maa`（重新注入）——读回校验要容忍"同一值被重写"与重启模拟器。
17. **回读时序**：L1 校验晚于注入；若已开打才发现注入错，最稳是 `dispatch/stop` 止损（自动解锁+Temp 还原），别边跑边改。

---

## 7. 对卡片原始表述的勘误（逐条，含行号）

| 卡片原话 | 勘误 |
|---|---|
| "计划表 API `/api/plan/{add,get,update,delete,**sort**}`" | 实际是 `/api/plan/**order**`（`app/api/plan.py:127-134`，host 同名同壳）。 |
| "**"只刷几次"要用 `Series`（连战次数），不是 `TimesLimit`**" | 只对了一半。`Series` 是"**每组连战几把**"（代理倍率），注入 `Fight.Series`（:819），**不构成总数上限**；总数上限是 `TimesLimit`（MAA `FightTimesTaskPlugin`：times=最大战斗次数）。因为简洁把 `EnableTimesLimit` 硬关（:835）才"没有现成字段"⇒ 正确表述：**简洁下无配置路径；可选 Series 近似 / 补丁放开 / 详细模式 ConfigFile**。 |
| "「正常刷 vs 只刷几次」⇒ 是否保留"剩余理智"任务（`Stage_Remain` / **对应 If 开关**）" | 没有"对应 If 开关"。门 = `Task.IfFight ∧ Stage_Remain != "-"`（:911-916）；挂/摘剩余理智就是改 `Stage_Remain`（"-"=摘）。 |
| "…（只想跑一个号要**临时队列**）" | 队列粒度=**脚本**（task_manager.py:288-316；`tools/auto_mas_rerun.py` 同脚本折叠）；多号脚本建队列仍会串行跑全部启用号。单号三条路：① 用单号脚本；② 临时停用其它号（写 `Info.Status=False`，同样要快照/还原）；③ 接受整脚本跑。现存按需工具（`tools/mas_ondemand.py`、`tools/mas_ondemand_mcp.py::dispatch_account`，t_1aa55dac）即按此口径实现：建「临时-按需-*」队列=单脚本项，回执里明确标注「该脚本全部启用用户会被跑到（AutoProxy 语义）」。 |
| "`EnableTimesLimit` **被硬设为 False**（:835）" | 需加限定：**仅简洁分支**（:834 条件）。详细模式不触这两键（沿用该号 ConfigFile）。 |
| "`StagePlan`（:820-829 按 stage_key 拼）" | 实际 :820-828（含 `*`→`""` 转译）；:829 是 `IsStageManually=True`。行号小差，正文已按现状。 |
| "行号（:782-793/:805/:815/:819/:835/:915）" | 与宿主 09-28 现状吻合（本轮逐条复核）。注意本地 `D:\MAS`（1090 行旧版）与 09-27 旧文档对不上——**引用一律取本页口径**。 |

---

## 8. 未验证项与限制（诚实清单）

1. **真发车未做**（本卡只读）；`tools/mas_run_profile.py` 的 `--apply` 最小实跑属 `t_b2a74be1` 的验收项。
2. **活动关优先**注入路径未真机验证（全队 `IfActivityFirst=false` 休眠中）；活动过期后该任务无意义（`_resolve_activity_stage` 可返回 None ⇒ 不插）。
3. **`Series` 近似限次**未实测（语义推导 + 整除警告源码；真实边界=理智不足一整组时停）。
4. **补丁方案未实施**（在注入处放开 `EnableTimesLimit/EnableTargetDrop` 或加计划表字段）：属"可选"，做的话要留 `.bak` + **重启侧车**才生效。
5. **多号脚本的按号读回窗口**未实测（推导自 :540→:544 与 :464 的循环结构）。
6. **药物字段**（`UseExpiringMedicine` 等）当前不在 AUTO-MAS 两层，改它只能动模板/ConfigFile/补丁；主战斗值随模板（08-23 起全队 true，见药审计 §1.1）。
7. `asst.log` 保留约 2 天；跨日复核核心参数要趁早或依赖 `asst.bak.log`。
8. `app/core/config.py`（core 层）本地与宿主**不一致**（MD5 实测），本页引用其行号时只认宿主（3206 行版，mtime 09-24 00:39）。

---

## 9. 证据索引

**宿主代码（行号口径，2026-09-28 抓取）**
- `app/task/MAA/AutoProxy.py`（1153 行）：:347-351 路径 | :464 模式循环 | :471-480 task_dict | :486 重试 | :540/:544 set_maa→启 MAA | :592-733 set_maa 装配（:607-621 ConfigFile 源、:644-659 活动解析、:662-679 任务模板、:687-697 关定时、:708-733 PostActions/RunDirectly/StartEmulator） | :758-779 切号（v2 仅官服） | :782-794 7 键取值 | :796-837 Fight 组装（:834-837 简洁托管） | :887-908 活动关插入 | :910-928 剩余理智 | :930-934 导出 | :938-1006 日志判定 | :253-280 活动关构建 | :236-250 活动关序号解析
- `app/utils/constants.py`（1094 行）：:57-66 MAA_TASKS | :113-121 MAA_STAGE_KEY | :150-187 剿灭 base（:165 临期 true） | :191-228 剩余 base（:206 临期 true）
- `app/task/MAA/manager.py`（272 行）：:67 check | :121-142 prepare（:124-125 lock、:139-142 Temp 备份、:146-161 用户列表） | :191-262 final_task（:199 unlock、:256-260 还原）
- `app/task/task_manager.py`（450 行）：:287-316 taskId 解析 | :320-321 运行中拒绝
- `app/api/plan.py` :33-134（/add /get /update /delete /order）；`app/api/scripts.py` :398-415（/user/update）；`app/api/dispatch.py` :34-70（/start /stop）；`app/api/queue.py` :34-135（队列增删改查/排序）、:232-322（队列项增删改查/排序）
- `app/models/config.py`（3606 行，=本地）：:393-603 MaaUserConfig（:499-501 药、:503-508 连战、:510-518 关卡键、:584-599 活动关、:412-419 StageMode） | :2244-2300 MaaPlanConfig（:2262-2281 字段、:2283-2300 按日解析）
- `app/models/ConfigBase.py`（1503 行，=本地）：:1003-1034 set（锁定拒绝） | :1112-1140 lock/unlock 递归
- `app/models/schema.py`（=本地）：:1685-1697 plan 字段与 extra=forbid | :1689-1691 SeriesNumb 域 | :1778-1779/:2111-2137 编解码壳

**MAA 侧（v6.18.0；`D:\1-2\_maa_src` core clone + mas-cache wpf 源码）**
- `FightTask.cs:32-165`（字段与默认） | `FightSettingsUserControlModel.cs:1272-1275`（临期窗口组参）、:1688（effectiveMaxTimes）、:1703-1705（整除警告） | `AsstFightTask.cs:31-62`（core 参数名：stage/medicine/medicine_expire_days/stone/times/series） | core `FightTask.cpp:88-102`（times/series 校验与装配） | core `FightTimesTaskPlugin.h`（计数语义） | `zh-cn.xaml:1010-1021`（SeriesTip）、:1586（整除警告文案）

**live 样本（2026-09-28 06:0x）**
- 计划表 5 张 / 用户 60 号结构（只读 REST 抓取，字段白名单+脱敏）：`MedicineNumb=0`、`SeriesNumb="0"` 为全队现状；58 号 `Fixed`、2 号计划表模式。
- `MAA_6/config/gui.new.json`（节选见 §5 L1）；`MAA_6/debug/asst.log` append_task 行（§5 L2）。
- `data/378f7379…/Default/ConfigFile/gui.new.json`（简洁模板：Fight `StagePlan=["1-7"]`、`UseExpiringMedicine=true`、`EnableTimesLimit=false`、`TimesLimit=2147483647`）。

**既有工具先例（写面/闸门/审计可复用）**
- `tools/mas_stage_fix.py`（用户配置最小键写：备份+读回+回滚+绕代理）
- `tools/auto_mas_toggle_user.py`（Status 写面语义） / `tools/auto_mas_rerun.py`（临时队列建删+同脚本折叠实测）
- `tools/mas_preflight.py`（GO/NO-GO） / `tools/mas_switch_gate.py`（注入闸门口径） / `tools/mas_dispatch.py`（发车骨架）
- `docs/MAS_SCENARIO_DISPATCH.md`（相邻路线；其行号为 09-27 版，勿套）
