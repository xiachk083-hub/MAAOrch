# MAA 日志判据字典（gui.log / asst.log 全标记）

> v1 · 2026-09-27 · 产出任务 `t_b26c519d`（只读产出，未改动任何生产配置）
> 适用版本：**MAA v6.18.0**（2026-09-24 构建；少量 v6.16.8 样本已单独标注）
> 样本来源：`E:\MAA-Multi\MAA_1 .. MAA_12\debug\` 的 `gui.log` ×12（2026-08-28 ~ 09-27，实测 165,861 行）+ `asst.log` ×3（MAA_3/6/12，约 100 MB），全部为实机日志。
> 目的：**任何"看日志下结论"的判断（巡检 / 定因 / 补跑 / 身份追踪 / 切号闸门）都以本表为准**；表里查不到的标记，先补样本再下结论，不许猜。

---

## 0. TL;DR（最常用 8 条）

| 你要判断 | 看这条 |
|---|---|
| 这一轮成功了吗 | `任务已全部完成！`（成功）/ `任务已完成，但出现错误！`（部分失败） |
| 哪条链失败 | `任务出错: <链名>` + 尾部 `本轮任务出现以下错误：` 清单 |
| 是不是没登进游戏 | `任务出错: 开始唤醒` |
| 是不是连接/模拟器问题 | `连接失败` / `请 ｢检查连接设置｣…` / `模拟器断开，正在尝试重连 (N)` / `重连失败，连接断开！` / `游戏掉线，停止任务` |
| 这轮切号了吗 | gui.log `账号切换 -->> <掩码>`；asst.log `AccountSwitchTask::_run | enter` + `SwitchAccount@*` 节点推进 |
| 为什么没吃上药/理智不够 | `药: N, 库存: M` / `已使用 N 天内过期理智药` / asst.log `MedicineCounterTaskPlugin` |
| 为什么被中止/退出 | `已停止` / `Shutdown called by \`RunPostActionsCoreAsync|OnClose|CheckAfterCompleted\`` / `MaaAssistantArknights GUI exited` |
| 这行要不要管 | 见 §5 噪声清单（`Toast 通知不可用`、`Failed to set startup`、`HTTP: NotModified` 等 → 不用管） |

---

## 1. 文件与行格式

### 1.1 文件清单与轮转（`<MAA目录>\debug\`）

| 文件 | 内容 | 轮转 |
|---|---|---|
| `gui.log` | GUI 侧日志（任务链起止、错误、账号切换、连接、截图、上报、后置动作） | 超限生成 `gui.bak.log`（上一段） |
| `asst.log` | MaaCore 侧日志（回调 JSON、识别细节、adb 调用） | 超过 **64 MiB** 轮转为 `asst.bak.log`（实测 67,108,968 B） |
| `pending-update-applier.log` | 应用内更新（Pending update）流程 | 追加 |
| `drops/ Fight/ infrast/ interface/ other/ recruit/` | 截图/调试子目录 | — |

要点：`asst.log` 会轮转，**"这一轮"要用时间窗切，不能拿文件起点当轮起点**；`gui.log` 覆盖多天（实测 9 天 2.3 MB）。

### 1.2 gui.log 行格式

```
[2026-09-22 05:43:55.951][INF][AsstProxy]              <2> Start Task Chain: StartUp, Task ID: 1
```

```python
GUI_LINE = r'^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]\[(?P<lvl>TRC|DBG|INF|WRN|ERR)\]\[(?P<mod>[^\]]+)\]\s*(?:<(?P<flow>\d+)>\s*)?(?P<msg>.*)$'
```

- `[模块名]` = 日志类别（`TaskQueueViewModel` / `AsstProxy` / `Bootstrapper` / `GameDataReportService` / `RootViewModel` / `AutoStart` / `ConfigurationHelper` / `HttpResponseLoggingExtension` / `ResourceUpdater` / `ToolboxViewModel` / `EmulatorHelper` / `RunningState` / `WindowManager` / `ETagCache` …）。
- `<N>` = 日志流编号（同一条链/线程上下文的日志共享同一个 N），**判据不要依赖它**。
- **续行规则（重要坑）**：多行消息的后续行**没有前缀**，直接裸行出现在文件里，例如：

```
[2026-09-26 06:13:18.297][ERR][TaskQueueViewModel]     <2> 本轮任务出现以下错误：
基建换班
自动公招
信用收支
领取奖励
```

  逐行 grep 时这些裸行（`基建换班`、`理智: 190/210`、`请 ｢检查连接设置｣…`、`正在尝试重连`、`剿灭模式 : 990 / 1800`…）会单独命中，解析时必须按"无前缀行属于上一条消息"处理。
  续行的另一大来源是**多行异常栈**（上报失败时）：`System.Net.Http.HttpRequestException: …` / ` ---> System.Net.Sockets.SocketException …` / `   at System.Net.Http.…` / `   --- End of inner exception stack trace ---`——这些同样没有前缀，且**不匹配**行正则。

### 1.3 asst.log 行格式

```
[2026-09-24 21:23:52.709][INF][Px28408][Tx3874] Assistant::append_callback | TaskChainCompleted {"taskchain":"Infrast","taskid":3,"uuid":"81e9b5e46be3072d"}
```

```python
ASST_LINE = r'^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]\[(?P<lvl>TRC|INF|WRN|ERR)\]\[Px(?P<pid>\d+)\]\[Tx(?P<tid>\d+)\] (?P<msg>.*)$'
```

- `Px` = 进程号，`Tx` = 线程号；`TRC` 占绝大多数（识别细节），**判据只看 INF/WRN/ERR**。
- 关键载体是 `Assistant::append_callback | <消息名> {json}` 与 `asst::<类>::<函数> | ...` 两类。

### 1.4 编码 / 语言

- 文件为 UTF-8。个别字节异常会让 grep 报 `Binary file … matches`，加 `-a` 或按 UTF-8 容错解码。
- **判据语言 = GUI 语言设置（本机队 zh-cn）**。若将来切语言，§2 的中文判据全部失效；英文常量（`Start/Completed Task Chain`、`TaskChainError`、`MuMuEmulator12`、`Shutdown called by`）不受影响。

---

## 2. 判据表（标记原文 → 含义 → 用途 → 样例）

> 严重度约定：**FATAL**=链/轮失败；**WARN**=需关注但未必失败；**INFO**=状态信息；**NOISE**=噪声（§5）。

