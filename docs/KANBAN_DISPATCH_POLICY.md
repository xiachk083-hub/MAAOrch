# 看板派发治理规范（KANBAN_DISPATCH_POLICY v1）

- **适用范围**：看板 `maa-parallel-benchmark-20260926`（MAA并行压测板），以及后续 MAS 相关看板的任务创建（dsh-lead）与执行（mas 等 worker）。
- **定稿**：2026-09-27 21:30（+08）；**21:40 二次复核补充**（§9.4）。**状态**：规范（v1，可直接执行）。
- **依据**：
  1. 成本/吞吐复盘 `docs/audits/2026-09-27_kanban_throughput.md`（21:08 定格，全部数字可复算）；
  2. 本板 `kanban.db` 实测（21:30 快照：80 卡 / done 25 / running 30 / todo 8 / blocked 2 / archived 15；task_runs：completed 26 / crashed 10 / reclaimed 12；事件：gave_up 5 / dependency_wait 14 / promoted 7）；
  3. Hermes 源码（v0.21.5，`C:\Users\xiach\AppData\Local\hermes\hermes-agent`）——文中带 `文件:行号` 的断言均可复核。
- **配套模板**：`docs/KANBAN_TASK_TEMPLATE.md`（复制即用；含空白模板 + 两个填好的实例）。

---

## 0. 一页速查（TL;DR）

**派发六条铁律**：

1. **建卡必用七段模板**（§1），必带 `assignee / priority / parents / idempotency_key`（周期卡）与明确产出路径；建卡前先查重（§6）。
2. **单卡预算 ≤10min 工具时间、≤150 次工具调用**（迭代硬顶 = profile `agent.max_turns`：mas 150 轮，用满即 `timed_out`）；预计超 20min 的活必须拆（按对象/数据集/章节切，见 §1.4）；长卡用 `max_runtime_seconds` 兜底。
3. **并发**：一波 4–6 张；宿主级上限 8、同 profile 6、本板 6 —— **Windows 下默认没有任何上限（必须显式配置，§2.2）**。今天峰值 31 并发是"重复波叠正波"的事故，不是能力。
4. **资源串行白名单**：碰 adb/模拟器、重启侧车/MAA、发车/改队列、写生产配置 → 全局串行，且**避开班次窗口 05:00–05:40、19:30–20:30**（§2.4）。
5. **失败处理**：默认 `failure_limit=2` 自动止损（今天 10 次 crashed 全是环境类，2 次即挂是对的）；**重派前必须先看 `last_failure_error` + 修卡/修环境 + 写重试备注**，禁止裸 promote（§4）。
6. **成本观测**：数据不用 worker 自报——`kanban.db` + 回放 `logs/t_<id>.log` 是权威源；完结时 `artifacts` 必须声明（§5）。

**派发控制只有三个层级**（记住这条就不会再出现 31 并发）：
```
建卡时机（不 ready 就不会被派：todo/parents/schedule） → 并发阀门（§2.2 config） → 单卡预算（max_runtime/budget）
```

---

## 1. 标准任务模板（写卡规范）

### 1.1 七段式（正式版见 `docs/KANBAN_TASK_TEMPLATE.md`）

| # | 段 | 写什么 | 判据 |
|---|----|--------|------|
| 1 | **目标** | 一句话、可验收：`<动词> <对象> → <交付物路径/形态>` | 读完知道"做完是什么样" |
| 2 | **背景与已确认事实（别重推）** | 引用已有审计/手册；写死权威数据源路径；列出"已确立判据" | 防重复调查（今天 AutoProxy 被读 41+ 次的教训） |
| 3 | **前置与环境** | 本机 vs 远端路径、资源占用、现成工具（mas_remote.sh 等）、允许访问的最小目录/端点集 | 防"本地找远端东西"（t_3f5e4681 的教训） |
| 4 | **步骤** | 编号、可执行：命令、坐标、等待秒数、分支处理（"若 X ⇒ 做 Y / 停手报告"） | worker 不用猜 |
| 5 | **产出** | 绝对路径 + 文件名规则 + 必含字段（表格列/结论格式） | `artifacts` 可直接声明 |
| 6 | **验收** | 一条能复跑的检查 + 期望形态；PASS/FAIL 判定 | 结论可机检 |
| 7 | **约束与红线** | 只读/可写边界；绝不做什么；与在跑任务冲突怎么办 | 出错时 worker 知道该停哪一步 |

