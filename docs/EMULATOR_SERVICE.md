# EmulatorService — 模拟器生命周期状态机设计

> 状态: **设计稿**（2026-08-12）— 待评审后实施
> 目标: 消除散落判定与补丁式修复，为多机协同预留节点抽象

---

## 1. 背景与问题

### 1.1 为什么做

2026-08-11 一天打了一串补丁（僵尸 MAA / 关闭链 / 空转检测 / 优雅杀 / A 型壳 / 崩溃弹窗 / 完成关游戏 / 降帧率）——每个事故加一个保护/标记。根因是**模拟器没有状态机**：

| 判定 | 位置 | 类型 |
|------|------|------|
| 模拟器在用吗 | `launch_queue._active_emus` / `runner._has_real_process` | 内存 dict |
| 系统启动吗 | `emu_service._system_started` | 内存 dict（重启丢失）|
| 刚关过吗 | `emu_service.recently_closed` | 内存 dict |
| 优雅关闭中吗 | `emu_service._locks` | 锁 |
| 空闲回收吗 | `_reclaim_idle_emus`（9 道保护）| 散落逻辑 |
| 崩溃恢复吗 | `runner._recover_account` | 散落逻辑 |

**"这个模拟器现在什么状态"——没有任何单一答案**。每个逻辑自己拼标记 → 互相矛盾 → 补丁叠补丁。

### 1.2 范围（用户明确）

- 这是**非常重要的项目**，后续范围会非常大
- **多机协同、多设备协同**（远程节点已有基础）——设计必须留节点抽象

### 1.3 不做什么（边界）

- 不改 MAA 本身（MAA 是黑盒工具）
- 不改队列的任务调度语义（优先级/节流/重试规则保留）
- 不做跨机自动迁移账号（账号绑模拟器不变）

---

## 2. 状态机定义

```
                  ┌─────────────┐
                  ▼             │
OFF ──prewarm──▶ PREWARMING ──▶ READY ──acquire──▶ BUSY ──release──▶ IDLE
 ▲                 │             │  ▲              │                   │
 │                 │ 失败        │  └──retry◀───────┤                   │
 │                 ▼             ▼     RECOVERING ◀─┘                   │
 │               (回 OFF)      CLOSING ◀────────────────────────────────┘
 └────────── 关闭完成/崩溃清理 ◀──┘          (回收/排位决策)
```

### 2.1 状态与进入/退出条件

| 状态 | 含义 | 进入条件 | 退出条件 |
|------|------|----------|----------|
| `OFF` | 无 VMM 进程 | 关闭完成 / 启动失败 | `prewarm()` / `acquire()` 冷启动 |
| `PREWARMING` | VBox 启动中（安卓未就绪）| 预热/冷启动发起 | 安卓 boot 完成 → `READY`；失败 → `OFF`（计次）|
| `READY` | 安卓就绪，无账号占用 | 预热完成 / 回收取消 | `acquire()` → `BUSY`；闲置超时 → `CLOSING` |
| `BUSY` | 账号占用（MAA 运行中）| 账号启动 | 任务完成 → `IDLE`；崩溃/失联 → `RECOVERING` |
| `IDLE` | 账号完成，游戏已关（空载）| `release()` | 排位决策：保留 → 等 `acquire`（复用）；回收 → `CLOSING` |
| `CLOSING` | 关闭中（优雅 → 兜底）| 回收/手动/降配 | 进程消失 → `OFF` |
| `RECOVERING` | 崩溃/失联恢复中（杀 MAA → 重启模拟器）| 崩溃检测 | 就绪 → `BUSY`（账号重试）；失败 N 次 → `OFF` + 账号挂起 |

### 2.2 关键规则

