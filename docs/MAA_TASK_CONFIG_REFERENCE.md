# MAA 任务配置全字段手册（v6.18.0 · gui.new.json · AUTO-MAS 注入视角）

> 生成：2026-09-27（kanban `t_f270d627`，只读调研，未改动任何远端配置）
> 基线：MAA **v6.18.0**（远端 `E:\MAA-Multi\resource\version.json`；GitHub tag `v6.18.0`，tree `23feeddc538de4e10646b692b0bb065efe77ccb3`）
> 样本：远端 16 个实例 `E:\MAA-Multi\MAA_N\config\gui.new.json`（271KB 全量抓取）+ AUTO-MAS 默认模板 `E:\AUTO-MAS\data\a3e72775-33ac-47ca-a901-6f3ca33eb9dc\Default\ConfigFile\gui.new.json`
> 注入侧源码：`E:\AUTO-MAS\app\task\MAA\{AutoProxy.py, constants.py, manager.py, tools/bilibili.py}`（远程部署版，sha256 与本地 `D:\MAS` 克隆不同，以远程为准）
> 互补文档：`docs/maa-config-mapping.md`（MAAOrch 侧旧映射）、`docs/MAA_ROGUELIKE_PLAYBOOK.md`、`docs/MAA_EVENT_FARMING_PLAYBOOK.md`、`docs/MAA_TASK_MECHANICS.md`
> 证据与提取脚本：任务工作区 `evidence/`（详见附录 A）

---

## 0. 速查：要改什么 → 看哪里

| 目标 | 看章节 |
|---|---|
| 弄清 gui.json 和 gui.new.json 谁说了算 | §1 |
| 每个任务条目有哪些字段、什么取值 | §3 |
| 字段 ↔ GUI 文案对照 | §3 各表「GUI 文案」列 |
| AUTO-MAS 发车前注入了什么、怎么拼队列 | §5 |
| 「关掉公招」「只刷 1-7」这类口语需求对应哪个字段 | §7 |
| 还有哪些没核实 | §8 |

三条铁律：

1. **改 `gui.new.json`，不要改 `gui.json`**。6.x 的真配置是 `gui.new.json`；`gui.json`（旧壳）在 6.18 仍被读（`ConfigurationHelper`），但任务队列只存在于 `gui.new.json` 的 `Configurations.<name>.TaskQueue`。
2. **`TaskQueue` 是数组，每个元素的 `$type` 决定字段集**。字段名与 GUI 上看到的名称经常不一致（见各表的「GUI 文案」列与 VM 漂移备注）。
3. **AUTO-MAS 每次发车前会用注入器重写两壳文件**（`AutoProxy.py` L592–936）：你在 GUI 里手改的内容，会被下一次发车的 `task_set` 合并逻辑覆盖或保留，取决于该字段是否在注入范围内。

---

## 1. 配置载体与读写链路

### 1.1 文件布局（实例目录 `E:\MAA-Multi\MAA_N\config\`）

| 文件 | 角色 |
|---|---|
| `gui.new.json` | **6.x 主配置**。`Root` 结构：`Configurations` / `Current` / `ConfigVersion` / `Gui` / `Timers` / `Update` / `AnnouncementInfo`（Root.cs L38–56） |
| `gui.json` | 旧壳配置。扁平点号键（`Configurations.Default.Start.ClientType`、`Global.GUI.Localization` …）；6.18 仍通过 `ConfigurationHelper` 读取部分键（ConfigurationHelper.cs L28–30；L101–123 含从 Current 配置迁移全局键的逻辑） |
| `gui.new.json.bak` / `gui.json.bak` | 保存时的滚动备份（ConfigFactory.cs L46、L162–169） |
| `gui.error.json` | 反序列化失败时对损坏文件的转存（ConfigFactory.cs L172–176） |

### 1.2 gui.new.json 顶层结构

```
{
  "Configurations": { "<配置名>": SpecificConfig },   // 可多套；实例里均为 "Default"
  "Current": "<配置名>",                              // 当前激活配置
  "ConfigVersion": 1,
  "Gui": {...},          // 全局 UI 设置（Global/Gui.cs，见 §4.5）
  "Timers": {"List": [{"IsEnabled": false, ...} × N]},
  "Update": {"CheckOnSchedule": false, "AutoDownload": true, "AutoInstall": false, ...},
  "AnnouncementInfo": {...}
}
```

`SpecificConfig` = `{ TaskQueue, Gui, Toolbox, Copilot, TaskSelectedIndex }`（SpecificConfig.cs L33–46）：

| 键 | 内容 | 章节 |
|---|---|---|
| `TaskQueue` | **任务队列数组**（每个元素一个任务，见 §3） | §3 |
| `Gui` | 单配置 GUI 设置：ConnectSettings / StartUpSettings / RuntimeSettings / Performance / ExternalNotification / RemoteControl / ThirdParty / PostActions（Settings/Gui.cs L40–67） | §4.1 |
| `Toolbox` | 小工具设置（Toolbox.cs L31–51） | §4.2 |
| `Copilot` | 自动战斗设置（Copilot.cs L34–44） | §4.3 |
| `TaskSelectedIndex` | UI 选中行索引（默认 -1；16 实例观测均为 6） | §4.4 |

### 1.3 序列化规则（影响手改）

- `System.Text.Json`，缩进输出；`DefaultIgnoreCondition = WhenWritingNull`（ConfigFactory.cs L65）→ **null 字段不落盘**。
- 容错枚举转换器 + `TolerantEnumConverter`：枚举可写字符串或数值（如 `PostActions: "None"` 与 `PostActions: 8` 均可读）。
- 自定义转换器包括 `RoguelikeStartingOpersConverter`、`TaskQueueConverter` 等（ConfigFactory.cs L65 附近）。
- **只读计算属性也会被写出**：如 MallTask 的 `IsCreditFightAvailable`/`IsVisitFriendsAvailable`、`SquadIsFoldartal`。它们由 MAA 写出供 UI 使用，手改无意义（反序列化时忽略）。
- MAA 每次运行结束会整体重写 `gui.new.json`：**手写的未知字段会丢**；改配置应在 MAA 关闭时进行。

---

## 2. TaskQueue 通用字段（所有任务条目共有）

| 字段 | 类型/默认 | 取值域 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|---|
| `$type` | string | 见下方 20 个允许值 | 多态判别符；决定该条目的字段集 | —（JSON 内部） | BaseTask.cs L21–40 |
| `Name` | string = "" | 任意 | 显示名；AUTO-MAS 写入中文名 | 列表标题 | BaseTask.cs L43 |
| `IsEnable` | bool? = true | true/false/null | 是否启用该任务 | 左侧勾选框（右键可置 null：「该选项仅生效一次」） | BaseTask.cs L48；视图 tooltip CheckBoxesNotSavedAsNull |
| `TaskType` | enum | 同 `$type` 前缀 | 任务类型枚举名 | — | BaseTask.cs L53 |

