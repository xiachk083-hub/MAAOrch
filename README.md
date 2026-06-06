# MAAOrch

基于 PySide6 的 MAA (MaaAssistantArknights) 多账号批量管理工具，支持 MAA、maa-cli 及其他程序的分组流水线调度。

## 功能

- **分组管理** — 将 MAA 和其他程序组织为分组，支持并行/串行批量启动，自定义预延迟与分组间隔
- **多账号管理** — 每个账号独立配置 ADB、模拟器预设、触控模式、账号切换、任务流水线
- **程序仓库** — 所有可执行程序集中管理（MAA / maa-cli / 其他），可搜索、按分组分配
- **MAA 自动下载/更新** — 从 GitHub Release 下载最新版，支持 Stable / Beta / Alpha 通道切换
- **模拟器多实例** — 通过 mumu-cli 检测 MuMu 12 / MuMu 6 / 雷电 / 夜神 / 逍遥 / 蓝叠实例，自动填路径和 ADB 端口
- **任务流水线** — 可视化勾选任务（启动游戏→刷关→公招→基建→信用→奖励→肉鸽→生息演算），参数详细配置，支持暂停/恢复
- **CLI 模式** — 支持 maa-cli，自动安装和生成 TOML 任务配置与连接配置
- **启动后操作** — 完成后可组合：返回主屏、退出方舟、关闭模拟器、退出 MAA
- **MAA 日志统计** — 解析 asst.log 展示任务时间线、掉落、理智，实时显示当前任务，支持 MAA v5/v6 双格式
- **进度监控** — 状态栏实时展示运行时长和当前任务名，异常退出弹出托盘通知
- **守护进程** — 进程异常退出时可弹窗询问重启（支持 MAA 程序绑定账号自动注入配置），每个程序可独立设置最大重启次数和是否捕获日志
- **定时任务** — 支持每日/每周定时自动启动流水线
- **理智驱动调度** — 跑完后自动计算理智恢复时间，回满自动再启动
- **运行历史** — 每次运行结果计入 `stats.json`，可查看最近/本周/本月的任务成功率、掉落汇总、理智趋势
- **暗色/亮色主题** — 跟随系统或手动切换
- **开机自启** — 可选添加启动项到 Windows 启动目录
- **通知推送** — 支持企业微信/钉钉等 Webhook 推送运行状态，完成通知附带理智恢复倒计时
- **HTTP API** — REST 接口支持外部调度：状态查询、启动/停止/暂停流水线、配置下发、统计查询
- **daigan 联动** — 接入代肝数据管理面板，运行数据自动推送，见 [daigan-integration.md](docs/daigan-integration.md)
- **代理自动检测** — 启动时自动探测本地代理（Clash/v2ray），确保 GitHub 访问通畅
- **打包构建** — PyInstaller 一键打包独立 .exe，内置 maa-cli 和 MaaCore.dll
- **ADB 工具** — 内置 ADB 扫描、连接测试、截图功能
- **配置导入/导出** — 支持导出/导入全局配置和单个账号配置

## 运行环境

- Windows 10/11
- Python 3.12+
- PySide6 (`pip install PySide6`)
- MuMu 模拟器（可选，也支持雷电、夜神、逍遥、蓝叠）

## 启动

双击 `main.pyw` 或命令行：
```bash
python main.pyw
```

首次启动需同意 UAC 弹窗（管理员权限），之后自动最小化到系统托盘。程序同时只允许运行一个实例。

## 使用流程

### 1. 创建账号
切换到「👤 账号」标签 → 点击「＋」→ 填写信息：
- 账号名、区服（官服 / B服 / 国际服 / 日服 / 韩服 / 繁中）
- ADB 路径、ADB 地址
- 连接预设、触控模式（ADB / MiniTouch / MaaTouch）
- 勾选需要执行的任务

### 2. 下载 MAA
选中账号 → 在右侧仪表盘点击「⬇ 下载 MAA」，自动从 GitHub 下载最新版到 `accounts/{账号ID}/MAA/`。每个账号独立目录，互不干扰。也可通过「📂 绑定」手动选择已有的 MAA 程序。

### 3. 更新/切换版本
在仪表盘的「📦 MAA 状态」卡片中可切换更新通道（Stable / Beta / Alpha），点击「🔄 切换版本」下载对应通道版本。也可通过菜单「工具 → 检查更新」批量检查所有 MAA 程序。

### 4. 配置模拟器
- 选择模拟器预设（如 MuMu 12）
- 点击「🔍 扫描」检测 ADB 在线设备
- 点击「🔄」刷新模拟器实例列表
- 从下拉框选实例，自动填启动路径和 ADB 端口
- 使用「🔍 扫端口」通过 mumu-cli 启动模拟器，自动从实例数据获取真实 ADB 端口并等待上线（不猜测端口号）
- 点击「⏻ 关闭」通过 mumu-cli 关闭模拟器实例

