# 20260924_1831 主线：面板实锤徽章 ⇒ 真打依据（「看着矿不挖」修复）

- 改动文件：`runtime/_prime_queue3.py`（**唯一**被改的生产文件；原子写 `.tmp` + `os.replace`，每改一次 `py_compile`）
- 改前 md5：`f4e16cbc4a96bf7c70490dfaebe62226`，改后 md5：`abb796f7fc9142048886d52f3a2ddcdc`
  （改前副本：`runtime/_prime_queue3.py.bak-badgereal-20260924_181913`，另存 scratch `prime_queue3.py.before-badgereal`）
- 没有碰：模拟器 / adb / MuMuManager / MAA / 8765 / `tools/*` / `data/*`，没有 kill 任何进程
- 离线验证：`assert_queue3_fixes.py` **通过 114 / 失败 0**；`assert_queue3_e2e.py` **失败 0 项（90 条 ✓）**
  真账本 `_prime_state3.json` / `_prime_noreach.json` / `axis_outcomes.jsonl` 跑前跑后 **md5 完全一致**

## 1. 真机证据（Lead 18:10–18:13 日志）

```
18:12:22  演习 1/2 prts://36786 ⇒ 无结论（gate3-invalid）
18:13:20  演习 2/2 prts://28313 ⇒ 无结论（gate1-not-detail）
18:13:20  [JT8-3|普通] ⚠ 演习 2 份轴都没有结论 ⇒ 只登记需求、不写 noreach、状态不变
18:13:49  [story] main_nav: ok=True on_page=JT8-3 stars=1 徽章=True      ← 面板已实锤有矿
18:13:49  [JT8-3|突袭] ⛔ 主线突袭不跑演习 ⇒ 本轮只登记需求
```

两个后果都不对：

1. **普通关**：面板实锤 `徽章=True`（当前实测的确定矿），却因为"演习拿不到结论"一份真打都没跑；
2. **突袭关**：同样读到 `徽章=True`，被"突袭不跑演习"守卫直接跳过 ⇒ 也是一份真打都没跑。

两者都是**看着矿不挖**：主线一颗源石都赚不到，而这恰是 EV 的大头。

## 2. 改了什么（逐条）

### 2.1 普通关：演习全无结论 + 徽章=True ⇒ 真打 1 份

`story_drill_first(..., *, panel_prime=None, broken_real=True)` 新增两个具名入参；
在 `dec == "drill_broken"`（闸3-invalid／超时／没进战斗，**没有任何结论**）且 `panel_prime is True` 时：

- 用**难度匹配的最优候选轴**（`cands[0]`，即 `story_jobs_ranked` 里 `match==0` 的队首）真打**恰好一次**；
- 走的是原 `story_real_fight()`：结算页星数 + 面板/徽章交叉确认、必写 `axis_outcomes.jsonl`、
  3★ 才写 `stone`、失败/2★ 只写 partial、**任何情况都不写 noreach**；
- 真打前**理智门槛不变**（`nav.sanity < u.ap` ⇒ 记 wait 换下一关，在 `main()` 里、真打之前）。

判据**严格 `panel_prime is True`**（不是真值判断）：徽章 `False`（面板明说没矿 ⇒ 更早一步 `⊘ done`）
或**读不到**（`None`）都**不盲打**，退回旧的"只登记需求"。

日志（真机可直接对着看）：

```
[JT8-3|普通] 🧱 演习 2 份轴都没有结论，但**面板已实锤有徽章**（徽章=True）⇒ 演习无结论也不放弃，真打 1 份（难度匹配的最优轴 JT8-3_36786.json）
[JT8-3|普通] ▶ 主线真打（JT8-3_36786.json｜标签=main_08-17｜难度匹配=是｜面板已实锤有徽章 ⇒ 演习无结论也不放弃，真打 1 份）
```

### 2.2 突袭关：跳过演习，但**直接真打**（难度匹配轴 + `--raid`）

保留 Lead 那条守卫的**精神**（突袭**确实不跑演习**：真机 2/2 轴 gate3-invalid，且把游戏留在
「演习中 + PAUSE」里），但不再因此连真打都不试：

```
if job["match"] != 0:   # 没有难度匹配的突袭轴
    ⛔ 不真打（**不许拿普通轴去当真突袭打**）、只登记需求、状态不变
else:
    ⚔ 突袭：演习不可用 ⇒ **直接真打**（难度匹配轴 <轴>.json + --raid；受理智门槛与 --raid-real 约束）
```