**七段之外的三条写作纪律**：
- **"已确认事实"是省钱的**：上一波结论写进新卡序言，worker 引用即可，禁止重推（`docs/audits/2026-09-27_mas_account_switch_chain.md` 被跨卡引用读 10 次 = 正面模式）。
- **环境红线写进正文**（单发模式）：禁 `python -c` / here-doc / `powershell -Command` / `execute_code`（今天 88 次审批拦截 + 26 次 execute_code 全拒）；Python 一律写成 `.py` 文件再跑（`C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe`）。
- **搜索范围写死**：禁止对 `D:/`、`E:/` 根做无范围 find/grep（今天 14 次 180s 超时 ≈42min）。

### 1.2 好例（今天最有效的三张卡，选段对照）

- **t_bec38656（实机操作类）**：对象与环境（脚本→MAA目录→实例 index→包名→坐标）＋"前置检查（必须先做）"＋逐步动作（含失败分支）＋产出表格列＋硬约束（"绝不点终端/作战/采购"、"发现 MAA 在跑 ⇒ 停手报告"）。
- **t_aaacd03c（排查类）**："背景（已确立的判据，别重复踩坑）"把 3 条坑写死在卡面；步骤里给了确认命令原文；约束"若与在跑任务冲突 ⇒ 跳过并注明"。
- **t_3006bc8f（危险操作类）**：目标带时间感（"现在就做"）＋背景（已确证，别重复调查）＋验收 checkboxes（"缺一不可"）＋硬约束＋回滚方法（还原 .bak）。

### 1.3 反例（今天真实出现过）

- "调查一下肉鸽" / "看看哪里有问题" / "全面梳理一下" —— 没有对象/路径/验收 ⇒ 必然产生找路空转。
- 目标写成"优化 XX 体验"但没说产出文件 ⇒ worker 只能猜交付形态。

### 1.4 粒度与拆分

- **预算**：单卡 ≤10min 工具时间、≤150 调用、产出 1 份文档。中位卡 58 调用/21min 墙钟已到上限边缘；超 20min 即拆。
- **硬上限（迭代预算）**：单次运行的迭代上限 = profile `agent.max_turns`（mas = 150 轮 API 循环）；用满即 `timed_out`（错误串 `Iteration budget exhausted (150/150)`，整卡作废重跑——今日 `t_5c23c432` 实测白跑 5,312s）。卡设计按 **≤120 轮** 留余量，别贴着上限写。
- **正面**：资产普查①–⑤（5 张，各 38–64 调用）；**反面**：t_7e69b3d0「全库体检 54 脚本」单卡 4,563s+ 仍在跑；t_b8476841 259 调用/36 次 SSH 逼近上限。
- **拆分轴**：按对象（脚本组/服务器组/账号段）、按数据集（1 天/7 天）、按章节（手册分册）。
- **拆分上限的反面**：不要为了"看起来并行"把一张卡切成 3 张各自重做同一段调查——先给每张卡划独立的"已确认事实"边界。

---

## 2. 并发与资源

### 2.1 阀门事实（源码级，先搞清语义再配置）

| 阀门 | 语义 | 当前值 | 建议值 |
|---|---|---|---|
| `kanban.max_in_progress` | **宿主级**上限：所有板 + ready/review 两条派发车道合计 running ≤ N（源码：`kanban_db_dispatch.py:2191-2197`） | **未设 → Windows 推导为 None = 不限**（`derive_default_max_in_progress` 在内存读不到时返回 None，`kanban_db_dispatch.py:1801-1813`；今天峰值 31 并发即无拦阻） | **8** |
| `kanban.max_in_progress_per_profile` | 同 profile 并发上限（blocked 延到下一 tick） | 未设 = 不限（本板 74/80 张卡 assignee=mas，**这是最有效的一个阀**） | **6** |
| `kanban.max_spawn` | **本板**并发上限：本板 running + 本 tick 新 spawn ≤ N（`kanban_db_dispatch.py:2161-2189`；未列入 config_defaults 默认表，但网关读取该键 `gateway/kanban_watchers_dispatcher.py:57`） | 未设 = 不限 | **6** |
| `hermes kanban dispatch --max N` | 手动单趟的 spawn 数上限（`dispatch --help`） | — | 手动补跑时用 `--max` |
| `dispatch_stale_timeout_seconds` | running 超 4h 且心跳断 1h → 回收（`kanban_db_dispatch.py:744-745`） | 14400（默认，保持） | 14400 |
| 内存压力护栏 | critical → 本 tick 不 spawn；elevated → 至多 1（`kanban_db_dispatch.py:2199-2218`） | 自动 | 保持 |

