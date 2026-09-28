# 肉鸽发车手册（MAA_ROGUELIKE_PLAYBOOK）

> 2026-09-27 ｜ 执行：mas（kanban **t_a1cf9f5b**）｜ **只读**：未发一局肉鸽、未改任何配置、未动实例。
> 目的：把「以后由 Hermes 调配**肉鸽刷取**」钉死到**字段与路径**：改哪、怎么发、怎么验、怎么退。
> 口径：实配 = 主机 MRFZ-0000 线上快照（2026-09-27 20:1x–20:3x，见 §7 证据来源）；
> MAA 源 = GitHub tag **v6.18.0**（机队实跑 **6.17.5**，差异单列）；AUTO-MAS = 线上 `E:\AUTO-MAS`（`app/` 运行时真源）。
> 相关：`docs/ROGUE_WATCH.md`（看守自证与形态表）、`docs/ROGUELIKE_BASELINE.md`（实测基线：时长/产出/失败点/排班窗口）、`docs/audits/2026-09-27_mas_task_modes.md`（AutoProxy/ManualReview/ScriptConfig 三模式）、`docs/audits/2026-09-27_mas_account_switch_chain.md`（切号链）。

---

## 0. 一页速查（TL;DR）

- **肉鸽任务在 MAA 侧** = `TaskQueue` 里一条 `RoguelikeTask`（字段全表 §1）＋一条**启用的 `StartUpTask`**（否则连游戏都进不去）。
- **真源文件**（机队）＝ `E:\maa-alas\fz-maa\l\l-N\config\gui.new.json` 的 `Configurations.Default.TaskQueue`；
  `config/gui.json` 是旧壳（实测不含 TaskQueue，1639B）。
- **发车**＝主机**登录态**计划任务 `MAAOrch-StartOne-{25,28,31,34,52}`（adb connect → 拉模拟器 → 等 boot → 起 MAA）；
  自动发车由**本机看守任务 `MAAOrch-RogueFleet`**（每 2 分钟）做（§4.4）。
- **肉鸽×产线不许并行**（本质约束）：让位区 **04:00–05:33 / 18:30–20:30**；产线在跑 / 内存 <8G 一律不发（§4）。
- **验证**看 `debug/gui.log`：`开始任务: 自动肉鸽` → `Start Task Chain: Roguelike, Task ID: 2` → `已开始探索 N 次` / `关卡: …` / `开始战斗: …`；
  `debug/asst.log` 里 `"cur_task":"<Theme>@Roguelike@…` 证主题（§5.1）。
- **回滚**＝停 MAA（杀到 0）→ 还原 `gui.new.json.bak-*` → 重启；经 AUTO-MAS 发的用 `/api/dispatch/stop`（§5.4）。
- **红线**：① 同一模拟器**不许两个 MAA**；② 产线在跑**绝不动那台机**；③ 改配置**先备份**、且**必须在 MAA 不在跑时改**（MAA 退出会把内存里的旧配置写回盘）。

---

## 1. `RoguelikeTask` 全字段（实配 × MAA 源 × 官方协议 三方核对）

**字段集与默认值**：以 `RoguelikeTask.cs` v6.18.0 为准；**实配样例**取机队 l-1（6.17.5）与 MAA_6（6.18.0）。
**「GUI 文案」列** = MAA 界面（肉鸽设置页 / 任务列表）里的实际中文；**「core 参数」列** = 传给 MAA 核心（`integration.md`「Roguelike」节）的 snake_case 名。

### 1.1 任务壳字段（所有任务类型通用）

| 字段 | 类型/取值 | 语义 | 证据 |
|---|---|---|---|
| `$type` | `"RoguelikeTask"` | 反序列化类型标记，别改 | 实配/源 |
| `TaskType` | `"Roguelike"` | 任务类别，别改 | 实配/源 |
| `Name` | string，可空 | 显示名（机队多为 `""` 或 `自动肉鸽`） | 实配 |
| `IsEnable` | bool | 任务列表里的**勾选**；机队真源要求 = `true` | 实配 |

> 机队看守的**队列闸**：启用任务必须都是「我们的」（`StartUp/Roguelike/Custom`）且**含 `Roguelike|Custom`**，
> 否则不发车只留痕（l-7 曾因队列只勾了 `[StartUp]` 被拦，见 ROGUE_WATCH §1）。

### 1.2 主题 / 难度 / 模式 / 分队 / 职业组

