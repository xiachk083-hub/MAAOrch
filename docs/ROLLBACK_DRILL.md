# 回滚演练矩阵（2026-09-28）

> 卡：`t_a90b0f8b`（mas）｜演练脚本：`tmp/hermes_bridge/rollback_drill.py`（可复跑）
> 定期巡检接线：卡 `t_7fa96ea5` ⇒ 独立周跑 job `mas-rollback-drill`（每周一 09:00；见 §5.2、§6）
> 实测证据（全部真实回执）：`docs/audits/2026-09-28_rollback_drill.txt` + `…_rollback_drill.json`（44/44 PASS）；
> 首跑（含一次真实踩坑）留档：`docs/audits/2026-09-28_rollback_drill.run1.{txt,json}`。
> 纪律遵守：**演练只在临时副本**（本机 `%TMPDIR%\rollback_drill_*`、主机 `E:\AUTO-MAS\_rbdrill_*` 演练后即删）；
> **未碰生产文件**（H1 复核哈希一致）、**未重启侧车**、未动模拟器。回滚的「重启生效」一环不在本演练范围（见 §4 卡片）。

---

## 0. 结论摘要（30 秒版）

- 10 项变更（R1–R10）的回滚路径**全部在副本上真跑通过**（44 个校验点）：4 个代码补丁、2 代模板面、关卡修复、配置快照、定时接管、mumu 免重启开关（R11 侧车重启不在演练范围）。
- **回滚基本都能"一次成功"**：补丁类 = `copy 备份→目标`（秒级）+ 重启侧车；模板类 = 一条 PS 遍历；配置类 = `--restore`（685 文件 50s）。
- 但演练**抓到 1 个真坑**：`mas_config_snapshot` 的锁在进程崩溃后会残留，**把"回滚"堵了最多 2 小时**（本次首跑实际撞上，见 §3-F1）。修正步骤已写进卡片。**→ 已加固（t_6fa92a0a，09-28）：死锁自愈 + drill 残留自清，见 §5.1。**
- 另发现模板备份面 2 周内会自然缩水（Temp 自清理），**不能按"59+56 份"去验收**；整文件回退 ScriptConfig 会连带回退备份时刻之后的全部配置改动（含账号/队列）——**优先字段级 `--rollback`**。
- 所有备份文件与清单哈希**8/8 一致**（演练前先核过），补丁闸门在"已打补丁/已回滚"两态的判定翻转全部符合预期。

---

## 1. 变更清单（补丁/配置改动 + 备份 + 声称的回滚 + 生效条件）

依据：`runtime/patches/manifest.json`（09:20 录制）+ `docs/MAS_EMERGENCY_RUNBOOK.md` §2 + `docs/audits/2026-09-27_full_change_audit.md` §6。
"声称的回滚"= 变更时文档里写的回滚方式（本表第 3 列），"演练"= 本次实跑（§2 矩阵）。

| # | 变更（时间） | 被改文件 / 对象 | 备份（主机 E:\AUTO-MAS） | 声称的回滚步骤 | 生效条件 |
|---|---|---|---|---|---|
| R1 | 切号补丁 v2（09-27 19:53） | `app\task\MAA\AutoProxy.py` | `AutoProxy.py.bak-accswitch-v1-20260927-195319` | copy 备份→AutoProxy.py（回 v1）；模板面同步 | **重启侧车** |
| R2 | 切号补丁 v1（09-27 05:03）回原版 | 同上 | `AutoProxy.py.bak-accswitch-20260927-050305` | copy 备份→AutoProxy.py | **重启侧车** |
| R3 | 模板面 v2（48+8 份） | `data\**\gui.new.json` | `…gui.new.json.bak-accswitch-v2-20260927-195319` | PS 遍历：`$o=名-replace 后缀; Copy-Item` | 下一轮注入（无需重启） |
| R4 | 模板面 v1（48+11 份） | 同上 | `…gui.new.json.bak-accswitch-20260927-050305` | 同上 | 下一轮注入 |
| R5 | 吃药·AutoProxy（09-28 02:19） | `AutoProxy.py` L278 | `AutoProxy.py.bak-medicine-20260928-021922` | copy 备份→AutoProxy.py（或手改 L278=False） | **重启侧车** |
| R6 | 吃药·constants（09-28 02:19） | `app\utils\constants.py` L206 | `constants.py.bak-medicine-20260928-021922` | copy 备份→constants.py | **重启侧车** |
| R7 | mumu 判活关闭（09-28 02:19） | `app\utils\emulator\mumu.py` | `mumu.py.bak-closefix-20260928-021922`；以及 flag 免重启档 | ① `New-Item config\mumu_close_legacy.flag`（免重启）② copy 备份→mumu.py | ① 下一轮 close 起即刻；② **重启侧车** |
| R8 | 关卡修复（09-27 23:41，46 号 Stage_1） | `config\ScriptConfig.json`（走 API 写） | `backups\stagefix_20260927_234051\{ScriptConfig.json,MANIFEST.txt}` + `runtime/reports/stage_fix_{ts}/plan.json` | ① `tools/mas_stage_fix.py --rollback`（字段级）② 整文件拷回备份 | ① 下一轮任务 prepare；② 文件级立即（注意停写） |
| R9 | 配置/数据面（全量） | `config\*.json` + `data\**` + 补丁文件 | `runtime/snapshots/<ts>` + 主机 `backups\cfgsnap_<ts>`（敏感文件只在主机） | `tools/mas_config_snapshot.py --restore <ts> [--apply]` | 立即（下轮/启动读取） |
| R10 | 定时接管（09-27） | AUTO-MAS 24 条定时 Enabled=false | 无需文件备份（只改 Enabled，Days/Time 保留） | `hermes cron pause 2600f6415f16 && mas_timers.py --enable` | 即时（先停后开） |
| R11 | （一切代码改动的统一开关） | 侧车进程 | —— | `tmp/hermes_bridge/do_restart.py` | README：REST 40-50s 恢复 |

