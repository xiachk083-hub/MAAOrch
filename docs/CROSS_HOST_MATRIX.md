# 跨机能力矩阵 + 降级通道：AUTO-MAS 挂掉时，主机 maa-cli 能否顶上

> v1 · 2026-09-28 · 任务 `t_31d8e3b9`（mas）· **只读 + dry-run**：未发车、未改任何配置、未开 SSH 隧道；实跑面 = 主机/本机各一次 `--dry-run` + 只读探测（记录见 `docs/audits/2026-09-28_cross_host_drill.txt`）。
> 口径：**主机** = MRFZ-0000（`100.79.173.69`；模拟器 + AUTO-MAS + MAA 全在这）；**本机** = DESKTOP-L1OEMR3（`D:\1-2\MAAOrch`；调度、编排、分析、文档）。
> 配套：`MAA_CLI_SURVEY.md`（maa-cli 能力面）/ `MAA_CLI_DEPLOY.md`（主机 maa-cli 实装与 1/2/3 级验证）/ `MAA_DRIVER_OPTIONS.md`（P1/P2/P3 取舍 + `maacli_run.py` 门禁草案）/ `MAS_EMERGENCY_RUNBOOK.md`（F1 恢复）/ `MAS_INSTANCE_LEASE.md`（实例租约）。

---

## 0. 结论先行（给拍板）

| 问题 | 结论 |
|---|---|
| 哪些活**必须**在主机 | 一切「碰实机」的物理动作：adb 截图/触控、MAA 任务执行、模拟器启停、AUTO-MAS 配置写、主机桌面/GUI。原因（本卡复测）：模拟器与 adb 端点**只绑主机 loopback**，本机直连被拒（§4.2）。 |
| 哪些活**本机**能做 | 发车（经 tailnet REST，生产现行；或 SSH 驱动主机程序）、日志/账表读取、对拉回帧的 OCR/识别、模板编制 + dry-run、文档与台账（§2）。 |
| 降级通道能否顶上 | **能，但限窄场景**。主机已装 maa-cli 0.7.5 + core 副本 + 演练沙盒（`E:\MAA-CLI\drill`：2 profile + 1 task）；本卡复核 dry-run 通过（主机/本机各一次 rc=0）；真跑先例 = 级别 3 `startup` 全链 rc=0（2026-09-27 23:03，`MAA_CLI_DEPLOY.md` §4.3）。**旁路不记账**，且对「在飞的 MAA」零感知 ⇒ 必须先过 §3.6 条件清单。 |
| 与 AUTO-MAS 记账冲突 | **不会写坏账，但会漏账**：旁路跑不进 `Tag`/`history`/通知；主链路恢复后按自己的账仍会再跑一遍（双跑）。实例层是硬冲突点（§3.5）。 |
| 需要新增什么 | 本卡不新增执行器；降级=人工/agent 按 §3.3 手工执行。自动化包装（`tools/maacli_run.py`，门禁 G0–G6 + ledger）已有设计（`MAA_DRIVER_OPTIONS.md` §3.2），未实现。 |

---

## 1. 入口清单（本卡实测）

