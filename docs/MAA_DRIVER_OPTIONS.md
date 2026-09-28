# MAA 驱动路径取舍：AUTO-MAS 注入 vs maa-cli vs MaaFw 绑定（推荐分工 + 接口设计）

> 卡：`t_cc935c9b`（板 `maa-parallel-benchmark-20260926`）｜落盘：2026-09-27 22:2x CST
> 性质：**只读分析**——未发车、未改任何工具/配置、未碰任何实例；本文只给结论与设计。
> 目标：三条可编程路径摆在一起，给出**推荐分工**，为「Hermes 调配肉鸽 / 活动」定接口。
> 输入材料：`docs/MAA_CLI_SURVEY.md`（t_8057ebaa，maa-cli 唯一实测面）、`docs/MAS_SCENARIO_DISPATCH.md`（受控场景发车草案）、
> `docs/MAS_RERUN_POLICY.md`（补跑规则）、`docs/MAA_EVENT_FARMING_PLAYBOOK.md`、`docs/MAA_ROGUELIKE_PLAYBOOK.md`、
> `docs/MAA_LOG_DICTIONARY.md`、`docs/MCP_TOOL_ROADMAP.md`、`docs/MULTI_GAME_VISION.md`、`docs/HERMES_MAS_HANDOVER.md`；
> 技能 `auto-mas-host-ops` / `maa-task-config` / `maa-roguelike-ops` / `maa-event-farming`。
> 并行卡：`t_9fc91afc`（maa-cli 部署与连通性验证 → `docs/MAA_CLI_DEPLOY.md`，**已交付**：主机实装 0.7.5+core 副本、1/2 级验证通过）——部署面归它，本卡只做取舍与接口。
> 版本口径：maa-cli 本机 0.7.5 / **主机侧已实装 0.7.5**（`E:\MAA-CLI\bin\maa.exe` + 独立 core 副本 `E:\MAA-CLI\core`；级别 3 真跑待批准）；MAA 6.18.0（生产 `MAA_1..12`）/ 6.17.5（肉鸽机队 fz-maa）；
> MaaFw（PyPI）5.9.2（本机 Python312）；AUTO-MAS（含 REST :36163）。

---

## 0. TL;DR（推荐结论）

1. **生产一律走「AUTO-MAS 注入」（P1），不迁**：日常批跑 / 活动编排 / 巡检补跑 / 生产槽肉鸽——这是唯一带
   记账（Tag·history）/ 通知 / 切号注入与闸门 / Temp 快照还原 / 巡检闭环的路径。
2. **maa-cli（P2）采纳为「第二驱动」，但限定用途**：① 本机零风险工具链（模板编制、convert/import、`--dry-run` 校验）；
   ② 主机侧**闲置实例**的低频单发实验（部署与连通性见 t_9fc91afc；真跑需批准）。
   明确**不用**于：日常批跑、账号级补跑、活动全队编排、切号验证——这些场景 maa-cli 会击穿记账与判据闭环（§4）。
3. **MaaFw 绑定（P3）＝ 感知/取证层 +（未来）非方舟适配**：截图取证是它的主场（FramePool 18 ms/帧，本机已用）；
   它是通用框架，不是方舟任务引擎——**不重造 MAA 的 869 任务语义**（§2.6、§4.2）。

**Hermes 选路速查**（一句话版）：

| 请求形态 | 走哪条 | 入口（现状/拟） |
|---|---|---|
| 「把今天日常/晚班盯好、没跑的补上」 | P1 | MCP `mas-dispatch`（list_schedule/dispatch_queue/switch_gate）+ `mas_rerun` + 巡检 job |
| 「按场景发一局肉鸽 / 一轮活动」 | P1 | `tools/mas_scenario_dispatch.py`（草案，双闸默认关）；肉鸽连刷=机队看守 |
| 「临时只跑一个号（无账实验/验证）」 | P2（受限） | 拟 `tools/maacli_run.py`（§3.2）+ 闲置实例（主机侧已实装） |
| 「看画面对不对 / 抓帧取证」 | P3 + adb | `client_state_check` / `mas_identity_trace` / `mas_preflight`（adb 链）；本机高频=`maafw_capture` |

---

## 1. 三条路径的事实面

### 1.1 P1 · AUTO-MAS 注入（现状主路径）

**是什么**：改 AUTO-MAS 侧数据 + REST 发车：「三写一车」——① `POST /api/scripts/user/update`（用户配置 `Task` 开关，10 键显式全写）；
② `data/<sid>/<uid｜Default>/ConfigFile/gui.new.json`（任务项 JSON 合并）；③ `POST /api/dispatch/start {"taskId":…, "mode":"AutoProxy"}`。
还原同理（到期写回 + 配置回传）。证据：`docs/MAS_SCENARIO_DISPATCH.md` §3（引 `AutoProxy.py` L273/L555/L563/L592–936）。

**已建成的工具矩阵**（全部已经/正在巡检与 cron 里跑）：

