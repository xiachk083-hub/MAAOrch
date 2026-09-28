# MAS 应急预案与一键回滚手册

> 版本：2026-09-27 21:45（全篇为**当日实测口径**；未实测的项目显式标注「未实跑」）
> 适用：AUTO-MAS 主机 `MRFZ-0000`（100.79.173.69）+ 调度机 DESKTOP-L1OEMR3（MAAOrch 仓库 `D:\1-2\MAAOrch`）
> 定位：**出事时不用想，照单执行**。常规运维看 `docs/MAS_RUNBOOK.md`；本手册只管「回滚 / 故障 / 判型 / 演练」。
> 命令约定：默认在调度机 git-bash 里执行。先定义一次：

```bash
SSH="ssh -i ~/.ssh/maaorch_target -o StrictHostKeyChecking=no Administrator@100.79.173.69"
PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"   # 带 cv2/rapidocr 的解释器
```

> 复杂 PowerShell 建议落成 `.ps1` 文件后 `$SSH "powershell -NoProfile -File C:\path\x.ps1"`；本文给出的均为可直接粘贴的单行形式。
> **班次窗口纪律**：`05:00–08:00` / `19:30–23:00` 为执行窗口，**禁止**重启侧车、重启实例、清磁盘（会吞在飞任务 / 抢 IO）。维护窗口 = 收班后至 04:00、以及 04:00/19:00 计划重启前后。

---

## 0. 一页纸速查（打印这页就够）

### 0.1 三类「马上要做的判断」（全只读）

| # | 一条命令 | 判读 |
|---|---|---|
| A | `curl -s -m 6 -X POST http://100.79.173.69:36163/api/scripts/get -H "Content-Type: application/json" -d '{"scriptId":null}'` | 有 `"code":200` = REST 活；拒绝/超时/502 = 挂了 → **F1** |
| B | `$PY312 tools/mas_dispatch.py --check` | `OK` = 调度面健康；`FAIL` + `AUTO-MAS 不可达` = **F1**；看 alerts/retry 行 |
| C | `$PY312 tools/mas_switch_gate.py --check` | `SWITCH_GATE=OK` / `VIOLATION:<槽位>` / `UNKNOWN` → 见**判定树 §4.3**（VIOLATION 不等于事故，先看槽位是否在飞） |

### 0.2 急停（按需逐级）

```bash
hermes -p mas cron pause 2600f6415f16      # ① 只停发车（Hermes 不再启动新队列）
hermes -p mas cron pause 52b88b0ffca0      # ② 连 30 分钟巡检一起停（一般不需要，巡检是只读）
# ③ 在飞任务止损（REST）：POST /api/dispatch/stop {"taskId":"<返回的 task_uid>"}
#    —— 只有走 stop 才会 unlock + 还原配置；不知道 task_uid 时不要乱停。
# ④ 恢复发车：hermes -p mas cron resume 2600f6415f16
```

### 0.3 六个故障 → 首条命令

| 故障 | 首条命令（只读） | 处置入口 |
|---|---|---|
| F1 AUTO-MAS REST 挂 | 0.1-A | §3.1 |
| F2 侧车假死 | `$SSH "powershell -NoProfile -Command \"(Get-Item 'E:\AUTO-MAS\debug\app.log').LastWriteTime; Get-Content -Encoding UTF8 -Tail 3 'E:\AUTO-MAS\debug\app.log'\""` | §3.2 |
| F3 实例 adb 失联/假死 | VMM 进程表（§3.3 第 2 条） | §3.3 |
| F4 MAA 更新后配置不兼容 | `$SSH "powershell -NoProfile -Command \"(Get-Item 'E:\MAA-Multi\MAA_6\MAA.exe').VersionInfo.FileVersion\""` | §3.4 |
| F5 磁盘满 | `$PY312 tools/storage_waterline.py` | §3.5 |
| F6 看板 worker 崩溃 | `hermes kanban --board maa-parallel-benchmark-20260926 diag` | §3.6 |

### 0.4 五个回滚 → 一条命令（详见 §2）

| 对象 | 一条命令 | 生效条件 |
|---|---|---|
| 切号补丁 v2（回 v1） | `Copy-Item '…\AutoProxy.py.bak-accswitch-v1-20260927-195319' '…\AutoProxy.py' -Force` | **+ 重启侧车** |
| 切号补丁 v1（回原版） | `Copy-Item '…\AutoProxy.py.bak-accswitch-20260927-050305' '…\AutoProxy.py' -Force` | **+ 重启侧车** |
| 吃药补丁（待定，未实施） | 未实施无需回滚；模板见 §2.3 | —— |
| 定时接管回滚 | `hermes -p mas cron pause 2600f6415f16 && $PY312 tools/mas_timers.py --enable` | 即时（先停后开） |
| 回滚演练周跑（t_7fa96ea5 接线） | `hermes -p mas cron pause 55337a0a2b62`（停）/ `… remove 55337a0a2b62`（移除，再删包装脚本） | 即时（未改演练本体/生产） |
| 侧车重启（一切代码改动的生效条件） | §2.5 单行命令 | 只在 0 在飞时 |

