# MAAOrch 系统架构

## 概述

MAAOrch 是一个基于 PySide6 (Qt 6) 的多账号 MAA 调度桌面应用。`main.pyw` 无控制台窗口启动，`MainWindow`（`app/main_window.py`）管理所有 UI 和业务逻辑。

```
main_web.pyw → QApplication + uvicorn
     ├── AccountRunner (services/runner.py)         启动→监控→完成回调
     ├── LaunchQueue   (services/launch_queue.py)   优先级队列 + 调度
     ├── ConfigService (services/config_injector.py) gui.json 双写入
     ├── MaintService  (services/instance_pool.py)  实例创建/删除/重建
     ├── LogService    (services/log_parser.py)     asst.log 解析
     ├── EmuService    (services/emu_service.py)    ADB / 模拟器操作
     ├── UpdateService (services/update_service.py) 下载/解压 MAA
     ├── HealthCheck   (services/health_check.py)   10 项健康检查
     ├── SmartScheduler(services/smart_scheduler.py) 理智/定时/material 决策
     ├── DispatchPool  (services/dispatch_pool.py)  模板池创建/查询/清理
     └── ApiServer     (network/api_fastapi.py)     FastAPI + uvicorn (50+ endpoints)
```

## 目录结构

| 目录 | 说明 |
|------|------|
| `app/` | 应用入口、主窗口、主题、DI 容器 |
| `services/` | 核心服务：启动队列、MAA 生命周期、配置注入、模拟器管理、日志、更新、健康检查 |
| `ui/` | 所有界面组件：账号卡片、侧栏、对话框、配置面板 |
| `models/` | 数据模型：Account、QueueEntry、ConfigManager、RunStats |
| `infrastructure/` | 基础设施：Logger、TaskConstants、Utils、BackgroundTask、PlatformHelper |
| `network/` | HTTP API 服务 |

## 核心模块

| 文件 | 职责 |
|------|------|
| `app/main.pyw` | 入口，UAC 提权、单实例锁（Windows 命名 Mutex）、代理检测、异常捕获 |
| `app/main_window.py` | MainWindow：UI 装配 + 定时器 setup |
| `app/service_context.py` | ServiceContext dataclass，通过信号传递共享状态 |
| `app/themes.py` | Dark/Light/Notepaper QSS 主题 |
| `services/runner.py` | AccountRunner：launch→monitor→account_finished 回调闭环 |
| `services/launch_queue.py` | LaunchQueue：优先级队列（手动/定时/理智三种来源），30s tick 调度 |
| `services/config_injector.py` | ConfigService：MAA 配置注入，双写 gui.json (v5) + gui.new.json (v6 TaskQueue) |
| `services/instance_pool.py` | MaintService：MAA 实例创建/删除/重建 |
| `services/smart_scheduler.py` | decide()：根据时间/理智余量/material 需求计算最优 dispatch_id |
| `services/dispatch_pool.py` | Dispatch 模板池（create/get/remove） |
| `services/log_parser.py` | LogService：asst.log 解析（v5/v6 双格式）、统计、日志轮转 |
| `services/update_service.py` | Update：GitHub API 查询、下载、解压 MAA 压缩包 |
| `services/emu_service.py` | EmuService：ADB 扫描/连接/截图，模拟器列表/启动/关闭 |
| `services/health_check.py` | HealthCheck：10 项检查（ADB、MAA 进程、配置完整性等） |
| `services/pipeline_thread.py` | PipelineThread (legacy)：分组流水线调度 |
| `services/schedule_thread.py` | ScheduleThread (legacy)：定时触发 |
| `models/account.py` | Account dataclass |
| `models/config_manager.py` | ConfigManager：config.json 加载/保存/版本迁移 |
| `models/queue_entry.py` | QueueEntry frozen dataclass |
| `models/stats.py` | RunStats：运行历史持久化到 accounts/{id}/stats.json |
| `infrastructure/logger.py` | Logger 类：debug/events/crash 三类日志 |
| `infrastructure/task_constants.py` | 任务常量、mumu-cli 发现、EmuMonitor QThread（30s 轮询） |
| `infrastructure/utils.py` | atomic_write / is_safe_zip_path / setup_proxy |
| `infrastructure/background_thread.py` | BackgroundTask QThread 通用封装 |
| `infrastructure/platform_helper.py` | IsUserAdmin / ShellExecuteW runas 提权 |
| `ui/smart_panel.py` | 账号卡片列表 + 批量操作 |
| `ui/side_bar.py` | 状态过滤器 + 模式切换 |
| `ui/create_account.py` | 新建账号对话框 |
| `ui/emu_selector.py` | 模拟器实例选择器 |
| `ui/task_config.py` | 账号任务配置（9 个标签页） |
| `ui/smart_config.py` | 全局智能调度配置对话框 |
| `ui/account_detail.py` | 账号设置对话框 |
| `ui/main_poll.py` | do_poll / do_smart_tick / health_check 驱动 |
| `ui/rebuild_dialog.py` | 重建实例进度对话框 |
| `ui/batch_edit.py` | 批量编辑账号 |
| `ui/log_window.py` | 日志显示窗口 |
| `ui/settings_window.py` | 设置对话框 |
| `ui/widgets/config_card.py` | ConfigCard 小部件 |
| `ui/dialogs.py` | 旧版对话框（兼容） |
| `network/api_fastapi.py` | HTTP REST 服务（FastAPI + uvicorn），50+ endpoints |
| `network/api_server.py` | 旧版 HTTPServer 实现（保留备用） |
| `ui/web/` | Web UI 前端（SPA, HTML+CSS+JS） |