| 字段 | 类型/取值 | 语义（GUI 文案） | core 参数 | 证据 |
|---|---|---|---|---|
| `Theme` | `Phantom` / `Mizuki` / `Sami` / `Sarkaz` / `JieGarden` / `BlackFlow` | 肉鸽主题（「肉鸽主题」下拉：傀影/水月/萨米/萨卡兹/界园/黑流树海） | `theme`（默认 Phantom） | 源 enum + 文案 `RoguelikeTheme*` |
| `Difficulty` | int；`2147483647`=MAX、`-1`=不切换、`0`=MIN、`N`=指定 | 「难度」下拉。显示为 `MAX (主题上限)` / `NotSwitch (-1)` / `MIN (0)` / `N`。**指定难度未解锁时自动选当前已解锁最高难度** | `difficulty`（默认 -1） | `RoguelikeSettingsUserControlModel.UpdateRoguelikeDifficultyList()`；机队实配=2147483647 |
| `Mode` | 配置存**字符串名**：`Exp`/`Investment`/`Collectible`/`CLP_PDS`/`Squad`/`Exploration`/`FindPlaytime`/`BlackFlowBabyAnimal` | 「模式」下拉（列表随主题变，见 §2.2） | `mode`（**数字**，见 §2.2） | 实配（`"Mode": "Exp"`）+ 源 enum |
| `Squad` | string（分队名，如 `指挥分队`） | 「开局分队」 | `squad` | 实配 + `StartingSquad` 文案 |
| `SquadCollectible` | string | 「刷开局使用分队」（仅烧水/凹开局语义，模式 4 用） | `collectible_mode_squad` | 源注释「烧水时候的分队」 |
| `Roles` | `先手必胜`/`稳扎稳打`/`取长补短`（+界园/黑流树海：`灵活部署`/`坚不可摧`；通用加 `随心所欲`） | 「开局职业组」 | `roles` | `UpdateRoguelikeRolesList()` |

### 1.3 开局干员（**6.17.5 与 6.18.0 字段不同，必须对版本写**）

| 版本 | 字段 | 语义 | core 参数 |
|---|---|---|---|
| ≤6.17.5（机队实跑） | `CoreChar` (string) ＋ `UseSupport` (bool) | 开局干员**中文名**（单个）＋是否借好友助战 | `core_char` / `use_support` |
| 6.18.0（生产 MAA_1..12） | `StartingOpers`: `[{ "Name": 干员名, "UseSupport": bool }, …]` ＋ `UseAdditionalStartingOpers` (bool) | 「开局干员顺位」按顺序招募**最多 3 位**；第 2、3 位由 `UseAdditionalStartingOpers` 开关；三位置职业不能相同 | `core_char_list`（与 `core_char` 同传时**以 list 为准**） |

- 干员名**只认中文名**（无论区服）；留空 = 按练度自动选。
- 机队实配（6.17.5）：`"CoreChar":"维什戴尔","UseSupport":true`；MAA_6 实配（6.18.0）：`"StartingOpers":[{"Name":"维什戴尔","UseSupport":true}],"UseAdditionalStartingOpers":false`。
- 建议把希望消耗高的干员排前面（官方文档原文：招募只在前几页翻找，排位靠后可能找不到）。

### 1.4 投资 / 烧水 / 凹开局 / 种子

| 字段 | 类型/取值 | 语义（GUI 文案） | core 参数 |
|---|---|---|---|
| `Investment` | bool | 投资源石锭（刷钱核心） | `investment_enabled` |
| `InvestCount` | int（实配 999） | 投资次数上限 | `investments_count` |
| `InvestWithMoreScore` | bool | 投资后购物刷分（**仅模式 1**） | `investment_with_more_score` |
| `CollectibleStartAwards` | flags 字符串（`"HotWater, Hope"`） | 「刷开局期望奖励」：`HotWater`热水壶/`Shield`盾/`Ingot`锭/`Hope`希望/`Random`随机奖励/`Key`钥匙/`Dice`骰子/`Idea`构想/`Ticket`票券（部分仅特定主题） | `collectible_mode_start_list`（对象） |
| `CollectibleShopping` | bool | 「刷开局模式启用购物」 | `collectible_mode_shopping` |
| `StartWithEliteTwo` | bool | 凹干员精二直升（**仅模式 4**） | `start_with_elite_two` |
| `StartWithEliteTwoOnly` | bool | 只凹直升不作战（需上一项 true） | `only_start_with_elite_two` |
| `StartWithSeed` / `Seed` | bool / string | 「使用指定种子开局」；仅萨卡兹·投资·点刺成锭/后勤分队生效 | `start_with_seed` |

### 1.5 主题专属开关（写了不生效 = 主题/模式不对，不是 bug）

