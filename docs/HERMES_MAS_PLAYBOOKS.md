# Hermes 调配手册（场景化操作剧本）

> 卡 `t_4d8f3ee3`（看板 `maa-parallel-benchmark-20260926`，assignee `mas`）｜落盘：2026-09-28 凌晨
> **性质：只读剧本集。** 本卡**未执行任何剧本的写动作**——未发车、未补跑、未改配置、未动队列；仅跑了 3 个本机只读检查（见 §8.1）。
> **目标**：让「一句话调配」可落地——用户说一句 → 命中一个剧本 → 照剧本走（先只读、再受控写、留痕、可回滚）。
> **衔接**：t_0f598a66 的沉淀去向是 mas profile 技能 `auto-mas-host-ops`（v1.3.0）+ 姊妹技能 `maa-roguelike-ops` / `maa-event-farming` / `maa-task-config` / `maaorch-queue-triage`；本手册的**技能草稿**在 `docs/skills-draft/mas-playbooks/`（未安装，安装与评审见该目录 README）。
>
> 上游证据链（写剧本时逐条引用）：
> `HERMES_MAS_HANDOVER.md`（接管契约/发车补发边界）｜`MAS_SCHEDULE_MAP.md`（队列/槽位全图）｜`MAS_SHIFT_PLAYBOOK.md` + `MAS_SHIFT_GAPS.md`（班次链路与缺口 G1–G12）｜`MAS_RERUN_POLICY.md`（补跑三问 C3）｜`MAS_EMERGENCY_RUNBOOK.md`（急停/回滚）｜`MAA_ROGUELIKE_PLAYBOOK.md`｜`MAA_EVENT_FARMING_PLAYBOOK.md`｜`MAA_TASK_CONFIG_REFERENCE.md`｜`MAS_SCENARIO_DISPATCH.md`（场景发车草案）｜`MAS_INTERNALS.md`（AUTO-MAS 内部机制）。

---

## 0. 手册怎么用

1. **一句话进场**：用户口述（如「补一下官-5」「把某号改刷 CE-6」「发一局肉鸽」）→ 在 §1–§6 找到对应剧本（§0.5 有路由表）。
2. **每个剧本六段式**：触发语 → 先确认什么 → 命令（含 dry-run 路径）→ 验证判据 → 回滚 → 红线。**顺序不能跳**：确认不全不动手；dry-run 不过不发车。
3. **默认只读**：所有判定先走只读工具；任何写（发车/补跑/改配置/停任务）都必须显式、留痕、可回滚；**生产写先拿用户批准**。
4. **执行坐标**：交互式 Hermes 会话（mas profile；工具面 = MCP 工具 + 本机终端）；被控系统 = AUTO-MAS 主机 `100.79.173.69`（别名 mrfz-0000，`E:\AUTO-MAS`）、肉鸽机队（`E:\maa-alas\fz-maa\l\l-*`）、本机 MAAOrch（`D:\1-2\MAAOrch`）。
5. **口径落款**：本手册是**口径唯一入口**；与单卡文档冲突时，冲突点显式标注「以 X 为准」。未实测的路径全部标 **（草案/未实跑）**，不得当既成能力引用。

### 0.1 术语（调配语言对齐）

| 词 | 含义 | 唯一性 |
|---|---|---|
| 脚本（script） | AUTO-MAS 里一台「机」的配置 = 一台模拟器实例 + N 个号；**队列项就是脚本** | 54 个脚本 ↔ 54 个模拟器实例 1:1（§附录 A） |
| 用户/号（user） | 脚本里的一个账号（含服别与掩码） | 多号脚本逐用户**串行**跑 |
| 队列（queue） | 班次队列（日常定时1-12 / 晚班定时1-12）或临时队列（`临时-补跑-*`） | 24 条班次队列；临时队列跑完即弃 |
| 任务（task） | 一次发车产生的执行体；`task_uid` = `dispatch/start` 返回的 id | **stop 只认 task_uid** |
| 槽位（slot） | MAA 目录 + 模拟器实例（如 `E:\MAA-Multi\MAA_6` + emu13） | 同目录**同一时刻只允许一个任务**（互杀按目录） |
| 两个补跑面 | **队列级**（mas_dispatch：发车/补发/late）与**账号级**（mas_rerun：补失败号） | 互不替代、互不抢（HERMES_MAS_HANDOVER §13） |

### 0.2 五个时间窗（最常混淆，按名字引用）

| 名称 | 时段 | 约束 | 出处 |
|---|---|---|---|
| 守卫窗 | **05:00–05:40 / 19:20–20:20** | 账号级补跑、发车**一律回避** | MAS_RERUN_POLICY §1（最严口径） |
| 班次发车点 | 05:00–05:33 / 19:30–20:03（+3 分钟错峰） | Hermes cron 发车时刻（SHIFT_TABLE） | tools/mas_dispatch.py |
| 肉鸽让位区 | **04:00–05:33 / 18:30–20:30** | 肉鸽**不发车**；产线一回来即**收工让路** | tools/lib/rogue_plan.mjs `inYieldZone` |
| 侧车重启禁窗 | 05:00–08:00 / 19:30–23:00 | 禁止重启侧车/实例（吞在飞任务⇒孤儿化） | auto-mas-host-ops「改动纪律」 |
| 看板资源类避让窗 | 05:00–05:40 / 19:30–20:30 | 看板资源类任务避让（编排用） | KANBAN_DISPATCH_POLICY §2.4 |

其他节律：AUTO-MAS 每天 **04:01 / 19:01** 惯性重启（会吞在飞任务）；主机 `DailyRestart0400/1900`（04:00/19:00 重启整机）。维护窗口 = 收班后至 04:00 与两次重启前后。

### 0.3 工具箱（剧本里用到的全部入口）

**MCP 工具**（mas profile 会话面，按 server 分组；数量为设计值，改配置后需重启网关才刷新，HERMES_MAS_HANDOVER §12.1）：

