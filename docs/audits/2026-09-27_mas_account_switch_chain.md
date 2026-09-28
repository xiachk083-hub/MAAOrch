# MAA 账号切换（AccountSwitch）全链路归档 —— AUTO-MAS 注入 → MAA 6.18 行为（只读研究）

> 2026-09-27 ｜ 执行：mas（kanban t_c5a92e8f）｜ **只读**：未改任何配置、未发车、未动实例；仅读主机文件/日志 + MAA 官方源码。
> 行号口径：`AutoProxy.py` = 线上 `E:\AUTO-MAS\app\task\MAA\AutoProxy.py`（含 05:03 热修，备份 `.bak-accswitch-20260927-050305`）；MAA 源 = tag **v6.18.0**（被引文件与 dev-v2 逐字节一致）；日志 = `E:\MAA-Multi\MAA_6\debug\{gui,asst}.log(/.bak)`。

## 0. 结论摘要

- 切号总开关 = StartUp 任务参数 `AccountName`（目标登录名）：**非空 ⇒ 启用切号；空 ⇒ 整个切号任务禁用并静默跳过**（core 侧 `enable=!account_name.empty()`，StartUpTask.cpp:96）。
- AUTO-MAS 原版只注入 `AccountName`、**从不写 `AccountSwitchEnabled`**（源里显式 `false` 原样透传）⇒ MAA 判定禁用 ⇒ 日志 "task disabled, pass"，**不报错、不切号、用当前已登录号继续** —— 这就是官服多号串号的机制根因。
- 开关打开后二分：能切 ⇒ 成功回报；**目标号不在客户端登录列表（或导航失败）⇒ 失败**（详见 §3）。
- 05:03 修复两处：①`AutoProxy.py` +4 行（L774-777：按 AccountName 有无自动开/关）；②源配置批量补写（58 个 `.bak` 备份，样例 fdbf292f false→true 实锤）。
- ⚠️ **热修尚未生效**：AUTO-MAS 后端（PID 14456, :36163）自 04:01 启动、未重启；05:20 新用户（130****9165）的注入**仍写 `AccountSwitchEnabled: false`**。需重载后端才会切号。

## 1. 全链路（源 → AUTO-MAS 注入 → MAA → 回报）

```
[源配置] 落点①(05:03 批量)
  ｜ 简洁: data\<脚本id>\Default\ConfigFile\gui.new.json
  ｜ 详细: data\<脚本id>\<uid>\ConfigFile\gui.new.json
  ▼
[AUTO-MAS] 每用户每次尝试:
  ① shutil.copytree 源 → E:\MAA-Multi\MAA_N\config\        (AutoProxy.py:606-621)
  ② set_maa 字段手术 (:592):
       Official → AccountName = Id[:3]+"****"+Id[7:]        (:767-771)
       Bilibili → AccountName = Id                          (:772-773)
       【热修】AccountSwitchEnabled = bool(AccountName)     (:774-777)
  ③ write_file config\gui.new.json                          (:932；gui.json 仅旧格式兼容 :928)
  ④ open_process MAA.exe                                    (:544)
  ▼
[MAA v6.18.0 GUI] 读 config\gui.new.json：
  开关=true 且 客户端∈{Official,Bilibili,Txwy,KR} 且 非PC连接 ⇒ 参数 account_name=掩码；否则 ""   (StartUpSettingsUserControlModel.cs:94-99)
  AsstStartUpTask.cs:42-44 account_name “留空以禁用”
  ▼
[MaaCore StartUpTask] set_params: enable=!account_name.empty()   (:96)
  run(): 启动游戏(:39) → AccountSwitchTask(:47；禁用时打印 "task disabled, pass"，出处=基类 AbstractTask.cpp:34)
    ├ 成功 → 进首页导航 {StartAtHome/StartWithSanity/…} retry50 (:20-25) → GUI「完成任务: 开始唤醒」
    └ 失败 → start_game 关 ⇒ 直接失败 (:54-56)
              start_game 开 ⇒ 重启客户端循环 ≤MaxRestartAttempts=5 (:59-78, .h:27)
              → 全败 ⇒ GUI「任务出错: 开始唤醒」
  ▼
[AUTO-MAS check_log] “任务出错: 开始唤醒” → status="MAA 未能正确登录 PRTS" (:967)
  → kill MAA → 该用户重试（≤RunTimesLimit, :486）→ 全败 ⇒ 用户状态=异常
```

## 2. AccountSwitchTask 内部（core 怎么切号，AccountSwitchTask.cpp）

- :26-29 客户端∈SupportedClientType 否则报错返回 false。支持面 {Official, Bilibili, txwy, YoStarKR}，EN/JP 源码注释未启用（.h:42-46）。
- :32-34 导航至账号选择/管理页（子任务 `SwitchAccount@StartUpBegin`，**retry 30**，:65-84；接受登录页/账号管理页终点）。
- :39-48 当前号 OCR == 目标 ⇒ 直接点管理页登录按钮（retry 3，:111-114）。
- :50-52 否则展开列表 → `swipe_and_select()`（下滑边滑边 OCR）**||** `swipe_and_select(true)`（回顶再一遍）。
- :123-143 每轮最多扫描 21 次（`repeat++ > 20`）；找不到 ⇒ false（源码注释「没找到对应账号」）。
- :150-169 命中判定 = `OCRer.set_required(mask)` **子串匹配**，命中即点行、点登录；:52-56 成功后回调 `SubTaskExtraInfo{what:"AccountSwitch", account_name=<点中行文本>}` → GUI 打印「账号切换 -->> <掩码>」（StartUpSettingsUserControlModel.cs:69-71）。