| 字段 | 适用 | 语义 | core 参数 |
|---|---|---|---|
| `ExpectedCollapsalParadigms` | Sami，模式 5 | 「坍缩范式列表」（英文分号 `;` 分隔；留空用默认表） | `expected_collapsal_paradigms` |
| `SamiFirstFloorFoldartal` / `SamiFirstFloorFoldartals` | Sami | 凹第一层远见密文板 | `first_floor_foldartal` |
| `SamiNewSquad2StartingFoldartal` / `SamiNewSquad2StartingFoldartals` | Sami + 生活至上分队 | 生活队凹开局密文板（最多 3 个，`;` 分隔） | `start_foldartal_list` |
| `RefreshTraderWithDice` | Mizuki | 用骰子刷新商店（刷指路鳞） | `refresh_trader_with_dice` |
| `FindPlaytimeTarget` | JieGarden，模式 20001 | 目标常乐节点：`Ling`=令(1)/`Shu`=黍(2)/`Nian`=年(3)；**该模式必填** | `find_playTime_target` |
| `BlackFlowCultivationTarget` | BlackFlow，模式 30001 | 目标襁褓动物：`Cat`/`FeatheredSerpent`/`Dog`/`Cerberus` | `blackflow_cultivation_target` |

### 1.6 自动切换 / 停止条件 / 其他

| 字段 | 类型 | 语义（GUI 文案） | core 参数 |
|---|---|---|---|
| `StartCount` | int（实配 99999） | 「探索次数」——达到后自动停止任务；**发一局=1** | `starts_count`（默认 2147483647） |
| `StopWhenDepositFull` | bool | 存款满了停 | `stop_when_investment_full` |
| `StopAtFinalBoss` | bool | 「在第五层 BOSS 前暂停」（Phantom 外） | `stop_at_final_boss` |
| `StopWhenLevelMax` | bool | 「满级后自动停止」 | `stop_at_max_level` |
| `MonthlySquadAutoIterate` | bool | 「月度小队自动切换」（模式 6） | `monthly_squad_auto_iterate` |
| `MonthlySquadCheckComms` | bool | 「月度小队通讯」（切换依据含通讯解锁） | `monthly_squad_check_comms` |
| `DeepExplorationAutoIterate` | bool | 「深入调查自动切换」（模式 7） | `deep_exploration_auto_iterate` |
| `UseSupportNonFriend` | bool | 「可以使用非好友助战」 | `use_nonfriend_support` |
| `SquadIsFoldartal` | bool（**派生值**） | MAA 自己算的（=Sami+生活至上+模式4），**别手工改** | — |

**版本差异注意**：6.18.0 起 `UseSupport`（顶层）与 `CoreChar` 退役为 `StartingOpers`/`UseAdditionalStartingOpers`；
6.17.5 及更早没有 `StartingOpers`。**机队（fz-maa）是 6.17.5 ⇒ 写 `CoreChar`；给生产 MAA_1..12（6.18.0）写才用 `StartingOpers`。**

---

## 2. 主题与模式

### 2.1 现网 Theme 全表（6 个；以资源目录与文案实测）

| Theme | 中文（GUI 下拉） | 难度上限 | 资源目录 | 备注 |
|---|---|---|---|---|
| `Phantom` | 傀影 | 15 | `resource/roguelike/Phantom/` | 老主题；默认值 |
| `Mizuki` | 水月 | 18 | `…/Mizuki/` | 指路鳞（骰子刷商店） |
| `Sami` | 萨米 | 15 | `…/Sami/` | 密文板 / 坍缩范式（模式 5） |
| `Sarkaz` | 萨卡兹 | 18 | `…/Sarkaz/` | 蓝图、思绪；刷钱种子 |
| `JieGarden` | 界园 | 18 | `…/JieGarden/` | 常乐节点（模式 20001）、通宝 |
| `BlackFlow` | 黑流树海 | 15 | `…/BlackFlow/` | 襁褓动物（模式 30001）；**旧资源没有**（共享 `E:\MAA-Multi\resource` 就没有，MAA_6 与机队副本有） |

> 「现网存在哪些」两层口径：**资源里有**（上表，随资源版本）＋**该账号客户端里可选**（跟着游戏进度走，机队实测在用 Sami/Sarkaz/JieGarden/Mizuki）。
> 机队各机当前主题：l-1=Sami｜l-2=Sarkaz｜l-4=Sarkaz(官服)｜l-5=JieGarden｜l-7=Mizuki（2026-09-27 实配）。

### 2.2 Mode 全表（配置写字符串，核心收数字）

| 配置值 | 数字 | 中文（GUI 模式下拉） | 适用 |
|---|---|---|---|
| `Exp` | 0 | 刷等级，尽可能稳定地打更多层数 | 全主题（默认） |
| `Investment` | 1 | 刷源石锭，投资完成后自动退出 | 全主题 |
| `Collectible` | 4 | 刷开局，刷取热水壶或精二干员开局 | 全主题 |
| `CLP_PDS` | 5 | 刷坍缩范式，遇到非稀有坍缩范式后直接重开 | **仅 Sami**（下拉里才出现） |
| `Squad` | 6 | 刷月度小队 | 全主题 |
| `Exploration` | 7 | 刷深入调查 | 全主题 |
| `FindPlaytime` | 20001 | 刷常乐节点，第一层进洞，找不到需要的节点就重开 | **仅 JieGarden**；必配 `FindPlaytimeTarget` |
| `BlackFlowBabyAnimal` | 30001 | 刷襁褓动物 | **仅 BlackFlow**（其下拉为 3 项：飞三层刷等级/刷源石锭/刷襁褓动物） |
| — | 2 / 3 | 已移除 / 未开放（传入会被核心拒绝） | — |
| （`10001`） | 10001 | 快速通过第一层 | 仅 Sarkaz（GUI 未暴露） |

