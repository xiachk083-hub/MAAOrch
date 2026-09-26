# MAAOrch 接手审计（离线 · 独立复核）— 2026-09-24 14:38

审计人：**独立 subagent（非 DSH-agent 团队）**
仓库：`D:\1-2\MAAOrch`｜shell：git-bash｜Python：`C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe`（3.12.10）｜node v24.14.0
口径：**每项结论都必须有可复跑的命令与原始输出为证**；凡只有文档说法、没有产物佐证的，一律记「无证据」。

## 硬约束遵守声明

| 约束 | 实际 |
|---|---|
| 不碰模拟器 / adb / MuMuManager / MAA / 8765 | ✅ 全程未执行任何 adb/MuMu/MAA/HTTP 调用 |
| 不 kill 进程 | ✅ |
| 不改 `tools/`、`runtime/` 下**现有**文件 | ✅（只新增 `docs/audits/` 报告 + hermes scratch；`py_compile` 产生的 `tools/__pycache__/stage_attempt.cpython-312.pyc` 已删除，见 §6） |
| 只跑 `--dry`，不跑真实 sweep/attempt | ✅ |

**已知副作用（不可避免，需知悉）**：`_prime_queue3.py` 的 `log()`（L63–66）**除了 print 还会 append 到 `runtime/_prime_queue3.log`**，所以题目允许的 `--dry` 也在该日志尾部追加了 ~55 行（14:34:02 起，见 L7033+）。这是被要求执行的那条命令自身的写法所致，我没有改动任何现有文件的内容。若审计要求"日志零污染"，需要在跑之前把 `LOG` 指到别处。

---

## 1. 账本自洽性（题目 1）

### 1.1 三条明细逐条核对

命令（逐条取原始行）：

```bash
cd /d/1-2/MAAOrch
cat runtime/stone_ledger.json
cat runtime/axis_outcomes.jsonl
ls -la runtime/reports/event_sweep_*.jsonl runtime/reports/attempt_*.json
ls -la runtime/_settle_ea1.png runtime/_lb2_top.png runtime/_t17_lobby_top.png runtime/_lobby4.png
grep -rln '41427' runtime/reports/
```

| # | 明细 | 账本文件里的证据行 | 旁证产物 | 结论 |
|---|---|---|---|---|
| 1 | `EA-1\|普通` 11:03 | `axis_outcomes.jsonl:56` `{"ts":"2026-09-24T11:06:06","stage":"EA-1","axis":"prts://48182","stars":3,"stone":true,"evidence":"真打 clear/3★；大厅源石 32→33"}` | `_ce6/ea1_real_48182.attempt.log`（mtime 11:03）= `结算：页面类型=clear  星级=3`；`runtime/_settle_ea1.png`（11:03，目视＝OPERATION **EA-1** / 行动结束 / **3 颗亮蓝星** / 首次掉落×1，**无「演习」字样**）；`runtime/_lobby_top.png`（11:04，顶栏＝**33**） | **有证据**（时间戳差 3 分钟：明细 11:03 vs 行 11:06，见 §1.3） |
| 2 | `CV-EX-8\|普通` 11:41 | `axis_outcomes.jsonl:58` `{"ts":"2026-09-24T11:42:36","stage":"CV-EX-8","axis":"prts://41427","stars":3,"stone":true,"evidence":"演习 3★→真打 clear/3★；大厅源石 33→34"}` | `_ce6/cvex8_41427.attempt.log`（11:34）= 演习 `maybe-clear 星级=3`；`_ce6/cvex8_real_41427.attempt.log`（11:41）= `页面类型=clear 星级=3`；`runtime/_lobby4.png`（11:42，顶栏＝**34**，时间戳 2026/09/24 11:42） | **有证据** |
| 3 | `AD-EX-2\|普通` 13:32 | `axis_outcomes.jsonl:69` `{"ts":"2026-09-24T05:32:19.498Z",...,"stars":3,"note":"settle-3star","src":"settle","mode":"real","stone":true}` → 换算 CST＝**13:32:19**（与明细 13:32 完全吻合） | `reports/attempt_20260924_1315_AD-EX-2_real_59767.json`（13:31）`{"mode":"real","kind":"clear","stars":3,"valid":true}`；`reports/event_sweep_act43side_20260924_1315.jsonl`（13:32）`"action":"done","result_stars":3,"reward_primes":true,"reward_score":0.998,"drill":[{"id":59767,"stars":3,"evidence":"settle"}],"real_axis":"59767"`；`runtime/_lb2_top.png`（13:33，目视＝**35**）＋`runtime/_t17_lobby_top.png`（13:32，目视＝**35**） | **有证据（最完整的一条）** |

