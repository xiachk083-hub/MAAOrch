# 审计：打断源治理（INTERRUPT_GUARD）落地 — 卡 t_634e7685

- 时间：2026-09-28 09:45–10:35（UTC+8）｜执行：mas（本机 DESKTOP-L1OEMR3）
- 目标：跑批被「客户端强更 / 系统重启」打断的两类风险，做成只读检测 + 一行 `INTERRUPT_RISK=`，
  接进 preflight、MCP 观察面与巡检 job。
- 约束（卡原文）：只读检测；不改计划任务、不动客户端；告警先 dry-run。
- 证据目录：`docs/audits/2026-09-28_interrupt_guard/`（12 个文件，全部真跑留痕）

## 1. 结论

四件产物全部落地并**实跑验证**：

1. **新工具** `tools/interrupt_guard.py`（只读，约 640 行）：`--check/--record/--json/--selftest/--dry-run`，
   文本末行恒为 `INTERRUPT_RISK=无|客户端更新|系统重启|客户端更新+系统重启|UNKNOWN:<原因>`；rc 0/1/2。
2. **preflight 两项新检查**：第 7 项 `client_version`、第 8 项 `host_restart`（`tools/mas_preflight.py`）。
3. **MCP 新工具** `mas_interrupt_guard`（`tools/mas_observe_mcp.py`，只读面第 6 个工具；关→开强制重连已做）。
4. **巡检 job 52b88b0ffca0 提示词**：主表加 `INTERRUPT_RISK=` 行（12 行 → 13 行）、发车前加打断源闸、预算 19→20。

## 2. 逐条改动 + 为什么

| # | 文件 | 改动 | 为什么 |
|---|---|---|---|
| 1 | `tools/interrupt_guard.py`（新建） | 版本面 + 重启面双检测；末行 `INTERRUPT_RISK=`；`--record` 为唯一写动作（本机基线） | 卡要求「一行机读结论」+「只读」；镜像既有 `SWITCH_GATE=` 约定（`tools/mas_switch_gate.py`）便于巡检/预检统一解析 |
| 1b | 同上 `WU_CHANNEL_RE` / `is_restart_task` | 补上 WU「装完更新自动重启」通道：`Reboot_AC/Battery`（`MusNotification.exe /RunOnAC|RunOnBattery`）按重启类任务识别、启用态出提示级 signal；报告打印 `WU 完成重启通道：…` 行 | 首次实现只认 `shutdown /r` 与 `Restart-Computer`，**漏掉 WU 自家重启通道**（上一轮 attempt 正是查到 `UpdateOrchestrator\Reboot` 时被中断的）；实测宿主 `Reboot_AC/Battery=Disabled`、无 `sih/sihboot` ⇒ 现状安全，但改启用后必须能被抓到 |
| 2 | 同上 `_PS_RESTART` / `_PS_VERSION` | 远程 PowerShell 一律走 `-EncodedCommand`（UTF-16LE base64）+ `chcp 65001` | 实测 stdin 路线（`powershell -Command -`）对稍大脚本**静默返回空**（`out_len=0`），`-EncodedCommand` 两次都通（dbg1 vs p1b 对照） |
| 3 | 同上 `SHIFT_WINDOWS` | 班次窗口取「KB 执行窗口 ∪ 发车点/守卫窗」= `05:00–08:00 / 19:20–23:00`（提前量 10 分钟） | KB `inc-2026-09-27-evening-orphan` 红线写的是执行窗口 05:00–08:00 / 19:30–23:00，比 PLAYBOOKS §0.2 的发车点窗更宽；现有 04:00/19:00 两个计划重启**不在窗内**，所以该 signal 不误报、只抓「有人把窗口改进班次」 |
| 4 | 同上 `restart_evaluate()` | 四级 signal：待重启标记 / ≤15 分钟即刻 / ≤45 分钟且宿主在飞 MAA>0 / 落班次窗口 | 「重启前查在飞数」本是 `MAS_EMERGENCY_RUNBOOK` 的人工纪律（KB 引用 L173-177），这里做成自动判据；`window_overlap` 只出建议不改计划任务（卡明确「只建议」） |
| 5 | 同上 `version_check()` / `version_compare()` | 运行中实例 `dumpsys package` 的 `versionName/lastUpdateTime` 与基线比对；`new/changed/same/unreadable` 四态 | 强更/整包重装无法事前感知，但**包已变**是硬事实；`changed` ⇒ 建议暂停该实例发车，避免半途更新打断 |
| 6 | 同上 `--record` / `MAAORCH_IG_STATE` | 基线写 `runtime/interrupt_guard_state.json`（原子写），env 可覆盖 | 只读工具需要一个「已知好版本」参照；env 覆盖让**演练不碰真实基线**（07c 已验证 sha 未变） |
| 7 | `tools/mas_preflight.py` | 新增 `guard_module()` + 第 7/8 项检查 + 输出两行 | 班前 GO/NO-GO 要能拦住「现在发车会被吞」；映射：版本 changed ⇒ FAIL，重启 imminent/soon_active ⇒ FAIL，其余 WARN/SKIP |
| 8 | `tools/mas_observe_mcp.py` | 新工具 `mas_interrupt_guard`（只读、无 record）+ `_KNOWN_TOOLS` + `_check` 的 CLI/selftest 列表 | 巡检 job 只有 MCP 工具面；工具描述里**不出现被禁字样**（`_check` 的只读断言含 `shut`+`down` 拼接词），已过 `--check` |
| 9 | 巡检 job `52b88b0ffca0` prompt | 8 处锚点断言改动（见 `09_patrol_prompt_diff.txt`）：预算 19→20、加 `interrupt_guard ≤1`、设计面加 `mas-observe 6`、六检→七检、第二步加「补位发车前两道只读闸」（preflight + interrupt_guard，非「无」不发车只告警）、第三步第 7 项、主表 12→13 行 | 卡要求「巡检输出加 INTERRUPT_RISK= 行（无风险也写）」+「班前/巡检要能拦住发车」；order 上把检测放第二步真发车前，第七项引用同轮结果（预算 ≤1 次） |

