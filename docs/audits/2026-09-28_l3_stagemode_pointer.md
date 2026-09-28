# 审计：L3 实机回归红 —— 用户_173920298 的 StageMode 是指针（45/46）— 卡 t_a8b4ceb0

- 时间：2026-09-28 10:43–11:06（UTC+8）｜执行：mas（本机 DESKTOP-L1OEMR3）
- 现象：`tools/tests/test_regression.py --live` = `REGRESSION=FAIL (32 passed, 1 failed…)`，唯一红项
  `test_L3_live_stage_fallback_and_medicine_ledger`：`API 复读不全：45/46`；
  底层复核 `mas_stage_fix.py --verify` 稳定复现（10:32 与 10:34 两次）：
  `✗ 用户_173920298: diffs=["StageMode:'Fixed'->'bc659275-698d-4371-9f1a-ad0f4888a1b0'"]`。
- 目标：定性（残留 vs 设计如此 vs 别的），据此收敛 L3 判据或修复该号状态。
- 约束（卡原文）：只读复核即可定因；**不要在班次运行中改这个号的配置**（碰实例/配置先拿租约）。
- 证据目录：`docs/audits/2026-09-28_l3_stagemode_pointer/`（复盘脚本 + 输出 + 复核留痕）。

## 1. 结论（定性）

**两者都不是**：既不是「真残留」，也不是「该号设计如此」。它是 `mas_run_profile` 的
**在飞按需运行**（`rp_20260928_102708_-b-3`，farm_orchestrator `fo_20260928_100815_用户_173920298`，
kanban `t_73ff4db2`）按既定机制临时挂上的**计划表指针**，被 L3 的采样窗口（10:32/10:34）套住了：

1. 该 run 于 **10:27:09** 新建临时计划表 `临时-按需-20260928_1027`（`bc659275-…`）并
   `user/update StageMode=<planId>`；**10:44:34** 以 `run_end` 自行还原（`restore ok, rc 0`）。
   ⇒ 10:31–10:44 这一段，账号 StageMode=指针是**合法中间态**，L3 采到的正是它。
2. 机制出处：`docs/MAS_RUN_PROFILE.md:112` —— 「写配置默认：新建临时计划表 + `StageMode` 指针」，
   `StageMode=uuid ⇒ 读那张计划表`（`mas_run_profile.py:11` 明确写了两类合法态）。
3. 残留假设（「本该 Fixed，被谁挂了指针」）不成立：① 计划生成时（09-27 23:36:40）该号
   `before_critical.StageMode = "Fixed"`，之后唯一写它的是这次 run（台账 `actions` 有 `user/update StageMode` 记录）；
   ② `runtime/activity_switch_state.json` 不存在 ⇒ 不是 activity_switch 的未还原残留；
   ③ 10:44:34 后**自愈**：11:05 复核 `API 46/46；pending 0；bad 0`（rc=0），计划表里也没有 `临时-按需-*` 残留。
4. 「其他 45 号是不是都 Fixed」= 是：10:47:59 与 11:05:37 两次 `--verify --json` 全量 46/46 通过，
   失败集 `failures=[]`（只有该号曾经不在）。

## 2. 时间线（逐条有出处）

| 时刻 | 事实 | 出处 |
|---|---|---|
| 09-27 23:36:40 | stage_fix `plan.json` 生成（46 号；该号 `uid=bd2e68c5-5af7-4883-b316-82c18799439b`、`sid=b4f8dbf2-…`、`明日方舟-b-3`、`emu=2`、`StageMode=Fixed`） | `runtime/reports/stage_fix_20260927_233640/plan.json:49-90` |
| 09-28 10:08:18 | farm_orchestrator 起跑（resolve → preflight GO → 台账 unknown ⇒ 先 probe） | `runtime/farm_orchestrator/fo_20260928_100815_用户_173920298/steps.json` |
| 10:08:54–10:26:50 | probe run `rp_20260928_100854_-b-3`（wait_timeout 900s → 还原，restored） | `runtime/run_profile_state.json` |
| 10:27:07 | 取实例租约 `100.79.173.69:2`（TTL 3600s，到期 11:27:07，owner `t_73ff4db2`） | `runtime/instance_leases.json`；`steps.json` lease |
| **10:27:09** | 建临时计划表 `bc659275-…` + `user/update StageMode`（run `rp_20260928_102708_-b-3`） | `runtime/mas_run_profile_log.jsonl` `plan_created`；`run_profile_state.json` `actions` |
| 10:27:29 | dispatch `taskId=7da40355-…`；台账 `status=dispatched` | 同上；`mas_farm_orchestrator_log.jsonl` |
| **10:32 / 10:34** | **L3 采样**：`API 45/46`，该号 `diffs=["StageMode:'Fixed'->'bc659275-…'"]`（其余 45 号全绿） | 卡面记录 + 本轮复核 |
| 10:44:34 | run_end 还原成功（`restore ok, reason run_end, rc 0`），orchestrator 收工「整链跑通（试探→执行→还原）」 | `mas_run_profile_log.jsonl`；`mas_farm_orchestrator_log.jsonl` |
| 10:47:59 / 11:05:37 | `mas_stage_fix --verify`：`API 46/46；pending 0；bad 0`（自愈确认） | `evidence_recheck.txt` |

## 3. 结论之后做了什么（收敛 L3，而不是改该号配置）

该号无需修复（自己还原了），所以走卡的第二个选项：**把 L3 判据收敛成「两类合法态」**，
并把「中间态 vs 残留」的口径用离线用例锁住。

