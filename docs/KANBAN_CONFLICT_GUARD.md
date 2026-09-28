# kanban_conflict_guard.py —— 文件级互斥闸：建卡/派发前检测「共享文件冲突」

> 2026-09-28 实测：同一批 running 卡里存在 **2 对同文件并发写**：
> ① `t_403cd83b`（UPCOMING 接线）与 `t_4cd871de`（kanban_health 接线）都要改 `tools/mas_observe_mcp.py`；
> ② `t_a190ca4b`（审计接线）与 `t_1df99ae5`（实例租约接入）都改 `tools/mas_run_profile.py` ——
> 工作树里该文件（mtime 11:39:14）**同时含** `mas_audit.safe_append`（①之外的 t_a190ca4b 族）与
> `lease_acquire/lease_release`（t_1df99ae5 族）两族改动，是「两卡并发写同一文件」的直接实证。
>
> 宿主并发阀门（`kanban.max_in_progress` / `max_in_progress_per_profile` / `max_spawn`）**均为不限**，
> 文件级冲突此前只能靠人看卡面兜。本工具把它做成**可复跑、可机检、零写盘**的闸：
> 只给「冲突对 + 串行建议」，**不改卡、不调度、不写看板库**（处置权归 lead / 发波人）。
>
> 文件：`tools/kanban_conflict_guard.py` ｜ 判据行：`KANBAN_CONFLICT=` ｜
> 实测留证：`docs/audits/2026-09-28_kanban_conflict_guard/{real_scan.txt,real_scan.json,selftest.txt}`
> 纪律接线：`docs/KANBAN_DISPATCH_POLICY.md` §2.6（派发前必跑）+ §6.4 清单

**与姊妹工具的分工**（互补，别互相顶替）：

| 工具 | 只管 | 输出 / 动作 |
|---|---|---|
| `tools/kanban_health.py`（板级体检） | 僵尸/重复/失败/孤儿产物等**状态类**病 | 清单 + 建议（不改卡） |
| `tools/mas_instance_lease.py` | **实例**级互斥（模拟器/adb 同一时刻一个持有者） | 租约（碰实例前 acquire） |
| **`kanban_conflict_guard.py`（本工具）** | **文件**级互斥：同一批在队卡是否会写同一文件 | 冲突对 + 串行建议 + 判据行（不改卡） |

## 1. 用法

```bash
PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"
$PY312 tools/kanban_conflict_guard.py                    # 默认 --line：末行 KANBAN_CONFLICT=（巡检/机检取数）
$PY312 tools/kanban_conflict_guard.py --verbose          # 人读：卡→文件清单 + 冲突对 + 建议（末行仍为判据行）
$PY312 tools/kanban_conflict_guard.py --json             # 单行 JSON（机读；含 cards 映射）
$PY312 tools/kanban_conflict_guard.py --card t_a190ca4b  # 候选=某张卡（任意状态）⇒ 只报它与在队卡的冲突
$PY312 tools/kanban_conflict_guard.py --files tools/a.py,docs/b.md
$PY312 tools/kanban_conflict_guard.py --body-file draft.md   # 建卡前预检：候选=卡面草稿正文
$PY312 tools/kanban_conflict_guard.py --out P.json       # 唯一写盘口（其它情况零写盘）
$PY312 tools/kanban_conflict_guard.py --selftest         # 23 例离线自检（合成板，不碰真板）
```

选项：`--board SLUG`（默认 `HERMES_KANBAN_BOARD` / `HERMES_KANBAN_DB` 推导）｜`--boards-root P`｜`--db P`（优先级最高）｜
`--scope running,ready,todo`（默认；可加 review/blocked/done，一般不要）｜`--card` / `--files` / `--body-file` **互斥**。

**退出码**：`0` = OK（可派发）；`2` = **WARN（有冲突 ⇒ 禁同批派发）或 fail-closed 错误**（未知卡 id / 半截输入 /
看板不可读 / 内部异常 —— 统一 `ok:false`，不抛裸异常）；`--selftest` 0/1。

判据行（末行，机检）：

