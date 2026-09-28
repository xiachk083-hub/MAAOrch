# MAAOrch 工具箱导航（按「要做的事」找工具）

> 建：2026-09-28 ｜ 任务 `t_2ac34fca` ｜ 每一行命令与产物路径都按当日仓库实况核对（工具 docstring / docs / 审计目录）。
> **用法**：先在本页按任务找「首选工具」→ 照「最少命令」跑；**不要新写脚本、不要手搓 REST/临时队列**（查重见 §E）。
> 邻接入口：业务全景「谁是谁」= `docs/MAS_MASTER_HANDBOOK.md`；出事照单执行 = `docs/MAS_EMERGENCY_RUNBOOK.md`；本页只管「哪件事用哪个工具」。

---

## 0. 总则（先记住 6 条）

1. **解释器统一 Python312**：`PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"`（preflight 截图+OCR 依赖 cv2/rapidocr，只有它带；其余工具也用它，口径统一）。
2. **写动作默认 dry-run**：`--apply` 才真发车/真写。每个工具都有 `--selftest`（离线、假传输）。
3. **末行判据（机检用）**：`PREFLIGHT=GO|NO-GO`、`SWITCH_GATE=…`、`PATCH_GUARD=…`、`ERR_SUMMARY=…`、`BOXREAD_DONE`、`CLIENT_STATE=…`、`UPCOMING=…`（周期事件到点提醒，数据不可用写 `UNKNOWN:<原因>`）。
4. **全局急停开关（环境变量）**：`MAAORCH_DISPATCH_NO_GO=1`（一切发车一票否决）、`MAAORCH_PROBE_FARM_NO_GO=1`（试探）、`MAAORCH_SCENARIO_APPLY=1`（场景发车第二道闸）。
5. **守窗口**：守卫窗 **05:00–05:40 / 19:20–20:20** 不发补跑（`dispatch_account` 默认拒，`ignore_window=true` 才放行）；重启侧车/实例/清盘的维护窗 = 收班后至 04:00 与 04:00/19:00 计划重启前后。
6. **同一实例同一时刻只做一件事** → 拿实例先租约（§A2）。跨工具共锁：`runtime/mas_dispatch.lock`（班次/按需/MCP 同源一把）。

---

## A. 发车前必做三件事（任何真发车动作前的硬序）

### A1 班前自检 preflight —— `PREFLIGHT=GO` 才发

```
$PY312 tools/mas_preflight.py --script 明日方舟-官-12 --skip-screen     # 或 --queue 晚班定时1
$PY312 tools/mas_preflight.py --script 明日方舟-官-12 --json
```

* 判读：末行 `PREFLIGHT=GO` 才继续；`NO-GO` / 解析失败 / 超时（UNKNOWN）一律停手。检查项：REST 可达 / 槽位空闲 / 实例可 adb / 无残留模态 / 注入合规 / 磁盘。
* 只读；全量（含截图+OCR，约 +20~40s）去掉 `--skip-screen`；截图落 `runtime/preflight_shots/`。
* MCP：`mas-observe.mas_preflight`（`skip_screen` 默认 true）。

### A2 实例租约 —— busy 就换实例 / 等

```
$PY312 tools/mas_instance_lease.py acquire --idx 13 --purpose "t_xxxx 补跑潮响" --ttl 3600
$PY312 tools/mas_instance_lease.py check  --idx 13      # rc0 free / rc1 busy / rc2 判不了
$PY312 tools/mas_instance_lease.py release --idx 13
```

* 账本 `runtime/instance_leases.json`（`list` 看在租）；`--owner` 缺省取 `HERMES_KANBAN_TASK`。
* rc1（busy）⇒ 换实例或 `--wait`；`--force` 抢租只在确认持有者已死时用（写 history=stolen）。

### A3 在飞检查 —— 目标槽位有活就不发

```
$PY312 tools/mas_ondemand.py --list-running --json
```

* 三源合并：班次 state / ondemand 账本 / 主机进程·实例·app.log；看到目标目录有 `MAA.exe` 或 `ondemand_open` 含同脚本/目录 ⇒ 不发。
* 发车工具自身闸门 G3 也会拦：`slot_busy` / `slot_warm`（班次两脚本空档 MAA.exe 会消失 1–2 分钟，别只看进程）/ `duplicate_inflight`。

> 三件事全过 ⇒ 才 `--apply`。任一 UNKNOWN ⇒ 停手报告，不猜。

---

## B. 任务 → 工具

### B1 想补跑 / 发车某个号（远端 AUTO-MAS 账号）

* 首选：`tools/mas_ondemand.py`｜MCP：`mas-ondemand.dispatch_account`
* 最少命令：

```
$PY312 tools/mas_ondemand.py --account 帝国猛虎            # 干跑：解析+闸门+计划
$PY312 tools/mas_ondemand.py --account 帝国猛虎 --apply    # 真发车（自动建 临时-按需-* 队列）
$PY312 tools/mas_ondemand.py --stop <task_uid> --apply     # 停（只认 start 返回的 task_uid，别 kill）
$PY312 tools/mas_ondemand.py --cleanup [<队列>] [--apply]  # 清本工具建的临时队列
```

