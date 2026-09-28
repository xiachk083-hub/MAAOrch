# mas_run_profile.py —— 按 profile 一次发车（工具说明）

> 机制与字段口径的**唯一权威**是 [`MAS_RUN_PROFILE.md`](MAS_RUN_PROFILE.md)（§2 需求→字段映射、§3 注入语义、§4 八步时序、§5 三级证据、§6 陷阱）。
> 本文只讲**这个工具怎么用、做了什么、哪里与父方案有出入**。所有行号（如 `:834-836`）指 AUTO-MAS 远端源码。

---

## 0. TL;DR

```bash
# 1) 复制模板改字段（先 dry-run，零写）
cp runtime/templates/run_profile.example.json runtime/templates/<场景>.json
python tools/mas_run_profile.py --profile runtime/templates/<场景>.json            # DRY-RUN：打印将做的改动/将调的接口
# 2) 确认无误再真发车
python tools/mas_run_profile.py --profile runtime/templates/<场景>.json --apply
```

- **默认 dry-run**；`--apply` 才写。写=①备份→②写配置（临时计划表）→③preflight→④发车→⑤读回校验→⑥等完/超时→⑦还原→⑧审计。
- **读回不符 ⇒ 自动止损**（`dispatch/stop`，走 stop 才触发 unlock+Temp 还原）+ 立即还原 + 非零退出。
- **失败也还原**（try/finally）；还原不成功会显式告警 + `rc=4`，并在 `--status` 里留 open 记录。
- 退出码：`0` 成功 ｜ `2` 拒绝/发车前失败（已回滚，配置干净）｜ `3` 发车后失败（读回/超时/传给 MAA 的参数不符；已还原）｜ `4` **还原失败（有残留！）**。
- 计划从哪来：`tools/mas_plan_builder.py`（干员箱 + 仓库 + 能力台账 + 活动 → 计划草案，同时产出**本工具 schema 的 profile**，可直接喂进来执行）——见 [MAS_PLAN_BUILDER.md](MAS_PLAN_BUILDER.md)。

---

## 1. 为什么需要它（而不是手点界面）

同一条「用指定配置替某个号跑一趟」，界面要点 5 处、跨 2 层（用户配置 / 计划表），而且：

- 未请求的键会被**静默改写**（§2 通用注记）⇒ 必须"当日生效值全量 + 本次差分"；
- 跑中**配置锁定**，只能跑前写、跑后还原（§6-2）；
- 写完还可能被 MAA 槽位的 Temp 快照覆盖（§6-1）；
- 「只刷 N 次」「目标掉落」「临期药」**没有字段**，硬写=白写（§2 行 4/6/8）。

所以本工具的取舍是：**能写的写、不能写的拒绝并解释、写完全链路读回、结束（或失败）必须还原**。

---

## 2. profile 字段

| 字段 | 必填 | 取值 | 落到哪 |
|---|---|---|---|
| `target.script` | ✅ | 脚本名或 uid（如 `明日方舟-官-12`） | 解析出 `sid / 目录(=MAA槽位) / 实例号` |
| `target.account` | 单号必填 | 账号名（脚本内唯一） | 决定写哪个号；脚本多号见 §4 |
| `stage_plan` | 可缺 | 1–4 个关卡码，`-`=空位，`*`=当前/上次 | 计划表 `Stage/Stage_1/Stage_2/Stage_3`；**全量语义**：少于 4 个时未给的位清成 `-` |
| `series` | 可缺 | `"0"｜"1".."6"｜"-1"` | `SeriesNumb`（**连战倍数/代理倍率，不是总次数**） |
| `medicine.use` / `.count` | 可缺 | bool / 0–9999 | `MedicineNumb`（`use=false ⇒ 0`）；注入成 `UseMedicine/MedicineCount` |
| `medicine.use_expiring` | 可缺 | bool | **不会写**（两层无键，§2 行 6）⇒ 只读回记录实际值 + 告警 |
| `activity_priority.enabled` / `.medicine` | 可缺 | bool / 0–9999 | `Task.IfActivityFirst` / `Task.ActivityMedicineNumb`（enabled 时才写药量；只开开关也不动别的 Task 键） |
| `target_drop.*` | 可缺 | `enabled=true` ⇒ **拒绝** | 无字段（§2 行 8；简洁模式每轮硬关 `:836`） |
| `keep_remain_sanity_task` | 可缺（默认 true） | bool | true=`Stage_Remain` 复制当日值；false=`"-"`（不挂「剩余理智」条目） |
| `restore_after` | 可缺（默认 `run_end`） | `run_end` ｜ `timeout:600s` | ⑥ 的等待上限；超时 ⇒（默认）`dispatch/stop` ⇒ 还原 |
| `notes` | 可缺 | 随便写 | 审计里留着看 |
| `_*` 开头的键 | —— | —— | 注释（模板自用），校验时跳过 |

