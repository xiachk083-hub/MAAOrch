# 开发指南

## 环境搭建

```bash
git clone <repo_url>
cd MAAOrch
pip install -r requirements.txt
pip install ruff pytest  # 开发工具
```

## 项目结构

```
MAAOrch/
├── main.pyw                 # 入口 (UAC + 单实例 + 代理)
├── main_window.py           # 主窗口 (800+ 行，UI + 逻辑)
├── config.py                # 配置加载/保存/迁移
├── config_ops.py            # MAA 配置注入服务
├── emu_ops.py               # ADB / 模拟器操作服务
├── log_ops.py               # 日志解析 / 统计服务
├── maint_ops.py             # 守护 / 更新 / 托盘服务
├── pipeline_thread.py       # 流水线调度线程
├── schedule_thread.py       # 定时任务线程
├── api_server.py            # HTTP API 服务
├── updater.py               # 下载 / 更新线程
├── task_constants.py        # 任务常量 / 模拟器检测
├── themes.py                # 暗色/亮色 QSS 样式
├── dialogs.py               # 对话框组件
├── callbacks.py             # ServiceContext 数据类
├── background.py            # 通用后台线程
├── utils.py                 # 工具函数
├── config.json              # 配置文件
├── accounts/                # 各账号 MAA 目录
├── maa-cli/                 # maa-cli 工具
├── backups/                 # 配置备份
├── tests/                   # 测试
└── docs/                    # 文档
```

## 编码规范

### 代码风格

- **ruff** 检查，配置见 `pyproject.toml`
- 行长度限制 200 字符
- 双引号字符串
- 空格缩进
- 禁止注释（除特殊情况外）

### 命名约定

- 文件名：`snake_case`
- 类名：`PascalCase`
- 函数/方法：`snake_case`
- 私有方法：`_snake_case`（前缀下划线）
- UI 组件变量：简短命名（如 `sl` = status label）

### 线程安全

- 所有 UI 操作必须在主线程执行
- 子线程通过 Qt Signal 向主线程发送数据
- 避免在子线程中直接操作 `self.mw` 的 UI 组件

## 配置版本迁移

`config.py` 包含自动迁移机制，确保旧版本配置文件兼容：

### 当前版本：v5

### v4 → v5 (`migrate_v4_to_v5()`)

迁移内容：
- 为每个账号添加默认字段（`emu_launch`、`sync_tasks`、`start_minimized`、`adb_retry`、`stats` 等）
- 为仓库条目添加 `guard_enabled`、`guard_max_restart`、`update_channel`、`launch_mode`、`account_ref`、`maa_type` 等
- 自动检测 MAA 类型（路径含 "MAA" → `maa`）
- 解析版本号

### ADB 地址自动修复（v5 加载时）

`load_config()` 在加载 v5 配置时自动修复编码问题：

```python
"27.0.0.1" → "127.0.0.1"  # 首字符丢失修复
```

## 测试

```bash
pytest tests/ -v
```

### test_core.py

测试范围：
- `make_id()` — UUID 生成格式（8 位十六进制）
- `parse_maa_version()` — 从路径提取版本号
- `_version_tuple()` — 版本号比较
- `get_platform_key()` — 平台标识
- `load_config()` → `save_config()` → 重新加载一致性
- v4 → v5 迁移完整性
- ADB 地址 sanitize 逻辑

### test_critical.py

测试范围：
- `gui.json` 注入后关键字段验证
- ADB 端口正则提取
- `parse_log()` 任务时间线和掉落解析
- 定时任务星期匹配逻辑

### test_emu.py

测试范围：
- 模拟器预设数据完整性
- ADB devices 输出解析
- MuMu 端口公式 `16384 + index * 32`
- mumu-cli 路径发现

### test_maint.py

测试范围：
- 版本号比较链
- 完整配置迁移路径（v3 → v4 → v5）
- 默认字段完整性

## 打包构建

```bash
pip install pyinstaller
pyinstaller MAAOrch.spec
```

### spec 关键配置

| 选项 | 值 | 说明 |
|------|----|------|
| 入口 | `main.pyw` | 窗口模式（无控制台） |
| 打包数据 | `maa-cli/`, `themes.py`, `task_constants.py`, `callbacks.py` | 运行时必需 |
| 压缩 | UPX | 减小体积 |
| 排除模块 | tkinter, unittest, email, http, xml, pydoc | 减小体积 |
| 图标 | `icon.ico`（若存在） | 自定义图标 |

产物路径：`dist/MAAOrch.exe`

## CI/CD

GitHub Actions 工作流（`.github/workflows/ci.yml`）：

| 触发 | push / PR 到 main 或 master |
|------|-----------------------------|
| 环境 | `windows-latest`, Python 3.12 |
| 步骤 | pip install → ruff check → pytest |

## 主题系统

`themes.py` 导出两个 QSS 样式表字符串：

- `DARK_STYLE` — 暗色主题
- `LIGHT_STYLE` — 亮色主题

通过 `app.setStyleSheet()` 全局应用。切换主题时调用 `_set_theme()`，重新设置 `QApplication` 的样式表和 `Palette`。

主题跟随系统设置（`appearance_mode` 配置项）或在设置对话框中手动切换。

## 开机自启

`config.set_auto_start()` 实现：

- **启用**：在 Windows 启动目录 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` 创建 `流水线启动器.bat`
- **禁用**：删除该文件
- bat 内容：`@start "" "{python路径}" "{main.pyw路径}"`

## 配置备份

每次保存配置（`save_config()` 调用时）自动备份：

- 目标目录：`backups/`
- 文件命名：`config_YYYYMMDD_HHMMSS.json`
- 保留数量：最近 10 份（超出删除最旧的）
