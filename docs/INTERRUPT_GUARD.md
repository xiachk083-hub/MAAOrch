# INTERRUPT_GUARD · 打断源治理（客户端强更 / 系统重启）

> 卡：`t_634e7685`（2026-09-28）｜工具：`tools/interrupt_guard.py`｜基线：`runtime/interrupt_guard_state.json`
> 判据来源：KB `inc-2026-09-27-evening-orphan`（班次执行窗口红线）、`docs/HERMES_MAS_PLAYBOOKS.md §0.2`（发车点/守卫窗）、
> `docs/MAS_EMULATORS.md`（MuMu 实例↔端口换算）、`docs/MAS_EMERGENCY_RUNBOOK.md`（重启前查在飞数）。

## 0. 为什么有这件东西

2026-09-28 一天里发生两次「跑批被打断」的真实事故形态，此前**没有任何检测**：

1. **04:00 宿主计划重启**（`DailyRestart0400` = `shutdown /r /f /t 30`）落在一轮跨零点的批次中间，
   在跑队列被吞（`MRFZ-0000` 当天开机时刻 = 04:01:18，前一日 19:00 那班跨夜未收尾）。
2. **游戏客户端更新**（版本变化 / 整包重装）会让 MAA 在跑时失败，且事后从 MAA 日志里分辨「更新打断」成本很高。

`tools/interrupt_guard.py` 把这两类「打断源」做成**只读检测**，并把结论折成一行 `INTERRUPT_RISK=`，
供班前自检（preflight）、巡检（cron `mas-readonly-inspection`）、MCP 观察面（`mas_interrupt_guard`）统一采用。

## 1. 快速开始

```bash
# 全量检查（默认只读；末行 INTERRUPT_RISK=；rc: 0=无风险 / 1=有风险 / 2=无法判定）
python tools/interrupt_guard.py

python tools/interrupt_guard.py --version-only          # 只查客户端版本面
python tools/interrupt_guard.py --restart-only           # 只查重启面
python tools/interrupt_guard.py --restart-only --scope host    # 只看宿主（不含本机）
python tools/interrupt_guard.py --idx 13,14              # 版本面只看这些实例
python tools/interrupt_guard.py --json                   # 机器可读（不打印 INTERRUPT_RISK= 行）
python tools/interrupt_guard.py --selftest               # 纯本地规则自检（不连网）
python tools/interrupt_guard.py --record                 # 记录/刷新版本基线（唯一写动作）
python tools/interrupt_guard.py --record --dry-run       # 连基线也不写，只打印将写入什么
```

环境变量：`MAAORCH_SSH_KEY`（默认 `~\.ssh\maaorch_target`）、`MAAORCH_SSH_HOST`（默认 `Administrator@100.79.173.69`）、
`MAAORCH_ADB`（默认 `E:\MuMu Player 12\shell\adb.exe`）、`MAAORCH_IG_STATE`（基线文件覆盖，演练用）。

耗时实测：全量 **≈12s**（2 台运行中实例）；版本面随运行中实例数线性增长，`--max` 默认 6。

## 2. 输出契约

文本模式末行恒为：

```
INTERRUPT_RISK=无 | 客户端更新 | 系统重启 | 客户端更新+系统重启 | UNKNOWN:<原因>
```

- `UNKNOWN:` ⇒ 宿主 ssh 不通 / PowerShell 探测无 `IG-END`（**fail-closed**，不当「无风险」用；rc=2）。
- 退出码：`0`=无风险；`1`=有风险；`2`=无法判定。
- 双面都查（版本 + 重启）；`--version-only` / `--restart-only` 单面时另一面记 `null`，不影响行格式。

## 3. 判据表

### 3.1 客户端版本面（版本变化 ⇒ 告警 + 建议暂停该实例发车）

- 取数：宿主 VMM 进程（`MuMuVMMHeadless.exe --comment MuMuPlayer-12.0-<idx>`）→ 监听端口 → `adb connect` →
  `pm list packages`（过滤 `arknights|hypergryph|yostar|txwy`）→ `dumpsys package <包>` 的
  `versionName` / `lastUpdateTime` / `firstInstallTime`。
- 比对：`runtime/interrupt_guard_state.json`（`schema=maaorch.interrupt_guard/1`，键 `"<idx>|<包名>"`）。
- 结论：
  | 状态 | 含义 | 处置 |
  |---|---|---|
  | `same` | 与基线一致 | 无 |
  | `changed` | versionName 或 lastUpdateTime 变化 | **建议暂停该实例发车**，人工确认可正常进游戏后 `--record` 刷新基线 |
  | `new` | 无基线（首次见到） | 建议 `--record` 建基线（不阻断） |
  | `unreadable` | 实例非 `device` / 无 ARK 包 / 读不到 versionName | 记录，不阻断（下次再查） |
