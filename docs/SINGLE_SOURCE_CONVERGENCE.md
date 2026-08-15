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
- **2026-08-15 进度**：`_update_status` 改查 LogWatcher.cur_task()（单点解析）；
  `_parse_log` 改消费 LogWatcher.tail_tasks()；`_err_count`/`_fight_times`
  递增点从自读日志迁移到 subtask_error/fight_times 事件（收敛中补齐，防计数丢失）；
  清理死代码 `_log_handles`。剩余：_check_one 中 mtime 活性检测（无内容解析，
  属文件活性非重复解析，保留直读）

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

## 附录 A：模拟器操作收口清单（2026-08-14 盘点，逐处迁）

MuMuManager/adb 直接调用点（emu_service / task_constants 单点之外），按迁移顺序：

| 文件:行 | 性质 | 迁移动作 |
|---|---|---|
| runtime_health.py:17 | 硬编码 CLI 路径 | ✅ 已迁 find_mumu_cli（65968bb） |
| launch_queue.py:32-52 | _mumu_manager_cli 单点实现（CLI 查找） | 保留（收口目标本身） |
| launch_queue.py:903+ `_reclaim_idle_emus` | 回收 shutdown/info 执行 | **行为切换**：`emu_v2_reclaim` 开关翻转 → v2 `reclaim_tick` 接管（框架已在，旧逻辑已互斥跳过） |
| runner.py:301-330/673 | 端口探测 fallback | ✅ 已收口 `task_constants.detect_emu_adb`（单点，2026-08-10 部署） |
| config_injector.py:319/460-467 | 注入时找 CLI 写 EmulatorPath | ✅ 已收口 `find_mumu_cli`（2026-08-14，注释"单点：盘扫兜底已内建"） |
| api_fastapi.py:494/1071/1176/1195 | 健康/连接批量操作 | 部分已迁（diagnose_emulator/get_health_snapshot/emu_service 状态）；494 启动模拟器可迁 v2 `prewarm`；probe 段已有 v2 保护标记（708） |
| depot_scheduler.py:123-152/161-172 | 扫描起停模拟器 | 已用 `find_mumu_cli` 单点 + `launch_queue.graceful_emu_shutdown`；launch 段（_ensure_emu）可迁 v2 `prewarm`+`close` |
| stage_probe.py | 检测独占 ADB | 并行会话活跃文件，放最后协调 |

adb 直接调用（绕过 emu_service）：depot_reader/depot_scheduler/config_injector/
log_watcher/runner/launch_queue/stage_probe — 迁移原则：连接建立可保留
（MAA 自己连），管理动作（重启/强停/端口判定）一律收口 emu_service。

## 附录 B：账房收口清单

| 域 | 唯一写者 | 现状散落（2026-08-14 复核） |
|---|---|---|
| 体力 | sanity_tracker | ✅ 已收口：bus 订阅写快照（Step 3a，575ee0e）+ sanity_after 补快照（97f9ea2）；剩余=log_watcher 直调删除（白天验证后） |
| 事件 | event_bus | ✅ 骨架+runner+LogWatcher 已接（a9d9872/17a7b79） |
| 仓库扫描 | depot_scheduler | ✅ 单一实现 `depot_reader.py`（material_planner CLI 与 scheduler 同用一类，非自建） |
| 材料关表 | farm_dispatch | ✅ smart_scheduler 整文件删除（bc3e84a） |
| 根日志 | logger 单点 | ⏳ 待收口：logger.py:7 `_LOG_DIR`=项目根（debug.log/events.log/crash.log）、api_fastapi.py:18 oplog.json+553/1277 debug.log、runtime_health.py:16+backfill_stats.py:18 events.log 读路径 — **logger.py 为并行会话活跃文件，白天协调后统一改 logs/** |

## 附录 C：runner 自读 asst.log 影子功能盘点（2026-08-14 摸底，Step 3b 输入）

**关键发现：`_parse_log()` 是影子功能 — 永远返回 `([], None, None)`。**

- `ctx.logs` 全仓库无任何赋值（`app/service_context.py:41` 默认 None；main_web.pyw 实例化不传）
- `parse_log` 方法全仓库无定义 → 即使 ctx.logs 非 None 也 AttributeError
- 14 个 `_parse_log(aid)` 调用点（runner.py:1417/1430/1552/1592/1621/1775/1803/1825/1939/1977/1994/2002/2056/2431）全部拿到空值

| 调用点场景 | 空值下的实际行为 | 性质 |
|---|---|---|
| `_finish_completed` 体力判定 `_done_pct = (sanity.current/max)*100` | sanity=None → `_done_pct=0` → `_final=True` → **自动续跑分支永不触发**（除肉鸽） | 功能已死 |
| `_cleanup` 失败任务检测（exit==0 且 tasks 有"失败"→ -11） | tasks=[] → 永不判 -11 | 功能已死 |
| `_cleanup` "任务完成→不是真错误"豁免 | tasks=[] → 豁免永不生效 | 功能已死 |
| `_on_process_exit` 60s 内 rc=0 判启动失败（`not tasks`） | tasks=[] → 条件恒真，退化为"60s 内退出=失败" | 碰巧正确 |
| `_cleanup`/`emit_finished` 任务列表透传 | 前端/统计永远看到空任务列表 | 数据缺失 |
| `_cleanup` 的 sanity/drops 参数 | **签名里有但函数体从未使用** | 死参数 |

**真实数据源现状**（无需再读 asst.log）：
- 体力：`sanity_tracker.get(aid)`（sanity_live.json，bus 订阅写 — Step 3a 已收口）
- 任务状态：`_core_tasks`（MaaCore direct drive）+ log_watcher `task_start`/`maa_stopped` 事件
- 完成：AllTasksCompleted 事件（`_finish_completed` 触发点，正确）
- 掉落：log_watcher `stage_drops` 事件（`_record_event` 有记，未 emit — 待收口）

**Step 3b 改造方案（白天实施，需生产验证 — 行为改变不是等价重构）**：
1. `_parse_log` 改为查询真实数据源：sanity → `sanity_tracker.get(aid)`；tasks → `_core_tasks` 累积快照；drops → StageDrops 事件累积（先 emit 结构化 payload，同 Step 3a 双写过渡）
2. 顺带清 `_cleanup` 死参数 sanity/drops
3. 验收：`_finish_completed` 续跑判定复活（`_done_pct` 真值）、-11 失败检测复活、前端任务列表有数据
4. 风险护栏：改完先小批账号生产观察一轮（体力续跑行为变化最大），异常回退到空值路径（一行 `return [], None, None`）

