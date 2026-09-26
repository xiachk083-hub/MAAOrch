# 多游戏图像识别适配方案（终末地 / 重返未来 1999 / 棕色尘埃 2）

> 2026-08-14 立项调研：用户点名两个目标游戏。本文=生态现状 + 架构 + 路径。
> 主线仍是方舟运营（先出方案再动工铁律）。
> 2026-08-14 v2 修正：用户指正「他们俩都有类似于 MAA 的工具」——第一轮英文
> 关键词漏了中文社区 MAA 系项目，复查确认两游戏均有 MaaFramework 系适配。
> 2026-08-14 v3 定位拍板：**「主要还是我们项目，我们项目就是调配其他这些东西的」**
> —— MAAOrch = 唯一编排平台，MAA.exe / MaaEnd / M9A 都是它调配的执行引擎。
> 2026-08-14 v5：**「还是直接用他们做好的吧」**——不重造轮子，先用现成成品。
>
> **2026-09-15 v6 修正（推翻 v5 的两条前提）**。用户输入：
> 「MAA 已经是一个 general 的框架了。用 MaaFW GUI，加上 codex 写脚本理论上
> 可以适配所有 2 游。我自己就在用它跑 yj 的两个，自己还写了 BD2 的脚本自动化，
> 目前一半日常能自动完成了。」+「怎么方便怎么来，我又不传播。」
> - ① **MaaFramework 本体是 LGPL-3.0**；AGPL-3.0 的是 M9A / MaaEnd 这些
>   *应用*。v5「不直驱框架以规避 AGPL 传染」**前提错了**——防错了对象。
> - ② **本项目不分发**，许可不是选型约束 → 选型标准 = **怎么方便怎么来**。
> - 随之：适配器模型从「每引擎一个 + 逆向解析各自日志」改为
>   **一个 MaaFramework 契约适配器 + N 份 resource**（见 §2）。

## 1. 生态调研（2026-08-14 实测 + 2026-09-15 补）

| 游戏 | MAA 系工具 | 引擎 | 成熟度 |
|---|---|---|---|
| 终末地 | **`MaaEnd/MaaEnd`**（3.6k★，**Go**，活跃） | **MaaFramework 底座 + MXU（GUI 壳）+ 自研视觉 AI（Agent 机制）** —— 识别核心不是纯模板匹配，3D 实时战斗靠自研视觉 AI | 高：日常（任务/邮件/战令/材料/基建/好友/商店）、刷取（自动拾取/矩阵/农场/协议空间/送货贸易）、战斗支持（普攻/闪避/技能/大招/连携/锁敌）、便利（跳剧情/解谜/对话/拾取/传送/送礼）；PC 前台 + 安卓 ADB |
| 重返未来 1999 | **`MAA1999/M9A`**（1.9k★，**Python**，活跃） | **纯 MaaFramework 驱动**（2D 回合制模板/OCR 够用） | 高：启停游戏/荒原收集/心相分析/智能材料刷取/常规作战/活动代币/深眠/银行购物/奖励/兑换码/账号切换/活动推图/UTTU/翻斗棋等 20+ 模块；Win/Linux/macOS；AGPL-3.0 |
| **棕色尘埃 2（BD2）** | **`sunyink/MFABD2`**（MaaBD2，550★，活跃） | 纯 MaaFramework | 高：每日近 50 分钟全量日常/周常/活动；推荐 MuMu 12 + 平板 1920×1080。**另：用户本人自写了一份 BD2 脚本**——自研路径已被自己验证可达 |
| 终末地（非 MAA 系，参考） | `AliceJump/ok-end-field`（534★，ok-script） | ok-script（Windows UI 自动化+OCR/模板/HSV） | 高（任务地图可参考，非 MAA 系不优先） |

**结论（2026-09-15 加厚）**：MaaFramework 不是"将来可能有用"，而是**已成事实的通用层**。
同一份 `interface.json` / Project Interface V2 契约在跑几十个游戏：M9A、MaaEnd、
**MaaBD2**、MSBA、MaaYuan、Maa-HBR、MaaGF2Exilium、MaaTOT、MaaFgo……
「理论上可以适配所有 2 游」在生态规模上已经成立。

**通用 GUI 生态（用户说的「MaaFW GUI」）**：**MFAAvalonia**（MaaXYZ，Avalonia+SukiUI，
**多独立实例**/定时执行/启动批量跑/CLI 自启/通知（钉钉·飞书·TG·Discord·SMTP·
WxPusher·OneBot·Server酱·webhook）/PI V2 的 tasks-options-presets-pretask-i18n）
+ MFW-CFA / MXU / MWU。**注意：我们给 M9A 选的正是 `...-MFAA.zip` = MFAAvalonia 版**，
已经在用同一套壳。

## 2. 适配器模型（v6 改写）：一个契约适配器，不是每引擎一个

**为什么可以只有一个**：M9A、MaaEnd、MFAAvalonia 驱动的任何项目、用户自写的
BD2 项目，**全都是同一个 MaaFramework 项目契约**——`interface.json`（PI V2）
+ pipeline JSON + resource 目录（模板/OCR 模型）+ Agent socket + 结构化回调。
契约相同 → 适配器就该只有一个。

```
MAAOrch（编排层 — 唯一平台，游戏无关）
  ├─ 调度台/队列/实例池/模拟器状态机/恢复链/统计/EventBus（已有，不动）
  ├─ 方舟链路（不动，铁律）：注入 gui.json → MAA.exe → LogWatcher 读 asst.log
  │    ⚠️ MAA 本体**不是** MaaFramework 项目（老一代 MaaCore + WPF），
  │       869 任务的成熟度是皇冠 → 不迁、不碰
  └─ MaaFramework 契约适配器（新游戏统一走这条）
       ├─ 装 resource 包（社区成品或自研）→ 作为数据加载
       ├─ 驱动 Tasker：连 ADB/窗口 → post 任务 → **吃结构化回调**
       │   （取代"逆向解析引擎日志"）
       └─ 我方扩展以 Agent 协议挂载：Custom Recognition / Custom Action
```

