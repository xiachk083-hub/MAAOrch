# MAA 能力全景图（能力 → 配置入口 → 判据 → 可编排性）

> 生成：2026-09-27（kanban t_561f330b · 机制深挖②）。**只读调查**，未改任何生产配置。
> 版本锚点：源码 = `D:\1-2\_maa_src`（MaaAssistantArknights，分支 dev-v2 @ `4d26dd5`，2026-09-19，CHANGELOG 顶部 = v6.18.0-beta.2；`resource/version.json` last_updated 2026-09-17）；装机 = 生产机 **MAA v6.18.0**（现场 gui.log 的 `User-Agent: MaaAssistantArknights/6.18.0` 实证）。两者按同一 6.18 线，字段以源码为准、行为以现场日志为准（版本漂移免责同 MAA_DOCS_MAP.md §3）。
> 证据面：① 源码（MaaCore C++ / MaaWpfGui C#）；② `resource/tasks/**` 资源（1089 任务的主 tasks.json + 82 个子文件）；③ 现场 `E:\MAA-Multi\MAA_6\debug\gui.log` 只读取样。所有行号指 `_maa_src` 内路径。
> 配套：`MAA_TASK_MECHANICS.md` / `MAA_FLOW_LINKS.md` / `MAA_STATE_MAP.md`（869 任务解剖）、`MAA_ROGUELIKE_PLAYBOOK.md`（肉鸽专项）、`MAA_TEMPLATE_MAP.md`、`MAA_ECOSYSTEM.md`、`MAA_LOG_DICTIONARY.md`（日志判据字典，机制深挖③）。本文侧重**能力清单与可编排性**，不重复上述文档的逐任务解剖。

---

## 0. 一句话结论

MAA v6.18.0 的能力面 = **19 种可注入 GUI TaskQueue 的任务类型**（MaaCore C-API 另有仅 GUI 侧入口的 VideoRecognition / ParadoxCopilot；`Debug` 仅调试构建、`OperProgress` 的 C-API 分支被注释）+ **一整套识别器（Vision）** + **四条上报/接口通道（PenguinStats / 一图流 / api.maa.plus / MirrorChyan）**。
对我们的意义：**凡是 TaskQueue 里有的（含肉鸽、生息演算、作业、小游戏、仓库维护、干员识别），我们都能通过「改注入配置 + 发车」编排，不需要碰 MAA GUI；工具箱里的「点击即用」类（视频识别/截图/公招计算）才是 GUI 独占。** 上报表（GameDataReport）另有完整取证价值（gui.log 原样落 request body）。

---

## 1. 三层结构：谁做什么

| 层 | 职责 | 我们能碰的接口 |
|---|---|---|
| **MaaCore**（C++，`src/MaaCore`） | 识别（Vision）+ 任务链执行（Tasks）+ 生成上报请求（ReportRequest 回调）| C-API（`Assistant.cpp`，我们**不用**，B 路线黑盒） |
| **MaaWpfGui**（C#，`src/MaaWpfGui`） | 读配置（gui.new.json）→ 组装任务队列 → 驱动 Core；执行上报 POST；工具箱；更新 | `config/gui.new.json`（配置注入面）；`debug/gui.log`（判据面） |
| **AUTO-MAS**（生产编排层） | 多号轮换、注入配置、按脚本/队列发车、改 MAA 配置、写 history | REST :36163（`/api/dispatch/start` 等）+ `data/<scriptId>/**/ConfigFile` 注入面 |

> 我们既有的编排链：Hermes cron → `tools/mas_dispatch.py` → AUTO-MAS REST → MAA 进程启动读注入配置 → 跑链 →（我们读 gui.log/asst.log 验收）。**本图的一切「可编排性」判定都以这条链为准。**

---

## 2. 任务能力全景

### 2.1 可注入 GUI TaskQueue 的任务类型（配置入口：`config/gui.new.json` → `Configurations.Default.TaskQueue[]`）

来源：`MaaWpfGui/Configuration/Single/MaaTask/BaseTask.cs` L21-40（`[JsonDerivedType]` 白名单，即 TaskQueue 能承载的全部类型；VideoRecognition 被注释掉）。

