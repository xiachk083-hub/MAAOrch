# GLOSSARY — 沟通黑话 ↔ 系统字段词典

> **产出**：kanban 卡 `t_8fe7a5a1`（2026-09-28）。**只读汇总**：全部条目取自仓库既有文档 / 工具 / 实测记录，不新增定义；文档未收录的口语词标「口语」并给出可核验锚点，不臆造。
> **用途**：听到或要说某个词（红票、搓玉、保有、让位区、抽歪了、理智溢出、串号、孤儿化、发车、缺口、台账、租约……），先在这里对齐它对应的**字段 / 文件 / 判据**。
> **出处列** = 文档或文件短名（相对仓库根；`tools/…`、`runtime/…` 同理），`§` 指该文小节。
> **口径优先级**：现场（进程 / 日志 / 文件实测）＞ 专项文档 ＞ 本表；「常见误用」列 = 既有文档明确记录过的坑 / 反例。
> **词条：150 条**，14 组。

---

## A. 实体与镜像（账号 → 脚本 → 槽位 → 实例）

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 账号 / 用户（user） | AUTO-MAS 用户条目 = 一个游戏账号（含服别与掩码）；共 60 个（58 启用 + 2 停用）；脚本内 1–4 个号，**逐用户串行** | `UserData[uid]`；脚本 `Info.Status`（停用）；history 目录名 = 账号名 | MAS_MASTER_HANDBOOK §2.1；HERMES_MAS_PLAYBOOKS §0.1 | 把「用户」当「脚本」；简称「官-6」不写明是脚本还是号；忽略停用号也出现在清单里 |
| 脚本（script） | 一台「机」的配置 = 1 个模拟器实例 + N 个号；**队列项就是脚本**；54 个（49 产线 + 5 肉鸽 l-*） | `ScriptConfig.json`；脚本 ID「明日方舟-<服>-<标签>」；`Emulator.Index` | MAS_MASTER_HANDBOOK §2.1；HERMES_MAS_PLAYBOOKS §0.1 | 把 l-* 肉鸽机当普通脚本补跑 / 发车（红线⑥）；以为改脚本内容 = 改队列顺序（独立） |
| 槽位（slot） | 一个 MAA 程序目录 `E:\MAA-Multi\MAA_1..12`（12 个在役）；**同目录同一时刻只准一个任务**；互杀按目录路径杀进程 | `E:\MAA-Multi\MAA_N`；「目录忙」判据 = 该目录有 `MAA.exe`；`slot_busy` / `slot_warm` 闸 | MAS_MASTER_HANDBOOK §2.1 / §5.1；MAS_ONDEMAND §4 | 看进程一时不在就当空闲——脚本间隙 MAA.exe 会消失 1–2 分钟（`slot_warm` 防的就是这个） |
| 实例（emu idx） | MuMu 虚拟机编号；55 台在役；主 adb 口：idx≤31 = `16384+32×idx`，idx≥32 = `16388+32×(idx−32)`；副口 `5555+2×idx` | `MuMuVMMHeadless` 进程；`adb devices`；`docs/MAS_EMULATORS.md` 全表 | MAS_MASTER_HANDBOOK §5.2；ACCOUNT_INVENTORY §3 | 信 MuMuManager 状态面（全实例恒报 `is_process_started=false`）；把旧端口表当保证（**执行前现查**）；用旧公式 −1020 |
| 机队 / 产线 | 产线 = 49 脚本日常批跑（MAA_1..12）；肉鸽机队 = `E:\maa-alas\fz-maa\l\l-1/2/4/5/7`（独立队列一直刷肉鸽）；两者**共用模拟器** | `rogue_fleet_watch.mjs`；`MAAOrch-StartOne-{25,28,31,34,52}` 计划任务；机队队列闸（启用任务必须含 `Roguelike`/`Custom`） | ROGUE_WATCH §1；HERMES_MAS_PLAYBOOKS §0.4 | 在同一台模拟器上叠两个 MAA（绝对禁止）；把 l-* 写进产线队列；产线在跑时动那台机 |
| 掩码与 UID | 掩码 = 手机号前 3 + `****` + 后 4（官服；B/日服无掩码，台服邮箱前 3 位 + `***@域`）；UID = 标题页 / 大厅 `ID:<8-9位>`，设备侧权威副本 `u8sdk_cached_uid`；**一切报告只写掩码** | `AccountName`；`USER_CACHE`；`u8sdk_cached_uid`；名册 `runtime/reports/mas_account_master_*` | MAS_MASTER_HANDBOOK §2.3；GAME_CLIENT_MECHANICS §2 | 拿掩码去「反查手机号」或写明文；认号凭掩码 + UID（＞博士名＞备注名），只凭昵称 |
| 串号（口语） | 实际登录的账号 ≠ 槽位该跑的号（切号静默失败的后果）；「防串号」= 每轮身份对台账 | 帧证（大厅/连接页 `ID:` + prefs）＞ 切换行；S5 `u8sdk_cached_uid` vs 身份台账；终极判据 = 干员名册指纹 `tools/mas_account_identity_check.py` | MAS_MASTER_HANDBOOK §3.2（防串号）；GAME_CLIENT_MECHANICS §5（S5）；MAS_EMERGENCY_RUNBOOK §4.3 | 只凭切换行 / 进程在跑就当没串（切号失败是静默的，要帧证）；发现串号还直接发车（先定位） |

## B. 游戏侧 · 资源与货币

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 理智（Sanity） | 游戏体力；上限按号不同（实测 168–210，常见 205/210）；6 分钟回 1 点（≈240/天） | 日志 `理智: N/M`、`开始行动 N~M 次, -K理智`；history JSON 字段 `sanity` / `sanity_full_at`；AP 预算默认 280/天 | MAA_LOG_DICTIONARY §2；DEPOT_PLANNING §3；tools/account_health.py；实测=09-27/28 history 日志样本 | 把「理智作战」消耗当唯一去处（「剩余理智」是另一条通道）；漏算自然回复 |
| 理智溢出（口语） | 理智回满后继续回复 = 白浪费；「谁在拖理智」日报名词 | `sanity_full_at` 已过 ⇒ 已满 hours_full；损失点 = 已满小时 × 10（1 点/6 分钟）；默认阈值 `--waste-hours` 26h | tools/account_health.py；runtime/reports/account_health_*.md §2 | 把「满着没跑」当无事（每天跑一次固有溢出 ≈3.5h 是基线，不告警） |
| 理智药（临期药） | 消耗品 → 补 XX 点理智；**临期药 = 48h 内过期**（窗口 N 天可调） | `UseMedicine` / `MedicineCount`；`UseExpiringMedicine` / `MedicineExpireDays`；日志 `已使用 N 天内过期理智药`、`药: N, 库存: M` | MAA_LOG_DICTIONARY §2；MAA_TASK_CONFIG_REFERENCE §3.2；MAS_RUN_PROFILE §2 | 以为主作战 / 剩余理智 / 剿灭 / 活动四条 merge 链共用一个药额度（**各自独立**，活动关有独立额度） |
| 至纯源石（源石 / 碎石） | 高级货币；「碎石」= 用源石换理智 | `UseStone` / `StoneCount`；日志 `已使用源石`；折算 1 颗源石 ≈ 135 AP | MAA_TASK_CONFIG_REFERENCE §3.2；DEPOT_PLANNING §3 | 顺手把碎石打开（`UseStoneAllowSave=false` 会强制 false）；把源石当合成玉 |
| 合成玉 / 搓玉 | 合成玉 = 抽卡货币；「搓玉」= 口语，指靠长期刷关（1-7 一类）攒素材换合成玉的玩法；本库内体现为**脚本命名**与材料规划三标签之一 | 脚本「明日方舟-官-3-搓玉」；`farm_tag` 三标签「补材料/搓玉/补资源」 | overview.md §3 / §4；MAS_SCRIPTS_CATALOG | 以为 MAA / AUTO-MAS 有「搓玉」任务类型（没有；是命名与分派标签） |
| 龙门币与养成材料 | 龙门币（itemId 4001）主刷 CE-6；另常见芯片 / 芯片组（PR-*）、技巧概要（CA-5） | Fight `DropId` 取值 `item_index.json`；库存保持预设（CE-6 200万 / AP-5 采购凭证 5000 / CA-5 200 / 芯片 20） | MAA_DEPOT_MAINTAIN §4 | 关卡码与物品 ID 混用（`Stage` 写关卡码，`DropId` 写 itemId） |
| 采购凭证（红票） | 凭证交易所「采购凭证区」货币（红票 = 口语） | `RedTicket@Store@*`（源任务 OCR 文本 =「采购凭证区」）；itemId 4006；掉落关 AP-5 | `D:\1-2\_maa_src\resource\tasks\tasks.json`（6.18 源表）；MAA_CAPABILITY_MAP §2.1；MAA_DEPOT_MAINTAIN §4 | 红票 / 绿票 / 黄票三区混用（三区货架与货币都不同） |
| 资质凭证（绿票） | 「资质凭证区」货币（绿票 = 口语） | `GreenTicket@Store@*`（OCR「资质凭证区」） | MAA_UI_LAYOUT（GreenTicket@Store 段）；MAA_EVENT_FARMING_PLAYBOOK §4.3 | 以为绿票商店是日常链自动跑（Mall 链不覆盖三凭证区） |
| 高级凭证（黄票） | 「高级凭证区」货币（黄票 = 口语）；可买寻访凭证 | `YellowTicket@Store@*`（买 `Headhunting_Permit`） | MAA_EVENT_FARMING_PLAYBOOK §4.3；`D:\1-2\_maa_src\resource\tasks\tasks.json` | 同绿票；把黄票当抽卡券本身 |
| 信用（点） | 信用交易所货币；「信用收支」= 日常链 Mall | `MallTask`（`OnlyBuyDiscount` / `ShoppingIgnoreBlackListWhenFull` / `CreditFight` / `VisitFriends`）；日志 `信用不足` 分支 | MAA_TASK_CONFIG_REFERENCE §3.5；MAA_FLOW_LINKS §8 | 以为「信用溢出无视黑名单」自动生效（需显式开启 `ShoppingIgnoreBlackListWhenFull=true`） |

