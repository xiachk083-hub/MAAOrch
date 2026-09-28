# MAS 标准班次操作剧本（端到端演练产物）

> 卡：`t_9d24454f`（看板 `maa-parallel-benchmark-20260926`，assignee `mas`）｜演练：**2026-09-27 23:07–23:55 CST，全部 dry-run/只读**
> 配套：`docs/MAS_SHIFT_GAPS.md`（自动化缺口清单，按优先级）｜索引：附录 B
> 演练性质：把「一次标准班次」的 6 步链路逐条真跑一遍（只读/dry-run），记录**输入、输出、判据、缺什么自动化**。
> 环境说明：演练当晚正处于真实事故期（晚班 1–8 队列停滞 + 系统代理间歇 502 + SSH 大文件拉取劣化），
> 因此本记录同时是**一次带噪环境的极限测试**——每一步的失败模式都是真实可复现的，见附录 A。

**一句话结论**：6 步链路全部有命令可跑、判据明确、失败均 fail-closed；但现状仍**必须人肉**把三段串起来——
① 三个观察行（CLIENT_STATE / POWER / IDENTITY）工具面未接线；② 补跑判定（C3 规则）没有单一工具实现、靠人对照文档；
③ 巡检报告只落本地、无人查看时无闭环。详见 `docs/MAS_SHIFT_GAPS.md` 的 P0/P1。

---

## 0. 标准日时间线（照这一张表过日子）

| 时刻 | 角色 | 动作 | 命令 / 工具 | 判据/口径 |
|---|---|---|---|---|
| 04:01 / 19:01 | AUTO-MAS 自身 | 惯性自重启（计划内） | — | **班次窗口内不许再重启侧车**（会吞在飞任务 ⇒ 孤儿化，今晚实例） |
| 04:50 起 | — | 早班运行窗开始（04:50–13:00） | — | RUNBOOK §10 |
| 04:55（建议，未接线） | 班前哨兵 | 早班 12 队列 preflight 汇总 | `mas_preflight --skip-screen`（接线方案 C） | 全 GO 才放心；见缺口 G4 |
| 05:00–05:33 | Hermes cron `mas-shift-dispatch`（`*/3 5,19,20 * * *`） | 早班 12 槽错峰发车（每 3 分钟对账） | `tools/mas_dispatch.py --reconcile --apply` | 每队列每班次当天只发 1 次；>30min 超窗只告警 |
| 05:40 / 19:20 起 | Hermes cron `mas-readonly-inspection`（每 30 分钟） | 全量体检 10 行表 | MCP 工具面（§5） | 固定格式；缺数据写 `UNKNOWN:<原因>`（fail-closed） |
| 05:00–05:40 / 19:20–20:20 | — | **守卫窗**：不补跑、不重启 | — | `MAS_RERUN_POLICY.md` §3 |
| 19:30–20:03 | Hermes cron | 晚班 12 槽错峰发车 | 同早班 | 上表 §2/§3 |
| 班次收工（连续 2 次 0 MAA） | 巡检 / 人工 | 日报 + **补跑判定（C3 规则）** | §6 | 守卫窗内不判不补；04:00 前是晚班缺口最后窗口 |
| 04:00 前 | 巡检 / 人工 | 晚班缺口补跑收尾 | §6 | 04:01 自重启会再吞一批 |

口径（唯一事实源）：班次表 = `tools/mas_dispatch.py` 的 `SHIFT_TABLE`（24 队列，05:00..05:33 / 19:30..20:03，+3min 错峰）；
AUTO-MAS 自带 24 条定时已全部 `Enabled=false`（保留作回滚备份；看到「定时没执行」别当故障修）。

---

## 1. 步骤①：班前自检 preflight ——「能不能发」