**主动拒绝（不是静默忽略）**：`total_times / times / times_limit / total / count / max_times`（§2 行 4，无字段）、`target_drop.enabled=true`、白名单外的顶层/子键（`Stage_1` 大小写敏感，错键在服务端是 `AttributeError`/500，§6-13）。

---

## 3. 八步时序的落点（与 §4 逐条对应）

| 步 | 本工具做了什么 | 关键实现 |
|---|---|---|
| ① 备份 | `runtime/run_profile/backups/<run_id>/{meta,plans_before,accounts_before,expected,hashes}.json`；计划表全量 + 该号 Info 白名单子集 + `Task` 子集 + 7 键当日生效值；每份带 sha256 | 白名单之外（`password/token/skland…`）**不读不落**；`state.json` 先落盘再写配置（崩溃可 `--restore-run` 补） |
| ② 写配置 | **默认：新建临时计划表** `plan/add{MaaPlan}` → `plan/update{Info:{Name:"临时-按需-<yyyymmdd_HHMM>",Mode:"ALL"}, ALL:{7 键=当前值+差分}}` → `user/update{Info:{StageMode:planId}}`；**同脚本多号 ⇒ 每号一张表**（各用各的当日值，绝不把甲的值塞给乙） | 计划表 schema `extra=forbid`（§6-13），payload 只含 7 键；`StageMode` 校验器要求目标存在 ⇒ 先 add 再指（`config.py:412-419`）；写后**立即 API 读回**核对（计划表 7 键 + StageMode + 停用号的 Status），不符⇒回滚 |
| ②b 备选 | `--write-mode fixed`：**只对 `StageMode=Fixed` 的号**，直接写它的 7 键（先例 `tools/mas_stage_fix.py`），还原时回写 7 键 | 计划表模式的号会被拒（要改表，本工具不改存量表） |
| ③ preflight | `mas_preflight.py --script <名> --json`；`GO` 才继续；`NO-GO`/无法判定 ⇒ 回滚 ② 后退出（fail-closed） | 槽位忙/实例 adb 不通/有残留模态都在这一步挡住 |
| ④ 发车 | `dispatch/start{taskId:<脚本uid>, mode:"AutoProxy"}` ⇒ 记下返回的 `task_uid`（stop 用它） | 被拒（如「已在运行」）⇒ 回滚退出（`rc=2`） |
| ⑤ 读回 | 轮询 `<MAA目录>/config/gui.new.json`（SSH 读、base64、含 mtime），断言 §5 期望值：`StagePlan/Series/MedicineCount/UseMedicine/IsEnable` + 「剩余理智」条目 + 简洁号的 `EnableTimesLimit/EnableTargetDrop=False`；**时效门**：文件 mtime 必须晚于发车前快照，否则不算通过 | 归属：先按值匹配，多号同值时用 `StartUp.AccountName` 掩码（官服 `前3+****+后4`）定位"当前活跃号"（§6-7）；剿灭段（`剿灭作战`）会标注后继续等（§6-8）；不符 ⇒ `dispatch/stop` + 还原 + `rc=3` |
| ⑤b L2 | 读 `<MAA目录>/debug/asst.log` 尾部 `append_task Fight {...}`：核 `stage/series/medicine`。默认**只记录**（`--strict-l2` 才据此止损） | 归因限制：asst.log 只保留约 2 天、且是多号共享目录，所以默认当参考证据不当判据（§5 三级证据的取舍，见 §6 诚实清单） |
| ⑥ 等完 | 轮询 app.log 的 `任务结束:/中止任务: <task_uid>`；或目标目录 MAA.exe 连续两次消失 ⇒ 判结束。超时（`restore_after`/`--max-wait-seconds`）⇒ 默认 `dispatch/stop` 再等解锁 | 按 §4-⑥ 的"停要停得干净"：stop 才会触发 unlock+Temp 还原（§6-2），**绝不 kill MAA**；04:00/19:00 宿主重启窗口会告警 |
| ⑦ 还原 | `StageMode` 回写原值 → 删临时计划表（`plan/delete` 会把仍指向它的号自动改回 `Fixed`，§6-12：**先回写再删**）→ `Task` 键回写 → 停用号的 `Status` 回写 → API 读回校验；被锁则退避重试（默认 12×30s，`--restore-attempts/--restore-gap-seconds`） | 幂等，可 `--restore-run <run_id>` / `--reconcile` 补 |
| ⑧ 审计 | `runtime/mas_run_profile_log.jsonl`（逐事件：plan/backup/write/preflight/dispatch/readback/l2/wait/restore/done）+ `runtime/mas_ledger.jsonl`（一行 `kind=run_profile` 结论） | 全部脱敏（白名单字段 + `scrub()`） |

