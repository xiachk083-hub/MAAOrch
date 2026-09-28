# 接线复核（run 2 · P1-2 改动之后重跑）

日期：2026-09-28 11:2x–11:3x ｜ 卡：`t_a190ca4b`（首跑 11:23 迭代预算耗尽 → 本回复核并收尾）
口径与首跑一致：schema `maaorch.audit.v1`、流 `runtime/audit/mas_audit.jsonl`；**只复核、不删不改既有行**。

## 一、总结论

- 11 个工具的接线在 **P1-2（t_1df99ae5）改动之后**仍全部在位、可用；
- 全部自检/回归/映射单测/隔离 harness **重跑全绿**；
- 生产统一流**零污染**（新增行全部可归因：patch_guard 沙箱自测 2 行 + 两次真写路径各 1 行）；
- 首跑遗留的唯一未完成项（§8.2「落点」列逐行标注 + 证据归档）本回已补齐。

## 二、复核命令与结果（文件 = 本目录）

| # | 项 | 命令 | 结果 | 文件 |
|---|---|---|---|---|
| 1 | 工具自检 ×12 套 | `$PY312 tools/<tool>.py --selftest`（10 工具）+ `mas_audit` + `mas_patch_guard_selftest.py` | 全 `OK`（19/19、36/36、23/23、24/24、37/37、26/26、36/36、35/35、13/13、stage_fix OK、patch_guard 沙箱 OK）；流 `1033→1035`（+2 为 patch_guard 沙箱自测按设计落账） | `selftests2.txt` |
| 2 | 回归（offline） | `$PY312 tools/tests/test_regression.py` | `REGRESSION=PASS (29 passed, 0 failed, 5 skipped, 23.6s) [offline]`；流 `1035→1035` | `regression2.txt` |
| 3 | 隔离 harness（6 工具真代码路径） | `$PY312 docs/audits/2026-09-28_audit_wire/harness_isolated.py` | 流 `1035→1035`；隔离流 10 行（probe_farm ×4 / dispatch ×1 / stage.fix ×1 / patch ×1 / watch ×2 / activity_switch ×1） | `harness2.txt`（产物覆盖 `iso/`） |
| 4 | 台账映射单测 | `$PY312 docs/audits/2026-09-28_audit_wire/map_unit.py` | `MAP_UNIT=OK bad=0`（12 例，含 `run_profile_refused`→`profile.refuse`、`lease_done` 家族兜底） | `map_unit2.txt` |
| 5 | watch 真跑 | `$PY312 tools/mas_watch.py --force --quiet --no-rerun` | 11:31:36 → 统一流 +1（`run.watch`·kind=report）+旧流 `mas_ledger` `49→50`；`--quiet` 无通知、`--no-rerun` 不补跑；fetch 侧脚本 `E:/_mas_watch_data.py` 只读 | `watch_real.txt` |
| 6 | run_profile dry-run | `$PY312 tools/mas_run_profile.py --profile runtime/templates/run_profile.example.json` | 11:34:06 → 统一流 +1（`profile.plan`）+旧流 `mas_run_profile_log` `64→65`；'[dry-run] 未触达任何写请求' | `run_profile_real.txt` |
| 7 | 分域查询 | `$PY312 tools/mas_audit.py query --since 6h --action '<域>.*' --limit 6` × 8 域 | profile/farm/dispatch/stage/config/patch/run/manual 均有行 | `query2.txt` |
| 8 | 最终校验 | `$PY312 tools/mas_audit.py validate` + `status --json` + 旧流 9 件行数 | `AUDIT_VALIDATE=OK files=1 lines=1037 bad=0`；`AUDIT_STATUS=OK` | `validate2.txt` |

## 三、逐工具「最后统一流行 vs 文件 mtime」核对（防 P1-2 改动后失效）

| 工具 | 文件 mtime | 最后统一流行 | 判读 |
|---|---|---|---|
| mas_ondemand.py | 11:07:35（P1-2 改） | 11:09:59（`dispatch.cleanup_ok`） | 行晚于改动 ✓ |
| mas_run_profile.py | 11:17:57（P1-2 改） | **11:34:06（本复核重跑）** | 已刷新 ✓ |
| mas_watch.py | 10:59:00 | **11:31:36** | 已刷新 ✓ |
| mas_patch_guard.py | 10:59:01 | 11:29:20 | ✓ |
| mas_config_snapshot.py | 10:59:04 | 11:09:16 | ✓ |
| mas_probe_farm.py | 10:58:38 | 11:09:12 | ✓ |
| mas_farm_orchestrator.py | 11:00:46 | 11:04:08 | ✓ |
| mas_plan_builder.py | 10:58:37 | 11:04:44 | ✓ |
| mas_dispatch.py | 10:58:37 | 10:58:23（早 14s——接线收口的同窗口保存） | 文件其后未再改；接线由 **11:29 隔离 harness 真 CLI**（`--release --apply`，临时 state → `dispatch.busy_release`）复核通过；下轮真发车（19:xx cron `2600f6415f16`）自然补生产行 |
| mas_stage_fix.py | 10:59:01 | 仅隔离（harness `stage.fix`） | 真跑会改 54 号配置 ⇒ 维持隔离取证（同首跑口径），`--selftest` 全绿 |
| activity_switch.py | 11:07:35（P1-2 文件） | 仅隔离（harness `account.activity_switch`） | 真跑会切账号/活动 ⇒ 维持隔离取证，`--selftest` 13/13 |

## 四、历史兜底行说明（不重写）

统一流中 4 条 `tool:ledger / ledger.run_profile_refused`（10:51–10:58）早于精确表落地时间（11:17 起
`run_profile_refused`→`tool:mas_run_profile`/`profile.refuse`）。按审计红线**保留原样**；此后同类事件
已归精确表（家族前缀兜底仍在，见 `map_unit2.txt` 末例）。

## 五、文件指纹（复核结论 pinned 到修订）

复核时点指纹全表见 `file_hashes.txt`（sha256 前 16 位 + mtime）。其中两处需注意：

- `mas_run_profile.py` 在复核期间被兄弟卡（P1-2 t_1df99ae5）**继续编辑**（11:37:41 落盘，
  sha `aa741331082cc980`）。其上接线四处（`_unified_from_audit` `:246` / `_unified_from_ledger` `:277` /
  两个 `_unified_guard` 钩子 `:319`/`:342`）仍在位，并以该修订**复跑 dry-run 通过**：
  11:38:01 → `profile.plan`（流 1037→1038、旧流 65→66）——即本目录两条 `run_profile_real` 证据
  分属 11:34 版与 11:37 版，结论一致。
- 其余 10 工具自复核起未再改动（mtime 均 ≤ 11:22）。
- ⚠ hotspot：`tools/mas_run_profile.py`、`tools/mas_ondemand.py` 为本卡与 P1-2 共写文件；
  审计侧钩子已按卡上评论收口（只加旁路，不再动）。
