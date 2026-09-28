# activity_switch.py —— 活动期「刷取计划」自动切换（受控 + 审批 + 可还原）

> 解决「**活动一开，很多号该改刷活动关；活动一关，该切回主线**」：做成**受控自动化**，不再靠人记得改。
> 现场教训：SR「月行水上」09-18 结束，46 个号一路空挂到 09-27 才被巡检抓到（技能 `maa-event-farming` §0-2）。
>
> 文件：`tools/activity_switch.py`（**默认 dry-run**；真写要 `--apply` **且** `--approved-by`）
> 依赖：`tools/mas_run_profile.py`（备份/临时计划表/StageMode/还原骨架）、`tools/stage_capability.py`（能力台账）、
> `tools/mas_probe_farm.py`（unknown 先试探）

---

## 1. 三条判据（全部可解释，落进报告的「依据 → 结论」链）

| # | 判据 | 来源 | 语义 |
|---|---|---|---|
| ① | 触发 | `runtime/reports/server_events.json` | 各服在期/待开活动（快照 >3 天告警、>7 天**拒绝据此切换**，可用 `--maa-json` 之外的刷新流程先更新，见 `docs/SERVER_EVENTS.md`） |
| ② | 可刷层（**权威 / fail-closed**） | MAA 活动表 `StageActivityV2.json` | 该 client 段**未过期**的 `sideStoryStage` → 关码列表（滤掉「代理」伪条目）。**表里没有 = 不可切**，绝不猜 |
| ③ | 能力台账 | `runtime/reports/stage_capability.json` | `success` 直切 / **`unknown` 先试探一次再切** / `failed` 从目标里剔除 |

目标关码来源优先级：`--stages`（显式，表未收录也行）＞ `config/activity_switch_plans.json`（per-服覆盖，可选）＞
MAA 表在期段 ＞ `config/activity_stages.json`（官服检测器缓存）。MAA 表 fail-closed 永远兜底，除非显式 `--stages`。

## 2. 动作语义（逐号判定）

| 动作 | 何时 | 效果 |
|---|---|---|
| **switch** | ②可刷 + ③success（或 `--stages` 显式） | 写：临时计划表 + StageMode 指针 |
| **probe** | ②可刷但③unknown | 默认只列「需试探」；`--probe-unknown` 时先跑 `mas_probe_farm.py --apply --probe-only`，**成功才切**（失败/无定论零写） |
| **restore** | 我台账里有该号的未还原切换，且活动 end 已过 / MAA 已不可刷 | 切回备份的原计划（StageMode 回写 + 删临时表） |
| **heal**（opt-in） | `--heal-stale`：该号计划所有非空位都是**已关闭活动关**（如 `[SR-7 SR-6 - -]`）⇒ MAA 整段跳过（白烧） | 收尾切到 `--heal-to`（默认 1-7 / 该号剩余理智位）；记录 `status=healed`，**不参与还原**（终态，防被推回死关） |
| **already** | 当前计划首位已是目标关 / 已在我方切换记录中且 StageMode 仍指向临时表 | 不写 |
| **skip / noop** | 不可切（fail-closed）/ 未启用 / 证据过期 / 计划无效 | 不写，`--report` 里带原因 |

**失效记录自愈**：记录里 `temp_plan.plan_id` 与该号当前 `StageMode` 不一致（被人工改回/他方改过）⇒ 该记录
**视为失效**，按现况重判（不会把号卡在「already」里干等）。

## 3. 写面与还原（不碰存量计划表）

* 写：**新建**「临时-活动-<服标>-<stamp>」计划表（全量复制该号当日生效值 + 只差分期关卡位）→ 把
  `Info.StageMode` 指过去。**账号自己的 7 键与存量计划表一律不碰**。
* 备份：`runtime/activity_switch/backups/<switch_id>/`（`accounts_before.json` / `plans_before.json` /
  `meta.json` / `hashes.json`；凭据键读入即弃）。凭备份可一键还原。
* 还原：`--restore <switch_id>` / `--restore-all`（dry-run 须再给 `--apply`）；活动结束（end 已过或 MAA 不可刷）
  也会在下次 `--apply` 时**自动纳入计划**。
