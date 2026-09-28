# MAS 总纲：业务全景图 + 交接手册

> 生成：2026-09-28 03:09 CST ｜ 任务 `t_5dcccff5`（板 `maa-parallel-benchmark-20260926`）｜ **只读汇总**（未发车、未改任何配置）
> 本文 = 8 份普查/机制产物 + 当日运维文档的**收口入口**：只给结论与跳线，不复制内容。
> 口径优先级：**现场 > 子文档 > 本文**。时效锚点：多数事实取 2026-09-27 21:00 – 09-28 03:00；其余标注采集时刻。

## 0. 十分钟上手

1. 两台机器：**MAS 主机**（`100.79.173.69` = MRFZ-0000；`E:\AUTO-MAS` 侧车 + `E:\MAA-Multi\MAA_1..12` + MuMu 机队）与**本机跑批机**（`D:\1-2\MAAOrch`：调度器/工具/文档 + Hermes cron）。
2. 三条心法：**只读优先** → 写操作必须**显式 + 可回滚 + 留痕** → **先查文档/知识库再动手**（`D:\1-2\MAS-KB`，技能 wiki-search）。
3. 读表路线：§1（谁是谁）→ §4（一天怎么过）→ §5（别踩什么）；细节按 §2/§3/附录 A 跳。
4. 出事直通：`docs/MAS_EMERGENCY_RUNBOOK.md`（一页纸 + 六个故障首条命令 + 一键回滚）。
5. 版本锚点：MAA **v6.18.0**、AUTO-MAS **v5.4.0**、游戏客户端 **2.7.71**。

## 1. 全景图：账号 → 脚本 → 实例 → 槽位 → 队列 → 班次 → 调度

### 1.1 一条链（总数口径；证据：MAS_SCHEDULE_MAP / MAS_SCRIPTS_CATALOG / MAS_EMULATORS / mas_account_master）

```
60 账号（58 启用 + 2 停用）           ← AUTO-MAS UserData[uid]；掩码↔UID 见 mas_account_master
   └─ 归属于 →
54 脚本（49 产线 + 5 肉鸽 l-*）       ← data/<sid>/…；每脚本绑 1 个模拟器实例索引
   └─ 排入 →
12 个槽位（E:\MAA-Multi\MAA_1..12）    ← 共享池；同目录同时刻只准一个任务（kill 按目录路径）
   └─ 跑在 →
55 台 MuMu 实例（MRFZ-0000）           ← 53 台被脚本绑定 + 2 台非绑定（idx0 碧蓝、idx44 l-3）；空洞索引 9/35/42/47/51/59
   └─ 组成 →
33 条队列（24 定时 = 日常/晚班定时1-12 + 9 条「临时-补跑-*」残留）
   └─ 班次 →
24 个班次（05:00..05:33 / 19:30..20:03，+3min/槽；SHIFT_TABLE = 权威）
   └─ 调度 →
Hermes cron 2600f6415f16（每 3 分钟）→ tools/mas_dispatch.py → REST :36163 /api/dispatch/start
```

### 1.2 四层职责

| 层 | 做什么 | 我们碰的面 |
|---|---|---|
| MaaCore（C++） | 识别 + 任务链执行 + 上报 | 不直接用（黑盒） |
| MaaWpfGui（C#） | 读 `config/gui.new.json` → 跑链 → 写 `debug/gui.log` | 配置注入面 + 判据面 |
| AUTO-MAS（v5.4.0 侧车） | 多号轮换 / 注入配置 / 发车 / history | REST :36163 + `data/<sid>` 注入面 |
| Hermes（本机） | 班次调度 / 巡检 / 补跑 / 取证工具链 | `tools/*` + cron + MCP 工具面 |

### 1.3 12 个产线槽位（脚本（emu idx）；每槽 = 日常定时N + 晚班定时N 一对队列）

