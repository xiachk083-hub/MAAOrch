# 2026-09-24 20:06 结算页误判修复（真通关被读成 0★/理智返还 ⇒ failed + 降权赢家 + 漏登源石）

**结论**：判据改为「**冗余证据优先于像素星数；failed 必须有正面证据；证据不足不许拍板**」。`--selftest` **112/112 通过**（改前 91/91，退出码 0）；赢家轴 `prts://41427` **权重 −6.0 → 0.0**。
## 1. 改前为何错（改前行号，全在 `tools/event_sweep.mjs`）
| 位置 | 旧写法 | 伤害 |
|---|---|---|
| `verdictFromStars` ⑥（原 919 行注释 / 1014 行调用） | `0★ ⇒ failed`（只看星区像素**蓝分量**） | 突袭结算页星区只有**一颗亮白六边形勋章**（不是三颗星）⇒ 蓝像素 0/0/0 ⇒ 读成 0★ ⇒ 误判 failed（1900 那张帧就是它） |
| ④徽章否决（原 1014 行 `if (badgeRemains) return out('partial'…)`） | 排在首通证据 ⑤ **之前**，且只看徽章 | 真故障记录 `event_sweep_act28side_20260924_1900.jsonl` 里 `reward_primes:true` ⇒ **正面证据被吞 + `not3:true` 降权 + 不发源石**（Lead 复核 `runtime/_audit_settle_mirror_20260924.md` §2 成立） |
| `recordOutcome`（原 611 / 1032 行） | 最后一次试的轴预写 `fallback-tried`（stars=null） | `_story_axes_big2.py:162`、`_event_axes_raid2.py:191` 见到即 **−6 降权** ⇒ 赢的那份轴被降权 |

另：`⊘ 报酬无源石` 只写「分 X；N 星、徽章已消失」，不记判别器（审计 §3：14 条 ⊘ 里 **10 条落歧义带 0.46–0.64**，与「徽章 ROI 假阴性」同分）⇒ 事后无法落定。
## 2. 改后判据（`settleVerdict` 纯函数；判据+置信度写进 `evidence` 落账，不静默拍板）
① 屏上没结算页 ⇒ 不判通/败；② **唯一 failed 通道**＝「理智返还」且无「首次掉落」且非演习（**正面证据**）；③ 演习帧不判胜负；
④′ 徽章仍在**且无首通文案** ⇒ partial（保留 `not3`/降权）；④″ **徽章仍在 + 首次掉落＝冲突 ⇒ 以游戏印的文案为准** ⇒ `done+stone`，落 `conflict=badge-remains-vs-firstdrop`（徽章 ROI 有假阴性前科：CV-EX-1..8 旧 ⊘ 已被回滚）；
⑤ 首次掉落 ⇒ 已通关：≥3★ done+源石、1~2★ partial、**星数读不出 ⇒ partial「已通关·星数未知」，绝不 failed**、突袭 ⇒ done+源石；
⑥ 0★/读不到且无理智返还 ⇒ **证据不足**（`clear=null`、不降权）。⊘ 另加 `rewardNoPrimeGate`：分数落 0.46–0.64 **或**判别器缺 ⇒ `⊘?（待判别）`+`needs_discriminator`，**不进终态、不进 noreach**。
## 3. 夹具（`runtime/fixtures/settle/` 9 张冻结副本 + `fixtures.json`；真值肉眼核对、信号离线实测）
| 夹具 | 肉眼真值 | 期望 |
|---|---|---|
| `clear_raid_firstdrop_CV-EX-8.png`（=1900 那张） | 真通关：亮白六边形勋章 + 「首次掉落×1」 | `done`/`raid-first-drop`/stone |
| `clear_3star_firstdrop_{AD-EX-2,EA-1}.png` | 3★ 首通 | `done`/stone |
| `fail_refund_{MT-EX-8,15-20}.png` | 真失败：三灰六边形 + 「理智返还」 | `failed`/`sanity-refund` |
| `other_{operator_page,drill_battle,storyline_book,battle_pause}.png` | **不是结算页**（干员页/演习 HUD/曲谱/PAUSE） | 页面守卫拦下、一个读数都不采 |

关键形状：`settle_blue=[0,0,0] + first_drop=true`（旧代码判 failed 的那种，现在必判 clear）。
## 4. selftest（离线：`node tools/event_sweep.mjs --selftest`，全量日志 `runtime/reports/settle_verdict_selftest.log`）
`--selftest（结算页星数判定，不连模拟器）：112/112 通过`（无 ✗、退出码 0）｜关键项：`✓【探针A】首次掉落=有+徽章仍在=有 ⇒ done+源石`、`✓【探针B】突袭(1900 形状) ⇒ done+源石、不降权`、`✓ 接线 P1：④′/④″ 排在 ⑤ 之前`、`✓ 接线 P3：终态 no_prime 只在 if (ng.terminal) 里`、`✓ ⊘ 闸：0.55/判别器缺 ⇒ 不判终态`、`✓ 账本 opers：null≠[] 三态`。
## 5. 真机再跑一次的日志对照（`--settle-replay` 用**同一批判据函数**离线产出，非手写）
改前：[19:10:31] `👁 结算页已确认（页面分 0.895）｜星数读数：0★（六边形亮蓝像素 0/0/0）` → [19:10:58] `✗ 结算页 0★（三个六边形全灰＝理智返还）⇒ 这局白跑，记 failed 并降权该轴` → `未通：CV-EX-8突袭(…)`，账本 `note=fallback-tried`（−6）
改后：`👁 结算页已确认（页面分 0.895）｜星数读数：0★（六边形亮蓝像素 0/0/0，帧存 …）` / `🧾 奖励栏证据：首次掉落=有｜理智返还=无` / `✓ 突袭通关（结算页奖励栏【首次掉落×1】）⇒ 首通源石到手；突袭没有星数评价，星区读数 0★ 是正常读数（不判 failed）` / `⚖️ 判据：clear=true｜why=raid-first-drop`
账本行：`{"id":41427,"stars":null,"note":"raid-first-drop","stone":true,"settle_clear":true,"settle_why":"raid-first-drop"}`（`stars:null`＝不骗 3★、`stone:true`＝源石入账、**无 `not3`**＝不降权）
## 6. 恢复被误降权的轴（`runtime/axis_outcomes.jsonl`，先存 `.bak-settle-20260924_1959`；脚本 `runtime/_audit_restore_41427.py`，复跑幂等）
`prts://41427`：19:10 那行 `note: fallback-tried → raid-first-drop`（补首次掉落证据/stone/settle_why）、16:00 那行 `note: fallback-tried → no-conclusion`（同轴、那批连结算页都没抓到 ⇒ 无正面失败证据）；**权重 −6.0 → 0.0**；只动这 2 行，其余 81 行逐字未变（脚本自检 True）。
## 7. 剩余风险
1. 冲突时按文案判 done ⇒ 若「首次掉落」OCR 假阳性会高估一次通关（概率低；`evidence` 留 `text_head` 可复核）。
2. ⊘ 判别器只做**文字级**取证、真机未验：`has=false` 一律降级 `⊘?` ⇒ 本可 ⊘ 的单位会多复查一轮（代价＝几分钟，不会误丢矿）。**历史 14 条 ⊘ 未整段回滚**，按单位回补。
3. `readAll(...,{text:true})` 依赖服务 `?text=1`（旧版服务会忽略）⇒ 拿不到文字只会更保守（降级待判别），不会误判通关；同批另两份轴（41415/51786）仍 −6（它们确实没打通这关，保留 —— 那是 `fallback-tried` 在 planner 侧的既定政策，不在本次改动文件范围内）。
