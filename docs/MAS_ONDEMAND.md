# 按需发车（`tools/mas_ondemand.py`）

> 一句话发车：**队列 / 脚本 / 单号 / 唤醒 / 停任务 / 看在跑** —— 任何 Hermes 会话、巡检任务、
> 看板 worker 都能直接调用，不用手搓 REST，也不用临时拼队列。
>
> 定位：**窄面工具 + 默认干跑 + 闸门全开**。真发车要显式 `--apply`；每一步（含被拒）都留审计。

---

## 1. 与既有件的关系（别重造）

| 件 | 位置 | 关系 |
| --- | --- | --- |
| 班次调度 | `tools/mas_dispatch.py` | 24 条 `日常/晚班定时N` 队列的「到点发车」；本工具**复用它**的 state、busy 口径与**同一条审计流** `runtime/mas_dispatch_log.jsonl` |
| MCP 双胞胎 | `tools/mas_ondemand_mcp.py` | 同一能力的 MCP 工具面（6 个工具，给 Hermes 会话用）；本工具是 **CLI/脚本面**，给 cron、巡检、人工与看板 worker 用 |
| 最早的补跑脚本 | `tools/auto_mas_rerun.py` | 「单号 → 临时队列」这套口径的出处；本工具沿用其命名与三步走（add → 改名 → 挂项） |
| 发车前自检 | `tools/mas_preflight.py` | 本工具在发车前把它当**闸门**调（`--json`，子进程，优先 Python312） |
| 现场巡检 | `tools/mas_preflight.py` / `ssh` | 只读 ssh：MAA.exe（槽位）、MuMuVMMHeadless（实例）、`E:\AUTO-MAS\debug\app.log` 任务事件 |

---

## 2. 三级语义（源码依据 `app/core/task_manager.py:287-316`）

| 模式 | 吃谁 | 跑什么 | 入口 |
| --- | --- | --- | --- |
| `AutoProxy` | **队列 uid 或 脚本 uid** | 该队列全部项 / 该脚本**全部启用号** | `--queue`、`--script`、`--account`（自动包一层临时队列） |
| `ManualReview` | 脚本 uid | 只唤醒（不跑链、不改配置）→ **等客户端应答** | `--wake` |
| `ScriptConfig` | 脚本/用户 uid | 只装配置 | 本工具不做（属改配置） |

**单号没有原生入口** ⇒ `--account` 会建一条 `临时-按需-<yyyymmdd_HHMM>-<n>` 队列，把该号所在脚本挂成队列项，再按队列发。
注意 AutoProxy 语义：**该脚本的全部启用号都会被跑到**（不是只跑你点的那个）；多启用号时命令里会显式警告。

---

## 3. 六个入口

```bash
python tools/mas_ondemand.py --list-running                # 只读：在跑槽位/任务/实例
python tools/mas_ondemand.py --queue 日常定时6              # 干跑：解析 + 闸门 + 计划
python tools/mas_ondemand.py --queue 日常定时6 --apply      # 真发车
python tools/mas_ondemand.py --script 明日方舟-官-38 --apply
python tools/mas_ondemand.py --account 帝国猛虎 --apply     # 单号 ⇒ 自动建临时队列
python tools/mas_ondemand.py --wake 明日方舟-官-38 --apply --force   # ManualReview（见下）
python tools/mas_ondemand.py --stop <taskId> --apply        # 只认 start 返回的 task_uid
python tools/mas_ondemand.py --cleanup [队列名] [--apply]    # 删本工具建的临时队列
python tools/mas_ondemand.py --release <目标|run_id> [--apply]  # 人工确认跑完后放行同一目标
python tools/mas_ondemand.py --selftest                    # 离线回归（27 例，假传输）
```

* `--queue/--script` 的 token 既可是**名字**也可是 **uid**（名字必须在 AUTO-MAS 侧真实存在，解析不出来=拒绝，零写请求）。
* 退出码：`0` 计划成立/成功；`2` 拒绝或失败（`--json` 时从 `refused`/`reason` 取原因）。
* `--json` 输出单行 JSON，便于会话与巡检解析。

