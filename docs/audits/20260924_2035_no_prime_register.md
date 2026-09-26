# ⊘（报酬无源石）全量登记 + 闸值盲区（离线，不占机）

日期：2026-09-24 20:35 ｜ 执行：prts(Hermes) ｜ 数据：`runtime/reports/event_sweep_*.jsonl`（225 份 / 369 行）+ `runtime/_prime_state3.json`（20:09:55）

## 0. 一句话

全量 ⊘ = **70 行，其中 65 行（93%）落在 0.46–0.64 带内**（复现 ark 的数字）。但把分数按 **(活动,难度)** 拆开看，分数**不是噪声、也不是「徽章有没有」的读数，而是版面常数** ⇒ 全局区间闸必然把 93% 判成可疑；另有一格落在闸外 0.003（`CV-EX-7|普通` 0.643 > HI 0.64），而它正是唯一没被肉眼验过的那格。

## 1. 发现 A：⊘ 分数是「版面指纹」

同一关普通/突袭成对读数（13 关同时有两种难度的 ⊘ 读数；同一格有多条 ⊘ 时取**最高分**）：

| 关 | 普通 | 突袭 | Δ |
|---|---|---|---|
| AD-EX-1 | 0.461 | 0.463 | -0.002 |
| CV-EX-1 | 0.634 | 0.511 | +0.123 |
| CV-EX-2 | 0.634 | 0.511 | +0.123 |
| CV-EX-3 | 0.634 | 0.511 | +0.123 |
| CV-EX-4 | 0.634 | 0.511 | +0.123 |
| CV-EX-5 | 0.638 | 0.511 | +0.127 |
| CV-EX-6 | 0.638 | 0.511 | +0.127 |
| CV-EX-7 | 0.643 | 0.529 | +0.114 |
| CV-EX-8 | 0.638 | 0.529 | +0.109 |
| MT-EX-1 | 0.472 | 0.463 | +0.009 |
| MT-EX-3 | 0.472 | 0.463 | +0.009 |
| MT-EX-4 | 0.472 | 0.463 | +0.009 |
| MT-EX-6 | 0.469 | 0.463 | +0.006 |

* **MT** 全族：普通 0.469–0.472 / 突袭 0.463（Δ≈+0.01）
* **CV** 全族：普通 0.631–0.643 / 突袭 0.511–0.529（Δ≈+0.12）
* 分档：A 0.461–0.472 = **39 行**（MT 22 / LE 8 / AD 6 / PV 5）｜B 0.486–0.529 = 11（其中 CV 突袭 8）｜C 0.591 = 1｜D 0.631–0.644 = 16（CV 普通 15 + EA 1）｜E ≥0.662 = 3

⇒ 同一个「报酬区空」的视觉事实，在不同活动/难度的版面上给出 **0.46 / 0.51 / 0.63** 三个稳定平台，而且**同关两种难度的 Δ 也是常数**（MT≈0.01、CV≈0.12）⇒ 分数主要由版面（背景/按钮布局）决定，徽章只在 0.999 那一档才说话。所以：① 0.46–0.64 这条带框住的正是三个版面基线的并集，「93% 落带」是**闸的形状**造成的，不等于这批读数都可疑；② 要区分「空面板」与「读数失败」，**区间做不到**，只能靠判别器字段（ark 的 `reward_obtain_tag/lock_*` 方向对）**或**按版面基线判：把每个 (活动,难度) 的实测量登记成基线，只有**偏离自身基线**的 ⊘ 才升级为可疑。

## 2. 发现 B：闸有 0.003 的盲区，正压在唯一没验过的那格

