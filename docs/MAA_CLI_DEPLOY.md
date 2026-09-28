# maa-cli 部署与连通性验证（主机侧，含与 AUTO-MAS 共存）

> v1.2 · 2026-09-27（22:2x 初版；23:57 级别 3 实测后更新）· 2026-09-28 并发日志隔离小样（t_72ed337a；**结论=不成立**，§5/§8.1）· 任务 `t_9fc91afc`（mas）· 目标机 **MRFZ-0000（100.79.173.69）**
> 状态：**已实装；三级验证全部完成**。级别 1/2 通过并留证（§4）；**级别 3 真跑通过**（2026-09-27 23:03–23:53，候选 B=idx19，`RC=0`，StartUp `Completed` 2m24s，见 §4.3 与 `docs/audits/2026-09-27_maa_cli_lv3_transcript.txt`）。
> 配套文档：能力面/schema = `MAA_CLI_SURVEY.md`（t_8057ebaa）；日志判据 = `MAA_LOG_DICTIONARY.md`；模拟器拓扑 = `MAS_EMULATORS.md`。

---

## 0. 结论（拍板用）

| 问题 | 结论 | 详见 |
|---|---|---|
| **装在哪** | **主机（MRFZ-0000）为主部署位**：已装 `E:\MAA-CLI\bin\maa.exe`（0.7.5）+ core/资源副本 `E:\MAA-CLI\core`。本机（DESKTOP-L1OEMR3）保留既有 0.7.5 做「模板编制 / 本机实例实验」；**不建议**用 SSH 隧道常态驱动生产实例（截图通道退化，SURVEY §5.2） | §1 §2 |
| **core/资源从哪来** | **复用生产同版（复制而非直链）**：`MAA_6` → `E:\MAA-CLI\core`（sha256 `EFF5C522…`，与生产 6.18.0 逐字节同款），`junction` 挂到 `data\{lib,resource}`。不用 `maa install` 下载（版本可能新于生产）；不直链生产目录（避免锁/写穿，SURVEY §4.3 的「更稳替代」即本方案） | §3 |
| **共存** | 与 AUTO-MAS **无资源竞争**：不占 MAA_N 槽位、不写 AUTO-MAS 数据、独立 core 副本、独立配置/日志目录；**唯一共享 = 同一 adb server（正常形态）**。唯一冲突点 = **同一台模拟器被双驱动** ⇒ 用「一进程一设备 + 运行前核对」隔离 | §5 |
| **现在能干什么** | 1 级 `version/dir/list/activity/--dry-run` ＋ 2 级 `adb devices`/截图 ＋ **3 级真跑 `startup` 全通**（idx19 实测：RC=0 / Summary Completed / 2m24s / asst.log 全链完成） | §4 §6 |

**红线遵守声明**：本 run 未触碰 AUTO-MAS 侧车、未干预任何在飞 MAA（当时 MAA_9/10/11 在飞，仅只读观察）、未改任何生产配置、未新建模拟器；主机侧仅新增 `E:\MAA-CLI\` 与 `%APPDATA%\loong\maa\`（均在 §7 可一键回滚）。

**级别 3 追加（2026-09-27 23:0x）**：仅冷启并操作**实例 19 一台**（停用号 v；先核 `MAA_4/MAA_8` 无 MAA 进程）；未触碰任何在产实例、未改任何配置、未做账号操作；收尾把实例**关回初始态**（详见 §4.3）。

---

## 1. 决策：装在哪（主机 vs 本机）

| 维度 | 主机 MRFZ-0000（✅ 采纳） | 本机 DESKTOP + SSH 隧道（备选） |
|---|---|---|
| 网络路径 | 模拟器/adb 全在 loopback，零隧道、零额外延迟 | 直连主机 adb 口**实测 ConnectionRefused**（SURVEY §5.2）⇒ 必须 `ssh -L` 隧道；一实例一隧道 |
| 截图/识别 | 原生（MaaCore 快速通道可用） | 隧道下 `screencap \| nc 10.0.2.2` 快速通道**必然失效**，退慢通道，识别变慢（SURVEY §5.2） |
| 批量并行 | 每设备一进程，天然形态；资源都在本机 | N 台实例 = N 条隧道，故障面大 |
| 与 AUTO-MAS 关系 | 同机共存，可控（§5） | 完全绕开生产机（但把风险转移到隧道稳定性） |
| 已实装 | ✅ 本次完成（0.7.5 + core + profile） | 本机已有 0.7.5（2026-09-20 装）+ 本机实例配置，可直接用于本机 |

**主机命令集（本 run 实装，逐条可用）**：

```powershell
# ① CLI 二进制 —— 优先 winget（源索引新时）；本主机源索引旧（停在 0.5.9）且 source update 被取消，故实装走官方 zip：
winget install --id MaaAssistantArknights.maa-cli -e --source winget --accept-source-agreements --accept-package-agreements --disable-interactivity
#   （winget 失败/旧版时）：
Invoke-WebRequest 'https://github.com/MaaAssistantArknights/maa-cli/releases/download/v0.7.5/maa_cli-v0.7.5-x86_64-pc-windows-msvc.zip' -OutFile 'E:\MAA-CLI\_dl\maa_cli-0.7.5.zip'
Expand-Archive 'E:\MAA-CLI\_dl\maa_cli-0.7.5.zip' -DestinationPath 'E:\MAA-CLI\bin'
winget uninstall --id MaaAssistantArknights.maa-cli -e --source winget --disable-interactivity   # 卸掉旧版，避免 PATH 歧义