允许的 `$type`（BaseTask.cs L21–40 的 `JsonDerivedType` 全列表）：
`StartUpTask`, `CloseDownTask`, `FightTask`, `RecruitTask`, `InfrastTask`, `MallTask`, `AwardTask`, `RoguelikeTask`, `CopilotTask`, `SSSCopilotTask`, `SingleStepTask`, `VideoRecognitionTask`, `DepotTask`, `OperBoxTask`, `ReclamationTask`, `CustomTask`, `UserDataUpdateTask`, `OperProgressTask`, `SwitchThemeTask`, `DepotMaintainTask`。

> 注意：`CloseDownTask` / `CopilotTask` / `SSSCopilotTask` / `SingleStepTask` / `DepotTask` / `OperBoxTask` 在配置模型里是**空子类**（无额外字段，BaseTask.cs L57–79）。

---

## 3. 逐任务类型字段表

标注约定：「GUI 文案」取自 MAA zh-cn 本地化或视图硬编码（`evidence/zh_cn_keys.json`、`ui_map2.txt`）；带 * 的文案在特定开关下才会显示星号提示。

### 3.1 StartUpTask（开始唤醒）

| 字段 | 类型/默认 | 取值域 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|---|
| `AccountName` | string = "" | 掩码手机号（官服）或空 | 账号切换目标（仅官服/B服/台服/韩服支持） | 「账号切换」输入框 | StartUpTask.cs L23；视图 L39–44 |
| `AccountSwitchEnabled` | bool? = null | true/false | 是否启用账号切换（旧配置无此字段 → null） | 「账号切换」勾选框 | StartUpTask.cs L28；视图 L31 |

AUTO-MAS 注入：官服 = 掩码 + `true`；其余 = `""` + `false`（AutoProxy L766–779）。

### 3.2 FightTask（理智作战）

> ⚠️ VM 属性名与字段名**漂移**（视图/VM 代码里叫另一套名字）：`MedicineNumber↔MedicineCount`、`HasTimesLimited↔EnableTimesLimit`、`IsSpecifiedDrops↔EnableTargetDrop`、`UseAlternateStage↔UseOptionalStage`、`AllowUseStoneSave↔UseStoneAllowSave`、`DropsItemId↔DropId`、`DropsQuantity↔DropCount`、`UseInventoryTarget↔IsInventoryTarget`（vm_map.txt 自动提取）。改 JSON 认**字段名**（左列）。

| 字段 | 类型/默认 | 取值域 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|---|
| `UseMedicine` | bool? = false | | 是否吃理智药 | 「使用药剂」 | FightTask.cs L35；视图 L35–44 |
| `MedicineCount` | int = 0 | 0–999 | 吃理智药数量 | （药剂数字框） | L40 |
| `UseStone` | bool? = false | | 是否碎石 | 「使用源石」 | L45；视图 L45–56 |
| `StoneCount` | int = 0 | 0–999 | 碎石数量 | （数字框） | L50 |
| `EnableTargetDrop` | bool? = false | | 是否启用指定材料 | 「指定材料」 | L55；视图 L111–119 |
| `DropId` | string = "" | 材料 ID 字符串 | 指定掉落物 ID | （材料下拉框） | L60；视图 L146–161 |
| `DropCount` | int = 0 | ≥1 当启用 | 刷取数量 | （数量数字框） | L65 |
| `IsInventoryTarget` | bool = false | | 按「目标库存」计算材料缺口 | 「目标库存」 | L70；视图 L135–144 |
| `EnableTimesLimit` | bool? = false | | 是否限制战斗次数 | 「指定次数」 | L75；视图 L57–65 |
| `TimesLimit` | int = int.MaxValue | ≥1 | 最大战斗次数 | （数字框） | L80 |
| `Series` | int = 0 | 0=AUTO；1–10 | 代理倍率 | 「代理倍率」下拉 | L85；视图 L218–228 |
| `StagePlan` | List\<string\> = [""] | 关卡码列表；`""`=当前/上次 | 候选关卡（从上往下取第一个可用） | 「关卡指定」/「候选关卡」 | L90；视图 L241–293、L349–358 |
| `IsDrGrandet` | bool = false | | 博朗台模式（等理智回满再打） | 「博朗台模式」 | L95；视图 L439–440 |
| `UseExpiringMedicine` | bool = false | | 使用临期理智药 | 「无限吃 N 天内过期的理智药」 | L100；视图 L442–447 |
| `MedicineExpireDays` | int = 2 | 1–30 | 临期药天数阈值 | （数字框） | L102；视图 L453–461 |
| `UseExpireMedicineForActivity` | bool = false | | 活动结束前 48H 吃当周过期理智药 | 同名文案 | L104；视图 L464–469 |
| `UseCustomAnnihilation` | bool = false | | 自定义剿灭关卡 | 「自定义剿灭关卡」 | L109；视图 L423–426 |
| `AnnihilationStage` | string = "Annihilation" | 剿灭关卡码 | 剿灭关卡 | （下拉） | L114；视图 L427–436 |
| `HideUnavailableStage` | bool = false | | 下拉框隐藏当日不开放关卡 | 「下拉框中隐藏当日不开放关卡」 | L119；视图 L502–506 |
| `IsStageManually` | bool = false | | 手动输入关卡 | 「手动输入关卡名」 | L124；视图 L489–490 |
| `UseOptionalStage` | bool = false | | 使用备选关卡 | 「使用备选关卡」 | L129；视图 L498–501 |
| `UseStoneAllowSave` | bool = false | | 允许使用源石保存状态（false 时强制 `UseStone=false`，OnDeserialized L154–159） | 「允许使用源石保存状态」 | L134；视图 L485–486 |
| `HideSeries` | bool = false | | 隐藏代理倍率 | 「隐藏代理倍率」 | L139；视图 L480–483 |
| `StageResetMode` | enum = Current | Current/Ignore | 过期活动关卡重置为 | 「过期活动关卡重置为」 | L141；FightStageResetMode.cs L16–27 |
| `UseWeeklySchedule` | bool = false | | 启用周计划 | 「启用周计划」 | L146；视图 L508–510 |
| `WeeklySchedule` | Dictionary\<DayOfWeek,bool\> = 全 true | 7 键 | 周计划；`UseWeeklySchedule=false` 时不序列化（`JsonPredict`） | （周列表） | L152 |