```
KANBAN_CONFLICT=OK conflicts=none
KANBAN_CONFLICT=WARN:5 conflicts=t_403cd83b:t_4cd871de(shared=tools/mas_observe_mcp.py),t_4cd871de:t_9ed6574c(shared=docs/KANBAN_DISPATCH_POLICY.md+…)
KANBAN_CONFLICT=ERROR:unknown_card detail=看板 maa-… 上没有卡 t_nope
```

JSON 契约（`--json` / `--out`，schema `maaorch.kanban_conflict_guard/1`；结构稳定）：
`{schema, ts, board, db, scope[], mode, ok, status(OK|WARN|ERROR), conflicts[{cards[a,b], shared[], suggest}], cards_scanned, cards[{id,status,priority,files[]}], line}`
（错误时：`ok:false, status:"ERROR", error:{kind, detail}, conflicts:[], cards_scanned:0`；kind ∈
`unknown_card|empty_input|bad_input|board_unreadable|internal`）。

## 2. 判据与规则（全部可从卡 body 复算）

* **文件提取**：卡 body 归一（`\`→`/`）后按 `(tools|docs|services|ui|network|manager|infra|config)/…`
  锚定目录 + 扩展名 `py|md|json|yaml|yml|ts|js|html|css|sh|ps1` 抓 token；
  `D:\1-2\MAAOrch\tools\x.py` 这类**长路径归一到仓库相对形态**（`tools/x.py`），去重保序，**比较时大小写不敏感**
  （Windows 文件系统语义）。URL 尾（`//host/tools/x.py`）不取。
* **冲突判定**：`scope` 内两两求**共享文件集**，非空 = 冲突对；单卡自身永不与自比。
* **suggest 规则**：
  * 涉及 running 卡 ⇒ 「串行/错峰 + 点名**该文件当前持有者**（卡号(running)）」；
  * 都未跑（同批 ready/todo）⇒ 「先 X 后 Y」+ 可直接粘贴的 `hermes kanban link <父=先跑者> <子>` —— 先后序
    与派发同序（`priority DESC, created_at ASC`）。
* **卡面背景（2026-09-28）**：就靠这三条判据复原了开篇那 2 对（见 §3）。

## 3. 实测证据（2026-09-28 11:51 真扫，板 `maa-parallel-benchmark-20260926`）

留证：`docs/audits/2026-09-28_kanban_conflict_guard/`（`real_scan.txt` = live 输出原样拼接；`real_scan.json` = `--out` 产物）。

* Run A（默认全扫，5 张在队）⇒ `KANBAN_CONFLICT=WARN:5`，其中 **第 1 对 live**：
  `t_403cd83b:t_4cd871de(shared=tools/mas_observe_mcp.py)`。
* Run B（`--card t_a190ca4b`，候选模式）⇒ **第 2 对复原**：
  `t_1df99ae5:t_a190ca4b(shared=tools/mas_run_profile.py+…)`；同 run 内 `t_9ed6574c:t_a190ca4b(shared=tools/mas_probe_farm.py+…)`。
  漂移说明：`t_a190ca4b` 完成于 11:39:19、本卡创建于 11:39:03 —— 写卡时该对仍 live，扫描时已 done，
  故用候选模式复比（这也正是「刚完成的卡其工作树改动仍在」的实用场景）。
* 文件级佐证（同留证）：`tools/mas_run_profile.py` mtime 11:39:14、`mas_audit.safe_append ×2` 且
  `lease_acquire ×39` —— 两族改动同落一文件；`tools/mas_observe_mcp.py` mtime 11:05:42（两卡共写中）。
* 零写盘：不带 `--out` 重跑两次，`runtime/reports` 快照零变化（`ZERO_WRITE=OK`，902 文件）；代码面唯一
  写盘口 = `_emit()` 内 `if a.out`（sqlite 一律 `mode=ro`）。
* 自检：`--selftest` = `SELFTEST=PASS (23 checks, 0 failed)`（含共享命中/不共享/同卡自比/长路径与反斜杠归一/
  未知卡 fail-closed/空 body 等 —— 原始输出在留证目录 `selftest.txt`）。

