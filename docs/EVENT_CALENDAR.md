# 周期性事件日历 · EVENT_CALENDAR（规则表 / 到点提醒 / 发车建议）

> 产物：`tools/event_calendar.py`（日历生成，只读）＋ `tools/event_calendar_xcheck.py`（交叉核对，只读）
> 数据：`runtime/reports/event_calendar.json|md`（schema `maaorch.event_calendar/1`，14 天滚动）
> 出处：kanban `t_5f2e36b2`（assignee mas，2026-09-28）。**只读**：不改 AUTO-MAS/MAA 配置、不发车、不动实例；
> 唯一写面 = 自己的日历产物（`--no-write` 可关）。
> 索引：`docs/SERVER_EVENTS.md`（活动快照怎么抓/怎么更新）、`docs/MAA_EVENT_FARMING_PLAYBOOK.md`（活动刷取判据）、
> `docs/MAS_SHIFT_PLAYBOOK.md`（班次窗口/让位区）、`docs/MAA_DEPOT_MAINTAIN.md`（资源关→材料对照）。

---

## 0. 一句话

「今天/未来 14 天，**固定周期**上会发生什么（日切、周切、资源关轮换、活动开关、商店截止、宿主重启），
什么时候该发车/补刷」——全部来自**本地周期规则 + 活动快照**，到点给提醒（`UPCOMING=`），只建议不执行。

已接线形态：

```
python tools/event_calendar.py --check     # 末三行：EVENT_CAL=… / UPCOMING=… / EVENT_ADVICE=…
```

---

## 1. 周期规则表（人读版；机器口径 = 工具内 `RULES`）

| 事件 | 周期/时刻（服务器本地） | 北京时间 | 影响什么 | 依据来源 | 置信 |
|---|---|---|---|---|---|
| 服务器日切（日常刷新） | 每日 04:00 | CN/TW 04:00；JP/KR 03:00；EN 19:00 | 日常任务/信用商店/每日免费寻访/月卡；AUTO-MAS 的「今天」= UTC+4（= 北京 04:00） | AUTO-MAS `app/task/MAA/AutoProxy.py` L324-327（LastProxyDate 日界）＋ `docs/MAA_EVENT_FARMING_PLAYBOOK.md` L120 | 高（CN；其余服 = 各服本地 04:00 通行口径，未逐服一手核验） |
| **周切（剿灭周重置 / 周任务）** | **每周一 04:00** | CN/TW 周一 04:00；JP/KR 03:00；EN 19:00 | 剿灭周额度重置、周任务刷新、周计划（Weekly）切周 | AUTO-MAS `AutoProxy.py` L82-98（`_should_run_annihilation`）+ L304-318（`AnnihilationStartWeekday` 默认 Monday，周标记 `%G-W%V`@UTC+4）；`docs/SCHEDULER.md` L18 | 高（代码＋本仓文档＋实测 2026-W39） |
| 剿灭作战周常窗口 | 周一 04:00 起，本周内任意时刻跑 1 次 | 同上 | 该号本周是否还需剿灭：`AnnihilationCompletedWeek` ≠ 当周 ⇒ 先剿灭再日常 | AUTO-MAS `AutoProxy.py` L938-964（记账宁重试不误记）；`docs/MAA_EVENT_FARMING_PLAYBOOK.md` §3.2/§3.3 | 高 |
| 资源关轮换（MAA 当日开放表） | 每日 04:00 按周表切换 | 同上 | 选关：MAA 只选「当日开放」的关；排了闭着的关 → 整条跳过（FightSkippedNoOpenStage） | MAA v6.18.0 `src/MaaWpfGui/Services/StageManager.cs` L677-695 + `Models/StageInfo.cs` L148-174；物品对照 `docs/MAA_DEPOT_MAINTAIN.md` L52 | 高（MAA 侧权威；与本仓在用的周计划实测一致，见 §2） |
| 「资源收集」全关卡全天开放（活动） | 活动期（逐期以快照为准；本档 2026-09-29 16:00 → 10-20 03:59） | 按公告（CN 同刻） | 期间全部资源关每日可刷（CE-6/AP-5/SK-5/PR…）——补材料/芯片黄金窗口 | `runtime/reports/server_events.json`（cn_official.upcoming_events）+ MAA `StageInfo.cs` L150-161（resourceCollection 在期 ⇒ 全开） | 高（本档时间来自官网公告快照） |
| 活动开/关（惯例时刻） | 开启通常 16:00；结束 03:59 | 按服换算（JP/KR −1h；EN +15h） | Stage 计划、药投放（临期药窗口）、活动关生命周期；**结束后不改 Stage = 理智作战静默跳过（SR 教训）** | `runtime/reports/server_events.json`（逐条起止）＋ `docs/MAA_EVENT_FARMING_PLAYBOOK.md` R1/§5.5 | 高（逐条来自官网；「16:00/03:59」是惯例不是规则） |
| 活动商店/里程碑/报酬领取截止 | 通常活动结束后 ~3 天 03:59（逐条以公告为准） | 从快照 note 抽取 **该服本地时间** → 工具换算 BJT（JP/KR −1h；EN +15h） | 代币/兑换、报酬领取；错过即损失 | `server_events.json` 的 note 字段自动抽取（`MM-DD HH:MM` 形式） | 中（自动抽取，需人工复核；时区口径见 `docs/SERVER_EVENTS.md` §2/§5） |
| 信用商店刷新 | 每日 04:00 | 同上 | MAA Mall 链（信用购物）当天可买项变换 | 社区口径（**待核验**）；MAA `CreditShop-*` 见 `docs/MAA_CAPABILITY_MAP.md` L86 | 低 |
| 凭证交易所刷新（绿票/黄票区） | 每月（初）；精确时刻（每月 1 日 04:00）**待核验** | — | 资质/高级凭证区库存与信物更新 | Terra Wiki `Certificate Store`（二手） | 低（跨月时人工确认） |
| 宿主计划重启窗口 | 每日 04:00 / 19:00（本机时间） | 本机同刻 | 跨窗口的长任务会被吞（孤儿化）；晚班缺口须在 04:00 前收工 | `tools/mas_plan_builder.py` L1136（计划任务 id=1074）＋ `docs/MAS_SHIFT_PLAYBOOK.md` §0/§7 | 高（本机口径） |

