# MAAOrch 项目说明

## 项目概述

基于 PySide6 的 MAA (MaaAssistantArknights) 多账号批量管理工具。通过队列系统统一调度多个模拟器实例，支持理智驱动的循环调度和每日批量启动。

## 技术栈

- Python 3.12+
- PySide6 (Qt 6 GUI)
- subprocess (启动 MAA 进程)
- GitHub Release API (更新/下载)

## 代码风格

- 无类型注解兼容（部分代码使用 `a.get("key", default)` 而非 `a.key`）
- 所有 UI 模块在 `ui/` 目录下
- Service 模式：`callbacks.py` 的 `ServiceContext` 传递共享状态
- 信号/槽通信：`PySide6.QtCore.Signal`

## GitHub 集成

- Issues 用户汇报 bug 或提需求
- 被 `@opencode` 提及时自动分析，创建分支并提 PR
- PR 会自动触发 CI（`github/workflows/ci.yml`），仅跑 lint-and-test

## 关键文件

| 文件 | 作用 |
|------|------|
| `main.pyw` | 入口 |
| `main_window.py` | 主窗口 |
| `runner.py` | 单账号启动→监控→完成回调 |
| `launch_queue.py` | 统一启动队列 |
| `config_ops.py` | MAA 配置注入 |
| `log_ops.py` | MAA 日志解析 v5/v6 |
| `maint_ops.py` | 进程守护、通知、更新 |
| `updater.py` | MAAOrch 自更新 / MAA 下载更新 |
| `stats.py` | 运行历史持久化 |
| `account.py` | Account 数据类 |

## 常见问题

### 1. 循环调度配置重启丢失

### 2. 卡片高度不一致

QGridLayout 的行高等高问题。修复：`frame.setMinimumHeight(140)` + `Qt.AlignTop`。
