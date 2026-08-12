## 区域语义标注（为什么点那里 — 游戏 UI 布局约定）

MAA 的 ROI 是**搜索窗口**（允许按钮微偏移），template 图是**目标本体**；点击点落在按钮区中心。
聚合全部点击点后，方舟 1280×720 界面呈现 5 大固定功能区域：

| 屏幕位置 | 游戏 UI 语义 | 代表任务/模板 | 特征 |
|----------|-------------|---------------|------|
| 左上 (0,0) | **返回/设置区**（全游戏统一） | ReturnButton/Return.png、Settings、FromStageSN | 返回箭头模板双态（Return/Return-White 换肤） |
| 右上 (1120,0) | **HUD 状态区**：战斗标记/理智/跳过动画 | BattleOfficiallyBegin（maskRange 滤浅色）、SanityMatch、ClickCorner 盲区、GachaSkip | 顶角小图标（75×65）+ 盲区点击无害区 |
| 右中 (1120,540) | **关卡详情页按钮区**：代理/扫荡/理智 | UsePrts 系列（全权委托 877,532）、NotUsePrts、OrundumActivitiesConfirm | 灰态（金 0.287）↔ 高亮态（金 0.677）两模板 |
| 右下 (1120,630) | **主确认按钮区** | StartButton1（开始行动 1010,625）、AnnihilationSwitchButton（切换）、AnnihilationConfirm、ReceiveMail | OCR 文字多词任一命中（开始行动/开始作战/开始推演） |
| 中央 (800,450) | **弹窗确认区** | PopupConfirm（ReturnConfirm/OfflineConfirm/RecruitRefreshConfirm 共享） | 同一弹窗模板被 5+ 域复用 |

**规律总结**：
1. 点"那里"因为按钮就在那——ROI 是开发者按游戏固定布局手工标注的镜像
2. 特征分两层：模板图（目标本体的像素特征，如金色高亮=已勾选）+ ROI（搜索窗口的宽松度，通常比按钮大 2-4 倍容 UI 微偏移）
3. 一个 ROI 可能被多个任务复用（如 [5,5] 中央弹窗区 44 个点共享 PopupConfirm）

---

# MAA UI 布局地图（8×8 网格，1280×720 基准）

> 聚合来源：全部任务 roi(487) + specificRect(79) 的点击/识别点
> 每格列出：识别的模板/文字 + 动作 + 代表任务

```
|#|.| | |:|.|:|@|
|.|.| | | | |.|:|
|.|.| | |.|.| |.|
|.|.| |.|:|.|.|.|
|.|:| |:|+|.|:|+|
| | |.|.|:|#|+|:|
| |:|:|.|:|+|+|#|
| | |.|:|:|:|+|#|
```

## [7,0] 区域 (1120,0) — 点击/识别点 53 个
- `ClickChapterNew` ClickSelf roi[991,0,289,170] OCR: 乐章收录 | ClickSelf
- `ClickChapterNewOpenTime` ClickSelf roi[987,0,293,156] OCR: 开启时间 | ClickSelf
- `ClickChapterNewOverview` ClickSelf roi[1175,20,65,40] OCR: 乐章 | ClickSelf
- `PRTS1` DoNothing roi[1165,20,75,65] 模板: BattleOfficiallyBegin.png 模板:BattleOfficiallyBegin.png
- `ClickCorner` ClickRect roi[1000,20,270,50] 直接动作 | ClickRect
- `SanityMatch` DoNothing roi[1120,20,160,40] 模板匹配
- …共 53 个（省略 47）

## [7,7] 区域 (1120,630) — 点击/识别点 51 个
- `AnnihilationSwitchButton` ClickSelf roi[966,577,314,142] OCR: 切换 | ClickSelf
- `StartButton1` ClickSelf roi[1010,625,260,61] OCR: 开始行动/开始作战/开始推演 | ClickSelf
- `StartButton1TryAgain` ClickRect roi[1260,650,1,1] 模板匹配 | ClickRect
- `AnnihilationConfirm` ClickSelf roi[992,584,288,136] 模板匹配 | ClickSelf
- `StageSanityCost` DoNothing roi[1100,660,180,60] 模板匹配
- `ReceiveMail` ClickSelf roi[980,580,300,140] 模板匹配 | ClickSelf
- …共 51 个（省略 45）

## [0,0] 区域 (0,0) — 点击/识别点 49 个
- `Settings` ClickSelf roi[20,15,55,50] 模板匹配 | ClickSelf
- `ReturnButton` ClickSelf roi[0,0,180,80] 模板: Return.png, Return-White.png | ClickSelf 模板:['Return.png', 'Return-White.png']
- `FromStageSN` ClickRect roi[20,20,135,35] 模板: StageSNReturnFlag.png | ClickRect 模板:StageSNReturnFlag.png
- `AnnihilationReturn` ClickSelf roi[0,0,180,80] 模板: Return.png | ClickSelf 模板:Return.png
- `FightSeries-List-Multiply` ClickRect roi[0,-1,75,45] 模板匹配 | ClickRect
- `StageSNReturnFlag` ClickRect roi[20,20,135,35] 模板匹配 | ClickRect
- …共 49 个（省略 43）

## [7,6] 区域 (1120,540) — 点击/识别点 48 个
- `EnterEpisodeNew-Click` ClickSelf roi[1078,595,112,42] OCR: 前往章节 | ClickSelf
- `UsePrts` ClickSelf roi[1000,550,280,90] 模板匹配 | ClickSelf
- `NotUsePrts` ClickSelf roi[1000,550,280,90] 模板匹配 | ClickSelf
- `UsePrts-StageSN` ClickSelf roi[1010,508,270,143] 模板匹配 | ClickSelf
- `OrundumActivitiesChoose` DoNothing roi[1023,504,198,81] OCR: 请选择/选择
- `OrundumActivitiesConfirm` ClickSelf roi[1030,500,200,80] OCR: 确定/确定抽取 | ClickSelf
- …共 48 个（省略 42）

## [5,5] 区域 (800,450) — 点击/识别点 44 个
- `LogoutOCR` DoNothing roi[677,445,440,80] OCR: 退出登录/登出/账号
- `AccountManagerTrashButton` DoNothing roi[790,375,50,175] 模板匹配
- `ReturnConfirm` ClickSelf roi[630,400,650,160] 模板: PopupConfirm.png | ClickSelf 模板:PopupConfirm.png
- `OfflineConfirmAfterBattle` ClickSelf roi[630,400,650,160] 模板: PopupConfirm.png | ClickSelf 模板:PopupConfirm.png
- `RecruitingActivitiesRecruitNowConfirm` ClickSelf roi[586,399,694,192] 模板: RecruitNowConfirm.png | ClickSelf 模板:RecruitNowConfirm.png
- `RecruitRefreshConfirm` ClickSelf roi[630,400,650,160] 模板: PopupConfirm.png | ClickSelf 模板:PopupConfirm.png
- …共 44 个（省略 38）

