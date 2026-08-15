# MAAOrch 稳定化整体方案 — 状态机收敛（2026-08-14 晚，待用户拍板）

> 背景：2026-08-14 事故日（17:50 内存耗尽 → 18:00-22:30 连续 15 轮补丁）。
> 补丁清单见 git log；本文说明"为什么补丁止不住"与"正解是什么"。
> 正解基于仓库既有蓝图（docs/SINGLE_SOURCE_CONVERGENCE.md + EMULATOR_SERVICE.md
> + ARCHITECTURE_IMPROVEMENTS.md + AUTO_REQUEUE_LIFECYCLE.md），不另起炉灶。

## 一、15 个补丁的根源归类

所有问题（误回收/双启动/幽灵 MAA/失联误杀/快速失败）本质相同：

**模拟器/账号的"在用/闲置/启动中"没有单一事实源**——判定散落 6+ 处
（`_active_emus`、`_active`、`_procs`、`_inst_reserved`、`.pid/.meta`、
`recently_closed`、`_system_started`），互相矛盾 → 映射一丢就误判 → 补丁叠补丁。

这正是 EMULATOR_SERVICE.md §1.1 原样预言的："每个事故加一个保护/标记，
根因是模拟器没有状态机"。

## 二、正解：emu_service_v2 状态机（已实现 494 行，未启用）

v2 已内置今晚全部保护（核对确认）：

| 保护 | 旧路径 | v2 状态机 |
|---|---|---|
| 运行中不可回收 | 活 MAA 防线 + adb 连接保险（映射依赖） | **转移表拒绝 BUSY→回收**（结构保证） |
| 排队保留窗口/位置门 | 手写 | ✅ 内置 |
| 内存强制回收 | 手写 | ✅ 内置 |
| 冷却/手动保护/优雅锁 | 散落 | ✅ 内置 |
| 关闭/启动异步化 | 串行阻塞 | ✅ `_boot_bg`/`_close_bg` |
| adb 连接探测保险 | 今晚手写（launch_queue _busy_emus） | ⚠️ **未同步，Phase 1 补** |

**未启用的原因**：`release()` 从未被调用——账号完成后状态机停在 BUSY，
到不了 IDLE/READY，回收决策空转（`emu_v2_reclaim=False`）。

## 三、分阶段方案（每阶段独立可回退）

### Phase 1 — 补全状态机链路并启用回收（关键）
1. 接 release 调用链：`on_account_finished` → `v2.release(account)`（BUSY→IDLE）
2. adb 连接终极保险同步进 v2 reclaim_tick
3. 翻转 `emu_v2_reclaim=True`；旧 `_reclaim_idle_emus` 保留为回退
4. 验证：回收决策基于状态机（BUSY 结构上不可回收），映射丢失类 bug 根除

### Phase 2 — 状态查询收敛
- queue/runner 查 v2 `states`；删除散落判定（_active_emus/recently_closed 直查）
- 失联检测改订阅 v2 事件（emu_lost），删 MuMuManager 轮询拼状态

### Phase 3 — 账号生命周期状态机 + 失败分类
- ARCHITECTURE_IMPROVEMENTS P2：账号状态机（idle→launching→running→
  completing→failed/requeue），根治双触发/孤儿
- AUTO_REQUEUE_LIFECYCLE：环境问题（3 次挂起）vs 任务失败（自动重入）分类

### Phase 4 — 可观测性与测试
- /api/debug/state 状态快照（排障从"猜"变"看"）
- 失败率/回收次数指标 + 回归测试

## 四、2026-08-14 晚补丁清单（保留为旧路径加固 + 回退保险）

1. 回收排队保护时间窗（v1）/ 位置门（v2）/ 30s 节流 / psutil 枚举
2. 内存强制回收（overload_mem_gb 4GB / reclaim_force_mem_gb 2GB）
3. 并发后台关闭（Semaphore 3）+ 即时回收钩子
4. 失联豁免（closing_in_progress + spawn 60s 宽限）
5. 实例分配原子锁 + 防双启动（.meta 新鲜度）+ 拒绝时补回队列标记
6. 实例残留清理 .meta 归属保护
7. 幽灵 MAA 自动清理（health ghost_maa）+ zombie 兜底
8. boot_wait 90→180s
9. 注入写后验证（gui.new.json Address 读回校验）
10. 每实例独立 adb server（ANDROID_ADB_SERVER_PORT=5037+实例号）
11. adb 连接探测保险（回收前扫 adb.exe 命令行）
12. runtime_health stale 对齐位置门

## 五、决策点（用户拍板）

1. 路线认可：按"启用 v2 状态机"走？
2. Phase 1 实施时机（建议明天，需观察窗口）
3. parallel_max 最终目标 10（用户 2026-08-14 明确）——adb 争用已隔离，
   可在 Phase 1 验证后调整
