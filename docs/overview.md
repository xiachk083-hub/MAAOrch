# MAAOrch 项目功能概览

## 项目架构总览

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             用户界面层                                            │
├──────────────────────────────────────────────────────────────────────────────────┤
│ ┌──────────────┐┌────────────┐┌───────────────┐┌───────────────┐┌────────────┐  │
│ │ 智能调度面板  ││ 侧边栏     ││ 创建账号对话框 ││ 模拟器选择器  ││ 任务配置   │  │
│ │ smart_panel  ││ side_bar   ││ create_account││ emu_selector  ││ task_config│  │
│ └──────┬───────┘└─────┬──────┘└───────┬───────┘└───────┬───────┘└──────┬─────┘  │
│        │              │               │               │              │          │
│        └──────────────┴───────────────┴───────────────┴──────────────┘          │
│ ┌──────────────┐┌────────────┐┌───────────────┐┌─────────────┐                 │
│ │ 智能全局配置  ││ 账号详情   ││ 重建进度对话框 ││ 定时器轮询  │                 │
│ │ smart_config ││detail      ││ rebuild_dialog││ main_poll   │                 │
│ └──────┬───────┘└─────┬──────┘└───────┬───────┘└──────┬──────┘                 │
│        │              │               │               │                         │
├────────┴──────────────┴───────────────┴───────────────┴────────────────────────┤
│                            服务层                                               │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│ ┌──────────────────────┐ ┌─────────────────────────┐ ┌─────────────────────────┐│
│ │ 配置写入注入          │ │ MAA 实例池管理            │ │ 进程与队列管理            ││
│ │ services/config_injector.py   │ │ instance_pool.py         │ │ launch_queue + runner   ││
│ │                      │ │                          │ │                         ││
│ │ 读账号配置            │ │ maa/source/ 用户目录      │ │ 排队 → 分配实例           ││
│ │ 生成 gui.json         │ │ → 目录软链接              │ │ → 后台线程启动 MAA       ││
│ │ 生成 gui.new.json     │ │ instances/1..N           │ │ → 监控日志 → 清理 → 重试 ││
│ │ 注入 TaskQueue        │ │ 每个实例独立配置          │ │ → 诊断收集                ││
│ │ 字段级合并(全局+覆盖)  │ │ 实例预留防竞争            │ │ PostActions="12" MAA自退 ││
│ │ PostActions="12"      │ │ junction 符号链接         │ │                          ││
│ └──────────────────────┘ └─────────────────────────┘ └─────────────────────────┘│
│                                                                                  │
│ ┌──────────────────────┐ ┌─────────────────────────┐ ┌─────────────────────────┐│
│ │ 智能调度引擎          │ │ ADB 检测与连接            │ │ 环境检测与修复            ││
│ │ services/smart_scheduler.py   │ │ infrastructure/task_constants.py        │ │ services/health_check.py         ││
│ │                      │ │                          │ │                         ││
│ │ 理智阈值触发          │ │ detect_emu_instances()   │ │ 检查 10 项               ││
│ │ 基建时间触发          │ │ 动态获取 ADB 端口         │ │ Python/PySide6/ADB/MAA  ││
│ │ 材料库存监控          │ │ adb connect 验证可达性     │ │ 配置/实例/备份/日志       ││
│ │ 剿灭自动检测          │ │ 备选公式端口回退           │ │ 一键修复                 ││
│ │ cache 读锁防并发      │ │ 3 次重连 + 1s 稳定延迟    │ │                         ││
│ └──────────────────────┘ └─────────────────────────┘ └─────────────────────────┘│
│                                                                                  │
│ ┌──────────────────────┐ ┌─────────────────────────┐ ┌─────────────────────────┐│
│ │ 异常自动恢复          │ │ 性能资源监控              │ │ 日志系统                 ││
│ │ runner._cleanup      │ │ runner._check_resources  │ │ infrastructure/logger  ││
│ │                      │ │                          │ │                         ││
│ │ 指数退避重试 (5→300s) │ │ 系统可用内存 < 4GB        │ │ TRACE/DEBUG/INFO/WARN   ││
│ │ 1h 超时 → 队尾重排    │ │ 自动暂停新启动            │ │ 自动旋转 512KB/3 备份    ││
│ │ 重启模拟器(失败≥3)    │ │ 单进程 > 4GB 杀          │ │ debug.log(全部级别)      ││
│ │ 诊断收集保存           │ │ 状态栏显示内存使用        │ │ events.log(INFO+ JSON)  ││
│ │ 连续 6+ 次暂停 30min  │ │                          │ │ crash.log(CRASH only)   ││
│ │ crash loop 保护       │ │                          │ │ 调用函数+行号+线程 ID    ││
│ └──────────────────────┘ └─────────────────────────┘ └─────────────────────────┘│
│                                                                                  │
│ ┌──────────────────────┐ ┌─────────────────────────┐                             │
│ │ 主题系统              │ │ HTTP API 服务器          │                             │
│ │ app/themes.py            │ │ network/api_server.py            │ │
│ │                      │ │                          │                             │
│ │ 暗色主题              │ │ 15+ 端点                 │                             │
│ │ 亮色主题              │ │ hmac 鉴权                │                             │
│ │ Notepaper 暖白纸张    │ │ /api/node/register       │                             │
│ │ 竹绿配色              │ │ /api/node/heartbeat      │                             │
│ │ DWM 暗色标题栏        │ │ 限流 60 req/min/IP       │                             │
│ └──────────────────────┘ └──────────────────────────┘                             │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## MAA 实例池架构