### 0.5 今日基线快照（2026-09-27 21:30，用来对比「哪里变了」）

```
侧车 main.py PID=33976（started 19:54:12，当日第二次重启后）  Electron AUTO-MAS.exe PID=9724
REST :36163 = LISTEN（owner 33976）  MAA 在飞 = 3（MAA_9/10/11）  MAA 版本 = v6.18.0（全线，09-23 更新）
AUTO-MAS 24 条定时 = 全 Enabled=false（Hermes 接管中）  Hermes cron = 2600f6415f16 发车 + 52b88b0ffca0 巡检（都 active）
调度自检 = OK（alerts 0，lock free）  切号闸门 = VIOLATION:MAA_12（闲置槽的历史残留，见 §4.3，非事故）
磁盘 = 主机 C: 29.7GB WARN / E: 142.7GB；E: 回收站 42.3GB、C:\Users\Public\mumu 导出残件 49GB 待清
```

---

## 1. 系统一图（谁是谁，30 秒扫盲）

```
[主机 MRFZ-0000 100.79.173.69]
  E:\AUTO-MAS\
    ├─ AUTO-MAS.exe (Electron 主进程；--auto-start；管界面+启动侧车)
    ├─ environment\python\python.exe main.py   ← 「侧车」= 真正跑 AdminProxy 注入/调度任务的进程
    ├─ app\task\MAA\AutoProxy.py               ← 切号注入补丁打在这里（v2，L766-779）
    ├─ app\utils\constants.py                  ← 剿灭/剩余理智的基础块（吃药补丁拟改点之一）
    ├─ data\<脚本uid>\{Default\ConfigFile,Temp}\gui.new.json   ← 注入源模板 / 轮起快照
    ├─ debug\app.log                           ← 侧车日志（周轮转，保留 1 月）
    └─ history\<日期>\<账号>\*.log|*.json       ← 每轮结果（账号定因在 .log 里）
  E:\MAA-Multi\MAA_1…MAA_12（+退役 13/14/25/28） ← MAA 槽位（每目录 = 一个脚本的号）
  REST: http://100.79.173.69:36163（无鉴权；调度/巡检/闸门全走它）

[调度机 DESKTOP-L1OEMR3]  D:\1-2\MAAOrch\
  Hermes cron (mas profile)
    ├─ 2600f6415f16 mas-shift-dispatch  */3 5,19,20 * * * → tools/mas_dispatch.py --reconcile --apply
    ├─ 52b88b0ffca0 mas-readonly-inspection every 30m（只读巡检 + 调度对账 + 切号闸门）
    └─ 55337a0a2b62 mas-rollback-drill 0 9 * * 1（周一 09:00）→ tools/mas_rollback_drill.py
         （回滚演练 A–H 周跑；只碰临时副本、班次窗口内自跳、FAIL 落卡+告警；见 docs/ROLLBACK_DRILL.md §5.2）
  runtime\mas_dispatch_state.json / log.jsonl / alerts.json   ← 发车记账
  tools\: mas_dispatch.py / mas_timers.py / mas_switch_gate.py / mas_preflight.py /
          mas_idle_emulators.py / storage_waterline.py / auto_mas_rerun.py / mas_account_identity_check.py
```

**黑话**：侧车 = AUTO-MAS 的 Python 后端（`main.py`）；**重启侧车 = 一切代码类改动的生效开关**；B3 = 并行任务 `t_b26c519d`「机制深挖③：日志判据字典」的产物 `docs/MAA_LOG_DICTIONARY.md`（v1 已落盘 2026-09-27 21:41；附录 A 只是它的速查替身，下结论以字典为准）。

---

## 2. 回滚清单（三个补丁 / 定时接管 / 侧车重启差异）

> 通用：所有 `Copy-Item` 命令的**进程选择与路径已用只读变体（`-WhatIf`）逐一验证**（2026-09-27 21:3x）。文件回滚**不会**立即生效——必须重启侧车（§2.5）。回滚改的只是 AUTO-MAS「注入逻辑」；**账号/队列/计划表一律不动**。

### 2.1 切号补丁 v1（05:03）回滚 = 还原原始 AutoProxy.py

- 打的内容：`AccountName` 赋值后追加 `AccountSwitchEnabled = bool(AccountName)` + 48 个 Default 模板 + 10 个 Temp 快照批量置 true（对应"官方多号串号"事故）。
- 文件：`E:\AUTO-MAS\app\task\MAA\AutoProxy.py`；备份 `AutoProxy.py.bak-accswitch-20260927-050305`（09-24 原件）、`…-v1-20260927-195319`（v1 版本）。

**一条命令（回原版）**：

```bash
$SSH "powershell -NoProfile -Command \"Copy-Item 'E:\AUTO-MAS\app\task\MAA\AutoProxy.py.bak-accswitch-20260927-050305' 'E:\AUTO-MAS\app\task\MAA\AutoProxy.py' -Force\""
```