实例观测：`StagePlan=["SR-7"]`（多数）、`["SR-5"]`（MAA_3）、`["1-7"]`（肉鸽机/停用机）；`Series=0`；`TimesLimit=2147483647` 或 `999`。

**AUTO-MAS 注入**（L796–837）：主作战关写 `StagePlan=[Stage]`、`Series`、`UseMedicine/MedicineCount`、`UseStone/StoneCount`、`IsStageManually=True`、`UseOptionalStage`；简洁模式下强制 `EnableTimesLimit=false`、`EnableTargetDrop=false`。

#### 3.2.1 「剩余理智」（队列内第二个 FightTask 条目）

| 字段 | 值 | 证据 |
|---|---|---|
| `Name` | `剩余理智` | constants L191–228（MAA_REMAIN_FIGHT_BASE） |
| `StagePlan` | `[Stage_Remain]`（用户配置；为空则 `""`） | AutoProxy L910–928 |
| `Series` | 用户 `SeriesNumb` | 同上 |
| `UseMedicine` 等 | false/0 | 模板 |

#### 3.2.2 「剿灭」（模式=Annihilation 时替换主作战）

| 字段 | 值 | 证据 |
|---|---|---|
| `Name` | `剿灭` | constants L150–187（MAA_ANNIHILATION_FIGHT_BASE） |
| `UseCustomAnnihilation` | true | AutoProxy L799–810 |
| `AnnihilationStage` | 用户配置的剿灭关卡 | 同上 |
| `MedicineCount` | 用户 `MedicineNumb` | 同上 |

#### 3.2.3 「活动关优先」（自动插入 StartUp 之后）

| 字段 | 值 | 证据 |
|---|---|---|
| `Name` | `活动关优先` | AutoProxy L253–282 |
| `StagePlan` | `[活动关卡码]`（由 `ActivityStageIndex` + 阶段表解析） | L236–250、L646–659 |
| `IsStageManually` | true；`UseOptionalStage=false` | 同上 |
| `EnableTargetDrop`/`EnableTimesLimit` | false | 同上 |
| `UseMedicine`/`MedicineCount` | `ActivityMedicineNumb>0` 时 true/数量 | 同上 |
| `UseExpiringMedicine`/`UseExpireMedicineForActivity` | false | 同上 |

### 3.3 InfrastTask（基建换班）

| 字段 | 类型/默认 | 取值域 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|---|
| `Mode` | enum = Normal | Normal / Custom(10000) / Rotation(20000) | 基建切换模式 | 「基建模式」（常规模式/自定义基建配置/队列轮换） | InfrastTask.cs L39；枚举 InfrastSettingsUserControlModel.cs L730–746 |
| `UsesOfDrones` | string = "Money" | Money / Synthesize / Trade / Dorm / DronesNotUse | 无人机用途 | 「无人机用途」 | L44 |
| `DormThreshold` | int = 30 | 0–100 | 工作心情阈值 | 「基建工作心情阈值」 | L49 |
| `DormTrustEnabled` | bool = true | | 宿舍空位蹭信赖 | 「宿舍空余位置蹭信赖」 | L54 |
| `OriginiumShardAutoReplenishment` | bool = true | | 源石碎片自动补货 | 「源石碎片自动补货」 | L59 |
| `DormFilterNotStationed` | bool = true | | 不将已进驻干员放入宿舍 | 「不将已进驻的干员放入宿舍」 | L64 |
| `ReceptionMessageBoard` | bool = true | | 会客室信息板收取信用 | 「会客室信息板收取信用」 | L69 |
| `ReceptionClueExchange` | bool = true | | 进行线索交流 | 「进行线索交流」 | L74 |
| `SendClue` | bool = true | | 赠送线索 | 「赠送线索」 | L79 |
| `ContinueTraining` | bool = false | | 训练完成后继续专精 | 「训练完成后继续尝试专精当前技能」 | L84 |
| `FiammettaTarget1` | string = "清流" | 无/清流/可露希尔/但书/巫恋/龙舌兰/歌蕾蒂娅 | 菲亚梅塔恢复目标 | 「菲亚梅塔恢复目标」 | L86；keys `InfrastFiammettaTarget*` |
| `FiammettaTarget2` | string = "可露希尔" | 同上 | 同上 | 同上 | L88 |
| `FiammettaTarget3` | string = "但书" | 同上 | 同上 | 同上 | L90 |
| `FiammettaRecoveryEnabled` | bool = false | | 启用菲亚梅塔心情恢复 | 「启用菲亚梅塔心情恢复」 | L95 |
| `UsePinusSylvestris` | bool = false | | 红松骑士团跨设施组合 | 「使用红松骑士团跨设施组合」 | L100 |
| `UsePerceptionInformation` | bool = false | | 感知信息跨设施组合 | 「使用感知信息跨设施组合」 | L105 |
| `UseWorldlyPlight` | bool = false | | 人间烟火跨设施组合 | 「使用人间烟火跨设施组合」 | L110 |
| `UseAbyssalHunter` | bool = false | | 深海猎人跨设施组合 | 「使用深海猎人跨设施组合」 | L115 |
| `CustomFileType` | string = "user_defined" | 内置配置 / user_defined | 自定义配置来源 | 「内置配置」/「自定义」 | L117 |
| `Filename` | string = "" | 文件路径 | 自定义排班文件路径 | 「自定义基建排班制作器」选择框 | L122 |
| `InfrastPlan` | List\<Plan\>，`[JsonIgnore]` | — | 解析缓存（不落盘；由 `Filename` 指向的 infrastructure.json 决定） | — | L125 |
| `PlanSelect` | int = -1 | -1=未选 | 自定义配置计划编号 | 「基建计划」下拉 | L130 |
| `RoomList` | List\<RoomInfo\> = 10 房型全开 | Mfg/Trade/Control/Power/Reception/Office/Dorm/Processing/Training/AssistantChange | 换班设施开关 | 「换班设施」 | L132；InfrastRoomType.cs L21–66 |

AUTO-MAS 注入（L839–885）：自定义基建时写 `infrastructure.json` → `Filename`/`InfrastPlan`/`PlanSelect`/`Mode=Custom`；失败回退 `Mode=Normal`。

### 3.4 RecruitTask（自动公招）

