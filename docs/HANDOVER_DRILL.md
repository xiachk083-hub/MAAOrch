# 交接演练：零上下文新会话仅凭 docs/ + 技能完成真实任务

> 卡：`t_c2d42071`（assignee `mas`）｜执行：2026-09-28 09:45–10:35 CST｜性质：**只读演练**（不发车、不补跑、不改配置/文档）
> 目的：验证「掌握」成立性 —— 两个**没有任何对话上下文**的新会话，只拿到用户口吻的任务 + `docs/` + 技能，能否完成真实运维任务。
> 证据目录：`docs/audits/2026-09-28_handover_drill/`（prompt 全文、stream-json 原始事件、工具序列 digest、最终汇报、him/v 实机记录摘录）

---

## 0. 一句话结论

**成立，但打折扣。** 两场演习都在 ~20 分钟内给出了**可用且大体正确**的结论（A：当刻无缺口、4 个「末条 ERROR」号均不该补；B：`stage_not_open` 定因正确、还纠正了题面错误），并且都找到了关键文档（`MAS_RERUN_POLICY.md`、`MAS_PROBE_FARM.md §9.1`、能力台账）与技能（`maaorch-queue-triage`、`auto-mas-host-ops`）。
折扣集中在三处：**① 取数入口不收敛**（「今天谁没跑成」没有单点入口，`mas_status` 依赖本地上游、日报会重生成、history 落盘延迟——A 的第一入口就 45s 空等失败）；**② 口径需要单点化**（资源关开放日、日报排除清单、实验发车污染等全靠临场拼凑）；**③ 演练题面本身也需 review**（本卡题面把 LS-5 写成「龙门币关」，实际仓库口径是作战记录线——B 纠正了它）。缺口清单见 §5（A/B/C 共 15 条 + D 2 条演练设计发现）。

---

## 1. 演练设计

| 项 | 内容 |
|---|---|
| 会话形态 | `hermes chat --oneshot --format stream-json` 新起两个**零上下文**会话（无历史对话；携带 profile 记忆/技能，即本机真实新会话形态） |
| 机队 | 会话 A `20260928_095051_cb27e0` ｜ 会话 B `20260928_095051_7b2d42` ｜ 模型 `deepseek-v4.1-flash`（profile 默认） |
| 预算 | 各 `--max-turns 100`、`--run-budget 1200s`（A 实际 36 次工具调用 / ~21min，B 54 次 / ~22min；均被预算收口正常结束） |
| 题面 | A：「查一下今天（2026-09-28）哪些号没跑成，并按规则补跑」；B：「LEUNG（明日方舟-官-19，跑在 MAA_5）今天上午尝试刷 LS-5 但没有刷成……为什么？还能不能刷？下一步怎么处理？」 |
| 约束 | 全程只读/dry-run、不发车、不改配置/文档/文件、不开子代理；**动手前先说依据（引用文档/字段）**；最后汇报须含：结论+证据、依据清单、**信息缺口**。prompt 全文见证据目录 |
| 执行窗口说明 | 演练与真实班次**并发**（板面其它卡同时在跑），数据在演练期间发生过漂移（见 §2.4「him 案例」）——这本身是有效发现 |

---

## 2. 演习 A 记录（补跑判定：谁没跑成 + 按规则补跑）

### 2.1 走的路（工具序列摘要，36 次调用）

技能先行：`skill_view(maaorch-queue-triage)` + `skill_view(auto-mas-host-ops/references/mas-error-triage.md)` → 读 `HERMES_MAS_HANDOVER.md`、`MAS_RERUN_POLICY.md`（分两次全读）→ 本地只读（dispatch log/state、master、reports）→ MCP 工具面（`mas_dispatch.list_schedule`、`auto_mas_monitor.search_history/get_history_data`、`mas_status` 尝试）→ ssh 只读（history 桶目录）。

### 2.2 结论（最终汇报摘要）