- **非法转移拒绝**：`BUSY` 不能被回收（回收只能从 `READY`/`IDLE` 进入 `CLOSING`）——状态转移表驱动，杜绝"MAA 还开着模拟器被回收"（2026-08-10 事故）
- **`IDLE` 是决策点**（不是自动回收）：账号完成 → 关游戏（已做）→ 空载；是否回收由"排位决策"决定（见 §5）
- **崩溃转移**：`BUSY` 检测到 VMM 进程消失/安卓失联 → `RECOVERING` → 杀残留 MAA → 重启模拟器 → 回 `BUSY`（账号自动重试）——现有 `_recover_account` 逻辑迁入

---

## 3. 单一数据源 `emu_state`

```python
emu_state: dict[str, EmuState]  # key = emu_idx
# EmuState:
#   node_id: str          # 本机标识（多机预留）
#   state: str            # OFF/PREWARMING/READY/BUSY/IDLE/CLOSING/RECOVERING
#   account_id: str|None  # 当前占用账号（BUSY/IDLE 时）
#   state_since: float    # 状态开始时间（闲置超时/回收计时用）
#   boot_ok: bool         # 上次 boot 是否成功（失败计次 → 挂起账号）
#   adb_port: int|None    # 实时端口（吸收 get_adb_port）
#   vaddr_errors: int     # VAddress 错误计数（30 分钟窗口）
#   crash_count: int      # 崩溃计数（趋势统计）
#   prewarm_ts: float|None
```

- **唯一事实来源**：所有"在不在用/什么状态"查 `emu_state`，**删除** `_active_emus`/`_system_started`/`recently_closed` 的散落判定
- **重启恢复**：MAAOrch 启动时扫描真实进程（VMMHeadless + .pid/.meta + 队列）重建 `emu_state`——吸收 2026-08-11 `_system_started` 重启丢失的教训（回收堆积事故）

---

## 4. EmulatorService API

```python
class EmulatorService:
    def prewarm(self, idx) -> None            # 预热（异步，READY 后待命）
    def acquire(self, account) -> bool        # 账号占用（READY→BUSY；OFF→冷启动）
    def release(self, account) -> None        # 完成（BUSY→IDLE，触发关游戏）
    def close(self, idx, reason) -> None      # 关闭（IDLE/READY→CLOSING→OFF）
    def reclaim_tick(self) -> None            # 周期回收决策（原 _reclaim_idle_emus 迁入）
    def diagnose(self, idx) -> dict           # 体检（吸收 diagnose_emulator）
    def handle_crash(self, idx) -> None       # 崩溃/失联 → RECOVERING
    def on_ready(self, idx) -> bool           # 内部：安卓就绪回调
    # 事件（订阅者: queue/runner）
    def subscribe(self, event, cb)            # emu_ready/emu_lost/emu_crashed
```

- 转移表驱动：`_TRANSITIONS = {state: {event: next_state, ...}}`——非法转移直接拒绝
- **queue 只订阅事件**（`emu_ready` → 账号可启动），**不再直接操作模拟器**

---

## 5. 空闲保留决策（IDLE 去向）

```
release()（完成+关游戏）
  → 账号在 pending 且排位靠前（30 分钟内会轮到）→ 保留 IDLE（下轮 acquire 复用）
  → 账号不在队列/排位靠后 → 回收（CLOSING）
  → 模拟器池数量 > 并行上限 + 预热缓冲 → 回收（数量约束优先）
```

- 替代今天"9 道保护 + 30 分钟排队保留"的散落逻辑（关键防护保留：非系统启动手动开的不回收——状态机里 `PREWARMING` 来源标记）
- **预热缓冲**：默认 1-2 台（配置化）——`READY` 状态的模拟器数量 = 缓冲上限

---

## 6. 事件流（解耦）

```
EmulatorService ──emu_ready(idx)──▶ queue: 该账号可启动（无冷启动等待）
              ──emu_lost(idx)───▶ queue: 账号重试
              ──emu_crashed(idx, type)──▶ runner: 杀残留 MAA → 恢复链
              ──emu_idle(idx, account)──▶ queue: 排位决策（保留/回收）
```

- 替代现在的：queue 轮询 `_has_real_process`/`check_processes` 拼状态
- **事件驱动**（现有 callback 模式扩展——不动框架）

---