| 字段 | 类型/默认 | 取值域 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|---|
| `UseExpedited` | bool? = false，`[JsonIgnore]` | | 自动使用加急许可（**不落盘**） | 「自动使用加急许可*」 | RecruitTask.cs L36 |
| `MaxTimes` | int = 4 | ≥0 | 每次执行最大招募次数 | 「每次执行时最大招募次数」 | L41 |
| `ExtraTagMode` | int = 0 | 0/1/2 | 公招多选 Tag 策略 | 「公招多选 Tag 的策略」 | L47 |
| `Level3PreferTags` | List\<string\> = [] | 词条名 | 3 星 Tag 倾向 | 「3 星 Tag 时的 Tag 倾向」 | L52 |
| `PreferTagEnabled` | bool = true | | 启用 3 星 Tag 倾向 | （开关） | L57 |
| `PreserveTagList` | List\<string\> = [支援机械] | 词条名 | 保留并跳过词条 | 「保留指定词条」 | L62 |
| `PreserveTagEnabled` | bool = false | | 启用保留 | （开关） | L67 |
| `RefreshLevel3` | bool = true | | 无倾向 Tag 时刷新 3 星 | 「自动刷新 3 星 Tags」 | L73 |
| `ForceRefresh` | bool = true | | 无招聘许可仍刷新 | 「无招聘许可时继续尝试刷新 Tags」 | L78 |
| `Level3Choose` | bool = true | | 自动确认 3 星 | 「自动确认 3 星」 | L83 |
| `Level3RecruitmentPermitReserveEnabled` | bool = false | | 3 星保留招聘许可 | 「3 星保留招聘许可」 | L88 |
| `Level3RecruitmentPermitReserve` | int = 8 | ≥1 | 保留数量 | （数字框） | L93 |
| `Level4Choose` | bool = true | | 自动确认 4 星 | 「自动确认 4 星」 | L98 |
| `Level5Choose` | bool = false | | 自动确认 5 星 | 「自动确认 5 星」 | L104 |
| `Level6Choose` | bool = false | | 自动确认 6 星（仅配置文件可改） | 「自动确认 6 星」 | L110 |
| `Level3Time` | int = 540 | 分钟 | 3 星时间 | 「时:分」 | L115 |
| `Level4Time` | int = 540 | 分钟 | 4 星时间 | 「时:分」 | L120 |

### 3.5 MallTask（信用收支）

| 字段 | 类型/默认 | 取值域 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|---|
| `Shopping` | bool = true | | 信用交易所自动购物 | 「信用交易所自动购物」 | MallTask.cs L33 |
| `CreditFight` | bool = false | | 借助战打 OF-1 赚信用 | 「借助战打 OF-1 赚信用」 | L38 |
| `CreditFightFormation` | int = 0 | 编队序号 | 信用战斗编队 | 「使用编队」 | L43 |
| `CreditFightLastTime` | string = 0001/01/01 00:00:00 | 时间戳 | 上次信用战斗时间（内部） | — | L48 |
| `CreditFightOnceADay` | bool = true | | 一日只执行一次 | 「一日只执行一次」 | L53 |
| `VisitFriends` | bool = true | | 访问好友 | 「访问好友」 | L58 |
| `VisitFriendsOnceADay` | bool = false | | 一日只执行一次 | 「一日只执行一次」 | L63 |
| `VisitFriendsLastTime` | string | 时间戳 | 上次访问时间（内部） | — | L68 |
| `FirstList` | string = 招聘许可 | 子串，分号分隔 | 优先购买 | 「优先购买 子串即可 分号分隔」 | L73 |
| `BlackList` | string = 碳;家具;加急许可 | 同上 | 黑名单 | 「黑名单 子串即可 分号分隔」 | L78 |
| `ShoppingIgnoreBlackListWhenFull` | bool = false | | 信用溢出时无视黑名单 | 「信用溢出时无视黑名单」 | L83 |
| `OnlyBuyDiscount` | bool = false | | 只购买打折商品 | 「只购买打折的信用商品」 | L88 |
| `ReserveMaxCredit` | bool = false | | 信用低于 300 停止购买 | 「信用点低于 300 时停止购买商品」 | L93 |
| `IsCreditFightAvailable` / `IsVisitFriendsAvailable` | bool，只读计算 | — | MAA 写出供 UI 用；手改无效 | — | L95–131 |

### 3.6 AwardTask（领取奖励）

| 字段 | 类型/默认 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|
| `Award` | bool = true | 领取每日/每周任务奖励 | 同名 | AwardTask.cs L26 |
| `Mail` | bool = true | 领取所有邮件奖励 | 同名 | L31 |
| `FreeGacha` | bool = true | 限定池每日免费单抽 | 同名 | L36 |
| `Orundum` | bool = true | 幸运墙每日合成玉 | 同名 | L41 |
| `Mining` | bool = true | 限时开采许可每日合成玉 | 同名 | L46 |
| `SpecialAccess` | bool = true | 周年赠送月卡奖励 | 同名 | L51 |
| `SignInEvent` | bool = true | 限时签到活动奖励 | 同名 | L56 |

### 3.7 RoguelikeTask（自动肉鸽）

