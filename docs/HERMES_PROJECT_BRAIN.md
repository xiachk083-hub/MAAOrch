# MAAOrch 项目大脑（Hermes 视角）：定位 · 路由 · 能力状态 · 学习闭环

> 建立：2026-09-28 11:3x CST ｜ 任务 kanban `t_1106669c`（板 `maa-parallel-benchmark-20260926`）｜ 性质：**只读汇编**（未发车、未改配置、未碰设备/生产机）
> 用途：**任何新会话进入本仓库的第一入口**——先读本页完成「定位 → 选路 → 状态判定」，再按指向的专业文档/技能执行；本页只给结论与跳线，不复制全文。
> 口径：**现场 > 子文档 > 本页**。本页每条事实都带来源与证据标签（标签定义见 §4.1），非本次实测的均标注来源日期与复验方式。
> 姊妹入口：会话技能 `.agents/skills/maaorch-project-brain/SKILL.md`（压缩版，随仓库加载）｜ 工具箱 `docs/TOOLBOX.md`（哪件事用哪个工具）｜ 业务全景 `docs/MAS_MASTER_HANDBOOK.md`（谁是谁）｜ 出事 `docs/MAS_EMERGENCY_RUNBOOK.md`。

---

## 0. 一页速读

- **系统 = 两机 + 四层**：主机（`100.79.173.69`）跑 AUTO-MAS + 12 个 MAA 槽位 + MuMu 机队；本机（`D:\1-2\MAAOrch`）跑 Hermes 调度/巡检/工具链。
- **Hermes 的角色（2026-09-27 起）**：既是调度员也是巡检员——班次发车、30 分钟巡检、补跑判定、按需发车、场景发车（受限）。
- **三条铁律**：**只读优先** → 写动作必须**显式 + 可回滚 + 留痕** → **先查文档/知识库再动手**（`D:\1-2\MAS-KB`，技能 `wiki-search`）。
- **开工三步**：读本页 §2/§3 → 查实时状态（不背旧结论）→ 判断只读 / 有副作用（有副作用 ⇒ 批准 + 闸门 + 收尾核验）。
- **红线速记**：守卫窗不发车（05:00–05:40 / 19:20–20:20）；同一 MAA 目录同一时刻一个任务；止损用官方 stop、**别 kill MAA**；凭据不落盘；不手改 `ScriptConfig.json` / `QueueConfig.json`。

---

## 1. 定位与系统边界

### 1.1 角色（谁是谁）

| 角色 | 归属 | 一行职责 | 权威边界 | 来源 |
|---|---|---|---|---|
| Harness（Hermes / DSH） | 本机 mas profile + cron | 大脑：定目标、排期、巡检、补跑、发车 | 计划与目标（本仓不存权威副本） | `POSITIONING.md` §1（2026-09-26 核查） |
| MaaCore / MaaWpfGui | 主机 `E:\MAA-Multi\MAA_1..12` | 单设备游戏自动化：一份注入配置 → 一次任务链 | 单账号单次执行结果（`asst.log`/`gui.log`） | `MAS_MASTER_HANDBOOK.md` §1.2 |
| AUTO-MAS v5.4.0 | 主机 `E:\AUTO-MAS`，REST/MCP `:36163` | 多账号调度器：注入 / 队列 / 班次 / history（**已存在，本仓不重写**） | 远端队列、账号、班次真相 | 手册 §1.2 / `POSITIONING.md` §3 |
| MAAOrch（本仓） | 本机 `D:\1-2\MAAOrch` | 粘合剂：状态收敛 / 命令翻译 / 事件归一 / 安全闸门（+ 工具链与文档） | 设备与 MAA 进程状态（本机侧） | `POSITIONING.md` §1–§2 |
| 肉鸽机队 `l-*`（fz-maa） | 主机 `E:\maa-alas\fz-maa` | 肉鸽连刷 + 看守 | 机队看守（与本仓产线边界互斥） | `MAA_DRIVER_OPTIONS.md` §2.2 |

MAAOrch **明确不做**（`POSITIONING.md` §1）：不做大脑（计划权威在 Harness）；不做研发工作台（`runtime/_pilot`、`*probe*` 不属契约面）。

⚠️ **边界图景漂移（UNVERIFIED）**：`POSITIONING.md`（09-26）与 `STRUCTURE.md`（08-12 定型）仍描述 **in-repo Web/Node 前端**（看板 19999、`ui/web`）——该图景**部分已被推翻**：2026-09-27 提交 `retire(frontend)`（前端整体退役、按 49 条白名单删 91 条路由，见 `git log`）。当前在役 in-repo 面 = Python API（`127.0.0.1:19997`，按需拉起；见 `docs/http-api.md`）+ `tools/*` 工具链。引用这两份文档的前端/UI 章节前，先对照 `git log` 与 `docs/http-api.md`；其余章节（分层/四职责/MCP 结构）本卡**未逐行复核**，真值源 = 代码。

### 1.2 两台机器与端点