> 配置位置：**网关所在 home 的 `config.yaml`**（本机 = `C:\Users\xiach\AppData\Local\hermes\config.yaml`；网关 `hermes_home` 为该目录）。用 `hermes config set kanban.max_in_progress 8` 写入，勿手改文件。**设置只在网关启动时读取一次（`gateway/kanban_watchers_dispatcher.py:35`），改完必须重启网关生效。**

### 2.2 并发档位结论

- **稳态并发 ≤6–8**：今天有效节奏是 3–5 张/波；31 并发无收益只有事故。
- 30+ 并发只允许"全只读、互不共享文件"场景，且 lead 在波说明里显式标注。
- 每波开波前用 `hermes kanban stats` 看一眼 running 数；已达上限就**把新卡留在 todo**（不挂 ready 就等于不派）。

### 2.3 资源矩阵（哪些能并行、哪些必须串行）

| 任务类型 | 并行性 | 规则 |
|---|---|---|
| 只读取证 / 日志抓取 / API 拉取 / 文档整理 / 模板设计 / 技能沉淀 / 静态检索 | ✅ 可并行 | 波容量 4–6；互不写同一文件 |
| 基线类（UID 表、资产普查） | ✅ 但"先建一次，之后引用" | 后续卡引用产物，不重推 |
| 多卡 SSH 同一宿主 | ⚠️ 并发 ≤2–3 条链路 | 大日志一律远端 tar 一次、本地解包（禁止 N 次 ssh 逐文件读） |
| 动模拟器（adb input / 点屏 / 启停） | ⛔ **实例级互斥** | 一张卡一次只碰一台实例；同一实例同一时刻只有一个卡在碰；班次窗口内禁碰 |
| 重启 AUTO-MAS 侧车 / MAA / 改队列 / 发车 | ⛔ **全局串行** | 需人工在场；本板定位只读，写操作默认转生产流程 |
| 写生产配置 | ⛔ 禁止（本板） | 需要时另开生产流程卡 |

**历史证据**：t_bec38656 / t_56ea4c40 / t_aaacd03c 同晚都在碰同一批设备——冲突真实存在；t_bec38656 主动"19:45:30 停手让车"是正确姿势。

### 2.4 班次窗口

- **05:00–05:40**、**19:30–20:30**：以上资源类任务一律避开；只读任务可继续但不得 touch 实例。
- 跨窗口的卡：**不要用 worker 内 sleep 硬等**（今天 t_5c23c432 睡了 1,750s、t_56ea4c40 睡了 895s）。改用 `hermes kanban schedule <id> "等到 19:25"` 停放（Scheduled = 不计等待、不可派发），到点人工 `unblock` 唤醒（`unblock` 会回到正确阶段：todo/ready/review，`kanban_db.py:3651-3679`）；worker 内只留 ≤60s 短等。

### 2.5 波次节奏（装车法）

1. 开波：4–6 张，同波内错开资源类（不要一波全碰实例）。
2. 前波消化 **≥80%（done/blocked）** 再发下波；中途出意外先处理再续发。
3. 契约/基线类最先发（被引用面最广）。
4. 反面教材：20:35–20:37 重复波 12 张（全部 archive，白烧 2,572s/171 调用）；20:31–20:32 一发 11 张同时开跑。

---

## 3. 优先级与依赖

### 3.1 排序语义（先看事实）

派发顺序 = `ORDER BY priority DESC, created_at ASC`（`kanban_db_dispatch.py:2222-2228`，ready/review 两条车道同序）。即：**priority 是主排序键**（不是文档里说的"tiebreaker"），同优先级按创建时间 FIFO。⇒ 一张 P3 新卡会插到所有 P2 老卡前面。

### 3.2 取值约定（0–3）

