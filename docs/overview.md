# MAAOrch 项目功能概览（2026-08-14 重写）

> 本文档 2026-08-14 从 Qt 时代版本整体重写。结构规范见 STRUCTURE.md，机制细节见各专篇
> （MAA_TASK_MECHANICS / STAGE_PROBE / DEPOT_PLANNING / EMULATOR_SERVICE / MATERIAL_FARMING）。

## 1. 定位

明日方舟（MAA）**多账号托管编排平台**：45+ 账号 × 模拟器池 × MAA 实例池的调度、执行、
材料规划、数据采集与运维。架构为「MAAOrch 决策 → MAA 执行」的外部辅助路线——MAA 只当执行
引擎（跟随官方最新版，项目适配），所有决策（刷什么/谁先刷/何时刷）在 MAAOrch。

## 2. 现状架构

```
manager/manager.py          独立常驻管理进程（stdlib only，19998，自包含铁律）
   ↓ HTTP + 文件
main_web.pyw                唯一业务入口（FastAPI + uvicorn，19999）
├── app/                    共享上下文 dataclass（web_context/service_context）
├── ui/web/                 纯静态原生 JS SPA（无框架：账号表/调度台/统计/材料规划页）
├── network/api_fastapi.py  API 层（唯一实现，50+ 端点 + SSE 事件流）
├── services/               业务域（子域划分见 STRUCTURE.md §2）
├── models/                 数据模型 + config.json + 每账号状态（accounts/<id>/）
├── infrastructure/         基础组件（logger/console_dash/task_constants/maa_core 等）
└── tools/                  独立脚本（deploy/校准/诊断，仅 stdlib）
```

## 3. 核心机制（当前生效）

| 域 | 机制 | 关键模块 |
|---|---|---|
| **调度** | 任务参数化（模板+账号打包成带参任务走调度台，stage×times 可见）；slot 优先级（维护30>刷关20>剿灭10>肉鸽0）；运行中只断肉鸽让位；队列持久化重启恢复 | dispatch_pool / launch_queue / runner |
| **执行** | 就绪信号驱动串行启动（等上一台 boot+ADB）；MAA 实例池（11 实例，resource 物理独立复制）；配置注入 gui.json/gui.new.json（v6 格式） | runner / instance_pool / config_injector |
| **恢复** | 异常窗口杀进程闭环（ErrorCongratulations 僵尸不占实例）；_err_count 启动复位+宽限；失联 2 轮确认；4 次失败自动挂起；刷关空转复核（Fight 0 把判失败重试） | runner |
| **材料规划** | Depot 仓库识别 → 企鹅物流掉率榜（含磨难 -HARD 不漏）→ 水位对比 → 多日排程（期望 AP 升序）→ 三标签分派（补材料/搓玉/补资源）× 活动/非活动 → 预检可刷性（刷不了自动往后延） | depot_reader / depot_scheduler / material_planner / farm_dispatch |
| **数据采集** | 企鹅物流每日 03:30 抓取 → SQLite 本地库 → 掉率变化自动重生成榜+审计门禁；活动关每小时自动识别（数据活跃度+公告时间窗）；官网公告 6h | penguin_data / activity_tracker / announce_collector |
| **体力** | SanityBeforeStage 回调真实值（双格式兼容）；per-账号 sanity_live.json；上次清体+余量；实测日耗预测（7 天窗口、排除吃药日、游戏日 04:00 界） | sanity_tracker / stats |
| **统计** | 日/周/月/年五档聚合（日/周均 04:00 界）；编年史；统计页 UI | stats_aggregator / stats |
| **运维** | deploy.py 一键部署（54 文件清单）；/api/health 五类体检；oplog 审计；终端模式仪表盘（console_dash）；MAA 版本跟随官方最新（6.16.8） | tools/deploy / runtime_health / console_dash |

## 4. 账号体系

- 45+ 账号三类：官服（33）/B 服（11）/日服（4）+ l-4/l-6 肉鸽业务机（白名单纳管）
- 每账号：模拟器绑定、farm_tag 三标签、主线章节、stage_ability（关卡可刷性）、
  farm_probe 缓存（CAN_FARM 永久/CANNOT-DONE 7 天重测）
- 游戏日 04:00 界：统计/体力预测/排程全按方舟游戏日对齐

## 5. 已知方向（未实施）

自动重新入队生命周期、EmulatorService Phase 1 剩余（实例池迁入/回收开关/前端）、
多游戏识别适配层（编排层游戏无关化）、多机节点扩展。见记忆库与各专篇。

## 6. 部署与生产

- 开发 D:\1-2\MAAOrch（本地）→ deploy.py → 生产 E:\MAAOrch（100.79.173.69）
- 结构改动 = 部署改动：部署要克制，清单必须同步（STRUCTURE.md §5）
- manager 自包含铁律：不 import 项目代码、stop 默认不关模拟器