| `$type`（注入用） | 中文名 | Core 任务类型 | 关键参数（core 侧解析键，出处） | 编排备注 |
|---|---|---|---|---|
| `StartUpTask` | 开始唤醒 | StartUp | `start_game_enabled` / `account_name` / `client_type`（StartUpTask.cpp L87-94；GUI 侧 AsstStartUpTask.cs） | 切号开关 = `AccountName` 非空（唯一注入点，见 v2 口径） |
| `FightTask` | 理智作战 | Fight | `stage` / `medicine` / `medicine_expire_days` / `stone` / `times` / `series` / `DrGrandet` / `drops{itemId:count}` / `report_to_penguin` / `penguin_id` / `report_to_yituliu` / `yituliu_id` / `server` / `client_type`（FightTask.cpp L69-121） | 活动/剿灭/主线都走它；`drops` 即「目标掉落」（StageDropsTaskPlugin L507-515 达成即停） |
| `RecruitTask` | 自动公招 | Recruit | `refresh` / `force_refresh` / `select` / `confirm` / `times` / `set_time` / `expedite` / `preserve_tags` / `extra_tags_mode` / `first_tags` / `recruitment_time{3,4,5,6}` / `report_to_*`（RecruitTask.cpp L42-80；GUI 全字段 AsstRecruitTask.cs） | `times=0` = 仅公招计算（不招募）；五星/六星固定 9:00 |
| `InfrastTask` | 基建换班 | Infrast | `mode` / `drones` / `threshold` / `fiammetta_recovery_enabled`+`fiammetta_targets` / `dorm_notstationed_enabled` / `dorm_trust_enabled` / `use_pinus_sylvestris|perception_information|worldly_plight|abyssal_hunter` / `reception_message_board|clue_exchange|send_clue` / `replenish` / `plan_index` / `rooms[]`（InfrastTask.cpp L72-336） | 排班用 custom_infrast；`可用干员不足` 是告警非致命 |
| `MallTask` | 信用购物 | Mall | `visit_friends` / `shopping` / `buy_first` / `blacklist` / `only_buy_discount` / `reserve_max_credit` / `force_shopping_if_credit_full` / `credit_fight` / `formation_index`（MallTask.cpp L41-121） | 信用战斗=内置一次战斗 |
| `AwardTask` | 领取奖励 | Award | `award` / `mail` / `recruit` / `orundum` / `mining` / `specialaccess` / `signinevent`（AwardTask.cpp L46-52） | 七个开关逐一控制 |
| `CloseDownTask` | 关闭游戏 | CloseDown | `client_type`（CloseDownTask.cpp L18） | 后置动作由 AUTO-MAS 的 PostActions 另外控制，非 MAA 任务 |
| `RoguelikeTask` | 自动肉鸽 | Roguelike | `theme` / `mode` / `starts_count` / `investment_enabled` / `refresh_trader_with_dice` / `stop_at_final_boss` / `start_foldartal_list` + 各插件参数（RoguelikeTask.cpp L254-293） | 逐字段见 `MAA_ROGUELIKE_PLAYBOOK.md`／肉鸽专项 t_a1cf9f5b |
| `ReclamationTask` | 生息演算 | Reclamation | `theme`∈{Tales, RelaunchAnchor}（**Fire 已停用**，ReclamationTask.cpp L39-43）/ `mode` / `difficulty` / `tool_to_craft` / `increment_mode` / `max_craft_count_per_round` / `clear_store`（ReclamationTask.cs L27-49，ReclamationConfig.h L16-36） | 现网默认 Tales+ProsperityInSave |
| `CopilotTask` | 自动抄作业 | Copilot | `filename` **或** `copilot_list[]`（互斥）/ `formation` / `support_unit_usage` / `add_trust` / `ignore_requirements` / `loop_times` / `use_sanity_potion` / `formation_index` / `user_additional[{name,skill,module}]`（CopilotTask.cpp L67-178） | 详见 §5 |
| `SSSCopilotTask` | 保全派驻 | SSSCopilot | `stage_name` 或 `filename`；`loop_times`（SSSCopilotTask.cpp L38-83） | 链节点 `SSSBegin/SSSTeamConfirm/SSSStartFighting` |
| `SingleStepTask` | 单步任务 | SingleStep | `type` / `subtype`（SingleStepTask.cpp L18-19） | 调试/教学向 |
| `DepotTask` | 仓库识别 | Depot | 无参（DepotTask.cpp L10-16） | 结果 = `DepotInfo`（见 §3） |
| `OperBoxTask` | 干员识别 | OperBox | 无参（OperBoxTask.cpp L10-16） | 结果 = `OperBoxInfo`（见 §3） |
| `UserDataUpdateTask` | 更新用户数据 | （GUI 展开） | `update_oper_box` / `update_depot` / 触发间隔（UserDataUpdateTask.cs L28-34） | GUI 自动拼 **Depot+OperBox 两条链**（AsstProxy.cs L1669-1672） |
| `DepotMaintainTask` | 库存保持（仓库维护） | （GUI 展开） | `plan_list[{stage,drop_id,drop_count,use_medicine,medicine_count,use_stone,stone_count}]` / `use_expiring_medicine`(固定 2 天阈值) / `skip_during_activity` / `skip_during_resource_collection` / `only_first_insufficient_plan` / `use_auto_series`（DepotMaintainTask.cs L21-75） | 执行 = 先 Depot 识别，再按缺口逐条拼 **Fight 链**（日志名带 ` #N`，AsstProxy.cs L1663-1686） |
| `OperProgressTask` | 干员增练计划 | OperProgress | `plans`（OperProgressTask.cpp L16-24；Core 侧 AutoRaiseProcessTask） | 在 GUI 白名单内（BaseTask.cs L25），但 **C-API 分支在 dev-v2 被注释（Assistant.cpp L329）** ⇒ 注入后能否真正 append 未验证，落地前先单实例试点 |
| `SwitchThemeTask` | 切换主题 | SwitchTheme | `themes[]`（SwitchThemeTask.cpp L16+） | 界面主题（UiTheme 资源） |
| `CustomTask` | 自定义任务 | Custom | `task_names[]` + `params`（CustomTask.cpp L63-208） | **小游戏总入口**：`MiniGame@SecretFront@Begin`、`MiniGame@MaterialSynthesis@Begin`、`MiniGame@PixelPaint@Begin` 等 |

C-API 层另有 2 个 GUI 未暴露为 TaskQueue 的类型（`Assistant.cpp` L323-344 分支清单）：
- `VideoRecognition`（视频识别 → 生成作业 JSON，参数 `filename`；VideoRecognitionTask.cpp L14-35）——GUI 在工具箱触发；
- `ParadoxCopilot`（悖论模拟作业，AsstParadoxCopilotTask.cs / ParadoxCopilotTask.cpp）——GUI 从 Copilot 页触发；
- （`Debug` 仅 ASST_DEBUG 编译可用；`OperProgress` 的 C-API 分支被注释。）