```
用户目录 maa/source/（用户自行管理版本和完整性）
     ↓
     ↓ _find_maa_source() 优先检查 source/
     ↓ _create_instance() 复制文件 + 创建软链接
     ↓ _init_maa_source() 静默运行 MAA.exe 生成 $type 配置
     ↓
实例池 maa/instances/{1..N}/
     ↓
     ├─ MAA.exe              → 复制
     ├─ MAA.deps.json        → 复制 (.NET 运行时必需)
     ├─ MAA.runtimeconfig.json → 复制
     ├─ MaaCore.dll          → 复制
     ├─ ... 其他 DLL          → 复制
     │
     ├─ config/               → 从 source 复制（含完整 TaskQueue + $type）
     │  ├─ gui.json          → MAA v5 格式（不含 TaskQueue）
     │  └─ gui.new.json      → MAA v6 格式（含 TaskQueue + $type）
     │
     ├─ resource ──mklink /J──→ maa/source/resource    (共享, ~240MB)
     ├─ externals ──mklink /J──→ maa/source/externals  (共享, ~202MB)
     ├─ Python ────mklink /J──→ maa/source/Python      (共享)
     │
     ├─ cache/               → 新建（空，运行时 MAA 写入）
     ├─ data/                → 新建（空）
     └─ debug/               → 新建（asst.log + gui.log）
```

**启动同步逻辑**：

```
每次启动 ensure_maa_instances_async()
  ├─ 比较 maa/source/MAA.exe → instances/1/MAA.exe 的修改时间+大小
  │  ├─ 不同 → 删除旧实例，重建全部
  │  └─ 相同 → 跳过
  │
  ├─ 版本变化 (maa_version ≠ maa_instances_version)
  │  └─ 删除旧实例，从新源重建
  │
  └─ 用户点「重建实例」
      └─ 强制删除全部 → 重建（含进度对话框）
```

---

## 账号启动 — 完成闭环