**演练前/后核对（实测）**：R1/R2/R5/R6/R7 涉及的 3 个生产文件 + 5 个备份 = 8 份 sha256 与清单/记录 **8/8 一致**；
演练结束后 3 个生产文件哈希与开演前一致（H1）→ 全程零生产改动。

---

## 2. 演练矩阵（变更 | 回滚步骤（实测命令）| 演练结果 | 修正后的步骤）

> 演练结果列 = 本次真实执行的证据（PASS 均附关键回执）。"修正后的步骤"= 演练后建议照此执行（有差异才写）。

### R1/R2 切号补丁（代码面）

| 项 | 内容 |
|---|---|
| 回滚步骤（实测） | `Copy-Item '<bak-…>' '<AutoProxy.py>' -Force` → 重启侧车 |
| 演练结果 | **PASS（一次成功）**。本机副本：v2→v1 后 sha256==v1 备份（`da864dd3…`）、`MARKERS=1`（v2=3/v1=1/原版=0 与文档一致）、py_compile 通过；主机 scratch：回 v1 后闸门 `PATCH_GUARD=MISSING:2`（accswitch+medicine 同时判缺）；`--reapply accswitch_v2 --apply` 可一键重放回 v2（`REAPPLY=OK`，闸门回 `OK`）。 |
| 耗时 | 演练全链 6.8s（本机）+ 32.6s（主机 scratch 含 6 轮闸门） |
| 需重启 | **侧车**（否则内存里仍是旧代码） |
| 残留 | 无（scratch 已删，GONE=True） |
| 修正后的步骤 | ① **回 v1 会连带撤掉吃药补丁（同一文件）**——只想回切号请用 `--reapply` 通道或手工只改 L766-779；② 回滚后立刻 `mas_patch_guard --check`，判 `MISSING` 即"补丁确实没了"，再重启；③ 回原版（R2）后闸门会报官服槽 `VIOLATION`（串号风险态回归）——**除非推倒重来不要用**。 |

### R3/R4 模板面

| 项 | 内容 |
|---|---|
| 回滚步骤（实测） | `Get-ChildItem 'E:\AUTO-MAS\data' -Recurse -File -Filter '*.bak-accswitch-v2-*' \| % { $o = $_.FullName -replace '\.bak-accswitch-(v2-)?\d{8}-\d{6}$',''; Copy-Item $_.FullName $o -Force }`（050305 同款） |
| 演练结果 | **PASS**。生产枚举（只读）：v2 代 48 份、050305 代 54 份，**全部一一映射到现存目标**（`nomatch=0 target_missing=0`）；本机沙箱 6 样本按同一条 PS 真跑，还原文件与备份逐字节一致；跨代"整文件回滚"（先 v2 再 050305）也验证可行。 |
| 耗时 | 4.4s |
| 需重启 | 不需要（下一轮注入生效） |
| 修正后的步骤 | ① **份数会漂**：文档口径 59+56，现查 54+48（Temp 备份随轮次自清）——执行前先枚举（命令自带），别按固定份数验收；② **模板回滚必须与代码面配套**：注入源是 `AutoProxy.py`，只回模板不回代码 ⇒ 下一轮注入按现代码重写模板（模板回滚是"一次性"的）；③ 建议顺序：先回代码 → 重启侧车 → 再回模板。 |

