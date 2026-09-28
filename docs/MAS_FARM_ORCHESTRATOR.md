# 端到端刷关编排器 · `tools/mas_farm_orchestrator.py` —— 一条命令跑通「决策 → 试探 → 执行 → 还原」

> 首跑实测：2026-09-28 用户_173920298 × LS-6（实例 2 / 明日方舟-b-3），`rc=0 整链跑通`。
> 逐条证据见 `docs/audits/2026-09-28_farm_orchestrator_rehearsal.md`；本页是用法与判据规范。

## 0. TL;DR

```
$PY312 tools/mas_farm_orchestrator.py --account <号> --stage <关>                    # 干跑（零写）
$PY312 tools/mas_farm_orchestrator.py --account <号> --stage <关> --times 1 --apply   # 真跑（一条链）
$PY312 tools/mas_farm_orchestrator.py --status                                      # 只读：最近运行 + 锁
$PY312 tools/mas_farm_orchestrator.py --selftest                                    # 离线自测（零触网）
```

* 干跑 = 走完「解析→闸门→台账→决策→计划→预览→查租」，**不发车、不写任何配置**，末尾一句结论。
* 真跑 = 干跑全过之后继续：试探（如需要）→ 取租 → `mas_run_profile --apply` → 读回 → 还原复核 → 释放租约 → 报告。
* **失败语义**：任一步失败 ⇒ 立即停 + 还原 + 照实报告，**绝不自动重试**。

## 1. 定位（它只做「串 + 判 + 报告」，每个环节仍是各自工具的权威）

| 环节 | 权威工具 | 本工具只做 |
|---|---|---|
| 班前闸门 | `mas_preflight.py` | 取 `PREFLIGHT=GO/NO-GO`，NO-GO 立即停 |
| 能力台账 | `stage_capability.py` | 读 `can_farm` 三态（success/failed/unknown） |
| 试探 | `mas_probe_farm.py` | `--probe-only` 跑 1 场，读判定（不改判定口径） |
| 执行/读回/还原 | `mas_run_profile.py` | 生成 profile 交它执行，读它的 L1/L2 与 run 状态 |
| 实例互斥 | `mas_instance_lease.py` | 事前查租（被占即停）、真跑取租、终局释放 |
| 审计 | `mas_ledger.jsonl` / 本工具 jsonl | 每次运行各写一行（含 owner） |

不做：不改 MAA/AUTO-MAS 代码；不重启侧车；不建队列（那是 `mas_ondemand` / `mas_dispatch` 的事）；不支持剿灭。

## 2. 前置与闸门（发车前会被逐条检查）

1. 环境：`MAAORCH_MAS_API` / `MAAORCH_SSH_KEY` / `MAAORCH_SSH_HOST`（同其它 `mas_*` 工具）；解释器 `Python312`（可用 `MAAORCH_FARM_ORCH_PYTHON` 覆盖）。
2. 目标解析：`--account` 必须**唯一**命中一个脚本；多号脚本默认 fail-closed（要 `--disable-others` 或 `--all-accounts` 显式表态）。
3. 关卡：只走 **7 键路线**（`Stage/Stage_1..3/SeriesNumb/MedicineNumb/Stage_Remain`）；`Annihilation/剿灭` 直接拒绝（它不是这 7 键）。
4. 闸门：`MAAORCH_FARM_ORCH_NO_GO=1` 一票否决；另沿用子工具的 `MAAORCH_PROBE_FARM_NO_GO` / `MAAORCH_DISPATCH_NO_GO` / `MAAORCH_RUN_PROFILE_NO_GO`。
5. 单飞：`runtime/mas_farm_orchestrator.lock`（stale 6h 可破）。
6. 租约：事前 `lease_check`（**被占 ⇒ 停，零写**）；真跑在「执行」前 `lease_acquire`（TTL 3600s，`owner` 记 `--owner`，默认 `$HERMES_KANBAN_TASK`）；**任何终局路径都会释放**（见 §7 修复记录）。

## 3. 参数表