## 4. 怎么接线（纪律 + 现状）

* **派发前必跑**（写进了 `docs/KANBAN_DISPATCH_POLICY.md` §2.6）：发波人 / lead 在 `hermes kanban promote`、
  `kanban dispatch`、或 `kanban_create` 之后挂 ready 之前跑一次；**`WARN` ⇒ 不得同批派发**，改「串行（等收口）」
  或「`hermes kanban link` 挂依赖」或「把范围改到无冲突文件」。候选预检两种姿势：
  * 建卡前：`--body-file 草稿.md`（或先建 todo 卡再 `--card t_xxx`）；
  * 已建卡待发：`--card t_xxx`。
* **巡检/自动化接线**（可选，以后做）：与 `KANBAN_HEALTH=` 同模式 —— 任何有 terminal/MCP 的巡检跑
  `--line`，把末行原样摘进报告即可。本工具刻意不依赖网关（纯本地只读 sqlite），接 MCP 面只是搬运。
* **并发阀门建议（本卡不改配置，待 lead 批准）**：文件级冲突多发的前提是并发阀门全空（`null=不限`）。
  既有建议仍是 `docs/KANBAN_DISPATCH_POLICY.md` §7 的 `kanban.max_in_progress=8` /
  `max_in_progress_per_profile=6` / `max_spawn=6`（`hermes config set <key> <val>` + 重启网关生效）——
  **本卡不做**（属调度面变更），仅标"待 lead 批准"。

## 5. 局限（已知，别当它万能）

* **提及 ≠ 写**：提取面是「卡面提到该文件」，包含只读引用（审计/模板/参考文件）。本闸**宁可多报**
  （放宽 = 保守），`WARN` 后由人/lead 用 §2 建议复审；反过来，卡面完全不提文件时本闸**看不见**其写行为（漏报面）。
* **示例 token 无豁免**：卡面里的格式样例（如 `conflicts=t_a:t_b(shared=tools/x.py)`）同会被提取
  —— 实测 Run A 中本卡自己的清单就带着 `tools/x.py`（占位样例）。
* 只看 **body**（不看 title / comments / result / 附件；要看某卡刚完成留下的工作树改动，用 `--card` 把它的清单当候选）。
* 单板范围（默认当前板）；跨板同文件冲突不检（板间本无共同工作树语义）。`scope` 不含 blocked/review 默认
  （不在飞就不消耗编辑窗口）。
* 大小写不敏感比较（Windows 语义）⇒ `Tools/X.py` 与 `tools/x.py` 视为同一文件（保守）。
* 阈值/口径全在本文件顶部常量（`DEFAULT_SCOPE` / `ANCHOR_DIRS` / `EXT_ALT`）；改口径 = 改常量 + 同步本文 + 跑自检。

## 6. 回滚（删段删文件，零残留）

1. `docs/KANBAN_DISPATCH_POLICY.md`：删 §2.6 整节 + §6.4 清单里那条 `- [ ] 冲突闸：…`；
2. 删 `tools/kanban_conflict_guard.py`；
3. 删 `docs/KANBAN_CONFLICT_GUARD.md`（本文）；
4. 删 `docs/audits/2026-09-28_kanban_conflict_guard/`（留证目录）。
   —— 全部为新增件，无对既有文件的其它改动（policy 只加了 §2.6/§6.4 两处增量）。

## 7. 维护

* 自检：`$PY312 tools/kanban_conflict_guard.py --selftest` → `SELFTEST=PASS (23 checks, 0 failed)`。
* 改提取规则/建议文案后：跑自检 + 重跑 `--verbose` 真扫一次（板是活的，数字会漂，判据行格式不许漂）。
* 相关：`docs/KANBAN_DISPATCH_POLICY.md`（怎么派活）｜`docs/KANBAN_HEALTH.md`（板级体检）｜
  `docs/MAS_INSTANCE_LEASE.md`（实例级互斥）｜`docs/KANBAN_TASK_TEMPLATE.md`（写卡模板）。