### 2.3 「用户口述 → 字段」速查（肉鸽版）

| 口述 | 落地 |
|---|---|
| 刷等级 / 尽量打更多层 | `Mode=Exp` |
| 刷源石锭 / 存钱 | `Mode=Investment` + `Investment=true` + `InvestCount=N`（刷完一层退） |
| 凹开局 / 只要热水壶 | `Mode=Collectible` + `CollectibleStartAwards` 勾热水壶（`"HotWater"`） |
| 刷坍缩范式 | `Theme=Sami` + `Mode=CLP_PDS` + `ExpectedCollapsalParadigms`（留空=默认表） |
| 月度小队 / 深入调查 | `Mode=Squad` / `Mode=Exploration`（+对应 AutoIterate 开关） |
| 刷常乐·令 | `Theme=JieGarden` + `Mode=FindPlaytime` + `FindPlaytimeTarget=Ling` |
| 满级就停 | `StopWhenLevelMax=true` |
| 只打一局 | `StartCount=1` |
| 一直刷 | `StartCount=99999`（＋三个 Stop* 都 false） |
| 难度拉到最高 | `Difficulty=2147483647`（=MAX；未解锁自动落到已解锁最高） |
| 借好友助战 | `UseSupport=true`（6.17.5）/ `StartingOpers[].UseSupport`（6.18+） |

---

## 3. 最小可运行发车路径

### 3.0 两条路，先选对

| 路线 | 用在哪 | 发车方式 | 本手册态度 |
|---|---|---|---|
| **A. 机队直发（fz-maa）** | 肉鸽专用机队 l-1/l-2/l-4/l-5/l-7（也是产线同款模拟器的另一套 MAA） | 改 `gui.new.json` → 起 MAA（`MAAOrch-StartOne-*`）或交给看守 | **首选**（现有看守/让位链路都围绕它） |
| **B. AUTO-MAS（AutoProxy）** | 生产 54 用户的那套（MAA_1..MAA_12，同一批模拟器） | `POST /api/dispatch/start {mode:"AutoProxy"}` | 可行但要动生产配置（`IfRoguelike`），**需用户授权**；见 §3.3 |

### 3.1 路线 A：机队直发（最小可运行）

**改哪**：`E:\maa-alas\fz-maa\l\l-N\config\gui.new.json`（l-1/l-2/l-4/l-5/l-7）→ `Configurations.Default.TaskQueue`。

**完整 TaskQueue 片段**（可直接放进 MAA 配置的形态；下面是「两条任务」的最小集，字段按 6.17.5 机队版）：

```json
"TaskQueue": [
  {
    "$type": "StartUpTask",
    "AccountName": "",
    "AccountSwitchEnabled": false,
    "Name": "开始唤醒",
    "IsEnable": true,
    "TaskType": "StartUp"
  },
  {
    "$type": "RoguelikeTask",
    "Theme": "Sami",
    "Difficulty": 2147483647,
    "Mode": "Exp",
    "Squad": "指挥分队",
    "SquadCollectible": "指挥分队",
    "Roles": "稳扎稳打",
    "StartCount": 99999,
    "CoreChar": "维什戴尔",
    "Investment": false,
    "InvestCount": 999,
    "InvestWithMoreScore": false,
    "CollectibleStartAwards": "HotWater",
    "CollectibleShopping": false,
    "StartWithEliteTwo": false,
    "StartWithEliteTwoOnly": false,
    "ExpectedCollapsalParadigms": "",
    "MonthlySquadAutoIterate": true,
    "MonthlySquadCheckComms": true,
    "DeepExplorationAutoIterate": true,
    "FindPlaytimeTarget": "Ling",
    "BlackFlowCultivationTarget": "Cat",
    "UseSupport": true,
    "UseSupportNonFriend": true,
    "RefreshTraderWithDice": false,
    "SquadIsFoldartal": false,
    "SamiFirstFloorFoldartal": false,
    "SamiFirstFloorFoldartals": "",
    "SamiNewSquad2StartingFoldartal": false,
    "SamiNewSquad2StartingFoldartals": "",
    "StopWhenDepositFull": false,
    "StopAtFinalBoss": false,
    "StopWhenLevelMax": false,
    "StartWithSeed": false,
    "Seed": "",
    "Name": "",
    "IsEnable": true,
    "TaskType": "Roguelike"
  }
]
```