* 输出：调用审计 `runtime/mas_ondemand_log.jsonl`（含干跑/被拒）+ 真动作 `runtime/mas_dispatch_log.jsonl`（`source:ondemand`）；账本 `runtime/mas_ondemand_ledger.json`（CLI/MCP 唯一账）。
* 禁忌：① `--account` 走 AutoProxy ⇒ **该脚本全部启用号都会被跑到**（多号脚本先看清）；② `--wake` 不是补跑（ManualReview 会挂等客户端应答、脚本锁不释放，须 `--apply --force` 且人在场）；③ busy 窗内拒删临时队列（删了会让收尾找不到队列而中途失败）；④ rc 0=成功 / 2=拒绝。

### B2 想按配置刷（关卡 / 连战 / 吃药 / 活动优先，一整轮）

* 首选：`tools/mas_run_profile.py`（八步：备份→写→preflight→发车→读回→等完→还原→审计；机制规范 `docs/MAS_RUN_PROFILE.md`）
* 最少命令：

```
$PY312 tools/mas_run_profile.py --profile runtime/templates/run_profile.example.json   # 干跑
$PY312 tools/mas_run_profile.py --profile <p.json> --apply
$PY312 tools/mas_run_profile.py --status          # 运行台账
$PY312 tools/mas_run_profile.py --list-plans      # 计划表 + 遗留「临时-按需-*」
```

* 输出：`runtime/run_profile/backups/<run_id>/`（还原快照）、`runtime/mas_run_profile_log.jsonl`、`runtime/mas_ledger.jsonl`。profile 可由 B3 生成、B4 试探复用。
* 禁忌：① 默认 **plan 路线**（新建临时计划表 + StageMode 指过去）⇒ 不动账号自己的 7 键，别改成 `--write-mode fixed` 除非明确要；② 多号脚本必须显式 `--disable-others` / `--all-accounts`；③ `series` 是**连战倍数**不是总次数；④ 超时默认 stop + Temp 还原，还原失败 rc=4 需人工（`--restore-run <id>`）；⑤ 单号运行前先做 §A 三件事。

### B3 想「按该刷什么」生成一份刷取计划（干员+仓库+能力台账+活动 → 计划草案）

* 首选：`tools/mas_plan_builder.py`（默认零写；产物 `.json` 与 run_profile schema 对齐，可直接执行）
* 最少命令：

```
$PY312 tools/mas_plan_builder.py --account y9Flipped                 # 生成草案（零写）
$PY312 tools/mas_plan_builder.py --account y9Flipped --sanity 210 --medicine 2 --series 1
$PY312 tools/mas_plan_builder.py --account y9Flipped --exec-dry-run  # 走一遍 run_profile 全路径（零写）
$PY312 tools/mas_plan_builder.py --account y9Flipped --apply         # 真发车（需用户/lead 批准）
```

* 输出：`runtime/reports/plans/<账号>_<yyyymmdd_HHMM>.md`（人读，含「依据→结论」链）+ 同名 `.json`。
* 禁忌：① 前置 = 该号有上号识别产物（B9），没有先补；② 活动快照超 7 天拒用「活动优先」，超 3 天告警；③ `--skip-capability` 不推荐（failed 关不剔除）；④ 缺省不含理智（识别产物里没有），给 `--sanity N` 才给次数估算。

### B4 想查「这个号能不能刷这关」（能力台账）

* 首选：`tools/stage_capability.py`（本工具是台账唯一生成者）
* 最少命令：

```
$PY312 tools/stage_capability.py --account y9Flipped --json    # 查询（读本地台账，不联网）
$PY312 tools/stage_capability.py --update                      # 增量重建（SSH 扫主机最近 3 天）
$PY312 tools/stage_capability.py --build                       # 全量重建（30 天窗口）
$PY312 tools/mas_probe_farm.py --list unknown --no-rest        # 或按状态列台账行
```

* 输出：`runtime/reports/stage_capability.json`、`runtime/reports/STAGE_CAPABILITY.md`、中间态 `runtime/state/stage_capability_state.json`。
* 禁忌：① 台账是 **30 天窗口的「刷过没有」**，≠「今天开放」；② 剿灭（Annihilation）不是 7 键关卡字段，本台账含它的记录但发剿灭另走 B16/B2；③ 单条 `--account` 查询别加 `--update`（会联网重扫）。

### B5 拿不准能不能刷 → 先试探后开刷

* 首选：`tools/mas_probe_farm.py`（三态决策：success 直刷 / failed 默认拒 / unknown 先试探一次）
* 最少命令：

```
$PY312 tools/mas_probe_farm.py --account y9Flipped --stage 1-7 --times 2   # 干跑
$PY312 tools/mas_probe_farm.py --account y9Flipped --stage 1-7 --times 2 --apply
$PY312 tools/mas_probe_farm.py --status        # 本工具历史 + 锁状态
```

