# 多游戏调配平台 — 未来规划路线图

> 2026-08-14 规划（用户「给未来做做做规划」）。定位已拍板：MAAOrch = 唯一
> 调配平台，直接调配成品引擎（MAA.exe / M9A / MaaEnd），黑盒进程，不做引擎级
> 开发。本文 = 从现状到多游戏平台的完整路线（每阶段独立可交付、可验证、
> 可回退）。**未动工**（先出方案铁律；主线仍是方舟运营）。

## 蓝图

```
                    MAAOrch（调配平台 — 皇帝）
   ┌────────────────┼────────────────┐
 调度台/队列       统计/账房        界面/审计
   │                │                │
   └───────── 引擎适配器层 ──────────┘
      maa_arknights   maa_r1999    maa_endfield
      （已有，不动）   （M9A）       （MaaEnd）
```

## 阶段 0：数据模型准备（游戏字段拆分）

账号/任务体系加「游戏」维度，**方舟行为零变化**：

```
account:
  game: "arknights" | "r1999" | "endfield"   ← 新增，决定用哪个适配器
  client_type: "Official" | "Bilibili" | "YoStarJP"  ← 现有 game_client 改名
```

- 迁移：现有 `game_client` 值（Official/Bilibili/YoStarJP）→ 映射
  `game="arknights"` + `client_type=原值`（存量账号一次性脚本）
- 分流点预留（本阶段只加字段不动逻辑）：队列 enqueue 按 game 选适配器、
  统计按 game 分表、UI 账号表「游戏」列/分组
- 验收：方舟全链路回归正常；game 字段在所有账号 = "arknights"

## 阶段 1：适配器壳抽象（引擎无关骨架）

把 runner 的「注入→启动→事件解析」抽成引擎适配器接口（**方舟现有链路
不迁移**，新壳旁路先行）：

```python
class EngineAdapter:            # 抽象接口
    def inject(self, task: dict, ac: dict) -> None: ...   # 配置注入
    def launch(self, ac: dict) -> Popen: ...              # 启动进程
    def stop(self, proc) -> None: ...                     # 停止（复用优雅链）
    def parse(self, line: str, aid: str) -> str | None: ...  # 日志→事件类型
```

- 产出：接口 + 文档 + 假引擎样例（验证骨架，不接真实引擎）
- 与单点收敛衔接：事件出口统一 EventBus（[[convergence-architecture-direction]]）
- 验收：骨架可用、方舟链路未动

## 阶段 2：试点 — 重返未来 1999（M9A）

**为什么 1999 先行**：M9A 是**标准 MaaFramework 项目**（根目录有
`interface.json` + `maa-project.json` + `tasks/` + `resource/` + `agent/`，
PI V2 协议入口，2026-08-13 仍在更新）——接入面清晰，Python 同族，
日志/事件可解析性强。

- 适配器 `maa_r1999`：
  1. **注入**：任务参数 → interface.json / M9A 配置目录（JSON）
  2. **启动**：M9A Python 入口进程（复用 runner 启停/恢复链骨架）
  3. **事件解析**：M9A 日志 → EventBus（completed/failed/体力/掉落信号）
- 最小闭环：1-2 个账号清日常/清体力，调度台可见、完成判定正确、统计落库
- 引擎接入面实测：M9A 的 CLI/配置注入点、日志格式、完成信号
- 验收：调度台跑通 1999 任务；方舟链路零影响

## 阶段 3：终末地（MaaEnd）

**与 M9A 不同**：MaaEnd **无 interface.json**（自研 Agent 结构 +
MaaFramework 底座 + MXU GUI），接入面需实测（可能走自身启动器/CLI/
配置目录），3D 实时战斗任务完全由 MaaEnd 内部视觉 AI 自理——我们只调配。

- 适配器 `maa_endfield`：注入（MaaEnd 配置格式）→ 启动 → 日志解析
- 先做固定界面任务（日常/商店/基建），战斗任务跟随 MaaEnd 能力
- 验收：1999 + 终末地双游戏可跑；方舟不受影响

## 阶段 4：编排层游戏分流

- 队列：enqueue 按 `game` 选适配器；让位/优先级规则按 game 独立配置
- 统计/账房：按 game 换（方舟 sanity_tracker vs 1999/终末地体力机制不同）
- 恢复链：适配器级恢复（引擎进程死了按 game 规则重启）
- UI：账号表按游戏分组/筛选；调度台任务卡显示游戏徽标
- 验收：三游戏并行（方舟 49 号 + 1999 + 终末地）互不干扰

## 长期演进（有意图无排期）

- **新游戏接入 = 复制适配器壳 + 解析新引擎格式**，平台本体不再改
- 识别能力服务化（OCR/截图/模板匹配独立服务）= 5 方向计划第 4 项
  「辅助能力框架」（[[maa-plugin-vs-auxiliary]]），游戏无关，方舟也受益
- 多机编排（node_id 已预留，[[emulator-service-state-machine]]）
- 账号体系扩展：一账号多游戏、同引擎多区服

## 风险与护栏

1. **方舟链路零改动**（每阶段验收第一项；[[dev-prod-topology]] 生产敬畏）
2. **AGPL-3.0**（M9A/MaaEnd 均 AGPL）：只写配置文件、不 import 对方代码；
   资源文件（JSON/模板）引用需确认许可证边界
3. **引擎跟随最新版**（[[maa-autoupdate-retry]] 教训；两引擎均活跃更新）
4. 引擎能力缺口由引擎自己补（MaaEnd 视觉 AI 即例），我们只验收成品
5. 每个阶段独立部署、独立回退；不搞一次性大重构

## 与现有工作的衔接

- 单点收敛（EventBus/账房收口）先行 → 适配器事件出口自然统一
- 阶段 0 的 game 字段拆分依赖账号体系（Account dataclass）稳定
- 部署运维（deploy.py 清单）每阶段扩展新模块
