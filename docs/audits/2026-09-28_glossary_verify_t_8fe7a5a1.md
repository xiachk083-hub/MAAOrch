# GLOSSARY 复核记录 — 卡 t_8fe7a5a1（第 2 轮 / run 176）

> 2026-09-28。第 1 轮（run 149）产出 `docs/GLOSSARY.md` 后于数值复核中途崩溃；本轮**接续复核**：逐项对照来源、修 6 处口径、补 2 个悬空词条，并把条目数压回 ≤150。全程只读（SSH 仅读 E:\AUTO-MAS\history 日志），**未发车、未改任何配置**。

## 1. 条目数账（目标 ≤150）

| 步骤 | 变化 | 累计 |
|---|---|---|
| 第 1 轮初稿 | — | 149 |
| 外部（卡 t_eb81df77『主机健康巡检』，09:52 done）加入 `host_health.py` 行 | +1 | 150 |
| 本轮新增卡面点名术语：`理智溢出`、`串号` | +2 | 152 |
| 本轮合并 L 组三行（mas_preflight / mas_switch_gate / 检查类 → 「闸门 / 检查类」一行） | −2 | **150** |

机器校验（`^| ` 行数 − 14 个表头 = 条目数）：**TOTAL=150，0 个畸形行**（每行 6 个未转义竖线）。

## 2. 数值核验（卡面点名项 + 抽查）

| 项 | 结论 | 证据 |
|---|---|---|
| 理智上限 | **按号不同，实测 168–210，常见 205/210**（原稿「约 205」不完整） | SSH 扫主机 `E:\AUTO-MAS\history\{2026-09-27,2026-09-28}` 250 个 `.log`，正则 `理智[:：]\s*(\d+)/(\d+)` 共 **954 条**：210×500、205×158、174×83、203×39、202×28、201×23、208×20、175×16、172×14、180×13、173×11、168×11、204×10、169×9、198×8、199×6、206×5。`tools/account_health.py` 基线记「约 205」亦保留 |
| AP 预算 280/天 | ✓ | `docs/DEPOT_PLANNING.md:39`（自然 240 + 周任务分摊；月卡 +80） |
| 临期药 48h | ✓ | `docs/MAA_TASK_CONFIG_REFERENCE.md:306`（`ExpiringMedicineDays=2`）；L126 活动临期药 48H |
| 库存保持预设 | ✓ CE-6 200万 / AP-5 5000 / CA-5 200 / 芯片 20 | `docs/MAA_DEPOT_MAINTAIN.md:52`（`DepotMaintainVM.cs:258-284`） |
| 源石折 AP 135 | ✓（原稿「135 源石 ≈ 1 颗当 AP」倒装，已改「1 颗源石 ≈ 135 AP」） | `docs/DEPOT_PLANNING.md:40`「至纯源石 135/颗」 |
| 班次表 | ✓ 05:00..05:33 / 19:30..20:03，各 12 槽 +3min | `tools/mas_dispatch.py:173-180` SHIFT_TABLE |
| 守卫窗 / 让位区 | ✓ 05:00–05:40 / 19:20–20:20；04:00–05:33 / 18:30–20:30 | MAS_RERUN_POLICY §3；`tools/lib/rogue_plan.mjs:21,50,70` |
| MCP 工具面 | **33**（原稿 27 已过期） | `docs/MCP_TOOL_ROADMAP.md` §10「27 → 33」：monitor10/daily6/dispatch4/observe5/triage2/ondemand6 |
| 红票节点 | ✓ `RedTicket@Store@ChooseTicketType` text=`["采购凭证区"]` | `D:\1-2\_maa_src\resource\tasks\tasks.json`（6.18 线）；绿/黄票同表；黄票可买 `Headhunting_Permit` 亦实 |
| 对账 cron | ✓ `*/3 5,19,20`（原稿「每 3 分钟」未写时窗） | `docs/MAS_SHIFT_PLAYBOOK.md:22` |
| 巡检 11 行 | ✓ 标签序与手册逐字一致 | `docs/MAS_MASTER_HANDBOOK.md:151` |
| 租约 TTL 30min | ✓ `DEFAULT_TTL_S=1800` | `tools/mas_instance_lease.py:88` |
| 其他抽查 | 60=58+2 号、54=49+5 脚本、55 实例、12 槽、24 定时全停、04:01/19:01 自重启、四药链独立、保底 50 抽 2%→4%(+2%/抽)、井 300、pendingStart 6min、S1–S6、P0–P3/N1–N4/Y1–Y3、C3 三问、L1–L3 → 全部与来源一致 | 各来源文档（见词条出处列） |

## 3. 引用解析

`docs/GLOSSARY.md` 出处列全部短名（MAS_MASTER_HANDBOOK / MAS_SHIFT_PLAYBOOK / RERUN_POLICY / INTERNALS / EMULATORS / SCHEDULE_MAP / ERUNBOOK / ONODEMAND / PROBE_FARM / PLAN_BUILDER / RUN_PROFILE(_TOOL) / SCENARIO_DISPATCH / MAA_* 系列 / GAME_CLIENT_MECHANICS / DEPOT_PLANNING / JOB_SYSTEM / FLEET / ROGUE_WATCH / SERVER_EVENTS / ACCOUNT_* / MASTERY_LEDGER / ROSTER_SKILLS_6star / MCP_TOOL_ROADMAP + tools/*.py + runtime/*）逐一对到落盘文件；`services/maa/source`（仓内）与 `D:\1-2\_maa_src`（仓外 6.18 源）双源核对；`runtime/` 台账文件（ledger / leases / dispatch 三件套 / stage_capability / 名册）全部实际存在。

## 4. 本轮改动清单（docs/GLOSSARY.md）

1. 理智：`上限约 205` → `上限按号不同（实测 168–210，常见 205/210）`；出处补实测来源。
2. 新增 `理智溢出`（含 `--waste-hours` 26h 阈值、lost_points×10 口径）；新增 `串号`（帧证＞切换行、S5、名册指纹终极判据 `tools/mas_account_identity_check.py`）——两词原在表头「用途」列点名但**无条目**，且 L 组有悬空引用「同『三态 / 理智溢出』条目」。
3. 至纯源石折算改写；红/黄票源表路径显式化为 `D:\1-2\_maa_src\...`。
4. 对账行补 cron 时窗；MCP 27→33（含 ondemand6）。
5. L 组三行合并为「闸门 / 检查类（mas_preflight / mas_switch_gate / mas_identity_trace / client_state_check）」；N 组「45+ 号内网池」改为与 `overview.md` 一致的「45+ 账号 × 模拟器池 × MAA 实例池」。
6. 表头计数 149→150；footer 增「数值复核」行。

## 5. 备注

- **热点提醒**：`docs/GLOSSARY.md` 在并行班（同板 20+ mas 卡共用一个工作区）被其它卡追加过内容（t_eb81df77 加了 `host_health.py` 行）。后续再加词条请先复核条目数，**总量红线 ≤150**，超了先合并 L 组同类行。
- 复核用的临时脚本在 Hermes scratch（本机，非交付物）；SSH 采样命令已存档于本记录 §2。