| 工具 | 作用 | 状态 |
|---|---|---|
| `tools/mas_dispatch.py` + `mas-dispatch` MCP | 班次发车/对账/门禁（白名单/单飞/REST 可达/并发锁/冲突窗）/告警台账 | 生产在用（2026-09-27 起司机） |
| `tools/mas_scenario_dispatch.py` | 「按场景发一局/一轮」受控发车（肉鸽/活动/剿灭；G1–G7 闸门、备份+还原、审计） | 草案，双闸默认关，apply 未实跑 |
| `tools/auto_mas_rerun.py` + `tools/mas_catchup_check.py` | 临时补跑队列（≤8 号/批、同目录同队、跑完清理）+ 只读候选判定 | 生产在用 |
| `tools/mas_preflight.py` | 班前 GO/NO-GO（REST/槽位/实例/残留模态/注入闸门/磁盘） | 已接巡检/接线方案在案 |
| `tools/mas_switch_gate.py` | 切号注入闸门（官 masked+true / 非官 empty+false，fail-closed） | 已接 30 分钟巡检 |
| `tools/mas_identity_trace.py` / `tools/client_state_check.py` | 每轮身份留痕（帧证）、客户端状态、现场取证 | CLI 在跑（MCP 化在 `MCP_TOOL_ROADMAP` 计划中，未接） |

**强项**：记账（`Tag`/`history`/`LastProxyDate`）、通知、队列错峰与脚本级锁、切号注入策略（v2 补丁 `AutoProxy.py` L766–779）、
轮末 Temp 快照还原、脚本配置锁定语义、以及围绕它的整套巡检/补跑/统计闭环。

**短板**（本卡评估口径）：写面受 ConfigFile 限制（UI 未暴露的开关改不了；`AccountName/AccountSwitchEnabled` 是禁区，见 §4.3）；
改配置受 Temp 还原影响（轮末值≠最后跑的号）；改 AUTO-MAS 代码需重启侧车（班次窗口禁重启，否则孤儿化）；
**无单用户入口**——AutoProxy 以 scriptId 发车会跑该脚本**全部启用用户**（只跑一个号须另建单号脚本或停用其余号）。

### 1.2 P2 · maa-cli（无头 MaaCore 驱动器；本机 0.7.5 / 主机侧已实装，见 t_9fc91afc）

**是什么**：跨平台命令行版 MAA。`maa startup/fight/roguelike/…/run <任务>`；三层配置
（`cli.toml` + `profiles/<名>.toml` + `tasks/<名>.toml`）；退出码可编程；`--batch` 非交互；`--dry-run` 离线校验链。
全部实测面见 `docs/MAA_CLI_SURVEY.md`（391 行），要点：

- **能力面完整**：`fight`（药/石/次数/连战/掉落即停）、`roguelike <主题>`（模式/分队/开局干员/投资/烧水/种子，
  参数面=MaaCore Roguelike 完整映射）、`startup --account-name`、`copilot`、`convert`/`import`（模板库/分发渠道）。
- **资源复用**：`data\lib` + `data\resource` 放链接即成（本机 symlink 已实证：`maa version` 报 MaaCore v6.18.0）；
  主机侧建议 junction，但**有写穿风险**（`install/update -f` 会写进被链接的生产目录）。
- **设备**：`address`=adb serial；远端模拟器**本机直连被实测否决**（100.79.173.69 的 16384/16416/16800/16832 全
  ConnectionRefused）⇒ maa-cli **要在主机上跑**（loopback）或走 SSH 隧道（截图通道退化，不推荐常态）。
- **账号**：`--account-name`=MaaCore `StartUp.account_name`（与 GUI 同层）；**GUI 的 `AccountSwitchEnabled` 无对应物**——
  maa-cli 场景天然「一进程一实例一号，不切号」。
- **输出/判据**：asst.log 位置/格式与 GUI 队完全一致（本仓字典 asst 段可直接复用）；**无 gui.log**
  （`账号切换 -->>`、`任务已全部完成！`、`任务出错: <链名>` 等一律没有）；退出码只有 0/1，无细分。
- **已知坑**：params 名/值**不校验**（错在运行期才炸）；`--log-file` 必须绝对路径（默认名含冒号，Windows 非法）；
  `startup` 的 client 是位置参数（`--client-type` 形式会 usage 错，`fight` 反之）；客户端类型/资源不对的典型表征
  =「识别全不中 + 零点击」；`cleanup` 会删日志、`install -f` 有写穿风险（红线动作）。

### 1.3 P3 · MaaFw 绑定（本机 MaaFramework 5.9.2 + 本仓已落地三件套）

**是什么**：MaaFramework（MaaXYZ，LGPL-3.0）的 Python 绑定（PyPI `MaaFw` 5.9.2 / 本机 Python312 site-packages：
`maafw-5.9.2` + `maa`(import 名) + `MaaAgentBinary`）。本仓已落地：