### `--list-running` 读什么
进程表（`MAA.exe` → 槽位目录 + pid + 启动时刻）、实例（`MuMuVMMHeadless` → idx + pid）、`app.log` 最近事件
（`创建任务/开始运行任务/任务开始/任务结束/中止任务/已解锁脚本配置/跳过任务/启动MAA进程/开始启动模拟器`），
并把「有开始、未见结束」的任务列出来（含它跑过的脚本）。**纯只读**。

### `--wake` 的真相（务必知道）
ManualReview 唤醒后会**阻塞等 AUTO-MAS 客户端回答「请检查用户代理情况…」**（源码 `app/task/MAA/ManualReview.py`）。
没有客户端在线（没人点）时，任务会一直挂着、**脚本锁不释放**。所以：`--apply` 必须再加 `--force` 明示你在场。

---

## 4. 闸门（全默认开启，逐个按序跑）

| 闸门 | 判据 | 拒绝理由（`reason`） | 放行 |
| --- | --- | --- | --- |
| **G0** `NO_GO` | `MAAORCH_DISPATCH_NO_GO=1` | `dispatch_disabled` | 清环境变量 |
| **G1** 可达 | `POST /api/scripts/get` 探活（fail-closed） | `rest_unreachable` | 修上游 |
| **G2** 解析 | 队列/脚本/账号必须真实存在；账号须**启用**（`Status` 且 `RemainedDay≠0`）；同名多脚本拒 | `target_not_found` / `account_unusable` / `queue_empty` | 改目标 |
| **G3** 单飞 | ① 同目标在窗内发过（`MAAORCH_ONDEMAND_DUP_WINDOW_MIN`，默认 150 分）；② 与本工具在跑记录**资源重叠**（目录/脚本/实例）；③ 与班次调度近 20 分钟发车记录重叠；④ **现场**目标目录有 `MAA.exe`（`slot_busy`）；⑤ 目标目录近 `MAAORCH_ONDEMAND_WARM_QUIET_MIN`（默认 8）分钟有活动（`slot_warm`：班次两个脚本之间的空档，MAA.exe 会消失 1–2 分钟，只看进程会误判空闲） | `duplicate_inflight` / `resource_inflight` / `shift_inflight` / `slot_busy` / `slot_warm` / `live_check_failed` | `--force`（记账） |
| **G3L** 实例租约 | 目标实例（脚本 `Emulator.Index`）已被**别的任务/工具**租下（`runtime/instance_leases.json`，跨族单飞，P1-2 接入；机制见 `docs/MAS_INSTANCE_LEASE.md`）。自己的租约不算被占（同主=续租） | `lease_busy` | **无**（`--force` 放行不了：要抢得先人工 `mas_instance_lease.py release --idx N`，或等它到期） |
| **G4** preflight | 调 `tools/mas_preflight.py`：REST / 槽位空闲 / 实例可 adb / 残留模态 / 注入合规 / 磁盘。`GO` 才放；`NO-GO` 拒；`UNKNOWN` fail-closed | `preflight_nogo` / `preflight_unknown` | `--skip-preflight`（记账，不推荐） |
| **G5** 唤醒 | `--wake --apply` 需 `--force`；`--stop ALL` 需 `--force` | `wake_needs_force` / `stop_all_needs_force` | `--force` |
| **LOCK** | 与班次调度/MCP 版共锁 `runtime/mas_dispatch.lock`（写动作串行；陈旧阈值同 `MAAORCH_DISPATCH_LOCK_STALE_S`） | `concurrent_write` | 重试 |

> G3 的设计要点：**现场证据优先**（真在跑的 MAA.exe 一定拦），记录只用来补「发出去 → 进程还没起来」的窗口
> （班次补窗 20 分钟）。所以班次记录的 busy 窗口（150 分钟）**不会**把空闲槽位一直锁死。

---

## 5. 审计与状态（可追溯；t_4ddb3728 起与 MCP 版同一份账、同一条审计流）