### 2.1 任务链生命周期（gui.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `Start Task Chain: <英文链名>, Task ID: N` [INF][AsstProxy] | GUI 把链交给 Core（提交） | 计时/链起点；`<链名>` 为英文常量：StartUp/Fight/Recruit/Infrast/Mall/Award/Roguelike/Reclamation/Custom | `[05:43:55.951][INF][AsstProxy] <2> Start Task Chain: StartUp, Task ID: 1` |
| `Completed Task Chain: <英文链名>, Task ID: N` [INF][AsstProxy] | Core 回报该链完成 | **链成功判据**；`Completed Task Chain: StartUp` 是"已进游戏"信号（巡检/自检在用） | `[05:46:59.876][INF][AsstProxy] <2> Completed Task Chain: StartUp, Task ID: 1` |
| `开始任务: <中文名>` [INF][TaskQueueViewModel] | 同上，中文镜像（开始） | 人眼阅读；中文名=链显示名 | `开始任务: 开始唤醒` / `开始任务: 剿灭作战` |
| `完成任务: <中文名>` [INF][TaskQueueViewModel] | 同上，中文镜像（完成） | AUTO-MAS 用 `完成任务: <名>` 判"该任务做过了"（补跑/剿灭判据） | `完成任务: 自动公招` |
| `Index N, Type "<英文>", Name <中文>, IsEnable true/false` [INF][TaskQueueViewModel] | 本轮队列组成（每轮开始列一遍全部链） | 判断"这轮该跑什么"；对比配置/补跑计划 | `Index 0, Type "StartUp", Name 开始唤醒, IsEnable true` |
| `理智作战: <任务名> 任务跳过` [INF] | 该链被跳过（含"不满足条件跳过"，如理智作战无体力计划） | 判定"跳过≠失败"；AUTO-MAS 视作已完成 | `理智作战: 理智作战 任务跳过`（样本 405 次） |
| `LinkStart Enter` / `LinkStart Exit, N,NNN ms` [INF] | 一轮的起止（含连接耗时） | 轮边界；配合 `Idle: … (called from …)` 定位状态 | `LinkStart Exit, 20,176 ms` |

### 2.2 任务链失败与结果判定（gui.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `任务出错: <中文链名>` [ERR] | **链级失败**（Core 回报 TaskChainError，GUI 记名） | 定因第一入口；`任务出错: 开始唤醒` = 没登进游戏 | `[ERR][TaskQueueViewModel] <2> 任务出错: 自动公招` |
| `出现错误, 已返回` [ERR] | 公招链的"错误返回"（AutoRecruitTask 无具体 why 时） | 公招失败判据（伴随 `任务出错: 自动公招`） | `[06:12:02.431][ERR] <2> 出现错误, 已返回` |
| `无招聘许可，已返回` [INF] | 公招：没有招聘许可（**资源型**） | 补跑判据：**不该补**（资源不足，补也白补） | `[2026-09-23 21:48:22.122][INF] <2> 无招聘许可，已返回` |
| `识别错误, 已返回` / `刷新次数达到上限, 已返回` | 公招：识别失败 / 标签刷新用尽 | 定因细分（识别 vs 上限） | 同族（whyStr 映射：recognition error / refresh limit） |
| `本轮任务出现以下错误：` [ERR] + **裸行清单** | 轮尾错误汇总；紧随其后的裸行=失败链中文名列表 | 一眼看完"这轮坏了哪些链"；补跑输入 | `本轮任务出现以下错误：` → 裸行 `基建换班 / 自动公招 / 信用收支 / 领取奖励` |
| `任务已完成，但出现错误！` [ERR] | **轮终态：部分失败**（有链出错但队列跑完） | 轮结果判据（AUTO-MAS：`任务已完成，但出现错误` 语境下结合 `任务出错` 判部分失败） | `[06:13:18.xxx][ERR] <2> 任务已完成，但出现错误！` |
| `任务已全部完成！` [INF] | **轮终态：成功** | 轮结果判据（AUTO-MAS 的 Success! 分支） | `[INF] <2> 任务已全部完成！` |
| `(用时 0h 27m 32s)` [INF]（续行） | 轮耗时 | 统计/成本观测 | `任务已全部完成！` 后一行 |
| `理智将在 2026-09-23 01:53 回满。(19h 41m 后)` [INF]（续行） | 理智回满时间 | 排班/发车参考 | 同上块 |
| `可用干员不足` [ERR] | 基建换班中某设施排班时可用干员不够（新号/干员被占） | **单次出现不致命**（链会继续）；若其后紧跟 `任务出错: 基建换班` 才算链失败 | `[ERR][TaskQueueViewModel] <2> 可用干员不足` |
| `代理指挥失误` [ERR] | 自动战斗与代理记录不一致（战斗判定失败） | 战斗类定因；常伴随重试 | `代理指挥失误`（样本 9 次） |
| `掉落识别错误` [ERR] | 结算掉落识别失败 | 影响上报与掉落统计，不影响战斗 | `掉落识别错误`（样本 3 次） |
| `无奖励关卡，已停止` [INF] | 该关无奖励（如打过的剿灭），链提前停 | 判定"提前停≠崩溃" | — |
| `关卡计划内没有当日开放的关卡` [INF] | 活动关计划无今日开放关卡（正常跳过） | 判定"跳过≠失败"（样本高频） | — |
| `已停止` [INF] | 一轮被停（用户停 / 掉线停 / 完成后停） | **必须看前文**：前有 `重连失败`/`游戏掉线` → 失败；否则多为正常停止 | `[2026-09-23 00:46:24.486][INF] <2> 已停止` |
| `未选择任务` [INF] | 队列为空（没勾任务） | AUTO-MAS 状态 `MAA 未选择任何任务` | — |

### 2.3 账号切换

**gui.log（权威起点信号）**

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `账号切换 -->> <掩码>` [INF][TaskQueueViewModel] | 本轮**准备切换**到该账号（读配置里的 AccountName 后打出，掩码形如 `139****8825`） | **切号权威信号**（身份追踪/切号闸门以它为准）；出现在 StartUp 链开始前 | `[2026-09-23 01:11:55.908][INF] <2> 账号切换 -->> 139****8825` |

**asst.log（过程/推进信号）**

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `asst::AccountSwitchTask::_run | enter` [TRC] | 切号任务开始 | 证明"确实执行了切号动作" | `[TRC][Px][Tx] asst::AccountSwitchTask::_run | enter` |
| `{"class":"asst::ProcessTask","cur_task":"SwitchAccount@<节点>","details":{"cur_retry":N,"retry_times":M,"to_be_recognized":[…]}}` [INF] | 切号导航的**节点推进状态**：`cur_task`=当前识别到的节点，`cur_retry`=当前重试计数，`retry_times`=上限 | **切号进度判读**：节点从 `StartUpBegin → GameStart → StartToWakeUp → …` 前进=正常；`cur_retry` 持续增长而节点不动=卡在识别 | `{"class":"asst::ProcessTask","cur_task":"SwitchAccount@GameStart","details":{"cur_retry":0,"retry_times":30,…}}` |
| 节点名 `SwitchAccount@GameStart` / `SwitchAccount@StartToWakeUp` | 关键节点：`GameStart`=游戏启动页（选号后点进），`StartToWakeUp`=进入"开始唤醒"状态 | 用 cur_task/cur_retry 判"切号是否推进/卡死" | 见上 |
| 其他节点（观测到的全集） | `StartUpBegin`、`StartUpConnectingFlag`、`StartUpOCR`、`StartToWakeUp`、`GameStart`、`StartLoginBServer`、`OfflineConfirm`、`NotDownloadVoice`、`LeidianGoogleFrameworkConfirm`、`BiliBiliNoMorePromptsNextTime`、`CloseAnno`/`CloseAnnoTexas`/`CloseAnnoSpecialAccess`、`TodaysSupplies`、`LoadingIcon` | 逐段定因 | — |
| `asst::AccountSwitchTask::select_account | enter/leave, N ms` [TRC] | 账号列表滚动查找（最多 20 次） | 判"找号失败"（leave 后无 SubTaskExtraInfo 且 gui.log 无切换成功） | — |
| `Assistant::append_callback | SubTaskExtraInfo {"class":"asst::AccountSwitchTask","details":{"account_name":"…"},"subtask":"AccountSwitch",…}` [INF] | **切换动作成功**（找到并点了目标账号） | asst 侧成功信号 | `…"details":{"account_name":"<掩码>","subtask":"AccountSwitch"…}` |
| `task disabled, pass {"class":"asst::AccountSwitchTask"…}` [TRC] | 切号任务被禁用（AccountSwitchEnabled=false 或该号未配 AccountName） | **"没切号"的 asst 侧证据**（≠ 失败） | — |
| `asst::AccountSwitchTask::_run | account is empty` / `unsupported client` [ERR] | 账号名为空 / 客户端不支持切号 | 切号失败定因（台/韩服需 v6.11+；日服/国际服不支持） | — |