| server | 工具 | 用途 |
|---|---|---|
| maaorch-daily | `mas_status` / `mas_snapshot` / `account_status` / `queue` / `mas_rerun_state` | 只读：产线总览/快照/账号状态/队列/补跑进度 |
| maaorch-daily | `mas_rerun`（`dry_run=True` 缺省） | **写**：账号级补跑（≤8/批，单飞） |
| mas-dispatch | `list_schedule` / `switch_gate` | 只读：24 条班次表对账 / 切号注入闸门 |
| mas-dispatch | `dispatch_due`（dry 缺省）/ `dispatch_queue`（dry 缺省、`force` 才绕当日已发） | **写**：队列级发车 |
| mas-triage | `triage_error` / `error_daily` | 只读：单号定因 / 每日错误聚合一行 |
| auto-mas-monitor | 10 个只读：`get_health_*` / `get_overview_*` / `get_git_version_*` / `get_queues_*`（MCP schema 限制，实际不可用）/ `get_item_*` / `get_time_set_*` / `get_script_*` / `get_user_*` / `search_history_*` / `get_history_data_*` | 只读：AUTO-MAS 官方面（`tools/mas_scrub_proxy.py` 脱敏硬闸） |

以上数量均为配置/源码口径**设计值**；落地以 `hermes -p mas mcp test <server>` 复核（MCP_TOOL_ROADMAP §0）。

**CLI 工具**（本机 `D:\1-2\MAAOrch`；解释器统一 `PY312=C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe`）：

| 工具 | 用途 | 默认 |
|---|---|---|
| `tools/mas_dispatch.py` | 班次发车/对账/一致性/busy/late（`--list/--check/--check-schedule/--busy/--reconcile/--queue/--release/--dismiss-late`） | dry-run |
| `tools/auto_mas_rerun.py` | 临时队列补跑（`--accounts/--apply/--skip-dead/--cleanup`） | dry-run |
| `tools/auto_mas_toggle_user.py` | 账号启用/停用（`--name/--enable/--dry-run/--wait-min`，锁感知） | 停用；`--dry-run` 可预演 |
| `tools/mas_stage_fix.py` | 关卡字段受控写**模具**（`--dry-run/--apply/--verify/--rollback/--only`） | dry-run |
| `tools/mas_scenario_dispatch.py` | 按场景发车：肉鸽/活动/剿灭（`--script/--instance/--scenario/--times/--window/--set/--apply/--reconcile/--status`） | dry-run；真发车双闸 |
| `tools/mas_preflight.py` | 班前 GO/NO-GO（`--queue/--script --skip-screen --json`） | 只读 |
| `tools/mas_switch_gate.py` | 切号注入闸门（`--check/--json`） | 只读 |
| `tools/client_state_check.py` | 客户端状态（`--serial 127.0.0.1:<口>`；`CLIENT_STATE=…`） | 只读 |
| `tools/mas_identity_trace.py` | 身份留痕（`report/watch/selftest`） | 只读 |
| `tools/maa_error_daily.py` | 四源错误聚合（`--summary --no-write`） | 只读 |
| `tools/mas_watch.py` | 收班日报 + 自动补跑（≤1 次/号） | 有写：仅补跑 |
| `tools/mas_timers.py` | AUTO-MAS 24 条定时开关（回滚用：`--enable/--disable/--list`） | 写 |
| `tools/rogue_fleet_watch.mjs` 等 | 肉鸽机队看守（`--status/--apply/--force/--drill-wrapup/--clear-human`） | 只读（`--status`） |

**急停/闸门环境变量**：`MAAORCH_DISPATCH_NO_GO=1`（拒一切真发车）｜`MAAORCH_DISPATCH_HOLD_MIN`（busy 窗，默认 150）｜`MAAORCH_DISPATCH_GRACE`（追发窗口，默认 30min）｜`MAAORCH_SCENARIO_APPLY=1` + `--apply`（场景发车双闸）｜`MAAORCH_SCENARIO_NO_GO=1`（一票否决）。

**本机网络坑（G2，2026-09-27 定案）**：系统代理 `127.0.0.1:7890` 会间歇 502 拦截内网 `100.79.x`（默认 urllib 实测 2/6 失败、绕代理 6/6 全 200；共 62 个工具用 urlopen、当时仅 `mas_stage_fix.py` 显式绕过）。**手工执行优先 `curl`（不读系统代理，实测直连 200）**；Python 工具遇到 502/超时先想这个根因，重试可自愈但会白耗一跳。

### 0.4 通用红线（每个剧本默认继承）

1. **同一 MAA 目录同一时刻只允许一个任务**——换号/重试/中止按目录路径杀进程，同目录并发 = 互杀。
2. **同一模拟器绝不允许两个 MAA**（产线 × 肉鸽共用模拟器：l-1↔emu25/MAA_1、l-2↔28/MAA_2、l-5↔34/MAA_9、l-4↔31/MAA_10、l-7↔52/MAA_12）。
3. **班次窗口内不重启侧车/实例**（05:00–08:00 / 19:30–23:00）；重启 = 吞重试/收尾 ⇒ 孤儿化。
4. **止损用官方 stop，不 kill MAA**——只有 `dispatch/stop` 会 unlock + `Temp→config` 还原；**不知道 task_uid 时不要乱停**。
5. **生产写先批准**：改配置/停用号/发车全部「备份 → 改 → 复读校验 → 留痕」；不确定的字段不写。
6. **凭据不进任何交付物**：密码/Token 读入即弃；报告只写掩码。
7. **补跑 ≠ 补发**：队列级只归 `mas_dispatch`，账号级只归 `mas_rerun`；两边都不替对方做决定。
8. **时间与事实以机器证据为准**（日志行/mtime/进程命令行）；时间口径一律机器时间。
9. **fail-closed**：任何工具返回 `UNKNOWN`/读不到 ⇒ 视为不通过，绝不当「正常/GO」。
10. **肉鸽红线（对一切剧本）**：让位区不发、产线在跑不动那台、`l-*` 机队与用户手动在用的实例本轮不许碰。

### 0.5 路由表（触发语 → 剧本）