### R5/R6 吃药补丁

| 项 | 内容 |
|---|---|
| 回滚步骤（实测） | `Copy-Item '<constants.py.bak-medicine…>' '<constants.py>' -Force`；AutoProxy 同款（或手改 L278 `UseExpiringMedicine`=False） |
| 演练结果 | **PASS（一次成功）**。AutoProxy：sha256==备份、`"UseExpiringMedicine": False` 行回归、v2 切号特征保持；constants：`True` 计数 2→1、False 行回归；py_compile 全过；主机 scratch 单回 medicine 时闸门 `MISSING:1`（accswitch 报 `WARN` 哈希漂移——证明"一个文件两个补丁"时**回其中一个不会误伤另一个的判定**）。 |
| 耗时 | 含在 A/B 两组里（本机 6.8s + 主机 scratch 32.6s） |
| 需重启 | **侧车** |
| 修正后的步骤 | 无需修正；备注：单文件多补丁时以 `mas_patch_guard` 的逐补丁 verdict 为准，别用文件级哈希判断"回滚成功"。 |

### R7 mumu 判活关闭（含免重启档）

| 项 | 内容 |
|---|---|
| 回滚步骤（实测） | ① `New-Item E:\AUTO-MAS\config\mumu_close_legacy.flag`；② `Copy-Item 'mumu.py.bak-closefix-…' 'mumu.py' -Force` + 重启 |
| 演练结果 | **PASS**。flag 档：代码里 `Path(__file__).resolve().parents[3]` 推导 == `E:\AUTO-MAS\config\mumu_close_legacy.flag`（生产路径实测一致）；沙箱真值表：无 flag→走新逻辑、建 flag→回退旧逻辑（`close()` 分支条件实测翻转）；当前生产 flag 不存在（新逻辑生效）。全量档：sha256==备份、新方法消失、py_compile 通过；主机会话闸门 `mumu_close_fix MISSING`。 |
| 耗时 | ≈0s（flag 判定） |
| 需重启 | flag 档**不需要**；全量档需要 |
| 修正后的步骤 | 无需修正。备注：flag 是"软回滚"，适合出事时先止血；事后仍应把 mumu.py 全量回退或按新版合并，避免开关长期挂着。 |

### R8 关卡修复回滚

| 项 | 内容 |
|---|---|
| 回滚步骤（实测） | 主机 scratch 上真跑：备份 `stagefix_20260927_234051\ScriptConfig.json` 拷回；另核对 `mas_stage_fix.py --rollback` 的 plan 在位 |
| 演练结果 | **PASS**。复读：现在档 `sr=46 / Stage_1=1-7 ×46`；备份档 `1-7 ×0 / '-'×46`（=修复前）。改坏 3 号（1-7→'-'）→ 拷回备份 → **sha256 与备份一致**（`6b3d9e42…`），46 号全部回到 `'-'`。凭据不出主机（演练在主机 scratch 完成，脚本不打印内容）。 |
| 耗时 | 3.9s |
| 需重启 | 不需要（文件级；下轮读取） |
| 修正后的步骤 | ① **整文件拷回 = 回到 22:11 的全量配置**——会把该时刻之后的**一切**改动（新号、队列、计划表）一并回退；实测 ScriptConfig 一小时内漂了 2 次（09:47 `41e51ad5` → 09:57 `59749a16`）⇒ **优先 `mas_stage_fix.py --rollback`**（按 plan 只回 Stage_1），整文件还原只当兜底；② 还原前先停写（收班窗口 + 确认无在飞任务），还原后跑 `--verify`。 |

### R9 配置/数据面快照回滚