```
  ┌─────────────────────┐
  │ 触发条件             │
  │ 智能调度 / 定时 / 手动 │
  └──────────┬──────────┘
             │
  ┌─────────────────────┐
  │ 入队等待             │
  │ LaunchQueue          │
  │ 优先级队列 + 平行上限 │
  │ 模拟器独占           │
  └──────────┬──────────┘
             │
  ┌─────────────────────┐
  │ 分配空闲实例         │
  │ _get_free_instance() │
  │ maa/instances/{N}   │
  │ 先检查 MAA.exe 存在  │
  │ 再检查 .pid 是否运行 │
  └──────────┬──────────┘
             │
  ┌──────────────────────────────────────────────┐
  │ _do_launch() — 实例预留 + 启动后台线程         │
  │                                              │
  │  ├─ 立即标记 _procs[aid] = inst_dir (string) │
  │  │  防止两个账号抢同一个实例                   │
  │  │                                            │
  │  └─ Thread(_launch_job).start()               │
  │     (daemon 线程)                              │
  └──────────────────┬───────────────────────────┘
                     │
  ┌──────────────────────────────────────────────┐
  │ _launch_job() — 后台线程                      │
  │                                              │
  │  1. 启动模拟器 (emu_launch=True)              │
  │     mumu-cli control launch                   │
  │     ├─ 检查是否已在运行                        │
  │     └─ 未运行 → 启动                           │
  │                                              │
  │  2. 等待模拟器就绪 (两阶段)                    │
  │     ├─ Phase 1: adb connect                   │
  │     │  轮询直到 adb 端口可达                   │
  │     └─ Phase 2: Android 开机完成               │
  │        adb shell getprop sys.boot_completed   │
  │        = "1" 时继续                           │
  │                                              │
  │  3. ADB 动态检测                              │
  │     detect_emu_instances() 获取当前端口       │
  │     ├─ adb connect 可达 → 使用检测端口         │
  │     └─ 不可达 → 回退公式端口 (16384+idx*32)    │
  │     3次重连 + 1s 稳定延迟                     │
  │                                              │
  │  4. 注入配置                                  │
  │     inject_smart()                            │
  │     ├─ gui.new.json (TaskQueue + $type)       │
  │     └─ gui.json (v5 扁平键 + PostActions=12)  │
  │                                              │
  │  5. 清除旧日志 + 启动 MAA                     │
  │     asst.log.write_text("")                   │
  │     _log_positions[aid] = 0                   │
  │     subprocess.Popen(MAA.exe)                 │
  │     _procs[aid] = Popen 对象                  │
  └──────────────────┬───────────────────────────┘
                     │
  ┌──────────────────────────────────────────────┐
  │ 实时监控（每 2s 轮询）                         │
  │                                              │
  │ _update_status()                             │
  │  ├─ 增量读取 asst.log (_log_positions[aid])   │
  │  ├─ 当前任务名：唤醒 / 刷关 / 基建 / 公招      │
  │  └─ ERR → 日志记录                            │
  │                                              │
  │ _check_one() — 卡死检测                       │
  │  ├─ p.poll() is None (进程存活)               │
  │  ├─ 增量日志无新内容超过 stuck_timeout_min     │
  │  └─ 超时 → _cleanup(aid, -3) → 队尾重排      │
  │                                              │
  │ _check_resources() — 性能监控                 │
  │  └─ psutil.virtual_memory()                   │
  │     ├─ 可用内存 < 4GB → 暂停新启动             │
  │     └─ 单进程 > 4GB → kill                    │
  └──────────────────┬───────────────────────────┘
                     │
  ┌──────────────────────────────────────────────┐
  │ 完成 → 出错                                    │
  ├──────────────────────────────────────────────┤
  │                                              │
  │ 正常完成 (exit=0):                            │
  │  ├─ _parse_log() → 任务状态/掉落/理智         │
  │  ├─ RunStats.save_run() → stats.json          │
  │  ├─ mark_annihilation_done()                  │
  │  ├─ rotate_log()                              │
  │  ├─ MAA 自行退出 (PostActions="12")           │
  │  └─ account_finished 信号 → 队列处理下一个     │
  │                                              │
  │ 异常退出 (code≠0 且无完成任务):                │
  │  ├─ consecutive_failures++                    │
  │  ├─ _collect_diagnostic()                     │
  │  │  └─ diagnostics/{name}_{ts}/               │
  │  │     ├─ screenshot.png (ADB 截图)           │
  │  │     ├─ asst.log (最后 100 行)              │
  │  │     ├─ asst_tail.log (运行中 200 行)       │
  │  │     └─ info.txt                            │
  │  │                                            │
  │  ├─ 指数退避重试 → 队尾重排                    │
  │  │  #1→5s  #2→10s  #3→20s  #4→40s  #5→80s   │
  │  │  #6+→300s (上限)                           │
  │  │                                            │
  │  ├─ 超时 (-3) → 队尾重排                       │
  │  ├─ 失败≥3 次 → 重启模拟器 (mumu-cli restart) │
  │  └─ 连续失败 ≥6 → 暂停该账号 30 分钟 + 通知    │
  └────────────────────────────────────────────────┘
```

