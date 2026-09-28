# P1-2 跨族单飞（instance lease）消费者侧接入 — 证据（2026-09-28）

本卡 = t_1df99ae5。机制本体（`tools/mas_instance_lease.py` + `runtime/instance_leases.json`）由 t_65b69753 交付；
本卡只做**消费者侧薄胶水** `tools/mas_lease_glue.py` 并接入 4 个调用点，不新造机制、不写第二本账。

## 接入点与闸门口径（G3L）

| 接入点 | 位置 | 语义 |
|---|---|---|
| `tools/mas_preflight.py` | 第 3L 项（只读检查） | 目标实例被别的任务租下 ⇒ status=FAIL；自己的 ⇒ self；报告逐台并入 |
| `tools/mas_run_profile.py` | 计划期只读探针 + `_apply_locked` 锁内取租 → 全流程 → `finally` 释放 | 拿不到租约 ⇒ rc=2 拒发，**零写**（不备份/不写配置/不发车）；**dry-run 与 apply 同口径**（见「第二轮 1)」） |
| `tools/mas_ondemand.py` | 闸门链 `single_flight(G3)` → **`lease(G3L)`** → `preflight(G4)` | 拒发零上游写；发车前取租、失败即放、`stop`/`release` 收尾释放 |
| `tools/mas_ondemand_mcp.py` | `_dispatch_target`（queue/script）+ `tool_dispatch_account`（临时队列前） | 同上；`stop_task` 成功按记录释放 |

owner 口径（统一）：工具参数/`--lease-owner` > `MAAORCH_LEASE_OWNER` > `HERMES_KANBAN_TASK` > 工具默认（`cli:run_profile` / `ondemand:cli` / `ondemand:mcp`）。
`--force`／`force_conflict` **放行不了租约**（要等对方到期或持有者 release）。TTL：run_profile 1800s；ondemand 侧 `max(600, HOLD_MIN*60)`，可用 `MAAORCH_ONDEMAND_LEASE_TTL_S` 覆盖。

## 实测（首轮，本机，Python312）

| 命令 | 结果 |
|---|---|
| `python tools/mas_preflight.py --selftest` | exit 0（全绿，含 3L 用例） |
| `python tools/mas_run_profile.py --selftest` | exit 0（全绿，含 lease 拒发零写/生命周期用例） |
| `python tools/mas_ondemand.py --selftest` | exit 0（36/36，含 6 条 G3L 用例） |
| `python tools/mas_ondemand_mcp.py --selftest` | **OK (20/20)**（含 `lease_busy_refused_zero_write`、`lease_kept_then_stop_releases`） |

## 第二轮（retry #2，11:23–12:05）：dry-run 收紧 + 同族自锁修复 + 跨族真账本冒烟

### 1) run_profile dry-run 收紧为 fail-closed（卡面验收「lease 持有中 run_profile dry-run/preflight 必须 NO-GO」）

首轮实现把 dry-run 做成「只提示、rc 不变」；第二轮按卡面改为**与 apply / ondemand 同口径**：

- 计划期只读探针（`build_plan` 的 G3L）被占 ⇒ 进 `plan["refusals"]`（gate=G3L, reason=`lease_busy`），**dry-run 也 rc=2 拒发**；
- apply 仍在本进程锁内 `acquire` 再判一次（兜住「探针 → 取租」之间的真竞态；该处只剩竞态分支）；
- 拒发留痕两处同口径（`refuse_lease_records()`）：审计流 `runtime/mas_run_profile_log.jsonl`（`reason=lease_busy` + lease 短视图）+ 全局台账 `runtime/mas_ledger.jsonl`（`kind=run_profile_refused`）；文案**原样沿用**租约工具输出（持有者卡号 + 到期时间）。
- selftest 用例 `lease_busy_refused_zero_write` 同步收紧并加强：dry-run rc=2、拒绝文案带持有者与到期、dry-run 路径零写/不落备份、apply 仍零写、释放后 dry-run 回原路径 rc=0。

### 2) 修复同族自锁：`mas_farm_orchestrator.py` 步骤 ⑥/④b 未把租约持有者透给 run_profile

- 现象（代码级）：orchestrator 步骤 ⑤ 以 `args.owner` 取租，但步骤 ⑥ `mas_run_profile.py --apply` 的命令行与环境里都没有该 owner ⇒ run_profile 缺省 `cli:run_profile` 会把**上游自己的租约**当成「别人占着」而 rc=2 拒发（CLI 直跑必现；kanban worker 环境因 `HERMES_KANBAN_TASK` 兜底而侥幸不撞）。
- 修复：`deps_farm` / `deps_plan_preview` 命令均加 `--lease-owner <owner>`；同主走「续租」语义，run_profile 收尾释放后 orchestrator ⑨b 的 release 读到「本来就没有」（幂等，属预期）。
- 新增 selftest `t_farm_cmd_lease_owner`（monkeypatch `_run_cmd` 断言两条命令都带 `--lease-owner`、预览仍无 `--apply`）→ orchestrator selftest 24/24 ⇒ **25/25**。

### 3) 跨族真账本冒烟（`smoke_crossfamily_20260928.txt`）

目标：脚本 `明日方舟-官-12`（idx 27，当时 VMM 空闲）；真账本 `runtime/instance_leases.json`。

