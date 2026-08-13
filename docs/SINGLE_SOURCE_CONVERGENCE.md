# 单点收敛（"皇帝架构"）— 总纲与实施计划

> 用户拍板（2026-08-14 凌晨）：「各个功能之间……应该更好的放在常用的/必要
> 的功能上，就像所有人都服务于皇帝一样」。立项：把零散的东西收集起来，
> 每个域只留一个"账房"，所有功能通过中心通信，不再各自为政。
> 蓝图源头：ARCHITECTURE_IMPROVEMENTS.md（2026-08-11）的 P0/P1 计划。

## 1. 目标架构

```
                ┌───────────── 皇帝（EventBus 事件中心）──────────────┐
                │  emit(type, payload) → 落盘 + SSE + 订阅回调         │
                └──────────────┬──────────────────────────────────────┘
                               │ 订阅
      ┌────────────┬───────────┼─────────────┬──────────────┐
      ▼            ▼           ▼             ▼              ▼
  调度台/统计   队列/runner   体力账房      材料/活动      运维健康
  （只订阅）   （订阅执行）  sanity_tracker depot 域     runtime_health
                               │
      ┌────────────────────────┼───────────────────────────┐
      ▼                        ▼                           ▼
 模拟器总管               MAA 实例池                   日志事件源
 emu_service 单点        instance_pool 单点           LogWatcher 单点
 （开关/端口/状态        （创建/重建/注入           （tail asst.log
  唯一出口）              唯一出口）                 唯一读者+解析者）
```

**铁律**：每个"账房"是它那个域的**唯一写者**；其余模块只读账房、只发事件、
只订阅事件。禁止绕账房直接操作（今晚清掉的"错误路径 ADB reboot"就是绕账房）。

## 2. 现状乱象 → 收敛动作（按实施顺序）

| # | 域 | 现状 | 收敛后 |
|---|---|---|---|
| 1 | 事件 | debug.log/events.log/oplog 三源，各模块自写自读 | event_bus.py 唯一出口：emit→落盘(events.log)+SSE+回调 |
| 2 | 日志事件 | LogWatcher 出事件，runner/sanity_tracker 各自再读 asst.log | LogWatcher 唯一读者+解析者，事件进 bus，其余订阅 |
| 3 | 模拟器 | 操作散落 graceful/回收直关/错误路径（已清 ADB 一处） | 全部经 emu_service 单点（emu_service_v2 状态机收口） |
| 4 | 体力 | sanity_tracker 与 runner 重复读日志 | sanity_tracker 唯一写者，runner 只订阅 sanity 事件 |
| 5 | 仓库 | depot_reader 协议层/depot_scheduler 编排/material_planner CLI 三路 | depot_scheduler 唯一编排入口，CLI 复用同一路径 |
| 6 | 材料关 | smart_scheduler 与 farm_dispatch 两张表手工同步 | farm_dispatch 唯一表，smart_scheduler 只保留工具函数 |
| 7 | 健康 | health_check（已删）与 runtime_health 两套 | runtime_health 唯一（✓ 已完成） |
| 8 | 根数据 | debug.log/events.log/oplog.json 散根目录 | 统一 logs/ 下（logger/api 硬编码收口） |

## 3. 实施步骤（每步独立可回退，随功能部署）

### Step 1：event_bus.py 骨架（新文件，零风险）
- `emit(type, payload)`：内存队列 → 落盘 events.log（结构化 JSONL）→ SSE
  广播 → 订阅回调（同步）
- `subscribe(type, fn)` / `unsubscribe`
- 事件类型枚举：launched/completed/failed/requeued/recovered/boot_ok/
  boot_timeout/stuck/downgrade/emu_closed/emu_reclaimed/sanity_updated 等
- 兼容现有 oplog.json 写入点（渐进迁移）

### Step 2：LogWatcher 接入 bus（现有事件源唯一化）
- LogWatcher 已每实例 tail 出事件（completed/sanity_before/medicine_used/
  subtask_error/stall_loop/maa_stopped…）→ 全部改 emit 进 bus
- runner 现有 `_handle_event` 改 `subscribe`（保留直接回调作为过渡）

### Step 3：runner/sanity_tracker 停止自读 asst.log
- runner._update_status/_parse_log 的任务名/体力解析改消费 bus 事件
- sanity_tracker 成为体力唯一写者（写 sanity_live.json）

### Step 4：模拟器操作收口
- grep 所有 MuMuManager/adb 直接调用点 → 逐处迁 emu_service 单点
- emu_service_v2 状态机正式接管回收决策

### Step 5：账房收口（体力/仓库/材料关/根数据）
- depot CLI 复用 depot_scheduler；材料关表合一；根目录日志收口 logs/

## 4. 验收标准

- `grep -rn "asst.log" services/` 只剩 log_watcher.py 一处读者
- `grep -rn "MuMuManager\|adb" services/` 只剩 emu_service 单点
- 每个事件类型单点 emit，排障只看 events.log 一查到底
- 新增功能自动走 bus/账房，不再出现第四套解析

## 5. 风险与护栏

- 迁移期双写（旧路径+新路径并存）→ 行为等价后切换（沿用 STRUCTURE.md 原则）
- 每步小步提交，可单独回退；不搞一次性大重构
- 与并行会话活跃文件（stage_probe/instance_pool）重叠的步骤放最后，先协调