| 工具 | 能力 | 实测 |
|---|---|---|
| `tools/maafw_capture.py` | Win32Controller + **FramePool** 取帧（顶层窗口，裁 chrome → 1920×1080） | **18 ms/帧 ≈ 55 fps**；`PostMessage` 且从不调输入接口 ⇒ 与 MAA 通道完全隔离 |
| `tools/maa_frame.py` | ctypes 直调 MaaCore：`AsstAsyncScreencap` + `AsstGetImageBgr`（强制新鲜帧） | 25–45 ms/帧；专治绑定 `get_image()` 拿缓存帧的老坑 |
| `tools/ce6_attempt.py`（+annotate/battle_sampler 等） | 一局闭环：导航→模板校验→MaaFwAdb 触控→录像→证据报告 | CE-6 已打通（历史交付） |

**本质与边界**：MaaFramework 是**通用**图像识别自动化框架（生态：M9A / MaaEnd / MFAAvalonia…），
**方舟任务语义不在其内**（那是 MAA 本体的 869 任务链）；`services/ai_agent/` 可按 Agent 协议重包装为
Custom Recognition/Action（`docs/MULTI_GAME_VISION.md` §4）。

- **强项**：控制粒度最强（自建 pipeline/自定义识别）、高频取帧不走 adb、Win32 输入通道可选。
- **短板**：要自建任务语义；Win32 窗口 = **必须与模拟器同机**（本机绑定 ≠ 远端可用；主机侧部署未评估）；
  非方舟适配属多游戏主线（`MULTI_GAME_VISION`），不在本卡「调配肉鸽/活动」的射程内。

---

## 2. 六场景对照表（推荐 + 理由）

| # | 场景 | 推荐 | 备选（受限） | 一句话理由 |
|---|---|---|---|---|
| 1 | 日常批跑（54 号 / 12 槽 / 两班） | **P1** | 无 | 记账/通知/锁/切号/还原/巡检闭环全在 AUTO-MAS 面，无可替代 |
| 2 | 肉鸽 | **P1（机队直发+看守）** | P2 实验室（本机/闲置实例单发实验） | 让位闸+看守形态处置是既有资产；maa-cli 无看守、无 gui.log 判据 |
| 3 | 活动刷取 | **P1（计划表 / IfActivityFirst / DepotMaintain）** | P2（单实例实验，`OnSideStory` 变体） | 活动关名解析/药额度/插入位置全在 AutoProxy；不拆全队计划表体系 |
| 4 | 临时单号 | **P2（受限）**；有账语义时回 P1 | P1（临时队列 / scriptId 发车） | maa-cli 天然一进程一实例一号、无配置手术；但**只在无账场景** |
| 5 | 巡检补跑 | **P1** | 无 | 判据源/上限/窗口/闭环全建在 history·Tag 上；maa-cli 发车不产生记账 |
| 6 | 截图取证 | **P3 + 既有 adb 链路** | — | FramePool 18 ms/帧是取证主场；**maa-cli 没有截图能力**（survey §9.7） |

### 2.1 日常批跑 → P1（唯一）

现状即最优：班次发车已经是 Hermes 驱动（cron `2600f6415f16` → `mas_dispatch.py --reconcile --apply`），
门禁/告警/对账/回滚完备；用户复盘、统计卡、巡检全部消费 AUTO-MAS 的 Tag/history。
maa-cli 要接这条得自建：号表-实例映射、错峰、通知、记账、切号注入、失败重试与告警——**全部已有件的重造**；MaaFw 更远（任务语义）。
⇒ 结论：**不迁**；maa-cli 只在本机做「不碰设备的工具链」（§5 Phase 0）。

### 2.2 肉鸽 → P1（机队路线 A/看守为主；生产槽路线 B 为辅）；P2 仅实验室

- **生产连刷** = 机队直发：`E:\maa-alas\fz-maa\l\*` 五台（l-1/l-2/l-4/l-5/l-7，MAA **6.17.5**），
  发车 `MAAOrch-StartOne-<idx>`（值：25/28/31/34/52），看守 `MAAOrch-RogueFleet`（每 2 分钟）——
  形态判据（假完成/卡死/掉线/冻结）与「产线空闲 ≥6 分钟 + 让位区（04:00–05:33 / 18:30–20:30）不发车」是**肉鸽最大风险面**，
  恰恰是机队看守的既有资产。
- **借生产槽跑一发** = 路线 B（AutoProxy + `IfRoguelike`，全队现状 false）；受控入口是 `mas_scenario_dispatch.py --scenario 肉鸽`
  （草案：写 ConfigFile 的 Roguelike 条目 + 开关，到期还原；⚠️ 6.17.5 模板 vs 6.18.0 目标有 `CoreChar`/`StartingOpers` 版本差，G2 会 WARN）。
- **maa-cli 的落点**：`maa roguelike <主题> --starts-count N` 参数面完整、无头、退出码可编程——适合**实验位**：
  本机（已有 0.7.5+资源）或主机**闲置实例**（部署已就绪：候选停用机 官-25/idx37（profile `kaori-official`）、
  官-5/idx19（`v-official`），见 `MAA_CLI_DEPLOY.md` §6）。
  **不对生产机队/生产槽承诺**：没有看守、没有 gui.log 判据（「已开始探索 N 次」是 gui.log 行）、6.17.5↔6.18 字段漂移。
- 红线照旧：**同一模拟器绝不允许两个 MAA**（肉鸽机与产线共用模拟器）；发一局/连刷≠可以绕过让位闸。

