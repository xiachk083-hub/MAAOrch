# 排查：理智药为何没按 48h 快过期优先使用（含修复草案）

- 任务：`t_ac9d3099`（board `maa-parallel-benchmark-20260926`，assignee `mas`）
- 日期：2026-09-27（数据抓取截至约 20:19；晚班运行中，09-27 数据不完整）
- 对象：宿主 AUTO-MAS（`E:\AUTO-MAS`，2026-09-27 19:53 版）＋ MAA-Multi（`E:\MAA-Multi`，MAA v6.17.5 → v6.18.0）
- **只读**：本审计未修改任何配置或代码、未发车；仅读取文件 / REST / 日志。
- 行号口径：AUTO-MAS 侧为当日抓取的宿主副本（`E:\AUTO-MAS\app\...`）；MAA 侧为本地 clone 的 tag `v6.17.5` / `v6.18.0`。每条结论均附出处（文件＋行号或链接）。

---

## 0. 结论速览（TL;DR）

1. **“没按 48h 吃”的当前主因不是药物配置**：机队的主「理智作战」任务自 **2026-09-18 04:00 起被 MAA 整体跳过**（`关卡计划内没有当日开放的关卡` → `理智作战: 理智作战 任务跳过`）。跳过时当轮只剩「剩余理智」（`UseExpiringMedicine=false`，设计如此）出战 → **临期药完全没有任何消耗路径** → 到期浪费。09-18～09-27 期间机队日志 1333 个文件中 **826 个（62%）** 含跳过；每日受影响的账号目录数 43～45 个。
2. **药物相关配置本身有效且符合预期**：全部 98 个脚本运行时模板、26 个轮起快照、16 个在线槽位配置的「理智作战」均为 `UseExpiringMedicine=true` + `MedicineExpireDays=2`（= 48h 窗口）；配额 `MedicineNumb` 全 0，**不影响**临期药消耗（核心语义相互独立，见 §2.2）。
3. **管道健康实证**：08-23～09-12 机队共消耗约 440 瓶临期药（周节奏波浪）；09-26 单账号（音乐厅生动的深海鱼）一夜连吃 8 瓶。09-18 后：**所有主关卡不可用的账号用药记录 = 0**，而主关卡正常的账号（1-7 / CE-6 等）仍有消耗 → 完美反相关（§3.3）。
4. **旧线索纠正**：卡片线索③所指“配置里是 false”的来源为 `MAA_6\config\gui.new.json.bak_expmedicine`（2026-08-18 的试验备份，非生效配置）；生效模板自 ≤08-23 起全部为 true（§1.3）。
5. **修复要点**：(⓪) 修复 46 个 SR-* 关卡账号的关卡配置（或补回退位）——这是真正恢复“吃药”的动作；(①) 可选把「剩余理智」（及休眠中的「活动关优先」）也设 `UseExpiringMedicine=true` 作安全网；(②) 可选把该开关暴露进 AUTO-MAS 用户配置。详 §6。
6. 卡片中“配额 0 ⇒ 无论是否勾选都不吃药”（方案③前提）**不成立**：`MedicineCount=0` 且临期窗口>0 时核心照样吃临期药（源码 + 7 周实证，§2.2/§2.3）。

---

## 1. 现状盘点

### 1.1 配置层级与关键值（全部核对于 2026-09-27）

| 层级 | 位置/数量 | 关键值 | 证据 |
|---|---|---|---|
| 计划表 | REST `get_plan` ×5 张 | `MedicineNumb=0` 全部 | MCP `get_plan`（2026-09-27） |
| 用户表 | REST `get_user` ×60 账号 | `I.MedicineNumb=0` 全部；`T.IfActivityFirst=false` 全部；`T.ActivityMedicineNumb=0` 全部；`I.SeriesNumb="0"` 全部 | `users_masked.tsv`（字段白名单+掩码） |
| 脚本模板（简洁模式配置源） | `E:\AUTO-MAS\data\<sid>\Default\ConfigFile\gui.new.json` ×98 脚本 | 「理智作战」`UseExpiringMedicine=true`、`MedicineExpireDays=2`、`UseMedicine=false`、`MedicineCount=0`，98/98 一致 | `med_full_out.txt` S2/S9；`templates_summary.txt` |
| 注入组装（AUTO-MAS） | `app\task\MAA\AutoProxy.py` | 主 Fight 不动该字段（继承模板）；剩余理智 merge 基础块（false）；剿灭 merge 基础块（true）；活动关硬编码 false | 见 §2.6 行号 |
| 轮起快照（运行态） | `E:\AUTO-MAS\data\<sid>\Temp\gui.new.json`（抓取时 26 个在盘） | 主 true / 剩余 false，含 08-23、08-24 等旧快照 | `med_full_out.txt` S3/S4 |
| 在线槽位 | `E:\MAA-Multi\MAA_*\config\gui.new.json`（16 个目录） | 逐槽位扫描：主「理智作战」全部 `UseExpiringMedicine=true`（含 13/14/25 等未启用槽位） | 2026-09-27 在线扫描（PowerShell） |
| 核心参数（运行证据） | `E:\MAA-Multi\MAA_6\debug\asst.bak.log`（覆盖 09-24 20:14～09-26 21:28） | 剩余理智：`{"stage":"1-7","medicine":0,"medicine_expire_days":0}` ×23；剿灭：`{"stage":"Annihilation",...,"medicine_expire_days":2}` ×2；**主 Fight 的 append 在保留窗口内缺席 = 因跳过从未下发** | asst.bak.log grep |

