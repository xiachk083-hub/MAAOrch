---
name: mas-playbooks
description: Use when 用户一句话要调配（补跑某号/改刷关卡/发肉鸽/插活动/停队列/腾实例）— 剧本路由、命令与红线。
version: 0.1.0-draft
author: mas
license: MIT
metadata:
  hermes:
    tags: [mas, auto-mas, dispatch, playbook, routing]
    related_skills: [auto-mas-host-ops, maa-roguelike-ops, maa-event-farming, maa-task-config, maaorch-queue-triage]
---

# MAS 调配剧本路由（一句话 → 场景 → 执行）

> **草稿状态**：本文件是 `t_4d8f3ee3` 的技能草稿（未安装）。全文手册 = `D:\1-2\MAAOrch\docs\HERMES_MAS_PLAYBOOKS.md`（六剧本六段式：触发语/先确认/命令含 dry-run/验证判据/回滚/红线）；执行细节以相关技能与手册为准，本技能只做**路由 + 红线复述**。

把「一句话调配」钉到可执行路径。**先查库**（`wiki_search`）→ **只读优先** → **生产写先批准**。

## When to Use

- 用户口述调配意图：「补一下某号 / 漏的号补一补 / 这个失败号重跑」「把某号改刷 CE-6 / 打 20 次 / 改回 1-7」「发一局肉鸽 / 让 l-2 刷一局」「活动开了插一轮活动关 / 活动关优先」「把晚班定时5 停了 / 别让某号再跑了 / 急停」「把某台实例空出来给肉鸽线」
- 以上任一意图的**追问、复核、回滚**（"补跑到底成没成""肉鸽停了吗""怎么改回去"）

## 路由表

| 触发语（样例） | 剧本 | 主命令 | 全文 |
|---|---|---|---|
| 补一下官-5 / 失败号重跑 | ① 补跑某号 | `mas_rerun(accounts=[…], dry_run=True)` → `dry_run=False`；CLI `auto_mas_rerun.py [--apply]` | §1 |
| 某号改刷 CE-6 / 打 20 次 | ② 改关卡/次数 | `mas_stage_fix.py --dry-run`（模具）→ `--apply --only` → `--verify/--rollback` | §2 |
| 发一局肉鸽 / l-2 刷一局 | ③ 肉鸽一局 | 机队：备份→`StartCount=1`→`schtasks /run MAAOrch-StartOne-<idx>`；产线：`mas_scenario_dispatch.py --scenario 肉鸽` | §3 |
| 插一轮活动关 / 活动关优先 | ④ 活动关 | 计划表 Stage（受控写）/ `--set Task.ActivityStageIndex=N`（草案） | §4 |
| 停晚班定时5 / 别让某号跑了 / 急停 | ⑤ 停队列/号 | 止损 `POST /api/dispatch/stop {taskId}`；停号 `auto_mas_toggle_user.py --name`；急停 `hermes -p mas cron pause 2600f6415f16` | §5 |
| 把实例空出来给肉鸽 | ⑥ 实例腾挪 | 等/停产线 → 机队闸开接管（`rogue_fleet_watch.mjs --status`） | §6 |

## 六个压缩卡

**① 补跑某号**：先三问 C3（会好吗/已成功吗/超上限吗；定因 `triage_error`，注意目录级串号）→ 守卫窗外 → 预演 `mas_rerun(dry_run=True)`（体检表+would_start）→ 发车 ≤8/批、同目录同队列 → 验 `mas_rerun_state`(ok+taskId) + 号下一条 history 闭环 → 临时队列跑完即删（残留 <24h `--cleanup`）→ **红线**：守卫窗/资源类不补、不重复烧理智。

**② 改关卡/次数**：先定位脚本+"简洁/详细"（简洁强制 `EnableTimesLimit=false` ⇒"打 N 次"不生效）→ dry-run 出 plan.json diff → `--apply --only <号>` 小样 → 下一轮发车看 `GetFightStage: … selected <关>` → 回滚 `--rollback`（原值回写）→ **红线**：生产写先批准、锁拒写别硬来、没确认的字段不写。