| 用户一句话（样例） | 剧本 | 一句话动作 |
|---|---|---|
| 「补一下官-5 / 昨晚漏的号补一下 / 这个失败号重跑」 | **①** | 账号级补跑（mas_rerun，预演→发车→复核） |
| 「把某号改刷 CE-6 / 打 20 次 / 关卡改回 1-7」 | **②** | 单号关卡/次数受控写（下轮生效） |
| 「发一局肉鸽 / 让 l-2 刷一局 / 给某号来一局」 | **③** | 肉鸽一局（机队直发 或 AUTO-MAS 场景发车） |
| 「活动开了，插一轮活动关 / 活动关优先 / 临期药投活动」 | **④** | 活动关编排（计划表 / 活动关优先 / 额外队列） |
| 「把晚班定时5 停了 / 别让某号再跑了 / 队列卡住停掉」 | **⑤** | 停任务/停号/急停（分诊→止损→恢复） |
| 「把某台实例空出来给肉鸽线 / 腾一台给肉鸽」 | **⑥** | 实例腾挪（让产线释放 → 机队接管） |

---

## 1. 剧本①：临时补跑某号

**触发语**：「补一下 <号>」「昨晚漏的号补一补」「这个失败号重跑一次」「清个尾巴」。

**执行坐标**：交互式 Hermes 会话（MCP `mas_rerun` / `mas_rerun_state` / `mas_status` / `triage_error`）或本机终端（`tools/auto_mas_rerun.py`）。

**先确认（缺一不动手）**：

| # | 确认项 | 判据/工具 |
|---|---|---|
| 1 | 目标号全称（含服别）与所属脚本/目录/实例 | `mas_status` / `--list-scripts` 表；号名不确定先对名册 |
| 2 | **该不该补**（三问 C3） | 照 `MAS_RERUN_POLICY.md` §2：①重跑会好吗（资源类不补）②本班次已成功吗（不补）③超上限吗（≥2 不补）。定因先跑 `triage_error`；**目录级串号警告**——triage 取的是同目录末条链，可能属别的号（hbznn119-日 实例），按「本号最近一次运行时间窗 + 归属」收敛证据 |
| 3 | 时间合法 | 在守卫窗外（05:00–05:40 / 19:20–20:20 不发）；晚班截断补跑不晚于次日 04:00（04:01 自重启会再吞） |
| 4 | 目标目录空闲 | 工具自带 `busy_paths` 体检（目录被占用 ⇒ 跳过），不要只信 MuMuManager/ADB 状态面 |
| 5 | 批量与上限 | 单批 **≤8** 号；同目录的号**必须放同一条**临时队列；同号同班次日 ≤2 次（自动路径 ≤1） |

**命令（dry-run → 真操作）**：

```bash
# A1 只读判定（交互会话里用 MCP；本机终端等价）
#   mas_status(days=1)  → today.failed / never_ran / ledger
#   triage_error(account="<号>")  → 定因 + 规则（N1..Y3）

# A2 dry-run（预演；零写请求）
"$PY312" tools/auto_mas_rerun.py --accounts "<号1>,<号2>"          # CLI：默认预演
#   或 MCP：mas_rerun(accounts=["<号1>"], dry_run=True) → {"would_start":[…],"note":"预演：未触达远端"}
#   预演输出：模拟器体检表 / 在跑 MAA 目录 / 按目录分组的临时队列计划

# A3 真发车（需批准；CLI 加 --apply，或 MCP dry_run=False）
"$PY312" tools/auto_mas_rerun.py --accounts "<号1>,<号2>" --apply
# 可选：--skip-dead（体检没等到进程的号直接剔除；默认只告警不拦）
```

**验证判据**（发车成功 ≠ 号恢复）：

1. 发车当时：`mas_rerun_state()` → `ok=true` + `detail` 里的**临时队列名 / 发车 taskId**（state 事后会被清空，以它和 ledger 为准）；口径：`runtime/mas_ledger.jsonl` 有 `kind=rerun, ok=true` 行。
2. 闭环：该号**下一条 history 记录**的 `maa_result`（一次发车 AUTO-MAS 自动重试至多 3 次、约 50 分钟才可定论；跨零点按**班次日**归档，`search_history(DAILY, 班次日)`）。
3. 复核 ≤2 次、只读、不等待。

**回滚/收尾**：

- 临时队列跑完即删；**残留 <24h 必须清**：`"$PY312" tools/auto_mas_rerun.py --cleanup`（写操作，人工确认）。残留挂着产线目录，误发车即抢目录（09-27 存量 9 条即是教训）。
- 发错了/要立刻止：用该批发车返回的 `task_uid` 走 `POST /api/dispatch/stop`（见剧本⑤；不要 kill）。

**红线**：守卫窗不补；资源类（N1）不补；超上限（N4）转人工；已成功后的收尾噪声（N3）不补；肉鸽机队占用实例的号不补；**同一时刻只跑 1 条临时补跑队列**（与产线**不同目录**才算并行）。

---

## 2. 剧本②：某号改刷指定关卡 / 次数

**触发语**：「把 <号> 改刷 CE-6」「<号> 明天刷 1-7 打 20 次」「活动结束了，把 <号> 改回常驻关」「<号> 多吃两瓶药」。

**执行坐标**：本机终端（受控写脚本）或 AUTO-MAS 界面；Hermes 可先做只读定位。

**先确认（这一步决定「改哪一层」，错层=白改）**：