### 1.2 平台与版本

- MAA：机队主力 **v6.18.0**（Built 2026-09-24，DLL 于 09-23 批量更新；实例日志实见 `Version v6.18.0`，如 2026-09-27 汐影月k/y9Flipped 的运行日志）。部分旧槽位仍在 v6.16.8（MAA_13/14，未启用）。
- 关键差异仅 GUI 文案：v6.17.5 `无限吃 N 小时内过期的理智药`（zh-cn.xaml L744）→ v6.18.0 `无限吃 N 天内过期的理智药`（zh-cn.xaml L793）。**核心行为两版一致**（`git diff v6.17.5 v6.18.0 -- src/MaaCore/Task/Fight/` 仅 `StageNavigationTask.cpp/.h` 有改动）。
- 历史日志中两种格式并存（`已使用 48 H内过期理智药` = 旧版文案；`已使用 2 天内过期理智药` = 6.18 文案），后续检索需同时匹配。

### 1.3 `.bak_expmedicine` 旧线索的纠正

- `E:\MAA-Multi\MAA_6\config\` 下存在 `gui.new.json.bak_expmedicine`（内容 `UseExpiringMedicine=false`）与 `.bak_expmedicine2`（true，且活动字段 true）——为 2026-08-18 前后的人工试验备份，**不是生效配置**。
- 生效链路（模板 → 注入 → 运行态 → 在线配置）自 ≤08-23 起该字段一律为 **true**（§1.1 各行）。卡片“样本里是 false”因此不能作为“当前配置为 false”的证据。

---

## 2. 语义（MAA v6.17.5/v6.18.0）

### 2.1 字段准确定义

| 字段 | 含义 | 承载位置 |
|---|---|---|
| `UseMedicine` | 是否启用“普通额度”吃药 | C# `FightSettingsUserControlModel.BuildAsstFightTask` L1331：`Medicine = UseMedicine != false ? MedicineCount : 0` |
| `MedicineCount` | 普通额度（0-9999） | 同上；核心参数名 `medicine` |
| `UseExpiringMedicine` | 是否启用“临期药窗口” | C# L1324：`var expireDays = UseExpiringMedicine ? MedicineExpireDays : 0;` |
| `MedicineExpireDays` | 窗口天数 N（样本值 2=48h） | C# L1335：`MedicineExpireDays = Math.Max(expireDays, activityExpireDays)`；核心参数名 `medicine_expire_days` |
| `UseExpireMedicineForActivity` | “活动结束前 48H 把窗口扩到本周末”的加速器（**不是**“活动关吃不吃”的开关） | C# L1315-1327；文案 zh-cn.xaml L795 |
| （活动关独立额度）`ActivityMedicineNumb` | 活动关专用配额，用户模型 `legacy_name=MedicineNumb` | `app/models/config.py` L592-598 |

### 2.2 “临期药”独立于“普通配额” —— 结论：**是**（三重证据）

1. **源码**：`MedicineCounterTaskPlugin.cpp`（v6.18.0）
   - L38-42 跳过条件：`if (m_used_count >= m_max_count && m_expire_days == 0) { "Needn't to use medicines" return true; }` —— **只有“额度用尽且窗口=0”才不吃**；窗口>0 时继续流程。
   - L85-99 临期分支：`else if (m_used_count >= m_max_count && m_expire_days > 0) { … 取消勾选 expire_days > m_expire_days 的药 … }` —— 额度用尽后，按“窗口”筛选留吃。
   - L188-201 上报：`info["details"]["is_expiring"] = m_used_count > m_max_count;` —— 标记“超出配额使用的即临期药”，日志据此输出 `已使用 N 天内过期理智药`。
2. **文案**：zh-cn.xaml L793 `无限吃 N 天内过期的理智药`（“无限”即不受配额限制）；L794 `只吃游戏内显示不足 N 天的理智药，显示“N”天及以上的不吃。`
3. **实证**：全部 60 个账号 `MedicineNumb=0`（§1.1），但 08-23～09-12 机队消耗约 440 瓶（§3.1），09-26 音乐厅生动的深海鱼一晚 8 瓶（§3.4）——若“配额 0=不吃”成立，这些都不可能发生。

**边界与细节**：
- 窗口判定基于游戏内显示的剩余天数：核心 OCR 读到显示值 `day` 后存 `day+1`（`MedicineCounterTaskPlugin.cpp` L271 `// 向上取整补足完整天数`），与窗口比较 `stored > m_expire_days → 取消勾选`（L89）。N=2 ⇒ 吃“显示不足 2 天”的药（“1 天/23 小时”等），即约 48h 窗口；显示“2 天及以上”不吃（L794 原文）。
- 混合选择安全阀：若选中集合“超量且含非临期药”，插件中止本次使用（L149-174 返回 false），不会误吃普通药。
- 普通额度下的计数规则：`当未超出使用次数上限时, 临期药也当做普通药品使用`（L78 注释）——即额度未满时按普通药计数输出（历史日志中“已使用理智药”不带“过期”字样属该口径）。
- 兼容：核心 v6.8.0 起 `expiring_medicine` 更名为 `medicine_expire_days`（`src/MaaCore/Task/Interface/FightTask.cpp` L70-85，含弃用告警）。