**③ 肉鸽一局**：选路线（机队直发首选 / AUTO-MAS 草案）→ 机队：按住看守（`rogue_hold.flag`）→ 备份→`StartCount=1`→`StartOne` → 看 `已开始探索 1 次`（完成≠成功）→ 回滚 `copy .bak` + 重启 → **红线**：同模拟器两 MAA 绝对禁止、产线在跑不动、6.17.5/6.18 模板版本先对上。

**④ 活动关**：确认活动在开 + 关码/序号（不确定用序号 1 打日志核对）→ 首选计划表 Stage（收尾必改回）→ 或 IfActivityFirst/额外队列/scenario（草案）→ 验 `selected <活动关>` → **红线**：活动结束不改 Stage=白挂（46 号教训）、药三套额度别串。

**⑤ 停队列/号**：分诊（在跑任务=stop 止损 / 号=停用用户 / 队列=无开关 H2 / 全局=cron pause）→ `task_uid` 从 dispatch_log/rerun_state 找，**不知道不乱停、别 kill** → 判据「已解锁脚本配置+任务结束」/ Status 复读 → 回滚 resume/`--enable`/下一班次 → **红线**：停号≠停它正在跑的轮次、停产线留缺口要记补跑池。

**⑥ 实例腾挪**：先判共用槽（emu 25/28/31/34/52 ↔ MAA_1/2/9/10/12）→ 默认等产线跑完；强停走 ⑤（批准）→ 闸门：产线停+空闲≥6min+非让位区+内存≥8G → 机队自动/`StartOne` 接管 → 验进程命令行归属 + `--status` → 回滚收工让路/下一班次 → **红线**：两 MAA 绝对禁止、不 kill-server、本机实例1（16384）不碰。

## 通用红线（十则）

1. 同 MAA 目录同时只一个任务（并发=互杀按目录杀）。2. 同模拟器绝不许两个 MAA。3. 班次窗（05:00–08:00 / 19:30–23:00）不重启侧车/实例。4. 止损用官方 stop（unlock+还原），**不 kill MAA**。5. 生产写先批准，备份→改→复读→留痕。6. 凭据不落盘。7. 补跑（账号级）≠ 补发（队列级），两边不替对方决定。8. 事实以机器证据为准。9. 工具返回 UNKNOWN/读不到 = 不通过。10. 肉鸽：让位区不发、产线在跑不动、机队与用户手动实例不碰。

## 时间窗速查

- 守卫窗（不补不发）：**05:00–05:40 / 19:20–20:20**
- 班次发车点：05:00–05:33 / 19:30–20:03（+3min 错峰）
- 肉鸽让位区（不发车+让路）：**04:00–05:33 / 18:30–20:30**
- 侧车重启禁窗：05:00–08:00 / 19:30–23:00
- 节律：04:01/19:01 AUTO-MAS 自重启、04:00/19:00 主机重启（都会吞在飞任务）

## 工具箱速查

- **MCP**：`mas_status`/`mas_snapshot`/`account_status`/`queue`/`mas_rerun_state`（读）；`mas_rerun`（写·补跑）；`list_schedule`/`switch_gate`（读）；`dispatch_due`/`dispatch_queue`（写·发车）；`triage_error`/`error_daily`（读·定因）。
- **CLI**（`D:\1-2\MAAOrch`，解释器 `$PY312`）：`mas_dispatch.py`、`auto_mas_rerun.py`、`auto_mas_toggle_user.py`、`mas_stage_fix.py`、`mas_scenario_dispatch.py`、`mas_preflight.py`、`mas_switch_gate.py`、`client_state_check.py`、`maa_error_daily.py`、`rogue_fleet_watch.mjs`。
- **急停 env**：`MAAORCH_DISPATCH_NO_GO=1`｜`MAAORCH_SCENARIO_APPLY=1`（双闸之一）｜`MAAORCH_SCENARIO_NO_GO=1`。

## 缺口提醒（引用时别越权）

H1 无通用"改某号关卡"工具（仅 SR 模具）｜H2 无单队列停发开关｜H3 场景发车 `--apply`/活动参数**未实跑（草案）**｜H4 CLIENT_STATE/POWER/IDENTITY 观察面未接线（验证要手跑 CLI）｜H5 临时队列无统一发车入口（显式 `POST /api/dispatch/start`，不走 mas_dispatch 白名单）。
