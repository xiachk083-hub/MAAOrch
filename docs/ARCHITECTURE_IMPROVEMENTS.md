# 架构改进计划（2026-08-11 凌晨制定，全量版）

> 背景：2026-08-10 全天 + 目标机崩溃重启后连续 15+ 个问题（二次关闭/完成竞态/
> 孤儿 MAA/僵尸实例/双 tick/失联误报/UnboundLocalError/手动模拟器被回收），
> 绝大多数根因：**同一逻辑散落多处 + 跨对象状态竞态 + 不可观测 + 无进程自愈**。
> 本计划按优先级收敛，每步独立可回退，队列空闲时实施。

---

## P0：看门狗（进程级自愈）— 最优先

**问题**：UnboundLocalError 瘫痪 30 分钟（进程活着没人管）；进程崩溃/被杀（段
错误/OOM）→ manager 不探测不重启 → 系统静默死亡。

**方案**：manager 每 30s 探测 `19999/api/health`：
- 无响应且进程不在 → 自动 `start_project()`
- 连续 N 次无响应（进程在但卡死）→ 杀进程重启
- 日志记录每次拉起

**收益**：进程级故障自动恢复，与现有"开机自启"闭环。

---

## P1：统一事件总线（EventBus）

**问题**：debug.log/events.log/oplog.json 三源分散，各模块自己写；排障靠
grep 拼时间线；轮询检测（5s）慢且有竞态。

**方案**：
1. `services/event_bus.py`：`emit(type, payload)` 统一出口
   - 事件类型：completed/failed/launched/reclaimed/closed/boot_ok/boot_timeout/downgrade/error
   - 统一落盘（events.log 结构化 JSON）+ SSE 推送 + 触发动作
2. **LogWatcher**（日志事件流）：每实例一个监控线程，tail 读 asst.log 新行
   → 行 → 事件（AllTasksCompleted→completed / FightMissionFailed→downgrade /
   日志停滞→stuck）→ 即时回调（<0.2s，替代 5s 轮询，消除增量位置竞态）
3. MAA 无法主动推（Python asst 无外部回调）— 日志事件流是唯一事件源

**收益**：响应实时、竞态消除、排障一查到底。

---

## P1：EmulatorService（模拟器操作单点）

**问题**：MuMuManager/adb 操作散落 3+ 处（graceful/回收直接关/manager stop），
行为不一致 → 二次关闭/端口漂移/误判崩溃。

**方案**：`services/emu_service.py` 单点实现：
- `shutdown(idx, mode)`：graceful（connect→reboot→重发→兜底后等退出）/ direct
- `launch(idx)` / `wait_boot(idx)`（boot_completed 独立等待）
- `get_adb_port(idx)`（实时端口唯一来源）
- `is_alive(idx)`（bool 三态防御）
- 关闭冷却 `_recently_closed` 与 graceful 锁内置
- **info 查询缓存 2-3s**（降低 MuMuManager 压力）

---

## P1：状态快照端点（可观测性）

**问题**：今晚每次排查靠 grep 日志 + 推理内部状态（_inst_reserved 泄漏只能猜）。

**方案**：`GET /api/debug/state` 返回完整内部状态：
`_active / _procs / _inst_reserved / _active_emus / _active_emus_ts /
_pending / _system_started / _done_flags / _recently_closed / _adb_fail_count`
（脱敏 aid 显示前缀）

**收益**：问题定位从"猜"变"看"。

---

## P2：账号生命周期状态机

**问题**：状态分散 runner（_active/_procs/_inst_reserved/_done_flags）与 queue
（_active_emus/_active_emus_ts/_pending）→ 双 tick/完成竞态/孤儿 MAA。

**方案**：状态枚举单点管理（带锁）：
```
idle → launching(等boot) → running → completing → idle
                      ↘ failed → requeue
```
- 完成判定单一来源（LogWatcher 事件 + _done_flags 归 0）

---

## P2：静默异常清零

**问题**：171 处 `except: pass`；恢复链曾因静默 except 失效。

**方案**：关键路径（启动/关闭/注入/回收/失联判定）全部落日志（warn 级），
只允许无害防御静默。

---

## P2：queue.json 恢复可靠性

**问题**：今晚重启 5+ 次，队列恢复不完整（反复手动补）。

**方案**：`_restore` 保证重启后 pending 完整恢复（含 not_before 保留）；
恢复后校验（数量对账）。

---

## P3：其他

- **指标采集**：队列深度/完成率/失败率/回收次数（趋势可看，/api/health 扩展）
- **操作审计带来源 IP**（谁调的 API，oplog 加 ip 字段）
- **配置 schema 校验**：config.json 加载校验（损坏防护/版本字段）
- **smart_scheduler 国际服时区**（遗留）
- **测试补建**：队列调度/完成判定/优雅关闭/回收保护（回归拦截）

---

## 实施顺序

| 步骤 | 内容 | 风险 |
|---|---|---|
| 1 | **看门狗**（manager 探测拉起）| 低 |
| 2 | 状态快照端点 | 低 |
| 3 | LogWatcher 事件流（完成/失败/卡死迁移）| 中 |
| 4 | EmulatorService 收敛（launch_queue 先切换）| 中 |
| 5 | 状态机收敛 | 高（分步）|
| 6 | 静默异常清零 / queue 恢复 | 低 |
| 7 | 指标/审计/schema/时区 | 低 |
| 8 | 测试补建 | 低 |

> 原则：每步独立部署可回退；队列空闲时实施；改动后先小队列验证再全量。