### 2.3 活动刷取 → P1（三条路径）；P2 只做单实例实验

现状三条路径（按顺位，见 `MAA_EVENT_FARMING_PLAYBOOK.md`）：① 计划表 `Stage`=活动关码（全队现状用法）；
② `IfActivityFirst=true`+`ActivityStageIndex=N`（生成独立「活动关优先」任务，独立药额度）；③ `DepotMaintain` 缺口补。
三者全部依赖 AUTO-MAS 的活动关名解析（`Data.Stage`←`StageActivityV2.json`）、药额度体系（活动/理智/临期三套）、
以及「插在 StartUp 之后、理智作战之前」的链位置——**在 AutoProxy 里**。

maa-cli 的 `OnSideStory` 条件变体（活动期自动刷当期关）是**单实例自动化**的可行替代，但用于全队等于：
逐实例模板 + 绕开计划表/药额度/通知/记账 + 与 AUTO-MAS 同目录互斥——**不进生产**。
⇒ 活动编排/收尾（活动结束把 `Stage` 改回常驻关，SR 教训：46 号白挂）继续 P1；P2 的价值只在「拿一台闲置实例试参数」。

### 2.4 临时单号 → P2（受限）为主推；需要记账时回 P1

**痛点**：P1 没有单用户入口——`POST /api/dispatch/start {taskId:<scriptId>}` 跑该脚本**全部启用用户**；
只跑一个号须另建/改用单号脚本或先停用其余号（另行批准）。当前替代品（临时补跑队列、scenario dispatch）都是为
「场景×目录」设计的，单号 = 高射炮打蚊子。

**maa-cli 是「临时单号」的天然形态**：一进程一实例一号、无队列、无配置手术、退出码可编程；本机 0.7.5 已实证可跑
（2026-09-20 CE-6 一局 2m31s，产物=asst.log+掉落帧）。**但只用于「无账语义」的临时跑**：

- ✅ 适用：登录验证、单号自查、参数实验、对某实例做「一轮 1-7 / 一局肉鸽」的隔离验证。
- ❌ 不适用（回 P1）：补跑闭环、需要进复盘统计、需要通知/告警、需要切号语义。
- 前置（详见 §3.2 门禁）：闲置实例（独占模拟器）＋班次外窗口＋ledger 留痕＋身份帧证（防串号）；主机侧部署已就绪（t_9fc91afc）。

### 2.5 巡检补跑 → P1（唯一）

补跑的判定与执行已经标准化（`docs/MAS_RERUN_POLICY.md`：P0–P3 前置体检 / N1–N4 不该补 / Y1–Y3 该补 / 上限 N=2 / 守卫窗 05:00–05:40、19:20–20:20），
判据源（history 逐条、Tag、重启 banner、队列进度）、执行器（`auto_mas_rerun` 临时队列）、闭环复核（该号下一条 history）
**全部围绕 AUTO-MAS 面**。maa-cli 发车**不产生** history/Tag ⇒ 巡检看不见、统计失真、闭环断裂、额度失控。
⇒ **补跑禁用 maa-cli**；同理禁用 MaaFw。

### 2.6 截图取证 → P3（MaaFw 绑定）+ 既有 adb 链路

三档取证，按场景选：

| 档 | 通道 | 速度 | 适用 |
|---|---|---|---|
| adb screencap（现状） | 主机侧 `adb exec-out screencap -p`（经 ssh）；preflight/client_state_check/identity_trace/rogue watch 都用它 | 250–380 ms/帧（跨机不更快） | **生产取证主力**：模态判定（OCR）、身份帧、on-call 现场图 |
| MaaFw FramePool | 本机 Win32 顶层窗口（`maafw_capture.py`） | **18 ms/帧 ≈ 55fps** | 本机高频感知、标注、采样、CLI 实验位取帧 |
| MaaCore BGR（`maa_frame.py`） | 本机 ctypes（`AsstAsyncScreencap`） | 25–45 ms | 需要「主动新鲜帧」的复核（绑定缓存帧不可信） |

- **maa-cli 明确排除**：无截图子命令（survey §9.7）。
- MaaFw 的天花板：Win32 窗口 ⇒ 与模拟器同机；**主机侧目前没有 MaaFw/Python 环境评估**（AUTO-MAS 自带 python 侧车，依赖未知）——
  要上主机需另评估（不属本卡）。
- 本机 #0 日服水月号 / 实例 #2 官服是既有的 maa-cli 实验位；**别碰正在跑 MAA 的窗口**（FramePool 取帧本身不发输入，安全；但选错窗口会抓错实例）。

---

## 3. 接口设计（若走 maa-cli：Hermes 需要哪些封装）

> 设计原则与既有件同构：**默认不跑（dry-run）＋双闸闭合（`--apply` ＋环境变量）＋一切留痕＋可回滚＋fail-closed**；
> 参考样式：`mas_scenario_dispatch.py`（G1–G7）、`mas_dispatch.py`（告警/锁/台账）。
> 本设计的执行体在**主机侧**（模拟器 loopback）；本地侧负责编排、拉回日志/Summary、落 ledger。