三条明细**全部**在账本文件里有对应证据行，且第 3 条可端到端闭环（演习→真打→结算→账本→顶栏 35）。

### 1.2 ⚠️ 账本内部算术不自洽（**矛盾**）

```bash
C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe -c "
print('7+36-1 =', 7+36-1); print('36-1 =', 36-1)"
# 7+36-1 = 42
# 36-1   = 35
```

`runtime/stone_ledger.json` 声明 `_目标: 基线 7 → 107（累计首通 100 颗）`，即「当前持有 = 基线 + 累计首通 − 已消耗」。
实测 **7 + 36 − 1 = 42 ≠ 当前持有 35**，而 **36 − 1 = 35 恰好相等** ⇒ `当前持有` 字段**没有把基线 7 计入**。
两处定义只能二选一为真：`当前持有` 少 7，或 `累计首通`/`基线` 其中一个错了 7。
另外：今日明细 32→33→34→35 只解释 4 颗，与 `累计首通 36` 不成比例。

**影响**：`docs/PLAN_100_STONES.md` §一「当前持有 34 颗」与「还要 +66」的推算是建立在这本账上的；账本字段若错 7，则「还差多少颗」整体偏移 7。**建议先钉死口径（是「含基线」还是「不含基线」），再让 `tools/dashboard.py` 打印时顺手断言这条恒等式。**

### 1.3 账本字段/schema 不一致（证据：逐行 key 对比）

```bash
python312 -c "import json; sl=json.load(open('runtime/stone_ledger.json',encoding='utf-8'));
[print(i, list(r.keys()), repr(r.get('星')), repr(r.get('源石'))) for i,r in enumerate(sl['明细'],1)]"
```

```
1 ['时间','关卡','活动','星','轴','源石','证据']      星=3   源石=1
2 ['时间','关卡','活动','星','轴','源石','证据']      星=3   源石=1
3 ['时间','关卡','星','活动','轴','证据','方式']      星='3' 源石=None   ← 缺「源石」字段，星是字符串
明细 sum 源石 = 2.0                                                    ← 3 条明细只累加到 2
```

`axis_outcomes.jsonl` 同样存在**两代 schema**混用：

| 代 | 样本行 | 特征 |
|---|---|---|
| 旧（EA-1 / CV-EX-8） | 第 56/57/58 行 | `ts` 为**裸本地时间**（`2026-09-24T11:06:06`）或裸本地、无 `mode`/`src`/`not3` 字段 ⇒ **无法从行本身区分演习与真打** |
| 新（task-15 起） | 第 59 行起 | `ts` 带 `Z`（UTC）、含 `mode`/`src`/`evidence` |

**风险**：下游若按 `mode`/`src` 过滤（例如统计"真打次数"、"演习拦截了多少理智"），**旧 schema 的三行会被静默漏掉**。建议：补 `mode`/`src` 到 56–58 行，或在任何统计脚本里对缺字段行显式告警。

### 1.4 次要：`EA-1|普通` 的另一条轴缺证据

`axis_outcomes.jsonl:57` 记 `axis prts://48114, stars 2, evidence:"首打 2★（单干员漏 1 只，maxLifePoint=3）"`，但：