## [4,4] 区域 (640,360) — 点击/识别点 42 个
- `ClickStageName` ClickSelf roi[0,0,1280,720] OCR:  | ClickSelf
- `ClickStageByTemplate` ClickSelf roi[0,0,1280,720] 模板: empty.png | ClickSelf 模板:['empty.png']
- `AccountManagerLoginButton` ClickSelf roi[423,329,448,147] OCR: 登录 | ClickSelf
- `AccountManagerLoginButtonBili` DoNothing roi[440,360,400,80] 模板匹配
- `AccountManagerTrashButtonBili` DoNothing roi[770,270,50,280] 模板匹配
- `StartToWakeUpOCR` DoNothing roi[373,145,535,430] OCR: 开始唤醒/登录/登
- …共 42 个（省略 36）

## [6,5] 区域 (960,450) — 点击/识别点 42 个
- `UnableToAgent` DoNothing roi[870,410,410,220] 模板匹配
- `ExitOldConfirm` ClickSelf roi[660,450,600,90] 模板匹配 | ClickSelf
- `StartButton2` ClickSelf roi[1000,350,220,340] 模板: StartButton2.png, StartButton2Adverse.png | ClickSelf 模板:['StartButton2.png', 'StartButton2Adverse.png']
- `RecruitNowConfirm` ClickSelf roi[640,450,640,150] 模板匹配 | ClickSelf
- `SpecialAccessActivitiesConfirm` ClickSelf roi[933,495,156,41] OCR: 立即/立即领取 | ClickSelf
- `BattleQuickFormationFilter-Cost` ClickSelf roi[989,433,196,142] OCR: 部署费用 | ClickSelf
- …共 42 个（省略 36）

## [5,6] 区域 (800,540) — 点击/识别点 37 个
- `RecruitingActivitiesConfirm` DoNothing roi[729,515,303,135] OCR: 赠送/次/小时
- `CreditShop-BuyIt` ClickSelf roi[800,500,250,150] 模板: BuyIt.png | ClickSelf 模板:BuyIt.png
- `ReplenishToMaxConfirm` ClickSelf roi[854,504,192,187] 模板匹配 | ClickSelf
- `InfrastSkillsLower` DoNothing roi[400,560,880,80] 模板: empty.png 模板:empty.png
- `InfrastFilterMenuConfirmButton` ClickSelf roi[870,528,152,50] 模板匹配 | ClickSelf
- `GetSelfClue` ClickSelf roi[680,540,250,80] 模板: GetClue1.png | ClickSelf 模板:GetClue1.png
- …共 37 个（省略 31）

## [7,4] 区域 (1120,360) — 点击/识别点 34 个
- `InfrastFlagCombatRecord` DoNothing roi[1000,0,280,720] 模板: InfrastMfgDogFoodFlag.png 模板:InfrastMfgDogFoodFlag.png
- `InfrastFlagPureGold` DoNothing roi[1000,0,280,720] 模板: InfrastMfgPureGoldFlag.png 模板:InfrastMfgPureGoldFlag.png
- `InfrastFlagOriginStone` DoNothing roi[1000,0,280,720] 模板: InfrastMfgOriginStoneFlag.png 模板:InfrastMfgOriginStoneFlag.png
- `InfrastFlagChip` DoNothing roi[1000,0,280,720] 模板: InfrastMfgChipFlag.png 模板:InfrastMfgChipFlag.png
- `SendClues` ClickSelf roi[1160,320,80,90] 模板匹配 | ClickSelf
- `ClueGiveTo3rdConfirm` ClickSelf roi[1150,350,100,160] 模板: SendClueConfirm.png | ClickSelf 模板:SendClueConfirm.png
- …共 34 个（省略 28）

## [6,6] 区域 (960,540) — 点击/识别点 33 个
- `UnableToAgent2` DoNothing roi[985,523,159,165] 模板匹配
- `UsePrts-Annihilation` ClickSelf roi[877,532,183,123] 模板匹配 | ClickSelf
- `UsePrts-AnnihilationSuccess` DoNothing roi[938,527,156,133] 模板匹配
- `FightSeries-Icon` DoNothing roi[927,567,122,66] 模板匹配
- `PRTS3` DoNothing roi[900,500,380,150] OCR: 角色
- `MedicineConfirm` ClickSelf roi[1000,500,200,150] 模板匹配 | ClickSelf
- …共 33 个（省略 27）

## [6,7] 区域 (960,630) — 点击/识别点 32 个
- `EnterChapterDifficultyHard` ClickSelf roi[861,570,330,145] OCR: 进入作战 | ClickSelf
- `InfrastRotationClick` ClickSelf roi[1020,600,150,120] 模板: InfrastQueueRotation.png | ClickSelf 模板:InfrastQueueRotation.png
- `InfrastTrimClick` ClickSelf roi[1020,600,150,120] 模板: InfrastOperatorTrim.png | ClickSelf 模板:InfrastOperatorTrim.png
- `InfrastConfirmButton` ClickRect roi[1108,667,22,22] 直接动作 | ClickRect
- `InfrastDormConfirmButton` ClickRect roi[1108,667,22,22] 直接动作 | ClickRect
- `GetFriendClue` ClickSelf roi[800,600,480,120] 模板: GetClue2.png | ClickSelf 模板:GetClue2.png
- …共 32 个（省略 26）

## [7,1] 区域 (1120,90) — 点击/识别点 31 个
- `CloseAnno` DoNothing roi[1100,0,180,200] 模板匹配
- `CloseAnnoTexas` DoNothing roi[1125,24,138,139] 模板匹配
- `TodaysSupplies` ClickRect roi[1000,1,270,340] OCR: 今日配给 | ClickRect
- `ClickCornerAfterPRTS` ClickRect roi[1260,100,10,10] 直接动作 | ClickRect
- `CreditStoreOcr` ClickSelf roi[1000,0,280,300] OCR: 信用交易所 | ClickSelf
- `CloseCollectCredit` ClickRect roi[1000,1,270,340] 直接动作 | ClickRect
- …共 31 个（省略 25）

## [4,6] 区域 (640,540) — 点击/识别点 31 个
- `BattleOpersFlag` DoNothing roi[35,588,1245,18] 模板匹配
- `SupportList-DetailPanel-Flag` DoNothing roi[677,557,70,85] 模板匹配
- `MiniGame@ALL@GreenGrass@DuelChannel@ChooseMode` ClickSelf roi[228,584,864,58] OCR: 自娱自乐 | ClickSelf
- `MiniGame@PV@04` DoNothing roi[600,580,100,50] 模板匹配
- `MiniGame@PV@09` DoNothing roi[640,520,100,50] 模板匹配
- `Reclamation2Birdland` ClickSelf roi[598,510,262,156] OCR: 聚羽/之地/捕猎区 | ClickSelf
- …共 31 个（省略 25）

## [4,7] 区域 (640,630) — 点击/识别点 30 个
- `ChangeToRaidDifficulty` ClickSelf roi[600,610,130,110] 模板: RaidDifficulty.png, RaidDifficulty-Chapter15.png | ClickSelf 模板:['RaidDifficulty.png', 'RaidDifficulty-Chapter15.png']
- `ChangeToNormalDifficulty` ClickSelf roi[600,610,130,110] 模板: NormalDifficulty.png, NormalDifficulty-Chapter15.png | ClickSelf 模板:['NormalDifficulty.png', 'NormalDifficulty-Chapter15.png']
- `GameStart` ClickSelf roi[550,600,200,120] 模板匹配 | ClickSelf
- `GameStartCheckResourceOCR` DoNothing roi[340,620,600,80] OCR: 资源完整性/不消耗流量
- `GameStartUpdateOCR` DoNothing roi[340,620,600,80] OCR: 正在释放神经递质/MB
- `AnnihilationPrtsRecordConfirm` ClickSelf roi[600,630,100,90] 模板匹配 | ClickSelf
- …共 30 个（省略 24）