- **工具**：`tools/mas_preflight.py`（t_cd11ca49 产物，只读）
- **输入**：`--queue 晚班定时1` 或 `--script 明日方舟-b-2`；可选 `--skip-screen` / `--json` / `--timeout N`（单次远端命令超时，默认 240）
- **输出**：6 项检查逐条 + 末行 `PREFLIGHT=GO/NO-GO`；退出码 `0=GO / 1=NO-GO / 2=用法错误`；截图落 `runtime/preflight_shots/`
- **六项检查**：1 REST 可达 → 2 目标 MAA 槽位空闲 → 3 目标实例可 adb（VMM 进程→监听端口→`get-state`）→ 4 残留画面（截图+OCR）→ 5 注入闸门（复用 `mas_switch_gate` v2）→ 6 磁盘水位
- **判据**：任一 `FAIL` ⇒ NO-GO（WARN 不阻断）；**GO = 允许发车**、NO-GO = 拒发（人工 `--force` 才能绕）

**本次演练记录（7 次真实调用，23:09–23:29）**：

| # | 时刻 | 目标 | 结果 | 失败项与原因 |
|---|---|---|---|---|
| 1 | 23:09:59 | 晚班定时1 | NO-GO（5.5s） | ①REST `10054 连接被重置` |
| 2 | 23:10:58 | 晚班定时1 | 被 >300s 超时杀掉 | 无总时限；默认 `--timeout 240` 叠加劣化通道 |
| 3 | 23:17:13 | 晚班定时1 `--skip-screen` | NO-GO | ①REST `502 Bad Gateway` |
| 4 | 23:17:36 | 晚班定时1 全量 | NO-GO | ①REST 502 |
| 5 | 23:20:30 | 晚班定时1 全量 | NO-GO | ①②③⑤⑥ PASS；④「screencap 无有效 PNG」×2 实例 |
| 6 | 23:27:23 | 晚班定时1 | NO-GO | ②`MAA_1 在跑(pid 29776)`（他卡补跑已进入 ⇒ 防互杀，正确） |
| 7 | 23:28:55 | 晚班定时3 | NO-GO | ③idx40「VMM 在跑但 adb 连不上」+ ④idx58 截图失败 |

> 对照健康样本（t_cd11ca49，21:04）：`--queue 晚班定时1` → **GO**（WARN: idx11 停桌面 / C: 29.7GB）。
> ⇒ 通道正常时全流程 40–60s 可达 GO；本次 1–4 号的失败根因已定案 = **系统代理 127.0.0.1:7890 拦内网**（见 §5 附录 A-2），
> 属于本机工具侧问题（一行修复），不是 AUTO-MAS 的问题。

**缺什么自动化（人肉部分）**：
- G4：preflight **没有接进任何流程**——发车前没人会自动调它（调度/巡检/班前哨兵三处接入建议在 `docs/audits/2026-09-27_mas_preflight_wiring.md`，未实施）。
- G9：无总时限（`--timeout` 只是单命令粒度；全量最坏可叠加 >10 分钟）。建议加 `--deadline`。
- G10：截图单次失败即 NO-GO；建议单实例重试 1 次（本次 2/5 实例出现「无有效 PNG」）。

---

## 2. 步骤②：调度决策（dry-run）——「该发什么」

- **工具**：`tools/mas_dispatch.py`（t_a53348ec 加固版；**默认 dry-run**）
- **输入/常用只读命令**：
  - `--reconcile`：到点且今天没发过的队列会发什么（dry-run，零上游 POST）
  - `--check`：自检（白名单/闸门/连通/state/台账/锁）
  - `--check-schedule`：SHIFT_TABLE ↔ AUTO-MAS 队列/时间/槽位一致性
  - `--busy`：busy 窗口（HOLD_MIN=150min）+ 未关 late 标记
  - `--list`：24 条班次队列 + 今日发放 + 漂移 + 告警数
  - 任何命令可加 `--json`
- **输出（本次实测）**：
  - `--reconcile`（23:07）→ **空输出** =「无事可做」（24/24 今天都已发过，幂等跳过）；`--json` → `{"count": 0, "actions": [], "alerts": []…}`（23:10:49）
  - `--queue 晚班定时3`（dry-run）→ `DRY  晚班定时3 would_dispatch`（23:10）
  - `--check` → `OK；retries=3 delays=60/180/300；alerts active=0；lock=free`（23:08）
  - `--busy` → `busy 窗口 0 条；late 标记 0 条`（23:08）
  - `--list` → 24/24 `SENT`（含 taskId）+ `MAS 侧定时项: 0 | 漂移/未关: 无` + `alerts: 无`（23:47）