| 值 | 语义 | 典型场景 | 约束 |
|---|---|---|---|
| **3** | 抢修/止损（express 车道） | 生产保全、班次硬故障、别人卡的"解锁钥匙"（先做它一片卡才能跑） | **同波 ≤2 张**；不得用于"只是比较重要的文档" |
| **2** | 常规主产出（**默认**） | 手册/审计/清单/验收/普查/演练 | 默认值；建卡不写就按 2 |
| **1** | 补充/深挖/可延后 | 依赖主产物的深挖、跨日回填、nice-to-have | 不与 2 抢 |
| **0** | 待定/记录 | 灵感、观察项、归档候选 | 一般不主动派；促升优先级后再跑 |

判据句：**"今天不做会怎样？"** 有钱/班次损失 → 3；只是晚点拿文档 → 1–2；没有后果 → 0。

现状提示：本板 80 卡中 3 用了 12 次、2/1/0 分别 36/24/8，同一系列（普查①–⑤ 用 1/2/1/2/3）取值混杂——新卡统一按上表，改已建卡用 `hermes kanban edit <id> --priority N`（会记 `reprioritized` 事件）。

### 3.3 parent 锁：何时用、何时不用

**用（产物依赖）**：
- 后置卡要用前置卡的**产物文件/结论**（总纲 t_5dcccff5 ← 8 个上游普查/深挖；技能沉淀 t_4e1d436d ← 3 份 playbook；调配手册 t_4d8f3ee3 ← 普查③+运维⑤）。
- 汇总/fan-in（≥2 上游）；复核后再发布；基线卡先建、引用卡后置。

**不用（别混淆）**：
- **仅共享资源** ≠ 依赖：两个都要碰实例的卡，用**串行白名单/时间窗**控制，parent 会白白空等。
- 只是"逻辑相关"：用 comment 交叉引用 id，不要挂 parent。
- 等真实世界事件（班次验收/闸门）：`schedule` + 定时 `unblock`（§2.4），或直接 cron 到点建卡。

**语义注意**：parents **全部 done** 才解锁（todo→ready 自动提升）。若上游是长卡（>20min），要么拆小上游，要么下游别挂在它后面。建卡时 `parents=[...]`；事后补挂 `kanban_link`；运行中发现要等别卡 → `kanban_block(kind="dependency")`（进 todo 自动续跑，不占人工）。

### 3.4 blocked 卡的写法（今天两张的评语）

- ✅ t_38b8ef3c：block 时写明"等到 19:25 再解除：任务是晚班 19:30 窗口的抓帧验收，提前跑没有数据"——等什么/何时解，lead 一眼可断。
- ⚠️ t_76b88661：靠 comment 补充背景（协作风报），可行但建议把"解锁条件"写进 block 备注本身。
- **规则**：blocked 必须写清 **在等谁 / 等什么 / 怎么解 / 复查时间**；四种 kind 选对（`needs_input`/`capability` 上人工；`dependency` 自动；`transient` 标记可能只是抖动）。同因 re-block 达 `BLOCK_RECURRENCE_LIMIT=2`（`kanban_db.py:111`）会转 triage——这是防 unblock 死循环的保险，别靠反复 unblock 硬顶。

---

## 4. 失败处理与重试

### 4.1 失败分类（源码级）

| 现象 | outcome/event | 计连续失败？ | 处置 |
|---|---|---|---|
| spawn 即崩（环境/解释器/profile 不存在） | `spawn_failed` / `crashed` | ✅ | 修环境（今天 = `python-3.14.7…hermes_cli not found` 回归，已修两次）；修完 `promote` |
| worker 非零退出 | `crashed` | ✅ | 看 `last_failure_error` 的 `Worker's last output` 尾行 |
| 超 `max_runtime_seconds` | `timed_out`（SIGTERM→SIGKILL 后重排队） | ✅ | 拆卡或调大 budget |
| 迭代预算用满（profile `agent.max_turns`，mas=150 轮） | `timed_out`（`Iteration budget exhausted (150/150)`） | ✅ | 拆卡；必要时（慎）提高 `agent.max_turns`——今日 `t_5c23c432` 实测 |
| 心跳断（>4h 且 1h 无心跳）被回收 | `reclaimed` | ❌（良性，不计罚） | 直接再派即可；先确认 worker 真死（pid+fingerprint 判活） |
| exit 0 但没调看板终态 | `protocol_violation` | 独立限额 **3**（`kanban_db_dispatch.py:923`） | 卡片正文追一句"最后必须 kanban_complete/kanban_block" |
| 同错误指纹 ≥3（系统级） | `crashed`（sticky） | **1 次即挂**（`kanban_db_dispatch.py:1268-1277`） | 挂起等人，不再自动重试 |
| worker 主动 block | `blocked`（4 种 kind） | — | 见 §3.4 |
| —— | `gave_up` = 自动止损落地事件 | — | 出现在 auto-block 时刻，报告里按"需人工"处理 |

