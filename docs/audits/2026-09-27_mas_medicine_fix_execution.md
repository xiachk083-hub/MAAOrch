# 46 个 SR-* 账号「理智药消耗路径」修复执行报告（2026-09-27）

- 卡：`t_52ebdda4`（阶段1 盘点 / 阶段2 修复 / 阶段3 代码侧草案 / 阶段4 实证）
- 审计依据：`docs/audits/2026-09-27_mas_medicine_expiry.md`
- 执行时点：2026-09-27 23:36 – 2026-09-28 00:15（本地）
- 工具：`tools/mas_stage_fix.py`（dry-run / apply / verify / rollback / selftest）
- 结论：**46 号中 45 号已写入并 API 复读通过；`以七` 因任务正在运行被锁，等待释放后一行命令补写。**

## 1. 修法与依据（为什么是 Stage_1='1-7'）

闭幕后 `[SR-x]` 单候选不开放 ⇒ MAA 直接跳过理智作战（`关卡计划内没有当日开放的关卡`），
临期理智药没有消耗路径。修复 = 给 46 号补恒开放回退位：`Info.Stage_1: "-" → "1-7"`，
Stage 主值不动（下次同前缀活动自动恢复）。依据全部来自实机证据：

1. 机制反证：`["AT-4","1-7"] → 1-7`（闭幕后自动回退）、`["SR-7","1-7"] → SR-7`（9/08 潮响）——
   多候选列表在 MAA 内会正确挑「当日开放」的关卡；`1-7` 在所有抽到的日志里恒可选中。
2. 用户固有表单模式 = `[活动关…, 1-7]`（墟复刻期 `["AT-8","AT-7","AT-6","1-7"]`）；
   46 号的 `Stage_Remain` 本来就是 `1-7` ⇒ 与既定惯例一致，不新增语义。
3. 不用 CE-6 当主回退的原因：CE-6 周三/五不开放（9/02、9/04 实机日志 `["CE-6","LS-6"] → LS-6`），
   要覆盖全部七天只有 1-7 可靠；且各号历史刷取物偏好不一（CE-6/CA-5/LS-6/1-7 混合），
   批量替用户选「刷什么」属越权。
4. 不用计划表（选项 c）的原因：牵动计划机制和其它使用者，回滚面大；活动回归用「Stage 不动」更稳。

## 2. 执行记录