对比 v5 的每引擎适配器：接手新游戏的工程量从
「写适配器（注入 + 启停 + 逆向解析该引擎日志格式）」降为
**「放一份 resource 包 + 配一个档案（客户端/模拟器/任务集）」**。

**顺带治我们的老病**：方舟链路的慢性痛点就是**靠文本解析 asst.log**——
`SubTaskCompleted` 被误判成 `TaskCompleted` 害了 35 个账号、`what`→`class`
字段漂移打断全量体力。MaaFramework 给的是**真回调协议**（生态另有
MaaLogAnalyzer）。方舟不迁，但新游戏不必重复踩这个坑。

## 3. 架构（对齐既有收敛方向）

```
编排层（游戏无关 — 已有，无需改）
  ├─ 调度台/任务参数化、launch_queue 队列+优先级+让位
  ├─ 实例池、模拟器池+状态机（EmulatorService）、恢复链
  ├─ 统计系统、账号体系（game 字段预留）、EventBus
MaaFramework 适配层（唯一，取代每引擎一个）
  ├─ 资源：每游戏一份 resource 包（引用社区包，或自研/局部改写）
  ├─ 驱动：Tasker 生命周期（连接/加载/post/停止）+ 回调 → EventBus
  └─ 扩展：ai_agent 以 Agent 协议挂载（见 §4）
方舟适配层（不动）
  └─ 现有 gui.json/tasks.json 注入体系 + LogWatcher
```

**进程隔离建议**：MaaFramework 是 C++ 核心 + 绑定，**别塞进 runner 主进程**
（引擎崩了别带崩编排器）。放**我们自己的 sidecar 进程**里驱动 —— 协议我们自己定，
不比自己写适配器多花工，却保住了现有"MAA.exe 崩了不影响编排器"的性质。

## 4. 与已有工作的汇合（v6 新增，最值钱的一条）

**① `services/ai_agent/` 就是 MaaFramework 的 Custom Recognition / Custom Action。**
它能干的事（percept / battle_vision / actuator / hud / tile_map）正是框架
Agent 机制要做的事，现在却焊死在我们自己的 runner 里、只服务方舟。按框架
协议重包装（Python：`@AgentServer.custom_recognition("X")` /
`@AgentServer.custom_action("X")` + `AgentServer.start_up(socket_id)`，
pipeline 侧 `"recognition":"Custom","custom_recognition":"X"`），同一套感知/执行
能力就能被**任意 MaaFramework 游戏**的 pipeline 节点调用，不必每游戏重写。
⚠️ 已知坑：agent 模式下每次回调无条件 `get_task_detail`，开销随节点数 O(N)
退化（Issue #1118，长跑会从毫秒掉到秒级）——长时任务要留意。

**② 我们早就建好了「LLM 写脚本」的地基。**
`tools/vision_translate.py`（模板图 → 文本喂 LLM）、`services/task_explainer.py`
（问答/生成任务定义）、`tools/task_graph.py`（链路可视化）——这就是用户说的
「codex 写脚本」的同构物，只是目标 schema 是 MAA 的 `tasks.json`。改映射到
MaaFramework 的 Pipeline JSON（后者更简单）**是映射工作量，不是新能力**。

## 5. 战略提醒：护城河不在脚本层

MFAAvalonia 已自带多实例管理、定时执行、批量启动、CLI 自启、通知 webhook，
与 MAAOrch 编排层**部分重叠**。所以：

- **每游戏脚本**已被框架 + 通用 GUI + LLM 作者化**商品化**——不要在这块竞争。
- MAAOrch 真正的价值是**机队运维控制面**：48+ 账号的实例池、模拟器状态机、
  回收/恢复链、断点续做、统计账房、挂起审计。生态里没有一个提供这个。
  力气压在这里。

## 6. 风险与铁律

- ~~License（AGPL 传染）~~ **不再是约束（v6）**：MaaFramework = LGPL-3.0，
  M9A/MaaEnd = AGPL-3.0 但**本项目不分发**（用户：「我又不传播」）。
  选型标准 = 怎么方便怎么来；引用/改写他方资源无需再为许可绕路。
- 方舟链路零改动（每阶段验收第一项；MAA 不是 MaaFramework 项目，不迁）
- 外部辅助不碰内存/注入（[[maa-plugin-vs-auxiliary]] B 路线）
- 跟随引擎最新版（[[maa-autoupdate-retry]] 教训）
- 适配器隔离：新增游戏不碰方舟现有链路（[[dev-prod-topology]]）
- 识别判定可审计（截图/置信度留痕）
- 定位：MAAOrch 只做账号与调度层，游戏侧各自带自己的壳（MFAAvalonia / MXU 等）

## 7. 待拍板 / 待核实

- 阶段 1 游戏：1999（M9A，resource 包现成，推荐）vs 终末地（MaaEnd 更大）
- 立项时机：用户 2026-08-15「先把方舟的搞好理清」→ 方舟稳定前多游戏不动工
- ~~待核实：MaaEnd 是否真无 `interface.json`~~ → **已核实（2026-09-16）：有**，
  PI V2 `interface_version: 2`，任务由 `import` 拆分；原文记载系误记。
  另：其控制器以 Win32 为主（非 ADB），真接时按 Win32 设计接入面