**验证**（两步）：
1. 代码已回退（一条命令；实测口径：**原版=0 / v1=1 / v2=3** 处标记，可直接比对）：
   `$SSH "powershell -NoProfile -Command \"Select-String -Path 'E:\AUTO-MAS\app\task\MAA\AutoProxy.py' -Pattern 'AccountSwitchEnabled' | Measure-Object | ForEach-Object { 'MARKERS=' + \$_.Count }\""` → 期望 `MARKERS=0`。
2. 重启侧车后跑闸门：`SWITCH_GATE=VIOLATION:<官服槽>` 属**预期**（切号功能已被移除，回到串号风险态）——这不是"修好了"，只是"补丁没了"。**除非推倒重来，否则不要用这一档**；优先用 §2.2。

（可选深层回滚：模板也回到 05:03 前——48 个文件一条命令，文件写 `C:\restore_v1_tmpl.ps1` 后执行：

```powershell
Get-ChildItem 'E:\AUTO-MAS\data\*\Default\ConfigFile\gui.new.json.bak-accswitch-20260927-050305' | ForEach-Object { Copy-Item $_.FullName ($_.FullName -replace '\.bak-accswitch-.*$','') -Force }
```

同型命令把 `Default\ConfigFile` 换成 `Temp` 再跑一遍。已 `-WhatIf` 验证：匹配 48 个、目标路径正确。）

### 2.2 切号补丁 v2（19:53）回滚 = 回到 v1

- 打的内容：注入收窄为 **仅官服** `掩码+true`，B/日/台一律 `""+false`（备份 `…-v1-20260927-195319`）；模板同步回落（`.bak-accswitch-v2-20260927-195319`）。
- **一键回 v1**：

```bash
$SSH "powershell -NoProfile -Command \"Copy-Item 'E:\AUTO-MAS\app\task\MAA\AutoProxy.py.bak-accswitch-v1-20260927-195319' 'E:\AUTO-MAS\app\task\MAA\AutoProxy.py' -Force\""
```

**验证**：
1. `MARKERS` 自查（命令同 §2.1）：回 v1 后期望 `MARKERS=1`（v1 写法；实测计数：原版 0 / v1 1 / v2 3）。
2. 重启侧车后看下一轮注入：官服槽 =`掩码+true`（与 v2 同）；**非官服槽 = `Id+true`（v1 语义，会被闸门报 VIOLATION——这是"v1 回来了"的预期信号，不是新故障）**。
3. 记录：v1 会让 B/日/台拿到账号名注入，与"一实例一号、永不切号"策略冲突 → **尽快重启回 v2 或同步回滚模板**。

> v2 的模板/快照手术回滚（默认不做）：`Get-ChildItem 'E:\AUTO-MAS\data\*\*\gui.new.json.bak-accswitch-v2-20260927-195319' | % { Copy-Item $_.FullName ($_.FullName -replace '\.bak-accswitch-.*$','') -Force }`（Default\ConfigFile 与 Temp 各一遍；`-WhatIf` 先行）。

### 2.3 吃药补丁 —— **待定，尚未实施**（当前无需回滚）

- 现状（21:16 审计定稿）：主因是 46 个 SR-* 关卡账号的「理智作战」自 09-18 起被 MAA 整体跳过（静默），"药物字段配置"本身没问题；修复草案=⓪ 关卡修复（配置类）+ ① 组装处统一 `UseExpiringMedicine=True`（代码类，需重启侧车）+ ② 暴露 UI 开关。见 `docs/audits/2026-09-27_mas_medicine_expiry.md` §6。
- **未实施证据**：主机上无任何 `.bak-med*` / `constants.py.bak*` 备份（21:31 复查为空）。
- 一旦实施，按此回滚（模板）：

| 实施层 | 回滚动作 | 验证 |
|---|---|---|
| ⓪ 关卡（`I.Stage`/`Stage_1..3`，配置类） | 改回原值（审计 §4 有全量原值表） | 下一轮注入即生效；日志恢复 `selected SR-7`→仍是跳过 
| ① 代码（AutoProxy.py / constants.py） | 还原改动前 `.bak` → 重启侧车 | 注入读回 `UseExpiringMedicine` 回旧值；`med` 日志行为复原 |

### 2.4 定时接管回滚（Hermes 发车 ⇄ AUTO-MAS 定时）

现状：AUTO-MAS 24 条定时全 `Enabled=false`（保留 Days/Time），发车由 Hermes cron 承担。

**一条命令（回 AUTO-MAS 定时；先停 Hermes 再开定时，防窗口重叠双发）**：

```bash
hermes -p mas cron pause 2600f6415f16 && $PY312 tools/mas_timers.py --enable
```

**验证**：`$PY312 tools/mas_timers.py --list | grep -c 'Enabled=True'` → `24`；`hermes -p mas cron list` → `2600f6415f16 [paused]`；次日 05:00 / 19:30 看 `history\<日期>` 有新记录（MAS 自发出车）。
**反向（恢复接管）**：`$PY312 tools/mas_timers.py --disable && hermes -p mas cron resume 2600f6415f16`。