| 槽位 | 脚本（emu idx） |
|---|---|
| MAA_1 | 官-23(11)、官-13(45)、官-27(36)、官-37(46)、l-1(25) |
| MAA_2 | 官-2-4-47(14·4号)、l-2(28) |
| MAA_3 | 官-3-搓玉(15)、台-2(32)、官-21(40)、官-20(50)、官-32(58) |
| MAA_4 | b-9(3)、日-2(10)、日-4(12)、官-5(19·停用)、官-41(56)、官-31(57) |
| MAA_5 | 官-22(6)、官-7(17)、官-14(21)、官-9(23)、官-19(33) |
| MAA_6 | 官-1-1-7(13·4号)、官-38(48)　← 已知互杀对 |
| MAA_7 | 官-8(18)、日-1(24)、官-11(26)、官-15(29)、官-18(41) |
| MAA_8 | 官-6(20)、官-16(30)、官-29(49)、日-3(53) ＋未排班：官-25(37·停用)、官-28(51·幽灵引用) |
| MAA_9 | b-2(1)、b-3(2)、b-10(8)、b-1(39)、l-5(34) |
| MAA_10 | 官-33(4)、b-12(5)、b-7(22)、台-1(60)、l-4(31) |
| MAA_11 | b-11(7)、日-6(38)、日-5(54)、官-30(55) |
| MAA_12 | b-5(16)、官-12(27)、官-24(43)、l-7(52) |

> 队列项：每班次 52 个脚本项（12 条队列各 4–6 项；晚班与日常同脚本集、顺序不同）；另 2 个脚本（官-25/官-28）不在任何队列。多号槽：官-1-1-7、官-2-4-47 各 4 号（每轮按序切号）。全表见 `docs/MAS_SCHEDULE_MAP.md` §3。

### 1.4 每天什么时候发生什么（调度视角）

- **04:01 / 19:01** AUTO-MAS 惯性自重启（计划内）；重启会**吞在飞任务** ⇒ 班次窗口内不许再重启（见红线②）。
- **05:00–05:33 / 19:30–20:03** Hermes 错峰发车（12 槽 +3min；cron 每 3 分钟对账，幂等单飞）。
- **05:00–05:40 / 19:20–20:20 守卫窗**：不发车、不补跑、不动配置（`MAS_RERUN_POLICY.md` §3）。
- **05:40 起 / 19:20 起** 巡检每 30 分钟（cron `52b88b0ffca0`，11 行表）；对账持续到追发窗口（30min）耗尽。
- **班次收工**（连续 2 次 0 MAA）→ 日报 + 补跑判定（C3）→ 04:00 前收完晚班缺口。
- **AUTO-MAS 侧 24 条定时全部 `Enabled=false`**：发车改由 Hermes 驱动，定时项仅作回滚备份（看到「定时没执行」别当故障修）。

## 2. 数据字典

### 2.1 实体

