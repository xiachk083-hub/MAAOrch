# 多游戏调配平台 — 未来规划路线图

> 2026-08-14 规划（用户「给未来做做做规划」）。定位已拍板：MAAOrch = 唯一
> 调配平台，直接调配成品引擎（MAA.exe / M9A / MaaEnd），黑盒进程，不做引擎级
> 开发（**以上为 v5 措辞，v6 修正见下**）。本文 = 从现状到多游戏平台的完整路线
> （每阶段独立可交付、可验证、可回退）。**未动工**（先出方案铁律；主线仍是方舟运营）。
>
> **2026-09-15 v6 修正**（详见 docs/MULTI_GAME_VISION.md §2）：引擎不是"三个
> 各自独立的成品"，而是**同一个 MaaFramework 项目契约**（M9A / MaaEnd /
> MFAAvalonia 驱动的任何项目 / 自写脚本同契约）→ 阶段 1-3 的「每引擎一个
> 适配器 + 逆向解析各自日志格式」改为**一个契约适配器 + N 份 resource**；
> 原「AGPL-3.0 护栏」作废（框架本体是 LGPL-3.0，且本项目**不分发**，
> 用户「怎么方便怎么来，我又不传播」）。方舟链路（MAA.exe，**不是**
> MaaFramework 项目）依旧零改动。

## 蓝图

```
                    MAAOrch（调配平台 — 皇帝）
   ┌────────────────┼────────────────┐
 调度台/队列       统计/账房        界面/审计
   │                │                │
   └───────── 引擎适配器层 ──────────┘
      maa_arknights        maafw_contract
      （已有，不动）        （M9A / MaaEnd / BD2 / 自写项目 统一走这条）
```

## 阶段 0：数据模型准备（游戏字段拆分）

账号/任务体系加「游戏」维度，**方舟行为零变化**：

```
account:
  game: "arknights" | "r1999" | "endfield"   ← 新增，决定用哪个适配器/资源包
  client_type: "Official" | "Bilibili" | "YoStarJP"  ← 现有 game_client 改名
```

- 迁移：现有 `game_client` 值（Official/Bilibili/YoStarJP）→ 映射
  `game="arknights"` + `client_type=原值`（存量账号一次性脚本）
- 分流点预留（本阶段只加字段不动逻辑）：队列 enqueue 按 game 选适配器、
  统计按 game 分表、UI 账号表「游戏」列/分组
- 验收：方舟全链路回归正常；game 字段在所有账号 = "arknights"

## 阶段 1：契约适配器（唯一新适配器，游戏无关）

v5 原设计是「抽一个通用 `EngineAdapter`，每引擎一个实现，靠 `parse()` 逆向
解析各自日志」。v6 改为：**只有一个**契约适配器，对齐 MaaFramework 的项目
契约——**事件来自结构化回调，不是日志文本**。（方舟现有链路不迁移，旁路先行）

```python
class MaafwAdapter:            # 唯一新适配器（覆盖所有 MaaFramework 项目）
    def load(self, resource: Path) -> None: ...   # 装 resource 包（社区成品/自研）
    def attach(self, ac: dict) -> None: ...       # 连 controller（ADB / 桌面窗口）
    def post(self, task: str) -> str: ...         # post 任务 → task_id
    def on_callback(self, cb: dict) -> str: ...   # 框架结构化回调 → 事件类型
    def stop(self) -> None: ...                   # 停止（复用优雅链）
```

注意与 v5 的关键差别：**没有 `parse(line)`** —— 不再有人去猜引擎的日志格式。

- 产出：适配器 + 文档 + 假 resource 样例（验证骨架，不接真实引擎）
- **进程隔离**：MaaFramework 是 C++ 核心 + 绑定，放**我们自己的 sidecar 进程**
  驱动，别塞进 runner 主进程（引擎崩了别带崩编排器，保住现有性质）
- 与单点收敛衔接：事件出口统一 EventBus（[[convergence-architecture-direction]]）
- 验收：骨架可用、方舟链路未动

## 阶段 2：试点 — 重返未来 1999（M9A）

**为什么 1999 先行**：M9A 是**标准 MaaFramework 项目**（根目录有
`interface.json` + `maa-project.json` + `tasks/` + `resource/` + `agent/`，
PI V2 协议入口，2026-08-13 仍在更新）——接入面清晰，Python 同族，
日志/事件可解析性强。