判读要点：`账号切换 -->> `（gui）与 `AccountSwitch`（asst）是"动作"信号；**MAA 不做"切到的就是目标号"的最终核对**——"是否真的切对号"要靠身份追踪（截图/UID）另行确认。

### 2.4 后置动作与退出（gui.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `Post actions: 退出 明日方舟 -> 退出 MAA` [INF][TaskQueueViewModel] | 本轮结束要执行的**计划**后置动作（配置决定） | 只是"计划"，不代表已执行；执行看下一行 | `[06:11:28.578][INF] <2> Post actions: 退出 明日方舟 -> 退出 MAA` |
| `Shutdown called by \`RunPostActionsCoreAsync\`` [INF][Bootstrapper] | **后置动作执行完**导致的 GUI 退出 | 正常收尾（配合 `Post actions:` 读） | `[06:11:29.610][INF][Bootstrapper] <2> Shutdown called by \`RunPostActionsCoreAsync\`` |
| `Shutdown called by \`CheckAfterCompleted\`` | "完成后"自动检查触发的退出 | 正常收尾 | 同上（样本 430 次） |
| `Shutdown called by \`OnClose\`` | 用户/外部**关闭窗口** | 被手动关掉/被清理 | 样本 371 次 |
| `Shutdown called by \`OnStart\`` | 启动阶段异常退出 | 罕见（样本 1 次） | — |
| `MaaAssistantArknights GUI exited` | 进程退出（AUTO-MAS 也以此判"未完成即退出"） | 判"没跑完就退了" | — |

### 2.5 连接 / ADB / 模拟器（gui.log + asst.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `正在连接模拟器……` [INF] | 开始连接 | 轮内连接段起点 | `[INF] <2> 正在连接模拟器……` |
| `name: MuMuEmulator12, extras: {}` + `handle: N, adbPath: E:\MuMu Player 12\nx_main\adb.exe, address: 127.0.0.1:NNNNN, config: MuMuEmulator12` [INF][AsstProxy] | 连接目标描述（MuMu12 配置、实例 adb 端口） | 确认"连的是哪个模拟器/端口"；端口↔实例核对 | `[INF][AsstProxy] <15> handle: 2294820910160, adbPath: …\adb.exe, address: 127.0.0.1:17220, config: MuMuEmulator12` |
| `… config: MuMuEmulator12, return: true/false` [INF] | **连接尝试结果**（false=失败） | 连接失败定位（哪次尝试、哪个端口） | `…, return: false` |
| `触控模式不可用。请进入 ｢设置 - 连接设置｣ 切换其他触控模式。` [ERR] | 触控方式不被当前环境支持 | 连接失败常见伴随项 | `[ERR][TaskQueueViewModel] <2> 触控模式不可用。请进入 ｢设置 - 连接设置｣ 切换其他触控模式。` |
| `连接失败\n正在尝试重连` [INF]（多行） | 失败第 1 级：直接重连 | 连接阶梯开始 | `连接失败` + 续行 `正在尝试重连` |
| `连接失败\n正在尝试重启 ADB Server` [INF]（多行） | 失败第 2 级：重启 ADB server | 阶梯判读 | 同上 |
| `连接失败\n正在尝试关闭并重启 ADB 进程` [INF]（多行） | 失败第 3 级：重启 ADB 进程 | 阶梯判读 | 同上 |
| `连接失败\n请 ｢检查连接设置｣ → ｢尝试重启模拟器与 ADB｣ → ｢重启电脑｣` [ERR]（多行） | **连接彻底失败**（阶梯走完） | AUTO-MAS 状态 `MAA 的 ADB 连接异常`；轮失败判据 | `[08:18:00.111][ERR] <2> 连接失败` + 续行 `请 ｢检查连接设置｣…` |
| `未检测到任何模拟器`（多行提示块） | 一个都没找到 | AUTO-MAS 状态 `MAA 未检测到任何模拟器`（本批样本未出现） | — |
| `模拟器断开，正在尝试重连 (N)` [ERR] | 运行中掉线，第 N 次重连 | 掉线判据（N 递增=反复掉） | `[ERR][TaskQueueViewModel] <2> 模拟器断开，正在尝试重连 (1)` |
| `重连成功，继续任务` [INF] | 掉线后恢复 | 掉线自愈证据 | `[2026-09-20 23:12:41.108][INF] <2> 重连成功，继续任务` |
| `重连失败，连接断开！` [ERR] | 掉线后没救回来 | **FATAL**：本轮必失败（后随 `已停止`） | `[ERR] <2> 重连失败，连接断开！` |
| `游戏掉线，停止任务` [ERR] | 游戏客户端离线（Core 判定） | **FATAL**：停止本轮 | `[ERR] <2> 游戏掉线，停止任务` |
| asst.log：`Call \` E:\…\adb.exe -s 127.0.0.1:NNNNN exec-out "screencap \| nc …" \` ret 0 , cost NNN ms` [INF] | 每次 adb 调用（截图走 nc 通道时） | 截图/连接性能定因（看 cost） | `Call \`…adb.exe -s 127.0.0.1:16964 exec-out "screencap \| nc -w 3 10.0.2.2 52029" \` ret 0 , cost 506 ms` |
| asst.log：`Assistant::append_callback | ConnectionInfo {"details":{"avg":N,"max":N,"min":N},"what":"ScreencapCost"}` [INF] | 截图耗时统计（avg/max/min，ms） | 截图性能定因（与 gui.log 的 `最快截图耗时` 对照） | `{"details":{"avg":443,"max":524,"min":345},"what":"ScreencapCost"}` |
| asst.log：`ConnectionInfo … "what":"EmulatorFPS" {"fps":60,…}` | 模拟器帧率 | 性能定因 | `{"details":{"fps":60,"refresh_period_ns":16666666},"what":"EmulatorFPS"}` |