- **判据**：`due_not_sent` 非空 ⇒ 下一跳 cron（3 分钟内）自愈发车；`late_beyond_grace` 非空 ⇒ **只告警不自动补**（补发须显式 `--queue <名> --apply`，放弃走 `--dismiss-late`）；`dispatch_alerts` 非空必须在报告里写一行。

**缺什么自动化（人肉部分）**：
- 人肉只在**异常时**：`late_beyond_grace` / `conflict_inflight` / `late_handoff` 三种情形需要人工拍板（补发 or 放弃 or 释放 busy 窗口）——属设计（防双发），但目前没有任何提醒通道会把「该拍板了」送达人（告警只进台账 + 巡检报告行；巡检 deliver=local）。
- G8：判定与巡检提示词/补跑策略的口径要靠人保持三处一致（`MAS_RERUN_POLICY.md` §5 草案的单一判定源还没落地）。

---

## 3. 步骤③：发车（dry-run 演示 + 真发车路径）

- **真发车（现网唯一路径）**：Hermes cron `2600f6415f16`（`mas-shift-dispatch`，no-agent 脚本 `mas_shift_reconcile.py`）→ `mas_dispatch.py --reconcile --apply` → `POST /api/dispatch/start {"taskId":"<队列uid>","mode":"AutoProxy"}`。stdout 空 = 本轮无事；有输出 = `SENT/LATE/FAIL`。
- **dry-run 演示（本次）**：`--queue 晚班定时3` → `DRY 晚班定时3 would_dispatch`；MCP `dispatch_due(dry_run=True)`（23:46:46）→ `count: 0`（due∅）。
- **真发车前闸门（任一不过即拒、零上游写请求）**：队列名白名单 / REST 可达（失败 fail-closed）/ 当天单飞 / `MAAORCH_DISPATCH_NO_GO=1` 硬闸 / 返回体 `code==200`+taskId / 并发锁串行；加固后新增：**同资源冲突闸门**（同 MAA 目录/脚本/实例在 busy 窗内被占 ⇒ `conflict_inflight` 拒发+告警；`--force-conflict` 人工放行）与**补发边界**（超 30min 超窗只记 late）。
- **失败分类**：`rest_unreachable / http_error / bad_response / no_taskid / target_running / slot_occupied / target_not_found / queue_missing`；可重试类退避 60/180/300s（预算 700s，下一跳自愈）；终态失败写台账 + stdout `DISPATCH_ALERT=<队列:原因>`。
- **本次实测**：见 §2 输出行；今晚 demo 期另有一条真实发车链（他卡）：MCP `mas_rerun` 连遭 502（代理根因，G2）→ 改用「临时队列 + POST dispatch/start mode=AutoProxy」23:26 发车成功（taskId `a28c09c8…`，ledger 有 `source: t_7d630aa6` 标记）。

**缺什么自动化（人肉部分）**：
- G4：**发车前没人自动跑 preflight**（建议方案 A：`dispatch()` 内调用，每队列每天 ≤1 次，NO-GO 拒绝、`--force` 人工绕）。
- G2：dispatch 链路的 urllib 也吃系统代理（间歇 502）——重试/退避能自愈，但会白白消耗一跳。
- 红线（每次默读）：同队列当天只发一次；守卫窗内不补发；同目录两脚本同窗 = 互杀（kill 按目录路径）；肉鸽机队 `l-*` 与产线实例互不占用；**补跑 ≠ 队列级补发**，两个面互不替代。

---

## 4. 步骤④：监控（身份留痕 / 错误聚合 / 闸门 / 快照）

### 4.1 切号注入闸门 `mas_switch_gate.py --check`（只读）
- 输出：末行 `SWITCH_GATE=OK|VIOLATION:<槽位>|UNKNOWN:<槽位>`；口径=官服 `掩码+true`、非官 `""+false`，fail-closed。
- 本次（23:07:40）：`SWITCH_GATE=VIOLATION:MAA_10,MAA_12`（两处 `official-switch-not-true`）。已定因（t_06801d45）：**轮末 Temp 快照还原写回的旧态残留**、非当晚注错；对下次启动 = 掩码被丢、静默不切号 ⇒ 判一次性清理项。VIOLATION 只报不处置。