- `--raid` 由 `story_attempt()` 里 `want=="突袭" ⇒ cmd.append("--raid")` 保证（这段没动，仍在）；
- 找不到**难度匹配**的突袭轴 ⇒ 只登记需求（例如 `R8-1|突袭`：3 份候选全是 `main_08-01` 普通档）；
- 突袭分支只在判决 `mine` 时到达 —— 面板明说"报酬无源石"会先判 `done`；所以能走到这里的
  "徽章读数缺失"情形同样值得真打一次（否则这关永远不挖），且仍受 `--raid-real` + 理智门槛约束。

### 2.3 两个上限开关（都默认 1 = 打；0 = 退回"只登记需求"）

```
--drill-broken-real 1   普通关演习全无结论 + 徽章=True 时是否真打 1 份
--raid-real 1           突袭关是否直接真打 1 份（+ --raid）
```

`--help` 实测（`ev_badgereal_help.txt`）已列出两条，且默认 1。

### 2.4 纪律（没动的，逐条确认）

- 失败**不写 noreach**：`story_real_fight` / `story_drill_first` 两段源码里 `mark_noreach`、
  `_noreach_write` 出现次数 = **0**（测试断言）；
- **drill 行永不带收益**：`outcome_row()` 的兜底断言 + `drill_outcome_row()` 唯一入口仍在，
  `stone` 全文件只在 `story_real_fight` 的 `extra["stone"] = True` 处写（测试断言 `count == 1`）；
- 真打仍是"结算页 > 面板"的口径（`story_verdict_from_stars`），面板只能否决 3★；
- 演习优先链其余分支（3★ 即打、1~2★ 换轴、券不足走真打、`no_axis` 0 理智放弃）**未改**。

## 3. 离线验证（命令与输出）

```bash
# 1) 语法（每次改完都跑）
C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe -m py_compile runtime/_prime_queue3.py
# → exit 0，无输出

# 2) 纯函数 / 源码守卫 / 单元级（不碰模拟器，状态与账本全重定向）
C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe \
  C:/Users/xiach/AppData/Local/hermes/cache/scratch/assert_queue3_fixes.py
# → ===== 通过 114 / 失败 0 =====        （证据：scratch/ev_badgereal_fixes.txt）

# 3) 端到端（真调 main()，build_units/sweep/story_enter 全桩，`--json-out` 由假工装写）
C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe \
  C:/Users/xiach/AppData/Local/hermes/cache/scratch/assert_queue3_e2e.py
# → ===== 失败 0 项 =====（90 条 ✓）     （证据：scratch/ev_badgereal_e2e.txt）

# 4) 真账本没被碰（live 跑批并发感知：只揪"本测试才会写的行/键"）
md5sum runtime/_prime_state3.json runtime/_prime_noreach.json runtime/axis_outcomes.jsonl
# 跑前 == 跑后：3dce0697… / eabc8309… / fb57caca…
```

### 3.1 新增/更新的用例（对着任务书逐条）

`assert_queue3_fixes.py`（P7，直调 `story_drill_first`，用例关卡 = 真机那关 `JT8-3|普通`）：

| 用例 | 断言 |
|---|---|
| P7-A 两份无结论 + 徽章=True | 真打**恰好一次**、轴 = 难度匹配队首 `prts://36786`、不带 `--drill`、3★ ⇒ `done`、真打行带 `stone`、**drill 行无 stone**、账本顺序 `drill,drill,real`、**0 条 noreach** |
| P7-B 两份无结论 + 徽章=False | **一次真打都没有**、状态仍 `todo`、账本只有 drill 行且无收益、日志「只登记需求、不写 noreach」 |
| P7-C `--drill-broken-real 0` | 徽章=True 也**不真打**、点名开关、状态不变 |
| P7-D 真打只有 2★ | 仍真打 1 份、状态 `partial`（不假报 done）、real 行 `stars=2` **无 stone**、**不写 noreach** |
| 源码守卫 | 开关默认值、`panel_prime is True` 严格判据、突袭分支要求 `job["match"] != 0`、真打路径无 noreach 写入、`stone` 只有一处写 |

`assert_queue3_e2e.py`（真调 `main()`）：

