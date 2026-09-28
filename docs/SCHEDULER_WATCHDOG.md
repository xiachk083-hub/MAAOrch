# 调度看门狗（scheduler watchdog）—— gateway / cron / 发车审计的外部守护

> 建：2026-09-28 ｜ 实现：`tools/scheduler_watchdog.py` ｜ 计划任务：`MAAOrch-SchedulerWatchdog`（每 5 分钟，**注册待 lead 批准**）
> 任务卡：kanban `t_4d30949b`。所有阈值为设计默认，可在 `runtime/scheduler_watchdog.ini` 覆盖。

---

## 0. 一句话

日常调度完全依赖 Hermes gateway 常驻（cron 在 gateway 进程里跑）。**gateway 一挂 ⇒ 到点不发车 ⇒
全队停摆，而且没人会知道**。本看门狗活在 Hermes 之外（Windows 计划任务每 5 分钟一轮），只读探测三件事：
① gateway 进程/端口/心跳 ② cron 任务是否到点没触发（按 schedule 算容忍窗口）③ 最近发车审计时间；
异常先记录、再告警（默认 dry-run 落文件，`--alert-send` 才真发邮件）；可选 `--restart` 拉起 gateway，**默认关闭**。

---

## 1. 为什么不能用 Hermes cron 看护 Hermes

这是本设计的**第一性原理**，不是实现细节：

1. **自指闭环**：Hermes 的一切「定期动作」——cron 任务、巡检 job（52b88b0ffca0）、班次日报——
   都在 gateway 进程的 ticker 线程里触发。gateway 死 = 触发源死 = 监控一起死；
   「定期检查 gateway 的 cron job」会在 gateway 死的那一刻**同时消失**，且没有任何第三方会
   因为「没收到心跳」而报警（因为没人在外面听心跳）。
2. **真实先例**：2026-09-28 凌晨 04:00 的宿主计划重启（`DailyRestart0400`）就吞过一轮队列——
   重启窗口里 gateway 没起来，没有任何信号发出，直到人肉发现。
3. **Hermes 自己的设计印证**：`gateway_state.json.updated_at` 是 gateway 每个 tick 重写的**心跳**
   （`gateway/run.py` "First every tick: re-stamp updated_at … so it is a real heartbeat"）；
   `hermes cron status` 会打印「Gateway is running but the cron ticker looks STALLED」这类警告——
   但这些只在**有人去看**时才有用。缺的是「没人看时的自动报警」。
4. **为什么不是别的**：
   - 再建一条 Hermes cron 任务来查 gateway：除以零（第 1 条）。
   - 看板卡（kanban）：同样靠 gateway 的 cron 派发；且要人开看板。
   - AUTO-MAS 主机侧通知（100.79.173.69）：它跑在**远端主机**上，看不到本机 gateway 进程；
     而且 AUTO-MAS 自己也是被调度对象（本机 Hermes 给它发车），不能当裁判。
   - 结论：**本机 Windows 计划任务 + 直读文件 + SMTP 邮件**——完全不经 Hermes 进程、CLI、数据库，
     才是真正的第三方。计划任务由系统调度器触发，与 Hermes 无任何生命周期耦合。

---

## 2. 检测矩阵（全部只读；不调 hermes CLI、不写 Hermes 目录）

