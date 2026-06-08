# MAAOrch 项目功能概览

## 项目架构总览

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                             用户界面层                                           │
├───────────────────────────────────────────────────────────────────────────────────┤
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────────┐   │
│  │ 账号面板    │ │ 队列面板    │ │ MAA 实例池 │ │ 仪表盘     │ │ 智能调度面板 │   │
│  │ Accounts   │ │ Queue      │ │ MAA Pool   │ │ Dashboard  │ │ Smart       │   │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └──────┬───────┘   │
│        │              │              │              │               │           │
├────────┴──────────────┴──────────────┴──────────────┴───────────────┴───────────┤
│                             服务层                                               │
├───────────────────────────────────────────────────────────────────────────────────┤
│                                                                                   │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ 配置写入注入        │  │ MAA 实例池管理     │  │ 进程与队列管理              │  │
│  │ config_ops.py      │  │ maint_ops.py       │  │ launch_queue + runner      │  │
│  │                    │  │                    │  │                            │  │
│  │ 读账号配置          │  │ maa/source/ 用户目录│  │ 排队 → 分配实例             │  │
│  │ 生成 gui.json      │  │  ↓ 目录软链接       │  │  → 启动 MAA                │  │
│  │ 生成 gui.new.json  │  │ instances/1..N     │  │  → 监控日志 → 清理 → 重试   │  │
│  │ 注入 TaskQueue     │  │ 每个实例独立配置     │  │  → 诊断收集                 │  │
│  └────────────────────┘  └────────────────────┘  └────────────────────────────┘  │
│                                                                                   │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ 智能调度引擎        │  │ 日志解析与统计     │  │ 环境检测与修复              │  │
│  │ smart_scheduler.py │  │ log_ops + stats   │  │ health_check.py            │  │
│  │                    │  │                    │  │                            │  │
│  │ 理智阈值触发        │  │ 解析 asst.log      │  │ 检查 10 项                  │  │
│  │ 基建时间触发        │  │ 统计运行次数/掉落  │  │ Python/PySide6/ADB/MAA     │  │
│  │ 材料库存监控        │  │ 理智恢复计算       │  │ 配置/实例/备份/日志        │  │
│  │ 剿灭自动检测        │  │ 存档到 stats.json  │  │ 一键修复                   │  │
│  └────────────────────┘  └────────────────────┘  └────────────────────────────┘  │
│                                                                                   │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ 异常自动恢复        │  │ 性能资源监控       │  │ 主题系统                   │  │
│  │ runner._cleanup    │  │ runner._check_     │  │ themes.py                 │  │
│  │                    │  │ resources          │  │                            │  │
│  │ 指数退避重试        │  │                    │  │ 暗色主题                   │  │
│  │ 关游戏 (ADB)       │  │ 系统可用内存 < 4GB │  │ 亮色主题                   │  │
│  │ 重启模拟器实例      │  │ 自动暂停新启动     │  │ Notepaper 暖白纸张主题     │  │
│  │ 诊断收集保存        │  │ 单进程 > 4GB 杀   │  │ 竹绿配色                   │  │
│  │ 连续 6+ 次暂停     │  │ 状态栏显示内存使用  │  │                            │  │
│  └────────────────────┘  └────────────────────┘  └────────────────────────────┘  │
│                                                                                   │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## MAA 实例池架构

```
用户目录 maa/source/（用户自行管理版本和完整性）
     │
     │ _find_maa_source() 优先检测 source/
     │ _create_instance() 复制文件 + 创建软链接
     ▼
实例池 maa/instances/{1..N}/
     │
     ├─ MAA.exe              ← 复制（小文件，几 MB）
     ├─ MAA.dll               ← 复制
     ├─ MAA.deps.json         ← 复制 (.NET 运行时必需)
     ├─ MAA.runtimeconfig.json ← 复制
     ├─ MaaCore.dll           ← 复制
     ├─ MaaUtils.dll          ← 复制
     ├─ ... 其他 DLL          ← 复制
     │
     ├─ config/               ← 从 source 复制（含完整 TaskQueue + $type）
     │   ├─ gui.json          ← MAA v5 格式（不含 TaskQueue）
     │   └─ gui.new.json      ← MAA v6 格式（含 TaskQueue + $type）
     │
     ├─ resource ──软链接──→  maa/source/resource    (共享, ~240MB)
     ├─ externals ──软链接──→  maa/source/externals  (共享, ~202MB)
     ├─ Python ────软链接──→  maa/source/Python     (共享)
     │
     ├─ cache/               ← 新建（空，运行时 MAA 写入）
     ├─ data/                ← 新建（空）
     └─ debug/               ← 新建（asst.log + gui.log）
```

