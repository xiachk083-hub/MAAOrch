# 班次日报 + 告警渠道（tools/mas_shift_report.py）

**一句话**：每个班次（08:30 出日常班 / 21:30 出晚班）自动把「应跑/实跑/缺口 + 失败 Top + 补跑 + 水位 +
闸门 + 发车审计」落成一份报告文件 + 看板只读汇总卡；5 类关键事件告警**默认 dry-run**（落文件 + 更新
看板卡），经用户批准后一键开真发。

**定稿**：2026-09-28（t_af7211cb 接入；实现 = tools/mas_shift_report.py）。**定位**：只读采集 + 本机写，
绝不写主机/不改配置/不发外部通知（除批准后开 `--alert-send`）。

---

## 1. 产物与位置

| 产物 | 路径 | 说明 |
|---|---|---|
| 班次报告 | `runtime/reports/shift_report_<YYYYMMDD>_<shift>.md` | 9 节：结论 / 逐号应跑实跑缺口 / 失败类型 Top（四源聚合）/ 补跑台账 / 资源水位 / 空转实例 / 配置漂移+SWITCH_GATE / 发车审计 / 告警评估 / **到点提醒（周期事件 UPCOMING=，t_403cd83b）** / 数据源新鲜度 |
| 告警 payload（真实评估） | `runtime/reports/shift_alert_<YYYYMMDD>_<shift>.json` | schema `maaorch.shift_alert/1`；dry-run 时 `send=false` |
| 告警 payload（模拟样例） | `runtime/reports/shift_alert_SAMPLE.json` | `--alert-test` 专用；5 类事件各一条，永不发送 |
| 告警流水 | `runtime/shift_alerts.jsonl` | 每次评估一行（含 dry-run 轮） |
| 告警状态 | `runtime/shift_report_state.json` | 去重键 `班次日\|班次\|kind\|subject`、first/last、notified、cleared |
| 看板只读汇总卡 | Hermes 看板（`maa-parallel-benchmark-20260926`） | 每（班次日, 班次）一张；`idempotency_key = shift-report:<date>:<shift>`；status=done、无 assignee ⇒ 永不参与派工 |

## 2. 接入方式（已生效）

- **cron job**：`mas-shift-report`（id `8b1708397046`），调度 `30 8,21,23 * * *`，脚本
  `mas_shift_report_cron.py`（mas profile 的 `scripts/` 目录），no_agent、deliver=local；
  每次运行输出存 `~/AppData/Local/hermes/profiles/mas/cron/output/8b1708397046/`。
  - 三个时间点的由来：**08:30**=日常班活跃窗（05:00–08:30）收口；**21:30**=晚班快照（任务书点名）；
    **23:30**=晚班定稿（活跃窗 19:30–23:00 结束后 30 分钟）——晚班在 21:30 还过不了成熟度闸
    （班次起 +150min），缺口类告警要到 23:30 才有语义，故晚间跑两次（同一份报告文件后写覆盖前写，
    cron 输出目录留每一轮 stdout）。
- **wrapper 的作用**：Hermes cron 只认 profile 的 `scripts/` 目录；wrapper 用 `sys.executable`
  调 `tools/mas_shift_report.py`（**不带 `--shift`**，工具按时刻自动判班：<12:00=daily、否则 evening）。
- **手工重跑**：

```
$PY312 tools/mas_shift_report.py                          # 自动判班（顺便当演练）
$PY312 tools/mas_shift_report.py --shift daily --date 2026-09-28
$PY312 tools/mas_shift_report.py --no-card --no-alert     # 只要报告文件
```

- **阈值参数**：`--gap-n 3`（缺口 > N 触发 gap 告警，默认 3）、`--streak-days 2`（连续失败天数，默认 2）。
- 班次口径：daily = `[D 05:00, D 12:00)`、evening = `[D 19:30, D+1 05:00)`；「活跃窗」= 班次起 +210min；
  「成熟度闸」= 班次起 +150min（未到点不评估缺口/全忙/连续失败，防误报）。

## 3. 告警判据（5 类，只在关键事件触发）

| kind | 级别 | 触发 | 闸门 |
|---|---|---|---|
| `dispatch_failed` | critical | 发车失败：`runtime/mas_dispatch_alerts.json` active（排除 test 注入） | 无（终态，任何时刻都报） |
| `sidecar_abnormal` | critical | REST 不可达 / `/api/core/health` 非 ready / 侧车进程在**班次活跃窗内重启** | 无 |
| `gap_over_n` | critical | 应跑 − 实跑 > N（默认 3），逐号列出 | 成熟度闸 |
| `all_busy` | warn | 运行中实例全部在用 且仍有缺口（无空闲产能消化） | 成熟度闸 |
| `consecutive_failed` | warn | 连续 ≥2 天「有失败记录且当天未完成」的账号（重试自愈不算失败日） | 成熟度闸 |

