# 主线「演习优先」链：把事件侧那条链搬进主线分支 · 2026-09-24 15:59

实施人：**Hermes subagent（story-drill-chain）**｜唯一被改的现有文件：`runtime/_prime_queue3.py`
依据：Lead 真机确认的缺口（事件侧 `tools/event_sweep.mjs` 已有完整链条，主线分支只有**一次** `stage_attempt`）
+ `docs/audits/20260924_1527_queue_fixes.md`（P4 主线那条路的现状）+ `docs/audits/20260924_1521_raid_drill_fix.md`（突袭不跑演习的由来）

## 硬约束遵守声明

| 约束 | 实际 |
|---|---|
| **只改 `runtime/_prime_queue3.py`** | ✅ 唯一被改的现有文件（1435 → 1791 行；`+381 / −25`，见 §3 的 diff 摘要） |
| 不碰 `tools/*.mjs` / `tools/*.py` / `data/*` | ✅ 全程未写（`event_sweep.mjs` / `stage_attempt.py` 只被**读**来做口径对齐） |
| 不碰模拟器 / adb / MuMuManager / MAA / 8765；不 kill 进程 | ✅ 所有验证命令都是**离线断言**；端到端测试把 `subprocess` 换成假工装、`story_enter` 换成假导航 |
| 文件始终可编译 + **原子写**（`.tmp` → `py_compile` → `os.replace`） | ✅ 两次**实质改动**（函数块+分支+参数；修回被误删的守卫）都走这条路。另有一处**只改注释文字**的编辑是就地写的（单行注释、无语法风险，改后立刻 `py_compile` 通过）——如实列出，不粉饰 |
| 主机上**有 live 无人值守跑批在跑** | ✅ 未触碰；`run_campaign.ps1` 那一路**不带** `--with-story` ⇒ 主线分支不在它的执行路径上（见 §5 的启用提示）。本轮 `os.replace` 后新起的轮次才会加载新代码，老轮次进程不受影响 |
| 已知副作用 | 两份离线断言脚本自身会 `log()`（既 print 又 append 到 `runtime/_prime_queue3.log`）——这是**既有写法**（上一个 bot 的同一脚本也这样）。本次把 `q.LOG` 也重定向进临时目录（`assert_queue3_fixes.py`），故**本轮之后**不再追加。三份账本（`_prime_state3.json` / `_prime_noreach.json` / `axis_outcomes.jsonl`）md5 全程未变，见 §4.3 |

---

## 1. 现象（真机 + 代码事实）

* `runtime/_prime_queue3.py` 的主线分支（改前 L1281–L1312）对每个单位**只起一次** `tools/stage_attempt.py`：

  ```python
  cmd = [PYEXE, "tools/stage_attempt.py", "--job", str(job["path"]), "--tag", tag]
  if u.get("want") == "突袭":
      cmd.append("--raid")
  cmd += [] if a.story_run else ["--drill"]        # 默认加 --drill，就没有下文了
  ...
  if a.story_run and re.search(r"三星|3星|源石到手", out):   # 只有 --story-run 才可能记 done
  ```

* ⇒ **默认（无 `--story-run`）**：演习拿到 3★ 也**不真打、不记账**，只打印一行读数 ⇒ 主线**永远不会**因为我方拿到一颗首通源石。
* ⇒ **`--story-run`**：跳过演习直接真打 ⇒ **盲打**（用户铁律「2★ 纯浪费理智」，因为首通源石只认 3★）。
* 同期事件侧（`tools/event_sweep.mjs`）已经有完整的链：`drillPlan()` → `drillPhase()`（0 理智演习 ≤`--drill-tries` 份）→ `drillDecision()`（3★ ⇒ `real`；有结论但不到 3★ ⇒ `no_axis` 零理智放弃；没结论 ⇒ `drill_broken`；**没券 ⇒ `no_tickets`，不是 `drill_broken`**）→ 真打后 `verdictFromStars()` + `settleFinish()` → 账本 `drillOutcomeRow()` / `outcomeRow()`（**演习行永远不带收益**）。

## 2. 根因

