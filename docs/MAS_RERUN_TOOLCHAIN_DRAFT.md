# 补跑判定工具链草案：给 `triage_error` / `mas_rerun` 增加「是否可补」字段（含证据）

- 关联卡：`t_bc75999f`（运维③ 补跑策略标准化）｜策略正本：`docs/MAS_RERUN_POLICY.md`｜落盘 2026-09-27 22:2x
- **性质：草案。先不改生产。** 如需落地，按 §8 的受控流程走（备份 → 只读灰度 → 门禁 → 回滚点）。
- 目标（卡面第 4 条）：把「是否可补」的判定字段化，让巡检/工具不再靠直觉；字段必须带**证据**与**规则编号**。

---

## 1. 现状契约（逐处锚点，均已只读核对）

| 组件 | 现状 | 缺口 |
|---|---|---|
| `tools/mas_error_triage.py`（156 行） | 输出 `{account, script, maa_dir, chains_with_error, subtask_errors, kind, severity, reason, action, evidence, log_grep}`（`:112-124`）；`kind ∈ {recruit_abandoned, fight_failed, infrast_failed, startup_failed, mall_failed, award_failed, unknown_chain}` + `recruit_confirm_failed` 细分（`:107-111`）；`KINDS` 表 `:42-50` | ① 取 **目录级** `asst.log` 最后 40 条（`ssh_lines()` `:75-83`），同目录多号 → **串号**（今晚实测，见 §2.4）；② 只有 kind，没有「该不该补」；③ 没有「当天是否已成功 / 已补几次」的口径 |
| `tools/maa_error_daily.py`（`--json`） | 四源聚合：`items[]{cat,sev,note,chain,raw,account,slot,ts,src}`（`cat ∈ 未知/资源类/客户端未就绪/人为/网络/干员不足`）、`hist[]`（history 逐条 `maa_result`）、`slots/subtask/dedupe/gui_noise`；`classify_*` 见 `:89-240` | 已是最好的「证据池」，但没有 rerun 语义字段 |
| `mas_rerun`（MCP，`tools/mas_maaorch_daily_proxy.py`） | `dry_run=True` 只返回 `{would_start, accounts[, queues_or_note]}`（`:1216-1250`）；单批 ≤ `MAX_RERUN_ACCOUNTS=8`（`:141`，拒绝文本 `_REFUSAL_TEXT[R_TOO_MANY]` `:1231-1239`）；`accounts=None` 时由上游按 host 快照 `failures` 解析 | 预演**不判可补性**；`blocked` 概念不存在；记账 `ok` 只表示「发车命令成功」（`services/mas_l3.py:265` `ok=("发车" in out)`） |
| `tools/auto_mas_rerun.py` | `busy_paths()` 目录占用检查（「目录被占用…跳过」）；`--cleanup`；一目录一条临时队列 | 无判定；被跳过的原因混在 stdout |
| `tools/mas_watch.py` | 自动补跑只取 `d["failures"]`（`:238-239`），同号每天最多 1 次（`state.rerun[date]`，`:236/260`），≤8 | ① 不看资源类/N3；② **只看 failures** ⇒ 截断类（`never_ran`）永不进入补跑（`runtime/_mas_watch_data.py:127-132`） |
| `services/mas_l3.py` | `_RERUN` 单飞互斥（`:43/:270-284`），`[:8]`（`:272`），`rerun_state()` | 无 per-account 计数 |

---

## 2. 目标契约

### 2.1 `triage_error` 新增字段

```json
{
  "account": "hbznn119-日", "kind": "recruit_confirm_failed", "severity": "info",
  "rerun": {
    "verdict": "不可补",                 // 可补 | 不可补 | 先修环境 | 未知
    "rule": "N1",                        // N1..N4 / Y1..Y3 / P0..P3
    "reason": "仅公招链失败（招聘许可=0）；重跑不会成功",
    "evidence": ["history 09-27 15 条全 ERROR", "12 条归类 资源类/benign（自动公招）",
                 "bucket: 无 DONE", "补跑派发计数=7（上限 2）"],
    "scope": "bucket",                   // bucket=本号证据；dir=目录级（仅提示，不作为单发依据）
    "limits": {"cap": 2, "used": 7, "window_ok": true, "dir_busy": false},
    "shift": {"key": "daily|evening", "done": false},
    "next": "等资源自愈；连续 ≥3 个班次日全败 ⇒ 转人工"
  }
}
```

