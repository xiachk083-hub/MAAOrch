# 主线可达性 / noreach 根因审计 · 2026-09-24 14:45

审计人：**auditor-2（独立只读审计）**｜范围：`noreach` 的根因分类 + 主线（主题曲 EP07~EP14）进关路径的现状与回归
纪律：**全程只读**。本轮**没有修改仓库里任何文件**（本报告除外）、**没有碰模拟器/adb/MuMuManager/MAA/8765 服务**、**没有 kill 任何进程**。
我做的动作只有：读代码、读日志/账本/状态文件、`python -c` 读文件做统计（不写盘）、读 git 状态。

---

## 0. 结论摘要（先看这三条）

1. **当前正在流血的根因不是导航，是解释器**：14:36–14:37 的 3 条 noreach 是
   `ModuleNotFoundError: No module named 'cv2'` 造成的 —— 工装被一个**没有 opencv 的 python** 拉起，
   `mt_nav/nav_map/enter_by_template/main_nav` 一进门就崩，横扫只能记 `blocked`，队列就把这一关标 `noreach`。
   （证据：`runtime/_prime_queue3.log:7261, 7280, 7285, 7292, 7310, 7315, 7322, 7340, 7345, 7352`；
   `tools/event_sweep.mjs:41`、`runtime/_prime_queue3.py:314,449,541,559,574,819`）
2. **108 个 noreach 里 103 个不是"真打不过"，而是三次"状态写坏"**：85 个是**一次把整章标死**
   （`放弃本次进 <活动> …该活动的单位标 noreach`，02:52–03:00 连标 5 个活动），23 个是**绕过冷却账本的**
   批量写入（EA 整章 + `EA-1→done`，09:54–12:29 之间，队列日志里**一条对应记录都没有**）。
   这 108 个里 **64 个从头到尾没有任何 sweep 记录**（没被试过一次就被判死）。
3. **主线路径"通了一半、且现在断着"**：`tools/main_nav.py` 12:02 真机已走到 7-2 详情页（task-12 报告），
   但（a）队列默认命令**不带 `--with-story`**（195 单位里没有主线）；（b）`story_enter()` 用的是裸 `"python"`
   ⇒ **和 14:36 那次崩溃同一个病**；（c）`story_verdict()` 把"面板对了但 ok=false"的**假失败**判成
   `nav_fail → noreach`（12:02 现场就是这种帧）；（d）**最肥的 H 关在图上找不到**（H8-1 滑 16 次没出现）。

---

## 1. 证据基础（全部可复跑、只读）

| 证据 | 说明 |
|---|---|
| `runtime/_prime_state3.json`（195 单位） | 14:32 快照：`noreach 108 / todo 85 / done 2`；14:37:31 被 live 跑批改成 `111 / 82 / 2`（新增 CV-EX-8\|突袭、CV-1、CV-2，全部是 cv2 崩的） |
| `runtime/_prime_state3.json.bak-20260924_102933`（09:54） | 与当前 diff：**74 处变化** = 47 处 `done→todo`（污染回滚）+ **23 处 EA `todo→noreach` + `EA-1→done`** + 3 处 CV（live） |
| `runtime/_prime_queue3.log`（517 KB，7355+ 行） | 68 次队列启动；`放弃本次进` 5 次；`ModuleNotFoundError: No module named 'cv2'` 10 次 |
| `runtime/reports/event_sweep_*.jsonl`（186 本账、317 条记录） | `action` 分布：blocked 162 / insufficient_sanity 50 / no_prime 47 / run 30 / done 20 / partial 6 / no_axis 1 / failed 1 |
| `runtime/_prime_noreach.json` | **14:37:31 才第一次出现**（510 B，只有 live 那 3 条）⇒ 旧 108 个 noreach **从来没走过 `mark_noreach()`** |
| `data/nav_nodes.json` | `maps` 里 7 张图**节点表全空**（`PV_EX/MT_EX/EP_EX/AD_MAIN/EA_MAIN/EA` 都是 0 个节点），只有 `LE_EX.views` 有 3+4 个坐标；`zones` **没有 EA** |
| `runtime/learned/` | `nodes_EP.json`（只有 EP-EX-1..8）、`nodes_EA.json`（2 个，key 还少前缀）、`nodes_AD.json`（10 个，无 EX）；`stages_EP.json`（8 关，EX）、`stages_EA.json`（1 关 / 应有 16，带 warning）；**没有 nodes_PV / nodes_MT / nodes_LE / nodes_CV** |
| 其它审计/报告 | `docs/audits/20260924_1040.md`、`20260924_1215_page_recog.md`、`20260924_1400_task18_sched.md`、`runtime/reports/story_nav_20260924.md`、`runtime/reports/task15_notes.md` |

---

## 2. Q1 —— 108 个 noreach 到底卡在哪一层（根因分类表）

### 2.1 分类表（快照 = 14:32 的 108；14:37 起 +3 CV，见 2.4）

| 类别 | 数量 | 代表条目 | 决定性证据（文件:行） |
|---|---|---|---|
| **A 环境层**：工装解释器没有 cv2 ⇒ 工具一进门就崩 ⇒ 记 blocked ⇒ 标 noreach | **3**（live 增长中） | `CV-EX-8\|突袭`、`CV-1\|普通`、`CV-2\|普通` | `_prime_queue3.log:7280/7285/7292`（14:36:49）；`7352`（14:37:31）；`events_sweep.mjs:41`；`_prime_queue3.py:559/574` |
| **B 入口层（策略）**：进活动连续失败 ⇒ **一次把整章的每个单位都标 noreach** | **85** | EP-1..8、PV-1..10、PV-S-1..5(普/突)、PV-SP-1..3、PV-EX-1..8 突袭、MT-S-1..5(普/突)、MT-EX-1..8 突袭、LE-1..8、LE-EX-1..8 突袭、LE-EX-8 普通 | `_prime_queue3.log:1043 / 1047 / 1158 / 1162 / 1166`（5 行杀 4 个活动 85 个单位）；代码 `_prime_queue3.py:840-849` |
| **C 可达性层（图上没有 / 锁区 / 无区域卡）**：EA 整章 | **23** | `EA-2\|普通`（在图上但节点找不到，主区只有 EA-1/EA-TR-1/EA-ST-1 + 右下「摇的影」🔒）；`EA-EX-1..8`（普/突）；`EA-3..8` | 账本 `event_sweep_act41side_20260924_1110.jsonl` → `地图上找不到 EA-2 的节点（章节=MAIN）｜屏上文字：OPERATION TUTORIAL EA-1 EA-TR-1 OPERATION EA-ST-1 摇的影 FLICKERN｜当时页面=活动地图（0.966）`；`data/nav_nodes.json` **zones 里没有 EA**、`maps.EA/EA_MAIN` 节点数 0；`runtime/learned/stages_EA.json` = `1 关 / 应有 16`（自带 warning） |
| **D 目标关不存在于任何坐标表/节点库（"计划表里有、图上没有"的候选）** | 交叉计数（含在 B/C 内） | `MT-S-1..5`（10）、`PV-S-1..5`（10）、`PV-SP-1..3`（3）、`AD-EX-3..8`（12，**状态是 todo 不是 noreach**） | `data/nav_nodes.json.hex` 只有 `PV_EX/MT_EX/EP_EX/AD_MAIN/EA_MAIN/EA`（**没有 PV_S / MT_S / PV_SP / AD_EX**）；`runtime/learned/nodes_AD.json` 无任何 `AD-EX-*`；task-15 真机记录 `runtime/reports/task15_notes.md:62`：AD 的 EX 区图上**只有 EX-1/EX-2 两个节点** |
| **E 状态陈旧（交叉计数，不参与 A~C 的分区）**：账本最后一条**已经给出结论**，单位却还是 noreach | **16** | `no_prime` 15 个：`LE-1..8\|普通`、`PV-1..4\|普通`、`MT-EX-1/4/6\|突袭`；`done` 1 个：`MT-EX-7\|突袭` | 账本末条 `action` 统计（见 2.3） |

