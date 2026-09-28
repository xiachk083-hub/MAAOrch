# mas_dispatch 调度器加固：一致性校验 / 冲突避免 / 补发边界 / 状态自愈

- **日期**：2026-09-27 21:5x（+08）｜**卡**：t_a53348ec（看板 `maa-parallel-benchmark-20260926`）
- **对象**：`tools/mas_dispatch.py`（Hermes cron 发车脚本，替代 AUTO-MAS 24 条定时）
- **集成基线**：同板并发卡 t_1b4ce956（失败分类/退避重试/告警台账/并发锁）**收工后**的版本
  （sha256 `73a04d5f…`，49,281 B / 1,066 行）；本卡在其上做**追加式外科补丁**。
- **交付版本**：sha256 `1c6f061c8d8dc0b6400c5270d9655bf09a87453ef2ee351292f3ca9b79d10331`（88,322 B / 1,828 行）
- **证据文件**：本文件；`runtime/mas_dispatch_{state,log,alerts}.json{l}`（运行时产物）

---

## 0. TL;DR

| 需求（卡面） | 落地 | 验证 |
|---|---|---|
| ① 一致性校验 SHIFT_TABLE ↔ AUTO-MAS | `--check-schedule`（名称/时间/开关/项/槽位对/跨槽重复） | 现场实跑 **OK**（24/24 队列，0 漂移）+ 合成差异 2 例自测 |
| ② 补发策略 + 与巡检的职责边界 | `late` 标记（state）+ `late_handoff` 台账告警；本脚本**不自动补**，只交巡检/人工；`--dismiss-late` 明确放弃 | 自测 `late_handoff_flow` / `dismiss_late_and_release` |
| ③ 冲突避免（同槽位/同资源双发） | 发车前「MAA 目录 + 脚本 sid + 实例号」同窗比对，重叠 => `refused=conflict_inflight` + 台账告警，**零上游 POST** | 自测 3 例（拒绝 / 过期放行 / 旧条目启发式） |
| ④ 幂等与自愈 + 审计字段 | state 损坏隔离重建（`state_quarantine`）；审计行补 `kind/final/reason/attempt/retries_used/terminal` | 自测 2 例（损坏自愈 / 形状归一） |
| ⑤ 自测（dry-run 语义） | `--selftest` **26/26**（新增 9 例专测上述）；所有新命令默认 dry-run | 见 §3 |

**没有真发车**：全部验证走离线自测 + 只读现场检查（`--check` / `--check-schedule` / `--busy` / MCP `--check` / dry-run 预演）。

---

## 1. 改动清单（对基线版本的追加）

### 1.1 新命令面

| 命令 | 语义 | 写操作 |
|---|---|---|
| `--check-schedule [--json]` | 只读一致性校验：缺失队列 / 表外定时队列 / 时间与开关漂移 / 队列为空 / 项指向不存在脚本 / 槽位对(日常N vs 晚班N)脚本集不一致 / 脚本跨槽重复；FAIL 退出码 2 | 无 |
| `--busy [--json]` | 只读：当前 busy 窗口（HOLD_MIN）+ 未关闭 late 标记 | 无 |
| `--release <队列> [--apply]` | 人工确认对方任务已结束后，清 busy 窗口（解同资源补发；兼容只有 `at` 的旧条目） | `--apply` 时写 state |
| `--dismiss-late <队列> [--apply] [--note 理由]` | 关闭 late 标记（决定不补）：清 `late` 标记 + 清 `late_handoff` 告警（`cleared_by=dismissed`）+ 审计 | `--apply` 时写 state/alerts |
| `--force-conflict` | 显式放行资源冲突拒绝；成功发车时在 state/输出记 `conflict_overridden`（供事后追账） | 随 `--queue/--reconcile/--shift` |

### 1.2 冲突闸门（`dispatch()` 锁内，dup-check 之后）

- 资源身份 = 该队列所有队列项指向脚本的 **`MAA 目录` + `sid` + `实例号`**（`resources_of()`；
  一次 `scripts/get` + 每队列一次 `item/get`，全部只读）。
- 对手范围 = state 中**近两日**（今天/昨天）已发车、且仍在 busy 窗口内的**其它**队列
  （`busy_until_of()`：显式 `busy_until`，旧条目退化为 `at + HOLD_MIN`）。
- 判定（`find_conflicts()`）：目录集 / 实例号集 / 脚本 sid 集**任一交集非空** => 冲突；
  旧条目缺 `resources` 时退化为**槽位启发式**（同槽位对 `日常定时N`↔`晚班定时N` 保守拒绝，标 `unknown_resources`）。