### 2.3 核心参数链路

- GUI → `BuildAsstFightTask`（C# L1312-1350）→ `AsstFightTask`（`Medicine`/`MedicineExpireDays`）→ 核心 `Interface/FightTask.cpp` L70-85 读取；L171-177 `set_times_limit("MedicineConfirm", medicine)`、`set_times_limit("ExpiringMedicineConfirm", expire_days==0?0:9999)`、`m_medicine_plugin->set_count/set_expire_days`。
- 实际下发样本（`asst.bak.log`）：
  - 剩余理智：`append_task Fight {"stage":"1-7","medicine":0,"medicine_expire_days":0, ...}`（窗口 0 ⇒ 不吃）
  - 剿灭：`append_task Fight {"stage":"Annihilation","medicine":0,"medicine_expire_days":2, ...}`（窗口 2 ⇒ 吃）
- `set_expire_days(0)` 语义 = “不使用过期药品”（`MedicineCounterTaskPlugin.h` L51 注释）。

### 2.4 活动关字段的精确语义

- `UseExpireMedicineForActivity`：**只有当“当前活动在 2 天内结束” 且该开关开启时**，把窗口从 N 天扩到“距本周末的天数（鹰历）”（C# L1315-1327，`activityExpireIn2Days` → `daysUntilEndOfWeek`）。目的是防活动结束时仓库临期药来不及用完。
- 它**不是**“活动关任务是否吃临期药”的开关——那个由任务组装处决定（AutoProxy L278 硬编码 false，见 §2.5）。当前该功能全队休眠（`IfActivityFirst=false` ×60，`ActivityMedicineNumb=0` ×60）。

### 2.5 AUTO-MAS 侧组装语义（宿主 `app\task\MAA\AutoProxy.py`）

- 合并函数 `_merge_fight_task`（L169-172）：`{**source, **managed}` —— **基础块的字段覆盖模板字段**。
- 主「理智作战」：Routine 分支 L812-831 只改 `UseMedicine/MedicineCount/Series/StagePlan/IsStageManually/UseOptionalStage/UseWeeklySchedule`，**不触碰 `UseExpiringMedicine`** ⇒ 沿用模板继承值（实际=true）。
- 「剩余理智」：L908-928，`_merge_fight_task(fight_source, MAA_REMAIN_FIGHT_BASE)`；基础块 `MAA_REMAIN_FIGHT_BASE`: `UseExpiringMedicine=False`（`app/utils/constants.py` L191-207，关键行 L206）。
- 「剿灭」：L796-810，`merge MAA_ANNIHILATION_FIGHT_BASE`；该基础块 `UseExpiringMedicine=True`（constants.py L150-166，关键行 L165）。
- 「活动关优先」：构建函数 L253-280（`UseExpiringMedicine/UseExpireMedicineForActivity` 硬编码 false，L278-279；L256 注释“使用独立理智药额度”）；仅由 `IfActivityFirst` 控制（L645），全队关闭 ⇒ 休眠。
- 简洁模式配置源：`shutil.copytree(data/<sid>/Default/ConfigFile → 槽位config)`（L608-609；详细模式用用户目录 L614-617）⇒ 简化用户的药物字段直接来自脚本模板（§1.1 第 3 行）。

---

## 3. 时间线与取证

### 3.1 临期药消耗波浪（全机队 history 日志，6847 文件扫描）

| 日期 | 消耗药数 | 日志行 | 账号数 | 文案 |
|---|---|---|---|---|
| 08-23 | 58 | 52 | 14 | 48 H |
| 08-26 | 2 | 2 | 1 | 48 H |
| 08-29 | 160 | 136 | 34 | 48 H |
| 09-02 | 9 | 9 | 3 | 48 H |
| 09-03 | 3 | 3 | 2 | 48 H |
| 09-05 | 146 | 121 | 34 | 48 H |
| 09-09 | 4 | 4 | 2 | 48 H |
| 09-12 | 58 | 48 | 14 | 48 H |
| 09-13 | 0 | （普通药口径 3 行） | 3 | REG=3 |
| 09-16 | 3 | 3 | 2 | 48 H |
| 09-19 | 5 | 5 | 4 | 48 H |
| 09-23 | 2 | 2 | 2 | 48 H |
| 09-24 | 1 | 1 | 1 | 48 H |
| 09-26 | 15 | 12 | 5 | 2 天 |