| 字段 | 类型/默认 | 取值域 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|---|
| `Theme` | enum = Phantom | Phantom/Mizuki/Sami/Sarkaz/JieGarden/BlackFlow | 主题 | 「肉鸽主题」 | RoguelikeTask.cs L31 |
| `Difficulty` | int = int.MaxValue | -1=不切换；MAX=最高；0..主题上限 | 难度 | 「难度」 | L33；VM L71–96 |
| `Mode` | enum = Exp | Exp(0)/Investment(1)/Collectible(4)/CLP_PDS(5)/Squad(6)/Exploration(7)/FindPlaytime(20001)/BlackFlowBabyAnimal(30001) | 策略 | 「策略」 | L35；枚举 L229–270 |
| `Squad` | string = "" | 分队名 | 开局分队 | 「开局分队」 | L40 |
| `SquadCollectible` | string = "" | 分队名 | 刷开局使用分队 | 「刷开局使用分队」 | L45 |
| `Roles` | string = "" | 职业组名 | 开局职业组 | 「开局职业组」 | L50 |
| `StartCount` | int = 999999 | ≥1 | 探索次数上限 | 「开始探索 N 次后停止任务」 | L55 |
| `StartingOpers` | List\<{Name, UseSupport}\> | 最多 3 位 | 开局干员顺位 | 「开局干员」+ 第 2/3 位 | L60 |
| `UseAdditionalStartingOpers` | bool = false | | 启用第 2、3 位开局干员 | 「启用第 2、3 位开局干员」 | L65 |
| `Investment` | bool = true | | 投资源石锭 | 「投资源石锭」 | L70 |
| `InvestCount` | int = 999 | ≥1 | 投资数量 | 「投资 N 个源石锭后停止任务」 | L75 |
| `InvestWithMoreScore` | bool = false | | 投资时启用招募/购物刷分 | 「投资模式启用购物、招募、进2层」 | L80 |
| `CollectibleStartAwards` | enum(flags) = HotWater\|Hope\|Idea | HotWater/Shield/Ingot/Hope/Random/Key/Dice/Idea/Ticket | 刷开局期望奖励 | 「刷开局期望奖励」 | L85；RoguelikeCollectibleAward.cs L19–65 |
| `CollectibleShopping` | bool = false | | 刷开局模式启用购物 | 同名 | L90 |
| `StartWithEliteTwo` | bool = false | | 凹开局直升精二 | 「凹 ｢开局干员｣ 直升精二」 | L95 |
| `StartWithEliteTwoOnly` | bool = false | | 只凹直升不打关 | 「只凹 … 不进行作战」 | L100 |
| `ExpectedCollapsalParadigms` | string = "" | 范式名 | 期望坍缩范式 | 「坍缩范式列表」 | L105 |
| `MonthlySquadAutoIterate` | bool = true | | 月度小队自动切换 | 同名 | L110 |
| `MonthlySquadCheckComms` | bool = true | | 月度小队刷通讯 | 「月度小队通讯」 | L115 |
| `DeepExplorationAutoIterate` | bool = true | | 深入调查自动切换 | 同名 | L120 |
| `FindPlaytimeTarget` | enum = Ling | Unknown/Ling/Shu/Nian | 目标常乐节点 | 「目标常乐节点」 | L125 |
| `BlackFlowCultivationTarget` | enum = Cat | Cat/FeatheredSerpent/Dog/Cerberus | 目标襁褓动物 | 「目标襁褓动物」 | L130 |
| `UseSupportNonFriend` | bool = false | | 可以使用非好友助战 | 同名 | L135 |
| `RefreshTraderWithDice` | bool = false | | 刷新商店（指路鳞） | 同名 | L140 |
| `SamiFirstFloorFoldartal` | bool = false | | 凹第一层远见密文板 | 「凹第一层远见密文板，不进行作战」 | L149 |
| `SamiFirstFloorFoldartals` | string = "" | 板子名 | 同上内容 | （输入框） | L154 |
| `SamiNewSquad2StartingFoldartal` | bool = false | | 生活队凹开局密文板 | 「生活队凹开局密文板」 | L159 |
| `SamiNewSquad2StartingFoldartals` | string = "" | | 同上内容 | （输入框） | L164 |
| `StopWhenDepositFull` | bool = false | | 储备源石锭上限停止 | 「储备源石锭达到上限时停止」 | L169 |
| `StopAtFinalBoss` | bool = false | | 第五层 BOSS 前暂停 | 「在第五层 BOSS 前暂停」 | L174 |
| `StopWhenLevelMax` | bool = false | | 满级自动停止 | 「满级后自动停止」 | L179 |
| `StartWithSeed` | bool = false | | 启用刷钱种子（美愿） | 同名 | L181 |
| `Seed` | string = "" | 种子串 | 种子 | （输入框） | L183 |

旧字段残留：`CoreChar` / `UseSupport`（旧版单开局干员字段；16 实例中有 4 例残留，由 `RoguelikeStartingOpersConverter` 迁移到 `StartingOpers`）。

### 3.8 DepotMaintainTask（仓库维护 / 刷材料）

| 字段 | 类型/默认 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|
| `UpdateDepot` | bool = true | 任务开始前更新库存数据 | 「任务开始前更新库存数据」 | DepotMaintainTask.cs L30 |
| `IsStageManually` | bool = false | 手动输入关卡名 | 「手动输入关卡名」 | L32 |
| `SkipDuringActivity` | bool = false | 活动期间跳过 | 「活动期间跳过」 | L37 |
| `SkipDuringResourceCollection` | bool = false | 资源收集限时全天开放期间跳过 | 同名 | L42 |
| `OnlyFirstInsufficientPlan` | bool = false | 仅执行第一个库存不足的计划 | 同名 | L47 |
| `UseAutoSeries` | bool = false（**AUTO-MAS 注入时置 True**） | 代理倍率用 AUTO | 「AUTO 代理倍率」 | L53 |
| `UseMedicine` | bool = true | 启用「使用药剂」勾选框 | 「启用「使用药剂」勾选框」 | L59 |
| `UseStone` | bool = true | 启用「使用源石」勾选框 | 「启用「使用源石」勾选框」 | L65 |
| `UseExpiringMedicine` | bool = false（阈值常量 `ExpiringMedicineDays=2`） | 使用 48 小时内过期的理智药 | 同名 | L71、L28 |
| `PlanList` | List\<Plan\> = [] | 计划列表（见下） | （计划卡片列表） | L73 |

`Plan` 条目：`Stage`(string 关卡码)、`DropId`(string 材料ID)、`DropCount`(int 目标数量)、`UseMedicine`(bool)、`MedicineCount`(int)、`UseStone`(bool)、`StoneCount`(int)、`TaskId`(int，`[JsonIgnore]` 内部)。
AUTO-MAS：仅当「仓库维护」任务开启才写入；写入时**强制计划内药剂/源石为 0**、`UseAutoSeries` 默认 True（builder L175–233；写入条件 L664–666）。材料排除清单 `MAA_DEPOT_EXCLUDED_ITEM_IDS`（constants L81–111）。

### 3.9 ReclamationTask（生息演算）

| 字段 | 类型/默认 | 取值域 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|---|
| `Theme` | enum = Tales | Fire(0)/Tales(1)/RelaunchAnchor(2) | 主题 | 「生息演算主题」 | ReclamationTask.cs L27；枚举 L52–68 |
| `Mode` | enum(flags) = ProsperityInSave | **v6.18.0**：ProsperityNoSave(0)/ProsperityInSave(1)/RA1(16)/RA15(32)/RA4(48) | 模式 | 「模式」 | L32；枚举 L70–97 |
| `ToolToCraft` | string = "" | 支援道具名称（如 荧光棒） | 「支援道具名称」 | L37 |
| `IncrementMode` | int = 0 | 0=连点 / 1=长按（键序推断） | 增加方式 | 「增加方式」 | L39 |
| `MaxCraftCountPerRound` | int = 16 | ≥0 | 单次最大组装轮数 | 「单次最大组装轮数」 | L44 |
| `ClearStore` | bool = true | | 任务完成后购买商店 | 「任务完成后购买商店」 | L49 |

> ⚠️ 已知不一致：v6.18.0 视图里两处 CalcBinding 引用 `ReclamationMode.Archive/NoArchive`，而 v6.18.0 枚举只有 `ProsperityNoSave/ProsperityInSave`（master 已重命名为 `NoArchive/Archive`，见 §8-3）。以安装版枚举为准。