- 冲突 => `refused=conflict_inflight`：写审计行（`kind=dispatch_refused`）+ 台账告警
  （`DISPATCH_ALERT=<队列>:conflict_inflight`），**不打上游 POST**。
- 优化：仅当存在候选对手时才去解析本方资源 —— 无对手时零额外请求（也让离线传输桩测试免于被干扰）。
- `--force-conflict` 只跳过"拒绝"，仍照常检测并记账（`conflict_overridden=[对手队列…]`）。

### 1.3 补发边界（`--reconcile` 的 LATE 分支）

- 超追发窗口（`> MAAORCH_DISPATCH_GRACE`，默认 30 min）的队列：**本脚本不自动补发**。
- 记 `state["late"][date][queue] = {scheduled, first_seen, last_seen, seen_count}`（幂等：重复跑只
  刷 `last_seen/seen_count`，`first_seen` 不变）。
- 台账告警一行 `late_handoff`（按 (队列, 日期, 原因) 去重；`--reconcile` 不做写入时仅干跑）。
- 两条出路，均须**显式**：
  1. **补发**：巡检/人工 `--queue <队列> --apply`（成功即自动关标记 + 清告警 + 审计 `late_resolved`）；
  2. **放弃**：`--dismiss-late <队列> --apply --note 理由`（审计 `late_dismissed`，告警 `cleared_by=dismissed`）。
- 与巡检补跑（MCP `dispatch_due`/`dispatch_queue`）：MCP 层对超窗队列**只报 `late: true`、不发**；
  巡检的账号级补跑（`mas_rerun`）与队列级发车互不抢：**同一队列同一班次一天只发一次**
  （`duplicate_today` 单飞，跨进程锁内复检）——两侧即使同时动作也不会双发同一队列。

### 1.4 状态自愈（`load_state()`）

- 文件损坏 / 顶层非 dict：隔离到 `<state>.corrupt-<ts>`（保留最近 **5** 份），审计行
  `kind=state_quarantine`，stderr 一行 WARN，**本轮按空状态继续**（下一跳自动重建；不阻塞发车）。
- 形状归一：日期段内非 dict 条目剔除；`late`/`meta` 及未知顶层键保留（向前兼容）。

### 1.5 审计日志字段

每次尝试一行（`runtime/mas_dispatch_log.jsonl`）：在 t_1b4ce956 的 `reason/attempt/retries_used/terminal/deferred`
之上追加 `kind`（`dispatch_ok` / `dispatch_failed` / `deferred` / `dispatch_refused` / `state_quarantine` /
`late_handoff` / `late_resolved` / `late_dismissed` / `busy_release`）与 `final`（ok/failed/deferred），
便于按事件类型过滤对账。

---

## 2. 职责边界（硬规则）：谁在什么条件下动哪个队列

| 场景 | 谁 | 动作 | 互斥保证 |
|---|---|---|---|
| 到点、末超窗（≤GRACE） | Hermes cron `mas-shift-dispatch` → `--reconcile --apply` | 自动发车（失败退避重试/告警见 t_1b4ce956） | 单飞 `duplicate_today` + 跨进程锁 + 冲突闸门 |
| 超窗（>GRACE） | **无人自动发**：脚本只记 `late` 标记 + `late_handoff` 告警 | 巡检报告呈现 `DISPATCH_ALERT`；补发须人工/巡检显式 `--queue --apply` | 补发成功自动关标记+清告警；同一队列仍受单飞保护 |
| 决定不补 | 人工 | `--dismiss-late <队列> --apply --note …` | 标记关闭，告警按 `dismissed` 清除（留审计） |
| 同资源疑似冲突（闸门拒绝） | 人工/巡检核对现场 | 对方确已结束 => `--release <对方> --apply`；确需并行 => `--force-conflict`（记账） | 拒绝期间零上游 POST；放行必留 `conflict_overridden` |
| 队列间一致性漂移 | 巡检/人工 | `--check-schedule`（只读）对照 SHIFT_TABLE 与 AUTO-MAS 实际队列 | 不自动改上游；改动留与人/管理端 |
| 账号级补跑（失败账号重跑） | 巡检（`mas_rerun`，t_1b4ce956 之外的老链路） | 与队列发车**正交**：补的是账号/批次，不产生队列级重复发放 | 队列级单飞与账号级补跑作用于不同对象 |

**一句话**：*队列级"要不要发/补不补"归 `mas_dispatch`（单飞 + 冲突闸门 + late 标记）；账号级"重跑失败号"归巡检 `mas_rerun`；两边都不替对方做决定，跨边界的动作必须显式命令并留审计。*

---

## 3. 验证证据

### 3.1 离线自测（模块内，假传输；不碰网络/真 runtime）

