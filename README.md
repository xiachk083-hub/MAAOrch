# MAAOrch

官服、B服、日服三个号每天手动切 MAA 配置太麻烦了。于是写了这个工具，一键按顺序跑完三个号的基建、刷图、公招。

---

## 快速上手

```bash
pip install PySide6
python main.pyw
```

Windows 10/11, Python 3.12+。

详细使用流程见 [docs/getting-started.md](docs/getting-started.md)。

## 功能一览

分组管理、多账号独立配置、MAA 下载/更新、模拟器多实例检测（MuMu/雷电/夜神/逍遥/蓝叠）、任务流水线（刷关/公招/基建/信用/肉鸽/生息）、maa-cli 支持、MAA 日志统计、进程守护、定时任务、循环调度（体力/时间/定时）、运行历史、暗色/亮色主题、Webhook 通知、HTTP API、开机自启。

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
| [下载更新与代理](docs/update-download.md) | MAA 下载、版本切换、代理检测 |
| [HTTP API](docs/http-api.md) | REST 接口完整参考 |
| [开发指南](docs/dev-guide.md) | 环境搭建、编码规范、测试 |
| [daigan 对接](docs/daigan-integration.md) | 代肝数据推送 |

## 快捷键

| 按键 | 功能 |
|------|------|
| `Ctrl+Enter` | 启动流水线 |
| `Esc` | 停止流水线 |

## ⚠️ 使用须知

MAAOrch 是我为了自己管理三个《明日方舟》账号写的工具，分享出来是因为可能有类似需求的玩家。

**不是为以下场景设计的：**
- 商业代肝、账号交易
- 大规模多开（超过 10 个账号同时运行）
- 任何违反《明日方舟》用户协议的行为

作为个人开发者，我保留在明显滥用的情况下将项目转为私有的权利。

## 技术栈

Python 3.12、PySide6 (Qt 6)、mumu-cli、ADB、maa-cli、GitHub Release API
