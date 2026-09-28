# 补跑策略标准化：什么该补、什么不该补（账号级 rerun 判定规则）

- 卡：`t_bc75999f`（看板 `maa-parallel-benchmark-20260926`，assignee `mas`）｜落盘：2026-09-27 22:1x CST
- 性质：**只读分析 + 规则草案**。本卡未改任何生产工具/配置/队列；工具侧改动见同目录
  `MAS_RERUN_TOOLCHAIN_DRAFT.md`（草案，先不改；如需改走受控流程）。
- 数据面（全部只读）：AUTO-MAS REST（`/api/history/search` 等）、`tools/mas_error_triage.py`（经 SSH 读
  `E:\MAA-Multi\MAA_7\debug\asst.log`）、`tools/maa_error_daily.py --json`（四源聚合，22:00 快照）、
  主机快照（`mas_status` 22:07）、`runtime/mas_dispatch_{state,log}.json{l}`、`runtime/mas_ledger.jsonl`、
  `E:\AUTO-MAS\debug\app.log` 摘录（引自既有审计）。

---

## 0. 一句话结论

> **补跑的对象是「账号 × 班次日 × 班次」；补跑只解决两类问题：可重试的运行类失败、被调度截断的缺口。
> 它不解决资源、环境、已完成三类「伪失败」。**

判定 = 三问，按顺序：

1. **重跑会好吗？**（资源类、活动关不可打、客户端未就绪未修 ⇒ 不会 ⇒ 不补）
2. **这个号已经成功了吗？**（班次窗内已有 DONE / 桶内最后的成功晚于最后的失败 ⇒ 不补）
3. **超上限了吗？**（同号同班次日补跑派发 ≥ N=2 ⇒ 不补，升级人工）

一句话判定速查：

| 现象（触发面） | 判据要点 | 结论 |
|---|---|---|
| 失败=仅「自动公招」/招募确认失败 / 活动关关卡不可打、无药可吃 | 资源类（`recruit_confirm_failed`） | **不补**，等资源自愈（N1） |
| 失败=开始唤醒 `StartUp@CloseAnno` 连试 50 次 → 全链连锁失败 | 客户端未就绪全链崩 | **先修环境再补**；版本内缺陷则**不补**（N2/P3） |
| 失败发生在已有 DONE 之后（退出/超时/登录重试等收尾噪声） | 本班次已达成 | **不补**（N3） |
| 同号同班次日已补 ≥2 次 | 上限 | **不补**，升级（N4） |
| 单链失败（作战/基建/信用/领奖）且本班次无 DONE | 非资源型可重试 | **该补 1 次**（Y1） |
| 桶内无任何 DONE + 环境类失败（掉线中止/实例起来后登录失败等） | 尚未达成 | **该补 1 次**（Y2） |
| 脚本被派发过但该号在班次窗内无任何记录（后端重启孤儿化） | 截断/缺口 | **该补**（Y3；只补未跑的号） |

---

## 1. 术语与边界（先把口径钉死）