---

## 智能调度流程

```
决策触发（每 30s 或手动）
     ↓
     ↓
┌───────────────────────────────────────────────┐
│ 检查每个账号                                   │
│                                               │
│ ├─ 缺 ADB 地址或模拟器索引？                    │
│ │  ├─ 是 → 跳过（日志记录"N个缺配置跳过"）       │
│ │  └─ 否 → 继续                                │
│ │                                              │
│ ├─ 已在队列或运行中？→ 跳过                     │
│ ├─ 5 分钟内出错过？→ 跳过                       │
│ │                                              │
│ ├─ 是否是基建时间？                             │
│ │  (04:00/16:00 → 15 分钟窗口 + 2h 补跑)       │
│ │   → 触发全任务（含基建/公招/信用/仓库）        │
│ │                                              │
│ ├─ 体力是否 ≥ stamina_threshold_pct 阈值？       │
│ │   → 触发刷关（读 stats.json，90s 缓存）        │
│ │                                              │
│ └─ 材料库存不足？                               │
│     → 触发刷材料关卡（读 depot.json 缓存）       │
│                                               │
│ 每周一自动检测剿灭（Annihilation）               │
│  → 排到 Fight 任务                             │
└─────────────────────┬─────────────────────────┘
                      │
                      │
               ┌───────────────┐
               │ 生成任务列表    │
               │ enqueue 入队   │
               │ 优先级 max+1   │
               └───────────────┘
```

---

## 数据流

```
用户操作
    ↓
    ↓
UI 面板 (smart_panel + side_bar)
    ↓
    ↓
ServiceContext / 信号事件
    ↓
    ├─→ launch_queue.enqueue(aid, "schedule")
    │       ↓
    │       ↓
    │   launch_queue._tick() (每 30s 自动 / 手动)
    │       ↓
    │       ↓
    │   runner.launch_by_id(aid)
    │       ↓
    │       ↓
    │   _get_free_instance() → maa/instances/{N}
    │       ↓
    │       ↓
    │   _do_launch(ac, inst)
    │       ├─ _procs[aid] = inst_dir  (预留，string 占位)
    │       └─ Thread(_launch_job).start()
    │           ↓
    │           ├─ detect_emu_instances() (动态获取 ADB 端口)
    │           ├─ launch emulator (emu_launch=True)
    │           ├─ wait: adb connect → boot_completed
    │           ├─ cfg.inject_smart(task_list, ac, config_dir)
    │           │       ↓
    │           │   config_injector.py:_write()
    │           │       ↓
    │           │   gui.json (v5 + PostActions=12)
    │           │   gui.new.json (v6 + TaskQueue)
    │           │
    │           └─ _spawn_instance(exe, ac, inst_dir)
    │                   ├─ 清空 asst.log
    │                   ├─ _log_positions[aid] = 0
    │                   ├─ subprocess.Popen → MAA.exe
    │                   └─ _procs[aid] = Popen 对象
    │
轮询 (每 2s QTimer)
    │
    ├─→ runner.check_processes()
    │       ↓
    │       ├─ _update_status(aid)
    │       │   ├─ 增量读取 asst.log (_log_positions[aid])
    │       │   └─ 解析任务名 / ERR
    │       │
    │       ├─ _check_one(aid)
    │       │   ├─ 进程存活？→ 卡死检测
    │       │   └─ 进程退出？→ _cleanup()
    │       │           ↓
    │       │       _parse_log() → tasks/sanity/drops
    │       │           ↓
    │       │       RunStats.save_run() → stats.json
    │       │           ↓
    │       │       account_finished 信号
    │       │           ↓
    │       │           ├─→ main_window._on_account_finished()
    │       │           │   └─ smart_pending 补跑
    │       │           │
    │       │           └─→ launch_queue.on_account_finished()
    │       │                    ├─ 正常: 释放实例 → 处理下一个
    │       │                    ├─ 超时(-3): 队尾重排
    │       │                    └─ 错误: 指数退避 → 失败≥3重启模拟器
    │       │
    │       └─ _check_resources()
    │               ↓
    │           psutil.virtual_memory()
    │           可用内存 < 4GB → 暂停新启动
    │
    ├─→ main_poll.do_smart_tick()
    │       ↓
    │   smart_scheduler.decide()  → enqueue
    │
    └─→ refresh_queue_view() → UI 更新
```

