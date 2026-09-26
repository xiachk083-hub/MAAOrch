# 主线作业轴数据层归一化审计（34 关替代轴 + 47 突袭核对 + 索引）

- 生成时间：2026-09-24 15:23（中国标准时间）
- 账号：官服 UID 751737728「堂吉诃德」（专精零、无模组）
- 输入：`runtime/plans/story_jobs_big/` 635 份（上游拉轴批次产物）+ `runtime/plans/story_axes_big2.json` + `data/_stage_table.json`（关卡代号权威表）+ prts 作业站（只读查询，补齐真·突袭轴正文）
- 产出：替代轴 101 份 / 真·突袭轴 21 份（新落盘）/ 突袭兜底 28 份；索引 `runtime/plans/story_jobs_index.json`
- **没有**真机跑过一局；所有替代/兜底轴都标注了「真打前必须自查」⇒ 本文只做数据层归一化，不宣称能过。
- 作业生成脚本：`runtime/plans/_story_norm_scan.py`（扫描）→ `_story_norm_apply.py`（改写+索引）→ `_story_norm_verify.py`（校验，输出 `runtime/reports/story_norm_verify.txt`）

## 0. 总量

| 项 | 数 |
|---|---|
| story_jobs_big 文件（归一化后） | **785** = 635（原） + 101（替代轴） + 21（真·突袭轴） + 28（突袭兜底） |
| 替代轴 `*_alt*.json` | **101**（覆盖 34 个原来 prts 无轴的关，34/34） |
| 真·突袭轴 `*_<id>.json`（stage_name 带 #f#） | **21** 本次新落盘（另有 9 份上游已有）；够 19 个突袭 unit 用 |
| 突袭兜底 `*_raidfb.json` | **28**（prts 该关突袭槽位无可用轴） |
| 索引 | `runtime/plans/story_jobs_index.json`：**307** 关 / **932** 条候选 / 唯一文件 785 |

## 1. 问题①：34 个「prts 上没有任何可用轴」的关（全是普通难度）

### 1.1 做法（改的是什么 / 依据什么）

- **改的是**：同关**磨难**难度的轴文件的 `stage_name` 字段（`tough_XX-YY` → 该关普通难度的真实代号 `main_XX-YY`），并在 `doc.details` 开头插入一行来源标注。**actions / opers 一个字都没改**（校验脚本用 sha256 对比过，101/101 与来源一致）。
- **依据**：`runtime/plans/story_mines.json` 里同一「显示代号」同时挂着普通与磨难两个 unit，其 `stage_id` 就是关卡代号；每条改写都用 `data/_stage_table.json` 复核过：目标代号存在、`code` 正是该显示代号、`zoneId` 是同一章（例：`main_13-19` → code=`13-21`, zoneId=`main_13`, apCost=24, difficulty=NORMAL）。⇒ **不伪造 prts 上不存在的轴**：每份替代轴的来源 prts id + 原难度都写在文件名（`<代号>_<原prtsid>_alt<N>.json`）、`doc.details`、以及索引的 `origin` 字段里。
- 每关最多落 3 份（沿用上游口径：来源磨难关原本的 rank1~3）。

### 1.2 风险（逐关写清）

- **共性风险（34 关全有）**：磨难与普通是**同一张地图、同一套 enemy spawn 词条**，但磨难下敌方属性更高（且部分关有附加限制）；把磨难轴放到普通难度上，**时序会偏**（敌人死得快 ⇒ 技能/撤退节奏可能对不上），**有失败可能**。所以每份替代轴都带 `must_selfcheck=true`，建议一律**先演习**（演习不耗理智）。
- **个性风险**：沿用来源磨难轴的原始风险（见下表「来源轴风险」列），按 101 份替代轴统计：
  - 含额外动作（时序敏感）的 **30** 份；
  - 点名了本号没精二的干员（大概率跑不动/需要手动）的 **40** 份；
  - 作者写了**显式 requirements**（本号专精零/无模组 ⇒ MAA 编队即拒，需 `--ignore-requirements`）的 **6** 份。

### 1.3 逐关明细