## [4,5] 区域 (640,450) — 点击/识别点 30 个
- `Logout` ClickSelf roi[635,435,190,100] 模板匹配 | ClickSelf
- `LogoutConfirm` ClickSelf roi[650,450,280,60] 模板匹配 | ClickSelf
- `LogoutConfirmOCR` ClickSelf roi[650,450,280,60] OCR: 是 | ClickSelf
- `StartToWakeUp` ClickSelf roi[520,460,240,100] 模板匹配 | ClickSelf
- `RecruitFinish` ClickSelf roi[0,300,1280,420] 模板匹配 | ClickSelf
- `StartRecruit` ClickSelf roi[0,300,1280,420] OCR: 开始招 | ClickSelf
- …共 30 个（省略 24）

## [4,0] 区域 (640,0) — 点击/识别点 29 个
- `ClickChapterNewDefaultProgress` ClickSelf roi[695,26,85,38] OCR: 默认进度 | ClickSelf
- `BattleQuickFormationFilter-PinUnactivated` DoNothing roi[725,15,80,50] 模板匹配
- `BattleQuickFormationFilter-PinActivated` ClickSelf roi[725,15,80,50] 模板匹配 | ClickSelf
- `BattleCancelSelection` ClickRect roi[630,0,20,20] 直接动作 | ClickRect
- `BattleHpFlag` DoNothing roi[400,0,650,60] 模板匹配
- `BattleHpFlag2` DoNothing roi[400,0,650,60] 模板匹配
- …共 29 个（省略 23）

## [4,3] 区域 (640,270) — 点击/识别点 29 个
- `SideStoryStage` DoNothing roi[0,60,1280,550] OCR: 
- `AccountCurrentOCRBili` DoNothing roi[407,239,468,109] OCR: 
- `AccountManagerListAccountBili` ClickSelf roi[311,167,662,266] OCR: 上次登录 | ClickSelf
- `LoadingIcon` DoNothing roi[480,210,320,280] 模板匹配
- `InfrastReceptionMessageBoardWithNew` ClickSelf roi[0,150,1280,250] 模板匹配 | ClickSelf
- `InfrastFilterMenuNotStationedButton` ClickSelf roi[300,160,890,300] 模板匹配 | ClickSelf
- …共 29 个（省略 23）

## [7,5] 区域 (1120,450) — 点击/识别点 29 个
- `RecruitingActivities` ClickSelf roi[1148,416,132,138] OCR: 赠送/次 | ClickSelf
- `BattleCostData` DoNothing roi[1199,494,80,44] 模板匹配
- `BattleCostFlag` DoNothing roi[1160,495,45,45] 模板匹配
- `SSSFightStart-PreSelect-Clear` ClickSelf roi[1060,440,120,120] 模板匹配 | ClickSelf
- `MiniGame@PV@06` ClickSelf roi[1070,450,100,50] 模板匹配 | ClickSelf
- `RelaunchAnchor@RA@RA1-Cost` DoNothing roi[1200,490,80,50] OCR: 22+
- …共 29 个（省略 23）

## [3,7] 区域 (480,630) — 点击/识别点 28 个
- `EnterChapterDifficultyNormal` ClickSelf roi[473,572,329,144] OCR: 进入作战 | ClickSelf
- `StageEpisode` ClickSelf roi[487,600,143,120] 模板匹配 | ClickSelf
- `StageResource` ClickSelf roi[462,600,140,120] 模板匹配 | ClickSelf
- `MiningActivitiesEarnRewards` ClickRect roi[600,600,70,70] 直接动作 | ClickRect
- `OrundumActivitiesEarnRewards` ClickRect roi[600,600,70,70] 模板匹配 | ClickRect
- `CloseTaskAward` ClickRect roi[600,630,60,60] 直接动作 | ClickRect
- …共 28 个（省略 22）

## [6,4] 区域 (960,360) — 点击/识别点 26 个
- `Chernobog@AnnihilationStage` DoNothing roi[721,22,529,693] 模板匹配
- `LungmenOutskirts@AnnihilationStage` DoNothing roi[721,22,529,693] 模板匹配
- `LungmenDowntown@AnnihilationStage` DoNothing roi[721,22,529,693] 模板匹配
- `FightSeries-List-Exceeded` DoNothing roi[965,200,70,325] 模板匹配
- `NoStone` DoNothing roi[800,250,350,250] 模板匹配
- `StartToVisit` ClickSelf roi[952,105,267,601] 模板匹配 | ClickSelf
- …共 26 个（省略 20）

## [3,4] 区域 (480,360) — 点击/识别点 25 个
- `SwipeLeftToStage1-7` DoNothing roi[0,120,1080,480] OCR: 1-8/TR-10/1-9
- `SwipeRightToStage1-7` DoNothing roi[0,120,1080,480] OCR: 1-1/1-2/1-3
- `LeidianGoogleFrameworkConfirm` ClickSelf roi[480,410,200,50] 模板匹配 | ClickSelf
- `RecruitTags` DoNothing roi[375,360,480,120] OCR: 
- `InfrastDormMini` DoNothing roi[232,213,690,434] 模板: DormMini.png 模板:DormMini.png
- `InfrastTrainingChooseSkillRec` DoNothing roi[470,80,145,615] 特征点: empty.png 模板:empty.png
- …共 25 个（省略 19）

## [2,6] 区域 (320,540) — 点击/识别点 25 个
- `BiliBiliNoMorePromptsNextTime` ClickSelf roi[200,430,430,260] OCR: 该内容不再展示 | ClickSelf
- `StageSNFlag` DoNothing roi[284,509,242,141] 模板: StageSNReturnFlag.png 模板:StageSNReturnFlag.png
- `InfrastOperLockedMfg` DoNothing roi[200,500,430,220] 模板: OperLockedMfg.png 模板:OperLockedMfg.png
- `InfrastOperLockedTrade` DoNothing roi[200,500,430,220] 模板: OperLockedTrade.png 模板:OperLockedTrade.png
- `InfrastAddOperatorMfgGentle` DoNothing roi[200,500,500,220] 模板: AddOperatorMfgGentle.png 模板:AddOperatorMfgGentle.png
- `InfrastAddOperatorMfgAggressive` DoNothing roi[200,500,500,100] 模板: AddOperatorMfgAggressive.png 模板:AddOperatorMfgAggressive.png
- …共 25 个（省略 19）

## [6,0] 区域 (960,0) — 点击/识别点 25 个
- `CollectCredit` ClickSelf roi[900,0,250,100] 模板匹配 | ClickSelf
- `InfrastEnterTrim` ClickSelf roi[1000,0,150,100] 模板: InfrastEnterTrim.png | ClickSelf 模板:InfrastEnterTrim.png
- `BattleQuickFormation` ClickSelf roi[816,0,404,149] 模板匹配 | ClickSelf
- `BattleQuickFormationFilter` ClickSelf roi[1045,0,134,123] 模板匹配 | ClickSelf
- `BattleSpeedUp` ClickRect roi[1070,20,60,60] 直接动作 | ClickRect
- `BattleSpeedUpCheck` DoNothing roi[1060,20,80,70] 模板匹配
- …共 25 个（省略 19）