| 单位 | 分数 | state | 说明 |
|---|---|---|---|
| CV-EX-7|普通 | 0.643 | done | **唯一没有肉眼验证的格子，闸管不到它**（0.643 只比 HI 高 0.003） |
| EA-1|普通 | 0.644 | done | 11:03 已真首通（源石到账）⇒ 该行早于首通，历史 |
| AD-EX-2|普通 | 0.662 | done | 13:32 已真首通 ⇒ 两行（08:10/08:12）早于首通，历史 |
| AD-EX-2|普通 | 0.662 | done | 13:32 已真首通 ⇒ 两行（08:10/08:12）早于首通，历史 |
| PV-EX-8|普通 | 0.702 | todo | 昨日读数，未记 done，无害 |

⇒ 建议：把「高置信空面板」的门槛从 `HI=0.64` 抬到 **≥0.66**，或对**任何 <0.70 的 ⊘** 都要求判别器字段；否则 `CV-EX-7|普通` 会一直被审计工具当成「凭据合格」而永远没人复查。

## 3. 状态分布（行 / 去重单位）

| state | 带内 | 带外 |
|---|---|---|
| done | 29 | 0 |
| todo | 15 | 0 |
| noreach | 21 | 0 |
| (不在状态表) | 0 | 0 |

* 带内+done 去重 23 格：AD-1|普通, AD-2|普通, AD-4|普通, AD-5|普通, AD-EX-1|普通, AD-EX-1|突袭, AD-EX-2|普通, CV-EX-1|普通, CV-EX-1|突袭, CV-EX-2|普通, CV-EX-2|突袭, CV-EX-3|普通, CV-EX-3|突袭, CV-EX-4|普通, CV-EX-4|突袭, CV-EX-5|普通, CV-EX-5|突袭, CV-EX-6|普通, CV-EX-6|突袭, CV-EX-7|普通, CV-EX-7|突袭, CV-EX-8|普通, CV-EX-8|突袭
* 带内+非 done 去重 25 格（队列会自己重读）：LE-1|普通, LE-2|普通, LE-3|普通, LE-4|普通, LE-5|普通, LE-6|普通, LE-7|普通, LE-8|普通, LE-EX-1|普通, MT-EX-1|普通, MT-EX-1|突袭, MT-EX-2|普通, MT-EX-3|普通, MT-EX-3|突袭, MT-EX-4|普通, MT-EX-4|突袭, MT-EX-5|普通, MT-EX-6|普通, MT-EX-6|突袭, MT-EX-7|普通, MT-EX-8|普通, PV-1|普通, PV-2|普通, PV-3|普通, PV-4|普通

**地面真值只有 CV-EX 一族**（ark 19:2x 与我 19:45 各肉眼读过详情页：报酬区空、锁已开）⇒ 0.631/0.511 这两个平台 = 「真已领」。**A 档 39 行（MT/LE/AD/PV 的 0.46x）没有任何地面真值**，是唯一还需要人/判别器确认的一批。

## 4. 全量 70 行明细