---

## 4. 多号脚本（§7-4 的落地）

发车粒度只到**脚本/队列**（`task_manager.py:305-316`），"只跑一个号"没有直接入口，临时队列也救不了（同脚本多号折叠成一条）。所以：

- 脚本**只有一个启用号**：直接发（也不建临时队列）。
- 脚本**多个启用号**：
  - `--account <名>` 且还有其它启用号 ⇒ **默认拒绝**，并给出两条明路：
    - `--disable-others`：临时把这些号 `Status=False`（快照 + 结束时还原）⇒ 只有目标号会跑；
    - `--all-accounts`：接受整脚本跑（每号各自一张临时计划表，读回按掩码逐号确认）。
- `target.account` 指向**未启用**号（`Status=False` 或 `RemainedDay=0`）⇒ 拒绝（AutoProxy 会跳过它，工具不改这些开关）。

---

## 5. 运维命令

```bash
python tools/mas_run_profile.py --profile <p>.json [--apply]      # 主命令（dry-run / 真发车）
    --account <名> --all-accounts --disable-others                # 多号脚本的显式选择
    --write-mode plan|fixed                                       # 写路线（默认 plan=临时计划表）
    --readback-timeout 300 --poll-seconds 15                      # ⑤ 轮询
    --max-wait-seconds 10800 --stop-on-timeout/--no-stop-on-timeout  # ⑥（run_end 的上限）
    --strict-l2                                                   # 把 L2 差异也算失败
    --restore-attempts 12 --restore-gap-seconds 30                # ⑦ 退避重试
    --preflight                                                   # dry-run 时顺带预览 preflight（只读）
python tools/mas_run_profile.py --status                          # 历史 run 状态（含未还原告警）
python tools/mas_run_profile.py --reconcile [--apply]             # 崩溃后补还原（任务已结束/从未发车才动）
python tools/mas_run_profile.py --restore-run <run_id> --apply    # 手动还原某条
python tools/mas_run_profile.py --list-plans                      # 列出计划表（标出「临时-按需-*」遗留）
python tools/mas_run_profile.py --selftest                        # 离线回归（假传输，零触网）
```

闸门：`MAAORCH_DISPATCH_NO_GO` / `MAAORCH_RUN_PROFILE_NO_GO` 置位 ⇒ 一票否决；`runtime/mas_run_profile.lock` 互斥（长跑期间心跳续期，`--status` 可看）。
env 覆盖：`MAAORCH_MAS_API`、`MAAORCH_SSH_KEY`、`MAAORCH_SSH_HOST`、`MAAORCH_PY312`。

---

## 6. 与父方案（`MAS_RUN_PROFILE.md` §4/§5）的差异 · 诚实清单

1. **⑥ 完成判据用 app.log（`任务结束:/中止任务: <task_uid>`）+ MAA.exe 消失**，没有解析 `gui.log` 的任务链（`完成任务: X` 更贴近 §5-L3，但要多一次远端解析；app.log 的 task_uid 更直接、与 stop 用同一个 id）。L3 行为锚点（`GetFightStage: selected …`）**未实现**。
2. **L2 默认不当判据**：`asst.log` 只留约 2 天且多号共享，用它止损会误伤；要严格请 `--strict-l2`。
3. **磁盘读回（`ScriptConfig.json`）未做**：接口读回是运行时权威；磁盘在"写后未再跑"期间是 pending-flush 态，读它只会误导（`mas_stage_fix.py` 的口径）。
4. **多号 `--all-accounts` 会建多张临时计划表**（一账号一张）。父方案说"新建临时计划表（单数）"，但把一张表指给多号=用甲的值覆盖乙 ⇒ 违背"未请求键复制各自当日值"，故按号建表。
5. **`stage_plan` 全量语义**：父方案说"7 键全量+差分"；对 4 个关卡位本工具按"你给几位就写几位、剩下清 `-`"（要保留旧候选就把它们写全）。这是**有意的**：候选位是"本次请求"的一部分，混着旧值会让"刷 X"变成"刷 X 或旧候选"。
6. **重试循环**（§6-16）：一次 run 内 MAA 重试会重新注入，读回可能看到同一值被重写多次 ⇒ 本工具取"首次匹配"即通过，不要求唯一。
7. **不碰**：不改存量计划表、不动与此无关的 Info/Task 键、不 kill 进程、不改队列/定时。

---

## 7. 自测与实机验证

### 7.1 离线回归（`--selftest`，假传输、零触网）