- 先钉口径：班次日 = [09-28 05:00, 09-29 05:00)；应跑 = 全名单 60 − 肉鸽 l-*5 − 停用 2 − 在册未排班 1（hbznn119-官）= **52**；判据 = history 班次窗内有 DONE。
- 结论 A：**当刻 0 个号没跑成**（52/52 有 DONE；证据 = search_history 直接拉取 + 09:55 版日报「实跑 52/缺口 0」+ list_schedule 12/12 已发）。
- 结论 B：4 个「末条记录为 ERROR」的号逐个定因，**全部不补**：hbznn119-日（仅公招链⇒N1 资源类）、LEUNG（尾部是 run_profile 实验 stop）、帝国猛虎（按需实验 + MAA_6 双 GUI 并发干扰）、许大胖子（run_profile 实验 stop 所致）。
- 结论 C：hbznn119-官（enabled 但 queues=[]，**从未排班**，非漏跑）与 Kaori（停用）——不补，转配置决策。
- 综合：补跑候选集 = 空；给出「若该补则先预演」的 dry-run 命令形态，未执行任何写动作。

### 2.3 核验（本卡复核）

| 演习主张 | 复核结果 |
|---|---|
| 当刻 52/52 有 DONE、无缺口 | **成立**（09:55:39 版班次日报「实跑 52｜缺口 0」；我另抽验 him 的 2 DONE/1 ERR 原始记录） |
| hbznn119-日 失败=仅公招链（N1） | **成立**（`maa_errors_2026-09-28` 归类「资源类/可忽略」；与 policy §6.1 案例 A 同款） |
| LEUNG/许大胖子 尾部失败=实验 stop | **成立**（`run_profile` 08:58/08:02 wait_timeout→dispatch/stop，ledger 有痕） |
| hbznn119-官 = 未排班（非缺口） | **成立**（account master：queues=`—`；`2026-09-26_hermes_mas_daily_view.md` 已有结论） |
| 「日报排除清单不完整」 | **成立**：应跑 52 vs 号表 60 的推导缺 `Kaori` 与 `hbznn119-官` 两项说明（见 §5-A3） |
| 「尝试级 87/119 复算不出」 | 认可为**口径文档缺口**（见 §5-A4），该数字由 `mas_shift_report.py` 产出、未找到文档化定义 |

### 2.4 him 案例（演练期数据漂移，重要）

演练前基线（我 09:44 读的 09:38 版日报）：**缺口 1 = him**。演习 A 执行时（09:52–）数据已是「0 缺口」。复核定案：

| 时刻 | 事件 |
|---|---|
| ~09:06 | him 的任务启动（第 1 尝试 09:06:01 登录失败，第 2 尝试 09:09:50 成功，第 3 尝试 09:13:25 起跑）——**由谁发车未定因**（`mas_dispatch`/`mas_ondemand` 台账均无留痕，见 §5-C3） |
| 09:37:58 | 09:38 版日报生成：him 未跑 ⇒ 触发 `gap_over_n` 告警（**假缺口**，数据迟到所致） |
| 09:48:38 | 缺口告警被自动清除（`shift_report_state.json`） |
| **09:51:42** | him 第 3 尝试**跑完**（him 日志尾部：`任务已全部完成！(用时 0h 37m 56s)`，含 1-7 掉落）——history 文件随之落盘（host mtime 09:51） |
| 09:55:39 | 日报重生成（卡 `t_1a15894c`）：缺口 0 |

⇒ 教训：**「谁没跑成」是移动靶**——单号任务可跑 ~38 分钟；history 记录在任务收尾才落盘；同日两次生成的日报可对同一号给出相反结论而**无任何迟到数据注释**。（→ 缺口 A2）

### 2.5 演习 A 自报的信息缺口（原文要点，已并入 §5）