| # | 判据 | 数据源（本机文件） | 阈值 | 判定 |
|---|------|--------------------|------|------|
| ① | gateway 进程是否活着 | `<hermes>/gateway.pid` → PID → `OpenProcess+GetExitCodeProcess`（Windows） | 即时 | `gateway_down` critical |
| ① | gateway 状态面心跳 | `<hermes>/gateway_state.json`（`gateway_state` / `updated_at`） | updated_at > 30min | `gateway_state_stale` warn |
| ① | 端口（辅助） | `netstat -ano` 该 PID 的 LISTEN 口 + TCP 连通；默认 auto；`port_check=<n>` 可固定端口 | 固定端口不可达 | `gateway_port_dead` warn |
| ① | cron ticker 心跳 | `profiles/<p>/cron/ticker_heartbeat`（epoch 秒） | > 3×60+20 = **200s** | `ticker_stalled` critical |
| ① | ticker 连续失败 | `ticker_last_success` / `ticker_last_error` | success > 200s 或缺失 | `ticks_failing` critical/warn |
| ① | 陈旧代码 | `gateway_state.code_sha` vs `hermes-agent` git HEAD | 不相等 | `stale_code` warn（需重启） |
| ② | 任务超期未触发 | `profiles/<p>/cron/jobs.json` 的 `next_run_at` | 超期 > `min(120, max(15, 发车间隔+5))` 分钟 | `job_overdue` critical |
| ② | 最近一次执行时间 | `profiles/<p>/cron/executions.db` 的 `claimed_at`（台账为准，jobs.json `last_run_at` 兜底） | 只报告 | — |
| ② | 任务连续失败 | `jobs.json` 的 `failure_streak` | ≥ 2 | `job_failing` warn |
| ③ | 最近发车审计时间 | `runtime/mas_dispatch_log.jsonl` 尾部 `ts` + `mas_dispatch_state.json` 今日 `at` 最大值 | 只报告 | — |
| ③ | 发车窗口内无审计 | 同上，对照窗口（日常 05:00 / 晚班 19:30 起） | 窗口起 +45min 仍无任何今日发车 | `audit_stale` critical |
| ③ | 附带报告 | `mas_dispatch_alerts.json`（未消除）、`state.late` | 只报告 | — |

**容忍窗口怎么算（②）**：`容忍 = min(120min, max(15min, 发车间隔 + 5min))`。
- `*/3 5,19,20`（调度 job）：间隔 3min ⇒ 容忍 **15min**（基线，与 Hermes cron doctor 的 15min 同口径）。
- `every 30m`（巡检 job）：间隔 30min ⇒ 容忍 **35min**。
- 日级任务：容忍封顶 **120min**。
间隔由内置 5 段 cron 解析器在 8 天窗口上枚举触发分钟取最小间隔算出（只用于估算容忍窗口；
`next_run_at` 本身由 Hermes ticker 维护，是「是否触发过」的权威事实）。

**判定的时间语义**：`next_run_at` 在过去且超过容忍窗 ⇒ 该任务「本该触发却没触发」。
gateway 健康时 ticker 每跳（~60s）都会把 `next_run_at` 推进到未来，所以过期本身即异常。
`job_overdue` 是**兜底**判据（针对 ticker 假活、脚本挂死等）；最快的是 ①（进程死=即时、心跳死≤200s）。

**为什么直读 `executions.db` 而不调 `hermes cron runs`**：看门狗要能在 hermes CLI 坏掉、
PATH 变化、profile 切换异常时照常工作；`executions.db` 正是 `cron runs` 的数据源（同一事实），
直读少一层依赖（只读连接 `mode=ro`，不阻塞 gateway 写入）。

**为什么每 5 分钟一轮**：最细判据是心跳 200s、调度粒度 3min——5 分钟一轮可保证任一失效模式
在两个容忍窗内被发现；同时一轮只读开销 < 2s。

---

## 3. 异常处置

### 3.1 先记录，再告警

- 记录：`runtime/scheduler_watchdog.log`（每轮一行探测 + 每条找据）、
  `runtime/scheduler_watchdog_state.json`（告警生命周期状态）。
- 告警载荷（dry-run 也落）：`runtime/reports/scheduler_watchdog_alert_<ts>.json` +
  追加 `runtime/scheduler_watchdog_alerts.jsonl`。
- 真发：`--alert-send`（或 `MAAORCH_SCHEDWD_ALERT_SEND=1`）才发；通道 = `runtime/watchdog.ini`
  的 `[notify]`（**与主机看门狗 / 班次日报同源**，当前配置 = email）。**发送失败不吞**：
  错误写 log，文件通道兜底。
- 去重：同一条告警（kind+subject）30 分钟内不重复通知；条件消失发一条「已消除」。
- **开机静默窗**：系统开机 < 20 分钟内只记录不告警（宿主每日 04:00 / 19:00 计划重启，
  gateway 尚未拉起属预期，避免假警报）。判定仍会写进日志，便于复盘。

### 3.2 可选拉起（`--restart`，默认关闭）

