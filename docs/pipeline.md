# 流水线调�?

## 分组与仓�?

MAAOrch 支持将可执行程序组织为分组，按组批量启动�?

### 数据结构

**分组** (`groups[]`)�?

```json
{
  "name": "主力账号",
  "mode": "serial",
  "post_delay": 3,
  "programs": [
    {"ref": "warehouse_id_1", "pre_delay": 0},
    {"ref": "warehouse_id_2", "pre_delay": 5}
  ]
}
```

**仓库条目** (`warehouse[]`)�?

```json
{
  "id": "warehouse_id_1",
  "path": "C:\\path\\to\\MAA.exe",
  "args": [],
  "cwd": "",
  "env": {},
  "maa_type": "maa",
  "maa_version": "v6.11.1",
  "account_ref": "account_id",
  "launch_mode": "gui",
  "task_pipeline": "startup,fight,recruit,infrast,mall,award",
  "guard_enabled": true,
  "guard_max_restart": 3,
  "guard_capture_log": false
}
```

### 分组模式

| 模式 | 行为 |
|------|------|
| `serial` | 逐个启动，每项之间有 `pre_delay` 秒间�?|
| `parallel` | 同时启动所有程�?|

### 程序类型

`maa_type` 字段标识程序类型�?

| �?| 说明 |
|----|------|
| `maa` | MAA 图形界面程序 |
| `maa-cli` | maa-cli 命令行工�?|
| `general` | 通用可执行程�?|

## LaunchQueue 统一启动队列

`LaunchQueue`（`launch_queue.py`）是系统的核心调度入口，所有启动请求都先进入队列�?

### 队列条目

```python
@dataclass
class QueueEntry:
    sort_key: tuple     # (priority, not_before)
    account_id: str
    source: str         # "manual" | "schedule" | "sanity"
    not_before: datetime
```

### 优先�?

| 来源 | priority | 触发方式 |
|------|----------|----------|
| 手动 | 0（最高） | 用户点击「▶ 启动」或 API |
| 定时 | 1 | ScheduleThread 定时触发 |
| 理智 | 2 | 上一轮完成后自动入队，设�?`not_before` 为恢复时�?|

### 调度规则（tick �?30 秒）

```
取队�?�?检查：
  �?已在运行�?�?跳过
  �?模拟器被占？ �?跳过
  �?还没�?not_before�?�?跳过（后面的也不会到时间�?
  �?理智不够（仅 sanity 来源）？ �?跳过
  ── 全部满足 ──
  �?启动 �?标记模拟器占�?
```

**核心原则：绝不中断正在运行的 MAA。只等空闲时启动下一个�?*

### 理智驱动流程

```
account_finished(大号)
  �?�?stats.json 获取最后理�?(5/210)
  �?计算恢复时间: 205 × 6 = 1230min �?20.5h
  �?LaunchQueue.enqueue("大号", "sanity", priority=2, not_before=明天04:30)

tick �?30s:
  �?检查队�?�?大号 not_before 未到 �?跳过
  �?小号满足条件 �?启动
  �?小号跑完 �?大号还没到时�?�?跳过
  �?...第二�?04:30...
  �?大号 not_before 已过，模拟器空闲 �?启动
```

### API

```python
queue.enqueue(account_id, source, priority, not_before)
queue.enqueue_batch(source, priority, accounts)
queue.dequeue(account_id)
queue.pending_count          # 排队�?
queue.active_count           # 运行中数
queue.is_queued(account_id)
queue.is_running(account_id)
queue.pending_summary()      # 状态栏文本
queue.get_next_for(account_id)  # 下次启动时间
```

## PipelineThread 调度线程

`PipelineThread`（`pipeline_thread.py`）继�?`QThread`，用于分组批量启动（非队列模式）�?

```
for 每个分组:
    if stop_flag: break
    while pause_flag: sleep(200ms)
    发射 progress 信号
    if mode == "parallel":
        for 每个程序: _launch(程序)
    else (serial):
        for 每个程序:
            sleep(pre_delay)
            _launch(程序)
    sleep(post_delay)
发射 finished 信号
```

### 程序启动 `_launch()`

