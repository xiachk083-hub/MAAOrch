# maa-cli 勘察报告（能力面 / 配置 schema / core 与资源复用 / 设备与账号）

> v1 · 2026-09-27 · 产出任务 `t_8057ebaa`（只读 + 本机无害探测；**未发车、未在生产主机 install、未改任何生产配置**）
> 版本口径：本机 `maa-cli 0.7.5`（WinGet shim `%LOCALAPPDATA%\Microsoft\WinGet\Links\maa-cli.exe`）；文档/源码/JSON Schema 均按上游 **tag v0.7.5** 拉取核对（GitHub API，raw 被墙）
> 端到端实测：`--help` / `dir` / `version` / `list` / `init` / `convert` / `import` / `activity` / `--dry-run`，全部跑在 `%TEMP%\maaprobe*` 沙盒配置目录（`MAA_CONFIG_DIR`）里
> 一句话结论：**maa-cli = 无头 MaaCore 驱动器**——参数面完整、可直接复用主机已有 MAA 资源（目录级链接）、退出码可编程；但它**没有 gui.log 那条判据链**，排障要改读 `asst.log` + 退出码/Summary；**远端模拟器必须"在主机上跑"或走 SSH 隧道（本机直连已被实测否决）**。

---

## 0. TL;DR（给拍板用）

| 问题 | 结论（证据见对应节） |
|---|---|
| 能否成为 Hermes 调配主路径 | **能，但要分场景**：① 主机侧部署（junction 复用 E:\MAA-Multi 资源）＝推荐；② 本机跑远端模拟器＝需隧道且截图通道退化（§5）；③ 本机跑本机模拟器＝现状即可用 |
| 配置体系 | `MAA_CONFIG_DIR`（默认 `%APPDATA%\loong\maa\config`）下：`cli.toml` + `profiles/<名>.{toml,yaml,json}` + `tasks/<名>.{toml,yaml,json}`；三种格式可混用（§3） |
| 资源复用 | **已实证**：本机 `data\lib` 与 `data\resource` 两个**符号链接**指向一个完整 MAA 目录，`maa version` 正常报 `MaaCore v6.18.0`、任务可连（§4） |
| 远端模拟器 | 本机直连主机 adb 口 **实测 ConnectionRefused**（SSH 22 通）⇒ 必须隧道/本机执行；隧道有 `10.0.2.2` 回连坑（§5.2） |
| 账号 | `startup --account-name`（别名 `--account`）→ MaaCore `StartUp.account_name`，与 GUI `AccountName` **同参数同层**；GUI 的 `AccountSwitchEnabled` 在 maa-cli **无对应项**（§6） |
| 判据对接 | `asst.log` 位置/格式与 GUI 完全一致（同一 MaaCore）⇒ 本仓字典 asst 段**全部适用**；`gui.log` 专属判据（`账号切换 -->>`、`任务已全部完成！` 等）**不可用**，替代＝退出码（0/1）+ stdout Summary（§7） |
| 主机侧现状 | 主机**已装 maa-cli v0.5.9**（`%LOCALAPPDATA%\WinGet\Links\maa.exe`），但**未装 core/资源**（`Failed to load MaaCore!` os error 126）——用前需对齐版本 + 复用资源（§4.3） |

---

## 1. 环境与既有安装（实测）

**本机（DESKTOP-L1OEMR3）**

- `maa-cli v0.7.5`；`maa version` → `maa-cli v0.7.5` + `MaaCore v6.18.0`（core 版本是**加载 dll 后读出来**的，说明资源/库已就位）
- 已有实验位（只读，未改动）：`%APPDATA%\loong\maa\config\profiles\default.toml`（`global_resource="YoStarJP"`、`address="127.0.0.1:16384"`、`adb_path=D:/Xiach/MuMuPlayer/nx_main/adb.exe`、`touch_mode="MaaTouch"`）、`config\tasks\spa-nav.toml`（`Custom` → `MiniGame@SPA@Begin`）
- 2026-09-22 08:28 有一次**完整成功连接**的 asst.log 记录（连接 + MaaTouch 推送 + 截图测速，见 §7.1 引用）
- ⚠️ 目录里另有 `profiles\t9test.toml` / `t9nores.toml`（近 20 分钟内由**其他会话**写入，非本次任务产物）——不要误删

**主机（MRFZ-0000 / 100.79.173.69，经 `ssh -i ~/.ssh/maaorch_target Administrator@…` 只读核查）**