## 3. 证据表（全部本轮真跑）

| 证据 | 命令 | 关键结果 |
|---|---|---|
| 自检 | `interrupt_guard.py --selftest` | 30 项 PASS（窗口 4 / 任务识别 4 / PS 解析 4+2 / 比对 4 / 评估 6 / 汇总 3 / 落盘 2） |
| 首跑 | `interrupt_guard.py` | 在跑 2 台（idx14 官=2.7.71、idx25 JP=36.7.22）均无基线；宿主 `DailyRestart0400/1900` NextRunTime 04:00/19:00、State=Ready、rc=0；待重启标记=无；在飞 MAA=1；`INTERRUPT_RISK=无`；12.4s |
| 建基线（dry） | `--record --dry-run` | 「将写入 2 项」且未落盘（`ls` 无文件） |
| 建基线（真） | `--record` | state 695B；`firstInstallTime`（官 2025-04-12 / JP 2025-04-12）也入库 |
| 复跑 | `interrupt_guard.py` | 2 项 `same`，`INTERRUPT_RISK=无`，rc=0 |
| 告警演练（版本） | 伪造基线 + `--check` | `客户端更新` + 逐条「建议暂停该实例发车」，真实基线未动 |
| 告警演练（重启） | 合成 survey × 5 | 窗口交叠/临近+在飞/即刻/待重启标记 ⇒ `系统重启`；平静态 ⇒ `无` |
| preflight（未运行实例） | `mas_preflight.py --script 明日方舟-官-1-1-7 --skip-screen` | 7 SKIP（不阻断）、8 PASS，`PREFLIGHT=GO` |
| preflight（运行中 + 真基线） | `--script 明日方舟-官-2-4-47`（idx14 UP） | 7 PASS `idx14 com.hypergryph.arknights=2.7.71` |
| preflight（FAIL 演练） | 同命令 + `MAAORCH_IG_STATE=伪造基线` | 7 FAIL ⇒ `PREFLIGHT=NO-GO` rc=1；真实基线 sha 复核未变 |
| MCP 工具面 | `hermes mcp test mas-observe` | Connected，Tools discovered = 6（含 `mas_interrupt_guard`） |
| MCP 真调用 | 无头客户端 `tools/list` + `tools/call` | 6 工具；`ok=true, risk_line=INTERRUPT_RISK=无`，13.2s（13.2s 含 3 台实例 adb 读） |
| 巡检 prompt | `hermes -p mas cron edit 52b88b0ffca0 --prompt <after>` | rc=0；回读 sha 一致 `5db65feecc096c81`（5441 chars）；job 仍 enabled、next run 正常 |
| 收尾回归 | `10_final_check.txt` / `11_record_widen.txt` / `12_postfix_verify.txt` | 冻结态复跑：selftest 30/30、check `INTERRUPT_RISK=无` rc=0（此轮 5 台在跑实例全有基线，无多余提示）；`--record` 把基线扩到 5 台（idx2/14/23/25/45）；`--record --dry-run` 报「将写入 5 项」不落盘 |