---

## 文件功能总览

| 文件 | 功能 | 关键类/函数 |
|------|------|------------|
| `app/main_window.py` | 主窗口、侧边栏、工具栏、状态栏、定时器 | `MainWindow`, `_smart_tick`, `_poll`, `_ra`, `_rw` |
| `ui/smart_panel.py` | 账号卡片列表、批量操作、状态追踪、右键菜单 | `SmartPanel`, `_get_selected_indices` |
| `ui/side_bar.py` | 状态过滤器（运行中/排队/错误/暂停）、总览全选 | `SideBar`, `_run_smart_all`, `_toggle_smart` |
| `ui/create_account.py` | 新建账号对话框、任务预设、EmulatorSelector 集成 | `CreateAccountDialog` |
| `ui/emu_selector.py` | 模拟器实例选择器、搜索分组、运行状态指示 | `EmulatorSelector` |
| `ui/task_config.py` | 账号级任务配置 (7 选项卡)、全部参数注入 | `TaskConfigDialog` |
| `ui/smart_config.py` | 智能调度全局默认配置 (5 选项卡) | `SmartConfigDialog` |
| `ui/account_detail.py` | 账号设置对话框、emu_instance 下拉联动 | `AccountDetailDialog` |
| `ui/main_poll.py` | 定时轮询入口、do_smart_tick、健康检查 | `do_poll`, `do_smart_tick`, `do_health_check` |
| `ui/rebuild_dialog.py` | 重建实例进度对话框 | `RebuildDialog` |
| `services/launch_queue.py` | 启动队列、优先级排队、模拟器独占、平行限制、重试 | `LaunchQueue`, `enqueue`, `_tick`, `on_account_finished` |
| `services/runner.py` | MAA 全生命周期（后台线程启动/ADB检测/监控/卡死/清理/重试/诊断） | `AccountRunner`, `launch`, `_launch_job`, `_check_one`, `_cleanup`, `_update_status` |
| `services/config_injector.py` | MAA 配置写入（gui.json + gui.new.json 字段级注入） | `ConfigService`, `inject_smart`, `inject` |
| `services/instance_pool.py` | 实例池创建/删除/重建、MAA 下载、静默初始化 | `ensure_maa_instances_async`, `_create_instance`, `_init_maa_source` |
| `services/smart_scheduler.py` | 智能调度决策（理智/时间/材料/剿灭）、cache 读锁 | `decide`, `_check_sanity_above_threshold`, `_cached_read_json` |
| `services/update_service.py` | 更新下载、zip 提取、zip slip 防护 | `download_update`, `is_safe_zip_path` |
| `infrastructure/logger.py` | 日志系统（TRACE~CRASH、自动旋转、JSON 事件日志） | `Logger`, `debug.log`, `events.log`, `crash.log` |
| `infrastructure/task_constants.py` | 任务模板、模拟器检测、ADB 查找、状态枚举、预设 | `TASK_NAMES`, `TASK_DEFAULTS`, `find_mumu_cli`, `detect_emu_instances` |
| `infrastructure/utils.py` | 工具函数（原子写、zip slip 检测、代理设置） | `atomic_write`, `is_safe_zip_path`, `setup_proxy` |
| `network/api_server.py` | HTTP REST API (15+ 端点)、hmac 鉴权、限流 | `ApiServer` |
| `models/account.py` | 账号数据模型（dataclass，完整字段集） | `Account`, `from_dict`, `to_dict` |
| `models/config_manager.py` | config.json 读写（原子写）+ 备份 + 迁移 | `load_config`, `save_config`, `migrate` |
| `models/queue_entry.py` | 冻结队列条目（防 sort_key 突变） | `QueueEntry` |
| `app/themes.py` | 暗色/亮色/Notepaper 主题 | `DARK_STYLE`, `LIGHT_STYLE`, `NOTEPAPER_STYLE` |
| `services/health_check.py` | 环境检测与修复（10 项检查） | `run_health_check`, `show_health_dialog` |