默认阈值：`failure_limit=2`（`kanban_db_dispatch.py:36`；连续 spawn_failed/timed_out/crashed 计 2 次 → auto-block）。**重新指派（reassign）会清零该卡的连败计数。**

### 4.2 阈值取舍（怎么选）

| 场景 | 选择 | 理由 |
|---|---|---|
| 默认（环境稳定） | `failure_limit=2`（或卡上 `max_retries=2`） | 今天 10 次 crashed 全为同类环境问题，2 次止损 + 人工修环境是正确节奏 |
| 已知环境不稳（spawn 回归期） | 宿主级 `failure_limit=3–5`，或问题卡 `max_retries=3–5` | 每次重试也烧钱，只留给"确定是环境、每次重试成本低"的卡 |
| 有外部副作用的卡（实机操作/发车/写配置） | `max_retries=1`（一次即挂） | 第一次失败可能留下残留状态，必须先人工核查 |
| 长卡（>30min 预算） | 配 `max_runtime_seconds`（如 45m）+ 默认 2 次 | 超时也计失败 ⇒ 两次超时必挂，防"跑飞" |

### 4.3 重派标准动作（禁止裸 promote）

1. `hermes kanban show <id>` 读 `last_failure_error` **和** comment（worker 可能留了现场）；
2. 判类别：环境类（修环境）/ 卡面类（`edit --body` 补判据、缩小范围）/ 资源类（换时间窗）；
3. **在卡上 comment 写重试备注**：上次为何失败、本次改了什么（这是给下一次 worker 的"已确认事实"）；
4. `hermes kanban promote <id>`（或 `unblock`）→ 等下一 tick（60s）或手动 `hermes kanban dispatch --max 1`；
5. 连续两次同因失败 ⇒ 不再重试，开**专项卡**（标注 P3）处理根因。

### 4.4 止损升级链

```
单卡 2 连败 → auto-block（gave_up）
  → 巡检日清（§5.4）列出 crashed/blocked/gave_up 卡
  → 同因 ≥2 卡 → 环境专项卡（P3）
  → 系统级（同指纹 ≥3）→ 1 击即挂 + sticky，等人
```

---

## 5. 成本观测（每个任务的耗时/产出/空转记录方式）

### 5.1 三个数据源（权威顺序）

| 层 | 内容 | 用途 |
|---|---|---|
| **看板库** `kanban.db` | `task_runs`（started/ended/outcome/error/worker_pid）、`task_events`（spawned/heartbeat/reclaimed/gave_up…）、`task_comments`、`task_attachments` | 净跑时长、重试史、附件字节 —— **零开销、永远可用** |
| **回放** `logs/t_<id>.log`（本板 `logs/`，今天 68 份/21MB） | 逐条工具调用：类型/耗时/退出码/超时/被拦截/SSH 次数 | 工具调用数、工具耗时、sleep、空转标记 |
| **卡内** summary/metadata/comment | 结论、findings、重试备注 | 人类可读的交接 |

**worker 不需要自报成本数字**（外部可复算）；唯一必须自报的是 `artifacts`。

### 5.2 单卡成本快照（复制即用）

```bash
DB=C:/Users/xiach/AppData/Local/hermes/kanban/boards/maa-parallel-benchmark-20260926/kanban.db
SQ=/c/platform-tools-latest-windows/platform-tools/sqlite3
# 净跑时长 + 运行次数（running 卡按当前时刻估）
"$SQ" -readonly "$DB" "select t.id, substr(t.title,1,24) title, t.status, count(r.id) runs,
  sum(coalesce(r.ended_at, strftime('%s','now')) - r.started_at) net_s
  from tasks t left join task_runs r on r.task_id=t.id group by t.id order by net_s desc limit 20;"
```