## ServiceContext 设计

`ServiceContext`（`app/service_context.py`）是一个 dataclass，通过 Qt Signal/Slot 将 MainWindow 的共享状态以类型安全方式暴露给各 Service，避免直接传递 MainWindow 引用。

## 配置双写策略

ConfigService 同时写入两种格式：
- **gui.json (v5)** — 旧版兼容，保留完整配置结构
- **gui.new.json (v6 TaskQueue)** — 新版任务队列格式

迁移逻辑在 `models/config_manager.py` 中处理 v4→v5 升级。

## 智能调度

`services/smart_scheduler.py::decide()` 根据以下条件计算最优 dispatch：
- 当前时间 & 定时设定
- 理智余量和恢复时间
- material 需求优先级

返回的 dispatch 通过 `services/dispatch_pool.py` 模板池转换为具体配置。

## 线程模型

| 线程 | 类型 | 说明 |
|------|------|------|
| 主线程 | GUI | Qt 事件循环，所有 UI 操作 |
| LaunchQueue 后台 | daemon thread | 5s tick，处理队列、清理残留 |
| ApiServer | uvicorn daemon thread | FastAPI 异步 HTTP 服务 |
| Runner `_launch_job` | daemon thread | 每个启动任务一个，崩溃自动清理 |
| Runner `_wait_exit` | daemon thread | 等待 MAA.exe 退出后直接调 `_cleanup` |
| BackgroundTask | daemon thread | 通用一次性后台任务 |

## 启动队列架构

`LaunchQueue` 是核心调度入口，所有启动请求先入队：

```
触发来源：
  - 手动点 ▶ (priority=0, not_before=now)
  - 定时触发 (priority=1, not_before=now)
  - 理智回满 (priority=2, not_before=recovery_time)

LaunchQueue (30s tick)
  跳过条件：已在运行 / 模拟器被占 / 未到时间 / 理智不够
  全部满足 → AccountRunner.launch()

account_finished 信号：
  释放模拟器 → 理智入队（自动计算恢复时间）→ tick() 下一轮
```

核心原则：**绝不中断正在运行的 MAA，只等空闲时启动下一个**。

## 单实例机制

`app/main.pyw` 创建 Windows 命名 Mutex，若已存在则通过广播消息激活已有窗口后退出。

## 管理员权限

`infrastructure/platform_helper.py` 启动时检测 `IsUserAdmin()`，若非管理员则调用 `ShellExecuteW` 以 `runas` 重新启动（UAC 弹窗）。

---

## 实体关系模型

### 核心实体

```
账号 (Account)
  ├─ game_client: Bilibili / Official  ← 不同 APK
  ├─ emu_instance_index: "0"          ← 在哪个模拟器 VM 上
  ├─ account_switch: "用户名"          ← APP 内的账号标识
  └─ name: "我的B服号"                ← 用户给的昵称

MAA 实例 (无状态工具)
  ├─ maa/instances/N/MAA.exe
  ├─ 被 MAAOrch 配置后启动
  ├─ 通过 ADB 连接模拟器
  └─ 执行 TaskQueue 后退出 (PostActions=12)

MuMu 模拟器 (VM)
  ├─ VM 0: ADB 127.0.0.1:16384
  │   ├─ 安装了 方舟Bilibili.apk
  │   │   └─ 用户手动登录了 "我的B服号"
  │   └─ 安装了 方舟Official.apk
  │       └─ 用户手动登录了 "我的官服号"
  └─ VM 1: ADB 127.0.0.1:16416
      └─ ...
```

### 关键约束

| 规则 | 说明 |
|------|------|
| 账号 ≠ MAA 实例 | 账号绑的是**模拟器 VM + 服务器 APP** |
| 一个 VM 可以有一个 B 服号 + 一个官服号 | 不同 APK 互不干扰 |
| 用户手动登录 | MAAOrch 不存密码，不做自动登录 |
| MAA 无状态 | 每次启动都重新注入配置，用完就退 |
| `game_client` 决定启动哪个 APK | Bilibili / Official 是不同的 APP |

### 调度链路

```
MAAOrch 调度器
  → 读账号配置 (API/VM/服/号)
  → 配 MAA (inject_smart)
  → 启动 MAA (Popen)
  → MAA 连模拟器 ADB
  → 启动对应方舟 APP (ClientType)
  → 执行任务队列
  → PostActions 退出
```
