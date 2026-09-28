# 首跑记录：`mas_farm_orchestrator` 真演练（用户_173920298 × LS-6，2026-09-28）

> 结论：**整链跑通，rc=0**（决策→试探→执行→还原→放租约）。一次真跑、最小规模（1 号 × 1 关 × SeriesNumb=1 × 不吃药）。
> 本页是「从决策到还原」的逐环证据；用法规范见 `docs/MAS_FARM_ORCHESTRATOR.md`。
> 落地来源：看板卡 `t_73ff4db2`（owner 同名）；操作者 Hermes 会话（profile `mas`）。

## 0. 一句话时间线

| 时刻(本地) | 事件 | 证据 |
|---|---|---|
| 10:08:18 | 干跑先行（同参数、零写）：preflight GO、台账 unknown、决策=probe | `runtime/reports/farm_run_20260928_100447_用户_173920298.md`（干跑版） |
| 10:08:54 | 命中目标：账号 用户_173920298 → 脚本 明日方舟-b-3（`E:\MAA-Multi\MAA_9`，实例 **2**） | steps.json `resolve` |
| 10:08:54–10:27:04 | **试探**：`mas_probe_farm --probe-only --apply`（窗口 900s） | `runtime/probe_farm/pf_20260928_100849__173920298/` |
| 10:24–10:25 | 日常链跑到 Fight：`selected LS-6` → `开始行动 1 次, -36理智` → 掉落入账 | history 尝试 `明日方舟-b-3-06-09-03.json`（同批 `.log` 判据行） |
| 10:25:26 | 试探窗口到点 ⇒ dispatch/stop（宿主答「任务已在中止中」） | app.log `中止任务`；run_profile `wait.stop_resp` |
| 10:26:32 | 任务结束；history 落盘（结果串「任务被用户手动中止」，但 drop_statistics 已含 LS-6） | app.log `任务结束`；`mt=10:25:32` |
| 10:27:01–04 | 判定 `success/stage_farmed`（drop_statistics 主证据）；台账 unknown→success | evidence.json；`ledger_update.after_status=success` |
| 10:27:07 | 计划（series=1/medicine=0）+ **取租**（TTL 3600s，owner=t_73ff4db2） | steps.json `plan`/`lease` |
| 10:27:08–10:44:34 | **执行**：`mas_run_profile --apply`（写→读回→等完→还原） | run `rp_20260928_102708_-b-3` |
| 10:44:34 | 还原复核通过 + 报告落地，rc=0 | 本页 §2/§3 |
| 10:49 | 手工释放演练租约（修复后的工具会自放，见 §4.1） | `LEASE=released`；`LEASES=0` |

## 1. 从决策到还原：逐环证据

1. **解析**：`脚本 明日方舟-b-3 ｜ dir E:\MAA-Multi\MAA_9 ｜ emu 2`；账号 uid `bd2e68c5-5af7-4883-b316-82c18799439b`。
2. **班前**：`preflight = GO`（failing/reasons 皆空）。
3. **台账**：`用户_173920298 × LS-6 = unknown`（报告 built_at 08:58:21；row: last_attempt 2026-09-07、selected_n 4、success_n 0）。
4. **决策**：`probe`（unknown ⇒ 先试探；不猜）。
5. **试探**（mas_probe_farm，--probe-only）：判定 `success / stage_farmed`，理由「drop_statistics 出现该关（主证据）」。
   - 新增尝试 1 个（窗口内集合差）：`明日方舟-b-3-06-09-03.json`，`mt=2026-09-28 10:25:32`，`result=任务被用户手动中止`，`drops=["LS-6"]`；
   - 同批 `.log` 判据行：`GetFightStage: from ["LS-6"], selected LS-6` → `Start Task Chain: Fight, Task ID: 2` → `开始行动 1 次, -36理智` → `LS-6 掉落统计:` → `Completed Task Chain: Fight, Task ID: 2`；
   - 掉落实值：`龙门币 432 ｜ 高级作战记录 4 ｜ 中级作战记录 2`（结束后理智 12）；
   - 台账更新：`unknown → success`（update rc=0；sources=`drop_statistics, fight_log_drop`；actions 1→2）；
   - 内层 run_profile 回执：`rc=3 / wait_timeout（等待超时 900s）`，`restore=restored`——**探针按证据判 success 与内层超时停止是两件事**，两处都在记录里可见（诚实口径）。