### 2.6 截图性能（gui.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `截图用时 {N}ms。…` [INF] | 单次/平均截图耗时（正常值） | 性能观测 | `截图用时 395ms…` |
| `截图用时较长(平均: NNNms)，自动战斗类功能（如自动肉鸽）可能表现异常。…` [WRN] | 截图偏慢（>阈值，实测 408–699ms 触发） | **WARN**：肉鸽/自动战斗类易受影响；持续出现再处理 | `[WRN] <2> 截图用时较长(平均: 495ms)，自动战斗类功能（如自动肉鸽）可能表现异常。如使用 MuMu、雷电模拟器…` |
| `截图用时过长(平均: NNNms)…` [ERR] | 截图过慢（更严重档） | 排查模拟器设置（图形/帧率/多开资源） | —（样本未出现） |
| `最快截图耗时: NNNms (RawWithGzip|RawByNc|Encode)` [INF/WRN] | 本段最快截图方法+耗时（`RawByNc`=nc 通道，`RawWithGzip`=gzip 通道） | 截图通道定因；WRN 档=偏慢 | `[INF] <2> 最快截图耗时: 395ms (RawByNc)` / `[WRN] <2> 最快截图耗时: 505ms (RawWithGzip)` |
| `截图失败，如反复出现请尝试重启或更换模拟器！` [ERR] | 截图彻底失败 | **FATAL**（多为掉线/连接问题的表现） | — |

### 2.7 吃薬 / 理智（gui.log + asst.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `开始行动 N~M 次, -K理智` [INF] | 战斗计划（次数区间 + 消耗理智） | 本轮作战规模 | `开始行动 21~30 次, -60理智` |
| `理智: N/M` [INF]（续行） | 当前/上限理智 | 巡检体力口径（AUTO-MAS 解析 `理智:\s*(\d+)/\d+`） | `理智: 157/210` |
| `药: N, 库存: M` [INF]（续行） | **本次吃理智药 N 个，库存 M** | 吃药口径（是否吃到药、库存余量） | `药: 1, 库存: 1` |
| `理智: N/M  药: K,L(临期)` [INF] | 吃药明细（含临期药） | 临期药口径 | `理智: 123/173  药: 0,1(临期)` |
| `已使用 N 天内过期理智药 X(+Y)` [INF] | 用了临期药（N 天内过期，X(+Y)=数量明细） | 临期药管理 | `已使用 2 天内过期理智药 8(+1)` |
| `已使用理智药` / `已使用源石` [INF] | 用了药 / 碎了石 | 资源消耗口径 | — |
| asst.log：`asst::Assistant::append_task Fight {"stage":"1-7","medicine":0,"medicine_expire_days":2,"stone":0,"times":2147483647,"series":0,"DrGrandet":false,"report_to_penguin":true,"report_to_yituliu":true,"server":"ZH_TW","client_type":"txwy"}` [INF] | **Fight 任务提交参数**：吃药上限/临期天数/碎石/次数/连战/DrGrandet 策略/上报开关/服务器与客户端 | 判"配置层是否允许吃药、上报、碎石"；`times:2147483647`=理智打完 | 见左 |
| asst.log：`asst::MedicineCounterTaskPlugin::init_count medicine using count: N , inventory count: M , expire days: D` [TRC] | 吃药计数初始化 | 吃药过程证据 | `… medicine using count: 1 , inventory count: 3 , expire days: 10` |
| asst.log：`MedicineCounterTaskPlugin::_run Using medicines …` / `Needn't to use medicines, used: 0 , max: 0 , expire_days: 0` [TRC] | 用了/没用（used/max） | 判"为什么没吃药"（不需吃 vs 吃不上） | 见左 |
| asst.log：`medicine reduce icon analyze failed` [ERR] | 药品减少图标识别失败（计数可能不准） | 低危，偶发忽略 | — |

### 2.8 基建（gui.log + asst.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `当前设施: <设施> <编号>` [INF] | 基建换班正在处理哪个设施 | 进度/卡点定位 | `当前设施: 宿舍 04` |
| `训练室空闲中` [INF] | 训练室无人 | 正常信息 | — |
| `进行线索交流` / `已开启线索交流` [INF] | 会客室线索交流动作/结果 | 基建完成度判据 | `已开启线索交流` |
| `可用干员不足` [ERR] | 某设施排不满人 | 见 §2.2（单次不致命） | — |
| asst.log：`asst::InfrastAbstractTask::enter_facility` / `InfrastAbstractTask::select_opers_review` / `InfrastProductionTask::opers_choose` [TRC/INF] | 设施进入/干员选择过程 | 基建细粒度定因 | — |
| asst.log：`Assistant::append_callback | SubTaskError {"class":"asst::InfrastControlTask","details":{"facility":"Control","index":0}…}` | 控制中枢出错 | 基建链内错误（facility/index 指明设施） | 见左 |

### 2.9 公招（gui.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `公招识别结果:` [INF] + 裸行标签清单 | 本槽位识别到的标签 | 公招过程证据 | `公招识别结果:` → `群攻 / 快速复活 / 元素 / 高级资深干员 / 支援机械` |
| `N ★ Tags` [INF] | 组合出的星级（1–6） | 判"值不值得招" | `6 ★ Tags` |
| `选择 Tags：` [INF] + 裸行 | 最终选择的标签 | 过程证据 | `选择 Tags：` → `快速复活` |
| `已刷新标签` [INF] | 刷新了一次标签 | 刷新计数佐证 | — |
| `当前槽位已刷新N次` [INF] | 该槽位累计刷新次数 | 判"刷新上限将至"（上限后 → `刷新次数达到上限, 已返回`） | `当前槽位已刷新1次` |
| `已确认招募 N` [INF] | 确认招募（N=时间/结果计数） | 公招成功动作 | `已确认招募 4` |
| `无招聘许可，已返回` / `出现错误, 已返回` / `识别错误, 已返回` | 公招失败/异常返回（见 §2.2） | 定因 + 补跑判据（资源型不补） | — |

### 2.10 商店 / 信用收支 / 访问好友（gui.log + asst.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `开始任务: 信用收支` / `完成任务: 信用收支` | 信用收支链边界 | 链完成判据 | `完成任务: 信用收支` |
| `开始任务: 访问好友` / `完成任务: 访问好友` | 访问好友链（信用来源） | 链完成判据 | — |
| `Index N, Type "Mall", Name 信用收支, IsEnable true` | 队列含信用收支 | 配置核对 | 见 §2.1 |
| asst.log：`asst::MallTask::set_params` / `asst::CreditShoppingTask` / `asst::CreditShopImageAnalyzer` [TRC/INF] | 信用商店执行过程 | 商店细粒度定因 | — |
| asst.log：`Assistant::append_callback | SubTaskError {"class":"asst::CreditShoppingTask","details":{}…,"taskchain":"Mall"…}` | 信用商店出错（**实测常见但不致命**：链仍 Completed） | 定因时结合 gui.log 是否 `任务出错: 信用收支` | 见左 |
| asst.log：OCR 文本 `获得信用点30` 等 | 识别到的信用点弹窗文本 | 仅内部识别细节（噪声级） | — |

### 2.11 掉落与上报（GameDataReport / 企鹅物流 / 一图流）