### 5. 配置流水线
- 勾选要执行的任务（启动游戏、刷关作战、公开招募、基建换班、信用商店、领取奖励、肉鸽探索、生息演算）
- 点击「⚙ 参数」进入详细配置：关卡名、用药数、基建设施、肉鸽主题等
- 可选「💾 模板」保存/加载参数配置
- 选择 gui 或 cli 模式（cli 模式需安装 maa-cli）

### 6. 启动
点击「▶ 启动」→ 模拟器先启动 → 等待 N 秒 → MAA 自动注入连接配置 → 运行。也可点击「▶ 启动全部」按顺序逐个启动所有账号。

## 仪表盘卡片

| 卡片 | 说明 |
|------|------|
| 📦 MAA 状态 | 版本号、运行状态（🟢运行中 + 时长）、切换通道、📊统计、📋日志、自动更新资源、理智回满自动启动 |
| 📱 连接 | 模拟器预设、ADB 路径/地址、🔍扫描、测试连接、切换账号、📸截图 |
| 🖥 启动模拟器 | 实例列表（名称+端口+▶运行标识）、启动路径、🔄刷新、📂浏览、⏻关闭、🔍扫端口 |
| ⚙ 流水线 | 任务勾选、参数配置、💾模板、启动时同步、gui/cli 模式 |
| 🔄 启动选项 | 最小化启动、直接运行、ADB 失败自动启动模拟器、ADB 连接重试 |
| 🏁 完成后 | 多选：返回主屏、退出方舟、关闭模拟器、退出 MAA |

## 分组与仓库

「📋 分组」视图下：
- 左侧为分组列表，显示「#序号 名称 并行/串行 N个程序」
- 通过「📦 仓库」标签管理所有程序（添加 .exe、按名称搜索、勾选分配到当前分组）
- 「📋 当前组」标签设定组名、运行模式（并行/串行）、每项预延迟时间
- 双击分组中的程序可直接启动

## 配置备份

每次保存配置自动备份到 `backups/` 目录，保留最近 10 份。

## 日志

- 启动器日志：`debug.log`（超过 100KB 自动裁剪）
- MAA 日志：点击仪表盘「📋 日志」查看 `asst.log`
- 点击底部状态栏可展开/收起日志面板

## 通知

- **托盘通知**：MAA 完成或异常时弹出气泡提示，附带理智恢复倒计时
- **Webhook**：在「设置 → 通知URL」填入企业微信/钉钉/自定义 Webhook 地址，运行状态自动推送

## HTTP API

MAAOrch 启动后自动在 `127.0.0.1:19999` 开启 REST 服务（端口可在设置中修改），供外部调度系统调用。认证通过 Header `x-agent-token` 传递，token 为空则不验证。详细文档见 `docs/daigan-integration.md`。

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 全部账号运行状态 + 流水线状态 |
| `/api/account/{index}/status` | GET | 单个账号状态 |
| `/api/pipeline/start` | POST | 启动流水线 |
| `/api/pipeline/stop` | POST | 停止流水线 |
| `/api/pipeline/pause` | POST | 暂停/恢复（`{"action":"pause"/"resume"}`） |
| `/api/account/{index}/launch` | POST | 启动单个账号 |
| `/api/logs?lines=N` | GET | 读取最近 N 行 debug.log（默认 50） |
| `/api/config/sync` | POST | 下发 MAA gui.json 配置到指定账号 |
| `/api/account/{index}/stats` | GET | 单个账号运行统计 |
| `/api/stats` | GET | 全部账号统计汇总 |
| `/api/queue` | GET | 队列状态（排队+运行中） |
| `/api/queue/enqueue` | POST | 入队（`{"account_index":0,"source":"manual"}`） |
| `/api/queue/dequeue` | POST | 取消排队（`{"account_index":0}`） |

安全措施：

- 仅监听 `127.0.0.1`，不暴露公网
- 可选 Token 鉴权
- 60 次/分钟的请求频率限制（返回 HTTP 429）
- 修改端口/Token 后自动重启服务

## 代理设置

启动时自动检测代理以访问 GitHub：

1. 优先使用环境变量 `HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` / `https_proxy`
2. 未设置则探测本地常见代理端口：7890、7891、1080、10809、8080（Clash、v2ray 等）
3. 检测到后自动为 GitHub 下载和更新检查启用代理

## 环境变量

| 变量 | 说明 |
|------|------|
| `HTTP_PROXY` / `HTTPS_PROXY` | HTTP(S) 代理地址，优先于自动探测 |
| `MUMU_CLI_HOME` | 自定义 MuMu 模拟器安装目录，优先于默认搜索路径 |

## 打包构建

使用 PyInstaller 构建独立 `.exe`（无需安装 Python）：

```bash
pip install pyinstaller
pyinstaller MAAOrch.spec
```

构建产物为 `dist/MAAOrch.exe`，包含 maa-cli 工具和 MaaCore.dll。`MAAOrch.spec` 已配置窗口模式（无控制台）、UPX 压缩、自定义图标（需提供 `icon.ico`）。

## 开发

```bash
pip install -r requirements.txt    # 安装依赖
pip install ruff pytest             # 安装开发工具

ruff check .                        # 代码检查
pytest tests/ -v                    # 运行测试
```