### 3.1 模板层：任务模板 → CLI 参数（映射表）

模板根：`runtime/templates/maacli/{profiles,tasks}/`（profile=实例连接；task=任务链）。
**优先模板文件**（可哈希、可审、可 import 分发）而非长参数链；参数名/取值一律以 0.7.5 `--help` + `--dry-run` 核（params 不校验）。

| 请求（Hermes 语义） | 模板 | 落成 CLI（主机侧执行） |
|---|---|---|
| 发一局肉鸽（Sarkaz 实验） | `tasks/roguelike_sarkaz_1.toml`：`[[tasks]] type="Roguelike" params={ theme="Sarkaz", mode=0, starts_count=1, … }`（+`startup=true`） | `maa --batch run roguelike_sarkaz_1 -p <实例profile>` |
| 连刷 N 局（仅限有看守的场景，否则不建议） | 同上 `starts_count=N` | 同上 |
| 单实例活动关 N 次 | `tasks/fight_event.toml`：`params={ stage="<活动码>", times=N, medicine=0 }`（或 `OnSideStory` 变体） | `maa --batch run fight_event -p <实例profile>` |
| 单号登录验证 | `tasks/startup_official.toml`：`startup=true`，`client_type="Official"` | `maa --batch run startup_official -p <实例profile>`（或 `maa startup Official --account-name <掩码>`，同 GUI 语义） |
| 抄作业（单实例） | `tasks/copilot_<关>.toml` | `maa copilot <uri>` / `maa --batch run …` |

`profiles/<实例>.toml`（每实例一份）：`[connection] adb_path=<主机 adb 绝对路径> / address="127.0.0.1:<现查端口>"`、
`[instance_options] touch_mode="MaaTouch"`（抄作业前提）、`kill_adb_on_exit=false`。
**端口现查现写**（MuMu 端口会漂；idx≤31=16384+32·idx、idx≥32=16388+32·(idx−32) 只是推算——执行前用 VMM 进程定位链复核）。

### 3.2 执行层：`tools/maacli_run.py`（拟建）门禁契约

```
python tools/maacli_run.py run --task <名> --profile <实例> [--apply] [--json] [--timeout 3600]
python tools/maacli_run.py preflight --profile <实例>          # 只读
python tools/maacli_run.py ledger --tail 20                    # 只读
python tools/maacli_run.py reconcile                           # 收尾/清理未闭合 run（默认 dry）
```

门禁（**真跑前必须全过**，任一不过即拒绝、零主机写请求；对齐 mas_scenario_dispatch 风格）：

| # | 闸门 | 判据 |
|---|---|---|
| G0 | 硬闸 | `MAAORCH_MAACLI_NO_GO=1` 一票否决一切真跑 |
| G1 | 双闸 | `--apply` ＋ `MAAORCH_MAACLI_APPLY=1`（缺一即拒） |
| G2 | 实例独占 | 该模拟器/该 MAA 目录**无任何 MAA 在跑**（Win32_Process 复核），VMM 进程在位，adb get-state 可连；与 `mas_dispatch` 冲突窗（同目录/同实例，HOLD_MIN）无重叠 |
| G3 | 目录隔离 | 目标 core/资源 = **独立副本**（如 `E:\MAA-CLI-CORE`）；**不得指向生产 `E:\MAA-Multi\MAA_*`**（fail-closed） |
| G4 | 窗口 | 班次窗口外 ＋ 让位区（04:00–05:33 / 18:30–20:30）外 ＋ 产线空闲 ≥6 分钟；同一实例单飞 |
| G5 | 现场 | `client_state_check` = `正常:*`（模态先处置）；身份帧证可选项（多号实例必做） |
| G6 | 单飞/额度 | `runtime/maacli_state.json`：同实例同任务同日一次；失败重试 ≤1 |

运行环境隔离（**现部署 t_9fc91afc 用默认 `%APPDATA%\loong\maa`（Administrator 户），已与生产隔离**；包装器化时建议改为按 run 的沙盒，`MAA_CONFIG_DIR` 必须 Windows 原生路径）：

```
MAA_CONFIG_DIR=runtime/maacli/config     # profiles/tasks（模板同步进来）
MAA_STATE_DIR =runtime/maacli/state      # asst.log 跟着搬（<state>/debug/asst.log）
MAA_CACHE_DIR =runtime/maacli/cache
maa --batch --log-file <绝对路径> run …   # --log-file 必须绝对路径（老坑）
```

超时收割：`--timeout` 到点杀 MaaCore 进程组（按目录路径定位，只杀自己的副本），留痕 `verdict=timeout`。

### 3.3 判读层：结果 → 判据字典（gui.log 不可用，用替代表）

maa-cli 下**只有** asst.log + 退出码 + stdout Summary 三件套；`docs/MAA_LOG_DICTIONARY.md` 中来源为 gui.log 的条目**一律不可用**。