| 参数 | 默认 | 说明 |
|---|---|---|
| `--account` / `--stage` | 必填 | 账号名（与 AUTO-MAS 一致）/ 关卡码（`1-7`、`LS-6`、`CE-6`…） |
| `--times N` | `1` | **连战倍数**（= `SeriesNumb`，取值 `0..6/-1`），**不是**总次数上限 |
| `--medicine N` | `0` | 理智药额度（0 = 不吃） |
| `--script` | 自动解析 | 目标脚本名/uid；必须唯一命中 |
| `--force-probe` | 关 | 台账 success 也先试探一次 |
| `--retry-after-fix 说明` | — | 台账 failed 时放行（说明进审计；与 probe_farm 同口径） |
| `--remain-sanity` | 关 | 保留「剩余理智」任务（默认关 = 不保留） |
| `--timeout` | `1500` | **试探**窗口秒数（冷启动+完整日常链实测 16~18 分钟才打完第一场，故默认给足；想快停可显式调小，见 §8） |
| `--farm-max-wait` | `10800` | **执行**最长等待秒数（等任务自然结束） |
| `--write-mode {plan,fixed}` | `plan` | 与 run_profile 同口径：`plan`=临时表+StageMode 指针；`fixed`=直接改 |
| `--disable-others` / `--all-accounts` | 关 | 多号脚本的显式表态 |
| `--owner` | `$HERMES_KANBAN_TASK` 或 `farm_orchestrator` | 租约/审计归属 |
| `--apply` | 关 | 默认 dry-run；真跑必须显式 `--apply` |
| `--status` / `--tail N` | — | 只读：最近运行 + 锁状态 |
| `--json` / `--selftest` | — | 单行 JSON / 离线自测 |

## 4. 流程（12 步，全部落 `run 目录/steps.json`）

| # | 步骤 | 做什么 | 失败 ⇒ |
|---|---|---|---|
| 1 | `resolve` | 账号 → 脚本（唯一）/目录/实例 | rc 2 |
| 2 | `preflight` | 子工具闸门（GO/NO-GO） | rc 2 |
| 3 | `lease_check` | 实例租约是否被人占 | rc 2 |
| 4 | `ledger` | 台账三态查询 | rc 2 |
| 5 | `decision` | success⇒直刷 / failed⇒拒 / unknown⇒先试探 | rc 2/3 |
| 6 | `probe` | `mas_probe_farm --probe-only`（1 场，含还原+判据） | rc 3 |
| 7 | `plan` | 生成 `profile.farm.json` + 干跑预览（拒绝项 fail-closed） | rc 2 |
| 8 | `lease` | `lease_acquire`（TTL 3600s） | rc 2 |
| 9 | `execute` | `mas_run_profile --apply`（备份→写→读回→等完→还原） | rc 2/3/4/5 |
| 10 | `readback` | L1/L2 读回结果 + 独立复核 run 状态 | rc 3 |
| 11 | `restore` | 独立复核：无 open run / 临时表删净 / StageMode 回写 | rc 4 |
| 12 | `lease_release` | 释放实例租约（幂等；任何终局路径） | 只记 WARN |

## 5. 退出码（与子工具一致，脚本可直接判）

| rc | 含义 | 残留 |
|---|---|---|
| `0` | 期望路径完成（试探[如需] + 执行 + 还原都过） | 无（还原已复核） |
| `2` | 拒绝 / 发车前失败（NO-GO、台账 failed、解析失败、计划被拒、租约被占） | 无 |
| `3` | 试探未证明可刷（failed/inconclusive），或无试探时执行后校验失败 | 无（试探自带还原） |
| `4` | **有残留**（还原失败 / open run / 临时表未删） | 有 ⇒ 人工 |
| `5` | 试探成功但执行阶段失败 | 视执行阶段 |

## 6. 产物与证据索引

* 报告：`runtime/reports/farm_run_<ts>_<账号>.md`（步骤表 + 判定链 + 原命令）。
* run 目录：`runtime/farm_orchestrator/fo_<ts>_<账号>/`：`steps.json`、`summary.json`、`profile.farm.json`、`probe_run.log`、`farm_run.log`。
* 证据：`runtime/probe_farm/<probe_run_id>/`（试探）、`runtime/run_profile/backups/<run_id>/`（执行前快照，含 `accounts_before.json`/`plans_before.json`）。
* 审计：`runtime/mas_ledger.jsonl`（`kind=farm_orchestrator`）+ `runtime/mas_farm_orchestrator_log.jsonl`（每次调用一行，含 runner/owner/rc）。`--json` 单行输出同内容。

