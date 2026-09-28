# 实例租约（`tools/mas_instance_lease.py`）—— 多任务抢同一台实例的轻量互斥

> kanban t_65b69753 ｜ 2026-09-28
>
> 痛点：**补跑卡、班次发车、实机测试同时想用同一台实例** ⇒ 互相抢、失败、「已在运行」、
> 甚至把别人正在跑的任务打断（今天一天撞了三次）。
>
> 定位：给「用实例」这件事加一层**协调清单**。谁要用，先 `acquire`；用完 `release`；
> 拿不到就**别发车**（NO-GO）——换一台、或者 `--wait` 等它空出来。
>
> 边界：**只写 `runtime/instance_leases.json`**，不发车、不启停实例、不改 AUTO-MAS/MAA 任何配置。
> 「能不能发」的裁决仍在 `tools/mas_preflight.py` / `tools/mas_ondemand.py` 手里；
> 本工具只给它们喂一条事实（第 5 节是接入建议，**不动别人代码**）。
> 接入进度（2026-09-28，kanban t_1df99ae5）：`mas_run_profile.py` / `mas_ondemand.py` / `mas_ondemand_mcp.py` / `mas_preflight.py` 已按 §5 接线
> （证据 `docs/audits/2026-09-28_p1_2_lease_consumers/`；probe_farm 复用 run_profile 路径；`mas_dispatch.py` 班次队列仍走 busy 窗口口径）。

---

## 1. 一页速查

```bash
python tools/mas_instance_lease.py list                        # 看在租实例
python tools/mas_instance_lease.py list --all                  # 加过期残留 + 最近历史
python tools/mas_instance_lease.py check --idx 13              # 空着吗？（rc 0 空 / rc 1 被占 / rc 2 判不了）
python tools/mas_instance_lease.py check --queue 晚班定时1      # 队列→实例现查 + 逐台判占用
python tools/mas_instance_lease.py check --script 明日方舟-官-6 --owner t_abc

python tools/mas_instance_lease.py acquire --idx 13 --owner t_abc --purpose "补跑 官-1-1-7" --ttl 1800
python tools/mas_instance_lease.py acquire --idx 13,14 --owner t_abc --purpose "一队双实例" --wait 120
python tools/mas_instance_lease.py acquire --script 明日方舟-官-6 --owner t_abc --purpose "实机测试"

python tools/mas_instance_lease.py renew   --idx 13 --owner t_abc [--ttl 1800]
python tools/mas_instance_lease.py release --idx 13 [--owner t_abc]
python tools/mas_instance_lease.py release --owner t_abc       # 收尾：放掉该卡的全部租约

python tools/mas_instance_lease.py resolve --script 明日方舟-官-6            # 只读：现查映射
python tools/mas_instance_lease.py resolve --queue 晚班定时1 --with-procs    # 加 ssh 现查在跑进程
python tools/mas_instance_lease.py prune                                     # 过期租约清进历史
python tools/mas_instance_lease.py selftest [--race 8]                       # 离线自测（含真·多进程互抢）
```

* `--check --idx N` / `--list` / `--selftest` 是 `check` / `list` / `selftest` 的兼容写法。
* 退出码：`0` 成功 / 空着 / 自己的；`1` 被占、被拒（acquire 撞车、check 撞别人、release 持有者不符）；
  `2` 用法错误或判不了（REST 不通、脚本名解析失败、Emulator.Index 为空 —— **一律 fail-closed**）。
* 末尾行（grep 用）：`LEASE=acquired|renewed|stolen|busy|free|self|released|not_owner|resolved ...`；
  `--json` 输出单行 JSON（同字段，不打印 `LEASE=` 行）。
* `owner` 建议就写**任务卡 id**（`t_xxx`）；不给 `--owner` 时自动取 `HERMES_KANBAN_TASK`
  （看板 worker 环境里天然就是卡 id），再没有就报用法错误 —— 不猜。

---

## 2. 文件与 schema（`runtime/instance_leases.json`）

键口径：**`<host>:<idx>`**。字段：