| # | 文件 | 改动 | 为什么 |
|---|---|---|---|
| 1 | `tools/mas_stage_fix.py` | `--verify --json`：新增 `verify_payload()`，末行打印 `STAGE_FIX_VERIFY_JSON={…}`（含 `failures[].uid/name/diffs`、`pending[]`）；日志文本一字未动；`--selftest` 加摘要形状断言 | L3 要**逐号**归因，不该去 parse 日志里的 Python dict repr；人读路径与既有调用方不受影响 |
| 2 | `tools/tests/test_regression.py` | 新增「在飞核销」判据组：`_run_profile_open_runs()`（未还原 = `status != "restored"`，口径同 `mas_run_profile.py --status` 的告警行；活体证据 = 本地锁新鲜 **或** 该实例租约在租）、`_inflight_account()`、`_excusable_diff()`、`_parse_ts/_diff_kv/_emu_set` | 「在飞」需要可判定的机读定义；纯函数便于离线锁定 |
| 3 | 同上 **L3** | 判据收敛：失败差异必须**逐键等于该在飞 run 自己声明的写入**（`StageMode`=它建的表 id / `expected.payload` 七键 / 它 `disabled_by_us` 关掉的 `Status`），且该 run 有活体证据，且指针**解引用**得到那张表；打印 `在飞核销 N 号（号←run）`；`pending` 仅对在飞号放行 | 直接对着现场：只有「声明的值」才核销，未声明差异/孤儿指针/已删表照红 |
| 4 | 同上 **L4** | 旧实现只匹配字面 `status == "open"`（台账真实态是 `prepared/written/dispatched/restored/restore_failed`）⇒ **这条闸门永远不会红**：10:32 明明有一条未还原记录却 P 了。改为「未还原 + 无活体证据 = 滞留 ⇒ FAIL」，在飞则打印提示 | 顺手的同类缺陷：判据谓词与现实词表不符（与 L3 同因） |
| 5 | `tools/tests/test_regression.py` **F3** | 新增离线用例 `test_F3_inflight_pointer_accounting`（用现场真值：uid/plan/run/时间）：声明值核销 ✓、未声明值拒绝 ✓、锁·租约两种活体 ✓、陈旧锁 ✗、孤儿 ✗、已还原 run 的指针 ✗ | 判据自身的回归闸；改口径必须显式过目（离线、零网络） |
| 6 | `docs/REGRESSION.md` | 覆盖表 L3/L4/F3、失败 triage、约束（在飞不算违规）、计数（离线 28→29、--live 33→34）、解释器坑 | 文档与判据同步，triage 才走得通 |
| 7 | `docs/audits/2026-09-28_l3_stagemode_pointer/` | `replay_incident.py` + `.txt`（现场重放）、`evidence_recheck.txt`（复核留痕） | 「把 10:34 的采样窗口重放给收敛后的判据」可复跑 |

**核销口径的边界**（避免变成万能放行）：只认「该 run 声明的值」+「有活体证据」两条同时成立；
崩掉/被 kill 的 run（锁陈旧 + 租约过期）= 滞留 ⇒ 指针没人认领 ⇒ 照红（含指针指向已删表的场景）。

## 4. 证据表（全部本轮真跑）

| 命令 | 结果 |
|---|---|
| `python tools/mas_stage_fix.py --selftest` | `SELFTEST=OK`（含新加的 `--json` 形状断言） |
| `python tools/mas_stage_fix.py --verify --json` | `API 46/46 通过；pending 0；bad 0` + `STAGE_FIX_VERIFY_JSON={"ok": true, "failures": [], …}`，rc=0 |
| `PY312 tools/tests/test_regression.py` | `REGRESSION=PASS (29 passed, 0 failed, 5 skipped, 25.0s) [offline]` |
| `PY312 tools/tests/test_regression.py --live` | `REGRESSION=PASS (34 passed, 0 failed, 0 skipped, 37.1s) [live]`（`docs/audits/2026-09-28_regression_run.{txt,json}` 已刷新；期间一次 F1 红是并行卡正在重写 `mas_ondemand.py`，落地后复跑即绿） |
| `PY312 docs/audits/2026-09-28_l3_stagemode_pointer/replay_incident.py` | ① 10:34 窗口（`status=dispatched`+租约在租）⇒ **PASS（核销，认领 run=rp_20260928_102708_-b-3）**；② 已还原仍挂指针 ⇒ **FAIL（真违例）** |
| `PY312 tools/mas_run_profile.py --status` / `--list-plans` | 4 条台账全部 `restored`；计划表 5 张，无 `临时-按需-*` 残留（临时表已在还原时删净） |

## 5. 边界与遗留

- **全程只读**：未发车、未改任何账号/远端配置、未碰实例（别人的租约没动）、未跑 `--apply`/`--rollback`。
  「改该号配置」这条卡面禁令没必要触发 —— 该号 10:44:34 已自行还原。
- 未做（不必做）：没动 `bc659275-…` 计划表（run 自己删了）、没改 MAA/AUTO-MAS 侧任何文件。
- 观察（非本卡产物，留给 lead 判断）：
  1. `tools/mas_run_profile.py` 在 10:58 被外部改动（本卡未改它）；用 **Hermes venv 的 3.14** 跑套件会让
     B6 `lease_preflight_env_passthrough` 假红（`pick_python()` 探测被自检的 subprocess 桩记成首条 argv），
     用文档口径的 **Python312 3.12.10** 全绿 —— 已写进 `docs/REGRESSION.md` 顶部解释器说明。
  2. L3 的「采样窗口撞在飞运行」这类噪声从此被显式核销并打印；但**发车高峰**同时有多条 run 时，
     L3/L4 仍会打印提示行 —— 属预期，不阻塞扫 `REGRESSION=` 末行。
