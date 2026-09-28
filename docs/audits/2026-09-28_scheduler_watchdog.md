# 调度看门狗交付审计（kanban t_4d30949b）

> 2026-09-28 10:2x ｜ 交付：`tools/scheduler_watchdog.py` + `tools/scheduler_watchdog_launch.ps1`
> + `tools/tests/watchdog_drill.py` + `docs/SCHEDULER_WATCHDOG.md`
> 约束遵守：**默认只告警不拉起**（launch ps1 只带 `--once --alert-send`，不含 `--restart`）；
> **计划任务未注册**（受控变更，命令与回滚见文档 §4，待 lead 批准）。

## 1. 实测证据（本机全量重跑，原始输出）

```
$ python tools/scheduler_watchdog.py --selftest
  ok  cron */3 5,19,20 最小间隔=3
  ok  cron 55 23 最小间隔=1440
  ok  interval 30m 间隔=30
  ok  坏 cron 回 None
  ok  容忍 */3 → 15min（基线）
  ok  容忍 30m → 35min
  ok  容忍日级 → 上限 120
  ok  超期夹具 → job_overdue
  ok  未超期夹具 → 无 job_overdue
  ok  禁用任务跳过
  ok  夹具：pid 死 → gateway_down
  ok  夹具：心跳旧 → ticker_stalled
  ok  夹具：活 pid + 新心跳 → 无 finding
  ok  pid_alive(本进程)=True
  ok  pid_alive(0)=False
  ok  审计夹具：last_ts 解析
  ok  窗口判据样例
  ok  状态机：首见 → emit 1
  ok  状态机：重复 → 不 emit
  ok  状态机：消失 → cleared 1
  ok  演练 all → 3 项找据 + DRILL 判定
  ok  payload schema
  ok  payload 标题含计数
  ok  开机静默窗 → BOOT_GRACE
  ok  脱敏丢凭据键
  ok  任务 XML 可解析
  ok  任务 XML 含 wscript+run_hidden
  ok  任务 XML 每 5 分钟触发
  ok  任务 XML 指向 launch ps1
SELFTEST=OK（0 项失败）
rc=0

$ python tools/scheduler_watchdog.py --check
scheduler_watchdog --check
  hermes home: C:\Users\xiach\AppData\Local\hermes
  profiles/mas/cron/jobs.json: ok
  profiles/mas/cron/executions.db: ok
  profiles/mas/cron/ticker_heartbeat: ok
  runtime: D:\1-2\MAAOrch\runtime
  runtime 可写: ok
  watchdog.ini（通知通道）: ok
  scheduler_watchdog.ini（可选覆盖）: absent
  netstat: ok
  git: ok
  schtasks: ok
  python: 3.12.10
  Python312（计划任务用）: ok
  launch ps1: ok
  gateway.pid: ok pid=23232
CHECK=OK
rc=0

$ python tools/scheduler_watchdog.py --once
2026-09-28 10:19:07  [探测] hermes=C:\Users\xiach\AppData\Local\hermes runtime=D:\1-2\MAAOrch\runtime 开机=39m 判定=OK
2026-09-28 10:19:07    gateway: pid=23232 存活=True 端口=127.0.0.1:56564:通 心跳=mas:12s
2026-09-28 10:19:07    cron[mas]: rogue-yield-check-1833 | next=None overdue=None | 最近执行=None(None) | tol=Nonemin
2026-09-28 10:19:07    cron[mas]: rogue-vs-prod-1952 | next=None overdue=None | 最近执行=None(None) | tol=Nonemin
2026-09-28 10:19:07    cron[mas]: rogue-resume-check-2245 | next=None overdue=None | 最近执行=None(None) | tol=Nonemin
2026-09-28 10:19:07    cron[mas]: dm-buyer-done-retry | next=None overdue=None | 最近执行=None(None) | tol=Nonemin
2026-09-28 10:19:07    cron[mas]: g28-detach-verify-2250 | next=None overdue=None | 最近执行=None(None) | tol=Nonemin
2026-09-28 10:19:07    cron[mas]: mas-readonly-inspection | next=2026-09-28T10:46:49.647513+08:00 overdue=-1663 | 最近执行=2026-09-28T10:00:52.863977+08:00(completed) | tol=35min
2026-09-28 10:19:07    cron[mas]: mas-shift-dispatch | next=2026-09-28T19:00:00+08:00 overdue=-31253 | 最近执行=2026-09-28T05:57:04.440107+08:00(completed) | tol=15min
2026-09-28 10:19:07    cron[mas]: mas-shift-report | next=2026-09-28T21:30:00+08:00 overdue=-40253 | 最近执行=2026-09-28T09:55:36.905109+08:00(completed) | tol=120min
2026-09-28 10:19:07    audit: 最近发车审计=2026-09-28 07:47:52（2h31m前）| 今日已发 12 | 未消除告警 0 | late 0
WATCHDOG=OK（findings=0）
rc=0

$ python tools/tests/watchdog_drill.py
  ok  ① rc=4（ALERT）
  ok  ① gateway_down
  ok  ① ticker_stalled
  ok  ① job_overdue
  ok  ① 最近执行时间取自台账
  ok  ① 禁用任务被跳过
  ok  ② 告警 payload 落盘
  ok  ② drill 状态文件生成
  ok  ② 生产 runtime 未被写入（前后快照一致）
  ok  ③ 拉起命令被执行（rc=0）
  ok  ③ 复验通过（活 pid + 心跳新）
  ok  ③ 拉起记录进状态文件
  ok  ③ 防循环（20min 内不重复拉起）
  ok  ④ 恢复后判定 OK（rc=0）
  ok  ④ 「已消除」通知
DRILL=PASS（15 步，失败 0）
rc=0
```