**红线**：本工具**只建议、不执行**——发车归 `tools/mas_dispatch.py` / run profile；改 Stage 归计划生成链（另一张卡）。
日历里的每个时间提醒都带 `依据`（source 字段），引用前先看依据是否仍有效（尤其快照类条目）。

---

## 2. 资源关周表（= MAA 当日开放判定）

| 关 | 产出 | 开放日（服务器本地） |
|---|---|---|
| CE-6 | 龙门币 | 周二/周四/周六/周日 |
| AP-5 | 采购凭证 | 周一/周四/周六/周日 |
| CA-5 | 技巧概要 | 周二/周三/周五/周日 |
| SK-5 | 碳素 | 周一/周三/周五/周六 |
| LS-6 | 作战记录 | 恒开放 |
| PR-A-1 | 职业芯片（A） | 周一/周四/周五/周日 |
| PR-B-1 | 职业芯片（B） | 周一/周二/周五/周六 |
| PR-C-1 | 职业芯片（C） | 周三/周四/周六/周日 |
| PR-D-1 | 职业芯片（D） | 周二/周三/周六/周日 |

- 口径 = MAA 上游 `StageManager.cs` L677-695（`AddPermanentStages`，v6.18.0）；`StageInfo.cs`「空 = 恒开放」。
- **与在用周计划实测一致**：`docs/MAS_SCRIPTS_CATALOG.md` L96-97 两张在用计划（逐日：周一 LS-6、周二 CE-6、
  周三 CA-5、周四 CE-6、周五 CA-5、周六/周日 CE-6）与上表逐日吻合 —— 互为佐证。
- 活动期「全关卡全天开放」时全开（全开日工具会额外标 `resource_open_all` 与起始时刻；
  部分覆盖日（如 09-29 16:00 起）会同时给出 `resource_open_base`= 当日前段按周表。
- 注意：MAA 版本若改周表，以 MAA 为准 → 本表在工具内 `RESOURCE_STAGES`，改版后同步。

---

## 3. 日历产物（14 天滚动）