### 3.10 其余类型（简表）

| 类型 | 字段 | 语义 | GUI 文案 | 证据 |
|---|---|---|---|---|
| `UserDataUpdateTask` | `UpdateOperBox` (bool=true)、`UpdateDepot` (bool=true)、`TriggerInterval` (enum=EveryTime) | 干员识别 / 仓库识别 / 触发间隔 | 「干员识别」「仓库识别」「触发间隔」 | UserDataUpdateTask.cs L28–32 |
| `SwitchThemeTask` | `Themes` (List\<string\> = []) | 候选主题名（随机切换） | 「主题名称」+ 添加 | SwitchThemeTask.cs L27 |
| `CustomTask` | `CustomTaskName` (string="") | 任务名（自定义入口） | 「任务名」 | CustomTask.cs L25 |
| `OperProgressTask` | `Plans` (List)、`DeleteOnCompleted` (bool=false) | 培养计划 / 完成后删除条目 | 「任务链结束后删除已完成条目」 | OperProgressTask.cs L29、L35 |
| `CloseDownTask` / `CopilotTask` / `SSSCopilotTask` / `SingleStepTask` / `DepotTask` / `OperBoxTask` | 无额外字段 | 空子类 | — | BaseTask.cs L57–79 |

---

## 4. 非任务配置速查

### 4.1 `SpecificConfig.Gui`（单配置 GUI 设置，Settings/Gui.cs L40–67）

| 子块 | 内容 |
|---|---|
| `ConnectSettings` | AutoDetect/AlwaysAutoDetect/Config/AdbPath/Address/AddressHistory/Extras{LDPlayer, MuMuEmulator12, Win32Extra, BluestacksExtra}/AllowAdbRestart/AllowAdbHardRestart/TouchMode/EnableAdbLite/KillAdbOnExit（ConnectSettings.cs L48–91） |
| `StartUpSettings` | StartEmulator/SkipStartupAutoRunAfterUpdate/EmulatorPath/WaitSeconds（StartUpSettings.cs L24–41） |
| `RuntimeSettings` | ClientType/StartGame/DeployWithPause/AutoRestartOnDrop/RoguelikeDelayAbortUntilCombatComplete/PreRunScript/PostRunScript/ExecuteScriptOnCopilot/ExecuteScriptOnManualStop/BlockSleep/BlockSleepWithScreenOn/ReportToPenguin/PenguinId/ReportToYituliu/EnableStallTimeout/StallTimeoutReminderIntervalMinutes/StallTimeoutMinutes（RuntimeSettings.cs L26–62） |
| `Performance` / `ExternalNotification` / `RemoteControl` / `ThirdParty` | 性能/通知/远程控制/第三方（AUTO-MAS 不注入） |
| `PostActions` | 完成后动作枚举（`None`/`ExitSelf`/`ExitArknights`/`ExitEmulator`…）；**AUTO-MAS 写 int**（`NoAction→"8"`、`ExitEmulator→"9"`，constants L134–138；注入 L708–716）。观察：默认模板为字符串 `"None"` |

### 4.2 `Toolbox`（Toolbox.cs L31–51）
`OperBoxExportFormat`(Clipboard)/`GachaShowDisclaimerNoMore`/`PeepTargetFps`(20)/`ChooseLevel3`(true)/`ChooseLevel3Time`(540)/`ChooseLevel4`(true)/`ChooseLevel4Time`(540)/`ChooseLevel5`(true)/`ChooseLevel6`(true)/`AutoSetTime`(true)/`ShowPotential`(true)。

### 4.3 `Copilot`（Copilot.cs L34–44）
`TaskList`/`EnableUserAdditional`/`UserAdditional`/`SupportMode`(WhenNeeded)/`SelectFormation`(1)/`LoopTimes`(1)。

### 4.4 `TaskSelectedIndex`
int，默认 -1；UI 当前选中行。16 实例观测均为 6（=「领取奖励」行的索引，AUTO-MAS 场景无实际影响）。

### 4.5 顶层 `Gui`（全局，Global/Gui.cs L30–119）
`DarkMode`(SyncWithOs)/`UseNotify`/`MainTasksInvertNullFunction`/`HideHoverActionButtons`/`Localization`/`OperNameLanguage`/`UseTray`/`MinimizeToTray`/`MinimizeOnStartup`/`HideCloseButton`/`WindowTitleSelectShowList`/`WindowTitleScrollable`/`LogItemDateFormat`/`WindowPlacement`/`LoadWindowPlacement`/`SaveWindowPlacement`/`InverseClearMode`/`TransitionSpeed`/`TaskQueueInverseMode`/`UseCardLog`/`MaxNumberOfLogThumbnails`/`SoberLanguage`/`Hangover`/`LastBuyWineTime`/`CustomCulture`/`IgnoreBadModulesAndUseSoftwareRendering`/`OverlayTarget`/`SettingOrders`/`CollapesStates`/`HotKeys`/`GuideStep`/`Background`/`BackgroundMonet*`。
AUTO-MAS 写：`Localization`（L641–642）、`UseTray`/`MinimizeToTray`/`MinimizeOnStartup`（L744–752，静默运行）。

### 4.6 `Timers` / `Update` / `AnnouncementInfo`
- `Timers.List[*].IsEnabled`：AUTO-MAS 每次发车**全部关闭**（L687–697）；老壳同步 `Timer.Timer1..8 = false`。
- `Update`：`CheckOnSchedule`/`AutoDownload`/`AutoInstall`/…（Global/Update.cs）。AUTO-MAS：`CheckOnSchedule=false`、`AutoDownload=true`、`AutoInstall=false`（L735–742；老壳 `VersionUpdate.*`）。
- `AnnouncementInfo`：`LastRefreshTime`/`DoNotRemindThisAnnouncementAgain`/`DoNotShowAnnouncement`（Global/AnnouncementInfo.cs）。

---

## 5. AUTO-MAS 注入侧（`E:\AUTO-MAS\app\task\MAA\AutoProxy.py`）

### 5.1 流水线（L592–936）

