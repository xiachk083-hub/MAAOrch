# 2026-09-27 变更审计归档：账号切换补丁 v1/v2 + 两次侧车重启 + 全部发现

> 归档卡：`t_f9450f26`（mas，kanban `maa-parallel-benchmark-20260926`）｜生成：2026-09-27 21:2x CST
> 性质：**只读汇总**（未重复实验、未改任何生产配置；全部引用既有机器输出/日志原文与文件行号）。
> 口径：时间以机器证据（脚本输出 / `E:\AUTO-MAS\debug\app.log`）为准。卡题「三次补丁」= 写入面三次：v1 代码 / v1 模板 / v2（代码+模板）；看板评论里「05:45 重启、05:52 验证」为操作员通报近似值，机器证据见 §4。

## 0. 摘要与当前状态

- 今日 accswitch 相关变更共两版补丁 + 两次人工侧车重启（另有 19:01 每日自重启，非本次操作）：
  - **补丁① v1**（05:03）：有 `AccountName` 即开 `AccountSwitchEnabled`（官/乙方都注入）——修复官服多号串号。
  - **补丁② v2**（19:53）：收窄为**仅官服**切号；B/日/台一律 `""`+`false`（v1 误伤面回滚）。
- 当前生产状态：**v2 已加载运行**（侧车 PID 33976），双向注入实测通过（官-33 masked+true / b-2 empty+false）；晚班两个多号脚本各跑完 1 号（无串号），其余 2-4 号被 19:54 重启孤儿化（§4.3）。
- 一键回滚：§6；备份清单：§2/§3；全部发现：§5；看板清单与未决：§7。

## 1. 今日时间线（关键锚点）

| 时间 | 事件 | 证据 |
|---|---|---|
| 04:01:37 | AUTO-MAS 后端开机启动（今日根进程） | app.log L87677 |
| 05:03:05 | 补丁① v1 落盘（代码+模板） | `patch_accswitch.py`（mtime 05:03）；备份 `.bak-accswitch-20260927-050305` |
| 05:15 | 该轮注入仍 `false`（侧车未重载 ⇒ 补丁未生效） | kb_restart.md §2（05:15 轮 MAA_6 的 gui.new.json 仍 false） |
| 05:33:46 → 05:34:49 | **重启①**（停 14456 / REST 50s / 新 37292） | DSH seq=3080/3081；app.log L90701（05:34:25 banner） |
| 05:39:12 | 补丁生效实测：官-12 注入 `138****0245`+true | seq=3120/3121；taskId `0b0a9730…` |
| 10:55~12:05 | 官-1-1-7 四次**真实切号**（134/139/130/187 各命中） | t_fe38f3d6 判据注释 |
| 18:58 → 19:46 | 官-1-1-7 四号 UID 基线（实机大厅） | t_bec38656 注释；lobby_check 11 图 |
| 19:01:38 | **每日自重启**（非操作员；晚班前 PID 已非 37292） | app.log L103094 |
| 19:33:55 / 19:45:55 | 晚班两多号任务派发（官-2-4-47 / 官-1-1-7） | REPORT_t_56ea4c40 §四 |
| 19:53:19 | 补丁② v2 落盘（TEMPLATES_TOUCHED=56） | seq=3429；备份 `.bak-accswitch-v1-20260927-195319` |
| 19:53:44 → 19:58:18 | **重启②**（停 13196 / REST 40s / 新 33976）+ 双向实测 | seq=3437/3438；app.log L103961（19:54:17.775 banner） |
| 20:02:11 | MAA_6 孤儿轮自行跑完（无收尾）；孤儿化确认 | REPORT_t_56ea4c40 §五 |

## 2. 补丁①：账号切换 v1（05:03）

