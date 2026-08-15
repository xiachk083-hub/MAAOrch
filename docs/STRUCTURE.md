# 项目结构规范（2026-08-12 定型）

> 用户决策：项目未来会非常大，结构越早定型越好。
> 本文件是**新代码落位的唯一依据**——写代码前先查"模块归属"，别往 services 顶层堆。

## 1. 分层架构

依赖方向单向：`infrastructure/models`（基础）→ `services`（业务域）→ `network/app`（调用面）。

```
main_web.pyw              唯一入口（FastAPI + uvicorn，端口 19999，sys.path 注入根目录）
│
├── app/                  仅存共享上下文 dataclass（web_context / service_context）
├── ui/web/               现行 Web 前端（纯静态原生 JS SPA，无框架）
├── network/              API 层：api_fastapi.py（唯一实现，3026 行，50+ 端点 + SSE）
├── services/             核心业务域（见 §2 子域划分）★ 新模块落位看这里
├── models/               数据模型 + 权威配置（config.json）+ 每账号状态（accounts/<id>/）
├── infrastructure/       无业务语义基础组件（logger/background_thread/maa_core/
│                         platform_helper/task_constants/utils）
├── tools/                独立运行诊断/部署脚本（仅 stdlib，不 import 项目模块）
├── docs/                 架构与机制文档
├── manager/              独立常驻管理进程（stdlib only，端口 19998，不 import 项目代码，
│                         通过 HTTP + 文件交互 —— 多机扩展的雏形，保持自包含铁律）
└── logs/                 运行时数据（样本/公告/统计/历史归档）
```

### 已删除的僵尸（2026-08-12，git 历史可恢复）
- `network/api_server.py` — 旧 http.server API 层（被 Qt 遗留引用，无实际调用方）
- `app/main_window.py` + `app/themes.py` — Qt 桌面遗留（imports 的 pipeline_thread/schedule_thread/ui.dialogs 早已不存在）
- `services/scheduler.py` — `start_scheduler` 无调用方（调度线程原在已删的 schedule_thread.py）
- `app/backups/`、`services/accounts/` — 空目录（后者的 stats 扫描路径已从 dynamic_params 移除）

### 2026-08-15 整理（瘦身）
- 删 `services/update_service.py` — Qt 时代 MAA 更新器（import PySide6，运行时死代码；API 层自研 /api/maa/check_update 替代）
- 移 `services/backfill_stats.py` / `backfill_account_state.py` / `task_explainer.py` → `tools/scripts/`（一次性脚本/诊断工具，非运行时模块）
- deploy.py 清单校正：移除僵尸项 `log_parser`（文件不存在）；补运行时缺失 `event_bus.py`/`shot_collector.py`/`daily_batch_scheduler.py`（在 main_web/api 使用但原不在清单）
- 大方法段落导航注释：`runner._check_one`（538 行，10 段）、`launch_queue._tick`/`_clean_stale_emus`/`_reclaim_idle_emus`（263/172/360 行）、`api_fastapi` 域分区表

## 2. services 子域划分（未来物理目录，现为逻辑分组）

| 未来目录 | 模块 | 职责 |
|---|---|---|
| `queue/` | launch_queue, runner, dispatch_pool, dynamic_params, smart_scheduler, farm_dispatch | 入队/调度/单账号运行编排/每日刷取分派 |
| `emulator/` | emu_state, emu_service, emu_service_v2 | 模拟器状态机 + 操作单点（EmulatorService 收口后迁入） |
| `maa/` | config_injector, maa_download, instance_pool, update_service | MAA 安装/实例池/配置注入（注意：`services/maa/` 目录本身是 MAA 运行时数据，不是代码域） |
| `monitor/` | log_watcher, log_sorter, stage_probe, event_analyzer, sanity_tracker | 日志事件流/样本/关卡探测/体力快照；Go 三件套（adb_monitor/health_monitor/log_monitor）独立 module 保持原位 |
| `depot/` | depot_reader, depot_scheduler, material_planner, penguin_data, activity_tracker | 仓库识别/扫描调度/水位规划/企鹅物流数据/活动识别 |
| `stats/` | stats_aggregator, runtime_health, backfill_stats, backfill_account_state | 统计聚合/运行期健康 |
| `ai/` | ai_assistant, task_explainer, announce_collector, vision | LLM 辅助/公告收集/视觉大模型 |