- 接入 = 吃 M9A 的 **resource 包**（pipeline JSON + 模板 + OCR 模型），由阶段 1
  的契约适配器直接加载驱动；M9A 的自定义 Python 逻辑若被 pipeline 引用，
  按 Agent 协议挂上（**不逐行逆向它的日志**）
- 最小闭环：1-2 个账号清日常/清体力，调度台可见、完成判定正确、统计落库
- 引擎接入面实测：resource 包结构 + 需要的 Agent 自定义节点清单
- 验收：调度台跑通 1999 任务；方舟链路零影响

## 阶段 3：终末地（MaaEnd）

**与 M9A 不同**：MaaEnd **有 interface.json**（标准 PI V2，任务拆成 35 个
`import`），同为 MaaFramework 底座 + MXU GUI；3D 实时战斗任务完全由 MaaEnd
内部视觉 AI 自理——我们只调配。**约束：控制器以 Win32 为主，不是 ADB/模拟器链路。**

- 接入 = 吃 MaaEnd 的 resource 包；其自研视觉 AI 以 **Agent 协议**挂载
  （3D 实时战斗能力由 MaaEnd 自理，我们只调配）
- 先做固定界面任务（日常/商店/基建），战斗任务跟随 MaaEnd 能力
- ~~待核实：MaaEnd 是否真无 `interface.json`~~ → **已核实（2026-09-16）：有**，
  `interface_version: 2`，任务拆成 35 个 `import`；原文"无 interface.json"系误记
- 验收：1999 + 终末地双游戏可跑；方舟不受影响

## 阶段 4：编排层游戏分流

- 队列：enqueue 按 `game` 选适配器；让位/优先级规则按 game 独立配置
- 统计/账房：按 game 换（方舟 sanity_tracker vs 1999/终末地体力机制不同）
- 恢复链：适配器级恢复（引擎进程死了按 game 规则重启）
- UI：账号表按游戏分组/筛选；调度台任务卡显示游戏徽标
- 验收：三游戏并行（方舟 49 号 + 1999 + 终末地）互不干扰

## 长期演进（有意图无排期）

- **新游戏接入 = 放一份 resource 包 + 配一个档案**（不写代码、不逆向日志），
  平台本体不再改
- **我方能力可反哺所有游戏**：`services/ai_agent/`（percept/actuator/…）按
  Custom Recognition / Custom Action 包装后，任意 MaaFramework 项目都能调用
  （见 docs/MULTI_GAME_VISION.md §4；注意 Agent 模式 Issue #1118 的 O(N) 退化）
- **LLM 写脚本的既有地基**：vision_translate + task_explainer + task_graph
  从 MAA `tasks.json` 映射到 Pipeline JSON（更简单）即可，非新能力
- 识别能力服务化（OCR/截图/模板匹配独立服务）= 5 方向计划第 4 项
  「辅助能力框架」（[[maa-plugin-vs-auxiliary]]），游戏无关，方舟也受益
- 多机编排（node_id 已预留，[[emulator-service-state-machine]]）
- 账号体系扩展：一账号多游戏、同引擎多区服

## 风险与护栏

1. **方舟链路零改动**（每阶段验收第一项；[[dev-prod-topology]] 生产敬畏）
2. ~~**AGPL-3.0 传染**~~ **作废（v6）**：MaaFramework 本体是 **LGPL-3.0**；
   M9A/MaaEnd 是 AGPL-3.0 但**本项目不分发**（用户「我又不传播」）→
   许可不约束选型，标准 = 怎么方便怎么来；引用/改写他方 resource 不必绕路
3. **引擎跟随最新版**（[[maa-autoupdate-retry]] 教训；两引擎均活跃更新）
4. 引擎能力缺口由引擎自己补（MaaEnd 视觉 AI 即例），我们只验收成品
5. 每个阶段独立部署、独立回退；不搞一次性大重构

## 与现有工作的衔接

- 单点收敛（EventBus/账房收口）先行 → 适配器事件出口自然统一
- 阶段 0 的 game 字段拆分依赖账号体系（Account dataclass）稳定
- 部署运维（deploy.py 清单）每阶段扩展新模块