- **根因（两段）**：① MAA 6.x 把切号开关从全局迁移到 `StartUpTask` 的 per-task `AccountSwitchEnabled`（默认 `false`）；② AUTO-MAS 只注入 `StartUp.AccountName`，从未设开关 ⇒ 切号任务被 MAA 核心**静默跳过**（"task disabled, pass"），用当前号继续跑 = **官服多号串号**（05:15 抓帧实锤：连续两次登录 `ID:551601505`）。缺号/切号失败时行为=**硬失败**（重试≤5 → 「任务出错: 开始唤醒」→ AUTO-MAS 「MAA 未能正确登录 PRTS」）。
- **文件面**：`E:\AUTO-MAS\app\task\MAA\AutoProxy.py`（追加 4 行，v1 版 L774-777）；备份 `AutoProxy.py.bak-accswitch-20260927-050305`（47,694B，原件 mtime 09-24 00:39:49）。模板 58 处写入（48 `Default\ConfigFile` + 10 `Temp`，t_e4620caa §2；21:2x 现查备份 59 份=48+11，Temp 随运行自建自清）。全部 `py_compile` 通过。
- **diff -u（base → v1）**：
```diff
@@ -771,6 +771,10 @@
             )
         elif self.cur_user_config.get("Info", "Server") == "Bilibili":
             task_set["StartUp"]["AccountName"] = self.cur_user_config.get("Info", "Id")
+        # 2026-09-27 fix: 只注入 AccountName 却不启用账号切换 => MAA 不切号（官服多号串号）
+        task_set["StartUp"]["AccountSwitchEnabled"] = bool(
+            task_set["StartUp"].get("AccountName")
+        )
```
- **验证链**：05:15 注入仍 false（侧车未重载）→ 05:33 重启①（§4）→ **05:39:12 官-12 AutoProxy 注入实测** `AccountName=138****0245, AccountSwitchEnabled=true` ✅（stop 用返回 task_uid，干净收尾；该脚本 4 用户 Task 未受影响）→ 10:55/11:16/11:33/12:05 四次真实切号各自命中（掩码列表见 §5f）→ 18:57 判据归档（t_fe38f3d6）。
- **模板全量核对**（t_e4620caa，06:0x）：`data\` 122 份 `gui.new.json` → Default 98 份 = 48 true / 50 false（**50 false 全为空账号**）；无「带账号仍 false」的运行源。

## 3. 补丁②：v2 收窄——仅官服切号（19:53）

- **动机**：v1 按源码原样给 B 服也注入「账号名=原始 Id」并开开关，但 **B 服账号名是 B 站用户名**（数字 Id 必匹配不上）⇒ 会弄挂 B 服 StartUp；且 MAA 已取消非官服切号（详 `docs/audits/2026-09-27_non_official_no_switch.md`、`2026-09-27_maa_account_switch_history.md`）。用户口径：B/日/台「一实例一号、永不切号」。
- **文件面**：`AutoProxy.py` 现文件 L766-779；备份 `AutoProxy.py.bak-accswitch-v1-20260927-195319`（49,080B，v1 版，mtime 05:03:05）；模板 `TEMPLATES_TOUCHED=56`（现查 v2 备份 56 份=48 `Default\ConfigFile`+8 `Temp`；其中 4 份 Temp 无 v1 对应面）。
- **diff -u（v1 → v2）**：
```diff
@@ -763,18 +763,20 @@
         ] = _MAA_CLIENT_TYPE_TO_INT.get(
             self.cur_user_config.get("Info", "Server"), 0
         )
+        # 2026-09-27 fix(v2): 仅官服支持账号切换；B/日/台一律不注入账号名、不开开关
+        #   - 官服：AccountName = 掩码手机号，AccountSwitchEnabled = True（否则 MAA 静默不切号 → 串号）
+        #   - B 服：账号名是 B 站用户名（收集麻烦/易错），MAA 亦已取消非官服切号 ⇒ 一实例一号，不切
+        #   - 日/台：同上，不切
         if self.cur_user_config.get("Info", "Server") == "Official":
             task_set["StartUp"]["AccountName"] = (
                 f"{self.cur_user_config.get('Info', 'Id')[:3]}****{self.cur_user_config.get('Info', 'Id')[7:]}"
                 if len(self.cur_user_config.get("Info", "Id")) == 11
                 else self.cur_user_config.get("Info", "Id")
             )
-        elif self.cur_user_config.get("Info", "Server") == "Bilibili":
-            task_set["StartUp"]["AccountName"] = self.cur_user_config.get("Info", "Id")
-        # 2026-09-27 fix: 只注入 AccountName 却不启用账号切换 => MAA 不切号（官服多号串号）
-        task_set["StartUp"]["AccountSwitchEnabled"] = bool(
-            task_set["StartUp"].get("AccountName")
-        )
+            task_set["StartUp"]["AccountSwitchEnabled"] = True
+        else:
+            task_set["StartUp"]["AccountName"] = ""
+            task_set["StartUp"]["AccountSwitchEnabled"] = False
```
- **双向实测**（19:53:44 发起，19:58:18 完成；同一 run 输出原文）：
```
KILL: KILLED=13196 / REST back after 40s / POST: SIDECAR=33976
DISPATCH 明日方舟-官-33(Official) expect masked+true -> taskId 34aec93e-…
   INJECTED: "AccountName": "186****6119", ; "AccountSwitchEnabled": true,    ✅
   STOP: code 200（用返回 task_uid）
