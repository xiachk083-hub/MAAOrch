收到时间预算提示，停止新探索，直接产出汇报。以下是完整交付。

══════════════════════════════════════════
交接演练汇报：2026-09-28 早班「谁没跑成 + 按规则补跑」判定
══════════════════════════════════════════

【合规声明】全程只读：未发车、未补跑、未改任何配置/队列/文档、未写任何文件、未开子代理。唯一副作用：调用 mas_status 时其代理按设计尝试「按需拉起」本地上游（127.0.0.1:19997），等待 45s 未起来后报错返回（日志指向 logs/web_ui.log）；未产生任何发车/写请求。

── 一、结论：今天哪些号没跑成 ──

先钉口径（引用 docs/MAS_RERUN_POLICY.md §1 + docs/HERMES_MAS_HANDOVER.md §10.4）：
- 「今天」= 班次日 2026-09-28，班次 [D 05:00, D+1 05:00)（现在 09:5x，只涉及早班；晚班 19:30 未到）。
- 「跑成」判据 = 该号在班次窗内有 DONE 记录（以 /api/history/search 当日记录为准，不看 LastProxyDate）。
- 「该跑」= 全名单 60 − 肉鸽机 l-* 5 − 停用 2（v、Kaori）− 在册未排班 1（hbznn119-官）= 52。

结论 A（严格缺口）：**0 个号没跑成**。52/52 全部有 DONE。
- 证据1：search_history(DAILY, 2026-09-28) 返回 52 个账号的逐次记录，每个号 ≥1 条 DONE（例：MikuKong [E 07:06→D 07:13→D 07:21]；睡个大鸡蛋 [E 08:04 超时→E 08:46→D 08:51→D 08:55]）。
- 证据2：runtime/reports/shift_report_20260928_daily.md（09:55 生成）：「应跑 52 ｜ 实跑 52 ｜ 缺口 0（未跑 0 / 仅失败 0）｜ 完成率 100.0%」。
- 证据3：list_schedule（mas-dispatch MCP）→ reachable=true，12/12 队列已发，due_not_sent=[]、late=[]、dispatch_alerts=0。

结论 B（失败面逐号，4 个号「末条记录为 ERROR」——这是 failed_names 口径下会进补跑候选的全部）：
1) hbznn119-日 — 末条 06:50:21 ERROR「MAA 在完成任务前退出」。取明细（get_history_data 读 02-50-21.json 的 log_content）：错误块为「本轮任务出现以下错误：自动公招」=**仅公招链失败** ⇒ 资源型。判定：**不补**，依据 **N1**（资源类等券自愈；policy §6.1 案例A 同款）。另注：本班次已有 DONE 05:55:09；「连续≥3天全败⇒转人工」的升级线今日未触发（已有成功），继续保持观察。
2) LEUNG — 末条 08:46:53 ERROR「任务被用户手动中止」。与台账交叉：runtime/mas_ledger.jsonl 同日有 run_profile「明日方舟-官-19 / LEUNG」实验（08:58:08 wait_timeout→dispatch/stop）+ probe_farm 08:58:21。判定：**不补**（三问#2 本班次已有 DONE 05:11/05:15/08:42；尾部失败=人工实验 stop，非系统失败）。
3) 帝国猛虎 — 末条 06:35:20 ERROR「MAA 在完成任务前退出」（06:31–06:35 三连）。与实验窗口重合：mas_dispatch_log 06:31:28 建/发「临时-按需-20260928_0631-1」(target=帝国猛虎)、06:46:14「人工放行：1 条在窗记录已关」；且同分钟同目录的 潮响 也三连失败（MAA_6 双脚本）。其 log_content 显示 MAA_6 在 06:36:40/06:36:51 **连续两次 GUI 启动**（并发现场）。判定：**不补**（已有 DONE 05:18/05:25；尾部属按需测试/同目录并发干扰，记观察项）。
4) 许大胖子 — 末条 07:51:04 ERROR「任务被用户手动中止」。台账：run_profile「明日方舟-官-6 / 许大胖子」08:02:39 wait_timeout→dispatch/stop。判定：**不补**（已有 DONE 06:25/06:33/07:45）。