## C. 游戏侧 · 玩法与任务类型

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 日常 / 理智作战 | 每号一天的主链：StartUp → Fight → Infrast → Recruit → Mall → Award（+ 可选肉鸽 / 库存保持） | 链名 `Fight`；日志 `开始任务: 理智作战`；MAA 任务队列顺序 | MAS_MASTER_HANDBOOK §1.3；MAA_DEPOT_MAINTAIN §3 | 把「日常」当单一任务（是一串链）；漏掉剿灭是独立前置轮 |
| 剿灭作战（Annihilation） | 循环刷的合成玉关；**每轮代理的前置模式**（先剿灭后日常）；按周记账 | `Info.Annihilation`（Close / Annihilation / Chernobog@Annihilation / LungmenOutskirts@Annihilation / LungmenDowntown@Annihilation）；`Data.AnnihilationCompletedWeek`；日志 `剿灭模式 X/Y`、`完成任务: 剿灭作战` | MAA_EVENT_FARMING_PLAYBOOK §3；MAA_LOG_DICTIONARY §2 | 把它当独立发车（不是）；忽略「周内完成自动记账、下周自动恢复」；Close = 不跑，却去补 |
| 基建换班 | 基建排班链（换班 / 无人机 / 线索） | `InfrastTask`（`Mode` / `UsesOfDrones` / `DormThreshold`）；日志 `可用干员不足`（单次不致命） | MAA_TASK_CONFIG_REFERENCE §3.3；MAA_LOG_DICTIONARY §2 | 见 `可用干员不足` 就报链失败（单次不算，除非后跟 `任务出错: 基建换班`） |
| 公开招募（公招） | 招募链；消耗「招聘许可」，加速用「加急许可」 | `RecruitTask`（`MaxTimes` / `RefreshLevel3` / `PreserveTagList`…）；日志 `无招聘许可，已返回` = 资源型失败 | MAA_TASK_CONFIG_REFERENCE §3.4；MAA_LOG_DICTIONARY §2；MAS_RERUN_POLICY N1 | 对「无招聘许可」类失败补跑（资源型，补也白补） |
| 信用收支与领取奖励 | 两条日常尾链：Mall（信用购物 + 访问好友）、Award（任务 / 邮件 / 免费一抽 / 挖矿 / 幸运墙 / 月卡 / 签到七开关） | `MallTask`；`AwardTask` 七开关 | MAA_TASK_CONFIG_REFERENCE §3.5 / §3.6 | 把「活动折叠」类奖励漏领当故障 |
| 肉鸽 / 集成战略（Roguelike） | MAA 的 Roguelike 任务；一局 10–30 分钟；机队线另有 fz-maa | `RoguelikeTask`；日志 `Start Task Chain: Roguelike`、`已开始探索 N 次` | MAA_ROGUELIKE_PLAYBOOK；ROGUE_WATCH §1 | 把它排进产线槽（有让位区与互斥）；把「任务已全部完成」当成功（见 D 组判据） |
| 生息演算（Reclamation） | 生息玩法（沙洲遗闻 / 重启锚点；沙中之火已停用）；生产机队未启用 | `ReclamationTask`（`Theme` / `Mode`…）；`Task.IfReclamation` 全 false | MAA_EVENT_FARMING_PLAYBOOK §4.2；MAA_CAPABILITY_MAP §2.1 | 以为 AUTO-MAS 会管它（不管，需另行配置） |
| 保有 / 库存保持（DepotMaintain） | 把指定材料刷到目标库存后自动停；先于理智作战执行；「保有」= 口语 | `DepotMaintainTask`（`PlanList{Stage,DropId,DropCount}` / `UpdateDepot` / `UseAutoSeries`）；`Task.IfDepotMaintain`；仅 `StageMode=Fixed` 会注入 | MAA_DEPOT_MAINTAIN §1–§3；MAA_TASK_CONFIG_REFERENCE §3.8 | 以为 AUTO-MAS 注入的药 / 石对它生效（强制 0）；计划表模式（StageMode≠Fixed）下被强制关闭 |
| 活动 / 复刻 / 资源收集 | SideStory 等活动关；活动结束不改 Stage → 理智作战静默跳过；复刻 = 老活动重开（同 code 两条 stageId） | `Stage` 写活动关码（如 `SR-7`）；`IfActivityFirst` + `ActivityStageIndex`；`FightSkippedNoOpenStage`；`SkipDuringActivity` / `SkipDuringResourceCollection` | MAA_EVENT_FARMING_PLAYBOOK §0.2 / §2；MAA_DEPOT_MAINTAIN §2 | 活动关了不改关卡（临期药事故真因链）；把「关卡计划内没有当日开放的关卡」当系统故障 |
| 代理指挥 / 代理权 | 代理 = 用记录好的作战自动打；「代理权」= 该关是否解锁代理；代理失误 = 战况与记录不符 | `UsePrts` / `无法代理`；日志 `代理指挥失误`、`UnableToAgent(@2)`、`UsePrtsSuccess` | MAA_STATE_MAP（关卡详情页）；DEPOT_PLANNING §6；MAA_LOG_DICTIONARY §2 | 以为所有关都能代理（不能则静默跳过）；把「代理指挥失误」当网络问题 |
| 作业类玩法（抄作业 / 保全派驻 / 悖论模拟） | 抄作业 = Copilot（导入作业 JSON 自动打）；保全派驻 = SSSCopilot；悖论模拟 = ParadoxCopilot（GUI 触发） | `CopilotTask` / `SSSCopilotTask`；本库作业系统另见 JOB_SYSTEM | MAA_CAPABILITY_MAP §2.1；JOB_SYSTEM §一 | 把「抄作业」与本库「作业系统」（AI 生成闭环）混为一谈 |
| 卫戍协议（SPA） | 台服「衛戍協議：盟約」等非刷关型玩法；机队 l-7 待定刷 SPA 还是肉鸽 | 事件表见 `SERVER_EVENTS`（台服行）；ROGUE_WATCH §1 附注 | SERVER_EVENTS §0；ROGUE_WATCH §1 | 在 MAA 活动表里找它（表不含台服盟约） |
| 限时福利（免费一抽 / 签到 / 挖矿 / 幸运墙 / 月卡） | 活动期才存在的界面，走常驻 Award 流程 | `AwardTask.FreeGacha / Mining / Orundum / SpecialAccess / SignInEvent`；`SignInEvent*` / `MiningActivities*` 资源 | MAA_EVENT_FARMING_PLAYBOOK §4.3 | 把「活动限定任务类型」当 MAA 概念（MAA 只有常驻流程 + 活动期界面） |

