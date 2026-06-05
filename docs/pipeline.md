# 流水线调度

## 分组与仓库

MAAOrch 支持将可执行程序组织为分组，按组批量启动。

### 数据结构

**分组** (`groups[]`)：

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

**仓库条目** (`warehouse[]`)：

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
| `serial` | 逐个启动，每项之间有 `pre_delay` 秒间隔 |
| `parallel` | 同时启动所有程序 |

### 程序类型

`maa_type` 字段标识程序类型：

| 值 | 说明 |
|----|------|
| `maa` | MAA 图形界面程序 |
| `maa-cli` | maa-cli 命令行工具 |
| `general` | 通用可执行程序 |

## PipelineThread 调度线程

`PipelineThread`（`pipeline_thread.py`）继承 `QThread`，运行逻辑：

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

1. 查找仓库条目获取路径、参数、工作目录
2. 若绑定账号（`account_ref` 非空），调用 `ConfigService.inject_for_thread()` 注入配置
3. `subprocess.Popen()` 启动进程
4. 将进程对象加入 `_running` 列表
5. 发射 `program_started` 信号

### 暂停/恢复

- `pause()`: 设置 `pause_flag=True`，线程在主循环中检测并进入 200ms 休眠等待
- `resume()`: 清除 `pause_flag`，线程继续执行
- 暂停期间已经启动的子进程继续运行，不会终止
- 可通过 HTTP API `POST /api/pipeline/pause` 外部控制

### 停止

- `stop()`: 设置 `stop_flag=True`，对所有 `_running` 中的进程调用 `terminate()`

### 进程存活检测

在 `_sleep()` 等待循环中，每 100ms 检查一次 `_running` 列表中进程是否退出（`poll() is None`），已退出的进程自动移除。

## 定时任务

`ScheduleThread`（`schedule_thread.py`）支持两种模式：

### 每日定时

- `type: "daily"`，`time: "08:00"`
- 每天在指定时间触发一次
- 若当前时间已过目标，顺延到次日

### 每周定时

- `type: "weekly"`，`time: "08:00"`，`days_of_week: [0,3,6]`（周一=0）
- 仅在指定星期几触发
- 搜索未来 7 天内第一个匹配的触发时间

### 防重复

若上次触发在 120 秒内，跳过本次触发（防止 NTP 校时等导致重复）。

## 启动选项

每个账号支持以下启动行为控制：

| 选项 | 说明 |
|------|------|
| `start_minimized` | MAA 启动后最小化到托盘 |
| `start_directly` | 跳过唤醒阶段，直接进入任务队列 |
| `adb_fail_launch_emu` | ADB 连接失败时自动启动模拟器 |
| `adb_retry` | ADB 连接失败重试次数 |
| `sync_tasks` | 启动时将任务参数同步写入 gui.json |

## 启动后操作

`post_action` 字段控制 MAA 完成后自动执行的操作，位掩码组合：

| 操作 | 内部标签 |
|------|----------|
| 返回主屏 | `ReturnToMain` |
| 退出方舟 | `ExitArknights` |
| 关闭模拟器 | `CloseEmulator` |
| 退出 MAA | `ExitMAA` |