### 2.2 `mas_rerun` 新增返回

```json
{
  "dry_run": true, "would_start": true,
  "verdicts": [{"account": "...", "verdict": "可补", "rule": "Y3", "reason": "...", "evidence": ["..."]}],
  "dispatchable": ["..."],                       // verdict=可补 且 limits 全过
  "blocked": [{"account": "...", "rule": "N1", "why": "resource_type"}],
  "refused": null                                 // 保留现有的 too_many_accounts 等
}
```
- `dry_run=False`（真发车）：`accounts` 与 `dispatchable` 取交集；`blocked` 里的号**拒绝**，除非调用方给
  `force_reason="<至少 8 字理由>"`（沿用该代理既有的「破坏性参数要显式确认」风格），并把 `force_reason` 记进 ledger。
- 记账事件（`mas_ledger.jsonl`）新增字段：`rule`、`verdicts` 摘要、`cap_used_after`。

### 2.3 判定引擎（新文件，纯只读）

`tools/mas_rerun_policy.py` —— **单一判定源**（triage / mas_rerun / mas_watch / 巡检都消费它，避免四处各写一套）：

```
用法：
  python tools/mas_rerun_policy.py --account hbznn119-日 [--json]
  python tools/mas_rerun_policy.py --accounts a,b,c --shift evening
  python tools/mas_rerun_policy.py --day 2026-09-27 --gaps            # 只列 Y3 候选（缺口清单）
  python tools/mas_rerun_policy.py --selftest                         # 离线单测（§7 fixture）

输入（全部只读，注入式便于测试）：
  hist(account, day)      -> [{ts, maa_result, script}]
  daily(day)              -> tools/maa_error_daily.py --json 的产物（cat/sev/note）
  host()                  -> mas_status 快照（failures / never_ran / queues / accounts）
  ledger(day)             -> runtime/mas_ledger.jsonl 里 kind=rerun 的 accounts
  triage(account)         -> tools/mas_error_triage.py 的产物（按账号收敛后）

输出：{verdict, rule, reason, evidence[], scope, limits, shift, next}
```

判定内核与正本 §2.4 一致；以下是**判据表**（实现时一行一条，便于单测覆盖）：

| 规则 | 实现判据（代码级） |
|---|---|
| P0 | `in_guard_window(now)`：`05:00–05:40 / 19:20–20:20`（常量表 `GUARD_WINDOWS`，与提示词/回填脚本共用） |
| P1 | 复用 `auto_mas_rerun.busy_paths()`；命中 ⇒ `verdict=未知/WAIT`，`rule=P1`，`next=下轮重判` |
| P2 | 实例在位检查（`MuMuVMMHeadless` 进程 + 实例名）；`same_emu_fail_today >= 3`（`daily.items` 按 slot 计数）⇒ `不可补` |
| P3 | `client_state_check.py` 结果 + 近 30 min 该目录出现 `StartUp@CloseAnno` 或 gui「开始唤醒」重试级 ⇒ `先修环境` |
| N1 | `daily.items` 里该号（或该号所属 slot 的本次运行）`cat=资源类` 且 `chain ∈ {自动公招, 招募确认}`；或 `triage.kind=recruit_confirm_failed` ⇒ `不可补` |
| N2 | 单次运行 `cat=客户端未就绪` 且 `chains≥3`（连锁）⇒ `先修环境`；`triage.kind=startup_failed` 且证据含 `CloseAnno`/`retry` ⇒ 同上 |
| N3 | `shift_done(account, shift)` 为真且 `is_tail_noise(record)`（`raw ∈ {MAA 在完成任务前退出, MAA 进程超时, 登录…}`）⇒ `不可补` |
| N4 | `rerun_count(account, day) >= 2` ⇒ `不可补`；计数 = ledger 里含该号的 rerun 事件数 + 现存/历史临时队列名计数（去重） |
| Y1 | `triage.kind ∈ {fight_failed, infrast_failed, mall_failed, award_failed, unknown_chain}` 且 `not bucket_done_after(account, last_fail)` ⇒ `可补` |
| Y2 | `not bucket_done(account)` 且 `triage.retryable_env` ⇒ `可补` |
| Y3 | `dispatched(script, shift)` 且 `not shift_done(account, shift)` 且 `truncation_evidence()`（重启 banner / 无收尾行 / 队列 done<items 且静默）⇒ `可补`（只补未跑的号） |
| 兜底 | 以上都不命中 ⇒ `未知`，`next=人工`（**fail-closed**：不发车） |