- 触发条件：`gateway_down` / `ticker_stalled` / `stale_code` 任一出现。
- 命令：默认 `hermes --profile default gateway restart`（本机 `bin\hermes.exe` 存在则用绝对路径；
  可 `--restart-cmd` 或 ini `restart_cmd` 覆盖）。
- 拉起后**复验**：轮询 ≤90s（`restart_wait_s`），要求「gateway.pid 的进程活 + 目标 profile 心跳新鲜」；
  失败 ⇒ 追加 `restart_failed` critical 告警（含命令输出尾部）。
- **防循环**：20 分钟（`restart_min_interval_min`）内不重复拉起。
- 本卡**未改默认**：注册用的 launch ps1 里只有 `--once --alert-send`，不含 `--restart`。

### 3.3 退出码 / 机检末行

| 退出码 | 含义 |
|---|---|
| 0 | OK / 开机静默窗 |
| 4 | ALERT（有找据，载荷已落文件 / 已按开关发送） |
| 2 | `--check` 环境自检有硬伤 |
| 3 | `--selftest` 失败 |
| 1 | 未预期 |

末行 `WATCHDOG=OK|ALERT|BOOT_GRACE|DRILL（findings=N…）`，供计划任务与巡检 grep
（与 `SWITCH_GATE=` / `SELFTEST=` / `REGRESSION=` 同风格）。

---

## 4. 落地：Windows 计划任务（**受控变更：先批准，后执行**）

**现状（本卡交付时）：未注册。** 本卡只提供命令与回滚（约束原文：「注册计划任务是受控变更
（给出命令与回滚，先征求 lead 批准再执行）」）。批准后任选其一：

```bash
PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"

# ① 查看将要执行的命令与任务 XML（不改任何东西）
$PY312 tools/scheduler_watchdog.py --install-task

# ② 真正注册（唯一受控动作；等价于 schtasks /Create /TN MAAOrch-SchedulerWatchdog /XML <xml>）
$PY312 tools/scheduler_watchdog.py --install-task --apply

# ③ 立即试跑一轮 + 查看任务
schtasks /Run   /TN "MAAOrch-SchedulerWatchdog"
schtasks /Query /TN "MAAOrch-SchedulerWatchdog" /V /FO LIST

# 回滚（注销）
$PY312 tools/scheduler_watchdog.py --uninstall-task --apply
```

- 任务行为：`wscript.exe //nologo D:\1-2\MAAOrch\tools\run_hidden.vbs
  D:\1-2\MAAOrch\tools\scheduler_watchdog_launch.ps1` → 隐藏窗口跑
  `python tools\scheduler_watchdog.py --once --alert-send`；每 5 分钟（PT5M）、
  `MultipleInstancesPolicy=IgnoreNew`、`StartWhenAvailable=true`、10 分钟执行上限。
  与既有 `MAAOrch-RogueFleet` 同一套壳（run_hidden.vbs），无新增机制。
- launch ps1 只记录**非 0/4** 退出码到 `logs/scheduler_watchdog_task.log`（4=有告警，属正常）。
- 注册后建议核对：`schtasks /Query` 状态 Ready；下一轮 5 分钟内 `runtime/scheduler_watchdog.log`
  出现新行；首轮 `WATCHDOG=OK`。
- 想先只落文件不真发邮件：把 launch ps1 里 `--alert-send` 去掉即可（恢复同理）。

---

## 5. 自测与验收（2026-09-28 实测记录）

