# 文档 ↔ 实装 一致性核验报告（DOC_IMPL_CONSISTENCY）

> 核验：2026-09-28 · kanban `t_5993a91f`（assignee mas）· 手法：**只读 + 实跑 `--help`/`--selftest`**
> 本轮未改任何既有文档/代码（仅新增本报告）。核验对象 = 近一天 worker 产出的 6 份文档。

## 一句话结论

**可复核范围内，这些文档的"可执行口径"基本可信**——默认 dry-run、CLI 参数名、默认值、输出/审计文件路径、
失败语义（退出码）、闸门默认开启，全部与实装实测一致；**未发现会导致误发车/误删队列/误改配置的高风险差异**。
另有 **1 项实现缺陷**（`--no-keep-remain` 在 help 里存在但实际不可用，键入即 rc=2）与 **6 项文档陈旧/笔误/口径不清**（均为低风险，逐条见 §2），修法都不涉及行为变更。

---

## 0. 高风险差异（置顶）

**无。** 本轮重点核了 5 类"会害人"的模式，结论全部一致：

| 高风险模式 | 结论 | 证据 |
|---|---|---|
| "文档说默认 dry-run，实际不是" | 未发现 | 4 个 CLI 工具 + MCP 面：缺省全为 dry-run；写动作要显式 `--apply`（逐工具实测 + selftest 断言"dry-run 零写"用例通过） |
| "文档说的输出/审计路径，实际写别处" | 未发现 | 路径逐条比对一致（§3） |
| "退出码语义不符" | 未发现 | run_profile 0/2/3/4、ondemand 0/2、probe_farm 0/2/3/4/5 均与文档一致 |
| "闸门/锁默认没开" | 未发现 | G0–G5/LOCK 全默认生效；CLI↔MCP 共锁共账（`mas_dispatch.lock` + `mas_ondemand_ledger.json`） |
| "单飞/重复保护没生效" | 未发现 | 重复窗口 150 分、资源冲突 20 分、slot_busy/slot_warm 现场判据均在代码里默认启用 |

最接近高风险的 4 项（均 **fail-closed 或良性**，供 lead 优先处理）：

1. `tools/mas_plan_builder.py` 的 `--no-keep-remain` 是**幽灵开关**（实装 argparse 把 `"--keep-remain/--no-keep-remain"` 当**一个字面选项名**；`--keep-remain` 因缩写匹配侥幸可用，`--no-keep-remain` 直接 `unrecognized arguments`，rc=2）。**用了只会报错，不会误写**。
2. `docs/MAS_RUN_PROFILE_TOOL.md:109` 的 `MAAORCH_PY312` **在仓库中不存在**（grep 全仓仅此一处）；真实变量是 `MAAORCH_RUN_PROFILE_PYTHON`。按文档设置该变量会被**静默忽略**（行为退化为默认解释器，不是危险方向）。
3. `docs/MAS_PLAN_BUILDER.md:4` "**默认零写**"不准确：默认运行会落**草案文件**（`runtime/reports/plans/<账号>_<stamp>.{md,json}`）+ 审计行；只有 **AUTO-MAS 配置侧**是零写、不发车。
4. 文档幽灵参数 `--stop-on-timeout`（`docs/MAS_RUN_PROFILE_TOOL.md:97`）：实装只有 `--no-stop-on-timeout`；键入前者 rc=2 报错（默认行为本来就是"超时即 stop"，不影响语义）。

---

## 1. 核验方法（可复跑）

```
# 1) 实跑 --help（4 个 CLI 工具，Python312）
python tools/mas_run_profile.py  --help
python tools/mas_ondemand.py     --help
python tools/mas_probe_farm.py   --help
python tools/mas_plan_builder.py --help
# （mas_ondemand_mcp.py 是 stdio server，无 argparse --help；自检入口为 --check/--selftest）

# 2) 离线自测实跑（各工具自证"零触网/沙箱写"：tempfile.mkdtemp，不碰 repo runtime）
python tools/mas_run_profile.py  --selftest   # → SELFTEST=OK (29/29)
python tools/mas_ondemand.py     --selftest   # → SELFTEST=OK (30/30)
python tools/mas_ondemand_mcp.py --selftest   # → mas_ondemand_mcp --selftest: OK (18/18)
python tools/mas_probe_farm.py   --selftest   # → SELFTEST=OK (23/23)
python tools/mas_plan_builder.py --selftest   # → SELFTEST=OK (37/37)

# 3) argparse 定义逐项导出（AST）+ 关键字默认值比对（脚本，只读）
# 4) 反向验证幽灵参数（直接对实装跑，argparse 在开工前即退出）：
python tools/mas_plan_builder.py --no-keep-remain --selftest   # rc=2 unrecognized arguments
python tools/mas_run_profile.py  --stop-on-timeout             # rc=2 unrecognized arguments
```