| 项 | 内容 |
|---|---|
| 回滚步骤（实测） | `python tools/mas_config_snapshot.py --restore 20260928_092944__baseline --apply --to <临时目录>`（演练用 `--to`；生产直接 `--apply`） |
| 演练结果 | **PASS**。物化 686 文件（`RESTORE=DONE ok=685 skipped=1`，skip=设计：敏感文件本地无 store 通道）；改坏 3 处（Config.json 字段、gui.new.json 字段、删除一个 gui.json）→ 二次 restore `ok=3`；**685 个文件逐一对 manifest 哈希：异常 0**。 |
| 耗时 | 50.3s（含一次只读漂移扫描） |
| 需重启 | 不需要（配置下一轮读取；补丁类如需生效仍要重启） |
| 修正后的步骤 | ① **锁残留坑（实测撞上）**：上次崩溃的 drill 在 `runtime/mas_config_snapshot.lock` 留锁，restore 直接被拒（"已有快照/回滚在跑"），**最长堵 2 小时**（LOCK_STALE_S）才自愈；遇此先 `tasklist` 查锁主 pid，死了就删锁再回滚（本次已清）。② 敏感文件（`config\ScriptConfig.json`）**只能走主机 store** 还原——回滚前确认快照有 store（本快照 store 在位，含 215,259B 的 ScriptConfig.json）；本地 `--to` 演练永远跳过它，属设计不是失败。③ 快照不是实时镜像：回滚前跑 `--drift`（只读）看清会回退哪些动态文件（本次 7 处：Config.json / ScriptConfig.json / 5 个活动缓存），避免把正常演进误回退。 |

### R10 定时接管回滚

| 项 | 内容 |
|---|---|
| 回滚步骤（实测） | `mas_timers.py --enable`（组合：`hermes cron pause 2600f6415f16` 先行） |
| 演练结果 | **PASS**（在本机 mock 副本上真跑，生产只做只读复读）。生产现状：24 条定时 `Enabled=False ×24`；mock：`--enable` → `changed=24`、Days/Time 原样保留、rc=0；**幂等**（再跑 changed=0）；反向 `--disable` 同样可跑（changed=24）。Hermes 发车 job `2600f6415f16 [active]` 只读核对在位。 |
| 耗时 | 35.7s |
| 需重启 | 不需要（AUTO-MAS 定时即时生效） |
| 修正后的步骤 | 无需修正。纪律重申：**先 pause cron 再 enable**（防窗口重叠双发）；本次**未实跑** pause/resume 与真 enable（生产调度动作，遵守"演练不碰生产"）。 |

### R11 侧车重启（统一生效开关）

| 项 | 内容 |
|---|---|
| 演练结果 | **未演练**（约束：不重启侧车）。依赖既有实战证据：09-27 两次重启（`do_restart.py`，REST 40-50s 恢复），见 `MAS_EMERGENCY_RUNBOOK` §2.5。 |
| 修正后的步骤 | 回滚类操作的重启判定：**代码文件改了必须重启**；flag/template/config 类**不需要**。重启前确认零在飞，重启后跑 `mas_patch_guard --check` + `SWITCH_GATE` + 下一轮注入读回。 |

---

## 3. 关键发现（演练抓出来的真问题）

- **F1 残留锁会堵死回滚（真事故级）**：`mas_config_snapshot` 用 `O_EXCL` 锁，crash 后锁文件留存，**2 小时**内所有快照/回滚请求全部被拒。本次首跑（09:52）实际撞上——锁主 pid=33876 已死、锁 mtime=09:32（= 上一次崩溃的 drill）。处置：`tasklist /FI "PID eq <锁内 pid>"` 判死活 → 死了删 `runtime/mas_config_snapshot.lock` → 重跑。已写入卡片。**→ 已修（t_6fa92a0a）：`run_lock()` 加锁前读锁内 pid 判活（Windows OpenProcess + GetExitCodeProcess；判不了活回退 2h 老逻辑）——死锁自动清理重试、活锁绝不抢；selftest 增 3 例全绿。**
- **F2 同样的崩溃还留了主机垃圾**：`E:\AUTO-MAS\backups\_cfgsnap_drill_20260928_093234__drill`（8MB）仍在（上一次 drill 死在阶段2，未走到 cleanup）。本次演练未动它；建议随手清（它是 drill 前缀，删除安全）。**教训：演练也要"可中断恢复"，crash 后先查锁+scratch 再重跑。** **→ 已修（t_6fa92a0a）：遗留已清（09-28 10:26，复核 1.8MB、清后复扫为零）；`--drill` 阶段0 现在先扫同前缀残留（自己/旧的都算）并清理、在 evidence.json 记 `residual_before`。**
- **F3 备份面会随时间缩水**：模板面备份从"59+56"漂到"54+48"（Temp 随轮次自建自清）。回滚命令本身不受影响（枚举式），但**验收口径必须用"枚举得到的现存量"，不是历史文档里的固定份数**。
- **F4 单文件多补丁**：AutoProxy.py 上挂着 2 个补丁（切号+吃药），"回整个文件"会一起撤掉。闸门按补丁逐条判定（B2/B5），是判断"回滚到底撤掉了什么"的权威工具。
- **F5 模板回滚是"一次性"的**：注入源在代码侧，只回模板不回代码，下一轮注入会把模板按现代码重写。模板面回滚只在"代码已回或即将回"时才有意义。
- **F6 配置备份是移动目标**：ScriptConfig.json 一小时漂 2 次（在飞任务的轮末落盘）。整文件还原前必须停写；能字段级就别整文件。
- **F7 快照≠实时**：09:29 的 baseline 到 09:57 已有 7 处漂移（2 个 config + 5 个活动缓存）。rollback 前用 `--drift` 看清代价。