| 项 | 命令 | 结果 |
|---|------|------|
| 离线自检 29 项 | `PY312 tools/scheduler_watchdog.py --selftest` | **SELFTEST=OK（0 失败）**（cron 解析/容忍窗口/超期夹具/心跳夹具/状态机/演练/XML） |
| 环境自检 | `PY312 tools/scheduler_watchdog.py --check` | **CHECK=OK**（hermes 主目录 / mas cron 存储 / runtime 可写 / watchdog.ini / netstat+git+schtasks / Python312 / launch ps1 / gateway.pid 全 ok） |
| 实机只读探测 | `PY312 tools/scheduler_watchdog.py --once` | **WATCHDOG=OK**（pid=23232 存活、端口 127.0.0.1:56564 通、mas 心跳 20s；7 条任务全评；审计/告警/late 全报） |
| **假环境全链路演练 15 步** | `PY312 tools/tests/watchdog_drill.py` | **DRILL=PASS（15/15）**：假网关死+心跳过期+任务超期 → ①ALERT(rc4) ②载荷落（生产 runtime 未触碰）③ `--restart` 执行+复验通过+防循环 ④修好假环境 → OK + 「已消除」 |
| 告警通道真发 | `PY312 tools/scheduler_watchdog.py --test-notify` | **TEST_NOTIFY=OK**（邮件已发 xiachk083@gmail.com，标题标注 [测试]） |
| 演练（真环境强制找据） | `PY312 tools/scheduler_watchdog.py --drill all` | DRILL 判定、SAMPLE 载荷落 `runtime/reports/`（drill 状态与生产分离） |

**为什么演练用「假环境」而不是真停 gateway**：真停 gateway 会打断线上调度（巡检/直播看守/发车），
且属于受控动作。`tools/tests/watchdog_drill.py` 造一个假 Hermes 主目录（假 pid/心跳/jobs/executions.db）
+ 假 runtime，让看门狗在**真实代码路径**上跑完整流程（检测→告警→拉起→恢复），全程不碰真系统。
**未做（需批准/窗口）**：真停真启的实弹验证、注册计划任务本体（见 §4）。
原始实测输出存档：`docs/audits/2026-09-28_scheduler_watchdog.md`。

---

## 6. 运维手册（收到告警怎么办）

| 告警 | 先做什么 | 依据 |
|---|---|---|
| `gateway_down` / `ticker_stalled` | 手动重启：`hermes --profile default gateway restart`；看 `gateway-starts.log` 与 `logs/` | 拉起命令与 §3.2 相同 |
| `ticks_failing` | 看 `ticker_last_error` 内容（fd 耗尽/权限/陈旧代码）；重启释放 | Hermes `cron status` 有同款提示 |
| `stale_code` | gateway 跑的是旧 commit：`hermes update` 后必须重启 gateway | ticker 会空转、什么都不触发 |
| `job_overdue` | `hermes cron runs <job_id>` / `hermes cron doctor`；看该 job 的 `last_status` | ② 的原始判据 |
| `job_failing` | 同上；脚本类 job 看它自己的日志 | — |
| `audit_stale` | `tools/mas_dispatch.py --list` + `--reconcile`（dry-run）对账；确认没发车的原因 | ③ 的原始判据 |
| 假警报怀疑 | 看运行日志里的「开机静默窗」注记；重启窗口（04:00/19:00）内属预期 | §3.1 |

调阈值/受查 profile：`runtime/scheduler_watchdog.ini`（可选文件）：

```ini
[watchdog]
profiles = mas
grace_min = 15
renotify_min = 30
boot_grace_min = 20
restart_min_interval_min = 20
restart_wait_s = 90
restart_cmd =            ; 留空 = 自动探测 hermes.exe
port_check = auto       ; auto | off | <端口号>
```

---

## 7. 文件清单

| 文件 | 角色 |
|---|---|
| `tools/scheduler_watchdog.py` | 看门狗本体（探测/告警/可选拉起/自检/计划任务注册） |
| `tools/scheduler_watchdog_launch.ps1` | 计划任务隐藏启动器（run_hidden.vbs → ps1 → `--once --alert-send`） |
| `tools/tests/watchdog_drill.py` | 假环境全链路演练（15 步，`DRILL=PASS|FAIL`） |
| `runtime/scheduler_watchdog.log` | 每轮探测日志 |
| `runtime/scheduler_watchdog_state.json` | 告警生命周期 / 拉起记录 |
| `runtime/scheduler_watchdog_alerts.jsonl` | 告警事件流（追加） |
| `runtime/reports/scheduler_watchdog_alert_*.json` | 告警载荷（dry-run 也落） |
| `runtime/scheduler_watchdog_task.xml` | 注册时生成的任务 XML（`--install-task --apply` 才落） |

> 复跑（任意时刻）：`PY312 tools/scheduler_watchdog.py --selftest && PY312 tools/scheduler_watchdog.py --check && PY312 tools/tests/watchdog_drill.py`