- `where maa` → `C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\maa.exe`（**v0.5.9**，比本机旧）
- `maa dir library` / `dir resource` → `Error: Library not found` / `Resource not found`；`maa version` → `Failed to load MaaCore! (os error 126)`
- ⇒ 主机上**只有 CLI 外壳，没有 core/资源**，`%APPDATA%\loong\maa\` 下 data/config 目录都还不存在

---

## 2. 命令能力表（0.7.5，逐条实测）

全局：`--batch`（跳过交互提示、取默认值；**无默认值的输入会报错**）、`-v/-q`（增减日志级）、`--log-file[=<PATH>]`、`-V/--version`。

| 子命令 | 能力 | 关键参数（实测 help） | 备注 |
|---|---|---|---|
| `install [CHANNEL]` | 下载安装 MaaCore+资源 | `stable/beta/alpha`（**alpha 仅 Windows**）、`--no-resource`、`-t/--test-time`（镜像测速秒）、`--api-url`（或 `MAA_API_URL`）、`-f/--force` | 装到 `data\lib` + `data\resource`（§4.1） |
| `update` | 更新 core+资源 | 同上（无 channel 位置参数） | 与 install 同一流水线 |
| `hot-update` | 拉「热更新资源」git 仓 + 活动文件 | 无参（配置在 `cli.toml`） | **每次跑任务都会自动执行**（实测输出 `Hot update completed successfully`） |
| `dir <DIR>` | 打印目录 | 取值：`data / library / config / cache / resource / hot-update / log` | 无参报错；全表见 §3.1 |
| `version [COMPONENT]` | 版本 | `all / maa-cli / maa-core`（默认 all） | `maa-core` 会真的加载 dll |
| `run <TASK>` | 跑自定义任务文件 | `-a/--addr`、`-p/--profile`、`--user-resource`、`--dry-run`、`--no-summary`、`--no-auto-reconnect` | 任务名＝`tasks\` 下的文件名（无扩展名） |
| `startup [CLIENT]` | 启动游戏到主界面 | `--account-name`（别名 `--account`）、`[ClientType]`＝Official/Bilibili/Txwy/YoStarEN/JP/KR | 给 client 才会带 `start_game_enabled=true` |
| `closedown [CLIENT]` | 关闭游戏客户端 | `[CLIENT]` 默认 Official | |
| `fight [STAGE]` | 刷关 | `-m/--medicine`、`--expiring-medicine`、`--stone`、`--times`、`-D/--drops`（掉落 ID=数量 退出）、`--series`（-1~6 连战）、`--report-to-penguin/yituliu`、`--dr-grandet` | 留空＝当前/上次关卡 |
| `roguelike <THEME>` | 集成战略 | THEME＝Phantom/Mizuki/Sami/Sarkaz/JieGarden；`--mode 0~5`、`-s/--squad`、`--core-char`、`--roles`、`--starts-count`、`--difficulty`、`--disable-investment`、`--investments-count`、`--use-support`、`--start-with-elite-two`、`--stop-at-final-boss`、`--use-foldartal`、`-F/--start-foldartals`、`-P/--expected-collapsal-paradigms`、`--refresh-trader-with-dice`、`--start-with-seed` | 肉鸽机最相关；参数面 = MaaCore Roguelike 任务的完整映射 |
| `reclamation <THEME>` | 生息演算 | 仅 `Tales`；`-m/--mode 0|1`、`-C/--tools-to-craft`（默认荧光棒）、`--increase-mode`、`--num-craft-batches` | |
| `copilot [URI…]` | 自动抄作业 | `maa://<code>` / `maa://<code>s`（合集）/ 本地文件；`--raid normal|raid|both`、`--formation`、`--formation-index 1-4`、`--add-trust`、`--ignore-requirements`、`--use-sanity-potion`、`--support-unit-usage` | |
| `ssscopilot <URI>` | 保全派驻 | `--loop-times`（默认 1） | |
| `paradoxcopilot [URI…]` | 悖论模拟 | 同 copilot 的 URI 形式 | |
| `convert <IN> [OUT] [-f fmt]` | TOML/YAML/JSON 互转 | 无 OUT 且无 `-f` → `Error: Format not given`（实测） | §8 |
| `activity [CLIENT]` | 显示当期活动 | 默认 Official；实测输出 `Opening mini games: - 黑流树海刷钱` | 走热更新缓存/API |
| `remainder <DIVISOR>` | 天偏移量（给 `DayMod` 用） | `--timezone` | 实测 `remainder 7` → `0` |
| `cleanup [TARGETS…]` | 清缓存 | `cli-cache / core-cache / debug / log` | **会删日志**，巡检机别乱跑 |
| `list` | 列出可用自定义任务 | 无参 | 读 `config\tasks\`，只打文件名（实测 `probe-daily`、`imported-daily`） |
| `import <SRC> [-t 类型]` | 导入配置（本地/远程 URL） | `-t`：`cli/profile/task/infrast/resource/copilot/sss-copilot/paradox-copilot`（默认 task）、`-n` 名字（**实测需带扩展名**）、`-f` 覆盖 | §8 |
| `init [-n 名] [-f fmt] [--force]` | 交互式生成 profile | fmt：json（默认）/yaml/toml | 实测会把提示吃掉取默认值（非 tty）；它**只生成 profile**，不生成 cli.toml |
| `complete <SHELL>` | 补全脚本 | bash/elvish/fish/powershell/zsh | 实测输出到 stdout（`Register-ArgumentCompleter … 'maa'`） |
| `mangen` | 生成 man page | 路径 | Linux/WSL 向 |
| `self update` | 自更新 | 包管理器装的用户无效 | WinGet 装的用它自带升级 |

> `init` 交互项（实测提示序列）：连接预设（`MuMuPro / PlayCover / Waydroid / Androws / ADB[默认]`）→ adb 路径 → address → config 名 → 是否设 instance options（触摸模式：`ADB / MiniTouch / MaaTouch[默认] / MacPlayTools / MaaFwAdb`）→ `deployment_with_pause` → `adb_lite_enabled` → `kill_adb_on_exit` → 是否设资源配置 → 是否设 static options。

---

## 3. 配置 schema

### 3.1 目录与环境变量（`maa dir` 实测 + maa-dirs 源码）

| dir | 本机实测值 | 说明 |
|---|---|---|
| `data` | `%APPDATA%\loong\maa\data` | 数据根 |
| `library` | `…\data\lib` | MaaCore 动态库目录（`MaaCore.dll` 等）→ **可链接复用** |
| `resource` | `…\data\resource` | MaaCore 资源目录 → **可链接复用** |
| `hot-update` | `…\data\MaaResource` | 热更新 git 仓（`MaaResource` 的 clone 位置） |
| `config` | `%APPDATA%\loong\maa\config` | `cli.toml` + `profiles\` + `tasks\` |
| `cache` | `%LOCALAPPDATA%\loong\maa\cache` | `StageActivityV2.json`、`core-manifest-*.json`、`resource\`(热更新缓存)、`copilot\` |
| `log` | `…\data\debug` | **= MaaCore 的 `<User Dir>\debug`**，`asst.log` 就在这里 |

环境变量（源码级）：`MAA_CONFIG_DIR` / `MAA_DATA_DIR` / `MAA_STATE_DIR` / `MAA_CACHE_DIR`（Windows 上 state 默认=data）；日志相关 `MAA_LOG`（级别，默认 Warn）、`MAA_LOG_PREFIX`（Always/Auto/Never）；安装相关 `MAA_API_URL`。
⚠️ `MAA_CONFIG_DIR` 值必须是 **Windows 原生路径**（用 MSYS `/tmp/...` 会被当成别处，实测踩过）。`MAA_STATE_DIR` 一改，MaaCore 的 `asst.log` 也跟着搬——**多任务并发时这正好是隔离日志的抓手**。

### 3.2 profile（传给 MaaCore 的配置；`profiles\<名>.{toml,yaml,json}`）

全字段（upstream asst.schema.json + 0.7.5 源码核对）：

```toml
[connection]
preset = "ADB"          # MuMuPro / PlayCover / Waydroid / Androws / ADB(默认)；旧写法 type="ADB" 仍认
adb_path = "adb"        # 默认值随 preset：MuMuPro=Mac 路径、Androws=自动探测、其余 "adb"
address = "127.0.0.1:16384"   # 旧名 device= 也认；留空按 preset 默认
config = "General"      # 连接配置名；Windows 默认 General，macOS CompatMac，Linux CompatPOSIXShell

