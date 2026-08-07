# 开发指南

## 环境搭建

```bash
git clone https://github.com/xiachk083-hub/MAAOrch.git
cd MAAOrch
pip install -r requirements.txt
pip install ruff pytest
```

## 项目结构

```
MAAOrch/
├── main_web.pyw                    # Web UI 入口（推荐）
├── main.pyw                        # Qt 桌面版入口
├── main_web.bat                    # 双击启动器
├── app/
│   ├── service_context.py          # ServiceContext 数据桥
│   └── web_context.py              # WebContext 类型化上下文（替代 type('MW',(),{})）
├── network/
│   ├── api_fastapi.py              # FastAPI + uvicorn HTTP 服务（50+ 端点）
│   └── api_server.py               # 旧版 HTTPServer（保留备用）
├── services/
│   ├── runner.py                   # AccountRunner：MAA 全生命周期
│   ├── launch_queue.py             # LaunchQueue：优先级队列调度
│   ├── config_injector.py          # MAA 配置注入（gui.json + gui.new.json）
│   ├── maa_download.py             # 自动下载 / 初始化 MAA
│   ├── dispatch_pool.py            # 调度模板池
│   ├── smart_scheduler.py          # 智能调度决策
│   ├── instance_pool.py            # 实例池创建 / 重建
│   ├── ai_assistant.py             # LLM 任务失败分析
│   └── maa/
│       ├── source/                 # MAA 源目录（自动下载或手动放置）
│       └── instances/{1..N}/       # MAA 实例池（从 source 复制）
├── infrastructure/
│   ├── task_constants.py           # 任务常量、mumu-cli 发现、EmuMonitor
│   ├── logger.py                   # 日志系统（debug / events / crash）
│   ├── maa_core.py                 # MaaCore ctypes 直连（默认禁用）
│   └── platform_helper.py          # UAC 提权 / 管理员检测
├── ui/web/                         # Web UI 前端（SPA）
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── pages/                      # 引导页等
├── models/
│   ├── config_manager.py           # 配置加载/保存/迁移
│   ├── config.json                 # 全局配置 + 账号列表
│   ├── account.py                  # Account dataclass
│   ├── queue_entry.py              # QueueEntry 冻结数据类
│   └── stats.py                    # RunStats 运行统计持久化
├── docs/                           # 文档
├── tests/                          # 测试（pytest，67+ 用例）
└── requirements.txt                # 依赖：PySide6(可选) / fastapi / uvicorn
```

## 核心架构

```
用户浏览器 → FastAPI (uvicorn) → WebContext → AccountRunner / LaunchQueue
                    ↓                            ↓
              SSE 实时推送                MAA.exe 子进程
```

- HTTP 服务器：FastAPI + uvicorn，守护线程运行
- 前端：纯静态 SPA（HTML+CSS+JS），通过 REST API + SSE 通信
- 队列：LaunchQueue 每 5 秒 tick，优先级调度
- 启动：AccountRunner._launch_job → 启动模拟器 → 等 ADB → 注入配置 → Popen MAA.exe
- 监控：_wait_exit 线程等待 MAA 退出 → _cleanup → account_finished 回调

## PySide6 可选

Web 模式不需要 PySide6。如果安装了 PySide6，启动时会有系统托盘图标。
无 PySide6 时自动纯 Web 模式（无托盘图标）。

```python
try:
    from PySide6.QtWidgets import QApplication
    _has_qt = True
except ImportError:
    _has_qt = False
```

## 编码规范

### 代码风格

- 缩进：4 空格
- 行宽：120 字符
- 命名：`snake_case`（变量/函数）、`PascalCase`（类）
- 导入：标准库 → 第三方 → 本地，每组空行分隔

### 测试

```bash
pytest tests/ -x -q
```

所有改动须通过全部测试（67+）。`runner.py` 和 `launch_queue.py` 已去 Qt 依赖，测试不需要 Qt 事件循环。

### 原则

- **不攒技术债**：现在图省事的地方，以后就是瓶颈
- **失败当可见**：守护线程必须有 try/except + 清理兜底
- **只更新传了的字段**：batch API 不能覆盖未传字段

## API 端点

见 `docs/http-api.md`。关键端点：

| 端点 | 说明 |
|------|------|
| `GET /api/status` | 全部账号状态 |
| `GET /api/accounts` | 账号列表（含 stages / smart_annihilation） |
| `POST /api/accounts/batch_save` | 批量保存（只更新传了的字段） |
| `GET /api/accounts/export` | 导出 CSV |
| `POST /api/accounts/csv_import` | 导入 CSV |
| `POST /api/accounts/batch_save` | 表格模式批量保存 |
| `POST /api/action/smart_all` | 一键调度（逐账号判断剿灭） |
| `GET /api/sse` | SSE 实时推送 |

## MAA 配置注入机制（血泪教训）