6. **计划**：`profile.farm.json`，payload `{SeriesNumb:"1", Stage:"LS-6", Stage_1..3:"-", Stage_Remain:"-", MedicineNumb:0}`，干跑预览 rc=0、零拒绝。
7. **取租**：`acquired`，`expires 10:27:07+3600s`，`purpose=farm_orchestrator 用户_173920298×LS-6`。
8. **执行**（run_profile --apply）：
   - 备份：`runtime/run_profile/backups/rp_20260928_102708_-b-3/`（`accounts_before.json` / `plans_before.json` / `hashes.json` / `meta.json`）；
   - 读回 L1：`ok=True`（satisfied=[`bd2e68c5…`]，polls=5）；L2 深查 `ok=null / 0 行`（见 §4.2）；
   - 等待：`ended=true / reason=maa_exit / duration_s=898`（任务自然结束，不是被停）；
   - 本批 history：`明日方舟-b-3-06-27-08.json`（`maa_result=Success!`，`sanity=15`，`drop_statistics={}`——该轮理智 15 < 36 ⇒ 未再战斗，属正常口径）。
9. **还原复核**（独立只读，非工具自报）：`open_runs=无`、临时计划表 `无`、`StageMode prev=Fixed now=Fixed ok=true`。
10. **放租约**（修复后）：见 §4.1。

## 2. 还原的独立复核（第三方视角）

用 run 自己的备份快照与**线上现值**逐键比对（`fetch_users`）：

| 键 | 备份（跑前） | 线上（跑后） | 一致 |
|---|---|---|---|
| StageMode | Fixed | Fixed | ✅ |
| Stage | SR-7 | SR-7 | ✅ |
| Stage_1 / _2 / _3 | 1-7 / - / - | 1-7 / - / - | ✅ |
| Stage_Remain | 1-7 | 1-7 | ✅ |
| SeriesNumb | "0" | "0" | ✅ |
| MedicineNumb | 0 | 0 | ✅ |
| Task | `{IfActivityFirst:false, ActivityMedicineNumb:0}` | 同左 | ✅ |

计划表：5 → 5（`新 MAA 计划表 / 非活动刷取计划 / 拍前看简介-重装芯片AT-6周计划 / Yeee CE-6/LS-6 周轮换 / 练号计划`），**无任何「临时-」残留**。
租约：演练后 `LEASES=0`（已释放）。

## 3. 产物索引（本次演练）

* 报告：`runtime/reports/farm_run_20260928_100815_用户_173920298.md`（rc=0 正式版）；`…_100447_…md`（10:04 干跑版）。
* run 目录：`runtime/farm_orchestrator/fo_20260928_100815_用户_173920298/`（`steps.json`/`summary.json`/`profile.farm.json`/`probe_run.log`/`farm_run.log`）。
* 试探证据：`runtime/probe_farm/pf_20260928_100849__173920298/{evidence.json,summary.json}`。
* 流水：`runtime/mas_ledger.jsonl`（kind=farm_orchestrator）、`runtime/mas_farm_orchestrator_log.jsonl`、`runtime/mas_run_profile_log.jsonl`。
* 台账：`runtime/reports/stage_capability.json`（LS-6×用户_173920298 → success，built_at 10:48:45）。

## 4. 本次暴露的缺陷 / 实测发现