---

## 关键数据结构

```
config.json 顶层字段
══════════════════════════════════════════════
  version: 5
  appearance_mode: "Dark" / "Light" / "Notepaper"
  parallel_max: 并行实例上限 (1-10)
  maa_version: "v6.12.0"
  maa_instances: 当前实例池数量
  maa_instances_version: 实例池基于的 MAA 版本
  accounts: [Account, Account, ...]          ← 39 条
  warehouse: [Program, Program, ...]
  groups: [Group, Group, ...]
  smart_global: 智能调度全局配置 + 任务默认值
  schedule: 定时/周期调度配置
  api_port: 19999
  api_token: "(自动生成的随机 token)"
  queue: []  (已弃用，改用 queue.json)


每个 Account 包含:
════════════════
  id, name, game_client
  adb_path, adb_address, connection_preset
  touch_mode, account_switch
  emu_path, emu_instance_index, emu_launch, emu_wait
  start_minimized, start_directly
  post_action: "ExitEmulator,ExitSelf"
  fight_stage, task_pipeline
  stuck_timeout_min: 10 | 60 (1h 超时)
  stamina_threshold_pct: 80
  smart_stage, smart_annihilation, smart_mon..sun
  smart_materials_enabled
  consecutive_failures, smart_plan (运行时)
  task_settings: {} (账号级任务覆盖)


gui.new.json (MAA v6 格式)
════════════════════════════
  Configurations.Default:
    TaskQueue: [Task, Task, ...]
      $type: "FightTask" / "StartUpTask" / ...
      TaskType: "Fight" / "StartUp" / ...
      IsEnable: true/false
      StagePlan: ["Annihilation"] / ["1-7"]
      UseCustomAnnihilation: true/false
      AnnihilationStage: "Annihilation" / "LungmenDowntown@Annihilation"
    TaskSelectedIndex: 0
    DragItemIsChecked: {}
  ConfigVersion: 1
  Current: "Default"
  GUI: { DarkMode, UseNotify, MinimizeToTray, ... }
```

---

## 配置注入：TaskQueue 处理流程

```
inject_smart(task_list, ac, config_dir)
    ↓
    ├─ 读取 config/gui.new.json
    │
    ├─ 清理 v5 扁平配置键（仅对 gui.new.json）
    │   Connect.Address, Start.ClientType → 删除
    │
    ├─ 加载 TaskQueue
    │  ├─ 有现有 TaskQueue → 使用
    │  └─ 无 TaskQueue → 从 maa/source/ 加载模板
    │
    ├─ 去重：Fight 条目 > 1 时保留最后一个
    │
    ├─ 字段级合并（全局默认 + 账号覆盖 → TaskQueue 参数）
    │
    ├─ 遍历每个条目：
    │  ├─ IsEnable = (task_type in task_set)
    │  ├─ Fight + 需要剿灭？
    │  │  ├─ 有普通刷关 → 第一个 Fight 为普通刷关，
    │  │  │                插入一条剿灭克隆
    │  │  └─ 只有剿灭 → 当前 Fight 改为剿灭
    │  ├─ Fight 刷关 → UseMedicine + UseExpiringMedicine (独立勾选)
    │  └─ 普通 Fight → 清除剿灭设置
    │
    ├─ 双写 gui.json (v5 + PostActions=12)
    │          gui.new.json (v6 + TaskQueue)
    │
    └─ Infrast.DefaultInfrast 同时写入两个文件 (MAA v6 兼容)
```

---

## 日志系统