| 术语 | 定义 | 口径 |
|---|---|---|
| 账号 / 用户（uid） | AUTO-MAS `UserData[uid]` 条目 | 60 个 = 58 启用 + 2 停用（官-5/v、官-25/Kaori） |
| 脚本（scriptId） | 「明日方舟-<服>-<标签>」，含 1–4 个账号 | 54 个（49 产线 + 5 肉鸽 l-*）；l-* 无 data 目录（肉鸽走 fz-maa 机队） |
| 槽位 | 一个 MAA 程序目录 `E:\MAA-Multi\MAA_N` | 12 个在役；同目录同时刻只准一个任务 |
| 实例（emu idx） | MuMu 虚拟机 | 55 台在役；主 adb 口公式见 §5-B |
| 队列 | 有序脚本项列表（顺序即执行顺序） | 33 条 = 24 定时 + 9 条「临时-补跑-*」残留 |
| 班次 | SHIFT_TABLE 里的一条发车时刻 | 24 条（日/晚各 12） |
| 注入 | `data/<sid>/…/ConfigFile/gui.new.json` → 槽位 `config\gui.new.json` | 简洁模式整包拷贝 + 用户级覆盖 |
| history | `E:\AUTO-MAS\history\<班次日>\<号>\` | 逐次尝试状态（DONE=成功）；跨零点按班次日归档 |

### 2.2 关键字段（改配置前必读）

- `Emulator.Index`：脚本 → 实例索引（权威实例表）。
- `Info.Stage / Stage_1..3 / Stage_Remain`：主关卡 + 回退位 + 剩余理智关（SR-* 药修复即补 `Stage_1=1-7`）。
- `Info.Annihilation`：剿灭（`Close` / `Annihilation` / `LungmenOutskirts` / `LungmenDowntown`）。
- `Info.MedicineNumb / SeriesNumb`：配额药（0=不吃配额药；临期药消耗独立）/ 连战次数。
- `Data.LastProxyDate`：**尝试即写**，不是完成度；完成度看 `Info.Tag`（「日常：已代理N次/未代理」）。
- `Info.IfActivityFirst / IfRoguelike / IfDepotMaintain / IfReclamation`：现网全 false。
- 队列侧：`TimeEnabled`（全 true，保留）/ `StartUpEnabled`（全 false）/ 定时项 `Enabled=false` / `AfterAccomplish`（电源动作）。
- 注入侧（AutoProxy 补丁 v2）：StartUp `AccountName=前3+****+后4`＋`AccountSwitchEnabled=true`（官服）；非官 `""+false`。Fight=StagePlan 去 `-`、MedicineCount、Series；简洁模式强制 `EnableTimesLimit/EnableTargetDrop=false`。

### 2.3 掩码 / 脱敏口径（硬规矩）

- 官服 = 手机号 **前3 + `****` + 后4**（如 `134****5276`）；B/日服无掩码（账号名代位）；台服邮箱 `前3位***@域`。
- 一切报告/台账**只写掩码**；11 位手机号明文模式全库扫描 0 命中；token / hgId **不出设备**（设备侧 grep 只回传掩码/UID，读入即弃）。
- 游戏内 **UID** = 标题页/大厅 `ID:<8-9位>`；设备侧权威副本 = playerprefs `u8sdk_cached_uid`；台账已取 43/60（`runtime/reports/mas_account_master_20260928.md`，17 号待核验已分类注明）。
- 客户端账号清单实存 `USER_CACHE`（`HypergryphSdkPreferences.xml`：掩码 + token + lastLoginTime + hgId；≤10 条、只能框内滚动）。

### 2.4 运行态文件（本机 MAAOrch 侧）

- `runtime/mas_dispatch_state.json`：当天各队列发放记录（单飞幂等键）。
- `runtime/mas_dispatch_log.jsonl`：每次发车尝试一行（ok/fail/deferred + reason）。
- `runtime/mas_dispatch_alerts.json`：未消除告警台账（active/cleared）。
- `runtime/mas_dispatch.lock`：发车临界区锁；`runtime/mas_ledger.jsonl`：动作台账（含 source 标记）。
- `runtime/preflight_shots/`：preflight 截图；巡检输出在 Hermes profile 侧：`%LOCALAPPDATA%\hermes\profiles\mas\cron\output\52b88b0ffca0\`。

## 3. 判据速查

### 3.1 日志判据（全文：`docs/MAA_LOG_DICTIONARY.md` 516 行 / 14 类 / 73 条正则）

| 判断 | 看这条 |
|---|---|
| 成功 | `任务已全部完成！`（部分失败 = `任务已完成，但出现错误！`） |
| 哪条链失败 | `任务出错: <链名>`（尾部有 `本轮任务出现以下错误：` 清单） |
| 没登进游戏 | `任务出错: 开始唤醒`（≠最终失败，可重试成功） |
| 连接/模拟器问题 | `连接失败` / `模拟器断开，正在尝试重连 (N)` / `游戏掉线，停止任务` |
| 切号了吗 | gui.log `账号切换 -->> <掩码>`（每实际切换一行） |
| 吃药 | `已使用 N 天内过期理智药` / `药: N, 库存: M` |
| 被中止/退出 | `已停止`（双关：判前文） |
| 不要管 | Toast 通知不可用 / `Failed to set startup` / `NotModified` / `截图用时较长`（WRN 常态） |

### 3.2 身份判据（防串号）

- 权威阶梯：**帧证**（大厅/连接页 `ID:` + 设备 prefs）＞ **切换行**（gui.log）＞ 无（黄，需补帧）。
- **切号失败是静默的**：目标掩码不在客户端登录记录 ⇒ MAA 在当前号上照跑完、零报错；同号重登也不产生切换行。
- 工具：`tools/mas_identity_trace.py`（watch/ocr/report/selftest）→ `IDENTITY_TRACE=OK|RED:n`；MCP `mas_identity_trail`；巡检验 `IDENTITY=一致/不一致`。

### 3.3 注入合规判据（切号闸门 v2）

- 口径：**官服 = 掩码 + `true`；非官服 = `""` + `false`**（异常一律 fail-closed，UNKNOWN ≠ 通过）。
- `tools/mas_switch_gate.py --check` → 末行 `SWITCH_GATE=OK|VIOLATION:<槽位>|UNKNOWN:<槽位>`；巡检 11 行之一。
- 已知项：`VIOLATION:MAA_10,MAA_12` = 轮末 Temp 还原残留（旧态 false 每晚写回）⇒ 一次性清理项（方案见 `audits/2026-09-27_switch_gate_two_violations_rootcause.md` §6，未执行）。
- 验证注入生效 80 秒法：AutoProxy 单脚本轮 → 读回槽位 `StartUpTask` → 用返回的 task_uid stop；**不要用 ScriptConfig 验证**。

### 3.4 客户端就绪判据 S1–S6（`docs/GAME_CLIENT_MECHANICS.md` §5）

S1 实例活（adb + `sys.boot_completed`，不信 MuMuManager）→ S2 包存在且 versionName=2.7.71 → S3 无死态黑名单（`tools/client_state_check.py`）→ S4 目标掩码 ∈ `USER_CACHE` → S5 `u8sdk_cached_uid` 对台账 → S6 版本一致（强更日人工更包）。
整合命令：`python tools/mas_preflight.py --queue <名>` → `PREFLIGHT=GO/NO-GO`（六项含闸门/磁盘/截图 OCR）。

### 3.5 调度 / 告警判据

- 发车结果：`SENT / LATE / FAIL`；失败分类 `rest_unreachable / http_error / bad_response / no_taskid / target_running / slot_occupied / target_not_found / queue_missing`；退避 60/180/300s（预算 700s）→ 下一跳自愈。
- 告警：`DISPATCH_ALERT=<队列:原因>` / 自愈 `DISPATCH_ALERT_CLEARED=`；超窗（>30min）只记 late 不自动补；资源冲突窗 `conflict_inflight`（busy=150min）。
- 收尾判据：**缺 `任务结束: <id>` 行 = 僵尸/孤儿**；缺「已解锁脚本配置」= 未正常收尾（锁 Temp 残留）。
- 巡检 11 行：`MCP_API_TOOL_COUNT|ISOLATION / SCHEDULE / SWITCH_GATE / CLIENT_STATE / ERR_SUMMARY / DISPATCH_ALERT / IDENTITY / DISK / POWER / WIKI_GOV / 结论`（每行 fail-closed，缺数据写 `UNKNOWN:<原因>`）。

## 4. 流程图：一次班次的完整生命周期

```
04:01/19:01  AUTO-MAS 惯性自重启（计划内；<04:00 必须收完晚班缺口——重启吞在飞任务）
04:50        早班运行窗开始
~04:55       班前哨兵 preflight（建议/未接线）
05:00–05:33  ┌ Hermes cron 2600f6415f16（*/3）→ mas_dispatch --reconcile --apply
             │  闸门链：白名单 → REST 可达(失败 fail-closed) → 单飞(当天未发) → NO_GO 硬闸
             │          → 并发锁 → 资源冲突窗(150min) → POST dispatch/start {taskId=队列uid, mode=AutoProxy}
             └ 每队列当天恰一次；走停即下一跳自愈