**去重/消除语义**：同一 `班次日|班次|kind|subject` 只算一次 new；未成功通知前每轮继续标注
「新（待通知）」；条件消失发一条「已消除」。状态记 `runtime/shift_report_state.json`。

## 4. 告警渠道（重要：默认不发任何外部通知）

- **默认渠道 = file + 看板**：payload 落 `runtime/reports/shift_alert_<date>_<shift>.json` +
  追加 `runtime/shift_alerts.jsonl` + 更新看板卡的「告警:」行。**不产生任何外部通知。**
- **真发通道已接好（一键开关）**：`--alert-send`（或环境变量 `MAAORCH_SHIFT_ALERT_SEND=1`）→ 走
  `runtime/watchdog.ini` 的通知配置（与 AUTO-MAS 通知同源；`[notify] channel` ∈
  serverchan / telegram / dingtalk / email）。
  - 开 cron 真发：把 wrapper `mas_shift_report_cron.py` 里 `EXTRA = []` 改成
    `EXTRA = ["--alert-send"]`（**需用户批准**；改一行即回滚）。
  - 单次手工真发：`MAAORCH_SHIFT_ALERT_SEND=1 $PY312 tools/mas_shift_report.py`。
- **模拟告警（永不发送）**：`$PY312 tools/mas_shift_report.py --alert-test` → 打印 + 落 SAMPLE。

## 5. 自检与排障

- `--selftest`：离线 31 项（窗口/脱敏/分类/连续失败/JSON 提取/告警判据/成熟度闸/渲染/看板 DB/**event_cal 解析·fail-closed·报告接线 6 例**），退出码 0/3。
- `--check`：连通性（REST / SSH 采样 / 5 个子工具（含 event_calendar）/ 看板 DB / history/search），退出码 0/2。
- 退出码：0=报告已出（含局部 UNKNOWN）；2=核心源不可达（报告仍出，REST 不可达会触发 sidecar_abnormal）；3=自检失败。
- 某节 UNKNOWN = 对应源不可达：看报告末尾「附: 数据源与新鲜度」表定位。

## 6. 验证记录（2026-09-28）

- `--selftest` 25/25；`--check` CHECK=OK（REST ok / 子工具齐 / 看板 DB 定位到 maa-parallel-benchmark-20260926）。
- 真实跑（09:51:55，**由 cron 作业实际触发**）：报告 + 卡 `t_1a15894c` 更新；
  应跑 52 / 实跑 51 / 缺口 1；告警 0（缺口 1 ≤ 3；上一轮的 gap 事件正确转「已消除」）。
- 模拟告警：`runtime/reports/shift_alert_SAMPLE.json`（5 类事件，`test=true`、`send=false`）。
- 附带修复：`tools/maa_error_daily.py` 输出改**原子写**（tmp+os.replace）——班次日报与 30min 巡检
  可能同时触发它，直接覆盖会让并发读方吃到半截文件。
- **到点提醒接线（t_403cd83b，2026-09-28 10:28）**：`--selftest` 31/31（25 例 → +6 例）、`--check` CHECK=OK（5 子工具）；
  真实跑报告第 9 节落 `EVENT_CAL=/UPCOMING=/EVENT_ADVICE=` 三行（`runtime/reports/shift_report_20260928_daily.md`）；
  fail-closed 演练（快照损坏 ⇒ `UPCOMING=UNKNOWN:活动快照不可读…`；工具缺失 ⇒ `UPCOMING=UNKNOWN:缺 tools/event_calendar.py（工具面未提供）`）
  两种情况下**日报照常产出**（应跑=52/实跑=52）。证据：`docs/audits/2026-09-28_event_calendar_wiring.txt`。

## 7. 红线

- 只读采集 + 本机写（`runtime/` + 看板的一张汇总卡）；**不写主机 / 不改 AUTO-MAS 配置 / 不改队列**。
- 外部通知默认关闭；开启必须用户批准（先看 dry-run payload 样例）。
- 看板汇总卡 status=done、无 assignee —— 只读展示，别对它派工；重复运行只更新原卡（不刷屏）。