（上例 = **l-1 实配原样**，即「连续刷」。**发一局**：把 `StartCount` 改 `1`。机队真实文件里 TaskQueue 还有一堆 `IsEnable=false` 的 Fight/Recruit/Infrast…，**保持原样别删**，只动这两条即可。）

**改的步骤（顺序不能反）**：

1. **确认该机没有 MAA 在跑**（杀了并**确认残留=0**，循环杀）——MAA 退出时会把内存里的旧配置写回盘，先改后杀 = 白改（ROGUE_WATCH §5.17 的坑）。
2. **备份**：`copy gui.new.json gui.new.json.bak-<yyyyMMdd>-<原因>`（例：`gui.new.json.bak-20260924-sarkaz`）。
3. **改文件**：编辑 `TaskQueue` 的 `RoguelikeTask`（字段见 §1；JSON 无注释、UTF-8、别加 BOM）。
4. **发车**（二选一）：
   - 手动：主机上 `schtasks /run /tn MAAOrch-StartOne-<idx>`（idx=25/28/31/34/52），它做 adb connect → `MuMuManager control --vmindex N launch` → 等 `sys.boot_completed` → `Start-Process MAA.exe`（工作目录=该 l-N 目录）。
   - 自动：等本机看守 `MAAOrch-RogueFleet`（每 2 分钟）在闸门开时发（§4.4）。
5. **验证**：§5.1 判据表。

**怎么让 MAA 读到**：MAA **只在启动时读** `config/gui.new.json`（工作目录 = `E:\maa-alas\fz-maa\l\l-N`）；运行中改文件**无效**，且退出时**会写回内存态**。所以「改文件」的生效方式永远是：**停→改→重启**。
（GUI 等价操作：任务列表里点「自动肉鸽」的**设置**展开=肉鸽设置页，各控件文案见 §1 表；改完点保存/GUI 退出写盘——但机队场景推荐直接改文件，可备份可审计。）

### 3.2 路线 A 的「发一局 / 连续刷」对照

| 目标 | 关键字段 | 后续行为 |
|---|---|---|
| 发一局 | `StartCount=1` | 打满 1 次探索后任务链正常收尾；**若看守在跑，它会把停着的又拉起来**（process-gone/idle-clean ⇒ 重发）——要真停住用 `runtime/rogue_hold.flag` 或临时 `schtasks /change /tn MAAOrch-RogueFleet /disable` |
| 连续刷 | `StartCount=99999` + 三个 Stop* 全 false | 局与局之间自动续（结算→下一局），直到看守收工/让位/出错 |
| 刷到满级自动停 | `StopWhenLevelMax=true` | 满级后 MAA 自己收尾（l-2 曾因满级+Exp 被判"假完成"，ROGUE_WATCH §5.24——**这不是故障**） |

### 3.3 路线 B：AUTO-MAS 侧怎么发（AutoProxy vs ManualReview）

**结论：肉鸽只能用 `AutoProxy` 发。**（依据：`docs/audits/2026-09-27_mas_task_modes.md` + `app/task/MAA/AutoProxy.py`）

- `ManualReview` = **只跑到登录**（`TaskQueue=[StartUp]` 导出，跑完自退＋弹窗问人）⇒ 跑不了肉鸽。
- `ScriptConfig` = 只把配置装进 MAA 并**拉起 GUI**（`RunDirectly=False`），**不跑任务链** ⇒ 也不能用来刷。
- `AutoProxy` = 真跑：开模拟器 → `set_maa` 装配用户配置 →「**剿灭 → 日常**」两轮依次跑。

**AutoProxy 里肉鸽是怎么被带上的**（`AutoProxy.py` L416-426 / L608-627 / L836-845）：

1. 任务清单 = `MAA_TASKS = [StartUp, DepotMaintain, Fight, Infrast, Recruit, Mall, Award, Roguelike]`（`utils/constants.py`）；
2. 每个任务开不开 = **该用户的 `Task.If<Task>` 开关**（Roguelike ⇒ `IfRoguelike`）——当前 **54 个用户全部 `IfRoguelike=false`**（实测）；
3. 各任务的**参数**取自该用户自己的 **ConfigFile**（`data/<script_uid>/<user_uid>/ConfigFile/gui.new.json`）里「同类型的第一条任务」为底，AUTO-MAS 只翻 `IsEnable` 并强制 `StartUp=true`。
   ⇒ **肉鸽字段（Theme/Mode/…）是在用户的 ConfigFile 里配的**，不是在 AUTO-MAS 界面上配的。
4. 发车：`POST /api/dispatch/start {"taskId":"<队列uid 或 脚本uid>","mode":"AutoProxy"}`；返回 `{taskId:<task_uid>}`；**停机用返回的 task_uid**：`POST /api/dispatch/stop {"taskId":"<task_uid>"}`（走它才会解锁配置并把 `Temp→config` 还原；**别 kill**）。

