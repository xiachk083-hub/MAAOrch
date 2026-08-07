# MAA 配置映射手册（MAAOrch → MAA 6.16）

> 本文说明 MAAOrch 如何把账号配置注入 MAA 的配置文件，以及 MAA 6.16 实际从**哪里**读取这些配置。
> 所有结论均来自 6.16.5 源码（`MaaAssistantArknights` v6.16.5 tag）、6.16 source 模板与线上实测日志。
> 最后更新：2026-08-07（修复 6.16 配置迁移后）。

---

## 1. 背景：MAA 配置体系演化

| 版本 | 配置文件 | 读取机制 | 连接/启动设置位置 | MAAOrch 适配 |
|------|----------|----------|-------------------|--------------|
| v5 | `gui.json` 单文件 | `ConfigurationHelper` 扁平键 | `Configurations.Default.Connect.*` / `Start.*` | ✅ 注入扁平键 |
| v6.11.x | `gui.json` + `gui.new.json` | 连接/启动读 gui.json；任务读 gui.new.json（ConfigFactory） | 同上（gui.json） | ✅ 注入扁平键 |
| **6.16** | `gui.json` + `gui.new.json` | 连接/启动**迁移到 gui.new.json 的 `Gui.*` 嵌套区** | `Configurations.Default.Gui.ConnectSettings.*` / `Gui.StartUpSettings.*` | ⚠️ 需注入嵌套结构（已修复） |

**识别依据**：`MaaWpfGui/Constants/ConfigurationKeys.cs` 中标记 `// √` 的键 = **已迁移到 ConfigFactory 新结构**，`ConfigurationHelper.GetValue` 读取的扁平键已不再生效。

**关键源码**（6.16.5）：
- `ConnectSettingsUserControlModel.AutoDetectConnection` 初始值 = `ConfigFactory.CurrentConfig.Gui.ConnectSettings.AutoDetect`
- `StartSettingsUserControlModel.RunDirectly` 初始值 = `ConfigurationHelper.GetValue(ConfigurationKeys.RunDirectly, ...)` → 键 `"Start.RunDirectly"` 标 √ → **已迁移**，实际读取在 `Gui.StartUpSettings.RunDirectly`
- 保存：`ConfigFactory` 通过 `AsstSaveConfig`（C# 侧）写 `gui.new.json`

---

## 2. 双文件读取机制深度对比

| 维度 | `config/gui.json` | `config/gui.new.json` |
|------|-------------------|------------------------|
| 读取方 | `ConfigurationHelper`（MaaWpfGui.Helper） | `ConfigFactory`（MaaWpfGui.Configuration.Factory） |
| 顶层结构 | `Configurations` / `Current` / `Global` | `Configurations` / `Current` / `Timers` / `ConfigVersion` / `Update` / `AnnouncementInfo` / `Gui` |
| 读取方式 | `GetValue(key)` → `Configurations[Current]` 扁平键；`GetGlobalValue(key)` → `Global` 区 | `ConfigFactory.CurrentConfig.Gui.*` 嵌套对象 |
| 保存触发 | `SetValue` / `SetGlobalValue` / `Release`（退出时） | `AsstSaveConfig`（模型序列化；`TaskQueue` 保存后为 `{"type":..., "enable":...}` 格式） |
| `.bak` 机制 | `Load()` 成功即写 `gui.json.bak`；解析失败回退 bak → 无 bak 则默认配置 | 同 |
| **6.16 仍有效** | **`Global` 区**（`GUI.*` 界面设置、热键、`Start.MinimizeDirectly`） | **几乎全部核心设置**：连接、启动、PostActions、任务队列 |
| **6.16 已失效** | `Configurations.Default` 下全部扁平键（`Connect.*` / `Start.*` / `MainFunction.*`） | — |

