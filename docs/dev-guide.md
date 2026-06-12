# 开发指南

## 环境搭建

```bash
git clone <repo_url>
cd MAAOrch
pip install -r requirements.txt
pip install ruff pytest
```

## 项目结构

```
MAAOrch/
├── main.pyw                          # 入口 (UAC + 单实例 + 代理)
├── main_window.py                    # 主窗口 (800+ 行，UI + 逻辑)
├── app/
│   ├── service_context.py            # ServiceContext 数据桥
│   └── api_server.py                 # HTTP API 服务
├── models/
│   ├── config_manager.py             # 配置加载/保存/迁移
│   ├── config_injector.py            # MAA 配置注入服务
│   ├── account.py                    # Account dataclass (a["key"] / a.key)
│   └── config.json                   # 全局配置 + 账号列表
├── services/
│   ├── emu_service.py                # ADB / 模拟器操作服务
│   ├── log_parser.py                 # 日志解析 / 统计服务
│   ├── instance_pool.py              # 守护 / 更新 / 托盘服务
│   ├── update_service.py             # 下载 / 更新线程
│   └── queue.json                    # 持久化队列
├── infrastructure/
│   ├── background_thread.py          # 通用后台线程 (BackgroundTask)
│   ├── pipeline_thread.py            # 流水线调度线程
│   └── schedule_thread.py            # 定时任务线程
├── models/accounts/{id}/stats.json   # 运行统计 (RunStats)
├── services/maa/instances/{N}/       # 各实例 MAA 目录
├── task_constants.py                 # 任务常量 / 模拟器检测
├── themes.py                         # 暗色/亮色 QSS 样式
├── dialogs.py                        # 对话框组件
├── utils.py                          # 工具函数
├── backups/                          # 配置备份
├── tests/                            # 测试
└── docs/                             # 文档
```

## 编码规范

### 代码风格

- **ruff** 检查，配置于 `pyproject.toml`
- 行长度限制 200 字符
- 双引号字符串，空格缩进
- 禁止注释（除特殊情况外）

### 导入风格

- 标准库 → 第三方库 → 本地模块，每组空行分隔
- 本地模块优先使用绝对导入

### 命名约定

- 文件名：`snake_case`
- 类名：`PascalCase`
- 函数/方法：`snake_case`
- 私有方法：`_snake_case`
- UI 组件变量：简短命名（如 `sl` = status label）

### 线程安全

- 所有 UI 操作必须在主线程执行
- 子线程通过 Qt Signal 向主线程发送数据
- 避免在子线程中直接操作 `self.mw` 等 UI 组件

## Account 模型

`models/account.py` 的 Account dataclass 支持 `a["key"]` 和 `a.key` 两种访问方式。`_TRANSIENT` 开头的字段不持久化。

## 配置版本迁移

`models/config_manager.py` 包含自动迁移机制，确保旧版本配置文件兼容。

### 当前版本：v5

### v4 → v5 (`migrate_v4_to_v5()`)

迁移内容：
- 为每个账号添加默认字段（`emu_launch`、`sync_tasks`、`start_minimized`、`adb_retry`、`stats` 等）
- 为仓库条目添加 `guard_enabled`、`guard_max_restart`、`update_channel`、`launch_mode`、`account_ref`、`maa_type` 等
- 自动检测 MAA 类型（路径含 "MAA" 或 `maa`）
- 解析版本号

### ADB 地址自动修复

`load_config()` 在加载 v5 配置时自动修复编码问题：

```python
"27.0.0.1" → "127.0.0.1"  # 首字符丢失修复
```

## 线程模型

| 线程 | 说明 |
|------|------|
| GUI 主线程 | PyQt 主循环 |
| EmuMonitor QThread | `task_constants.py` 模拟器状态轮询 |
| BackgroundTask | `infrastructure/background_thread.py` |
| API 服务线程 | `app/api_server.py` |
| launch 后台线程 | `runner._launch_job` |

## 测试

```bash
pytest tests/ -v
```

68 测试用例分布：
- **test_core.py**: `make_id()`、`parse_maa_version()`、`load_config/save_config` 一致性、v4→v5 迁移、ADB sanitize
- **test_critical.py**: `gui.json` 注入、ADB 端口正则、`parse_log()` 掉落解析、定时任务星期匹配
- **test_emu.py**: 模拟器预设、ADB devices 解析、MuMu 端口公式、mumu-cli 路径发现
- **test_maint.py**: 版本号比较、完整迁移路径（v3→v4→v5）、默认字段完整性
- **test_queue.py**: 入队/出队、优先级排序、模拟器冲突、并行控制
- **test_runner.py**: AccountRunner 启停、进程跟踪、统计记录、Signal 发送
- **test_stats.py**: RunStats 持久化、理智查询、每日汇总、上限截断

## CI

GitHub Actions（`.github/workflows/ci.yml`）单 job lint-and-test：

| 触发 | push / PR → main |
|------|------------------|
| 环境 | `windows-latest`, Python 3.12 |
| 步骤 | ruff check → pytest |

## 主题系统

`themes.py` 导出 `DARK_STYLE` / `LIGHT_STYLE` 两个 QSS 样式表。通过 `app.setStyleSheet()` 全局应用，切换时调用 `_set_theme()` 重新设置样式和 Palette。

## 开机自启

`config_manager.set_auto_start()` 实现：
- **启用**：在 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` 创建 `流水线启动器.bat`
- **禁用**：删除该文件
- bat 内容：`@start "" "{python路径}" "{main.pyw路径}"`

## 配置备份

每次 `save_config()` 保存时自动备份：
- 目标目录：`backups/`
- 文件命名：`config_YYYYMMDD_HHMMSS.json`
- 保留数量：最多 10 份（超出删除最旧的）