- **基线只在 `--record` 时写**；`--dry-run` 连它也不写。`MAAORCH_IG_STATE` 可把基线指到别处（演练用，见 §6 演练记录）。

### 3.2 重启面（计划任务 + 待重启标记 + 在飞 MAA）

数据（宿主经 ssh、本机直跑，都用 `powershell -EncodedCommand`）：
`Get-ScheduledTask`（筛「action 命中 `shutdown` 或 `Restart-Computer`，或名字含 Restart/Reboot」）→ `Get-ScheduledTaskInfo`
（NextRunTime/LastRunTime/LastTaskResult，**属性名固定英文**，不用本地化 `schtasks` 输出）、
`PendingFileRenameOperations`、CBS `RebootPending`/`RebootInProgress`、WU `RebootRequired`、`Win32_OperatingSystem.LastBootUpTime`、
`Win32_Process` 里 `MAA.exe` 计数（在飞）。

判定（`restart_evaluate()`，阈值可调）：

| # | signal | 触发 | 等级 | 建议 |
|---|---|---|---|---|
| 1 | `pending_reboot` | PFR>0 或 CBS/WU 标记存在 | 风险 | 升级可能随时落地；跨重启的长任务先别发 |
| 2 | `restart_imminent` | 下一次重启 ≤ `--block-min`（默认 15 分钟） | 风险 | 现在发车必被吞 |
| 3 | `restart_soon_active` | 下一次重启 ≤ `--soon-min`（默认 45 分钟）**且宿主在飞 MAA>0** | 风险 | 在飞任务会被吞（09-28 04:00 事故形态） |
| 4 | `window_overlap` | 重启时刻落在班次窗口（`05:00–08:00` / `19:20–23:00`，含 `--window-lead-min` 默认 10 分钟提前量） | 风险+建议 | **建议把该计划任务挪出班次窗口（只建议，不改计划任务）** |
| 5 | `wu_reboot_channel` | WU「装完更新自动重启」通道任务处于启用态（按名：`Reboot_AC`/`Reboot_Battery`/`sih`/`sihboot`；含 `MusNotification.exe /RunOnAC|RunOnBattery RebootDialog` 型 action） | 提示（advisory） | 更新装完会自动重启，视情况暂停发车（不单独算风险，但会印在 `提示：` 里） |
| — | 其余 | 例：重启在 9 小时后、无在飞、无标记 | 无 | 显示「最近重启时刻」仅作参考 |

- 班次窗口口径 = KB 执行窗口（05:00–08:00 / 19:30–23:00）∪ 发车点/守卫窗（05:00–05:33 / 19:30–20:03 / 05:00–05:40 / 19:20–20:20）
  ⇒ `05:00–08:00 / 19:20–23:00`。现有 `DailyRestart0400`（04:00）与 `DailyRestart1900`（19:00）**都不在窗口内**（=按设计的安全窗），
  所以正常情况下这条 signal 不会每天误报；一旦有人把窗口改到 05:xx/19:3x 才会亮——这正是它要抓的。
- `Disabled` 的任务不参与判定（只显示）；`restart-imminent/soon_active` 只看**启用中**且 action 是真正重启机器的任务。

## 4. 接线位置

| 位置 | 形态 | 说明 |
|---|---|---|
| `tools/mas_preflight.py` | 第 7 项 `client_version`、第 8 项 `host_restart` | `version_check()` / `restart_check()` 直接 import 复用；映射见下 |
| `tools/mas_observe_mcp.py` | MCP 工具 `mas_interrupt_guard` | 只读工具（无 record），返回 `{ok, interrupt_risk, risk_line, reasons[], version{}, restart{}}` |
| 巡检 cron `52b88b0ffca0` | 第二步（发车前闸）+ 第三步第 7 项 + 第六步主表第 12 行 | `INTERRUPT_RISK=` 行，无风险也写；非「无」⇒ 只告警、不发车 |

preflight 映射（第 7 项）：

- `changed` ⇒ **FAIL**（NO-GO，暂停该实例发车）；`new`/`unreadable` ⇒ WARN；目标实例全未运行/读不到 ⇒ **SKIP**（不阻断）；
  单面探测异常（ssh 不通/脚本缺失）⇒ FAIL（fail-closed）。

preflight 映射（第 8 项）：

- `restart_imminent` / `restart_soon_active` ⇒ **FAIL**（暂停发车）；`pending_reboot` / `window_overlap` ⇒ **WARN**（只提示）；
  宿主探测失败 ⇒ FAIL。

## 5. 只读保证 / 不碰清单