主线的判定与执行**没有分层**：一条 CLI 同时承担了"挑轴验证"和"花理智真打"两件事，而唯一的区分开关 `--story-run` 被用来在"只验证"和"盲打"之间二选一 —— 缺的是事件侧那条 **"先用 0 理智的演习把 3★ 证据换到手，再花理智打那一份已知能过的轴"** 的中间态。附带三条会一起坏掉的账：没有真打就没有首通源石、没有 `axis_outcomes.jsonl` 行 ⇒ 后面的排轴（P(3星)/战绩降权）在主线这一路永远是空的。

## 3. 改法（逐条对齐任务书 1~7；含 3 处有意偏差）

### 3.1 新增纯函数（与事件侧同名函数逐条对齐，离线可单独跑）

| 新函数 | 对齐对象 | 说明 |
|---|---|---|
| `story_verdict_from_stars(settle, panel, primes, maa_done)` | `verdictFromStars()` | 结算页 > 面板；**徽章仍在 ⇒ partial（源石没发）**；≥3★ done / 1~2★ partial / 0★ failed；两读数都缺 ⇒ 承 MAA verdict 并注明读数缺失 |
| `story_drill_decision(drills)` | `drillDecision()` | 3★ ⇒ `real`；有"到不了 3★"的证据（`valid` 或 `not3`）⇒ `no_axis`（零理智）；`notickets` ⇒ `no_tickets`；全无结论 ⇒ `drill_broken` |
| `story_drill_plan(want, tickets, need)` | `drillPlan()` | `drill-first` / `raid-no-drill` / `drill_tickets_low` / `no-drill`；**只有真读到整数券数才拦**，`None` ⇒ 照常演习（不把"读不到"当"没券"） |
| `outcome_row(...)` / `drill_outcome_row(...)` / `record_outcome(...)` | `outcomeRow()` / `drillOutcomeRow()` / `recordRow()` | 写 `runtime/axis_outcomes.jsonl`；`stars=None` ⇒ 写 `null`；**`mode=='drill'` 的行一律 `pop('stone')`**（兜底断言，上游硬塞也抹掉） |
| `story_attempt(u, job, drill=…)` | `runAttempt()` | 一局（演习/真打**同一套 CLI**）；星数口径 `result_stars` > `stars`；`not3 = (valid and stars<3) or leaked`；`evidence ∈ settle/leak/attempt/none` |

### 3.2 主线分支改成两条路（L1648–L1673 附近）

```python
if (not a.story_run) and u.get("want") == "突袭":     # ← Lead 的守卫，**原样保留**
    log(...) ; continue
if not a.story_run:
    story_drill_first(u, job, a.drill_tries, st, key) # ← 默认：演习优先（0 理智挑轴 → 3★ 才真打）
    continue
story_real_fight(u, job, st, key, why="--story-run 跳过演习、直接真打")  # ← 老路（盲打）保留
continue
```

`story_drill_first()`（新，~90 行）按任务书 1~6 条落地：

1. **候选轴**：`story_jobs_ranked()` 里取 `match == 0`（难度匹配）的前 `--drill-tries` 份（默认 2，clamp 1~8）；
2. 每份先 **`--drill --no-record`**（0 理智、只花 1 张演习券）；
3. 演习 **3★** ⇒ **马上** `--job <同一份轴>` 真打一次（**真打只发生一次**）⇒ 结算星数 + **再走一次 `story_enter` 做面板/徽章交叉确认** ⇒ `done` + 真打行（`mode=real`、`stone:true`）+ 日志 `✓ **主线首通源石到手**`；
4. 演习 **1~2★ / 漏怪被掐** ⇒ 这份轴**不真打**，换下一份；
5. 演习**没结论**（闸3-invalid / 超时 / 没进战斗）⇒ 换下一份；**全军覆没 ⇒ 只登记需求、不写 noreach、状态一个字节不动**（对齐审计 §P3②「连续两次才认账」）；
6. **演习券 < 需要份数 ⇒ 不跑演习**，按 `drill_tickets_low` 走真打（如实记这个原因，**绝不记 `drill_broken`**）；
7. 日志逐行打印：`演习 i/N prts://<id> ⇒ N★（kind，证据 settle） ✓ 达标` / `🎯 演习 3★ ⇒ 用**同一份轴**真打` / `（这份轴确定到不了 3★ ⇒ **不真打**，换下一份 —— 省理智就省在这）`。