[resource]
global_resource = "YoStarJP"      # txwy / YoStarEN / YoStarJP / YoStarKR（留空=简中）
platform_diff_resource = "iOS"    # 一般不用写（PlayTools 自动）
user_resource = false             # true=再叠加 config\resource\ 覆盖

[static_options]
cpu_ocr = false
gpu_ocr = 1

[instance_options]
touch_mode = "MaaTouch"          # ADB / MiniTouch / MaaTouch / MacPlayTools / MaaFwAdb
deployment_with_pause = false
adb_lite_enabled = false
kill_adb_on_exit = false

[behavior]
auto_reconnect = true            # 掉线自动重连（可被 --no-auto-reconnect 关）
```

要点：`preset` 的默认地址：**MuMuPro→127.0.0.1:16384**、PlayCover→127.0.0.1:1717、Androws→127.0.0.1:5555、Waydroid→`waydroid`、**ADB→跑一次 `adb devices` 取首个 online，取不到才退回 `emulator-5554`**。`--addr/-a` 命令行覆盖 profile 的 address。`-p/--profile` 指定名；缺省找 `profiles\default.*`，再退 `asst.toml`（会给弃用 WARN）。**profile 里没有账号字段**（见 §6）。

### 3.3 自定义任务（`tasks\<名>.toml|yaml|json`）

顶层：`client_type`、`startup`(bool)、`closedown`(bool)、`tasks[]`；`startup=true` 会在链首自动插 `StartUp`、`closedown=true` 自动补 `CloseDown`（源码 `TaskConfigTemplate::init` 实测语义）。单任务：`name? / type / params / variants[] / strategy = "first"(默认)|"merge"`。

```toml
client_type = "Official"