### 2.4 本次必修：`triage` 的「按账号收敛」（今晚实测串号）

今晚 22:0x 对 hbznn119-日 跑 triage，得到 `kind=startup_failed`（可重试）——**错**。原因：
`ssh_lines()` 取 `E:\MAA-Multi\MAA_7\debug\asst.log` 目录级最后 40 条链错，命中的是 **19:52:48 的 `StartUp@TodaysSupplies`**
（同目录另一个号 官-15 的「客户端未就绪全链崩」），而本号自己的证据是 `18:08:08 TaskChainError Recruit` + `18:09:00 SubTaskError CreditShoppingTask`。

改法（3 处，都不大）：

```diff
-def ssh_lines(maa_dir: str, limit: int = 40) -> list[str]:
+def ssh_lines(maa_dir: str, limit: int = 40, since: str | None = None) -> list[str]:
-    ps = ("$p='%s'; if (Test-Path $p) { Select-String -Path $p -Pattern 'TaskChainError|SubTaskError' "
-          "-Encoding UTF8 | Select-Object -Last %d | ForEach-Object { $_.Line } } else { 'NO_LOG' }") % (log, limit)
+    # since：本号最近一次 history 运行窗口的起点（HH:MM:SS），只取窗口内的链错 → 不串同目录的号
+    ps = ("$p='%s'; if (Test-Path $p) { Select-String -Path $p -Pattern 'TaskChainError|SubTaskError' "
+          "-Encoding UTF8 | ForEach-Object { $_.Line } | Where-Object { %s } | Select-Object -Last %d } "
+          "else { 'NO_LOG' }") % (log, since_filter(since), limit)
```

```diff
 def triage(account: str | None, maa_dir: str | None) -> dict:
-    lines = ssh_lines(maa_dir)
+    win = account_run_window(account)        # 只读：走 /api/history/search 取本号最近一次运行 [start, end]
+    lines = ssh_lines(maa_dir, since=win.start) if win else ssh_lines(maa_dir)
+    ...
     return {
         ...
-        "evidence": lines[-4:],
+        "evidence": lines[-4:],
+        "scope": "bucket" if win else "dir",     # dir 级证据只提示、不单发
+        "window": (win.start, win.end) if win else None,
     }
```

```diff
+    # 资源类证据优先于链名兜底：本号窗口内链错含 Recruit + RecruitConfirm ⇒ 即便其它链错存在也按资源类
+    if "Recruit" in chains and any("RecruitConfirm" in ln for ln in lines):
+        kind, sev = "recruit_confirm_failed", "info"
+    elif chains and chains[-1] == "StartUp" and "RecruitConfirm" in "\n".join(lines):
+        kind, sev = "recruit_confirm_failed", "info"
```

> 配套：`t_ae836086`（stdio 下 `stdin=DEVNULL` 修复）——不修的话巡检里 triage 会挂死（SSH 240s 超时那类），
> 再怎么加字段也用不上。**本草案的落地前置依赖它。**

---

## 3. `mas_rerun`（MCP 代理）改动草案

