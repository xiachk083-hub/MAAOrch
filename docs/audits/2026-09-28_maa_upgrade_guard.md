# MAA 升级护栏：实机验证与基线重建（2026-09-28）

> 对应 kanban `t_94c7e90e`（「X3 快照」工作面的升级护栏交付）。工具：`tools/maa_upgrade_check.py`；方法文档：`docs/MAA_UPGRADE_GUARD.md`。

## 1. 本次做了什么

| # | 动作 | 结果 |
|---|---|---|
| 1 | 修 live 路径掩码期望 bug（`fetch_users` 缺 `Info.Id`；`expect_masks` 纯函数） | `tools/mas_ondemand.py` +`id`；`maa_upgrade_check.expect_masks` 生效（实机 `expect_masks=["185****3616"]`） |
| 2 | 修 stop 判定假阴性（stop 的 `code=200` 应答不带 `taskId`，被 start 口径误判 `no_taskid`） | 新增 `_stop_result()`，与 `mas_ondemand.cmd_stop` 口径一致；实机复验 `判定 OK` |
| 3 | 防陈旧切号行冒充（发车前快照已有 `账号切换 -->>` 行，只认新增行） | `_switch_lines` + `_find_switch_line(exclude=…)`；selftest 覆盖 |
| 4 | LOCK 分支 fail-closed（并发 live 拒绝时判 `BLOCKED`，不再放行） | selftest 覆盖 |
| 5 | 识别 AUTO-MAS per-run 注入噪声（`BilibiliAgreement_AUTO`） | `diff_tasks` 增 `benign` 标记 + 报告注记；selftest 覆盖 |
| 6 | 把 `--selftest` 锁进回归套件 | `tools/tests/test_regression.py` F1 增 `maa_upgrade_check: rc=0` |

## 2. 验证证据（全绿）

- `python tools/maa_upgrade_check.py --selftest` ⇒ **selftest: OK (35/35)**（含实机回归项）。
- `python tools/tests/test_regression.py` ⇒ **REGRESSION=PASS (28 passed, 0 failed, 5 skipped)**，F1 自检明细含 `"maa_upgrade_check": "rc=0"`。
- 邻接工具：`mas_ondemand --selftest`（在 F1 内）rc=0。

## 3. 实机 live 验证（注入 → 读回）

**目标**：`明日方舟-官-23` @ `E:\MAA-Multi\MAA_1`（单官服用户，窗口外，槽位空闲）。

- 第 1 次（修复前）：读回成功，但 stop 判定假阴性 `STOP-FAILED` → 见 §1-2 修复。
- 第 2 次（修复后，`runtime/upgrade_guard/live_guan23_run2.json`）：
  - gates：REST ok / WINDOW 窗口外 / IDLE 无 MAA.exe；
  - 读回成功：`StartUp=185****3616/true`；`Fight#0 stagePlan=['SR-7']`（列表含 `1-7`；吃药 `useMedicine=false, medicineCount=0`，与配置一致）；
  - stop：`{"code":200,"status":"success","message":"操作成功"}` ⇒ **判定 OK**；
  - 判定 `PARTIAL`（gui.log 未见「账号切换 -->>」新增行）。
- **影响面**：发车 10:17:41 → stop 10:17:53 → 任务结束 10:18:20；app.log 无 `maa_launch` ⇒ MAA 未及启动即被官方 stop 收尾，账号无实际消耗；模拟器 11 收尾关闭。**这是设计内的最快闭环**：注入写在 MAA 启动前。
- PARTIAL 解读（写进 §6 文档）：同号重登不产生切换行 / stop 早于 MAA 启动，均属常见；如要「见行」级证据，需让 MAA 跑完整 StartUp，不值得为此额外扰动账号。

## 4. 基线 / verify（当前事实）

- 基线：`runtime/upgrade_guard/20260928_101942`（12 槽，均 `v6.18.0+23feeddc538de4e10646b6`；核 sha `eff5c522`；资源 sha：11 槽 `38e897ce`，MAA_9 `7b172da9`）。
- verify：`runtime/upgrade_guard/20260928_102347_verify/report.md` ⇒ 程序/资源无变化；`VIOLATION` 仅事件面 `MAA_10,MAA_12: official-switch-not-true`（非升级）。
- **新发现（已入工具）**：MAA_9 的资源 sha 与其它 11 槽不同 = AUTO-MAS `agree_bilibili` 每轮对 `tasks.json` 增删 `BilibiliAgreement_AUTO` 节点所致（B 服脚本在跑期间存在，收尾删除）。差异=+1 节点，工具标 `benign`；已在 §3 D2 与报告注记中说明。

## 5. 交付物

- `docs/MAA_UPGRADE_GUARD.md` —— SOP（升级前/后三步）+ §3 漂移处置表 + §4 回滚操作卡（含实数据：单槽 610–854 MB、最小集 ≈265 MB/槽、`E:` 剩 150,832 MB、robocopy 命令、验证口径）+ §5 报告读法 + §6 工具面。
- `docs/audits/2026-09-28_maa_6168_to_6180_tasks_diff.md` —— 跨大版本差异样例（+224/−3，关注节点全在 ⇒ 新能力型）。
- `runtime/upgrade_guard/live_guan23_run2.json` —— live 实机证据原件。
- 配置面快照（X3 工具，升级前步骤的实物）：`SNAPSHOT=OK ts=20260928_103857__pre-maa-upgrade`（684 文件 / 8.2 MB；主机 store `E:\AUTO-MAS\backups\cfgsnap_20260928_103857__pre-maa-upgrade`）。
- 回滚数据补充：`E:\MAA-Multi\backups\` 待建（母版与 12 槽全量镜像 ≈11.4 GB；`E:` 余量充足）；配置/补丁/定时侧回滚沿用 `docs/audits/2026-09-28_rollback_drill.txt` 已验路径。

## 6. 未尽事项

- 未做「真升级」演练（本日无新版本发布；升级后三步已在 live/verify 上按同一路径实测）。
- 建议接线（未接线）：30 分钟巡检加只读位 `maa_upgrade_check verify`。