[[tasks]]
type = "StartUp"                        # 16 种 type 枚举见 task.schema.json：StartUp/CloseDown/Fight/Recruit/
params = { client_type = "Official", start_game_enabled = true }   # Infrast/Mall/Award/Roguelike/Copilot/SSSCopilot/
                                        # Depot/OperBox/ReclamationAlgorithm/Custom/SingleStep/VideoRecognition
[[tasks]]
name = "Fight 1-7"
type = "Fight"
strategy = "merge"
params = { stage = "1-7", medicine = 0, times = 1, series = 1 }

[[tasks.variants]]                      # 条件变体：first=取第一个命中；merge=命中项参数合并(后覆盖前)
condition = { type = "Weekday", weekdays = ["Tue","Thu","Sat"] }
params = { stage = "CE-6" }             # 条件族：Always/Time/DateTime/Weekday/DayMod(divisor,remainder)
                                        # /OnSideStory(按热更新活动自动命中!)/And/Or/Not
[[tasks]]
type = "CloseDown"
```

- `OnSideStory`：**活动期自动刷当期关**，不用手写活动起止——对"活动刷取"场景价值最高。
- 输入项（`Input`/`Select`）：`alternatives=[…]`、`default_index`（1 起）、`default`、`allow_custom`、`conditions={同级参数=值}`；`--batch` 全跳过取默认（无默认值会报错）。
- 相对路径：基建计划相对 `config\infrast\`、保全作业相对 `config\ssscopilot\`（**这些文件必须是 JSON**，MaaCore 读）。
- 实测坑：**params 名/值不校验**——写错只有 MaaCore 运行时才知道（上游文档原文）。
- 预设子命令（`startup/fight/roguelike/...`）本质是"临时造一个单任务 task 文件"，其默认参数可由 `config\overlays\<tasktype 小写>` 覆盖（源码 `default_file()`），可做"本机默认值"层。

### 3.4 cli.toml（CLI 自身配置，`config\cli.toml`）

```toml
[core]
channel = "Stable"        # Stable/Beta/Alpha(仅 Win)
test_time = 0             # 镜像测速秒；0=跳过
api_url = "https://…"      # 留空用默认（可配镜像）

[core.components]
library = true            # 不装库/资源分开装会版本错配，官方不推荐

[cli]
channel = "Stable"

[resource]
auto_update = true        # 每次跑任务自动热更新资源（默认 false！）
warn_on_update_failure = true
backend = "libgit2"       # git / libgit2（git 后端要求 git 在 PATH；主机有 git ✓）

[resource.remote]
branch = "main"
url = "https://github.com/MaaAssistantArknights/MaaResource.git"   # 首次装资源用；改地址要手动改仓