1. **L601–604**：改写 `resource/tasks/tasks.json`（仅 B 服；`tools/bilibili.py` 注入 `StartUpThemes.next = BilibiliAgreement_AUTO`）。
2. **L607–621**：按用户「模式」拷贝配置模板——简洁模式复制 `data/<script_id>/Default/ConfigFile/*`，详细模式用 `<uid>/ConfigFile/*`。
3. **L623–634**：读 `gui.json` + `gui.new.json`，`Configurations` 的 key 归一为 `Default`。
4. **L641–642**：写 `Gui.Localization`（老壳 `Global.GUI.Localization`）。
5. **L644–685**：按 `task_set`（脚本里勾选的任务）合并/生成任务条目。
6. **L687–697**：关闭全部定时（两壳）。
7. **L699–706**：写 ADB 地址（`ConnectSettings.Address` / 老壳 `Connect.Address`）。
8. **L708–716**：写 `PostActions`（两壳）。
9. **L718–733**：`RunDirectly`/`StartGame`/`StartEmulator`。
10. **L735–742**：`Update` 三键。
11. **L744–752**：静默运行（`UseTray`/`MinimizeToTray`/`MinimizeOnStartup`）。
12. **L754–779**：客户端类型（`RuntimeSettings.ClientType` int；老壳 `Start.ClientType` 字符串）+ **账号规则**（官服掩码+true，其余空+false；仅更新 `AccountName`/`AccountSwitchEnabled` 两个键）。
13. **L781–794**：加载阶段计划（`MAA_STAGE_KEY`，constants L113–121）。
14. **L796–837**：写主作战（Fight）与剿灭。
15. **L839–885**：写基建（自定义排线 JSON + 回退）。
16. **L887–894**：写「活动关优先」。
17. **L896–928**：**按 `MAA_TASKS` 顺序重建 TaskQueue**：StartUp → (活动关优先) → Fight → (剩余理智) → Infrast → Recruit → Mall → Award → DepotMaintain(条件) → Roguelike；`IsEnable=task_dict`，StartUp 恒 true（L897）。
18. **L930–934**：写回 `gui.json` + `gui.new.json`。

### 5.2 用户配置键 → JSON 字段

| 用户配置键 | 落点 |
|---|---|
| `Info.Server` | `RuntimeSettings.ClientType`（int 0=Official/1=Bilibili/…，ClientType.cs L25–40）+ 老壳 `Start.ClientType` + 账号规则 |
| `Info.Id` | `StartUpTask.AccountName`（掩码 `185****3616` 形式） |
| `Info.StageMode`（Fixed/计划表） | 主作战 `StagePlan`（Fixed→`[Stage]`；计划表→`MAA_STAGE_KEY` 对应键） |
| `Info.MedicineNumb` / `StoneNumb` / `SeriesNumb` | `UseMedicine`+`MedicineCount` / `UseStone`+`StoneCount` / `Series` |
| `Info.Stage_Remain` | 剩余理智条目 `StagePlan` |
| `Task.IfActivityFirst` + `ActivityStageIndex` | 「活动关优先」条目 + `Task.ActivityMedicineNumb` |
| `Task.IfDepotMaintain` + `Task.DepotMaintainPlans` | DepotMaintainTask（`PlanList` 等） |
| `Task.IfXxx`（各任务开关） | 对应条目 `IsEnable`（StartUp 恒 true） |
| `Info.InfrastMode` / `InfrastIndex` / `Data.CustomInfrast` | InfrastTask（`Mode`/`Filename`/`PlanSelect`；失败回退 Normal） |
| `Run.TaskTransitionMethod` | `PostActions`（两壳） |
| `Function.IfSilence` | 顶层 `Gui.UseTray/MinimizeToTray/MinimizeOnStartup` |
| `Info.Mode`（简洁/详细） | 配置模板来源（Default/ConfigFile vs `<uid>/ConfigFile`） |
| 运行模式（script_config：Annihilation/Routine） | 剿灭 vs 日常的任务注入路径（manager 侧） |
| 模拟器 ADB 地址 | `ConnectSettings.Address` / 老壳 `Connect.Address` |

### 5.3 任务模板常量（constants.py L141–229）
`MAA_STARTUP_BASE`(L141–147)、`MAA_ANNIHILATION_FIGHT_BASE`(L150–187)、`MAA_REMAIN_FIGHT_BASE`(L191–228)；任务清单 `MAA_TASKS`(L57–66) / 中文名 `MAA_TASKS_ZH`(L69–78)；仓库维护材料排除表 `MAA_DEPOT_EXCLUDED_ITEM_IDS`(L81–111)。

---

## 6. tasks.json（核心侧）与资源文件

- `resource/tasks/tasks.json`：MaaCore 流水线任务定义（StartUp/Fight/Recruit/Award/Mall/Infrast 等顶层任务；含 `Depot@`/`Roguelike@` 前缀链的引用，21 处命中）。**AUTO-MAS 只在 B 服改写 `StartUpThemes.next`**（`tools/bilibili.py` L29–49）。
- 肉鸽家族核心任务在 `resource/tasks/Roguelike/*`（仓库树）；生息演算在 `resource/tasks/RA/*`（如 `Reclamation2.json`/`Reclamation3.json`）。
- **未定位**：`DepotMaintain` 在 tasks.json 与资源树均无同名核心任务文件（仅 GUI 侧命中，见 §8-2）。

---

## 7. 人机沟通口径表（口语 → 字段）

| 用户说 | 改哪里 |
|---|---|
| 「关掉公招」 | `TaskQueue` 中 RecruitTask 条目 `IsEnable=false`（或 AUTO-MAS `Task.IfRecruit`） |
| 「只刷 1-7」 | FightTask `StagePlan=["1-7"]`；`IsStageManually=true` |
| 「别碎石 / 允许碎石」 | `UseStone` + `StoneCount`（注意 `UseStoneAllowSave=false` 会强制 UseStone=false） |
| 「多吃药」 | `UseMedicine=true` + `MedicineCount` |
| 「剿灭打龙门市区」 | `UseCustomAnnihilation=true` + `AnnihilationStage`（模式需为剿灭） |
| 「基建切自定义排班」 | InfrastTask `Mode=Custom` + `Filename` + `PlanSelect` |
| 「肉鸽刷开局 / 烧水」 | RoguelikeTask `Mode=Collectible` + `CollectibleStartAwards`/`StartWithEliteTwo*` |
| 「肉鸽只投资」 | `Mode=Investment` + `InvestCount`/`InvestWithMoreScore` |
| 「肉鸽等级满了停」 | `StopWhenLevelMax=true` |
| 「活动关优先」 | 队列中「活动关优先」条目 `IsEnable`（或 AUTO-MAS `Task.IfActivityFirst`） |
| 「周几不打」 | FightTask `UseWeeklySchedule=true` + `WeeklySchedule` |
| 「信用只买打折」 | MallTask `OnlyBuyDiscount=true` |
| 「信用溢出无视黑名单」 | `ShoppingIgnoreBlackListWhenFull=true` |
| 「基建心情阈值」 | InfrastTask `DormThreshold` |
| 「不把已进驻的放宿舍」 | `DormFilterNotStationed=true` |
| 「保持某材料 ≥N」 | DepotMaintainTask `PlanList=[{Stage,DropId,DropCount}]` + `UpdateDepot=true` |
| 「静默发车」 | 顶层 `Gui.UseTray/MinimizeToTray/MinimizeOnStartup`（AUTO-MAS `Function.IfSilence`） |
| 「发车后关模拟器」 | `PostActions`（老壳 `MainFunction.PostActions`） |
| 「切账号」 | StartUpTask `AccountSwitchEnabled=true` + `AccountName`（掩码） |