DISPATCH 明日方舟-b-2(Bilibili) expect empty+false -> taskId ae1d38bf-…
   INJECTED: "AccountName": "", ; "AccountSwitchEnabled": false,             ✅
   STOP: code 200
```
- 附带：v2 模板面顺带修正 378f 的 `Temp` 快照（19:53:19），此后轮末还原值为 true（reset 归档 §3）。

## 4. 两次侧车重启（14456→37292 / 13196→33976）

**4.1 为什么必须重启**：改的是 Python 模块（`AutoProxy.py`），侧车进程（`E:\AUTO-MAS\environment\python\python.exe E:\AUTO-MAS\main.py`）在启动时导入该模块，改盘不热更；05:15「已打补丁但注入仍 false」即实证。重启只停侧车：Electron 主进程会自动重拉（REST 50s/40s 恢复），MAA.exe 与模拟器不动。

**4.2 两次重启数据**（`do_restart.py` / `restart_verify_v2.py`，均在 `D:\1-2\MAAOrch\tmp\hermes_bridge\`，动态查 PID、可重跑）：

| # | 发起→完成 | PRE | KILL | 恢复 | POST | app.log 新 banner |
|---|---|---|---|---|---|---|
| ① | 05:33:46 → 05:34:49 | SIDECAR=14456, **MAA=11**, ELECTRON=4 | KILLED=14456 | REST back 50s | **SIDECAR=37292, MAA=10** | L90701 05:34:25.552 |
| ② | 19:53:44 → 19:58:18 | REST before True | KILLED=13196 | REST back 40s | **SIDECAR=33976** | L103961 19:54:17.775 |

（旁注：09-26 19:01:37、09-27 19:01:38 各有一次**每日自重启**（app.log L70288/L103094）——晚班前 PID 13196 而非 37292 的原因。）

**4.3 在飞 MAA 数与副作用**：
- ① 在飞 **11 → 10**（脚本直接测得）。该时刻在飞 run 的 AUTO-MAS 侧收尾同样会丢——**未见逐条核销记录**（列入 §7 未决）。
- ② 重启前的检查读数「`=== 当前在飞 MAA ===` → `0`」（19:53:34）**不成立，是引号塌陷伪影**：该条查询在 pwsh→ssh→cmd→PowerShell 四层嵌套中 `"..."` 配对塌陷，本机（无 MAA 进程）先把 `(Get-Process MAA|Measure-Object).Count` 求值成 `0`，再作为参数发给远端 `powershell -Command 0` 回显（21:1x 本地复现：表达式被本机求值为 `0`/Int32 并拼进远端命令行；该引号坑在运维技能 remote-file-ops 参考中已有警告）。**实际在飞 ≥2**：MAA_2 19:34:53→19:55:33、MAA_6 19:45:46→20:02:11（gui.log 实跑窗口），且该重启最终**孤儿化**了两任务：
  - 919c8a3b@19:33（官-2-4-47）与 378f7379@19:45（官-1-1-7）会话态随旧后端消失；MAA 客户端未杀、各自跑完，但无收尾（无「日志统计/已解锁/第2号/任务结束」）；**每脚本第 2-4 号当晚未跑**。
  - 核验：晚班定时2/6 Time=19:33/19:45 已过且 Enabled=false；Hermes 调度日志无补发；6 个 watcher 观察至 20:50 无新事件。
  - 锁/残留：未走 unlock（无「已解锁」行）、Temp 残留；重启后新后端未见锁冲突（REPORT §六4）。

## 5. 发现合集（逐条带证据）

- **a. 任务模式语义**（t_806dac03 → `docs/audits/2026-09-27_mas_task_modes.md`）：**ScriptConfig=配置管理模式（无限等待）**——只装配置+拉 GUI，`ScriptConfig.py:70-78 wait_event.wait()` 且无任何 setter ⇒ 拿它等任务链永远等不到（历史坑 `tools/mas_startup_verify.py:132`）；**ManualReview=只跑 StartUp（登录）+人工判收**（`ManualReview.py:396/332-336`；弹窗 `manager.py:237-251`）；**AutoProxy=唯一跑完整链**（剿灭→日常两轮，`AutoProxy.py:464` 起）。⇒ **验证注入必须走 AutoProxy**（05:15 的 ScriptConfig 验证实测看到的是旧快照 `data\<sid>\<user>\ConfigFile`，不得用作判据；该旧快照现象本身是独立问题，§7）。
- **b. `stop` 必须用 start 返回的 task_uid**（`task_manager.py:396-407`；对已结束任务的 stop 视为成功 L397-400）——传队列/脚本 uid 无效；**只有走 stop 才会 unlock+还原配置**；不要 kill MAA。
- **c. 配置锁定语义**：每次任务 prepare 锁脚本配置（`manager.py:125`，锁后配置写被拒）→ final_task `unlock`（L199）→ 还原 MAA 配置 Temp→config（L256-260）；被 stop 也会走 finally shield（`models/task.py:199-212`）。锁是运行期状态：**不正常收尾 ⇒ 不会出现「已解锁脚本配置」行**（晚班孤儿即此症状）。
- **d. 切号日志判据 = `账号切换 -->> <掩码>`**（gui.log，`TaskQueueViewModel`；掩码=前 3 后 4）。今日实证样本：`[2026-09-27 12:05:08.166] 账号切换 -->> 187****8608`（另 10:55→134 / 11:16→139 / 11:33→130 / 19:46:18.964→134）。**判据只认这行**，不要用开关值本身推断「切过了」。
- **e. PostActions=9 = 退出明日方舟 + 退出 MAA（不含关模拟器）**：`AutoProxy.py:713-716` 注释枚举（None=0, ExitSelf=8, ExitArknights=1, ExitEmulator=4）；MAA_2 gui.log `[19:55:31.965] Post actions: 退出 明日方舟 -> 退出 MAA`；配置面 `"PostActions": "ExitArknights, ExitSelf"`。⇒「跑完实例停在桌面」是**设计行为**（t_aaacd03c 判因一致）。
- **f. `UseExpiringMedicine` 只给剿灭开了**：`constants.py:165` 剿灭=**True** / `:206` 剩余理智=**False** / `AutoProxy.py:278` 活动关=**False**；主理智作战未显式设置 ⇒ 沿用用户配置（样本 false）。深挖与修复草案见 `docs/audits/2026-09-27_mas_medicine_expiry.md`（t_ac9d3099）。
- **g. 官服 4 号 UID 基线**（t_bec38656；图片 `tmp\hermes_daily_check\lobby_check\` 11 张）：`134****5276`汐影月k=**568685565**「空白」/ `139****8825`潮响=**551601505**「紧急神出鬼没」/ `130****9165`tbNick_2l3vf=**394162955**「XAN」/ `187****8608`him=**940869304**「当你流眼泪时」。官-2-4-47 侧：`192****7973`=64323902（其余 3 号待 t_76b88661）。
- **h. 客户端账号列表可滚动**（最多 10 条；**只在列表容器内拖**）——「只有 2 条 ⇒ 切不到」的误判已作废；4 号全在列表。
- **i. 开关不会「切一次自动取消」**（t_e43fecf4 → `docs/audits/2026-09-27_mas_account_switch_reset.md`）：唯一「勾选消失」机制=AUTO-MAS 轮末 Temp 快照还原（mtime 取证）；MAA v6.18.0 无运行期自动置 false 写入点。
- **j. 非官服面**：B/日/台不注入（v2）；MAA 对非官服/日服的切号支持差异见 `2026-09-27_non_official_no_switch.md`、`2026-09-27_maa_account_switch_history.md`（含 22 号核实与版本溯源）。
- **k. 本次已验证的「排除项」**：串号根因**不是**「MAA 从没写过开关」，而是 per-task schema 未跟随；失败时**不会**报错文案（静默跳过）；**不是**客户端账号列表缺失（列表可滚动、4 号齐全）。

## 6. 备份清单与一键回滚

**备份清单**（主机 `100.79.173.69`；两段时间戳：v1 前=`…-20260927-050305`，v2 前=`…-v1-20260927-195319`）：
- 代码：`E:\AUTO-MAS\app\task\MAA\AutoProxy.py.bak-accswitch-20260927-050305`（原始）、`…bak-accswitch-v1-20260927-195319`（v1）。
- 模板：`E:\AUTO-MAS\data\**\gui.new.json.bak-accswitch-20260927-050305`（59 份）、`…bak-accswitch-v2-20260927-195319`（56 份）。
- 补丁脚本（幂等，重跑会打印 `CODE_ALREADY_PATCHED`/`ALREADY_V2`）：`D:\1-2\MAAOrch\tmp\hermes_bridge\patch_accswitch.py`、`patch_accswitch_v2.py`。

**回滚 A：仅撤销 v2（回到 v1 状态）**
```bat
copy /y "E:\AUTO-MAS\app\task\MAA\AutoProxy.py.bak-accswitch-v1-20260927-195319" "E:\AUTO-MAS\app\task\MAA\AutoProxy.py"
```
```powershell
Get-ChildItem 'E:\AUTO-MAS\data' -Recurse -File -Filter '*.bak-accswitch-v2-*' | ForEach-Object {
  $o = $_.FullName -replace '\.bak-accswitch-v2-\d{8}-\d{6}$',''; Copy-Item $_.FullName $o -Force; Write-Output ("restored: " + $o) }
