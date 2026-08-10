# 架构改进计划（2026-08-11 凌晨制定）

> 背景：2026-08-10 全天 + 目标机崩溃重启后连续 10+ 个问题（二次关闭/完成竞态/
> 孤儿 MAA/僵尸实例/双 tick/失联误报/UnboundLocalError），绝大多数根因是
> **同一逻辑散落多处实现 + 跨对象状态竞态**。本计划按优先级收敛。

---

## P1：收敛模拟器操作层（EmulatorService）

**现状**：MuMuManager/adb 操作散落 3+ 处，行为不一致：
- `graceful_emu_shutdown`（launch_queue）— reboot 优雅关闭
- `_reclaim_idle_emus` 的直接 shutdown（绕过了 graceful 锁 → 二次关闭）
- manager `stop_project` 的关闭逻辑（独立实现）
- boot 等待/端口检测在 runner `_launch_job_body` 多处
- api_fastapi 的 `_emu_adb`/`_wait_emu_started`

**目标**：新建 `services/emu_service.py`，单点实现：
- `shutdown(idx, mode)`：graceful（connect→reboot→重发→兜底后等退出）/ direct（闲置无游戏）
- `launch(idx)` / `wait_boot(idx)`（boot_completed 独立等待）
- `get_adb_port(idx)`（MuMuManager 实时端口，全项目唯一来源）
- `is_alive(idx)`（bool 防御三态：True/False/None=无法确认）
- 关闭冷却 `_recently_closed` 与 graceful 锁内置

**收益**：二次关闭/端口漂移/误判崩溃类 bug 从根上消除（单点修）。

---

## P2：账号生命周期状态机

**现状**：状态分散在 runner（`_active/_procs/_inst_reserved/_done_flags`）
与 queue（`_active_emus/_active_emus_ts/_pending`），双 tick/完成竞态/孤儿
MAA 全是状态同步问题。

**目标**：账号状态枚举单点：
```
idle → launching(等boot) → running → completing(收尾) → idle
                      ↘ failed → requeue
```
- 状态迁移集中一个模块管理（带锁）
- 完成判定单一来源：AllTasksCompleted 检测 + `_done_flags` 归 0（已有雏形）

**收益**：双 tick 竞争、孤儿/僵尸 MAA、启动宽限等状态类 bug 结构性消除。

---

## P3：核心逻辑补测试

**现状**：测试已删（记忆：删除测试）。今晚全部靠手工+日志验证，成本极高。

**目标**（`tests/`）：
- 队列调度：启动间隔/并发上限/双 tick 防重入
- 完成判定：AllTasksCompleted → terminate → 归 0（竞态场景）
- 优雅关闭：reboot 失败 → 重发 → 兜底 → 等退出；冷却/锁
- 回收保护：active/排队/手动启动/关闭冷却/优雅进行中

**收益**：今晚这类连环问题回归测试直接拦住。

---

## P4：关键运行状态落盘

**现状**：`_inst_reserved`（实例预留）泄漏后只能重启清；`manual_emu_started`
（手动保护）重启丢；`_recently_closed`（关闭冷却）重启丢。

**目标**：`models/runtime_state.json`（gitignore）：
- 实例预留（aid→inst，启动失败自动清理）
- 手动启动时间戳
- 关闭冷却时间戳

**收益**：重启后状态可恢复，泄漏/丢失类问题消失。

---

## 实施节奏

| 步骤 | 内容 | 风险 |
|---|---|---|
| 1 | 新建 EmulatorService，launch_queue 先切换（回收/完成关闭）| 中（需实测）|
| 2 | runner boot/端口检测切换 | 中 |
| 3 | manager/api_fastapi 切换 | 低 |
| 4 | 状态机收敛 | 高（大改，需分步）|
| 5 | 测试补建 | 低 |
| 6 | runtime_state 落盘 | 低 |

> 原则：**每步独立部署可回退**；队列空闲时实施；改动后先跑小队列验证再全量。