| 字段 | 含义 |
| --- | --- |
| `host` | 机台标签（见 §3 host 口径） |
| `idx` | MuMu 实例号（语义见 `docs/MAS_EMULATORS.md`；本工具**不校验**实例是否存在/死活） |
| `owner` | 持有者（任务卡 id / 调用方标识） |
| `purpose` | 用途（必填：拿这台实例干什么） |
| `acquired_at` / `renewed_at` | 起租 / 最近续租时刻（续租不清 `acquired_at`） |
| `expires_at` | 到期时刻；`ttl_s` 到期时间秒数（默认 1800，1..86400） |
| `pid` / `src` / `note` | 取租进程 pid / 来源面 / 备注（便于人工判断持有者是否还活着） |

```json
{
 "meta": {"v": 1, "updated": "2026-09-28 09:25:27"},
 "leases": {
  "100.79.173.69:13": {
   "host": "100.79.173.69", "idx": "13", "owner": "t_65b69753",
   "purpose": "补跑 官-1-1-7 刷关", "acquired_at": "2026-09-28 09:08:11",
   "renewed_at": "2026-09-28 09:08:11", "expires_at": "2026-09-28 09:38:11",
   "ttl_s": 1800, "pid": 1234, "src": "cli"
  }
 },
 "history": [
  {"host": "100.79.173.69", "idx": "13", "owner": "t_abc", "purpose": "…",
   "ended_at": "2026-09-28 09:38:12", "ended_reason": "released|expired|stolen", "ended_by": "t_abc"}
 ]
}
```

* **过期自动失效**：`expires_at <= now` 的租约在任何读路径（`check` / `list` / `get`）都按「没有」算；
  下一次写账时清进 `history`（保留最近 200 条，含 `released` / `expired` / `stolen` 三种结束原因）。
* 同 key 同一天再取：**同主=续租**（不换 `acquired_at`）；**异主且未过期=被拒**；`--force` 才抢，且留 `stolen` 痕迹。
* 账本损坏（手工编辑/落盘被打断）⇒ 隔离成 `.corrupt-<ts>` 并空账自愈，**不阻塞**发车链。

---

## 3. host 口径（不统一就互相看不见）

| 场景 | 写法 | 默认值来源 |
| --- | --- | --- |
| AUTO-MAS 远端机队（MRFZ-0000） | `--host` 省略即可 | `MAAORCH_LEASE_HOST`；缺省取 `MAAORCH_SSH_HOST` 去掉 `user@`（本 rig = `100.79.173.69`） |
| 本机（DESKTOP-L1OEMR3）实例 | `--host local` | 肉鸽 #0 / 设备 V 这类本机实例显式写 `local` |

提示：`idx` 是**每台机各自的编号**，两台机都可能有 idx 13 ⇒ 键里必须带 host。

---

## 4. 竞态与并发（为什么两个进程抢只有一个能拿到）

单文件 + 单锁 + 原子替换，全部读-判-写都在锁内完成：

1. **跨进程锁** `runtime/instance_leases.lock`：`os.open(..., O_CREAT|O_EXCL)` 抢位；
   抢不到就 50ms 一轮重试（默认等 10s），拿到后写入 `pid/ts/token` 并**回读校验 token**
   —— 实测本机可回读（`read_back_ok True`）。
2. **陈旧锁抢占**：锁文件 mtime 超 30s（`MAAORCH_LEASE_LOCK_STALE_S` 可调）才尝试删除；
   **活着持有者的锁在本机句柄语义下删不掉**（实测 `remove_while_open_blocked PermissionError`）
   ⇒ 不存在「A 抢了 B 的新锁」的经典竞态。删掉且确认已删才继续抢。
3. **写盘原子**：先写 `instance_leases.json.tmp` 再 `os.replace` —— 读侧永远看不到半截 JSON。
4. **释放不误删**：退出时只在锁内容里仍是自己的 token 时才删除锁文件。
5. **TTL 自愈**：持有者进程崩了/卡死，锁最多卡 30s、租约最多卡一个 TTL，之后别人可接管（`--force` 可即时抢，留痕）。

实测证据（`selftest`，2026-09-28，本机）：