> 口径说明：A/B/C 是**互斥分区**（108 = 85 + 23；再加 live 的 3 条 → 111）；**D/E 是交叉视角**（同一批单位同时属于 B/C），所以 D/E 的数字**不能与 A/B/C 相加**。
> D 的意义不是计数，而是"**这 35 个条目在任何本地坐标/节点证据里都不存在**"——这正是排序层该降级的对象（见 §5）。

### 2.2 三种"卡住的位置"在白话上的区分（这决定了修法完全不同）

| 现象 | 卡在哪一层 | 典型日志/读数 | 该怎么修 |
|---|---|---|---|
| `mt_nav 无输出：Traceback … No module named 'cv2'` | **环境（工装）** | 14:36–14:37 那 3 条 | 换解释器 + **预检失败就整轮中止**，绝不写 noreach |
| `不在活动图里（屏上：默认进度 奖励进度 完成进度 乐章 6/6…）` | **入口（活动卡片/曲谱页）**：人在「完成进度」网格页，没人把他带回活动地图 | `event_sweep_act39side_20260924_0740.jsonl` 等 | 模板进活动兜底（已有）+ **失败只记"本次会话进不去"，不许写全章状态** |
| `地图上找不到 EA-2 的节点` / `这一屏是 MT-EX-1 的详情页，不是 MT-EX-5` | **节点/身份**：mt_nav 的自我保护（这层是对的！） | `..._20260924_1110.jsonl`、`..._20260923_1223.jsonl` | 把"图上没有"落到**排序降级 + 一次性证据**，而不是 noreach |

### 2.3 账本层面的定量（44/108 有记录，64/108 一条都没有）

```
noreach 108｜有 sweep 记录 44｜没有任何记录 64
  blocked 23   no_prime 15(done 1)   run 5
无记录的 64 按前缀：EA 22、PV 20、EP 14、LE 6、MT 2   ← 与 B（整章标死）+ C（EA 批量写）完全吻合
```
- **"没有任何记录"= 被标死时根本没跑过**。这是 B 类"整章标死"最硬的证据：
  `_prime_queue.py` 一行的 `放弃本次进 出苍白海` 就把 EP 的 16 个单位的命运定死了（`_prime_queue3.log:1043`）。
- **15 个 `no_prime`** 说明"标 noreach 之后，后来其实跑进去过、而且读到报酬区没有源石"（= 矿早领了）⇒ 这些应该是 `done`，
  现在是 noreach ⇒ 每轮 TTL 到点都会**再导航一次**（不花理智，但白花 10~60s/关）。
- 只有 **1 个 `no_axis`**、**1 个 `failed`**：说明"有矿但打不过"的比例极低 —— **瓶颈确实在"进不去"，不在"打不过"**。

### 2.4 live 变化（写报告期间的真实观测，值得记一笔）

`runtime/_prime_state3.json` 在 **14:37:31** 被正在跑的队列改写：noreach 108 → **111**，
新增 `CV-EX-8|突袭 / CV-1|普通 / CV-2|普通`，三条日志全部是 `mt_nav 无输出：… No module named 'cv2'`
（`_prime_queue3.log:7295 / 7325 / 7355`）。
**14:44 复读时该文件已回到 `noreach 108 / todo 85 / done 2`**（PV31/EA23/MT21/LE17/EP16）——
即那 3 条是**外部回滚掉的**（同期 `docs/audits/20260924_1438_takeover_audit.md` 由另一个 agent 落地，疑为其交接产物）。
⇒ 两条结论：① **只要解释器不修，每跑一关就会多一个假 noreach**；
② 这次"写进去又回滚"恰好证明这类写入**没有单一权威**：状态文件在 12:29 / 14:37 / 14:44 被三次不同来源改写，
本审计 §2.2 的**分类表以 14:32–14:44 稳定态（108）**为准，live 期间的 111 只作证据。

---

## 3. Q2 —— 主线（主题曲 EP07~EP14）进关路径：走哪条代码、与活动的差别、回归

### 3.1 现在走的是哪条代码

```
runtime/_prime_queue3.py
  build_units(with_story=True)      :114-160  →  story_units() :413-440（读 runtime/plans/story_mines.json）
  主循环主线分支                      :795-831
    story_enter()                   :443-468  →  subprocess ["python","tools/main_nav.py","--code",code,"--quiet"(+"--raid")]  :449-451
        tools/main_nav.py
          ensure_map()              :493-570  大厅→终端→MAIN THEME 卡/主题曲卡→章节地图（全 OCR 认页）
          switch_episode()          :573-632  EPISODE 页签滑动窗口（默认 tries=16）
          find_node()               :441-455  OCR 找 `7-2`/`R8-1` 标签；找不到就左右各滑 6/10 次 :740-752
          detail_confirmed()        :369-391  用面板代号核实"确实是这一关"（**不依赖 page_of**）
          stars_from_panel() :419-438 / reward_has_prime() :243-260（ROI y430:620 x860:1280，阈值 0.75）
        → 返回一行 JSON {ok,on_page,stars,reward_primes,sanity,mode,detail}
    story_verdict()                 :477-494  纯函数判决：nav_fail→noreach / other_page→不判 / done / mine
    有轴 → tools/stage_attempt.py   :819-825  （默认只 --drill 演习；--story-run 才真打）
```