```bash
find runtime -iname '*48114*'
# runtime/plans/ea1_jobs/EA-1_48114.json
# runtime/reports/ea1_jobs/job_48114.json
# runtime/_ce6/ea1_drill_48114.attempt.log      ← 只有这一条日志
# runtime/_ce6/ea1_drill_48114_pre.png
cat runtime/_ce6/ea1_drill_48114.attempt.log
#   ✗ 闸1 拦下：这帧不像关卡详情页 … ⇒ 不起 MAA、不耗理智  |  === 完毕（无效局） ===
```

唯一一份 48114 日志是**无效局**，没有任何产物支持「2★」这个读数。该行**无法离线复核**（不属于题目要求的三条明细，但同属账本，建议补产物或标注"Lead 亲验"）。

---

## 2. task-15（演习优先：`event_sweep.mjs` + `stage_attempt.py`）

### 2.1 结论

| 检查项 | 实测 | 结论 |
|---|---|---|
| `node tools/event_sweep.mjs --selftest` | **34/34 通过**（exit 0） | ✅ 通过 |
| `python -m py_compile tools/stage_attempt.py` | OK（exit 0，无输出） | ✅ 通过 |
| `node --check tools/event_sweep.mjs` | OK | ✅ 通过 |
| `stage_attempt.py` 的 `--no-record` | 存在（L228）、L404 有对应提示文案 | ✅ |
| `stage_attempt.py` 的 `--json-out` | 存在（L230）、统一出口 `finish()`（L237） | ✅ |
| `drillDecision` / `starsFromAttemptLog` / `pickAxes` / `judgeAfterRun` | 全部存在于 `event_sweep.mjs`（L587 / L1018 区 / 轴序 & 判定段） | ✅ |

### 2.2 ⚠️ `task15_notes.md` 的「22/22」与实测「34/34」**不一致**（但**不是功能回归**）

```bash
node tools/event_sweep.mjs --selftest | tail -1
# --selftest（结算页星数判定，不连模拟器）：34/34 通过
node tools/event_sweep.mjs --selftest | grep -c '✓'
# 46
grep -n '22/22' runtime/reports/task15_notes.md
# 24:| `node tools/event_sweep.mjs --selftest` | **22/22**（含 6 条演习决策 + 3 条 attempt.log 解析） |
```

**根因已定位（读源码 L909–L1055）**：`runSelfTest()` 的 `cases` **初始数组恰好 22 条**（L910–L969，其中演习决策 10 条、结算页判定 11 条、…），汇总行打印 `cases.length`；但 task-17 之后又在运行时**动态追加 12 条**（attempt.log×3 + 回归×2 + `maxStars`×4 + `outcomeRow`×3）⇒ 汇总变成 **34/34**。`✓` 计数 46 = 34 + 12（这 12 条先内联打印一次，再在汇总循环里重打一次）。

⇒ **「22/22」是 task-15 当时的旧数字，笔记没跟着 task-17 更新**；用例只增不减、全绿，**不是回归**。但笔记同一行里「含 6 条演习决策 + 3 条 attempt.log 解析」也过期了（实际演习决策 10 条、attempt.log 断言 5 条）。**建议把笔记里所有测试计数改成"引用命令输出"而非手抄数字。**

### 2.3 ⚠️ `task15_notes.md` 内部自相矛盾

同一份文件里三处打架：

- 文首：`> 给审计/Lead 的索引；独立验证请走 auditor-1 的报告。**真机演示尚未做**（等 Lead 给窗口）。`
- 文中：`## 真机演示结果（2026-09-24，AD-EX-2）｜### 13:15 那轮：验收 1 达成 … ✓｜### 12:28 那轮：验收 2 达成 … ✓`
- 文末：`1. **真机演示（验收 1/2）没做**：需要 Lead 给窗口 + 指定关卡`

⇒ 文首/文末是没删掉的旧稿。**建议删掉这两句**（现在会让接手人误判"演习优先还没上真机"）。

**笔记里可核的部分全部为真**（逐字段比对，非抽样）：