`python tools/event_calendar.py` → `runtime/reports/event_calendar.{json,md}`：

- `per_server.<key>.days[]`：逐「游戏日」（服务器本地 04:00 日界）——`resource_open`（当日实际开放关，
  含活动全开）、`resource_open_base`（周表基表）、`resource_open_all[+_from_bjt]`、`entries[]`
  （`weekly_reset` / `event_start` / `event_end` / `deadline_from_note` / `host_restart`，各带 `ts_bjt` + `ts_local` + `source`）；
- `upcoming[]`：48h 窗口内提醒（供 `UPCOMING=`，JSON 里留 24 条）；
- `deadlines[]`：note 抽取的商店/奖励截止（含 14 天窗外，标 `in_window`）；
- `advice[]`：发车建议（rule：`annihilation_weekly` / `resource_rotation` / `resource_open_all` /
  `event_start` / `event_end` / `signin_window` / `event_shop_deadline` / `host_restart_window` / `stale_snapshot`，各带 `evidence`）；
- `rules[]`：§1 的机器口径；`warnings[]` / `state`：快照新鲜度（`OK` / `DEGRADED:…`）。

---

## 4. 到点提醒：`UPCOMING=` 接线

**格式**（`--check`，末三行为机器可解析）：

```
EVENT_CAL=OK（14 天 / 事件源 server_events / 快照 0.2 天前）
UPCOMING=国际服 周切：剿灭周额度重置 + 周任务刷新:2026-09-28 19:00（约 9.0 小时后）；… 等另有 5 项在窗内
EVENT_ADVICE=今日（周一）04:00 已周切：…
```

- 升序、最多 6 项（`--window-hours` 默认 48h；`MAX_UPCOMING`），尾部写 `；等另有 N 项在窗内`；
- 窗口内无事件 ⇒ `UPCOMING=无（窗口内无已登记事件）`；
- 数据不可用 ⇒ `UPCOMING=UNKNOWN:<原因>`（fail-closed，**不许当「无事件」用**）＋ rc=2；
- 退出码：0=OK/DEGRADED（可用）；2=UNKNOWN（拒绝）；3=自测失败。

**接线状态**（2026-09-28，卡 `t_403cd83b` 落地 ⇒ 三处**已接线**）：

| 消费者 | 状态 | 做法 |
|---|---|---|
| 班次日报 `tools/mas_shift_report.py` | **已接线** | `collect_event_calendar()`（采集段）= 一次 subprocess `python tools/event_calendar.py --check --window-hours 48 --days 14`（`stdin=DEVNULL`、`timeout=60`），三行 `EVENT_CAL=/UPCOMING=/EVENT_ADVICE=` **原样**进报告第 9 节「到点提醒（周期事件）」+ 附源表 `event_calendar` 行；失败/无输出 ⇒ `UPCOMING=UNKNOWN:<原因>`（工具缺失 ⇒ `UNKNOWN:缺 tools/event_calendar.py（工具面未提供）`、超时 ⇒ `UNKNOWN:超时（event_calendar --check >60s）`），**不阻塞日报**。实证：`runtime/reports/shift_report_20260928_daily.md` §9。 |
| 巡检 job `52b88b0ffca0`（mas-readonly-inspection） | **已接线** | `mas-observe` 新增只读工具 `event_calendar(days=14, window_hours=48)` → 内部 subprocess `tools/event_calendar.py --check`（`stdin=DEVNULL`、timeout 60s、无 `--json` 需要——`--check` 即机器可解析三行），返回 `{ok, event_cal, upcoming, event_advice, exit_code, elapsed_s}`；提示词第六步「现场四行」调 1 次、主表第 14 行写 `UPCOMING=<upcoming 原文>`（`UNKNOWN:` 开头原样写、**不许当「无事件」用**）；工具面未接线 ⇒ 固定写 `UNKNOWN:工具面未提供（mas-observe 未接线 event_calendar）`。 |
| MCP 工具面刷新（手续） | **已执行** | 改完 schema/代码后：`hermes -p mas config set mcp_servers.mas-observe.enabled false` → `true`（~1min 生效；config.yaml 内容哈希前后一致 = 净零改动，仅触发重连）。核验：`hermes -p mas mcp test mas-observe` ⇒ `✓ Tools discovered: 7`（含 `event_calendar`）。（依据 `docs/MCP_TOOL_ROADMAP.md` §1.1/§6、`docs/HERMES_MAS_HANDOVER.md` §12.1） |