结论 C（在册但今天没跑的 2 个，逐个定性）：
5) hbznn119-官（脚本 官-28，MAA_8/#51）— enabled=true 但 queues=[]：**从未排班**。依据 docs/audits/2026-09-26_hermes_mas_daily_view.md §5：「没有任何队列项引用 官-28 ⇒ 配置在册、但从未排班——不是"漏跑"，是"没排班"」；docs/ACCOUNT_HEALTH_PROFILES.md：「实例51 幽灵占用；09-23 起连续 18 次全败后无记录」。判定：**不补**（无队列可发；属配置决策项，转人工拍板：入队 or 停用）。
6) Kaori（官-25）— enabled=false 且无队列 ⇒ 不跑属预期，**不补**。
（l-*×5：酸酸酸酸/舒7ucky/Buling/用户_99606616/不要加班了——按口径排除，非缺口。）

结论 D：另有 25 个号班次内出现过 ERROR 但末条为 DONE（已自愈，含「先错后成」15 个：Gitano、LAZA、LEUNG、MikuKong、PAGI、hbznn119-日、him、rzzz、tbNick_2l3vf、tbNick_h56zy、૮ต蓝熹ตა、以七、史蒂芬芬、小熊附体、帝国猛虎 等）⇒ 一律**不补**（三问#2：班次窗内已有 DONE / N3 类）。
» 综合：**今天没有任何号满足「该补」条件，补跑候选集 = 空**，无需任何发车。

── 二、会执行但本次不执行的命令（dry-run/预演形态） ──

若存在「该补」候选，按 SOP（MAS_RERUN_POLICY §4-C / 技能 §11）我会先跑预演、绝不直接发车：
  预演：  mas_rerun(accounts=["<候选>"], dry_run=True)        # MCP 预演，仅回显 would_start
   或 CLI：python tools/auto_mas_rerun.py --accounts "<号1,号2>"   # 默认预演，只打印计划不发车
  真发车（需人工点头 + 过 P0–P3/守卫窗外/≤8/同号≤2次/班次日）：python tools/auto_mas_rerun.py --accounts "..." --apply
本次候选中为空，以上均未执行。

── 三、我实际执行/验证的（命令 + 关键输出片段） ──

1. list_schedule（MCP）→ {"reachable":true,"sent_today":[日常定时1..12 全],"due_not_sent":[],"late_beyond_grace":[],"dispatch_alerts_count":0}
2. search_history（MCP，mode=DAILY，2026-09-28..2026-09-28）→ 52 账 | 例："hbznn119-日" index 8 条：…05:55:09 DONE / 05:59:48、06:24:40、06:50:21 ERROR…
3. get_history_data（MCP）×2：
   - hbznn119-日 02-50-21.json → log_content 尾部 "[ERR] 任务出错: 自动公招 … 本轮任务出现以下错误：自动公招"（N1 铁证）
   - 帝国猛虎 02-35-20.json → "MAA GUI started" 06:36:40 与 06:36:51 相邻两次（并发现场）
4. 本地只读文件：
   - runtime/reports/shift_report_20260928_daily.md：「应跑 52｜实跑 52｜缺口 0｜100.0%（87/119 尝试级）」
   - runtime/reports/shift_alert_20260928_daily.json：events=[] 、send=false
   - runtime/reports/maa_errors_2026-09-28.json：window [09-28 05:00, 09-29 05:00)；条目含「客户端未就绪/资源类」分型
   - runtime/mas_ledger.jsonl：14 行（rerun 04:18 发车 hbznn119-日+河清灯；run_profile 08:02/08:58；probe_farm 08:58；farm 09:31）
   - runtime/mas_dispatch_log.jsonl：05:00:05–05:33:05 12×dispatch_ok；06:09 拒发 slot_busy；06:31:28 帝国猛虎按需；06:46:14 release；07:47:52 stop_ok
   - runtime/reports/mas_account_master_20260928.json：60 账号；v、Kaori 均 disabled；hbznn119-官 enabled 但 queues=[]