```bash
cat runtime/reports/event_sweep_act43side_20260924_1315.jsonl
# "action":"done","result_stars":3,"page_after":"关卡详情","real_axis":"59767",
# "drill":[{"id":59767,"stars":3,"evidence":"settle"}],"real_stars":3,"stars_after":3
grep -n 'act43side_ex02_59767' runtime/axis_outcomes.jsonl | tail -2
# 05:23:44.999Z  note=drill-3star  mode=drill  evidence=settle
# 05:32:19.498Z  note=settle-3star mode=real   stone=true     ← 笔记说的两行确实存在
ls runtime/plans/drill_jobs/ | head
# job_41427.json 等 —— 笔记说的演习作业落盘目录存在
```

截图引用不一致（**不影响结论**）：笔记说 34→35 的裁图是 `runtime/_t17_lobby_top.png`，账本说是 `runtime/_lb2_top.png`；两个文件都存在，**且我都目视确认为 35**（13:32 / 13:33）。

---

## 3. task-18（`_prime_queue3.py`：AP 兜底 + 矿插队首）

### 3.1 代码存在性（grep 实证）

```bash
grep -n 'AP_UNKNOWN' runtime/_prime_queue3.py
# 43:AP_UNKNOWN = 21.0
# 208:            ap = ap_raw if ap_raw > 0 else AP_UNKNOWN
# 219:            log(f"⚠ {len(miss_ap)} 个单位**没有理智消耗**（学习产物缺字段）⇒ 按保守值 {AP_UNKNOWN} 排："
grep -n 'panel_mine' runtime/_prime_queue3.py
# 178/188/189/191（从 runtime/learned/panels_*.json + runtime/reports/map_panels.json 收集 reward_primes is True）
# 210:            tier = 0 if u["code"].upper() in panel_mine else 1
```

✅ 两处改动都**在代码里**，且 rationale 注释与审计文档一致（L173–177、L202–206）。

### 3.2 `--dry` 头部排序 vs `docs/audits/20260924_1400_task18_sched.md` 的预期

```bash
C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe runtime/_prime_queue3.py --dry > "$TMPDIR/_prime_dry.txt" 2>&1
grep '\[dry\]' "$TMPDIR/_prime_dry.txt" | head -6
```

```
[dry] CV-EX-8|普通       ap=20 state=done     ← #1
[dry] CV-EX-8|突袭       ap=20 state=todo     ← #2（tier=0，可打的第一个）
[dry] CV-1|普通          ap= 9 state=todo
[dry] CV-2|普通          ap= 9 state=todo
[dry] CV-3|普通          ap= 9 state=todo
[dry] AD-1|普通          ap= 9 state=todo
```

```
14:34:02 排产：1 个关有**预扫实证的矿**（插到最前）：['CV-EX-8']
14:34:02 [dry] 共 195 个单位；还待打 193 个；其中 noreach 会被重试 108 个（TTL=6h，--retry-noreach 可强制全部重试）
```

| 文档预期 | 实测 | 结论 |
|---|---|---|
| `CV-EX-8突` **在前** | 全量序列 index **2**（index 1 是已 `done` 的 `CV-EX-8\|普通`），可打单位里排第 1 | ✅ **一致** |
| `CV-1/2/3 → AD-1/2` 紧随 | 完全一致（#3–#7） | ✅ |
| `noreach 会重试 108 个` | `noreach 会被重试 108 个`（精确相同） | ✅ **一致** |
| 「本轮 `--dry` 无警告 ⇒ 当前没有缺 ap 的单位」 | `grep '没有理智消耗' $TMPDIR/_prime_dry.txt` → 无匹配；195 行 `ap=` 全部有值 | ✅ **一致** |

**唯一不精确处**：文档 §5 把头部写成 `CV-EX-8突 → CV-1/2/3 → AD-1/2 → CV-EX-1/2 …`，**漏掉了实际排在 AD-2 之后的 `EA-2|普通`(noreach)、`EA-1|普通`(done)**；而文档 §2 的表（rank 6–7 = `EA-2★ EA-1★`）与实际**逐项吻合**。⇒ §2 可信，§5 是简化摘要。**结论：文档与实测一致**（不影响任何决策）。