| 显示代号 | 目标 stage_name | 依据（stage_table） | 来源 prts id | 原难度 | 新文件 | 动作/部署 | 来源轴风险 |
|---|---|---|---|---|---|---|---|
| 13-21 | `main_13-19` | code=13-21·zone=main_13·ap=24 | `prts://28828`、`prts://32371`、`prts://49375` | 磨难(`tough_13-19`) | `13-21_28828_alt1.json`<br>`13-21_32371_alt2.json`<br>`13-21_49375_alt3.json` | 16/10、16/10、4/3 | 含额外动作 ['Retreat', 'Skill']（时序敏感）；doc 口径：专三、模组（软风险，不是 MAA 门槛）；点名了没精二的干员：夜刀(精0Lv30)、翎羽(精1Lv55)、安德切尔(精1Lv55)、巡林者(精0Lv30) |
| 13-20 | `main_13-18` | code=13-20·zone=main_13·ap=21 | `prts://28766`、`prts://41363`、`prts://91700` | 磨难(`tough_13-18`) | `13-20_28766_alt1.json`<br>`13-20_41363_alt2.json`<br>`13-20_91700_alt3.json` | 10/6、10/6、10/5 | 含额外动作 ['Skill', 'SkillUsage']（时序敏感）；点名了没精二的干员：芙蓉(精1Lv55)、安赛尔(精1Lv55)；含额外动作 ['Retreat']（时序敏感）；doc 口径：专三、模组（软风险，不是 MAA 门槛） |
| 13-19 | `main_13-17` | code=13-19·zone=main_13·ap=21 | `prts://73082`、`prts://74125`、`prts://33633` | 磨难(`tough_13-17`) | `13-19_73082_alt1.json`<br>`13-19_74125_alt2.json`<br>`13-19_33633_alt3.json` | 15/10、15/10、18/10 | 含额外动作 ['Skill', 'SkillUsage']（时序敏感）；含额外动作 ['Retreat', 'Skill']（时序敏感） |
| 13-12 | `main_13-10` | code=13-12·zone=main_13·ap=21 | `prts://71775`、`prts://47496`、`prts://47720` | 磨难(`tough_13-10`) | `13-12_71775_alt1.json`<br>`13-12_47496_alt2.json`<br>`13-12_47720_alt3.json` | 5/3、5/3、7/4 | doc 口径：专三、模组（软风险，不是 MAA 门槛）；无显式风险（仍需演习验证）；含额外动作 ['Retreat']（时序敏感）；doc 口径：专3、模组、满级（软风险，不是 MAA 门槛） |
| 13-11 | `main_13-09` | code=13-11·zone=main_13·ap=24 | `prts://47494`、`prts://47717`、`prts://87712` | 磨难(`tough_13-09`) | `13-11_47494_alt1.json`<br>`13-11_47717_alt2.json`<br>`13-11_87712_alt3.json` | 13/8、21/12、21/12 | 含额外动作 ['Retreat', 'Skill']（时序敏感）；点名了没精二的干员：史都华德(精1Lv55)、古米(精1Lv60)；含额外动作 ['Retreat', 'Skill', 'SkillUsage']（时序敏感）；doc 口径：专3、模组、满级（软风险，不是 MAA 门槛） |
| 13-10 | `main_13-08` | code=13-10·zone=main_13·ap=21 | `prts://28777`、`prts://68645`、`prts://47711` | 磨难(`tough_13-08`) | `13-10_28777_alt1.json`<br>`13-10_68645_alt2.json`<br>`13-10_47711_alt3.json` | 11/6、6/4、6/4 | 含额外动作 ['BulletTime', 'Skill']（时序敏感）；点名了没精二的干员：芙蓉(精1Lv55)；doc 口径：专三、模组（软风险，不是 MAA 门槛）；显式 requirements ['维什戴尔 专- 模1'] ⇒ 本号专精零/无模组，MAA **编队即拒**（需 --ignore-requirements）；练度不足 维什戴尔 需 Lv90（你是精二Lv88）；点名了没精二的干员：梓兰(精1Lv55)、白面鸮(精1 |
| 13-8 | `main_13-07` | code=13-8·zone=main_13·ap=21 | `prts://65683`、`prts://28801`、`prts://47710` | 磨难(`tough_13-07`) | `13-8_65683_alt1.json`<br>`13-8_28801_alt2.json`<br>`13-8_47710_alt3.json` | 7/4、11/7、7/4 | 含额外动作 ['Skill']（时序敏感）；doc 口径：专3、模组、满级（软风险，不是 MAA 门槛）；含额外动作 ['Skill', 'SkillUsage']（时序敏感）；点名了没精二的干员：梓兰(精1Lv55) |
| 13-7 | `main_13-06` | code=13-7·zone=main_13·ap=21 | `prts://28733`、`prts://47709`、`prts://47490` | 磨难(`tough_13-06`) | `13-7_28733_alt1.json`<br>`13-7_47709_alt2.json`<br>`13-7_47490_alt3.json` | 6/3、4/2、6/3 | 含额外动作 ['Skill']（时序敏感）；doc 口径：专3、模组、满级（软风险，不是 MAA 门槛） |
| 13-4 | `main_13-03` | code=13-4·zone=main_13·ap=21 | `prts://71763`、`prts://68640`、`prts://47702` | 磨难(`tough_13-03`) | `13-4_71763_alt1.json`<br>`13-4_68640_alt2.json`<br>`13-4_47702_alt3.json` | 7/5、7/5、11/8 | doc 口径：专三、模组（软风险，不是 MAA 门槛）；点名了没精二的干员：芙蓉(精1Lv55)；点名了没精二的干员：伊桑(精0Lv1)；含额外动作 ['Retreat']（时序敏感）；doc 口径：专3、模组、满级（软风险，不是 MAA 门槛） |
| 13-3 | `main_13-02` | code=13-3·zone=main_13·ap=21 | `prts://65814`、`prts://61655` | 磨难(`tough_13-02`) | `13-3_65814_alt1.json`<br>`13-3_61655_alt2.json` | 13/10、8/5 | 点名了没精二的干员：Lancet-2(精0Lv30)、古米(精1Lv60)、梓兰(精1Lv55)、伊桑(精0Lv1)、12F(精0Lv30)、蜜莓(精0Lv1)；含额外动作 ['Retreat']（时序敏感）；显式 requirements ['乌尔比安 专10 模-'] ⇒ 本号专精零/无模组，MAA **编队即拒**（需 --ignore-requirements） |
| 13-2 | `main_13-01` | code=13-2·zone=main_13·ap=21 | `prts://61652`、`prts://62018`、`prts://93158` | 磨难(`tough_13-01`) | `13-2_61652_alt1.json`<br>`13-2_62018_alt2.json`<br>`13-2_93158_alt3.json` | 7/5、7/5、6/3 | 显式 requirements ['维什戴尔 专10 模-', '乌尔比安 专10 模-', '歌蕾蒂娅 专10 模-', '桃金娘 专7 模-', '塞雷娅 专10 模-'] ⇒ 本号专精零/无模组，MAA **编队即拒**（需 --ignore-requirements）；显式 requirements ['乌尔比安 专10 模1', '逻各斯 专10 模4', '歌蕾蒂娅 专10 模1'] ⇒ 本号专精零/无模组，MAA **编队 |
| 14-20 | `main_14-18` | code=14-20·zone=main_14·ap=24 | `prts://37086`、`prts://49011`、`prts://82854` | 磨难(`tough_14-18`) | `14-20_37086_alt1.json`<br>`14-20_49011_alt2.json`<br>`14-20_82854_alt3.json` | 13/6、6/4、9/5 | 含额外动作 ['Skill']（时序敏感）；点名了没精二的干员：杜林(精0Lv30)；doc 口径：专3、模组、满级（软风险，不是 MAA 门槛）；点名了没精二的干员：芙蓉(精1Lv55)、梓兰(精1Lv55)；doc 口径：专三、模组（软风险，不是 MAA 门槛） |
| 14-12 | `main_14-11` | code=14-12·zone=main_14·ap=21 | `prts://45137`、`prts://82846`、`prts://41465` | 磨难(`tough_14-11`) | `14-12_45137_alt1.json`<br>`14-12_82846_alt2.json`<br>`14-12_41465_alt3.json` | 11/2、5/3、4/2 | 含额外动作 ['Retreat', 'Skill']（时序敏感）；点名了没精二的干员：芙蓉(精1Lv55)；doc 口径：专三（软风险，不是 MAA 门槛）；点名了没精二的干员：拉普兰德(精1Lv70) |
| 14-6 | `main_14-05` | code=14-6·zone=main_14·ap=21 | `prts://36687`、`prts://47779`、`prts://37002` | 磨难(`tough_14-05`) | `14-6_36687_alt1.json`<br>`14-6_47779_alt2.json`<br>`14-6_37002_alt3.json` | 3/2、4/2、4/2 | 无显式风险（仍需演习验证）；doc 口径：专3、模组、满级（软风险，不是 MAA 门槛） |
| 14-4 | `main_14-03` | code=14-4·zone=main_14·ap=21 | `prts://36963`、`prts://36900`、`prts://37449` | 磨难(`tough_14-03`) | `14-4_36963_alt1.json`<br>`14-4_36900_alt2.json`<br>`14-4_37449_alt3.json` | 7/5、6/4、4/3 | 无显式风险（仍需演习验证） |
| 14-3 | `main_14-02` | code=14-3·zone=main_14·ap=21 | `prts://36962`、`prts://36658`、`prts://82837` | 磨难(`tough_14-02`) | `14-3_36962_alt1.json`<br>`14-3_36658_alt2.json`<br>`14-3_82837_alt3.json` | 8/6、4/3、7/5 | 无显式风险（仍需演习验证）；点名了没精二的干员：芙蓉(精1Lv55)；doc 口径：专三、模组（软风险，不是 MAA 门槛） |
| 14-2 | `main_14-01` | code=14-2·zone=main_14·ap=21 | `prts://36960`、`prts://79536`、`prts://82836` | 磨难(`tough_14-01`) | `14-2_36960_alt1.json`<br>`14-2_79536_alt2.json`<br>`14-2_82836_alt3.json` | 4/2、3/1、3/1 | 无显式风险（仍需演习验证）；只下 1 个干员（无兜底）；doc 口径：专三、模组（软风险，不是 MAA 门槛） |
| 12-20 | `main_12-18` | code=12-20·zone=main_12·ap=24 | `prts://49567`、`prts://66745`、`prts://57092` | 磨难(`tough_12-18`) | `12-20_49567_alt1.json`<br>`12-20_66745_alt2.json`<br>`12-20_57092_alt3.json` | 6/5、5/5、10/8 | 无显式风险（仍需演习验证）；点名了没精二的干员：德克萨斯(精1Lv70)；点名了没精二的干员：蜜莓(精0Lv1)、古米(精1Lv60)、芙蓉(精1Lv55)、嘉维尔(精1Lv33)；doc 口径：专三、模组（软风险，不是 MAA 门槛） |
| 12-19 | `main_12-17` | code=12-19·zone=main_12·ap=24 | `prts://22445`、`prts://49189`、`prts://28442` | 磨难(`tough_12-17`) | `12-19_22445_alt1.json`<br>`12-19_49189_alt2.json`<br>`12-19_28442_alt3.json` | 14/8、14/8、12/9 | 含额外动作 ['BulletTime', 'Skill']（时序敏感）；doc 口径：专三、模组（软风险，不是 MAA 门槛）；点名了没精二的干员：德克萨斯(精1Lv70)、拉普兰德(精1Lv70)、白面鸮(精1Lv1)；点名白面鸮（本号 精1Lv1）⇒ 奶量很可能不够；含额外动作 ['Retreat']（时序敏感）；doc 口径：模组（软风险，不是 MAA 门槛） |
| 12-10 | `main_12-09` | code=12-10·zone=main_12·ap=21 | `prts://49038`、`prts://22326`、`prts://28393` | 磨难(`tough_12-09`) | `12-10_49038_alt1.json`<br>`12-10_22326_alt2.json`<br>`12-10_28393_alt3.json` | 7/4、7/5、7/5 | 含额外动作 ['Skill']（时序敏感）；点名了没精二的干员：拉普兰德(精1Lv70) |
| 12-9 | `main_12-08` | code=12-9·zone=main_12·ap=21 | `prts://22325`、`prts://36023`、`prts://52562` | 磨难(`tough_12-08`) | `12-9_22325_alt1.json`<br>`12-9_36023_alt2.json`<br>`12-9_52562_alt3.json` | 7/5、7/5、7/5 | 点名了没精二的干员：德克萨斯(精1Lv70) |
| 12-8 | `main_12-07` | code=12-8·zone=main_12·ap=21 | `prts://22324`、`prts://49035`、`prts://67483` | 磨难(`tough_12-07`) | `12-8_22324_alt1.json`<br>`12-8_49035_alt2.json`<br>`12-8_67483_alt3.json` | 5/3、5/3、5/3 | 无显式风险（仍需演习验证）；doc 口径：满潜、满级、潜能（软风险，不是 MAA 门槛） |
| 12-6 | `main_12-05` | code=12-6·zone=main_12·ap=21 | `prts://48999`、`prts://22321`、`prts://28453` | 磨难(`tough_12-05`) | `12-6_48999_alt1.json`<br>`12-6_22321_alt2.json`<br>`12-6_28453_alt3.json` | 9/6、9/7、9/7 | 含额外动作 ['Output']（时序敏感）；doc 口径：满潜（软风险，不是 MAA 门槛）；点名了没精二的干员：德克萨斯(精1Lv70) |
| 12-4 | `main_12-03` | code=12-4·zone=main_12·ap=21 | `prts://36205`、`prts://48982`、`prts://66780` | 磨难(`tough_12-03`) | `12-4_36205_alt1.json`<br>`12-4_48982_alt2.json`<br>`12-4_66780_alt3.json` | 7/5、8/6、7/5 | 无显式风险（仍需演习验证） |
| 12-3 | `main_12-02` | code=12-3·zone=main_12·ap=21 | `prts://36204`、`prts://48979`、`prts://48980` | 磨难(`tough_12-02`) | `12-3_36204_alt1.json`<br>`12-3_48979_alt2.json`<br>`12-3_48980_alt3.json` | 7/5、7/5、7/5 | 无显式风险（仍需演习验证）；doc 口径：模组、满级（软风险，不是 MAA 门槛） |
| 12-2 | `main_12-01` | code=12-2·zone=main_12·ap=21 | `prts://48978`、`prts://57073`、`prts://28388` | 磨难(`tough_12-01`) | `12-2_48978_alt1.json`<br>`12-2_57073_alt2.json`<br>`12-2_28388_alt3.json` | 6/4、6/4、6/5 | doc 口径：模组、满级（软风险，不是 MAA 门槛）；点名了没精二的干员：芙蓉(精1Lv55)；点名了没精二的干员：拉普兰德(精1Lv70) |
| 11-8 | `main_11-07` | code=11-8·zone=main_11·ap=24 | `prts://47351`、`prts://28596`、`prts://45125` | 磨难(`tough_11-07`) | `11-8_47351_alt1.json`<br>`11-8_28596_alt2.json`<br>`11-8_45125_alt3.json` | 6/4、9/6、14/7 | doc 口径：专3、模组、满级（软风险，不是 MAA 门槛）；点名了没精二的干员：德克萨斯(精1Lv70)；含额外动作 ['Skill']（时序敏感）；点名了没精二的干员：Lancet-2(精0Lv30)、德克萨斯(精1Lv70) |
| 11-1 | `main_11-01` | code=11-1·zone=main_11·ap=21 | `prts://54209`、`prts://39383`、`prts://92637` | 磨难(`tough_11-01`) | `11-1_54209_alt1.json`<br>`11-1_39383_alt2.json`<br>`11-1_92637_alt3.json` | 4/2、3/1、4/2 | 无显式风险（仍需演习验证）；只下 1 个干员（无兜底）；显式 requirements ['乌尔比安 专10 模1', '歌蕾蒂娅 专10 模1'] ⇒ 本号专精零/无模组，MAA **编队即拒**（需 --ignore-requirements） |
| 10-14 | `main_10-12` | code=10-14·zone=main_10·ap=21 | `prts://28937`、`prts://48355`、`prts://54016` | 磨难(`tough_10-12`) | `10-14_28937_alt1.json`<br>`10-14_48355_alt2.json`<br>`10-14_54016_alt3.json` | 6/4、6/4、8/5 | 无显式风险（仍需演习验证）；点名了没精二的干员：维荻(精0Lv1)、嘉维尔(精1Lv33)、月见夜(精1Lv55)；含额外动作 ['Retreat']（时序敏感） |
| 10-10 | `main_10-09` | code=10-10·zone=main_10·ap=21 | `prts://28082`、`prts://47303`、`prts://54013` | 磨难(`tough_10-09`) | `10-10_28082_alt1.json`<br>`10-10_47303_alt2.json`<br>`10-10_54013_alt3.json` | 7/5、9/6、8/5 | 点名了没精二的干员：德克萨斯(精1Lv70)；点名了没精二的干员：伊桑(精0Lv1)；含额外动作 ['SkillUsage']（时序敏感）；doc 口径：专3、模组、满级（软风险，不是 MAA 门槛）；点名了没精二的干员：蜜莓(精0Lv1)、炎熔(精1Lv55)；含额外动作 ['Skill']（时序敏感） |
| 10-7 | `main_10-06` | code=10-7·zone=main_10·ap=24 | `prts://20805`、`prts://54010`、`prts://39381` | 磨难(`tough_10-06`) | `10-7_20805_alt1.json`<br>`10-7_54010_alt2.json`<br>`10-7_39381_alt3.json` | 17/6、13/9、13/6 | 含额外动作 ['Retreat', 'Skill']（时序敏感）；doc 口径：专三（软风险，不是 MAA 门槛）；点名了没精二的干员：夜刀(精0Lv30)、孑(精0Lv1)、嘉维尔(精1Lv33)、梓兰(精1Lv55)；含额外动作 ['Retreat']（时序敏感）；doc 口径：专三、模组、潜能（软风险，不是 MAA 门槛）；点名了没精二的干员：夜刀(精0Lv30)、砾(精0Lv45)、德克萨斯(精1Lv70)、调香师(精1Lv60 |
| 10-6 | `main_10-05` | code=10-6·zone=main_10·ap=21 | `prts://21858`、`prts://21339`、`prts://54009` | 磨难(`tough_10-05`) | `10-6_21858_alt1.json`<br>`10-6_21339_alt2.json`<br>`10-6_54009_alt3.json` | 5/3、3/2、5/3 | 无显式风险（仍需演习验证）；点名了没精二的干员：芙蓉(精1Lv55) |
| 10-5 | `main_10-04` | code=10-5·zone=main_10·ap=21 | `prts://45199`、`prts://46220`、`prts://54008` | 磨难(`tough_10-04`) | `10-5_45199_alt1.json`<br>`10-5_46220_alt2.json`<br>`10-5_54008_alt3.json` | 8/6、7/5、12/9 | 无显式风险（仍需演习验证）；点名了没精二的干员：芙蓉(精1Lv55)、炎熔(精1Lv55)、伊桑(精0Lv1)；含额外动作 ['Retreat']（时序敏感） |
| 10-2 | `main_10-01` | code=10-2·zone=main_10·ap=21 | `prts://45196`、`prts://50451`、`prts://30594` | 磨难(`tough_10-01`) | `10-2_45196_alt1.json`<br>`10-2_50451_alt2.json`<br>`10-2_30594_alt3.json` | 10/8、10/5、7/5 | 点名了没精二的干员：梓兰(精1Lv55)、古米(精1Lv60)、安赛尔(精1Lv55)；点名了没精二的干员：讯使(精1Lv60)；含额外动作 ['Skill', 'SkillUsage']（时序敏感）；点名了没精二的干员：德克萨斯(精1Lv70)、白面鸮(精1Lv1)；点名白面鸮（本号 精1Lv1）⇒ 奶量很可能不够 |

> 来源轴风险来自上游 `story_axes_big2.json` 的候选评估（`flags`）：「含额外动作」= 有 Deploy/SkillDaemon 之外的动作，时序敏感；「点名了没精二的干员」= opers 里有本号只有精一/精零的干员；「显式 requirements」= 作者写了专精/模组要求，本号专精零无模组 ⇒ MAA 编队即拒，需 `--ignore-requirements`。

## 2. 问题②：47 个突袭 unit 的逐条核对

- 核对口径：「真·突袭轴」= prts 上挂在**同关突袭槽位**（`stage_name` 形如 `main_XX-YY#f#`，即游戏里 0~9 章的 FOUR_STAR 难度）且**本号 box 跑得动**（不缺干员、练度达标）的作业。
- 结果：**19/47** 个突袭 unit 换上了真·突袭轴（其中 **17** 个 unit 的轴是本次从 prts 补拉正文后新落盘的，其余 2 个上游已落盘、只是排错序，本次置顶）；
  **28/47** 在 prts 上找不到可跑的真突袭轴 ⇒ 保留普通轴兜底 + 标红。
  其中：**10** 个「prts 上有真突袭轴，但缺干员/练度不足」（练一练就能用，见 2.3），**18** 个「prts 突袭槽位一条作业都没有」。

### 2.1 换上真·突袭轴的 unit（19 个）

| 显示代号 | 突袭代号 | 真·突袭轴（prts id） | 点赞 | 作者 difficulty | 动作/部署 | 处置 |
|---|---|---|---|---|---|---|
| 9-19 | `main_09-17#f#` | `prts://76803` | 49 | 3=普通/突袭通用 | 17/12 | 上游已落盘（本次置顶）；文件 `9-19_76803.json` |
| 9-18 | `main_09-16#f#` | `prts://76788` | 95 | 3=普通/突袭通用 | 10/6 | 本次新落盘；文件 `9-18_76788.json` |
| 9-17 | `main_09-15#f#` | `prts://39915` | 19 | 3=普通/突袭通用 | 15/11 | 本次新落盘；文件 `9-17_39915.json`；备选 `39916` |
| 9-14 | `main_09-12#f#` | `prts://76782` | 52 | 3=普通/突袭通用 | 6/4 | 本次新落盘；文件 `9-14_76782.json` |
| 9-4 | `main_09-03#f#` | `prts://37118` | 107 | 2=仅突袭 | 8/6 | 本次新落盘；文件 `9-4_37118.json` |
| 7-14 | `main_07-12#f#` | `prts://41696` | 7 | 3=普通/突袭通用 | 7/6 | 本次新落盘；文件 `7-14_41696.json` |
| 7-13 | `main_07-11#f#` | `prts://42702` | 84 | 3=普通/突袭通用 | 9/6 | 本次新落盘；文件 `7-13_42702.json` |
| 7-10 | `main_07-08#f#` | `prts://76678` | 19 | 3=普通/突袭通用 | 6/4 | 本次新落盘；文件 `7-10_76678.json` |
| 7-9 | `main_07-07#f#` | `prts://24319` | 0 | 1=仅普通⚠️ | 6/4 | 本次新落盘；文件 `7-9_24319.json` |
| 7-6 | `main_07-05#f#` | `prts://76676` | 60 | 3=普通/突袭通用 | 7/5 | 本次新落盘；文件 `7-6_76676.json` |
| 7-5 | `main_07-04#f#` | `prts://23982` | 0 | 3=普通/突袭通用 | 9/6 | 本次新落盘；文件 `7-5_23982.json`；备选 `24320` |
| 7-4 | `main_07-03#f#` | `prts://76674` | 103 | 3=普通/突袭通用 | 8/6 | 上游已落盘（本次置顶）；文件 `7-4_76674.json`；备选 `24321` |
| 7-3 | `main_07-02#f#` | `prts://24322` | 2 | 1=仅普通⚠️ | 5/3 | 本次新落盘；文件 `7-3_24322.json` |
| 7-2 | `main_07-01#f#` | `prts://76672` | 25 | 3=普通/突袭通用 | 5/3 | 本次新落盘；文件 `7-2_76672.json` |
| JT8-3 | `main_08-17#f#` | `prts://47402` | 24 | 3=普通/突袭通用 | 13/8 | 本次新落盘；文件 `JT8-3_47402.json` |
| M8-8 | `main_08-14#f#` | `prts://76731` | 27 | 3=普通/突袭通用 | 26/9 | 本次新落盘；文件 `M8-8_76731.json` |
| R8-9 | `main_08-10#f#` | `prts://49565` | 533 | 3=普通/突袭通用 | 8/7 | 上游已落盘（本次置顶）；文件 `R8-9_49565.json` |
| R8-8 | `main_08-08#f#` | `prts://76705` | 44 | 3=普通/突袭通用 | 13/8 | 本次新落盘；文件 `R8-8_76705.json` |
| R8-6 | `main_08-06#f#` | `prts://52449` | 368 | 3=普通/突袭通用 | 9/6 | 本次新落盘；文件 `R8-6_52449.json`；备选 `76701` |

- 这些文件的 `stage_name` 就是突袭代号（带 `#f#`），MAA 认这个代号 ⇒ 会自己去点「突袭」。
- ⚠️ 其中有 4 份虽然挂在 `#f#` 槽位、作者却标了 `difficulty=1（仅普通）`（`7-9` `prts://24319`（首选）、`7-3` `prts://24322`（首选）、`7-5` `prts://24320`（备选）、`7-4` `prts://24321`（备选））⇒ 索引里标 `raid-slot-author-declared-normal` + `must_selfcheck=true`，当普通轴对待。

### 2.2 找不到真·突袭轴 ⇒ 普通轴兜底 + 显式标红（28 个）

| 显示代号 | 突袭代号 | 兜底文件（新，文件名带 `_raidfb`） | 来源 prts id（普通轴） | 改动 | 真轴情况 |
|---|---|---|---|---|---|
| 9-16 | `main_09-14#f#` | `9-16_46209_raidfb.json` | `prts://46209`（← 原文件 `9-16_46209.json`） | 只把 `stage_name` main_09-14 → main_09-14#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| 9-15 | `main_09-13#f#` | `9-15_50530_raidfb.json` | `prts://50530`（← 原文件 `9-15_50530.json`） | 只把 `stage_name` main_09-13 → main_09-13#f#，动作/干员原样 | prts 有真轴但跑不动：`42947`缺煌 |
| 9-13 | `main_09-11#f#` | `9-13_47229_raidfb.json` | `prts://47229`（← 原文件 `9-13_47229.json`） | 只把 `stage_name` main_09-11 → main_09-11#f#，动作/干员原样 | prts 有真轴但跑不动：`42216`缺锏 |
| 9-12 | `main_09-10#f#` | `9-12_47227_raidfb.json` | `prts://47227`（← 原文件 `9-12_47227.json`） | 只把 `stage_name` main_09-10 → main_09-10#f#，动作/干员原样 | prts 有真轴但跑不动：`76767`缺圣聆初雪 |
| 9-11 | `main_09-09#f#` | `9-11_47223_raidfb.json` | `prts://47223`（← 原文件 `9-11_47223.json`） | 只把 `stage_name` main_09-09 → main_09-09#f#，动作/干员原样 | prts 有真轴但跑不动：`76638`练度:黍 需 Lv62（你是精二Lv60） |
| 9-10 | `main_09-08#f#` | `9-10_28113_raidfb.json` | `prts://28113`（← 原文件 `9-10_28113.json`） | 只把 `stage_name` main_09-08 → main_09-08#f#，动作/干员原样 | prts 有真轴但跑不动：`76639`缺圣聆初雪 |
| 9-9 | `main_09-07#f#` | `9-9_48098_raidfb.json` | `prts://48098`（← 原文件 `9-9_48098.json`） | 只把 `stage_name` main_09-07 → main_09-07#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| 9-7 | `main_09-06#f#` | `9-7_38692_raidfb.json` | `prts://38692`（← 原文件 `9-7_38692.json`） | 只把 `stage_name` main_09-06 → main_09-06#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| 9-6 | `main_09-05#f#` | `9-6_48087_raidfb.json` | `prts://48087`（← 原文件 `9-6_48087.json`） | 只把 `stage_name` main_09-05 → main_09-05#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| 9-5 | `main_09-04#f#` | `9-5_28099_raidfb.json` | `prts://28099`（← 原文件 `9-5_28099.json`） | 只把 `stage_name` main_09-04 → main_09-04#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| 9-3 | `main_09-02#f#` | `9-3_53366_raidfb.json` | `prts://53366`（← 原文件 `9-3_53366.json`） | 只把 `stage_name` main_09-02 → main_09-02#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| 9-2 | `main_09-01#f#` | `9-2_28096_raidfb.json` | `prts://28096`（← 原文件 `9-2_28096.json`） | 只把 `stage_name` main_09-01 → main_09-01#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| 7-18 | `main_07-16#f#` | `7-18_37456_raidfb.json` | `prts://37456`（← 原文件 `7-18_37456.json`） | 只把 `stage_name` main_07-16 → main_07-16#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| 7-17 | `main_07-15#f#` | `7-17_21482_raidfb.json` | `prts://21482`（← 原文件 `7-17_21482.json`） | 只把 `stage_name` main_07-15 → main_07-15#f#，动作/干员原样 | prts 有真轴但跑不动：`76697`缺阿斯卡纶 |
| 7-16 | `main_07-14#f#` | `7-16_41698_raidfb.json` | `prts://41698`（← 原文件 `7-16_41698.json`） | 只把 `stage_name` main_07-14 → main_07-14#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| 7-15 | `main_07-13#f#` | `7-15_45149_raidfb.json` | `prts://45149`（← 原文件 `7-15_45149.json`） | 只把 `stage_name` main_07-13 → main_07-13#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| 7-12 | `main_07-10#f#` | `7-12_52587_raidfb.json` | `prts://52587`（← 原文件 `7-12_52587.json`） | 只把 `stage_name` main_07-10 → main_07-10#f#，动作/干员原样 | prts 有真轴但跑不动：`76680`缺阿斯卡纶、`24318`缺煌 |
| 7-8 | `main_07-06#f#` | `7-8_52115_raidfb.json` | `prts://52115`（← 原文件 `7-8_52115.json`） | 只把 `stage_name` main_07-06 → main_07-06#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| JT8-2 | `main_08-16#f#` | `JT8-2_71495_raidfb.json` | `prts://71495`（← 原文件 `JT8-2_71495.json`） | 只把 `stage_name` main_08-16 → main_08-16#f#，动作/干员原样 | prts 有真轴但跑不动：`76752`缺圣聆初雪练度:八幡海铃 需 Lv52（你是精零Lv1）;流明 需 Lv62（你是精二Lv60）、`45397`缺提丰 |
| R8-11 | `main_08-13#f#` | `R8-11_39446_raidfb.json` | `prts://39446`（← 原文件 `R8-11_39446.json`） | 只把 `stage_name` main_08-13 → main_08-13#f#，动作/干员原样 | prts 有真轴但跑不动：`28209`缺煌/W |
| M8-7 | `main_08-12#f#` | `M8-7_71542_raidfb.json` | `prts://71542`（← 原文件 `M8-7_71542.json`） | 只把 `stage_name` main_08-12 → main_08-12#f#，动作/干员原样 | prts 有真轴但跑不动：`76727`缺圣聆初雪 |
| R8-10 | `main_08-11#f#` | `R8-10_23177_raidfb.json` | `prts://23177`（← 原文件 `R8-10_23177.json`） | 只把 `stage_name` main_08-11 → main_08-11#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| M8-6 | `main_08-09#f#` | `M8-6_52971_raidfb.json` | `prts://52971`（← 原文件 `M8-6_52971.json`） | 只把 `stage_name` main_08-09 → main_08-09#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| R8-5 | `main_08-05#f#` | `R8-5_27240_raidfb.json` | `prts://27240`（← 原文件 `R8-5_27240.json`） | 只把 `stage_name` main_08-05 → main_08-05#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| R8-4 | `main_08-04#f#` | `R8-4_62341_raidfb.json` | `prts://62341`（← 原文件 `R8-4_62341.json`） | 只把 `stage_name` main_08-04 → main_08-04#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| R8-3 | `main_08-03#f#` | `R8-3_100189_raidfb.json` | `prts://100189`（← 原文件 `R8-3_100189.json`） | 只把 `stage_name` main_08-03 → main_08-03#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| R8-2 | `main_08-02#f#` | `R8-2_45471_raidfb.json` | `prts://45471`（← 原文件 `R8-2_45471.json`） | 只把 `stage_name` main_08-02 → main_08-02#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |
| R8-1 | `main_08-01#f#` | `R8-1_77769_raidfb.json` | `prts://77769`（← 原文件 `R8-1_77769.json`） | 只把 `stage_name` main_08-01 → main_08-01#f#，动作/干员原样 | prts 突袭槽位 **0 条**作业 |

**标红位置（三处）**：① 文件名后缀 `_raidfb`；② `doc.details` 开头 `【⚠️ 突袭兜底轴 = 普通难度轴，真打前必须自查】`；③ 索引里 `risk=raid-unverified-fallback` + `must_selfcheck=true`。

### 2.3 「prts 有真·突袭轴，但本号 box 缺口」的真轴清单

下表列出**全部被 box 挡住的真·突袭轴**（24 份、涉及 15 个 unit）。其中 5 个 unit（如 `7-14`/`9-14`/`JT8-3`）另有可用的真轴（已经换上了），练人只是多一个选择；10 个 unit（`7-12`/`7-17`/`9-10`/`9-11`/`9-12`/`9-13`/`9-15`/`JT8-2`/`M8-7`/`R8-11`）就只有这些真轴、练不出人只能用 `_raidfb` 兜底。

| 显示代号 | 真轴 prts id | 点赞 | 缺干员 | 练度不足 |
|---|---|---|---|---|
| 9-15 | `prts://42947` | 34 | 煌 | — |
| 9-14 | `prts://87486` | 1 | — | 若叶睦 需 精二（你是精零Lv1）；三角初华 需 Lv50（你是精零Lv1） |
| 9-13 | `prts://42216` | 11 | 锏 | — |
| 9-12 | `prts://76767` | 82 | 圣聆初雪 | — |
| 9-11 | `prts://76638` | 138 | — | 黍 需 Lv62（你是精二Lv60） |
| 9-10 | `prts://76639` | 25 | 圣聆初雪 | — |
| 7-17 | `prts://76697` | 11 | 阿斯卡纶 | — |
| 7-14 | `prts://50492` | 414 | 煌 | — |
| 7-14 | `prts://42703` | 131 | 煌 | — |
| 7-14 | `prts://38398` | 60 | 煌 | — |
| 7-14 | `prts://29842` | 14 | 煌 | — |
| 7-14 | `prts://27146` | 1104 | 煌 | — |
| 7-14 | `prts://25544` | 0 | 煌 | — |
| 7-14 | `prts://24327` | 43 | 煌 | — |
| 7-13 | `prts://23497` | 4 | 斥罪 | — |
| 7-12 | `prts://76680` | 99 | 阿斯卡纶 | — |
| 7-12 | `prts://24318` | 3 | 煌 | — |
| 7-12 | `prts://24311` | 0 | 煌 | — |
| 7-5 | `prts://42690` | 1 | 煌 | — |
| JT8-3 | `prts://45864` | 10 | [地面] | — |
| JT8-2 | `prts://76752` | 12 | 圣聆初雪 | 八幡海铃 需 Lv52（你是精零Lv1）；流明 需 Lv62（你是精二Lv60） |
| JT8-2 | `prts://45397` | 32 | 提丰 | — |
| R8-11 | `prts://28209` | 14 | 煌、W | — |
| M8-7 | `prts://76727` | 164 | 圣聆初雪 | — |

### 2.4 建议的自查方法（真打前必做）

1. **先演习**（首选，零成本）：`python tools/stage_attempt.py --job runtime/plans/story_jobs_big/<代号>_<id>_raidfb.json --tag <tag> --drill --raid`（突袭页下的演习：2026-09-24 CV-EX-8 实测「--drill + --raid 两份轴都跑不出」，所以演习只当**粗筛**，演习过了仍不能保证真打三星）。
2. **看面板确认难度切过去了**：`tools/bi_state.py` / 闸1 —— 突袭详情页的读数是「开始突袭」按钮 + 左下「普通难度」+「附加条件：…」；读到「开始行动」+「突袭模式」就是**没切**，别真打（`tools/stage_attempt.py --raid` 的闸1 会给 `gate1-raid-page`）。
3. **真打时把 `--raid` 传下去**：`_prime_queue3.py` 的主线路径**目前不给 stage_attempt 传 `--raid`**（`story_enter()` 只给 `main_nav` 传），`stage_attempt` 的闸1 会按「开始行动」判按钮 ⇒ 在突袭页上可能直接判失败。接索引时请对 `kind=突袭` 的 unit 补上 `--raid`。
4. **别信「演习过了」**：演习不花理智但也没结算 3 星；真打请挑理智富裕时先打 1 关验证。

## 3. 索引 `runtime/plans/story_jobs_index.json`

- 条目数：**307** 个 stage（= `story_axes_big2.json` 里带首通源石的全部单位）
- 候选条目：**932** 条（同一份作业被普通/突袭两个 unit 共用时会各列一次）
- 唯一文件：**785** = 目录里文件数 785（**一一对应，无孤儿、无重复**）

```json
{
 "_meta": {
  "schema_version": 1,
  "counts": {
   "stages": 307,
   "candidates_total": 932,
   "unique_files_referenced": 785,
   "files_in_story_jobs_big": 785,
   "alt_axes_added": 101,
   "raid_exact_files_added": 21,
   "raid_fallback_files_added": 28,
   "baseline_files_before_normalization": 635
  },
  "keys": {
   "stages.<stage_id>": "MAA 关卡代号（= data/_stage_table.json 的 stageId，突袭带 #f#）",
   "candidates": "按优先级从高到低，第 0 条=首选",
   "candidate.job_file": "仓库相对路径的作业文件（MAA copilot 直接吃这份）",
   "candidate.prts_id": "来源 prts 作业 id ⇒ prts://<id>",
   "candidate.difficulty": "本条目**服务的目标难度** normal/tough/raid/hard/sub",
   "candidate.src_mode": "exact=prts 上就是本难度；raid-exact=prts 真·突袭槽位；tough-axis-retargeted=磨难轴改 stage_name 来当普通轴；raid-fallback-retargeted=普通轴改 stage_name 来当突袭兜底；normal-axis-fallback=上游落盘的普通轴（stage_name 仍是普通）",
   "candidate.risk": "ok / ok-with-flags / cross-difficulty-unverified / raid-unverified-fallback",
   "candidate.must_selfcheck": "true ⇒ 真打前必须人工/演习自查（本条不是该难度的原生轴）",
   "candidate.origin": "替代轴的来源（原 prts id / 原难度 / 改了哪些字段）"
  }
 },
 "stages": {
  "main_13-19": {
   "code": "13-21",
   "kind": "普通",
   "chapter": 13,
   "ap": 24,
   "difficulty": "normal",
   "stage_name_in_table": true,
   "n_candidates": 3,
   "candidates": [
    {
     "job_file": "runtime/plans/story_jobs_big/13-21_28828_alt1.json",
     "prts_id": 28828,
     "difficulty": "normal",
     "src_mode": "tough-axis-retargeted",
     "risk": "cross-difficulty-unverified",
     "must_selfcheck": true,
     "note": "⚠️ 跨难度替代：磨难轴(tough_13-19) 改 stage_name 到 main_13-19(普通)，动作未改 ⇒ 真打前先演习",
     "stage_name_body": "main_13-19",
     "n_actions": 16,
     "n_deploys": 10,
     "title": "第十三章 恶兆湍流 - 13-21（磨难） - 殷红君主",
     "flags": [
      "含额外动作 ['Retreat', 'Skill']（时序敏感）"
     ],
     "origin": {
      "prts_id": 28828,
      "src_difficulty": "磨难",
      "src_stage_name": "tough_13-19",
      "dst_stage_name": "main_13-19",
      "changed_fields": [
       "stage_name",
       "doc.details"
      ]
     }
    }
   ]
  }
 }
}
```

**给 `_prime_queue3.py` 的接入口径（3 条）**：

1. **按 stage_id 取值，不要按显示代号前缀取**：同一个显示代号（如 `13-21`）同时存在 `main_13-19`（普通）与 `tough_13-19`（磨难）两个 unit，用代号 glob 会把两种难度的轴混在一起（上游 `story_jobs_for()` 就是按 `<code>_` 前缀 glob 的，会踩这个坑）。
2. **取 `candidates[0]` 当首选**，列表已按优先级排好：真·突袭轴 → 突袭兜底(`_raidfb`) → 原普通轴；普通/磨难关：替代轴(`_altN`) → 原轴。
3. **`must_selfcheck=true` 的条目不要盲打**：先 `--drill` 演习，或先人工看一眼；目前 307 关里有 136 条候选带这个标记。

## 4. 仍然需要 Lead/人工判断的（没有一处是「自动化能兜」的）

1. **34 个替代轴全部是「磨难轴改普通」的跨难度替代**，真机能不能过**未知**（先演习；尤其 13-21/13-19/13-20 这类 24 理智的长关，一次失败就是 24 理智）。
2. **28 个突袭 unit 没有原生突袭轴**（18 个 prts 上一片空白 + 10 个有真轴但本号缺人）⇒ 只能靠 `_raidfb` 兜底，属于「赌一把」。
3. **10 个突袭 unit 差干员/练度**：列表见 2.3（缺 `煌`×8、`圣聆初雪`×4、`阿斯卡纶`×2、`提丰`/`锏`/`斥罪`/`W`/`[地面]`、以及 `八幡海铃/流明/若叶睦/三角初华/黍` 的练度），要不要为这些关卡练人/换轴，是人的决定。
4. **4 份「突袭槽位但作者标仅普通」的轴**（`7-9`/`7-5`/`7-4`/`7-3` 各一份）：语义上不该当真突袭轴用。
5. **7-14 突袭**：prts 上有 8 条真突袭轴，但 7 条都点名 `煌`（本号没有），只有 1 条（`prts://41696`，点赞 7）能跑 ⇒ 首选质量偏弱，属「有轴但弱」。

## 5. 校验输出（`runtime/reports/story_norm_verify.txt` 原文）

```
1) story_jobs_big 共 785 份，JSON 解析失败 0 份 []
2) 替代轴 *_alt*.json 101 份：通过 101，问题 0 []
3) 突袭兜底 *_raidfb.json 28 份：通过 28，问题 0 []
4) 索引里标 raid-exact 的文件 30 份（本次新落盘 21 份；上游已有 9 份）
   新落盘清单：['7-10_76678.json', '7-13_42702.json', '7-14_41696.json', '7-2_76672.json', '7-3_24322.json', '7-4_24321.json', '7-5_23982.json', '7-5_24320.json', '7-6_76676.json', '7-9_24319.json', '9-14_76782.json', '9-17_39915.json', '9-17_39916.json', '9-18_76788.json', '9-4_37118.json', 'JT8-3_47402.json', 'M8-8_76731.json', 'R8-6_52449.json', 'R8-6_76701.json', 'R8-7_76704.json', 'R8-8_76705.json']
   全部 stage_name 是合法关卡代号且带 #f#：True
5) 索引：关 307 / 候选条目 932 / 唯一文件 785 / 目录 785；索引指向但不存在的文件 0；目录里有但索引没引用 0
   counts 声明：{'stages': 307, 'candidates_total': 932, 'unique_files_referenced': 785, 'files_in_story_jobs_big': 785, 'alt_axes_added': 101, 'raid_exact_files_added': 21, 'raid_fallback_files_added': 28, 'baseline_files_before_normalization': 635}
6) 随机 5 份新文件的 json 校验（json.loads + 关键字段）：
   · 10-14_28937_alt1.json  json.loads=OK  stage_name=main_10-12  合法关卡代号=True  actions=6  opers=4  Deploy=4  与来源(10-14_28937.json)动作一致=True
     doc.details=【归一化替代轴·未真机验证】stage_name 由 tough_10-12（磨难）改为 main_10-12（普通）；动作/干员原样来自 prts://28937。（跨难度替代：地图同源，敌人配置/时序可能不同）｜真打前请先演习。10-14磨难 2603
【【破碎日冕】明日方舟 主线第十章 磨难险
   · 12-3_48979_alt2.json  json.loads=OK  stage_name=main_12-02  合法关卡代号=True  actions=7  opers=1  Deploy=5  与来源(12-3_48979.json)动作一致=True
     doc.details=【归一化替代轴·未真机验证】stage_name 由 tough_12-02（磨难）改为 main_12-02（普通）；动作/干员原样来自 prts://48979。（跨难度替代：地图同源，敌人配置/时序可能不同）｜真打前请先演习。测试已过
古米精一一级
满级克洛丝
山2603 3级x模组
塞雷亚2
   · 10-6_54009_alt3.json  json.loads=OK  stage_name=main_10-05  合法关卡代号=True  actions=5  opers=4  Deploy=3  与来源(10-6_54009.json)动作一致=True
     doc.details=【归一化替代轴·未真机验证】stage_name 由 tough_10-05（磨难）改为 main_10-05（普通）；动作/干员原样来自 prts://54009。（跨难度替代：地图同源，敌人配置/时序可能不同）｜真打前请先演习。原作者：低配挂机研讨会

视频地址：https://www.bili
   · 9-12_47227_raidfb.json  json.loads=OK  stage_name=main_09-10#f#  合法关卡代号=True  actions=9  opers=3  Deploy=5  与来源(9-12_47227.json)动作一致=True
     doc.details=【⚠️ 突袭兜底轴 = 普通难度轴，真打前必须自查】【归一化替代轴·未真机验证】stage_name 由 main_09-10（普通）改为 main_09-10#f#（突袭）；动作/干员原样来自 prts://47227。本条**不是** prts 上的真·突袭轴：prts 该关突袭槽位无可跑作业。
   · 13-2_93158_alt3.json  json.loads=OK  stage_name=main_13-01  合法关卡代号=True  actions=6  opers=3  Deploy=3  与来源(13-2_93158.json)动作一致=True
     doc.details=【归一化替代轴·未真机验证】stage_name 由 tough_13-01（磨难）改为 main_13-01（普通）；动作/干员原样来自 prts://93158。（跨难度替代：地图同源，敌人配置/时序可能不同）｜真打前请先演习。作者：猫猫不想被摸
URL：https://www.bilibili
```

## 6. 顺手查出来的上游问题（本次**没有**动上游文件，只报告）

1. **突袭 unit 的 fallback 文件 `stage_name` 指向的是普通代号**（如 `9-16_46209.json` 的 `stage_name=main_09-14`）⇒ MAA 按代号选关时会走到**普通**难度；本次用 `_raidfb`（同名同内容、只把 `stage_name` 改成 `main_09-14#f#`）修掉，原文件保留不动。
2. `runtime/_prime_queue3.py::story_jobs_for()` 用 `<code>_` 前缀 glob 且取 `jobs[-1]`（**最后一份**），既会把普通/磨难的轴混选，也不等价于上游的评分首选 → 建议改为读本索引的 `candidates[0]`。
3. `story_axes_big2.json` 的 `n_candidates_on_prts` 只统计**同代号精确命中**的条数，突袭关的普通轴兜底没计入，读的时候别当成「作业站总量」。

## 7. 产物清单

| 路径 | 说明 |
|---|---|
| `runtime/plans/story_jobs_big/*_alt*.json` | 101 份替代轴（34 关） |
| `runtime/plans/story_jobs_big/*_raidfb.json` | 28 份突袭兜底（标红） |
| `runtime/plans/story_jobs_big/<代号>_<id>.json`（新） | 21 份真·突袭轴（stage_name 带 #f#） |
| `runtime/plans/story_jobs_index.json` | 机器可读索引（供 `_prime_queue3.py` 读） |
| `runtime/plans/story_norm_scan.json` | 扫描素材（34 关来源 / 47 突袭候选/缺人核对） |
| `runtime/plans/story_norm_result.json` | 归一化结果明细（每条替代轴/兜底的来源与改动） |
| `runtime/plans/_story_norm_scan.py` / `_story_norm_apply.py` / `_story_norm_verify.py` | 可复跑（幂等） |
| `runtime/reports/story_norm_verify.txt` | 校验原始输出 |
| 本文件 | 审计报告 |