### 3.3 三处**有意偏差**（请 Lead 过目）

| # | 偏差 | 理由 |
|---|---|---|
| A① | 券不足时的 `need` 用**实际候选份数**（`len(cands)` ≤ `--drill-tries`），事件侧固定用 `DRILL_TRIES` | 只有 1 份匹配轴时，事件侧会因为"券 1 < 2"而不演习；主线按"真会跑几份"算更准确。方向是**更少拦**，不会多花理智 |
| A② | 演习时加 `--no-record`（事件侧 `DRILL_RECORD` 默认也是关） | 演习只用来挑轴，不录屏 ⇒ 每份省 180s + 一次 ~50MB 拉取。任务书允许"尽量复用现有 CLI 参数"，这条是纯省时 |
| A③ | **只演习难度匹配的轴**（`match == 0`）；若一份都不匹配 ⇒ 只登记需求、不演习 | 真打那条路本身有"必须难度匹配"的安全阀（P4③）。拿一份不匹配的轴去演习，就算 3★ 也**真打不了** ⇒ 烧券烧时间却带不动任何收益。改前那句注释（"演习不花理智所以照跑"）已同步改掉 |

### 3.4 一处**回退事故与修复**（必读，防止后人误判 diff）

第 2 批替换的**起点选错**（起点取在 `tag = ...` 那一行，而 Lead 的「主线突袭不跑演习」守卫在它**之后**）⇒ 守卫被一起删掉。
**被离线断言当场抓住**（新增用例 5g 的源码守卫断言 `✗ P5 突袭默认不跑演习那条守卫还在`），随即从**备份**逐字恢复（原子写）。
现在 `grep -n 'want") == "突袭"' runtime/_prime_queue3.py` 三处齐全：
`L706`（story_enter 带 `--raid`）、`L1066`（story_attempt 带 `--raid`）、`L1654`（主线突袭**不跑演习**）——
**Lead 刚打上的这两处行为（突袭不演习、真打必带 `--raid`）都在，且有用例守着。**

## 4. 证据（原文）

### 4.1 `py_compile`

```
$ C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe -c "import py_compile; print('py_compile OK ->', py_compile.compile(r'D:\1-2\MAAOrch\runtime\_prime_queue3.py', doraise=True))"
py_compile OK -> D:\1-2\MAAOrch\runtime\__pycache__\_prime_queue3.cpython-312.pyc

$ C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe -m py_compile runtime/_prime_queue3.py ; echo "exit=$?"
exit=0
```

两次**实质改动**都是"写 `runtime/_prime_queue3.py.tmp` → `py_compile.compile(doraise=True)` → `os.replace()"；
两次都打印了 `py_compile tmp: OK` + `os.replace: OK`（唯一的例外是那处**只改注释**的就地编辑，见硬约束声明表的最后两行）。

### 4.2 离线端到端（扩展 `assert_queue3_e2e.py`：5a/5b/5c/5d/5e + 5f/5g 共 7 个新场景）

跑法（**不 spawn 任何工具**：`q.subprocess` 换成假工装、`story_enter` 换成假导航、状态/账本/日志/报告全在临时目录）：

