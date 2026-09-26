# 推图（push）— 明日方舟活动推图的最小闭环

> 2026-09-18 从沙箱收进仓库。**收进仓库的唯一理由**：以前 `auto_push.py` 只活在沙箱里，
> 改代码时经常分不清"改的是仓库那份还是沙箱那份"——结构性问题，已经吃过亏。

## 这是什么

| 层 | 谁负责 |
|---|---|
| **战斗** | MAA（`tools/maa_copilot_run.py` 起 MaaCore：编队 + 按作业逐帧落子） |
| **战斗之外的一切界面** | 本目录（导航进活动/找关/进详情页、清弹窗与结算、按 box 算用哪份作业、记状态、失败归类） |

三个号/三台模拟器 → 三条网页通道（8890/8891/8892），每台一个 MAA 实例（1/2/3）。

## 怎么跑

```bash
# 1) 读干员 box（身份从**画面**的 RHODES ID 读，与 --uid 不符就停手不写）
python tools/scan_box.py --emu 2                     # 或 tools/box_read_when_free.py --emu 2 --uid <UID>

# 2) 筛作业（按 box 算缺几个）
python tools/list_jobs.py --stage act54side_ex07 --box data/boxes/box_751737728.json

# 3) 人把游戏点到那一关的详情页，然后执行指定作业（或作业站的码）
python tools/run_job.py --emu 2 --job data/copilot/<pool>/act54side_ex07_102448.json
python tools/run_job.py --emu 2 --job maa://102448      # 码直跑：拉临时文件 → 跑 → 删

# 4) 开网页控制台（每个账号一条通道）
python ui/control_server.py --emu 1 --account 394162955 --pool event54_d --port 8891
```

网页：`/` 富页面（状态网格 + 实时画面 + 日志）｜`/mini` 极简页（候选 + ▶）。
每关下面一排 `▶ 作业号` = 直接让 MAA 跑那一份；`🔍 搜` = 现场去作业站搜（点才搜）。

## 文件在哪

```
tools/push/          ← 本目录：推图引擎（auto_push.py）+ 校准（calibrate.py）+ 脱离启动（_spawn.py）
tools/push/push_state_<UID>.json   ← **唯一事实源**：每关状态（三星/失败/未确认/未解锁/跳过）
data/copilot/<pool>/               ← 作业池（按来源分目录；同内容跨池会按内容去重）
data/operbox_<UID>_<时间>.json     ← box（**文件内带 uid 字段**，用前校验）
data/boxes/box_<UID>.json          ← scan_box.py 的落点（同上内容，另一种布局）
ui/prefs.json / ui/lastrun.json    ← 页面开关 / 横幅状态（**落盘**，刷新与重启都不丢）
data/_trash/                       ← 被判定"挂错号"的旧 box（**只移不删**，可回捞）
```

## 谁是谁（一张表，别再挪文件）

| 角色 | 文件 | 说明 |
|---|---|---|
| **MVP 本体** | `tools/scan_box.py` / `tools/list_jobs.py` / `tools/run_job.py` | 扫 box、筛作业、跑指定作业 |
| **入口** | `ui/control_server.py` 的 `/mini` 页面 | 账号 + 候选 + 每行一个 ▶ |
| **可选上一层** | `tools/push/auto_push.py` | 带自动导航 + 状态机 + 状态回写（不是 MVP 本体） |

> 目录为什么这么放：**挪文件要改引用，今晚已经吃过太多次"改一处坏一处"** ⇒
> 宁可在这里指路，也不动文件（2026-09-18 用户拍板）。

## MVP 边界（核心就三个脚本 + 一个页面）

| 脚本 | 做什么 | **不做什么** |
|---|---|---|
| `scan_box.py` | 读 box 存文件（身份来自画面） | 不判进度、不迁移状态 |
| `list_jobs.py` | 筛作业、按缺人数排序 | 不自动选、不自动跑 |
| `run_job.py` | 起 MAA 跑指定作业 | 不导航、不判结算、不归位 |

**核心之外一律"可选、默认关"**：自动导航 / 状态回写 / 突袭模式 / 换作业轮换 / 页面校准…
（`auto_push.py` 就是"带自动导航与状态机"的那一层——它是可选的上一层，不是 MVP 本体。）

## 开发纪律（七条，硬性）

1. **改共享模块前，先 grep 全仓库谁在调它**
2. **改返回值/变量前，先 grep 全仓库谁在用**（`load_box` 的返回语句被替换掉 ⇒ 全链路预检拿不到 box，静默跳过 ✗ 实测）
3. **改完必须跑一次真实调用**，不能只 `ast.parse`
4. **超时/异常路径必须实测**（`nc_capture` 的 `timeout=` 从来没生效过：超时后 `communicate()` 收尾被 adb server 的管道堵死 ⇒ 能永久冻住 ✗ 实测）
5. **告警/断言类改动，上线前必须"故意造一次信号"**：断言要**跑红**（改坏被检查的对象，看它真会拦），
   告警要**真发**（造一个假信号，看它真会发）。理由：**假通过的告警比没有告警更危险 —— 你以为有人在看**。
   实测：内存告警第一版把"实例数 13"当"内存 13MB"，上线即发**假告警**，靠"三条路径都真跑一遍"才抓到。
6. **杀进程/关模拟器之前，先确认"杀的是谁的进程"**：同一台机器上跑着多个系统
   （AUTO-MAS / MAA-ALL / fz-maa / maaorch-channel），**按进程名杀会误杀别人**。
   一律按**可执行文件路径**限定，例：只杀 fz-maa 的 MAA ——
   `Get-CimInstance Win32_Process -Filter "Name='MAA.exe'" | Where-Object { $_.ExecutablePath -like "*maa-alas*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`。
   实测：计划里写的 `taskkill /F /IM MAA.exe` 会在 04:00 把生产线正在跑的客户号**全部中断**。

## 目标

**A（稳定）+ B（泛化）** —— 理由：今晚所有 bug 都是"假通过 / 静默卡死 / 串号"，
先让三个号**不假、不卡、不串**（A）；而"新 box / 新关 / 新活动能自动适配"（B）取决于
**身份与状态是否统一从证据来**（画面 UID、文件内 uid、结算页原文）——A 的地基正是 B 的前提。
C（量产 45 号无人值守）在 A/B 之前不该动。