> ⚠️ **最重要的坑**：MAAOrch 旧代码把连接/启动设置写入 gui.json 扁平键。6.16 不读这些键 → 全部走默认值 → `AutoDetect=true`（注入的 Address 被忽略、ADB 框禁用）、`RunDirectly=false`（不自动连接）、`SkipStartupAutoRunAfterUpdate=true`（更新后跳过运行）→ 同时引发 `AsstSaveConfig` 保存风暴（每秒 20+ 次，占满 GUI 线程，阻塞 `OnUIThread` 的 `LinkStart`）。

---

## 3. gui.new.json 完整结构参考（6.16 source 模板 + 默认值）

### 3.1 `Configurations.Default.Gui.ConnectSettings`

```json
{
  "AutoDetect": true,              // ← 默认开！注入 Address 前必须关
  "AlwaysAutoDetect": false,
  "Config": "General",             // ← 连接配置（MuMuEmulator12 / General / LDPlayer ...）
  "AdbPath": "",
  "AdbReplaced": false,
  "Address": "",                   // ← ADB 地址（AutoDetect 开着时 UI 禁用此框）
  "AddressHistory": [],
  "Extras": {
    "LDPlayer":      { "EmulatorPath": "", "ManualSetIndex": false, "InstanceIndex": 0, "IsEnabled": false },
    "MuMuEmulator12":{ "EmulatorPath": "", "EnableBridgeConnection": false, "EnableTouch": false,
                       "InstanceIndex": 0, "IsEnabled": false },
    "Win32Extra":    { "ScreencapMethod": "FramePool", "MouseMethod": "SendMessageWithCursorPos", "KeyboardMethod": "SendMessage" },
    "BluestacksExtra": { "ConfigKeyword": "", "ConfigPath": "" }
  },
  "AllowAdbRestart": true,
  "AllowAdbHardRestart": true,
  "TouchMode": "MiniTouch",
  "EnableAdbLite": false,
  "KillAdbOnExit": false
}
```

### 3.2 `Configurations.Default.Gui.StartUpSettings`

```json
{
  "RunDirectly": false,                     // ← "启动 MAA 后直接运行"（默认关）
  "SkipStartupAutoRunAfterUpdate": true,    // ← 更新后跳过自动运行（默认开！每次注入必须重置 false）
  "StartEmulator": false,                   // ← "启动 MAA 后自动开启模拟器"
  "RestartEmulatorWhenAdbFailed": false,    // ← "ADB 连接失败时尝试启动模拟器"
  "EmulatorPath": "",
  "EmulatorAddCommand": "",
  "EmulatorWaitSeconds": 60
}
```

### 3.3 `Configurations.Default.Gui.PostActions`

```json
"None"
```

6.16 用**字符串枚举**，不再是 v5/v6.11 的数字编码（`"8"`=ExitSelf、`"12"`=ExitEmulator+ExitSelf）。可选值未在源码中逐一确认，默认保持 `"None"`（MAAOrch 不关模拟器，模拟器由用户/MAAOrch 管理）。

### 3.4 `Configurations.Default.TaskQueue`

条目必须带 `$type`（JSON.NET TypeNameHandling）：

| TaskType | `$type` |
|----------|---------|
| StartUp | `StartUpTask` |
| Fight | `FightTask` |
| Infrast | `InfrastTask` |
| Recruit | `RecruitTask` |
| Mall | `MallTask` |
| Award | `AwardTask` |
| Roguelike | `RoguelikeTask` |
| Reclamation | `ReclamationTask` |

6.16 模板默认 9 条：StartUp / Fight / Infrast / Recruit / Mall / Award / Roguelike / Reclamation / UserDataUpdate。
MAA 保存后 TaskQueue 会被序列化为 `{"type": "StartUp", "enable": true}`（无 `$type`/`TaskType`/`IsEnable`）——这是 MAA 自己的模型序列化，**无碍**；下次启动 MAA 用自己的模型加载。**但 MAAOrch 注入时必须用 `$type` 格式**（从 source 模板继承）。

---

## 4. 配置映射全表（MAAOrch 字段 → 新旧位置 → 默认值）

### 4.1 连接设置