- **不启动、不关闭实例**；不点击、不按键、不动客户端；**不改计划任务**（窗口只建议，改由人工）；
  不发车；不写远端；不落盘到远端。
- 唯一写动作 = `--record` → 本机 `runtime/interrupt_guard_state.json`（原子写：`.tmp` → `os.replace`；`runtime/` 已 gitignore）。
- adb 只做 `connect` + `get-state` + `pm list packages` + `dumpsys package`（读）；与产线同用 adb 时属于轻量只读，
  但**契约定死：巡检轮内对同一实例不重复调用**（巡检预算 `interrupt_guard ≤1`）。

## 6. 实测证据（2026-09-28，全部真跑）

证据目录：`docs/audits/2026-09-28_interrupt_guard/`

| 文件 | 内容 | 关键结论 |
|---|---|---|
| `01_selftest.txt` | `--selftest` | 30/30 PASS（窗口/任务识别/解析/比对/评估/落盘） |
| `02_check_first.txt` | 首跑全量 | 2 台在跑实例（idx14 官 2.7.71 / idx25 JP 36.7.22）无基线；宿主重启 2 条（04:00/19:00，均 Ready）；`INTERRUPT_RISK=无` |
| `03_record_dryrun.txt` | `--record --dry-run` | 打印「将写入 2 项」，**未落盘**（`ls` 证实无 state 文件） |
| `04_record.txt` | `--record` | 基线建立（state 文件 695B，2 条），`first_seen/recorded_at` 落库 |
| `05_check_after.txt` | 复跑 | 2 项全部 `same`，`INTERRUPT_RISK=无`，rc=0 |
| `06_drill_alert_path.txt` | 告警路径演练（全干跑） | 版本面：伪造基线 ⇒ `客户端更新` + 逐条建议，**真实基线未动**；重启面：窗口交叠 / 临近+在飞 / 即刻 / 待重启标记 4 形态 ⇒ 均 `系统重启`，平静态 ⇒ `无` |
| `07_preflight_live.txt` | preflight（idx13 未运行） | 第 7 项 SKIP（不阻断）、第 8 项 PASS；`PREFLIGHT=GO` |
| `07b_preflight_version_pass.txt` | preflight（idx14 在运行） | 第 7 项 PASS `idx14 com.hypergryph.arknights=2.7.71`（与基线一致） |
| `07c_preflight_version_fail_drill.txt` | preflight + `MAAORCH_IG_STATE=<伪造基线>` | 第 7 项 FAIL ⇒ `PREFLIGHT=NO-GO`（rc=1），真实基线 sha256 复核未变 |
| `08_mcp_call_probe.txt` | 无头 MCP 客户端 `tools/list` + `tools/call` | 工具面 6 个；调用返回 `ok=true, risk_line=INTERRUPT_RISK=无`，13.2s |
| `09_patrol_prompt_diff.txt` / `09a` / `09b` | 巡检 prompt 改动 | 8 处锚点断言改动；落库后回读 sha 一致（`5db65feecc096c81`），job 仍 enabled、next run 正常 |

复跑（只读）：

```bash
python tools/interrupt_guard.py --selftest
MAAORCH_IG_STATE=<任意伪造基线.json> python tools/interrupt_guard.py --check   # 演练（不碰真实基线）
python tools/mas_preflight.py --script 明日方舟-官-2-4-47 --skip-screen
python tools/mas_observe_mcp.py --check
```

## 7. 已知限制 / 未做（交后人）

- **Windows Update 的安装体 / 起更**不单独查（查 `RebootRequired` + 完成重启通道 `Reboot_AC/Battery/sih/sihboot` 的启用态；
  后者只作提示级）；「起更」与 AU 计划不在本工具判据里。本机实测（`13_wu_tasks_probe.txt`）：`Reboot_AC/Battery=Disabled`、无 `sih/sihboot`。
- **强更前置信号**（商店/官方公告）不查：本工具只认「包已变」这一既成事实。
- **本机 #0（用户 JP 机）不做版本面**：红线是不碰用户自己的实例；版本面只管宿主实例。
- **服务器侧「该发车队列目标含哪个实例」的粒度**：preflight 是按目标实例 idx 查版本；巡检是全局风险；两者口径已在 §4 写明。
- **`--record` 只在实例运行时可记录**：未运行实例下次被见到时是 `new`（提示建基线），属预期。
- 巡检 job 的 `INTERRUPT_RISK=` 行在工具面滞后时会写 `UNKNOWN:工具面未提供`（job 的 fail-open 口径），
  **不**把 UNKNOWN 当「有风险」处理；工具面刷新（`hermes mcp config` 关→开）后自然恢复。
- 窗口只建议、**不改计划任务**：改窗口要走人工（宿主计划任务属变更动作）。