[hot_update]
api_url = "…"             # 活动/热更资源 API（可配镜像）
check_interval = 3600     # 秒；0=每次都下
```

### 3.5 最小可用样例（本报告实测通过 `--dry-run`）

`profiles\default.toml`（沙盒实测）：

```toml
[connection]
adb_path = "C:/platform-tools-latest-windows/platform-tools/adb.exe"
address = "127.0.0.1:16384"
config = "General"
[instance_options]
touch_mode = "MaaTouch"
```

`tasks\probe-daily.toml`：见 §3.3 首个样例（`client_type` + StartUp + Fight + Mall + CloseDown）。
实测：`MAA_CONFIG_DIR=<沙盒> maa --batch run probe-daily --dry-run` → 打印 `Summary`（各子任务 `Unstarted`）、**exit 0**（dry-run 不连设备）。

---

## 4. core 与资源策略

### 4.1 `install/update` 做什么（源码 `installer/maa_core.rs`）

- 下载 asset：`MAA-v<版本>-win-x64.zip`（macOS/Linux 另名），解包时按 `extract_mapper` 分流：**zip 里 `resource/` 子树 → `data\resource`，其余（`MaaCore.dll` + 依赖 dll）→ `data\lib`**。
- 镜像测速（`-t`）、版本 API（`--api-url`/`MAA_API_URL`）、channel 决定 manifest（`cache\core-manifest-<channel>.json`）。
- 已装则跳过；`-f` 强制重装（**注意会写穿链接，见 4.3 风险**）。
- `hot-update`（每次跑任务自动跑）拉的是「热更资源仓」+ `StageActivityV2.json` 等活动文件，进 `data\MaaResource` / `cache\`，**不覆盖基础资源**；git 后端要求 PATH 里有 git（主机 ✓）。

### 4.2 复用已有 MAA —— 本机已实证 ✅

本机现状（只读观察）：`data\lib` 与 `data\resource` 都是**符号链接**：

```
data\lib      -> D:\Xiach\M-A\MAA\MAA-v6.12.2-win-x64          （含 MaaCore.dll 全套依赖）
data\resource -> D:\Xiach\M-A\MAA\MAA-v6.12.2-win-x64\resource（含 tasks/ global/ onnx/ …）
```

结果：`maa version` 报 `MaaCore v6.18.0`；2026-09-22 08:28 的 asst.log 显示**完整成功连接**（`adb -s 127.0.0.1:16384` 全流程 + MaaTouch 推送 + 截图测速 RawByNc 225ms）。目录名 `v6.12.2` 是历史壳，内容是新版（dll 构建 `Sep 23 2026 21:04:42`）——**版本以 dll 为准，别信目录名**。

机制（maa-dirs 源码）：`library` 先看 `data\lib\MaaCore.dll` 在不在、`resource` 先看 `data\resource\` 在不在——**存在即用**。所以"复用"= 在这两个位置放链接/目录（Windows 下 `mklink /J` junction 不需要开发者模式；symlink 需要）。

### 4.3 主机侧复用方案（E:\MAA-Multi）

主机实测：`E:\MAA-Multi\` 下有 `MAA_1…MAA_14/MAA_25/MAA_28` 等**完整 MAA 目录**（每份都带 `MaaCore.dll` + `resource\`，`resource\version.json` 与本机同快照 `2026-09-20 12:47:33`），根目录另有一份全量 `MaaCore.dll`+`resource`。

建议配方（**未执行，仅是勘察结论**）：

```
mklink /J %APPDATA%\loong\maa\data\lib      E:\MAA-Multi\MAA_6
mklink /J %APPDATA%\loong\maa\data\resource E:\MAA-Multi\MAA_6\resource
```

- 版本对齐（**实测证据**）：主机 `E:\MAA-Multi\MAA_6\MaaCore.dll` = **5,902,336 B / 2026-09-23 21:17**，与本机复用对象**逐字节同款**（同 v6.18.0 构建）；⚠️ 主机根目录另有一份 `E:\MAA-Multi\MaaCore.dll` 是**旧版**（4,264,448 B / 2026-08-18），别拿它链接。主机 CLI 却是 **0.5.9**——先升级 CLI（WinGet 升级或重装 0.7.5），否则参数/行为可能与本报告有出入（未逐项 diff 0.5.9）。
- 分工注意：`E:\MAA-Multi\MAA_6` 是 AUTO-MAS 在用的生产目录（官-1-1-7 + 官-38 共用），**链接复用＝共享其 dll+资源**。若将来 GUI 侧自更新，maa-cli 跟着变版本——需接受；反之 maa-cli `install/update -f` 会**写进该目录**（MaaCore.dll/资源被换掉 → 可能影响 GUI 侧运行）⇒ 这类机器上**禁用 `install/update --force`**，升级只走 GUI 自身或拆链接后再动。
- 更稳的替代：复制一份 `MAA_6` 到独立目录（如 `E:\MAA-CLI-CORE`）再链接，彻底隔离生产目录（占磁盘换安全）。

### 4.4 自动更新与热更新

- `cli.toml [resource] auto_update` 默认 **false**，但 `run/startup/fight/...` 每次都会跑 `hot_update::update()` + `resource::update(true)`；实测输出 `Updating hot update files... Hot update completed successfully`（本机网络可达该 API；`raw.githubusercontent.com` 在本机被 reset，但默认 API 端点不受影响——**api_url 可换镜像**）。
- 无 `data\MaaResource\resource` 时每次都会 WARN：`Resource directory …\MaaResource\resource not found, ignoring` ——**属噪声**，不用管。

---

## 5. 设备 / 实例指定

### 5.1 address 语义（源码 + 实测）

- `address` 就是 **adb serial**：`emulator-5554` 或 `127.0.0.1:16384` 都行；`--addr` 覆盖 profile；`--user-resource` 同理。
- MaaCore 侧行为（asst.log 实测 + MaaCore.dll 字符串）：先 `adb devices`，命中即用；未命中且**像序列号**（无 `:`）→ 报 `Cannot connect: address appears to be serial number but device not found`（本地 June 实测原文）；DLL 内含 `^.+ connect (\S+)$`、`adb connect failed:` 等字符串 ⇒ **`host:port` 形式会由其自行 `adb connect`**（字符串级推断，未端到端实测）。
- MuMu 端口惯例 `16384+32×idx`（主机侧已多次验证，且**会漂**）——profile 按实例分文件即可（一实例一个 `<名>.toml`）。
- 触屏：`touch_mode` 选 `MaaTouch`（本机实测走 `app_process` 管道推送，见 §7.1）。

### 5.2 远端模拟器：实测与结论 ⚠️

本机对主机（100.79.173.69）做 TCP 探测（纯握手，无业务）：

```
16384 / 16416 / 16800 / 16832 → ConnectionRefusedError（WinError 10061「积极拒绝」）
22 → OPEN（SSH）
```

⇒ **模拟器 adb 端口不在局域网暴露**（只绑主机 127.0.0.1 或被防火墙 REJECT），而 SSH 通 ⇒ 想从本机操作远端模拟器，**只有两条路**：

1. **在主机上跑 maa-cli**（推荐）：主机已有 CLI 壳 + git + 完整 MAA 资源；届时是 `127.0.0.1:<口>` 本机 loopback，**没有本文提及的任何网络坑**。缺的只是「对齐 CLI 版本 + 链接资源 + 写 profile/task 模板」。
2. **本机 + SSH 隧道**（可行但打折）：
   - 对每个实例 `ssh -L <本地新口>:127.0.0.1:<远端口> Administrator@100.79.173.69 -N`，profile 里写 `address="127.0.0.1:<本地新口>"`（**别用远端同号端口，避免和本机自己的 MuMu 撞**）。
   - ⚠️ **`10.0.2.2` 回连坑**：MaaCore 的快速截图通道是 `adb -s <serial> exec-out "screencap | nc -w 3 10.0.2.2 <本机随机口>"`（asst.log 实测原文）。`10.0.2.2` 是**模拟器视角的宿主**——模拟器在远主机上时它指向**远主机 loopback**，而你监听的随机口在本机 ⇒ 该通道必然失败，MaaCore 会退到 `exec-out screencap -p`（本机实测 2.7MB/帧、946ms；跨网路只会更慢），识别速度整体下降。
   - 结论：隧道方案适合"偶发/低频"，**不适合常态批量**；真要走，先量化截图退化再定。

---

## 6. 账号

- `maa startup [CLIENT] --account-name <名>`（**别名 `--account`**，源码 `StartUpParams`）→ 写入 StartUp 任务 `params.account_name` → 交给 MaaCore 的 **StartUp 任务参数 `account_name`**——**与 MAA GUI 的 `StartUpTask.AccountName` 是同一参数、同一层**（GUI 的"账号管理"下拉也是落到这里）。
- 任务文件里也能直接写：`[[tasks]] type="StartUp" params={ account_name="…" }`。
- **GUI 的 `AccountSwitchEnabled` 在 maa-cli 无对应物**（没有配置文件字段、没有命令行开关）。它本来就是 GUI 侧的"要不要按 AccountName 切号"开关；maa-cli 场景下"一进程一实例一号"是自然形态，**天然不切号**。
- 判据（对本仓字典）：asst.log 侧切号过程标记（`AccountSwitchTask::…`、`SwitchAccount@*`）**照旧可用**；gui.log 侧 `账号切换 -->> <掩码>`、`任务出错: <链名>`、`任务已全部完成！` 等**在 maa-cli 下不存在**（没有 gui.log）。
- 切号失败静默的既有结论（目标掩码不在登录记录里则照常跑完）**在 maa-cli 下同样成立**——它只是把参数透传给同一 MaaCore。

---

## 7. 输出与判据对接

### 7.1 日志

| 产物 | 位置 | 说明 |
|---|---|---|
| MaaCore 日志 `asst.log` | `<maa dir log>` = `…\data\debug\asst.log` | **格式与 GUI 队完全一致**（同款 `[YYYY-MM-DD HH:MM:SS.mmm][LVL][Px][Tx]`），行内容同源 |
| maa-cli 自身日志 | **stderr**（默认）；`--log-file[=路径]` 落盘（默认 `<maa dir log>\YYYY\MM\DD\HH:MM:SS.log`） | 级别 `MAA_LOG` 或 `-v/-q`；前缀风格 `MAA_LOG_PREFIX` |
| 任务总结 Summary | stdout | 每子任务 `名字/类型/开始/结束/耗时`；fight 有掉落、infrast 有干员、recruit 有 tag——**`--no-summary` 可关** |
| `gui.log` | **不存在** | 这是 GUI 特有产物 |

asst.log 实测样本（2026-09-22 连接段，节选）：

```
Call ` D:/Xiach/MuMuPlayer/nx_main/adb.exe -s 127.0.0.1:16384 shell settings get secure android_id ` ret 0
Assistant::append_callback | ConnectionInfo {"details":{…,"address":"127.0.0.1:16384",…},"what":"Connected"}
Assistant::append_callback | TaskChainStart {"taskchain":"Custom","taskid":1,"uuid":"c80c68cdd4972cfd"}
Call ` … -s 127.0.0.1:16384 exec-out "screencap | nc -w 3 10.0.2.2 60226" ` ret 0 , cost 217 ms
The fastest way is RawByNc , cost: 225 ms
```

⇒ **本仓 `docs/MAA_LOG_DICTIONARY.md` 的 asst.log 段可直接复用**；gui.log 段判据需要"替代信号"（见 7.3）。

### 7.2 退出码（源码 `main.rs`/`run/mod.rs` + 实测）

- `0`：正常（含 `--dry-run`）；`1`：一切失败——配置错、core 加载失败、`Failed to find task file!`、任务链内 `MAA_CORE_ERRORED`（打印 `Some error occurred during running task!`）、SIGINT/SIGTERM（`Interrupted by user!`）。
- **没有细分退出码**（不是 `2=连接失败` 那种）⇒ 定因必须读日志/Summary，退出码只能判"跑没跑完"。

### 7.3 判据映射（巡检/补跑用）

| 想问 | GUI 队（现状） | maa-cli 队 |
|---|---|---|
| 这轮成功了吗 | gui.log `任务已全部完成！` | **退出码 0** + stdout Summary 全 Completed |
| 哪条链失败 | `任务出错: <链名>` | 非 0 退出 + `Some error occurred during running task!` + **asst.log** `Assistant::append_callback \| TaskChainError {"taskchain":…}`（字典 §2 已收录，`tools/mas_error_triage.py` 现成在读） |
| 切号了吗 | gui.log `账号切换 -->>` | asst.log `AccountSwitchTask::*`、`SwitchAccount@*`（无 GUI 侧"准备切换"行） |
| 连接失败？ | gui.log `连接失败`/`重连失败` | asst.log `ConnectionInfo … "what":"ConnectFailed","why":…` + 非 0 退出 |
| 没登进游戏 | `任务出错: 开始唤醒` | StartUp 链在 asst.log 的节点推进停住 + 超时；无中文链名行 |
| 吃了多少药 | gui.log `药: N, 库存: M` | asst.log `MedicineCounterTaskPlugin` + `append_task Fight {…}` 原样 |

**结论：巡检侧最省事的口径 = 「asst.log（同字典）+ 退出码 + Summary」三件套；凡字典里标注来源是 gui.log 的条目，在 maa-cli 队一律不要用。**

---

## 8. convert / import 与"模板库/场景调配"价值

- `convert`：TOML/YAML/JSON 互转（本机实测 `probe-daily.toml → probe-daily.yaml` 正常，`--format` 省略时按输出扩展名猜；既无输出又无 `-f` → `Error: Format not given`）。用途：MaaCore 只吃 JSON 的产物（基建计划、保全作业）可以**用 TOML 手写→convert 成 JSON**；反之把 GUI 导出的 JSON 转成可注释的 TOML 存档。
- `import`：把**本地文件或远程 URL** 的配置按类型放进 config 目录（`-t cli/profile/task/infrast/resource/copilot/sss-copilot/paradox-copilot`）。实测：`-n` 必须带扩展名（`imported-daily` → 报 `unsupported extension`；`imported-daily.toml` → OK）。远程导入 = **配置分发渠道**：任务模板/profile 挂到内部 HTTP 或 GitHub，`maa import <url> -t task` 即同步——对我们"模板库/场景调配"正合适（前提：别放凭据）。
- 注意：`import -t profile` 进来的是 profile（无账号字段），`-t cli` 才是 cli.toml；导入不校验语义，错在运行期才炸。

---

## 9. 不确定项 / 未实测 / 风险

1. **主机侧 GitHub 可达性**未测：`install/update`（下 MAA zip）与 `hot-update`（git clone MaaResource）在主机上要不要镜像，需实机验证（本机 raw 被 reset、api 可达）。
2. **隧道方案未端到端实测**：截图通道退化程度、"MaaCore 自行 adb connect"的确切条件（字符串级推断）、隧道稳定性都只是推演。别直接投产到巡检链路。
3. **0.5.9 vs 0.7.5 差异未逐项 diff**：主机 CLI 旧（本次全部结论以 0.7.5 为准）。
4. MaaCore 的**模拟器自动启动**行为（本机 config.json 里的模拟器路径）在远端场景会误触（找不到就报错，不会乱启动本机），未实测。
5. `--dry-run` **不校验 params 名值**（上游明说），Schema 只覆盖结构不覆盖 MaaCore 语义——"配置写得对"要靠实跑。
6. `cleanup`、`install -f`、`update` 在**链接复用**的单机上是危险动作（删日志 / 写穿生产目录），已在 §4.3 标注红线。
7. maa-cli **没有截图/取证子命令**（截图只能靠 MaaCore 日志行或外部 adb）——"截图取证"场景别指望它。

## 10. 下一步建议（给「路径取舍」卡 t_cc935c9b 的输入）

1. **主机侧 maa-cli 试点**（成本最低）：升级 CLI → 用 junction 复用 `E:\MAA-Multi\MAA_6`（或独立副本）→ 只在**非生产窗口**跑 `--dry-run` 与只读类命令，先验证"能连上、能起链"，再谈接管任务。
2. **本机**保留 maa-cli 做：模板/任务文件编制与 `convert` 工具链、`--dry-run` 校验、离线参数实验——**不抢生产实例**。
3. 与 AUTO-MAS 注入路线的取舍、抢实例/记账/通知缺口另见 `docs/MAA_DRIVER_OPTIONS.md`（t_cc935c9b）。

---

## 附录 A：本次实测命令与关键原始输出

```bash
MAA="$LOCALAPPDATA/Microsoft/WinGet/Links/maa-cli.exe"
"$MAA" --version                      # → maa 0.7.5
"$MAA" version                        # → maa-cli v0.7.5 / MaaCore v6.18.0
"$MAA" dir data|library|config|cache|resource|hot-update|log   # → §3.1 全表
"$MAA" list                           # → spa-nav（读 config\tasks\）
# 沙盒（避免碰实验位）：
MAA_CONFIG_DIR=C:/…/maaprobe4 "$MAA" list                     # → probe-daily
MAA_CONFIG_DIR=… "$MAA" --batch startup Official --dry-run    # → Summary:[StartUp] Unstarted ; exit 0
MAA_CONFIG_DIR=… "$MAA" --batch -v run probe-daily --dry-run  # → Hot update completed successfully / 4 段 Summary ; exit 0
"$MAA" convert …/probe-daily.toml …/probe-daily.yaml          # → 361 B yaml ; exit 0
"$MAA" convert …/probe-daily.toml                             # → Error: Format not given ; exit 1
MAA_CONFIG_DIR=… "$MAA" import …/daily.toml -t task -n imported-daily.toml -f   # → OK，list 中出现
"$MAA" activity Official              # → Opening mini games: - 黑流树海刷钱 ; exit 0
"$MAA" remainder 2 / remainder 7      # → 0 / 0
"$MAA" run nope-task                  # → Error: Failed to find task file! ; exit 1
"$MAA" complete powershell            # → Register-ArgumentCompleter … 'maa'
MAA_CONFIG_DIR=… "$MAA" init --format toml   # → 生成 profiles/default.toml（提示全走默认值）
```

主机侧（只读，经 ssh Administrator）：

```bash
where maa        # → C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\maa.exe
maa version      # → maa-cli v0.5.9 / Error: Failed to load MaaCore! (os error 126)
maa dir library  # → Error: Library not found
dir /b E:\MAA-Multi\MAA_6\resource            # → tasks/ global/ onnx/ …（完整资源）
type E:\MAA-Multi\MAA_6\resource\version.json # → last_updated 2026-09-20 12:47:33（与本机同快照）
```

本机 TCP 探测（纯握手）：`100.79.173.69:16384/16416/16800/16832 → Refused`；`:22 → OPEN`。

## 附录 B：本次探测的写入清单（透明性）

- 新增：`%TEMP%\maaprobe2/profiles/*`、`%TEMP%\maaprobe4/{profiles,tasks}/*`、`%TEMP%\maaup/*`（探测沙盒与上游文档缓存，可随时删）
- 追加：`%APPDATA%\loong\maa\data\debug\asst.log`（`--dry-run` 会加载 MaaCore，日志照写该处——**MaaCore 的 User Dir 不受 `MAA_CONFIG_DIR` 影响**，这本身就是一条隔离日志的用法）
- 未触碰：`config\profiles\default.toml`、`config\tasks\*`（含他会话的 `t9test/t9nores`）、主机任何文件、任何生产配置
