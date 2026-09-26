# 练度台账：专精 / 模组的可见性

> 2026-09-15。起因：用户指出「MAA 这个查看干员，他并不能看模组和技能的情况」——
> 这正是"筛选出来的作业看着能跑、一打伤害不够"的根因。

## 1. MAA 能看到什么、看不到什么

| 数据 | MAA 的「干员识别」(OperBox) | 抄作业编队那一刻 |
|---|---|---|
| 持有 / 星级 / 精英化 / 等级 / 潜能 | ✅ 有 | ✅ 有 |
| 技能专精（skill_level 8/9/10 = 专一/二/三） | ❌ 没有 | ✅ **有**（校验后写日志） |
| 模组（module 1/2/3） | ❌ 没有 | ✅ **有**（校验后写日志） |

作业站的要求恰恰按后两项写（例：`{"skill_level":10,"module":2}`）。所以只靠干员盒，
筛选器永远只能标"未核专精/模组"——2026-09-15 的 SR-EX-5 首选 102533 就是这么被选出来的：
它要求 5 个干员**全员专三+模组**，而号里一个都没有。

编队那一刻 MAA 会把这些写进日志（这是**唯一的本地真值来源**）：

```
gui.log:   干员不可用: 歌蕾蒂娅, 原因: 技能等级不足        ← 技能/模组/精英化/等级
gui.log:   以下干员组内的干员均未拥有（每组只需其一）或未达到作业要求：
           圣聆初雪                                        ← 失败的干员逐个列行
           奶盾=> 黍 / 塞雷娅                               ← 失败的组：组名=> 候选
asst.log:  SubTaskError {"details":{"opers":{"令":[{"name":"令","reason":"Missing"}]}}}
```

## 2. 工具与用法

```bash
# 扫日志建台账（默认扫两个 MAA 安装目录 + 项目实例）
python tools/maa_mastery_ledger.py
# 产出 data/mastery_ledger.json：by_op / by_account(端口/服) / by_group / entries

# 筛选作业时带上台账（硬信号进排序，提示只显示）
python tools/_event_user_pick.py --event act54side --letter SR \
    --operbox data/operbox_pasted_0915f.json --ledger-port 16416 \
    --out data/copilot/sr_ex05_ledger_pick.json
```

台账口径：

- **硬信号**（进排序，真会毁掉这一把）
  - 作业要 `skill_level ≥ 8` 而台账记过该干员「技能等级不足」→ 专精存疑
  - 作业要 `module > 0` 而台账记过该干员「所需模组未解锁」→ 模组实锤
  - 台账记过「未拥有」而干员盒说持有 → **干员盒误报**（MAA 都找不到这张卡）
- **只提示**：「等级不足 / 精英化不足」是**相对某份旧作业**的判定，作业间门槛不同，
  拿来当通用短板会把整张表标红（2026-09-15 实测 20 份 SR-EX-5 作业全红），所以只显示。

号归属：gui.log 每段运行前会打 `Already connected to ...127.0.0.1:<端口>` 与
`"client_type":"<服>"`，据此把证据归到 `端口/服`（如 `16416/Official`）。同一端口换号
的时段靠时间戳区分——它只保证"这台机器这个端口上这些干员不达标"，够用了。

## 3. 实测：16416 号（SR 那台）的练度画像

378 条证据里属于该号的（只列 ≥1 次的技能/模组项）：

| 干员 | 技能等级不足 | 所需模组未解锁 |
|---|---|---|
| 歌蕾蒂娅 | 17 | 4 |
| 黍 | 14 | 4 |
| 娜仁图亚 | 8 | 4 |
| 阿斯卡纶 | 8 | 2 |
| 乌尔比安 | 6 | 4 |
| 结城理 | 6 | 7 |
| 凛御银灰 / 维什戴尔 / 蕾缪安 | 4 | 0 / 0 / 3 |
| 赤刃明霄陈 / Mon3tr | 0 | 3 |
| 芬 / 酒神 / 古米 / 月见夜 | 1~3 | 0 |

这就解释了 SR 系列"能跑但伤害不够"：核心干员普遍缺专精，结城理/乌尔比安/歌蕾蒂娅/黍
还缺模组。

## 4. 图像识别路线（2026-09-15 起，替代森空岛）

森空岛官方接口被否掉（拿不全：代肝客户号不该碰登录态、B 服/无森空岛的号也没有）。
专精/模组在界面上**本身就是小图标**，MAA 自己的模板就是它们 —— 直接模板匹配即可。