告警通道真发（已发生，收件人 = watchdog.ini 的 to_addr）：

```
$ python tools/scheduler_watchdog.py --test-notify
[通知] 已发送（邮件 -> xiachk083@gmail.com）: [测试] MAAOrch 调度看门狗
TEST_NOTIFY=OK
```

## 2. 设计要点（详见 docs/SCHEDULER_WATCHDOG.md）

- **自指闭环**：cron 在 gateway 进程里，任何 Hermes 内的监控会随 gateway 一起死 ⇒
  看门狗必须 out-of-band（Windows 计划任务，直读 gateway.pid / gateway_state.json /
  cron 心跳 / jobs.json / executions.db，不调 hermes CLI）。
- 判据①②③：gateway 进程+状态面+端口（辅助）+ ticker 心跳（>200s）｜
  每个 enabled 任务 `next_run_at` 超期（容忍 = min(120, max(15, 间隔+5)) min）+
  最近一次执行（executions.db claimed_at）｜ 发车窗口 +45min 无今日发车审计。
- 处置：日志+state → dry-run payload（reports/ + alerts.jsonl）→ `--alert-send` 真发（邮件）；
  去重 30min、消除通知、开机静默窗 20min；`--restart` 默认关（防循环 20min + 复验）。

## 3. 受控变更（待 lead 批准，不在本卡执行）

```bash
$PY312 tools/scheduler_watchdog.py --install-task                       # 只看命令与 XML
$PY312 tools/scheduler_watchdog.py --install-task --apply               # 注册 MAAOrch-SchedulerWatchdog（每 5 分钟）
schtasks /Run /TN "MAAOrch-SchedulerWatchdog"                           # 立即试跑
$PY312 tools/scheduler_watchdog.py --uninstall-task --apply             # 回滚（注销）
```

未做（需批准/窗口）：① 注册计划任务本体；② 真停/真拉实弹（本卡用假环境演练全覆盖）；
③ 提醒：注册后若邮件通道要收「首发」，可人工 `--test-notify` 再验一次。

## 4. 备注

- `docs/TOOLBOX.md` 为并发写热点（本卡与 activity_switch 卡同日在加节）：本卡段号已让位为
  **B19**（原 B18 被 activity_switch 占用）；后续加节请先 grep 段号。
- 本卡产物全部只在**本机**：不写主机、不改 AUTO-MAS、不改 Hermes 配置（写边界见 docstring）。