- 工具干跑（零写入）→ `runtime/reports/stage_fix_20260927_233640/plan.json`（46 号 before→after 全量 diff）。
- 单号试写 `MikuKong` → API 复读通过（先小样后全量）。
- 全量 apply → **45/46 ok**；`以七`（脚本 `明日方舟-官-13`，emu 45）`locked`（该脚本有任务在跑，10 分钟重试窗口内未释放）。
- 写入字段仅 `Info.Stage_1`；`ConfigBase.update()` 为字段级合并（`app/models/ConfigBase.py:995`），凭据键读入即弃、未落盘。
- 备份（写前快照）：`E:\AUTO-MAS\backups\stagefix_20260927_234051\`
  - `ScriptConfig.json`（sha256 `6B3D9E425C698DF9933FEA758A958F10B8785066060E52E6FA91651BA5439B8F`，212,692 B，mtime 2026-09-27 22:11:20）
  - `MANIFEST.txt`（文件清单 / 哈希 / 大小 / mtime / 范围）
- 明细结果：`runtime/reports/stage_fix_20260927_233640/result.json`；ledger：`runtime/mas_ledger.jsonl`（`kind=stage_fix`）。

### 待补（一行命令，幂等）

```bash
python tools/mas_stage_fix.py --apply --only 以七 --wait-min 10   # 锁释放后补写，其余号自动跳过
```

## 3. 关键机制发现（影响验收与「变更窗口」重启）

**`user/update` 只改内存、不立即落盘**：实测写入 24 分钟后 `ScriptConfig.json` mtime 未变；
与 2026-09-16 审计（`logs/auto_mas_toggle.jsonl`：`user/update 只改内存不落盘`）互相印证。

- **生效时机**：内存即时生效 ⇒ 影响之后所有轮次的注入（MAA 日志里的关卡计划即为准）。
- **落盘时机**：**每次 MAA 任务轮末**显式全量保存 —— `app/task/MAA/manager.py:210 → Config.ScriptConfig.save()`
  （另有 general/HSR/M9A/MaaEnd/Okww/SRC manager 同类调用）。⇒ 下一个任务轮次跑完，磁盘即为新值。
- **对重启的约束**：**任何重启侧车/App 的变更窗口，必须先确认已跑过至少一个任务轮（或先 `--verify` 确认落盘位已是 `1-7`）**，
  否则重启会把窗口期内只存在于内存的配置变更回滚成磁盘旧值。已把该语义固化进工具（`verify_disk_pending_flush` 视为「待 flush」而非失败）。
- 顺带定位：本机 urllib 走系统代理 `127.0.0.1:7890` 时对 `100.79.173.69:36163` **间歇性 502**；绕代理后 20/20 全 200。
  （MAS 侧工具统一加 `build_opener(ProxyHandler({}))` 即可；已提示 `t_7d630aa6`。）

## 4. 阶段4 实证方案（下一个已排定轮次）

判据（对 46 号任一任务轮日志，`E:\AUTO-MAS\history\<日期>\<账号>\*.log`）：

1. **不应再出现** `关卡计划内没有当日开放的关卡` / `理智作战 任务跳过`（闭幕后旧现象）；
2. `GetFightStage` 的列表应变为 `["SR-x","1-7"]`，闭幕后 `selected 1-7`（活动期仍 `selected SR-x`）；
3. `理智作战` 有正常消耗（临期药被使用，`使用临期理智药` 出现）。

（本报告落笔时补跑 `t_7d630aa6` 正在执行今晚的补跑，其涉及的 SR 号即为第一批实证样本；
明早 05:00–05:44 班次全量覆盖。）

## 5. 回滚

```bash
python tools/mas_stage_fix.py --rollback            # 按 plan.json 还原 Stage_1='-'（锁感知，锁定时等待）
```
应急：`ScriptConfig.json` 从 `E:\AUTO-MAS\backups\stagefix_20260927_234051\` 还原（注意先停写、再还原、跑一轮或重启）。

## 6. 阶段3 代码侧草案（**不自行实施**，并入 `t_02210a15` 变更窗口）

目的：把「剩余理智」也纳入临期药消耗；与「判活逻辑修复」同一次重启窗口实施。

1. `app/constants`（mirror 行号 `_host_constants.py:206`）`MAA_REMAIN_FIGHT_BASE`：
   `"UseExpiringMedicine": False` → `True`
2. `app/task/MAA/AutoProxy.py:278`（活动关优先分支 `_build_activity_priority_fight`）：
   `"UseExpiringMedicine": False` → `True`
   （同段 `UseExpireMedicineForActivity: False` L279 保持不动，单独评估）
3. 注：卡面写「constants.py 两处」；实测 `UseExpiringMedicine` 全仓库仅 3 处——
   `constants.py:165`（剿灭，已 True）、`constants.py:206`（剩余理智，待改）、`AutoProxy.py:278`（活动关，待改）。
   ⇒ 实际为 constants 1 处 + AutoProxy 1 处。
4. 实施前先跑 `--verify` 确认本卡变更已落盘，避免窗口重启丢内存变更（见 §3）。

## 7. 遗留与建议

- `以七` 补写（§2 命令）；补写后 `--verify` 全绿即为 46/46。
- 巡检/日报可加一条断言：近 24h `任务跳过` 计数 > 0 且原因含「没有当日开放的关卡」即告警。
- `docs/audits` 之外建议在 MAS-KB 补一条「配置落盘语义」条目（本报告 §3）。
