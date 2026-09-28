# MAS(AUTO-MAS) 任务模型：三种模式与 MaaManager 流水线（只读研究）

> 2026-09-27 ｜ 执行：mas（kanban t_806dac03）｜ **只读**：未改任何配置、未发车、未 stop 任务，AUTO-MAS 未受影响。
> 行号口径 = **线上运行代码** `E:\AUTO-MAS\app\`。被引 9 个后端文件与本地 `D:\MAS` v5.4.0 逐字节一致（SHA1 实测）；
> `AutoProxy.py` 取线上现状（09-27 05:03 应用过 t_e4620caa 的 AccountSwitchEnabled 热修）；`constants.py` 行号亦为线上。
> 只读探针：`POST /api/queue/get` + `/api/queue/time/get`（2026-09-27 05:14）；线上 HEAD=`491c40a2`。

## 0. 摘要

- 唯一入口 `POST /api/dispatch/start {taskId, mode}`；三种模式共用 `TaskManager.add_task → Task → MaaManager` 一条流水线，
  差异只在两处：**taskId 怎么解析** ＋ **每用户子任务跑什么**。
- **只有 `AutoProxy` 会跑完整任务链**（真干活）；`ManualReview` 只跑到登录（StartUp）＋人工判收；
  **`ScriptConfig` 只把配置装进 MAA 并拉起 GUI，不跑任务链** —— 这正是我们踩过的坑。
- taskId 语义（schema.py:2245-2249 原文）：代理类 = 队列ID 或 脚本ID；设置类 = 脚本ID 或 用户ID。

## 1. 入口与 taskId 解析

- API：`app/api/dispatch.py:33-52`（start）/ `:55-70`（stop）；后端 handler 均在 `TaskManager`。
- UI：调度台模式下拉（自动代理/人工排查，schedulerConstants.ts:39-40；发起 useSchedulerLogic.ts:443-460）；
  脚本卡片「MAA配置」Scripts.vue:1150-1164（ScriptConfig，taskId=脚本uid）；用户编辑页「MAA配置」MAAUserEdit.vue:940-942（taskId=用户uid）；
  首页快捷启动（默认 AutoProxy，useHomeQuickStart.ts:102）。
- 解析（task_manager.py:287-316，分支顺序即优先级）：

| mode | taskId 传 | 解析结果 |
|---|---|---|
| ScriptConfig | 脚本 uid | script=uid，user="Default"（L289-294） |
| ScriptConfig | 用户 uid | 扫各脚本 UserData 找属主 → script+user（L296-302） |
| AutoProxy / ManualReview | 队列 uid | queue=uid → 执行队列全部脚本项（L305-309；Task.prepare L86-99） |
| AutoProxy / ManualReview | 脚本 uid | script=uid → 该脚本**全部启用用户**（L310-314；manager.py:151-159） |
| 任意 | 其它 UUID | `ValueError`（L315-316） |

- 目标脚本已被锁（运行中）→ 直接拒「已在运行」（L318-321）。
- 返回 `{taskId: <新 task_uid>}`；**stop 必须用这个 task_uid**（不是队列/脚本 uid）；对已结束任务的 stop 视为成功（L397-400）。

## 2. 三个触发者（除手动外）

- **scheduled_task**（AUTO-MAS 自带定时）：主定时器每秒 tick（timer.py:125-136→166-198）；命中条件 = 队列 `Info.TimeEnabled`（L174）
  ＋定时项 `Enabled`＋`Days` 含今天＋`Time` 到分（L181-186）；命中后以 AutoProxy 发该队列（L188-197），并写 `Data.LastTimedStart` 防同分钟重复（L178-179）。
- **startup_task**：AUTO-MAS 启动 10s 后跑 `StartUpEnabled` 的队列（task_manager.py:409-443）。
- **manual_task**：API/UI 默认（L272）。触发来源只影响两件事：签到归因（L120-130）与队列跑完的电源动作（L232-244）。

## 3. 三种模式各自跑什么（MaaManager 之下的子任务）

公共骨架：Task 对每个脚本建 `MaaManager`（task_manager.py:192-193），逐个 `await spawn` **串行**执行（L220；子任务/异常语义 models/task.py:199-217）。

- **AutoProxy（真跑链）**：开模拟器 → `set_maa` 装配用户配置（**RunDirectly=True**，AutoProxy.py:720/728；TaskQueue 导出 L896）
  →「剿灭 → 日常」两轮依次跑（L464 起），每用户日志锚点 `开始代理用户: <uid>`（L397）；每日状态初始化 L378-381。
- **ManualReview（跑到登录就停）**：`set_maa` 只导出 `TaskQueue=[StartUp]`（ManualReview.py:396），跑完自退（ExitSelf=8，L332-336）；
  等 gui.log「完成任务: StartUp」→ Success（check_log L405-436，wait_event.set L435）→ **弹窗问人**「是否正确完成代理？」（L237-251）
  → 结论写 `IfPassCheck`（L448-453）。即：登录自动化＋人工判收，不跑日常链。
- **ScriptConfig（不跑链，坑）**：kill MAA → 把该用户 ConfigFile 拷进 MAA 配置目录 → 强制 `RunDirectly=False`＋`StartEmulator=False`
  ＋关 MAA 自带全部定时/更新（ScriptConfig.py:125-161，注释原文「不直接运行任务」L125）→ **只 open_process(MAA.exe) 拉起 GUI 后
  `wait_event.wait()` 无限等待**（L70-78）；该类**没有任何地方 set 该事件**——结束只能靠外部 stop；
  stop 后 final_task 杀 MAA，并把 GUI 里的改动**回写** `data/<script>/<user>/ConfigFile`（L176-195，即「存配置」）。
  ⇒ 拿 ScriptConfig 等任务链 = 永远等不到（我们的历史踩坑点：tools/mas_startup_verify.py:132）。要真跑只能 AutoProxy。

## 4. MaaManager 生命周期（app/task/MAA/manager.py）

1. **check**（L67-119）：mode 白名单（L69-70）→ 是 MaaConfig 类型（L71-74）→ 模拟器已配（L75-83）→ MAA.exe 存在（L84-92）
   → gui.json+gui.new.json 存在（L93-111）→ **仅非 ScriptConfig** 才要求 `data/<sid>/Default/ConfigFile`（L112-118）。
   不过关：websocket 报错、脚本=异常、直接结束（L166-174）。
2. **prepare**（L121-162）：**lock 脚本配置**（L125；锁后一切配置写被拒，ConfigBase.py:1100 一带）→ 加载用户配置（L127-129）
   → 备份 MAA 配置到 `data/<sid>/Temp`（L139-142）→ 组用户列表：非 ScriptConfig = `Status 启用 且 RemainedDay≠0`（L151-159）；
   ScriptConfig = 单用户（uid 或 "Default"，L145-150）。
3. **main_task**（L164-189）：check → prepare → for 每用户 `await spawn(METHOD_BOOK[mode])`（L182-189）——**逐用户串行**。
4. **final_task**（L191-262）：check 未过 → 异常返回（L194-196）；**unlock**（L199）；AutoProxy/ManualReview 额外：关模拟器＋
   用户数据落盘＋完成率通知（L202-254）；**还原** MAA 配置 Temp→config（L256-260）；脚本=完成（L262）。
5. **on_crash**（L264-272）：脚本=异常＋报错。子任务 final_task 在 finally 里 shield 执行（models/task.py:199-212）
   ⇒ 即使被 stop（cancel）也会走 unlock＋还原＋回写。

## 5. 与「Hermes cron 接管发车」的接口面

- **发车**：`tools/mas_dispatch.py:158` `POST /api/dispatch/start {taskId:<队列uid>, mode:"AutoProxy"}`；
  闸门=队列名白名单（L49）＋可达 fail-closed（L152-154）＋当天单飞（L150-151）＋`NO_GO`（L148-149）＋返回体 code==200 且带 taskId（L161-165）；
  审计 L166-168。链路（cron → mas_shift_reconcile.py → mas_dispatch.py）见 `docs/audits/2026-09-27_hermes_takes_over_mas_scheduler.md`。
- **停机**：`/api/dispatch/stop {taskId:<start 返回的 task_uid>}`（dispatch.py:55-70；task_manager.py:396-407）。
  用途：配置被锁/卡死任务止损——走它才会触发 final_task 的 unlock＋还原（manager.py:199/256-260）；不要 kill MAA。
- **定时项 Enabled 语义**（timer.py:174-186；schema.py:384/407-409）：**两层开关**——队列级 `TimeEnabled`（总闸）＋条目级 `Enabled`；
  另需 `Days` 含今天、`Time`=当前分钟。**线上只读实测（05:14）**：24 条班次队列 `TimeEnabled=true`、24 个定时项全部 `Enabled=false`、
  Days=全周、Time 保留（早 05:00..05:33 / 晚 19:30..20:03）⇒ AUTO-MAS 侧不会再自发车；
  回滚=置回 Enabled（`tools/mas_timers.py --enable`）＋同步停 Hermes 发车（`hermes -p mas cron pause 2600f6415f16`）——只做一条会双发/断发。
- 注意：经 API/调度触发的任务，**只有 mode=AutoProxy 会真跑任务链**；ScriptConfig 仅用于「打开 MAA 改配置」。

## 附：随本页确认的在线事实

- 线上 `E:\AUTO-MAS\app\task\MAA\AutoProxy.py` mtime=2026-09-27 05:03:05，为 t_e4620caa 的 AccountSwitchEnabled 热修
  （同目录备份 `.bak-accswitch-20260927-050305`）；本页 AutoProxy 行号已按该现状复核。
- 运行进程=`E:\AUTO-MAS\environment\python\python.exe E:\AUTO-MAS\main.py` ⇒ `app/` 包是运行时真源，`repo/`=更新跟踪检出 @491c40a2。