### 3.3 风险：tier 规则吃的是**过期缓存**，且面板判据官方只对「普通」有效

```bash
grep -n '面板预扫生效\|TTL' "$TMPDIR/_prime_dry.txt" | head -3
# 14:34:02   面板预扫生效（AD/main｜2.28h 前扫的，TTL 6.0h）：有矿 0 关、**直接标 done 0 关**、unknown 8 关…
grep -n '只对 `want=普通` 的单位生效' runtime/_prime_queue3.py
# 346:      · 只对 `want=普通` 的单位生效 —— 面板默认读的是普通档，拿它判突袭/磨难的矿会误判；
```

- 把 `CV-EX-8` 提到队首的 `reward_primes=True` 来自 **2.28 小时前**（≈12:20）的一次 AD 主区扫描，**早于 13:32 那次通关**，即它是**通关前的旧读数**。
- `tier` 只按 `u["code"]` 判，因此**「普通」和「突袭」会被同时提到 tier 0**，而第 346 行的纪律明确说面板判据只对普通档成立。
- 文档自己给的安全网（真打前重读徽章，无徽章⇒⊘）**在代码里确实存在**（`apply_panel_prescan` + 闸门），所以最坏是"多一次导航"，不会白花理智。

**建议**：给 `panel_mine` 加"扫描时间 + 只提升普通档"两个约束，或至少在日志里打出该缓存的时间戳（现在只打 `2.28h 前扫的`，容易被读成"当前实测"）。

---

## 4. noreach 6h TTL 是否真在代码里生效（题目 4）

### 4.1 代码路径：✅ 生效

```bash
sed -n '31,38p;240,275p' runtime/_prime_queue3.py
grep -n 'NOREACH_TTL_H\|noreach_expired\|mark_noreach\|clear_noreach' runtime/_prime_queue3.py
```

- `NOREACH_TTL_H = 6.0`（L38），账本文件 `runtime/_prime_noreach.json`（L37）
- `mark_noreach()` L240–248（写 `{at, n, reason}`）、`noreach_expired()` L251–266（`force`→True；**无记录→True**；解析失败→True；否则 `now - at > 6h`）、`clear_noreach()` L269–273
- 真跑路径 L781–784：`if st.get(key)=="noreach": if not noreach_expired(...): continue`
- 干跑统计 L760 / 汇总 L763–764
- 三处"标 noreach"的入口都改成了带冷却（L799/L846/L889），且注释更新了旧行为

### 4.2 ⚠️ 但冷却账本**一条记录都没有**⇒「108 个重试」靠的是兜底分支，不是 TTL 计时

```bash
ls -la runtime/_prime_noreach.json
# ls: cannot access 'runtime/_prime_noreach.json': No such file or directory
python312 -c "
import json;from collections import Counter
st=json.load(open('runtime/_prime_state3.json',encoding='utf-8'))
print(len(st), Counter(st.values()))"
# 195 Counter({'noreach': 108, 'todo': 85, 'done': 2})
```

- 状态表 195 条中 **108 条 noreach**，与 `--dry` 的"会被重试 108 个"**精确对上**。
- 但 108 条**全部**命中 `noreach_expired()` 的 **"没有记录 ⇒ True"** 分支 ⇒ 这是**迁移兜底**，**不是**"6 小时到了"。
- `mark_noreach()`（唯一会写 `_prime_noreach.json` 的地方）只在**真跑**里被调用，而自本次改动上线后**没有跑过真跑** ⇒ 该文件的创建路径 + `now - at > 6h` 这条计时分支 + `clear_noreach()`，**在运行时证据上从未被触发过**。