| 项 | 值 | 备注 |
|---|---|---|
| 主机 | `100.79.173.69`（MRFZ-0000） | `E:\AUTO-MAS` 侧车；`E:\MAA-Multi\MAA_1..12`；MuMu 55 实例；maa-cli `E:\MAA-CLI` |
| 本机 | DESKTOP（`D:\1-2\MAAOrch`） | 调度器 / 工具 / 文档 / 审计 + Hermes cron |
| SSH | `ssh -i ~/.ssh/maaorch_target Administrator@100.79.173.69` | 查询类用 base64 EncodedCommand，避免引号假读数（手册 附录 B） |
| AUTO-MAS REST | `http://100.79.173.69:36163`（OpenAPI 副本 `_ams.json`） | 只读端点 `/api/scripts|queue|user|item|time|plan|history|overview` |
| MAAOrch API | `http://127.0.0.1:19997/mcp/`（41 工具） | **不常驻**（代理按需拉起，首次 ≤45s；设计如此，别当故障） |
| 掩码口径 | 官服 = 前 3 + `****` + 后 4（如 `134****5276`） | 一切报告/台账只写掩码；token / hgId **不出设备** |

### 1.3 每日节律（时间窗速记）

```
04:00 / 19:00  主机计划重启（事件 id=1074；排期须规避）
04:01 / 19:01  AUTO-MAS 惯性自重启（计划内；会吞在飞任务）
05:00–05:33    早班错峰发车（12 槽 +3min，cron 每 3 分钟对账）
05:00–05:40    守卫窗：不发车 / 不补跑 / 不动配置
05:40 起       30 分钟只读巡检（cron 52b88b0ffca0）
19:30–20:03    晚班重演；19:20–20:20 守卫窗
侧车重启禁窗   05:00–08:00 / 19:30–23:00（吞在飞任务 = 孤儿化）
肉鸽让位区     04:00–05:33 / 18:30–20:30（不发车 + 让路）
```
来源：`MAS_MASTER_HANDBOOK.md` §1.4/§4、`MAS_RERUN_POLICY.md` §3、`MAA_DRIVER_OPTIONS.md` §2.2。纪律：跨窗**不要 worker 内 sleep 硬等**——用 `hermes kanban schedule` 停放或 cron 到点（`KANBAN_DISPATCH_POLICY.md` §2.4）。

---

## 2. 任务 → 工具路由（Hermes 选路）

> 总原则：**生产 Maa 任务一律走 AUTO-MAS 主路径**（P1：记账 / 通知 / 锁 / 切号 / 还原 / 巡检闭环都在它里面）；maa-cli（P2）是**受限第二驱动**；MaaFw（P3）是取证层。依据 `MAA_DRIVER_OPTIONS.md` §0/§2。
> 每一行的「首选工具」细节（最少命令 / 禁忌）见 `docs/TOOLBOX.md` 对应节；**不要新写脚本、不要手搓 REST/临时队列**（查重见 TOOLBOX §E）。