### 4.2 错误聚合 `tools/maa_error_daily.py --summary --no-write`（只读，四源）
- 输出一行：`ERR_SUMMARY=<n> 条，Top=<类型>`（三档：致命/告警/可忽略）。
- 本次：**失败**——23:32–23:37 第一次运行 >300s 未完成；23:42–23:50 后台重试 EXIT=2，输出 `ERR_SUMMARY=UNKNOWN:Command '[...ssh…]'`（ssh 超时；fail-closed 行为正确）。健康样本（t_d054ba36，22:00/22:36）：`ERR_SUMMARY=100 条，Top=客户端未就绪`。

### 4.3 身份留痕 `tools/mas_identity_trace.py`（只读）
- `report` → `IDENTITY_TRACE=OK|RED:n`（多号脚本每轮「账号切换 -->> 掩码」序列 vs 配置）；`selftest` 解析器自检；`watch` 盯 gui.log + 抓帧。
- 本次：`report` **两次均失败**（23:38/23:43，`subprocess.TimeoutExpired` 拉 MAA_6 `gui.log` >300s）；`selftest` 23:52 **OK**（含真实切换行样本 `账号切换 -->> 134****5276`）；`watch` 冒烟在 t_6f159f59 已有（21:20–21:22 抓 6 条真实事件）。
- 根因（本次实测）：劣化通道下全量拉日志太慢——`gui.log`=2.34MB、`asst.log`=41MB、`app.log`=12.6MB（23:5x 实测），单文件拉取 >200s 未完成。**工具本身有 stdin=DEVNULL 守护、不是挂死**，是通道 + 全量拉取策略问题（G5）。

### 4.4 主机快照（MCP `mas_status` / `mas_snapshot`）
- 本次（23:43:20）：`in-flight MAA=1`；`disk C:30G/238G ⚠、E:141G/1863G ⚠`；`failures=[]`、`never_ran=43`；队列 1–8 `done=0`（停滞），9–12 `done=4/4/4/3`；今日 `accounts=24 / ok=8 / retry=15 / fail=1（hbznn119-日）/ runs=70 / errors=43`。

### 4.5 原始进程探针（本次新增的照妖镜）
- `ssh <host> Get-CimInstance Win32_Process -Filter "Name='MAA.exe'"`（只读）→ 23:09 实测 3 个进程**全是肉鸽机队**（`E:\maa-alas\fz-maa\l\l-1/l-2/l-4`），产线 0 在飞 + `MuMuVMMHeadless` 9 个。探针脚本：`…/workspaces/t_9d24454f/evidence/probe_host_processes.py`。
- 用途：`mas_snapshot.maa_processes` 只是**全主机计数**（谁在飞不给）——**归属只认进程命令行**（技能铁律）。

**缺什么自动化（人肉部分）**：
- G1：CLIENT_STATE / POWER / IDENTITY 三个观察行在巡检里恒为 `UNKNOWN`（mas-observe 未接线）；今晚只能靠人补 §4.5 这种探针。
- G5：大文件全量拉取在劣化通道下不可用（无压缩/无增量/无字节上限）；identity report 与 error_daily 已两次实证失败。
- G2：闸门/聚合/留痕工具全部 urllib 一族 ⇒ 502 间歇（闸门的 `rest=down` 元数据缺失也是它）。
- G11：巡检（含这些判据）deliver=local，30 分钟一轮的结论**没有任何人收**——事故期全靠各 worker 自己碰。

---

## 5. 步骤⑤：报告（巡检 10 行格式）

- **执行者**：cron `52b88b0ffca0`（`mas-readonly-inspection`，每 30 分钟；只读 + 两个受闸门的写动作）；格式见提示词（备份于 t_562840d3 工件 `prompt_final.txt`）。
- **固定格式**：10 行主表（`MCP_API_TOOL_COUNT|ISOLATION / SCHEDULE / SWITCH_GATE / CLIENT_STATE / ERR_SUMMARY / DISPATCH_ALERT / IDENTITY / DISK / POWER / 结论`）+ ≤3 行证据/处置；每行都要有本轮工具返回直接支撑，写不出就 `UNKNOWN:<原因>`。
- **每行数据来源映射**（= 六步链路里已经练过的命令，一一对应）：