完整 `--help` 快照与 selftest 输出留档：
`C:\Users\xiach\AppData\Local\hermes\profiles\mas\cache\scratch\doccons\{mas_*.txt, st_mas_*.txt, args.json}`

---

## 2. 差异表（文档项 | 实际 | 严重度 | 建议改哪边）

### 2.1 `docs/MAS_ONDEMAND.md`

| # | 文档项（行号） | 实际 | 严重度 | 建议 |
|---|---|---|---|---|
| A1 | §3 自测 `--selftest # 离线回归（27 例，假传输）`（:47） | 实测 **30/30**；同文 §7（:122）也是"30 例全过" ⇒ **文档内部自相矛盾** | 低 | **改文档**：27 → 30（或删数字） |
| A2 | §7 真发车最小验证表"state 落 `mas_ondemand_state.json`"（:141） | 属 06:31 时点历史记录；同日 t_4ddb3728 后账本已统一为 `mas_ondemand_ledger.json`（同文 §10 已说明） | 低 | **改文档**：该行加"（历史；§10 已收口）"，避免读者去翻旧文件 |
| A3 | 全文未提 `--events`（80）/`--timeout`（240）/`--preflight-timeout`（900） | `--help` 中确实存在（默认值如上） | 提示 | **可选补文档**（不影响使用） |
| A4 | §7"线上只读自测：12 槽位在跑…"、真发车/失败归因表 | 历史交付快照，本轮未重跑（见 §4） | — | 保留（建议注明"当日快照"） |

其余全部**一致**（核过：G0–G5/LOCK 判据与理由码、`MAAORCH_ONDEMAND_{HOLD,DUP_WINDOW,WARM_QUIET,CONFLICT_GRACE}_MIN` 默认 150/150/8/20、临时代理队列命名 `临时-按需-<yyyymmdd_HHMM>-<n>`、`--cleanup` busy 守卫、`--wake` 必须 `--force`、审计双流分流（调用流含干跑/被拒，事件流 only 真动作）、共享账本 schema 与旧账只读合并、CLI/MCP 共锁、`--apply` 撞锁 rc=2）。

### 2.2 `docs/MAS_RUN_PROFILE_TOOL.md`

| # | 文档项（行号） | 实际 | 严重度 | 建议 |
|---|---|---|---|---|
| B1 | §5 运维命令 `--stop-on-timeout/--no-stop-on-timeout`（:97） | 实装**只有** `--no-stop-on-timeout`；`--stop-on-timeout` 键入即 `unrecognized arguments` (rc=2)。默认行为=超时 stop | 低（fail-closed） | **改文档**：写成"默认即超时 stop；`--no-stop-on-timeout` 关闭" |
| B2 | §5 "env 覆盖：…、`MAAORCH_PY312`"（:109） | `MAAORCH_PY312` **全仓不存在**（grep 仅此一处）；真实变量 `MAAORCH_RUN_PROFILE_PYTHON`（preflight 侧另有 `MAAORCH_{MAS_API,SSH_KEY,SSH_HOST,MUMUMANAGER,ADB,PREFLIGHT_SKIP_SCREEN}`） | 中低（静默失效） | **改文档**（推荐）；或补实装支持该别名（不推荐，避免双名） |
| B3 | §5 "`runtime/mas_run_profile.lock` 互斥（长跑期间心跳续期，**`--status` 可看**）"（:108） | 心跳续期 ✓（wait/restore 阶段 `touch()`）；但 `--status` 只列 run 台账 + 未还原告警，**不显示锁状态** | 低 | **改文档**（去掉"--status 可看锁"）或给 `--status` 补一行锁状态 |
| B4 | §5 未提 `--wait-poll-seconds`（20）/`--restore-wait-seconds`（180）/`--json` | `--help` 中存在 | 提示 | 可选补文档 |
| B5 | §7.1 "29 个用例" | `--selftest` = **29/29** ✓ | — | — |