---

## 4. 回滚卡片（一页纸 · 出事时照单执行）

```
【通用】执行环境：调度机 git-bash。先定义：
  SSHDIR="ssh -i ~/.ssh/maaorch_target -o StrictHostKeyChecking=no Administrator@100.79.173.69"
  PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"
纪律：代码类回滚 → 必须重启侧车；班次窗口 05:00-08:00 / 19:30-23:00 内不重启。
      任何回滚前：$PY312 tools/mas_patch_guard.py --check（先知道现状）。

① 切号 v2 出问题 → 回 v1（少数场景）：
   $SSHDIR "powershell -NoProfile -Command \"Copy-Item 'E:\AUTO-MAS\app\task\MAA\AutoProxy.py.bak-accswitch-v1-20260927-195319' 'E:\AUTO-MAS\app\task\MAA\AutoProxy.py' -Force\""
   警告：会同时撤掉吃药补丁（同文件）。之后：重启侧车 → mas_patch_guard --check（应 MISSING:2）。
② 切号整体推倒 → 回原版（一般不用）：
   …同款，源换 'AutoProxy.py.bak-accswitch-20260927-050305'。后果：官服多号串号风险回归（闸门 VIOLATION 是预期）。
③ 吃药回滚：
   AutoProxy：源 '…bak-medicine-20260928-021922'；constants：'constants.py.bak-medicine-20260928-021922' → 各自覆盖 → 重启侧车。
④ mumu 判活止血（免重启）：$SSHDIR "powershell -NoProfile -Command \"New-Item 'E:\AUTO-MAS\config\mumu_close_legacy.flag' -ItemType File -Force\""
   撤销：删掉该文件。全量回退：'mumu.py.bak-closefix-20260928-021922' 覆盖 → 重启侧车。
⑤ 模板面（与代码配套做）：
   $SSHDIR "powershell -NoProfile -Command \"Get-ChildItem 'E:\AUTO-MAS\data' -Recurse -File -Filter '*.bak-accswitch-v2-*' | ForEach-Object { $o = $_.FullName -replace '\.bak-accswitch-(v2-)?\d{8}-\d{6}$',''; Copy-Item $_.FullName $o -Force; Write-Output ('restored: ' + $o) }\""
   （回 05:03 前把 -Filter 换 '*.bak-accswitch-20260927-050305'）
⑥ 关卡修复回滚（优先字段级）：$PY312 tools/mas_stage_fix.py --rollback
   兜底（整文件，先停写）：拷回 E:\AUTO-MAS\backups\stagefix_20260927_234051\ScriptConfig.json
⑦ 配置/数据面回滚：$PY312 tools/mas_config_snapshot.py --restore <快照ts> --apply
   ⚠ 若被拒"已有快照/回滚在跑"：09-28 起**锁主已死会自动清理**（t_6fa92a0a）；仍被拒 = 有活进程在跑（或判不了活且未满 2h）→ `tasklist /FI "PID eq <锁内 pid>"` 复核后再决定。
   回滚前先 $PY312 tools/mas_config_snapshot.py --drift 看代价；敏感文件走主机 store（勿用 --to 演练版顶替）。
⑧ 恢复 AUTO-MAS 定时（交还发车）：hermes -p mas cron pause 2600f6415f16 && $PY312 tools/mas_timers.py --enable
   反悔：$PY312 tools/mas_timers.py --disable && hermes -p mas cron resume 2600f6415f16
⑨ 重启侧车（⑤⑥⑦⑧ 之外的一切代码改动的生效开关）：$PY312 tmp/hermes_bridge/do_restart.py
   判定：KILL: KILLED=<pid> → REST back after NN s → POST: SIDECAR=<新pid>；120s 未回自动兜底。

【验证三连】mas_patch_guard --check ／ mas_switch_gate --check ／ 下一轮注入读回（gui.new.json）
【残留自查】主机：E:\AUTO-MAS\_rbdrill_* 不应存在；backups\_cfgsnap_drill_* 由 drill 自扫（新增），
           非 drill 运行期仍不应存在。本地：runtime/mas_config_snapshot.lock 不应残留（死锁会自愈，正常收尾不该有）。
```