## 7. 多机协同预留（Phase 4）— 多台实体电脑

> 用户明确（2026-08-12）: 多机 = **多台实体电脑**（物理 PC 协同），不是多开。

```
┌─ 主控（调度层：可独立一台，或某节点兼）─────────────┐
│  账号池（全局：node_id + emu_instance_index 定位）  │
│  跨机队列调度（任务 → 分配目标节点）                │
│  全局健康/统计视图                                 │
└──────────────┬───────────────────────────┬────────┘
               │ 下发任务 / 状态上报(SSE/HTTP)
     ┌─────────▼──────────┐      ┌─────────▼──────────┐
     │ 电脑 A: MAAOrch     │      │ 电脑 B: MAAOrch     │
     │  EmulatorService    │      │  EmulatorService    │
     │  （本机模拟器状态机）│      │  （本机模拟器状态机）│
     │  runner/queue       │      │  runner/queue       │
     └────────────────────┘      └────────────────────┘
```

- **node_id = 实体电脑标识**（不是模拟器）；`emu_instance_index` 在**节点内**有效——全局定位 = `node_id + emu_instance_index`
- 每台电脑跑完整 MAAOrch（EmulatorService + runner + queue）——**本机自治**（断网也能跑本机队列）
- 主控只做**跨机分配**（账号 → 节点）与**全局视图**——任务执行全在节点本地
- 账号配置带 `node_id`（该账号的模拟器在哪台电脑）
- 通信复用现有基础：manager HTTP（19998）+ 远程节点页（SSE）——Phase 4 详设
- Phase 1-3 单机实现时：`node_id` 字段 + API 签名留好（默认本机 node_id），不实现跨机逻辑

---

## 8. 迁移路径（渐进，不推倒）

| Phase | 内容 | 影响 |
|-------|------|------|
| **1** | 状态机骨架 + `emu_state` + 回收/开启迁入（删散落判定）| queue/emu_service 重构 |
| **2** | 预热（PREWARMING/READY）+ 空闲保留决策 | 队列吞吐提升 |
| **3** | 崩溃恢复/壳清理/弹窗/VAddress 监控入状态机 | 补丁收敛 |
| **4** | 多机节点抽象 + 调度 | 新能力 |

- 每 Phase 独立可部署（不破坏现有功能）
- Phase 1 完成后：`_reclaim_idle_emus` 的 9 道保护 → 状态机转移（行为等价，逻辑收敛）

---

## 9. 边缘案例与回退

| 场景 | 处理 |
|------|------|
| MAAOrch 重启 | 扫描真实进程重建 `emu_state`（不丢状态——吸收 `_system_started` 教训）|
| 预热失败 | `PREWARMING → OFF` + 计次；账号启动时兜底冷启动（现有逻辑）|
| 预热未使用 | `READY` 闲置超时 → 回收（防浪费）|
| 关闭中又被 acquire | 转移表拒绝（CLOSING 不可 acquire）→ 账号等 `emu_ready` |
| 回收时 MAA 还活着 | `BUSY` 不可回收（转移表）→ 不会重演"MAA 开着模拟器被关" |
| 模拟器物理不可用 | `PREWARMING`/`RECOVERING` 失败 N 次 → `OFF` + 账号挂起（现有语义）|
| 崩溃弹窗 | `handle_crash` 内清理 MuMuNxCrashReporter（吸收 health 补丁）|
| 壳残留（A 型）| 状态机启动扫描识别低内存残留 → 直接清理（吸收回收补丁）|
| 多机通信失败 | 节点本地状态机独立运行；调度层超时重试（Phase 4 详设）|
| VAddress 高错误率 | `vaddr_errors` 窗口计数 → 建议降配/标记节点不健康（数据驱动）|

---

## 9.1 补充设计（2026-08-12 评审后）

### 9.1.1 MAA 进程/实例池纳入状态机（关键补充）

模拟器状态机只管模拟器——**MAA 进程（僵尸源头）和实例池分配（`_inst_reserved`）仍是散落判定**。Phase 1 把实例池分配纳入：