回放统计（工具调用/耗时/超时/睡眠/SSH）：复用附件 `t_85025aea/analyze.py` + `t_85025aea/gen_csv.py`（各改 3 个常量：DB / LOGDIR / OUT 与 CSV 路径；OUT 目录先建）。

### 5.3 空转标记与告警线（单卡超线 ⇒ lead 复盘）

| 标记 | 告警线 | 对策 |
|---|---|---|
| sleep 硬等 | 单卡累计 ≥60s（§2.4 的例外之外） | 改 schedule/cron |
| 180s 终端超时 | ≥1 次 | 卡面写死搜索范围；批量命令拆开 |
| 同文件重复读 | 同一文件 ≥3 次 | 先 grep -n 定位；结论进 audits 供引用 |
| SSH 样板 | 单卡 >10 次 | 用 `mas_remote.sh` / 合并远端命令 |
| 审批拦截 | 单卡 ≥3 次 | 换写法（禁 python -c 等，§1.1 纪律） |
| 总量 | >600s 工具时间 或 >150 调用 | 拆卡 |

### 5.4 汇总节奏（每班次/每日一次）

1. 运行 `analyze.py` + `gen_csv.py` → `docs/audits/kanban_throughput_<date>.csv`；
2. 巡检一行摘要进每日报告：卡数 / 峰值并发 / crashed+gave_up / 重复波次数 / 高耗卡 Top3；
3. 健康度红线（周维度）：重复波 = 0、峰值并发 ≤8、blocked 平均停留 <24h、空转标记超线卡 = 0。

---

## 6. 防重复发卡

### 6.1 idempotency_key 规范

- 格式：`<scope>-<slug>-<yyyymmdd>`，例：`maa-bench-account-isolation`、`kanban-worker-launch-repair-20260926`。
- **周期卡/巡检卡/重发高危卡必带**（内核行为：同 key 的非归档卡存在时直接返回旧卡 id，不新建；`create --help`）。
- 现状：本板仅 4 张卡带了 key（`kanban.db` 实测）——新流程从下一波开始执行。

### 6.2 建卡前查重（三步）

1. `hermes kanban list --status running`（以及 `todo`）看有没有同题在跑；**一行查重**（worker 上下文必须前缀 `env -u HERMES_DELEGATED_CHILD_CONTEXT`，否则 CLI 以 "delegate_task child contexts cannot mutate…" 拒绝；lead 交互 shell 可省）：

```bash
env -u HERMES_DELEGATED_CHILD_CONTEXT hermes kanban list --json \
  | grep -o '"title": *"[^"]*"' | sort | uniq -d   # 有输出 = 有重复标题组
```

2. 关键词 SQL（git-bash 下 **中文 LIKE 会哑火**，用 ASCII 关键词或 python）：

```bash
# ASCII 关键词
"$SQ" -readonly "$DB" "select id,status,priority,substr(title,1,50) from tasks
  where status in ('todo','ready','running','review') and title like '%MCP%';"
```
   （中文关键词建议写成 .py 脚本跑，或用 `char(...)` 码点；见附注（§9）。）
3. 命中同题 ⇒ 不建新卡；要补充信息就落到**已有卡的 comment**（这是本板 36 次 commented 的正确用法）。

### 6.3 已发生重复的处置

- 保留**先建且在跑**的那张；新卡 `archive`（附一句"与 t_xxx 重复"）。
- 两都在跑且都产出了 ⇒ 保留质量好的，另一张 archive，把它的发现以 comment 汇总进保留卡。
- **重复对清单（21:40 二次复核更新）**：
  - 能力③ `t_64097d9a`（21:02）↔ `t_f662b13c`（21:07）：**已收口**——t_f662b13c 先完成（21:36）并交付本文档与模板；t_64097d9a 二次复核后收口（补充见 §9.4），未重复写盘；
  - 能力② `t_cf2dc452`（21:00）↔ `t_87409df9`（21:05）：双双 done（对比后留优即可）；
  - 数据① `t_183e9907`（21:05）↔ `t_597ec823`（21:09）：**仍并跑（21:40 实测）——建议 lead 立即 archive 后发那张（t_597ec823）止损**；
  - 数据② `t_718d07db`(todo) ↔ `t_d9f168b9`(archived)、总纲 `t_5dcccff5`(todo) ↔ `t_a344ec9e`(archived)：均已收敛为一张。
