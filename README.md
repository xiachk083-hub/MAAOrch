# MAAOrch

一键按顺序跑完多个号的配置。支持多账号独立配置、程序多实例检测、任务流水线调度。

---

## 快速上手

```bash
pip install PySide6
python main.pyw
```

Windows 10/11, Python 3.12+。

## 功能一览

分组管理、多账号独立配置、程序下载/更新、模拟器多实例检测、任务流水线、日志统计、进程守护、定时任务、循环调度、运行历史、暗色/亮色主题、Webhook 通知、HTTP API、开机自启。

## 目录结构

```
main.pyw                入口
main_window.py          主窗口
config.py               配置加载/迁移
callbacks.py            ServiceContext 依赖注入
account.py              Account 数据类
runner.py               单号启动→监控→完成回调
launch_queue.py         统一启动队列
pipeline_thread.py      流水线调度
schedule_thread.py      定时任务
maint_ops.py            进程守护/通知/更新
config_ops.py           MAA 配置注入
log_ops.py              日志解析/统计
emu_ops.py              ADB/模拟器操作
updater.py              下载/更新
stats.py                运行历史持久化
api_server.py           HTTP API
dialogs.py              对话框
task_constants.py       任务常量/模拟器检测
themes.py               主题
utils.py                工具函数
background.py           通用线程
ui/                     面板模块
tests/                  测试
docs/                   技术文档
accounts/               各账号 MAA 目录
```

## 技术文档

| 文档 | 内容 |
|------|------|
| [快速上手](docs/getting-started.md) | 使用流程、仪表盘、分组、故障排除 |
| [系统架构](docs/architecture.md) | 模块划分、数据流、线程模型 |
| [多账号与模拟器](docs/account-management.md) | Account 类、ADB、mumu-cli |
| [流水线调度](docs/pipeline.md) | 队列、分组、循环调度 |
| [任务配置注入](docs/task-config.md) | gui.json、maa-cli TOML |
| [日志与监控](docs/monitoring.md) | asst.log 解析、统计、守护 |
| [下载更新与代理](docs/update-download.md) | 下载、版本切换、代理检测 |
| [HTTP API](docs/http-api.md) | REST 接口完整参考 |
| [开发指南](docs/dev-guide.md) | 环境搭建、编码规范、测试 |

## 快捷键

| 按键 | 功能 |
|------|------|
| `Ctrl+Enter` | 启动流水线 |
| `Esc` | 停止流水线 |


## 技术栈

Python 3.12、PySide6 (Qt 6)、mumu-cli、ADB、maa-cli、GitHub Release API

本工具基于 [MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights)（AGPL-3.0）开发。