| # | 确认项 | 说明 |
|---|---|---|
| 1 | 号 → 脚本/目录/实例 | `"$PY312" tools/mas_scenario_dispatch.py --list-scripts`（只读）或 `mas_status` |
| 2 | 该用户是「简洁」还是「详细」模式 | 简洁用户由 `data/<sid>/Default/ConfigFile` 供模板；详细用户用 `data/<sid>/<uid>/ConfigFile`。**简洁模式下 AUTO-MAS 强制 `EnableTimesLimit=false`** ⇒ 「打 N 次」在简洁用户上**不生效**（该发生来自代码 L796–837，未实测触发路径——引 MAA_TASK_CONFIG_REFERENCE §14#14），要么用计划表口径、要么该用户必须是详细模式 |
| 3 | 「改什么」的歧义对齐 | 主关卡（`Info.Stage`）／回退位（`Info.Stage_1..3`，活动期挂"活动关+恒开放回退"的常用位）／战斗次数（MAA `EnableTimesLimit`+`TimesLimit`）／连战倍率（`Series`，用户字段 `SeriesNumb`，0=自动）／药量（`MedicineNumb`）。**用户说「次数」先问清是哪个** |
| 4 | 生效时点 | 改动在**下一轮发车**时由 AutoProxy 注入生效（不是当场）；MAA 侧手改只在「下次发车之前」有效，之后被重写 |
| 5 | 原值与回滚值 | 改前先落原值（plan），回滚靠它 |

**命令（dry-run → 真操作）**：

```bash
# B1 只读：定位 + 现况（对照 MAA_TASK_CONFIG_REFERENCE / maa-event-farming 的字段表）
"$PY312" tools/mas_scenario_dispatch.py --list-scripts --with-users

# B2 dry-run：受控写模具（先出 diff-plan，不写；参考 tools/mas_stage_fix.py 的机制：
#    写面 = POST /api/scripts/user/update（等价界面操作，绝不手改 ScriptConfig.json），
#    锁感知——脚本任务在跑时写会被拒「配置已锁定，无法修改」，等锁释放重试）
"$PY312" tools/mas_stage_fix.py --dry-run            # 产出 runtime/reports/stage_fix_<ts>/plan.json（逐号 diff）
"$PY312" tools/mas_stage_fix.py --apply --only "<号>" # 先小样单号试写（仍属生产写，需批准）

# B3 详细参数（次数/连战等 ConfigFile 字段）：按模板合并规则写
#    （同 mas_scenario_dispatch 写面2：同类型按 Name 命中、缺项不新增、切号字段跳过；
#    真写走备份→合并→回传→回读）
#    若仅活动场景参数 → 直接用剧本④的 --set 路径

# B4 验证后全量（如需）
"$PY312" tools/mas_stage_fix.py --verify
```

**验证判据**（等下一轮发车后）：

- `gui.log`：`GetFightStage: from ["<关码>", …], selected <关>` 必须是**预期关**（`selected null` = 静默跳过，说明关不可用/关码错）。
- 次数：日志出现完成行/`FightTimesMayNotExhausted` 警告（`effectiveMaxTimes % Series != 0` 的整除警告属正常噪音）。
- 回读：API 复读改动值 = 目标值（写后复读是铁律）。

**回滚**：`"$PY312" tools/mas_stage_fix.py --rollback`（按 plan.json 原值回写）；效果同样在**下一轮发车**生效。若不慎写坏，按「改前备份 + 原值回写」恢复。

**红线**：生产写须批准；先 dry-run 看 diff；写时确保目标脚本任务不在跑（锁拒写）；**不改没确认的字段**（MAA 重写时未知字段会丢）；活动关关码会过期（见剧本④收尾）；`StagePlan` 里常驻关/空串别夹在中间（后续关卡永不选中）。

> **现状缺口 H1（记录在案）**：没有「改某号关卡/次数」的通用 CLI——现有唯一模具是 SR 专项的 `mas_stage_fix.py`。其余需求靠：① 复用其机制新写受控脚本；② AUTO-MAS 界面操作；③ 剧本④的 `--set`。已入 §7 缺口清单。

---

## 3. 剧本③：发一局肉鸽

**触发语**：「发一局肉鸽」「让 l-1 刷一局」「给某号来一局肉鸽」「刷一局就停」。

**先确认**：目标（产线某脚本/号 还是 肉鸽机队某台 l-N）；**一局 vs 连刷**（`StartCount=1` vs `99999`）；主题/模式（l-1=Sami｜l-2=Sarkaz（09-24 起，`ROGUE_WATCH.md` §1 表仍是旧值 Sami）｜l-4=Sarkaz(官服)｜l-5=JieGarden｜l-7=Mizuki；**发车前核对游戏当前界面与 `Theme` 一致**——l-7 曾因主题/界面不匹配 20/20 耗尽翻车）；窗口（避开让位区 04:00–05:33 / 18:30–20:30）；谁批准（路线 B 属生产写）。

**两条路线（先选对，别混用）**：

### 3A. 机队直发（fz-maa，首选；l-1/l-2/l-4/l-5/l-7）

```powershell
# A1 先按住看守的自动重发（否则一局跑完看守会再拉起来）
#    临时：runtime/rogue_hold.flag 落文件；长期：schtasks /change /tn MAAOrch-RogueFleet /disable
# A2 确认该机没有 MAA 在跑（杀到残留=0；MAA 退出会把内存旧配置写回盘，先改后杀=白改）
# A3 备份 → 改 E:\maa-alas\fz-maa\l\l-N\config\gui.new.json 的 RoguelikeTask.StartCount=1
#    （只动这一条；6.17.5 机队写法；备份名 gui.new.json.bak-<yyyyMMdd>-<原因>）
# A4 发车：主机上 schtasks /run /tn MAAOrch-StartOne-<idx>   （idx：l-1=25 / l-2=28 / l-4=31 / l-5=34 / l-7=52）
#    或等本机看守 MAAOrch-RogueFleet（每 2 分钟节拍）在闸门开时自取
```

**dry-run 路径**：机队直发没有 dry-run——先 `node tools/rogue_fleet_watch.mjs --status`（只读一行：闸门/在跑）与 `node tools/rogue_acceptance.mjs`；写文件前必须备份。发车前置闸（看守同款，全过才发）：产线**不在跑** + **连续空闲 ≥6 分钟** + 窗口内（非让位区）+ 主机内存 ≥8G。

### 3B. AUTO-MAS 产线（场景发车，**草案·未实跑**）