证据：`docs/audits/2026-09-28_event_calendar_wiring.{txt,json}`（含 selftest 31/31、mcp test 工具表、巡检轮主表、fail-closed 演练）。

---

## 5. 发车建议（只建议）——示例判读

工具会按规则给建议（md §1 / JSON `advice[]`），典型：

- `annihilation_weekly`：周一 04:00 已周切 ⇒「未跑剿灭的号本周内跑 1 次」；周日临近周切 ⇒「最晚周日晚补跑」。
  发车窗口引用早班 05:00–05:33 / 晚班 19:30–20:03，避开让位区（`docs/MAS_SHIFT_PLAYBOOK.md`）。
- `resource_rotation` / `resource_open_all`：当日开放表 + 明天预告；全开放活动起日会写明 `HH:MM 起`（此前按周表）。
- `event_start` / `event_end`：16:00 开 / 03:59 止的惯例动作（改 Stage / 药投放 / 收活动关）；
  刷关型活动会提示先核 MAA 活动表是否补录（未补录要手输关卡代码）。
- `signin_window`：签到按日累计，临期提醒每天点一次（最后一天明确标注）。
- `event_shop_deadline`：note 抽取的商店/奖励截止（BJT），提醒兑换/领取。
- `host_restart_window`：24h 内的宿主重启窗口——别跨重启排长任务。
- `stale_snapshot`：快照 >3 天（提示）/ >7 天（DEGRADED 告警）时提醒重抓（`docs/SERVER_EVENTS.md` §8）。

---

## 6. 自测与交叉核对（都可重跑）

```
python tools/event_calendar.py --selftest          # 32 例：窗口/锚点/时区/全开/截止/d/DEGRADED/fail-closed
python tools/event_calendar_xcheck.py              # 24 例：日历 vs 快照逐条落表 + SERVER_EVENTS.md 锚点 + 结构
```

- `xcheck` = 结构性核对：快照里落在窗内的每条活动起/止必须在日历逐日表里（B 服按官服口径）；
  人工锚点（`SERVER_EVENTS.md` §0/§1.1/§1.2/§2/§5 的 8 个时间点 + 2 个 note 截止）逐一比对，
  事件从快照消失后自动 SKIP（不误报）。
- 快照刷新后：重跑两条即回归。首轮实跑证据存 `docs/audits/2026-09-28_event_calendar.*`。

---

## 7. 边界与已知局限

1. **快照保质期**：活动条目来自 `server_events.json` 快照（>3 天提示、>7 天 DEGRADED）；活动密集期先按
   `docs/SERVER_EVENTS.md` §8 重抓再排期。
2. **note 截止为「中」置信**：自动抽取 `到/至/截止 MM-DD HH:MM`；note 内时间按**该服本地时区**换算 BJT。
   个别 note 里的时间可能是礼包/礼包类节点（标题统一写「商店/奖励截止」，原文见 `source` 字段）。
3. **全开放部分覆盖日**（如 09-29）：`resource_open` 列全关 + `resource_open_all_from_bjt` 标 `16:00 起`，
   `resource_open_base` 记前段周表——排上午的关卡看 base。
4. **周表/日切是「MAA 侧口径」**：MAA 升级改表后要同步 `RESOURCE_STAGES`（工具内注释了依据行号）。
5. **台服 MAA 表滞后**（见 `SERVER_EVENTS.md` §4.3）：台服活动条目按快照如实给出，但 MAA 未必认。
6. **B 服无独立公告面**：B 服事件直接引用官服口径（warnings 里会写明）。
7. **宿主重启窗口是「本机计划口径」**（04:00/19:00，事件 id=1074）；机器改计划要同步。
8. 世界服/时区：EN 按官网口径 UTC-7（冬令时可能 UTC-8，工具按 `--servers` 各服 offset 换算，改季要复核）。

---

*本文由 mas（Hermes）按 kanban `t_5f2e36b2` 产出；只读工具链，未改任何 MAA/AUTO-MAS 配置、未发车。*