```diff
 # tools/mas_maaorch_daily_proxy.py（dry_run 分支）
     if accounts is not None and len(accounts) > MAX_RERUN_ACCOUNTS:
         ...refuse...
+    from mas_rerun_policy import verdict_for          # 只读
+    verdicts = [verdict_for(a) for a in (accounts or resolved_from_snapshot)]
+    dispatchable = [v["account"] for v in verdicts if v["verdict"] == "可补" and limits_pass(v)]
+    blocked = [{"account": v["account"], "rule": v["rule"], "why": short(v)} for v in verdicts
+               if v["account"] not in dispatchable]
+    if dry_run:
+        return json_text({**exist, "verdicts": verdicts, "dispatchable": dispatchable, "blocked": blocked})
+    # 真发车
+    hard = [b for b in blocked if b["rule"] not in ("P1",)]        # P1=目录忙 → 本轮跳过，不算拒绝
+    if hard and not force_reason:
+        return json_text({"ok": False, "refused": "verdict_blocked", "blocked": hard,
+                          "hint": "如确需强制：force_reason='<理由>'"})
+    accounts = dispatchable or (accounts if force_reason else [])
```
- 记账：`log_event("rerun", accounts=..., ok=..., rule={a: v["rule"] for ...}, detail=out[-400:])`；
  `_RERUN` 状态里加 `verdicts`（便于巡检一行摘要展示）。
- 现有调用方兼容：新键是**追加**，`accounts=None` 的解析路径不变（但 `dispatchable` 会先把 N1/N3/N4 滤掉，
  这正是「从 host 快照 failures 全发」→「按规则发」的变化）。

---

## 4. `mas_watch` 自动路径改动草案（最小改动）

```diff
-    if d["failures"] and not args.no_rerun:
-        todo = [f["name"] for f in d["failures"] if f["name"] not in rerun_done][:8]
+    if d["failures"] and not args.no_rerun:
+        cand = [f["name"] for f in d["failures"] if f["name"] not in rerun_done][:8]
+        todo = [a for a in cand if verdict_for(a)["verdict"] == "可补"]      # N1/N2/N3/N4 不进自动路径
+        for a in cand:
+            if a not in todo:
+                log_event("rerun_skip", accounts=[a], rule=verdict_for(a)["rule"],
+                          detail=verdict_for(a)["reason"])
```
- 报告里新增一段「**未补原因**」（N1/N3 各一行），并把 `never_ran` 中命中 Y3 的号列为
  「**待补缺口（需显式批量）**」——**不自动发车**（截断类批量补跑要人拍板，见正本 §8-1）。

---

## 5. 巡检提示词改动（归 运维① `t_562840d3`，此处只给契约）

见 `MAS_RERUN_POLICY.md` §7：第四步读 `rerun.verdict/rule/evidence`；第五步条件改「存在 `verdict=可补`」；
新增第六步「缺口检测（Y3）」；汇报行 `RERUN_VERDICT=<掩码号>:<verdict>:<rule>`。

---

## 6. 判定输入的可获得性（谁在哪跑）

| 输入 | 位置 | 本会话实测可得 |
|---|---|---|
| history 逐条 | `POST /api/history/search` on `100.79.173.69:36163` | ✅（探针已跑） |
| 四源聚合 | `tools/maa_error_daily.py --json --no-write`（经 SSH） | ✅（22:00 快照） |
| host 快照（failures/never_ran/queues） | `mas_status` MCP（上游 `runtime/_mas_watch_data.py` 在主机侧） | ✅（22:07 快照） |
| 调度账 | `runtime/mas_dispatch_{state,log}` / `list_schedule` MCP | ✅ |
| 补跑账 | `runtime/mas_ledger.jsonl` | ✅ |
| 客户端就绪 | `tools/client_state_check.py`（需主机 SSH） | 依赖 `t_ae836086` 的 stdio 修复 |

> 因此 `mas_rerun_policy.py` 全部输入都能在 **mas profile（编排侧）**拿到，不需要在主机侧部署新东西；
> 只有 `never_ran/queues` 依赖已有的 `runtime/_mas_watch_data.py`（若要在主机侧自判，才需同步该文件）。

---

## 7. 测试计划

**A. 离线单测（`--selftest`，不联网，fixture 全部内联）**