```
$ C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe C:/Users/xiach/AppData/Local/hermes/cache/scratch/assert_queue3_e2e.py
...
✓ 5a 演习 1 份轴就 3★ ⇒ 只演习 1 次   [['102059']]
✓ 5a **真的调了一次真打**（同一份轴 prts://102059）   [['102059']]
✓ 5a 真打**不带** --drill（是真打不是演习）
✓ 5a 状态 done   [{'7-2|普通': 'done'}]
✓ 5a 日志打出「✓ 主线首通源石到手」
✓ 5a 真打行带 stone（结算 3★ + 面板徽章已消失）
✓ 5a **drill 行不带任何收益**（mode=drill ⇒ 无 stone）
✓ 5a 记账 op_id 用关卡代号（`7-2_102059`）
✓ 5b 换了下一份轴（共演习 2 份，轴序 102059 → 99069）   [['102059', '99069']]
✓ 5b 真打的是**第 2 份**（那份 3★ 的），不是 1★ 那份   [['99069']]
✓ 5b 日志明说 1★ 那份「不真打」
✓ 5b 1★ 那份的 drill 行 stars=1 + not3=true（给后面的排轴降权）
✓ 5c 两份都演习过了   [['102059', '99069']]
✓ 5c **一份都没真打**（不盲目真打）
✓ 5c 状态不变（仍是 todo，不是 noreach/failed）   [{'7-2|普通': 'todo'}]
✓ 5c noreach 账本里**没有**这一关
✓ 5c 日志写明「只登记需求、不写 noreach」
✓ 5c 无效局**不写 stars**（stars=null + valid=false）+ 不写收益字段
✓ 5d **drill 行无 stone**（哪怕假工装硬塞了 stone=true）
✓ 5d 真打行有 stone（收益只记在真打那一行）
✓ 5e 券 1 < 需要 2 ⇒ **一次演习都没跑**
✓ 5e 走真打（drill_tickets_low 口径，与事件侧 drillPlan 一致）   [['102059']]
✓ 5e 日志写明 drill_tickets_low 且**明确不是 drill_broken**
✓ 5e 状态是 done（真打 3★）——**没被当成「演习通道坏」**
✓ 5e 账本里没有 valid=false 的 drill 行（没演习就没演习行）
✓ 5f 徽章仍在 ⇒ **不写 done**、判 partial（源石没发）   [{'7-2|普通': 'partial'}]
✓ 5f 真打行**不带 stone**（收益以徽章为准）
✓ 5g 突袭默认**不演习**（一次都没跑）   [([], [])]
✓ 5g 突袭默认状态不变（只登记需求）   [{'7-2|突袭': 'todo'}]
✓ 5g 突袭默认日志有「不跑演习」那条守卫
✓ 5g --story-run 突袭 ⇒ 跳过演习直接真打，且**必带 --raid**
✓ 5g --story-run 突袭真打选的是突袭档轴（prts://76672）
✓ 真 _prime_state3.json 未被本次端到端测试改动   [686c15fae62d03ecd903f36f61dffce1 → 686c15fae62d03ecd903f36f61dffce1]
✓ 真 _prime_noreach.json 未被本次端到端测试改动   [eb7350ee947d99ee1eec05606f8c20ff → eb7350ee947d99ee1eec05606f8c20ff]
✓ 真 axis_outcomes.jsonl 未被本次端到端测试改动   [7b5754520ad43fa97b181aed1465a6fc → 7b5754520ad43fa97b181aed1465a6fc]

===== 失败 0 项 =====   (exit=0；共 56 个 ✓、0 个 ✗)
```

**关键场景的队列原始日志**（假工装，但走的是**真** `story_drill_first` / `story_attempt` / `story_real_fight` / 账本代码）：

```
── 5a 演习 3★ ⇒ 真打 ──
15:56:35   [7-2|普通] 演习券剩余 9 张（本次最多需要 2 张）｜屏上文字：演习券 3
15:56:35   [7-2|普通] 🧪 主线**演习优先**（0 理智）：按轴序先试 2 份，每份 1 张演习券；3★ 才用**同一份轴**真打
15:56:35     演习 1/2 prts://102059（7-2_102059.json）⇒ 3★（clear，证据 settle） ✓ 达标
15:56:35     🎯 演习 3★ ⇒ 用**同一份轴**真打（真打只发生一次）
15:56:35   [7-2|普通] ▶ 主线真打（7-2_102059.json｜标签=main_07-01｜难度匹配=是｜演习 3★ 达标）
15:56:35   [7-2|普通] 真打读数：证据=settle／kind=clear／stars=3／valid=True｜（假工装）stage_attempt...
15:56:35   [7-2|普通] 交叉确认（面板）：stars=3／徽章=False
15:56:35   [7-2|普通] ✓ **主线首通源石到手**（真打 3★／证据 settle／settle-3star）⇒ done；已记账 runtime/axis_outcomes.jsonl（真打行带 stone）
    演习命令  = tools/stage_attempt.py --job D:\1-2\MAAOrch\runtime\plans\story_jobs\7-2_102059.json --tag story_72_7-2_102059_drill --json-out ...\attempt_story_72_7-2_102059_drill.json --drill --no-record
    真打命令  = tools/stage_attempt.py --job D:\1-2\MAAOrch\runtime\plans\story_jobs\7-2_102059.json --tag story_72_7-2_102059_real --json-out ...\attempt_story_72_7-2_102059_real.json
```

