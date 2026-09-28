# 统一审计流接线验收（t_a190ca4b · 双写期）

日期：2026-09-28 ｜ 卡：`t_a190ca4b` ｜ schema：`maaorch.audit.v1` ｜ 流：`runtime/audit/mas_audit.jsonl`

一句话：**11 个 MAAOrch 工具的旧审计/台账写出口全部旁路接进统一流，旧流写出行为未改**；
审计写入失败一律静默（`mas_audit.safe_append` + 各工具 `_unified_guard` 兜底），绝不打断主流程。

## 1. 接线总表（工具 → 落点 → 统一流动作 → 取证）

| # | 工具（源文件） | 旧流落点 | 统一流动作（actor） | 本次取证 | 结果 |
|---|---|---|---|---|---|
| 1 | `mas_run_profile.py` | `mas_run_profile_log.jsonl` + `mas_ledger.jsonl` | `profile.*`（tool:mas_run_profile） | **生产 dry-run**：`--profile runtime/templates/run_profile.example.json`（脚本 明日方舟-官-12 / 烤箱）11:03:02 → `profile.plan`；旧流 62→63 | ✓ |
| 2 | `mas_probe_farm.py` | `mas_probe_farm_log.jsonl` + `mas_ledger.jsonl` | `farm.*` / `stage.ledger_update`（tool:mas_probe_farm） | **生产 apply 拒绝路径**：`--account <不存在的号> --apply` 11:09:12 → `farm.refused`+`farm.refuse`，旧流 9→10、台账 47→48；另隔离 harness 4 行（含 `farm.probe`/`farm.done`） | ✓ |
| 3 | `mas_farm_orchestrator.py` | `mas_farm_orchestrator_log.jsonl` + `mas_ledger.jsonl` | `farm.<event>`（tool:mas_farm_orchestrator） | **生产 dry-run**：`--account 烤箱 --stage 1-7` 11:04:08 → `farm.done`+`farm.run`，旧流 4→5、台账 +1 | ✓ |
| 4 | `mas_plan_builder.py` | `mas_plan_builder_log.jsonl` | `stage.<event>`（tool:mas_plan_builder） | **生产 dry-run**：`--account y9Flipped --exec-dry-run` 11:04:43-44 → `stage.plan_built`/`stage.exec_dry_run`/`stage.exec_done` | ✓ |
| 5 | `mas_dispatch.py` | `mas_dispatch_log.jsonl`（双侧） | `dispatch.<event>`（tool:mas_dispatch） | **隔离 harness** `--release --apply`（临时 state）→ `dispatch.busy_release`；生产侧另有其它 worker 触发行（10:58 起） | ✓ |
| 6 | `mas_ondemand.py` | `mas_ondemand_log.jsonl`（LOG + LOG_CALLS） | `dispatch.<event>`（tool:mas_ondemand） | **生产 dry-run**：`--account 烤箱` 11:05:29 → `dispatch.refuse`（preflight_no-go，真拒绝）；旧流 LOG_CALLS +1 | ✓ |
| 7 | `mas_config_snapshot.py` | `mas_config_snapshot_log.jsonl` | `config.<action>`（tool:mas_config_snapshot） | **生产 `--drift`** 11:05:55 → `config.drift`；旧流 33→34 | ✓ |
| 8 | `mas_patch_guard.py` | `mas_patch_guard_log.jsonl` | `patch.<kind>`（tool:mas_patch_guard） | **生产**：他人回滚演练 11:00-11:12 自然落 `patch.reapply`；另隔离 harness 1 行 | ✓ |
| 9 | `mas_watch.py` | `mas_ledger.jsonl`（watch/watch_report） | `run.watch`（tool:mas_watch） | **隔离 harness**：`fetch_error` + `rerun` → 2 行（真跑会 SSH 上宿主 + 可能外发通知，故不真触发） | ✓ |
| 10 | `mas_stage_fix.py` | `mas_ledger.jsonl`（stage_fix） | `stage.fix`（tool:mas_stage_fix） | **隔离 harness** 1 行（真跑会改 54 号配置，只在明确要求时执行） | ✓ |
| 11 | `activity_switch.py` | `mas_ledger.jsonl`（activity_switch/restore） | `account.activity_switch`（tool:activity_switch） | **隔离 harness** 1 行（真跑会切账号/改活动） | ✓ |

共享写者说明：`mas_ledger.jsonl` 由 run_profile / probe_farm / farm_orchestrator / activity_switch 共用，
统一流按 `kind` 分派 actor/action（`tools/mas_run_profile.py::_LEDGER_ACTOR_ACTION` + 家族前缀兜底），
与桥接口径 `tools/mas_audit.py::_ad_ledger` 同表；未知 kind 落 `tool:ledger` / `ledger.<kind>`。

## 2. 验收命令与结果