**一页纸背后的一行逻辑**：*先判型（代码/模板/配置/开关）→ 选最小回滚档（flag>字段级>整文件）→ 执行 → 闸门验证 → 重启（仅代码类）→ 清残留。*

---

## 5. 残留与后续

| 项 | 状态 |
|---|---|
| 本地残留锁 `runtime/mas_config_snapshot.lock`（09:32 遗留） | **已清除**（演练首跑被它堵住，处置已归档） |
| 主机残留 `backups\_cfgsnap_drill_20260928_093234__drill`（8MB） | **已清除**（2026-09-28 10:26，卡 t_6fa92a0a；复核逻辑大小 1.8MB、清后复扫为零；drill 亦已加启动自扫） |
| 本演练 scratch（本机 `%TMPDIR%\rollback_drill_*`、主机 `E:\AUTO-MAS\_rbdrill_*` / `_rbdrill_sc_*`） | 两轮演练共 4 个主机 scratch 全部已删（B7/F4/H2；收尾复扫 `RB=[]`）。本机目录留存备查（`%TMPDIR%` 自动回收） |
| 建议改进 | ① **已做**（t_6fa92a0a：锁主存活检查→死锁自动清，活锁不抢）；② **已做**（drill 阶段0 先扫/清 `_cfgsnap_drill_*` 并在 evidence 记「上次未清」；锁侧同源自愈）；③ **已做**（t_7fa96ea5：独立周跑 job `mas-rollback-drill`，每周一 09:00 + FAIL 告警；见 §5.2）；④ **已做**（遗留已清）。子卡 **`t_6fa92a0a`** 已完成，证据见 §7；**`t_7fa96ea5`** 为 ③ 的接线卡，证据见 §7 |

### 5.1 加固记录（t_6fa92a0a，2026-09-28）

- **锁自愈**（`tools/mas_config_snapshot.py::run_lock()`）：拿锁遇 `FileExistsError` 时读锁内 pid 判活——
  已死 → 清锁重试（崩溃遗留锁不再堵 ≤2h）；**判不了活**（锁内容非法 / 跨平台判活失败）→ 回退原
  「mtime 超 `LOCK_STALE_S`=2h 视为过期」逻辑；**活锁绝不抢**（拒绝并保留）。判活口径：Windows
  `OpenProcess(0x1000)` + `GetExitCodeProcess`（实测：仅靠 OpenProcess 会把「已退出但句柄未关」的
  僵尸 pid 误判为活）；非 Windows `os.kill(pid,0)`（Linux 僵尸态 Z 判死）。selftest 新增 3 例，
  35/35 全绿。
- **演练残留自清**（`do_drill()` 阶段0）：`--drill` 起跑先扫 `E:\AUTO-MAS\backups\_cfgsnap_drill_*`（自己/
  旧的都算）并清理（`remote_drill_residuals()`，前缀 guard 同 `remote_rmdir`），`evidence.json` 记
  `residual_before`；`--skip-remote` 时跳过。
- **验收证据**：`docs/audits/2026-09-28_lock_selfheal.txt`（selftest 全绿 + 死锁直清/活锁拒绝端到端 +
  残留清扫 + drill 阶段0 闭环 + **E 组真实 685 文件复跑 5/5** + **真实 runtime 死锁直清（事故原形态，
  锁窗口 0.6s）**；全程零生产改动；真实锁仅在单文件 `--only` 验收里持 0.6s，前后核对无同族进程）
  + `docs/audits/2026-09-28_lock_selfheal.e_group.json`。
- **复跑脚本**：`tmp/hermes_bridge/t_6fa92a0a_{lock_accept,residual_clean,drill_phase0,real_deadlock}.py`
  （末行各为 `LOCK_ACCEPT=` / `RESIDUAL_CLEAN=` / `DRILL_PHASE0=` / `REAL_DEADLOCK=PASS|FAIL`）。

### 5.2 定期巡检接线（t_7fa96ea5，2026-09-28）

