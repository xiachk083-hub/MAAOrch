# AUTO-MAS 内部机制深挖①：全链路（TaskManager / Timer / 模拟器 / 配置 / WebSocket / 签到·通知）

> 任务：kanban `t_68bd0f65`（board `maa-parallel-benchmark-20260926`，create 者 dsh-lead）
> 撰写：mas（Hermes），2026-09-27。**只读调查**：未改任何配置、未发车、未 stop 任何任务、未动实例；远端只做了 `type`/`certutil`/只读 PowerShell。
> 基线文档：`docs/audits/2026-09-27_mas_task_modes.md`（三种模式 + MaaManager 生命周期，t_806dac03）。**本篇只补其未覆盖的六条链路**，两者行号口径一致。
> 口径：行号 = **线上运行代码 `E:\AUTO-MAS\app\`**（v5.4.0，2026-09-27 21:0x 拉取核对）。被引 33 个后端文件的 SHA1 与本地 `D:\MAS` 逐个比对：29 个逐字节一致，4 个不同（`app/core/config.py`、`app/task/MAA/AutoProxy.py`、`app/services/notification.py`、`app/utils/constants.py`）——**这 4 个一律以线上副本为准**（已整包拉到本地核对行号）。完整校验表见附录 A。
> 术语沿用基线文档：TaskManager = 业务调度；Timer = 主业务定时器；MaaManager = 单脚本的 MAA 调度器（三种模式的公共壳）。

## 0. 摘要（给编排人）

| # | 链路 | 一句话 | 最常用钩子 |
|---|------|--------|-----------|
| 1 | TaskManager | `add_task` 解析 taskId → Task（逐脚本串行）→ 每个脚本一个 MaaManager；停止走 `stop_task` | `POST /api/dispatch/start|stop`；日志行 `任务结束: <task_id>`（缺 = 僵尸）；电源 `/api/dispatch/get|set/power` |
| 2 | Timer | 每秒 tick，`TimeEnabled`×条目 `Enabled`×`Days`×`Time` 命中即发；**分钟精确匹配、无 ± 容差**；靠 `LastTimedStart` 防分钟级重复 | 回滚/排查用；发车链路已由 Hermes cron 接管（见 `docs/audits/2026-09-27_hermes_takes_over_mas_scheduler.md`） |
| 3 | 模拟器 | `EmulatorManager.get_emulator_instance` → `MumuManager`；关模拟器 = `MuMuManager control -v <idx> shutdown`（有状态门） | `POST /api/emulator/status`（读）、`POST /api/emulator/operate {operate:close}`（温和关闭）；**没被关**的四种成因见 §3.4 |
| 4 | 配置管理 | `ConfigBase.lock()/unlock()` 级联拒写；`data/<sid>/Temp` 是 MAA 配置快照（= 「勾选消失」真凶）；legacy 迁移在 `check_data` 与 `_sync_legacy_skland_*` | 任何配置写都要先确认无任务在跑（被锁 → API 500「配置已锁定, 无法修改」）；止损走 stop 而不是 kill |
| 5 | WebSocket | 前端主 WS `/api/core/ws` 是**单连接**（第二连接被拒）；事件类型 `Update/Message/Info/Signal`；调试 WS `/api/ws_debug/live` 只覆盖**对外客户端的消息流**，不是任务事件流 | 任务事件流复用面 = `app.log` + `/mcp`（OpenAPI 派生）+ REST；不要抢前端 WS |
| 6 | 签到/通知 | 签到 = 4 平台注册表（森空岛/米游社/库街区/塔吉多+云异环），触发点 4 个；通知渠道 = 邮件/ServerChan(sctp)/自定义 Webhook/系统/koishi，各有闸门 | `POST /api/tools/sign`（手动、force）；任务完成报告由 `push_notification` 三个模式发出；触发点表见 §6.3 |

---

## 1. TaskManager（业务调度，`app/core/task_manager.py`）

### 1.1 职责

- 唯一发车门：把 `(mode, taskId)` 解析成「队列任务」或「单脚本/单用户任务」，构建 `Task` 并执行；维护 `task_info/task_handler` 两张表。
- 逐脚本串行执行：一个 Task 内的多个脚本项**顺序**跑（每个脚本一个 MaaManager），脚本内再逐用户串行（基线文档 §3）。
- 任务的收尾：`final_task` 打「任务结束」日志、发 WS 信号、按队列 `AfterAccomplish` 设置电源标志；`clean_task` 在全部任务结束后执行电源动作。
- 中止：`stop_task`（单个或 `ALL`），保证 final_task/unlock/Temp 还原仍被执行（Cancel 语义在 `models/task.py:199-212`，shield final_task）。

### 1.2 关键函数 + 行号

| 函数 | 行号 | 要点 |
|---|---|---|
| `TaskInfo.on_change` | L65-76 | 任何状态变化 → WS `Update{task_info}` + `Update{log}`（前端专用） |
| `Task.prepare` | L86-115 | 队列 uid → 展开 `QueueItem` 里 `ScriptId != "-"` 的全部脚本项（L89-99）；建 `script_list`（L101-111） |
| `Task.main_task` | L117-220 | ①AutoProxy 先签到（L120-130）②逐脚本循环（L158-220）：脚本被删 → 异常跳过（L165-173）；**脚本已被锁 → 跳过该项**（L176-186）；按配置类型建 Manager（MAA/SRC/General/Okww/OkNte/MaaEnd/M9A/HSR，L192-217）；`await self.spawn(task_item)`（L220） |
| `Task.final_task` | L222-244 | 「任务结束: <task_id>」日志（L224）；WS `Signal{Accomplish}`（L226-230）；**队列任务**且 `power_sign=="NoAction"` 时把队列 `AfterAccomplish` 写进 `Config.power_sign`（L232-244） |
| `Task.on_crash` | L246-252 | 异常 → WS `Info{Error}` |
| `_TaskManager.add_task` | L266-342 | taskId 解析（见下方 L289-316）；**目标脚本已锁 → `RuntimeError 已在运行`**（L318-321）；`new_task_info` → WS `Signal`（L324-328）；建表 + `execute()` + `clean_task` 协程（L329-340） |
| `_TaskManager.clean_task` | L344-366 | 等 accomplish → 表清空后（`len(task_handler)==0`）且 `power_sign != NoAction` 且非 ScriptConfig → WS `Message{Countdown}` + `System.start_power_task()`（L351-366） |
| `_TaskManager.stop_task` | L368-407 | `"ALL"`：清空 power_sign、逐任务 cancel、等全部结束（L377-395）；单任务：无此任务视为成功（L398-400），重复 stop → `RuntimeError 任务已在中止中`（L401-402） |
| `_TaskManager.start_startup_queue` | L409-447 | 启动时任务：sleep 10s（L422）→ **主 WS 未连则不跑**（L424-426）→ 遍历 `StartUpEnabled` 队列逐个 `add_task(AutoProxy, trigger_source="startup_task")`（L430-443） |

taskId 解析（L289-316，分支顺序即优先级）：

| mode | taskId | 结果 |
|---|---|---|
| ScriptConfig | 脚本 uid | script=uid, user="Default"（L289-294） |
| ScriptConfig | 用户 uid | 扫各脚本 UserData 找属主（L296-303；找不到 → `ValueError` L304） |
| AutoProxy/ManualReview | 队列 uid | queue=uid → 跑队列全部脚本项（L305-309） |
| AutoProxy/ManualReview | 脚本 uid | script=uid → 该脚本全部启用用户（L310-314） |
| 任意 | 其它 | `ValueError`（L315-316） |

### 1.3 任务队列与「已在运行」判定（三层）

1. **入口层**：`add_task` 见 `ScriptConfig[script].is_locked == True` 直接拒（L318-321，异常文本 `已在运行`）——同脚本不可能并发两个任务。
2. **执行层**：任务已启动后，某脚本项发现 `is_locked`（被别的任务抢先）→ 该项标「跳过」、写日志 `跳过任务: …, 该任务已被其他任务调度器锁定`（L176-186）→ **该轮丢掉、不分叉、不重试**（整任务继续跑其余脚本项）。
3. **调度器层**：`TaskExecuteBase.execute()` 防重入 `RuntimeError 任务已在运行`（`app/models/task.py:219-221`）。

锁的生命周期：MaaManager.prepare 时 `lock()`（`app/task/MAA/manager.py:125`），final_task 里 `unlock()`（同文件 L199）；**锁是内存态**，后端重启即全部消失（现场：19:54:14 重启后，之前被锁的脚本马上能再次发车）。

### 1.4 电源动作（PowerSign）

- 标志位 `Config.power_sign`（`app/core/config.py:271-280`），取值 `NoAction/Shutdown/ShutdownForce/Reboot/Hibernate/Sleep/KillSelf/Logoff`（中文名表 `app/utils/constants.py:781` `POWER_SIGN_MAP`）。
- 触发链：队列 `Info.AfterAccomplish`（`app/models/config.py:360-371`）→ `Task.final_task` 写入（仅当当时是 NoAction，L238-241）→ **所有任务跑完**（task_handler 空）时 `clean_task` 启动 60 秒倒计时电源任务（`app/services/system.py:46 countdown=60`，L320-330 `start_power_task`）→ 实际执行 `_power_task`→`set_power`（L302-318 / L193-300）。
- 中止语义：`stop_task("ALL")` 会先把 `power_sign` 清成 `NoAction` 并取消未执行的电源任务（L380-395）——即**主动全停不会引发关机**。
- 电源动作里 `Shutdown/Reboot/Logoff` 会先 `kill_emulator_processes()`（关键词 `Nemu/nemu/emulator/MuMu` 扫进程名，`system.py:345-360`），这是全机层面唯一主动杀模拟器的地方。
- REST：`POST /api/dispatch/get/power`（读）/ `set/power`（写）（`app/api/dispatch.py:73-110`）。

### 1.5 我们可用的钩子

- 发车：`POST /api/dispatch/start`（`app/api/dispatch.py:33-52`）——现用链路（`tools/mas_dispatch.py`）不动。
- 停机/止损：`POST /api/dispatch/stop {"taskId": <start 返回的 task_uid 或 "ALL">}`（L55-70）；对已结束任务视成功；**会走 unlock+Temp 还原**（这才是「配置被锁/卡死」的正确止损口）。
- 「任务真的跑完」判据：`任务结束: <task_id>`；只有 `任务开始:` 没有 `任务结束:` = 僵尸（本轮/重启吃掉；基线文档 §5 同款）。
- 「谁在跑/谁锁着」：无 REST 直读接口；看 `app.log` 的 `已锁定脚本配置/已解锁脚本配置`、`任务开始:`、`开始代理用户:` 行；或被锁时 start 直接返回「已在运行」。
- 电源闸门巡检：`get/power` 只读；值 ≠ NoAction 且长时间无任务 = 倒计时/遗留标志（注意 `stop ALL` 会清它，别误判）。

---

## 2. Timer（主业务定时器，`app/core/timer.py`）

### 2.1 职责

- 常驻两个协程：`second_task`（每秒 tick：定时发车 + 明日方舟PC工具）与 `hour_task`（每小时：Matomo 统计上报）。
- 定时发车只在这里发生（AUTO-MAS 侧的 24 条班次定时；现已全关，见回滚说明）。
- 签到后台任务的分发也挂在本类（启动时/任务内），见 §6。

### 2.2 tick 逻辑与命中条件

- 启动：`start()`（L80-99）创建 `second_task/hour_task`；若 `ToolsConfig.GameSign.Enabled && RunOnStartup` 顺带派发一次后台签到（L91-97）。
- `second_task`（L125-136）：`while True: await self.timed_start(); (ArknightsPC); await asyncio.sleep(1)`。
- `timed_start`（L166-198），**每秒执行**，对每个队列：
  1. `queue.Info.TimeEnabled` 为假 → 跳过（L174）；
  2. `curtime == queue.Data.LastTimedStart` → 跳过（防同分钟重复，L178-179）；
  3. 遍历 `queue.TimeSet`：`Enabled==True` 且 `{当前英文星期} in Days` 且 `curtime[11:16] == Time`（HH:MM **字符串相等**，L181-186）→ `add_task(AutoProxy, queue_uid, trigger_source="scheduled_task")`（L188-197）→ 写 `LastTimedStart = curtime`（L198）。
- 两层开关 + 两个附加条件，缺一不发：`TimeEnabled`（队列总闸，`app/models/config.py:350-352`）× `TimeSet.Info.Enabled`（条目，L331-338 的 `Days`/`Time` 字段）。

### 2.3 `LastTimedStart` 防重

- 语义 = 「该队列上一次定时发车的**分钟**」（`YYYY-MM-DD HH:MM`），命中后即写（L198，即使任务随后被锁跳过也写了）。
- 只读观测：`POST /api/queue/get` 的 `Data.LastTimedStart` —— 可用于判断「AUTO-MAS 侧最近有没有自发车」，但**不能**当成功判据（写了不代表任务跑成功）。

### 2.4 「错峰(±分钟)」澄清（实测结论）

- **代码里不存在 ±分钟容差的错峰实现**（全仓 grep `错峰/jitter/random` 无命中，含前端 dist）。命中判定是**精确到分钟**的字符串相等（L185）：
  - 含义①：条目 `Time=05:00` 的队列，只有当某一秒 tick 落在 `05:00:00–05:00:59` 内才会发；**错过这一分钟 = 该班次永久丢失**（除非下一分钟再次命中别的条目）——后端卡顿/重启跨越分钟边界，就是「定时没发车」的天然根因之一。
  - 含义②：所谓错峰只能靠**条目 Time 本身错开**。全队现状 = 12 槽 × 3 分钟错峰（05:00/05:03/…/05:33、19:30/…/20:03），这个表由 Hermes 侧 `tools/mas_dispatch.py` 的 `SHIFT_TABLE` 持有（发车已改 cron，见 `docs/audits/2026-09-27_hermes_takes_over_mas_scheduler.md`）。
  - 含义③：`GameSign` 存在**旧版的「窗口内随机时间」错峰字段**（`WindowStart/WindowEnd/ScheduledTime`），现行版本注释明确「保留用于读取历史配置，不参与调度」（`app/models/config.py:3169-3198`）——看到这些字段别当现行功能。
- 附带：`Days` 用英文星期名（`datetime.strftime("%A")`，L170/L184），与中文界面无关。

### 2.5 startup_task

- 触发点是 **WS 连接**，不是进程启动：`app/api/core.py:76` 在任一前端 WS 连接时 `create_task(TaskManager.start_startup_queue())`；内部 sleep 10s 后再查一次 WS（L422-426，未连则不跑并放弃本次）。
- 语义：对每个 `StartUpEnabled` 队列发 AutoProxy（L430-443），`trigger_source="startup_task"`；只跑一次（`_startup_queue_started` 守卫，L412-417）。
- 现场例：09-27 19:54:14 重启后端 → 19:54:18 前端自动回连（`WebSocket /api/core/ws [accepted]`）→ 19:54:28 `启动时任务开始运行`（app.log:104012），随后 19:54:41 起有新任务创建——注意：**前端不在线时本次不触发**，要等下一次 WS 连接。

### 2.6 与签到的接口（详见 §6）

- `schedule_game_sign_for_startup`（L200-217）：仅 `Enabled && RunOnStartup`，后台单例。
- `try_game_sign_for_task(source=...)`（L300-327）：由 `Task.main_task` 在 AutoProxy 任务开头调用（`task_manager.py:120-130`），来源映射 `scheduled_task/manual_task/startup_task`（L123-127）；任务触发的签到结果**不单独通知**，并入任务完成报告（`_TASK_GAME_SIGN_SOURCES` L44-48）。
- `_execute_game_sign`（L232-298）：`game_sign_flow()` 流程锁 + `run_all_sign_in(force=False)`；结果落 `update_game_sign_results`；`NotifyEnabled` 时非任务来源单独推送（L274-290）。

### 2.7 我们可用的钩子

- 回滚（两条一起做）：`python tools/mas_timers.py --enable` + `hermes -p mas cron pause 2600f6415f16`；只做一条会双发/断发。
- 巡检「漂移/未关」：`tools/mas_dispatch.py --list`（只读点名）。
- 判断「某班次有没有发」：cron 侧 `runtime/mas_dispatch_log.jsonl`（我们发的）+ AUTO-MAS 侧 `LastTimedStart`/`任务开始:`（兜底）。
- 若未来恢复 AUTO-MAS 定时：注意分钟精确匹配语义（§2.4①），别指望它容忍错峰/补跑。

---

## 3. 模拟器链路（DeviceManager / EmulatorManager / MumuManager）

### 3.1 职责与层次

- `app/core/emulator_manager.py`（`EmulatorManager`）：按 emulatorId 实例化具体模拟器实现、代理 open/close/show 操作、聚合状态。
- `app/utils/emulator/__init__.py:28-32` `EMULATOR_TYPE_BOOK` = {mumu: MumuManager, ldplayer: LDManager, general: GeneralDeviceManager}；本机全队用 MuMu。
- `app/models/emulator.py`：`DeviceStatus`（ONLINE=0/OFFLINE=1/STARTING=2/…）L27-41；`DeviceBase` 抽象 L52-146。
- 任务侧消费：MaaManager.prepare 建实例（`app/task/MAA/manager.py:135-137`），open/close 在 AutoProxy/ManualReview/ScriptConfig 三种模式里调用（位置见 §3.3）。

### 3.2 关键函数 + 行号

| 函数 | 行号 | 要点 |
|---|---|---|
| `EmulatorManager.get_emulator_instance` | `emulator_manager.py:46-82` | 读 `Config.EmulatorConfig[uid]` 字典 → `EMULATOR_TYPE_BOOK[Type](config)`；顺带按 `Function.IfBlockAd` 去广告（MuMu 走 appops 拦截 + force-stop 商店，见 mumu.py:200-248） |
| `operate_emulator` / `operate_emulator_task` | L84-88 / L90-110 | **fire-and-forget**：`asyncio.create_task(...)`（L88）；失败只发 WS `Info{error}`（id=`EmulatorManager`，L106-110），REST 调用方拿不到错误 |
| `get_status` | L112-137 | 聚合各模拟器 `getInfo(None)` → `SchemaDeviceInfo{title,status,adb_address}` |
| `MumuManager.open` | `mumu.py:250-337` | 已在跑（`getStatus==ONLINE`）直接返回（L260-264）；先 `setting -k app_keptlive -val false`（L274-288）→ `control -v idx launch`（L290-303）→ 轮询到 ONLINE（L305-333）：静默时 `setVisible(False)`、`IfBlockAd` 时拦广告、带包名时 `_ensure_app_foreground`（launch→dumpsys 前台检查→monkey 补启，L116-198）；超时/错误抛异常（L334-337） |
| `MumuManager.close` | `mumu.py:339-375` | **状态门**：非 ONLINE/STARTING → 只告警 `设备{idx}未在线，当前状态: {status}` 并返回（L341-344，**不发 shutdown**）；否则 `control -v idx shutdown`（L346-355）→ 轮询到 OFFLINE（L360-372，超时抛 `关闭超时`）；`finally`：`ForceKillOnClose` 时强杀残留（L373-375） |
| `_force_kill_mumu_processes` | L377-395 | psutil 遍历，命中白名单关键字 `mumunxdevice/mumunxmain/mumuvmmheadless`（L40-44）→ 杀整棵进程树（L397-402） |
| `getStatus` / `_get_status_from_data` | L420-433 / L435-442 | **状态面完全基于 `MuMuManager info -v <idx>` 的 `is_process_started/is_android_started`**——这正是我们已知会假死的字段（全实例误报 false ⇒ 这里判成 OFFLINE） |
| adb 地址解析 | L444-491、L539-566 | `_resolve_adb_address`：`adb_host_ip/adb_host + adb_port`（L444-451）；取不到 → `MuMuManager adb -v <idx>`（L554-566）再解析（L474-491）；**再取不到 → 默认端口兜底 `127.0.0.1:(5555 + idx*2)`**（L453-458）——实例 13 ⇒ 5581，与实测常开的 5581 一致 |
| MAA 侧二次解析 | `app/core/maa_manager.py:133-163` | `convert_adb`：从 `adb_address` 取端口（支持 `127.0.0.1:x` / `emulator-x` 两种），在 `Toolkit.find_adb_devices()` 里按端口匹配（L154-161）；找不到 → `RuntimeError 无法找到指定设备` |
| `setVisible` / 多开器窗口 | L519-537 / L568-609 | show/hide_window；`find_mumu_nx_window` 找「MuMu模拟器」窗口（mumunxmain.exe）并按需 `WM_CLOSE` |
| REST | `app/api/emulator.py:145-178` | `/operate {operate: open|close|show, emulatorId, index}`、`/status`（读）；`/search` L181-200（枚举卸载表） |

### 3.3 「关模拟器」的全部调用点与三道门

调用点（全部集中在 MAA 系任务）：

| 位置 | 行号 | 条件 |
|---|---|---|
| AutoProxy 用户级收尾 | `task/MAA/AutoProxy.py:1026-1033` | **仅** `Run.TaskTransitionMethod == "ExitEmulator"`（默认值即此，`models/config.py:841-846`；实测 55 条 ScriptConfig 中 54 条 `ExitEmulator`、1 条 `Run` 结构例外） |
| AutoProxy 失败止损 | L517-522（开模拟器失败）、L566-573（代理失败） | 无条件（错误路径） |
| ManualReview | L153-158、L204-211 | 错误路径 |
| MaaManager 脚本级收尾 | `task/MAA/manager.py:202-206` | **AutoProxy/ManualReview 无条件**（收尾兜底，脚本全部用户跑完后必关一次）；ScriptConfig 不关（它根本不开模拟器） |
| 电源操作 | `services/system.py:345-360` | 仅 Shutdown/Reboot/Logoff 时全机扫杀模拟器进程 |

「MAA 配置里的 PostActions」不等于关模拟器：`MAA_TASK_TRANSITION_METHOD_BOOK`（`utils/constants.py:134-139`）把 `ExitGame` 与 `ExitEmulator` **都映射成 MAA 侧 `"9"`**（=退出游戏+退出 MAA）；**关模拟器这件事 100% 由 AUTO-MAS 侧的 close() 完成**，MAA 自己不会关。

`close()` 的三道门（决定「关没关」）：
1. **状态门**（mumu.py:341-344）：状态面报 OFFLINE/ERROR/UNKNOWN 时**静默返回、不发 shutdown**——状态面假死 ⇒ 「代码跑了但没关」。
2. **模式门**：ScriptConfig 模式没有任何 close（设计）；用户级只有 ExitEmulator 才关（但脚本级兜底仍会关）。
3. **收尾门**：只有任务真正走到 final_task 才会关——被重启吞掉的孤儿任务、被外部抢锁打断后没续跑的任务，不会关。

重复关闭是正常的：用户级关完 → 脚本级再关一次会打 `设备{idx}未在线，当前状态: 1`（实测 app.log:90235、96985）。

### 3.4 现场考证：为什么「观察到模拟器没被关」（2026-09-27 晚班，7 台桌面留机）

**结论：不是策略留机，是 19:54:14 AUTO-MAS 后端静默重启吞掉了 8 条在跑任务，孤儿任务的 final_task 从未执行。**

证据链（app.log，行号为抓取时文件行号）：

1. 19:30:41–19:51:41 晚班定时 1–8 各发一车（各链首个用户依次 官-23/官-2-4-47/官-3/官-41/官-9/官-1-1-7/官-15/官-6；日志 `创建任务→任务开始→已锁定`：L103633–103871）。
2. 19:54:14.401 后端静默重启（banner 起于 app.log:103953，19:54:17 `Started server process [33976]`；上一处 `Shutting down` 还是 09-24 00:39（app.log:38388），本次时点前无任何优雅退出/traceback 行）——这 8 个任务对象随旧进程消失。（该次属计划外：AUTO-MAS 惯性重启节奏为每天 04:01/19:01，见 `docs/MAS_SCHEDULE_MAP.md` 差异 D9。）
3. 它们的 MAA 子进程是独立进程，重启后照常跑完（官-23 19:54:50、官-2-4-47 ~19:55、官-6 19:57:58、官-3 ~19:59、官-1-1-7 20:02:12、官-41 ~20:03、官-9 ~20:07），**但没有任何 AUTO-MAS 收尾**：无 `用户任务结束, 关闭模拟器`、无 `已解锁脚本配置`、无 `任务结束:`（这些行在对照任务里都有）⇒ 模拟器保持开机、停在 Android 桌面。
4. 对照组（重启后完整跑完的任务）：b-3 20:19:22、官-33 20:19:33、b-11 20:25:11、官-24 20:26:54 全部出现 `用户任务结束, 关闭模拟器` 并真实关闭（app.log L104055/L104127/L106181/L106197/L106636/L106676 均为该类行；「设备离线」告警即关闭后现象）。
5. 第 8 条链（官-15）首试失败后的重试也被重启吞掉 → 游戏悬置标题页、无 MAA（该实例属异常，见 t_aaacd03c 报告）。

**排查口诀（下次看到「模拟器没关」）**：先看该实例脚本的任务有没有 `任务结束:` / `用户任务结束, 关闭模拟器` → 有 = 关了但状态面/重复关闭噪音；没有 = 收尾没跑（重启孤儿/被锁跳过/僵尸），别去动配置。

### 3.5 我们可用的钩子

- 只读状态：`POST /api/emulator/status {"emulatorId": ...}`（注意：`status=1` 不代表在跑，假死已知；最终判据仍以 VMM 进程/adb 为准——见技能 `auto-mas-host-ops`）。
- 温和关闭留机实例：`POST /api/emulator/operate {"operate":"close","emulatorId":<uuid>,"index":"<idx>"}`（走 `control shutdown`，比 kill 干净）；**但**：① 是后台任务，REST 不等结果；② 失败只在 WS（我们看不到）；③ 会撞状态门（假死时不关）。执行后需自证（adb/进程复核）。
- adb 端点复现：`MuMuManager info -v <idx>` 的 `adb_host_ip:adb_port` → 取不到再用 `adb -v <idx>` → 再取不到按 `5555+2*idx` 兜底（这正是我们脚本里用的三段式）。
- 别用 kill 代替 close：kill 绕过 `app_keptlive` 设置与状态机，可能让下次 open 撞坏状态；`ForceKillOnClose` 白名单强杀只在配置开启时由 close 自己执行。

---

## 4. 配置管理（ConfigManager：锁定 / Temp / legacy）

### 4.1 职责

- `app/core/config.py` 的 `AppConfig`（L238 起）：加载/持有全部配置文件（`Config.json`、`EmulatorConfig.json`、`PlanConfig.json`、`ScriptConfig.json`、`QueueConfig.json`、`ToolsConfig.json`、`GameSignAccounts.json`），提供全局 get/set/WS 推送；启动时做数据版本迁移。
- `app/models/ConfigBase.py`：配置对象基座（`ConfigItem/ConfigBase/MultipleConfig`），**锁**机制与 JSON 落盘都在这里。
- 任务侧：MaaManager 在 prepare 锁脚本配置、把 MAA 配置目录快照到 `data/<sid>/Temp`；final_task 还原快照并解锁。

### 4.2 锁定 / 解锁

- `ConfigBase.lock(): is_locked=True` 并**级联**给所有 ConfigItem 与嵌套 `MultipleConfig`（`ConfigBase.py:1112-1125`）；`unlock()` 反向（L1125-1133）。`ConfigItem.lock/unlock` 是同步版（L811-821），`MultipleConfig.lock/unlock` L1467-1481。
- 锁后**所有写路径抛 `ValueError("配置已锁定, 无法修改")`**（11 处守卫：L716 值写 `ConfigItem.setValue`、L869 `connect`、L939 `load`、L1029 `set`、L1077 `bind`、L1099 `unbind`、L1209/L1280/L1406/L1433/L1460 多重配置的 connect/load/add/remove/setOrder；多重配置删除还有子项锁判定 L1439）。
  - 表现：对任务锁定的脚本调 `/api/scripts/update` 等 → API 返回 500「配置已锁定, 无法修改」；`tools/mas_startup_verify.py --restore` 撞的就是它。
  - 例外：`save()`（L1104）本身不看锁（只检查文件路径），真正拦截发生在更上层的 set/load。
- 锁的持有者 = 当前跑着的任务（MaaManager.prepare L125 → final_task L199）；**内存态**，进程没了锁就没了。stop 任务（含被中止）仍会在 shield 的 final_task 里解锁。

### 4.3 Temp 备份 / 还原（「勾选消失」的真凶）

- **备份**（脚本级 prepare）：`app/task/MAA/manager.py:139-142`——把 MAA 目录 `config/` 整包 `copytree` 到 `data/<scriptId>/Temp`（`dirs_exist_ok=True`，覆盖旧快照）。
- **运行期**：AutoProxy 用 `data/<sid>/Default/ConfigFile`（简洁）或 `data/<sid>/<user>/ConfigFile`（详细）覆盖 MAA 的 `config/` 目录（AutoProxy.py:607-621），并程序化写 `gui.json`/`gui.new.json`。
- **还原**（脚本级 final_task）：`manager.py:256-260`——`rmtree(config/)` 后用 Temp 快照整体回填，再删 Temp。
- 后果（已实证）：**任务运行期间在 MAA GUI 里改的任何设置/勾选，跑完会被 Temp 快照还原**——这就是「AccountSwitchEnabled 勾选消失」现象的真凶（详见 `docs/audits/2026-09-27_mas_account_switch_reset.md`，mtime 取证）。要永久生效的改动必须落到 `data/<sid>/<user>/ConfigFile` 或 `data/<sid>/Default/ConfigFile`（即 AUTO-MAS 侧源配置），而不是 MAA 目录里的活动配置。
- 对照：**ScriptConfig 模式方向相反**——final_task 把 MAA `config/` 整包 `copytree` **回写**到 `data/<sid>/<user>/ConfigFile`（`task/MAA/ScriptConfig.py:176-195`），即「在 GUI 里改完 → stop → 存回源配置」。这也是「改配置」唯一被设计支持的路径。

### 4.4 legacy 字段迁移

- 启动迁移总入口：`check_data()`（`core/config.py:385-693`，按 `data.db` 里 version 表逐级升级；例：v1.10→v1.11 把 `ScriptConfig.json` 里 `IfCombat→IfFight` 等改名并**直接写回文件**，L679-689）。
- 森空岛凭据迁移：`_sync_legacy_skland_accounts()`（L1393-1408；由 init_config L355-357 调用）——把旧 MAA/MaaEnd 用户侧的 `IfSkland/SklandToken/LastSklandDate` 迁到统一签到账号组（`_sync_legacy_skland_user` L1272、`_sync_legacy_skland_sign_date` L1368、账号匹配 `_find_game_sign_account_by_skland_token` L1192、token 状态 `_legacy_skland_token_state` L1237）。
- 旧用户侧的兼容路径仍活着：`Task.main_task` 里签到工具未启用时，AutoProxy 每用户跑一遍内置森空岛签到（AutoProxy.py:400-454，写 `Data.LastSklandDate`）。
- 其它「保留用于读取」的旧字段：`GameSign.WindowStart/WindowEnd/ScheduledRun/ScheduledTime`（`models/config.py:3169-3198`，注释原文「不参与调度」）；`Data.LastStatisticsUpload` 等结构化字段实际由各 ConfigItem 定义（`legacy_group` 参数）。
- 我们的红线不变：**不手改任何 `*Config.json`**（活配置会被回写覆盖 + 含 DPAPI 密文）；写操作走 REST/界面，只读分析用白名单字段。

### 4.5 我们可用的钩子

- 「现在能不能改这个脚本的配置」= 该脚本是否在跑：`add_task` 会拒（§1.3）；被锁时唯一正解是 stop 任务（触发 unlock+Temp 还原），或用 ScriptConfig 模式改配置（它专门做这件事）。
- Temp 目录的三个时间点（manaeger.py L139-142 / L256-260）是我们取证「配置在何时被谁覆盖」的锚（mtime），例：t_e43fecf4 的还原取证。
- legacy 迁移只发生在 `check_data()`（启动时）；`Config.json` 里 version 落后会触发写文件——排查「配置莫名被改写」先看这里。
- 只读导出：`tools/mas_accounts.py` / `tools/redact_scriptconfig.py`（白名单字段，凭据读入即弃）。

---

## 5. WebSocket 事件面

### 5.1 三个面，别搞混

| 面 | 端点 | 用途 | 我们能用吗 |
|---|---|---|---|
| 前端主 WS | `/api/core/ws`（`api/core.py:63-110`） | 前端 ↔ 后端全部事件（任务/日志/弹窗/信号） | **单连接**，前端占着；不要抢 |
| 外部 WS 客户端（出向） | 无固定端点（`utils/websocket.py`，`/api/ws_debug/*` 管理） | AUTO-MAS 主动连外部（koishi 系统客户端等） | 可管理/观察（见 5.5） |
| 调试 WS | `/api/ws_debug/live`（`api/ws_debug.py:457-497`） | 实时看**外部客户端**的消息流+历史 | 可连（只覆盖外部客户端，不含任务事件） |

### 5.2 前端主 WS（`/api/core/ws`）

- 路由：`api/core.py:35`（prefix `/api/core`）→ `connect_websocket` L63-110。
- **单连接**：已有连接时新连接直接 `close(1000, "已有连接")`（L66-68）；等同「前端独占」。
- 建立后：`Config.websocket = websocket`（L71）→ `create_task(TaskManager.start_startup_queue())`（L76，即 §2.5 启动时任务）。
- 心跳：收超时 15s；收到 `Signal{Pong}` 刷新；否则发 `Signal{Ping}`；先 Ping 后始终无 Pong → `close("Ping超时")`（L82-104）。
- 入站消息（前端 → 后端）：非 Ping/Pong 一律 `await Broadcast.put(data)`（L92）——交给广播订阅者（见 5.4）。
- 断开：清 `Config.websocket`（L107-110）；此后所有 `send_websocket_message` 记警告并丢弃（`config.py:702-722`，断开时顺手置空引用）。

### 5.3 消息类型与事件清单

`WebSocketMessage` 模型（`models/schema.py:2266-2272`）：`{id, type, data}`，type ∈ `Update | Message | Info | Signal`：

- **Update**：数据更新。任务侧：`task_info`（脚本/用户状态列表）与 `log`（实时日志文本）（`task_manager.py:65-76`）；电源标志 `PowerSign`（L242-244、L393-395）。
- **Message**：请求 UI 弹窗。任务完成倒计时 `{type:"Countdown",...}`（L357-365）；人工排查问答 `{type:"Question", message_id, title, message, options}`（`ManualReview.py:163-171/L213-224/L238-248`）。
- **Info**：需要在 UI 显示的信息（日志流）：`{Info|Warning|Error: text}`——任务/用户级错误、检查未过、跳过告警等（散见 task_manager / AutoProxy / emulator_manager）。
- **Signal**：程序信号。`Accomplish`（任务结束结果，`task_manager.py:226-230`）、`newTask`+队列信息（新任务，L324-328）、`Ping/Pong`、`RequestClose`（`system.py:252/293`）、更新服务的各种信号（`services/update.py`）。
- **id 语义**：`"Main"`=主进程级；`"TaskManager"`=调度器；`"EmulatorManager"`=模拟器操作；其余 = task_id（前端据此路由到对应任务面板）。

### 5.4 Broadcast（`core/broadcast.py:33-53`）

- 极简发布/订阅：`subscribe(queue)` / `put(item)`（深拷贝给每个订阅者）。
- 主消费者 = **人工排查模式**：`ManualReview.prepare` 订阅（`task/MAA/ManualReview.py:98-99`），`_wait_for_user_response` 等待 `{id: message_id, type: "Response", data:{choice: ...}}`（L253-263）——即弹窗答案经主 WS → Broadcast → 任务协程。HSR 的 ManualReview 亦然（`task/HSR/ManualReview.py:140`）。
- 结论：Broadcast ≈ 前端交互回包通道；**不是**给第三方做事件订阅的口子。

### 5.5 外部 WS 客户端 & 调试面

- `utils/websocket.py`：`WebSocketClient`（L37 起：连接/重连/心跳/鉴权/命令处理）+ `WSClientManager`（L488 起：多客户端管理、消息历史、调试连接广播、`init_system_client_koishi` L798-852 系统级 koishi 客户端）。
- REST（`api/ws_debug.py`，prefix `/api/ws_debug`）：create/connect/disconnect/remove/status/list/send/send_json/send_auth（L62-390）+ `GET /history`（L391-412）+ `GET /commands`（L439-454，列出 `@ws_command` 注册表，`api/ws_command.py`）。
- `WS /live`（L457-497）：连上先收 `{type:"init",clients}` + 历史消息回放，然后实时推**外部客户端**消息；心跳 ping/pong 文本。
- 注意：这些**都不含**任务事件流（任务事件只走主 WS + 日志）。

### 5.6 我们可复用哪些做监控

1. **app.log 是一等公民**：几乎所有 `send_websocket_message` 都有对应日志行（`业务调度/MAA 调度器/MAA 自动代理/MuMu模拟器管理` 等 logger），
   且带毫秒时间戳——巡检/取证都靠它（现有 `mas-readonly-inspection` 30 分钟巡检即此路线）。
2. **主 WS 别抢**：想接任务事件只能在前端断开时接（单连接），不适合作为常驻监控面；抢连还会顶掉前端（新连接被拒，前端回连后又踢不下我们——总之是脆的）。
3. **`/mcp`**：后端把 OpenAPI 派生为 MCP（`main.py:150-172`，挂 `/mcp`，`exclude_tags=["Delete"]`），是官方认可的集成面；现有 4 只读 MCP server 即走它（schema 缺陷见技能笔记 `get_queues` 不可用）。
4. **REST**：`/api/dispatch/*`（发车/停/电源）、`/api/emulator/*`、`/api/history/*`（历史日志）等（`main.py:271-287` 全量注册）。
5. **调试 WS**：若将来接 koishi 类外部客户端，用 `/api/ws_debug/live` + `/history` 观察，无需碰主 WS。

---

## 6. 签到与通知

### 6.1 职责

- 签到（`app/tools/game_sign.py` + `skland.py`/`miyoushe.py`/`kuro.py`/`taygedo.py`）：统一「社区签到」工具，多平台、多账号、凭据刷新回写、每日一次。
- 通知（`app/services/notification.py` + `app/task/MAA/tools/notify.py` + `game_sign_notify.py`）：把任务结果/统计/六星/签到结果按闸门分发到各渠道。

### 6.2 签到链路（`game_sign.py`）

- 平台注册表（L346-378）：`SklandToken→森空岛`、`MiyousheToken→米游社`、`KuroToken→库街区`、`TaygedoToken→塔吉多/云异环`（塔吉多/云异环按凭据内容解析，L221-236）。`has_game_sign_credentials` L391-397。
- 执行：`run_all_sign_in(force=False)`（L194-206）→ 全局锁 `_enter_game_sign_lock`（L93-112，同一任务内可重入）→ `_run_all_sign_in`（L538-641）：
  - 跳过禁用账号（L553-555）；非强制时跳过 `LastSignDate==今天`（L557-562）；
  - 同账号的多个平台**并发**（L579-589），结果统一合并；凭据刷新（如 SklandToken）回写（L596-602）；
  - **防重口径**：自动（非 force）每天都在同一天只尝试一次（成功失败都记日期，L604-621 注释原文）；手动 force 要所有已配置平台全部完成才记日期；
  - 森空岛完成时额外回写旧用户侧 `LastSklandDate`（L623-636）。
- 时间校准：`_check_system_time`（L143-174）对比 worldtimeapi，偏差 >300s 仅告警不阻断（米游社 DS 签名对时间敏感）。
- Skland 客户端（`skland.py`）：凭据解析/校验 L100/L146，登录 `login_skland_with_password` L536，签到主函数 `skland_sign_in` L578（`_run_skland_sign_in` L596 起）；被 `game_sign._run_skland_provider`（L239-265）以 `app_code="all"` 调用并回收新凭据。

### 6.3 签到触发点（4 个）

| 触发点 | 位置 | 闸门/说明 |
|---|---|---|
| ① 随 AutoProxy 任务 | `task_manager.py:120-130` + `timer.py:300-327` | 仅 AutoProxy；任务开跑前执行；来源 `task_scheduled/task_manual/task_startup`；**结果并入任务完成报告**（不单独通知；`manager.py:223-236` 拼接、L246-247 消费标记） |
| ② 启动时 | `timer.py:91-97` → `schedule_game_sign_for_startup`（L200-217） | `GameSign.Enabled && RunOnStartup`；单例后台；结果**单独通知**（`NotifyEnabled`） |
| ③ 手动按钮 | `POST /api/tools/sign`（`api/tools.py:141-206`） | `force=True`；已在跑 → 409 `GameSignInProgressError`；按 account_uid 替换旧结果；有失败项 → warning 回执 |
| ④ 旧用户兼容路径 | `AutoProxy.py:400-454` | **仅当签到工具未启用**时才跑（每用户内置森空岛签到，写 `Data.LastSklandDate`）——这是 5.3.1 兼容层，别和 ① 搞混 |

### 6.4 通知链路

渠道实现（`services/notification.py`）：系统通知 `push_plyer` L99；邮件 `send_mail` L134；ServerChan `ServerChanPush` L206-244（SendKey 支持 `sctp<int>t…` 格式）；**自定义 Webhook** `WebhookPush` L246-368（URL/模板/Headers/Method 均可配，模板变量替换 L282-330；配置模型 `models/config.py:277-297`）；企业微信机器人图片 L394；koishi `send_koishi` L432。测试入口 `send_test_notification` L484-528。

任务结果推送（`task/MAA/tools/notify.py push_notification` L30-190），三个模式：

| 模式 | 闸门 | 渠道 |
|---|---|---|
| `代理结果` | `Notify.SendTaskResultTime`（不推送/任何时刻/仅失败时）或本任务带签到摘要（L37-44） | 邮件(L53-56)/ServerChan(L57-62)/自定义 Webhook(L63-64)/koishi(L67-68)（+ 系统 plyer 在 manager.py:238-243 单独发） |
| `统计信息` | 全局 `IfSendStatistic`（L94）+ 每用户 `Notify.*`（L113-142） | 同上（HTML 模板 `MAA_statistics.html`） |
| `公招六星` | 全局 `IfSendSixStar`（L146）+ 每用户（L163-189） | 同上 |

- 发送点：每用户收尾 `AutoProxy.final_task`（统计 L1071-1078、六星 L1087-1101）+ 脚本收尾 `MaaManager.final_task`（完成率报告 L222-254）。
- 签到结果推送：`game_sign_notify.push_game_sign_notification`（L227-334），带**重试一次**的通道封装（L207-224）；触发者 = ②③（①并入任务报告；见 §6.3）。
- 队列/全机器收尾：`AfterAccomplish` → 电源动作（§1.4）；完成后通知「报统计」走上面统计模式，不额外发。

### 6.5 我们可用的钩子

- 手动补签：`POST /api/tools/sign`（幂等靠 `LastSignDate`；重复调用 force 也会跑——它只是重签一遍，注意风控）；读签到状态用 `Config.ToolsConfig.GameSign_Status/Result`（ConfigItem，`models/config.py:3199/3206`）或 `/api/tools/get`。
- 判断「今天签到成功没」：账号组 `LastSignDate==今天`（自动模式成功/失败都会写；**失败也记**）=「尝试过了」而非「成功」；精确结果看 `GameSign.Result`/日志 `游戏社区签到执行完成`。
- 通知闸门排查：`Notify.SendTaskResultTime=不推送` 时任务报告只在 UI/系统通知出现（外部渠道静默）——「没收到推送」先查这三个闸门 + `NotifyEnabled`。
- 别改成「全靠 AutoProxy 随任务签到」：任务触发的签到被吞过一次就整天没签（并入报告模式无独立通知）。
- koishi 是**出向**客户端（`utils/websocket.py`），配置在工具设置里；调试其消息用 `/api/ws_debug/live`。

---

## 附录 A：文件 SHA1 对照表（线上 `E:\AUTO-MAS\app` vs 本地 `D:\MAS` v5.4.0）

2026-09-27 21:0x 实测（certutil/sha1sum）。✅=逐字节一致（本地行号=线上行号）；❗=不同（本文行号按线上副本，已逐段复核；本地 D:\MAS 对应文件较旧/较新，勿用其行号）。

| 文件 | 线上 SHA1 | 一致性 |
|---|---|---|
| core/task_manager.py | e95a5c64… | ✅ |
| core/timer.py | 4a7df68d… | ✅ |
| core/emulator_manager.py | f6f9bbc0… | ✅ |
| core/maa_manager.py | 6212e835… | ✅ |
| core/broadcast.py | cc6a2982… | ✅ |
| core/config.py | 51347d23… | ❗（本地 e4644b35） |
| models/emulator.py | aec71294… | ✅ |
| models/config.py | 79d96629… | ✅ |
| models/ConfigBase.py | 2a8a2a76… | ✅ |
| models/task.py | 4ee43aac… | ✅ |
| utils/emulator/{mumu,general,tools,ldplayer,__init__}.py | 1b12affb/df1f4cf7/2d57ff33/f8e0147f/1407ba91 | ✅ |
| task/MAA/{manager,ScriptConfig,ManualReview}.py | d6291606/47a96b4a/1ba8611a | ✅ |
| task/MAA/AutoProxy.py | 384ba36d… | ❗（本地 f3342312；线上含 09-27 05:03 切号热修） |
| api/{core,ws_command,ws_debug,dispatch,emulator}.py | ed6512c5/7c716be4/9cb913a0/d253b309/06b59993 | ✅ |
| services/{notification,system}.py | c0cb9fba/0a981bed | ❗ notification（本地 9959fb5b）；system ✅ |
| tools/{skland,game_sign,game_sign_notify,game_sign_result}.py | f7134e23/77c0d948/42f512ab/02d63767 | ✅ |
| utils/{websocket,ProcessManager}.py | 90a7aab6/47184c1c | ✅ |
| utils/constants.py | f5c34b59… | ❗（本地 46c6fa19） |

（线上根目录 = git 检出，HEAD `491c40a2f8c903ad33e0d31e9528bedab0bec41f`（2026-09-27 复核）；运行入口 = `environment\python\python.exe main.py`（wmic 复核），`app/` 即运行时真源，`repo/` 仅更新跟踪。）

## 附录 B：本次只读操作面（可复核）

- 远端：`type`（app.log/main.py/配置切片——脚本仅打印白名单字段：脚本名、TaskTransitionMethod、RunTimesLimit、Emulator.Index/Id、用户数、队列名/TimeEnabled/LastUpstart 等，凭据不落地）、`certutil -hashfile SHA1`、`tar -cf - app`（拉线上源码副本）、`powershell -Command -`（只读遍历）。**未写任何文件、未调任何写 API、未发车/停任务/动实例。**
- 本地：`D:\1-2\MAAOrch` 只新增本文档；scratch 工作区存有线上 app 副本与 app.log（11.5MB，21:13 抓取）。
- 交叉引用：`docs/audits/2026-09-27_mas_task_modes.md`（基线）、`docs/audits/2026-09-27_mas_account_switch_reset.md`（Temp 还原取证）、`docs/audits/2026-09-27_hermes_takes_over_mas_scheduler.md`（发车链路）、kanban t_aaacd03c 报告（桌面留机现场）。