* 输出：证据 `runtime/probe_farm/<run_id>/`；流水 `runtime/mas_ledger.jsonl`（`kind=probe_farm`）；锁 `runtime/mas_probe_farm.lock`；结论回写台账。
* 退出码：0 完成 / 2 拒绝 / 3 试探未证明（failed/inconclusive，已还原）/ 4 有残留（人工）/ 5 试探成功但正式刷失败。
* 禁忌：① failed 台账默认拒发，修复后放行必须 `--retry-after-fix "说明"`（进审计）；② 一次最小试探，失败不重试、不连环试探；③ 不碰其它账号（多号脚本要 `--disable-others`）；④ 剿灭不支持（拒绝）。

### B5b 想「一条命令跑通整条链」（决策→试探→执行→还原）

* 首选：`tools/mas_farm_orchestrator.py`（把 B4 台账 + B5 试探 + B2 执行 + A2 租约串成一条链；每个环节仍是各自工具的权威，本工具只做「串 + 判 + 报告」）
* 最少命令：

```
$PY312 tools/mas_farm_orchestrator.py --account <号> --stage <关>                        # 干跑（零写）
$PY312 tools/mas_farm_orchestrator.py --account <号> --stage <关> --times 1 --apply     # 真跑（一条链）
$PY312 tools/mas_farm_orchestrator.py --status                                          # 最近运行 + 锁
```

* 输出：报告 `runtime/reports/farm_run_<ts>_<账号>.md`；run 目录 `runtime/farm_orchestrator/fo_<ts>_<账号>/`（steps.json/summary.json/profile.farm.json/probe_run.log/farm_run.log）；审计 `mas_ledger.jsonl` + `mas_farm_orchestrator_log.jsonl`。
* 退出码：0 整链过 / 2 发车前拒（含租约被占）/ 3 试探未证明 / 4 **有残留**（人工）/ 5 试探过但执行失败。
* 禁忌：① 试跑窗口别小于实测（冷启动+完整日常链 ≈16–18 分钟才打完第一场，`--timeout` 默认已给 1500s）；② 多号脚本默认 fail-closed；③ 剿灭不支持；④ 失败不重试，照实报告。
* 实测：2026-09-28 用户_173920298 × LS-6 一次真跑 rc=0（`docs/audits/2026-09-28_farm_orchestrator_rehearsal.md`）。

### B6 想查失败原因（单账号链级定因）

* 首选：`tools/mas_error_triage.py`｜MCP：`mas-triage.triage_error`
* 最少命令：

```
$PY312 tools/mas_error_triage.py --account hbznn119-日 --json
$PY312 tools/mas_error_triage.py --maa-dir "E:\MAA-Multi\MAA_6"
```

* 输出：stdout 结构化 `{kind, severity, reason, action, chains_with_error, evidence[]}`（例：`recruit_confirm_failed` severity=info=资源类，**不补跑**）。
* 禁忌：① 读忙时 `asst.log` 会慢/超时——先看 B7 的轻读聚合再决定深挖；② 判定只看 `asst.log` 的 `TaskChainError/SubTaskError` 纯 ASCII 标记，别按中文报错串猜。

### B7 想知道「今天错在哪 / 上一班跑得怎样」

* 首选：`tools/maa_error_daily.py`（四源聚合）＋ `tools/mas_shift_report.py`（班次日报）
* 最少命令：

```
$PY312 tools/maa_error_daily.py --summary            # 一行：ERR_SUMMARY=<n> 条，Top=<类型>
$PY312 tools/maa_error_daily.py --date 2026-09-28 --json
$PY312 tools/mas_shift_report.py --shift daily       # 班次日报（缺口/成功率/失败 Top/水位/闸门/发车审计）
```

* 输出：`runtime/reports/maa_errors_<date>.{md,json}`（`--no-write` 只打印）；`runtime/reports/shift_report_<YYYYMMDD>_<shift>.md` + 看板只读汇总卡；告警默认 dry-run（`--alert-send` 才真发）。
* 排期（2026-09-28 起）：Hermes cron `mas-shift-report`（id `8b1708397046`）每天 08:30（日常班）/ 21:30＋23:30（晚班快照＋定稿）自动出报告+看板卡（no_agent、deliver=local）；接入细节/告警渠道/开关见 `docs/MAS_SHIFT_REPORT.md`。
* 卡面俗称「errboard」= 这一组 + `mas-triage` MCP（`error_daily`）；**没有**独立叫 errboard 的工具。
* 禁忌：① 班次日窗口 = `[D 05:00, D+1 05:00)`；② 别拿 `data/daily_state/*.json` 当「今日是否完成」依据（已长期不更新，以 history / REST 当日记录为准）。

### B8 想上号读干员与仓库（干员 box / depot）

* 首选（现状）：主机侧 `tmp/boxread/host_boxread.py`（asst 子进程直连 MaaCore；已跑通闭环，**尚未提升为 tools/ 正式工具**——提升是待办）；本机版：`tools/maa_operbox.py`（只读 OperBox）、`tools/box_read_when_free.py`（回大厅+抢锁版）。
* 最少命令（主机侧，Python 3.12.9）：

```
"C:\Program Files\Python312\python.exe" host_boxread.py --vmindex 14 --out "E:\_hermes_boxread\run1" [--maa-dir "E:\MAA-Multi\MAA_6"]
```