- **形态**：**独立 cron job**（不挂进 30 分钟巡检主链 `52b88b0ffca0`——那是只读巡检，别给它加 3 分钟非只读负担）；
  **no_agent 脚本**（零 LLM/token，与 `mas-shift-dispatch` `2600f6415f16` / `mas-shift-report` `8b1708397046` 同形态）。
  这不违反 `HERMES_MAS_HANDOVER` §11 的「不新增探针 cron / 不加第二个巡检」——那条红线针对 **agent 型**任务的
  agent-time/token 成本（30 分钟节拍 ≈ 10M token/天），脚本型任务不构造 AIAgent、零 token。
- **链路**：cron `mas-rollback-drill`（id **`55337a0a2b62`**，`0 9 * * 1` = **每周一 09:00**，deliver=local）
  → `profiles/mas/scripts/mas_rollback_drill_cron.py`（薄包装，`EXTRA=[]`）→ `tools/mas_rollback_drill.py`
  → `tmp/hermes_bridge/rollback_drill.py`（全量 A–H；2026-09-28 实测 48–50s）。
- **三闸（任一不过 = `SKIP`，不产生 FAIL 噪音；进度可见于 cron 输出 + 运行流水）**：
  ① **窗口闸**：班次窗口 05:00–08:00 / 19:30–23:00 内不跑（`--force` 只跳过本闸）；
  ② **单飞锁**：`runtime/mas_rollback_drill.lock`（pid 判活自愈——死锁自动清、活锁绝不抢；另查
     `runtime/mas_config_snapshot.lock`：生产快照/回滚在跑 ⇒ 本轮 SKIP——E 组要拿同一把锁，别互相堵）；
  ③ **主机可达**：SSH 不通 ⇒ SKIP、**不告警**（主机可达性归口 host_watchdog，本工具不重复报「主机没了」）。
- **判据**：演练末行 `ROLLBACK_DRILL=PASS|FAIL`；工具再交叉核对退出码与演练 JSON 计数，
  **三者取最严**（末行 PASS 但 rc≠0 / 计数不符 / 无末行 ⇒ 一律 FAIL，fail-closed）。
- **产物**：`docs/audits/<date>_rollback_drill_auto.{txt,json}`（保留最近 10 份；子集运行 `--only` 走
  `…_auto_only_<组>.{txt,json}`、不参与轮转）；每轮一行 `runtime/rollback_drill_cron_log.jsonl`（含 SKIP 轮）。
- **FAIL 告警三件套（默认 dry-run，不发外部通知）**：① payload `runtime/reports/rollback_drill_alert_<date>.json`
  （schema `maaorch.rollback_drill_alert/1`）；② 流水 `runtime/rollback_drill_alerts.jsonl`；
  ③ **看板只读卡**（`idempotency_key=rollback-drill:<date>`、status=done、无 assignee ⇒ 不参与派工，人来处置）。
  **真发需用户批准**：`--alert-send` 或 `MAAORCH_ROLLBACK_DRILL_ALERT_SEND=1`（走 `runtime/watchdog.ini`，
  与 AUTO-MAS 通知同源）；开 cron 真发 = 把包装脚本 `EXTRA = []` 改成 `["--alert-send"]`（改一行即回滚）。
- **超时兜底**：演练被超时打断（默认 1500s）⇒ 归档部分 stdout + 按演练自报 ts 清它自己的主机 scratch
  （`E:\AUTO-MAS\_rbdrill_<ts>` / `_rbdrill_sc_<ts>`，严格 `\d{8}-\d{6}` 白名单，绝不误删别的）。
- **已知边界**：① 演练本体仍在 `tmp/hermes_bridge/`（未入 git，docs 各处复跑命令都指它）——若被清理，
  job 会 FAIL（`NO_SCRIPT`）并落卡，**不会静默**；要把它归档进 `tools/` 属另一张卡（会牵动 §3/§6 引用）。
  ② FAIL 有两种性质：**真回归**（回滚路径失效）或**基线已变**（补丁面/manifest/快照 ts 变了）——
  告警正文与看板卡都提示「先分辨再处置」。③ 看板卡的 `created_by` 复用班次日报的写卡函数 ⇒ 字段显示
  `shift-report`（仅归属字段；卡体已写明由 `tools/mas_rollback_drill.py` 生成）。