### 3.2 和"活动"进关路径的差别

| 维度 | 活动（EP/PV/MT/LE/AD/CV/EA） | 主线（主题曲 7~14） |
|---|---|---|
| 入口 | 曲谱页 →「乐章收录」→「完成进度」网格 → 卡片模板/OCR 找乐章 →「进入活动」 | 大厅 → 终端 → MAIN THEME 卡 / 主题曲卡 → 章节地图 |
| 导航工具 | `tools/nav_map.py`（薄壳，调服务 `/nav`）→ `tools/mt_nav.py`（OCR + 页签 + 左右滑） | `tools/main_nav.py`（自己截图 + RapidOCR，**不走服务**） |
| 身份核实 | 模板 `data/templates/stages/<代号>.png` 对 `CODE_ROI(860,80,1010,120)`（5 ms） | OCR 面板代号 + `norm_code()` 归一（`07-2→7-2`） |
| 打关 | `tools/event_sweep.mjs`（演习优先、闸门、账本、轴选择） | **完全不走 sweep**：队列直接调 `tools/stage_attempt.py` |
| 理智门槛 | `--sanity-min str(u["ap"])`（`_prime_queue3.py:587`） | **没有门槛**（读到了 `nav.sanity` 但主线分支从不检查 → :795-831） |
| 难度档 | `--wants 突袭`（走 sweep） | `main_nav --raid`（普通/磨难/H/S 混合在 `want` 里，:434） |
| 进度判据 | 面板徽章 + 结算页 `result_stars` | 面板徽章 `reward_has_prime` + `stars`（这套复用是合理的） |

### 3.3 task-12 之后的回归（按严重度）

| # | 回归/缺口 | 证据 | 影响 |
|---|---|---|---|
| **R1** | **`story_enter()` 用裸 `"python"` spawn `main_nav.py`（:449），而当前 PATH 上的 `python` 没有 cv2** ⇒ 主线导航**现在 100% 崩** | `main_nav.py:38` `import cv2`；`_prime_queue3.log:7280` 同类 traceback；本机实测 `python -c "import cv2"` 失败、`Python312` 的 `python.exe` 有 cv2 5.0.0 | 主线一颗源石都拿不到；就算开 `--with-story` 也是整章 `nav_fail → noreach` |
| **R2** | **`--with-story` 默认关**：生产跑的是 195 单位（活动），主线 307 单位不在计划里 | `_prime_queue3.log:7031`（195 单位、无"主线单位"行）；`story_nav_20260924.md:10,32`（`--with-story` 时 502 单位）；`_prime_queue3.py:704-705` | EV 的 85%（134 颗）**根本不在排产里** |
| **R3** | **`story_verdict()` 先看 `ok`、后看 `on_page`** ⇒ 12:02 那种"面板读对了但 ok=false"的**假失败会直接标 noreach** | `story_nav_20260924.md:76`（`on_page=07-2 / stars=3 / reward_primes=false` 却 `ok=false`）；`_prime_queue3.py:487-490` | 会把**能打的关**标死（与 1040 号审计里 activity 侧 3 个"数据对、判据错"的 bug 同类） |
| **R4** | **H 关在图上找不到**：H8-1 左右滑 16 次没出现，推测要先清完整章 | `story_nav_20260924.md:87`；`story_mines.json._top5_recommendation` 的 **5 条推荐里 5 条都是 H 关**（H8-1/H8-2..4/H9-1/H11-1/H7-1，ap 只要 10） | "H 关最肥"这条规划**目前不可执行**；抢窗口前必须先确认 H 关可见性 |
| **R5** | **307 个主线单位里只有 7 关有轴**（`runtime/plans/story_jobs/` = 166 份，前缀只有 `7-2 / H8-1..4 / H9-1 / H11-1`） | `ls runtime/plans/story_jobs` 前缀统计；`_prime_queue3.story_jobs_for()` :471-474；无轴分支 :813-816 只"登记需求" | 即使导航修好，**最多只有 7 关能打**；其余 300 关只会"打印一行需求" |
| **R6** | 主线分支**没有理智门槛** | `_prime_queue3.py:795-831` 全程不读 `nav["sanity"]` | 可能只剩 3 点理智就去开一局（activity 侧专门为这事加了 `--sanity-min`） |

---

## 4. Q3 —— "计划表里有、图上没有"的假条目，为什么会被当 noreach 反复重试；怎么在排序层剪掉

### 4.1 它是怎么进来的（三段代码）

1. **单位是"表展开"出来的，不是"图上看到的"**：
   `build_units()` :126-130
   ```python
   learned_codes = {str(c).upper() for c in (ld.get("codes") or [])}
   table_codes   = {c for (c, _w) in cost if c.startswith(f"{prefix}-")}   # ← 全量游戏关卡表
   codes = sorted(learned_codes | table_codes, key=...)
   ```
   `cost` 来自 `stage_costs()` :84-111（`runtime/learned/stages_*.json` **∪** `data/_stage_table.json`）。
   于是 EA 的 16 个 EX 关、PV/MT 的 S 关、AD 的 EX-3..8 **全部**变成"待打单位"，
   而账号地图上根本没有这些节点（EA 主区只有 `EA-1/EA-TR-1/EA-ST-1` + 🔒 摇的影；`nav_nodes.json.zones` 里连 `EA` 都没有）。
2. **告警只打日志、不影响清单**：:136-139 对 EA 打了
   `⚠ EA: learned 只有 1 关 < 应有 16 关 ⇒ 清单残缺，需要重学`，然后**照样**用 `learned ∪ 旧表` 展开 16 个单位。
3. **排序层对"有没有节点"一无所知**：`_key()` :193-211 只用 `tier = panel_mine ? 0 : 1`，
   没有任何"图上存在性"的判据 ⇒ 假条目和真矿**同优先级**，按效率穿插排到最前面
   （14:32 的真排产前 20：`CV-EX-8突→CV-1/2/3→AD-1/2→EA-2→EA-1→CV-EX-1/2→AD-EX-2/1突→EA-EX-1/2→CV-4`）。

### 4.2 它为什么"反复"重试（三重放大）

