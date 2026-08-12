# MAA 模板图对照索引（模板图 ↔ 任务 ↔ ROI ↔ 语义）

> 生成：2026-08-12 用 tools/vision_translate.py 批量转译（像素特征 + 任务反向引用）
> 用途：理解每张模板图在任务体系里的角色（识别什么、在哪用、点击哪里）
> 重新生成：`python tools/vision_translate.py --dir services/maa/source/resource/template/<目录> --no-ocr`

## 模板命名规则（先看这个再查表）

1. **缺省模板**：任务不写 template 时 = `任务名.png`（如 `UsePrts-Annihilation` → `UsePrts-Annihilation.png`）
2. **目录引用**：template 写相对路径（如 `Default/Terminal.png`、`InfrastPic/InfrastLoading.png`）——所以 UiTheme 各主题图在反向引用里显示"未被直接引用"（按文件名匹配的局限，实际被 `TerminalDefault` 等主题任务使用）
3. **@ 模板约定**：`SwitchTheme@ToggleSettingsMenu.png` = 引用"另一任务的模板文件"（主题无关锚点）
4. **空模板**：`empty.png` 是占位（28+ 任务用），表示"盲点点击/纯状态检测/C++ 配置壳"
5. **隐式命名**：无 template 时按任务名在模板树递归查找（`ShamareThumbnail1` → `InfrastPic/ShamareThumbnail/ShamareThumbnail1.png`）

---
## Battle/UsePrts — 剿灭判定核心（stage_probe 判定信号来源）（8 张）
### NotUsePrts.png
- 路径: `services\maa\source\resource\template\Battle\UsePrts\NotUsePrts.png`
- 尺寸: 109x39 | 主色: #A0A0A0 69%, #404040 12%, #606060 9% | 金色占比: 0.0 | 亮度: 148
- 被任务引用 (1):
  - NotUsePrts [MatchTemplate | ClickSelf | roi [1000, 550, 280, 90] | next: ]
### UnableToAgent.png
- 路径: `services\maa\source\resource\template\Battle\UsePrts\UnableToAgent.png`
- 尺寸: 86x106 | 主色: #000000 40%, #00C0E0 10%, #002040 4% | 金色占比: 0.0 | 亮度: 75
- 被任务引用 (1):
  - UnableToAgent [MatchTemplate | DoNothing | roi [870, 410, 410, 220] | next: Stage, UnableToAgent@SwipeToTheLeft]
### UnableToAgent2.png
- 路径: `services\maa\source\resource\template\Battle\UsePrts\UnableToAgent2.png`
- 尺寸: 59x65 | 主色: #000000 79%, #808080 10%, #202020 3% | 金色占比: 0.0 | 亮度: 46
- 被任务引用 (2):
  - UnableToAgent2 [MatchTemplate | DoNothing | roi [985, 523, 159, 165] | next: Stage, UnableToAgent2@SwipeToTheLeft]
  - Annihilation@UnableToAgent2 [MatchTemplate | DoNothing | roi  | next: ]