> 备注：本组合为**回滚路径**（接管当天未实跑）：两半均单点可用——`cron pause/resume` 已在交接文档流程中用过；`mas_timers --enable/--disable` 源码确认（只改 Enabled，Days/Time 原样保留）。执行后按上面验证。

### 2.5 侧车重启 —— 前后差异 + 一键命令

**为什么重启**：`AutoProxy.py` 等代码在侧车内存里；改了文件必须重启侧车才生效（05:33 / 19:53 两次重启都是这个原因）。

**先查（0 在飞才好重启）**（只读，已验证）：

```bash
$SSH "powershell -NoProfile -Command \"\$sc = Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*AUTO-MAS*main.py*' } | Select-Object -First 1; if (\$sc) { Write-Output ('SIDECAR_PID=' + \$sc.ProcessId + ' START=' + \$sc.CreationDate) } else { Write-Output 'NO_SIDECAR' }; Write-Output ('MAA=' + (@(Get-Process MAA -ErrorAction SilentlyContinue).Count))\""
```

**一条命令（重启；把 `Write-Output` 段换成 `Stop-Process` 即为执行版，选择逻辑已实测同款）**：

```bash
$SSH "powershell -NoProfile -Command \"\$sc = Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*AUTO-MAS*main.py*' } | Select-Object -First 1; if (\$sc) { Stop-Process -Id \$sc.ProcessId -Force; Write-Output ('KILLED=' + \$sc.ProcessId) } else { Write-Output 'NO_SIDECAR' }\""
```

然后：Electron 会**自重拉**侧车（实测 50s~120s 恢复 REST）；120s 未回 → 兜底整机侧：`Stop-Process -Name AUTO-MAS -Force; Start-Sleep 3; Start-Process 'E:\AUTO-MAS\AUTO-MAS.exe' -ArgumentList '--auto-start'`（≤180s 轮询）。

**重启前/后差异表（实测口径）**：

| 面 | 重启前 | 重启后 | 验证 |
|---|---|---|---|
| 代码 | 旧 `AutoProxy.py` 驻留内存 | 新代码生效（v2 注入等） | 下一轮注入读回 + 闸门 |
| 在飞任务 | N 条任务链在推进 | **被静默吞掉**（MAA 不死、AUTO-MAS 不再推进/记账；`app.log` 出现「启动时任务开始运行」；无「已解锁」；锁不释放、Temp 残留） | `history` 断档 + 任务孤儿 → 走补跑策略（运维③ `t_bc75999f`：该不该补） |
| 进程 | 侧车 PID=旧（如 13196） | 侧车 PID=新（如 33976）；Electron PID 不变 | 上面「先查」命令前后各跑一次 |
| REST | 端口 LISTEN owner=旧 PID | 短暂 DOWN → 恢复（owner=新 PID） | 0.1-A 探针 + 端口检查 |
| 不变 | 队列/账号/计划表/定时项/源模板（补丁之外）不受影响；Hermes cron 不受影响（下一跳 ≤3min 照常） | 同左 | `mas_dispatch --reconcile`（dry-run）无异常 |

> **纪律**：重启 = 全局串行操作 + 人工在场；只在班次窗口外做；**绝不 kill MAA.exe / 不动模拟器**（会破坏收尾与配置还原）。若不得不在窗口内重启（如 REST 真挂），重启后必须人工对账：`history` 对比 + 未完成号清单 → 交补跑策略，并给 30 分钟告警观察窗。

---

## 3. 故障处置（症状 → 判据 → 动作 → 验证）

### 3.1 F1：AUTO-MAS REST 挂了（fail-closed）

- **症状**：发车告警 `DISPATCH_ALERT=<队列:rest_unreachable>`；`mas_dispatch.py --check` FAIL；闸门 `UNKNOWN`；巡检报告 MAS 不可达。
- **判据**（0.1-A；故障特征实测：错误端口 → `HTTP 502`（该端口另有服务应答）/ 本地闭口 → `WinError 10061 拒绝连接`；超时=防火墙/死机）：
  - `{"code":200,...}` = 活；异常 = 挂。
- **动作**：按 §2.5 重启侧车（**先加探针确认在飞数**：窗口内带任务重启会吞任务）。120s 未回 → AUTO-MAS.exe 整机侧重启。
- **验证**：0.1-A 回 200 + `mas_dispatch.py --check` OK；若班次窗口内，看 cron 下一跳（≤3min）`mas_dispatch_log.jsonl` 的 `ok` 行；超窗（>30min）按补跑策略（运维③ `t_bc75999f`）处置。
- **注**：调度是 fail-closed 的——REST 挂时**零上游写请求**，不会发半截车。

### 3.2 F2：侧车假死（REST 可能还答，但任务不推进）