**gui.log（GameDataReportService）**

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `Start PostWithRetryAsync, url: https://penguin-stats.io/PenguinStats/api/v2/report` [INF] | 企鹅物流掉落上报开始 | 上报链路观测 | 见左 |
| `Start PostWithRetryAsync, url: https://backend.yituliu.cn/maa/upload/stageDrop` / `…/upload/recruit` [INF] | 一图流（关卡掉落 / 公招结果）上报开始 | 上报链路观测 | 见左 |
| `Request headers: {…"X-Penguin-Idempotency-Key"…}` / `Request headers: {}` [INF] | 上报请求头（幂等键） | 调试上报 | — |
| `Request body: {"drops":[{"dropType":"NORMAL_DROP","itemId":"30012","quantity":11},…],"server":"CN","source":"MaaAssistantArknights","stageId":"main_01-07","times":10,"version":"v6.18.0"}` [INF] | **掉落上报正文**。键：`drops[]`(`dropType`∈NORMAL_DROP/EXTRA_DROP/FURNITURE、`itemId`、`quantity`)、`server`(CN/JP…)、`source`、`stageId`(如 `main_01-07`)、`times`(作战次数)、`version`；`drops` 可为 `null` | 数据面口径（"上报了什么"）；对账掉落统计 | 见左 |
| 公招上报 body：`{"drops":[{"dropType":"NORMAL_DROP","itemId":"重装干员","quantity":1},…]…}` 与 `{"level":3,"result":[{"level":3,"opers":[{"id":"char_346_aosta","level":5,"name":"…"}]}]}` | 公招标签/结果上报（一图流） | 公招数据面 | 见左 |
| `HTTP: OK POST https://penguin-stats.io/…` [INF] | 上报成功 | 上报成功判据 | — |
| `Trying backup domain…` / `Initial request failed…` / `Request failed on backup domain…` / `All retries failed for URL: …` [WRN/ERR] | 主域失败→备用域→全败 | 上报失败定因（网络/限流，**不影响游戏任务**） | — |
| `Error during HTTP POST request to <url>` [ERR] + 异常栈 | 上报请求异常（如代理拒连 `127.0.0.1:7890`） | 网络/代理定因 | `由于目标计算机积极拒绝，无法连接。 (127.0.0.1:7890)` |
| `未识别关卡, 放弃上传企鹅物流` / `非三星结算, 放弃上传企鹅物流` / `未识别掉落类型, 放弃上传企鹅物流` / `Failed to report, 放弃上传企鹅物流` / `剿灭关卡, 放弃上传企鹅物流` [WRN] | 上报被放弃（数据不全或规则排除） | **WARN**，正常情况之一（如剿灭不报） | 见左 |
| `<关卡> 掉落统计:` [INF] + 裸行 `物品 : N (+N)` | 本轮掉落统计块 | 掉落对账；`(+N)`=本次新增 | `炎国 掉落统计:` → `龙门币 : 250 (+250)` |
| `Depot updated from stage drops, N items processed` [INF][ToolboxViewModel] | 仓库已按掉落更新 | 数据面正常信号 | — |

**asst.log**

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `Assistant::append_callback | ReportRequest {"body":"{\"drops\":[…]}…}` [INF] | Core 向 GUI 提交上报请求（原始数据） | 数据面源头 | `{"body":"{\"drops\":[{\"dropType\":\"NORMAL_DROP\",\"itemId\":\"术师干员\",\"quantity\":1},…` |
| `StageDropsTaskPlugin::report_penguin_callback` / `AutoRecruitTask::report_penguin_callback` / `…::report_yituliu_callback` [TRC] | 上报回调细节 | 细粒度定因 | — |

### 2.12 剿灭（gui.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `Index N, Type "Fight", Name 剿灭作战, IsEnable true` | 本轮含剿灭（Fight 链的剿灭模式） | 排班核对 | `Index 1, Type "Fight", Name 剿灭作战, IsEnable true` |
| `完成任务: 剿灭作战` [INF] | 剿灭链完成 | AUTO-MAS 剿灭完成判据之一 | `[2026-08-28 10:57:39.351][INF] <2> 完成任务: 剿灭作战` |
| `剿灭模式 : N / M` [INF]（裸行/续行） | **剿灭周进度**（如 990/1800） | AUTO-MAS 解析 `(?:剿灭模式|剿滅模式|Annihilation(?: Mode| weekly limit)|殲滅作戦|섬멸 모드)\s*[:：]\s*(\d+)\s*/\s*(\d+)`；到 M 即本周完成 | `剿灭模式 : 990 / 1800` |
| `无奖励关卡，已停止` | 本周已满仍去打 → 无奖励停 | 正常 | — |

### 2.13 运行框架 / 信息类（gui.log）

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `MaaAssistantArknights GUI started` / `Version v6.18.0` / `Built at …` / `Maa ENV: Production` / `User Dir …` / `Run as Administrator` | 启动头（每次启动一段，前后为 `=====`） | 版本口径（判据与版本绑定）；定位重启 | `[INF][Bootstrapper] <2> Version v6.16.8` |
| `Main windows log clear.` [INF] | 新轮清屏 | 轮边界辅助 | — |
| `Build Time:` + 裸行 `2026/8/11 20:20:11` + `Resource Time:` + 裸行 | 构建/资源时间 | 版本核对 | 见左 |
| `Idle: true to false (called from LinkStartWithTasks)` / `Init: false to true (called from Init)` / `InterruptLock: depth=…` [INF][RunningState] | 状态机迁移（谁触发） | 精确定位"谁发起的动作" | `Idle: true to false (called from LinkStartWithTasks)` |
| `Timer wait time: 10` / `Timer canceled: false` [INF] | 起跑倒计时 | 轮边界 | — |
| `AsstLoadResource dirname: …` / `ret: true` / `Load resource: …` / `LoadResource Exit, N ms` [INF][AsstProxy] | 资源加载 | 启动健康 | — |
| `ConfigurationHelper … gui.json.bak saved` / `Configuration … has been set: \`…\` -> \`…\`` [INF] | 配置备份/写值（含 AutoProxy 注入的值） | **注入合规核对**（闸门辅助） | — |
| `New version found: 2026-08-25+18:05:48.000, 墟·复刻` [INF][ResourceUpdater] | 资源有新版本 | 信息 | — |
| `Pending update package detected…` / `Delegating pending update…` [INF] | 应用内更新流程 | 信息（升级窗口期） | — |

### 2.14 asst.log 核心回调与状态