其余全部**一致**（退出码 0/2/3/4 与集合定义、备份目录 `runtime/run_profile/backups/<run_id>/{meta,plans_before,accounts_before,expected,hashes}.json`+sha256、审计 `runtime/mas_run_profile_log.jsonl`+`runtime/mas_ledger.jsonl`、锁 `runtime/mas_run_profile.lock`（6h 陈旧抢占）、默认 `restore_after=run_end`、`--readback-timeout 300/--poll-seconds 15/--restore-attempts 12/--restore-gap-seconds 30`、`--reconcile`/`--restore-run` 缺省 dry-run 需 `--apply`、L1 读 `<MAA目录>/config/gui.new.json`（含 mtime 时效门）、⑥ 判据=app.log「任务结束/中止」或 MAA.exe **连续两次**消失、`MAAORCH_DISPATCH_NO_GO`/`MAAORCH_RUN_PROFILE_NO_GO` 一票否决）。

### 2.3 `docs/MAS_PROBE_FARM.md`

| # | 文档项（行号） | 实际 | 严重度 | 建议 |
|---|---|---|---|---|
| C1 | §5 `--status` "只读：本工具**最近 5 次**记录 + 锁状态"（:98） | 实际：锁状态 ✓ + 日志尾部 **`--tail` 条（默认 15）** + "证据目录（最近 **8** 个）" | 低 | **改文档**：5 → "默认 15 行（--tail 可调）+ 证据目录 8 个" |
| C2 | §5 接口表未列 `--ledger-days`（1）/`--tail`（15）/`--no-rest` | `--help` 中存在 | 提示 | 可选补文档 |
| C3 | §5/§8 "23 条离线用例" | `--selftest` = **23/23** ✓ | — | — |

其余全部**一致**（退出码 0/2/3/4/5、锁 `runtime/mas_probe_farm.lock`（>6h 抢占）、`MAAORCH_PROBE_FARM_NO_GO`/`MAAORCH_DISPATCH_NO_GO` 一票否决、默认 dry-run、`--times` 默认 1 且域=连战倍数 0..6/-1、`--medicine` 0、`--timeout` 600、`--farm-max-wait` 10800、台账 `runtime/reports/stage_capability.json`、全局台账 `runtime/mas_ledger.jsonl` kind=probe_farm、试探 profile 的关键字段（series="1"/medicine/活动关 false/keep_remain 显式/不写 target_drop·use_expiring/restore_after=timeout:Ns）、三态决策与 11 个判定 kind 全在代码里、剿灭拒绝、失败不重试）。

### 2.4 `docs/MAS_PLAN_BUILDER.md`

| # | 文档项（行号） | 实际 | 严重度 | 建议 |
|---|---|---|---|---|
| D1 | 开头 "**默认零写、零发车**"（:4-5），§4 "`--exec-dry-run`（走一遍 run profile 全路径，**不写任何东西**）"（:82） | 默认即**写盘**：`runtime/reports/plans/<账号>_<stamp>.{md,json}` + 审计行 `runtime/mas_plan_builder_log.jsonl`（工具自带 header 明写"输出（默认写盘…）"）。AUTO-MAS 配置侧确实零写、不发车 ✓ | 中低（口径不清） | **改文档**：写"零**配置**写/不发车；默认会落草案文件与审计行"；:82 同理说明"不写 AUTO-MAS 侧" |
| D2 | 工具 `--help` 宣传 `--keep-remain/--no-keep-remain`（实装 source: `tools/mas_plan_builder.py:1214`） | argparse 把它当**字面单选项**：`--keep-remain` 可用（缩写匹配），**`--no-keep-remain` 不可用**（rc=2）。⇒ "不保留剩余理智"无开关可用 | 中（工具缺陷，fail-closed 无副作用） | **改代码**（推荐，1 行）：换成 `argparse.BooleanOptionalAction`；或改 help 只写 `--keep-remain` |
| D3 | §5 "SELFTEST=OK (37/37)" | 实测 **37/37** ✓ | — | — |