## 7. 自测与实测

* 自测：`--selftest` 覆盖 24 个用例（含「未知⇒试探⇒执行⇒还原」全链、水池旁路、NO-GO 否决、剿灭拒绝、dry-run 零发车、取值域）——**24/24 OK**（2026-09-28）。
* 首跑实测（2026-09-28，详见 `docs/audits/2026-09-28_farm_orchestrator_rehearsal.md`）：
  - `用户_173920298 × LS-6 --times 1 --medicine 0 --apply --owner <kanban>`：**rc=0 整链跑通**；
  - 试探 18 分 15 秒（冷启动 + 完整日常链 + 1 场 LS-6，`drop_statistics` 落 LS-6：龙门币 432 / 高级作战记录 4 / 中级作战记录 2）→ 台账 unknown→success；
  - 执行 17 分 26 秒（任务自然结束，`reason=maa_exit`；该轮理智 15 < 36 ⇒ 未再战，属正常）；
  - 还原复核：`accounts_before.json` 与线上逐键一致（`StageMode=Fixed`、`Stage=SR-7`、`Stage_1=1-7`、`Stage_Remain=1-7`、`SeriesNumb="0"`、`MedicineNumb=0`）、计划表 5→5 无临时残留；
  - **缺陷（本次暴露，已修）**：租约取到后从不释放（只等 TTL）——已加第 12 步 `lease_release`（任何终局路径都释放）并补 3 条 selftest 断言；本次演练的残留租约已按 `release --idx 2 --owner <kanban>` 手工释放留证（修复后未再真跑，遵循「真跑只做一次」）。

## 8. 已知边界 / 未验证（诚实清单）

1. **首跑等待预算**：冷启动 + 完整日常链实测 ≈ 16–18 分钟才打完第一场；`--timeout` 默认已按此给到 **1500s**（600 会在开打前就停，见首跑记录）。实例更慢/脚本更重时再往上调；确认实例很暖、只想快试也可显式调小。
2. **L2 深查锚点在本环境为空**：`asst.log` 的 `append_task` 锚点在本 MAA 版本/本机几乎不出现（全文件计数：`append_task=2`、`FightTask=0`、`StartButton=0`），因此 run_profile 的 L2 与探针的 asst 证据常为 0 行且**不影响判定**；判据主力是 `L1 读回`（`gui.new.json` 注入值）+ `history/<date>/<号>/*.json` 的 `drop_statistics`（含同批 `.log` 判据行）。
3. **探针 `clock_offset_s` 语义**：`mas_probe_farm` 的该字段实为 `host_now − since_local`（≈耗时），不是钟差；其 asst 时窗实际取 `host_now − 120s`。本环境无影响（锚点本就为空），若将来要依赖 asst 锚点，先修这里。
4. **AUTO-MAS history 文件名时标另有偏移**（`明日方舟-b-3-06-27-08.json` 对应宿主 10:27:08，恒 −4h）；只是命名，工具按 mtime/集合差匹配。宿主钟与本地钟实测有几十秒级偏移也在容差内。
5. **样本量**：只在 1 个号 × 1 个关上真跑过（b-3 × LS-6）。其它号/关、`--write-mode fixed`、多号脚本 `--all-accounts` 路径均未真跑（自测覆盖逻辑，但不等于实机验证）。
6. 剿灭不支持（拒绝）；要发剿灭走 `mas_run_profile` / `mas_scenario_dispatch` 对应路线。

## 9. 与其它入口的关系

* 只想发车（不关心关卡配置）：`mas_ondemand`（B1）。
* 已有计划 JSON：`mas_run_profile`（B2）。
* 拿不准能不能刷：本工具（一条链）或手搓 `mas_probe_farm`（B5）。
* 想要「该刷什么」的计划草案：`mas_plan_builder`（B3）→ 产出可直接喂 `mas_run_profile`。