### 2.2 资源文件规模（`resource/tasks/**`，本次全量解析）

- `tasks.json`：**1089** 个任务（828 个前缀族；其中 ≥2 成员的 32 族）。
- 子目录合计 **2753** 个任务，总计 **3842**。

| 目录 | 文件 | 任务数 | 用途 |
|---|---|---|---|
| `Roguelike/` | base 270 + BlackFlow 281 + Sami 195 + JieGarden 190 + Sarkaz 174 + Mizuki 115 + Phantom 96 + routing 35 | 1356 | 肉鸽主题（`主题@Roguelike@节点` 命名） |
| `RA/` | Reclamation3 214 + Tales 115 + Reclamation2 69 + plugin/base 21 | 419 | 生息演算（重启锚点/沙洲遗闻） |
| `MiniGame/` | 12 文件（PV-Reproduction 37、RebuildingMandate 32、SecretFront 31、SPA 22、MaterialSynthesis 20、…）+ ALL 11 | 216 | 小游戏（经 `CustomTask.task_names` 触发） |
| `Stages/` | 44 个活动文件 + base 15 + Supplies 39 | ~430 | 关卡识别/入口（每活动一个 code 文件） |
| `Copilot/formation.json` | 68 | 编队相关任务 |
| `UiTheme/` | 12 文件 ×17-23 | ~200 | 同一界面在不同主题（UI Theme）下的差异节点 |

（完整前缀族清单与逐任务字段 dump：本次调查产物 `…\profiles\mas\cache\scratch\maa6_task_groups.txt` / `maa6_tasksjson_inventory.txt`。）

### 2.3 任务链子任务族（按前缀归类，重点链）

| 链 | tasks.json 节点族（代表） | 说明 |
|---|---|---|
| **StartUp** | `StartUp`、`StartUpBegin`、`StartUp@ClickCorner`、`StartToWakeUp`、`StartToWakeUpOCR`、`StartUpConnectingFlag`、`StartUpThemes` | 唤醒入口；切号节点族 `SwitchAccount`（3）+ `AccountManager*`（14）+ `AccountCurrentOCR`、`AccountListSwipe*` = 账号管理列表导航 |
| **Fight** | `Fight`、`FightBegin`、`FightMissionFailed(AndStop)`、`StageQueue(+@UsePrts,@CheckPrts,@StartButton1,@GoLastBattle,@CloseStonePage)`、`UsePrts(-Annihilation)`、`Annihilation(+Confirm/SwitchButton/Return/PrtsRecordConfirm)`、`MedicineConfirm`、`UsingMedicine(-SanityMax/-Target)`、`UsingMedicineCount`、`DrGrandetUseOriginiums`、`StageDrops*`（24）、`FightSeries-*`（11） | 关卡导航/开战/吃药吃药确认/连战/掉落识别；`Store`（11）/`GreenTicket`（25）/`RedTicket`/`YellowTicket`/`ChooseDualchip` 等为商店/票据节点 |
| **Recruit** | `Recruit`、`RecruitBegin`、`RecruitTags`、`RecruitNow(-Confirm)`、`RecruitRefresh(-Confirm)`、`RecruitSkip*`、`RecruitTimerH/M`、`RecruitingActivities*`（11 节点，公招活动） | 公招全链；tag 识别即在此链 |
| **Infrast** | `Infrast`、`InfrastBegin`、`Infrast@MainThemes`、Mfg/Trade/Power/Dorm/Control/Office/Processing/Reception 各设施族（`InfrastClueSpeak*`=线索组合、`InfrastAssistant*`=换班助手、`InfrastDrones*`…） | 数百节点；GUI 侧有 10+ 个插件参数（见 2.1） |
| **Mall** | `Mall`、`MallBegin`、`CreditShop-*`（8）、`CreditStoreOcr`、`CreditStoreFailed`、`CreditFight*` | 信用商店 OCR/比价/购买 |
| **Award** | `Award`、`AwardBegin`、`AwardCheckSkip`、`AwardExpandCheckFlow`、`AwardNeedExpand/NotNeedExpand`、`AwardFinished` | 奖励领取（含展开/跳过判定） |
| **CloseDown** | 链级单节点 | 关游戏 |
| **Depot** | `Depot`、`DepotBegin`、`DepotMaterialTab(-Clicked)`、`DepotAllTab`、`DepotQuantity`、`DepotBaseRect`、`DepotMatchData`、`DepotSlowlySwipeToTheRight` | 仓库识别（滚动+匹配 16 节点） |
| **OperBox** | `OperBox`、`OperBoxBegin`、`OperBoxRoleTab*`、`OperBoxSortTabSelect`、`OperBoxNameOCR`、`OperBoxLevelOCR`、`OperBoxPotential1-6`、`OperBoxFlagElite/Role*`、`OperBoxSlowlySwipeToTheRight` | 干员 box 识别（28 节点） |
| **OperProgress** | `OperProgress@*`（96 节点） | 干员练度推进（核心 AutoRaiseProcessTask） |
| **Copilot** | `Copilot@*`（16 节点：`@BattleStartPreFlag`、`@EndOfAction(-Bypass,-Sandbox)`、`@StageDrops-Stars-3/-Adverse`、`@WaitUntilEndOfAction(-Bypass)`、`@ClickCornerUntilStartButton`…） | 作业执行通用战斗节点 |
| **SSS** | `SSSFightStart`（4 节点族）+ `SSS*`（在其它文件） | 保全派驻 |
| **Paradox** | `ParadoxChooseSkill1-3`、`ParadoxReturn*`（8+） | 悖论模拟 |
| **SwitchTheme** | `SwitchTheme`（7）+ `SwitchThemeByName`（13） | 主题切换（UiTheme 资源配合） |
| **MiniGame** | `MiniGame@<Game>@*`（见 2.2；入口 `MiniGame@SecretFront@Begin` / `@MaterialSynthesis@Begin` / `@PixelPaint@Begin`） | 经 CustomTask 触发（CustomTask.cpp L63-208） |
| **VideoRecognition / Custom / SingleStep / Debug** | 少量节点 | 工具箱/自定义/调试 |