| 名词 | 口径 | 来源/依据 |
|---|---|---|
| 班次 | 早班 05:00–05:33 发车、晚班 19:30–20:03 发车（12 槽错峰 +3 min） | `docs/MAS_SCHEDULE_MAP.md` §2、`tools/mas_dispatch.py` SHIFT_TABLE |
| 守卫窗 | **05:00–05:40** 与 **19:20–20:20**（发车/补跑一律回避）。看板侧的「资源类任务避让窗」用的是 05:00–05:40 / 19:30–20:30，两者差别在晚班收口，按本表执行（更严） | 巡检提示词（cron `52b88b0ffca0`）、`docs/audits/2026-09-26_hermes_clear_daily.md:58`；看板侧见 `docs/KANBAN_DISPATCH_POLICY.md:107` |
| 班次运行窗（观察） | 早班 04:50–13:00、晚班 19:20–02:00 | `runtime/_mas_watch_data.py:24` |
| 班次日 | AUTO-MAS history 桶（跨零点补跑按班次日归档；实测边界 ≈04:00，与 04:01 自重启重合） | 实测：09-26 桶含 09-27 01:01/01:16/01:34；09-27 桶从 04:43 起 |
| 补跑对象粒度 | **账号（脚本里的一个 user）**，不是队列/脚本。多号脚本「只跑了第 1 号」时只补未跑的号 | `AutoProxy` 多 user 顺序调度；今晚实证见 §6.2 |
| 两个补跑面 | **队列级**（`mas_dispatch`：late 标记/补发，一天一发）与**账号级**（本策略：巡检 `mas_rerun` / mas_watch / 人工）互不替代、互不抢 | `docs/audits/2026-09-27_dispatch_hardening.md` §2 |
| 触发面 T1 | 失败触发：该号在本班次窗内有 ERROR 记录（history `maa_result` 非 Success） | `mas_status.failures` / history |
| 触发面 T2 | 缺口触发：脚本在本班次被派发过，但该号在本班次窗内**无任何 DONE 记录**（截断/孤儿化） | 快照 `never_ran` / 队列进度 `done<pending` |

---

## 2. 判定规则

### 2.1 前置体检 P0–P3（任一不过 ⇒ 本轮不补，等下轮/人工）

| # | 检查 | 不过时动作 | 现状与证据 |
|---|---|---|---|
| **P0** | 本地时间不在守卫窗（05:00–05:40 / 19:20–20:20）；且目标脚本所属队列本班次未在跑 | 不补，等窗口外 | 守卫窗外补跑是既有巡检硬条件（提示词第五步 a 条）；今晚合法补跑窗口 = 20:20 之后 |
| **P1** | 目标 MAA 目录空闲（无 MAA.exe 在跑） | 本轮跳过，下轮重判 | 已实现：`tools/auto_mas_rerun.py` `busy_paths()` →「目录被占用（有产线 MAA 在跑），跳过」（今晚 14:48/16:25/18:00 三次实测） |
| **P2** | 实例 VMM 进程在位（`MuMuVMMHeadless`）；同实例当日「模拟器启动失败」< 3 次 | 先拉起/复检；同实例当日 ≥3 次 ⇒ 不补、交人工（迁号） | 工具已体检但**默认不拦**（`--skip-dead` 才剔除，误判保守）；新增「同实例当日计数」闸（taxonomy §4.6） |
| **P3** | 客户端就绪：`tools/client_state_check.py` 判 `正常:*`；同实例近 ~30 min 无 `StartUp@CloseAnno` 连试 50 次 | 先处置挡路界面（公告/活动弹层、冷启动卡、登录态）或重登；处置不了 ⇒ 不补，升级人工 | 今晚 MAA_7/8/9 全链崩即此类（§6.3）；检测器见 `docs/audits/2026-09-27_client_state_detection.md` + `tools/client_state_check.py` |

> P1 是「与在跑批次互斥」的落地形态：**同目录并发 = 互杀**（同目录换号/重试按目录路径杀进程），
> 因此补跑只看「目录忙不忙」，不看「全局有没有批次」——批跑失败之后正是补跑要用目录的时候。

### 2.2 不该补（N1–N4）

