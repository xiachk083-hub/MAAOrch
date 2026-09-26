# 调度器（叫号模型）设计文档

> 2026-09-18 起。实现：`tools/scheduler.py`（默认 dry-run）。
> 覆盖：任务模型 / 状态机 / 编号 / 叫号 / 三道闸 / 肉鸽打断 / 崩溃对账。

## 0. 它和 AUTO-MAS 内置调度的根本区别

**MAS 内置调度崩了就忘；这个调度器记得。** 崩在半路的任务靠 history 对账找回来
（`running` → `done` 或 `pending`），这是它存在的第一理由。

## 1. 任务模型

| 任务 | 优先级 | 窗 | 完成判据（读 `data/daily_state/<uid>.json`） | next_run |
|---|---|---|---|---|
| event | 1 | 活动期 | 关卡推完（**无判据时默认 disabled**） | 手动 |
| infrast | 3 | 全天 | `infrast_done` | 完成 + 12h |
| stamina | 5 | 全天 | `stamina_cleared` | 完成 + 上限/10h |
| annihilation | 7 | 周一~周日 | `annihilation_done` | 下周一 04:00 |

肉鸽**不是任务，是背景**（长任务、可中断）。

> ⚠️ `event` 无 `done_when` 时**必须 disabled** —— 否则永远 pending，把队列淹掉（实测：16 号里 event 全 pending，待叫号 34→18 才正常）。

## 2. 状态机

```
disabled → waiting → pending → running → done
                                     ↘ failed → pending（重试 ≤3，5 分钟后）
running → pending  # 只走"崩溃对账"这条路（被打断，history 无完成记录）
```
非法迁移**抛错**，不静默改状态。

## 3. 编号

`YYYYMMDDHH-NNN`（跨小时重置），落 `data/schedule/id_counter.json`，永久保留。

## 4. 叫号

```
有效优先级 = 基础优先级 − 等待小时数 × 0.5      # 等得越久越靠前，防饿死
next_run = 完成时间 + 周期（不是上次时间 + 周期） # 避免漂移
next_run += random(−30min, +30min)              # 只加一次，避免所有号同一分钟涌上来
```
触发时机是**"有空位"**，不是"时间到了"。

### 4.1 三道闸（任一不过 ⇒ 不派）

| 闸 | 条件 | 为什么 |
|---|---|---|
| ① | `MAA = 0` | 机器刚起 / AUTO-MAS 未在跑；它自己会把启动队列跑起来，别叠加 |
| ② | 无采样 | 保守不派 |
| ③ | **`uptime < 30 分钟`** | 刚崩完重启，AUTO-MAS 正在爬坡（闸①不够：MAA 会从 0 爬到满，中间几次采样 `MAA=4 NxDevice=5` 会算出"6 个空位"） |

空位 = `并发上限 − max(NxDevice, adb)`（NxDevice 主口径；adb 是副口径 —— adb server
重启后连接表为空会低估）。

### 4.2 设计决定（2026-09-18 用户拍板，勿改）

**重启后的第一轮，仍由 AUTO-MAS 自己的启动队列跑；调度器只负责白天补漏。**
- 阈值保持 30 分钟，调度器**不接管"开机后的第一批"**
- 理由：19:00 计划重启 → 19:30 晚班，开机时长正好 30 分钟；若调度器接管会互相打架
- **两个系统各管一段**：AUTO-MAS 管"重启后起步"，调度器管"白天补漏与对账"

## 5. 肉鸽打断/恢复

```
任务要跑 → 有空位？
  有 → 直接跑（不关肉鸽）
  无 → 关 1 台（运行最久那台）→ 叫号进 1 个
恢复：任务队列空了才恢复（不是跑完一个就恢复）
```

- 选台：路径含 `maa-alas` 的 `MAA.exe` 按 `CreationDate` 升序取最老
- 映射 `l-N → 实例号`：**从 `_autostart_l.ps1` 的 `$names`/`$emus` 读，不写死**
- 关：`Stop-Process -Id <pid> -Force`（**按 PID，不用进程名**）+ `MuMuManager control -v <N> shutdown`
- **自查**：`MuMuManager info -v <N>` 的 `is_process_started` 必须为 false
- 恢复：`powershell -File E:\maa-alas\fz-maa\_autostart_l.ps1`
- 默认 dry-run；`--live` 才执行；**`--live` 同样受闸③ 约束**（刚崩完不许打断）

实测观察（2026-09-18）：崩溃重启后**肉鸽会自己起来抢机器**（3 台跑了 10 分钟）——
这正是闸③ 的现实依据。

## 6. 崩溃对账

```
启动时：所有 state==running 的任务 → 查 history
  ├─ 有完成记录 → done（它跑完了，只是没来得及写）
  └─ 无记录     → pending（被打断，重新排队）
waiting / done / disabled 不动。
```
MAA 任务**没有断点续跑**（基建换一半 = 没换）⇒ 对账只有两个结果。

> ⚠️ 比较时间**必须转 datetime**：`"…05:00:00"` 与 `"…T05:00:00"` 在字符串层面
> 不等价（空格 0x20 < 'T' 0x54）⇒ 恒 False ⇒ 把跑完的任务全判成 interrupted ⇒ **重复执行**。

## 7. 纪律（本模块相关的）

1. 杀进程按**可执行文件路径/PID**限定，不用进程名
2. 关完**自查**（`is_process_started`）
3. **dry-run 默认**，`--live` 才真执行
4. **先写 `running` 到磁盘，再派发**（否则崩在半路时磁盘上还写着 pending，对账无从下手）
5. **安全闸必须 fail-closed**：闸的判据"取不到"时要**拒绝**，不是放行。
   实测事故（2026-09-18）：`if up is not None and up < 30` ⇒ uptime 探测失败时
   **静默放行**，`--live` 在开机 19 分钟（爬坡期）真的杀了 l-1 的 MAA、关了实例 25。
6. **从 SSH 起 GUI 程序无效**（session 0）：肉鸽恢复不能直接调 `_autostart_l.ps1`，
   要走登录任务 `schtasks /run /tn fz-maa-l-autostart`（实测：直接调时脚本
   `autostart done` + `exit=0`，但一台模拟器都没起来）。
7. **外部进程调用要看 stderr**：`powershell -File` 不带 `-ExecutionPolicy Bypass`
   会被策略拦掉，错误只进 stderr ⇒ 只看 stdout 会以为"跑过了"（实测：脚本日志一行没新增）。

## 8. 自检

`python tools/scheduler.py --selftest` —— 9 条断言：状态机（合法/非法迁移）、防饿死、
编号跨小时重置、next_run 不漂移、三道闸、对账时间比较。
**自检不得写真实状态**（计数器/采样日志指向临时目录；踩过：第二次跑必然 FAIL）。

## 9. 现状与未做

- ✅ 1-6（任务模型/状态机/编号/叫号/stamina 闭环/主循环）、8（对账）、9（空位+三道闸）、肉鸽 dry-run
- ⬜ 肉鸽 `--live`（命令已确认，等窗口）
- ⬜ 其他任务类型（infrast / annihilation / event）的闭环
- ⬜ 属性：`expected_duration` / `queue_wait` / `trigger_delay` 等可观测字段
- ⬜ 截图存储（上限/元数据）—— 注意主机 E: 只剩 ~48GB，配置里写的是 40GB 不是 100GB