## D. 游戏侧 · 关卡与刷取

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 关卡代码（code） vs stageId | 写 MAA 配置用 **code**（与游戏 OCR 一致：`1-7` / `SR-7` / `12-17-HARD`）；本仓表用 **stageId**（`main_01-07` / `act54side_07`） | StagePlan 填 code；`data/stage_apcost_v3/v4.json` 存 stageId | MAA_EVENT_FARMING_PLAYBOOK §2 | 用 stageId 写 StagePlan；code 不唯一时不消歧（`10-6` = 普通 / 磨难 / 简化三条） |
| stageId 前缀族 | `main_` / `easy_` / `tough_` / `hard_` / `st_` / `sub_` / `actNNside_*` 等 = 主线普通 / 简化 / 磨难 / 高难 / 剧情 / 支线 / 活动 | `data/stage_apcost_v4.json`；磨难 = `tough_*`（code 与普通同） | MAA_EVENT_FARMING_PLAYBOOK §2 | 把 `12-17-HARD` 当 stageId（表内是 `tough_12-15`，需映射）；把 `#f#` 后缀写进 StagePlan（MAA 不收） |
| 磨难与突袭 | 磨难关（-HARD）需单独解锁；突袭 = 额外难度（表内 `#f#`，AP 同普通） | `12-17-HARD`；`main_01-07#f#`；计划生成默认让位普通关（价差 ≤2%） | MAA_EVENT_FARMING_PLAYBOOK §2；MAS_PLAN_BUILDER §2 | 把 `#f#` 写进配置；以为磨难关与普通关共享代理权 |
| 常驻关与 1-7 | 常驻关 = 永远开放（CE-6 等）；`1-7` 是本库的**兜底尾位**（只在前方候选全不可用时出战） | StagePlan 4 位；尾位恒 `1-7`；常驻关排前会永久阻断后面的关（`PermanentStageBlocksStages`） | MAA_EVENT_FARMING_PLAYBOOK §1 / §5.2；MAS_PLAN_BUILDER §2；MAS_RUN_PROFILE §2 | 把 1-7 放在候选前位（会阻断活动关）；忽略「常驻关阻断」警告 |
| 当日开放与选关 | MAA 选「从上往下第一个**开放**关卡」；活动过期 / 未解锁 → 跳过 | 日志 `GetFightStage: from [...], selected <关>`；`IsStageOpen`；`关卡计划内没有当日开放的关卡` | MAA_EVENT_FARMING_PLAYBOOK §1；MAA_LOG_DICTIONARY §2 | 看到跳过就当失败（是正常跳过）；以为 selected null 可忽略（理智作战整条被跳过） |
| 掉落统计与上报 | 结算掉落解析（drop_statistics）→ 上报企鹅物流 / 一图流 | history JSON `drop_statistics`；`report_to_penguin` / `yituliu`；日志 `掉落识别错误` | MAA_CAPABILITY_MAP §3；MAA_LOG_DICTIONARY §2 | 把 `maa_result=Success!` 当「这关刷到了」（成功率判据看该关出现证据，见「三态」） |

## E. 游戏侧 · 肉鸽专项

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 主题 | 6 个：傀影 / 水月 / 萨米 / 萨卡兹 / 界园 / 黑流树海 | `RoguelikeTask.Theme`（Phantom / Mizuki / Sami / Sarkaz / JieGarden / BlackFlow） | MAA_ROGUELIKE_PLAYBOOK §1–§2 | 写了主题专属字段但主题不对（不生效 ≠ bug，先查主题） |
| 难度 | `2147483647` = MAX、`-1` = 不切换、`0` = MIN、N = 指定；未解锁自动降到当前最高 | `Difficulty` | MAA_ROGUELIKE_PLAYBOOK §1 | 把 `-1` 当「最低难度」（是不切换） |
| 模式 | 练级(Exp) / 投资(Investment) / 刷开局(Collectible) / …；可选列表随主题变 | `Mode`（配置存字符串名，core 传数字） | MAA_ROGUELIKE_PLAYBOOK §1–§2 | 跨主题抄配置（模式编号不同义） |
| 开局设定（分队 / 职业组 / 开局干员 / 助战） | 6.18 `StartingOpers` 最多 3 位；6.17.5 用 `CoreChar` 单干员（机队 = 6.17.5） | `Squad` / `Roles` / `StartingOpers` / `CoreChar` / `UseSupport*` | MAA_ROGUELIKE_PLAYBOOK §1 | 6.17.5 与 6.18 字段混写；干员写昵称 / 英文名（只认中文名） |
| 投资与存款 | 投资 = 花源石锭刷钱；存款满停 | `Investment` / `InvestCount` / `StopWhenDepositFull` | MAA_ROGUELIKE_PLAYBOOK §1 | 把「投资」当金币（投资用源石锭 = 局内货币） |
| 烧水 / 凹开局 | 刷开局期望奖励（热水壶 / 希望等）与凹精二直升 | `CollectibleStartAwards`（`HotWater,Hope`…）；`StartWithEliteTwo*`；`SquadCollectible` | MAA_ROGUELIKE_PLAYBOOK §1 | 在非 Collectible 模式写这些字段；忽略 `StartWithEliteTwoOnly` 语义 |
| 主题专属机制 | Sami 密文板 / 坍缩范式；Mizuki 骰子刷商店（指路鳞）；JieGarden 常乐节点 / 通宝；Sarkaz 蓝图 / 思绪 / 种子；BlackFlow 襁褓动物；月度小队 / 深入调查自动切换 | `SamiFirstFloorFoldartal(s)` / `ExpectedCollapsalParadigms` / `RefreshTraderWithDice` / `FindPlaytimeTarget` / `StartWithSeed` / `MonthlySquad*` / `DeepExplorationAutoIterate` | MAA_ROGUELIKE_PLAYBOOK §1（各节） | 写了不生效就报 bug（先查主题 / 模式匹配） |
| 发一局与连刷 | 「发一局」= `StartCount=1`；连刷 = 99999；一局 10–30 分钟 | `StartCount`；看守 `pendingStart`（6 分钟没起来判「这次发车没起来」） | MAA_ROGUELIKE_PLAYBOOK §1；ROGUE_WATCH §1 / §4 | 把「发一局」发成 99999（停不下来）；把「起不来」当已发车 |