```
debug.log (MAAOrch 自身日志，全部级别)
──────────────────────────────
[2026-06-11 16:23:24] [启动] ADB(127.0.0.1:16384) Emu(0) 实例#1
[2026-06-11 16:23:26] [MAA] aid=1 当前任务: 刷关
[2026-06-11 16:23:29] [完成] aid=1 退出码=0 耗时=1m03s


events.log (INFO+ 级别，JSON 格式，含 elapsed/func/tid)
──────────────────────────────
{"t":"16:23:24","l":"INFO","m":"启动 ADB(127.0.0.1:16384)","e":"0.000","f":"_launch_job","i":1234}
{"t":"16:23:29","l":"INFO","m":"账号 1 状态 completed","e":"65.000","f":"_cleanup","i":1234}


crash.log (CRASH 级别)
──────────────────────


日志级别: TRACE → DEBUG → INFO → WARN → ERROR → CRASH
自动旋转: 512KB 每文件，保留 3 个备份
调用链: Logger(name) → 从调用栈提取函数名+行号 (+3)
线程 ID: 记录在 events.log 中


asst.log (MAA 运行日志)
──────────────────────────
→ maa/instances/{N}/debug/asst.log
→ MAA 自身写入，MAAOrch 增量读取 (_log_positions[aid])
→ 启动前清空 asst.log，防止上次运行残留触发 AllTasksCompleted
```

---

## 状态栏

```
┌──────────────────────────────────────────────────────────────────┐
│ 就绪                                      │ ●1 │ MEM:6.2GB      │
│                                              /16GB(2)           │
│ ← self.sl(左侧)         → _qsb  → _health  → _resource_lbl    │
└──────────────────────────────────────────────────────────────────┘
```

| 指示器 | 变量 | 格式 | 说明 |
|--------|------|------|------|
| 左侧文本 | `self.sl` | `就绪` / `MAA: 刷关...` | 当前操作状态或 MAA 任务名 |
| 运行/队列 | `self._qsb` | `●1` / `●2 ●3` | 运行中账号数 / 排队等待数 |
| 健康状态 | `self._health_indicator` | 绿色=正常 / 黄色 N个问题 | 环境检测结果，点击打开检测面板 |
| 资源监控 | `self._resource_lbl` | `MEM:6.2GB/16GB(2)` | 系统已用/总内存(MAA进程数)，`⚠` 表示内存不足 |

---

## 关键决策记录

| 决策 | 说明 |
|------|------|
| PostActions=`"12"` | MAA v6 编码: 4=关模拟器 + 8=退MAA，MAA 自行处理退出 |
| MAAOrch 不手动关模拟器 | PostActions 由 MAA 执行，MAAOrch 只监控进程退出 |
| 实例预留 `_procs[aid]=string` | 在 `_do_launch` 时立即写入字符串占位，防两个账号抢同一实例 |
| ADB 动态检测 | `detect_emu_instances()` 获取当前 ADB 端口，验证可达，不可达回退公式 |
| 增量日志 `_log_positions` | 只检查新增日志，防上次运行残留触发 AllTasksCompleted |
| 启动前清空 asst.log | 每次 MAA 启动时清空日志 + 重置 position=0 |
| 1h 超时 → 队尾重排 | 超时后清理 → release 实例 → on_account_finished 重新 enqueue |
| 字段级配置合并 | 不是整字典替换，而是逐字段合并全局默认 + 账号覆盖 |
| Fight 药品分离 | UseMedicine + UseExpiringMedicine 独立勾选 |
| `gui.new.json` 含 `$type` | MAA v6 要求 TaskQueue 条目带 `$type` 字段 |
| 排队默认暂停 | 队列启动时 paused，智能调度首次 enqueue 自动 resume |
| subprocess gbk 修复 | 中文 Windows 必须加 `encoding="utf-8", errors="replace"` |
| API token 自动生成 | 启动时检查，空则生成随机 token，hmac.compare_digest 鉴权 |
| `EmulatorAddCommand` 始终写入 | 只要有 `emu_instance_index` 就写 `control --vmindex N launch`。即使 `emu_launch=True`（MAAOrch 启动），PostActions=12 退出模拟器时 MAA 也需要 VM index |
| ADB 连接失败尝试启动模拟器 | MAA v6 此复选框不存于 gui.json/gui.new.json（内部管理），需用户在 MAA 原版 GUI 手动勾选一次，之后 MAA 会记住 |
| mumu-cli `shutdown` 非 `quit` | `quit` 子命令不存在 |