1. 查找仓库条目获取路径、参数、工作目�?
2. 若绑定账号（`account_ref` 非空），调用 `ConfigService.inject_for_thread()` 注入配置
3. `subprocess.Popen()` 启动进程
4. 将进程对象加�?`_running` 列表
5. 发射 `program_started` 信号

### 暂停/恢复

- `pause()`: 设置 `pause_flag=True`，线程在主循环中检测并进入 200ms 休眠等待
- `resume()`: 清除 `pause_flag`，线程继续执�?
- 暂停期间已经启动的子进程继续运行，不会终�?
- 可通过 HTTP API `POST /api/pipeline/pause` 外部控制

### 停止

- `stop()`: 设置 `stop_flag=True`，对所�?`_running` 中的进程调用 `terminate()`

### 进程存活检�?

�?`_sleep()` 等待循环中，�?100ms 检查一�?`_running` 列表中进程是否退出（`poll() is None`），已退出的进程自动移除�?

## AccountRunner 单号启动闭环

`AccountRunner`（`runner.py`）管理单个账号的完整生命周期�?

```
launch(row)
  �?检查前提（�?MAA 程序？绑定模拟器？模拟器空闲？）
  �?启动/连接模拟�?
  �?ConfigService.inject() 注入配置
  �?subprocess.Popen() 启动 MAA
  �?记录�?_procs[aid]
  �?发射 account_started(aid)

check_processes() (�?2s �?proc_timer 调用)
  �?进程退出？
     �?parse_log() 解析任务状态、理智、掉�?
     �?RunStats.save_run() 持久�?
     �?发射 account_finished(aid, exit_code, tasks)
     �?LaunchQueue.on_account_finished() 释放模拟�?
```

## 定时任务

`ScheduleThread`（`schedule_thread.py`）支持两种模式：

### 每日定时

- `type: "daily"`，`time: "08:00"`
- 每天在指定时间触发一�?
- 若当前时间已过目标，顺延到次�?

### 每周定时

- `type: "weekly"`，`time: "08:00"`，`days_of_week: [0,3,6]`（周一=0�?
- 仅在指定星期几触�?
- 搜索未来 7 天内第一个匹配的触发时间

### 防重�?

若上次触发在 120 秒内，跳过本次触发（防止 NTP 校时等导致重复）�?

## 启动选项

每个账号支持以下启动行为控制�?

| 选项 | 说明 |
|------|------|
| `start_minimized` | MAA 启动后最小化到托�?|
| `start_directly` | 跳过唤醒阶段，直接进入任务队�?|
| `adb_fail_launch_emu` | ADB 连接失败时自动启动模拟器 |
| `adb_retry` | ADB 连接失败重试次数 |
| `sync_tasks` | 启动时将任务参数同步写入 gui.json |
| `round_robin_deficit` | 距满�?N 点自动启动（0=回满�?|

## 循环调度

`�?调度`标签页管理全局循环调度配置�?

### 全局设置

| 字段 | 说明 |
|------|------|
| `daily_batch_time` | 每日批量入队时间（如 "04:00"，空=关闭�?|
| `parallel_max` | 最大并�?MAA 进程�?|
| `round_robin_deficit` | 距满�?N 点即自动入队�?=回满�?0=�?30 点就启动�?|

### 调度逻辑

```
1. 每日批量: daily_batch_time 到点 �?全部账号入队 (priority=1)
2. 跑完自动: 算恢复时�?�?距满 �?deficit 时自动入�?(priority=2)
3. 并行控制: 同时运行 �?parallel_max，超过上限排队等�?
```

### 与定时任务的关系

- 定时任务（ScheduleThread）：固定时间触发（每�?每周�?
- 循环调度：每日批�?+ 跑完自动算恢复时�?
- 手动入队：优先级最�?0)，随时可插队

## 启动后操�?

`post_action` 字段控制 MAA 完成后自动执行的操作，位掩码组合�?

| 操作 | 内部标签 |
|------|----------|
| 返回主屏 | `ReturnToMain` |
| 退出方�?| `ExitArknights` |
| 关闭模拟�?| `CloseEmulator` |
| 退�?MAA | `ExitMAA` |