29 个用例，覆盖：profile 校验红灯（`total_times`/`target_drop`/`series` 越界）、dry-run 零写、多号脚本"必须显式"拒绝、未启用号拒绝、**计划表全量复制+差分**（含 Weekly 计划表的当日值复制）、`keep_remain=false` 语义、apply 全链路（建表→指号→发车→读回→结束→还原→审计/备份不落凭据）、读回不符止损、读回**旧文件时效门**、简洁号 `EnableTimesLimit/EnableTargetDrop` 断言、详细号同键容忍、掩码归属、preflight NO-GO 回滚、超时停+还原、strict-l2、fixed 路线及其拒绝、锁互斥、NO_GO、发车被拒回滚、对账还原、status/list-plans。

### 7.2 实机（真发车一次最小验证）

见 §7.3（本次验证记录）。验证目标：**注入确实生效（读回证据）** + **结束时无残留（StageMode/计划表/Task 全还原）**。

### 7.3 验证记录（2026-09-28 · 实机最小真发车一次）

**目标**：`明日方舟-官-6 / 许大胖子`（简洁、StageMode=Fixed）→ MAA_8 / 模拟器 20。`run_id=rp_20260928_074523_--6`。
选窗：MAA_8 的「日常定时8」队列 07:37:35 任务结束（宿主 app.log），白天无后续排班；首选 官-28 因 emu 51 在 MuMuManager 无有效记录被 preflight 否掉（NO-GO，**未发车**，回退无痕）。
实跑 profile：`docs/audits/2026-09-28_mas_run_profile_live.profile.json`（1-7 / Series=1 / 不用药 / `timeout:600s`）。

时间线（本地钟；宿主钟约慢 24s）：

| 时刻 | 步骤 | 证据（文件/锚点） |
|---|---|---|
| 07:45:24 | ① 备份（含 sha256） | `runtime/run_profile/backups/rp_20260928_074523_--6/{meta,plans_before,accounts_before,expected}.json` |
| 07:45:24 | ② 建临时表 `bafe3d84`「临时-按需-20260928_0745」+ `StageMode` 指过去（写后 API 读回 OK） | jsonl `plan_created`；宿主 app.log 07:45:00「更新用户配置: 17f02716…」 |
| 07:45:30 | ③ preflight `GO` | jsonl `preflight` |
| 07:45:30 | ④ 发车 AutoProxy，`task_uid=bf40d8d6` | 宿主 app.log 07:45:06「创建任务: bf40d8d6…模式: AutoProxy, 触发来源: manual_task」 |
| 07:51:51 | ⑤ L1 读回通过（gui.new.json mtime=07:51:19） | `StagePlan=['1-7'] Series=1 MedicineCount=0 UseMedicine=false IsEnable=true EnableTimesLimit/EnableTargetDrop=false`（polls=25） |
| 07:46:46 / ~08:00 | ⑤b L2：core 实收 | 首段剿灭 `LungmenDowntown@Annihilation`（该号自己的 Annihilation 配置，§6-8 预期）→ 日常段 `append_task Fight {"stage":"1-7","series":1,"medicine":0,…}`；`analyze_sanity_cost → sanity_cost: 6`（=1-7 消耗）、`series: 1`，`Fight@StartButton1` 已点「开始行动」 |
| 08:01:53 | ⑥ 等超时(600s) ⇒ `dispatch/stop` | 宿主 app.log 08:01:29「中止任务: bf40d8d6」→ 08:01:55「已解锁脚本 b0e6228c」→ 08:02:13「任务结束: bf40d8d6」 |
| 08:02:39 | ⑦ 还原 ok（attempts=1） | jsonl `restore`；独立 REST 复核见下 |
| 08:02:39 | ⑧ 审计 | `runtime/mas_run_profile_log.jsonl` + `runtime/mas_ledger.jsonl` |

**还原复核（REST 直读，独立于工具自述）**：`StageMode` 已回 `Fixed`；账号 7 键与备份**逐字段一致**（`SeriesNumb "0" / Stage "SR-7" / Stage_1 "1-7" / Stage_2 "-" / Stage_3 "-" / Stage_Remain "1-7"`）；`Task{IfActivityFirst,ActivityMedicineNumb}` 原样；计划表共 5 张、**无**「临时-按需-20260928_0745」及任何「临时-*」残留；MAA_8 目录 MAA.exe=0；`runtime/mas_run_profile.lock` 已释放；`--status`=restored。

**rc=3 属预期**：`restore_after=timeout:600s` 是限时验证（600s 内任务未自然结束 ⇒ 走设计内「stop 止损 →还原」路径）；rc=3=「发车后失败（超时；已还原）」，不是缺陷。要跑到自然结束请用 `restore_after=run_end`。

完整证据：`docs/audits/2026-09-28_mas_run_profile_live.{profile.json,dryrun.txt,run.log,host_evidence.txt,l2_core.txt}`。