| 想问 | maa-cli 信号 | 备注 |
|---|---|---|
| 这轮成功了吗 | 退出码 0 ＋ Summary 全 Completed | Summary 默认有；别加 `--no-summary` |
| 哪条链失败 | 非 0 退出 ＋ `Some error occurred during running task!` ＋ asst.log `TaskChainError {"taskchain":…}` | 与 GUI 队同源回调；`mas_error_triage` 现成在读 |
| 连接失败 | asst `ConnectionInfo … "what":"ConnectFailed"` ＋ 非 0 | 对 gui.log「连接失败/重连失败」的替代 |
| 切号了吗 | asst `AccountSwitchTask::*` / `SwitchAccount@*` | **无** `账号切换 -->>`；切号失败静默的既有结论不变 |
| 没登进游戏 | StartUp 链节点推进停住＋超时 | 无「任务出错: 开始唤醒」中文链名行 |
| 吃了多少药 | asst `MedicineCounterTaskPlugin` ＋ `append_task Fight {…}` | 对 gui.log「药: N, 库存: M」的替代 |
| 肉鸽在刷 | asst `cur_task: <Theme>@Roguelike@…` 在滚 ＋ `TaskChainCompleted/Error` | 「已开始探索 N 次」是 gui.log 行——maa-cli 下需从 Summary/asst 重新定标（**未实测**，试点时补） |

落地建议：新判据并进 `docs/MAA_LOG_DICTIONARY.md` 增补「maa-cli 段」（属后续卡，不在本卡）。

### 3.4 审计层：ledger（`runtime/maacli_ledger.jsonl`，append-only，一行一跑）

```json
{"ts":"2026-09-27T22:40:00+08:00","run_id":"maacli_20260927_2240_1","kind":"maacli_bypass",
 "scenario":"roguelike_1run","task_file":"runtime/templates/maacli/tasks/roguelike_sarkaz_1.toml",
 "task_file_sha1":"…","profile":"host-mumu13","target":{"host":"100.79.173.69","serial":"127.0.0.1:16800","mmu_idx":13,"maa_dir":null},
 "account_expected":"<掩码|null>","exit_code":0,"verdict":"ok","evidence":{"asst_log":"runtime/maacli/runs/<id>/asst.log#L120-L145","summary_sha1":"…"},
 "gates":{"g0":true,"g1":true,"g2":"idle","g3":"E:\\\\MAA-CLI-CORE","g4":"22:40 OK","g5":"正常:大厅","g6":"first"},"operator":"mas"}
```

- 产物目录：`runtime/maacli/runs/<run_id>/`（summary.json、asst.log 副本、cli.log、可选 shot.png）。
- 脱敏沿用 `scrub()` 口径（password/token/secret/cookie/credential/skland 一律剔除）；日志归档按敏感数据处理（gui.log/asst.log 可能含名册片段）。
- **与 AUTO-MAS 账隔离**：`kind=maacli_bypass` **不计入**任何「日常完成」口径（Tag/history 才是事实）；巡检只把它当
  「旁路审计」读，不得据此判补跑/完成。版本指纹（`resource\version.json` + `MaaCore.dll` 哈希）每次跑前记录，供漂移审计。

### 3.5 触发面与分期

- **Phase 2 内只给 CLI**（人工/巡检调用，`--json` 机读）；**不接 cron、不接 MCP**。
- 稳定后（Phase 3 决策）考虑 MCP 化：新 server `mas-cli` 或并入规划中的 `mas-observe`（见 `docs/MCP_TOOL_ROADMAP.md` §3），
  工具面建议仅 2–3 个只读+受控写（`maacli_ledger` / `maacli_run(dry_run=True 默认)`）。
- 与 Hermes 现有面（21 工具）叠加时注意网关缓存：改完 toolset 必须 `hermes gateway restart`（`HERMES_MAS_HANDOVER.md` §12.1）。

---

## 4. 风险（点名四类 + 附）

### 4.1 与 AUTO-MAS 抢实例（最高危）

- **机制**：AUTO-MAS 换用户/重试的 kill **按目录路径执行**——会杀掉同目录在跑的任何 MAA；共享 MAA 目录的两脚本同窗=互杀（实测）。
  一个模拟器跑两个 MAA（GUI 的 + maa-cli 的）则是直接把两边都打残。而且 maa-cli 的 MaaCore 会自行 `adb connect`，
  对「这台机器还有别的驱动」零感知。
- **现状防线（P1 侧）**：`mas_dispatch` 冲突窗（目录+script+实例）、`auto_mas_rerun.busy_paths`（目录忙跳过）、
  `mas_preflight` 槽位空闲项、肉鸽让位闸/让位区。
- **P2 缺口**：上述防线一个都没有 ⇒ 必须由 §3.2 的 G2–G4 补齐（只跑闲置实例+班次外+产线空闲），**或干脆只在本机跑**。
- 结论：**maa-cli 永不指向 `E:\MAA-Multi\MAA_*` 生产目录**（G3 fail-closed）；真跑一次性实例、一次一台。

### 4.2 资源版本漂移

- junction/符号链接复用 = 与 GUI 侧**共享** MaaCore.dll + resource：GUI 侧自更新 → maa-cli 跟着变；
  maa-cli `install/update -f` → 写穿生产目录（survey §4.3 红线）。