> 完整映射手册见 **`docs/maa-config-mapping.md`**（含 6.16 结构、字段映射全表、症状速查、诊断方法）。

### MAA 6.16 双配置源（2026-08-07 已适配）

| 文件 | 管理方 | 6.16 有效内容 |
|------|--------|---------------|
| `config/gui.json` | `ConfigurationHelper` | **Global 区**（`GUI.*` 界面设置/热键/`Start.MinimizeDirectly`）；扁平键已废弃 |
| `config/gui.new.json` | `ConfigFactory` | **全部核心设置**：`Configurations.Default.Gui.ConnectSettings` / `Gui.StartUpSettings` / `Gui.PostActions`（字符串枚举）/ `TaskQueue` |

**核心规则**：6.16 起连接/启动设置必须注入 gui.new.json 的 **Gui 嵌套区**（`config_injector._set_connection_v6_gui`），gui.json 扁平键（`Connect.Address`/`Start.RunDirectly` 等）已不读。

### 关键键（gui.new.json `Configurations.Default.Gui.*`）

| 键 | 值 | 作用 |
|----|-----|------|
| `ConnectSettings.AutoDetect` | `false` | **必须关**（默认 true → ADB 框禁用、注入地址被忽略、自动检测错位端口 16992） |
| `ConnectSettings.Address` | `127.0.0.1:<port>` | ADB 地址（AutoDetect=false 才生效） |
| `ConnectSettings.Config` | `MuMuEmulator12` | 连接配置 |
| `ConnectSettings.Extras.MuMuEmulator12.InstanceIndex` | `<emu_idx>` | MuMu12 多开定位（绕开自动检测错位） |
| `StartUpSettings.RunDirectly` | `true` | "启动 MAA 后直接运行" → 自动 LinkStart |
| `StartUpSettings.SkipStartupAutoRunAfterUpdate` | `false` | **必须每次重置**（MAA 更新后默认跳过自动运行） |
| `StartUpSettings.StartEmulator` | `false` | 模拟器由 MAAOrch 管理（true 会卡 TryToStartEmulator 阻塞 LinkStart） |
| `StartUpSettings.EmulatorPath` | mumu-cli / MuMuManager | MuMu 12 无 mumu-cli 时 fallback `MuMuManager.exe` |
| `PostActions` | `"None"`（字符串） | 6.16 不再用数字编码（v5 "8"/"12"） |

### 已踩的坑（务必遵守）

1. **ADB 端口检测只用 `detect_emu_instances`（`--vmindex all`）** — `_auto_derive`/`_launch_job_body`/`_set_connection` 三处统一；只在 `adb_address` 为空时执行。mumu-cli 单查 `--vmindex N` 在 MuMu12 多开时索引错位返回**错误端口**（16992 vs 16708），已全部替换。
2. **注入后删除 `.bak`** — MAA 启动解析失败会从 `.bak` 回退旧 `Connect.Address`（旧模拟器端口残留）。`config_injector._write` 写后 `unlink(gj.bak)`。
3. **MaaCore 直连**（`infrastructure/maa_core.py`）— ctypes 绑定完整（AsstConnect/AppendTask/Start/Stop + 回调），默认禁用（`_launch_core_daily` 无调用者）。**日常自动化走 MAA GUI + RunDirectly**（6.16 注入修复后已可靠）。
4. **6.16 注入必须写 gui.new.json 的 Gui 嵌套区**（`_set_connection_v6_gui`）— gui.json 扁平键 6.16 已不读；`AutoDetect` 默认 true（地址框禁用/注入失效）、`RunDirectly` 默认 false（不自动连）、`SkipStartupAutoRunAfterUpdate` 默认 true（更新后跳过运行）。
5. **MAA 进程启动 `cwd`**：MAA 启动时 `Directory.SetCurrentDirectory(BaseDirectory)`（exe 目录）— cwd 参数不影响配置读取，但 junction 实例目录作 cwd 会 E_FAIL 崩溃，用 `Path(inst_dir).resolve()`。
6. **优雅关闭**：`runner.stop()` 用 WM_CLOSE（`_graceful_close`）→ MAA 走 OnClose 释放 ADB/minitouch；硬杀（TerminateProcess）残留触摸服务 → MuMu 弹"运行异常"。
7. **连接模式（`_connect_only`）MAA 退出是正常** — 空任务自退不应触发模拟器关机（`is_real_error` 排除）。
8. **模拟器"运行异常"**：MuMu 进程在但 Android 没起来（ADB 连不上）→ 需重启模拟器（`POST /api/emulator/{idx}/restart`）。

### 诊断端点

`GET /api/maa/instances/{n}/config` — 看 MAA 实际读的配置：`gui_connect`（Gui.ConnectSettings）/ `gui_startup`（Gui.StartUpSettings）/ `gui_postactions` / `connect`（扁平键）/ dir_files（含 bak）/ task_queue / global。