# ② core/资源（复制同版 + junction）
robocopy 'E:\MAA-Multi\MAA_6' 'E:\MAA-CLI\core' /E /XD 'E:\MAA-Multi\MAA_6\cache' 'E:\MAA-Multi\MAA_6\config' 'E:\MAA-Multi\MAA_6\data' 'E:\MAA-Multi\MAA_6\debug'
mklink /J '%APPDATA%\loong\maa\data\lib'      'E:\MAA-CLI\core'
mklink /J '%APPDATA%\loong\maa\data\resource' 'E:\MAA-CLI\core\resource'

# ③ profile（一实例一文件；示例见 §4.1）
```

**本机命令集（备选，隧道）**：

```bash
# 每个目标实例一条隧道（本地端口别用远端同号，避免与本机 MuMu 撞）：
ssh -N -L 16800:127.0.0.1:16800 Administrator@100.79.173.69
# 然后本机跑（address 指向本地隧道口）：
maa-cli startup Official -a 127.0.0.1:16800 -p <profile>
# 注意：快速截图通道退化（SURVEY §5.2），适合偶发、不适合常态批量。
```

---

## 2. 主机安装实录（2026-09-27 21:57–22:20，全部实跑）

| 步骤 | 命令/动作 | 结果（证据） |
|---|---|---|
| 1 | `winget install --id MaaAssistantArknights.maa-cli -e --source winget …` | winget v1.10.390；源索引停在 **0.5.9**（`winget source update` 被取消）；装上 0.5.9 后又卸载（避免 PATH 歧义） |
| 2 | 官方 zip v0.7.5 下载 | `maa_cli-v0.7.5-x86_64-pc-windows-msvc.zip` 3,464,247 B / **1.8 s**（主机 GitHub 可达 ✅） |
| 3 | 解包 | `E:\MAA-CLI\bin\maa.exe`（7.84 MB，sha256 `49FC56C7…`；zip 内文件名是 `maa.exe`，非 `maa-cli.exe`） |
| 4 | `robocopy MAA_6 → E:\MAA-CLI\core` | **10,107 文件 / 529.41 MB**，rc=1（成功）；`MaaCore.dll` sha256 = `EFF5C522…`（与生产 MAA_6 相同） |
| 5 | junction ×2 | `data\lib → E:\MAA-CLI\core`；`data\resource → E:\MAA-CLI\core\resource` |
| 6 | profile ×2 | `%APPDATA%\loong\maa\config\profiles\{kaori-official,v-official}.toml`（§4.1） |
| 7 | 验证 | `maa.exe version` → **`maa-cli v0.7.5` + `MaaCore v6.18.0`**（说明 DLL 经 junction 正常加载） |

**规范入口：`E:\MAA-CLI\bin\maa.exe`（全路径；未改 PATH）。** 自动化经 SSH 以 `Administrator` 执行（配置目录在 Administrator 的 `%APPDATA%`；换账户需带 `MAA_CONFIG_DIR/MAA_DATA_DIR`）。

---

## 3. core / 资源策略（三方案对比与取舍）

| 方案 | 版本 | 下载 | 与生产隔离 | 风险 |
|---|---|---|---|---|
| `maa install`（stable） | 最新 stable（可能 > 生产 6.18.0） | ~数百 MB（需镜像/网络） | 完全独立 | 版本与生产不一致；首次网络依赖 |
| junction 直链 `E:\MAA-Multi\MAA_6`（SURVEY §4.3 建议） | = 生产 | 0 | ❌ 与生产目录耦合 | GUI 自更新时版本被动跟随；`install/update -f` 会**写穿**生产目录（SURVEY 红线） |
| **复制 `MAA_6` → `E:\MAA-CLI\core` + junction（本 run 实装）** | = 生产（sha 逐字节同款） | 0（本机磁盘复制，秒级） | ✅ 完全独立 | 占 529 MB 磁盘（E: 余 ~140 GB） |

- 选型理由：要「与生产同版」（可比性）又不想承担「耦合生产目录」的锁/写穿风险 ⇒ 复制是唯一同时满足两者的方案。
- 维护：生产 MAA 升级后，重跑一次 robocopy 同步即可（`MAA_6` 若换版，core 副本跟新）；或用 `maa install` 走独立线（需在 `cli.toml` 配镜像，见 SURVEY §3.4）。
- 备查：`E:\MAA-Multi\MAA_25/MAA_28` 是未挂脚本的空闲目录，但 core 为旧版（`295A7AA7…`，2026-09-09）——**不要**拿它们当源；根目录那份 `MaaCore.dll` 是更旧的 2026-08-18 版。

---

## 4. 连通性证据（级别 1 + 级别 2）

### 4.1 级别 1：无害命令（主机实测）

```
> maa.exe version
maa-cli v0.7.5
MaaCore v6.18.0

