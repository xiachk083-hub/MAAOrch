# 多账号与模拟器管理

## 账号数据结构

每个账号在 `config.json` 中存储为 `accounts[]` 数组的一个元素：

```json
{
  "id": "a1b2c3d4",
  "name": "官服大号",
  "game_client": "Official",
  "adb_path": "C:\\platform-tools\\adb.exe",
  "adb_address": "127.0.0.1:16384",
  "connection_preset": "MuMuPro",
  "touch_mode": "ADB",
  "account_switch": "",
  "emu_path": "C:\\MuMu Player 12\\shell\\MuMuPlayer.exe",
  "emu_launch": true,
  "emu_wait": 30,
  "emu_add_cmd": "",
  "emu_instance_index": "0",
  "emu_instance_name": "MuMu 模拟器",
  "post_action": "",
  "start_minimized": false,
  "start_directly": false,
  "adb_fail_launch_emu": false,
  "adb_retry": 0,
  "task_settings": {},
  "sync_tasks": false,
  "stats": {}
}
```

## 区服支持

`game_client` 字段支持以下值，对应明日方舟各客户端：

| 值 | 客户端 |
|----|--------|
| `Official` | 官服 |
| `Bilibili` | B 服 |
| `YoStarEN` | 国际服 |
| `YoStarJP` | 日服 |
| `YoStarKR` | 韩服 |
| `Txwy` | 繁中服 |

## 连接配置

### 连接预设

`connection_preset` 决定 MAA 的连接模式，可选项：

| 预设 | 说明 |
|------|------|
| `General` | 通用模式 |
| `MuMuPro` | MuMu 模拟器 12 |
| `BlueStack` | 蓝叠模拟器 |
| `Nox` | 夜神模拟器 |
| `Xiaoyao` | 逍遥模拟器 |
| `Ledi` | 雷电模拟器 |

### 触控模式

`touch_mode` 控制 MAA 的触控方式：

| 值 | 对应 MAA 配置 |
|----|---------------|
| `ADB` | `adb`（默认） |
| `MiniTouch` | `minitouch` |
| `MaaTouch` | `maatouch` |

## ADB 工具

### 扫描设备

`EmuService.scan()` 执行流程：

1. 调用 `adb devices` 获取已连接设备列表
2. 解析输出，过滤 `device` / `unauthorized` / `offline` 状态
3. 若无在线设备，遍历所有模拟器预设端口，执行 `adb connect` 探测后重新扫描

### 测试连接

`EmuService.test_adb()` 对指定地址执行 `adb connect`，根据输出判断连接状态。

### 截图

`EmuService.screenshot()` 通过 `adb exec-out screencap -p` 获取设备屏幕截图，保存到 `screenshots/` 目录，文件名格式 `MAA_YYYYMMDD_HHMMSS.png`。

## 模拟器多实例

### 支持的模拟器

通过 `task_constants.py` 中的 `EMU_PRESETS` 定义：

| 模拟器 | 关键端口 |
|--------|----------|
| MuMu 12 | 16384 + index × 32 |
| MuMu 6 | 7555 + index × 32 |
| 雷电 | 5555, 5556, 5557... |
| 夜神 | 62001, 62025... |
| 逍遥 | 21503, 21513... |
| 蓝叠 | 5555 |

### mumu-cli 集成

MuMu 模拟器通过 `mumu-cli` 命令行工具管理，搜索路径优先级：

1. 环境变量 `MUMU_CLI_HOME`
2. `C:\Program Files\MuMu Player 12\shell\`
3. `C:\Program Files\Nemu\vmonitor\bin\`
4. `C:\Program Files\Muvm6\emulator\nemu\EmulatorShell\`

### 实例检测

`detect_emu_instances()` 函数（`task_constants.py`）执行：

1. 调用 `mumu-cli info --vmindex all` 获取实例列表
2. 解析每个实例的 `name`、`adb_port`、`running` 状态
3. 若 mumu-cli 不可用，回退到读取 `MUMU_INSTANCE_DIRS` 中各实例目录的 `config.json`

### 实例状态监控

`EmuMonitor`（`task_constants.py`）是一个持续运行的后台线程，每 30 秒通过 `mumu-cli info --vmindex all` 轮询所有 MuMu 实例的运行状态，更新 `emu_status` 字典供 UI 显示。

### 扫端口流程

`EmuService.scan_port()` 三步操作：

1. **启动**：`mumu-cli control --vmindex {index} launch`
2. **等待**：休眠 5 秒等待开机
3. **获取端口**：先读实例目录 `config.json` 的 `adb_port` 字段 → 失败则调 `detect_emu_instances()` → 最终回退公式 `16384 + index × 32`
4. **连接**：`adb connect 127.0.0.1:{port}`

### 关闭模拟器

`EmuService.stop_emu()` 调用 `mumu-cli control --vmindex {index} shutdown`。

## 配置导入/导出

支持通过菜单导出当前全局配置或单个账号配置为 JSON 文件，以及从文件导入合并配置。导出时会自动去除敏感路径信息。