| 面 | 入口 | 谁可达 | 实测（2026-09-28） |
|---|---|---|---|
| AUTO-MAS REST | `http://100.79.173.69:36163`（**无鉴权**，OpenAPI=`_ams.json`） | 主机 loopback + tailnet（0.0.0.0 LISTEN） | 本机 HTTP 404 in 0.09s；`/api/info/get/overview` code=200；TIME_WAIT 源 IP=本机 100.111.209.108 |
| SSH | `ssh -i ~/.ssh/maaorch_target Administrator@100.79.173.69` | 本机（key） | 全程在用；截图拉流 1.36s / 1.58MB |
| 主机 maa-cli | `E:\MAA-CLI\bin\maa.exe`（**全路径**；PATH 里已没有 maa） | 主机（经 SSH 执行） | `version` → maa-cli v0.7.5 + MaaCore v6.18.0，rc=0 |
| 主机 adb | `E:\MuMu Player 12\shell\adb.exe`；端点 `127.0.0.1:<口>`（**loopback 绑定**） | 主机；本机仅隧道 | `adb devices` ≠ 常量（09:50 五台 → 09:56 三台）；本机直连 16832 被拒 |
| 主机 MuMuManager | `E:\MuMu Player 12\nx_main\MuMuManager.exe`（少写 `x_main\` 会找不到） | 主机（经 SSH 执行） | 只读引用（`MAA_CLI_DEPLOY.md` §4.3 实跑） |
| 主机桌面 | Electron AUTO-MAS / MAA GUI / 模拟器窗口 | 主机 | —— |
| 本机工具链 | repo `D:\1-2\MAAOrch\tools\*`（297 个）；PY312=`C:\...\Programs\Python\Python312\python.exe`（cv2+rapidocr 已装） | 本机 | 全程在用 |
| 本机 maa-cli | `%LOCALAPPDATA%\Microsoft\WinGet\Links\maa-cli.exe`（0.7.5） | 本机 | `--version` → 0.7.5；dry-run rc=0 |
| 本机 MuMu | `D:\Xiach\MuMuPlayer\nx_main\adb.exe`（本机实例当前未运行） | 本机 | `adb devices` → 空 |

---

## 2. 能力矩阵（主机可行 / 本机可行 / 需 SSH 隧道 / 需额外依赖）

> 语义：**主机可行**=登录主机（或主机内程序）可直接完成；**本机可行**=从本机侧（调度机）直接完成（含「本机发起、经 SSH 在主机执行」——那不是隧道，是 exec 通道）；**需 SSH 隧道**=只有经 `ssh -L` 端口转发才能从本机完成的子集；**额外依赖**=该能力在现有面之外还要满足什么。

| 能力 | 主机可行 | 本机可行 | 需 SSH 隧道 | 额外依赖 |
|---|---|---|---|---|
| **发车**（把任务链交给某实例） | ✅ REST loopback / maa-cli 直跑（级别 3 实跑先例） | ✅ 经 tailnet REST `POST /api/dispatch/start`（**生产现行**：cron→`mas_dispatch.py`）；或 SSH 驱动主机 maa-cli（勿隧道） | ❌ 不需要（REST 直达；maa-cli 跑在主机 loopback） | REST 路＝AUTO-MAS 侧车在线；旁路＝SSH + 主机 maa-cli（profile+task）+ 目标实例空闲 |
| **截图**（取实机画面） | ✅ `adb exec-out screencap` / 窗口截图；AUTO-MAS 亦有 `/api/ocr/screenshot*`（服务端执行） | ✅ 三条：① SSH 执行主机 adb→字节拉回（**生产现行**，1.58MB/1.36s）；② REST `/api/ocr/screenshot/adb`（0.59s，PNG base64，**无需 SSH**）；③ 隧道（见右） | ⚠️ 技术上可行（adb 绑 loopback+SSH 通），但**快速通道必退化**（`10.0.2.2` 回连坑，SURVEY §5.2）；仅偶发/排查 | SSH 或 REST（二选一）；隧道路另需端口转发 |
| **识别**（OCR/画面判定） | ✅ MaaCore 原生（跑任务时内建）；AUTO-MAS OCR 端点（`/api/ocr/check/image*`）也在主机执行 | ✅ 对**已拉回帧**离线判定（本卡实测 `CLIENT_STATE=正常:大厅` / `模态:账号管理残留`）；实时闭环识别需连续取帧（同上） | —（识别不依赖隧道；隧道只解决"取帧"） | 本机 PY312 + cv2 + rapidocr（**已装**）；主机侧零额外依赖 |
| **日志**（app.log/gui.log/asst.log/history） | ✅ 文件都在主机 | ✅ SSH 拉取（`type`/tar/尾部轮询，大文件走 tar）；REST 结构化读（`/api/history/*`、`/api/info/get/overview` 的 `Proxy` 段含 `LastProxyDate/ProxyTimes/ErrorTimes/ErrorInfo`） | ❌ 不需要 | SSH；**吞吐会波动**（曾 8KB/s，本卡时段 ~1.2MB/s） |
| **配置**（脚本/队列/计划/用户） | ✅ REST loopback / GUI / 文件 | ✅ REST 写＝**界面等价操作**（生产现行三写一车）；文件级只读/白名单（写文件走 SSH 上传+mtime 校验） | ❌ 不需要 | 红线：**绝不手改 `ScriptConfig.json`/`QueueConfig.json`**（活配置+DPAPI 密文）；写操作"读→写→复读"留撤销记录 |
| **实例控制**（启停/健康/连接） | ✅ MuMuManager 启停 + adb connect（权威判据链：VMM 进程→监听口→`get-state`） | ⚠️ 无直连通道；必须 SSH 执行主机侧命令（`mas_preflight.py` 现行做法）；REST `/api/emulator/status` 只读可本机直取 | ⚠️ 仅 adb 类动作可隧道；**MuMuManager 不可**（主机进程，必须 SSH） | SSH；（状态面 MuMuManager `info -v` **假死是常态**，只信 VMM 进程+adb） |
| **内容写作**（文档/模板/台账） | ✖ 不适用（写作面不在主机；主机只放"部署件"） | ✅ repo + 本机 maa-cli（`--dry-run`/`convert` 本卡实测通过）；文档/复盘/审计全在本机 | ❌ 不需要 | 模板/文件下发到主机需 SSH 上传（或版本库分发） |

**一句话小结**：
- **必须主机**：碰实机的物理动作（截图/触控/跑任务/起停模拟器/主机 GUI）。
- **本机直接可做**：编排发车（REST）、读账读日志（REST+SSH）、离线识别（PY312）、模板编制与校验（本机 maa-cli）。
- **隧道只补一小块**：本机→adb 直连（截图/触控）——可行但不推荐常态；生产取图走「SSH 执行主机 adb」或 REST，均不经隧道。

---

## 3. 降级通道：AUTO-MAS 挂掉 → 主机 maa-cli 跑一个号的日常

### 3.0 先恢复主链路，再谈旁路

REST 挂的第一动作仍应是**恢复 AUTO-MAS**（`MAS_EMERGENCY_RUNBOOK.md` §3.1 F1：重启侧车→Electron 自权重拉→兜底整机重拉）。本节的 maa-cli 旁路只在「**主链路短时间恢复不了，且有号的当日任务必须跑**」时启用——它是预案 C，不是替代品。

### 3.1 可行性与判据（本卡复核）

- **dry-run 通过**：主机 `MAA_CONFIG_DIR=E:\MAA-CLI\drill\config` + `run daily_official --dry-run -p idx19` → rc=0，Summary 7 段（StartUp / Fight 1-7 x1 / Recruit / Infrast / Mall / Award / CloseDown）全 `Unstarted`；本机沙盒同源副本同样 rc=0。
- **真跑先例**：级别 3（2026-09-27 23:03–23:53，idx19）：`startup` 全链 rc=0、Summary `Completed`、asst.log `AllTasksCompleted`、2m24s；当时 `screencap | nc 10.0.2.2` 快速通道 238ms/帧（主机 loopback 形态健康）。
- **判据三件套**（gui.log 判据在 maa-cli 下**不存在**，SURVEY §7.3）：**退出码（ssh 退出码即 maa rc）＋ stdout Summary 全 Completed ＋ asst.log**（`%APPDATA%\loong\maa\data\debug\asst.log`，与 GUI 同格式，本仓字典 asst 段直接可用）。

### 3.2 现成资产（复用，不新装）

| 资产 | 位置 | 说明 |
|---|---|---|
| CLI 二进制 | `E:\MAA-CLI\bin\maa.exe` | v0.7.5；**必须全路径**（PATH 已无 maa） |
| core/资源 | `E:\MAA-CLI\core` + junction（`%APPDATA%\loong\maa\data\{lib,resource}`） | 与生产 6.18.0 同版副本；**禁用 `install/update -f`**（写穿红线） |
| 演练沙盒 | `E:\MAA-CLI\drill\config\` 下 3 文件（`profiles\idx19.toml`、`profiles\idx37.toml`、`tasks\daily_official.toml`） | 两个 profile 对应**停用号**：idx19=官-5/v（`127.0.0.1:16992`）、idx37=官-25/Kaori（`127.0.0.1:16548`）；**端口用前现查** |
| 日志 | `E:\MAA-CLI\logs\`（03 级 `--log-file` 惯例） | asst.log 在用户 data 目录；`--log-file` 必须绝对路径 |
| 本机副本 | scratch `t31_drill`（本卡）；repo 侧无 | 模板编制可在本机做、校验后再上传 |

### 3.3 命令集（五步；0–2 只读，3 需批准，4–5 收尾）

```bash
SSH="ssh -i ~/.ssh/maaorch_target Administrator@100.79.173.69"

# 0) 主链路恢复尝试（runbook F1）——恢复成功就停在这里，不用旁路

# 1) 选靶 + 现查（全只读）
$SSH '"E:\MuMu Player 12\shell\adb.exe" devices'          # 目标实例在不在、端点是多少
$SSH 'netstat -ano -p tcp | findstr LISTENING | findstr /C:":<口>"'   # loopback 绑定复核
python tools/mas_instance_lease.py check --idx <N>          # 实例租约（本机侧账）
$SSH 'powershell -NoProfile -Command "(Get-Process MAA -ErrorAction SilentlyContinue | Measure-Object).Count"'  # 在飞 MAA 数（侧车挂≠MAA 停！）
python tools/client_state_check.py --image <刚拉回的帧>      # 客户端状态（本机 OCR）；必要时身份帧证 mas_identity_trace.py

# 2) 干跑（不连设备，先验配置结构）
$SSH 'set "MAA_CONFIG_DIR=E:\MAA-CLI\drill\config" & "E:\MAA-CLI\bin\maa.exe" --batch run daily_official --dry-run -p idx19'

# 3) 真跑（需明确批准；一次一台一号；班次窗口外）
$SSH 'set "MAA_CONFIG_DIR=E:\MAA-CLI\drill\config" & "E:\MAA-CLI\bin\maa.exe" --batch run daily_official -p idx19 --log-file E:\MAA-CLI\logs\degrade-<日期>-<tag>.log'
#    rc = ssh 退出码（0/1）；Summary 打在 stdout；--no-summary 别加

# 4) 取证：Summary 原文 + rc + asst.log 尾部（TaskChainCompleted / TaskChainError）
# 5) 收尾：链尾 closedown=true 已自动关客户端；release 实例租约；台账手工记录（见 §3.7）
```

**单号替代形态**（只想跑一条链，不想维护 task 文件）：`maa.exe --batch startup Official -a 127.0.0.1:<口> -p idx19 --account-name <掩码>` 等预设子命令亦可；`--account-name` 与 GUI 同层，**无** `AccountSwitchEnabled`（不切号，见 §3.5-N4）。

### 3.4 需要的配置（与"对齐"的限度）

- **profile**（每实例一份）：`[connection] adb_path=<主机 adb 绝对路径>`、`address="127.0.0.1:<现查口>"`、`config="General"`；`[instance_options] touch_mode="MaaTouch"`、`kill_adb_on_exit=false`。**端口会漂**（idx≤31=16384+32·idx、idx≥32=16388+32·(idx−32) 只是常态推算）。
- **task**（链）：`client_type/startup/closedown/tasks[]`；预设子命令的默认参数可由 `config\overlays\<type>` 覆盖。**params 名/值不校验**（错只在运行期炸）——所以"干跑"只保结构，不保语义。
- ⚠️ **对齐限度（重要）**：drill 的 `daily_official.toml` 是**近似日常链**（StartUp+Fight(1-7,药0)+Recruit+Infrast+Mall+Award+CloseDown）。AUTO-MAS 对该号实际跑的链由 `ScriptConfig`（该号 Stage/任务开关）+ `gui.new.json` 模板决定（含剿灭/理智作战/活动关/药量等）。

  ⇒ 降级跑之前，按该号真实配置**手工对齐关键项**（关卡、药/石、次数、是否含剿灭），否则**不得**对外声称"该号已代理"。
- 登录态：不切号场景 = 用客户端当前登录号；多号实例必须先做身份帧证（防串号静默）。

### 3.5 会不会与 AUTO-MAS 冲突？（逐面）

**不会冲突的面（结构性隔离，已实装）**：maa-cli 不写 AUTO-MAS 任何数据（`ScriptConfig/QueueConfig/data/history/Tag`）；不占用 `E:\MAA-Multi\MAA_*` 生产目录（独立 core 副本）；配置/日志独立（`%APPDATA%\loong\maa`）；adb server 共享属正常形态（`kill_adb_on_exit=false` 不杀 server）。

**会冲突/会缺的面**：

- **N1 记账盲区**：旁路跑**不进** `Tag`（日常：已代理N次）/`history`/通知 ⇒ 巡检、统计、补跑判定全部看不见；且 AUTO-MAS 不知道这天已跑过——**恢复后按自己的账仍会再跑一遍（双跑，实打实消耗理智/资源）**。反之：maa-cli 跑的这轮也**不会**产生"假账"。
- **N2 实例双驱动（硬冲突）**：**REST 挂 ≠ MAA 停**——侧车死掉时，先前拉起的 MAA 进程会继续跑到收尾（孤儿态：不记账/不解锁/不续跑，runbook §2.5）。旁路开跑前必须现查 `Get-Process MAA` 与 `adb devices`，确认目标实例**没有任何驱动在跑**；同实例双驱动＝双输入互踩，两边都打残。
- **N3 班次窗口追发**：REST 若在班次窗口内恢复，`mas_dispatch.py` 会按幂等对账**追发**（宽限 30 分钟，`MAAORCH_DISPATCH_GRACE`）；旁路若在追发窗内跑同一号＝双跑+抢实例。⇒ 旁路尽量等「当日确认不再追发」再跑。
- **N4 切号语义不存在（静默）**：maa-cli 一进程一实例一号、**不切号**；`--account-name` 目标不在登录记录时**静默照跑当前号**（同源结论）。⇒ 多号实例必须身份帧证；「切号验证」类需求一律回 P1。
- （延伸）AUTO-MAS 的目录级 kill、脚本锁、Temp 快照还原**都不覆盖** maa-cli——反过来 maa-cli 也不受它们保护，全靠 §3.6 的人工纪律。

### 3.6 使用条件（红绿灯）与红线

- 🟢 **绿灯（可用旁路）**：主链路恢复不了；目标号是**停用号**（如 v/Kaori）或明确点名的号；实例租约 `check` 空闲 + 三查（VMM 进程/adb get-state/MAA 进程数）通过；**班次窗口外**（05:00–08:00 / 19:30–23:00 之外）且当日确认不再追发；有批准。
- 🟡 **黄灯（要更谨慎）**：活跃号点名叫跑（需身份帧证 + 跑完补记录 + 次日复盘时对账）。
- 🔴 **红线**：实例上有 MAA 在跑；追发窗内仍会发车；指向生产 `MAA_*` 目录/配置（fail-closed 禁止）；`install/update -f`、`cleanup log/debug`、`self update`（危险命令，禁）；把旁路结果并进 Tag/history 口径。
- 班次窗口内确需执行：按"孤儿化"风险对待——还会吞任务/抢 IO，能等就等（runbook 纪律）。

### 3.7 收尾与记录（补"漏账"）

1. `release` 实例租约（`python tools/mas_instance_lease.py release --owner <卡id>`）。
2. 记录到 **repo 侧旁路台账**（建议约定 `runtime/reports/degrade_runs.jsonl`：ts/账号/实例/task 文件 sha1/rc/Summary 摘要/asst.log 路径/批准人）＋知识库留痕（`D:\1-2\MAS-KB`）。
3. 明确对外口径：旁路结果**只算"已跑同族任务"**，不写进 `Tag`/统计；该号在 AUTO-MAS 账上仍是"未代理"（下一班可能再跑——预期行为，别当故障查）。

---

## 4. 网络与端口盘点

### 4.1 REST `:36163`（主机）

- 绑定 `0.0.0.0:36163`（pid 15124，09:50 快照）；本机（100.111.209.108）直连 **HTTP 404 in 0.09s**（无根路由，404 属正常；能回 HTTP 即=服务活着）；生产工具全走它（`MAAORCH_MAS_API`）。
- 结论：**本机访问 REST 无需隧道**（tailnet 直达）；「前门 19999 / 看板」同为本机侧服务。

### 4.2 adb 端口（主机）

- 实测快照：**binding = `127.0.0.1:<口>`（loopback only）**（netstat：16832/17184/5583/5037）；本机直连 `100.79.173.69:16832` → **Could not connect（rc=7，~2.1s）**。
- 端点会**漂/增减**：09:50 五台（16800/16832/17184/5581/5583）→ 09:56 三台（16832/17184/5583）。常态推算 `16384+32·idx`（idx≥32：`16388+32·(idx−32)`）**只作线索**；权威定位 = VMM 进程（`--comment MuMuPlayer-12.0-<idx>`）→ 监听口 → `adb connect` 验证。
- 结论：**本机→adb = 必须隧道**（直接不可达）；隧道可用但降级（下条）。

### 4.3 「本机经 SSH 隧道访问 adb」可行性 + 命令样例（**本卡未实际连**）

```
# 每个目标实例一条隧道；本地口别用远端同号（避开本机 MuMu 自己的端口）：
ssh -i ~/.ssh/maaorch_target -N -L 26832:127.0.0.1:16832 Administrator@100.79.173.69
# 本机侧消费（adb / maa-cli 均可）：
"D:/Xiach/MuMuPlayer/nx_main/adb.exe" connect 127.0.0.1:26832
MAA_CONFIG_DIR=<本机沙盒> maa-cli.exe --batch startup Official -a 127.0.0.1:26832 -p <本机侧 profile> --dry-run   # 先干跑
```

- **能成立的前提（已实测）**：adb 绑主机 loopback（§4.2）+ SSH:22 通 ⇒ `-L` 转发成立。
- **代价（引用 SURVEY §5.2 结论）**：MaaCore 快速截图通道 `exec-out "screencap | nc -w 3 10.0.2.2 <本机随机口>"` 中 `10.0.2.2` 指向**主机 loopback**，而监听在本机 ⇒ **该通道必失败**，退到 `exec-out screencap -p` 慢通道（本机基线 946ms/帧，跨网更慢）⇒ 识别变慢、不适合常态批量。
- 其他注意：一实例一隧道（N 台=N 条）；本机侧 adb server 会看到"远端设备"；触控（MaaTouch/adb pipe）跨隧道额外变慢（未实测）；**批量/生产取图请走 §4.4 两条无隧道通道**。

### 4.4 截图/取图的三条通道（本卡实测）

| 通道 | 路径 | 实测 | 适用 |
|---|---|---|---|
| SSH 执行主机 adb → 字节拉回 | `ssh HOST 'cmd /S /c "adb.exe -s … exec-out screencap -p"'` | 1.58MB / **1.36s**（当日 ~1.2MB/s；历史曾 8KB/s——波动大） | 生产取证主力；REST 也挂时唯一可用 |
| REST `/api/ocr/screenshot/adb` | 本机 POST（`adb_path`+`serial`）→ base64 PNG | **0.59s**、1280×720、code=200 | REST 活着时的快捷取证（无需 SSH） |
| 隧道（§4.3） | 本机 adb 直连 | 未连（本卡约束）；按 SURVEY 预判降级 | 偶发排查 |

---

## 5. 限制与未测项（诚实清单）

1. **隧道未端到端实测**（本卡约束"不实际连"）：截图退化程度、触控时延、稳定性均按 SURVEY §5.2 推演。
2. **旁路"日常链"未端到端真跑**：级别 3 只验过 `startup`；`daily_official.toml` 链的全链真跑未做（本卡只 dry-run）。首次真跑请按"一次一台/一号"小额试。
3. **"侧车挂而 MAA 在飞"**只有方法论（查进程数），没有现场演练记录。
4. **对齐限度**（§3.4）：旁路链 ≠ AUTO-MAS 对该号配置的链；手工对齐质量取决于执行人。
5. AUTO-MAS OCR 端点仅实测 `/api/ocr/screenshot/adb`；`check/click` 系列未测（`click` 是**写动作**，别拿来做只读判断）。
6. maa-cli 在同一 data 目录多进程时的 asst.log 混写**未验证**（DEPLOY §8 遗留）⇒ 旁路**串行**跑，不要并发。
7. 本机 PY312 的 OCR 以 1280×720/1600×900 帧为基线；其他分辨率未回归。
8. 本卡未覆盖：肉鸽/活动场景的旁路（复杂度与看守成本更高，结论仍是"回 P1"，见 `MAA_DRIVER_OPTIONS.md` §2.2/§2.3）。

---

## 附 A：本卡实测命令与输出

见 `docs/audits/2026-09-28_cross_host_drill.txt`（dry-run 两次 / 端口与直连探测 / 三条取图通道 / 记账面读取 / 本机资产核对）。

## 附 B：drill 沙盒残留与清理

- 主机新增（attempt 1 + 本卡复用）：`E:\MAA-CLI\drill\`（3 文件，即降级通道的演练配置）。
- 一键清理：`Remove-Item -Recurse -Force 'E:\MAA-CLI\drill'`（清掉后按 §3.2 重建：2 profile + 1 task）。
- 主机 `E:\MAA-CLI\logs\lv3-*`、`docs/audits/shots/*` 为 `MAA_CLI_DEPLOY.md`（t_9fc91afc）证据，勿随本卡清理。
- 本机仅 scratch 沙盒（`t31_drill`、2 PNG），无 repo 外残留。