## [1,6] 区域 (160,540) — 点击/识别点 23 个
- `DroneCancel` ClickSelf roi[85,498,452,162] 模板匹配 | ClickSelf
- `InfrastTrainingLevel` DoNothing roi[100,500,200,130] 模板: empty.png 模板:empty.png
- `BattleQuickFormationSkillLevel-Base` DoNothing roi[0,365,360,355] 模板匹配
- `MiniGame@SecretFront@ActionContinue` ClickSelf roi[150,500,250,150] 模板匹配 | ClickSelf
- `MiniGame@SecretFront@EventContinue` ClickSelf roi[150,500,250,150] 模板匹配 | ClickSelf
- `RelaunchAnchor@RA@RA4-EW1` DoNothing roi[138,577,100,30] 模板匹配
- …共 23 个（省略 17）

## [5,7] 区域 (800,630) — 点击/识别点 22 个
- `AccountManager` ClickSelf roi[855,660,150,40] 模板匹配 | ClickSelf
- `CloseHomeSanity` ClickSelf roi[750,610,140,70] 模板匹配 | ClickSelf
- `LoadingText` DoNothing roi[700,600,300,120] OCR: 正在提交/反馈至神经
- `RecruitingActivitiesRecruit` ClickSelf roi[802,619,198,61] OCR: 赠送一次/赠送 | ClickSelf
- `RecruitingActivitiesRecruitEnd` DoNothing roi[770,577,267,143] OCR: 寻访一次/寻访
- `InfrastConfirmButtonWait` DoNothing roi[700,640,300,80] OCR: 正在提交/反馈至神经
- …共 22 个（省略 16）

## [1,4] 区域 (160,360) — 点击/识别点 22 个
- `StartUp` DoNothing roi[227,327,159,152] 模板: SwitchTheme@ToggleSettingsMenu.png 模板:SwitchTheme@ToggleSettingsMenu.png
- `DrGrandetUseOriginiums` DoNothing roi[50,376,250,40] OCR: 
- `FightMissionFailed` ClickSelf roi[36,226,482,269] OCR: 任务失败 | ClickSelf
- `FightMissionFailedAndStop` ClickSelf roi[36,226,482,269] OCR: 任务失败 | ClickSelf
- `Fight` DoNothing roi[227,327,159,152] 模板: SwitchTheme@ToggleSettingsMenu.png 模板:SwitchTheme@ToggleSettingsMenu.png
- `Recruit` DoNothing roi[227,327,159,152] 模板: SwitchTheme@ToggleSettingsMenu.png 模板:SwitchTheme@ToggleSettingsMenu.png
- …共 22 个（省略 16）

## [2,7] 区域 (320,630) — 点击/识别点 19 个
- `PRTS2` DoNothing roi[324,579,219,141] OCR: 接管
- `RecruitNoPermit` DoNothing roi[300,600,190,110] 模板匹配
- `InfrastReward` ClickSelf roi[0,600,800,118] OCR: 可收获/订单交付/员信赖 | ClickSelf
- `ShamareThumbnail1` DoNothing roi[240,550,380,170] 模板匹配
- `ShamareThumbnail2` DoNothing roi[240,550,380,170] 模板匹配
- `ShamareThumbnail3` DoNothing roi[240,550,380,170] 模板匹配
- …共 19 个（省略 13）

## [5,4] 区域 (800,360) — 点击/识别点 18 个
- `ExitOld` ClickSelf roi[770,350,190,60] 模板匹配 | ClickSelf
- `FightSeries-Opened` DoNothing roi[900,370,70,40] 模板: FightSeries-Indicator.png 模板:FightSeries-Indicator.png
- `InfrastOfficeMini` DoNothing roi[627,160,653,404] 模板: OfficeMini.png 模板:OfficeMini.png
- `InfrastProcessingMini` DoNothing roi[627,160,653,404] 模板匹配
- `InfrastReceptionMini` DoNothing roi[627,160,653,404] 模板: ReceptionMini.png 模板:ReceptionMini.png
- `InfrastTrainingMini` DoNothing roi[627,160,653,404] 模板: TrainingMini.png 模板:TrainingMini.png
- …共 18 个（省略 12）

## [3,3] 区域 (480,270) — 点击/识别点 18 个
- `AccountCurrentOCR` DoNothing roi[495,335,126,23] OCR: 
- `UsingMedicine-SanityMax` DoNothing roi[440,330,150,55] 模板匹配
- `RecruitTimerDecrement` DoNothing roi[350,250,370,100] 模板匹配
- `DepotMatchData` DoNothing roi[0,0,1066,599] 模板: empty.png 模板:empty.png
- `MiniGame@SecretFront@EventTeam` ClickSelf roi[250,200,500,250] 模板匹配 | ClickSelf
- `RelaunchAnchor@RA@RA1-DeliverWood5` ClickSelf roi[575,281,126,30] OCR: 木材 | ClickSelf
- …共 18 个（省略 12）

## [0,4] 区域 (0,360) — 点击/识别点 18 个
- `FromAnnihilation` ClickRect roi[0,0,90,720] 模板: AnnihilationReturnFlag.png | ClickRect 模板:AnnihilationReturnFlag.png
- `CloseStonePage` ClickRect roi[0,200,300,400] 直接动作 | ClickRect
- `InfrastStationedInfo` ClickSelf roi[0,0,150,720] OCR: 进驻信息 | ClickSelf
- `InfrastFacilityListTabMfg` DoNothing roi[0,0,250,720] 模板: FacilityListTabMfg.png 模板:FacilityListTabMfg.png
- `InfrastFacilityListTabTrade` DoNothing roi[0,0,250,720] 模板: FacilityListTabTrade.png 模板:FacilityListTabTrade.png
- `InfrastAddOperatorReceptionGentle` DoNothing roi[0,0,300,720] 模板: AddOperatorReceptionGentle.png 模板:AddOperatorReceptionGentle.png
- …共 18 个（省略 12）

## [1,0] 区域 (160,0) — 点击/识别点 18 个
- `Mail` ClickSelf roi[120,0,150,135] 模板匹配 | ClickSelf
- `MailWithoutAward` DoNothing roi[120,0,150,135] 模板匹配
- `StageDrops-StageName` DoNothing roi[140,60,200,55] OCR: 
- `ChangeMoneyOrderConfirm` ClickRect roi[0,0,380,140] 直接动作 | ClickRect
- `ChangeSyntheticJadeOrderConfirm` ClickRect roi[0,0,380,140] 直接动作 | ClickRect
- `RoguelikeSkillSelectionName` DoNothing roi[210,8,120,20] OCR: 
- …共 18 个（省略 12）

## [3,5] 区域 (480,450) — 点击/识别点 17 个
- `OfflineConfirm` DoNothing roi[310,409,648,165] 模板匹配
- `OfflineConfirmImpl` ClickSelf roi[310,409,648,165] 模板: OfflineConfirm.png | ClickSelf 模板:OfflineConfirm.png
- `DroneAssistTrade` ClickSelf roi[250,430,460,120] 模板匹配 | ClickSelf
- `InfrastClueVacancy5` DoNothing roi[300,420,500,195] 模板: ClueVacancy5.png 模板:ClueVacancy5.png
- `InfrastClueVacancyNo5` DoNothing roi[300,420,500,195] 模板: ClueVacancyNo5.png 模板:ClueVacancyNo5.png
- `StageDrops-AnnihilationWeeklyLimit` DoNothing roi[530,472,100,20] 模板匹配
- …共 17 个（省略 11）