| MAAOrch 账号字段 | 旧位置（6.16 不读） | 新位置（6.16 生效） | 说明 |
|------------------|---------------------|---------------------|------|
| `adb_address` | `Connect.Address` | `Gui.ConnectSettings.Address` | `127.0.0.1:<port>`；**AutoDetect 必须 false 才生效** |
| `adb_path` | `Connect.AdbPath` | `Gui.ConnectSettings.AdbPath` | `find_adb()` 全盘搜索 / mumu-cli 旁 `adb.exe` |
| `connection_preset` | `Connect.ConnectConfig` | `Gui.ConnectSettings.Config` | `MuMuPro` → `MuMuEmulator12` |
| `touch_mode` | `Connect.TouchMode` | `Gui.ConnectSettings.TouchMode` | `MiniTouch` / `MaaTouch` / `ADB` |
| `emu_instance_index` | `Start.EmulatorPath` / `EmulatorAddCommand` | `Gui.ConnectSettings.Extras.MuMuEmulator12.InstanceIndex` | **6.16 多开定位靠 InstanceIndex**（绕开自动检测错位） |
| —（固定） | `Connect.AutoDetect` | `Gui.ConnectSettings.AutoDetect` | **必须 false**（默认 true） |
| —（固定） | `Connect.AlwaysAutoDetect` | `Gui.ConnectSettings.AlwaysAutoDetect` | false |
| —（固定） | `Connect.AdbReplaced` | `Gui.ConnectSettings.AdbReplaced` | true |
| —（固定） | `Connect.AllowADBRestart` | `Gui.ConnectSettings.AllowAdbRestart` | false（防跨实例干扰） |
| —（固定） | `Connect.AllowADBHardRestart` | `Gui.ConnectSettings.AllowAdbHardRestart` | false |
| —（固定） | `Connect.RetryOnDisconnected` | `Gui.StartUpSettings.RestartEmulatorWhenAdbFailed` | false |

### 4.2 启动设置

| MAAOrch 字段 | 旧位置（6.16 不读） | 新位置（6.16 生效） | 说明 |
|--------------|---------------------|---------------------|------|
| `start_directly` | `Start.RunDirectly` | `Gui.StartUpSettings.RunDirectly` | **true = 启动后直接运行**（LinkStart 自动触发） |
| —（固定） | — | `Gui.StartUpSettings.SkipStartupAutoRunAfterUpdate` | **必须 false**（默认 true → MAA 更新后不自动运行） |
| —（固定） | `Start.OpenEmulatorAfterLaunch` | `Gui.StartUpSettings.StartEmulator` | **必须 false**（模拟器由 MAAOrch 管理；MAA 若尝试启动模拟器，mumu-cli 索引错位会卡住 TryToStartEmulator → 阻塞 RunDirectly 的 LinkStart） |
| `emu_wait` | `Start.EmulatorWaitSeconds` | `Gui.StartUpSettings.EmulatorWaitSeconds` | 默认 60 |
| `emu_instance_index` | `Start.EmulatorPath` / `EmulatorAddCommand` | `Gui.StartUpSettings.EmulatorPath` / `EmulatorAddCommand` | MuMu 12 用 mumu-cli / MuMuManager.exe |
| `start_minimized` | `Global.GUI.MinimizeToTray` | `Global` 区（gui.json，**仍读**） | 6.16 界面设置仍走 gui.json Global |
| `game_client` | `Start.ClientType` | （保留兼容，6.16 建议在 GUI/任务层配置） | 决定启动哪个 APK（Bilibili/Official） |
| `post_action` | `MainFunction.PostActions` | `Gui.PostActions`（字符串枚举） | 默认 `"None"`（不注入） |

### 4.3 任务设置