---

## 3. 识别（Vision）能力

### 3.1 识别原语（`src/MaaCore/Vision/`）

| 原语 | 文件 | 说明 |
|---|---|---|
| 模板匹配 | `Matcher.cpp` / `MultiMatcher.cpp` / `MaskedCcoeffMatcher.cpp` / `BestMatcher.cpp` | 单/多模板、掩码匹配、最优组合匹配 |
| 特征匹配 | `FeatureMatcher.cpp` | 特征点（用于形变/缩放敏感场景） |
| OCR | `OCRer.cpp` / `RegionOCRer.cpp` / `TemplDetOCRer.cpp` + `OnnxHelper.cpp` | PaddleOCR 引擎：`resource/PaddleOCR/{det,rec}/inference.onnx`（+`keys.txt`）、`resource/PaddleCharOCR/`（字符集模型）；`resource/config.json`+`ocr_config.json` 调参 |
| 专用 ONNX 模型 | `resource/onnx/` | `operators_det.onnx`（干员区域检测）、`skill_ready_cls.onnx`（技能就绪分类）、`deploy_direction_cls.onnx`（部署方向分类）、`BlackFlow_corridor_net.onnx`（黑流树海可通行判定） |

### 3.2 业务识别器 → 用途 → 触发 → 结果

| 识别器 | 识别什么 | 触发链 | 结果/回调（判据） |
|---|---|---|---|
| `RecruitImageAnalyzer`（Vision/Miscellaneous） | 公招 5 个 tag + 星级预测 | Recruit 链 | `RecruitTagsDetected` / `RecruitTagsSelected` / `RecruitResult`（GUI 弹候选）；数据源 `resource/recruitment.json`（RecruitConfig.cpp L34-45） |
| `DepotImageAnalyzer` + `DepotRecognitionTask` | 仓库物品与数量（滚动扫描） | DepotTask / UserDataUpdateTask / DepotMaintainTask | `SubTaskExtraInfo.what=DepotInfo`，`details.done` + `details.data={"itemId":count}`（DepotRecognitionTask.cpp L94-112） |
| `OperBoxImageAnalyzer` + `OperBoxRecognitionTask` | 干员 box（谁有/精英/等级/潜/星级） | OperBoxTask / UserDataUpdateTask | `SubTaskExtraInfo.what=OperBoxInfo`，`all_opers[]`+`own_opers[{id,name,own,elite,level,potential,rarity}]`（OperBoxRecognitionTask.cpp L61-104） |
| `OperFilesImageAnalyzer` / `OperNameAnalyzer`（Vision/Oper） | 干员档案页/名字识别 | OperProgress 等 | 供练度推进使用 |
| `StageDropsImageAnalyzer` | 战斗结算掉落（含星级/次数） | Fight 链每战结束 | `StageDrops` 回调（GUI 记掉落日志）+ 触发上报（§4.2）；`drops` 目标达成即停（StageDropsTaskPlugin.cpp L507-523） |
| `CreditShopImageAnalyzer` | 信用商店商品/价格/折扣 | Mall 链 | `CreditShop-*` 节点（L652+ tasks.json） |
| `BattlefieldClassifier` / `BattlefieldDetector` / `BattlefieldMatcher`（Vision/Battle） | 战斗界面识别（费用/冷却/部署区/敌我） | Fight/Copilot/SSS 战斗过程 | Copilot 自动战斗核心 |
| `BattleFormationAnalyzer` / `SupportListAnalyzer` | 编队界面/助战列表 | 自动编队/助战选择 | Copilot `formation` |
| Roguelike 系列（`Roguelike*ImageAnalyzer`） | 肉鸽招募/编队/技能/商店/事件选项/参数 | Roguelike 链 | 见肉鸽专项文档 |
| Infrast 系列（`InfrastOperImageAnalyzer` 等 5 个） | 设施干员/笑脸/线索 | Infrast 链 | 换班判定 |
| `MaterialSynthesisImageAnalyzer` / `PixelAnalyzer` / `PipelineAnalyzer` | 小游戏（材料合成/像素画/管道） | MiniGame（Custom 触发） | MiniGame 任务集 |

### 3.3 编排结论（识别类）

- **掉落/公招识别无需单独触发**：内建于 Fight / Recruit 链，编排时只要链在跑就有。
- **仓库识别 / 干员 box**：有独立任务类型（`Depot` / `OperBox`），**TaskQueue 可注入** ⇒ 可远程编排（例子：跑一轮 `UserDataUpdateTask` 拿全量 depot/box 快照；或单独 `DepotTask`）。
- **干员 box 的替代路径**：v6.18.0 新增「一图流 OpenAPI 读取练度」（设置 → 三方服务填 token），不连模拟器即可拿**含技能/专精/模组**的数据（CHANGELOG v6.18.0-beta.2；`YituliuApiService.cs` L28-35、L164-180）。
- 识别结果的稳定判据面 = `gui.log`（GUI 把这些回调翻译成中文日志）+ `asst.log`（SubTaskExtraInfo 原文，含 DepotInfo/OperBoxInfo 的完整 JSON）。