测试覆盖：

- `test_core.py` — 配置加载/保存、ID 生成、版本解析、迁移
- `test_critical.py` — gui.json 注入、ADB 端口提取、MAA 日志解析、定时匹配
- `test_emu.py` — 模拟器预设、ADB 设备列表解析、MuMu 端口公式、mumu-cli 发现
- `test_maint.py` — 版本比较链、配置迁移路径、字段默认值
- `test_queue.py` — 启动队列入队/出队、优先级排序、模拟器冲突、并行启动
- `test_runner.py` — AccountRunner 启动/停止、进程跟踪、统计记录、信号
- `test_stats.py` — RunStats 持久化、理智查询、每日汇总、上限截断

CI（GitHub Actions）在 push/PR 到 main 分支时自动执行 ruff 检查和 pytest 测试。

## 目录结构

```
MAAOrch/
├── main.pyw                  # 入口
├── main_window.py            # 主窗口
├── emu_ops.py                # ADB / 模拟器操作
├── config_ops.py             # MAA 配置注入
├── log_ops.py                # 日志解析 / 统计
├── maint_ops.py              # 监控 / 更新 / 托盘
├── account.py                # Account 数据类
├── runner.py                 # 单号启动→监控→完成回调
├── launch_queue.py           # 统一启动队列（手动/定时/理智）
├── stats.py                  # 运行历史持久化
├── pipeline_thread.py        # 流水线调度
├── api_server.py             # HTTP API 服务
├── config.py                 # 配置加载 / 迁移
├── utils.py                  # 工具函数
├── task_constants.py         # 任务常量 + 模拟器检测
├── themes.py                 # 暗色 / 亮色主题
├── dialogs.py                # 对话框
├── updater.py                # 下载 / 更新
├── schedule_thread.py        # 定时任务
├── callbacks.py              # ServiceContext 依赖注入
├── background.py             # BackgroundTask 通用线程
├── config.json               # 配置文件
├── debug.log                 # 启动器日志
├── accounts/                 # 各账号的 MAA 目录
├── maa-cli/                  # maa-cli 命令行工具
├── backups/                  # 配置备份
├── screenshots/              # ADB 截图
├── ui/                       # UI 面板模块
│   ├── dashboard.py          # 账号仪表盘
│   ├── groups_panel.py       # 分组/仓库面板
│   └── accounts_panel.py     # 账号列表面板
├── tests/                    # 测试
├── docs/                     # 技术文档
│   ├── architecture.md
│   ├── account-management.md
│   ├── pipeline.md
│   ├── task-config.md
│   ├── monitoring.md
│   ├── update-download.md
│   ├── http-api.md
│   ├── dev-guide.md
│   └── daigan-integration.md
└── README.md
```

## 快捷键

| 按键 | 功能 |
|------|------|
| `Ctrl+Enter` | 启动流水线 |
| `Esc` | 停止流水线 |

## 故障排除

- **双击没反应** — 确认 PySide6 已安装：`pip install PySide6`
- **MAA 不启动** — 检查 `accounts/{id}/MAA/MAA.exe` 是否存在
- **模拟器不启动** — 确认模拟器已安装，可通过「浏览」手动选择 exe
- **ADB 连不上** — 先从实例下拉框选择正确实例（自动填入真实 ADB 端口），再点击「🔍 扫端口」启动模拟器并等待端口上线
- **任务参数不生效** — 勾选「启动时同步」
- **查看详细错误** — 菜单 → 日志，或查看 `debug.log`
- **程序已运行** — 程序仅允许单实例，再次启动会自动激活已有窗口

## 技术文档

| 文档 | 内容 |
|------|------|
| [系统架构](docs/architecture.md) | 模块划分、数据流、线程模型、ServiceContext、启动队列架构 |
| [多账号与模拟器](docs/account-management.md) | Account 类、ADB 工具、模拟器多实例、mumu-cli 集成 |
| [流水线调度](docs/pipeline.md) | LaunchQueue、AccountRunner、分组调度、定时任务、理智驱动 |
| [任务配置注入](docs/task-config.md) | gui.json 注入、maa-cli TOML 生成、任务参数映射 |
| [日志与监控](docs/monitoring.md) | asst.log v5/v6 解析、Version 定位、统计持久化、日志轮转 |
| [下载更新与代理](docs/update-download.md) | MAA/maa-cli 下载更新、版本切换、代理自动检测 |
| [HTTP API](docs/http-api.md) | REST 接口完整参考、安全机制、stats 端点、集成示例 |
| [开发指南](docs/dev-guide.md) | 环境搭建、编码规范、配置迁移、测试、打包 |
| [daigan 对接](docs/daigan-integration.md) | stats.json 格式、字段说明、统计计算示例 |

## 技术栈

- Python 3.12
- PySide6 (Qt 6 GUI)
- mumu-cli (MuMu 模拟器管理)
- MAA v6 配置注入 (gui.json / gui.new.json)
- maa-cli (命令行模式)
- GitHub Release API (自动更新)
- ADB (设备连接与截图)