## F. 游戏侧 · 卡池与养成

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 寻访 / 抽卡 | 抽卡入口；每日免费一抽 | `GachaOnce` / `GachaTenTimes`；`AwardTask.FreeGacha`；日志 `GachaNoMoney` | MAA_FLOW_LINKS §8；MAA_EVENT_FARMING_PLAYBOOK §4.3 | 把抽卡当可自动化日常（现网 FreeGacha=false；规则计算见 arknights-community） |
| 当期 UP | 当期概率提升的六星 | 标准寻访 UP 占比按 50% 口径；限定池 70% 组占比 | `arknights-community/drafts/07_up-probability.md` | 把「任意六星」概率当「拿到 UP」概率（两回事，分开算） |
| 歪（口语） | 抽到**非 UP 六星**（吃掉了六星保底） | 保底计数重置口径见 03/07 稿；「歪一次后必 UP」机制**经核验不存在** | arknights-community drafts 03 / 07 | 拿「歪一次必 UP」当机制算期望（不存在）；把歪当概率异常 |
| 保底（软保底） | 标准寻访 50 抽未出六星后下次概率 2%→4%，之后每抽 +2% 直到 100%；计数**跨标准池继承** | 规则出处 = 鹰角概率公示（2019，链接见 07 稿）；限定池结束清零 | arknights-community drafts 03 / 07 | 把保底当「N 抽必出」的固定线；把限定池当继承 |
| 井 / 兑换（口语） | 限定池 300 抽可额外领取 / 数据契约兑换（俗称「井」） | 限定寻访公告（300 次额外领取） | arknights-community drafts 07 | 把井当保底的一部分（是独立机制，单列核算） |
| 池型 | 标准寻访 / 限定寻访（含组占比与结束清零）；两者规则不可互换 | 同保底 / 井条目出处 | arknights-community drafts 07 / 03 | 用标准池模型算限定池 |
| 干员（operator） | 角色；肉鸽开局干员只认中文名 | `OperBox`；`StartingOpers`；`docs/ROSTER_SKILLS_6star.md` 数值表 | MAA_ROGUELIKE_PLAYBOOK §1；ROSTER_SKILLS_6star | 用代号 / 英文名写配置 |
| 练度三件套（精英化 / 专精 / 模组） | E1/E2 = 精一 / 精二；技能专精 8/9/10 = 专一 / 二 / 三；模组 1/2/3；**MAA 读不到专精 / 模组**，靠台账或官方接口 | `skill_level`；`module`；日志证据行「技能等级不足 / 所需模组未解锁」；mastery_scan | MASTERY_LEDGER；ROSTER_SKILLS_6star | 以为 MAA 能看模组（不能，只读界面）；把「能跑但伤害不够」当玄学（多为缺专精 / 模组） |
| 潜能 / 满潜 | 重复干员升潜能；「满潜」= 最高潜能 | OperBox 数据（持有 / 星级 / 精英化 / 等级 / 潜能） | MASTERY_LEDGER | 把潜能当练度门槛（MAA 核练度不看潜能） |
| 干员盒（OperBox） | 上号识别的干员清单产物 | `OperBox` 任务 → `OperBoxInfo` 回调；`runtime/reports/accounts/<账号>/` 下产物 | MAA_CAPABILITY_MAP §2.1 / §3；MAS_PLAN_BUILDER §1 | 把 Depot（仓库）与 OperBox（干员）混用 |

## G. MAA 配置面（字段 / 文件 / 判据）

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| gui.new.json / TaskQueue | 6.x 真配置（`Configurations.Default.TaskQueue[]`）；`gui.json` 是旧壳 | `<MAA目录>/config/gui.new.json`；每轮发车前被 AUTO-MAS 重写、轮末 Temp 还原 | MAA_TASK_CONFIG_REFERENCE §1；MAS_INTERNALS §4 | 手改被覆盖后当「改没生效的 bug」；去 `gui.json` 找任务队列 |
| 任务链（九链） | StartUp / Fight / Recruit / Infrast / Mall / Award / Roguelike / Reclamation / Custom（+ Depot / CloseDown） | 日志 `Start Task Chain: <链>` / `Completed Task Chain: <链>`；`开始任务: <中文名>` | MAA_LOG_DICTIONARY §2；MAA_CAPABILITY_MAP §2.1 | 中文链名对英文链名；漏看 `任务出错: <链名>` |
| 切号字段 | `AccountName`（掩码）+ `AccountSwitchEnabled`；官服 = 掩码 + true、非官 = `""` + false（fail-closed） | StartUpTask 字段；日志 `账号切换 -->> <掩码>`；切号闸门 | MAS_MASTER_HANDBOOK §3.3；tools/mas_switch_gate.py；MAS_ONDEMAND §3 | 手改 true（v2 注入已接管）；以为「有切换行」= 切成功（要帧证） |
| StagePlan | 关卡候选（≤4），从上往下第一个开放者出战 | FightTask.StagePlan（`*` = 当前 / 上次；`-` = 空位） | MAA_TASK_CONFIG_REFERENCE §3.2；MAS_RUN_PROFILE §2 | 写 >4 项；把「第一项」当「总是它」 |
| Series（连战倍数） | 「一组连战几把」的**倍数**，不是总次数；0 = 自动 | `SeriesNumb` → `Series`；域 `0..6 / -1` | MAS_RUN_PROFILE §2 | 把 Series 当「只刷 N 次」 |
| 药与石 | 药额度、碎石上限 | `MedicineNumb` → `UseMedicine` / `MedicineCount`；`UseStone` / `StoneCount`；`UseStoneAllowSave=false` 强关 | MAS_RUN_PROFILE §2；MAA_TASK_CONFIG_REFERENCE §3.2 | 以为 AUTO-MAS 有临期药字段（没有，在 MAA 侧）；碎石被静默关掉 |
| 临期药窗口 | 只吃 N 天内过期的药；活动临期药可拉长 | `UseExpiringMedicine` / `MedicineExpireDays`；`UseExpireMedicineForActivity`；日志 `已使用 N 天内过期理智药` | MAA_EVENT_FARMING_PLAYBOOK §1；MAA_LOG_DICTIONARY §2 | 以为所有通道共用（四条 merge 链各自独立） |
| 次数上限 | 「只刷 N 次」的字面字段；简洁模式被硬设 false | `EnableTimesLimit` / `TimesLimit`；日志 `FightTimesMayNotExhausted` 警告 | MAS_RUN_PROFILE §2；MAA_TASK_CONFIG_REFERENCE §3.2 | 在简洁模式期待「打 N 次」生效（用 Series 近似或改 ConfigFile 模式） |
| 目标掉落 | 按材料刷到量就停（`drops`）；库存目标模式按最新库存重算缺口 | `EnableTargetDrop` / `DropId` / `DropCount` / `IsInventoryTarget`；注入 `drops{itemId:qty}` | MAA_TASK_CONFIG_REFERENCE §3.2；MAA_EVENT_FARMING_PLAYBOOK §1 | 在 AUTO-MAS 两层找该字段（没有；简洁模式硬关） |
| DrGrandet（葛朗台） | 省药抠门模式（留最后 1 点血量用药） | `IsDrGrandet` → `DrGrandet` | MAA_TASK_CONFIG_REFERENCE §3.2 | 把名字当人物功能 |
| PostActions | 轮末电源动作（不关模拟器 = 设计） | AUTO-MAS int：NoAction=8 / ExitEmulator=9（映射未逐项验证）；日志 `Post actions: …` 是**计划**行 | maa-task-config 技能 §4；MAA_LOG_DICTIONARY §2 | 把「跑完停在桌面」当故障（设计如此）；把 Post actions 行当已执行 |
| 任务跳过 vs 任务出错 | `跳过≠失败`（无体力计划 / 关卡无开放 / 锁占用都会跳过）；失败看 `任务出错: <链>` | 日志 `理智作战: … 任务跳过`、`任务出错: …`、`本轮任务出现以下错误：` 清单 | MAA_LOG_DICTIONARY §2 | 把跳过计入失败；把「任务已完成，但出现错误！」当成功 |
| 日志双文件 | gui.log = GUI 侧（链起止 / 切号 / 连接）；asst.log = Core 侧（识别细节 / 回调）；续行无前缀 | `debug/gui.log`、`debug/asst.log`（64MiB 轮转）；判据语言 = GUI 语言（本队 zh-cn） | MAA_LOG_DICTIONARY §1 | 把续行裸行（如 `基建换班`）当独立事件；跨文件起点当轮起点（要时间窗） |
| 识别回调与上报 | Depot / OperBox 识别完成回调；掉落上报企鹅物流 / 一图流 | `DepotInfo` / `OperBoxInfo` 回调；`report_to_penguin` / `yituliu` | DEPOT_PLANNING §2；MAA_CAPABILITY_MAP §3 | 把 `Depot`（识别任务）当「库存保持」（DepotMaintain 会先跑 Depot 再拼 Fight） |