> maa.exe dir {config,data,resource,library,cache,log,hot-update}
config     = C:\Users\Administrator\AppData\Roaming\loong\maa\config
data       = C:\Users\Administrator\AppData\Roaming\loong\maa\data
resource   = C:\Users\Administrator\AppData\Roaming\loong\maa\data\resource
library    = C:\Users\Administrator\AppData\Roaming\loong\maa\data\lib
cache      = C:\Users\Administrator\AppData\Local\loong\maa\cache
log        = C:\Users\Administrator\AppData\Roaming\loong\maa\data\debug
hot-update = C:\Users\Administrator\AppData\Roaming\loong\maa\data\MaaResource

> maa.exe list          # 尚无自定义任务 → 空输出，rc=0
> maa.exe activity      # → Opening mini games: - 黑流树海刷钱 ; rc=0（22:29 复跑）
> maa.exe --batch startup Official --dry-run -p kaori-official
Summary
----------------------------------------
[StartUp] Unstarted     # rc=0，配置解析通过（dry-run 不连设备）
> maa.exe --batch startup Official --dry-run -p v-official    # 同样 [StartUp] Unstarted, rc=0
```

profile 示例（已落盘，`kaori-official.toml`）：

```toml
[connection]
adb_path = "E:/MuMu Player 12/shell/adb.exe"
address = "127.0.0.1:16548"          # idx37 官-25（停用号）
config = "General"