* **调用审计**(每次调用一行，含干跑/被拒)：`runtime/mas_ondemand_log.jsonl`（两端统一；本工具行带 `src:"cli"`）
* **事件审计**（真动作：`dispatch_ok / dispatch_failed / temp_queue_created / stop_ok / stop_failed /
  cleanup_ok / cleanup_failed / release`）：`runtime/mas_dispatch_log.jsonl`（与班次事件同一条流，
  带 `source:"ondemand"`；干跑/被拒**不**写这里 ⇒ 拿行数当发车计数不再被污染）。

```json
{"ts": "2026-09-28 06:09:09", "kind": "dispatch_ok", "target_kind": "queue", "target": "日常定时6",
 "mode": "AutoProxy", "ok": true, "code": 200, "taskId": "52e5…", "reason": null,
 "apply": true, "force": false, "source": "ondemand", "src": "cli", "pid": 12345}
```

* **状态**（共享账本，跨进程单飞与收尾；**CLI 与 MCP 唯一账本**）：`runtime/mas_ondemand_ledger.json`
  schema：`{"meta":{...}, "temp_queues":[...], "<day>": {"q:<uid>|s:<sid>|a:<账号名>|w:<sid>": {...}}}`；
  entry = `{kind,target,uid,task_uid,ts,src,mode,resources,busy_until,...}`（旧字段名 `taskId/at` 读入兼容）。
  首次读入会把旧账 `mas_ondemand_state.json`（CLI）/ `mas_ondemand_mcp_state.json`（MCP）**只读合并**进来
  （不删旧文件），同 key 同一天再发 ⇒ 新记录为主、旧记录进 `prev`（不丢）。窗口 = `MAAORCH_ONDEMAND_HOLD_MIN`
  （默认 150 分，与班次一致）。跑完或人工确认后放行同一目标：`--release <目标|run_id> --apply`；
  `--list-running` 里 `ondemand_open` 带 `src`（cli/mcp），**能看到另一端的车**。
* **锁**：`runtime/mas_dispatch.lock`（与 cron 调度器、MCP 版同源同一把；任何一路发车时另一路被拒
  `concurrent_write` —— 此前 CLI 自持 `mas_ondemand.lock` 与另一端互不设防，t_4ddb3728 收口）。

---

## 6. 临时队列：建、用、清

* 名字：`临时-按需-<yyyymmdd_HHMM>-<n>`（`n` 同分钟内的序号，避免重名）。
* 建法（三步，与 `auto_mas_rerun.py` 同口径）：`queue/add` → `queue/update`（改名 + `TimeEnabled=false`
  + `StartUpEnabled=false` + `AfterAccomplish=NoAction`，避免它自己按点跑/结束动作）→ `queue/item/add`
  → `queue/item/update`（`ScriptId`）→ `dispatch/start`。
* **同 MAA 目录的号必须进同一条队列**（一条队列同时只跑一个项）——多个号时按目录分组，一组一条队列。
* 清理：`--cleanup`（默认只删 `临时-按需-*`，也可给具体名字/uid）。
  **守卫**：对应 run 还在 busy 窗口内时**拒删**（`--force` 才删）——因为删掉正在跑任务的队列会让收尾
  `final_task` 找不到 queue 而**中途失败**（脚本锁不释放）。跑完再删。

---

## 7. 自测