```bash
PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"
# ① 统一流查询（生产，各域都有行）
$PY312 tools/mas_audit.py query --since 3h --action 'profile.*'   --limit 8   # → 见 query_prod.txt
$PY312 tools/mas_audit.py query --since 3h --action 'farm.*'      --limit 8
$PY312 tools/mas_audit.py query --since 3h --action 'dispatch.*'  --limit 8
$PY312 tools/mas_audit.py query --since 3h --action 'stage.*'     --limit 8
$PY312 tools/mas_audit.py query --since 3h --action 'config.*'    --limit 8
$PY312 tools/mas_audit.py query --since 3h --action 'patch.*'     --limit 8
$PY312 tools/mas_audit.py validate                                 # AUDIT_VALIDATE=OK files=1 lines=1033 bad=0
# ② 旧流仍在长（双写）：见 §1 各行的「旧流 x→y」
# ③ 工具自检 + 回归：docs/audits/2026-09-28_audit_wire/selftests.txt
#    REGRESSION=PASS (29 passed, 0 failed, 5 skipped, 25.3s) [offline]
# ④ 隔离 harness（6 工具真代码路径）+ 映射单测
$PY312 docs/audits/2026-09-28_audit_wire/harness_isolated.py      # → harness_isolated.txt
$PY312 docs/audits/2026-09-28_audit_wire/map_unit.py              # → MAP_UNIT=OK bad=0
```

## 3. 自测 / 生产流隔离（实测）

- 新增 `mas_audit.stream_for_runtime(runtime_dir)`：工具 runtime 被 `MAAORCH_*_RUNTIME` 重定向
  （自测/异机语义）时，统一流跟随到 `<runtime>/audit/mas_audit.jsonl`，**不再灌生产流**。
- 实测零污染：harness/映射单测跑前跑后生产流 `1015→1015`；9 个工具 `--selftest` 连跑 `1028→1028`；
  回归套件连跑 `1032→1032`（其间新增行均为其它 worker 的真实动作）。
- 例外（工具既有设计，非本次引入）：`mas_patch_guard --selftest` 按设计对宿主
  `E:\AUTO-MAS\_patchguard_selftest\` 真写补丁并落账（旧流同款），统一流会看到这 2 行 —— 目标路径自带
  `_patchguard_selftest` 可辨识。

## 4. 诚实披露：接线窗口内的自测事件（不删行）

隔离补丁生效**之前**（约 10:47–11:06），工具 `--selftest`/回归套件把约 900 行自测事件写进了生产统一流
（`actor` 多为 `tool:mas_run_profile`，`evidence` 指向 `%TEMP%\mas_*_selftest_*`）。
按审计红线（不删不改既有行）保留原样，并在流里补了一条更正事件：
`2026-09-28 11:19:55 worker:mas manual.note`（本目录 README 为证据）。
**读生产事件时按 `evidence` 路径过滤自测行。**

## 5. 本目录文件

| 文件 | 内容 |
|---|---|
| `README.md` | 本验收报告 |
| `harness_isolated.py` / `.txt` | 6 工具隔离取证 harness + 运行输出（10 行统一流 + 旧流） |
| `iso/` | 隔离产物：`mas_audit.iso.jsonl`(10)、`mas_ledger.jsonl`(5)、`mas_probe_farm_log.jsonl`(3)、`mas_dispatch_log.jsonl`(1)、`mas_patch_guard_log.jsonl`(1) |
| `map_unit.py` / `.txt` | 台账 kind → actor/action 映射单测（12 例，含 P1-2 新 kind `run_profile_refused`；MAP_UNIT=OK bad=0） |
| `selftests.txt` | 9 工具 `--selftest` + `mas_patch_guard_selftest` + 回归套件原始输出（全绿） |
| `query_prod.txt` | 生产统一流 6 个域的 query 输出（各 8 条） |
| `plans_wire_accept/` | `mas_plan_builder --exec-dry-run` 产出的计划样本（y9Flipped） |
| `recheck2/` | 复核（run 2，P1-2 改动之后重跑）：索引见 `recheck2/README.md`——`selftests2` / `harness2` / `map_unit2` / `regression2` / `watch_real` / `run_profile_real` / `query2` / `validate2`（全绿、生产流零污染） |

## 6. 与 P1-2（t_1df99ae5）的边界

`tools/mas_ondemand.py` / `tools/mas_run_profile.py` 两文件双方都在改：
- 审计侧只加「旁路 + 映射表 + 两行钩子」，**已收口**（见卡上 11:1x 评论）；
- P1-2 新 kind（如 `run_profile_refused`）已进精确表；后续新 kind 由家族前缀兜底接住，
  不再需要动审计代码。

## 7. 复核（run 2，同日 11:2x–11:3x，在 P1-2 改动之后重跑）

首跑随卡迭代预算耗尽于 11:23 中断；本回逐项复核并补齐 §8.2 逐行标注，结论未变：

- **工具自检 ×12 套全绿**（流 1033→1035，+2 = patch_guard 沙箱自测按设计落账）；
- **回归** `REGRESSION=PASS (29 passed, 0 failed, 5 skipped)`（流 1035→1035）；
- **隔离 harness 重跑**（6 工具真代码路径）隔离流 10 行、**生产流零污染**（1035→1035）；
- **映射单测** `MAP_UNIT=OK bad=0`；
- **真写路径刷新**：`mas_watch --force --quiet --no-rerun` 11:31:36 → `run.watch`（旧流 49→50）；
  `mas_run_profile` dry-run 11:34:06 → `profile.plan`（旧流 64→65）；
- **最终校验** `AUDIT_VALIDATE=OK files=1 lines=1037 bad=0`。

逐工具「mtime vs 最后统一流行」核对、`mas_dispatch` 生产行时差说明、历史兜底行（4 条
`tool:ledger/run_profile_refused`）判读 —— 全部细节见 `recheck2/README.md`。