**启动同步逻辑**：

```
每次启动 ensure_maa_instances_async()
  ├─ 比较 maa/source/MAA.exe 与 instances/1/MAA.exe 的修改时间/大小
  │   ├─ 不同 → 删除旧实例 → 重建全部
  │   └─ 相同 → 跳过
  │
  ├─ 版本变化 (maa_version ≠ maa_instances_version)
  │   └─ 删除旧实例 → 从新源重建
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
             ▼
  ┌─────────────────────┐
  │ 入队等待             │
  │ LaunchQueue          │
  │ 优先级队列 + 平行上限 │
  │ 模拟器独占           │
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │ 分配空闲实例         │
  │ _get_free_instance()│
  │ maa/instances/{N}   │
  │ 先检查 MAA.exe 存在  │
  │ 再检查 .pid 是否运行 │
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │ 注入配置             │
  │ inject_smart()      │
  │ → gui.new.json      │
  │                     │
  │ TaskQueue:          │
  │  StartUp            │
  │  Award              │
  │  Fight (剿灭)       │
  │  Fight (刷关)       │
  │  Infrast            │
  │  Recruit            │
  │  Mall               │
  │  UserDataUpdate     │
  │  CloseDown          │
  │                     │
  │ 连接参数:           │
  │  ADB 地址/路径      │
  │  触控模式            │
  │  模拟器启动命令      │
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │ 启动 MAA             │
  │ _spawn_instance()   │
  │ subprocess.Popen    │
  │ → 写 .pid 文件      │
  │ → MAA.exe 读取配置   │
  │   执行任务队列       │
  └──────────┬──────────┘
             ▼
  ┌────────────────────────────────────────────────┐
  │ 实时监控（每 2s 轮询）                          │
  │                                                │
  │  _update_status()                              │
  │   ├─ 读 asst.log 最后 400 字节                  │
  │   │  ├─ 当前任务名：唤醒 / 刷关 / 基建 / 公招   │
  │   │  └─ ERR 行 → 日志记录                      │
  │   │                                             │
  │  _check_one() → 卡死检测                       │
  │   └─ stuck_timeout_min = 10                    │
  │     无任务进展超 10 分钟 → 自动杀进程重启       │
  │                                                 │
  │  _check_resources() → 性能监控                 │
  │   └─ psutil.virtual_memory()                   │
  │     可用内存 < 4GB → 暂停新启动                │
  └──────────────────┬─────────────────────────────┘
                     ▼
  ┌────────────────────────────────────────────────┐
  │ 完成 或 出错                                    │
  ├────────────────────────────────────────────────┤
  │                                                 │
  │  正常完成:                                      │
  │   ├─ _parse_log() → 任务状态 / 掉落 / 理智      │
  │   ├─ RunStats.save_run() → stats.json           │
  │   ├─ mark_annihilation_done()                   │
  │   ├─ rotate_log() → 保留最近 3 次运行            │
  │   └─ account_finished 信号 → 队列处理下一个      │
  │                                                 │
  │  异常退出 (code≠0 且无完成任务):                  │
  │   ├─ consecutive_failures++                     │
  │   ├─ _collect_diagnostic()                      │
  │   │  └─ diagnostics/{name}_{ts}/                │
  │   │     ├─ screenshot.png (ADB 截屏)            │
  │   │     ├─ asst.log (最后 100 行)               │
  │   │     ├─ asst_tail.log (运行中持续采集 200 行) │
  │   │     └─ info.txt (aid/exit_code/失败次数)    │
  │   │                                              │
  │   ├─ 指数退避重试                                │
  │   │  第 1 次 → 5s                               │
  │   │  第 2 次 → 10s                              │
  │   │  第 3 次 → 20s                              │
  │   │  第 4 次 → 40s                              │
  │   │  第 5 次 → 80s                              │
  │   │                                              │
  │   ├─ 重启模拟器 (mumu-cli quit + launch)        │
  │   ├─ ADB force-stop 关游戏                       │
  │   ├─ 每分钟重启 > 4 次 → 限流 5 分钟              │
  │   └─ 连续失败 ≥ 6 → 暂停该账号 30 分钟 + 通知    │
  └──────────────────────────────────────────────────┘
```