| 放大机制 | 代码 | 后果 |
|---|---|---|
| ① 冷却账本**对旧数据等于不存在** | `noreach_expired()` :251-266 `if not rec or not rec.get("at"): return True` | 108 个旧 noreach **每一轮都被判定"过期"** ⇒ 每轮重试全部（`_prime_queue3.log:7032/7250` 都是 `会被重试 108 个`） |
| ② 单次导航失败就写 noreach | :881-891（`renav` ⇒ `mark_noreach` 一次就完） + 分类把任何 `blocked` 都算 `renav` :632-633 | 一个假条目 = 一次导航（10~60s）就"确认"成 noreach；而它下次又会因 ① 重试 |
| ③ 事件在排序里**互相穿插** ⇒ 每次换活动都要整条重进 | `_key()` 里没有"事件"分组键；`:832-836` `grp=(u["ev"],True)` 一变就 `run_enter()` | 前 30 个单位里 CV/AD/EA 换了 5 次 ⇒ 5 次"进入活动"（每次 40~140s，失败还标 noreach）—— 用户说的"一遍一遍从头再来"在这一层 |

### 4.3 排序层怎么剪（不改结论、只降优先级；**不删条目**）

原则：**"图上有没有"必须有本地证据才降级；没有证据的只能降级，不能删、不能判死**（沿用"没 100% 看到就不下结论"）。
三档 tier：

| tier | 含义 | 判据（全部本地、离线可算） |
|---|---|---|
| 0 | 有**当前实测的矿** | `runtime/learned/panels_*.json` + `reports/map_panels.json` 里 `reward_primes is True`（已有，:178-191） |
| 1 | 图上**有证据存在**这个节点 | 代号出现在 `runtime/learned/stages_<前缀>.json.codes`（= 进图后从画面学到）**或** `runtime/learned/nodes_<前缀>.json` 有坐标**或** `data/nav_nodes.json.maps/<前缀[_EX]>` 里有这个键 |
| **2（新）** | **本地没有任何"图上存在"的证据**（= 假条目候选） | 以上三条全不成立（例：`EA-EX-*` 因 zones 无 EA；`PV-S-*`/`MT-S-*`/`PV-SP-*` 因 hex/maps 无对应键；`AD-EX-3..8` 因 nodes_AD 无 EX） |

`tier=2` 只在**同一 `tier` 内**按原有 `-eff, -ce, ap, code` 排（即**沉底但不消失**），并且 `--dry` 要**多打一行**
`图上无证据（降级）N 个：…` 让人一眼看到 —— 这样既不误删（真机一旦确认存在就自动回到 tier 1），
也不会再拿窗口去撞"计划表里有、图上没有"的关。

> 对 AD-EX-3..8 特别说明：它们状态是 `todo`（不是 noreach），而 task-15 的真机记录说 EX 区只有 EX-1/EX-2。
> **本审计不在真机确认前把这条当真**（纪律），所以方案是**降级 + 打印**，不是删除。
> 一旦有人用人眼/截图确认 EX-3..8 确实不在图上，再单独出一份"条目剔除"清单。

---

## 5. Q4 —— 修复方案（文件 + 函数 + diff 草案 + 离线验证 + 真机验证）

排期建议按杠杆排序：**P1（解释器）→ P2（崩溃不许写状态）→ P4（主线判决/门槛/开关）→ P3（假条目降级 + 事件成块）→ P5（坐标表回归）→ P6（把已验证的步骤固化成技能）**。
P1/P2 是**当天必须做**的（否则每跑一关多一个假 noreach，且主线永远拿不到）。

---

### P1（最高优先）修解释器：所有工装用"能 import cv2 的 python"

**症状证据**：`_prime_queue3.log:7261` `enter_by_template: to_book 失败： No module named 'cv2'`；
`7280/7285/7292/7310/7315/7322/7340/7345/7352` 同一 Traceback；本机实测 PATH 上的 `python`（Hermes venv）**没有 cv2**，
`C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe` 有 **cv2 5.0.0**。

**改动 1：`runtime/_prime_queue3.py` —— 新增解析器（插在 :293 `TOOLS = ROOT / "tools"` 之后）**

```diff
 TOOLS = ROOT / "tools"
 
 
+def pick_python() -> str:
+    """选一个**能 import cv2** 的解释器当作工装（所有 spawn 都用它）。
+
+    为什么必须运行时解析（2026-09-24 实测事故）：PATH 上的 `python` 可能是一个
+    **没有 opencv 的 venv**（实测 Hermes venv：`python -c "import cv2"` →
+    ModuleNotFoundError），于是 mt_nav/nav_map/enter_by_template/main_nav **一进门就崩**，
+    横扫只能记 blocked ⇒ 队列把这一关标 noreach。**这不是导航问题，却写坏了状态。**
+    候选顺序：$MAAORCH_PYTHON → sys.executable → PATH 的 python → 已知的 Python312 绝对路径。
+    全都不能 import ⇒ **大声退出**（宁可整轮不动，也不要写坏状态）。
+    """
+    import shutil
+    cands = [os.environ.get("MAAORCH_PYTHON"), sys.executable, shutil.which("python"),
+             r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe"]
+    for c in cands:
+        if not c:
+            continue
+        try:
+            r = subprocess.run([c, "-c", "import cv2, rapidocr_onnxruntime"],
+                               capture_output=True, timeout=90)
+            if r.returncode == 0:
+                return c
+        except Exception:                                     # noqa: BLE001
+            continue
+    raise SystemExit("✗ 找不到能 `import cv2, rapidocr_onnxruntime` 的 python。"
+                     "工装依赖不满足 ⇒ 拒绝启动（避免把工具崩溃写成 noreach）。"
+                     "请设 MAAORCH_PYTHON=…\\Python312\\python.exe")
+
+
+PYEXE = pick_python()
```

**改动 2：6 处 spawn 改用 `PYEXE`（`diff` 只列 2 处，其余 4 处同改）**

```diff
@@ runtime/_prime_queue3.py:449 (story_enter)
-    args = ["python", "tools/main_nav.py", "--code", u["code"], "--quiet"]
+    args = [PYEXE, "tools/main_nav.py", "--code", u["code"], "--quiet"]
@@ runtime/_prime_queue3.py:314 (run_panel_scan)
-    args = ["python", "tools/scan_map_panels.py", "--prefix", prefix, "--area", area,
+    args = [PYEXE, "tools/scan_map_panels.py", "--prefix", prefix, "--area", area,
```
其余：`:541`（discover_stages）、`:559`（enter_by_template）、`:574`（enter_event）、`:819`（stage_attempt）。