`mas_status` 上游 19997 不常驻/拉起失败（建议补「上游不可达时的替代取数路径」页）；日报排除清单不完整；87/119 口径未文档化；history 的 `date` 字段语义未写明；实验性发车与生产失败在 history 里混着；本机 bash 无 curl、PowerShell/内联脚本在单查询模式被拦。

### 2.6 弯路与返工（是否走错路 / 用错 API）

- **无重复劳动、无重复发现、无写错 API**：它用的 MCP 工具（`list_schedule`/`search_history`/`get_history_data`）均为只读类，且在动手前已列依据（技能+policy 文档）。
- 代价点 1：首入口 `mas_status` 45s 空等失败（→ C1），之后才切到日报/历史直取。
- 代价点 2：花约 1/3 调用自行推导「应跑 52」名单（在 master/queues 结构间摸索）；而该名单在日报 §1 与 `account_health` 里本有现成版本——顺序上「先拿现成再自算」更省。
- 代价点 3：对「尝试级 87/119」复算 3 次未果（→ A4 口径缺口）。

---

## 3. 演习 B 记录（LEUNG × LS-5 定因：为什么没刷成）

### 3.1 走的路（54 次调用）

先列 docs/tools 清单 + 技能（同上两个）→ `search_files` 定位 LEUNG/LS-5 全仓库引用 → 读 `MAS_PROBE_FARM.md`、探针证据（`docs/audits/2026-09-28_mas_probe_farm_live.*`）、`stage_capability_qa/` 复核材料 → ssh 只读直取证（LEUNG 09-28 四条记录、04-46-53.log、09-07 v 的 LS-5 成功记录、MAA_5 resource 里 LS-5 引用）→ 本地台账 `stage_capability.json`、`STAGE_CAPABILITY.md`、`services/farm_dispatch.py`、KB（`D:/1-2/MAS-KB`）交叉。

### 3.2 结论（最终汇报摘要）

- **定因**：卡在 MAA 的「当日开放关」判定——`GetFightStage: from ["LS-5"], selected null` + `关卡计划内没有当日开放的关卡` + `理智作战: 任务跳过`；归类 `failed / stage_not_open`。**不是账号/模拟器/注入问题**（探针已还原配置，前/后快照一致）。
- **能不能刷**：今天不能（轮换/当日不可选），且**不是「这号不行」**——同窗口 1-7/剿灭正常；09-07（周一）v（官-5）有 LS-5 成功实证。
- **下一步**：今天别重试；开放日复测（`mas_probe_farm --probe-only --retry-after-fix "LS-5 开放日复测"` 先预演，需授权才 `--apply`）；给了一行只读判法（当天 MAA 日志 `selected LS-5`=开放 / `selected null`=不开）。
- **主动纠错**：题面「LS-5（龙门币关）」不准确——仓库口径 LS=作战记录主产（兼掉龙门币）、纯龙门币是 CE 系；并指出 `RES_DAYS`（mon/thu/fri/sun）与今日 MAA 判定**冲突**、列为待核项。

### 3.3 核验（本卡复核）

| 演习主张 | 复核结果 |
|---|---|
| `stage_not_open` 定因 + 判据行 | **成立**（`MAS_PROBE_FARM.md §9.1` 同口径；LEUNG 04-46-53 记录 `not_open/skipped`） |
| 配置已还原、非注入问题 | **成立**（探针 summary：restore=restored、7 键比对一致） |
| `RES_DAYS` 冲突 | **成立**（我复核 `services/farm_dispatch.py:23-28`：exp(LS-6→LS-5) 开 mon/thu/fri/sun；今日为周一却判不开——悬而未决，见 §5-B1） |
| 「LS-5=作战记录线」纠错 | **成立**（`stage_efficiency.json` LS-5 `main_drops`=高级/初级/中级作战记录×3/1/1；`RES_DAYS` lmd→CE、exp→LS 与之一致） |
| v 09-07 LS-5 成功实证 | **成立**（我复核 16-54-50.json：drop_statistics 含 LS-5、Success） |
| 「-4h 文件名怪癖」差点误读 | 记录属实（08:46 记为 04:46；该口径目前只埋在审计文档里） |