**结论**：TTL 逻辑**代码里确实生效**（可静态确证），但**"6h 后自动重试"这个时间语义没有离线可得的运行时证据**。
**建议**：做一次最小真机/或单元级验证（临时构造一条 `{"at": 7h 前}` 的记录跑 `noreach_expired()` 断言 True、`{"at": 现在}` 断言 False），否则这条"自动重试"一直只是纸面承诺。
另外：代码 L31–32 注释仍写「195 个单位里有 **85** 个是 noreach」，**实测是 108 个 noreach / 85 个 todo**——数字串位了，建议修注释。

### 4.3 🔴 审计进行中现场抓到：**队列真实运行正在全量打不上卡**（14:36–14:38）

> ⚠️ 这不是我跑的。我在 14:34 只跑了 `--dry`；14:36 起宿主上有**另一个进程**启动了真实的 `_prime_queue3.py`。以下全部是**只读**观察。

```bash
tail -30 runtime/_prime_queue3.log
grep -c '导航失败 ⇒ 不重进，标 noreach' runtime/_prime_queue3.log   # 4
cat runtime/reports/event_sweep_act28side_20260924_1437.jsonl
```

**现象**：每一关的导航都死在同一个 import 上 ⇒ 队列把一关接一关标成 `noreach`：

```
14:36:49   [CV-EX-8|突袭] 导航失败 ⇒ 不重进，标 noreach（冷却 6h）换下一关：
14:37:10   [CV-1|普通]    导航失败 ⇒ 不重进，标 noreach（冷却 6h）换下一关：
14:37:31   [CV-2|普通]    导航失败 ⇒ 不重进，标 noreach（冷却 6h）换下一关：
14:37:52   [CV-3|普通]    导航失败 ⇒ 不重进，标 noreach（冷却 6h）换下一关：
14:38:05   enter_by_template: to_book 失败： No module named 'cv2'
14:38:05   （为戏剧，为永恒 进不去，第 2 次）
```

`runtime/reports/event_sweep_act28side_20260924_1437.jsonl` 的 verdict 字段把根因写得很清楚：

```json
"verdict":"mt_nav 无输出：Traceback … File \"D:\\1-2\\MAAOrch\\tools\\mt_nav.py\", line 28, in <module>
            import cv2 / ModuleNotFoundError: No module named 'cv2' ｜断在=nav｜当时页面=未知（分 0.535）"
```

**根因（代码级可证）**：`_prime_queue3.py` 启动所有子工具都用**裸 `python`**，子进程继承调用方的 PATH：

```bash
grep -n '"python"' runtime/_prime_queue3.py
# 314:"python" tools/scan_map_panels.py | 449:"python" tools/main_nav.py
# 541:discover_stages.py | 559: tools/enter_by_template.py | 574: tools/enter_event.py | 819: tools/stage_attempt.py
grep -n 'import cv2' tools/mt_nav.py      # 28:import cv2
C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe -c "import cv2"   # 静默通过（有 cv2）
python -c "import cv2"                                                              # ModuleNotFoundError（3.11.16，无 cv2）
```

⇒ **哪个 `python` 在 PATH 最前，决定了队列能不能导航。** 从 Hermes/agent 环境启动（PATH 命中 3.11 venv，无 cv2）时，**195 个单位全部只能"导航失败 ⇒ 标 noreach"**。仓库自带 `requirements.txt` 但 `_prime_queue3.py` 不做 cv2 预检，失败被降级成一句"导航失败"，**根因被埋在 521KB 日志里**。

**第二个疑点（需人工确认，我只报事实）**：日志显示 4 次 `mark_noreach`，但冷却账本与状态表**都没落盘**：

```bash
cat runtime/_prime_noreach.json      # {}            ← 2 字节，14:38 创建，0 条记录
python312 -c "import json;from collections import Counter;[print(len(st),Counter(st.values())) ...]"
# 195 Counter({'noreach': 108, 'todo': 85, 'done': 2})   ← 与 14:34 完全一致，CV-1/2/3 仍是 todo
```