（来源：`usage2_out.txt`；09-27 截至抓取时 = 0。08-23～09-12 合计 ≈440 瓶。）

### 3.2 关卡活动窗口与“跳过疫情”

**活动窗口（权威源：api.maa.plus `gui/StageActivityV2.json`，2026-09-27 抓取）**：
- SideStory「墟」复刻（AT-8/7/6/4）：`2026/08/22 04:00 ～ 2026/09/01 03:59:59`
- SideStory**「月行水上」（SR-8/7/6/5）：`2026/09/04 12:00 ～ 2026/09/18 03:59:59`**

**逐日跳过统计**（`skip_stats_out.txt`，文件数/当日跳过账号目录数）：
`09-01: 12/6（墟关到期首波） · 09-02: 6/6 · 09-07: 1/1 · 09-08: 1/1 → 09-18: 68/43 · 09-19: 89/43 · 09-20: 54/45 · 09-21: 91/45 · 09-22: 72/45 · 09-23: 119/43 · 09-24: 123/43 · 09-25: 129/43 · 09-26: 56/39 · 09-27: 25/13（当日仅凌晨+上午批次）`
- 09-18～09-27 合计：**1333 个日志文件，826 个含跳过（62%）**。
- 在册 60 个账号中 **46 个** 有跳过记录；其中 **39 个首跳于 09-18 之后**（全部为 SR 关卡账号）。主关卡=SR-* 的账号共 46 个（SR-7×40、SR-8×3、SR-5×3）——与“SR-7 于 09-18 04:00 随活动关闭后 `IsStageOpen` 恒 false”闭环。

**机制（MAA 源码）**：
- 选关：`FightSettingsUserControlModel.GetFightStage`（v6.18.0 L1197-1202）：`list.FirstOrDefault(s => Instances.StageManager.IsStageOpen(s, CurDayOfWeek))` → 计划内无“当日开放”关卡 ⇒ `selected null`。
- 序列化拦截：同文件序列化路径 L1675-1690：`stage is null` ⇒ 日志 `FightSkippedNoOpenStage`（zh-cn.xaml L588 = `关卡计划内没有当日开放的关卡`）⇒ 返回 `(null, [])`。
- 任务跳过：`TaskQueueViewModel.cs` L2400-2412：`case null: AddLog("TaskAppend.Skip", ...)` ⇒ `理智作战: 理智作战 任务跳过`。
- 后果：该轮**只有「剩余理智」出战**（`medicine_expire_days=0`，不到药）；AUTO-MAS 侧把“任务跳过”计为已完成（AutoProxy L970-1000 的状态识别同时匹配 `完成任务:` 与 `任务跳过`），因此**上层看板显示正常、问题静默**。

### 3.3 相关性验证（skippers ↔ 零消耗）

- **跳过账号（39+）在 09-18 后用药记录 = 0**；唯一例外 `河清灯_别寄圆通` 的跳过发生在 08-21（历史小事件），09-26 正常消耗（其 StageMode=计划，不受 I.Stage 影响）。
- **非跳过账号** 09-13 后仍有消耗：`音乐厅生动的深海鱼`、`hbznn119-日`、`排前联系我有的不能发`、`欧哈呦学妹`、`石头城修行的深海鳗`、`Raiz`（全部主关卡为 1-7/CE-6 等常驻关）。
- 08-23～09-12 的波浪消耗账号（y9Flipped、许大胖子、烤箱、MikuKong 等——其后成为 SR-skipper）证明：**只要主战斗实际出战，临期药消耗管道工作正常**。

### 3.4 样例摘录

**A. 跳过现场**（`history/2026-09-27/汐影月k/明日方舟-官-1-1-7-06-54-27.log`）：
```
[2026-09-27 10:54:54.428][INF][FightSettingsUserControlModel] <2> GetFightStage: from ["SR-7"], selected null
[2026-09-27 10:54:54.428][INF][TaskQueueViewModel]     <2> 关卡计划内没有当日开放的关卡
[2026-09-27 10:54:54.431][INF][TaskQueueViewModel]     <2> 理智作战: 理智作战 任务跳过
[2026-09-27 10:54:54.432][INF][TaskQueueViewModel]     <2> Index 2, Type "Fight", Name 剩余理智, IsEnable true
```
（随后仅「剩余理智」1-7 出战；该账号此后无任何药剂消耗。）

**B. 正常消耗现场**（`history/2026-09-26/音乐厅生动的深海鱼/明日方舟-官-2-4-47-16-15-01.log`）：
```
20:16:30  GetFightStage: from ["CE-6"], selected CE-6
20:19:10  开始任务: 理智作战
20:20:01  已使用 2 天内过期理智药 2(+2)
…… 4(+2) / 6(+2) / 7(+1) / 8(+1)
20:36:17  完成任务: 理智作战
```
（单轮 8 瓶，配额 0，纯临期窗口消耗。）

**C. 旧版文案现场**（`history/2026-09-16/欧哈呦学妹/明日方舟-日-5-03-09-09.log` L136）：`已使用 48 H内过期理智药 1(+1)`。

---