```
BUSY 时: acquire_instance(account) → 实例池分配（原子，防并发竞态 — 吸收
       2026-08-10 实例分配竞态事故）
BUSY 释放: release_instance(account) → 归还 + 确保 MAA 进程死（优雅杀）
恢复链: RECOVERING 期间实例不归还（占用中）→ 吸收"运行中占位无缝恢复"
```

- `_inst_reserved` 从 runner 内存 dict → 状态机（`instance_state` 或并入 `emu_state` 的 account 关联）
- 僵尸 MAA 检测（health 补丁）→ 状态机启动扫描 + 周期校验内建

### 9.1.2 手动/外部启动（EXTERNAL 态）

- 启动扫描时发现：VMM 进程在但 `emu_state` 无记录 → **EXTERNAL**（用户在 MuMu 管理器开的）
- EXTERNAL 规则：**不回收、不预热、可被 acquire 接管**（账号启动时直接连——不用重启模拟器）
- 吸收"非系统启动永不回收"的语义（不再用内存 dict 标记——状态即事实）

### 9.1.3 队列暂停/维护态

- queue pause 时：模拟器**保留现状**（BUSY 的继续跑完、IDLE/READY 保留不回收）
- 暂停 > 30 分钟（长期暂停）→ IDLE/READY 回收（防长期空载占资源）
- 转移表加 `PAUSE` 修饰：回收决策在暂停时挂起

### 9.1.4 可观测性（审计）

- 每次状态转移写审计日志（oplog 模式）：`emu=26 OFF→PREWARMING reason=prewarm by=queue ts=...`
- 今天排查"谁在关模拟器"全靠日志拼——状态转移日志直接回答

### 9.1.5 预热与节流

- 预热解决"模拟器启动"节流；**MAA 连接节流保留**（20s——防 MAA 并发风暴）
- 预热就绪的账号启动 = 直接连 MAA（无模拟器等待）——队列 tick 的"启动间隔"判定改为"MAA 启动间隔"

### 9.1.6 多机细节（Phase 4 语义）

- **节点故障**：账号任务挂起（账号绑节点）+ 全局视图标红——**不做跨机 failover**（模拟器物理绑定，账号不能迁移——除非账号配置允许多节点候补，Phase 4 决策）
- **主控故障**：节点自治（本机队列继续跑本机账号）——主控恢复后状态同步（全量上报）
- 账号配置：`node_id`（主）+ 可选 `node_fallback`（候选节点——账号可移动时）

---
|------|------|
| MAAOrch 重启 | 扫描真实进程重建 `emu_state`（不丢状态——吸收 `_system_started` 教训）|
| 预热失败 | `PREWARMING → OFF` + 计次；账号启动时兜底冷启动（现有逻辑）|
| 预热未使用 | `READY` 闲置超时 → 回收（防浪费）|
| 关闭中又被 acquire | 转移表拒绝（CLOSING 不可 acquire）→ 账号等 `emu_ready` |
| 回收时 MAA 还活着 | `BUSY` 不可回收（转移表）→ 不会重演"MAA 开着模拟器被关" |
| 模拟器物理不可用 | `PREWARMING`/`RECOVERING` 失败 N 次 → `OFF` + 账号挂起（现有语义）|
| 崩溃弹窗 | `handle_crash` 内清理 MuMuNxCrashReporter（吸收 health 补丁）|
| 壳残留（A 型）| 状态机启动扫描识别低内存残留 → 直接清理（吸收回收补丁）|
| 多机通信失败 | 节点本地状态机独立运行；调度层超时重试（Phase 4 详设）|
| VAddress 高错误率 | `vaddr_errors` 窗口计数 → 建议降配/标记节点不健康（数据驱动）|

---

## 9.2 第二轮评审补充（2026-08-12）

### 9.2.1 任务层生命周期（模拟器状态机的配套）

模拟器状态机只管模拟器——**任务层**（账号的一次运行：启动→任务→完成/失败/空转/卡死）是另一层状态机，两者通过事件衔接：

