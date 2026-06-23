# 调度系统

## 概述

MAAOrch 的调度系统基于三层结构：

```
按钮 / API → 分发器 (dispatch_pool) → 队列 (LaunchQueue) → 执行器 (AccountRunner)
```

| 层 | 文件 | 职责 |
|----|------|------|
| 分发器 | `services/dispatch_pool.py` | 管理每个账号的任务模板 |
| 队列 | `services/launch_queue.py` | 优先级排队、模拟器独占、并行上限 |
| 执行器 | `services/runner.py` | 启动 MAA、注入配置、监控、清理 |

## 一键调度

仪表盘的调度按钮：

| 按钮 | `include_anni` | `only_anni` | 行为 |
|------|---------------|-------------|------|
| 日常 | false | false | 基础任务，逐账号检查 `smart_annihilation` |
| 含剿灭 | true | false | 基础任务 + 剿灭，逐账号检查 |
| 仅剿灭 | true | true | 只跑剿灭 |

点击调度按钮后：

1. 为每个可用账号生成任务列表
2. 检查账号的 `smart_annihilation` 字段：有值 → 包含 `Annihilation`；空 → 不含
3. 通过 `create_dispatch(tasks)` 创建调度模板，存入 `dispatch_pool`
4. 设置账号的 `dispatch_id`
5. 调用 `lq.enqueue(aid, "force", priority=0)` 入队

## 任务模板

`dispatch_pool` 是一个内存中的字典，`dispatch_id → task_list`：

```python
_dispatch_templates = {
    "abc123def456": ["StartUp", "Annihilation", "Fight", "Infrast", "Recruit", "Mall", "Award"],
    "def789ghi012": ["StartUp", "Fight", "Infrast", "Recruit", "Mall", "Award"],
}
```

每个账号有自己的 `dispatch_id`，指向其专属的任务列表。MAA 启动时从 `get_template(did)` 获取任务列表并注入配置。

### ⚠️ 已知问题：dispatch_pool 不持久化

dispatch_pool 只在内存中，服务器重启后清空。重启后 `get_template(dispatch_id)` 返回 None，runner 回退到 `["StartUp", "Award"]`（只跑启动和领奖）。

### 逐账号剿灭控制

`smart_annihilation` 字段控制该账号是否执行剿灭：

| `smart_annihilation` 值 | 行为 |
|------------------------|------|
| `""`（空） | 不跑剿灭 |
| `"Annihilation"` | 自动检测本周剿灭 |
| `"龙门外环@Annihilation"` | 指定剿灭关卡 |

配置方式：
- **表格模式**：剿灭列下拉框选择
- **CSV 导入**：剿灭列填写关卡 ID
- **API**：`POST /api/accounts/batch_save` 或 `POST /api/account/{idx}/edit`

runner 启动时检查 `smart_annihilation`，有值就添加 `Annihilation` 到任务列表。

## 启动队列（LaunchQueue）

### 优先级

| 来源 | 优先级 | 说明 |
|------|--------|------|
| 手动 / 强制调度 | 0（最高） | 用户点击或 API 调用 |
| 定时触发 | 1 | *Web UI 暂未实现* |
| 理智恢复 | 2 | *Web UI 暂未实现* |

> 定时触发和理智恢复是旧 Qt 桌面版的功能，Web UI 当前只能手动调度。

### 调度规则

每 5 秒执行一次 tick：

1. 取队首条目
2. 跳过已在运行中的账号
3. 跳过模拟器被占用的账号
4. 跳过未到 `not_before` 时间的条目
5. 跳过理智不足的条目（仅 sanity 来源）
6. 全部满足 → 启动

每次 tick 还会执行 `_clean_stale_emus()`（清理卡死的残留条目，150s 超时）。

### 限制

| 限制 | 配置 | 默认 | 说明 |
|------|------|------|------|
| 并行上限 | `parallel_max` | 1 | 同时最多运行 N 个账号 |
| 启动间隔 | 硬编码 | 20s | 两次启动之间最少间隔 |
| 模拟器独占 | - | - | 一个模拟器同时只能给一个账号用 |
| 实例上限 | `max(maa_instances, parallel_max)` | 9 | MAA 实例目录数量 |
| 内存过载 | 硬编码 | 1GB | 可用内存低于此值暂停全部启动 |

### 失败重试

```
exit_code 处理:
  0（成功）   → 正常完成，移除队列
  -3（超时）  → 释放实例，队尾重排
  -8（ADB）   → 重启模拟器，队尾重排
  其他错误    → 指数退避重试（5s → 10s → 20s → ... → 300s）
                 连续失败 ≥ 6 次 → 暂停该账号 30 分钟
```

### 错误自愈

| 检测 | 触发条件 | 行为 |
|------|---------|------|
| 启动后无任务 | 120s 内 asst.log 无任何任务记录 | 杀 MAA 释放实例 |
| 连续错误 | asst.log 出现 3 次 `[ERR]` | 杀 MAA + 重启模拟器 |
| ADB 失联 | ADB ping 失败 N 次 | 日志记录，不杀 MAA |
| ADB server 崩溃 | protocol fault / connection reset | kill-server + start-server |
| ADB 端口变化 | mumu-cli `adb_port` 与实际不符 | 每次启动重新检测 |

## 关卡管理

关卡存储在 `config.json.stage_library`：

```json
[
  {"id": "1-7", "name": "1-7", "count": 2},
  {"id": "CE-6", "name": "CE-6", "count": 1}
]
```

每个账号的 `stages` 数组记录已分配的关卡 ID：

```json
{
  "id": "V000001",
  "name": "V",
  "stages": ["1-7", "CE-6"]
}
```

### 关卡分配方式

| 方式 | 说明 |
|------|------|
| 表格模式 | 📊 表格 → 关卡列勾选 |
| CSV 导入 | 导出 → Excel 改 0/1 → 导入 |
| 账号编辑 | 详情页 → 关卡管理 |
| API | `POST /api/stages/apply` |

## 实例池

MAA 实例位于 `services/maa/instances/{1..N}/`，每个实例包含完整的 MAA 运行环境。

### 创建策略

```
ensure_maa_instances_async()
  → desired = parallel_max + 1
  → 从 maa/source/ 复制到 instances/{1..desired}/
  → resource/ → junction 指向 source（共享，~240MB）
  → externals/ → junction 指向 source（共享，~200MB）
```

### 生命周期

- 启动时自动检测并创建缺少的实例
- `parallel_max` 变化时自动扩展实例池
- 实例重建触发条件：`maa_version` 变化、用户点击"重建实例"

## 已知问题

| 问题 | 影响 | 状态 |
|------|------|------|
| dispatch_pool 不持久化 | 重启后任务列表丢失，回退到 `["StartUp","Award"]` | 待修复 |
| 两套调度路径 | `smart_global.enabled` 决定走 dispatch 还是硬编码，用户可能不知道 | 待合并 |
| 无定时调度 | 不能按时间自动触发 | 待实现 |
| 无理智调度 | 不能按理智回满自动触发 | 待实现 |
| dispatch_id 残留 | 非正常退出时 dispatch_id 残留在账号上 | 待修复 |
| schedule_mode 被忽略 | 配置有 roguelike/reclamation 模式但调度按钮没用 | 待修复 |