| 意图 | 首选（入口） | 边界 / 备选 | 详细 |
|---|---|---|---|
| 队列级发车/补发/对账（24 条班次） | `tools/mas_dispatch.py`（cron `2600f6415f16` `*/3 5,19,20`） | **只归它**；超追发窗（>30min）不自动补，只记 late+告警 | TOOLBOX B10 |
| 账号级补跑某个号 | `tools/mas_ondemand.py` / `auto_mas_rerun.py`（+MCP `mas_rerun`） | 与队列级**正交**，互不替代；busy 窗内拒 | TOOLBOX B1 |
| 按配置整轮刷（关卡/连战/药/活动优先） | `tools/mas_run_profile.py`（八步：备份→写→preflight→发车→读回→等完→还原→审计） | 默认 plan 路线；多号脚本须显式 `--disable-others/--all-accounts` | TOOLBOX B2 |
| 生成「该刷什么」计划 | `tools/mas_plan_builder.py`（默认落草案 .md/.json；AUTO-MAS 侧零写） | 前置=该号有识别产物（B8）；活动快照 >7 天拒用 | TOOLBOX B3 |
| 查「这个号能不能刷这关」 | `tools/stage_capability.py`（台账唯一生成者） | 30 天窗口「刷过没有」≠「今天开放」 | TOOLBOX B4 |
| 拿不准 → 先试探后开刷 | `tools/mas_probe_farm.py`（三态决策） | failed 默认拒；一次最小试探，失败不重试 | TOOLBOX B5 |
| 整链编排（决策→试探→执行→还原） | `tools/mas_farm_orchestrator.py` | 首跑 2026-09-28 真演练 rc=0（单例，未常态化） | TOOLBOX B5b |
| 失败定因 / 今天错在哪 | `tools/mas_error_triage.py`、`maa_error_daily.py`、`mas_shift_report.py` | 读忙时 `asst.log` 会慢——先轻读聚合 | TOOLBOX B6/B7 |
| 上号读干员/仓库 | 主机 `tmp/boxread/host_boxread.py`（未提升为正式工具）；本机 `maa_operbox.py` | 游戏必须在大厅/标题页 | TOOLBOX B8 |
| 配置快照/回滚（AUTO-MAS） | `tools/mas_config_snapshot.py` | 含凭据文件全保副本只留主机 | TOOLBOX B9 |
| 看现场：谁在跑/卡没卡/空转 | `mas_ondemand.py --list-running`、`mas_idle_emulators.py`、`client_state_check.py` | 关实例动作永远人工拍板 | TOOLBOX B11 |
| 串号 / 每轮身份留痕 | `mas_switch_gate.py --check`、`mas_identity_trace.py` | 权威切号信号 = `gui.log`「账号切换 -->> 掩码」 | TOOLBOX B12 |
| 巡检健康：补丁/自动路径/主机指标 | `mas_patch_guard.py`、`mas_agent_time.py`、`host_perf.py`、`mas_watch.py` | 重放/录制是受控写动作（默认 dry-run） | TOOLBOX B13 |
| 「今天该刷什么/谁该补」只读决策件 | `mas_daily_advice.py`、`mas_catchup_check.py` | 候选 ≠ 许可 | TOOLBOX B14 |
| 场景发车：肉鸽 / 活动 / 剿灭 | `tools/mas_scenario_dispatch.py`（**草案，双闸默认关**） | apply 从未实跑（见 §4.4）；肉鸽生产连刷走机队看守 | TOOLBOX B15 |
| 账号清单 / 在哪台 / 端口 | `mas_accounts.py --dump --schedule`、`mas_mapping.py`（端口现查！） | 清单是现取，别用旧快照 | TOOLBOX B16/B20 |
| 刷哪里划算（关卡效率） | `tools/stage_efficiency.py` | 公开数据是快照；不判开放状态 | TOOLBOX B17 |
| 活动开/关自动切换刷取计划 | `tools/activity_switch.py`（`--apply` 且 `--approved-by`） | 本卡未见 apply 实跑记录（见 §4.4） | TOOLBOX B18 |
| 调度还在不在（gateway/cron/发车心跳） | `tools/scheduler_watchdog.py` | 计划任务注册**待批准**（见 §4.4） | TOOLBOX B19 |
| 看板健不健康 | `tools/kanban_health.py`（只读，只建议不处置） | 处置权归 lead | TOOLBOX B20 / `KANBAN_HEALTH.md` |
| 临时单设备实验 / 模板编制 | **maa-cli**（本机：模板/convert/dry-run；主机：闲置实例单发，需批准） | **不用于**日常批跑/补跑/活动编排/切号（击穿记账与判据闭环） | `MAA_DRIVER_OPTIONS.md` §0；技能 `maa-cli-ops` |
| 截图 / 取证 | 既有 adb 链（`client_state_check`/`preflight`）；本机高频=`maafw_capture` | maa-cli 无截图能力 | `MAA_DRIVER_OPTIONS.md` §0/§1.3 |

**急停一票否决（环境变量）**：`MAAORCH_DISPATCH_NO_GO=1`（一切发车）、`MAAORCH_PROBE_FARM_NO_GO=1`（试探）、`MAAORCH_SCENARIO_APPLY=1`（场景第二道闸）、`MAAORCH_SCENARIO_NO_GO=1`（场景一票否决）。来源：TOOLBOX §0 / B15。

---

## 3. 权威事实源（谁说的算）

> 口径优先级：**现场（机器证据） > 子文档 > 总纲/本页**（手册 §0）。凡文档声称 ≠ 当前真值，以现场为准并回写。

