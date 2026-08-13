# 多游戏图像识别适配方案（终末地 / 重返未来 1999）

> 2026-08-14 立项调研：用户点名两个目标游戏。本文=生态现状 + 架构 + 路径，
> **未动工**（先出方案再动工铁律）。主线仍是方舟运营。
> 2026-08-14 v2 修正：用户指正「他们俩都有类似于 MAA 的工具」——第一轮英文
> 关键词漏了中文社区 MAA 系项目，复查确认两游戏均有 MaaFramework 系适配。

## 1. 生态调研（2026-08-14 实测 GitHub，v2 修正）

| 游戏 | MAA 系工具 | 引擎 | 成熟度 |
|---|---|---|---|
| 终末地 | **`MaaEnd/MaaEnd`**（3.6k★，**Go**，4h 前仍在更新） | **MaaFramework & MXU**，视觉 AI | 高：日常（任务/邮件/战令/材料/基建/好友/商店）、刷取（自动拾取/矩阵/农场/协议空间/送货贸易）、战斗支持（普攻/闪避/技能/大招/连携/锁敌）、便利（跳剧情/解谜/对话/拾取/传送/送礼）；PC 前台 + 安卓 ADB |
| 重返未来 1999 | **`MAA1999/M9A`**（1.9k★，**Python**，1816 commits） | **MaaFramework 强力驱动**，图像技术+模拟控制 | 高：启停游戏/荒原收集/心相分析/智能材料刷取/常规战斗/活动代币/深眠/银行商店/奖励/兑换码/账号切换/活动关卡；Win/Linux/macOS + 模拟器/PC 客户端连接；AGPL-3.0 |
| 终末地（非 MAA 系，参考） | `AliceJump/ok-end-field`（534★，Python/ok-script） | ok-script（Windows UI 自动化+OCR/模板匹配/HSV） | 高（任务地图可参考，非 MAA 系不优先） |

**结论：MaaFramework 对两个游戏都有成熟社区适配**（MaaEnd + M9A），
原方向「执行引擎优先评估 MaaFramework」**成立**，无需自研识别框架。

## 2. 集成方式选项（关键决策）

M9A/MaaEnd 是**完整助手应用**（自有 UI/配置/调度），不是 asst.py 那种
引擎库——与方舟「MAAOrch 注入配置驱动 MAA.exe」的集成模式不完全同构。
三个选项：

| 选项 | 做法 | 成本 | 适配度 |
|---|---|---|---|
| A. 进程级黑盒 | MAAOrch 当执行引擎管理：配置注入（JSON→其配置目录）→ 启动进程 → 日志/心跳监控 → 完成判定（其日志事件） | 低（复用方舟管线：队列/实例/恢复链只换启动器和事件解析） | 快落地；但两个工具各自配置格式，隔离层要做两套解析 |
| B. 引擎级复用（推荐） | 用 **MaaFramework Python 绑定**（与 asst.py 同族）直驱，**复用 M9A/MaaEnd 的 task JSON/资源**（识别模板、任务链） | 中（MaaFramework 绑定一次，两游戏共享） | 架构最干净：MAAOrch 编排层 + MaaFramework 引擎层 + 游戏 task 资源层，与方舟同构 |
| C. 深度集成 | 直接调 M9A 的 Python 模块（import 其任务定义） | 低-中 | 耦合对方代码结构，升级/AGPL 传染风险（M9A/MaaEnd 均 AGPL-3.0） |

**推荐 B**：MaaFramework 是 MAA 团队通用框架（C++ 核心 + Python 绑定），
M9A/MaaEnd 的 task 资源是 JSON（识别模板/任务链），与方舟 tasks.json
同构（[[maa-task-intelligence-toolchain]] 已摸过 schema）——MAAOrch 的
任务洞察工具链（task_graph/vision_translate）直接可复用。

## 3. 架构（对齐既有收敛方向）

```
编排层（游戏无关 — 已有，无需改）
  ├─ 调度台/任务参数化、launch_queue 队列+优先级+让位
  ├─ 实例池、模拟器池+状态机（EmulatorService）、恢复链
  ├─ 统计系统、账号体系（game_client 字段已预留）、EventBus
引擎层（MaaFramework — 统一，新引入）
  └─ Python 绑定（与 asst.py 同族）— 方舟/终末地/1999 共用
游戏资源层（task JSON/模板 — 每个游戏一份，复用社区适配）
  ├─ 方舟：现有 gui.json/tasks.json 注入体系（不动）
  ├─ 终末地：MaaEnd 的 task 资源（转换/引用）
  └─ 1999：M9A 的 task 资源（转换/引用）
适配层（每游戏一个，薄）
  ├─ task_defs   任务定义映射（MAAOrch 任务 ↔ 游戏 task 链）
  ├─ events      完成判定（游戏日志/回调 → EventBus）
  └─ controller  连接（模拟器 adb / PC 客户端窗口）
```

## 4. 推荐路径（试点先行）

- **阶段 0（铺垫，游戏无关，方舟也受益）**：MaaFramework Python 绑定
  评估（与 asst.py 的差异/多实例能力）——本机验证，零生产影响。
- **阶段 1（试点）**：**1999 先行**——M9A 是 Python（技术栈同族），
  任务资源直接可读；最小闭环=清日常/清体力验证「编排→MaaFramework→
  判定→统计」管线。
- **阶段 2**：终末地（MaaEnd Go 核心，task 资源 JSON 仍可复用；
  3D 识别复杂度高，先固定界面任务）。
- **阶段 3**：编排层按 `game_client` 分流（队列/统计/恢复链），
  方舟链路不动。

## 5. 风险与铁律

- **License**：M9A/MaaEnd 均 **AGPL-3.0** —— 引 task 资源（JSON 数据/
  模板）一般不传染，直接 import 代码则有义务开源；选项 B（资源引用）
  规避代码传染，需确认。
- 外部辅助不碰内存/注入（[[maa-plugin-vs-auxiliary]] B 路线）
- 跟随引擎最新版（[[maa-autoupdate-retry]] 教训；MaaEnd 4h 前更新=活跃）
- 适配器隔离：新增游戏不碰方舟现有链路（[[dev-prod-topology]]）
- 识别判定可审计（截图/置信度留痕）

## 6. 待拍板

- 集成方式 A / B / C（推荐 B）
- 阶段 1 游戏：1999（Python 同族，推荐）vs 终末地（MaaEnd 更大）
- 立项时机：方舟运营稳定后 vs 并行