## H. AUTO-MAS 侧（概念 / 机制 / 文件）

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| AUTO-MAS / 侧车 | 生产编排层（v5.4.0），常驻主机 MRFZ-0000；每天 04:01 / 19:01 惯性自重启（计划内，会吞在飞任务） | `E:\AUTO-MAS`；`debug/app.log`；REST :36163 | MAS_MASTER_HANDBOOK §0 / §1.4；MAS_SHIFT_PLAYBOOK §0 | 把「定时没执行」当故障修（24 条定时 Enabled=false 是设计）；在班次窗内重启（孤儿化） |
| 配置三件套 | `ScriptConfig.json`（脚本 / 账号，含 DPAPI 密文）+ `QueueConfig.json`（队列 / 定时）+ `PlanConfig`（计划表） | `E:\AUTO-MAS\config\*.json`；一切配置改动走受控流程 | ACCOUNT_INVENTORY §3；MAS_RUN_PROFILE §1 | 手改 ScriptConfig / QueueConfig（红线⑦：活配置 + 密文）；拿脱敏版当原始版 |
| 队列与定时项 | 有序脚本项列表；定时项 = `TimeSet`（`Enabled` × `Days` × `Time`）；Timer 每秒 tick、**分钟精确匹配无容差**，命中写 `LastTimedStart` 防重 | `QueueConfig.json`；`Data.LastTimedStart`；`TimeEnabled` 总闸 | MAS_INTERNALS §2 | 以为有 ±容差错峰（没有；错峰 = 条目时间本身错开）；错过一分钟 = 该次永久丢失 |
| 计划表与 StageMode | 两种关卡来源：`Fixed`（写用户配置 7 键）或计划表（写该表）；发车前组装二选一 | `Info.StageMode`；`MaaPlanConfig`；`MAA_STAGE_KEY` 7 键 | MAS_RUN_PROFILE §0 / §1 | 不问 StageMode 直接写用户配置（写错层 = 不生效） |
| history / 班次日 / DONE | 逐次尝试记录（含 `maa_result` / `sanity` / `drop_statistics`）；跨零点按班次日归档 | `E:\AUTO-MAS\history\<班次日>\<号>\*.json`；`Success!` = 成功 | MAS_MASTER_HANDBOOK §2.1；ACCOUNT_HEALTH_PROFILES §4 | 把 `Data.LastProxyDate` 当完成度（尝试即写；完成度看 `Info.Tag`） |
| 注入 | 每轮发车前把用户模板写进槽位 `gui.new.json`（简洁 = Default / 详细 = per-uid）；固定序重建 TaskQueue | `data/<sid>/Default` / `ConfigFile`；AutoProxy 写配置路径 | MAA_TASK_CONFIG_REFERENCE §5；maa-task-config 技能 §5 | 以为改了盘上槽位配置就持久（会被下轮覆盖 + 轮末还原） |
| 三模式 | AutoProxy（队列 / 脚本发车）/ ManualReview（只唤醒等应答）/ ScriptConfig（只装配置） | `POST /api/dispatch/start {mode}`；ManualReview 阻塞等客户端应答（没人应答会挂锁） | MAS_ONDEMAND §2；MAS_INTERNALS §1 | `--wake` 不加 `--force`（无客户端在线时白挂）；把 ScriptConfig 模式当发车 |
| 脚本锁 / 已在运行 | 任务准备时 lock、结束 unlock；锁是内存态（重启全消失）；被锁时写配置报 500「配置已锁定」 | `is_locked`；`已在运行`；`配置已锁定, 无法修改`；`跳过任务: … 已被其他任务调度器锁定` | MAS_INTERNALS §1；MAS_RUN_PROFILE §1 | 见「已在运行」当幽灵任务（可能真在跑）；重启后以为锁还在 |
| Temp 快照还原 | 轮末用 `data/<sid>/Temp` 快照整份还原槽位配置——「勾选 / 设置消失」的真凶；还原值 ≠ 最后运行的号 | `data/<sid>/Temp`；manager 还原代码 | MAS_MASTER_HANDBOOK §5.2；MAS_INTERNALS §4 | 把「设置消失」当 MAA bug；拿轮末残留值当「最后跑的号」 |
| 任务结束: \<id\> | 任务收尾判据；只有「任务开始:」没有「任务结束:」= 僵尸 / 孤儿（本轮被重启吃掉） | app.log `任务结束: <task_id>`；配套 `已锁定 / 已解锁脚本配置` | MAS_INTERNALS §1；MAS_MASTER_HANDBOOK §3.5 | 拿非 task_uid 的名字去 stop（stop 只认 task_uid） |
| 电源动作 | 队列 `AfterAccomplish` 决定跑完的动作；`stop ALL` 会清 power_sign（不会误关机） | `Config.power_sign`；POWER_SIGN_MAP | MAS_INTERNALS §1 | 把停止当会关机；误判电源倒计时遗留 |
| 签到与通知 | 森空岛等平台签到；通知渠道（邮件 / ServerChan / Webhook） | `/api/tools/sign`；`push_notification`；`app.log` | MAS_INTERNALS §6 | 把签到结果当任务成功判据 |
| REST :36163 | AUTO-MAS 唯一 API 面（只读端点 + dispatch/start\|stop）；本机工具经它发车 | `http://100.79.173.69:36163`；OpenAPI 副本 `_ams.json` | MAS_MASTER_HANDBOOK 附录 B；HERMES_MAS_PLAYBOOKS §0.3 | 遇到 502 当 AUTO-MAS 挂了（先想系统代理 127.0.0.1:7890） |