| 标记（原文） | 含义 | 用途 | 样例 |
|---|---|---|---|
| `Assistant::append_callback | TaskChainStart {"taskchain":"…","taskid":N,"uuid":"…"}` [INF] | 链开始（core 侧） | 与 gui `Start Task Chain` 对照 | — |
| `… | TaskChainCompleted {"taskchain":"Infrast","taskid":3,…}` [INF] | 链完成（core 侧） | **链成功权威证据**（含 uuid 关联一轮） | 见左 |
| `… | TaskChainError {"taskchain":"StartUp","taskid":1,"uuid":"…"}` [INF] | **链失败**（core 侧） | 定因主证据（`mas_error_triage` / `maa_error_daily` 都在读它） | `[2026-09-25 05:09:47.029][INF][Px11404][Tx34535] Assistant::append_callback | TaskChainError {"taskchain":"StartUp","taskid":1,"uuid":"81e9b5e46be3072d"}` |
| `… | TaskChainStopped {"finished_tasks":[…],"taskchain":"CloseDown",…}` [INF] | 链被停（含收尾 CloseDown） | 判"停在哪" | 见左 |
| `… | AllTasksCompleted {"finished_tasks":[1,2,3,4,5,6],"taskchain":"Award","taskid":6,…}` [INF] | **全部任务完成**（core 侧） | 轮成功权威证据（对照 gui `任务已全部完成！`） | 见左 |
| `… | SubTaskError {"class":"asst::<类>","details":{…},"subtask":"…","taskchain":"…","taskid":N,…}` [INF] | **链内子任务错误**（细因：class/details 指明是谁、在哪） | 细粒度定因（如 `BattleFormationTask` 编队缺人 `details.opers{name,reason:Missing}`） | `{"class":"asst::CreditShoppingTask","details":{},…,"taskchain":"Mall"…}` |
| `… | SubTaskStart/SubTaskCompleted {"class":…}` [INF] | 子任务边界 | 进度分析 | — |
| `… | ConnectionInfo {…}` | 截图耗时/帧率（见 §2.5） | 性能 | — |
| `{"class":"asst::ProcessTask","cur_task":"<节点>","details":{"cur_retry":N,"retry_times":M,"to_be_recognized":[…]}}` [INF] | **通用 ProcessTask 状态**（含切号） | 卡点判读（见 §2.3） | 见 §2.3 |
| `asst::PipelineAnalyzer::analyze | MatchTemplate <节点名>` [TRC] | 模板匹配细节 | 深挖识别问题时才用 | — |
| `asst::Assistant::append_task <链> {…}` [INF] | 任务提交参数（Fight 见 §2.7；其他链同理） | 配置面口径 | 见 §2.7 |

---

## 3. 结果判定 SOP（一轮跑完怎么读）

**成功一轮（实测序列）**

```
账号切换 -->> 139****8825            ← 仅多号脚本有
Index 0..N, Type … IsEnable …        ← 队列组成
开始任务: 开始唤醒 / Start Task Chain: StartUp, Task ID: 1
Completed Task Chain: StartUp, Task ID: 1
…（逐链）…
任务已全部完成！
(用时 0h 27m 32s)
理智将在 2026-09-23 01:53 回满。(19h 41m 后)
Post actions: 退出 明日方舟 -> 退出 MAA
Shutdown called by `RunPostActionsCoreAsync`
```

**部分失败一轮（实测序列）**

```
出现错误, 已返回                      ← 某链的错误返回
任务出错: 自动公招                    ← 链失败记名
…（其余链继续）…
任务已完成，但出现错误！
(用时 …)
理智将在 … 回满。(…)
本轮任务出现以下错误：                ← 后随裸行清单
基建换班
自动公招
信用收支
领取奖励
Post actions: 退出 明日方舟 -> 退出 MAA
Shutdown called by `RunPostActionsCoreAsync`
```

**中止/未完成**：`重连失败，连接断开！` 或 `游戏掉线，停止任务` → `已停止`（→ 判失败）；进程被杀则无收尾行，只看到 `MaaAssistantArknights GUI exited` 缺失 + 日志停在半路。

**读日志顺序建议（tail 优先，与 AUTO-MAS `check_log` 一致）**：
`未选择任务` → `任务出错: 开始唤醒` → `任务已全部完成！`（再看 `完成任务:` / `任务跳过` 是否覆盖全部链 + `任务出错: 理智作战`） → `请 ｢检查连接设置｣…` → `未检测到任何模拟器` → `已停止` → `MaaAssistantArknights GUI exited`/进程不在 → 超时 → 正常运行中。

---

## 4. 正则清单（机器可用）

> 用法：全部对**原始行**（含续行裸行）匹配即可；如需严格"消息级"匹配，先按 §1.2 取出 `msg` 再匹配（多行消息已按续行拼接后匹配）。
> 本清单已用 12 份 gui.log + 3 份 asst.log 实机样本回归验证（73 条正则全部编译通过、逐条命中样本）。