* 跑中「配置锁定」：写被 AUTO-MAS 拒绝 ⇒ 该号跳过（零残留：临时表当场清）；**绝不 kill / 重启任何东西**。

## 4. 闸门（apply 时逐条；dry-run 只打印）

| 闸 | 内容 |
|---|---|
| G0 | `MAAORCH_ACTIVITY_SWITCH_NO_GO` / `MAAORCH_DISPATCH_NO_GO` 一票否决（rc=2） |
| G1 | REST 可达（`MAAORCH_MAS_API`，绕系统代理） |
| G2 | **apply 必须显式给范围**（`--server` / `--accounts` / `--scripts`） |
| G3 | **apply 必须 `--approved-by <谁/依据>`**（写入审计与全局台账；缺 = 拒绝） |
| G4 | 证据新鲜：server_events >7 天 ⇒ 拒绝切换（>3 天告警）；期间**不产生 switch/probe 动作** |
| G5 | 单飞锁 `runtime/activity_switch.lock`（长动作逐试探/逐号写会续锁，6h 无心跳才视为陈旧） |
| G6 | 班次运行中不动：先 ssh 探 MAA.exe 占用（忙 = 跳过该脚本的号；探不到 = 告警后仍走，写被 AUTO-MAS 锁兜底） |

退出码：`0` 正常 ｜ `2` 拒绝（参数/闸门/证据过期，零写）｜ `3` 部分完成（班次忙跳过 / 试探未证 / 单项失败已清理）｜
`4` 有残留或执行中断（需人工，`--status` / `--list-plans` 核对）。

## 5. 用法

```bash
PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"

# 计划（默认 dry-run：只算不写；落 runtime/reports/activity_switch/*.md|.json）
$PY312 tools/activity_switch.py --server cn_official
$PY312 tools/activity_switch.py --server jp --accounts "日甲,日乙"
$PY312 tools/activity_switch.py --server cn_official --pretend-now "2026-09-29 17:00"   # 预演某时刻（不写）
$PY312 tools/activity_switch.py --server cn_official --stages "VEC-08,VEC-07,VEC-06"    # 显式目标（表未收录也行）

# 真写（需用户/lead 批准；--approved-by 记入审计）
$PY312 tools/activity_switch.py --server cn_official --apply --approved-by <批准人> [--probe-unknown] [--max-probes 3]

# 还原 / 台账
$PY312 tools/activity_switch.py --restore asw_20260929_1601_cn_official [--apply]
$PY312 tools/activity_switch.py --restore-all --apply
$PY312 tools/activity_switch.py --status          # 切换台账（含未还原）
$PY312 tools/activity_switch.py --list-plans      # 只读：计划表 + 「临时-活动-*」残留
$PY312 tools/activity_switch.py --selftest        # 离线自测（假传输，零触网零写 runtime）
```

环境变量：`MAAORCH_MAS_API` / `MAAORCH_SSH_KEY` / `MAAORCH_SSH_HOST`（同其它 `mas_*` 工具）；
`MAAORCH_ACTIVITY_SWITCH_NO_GO` 一票否决。

## 6. 标准工作流（活动开 / 关）

1. **活动开**（如国服 09-29 16:00 矢量突破#3）：等 MAA 表收录该段（`api.maa.plus` 的 `StageActivityV2.json`），
   跑 dry-run 复核「会改哪些号、改成什么」→ lead 批准后
   `--apply --approved-by <批准人> --probe-unknown`。
2. **活动关**：下一次 `--apply`（或专项 `--restore-all --apply`）自动把到期的切换还原回原计划；
   `--status` 常看「未还原」列表。
3. **收尾（可选）**：对计划全挂在已关闭活动关的号（`[SR-7 SR-6 - -]` 这类白烧形态）用
   `--heal-stale --apply --approved-by <...>` 切到 1-7。
4. 窗口纪律：班次运行中不硬改（G6）；04:00 / 19:00 宿主计划重启前后别排。

## 7. 自测（selftest 13 例，全绿）

