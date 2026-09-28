# 库存保持（仓库保有 / DepotMaintain）：逻辑、注入与用法

> 调研 2026-09-28（全程只读）。对象：机队 MAA v6.18.0（`E:\MAA-Multi\MAA_1..12`）+ AUTO-MAS `release/v5.4.0`@`491c40a`（`E:\AUTO-MAS`）。
> 一句话结论：**库存保持 = 把指定材料刷到目标库存的刷图任务；它排在理智作战之前、会先吃掉当天理智；本队 60/60 账号当前全部未启用。**

## 1. 它是什么

- MAA 侧 UI 层任务「库存保持」：按缓存的仓库数据算缺口，把指定材料刷到目标数量后自动停。官方文档：`docs.maa.plus/zh-cn/manual/introduction/depot-maintain.html`。
- 版本线：v6.15.0（2026-07-29，PR #17042）引入；6.15.0-beta.3 加 AUTO 倍率；6.16.0 先判库存再判关卡开放；6.16.2 UI 重构（药/石全局开关、临期药、预设）；**6.17.0（#17741）加「理智不足跳过后续计划」**。
- 非破坏性：不合成、不拆解、不消耗材料；副作用只有 **消耗理智** 并产出掉落（掉落进仓，不可撤回的是理智）。

## 2. 判定逻辑链（MAA v6.18.0 源码）

1. `UpdateDepot=true` → 先追加 core `Depot` 仓库识别任务（`DepotMaintainTaskUserControlModel.cs:561-571`）；识别回调 → `ToolboxViewModel.DepotParse`（`AsstProxy.cs:2210-2212`）。
2. 识别结果缓存到 `<MAA目录>\data\DepotData.json`（实测 MAA_6：`{"done":true,"data":{"<itemId>":<count>,…}}`；由 `JsonDataHelper` 写 `data\{key}.json`）。
3. 逐条计划算缺口：`need = DropCount − 当前库存`（无该材料记 0），序列化时先算一遍（`DepotMaintainVM.cs:600-608`）。
4. `need<=0` → 记「已充足」跳过；否则下发 core `Fight`：`Drops={DropId: need}`、`times=int.MaxValue`、`series=UseAutoSeries?0:1`；药/石= `task.UseMedicine&&plan.UseMedicine`（`DepotMaintainVM.cs:626-640`）。
5. 每次 StageDrops 实时回写缓存（`AsstProxy.cs:2281-2282`）⇒ **前序计划刷出的材料会扣减后续计划的缺口**。
6. 每个计划开打前重算：任务转 InProgress → `RefreshFightTaskDrops`（`DepotMaintainVM.cs:120-145`）；达标、或「无药石预算且理智不足（估计=最近进图上报值+6 分钟 1 点，封顶）」→ `times=0` 整任务跳过，不导航进图（`FightSettingsUserControlModel.cs:1242-1302`）。
7. `SkipDuringActivity` / `SkipDuringResourceCollection` 命中 → 整任务不进 core（`DepotMaintainVM.cs:543-555`）。

### DepotMaintainTask 字段（v6.18.0 `Configuration\Single\MaaTask\DepotMaintainTask.cs`）

| 字段 | 默认 | 语义 |
|---|---|---|
| `UpdateDepot` | true | 任务开始前更新库存数据（追加一次仓库识别） |
| `IsStageManually` | false | 手输关卡名；「当前/上次」不支持 |
| `SkipDuringActivity` | false | 有 SideStory 活动进行中 → 整任务跳过 |
| `SkipDuringResourceCollection` | false | 资源收集限时全天开放 → 整任务跳过 |
| `OnlyFirstInsufficientPlan` | false | 只下发第一个不足计划，其余计划下轮继续 |
| `UseAutoSeries` | false | AUTO 代理倍率（Series=0；单次进图可能刷超目标） |
| `UseMedicine` / `UseStone` | true | 计划级「药剂/源石」行的总开关 |
| `UseExpiringMedicine` | false | 只用 48h 内过期的理智药（阈值固定 2 天） |
| `PlanList[]` | [] | `{Stage, DropId, DropCount, UseMedicine, MedicineCount, UseStone, StoneCount}`，按序执行 |