| 用例 | fixture 要点 | 期望 |
|---|---|---|
| A1 资源型 | 12 条 `资源类/benign` + 无 DONE + ledger 7 次 | `不可补 / N1` |
| A2 噪声收尾 | DONE 21:04:15 + 21:04:28 `MAA 进程超时` | `不可补 / N3` |
| A3 单链失败 | 1 条 `Infrast` warn + 无 DONE | `可补 / Y1` |
| A4 截断 | 队列 done=0、pending 含该号、`app.log` 19:54:17 重启、无收尾 | `可补 / Y3` |
| A5 窗口 | now=19:50（守卫窗内） | `不可补 / P0` |
| A6 上限 | ledger 已 2 次 | `不可补 / N4` |
| A7 目录忙 | busy_paths 命中 | `未知 / P1`（下轮） |
| A8 客户端未就绪 | 5 链连锁 + `CloseAnno` retry50 | `先修环境 / P3|N2` |
| A9 串号回归 | 目录内另一号 19:52 `StartUp` 错误 + 本号 18:08 `Recruit` | `不可补 / N1`（**不得**判 startup_failed） |

**B. 今日真实案例回归（fixture 来自本卡只读取证）**
- `hbznn119-日`（09-27）：期望 `不可补 / N1`，与正本 §6.1 一致；
- 晚班截断 6 号：期望 `可补 / Y3`（正本 §6.2）；
- `阿仄仄仄`：期望 `不可补 / N3`。

**C. 现场只读验证（灰度期每轮巡检跑一次，不动产物）**
- `mas_rerun --dry_run=True` 全量跑：`blocked` 与人工结论逐号比对，差异记入巡检报告；
- **不变量**：dry-run 不得产生任何写操作（`mas_ledger.jsonl` 行数不变、无新临时队列、
  `mas_dispatch_state.json` 不变）——验证方式：跑前跑后 `md5sum` 三个文件。

---

## 8. 实施与回滚（受控流程）

1. **步骤**：① `mas_rerun_policy.py` + `--selftest`（纯只读，先合）→ ② `triage_error` 加 `rerun{}` 与 scope 收敛
   → ③ `mas_rerun` dry-run 加判定（**只加返回，不改发车**）→ ④ 灰度 `MAAORCH_RERUN_GATE=warn`（只记录 blocked，不拦）
   → ⑤ 切 `enforce`（拦 blocked）→ ⑥ `mas_watch` 加 N1/N3 过滤 → ⑦ 巡检提示词切字段（运维①）。
2. **备份**：改动前 `cp <file> <file>.bak-$(date +%Y%m%d)`；改动留 `git diff` 归档（外层仓库 `D:/1-2/MAAOrch`）。
3. **回滚**：`export MAAORCH_RERUN_GATE=off`（判定退化为仅提示，发车行为回到现状）；或还原 `.bak` 文件；
   巡检提示词回滚点 = 改前提示词全文快照（cron `52b88b0ffca0` 的 prompt 字段）。
4. **每步验收**：①单测全绿；②`--json` 旧键不丢（`kind/severity/evidence` 原样保留）；③dry-run 输出含
   `verdicts/blocked/dispatchable`；④warn 期一周内无「误拦」投诉（对比 blocked 与人工复判）；⑤enforce 后当天
   若无补跑需求，`blocked` 全为 N1/N3/N4。
5. **风险与缓解**：误判「不可补」⇒ 漏补（缓解：warn 灰度 + `未知⇒不补但报告`；巡检一行摘要里显式列 blocked）；
   计数口径歧义（ledger + 队列名双源，取并集去重；口径写进正本 §3）。

## 9. 未决与依赖

1. 依赖 `t_ae836086`（triage 的 SSH 在 MCP stdio 下挂死）——不修则字段拿不到。
2. 依赖 `t_562840d3`（巡检提示词重构）消费新字段。
3. `P3` 的 `client_state_check.py` 尚无「按实例」批量模式，本次只按目录/实例逐个调用（可接受）。
4. 截断类批量补跑的**执行**（正本 §8-1，口径复算 ≈ 43 个启用号）不在本草案范围：草案只提供 `--gaps` 清单能力。

## 附. 变更记录

- 2026-09-27 22:2x：首版草案（契约/引擎/四处 diff/测试/回滚）。未改任何生产文件。