| # | 规则 | 机器判据（证据） | 动作 |
|---|---|---|---|
| **N1** | **资源型**：公招「招募确认」失败（招聘许可=0）；活动关因关卡不可打/无药可吃/干员不足失败 | `triage.kind == recruit_confirm_failed`；或 history 同名 `.log` 错误块**仅**「自动公招」；`maa_error_daily` 归类 `资源类/benign` | 不补、不告警升级；等资源/版本自愈。**连续 ≥3 天仍全败 ⇒ 转人工**（查配置/关卡计划，如 09-18 起主关卡被跳过导致临期药无消耗路径一类） |
| **N2** | **客户端未就绪全链崩**（未修环境前） | 单次运行 ≥3 条链连锁失败且首链为 `开始唤醒`；或 `asst.log` 出现 `StartUp@CloseAnno` retry→50；或 `client_state_check != 正常:*` | 先修环境（P3 处置）；若属版本内固定缺陷（如 MAA 6.18.0 `CloseAnno` 对中秋活动弹层失效）⇒ **不补**，升级为上游 issue/人工处置 |
| **N3** | **同一号当天已成功** | 该号在**本班次窗内**已有 DONE，且当前失败记录属「收尾噪声类」（`MAA 在完成任务前退出`、`MAA 进程超时`、登录重试、截图失败） | 不补。典型：今晚 21:04:15 Success → 21:04:28 超时的产线退出残留（§6.4） |
| **N4** | **超上限** | 本班次日该号被「补跑派发」计数 ≥ **2**（数据源：`runtime/mas_ledger.jsonl` 里 `kind=rerun` 的 accounts + 临时队列名 `临时-补跑-*`） | 不补；记「未收敛/钉子户」，升级人工复核（装备/实例/账号侧） |

> N3 的边界：若失败是**真失败**（整链失败、掉线中止）且发生在班次窗内、其后无 DONE ⇒ 不算 N3，
> 走 Y1/Y2/Y3 重新判。例：某号早班成功、晚班整链失败 ⇒ 晚班缺口按 Y3 判（见 §8 待拍板 2）。

### 2.3 该补（Y1–Y3）

| # | 规则 | 机器判据（证据） | 动作 |
|---|---|---|---|
| **Y1** | **单链失败且非资源型** | `triage.kind ∈ {fight_failed, infrast_failed, mall_failed, award_failed, unknown_chain}`；且该失败之后本班次窗内无 DONE；且未命中 N1–N4 | 补 1 次（同号同班次日） |
| **Y2** | **当班次日无任何成功记录 + 环境类失败** | 桶内无 DONE；失败 ∈ {掉线中止、截图失败、模拟器启动失败（P2 复检通过）、登录瞬时失败（非全链崩）、进程超时（冷启一次后）} | 补 1 次；模拟器类必须先过 P2 |
| **Y3** | **被调度截断 / 孤儿化** | 脚本本班次被派发过，但该号本班次窗内无 DONE；**且**有下列证据之一：<br>① `app.log` 重启 banner 落在班次窗内（如 19:54:17、05:34:25）；<br>② 该目录 gui.log 有实跑窗口但**无**「已解锁脚本配置」+「任务结束」收尾；<br>③ 队列进度 `done < items` 且无新事件（watcher 静默） | 补（先过 P3）；**只补未跑的号**；每号计 1 次额度 |

> T2（Y3）在现有自动化里**没有落点**：`mas_watch` 的自动补跑只看 `failures`（最后一条非 Success），
> 截断类的号全部落在 `never_ran`（只报告、不补跑，见 `runtime/_mas_watch_data.py:127-132`）。
> 这是今晚 6 个号「被决策卡住」的机制原因。

### 2.4 判定顺序（伪代码，供工具实现对照）

```
def rerun_verdict(account, shift_day, shift):        # 全部输入只读
    if in_guard_window(now):                    return NO  (P0)
    if account not enabled:                     return NO  (名册)
    if dir_busy(account):                       return WAIT(P1)   # 下轮重判
    if emu_bad(account) and same_emu_fail_today >= 3: return NO  (P2)
    if client_not_ready(account):               return FIX_ENV(P3)  # 先修再补
    tri = triage(account)                        # 按账号收敛的定因（见草案 §3）
    if tri.kind == recruit_confirm_failed or tri.resource_only:  return NO  (N1)
    if tri.chain_collapse_client:                                return FIX_ENV / NO (N2)
    if shift_done(account, shift) and tri.is_tail_noise:         return NO  (N3)
    if rerun_dispatches_today(account) >= 2:                     return NO  (N4)
    if tri.kind in {fight,infrast,mall,award,unknown} and not bucket_done_after_fail(account): return YES (Y1)
    if not bucket_done(account) and tri.retryable_env:           return YES (Y2)
    if truncated(account, shift):                                return YES (Y3)
    return UNKNOWN -> NO           # fail-closed：定不了因就不补
```