其余全部**一致**（输出/审计/输入路径全部命中：`runtime/reports/accounts/<账号>/{operators.json,depot.json}`、`runtime/reports/{stage_capability,server_events}.json`、`config/{material_water_levels,farm_stages}.json`、`data/stage_apcost_v4.json`、`runtime/reports/plans/`、`runtime/mas_plan_builder_log.jsonl`；闸门 `MAAORCH_PLAN_BUILDER_NO_GO=1` ⇒ rc=2 且不调 run profile；生成时先过 run profile 校验器、失败不落盘；`--apply` 转发 `--disable-others/--all-accounts/--write-mode/--max-wait-seconds`；决策规则（缺口/补刀模式/磨难破平 ≤2%（`NORMAL_PREF_MARGIN=1.02`）/练度门槛 章≤9 无要求·10–14 需 E2-60+≥8·≥15 需 ≥15/AP 预算默认 280/1-7 尾位）均与代码一致）。

### 2.5 `docs/MAS_RUN_PROFILE.md`（机制规范）

| # | 文档项 | 实际 | 严重度 | 建议 |
|---|---|---|---|---|
| E1 | §4③ "`tools/mas_preflight.py`（770 行）"；"exit 0=GO 1=NO-GO 2=无法判定" | 770 行 ✓；preflight 退出码 0/1/2 ✓ | — | — |
| E2 | §4/§5 的 `文件:行` 引用全部指向**宿主 `E:\AUTO-MAS`**（远程源码） | 本轮未连主机复核（见 §4）；文档已在头部声明口径（本地 `D:\MAS` 为旧版、行号勿互套） | — | 保留 |
| E3 | §0-2 "`<MAA目录>/config/gui.new.json` 是产物不是源头"、§4 时序、"只刷 N 次无字段"等机制结论 | 与 `tools/mas_run_profile.py` / `probe_farm` 的实装行为一致（实现即按此口径写的） | — | — |

### 2.6 `docs/HERMES_ACCOUNT_INSPECT_PLAN.md`（勘察/方案）

本机可复核引用**全部命中**：

| # | 文档项 | 实际 | 严重度 | 建议 |
|---|---|---|---|---|
| F1 | `MAAOrch:tools/mas_ondemand.py:1024-1025` 的 ManualReview 警告 | 实际就是 G5 `wake_needs_force` 块（:1024 起，警告文案在 :1026）✓ 精确 | — | — |
| F2 | `docs/HERMES_MAS_PLAYBOOKS.md:29`"同目录并发=互杀" | ✓ 该行即"槽位…同目录同一时刻只允许一个任务（互杀按目录）" | — | — |
| F3 | 本机资产：`tools/maa_operbox.py`（20003/OperBox 前缀 ✓）、`services/depot_reader.py`（Depot 任务/DepotInfo 前缀/`models/accounts/<id>/depot.json` ✓）、`tools/box_read_when_free.py`、`services/maa/instances/{1..3}` | 全部存在 ✓ | — | — |
| F4 | `docs/MAA_CLI_SURVEY.md:133-135`（16 种 type 含 Depot/OperBox）、`:277`（任务文件 `account_name`） | ✓ 命中 | — | — |
| F5 | §0 主机侧实测（MAA 6.18.0、`MAA_6\data\DepotData.json` 存在、全主机无 OperBoxData.json、宿主 Python 3.12.9 等） | **未复核**（需连主机，本轮约束未做） | — | 若闭环依赖这些数，另起只读复核卡 |

---

## 3. 逐项一致清单（抽查过、确认没问题的部分）