### 3.4 演习 B 自报的信息缺口（原文要点，已并入 §5）

「LS-5 到底哪天开」无单点文档、仓库周表与 MAA 运行时冲突无人解释；MAA 本地开放表（Supplies.json / StageActivityV2.json）的位置与读法没文档、读原文被安全策略拦；「同系列回落」（计划 LS-6、实开 LS-5）机制只见于 QA 笔记；−4h 怪癖埋得太深；题面 LS-5 语义错误（已纠）；探针止损的 `maa_result="任务被用户手动中止"` 极易误读为真人中止。

### 3.5 弯路与返工（是否走错路 / 用错 API）

- **无重复劳动、无用错 API**；且表现亮点：在仓库里已有现成定因（`MAS_PROBE_FARM §9.1`）的情况下，它仍独立取证交叉验证（4 条 history 记录 + 失败日志 + v 的 09-07 成功记录 + 台账），并对题面错误前提做了纠正而不是将错就错。
- 代价点 1：尝试读 MAA 本地 resource 原文两处均被安全策略拦（→ C2/B3），改用 ssh + `findstr` 只读绕行——**方法正确但多花调用**。
- 代价点 2：KB（`D:/1-2/MAS-KB`）命中对其问题增量有限，绕了一小圈后回到仓库数据。

---

## 4. 只读合规核验

- **写动作审计**：两场会话的工具序列中无 `write_file`/`patch`/`--apply` 成功的调用（B 的 `execute_code` 被安全策略拦截、A 的 PowerShell 内联被拦）；`mas_ledger.jsonl` / `mas_dispatch_log.jsonl` / `mas_ondemand_log.jsonl` 在执行窗口（09:52–10:12）内**无**来自演习会话的写记录；`git status` 无演习产生的文档/代码变动。
- **唯一副作用（候选，已核实归因）**：A 调用 `mas_status` 时，MCP 代理按设计尝试「按需拉起」本地上游（`127.0.0.1:19997`），45s 未达即失败返回（09:5x）。事后复核：`main_web.pyw`（pid 36164）实际于 **10:12:20** 启动，10:12:26 由服务自身 tracker 自写 `config/activity_stages.json` 的 `ts` 字段；时间与 10:16 巡检 job（`52b88b0ffca0`，其输出同样报「19997 拉起 45s 不可达」）的修复动作吻合，疑似巡检 job 按修复 hint 拉起。结论：**非演习会话的写动作**；且 `config/activity_stages.json` 在演练前已是 git dirty（历史未提交修改），非本次产生。
- **约束形态**：本演练为「prompt 自律 + 事后审计」的软隔离（未做技术沙箱）；如需强隔离，下轮可给演练会话单独 profile / 关写工具面。

---

## 5. 文档缺口清单（逐条：哪一页缺什么 → 改进建议）

> 全部有本次演习证据支撑；**改进建议交 lead 拍板，本卡不擅自改文档**。

### A. 取数与日报类

