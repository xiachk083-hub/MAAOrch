# kanban_health → mas-observe MCP 接线（卡 t_4cd871de）· 2026-09-28

**一句话**：只读工具 `mas_kanban_health` 已实现并经 MCP 面探针（15/15）+ 活体网关实调（真值）验证；
巡检 prompt 的 `KANBAN_HEALTH=` 接线按 lead 11:10 协调由集成 owner **t_403cd83b** 收口为 v4——本卡不再自行 apply，
本目录 = 本卡验收证据 + 交班说明 + 复核脚本。

## 1. 验收状态（本卡 scope：实现 + 只读探针 + 工具注册证据）

| 项 | 状态 | 证据 |
|---|---|---|
| MCP 工具 `mas_kanban_health`（schema/impl/dispatch/--check 全齐） | ✅ | `12_server_check.txt`：`tools(9)=…mas_kanban_health…` + 全 CLI 自检 OK + `readonly_assert=PASS` |
| MCP 面探针（真实 stdio 协议 15 例） | ✅ 15/15 | `10_mcp_probe.txt` |
| CLI 面探针（line/json/错板/双板/selftest 45/45） | ✅ | `11_cli_probes.txt` |
| 工具注册可见 + 关→开刷新后实调 | ✅ | `13_live_call.json`：ok=true / status=CRIT / 0.8s；config `enabled=true`；11:17-18 服务重起日志 |
| 巡检 prompt 接线（v4） | ✅ 由 t_403cd83b 收口 | 活体快照 `live_prompt_now.txt`（7246 chars / sha16 `9b41eaa33303614e`）= 现场九检第 9 项 + 主表 16 行 |
| 关键文件哈希 | ✅ 4/4 | `14_hashes.txt`（sha256sum -c 通过） |

## 2. 单一真源与接口契约（MCP 层只搬运，不重算）

* 真源：`tools/kanban_health.py`（`--line`/`--json`/`--no-write`；这两条路径 rc=0=出判据、rc=4=没扫到板）。
* MCP：`mas_kanban_health(board?: "slug[,slug]", json?: bool)` →
  `{ok, line, status, counts{boards…P3}, exit_code, elapsed_s}`；`json=true` 另附 `json`（`schema=maaorch.kanban_health/1`）与 `totals`。
  失败 `{ok:false, error:{kind,detail}}`：`bad_args|parse_error|timeout|unknown`。
* 只读硬闸：子进程只允许 `--line/--json/--no-write/--board`；`--check` 正则断言源码无写端点字样；**不引 `hermes kanban`**。
* 子进程纪律：`stdin=DEVNULL`、`_env()`（大写键合并 + OpenSSH PATH 前置）、`timeout=60s`（`mas_observe_mcp.py::_run`）。

## 3. 与 v4 prompt 的差异（交 lead/集成方裁定；本卡按协调未改 prompt）

卡原文要求「WARN/ALERT 时列出报告里的 P1/P2 标题（不要全文粘）」；v4 第 9 项为「调 1 次、别重试」+ 指向
docs/KANBAN_HEALTH.md，未包含 `json=True` 取明细步骤。若需严格式，仅需在第 9 项加一句：

> status 非 OK（WARN/CRIT）⇒ 再调 1 次 `mas_kanban_health(json=True)`，证据行列 P1/P2 标题 ≤4 条（只列标题，不粘全文）。

该能力已探针验证（④ json 结构用例 + `json_peek.py` 实测：schema/status/line/totals/findings 一致）。

**落地片段（未应用）**：`PROMPT_PATCH_kanban_health_p1p2.md` —— 含 R1（重复上限表）/ R2（第 9 项）两条精确 old→new 替换串 + 应用/回滚口径；合并方 = t_403cd83b（或由 lead 裁定作废）。

## 4. 崩溃与重跑核查（lead 指派）

* **run#216（本卡上一跑，crashed ≈11:00:28）**：exit code 1；同窗口 `provider=opencode-go` 密集
  HTTP 402「Insufficient account funds」（not retryable，10:33→11:22 持续；**10:57 的 cron 巡检轮同因失败**）。
  定性：**provider 侧故障类，非本卡代码缺陷**（工件全部幸存，本目录即其留档+复核）。
* **run#223（当前重跑）**：§1 全部验收点通过。
* **t_403cd83b**：无崩溃记录（task_runs 仅 run#213 存活，`last_failure_error=NULL`）；其收口声明经本卡独立复核一致
  （prompt sha `9b41eaa3…`、tools(9)、config `enabled=true`、mcp-stderr 11:18 重起）。
* ⚠ **运维提示（持续中）**：opencode-go 402 仍有新例（最新 11:22:57），落到该 provider 的 worker 会成批崩（与 09:40 crash batch 同家族）；
  建议统筹充值/改派该 provider。看板同期 `fail` 计数在涨（11:25 行：fail=5, P2=9）。

## 5. 复现 / 下一轮验收

```bash
PY312=C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe
cd D:/1-2/MAAOrch
$PY312 tests/kanban_health_mcp_probe.py        # PROBE: OK (15/15)
$PY312 tools/mas_observe_mcp.py --check        # tools(9) + selftest + readonly_assert=PASS
$PY312 tools/kanban_health.py --line --no-write
# 巡检端到端（下一轮）：报告主表应含 KANBAN_HEALTH= 行
ls "$LOCALAPPDATA/hermes/profiles/mas/cron/output/52b88b0ffca0/"
```

## 6. 文件索引

**当前（证据/复核）**：`10_mcp_probe.txt`、`11_cli_probes.txt`、`12_server_check.txt`、`13_live_call.json`、
`14_hashes.txt`、`live_prompt_now.txt`（v4 活体快照）、`live_prompt_1112snapshot.txt`（v3 快照，audit 版）、
`live_vs_after.diff`（旧 rebase 目标 vs v4 差异）、`verify_prompt_state.py`/`dump_live_prompt.py`/`dump_runs.py`/`dump_runs2.py`/`json_peek.py`（复核脚本）。

**历史（已被 t_403cd83b 集成取代，保留溯源，勿再 apply）**：`apply_prompt_edit.py`（首版 7 处替换）、
`apply_prompt_rebase.py`（rebase 提案脚本；干跑即发现活体 prompt 已被集成 ⇒ 命中数 0，见 `20_prompt_rebase_dryrun.txt`）、
`job_prompt_before.txt`/`job_prompt_after.txt`/`job_prompt_diff.txt`（首版留档）。