## [0,2] 区域 (0,180) — 点击/识别点 17 个
- `AwardNeedExpand` ClickSelf roi[0,192,155,115] 模板匹配 | ClickSelf
- `AwardNotNeedExpand` DoNothing roi[0,192,155,115] 模板匹配
- `FriendsList` ClickSelf roi[0,150,250,150] 模板匹配 | ClickSelf
- `BattleOperName` DoNothing roi[5,177,191,37] OCR: 
- `OperBoxFlagElite` DoNothing roi[11,171,24,35] 模板: empty.png 模板:empty.png
- `OperBoxPotential` DoNothing roi[99,194,24,24] 模板: empty.png 模板:empty.png
- …共 17 个（省略 11）

## [6,1] 区域 (960,90) — 点击/识别点 16 个
- `ResourceStageLevel6` DoNothing roi[936,101,243,143] OCR: 
- `CloseAnnoSpecialAccess` DoNothing roi[998,87,143,143] 模板匹配
- `ReceiveAward` ClickSelf roi[950,50,330,150] 模板匹配 | ClickSelf
- `ReceivedAllMail` DoNothing roi[909,33,166,165] 模板匹配
- `VisitLimited` DoNothing roi[900,50,375,140] OCR: 今日参与/已达上限
- `CreditShop-NoMoney` DoNothing roi[940,60,339,110] OCR: 信用不足/无法购买
- …共 16 个（省略 10）

## [2,5] 区域 (320,450) — 点击/识别点 16 个
- `ChipStageLevel1` DoNothing roi[27,260,742,400] OCR: 
- `NotDownloadVoice` DoNothing roi[369,462,152,62] 模板匹配
- `UsingMedicine-Target` DoNothing roi[255,390,315,140] 模板匹配
- `AbandonAction` ClickSelf roi[0,400,650,230] 模板匹配 | ClickSelf
- `InfrastMfg` DoNothing roi[0,200,820,520] 模板: Manufacturing.png 模板:Manufacturing.png
- `InfrastTrade` DoNothing roi[0,200,820,520] 模板: Trade.png 模板:Trade.png
- …共 16 个（省略 10）

## [0,3] 区域 (0,270) — 点击/识别点 16 个
- `EndOfAction` DoNothing roi[0,290,70,60] 模板匹配
- `InfrastProcessingEnterOperList` ClickRect roi[45,180,160,334] 直接动作 | ClickRect
- `InfrastAddOperatorReceptionAggressive` ClickRect roi[30,200,200,300] 直接动作 | ClickRect
- `BattleOperDetailPageFlag` DoNothing roi[0,322,110,16] 模板: empty.png 模板:empty.png
- `StageDrops-Stars-Adverse` DoNothing roi[70,285,90,95] 模板匹配
- `DepotBaseRect` DoNothing roi[0,190,156,190] 模板: empty.png 模板:empty.png
- …共 16 个（省略 10）

## [5,3] 区域 (800,270) — 点击/识别点 15 个
- `AccountManagerListAccount` ClickSelf roi[770,315,85,65] 模板匹配 | ClickSelf
- `UseStone` DoNothing roi[600,130,420,420] 模板匹配
- `InfrastSkillsUpper` DoNothing roi[400,280,880,80] 模板: empty.png 模板:empty.png
- `InfrastOffice` DoNothing roi[378,94,900,529] 模板: Office.png 模板:Office.png
- `InfrastProcessing` DoNothing roi[378,94,900,529] 模板匹配
- `InfrastReception` DoNothing roi[378,94,900,529] 模板: Reception.png 模板:Reception.png
- …共 15 个（省略 9）

## [3,6] 区域 (480,540) — 点击/识别点 15 个
- `StageDrops-StageCF-ItemMatch` DoNothing roi[300,533,600,87] 模板: empty.png 模板:empty.png
- `MiniGame@SecretFront@ClickAnywhere` ClickSelf roi[300,550,600,150] 模板: MiniGame@SecretFront@ClickAnywhere.png | ClickSelf 模板:MiniGame@SecretFront@ClickAnywhere.png
- `MiniGame@SecretFront@EventDetected` DoNothing roi[190,580,850,60] 模板: MiniGame@SecretFront@Available.png 模板:MiniGame@SecretFront@Available.png
- `RelaunchAnchor@RA@RA4-EW4` DoNothing roi[560,577,100,30] 模板匹配
- `RelaunchAnchor@RA@RA15-Fill4` ClickRect roi[470,540,20,20] 直接动作 | ClickRect
- `RelaunchAnchor@RA@RA15-CX4` DoNothing roi[560,577,100,30] 模板匹配
- …共 15 个（省略 9）

## [1,1] 区域 (160,90) — 点击/识别点 14 个
- `ClickChapter` ClickSelf roi[142,43,250,150] OCR:  | ClickSelf
- `InfrastExitReward` ClickRect roi[250,100,50,50] 直接动作 | ClickRect
- `InfrastEnterRotation` ClickSelf roi[120,50,160,160] 模板: InfrastOverviewEnter.png | ClickSelf 模板:InfrastOverviewEnter.png
- `EndOfClueExchange` DoNothing roi[0,0,400,200] 模板匹配
- `MiniGame@PF@Victory` ClickSelf roi[0,58,535,227] 模板匹配 | ClickSelf
- `MiniGame@SecretFront@ClickPrevPage` ClickRect roi[233,150,1,1] 直接动作 | ClickRect
- …共 14 个（省略 8）

## [5,2] 区域 (800,180) — 点击/识别点 14 个
- `ChipStageLevel2` DoNothing roi[472,0,793,502] OCR: 
- `InfrastClueVacancy4` DoNothing roi[650,150,500,195] 模板: ClueVacancy4.png 模板:ClueVacancy4.png
- `InfrastClueVacancyNo4` DoNothing roi[650,150,500,195] 模板: ClueVacancyNo4.png 模板:ClueVacancyNo4.png
- `MiniGame@PV@16` DoNothing roi[890,230,100,50] 模板匹配
- `MiniGame@SPA@EnterDanger` ClickSelf roi[735,40,300,300] 模板匹配 | ClickSelf
- `RoguelikeTraderShoppingOcr` DoNothing roi[406,90,825,284] OCR: 
- …共 14 个（省略 8）

## [4,2] 区域 (640,180) — 点击/识别点 14 个
- `AccountManagerOfficial` DoNothing roi[570,165,140,80] 模板匹配
- `StartWithSanity` DoNothing roi[700,130,140,120] 模板匹配
- `InfrastControlMini` DoNothing roi[553,82,396,248] 模板: ControlCenterMini.png 模板:ControlCenterMini.png
- `InfrastClueVacancy3` DoNothing roi[400,100,500,195] 模板: ClueVacancy3.png 模板:ClueVacancy3.png
- `InfrastClueVacancyNo3` DoNothing roi[400,100,500,195] 模板: ClueVacancyNo3.png 模板:ClueVacancyNo3.png
- `BattleQuickFormation-OperNameFlagBase` DoNothing roi[342,66,768,332] 模板: empty.png 模板:empty.png
- …共 14 个（省略 8）