```bash
# B1 dry-run（默认；打印计划 + G1–G7 闸门，零写请求）
"$PY312" tools/mas_scenario_dispatch.py --script "明日方舟-官-1-1-7" \
    --scenario 肉鸽 --times 1 --window 21:30-22:30        # 或 --duration 30（现在起 N 分钟）
# B2 真发车（草案双闸：缺一即拒）
MAAORCH_SCENARIO_APPLY=1 "$PY312" tools/mas_scenario_dispatch.py ... --apply
# B3 到期还原 / 手动还原 / 急停
"$PY312" tools/mas_scenario_dispatch.py --reconcile --apply      # 到期自动还原（Task+ConfigFile 回写）
"$PY312" tools/mas_scenario_dispatch.py --restore-run <run_id>   # 手动还原某次
MAAORCH_SCENARIO_NO_GO=1                                         # 一票否决（一切写被拒）
```

B 路线的闸门要点：G2 模板版本（**肉鸽模板眼下是 6.17.5 `CoreChar` 形态，目标 MAA_1..12 是 6.18.0 `StartingOpers`——直接注入会丢核心干员**，先按版本选形态）；G4 让位区；G5 preflight=GO；G6 目标目录空闲；G7 单飞（同脚本同日同场景一次）。

**验证判据**（跑起来后看什么）：

- `gui.log`：`完成任务: 开始唤醒` → `开始任务: 自动肉鸽` → `Start Task Chain: Roguelike, Task ID: N` → **`已开始探索 1 次`**（发一局看这一行）。
- `asst.log`：`"cur_task":"<Theme>@Roguelike@…"`（主题加载对）；链结局只看 `TaskChainCompleted`（完）vs `TaskChainError`（错）。
- ⚠️ 「任务已全部完成！」永远紧跟「任务出错」（~0.5s）——**完成 ≠ 成功**。
- 发车没起来：发车后 6 分钟仍 `parked` ⇒ 判 `start-failed`；连续两次交人工。

**回滚**：

| 场景 | 动作 |
|---|---|
| 机队改了配置想还原 | **停 MAA（杀到 0）** → `copy /y gui.new.json.bak-<日期>-<原因> gui.new.json` → 重启（StartOne 或看守） |
| 经 AUTO-MAS 发的要停 | `POST /api/dispatch/stop {"taskId":"<start 返回的 task_uid>"}`（优雅停＋解锁＋Temp 还原）；**别 kill** |
| 不想让看守再动手 | `runtime/rogue_hold.flag`；长期 disable `MAAOrch-RogueFleet` |
| 要立刻让位给日常 | 等让位闸 / `node tools/rogue_fleet_watch.mjs --drill-wrapup`（受控演练口径：只收 MAA 不动模拟器） |

**红线**：同一模拟器两个 MAA（绝对禁止）；产线在跑不动那台；改配置先备份且必须 MAA 不在跑；路线 B 开 `IfRoguelike`/动生产 ConfigFile = **生产写、先批准**（54 用户现状全 false）；**6.18 的 `StartingOpers` 在机队未实测**、AutoProxy 带肉鸽未实跑——向外引用时声明。

---

## 4. 剧本④：活动期插一轮活动关

**触发语**：「活动开了，插一轮活动关」「让 <号> 优先刷活动」「活动关优先打开」「临期药投到活动」「活动结束了顺手改回来」。

**先确认**：

1. **活动确实在开**：MAA 活动列表非空（`StageActivityV2.json` / AUTO-MAS `Data.Stage` 缓存 ≤1h）；目标关在列表内。
2. **目标关的写法**：关码（如 `SR-7`）；若走 `ActivityStageIndex`，**序号语义 = MAA 活动接口顺序**（SR 示例 `['SR-8','SR-7','SR-6','SR-5']` → 1=SR-8、2=SR-7）——**不确定先用序号 1 打日志核对**。
3. **药额度**：三套额度别串——活动关优先 `ActivityMedicineNumb`（1–2 瓶）/ 理智作战 `MedicineNumb`（0–1）/ 库存保持计划级（注入时药石强制 0）。临期药用 `UseExpireMedicineForActivity=true`。
4. **走哪条路线**（下表）+ 窗口（避开让位区；额外发车要等产线闸）+ **收尾责任人/时限**（活动结束日必改回）。
5. 生效方式：改配置在**下一轮发车**生效；额外队列是**立即发车**。

**四条路线**：

| 路线 | 做法 | 何时用 | 现状 |
|---|---|---|---|
| ① 计划表 Stage=活动关（+`Stage_1` 回退位如 `1-7`） | 改用户 `Info.Stage*`（受控写，见剧本②模具） | 日常链内解决、零新增发车（首选） | 现状主用法；**收尾必须改回** |
| ② IfActivityFirst | `Task.IfActivityFirst=true` + `Task.ActivityStageIndex=N`（生成独立「活动关优先」任务，插在 StartUp 后、理智作战前） | 想"活动关优先"又不想动 Stage | **未实跑**；全队现状 false |
| ③ 额外「冲活动」队列 | 复制一条 `临时-补跑-…` 队列（AutoProxy）+ **显式** `POST /api/dispatch/start`（或复用 rerun/scenario 工具口径） | 12 槽之外的整队冲活动 | 先例：09-27 他卡临时队列发车 |
| ④ 场景发车（草案） | `--scenario 活动 --set Task.ActivityStageIndex=N --set Task.ActivityMedicineNumb=1 --window …`（到期自动还原） | 短窗、就想用地利既有脚本跑一发 | **未实跑**（草案双闸） |

**命令（草案路线的 dry-run）**：

```bash
"$PY312" tools/mas_scenario_dispatch.py --instance 13 --scenario 活动 \
    --set Task.ActivityStageIndex=3 --set Task.ActivityMedicineNumb=1 --duration 60
# 默认 dry-run：打印计划/闸门/合并点；--apply + MAAORCH_SCENARIO_APPLY=1 才真写；到期 --reconcile --apply 还原
```

**验证判据**：`gui.log` 的 `GetFightStage: from [...], selected <活动关>`（选中预期关；`selected null` = 跳过要修）；「活动关优先」任务出现在 StartUp 之后；药消耗与记账核对（剿灭记账看 `AnnihilationCompletedWeek`）。