| 报告行 | 数据来源（MCP 工具 / CLI 等价物） | 本次演练实测值 |
|---|---|---|
| ISOLATION | 工具面自检（设计面 21 个 MCP 工具） | worker 面 12 个可见、无禁用项 ⇒ PASS |
| SCHEDULE | `list_schedule`（等价 `mas_dispatch --list/--check-schedule`） | 24/24 已发；due/late/drift=无 |
| SWITCH_GATE | `switch_gate`（等价 `mas_switch_gate --check`） | VIOLATION:MAA_10,MAA_12 |
| CLIENT_STATE | `mas_client_state`（**未接线**；等价 `client_state_check.py`） | UNKNOWN（G1） |
| ERR_SUMMARY | `error_daily`（等价 `maa_error_daily.py --summary`） | 本次 UNKNOWN:ssh 超时（fail-closed；健康样本 100 条 Top=客户端未就绪） |
| DISPATCH_ALERT | `list_schedule.dispatch_alerts` | 无 |
| IDENTITY | `mas_identity_trail`（**未接线**；等价 `mas_identity_trace.py report`） | UNKNOWN（G1；本次 report 另有通道超时） |
| DISK | `mas_snapshot`（等价 `mas_status` 主机段） | C:30G/238G ⚠、E:141G/1863G ⚠ |
| POWER | `mas_idle_emulators`（**未接线**；等价 `mas_idle_emulators.py`） | UNKNOWN（参考：在飞 MAA=1） |
| 结论 | 以上汇总 | 异常:晚班 1–8 停滞（never_ran=43）+ 通道问题已定因 |

- **本次演练按该格式实写的一份报告样本**（数据全部来自本记录内的只读命令）：

```
MCP_API_TOOL_COUNT=12 | ISOLATION=PASS
SCHEDULE=OK（24/24 已发；due=无、late=无、drift=无）
SWITCH_GATE=VIOLATION:MAA_10,MAA_12
CLIENT_STATE=UNKNOWN:工具面未提供（mas-observe 未接线）
ERR_SUMMARY=UNKNOWN:ssh 通道超时（本次；健康样本 22:00=100 条 Top=客户端未就绪）
DISPATCH_ALERT=无
IDENTITY=UNKNOWN:工具面未提供（mas-observe 未接线）
DISK=C:30G/238G ⚠、E:141G/1863G ⚠
POWER=UNKNOWN:工具面未提供（mas-observe 未接线）（参考：在飞 MAA=1）
结论=异常:晚班 1–8 队列停滞未完（43 号 never_ran、done=0）+ 通道问题（代理 502/大文件拉取超时，已定因）；闸门 2 处违规（只报）
证据/处置：
- 工具面=12（worker 面；cron 面=21）：缺 mas-observe×3（未接线）、mas_preflight（未接）
- 后端就绪=true（code=200，但有代理 502 间歇）｜今日=24 有记录/DONE 8/retry 15/失败 1（hbznn119-日）｜在飞 MAA=1
- 补位=无（due∅→未调用 dispatch_due）｜补跑=未执行（默认；Y3 缺口 43 号由 t_7d630aa6 串行批次处理中）
```

**缺什么自动化（人肉部分）**：
- G1/G6：三行恒 UNKNOWN；cron 工具面刷新滞后（error_daily 未加载，待 gateway 刷新 → t_6cfe7865）。
- G11：报告 deliver=local——**没有推给任何人**；事故期完全靠轮换的 worker 各自发现。
- G12：「谁改了/谁在跑」没有统一台账（今晚三路 worker 同时在主机动作；报告不体现占用者）。

---

## 6. 步骤⑥：补跑判定（C3 规则）——「该不该补」

规则全文：`docs/MAS_RERUN_POLICY.md`（运维③ t_bc75999f 产物）。**判定 = 三问**：
① 重跑会好吗（资源/环境类 ⇒ 不会 ⇒ 不补）→ ② 这个号已经成功了吗（本班次已有 DONE ⇒ 不补）→ ③ 超上限了吗（同号同班次日补跑派发 ≥2 ⇒ 不补，升级人工）。

