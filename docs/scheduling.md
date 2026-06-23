# 调度系统

## 当前状态

```
调度方式：手动（Web UI 按钮 / API）+ 自动（定时 / 理智恢复）
任务队列：LaunchQueue（每 5s tick）
任务模板：dispatch_pool（持久化到文件，重启不丢失）
关卡管理：stage_library + per-account stages
审计日志：`/api/oplog` 每次调度操作有记录
```

## 三层架构

```
按钮 / API → 分发器 (dispatch_pool) → 队列 (LaunchQueue) → 执行器 (AccountRunner)
```

| 层 | 文件 | 职责 |
|----|------|------|
| 分发器 | `services/dispatch_pool.py` | 创建/管理每个账号的任务模板 |
| 队列 | `services/launch_queue.py` | 优先级排队、模拟器独占、并行上限、启动间隔 |
| 执行器 | `services/runner.py` | 启动 MAA、注入配置、监控、清理、错误自愈 |

## dispatch_pool（任务模板池）

`dispatch_pool.py` 是一个内存中的字典：

```python
_dispatch_templates: dict[str, list[str]] = {}
```

| 函数 | 说明 |
|------|------|
| `create_dispatch(task_list)` | 创建模板，返回 dispatch_id |
| `get_template(dispatch_id)` | 获取模板，不存在返回 None |
| `remove_dispatch(dispatch_id)` | 清理模板 |

**流程：**

```
smart_all / smart_selected
  → 为每个账号生成 task_list
  → create_dispatch(task_list) → 存入内存
  → account["dispatch_id"] = did
  → enqueue(aid)

runner._launch_for_instance
  → get_template(ac["dispatch_id"])
  → 存在 → 注入该任务列表
  → 不存在 → 回退到 ["StartUp", "Award"]
```

**持久化：** 每次 `create_dispatch` / `remove_dispatch` 自动写入 `services/dispatch_pool.json`，服务器重启后恢复。不再丢失任务列表。

## 一键调度（Web UI 仪表盘）

### 调度按钮

| 按钮 | 说明 | include_anni | only_anni |
|------|------|-------------|-----------|
| 一键调度 | 默认日常模式 | false | false |
| 日常 | 基础任务不含剿灭 | false | false |
| 含剿灭 | 基础任务+剿灭 | true | false |
| 仅剿灭 | 只跑剿灭 | true | true |

### 调度流程

```
smartAll() / smartSelected()
  → 判断模式 → include_anni / only_anni
  → _get_web_schedule_tasks() 生成基础任务列表
  → 遍历可用账号:
      ├─ 跳过无 ADB 且无模拟器索引的
      ├─ 跳过错停（suspended）的
      ├─ 跳过已在队列或运行中的
      └─ 逐账号检查 smart_annihilation:
           有值 → 追加 Annihilation 到任务列表
           空值 → 从任务列表移除 Annihilation（如果有）
  → create_dispatch(tasks) → account["dispatch_id"] = did
  → enqueue(aid, "force", priority=0)
```

### 逐账号剿灭控制

`smart_annihilation` 字段控制每个账号是否执行剿灭：

| 值 | 行为 |
|----|------|
| 空字符串 | 不跑剿灭 |
| `"Annihilation"` | 自动检测本周剿灭 |
| `"龙门外环@Annihilation"` | 指定剿灭关卡 |
| `"龙门市区@Annihilation"` | 指定剿灭关卡 |
| `"切尔诺伯格@Annihilation"` | 指定剿灭关卡 |

配置方式：表格模式下拉框、CSV 编辑、API `batch_save`/`edit`

**生效范围：** `smart_global.enabled` 不论开闭都生效——runner 启动时检查此字段。

## LaunchQueue（启动队列）

### 优先级

| 来源 | 优先级 | 说明 |
|------|--------|------|
| 手动 / 强制调度 | 0 | 用户点击或 API 调用 |
| 定时触发 | 1 | `services/scheduler.py` 按 `daily_batch_time` 自动触发 |
| 理智恢复 | 2 | `services/scheduler.py` 检测 stats.json 理智值 ≥ 阈值时自动入队 |

### tick 调度规则（每 5 秒）