---

## 智能调度流程

```
决策触发（每 60 秒或手动）
     │
     ▼
┌─────────────────────────────────────────────┐
│ 检查每个账号                                 │
│                                             │
│  ├─ 有 ADB 地址或模拟器？                    │
│  │   ├─ 无 → 跳过（日志记录"N个缺配置跳过"）  │
│  │   └─ 有 → 继续                           │
│  │                                           │
│  ├─ 已在队列或运行中？ → 跳过                │
│  ├─ 5 分钟内出错过？ → 跳过                  │
│  │                                           │
│  ├─ 是否是基建时间？                          │
│  │  (04:00/16:00 各 15 分钟窗口 + 2h 补跑)   │
│  │   → 触发全任务（含基建/公招/信用/仓库）    │
│  │                                           │
│  ├─ 体力是否 ≥ 80% 阈值？                    │
│  │   → 触发刷关（读 stats.json，30s 缓存）   │
│  │                                           │
│  └─ 材料库存不足？                           │
│      → 触发刷材料关卡（读 depot.json 缓存）   │
│                                             │
│ 每周一自动检测剿灭（Annihilation）            │
│  → 排到 Fight 任务前                         │
└─────────────────────┬───────────────────────┘
                      │
                      ▼
              ┌───────────────┐
              │ 生成任务列表    │
              │ enqueue 入队   │
              └───────────────┘
```

---

## 数据流

```
用户操作
    │
    ▼
UI 面板 (main_window.py)
    │
    ▼
ServiceContext (callbacks.py)
    │
    ├─→ launch_queue.enqueue(aid, "schedule")
    │       │
    │       ▼
    │   launch_queue._tick() (每 30s 自动)
    │       │
    │       ▼
    │   runner.launch_by_id(aid)
    │       │
    │       ▼
    │   _get_free_instance() → maa/instances/{N}
    │       │
    │       ▼
    │   _launch_for_instance()
    │       │
    │       ├─→ cfg.inject_smart(task_list, ac, config_dir)
    │       │       │
    │       │       ▼
    │       │   config_ops.py:_write()
    │       │       │
    │       │       ▼
    │       │   gui.json (v5) + gui.new.json (v6)
    │       │       │
    │       │       └─ 从 maa/source/ 加载 TaskQueue 模板
    │       │          设 IsEnable / StagePlan / Annihilation
    │       │          写入文件
    │       │
    │       └─→ _spawn_instance(exe, ac, inst_dir)
    │               │
    │               ▼
    │           subprocess.Popen → MAA.exe
    │               │
    │               ▼
    │           写 .pid 文件
    │
    ▼
轮询 (每 2s QTimer)
    │
    ├─→ runner.check_processes()
    │       │
    │       ├─→ _update_status(aid)
    │       │       │
    │       │       ▼
    │       │   asst.log 最后 400 字节
    │       │   → 任务名 / ERR 行
    │       │
    │       ├─→ _check_one(aid)
    │       │       │
    │       │       ├─ 进程存活？→ 卡死检测
    │       │       └─ 进程退出？→ _cleanup()
    │       │               │
    │       │               ▼
    │       │           _parse_log() → tasks/sanity/drops
    │       │               │
    │       │               ▼
    │       │           RunStats.save_run() → stats.json
    │       │               │
    │       │               ▼
    │       │           account_finished 信号
    │       │               │
    │       │               ├─→ main_window._on_account_finished()
    │       │               │       └─ smart_pending 补跑
    │       │               │
    │       │               └─→ launch_queue.on_account_finished()
    │       │                       └─ deficit 检测 → 重新入队
    │       │
    │       └─→ _check_resources()
    │               │
    │               ▼
    │           psutil.virtual_memory()
    │           → 可用内存 < 4GB → 暂停新启动
    │
    ├─→ maint.poll() → 守护进程检查
    │
    └─→ refresh_queue_view() → UI 更新
```