---

## 3. 窗口、上限与并发（硬约束）

| 约束 | 取值 | 依据/落点 |
|---|---|---|
| 补跑窗口 | 守卫窗外（05:00–05:40、19:20–20:20 内**不发**）；建议在班次收工后（连续两次 0 MAA）或两班之间；晚班截断的补跑不晚于次日 04:00（04:01 自重启会再吞任务） | 巡检提示词；`MAS_RUNBOOK.md` §3 |
| 每号上限 | **N = 2 次补跑派发 / 班次日**（默认）。自动路径（mas_watch）≤ **1** 次（沿用现防抖 `state.rerun[date]`）；第 2 次必须由巡检/人工显式执行并写明理由 | `tools/mas_watch.py:236-239`（现有 1 次）；新增总量计数 |
| 单批上限 | ≤ **8 个账号**/次（上游也不接受更多） | `mas_maaorch_daily_proxy.py` `MAX_RERUN_ACCOUNTS=8`；`mas_adapter._accounts()` 同样 [:8] |
| 同目录约束 | 同一 MAA 目录的号**必须放在同一条临时队列**；目标目录必须空闲 | `tools/auto_mas_rerun.py` 头注三条硬约束 |
| 并发约束 | 同一时刻只跑 **1 条**临时补跑队列（`mas_l3._RERUN.running` 已互斥）；补跑队列与产线队列**不同目录**才算并行 | `services/mas_l3.py:270-284` |
| 复核闭环 | 发车后必须读到该号**下一条 history 记录**的 `maa_result` 才算闭环；巡检只读复核 ≤2 次、不等待 | taxonomy §4.8；`mas_rerun_state.ok` 只表示「发车命令成功」不是「号恢复」 |
| 清理 | 补跑队列跑完即删；残留 <24h 必须清（今晚存量 9 条残留占目录，误发车即抢目录） | `tools/auto_mas_rerun.py --cleanup`；`MAS_SCHEDULE_MAP.md` §5.2（d 项 2） |

---

## 4. 执行 SOP（三条路径）

**A. 自动（mas_watch，每 20 分钟）**
1. 班次收工（连续两次 0 MAA）→ 出日报；
2. `failures` 非空 → 过滤 N1–N4 → 每号最多 1 次 → `auto_mas_rerun --apply`；
3. 留痕 `mas_ledger.jsonl`。（现状即可用；**建议加 N1 过滤**，见草案 §5）

**B. 巡检（cron `52b88b0ffca0`，每 30 分钟）**
1. `list_schedule` 对账 → 2. 只读巡检 + `switch_gate` → 3. `ERR_SUMMARY` 行 → 4. 失败定因（`triage_error`，读新的 `rerun_verdict` 字段）→ 5. 仅当 `verdict=可补` 且窗口/pass 才 `mas_rerun`（预演→确认）；
6. 复核 ≤2 次只读；汇报里写 `RERUN_VERDICT=<账号:规则>`。

**C. 人工兜底**
```bash
# 1) 判定（只读）
python tools/mas_error_triage.py --account "<号>" --json
# 2) 预演（写路径默认预演）
python tools/auto_mas_rerun.py --accounts "<号1,号2>"
# 3) 确认真发 + 跑完清理
python tools/auto_mas_rerun.py --accounts "<号1,号2>" --apply
python tools/auto_mas_rerun.py --cleanup
```

---

## 5. 判据落地（工具改动草案摘要）

> 完整草案（字段、伪代码、测试计划、回滚）见 **`docs/MAS_RERUN_TOOLCHAIN_DRAFT.md`**。先不改生产。