---

## 4. 上报 / 接口（第三方 + 官网）

### 4.1 总链路（关键：谁在真正发请求）

```
MaaCore（生成 body） --AsstMsg::ReportRequest（url+headers+body+subtask）--> MaaWpfGui
  AsstProxy.ProcReportRequest（AsstProxy.cs L2831-2895）
    → 过滤（txwy 跳过 Penguin；PC/PlayCover 跳过两者）
    → GameDataReportService.PostWithRetryAsync（GameDataReportService.cs L32-70）
        · 落日志：'Start PostWithRetryAsync, url: …' / 'Request headers: …' / 'Request body: …'  ←★ gui.log 取证金矿
        · Penguin 失败：域内 3 次退避（3s×1.5）+ 备份域 penguin-stats.cn；一图流失败静默
        · 响应头 x-penguin-set-penguinid → 回写 PenguinId 设置（L102-116）
```

- url 常量：core 侧在 `resource/config.json` L31-39（可被资源更新改变）；GUI 侧的域名单：`MaaUrls.cs` L99-104。
- core 侧还有 **server 白名单**：`m_server ∈ {CN,US,JP,KR}` 才上传（StageDropsTaskPlugin.cpp L351-353）。

### 4.2 PenguinStats（企鹅物流）

| 项 | 内容 |
|---|---|
| 端点 | `https://penguin-stats.io/PenguinStats/api/v2/report`（config.json L33）；备份域 `https://penguin-stats.cn`（MaaUrls.cs L100-104） |
| 上传物-掉落 | `server` / `stageId` / `times`（未知则省略）/ `drops[{dropType∈NORMAL_DROP,EXTRA_DROP,FURNITURE,SPECIAL_DROP, itemId, quantity}]` / `source` / `version`（StageDropsTaskPlugin.cpp L377-410） |
| 上传物-公招 | `server` / `stageId="recruit"` / `drops[{dropType:NORMAL_DROP,itemId:<tag>,quantity:1}]` / `source` / `version`（AutoRecruitTask.cpp L1014-1027） |
| 头部 | `authorization: PenguinID <id>`（可选）、`User-Agent: MaaAssistantArknights/<ver>`、`X-Penguin-Idempotency-Key: MAA<hex tick><rand>`（ReportDataTask.cpp L72-89） |
| 触发点 | Fight 链每战 3 星结算后；StageQueue 任务完成；公招每次确认 |
| 前置条件 | 3 星 + 已知 stageId + times 已知，否则报 `SubTaskError{why:NotThreeStars/UnknownStage/UnknownTimes/UnknownDropType/UnknownDrops}`（StageDropsTaskPlugin.cpp L358-404） |
| 开关 | GUI：设置 → 三方服务「上报企鹅物流」（默认 true，ConfigConverter.cs L753）；配置键 `Penguin.EnablePenguin` / `Penguin.Id`（ConfigurationKeys.cs L257-259） |

### 4.3 一图流（yituliu）

| 项 | 内容 |
|---|---|
| 公招上报 | POST `https://backend.yituliu.cn/maa/upload/recruit`（config.json L38）；body = 公招结果 `{level, result[{level, opers[{id,level,name}], tags[]}], server, source, version, uuid}`（AutoRecruitTask.cpp L1057-1075）；`uuid = yituliu_id`（GUI 未提供该设置 ⇒ 现网恒为 `""`） |
| 掉落上报 | POST `.../maa/upload/stageDrop`（config.json L39）；body 同企鹅（server/stageId/times/drops/source/version）（StageDropsTaskPlugin.cpp L472-477） |
| 失败行为 | 静默（仅企鹅域失败会提示/走备份域，GameDataReportService.cs L48-52） |
| 读取侧（v6.18.0 新） | GET `https://backend.yituliu.cn/open-api/operator/info`（`Authorization: <token>`；业务码 200=成功 / 20010=只写 / 20027=token 失效）；返回干员练度（等级/精英/技能/专精/模组），供「干员识别」免模拟器使用（YituliuApiService.cs；设置项 `ThirdParty.YituliuOpenApiToken` / `OperBoxUseYituliuApi`，ThirdParty.cs L30-38） |
| 开关 | `Yituliu.EnableYituliu`（默认 true，ConfigConverter.cs L754） |

### 4.4 MAA 官网 API 与 MirrorChyan

| 通道 | 端点 | 用途 |
|---|---|---|
| api.maa.plus（+api2 备用） | `https://api.maa.plus/MaaAssistantArknights/api/`、`api2.maa.plus`（MaaUrls.cs L51-52） | 公告（`…/announcement/…md`，AnnouncementDialogViewModel.cs L231）、版本信息、adb 包镜像（L59-60）、QQ 群页；MaaApiService 带本地缓存兜底 |
| 作业站 | `https://prts.plus`；`https://prts.maa.plus/copilot/get/<id>`、`/set/get?id=<id>`、`/copilot/rating`（MaaUrls.cs L36-44；CopilotHelper.cs L36/L60/L82） | 作业下载/作业集/评分（§5） |
| MirrorChyan | `https://mirrorchyan.com/api/resources/MAA/latest`（程序）、`/api/resources/MaaResource/latest`（资源）（MaaUrls.cs L89-96） | **更新源二选一：GitHub / MirrorChyan**（UpdateSource.cs；`Update.cs` L39-45 存 CDK 与过期时间；资源与程序两条通道各自可走镜像源，需 CDK） |