**回滚**：

- 改配置类：改回原值（plan/记录）；IfActivityFirst 关掉即恢复原链。
- 额外队列：跑完即删（`--cleanup` 口径）；排队中不想发 → ⑤ 的 stop/late 处置。
- **活动结束收尾（最常执行、最贵的一条）**：把受影响号的 `Stage` 改回常驻关——**SR 教训：9/18 结束、9/27 还有 46 个号挂着活动关白挂**（理智作战静默跳过、只剩剩余理智在烧）。

**红线**：活动结束不改 Stage = 白挂 46 号（现场教训）；药三套额度别串；手输活动关无 apCost 会被理智不足判定跳过（可能白跑）；让位区/产线闸照样适用；**不要给 AUTO-MAS 重开定时项**（回滚链会分叉）。

> 口径更正（H5）：`MAA_EVENT_FARMING_PLAYBOOK.md` §5.3 写「临时队列挂到 Hermes 发车表（mas_dispatch.py）」——**不准确**：`mas_dispatch.py` 的队列白名单只认 24 条班次队列（`^(日常|晚班)定时N$`），临时队列走**显式** `POST /api/dispatch/start`。本手册以本条为准。

---

## 5. 剧本⑤：停某队列 / 某号

**触发语**：「把晚班定时5 停了」「别让 <号> 再跑了」「队列卡住了，停掉」「急停，先别发车」。

**先分诊（停什么？停多久？）**：

| 对象 | 目标 | 手段 |
|---|---|---|
| **在跑任务**（止损） | 立刻停下当前任务 | 官方 `stop`（唯一会 unlock + 还原的口） |
| **某个号**（不再参与后续轮转） | 从下一轮起不跑 | 停用用户（`auto_mas_toggle_user.py`） |
| **某条队列**（未来不再发） | 阻止后续发车 | **无单队列开关**（H2）；见下方替代 |
| **全局**（都要停） | 急停一切 | `cron pause` 发车 job（±巡检 job） |

**命令**：

```bash
# ⑤-1 停「在跑任务」（止损；先找到 task_uid）
#     task_uid 来源：① runtime/mas_dispatch_log.jsonl 当日成功行（字段 taskId）
#                   ② mas_rerun_state / scenario dispatch 台账（其 run 的 taskId）
#                   ③ 派发时打印的返回值
curl -s -m 10 -X POST http://100.79.173.69:36163/api/dispatch/stop \
     -H "Content-Type: application/json" -d '{"taskId":"<task_uid>"}'
#     判据：app.log 出现「进程已中止 → 已解锁脚本配置」+「任务结束: <id>」；对已结束任务视成功；
#     重复 stop 会报「任务已在中止中」。**别 kill MAA**。

# ⑤-2 停「某号」（不再参与轮转；锁感知，等该脚本任务结束时才写）
"$PY312" tools/auto_mas_toggle_user.py --name "<号>" --dry-run        # 先预演（打印归属与现状态）
"$PY312" tools/auto_mas_toggle_user.py --name "<号>"                  # 停用（Info.Status 整对象回写）
"$PY312" tools/auto_mas_toggle_user.py --name "<号>" --enable         # 反向：启用
#     验证：API 复读 Status=false + ScriptConfig.json 落盘值 + 36163 存活（工具自带三重校验）；
#     审计：logs/auto_mas_toggle.jsonl；下一轮该号不再出现在「开始代理用户」序列

# ⑤-3 全局急停（按需逐级；恢复=resume）
hermes -p mas cron pause  2600f6415f16        # ① 只停发车（不再启动新队列）
hermes -p mas cron pause  52b88b0ffca0        # ② 连巡检一起停（一般不需要，巡检只读）
# ③ 在飞任务止损 = ⑤-1（不知道 task_uid 时不要乱停）
hermes -p mas cron resume 2600f6415f16        # ④ 恢复发车

# ⑤-4 周边处置（mas_dispatch）
"$PY312" tools/mas_dispatch.py --busy                      # 只读：busy 窗口 + 未关 late 标记
"$PY312" tools/mas_dispatch.py --dismiss-late <队列> --apply --note "放弃理由"   # 决定不补发（关 late 标记）
"$PY312" tools/mas_dispatch.py --release <队列> --apply    # 人工确认对方任务已结束后清 busy 窗口
```

**「停某队列（未来不发）」现状**：Hermes 发车按 SHIFT_TABLE 到点对账（幂等：同队列同天只发一次、超 30min 只告警），**没有单队列暂停开关**（H2）。可用的替代：全局 `cron pause`（影响全部，窗口过后恢复）；或停用该队列**全部脚本的号**（重，须批准；队列仍会被派发，但无号可跑）；当天已发过的队列天然不会再发。

**验证判据**：任务停：`app.log` 的「已解锁脚本配置」+「任务结束: <id>」（缺 = 僵尸，继续处理）；号停：Status 复读 + 下轮不含该号；急停：`hermes -p mas cron list` 显示 paused，`runtime/mas_dispatch_log.jsonl` 不再新增。

**回滚**：stop 之后要补回来 → 队列级：下一班次自然发 or 手动 `dispatch_queue(queue, dry_run=False)`（队列级补发须显式）；号级：下一班次 / `mas_rerun`（见①）；用户停用 → `--enable`；急停 → `cron resume`。**停用号 ≠ 停它当前正在跑的那一轮**（逐用户串行：要立刻停它只能停整个任务）。

**红线**：不知道 task_uid 不乱停；`stop ALL` 会清 power_sign（主动全停**不会**引发关机，语义相反要小心）；停产线任务会留下缺口（未跑的号 ⇒ 记入补跑判定池，别当"没发生"）；写用户配置要等锁释放（工具自等，别手改 ScriptConfig.json）。

---

## 6. 剧本⑥：把某台实例空出来给肉鸽线

**触发语**：「把 <idx> 那台空出来给肉鸽」「腾一台给肉鸽线」「l-2 那台先别刷，给肉鸽用」「收工让路」/ 反向：「肉鸽先让位，日常要跑」。