## [1,2] 区域 (160,180) — 点击/识别点 14 个
- `OrundumActivitiesClick1` ClickRect roi[284,255,25,25] 直接动作 | ClickRect
- `InfrastClueVacancy1` DoNothing roi[0,120,500,195] 模板: ClueVacancy1.png 模板:ClueVacancy1.png
- `InfrastClueVacancyNo1` DoNothing roi[0,120,500,195] 模板: ClueVacancyNo1.png 模板:ClueVacancyNo1.png
- `BattleQuickFormationFilterClose` ClickRect roi[37,104,291,185] 直接动作 | ClickRect
- `GreenTicket@Store@ChooseTicketType` ClickSelf roi[108,163,124,56] OCR: 资质凭证区 | ClickSelf
- `GreenTicket@Store@ClickFirstFloor` ClickSelf roi[263,196,35,73] OCR: 1/T/I | ClickSelf
- …共 14 个（省略 8）

## [6,3] 区域 (960,270) — 点击/识别点 13 个
- `Annihilation` ClickSelf roi[900,70,380,400] 模板: StageAnnihilation.png | ClickSelf 模板:StageAnnihilation.png
- `DroneMax` ClickSelf roi[900,300,120,80] 模板匹配 | ClickSelf
- `ClueGiveTo2nd` DoNothing roi[830,220,340,100] 模板: ClueCanBeSent.png 模板:ClueCanBeSent.png
- `BattleSupportUnitFormation` ClickSelf roi[995,248,244,167] 模板匹配 | ClickSelf
- `SSSHalfTimeDropsConfirm` ClickSelf roi[1035,284,145,145] 模板匹配 | ClickSelf
- `MiniGame@PV@03` DoNothing roi[950,330,100,50] 模板匹配
- …共 13 个（省略 7）

## [1,3] 区域 (160,270) — 点击/识别点 13 个
- `SelectClue` ClickSelf roi[99,222,177,147] 模板匹配 | ClickSelf
- `StageDrops-Stars-3` DoNothing roi[50,270,250,100] 模板匹配
- `StageDrops-Stars-2` DoNothing roi[50,270,250,100] 模板匹配
- `SSSHalfTimeDropsBegin` DoNothing roi[148,332,46,46] 模板: SSSHalfTimeDropsCancel.png 模板:SSSHalfTimeDropsCancel.png
- `SSSHalfTimeDropsCancel` ClickSelf roi[100,284,145,145] 模板匹配 | ClickSelf
- `YellowTicket@Store@ChooseTicketType` ClickSelf roi[108,267,124,47] OCR: 高级凭证区 | ClickSelf
- …共 13 个（省略 7）

## [0,1] 区域 (0,90) — 点击/识别点 12 个
- `EndOfActionAnnihilation` DoNothing roi[50,50,170,180] 模板匹配
- `MiningActivities` ClickSelf roi[0,20,302,302] OCR: 限时开采/开采 | ClickSelf
- `OrundumActivities` ClickSelf roi[64,72,97,156] OCR: 幸运墙/幸运/好运墙 | ClickSelf
- `SpecialAccessActivities` ClickSelf roi[5,21,203,258] OCR: 专享/专享活动/活动推荐 | ClickSelf
- `CreditStoreFailed` DoNothing roi[0,45,216,124] 模板匹配
- `InfrastEnteredFlag` DoNothing roi[0,100,300,150] 模板: InfrastOverview.png 模板:InfrastOverview.png
- …共 12 个（省略 6）

## [7,3] 区域 (1120,270) — 点击/识别点 11 个
- `CheckLevelUp` ClickSelf roi[1000,250,270,200] 模板匹配 | ClickSelf
- `ClueGiveTo2ndConfirm` ClickSelf roi[1150,200,100,160] 模板: SendClueConfirm.png | ClickSelf 模板:SendClueConfirm.png
- `Roguelike@Abandon` ClickSelf roi[1076,243,177,179] 模板: empty.png | ClickSelf 模板:empty.png
- `Roguelike@EnterAfterRecruit` ClickSelf roi[1078,234,202,239] 模板: empty.png | ClickSelf 模板:empty.png
- `Roguelike@OptionChoose2-1` ClickRect roi[1175,255,100,60] 直接动作 | ClickRect
- `Roguelike@OptionChoose4-2` ClickRect roi[1175,260,100,60] 直接动作 | ClickRect
- …共 11 个（省略 5）

## [5,0] 区域 (800,0) — 点击/识别点 11 个
- `WeeklyTask` ClickSelf roi[400,0,880,100] 模板匹配 | ClickSelf
- `InfrastSortByTrustButton` ClickSelf roi[538,6,650,64] 模板匹配 | ClickSelf
- `InfrastSortByTrustButtonClickAgain` ClickSelf roi[538,6,650,64] 模板匹配 | ClickSelf
- `InfrastOperListTabSkillUnClicked` ClickSelf roi[538,6,650,64] 模板匹配 | ClickSelf
- `InfrastOperListTabWorkStatusUnClicked` ClickSelf roi[538,6,650,64] 模板匹配 | ClickSelf
- `InfrastOperListTabMoodDoubleClick` ClickSelf roi[538,6,650,64] 模板匹配 | ClickSelf
- …共 11 个（省略 5）

## [7,2] 区域 (1120,180) — 点击/识别点 11 个
- `VerifyProductChangedToBattleRecord` DoNothing roi[1030,220,190,65] 模板: InfrastMfgDogFoodFlag.png 模板:InfrastMfgDogFoodFlag.png
- `VerifyProductChangedToPureGold` DoNothing roi[1030,220,190,65] 模板: InfrastMfgPureGoldFlag.png 模板:InfrastMfgPureGoldFlag.png
- `VerifyProductChangedToOriginiumShard` DoNothing roi[1030,220,190,65] 模板: InfrastMfgOriginStoneFlag.png 模板:InfrastMfgOriginStoneFlag.png
- `InfrastClueFriendNew` ClickSelf roi[1150,220,130,80] 模板: ClueNew.png | ClickSelf 模板:ClueNew.png
- `MiniGame@RM-1@BattleBegin` DoNothing roi[1165,172,115,151] 模板匹配
- `MiniGame@SPA@Goliath` ClickSelf roi[1048,84,191,196] 模板匹配 | ClickSelf
- …共 11 个（省略 5）

## [0,7] 区域 (0,630) — 点击/识别点 10 个
- `EnterChooseLevelOfDifficulty` ClickSelf roi[0,598,131,122] 模板匹配 | ClickSelf
- `InfrastBottomLeftTab` ClickRect roi[0,719,1,1] 直接动作 | ClickRect
- `InfrastClueQuickSendDuplicates` ClickSelf roi[34,559,154,161] 模板匹配 | ClickSelf
- `BattleSelectFormation1` ClickRect roi[105,662,10,58] 直接动作 | ClickRect
- `BattleQuickFormationSkill3` ClickRect roi[106,650,1,30] 直接动作 | ClickRect
- `SupportList-ClickSupportUnit` ClickRect roi[-1,606,129,50] 直接动作 | ClickRect
- …共 10 个（省略 4）