1. **单一判定源**：新增只读模块 `tools/mas_rerun_policy.py`
   （`--account / --day / --shift [--json]`），输出 `{verdict, rule, reason, evidence[], scope, limits}`；
   `triage_error` 与 `mas_rerun` 都消费它，避免三处各写一套判据。
2. **`triage_error` 增加「是否可补」字段（含证据）**：
   `rerun_verdict ∈ {可补/不可补/先修环境/未知}`、`rerun_rule`（N1..Y3）、`rerun_evidence[]`、
   `scope=bucket`（本号证据）与**按账号收敛**（现实现取目录级 asst.log 末条链，会同目录串号，见 §6.1 实测）。
3. **`mas_rerun` 增加预演判定与门禁**：`dry_run=True` 时逐号返回 `verdicts[]` 与 `blocked[]`
   （`resource_type / same_shift_done / cap_reached / guard_window / dir_busy / client_not_ready`）；
   `dry_run=False` 时对 `blocked` 号拒绝（除非调用方给 `force_reason`，并留审计）。
4. **巡检提示词**同步改用 `rerun_verdict`（提示词重构归 **运维① t_562840d3**，本卡只给契约要点，见 §7）。
5. 依赖：`triage_error` 的 SSH 挂死修复（卡 `t_ae836086`，stdio 下 `stdin=DEVNULL`）。

---

## 6. 真实案例回填（2026-09-27，全部实机证据）

### 6.1 案例 A：公招缺券（hbznn119-日）— 规则判定：**不该补（N1）**

**事实**（`/api/history/search` 逐条 + `maa_error_daily --json` 归类）：

| 桶 | 记录 | 结果 |
|---|---|---|
| 09-26 | 6 条：21:03:06 / 21:38:29 / 21:57:55 /（09-27 凌晨）01:01:00 / 01:16:58 / 01:34:47 | 全 ERROR「MAA 在完成任务前退出」 |
| 09-27 | 15 条：04:43:28（进程超时）、05:10:27、05:19:48、10:23:09、10:42:55、10:58:25、14:04:26、14:23:13、14:41:17、15:38:52、15:57:49、16:14:21、17:14:11、17:32:49、17:52:38 | 全 ERROR；其中 12 条被 `maa_error_daily` 归类 **资源类/benign**（「仅公招链失败（资源类，等券自愈）: 自动公招」） |

**实际补跑**（本规则要消灭的行为）：
- 涉及该号的临时补跑队列 **7 条**：`临时-补跑-0927_0101-1 / _0312-1 / _0443-1 / _1023-2 / _1404-1 / _1539-1 / _1714-1`；
- 留痕事件（`mas_ledger.jsonl`）：01:01 / 03:12 / 04:43 / 10:23 / 14:04 / 15:39 / 17:14 共 7 次真发车，
  另 14:48 / 16:25 / 18:00 三次「目录忙跳过」、18:45 一次发车失败；
- 全天 15 次 MAA 尝试、0 成功 —— **不改资源，重跑不会成功**（N1），且远超任何上限（N4）。

**定因判据为什么今天会失效（必须修的点）**：
- 今天 22:0x 直接跑 `tools/mas_error_triage.py --account hbznn119-日` 返回 **`kind=startup_failed`（可重试）**——
  错。它取的是 `MAA_7/debug/asst.log` **目录级**末条链错：
  `19:52:48 ... StartUp ... pre_task: StartUp@TodaysSupplies`（Px26660），这条其实属于**同目录另一个号**
  （官-15 的「客户端未就绪全链崩」，见 §6.3）；本号自己的证据是
  `18:08:08 TaskChainError {"taskchain":"Recruit"}` + `18:09:00 SubTaskError CreditShoppingTask`。
- 巡检提示词老口径把 `startup_failed` 当「真失败 ⇒ 补跑」，于是资源类号被反复补。
- 修法：判据按账号收敛（本号最近一次运行的时间窗 + 该脚本的 user 归属），资源类证据优先级高于链名兜底（草案 §3）。