```
== 真·多进程互抢（4 个进程同时 acquire 同一实例，临界区撑 400ms）==
PASS exactly one winner   got=1        （4 抢 1，唯一赢家）
PASS losers busy          got=['busy']
PASS exactly one lease on disk got=1
== 同主并发（幂等续租，仍只有一条）== PASS same owner all rc0 / one lease row
== TTL 过期自动失效 + 被接管（真时钟 ttl=4s）== PASS 在租期内=busy → 过期满 4s→free → 子进程接管 rc0
selftest: OK (0 失败)
```

**已知边界（照实说）**：

* 只协调**本仓库可见的**任务（账本在 `runtime/`，远端别处的脚本看不见它）；
* 吃本机时钟：跨机协调不在设计内（同机同钟，够用）；
* `--wait` 是「等空位」不是 FIFO 排队器 —— 不保证先来先得；不想等就先 `list` 看谁占着，换实例更省事；
* TTL 上限 86400s；超长任务请周期性 `renew`（或接受 TTL 到期后被别人接管的风险）；
* 租约**不保证实例真的在跑**（那是 VMM/adb 的活），也**不能替代发车锁**（见 §6）。

---

## 5. 接入建议（**建议**——本卡不动别人代码，逐条带代码骨架）

统一约定：`owner` = 任务卡 id；`purpose` 一句话写清；收尾 `release --owner t_xxx`（或让 TTL 兜底）。
host 一律用本工具默认口径；本机实例写 `local`。

### 5.1 preflight（`tools/mas_preflight.py`）：被占 ⇒ NO-GO

在「3 模拟器」检查之后并列加一项（FAIL 会自动进 `failing_checks` ⇒ `PREFLIGHT=NO-GO`）：

```python
import mas_instance_lease as lease                       # 同目录，只读
host = os.environ.get("MAAORCH_LEASE_HOST") or SSH_HOST.split("@")[-1]
rows, bad = {}, []
for i in emus:                                           # emus = 目标实例号（已有）
    r = lease.check(host, i)
    rows[i] = {"status": r["status"], "detail": r["detail"], "holder": (r.get("holder") or {}).get("owner")}
    if not r["ok"]:
        bad.append(i)
res["checks"]["lease"] = {"status": "FAIL" if bad else "PASS", "per_idx": rows,
                          "detail": "; ".join("idx%s %s" % (i, rows[i]["detail"]) for i in rows) or "无目标实例"}
if bad:
    res["reasons"].append("实例租约：" + "; ".join("idx%s(%s)" % (i, rows[i]["detail"]) for i in bad))
```

效果：发车前 5 分钟自检会因为「某张卡正占着这台实例」直接 NO-GO，而不是发出去再互相踩。

### 5.2 按需发车（`tools/mas_ondemand.py` / MCP 版）

闸门链里加一步（建议在 G3 单飞之后、G4 preflight 之前）：

```python
with lease.hold(host, idx, owner=task_card, purpose="按需发车 %s" % target, ttl_s=1800, wait_s=args.wait):
    ...  # 真发车临界区（现状：with _lock(): post("/api/dispatch/start", ...)）
```

* **同步任务**（等结果才返回）：用 `hold()`，异常也保证释放；
* **异步发车**（发完即返回、MAA 长跑 30~150 分钟）：`acquire(ttl=预计时长+余量)`，
  在「任务结束事件」处 release；TTL 只是兜底，别把它当释放机制的唯一依赖；
* 拒绝话术建议直接抄本工具的输出（含持有者卡号与到期时间），落审计流即可。

### 5.3 补跑卡（`tools/auto_mas_rerun.py` / 看板补跑卡）

它原有三条硬约束的第 2 条「目标 MAA 目录必须当前空闲」正是租约要回答的问题。建议顺序：

```
账号 → 脚本 → 实例(idx)   # 现查：python tools/mas_instance_lease.py resolve --script <脚本名>
acquire(idx, owner=卡id, purpose="补跑 <账号>")   # 撞车就停：换实例 / 报卡
建临时队列 → 发车 → 跑完 release
```

### 5.4 实机测试 / 按配置发车（`tools/mas_run_profile.py`、`tools/mas_probe_farm.py`）