* 输出（本仓）：`runtime/reports/accounts/<账号>/{operators.json, depot.json}`（+`evidence/`、`raw/`）；主机 run 目录：`operbox_raw.json / depot_raw.json / state.json / shots/`；判据末行 `BOXREAD_DONE`。
* 禁忌：① **游戏必须在大厅/标题页**——停在活动地图直读会返回 `own: []`（看着像空号，其实没读到）；② 同一 MAA 目录互杀 ⇒ 目标目录整队收尾后再上，读期间 ManualReview 挂起等应答（天然占窗）；③ 全程不作战/不领奖/不改配置（闭环实测仅 StartUp×2 + OperBox + Depot 四条链）。

### B9 想快照 / 回滚 AUTO-MAS 配置

* 首选：`tools/mas_config_snapshot.py`（快照→差异→回滚→演练闭环）
* 最少命令：

```
$PY312 tools/mas_config_snapshot.py --label pre-change         # 建快照
$PY312 tools/mas_config_snapshot.py --list
$PY312 tools/mas_config_snapshot.py --diff <tsA> <tsB>
$PY312 tools/mas_config_snapshot.py --drift                    # 生产 vs 最近快照（只读）
$PY312 tools/mas_config_snapshot.py --restore <ts>             # 默认 dry-run；--apply 真回滚
$PY312 tools/mas_config_snapshot.py --drill                    # 恢复演练（本地临时副本+主机 scratch）
```

