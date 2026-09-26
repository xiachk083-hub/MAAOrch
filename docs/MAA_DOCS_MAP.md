# MAA 官方文档 → MAAOrch 落点总盘点

> 2026-08-17 官方文档站（docs.maa.plus）三区全站走查：develop / protocol / manual，对照项目模块逐一落点。
> 配套已有文档：`MAA_ECOSYSTEM.md`（生态调研）、`maa-config-mapping.md`（配置映射，6.16.5 源码级）、`MAA_TASK_MECHANICS.md`/`MAA_FLOW_LINKS.md`/`MAA_STATE_MAP.md`（869 任务源码解剖）。
> 一句话结论：**protocol 区是本项目唯一直接相关的文档区**；develop 区除外服适配机制外都是给 MAA 提 PR 的贡献者教程；manual 区是用户向语义，仅低优先级校准用。
> 当前阶段约束：只读本地，不动远端生产机器（见记忆 [[no-remote-touch-period]]）。

---

## 1. 文档区 → 项目模块映射总表

### protocol 区（核心，机器级真值）

| 文档 | 对应项目模块 | 状态 | 落点 / 缺口 |
|------|-------------|------|-------------|
| **回调消息协议** `callback-schema.html` | `services/log_watcher.py`、`services/runner.py` 完成判定、`services/sanity_tracker.py`、`services/depot_reader.py` | 🔥 已大量使用但可加固 | 消息枚举 + 字段是 LogWatcher 解析 asst.log 的官方地图。**关键缺口：`ConnectionInfo` 全族**（Reconnecting/Reconnected/Disconnect/ScreencapFailed/EmulatorFPS/FastestWayToScreencap）是 MAA 自报的连接态信号，比「MuMuManager info 轮询 + 心跳推断失联」更权威（历史事故：6 台集体误判失联杀 MAA）。`ProcessTask.details.task` 官方任务名清单（MedicineConfirm/ExpiringMedicineConfirm/肉鸽节点…）覆盖 LogWatcher 判断的全部节点。**版本漂移警戒：官方文档版本不含 sanity 回调，最新真值 = MAA 源码 `AsstProxy.cs` + 装机 asst.log**（what→class 事故即版本漂移实锤）。 |
| **集成文档** `integration.html` | `services/config_injector.py` | 可作注入核对基准 | 任务类型 + params 官方 schema（`Fight.medicine/medicine_expire_days`、`StartUp.client_type`、`Custom.task_names` 等）+「运行中能否改」标记；`TouchMode` 取值集合（minitouch/maatouch/adb/MaaFwAdb）。**不做 C API 深嵌**（B 路线黑盒 + AGPL 规避，只当 schema 参考）。 |
| **远程控制协议** `remote-control-schema.html` | `network/api_fastapi.py` `/api/maa_remote/*`、`services/runner.py` 心跳 | ✅ 已接入（仅用 ~10%） | 只用了心跳（MAA 每秒轮询 getTask）+ FAILED 日志上报。**未用能力**：`StopTask`（优雅停任务，不杀进程不留 minitouch 残留）、`CaptureImageNow`（MAA 自截回传 base64，绕开 ADB 抢 vGPU）、`Settings-ConnectionAddress`（端口漂移不重启纠正）、`HeartBeat` 任务（payload=当前运行任务 id，任务级状态权威）。**明确不用**：`LinkStart-*` 远程任务队列——它按 MAA 自身配置跑一键长草，丢 config_injector 的精确控制（关卡/次数/药），与「所有东西走调度台」原则冲突。无 token 认证（仅 user/device）→ loopback 白名单决策正确。 |
| **任务流程协议** `task-schema.html` | `tools/task_graph.py` + 自定义任务地基 | ⏳ 待读 | 任务 JSON schema 官方定义，可对照 `MAA_TASK_MECHANICS/FLOW_LINKS`（869 任务源码解剖）核对字段语义（@/#/next/maxTimes/exceededNext）。 |
| **肉鸽辅助协议** `integrated-strategy-schema.html` | 肉鸽管理（roguelike_only / l-4/l-6） | 中 | theme/mode/squad/core_char 官方 schema，对照 ROADMAP 肉鸽管理。 |
| **基建排班协议** `base-scheduling-schema.html` | Infrast 排班 | 低中 | 基建换班 JSON 官方定义。 |
| **战斗流程协议** `copilot-schema.html` / **保全** `sss-schema.html` | Copilot/SSS | 低 | 非主线，用到再读。 |