**改动 3：`tools/event_sweep.mjs:41` + 启动预检（照抄 1103-1110 那个"旧服务契约"守卫的写法）**

```diff
-const PY = process.env.MAAORCH_PYTHON || 'python'
+// **必须先解析出"能 import cv2 的 python"**（2026-09-24 实测事故：PATH 上的 python 是没有
+//   opencv 的 venv ⇒ 每个工具一进门就 Traceback ⇒ 队列把这一关标 noreach）。
+const PY_CANDS = [process.env.MAAORCH_PYTHON, process.execPath && 'python',
+                  'python', 'C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe'].filter(Boolean)
+let PY = null
+for (const c of PY_CANDS) {
+  const r = await run(c, ['-c', 'import cv2, rapidocr_onnxruntime'], { timeoutMs: 90000, label: `pycheck ${c}` })
+  if (r.code === 0) { PY = c; break }
+}
+if (!PY) {
+  log('✗ 找不到能 import cv2 的 python ⇒ 拒绝开跑（避免把工具崩溃写成 noreach）。')
+  log('   设 MAAORCH_PYTHON=… 后重试，例如： set MAAORCH_PYTHON=C:\\Users\\xiach\\AppData\\Local\\Programs\\Python\\Python312\\python.exe')
+  process.exit(2)
+}
+log(`工具解释器：${PY}`)
```

（`tools/prime_sweep.mjs:29`、`tools/maaorch_service.py:1075`、`runtime/_rank_refresh.py:18`、`runtime/_gate_scan.py:54,67` 同一处病，建议同批改；服务那份属于 fixer-1 的地盘。）

**离线验证（不碰模拟器）**
```bash
# ① 证伪/证实解释器差异（只读）
python -c "import importlib.util as u;print('PATH python has cv2:', bool(u.find_spec('cv2')))"        # → False
"C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe" -c "import cv2;print(cv2.__version__)"  # → 5.0.0
# ② 解析器自证（不需要真机）：把 $MAAORCH_PYTHON 指到坏解释器 ⇒ 必须 SystemExit；指向好解释器 ⇒ 返回它
MAAORCH_PYTHON=/nonexistent python -c "import sys;sys.path.insert(0,'runtime');import _prime_queue3 as q;print(q.PYEXE)"
# ③ 干跑回归：不再出现任何 "No module named" 的 spawn（--dry 不 spawn，主要看启动没报错）
python runtime/_prime_queue3.py --dry --no-save-state | tail -3
```

**真机验证**：`python tools/main_nav.py --code 7-3`（只进详情页、不点开始 ⇒ 不花理智）应返回一行 JSON 且 `ok/on_page` 正常；
再看一轮 `--retry-noreach --max-attempts 1` 的日志，**不应该**再出现 `No module named 'cv2'`。

---

### P2（最高优先）工具崩溃 ≠ noreach：分类层把"环境/崩溃"单列并**整轮中止**

**为什么**：`classify_report()` :601-655 把任何 `action=blocked` 都翻成 `renav`（:632-633），
主循环再把它写成 noreach（:888-891）——**没有区分"导航失败"和"工具根本没跑起来"**。
14:36 那三条就是这个洞漏进来的。

```diff
@@ runtime/_prime_queue3.py :: classify_report()  在 `act = str(rec.get("action") or "")` 之前插入
+    # **工装崩溃必须与"导航失败"分开**（2026-09-24 实测事故）：解释器缺 cv2 / 任何 ImportError
+    #   都会让 mt_nav 连一行 JSON 都输出不了，横扫记 blocked；旧代码把它当"导航失败"⇒
+    #   队列把这一关标 noreach。**环境问题绝不能写进状态**（这轮就是这么把 CV 3 关写坏的）。
+    _blob = json.dumps(rec, ensure_ascii=False)
+    if re.search(r"Traceback \(most recent call last\)|ModuleNotFoundError|No module named|ImportError",
+                 _blob):
+        return "toolcrash", rec
     act = str(rec.get("action") or "")
```
```diff
@@ runtime/_prime_queue3.py :: 主循环（:868 的 `if kind == "wait"` 之前）
+            if kind == "toolcrash":
+                log(f"  ✗ [{key}] **工装崩溃**（不是导航问题）⇒ 立刻停轮，不写任何状态：{vl}")
+                log("    修法：设 MAAORCH_PYTHON 指到能 import cv2 的 python，或跑 P1 的解释器解析")
+                raise SystemExit(2)
```
```diff
@@ tools/event_sweep.mjs :: nav 失败分支（:1152-1166）——把"崩溃"如实标出来
       rec.action = 'blocked'
       rec.blocked_step = 'nav'
+      rec.tool_error = /Traceback|No module named|ImportError/.test(String(nav.detail || ''))
+      if (rec.tool_error) rec.action = 'error'      // 队列据此整轮中止，不要写 noreach
```

**离线验证**（用仓库已有的"账本探针"套路，如 `runtime/_audit_probe7.py`）：造一本只有一行的 JSONL
`{"action":"blocked","verdict":"mt_nav 无输出：Traceback … ModuleNotFoundError: No module named 'cv2'"}`，
断言 `classify_report()` 返回 `("toolcrash", …)` 而不是 `("renav", …)`；再断言 `classify()` 对它是 `toolcrash`。
**真机验证**：故意把 `MAAORCH_PYTHON` 指到一个空 venv 跑一次 `--max-attempts 1`：
应**在第一个单位就中止**、日志出现"工装崩溃"、且 `_prime_state3.json` **没有任何 noreach 写入**（跑前记 md5、跑后比对）。

---

### P3 排序层：假条目降级（tier 2）+ 事件失败不再写全章状态