| MAAOrch 字段 | 注入位置 | 说明 |
|--------------|----------|------|
| `task_list`（调度） | `Configurations.Default.TaskQueue[*].IsEnable` | 模板 9 条，按 `TaskType` 匹配开关 |
| `smart_annihilation` | Fight 条目 `AnnihilationStage` / `UseCustomAnnihilation` | 或插入剿灭克隆条目 |
| `fight_mode` / `stages` / `schedule_weekly` 等 | Fight 条目 `StagePlan` / `IsStageManually` | 关卡选择在 `inject_smart` 内完成（API 层 `Fight(stage)` 格式 MAA 不认） |
| `account_switch` | StartUp 条目 `AccountName` | APP 内账号标识 |
| `sync_tasks` | 各任务参数（Recruit 星级/Infrast 设施/Mall 黑名单等） | 白名单字段级注入 |

### 4.4 其他固定注入

| 键 | 值 | 说明 |
|----|-----|------|
| `Resource.AutoUpdate`（gui.json 顶层） | `false` | 禁 MAA 自更新（防止 OTA 与 `$type` 等待循环冲突） |
| `Global.GUI.MinimizeToTray` / `GUI.UseTray` / `Start.MinimizeDirectly`（gui.json Global） | true | 6.16 仍从 gui.json Global 读 |
| `Global.GUI.Localization` | `zh-cn` | |

---

## 5. inject_smart 注入流程（代码级）

`services/config_injector.py`：

```
inject_smart(task_list, ac, config_dir)                     # line 325
 └─ _write("gui.json", use_v6=False)                        # line 330
 │    ├─ 顶层: Resource.AutoUpdate=false, Global.* (GUI 设置)
 │    ├─ _set_connection(c, ac, use_v6=False)               # line 220  → 扁平键（兼容旧版）
 │    └─ MainFunction.PostActions="12"（兼容旧版，6.16 不读）
 └─ _write("gui.new.json", use_v6=True)
      ├─ 删除 Configurations.Default 顶层含 '.' 的扁平键    # 清旧注入残留
      ├─ _set_connection(c, ac, use_v6=True)                # → 扁平键 + line 269 _set_connection_v6_gui
      │    └─ _set_connection_v6_gui(c, ac)                 # ★ 6.16 嵌套结构注入（见下）
      ├─ Start.* 扁平键（兼容旧版，6.16 不读）
      ├─ TaskQueue 注入：
      │    existing_tq = c["TaskQueue"] 或 source 模板（maa/{ver}/config/gui.new.json）fallback
      │    Fight 去重（保留最后一条）
      │    IsEnable = TaskType in task_set
      │    剿灭：克隆 Fight 条目 / AnnihilationStage / UseCustomAnnihilation
      │    Award 强制置尾
      │    $type 从模板继承保留
      └─ 写回（临时文件 + rename，防半写）
```

### `_set_connection_v6_gui(c, ac)`（line 269）注入清单

```python
Gui.ConnectSettings:
  AutoDetect=false  AlwaysAutoDetect=false  AdbReplaced=true
  AllowAdbRestart=false  AllowAdbHardRestart=false
  EnableAdbLite=false  KillAdbOnExit=false
  Config  = connection_preset 映射（MuMuPro → MuMuEmulator12，默认 MuMuEmulator12）
  TouchMode = touch_mode 映射（默认 MiniTouch）
  Address = ac.adb_address（存在才写）
  AdbPath = ac.adb_path（存在才写）
  Extras.MuMuEmulator12: InstanceIndex=int(emu_instance_index)  IsEnabled=false
                          EnableBridgeConnection=false  EnableTouch=false
                          EmulatorPath=cli（mumu-cli / MuMuManager.exe fallback）

Gui.StartUpSettings:
  RunDirectly=true
  SkipStartupAutoRunAfterUpdate=false      # ★ 每次注入重置（MAA 更新后默认跳过运行）
  StartEmulator=false                      # MAAOrch 管理模拟器
  RestartEmulatorWhenAdbFailed=false
  EmulatorPath=cli（存在才写）
  EmulatorAddCommand=f"control --vmindex {idx} launch"
  EmulatorWaitSeconds=emu_wait 或 60
```

---

