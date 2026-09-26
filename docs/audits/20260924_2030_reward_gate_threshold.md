# 报酬栏（⊘）门槛：删 0.46/0.64 → 配置单一来源 + 一致性判据 + 可审计四件套 + 查询工具（含 partial 节）

2026-09-24 20:30 ｜ prts(Hermes) ｜ 只动 `tools/event_sweep.mjs`、`tools/panel_gate.py`（**只加"从配置读阈值"**，ROI/算法未动）；新建 `runtime/config/reward_gate.json`、`tools/pending_reverify.py`、`runtime/reports/` 产物、本文。`.bak`：`tools/event_sweep.mjs.bak`(20:06)、`tools/panel_gate.py.bak`(10:25)。上一版：`20260924_2022_reward_gate_threshold.md`。
## 1. 改前 / 改后 / 来源
- **改前**：`event_sweep.mjs:397-398` 写死 `REWARD_AMBIG_LO=0.46 / HI=0.64` ⇒ 0.46–0.64 **一律降级 `unreliable`**（把确信的「无矿」当「读数不可信」⇒ ⊘ 永远无法终结、队列把已领关无限重走）。
- **改后**：阈值**单一来源** `runtime/config/reward_gate.json`（`present 0.75 / absent 0.72`）：`≥0.75`⇒有矿、`≤0.72`⇒**无矿**、`(0.72,0.75)`／缺失／非数／负数哨兵⇒`ambiguous`（不判终态）。
- **来源**（写进配置 `source`）：`tools/panel_gate.py:28` 原文「reward_score 模板最高分（0.999=徽章在；≤0.72=不在。阈值 0.75）」＋ Lead 实测全库 `no_prime n=70 max=0.702`（中位 0.472）／`done n=20 min=0.950` ⇒ **(0.702,0.950] 完全无重叠**。
- **为什么不用 0.8**：原文只给 0.72/0.75，实测两组间隔 (0.702,0.950] 宽 0.248、`0.702` 与 `0.950` 之间**没有任何观测** ⇒ 没有理由抬高。0.8 只在 `(0.75,0.8]` 凭空造一条 ambiguous 带，把本可判定的「有矿」重新打成"说不准"＝无依据地让 ⊘ 重新不可终结（同类错误再犯一次）。
## 2. 落点行号（四件事 + 两个追加）
1. **阈值单一来源**：`event_sweep.mjs:63 loadRewardGate`→`:79`；`panel_gate.py:88 _load_reward_gate`→`:102`（读不到**回退** 0.75/0.72 ＋ stderr 警告）；一致性检查 `panel_gate.py:105`（config `present` 必须 = 算法真用的 `main_nav.BADGE_THRESHOLD`，实测同为 0.75）；新读数字段 `panel_gate.py:123,226-227`。
2. **判决统一（一处函数）**：`event_sweep.mjs:443 rewardVerdict()`（唯一实现）＋`:453 badgeStillThere()`（三态）；⊘ 处 `:2227-2240`，结算复核 `:1269`、`:1361`。0.46/0.64 已删（只在注释里说明删了什么）。
3. **⊘ 终态一致性判据**：`event_sweep.mjs:496 rewardNoPrimeGate(score, disc, {stars})` —— `contradiction = absent && stars 是整数 && stars<3` ⇒ **不判终态、不写 noreach**，落 `reward_contradiction`（`:2253-2257,2273`），`stars/reward_score/reward_primes` 全落行。
4. **可审计四件套**：`event_sweep.mjs:468 rewardAuditFields()`（`reward_score`＋`reward_primes` 三态 true/false/**null**＋`page`/`page_guard`＋`stars`＋`reward_verdict`），⊘ 分支一进来就落 `:2209`（守卫失败的行也齐全 `:2210-2218`）；**写入契约＝键必须在场**（缺字段写 null，不是省键），只要求**从现在起的新行**。
5. **守卫判据加时间范围**（Lead/审计员逐单位对撞出的缺陷）：`page-guard-missing` **只对 `ts >= 2026-09-24T12:15` 的行判**（`PAGE_GUARD_CONTRACT_FROM:58`、`:145`）；`ts` 读不到/说不清按历史行处理（`ts_key:79`）。原实现按**行并集**算单位 ⇒ 只要一个单位有过任何一行旧行就永远留在清单里、**重验也清不掉** ⇒ 清单每轮自己长大、判决永远到不了终态（＝0.46/0.64 同类错误）。判定逻辑其余部分**一律未动**。
6. **查询工具**：`tools/pending_reverify.py`（`guard_required:102`/`audit_reasons:110`/`scan:158`/`write_atomic:214`）——键「在场 vs 显式 null」分开记、缺字段**不崩**（畸形行计数跳过）、行数与**单位数**两套都印、旧手工清单原文存 `legacy_manual_snapshot`。
7. **`partial` 只读一节**（Lead steer）：`scan:186-205`、`build_report:290-297`、回显 `main` 末尾 —— `action=="partial"` = **徽章在（有矿）但没三星 ⇒ 源石未发，属于还有确定收益的目标**，理由写进行内 `why`；**单独计数、绝不与 ⊘ 的待重验数混算**（重试路径本来就会照打它：选关只跳过 `done`）。
## 3. 验收原始输出
```
$ python tools/pending_reverify.py --selftest     # exit=0
{"selftest": {"ok": true, "caught": ["A-1|普通","A-2|普通","A-3|普通","A-4|普通","A-5|普通"], "want": [同上], "clean_caught": [], "edge_absent_not_caught": true,
 "junk_lines_handled": 3, "key_vs_null_distinguished": true, "guard_scope_window": "2026-09-24T12:15", "self_clearing": true, "new_row_missing_guard_flagged": true,
 "partial_counted_separately": true, "present": 0.75, "absent": 0.72}}
$ node tools/event_sweep.mjs --selftest            # exit=0；改前 112，上一轮 133，现仍 133/133
  ✓ P4 查询工具：reward_primes=None / 分数缺失 / 分数 > absent / stars<3 ⇒ 全部被抓（干净行与 score=absent 的边界不抓）
--selftest（结算页星数判定，不连模拟器）：133/133 通过
断言：0.702⇒**终态 no_prime**｜0.73⇒unreliable｜stars=0+0.638⇒unreliable(矛盾)｜分数缺失/-1⇒ambiguous｜0.999⇒不判 ⊘｜reward_primes=None⇒明写 null 且子进程真跑 pending_reverify.py --selftest 抓到 A-1..A-5
$ node tools/event_sweep.mjs --settle-replay runtime/fixtures/settle/clear_raid_firstdrop_CV-EX-8.png
  ✓ 突袭通关（结算页奖励栏【首次掉落×1】）⇒ 首通源石到手；星区读数 0★ 是正常读数（**不判 failed**）
  ⚖️ 判据：clear=true｜why=raid-first-drop｜首次掉落=true｜理智返还=false｜结算页星数=0｜📒 账本行：{"stars":null,"note":"raid-first-drop","stone":true,"settle_clear":true,...}   # 仍 done+stone
$ python tools/pending_reverify.py
== ⊘ 待重验（查询，不是快照）｜扫 234 份账本 / 385 行｜⊘ 共 76 行 ⇒ **待重验 48 行 / 36 单位**
   按活动（行）：{"MT":22,"LE":9,"CV":8,"PV":6,"AD":2,"EA":1}｜按原因（行）：{"reward_primes-key-missing":42, "reward_primes-key-missing+stars=0<3-contradiction":5, "page-guard-missing":1}   # 时间范围生效：旧行的守卫缺失已不再入列
   键在场/缺键：primes 键在场 1 / 缺键 47｜page_guard 有 0 / 缺 48｜⊘?（unreliable）：8 行 / 4 单位：["PV-1|普通","AD-6|普通","AD-7|普通","AD-8|普通"]
   守卫判据时间范围：只对 ts >= 2026-09-24T12:15 的行判 page-guard-missing（范围内 6 行；ts 说不清按历史行处理 0 行）⇒ **清单能自清空**，不会因历史行永远长大
== ⚔ 待打（partial）：7 行 / **4 单位**｜按活动（行）：{"PV":5,"EP":1,"AD":1}（**与上面 ⊘ 的计数分开，不许混算**）
   单位（行数）：PV-EX-8|普通×3、PV-EX-3|普通×2、EP-EX-4|普通×1、AD-EX-3|普通×1
     AD-EX-3|普通  score=0.998  primes=True  stars=0  guard=（缺）  ts=2026-09-24T11:47:12.429Z  event_sweep_act43side_20260924_1947.jsonl
```
- **对撞一致性 + 自清空证据**：审计员独立实现（同刻 76 行 ⊘）我 **36 单位 / 48 行** vs 他 **36 单位 / 47 行** ⇒ 单位差 **0**；行差 1 是他的判据不含守卫项，而多出的 1 行正是**新契约违约**：`PV-2|普通 ts=2026-09-24T12:15:16Z`（契约生效后 16 秒写的行却**没有** `page_guard`/`page`）⇒ 该抓且一眼可查。**自清空**（selftest 边界③，`pending_reverify.py:376-388`）：同单位「历史行缺 `page_guard` + 新合规行」⇒ **不在**清单里（`self_clearing:true`）；同单位「新行也缺 `page_guard`」⇒ 仍在（`new_row_missing_guard_flagged:true`）。
- **只动了声明的文件**（`ls -l`）：`event_sweep.mjs`(20:24) / `.bak`(20:06) / `panel_gate.py`(20:22) / `.bak`(10:25) / `reward_gate.json`(20:22,新) / `pending_reverify.py`(20:30,新) / `reports/pending_reverify.json`(20:30,新) / 本文。`git status` 对禁改清单（`tools/stage_attempt.py`、`runtime/_prime_queue3.py`、`runtime/plans/_event_axes_raid2.py`、`runtime/plans/_story_axes_big2.py`、`runtime/stone_ledger.json`、`runtime/_prime_state3.json`）**无本任务改动**（其中 `stone_ledger.json`/`_prime_state3.json` 20:30 的 mtime 是 live 跑批自己写的，非我）；未碰模拟器/adb/MuMuManager/MAA/8765，全程离线（`--selftest`/`--settle-replay`）。