## 4. 逐账号盘点（60 账号全量）

（自动生成：跳过统计来自全量 history 日志；用药日期来自 `已使用…理智药` 行；配置值来自 2026-09-27 用户表快照。“主关卡”为 `I.Stage` 快照，其中 StageMode=计划的账号见下方表注①。）

| 账号 | 服 | 主关卡(I.Stage) | 计划药量 | 近7天(09-21..27)用药 | 最后用药 | 09-18后跳过 | 跳过天数 |
|---|---|---|---|---|---|---|---|
| 145三月初发货 | Official | SR-7 | 0 | - | 2026-09-12 | 是 | 10 |
| Buling | YoStarJP | 1-7 | 0 | - | - | 否 | 0 |
| Gitano | Official | SR-5 | 0 | - | 2026-09-05 | 是 | 8 |
| Kaori | Official | SR-7 | 0 | - | - | 否 | 0 |
| LAZA | YoStarJP | 1-7 | 0 | - | 2026-09-19 | 否 | 0 |
| LEUNG | Official | SR-7 | 0 | - | - | 是 | 9 |
| MikuKong | Bilibili | SR-7 | 0 | - | 2026-09-12 | 是 | 9 |
| PAGI | Official | SR-7 | 0 | - | 2026-09-12 | 是 | 10 |
| Raiz | txwy | CE-6 | 0 | 2026-09-26 | 2026-09-26 | 否 | 0 |
| Yeee | Official | SR-7 | 0 | - | 2026-09-12 | 是 | 9 |
| badwxxx | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |
| emmmmmm | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 10 |
| hbznn119-官 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 5 |
| hbznn119-日 | YoStarJP | 1-7 | 0 | 2026-09-23 | 2026-09-23 | 否 | 0 |
| him | Official | SR-7 | 0 | - | - | 是 | 12 |
| rzzz | Official | SR-7 | 0 | - | 2026-09-12 | 是 | 10 |
| tbNick_2l3vf | Official | SR-8 | 0 | - | - | 是 | 10 |
| tbNick_h56zy | Bilibili | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |
| v | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 2 |
| x7 | Bilibili | SR-7 | 0 | - | 2026-08-29 | 是 | 9 |
| y9Flipped | Official | SR-7 | 0 | - | 2026-09-12 | 是 | 10 |
| ૮ต蓝熹ตა | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |
| 不要加班了 | YoStarJP | PA-6 | 0 | - | 2026-09-03 | 否 | 0 |
| 以七 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |
| 古茶七心红 | Official | SR-7 | 0 | - | - | 是 | 7 |
| 史蒂芬芬 | txwy | 1-7 | 0 | - | - | 否 | 0 |
| 喵喵喵喵 | Official | SR-8 | 0 | - | 2026-09-12 | 是 | 9 |
| 寻找勇者的大魔王 | Official | SR-7 | 0 | - | 2026-09-13 | 是 | 9 |
| 小熊附体 | Bilibili | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |
| 岁岁iyou | Official | SR-5 | 0 | - | 2026-09-12 | 是 | 9 |
| 帝国猛虎 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 10 |
| 拍前看简介 | Bilibili | SR-7 | 0 | - | 2026-09-05 | 是 | 11 |
| 排前联系我有的不能发 | YoStarJP | 1-7 | 0 | 2026-09-26 | 2026-09-26 | 否 | 0 |
| 摸摸摸摸鱼 | Bilibili | SR-7 | 0 | - | 2026-09-05 | 是 | 7 |
| 檀丝江喜 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |
| 欧哈呦学妹 | YoStarJP | 1-7 | 0 | 2026-09-23,2026-09-24 | 2026-09-24 | 否 | 0 |
| 汐影月k | Official | SR-7 | 0 | - | - | 是 | 10 |
| 河清灯_别寄圆通 | YoStarJP | CE-6 | 0 | 2026-09-26 | 2026-09-26 | 是 | 1 |
| 潮响 | Official | SR-8 | 0 | - | 2026-09-12 | 是 | 10 |
| 烤箱 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 10 |
| 爱尔奎特我老婆 | Official | SR-7 | 0 | - | 2026-08-29 | 是 | 8 |
| 狂暴组长出物版 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |
| 用户_173920298 | Bilibili | SR-7 | 0 | - | 2026-08-29 | 是 | 10 |
| 用户_99606616 | YoStarJP | 1-7 | 0 | - | - | 否 | 0 |
| 白桑_ | Official | SR-7 | 0 | - | 2026-08-29 | 是 | 10 |
| 白海雾 | Bilibili | SR-7 | 0 | - | 2026-08-29 | 是 | 10 |
| 皮埃尔商店 | Official | SR-7 | 0 | - | 2026-09-12 | 是 | 11 |
| 看看我看看我啊啊啊啊 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |
| 睡个大鸡蛋 | Official | SR-7 | 0 | - | - | 是 | 11 |
| 石头城修行的深海鳗 | YoStarJP | 1-7 | 0 | 2026-09-26 | 2026-09-26 | 否 | 0 |
| 祝你祝我天天开心 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 8 |
| 笙笙wzh | Official | SR-5 | 0 | - | 2026-08-23 | 是 | 9 |
| 舒7ucky | YoStarJP | 1-7 | 0 | - | 2026-09-03 | 否 | 0 |
| 许大胖子 | Official | SR-7 | 0 | - | 2026-09-12 | 是 | 9 |
| 评估苹果 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |
| 酸酸酸酸 | YoStarJP | 1-7 | 0 | - | - | 否 | 0 |
| 门外蹲着只狗 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |
| 阿仄仄仄 | Bilibili | SR-7 | 0 | - | - | 是 | 9 |
| 音乐厅生动的深海鱼 | Official | - | 0 | 2026-09-26 | 2026-09-26 | 否 | 0 |
| 骰子 | Official | SR-7 | 0 | - | 2026-09-05 | 是 | 9 |