- **症状**：任务长时间无进展；`history` 无新记录；巡检/报告说"没跑"但也没报错；`app.log` mtime 停滞。
- **判据**（0.3-F2 一条命令；健康 = mtime≈now 且末行是新鲜请求日志；假死 = mtime 冻结在班次中途，或末行停在某个动作上不动）：
  - 配合交叉：`history\<今日>` 是否断档 + 在飞 MAA 数（REST 不答/账目不推）。
  - 注意区分「班次间隙真安静」（无请求=正常）与「班次中冻结」——只在**窗口内**判假死。
- **动作**：同 F1（重启侧车）。**若任务已被吞**：登记被吞队列/号 → 按补跑策略（运维③ `t_bc75999f`；规则：资源类不补、截断类补一次）处理，不要盲目重发。
- **验证**：重启后 `app.log` 恢复心跳（mtime 变新）；下一轮注入/任务推进正常。

### 3.3 F3：实例 adb 失联 / 假死（MuMu 状态面**不可信**是常态）

- **症状**：MAA 日志 `连接失败/截图失败/游戏掉线`；AUTO-MAS 记「模拟器启动失败」；`adb devices` 里目标实例非 `device`。
- **判据**（三条命令，按序；**只信后两条**）：

```bash
# ① 状态面（仅作参考，今天实测 0/55 在跑 vs 实际 14 台在跑 —— 全机状态面假死）
$SSH "powershell -NoProfile -Command \"& 'E:\MuMu Player 12\nx_main\MuMuManager.exe' info -v all\" | head -c 400"
# ② 真身：VMM 进程表（权威）
$SSH "powershell -NoProfile -Command \"Get-CimInstance Win32_Process | Where-Object { \$_.Name -eq 'MuMuVMMHeadless.exe' } | ForEach-Object { if (\$_.CommandLine -match 'MuMuPlayer-12\.0-(\d+)') { 'VMM idx=' + \$Matches[1] + ' pid=' + \$_.ProcessId } }\""
# ③ adb 真连接（把 <VMM_PID> 换成上一步某实例的 pid：找它的监听端口 → connect → get-state）
$SSH "powershell -NoProfile -Command \"\$ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { \$_.OwningProcess -eq <VMM_PID> } | ForEach-Object { \$_.LocalPort } | Sort-Object -Unique); \$adb='E:\MuMu Player 12\shell\adb.exe'; foreach (\$p in \$ports) { 'P=' + \$p; (& \$adb connect ('127.0.0.1:'+\$p)); (& \$adb -s ('127.0.0.1:'+\$p) get-state); (& \$adb -s ('127.0.0.1:'+\$p) shell getprop sys.boot_completed) }\""
```

  （三条均已按本页原文实跑验证，2026-09-27 21:3x–21:4x：① 返回 55 台 JSON、`is_process_started` 全 false；② 列出 14 台在跑 VMM；③ 对 idx=3 / pid=41016 → `P=5561 / device / 1`、`P=16480 / device / 1`。）
- **动作**（按判型，命令**未实跑**只在演练里打印）：
  - ① VMM 不在跑 → 拉起：`& 'E:\MuMu Player 12\nx_main\MuMuManager.exe' control -v <idx> launch`，轮询 VMM 进程 ≤180s。
  - ② VMM 在跑但 `get-state ≠ device` → `control -v <idx> shutdown` → 等 15s → `launch`；仍不行 → 走 AUTO-MAS 界面重启实例/换实例。
  - ③ **状态面假死（MuMuManager 说没跑，VMM/端口活着）→ 不要 launch！** 以 ③ 实测为准继续用，登记该实例。
- **验证**：`VMM pid 存在` + `get-state=device` + `boot_completed=1`；再跑 `$PY312 tools/mas_preflight.py --queue <该实例所属队列> --skip-screen` → `PREFLIGHT=GO`。

### 3.4 F4：MAA 版本更新后配置不兼容

- **背景**：用户原则=「装机 MAA 永远最新」（版本漂移是常态；例：6.14.0-b2 起对缺键配置做过一次性迁移；6.17.5→6.18 文案变化）。今天全线 v6.18.0（MAA.exe mtime 09-23 21:19）。
- **症状**：更新后 MAA 读不到/重置配置；注入字段被忽略（如 `AccountSwitchEnabled` 行为漂移）；GUI 文案/字段改名；轮次行为异常。
- **判据**：
  1. 版本：`(Get-Item 'E:\MAA-Multi\MAA_6\MAA.exe').VersionInfo.FileVersion` + `gui.log` 头部 `Version vX`；
  2. 配置 diff：该槽 `config\gui.new.json` 与 `data\<sid>\Default\ConfigFile\gui.new.json` 字段对比（尤其 StartUpTask/Fight 的字段名与类型）；
  3. 行为：跑一轮空槽（ScriptConfig/AutoProxy 单号）看注入读回与核心日志。