```
任务状态（runner 内部，独立于 emu_state）:
  PENDING → LAUNCHING → RUNNING → DONE | FAILED | STALLED | RECOVERING
  STALLED（空转/卡死检测 — 吸收 stall_loop 补丁）
  RECOVERING（吸收 _recover_account / 自动重启 / _check_one 三套 → 统一一条）

衔接:
  LAUNCHING 需模拟器 READY（acquire）
  RUNNING 时模拟器 = BUSY
  DONE → 模拟器 release（IDLE）
  FAILED/STALLED → 模拟器 RECOVERING 或保留（按失败类型）
```

- 任务状态机**不进 EmulatorService**（职责分离），但**恢复路径统一**（9.2.2）
- 今天的任务层补丁（空转检测/完成误判/SubTaskError）在任务状态机里是正常转移——不再散落

### 9.2.2 恢复路径统一（Phase 1 必做）

今天三条恢复并存（`_recover_account` 失联链 / 自动重启重入队 / `_check_one` 轮询检测）——统一为：

```
失败检测（事件: 进程死/模拟器失联/空转/SubTaskError+停滞）
  → 恢复决策（单入口 recover_decide(account, cause)）:
      cause=完成误判      → 不恢复（_done_flags 语义）
      cause=空转/卡死     → 杀 MAA（优雅）→ 重入队（计次 → 跳关/挂起）
      cause=模拟器失联    → 模拟器 RECOVERING（重启模拟器）→ 账号重试
      cause=进程异常      → 自动重启（第 N/3 次 → 挂起）
  → 次数/频率统一管理（现有 _auto_restart_count/_recover_count/_stall_skip 归一）
```

### 9.2.3 状态机并发模型

- **单线程事件循环**（模拟器事件 + 回收 tick + 用户操作统一入队，顺序处理）——转移表在事件循环内执行，**天然无锁**
- 外部读取（health/前端查询）只读 `emu_state` 快照（复制引用）
- 长操作（VBox 启动/关闭）异步（后台线程 + 完成事件回投事件循环）——状态立即转移（PREWARMING 发出即回，启动完成事件再转移）
- 吸收教训：runner 双 tick 竞争（2026-08-11 tick 锁）——事件循环单线程从机制上消除

### 9.2.4 配置项清单（config.json 字段）

| 字段 | 默认 | 说明 |
|------|------|------|
| `prewarm_buffer` | 1 | 预热缓冲模拟器数（READY 保留上限）|
| `emu_idle_keep_min` | 30 | IDLE 保留窗口（排位决策用）|
| `emu_idle_reclaim_sec` | 60 | READY/EXTERNAL 闲置回收 |
| `recover_attempts` | 3 | 恢复尝试次数（超 → 挂起账号）|
| `vaddr_error_window_min` | 30 | VAddress 错误计数窗口 |
| `vaddr_error_threshold` | 50 | 窗口内错误超限 → 建议降配 |

### 9.2.5 测试策略

- **转移表单测**（纯逻辑）：非法转移拒绝 / 竞态事件顺序 / 各状态退出条件
- **状态恢复单测**：emu_state 重建（扫描进程 → 状态还原）
- **集成**：模拟器启停生命周期（现有 lab 测试方法论）
- 转移表 = 数据驱动 → 测试即表遍历

### 9.2.6 降级交互（BUSY 期间）

```
降级（关卡不可用）: BUSY 不转移 — 内部动作: 优雅关 MAA → 重注入 → 重入队
  （模拟器保持 BUSY/短暂 IDLE？— 决策: 降级期间模拟器保持 BUSY 占位，
   账号重入队后原地复用 — 吸收"处理问题保持运行中占位无缝恢复"）
```

- 降级失败（模拟器起不来）→ 走 9.2.2 恢复路径（BUSY → RECOVERING）
- 转移表加 `downgrade` 事件（BUSY 内处理，不退出 BUSY）

## 9.3 第三轮评审补充（2026-08-12）

### 9.3.1 CLOSING 超时语义