| 类别 | 规则 | 动作 |
|---|---|---|
| 前置体检 | P0 守卫窗外 / P1 目标 MAA 目录空闲 / P2 VMM 在位 / P3 客户端就绪（`client_state_check`） | 任一不过：本轮不补（等下轮/先修环境） |
| 不该补 | N1 资源型（公招缺券等）/ N2 客户端未就绪全链崩（先修环境）/ N3 同班次已成功（收尾噪声）/ N4 超上限 | 不补；N1 连续 ≥3 天全败、N4 转人工 |
| 该补 | Y1 单链失败非资源 / Y2 桶内无 DONE + 环境类可重试 / Y3 被调度截断·孤儿化（只补未跑的号） | 每号每班次日 ≤2 次（自动 ≤1）；≤8/批；同目录组同队列；发车后闭环看该号**下一条 history 记录** |

- **预演路径（只读）**：MCP `mas_rerun(accounts=[…], dry_run=True)` → `{"would_start": […], "note": "预演：未触达远端"}`；确认真发才 `dry_run=False`（**本卡未执行**）。人工等价物：`python tools/auto_mas_rerun.py --accounts "…"`（默认预演）→ `--apply`。
- **本次判定样本（23:4x 实况）**：

| 对象 | 证据（本轮实测） | C3 判定 |
|---|---|---|
| hbznn119-日（today 唯一 fail） | `triage_error` 返回 `startup_failed`**但是目录级串号**（Px26660 行属同目录他号）；本号自身证据 = 18:08 `TaskChainError Recruit` + 18:09 `CreditShoppingTask`；今天 7 次补跑派发 0 成功 | **不补（N1，且已超 N4 上限）**——triage 的 kind 今天仍会误导（G4 缺口实证） |
| 晚班截断 6 号（古茶七心红/音乐厅生动的深海鱼/寻找勇者的大魔王/潮响/tbNick_2l3vf/him） | 19:54 侧车重启 banner + 无收尾行；snapshot `never_ran` 含全部 6 号；`mas_rerun` 预演 `would_start`=6/6 ✓ | **该补（Y3）**——由他卡串行批次执行中（t_7d630aa6，23:26 起） |
| 阿仄仄仄 | 21:04:15 `Success!` → 21:04:28 0 秒「MAA 进程超时」 | **不补（N3）**——收尾噪声 |
| 全局缺口 | `never_ran=43`、队列 1–8 `done=0`；其中 l-* 5 号（酸酸酸酸等）**不补**（肉鸽机队占用，防同实例冲突）；白天已 DONE 9 号不补 | Y3 批量（≤8/轮、按目录分组、先过 P3、04:00 前收工） |

- **上限计数来源**：`runtime/mas_ledger.jsonl`（`kind=rerun` 的 accounts）+ 临时队列名 `临时-补跑-MMDD_HHMM-*`。

**缺什么自动化（人肉部分）**：
- G4（核心）：**判定没有落点**——`triage_error`/`mas_rerun` 都还没有 `rerun_verdict/rerun_rule/rerun_evidence` 字段（草案见 `MAS_RERUN_TOOLCHAIN_DRAFT.md`，未实施）；巡检提示词也还没消费 `RERUN_VERDICT`。今晚的判定全部由人对照文档完成。
- G7：**缺口类（Y3）没有检测入口**——`mas_watch` 自动补跑只看 `failures`，截断号只出现在 `never_ran` ⇒ 今晚 43 号缺口靠人肉发现 + 他卡手工串行。
- G8：P1/P2/P3 体检（目录忙/VMM/客户端）目前是补跑方各自现查，没有统一体检函数。
- 红线：守卫窗内不补；补跑队列与产线**不同目录**才算并行；同一时刻只跑 1 条临时补跑队列；残留临时队列 <24h 必须清（今晚存量 9 条）。

---

## 7. 红线（每次动手前默读）