```

**回滚 B：全量回滚到 05:03 前（撤销 v1+v2）**
```bat
copy /y "E:\AUTO-MAS\app\task\MAA\AutoProxy.py.bak-accswitch-20260927-050305" "E:\AUTO-MAS\app\task\MAA\AutoProxy.py"
```
```powershell
Get-ChildItem 'E:\AUTO-MAS\data' -Recurse -File -Filter '*.bak-accswitch-20260927-050305' | ForEach-Object {
  $o = $_.FullName -replace '\.bak-accswitch-20260927-050305$',''; Copy-Item $_.FullName $o -Force; Write-Output ("restored: " + $o) }
Get-ChildItem 'E:\AUTO-MAS\data' -Recurse -File -Filter '*.bak-accswitch-v2-*' | ForEach-Object {
  $o = $_.FullName -replace '\.bak-accswitch-v2-\d{8}-\d{6}$',''
  if (-not (Test-Path ($o + '.bak-accswitch-20260927-050305'))) { Copy-Item $_.FullName $o -Force; Write-Output ("restored(v2-only): " + $o) } }
```

**任何回滚后必须重启侧车**（否则不生效）：
```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" D:\1-2\MAAOrch\tmp\hermes_bridge\do_restart.py
# 判定：输出 `KILL: KILLED=<pid>` → `REST back after NN s` → `POST: SIDECAR=<新pid>`；120s 未恢复会自动兜底整机重启
```
**回滚后验证**（任选其一只读手段）：读 `E:\MAA-Multi\MAA_6\config\gui.new.json` 的 `StartUpTask`（回 A 后断号轮应为 `""+false` 或旧 v1 语义；回 B 后为原始不注入）；或按 kb_restart 的 80 秒注入实测法（AutoProxy 单机轮 + 读回 + stop）。终结验收标准仍是 §5d 的 `账号切换 -->> 掩码` 日志行。

## 7. 看板任务清单（材料卡）与未决问题

与本审计直接相关的卡（状态定格 21:2x；**全量 80 卡清单见配套页 `2026-09-27_kanban_task_inventory.md`**）：

| 卡 | 状态 | 内容 → 产出 |
|---|---|---|
| t_e4620caa | done 05:41 | 修复 AccountSwitchEnabled → 补丁①（FIX_VERIFICATION.md 附件） |
| t_c5a92e8f | done 05:38 | 切号全链路 → `2026-09-27_mas_account_switch_chain.md` |
| t_806dac03 | done 05:23 | 三种任务模式 → `2026-09-27_mas_task_modes.md` |
| t_3006bc8f | done 05:40 | 重启①+注入验证（本页 §4.2/§2） |
| t_56ea4c40 | done 20:29 | 晚班端到端验收 → `shift1930\REPORT_t_56ea4c40.md` |
| t_fe38f3d6 | done 18:57 | 切号日志判据（§5d） |
| t_bec38656 | done 19:46 | 官-1-1-7 四号 UID 基线（§5g） |
| t_e43fecf4 | done 20:26 | 开关不自动取消+重置层 → `2026-09-27_mas_account_switch_reset.md` |
| t_3bb20fe1 / t_263b584f / t_3f5e4681 | done | 非官服口径修正 → `2026-09-27_non_official_no_switch.md`、`…_account_switch_history.md` |
| t_7e69b3d0 / t_5c23c432 | running | 全库切号体检 / 注入闸门接巡检（`SWITCH_GATE`） |
| t_38b8ef3c / t_76b88661 | blocked | 补号与首轮验收 / 官-2-4-47 UID 基线（等用户补号） |
| t_aaacd03c / t_4dc0a418 | done | 桌面停因 / MAA 报错定因 → `…_maa_errors_triage.md` |
| t_ac9d3099 | done 21:16 | 理智药 → `2026-09-27_mas_medicine_expiry.md`（§5f） |
| t_f270d627 / t_a1cf9f5b / t_b8476841 | done | 字段手册 / 肉鸽 playbook / 活动 playbook（t_4e1d436d 技能化 running） |
| t_85025aea | done 21:12 | 成本/吞吐复盘 → `2026-09-27_kanban_throughput.md` |
| t_f9450f26 | running | **本卡**（本文档）→ 子任务 t_0f598a66（技能沉淀，todo） |
| 批次（running ~26 张） | — | 资产普查①-⑤、机制深挖①-④、运维①②④⑤、能力②③、数据① 及接续重发卡（21:2x 现查） |
| 批次（archived 15 张） | — | 多为 20:35-20:37 重复派发（含本卡重复体 t_31bf214a）与 21:10-21:11 重发归档 |

**未决问题**：
1. 重启①在飞 11 个 run 的收尾/记账影响未逐条核销（晨班尾部；建议下个完整班次抽样对照 history）。
2. 晚班两脚本 2-4 号补跑与否待用户决策（t_38b8ef3c/t_76b88661 blocked）；官-2-4-47 后 3 号 UID 未采。
3. 「在飞 0」伪影已澄清（§4.3）；看板中「05:45/05:52」口径与机器时间（05:34:49/05:39:12）不一致，建议后续以机器时间归档。
4. ScriptConfig 注入 `data\<sid>\<user>\ConfigFile` 旧快照（196****0050=帝国猛虎）为独立未修问题（t_3006bc8f 注释记录）。
5. MAA_6 双脚本共用导致的 Temp 串号观察（运行期副本，未改动）。
6. idx29/官-15 残留模态需人工处理（t_aaacd03c）；非本轮修复面。
7. 注入闸门（`switch_gate`）已接巡检但尚未覆盖「重建模板面」回归场景（t_5c23c432 进行中）。
8. 看板仍有同题并跑卡（能力②/③、数据① 各两卡同时 running），建议由「看板治理规范」卡统一去重。

## 8. 证据索引（速查）

- 补丁脚本：`tmp\hermes_bridge\patch_accswitch.py` / `patch_accswitch_v2.py`；重启/验证：`do_restart.py` / `restart_verify_v2.py` / `verify_autoproxy.py`
- 主机日志：`E:\AUTO-MAS\debug\app.log`（L87677/L90701/L103094/L103961）；`E:\MAA-Multi\MAA_2|MAA_6\debug\gui.log`
- 归档文档：`docs\audits\2026-09-27_mas_account_switch_chain.md`、`…_task_modes.md`、`…_reset.md`、`…_non_official_no_switch.md`、`…_account_switch_history.md`、`…_mas_medicine_expiry.md`、`…_maa_errors_triage.md`、`…_mas_failure_taxonomy.md`、`…_hermes_takes_over_mas_scheduler.md`
- 报告/产物：`tmp\hermes_daily_check\shift1930\`（验收报告+帧+OCR）、`tmp\hermes_daily_check\lobby_check\`（4 号基线 11 图）、`lobby_baseline\`
- 看板：DB `boards\maa-parallel-benchmark-20260926\kanban.db`（80 卡）；**全量清单（快照 21:33）→ 本目录 `2026-09-27_kanban_task_inventory.md`**

## 附：配套文件
- `2026-09-27_kanban_task_inventory.md` — 看板全量 80 卡导出（id/状态/建卡/完成/标题/产出摘要；快照 2026-09-27 21:33）。