- **动作**：① 更新前固定动作：备份全量 `gui.new.json`（16 目录）+ `data\*\*\gui.new.json` 模板；② 先更**一个空闲槽**验证一轮（注入+闸门+真实 run），通过后再放开；③ 不兼容时：回退该槽 MAA 版本（保留旧版目录），并把受影响字段（如切号开关）标记停用，上报适配。
- **验证**：`mas_switch_gate --check` OK + 空槽一轮真实跑批成功 + 注入读回字段与口径一致。
- **红线**：不做"全院同时升级"；不在窗口内升级。

### 3.5 F5：磁盘满

- **症状**：`history` 出现 0 字节 json（写盘崩）；实例启动失败/克隆失败；C:/E: 低水位。
- **判据**（一条命令，90s，只读）：`$PY312 tools/storage_waterline.py`（黄/红阈值内置于工具：主机 E:<120/60GB、C:<35/15GB，history>500MB/1000MB，回收站>20GB…）。
- **动作**（按 `docs/audits/2026-09-27_storage_policy.md` §5/§6，**人工拍板后**执行）：可即时回收 ≈143GB——E: 回收站 42.3GB（`Clear-RecycleBin -DriveLetter E`）、`C:\Users\Public\mumu\*.mumudata` 残件 49GB、退役实例 l-3(idx44) 30.5GB（走 MuMu 界面删）、DXCache 5.4GB、退役 MAA_13/14/25/28 2.5GB。所有删除命令**先 `-WhatIf`**、**避开班次窗口**。
- **验证**：复跑 waterline 到 WARN 消失；下一次跑批 `history` json 正常非 0 字节。
- **预置闸门**：建/克隆实例前要求 `E: ≥45GB 且 C: ≥30GB`。

### 3.6 F6：看板 worker 崩溃

- **症状**：任务长时间 `running` 无心跳；`runs` 显示 `crashed` / `gave_up`；worker_output 有 `ModuleNotFoundError` 之类（历史已知：Windows detached worker `ModuleNotFoundError: No module named 'hermes_cli'`，已修复于 `kanban_db_dispatch._module_hermes_argv()`——若**复发**说明该修复被回退/环境变化）。
- **判据**：

```bash
hermes kanban --board maa-parallel-benchmark-20260926 diag          # 活动诊断
hermes kanban --board maa-parallel-benchmark-20260926 runs <task_id> # 该卡的运行史（outcome/error）
# 事件流里的 crashed / gave_up / worker_output 关键字：hermes kanban tail <task_id>
```

- **动作**：① 僵尸占用（长 running 无心跳）→ `hermes kanban reclaim <task_id>`；② `gave_up` → 修复根因后 `hermes kanban promote <task_id>`（或让依赖自动重派）；③ dispatcher 整体不动 → 重启 gateway（dispatcher 常驻 gateway：`hermes gateway status` / `hermes gateway restart`）。
- **验证**：任务回到 `running`/`done`；worker log 无 traceback；`[kanban-worker-exit] rc=0`。
- **注意**：在 worker 会话里执行写类 kanban 命令会被守卫拦住（"child contexts cannot mutate"）——请在普通 shell 里做。

---

## 4. 判定树（现象 → 判据 → 动作 → 验证）

> 判据引用：**B3 日志字典** `docs/MAA_LOG_DICTIONARY.md`（`t_b26c519d` v1 已落盘 21:41；附录 A 只是速查替身，正式下结论以字典 §0/§5 为准）。行号/来源均见当日审计。

### 4.1 没出车 / 发不出

```
现象：到点后无任务、无 MAA 起
├─ mas_dispatch --check FAIL + rest_unreachable ─────────────→ F1（重启侧车）
├─ DISPATCH_ALERT=<队列:target_running|slot_occupied> ────────→ 目标已在跑/槽位被占：正常退避重试；连续 >2 跳看队列是否卡在别的任务
├─ DISPATCH_ALERT=<队列:bad_response|no_taskid> ──────────────→ REST 活着但发车异常：查 AUTO-MAS app.log 尾部；重试
├─ late_beyond_grace（超 30min 追发窗）───────────────────────→ 不自动补发：人工决定当日是否还发（看剩余时间 vs 04:00 重启）
└─ cron 根本没跑（execution 缺失）────────────────────────────→ hermes cron list / status；gateway 存活（F6 同面）
```

### 4.2 出了车但"没结果"

```
├─ history 今日无该号目录 ───────────────────────────────→ 被侧车重启吞掉（F2 表征）或 MAA 没起：对号入座
├─ 有目录但日志静默（json 0 字节）───────────────────────→ 写盘崩：以同名 .log 为准（磁盘 F5）
├─ MAA 记「模拟器启动失败, MAA 未实际运行」───────────────→ F3（实例）；同实例当日≥3 次 → 停、交人工
├─ MAA 记「任务跳过」/「关卡计划内没有当日开放的关卡」────→ 主战斗被跳过（活动关到期）：资源类，**不补跑**；按吃药审计 ⓪ 修关卡
└─ MAA 记「用干员不足」──────────────────────────────────→ 慢性警告（基建换班制造站02）：不阻断
```

