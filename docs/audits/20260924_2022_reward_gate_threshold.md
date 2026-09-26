# 报酬栏（⊘）门槛：删 0.46/0.64 → 配置单一来源 + 一致性判据 + 可审计四件套 + 查询工具

2026-09-24 20:2x ｜ prts(Hermes) ｜ 只动 `tools/event_sweep.mjs`、`tools/panel_gate.py`（只加"从配置读阈值"，ROI/算法未动）、新建 `runtime/config/reward_gate.json`、`tools/pending_reverify.py`、`runtime/reports/_stars11_fix_apply.py`（一次性卫生脚本）与 `runtime/reports/` 产物；`.bak`：`tools/event_sweep.mjs.bak`、`tools/panel_gate.py.bak`。
## 1. 改前 / 改后 / 来源
- **改前**：`event_sweep.mjs:397-398` 写死 `REWARD_AMBIG_LO=0.46 / HI=0.64`，**0.46–0.64 一律降级 `unreliable`**（把确信的「无矿」当「读数不可信」⇒ ⊘ 永远无法终结、队列把已领关无限重走）。
- **改后**：阈值**单一来源** `runtime/config/reward_gate.json`（`present 0.75 / absent 0.72`）；`≥0.75`⇒有矿、`≤0.72`⇒**无矿**、`(0.72,0.75)`/缺失/非数/负数⇒`ambiguous`。
- **来源**（写进配置 `source`）：`tools/panel_gate.py:28` 原文「模板最高分 0.999=徽章在；≤0.72=不在；阈值 0.75」＋ Lead 实测全库 `no_prime n=70 max=0.702`（中位 0.472）／`done n=20 min=0.950` ⇒ **(0.702,0.950] 完全无重叠**。
- **为什么不用 0.8**：原文只给 0.72/0.75，实测间隔宽 0.248 不需要抬高；0.8 只会在 (0.75,0.8] 凭空造一条 ambiguous 带，把本可判定的读数又打成"说不准"＝无依据地让 ⊘ 重新不可终结。
## 2. 落点行号（四件事 + 两个追加）
1. **阈值单一来源**：`event_sweep.mjs:63 loadRewardGate`→`:79`；`panel_gate.py:88 _load_reward_gate`→`:102`（读不到回退 0.75/0.72 + stderr 警告）；一致性检查 `panel_gate.py:105`（config `present` 必须 = 算法真用的 `main_nav.BADGE_THRESHOLD`，实测同为 0.75）；新读数字段 `panel_gate.py:123,226-227`。
2. **判决统一**：`event_sweep.mjs:443 rewardVerdict()`（唯一实现）＋`:453 badgeStillThere()`（三态）；⊘ 处 `:2227-2240`，结算复核 `:1269`、`:1361`。0.46/0.64 已删（只在注释里说明删了什么）。
3. **⊘ 终态一致性判据**：`:496 rewardNoPrimeGate(score, disc, {stars})` —— `contradiction = absent && stars 是整数 && stars<3` ⇒ 不判终态，落 `reward_contradiction`（`:2253-2257,2273`）。
4. **可审计四件套**：`:468 rewardAuditFields()`（`reward_score`＋`reward_primes` 三态 true/false/**null**＋`page`/`page_guard`＋`stars`＋`reward_verdict`），⊘ 分支一进来就落 `:2209`（守卫失败的行也齐全 `:2210-2218`）；**写入契约＝键必须在场**（缺字段写 null，不是省键），只要求**新行**。
5. **P5 写行策略**：`:787 fallbackRowPlan()`＋`:798 runFallback(..., ctx)`；`ctx.chainBroken`（`:2364` 传）⇒ 整批中性 `chain-broken`（带 `chain_broken/drill_broken/drill_why`），否则整批 `no-conclusion`；**逐份判"最后试的那一份"已删** ⇒ 不再产生 `fallback-tried`(−6)；写行策略落账 `:2375`。
6. **查询工具**：`tools/pending_reverify.py`（`audit_reasons:64`/`scan:104`/`write_atomic:194`）——键「在场 vs 显式 null」分开记、缺 `page_guard` 也入列、缺字段**不崩**（畸形行计数跳过）、行数与**单位数**两套都印；历史行 `reward_primes` 有键仅 27、`page_guard` 仅新行有 ⇒ 清单自然变大（71 行 / **50 单位**），`write_contract.applies_to` 写明"只要求从现在起的新行"。**卫生项③**：`_stars11_fix_apply.py` 把 7 行 `stars=11` 改成 `stars:null`+`stars_raw:11`（每份账本先 `.bak-stars11-<ts>`；逐行仅这 3 键变化，全库 `stars>3` 归零）。
## 3. 验收原始输出
```
$ node tools/event_sweep.mjs --selftest        # 改前 106/112；现 133/133，exit=0
--selftest（结算页星数判定，不连模拟器）：133/133 通过
断言：0.702⇒terminal no_prime｜0.73⇒unreliable｜stars=0+0.638⇒unreliable(contradiction)｜-1/缺失⇒ambiguous｜reward_primes=None⇒明写 null 且子进程真跑 pending_reverify.py --selftest 抓到 A-1..A-5（含整行缺字段）｜P5：drill_broken=true⇒整批 chain-broken（无 fallback-tried）｜同批两份轴判据/权重一致｜全写/全不写

$ node tools/event_sweep.mjs --settle-replay runtime/fixtures/settle/clear_raid_firstdrop_CV-EX-8.png
  ✓ 突袭通关（结算页奖励栏【首次掉落×1】）⇒ 首通源石到手；星区读数 0★ 是正常读数（不判 failed）
  ⚖️ clear=true｜why=raid-first-drop｜首次掉落=true｜理智返还=false ⇒ 账本 {"stone":true,"settle_clear":true}（仍 done+stone）

$ python tools/pending_reverify.py
== ⊘ 待重验（查询，不是快照）｜扫 232 份账本 / 379 行｜⊘ 共 76 行 ⇒ **待重验 71 行 / 50 单位**（引用一律报单位数）
   按活动（行）：{"CV": 23, "MT": 22, "AD": 9, "LE": 9, "PV": 7, "EA": 1}
   按原因（行）：{"primes-key-missing+page-guard-missing": 42, "page-guard-missing": 24, "primes-key-missing+stars=0<3-contradiction+page-guard-missing": 5}
   键在场/缺键：primes 键在场 24 / 缺键 47｜page_guard 有 0 / 缺 71
   ⊘?（unreliable）现状：8 行 / **4 单位**：["PV-1|普通","AD-6|普通","AD-7|普通","AD-8|普通"]
   单位（行数）前 10：MT-EX-1|突袭×4、MT-EX-4|突袭×4、AD-EX-2|普通×3、CV-EX-1..7|普通×2…
   前 10 行：AD-1|普通 0.464 primes=False stars=3 guard=（缺）｜AD-EX-2|普通 0.662 primes=键缺失 stars=0（矛盾）｜CV-EX-1|普通 0.634 primes=键缺失 stars=None（已按③挪 stars_raw）…
   产物 runtime/reports/pending_reverify.json（原子写 tmp→os.replace；旧手工清单原文存 legacy_manual_snapshot）
```
**线上实证**（不停机）：`LE-1|普通 12:17:46Z` 已带 `page_guard:"ok" / reward_verdict:"absent" / reward_contradiction:false`，判决串「分 0.462 ≤ absent 0.72 … stars≥3 ⇒ 与"无矿"自洽 ⇒ 不打」；20:22:38 队列日志已是 `⊘ 报酬无源石（分 0.591 ≤ absent 0.72…）`。历史行**不清账本、不整段回滚**，一律由查询工具逐条列出。