| # | 缺什么（落点） | 演习证据 | 改进建议 |
|---|---|---|---|
| A1 | **「今天谁没跑成」没有单点入口/一页纸**。入口散在 `mas_status`/`shift_report`/`account_health`/`never_ran`/history 之间；`MAS_SHIFT_PLAYBOOK.md` 没有「只做这一个问题」的取数路径 | A 先走 `mas_status` 失败（45s），再拼 4 条路径才收口；最终建议原文「补一份『上游不可达时的替代取数路径』一页纸」 | 在 `MAS_SHIFT_PLAYBOOK.md`（或新页《补跑判定速查》）列**取数优先级 + 替代路径**：shift_report → search_history/history 桶 → account_health；注明各源新鲜度；并**对齐两个「未跑」口径**：shift_report 的「缺口」= 已发车但应跑未跑，巡检 job 的「未跑」= 含未到点的定时项（10:16 巡检输出「未跑 6」= 5 个未到点 + hbznn119-官，易被误读为缺口） |
| A2 | **日报重生成 / history 落盘延迟无说明**：单号任务可跑 ~38min、记录在任务收尾才落盘；同日两次报告结论相反且无注释；缺口告警可被迟到数据自动清除 | §2.4 him 全程时间线（09:38 缺口1 → 09:51:42 跑完 → 09:55 缺口0） | 日报加「数据截至/迟到落盘」注脚；`gap_over_n` 告警文案加「可能为迟到数据，先复核 in-flight 任务」；巡检/补跑判定文档写明「盘缺口前先查该号是否正在跑」 |
| A3 | **日报排除清单不完整**：写「l-*×5 + 停用 1（v）」，但应跑 52 vs 号表 60 还差 `Kaori`（也是停用）与 `hbznn119-官`（enabled 未排班） | A §五-2；我复核报告 §1 注与 account master | `mas_shift_report.py` 输出「排除原因 × 号数」全表（含未排班项），避免交接者以为账不平 |
| A4 | **尝试级成功率 87/119 口径未文档化**（复算不出分子分母窗口） | A §五-3 | 统计口径（窗口、尝试定义）写一行注到 `mas_shift_report.py` 输出或 `docs/` 对口页 |
| A5 | **history 时间语义埋没**：`date` 字段是开始还是结束未写明；文件名 −4h 怪癖只在审计里 | A §五-4；B §三-4（差点把 08:46 读成 04:46） | 提升到 `docs/MAA_LOG_DICTIONARY.md` 顶部「时间与文件名」小节（含 −4h 对照） |
| A6 | **实验性发车污染 history**：`run_profile`/`probe_farm` 的止损记录（`任务被用户手动中止`）与生产失败同池；A 靠 ledger 交叉才没误判，B 特别提醒勿读成真人中止 | A §五-5；B §三-6 | ① 巡检提示词/判定文档写明「先查 ledger 同日实验记录再判失败」；② probe 记录层补注释或改文案（剔除「用户手动」误导） |

### B. 关卡 / 能力台账类

| # | 缺什么（落点） | 演习证据 | 改进建议 |
|---|---|---|---|
| B1 | **资源关开放日无单点真值**：`RES_DAYS`（mon/thu/fri/sun）与今日 MAA 运行时判定冲突，无人解释 | B §一-2/§三-1/§三-7；我复核 `services/farm_dispatch.py:23-28` | 新页《资源关轮换速查（以 MAA 运行时为准）》+ 只读查法；并建卡核实 09-07 vs 09-28（读 MAA resource 原文、MAA 周表/缓存） |
| B2 | **`stage_not_open` 语义未进中央文档**：只在 `MAS_PROBE_FARM.md §9.1` 教训与 QA 笔记 | B 全文依据路径 | 把「stage_not_open = 今日不可选，不是这号不行；挑试探目标先选当日有成功记录的关」提升到 `STAGE_CAPABILITY`/巡检/GLOSSARY |
| B3 | **MAA 开放表文件位置未文档化**（`resource/tasks/Stages/*.json`、`cache/gui/StageActivityV2.json`） | B §三-2（想读原文，无路径文档） | 机制文档补「开放日表在哪、怎么只读查」一行 |
| B4 | **「同系列回落」（计划 LS-6、实开 LS-5）机制无正式文档**（只在 QA 笔记） | B §三-3 | 并入 MAA 机制类文档（如 `MAA_TASK_MECHANICS.md`） |
| B5 | **LS/CE 语义易混**：本卡题面都把 LS-5 写成「龙门币关」 | B 主动纠错；我复核 `stage_efficiency.json`（LS-5 main_drops=作战记录） | `GLOSSARY.md` 补一行代号对照（LS=作战记录／CE=龙门币／CA／PR…） |
| B6 | **日志字典未覆盖 `GetFightStage / selected null / 关卡计划内没有当日开放的关卡`**（只覆盖了「任务跳过」） | B 实际使用的判据行；我 grep 确认 | `MAA_LOG_DICTIONARY.md` 补这三行（附语义与只读判法） |