## [6,2] 区域 (960,180) — 点击/识别点 9 个
- `ResourceStageLevel5` DoNothing roi[850,140,250,220] OCR: 
- `BiliBiliNoMorePromptsNextTimeClose` ClickSelf roi[930,120,150,150] 模板匹配 | ClickSelf
- `MedicineReduceIcon` DoNothing roi[750,200,500,50] 模板匹配
- `ReplenishToMax` ClickSelf roi[882,128,165,149] 模板匹配 | ClickSelf
- `ClickProductMax` ClickSelf roi[920,170,85,60] 模板匹配 | ClickSelf
- `MiniGame@SecretFront@SelectTeam` DoNothing roi[850,160,250,150] 模板: MiniGame@SecretFront@SelectTeam.png 模板:MiniGame@SecretFront@SelectTeam.png
- …共 9 个（省略 3）

## [3,1] 区域 (480,90) — 点击/识别点 9 个
- `AccountManagerBili` DoNothing roi[237,50,771,242] OCR: 登录记录
- `OperBoxFlagRoleTopROI` DoNothing roi[0,78,1145,50] 模板: empty.png 模板:empty.png
- `SSSDropRecruitmentFlag` DoNothing roi[163,78,900,112] OCR: 请选择/请进行/增调
- `RelaunchAnchor@RA@RA15-Fill1` ClickRect roi[470,125,20,20] 直接动作 | ClickRect
- `JieGarden@Roguelike@CoppersListSwipeErrorRecognition` DoNothing roi[397,113,460,60] 模板: JieGarden@Roguelike@CoppersTypeLi.png, JieGarden@Roguelike@CoppersTypeHeng.png, JieGarden@Roguelike@CoppersTypeHua.png 模板:['JieGarden@Roguelike@CoppersTypeLi.png', 'JieGarden@Roguelike@CoppersTypeHeng.png', 'JieGarden@Roguelike@CoppersTypeHua.png']
- `JieGarden@Roguelike@CoppersListSwipeRightMostClick` ClickSelf roi[400,113,450,60] 模板: JieGarden@Roguelike@CoppersTypeLi.png, JieGarden@Roguelike@CoppersTypeHeng.png, JieGarden@Roguelike@CoppersTypeHua.png | ClickSelf 模板:['JieGarden@Roguelike@CoppersTypeLi.png', 'JieGarden@Roguelike@CoppersTypeHeng.png', 'JieGarden@Roguelike@CoppersTypeHua.png']
- …共 9 个（省略 3）

## [2,2] 区域 (320,180) — 点击/识别点 9 个
- `RecruitTimerH` DoNothing roi[410,195,85,60] OCR: 01/02/03
- `ClueSelected` DoNothing roi[361,130,112,225] 模板匹配
- `InfrastTrainingContinue1` ClickSelf roi[310,145,195,185] 模板: InfrastTrainingContinue.png | ClickSelf 模板:InfrastTrainingContinue.png
- `MiniGame@SPA@DecisionClick` ClickRect roi[300,200,50,50] 直接动作 | ClickRect
- `Sarkaz@Roguelike@ChooseBurdenOperatorSelect1` ClickRect roi[330,240,0,0] 直接动作 | ClickRect
- `Sarkaz@Roguelike@ChooseBurdenOperatorSelect2` ClickRect roi[450,240,0,0] 直接动作 | ClickRect
- …共 9 个（省略 3）

## [2,4] 区域 (320,360) — 点击/识别点 9 个
- `DeliverableOrder` ClickSelf roi[250,300,230,250] 模板匹配 | ClickSelf
- `InfrastMfgMini` DoNothing roi[0,240,640,400] 模板: ManufacturingMini.png 模板:ManufacturingMini.png
- `InfrastTradeMini` DoNothing roi[0,240,640,400] 模板: TradeMini.png 模板:TradeMini.png
- `InfrastPowerMini` DoNothing roi[0,240,640,400] 模板: PowerMini.png 模板:PowerMini.png
- `InfrastTrainingContinue2` DoNothing roi[310,350,195,185] 模板: InfrastTrainingContinue.png 模板:InfrastTrainingContinue.png
- `MiniGame@SecretFront@ClickThenActionsDetected` ClickRect roi[363,426,1,1] 模板: MiniGame@SecretFront@RouteSelect.png | ClickRect 模板:MiniGame@SecretFront@RouteSelect.png
- …共 9 个（省略 3）

## [4,1] 区域 (640,90) — 点击/识别点 8 个
- `UseMedicine` DoNothing roi[580,50,400,100] 模板匹配
- `NoFriends` DoNothing roi[600,100,350,150] OCR: 暂无好友
- `InfrastReceptionMessageBoard-AccessRecord` ClickSelf roi[520,50,240,140] 模板匹配 | ClickSelf
- `SSSClickUntilStartScreen` ClickRect roi[529,27,227,126] 直接动作 | ClickRect
- `GachaLastTenTimesResult1` ClickSelf roi[0,145,1280,42] 模板匹配 | ClickSelf
- `GachaLastTenTimesResult2` ClickSelf roi[508,66,267,172] OCR: 获得物资 | ClickSelf
- …共 8 个（省略 2）

## [2,3] 区域 (320,270) — 点击/识别点 8 个
- `InfrastClueVacancy2` DoNothing roi[200,195,500,195] 模板: ClueVacancy2.png 模板:ClueVacancy2.png
- `InfrastClueVacancyNo2` DoNothing roi[200,195,500,195] 模板: ClueVacancyNo2.png 模板:ClueVacancyNo2.png
- `ChooseBattleRecord` ClickSelf roi[250,270,165,165] 模板匹配 | ClickSelf
- `MiniGame@RM-TR-1@BattleFailedClick` ClickSelf roi[228,246,329,218] 模板: MiniGame@RM-1@BattleFailedClick.png | ClickSelf 模板:MiniGame@RM-1@BattleFailedClick.png
- `MiniGame@RM-1@BattleFailedClick` ClickSelf roi[228,246,329,218] 模板匹配 | ClickSelf
- `Roguelike@StageVertical` ClickSelf roi[0,53,680,554] 模板: empty.png | ClickSelf 模板:empty.png
- …共 8 个（省略 2）

## [2,1] 区域 (320,90) — 点击/识别点 8 个
- `BackToReceptionMain` ClickRect roi[280,96,130,32] 模板: ReceptionFlag.png | ClickRect 模板:ReceptionFlag.png
- `ChoosePureGold` ClickSelf roi[250,90,165,165] 模板匹配 | ClickSelf
- `ChooseOriginiumShard` ClickSelf roi[250,90,165,165] 模板匹配 | ClickSelf
- `MiniGame@PF@PF-1` ClickSelf roi[373,94,152,141] 模板匹配 | ClickSelf
- `RelaunchAnchor@RA@RA1-CommunicationStation` ClickSelf roi[374,159,106,34] OCR: 通讯基站 | ClickSelf
- `Roguelike@StageTraderRefreshWithDice` ClickSelf roi[230,47,211,182] 模板: Roguelike@StageTraderRefresh.png | ClickSelf 模板:Roguelike@StageTraderRefresh.png
- …共 8 个（省略 2）

