收到——时间预算已到，我用现有证据收口，不再新增探查。以下是最终汇报。

════════════════════════════════════════════
交班演练 · 只读复盘
LEUNG（明日方舟-官-19 / MAA_5）× LS-5 未刷成 — 定因、能否、下一步
════════════════════════════════════════════

本演练零写动作：未发车、未补跑、未改任何配置/队列/文档；全部查询均为只读（本地文件、SSH 只读、REST GET、KB 检索）。凡涉及写动作只在本报告中以"将执行的命令"列出。

────────────────────────────────────────────
一、结论
────────────────────────────────────────────

1）为什么没刷成（定因 + 证据链）

定因：不是账号问题、不是模拟器问题、不是配置注入失败——卡在 MAA 自己的"当日开放关"判定：LS-5 不在当日开放列表，整段"理智作战"被跳过，根本没开打，所以没刷成（归因 stage_not_open）。

证据链（按时间）：
- 实机记录：E:\AUTO-MAS\history\2026-09-28\LEUNG\ 共 4 条：两条清晨班次（05:11:40 空掉落 Success；05:15:56 刷 1-7 Success——账号/线路正常）；两条属于 08:42–08:58 的定向探针（文件名 04-42-24 / 04-46-53）。命名规则 hour=本机小时−4（08:46 记为 04:46），出处 docs/audits/2026-09-27_maa_errors_triage.md:117。
- 探针日志（04-46-53.log）三连：
  GetFightStage: from ["LS-5"], selected null → 关卡计划内没有当日开放的关卡 → 理智作战任务跳过
- 记录 JSON：not_open=["LS-5"]、skipped=["理智作战"]、maa_result="任务被用户手动中止"（这是探针超时止损，不是真人中止，勿误读）。
- 探针结论文档：docs/MAS_PROBE_FARM.md §9.1（窗口 08:42–08:58）= failed / stage_not_open；runtime/probe_farm/pf_20260928_084240_LEUNG/summary.json 同口径。
- 台账 + 独立复核：runtime/reports/stage_capability.json 中 LEUNG|LS-5 = 1 次、stage_not_open、last_failure=2026-09-28；docs/audits/2026-09-28_stage_capability_qa/verify_final.md 复核一致（selected null ×1），且它属于"仅 stage_not_open 失败"的 3 个特例行（另两个：帝国猛虎/SR-8、拍前看简介/AT-6）。
- 排除项：探针以临时注入（前/后快照）+ 读回验证把 LEUNG 关卡切到 LS-5，跑完已还原（现配置仍为 SR-7+剩余 1-7）——"没刷成"不是配置没切对。

2）这个号现在能不能刷 LS-5

- 今天（09-28）不能：MAA 运行时判定"当日不开放"，实测被跳过（上链）。这是"轮换关当天不开"，不是"这号不行"——依据 docs/STAGE_CAPABILITY_QA.md 的 R2 读法：仅 stage_not_open 的行应读成「当日不可选（活动/日轮换关）」，并交叉引用 MAS_PROBE_FARM 的教训（stage_not_open=今天打不了，不是这号不行）。
- 账号能力无不良证据：同窗口 1-7/剿灭正常；同服账号 v（官-5）在 09-07（周一）真实刷成 LS-5 5 次（Success；同一次是 MAA"同系列回落"：计划选 LS-6、实开 LS-5，掉落 龙门币1800+作战记录）。台账里 09-07 亦有 LS 系多号成功记录。
- 要"钉死能刷"还差一次开放日实证。注意口径冲突：仓库唯一周表 services/farm_dispatch.py RES_DAYS 说 exp(LS-6→LS-5) 开 mon/thu/fri/sun，与今天"不开"矛盾——该表是调度侧近似，未必等于游戏真相；开放日应以 MAA 运行时判定为准（本次未能读到 MAA 本地表原文，见"信息缺口"2）。

3）下一步建议（含将执行的命令，只读/预演优先）

a. 今天别重试、别上报缺陷：QA 已定性为日粒度（当日不可选）；重试只会再吃一次跳过。
b. 开放日复测（先预演后执行）：
   - 预演（dry-run，默认）：python tools/mas_probe_farm.py --account LEUNG --stage LS-5 --probe-only --retry-after-fix "LS-5 开放日复测"
   - 真跑（需你明确授权后再执行）：同命令加 --apply
   （门禁要求对 failed 行给 override 理由；--probe-only=只探不发；具体参数以该脚本 --help 为准）