## 6. 症状 ↔ 根因 ↔ 修复速查表

| # | 症状 | 根因 | 修复 |
|---|------|------|------|
| 1 | GUI 显示"自动检测"开启、ADB 地址框禁用（填不进去） | 6.16 读 `Gui.ConnectSettings.AutoDetect`（默认 true）；注入的 gui.json `Connect.AutoDetect` 已不读 | `_set_connection_v6_gui` 注入 `AutoDetect=false` |
| 2 | MAA 用 16992 端口连不上（正确是 16708） | `AutoDetect=true` → MAA 自动检测 MuMu12 多开索引错位；或 mumu-cli 单查 `--vmindex N` 返回错位端口 | 关 AutoDetect 注入显式 Address；端口检测改用 `detect_emu_instances`（`--vmindex all`） |
| 3 | MAA 启动后不自动连接（asst.log 停在 set_instance_option，无 AsstConnect） | `Gui.StartUpSettings.RunDirectly` 默认 false（注入位置错） | 注入 `RunDirectly=true` |
| 4 | MAA 更新后首次启动不自动运行 | `SkipStartupAutoRunAfterUpdate=true`（默认） | 每次注入重置 false |
| 5 | `AsstSaveConfig` 保存风暴（每秒 20+ 次，GUI 卡死 → LinkStart 不触发） | 配置结构错位触发 ConfigFactory 反复保存（GUI 线程被占满，`OnUIThread` 的 RunDirectly 块永远排不上队） | 修正注入结构后自然消失 |
| 6 | MAA 解析失败回退旧 ADB 地址（旧模拟器端口残留） | 注入后残留 `.bak`，MAA `ParseConfig` 失败会回退 bak | `config_injector._write` 写后 `unlink(gui.json.bak)` |
| 7 | 连接了但任务全不跑 | TaskQueue 条目缺 `$type` / `TaskType` 匹配失败 | inject_smart 从 source 模板继承 `$type`（勿自行重建条目） |
| 8 | PostActions 数字编码失效 | 6.16 用字符串枚举（`"None"`） | 不再写 `MainFunction.PostActions="12"`；默认不注入 |
| 9 | 启动 MAA.exe 崩溃 E_FAIL | junction（未 resolve）路径作 cwd | `_spawn_instance` 用 `Path(inst_dir).resolve()` |
| 10 | MAA 无 UAC 弹窗但仍以管理员运行 | MAA.exe manifest `asInvoker`（继承父进程权限） | 无（MAAOrch 以管理员运行 → 子进程继承） |
| 11 | 账号列表空（config.json accounts 键消失） | `load_config` v5 分支无 `setdefault("accounts")` → KeyError 静默回退默认 | 已补 `data.setdefault("accounts", [])`；备份仅保留最近 5 份，丢失早无法恢复 |
| 12 | 多 MAA 进程并发共享实例目录（配置文件互踩） | UAC/手动打开/重启流程残留孤儿进程 | 启动前检查进程；`_has_real_process` 判定 + 150s 超时释放 |

---

## 7. 验证与诊断手册

### 7.1 诊断端点

`GET /api/maa/instances/{n}/config`（只读）返回：

| 字段 | 说明 |
|------|------|
| `files.<fn>.connect` | 扁平键（gui.json 仍读的兼容键；gui.new.json 的该字段**恒空**——证明嵌套结构） |
| `files.<fn>.gui_connect` | **6.16 实际读取**的 `Gui.ConnectSettings`（核对 AutoDetect/Address/Extras.InstanceIndex） |
| `files.<fn>.gui_startup` | `Gui.StartUpSettings`（核对 RunDirectly/SkipStartupAutoRunAfterUpdate/StartEmulator） |
| `files.<fn>.gui_postactions` | `Gui.PostActions` |
| `files.<fn>.task_queue` | TaskQueue 的 TaskType/IsEnable 摘要 |
| `files.<fn>.global` | gui.json Global 区 GUI.* / Start.* 键 |
| `dir_files` | config 目录全部文件（含 .bak — 检查是否残留） |