## 3. AUTO-MAS 侧：schema 与注入

- 用户配置：`Task.IfDepotMaintain`(bool，默认 False，`app/models/config.py:580`)、`Task.DepotMaintainPlans`(JSON 字符串，默认 `[]`，`:601`)；API `app/models/schema.py:534/544`。
- 闸门：仅 Routine 模式注入；`Info.StageMode != "Fixed"`（计划表模式）强制关（生产 `AutoProxy.py:475-476`）。
- 注入器 `_build_depot_maintain_task(plans_json, source_task=…)`（生产 `AutoProxy.py:175-241`）：
  - 校验项：`Stage` 非空 str、`DropId` 非空 str、`DropCount` int>0（bool 拒收）；**非法项静默丢弃**；
  - 每项只保留 `Stage/DropId/DropCount`，`UseMedicine/MedicineCount/UseStone/StoneCount` 强制 `0/False` ⇒ **经 AUTO-MAS 不花药剂/源石**；
  - 任务级 `$type/UpdateDepot/IsStageManually/SkipDuringActivity/SkipDuringResourceCollection/UseAutoSeries` 从 **用户模板里的同型任务条目**继承，无则默认 `…/True/False/False/False/**True**`（`UseAutoSeries` 默认 **True**）。
- 队列位置：`MAA_TASKS` = `[StartUp, DepotMaintain, Fight, Infrast, Recruit, Mall, Award, Roguelike]`（`app/utils/constants.py:57-66`），按序重建 TaskQueue ⇒ 实际顺序：
  `StartUp →（活动关优先）→ **库存保持** → 理智作战 →（剩余理智）→ 基建 → 公招 → 信用 → 奖励 → 肉鸽`。
- 最小可用样例（写入 `Task.DepotMaintainPlans` 的字符串）：
  `[{"Stage":"CE-6","DropId":"4001","DropCount":500000}]`
  （`Stage` 须当日开放且为 MAA 认可关卡；`DropId` 用物品 ID：4001 龙门币 / 3303 技巧概要·卷3 / 3261 医疗芯片 / 3262 医疗芯片组 / 4006 采购凭证——`item_index.json` 实测。）

## 4. UI 入口

- MAA 侧：任务队列「库存保持」。内置预设（`DepotMaintainVM.cs:258-284`）：芯片 PR-A/B/C/D-1 各 20；芯片组 PR-*-2 各 20；CE-6 龙门币 200 万；AP-5 采购凭证 5000；CA-5 技巧概要·卷3 200。
- AUTO-MAS 侧：编辑用户 →「库存保持」区（`frontend/src/views/EditView/User/MAAUserEdit.vue:68-75` + `MAAUserEdit/DepotMaintainConfigSection.vue`）：启用开关、计划表（关卡/物品/目标库存）、添加/删除/预设（5 组，数值同 MAA：`depotMaintainPresets.ts`）。
- 物品下拉 = `POST /maa/depot/items` → 读 `<MAA>/resource/item_index.json`，排除 `MAA_DEPOT_EXCLUDED_ITEM_IDS`（`app/api/scripts.py:493-508`、`app/core/config.py:1575-1603`、`app/utils/constants.py:81-111`）。
- 部署一致性（MD5 实测）：`E:\AUTO-MAS\repo\frontend\...` 与本地 `D:\MAS\frontend\...` 三个 UI 文件逐字节相同（MAAUserEdit.vue `a5a3b1dc…`、DepotMaintainConfigSection.vue `db9a7505…`、depotMaintainPresets.ts `edc29ea1…`）；**后端 `AutoProxy.py`/`constants.py` 生产更新**（含 source_task 继承）。

## 5. 运行位置与判据（怎么认）