### UsePrts-Annihilation.png
- 路径: `services\maa\source\resource\template\Battle\UsePrts\UsePrts-Annihilation.png`
- 尺寸: 83x23 | 主色: #202020 37%, #404020 17%, #E0A040 8% | 金色占比: 0.287 | 亮度: 85
- 被任务引用 (2):
  - UsePrts-Annihilation [MatchTemplate | ClickSelf | roi [877, 532, 183, 123] | next: #self, UsePrts-AnnihilationSuccess]
  - UsePrts-AnnihilationCheck [MatchTemplate | DoNothing | roi  | next: ]
### UsePrts-AnnihilationSuccess.png
- 路径: `services\maa\source\resource\template\Battle\UsePrts\UsePrts-AnnihilationSuccess.png`
- 尺寸: 56x33 | 主色: #C08000 25%, #A08000 16%, #E0E0A0 7% | 金色占比: 0.677 | 亮度: 145
- 被任务引用 (2):
  - UsePrts-AnnihilationSuccess [MatchTemplate | DoNothing | roi [938, 527, 156, 133] | next: StartButton1]
  - UsePrts-AnnihilationSuccessCheck [MatchTemplate | DoNothing | roi  | next: ]
### UsePrts-StageSN.png
- 路径: `services\maa\source\resource\template\Battle\UsePrts\UsePrts-StageSN.png`
- 尺寸: 183x43 | 主色: #000000 73%, #608080 10%, #202020 5% | 金色占比: 0.0 | 亮度: 35
- 被任务引用 (1):
  - UsePrts-StageSN [MatchTemplate | ClickSelf | roi [1010, 508, 270, 143] | next: StartButton1]
### UsePrts.png
- 路径: `services\maa\source\resource\template\Battle\UsePrts\UsePrts.png`
- 尺寸: 102x36 | 主色: #202020 66%, #A0A0A0 16%, #002000 6% | 金色占比: 0.0 | 亮度: 65
- 被任务引用 (2):
  - StageQueue@UsePrts [MatchTemplate | DoNothing | roi  | next: StageQueue@CheckPrts]
  - UsePrts [MatchTemplate | ClickSelf | roi [1000, 550, 280, 90] | next: #self, UsePrts-Annihilation, StartButton1]
### UsePrtsSuccess.png
- 路径: `services\maa\source\resource\template\Battle\UsePrts\UsePrtsSuccess.png`
- 尺寸: 54x55 | 主色: #A0A0A0 39%, #202020 24%, #E0E0E0 18% | 金色占比: 0.0 | 亮度: 148
- 被任务引用 (1):
  - UsePrtsSuccessCheck [MatchTemplate | DoNothing | roi  | next: ]
## StageNavigation — 关卡导航入口（3 张）
### EnterChooseLevelOfDifficulty.png
- 路径: `services\maa\source\resource\template\StageNavigation\EnterChooseLevelOfDifficulty.png`
- 尺寸: 31x33 | 主色: #000000 67%, #202020 15%, #404040 13% | 金色占比: 0.0 | 亮度: 38
- 被任务引用 (1):
  - EnterChooseLevelOfDifficulty [MatchTemplate | ClickSelf | roi [0, 598, 131, 122] | next: ]
### RaidDifficulty-Chapter15.png
- 路径: `services\maa\source\resource\template\StageNavigation\RaidDifficulty-Chapter15.png`
- 尺寸: 66x61 | 主色: #000040 55%, #000020 24%, #000060 8% | 金色占比: 0.0 | 亮度: 50
- 被任务引用 (2):
  - ChangeToRaidDifficulty [MatchTemplate | ClickSelf | roi [600, 610, 130, 110] | next: RaidConfirm, #self]
  - NormalConfirm [MatchTemplate | DoNothing | roi  | next: ]
### RaidDifficulty.png
- 路径: `services\maa\source\resource\template\StageNavigation\RaidDifficulty.png`
- 尺寸: 29x58 | 主色: #000060 85%, #000040 15%, #000080 0% | 金色占比: 0.0 | 亮度: 38
- 被任务引用 (2):
  - ChangeToRaidDifficulty [MatchTemplate | ClickSelf | roi [600, 610, 130, 110] | next: RaidConfirm, #self]
  - NormalConfirm [MatchTemplate | DoNothing | roi  | next: ]
## Award — 奖励领取（6 张）
### AwardFinished.png
- 路径: `services\maa\source\resource\template\Award\AwardFinished.png`
- 尺寸: 139x42 | 主色: #202020 62%, #C0C0C0 9%, #E0E0E0 9% | 金色占比: 0.0 | 亮度: 91
- 被任务引用 (1):
  - AwardFinished [MatchTemplate | Stop | roi [400, 0, 880, 100] | next: ]
### AwardNeedExpand.png
- 路径: `services\maa\source\resource\template\Award\AwardNeedExpand.png`
- 尺寸: 55x15 | 主色: #202020 73%, #0060E0 6%, #404040 6% | 金色占比: 0.0 | 亮度: 64
- 被任务引用 (1):
  - AwardNeedExpand [MatchTemplate | ClickSelf | roi [0, 192, 155, 115] | next: ]
### AwardNotNeedExpand.png
- 路径: `services\maa\source\resource\template\Award\AwardNotNeedExpand.png`
- 尺寸: 50x18 | 主色: #202020 74%, #606060 12%, #404040 9% | 金色占比: 0.0 | 亮度: 65
- 被任务引用 (1):
  - AwardNotNeedExpand [MatchTemplate | DoNothing | roi [0, 192, 155, 115] | next: ]
### DailyTask.png
- 路径: `services\maa\source\resource\template\Award\DailyTask.png`
- 尺寸: 147x42 | 主色: #E0E0E0 71%, #C0C0C0 6%, #202020 6% | 金色占比: 0.0 | 亮度: 192
- 未被任务直接引用（可能是动态模板）
### ReceiveAward.png
- 路径: `services\maa\source\resource\template\Award\ReceiveAward.png`
- 尺寸: 261x66 | 主色: #C08000 35%, #E0C020 17%, #E0E0E0 13% | 金色占比: 0.794 | 亮度: 158
- 被任务引用 (1):
  - ReceiveAward [MatchTemplate | ClickSelf | roi [950, 50, 330, 150] | next: CloseTaskAward]
### WeeklyTask.png
- 路径: `services\maa\source\resource\template\Award\WeeklyTask.png`
- 尺寸: 148x49 | 主色: #E0E0E0 65%, #C0C0C0 8%, #000000 7% | 金色占比: 0.0 | 亮度: 184
- 被任务引用 (2):
  - DailyTask [MatchTemplate | DoNothing | roi  | next: ReceiveAward, WeeklyTask]
  - WeeklyTask [MatchTemplate | ClickSelf | roi [400, 0, 880, 100] | next: ReceiveAward, Stop]
## UiTheme/Default — 默认主题终端入口（16 主题之一）（9 张）
### DepotEnter.png
- 路径: `services\maa\source\resource\template\UiTheme\Default\DepotEnter.png`
- 尺寸: 82x76 | 主色: #404040 78%, #E0E0E0 11%, #606060 3% | 金色占比: 0.0 | 亮度: 97
- 未被任务直接引用（可能是动态模板）
### EnterInfrast.png
- 路径: `services\maa\source\resource\template\UiTheme\Default\EnterInfrast.png`
- 尺寸: 85x75 | 主色: #E0E0E0 58%, #202020 19%, #C0C0C0 6% | 金色占比: 0.0 | 亮度: 179
- 未被任务直接引用（可能是动态模板）
### Friends.png
- 路径: `services\maa\source\resource\template\UiTheme\Default\Friends.png`
- 尺寸: 135x41 | 主色: #404040 60%, #E0E0E0 21%, #202020 9% | 金色占比: 0.0 | 亮度: 113
- 未被任务直接引用（可能是动态模板）
### GachaEnter.png
- 路径: `services\maa\source\resource\template\UiTheme\Default\GachaEnter.png`
- 尺寸: 144x56 | 主色: #C0A000 54%, #E0C040 10%, #E0E0E0 9% | 金色占比: 0.812 | 亮度: 160
- 未被任务直接引用（可能是动态模板）
### Mall.png
- 路径: `services\maa\source\resource\template\UiTheme\Default\Mall.png`
- 尺寸: 86x71 | 主色: #E0C040 29%, #C0A000 27%, #E0E0E0 15% | 金色占比: 0.775 | 亮度: 169
- 未被任务直接引用（可能是动态模板）
### OperBoxEnter.png
- 路径: `services\maa\source\resource\template\UiTheme\Default\OperBoxEnter.png`
- 尺寸: 95x81 | 主色: #E0E0E0 63%, #202020 11%, #C0C0C0 8% | 金色占比: 0.0 | 亮度: 204
- 未被任务直接引用（可能是动态模板）
### Recruit.png
- 路径: `services\maa\source\resource\template\UiTheme\Default\Recruit.png`
- 尺寸: 151x61 | 主色: #C0A000 52%, #E0C040 11%, #E0E0E0 8% | 金色占比: 0.783 | 亮度: 151
- 未被任务直接引用（可能是动态模板）
### Task.png
- 路径: `services\maa\source\resource\template\UiTheme\Default\Task.png`
- 尺寸: 90x85 | 主色: #E0E0E0 77%, #202020 12%, #C0C0C0 2% | 金色占比: 0.0 | 亮度: 212
- 未被任务直接引用（可能是动态模板）
### Terminal.png
- 路径: `services\maa\source\resource\template\UiTheme\Default\Terminal.png`
- 尺寸: 138x80 | 主色: #E0E0E0 59%, #202020 23%, #404040 4% | 金色占比: 0.0 | 亮度: 182
- 未被任务直接引用（可能是动态模板）
## Battle/StartButton — 开始行动按钮组（12 张）
### BattleStartAdverse.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\BattleStartAdverse.png`
- 尺寸: 131x272 | 主色: #000040 18%, #002080 18%, #002060 15% | 金色占比: 0.0 | 亮度: 81
- 被任务引用 (1):
  - BattleStartAdverse [MatchTemplate | DoNothing | roi  | next: ]
### BattleStartExercise.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\BattleStartExercise.png`
- 尺寸: 131x277 | 主色: #E0E0E0 12%, #806000 8%, #A08020 8% | 金色占比: 0.398 | 亮度: 121
- 被任务引用 (1):
  - BattleStartExercise [MatchTemplate | DoNothing | roi  | next: ]
### BattleStartNormal.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\BattleStartNormal.png`
- 尺寸: 131x272 | 主色: #002080 19%, #0020A0 12%, #000040 11% | 金色占比: 0.0 | 亮度: 93
- 被任务引用 (1):
  - BattleStartNormal [MatchTemplate | DoNothing | roi  | next: ]
### BattleStartPlot.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\BattleStartPlot.png`
- 尺寸: 112x115 | 主色: #C08000 27%, #806000 26%, #804000 17% | 金色占比: 0.591 | 亮度: 106
- 被任务引用 (1):
  - BattleStartPlot [MatchTemplate | ClickSelf | roi [872, 250, 212, 255] | next: SkipBattlePlot]
### BattleStartRaid.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\BattleStartRaid.png`
- 尺寸: 132x274 | 主色: #000060 27%, #000040 23%, #000080 12% | 金色占比: 0.0 | 亮度: 63
- 被任务引用 (1):
  - BattleStartRaid [MatchTemplate | DoNothing | roi  | next: ]
### BattleStartSimulation.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\BattleStartSimulation.png`
- 尺寸: 63x33 | 主色: #A06000 79%, #E0E0E0 8%, #A08000 6% | 金色占比: 0.882 | 亮度: 120
- 被任务引用 (1):
  - BattleStartSimulation [MatchTemplate | ClickSelf | roi [955, 86, 136, 606] | next: ]
### BattleStartTrials.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\BattleStartTrials.png`
- 尺寸: 134x280 | 主色: #806000 14%, #A08000 13%, #604000 13% | 金色占比: 0.455 | 亮度: 109
- 被任务引用 (1):
  - BattleStartTrials [MatchTemplate | DoNothing | roi  | next: ]
### BattleStartTrialsRaid.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\BattleStartTrialsRaid.png`
- 尺寸: 128x273 | 主色: #0020A0 20%, #000060 18%, #002080 14% | 金色占比: 0.0 | 亮度: 78
- 被任务引用 (1):
  - BattleStartTrialsRaid [MatchTemplate | DoNothing | roi  | next: ]
### OperParadoxBegin.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\OperParadoxBegin.png`
- 尺寸: 204x60 | 主色: #C08000 23%, #A06000 21%, #806000 20% | 金色占比: 0.624 | 亮度: 106
- 被任务引用 (2):
  - OperParadoxBegin [JustReturn | DoNothing | roi  | next: OperOpenInformationMenu]
  - OperOpenParadoxChooseSkill [MatchTemplate | ClickSelf | roi [1009, 603, 260, 114] | next: #self, Stop]
### StartButton1.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\StartButton1.png`
- 尺寸: 207x74 | 主色: #806000 18%, #C08000 17%, #A06000 16% | 金色占比: 0.47 | 亮度: 92
- 被任务引用 (1):
  - StartButton1 [OcrDetect | ClickSelf | roi [1010, 625, 260, 61] | next: StartButton2, UseMedicine, UseStone]
### StartButton2.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\StartButton2.png`
- 尺寸: 135x280 | 主色: #002080 19%, #0040C0 12%, #0020A0 11% | 金色占比: 0.0 | 亮度: 93
- 被任务引用 (2):
  - StartButton2 [MatchTemplate | ClickSelf | roi [1000, 350, 220, 340] | next: StartButton2TryAgain, StartButton2WaitTime]
  - StartButton2TryAgain [MatchTemplate | DoNothing | roi  | next: ]
### StartButton2Adverse.png
- 路径: `services\maa\source\resource\template\Battle\StartButton\StartButton2Adverse.png`
- 尺寸: 131x272 | 主色: #000040 18%, #002080 18%, #002060 15% | 金色占比: 0.0 | 亮度: 81
- 被任务引用 (2):
  - StartButton2 [MatchTemplate | ClickSelf | roi [1000, 350, 220, 340] | next: StartButton2TryAgain, StartButton2WaitTime]
  - StartButton2TryAgain [MatchTemplate | DoNothing | roi  | next: ]