## I. 调度与班次（我方）

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 班次 / SHIFT_TABLE | 一天两班：早班 `05:00..05:33`、晚班 `19:30..20:03`（各 12 槽，条目间 +3 分钟错峰）；唯一权威 = `SHIFT_TABLE` | `tools/mas_dispatch.py` SHIFT_TABLE；AUTO-MAS 24 条「日常/晚班定时N」 | MAS_MASTER_HANDBOOK §1.4；MAS_SHIFT_PLAYBOOK §0 | 拿旧快照数字当现状（队列数会变）；手改表不跑 `--check-schedule` |
| 班次日 | AUTO-MAS history 的归档日（≈04:00 界，跨零点归上一班次日）；补跑 / 统计按它 | `history\<班次日>\`；错误聚合窗口 [D 05:00, D+1 05:00) | MAS_RERUN_POLICY §1 | 用自然日切数据；晚班缺口拖到次日 04:00 后（自重启再吞） |
| 发车 | 把队列 / 脚本交给 AUTO-MAS 执行；结果 `SENT / LATE / FAIL` 分类（rest_unreachable / http_error / conflict_inflight / …）；退避 60 / 180 / 300s；告警 `DISPATCH_ALERT=` + 自愈 `CLEARED`；busy 窗 150min 内资源重叠拒发 | `POST /api/dispatch/start {"taskId":…,"mode":"AutoProxy"}`；`runtime/mas_dispatch_{state,log}.jsonl`、`_alerts.json` | MAS_SHIFT_PLAYBOOK §2 / §3；tools/mas_dispatch.py | 以为发车 = 成功（看该号 history 下一条）；绕过闸门直发；`HOLD_MIN` / `GRACE` 当固定值（可调） |
| 对账（reconcile） | cron `*/3 5,19,20`（班次窗内每 3 分钟）对比「到点该发 vs 今天发过」；幂等跳过；`due_not_sent` 下一跳自愈 | `mas_dispatch --reconcile`；state.json；`late_handoff` 标记 | MAS_SHIFT_PLAYBOOK §2 | 手工重复补发（同队列同天只发一次 = 单飞） |
| 单飞 | 同队列同天只发一次（幂等键 = 当天发放记录）；并发锁串行 | `mas_dispatch_state.json`；`runtime/mas_dispatch.lock` | MAS_MASTER_HANDBOOK §5.1；MAS_ONDEMAND §4 | 想「再发一次保险」→ 被拒或重复窗 |
| 守卫窗 | `05:00–05:40 / 19:20–20:20`：不发车、不补跑、不动配置（最严口径） | 巡检提示词；MAS_RERUN_POLICY §3 | MAS_RERUN_POLICY；HERMES_MAS_PLAYBOOKS §0.2 | 把它与「发车点」「让位区」「看板避让窗」混用（五个时间窗名字不同、口径不同） |
| 让位区 | 肉鸽让路窗口 `04:00–05:33 / 18:30–20:30`；肉鸽不发车、产线一回来即收工让路；本质闸门 = 「产线连续 ≥6 分钟无槽在写」 | `tools/lib/rogue_plan.mjs` `inYieldZone`；ROGUE_WATCH §5.25 | ROGUE_WATCH §1 / §5.25；MAA_EVENT_FARMING_PLAYBOOK §5 | 只在时间窗内让位、不看产线实际在跑（本质约束是「同模拟器两 MAA 禁止」） |
| 收工 / 收班 | 班次收尾：连续 2 次 0 MAA ⇒ 日报 + 补跑判定；「收工让路」= 收掉我们的 MAA、不关模拟器 | `mas_watch.py`；ROGUE_WATCH 台账 | MAS_SHIFT_PLAYBOOK §0 / §6；ROGUE_WATCH §1 | 拿「看起来跑完了」当收工（判据 = 0 MAA 连续 2 次） |
| 补跑 vs 补发 | 两个面：**队列级**（mas_dispatch：晚发 / 丢发）与**账号级**（mas_rerun：失败号）；互不替代、互不抢 | `mas_dispatch --queue --apply`；MCP `mas_rerun`；`auto_mas_rerun.py` | HERMES_MAS_PLAYBOOKS §0.1 / §0.4；MAS_RERUN_POLICY §1 | 用补发解决坏号（该走账号级）；同一件事两边各做一遍 |
| 缺口（never_ran） | 被截断（脚本派发过但号无记录）；只补未跑的号；04:00 前收完晚班缺口 | 快照 `never_ran`；队列进度 done<pending；MAS_RERUN_POLICY Y3 | MAS_SHIFT_PLAYBOOK §6；MAS_RERUN_POLICY §1 / §2.3 | 与「材料缺口」（水位−仓库）混用（两个域同名不同义） |
| 临时队列 | `临时-补跑-MMDD_HHMM-N`（历史遗留）/ `临时-按需-*`；跑完即删、残留 <24h 清；误发即抢产线目录 | 队列名；`auto_mas_rerun --cleanup`；`mas_ondemand --cleanup` | MAS_MASTER_HANDBOOK §5.2；MAS_ONDEMAND §3 | 忘了清残留；对残留队列再发车 |
| 在飞 / 吞任务 | 「在飞」= 正在跑的任务；重启侧车会「吞」在飞任务的收尾 / 重试 | `in-flight MAA=N`；app.log banner | MAS_MASTER_HANDBOOK §5.1；MAS_SHIFT_PLAYBOOK | 以为重启对在飞透明 |
| 孤儿化 / 收尾 / 僵尸 | 重启吞在飞任务 ⇒ MAA 照跑完但 AUTO-MAS 无记账 / 无解锁 / 不续跑剩余号；缺「任务结束」「已解锁脚本配置」= 未正常收尾 | app.log；history 无 DONE；Y3 判据 | auto-mas-host-ops 技能（改动纪律）；MAS_RERUN_POLICY §2.3 | 把孤儿当「没跑」（它在跑但没人管）；重启窗内重启（红线②） |
| 互杀 / 撞车 | 同一 MAA 目录并发 = 互杀（按目录路径杀）；同队列同槽位撞车组合见 schedule map；冲突窗 `conflict_inflight` | MAS_SCHEDULE_MAP §4.2；busy 窗；`--force-conflict` 人工放行 | MAS_MASTER_HANDBOOK §5.1 / §5.2；MAS_SCHEDULE_MAP §4.2 | 「就试一下」地并发两个任务 |
| 漂移 | ①ADB 端口漂移（**重建**才漂，重启不漂）②队列定时漂移 / 未关（`--list` 的「漂移 / 未关」栏） | `mas_dispatch --list`；adb 现查 | MAS_MASTER_HANDBOOK §5.2；MAS_EMULATORS | 拿老端口表直接用；把漂移行当噪音 |
| 幽灵 / 残留 | 幽灵 = 不存在的引用（幽灵绑定 / 幽灵占用）；残留 = Temp 快照 / 临时队列 / 模态层等遗留物 | 日志 `幽灵占用` / `幽灵引用`；`残留` 项；`unlock` / Temp 清理记录 | ACCOUNT_HEALTH_PROFILES §2；MAS_MASTER_HANDBOOK §5.2 | 把幽灵引用当活实例；不查残留就发车（残留模态让 StartUp 必败） |
| 空转 / 假死 | 空转 = 跑着但没在干活（MAA 空转自动杀 + 重启）；假死 = 状态面说死实际活（MuMuManager 恒假）/ 侧车假死 | `mas_idle_emulators.py`；巡检 `POWER` 行；F2 故障首条命令 | MAS_MASTER_HANDBOOK §5.2；MAS_EMERGENCY_RUNBOOK | 用状态面判死活（三件套：VMM 进程 → 监听口 → `adb devices`） |

## J. 判据与闸门

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 预检（preflight） | 发车前 GO / NO-GO 六项（REST / 槽位 / 实例 adb / 残留画面 / 注入闸门 / 磁盘） | `PREFLIGHT=GO/NO-GO`；rc 0/1/2；`tools/mas_preflight.py` | MAS_SHIFT_PLAYBOOK §1；tools/mas_preflight.py | 把 WARN 当 NO-GO（WARN 不阻断）；以为没接线（G4 缺口） |
| 切号闸门（switch_gate） | 验**配置层**该切谁：官 = 掩码 + true、非官 = `""` + false；fail-closed | `SWITCH_GATE=OK\|VIOLATION:<槽位>\|UNKNOWN`；`tools/mas_switch_gate.py` | MAS_MASTER_HANDBOOK §3.3；tools/mas_switch_gate.py | VIOLATION 当事故（先看槽位是否在飞）；忘 v2 注入后手改作废 |
| 身份追踪 / 帧证 | 权威阶梯：帧证（大厅 / 连接页 ID + prefs）＞ 切换行（gui.log）＞ 无（黄，需补帧）；切号失败是静默的 | `IDENTITY_TRACE=OK\|RED:n`；巡检 `IDENTITY=一致/不一致`；`tools/mas_identity_trace.py` | MAS_MASTER_HANDBOOK §3.2；GAME_CLIENT_MECHANICS | 拿切换行当「切对了」（MAA 不核对最终身份）；把「没有切换行」当异常（同号重登无行） |
| 巡检（11 行） | 只读巡检固定格式：`MCP_API_TOOL_COUNT\|ISOLATION / SCHEDULE / SWITCH_GATE / CLIENT_STATE / ERR_SUMMARY / DISPATCH_ALERT / IDENTITY / DISK / POWER / WIKI_GOV / 结论`；缺数据写 `UNKNOWN:<原因>` | cron 巡检 job（每 30 分钟）；输出在 Hermes profile cron/output | MAS_MASTER_HANDBOOK §3.5（11 行版）；MAS_SHIFT_PLAYBOOK §5（10 行旧版） | 把 10 行 / 11 行版本混淆；把 deliver=local 当会推送（G11） |
| S1–S6 客户端就绪 | 发车前客户端 6 项：S1 实例活 → S2 版本对齐 → S3 无死态 → S4 目标掩码 ∈ USER_CACHE → S5 `u8sdk_cached_uid` 对台账 → S6 版本一致 | `client_state_check.py`；`USER_CACHE`；`u8sdk_cached_uid` | GAME_CLIENT_MECHANICS §5；MAS_MASTER_HANDBOOK §3.4 | 用 MuMuManager 判 S1；S4 缺失就去发车（切号将静默失败） |
| 死态 | 「登录被取消，请重试」卡死（账号面板触发、不自愈）与强更；都无 MAA 处理器 | 黑名单检测：非黑名单且非纯黑 >60s；处置 `am force-stop` + `am start` | GAME_CLIENT_MECHANICS §2；MAS_RERUN_POLICY §2.2 | 反复重试（不自愈）；把强更当登录问题（人工更新包） |
| ERR_SUMMARY / 三档 | 每日错误聚合一行；三档 = 致命 / 告警 / 可忽略 | `ERR_SUMMARY=<n> 条，Top=<类型>`；`tools/maa_error_daily.py` | MAS_SHIFT_PLAYBOOK §4；tools/maa_error_daily.py | 把「可用干员不足」类告警当致命 |
| C3 三问 | 补跑判定前置：①重跑会好吗 ②已成功吗 ③超上限吗 | MAS_RERUN_POLICY §0；技能「三问」 | MAS_RERUN_POLICY §0；HERMES_MAS_PLAYBOOKS §1 | 跳过三问直接补（资源型白烧） |
| P0–P3（补跑前置体检） | P0 窗口外 / P1 目录空闲 / P2 VMM 在位 / P3 客户端就绪；任一不过 ⇒ 本轮不补 | 判据表 | MAS_RERUN_POLICY §2.1 | 把 P1 当全局互斥（只看目标目录） |
| N1–N4（不该补） | N1 资源型 / N2 客户端未就绪全链崩 / N3 已成功（收尾噪声）/ N4 超上限（钉子户） | 判据表 | MAS_RERUN_POLICY §2.2 | 把收尾噪声（Success 后超时）当真失败补跑 |
| Y1–Y3（该补） | Y1 单链失败非资源 / Y2 桶内无 DONE + 环境可重试 / Y3 截断孤儿（只补未跑的号） | 判据表；每号每班次日 ≤2（自动 ≤1）、≤8/批 | MAS_RERUN_POLICY §2.3 | 把截断当 failed（在 never_ran 里，按 Y3 走） |
| L1/L2/L3（读回） | write 后校验三级：L1 配置读回 / L2 core 参数（asst.log append_task）/ L3 行为锚点（gui.log）；不符 ⇒ `dispatch/stop` 止损 | run_profile 读回断言；`tools/mas_run_profile.py` | MAS_RUN_PROFILE；MAS_RUN_PROFILE_TOOL | 只看 L1 就宣布成功 |
| fail-closed / UNKNOWN / 一票否决 | 读不到就 `UNKNOWN` = 不通过；`MAAORCH_*_NO_GO=1` 一票否决；场景发车双闸（`--apply` + env） | 各工具末行；NO_GO 环境变量 | HERMES_MAS_PLAYBOOKS §0.4；MAS_SCENARIO_DISPATCH | 把 UNKNOWN 当正常；把「重试能过」当方案（先修数据面） |
| 三态（能力台账） | `success / failed / unknown`：success = 关卡出现在成功记录的掉落里；failed = 关卡级失败；unknown = 没证据（含环境失败） | `tools/stage_capability.py`；`can_farm()`；`runtime/reports/stage_capability.json` | MAS_PROBE_FARM §1；tools/stage_capability.py | 把 `maa_result=Success!` 当关卡 success（要看该关出现证据）；unknown 直接拒绝（应试探） |
| 干跑 / 受控写 / 留痕 | 默认干跑（dry-run）；写 = 备份 → 改 → 读回校验 → 留痕 → 可回滚；「停 → 改 → 重启」生效 | 各工具 `--apply`；`.bak-*`；审计 jsonl | HERMES_MAS_PLAYBOOKS §0；maa-task-config 技能 §2 | 没看 diff 就 `--apply`；改运行中的 MAA（先停） |
| 止损 / 急停 | 止损走官方 `dispatch/stop`（unlock + Temp 还原）；急停分级：pause cron 发车 → pause 巡检 → stop 任务；**不知道 task_uid 不要乱停** | `POST /api/dispatch/stop`；cron pause | MAS_EMERGENCY_RUNBOOK；HERMES_MAS_PLAYBOOKS §0.4 | kill MAA 进程（解锁 / 还原不会发生）；无脑 stop ALL |
| 红线 | 10 条动手前默读（只读优先 / 单飞 / 不重启侧车 / 同目录一任务 / 守卫窗 / 补跑≠补发 / l-* 不碰 / 不手改活配置 / 密文不进交付 / 机器证据优先） | MAS_MASTER_HANDBOOK §5.1；HERMES_MAS_PLAYBOOKS §0.4 | 同左 | 把红线当建议条（是硬约束） |

## K. 数据面与台账

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 台账（动作） | 每笔动作的流水（谁发的 / 什么 / 结果） | `runtime/mas_ledger.jsonl`（kind=rerun / probe_farm / …，含 source 卡号） | MAS_MASTER_HANDBOOK §2.4；MAS_PROBE_FARM §2 | 拿台账当成功判据（它是动作记录不是结果） |
| 能力台账 | 账号 × 关卡可刷能力（30 天扫描） | `runtime/reports/stage_capability.json`；`can_farm`；`tools/stage_capability.py` | MAS_PROBE_FARM §1；tools/stage_capability.py | 见「三态」条目 |
| 名册（身份台账） | 60 号 / 54 实例名册 + 掩码↔UID 基线 + 实机证据帧 | `runtime/reports/mas_account_master_*.md/.json` | MAS_MASTER_HANDBOOK 附录 A；ACCOUNT_INVENTORY | 拿快照当永久真值（要现查） |
| 按需账本 | 按需发车统一账本（CLI / MCP 共享；temp_queues 登记；busy_until） | `runtime/mas_ondemand_ledger.json`（schema 见 `tools/mas_ondemand_ledger.py`） | MAS_ONDEMAND；tools/mas_ondemand_ledger.py | 人工删旧账文件（会只读合并 / 隔离） |
| 租约（instance lease） | 「一台实例同一时刻只归一个持有者」的轻量互斥；TTL 默认 30min；过期自动失效 | `runtime/instance_leases.json`；`LEASE=acquired\|renewed\|stolen\|busy\|free\|self\|released`；`tools/mas_instance_lease.py` | tools/mas_instance_lease.py | 拿租约当发车闸门（它是协调清单；裁决仍归 preflight / ondemand） |
| 发车状态三件套 + 报告目录 | state（当天发放）/ log（每次尝试）/ alerts（告警台账） | `runtime/mas_dispatch_state.json`、`mas_dispatch_log.jsonl`、`mas_dispatch_alerts.json`；`runtime/reports/*` | MAS_MASTER_HANDBOOK §2.4 | 手工改 state 消警 |
| 配置快照 | AUTO-MAS 配置定期快照 + 一键回滚 | `tools/mas_config_snapshot.py` | tools/mas_config_snapshot.py | 无快照前提做配置手术 |

## L. 工具面

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| mas_dispatch.py | 班次发车 / 对账 / 一致性 / busy / late | 默认 dry-run；`SENT/LATE/FAIL`；`--check-schedule` | MAS_SHIFT_PLAYBOOK §2；tools/mas_dispatch.py | 拿它做账号级补跑（那是 mas_rerun） |
| 闸门 / 检查类（mas_preflight.py / mas_switch_gate.py / mas_identity_trace.py / client_state_check.py） | 班前 GO/NO-GO 六项 / 切号注入闸门 / 身份留痕 / 客户端状态 | `PREFLIGHT=`（rc 0/1/2）、`SWITCH_GATE=`、`IDENTITY_TRACE=`、`CLIENT_STATE=` | MAS_SHIFT_PLAYBOOK §1；tools/*.py；GAME_CLIENT_MECHANICS | 同「预检」「切号闸门」条目；看一眼就推定（留痕才有证据） |
| maa_error_daily.py | 错误每日聚合（四源） | `ERR_SUMMARY=` | tools/maa_error_daily.py | 拿单条日志当统计 |
| mas_watch.py / auto_mas_rerun.py | 收班日报 + 自动补跑（≤1 次/号）/ 临时队列补跑与清理 | 台账 kind=rerun；`--cleanup` | MAS_RERUN_POLICY §4；tools/*.py | 让自动补跑替代人工判断（上限内才补） |
| mas_ondemand.py | 按需发车（队列 / 脚本 / 单号 / 唤醒 / 停在跑）；MCP 双胞胎 6 工具 | `--apply`；rc 0/2；G0–G5 闸门 | MAS_ONDEMAND | 跳过 `ondemand_preflight` 直接发 |
| mas_instance_lease.py | 实例租约收放 | `LEASE=` | tools/mas_instance_lease.py | 同「租约」条目 |
| mas_run_profile.py | 按 profile 一次发车（8 步；写后读回 + 还原） | rc 0/2/3/4 | MAS_RUN_PROFILE_TOOL | 绕过它手发（失去读回与还原） |
| mas_probe_farm.py | 先试探后开刷（三态决策 → 最小试探 → 三证判定 → 正式刷） | rc 0/2/3/4/5 | MAS_PROBE_FARM | 对 unknown 关直接正式刷 |
| mas_plan_builder.py | 计划草案（缺口 / 候选关 / 练度启发 / 活动）→ run profile JSON | `--apply` 需批准；NO_GO 一票否决；默认零写 | MAS_PLAN_BUILDER | 拿草案当已执行 |
| mas_scenario_dispatch.py | 场景发车草案（肉鸽 / 活动 / 剿灭） | 双闸（`--apply` + env）；草稿标未跑 | MAS_SCENARIO_DISPATCH | 把草稿当真发车 |
| stage_capability.py / account_health.py | 能力台账 / 健康日报（理智溢出 / 成功率 / 健康分） | `HEALTH_SUMMARY=`；`stage_capability.json` | tools/*.py | 同「三态 / 理智溢出」条目 |
| host_health.py | 主机健康巡检（CPU/内存/磁盘/进程/温度 + 趋势 + 预警 一行取数） | `HOST_HEALTH=`；rc 0/2/3/1 | HOST_HEALTH | 把 `temp=na` 当取数失败（主机不暴露 WMI 温度源；na 不告警） |
| rogue_fleet_watch.mjs（+ rogue0_watch.mjs） | 肉鸽机队看守（每 2 分钟；发车 / 救援 / 让位 / 收工） | 台账 wrap-up；`pendingStart` 判据 | ROGUE_WATCH §6 | 拿它管产线（只管机队） |
| 受控写工具组（mas_stage_fix.py / auto_mas_toggle_user.py / mas_timers.py） | 关卡字段模具 / 账号启停 / 定时开关（回滚可用） | dry-run 默认；`--rollback` | HERMES_MAS_PLAYBOOKS §0.3 | 不经它们手改活配置 |
| MCP 工具面（33 工具） | monitor10 / daily6 / dispatch4 / observe5 / triage2 / ondemand6；MCP 会话面 | 改工具面后需「关 → 开」强制重连刷新；`hermes -p mas mcp test` | MCP_TOOL_ROADMAP §10（27→33）；MAS_MASTER_HANDBOOK 附录 B | 改完不重连就断言工具缺失；拿旧「27 工具」当现状 |

## M. 协作与沟通（看板 / 巡检 / 让位）

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 看板 / 卡 | 任务看板（`maa-parallel-benchmark-20260926`）；卡 = 任务（七段模板：目标 / 背景 / 前置 / 步骤 / 产出 / 验收 / 约束） | kanban.db；KANBAN_TASK_TEMPLATE | KANBAN_DISPATCH_POLICY §1 | 「调查一下 XX」式空卡；产出路径不写死 |
| worker / lead | 执行卡片的 profile（如 mas）与建卡 / 拍板者（lead） | 卡的 assignee 字段 | KANBAN_DISPATCH_POLICY | 把 worker 自报当既成事实（要证据） |
| 巡检 job | cron 每 30 分钟只读巡检 + 受闸门写（第 3 步 switch_gate） | `tools/mas_switch_gate.py`；输出末行 `SWITCH_GATE=` | MAS_SHIFT_PLAYBOOK §5 | deliver=local 没人看（G11） |
| 日报 | 收班日报 / 错误日报 / 健康日报（多份、口径不同） | `runtime/reports/*`；`mas_watch.py` | MAS_SHIFT_PLAYBOOK §0 / §6 | 混淆三份日报的口径 |
| 让位协议（_preempt.flag） | 共享设备借还：写 `_device_want.txt`（谁\|多久\|死线\|理由）→ 队列单位边界优雅退出（`==== 让位`）或兜底 tree kill | `runtime/_preempt.flag`、`_preempt_watch.log` | FLEET §2；maaorch-queue-triage 技能 | 删了 flag 就以为自动续跑（要手拉）；信口头承诺不看日志 |
| 席位 / 设备契约 / 复验 | 席位分工（设备 owner / 代码 / 离线 / 独立审计 = 复验出口） | FLEET §1 | FLEET §1 | 跨车道动手 |
| TTL / 死线 | 行动类消息带 `[TTL HH:MM]`；过期一律忽略；阻塞通报三件套「等 X｜阻塞于 Y｜死线 Z」 | FLEET §3 | FLEET §3 | 处理过期旧消息 |

## N. MAAOrch 本地线（本地编排）

| 术语 | 含义 | 对应系统字段 / 文件 / 判据 | 出处 | 常见误用 |
|---|---|---|---|---|
| 调度台 / 队列任务类 | 本机编排（45+ 账号 × 模拟器池 × MAA 实例池）；slot 优先级：维护30 > 刷关20 > 剿灭10 > 肉鸽0；运行中只断肉鸽让位 | dispatch_pool / launch_queue / runner；UI 调度台 | docs/overview.md §3 | 与 AUTO-MAS 线（60 号 / 12 槽）混谈——两条线不同主 |
| 三标签（补材料 / 搓玉 / 补资源） | 材料规划三标签分派（× 活动 / 非活动） | `farm_tag`；farm_dispatch | docs/overview.md §3 / §4 | 把「搓玉」当任务类（是标签） |
| stage_ability / farm_probe | 本机线关卡可刷性缓存（CAN_FARM 永久 / CANNOT-DONE 7 天重测） | `models/accounts/<id>/farm_probe.json` | docs/overview.md §4；DEPOT_PLANNING §6 | 与能力台账（stage_capability）混用（两个系统） |
| 游戏日（04:00 界） | 方舟游戏日 04:00 刷新；统计 / 体力 / 排程全按它对齐 | stats / sanity 04:00 界 | docs/overview.md §3 / §4 | 用自然日对账 |
| 作业系统 | 本库 AI 作业闭环：作业（copilot JSON）→ 修改树 versions（v1_ai / v2_human）→ 关卡逻辑 → 规则库（edits.json）→ 分组 | `data/workspaces/…`、`data/stage_logic/`、`knowledge/edits.json` | JOB_SYSTEM §一 | 与游戏「抄作业」混为一谈 |
| 本地管理器与端口 | manager 常驻 19998（自包含铁律）+ Web 19999（FastAPI）；部署 `deploy.py` → 生产 `E:\MAAOrch` | `/api/health` 五类体检 | docs/overview.md §2 / §6 | 让 manager 依赖项目代码（铁律禁止） |

---

## 附：本表生成与维护

- 生成方式：只读扫描 `docs/`（含 audits / playbooks 引用的工具与数据）、`tools/*.py` 文档串、`services/maa/source` 与 `D:\1-2\_maa_src`（6.18 源）任务表、Hermes `profiles/mas/skills`、`D:\1-2\MAS-KB`、`D:\1-2\arknights-community`，汇总为词条；**未发车、未改任何配置**。
- 数值复核（2026-09-28，卡 `t_8fe7a5a1` 第 2 轮）：关键数字逐项对照来源——理智上限（history 日志实测 168–210；`tools/account_health.py` 基线「约 205」）、AP 预算 280/天、临期药 48h、库存保持预设、源石折 AP 135、班次表 `SHIFT_TABLE`、守卫窗 / 让位区、MCP 工具面 33（含 ondemand6）。
- 需要新增词条时：先找到它的「字段 / 文件 / 判据」锚点再入表；找不到锚点的口语词形如「×（口语）」处理，不许写成系统定义。
- 冲突口径：以 `MAS_MASTER_HANDBOOK` 与现场实测为准；本表只是导航。