### 4.3 号疑似跑错 / 切号类

```
├─ mas_switch_gate --check = VIOLATION:<槽位>
│   ├─ 槽位在飞（该目录有 MAA.exe）→ 真问题：查侧车代码版本（§2.5 先查）＋注入读回；必要时重启侧车（v2 未加载）
│   └─ 槽位闲置（无 MAA）+ 文件 mtime 属旧班次 → 历史残留（下轮注入自愈）；记录不处理
│        （实测 21:32 的 VIOLATION:MAA_12 即此类：mtime=09-26 06:13 的旧文件）
├─ 多号脚本要确认"真的切了" → gui.log 权威行「账号切换 -->> <掩码>」（每实切一次一行；已在目标号则静默）
├─ asst.log 出现「task disabled, pass」→ 切号任务被禁用（开关=false）→ 参见链条审计 §3
└─ 身份核对（串号终极判据）→ $PY312 tools/mas_account_identity_check.py --script <名>（干员名册指纹法）
```

### 4.4 客户端/实例类（卡桌面、模态、掉线）

```
├─ 停在 Android 桌面 ─────────────────────────────────→ 先查是"收尾留机（正常）"还是"卡死"：见 `t_aaacd03c`「现场排查：部分模拟器停在 Android 桌面」；卡死→F3
├─ 「登录被取消」模态 ────────────────────────────────→ StartUp 必失败（实例 13 实锤）：需人工点掉/重启实例
├─ 「开始唤醒」失败（含公告弹窗关不掉，CloseAnno 50 连败）→ 日常抖动：靠重试自愈；真掉链子看是否叠加重启吞任务（F2）
└─ 「游戏掉线，停止任务」/截图失败/重连失败 ────────────────→ 网络/连接抖动：重试即重登；连3次→F3
```

### 4.5 基础设施类

```
├─ REST 不答 ─────────────────────────────────────────→ F1
├─ REST 答但任务不推进 ───────────────────────────────→ F2
├─ 磁盘告警 ──────────────────────────────────────────→ F5
├─ 看板任务悬挂 ──────────────────────────────────────→ F6
└─ 主机整体无响应 → AUTO-MAS 自启任务（AUTO-MAS_AutoStart / AUTO-MAS-Boot-Backend）＋ 04:00 计划重启兜底：走主机重启后按 §2.5 验证
```

---

## 5. 无副作用演练记录（2026-09-27 21:1x–21:3x 实跑）

> 演练原则：**只读探测 + 打印将执行的命令**；未执行任何破坏性动作。演练脚本（本地 scratch）`drill_probe.py`，输出为真实回执。

### 5.1 演练 A ——「REST 挂」

| 步骤 | 实跑结果 |
|---|---|
| 健康探针 | `{"ok": true, "code": 200, "n_scripts": 54, "ms": 1603}` |
| 故障特征（死端口，无副作用） | 36164（该端口另有服务应答）→ `HTTP Error 502`；本地闭口 → `WinError 10061 拒绝连接` |
| 进程面 | `SIDECAR_PID=33976 START=09/27/2026 19:54:12`；`ELECTRON_PID=9724`；`MAA_PROCS=3`；`PORT_36163=LISTEN owners=33976` |
| fail-closed 实证 | 把 `MAAORCH_MAS_API` 指向死端口跑 `mas_dispatch.py --check` → `FAIL … AUTO-MAS 不可达`，退出码 2（零上游写请求） |
| 将执行命令 | §2.5 重启侧车一条命令 + 120s 轮询 + 兜底整机侧 + §3.1 验证三连 |

**结论**：判据可复现；恢复路径与现实一致（侧车自权重拉已被 05:33 / 19:54 两次实战验证）。

### 5.2 演练 B ——「实例假死」

| 步骤 | 实跑结果 |
|---|---|
| 状态面 | `MuMuManager info -v all` 报 **0/55** 台在跑（is_process_started 全 false） |
| 真身 | VMM 进程表实为 **14 台**在跑：idx=3,5,8,11,12,13,14,15,20,23,24,29,38,56 |
| 交叉判定 | 14 台 **全部**命中"状态面假死"（VMM 在跑而 MuMuManager 说没跑）——状态面假死不是异常而是**常态** |
| adb 实测 | idx=3（VMM pid=41016）：端口 5561/16480 → `device` + `boot_completed=1` |
| 将执行命令 | launch / shutdown+launch / 只登记不动作 三分支 + 验证链（§3.3） |

**结论**：**判型一律以后两条为准（VMM 进程 + adb get-state）**；状态面仅供参考——谁要按 MuMuManager 的状态做决策，谁就会误杀在跑实例。

---

## 6. 维护纪律与红线