### 4.1 表注

① **StageMode=计划**（2 个账号）：`音乐厅生动的深海鱼`（`c6684b55…`）、`河清灯_别寄圆通`（`a18f167e…`）。它们的实际关卡来自计划表，`I.Stage` 快照值（`-` / `CE-6`）不代表运行值；两者运行正常、不受 SR 关闭影响。其余 58 个账号为 `Fixed`，以 `I.Stage` 为准。
② **无运行记录的账号**：`Kaori`（SR-7，无任何日志目录）、`Buling`/`用户_99606616`/`酸酸酸酸`/`史蒂芬芬`/`不要加班了` 等（1-7/PA-6，无跳过）。
③ **A 类/B 类（卡片口径）修正**：A 类“配额 0”= **60/60 全量**；B 类“配额>0 但临期不优先”= **0 个**（全部账号走临期通道，且一旦主战斗出战即正常消耗，§2.2/§3.3）。因此真正需要修复的不是配额/开关，而是 §5 的关卡跳过。
④ 09-27 数据不完整（晚班运行中抓取）；当日“跳过 13 账号/25 文件”为凌晨+上午批次。
⑤ `最后用药`/`近 7 天` 均来自日志痕迹；“-”= 无记录，不等于“从未用过药”（历史目录自 08-17 起）。

---

## 5. 根因判定

**主因（结构性问题，直接解释“药没吃 / 过期浪费”）**：
> 40 个 `I.Stage=SR-7`（+3 个 SR-8、3 个 SR-5）的账号，其主「理智作战」在 **2026-09-18 04:00 SR 活动「月行水上」到期后每次运行都被 MAA 跳过**（单关卡计划、无回退候选）。跳过后当轮仅「剩余理智」（`UseExpiringMedicine=false`，`medicine_expire_days=0`）出战 1-7，**临期药窗口完全没有被检查/消耗的机会**。09-18 起每天 43～45 个账号受影响，累计 826 个跳过文件；这批账号在此期间用药记录=0，其临期药自然过期浪费。

**次因 / 边界**：
- 09-13～09-17 的稀疏消耗（仅 09-16 两账号 3 瓶）：此窗口主战斗正常（SR-7 仍开放），说明**当时机队总体上没有大批临期药可吃**（供应侧不可观测）；这不改变 09-18 后的结构性问题。
- 5 个账号 `Stage_Remain="-"`（无剩余理智任务）：主战斗被跳过后当轮**完全无战斗**，影响更甚。
- 「剩余理智」设计为 `UseExpiringMedicine=false`（constants.py L206；AutoProxy L908-928 merge）——在“主战斗先跑”的正常链路下无碍（窗口药已被主战斗吃光）；但在主战斗被跳过/失败时会成为**静默漏吃**的兜底层缺陷。
- 2 个 StageMode=计划账号、10 个 1-7/CE-6 账号不受影响，佐证因果。

**明确非因**（均有反证）：
- 药物字段配置：模板/快照/在线配置一律 `UseExpiringMedicine=true`+`MedicineExpireDays=2`（§1.1）。
- 配额=0：与临期消耗无关（§2.2）。
- MAA 版本差异：核心语义跨 6.17.5/6.18.0 一致（§1.2）。
- `.bak_expmedicine` 的 false：陈旧试验备份（§1.3）。

**量化损失**：**无法精确量化**——宿主侧没有游戏仓库/药品获得数据，MAA 日志只记录“已使用”不记录库存全貌。可给的下界描述：09-18～09-27 受影响账号（≥39 个在册）每个出车日都错过 1～2 轮临期药窗口；同期全机队仅记录的 26 瓶消耗全部来自非跳过账号。进一步量化需游戏侧抽查（建议样本：y9Flipped、汐影月k、him、潮响）。

---

## 6. 修复草案（未实施；供决策）

> 共通安全说明：临期窗口只会消耗**游戏内显示不足 2 天**的理智药（§2.2），不会动“2 天及以上”的存量，不存在“把药全吃光”的口径；与抽卡/合成玉数无关（不涉及石头，`StoneCount` 路径独立）。