| 状态 | 权威（唯一口径） | 落地位置 |
|---|---|---|
| 账号级执行结果 | AUTO-MAS history | 主机 `E:\AUTO-MAS\history\<班次日>\<账号>\*.json`（`maa_result`，DONE=成功）+ 同批 `.log` |
| 切号是否发生 | MAA 目录 `debug/gui.log` | 「账号切换 -->> <掩码>」每实际切换一行；**同号重登无切换行**（切号失败是静默的） |
| 任务链级成败 | `debug/asst.log` | `TaskChainCompleted` / `TaskChainError` / `AllTasksCompleted`；中文报错串（判据字典 `docs/MAA_LOG_DICTIONARY.md`） |
| 班次/发车账 | 本机 `runtime/` | `mas_dispatch_state.json`（当天发放）、`mas_dispatch_log.jsonl`、`mas_dispatch_alerts.json`；统一审计 `runtime/audit/mas_audit.jsonl` |
| 按需发车账 | 本机 `runtime/` | `mas_ondemand_ledger.json`（CLI/MCP 唯一账）、`mas_ondemand_log.jsonl`（含干跑/被拒） |
| 实例互斥 | 本机 `runtime/instance_leases.json` | 碰实例前先 `mas_instance_lease.py acquire` |
| 单飞锁 | `runtime/*.lock` | `mas_dispatch.lock`（班次/按需/MCP 同源一把）、`mas_probe_farm.lock`、`activity_switch.lock`、`run_profile.lock` |
| 端口 / 实例映射 | **现查**：`tools/mas_mapping.py` | 端口会漂（idx27 实测 17248→17249）；**别按公式写死，别信 MuMuManager 状态面** |
| MCP 工具面 | `hermes -p mas mcp list` / `mcp test <srv>` | 六 server 契约计数 38（见 §附录 B）；工具面刷新=「关→开」enabled 或重启网关 |
| 巡检结论 | cron `52b88b0ffca0` 输出 | `%LOCALAPPDATA%\hermes\profiles\mas\cron\output\52b88b0ffca0\`（**deliver=local：没有推送通道**） |

**反例（禁止）**：把 MAS 队列/账号在本机镜像成可写权威（双事实源）；拿 `data/daily_state/*.json` 当「今日是否完成」依据（长期不更新）；用 `ScriptConfig` 模式等任务链（等不到）。

---

## 4. 能力状态分类（证据标签）

### 4.1 标签定义与证据要求

| 标签 | 含义 | 最低证据要求 |
|---|---|---|
| `IMPLEMENTED` | 代码/工具在仓库可用（含 `--selftest`/离线用例） | 文件路径 + 自测计数（**不证明连过真机/已接线**） |
| `TESTED` | 有实跑/演练证据 | 标**测试环境 / 版本 / 日期**；单次证据 ≠ 常态化 |
| `DEPLOYED` | 进入生产运行路径（cron / 巡检 / 日常操作） | 标**机器 / 位置 / 日期** + 近期运行证据（执行日志/台账） |
| `PROPOSED` | 方案/草案/待批准，未接线未实跑 | 指向方案文档；写明缺哪一步才可用 |
| `UNVERIFIED` | 声称无法用本机证据确认（或本卡未复验） | 必须写出**要查的真值源** |
| `DEPRECATED` | 明确被推翻/退役 | 写明替代物与推翻依据（提交/审计） |

**纪律**：**设计 ≠ 可用**。文档里"已设计/已自测/ dryness 通过 / 演练通过"都**不等于**生产可用；任何写路径只有在**有实跑证据 + 接线位置 + 回滚方式**三者齐全时才可标 `DEPLOYED`。

### 4.2 能力表（本卡快照 2026-09-28 11:31；26 行）

| # | 能力 | 标签 | 关键证据 / 来源 | 复验方式 |
|---|---|---|---|---|
| 1 | 班次发车（队列级 24 条） | DEPLOYED | cron `2600f6415f16` active（11:31 实查，last ok 05:57）；`mas_shift_reconcile.py` | `mas_dispatch.py --list / --busy` |
| 2 | 账号级补跑 | DEPLOYED | `HERMES_MAS_HANDOVER.md` §12 接线记录；rerun 流程在用（09-26 起） | `mas_rerun_state` / `--list-running` |
| 3 | 30 分钟只读巡检 | DEPLOYED | cron `52b88b0ffca0`（11:10 ok；提示词 v4：主表 16 行/现场九检，`MCP_TOOL_ROADMAP.md` §12.1） | `hermes -p mas cron list` |
| 4 | 按需发车（CLI + MCP `mas-ondemand`） | DEPLOYED | `MCP_TOOL_ROADMAP.md` §10 落地记录（09-28） | `mas_ondemand.py --list-running` |
| 5 | run_profile 八步按配置刷 | DEPLOYED | 证据批 `docs/audits/2026-09-28_mas_run_profile_live.*` | `mas_run_profile.py --status` |
| 6 | probe_farm 试探 | DEPLOYED | 证据批 `docs/audits/2026-09-28_mas_probe_farm_live.*` | `mas_probe_farm.py --status` |
| 7 | plan_builder 计划草案 | DEPLOYED | selftest 37/37 + 干跑留档（`DOC_IMPL_CONSISTENCY.md` §2.4） | `--selftest` |
| 8 | stage_capability 能力台账 | DEPLOYED | `docs/audits/2026-09-28_stage_capability_qa/`；被 plan/activity 消费 | `--account <号> --json` |
| 9 | farm_orchestrator 端到端刷关链 | TESTED | 2026-09-28 首跑真演练 rc=0（commit `62feab6`；`docs/audits/2026-09-28_farm_orchestrator_rehearsal.md`）——**单例，未常态化** | `--status` |
| 10 | scenario_dispatch 场景发车（肉鸽/活动/剿灭） | IMPLEMENTED | selftest 44/44 + 三场景干跑 GO；**apply 从未实跑**、双闸默认关（`MAS_SCENARIO_DISPATCH.md` §7.1） | 干跑 + 读 §7 诚实清单 |
| 11 | activity_switch 活动期计划切换 | IMPLEMENTED | 干跑报告 `runtime/reports/activity_switch/*`（09-28 10:07–10:20）；**`runtime/activity_switch_state.json` 不存在 ⇒ 尚无 live apply** | `--status` |
| 12 | 切号注入闸门 switch_gate | DEPLOYED | 巡检行 `SWITCH_GATE=`；审计 09-27；**遗留 `MAA_10/MAA_12` VIOLATION 待一次性清理**（方案未执行） | `mas_switch_gate.py --check` |
| 13 | 补丁持久化闸门 patch_guard | DEPLOYED | `PATCH_GUARD=OK` 4/4；MCP `mas-dispatch` 第 5 工具；审计 09-28 | `--check` |
| 14 | 统一审计 + 水位判据 | DEPLOYED | `mas_audit.py` + `audit_waterline.py`（卡 `t_537fbff2`）；`docs/audits/2026-09-28_audit_patrol/` | `AUDIT_STATUS=` |
| 15 | 看板体检 kanban_health | DEPLOYED | MCP `mas-observe.mas_kanban_health`；实跑报告 10:18（`KANBAN_HEALTH=…`） | `--line` |
| 16 | 回滚演练 rollback_drill | DEPLOYED | cron `55337a0a2b62`（每周一 09:00；11:09 手动 ok） | `ROLLBACK_DRILL_AUTO=` |
| 17 | 班次日报 shift_report | DEPLOYED | cron `8b1708397046`（08:30/21:30/23:30；09:56 ok） | `--shift daily` |
| 18 | 调度看门狗 scheduler_watchdog | PROPOSED | CLI+演练 PASS（`WATCHDOG=OK`；15 步假环境演练）；**计划任务注册"待 lead 批准"** ⇒ 还不是活的安全网 | `--check` / `--install-task`（未批准勿跑） |
| 19 | 知识库巡检行 WIKI_GOV | PROPOSED | 卡 `t_c8b36f5e`（blocked）；巡检行待接线 | `mas_watch`/巡检报告观察 |
| 20 | maa-cli 第二驱动 | DEPLOYED（主机） | 三级验证全过：L3 真跑 idx19 `RC=0`（2026-09-27 23:03–23:53，`MAA_CLI_DEPLOY.md` §4.3）；**受限用途**；并发日志隔离 **UNVERIFIED** | 使用前重查 `maa version`/`maa dir config` |
| 21 | MaaFw 取证三件套 | TESTED | 18 ms/帧、`PostMessage` 输入隔离（`MAA_DRIVER_OPTIONS.md` §1.3） | `maafw_capture.py` 自测 |
| 22 | 肉鸽机队 `l-*`（fz-maa 看守） | UNVERIFIED | 文档口径=既在看守（手册/驱动取舍 §2.2）；**本卡未复验主机现状** | 真值源：主机计划任务 `MAAOrch-RogueFleet` + 看守日志 |
| 23 | 共享知识库 MAS-KB | DEPLOYED | `D:\1-2\MAS-KB` + 技能 `wiki-search`（先查库再动手） | `wiki_search.py` |
| 24 | in-repo 栈图景（POSITIONING/STRUCTURE 的 UI 章节除外） | UNVERIFIED | 本卡只读文档、未复核代码现状；部分已被 `retire(frontend)` 推翻 | 真值源：`git log` + 代码 + `docs/http-api.md` |
| 25 | in-repo 前端 / Node 看板图景 | DEPRECATED | 2026-09-27 `retire(frontend)`（提交 `9f9fab9`，49 条白名单路由） | 以 Hermes 会话/看板卡为入口 |
| 26 | `REF_MAA_CLI_SKILL.md` 的环境搭建路径 | DEPRECATED | 2026-09-20 本机实验稿，其安装/junction 做法已被 `MAA_CLI_DEPLOY.md`（主机主部署位）取代；探测器结论（客户端类型表征等）仍有效 | 用 `MAA_CLI_DEPLOY.md` + 技能 `maa-cli-ops` |

**计数（本表 26 行）**：`DEPLOYED` 16 ｜ `TESTED` 2 ｜ `IMPLEMENTED` 2 ｜ `PROPOSED` 2 ｜ `UNVERIFIED` 2 ｜ `DEPRECATED` 2。

### 4.3 状态迁移示例（本仓库真实发生过的）

- `PROPOSED → DEPLOYED`：mas-observe MCP（`MCP_TOOL_ROADMAP.md` §2 建议 5 工具 → §12.1 收口 **9 工具**，09-28 11:16）；mas-ondemand（§10，工具面 27→33）。
- `PROPOSED → TESTED`：scenario_dispatch —— 自测 44/44 + 干跑 GO，但 **apply 仍 UNVERIFIED**（未满足 DEPLOYED 三条件）。
- `DEPLOYED → 需复验`：maa-cli —— L3 已跑通，但**版本/部署会漂**，任何使用前必须重查（技能 `maa-cli-ops` 的版本差异纪律）。
- `× → DEPRECATED`：前端/Node 图景（`retire(frontend)`）。

### 4.4 「设计 ≠ 可用」清单（本页已确认的例子）

1. **scheduler_watchdog**：实现齐、演练 `DRILL=PASS`，但计划任务**未注册**（待批准）⇒ 当前没有任何自动看门狗在跑。
2. **scenario_dispatch**：全靠闸门与自测背书，**真发车（apply）从未实跑**；启用前按 `MAS_SCENARIO_DISPATCH.md` §8 清单走批准。
3. **activity_switch**：干跑报告看似完整，但**无 state 账本**=从未 live apply；"能用"待首次受控实跑证明。
4. **maa-cli**：部署与三级验证为真，但用途边界是**受限**的（无记账/无 gui.log 判据/无切号语义），并发日志隔离未验证；**不要把"跑通过一次"说成"可用于生产批跑"**。
5. **skills-draft/mas-playbooks**：技能草稿**不生效**（不在 profile skills 目录；`docs/skills-draft/README.md`），评审通过前任何会话都不会加载它。

---

## 5. 证据新鲜度与复验规则

**版本锚点（本页建立时）**：MAA **v6.18.0** ｜ AUTO-MAS **v5.4.0** ｜ 游戏客户端 **2.7.71** ｜ maa-cli **0.7.5** + MaaCore 6.18.0 ｜ 肉鸽机队 MAA **6.17.5**。来源：`MAS_MASTER_HANDBOOK.md` §0、`MAA_CLI_DEPLOY.md` §2、`MAA_DRIVER_OPTIONS.md` §2.2。

**新鲜度分级**（引用任何结论前自问）：

| 事实类型 | 保鲜期 | 复验方式 |
|---|---|---|
| 端口 / 实例映射 / 谁在跑 | **0 秒**（现查） | `mas_mapping.py --port N` / `--lookup`；`mas_ondemand.py --list-running` |
| 队列 / 账号 / 班次清单 | 当天 | `mas_accounts.py --dump`；REST `/api/queue|scripts` |
| 发车/巡检运行证据 | 当日有效 | cron `hermes -p mas cron list`；`runtime/mas_dispatch_state.json` |
| 版本类事实 | 每次使用前 | `maa version` / MAA/GUI 版本读回 |
| 能力状态（§4 表） | **随卡更新**：任何任务改动了能力面就要回写 | 本页 §9 流程 |
| 文档声称（旧审计/手册） | 看头部日期与版本锚点；与现场冲突即弃 | 现场优先，且回写文档 |

**会话默认动作**：不背旧结论——先 `git log --oneline -10` 看有没有推翻旧结论的提交；再查本页 §4 表里目标能力的"复验方式"一栏。

---

## 6. 失败 / UNKNOWN 处理（fail-closed 纪律）

1. **fail-closed 是全局口径**：读不到 / 判不了 ⇒ 输出 `UNKNOWN:<原因>`，**绝不**被解读为"正常/GO"（巡检主表、preflight、闸门全按此）。
2. **末行判据**（机检用）：`PREFLIGHT=GO|NO-GO`、`SWITCH_GATE=`、`PATCH_GUARD=`、`ERR_SUMMARY=`、`AUDIT_STATUS=`、`KANBAN_HEALTH=`、`CLIENT_STATE=`、`UPCOMING=`（不可用写 `UNKNOWN:<原因>`）。
3. **退出码语义**：发车/工具普遍 `0=成功 / 2=拒绝（零写）`，部分 `3=部分完成 / 4=有残留需人工 / 5=试探过但执行失败`（逐工具见 TOOLBOX 各节）。
4. **失败不自动重试**：单链失败走 C3 三问（会好吗 / 已成功吗 / 超上限吗）；资源型不补；超窗只记 late；**失败照实报告，不连环重试**。
5. **止损**：走官方 `dispatch/stop`（要有 `task_uid`），**绝不 kill MAA**；紧急全局先 `MAAORCH_DISPATCH_NO_GO=1` 止血再查。
6. **升级链**：看板卡 2 连败 → 自动止损（gave_up）；同因 ≥2 卡 ⇒ 环境专项（P3）；任何重派**必须先 comment 写重试备注**（禁止裸 promote）。

---

## 7. 风险与过期内容判定

### 7.1 已知风险（活跃）

| 风险 | 说明 | 处置 |
|---|---|---|
| **G2 系统代理拦内网（P0，未修）** | 62 个 urllib 工具仅 1 个显式绕代理；间歇 502/10054 ⇒ 误 NO-GO、补跑通道失败 | 现状=手动路径+重试兜底；修复落点见 `MAS_SHIFT_GAPS.md` G2（改前先拍板） |
| `MAA_10/MAA_12` 切号闸门 VIOLATION | 轮末 Temp 还原残留 `masked+false`，永久复现 | 一次性清理方案已出**未执行**（as-of 09-28 01:34） |
| 幽灵绑定 idx51（官-28） | 启用但未排班 + 实例索引指向已删 VM | 三件一起拍板（重建 ≈+30GB 或停用） |
| Temp 快照轮末整份还原 | 「勾选/设置消失」真凶；还原值 ≠ 最后跑的号 | 改配置走 run_profile/activity_switch 骨架，别手动绕 |
| 巡检/日报 `deliver=local` | 结论落盘但**没有推送通道** | 需要推送就改 deliver 到消息平台（产品决策） |
| 劣化 SSH 通道 | 大日志全量拉取不可用（实测 <12KB/s）；`triage` 读忙 `asst.log` 会超时 | 先轻读聚合；remote 预过滤/压缩增量（G5） |
| MCP 面改后不刷新 | 工具增删要「关→开」enabled 或重启网关才进巡检面 | 见 §附录 B 口径 |

### 7.2 过期内容判定规则（怎么识别"文档在撒谎"）

1. **版本锚点失效**：文档声称基于的版本 < 当前生产版本（MAA/AUTO-MAS/客户端）⇒ 先复核再用。
2. **被提交推翻**：`git log` 里有 `retire/remove/replace` 类提交命中该主题（例：`retire(frontend)` 推翻 UI 图景）。
3. **与现场冲突**：现场（history/gui.log/进程/端口）与文档冲突 ⇒ 以现场为准，并把修正回写（§9）。
4. **数字漂移**：工具数/行数/队列数类数字（如"28 队列"=旧快照，实测 33；TOOLBOX §C 表列 mas-dispatch 4 工具，实际 5 含 patch_guard）——数字一律以现查为准。
5. **"草案特性"混入结论**：`PROPOSED` 内容不得当 `DEPLOYED` 引用（§4.4 五例）。

**已判定 DEPRECATED**：§4.2 第 25/26 行。**待复核（UNVERIFIED）**：§4.2 第 22/24 行。

---

## 8. Kanban 工作闭环（本板）

- **板**：`maa-parallel-benchmark-20260926`；worker 用 `kanban_*` 工具（不用 CLI 改卡）。查重/列卡若走 CLI 需 `env -u HERMES_DELEGATED_CHILD_CONTEXT` 前缀。
- **建卡**：七段式模板（`KANBAN_TASK_TEMPLATE.md`）；必带 `assignee / priority / parents / idempotency_key(周期卡) / 产出绝对路径 / 验收 / 红线 / 预算`。建卡前**查重三步**（`KANBAN_DISPATCH_POLICY.md` §6.2）。
- **并发与资源**：稳态 4–6 张/波（宿主上限 8、同 profile 6、板 6）；碰 adb/实例、重启侧车、发车、写生产配置 = **全局串行**且避开班次窗；只读卡可并行但要**互不写同一文件**。
- **依赖**：产物依赖才挂 `parents`（全 done 才解锁）；仅共享资源用时间窗/租约，别挂 parent。
- **失败处理**：`failure_limit=2` 自动止损；重派前读 `last_failure_error` + 补 comment（§6.6）；有副作用的卡 `max_retries=1`。
- **完结**：`kanban_complete(artifacts=[绝对路径])`——**产物必须落在 durable 路径**（`docs/`、`runtime/reports/`），scratch 完结即删会被判孤儿。
- **体检**：`kanban_health.py`（或 MCP `mas_kanban_health`）→ `KANBAN_HEALTH=`；治理闭环=发现→comment/修卡→promote，禁止裸 promote。
- **review 车道**：实现完 `kanban_request_review` → reviewer 通过=complete / 打回=request_changes；**给 mas 的卡别用自审**（需约定独立 reviewer 或人工审）。

---

## 9. 自我学习闭环（新知识怎么沉淀回来）

1. **任务产出分两处落**：证据/过程 → `docs/audits/<date>_<slug>.*`；结论/能力状态 → **回写本页 §4 表对应行**（改标签必须附新证据：日期+机器+命令/执行记录）。
2. **状态迁移即知识**：出现 `PROPOSED→DEPLOYED`、`DEPLOYED→DEPRECATED` 等迁移，在 §4.3 追加一行示例（一行即可，不写长文）。
3. **数字与口径的修正**：发现文档与现场不符（§7.2 规则），先修**最权威的那份**（工具文档/审计），再在本页留下"已修正"指针；不复制全文。
4. **工具新增/变更**：按 TOOLBOX §E 查重（先扩展、不重写）；落成后更新 `docs/TOOLBOX.md` + 工具文档 + 本页 §4 表 +（如涉及）巡检/日报接线。
5. **技能层面**：跨会话可复用的流程沉淀到技能（mas profile 已装：`mas-ops-toolbox`、`auto-mas-host-ops`、`maaorch-queue-triage`、`maa-task-config`、`maa-roguelike-ops`、`maa-event-farming`、`maa-cli-ops`、`wiki-search`）；**凭据/账号隐私不入技能**。
6. **本页维护规则**：最小 diff；**不覆盖他人改动**；每条新事实带来源+日期；超过约 350 行就先"压缩跳线"再拆分子页。

---

## 10. 后续学习 / 待决清单（backlog 建议 —— 交 lead/用户拍板，本卡不自动执行）

| P | 事项 | 依据 |
|---|---|---|
| P0 | G2 绕代理统一修复（关键链路五件套 + MCP adapter） | `MAS_SHIFT_GAPS.md` G2 |
| P1 | `MAA_10/MAA_12` 闸门一次性清理落地；官-28/idx51 三件套拍板；9 条临时队列清理 | 手册 §6.1 |
| P1 | scheduler_watchdog 计划任务注册（获批后）→ 补一条"看门狗活体"巡检证据 | TOOLBOX B19 |
| P1 | scenario_dispatch 首次受控 apply 演练（单号 + 窗口 + 批准链） | `MAS_SCENARIO_DISPATCH.md` §8 |
| P2 | activity_switch 首次 live apply 演练（先 pretend 报告评审） | `MAS_ACTIVITY_SWITCH.md` |
| P2 | maa-cli 并发小样（2 进程同 data 目录 asst.log 隔离验证） | `MAA_CLI_DEPLOY.md` §8.1 |
| P2 | `skills-draft/mas-playbooks` 评审（安装 / 并入 host-ops 二选一） | `docs/skills-draft/README.md` |
| P2 | POSITIONING/STRUCTURE 与代码现状对齐（前端退役后的漂移收尾） | §1.1 / §7.2 |
| P3 | WIKI_GOV 巡检行接线（`t_c8b36f5e` blocked 解锁） | TOOLBOX D/巡检表 |

---

## 附录 A：本页抽查来源（本次实读 18 份；路径存在性核验 40 条全命中，2026-09-28 11:31 本卡实测）

| 来源 | 抽查方式 | 用于 |
|---|---|---|
| `docs/TOOLBOX.md` | 全文 444 行 | §2 路由、§6 判据、急停开关 |
| `docs/MAS_MASTER_HANDBOOK.md` | 全文 | §1 角色/节律、§3 事实源、§7 风险 |
| `docs/KANBAN_TASK_TEMPLATE.md` / `docs/KANBAN_DISPATCH_POLICY.md` | 全文 | §8 看板闭环 |
| `docs/KANBAN_HEALTH.md` | 全文 | §8 体检、结果快照 |
| `docs/MAA_CLI_DEPLOY.md` | 全文 | §4.2-20 / §4.4-4 / §10 |
| `docs/MAS_SCENARIO_DISPATCH.md` | 全文 | §4.2-10 / §4.4-2 |
| `docs/DOC_IMPL_CONSISTENCY.md` | 全文 | §5 复验方法、数字漂移例证 |
| `docs/MAA_DRIVER_OPTIONS.md` | 前 160 行 | §2 选路（P1/P2/P3）、§1.3 |
| `docs/MCP_TOOL_ROADMAP.md` | 前 150 + 尾 88 行 | §4.3 迁移示例、附录 B 工具面 |
| `docs/MAS_SHIFT_GAPS.md` | 全文 | §7.1 G 系列风险 |
| `docs/HERMES_MAS_HANDOVER.md` | 全文 | §3 事实源、§6/§12 边界与接线 |
| `docs/MAS_ACTIVITY_SWITCH.md` | 前 70 行 | §4.2-11 |
| `docs/POSITIONING.md` / `docs/STRUCTURE.md` | 全文 | §1.1 边界与漂移 |
| `docs/REF_MAA_CLI_SKILL.md` | 全文 | §4.2-26 DEPRECATED 判定 |
| `docs/skills-draft/README.md` + `mas-playbooks/SKILL.md` | 全文 | §4.4-5；技能体例参照 |
| 另：40 条路径逐条 `test -f/-e`（工具、审计、runtime 账本、playbook 文档） | 清单见 git 记录/本卡 summary | §3/§4 每行的"来源"列 |

## 附录 B：实时快照（2026-09-28 11:31，本卡实测）

- **cron（mas profile，4 个 active）**：`52b88b0ffca0` 巡检 every 30m（last ok 11:10）｜`2600f6415f16` 发车对账 `*/3 5,19,20`（last ok 05:57）｜`8b1708397046` 班次日报 `30 8,21,23`（last ok 09:56）｜`55337a0a2b62` 回滚演练 `0 9 * * 1`（last ok 11:09）。
- **MCP（6 个生产 server，契约计数 38）**：`auto-mas-monitor` 10 只读（硬闸）｜`maaorch-daily` 6（5 只读+1 写默认预演）｜`mas-dispatch` 5（3 只读+2 写默认预演；含 `patch_guard`）｜`mas-triage` 2 只读｜`mas-observe` 9 全只读｜`mas-ondemand` 6（1 只读+5 写默认预演）。核验：`hermes -p mas mcp list`（本卡 11:31）；工具面刷新=「关→开」enabled。
- **看板**：板 `maa-parallel-benchmark-20260926`；11:16 快照 running 5 张（本卡 + 4 张并行接线/修复卡）。
- **git**：HEAD `62feab6`（feat(farm)：端到端刷关编排器 + 首跑真演练 rc=0）；本卡只新增 2 个文件（`docs/HERMES_PROJECT_BRAIN.md`、`.agents/skills/maaorch-project-brain/SKILL.md`）。
- **主机侧**：本卡未连接主机（只读约束）；主机相关结论一律标注来源日期（见 §4/§5）。