## 3. 「目标号找不到 / 切号失败」行为（卡片问题 3）

| 条件 | core 行为 | 用户可见 | 业务后果 |
|---|---|---|---|
| 开关关 / AccountName 空 | "task disabled, pass" 静默跳过 | 无任何账号相关日志 | **当前号继续跑 ⇒ 串号**（09-24..26 实测 ×25 条，asst.bak.log） |
| 开关开 + 找不到号 | 导航≤30 + 滑扫 ≤21×2 全 miss → false → 重启客户端≤5 轮 → 仍败 → false | 「任务出错: 开始唤醒」 | AUTO-MAS 判「MAA 未能正确登录 PRTS」→ kill+重试 → 全败=异常 |
| 开关开 + start_game 关 | 切号 false 即整体失败（不重启） | 同上 | 同上 |
| 不支持客户端 | "unsupported client" → false | 核心错误日志 | 同上 |

实测时间线（「账号切换 -->>」→ 任务结论；**该行=核心完成选行+点登录**，后续=进首页导航）：

| 期间 | 次数 | 结果 | 间隔 |
|---|---|---|---|
| 09-16..09-22（gui.bak.log） | 29 | 全 OK | 63.1–171.8s |
| 09-23（gui.log） | 8 | OK | 47.8–371.7s |
| 09-23 | 3 | **ERR** | 175.6 / 179.6 / 175.8s（均在「-->>」打出后失败） |

- 09-23 三次失败的精确内部路径已不可复盘（当日 core 日志轮转），但「-->>」已打出 ⇒ 失败点在**点登录之后**（导航阶段）。「日志说成功但实际没换号」有上游先例（issue #17845「账号切换失败，未能正确切换账号」）——最终以账号自查为准。
- 静默型失败（开关关）无任何报错/超时；有界重试仅存在于开关开的情况；AUTO-MAS 侧另有进程超时兜底「MAA 进程超时」（check_log:1006）。

## 4. 客户端支持与匹配规则

- 核心支持 = {Official, Bilibili, txwy(繁中), YoStarKR}（AccountSwitchTask.h:42-46；EN/JP 注释掉）；GUI 侧同为 {Official, Bilibili, Txwy, KR} 且排除 PC 模式（StartUpSettingsUserControlModel.cs:94-99）。
- **只能切到「已登录列表」里的账号**，输入内容需在列表唯一；匹配为 OCR 子串（官方文案 startup.md：「仅支持切换至已登录的账号…可输入 123、8901、3****8」）。
- 官服注入的掩码=`Id[:3]+"****"+Id[7:]`（AutoProxy.py:767-771），恰好与列表行渲染一致，可被子串匹配命中。
- MAA 6.14.0-b2 起对**缺键**的历史配置做一次性迁移 `AccountSwitchEnabled ??= AccountName 非空`（WPF StartUpTask.cs:28-33）——「缺键+有名」会变开启；现网磁盘值均为显式 false/true。

## 5. 二进制 / 资源实证

- `MaaCore.dll`：`AccountSwitchTask`、`::_run`、`::select_account`；`MAA.dll`：`AccountSwitchEnabled`、`AccountSwitchManualRun`、`AccountSwitchTip`（本地 strings 实测）。
- `E:\MAA-Multi\MAA_6\resource\template\WakeUp\AccountManager\` 共 12 张模板：AccountManager(.png)/ListAccount(.Bili)/Official/TrashButton(.Bili)/ExitOld(.Confirm)/Logout(.Confirm)/Settings/UserCenter.png。
- `asst.bak.log` 实测 25 条 `"class":"asst::AccountSwitchTask" … "task disabled, pass"`（taskchain=StartUp；L11489…L389651，09-24 20:25 → 09-26 21:15，即 MAA_6 两个脚本每次运行的常态）。
- 运行中配置实证：`MAA_6\config\gui.new.json`（05:20 写入）`AccountName=130****9165` + `AccountSwitchEnabled=false`。

## 6. 不确定项 / 待验证

1. **热修生效条件=重启 AUTO-MAS 后端**（PID 14456 未重启；05:20 仍写 false）。若 19:00 班前不重启，预计仍不切号。
2. 09-23 三败的「重启循环是否曾介入」无法实锤（core 日志轮转）；仅知失败点晚于「-->>」。
3. 源配置改动面 58 处 `.bak`（Default\ConfigFile 为主 + 部分 Temp）未逐一核对；是否覆盖 <uid>\ConfigFile「详细模式」源未验证。
4. 样例源 fdbf292f 不在当前 54 脚本清单（疑退役/孤儿目录），仅作前后对照引用。
5. 09-24..26 每日 ~05:16→05:19「任务出错: 开始唤醒」（开关关闭期）与本链无关，属其它苏醒失败模式（归 t_73638292 失败模式库）。
6. `Temp\` 副本用途未考证（运行读取口径为 Default\ConfigFile / <uid>\ConfigFile）。

## 7. 复核用命令（只读）

- `findstr /n "账号切换" E:\MAA-Multi\MAA_6\debug\gui*.log`（成功切号回显）
- `findstr /n "task disabled, pass" E:\MAA-Multi\MAA_6\debug\asst*.log`（禁用态实证）
- `findstr "AccountSwitchEnabled" E:\MAA-Multi\MAA_N\config\gui.new.json`（任意槽位当前开关）