5. SSH 只读：E:\AUTO-MAS\history\2026-09-28 桶目录列表 → 与 52 号集合一致（hbznn119-日 等，官-28 不在）
6. 服务面探测：8765/19997 未监听（mas_status 上游不可达）；19999 Node 前门在

── 四、依据清单（文档 / 字段 / 工具） ──

文档：
- docs/MAS_RERUN_POLICY.md（三问判定、P0–P3、N1–N4、Y1–Y3、§6.1 案例A hbznn119-日、§6.2/6.4 样例）
- docs/HERMES_MAS_HANDOVER.md（§6 硬边界、§7 准入五条、§10.4 以 history 为准）
- docs/audits/2026-09-26_hermes_mas_daily_view.md（hbznn119-官「从未排班」结案）
- docs/ACCOUNT_HEALTH_PROFILES.md（hbznn119-官 故障簇与摘除、停用号）
- 技能 auto-mas-host-ops → references/mas-error-triage.md（§6 补跑判据）、技能 maaorch-queue-triage §11
字段：
- history：index[].status(DONE/ERROR)、error_info{ts:原文}、jsonFile；文件名 hour=本地−4 怪癖
- log_content 内「本轮任务出现以下错误：…」＝资源类判据（N1）
- ledger：kind=rerun/run_profile/probe_farm；dispatch_log：kind=dispatch_ok/dispatch_refused/release/stop_ok；master：enabled/queues/maadir/idx
工具：auto-mas-monitor(search_history/get_history_data)、mas-dispatch(list_schedule)、本地 grep/ls、ssh 只读
（对照口径：硬规矩1「看用户 Tag 不看 LastProxyDate」——本次以 history DONE 为准并一致。）

── 五、信息缺口 / 弯路 / 建议补的文档 ──

1. mas_status（maaorch-daily MCP）不可用：上游 19997 未常驻、按需拉起 45s 失败（logs/web_ui.log）。而技能 §11 与策略 §附A 都把它列为第一入口 ⇒ 建议补一份「上游不可达时的替代取数路径」一页纸（search_history / shift_report / list_schedule 组合已够用，本次即如此）。
2. 班次日报的排除清单文字不完整：只写「l-* 5 + 停用 1(v)」，但「应跑 52」实际还排除了 Kaori（也停用）与 hbznn119-官（未排班）——推导没写明。建议日报输出「排除原因×号数」全表，否则交接者会以为 54/58≠52 是账不平。
3. 「尝试级成功率 87/119」口径复算不出（我按 history 原始记录手工数约 145 条/窗口内约 138 条），分子分母窗口未文档化。建议 mas_shift_report.py 的统计口径写入文档一行注。
4. history 的 date 字段语义未写明（是尝试开始还是结束？实测 06:50:21 记录对应 MAA 日志 06:51:30 起 ⇒ 更像开始时间；已知的只有「文件名 hour−4」怪癖）。建议补进 docs/MAA_LOG_DICTIONARY.md。
5. 人工实验（run_profile / probe_farm / 按需）与生产失败的留痕会混在一起：本次 LEUNG/许大胖子/帝国猛虎 的尾部 ERROR 若不读 ledger 就会被误判成「真失败该补」。建议实验性发车强制打标（note=manual-experiment）并由巡检跳过带标记录。
6. 演练环境小坑：本机 bash 无 curl、`powershell -Command` 与内联脚本在单查询模式被拦；中文路径经 ssh/cmd 做跨日批量核对不稳（5 日循环 grep 未产出可信输出，故 hbznn119-官 的跨日结论改用仓库文档取证）。建议交接文档注明「新会话取数首选 MCP 工具 + 本地只读文件，避免 curl/powershell/内联脚本」。

（完）如需我把其中任何一步做成可复跑探针脚本或补充审计文档，说一声即可——本次为演练约束，均未落盘。