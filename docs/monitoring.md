# 日志、统计、监控与调度

## 日志系统

### 启动器日志

MAAOrch 自身的运行日志写入根目录的 `debug.log`：

- 最大 100KB，超过自动裁剪保留最近 200 行
- 通过底部状态栏点击展开/收起日志面板实时查看
- HTTP API `GET /api/logs?lines=N` 可读取最近 N 行

### MAA 日志

每个 MAA 程序的日志位于 `{MAA目录}/debug/asst.log`，由 MAA 自身写入，MAAOrch 负责解析和展示。MAA 日志永久追加，不会自动轮转。

## 日志解析 (`LogService`)

### parse_log()

`LogService.parse_log()` 通过以下策略提取最新一轮运行结果：

1. **Version 定位** — 倒序扫描 asst.log，找到 `Version v` 标记，只解析最新一轮
2. **统一 JSON 提取** — 同时兼容 `append_callback | SubTaskStart {...}` 和裸 `{"taskchain":"Fight",...}` 两种日志格式
3. **去重** — 同一 taskchain 重复出现时更新而非新增（应对多轮重启场景）
4. **过滤** — 排除 MAA 内部任务链（Depot、OperBox）

返回 `(tasks, sanity, drops)` 三元组：

```python
tasks = [
    {"name": "刷关作战", "start": "08:39:03", "status": "完成", "drops": "", "error": ""},
    {"name": "公开招募", "start": "08:40:15", "status": "完成", "drops": "", "error": ""},
    ...
]
sanity = {"current": 5, "max": 210, "report_time": "2026-06-06 09:36:33"}
drops = {"固源岩": 21, "赤金": 12}
```

#### 解析规则 (v6)

| 日志关键词 | 操作 |
|------------|------|
| `SubTaskStart` JSON 含 `taskchain` | 记录新任务（去重：同一 chain 更新而非新增） |
| `TaskChainCompleted` JSON | 标记对应任务完成 |
| `AllTasksCompleted` JSON | 标记所有 `运行中` 任务为完成 |
| `SubTaskExtraInfo` + `"what":"SanityBeforeStage"` | 提取当前理智值 |
| `SubTaskExtraInfo` + `"what":"StageDrops"` | 提取累积掉落统计 |
| `SubTaskExtraInfo` + `"what":"ExceededLimit"` | 标记任务失败 |
| `[ERR]` | 标记当前任务失败，提取错误信息 |

### 任务名映射

| 日志标识 | 中文名 | 是否主任务 |
|----------|--------|-----------|
| StartUp | 开始唤醒 | ✅ |
| Fight | 刷关作战 | ✅ |
| Recruit | 公开招募 | ✅ |
| Infrast | 基建换班 | ✅ |
| Mall | 信用商店 | ✅ |
| Award | 领取奖励 | ✅ |
| Roguelike | 肉鸽探索 | ✅ |
| Reclamation | 生息演算 | ✅ |
| CloseDown | 关闭游戏 | ✅ |
| Depot | 仓库扫描 | ❌ 内部 |
| OperBox | 干员识别 | ❌ 内部 |

## 统计展示与持久化

### show_stats()

点击「📊 统计」打开多标签对话框：

| 标签 | 数据源 | 说明 |
|------|--------|------|
| 最近 | stats.json | 最近 15 次运行记录 |
| 今日 | stats.json | 当日汇总（次数、任务、掉落） |
| 本周 | stats.json | 本周汇总 |
| 实时 | asst.log | 当前运行实时解析（作为补充） |

显示理智恢复倒计时和掉落汇总。若无历史记录则回退到实时解析。

### stats.json 持久化

`RunStats` 类（`stats.py`）每次运行完成后自动写入 `accounts/{id}/stats.json`：

```json
{
  "runs": [{
    "ts": "2026-06-06 09:45:29",
    "tasks": {"开始唤醒":"完成", "刷关作战":"完成"},
    "drops": {"固源岩":21, "赤金":12},
    "sanity": {"current":5, "max":210, "deficit":205}
  }]
}
```

保留最近 200 次，支持跨启动查询本月/本周汇总。

## 实时监控 (`MaintService.poll()` → `AccountRunner.check_processes()`)

`MaintService.poll()` 由主窗口定时器每 2 秒调用，执行：

### 1. CLI 进程检测

遍历 `_cli_procs`，读取 stdout/stderr，退出时记录日志和通知。

### 2. GUI 进程检测 → 委托给 Runner

调用 `runner.check_processes()`，由 `AccountRunner` 统一管理进程生命周期：

- 进程退出 → 调用 `_parse_log()` 解析 asst.log
- 提取任务状态、理智、掉落
- 发射 `account_finished(aid, exit_code, tasks)` 信号
- 自动保存到 `stats.json`

### 3. 状态栏更新

- 显示运行时长（分钟+秒）
- 从 asst.log 尾部提取当前 taskchain，显示 "MAA: 刷关..."

## 启动队列 (`LaunchQueue`)

### 统一入口

所有启动请求都进入 `LaunchQueue`，按优先级排序：

| 来源 | priority | 触发方式 |
|------|----------|----------|
| 手动 | 0（最高） | 用户点击「▶ 启动」或 API |
| 定时 | 1 | ScheduleThread 定时触发 |
| 理智 | 2 | 上一轮完成后自动入队，设置 `not_before` 为恢复时间 |

### 调度规则（tick 每 30 秒）

```
取队首 → 检查：
  ① 已在运行？ → 跳过
  ② 模拟器被占？ → 跳过
  ③ 还没到 not_before？ → 跳过（后面的也不会到时间）
  ④ 理智不够（仅 sanity 来源）？ → 跳过
  ── 全部满足 ──
  → 启动 → 标记模拟器占用
```

**核心原则：绝不中断正在运行的 MAA。只等空闲时启动下一个。**

### 理智驱动流程

```
account_finished(大号)
  → 读 stats.json 获取最后理智 (5/210)
  → 计算恢复时间: 205 × 6 = 1230min ≈ 20.5h
  → LaunchQueue.enqueue("大号", "sanity", priority=2, not_before=明天04:30)

tick 每 30s:
  → 检查队列 → 大号 not_before 未到 → 跳过
  → 小号满足条件 → 启动
  → 小号跑完 → 大号还没到时间 → 跳过
  → ...第二天 04:30...
  → 大号 not_before 已过，模拟器空闲 → 启动
```

## 通知系统

### 托盘通知

`MaintService.notify()` 调用 `QSystemTrayIcon.showMessage()` 弹出气泡：

- 完成通知：信息图标 + 理智恢复倒计时（"MAA 完成: 5 个任务 | 理智 5/210 (20h30m回满)"）
- 错误通知：错误图标，持续 3 秒

### Webhook 通知

若配置了 `webhook_url`，同步发送 HTTP POST，格式与企业微信/钉钉兼容。

## 守护进程配置

每个仓库条目支持独立守护设置：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `guard_enabled` | bool | MAA 自动下载=true | 是否启用守护 |
| `guard_max_restart` | int | 3 | 最大重启次数 |
| `guard_capture_log` | bool | false | 崩溃时是否捕获日志 |