`--selftest` 用假传输 + 假 MAA 表 + 假事件快照覆盖：JP 直切判定与尾位 1-7、CN fail-closed、unknown⇒试探
（未开探针跳过 / 探针成功才切 / 探针失败零写）、写面（临时表复制+差分+备份）、还原（dry-run 零写 / 回写+删表 /
幂等）、活动结束自动还原、班次锁定零残留、heal 收尾、四条闸门（显式范围 / approved-by / NO_GO / pretend-now 与
apply 互斥）、证据过期拒绝、`--stages` 强制与关码数校验、报告渲染与计划文件落盘、`record_due` 两条判据。
末行 `SELFTEST=OK (13/13)`；已登记进回归套件 `tools/tests/test_regression.py`（F1 全工具离线自检）。

## 8. 本次实跑（2026-09-28，国服 矢量突破#3 自测）

**实跑 A（实时 dry-run，官服全队）**：`--server cn_official`

* 结论：`switch 0 ｜ probe 0 ｜ heal 0 ｜ restore 0 ｜ skip 38`
* 38 个官服号全部 **skip（fail-closed）**——MAA 表 Official 段当前无在期活动关（SR/AT 均已关闭），
  报告同时给出「待开：矢量突破#3「拟生态」（2026-09-29 16:00 起）」与已关闭活动关清单。
* 产物：`runtime/reports/activity_switch/20260928_1020_cn_official.md|.json`（10:20 复跑与 10:07 首跑口径一致：`skip 38`、rc=0；首跑文件保留在
  同目录 `20260928_1007_*`）

**实跑 B（预演 09-29 17:00 + 显式目标）**：`--pretend-now "2026-09-29 17:00" --stages "VEC-08,VEC-07,VEC-06"`

* 结论：`probe 36 ｜ skip 2`（36 个在跑号将切到 `[VEC-08 VEC-07 VEC-06 1-7]`，但 ③ 台账 unknown ⇒
  按任务第 4 条**先各试探一次**，成功才切；2 个未启用号不动）。
* 说明：MAA 表当前尚未收录 VEC#3 段（活动未开），预演用 `--stages` 显式指定；**到点后应以 MAA 表实收录
  的关码/顺序为准**（届时直接 dry-run 复核，无需 `--stages`）。VEC-08/07/06 为示例代码，发放前请核对。
* 产物：`runtime/reports/activity_switch/20260928_1010_pretend_cn_official.md|.json`

两条实跑均为 **零写**（无 --apply：不建计划表、不写账号、不动 runtime 之外的任何东西）。

## 9. 产物 / 审计

| 路径 | 内容 |
|---|---|
| `runtime/activity_switch_state.json` | 切换台账：凭它还原，不凭记忆（含 `temp_plan` / `prev_stage_mode` / `backup_dir` / `status`） |
| `runtime/activity_switch/backups/<switch_id>/` | 账号/计划表快照 + sha256（凭据键读入即弃） |
| `runtime/reports/activity_switch/*.md|.json` | 每次的计划/执行报告（人读 + 机读，schema `maaorch.activity_switch_plan/1`） |
| `runtime/mas_activity_switch_log.jsonl` | 本工具审计流（逐号 written/skip/probe/restore） |
| `runtime/mas_ledger.jsonl` | 全局台账（`kind=activity_switch` / `activity_switch_restore`） |

## 10. 已知限制 / 红线

* **不做**：直接改存量计划表；改账号自己的 7 键；班次运行中硬改（配置锁定）；自动批准（apply 永远要人）。
* MAA 表 lag：官方日历已开但 MAA 未收录 ⇒ 工具 fail-closed 不切（这是特性）；要抢开局用 `--stages` 显式指定并自行核对关码。
* 台账 unknown 全队常见（新活动关第一次见）⇒ 首轮是「逐个试探」，建议 `--max-probes` 限次、分批推进。
* heal 目标 `1-7` 是「恒开放兜底」；若该号剩余理智位非空则优先用剩余理智位（`Stage_Remain`）。
* 本工具与 `mas_probe_farm` / `mas_run_profile` 共用 REST 与主机；发车窗口纪律见 `docs/TOOLBOX.md` §0。