### ⓪ 高优先：修复关卡跳过（真正恢复消耗路径）
- **动作**：为 46 个 SR-* 账号更新 `I.Stage`（经 AUTO-MAS 用户配置/REST 正常途径）到当前开放关卡；**更稳做法**同时填 `Stage_1..3` 回退（如 `1-7`）——MAA 会在列表内自动选第一个“当日开放”的关卡（反证：`["AT-4","1-7"] → 1-7` 自动回退 57 次；单候选列表则直接跳过）。
- **影响面**：46 账号（其中在跑约 40+）；恢复主战斗与临期药消耗。
- **风险**：属于“运营目标关变更”（这些账号本已被跳过、未在实际使用 SR-7）；建议按当周活动/常驻关统一决策。
- **回滚**：改回原 `I.Stage` 即可。
- **生效**：配置类改动下一轮注入即生效（无需重启侧车）。

### ① 可选：AutoProxy 组装统一 `UseExpiringMedicine=True`（安全网）
- **改动点**（宿主 `E:\AUTO-MAS\app\`）：
  - 主「理智作战」：AutoProxy L796-820 组装处显式写入 `"UseExpiringMedicine": True`（现状靠模板继承 true，显式化防模板漂移）；
  - 「剩余理智」：`constants.py` L206 `MAA_REMAIN_FIGHT_BASE["UseExpiringMedicine"] = True`（或 merge 后覆写）——**主战斗被跳过/失败时仍能吃临期药**（本 bug 的直接兜底）；
  - 「活动关优先」：L278 改 true（当前休眠，防未来开启时同样漏吃）。
- **影响面**：全机队 60 账号 / 12 槽位；行为变化=“剩余理智”阶段也会吃临期药。
- **风险**：低。临期药会被更早消耗（这正是预期语义）；不会使用普通药（安全阀 §2.2）；`ActivityMedicineNumb` 独立额度不受影响。
- **回滚**：还原这两个文件（改动前留 `.bak`）+ 重启侧车。
- **生效**：**Python 代码改动需重启 AUTO-MAS 侧车进程**；配置类改动每轮注入自动生效。

### ② 可选：在 AUTO-MAS 用户配置暴露开关（由人决定）
- **改动点**：`app/models/config.py` 用户模型新增 `Info.UseExpiringMedicine` / `Info.MedicineExpireDays`（现仅 `Info.MedicineNumb` L499-500、`Task.ActivityMedicineNumb` L592-598）；前端表单同步（AUTO-MAS 前端源码不在本次范围，落地前需定位）；注入侧优先级建议：用户 > 计划 > 基础值。
- **影响面**：全量用户配置面扩大。
- **风险/回滚**：配置一致性成本、UI 文档同步；回滚=移除字段+恢复注入。
- **生效**：同①（代码重启侧车）。

### ③ 修正版（替换卡片原方案③）
原“只修配额=0 的账号（因为配额 0 时无论是否勾选都不吃药）”前提不成立（§2.2）。修正结论：**现状的药物配置本身无需改动**；若不做 ①②，则只需做 ⓪（关卡修复）即可恢复“48h 快过期优先使用”。但 ① 的剩余理智兜底建议仍纳入（成本极低、防复发性最好）。

### 备用硬化（建议单独立项）
AUTO-MAS 注入前对 `StagePlan` 做“当日开放”校验或自动回退/告警（防活动轮换再次静默停摆）；可复用 MAA 已有的多候选回退机制。

---

## 7. 验证步骤（改后/现状复验均适用）

1. **注入值**：下一轮开始后查 `E:\AUTO-MAS\data\<script>\Temp\gui.new.json`（或槽位 `E:\MAA-Multi\MAA_X\config\gui.new.json`）：`理智作战.UseExpiringMedicine==true`、`MedicineExpireDays==2`；或核心日志 `append_task Fight {…"medicine_expire_days":2…}` 出现主战斗 append。
2. **行为（跳过修复）**：`gui.log` 中目标账号 `GetFightStage: from [...], selected <非null>`、`开始任务: 理智作战`，且不再出现 `关卡计划内没有当日开放的关卡` / `任务跳过`（可 `rg -c "任务跳过" gui.log` 复核归零）。
3. **行为（消耗）**：出窗口账号的日志出现 `已使用 N 天内过期理智药`（6.17.x 为 `已使用 48 H内过期理智药`）+ 战斗行 `药: x,y(临期)`；MAA 日志中 `药: …, 库存: …` 明细核对库存递减。
4. **仓库侧**：游戏内抽查受影响账号（如 y9Flipped/汐影月k）——修复后第一个临期窗口内库存应下降（本次任务口径只读，未做截图）。
5. **验收样本建议**：任一 SR-skipper 账号 1～2 轮内应恢复主战斗 + 出现消耗行；任一非 skip 账号行为不应变化（回归）。

---

## 8. 数据边界与未决

- 宿主 history 日志全覆盖（6847 文件）；但 **asst.log 仅保留约 2 天**（asst.bak 覆盖 09-24 20:14～09-26 21:28），故 09-18 前“主战斗核心参数下发”的直接证据取自配置快照与行为日志，而非核心 append。
- 无库存/获得数据 ⇒ 药浪费量不可精确量化（§5）。
- 卡片中“47 个账号”与当前号表（60 用户，2026-09-27 REST）不一致——按现表口径复核（46 个 SR 账号 / 39 个 09-18 后首跳）；如“47”另有所指（如某历史快照），需补充说明。
- SR 活动「月行水上」是否复刻/回归不在本审计范围；如复刻，SR 关卡账号可恢复原计划（MAA 的 `IsStageOpen` 会随活动窗口自动放行）。
- 未决项：`Stage_Remain="-"` 的 5 个账号清单及修复方式（建议随 ⓪ 一并处理）。

---

## 附录 A：引用索引（文件＋行号）

**MAA（tag v6.18.0，`src/`）**
- `MaaWpfGui/Res/Localizations/zh-cn.xaml`：L588 `FightSkippedNoOpenStage`；L793/L794/L795 三文案；L1594/1596/1597 消耗日志文案。（v6.17.5 对应：L744/L745）
- `MaaWpfGui/ViewModels/UserControl/TaskQueue/FightSettingsUserControlModel.cs`：L1197-1202 `GetFightStage`；L1312-1350 `BuildAsstFightTask`（L1324、L1326-1327、L1331、L1335）；L1675-1690 跳过序列化（L1686 文案）。
- `MaaWpfGui/ViewModels/UI/TaskQueueViewModel.cs`：L2400-2412 `TaskAppend.Skip`（L2408）。
- `MaaCore/Task/Interface/FightTask.cpp`：L70-85 参数读取/弃用；L171-177 插件装配。
- `MaaCore/Task/Fight/MedicineCounterTaskPlugin.cpp`：L18-32 verify；L38-42 跳过条件；L78-99 计费/临期筛选；L149-174 安全阀；L182-201 上报（L189 `is_expiring`）；L244-257/L271 过期天数 OCR 与 `day+1`。`.h`：L17-21 setters；L51 注释。
- 版本差异：`git diff v6.17.5 v6.18.0 -- src/MaaCore/Task/Fight/`（仅 StageNavigationTask.cpp/.h）。

**AUTO-MAS（宿主，2026-09-27 19:53）**
- `app/utils/constants.py`：L150-166 `MAA_ANNIHILATION_FIGHT_BASE`（L165 true）；L191-207 `MAA_REMAIN_FIGHT_BASE`（L206 false）。
- `app/task/MAA/AutoProxy.py`：L169-172 merge；L253-280 活动关构建（L278-279）；L592 `set_maa`；L608-617 配置源 copytree；L796-810 剿灭；L812-837 主战斗/简洁托管；L888-893 活动关插入；L908-928 剩余理智；L930-934 写入 `gui.new.json`；L970-1000 状态识别（跳过计为完成）。
- `app/task/MAA/manager.py`：L139-142 轮起快照；L256-260 轮末还原。
- `app/models/config.py`：L499-500 `Info.MedicineNumb`；L592-598 `Task.ActivityMedicineNumb`（legacy）。

**外部数据**
- `https://api.maa.plus/MaaAssistantArknights/api/gui/StageActivityV2.json`（2026-09-27 抓取）：`Official.sideStoryStage.SSReopen`（墟 08-22～09-01）、`Official.sideStoryStage.SR`（月行水上 09-04 12:00～09-18 03:59:59）。
- 宿主 REST（端口 36163）：`get_plan`（5 张计划 MedicineNumb=0）、`get_user`（60 账号字段白名单抓取）。