```
── 5c 两份都没结论 ──
15:56:36     演习 1/2 prts://102059（7-2_102059.json）⇒ 无结论（gate3-invalid）
15:56:36     演习 2/2 prts://99069（7-2_99069.json）⇒ 无结论（gate3-invalid）
15:56:36   [7-2|普通] ⚠ 演习 2 份轴都没有结论（闸3-invalid／超时／没进战斗）⇒ **只登记需求、不写 noreach、状态不变**（对齐审计 §P3②：连续两次才认账）
```

```
── 5e 演习券不足 ──
15:56:36   [7-2|普通] 演习券剩余 1 张（本次最多需要 2 张）｜屏上文字：演习券 3
15:56:36   [7-2|普通] 🎫 演习券不足（1 < 需要 2 张）⇒ **不跑演习**，按 `drill_tickets_low` 走真打（**不是 drill_broken**；券会随时间恢复）
15:56:36   [7-2|普通] ▶ 主线真打（7-2_102059.json｜标签=main_07-01｜难度匹配=是｜drill_tickets_low（券 1））
15:56:36   [7-2|普通] ✓ **主线首通源石到手**（真打 3★／证据 settle／settle-3star）...
```

**账本行原文**（5d：假工装故意在演习 JSON 里塞 `stone:true`）：

```
drill 行 = {"ts":"2026-09-24T15:56:36","op_id":"7-2_102059","stage":"7-2","want":"普通","id":102059,
            "stars":3,"note":"drill-3star","src":"drill","mode":"drill","evidence":"settle","opers":[]}
real  行 = {"ts":"2026-09-24T15:56:36","op_id":"7-2_102059","stage":"7-2","want":"普通","id":102059,
            "stars":3,"note":"real-settle-3star","src":"settle","mode":"real","evidence":"settle",
            "stone":true,"opers":[]}
```

`--story-run` 突袭的真打命令行原文（证明 `--raid` 没丢）：

```
C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe tools/stage_attempt.py
  --job D:\1-2\MAAOrch\runtime\plans\story_jobs_big\7-2_76672.json
  --tag story_72_7-2_76672_real --json-out ...\attempt_story_72_7-2_76672_real.json --raid
```

### 4.3 纯函数断言（扩展 `assert_queue3_fixes.py`：`story_verdict_from_stars` × 8 / `story_drill_decision` × 10 / `story_drill_plan` × 7 / 账本行 × 6 / 源码守卫 × 5）

```
$ .../python.exe .../assert_queue3_fixes.py
✓ P5 verdict 真打结算页 3★ + 徽章已消失 ⇒ done（源石到手）
✓ P5 verdict 结算页 3★ 但**徽章仍在** ⇒ partial（星星再好看源石也没发）
✓ P5 verdict 结算页 2★ 而面板读成 3★ ⇒ **结算页优先**（不许判 done）
✓ P5 verdict 读数缺失 + MAA 判 failed ⇒ failed（绝不假报 done）
✓ P5 drillDecision 第 1 份 2★、第 2 份 3★ ⇒ 换轴后真打**第 2 份**
✓ P5 drillDecision 两份都到不了 3★ ⇒ no_axis（**一份都不真打**、零理智）
✓ P5 drillDecision 漏怪被掐（not3、无结算页）⇒ no_axis（不许当通道坏）
✓ P5 drillDecision **没券** ⇒ no_tickets（drill_tickets_low），**绝不 drill_broken**
✓ P5 drillPlan **券 0 < 需要 2 ⇒ 不演习 + why=drill_tickets_low**
✓ P5 drillPlan 券**读不到**（None）⇒ 照常演习（不把「读不到」当「没券」）
✓ P5 drillPlan 任何输入都不返回 why=drill_broken
✓ P5 **演习行不带 stone**（上游硬塞也抹掉）
✓ P5 落盘后：drill 行仍无 stone、real 行有 stone
✓ P5 突袭默认不跑演习那条守卫还在（不回退 Lead 的修复）
✓ P5 真打必带 --raid（不回退 Lead 的修复）

===== 通过 85 / 失败 0 =====   (exit=0)
```

