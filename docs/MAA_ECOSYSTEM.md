# MAA 生态调研（官网/社区项目 + 选型建议）

> 调研时间：2026-08-12
> 用途：回答「MAA 官网有没有其他项目可以做」——即我们将来自己拿 MAA 做事时，有哪些现成项目/框架/协议可用

## 1. 官方资产

| 项目 | 定位 | 对我们有什么用 |
|------|------|----------------|
| **MaaAssistantArknights**（主仓库） | MAA 本体（C++ 核心 MaaCore + WPF 前端），AGPL-3.0 | 我们正在用的就是这个（v6.16） |
| **docs.maa.plus** | 官方文档站（协议/接口/开发指南） | **任务机制手册的权威来源**（见 MAA_TASK_MECHANICS.md） |
| **maa.plus**（maa-website） | 官网前端 | 发布/下载入口 |
| **MaaDownloadServer** | 官方更新服务器 | 我们下载 MAA 的镜像来源之一 |

### 官方协议文档（docs.maa.plus，JSON 规范，可直接参考）

| 协议 | 内容 | 相关代码 |
|------|------|----------|
| 任务流程协议 | 任务 JSON 定义（tasks.json schema） | `config_injector.py` 的注入逻辑 |
| 回调消息协议 | MAA 运行时回调事件（TaskStart/AllTasksCompleted 等） | `log_watcher.py` 的事件流 |
| 集成文档 | C/Python/Java/Rust 等语言 SDK 集成 | `stage_probe.py` 的 Asst 子进程 |
| 自动战斗协议 | Copilot 抄作业 JSON | — |
| 基建排班协议 | 基建换班 JSON | — |

## 2. MAA 团队的其他项目（你问的"其他项目"）

| 项目 | 定位 | 什么时候用 |
|------|------|-----------|
| **MaaFramework** | MAA 团队（MaaXYZ）出的**通用自动化框架**：C++20 图像识别黑盒自动化，**LGPL-3.0**，约 4.4-4.8k★。不是明日方舟专用，可套任何 2D 游戏/应用。绑定齐全：PyPI `MaaFw` / NuGet / npm `@maaxyz/maa-node` / Go / Rust | **"自己拿 MAA 做自己的事情"的首选；2026-09-15 起 = 新游戏的默认路径**。一个 `interface.json`（PI V2）+ pipeline + resource 契约在跑几十个游戏：M9A / MaaEnd / **MaaBD2** / MSBA / MaaYuan / Maa-HBR / MaaGF2Exilium / MaaTOT / MaaFgo… 文档 maafw.com |
| **MFAAvalonia** | MaaFramework 的**跨平台通用桌面 GUI**（Avalonia+SukiUI，本身不带业务资源，靠项目的 `interface.json` 驱动） | **我们给 M9A 选的就是它的发行版（M9A 的 `...-MFAA.zip`）**。多独立实例 / 定时执行 / 启动批量跑 / CLI 自启 / 通知（钉钉·飞书·TG·Discord·SMTP·WxPusher·OneBot·Server酱·webhook）/ PI V2 tasks-options-presets-pretask-i18n。⚠️ **与 MAAOrch 编排层部分重叠** → 别重复造；护城河在机队运维控制面（见 MULTI_GAME_VISION.md §5） |
| **MaaAI** | MAA 的深度学习项目（官方方向） | 如果将来真要训练（识别/决策模型），这是官方配套路线 |
| **maa-cli** | 跨平台命令行版 MAA | 轻量部署/脚本化场景 |
| MaaPi / 第三方插件 | 社区插件生态（公招识别、仓库截图识别等） | 按需借鉴 |

## 3. 两条"自己做事情"的路线对比

| 维度 | A. 直接改/扩展 MAAOrch（现状） | B. 引入 MaaFramework 自定义流程 |
|------|------|------|
| 学习成本 | 低（本手册 + config_injector 已有） | 中（新框架，但文档是 MAA 自家风格） |
| 适用范围 | 明日方舟现有玩法（刷关/剿灭/肉鸽/生息） | 任意游戏/应用（不只方舟） |
| 与 MAAOrch 集成 | 天然（注入 config） | 需自行桥接（MaaFramework 是独立运行时） |
| 自定义深度 | 任务链组合（869 任务现成复用） | 从零设计任务图（模板/OCR 要自己准备） |
| 维护成本 | 跟随 MAA 更新 | 跟随 MaaFramework 更新 |
| 许可 | MAA 本体 AGPL-3.0 | **MaaFramework 本体 LGPL-3.0**（AGPL 的是 M9A/MaaEnd 这些**应用**，不是框架） |

**建议（2026-09-15 更新）**：
- **方舟继续 A**——MAA 本体**不是** MaaFramework 项目（老一代 MaaCore + WPF），
  869 任务的成熟度是皇冠，零改动铁律。
- **方舟以外的游戏直接走 B**：不再是"将来再考虑"，而是默认路径
  （用户：「用 MaaFW GUI，加上 codex 写脚本理论上可以适配所有 2 游」+「怎么
  方便怎么来，我又不传播」）。
- B 的正确形态见 docs/MULTI_GAME_VISION.md §2：**一个契约适配器 + N 份
  resource**；不做"每引擎一个适配器"，**不逆向解析引擎日志**（吃结构化回调）。

## 4. 我们的知识资产（本仓库已有的）

| 资产 | 位置 | 说明 |
|------|------|------|
| 任务机制手册 | `docs/MAA_TASK_MECHANICS.md` | 869 任务引擎解剖（本调研配套） |
| 任务转移图工具 | `tools/task_graph.py` | 任意任务链路可视化/查询 |
| 图片转译工具 | `tools/vision_translate.py` | 模板图 → 文本（OCR+特征），喂给 LLM |
| DeepSeek 逻辑问答 | `services/task_explainer.py` | 问"这个链在干嘛"、生成自定义任务定义 |
| 任务注入能力 | `services/config_injector.py` | 已能把自定义任务队列写进 MAA 配置 |
| 事件流+样本库 | `services/log_watcher.py` + `logs/log_samples/` | 运行数据积累（将来训练/规则提炼用） |
| **AI 攻坚代理** | `services/ai_agent/`（percept / battle_vision / actuator / hud / tile_map） | 2026-09-14 实机只读感知打通（见 docs/AI_AGENT.md）。**本质就是 MaaFramework 的 Custom Recognition / Custom Action** —— 按 Agent 协议包装后可供**任意** MaaFramework 游戏复用，而非只服务方舟 |
| LLM 写脚本地基 | `tools/vision_translate.py` + `services/task_explainer.py` + `tools/task_graph.py` | 即「codex 写脚本」的同构物；目标 schema 从 MAA `tasks.json` 映射到 MaaFramework Pipeline JSON（更简单）即可 |

## 5. 来源

- [MAA 官方文档站](https://docs.maa.plus/zh-cn/)
- [MAA 主仓库](https://github.com/MaaAssistantArknights/MaaAssistantArknights)
- [任务流程协议](https://docs.maa.plus/zh-cn/protocol/task-schema.html)
- [MAA Task Schema (EN)](https://docs.maa.plus/en-us/protocol/task-schema.html)
- [MaaFramework（LGPL-3.0 通用框架）](https://github.com/MaaXYZ/MaaFramework) · [maafw.com](https://maafw.com/)
- [Custom & Agent 机制](https://maafw.com/docs/1.3-Custom&Agent)
- [MFAAvalonia 通用 GUI](https://github.com/MaaXYZ/MFAAvalonia)
- [MaaBD2 / sunyink/MFABD2](https://github.com/sunyink/MFABD2)