---

## 文件功能总览

| 文件 | 功能 | 关键类/函数 |
|------|------|------------|
| `main_window.py` | 主窗口、账号表格、分组管理、状态栏、定时器 | `MainWindow`, `_smart_tick`, `_poll`, `_ra`, `_rw` |
| `launch_queue.py` | 启动队列、优先级排队、模拟器独占、平行限制 | `LaunchQueue`, `enqueue`, `_tick`, `on_account_finished` |
| `runner.py` | MAA 进程全生命周期（启动/监控/卡死/清理/重试/诊断） | `AccountRunner`, `launch`, `_check_one`, `_cleanup`, `_update_status` |
| `config_ops.py` | MAA 配置写入（gui.json + gui.new.json 注入） | `ConfigService`, `inject_smart`, `inject` |
| `maint_ops.py` | 实例池创建/删除/重建、MAA 下载、维护任务 | `ensure_maa_instances_async`, `_create_instance`, `_init_maa_source` |
| `smart_scheduler.py` | 智能调度决策（理智/时间/材料/剿灭） | `decide`, `_check_sanity_above_threshold`, `_get_material_stage` |
| `log_ops.py` | asst.log 解析、运行统计、日志轮转 | `LogService`, `parse_log`, `show_stats`, `rotate_log` |
| `health_check.py` | 环境检测与修复（10 项检查） | `run_health_check`, `show_health_dialog` |
| `themes.py` | 暗色/亮色/Notepaper 主题 | `DARK_STYLE`, `LIGHT_STYLE`, `NOTEPAPER_STYLE` |
| `account.py` | 账号数据模型（dataclass） | `Account`, `from_dict`, `to_dict` |
| `config.py` | config.json 读写（原子写入 + 备份 + 迁移） | `load_config`, `save_config`, `migrate_v4_to_v5` |
| `task_constants.py` | 任务模板、模拟器检测、ADB 查找、状态枚举 | `TASK_NAMES`, `TASK_DEFAULTS`, `find_mumu_cli`, `detect_emu_instances` |
| `emu_ops.py` | ADB 测试/截图、模拟器列表/刷新 | `EmuService`, `test_adb`, `refresh_instance_list` |
| `stats.py` | 运行统计读写 | `RunStats`, `save_run`, `get_last_sanity` |
| `dialogs.py` | 设置/定时/账号编辑/任务配置对话框 | `ScheduleDialog`, `SettingsDialog`, `AccountDialog`, `TaskSettingsDialog` |
| `ui/rebuild_dialog.py` | 重建实例进度对话框 | `RebuildDialog` |
| `background.py` | QThread 包装工具 | `BackgroundTask` |
| `api_server.py` | HTTP REST API（15+ 端点） | `ApiServer` |
| `callbacks.py` | 服务上下文（依赖注入） | `ServiceContext` |
| `schedule_thread.py` | 定时调度线程 | `ScheduleThread` |
| `pipeline_thread.py` | 流水线执行线程（旧版） | `PipelineThread` |

---

## 关键数据结构

```
config.json 顶层字段
═══════════════════════════════════════════════
  version: 5
  appearance_mode: "Dark" / "Light" / "Notepaper"
  parallel_max: 并行实例上限 (1-10)
  maa_version: "v6.11.1"
  maa_instances: 当前实例池数量
  maa_instances_version: 实例池基于的 MAA 版本
  accounts: [Account, Account, ...]          ← 39 个
  warehouse: [Program, Program, ...]
  groups: [Group, Group, ...]
  smart_global: 智能调度全局配置
  schedule: 定时/周期调度配置
  api_port: 19999
  api_token: ""
  queue: []  (已弃用，改用 queue.json)


每个 Account 包含:
═════════════════
  id, name, game_client
  adb_path, adb_address, connection_preset
  touch_mode, account_switch
  emu_path, emu_instance_index, emu_launch, emu_wait
  start_minimized, start_directly, sync_tasks
  fight_stage, task_pipeline
  stuck_timeout_min: 10 (分钟无进展视为卡死)
  smart_stage, smart_annihilation, smart_mon..sun
  smart_materials_enabled
  consecutive_failures, smart_plan (运行时)


gui.new.json (MAA v6 格式)
═════════════════════════════
  Configurations.Default:
    TaskQueue: [Task, Task, ...]
      $type: "FightTask" / "StartUpTask" / ...
      TaskType: "Fight" / "StartUp" / ...
      IsEnable: true/false
      StagePlan: ["Annihilation"] 或 ["1-7"]
      UseCustomAnnihilation: true/false
      AnnihilationStage: "Annihilation" / "LungmenDowntown@Annihilation"
    TaskSelectedIndex: 0
    DragItemIsChecked: {}
  ConfigVersion: 1
  Current: "Default"
  GUI: { DarkMode, MinimizeToTray, ... }
```