### 4.1 租约只取不放（已修，本演练暴露）
* 现象：编排器第 8 步 `lease_acquire` 拿到租约后，**任何终局路径都不释放**（`lease_release` 只出现在自测假依赖里）。演练跑完 10:44 时租约仍在（到期 11:27），会把实例白占最多 1 小时。
* 修复：新增第 12 步 `lease_release`（`finish()` 内统一调用，幂等；任何终局都放；失败只记 WARN 不掩盖主结论），并补 3 条 selftest 断言（成功链必放、执行失败链必放、dry-run 不取不放）+ 报告 §2.5 增加「释放」行。
* 证据：`--selftest` 修复前 22/24 FAIL → 修复后 **24/24 OK**；本次演练的残留租约已按 `mas_instance_lease.py release --idx 2 --owner t_73ff4db2` 手工释放（`LEASE=released`、`LEASES=0`）。
* 未做：修复后未再真跑（遵循「真跑只做一次」）；该路径的实机复验留给下一次真跑或联调。

### 4.2 L2/锚点判据在本环境天然为空（不是回归）
* `E:\MAA-Multi\MAA_9\debug\asst.log`（3.5 MB）全文件计数：`append_task=2`、`FightTask=0`、`StartButton=0`。因此 run_profile 的 L2 深查与探针的 asst 证据常为 `0 行`（`ok=null`）。
* 影响：无——判据主力是 `L1 读回` + history `drop_statistics`（含同批 `.log` 判据行），本次判定即由它支撑。
* 建议（后续卡）：要么按本 MAA 版本改锚点模式（如 `GetFightStage` / `Start Task Chain` 行），要么在文档里把 L2 标注为「本机不适用」。

### 4.3 探针 `clock_offset_s` 名不副实（未改，别人工具）
* `mas_probe_farm.py:695-705` 计算的是 `host_now − since_local`（≈本次窗口耗时，本次 1060s），并非钟差；随后 asst 时窗取 `host_now − 120s`。
* 影响：本环境为 0（锚点本就为空）；但若将来要依赖 asst 锚点，需先修这里（应先真正测钟差，再 `since_host = since_local + off − 余量`）。**未改**：该文件属已核验的 6 件套之一，改动需其 owner 的核验链。

### 4.4 首跑等待预算（已按实测改默认）
* 实测：冷启动 + 完整日常链 ≈ **16–18 分钟**才打完第一场（试探整段 1095s，其中窗口 900s 踩着线过——Fight 刚打完就撞窗口，任务随后被 stop，超时）。
* 处置：编排器 `--timeout` 默认 `600 → 1500`（脚本内 help 注明理由），文档 §3/§8 同步；否则 cold 实例的「先试探」默认路径会系统性踩空。
* 附带观察：试探的 history 结果串是「任务被用户手动中止」（超时 stop 所致），**不要拿它当失败**；判据只看 `drop_statistics`（工具口径已如此）。

### 4.5 AUTO-MAS history 文件名时标 −4h（仅命名）
* `明日方舟-b-3-06-27-08.json` 对应宿主时间 10:27:08（发车时刻），恒差 −4h；且宿主钟与本地钟有几十秒级偏移（探针 evidence 记 `host_now` 供对表）。
* 影响：无——工具按 mtime 与集合差匹配；**别按文件名时间对表**。

## 5. 未做 / 未验证（诚实清单）

* 只在 1 个号（用户_173920298）× 1 个关（LS-6）上真跑过；多号脚本 `--disable-others/--all-accounts`、`--write-mode fixed`、`--remain-sanity`、`--force-probe`、台账 failed 的 `--retry-after-fix` 放行——均只有自测覆盖，无实机证据。
* 执行阶段本轮因理智 15 < 36 未再战（`drop_statistics={}`），所以「执行阶段再打一场」的实机证据由**试探阶段**那一场提供（同一 run_profile 执行路径）。
* 修复后的租约释放路径未实机复验（见 4.1）。
* 剿灭不支持（工具直接拒绝）；`--farm-max-wait` 的兜底超时未真触发过。

## 6. 复现命令

```
$PY312 tools/mas_farm_orchestrator.py --account 用户_173920298 --stage LS-6 --times 1 --medicine 0            # 干跑
$PY312 tools/mas_farm_orchestrator.py --account 用户_173920298 --stage LS-6 --times 1 --medicine 0 \
        --apply --owner t_73ff4db2                                                                          # 真跑
$PY312 tools/mas_instance_lease.py list                                                                     # 租约应回到 0
```