* 输出：本地 `runtime/snapshots/<ts>/{manifest.json, files/, redacted/}`；主机侧敏感件全保 `E:\AUTO-MAS\backups\cfgsnap_<ts>\`；保留 30 份轮转。
* 禁忌：① 含凭据的文件**全保副本只留主机**（本机只有脱敏版+哈希）——别往本机搬 raw；② `--restore` 生产回滚会自动前置快照（`--no-pre-snapshot` 不推荐）；③ 演练加 `--skip-remote` 可离线。
* 细节文档：`docs/MAS_CONFIG_SNAPSHOT.md`（上传通道口径/巡检接入点/排障）。注意：上传已改走 **HTTP 拉取通道**——旧版 `ssh stdin` 大载荷会触发 Windows sshd 随机死锁（2026-09-28 实测，见 `docs/audits/2026-09-28_mas_config_snapshot_transport.md`）。

### B10 想按班次发车 / 对账（队列级，24 条定时）

* 首选：`tools/mas_dispatch.py`（Hermes cron `2600f6415f16` 每 3 分钟调 `--reconcile --apply`）
* 最少命令：

```
$PY312 tools/mas_dispatch.py --list                 # 24 条班次 + 今日进度 + 告警数
$PY312 tools/mas_dispatch.py --busy                 # 当前 busy 窗口 + 未关闭 late 标记
$PY312 tools/mas_dispatch.py --reconcile            # 对账（dry-run）
$PY312 tools/mas_dispatch.py --queue 晚班定时6 --apply          # 人工补发
$PY312 tools/mas_dispatch.py --dismiss-late 晚班定时6 --apply --note "理由"
$PY312 tools/mas_dispatch.py --alerts               # 未消除发车告警
```

* 输出：`runtime/mas_dispatch_state.json`（当天发放记录）、`runtime/mas_dispatch_log.jsonl`、`runtime/mas_dispatch_alerts.json`；锁 `runtime/mas_dispatch.lock`。
* 边界（硬）：① **队列级发车/补发只归本工具**；超追发窗口（>30min）**不自动补**，只记 `late` + 告警，补发必须显式 `--queue … --apply` 或 `--dismiss-late`；② **账号级补跑只归 B1**（mas_rerun/ondemand），与队列级正交；③ 同资源冲突（`conflict_inflight`）发车拒绝且零上游写——是否并行只由人工 `--release`/`--force-conflict` 决定。

### B11 想看现场：谁在跑 / 卡没卡 / 空转

```
$PY312 tools/mas_ondemand.py --list-running --json     # 在跑槽位/任务/实例（三源合并，含 ondemand_open、guard_window）
$PY312 tools/mas_idle_emulators.py --json              # 空转实例收敛清单（只出建议，不执行关闭）
$PY312 tools/client_state_check.py --serial 127.0.0.1:16416   # CLIENT_STATE=<正常:大厅|模态:…|未登录…> + ACTION=
```

* MCP：`mas-observe.mas_idle_emulators` / `mas-observe.mas_client_state`。
* 禁忌：`mas_idle_emulators --with-fg` 会碰 adb，别与其它 adb 任务同时开；关实例动作永远由人工/上层决定。

### B12 想查串号 / 每轮身份留痕

```
$PY312 tools/mas_switch_gate.py --check                # 配置层：末行 SWITCH_GATE=OK|VIOLATION:<槽位>|UNKNOWN
$PY312 tools/mas_identity_trace.py watch --duration-min 60     # 运行层：watch/ocr/report 三子命令
$PY312 tools/mas_account_identity_check.py --script 明日方舟-官-1-1-7   # 名册指纹反推每轮登录号
```

* 输出：`runtime/identity_trace/`（帧+报告）；SWITCH_GATE 在巡检 job 52b88b0ffca0 第三步。权威切号信号 = MAA 目录 `debug/gui.log` 的「账号切换 -->> <掩码>」。
* 禁忌：VIOLATION 不等于事故（先看该槽位是否在飞）；掩码规则：官服必须掩码 + AccountSwitchEnabled=true，非官必须空 + false（口径 `docs/audits/2026-09-27_*account_switch*`）。

### B13 巡检健康：补丁、自动路径、主机指标

```
$PY312 tools/mas_patch_guard.py --check      # 末行 PATCH_GUARD=OK|MISSING:<n>（AUTO-MAS 升级覆盖补丁的第一发现线）
$PY312 tools/mas_agent_time.py --check       # AUTO_PATH=HEALTHY|RECOVERED|BROKEN（巡检自动路径）
$PY312 tools/host_perf.py --once             # 主机内存/磁盘采样（写本地日志；--no-send 测告警）
$PY312 tools/mas_watch.py --force            # L3 守护：立即出报告（默认由计划任务每 20 分钟调；只读+补跑闭环）
```

* 输出：`runtime/patches/manifest.json`（补丁清单/哈希，快照 `runtime/patches/payloads/`）；`docs/audits/<date>_mas_agent_time.{md,json}`；`runtime/mas_ledger.jsonl`（watch 事件）。
* 禁忌：重放/录制是**受控写动作** —— `--reapply` 默认 dry-run 零写入，`--apply`（或 `--capture --apply`）才落盘；要批准 + 变更窗口外做，改完重启侧车才生效。**别新增巡检 cron**（Hermes 侧只保留 52b88b0ffca0 一个巡检任务）。

### B14 「今天该刷什么 / 谁该补」的只读决策件

```
$PY312 tools/mas_daily_advice.py              # 活动期+仓库缺口+理智 → 今日各号建议
$PY312 tools/mas_catchup_check.py             # 受控补漏的五条准入判据 → 候选+证据（永不发车）
```

* 输出：`runtime/reports/daily_advice_<yyyyMMdd>.{md,json}`；catchup 直接打印候选（源码不开写端点，测试有断言防改）。
* 禁忌：候选 ≠ 许可；补跑动作仍走 B1/B2 且受 §A 三件事与守卫窗约束。

### B15 场景发车：肉鸽 / 活动 / 剿灭

* 首选：`tools/mas_scenario_dispatch.py`（草案阶段，**双闸默认关闭**：`--apply` 且 `MAAORCH_SCENARIO_APPLY=1` 才触达写请求）
* 最少命令：

```
$PY312 tools/mas_scenario_dispatch.py --list-scripts
$PY312 tools/mas_scenario_dispatch.py --script "明日方舟-官-1-1-7" --scenario rogue --times 3 --window 21:00-23:00
```

* 细则：肉鸽手册 `docs/MAA_ROGUELIKE_PLAYBOOK.md`＋技能 `maa-roguelike-ops`；活动 `MAA_EVENT_FARMING_PLAYBOOK.md`＋技能 `maa-event-farming`；机队 l-* 与肉鸽探针在 `tools/rogue_*.mjs`/`rogue_fleet_*.ps1`。
* 禁忌：草案双闸别只给一个；剿灭走 `--scenario annihilation`（不走 B5 的 7 键路线）。

### B16 账号清单 / 在哪台 / 怎么切

```
$PY312 tools/mas_accounts.py --dump --schedule      # 现取：账号→脚本→实例→ADB 口 + 班次表
```

* 输出：`runtime/reports/mas_accounts_<ts>.{json,md}`；掩码↔UID 全表见 `docs/MAS_EMULATORS.md` 与 `runtime/reports/mas_account_master_*.json`；离线模式 `--offline --script-config/--mumu`。
* 禁忌：清单是**现取**（易变），别拿旧快照排期；ADB 口公式见 `docs/MAS_EMULATORS.md`（别再手算 old 公式）。

### B17 想查「刷哪里划算」（关卡效率 / 某材料去哪刷 / 实测与公开对不对得上）

* 首选：`tools/stage_efficiency.py`（只读 + 本地数据生成；不抓森空岛，公开数据只读本地企鹅快照）
* 最少命令：

```
$PY312 tools/stage_efficiency.py --query 固源岩组      # 材料→推荐关（每理智产出降序 + 实测对照）
$PY312 tools/stage_efficiency.py --stage 1-7           # 单关详情（主要掉落/口径/置信度/实测）
$PY312 tools/stage_efficiency.py --build               # 重建库（默认吃实测缓存；--measured 强制重扫）
```

* 输出：`runtime/reports/stage_efficiency.json`（schema `maaorch.stage_efficiency/1`：关卡表 + 材料反查 + `mismatches`）＋实测缓存 `runtime/state/stage_efficiency_measured.json`。
* 禁忌：① 公开数据是**快照**（看 `sources.penguin.fetched_at`），活动期别拿隔夜结论当今天；② 库**不判断开放状态**，发车资格仍走 B4/B5；③ 不给计划生成器当「决策」用——它只是数据底垫（计划仍走 B3）；④ 龙门币/凭证等固定产出不在库（CE/AP 本别按 main_drops 理解）。

### B18 活动开/关时自动切换刷取计划（StagePlan）

* 首选：`tools/activity_switch.py`（受控自动化：活动开⇒切活动关、活动关⇒切回原计划；默认 dry-run，`--apply` **且** `--approved-by` 才写；一键还原）
* 最少命令：

```
$PY312 tools/activity_switch.py --server cn_official                              # 计划（零写；落 runtime/reports/activity_switch/）
$PY312 tools/activity_switch.py --server cn_official --pretend-now "2026-09-29 17:00"
$PY312 tools/activity_switch.py --server cn_official --apply --approved-by <批准人> --probe-unknown
$PY312 tools/activity_switch.py --restore-all --apply                             # 一键还原
$PY312 tools/activity_switch.py --status                                          # 切换台账（未还原清单）
```

* 判据链：`server_events.json`（触发）→ MAA 活动表（**能否真刷**，fail-closed）→ 能力台账（success 直切 / unknown 先试探 / failed 剔除）；写面 = 临时计划表 + StageMode 指针（不碰存量计划表与 7 键），备份 `runtime/activity_switch/backups/`。
* 输出：报告 `runtime/reports/activity_switch/*.md|.json`；台账 `runtime/activity_switch_state.json`；细则 `docs/MAS_ACTIVITY_SWITCH.md`。
* 禁忌：① 班次运行中不硬改（G6；配置锁定会拒写，该号跳过零残留）；② 不自动批准（缺 `--approved-by` 直接拒）；③ MAA 表未收录的活动关别猜（等收录，或 `--stages` 显式指定并自行核对关码）；④ 活动结束后用 `--restore-all --apply` 或下次 apply 的自动还原，别让号挂在临时表上。

---

### B19 想知道「调度还在不在」：gateway / cron / 发车心跳（挂了没人报 ⇒ 外部看门狗）

* 首选：`tools/scheduler_watchdog.py`（**外部**看门狗：Windows 计划任务每 5 分钟一轮；只读本机文件判据，不依赖 hermes CLI / 不依赖 gateway 活着）
* 最少命令：

```
$PY312 tools/scheduler_watchdog.py --once          # 单次探测（人肉看）：末行 WATCHDOG=OK|ALERT
$PY312 tools/scheduler_watchdog.py --check         # 环境自检（hermes 目录/心跳/通道/计划任务壳），CHECK=OK|FAIL
$PY312 tools/scheduler_watchdog.py --test-notify   # 真发一条标注测试的告警（验证通道）
$PY312 tools/tests/watchdog_drill.py               # 假环境全链路演练（15 步，末行 DRILL=PASS|FAIL）
```

* 判据：① gateway 进程存活 + `gateway_state.json` 状态面 + 端口（辅助）+ `profiles/<p>/cron/ticker_heartbeat` 心跳（>200s=停摆）② 每个 enabled cron 任务 `next_run_at` 超期（容忍=min(120, max(15, 间隔+5))min；附 executions.db 最近一次执行时间）③ 最近发车审计（发车窗口起 +45min 仍无今日发车 ⇒ ALERT）。
* 输出：`runtime/scheduler_watchdog.log` / `_state.json` / `_alerts.jsonl` / `reports/scheduler_watchdog_alert_*.json`；告警默认 dry-run 落文件，`--alert-send` 真发（`watchdog.ini` 邮件通道，与班次日报/主机看门狗同源）。
* 计划任务 `MAAOrch-SchedulerWatchdog`（每 5 分钟）**注册待 lead 批准**：`--install-task`（只看命令）→ `--install-task --apply`（注册）→ `--uninstall-task --apply`（回滚）；细节 `docs/SCHEDULER_WATCHDOG.md`。
* 禁忌：别用 Hermes cron 看护 Hermes（自指闭环——gateway 死 = 监控一起死，见文档 §1）；别随手开 `--restart`（默认关，注册脚本不含）。

---

### B20 想知道看板健不健康（重复卡/僵尸卡/长期 blocked/崩溃重派/孤儿产物）

* 首选：`tools/kanban_health.py`（**只读**扫 `kanban.db`；只出清单+建议动作，**不归档/不重派**）
* 最少命令：

```
$PY312 tools/kanban_health.py                 # 两块板全扫 → runtime/reports/kanban_health_<ts>.{md,json} + 末行 KANBAN_HEALTH=
$PY312 tools/kanban_health.py --line          # 只打一行 KANBAN_HEALTH=（巡检取数用）
$PY312 tools/kanban_health.py --selftest      # 45 例离线自检
```

* 判据：①重复（标题相似度≥0.86 / 同 idempotency_key）②僵尸（running≥4h 且心跳断≥60min，与内核同判据；另报 claim 过期未回收=调度器可能停摆、活着的长跑卡）③长期 blocked（≥6h 无新注释；dependency 且父卡全 done 单列）④崩溃/超限（gave_up 未解、连败≥2、迭代预算 150 轮、反复崩溃、批量崩溃聚合）⑤孤儿产物（声明路径不存在；远端盘/scratch 清理/同名附件兜住的一律不报）。
* 输出：`runtime/reports/kanban_health_<ts>.md`（逐类 + 建议动作 + 已归档重复附录）+ 同名 `.json`（schema `maaorch.kanban_health/1`，含 `line`）。退出码 `0` 全绿 / `2` 有 P2·P3 / `3` 有 P1 / `4` 无板可扫。
* 建议动作只是**文本**（如「保留 t_x，comment 汇总后 archive t_y」）；真动手仍走 `hermes kanban archive|unblock|comment|edit`（§B10/§D），并按 `KANBAN_DISPATCH_POLICY.md` §4.3 先写重试备注再 promote。
* 细节/口径/误报防线：`docs/KANBAN_HEALTH.md`。

---

### B20 想知道「某实例/脚本现在到底是哪个 adb 口」（端口会漂）+ 全机队映射一份看清

* 首选：`tools/mas_mapping.py`（只读；一张表把 槽位↔脚本↔实例 idx↔标题↔**实测** adb 口↔账号数↔队列 全串起来；含漂移校验）
* 最少命令：

```
$PY312 tools/mas_mapping.py --port 27          # 单实例现查（vm_config+VMM 三证）→ 127.0.0.1:17249
$PY312 tools/mas_mapping.py --lookup 明日方舟-官-12   # 按脚本名/槽位 MAA_N 查整行
$PY312 tools/mas_mapping.py --build            # 全机队现取（~30s）→ runtime/reports/mapping.{json,md}
$PY312 tools/mas_mapping.py --verify           # 校验+漂移，末行 MAPPING_VERIFY=OK|DRIFT:<n>（rc 0/1/2）
$PY312 tools/mas_mapping.py --selftest         # 离线自测（假数据，不碰网）
```

* 输出：`runtime/reports/mapping.json`（schema `maaorch.mapping/1`，含 findings/changes）+ `mapping.md`（人读主表）。
* 规律与漂移史：`docs/MAS_PORT_RULES.md`（权威）；`docs/MAS_EMULATORS.md` §2 是 09-27 快照（其「重启不改口」已被 idx27 反例更正）。
* 禁忌：**别按公式写死口**（idx27 已从 17248 漂到 17249）、别信 `MuMuManager info` 的 `is_process_started`、别拿槽位记忆地址当当前口（那是上一轮注入值）。

---

## C. MCP 工具面（会话里直接调；工具面=注册期白名单）

| server | 工具 | 性质 |
|---|---|---|
| `mas-ondemand` | `list_running` / `dispatch_queue` / `dispatch_script` / `dispatch_account` / `stop_task` / `ondemand_preflight` | 1 只读 + 5 写（全部默认 dry-run） |
| `mas-observe` | `mas_preflight` / `mas_report` / `mas_idle_emulators` / `mas_client_state` / `mas_identity_trail` / `mas_interrupt_guard` / `event_calendar` | 全只读 |
| `mas-dispatch` | `list_schedule` / `dispatch_due` / `dispatch_queue` / `switch_gate` | 2 只读 + 2 写（默认预演） |
| `mas-triage` | `triage_error` / `error_daily` | 只读 |
| `maaorch-daily` | `mas_status` / `mas_snapshot` / `mas_rerun_state` / `account_status` / `queue` / `mas_rerun` | 5 只读 + 1 写（`dry_run=True` 默认预演） |
| `auto-mas-monitor` | 10 个精确名只读（health/overview/queues/scripts/user/history…） | 只读硬闸（写调用零上游触达） |

> 工具面变更（新增/改工具）**要新会话或网关重启才可见**（`docs/HERMES_MAS_HANDOVER.md` §12.1）；CLI 侧不受影响。

---

## D. 审计流 / 锁 / 台账（回答「这是谁干的、什么时候干的」）

> **统一入口（新）**：`tools/mas_audit.py` —— 写入走一条流 `runtime/audit/mas_audit.jsonl`（schema `maaorch.audit.v1`），
> 查询可多源合并（本流 + 归档 + 下表 9 套旧流 + 看板库，旧流只用 `query --legacy` **只读桥接**，不迁移不重写）；
> 轮转/保留见 `docs/MAS_AUDIT_SCHEMA.md`（§7 阈值、§8 接入建议、§9 清理清单）。

| 文件 | 内容 |
|---|---|
| `runtime/audit/mas_audit.jsonl`（+ `archive/*.jsonl.gz`、`mas_audit.lock`） | **统一审计流**（v1；`tools/mas_audit.py` 写/查；旧流经 `--legacy` 桥接）（`docs/MAS_AUDIT_SCHEMA.md`） |
| `runtime/mas_dispatch_log.jsonl` | 发车事件流（班次 + ondemand 真动作，含 reason/attempt/terminal） |
| `runtime/mas_ondemand_log.jsonl` | 按需发车每次调用（含干跑/被拒；`src:cli|mcp`） |
| `runtime/mas_run_profile_log.jsonl` | run_profile 每次 plan/apply/readback/restore 一行 |
| `runtime/mas_activity_switch_log.jsonl` | 活动切换每号一行审计（switched/skip/probe/restore/apply_crashed） |
| `runtime/mas_ledger.jsonl` | 全局流水（run_profile / probe_farm / watch 事件） |
| `runtime/mas_dispatch_state.json` / `mas_dispatch_alerts.json` | 当天发放记录 / 未消除告警台账 |
| `runtime/mas_ondemand_ledger.json` | 按需发车共享账本（busy 窗口与单飞判据） |
| `runtime/instance_leases.json` | 实例租约 |
| `runtime/activity_switch_state.json` / `runtime/activity_switch/backups/<switch_id>/` | 活动切换台账与还原快照（未还原 = 存在「临时-活动-*」表 + StageMode 指针） |
| `runtime/mas_dispatch.lock` / `runtime/mas_probe_farm.lock` / `runtime/activity_switch.lock` | 跨进程单飞锁 |
| `runtime/scheduler_watchdog.log` / `_state.json` / `_alerts.jsonl` | 调度看门狗每轮探测 / 告警生命周期 / 告警事件流（`docs/SCHEDULER_WATCHDOG.md`） |
| 主机 `E:\AUTO-MAS\history\<date>\<账号>\*.json` | **账号级权威口径**（maa_result + 同批 .log）；`debug/app.log` 任务事件 |
| MAA 目录 `debug/gui.log`、`debug/asst.log`、`config/gui.new.json` | 切号信号 / 任务链锚点 / 注入读回面 |

---

## E. 写新工具/新脚本之前先查重（防「重复实现同一能力」）

1. 先查本页 §B 与 `ls tools/`；用 `search_files` 在 `tools/` 里按关键词搜 docstring（pattern 例：`argv|add_argument|"""`）。
2. 再看 `docs/` 的 `MAS_*.md` 与 `docs/audits/` 最近条目（同日已有 tooling audit）；跨会话先查知识库 `D:\1-2\MAS-KB`（技能 `wiki-search`）。
3. 优先**扩展现有工具**（加子命令/参数/JSON 字段），而不是新脚本；确需新脚本时：默认 dry-run + `--selftest` + 末行判据 + 审计留痕，缺一不可。
4. 已知能力↔工具一言表（速查）：发车=ondemand/dispatch ｜ 按配置刷=run_profile ｜ 试探=probe_farm ｜ 计划=plan_builder ｜ **整链编排（决策→试探→执行→还原）=farm_orchestrator** ｜ 台账=stage_capability ｜ 班前=preflight ｜ 互斥=lease ｜ 快照=config_snapshot ｜ 定因=error_triage/maa_error_daily ｜ 日报=shift_report ｜ 场景=scenario_dispatch ｜ 刷哪里划算=stage_efficiency ｜ 活动切换=activity_switch ｜ **端口/映射=mas_mapping** ｜ 审计统一（写/查/轮转）=mas_audit。

---

## F. 深挖文档索引

* 单工具规范：`docs/MAS_ONDEMAND.md`（含闸门 G0–G5 全表）、`MAS_RUN_PROFILE.md`、`MAS_PROBE_FARM.md`、`MAS_PLAN_BUILDER.md`、`MAS_FARM_ORCHESTRATOR.md`（端到端编排：11 步链路/退出码/首跑预算）、`MAS_SCENARIO_DISPATCH.md`、`MAS_INSTANCE_LEASE.md`、`STAGE_EFFICIENCY.md`（关卡效率与材料需求库：口径/来源时效/局限）、`MAS_ACTIVITY_SWITCH.md`（活动期刷取计划受控切换：三判据/闸门/还原/实跑）、`SCHEDULER_WATCHDOG.md`（外部看门狗：为什么不能用 Hermes cron 看护 Hermes / 判据阈值 / 计划任务注册与回滚）、`MCP_TOOL_ROADMAP.md`（MCP 面设计+契约）、`MAS_AUDIT_SCHEMA.md`（审计统一：schema v1 / 词表 / 轮转保留 / 桥接映射 / 现存工具接入建议）、`KANBAN_HEALTH.md`（看板治理体检：五类判据/误报防线/一行取数）。
* 机制/地图：`MAS_MASTER_HANDBOOK.md`（全景）、`MAS_SCHEDULE_MAP.md`、`MAS_SCRIPTS_CATALOG.md`、`MAS_EMULATORS.md`、`MAA_TASK_CONFIG_REFERENCE.md`、`MAS_INTERNALS.md`、`DISASTER_RECOVERY_DRILL.md`（从零恢复演练：重建 SOP / RTO·RPO 实测 / 失败点；校验器 `tools/mas_dr_verify.py`）。
* 策略/红线：`MAS_RERUN_POLICY.md`、`KANBAN_DISPATCH_POLICY.md`、`HERMES_MAS_HANDOVER.md`（硬边界 §6/§7）、`MAS_EMERGENCY_RUNBOOK.md`、`MAS_RUNBOOK.md`。
* 战术手册：`MAA_ROGUELIKE_PLAYBOOK.md`、`MAA_EVENT_FARMING_PLAYBOOK.md`、`HERMES_ACCOUNT_INSPECT_PLAN.md`（上号读数三条路径）。
* 技能：`mas-ops-toolbox`（本页的会话速查版）、`auto-mas-host-ops`、`maaorch-queue-triage`、`maa-task-config`、`maa-roguelike-ops`、`maa-event-farming`、`wiki-search`。