**日志证据（宿主 `E:\AUTO-MAS\history` 与 `E:\MAA-Multi\MAA_6\debug`）**
- 跳过样例：`history/2026-09-27/汐影月k/明日方舟-官-1-1-7-06-54-27.log`。
- 消耗样例：`history/2026-09-26/音乐厅生动的深海鱼/明日方舟-官-2-4-47-16-15-01.log`；`history/2026-09-16/欧哈呦学妹/明日方舟-日-5-03-09-09.log`。
- 核心 append 样例：`MAA_6/debug/asst.bak.log`（1-7/d0 ×23；Annihilation/d2 ×2）。

## 附录 B：证据产物清单（scratch，可复核）

| 文件 | 内容 |
|---|---|
| `users_masked.tsv` | 60 账号字段（掩码） |
| `templates_summary.txt` / `med_full_out.txt` | 98 模板 / 全量 medicine 审计（模板、Temp、用户、diff、历史统计） |
| `usage2_out.txt` | 用药行全量聚合（按日/账号） |
| `skip_stats_out.txt` | 跳过统计（按日/账号/首末日期） |
| `table.md` | §4 逐账号表原始输出 |
| `dl/history.tar`（246,804,480B）/ `dl/*.log` | 宿主 history 全量 + MAA_6 调试日志 |
| `host_AutoProxy_lf.py` / `host_constants.py` / `host_config.py` | 宿主代码镜像（行号口径） |

（脚本：`fetch_users.py`、`med_full.py`、`usage2.py`、`skip_stats.py`、`gen_table.py` 等；全部只读抓取。）