`mark_noreach()`（L240–248）是**逐事件立即 `write_text`** 的，理应有 4 条 `{key:{at,n,reason}}`。现在文件是 `{}`、状态表计数未变。
**两种可能，离线无法区分**：① 那次真实运行被中途掐掉/回滚；② `mark_noreach` 的落盘路径有问题（若是②，则 §4.2 结论升级为**功能缺陷**，且 108+ 个单位会**每轮都重试一遍**、而不是「6h 后重试一次」）。

**风险与建议（按优先级）**：

1. **立刻给 `_prime_queue3.py` 加启动自检**：`subprocess.run([sys.executable, "-c", "import cv2, rapidocr_onnxruntime"])`，失败就**拒绝启动**（现在是把"环境坏了"翻译成"这一关没矿"，会污染状态表 —— 一旦持久化成功，CV 全章会被错误地锁 6 小时）。
2. **所有子进程改用 `sys.executable`**（或显式 `PY` 常量），别用裸 `python`。
3. 核对 `mark_noreach` 的落盘（造一条记录看文件是否出现），并把丢失写状态当成错误上报而不是静默继续。
4. `noreach` 与"导航环境坏"必须区分：**导航失败是基础设施问题，不该消耗掉这关的重试额度**。

---

## 5. 四步验证（README 末尾口径）——离线可做的部分

`README.md` 末尾《改 Python 的四步验证》：① `ast.parse` ② **函数清单对比** ③ 真 `import` ④ 最小 smoke test。抽查两份最近改动的 .py（`tools/stage_attempt.py` 13:11、`runtime/_prime_queue3.py` 13:27）。脚本：`C:/Users/xiach/AppData/Local/hermes/cache/scratch/fourstep_check.py`

| 步骤 | `tools/stage_attempt.py` | `runtime/_prime_queue3.py` |
|---|---|---|
| ① `ast.parse` | **OK** | **OK** |
| ② 函数清单 | 顶层 10 个：`sh, shot, tap_abandon, _ocr_hits, _ocr_text, in_battle, is_detail_page, is_raid_page, read_life, main`；**无重名/无重复定义**；类 0 | 顶层 28 个（`log … main`）；**无重名/无重复定义**；类 0；`AP_UNKNOWN`、`NOREACH_TTL_H` 均可取到 |
| ③ 真 `import` | **OK**（10 个函数全部可达，无副作用） | **OK**（28 个函数全部可达；`AP_UNKNOWN=21.0`、`NOREACH_TTL_H=6.0` 从模块取到） |
| ④ 最小 smoke | ⛔ **未做**（需模拟器/停在详情页） | ✅ 间接做了：`--dry` 全流程跑通（排序+tier+警告+计数），exit 0 |

**第 ② 步的口径说明（重要）**：README 要求的是"**替换前后**函数清单对比"，但 `runtime/` 整个目录在 git 里是 `??`（未跟踪），`tools/stage_attempt.py` / `tools/event_sweep.mjs` 同样未跟踪：

```bash
git ls-files tools/stage_attempt.py tools/event_sweep.mjs runtime/_prime_queue3.py
# （空输出 = 三者都不在版本控制里）
```

⇒ **没有改前基线可比**，"相邻函数被整段替换吃掉"这类静默事故**离线无法证伪**。我做的是**结构体检**（无重名、函数数合理、可 import），**不等于**README 说的第 2 步。
**建议**：把 `tools/*.py` 至少 `git add -N` 或纳入版本控制，否则这四步里最值钱的第 2 步永远做不成。

---

## 6. 我做了但未改动的现场副作用 / 无法离线验证的点

**副作用（已还原）**：题目要求跑 `python -m py_compile tools/stage_attempt.py`，它生成了 `tools/__pycache__/stage_attempt.cpython-312.pyc`（14:33）。审计结束已 `rm` 删除，`tools/` 与 `runtime/` 下现有文件**零改动**。（`tools/__pycache__/mt_nav.cpython-311.pyc`、`enter_event.cpython-311.pyc` 于 14:36 出现，是**宿主上其它进程**写的，不是本次审计。）

**无法离线验证**：

