# MAA 任务配置与注入

## ConfigService 概述

`ConfigService`（`config_ops.py`）负责将 MAAOrch 中配置的账号连接信息和任务参数写入 MAA 的配置文件中。支持两种写入模式：

| 模式 | 目标文件 | 用途 |
|------|----------|------|
| `inject()` | `gui.json` + `gui.new.json` | MAA GUI 模式配置 |
| `gtc()` | `daily.toml` + `default.toml` | maa-cli 命令行模式配置 |

## gui.json 注入 (`inject()`)

同时写入 `{MAA目录}/config/gui.json` 和 `gui.new.json`，确保兼容各版本 MAA。

### 连接配置

从账号数据映射到 MAA 配置项：

| 账号字段 | MAA 配置项 | 说明 |
|----------|------------|------|
| `adb_address` | `Connect.Address` | ADB 地址 |
| `adb_path` | `Connect.AdbPath` | ADB 可执行文件路径 |
| `connection_preset` | `Connect.ConnectConfig` | MuMuPro → `MuMuEmulator12` |
| `touch_mode` | `Connect.TouchMode` | MiniTouch → `minitouch`, MaaTouch → `maatouch`, ADB → `adb` |
| `game_client` | `Start.ClientType` | 客户端区服 |
| - | `Connect.AdbReplaced` | 固定 `"True"` |
| - | `Connect.AutoDetect` | 固定 `"False"` |
| - | `Connect.AlwaysAutoDetect` | 固定 `"False"` |

### 启动配置

| 账号字段 | MAA 配置项 |
|----------|------------|
| `start_minimized` | `Global.GUI.MinimizeToTray` |
| `start_directly` | `Start.RunDirectly` |
| `post_action` | `MainFunction.PostActions` |
| `adb_retry` > 0 | `Connect.RetryOnDisconnected` |
| `account_switch` | `Start.StartGame` + 任务队列中的 `AccountName` |

### 模拟器自动启动

若 `emu_instance_index` 非空且 `emu_launch` 为 false（由 MAA 管理启动），则注入：

```
Start.EmulatorPath = mumu-cli 路径
Start.EmulatorAddCommand = control --vmindex {index} launch
Start.OpenEmulatorAfterLaunch = True
Start.EmulatorWaitSeconds = {emu_wait}
```

### 任务队列同步

当 `sync_tasks` 为 `True` 时，遍历 `gui.json` 中的 `TaskQueue` 数组：

1. 任务在 `task_pipeline` 列表中 → `IsEnable = True`，填入详细参数
2. 任务不在列表中 → `IsEnable = False`（禁用）

#### 各任务参数映射

**刷关作战 (Fight)**：
| 参数 | MAA 配置 |
|------|----------|
| `stage` | `StagePlan` |
| `medicine` | `UseMedicine` + `MedicineCount` |

**公开招募 (Recruit)**：
| 参数 | MAA 配置 |
|------|----------|
| `select` | `Level3Choose` / `Level4Choose` / `Level5Choose` |
| `confirm` | `Confirm` |
| `times` | `MaxTimes` |

**基建换班 (Infrast)**：
| 参数 | MAA 配置 |
|------|----------|
| `facilities` | `RoomList` |
| `drones` | `UsesOfDrones` |

**信用商店 (Mall)**：
| 参数 | MAA 配置 |
|------|----------|
| `shopping` | `Shopping` |
| `blacklist` | `BlackList` |

**领取奖励 (Award)**：
| 参数 | MAA 配置 |
|------|----------|
| `award` | `Award` |
| `mail` | `Mail` |

**肉鸽探索 (Roguelike)**：
| 参数 | MAA 配置 |
|------|----------|
| `theme` | `Theme` |
| `mode` (0/1) | `Mode` (Exp/Investment) |

**生息演算 (Reclamation)**：
| 参数 | MAA 配置 |
|------|----------|
| `theme` | `Theme` |

## maa-cli TOML 生成 (`gtc()`)

为 CLI 模式生成 TOML 配置文件：

### daily.toml（任务配置）

根据 `task_pipeline` 列表生成 `[[tasks]]` 段：

```toml
[[tasks]]
type="Fight"
[tasks.params]
stage="1-7"
```

支持的任务类型：`StartUp`、`Fight`、`Recruit`、`Infrast`、`Mall`、`Award`、`Roguelike`、`Reclamation`、`CloseDown`。

生成路径：`{MAA目录}/config/tasks/daily.toml`

### default.toml（连接配置）

```toml
[connection]
address="127.0.0.1:16384"
adb_path="C:\\platform-tools\\adb.exe"
preset="MuMuPro"

[instance_options]
touch_mode="ADB"
```

生成路径：`{MAA目录}/config/profiles/default.toml`

## 任务常量

`task_constants.py` 中定义：

```python
TASK_NAMES = {
    "StartUp": "开始唤醒", "Fight": "刷关作战", "Recruit": "公开招募",
    "Infrast": "基建换班", "Mall": "信用商店", "Award": "领取奖励",
    "Roguelike": "肉鸽探索", "Reclamation": "生息演算", "CloseDown": "关闭游戏"
}

TASK_DEFAULTS = {
    "Fight": {"stage": "1-7", "medicine": 0},
    "Recruit": {"select": [3,4,5], "confirm": [3,4,5], "times": 4},
    "Infrast": {"facilities": ["Trade","Reception","Mfg","Control","Power","Office","Dorm"], "drones": "Money"},
    "Mall": {"shopping": True, "blacklist": []},
    "Award": {"award": True, "mail": True},
    "Roguelike": {"theme": "Sarkaz", "mode": 0},
    "Reclamation": {"theme": "Tales"}
}
```

## 参数模板

支持将当前任务参数保存为命名模板，后续可加载套用。模板功能通过 `TaskSettingsDialog`（`dialogs.py`）实现，数据存储在账号的 `task_settings` 字段中。