**先确认**：

| # | 确认项 | 说明 |
|---|---|---|
| 1 | 目标实例 idx → 归属 | 是否**机队共用槽**：emu 25/28/31/34/52 ↔ 产线 `MAA_1/2/9/10/12`（l-1/l-2/l-5/l-4/l-7）。非共用实例**加进机队 = 配置工程**（机队机器表 + StartOne 计划任务 + 配置），**不在本剧本授权内**，需另行立项 |
| 2 | 产线现在谁在用它 | `mas_status` 快照 / 主机进程探针（`Get-CimInstance Win32_Process … 'MAA-Multi'`）→ `MAA_N\MAA.exe`；队列进度 |
| 3 | 用途与时长 | 给机队哪台（l-N）/ 给多久 / 是否只跑一局 |
| 4 | 能否等 | **默认等产线跑完**（等 = 让位闸自动接管）；要"现在就要"才谈受控强停（需批准，后果=该队列剩余号进缺口池） |
| 5 | 窗口 | 让位区（04:00–05:33 / 18:30–20:30）内**不发车**；其余时间看闸 |

**标准步骤（产线槽 → 让给肉鸽机队）**：

```text
① 只读现状：
   "node tools/rogue_fleet_watch.mjs --status"        （本机：机队一行状/闸门）
   mas_status / 主机进程探针                          （产线谁在跑、目录闲忙）
② 让产线释放：
   - 默认：等该槽任务跑完（信号：gui.log「任务已全部完成」/ 队列走完 / 产线连续 6 分钟空闲）
   - 强停（需批准）：按剧本⑤ stop 该队列任务（用 task_uid）——**绝不 kill MAA**
   - （没有"跳过某队列"开关；也不要为让路去改排班；H2）
③ 确认移交条件（看守同款闸，fail-closed）：
   产线不在跑 + 连续空闲 ≥6 分钟 + 非让位区 + 主机内存 ≥8G
④ 肉鸽侧接管：
   - 自动：MAAOrch-RogueFleet（本机，每 2 分钟）闸门开时发车；
   - 人工：主机上 schtasks /run /tn MAAOrch-StartOne-<idx>（idx=25/28/31/34/52；登录态计划任务，SSH 直拉模拟器无效）
⑤ 验证：见下
```

**反向（肉鸽占用中，要立刻让给日常）**：让位闸会在产线回来时自动**收工让路**；人工口径 = `node tools/rogue_fleet_watch.mjs --drill-wrapup`（只收 MAA、不动模拟器；恢复用 `--apply --force` 重发车）；要按住看守 = `runtime/rogue_hold.flag`。

**验证判据**：

- 主机进程命令行归属：肉鸽 MAA = `E:\maa-alas\fz-maa\l\l-N\MAA.exe`；产线 MAA = `E:\MAA-Multi\MAA_N\MAA.exe`（**归属只认进程命令行**，快照里的计数不分区）。
- `node tools/rogue_fleet_watch.mjs --status`：目标机从 `parked` → 起车（含闸门原因串）。
- 肉鸽侧起车后按剧本③的 gui.log 判据看「已开始探索」。
- 交接期间若出现 `⚠️ 冲突：产线在跑（N/16），而肉鸽也在跑（l-…）` ⇒ 本质约束被破了，立刻收肉鸽（wrapup）。

**回滚**：把实例还回产线 = 收肉鸽 MAA（让路/`--drill-wrapup`）→ 产线**下一班次照常发**（不用改任何东西；若强停过产线任务，剩余号按①判定补跑）。

**红线**：同一模拟器两个 MAA（绝对）；产线在跑不动那台；让位区不发起；**不 kill 产线 MAA、不 kill-server**（主机 adb 是机队+产线共用，连坐）；l-7 机队注意队列闸（历史上有"只勾 StartUp 不发车"的形态，发车前先看看守的队列闸留痕）；本机 `实例1（16384）= 用户自己的 JP 机，任何情况下不碰`。

---

## 7. 现状缺口（影响剧本可执行性；补跑链路缺口见 MAS_SHIFT_GAPS G1–G12）

| # | 缺口 | 影响 | 临时替代 |
|---|---|---|---|
| H1 | 无「改某号关卡/次数」通用受控工具（唯一模具=SR 专项 `mas_stage_fix.py`） | 剧本②部分靠手写受控脚本/界面 | 复用 mas_stage_fix 机制；界面操作 |
| H2 | 无「单队列停发」开关（只有全局 pause / NO_GO / 停用户） | 剧本⑤"停某队列（未来不发）"不完整 | 全局 pause + 窗口后恢复；或停该队列全部号（重） |
| H3 | 场景发车（剧本③B/④④）`--apply` **从未实跑**；肉鸽模板版本形态未定；活动参数语义未在真机验证 | 这两条真发车路径是"草案" | 先 dry-run + 单测试号演练（§8.2） |
| H4 | 观察面未接线：巡检的 `CLIENT_STATE`/`POWER`/`IDENTITY` 恒 `UNKNOWN`；`mas_preflight` 未进自动链路（G1/G4） | 剧本验证判据里这几项要**手动**跑 CLI | 手动 `client_state_check.py` / `mas_identity_trace.py` / `mas_preflight.py` |
| H5 | 临时队列发车没有统一入口；`MAA_EVENT_FARMING_PLAYBOOK §5.3` 的"挂 mas_dispatch 发车表"与白名单不符 | 文档口径分叉 | 本手册 §4 已更正：显式 `POST /api/dispatch/start` |
| H6 | 巡检/发车报告 deliver=local（无送达通道）（G11） | "该拍板了"没有信号 | 人工查看；或接 gateway 平台 |

**升格条件（草案路线何时可变正式）**：① 该路线 dry-run 全绿 ≥3 次（跨日）；② 单测试对象实跑 1 次成功 + 回滚演练 1 次（留痕 docs/audits/）；③ 用户批准；④ 在 `auto-mas-host-ops`/本手册把"草案"字样去掉并注明实测日期。

---

## 8. 演练与验收（全是 dry-run / 只读路径）