---

## 8. 不确定项 / 未核清单

1. **bool? 三态与 null 落盘**：`IsEnable`/`UseMedicine` 等可置 null（GUI 右键「仅生效一次」），但 null 的准确持久化行为未实测（序列化端 `WhenWritingNull` 不写出）。
2. **DepotMaintain 核心任务链未定位**：tasks.json 与 GitHub 资源树均无同名文件（待核 MaaCore 内部或改名）。
3. **ReclamationMode 命名不一致**：v6.18.0 枚举 = `ProsperityNoSave/ProsperityInSave`，但同版视图 CalcBinding 引用 `Archive/NoArchive`；master 已把枚举重命名为 `NoArchive/Archive`。以安装版实测为准。
4. **PostActions 映射未逐项验证**：`MAA_TASK_TRANSITION_METHOD_BOOK`（NoAction→"8"、ExitEmulator→"9"）与枚举注释（None=0/ExitArknights=1/ExitEmulator=4/ExitSelf=8）不完全自洽。
5. **Roguelike 部分文案未提取**：`RoguelikeUseSupportUnit`、`RoguelikeStartAward*` 等 key 在 zh-cn.xaml 未命中（可能为运行时生成文案）。
6. **Roguelike 难度上限**：已确认 Phantom 15 / Mizuki 18 / Sami 15 / Sarkaz 18；JieGarden、BlackFlow 未取到。
7. **UserDataUpdateTriggerInterval 枚举成员未提取**（默认 `EveryTime`）。
8. **Fiammetta 目标字段取值域**为 GUI 下拉观察（无/清流/可露希尔/但书/巫恋/龙舌兰/歌蕾蒂娅），未逐一核源码。
9. **gui.json（旧壳）在 6.18 的读取键范围**未逐键验证（仅确认 ConfigurationHelper 合并逻辑）。
10. **tasks.json 仅抽样检索**：未做全量 task 名 → `$type` 对照；肉鸽/仓库家族文件未逐一核。
11. **MAA_28 异常观察**：`AccountName=''` 且 `AccountSwitchEnabled=true`，与 AUTO-MAS v2 注入规则不符（疑似历史遗留，未深究）。
12. **部分 tooltip 未提取**：`SeriesTip`、`CustomStageCodeTip`、`DepotPlanBudgetTip`、`SpecifiedDropsTip` 等未在 zh-cn.xaml 命中。
13. **`VideoRecognitionTask` 字段未核**（仅判别符，仓库无对应配置模型文件）。
14. **Fight 简洁模式强制 False** 仅来自代码（L796–837），未实测触发路径。

---

## 附录 A. 证据清单（任务工作区 `evidence/`）

| 证据 | 说明 |
|---|---|
| `remote/AutoProxy_remote.py` (1153 行) | AUTO-MAS 注入器（远程部署版） |
| `remote/constants_remote.py` | 任务常量/模板/排除表 |
| `remote/manager_remote.py`、`remote/tools/bilibili.py` | 调度器与 B 服 tasks.json 改写 |
| `remote/tasks.json` | 远端核心任务定义（抽样） |
| `all_instances_configs.txt` (271KB) | 16 实例 gui.new.json + gui.json 全量 |
| `config_aggregate.json` / `agg_summary.txt` | 实例字段聚合 |
| `queues_summary.txt` | 16 实例队列结构摘要（§7 数据源） |
| `sample_default_gui.new.json` / `sample_default_gui.json` | AUTO-MAS 默认模板（新壳/旧壳） |
| `maasrc/**` | MAA v6.18.0 源码（Configuration/Enums/Helper/视图 XAML） |
| `fields_skeleton2.md` | 全部任务模型字段骨架（自动提取） |
| `ui_map2.txt` / `vm_map.txt` | 视图文案 ↔ 绑定 ↔ 配置字段映射（自动提取） |
| `zh_cn_keys.json` / `keys_dump.txt` | zh-cn 本地化键值 |
| `maa_tree.json` | MAA 仓库树（SHA `23feeddc…`） |
| 提取脚本 | `gen_fields2.py`、`ui_map2.py`、`vm_map.py`、`agg_summary.py`、`extract_queues.py`、`keys_dump.py`、`analyze_configs.py` |

## 附录 B. 16 实例实配摘要（2026-09-27 抓取）

- 结构：全部 `Configurations.Default.{Copilot,Gui,TaskQueue,TaskSelectedIndex,Toolbox}`；`TaskQueue` 仅存在于 `gui.new.json`；`TaskSelectedIndex=6`、`Current=Default`。
- 日常机（MAA_1–9, 10, 11, 12, 28）：StartUp(账号切换) + 理智作战(SR-7，MAA_3=SR-5) + 剩余理智(1-7) + Infrast + Recruit + Mall + Award；Roguelike 条目存在但 `IsEnable=false`。
- 肉鸽机（MAA_13/14/25）：Roguelike `IsEnable=true`（13/14=Sarkaz、25=Mizuki，`Difficulty=2147483647`）；13/14 其余任务全关，25 保留 Infrast。
- 账号：官服机 `AccountName` 为掩码 + `true`；B 服/停用机 `''` + `false`（MAA_28 例外见 §8-11）。

## 附录 C. 常用路径速查

| 路径 | 内容 |
|---|---|
| `E:\MAA-Multi\MAA_N\config\gui.new.json` | 实例主配置（任务队列） |
| `E:\MAA-Multi\MAA_N\config\gui.json` | 实例旧壳 |
| `E:\MAA-Multi\resource\tasks\tasks.json` | 核心任务定义 |
| `E:\AUTO-MAS\data\<script_id>\Default\ConfigFile\` | AUTO-MAS 默认模板（简洁模式源） |
| `E:\AUTO-MAS\data\<script_id>\<user_uuid>\ConfigFile\` | AUTO-MAS 用户配置（详细模式源） |
| `E:\AUTO-MAS\app\task\MAA\AutoProxy.py` | 注入器 |