## [1,7] 区域 (160,630) — 点击/识别点 7 个
- `StageTheme` ClickSelf roi[243,598,147,122] 模板匹配 | ClickSelf
- `LevelOfDifficulty` ClickSelf roi[26,565,302,155] OCR: 环境/PRTS | ClickSelf
- `StartUpConnectingFlag` ClickSelf roi[0,635,450,85] 模板匹配 | ClickSelf
- `InfrastTrainingTime` DoNothing roi[150,645,100,40] 模板匹配
- `MiniGame@RM-TR-1@SettlementStageName` ClickSelf roi[0,550,600,170] 模板匹配 | ClickSelf
- `MiniGame@RM-1@SettlementStageName` ClickSelf roi[0,550,600,170] 模板匹配 | ClickSelf
- …共 7 个（省略 1）

## [5,1] 区域 (800,90) — 点击/识别点 6 个
- `ClickedCorrectStage` DoNothing roi[845,72,220,50] OCR: 
- `InfrastControl` DoNothing roi[400,0,880,300] 模板: ControlCenter.png 模板:ControlCenter.png
- `Store@EnterTicketStore` ClickSelf roi[730,38,215,134] OCR: 凭证交易所 | ClickSelf
- `MiniGame@PV@Options-1` ClickRect roi[866,143,16,16] 直接动作 | ClickRect
- `MiniGame@SPA@EnterStandard` ClickSelf roi[735,40,300,140] 模板匹配 | ClickSelf
- `RelaunchAnchor@RA@RA15-Fill5` ClickRect roi[840,125,20,20] 直接动作 | ClickRect

## [0,6] 区域 (0,540) — 点击/识别点 6 个
- `InfrastTrainingSkillImg` DoNothing roi[28,558,106,108] 模板: empty.png 模板:empty.png
- `StageDrops-NoDropCheck` DoNothing roi[60,510,150,160] 模板: empty.png 模板:empty.png
- `BattleQuickFormationSkill2` ClickRect roi[106,560,1,30] 直接动作 | ClickRect
- `BattleQuickFormationModule-Item` ClickSelf roi[5,370,80,350] 模板: empty.png | ClickSelf 模板:empty.png
- `Sarkaz@Roguelike@ChooseBurdenOperator` ClickSelf roi[11,493,140,143] 模板匹配 | ClickSelf
- `PVChapterToPV` ClickSelf roi[69,594,123,40] OCR: 四幕汇演 | ClickSelf

## [3,0] 区域 (480,0) — 点击/识别点 6 个
- `DepotAllTab` ClickSelf roi[452,0,281,134] 模板匹配 | ClickSelf
- `OperOpenInformationMenu` ClickSelf roi[447,0,158,135] 模板: OperInformationMenu.png | ClickSelf 模板:OperInformationMenu.png
- `MiniGame@SPA@TurnFive` DoNothing roi[392,0,180,128] 模板匹配
- `Roguelike@TempRecruitFlag` DoNothing roi[587,76,100,20] 模板匹配
- `Sami@Roguelike@CheckCollapsalParadigmsOnStage` DoNothing roi[590,45,45,35] 模板: Sami@Roguelike@OnStage_1.png, Sami@Roguelike@OnStage_2.png, Sami@Roguelike@OnStage_3.png 模板:['Sami@Roguelike@OnStage_1.png', 'Sami@Roguelike@OnStage_2.png', 'Sami@Roguelike@OnStage_3.png', 'Sami@Roguelike@OnStage_4.png', 'Sami@Roguelike@OnStage_5.png', 'Sami@Roguelike@OnStage_6.png', 'Sami@Roguelike@OnStage_7.png', 'Sami@Roguelike@OnStage_0.png']
- `QuickSwitch@ToTerminal@Entry` DoNothing roi[450,0,159,148] 模板: QuickSwitch/Terminal.png 模板:QuickSwitch/Terminal.png

## [3,2] 区域 (480,180) — 点击/识别点 5 个
- `Stage1-7` ClickSelf roi[0,190,1080,60] OCR: 1-7 | ClickSelf
- `RecruitTimerM` DoNothing roi[575,195,85,60] OCR: 00/10/20
- `BattleOperRetreat` ClickSelf roi[554,146,119,118] 模板匹配 | ClickSelf
- `BattleOperRetreatJustClick` ClickRect roi[572,225,1,6] 直接动作 | ClickRect
- `Sarkaz@Roguelike@ChooseBurdenOperatorSelect3` ClickRect roi[570,240,0,0] 直接动作 | ClickRect

## [0,5] 区域 (0,450) — 点击/识别点 4 个
- `UserCenter` ClickSelf roi[50,470,50,75] 模板匹配 | ClickSelf
- `MiniGame@SecretFront@ReadProperty-Medicine` DoNothing roi[70,450,50,23] 模板匹配
- `RelaunchAnchor@RA@RA1-ManufactureIcon` ClickSelf roi[0,470,120,130] 模板: RelaunchAnchor@RA@ManufactureIcon.png | ClickSelf 模板:RelaunchAnchor@RA@ManufactureIcon.png
- `RelaunchAnchor@RA@RA4-WaitSwipeMap-Common` DoNothing roi[10,486,96,96] 模板: RelaunchAnchor@RA@ManufactureIcon.png 模板:RelaunchAnchor@RA@ManufactureIcon.png

## [1,5] 区域 (160,450) — 点击/识别点 4 个
- `InfrastClueVacancy7` DoNothing roi[50,380,500,195] 模板: ClueVacancy7.png 模板:ClueVacancy7.png
- `InfrastClueVacancyNo7` DoNothing roi[50,380,500,195] 模板: ClueVacancyNo7.png 模板:ClueVacancyNo7.png
- `JieGarden@Roguelike@CoppersAnalyzer-LeftType` DoNothing roi[0,396,353,235] 模板: JieGarden@Roguelike@CoppersTypeLi.png, JieGarden@Roguelike@CoppersTypeHeng.png, JieGarden@Roguelike@CoppersTypeHua.png 模板:['JieGarden@Roguelike@CoppersTypeLi.png', 'JieGarden@Roguelike@CoppersTypeHeng.png', 'JieGarden@Roguelike@CoppersTypeHua.png']
- `SN-Chapter2` ClickSelf roi[163,417,195,125] OCR: 失落旗舰/失落/旗舰 | ClickSelf

## [2,0] 区域 (320,0) — 点击/识别点 3 个
- `InfrastReceptionIcon` DoNothing roi[340,0,210,75] 模板: InfrastReceptionIcon.png 模板:InfrastReceptionIcon.png
- `ReceptionFlag` DoNothing roi[350,0,200,100] 模板匹配
- `BattleKillsFlag` DoNothing roi[150,0,600,70] 模板匹配

## [-1,-1] 区域 (-160,-90) — 点击/识别点 1 个
- `BattleQuickFormationSkillLevel-OCR` DoNothing roi[-50,-66,20,20] 模板匹配

## [-1,1] 区域 (-160,90) — 点击/识别点 1 个
- `JieGarden@Roguelike@CoppersAnalyzer-CastOCR` DoNothing roi[-115,90,69,24] OCR: 已投出

## [0,-3] 区域 (0,-270) — 点击/识别点 1 个
- `JieGarden@Roguelike@CoppersAnalyzer-PickupNameOCR` DoNothing roi[-29,-209,218,37] 模板匹配