- **默认 dry-run**：run_profile / ondemand / probe_farm / plan_builder（exec 面）/ MCP 6 工具，全部缺省只读；写动作需 `--apply`（MCP 需 `apply=true`）。
- **参数名/默认值**：4 份 `--help` 与文档逐条比对，除 §2 列出的条目外全部一致（含所有超时/重试/窗口默认值）。
- **输出与审计文件**（文档声明 vs 实装常量）：见 §2.1–2.4"其余全部一致"括号内清单——全部命中，无一处指向错误路径。
- **失败语义**：rc 集合与含义一致；拒绝路径零写请求（G2 解析失败即拒）；止损走 `dispatch/stop`（不 kill MAA）。
- **互斥**：`mas_dispatch.lock`（CLI↔MCP↔cron 同源）、`mas_run_profile.lock`、`mas_probe_farm.lock` 与文档一致；陈旧阈值 1500s / 6h / 6h 一致。
- **口径澄清（非差异，但建议后人知道）**："dry-run"指**不写 AUTO-MAS 配置、不发车**，不等于零落盘——三处 dry-run 都会写**自己的审计行**（run_profile 的 `plan_dry_run`；probe_farm 的 dry-run 会再拉一次 run_profile dry-run；plan_builder `--exec-dry-run` 同理）。实测证据：`runtime/mas_run_profile_log.jsonl` 里有 probe_farm 发起的 `plan_dry_run` 行（如 `pf_20260928_090109_Raiz/profile.probe.json`）。

---

## 4. 未复核项（本轮边界，如实标注）

1. **主机侧/远程源码引用**：`MAS_RUN_PROFILE.md` 与 `MAS_ONDEMAND.md` 里 `E:\AUTO-MAS` / `D:\MAS` / MAA 源码的 `文件:行`（属另一台机的状态）；`HERMES_ACCOUNT_INSPECT_PLAN.md` §0/§8 的主机实测值。— 本轮约束=只读 + `--help`，未连主机。
2. **live 交付记录**（MAS_ONDEMAND §7 的"--list-running 12 槽位"、真发车一次的表；MAS_PROBE_FARM §9.1 的 LEUNG×LS-5 记录）：历史快照，未重跑（其产物文件在 `docs/audits/` 留档）。
3. 本轮核验跑动说明：`--help`/`--selftest` 均为只读或沙箱（tempfile）写，`PYTHONDONTWRITEBYTECODE=1`；**repo 内未新增/修改任何既有文件**（仅本报告）。注意：核验期间 `runtime/` 下确有其它并发 worker 的正常写入（probe farm/watchdog 等），与本次核验无关。

---

## 5. 修复清单（**先不改；交 lead 决策**，按建议优先级）

| P | 动作 | 位置 | 说明 |
|---|---|---|---|
| P1 | 改代码（1 行） | `tools/mas_plan_builder.py:1214` | `--keep-remain/--no-keep-remain` → `action=argparse.BooleanOptionalAction`（同时修 usage/help 渲染），或退而只保留 `--keep-remain` |
| P2 | 改文档 | `docs/MAS_RUN_PROFILE_TOOL.md:109` | `MAAORCH_PY312` → `MAAORCH_RUN_PROFILE_PYTHON`（并顺带列 preflight 的 env 名） |
| P3 | 改文档 | `docs/MAS_ONDEMAND.md:47` | 27 例 → 30 例（或删数字；同文 §7 已是 30） |
| P4 | 改文档 | `docs/MAS_PROBE_FARM.md:98` | "最近 5 次" → "最近 15 行（`--tail`）+ 证据目录最近 8 个" |
| P5 | 改文档 | `docs/MAS_PLAN_BUILDER.md:4-5 与 :82` | "默认零写" → 明确"零**配置**写/不发车（默认落草案 .md/.json + 审计行）" |
| P6 | 改文档 | `docs/MAS_RUN_PROFILE_TOOL.md:97,:108` | 删幽灵参数 `--stop-on-timeout`；删/修"`--status` 可看锁" |
| P7 | 可选补文档 | 三份 | 补 `--help` 有而文档无的选项（A3/B4/C2 及 `--json` 输出说明） |
| P8 | 可选（低价值） | `docs/MAS_ONDEMAND.md:141` | 历史行加"（§10 已收口到 mas_ondemand_ledger.json）"注记 |

> 只读核验未发现需要"改代码救急"的项：P1 是唯一代码项，且影响面=一个开关报错（无副作用）。