### 8.1 本卡实跑（2026-09-28 00:1x，只读）

| 检查 | 命令 | 结果 |
|---|---|---|
| 场景发车自检 | `"$PY312" tools/mas_scenario_dispatch.py --selftest` | **OK 44/44**（含 merge/让位区/写白名单/双闸 env 名） |
| 关卡写模具自检 | `"$PY312" tools/mas_stage_fix.py --selftest` | **SELFTEST=OK**（含 opener 绕代理检查） |
| 肉鸽一局 dry-run（纯本地） | `… --script "明日方舟-官-1-1-7" --scenario 肉鸽 --times 1 --duration 30 --offline` | 打印计划（备份→合并 `StartCount=1`→发车→到期还原）；G4 让位区 GO；G2 模板 WARN（6.17.5 形态）；G3/G5/G6 SKIP（dry-run+offline）；**零写请求**，exit 0 |

### 8.2 各剧本的 dry-run 路径汇总

| 剧本 | dry-run 命令 | 期望输出 |
|---|---|---|
| ① | `mas_rerun(accounts=[…], dry_run=True)` / `auto_mas_rerun.py --accounts …` | `would_start` + 体检表（零上游写） |
| ② | `mas_stage_fix.py --dry-run`（→ plan.json）；`mas_scenario_dispatch … `（默认） | 逐号 diff / 计划+闸门 |
| ③ | 机队：`rogue_fleet_watch.mjs --status` + 备份；产线：scenario dry-run | 闸门一行 / G1–G7 |
| ④ | scenario dry-run（`--set Task.ActivityStageIndex=N`） | 计划 + 活动参数合并点 |
| ⑤ | `auto_mas_toggle_user.py --dry-run`；`mas_dispatch.py --busy` | 归属/现状态；busy/late 视图 |
| ⑥ | `rogue_fleet_watch.mjs --status`；进程探针（只读） | 在跑归属 + 闸门原因串 |

### 8.3 建议的首次实弹演练顺序（用户批准后、逐条来）

1. 剧本①在一批**真实失败号**上走一遍（先预演，后发车，跑完核销）——这是唯一已有大量实战的剧本。
2. 剧本⑤ `--dry-run` + 一次真实"停号→启用"（无副作用用例）。
3. 剧本③机队版：拿一台空闲机 `StartCount=1` 实跑 + `copy .bak` 回滚演练（看守先按住）。
4. 剧本②用 `--only` 单号小样实写 + `--rollback` 回滚，各留痕一次。
5. 剧本④在真实活动期走"计划表 Stage"路径 + 收尾改回（本次 SR 教训的正面复现）。
6. 剧本③B/④④（草案路线）最后再说：先 dry-run 跨日三次，再单测试号实跑。

---

## 附录 A：槽位速查（12 目录 ↔ 队列对 ↔ 共用关系）

| MAA 目录 | 脚本数 | 队列对 | 肉鸽共用 |
|---|---|---|---|
| MAA_1 | 5 | 日常定时1 / 晚班定时1 | **l-1（emu25）** |
| MAA_2 | 2 | 日常定时2 / 晚班定时2 | **l-2（emu28）** |
| MAA_3 | 5 | 日常定时3 / 晚班定时3 | — |
| MAA_4 | 6 | 日常定时4 / 晚班定时4 | — |
| MAA_5 | 5 | 日常定时5 / 晚班定时5 | — |
| MAA_6 | 2 | 日常定时6 / 晚班定时6 | — |
| MAA_7 | 5 | 日常定时7 / 晚班定时7 | — |
| MAA_8 | 4（另有 2 个未排班脚本：官-25/官-28） | 日常定时8 / 晚班定时8 | — |
| MAA_9 | 5 | 日常定时9 / 晚班定时9 | **l-5（emu34）** |
| MAA_10 | 5 | 日常定时10 / 晚班定时10 | **l-4（emu31）** |
| MAA_11 | 4 | 日常定时11 / 晚班定时11 | — |
| MAA_12 | 4 | 日常定时12 / 晚班定时12 | **l-7（emu52）** |

（完整 52 行槽位表见 `MAS_SCHEDULE_MAP.md` §3；另有 2 个未排班脚本见 §5.1：官-25（用户停用）/官-28（启用无队列），均在 MAA_8 目录。）

## 附录 B：索引

| 主题 | 文档/工具 |
|---|---|
| 接管契约 / 发车补发边界 / 工具面刷新 | `docs/HERMES_MAS_HANDOVER.md`（§12.1/§13） |
| 队列/槽位/冲突全图 | `docs/MAS_SCHEDULE_MAP.md` |
| 班次链路 + 缺口 G1–G12 | `docs/MAS_SHIFT_PLAYBOOK.md` / `docs/MAS_SHIFT_GAPS.md` |
| 补跑三问 C3 全文 | `docs/MAS_RERUN_POLICY.md` |
| 急停/回滚/故障判定树 | `docs/MAS_EMERGENCY_RUNBOOK.md` |
| 肉鸽（发一局/连刷/让位） | `docs/MAA_ROGUELIKE_PLAYBOOK.md` + 技能 `maa-roguelike-ops` |
| 活动/剿灭/仓库 | `docs/MAA_EVENT_FARMING_PLAYBOOK.md` + 技能 `maa-event-farming` |
| 字段速查（改哪层） | `docs/MAA_TASK_CONFIG_REFERENCE.md` + 技能 `maa-task-config` |
| 场景发车草案（③B/④） | `docs/MAS_SCENARIO_DISPATCH.md` + `tools/mas_scenario_dispatch.py` |
| AUTO-MAS 内部机制（stop/锁/电源） | `docs/MAS_INTERNALS.md` |
| 技能沉淀（t_0f598a66） | 技能 `auto-mas-host-ops` v1.3.0（+ refs） |
| 本手册的技能草稿 | `docs/skills-draft/mas-playbooks/`（未安装；README 说明） |

---

_生成：t_4d8f3ee3（mas）｜2026-09-28 凌晨｜只读剧本，未执行。_