05:00–05:40  守卫窗：不发车 / 不补跑 / 不动配置
05:40 起    巡检 cron 52b88b0ffca0（每 30 分钟，11 行表；19:20 起同）；对账持续到追发窗口耗尽
任务内（逐脚本 → 逐用户串行）：
  spawn 前注入(v2 切号) → MAA 启动/登录 → 链：剿灭 → 日常（唤醒/作战/基建/公招/信用/奖励）
  判据：`Start Task Chain` … `任务已全部完成！`；收尾行 `任务结束: <id>`；轮末 Temp 快照还原 + unlock
收工（连续 2 次 0 MAA）→ 日报 → C3 补跑三问 → 守卫窗外补跑（临时队列；同目录同队列；<24h 清残留）
19:30–20:03  晚班重演（同早班；19:20 起巡检）
19:01/04:01  自重启窗口（勿撞）
```

异常分支：

- 单链失败 → C3 三问（会好吗 / 已成功吗 / 超上限吗）；资源型不补、升级人工。
- 截断 / 孤儿（重启吞任务）→ Y3：只补未跑的号（`runtime/mas_ledger.jsonl` kind=rerun 计数）。
- 第三方打进产线窗 → 闸门 `conflict_inflight` 拒发；人工放行 `--force-conflict`（慎）。
- 告警 late / conflict → 人工拍板：补发 `--queue <名> --apply` 或放弃 `--dismiss-late`。
- 闸门/巡检数据缺失 → fail-closed 写 `UNKNOWN:<原因>`，不许沉默通过。

## 5. 已知坑与红线

### 5.1 红线（每次动手前默读）

1. 只读优先；写动作显式 + 可回滚 + 留痕；**同队列同天只发一次**（单飞）。
2. **班次窗口（05:00–08:00 / 19:30–23:00）不重启侧车**——吞在飞任务 = 孤儿化。
3. 同一 MAA 目录同一时刻只准一个任务（互杀按目录路径）。
4. 守卫窗（05:00–05:40 / 19:20–20:20）不发车、不补跑、不动配置。
5. 补跑 ≠ 队列级补发，两个面互不替代、互不抢。
6. `l-*` 肉鸽机队 / 用户手动实例：本轮不许碰。
7. 绝不手改 `ScriptConfig.json` / `QueueConfig.json`（活配置 + DPAPI 密文）——走 :36163 API 或让用户点界面。
8. 密文不经我；token/hgId 不出设备；报告只写掩码。
9. 止损走官方 stop（有 task_uid 才 stop）；**别 kill MAA**；别用 ScriptConfig 模式等任务链（等不到）。
10. 定因以机器证据（日志行 / mtime / 进程命令行 / 机器时间）为准；说错当场更正。

### 5.2 坑清单（今天踩过/确认过的全部）

**A. 调度 / 队列**
- 卡面「28 队列」是旧快照，实测 33（24+9 残留）；9 条「临时-补跑-*」挂产线目录，误发即抢（`--cleanup` 未跟进）。
- 单脚本直发（taskId=scriptId）没有体检层 → 09-27 实测 4 例打进产线窗（C0 锁抢 / C1·C3 重叠 / C2 紧邻），~1.5min 内被中止。
- AUTO-MAS Timer 分钟精确匹配、**无 ±容差**（仅在回滚场景相关）；app 重启杀在跑任务不写收尾行（09-27 早班 12 条仅 2 条有收尾）。
- 「定时没执行」别当故障修：24 条 `Enabled=false` 是设计（Hermes 接管，留作回滚）。

**B. 实例 / ADB**
- MuMuManager 状态面**恒假**（`is_process_started=false`、`adb -v N connect` 恒 -201）；AUTO-MAS `status=1` ≠ 在跑。死活三件套 = VMM 进程 → 监听口 → `adb devices`。
- 端口公式：idx0–31 = `16384+32×idx`；**idx32–60 = `16388+32×(idx−32)`**（旧公式 −1020）；副口 `5555+2×idx`；漂移=「**重建**就漂」不是重启漂；偶发 7555 勿依赖。执行前现查。
- 关实例瞬间的「设备N未在线 / device offline」是噪音；单坏实例刷屏「获取模拟器 N 信息失败」要聚合归因，别报全局灾难。
- 「跑完停在桌面」是设计（PostActions=9 不含关模拟器）；但发车前必须看目标实例画面（残留模态会让 StartUp 必败）。

**C. 切号 / 身份**
- 切号失败**静默**（见 §3.2）；同号重登无切换行；帧证 > 切换行。
- 客户端登录记录 ≤10 条、仅框内滚动、删除确认 MAA 无处理；多号槽可达性受记录条数限制（4 号不可全达）。
- 死态①「登录被取消」（账号面板×触发、不自愈；处置 = `am force-stop` + `am start`）；死态②强更（MAA 零处理器，人工更包）。
- 手改 `AccountSwitchEnabled=true` 的旧做法作废（v2 注入层已接管）。

**D. 配置 / 注入**
- **Temp 快照轮末整份还原 =「勾选/设置消失」真凶**；还原值 ≠ 最后运行的号；`masked+false` 残留会永久复现（MAA_10/12）。
- 改代码/模板后**必须重启侧车**才生效（启动时导入、不热更）；改前 `.bak-*` 备份、改后备注回滚；班次窗口内禁止重启。
- 简洁模式强制 `EnableTimesLimit/EnableTargetDrop=false`；StagePlan 去 `-`。
- 教训（已修）：SR-* 46 号关卡计划闭档 → 「关卡计划内没有当日开放的关卡」→ 主作战整跳 → **临期药无消耗路径**（修复 = `Stage_1=1-7` 回退位；46/46 已验证）。

**E. 日志判据**（11 条详见 `MAA_LOG_DICTIONARY.md` §6）
- 续行无前缀；`任务出错: 开始唤醒` ≠ 最终失败；`已停止` 双关；`截图用时较长` 是 WRN；asst.log 64MiB 轮转；判据是本地化串（zh-cn）；无切号行 ≠ 异常；`Post actions:` 是计划；`可用干员不足` 单次不致命。

**F. 工具 / 环境（本机）**
- 系统代理 `127.0.0.1:7890` 拦内网 → urllib 间歇 502（62 工具仅 1 个显式绕代理）——**未修**；症状 =「curl 好、urllib 坏」。
- 经 MCP 派生的 ssh 子进程：必须 `stdin=DEVNULL` + env 键大写化（PATH/Path 双键 → 裸名 ssh WinError2）——已修（`mas_observe_mcp._env` / `maa_error_daily._child_env`）。
- MCP 工具面热加载只增删不刷新：改完 `enabled false→true` 强制重连（~1min）。
- 劣化通道下全量拉日志不可用（2.34MB >200s）；identity 已加远端预过滤（20MB→0.64MB，27.7s）；PS 读日志需 `FileShare::ReadWrite`（否则静默吞空）。
- 巡检 deliver=local：30 分钟结论**没有推送通道**（G11）。

## 6. 未决问题与路线图

### 6.1 待人工拍板

- `MAA_10 / MAA_12` 闸门违规：清一次即永久合规（受控播种方案已出，未执行；as-of 09-28 01:34 仍 VIOLATION）。
- 官-28（hbznn119-官）：启用但未排班 + 幽灵绑定 idx51（重建实例 ≈+30GB 或停用）；官-25（Kaori）双停；官-5 空转项——建议三件一起拍。
- 9 条「临时-补跑-*」残留清理（`tools/auto_mas_rerun.py --cleanup`，需确认无在跑）。
- `l-7-不要加班了` 路径双反斜杠规范化。
- 04:01/19:01 自重启的去留与窗口纪律。
- 48 个非现存脚本 data 目录被非 API 进程批量写（09-27 19:53:19–20；来源未定）——数据卫生跟进。
- 官-2-4-47 多号轮换全天验证；17 号实机核验补跑（见 `mas_account_master_20260928.md` §3）。
- 肉鸽 `l-*`（无 data 目录、fz-maa 驱动）与产线边界：不补、不碰。

### 6.2 路线图（已落地 / 在途 / 草案）

- ✅ 已落地（09-27/28）：Hermes 发车接管（`2600f6415f16`）；巡检 `52b88b0ffca0`（11 行表）；mas-observe 5 工具 + mas-triage.error_daily 接线（27 工具，t_6cfe7865）；切号补丁 v2；SR-* 药修复 46/46；mas_dispatch 加固（重试/告警/锁/冲突窗）；maa-cli 部署三级验证通过。
- 🔜 G 系列缺口（`docs/MAS_SHIFT_GAPS.md`）：**G2 绕代理（P0）** → G3 补跑判定落地（`MAS_RERUN_TOOLCHAIN_DRAFT.md` 草案）→ G4 preflight 接入（3 方案）→ G5 通道加固 → G7 Y3 缺口检测 → G9/G10/G11/G12。
- 🧭 目标态：MCP 五工具（`MCP_TOOL_ROADMAP.md` §2）；受控场景发车（`MAS_SCENARIO_DISPATCH.md`）；多路径分工（`MAA_DRIVER_OPTIONS.md`：AUTO-MAS 注入主力 / maa-cli 无头补充 / MaaFw 绑定远期）。
- Wiki 治理：`WIKI_GOV` 巡检行待接线（卡 t_c8b36f5e，blocked）。

## 附录 A：文档地图（按主题）

**普查 / 机制 8 件套（本文的直接来源）**

| 文档 | 一句话 |
|---|---|
| `runtime/reports/mas_account_master_20260928.md`(.json) | 60 账号/54 实例名册 + 掩码↔UID 基线（43/60）+ 实机证据帧 |
| `docs/MAS_SCRIPTS_CATALOG.md` | 54 脚本逐档（注入值指纹/7 天成功率/失败分布）+ 差异即洞察 |
| `docs/MAS_SCHEDULE_MAP.md` + `runtime/reports/mas_schedule_20260927.json` | 33 队列/槽位/冲突面/差异 D1–D9 |
| `docs/MAS_EMULATORS.md` | 55 台实例逐台 + ADB 端口修正 + 幽灵 idx51 |
| `docs/MAS_INTERNALS.md` | TaskManager/Timer/模拟器/配置/WS/签到通知六链路（含行号钩子） |
| `docs/GAME_CLIENT_MECHANICS.md` | USER_CACHE/死态/冷启动/发车前 S1–S6 |
| `docs/MAA_CAPABILITY_MAP.md` | MAA v6.18.0 能力全景（19 任务类型/识别/上报/可编排性） |
| `docs/MAA_LOG_DICTIONARY.md` | 日志判据字典（14 类 / 73 正则 / 噪声清单 / 11 坑） |

**运维 / 专项**

| 文档 | 用途 |
|---|---|
| `docs/MAS_SHIFT_PLAYBOOK.md` / `docs/MAS_SHIFT_GAPS.md` | 标准班次 6 步剧本 / 自动化缺口（G 系列） |
| `docs/MAS_EMERGENCY_RUNBOOK.md` | 应急预案 + 一键回滚（§2 回滚清单） |
| `docs/MAS_RERUN_POLICY.md` + `MAS_RERUN_TOOLCHAIN_DRAFT.md` | 补跑规则（C3 三问）/ 判定工具化草案 |
| `docs/MAA_ROGUELIKE_PLAYBOOK.md` / `docs/MAA_EVENT_FARMING_PLAYBOOK.md` | 肉鸽发车 / 活动·剿灭·仓库维护 |
| `docs/MAA_TASK_CONFIG_REFERENCE.md` | gui.new.json 全字段手册（注入视角） |
| `docs/MAA_DEPOT_MAINTAIN.md` | 库存保持逻辑与用法 |
| `docs/MCP_TOOL_ROADMAP.md` | 工具面扩展（§9 = 09-28 落地记录） |
| `docs/MAA_DRIVER_OPTIONS.md` / `MAA_CLI_SURVEY.md` / `MAA_CLI_DEPLOY.md` | 驱动路径取舍 / maa-cli 勘察 / 部署验证 |
| `docs/MAS_STORAGE_INVENTORY.md` + `audits/2026-09-27_storage_policy.md` | 存储水位与保留策略 |
| `docs/HERMES_MAS_HANDOVER.md` | Hermes 接管 AUTO-MAS 交接契约 |
| `docs/REGRESSION.md` | 回归测试套件：怎么跑 / 覆盖表 / 失败 triage（`tools/tests/test_regression.py`） |
| `docs/PERF_BASELINE.md` | 性能基线（单号日耗时 / 启动首链 / 截图 / 链级 P50-P90）+ `perf_baseline.py --check` 回归检测 |
| `docs/audits/` 关键 5 篇 | switch_gate_two_violations_rootcause / medicine_fix_20260928 / mas_observe_wiring_t_6cfe7865 / gate_wiring_check / dispatch_hardening |

**工具（本机 `tools/`）**：调度 `mas_dispatch.py`（--reconcile/--list/--check/--busy/--check-schedule/--alerts）｜闸门 `mas_switch_gate.py`｜预检 `mas_preflight.py`｜客户端 `client_state_check.py`｜身份 `mas_identity_trace.py`｜空转 `mas_idle_emulators.py`｜错误 `maa_error_daily.py`｜补跑 `auto_mas_rerun.py`（+MCP `mas_rerun`）｜账号 `mas_accounts.py`｜回归 `tools/tests/test_regression.py`（默认离线；`--live` 实机只读）｜性能 `perf_baseline.py`（基线/`--check` 超阈=退出码 3）。

## 附录 B：数据面 / 复现

- AUTO-MAS REST `http://100.79.173.69:36163`（OpenAPI 副本 `_ams.json`）；只读端点 `/api/scripts|queue|user|item|time|plan|history|overview`。
- SSH `ssh -i ~/.ssh/maaorch_target Administrator@100.79.173.69`（查询类走 base64 EncodedCommand，避免引号假读数）。
- MAA 侧只读面：`E:\MAA-Multi\MAA_N\debug\{gui.log,asst.log}`、`config\gui.new.json`、`E:\AUTO-MAS\debug\app.log`。
- 巡检工具面 = 27 个 MCP 工具（monitor10 / daily6 / dispatch4 / observe5 / triage2）。
- 快照 JSON：`runtime/reports/*.json`（schedule/account/errors）；证据帧：`docs/img/game_client/`、任务工作区 `shots/`。
