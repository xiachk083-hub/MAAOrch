# MAAOrch 系统架构

## 概述

MAAOrch 是一个基于 PySide6 (Qt 6) 的桌面应用，通过 `main.pyw` 无控制台窗口启动。核心入口为 `MainWindow`（`main_window.py`），管理所有 UI 组件和业务逻辑。

```
main.pyw → MainWindow → ServiceContext
                         ├── AccountRunner (runner.py)      — 单号启动→监控→完成回调
                         ├── LaunchQueue  (launch_queue.py)  — 统一启动队列
                         ├── RunStats     (stats.py)         — 运行历史持久化
                         ├── EmuService   (emu_ops.py)       — ADB / 模拟器操作
                         ├── ConfigService(config_ops.py)    — MAA 配置注入
                         ├── LogService   (log_ops.py)       — 日志解析 / 统计
                         ├── MaintService (maint_ops.py)     — 守护 / 更新 / 托盘
                         ├── PipelineThread(pipeline_thread.py) — 分组流水线调度
                         ├── ScheduleThread(schedule_thread.py) — 定时任务
                         └── ApiServer    (api_server.py)    — HTTP API 服务
```

## 核心模块

| 文件 | 职责 |
|------|------|
| `main.pyw` | 入口，处理 UAC 提权、单实例锁定、代理检测、异常捕获 |
| `main_window.py` | 主窗口类 `MainWindow`，包含所有 UI 组件和交互逻辑 |
| `account.py` | `Account` 数据类，类型化的账号模型，兼容旧 dict 访问 |
| `config.py` | 配置文件加载/保存，版本迁移（v4→v5），开机自启 |
| `runner.py` | `AccountRunner` — 单号启动→监控→完成回调闭环 |
| `launch_queue.py` | `LaunchQueue` — 统一启动队列（手动/定时/理智三种来源） |
| `stats.py` | `RunStats` — 运行历史持久化到 `accounts/{id}/stats.json` |
| `config_ops.py` | `ConfigService` — MAA 配置注入（gui.json / gui.new.json / TOML） |
| `emu_ops.py` | `EmuService` — ADB 扫描/连接/截图，模拟器实例检测/启动/关闭 |
| `log_ops.py` | `LogService` — asst.log 解析（v5/v6 双格式），统计展示，日志轮转 |
| `maint_ops.py` | `MaintService` — 进程守护、更新检查、系统托盘、通知 |
| `pipeline_thread.py` | `PipelineThread` — 分组流水线调度线程（串行/并行/暂停/恢复） |
| `schedule_thread.py` | `ScheduleThread` — 每日/每周定时触发 |
| `api_server.py` | `ApiServer` — HTTP REST 服务，127.0.0.1 监听 |
| `updater.py` | 下载/更新线程（`UpdateCheckThread`, `DownloadThread`, `MaacliInstallThread`） |
| `task_constants.py` | 任务名常量、模拟器预设、mumu-cli 发现、`EmuMonitor` 后台线程 |
| `themes.py` | 暗色/亮色 QSS 样式表 |
| `dialogs.py` | 设置、定时、账号、任务参数对话框 |
| `callbacks.py` | `ServiceContext` 数据类，解耦服务模块与主窗口 |
| `background.py` | `BackgroundTask` 通用后台线程封装 |
| `utils.py` | 工具函数（代理检测、管理员权限、ID 生成、版本解析等） |
| `ui/dashboard.py` | 账号仪表盘，支持增量刷新 |
| `ui/groups_panel.py` | 分组/仓库面板 |
| `ui/accounts_panel.py` | 账号列表面板 |

## 数据流

```
config.json ──→ load_config() ──→ accounts[] / warehouse[] / groups[]
                                       │                    │
                                       ▼                    ▼
                                AccountRunner          PipelineThread
                                (单号启动闭环)         (按分组调度启动)
                                       │
                                       ▼
                                 MAA 程序进程
                                       │
                                       ▼
                                asst.log ──→ LogService.parse_log()
                                               │
                                               ▼
                                          RunStats.save_run()
                                               │
                                               ▼
                                          stats.json ──→ HTTP API / 仪表盘
```

## ServiceContext 设计

`ServiceContext`（`callbacks.py`）是一个 dataclass，将主窗口的共享状态和回调以类型安全的方式暴露给各 Service 类，避免直接传递 `MainWindow` 引用：

```python
@dataclass
class ServiceContext:
    # 回调
    log: Callable[[str], None]
    save: Callable[[], None]
    notify: Callable[[str, bool], None]
    set_status: Callable[[str], None]
    set_theme: Callable[[str], None]
    show_dashboard: Callable[[int], None]
    inject_config: Callable[[dict, dict], None]
    launch_program: Callable[[dict], None]
    start_pipeline: Callable[[], None]
    restart_api_server: Callable[[], None]
    on_account_done: Callable[[str, int, list], None]

    # 共享数据
    accounts: list[dict]
    warehouse: list[dict]
    config: dict
    groups: list[dict]
    emu_status: dict
    proc_status: set
    proc_start_times: dict
    running_procs: dict
    cli_procs: dict

    # 服务引用
    cfg: ConfigService | None
    logs: LogService | None
    update_thread: Any
    schedule_thread: Any
    api_server: Any
    emu_monitor: Any

    _mw: MainWindow  # 仅用于弹框等需要 parent 的场景
```

## 单实例机制

通过 `main.pyw` 创建命名的 Windows 互斥体（Mutex），若已存在则通过广播消息激活已有窗口后退出。

## 管理员权限

启动时通过 `ctypes.windll.shell32.IsUserAnAdmin()` 检测，若非管理员则调用 `ShellExecuteW` 以 `runas` 重新启动（UAC 弹窗）。

## 线程模型

| 线程 | 类型 | 说明 |
|------|------|------|
| 主线程 | GUI | Qt 事件循环，所有 UI 操作 |
| `PipelineThread` | QThread | 流水线调度，支持暂停/停止 |
| `ScheduleThread` | QThread | 定时检查，到点触发 |
| `ApiServer` | QThread | HTTP 服务器，独立监听 |
| `EmuMonitor` | QThread | 每 30s 轮询 MuMu 实例状态 |
| `UpdateCheckThread` | QThread | GitHub API 查询 |
| `DownloadThread` | QThread | 下载 MAA 压缩包 |
| `BackgroundTask` | QThread | 通用一次性后台任务 |
| `LaunchQueue` | QTimer | 30s tick 调度启动队列 |

所有线程间通信通过 Qt Signal/Slot 机制，数据更新回主线程执行。

## 启动队列架构

`LaunchQueue` 是系统的核心调度入口，所有启动请求都先进入队列：

```
  触发来源:
    ● 手动点▶ (priority=0, not_before=now)
    ● 定时到了 (priority=1, not_before=now)
    ● 理智回满 (priority=2, not_before=recovery_time)
           │
           ▼
  LaunchQueue (30s tick)
    ├─ 已在运行？ → 跳过
    ├─ 模拟器被占？ → 跳过
    ├─ 还没到时间？ → 跳过
    ├─ 理智不够？ → 跳过
    └─ 全部满足 → 启动
           │
           ▼
  AccountRunner.launch()
           │
           ▼
  account_finished 信号
    ├─ 释放模拟器
    ├─ 理智入队（自动计算恢复时间）
    └─ tick() 检查下一个
```

核心原则：**绝不中断正在运行的 MAA，只等空闲时启动下一个**。