[instance_options]
touch_mode = "MaaTouch"
```

### 4.2 级别 2：只读连设备（主机实测）

- **`adb devices`**（`E:\MuMu Player 12\shell\adb.exe`，22:15 快照）：**31 台 online**（29 个 `127.0.0.1:<端口>` + 2 个 `emulator-55xx`），如 `127.0.0.1:16480/16736/16800/16832/…`（完整清单见 `docs/audits/2026-09-27_maa_cli_deploy_transcript.txt`）。
- **截图（只读，adb `exec-out screencap -p`）**：目标 idx3（明日方舟-b-9，MAA_4 槽，其班次任务已完成、实例空闲）→ PNG 1,374,105 B；OCR 判定 = **游戏大厅**，账号 **「小熊附体三」/ ID:15533566**（与 idx3 绑定账号 小熊附体@B 一致），画面元素：逐影集趣（当期活动）、Sanity 210、15-10 隔离净空、编队/干员/公开招募等。
  ⇒ 主机侧「设备可达 + 画面可读」实证；截图：`docs/audits/shots/2026-09-27_maa_cli_deploy_idx3.png`。

### 4.3 级别 3：真跑（2026-09-27 23:03–23:53，靶位=候选 B idx19「官-5 / v」，全程只碰这一台）

**为什么打 B 没打 A（idx37）**：同板资产普查任务（t_b6d3912a）剩余批次含 37 且 22:42 刚打过（其 `results_sweep.jsonl` 有 37 记录）、batch_f/g 不含 19；补跑任务（t_7d630aa6）只跑启用号，v 已停用（`/api/scripts/user/get` 只读复核 `Status=False`）⇒ **19 是当晚「无人排队」的最干净靶位**。

**步骤（全部 SSH Administrator，命令逐条实跑）**

1. 前置检查：主机 `MAA.exe` 仅肉鸽队 l-1/l-2/l-4；`E:\MAA-Multi\MAA_4`、`MAA_8` 均无 MAA（lead 条件 1）；实例 19 未运行 ⇒ 冷启。
2. 冷启与就绪：`MuMuManager.exe control --vmindex 19 launch` → VMM pid 44568；监听口 `16992`/`5593`；`adb connect 127.0.0.1:16992` → `state=device`、`sys.boot_completed=1`。
3. 真跑（唯一动作；无 fight / 无 roguelike / 无改配置）：
   `E:\MAA-CLI\bin\maa.exe --batch startup Official -a 127.0.0.1:16992 -p v-official --log-file=E:\MAA-CLI\logs\lv3-v-idx19.log`
4. 结果：**`RC=0`**；stdout `[StartUp] 23:28:54 – 23:31:18 (2m 24s) Completed`；asst.log：`TaskChainCompleted` + `AllTasksCompleted {"taskchain":"StartUp"}`；游戏进程存活（pid 2768）。

**证据与判读**

- 截图 before/after（`shots/2026-09-27_maa_cli_lv3_idx19_pre.png` / `..._post.png`）：before = MuMu 桌面（游戏未启动、可见「明日方舟」图标）；after = **游戏大厅**（理智 210、当期活动「逐影集趣」、终端/基建/仓库等全导航条）。
- **账号对照**：after 大厅博士名「堂吉诃德」/ ID `751737728`（本地 OCR）。与实例 19 的槽位身份一致——同板普查 22:23 于其标题页读到 `mask 189****9036 / uid 751737728`（t_b6d3912a `results_sweep.jsonl`）。**全程未切号**（客户端原登录态直接进大厅；lead 条件 6 未触发）。
- 性能注记：连接 → StartUp 全链 2m24s；MaaTouch 快速通道命中 `screencap | nc 10.0.2.2`（238ms/帧）——主机内网形态工作正常（SURVEY §5.2 的「隧道退化」与主机侧无关）。

**收尾（干净）**：`closedown` 尝试报 `Error`——**漏带 `-p v-official`**，回退 default profile 的裸 `adb` ⇒ `Connection command failed to exec`（教训并入 §6/§7）→ 改用 `control --vmindex 19 shutdown` 成功；终态复读：`VMM19=down`、`maa-cli procs=0`、`16992 offline`、`MAA_4/MAA_8` 仍无 MAA。**实例已回到初始态（原本就是关的）。**

**本次踩坑修订（均实测，值得归档）**

- `MuMuManager.exe` 真实路径 = `E:\MuMu Player 12\nx_main\MuMuManager.exe`（仓库两处旧记录的 `x_main\` 不存在）；`-v` 与 `--vmindex` 两种写法均可。
- 经 ssh 拉起宿主机长任务：`Start-Process` 会随会话结束被带走 ⇒ 用 **`Win32_Process.Create`**（或 schtasks，同肉鸽队做法）才能跨会话存活。
- 今晚主机 SSH 吞吐仅 ~8KB/s：1.6MB 截图直取/回传会超时（2–4 分钟起）；大图取证走 **`screencap → /sdcard → adb pull → scp -O`**。

---

## 5. 与 AUTO-MAS 共存策略

| 资源 | 共享？ | 说明 / 隔离手段 |
|---|---|---|
| adb server（5037） | **共享**（同一个 `adb.exe`） | adb 是客户端-服务端模型，多客户端并存是常态；maa-cli 默认 `kill_adb_on_exit=false`（SURVEY §3.2），不会杀 server |
| 模拟器实例 | **唯一冲突点** | 同设备双驱动 = 双输入互踩。隔离 = **一进程一设备**（profile `address` / `-a` 点名）；每次运行前核对该设备「无在飞任务」（`app.log` 现查 / `mas_preflight.py`） |
| MAA 槽位目录（MAA_N） | ❌ 不共享 | maa-cli 不读不写 `E:\MAA-Multi\MAA_*`（本方案用独立副本） |
| AUTO-MAS 数据 | ❌ 不共享 | 不碰 `E:\AUTO-MAS`；AUTO-MAS 的「脚本锁」体系不覆盖 maa-cli ⇒ 人工/脚本层核对必须做 |
| core/资源 | ❌ 不共享（独立副本） | 升级解耦；**禁止**对共享目录跑 `install/update -f`（SURVEY 红线） |
| 配置/日志 | ❌ 独立 | `%APPDATA%\loong\maa\*`；MaaCore `asst.log` 在 `data\debug\` |
| 端口 | ❌ 独立 | maa-cli 无监听口；模拟器端口只读现查（会漂，见 `MAS_EMULATORS.md` §2） |

**并发（并行基准）注意**：
- 多进程 = 每进程独立 profile/设备；日志隔离用 `MAA_STATE_DIR`（改它 `asst.log` 跟着搬，SURVEY §3.1）或 `--log-file`。
- ✅ 同 data 目录下多进程 `asst.log` **互踩已实测坐实（2026-09-28，不隔离，丢日志形态）**：先占文件者正常记账，另一方在重叠窗口的日志**整段静默丢失**（无回填、无第二文件），前者退出后才恢复落盘 ⇒ **并发必逐进程 `MAA_STATE_DIR`**；证据 `docs/audits/2026-09-28_maacli_log_isolation/`。
- 班次窗口（05:00–08:00 / 19:30–23:00）建议只碰**非产线实例**（停用号），避开重试/补发窗口。
- maa-cli 无 AUTO-MAS 的排队/记账/通知——批跑场景需要外部编排（与 t_cc935c9b 的取舍对接）。

---

## 6. 级别 3 runbook（**已执行 2026-09-27 23:0x（候选 B/idx19）**；以下保留为复用模板）

**候选**（首选 A；**本轮实际执行了 B——理由见 §4.3**）：

| 候选 | 实例 | adb | 账号 | profile | 备注 |
|---|---|---|---|---|---|
| **A（首选）** | idx37 官-25 | `127.0.0.1:16548` | Kaori@官（**停用**） | `kaori-official.toml` ✅ | 停用号 ⇒ AUTO-MAS 不调度；实例当前未运行（需冷启） |
| B | idx19 官-5 | `127.0.0.1:16992` | v@官（**停用**） | `v-official.toml` ✅ | 同上 |
| C | 任一空闲产线实例 | 现查 | — | 按需建 | **需 lead 点名**；仅班次外；运行前核对无在飞 |

**步骤（候选 A 示例，全部经 SSH Administrator）**：

```powershell
# 0) 前置核对：Kaori 停用（不在调度内）+ 该实例无在飞（app.log 现查无此 sid）
# 1) 冷启实例
& 'E:\MuMu Player 12\nx_main\MuMuManager.exe' control -v 37 launch
# 2) 等 boot：adb -s 127.0.0.1:16548 get-state = device 且 getprop sys.boot_completed = 1
# 3) 真跑（只做 StartUp 到主界面，不跑任何游戏任务）
& 'E:\MAA-CLI\bin\maa.exe' startup Official -a 127.0.0.1:16548 -p kaori-official --log-file 'E:\MAA-CLI\logs\lv3-kaori.log'
# 4) 留证：stdout Summary / asst.log（data\debug\asst.log）/ 结束截图 / 退出码
# 5) 收尾：& '...maa.exe' closedown Official -a 127.0.0.1:16548 ; 可选 MuMuManager control -v 37 shutdown
```

**判据**（SURVEY §7.3）：成功 = 退出码 0 + Summary `[StartUp] Completed` + asst.log 推进到主界面；失败同样留证（连不上/登出态/挡路弹窗）。
**预案**：客户端若处登出态 → StartUp 失败（仍算「连通性」证据，但不含端到端成功）；任何失败不碰 AUTO-MAS，直接 teardown。
**批准方式（本轮已走完）**：lead 于 2026-09-27 22:48 在任务 body 追加批准＋约束（只 startup、一次一台、不换号、与补跑错开）；执行记录与结果见 §4.3。
**实测修订**：MuMuManager 实路径 = `E:\MuMu Player 12\nx_main\MuMuManager.exe`；冷启后端口以 VMM pid 的监听口现查为准（本例 16992/5593 均通）；`closedown` 务必带 `-p`（见 §7）。

---

## 7. 回滚 / 清理

**主机全撤（3 条，互不依赖）**：

```powershell
Remove-Item -Recurse -Force 'E:\MAA-CLI'                                                  # 二进制 + core 副本 + 日志
Remove-Item -Recurse -Force "$env:APPDATA\loong\maa", "$env:LOCALAPPDATA\loong\maa"       # 配置/数据/缓存（含 junction 本体）
winget uninstall --id MaaAssistantArknights.maa-cli -e --source winget --disable-interactivity  # 若仍装着 winget 版（本 run 已卸）
```

**`maa cleanup` 语义（别当卸载用）**：`cleanup [cli-cache|core-cache|debug|log]` **只清缓存/日志**，不删 core、不删配置、不卸载二进制；它是日常保养命令（注意 `log/debug` 会删 asst.log，巡检机别乱跑）。
**子命令都要带 `-p <profile>`**：本 run 实测 `closedown Official -a <serial>` 省略 `-p` 时回退 default profile（裸 `adb`，无 PATH ⇒ `Connection command failed to exec`）；正确形态：`maa.exe --batch closedown Official -a 127.0.0.1:<port> -p <profile>`。`startup` 同理必须带 `-p` 才能复用生产同款 adb_path。
**影响面**：全撤不动 AUTO-MAS / MAA / 模拟器；`E:\MAA-CLI` 是独立副本，删除不影响任何生产目录。
**本机残留**：本次探测在本机建的 `profiles\t9test.toml`、`t9nores.toml` 已删除（dry-run 追加的 asst.log 行属日志噪声，无碍）。

---

## 8. 缺口与后续（交给并行基准 / 路径取舍卡）

1. ~~**并发日志隔离**（多进程同 data 目录）未验证 —— 并行基准先做 2 进程小样。~~ ⇒ **已实测（2026-09-28，t_72ed337a）：隔离不成立（丢日志形态）**——后占者重叠窗口整段静默丢、无回填、无第二文件；并行基准须逐进程 `MAA_STATE_DIR`。证据 `docs/audits/2026-09-28_maacli_log_isolation/`（含 phase3/phase4 原始输出）。遗留项：MaaCore `Utils/Logger.cpp` 源码级证据未取到（GitHub raw 404）＝运行时推断。
2. `hot-update`：本次级别 3 真跑 CLI 日志仅见 `MaaResource\resource not found` WARN（噪声级），未见拉仓/限速；如需强制刷新再评估镜像（SURVEY §3.4）。
3. winget 源索引旧（0.5.9）——回归 winget 管理需先修源（`source update` 被取消原因未深究）。
4. 账号语义：maa-cli 无 `AccountSwitchEnabled` 对应物（SURVEY §6）——多号设计注意。
5. **级别 3 已通过（§4.3）**；下一步 = 「并行基准小样」与「编排封装」（与 t_cc935c9b 对接）。
6. 并发取证通道：主机 SSH 吞吐低时（今晚 ~8KB/s）用 `screencap→/sdcard→adb pull→scp -O`；并行基准若同机多任务取图，注意 adb server 争用。

---
*原始 transcript：部署 + 级别 1/2 = `docs/audits/2026-09-27_maa_cli_deploy_transcript.txt`；**级别 3 = `docs/audits/2026-09-27_maa_cli_lv3_transcript.txt`**（含 step1/launch/watch/teardown 全日志与 asst.log 片段）。截图：`docs/audits/shots/` 下 `..._deploy_idx3.png`（2 级）、`..._lv3_idx19_pre.png`、`..._lv3_idx19_post.png`（3 级）。*