| # | 单位 | 分数 | 带 | state | 时间(UTC) | 账本 |
|---|---|---|---|---|---|---|
| 1 | AD-1|普通 | 0.464 | 带内 | done | 2026-09-24T07:05:15 | event_sweep_act43side_20260924_1505.jsonl |
| 2 | AD-2|普通 | 0.463 | 带内 | done | 2026-09-24T07:05:20 | event_sweep_act43side_20260924_1505.jsonl |
| 3 | AD-4|普通 | 0.470 | 带内 | done | 2026-09-24T10:26:12 | event_sweep_act43side_20260924_1826.jsonl |
| 4 | AD-5|普通 | 0.486 | 带内 | done | 2026-09-24T08:07:23 | event_sweep_act43side_20260924_1607.jsonl |
| 5 | AD-EX-1|普通 | 0.461 | 带内 | done | 2026-09-24T11:25:35 | event_sweep_act43side_20260924_1925.jsonl |
| 6 | AD-EX-1|突袭 | 0.463 | 带内 | done | 2026-09-24T11:12:59 | event_sweep_act43side_20260924_1912.jsonl |
| 7 | AD-EX-2|普通 | 0.461 | 带内 | done | 2026-09-24T11:12:12 | event_sweep_act43side_20260924_1912.jsonl |
| 8 | AD-EX-2|普通 | 0.662 | **带外** | done | 2026-09-24T00:10:27 | event_sweep_act43side_20260924_0810.jsonl |
| 9 | AD-EX-2|普通 | 0.662 | **带外** | done | 2026-09-24T00:12:50 | event_sweep_act43side_20260924_0812.jsonl |
| 10 | CV-EX-1|普通 | 0.631 | 带内 | done | 2026-09-24T07:14:18 | event_sweep_act28side_20260924_1514.jsonl |
| 11 | CV-EX-1|普通 | 0.634 | 带内 | done | 2026-09-23T23:29:43 | event_sweep_act28side_20260924_0729.jsonl |
| 12 | CV-EX-1|突袭 | 0.511 | 带内 | done | 2026-09-24T07:14:32 | event_sweep_act28side_20260924_1514.jsonl |
| 13 | CV-EX-2|普通 | 0.631 | 带内 | done | 2026-09-24T07:14:43 | event_sweep_act28side_20260924_1514.jsonl |
| 14 | CV-EX-2|普通 | 0.634 | 带内 | done | 2026-09-23T23:29:52 | event_sweep_act28side_20260924_0729.jsonl |
| 15 | CV-EX-2|突袭 | 0.511 | 带内 | done | 2026-09-24T07:15:07 | event_sweep_act28side_20260924_1515.jsonl |
| 16 | CV-EX-3|普通 | 0.631 | 带内 | done | 2026-09-24T11:41:54 | event_sweep_act28side_20260924_1941.jsonl |
| 17 | CV-EX-3|普通 | 0.634 | 带内 | done | 2026-09-23T23:30:07 | event_sweep_act28side_20260924_0730.jsonl |
| 18 | CV-EX-3|突袭 | 0.511 | 带内 | done | 2026-09-24T11:42:42 | event_sweep_act28side_20260924_1942.jsonl |
| 19 | CV-EX-4|普通 | 0.631 | 带内 | done | 2026-09-24T11:43:00 | event_sweep_act28side_20260924_1942.jsonl |
| 20 | CV-EX-4|普通 | 0.634 | 带内 | done | 2026-09-23T23:30:21 | event_sweep_act28side_20260924_0730.jsonl |
| 21 | CV-EX-4|突袭 | 0.511 | 带内 | done | 2026-09-24T11:43:26 | event_sweep_act28side_20260924_1943.jsonl |
| 22 | CV-EX-5|普通 | 0.634 | 带内 | done | 2026-09-23T23:30:34 | event_sweep_act28side_20260924_0730.jsonl |
| 23 | CV-EX-5|普通 | 0.638 | 带内 | done | 2026-09-24T11:43:42 | event_sweep_act28side_20260924_1943.jsonl |
| 24 | CV-EX-5|突袭 | 0.511 | 带内 | done | 2026-09-24T11:44:07 | event_sweep_act28side_20260924_1944.jsonl |
| 25 | CV-EX-6|普通 | 0.634 | 带内 | done | 2026-09-23T23:30:50 | event_sweep_act28side_20260924_0730.jsonl |
| 26 | CV-EX-6|普通 | 0.638 | 带内 | done | 2026-09-24T11:44:23 | event_sweep_act28side_20260924_1944.jsonl |
| 27 | CV-EX-6|突袭 | 0.511 | 带内 | done | 2026-09-24T11:44:57 | event_sweep_act28side_20260924_1944.jsonl |
| 28 | CV-EX-7|普通 | 0.635 | 带内 | done | 2026-09-23T23:36:32 | event_sweep_act28side_20260924_0736.jsonl |
| 29 | CV-EX-7|普通 | 0.643 | **带外** | done | 2026-09-24T12:04:14 | event_sweep_act28side_20260924_2004.jsonl |
| 30 | CV-EX-7|突袭 | 0.529 | 带内 | done | 2026-09-24T12:04:41 | event_sweep_act28side_20260924_2004.jsonl |
| 31 | CV-EX-8|普通 | 0.638 | 带内 | done | 2026-09-23T23:36:43 | event_sweep_act28side_20260924_0736.jsonl |
| 32 | CV-EX-8|突袭 | 0.529 | 带内 | done | 2026-09-24T11:11:00 | event_sweep_act28side_20260924_1910.jsonl |
| 33 | EA-1|普通 | 0.644 | **带外** | done | 2026-09-24T02:10:26 | event_sweep_act41side_20260924_1010.jsonl |
| 34 | LE-1|普通 | 0.462 | 带内 | noreach | 2026-09-23T12:02:24 | event_sweep_act18side_20260923_2002.jsonl |
| 35 | LE-2|普通 | 0.462 | 带内 | noreach | 2026-09-23T12:02:32 | event_sweep_act18side_20260923_2002.jsonl |
| 36 | LE-3|普通 | 0.486 | 带内 | noreach | 2026-09-23T12:02:49 | event_sweep_act18side_20260923_2002.jsonl |
| 37 | LE-4|普通 | 0.472 | 带内 | noreach | 2026-09-23T12:03:06 | event_sweep_act18side_20260923_2002.jsonl |
| 38 | LE-5|普通 | 0.462 | 带内 | noreach | 2026-09-23T12:03:23 | event_sweep_act18side_20260923_2002.jsonl |
| 39 | LE-6|普通 | 0.463 | 带内 | noreach | 2026-09-23T12:03:40 | event_sweep_act18side_20260923_2002.jsonl |
| 40 | LE-7|普通 | 0.468 | 带内 | noreach | 2026-09-23T12:03:57 | event_sweep_act18side_20260923_2002.jsonl |
| 41 | LE-8|普通 | 0.523 | 带内 | noreach | 2026-09-23T12:04:14 | event_sweep_act18side_20260923_2002.jsonl |
| 42 | LE-EX-1|普通 | 0.591 | 带内 | todo | 2026-09-23T12:05:23 | event_sweep_act18side_20260923_2005.jsonl |
| 43 | MT-EX-1|普通 | 0.472 | 带内 | todo | 2026-09-23T03:41:30 | event_sweep_act42side_20260923_1141.jsonl |
| 44 | MT-EX-1|普通 | 0.472 | 带内 | todo | 2026-09-23T03:55:53 | event_sweep_act42side_20260923_1155.jsonl |
| 45 | MT-EX-1|突袭 | 0.463 | 带内 | noreach | 2026-09-23T03:41:51 | event_sweep_act42side_20260923_1141.jsonl |
| 46 | MT-EX-1|突袭 | 0.463 | 带内 | noreach | 2026-09-23T03:56:13 | event_sweep_act42side_20260923_1155.jsonl |
| 47 | MT-EX-1|突袭 | 0.463 | 带内 | noreach | 2026-09-23T18:15:56 | event_sweep_act42side_20260924_0215.jsonl |
| 48 | MT-EX-1|突袭 | 0.463 | 带内 | noreach | 2026-09-23T18:23:09 | event_sweep_act42side_20260924_0223.jsonl |
| 49 | MT-EX-2|普通 | 0.466 | 带内 | todo | 2026-09-23T03:42:06 | event_sweep_act42side_20260923_1141.jsonl |
| 50 | MT-EX-2|普通 | 0.466 | 带内 | todo | 2026-09-23T03:56:26 | event_sweep_act42side_20260923_1155.jsonl |
| 51 | MT-EX-3|普通 | 0.472 | 带内 | todo | 2026-09-23T03:46:14 | event_sweep_act42side_20260923_1141.jsonl |
| 52 | MT-EX-3|普通 | 0.472 | 带内 | todo | 2026-09-23T04:00:09 | event_sweep_act42side_20260923_1155.jsonl |
| 53 | MT-EX-3|突袭 | 0.463 | 带内 | noreach | 2026-09-23T04:00:43 | event_sweep_act42side_20260923_1155.jsonl |
| 54 | MT-EX-4|普通 | 0.472 | 带内 | todo | 2026-09-23T03:50:58 | event_sweep_act42side_20260923_1141.jsonl |
| 55 | MT-EX-4|普通 | 0.472 | 带内 | todo | 2026-09-23T04:00:56 | event_sweep_act42side_20260923_1155.jsonl |
| 56 | MT-EX-4|突袭 | 0.463 | 带内 | noreach | 2026-09-23T03:51:22 | event_sweep_act42side_20260923_1141.jsonl |
| 57 | MT-EX-4|突袭 | 0.463 | 带内 | noreach | 2026-09-23T04:01:15 | event_sweep_act42side_20260923_1155.jsonl |
| 58 | MT-EX-4|突袭 | 0.463 | 带内 | noreach | 2026-09-23T18:19:39 | event_sweep_act42side_20260924_0219.jsonl |
| 59 | MT-EX-4|突袭 | 0.463 | 带内 | noreach | 2026-09-23T18:28:06 | event_sweep_act42side_20260924_0223.jsonl |
| 60 | MT-EX-5|普通 | 0.469 | 带内 | todo | 2026-09-23T04:01:28 | event_sweep_act42side_20260923_1155.jsonl |
| 61 | MT-EX-6|普通 | 0.469 | 带内 | todo | 2026-09-23T04:04:37 | event_sweep_act42side_20260923_1155.jsonl |
| 62 | MT-EX-6|突袭 | 0.463 | 带内 | noreach | 2026-09-23T18:29:10 | event_sweep_act42side_20260924_0223.jsonl |
| 63 | MT-EX-7|普通 | 0.469 | 带内 | todo | 2026-09-23T04:09:14 | event_sweep_act42side_20260923_1155.jsonl |
| 64 | MT-EX-8|普通 | 0.469 | 带内 | todo | 2026-09-23T18:08:31 | event_sweep_act42side_20260924_0208.jsonl |
| 65 | PV-1|普通 | 0.464 | 带内 | todo | 2026-09-23T16:53:42 | event_sweep_act38side_20260924_0053.jsonl |
| 66 | PV-1|普通 | 0.464 | 带内 | todo | 2026-09-24T10:15:29 | event_sweep_act38side_20260924_1815.jsonl |
| 67 | PV-2|普通 | 0.464 | 带内 | noreach | 2026-09-23T16:54:01 | event_sweep_act38side_20260924_0053.jsonl |
| 68 | PV-3|普通 | 0.464 | 带内 | noreach | 2026-09-23T16:54:27 | event_sweep_act38side_20260924_0053.jsonl |
| 69 | PV-4|普通 | 0.472 | 带内 | noreach | 2026-09-23T16:54:53 | event_sweep_act38side_20260924_0053.jsonl |
| 70 | PV-EX-8|普通 | 0.702 | **带外** | todo | 2026-09-23T21:31:45 | event_sweep_act38side_20260924_0531.jsonl |

## 5. 复现（只读）

```bash
# 同一解释器：C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe
PY=C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe
$PY - <<'EOF'
import json,glob,collections
RT=r'D:\1-2\MAAOrch\runtime'
rows=[json.loads(l) for f in glob.glob(RT+r'\reports\event_sweep_*.jsonl') for l in open(f,encoding='utf-8') if l.strip()]
np_=[r for r in rows if r.get('action')=='no_prime']
print(len(np_), sum(1 for r in np_ if 0.46<=(r.get('reward_score') or 0)<=0.64))
EOF
```

## 6. 未做 / 交给谁

* A 档 39 行（MT/LE/AD/PV 的 0.46x）**没有地面真值**：这批正是队列 PV→LE→MT 接下来要走的路，建议随路读数一律带判别器字段落账，别再只落分数；
* 闸值改动（`HI` 0.64→0.66）是**政策**，归 ark 的 A/B 框架；
* 本文件只读产物，未改任何代码 / 状态 / 账本。