```
_tick():
  ├─ _clean_stale_emus()       ← 兜底清理卡死的活跃条目
  ├─ 检查 runner._overloaded   ← 内存不足时暂停全部
  └─ 逐个处理 to_launch 列表:
      ├─ len(active_emus) >= parallel_max → 推回
      ├─ 模拟器已被占用 → 推回
      ├─ 距上次启动不足 20s → 推回
      └─ 全部满足 → 标记 active_emus → 启动
```

### 限制参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `parallel_max` | 1（范围 1-30） | 同时最多运行 N 个账号 |
| 启动间隔 | 20s（硬编码） | 两次启动之间最少间隔 |
| 模拟器独占 | 自动 | 一个模拟器同时只能给一个账号用 |
| 实例上限 | `max(maa_instances, parallel_max)` | 初始 9，随 parallel_max 自动扩展 |
| 内存过载保护 | < 1GB（硬编码） | 低于此值暂停全部 |

### 失败重试

```
exit_code 处理:
  0   → 正常完成 → 移除队列
  -2  → deadline 超时 → 释放实例
  -3  → 运行超时 → 队尾重排（优先级+1）
  -8  → ADB 失败 → 重启模拟器 → 队尾重排
  -9  → 卡死 → 队尾重排
  -999 → 线程崩溃 → 释放实例
  其他 → 指数退避:
          #1=5s  #2=10s  #3=20s  #4=40s  #5=80s  #6+=300s
          连续失败 ≥6 次 → 暂停该账号 30 分钟
```

### 错误自愈

| 检测 | 触发条件 | 行为 |
|------|---------|------|
| 启动后无任务 | 120s 内 asst.log 无任何任务记录 | 杀 MAA → 释放实例 |
| 连续错误 | asst.log 出现 3 次 `[ERR]` | 杀 MAA → 重启模拟器 |
| ADB 失联 | ADB ping 失败 | 日志记录，不杀 MAA |
| ADB server 崩溃 | protocol fault / connection reset | kill-server → start-server |
| ADB 端口变化 | mumu-cli adb_port 与缓存不符 | 每次启动重新检测 |

## 关卡管理

关卡库 `config.json.stage_library`：

```json
[
  {"id": "1-7", "name": "1-7", "count": 2},
  {"id": "CE-6", "name": "CE-6", "count": 1}
]
```

每个账号的 `stages` 数组记录分配的关卡 ID。分配方式：

| 方式 | 说明 |
|------|------|
| 📊 表格模式 | 勾选关卡列 |
| 📋 CSV 编辑 | Excel 改 0/1 → 导入 |
| 账号详情页 | 关卡管理标签 |
| API | `POST /api/stages/apply` |

## 实例池

`services/maa/instances/{1..N}/`，从 `services/maa/source/` 创建：

```
ensure_maa_instances_async()
  → desired = parallel_max + 1
  → 复制 MAA.exe + config/
  → resource/ → junction 指向 source（共享，~240MB）
  → externals/ → junction 指向 source（共享，~200MB）
```

创建时机：启动时、`parallel_max` 变化时、版本更新时、用户点"重建实例"。

`_get_free_instance()` 搜索上限为 `max(maa_instances, parallel_max)`。

## 自动调度（services/scheduler.py）

启动时自动运行的后台守护线程，每 60 秒检查一次：

### 定时调度

- 配置项 `daily_batch_time`（默认 `"08:00"`）
- 每天在指定时间 ±5 分钟内触发一次 `smart_all`
- 已调度过的日期不会重复触发
- 任务列表：基础任务 + 逐账号剿灭判断

### 理智恢复调度

- 读取每个账号的 `accounts/{id}/stats.json` 最近一条理智数据
- 当前理智 ≥ 阈值（`smart_global.threshold`，默认 80%）时自动入队
- 优先级 2（低于强制调度和定时触发）
- 跳过已暂停、无 ADB、已在队列或运行中的账号

## 调度审计

每次调度操作记录到 `_OPLOG`（内存，保留最近 100 条）：

| API 端点 | 记录内容 |
|----------|---------|
| `POST /api/action/smart_all` | "一键调度: N 个账号" |
| `POST /api/action/smart_selected` | "调度选中: N 个账号" |

通过 `GET /api/oplog` 查看审计日志。

## 已知问题与待办

| 问题 | 影响 | 优先级 | 状态 |
|------|------|--------|------|
| 无 | — | — | 全部已解决 |