```
CLOSING 进入 → 关闭链（优雅 → shutdown → taskkill，现有 _body）
  → 90s 未退出（进程仍在）→ 强制 taskkill（兜底）→ 确认进程消失 → OFF
  → 进程消失但 MuMu 状态残留（headless_pid 登记）→ 状态机以【真实进程】
    为准（吸收 2026-08-11 关闭链等待判定教训 — 不信 MuMuManager info）
  → CLOSING 全程可重入（重复 close 幂等，见 9.3.3）
```

- 关闭失败（taskkill 都杀不掉——极端）→ 状态机强制 OFF + 审计警告（进程泄漏可观测）

### 9.3.2 前端 UI 接入

- 模拟器页/连接页直接渲染 `emu_state`：state → 文案/颜色映射（PREWARMING=启动中/READY=就绪/BUSY=刷关中/IDLE=空闲待回收/CLOSING=关闭中/RECOVERING=恢复中/EXTERNAL=手动）
- SSE 推送状态变化事件（现有 /api/sse 扩展）——前端增量更新（不做全量轮询——吸收连接页闪烁修复教训）
- `/api/emulators/diagnose` 改读状态机快照（health 只读）

### 9.3.3 API 幂等性

- **所有 API 幂等**：重复 acquire/release/close 无害（转移表拒绝非法转移 + 状态检查）
- release 重复（完成事件重复触发——`_done_flags` 教训）：第二次 release 时状态已非 BUSY → 转移表拒绝 → 静默忽略
- close 重复（手动+回收并发）：CLOSING 中再次 close → 忽略（已有 graceful 锁语义，转移表化）

### 9.3.4 配置校验

- 启动时校验所有账号的 `emu_instance_index`：指向的模拟器在 MuMu 中不存在（OFF 且无配置）→ 账号标记 `config_error`（挂起 + 设置页红标提示）
- acquire 时二次校验（配置可能运行时被改）——失败 → 挂起 + 明确错误原因（吸收"目标机器账号丢失"事故——不再静默回退默认）

### 9.3.5 规模化（57 台模拟器现实）

- **事件循环吞吐**：状态变化事件不密集（每台每分钟 ~1-2 个事件），单线程处理 60 台无压力——长操作全异步（VBox 启停后台线程）——无需多线程
- **长队列预热**：预热只预判**前 1-2 个**即将启动的账号（不是整条队列）——`prewarm_buffer` 默认 1——57 台轮转时预热窗口 = 即将轮到的 1-2 台
- **审计日志量**：状态转移日志走 oplog 模式（按天轮转，容量上限）——不无限增长

### 9.3.6 外部使用冲突（账号被其他设备玩）

- 任务失败且原因 = 连接/顶号（MAA 连接失败/重复登录）→ 判定 `external_use` → **挂起账号**（不自动重试——避免反复顶号风暴）+ 调度台提示"账号可能在其他设备登录"
- 与挂起语义统一（suspended=True + 原因字段 external_use）
- 用户确认后解除（现有手动解除模式）

## 10. 与现有代码的映射

| 现有 | 去向 |
|------|------|
| `launch_queue._reclaim_idle_emus` | → `EmulatorService.reclaim_tick` |
| `emu_service.graceful_shutdown/direct_shutdown/_body` | → `EmulatorService.close` 内部 |
| `emu_service._system_started/recently_closed/_locks` | → `emu_state` 字段 + 转移表 |
| `runner._recover_account` / 自动重启 / `_check_one` | → 9.2.2 统一恢复路径 |
| `runner._stall_skip` / `_err_windows` / 空转检测 | → 任务层状态机（9.2.1）|
| `runner._recover_account` | → `EmulatorService.handle_crash` + 恢复链 |
| `runner._kill_maa_graceful` | → 保留（MAA 收尾工具）|
| `runtime_health` 的 zombie/壳/弹窗检查 | → `EmulatorService` 内建（health 只读快照）|
| `runner` 启动模拟器逻辑 | → `EmulatorService.acquire`（冷启动/预热统一）|