### 4.4 两份/三份真账本 md5（跑前/跑后）+ **并发写批的处理**

```
$ md5sum runtime/_prime_state3.json runtime/_prime_noreach.json runtime/axis_outcomes.jsonl   # 跑前
686c15fae62d03ecd903f36f61dffce1 *runtime/_prime_state3.json
eb7350ee947d99ee1eec05606f8c20ff *runtime/_prime_noreach.json
7b5754520ad43fa97b181aed1465a6fc *runtime/axis_outcomes.jsonl
$ （跑 fixes + e2e 两份断言）
fixes exit=0  e2e exit=0
$ diff md5_before.txt md5_after.txt && echo MD5-UNCHANGED-OK
MD5-UNCHANGED-OK                       # ← 连续 6 次运行都是"一字未改"
```

**最后一次运行出现了一次 md5 变化，已查明是 live 无人值守跑批并发写的，不是本测试**（下面是原文与判定）：

```
✓ 真 _prime_state3.json 被**别的进程**（live 跑批）并发改过，但**没有本次测试写的行/键**
    [686c15fae62d03ecd903f36f61dffce1 → 575607d429708ae356dd1544db287450；可疑改动={}]
✓ 真 _prime_noreach.json 未被本次端到端测试改动   [71f4b00e48a6b68e7585be28369640f4]
✓ 真 axis_outcomes.jsonl 未被本次端到端测试改动   [32e56640855a07dd8e8d9900126cc1c0]

===== 失败 0 项 =====
```

判据（也是这轮给两份断言脚本加的**并发感知**硬约束：只揪"本测试才会写的那种改动"）：

```
$ ls -la --time-style=+%H:%M:%S runtime/_prime_state3.json runtime/_prime_noreach.json runtime/axis_outcomes.jsonl
-rw-r--r-- 1 xiach 197609   856 16:02:43 runtime/_prime_noreach.json     # ← live 队列写的 CV-EX-8|突袭(class=reach)
-rw-r--r-- 1 xiach 197609  5604 16:04:07 runtime/_prime_state3.json     # ← e2e 运行中 live 队列写的
-rw-r--r-- 1 xiach 197609 18762 16:00:55 runtime/axis_outcomes.jsonl    # ← live event_sweep 写的 fallback-tried 行

$ python -c "...统计..."
state keys: 195 | 7-2 相关: {}                       # ← 状态里**一个主线键都没有**（本测试若漏写必有 7-2|普通）
状态分布: Counter({'noreach': 113, 'todo': 73, 'done': 9})
axis_outcomes 行数: 75 | 含 stage=7-2 的行: []        # ← 账本里**一行本测试的东西都没有**
```

本测试所有的账本行/状态键都带 `7-2` 且只写进临时目录 ⇒ **真文件里 0 条**，md5 的变化来自 16:00–16:04 期间 live
跑批的写入（`event_sweep.mjs` 的 `fallback-tried`/`maa-selfreport` 行、主队列的 `CV-EX-8|突袭` noreach）。
**这条也提醒后人：在主机有 live 跑批时，任何"跑前跑后 md5 必须相同"的断言都要按内容归属判定，否则会误报。**

改后文件：`runtime/_prime_queue3.py` 1791 行 / md5 `f4e16cbc4a96bf7c70490dfaebe62226`（改前 1435 行 / `1eea858621671a48c24321c4e8b51f15`）。
备份：`C:\Users\xiach\AppData\Local\hermes\cache\scratch\storydrill_backup\_prime_queue3.py.bak`；
diff：`...\scratch\ev_storydrill_diff.txt`（+381 / −25）；两份断言的完整输出：`...\scratch\ev_storydrill_fixes.txt`、`...\scratch\ev_storydrill_e2e.txt`。