* 离线回归（不碰网络/runtime，假传输）：`python tools/mas_ondemand.py --selftest` → **30 例全过**
  （解析/干跑零写/发车+审计+账本/单号临时队列/停用号·同名号·坏 uid 拒绝/重复与资源单飞/槽位忙/槽位刚动过（脚本间空档）/
  班次补窗/preflight NO-GO/可达 fail-closed/NO_GO 硬闸/上游在跑分类/stop 三道防呆/cleanup busy 守卫/release/wake 需 force/
  app.log 事件解析/**账本旧账迁移/跨端可见（MCP 发的车 CLI 看得见）/共锁拒发**）。
  MCP 端同款：`python tools/mas_ondemand_mcp.py --selftest` → **18 例全过**（含跨端单飞、事件流分流、共锁）。
* 线上只读自测（真实 AUTO-MAS）：
  * `--list-running`：12 槽位在跑、实例清单、在跑任务与最近事件全部正确解析（见交付记录）。
  * 干跑 `--queue/--script/--account/--wake` 各一次：命中 `slot_busy` 的拒绝路径（当班次占着槽位时，这正是**该拦**）。

### 真发车最小验证（2026-09-28 06:31，唯一一次真发）

| 项 | 值 |
| --- | --- |
| 目标 | 单号 `帝国猛虎`（脚本 `明日方舟-官-38`，目录 `E:\MAA-Multi\MAA_6`，实例 48） |
| 选点 | 等 `日常定时6` 的两个队列项**都已开跑**、MAA_6 进程退出且静默 ~28 分钟（避开脚本间空档） |
| preflight | **GO**（槽位空闲 / 实例可 adb / 无残留模态 / 磁盘水位） |
| 临时队列 | `临时-按需-20260928_0631-1`（id `ae9ce6f8…`，项 = 官-38 的 uid `86782a20…`） |
| 发车应答 | `code=200`，`taskId=52e50bf7-1e2f-427d-a039-3d0cc0bb6231` |
| 现场确认 | app.log：`创建任务/开始运行任务` 06:31:04（主机时钟比本机慢 ~24s）+ `任务开始: 官-38` + `开始启动模拟器 48`；`--list-running` 显示该任务在跑、且「本工具在窗记录 帝国猛虎 busy 至 09:01」 |
| 审计 | `temp_queue_created` + `dispatch_ok` 两条落 `runtime/mas_dispatch_log.jsonl`；state 落 `mas_ondemand_state.json` |
| 跑完结果 | **未完成**（06:37:31 app.log `用户 d61309ea… 的自动代理任务未完成`；3 份 MAA 日志（`E:\AUTO-MAS\history\2026-09-28\帝国猛虎\` 02-31-04 / 02-32-57 / 02-35-20）全部以 `MAA 在完成任务前退出` 收尾；随后 `已解锁脚本配置 86782a20…` + 关模拟器 48，槽位无残留） |
| 归因（与分发无关） | ① 目标 uid 已回查核实（`scripts/user/get`：`d61309ea…` = `帝国猛虎`）；② 同期同机队同类失败：06:38 `f152843f…`@设备13 同因、triage 日报 Top=「客户端未就绪」×24（escalated）；③ `docs/MAS_EMULATORS.md` 已把 MAA_6 列为重点机（启动冻结 / 启动失败簇）。本车失败属机队已知故障类，非队列形态/工具缺陷。 |
| 收尾实测 | 发车后立刻 `--cleanup` 干跑 → **被守卫拒删**（「对应任务可能仍在跑 busy 至 09:01:28」）；确认任务已结束（app.log）后 `--release 帝国猛虎 --apply`（run `0a3cdcb7`，06:46:14）→ 再 `--cleanup` 干跑 → **守卫正确放行**（显示「将删除」）。本次**未真删**临时队列（留档，无副作用；`--cleanup --apply` 一条命令即走） |

教训（本次踩到的）：等待期的就绪判据要用**工具自己的口径**（`plan["resources"]["dirs"]` 是 `dkey` 过的），
别在旁路脚本里拿原始路径去比——`_recent_activity` 已改成内部 `dkey`，两侧都稳。

## 8. 实测教训（别重走）

1. **`powershell -Command -`（stdin 喂脚本）会静默吞输出**：脚本里任何一处**运行期错误**
   （实测把 `$false` 写成裸 `false` —— PowerShell 会把 `false` 当命令名去找）都会让整份脚本
   **rc=0、stdout/stderr 全空**，比报错难查一百倍。修法：改用
   `powershell -NoProfile -NonInteractive -EncodedCommand <UTF-16LE base64>`（本工具 `ssh_ps` 的实现），
   出错至少能以 CLIXML 回来看。**注意**：`tools/mas_preflight.py` 仍用 stdin 方式——它目前没踩到，
   但**改它的 PS 脚本时要小心**（任何运行期错误都会变成「远端快照无输出」）。
2. **ssh 从 Python 子进程调用**：本机 `ssh` 可能解析到 git 自带的 MSYS 版；环境里 `PATH/Path` 双键时
   裸名 `ssh` 的解析还可能被「后写键」覆盖（详见 `mas_observe_mcp._env()` 的注释）。本工具的 ssh 命令
   在 git-bash/完整环境下实测可用；**若未来在极简 env（MCP/cron）里跑出「远端快照无输出」，先查 PATH**。
3. **干跑不等于零成本**：`--queue/--script/--account/--wake` 的干跑会真调 preflight（一次 ssh 巡检，约 30–60s）。
   只想看解析时用 `--skip-preflight`。
4. 班次占满 12 槽位时，**所有**按需发车都会被 G3 `slot_busy` 拒绝——这不是 bug，是设计（同目录并行=互杀）。
5. `slot_warm` 的**已知边界**（实测发现，别高估它）：它靠 app.log 的 `启动MAA进程 / 开始启动模拟器 / 任务开始`
   等**带目录可推断**的事件做锚点。实测 AUTO-MAS 在「单个脚本跑完」时**不一定**写一条带脚本 uid 的收尾行
   （06:03–06:31 的 MAA_6 就是如此：整段窗口里只有 launch/任务开始，没有该脚本的 `已解锁脚本配置`）。
   于是：**脚本本身运行超过 N 分钟**（N=`WARM_QUIET_MIN`）时，「它刚跑完、下一个脚本还没拉起」的那 1–3 分钟空档，
   `slot_warm` 抓不到（此时 `slot_busy` 也已消失）。补跑纪律：跑完 `--list-running` 看一眼目标槽位最近事件，
   或等该目录的**班次队列整条做完**再补（06:31 的实测发车就是这样挑的：MAA_6 的 2 个队列项都已开跑且静默 28 分钟）。

## 9. 不变量（本工具不做的事）

* 不改 AUTO-MAS 配置（队列/脚本/用户只读；唯一的写动作是**建/删本工具自己的临时队列**）；不改 MAA 配置；
* 不 kill 进程、不重启侧车/实例（要停任务用 `--stop`，走官方优雅停 + 解锁 + Temp→config 还原）；
* 不动班次调度的队列与台账（只读它的 state 做冲突判定）；
* 只新增文件：`tools/mas_ondemand.py`、`tools/mas_ondemand_ledger.py`、本说明、（运行期）
  `runtime/mas_ondemand_ledger.json` 与两条审计流追加行。

## 10. 账本统一（t_4ddb3728，2026-09-28）

此前 CLI 与 MCP 各记一本账（`mas_ondemand_state.json` / `mas_ondemand_mcp_state.json`）、各持一把锁，
互不可见 ⇒ 同一目标可能一头一尾双发。现已收口：

* 两端读写同一份 `runtime/mas_ondemand_ledger.json`（`tools/mas_ondemand_ledger.py`，schema 见 §5）；
  旧账在首次读入时按文件指纹**只读合并**（幂等，重复读到不会产生 `prev` 杂物）；
* 两端共用 `mas_dispatch.lock`（CLI 侧弃用自持的 `mas_ondemand.lock`）；`--list-running`/`list_running`
  的输出里 `ondemand_open`/`ondemand_busy` 都带 `src`，"另一端在跑什么"一眼可查；
* 审计分流统一：全量调用行 → `mas_ondemand_log.jsonl`（dry-run/被拒也写，排查用）；真动作事件 →
  `mas_dispatch_log.jsonl`（only 真动作，计数用）。
* 顺带修掉一个既有缺陷：CLI `--apply` 撞锁（`concurrent_write`）时退出码误报 0 —— 现在与其它拒绝一致回 2，
  脚本化调用可依退出码判断。