**回填结论**：规则对案例 A 给出「不补」。若今天 10:42 起就按规则执行，7 次无效发车全部省掉；
剩余待人工项 = 该号连续多天无成功（09-21 起几乎每日有失败）⇒ 按 N1 的「连续 ≥3 天全败升级」转人工查号（招募许可/账号状态）。

### 6.2 案例 B：晚班被截断（19:54 后端重启孤儿化）— 规则判定：**该补（Y3）**

**事实链**（三源交叉）：

| 证据 | 内容 |
|---|---|
| 调度侧 | `晚班定时2` 19:33:55 派发（taskId `b635b22e…`）、`晚班定时6` 19:45:55 派发（`9255d842…`）；`mas_dispatch_log.jsonl` 当天各只有 **1 行**，无补发；`late` 标记 0、`dispatch_alerts` 0 |
| 主机侧 | 19:54:17.775 后端新 banner（app.log L103961；侧车 PID 13196→33976，`do_restart.py` 实测）——落在晚班发车中段 |
| MAA 侧 | MAA_2 19:34:53→19:55:33（y9Flipped，19:55:31「任务已全部完成！」）；MAA_6 19:45:46→20:02:11（汐影月k，20:02:11「任务已全部完成！」）——两轮**跑完但无收尾**：无「已解锁脚本配置」、无「开始代理用户」第 2 个、无「任务结束」 |
| 结果 | **两脚本第 2–4 号当晚未跑**：官-2-4-47 的 古茶七心红、音乐厅生动的深海鱼、寻找勇者的大魔王；官-1-1-7 的 潮响、tbNick_2l3vf、him。6 个 watcher 观察到 20:50 全静默 |

**独立复核（本卡自做，只读，两个口径）**：
- **口径 1｜主机快照（22:07）**：`晚班定时1–8` 全部 `done=0`、`pending` 为全部号（8 条队列共 **39 个启用号项**）；
  `晚班定时9–12`（发车在 19:54 重启**之后**）正常推进（各 3–4 项完成）。同一快照 `failures=[]`、`never_ran=45`。
- **口径 2｜名册 × history 复算（~22:35，探针 `probe_eve2.py`）**：58 个启用号中，**晚班窗（09-27 ≥19:20）无记录 43 个**：
  其中 **34 个全天零记录**、9 个只有白天记录（145三月初发货、PAGI、him、tbNick_2l3vf、y9Flipped、帝国猛虎、汐影月k、潮响、hbznn119-日）；
  仍在跑的 9–12 号队列里还剩 3 个号（用户_99606616、Buling、不要加班了）未出记录——属正常在跑，**不算缺口**。
- 案例 6 号的明细：前 3 号（古茶七心红、音乐厅生动的深海鱼、寻找勇者的大魔王）**全天零记录**（早班也被 05:34 重启截断，且从无补跑）；
  后 3 号（潮响、tbNick_2l3vf、him）仅白天 DONE（11:15 / 11:35 / 12:07，来自 10:23 补跑——早班缺口补过，晚班缺口没有）。
- ⇒ **19:54 重启的真实爆炸半径 ≈ 前 8 条晚班队列（40 号上下）**，不止两条多号脚本（见 §8 待办 1）。
- 机制空档：截断号**不在** `failures` 里（它们在 `never_ran`），而现有自动补跑只看 `failures`（§2.3 注）。

**回填结论（规则对案例 B 的判定与动作）**：
1. 6 个号 = `Y3 该补`（本班次窗内无 DONE + 重启 banner + 无收尾行）。
2. 补法：按目录分 **2 条临时队列**（MAA_2 三个号一条 / MAA_6 三个号一条）；**只补未跑的号**（不重跑已完成的第 1 号）。
3. 前置：**先过 P3**——同一晚 MAA_7/8/9 正出现「客户端未就绪全链崩」（§6.3），不修环境的补跑会原样重演。
4. 窗口：20:20 之后（守卫窗外）；目录空闲已满足（MAA_2 19:55、MAA_6 20:02 已收尾）。
5. 额度：6 号各计 1 次（其中潮响/tbNick_2l3vf/him 今天已用 1 次——10:23 那次补的是**早班**缺口，按「派发计数/号/班次日」口径，本班次窗内计 1 次，仍在上限内）。
6. 需人工/巡检显式执行（本卡只读，不发车）；`t_38b8ef3c`（补号与首轮验收）可引用本节证据。