- 主机实况：`E:\MAA-Multi\MAA_6\MaaCore.dll` 与本机同款（v6.18.0，5,902,336 B/09-23；部署卡复核哈希一致）；
  **主机根目录另一份 dll 是旧版（08-18）**——链接对象别拿错。
- 部署实况（t_9fc91afc，已实装）：**独立副本** `E:\MAA-CLI\core`（复制自 MAA_6：10,107 文件/529 MB，dll sha `EFF5C522…`）；
  junction 只挂在本 CLI 自己的 `%APPDATA%\loong\maa\data\{lib,resource}`——与生产解耦成立，`install/update -f` 已无写穿目标。
- 版本差：机队 6.17.5 vs 生产 6.18.0（肉鸽开局干员 `CoreChar` vs `StartingOpers` 字段差，写错=不生效/静默丢）。
- maa-cli 每次跑任务都会执行 hot-update（`resource::update` + `hot_update::update`）——会动热更文件与缓存。
- 对策：**独立副本**（copy 自 `MAA_6` 或根目录新版）+ 禁用 `install/update -f` + 每次跑前记录
  `resource\version.json`/dll 哈希进 ledger；**版本以 dll 构建为准，别信目录名**。

### 4.3 账号切换语义不一致（静默危险）

- maa-cli `--account-name` = `StartUp.account_name`（同 GUI 同层），但 **无 `AccountSwitchEnabled`**；
  「一进程一实例一号」不切号。切号失败静默（目标掩码不在登录记录 ⇒ 在当前号上照常跑完）**同源成立**。
- 判据面：gui.log 的权威判据（`账号切换 -->>`）不存在；`mas_switch_gate` 读的是生产 gui.new.json 的注入值，
  **对 maa-cli 运行无意义**。⇒ maa-cli 的运行**不能**用于「切号是否正常」的任何验证/加固。
- 对策：① maa-cli 只对「已知当前登录号」的实例跑；② 多号实例跑前做**身份帧证**（大厅/连接页 `ID:<9位>` OCR，
  `mas_identity_trace.py ocr` 现成）；③ ledger 记录 `account_expected` 与帧证哈希；④ 任何「这个号切没切对」的问题回 P1 口径。

### 4.4 日志双份

- asst.log 同格式但**随 MAA_STATE_DIR 走**：不隔离 → 写进共享目录与 GUI 日志混流；隔离 → 巡检脚本（固定读
  `E:\MAA-Multi\MAA_7\debug\asst.log` 一类路径）看不到 maa-cli 运行；两次迁移后同一时间窗两处 asst.log。
- maa-cli 自身日志：stderr（默认）/`--log-file`（默认名含 `HH:MM:SS` 冒号 ⇒ Windows 非法，**必须绝对路径**）。
- `maa cleanup` 会删 log/debug——记录机别乱跑（红线动作）。
- 对策：**每 run 独立 state 目录**（`runtime/maacli/state/<run_id>`，asst.log 跟着搬）+ ledger 记日志绝对路径 +
  巡检字典按 run_id 定位；两条链的日志永不混读。另：**同 data 目录多进程 asst.log 是否互踩未验证**（部署卡 §5 缺口 1）——
  并行跑前先做 2 进程小样（这条对「并行基准」卡直接适用）。

### 4.5 附：其它已识别风险

| 风险 | 说明 | 对策 |
|---|---|---|
| 记账/通知空白 | maa-cli 运行不进 Tag/history/通知；看板、统计、复盘全盲 | 明确旁路口径（§3.4）；需要进账的场景回 P1 |
| 主机 CLI 旧 + 网络（已处理） | 原 0.5.9 无 core；GitHub 可达性曾未测 | **部署卡已完成**（0.7.5 + 独立副本；主机 GitHub 可达 ✅；winget 源索引仍停在 0.5.9，回归 winget 管理需先修源） |
| 参数不校验、退出码粗 | `params` 错在运行期才炸；退出码仅 0/1 | 模板干跑（dry-run）+ asst.log 定因；试跑先 1 局小额 |
| 危险动作 | `cleanup`（删日志）、`install/update -f`（写穿）、`self update`（WinGet 装无效） | 禁用清单入 wrapper（G3 + 命令白名单） |
| 单点 | Hermes 侧 cron/MCP 挂 = 没人发车（既有结论，与路径无关） | 维持 30 分钟巡检「只发现不补救」的现状 |

---

## 5. 迁移 / 共存建议（分阶段）

**总原则：P1 不动；P2 旁路增量试点，成功前不影响任何生产面；P3 保持感知/取证定位。**

- **Phase 0（现在即可，零生产风险）**：本机 maa-cli 当「工具链」——模板编制、`convert/import`、`--dry-run` 校验、
  实验位（本机 #2 官服）参数试玩（部署卡同结论：本机保留做这两件事）。
- **Phase 1（部署，已实装 ✅ t_9fc91afc）**：`E:\MAA-CLI\bin\maa.exe` 0.7.5 + `E:\MAA-CLI\core`（独立副本，与本卡倾向一致）+
  junction（CLI 自有 appdata）；1/2 级验证通过（adb devices 29 台 online；idx3 截图 OCR=大厅/ID 核对；主机 GitHub 可达 ✅）。
  禁用 `install/update -f`（现无写穿目标）。
