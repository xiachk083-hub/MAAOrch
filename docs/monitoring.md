# 日志、统计与监控

## 日志系统

### 启动器日志

MAAOrch 自身的运行日志写入根目录的 `debug.log`：

- 最大 100KB，超过自动裁剪保留最近 200 行
- 通过底部状态栏点击展开/收起日志面板实时查看
- HTTP API `GET /api/logs?lines=N` 可读取最近 N 行

### MAA 日志

每个 MAA 程序的日志位于 `{MAA目录}/debug/asst.log`，由 MAA 自身写入，MAAOrch 负责解析和展示。

## 日志解析 (`LogService`)

### parse_log()

`LogService.parse_log()` 解析 `asst.log` 尾部 500 行，返回任务列表：

```python
[
    {"name": "开始唤醒", "start": "08:00:01", "status": "完成", "drops": "", "error": ""},
    {"name": "刷关作战", "start": "08:02:15", "status": "完成", "drops": "固源岩x3,装置x1", "error": ""},
    {"name": "公开招募", "start": "08:10:30", "status": "失败", "drops": "", "error": "连接超时"},
]
```

#### 解析规则

| 日志关键词 | 操作 |
|------------|------|
| `append_task` | 记录新任务开始（匹配任务名映射） |
| `[ERR]` | 标记当前任务失败，提取错误信息前 100 字符 |
| `TaskSwitched` | 标记当前任务完成 |
| `StageDrops` | 提取掉落物品（如 `固源岩 x 3`），保留最近 5 项 |

### 任务名映射

| 日志标识 | 中文名 |
|----------|--------|
| `StartUp` | 开始唤醒 |
| `Fight` | 刷关作战 |
| `Recruit` | 公开招募 |
| `Infrast` | 基建换班 |
| `Mall` | 信用商店 |
| `Award` | 领取奖励 |
| `Roguelike` | 肉鸽探索 |
| `Reclamation` | 生息演算 |
| `CloseDown` | 关闭游戏 |

## 统计展示

`LogService.show_stats()` 弹出对话框，以表格形式展示：

| 任务 | 状态 | 详情 |
|------|------|------|
| 开始唤醒 | 完成 | - |
| 刷关作战 | 完成 | 固源岩x3 |
| 公开招募 | 失败 | 连接超时: ... |

状态颜色：完成 = 绿色，失败 = 红色。

## 日志查看

`LogService.view_log()` 弹出对话框展示 `asst.log` 尾部 200 行，自动滚动到底部。

## 实时监控 (`MaintService.poll()`)

`MaintService.poll()` 由主窗口定时器每 2 秒（`POLL_INTERVAL_MS = 2000`）调用，执行：

### 1. 进程存活检测

- 遍历 `_running_procs`，对已退出的进程调用 `poll()`
- 若退出码非零且 `guard_enabled` 为 true，弹出 `QMessageBox` 询问是否重启
- 若退出码为零，检查日志中是否有失败任务，触发托盘通知

### 2. CLI 进程检测

- 遍历 `_cli_procs`，读取 stdout/stderr 输出并记录日志
- 退出码为 0 时通知"已完成"，非零时通知"异常退出"

### 3. 状态栏更新

- 显示运行时长（分钟+秒）
- 从 `asst.log` 尾部 3 行中提取 `append_task` 关键词，匹配当前任务名显示

### 4. MAA 错误捕获

- 扫描日志尾部 `[ERR]` 行，提取错误信息并写入启动器日志
- 同时触发托盘错误通知

## 守护进程配置

每个仓库条目支持独立守护设置：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `guard_enabled` | bool | MAA 自动下载=true, 手动绑定=false | 是否启用守护 |
| `guard_max_restart` | int | 3 | 最大重启次数 |
| `guard_capture_log` | bool | false | 崩溃时是否捕获 asst.log |

守护逻辑：进程异常退出 → 弹出对话框"是否重启？" → 用户确认后重新调用 `_ls(w)` 启动 → 重启次数由调用方控制。

## 通知系统

### 托盘通知

`MaintService.notify()` 调用 `QSystemTrayIcon.showMessage()` 弹出气泡：

- 完成通知：信息图标，持续 3 秒
- 错误通知：错误图标，持续 3 秒

### Webhook 通知

若配置了 `webhook_url`，同步发送 HTTP POST：

```json
{
  "msg": "MAA 完成",
  "type": "info",
  "time": "2025-01-01T08:00:00"
}
```

- Content-Type: `application/json`
- 超时: 5 秒
- 失败静默处理，写入 debug.log
- 支持企业微信、钉钉等兼容 Webhook 的服务