### develop 区（开发，仅一条机制有价值）

| 文档 | 对应项目模块 | 状态 | 落点 |
|------|-------------|------|------|
| **外服适配教程** `overseas-client-adaptation.html` | client_type 映射 / 资源解析 | 机制有用 | 官方适配机制：外服资源在 `resource/global/<ClientName>/`，缺失自动回退国服，外服条目**字段级覆盖**国服任务。解释了为何 client_type 错配会导致 MAA 加载错任务（2943896 事故）。asst.log `cur_retry/retry_times`（识别重试到上限=跳过任务报错）、`to_be_recognized`、`pre_task` 字段语义可补 LogWatcher。client_type 正源在 integration.html + AsstProxy.cs，**不在本页**。 |
| **VS Code 插件教程** `vsc-ext-tutorial.html` | task 工具链（可选） | 开发侧工具 | 控制面板任务展开/校验/ROI 截图识别测试，可配合 task_graph/vision_translate；MaaFramework 项目（1999/M9A）可直接复用调试。非运行时集成。 |
| development.html / linux-tutorial / pr-tutorial / issue-bot / doc-guidelines / ci-tutorial | — | 弃 | 全部是给 MAA 仓库提 PR 的贡献者教程（构建/PR/CI/文档规范），与黑盒编排无落点。 |

### manual 区（用户手册，用户向语义）

| 文档 | 对应项目模块 | 状态 | 落点 |
|------|-------------|------|------|
| **连接设置** `connection.html` | `Gui.ConnectSettings` / inst_addr_drift | 低优先校准 | maa-config-mapping 已从 6.16.5 源码覆盖读取位置，本页仅用户向语义确认。 |
| **理智作战** `combat.html` | `config_injector.py` Fight 注入 | 中 | 确认体力药、代理作战、理智阈值用户向行为假设。 |
| **自动肉鸽** `integrated-strategy.html` | 肉鸽管理 | 中 | theme/mode/squad/core_char GUI 语义确认。 |
| 开始唤醒 / 库存保持 / 基建 / 信用 / 领取 / 生息 / 抄作业 / 小工具 / 其他 | 各对应功能 | 低 | 印证已知，非发现新东西。 |
| install / newbie / faq / device(Windows/Mac/Linux/Android) / CLI 全页 | — | 弃 | 安装给最终用户/CLI(MaaPiCli)未用。 |

---

## 2. 官方文档能补的真实缺口（按优先级，均本地可做）

1. **ConnectionInfo 全族接入**（callback-schema）→ MAA 自报连接态（Reconnecting/Disconnect/ScreencapFailed），替代/补强当前「MuMuManager info 轮询 + 心跳推断」的失联判定——历史事故（6 台集体误判失联杀 MAA）就是推断式检测的代价。
2. **integration schema 核对 config_injector** → 用官方 params 字段定义校验注入，防「注入错位置/错字段」类事故（Gui.Resource 注入崩溃前车之鉴）。
3. **RemoteControl 未用能力** → StopTask 优雅停 / CaptureImageNow 自截 / Settings-ConnectionAddress 改地址 / HeartBeat 任务级状态（待用户拍板，见 [[maa-remote-control]]）。

## 3. 版本漂移警戒（重要）

官方回调协议随 MAA 版本快速迭代，文档版本**不含 sanity 回调**（SanityBeforeStage）；装机 MAA 永远最新（用户原则）。→ **文档是地图，真值 = 装机 MAA 的 `AsstProxy.cs` + 现场 asst.log**。解析层必须跟版本走，what→class 体力全断事故即版本漂移实锤。

## 4. 该弃清单

- develop 区：development / linux-tutorial / pr-tutorial / issue-bot-usage / documentation-guidelines / ci-tutorial
- manual 区：install / newbie / faq / 设备支持（Windows/Mac/Linux/Android）/ CLI 全页
- protocol 区：copilot-schema / sss-schema（低优先，用到再读）；C API 深嵌路线（integration 正文）整体不做

---

## 附：本次走查覆盖的官方页面

- develop/：index、development.html、vsc-ext-tutorial.html、overseas-client-adaptation.html
- protocol/：index、integration.html、callback-schema.html、remote-control-schema.html（task-schema 未读 ⏳）
- manual/：index（仅结构盘点，未逐页读）