---

## 配置注入：TaskQueue 处理流程

```
inject_smart(task_list, ac, config_dir)
    │
    ├─ 读取 config/gui.new.json
    │
    ├─ 清理 v5 扁平配置键（仅对 gui.new.json）
    │   Connect.Address, Start.ClientType 等 → 删除
    │
    ├─ 加载 TaskQueue
    │   ├─ 有现有 TaskQueue → 使用它
    │   └─ 无 TaskQueue → 从 maa/source/ 加载模板
    │
    ├─ 去重：Fight 条目 > 1 → 保留最后一个
    │
    ├─ 遍历每个条目：
    │   ├─ 设 IsEnable = (task_type in task_set)
    │   ├─ Fight + 需要剿灭 →
    │   │   ├─ 有普通刷关 → 第一条 Fight 为普通刷关
    │   │   │                  插入一条剿灭克隆
    │   │   └─ 只有剿灭 → 当前 Fight 改为剿灭
    │   └─ 普通 Fight → 清除剿灭设置
    │
    ├─ 设 TaskSelectedIndex = 0
    │
    ├─ 写 gui.json (v5 格式, 含扁平键, TaskQueue 移除)
    │
    └─ 写 gui.new.json (v6 格式, 含 $type + TaskQueue)
```

---

## 日志系统

```
debug.log (MAAOrch 自身日志)
──────────────────────────────
[16:23:24] [启动] ADB(127.0.0.1:16384) Emu(0) 实例#1
[16:23:26] [MAA] 1 当前任务: 刷关
[16:23:29] [完成] 1 退出码=0 耗时=1m03s
[16:23:29] [账号] 1 状态: completed (exit=0)
[16:23:30] [状态] 运行中: 2/39 | 队列: 0 | 错误: 0


日志类别:
══════════════
[账号]   → 状态变化
[MAA]    → 当前任务 / 错误
[重试]   → 异常后自动重试
[暂停]   → 连续失败 ≥ 6
[模拟器]  → 开关操作
[诊断]   → 异常时收集
[状态]   → 每 30s 汇总
[资源]   → 内存超限 / 恢复
[实例]   → 创建/重建/源变更


asst.log (MAA 运行日志)
──────────────────────────
在 maa/instances/{N}/debug/asst.log
由 MAA 自身写入，MAAOrch 定期读取尾部

---

## 状态栏

```
┌─────────────────────────────────────────────────────────────────┐
│ 就绪                                     ▶2  ⚠ 1  MEM:6.2GB   │
│                                              /16GB(2)          │
│ ← self.sl(左侧)         ← _qsb  ← _health  ← _resource_lbl   │
└─────────────────────────────────────────────────────────────────┘
```

| 指示器 | 变量 | 格式 | 说明 |
|--------|------|------|------|
| 左侧文本 | `self.sl` | `就绪` / `MAA: 刷关...` | 当前操作状态或 MAA 任务名 |
| 运行/队列 | `self._qsb` | `▶8` / `▶8 ⏳5` / `⏳5` | 运行中账号数 / 排队等待数 |
| 健康状态 | `self._health_indicator` | `✅` / `⚠ 2` | 环境检测结果，绿色=正常、黄色=N个问题，点击打开检测面板 |
| 资源监控 | `self._resource_lbl` | `MEM:6.2GB/16GB(2)` | 系统已用内存/总内存(MAA进程数)，后附 `⚠` 表示内存不足新启动已暂停 |