## 4. 可复跑命令（只读）

```bash
PY="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"
$PY tools/interrupt_guard.py --selftest
$PY tools/interrupt_guard.py                      # 末行 INTERRUPT_RISK=
$PY tools/interrupt_guard.py --json | head -40
MAAORCH_IG_STATE="C:/Users/.../drill_baseline.json" $PY tools/interrupt_guard.py   # 演练（不碰真实基线）
$PY tools/mas_preflight.py --script 明日方舟-官-2-4-47 --skip-screen
$PY tools/mas_observe_mcp.py --check
hermes -p mas mcp test mas-observe
```

## 5. 未做 / 待定（交后人）

- 宿主 Windows Update 的「起更 / AU 计划」不在判据里（只查 `RebootRequired`）。
- 强更的前置信号（公告/商店版本）不查；本工具只认「包已变」。
- 巡检 job 下一轮（`next_run_at=2026-09-28T10:57:18`）的实际输出应出现 `INTERRUPT_RISK=…` 行——**本轮交付时尚未到点**
  （为催一轮，10:29 用 `hermes cron run 52b88b0ffca0` 登记过一次 fire_claim，但 10:34 检查时该轮仍未执行）⇒ 请以 10:57 那轮输出为准复核；
  若显示 `UNKNOWN:工具面未提供`，说明该会话拿到的还是旧工具面，按上面的关→开强制重连即可刷新
  （`hermes -p mas config set mcp_servers.mas-observe.enabled false` → `true`；本会话已完成一次，`hermes mcp test` 实测 6 工具）。
  提醒：只读工具面在跑会话里的刷新滞后**只影响该轮巡检的 INTERRUPT_RISK 行**（会写 UNKNOWN），不影响 preflight/CLI 两个入口。
- `--record` 只覆盖「记录时在运行」的实例；未运行实例下次被读到是 `new`（提示建基线），属预期，不是故障。
- 改宿主计划任务的窗口动作**未做也不该做**（只建议）——若要挪，走人工变更。

## 6. 我没碰哪些文件 / 红线自查

- 未改：宿主 `DailyRestart0400/1900` 等任何计划任务、`E:\AUTO-MAS` 配置、任何 MAA 配置、实例开关机、客户端（未点击未按键）。
- 未改：`tools/mas_dispatch.py`、`tools/mas_switch_gate.py`、`tools/maa_error_daily.py`、`tools/mas_idle_emulators.py`、
  `tools/client_state_check.py`、`tools/mas_identity_trace.py`、`runtime/_prime_queue3.py`、`tools/maaorch_service.py`（8765 前门）。
- 本机 #0（用户 JP 机）未被探测：版本面只查宿主实例。
- 写动作清单（全部有备份/复核）：① `runtime/interrupt_guard_state.json`（新文件，工具自身基线）；
  ② 巡检 job prompt（改前 `cp -p` 备份到 `scratch/interrupt/jobs.json.bak-0928_1018`，改后回读 sha 核对）；
  ③ 本仓库新增 `tools/interrupt_guard.py`、`docs/INTERRUPT_GUARD.md`、本审计 + 证据目录。
- 凭据：全程未落任何密钥/口令/账号明文；账号一律掩码（`134****5276`）。