**迁移触发条件**（不主动拆，随重构联动）：
- `emulator/`：EmulatorService 状态机收口（Phase 2+）时迁入
- `queue/`：launch_queue ↔ runner 解耦重构时迁入
- `monitor/`：EventBus 落地时迁入
- 迁移时必须同步 §5 的 deploy.py 清单

## 3. 新模块落位规则

1. **先查 §2 表**：业务服务归对应子域；不归属任何域的才允许放 services 顶层（如 emu_state 状态机核心）
2. **基础组件**（无业务语义）→ `infrastructure/`；**数据类** → `models/`；**HTTP 端点** → `network/api_fastapi.py`
3. **独立诊断脚本** → `tools/`（只用 stdlib，不 import 项目模块，保持可单独运行）
4. **一次性脚本/实验** → `debug/scripts/`（gitignore，不入库）
5. 服务间 import 一律 `from services.xxx import ...`，入口依赖 main_web.pyw 注入 sys.path；**不要在函数内 import 之外再发明新的绕循环方式**

## 4. 数据文件规范

- **运行数据一律 `logs/`（样本/快照/归档）或 `models/`（配置/账号状态）**，不往根目录塞
- 根目录当前仍存在（logger/api 硬编码，EventBus 统一时收口 §6）：`debug.log`/`events.log`/`crash.log`（infrastructure/logger.py:7 `_LOG_DIR`）、`oplog.json`（network/api_fastapi.py:18）
- `services/queue.json`、`services/dispatch_pool.json` 为队列持久化（launch_queue/dispatch_pool 的 `Path(__file__).parent` 相对位置，**迁移子域时路径会变，manager 部署保留清单 manager/manager.py:462 必须同步**）
- `services/maa/` 是 MAA 运行时树（source/instances/），部署跳过清单已保护，**任何结构改动不得移动它**
- 敏感数据（accounts*.json 副本等）一律进 `debug/`（gitignore），绝不允许入库

## 5. deploy.py 清单维护警示 ⚠️

`tools/deploy.py` 的 `DEFAULT_FILES` 是**显式文件清单**（逐文件 base64 分块传输）：
- 移动/重命名/新增任何被部署的文件 → 必须同步清单
- 删除文件 → 从清单移除（目标机旧文件不会自动删，无引用即无影响）
- 2026-08-14 已补全为 54 项（此前约半数运行时模块不在清单，目标机靠存量文件
  运行）：覆盖 models/config_manager+account_state、services 全部主链路模块
  （含 emu_service 系/log_watcher/sanity_tracker/depot 系/penguin/activity/
  farm_dispatch/maa_download/vision 等）、app/*、infrastructure 全部、
  ui/web/style.css、config 三个 JSON。**新增 services 模块必须同时加清单**。

## 6. 迁移路线图（随重构联动，不单独安排）

| 触发重构 | 内容 |
|---|---|
| EventBus 落地（ARCHITECTURE_IMPROVEMENTS.md 规划） | 根日志/oplog 写入统一收口 `logs/`，删除 infrastructure/logger.py 的根目录硬编码 |
| EmulatorService 状态机收口（EMULATOR_SERVICE.md） | emu_state/emu_service/emu_service_v2 → `services/emulator/` |
| launch_queue ↔ runner 解耦 | 编排层 → `services/queue/`；顺带清理互引（runner._close_mumu_popups 等跨域私有调用） |
| 多机节点（node_id 已预留） | manager/ 扩展为多机编排；deploy.py 演进为多机分发 |

## 7. 开发/生产拓扑提醒

- 本地 D:\1-2\MAAOrch = 开发副本；目标机 目标机 E:\MAAOrch = 生产（45+ 账号运行中）
- 结构改动 = 部署改动：**部署要克制**，结构整理随功能部署一次到位，不要频繁部署重启
- 第三工作区 E:\maa-alas\fz-maa（l- 系列模拟器）与 MAAOrch 共用机器：批量操作严禁碰 l- 前缀（task_constants 单点过滤）
