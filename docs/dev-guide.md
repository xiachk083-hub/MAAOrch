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
