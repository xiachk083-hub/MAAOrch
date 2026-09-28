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

## 隔离与现场清理（实测）

- 所有 selftest 一律把租约账本重定向到 `MAAORCH_LEASE_RUNTIME=<tmp>/lease`（`lease_mod().set_runtime()`），**绝不动** `runtime/instance_leases.json`；
  自测收尾还原环境与路径。
- 诚实记录：mas_ondemand 自测首轮曾把 2 条**假发车**租约写进真账本（owner `ondemand:cli`，`按需发车 脚本A/B（script）`，idx1/idx2，到期 13:32）。
  已在 11:21 用 `mas_instance_lease.release` 逐条回收（release 留历史行），回收后在租 `{}`；真账本现无本卡残留。
- MCP 面工具契约**未变**：仅新增可选参数 `lease_owner`（additive）；新租约闸门需**重启 MCP server** 才在会话里生效（既有口径）。

## 与审计接线卡 t_a190ca4b 的同文件串行（tools/mas_ondemand.py）

- 两组改动**同文件共存**，未互相覆盖：`grep` 计数 = 租约标记 10 处 / 审计标记 4 处；
- 对方 3 处 hunk 原样保留：`_unified_from_audit()`（纯旁路）、`audit()` 末尾 `safe_append(...)` 与 `stream_for_runtime(RUNTIME)`；
- 本卡的租约逻辑**全部在 `audit()` 之外**，只通过 `audit(rec)` 传新 kind（`lease_acquire`/`lease_release`）与新键（`lease_*`），旁路自动透传；
- 该文件本卡**已收口，不再动**（后续如需再改先与审计卡持卡人打招呼）。

## 边界 / 未做

- `tools/mas_dispatch.py`（24 条班次队列）不在本卡 4 个接入点内：仍走既有 busy 窗口口径；若要跨族单飞覆盖班次族，另开卡。
- 不做排队等待策略（`wait_s` 已预留但默认 0）；不做自动抢占（抢占仍须人判）。