| 用例 | 断言 |
|---|---|
| 5c0 两份无结论 + 徽章**读不到** | 演习照跑、**不真打**、状态 `todo`、日志「面板**没有**实锤徽章 ⇒ 不盲打」 |
| 5c 两份无结论 + 徽章=False | 不真打、连演习都不跑（更早走 `⊘ done`）、无 noreach、账本空 |
| 5c2 两份无结论 + **徽章=True** | **必须调一次真打**（`prts://102059`）、不带 `--drill`、3★ ⇒ `done`、真打行带 stone、drill 行无 stone、无 noreach |
| 5c3 `--drill-broken-real 0` | 不真打、状态 `todo`、日志点名开关 |
| 5c4 徽章=True 但理智 5 < 门槛 18 | **不真打**、状态 `wait` |
| 5g1 突袭（默认） | 一次演习都不跑、**真打 1 份**、命令行**必带 `--raid`**、轴 = 突袭档 `prts://76672`、`done`、带 stone、无 drill 行、无 noreach |
| 5g2 `--raid-real 0` | 不演习、不真打、状态 `todo` |
| 5g3 无匹配突袭轴（`R8-1|突袭`，3 份候选全是普通档） | 不真打、只登记需求、一行账本都没写、日志「不许拿普通轴去当真突袭打」 |
| 5g4 突袭理智不足 | 不真打、状态 `wait` |
| 5g5 `--story-run` 突袭 | 老路仍在（跳过演习直打 + `--raid`） |

顺带修了一条**因 live 数据过期**而误报的旧断言：`P3④ AD-EX-3 无证据 ⇒ 降级`——18:25 的跑批把
`AD-EX-1..3` 学进了 `runtime/learned/stages_AD.json`，所以它现在**有真凭据**。该用例已改为
**独立重算四条证据源**再比对 `map_evidence()`（口径一致 ⇒ 必须为 True），不再硬写死快照。

## 4. 需要 Lead 真机验证的步骤

1. **普通关（JT8-3|普通，真机已复现的那关）**
   ```bash
   cd /d/1-2/MAAOrch
   MAAORCH_PYTHON=C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe \
   C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe runtime/_prime_queue3.py \
       --story-only --drill-tries 2
   ```
   期望日志顺序：
   `[story] main_nav: … 徽章=True` → `🧪 演习优先…先试 2 份` → 两份 `⇒ 无结论（…）` →
   `🧱 演习 2 份轴都没有结论，但**面板已实锤有徽章**（徽章=True）⇒ …真打 1 份（…JT8-3_36786.json）`
   → `▶ 主线真打（… --raid 无）` → `真打读数：…stars=…` → `交叉确认（面板）…` →
   3★ 时 `✓ **主线首通源石到手**` + `done`；2★/1★ 时 `◐ …partial`（**不写 noreach**）。
   请同时确认**只花了一次真打的理智**（不是两份轴连打）。
2. **突袭关（JT8-3|突袭）**：同一条命令（`--story-only`），期望：
   `⚔ 突袭：演习不可用（真机已证会把游戏留在战斗里）⇒ **直接真打**（难度匹配轴 JT8-3_47402.json + --raid；…）`
   ⇒ 进程命令行里能看到 `stage_attempt.py --job …/JT8-3_47402.json … --raid`（**这是一处硬要求**），
   且**一次演习都没有**。若该关没有难度匹配的轴，应打印 `⛔ …没有难度匹配的突袭轴 …不许拿普通轴去当真突袭打`。
3. **开关回退**：再各加一次 `--drill-broken-real 0`（普通）与 `--raid-real 0`（突袭），
   应能在日志里看到 `--drill-broken-real 0 ⇒ 不真打` / `--raid-real 0 ⇒ 本轮只登记需求`，**且零理智消耗**。
4. **账本核对**（真打那几行）
   ```bash
   tail -n 8 runtime/axis_outcomes.jsonl
   ```
   期望：`mode":"real"` 且有 `"stone":true`（只在 3★ 且徽章消失时）；同一关的 `mode":"drill"` 行
   **永远没有 `stone`**、无效局 `stars` 为 `null`。
5. **失败不留痕**：若真打只拿到 1~2★，确认 `runtime/_prime_noreach.json` 里**没有**这一关，
   且 `_prime_state3.json` 里该键是 `partial`（不是 `noreach`）。

## 5. 已知边界 / 没做的事

- 真打一次最多只打**一份**轴（任务书"真打 1 份"）；失败不自动换下一份（那会变成盲烧理智）。
- 徽章读数**缺失**（`None`）时普通关仍不真打（保守）；突袭关会真打（否则这关永远不挖）——
  这条口径写在 `main()` 的注释里，Lead 若要收紧可加一条 `prime is True` 判据。
- 事件侧（`tools/event_sweep.mjs`）未动：那边早有 `--drill-broken-tries` 同类回退。
- 本报告只覆盖离线证据；真机 3★ 与理智扣减必须由 Lead 按 §4 跑一次确认。