### C. 工具与通道类

| # | 缺什么（落点） | 演习证据 | 改进建议 |
|---|---|---|---|
| C1 | **`mas_status` 依赖本地上游 19997**（不常驻；拉起失败时 45s 空等、报错含日志路径但无替代指引） | A §五-1；我复核 19997 状态在窗口内漂移（09:5x 未监听 → 10:14 LISTENING）；**巡检 job 自己的 10:16 输出同样报「拉起后 45s 不可达」——系统性故障而非演习偶发** | 与 A1 合并落地：工具侧改「快速失败 + 替代路径提示」，文档侧给替代命令 |
| C2 | **无人值守环境的取数摩擦**：单查询/headless 下 PowerShell 内联、heredoc、`python -c`、`execute_code` 均被安全策略拦；本机 bash PATH 无 curl | A §五-6；B 多处 BLOCKED；本卡执行者同样被拦 | 交接文档加「新会话/无人值守取数首选：MCP 工具 + 本地文件 + 仓库脚本；避免内联脚本」一节；或给演练/巡检会话配 allowlist |
| C3 | **发车留痕不完整**：him 09:05–09:06 的发车来源在 `mas_dispatch`/`mas_ondemand` 均查不到（疑似直连 POST 或人工）⇒「谁发的车」无法定因 | §2.4 | 所有发车路径（含直连 `POST /api/dispatch/start`）统一写 ledger/审计（至少记调用方标记）；巡检报告加「占用者/发起者」列（对应既有 G12） |

### D. 演练设计发现（给下一次交接演练）

| # | 发现 | 建议 |
|---|---|---|
| D1 | 「先说依据再动手」要求有效：两场都先列了依据链；但**演练题面本身也会写错**（LS-5 语义），题面应像交付物一样被 review | 下次演练前用 30 秒对题面做术语核对（或有意埋错观察纠错能力——本次 B 的表现证明该题面错误可被纠正） |
| D2 | 演练窗口与真实运行重叠 ⇒ 「正确答案」漂移（him）；若要在稳定数据上做**复跑对照**，应取固定 date 快照或选静默窗 | 复跑建议：`--date 2026-09-28` 类只读复盘放 12:30 后；对照基线以「同一时刻的 shift_report + search_history」为准 |

---

## 6. 附：证据与复跑

- **证据目录**：`docs/audits/2026-09-28_handover_drill/`
  - `drill_A_rerun.prompt.md` / `drill_B_leung_ls5.prompt.md` —— 题面全文（含只读约束）
  - `drill_A.stream.jsonl`（1345 事件）/ `drill_B.stream.jsonl`（1356 事件）—— 原始 stream-json（含全部工具调用与回执）
  - `drill_A.digest.md` / `drill_B.digest.md` —— 工具序列 + 结果摘要（人工可读）
  - `drill_A.final.md` / `drill_B.final.md` —— 两场最终汇报全文
  - `drill_A.stderr.log` / `drill_B.stderr.log`、`him_05-13-20.log`、`him_and_v097_records.txt` —— 会话 stderr、him 任务收尾日志、him/v 原始记录摘录
- **复跑**（同样零上下文）：
  ```bash
  hermes chat --query-file <prompt.md> --oneshot --format stream-json \
      --in D:/1-2/MAAOrch --max-turns 100 --run-budget 1200
  ```
- 会话可回翻：`hermes chat --resume 20260928_095051_cb27e0` / `--resume 20260928_095051_7b2d42`（或在 `~/.hermes` 会话库检索）。