### 4.5 「GameDataReport」键语义（本次任务点名）

- **名称来源**：WPF 类 `GameDataReportService`（`Services/Web/GameDataReportService.cs`）——它是 PC 端**所有 MAA 上报的统一出口**（core 只产生 ReportRequest 回调，真正 POST 由它做）；因此 gui.log 里 `Request body: …` 行 ≈「GameDataReport 收到的上报体」。
- 现场实测样本（2026-09-27，`MAA_6\debug\gui.log`，公招一图流，只读取样）：

```
Start PostWithRetryAsync, url: https://backend.yituliu.cn/maa/upload/recruit
Request headers: {}
Request body: {"level":3,"result":[{"level":3,"opers":[{"id":"char_155_tiger","level":5,"name":"因陀罗"}, …],"tags":["生存"]}, …],
               "server":"CN","source":"MaaAssistantArknights","tags":["术师干员","先锋干员","生存","医疗干员","费用回复"],"uuid":"","version":"v6.18.0"}
```

- 键语义逐项：

| 键 | 语义 | 取值来源 |
|---|---|---|
| `uuid` | 第三方站点（一图流）用户标识；关联该用户历史数据 | `yituliu_id`（GUI 无此设置 ⇒ `""`；core Fight 链有一处传的是 penguin_id，见 §6.4） |
| `server` | 游戏服别，决定是否上传 | `"CN"/"US"/"JP"/"KR"`（白名单外一律不上传） |
| `source` | 数据来源标识 | 固定 `"MaaAssistantArknights"`（AsstTypes.h L832 `UploadDataSource`） |
| `tags` | 本次公招识别到的 tag 列表（随客户端语言） | RecruitImageAnalyzer → AutoRecruitTask |
| `version` | MAA 版本 | `MAA_VERSION`（如 `v6.18.0`；DEBUG 版为 `dev`） |
| `level`/`result` | 星级的候选组合与命中干员 | 同上（`result[].opers[].id` 即 `char_*`） |

- **取证价值**：gui.log 原样落 body ⇒ ①「到底传了什么」可审计；② `char_*` 集合可做**多号串号指纹**（已用在 `MAAOrch/tools/mas_account_identity_check.py`：按轮次窗口比对各账号上报的干员集合）；③ 体积/隐私：body 含完整干员名册片段与 tag 记录，日志保留策略见 `MAS_STORAGE_INVENTORY.md`（姊妹任务）。

---

## 5. Copilot（作业）专题

### 5.1 概念与来源（GUI Copilot 页 = `CopilotViewModel.cs`）

作业（Copilot JSON）四种来源：
1. **本地文件**（`*.json`，可拖入/选择，L812-880）；
2. **作业站 ID/URL**（`神秘代码`或 prts.plus 链接 → `PrtsPlusCopilotGet` 下载，L268-283、L1013-1238）；
3. **作业集**（`set/get?id=`，一次拉一组，AsstCopilotTask.MultiTasks 多作业模式）；
4. **视频识别**（工具箱 VideoRecognition → 导出作业 JSON；任务类型 `VideoRecognition`，不在 TaskQueue 白名单）。

### 5.2 落盘与传递

- 落盘目录：`<MAA>/config/copilot/<stageCode>.json`（`CopilotJsonDir = ConfigDir/copilot`，CopilotViewModel.cs L80；重名加毫秒戳 L1650-1655）；网络数据临时文件 `cache/_temp_copilot.json`（L76）。
- 传给 Core 的 `filename` 用**相对路径**（相对 MAA 目录，如 `config/copilot/1-7.json`）。
- `FileName` 与 `MultiTasks` **互斥**（AsstCopilotTask.cs L91-106）。

### 5.3 开关与配置入口

| 项 | 位置 | 说明 |
|---|---|---|
| `Configurations.Default.Copilot` | `Copilot.cs` L34-45 | `TaskList`（作业清单）/ `EnableUserAdditional`+`UserAdditional`（自定干员）/ `SupportMode`（助战策略，默认 WhenNeeded）/ `SelectFormation` / `LoopTimes` |
| 运行前/后脚本 | `RuntimeSettings.PreRunScript` / `PostRunScript`（RuntimeSettings.cs L34-36） | 任意脚本串 |
| **`ExecuteScriptOnCopilot`** | RuntimeSettings.cs L38；配置键 `Start.CopilotWithScript`（ConfigurationKeys.cs L112，旧键迁移 ConfigConverter.cs L749） | **勾选 = 抄作业时也执行前/后脚本** |
| `ExecuteScriptOnManualStop` | RuntimeSettings.cs L40；键 `Start.ManualStopWithScript`（L113） | 手动停止时执行脚本 |
| SSS/悖论 | Copilot 页内按钮（保全派驻走 SSSCopilot；悖论走 ParadoxCopilot） | SSS 另有 `SSSCopilotTask` 可 TaskQueue 注入 |

### 5.4 判据与编排性