```
$ python tools/mas_dispatch.py --selftest
SELFTEST=OK (26/26)         # 基线 17 例 + 本卡新增 9 例
```

新增 9 例：`state_corrupt_self_heal`、`state_shape_normalize`、`conflict_inflight_refuse`、
`conflict_expired_and_other_slot`、`conflict_legacy_slot_heuristic`、`check_schedule_diff_detect`、
`check_schedule_all_ok`、`late_handoff_flow`、`dismiss_late_and_release`。
其中 `conflict_inflight_refuse` 断言 **拒绝时 POST 计数为 0**；`late_handoff_flow` 断言
幂等（`first_seen` 不变、告警去重）与"补发成功 => 标记/告警双清"。

### 3.2 回归（并发卡 t_1b4ce956 的 pytest 套件，未改动其断言）

```
$ python -m pytest tests/test_mas_dispatch_retry.py -q
13 passed
```

### 3.3 现场只读检查（生产 AUTO-MAS，无写请求）

```
$ python tools/mas_dispatch.py --check
mas_dispatch --check: OK
  retry: retries=3 delays=60/180/300 budget=700s | alerts active=0 | lock=free

$ python tools/mas_dispatch.py --check-schedule
mas_dispatch --check-schedule: OK
  brief: {"scheduled": 24, "found": 24, "missing": [], "extra": []}
  ~ 非班次队列 9 条（临时-补跑-0923_0044-1, 临时-补跑-0927_0101-1, 临时-补跑-0927_0312-1…）：不参与调度，按残留物看

$ python tools/mas_dispatch.py --busy
busy 窗口（HOLD_MIN=150min）：12 条      # 晚班 12 队列（19:30–20:03 发车）均仍在窗内
late 标记：0 条

$ python tools/mas_dispatch_mcp.py --check
mas_dispatch_mcp --check: OK
  tools=list_schedule,dispatch_due,dispatch_queue,switch_gate
  reachable=True sent_today=24 due_not_sent=[] late=[] drift=[] alerts=0
```

dry-run 预演（不写、不发）：

```
$ python tools/mas_dispatch.py --queue 日常定时1          -> DRY  日常定时1 would_dispatch   (退出 0)
$ python tools/mas_dispatch.py --queue 打杂任务           -> unknown queue: 打杂任务        (退出 2)
$ python tools/mas_dispatch.py --release 晚班定时1        -> DRY release 晚班定时1（旧条目，按 at+HOLD_MIN 推算）——加 --apply 生效
$ python tools/mas_dispatch.py --dismiss-late 日常定时5   -> no open late marker for 日常定时5 (退出 2)
```

### 3.4 变更规模

| | 行数 | 字节 | sha256 |
|---|---|---|---|
| 集成基线（t_1b4ce956 终版） | 1,066 | 49,281 | `73a04d5f…` |
| 本卡交付 | 1,828 | 88,322 | `1c6f061c…` |

---

## 4. 回滚

1. **只回滚本卡加固**（回到 t_1b4ce956 的重试/告警/锁版本）：
   `cp tools/mas_dispatch.py.bak-hardening-20260927-2148 tools/mas_dispatch.py`
2. **整体回滚到接管前的原始短版**（撤销重试/告警/锁 + 本卡加固）：
   `cp tools/mas_dispatch.py.bak-retryalert-20260927-205715 tools/mas_dispatch.py`
3. **兼容性**：本卡新增的 state 字段（`resources` / `busy_until` / `conflict_overridden` / `late`）
   与审计/台账文件对旧代码**透明**（旧代码读 state 只取它认识的键；`late` 段不被旧逻辑消费）。
   新增 CLI 参数（`--check-schedule/--busy/--release/--dismiss-late/--note/--force-conflict`）
   在回滚后消失，无残留副作用。
4. 论文级细节：`runtime/mas_dispatch_log.jsonl` 中本卡引入的 `kind` 值若回滚则不产生新行，
   历史行不删（纯追加语义）。

---

## 5. 已知限制（诚实声明）

- 冲突判定是**同窗启发式**：以「近两日已发 + HOLD_MIN(150min) busy 窗」为界。若某队列任务实际跑
  超 150 分钟，其 busy 窗会提前过期（人工可用 `--release`/`--force-conflict` 校正；窗口时长可调
  `MAAORCH_DISPATCH_HOLD_MIN`）。
- 旧状态条目缺 `resources` 时只能按**槽位对**保守判断（宁拒勿双发）；首个成功发车后自动升级为新格式。
- `--check-schedule` 是**只读**工具，不会修复漂移；修复动作仍归管理端/人工（避免脚本"顺手改生产"）。
- 队列级单飞是"一天一发"；**不**覆盖账号级补跑（那是巡检 `mas_rerun` 的职能，见 §2）。