- `debug\gui.log`（AUTO-MAS 也读它，生产 `AutoProxy.py:349`）：
  - `开始任务: 库存保持 (仓库识别)` / `开始任务: 库存保持 #1`（多链后缀规则 `AsstProxy.cs:1679-1698`）；
  - `Start Task Chain: Depot, Task ID: n`（识别链）、`Start Task Chain: Fight, Task ID: n`（各计划）；
  - 计划行：`#1：龙门币 不足 1,234/500,000，需刷取 498,766` / `#1：… 已充足 …` / `#1：理智不足（当前 X，低于关卡消耗 Y），已跳过`；
  - 重算行：`FightTask <id> (#1) re-calculated: <item> need N (current C / target T)`；
  - `完成任务: 库存保持 #1`。
- `debug\asst.log`：core 侧只见 `Depot`/`Fight` 链；对账数据看 `data\DepotData.json`。
- AUTO-MAS：`AutoProxy.py:78-84` 把 `完成任务: 库存保持` 列为「体力任务完成」marker（:1068 起用于「中止时仍推统计通知」）。
- 失败/跳过文案：`#N：关卡 ｢…｣ 未开放`、`未配置任何计划`（仅 UpdateDepot 关时）、`更新库存数据失败。`（`zh-cn.xaml:1065-1086`）。

## 6. 全量现状（2026-09-28 实测）

- `ScriptConfig.json` 60 用户：`IfDepotMaintain` 全 `False`、`DepotMaintainPlans` 全 `[]` ⇒ **0/60 启用**。
- 12 个在用 MAA 目录 `config\gui.new.json`：无一含 DepotMaintain；`E:\AUTO-MAS\data` 全树 549 个 gui.new.json（含备份）0 命中（对照 `FightTask` 命中 121 个）。
- 版本：12/12 实例最后启动 `v6.18.0`（gui.log Bootstrapper），历史 v6.16.8→v6.17.5→v6.18.0，均 ≥ v6.15.0（支持线）。
- 两个非 Fixed 用户（日-4「河清灯_别寄圆通」、官-2-4-47「音乐厅生动的深海鱼」）即使开启也会被 AUTO-MAS 强制关。

## 7. 用法 / 风险 / 回滚

启用（受控写，需用户批准）：
1. 确认目标号 MAA ≥ v6.15.0（本队满足）。
2. AUTO-MAS 界面：编辑用户 → 库存保持 → 启用 → 加计划/预设 → 保存；或写 `Task.DepotMaintainPlans`（最小样例）+ `Task.IfDepotMaintain=true`。
3. 验证：下一班次 gui.log 出现 `(仓库识别)`→`#N` 链；`DepotData.json` 数量变化与「不足→需刷取」数字自洽。

回滚：`IfDepotMaintain=false` → 下一班次不再注入；无残留需清理（`DepotData.json` 只是缓存）。已消耗理智/已得掉落不可逆。

红线：
- **先于理智作战执行**：会先吃当天理智（活动关优先后、Fight 前）——不是「该号想要的用途」就别开；
- AUTO-MAS 注入 `UseAutoSeries` 默认 **True**（AUTO 倍率）→ 单次进图可刷超目标数量；
- 结果依赖库存缓存（`UpdateDepot=True` 已在开头刷新，缓解但非消除）；
- plans 非法项静默丢；`Stage` 写「当前/上次」= 无效；非 Fixed 模式强制关。

## 8. 待验证

- 60 号从未启用 ⇒ 无真实运行样本；`完成任务: 库存保持 (仓库识别)` 是否各版本都写 gui.log 未实测。
- AUTO-MAS mainline v5.5.0+ 的 UI 增强（「关卡下拉只列真正掉落关卡并显示库存」等）未落入本部署分支（本部署前端=v5.4.0 源，无此过滤）。
- 模板继承：若用户模板 gui.new.json 含同型 DepotMaintainTask 条目，上述任务级字段取值随模板（当前 0 个模板含）。
