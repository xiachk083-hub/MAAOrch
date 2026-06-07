# MAAOrch 开发技能

## 项目架构

```
main.pyw → MainWindow → ServiceContext
                         ├── AccountRunner (runner.py) — 单号启动→监控→完成回调
                         ├── LaunchQueue (launch_queue.py) — 统一启动队列
                         ├── RunStats (stats.py) — 运行历史持久化
                         ├── EmuService (emu_ops.py) — ADB/模拟器操作
                         ├── ConfigService (config_ops.py) — MAA 配置注入
                         ├── LogService (log_ops.py) — 日志解析/统计
                         ├── MaintService (maint_ops.py) — 守护/更新/托盘
                         └── ApiServer (api_server.py) — HTTP API

UI 面板 (ui/):
  dashboard.py — 账号仪表盘（增量刷新）
  queue_panel.py — 队列（运行中/等待中/历史）
  config_cards.py — 账号卡片网格
  schedule_panel.py — 循环调度管理
  accounts_panel.py — 账号列表
```

## CI/CD

### Workflow (`.github/workflows/ci.yml`)

两个 job：
1. `lint-and-test` — pip install + pytest（push 到 main 时触发）
2. `build-release` — pyinstaller 构建 + `release.py` 上传（tag push 时触发）

### 发布流程

```bash
# 更新版本号
# main_window.py 的 VERSION 常量
git add -A
git commit -m "release: v1.x.x — 说明"
git push origin main
git tag v1.x.x
git push origin v1.x.x
```

CI 自动构建 exe 并上传到 Release。

## 编码约定

### import 风格

```python
import sys,json,os    # 标准库一行
from pathlib import Path  # 分开
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, ...)
```

### 信号/槽

使用 Qt Signal/Slot，所有 emit 在主线程处理（QThread 通过 signal 通信）。

### 账号数据结构

`Account` dataclass (`account.py`) 支持两种访问方式：
```python
a["name"]  # dict 式
a.name     # 属性式
```
现有代码混用，优先用 `a.get("key", default)` 兼容。

### 配置存储

- `config.json` — 全局配置 + 账号列表
- `accounts/{id}/stats.json` — 运行统计
- `accounts/{id}/MAA/` — MAA 安装目录

## 测试

```bash
pytest tests/ -v
```

68 个测试覆盖：core/critical/emu/maint/queue/runner/stats。

## 常见修复

### CI 构建问题

`release.py` 上传失败 → 检查 GITHUB_TOKEN 和 GITHUB_REF。
`pyinstaller` 构建失败 → 检查 `MAAOrch.spec` 的 hiddenimports。
文件大小超限 → 更新 `ci.yml` 的 `LIMITS`。

### 账户相关问题

新建账号不继承设置 → 检查 `AccountDialog.__init__` 的 `acc` 参数。
MAA 绑定不上 → 检查 `maint_ops.dl_maa` 路径和 `account_ref`。

### 调度问题

重启设置丢失 → `schedule_panel.py` 初始化没读 config。
理智估算不对 → `report_time` 为空，`_get_last_sanity` 需要 fallback。