- 判据：`gui.log` `Start Task Chain: Copilot` / `Completed Task Chain: Copilot`（AsstProxy.cs L1407/L1463）；SubTask `Copilot@*` 节点；作业站拉取有独立日志（CopilotHelper）。
- **可编排性：✅（我们链路内可行）**——把作业 JSON 预置到 `config/copilot/`（或 `resource/copilot/`）→ 注入 TaskQueue 一条 `CopilotTask{filename}`（或 `copilot_list`）→ 发车。**不需要碰 GUI**。注意 AUTO-MAS 换号时会整包覆盖 ConfigFile，**作业文件要放在不被覆盖的目录**（config/copilot 位于 MAA 目录内，复制/还原的是 `ConfigFile/` 子集，需在本任务落地时实测确认覆盖范围）。

---

## 6. 可编排性结论（Hermes 视角）

### 6.1 三条现实通道（全部已具备）

1. **配置注入**：改 `data/<scriptId>/<uid>/ConfigFile/gui.new.json`（TaskQueue / Gui / Copilot 段）——已有切号/药额度注入先例（v2：Official⇒masked+true；其它⇒""+false）。
2. **发车**：AUTO-MAS REST `/api/dispatch/start {taskId, mode:"AutoProxy"}`（唯真跑链的模式）；队列级/脚本级。
3. **取证**：`<MAA>/debug/gui.log`（链/账号/上报）、`asst.log`（SubTask 原文）、`E:\AUTO-MAS\history`（每轮结果）。

### 6.2 能力 → 编排性判定表