```python
# ========== 行解析 ==========
GUI_LINE  = r'^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]\[(?P<lvl>TRC|DBG|INF|WRN|ERR)\]\[(?P<mod>[^\]]+)\]\s*(?:<(?P<flow>\d+)>\s*)?(?P<msg>.*)$'
ASST_LINE = r'^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]\[(?P<lvl>TRC|INF|WRN|ERR)\]\[Px(?P<pid>\d+)\]\[Tx(?P<tid>\d+)\] (?P<msg>.*)$'
# 注意：不匹配 GUI_LINE 的行 = 上一条消息的续行（多行消息）

# ========== 轮结果（FATAL/OK）==========
R_NO_TASK        = r'未选择任务'
R_LOGIN_FAIL     = r'任务出错[:：]\s*开始唤醒'
R_ALL_DONE       = r'任务已全部完成！'
R_DONE_WITH_ERR  = r'任务已完成，但出现错误！'
R_ERR_LIST_TITLE = r'本轮任务出现以下错误：'
R_TASK_ERROR     = r'任务出错[:：]\s*(?P<chain>[^\r\n]+)'
R_CHAIN_RETURN   = r'(?:出现错误|识别错误|刷新次数达到上限), 已返回|无招聘许可，已返回'
R_STOPPED        = r'(?<!，)已停止\s*$'   # 排除 "无奖励关卡，已停止"；直接对原始行/消息尾匹配
R_GUI_EXITED     = r'MaaAssistantArknights GUI exited'

# ========== 连接/模拟器 ==========
R_CONN_FAIL_LADDER = r'连接失败\n(?:正在尝试重连|正在尝试重启 ADB Server|正在尝试关闭并重启 ADB 进程)'
R_CONN_FAIL_FINAL  = r'连接失败\n请 ｢检查连接设置｣ → ｢尝试重启模拟器与 ADB｣ → ｢重启电脑｣'
R_NO_EMULATOR      = r'未检测到任何模拟器'
R_TOUCH_UNAVAIL    = r'触控模式不可用'
R_EMU_DISCONNECT   = r'模拟器断开，正在尝试重连 \((?P<n>\d+)\)'
R_RECONNECT_OK     = r'重连成功，继续任务'
R_RECONNECT_FAIL   = r'重连失败，连接断开！'
R_GAME_DROP        = r'游戏掉线，停止任务'
R_EMU_CONNECT_TRY  = r'name: (?P<cfg>\w+), extras: \{\}'          # 后随 handle/adbPath/address 行
R_EMU_CONNECT_RES  = r'address: (?P<addr>\S+), config: (?P<cfg>\w+), return: (?P<ok>true|false)'
R_SCREENCAP_FAIL   = r'截图失败，如反复出现请尝试重启或更换模拟器！'
R_SCREENCAP_WARN   = r'截图用时较长\(平均: (?P<ms>\d+)ms\)'
R_SCREENCAP_SLOW   = r'截图用时过长\(平均: (?P<ms>\d+)ms\)'
R_SCREENCAP_BEST   = r'最快截图耗时: (?P<ms>\d+)ms \((?P<way>RawWithGzip|RawByNc|Encode)\)'

# ========== 任务链 ==========
R_CHAIN_START  = r'Start Task Chain: (?P<chain>\w+), Task ID: (?P<id>\d+)'
R_CHAIN_DONE   = r'Completed Task Chain: (?P<chain>\w+), Task ID: (?P<id>\d+)'
R_ZH_START     = r'开始任务: (?P<name>.+)'
R_ZH_DONE      = r'完成任务: (?P<name>.+)'
R_ZH_SKIP      = r'(?P<chain>[^:]+): (?P<task>.+) 任务跳过'
R_QUEUE_ITEM   = r'Index (?P<i>\d+), Type "(?P<type>\w+)", Name (?P<name>.+), IsEnable (?P<on>true|false)'

# ========== 账号 ==========
R_ACCT_SWITCH  = r'账号切换 -->> (?P<mask>\S+)'
R_ACCT_NODE    = r'"cur_task":"SwitchAccount@(?P<node>\w+)".*?"cur_retry":(?P<retry>\d+),"retry_times":(?P<max>\d+)'
R_ACCT_DISABLED= r'task disabled, pass \{"class":"asst::AccountSwitchTask"'

# ========== 后置/退出 ==========
R_POST_ACTIONS = r'Post actions: (?P<acts>.+)'
R_SHUTDOWN     = r'Shutdown called by `(?P<who>[A-Za-z]+)`'

# ========== 吃薬/理智/基建/公招/商店/掉落 ==========
R_SANITY        = r'理智: (?P<cur>\d+)/(?P<max>\d+)'
R_MEDICINE      = r'药: (?P<n>\d+)(?:,(?P<exp>\d+)\(临期\))?(?:, 库存: (?P<inv>\d+))?'
R_MEDICINE_EXP  = r'已使用 (?P<days>\d+) 天内过期理智药 (?P<x>\d+)\(\+(?P<y>\d+)\)'
R_FIGHT_PLAN    = r'开始行动 (?P<a>\d+)(?:~(?P<b>\d+))? 次, -(?P<sanity>\d+)理智'
R_FACILITY      = r'当前设施: (?P<fac>\S+) (?P<idx>\d+)'
R_INFRA_NO_OPER = r'可用干员不足'
R_RECRUIT_RES   = r'公招识别结果:'
R_RECRUIT_STAR  = r'(?P<n>\d) ★ Tags'
R_RECRUIT_TAGS  = r'选择 Tags：'
R_RECRUIT_CONF  = r'已确认招募 (?P<n>\d+)'
R_RECRUIT_REFR  = r'当前槽位已刷新(?P<n>\d+)次'
R_MALL_CHAIN    = r'(?:开始|完成)任务: (?:信用收支|访问好友)'
R_DROPS_STAT    = r'(?P<stage>\S+) 掉落统计:'
R_DEPOT_UPDATED = r'Depot updated from stage drops, (?P<n>\d+) items processed'
R_ANNIHILATION  = r'剿灭模式\s*[:：]\s*(?P<cur>\d+)\s*/\s*(?P<max>\d+)'      # AUTO-MAS 同口径(含多语言变体)
R_ANNIHILATION_OK = r'完成任务: 剿灭作战'

# ========== 上报（GameDataReport）==========
R_REPORT_START = r'Start PostWithRetryAsync, url: (?P<url>\S+)'
R_REPORT_BODY  = r'Request body: (?P<body>\{.*\})'
R_REPORT_OK    = r'HTTP: OK POST (?P<url>\S+)'
R_REPORT_GIVEUP= r'(?P<why>未识别关卡|非三星结算|未识别掉落类型|剿灭关卡|Failed to report), 放弃上传企鹅物流'
R_REPORT_FAIL  = r'(?:All retries failed for URL: (?P<url>\S+)|Error during HTTP POST request to (?P<url2>\S+))'

# ========== asst.log ==========
A_CALLBACK    = r'Assistant::append_callback \| (?P<msg>[A-Za-z]+) (?P<json>\{.*\})'
A_CHAIN_ERR   = r'Assistant::append_callback \| TaskChainError (?P<json>\{.*\})'
A_CHAIN_OK    = r'Assistant::append_callback \| TaskChainCompleted (?P<json>\{.*\})'
A_CHAIN_STOP  = r'Assistant::append_callback \| TaskChainStopped (?P<json>\{.*\})'
A_ALL_DONE    = r'Assistant::append_callback \| AllTasksCompleted (?P<json>\{.*\})'
A_SUB_ERR     = r'Assistant::append_callback \| SubTaskError (?P<json>\{.*\})'
A_ACCT_ENTER  = r'asst::AccountSwitchTask::_run \| enter'
A_ACCT_DISABLED = r'task disabled, pass \{"class":"asst::AccountSwitchTask"'
A_PROC_STATE  = r'\{"class":"asst::ProcessTask","cur_task":"(?P<node>[^"]+)","details":\{"cur_retry":(?P<retry>\d+),"retry_times":(?P<max>\d+)'
A_APPEND_FIGHT= r'asst::Assistant::append_task Fight (?P<json>\{.*\})'
A_MED_COUNT   = r'MedicineCounterTaskPlugin::init_count medicine using count: (?P<use>\d+) , inventory count: (?P<inv>\d+) , expire days: (?P<days>\d+)'
A_MED_RUN     = r'MedicineCounterTaskPlugin::_run (?P<kind>Using medicines|Needn.t to use medicines), used: (?P<used>\d+) , max: (?P<max>\d+)'
A_SCREENCAP    = r'ConnectionInfo \{"details":\{"avg":(?P<avg>\d+),"max":(?P<max>\d+),"min":(?P<min>\d+)\}.*"what":"ScreencapCost"\}'
A_FPS          = r'ConnectionInfo \{"details":\{"fps":(?P<fps>\d+).*"what":"EmulatorFPS"\}'
A_ADB_CALL     = r'Call ` (?P<cmd>.+) ` ret (?P<ret>-?\d+) , cost (?P<cost>\d+) ms'
A_REPORT_REQ   = r'Assistant::append_callback \| ReportRequest (?P<json>\{.*\})'
```

**严重度标注（建议给巡检/聚合用）**

| 级别 | 判据 |
|---|---|
| FATAL（轮/链失败） | `R_LOGIN_FAIL`、`R_TASK_ERROR`、`R_CHAIN_RETURN`、`R_CONN_FAIL_FINAL`、`R_RECONNECT_FAIL`、`R_GAME_DROP`、`R_SCREENCAP_FAIL`、`R_TOUCH_UNAVAIL`、`R_DONE_WITH_ERR`、`A_CHAIN_ERR`、`A_SUB_ERR`(class=关键链时) |
| WARN | `R_EMU_DISCONNECT`、`R_SCREENCAP_WARN`、`R_SCREENCAP_SLOW`、`R_REPORT_GIVEUP`、`R_REPORT_FAIL`、`R_INFRA_NO_OPER`、`代理指挥失误`、`掉落识别错误` |
| INFO | 其余 §2 条目 |
| NOISE | §5 清单 |

---

## 5. 噪声清单（看到这些不用管）