```diff
@@ runtime/_prime_queue3.py :: build_units() 里，`if ev:` 之前准备"图上证据"
+    # **"图上有没有这个节点"的本地证据**（只读取，不碰模拟器）：
+    #   learned 的关卡清单（进图后从画面学的）＞ nodes_<前缀>.json 坐标 ＞ nav_nodes.json 坐标表。
+    #   目的只有一个：把"计划表里有、图上没有"的假条目**降级沉底**（不是删、不是判死）。
+    def _has_map_evidence(code: str, prefix: str) -> bool:
+        ld = learned.get(prefix.upper()) or {}
+        if str(code).upper() in {str(c).upper() for c in (ld.get("codes") or [])}:
+            return True
+        try:
+            if code in json.loads((LEARNED / f"nodes_{prefix.upper()}.json")
+                                  .read_text(encoding="utf-8")):
+                return True
+        except Exception:                                     # noqa: BLE001
+            pass
+        try:
+            cfg = json.loads((ROOT / "data" / "nav_nodes.json").read_text(encoding="utf-8"))
+            key = f"{prefix.upper()}_EX" if "-EX-" in str(code).upper() else prefix.upper()
+            tbl = (cfg.get("maps") or {}).get(key) or {}
+            if code in (tbl.get("nodes") or {}):
+                return True
+            for v in (tbl.get("views") or []):
+                if code in (v.get("nodes") or {}):
+                    return True
+            if code in (cfg.get("hex") or {}).get(key, {}):
+                return True
+        except Exception:                                     # noqa: BLE001
+            pass
+        return False
```
```diff
@@ runtime/_prime_queue3.py :: _key()（:193-211）
             ap = ap_raw if ap_raw > 0 else AP_UNKNOWN
             eff = (ce + unit_ev) / ap
-            tier = 0 if u["code"].upper() in panel_mine else 1
+            if u["code"].upper() in panel_mine:
+                tier = 0
+            elif _has_map_evidence(u["code"], u["prefix"]):
+                tier = 1
+            else:
+                tier = 2          # **图上无证据 ⇒ 沉底**（真机确认存在后自动回到 1）
             return (tier, -eff, -ce, ap, u["code"])
```
```diff
@@ runtime/_prime_queue3.py :: 事件入口连续失败分支（:838-849）——**删掉"整章标 noreach"**
             if not ok:
                 group_fails += 1
                 log(f"  （{u['name']} 进不去，第 {group_fails} 次）")
                 if group_fails >= 4:
-                    log(f"  ⇒ 放弃本次进 {u['name']}（连续 {group_fails} 次进活动失败），"
-                        f"该活动的单位标 noreach（{NOREACH_TTL_H:g}h 后自动重试）")
-                    for v in units:
-                        if v["ev"] == u["ev"] and st.get(f"{v['code']}|{v['want']}") != "done":
-                            mark_noreach(f"{v['code']}|{v['want']}", f"进活动 {u['name']} 连续失败", st)
-                    save_state(st)
+                    # **软失败**：只在本会话里放弃这个活动（dead_groups 本来就是会话内的），
+                    #   **绝不把整章的每个单位写进状态**（2026-09-24 的 85 个 noreach 就是这么来的：
+                    #   一行日志杀 16~31 个单位，其中 64 个连一次 sweep 都没跑过）。
+                    log(f"  ⇒ 本次会话放弃进 {u['name']}（连续 {group_fails} 次失败）；"
+                        f"**只记会话内跳过，不写 noreach**（磁盘状态不动）")
                     dead_groups.add(u["ev"])
                     continue
```
```diff
@@ runtime/_prime_queue3.py :: renav 分支（:881-891）——单次失败不判死，退化到"本次会话跳过该关"
-                log(f"  [{key}] 导航失败 ⇒ 不重进，标 noreach（冷却 {NOREACH_TTL_H:g}h）换下一关：{vl}")
-                mark_noreach(key, f"导航失败：{vl}", st)
-                save_state(st)
+                # 一次导航失败 ≠ 这关打不了（连续两次才写 noreach，且要带 reason_class）
+                _r = load_noreach().get(key) or {}
+                _n = int(_r.get("n") or 0)
+                if _n >= 1:                        # 上一轮也是"导航失败" ⇒ 现在才认账
+                    log(f"  [{key}] 连续 2 轮导航失败 ⇒ noreach（reason=reach，冷却 {NOREACH_TTL_H:g}h）")
+                    mark_noreach(key, f"连续导航失败：{vl}", st)
+                    save_state(st)
+                else:
+                    log(f"  [{key}] 导航失败（第 1 次）⇒ **只记本轮跳过**，不写 noreach：{vl}")
+                    mark_noreach(key, f"导航失败一次：{vl}", st)   # 只记次数，state 由下面恢复
+                    st[key] = "todo"
+                    DIRTY.add(key)
+                    save_state(st)
                 break
```
```diff
@@ runtime/_prime_queue3.py :: clear_noreach 也要在 no_prime 分支调用（:909-915）
             elif kind == "no_prime":
                 st[key] = "done"
                 DIRTY.add(key)
+                clear_noreach(key)        # 已经有结论的关，冷却账本也要清掉（否则会一直是"陈旧 noreach"）
                 save_state(st)
```

**离线验证**
- `--dry` 必须新增一行 `图上无证据（降级）N 个：['EA-EX-1','EA-EX-2',…]`，并断言：
  `EA-EX-*`（zones 无 EA）、`MT-S-*`/`PV-S-*`/`PV-SP-*`（hex/maps 无对应键）、`AD-EX-3..8`（nodes_AD 无 EX）**都在降级名单里**，
  而 `CV-1/AD-1/EA-1/EP-EX-3`（有证据）**不在**。
- 断言排序性质（纯函数层）：`sum(1 for u in units if _key(u)[0]==2) == len(降级名单)`；且 tier 0/1 的相对顺序与改动前**完全一致**
  （可在 `runtime/_audit_task18_sched.py` 那种对照脚本里跑）。
- 事件成块/软失败：把 `group_fails>=4` 的分支用一个假的 `run_enter→False` 单测（monkeypatch）跑一遍，
  断言 `_prime_state3.json` 的 md5 **不变**（关键：不写盘）。

**真机验证**：一轮 `--retry-noreach --max-attempts 1` 之后，`_prime_noreach.json` 里应出现 `reason` 字段，
且 `_prime_state3.json` 的 noreach 总数**不会因为一次进活动失败而增加 ≥5**（对比跑前跑后的 `Counter`）。
`AD-EX-3..8` 只应在日志里"沉底"，**不应该**再多花 10 分钟去撞。

---

### P4 主线专项：判决顺序 + 理智门槛 + 默认入列 + H 关可达性

```diff
@@ runtime/_prime_queue3.py :: story_verdict()（:477-494）——**on_page 优先于 ok**
 def story_verdict(nav: dict, code: str) -> str:
-    if not nav.get("ok"):
-        return "nav_fail"
     on_page = str(nav.get("on_page") or "")
+    # **先看"站在哪一关的面板上"，再看 ok**（2026-09-24 12:02 真机实测）：
+    #   那次 `on_page=07-2 / stars=3 / reward_primes=false` 却 `ok=false`（难度切换那条路的返回值），
+    #   旧顺序会把它判成 nav_fail ⇒ 队列把**能打的关**标 noreach（与 activity 侧同类假失败）。
+    if on_page and _norm_code(on_page) == _norm_code(code):
+        return "done" if nav.get("reward_primes") is False else "mine"
+    if not nav.get("ok"):
+        return "nav_fail"
     if on_page and on_page != code:
         return "other_page"
     if nav.get("reward_primes") is False:
         return "done"
     return "mine"
```
（需要把 `main_nav.norm_code` 的 4 行正则复制成 `_prime_queue3._norm_code`，或 `from tools.main_nav import norm_code`。）