| 能力 | 可远程编排？ | 入口 | 判据 | 备注/风险 |
|---|---|---|---|---|
| 日常套装（唤醒/作战/基建/公招/信用/奖励） | ✅ 已在跑 | TaskQueue 注入 | `Start Task Chain: <名>`；Completed/任务出错 | 54 号现状 |
| 剿灭 | ✅ | `FightTask.stage="Annihilation"`（节点 `UsePrts-Annihilation`/`Annihilation*`） | 同上 | AUTO-MAS 侧有 `AnnihilationStartWeekday/CompletedWeek` 逻辑配合 |
| 活动关/限时刷取 | ✅ | `FightTask.stage=<活动 code>`（code 见 `resource/Stages/*.json`） | 掉落日志 + `StageDrops` | 活动 code 随版本更新资源变化 |
| 肉鸽（含主题/模式/开局自选） | ✅ | `RoguelikeTask` | `Start Task Chain: Roguelike`；肉鸽节点回调 | 全字段见肉鸽专项；占用实例时长需让位 |
| 生息演算 | ✅ | `ReclamationTask{theme:Tales/RelaunchAnchor, mode, …}` | `Start Task Chain: Reclamation`；RA@ 节点 | Fire 已停用 |
| 保全派驻（SSS） | ✅（注入） | `SSSCopilotTask{stage_name|filename}` | `SSSStartFighting` 等节点 | 作业 JSON 需要；GUI 里的「SSS」按钮只是入口 |
| 抄作业（普通关） | ✅（注入+预置 JSON） | `CopilotTask{filename|copilot_list}` + `config/copilot/*.json` | `Start Task Chain: Copilot` | 注意 ConfigFile 覆盖范围与 filename 相对路径 |
| 库存保持（仓库维护） | ✅ | `DepotMaintainTask{plan_list,…}`（GUI 自动展开 Depot+Fight 多链） | 各链 Completed；识别链名带 `(仓库识别)` | 「仅下发第一个缺口计划」可省体力；`UseExpiringMedicine` 固定 2 天 |
| 仓库识别 / 干员识别 | ✅（任务化） | `DepotTask` / `OperBoxTask` / `UserDataUpdateTask`；结果落 asst.log 回调 | `what=DepotInfo/OperBoxInfo` | 也可用一图流 OpenAPI 拿练度（免模拟器） |
| 公招「仅计算」 | ✅ | `RecruitTask{times:0, confirm:[]}` | `RecruitTagsDetected/Result` | 不消耗许可 |
| 小游戏（卫戍协议/材料合成/像素画…） | ✅（半自动） | `CustomTask{task_names:["MiniGame@SecretFront@Begin"]}` | 对应 MiniGame 节点回调 | 需按活动手册挑正确入口名（见 MiniGame/*.json doc 字段） |
| 关卡切换主题 | ✅ | `SwitchThemeTask{themes}` | 节点回调 | 配合 UiTheme 资源 |
| 干员练度推进（OperProgress） | ⚠️ 未验证 | `OperProgressTask{plans}` | `OperProgress@*` | 核心 append 分支被注释（Assistant.cpp L329）⇒ 先单实例试点确认能否 append，再谈编排 |
| 视频识别（生成作业） | ❌ GUI 独占 | 工具箱（C-API 有，但我们无 GUI 入口） | — | 需要人上传视频 |
| 悖论模拟 | ❌ GUI 独占（Copilot 页） | — | — | C-API 有 ParadoxCopilot，但非 TaskQueue |
| 工具箱其它（截图/公招tag面板/小工具） | ❌ GUI 独占 | 工具箱 | — | 需要人（或桌面自动化） |
| 更新（程序/资源，GitHub/MirrorChyan） | ❌ 编排无关 | GUI 设置 | gui.log 更新检查行 | 见噪声清单（NotModified 等） |
| 上报开关/一图流 token | ⚠️ 可注入（ThirdParty 段） | `Gui.ThirdParty.ReportToPenguin/ReportToYituliu/PenguinId/YituliuOpenApiToken/OperBoxUseYituliuApi` | gui.log `Start PostWithRetryAsync` | 默认两者均 true；txwy 自动跳过企鹅；PC 客户端跳过两者 |

### 6.3 GUI 独占清单（明确不能编排）

工具箱面板行为（干员识别面板/仓库识别面板/视频识别/截图工具/公招计算 UI）、公告与更新弹窗交互、Copilot 页的「作业站浏览/一键复制」按钮、悖论模拟启动、设置页全部交互。→ 需要人；若要自动化只能走桌面自动化（computer-use），成本高，暂不做。

### 6.4 风险与注意事项（本次新发现）

1. **`FightTask.cpp` L183**：`set_yituliu_id(penguin_id)` —— Fight 链把 `penguin_id` 当作 yituliu 的 `uuid` 传给上报插件（疑似复制粘贴遗留）。含义：掉落上报的 `uuid` 不等于界面设置里的 yituliu id。改上报审计口径时别被这个坑到（公招链用的是自己的 `yituliu_id`，L79-95）。
2. **上报默认全开**：`ReportToPenguin`/`ReportToYituliu` 默认 true（ThirdParty.cs L24-28 + ConfigConverter L753-754）⇒ 只要号在跑，数据就在传；`source` 恒为 `MaaAssistantArknights`、`version=v6.18.0` 可用来核对″这台机器到底在传什么″。
3. **gui.log 含完整上报体**（含 `char_*` 名册片段）：既是取证金矿也是隐私面 ⇒ 导出/归档日志时按敏感数据处理（同 DSH 侧纪律）。
4. **TaskQueue 类型 ≠ C-API 类型**：GUI 白名单（BaseTask.cs L21-40）才是「注入能用什么」的边界；C-API 独有的 VideoRecognition/ParadoxCopilot/Debug 注入无效。
5. **多链任务的日志名**：DepotMaintain/UserDataUpdate 会在同一 TaskChain 名下拼多条链，日志区分方式见 AsstProxy.cs L1663-1686（` #N` / `(仓库识别)` / `(干员识别)`）——写判据时别把多链当单链。
6. **版本漂移**：以上行号锚定 dev-v2@4d26dd5（≈v6.18.0）。升级后先重跑本任务的只读核对命令（§7）再信任字段。

---

## 7. 复现与溯源（只读）

本机复现命令（源码侧）：

```bash
# 1) 版本锚点
cd /d/1-2/_maa_src && git log -1 --format='%H %ci' && head -3 CHANGELOG.md && cat resource/version.json

# 2) 能力清单重放（本次脚本产物在 scratch）
python C:/Users/xiach/AppData/Local/hermes/profiles/mas/cache/scratch/parse_tasks2.py     # 前缀族 + inventory
python C:/Users/xiach/AppData/Local/hermes/profiles/mas/cache/scratch/group_counts.py     # 各族计数 + 子文件计数

# 3) gui.log 上报取样（生产机，只读）
python C:/Users/xiach/AppData/Local/hermes/profiles/mas/cache/scratch/probe_roster_body.py
```

关键源码索引（`_maa_src` 内路径:行）：

| 主题 | 出处 |
|---|---|
| C-API 任务类型分支 | `src/MaaCore/Assistant.cpp` L315-349 |
| TaskQueue 白名单 | `src/MaaWpfGui/Configuration/Single/MaaTask/BaseTask.cs` L21-40 |
| 各链参数解析 | `src/MaaCore/Task/Interface/{Fight,Recruit,Infrast,Mall,Award,Reclamation,Roguelike,Copilot,SSS,OperProgress,SwitchTheme,SingleStep,VideoRecognition}*.cpp` |
| 上报生成 | `src/MaaCore/Task/ReportDataTask.{h,cpp}`；`Fight/StageDropsTaskPlugin.cpp` L347-477；`Fight/StageQueueMissionCompletedTaskPlugin.cpp` L179-236；`Miscellaneous/AutoRecruitTask.cpp` L997-1075 |
| 上报执行（GUI） | `src/MaaWpfGui/Main/AsstProxy.cs` L2831-2895；`src/MaaWpfGui/Services/Web/GameDataReportService.cs` L32-116 |
| 上报常量 | `resource/config.json` L31-39；`src/MaaCore/Common/AsstTypes.h` L832 |
| 识别器 | `src/MaaCore/Vision/**`；回调样例 `Task/Miscellaneous/{Depot,OperBox}RecognitionTask.cpp` |
| Copilot | `src/MaaWpfGui/ViewModels/UI/CopilotViewModel.cs`（L76-80、L1608-1660、L2010-2090）；`Models/AsstTasks/AsstCopilotTask.cs`；`Configuration/Single/Settings/Copilot.cs`；`Helper/CopilotHelper.cs` |
| 开关/键 | `Configuration/Single/Settings/{RuntimeSettings,ThirdParty}.cs`；`Constants/ConfigurationKeys.cs` L112-113、L257-261；`Helper/ConfigConverter.cs` L746-755 |
| 更新源 | `Constants/MaaUrls.cs` L89-104；`Constants/Enums/UpdateSource.cs`；`Configuration/Global/Update.cs` L39-45 |
| 日志判据 | `Main/AsstProxy.cs` L1407/L1463（Task Chain 行）、L2653（FightTimes）、L2644-2652（Sanity） |

---

*本文为只读调查产物；未修改任何 MAA/AUTO-MAS 配置。姊妹任务：①`MAS_INTERNALS.md`（AUTO-MAS 全链路）③`MAA_LOG_DICTIONARY.md`（日志判据字典，已交付）④`GAME_CLIENT_MECHANICS.md`（客户端侧机制）。*