### 6.3 附：同夜「客户端未就绪全链崩」（N2/P3 的真实素材）

- MAA_7：19:49:58 起客户端停在**冷启动世界观光卡/公告弹层** → 19:51:39 `CloseAnno` 命中一次后再无法命中 →
  19:51:40–19:52:48 **50 次重试吃满** → `TaskChainError StartUp`；此后 Fight/Infrast/Recruit/Mall/Award 连锁失败。
- MAA_8：19:53:27 登录完成 → `CloseAnno` retry0 → 19:55:58 retry50 崩 → 19:56:04–19:57:56 五条链连锁失败
  （「本轮以『已完成但出现错误』收尾」）。
- MAA_9 同构（20:22→20:24 崩）。
- 今日 `ERR_SUMMARY=100 条，Top=客户端未就绪`（35 条；未知 34、资源类 28；fatal 64 / warn 7 / benign 29；登录类 33 ≥ 20 升级线）。
- 判读：**这类失败重跑无用**（版本内弹层缺陷 + 客户端残留模态），必须先跑
  `tools/client_state_check.py` 处置（清弹层/回大厅/重登）再谈补跑；对上游提 issue（CloseAnno 对活动公告页失效）。

### 6.4 附：噪声对照——「成功之后再超时」（N3 的正样本）

- 阿仄仄仄（b-7 / MAA_10）：21:04:15 `Success!` → 21:04:28 一条 0 秒的「MAA 进程超时」（t_start=time=21:04:28）。
- 按 N3：本班次窗内已有 DONE，失败属收尾噪声 ⇒ **不补**。（若按老口径「最后一条非 Success」→ 会被列入 `failures` 去补，浪费一次发车。）
- 同类：MikuKong（19:57/20:53 登录重试后 21:19 DONE）、用户_173920298（19:57 中止 → 20:19 DONE）等，均已自愈，不应补。

---

## 7. 巡检提示词契约要点（移交 运维① `t_562840d3` 整合，本卡不直接改）

1. 第四步（失败定因）：读 `triage_error.rerun_verdict / rerun_rule / rerun_evidence`；汇报行建议
   `RERUN_VERDICT=<账号(掩码)>:<verdict>:<rule>`。
2. 第五步（账号级补漏）硬条件改为四条：
   a) 守卫窗外；b) 存在 `verdict=可补` 的号（**不是**「failed_names 非空」——截断类不在 failures 里）；
   c) 每个号当天补跑派发 < 2；d) 无批次在跑（沿用）。
3. 新增第六步（缺口检测，Y3 的入口）：`mas_status` 的 `never_ran` 非空且其队列在班次窗内被派发过、
   有重启/无收尾证据 ⇒ 按 Y3 组队补（≤8/轮，先 P1/P3 体检）。
4. 保留「只报告不处置」项：`DISPATCH_ALERT`、`late_beyond_grace`、`SWITCH_GATE`。

---

## 8. 遗留与待拍板

1. **今晚 1–8 号队列的晚班缺口（口径 2 复算 ≈ 43 个号，其中 34 个全天零记录）**：本卡只做只读取证
   （22:07 快照 done=0 + 6 号逐号复核 + ~22:35 名册×history 复算，见 §6.2）。
   是否按 Y3 批量补、分几批（每批 ≤8、按目录分组、先过 P3、避开次晨 04:00 前）——**需拍板**；本卡不发车。
   注意口径差异：主机快照按「队列项」数=39，名册复算按「账号」数=43（含少数不在 1–8 号队列里的号，如官-28/hbznn119-官）。