- **Phase 2（受控真跑，需批准）**：闲置实例单发实验——候选 A 官-25/idx37（`kaori-official`，adb `127.0.0.1:16548`）/
  B 官-5/idx19（`v-official`，adb `127.0.0.1:16992`）；runbook 见 `MAA_CLI_DEPLOY.md` §6（批准方式=卡评论「批准级别3，候选 A/B/C」）；
  窗口=班次外+让位区外；执行=§3.2 门禁 + §3.4 ledger；**一次一台**；先 `startup`，`fight 1-7 -n 1` 留作第二级。
- **Phase 3（决策点）**：是否 MCP/cron 化。准入 = Phase 2 数据（成功率、判据完备性、互斥事故 0、目录/账互不干扰）；
  不过就不接。**不迁清单**（无论哪期）：日常批跑、账号级补跑、活动全队编排、切号验证、肉鸽连刷看守——全部留在 P1。
- **回滚**：主机删 `E:\MAA-CLI` + `%APPDATA%\loong\maa`（三条命令见 `MAA_CLI_DEPLOY.md` §7）；撤 `runtime/templates/maacli/`
  与 wrapper 文件。全程不触碰生产目录 ⇒ 回滚零影响。

---

## 6. 待拍板清单

1. **Phase 2 真跑批准**：部署 runbook 已备（候选 A/B，先只做 `startup`，`fight 1-7 -n 1` 作第二级）；
   在 t_9fc91afc 卡评论「批准级别3，候选 A/B/C」即可解锁。
2. **core/资源形态（已定 ✅）**：独立副本 + CLI 自有 appdata junction（部署卡实装 529 MB）；
   遗留：winget 源索引旧（0.5.9）——是否修源回归 winget 管理待定。
3. **用途边界生效**：确认「maa-cli=无账旁路（实验/验证）」口径；越界（进批跑/补跑/切号）需谁批、怎么拦（环境硬闸）。
4. **接口落位时机**：`tools/maacli_run.py`（§3.2）与判据字典 maa-cli 段、ledger 的落地顺序（建议：Phase 2 之前先落最小版；
   MCP 化放到 Phase 3 之后）。
5. **肉鸽模板版本差**：借生产槽发肉鸽（路线 B）前，先把 `runtime/templates/maa/roguelike.json` 从 6.17.5 形态升到
   6.18.0（`StartingOpers`），否则 G2 会 WARN（防静默丢核心干员）。

---

## 附 A · 证据索引（全部只读可复现）

| 用途 | 出处 |
|---|---|
| maa-cli 能力/schema/复用/设备/账号/判据 | `docs/MAA_CLI_SURVEY.md`（t_8057ebaa；命令与实测输出见其附录 A/B） |
| AUTO-MAS 注入三写一车 / 闸门 / 还原 | `docs/MAS_SCENARIO_DISPATCH.md`；`tools/mas_scenario_dispatch.py`（`--selftest` 44/44） |
| 发车/告警/锁/回滚 | `tools/mas_dispatch.py`（头注）；cron `2600f6415f16` |
| 补跑规则（P/N/Y、窗口、上限） | `docs/MAS_RERUN_POLICY.md`；`tools/auto_mas_rerun.py`；`tools/mas_catchup_check.py` |
| 肉鸽机队（让位、看守、判据、schtasks 映射） | `docs/MAA_ROGUELIKE_PLAYBOOK.md`；`docs/ROGUE_WATCH.md`；`tools/rogue_fleet_watch.mjs` |
| 活动三路径（计划表/IfActivityFirst/DepotMaintain） | `docs/MAA_EVENT_FARMING_PLAYBOOK.md` |
| 日志判据（gui/asst 全标记） | `docs/MAA_LOG_DICTIONARY.md`（asst 段对 maa-cli 全部适用；gui 段不可用） |
| MCP 工具面现状/规划 | `docs/MCP_TOOL_ROADMAP.md`（21 工具现状 + `mas-observe` 草案） |
| MaaFw 定位/多游戏 | `docs/MULTI_GAME_VISION.md`；`docs/MAA_ECOSYSTEM.md`；`tools/maafw_capture.py`（18ms 实测注释） |
| 本机 maa-cli 真机先例（CE-6）与五个坑 | `docs/REF_MAA_CLI_SKILL.md`（2026-09-20） |
| 并行部署卡 | `t_9fc91afc` → `docs/MAA_CLI_DEPLOY.md`（装哪/资源/连通性/回滚） |

本卡实测面：仅**读**（文档/工具头注/本机 site-packages 版本核对）；未发车、未 ssh 写、未改任何配置或工具。

## 附 B · 变更记录

- 2026-09-27 22:2x：首版（结论 + 六场景对照 + maa-cli 接口四层设计 + 四类风险点名 + 分阶段迁移/共存 + 待拍板）。
- 2026-09-27 22:3x：与并行卡 `t_9fc91afc`（`docs/MAA_CLI_DEPLOY.md`）交叉对齐——主机实装结论、候选 profile/端口、回滚命令同步。