1. `--drill --raid` 组合能否跑（`task15_notes.md` 自陈"没实测过"）——需模拟器。
2. `--drill-strict` 默认关是否合理——是**策略取舍**，不是事实问题。
3. `stage_attempt.py` 的**第 4 步 smoke test**（真起一局）——需模拟器，本次不跑。
4. **第 2 步"函数清单前后对比"**——无改前基线（见 §5），抽查只能做到结构体检。
5. `noreach` 6h TTL 的**计时分支**与 `mark_noreach()/clear_noreach()` 的落盘——`_prime_noreach.json` 在 14:38 由**别的进程**创建，内容为 `{}`（0 条记录），即使日志显示 4 次 `mark_noreach`（见 §4.3）。**"写了却没落盘"是掐进程还是代码缺陷，离线无法区分。**
6. 账本 §1.2 的 **7 颗差**到底该改哪个字段——取决于团队意图（"含基线" vs "不含基线"），离线只能证明**不自洽**，不能判定谁错。
7. `EA-1|普通`/`prts://48114` 那行 `stars=2` 的读数（§1.4）——无任何产物。
8. `axis_outcomes.jsonl:56/57/58` 三行是否为人工写入（无 `mode`/`src`、`ts` 格式与后续行不同）——只能指出 schema 差异，不能断定写入者。
9. **CV-EX-8 的两处互相打架的读数**：`event_sweep_act28side_20260924_0736.jsonl`（07:36）判 `CV-EX-8|普通 = 报酬无源石（reward_score 0.638；0 星）⇒ 不打`，而同日 11:41 该关 **clear/3★** 拿到源石。两者都在产物里，但"07:36 是不是一次假阴性（检测器把有源石读成 0.638）"**必须回看当时的屏幕才能定**——离线只能记录这个矛盾，不能归因。
10. 顶栏读数（33/34/35）我做了**目视复核**（`_lobby_top.png`=33@11:04、`_lobby4.png`=34@11:42、`_lb2_top.png`=35@13:33、`_t17_lobby_top.png`=35@13:32），但**「35 是 EA/AD 那几颗的净和」这条因果链**离线无法证明（游戏内没有可查的账目）。

---

## 7. 一句话汇总

| 题目 | 结论 |
|---|---|
| 1 账本自洽（三条明细） | ✅ 三条**全部有证据行**，AD-EX-2 端到端闭环（含顶栏 35 目视复验）；⚠️ 但**账本自身算术差 7 颗**（7+36−1=42≠35）、明细/账本两代 schema 混用、`48114` 那行 2★ 无产物 |
| 2 task-15 | ✅ 改动都在、`--selftest` 34/34、`py_compile` OK；⚠️ `task15_notes.md` 的「22/22」是 task-17 前的旧数字（**非回归**），且该笔记**文首/文末与正文自相矛盾**（说"真机演示没做"，正文却写"验收 1/2 达成"） |
| 3 task-18 | ✅ `AP_UNKNOWN=21` 与 tier 规则都在代码里；`--dry` 头部 `CV-EX-8突` 在前、`noreach 重试 108 个`、`无 ap 警告` **与审计文档三项全中**；风险：tier 吃 2.28h 前的旧缓存且同时提升突袭档（靠真打前重读徽章兜底） |
| 4 noreach 6h TTL | ✅ 代码里确实生效（L38/240–273/781–784）；⚠️ 但 `runtime/_prime_noreach.json` 直到 14:38 才被别的进程创建、内容 `{}` ⇒「108 个重试」走的是"无记录⇒过期"兜底，**计时分支零运行时证据**；🔴 **且现场抓到队列真实运行正在全量导航失败**（`cv2` 缺失 ⇒ 逐关标 noreach），建议先修环境再谈 TTL |
| 5 四步验证 | ✅ ①`ast.parse` ②结构体检（无重名）③真 `import` 全过（两份文件）；⛔ ④smoke 未做（需模拟器）；⚠️ README 的第 ② 步（前后对比）**因文件未纳入 git 而无法执行** |