## 5. 待 Lead 真机验证的步骤（我这边**不能**碰模拟器）

**⚠ 行为变更要点（务必知悉）**：主线普通关的**默认**行为由「只演习、不真打」改成「**演习优先：0 理智演习挑轴，拿到 3★ 就用同一份轴真打一次**」——也就是说默认模式**会花理智**（但只花在已被 0 理智证据证明能 3★ 的轴上；2★ 白烧理智这条路被堵住）。旧的"只验证不真打"现在只能用 `--dry`（只排产）表达；若 Lead 需要一个"演习验证、不真打"的开关（例如 `--story-no-real`），说一声我加 —— **本轮没加**，避免超出授权范围。

1. **只读预检**：`python runtime/_prime_queue3.py --story-only --dry`（应看到 `主线就绪度：难度匹配的轴 245/307 个`、`共 502 个单位`；不写盘、不碰模拟器）。
2. **单关离线+真机（安全做法：用状态副本，不污染生产状态）**：
   复制 `runtime/_prime_state3.json` 到副本 `runtime/_prime_state3.drilltest.json`，把副本里**除 7-2|普通 之外**的单位全标 `done`，然后
   `python runtime/_prime_queue3.py --story-only --state runtime/_prime_state3.drilltest.json --drill-tries 2`
   —— 预期日志依次出现：`演习券剩余 N 张` → `🧪 主线**演习优先**` → `演习 1/2 prts://… ⇒ 3★（clear，证据 settle）` → `🎯 演习 3★ ⇒ 用**同一份轴**真打` → `交叉确认（面板）：stars=…／徽章=…` → **`✓ **主线首通源石到手**`**。
3. **核对三处账**：① `runtime/axis_outcomes.jsonl` 新增两行（`mode:"drill"` 行**无** `stone`；`mode:"real"` 行有 `stone:true`）；② `runtime/stone_ledger.json` 的「当前持有」+1；③ 大厅顶栏源石数（账本不作为唯一凭据，Lead 口令）。
4. **零理智的反例**（挑一个演习拿不到 3★ 的关）：确认日志是 `（这份轴确定到不了 3★ ⇒ **不真打**）` 或 `演习 N 份轴都没有结论 ⇒ **只登记需求、不写 noreach、状态不变**`，且 `_prime_state3.json` 里该单位仍是 `todo`、`_prime_noreach.json` **不变**（candidate 是真机反例的关键：**不能又出现"一整章被标 noreach"**）。
5. **券不足**：把演习券打到 <2（或手工把 `--drill-tries` 调到 3 制造券不足），确认日志出现 `🎫 演习券不足 … 按 drill_tickets_low 走真打（**不是 drill_broken**）`，且**没有**任何「演习通道坏」的限流/账目。
6. **突袭回归**（Lead 刚打上的两处）：`--story-only` 默认模式对 `7-2|突袭` 应只打印 `⛔ 主线突袭**不跑演习**…本轮只登记需求`（0 演习 0 真打）；`--story-only --story-run` 的真打命令行必须带 `--raid`。
7. **生产启用提示**：`tools/run_campaign.ps1` 里是 `& $PY runtime\_prime_queue3.py`（**不带** `--with-story`）⇒ 无人值守现在**根本不含主线**。要让跑批去挖主线那 307 个单位，需要把命令行改成 `--story-only`（建议先跑主线、再跑活动那一路，理由见 `docs/audits/20260924_1527_queue_fixes.md` §P4④）。**这行不在我的授权范围内，我没改。**
8. `tools/stage_attempt.py --read-tickets` 是本次新用到的接口（事件侧已在用）：若真机上它常读成 `null`，主线会**照常演习**（不会因为读数缺失卡住）——这一点在 §3.1 的 `drillPlan` 里有断言护着（`✓ P5 drillPlan 券读不到（None）⇒ 照常演习`）。