1. **只读/dry-run 优先**；所有写动作（发车/补跑/队列增删/配置）都必须显式、可回滚、留痕。
2. 班次窗口内**不重启侧车**（05:00–08:00 / 19:30–23:00）——孤儿化样板事故就在今晚（19:54 吞掉晚班 1–8）。
3. 同 MAA 目录**同一时刻只允许一个任务**（互杀按目录杀）。
4. 守卫窗（05:00–05:40 / 19:20–20:20）不发车、不补跑、不动配置。
5. 补跑 ≠ 队列级补发（两个面互不替代、互不抢；队列级只归 mas_dispatch、账号级只归 mas_rerun）。
6. 肉鸽机队 `l-*`、用户手动在用的实例：**本轮不许碰**；与产线实例互斥。
7. 任何「定因」以机器证据（日志行/文件 mtime/进程命令行）为准；时间一律用机器时间；说错了当场更正。
8. 不动 `.env`/凭据；报告只写掩码。

---

## 附录 A：本次演练现场记录（原始证据）

**A-1 时间线摘要**：23:07:40 闸门+对账 → 23:09–23:29 preflight 7 连试（见 §1）→ 23:10 dry-run 演示 → 23:32–23:50 error_daily（失败）→ 23:38–23:52 identity（report 崩溃 / selftest OK）→ 23:43 快照+对账（24/24、never_ran=43）→ 23:44 triage → 23:45 rerun 预演 → 23:46 dispatch_due 预演 → 23:55 代理 A/B 定因。
**A-2 502 根因（跨卡复核）**：系统代理 `127.0.0.1:7890` 拦内网地址；本卡 A/B 实测 `getproxies()={'http':'http://127.0.0.1:7890',…}`，默认 urllib **2/6 次 502（每次卡 5.0s、空 body）**，显式 `ProxyHandler({})` **6/6 全 200**；curl 不受影响（只读环境变量代理）。**共 62 个工具用 urlopen，仅 1 个绕代理**。
**A-3 通道劣化数据**：`gui.log`=2.34MB / `asst.log`=41.0MB / `app.log`=12.6MB（23:5x）；2.34MB 文件 `ssh type` >200s 未拉完 ⇒ 有效带宽 <12KB/s（同期多个 worker 会话共用通道）。
**A-4 他卡并发（背景事实）**：t_7d630aa6 23:26 起串行补跑（ledger `source` 标记）；23:42 有 `stage_fix` ledger 条目（另卡配置手术）；23:14 观察到他会话 MAAOrch-StartOne-25 / 截图/历史拉取 ssh。
**A-5 证据文件**（任务工作区 `…/workspaces/t_9d24454f/evidence/`）：`step0_host_processes.json`、`step0_rest_probe_series.txt`、`step0_rest_urllib_vs_curl.txt`、`step0_rest_matrix.txt`、`step0_rest_proxy_ab.txt`、`step1_preflight_wb1*.txt`（7 次）、`step2_reconcile_dryrun.txt`、`step2_reconcile_json.txt`、`step2_list.txt`、`step3_queue_wb3_dryrun.txt`、`step4_switch_gate.txt`、`step4_err_daily_bg.txt`、`step4_identity_report_bg.txt`、`step4_identity_selftest.txt`、4 个探针脚本。

## 附录 B：文档索引

| 主题 | 文档 |
|---|---|
| 六个步骤的全链路口径 | 本文 + `docs/MAS_SCHEDULE_MAP.md`（槽位/队列/冲突实例） |
| 应急预案 / 一键回滚 | `docs/MAS_EMERGENCY_RUNBOOK.md`（运维④） |
| 补跑规则全文 | `docs/MAS_RERUN_POLICY.md` + 草案 `docs/MAS_RERUN_TOOLCHAIN_DRAFT.md`（运维③） |
| 调度器命令/加固 | `docs/audits/2026-09-27_dispatch_hardening.md`（运维②） |
| preflight 接入方案 | `docs/audits/2026-09-27_mas_preflight_wiring.md`（t_cd11ca49） |
| 观察面接线设计 | `docs/MCP_TOOL_ROADMAP.md` §2–§4 |
| 日志判据字典 | `docs/MAA_LOG_DICTIONARY.md`（B3） |
| 接管契约 / 补发边界 | `docs/HERMES_MAS_HANDOVER.md` §2/§13 |
| 巡检提示词（现行） | cron `52b88b0ffca0`（备份见 t_562840d3 工件） |