### 7.2 asst.log（实例 `debug/asst.log`，每次启动清空）

| 日志行 | 含义 |
|--------|------|
| `asst::Assistant::set_instance_option | key 2/3/4` | AsstProxy 初始化完成（**若之后 2 分钟无活动 = RunDirectly 未触发**） |
| `AsstConnect ... address: 127.0.0.1:16708`（gui.log） | **连接地址**（核对 16708 而非 16992） |
| `Start Task Chain: StartUp, Task ID: 1`（gui.log） | 任务开始 |
| `StartUp@GameStart` / `StartUp@StartToWakeUp@LoadingIcon` | StartUp 阶段推进（等游戏启动） |

### 7.3 gui.log（实例 `debug/gui.log`）

| 日志行 | 含义 |
|--------|------|
| `LinkStartWithTasks` → `正在连接模拟器……` | **RunDirectly 自动触发 LinkStart**（关键成功标志） |
| `AsstSaveConfig ret: true` 每秒 20+ 次 | 保存风暴（结构错位信号） |
| `LinkStart Exit, 2619 ms` | 连接完成 |
| `Bootstrapper ... Run as Administrator` | MAA 以管理员运行（继承 MAAOrch） |
| `ConfigurationHelper ... HotKeys has been set` | gui.json Global 区读写正常 |

### 7.4 连接页 vs 调度台

两条路径**共享同一启动链路**：`lq.enqueue → _tick → runner._launch_job → _launch_for_instance → inject_smart → _spawn_instance`。
区别仅在账号对象（连接页临时账号 `connect_accounts`，带 `_connect_only` 标记；调度用正式账号）与任务集。**修复注入后两条路径同时生效**。

---

## 8. 数据流

```
MAAOrch 启动 MAA（Popen，cwd=实例目录 resolve 后）
    │
    ├─ inject_smart() 写入实例 config/
    │    ├─ gui.json      → Global 区（界面设置）+ 扁平键（兼容，6.16 不读）
    │    └─ gui.new.json  → Gui.ConnectSettings（AutoDetect=false / Address=16708 / InstanceIndex=42）
    │                      → Gui.StartUpSettings（RunDirectly=true / SkipStartupAutoRunAfterUpdate=false）
    │                      → TaskQueue（$type 条目 + IsEnable）
    │
MAA.exe 启动（cwd=实例目录 → 读 config/）
    ├─ ConfigurationHelper.Load(gui.json)   → Global 区生效（GUI 设置/热键）
    ├─ ConfigFactory.Load(gui.new.json)     → Gui.* 生效 + TaskQueue 加载
    ├─ AsstProxy.Init → set_instance_option → OnUIThread:
    │    └─ RunDirectly=true → TryToStartEmulator（StartEmulator=false 直接跳过）
    │                           → LinkStart() → AsstConnect(adb, 127.0.0.1:16708, MuMuEmulator12)
    └─ TaskQueue 执行：StartUp → Infrast → Recruit → Mall → Award（Award 置尾）
```

---

## 9. 未来版本适配检查清单（6.17+）

MAA 每次大版本可能继续迁移配置键。升级后按此检查：

1. **看 ConfigurationKeys.cs 的 √ 标记** — 新增 √ = 该键迁移到 `ConfigFactory.CurrentConfig.Gui.*`，MAAOrch 注入位置要跟着搬
2. **用诊断端点对比** `gui_connect`/`gui_startup` 与扁平键 — 若扁平键又生效/嵌套失效，说明读取机制回退或再变
3. **启动后看 gui.log** — `LinkStartWithTasks` 出现 = RunDirectly 链路 OK；`AsstSaveConfig` 刷屏 = 结构错位
4. **核对 source 模板** `services/maa/source/config/gui.new.json` — 新版本的默认结构就是注入模板
5. **PostActions 枚举** — 确认还是字符串枚举（"None"/"ExitSelf"/...）还是数字编码回归