**要开这条路**：把目标用户的 `IfRoguelike` 置 true＋把肉鸽参数写进其 ConfigFile ⇒ **生产写操作，须用户批准后再做**（本手册只给流程，未执行）。
发车后的验收：MAA 侧日志判据同 §5.1（AUTO-MAS 也会把每个用户日志投到它的界面/历史里）。

---

## 4. 占用与让位

### 4.1 一局/一连串大概占用实例多久（历史日志统计）

口径：解析五台机 `debug/gui.log(+.bak)`（全量历史）里「已开始探索 N 次」与「肉鸽结算」时间戳。
「活跃节奏」= 相邻两次开探索的间隔**截尾 ≤60 分钟**（去掉停机空档）；「单局」= 开探索→下一次结算的配对（≤4h）。

| 机 | 活跃节奏中位数（n） | 单局中位数（p90） | 备注 |
|---|---|---|---|
| l-1 | **10.8 分钟**（n=760） | 10.9（32.6） | 日服 Sami |
| l-2 | **30.3 分钟**（n=161） | 41.8（99.2） | 日服 Sarkaz；含满级期 |
| l-4 | 24.6 分钟（n=35） | 18.6（222） | 官服，样本少 |
| l-5 | 21.0 分钟（n=253） | 24.2（59.2） | 日服 JieGarden |
| l-7 | **9.6 分钟**（n=276） | 11.8（54.8） | 日服 Mizuki |

**用法**：一局 ≈ **10–30 分钟**（典型），波峰 1–4 小时（深层局/停等）；
「一连串」= 局与局自动续的**长驻**占用——时长不取决于单局，取决于**让位闸**什么时候把它收掉。
⇒ 估算占机：按「小时 × 台」算，一晚上窗口（例：22:30–03:30 ≈ 5h）单台可刷约 10–30 局。

### 4.2 机 ↔ 产线班次（这就是让位区的由来）

每台肉鸽机的模拟器**同时是产线的班次槽位**（两套 MAA：肉鸽=fz-maa 副本，日常=AUTO-MAS 的 MAA-Multi 副本；**同一模拟器**）：

| 机 | emu idx | 产线班次（AUTO-MAS 队列） | 早班发车 | 晚班发车 | 肉鸽 MAA | 日常 MAA |
|---|---|---|---|---|---|---|
| l-1 | 25 | 日常定时1 / 晚班定时1 | 05:00 | 19:30 | `fz-maa\l\l-1` (6.17.5) | `MAA_1` (6.18.0) |
| l-2 | 28 | 日常定时2 / 晚班定时2 | 05:03 | 19:33 | `l\l-2` | `MAA_2` |
| l-5 | 34 | 日常定时9 / 晚班定时9 | 05:24 | 19:54 | `l\l-5` | `MAA_9` |
| l-4 | 31 | 日常定时10 / 晚班定时10 | 05:27 | 19:57 | `l\l-4` | `MAA_10` |
| l-7 | 52 | 日常定时12 / 晚班定时12 | 05:33 | 20:03 | `l\l-7` | `MAA_12` |

（24 条班次队列 05:00/05:03/…/05:33 与 19:30/…/20:03 陆续发车；主机另有 **DailyRestart0400 / DailyRestart1900** 每日 04:00 / 19:00 `shutdown /r`，AUTO-MAS 随开机自启。）

### 4.3 让位规则（看守实现，参数在 `tools/lib/rogue_plan.mjs`）

- **让位区（不发车/收工）**：**04:00–05:33**（早班 05:00 前 60 分钟 + 爬坡到最后一槽 05:33）与 **18:30–20:30**（晚班 19:30 前 60 分钟 + 爬坡到 20:30）。其余时间**允许**发车。
- **让路闸（gate）**，全过才发：① 产线**不在跑**（有槽在写 asst.log 或产线 MAA 进程存在 ⇒ 关）；② 产线**连续空闲 ≥6 分钟**；③ 窗口内；④ 主机空闲内存 **≥8G**（<14G 一轮只发 1 台）。fail-closed：拿不准就不发。
- **收工让路（wrapUp）**：产线一恢复、或进了让位区 ⇒ **收掉我们的 MAA**（不动模拟器），防「同一台模拟器两个 MAA」（红线①）。
- 时间窗口径一句话：**肉鸽只能吃两班之间的空档**；「先保日常再保肉鸽」。

### 4.4 看守任务（谁在替你做让位）