2. **「一天一成功」还是「一班一成功」**：晚班缺但当天中午已成功（潮响类）算不算缺口？本策略默认
   **按班次判**（缺晚班 ⇒ 该补），理由=两班制的目的就是两次消耗理智；若拍板改为「一天一成功即达成」，
   把 §2.3 Y3 的「本班次窗内无 DONE」放宽为「桶内无 DONE」即可（一行改动）。
3. **N 的取值**：默认 2（含自动 1）。若希望更保守可设 1；不建议 ≥3（今晚证据：2 次不收敛基本可判钉子户）。
4. **资源类连续判升级线**：建议「连续 ≥3 个班次日全败」⇒ 转人工（hbznn119-日 09-21 起已满足）。
5. **残留清理**：9 条「临时-补跑-*」残留队列 + 停在 `state` 里的 3 条跳过记录，建议走
   `auto_mas_rerun.py --cleanup`（写操作，人工执行）——本卡不执行。
6. **同目录多号 + 目录级 asst.log** 的定因串号（§6.1）在草案里给了收敛方案；落地前巡检继续沿用
   「只报告、不据此单发」的保守口径。

---

## 附 A. 证据索引（全部只读、可复现）

| 用途 | 命令/文件 |
|---|---|
| 当日逐条尝试 | 只读探针（本卡工作区 `probe_history.py / probe_day.py`，走 `POST /api/history/search` DAILY） |
| 名册 × history 缺口复算 | 只读探针（`probe_eve2.py`：58 启用号 × 09-27 晚班窗 → 43 号无记录，34 号全天零记录） |
| 四源错误聚合 | `python tools/maa_error_daily.py --json --no-write`（22:00 快照：100 条，Top=客户端未就绪） |
| 账号定因 | `python tools/mas_error_triage.py --account hbznn119-日 --json` |
| 主机快照（failures/never_ran/队列进度） | `mas_status(days=1)`（22:07 快照） |
| 调度账 | `runtime/mas_dispatch_state.json`、`runtime/mas_dispatch_log.jsonl`、`mas_dispatch` MCP `list_schedule` |
| 补跑留痕 | `runtime/mas_ledger.jsonl`（kind=rerun） |
| 截断现场 | `tmp/hermes_daily_check/shift1930/REPORT_t_56ea4c40.md`、`docs/audits/2026-09-27_full_change_audit.md` §4.3 |
| 客户端未就绪 | `docs/audits/2026-09-27_client_state_detection.md`、`docs/audits/2026-09-27_maa_errors_triage.md` |
| 失败模式库/巡检口径 | `docs/audits/2026-09-27_mas_failure_taxonomy.md` |
| 日志判据字典 | `docs/MAA_LOG_DICTIONARY.md`（B3 卡产物） |

## 附 B. 判据/口径速查

- 失败码 → 归类：`tools/maa_error_daily.py:89-236`（`CHAIN_CAT` / `classify_result` / `classify_gui` / `classify_app`）。
- 「班次收工」= 连续两次（间隔 ≥3 min）0 个 MAA 进程（RUNBOOK §10 工程纪律 3）。
- 「目录忙」= 有 `MAA.exe` 在跑，取其**父目录**（`auto_mas_rerun.busy_paths`）。
- 「实例在位」= `MuMuVMMHeadless.exe` 进程命令行含 `MuMuPlayer-12.0-<N>`；**不要信** `MuMuManager info -v N` / `adb -v N`。
- 「切号判据」=`gui.log` 的 `账号切换 -->> <掩码>`（其它一律不算）。
- 补跑发车成功 ≠ 号恢复：`ok` 只代表发车命令；闭环看该号下一条 history 记录。

## 附 C. 本卡变更记录

- 2026-09-27 22:1x：首版（规则 3+4 条、窗口/上限、两案回填、草案分离）。未改生产工具/配置/队列。