```diff
@@ runtime/_prime_queue3.py :: 主线分支（:795-831）——加理智门槛（与活动侧 --sanity-min 同纪律）
             stars, prime = nav.get("stars"), nav.get("reward_primes")
+            if nav.get("sanity") is not None and int(nav["sanity"]) < int(u.get("ap") or 0):
+                st[key] = "wait"
+                DIRTY.add(key)
+                save_state(st)
+                log(f"  [{key}] ⏸ 主线理智 {nav['sanity']} < 门槛 {u['ap']} ⇒ 记 wait 换下一关"
+                    f"（药由 MAA 吃；源石只能人工用）")
+                continue
             if vd == "done":
```
```diff
@@ runtime/_prime_queue3.py :: CLI（:704-705）——把"主线在不在计划里"变成可见的（默认不改行为，但打印差距）
     ap.add_argument("--with-story", action="store_true",
                     help="把主线（主题曲）缺口单位也纳入排产（导航走 tools/main_nav.py；需真机验证后再开）")
+    # **建议的落地方式**（不劫持默认值，避免"静默改变生产行为"）：
+    #   生产串两条命令：先 `--with-story --story-only` 跑主线（134/157 的 EV 在这里），
+    #   再跑原来的活动队列。`--story-only` 只取 story_units() 排序，不碰活动状态。
+    ap.add_argument("--story-only", action="store_true", help="只跑主线缺口单位（与 --with-story 互斥使用）")
```
```diff
@@ runtime/_prime_queue3.py :: 启动自检（:721 `units = build_units(...)` 之后）——主线就绪度一眼可见
     if with_story:
         su = story_units()
         log(...)
+        _cov = sorted({u["code"].split("-")[0] for u in su
+                       if story_jobs_for(u["code"])})
+        log(f"主线就绪度：有本地轴的关 {len([u for u in su if story_jobs_for(u['code'])])}"
+            f"/{len(su)}（章 {_cov or '无'}）⇒ 其余只会打印需求、不会真打（补轴见 runtime/plans/story_jobs/）")
```

**离线验证**
- `story_verdict` 是纯函数：用 12:02 的真实返回 `{"ok": false, "on_page": "07-2", "stars": 3, "reward_primes": false}`
  断言 `== "done"`（旧代码给 `"nav_fail"`）；再补 `{"ok": true, "on_page": "07-2", "reward_primes": true}` → `"mine"`、
  `{"ok": true, "on_page": "07-1"}` → `"other_page"`。可直接跑现成的 `python tools/story_nav_selftest.py` 作回归基础。
- `--with-story --dry --no-save-state | tail -5`：应看到 `主线单位：307 个` + 就绪度行，且**不写盘**。

**真机验证**（全程只读、不点"开始行动"）
1. `python tools/main_nav.py --code 7-3` → 期望 `ok=true / on_page=7-3 / stars / reward_primes`（7-2 已知 3★ 无矿）。
2. `/all?text=1` → `page=关卡详情`（主线详情页 `page_of` 会给"未知"，所以只能用 OCR 判据，别拿 page 当判据）。
3. `python runtime/_prime_queue3.py --with-story --dry` → 看主线单位是否排到了活动单位之后（EV 排序符合预期）。
4. **H 关可达性必须先确认**：`python tools/main_nav.py --code H8-1`；若还是找不到节点，
   把 `H*` 归到 tier 2（P3 的机制），并让 Lead 在游戏里确认"H 关是否需要清章才出现"，
   否则 `story_mines.json._top5_recommendation` 的 5 条建议全不可执行。

---

### P5 坐标表回归：把"读图填的坐标"从死代码搬回数据文件

**问题**：`tools/nav_map.py:30-44` 里那两张**手读坐标表**（`NODES` 的 `PV-EX-1..8`/`PV-1..4`、`ZONE_CARDS`）
**从未被读取**（`grep NODES tools/nav_map.py` 只有定义、没有使用）：文件已经改成薄壳，转发给服务 `/nav`
（`nav_map.py:104`）。而服务的坐标来源 `data/nav_nodes.json` 里 **7 张图的 nodes 全是 0**，
只有 `LE_EX.views` 有 7 个坐标 ⇒ **"坐标优先"这条快路对所有活动其实都是空的**，
每次都要退到 `nav_to` 的 `nodes_scan` 自动试点（`maaorch_service.py:790-825`），
而那条路在 1040 号审计里已实测"点不动/只能检出已通关的关"（§6、§6b）。

```diff
@@ data/nav_nodes.json（**纯数据**，把 nav_map.NODES 搬进来；建议保留注释说明来源）
   "maps": {
+    "PV_EX": { "nodes": { "PV-EX-1": [172,133], "PV-EX-2": [263,311], "PV-EX-3": [172,491],
+                          "PV-EX-4": [512,422], "PV-EX-5": [777,422], "PV-EX-6": [1103,491],
+                          "PV-EX-7": [1010,311], "PV-EX-8": [1103,133] } },
+    "PV":    { "nodes": { "PV-1": [232,133], "PV-2": [325,311], "PV-3": [232,491], "PV-4": [555,422] } },
     "PV_EX": { "nodes": {}, "views": [] },
```
```diff
@@ tools/nav_map.py —— 同时把"死代码"标清（或直接删，另出 PR）
-# ── 坐标表（我读图填的；key = 关卡代号，value = 该节点六边形的屏幕坐标）────────
-NODES = { ... }          # ⚠️ **已无调用方**：本文件改成薄壳后走服务 /nav，坐标真源是
-                         #   data/nav_nodes.json。删之前先确认 §P5 的搬迁已完成，别让坐标丢失。
-ZONE_CARDS = { ... }
```
**离线验证**：`python tools/nav_map.py --list` 输出里应出现 `PV -> ['PV-EX-1',…]`（现在只有 LE）。
**真机验证**：`curl -s "127.0.0.1:8765/nav?stage=PV-EX-3"` 应 `ok=true, point=[172,491], from_table=true`
（建议在 `/nav` 返回里加 `coord_src` 字段，便于区分"坐标表命中"与"自动试点"）。