| 任务 | 在哪 | 现状（09-27 20:3x） | 作用 |
|---|---|---|---|
| `MAAOrch-RogueFleet` | **本机**（`run_hidden.vbs`→`rogue_fleet_launch.ps1`→`node tools\rogue_fleet_watch.mjs --apply`，每 2 分钟） | Ready（每 2 分钟；20:3x 时正在跑） | 机队发车/救援/让位/收工全自动 |
| `MAAOrch-Rogue0Watch` | 本机 | **Disabled** | #0（本机 desktop 水月号）看守，用户手动开的 MAA |
| `MAAOrch-RogueWatch` | 主机 MRFZ-0000（`E:\maa-alas\fz-maa\rogue_watch.mjs`） | **Disabled**（旧） | 主机侧旧看守（已由本机那套取代） |
| `MAAOrch-StartOne-{25,28,31,34,52}` | 主机 | Ready（最近 lastResult=0） | **登录态发车**：拉模拟器+起 MAA |

**人工按住**：`runtime/rogue_hold.flag`（存在=看守不动手）；或 `schtasks /change /tn MAAOrch-RogueFleet /disable`。
**现状**（写本手册时）：五台全部 `parked`（未在刷），闸门因「产线在跑(3/16)」关闭——这就是让位在工作。

---

## 5. 验证与回滚

### 5.1 跑起来后看什么（判据，全部实测格式）

**`E:\maa-alas\fz-maa\l\l-N\debug\gui.log`**（成功链路的原文样式）：

```
[2026-09-27 18:26:16.748] 完成任务: 开始唤醒
[2026-09-27 18:26:16.748] Completed Task Chain: StartUp, Task ID: 1
[2026-09-27 18:26:17.238] 开始任务: 自动肉鸽
[2026-09-27 18:26:17.239] Start Task Chain: Roguelike, Task ID: 2
[2026-09-27 18:26:59.776] 肉鸽结算: ✗ (n0) 探索: 2 层 10 步 / 战斗: 5/ 2紧急/ 0BOSS / 招募: 8 收藏品: 4 / 得分: 129 / Exp: +129 Skill: +1
[2026-09-27 18:27:12.439] 已开始探索 1 次
[2026-09-27 18:28:27.840] 预见: 眼睛
[2026-09-27 18:28:32.798] 关卡: 普通作战
[2026-09-27 18:29:03.867] 开始战斗: 度假村冤魂
```

**`debug/asst.log`**（主题核对 + 链结局）：

- 主题实证：`"cur_task":"Sarkaz@Roguelike@IntegratedStrategies"` 一类（`<Theme>@Roguelike@…` 出现即主题加载对了）。
- 链结局只看两个词：`TaskChainError`（错） vs `TaskChainCompleted`（完）。
- ⚠️ 「任务已全部完成！」永远紧跟「任务出错」（差 ~0.5s）——**完成 ≠ 成功**；且**子步骤级** `exec times exceeded` 正常刷图也会出现，不算失败。

**判定表（简版；全 12 形态见 ROGUE_WATCH §3）**：

| 现象 | 判据 | 处置 |
|---|---|---|
| 正常在刷 | `已开始探索`/`关卡:`/`开始战斗:` 在滚，asst.log 在写 | 不动 |
| 链报错 | `任务出错: 自动肉鸽` + `TaskChainError` | 看守自动：收 MAA 重发（先轻后重） |
| 假完成 | 链级 `TaskChainCompleted` 但**无进度**（探索计数不动） | 同上；别判「当日封顶」 |
| 卡死 | 同一 `cur_task` ≥20 分钟 / `pre_task` 重试 20/20 | 升级：关客户端→重启模拟器 |
| 掉线 | gui「游戏掉线，停止任务」/ `OfflineConfirm` / 「联系中断」屏 | **关客户端**重拉（只重拉 MAA 必再撞） |
| 画面冻结 | 连续多轮截图 md5 相同（看守自动） | 冻结阶梯：关客户端→重启模拟器→交人工 |
| 客户端不在 | MAA 在写日志但 `pidof` 空（启动阶段豁免） | 连续 3 轮才动手 |

**命令**（本机）：

```bash
node tools/rogue_fleet_watch.mjs --status     # 机队一行状态（含闸门）
node tools/rogue0_watch.mjs --status          # #0
node tools/rogue_acceptance.mjs               # ★ 验收一页纸
node tools/rogue_report.mjs --hours 12 --md   # 盯号日报
```

机读状态：`runtime/rogue_fleet_state.json`、`runtime/rogue_fleet_watch.log`、现场图 `runtime/fleet_cases/`。

### 5.2 怎么判断「失败」并止损

- **发车没起来**：发车后 6 分钟仍 `parked` ⇒ 判「这次没起来」（台账 `start-failed`）；连续两次交人工（看守已实现）。
- **救不活**：同一形态 1 小时内连救 ≥3 次 / 换花样犯 ⇒ 交人工静默 2h（不再烧机器）。
- **真停机**（要人看）：`restart-emulator` 后设备回不来（端口漂移）等 ⇒ 看守留痕「需要人看」，人去按 ROGUE_WATCH §5.22 的排查走。

### 5.3 单台手动核查（只读）