| 标记 | 为什么不用管 |
|---|---|
| `Toast 通知不可用, 用户在 Windows 设置中关闭了所有应用通知` [ERR][RootViewModel] | 仅影响桌面通知；与任务执行无关 |
| `Failed to set startup: No value exists with that name.` [ERR][AutoStart] | 注册表自启项不存在（未勾自启）；每次启动都报 |
| `HTTP: NotModified GET https://api.maa.plus/…` [INF] | 资源/公告未变化（更新检查的正常结果） |
| `HTTP: OK GET https://mirrorchyan.com/api/resources/…` / `{"code":0,…"current resource latest version is …"}` [INF][ResourceUpdater] | 更新检查/镜像源查询正常 |
| `Application is releasing, skip create save task` [ERR][ConfigurationHelper] | 退出时跳过一次配置保存；无害 |
| `Waiting for save task to complete` / `… saved` [INF][ConfigurationHelper] | 配置写盘过程 |
| `InterruptLock: depth=…` / `Idle: … to …` / `Timer wait time` / `Timer canceled` / `Main windows log clear.` [INF][RunningState/TaskQueueViewModel] | 运行框架内部状态 |
| `Failed to send GET request to https://api.maa.plus/…` [ERR][ETagCache] | 单次网络失败（持续出现才当网络问题） |
| `mirrorc failed` / `Failed to send GET request to https://mirrorchyan.com/…` [ERR][ResourceUpdater] | 镜像源失败自动回退官方源 |
| asst.log `match_templ \| …` [TRC] | 模板匹配细节，海量；只在查识别问题时才看 |
| asst.log `ready to sleep N` / `end of sleep N` [TRC] | 内部 sleep |
| asst.log `stdout output:` [TRC] / `Call \`…\` ret 0` [INF]（截图通道成功、cost 正常时） | adb 回显/调用细节 |
| asst.log `screencap \| nc -w 3 …` ret 0（socket size 正常） | MuMu nc 截图通道正常回显（`failed` 字样为回退过程，非失败） |
| asst.log `data is empty!` [TRC] / `fps command failed or empty` [TRC/WRN] | 内部识别/读取空结果，会自动重试 |
| asst.log `medicine reduce icon analyze failed` [ERR] | 偶发图标识别失败（罕见） |
| `Pending update package detected…` / `Delegating pending update…` [INF] | 应用内更新流程（升级窗口期） |
| `New version found: …` [INF] | 资源更新提示 |
| `Integrity check passed` / `ItemListHelper … Loading item list` / `WindowManager` 相关 [INF] | 启动自检/UI 内部 |
| OCR 文本行（如 asst.log `获得信用点30`） | 识别到的画面文本，非事件 |

---

## 6. 已知坑（踩过的）

1. **续行**：多行消息后续行无前缀（`基建换班`、`理智: …`、`请 ｢检查连接设置｣…`、`剿灭模式 : …`）。逐行扫描的工具必须做"续行归属"，否则会把裸行当独立事件（`mas_error_daily`/`auto_mas_archiver` 已按此处理）。
2. **`任务出错: 开始唤醒` ≠ 最终失败**：部分场景后随重试成功；AUTO-MAS 把它单独口径为"未能正确登录 PRTS"并**优先于** `任务已全部完成！` 判定。
3. **`已停止` 语义双关**：掉线/重连失败后是失败；完成后/用户停是正常。判前文。
4. **`截图用时较长` 是 WRN**：常态 400–700ms 都会报（MuMu 常见），**只有持续出现或伴随 `过长/失败` 才处理**。
5. **asst.log 会轮转（64 MiB → `asst.bak.log`）**；gui.log 跨多天。判"这一轮"必须用时间窗（结合 `MaaAssistantArknights GUI started` / `LinkStart Enter` / 任务起止时间）。
6. **UTF-8 与 `Binary file matches`**：个别日志有异常字节，grep 时加 `-a`。
7. **判据是本地化字符串**：本队用 zh-cn；切语言即失效（英文常量除外，见 §1.4）。
8. **无切号行 ≠ 异常**：只有启用了账号切换的脚本才有 `账号切换 -->> `。
9. **`Post actions:` 是计划不是执行**：执行结果看 `Shutdown called by …` / GUI exited。
10. **`可用干员不足`、`代理指挥失误`、`SubTaskError(CreditShoppingTask)` 单次不致命**：不要单条当链失败，链失败只认 `任务出错: <链名>` / asst `TaskChainError`。
11. **`添加任务失败`**（Fight 追加任务失败，AUTO-MAS 判据 `理智作战: <任务名> 添加任务失败`）本批样本未出现，键名未在源码中核到；如巡检命中，先留样本再扩表。

---

## 7. 工具对照（谁在用哪些判据）

| 工具/系统 | 读取 | 使用的判据 |
|---|---|---|
| AUTO-MAS `AutoProxy.check_log` | gui.log tail | `未选择任务` / `任务出错: 开始唤醒` / `任务已全部完成！`(+`完成任务:`/`任务跳过`/`任务出错: 理智作战`) / `请 ｢检查连接设置｣…` / `未检测到任何模拟器` / `已停止` / `MaaAssistantArknights GUI exited` / 超时；剿灭：`剿灭模式 N / M`、`完成任务: 剿灭作战` |
| AUTO-MAS `config.py` 解析 | gui.log | `理智:\s*(\d+)/\d+`（体力）、`公招识别结果/★ Tags/已确认招募`（公招统计） |
| `tools/mas_error_triage.py` | asst.log | `TaskChainError` / `SubTaskError`（只读 MCP `triage_error`） |
| `tools/maa_error_daily.py` | gui.log + asst.log | `任务出错` / `可用干员不足` / `截图失败` / `游戏掉线` / `任务已全部完成` / `任务已完成，但出现错误` / `TaskChainError` / `SubTaskError` |
| `tools/auto_mas_archiver.py` | gui.log | `Start/Completed Task Chain` / `任务出错`（归档统计） |
| `tools/mas_dashboard.py` | gui.log | `Start Task Chain` / `Completed Task Chain` / `任务出错`（看板） |
| `tools/mas_identity_trace.py` / 身份判据 | gui.log | `账号切换 -->> <掩码>` 序列（身份追踪） |
| `tools/mas_startup_verify.py` | gui.log | `Completed Task Chain: StartUp`（启动自检） |
| `tools/mas_switch_gate.py` | 配置（非日志） | 注入闸门；日志侧佐证用 `账号切换 -->>` + asst `SwitchAccount@*` |
| `tools/maa_form_probe*.py` / `maa_mastery_ledger.py` | asst.log | `SubTaskError {details.opers{name,reason}}`（编队/练度） |

---

## 附：本表未覆盖 / 待补

- `理智作战: <任务名> 添加任务失败`（见 §6-11，待样本）。
- `未检测到任何模拟器` 与 `截图用时过长` 本批样本未出现（来自 AUTO-MAS 判据/本地化文件，非实测样本）。
- 台/韩/日/国际服客户端的对应英文/本地化判据（本队全为官/B 服，未采）。
- 肉鸽（Roguelike）链细粒度标记未展开（另有专册 `docs/MAA_ROGUELIKE_PLAYBOOK.md`）。