- 历史最大单笔：20:35–20:37 重复波 12 张（2,572s/171 调用白烧，全部 archive）。

### 6.4 发波前检查清单（lead）

- [ ] running 数 + 本波待发数 ≤ 并发上限（§2.2）
- [ ] 同题查重完成（§6.2）
- [ ] 周期卡带 `idempotency_key`
- [ ] 资源类卡与本波其它卡不冲突；班次窗口内无资源类卡
- [ ] 每张卡过一遍 §1.1 七段自检

---

## 7. 一次性配置清单（建议值）

写入 **网关 home** 的 `config.yaml`（`hermes config set <key> <val>`；改后**重启网关**生效）：

| 键 | 现值 | 建议 | 理由 |
|---|---|---|---|
| `kanban.max_in_progress` | 未设（Windows = 不限） | **8** | 宿主级保险丝；防任何来源的 fan-out 失控（今天 20:37 峰值 31 并发 = 无生效上限的迹象） |
| `kanban.max_in_progress_per_profile` | 未设 | **6** | 本板卡绝大多数是 mas（74/80）；波容量强制 ≤6 |
| `kanban.max_spawn` | 未设 | **6** | 板级并发（running+tick spawns）；实现支持但未登记默认表，注明来源 |
| `kanban.failure_limit` | 2（默认） | 2（保持） | §4.2 |
| `kanban.dispatch_stale_timeout_seconds` | 14400 | 14400（保持） | 4h+1h 心跳断 = 死卡回收 |
| `kanban.review_dispatch` | true | true（保持） | review 车道已开；reviewer 派发规则见 §8.3 |
| `kanban.auto_decompose` | true | 按流程定 | 本板流程是 dsh-lead 手动拆卡 ⇒ 若不想 triage 卡被 aux-LLM 自动消费，设 `false` |
| `kanban.dispatch_interval_seconds` | 60 | 60（保持） | 手动加速用 `hermes kanban dispatch`（不常驻） |

---

## 8. 运行手册（命令级）

### 8.1 只读核查（任何时候可跑）

```bash
B=maa-parallel-benchmark-20260926
hermes kanban --board $B stats
hermes kanban --board $B list --status running
hermes kanban --board $B show <id>          # 含 last_failure_error / 评论 / 事件
```

### 8.2 重派/止损（§4.3 的动作化）

```bash
hermes kanban show <id>
hermes kanban edit <id> --body "…（补充判据）"      # 卡面类
hermes kanban comment <id> "重试备注：上次 …；本次已 …"
hermes kanban promote <id>                          # 或 unblock（回原阶段）
hermes kanban dispatch --max 1                      # 加速一趟（可选）
```

### 8.3 review 车道（当前 0 张在用）

- 流程：实现完 `kanban_request_review(summary)` → 任务进 `review` → 派发器以 `sdlc-review` 技能拉起（reviewer 默认 = 卡上 assignee；**给 mas 的卡别用自审**，需先约定独立 reviewer profile，或保持"人工审"；也可 `request-review --reviewer <profile>` 显式指定，内核会先改派再派发）。
- 审阅通过 = reviewer `kanban_complete`；打回 = `kanban_request_changes`（不计 block 循环）。

### 8.4 等待类任务的两种正确姿势

```bash
hermes kanban schedule <id> "等到 19:25：晚班窗口数据"   # 停放（不计等待）
hermes kanban unblock <id>                              # 到点由人/cron 唤醒
# 或：用 Hermes cron 到点建卡/发卡（推荐；不占用看板状态）
```

---

## 9. 附录

### 9.1 源码锚点（v0.21.5，供复核）