c. 零成本判"今天开不开"（只读）：看当天任意一班的 MAA 日志是否出现 GetFightStage: from ["LS-5"], selected LS-5（=开放；selected null=不开）；或查台账当日 LS 系有无成功记录。
d. 若目标其实是龙门币：LS-5 属作战记录（经验）线、兼掉龙门币；纯龙门币是 CE 系（仓库表 wed/sat/sun，同样轮换）。LEUNG 眼下稳定产出=剿灭+剩余理智 1-7（今天均 Success）——急资源就维持现状，别消耗体力探测轮换关。
e. 顺带（独立问题）：LEUNG 的 SR-7 台账仍 0 成功（68 次尝试/28 实动）——与本案无关，但若要给 LEUNG 换产出，更值得单独开单。

────────────────────────────────────────────
二、依据清单（引用过的文档 / 字段 / 工具）
────────────────────────────────────────────

文档：
- docs/MAS_PROBE_FARM.md（§9.1）｜docs/STAGE_PROBE.md｜docs/STAGE_CAPABILITY_QA.md（R2）｜docs/STAGE_CAPABILITY.md｜docs/SERVER_EVENTS.md（StageActivityV2 口径）
- docs/audits/2026-09-28_stage_capability_qa/：verify_final.md / verify_table.md / sample_design.txt
- docs/audits/2026-09-28_tooling_audit/_probe_table.md
- docs/audits/2026-09-27_maa_errors_triage.md:117（−4h 命名怪癖）｜docs/MAA_EVENT_FARMING_PLAYBOOK.md:120（UTC+4 日切）

字段/口径：
- stage_not_open、not_open / skipped / maa_result；selected null；"关卡计划内没有当日开放的关卡"
- RES_DAYS（services/farm_dispatch.py）；stage / status / last_failure（stage_capability.json）
- 探针 flags：--probe-only / --retry-after-fix / --dry-run / --apply（tools/mas_probe_farm.py）

实机只读：
- E:\AUTO-MAS\history\2026-09-28\LEUNG\（4 条记录 + 04-46-53.log/json）
- E:\AUTO-MAS\history\2026-09-07\v\16-54-50.log/json（LS-5 成功实证）
- MAA_5 资源 findstr：LS-5 出现于 resource\stages.json、resource\tasks\Stages\Supplies.json、cache\gui\StageActivityV2.json、Arknights-Tile-Pos\wk_kc_5*
- MAA_5 cache\gui\StageActivityV2.json（含 IsResourceCollection:true，未解读完）

KB：D:\1-2\MAS-KB tools\wiki_search.py（"LS-5"=无记录；"LEUNG"=号实体页：SR-7+剩余1-7）
时间校验：date（09-07 / 09-28 均为周一）

────────────────────────────────────────────
三、信息缺口（找不到 / 没写清 / 走弯路处 + 希望补什么）
────────────────────────────────────────────

1. "LS-5 到底哪天开"没有单点文档；仓库 RES_DAYS 与 MAA 运行时冲突无人解释。我在"账号能力→缓存过期→轮换未开放"上绕了三圈才落点。建议补《资源关轮换速查（以 MAA 为准）》，含"今天开不开"的只读查法。
2. MAA 本地开放表原文件未读成：本想贴出 resource\tasks\Stages\Supplies.json 里 LS-5 条目原文，但 PowerShell 编码命令被本机安全策略拦截（BLOCKED）。下一位可用只读命令补：
   ssh … 'findstr /n /c:"LS-5" "E:\MAA-Multi\MAA_5\resource\tasks\Stages\Supplies.json"'（必要时 type 全文）
   希望文档写明"开放日表在 MAA 哪个文件、怎么读"。
3. "同系列回落"（计划选 LS-6、实开 LS-5）目前只见于 QA 笔记与本次实证，正式机制文档没有说明——建议并入 MAA 机制类文档。
4. −4h 文件名怪癖埋得太深（差点把 08:46 读成 04:46）——建议提升到中央运维文档或技能参考。
5. 题面把 LS-5 称"龙门币关"：仓库口径 LS-5=作战记录线（兼掉龙门币）；纯龙门币=CE 系。建议加一行对照，避免后人拿错关。
6. 探针止损的 maa_result="任务被用户手动中止" 极易被误读成真人中止——建议记录层补注释或改文案。
7. 悬而未决（建议专人）：09-07 周一能刷 LS-5、09-28 周一判"不开"——是轮换表变过（09-05→09-20 间游戏/MAA 资源更新），还是"同系列回落"边界不同？核对办法：读 MAA resource 里 LS-5/LS-6 的开放日数据，对照这两个日期。

（复盘完）