```powershell
# 进程在不在（按可执行路径认）
Get-Process MAA | Where-Object { $_.Path -like 'E:\maa-alas\fz-maa\l\l-*' } | Select Id,Path
# 日志尾部
Get-Content 'E:\maa-alas\fz-maa\l\l-2\debug\gui.log' -Tail 30
Get-Content 'E:\maa-alas\fz-maa\l\l-2\debug\asst.log' -Tail 10
```

### 5.4 回滚

| 场景 | 动作 |
|---|---|
| 改了 `gui.new.json` 想还原 | **停 MAA（杀到 0）** → `copy /y gui.new.json.bak-<日期>-<原因> gui.new.json` → 重启（`MAAOrch-StartOne-<idx>` 或让看守发） |
| 经 AUTO-MAS 发的任务要停 | `POST /api/dispatch/stop {"taskId":"<start 返回的 task_uid>"}`（优雅停＋解锁＋`Temp→config` 还原）；**别 kill MAA** |
| 不想让看守再动手 | `runtime/rogue_hold.flag` 落一个文件（人按住）；长期=disable `MAAOrch-RogueFleet` |
| 要让位给日常（立刻） | 看守的收工让路会做；人工=等闸门/`--drill-wrapup` 演练口径收 MAA（不动模拟器） |
| 主题与界面不匹配（"救不活"） | 核对 `Theme` 与**游戏当前界面**是否一致，再改（ROGUE_WATCH §5.6 教训）；改前先快照 |

**红线（再强调）**：同一模拟器两个 MAA；产线在跑不动那台；改配置先备份；MAA 在跑时改文件无效；`gui.json` 不是真源。

---

## 6. 还没验到的（诚实清单）

1. **本手册所有「发车」动作均未实测执行**（任务要求只读）。但每件要素都有既存实证：`MAAOrch-StartOne-*` lastResult=0、看守近期日志、ROGUE_WATCH §5.16-5.26 的实盘记录。
2. **AUTO-MAS AutoProxy 带肉鸽没跑过**：54 个用户 `IfRoguelike` 全 false，且改它是生产写操作（未做）。
3. **6.18.0 的 `StartingOpers` 没在机队实测**（机队全是 6.17.5；样例来自 MAA_6 实配与源码）。
4. **时长统计是近似**：结算配对/60 分钟截尾口径；l-4（官服）样本少（n=9）。
5. **BlackFlow 主题**：资源里有、机队没用过；「该账号可选」未验证。
6. 让位区的**现场确认项**（ROGUE_WATCH §5.26 遗留）：早班产线是否存在「定时点≠实际发车点」的滞后。

## 7. 证据来源（全部可复核）

- **实配**：`E:\maa-alas\fz-maa\l\l-{1,2,4,5,7}\config\gui.new.json`（2026-09-27 20:1x 快照，本任务读取）；`E:\MAA-Multi\MAA_6\config\gui.new.json`（6.18.0 样例）；`E:\MAA-Multi\MAA_25/28`（旧副本）。
- **MAA 源**：GitHub `MaaAssistantArknights/MaaAssistantArknights` tag **v6.18.0**：`src/MaaWpfGui/Configuration/Single/MaaTask/RoguelikeTask.cs`（全字段+枚举）、`ViewModels/UserControl/TaskQueue/RoguelikeSettingsUserControlModel.cs`（难度/模式/分队列表）、`Res/Localizations/zh-cn.xaml`（界面文案）；与 tag **v6.17.5** 的 `RoguelikeTask.cs` diff（CoreChar→StartingOpers）。
- **官方协议**：`docs.maa.plus` protocol → integration「Roguelike」参数节（theme/mode/core_char_list/starts_count/… 取值与默认）。
- **AUTO-MAS**：线上 `E:\AUTO-MAS`：`config\ScriptConfig.json`（54 用户；`Task.IfRoguelike`）、`config\QueueConfig.json`（24 班次队列 + 时间）、`app\task\MAA\AutoProxy.py`（L416-426 任务清单、L608-627 任务底稿、L836-845 导出）、`app\utils\constants.py`（`MAA_TASKS`）；模式结论见 `docs/audits/2026-09-27_mas_task_modes.md`。
- **看守/让位**：`D:\1-2\MAAOrch\tools\{rogue_fleet_watch.mjs, lib\rogue_plan.mjs, lib\rogue_judge.mjs}`、`docs\ROGUE_WATCH.md`；本机计划任务 `MAAOrch-RogueFleet`（Ready/2 分钟）。
- **日志统计**：五台机 `debug\gui.log(+.bak)` 全量解析（本任务，2026-09-27 20:2x）。
- **主机事实**：计划任务 `MAAOrch-StartOne-{25,28,31,34,52}`（action=`_launch_one.ps1 -Index N -StartMaa`）、`DailyRestart0400/1900`（04:00/19:00 `shutdown /r`）、`AUTO-MAS_AutoStart`；`schtasks` 实测。