### 4.1 标记语义（在 MAA `resource/template/Battle/Formation/BattleQuickFormation/`）

| 模板 | 含义 | 尺寸 |
|---|---|---|
| `SkillLevel-07` | 数字 **7** = 技能 7 级 | 18×17 |
| `SkillLevel-08 / 09 / 10` | **1 / 2 / 3 个亮点 = 专一 / 专二 / 专三** | 18×17~18 |
| `SkillLevel-Base` | 闪电（技能图标本体，未显示等级时） | 15×21 |
| `Module-0` | 「ORIGINAL」框 = 未装模组 | 53×32 |
| `Module-1 / 2-A / 3 / 4` | 模组形状 **X / Y / α / Δ** | ~16×17 |
| `Elite1 / Elite2` | 精一 / 精二 | 17×32 |

### 4.2 在哪个界面能读到（实机定位，1280×720）

- **技能专精**：✅ 在「快速编队选人页」**选中干员的左侧详情面板** —— 每个技能一个技能图标（x≈59），
  等级标记在其下方 ~60px（x≈9）。点一个干员读一帧，约 1 秒。
  **卡片上的技能标记不可靠**（同一帧只零星命中），别用它做全量扫描。
- **精英化**：✅ 在卡片左上角（选人页/编队页都行），一帧可读 8~12 张卡。
- **模组**：❌ 不在选人页（多尺度全库截图扫描 ≥0.90：0 命中）→ 需在「干员详情 → 模组」页另做标定。
- **干员名 OCR**：❌ 暂时不行。通用中文 OCR 本项目已知偏弱（`凯尔希` → 「小吞」）；
  MAA 的专用 `PaddleCharOCR` 我们的预处理没对齐它的 C++（输出 `talltait`）。
  临时办法：人工点名（`--op <名>`）/「你点谁我记谁」。

### 4.3 用法

```bash
python tools/mastery_scan.py --image logs/agent/sr1_squad.png      # 离线：拿现成截图校准
python tools/mastery_scan.py --emu 2                                # 实时：adb 抓一帧（只读，可与 MAA 并行）
python tools/mastery_scan.py --emu 2 --panel                        # 读当前选中干员的每个技能专精
python tools/mastery_scan.py --emu 2 --watch 120                    # 盯屏 2 分钟，攒证据
```

产出 `data/mastery_scan/<tag>.json` + `<tag>_annotated.png`（标注图，人眼一秒核对）。

### 4.4 不依赖识别的补充手段：MAA 编队校验当「探针」

造一份只含目标干员、要求拉满的作业（`skill_level:10, module:4, elite:2, level:90`），
MAA 编队时会逐人报「技能等级不足 / 所需模组未解锁 / 精英化不足 / 等级不足」——
换不同门槛跑两次就能把专精定位到区间（<7 / 7 / ≥专一 / ≥专二 / ≥专三）。
这条不需要任何新 OCR，今天的台账就是它的副产品（见 §3）。

## 5. 根治方向：森空岛（Skland）官方接口（已被否，留档）


MAA 看不到专精/模组，是它只读界面；**官方接口有真值**：

- 数据：`GET https://zonai.skland.com/api/v1/game/player/info?uid=<uid>`，
  头 `cred` + `sign` + 时间戳；返回 `data.chars` 里每个干员带
  `evolvePhase / level / potentialRank / skills[].specializeLevel / modules[].level`
  —— 正好是作业站要的两个字段。
- 签名：`sign = MD5( HMAC_SHA256(key=cred 接口返回的 token, msg=路径+query+timestamp+四个设备头JSON) )`。
- 登录：2024-09 起鹰角在 Cred 端点加了阿里云 WAF + 数美 `dId`，登录流程要借现成方案
  （见下 Sources）；**数据接口本身没变**。拿一次 cred 可以长期用。

结论：**自有的号**值得接一次（专精/模组变硬判，首选作业再不会挑到练度不够的）；
**代肝的客户号**不碰登录态（见项目既有原则），只能走台账这条近似路线。

## Sources

- [ProbiusOfficial/Skland_API](https://github.com/ProbiusOfficial/Skland_API) — 接口与字段（已停止维护，字段名可能变）
- [YueHen14/skyland-auto-sign](https://github.com/YueHen14/skyland-auto-sign) — sign 签名算法
- [GuGuMur/nonebot-plugin-skland-arksign](https://github.com/GuGuMur/nonebot-plugin-skland-arksign) — 2025 年仍在维护，含 dId 绕过的三种方案