- **手动触发**：`hermes -p mas cron run 55337a0a2b62`（即时一次）；或直接跑工具（见 §6）。
- **入口自检进回归**：`python tools/mas_rollback_drill.py --selftest`（25 例，离线）已并入
  `tools/tests/test_regression.py` **F1 组**；2026-09-28 全量复跑 `REGRESSION=PASS (34 passed, 0 failed,
  0 skipped, 34.3s) [live]`（含实机只读层）。
- **回滚**：`hermes -p mas cron pause 55337a0a2b62`（停）/ `hermes -p mas cron remove 55337a0a2b62`（移除）
  + 删包装脚本；本卡未改演练本体、未动生产文件、未重启任何东西。

## 6. 复跑方式

**自动（周跑，t_7fa96ea5 已接线）**：cron `mas-rollback-drill`（`55337a0a2b62`，**每周一 09:00**）→
`mas_rollback_drill_cron.py` → `tools/mas_rollback_drill.py`（三闸 → 演练 → 归档 → 判据 → FAIL 告警，见 §5.2）；
即时手动触发 `hermes -p mas cron run 55337a0a2b62`。**判据看末行**：`ROLLBACK_DRILL_AUTO=PASS|FAIL|SKIP:<原因>`
（退出码 0=通过/跳过、4=FAIL、3=入口自身异常）。

```bash
# 手动（建议走巡检入口：自带三闸/归档/判据/FAIL 告警；全量约 50–150 秒，需 SSH 可达主机、只碰临时副本）
python tools/mas_rollback_drill.py                    # 全量 A–H
python tools/mas_rollback_drill.py --only A,H          # 子集（调试；不覆盖全量证据）
python tools/mas_rollback_drill.py --force             # 应急手跑（忽略窗口闸）
python tools/mas_rollback_drill.py --selftest          # 离线自检（25 例，不连主机、不跑演练）

# 直跑演练本体（绕过巡检入口；证据 = docs/audits/<date>_rollback_drill.{txt,json}）
C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe tmp/hermes_bridge/rollback_drill.py \
  --json-out docs/audits/$(date +%F)_rollback_drill.json | tee docs/audits/$(date +%F)_rollback_drill.txt
# 单组（调试）：--only A,B,C…（A 补丁本机 / B 主机 scratch / C flag / D 模板 / E 配置 / F 关卡 / G 定时 / H 收尾）
# 末行判据：ROLLBACK_DRILL=PASS|FAIL
```

## 7. 证据文件索引

- `docs/audits/2026-09-28_rollback_drill.txt` / `.json` —— 44/44 PASS 完整回执（含每步 detail 与耗时；cycle：A 6.8s / D 4.4s / B 32.6s / F 3.9s / E 50.3s / G 35.7s / H 3.7s / 合计 137.5s）
- `docs/audits/2026-09-28_rollback_drill.run1.{txt,json}` —— 首跑留档（38/41）：3 个 FAIL = E 组被残留锁堵住（F1 事故，已清锁重跑全绿）+ F2 一处**演练脚本自身的期望值写错**（把"回到备份态"误写成"回到修复态"，已修正脚本注释与断言；问题在被测工具之外，见 run2 的 F2=PASS）
- `docs/audits/2026-09-28_lock_selfheal.txt` / `.e_group.json` —— t_6fa92a0a 验收（锁自愈：死锁直清/活锁拒绝/判不了活回退 2h；drill 阶段0 残留自扫闭环；E 组真实 685 文件复跑 5/5；selftest 35/35）
- `runtime/patches/manifest.json` —— 补丁清单（备份/断言/回滚/重放）
- `docs/MAS_EMERGENCY_RUNBOOK.md` §2 —— 声称的回滚（本次被逐条演练）
- `tmp/hermes_bridge/rollback_drill.py` —— 演练脚本本体（可复跑）
- `docs/audits/2026-09-28_rollback_drill_auto.{txt,json}` —— **自动跑**证据（cron `55337a0a2b62` 首次一跑
  44/44 PASS 49.5s；当日 4 轮全量全 PASS，本文件 = 最近一次全量（cron 2026-09-28 11:09 那轮）；
  txt = 演练 stdout 全文，json = 演练逐步骤证据；保留最近 10 份）
- `docs/audits/2026-09-28_rollback_drill_wiring.txt` —— 接线验收（t_7fa96ea5：入口 selftest 25/25 +
  告警通道 smoke 8/8 + 手动全量 44/44 + cron 自动跑 ×2 + 收尾核对（锁/主机残留/看板复原）；
  含 cron 输出路径与回滚命令）