---

### P6 工程纪律（低成本、防再犯）

1. **`noreach` 必须带 `reason_class`**（`env|entry|reach|stale`）：`mark_noreach()` 已经写 `reason` 自由文本，
   建议加一个受控字段并在 `--dry`/`/state` 里按类计数 —— 本轮就是因为自由文本+旧数据无记录，才花了半天才定位。
2. **任何"批量改状态"的脚本一律走 `_prime_noreach.json` + 备份 + 打印改动条数**：
   本轮 23 个 EA noreach 是**脚本外批量写**（`_prime_state3.json.bak-20260924_102933` vs 现在的 diff 里
   23 处 `todo→noreach` + `EA-1→done`，而队列日志里**一条对应记录都没有**）——这种写入**不可审计**，
   是"为什么会被当成 noreach"最直接的答案。
3. **道具化预检**：把 P1 的解释器预检、`/all` 契约预检（`event_sweep.mjs:1103-1110` 已有）、
   `nav_nodes.json` 空表检查（`maps.*.nodes == {} and not views`）三条做成一个 `--preflight`，
   任何一条不过就**拒绝开跑**。这三条恰好覆盖本轮三个最贵的坑。

---

## 6. 未决问题 / 需要人拍板

1. **EA 的 23 个 noreach 是谁写的？** 队列日志没有、`_prime_noreach.json` 当时不存在、账本也没有 ⇒
   **脚本外批量写**（人工编辑或某个一次性脚本）。**需要 Lead 确认**：EA 是"故意暂停"（0/56 + 🔒 锁区）
   还是误标。若是故意暂停，建议改成 `noreach(reason=reach)` 并**只标真正撞过的单位**，别标整章。
2. **AD-EX-3..8 是否真的不在图上**：只有 task-15 的一句真机记录（`EX 区地图上只有 EX-1/EX-2 两个节点`）。
   本审计**不据此删条目**，只做降级。需要一次人眼/截图确认（未确认前不要让排序把它们提到队首）。
3. **H 关可见性**（H8-1 找不到节点）：直接影响"最肥的矿"这条规划，需要人在游戏里确认。
4. **2★ 的 2 单位**：`EA-1` 已被真打拿到源石（task-15），但活动卡仍显示 0/56 —— 卡片进度与本地状态谁对，
   需要一次对照（关系到 `panel_mine` 的 tier 0 会不会把"其实没矿"的关插到最前）。
5. **`insufficient_sanity` 50 条**（账本里占比第二）：主线分支没有门槛（P4 已给修法），
   但活动侧 `--max-waits 3`（12 分钟）之后记 `wait` 换下一关，理智没补就再跑 ⇒ 会在"等/换关"之间循环，值得单独跟一次。

---

## 7. 附：行号索引（便于复核者逐条打勾）

| 断言 | 位置 |
|---|---|
| `PY = process.env.MAAORCH_PYTHON \|\| 'python'` | `tools/event_sweep.mjs:41`（`tools/prime_sweep.mjs:29` 同） |
| 服务 `/gate` 用裸 python spawn mt_nav | `tools/maaorch_service.py:1075` |
| 队列 6 处裸 python spawn | `runtime/_prime_queue3.py:314, 449, 541, 559, 574, 819` |
| "连续失败 ⇒ 整章标 noreach" | `runtime/_prime_queue3.py:838-849`（日志 `_prime_queue3.log:1043/1047/1158/1162/1166`） |
| 单次导航失败 ⇒ noreach | `runtime/_prime_queue3.py:881-891` |
| 分类把任何 blocked 都当 renav | `runtime/_prime_queue3.py:632-633` |
| noreach 判定/冷却/清理 | `runtime/_prime_queue3.py:240-273`（`noreach_expired` :251-266） |
| 单位展开 = learned ∪ 全量关卡表 | `runtime/_prime_queue3.py:126-130, 84-111` |
| 排序 key（无"图上证据"档） | `runtime/_prime_queue3.py:193-213` |
| 主线单位/导航/判决/轴 | `runtime/_prime_queue3.py:413-440, 443-468, 477-494, 471-474, 795-831` |
| 主线无理智门槛 | `runtime/_prime_queue3.py:795-831`（对比活动 `:587 --sanity-min`） |
| 事件组切换 = 重进活动 | `runtime/_prime_queue3.py:832-836` |
| `main_nav` 入口链/切章/找节点/核身份 | `tools/main_nav.py:493-570, 573-632, 441-455, 369-391` |
| `main_nav` 面板星数/徽章 ROI | `tools/main_nav.py:419-438, 243-260` |
| `nav_map` 是薄壳 + 死坐标表 | `tools/nav_map.py:30-44, 104` |
| `/nav` 坐标查找链 + 自动试点 | `tools/maaorch_service.py:733-929`（:754-776, 780-825） |
| 活动地图模板清单（6 张） | `tools/maaorch_service.py:256-271` |
| `mt_nav` 危险闸/页签/滑找节点 | `tools/mt_nav.py:261-268, 273-284, 285-302` |
| 账本路径打印（分类依赖它） | `tools/event_sweep.mjs:1086` |
| "旧服务契约"预检（P1 可照抄的模板） | `tools/event_sweep.mjs:1103-1110` |
| nav 失败 ⇒ blocked 记录 | `tools/event_sweep.mjs:1152-1166` |
| 闸门新契约（徽章/星数） | `tools/event_sweep.mjs:1174-1220` |
| 历史结论：noreach 永久冻死 85/195 | `docs/audits/20260924_1040.md:109-124` |
| 历史结论：`/nav` 自动试点真机点不动、`nodes_scan` 只能检出已通关 | `docs/audits/20260924_1040.md:153, 196-200` |
| 页面识别未知率 62%、单阈值不可分 | `docs/audits/20260924_1215_page_recog.md:12-14, 57-58` |
| 108 noreach 与"主线是真正杠杆" | `docs/audits/20260924_1400_task18_sched.md:48-49, 97-99` |
| 主线 7-2 真机通、H8-1 未找到、502 单位 | `runtime/reports/story_nav_20260924.md:74-87, 10, 32` |
| AD 的 EX 区只有 EX-1/EX-2 | `runtime/reports/task15_notes.md:62` |
| `swipe` 不生效、必须 motionevent | `docs/INPUT_GESTURES.md`（服务侧已用 `drag()`/`_drag_step`：`maaorch_service.py:167-207, 770-774, 880-883`） |