| 断言 | 位置 |
|---|---|
| `DEFAULT_FAILURE_LIMIT = 2` | `hermes_cli/kanban_db_dispatch.py:36` |
| 派发排序 `priority DESC, created_at ASC` | `hermes_cli/kanban_db_dispatch.py:2222-2228` |
| `_STALE_HEARTBEAT_GAP_SECONDS = 3600`；4h 门限 | `hermes_cli/kanban_db_dispatch.py:744-745` |
| 协议违规独立限额 = 3 | `hermes_cli/kanban_db_dispatch.py:923` |
| 系统级崩溃 1 击即挂（sticky） | `hermes_cli/kanban_db_dispatch.py:1268-1277` |
| `derive_default_max_in_progress`（内存读不到 ⇒ None=不限） | `hermes_cli/kanban_db_dispatch.py:1801-1813` |
| `max_spawn` 为板级并发上限；预算逻辑 | `hermes_cli/kanban_db_dispatch.py:2161-2197` |
| 内存压力护栏（critical/elevated） | `hermes_cli/kanban_db_dispatch.py:2199-2218` |
| `BLOCK_RECURRENCE_LIMIT = 2`；unblock 回原阶段 | `hermes_cli/kanban_db.py:111, 3651-3679` |
| 调度器设置读一次（boot）；`max_spawn`/`max_in_progress*` 读取 | `gateway/kanban_watchers_dispatcher.py:35, 47-118` |
| `kanban.*` 默认值全表 | `hermes_cli/config_defaults.py:1861-1931` |
| `schedule`/`unblock`/`block --kind` 语义 | `hermes kanban schedule|unblock|block --help` |

### 9.2 今日数据快照（2026-09-27 21:30，可复算）

- 卡：80 张（done 25 / running 30 / todo 8 / blocked 2 / archived 15）；priority 分布 3:12 / 2:36 / 1:24 / 0:8。
- runs：completed 26 / crashed 10 / reclaimed 12 / blocked 3 / running 30。
- 事件：spawned 79 / claimed 79 / heartbeat 1,770 / gave_up 5 / dependency_wait 14 / promoted 7 / block_loop_detected 1。
- 21:08 定格成本：4,487 工具调用 / 30,049s 工具耗时（含 sleep 2,991s）/ 2,220 推理回合 / 峰值并发 31 / 88 次审批拦截 / 26 次 execute_code 全拒 / 14×180s 超时 / 382 次 SSH。
- 崩溃 10 条 = 5 张卡 × 各 2 次（t_b7b6e02f / t_786edd63 / t_9a361301 / t_4a46abc8，均 09-26；t_e4620caa，09-27 05:03），全部为 spawn 环境类（`hermes_cli` 解释器回归），已由 gave_up 正确止损；09-27 05:08 修复后未再复现（21:40 二次复核）。

### 9.3 附注

- **git-bash 下 sqlite3 中文 LIKE 的坑**：`title like '%看板%'` 会静默返回空集（编码不匹配）；用 ASCII 关键词、python 脚本、或 `like '%'||char(30475,26495)||'%'`（码点方式，实测可行）。
- **worker 上下文跑看板 CLI 的坑**（2026-09-27 21:38 实测）：worker/委派子上下文带 `HERMES_DELEGATED_CHILD_CONTEXT` 时，`hermes kanban list` 被守卫拒绝（`hermes_cli/kanban.py:153`），报 "delegate_task child contexts cannot mutate Kanban tasks via the CLI"；`stats / show / runs / log / assignees` 不受影响。需要 list（查重）时前缀 `env -u HERMES_DELEGATED_CHILD_CONTEXT`。
- **scratch 工作区完结即删**：产出必须落在 durable 路径（`D:\1-2\MAAOrch\docs\` 或 `runtime/reports\`）并在 `kanban_complete(artifacts=[...])` 声明；只留 scratch 的产物会丢。
- 本规范与 `docs/MAA_ROGUELIKE_PLAYBOOK.md`（发车）/ `docs/MAA_TASK_CONFIG_REFERENCE.md`（字段）是互补关系：本规范管"怎么派活"，两者管"活怎么干"。

### 9.4 二次复核记录（t_64097d9a · 2026-09-27 21:40）

- 源码锚点独立抽查：`kanban_db_dispatch.py:36 / 744-745`、`config_defaults.py:1861-1931` 通读——`dispatch_stale_timeout_seconds` 合并默认确为 **14400**（与 §2.1 一致）；
- 本轮补充：§0-2 / §1.4 / §4.1 迭代预算硬顶（150 轮 → `timed_out`，t_5c23c432 实测）；§6.2 一行查重命令（含 worker 上下文 `env -u …` 前缀）；§9.2 崩溃卡数勘正（6 张 → 5 张 × 2 次）；§9.3 worker CLI 守卫坑；
- §6.3 重复对清单刷新为 21:40 状态；本文档与模板均未重复覆盖写盘（先到者 t_f662b13c 的成品 + 本卡增量合并）。