| # | 场景 | 结果 |
|---|---|---|
| 0 | 租约空闲（控制组） | `run_profile dry-run` rc=0，`实例租约(G3L): 空着/自己的 idx=27` |
| 1 | 族 A 持有（owner `t_p1_2_rp_holder`）⇒ 族 B | `mas_ondemand --script …` **dry-run rc=2**，`拒绝[G3L]: lease_busy — idx27 被 t_p1_2_rp_holder 占着（用途…；到期 11:55:16）` |
| 2 | 族 B 持有（owner `t_p1_2_od_holder`）⇒ 族 A | `run_profile dry-run` **rc=2** + `拒绝[G3L]`（带持有者与到期）；`--dry-run --preflight` 同 rc=2；`mas_preflight` **3L FAIL** + `PREFLIGHT=NO-GO`（reasons 里带同一条租约文案） |
| 3 | 释放后（控制组） | `run_profile dry-run` rc=0；preflight 3L 回 **PASS**（余下的 NO-GO 是**另一项**注入闸门，见下） |
| 4 | 账本收尾 | `list` → `（没有在租实例）LEASES=0` |
| 5 | 同主续租（orchestrator 场景） | owner `t_p1_2_orch` 持有；`run_profile --dry-run --lease-owner t_p1_2_orch` rc=0（`自己的`）；对照 `--lease-owner t_other` rc=2 拒 —— 修复所依赖的语义实测成立 |
| 6 | 六工具 dry-run 语义抽样 | `mas_probe_farm --account 烤箱 --stage 1-7`（默认 dry-run）rc=0，内层 `farm-dry-run rc=0`，语义未变 |

**第 7 步（追加）编排器全链活体尝试**：`mas_farm_orchestrator --account 烤箱 --stage 1-7 --dry-run --owner t_p1_2_orch_live` → 编排器自身 preflight 首闸 `config_gate` 未过 ⇒ 链路在 ④b/⑥ 之前 rc=2 停下，**未覆盖到 --lease-owner 透传的活体证据**（见 `smoke_orch_seam_20260928.txt`）。替代证明：(a) 机制级活体 = 本表第 5 行（同主续租 rc=0 / 异主 rc=2）；(b) 命令级 = selftest `t_farm_cmd_lease_owner`（④b/⑥ 两条命令都带 `--lease-owner`，25/25 绿）。

**顺带发现（非本卡范围，记录备查）**：`mas_preflight` 对 `E:\MAA-Multi\MAA_12` 报 `[FAIL] 5 注入闸门 VIOLATION`，同一现象正是编排器 `config_gate` 拒发的原因（第 2/3 步冒烟与第 7 步都命中）。既有问题、与租约无关、本卡未触碰；但**它会挡住任何落在 MAA_12 上的真跑**（含编排器 apply 链），建议 lead 单开一条排查（先判「人改配置 vs 基线漂移」，再决定重签基线还是回滚配置）。

### 4) 终验（`selftests_final_20260928.txt`，Python312，2026-09-28 11:48）

| 命令 | 结果 |
|---|---|
| `mas_instance_lease.py selftest` | rc=0，0 失败 |
| `mas_run_profile.py --selftest` | rc=0，**36/36** |
| `mas_ondemand.py --selftest` | rc=0，**36/36** |
| `mas_ondemand_mcp.py --selftest` | **OK (20/20)** |
| `mas_preflight.py --selftest` | rc=0，0 失败 |
| `mas_farm_orchestrator.py --selftest` | rc=0，**25/25**（含新增 `--lease-owner` 用例） |
| `mas_probe_farm.py --selftest` | rc=0，**23/23** |
| `tools/tests/test_regression.py` | **REGRESSION=PASS（29 passed / 0 failed / 5 skipped）** |

## 隔离与现场清理（实测）

- 所有 selftest 一律把租约账本重定向到 `MAAORCH_LEASE_RUNTIME=<tmp>/lease`（`lease_mod().set_runtime()`），**绝不动** `runtime/instance_leases.json`；
  自测收尾还原环境与路径。
- 诚实记录：mas_ondemand 自测首轮曾把 2 条**假发车**租约写进真账本（owner `ondemand:cli`，`按需发车 脚本A/B（script）`，idx1/idx2，到期 13:32）。
  已在 11:21 用 `mas_instance_lease.release` 逐条回收（release 留历史行），回收后在租 `{}`；真账本现无本卡残留。
- 第二轮冒烟用的 3 条真租约（`t_p1_2_rp_holder` / `t_p1_2_od_holder` / `t_p1_2_orch`）全部当场 release，末次 `list` = `LEASES=0`。
- MCP 面工具契约**未变**：仅新增可选参数 `lease_owner`（additive）；新租约闸门需**重启 MCP server** 才在会话里生效（既有口径）。

## 与审计接线卡 t_a190ca4b 的同文件串行（tools/mas_ondemand.py）

- 两组改动**同文件共存**，未互相覆盖：`grep` 计数 = 租约标记 10 处 / 审计标记 4 处；
- 对方 3 处 hunk 原样保留：`_unified_from_audit()`（纯旁路）、`audit()` 末尾 `safe_append(...)` 与 `stream_for_runtime(RUNTIME)`；
- 本卡的租约逻辑**全部在 `audit()` 之外**，只通过 `audit(rec)` 传新 kind（`lease_acquire`/`lease_release`）与新键（`lease_*`），旁路自动透传；
- 该文件本卡**已收口，不再动**（后续如需再改先与审计卡持卡人打招呼）；第二轮未再改该文件。

## 边界 / 未做

- `tools/mas_dispatch.py`（24 条班次队列）不在本卡 4 个接入点内：仍走既有 busy 窗口口径；若要跨族单飞覆盖班次族，另开卡。
- 不做排队等待策略（`wait_s` 已预留但默认 0）；不做自动抢占（抢占仍须人判）。
- 未做**真·apply 的实机拒发演练**：apply 侧零写契约由 selftest（假传输：断言无 `/api/*` 写调用、无备份目录、无运行台账）覆盖；真账本冒烟只跑 dry-run/preflight（避免「若闸门失守就真发车」的风险）。