这两个工具已有**单工具**单飞锁（`runtime/mas_run_profile.lock` 等）——它防的是「自己跟自己撞」；
**跨工具撞同一实例**（测试 vs 补跑 vs 班次）要靠租约。建议在各自 preflight 通过之后、备份配置之前
`acquire`（owner=卡 id），跑完 `release`；长跑阶段在已有 `touch()` 心跳处顺带 `renew`。

### 5.5 肉鸽机队（`tools/rogue_fleet_watch.mjs`、`tools/rogue0_watch.mjs`）

肉鸽用 l-1/2/4/5/7（idx 25/28/31/34/52）+ 本机 #0（`--host local`）。看守拉活实例前先 `check`；
抢不到就跳过本轮（别的任务在用），而不是硬拉。

### 5.6 看板 / cron worker 约定

* `owner` 用卡 id；`HERMES_KANBAN_TASK` 会自动兜底；
* 收尾 `release --owner <卡id>`（放掉该卡全部）——尤其在被 kill 前的正常退出路径；
* 卡里写「用实例」需求时，直接附一条 `check --idx N` 的结论（现查，别抄档案表）。

---

## 6. 与现有锁的关系（别混用）

| 锁 / 文件 | 管什么 | 粒度 | 生命周期 |
| --- | --- | --- | --- |
| `runtime/mas_dispatch.lock` | 发车临界区（cron / 按需 / MCP 同源） | 毫秒级串行 | 一次发车动作内 |
| `runtime/mas_run_profile.lock`、`mas_probe_farm.lock` | 单工具的单飞（自己别跟自己撞） | 一次长跑 | 跑完释放 |
| `runtime/instance_leases.json`（本工具） | **跨任务/跨工具的实例层占位** | 分钟~小时 | TTL 内有效，可续租 |

优先级一样：**谁先拿到谁用**；抢不到不是「再试一把」，而是**换实例 / 等 / 走走 `--force` 但要留痕**。

---

## 7. 自测与证据（本卡跑过什么）

```bash
python tools/mas_instance_lease.py --selftest            # 离线：隔离 runtime + 真子进程互抢
python tools/mas_instance_lease.py resolve --queue 晚班定时1 --with-procs   # 线上只读
python tools/mas_instance_lease.py check --queue 晚班定时1                  # 线上只读
```

* `selftest` 覆盖：取/续/放语义、撞车被拒、多实例**全有或全无回滚**、`--force` 留痕、
  `hold()` 异常也释放、**真时钟 TTL 过期 + 接管**、**4/8 进程互抢只出一个赢家**、
  同主并发幂等、文件卫生（有效 JSON / 无 tmp 残留 / 锁已删）、`check` 退出码与 `--check` 别名。
  2026-09-28 实测全绿：**45 项 PASS / 0 FAIL**（含 `--race 8` 互抢）；完整捕获见
  `docs/audits/2026-09-28_instance_lease.selftest.txt`（`selftest_rc=0`）。
* 线上只读实测：`resolve --queue 晚班定时1 --with-procs` → 5 脚本 / 5 实例（idx 11,25,36,45,46），
  VMM 在跑 13,14,25 —— 正好演示「l-1 实例(25) 在跑却被某队列当成目标」这类冲突，租约能把这件事提前说清。
  本卡复核捕获见 `docs/audits/2026-09-28_instance_lease.live.txt`（list / check / resolve / resolve --with-procs）。
* 线上写路径实测：`acquire --idx 999 … → check(busy) → release`、`acquire --script 明日方舟-官-6`（现查 idx 20）→ release，
  全部按预期落账并释放；`release --owner` / `renew` / `--force` / `--wait 4`（实测等待 5.9s 后 rc 1）逐条验过。

---

## 8. 给下一个 agent 的三句话

1. 要动某台实例之前：`check --idx N`（或 `--queue/--script` 现查）→ 空着就 `acquire`，不空就换/等；
2. 完事 `release`；被 kill 也不怕，TTL 兜底；
3. 看到 `LEASE=busy` 别硬上 —— 上面带持有者卡号与到期时间，先去人/卡那里对一下。