1. **窗口**：`05:00–08:00` / `19:30–23:00` 禁重启侧车/实例、禁清磁盘；维护窗口=收班后。主机 04:00 / 19:00 计划重启（重启后不续跑，未完成项按补跑策略）。
2. **绝不**：kill MAA.exe（破坏收尾/还原）、动 Electron 主进程（除兜底重启）、改 ScriptConfig/QueueConfig（除受控变更）、并发做两件全局操作（adb/实例/侧车/发车 全局串行）。
3. **一切文件回滚先备份现状**（`Copy-Item x x.bak-$(date +%H%M%S)`），改完回读校验。
4. **判定不靠猜**：日志判据以 B3 字典（附录 A 过渡）为准；状态面（MuMuManager / emulator/status）不可信。
5. **改代码必须重启侧车才生效**；改模板（data\*\*)下一轮注入即生效；改队列/计划表下一轮注入即生效（但只影响下轮，见 MAS_RUNBOOK §3）。
6. 演练/只读探测可随时做；**破坏性动作一律先 dry-run / -WhatIf / 打印命令**。

---

## 附录 A：日志判据速查（B3 字典过渡版）

> B3 正式版已落盘（v1 2026-09-27 21:41）：`docs/MAA_LOG_DICTIONARY.md`——全标记 + 正则 + 噪声清单（§5）。本附录只是现场速查替身；表中"状态"列：`已验证` = 当天真实日志中出现过；`字典` = 已收录进 B3 字典、未在当天日志里亲见。

| 标记（原文片段） | 含义 | 用途 | 状态 |
|---|---|---|---|
| `任务已全部完成！`（gui.log） | 本轮全链成功 | **成功判据**（对比下下行） | 字典 |
| `账号切换 -->> <掩码>`（gui.log） | 核心完成选行+点登录 | **切号权威信号**；每实切一次一行 | 已验证 |
| `task disabled, pass`（asst.log） | 切号任务被禁用（开关=false） | 串号根因信号 | 已验证（历史 25 条） |
| `任务出错: <链>` / `出现错误, 已返回` | 某链失败 | 定因入口（failure taxonomy §2） | 已验证 |
| `本轮任务出现以下错误：` / `任务已完成，但出现错误！` | 收尾错误块 | 失败分类（退出类） | 已验证 |
| `关卡计划内没有当日开放的关卡` + `理智作战: 理智作战 任务跳过` | 主战斗被跳过（静默） | 资源类；不补跑；吃药审计主因 | 已验证 |
| `任务出错: 开始唤醒` → AUTO-MAS 记 `MAA 未能正确登录 PRTS` | 登录/唤醒失败 | 抖动类；靠重试；不单列补跑 | 已验证 |
| `模拟器启动失败, MAA 未实际运行` | 实例级失败 | 先体检实例（F3）再谈补 | 已验证 |
| `游戏掉线，停止任务` / `截图失败` / `重连失败` | 运行中连接断 | 重试即重登；连3次→F3 | 已验证 |
| `可用干员不足` | 慢性非致命（基建制造站02） | 不阻断 | 已验证 |
| `Start/Completed Task Chain: <名>` | 任务链起止 | 进度判读 | 待 B3 |
| `Post actions: …` / `Shutdown called by …` | 后置动作收尾 | 判断是否正常退场 | 待 B3 |
| 噪声（看到不用管）：`Toast 通知不可用`、`Failed to set startup`、更新检查 `NotModified` | —— | 降噪 | 待 B3 |

## 附录 B：相关文档与工具索引

- 调度接管与回滚：`docs/audits/2026-09-27_hermes_takes_over_mas_scheduler.md`
- 切号补丁全链：`docs/audits/2026-09-27_mas_account_switch_chain.md` / `_reset.md` / `2026-09-27_maa_account_switch_history.md`；补丁脚本 `tmp/hermes_bridge/patch_accswitch.py` / `patch_accswitch_v2.py`
- 失败模式库（判据/补跑）：`docs/audits/2026-09-27_mas_failure_taxonomy.md`
- 晚班错误定因（公告弹窗/侧车吞噬）：`docs/audits/2026-09-27_maa_errors_triage.md`
- 吃药（待定补丁）：`docs/audits/2026-09-27_mas_medicine_expiry.md`
- 磁盘策略：`docs/audits/2026-09-27_storage_policy.md`；工具 `tools/storage_waterline.py`
- 班前自检：`tools/mas_preflight.py`（接线建议 `docs/audits/2026-09-27_mas_preflight_wiring.md`）
- 从零恢复演练（重建 SOP / RTO·RPO 实测 / 失败点）：`docs/DISASTER_RECOVERY_DRILL.md`；校验器 `tools/mas_dr_verify.py`
- 工具速查：`tools/mas_dispatch.py`（--list/--check/--reconcile/--alerts）、`tools/mas_timers.py`（--list/--enable/--disable）、`tools/mas_switch_gate.py`（--check/--json）、`tools/auto_mas_rerun.py`（单号补跑）、`tools/mas_idle_emulators.py`（空转实例）
- 常规运维：`docs/MAS_RUNBOOK.md`

---
（本手册为 2026-09-27 并行任务 `t_5a3e6998` 产物；演练部分全部只读，未执行破坏性动作。）
