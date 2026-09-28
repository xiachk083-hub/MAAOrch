# mas-ondemand 接线审计（kanban t_1aa55dac，2026-09-28）

> 交付：第 6 个 MCP server **`mas-ondemand`**（按需发车 / 停任务 / 查在跑），工具面 27 → **33**。
> 姿态：本卡**未发任何真车、未停任何任务、未动 AUTO-MAS 侧**；所有实测均为只读或 `apply=false` 预演。
> 姊妹文档：`docs/MCP_TOOL_ROADMAP.md` §10（落地记录）、`docs/HERMES_MAS_HANDOVER.md` §12.1（网关缓存口径）。

---

## 1. 交付物

| 文件 | 性质 | 说明 |
|---|---|---|
| `tools/mas_ondemand_mcp.py` | 新增（1495 行） | stdio MCP server，6 工具；写动作默认预演；`--selftest` / `--check` 自测入口 |
| `tests/test_mas_ondemand_mcp.py` | 新增 | pytest 回归 3 项：工具面契约（6 名）、写动作 `apply` 默认 false、`--selftest` 全绿 |
| `tests/ondemand_mcp_probe.py` | 新增 | 真 stdio 协议现场探针（13 项断言；只读 + dry-run，可反复重跑） |
| `profiles/mas/config.yaml` | 修改（+18 行） | `mcp_servers.mas-ondemand` + `platform_toolsets.cron` 追加一行 |
| `docs/MCP_TOOL_ROADMAP.md` | 修改（§10） | 落地记录 + 计数断言口径 27 → 33 + 回滚 |

## 2. 工具面契约（6）

| 工具 | 性质 | 关键入参 | 说明 |
|---|---|---|---|
| `list_running` | 只读 | `timeout` / `with_users` / `mask_accounts` | 三源合并：① Hermes 班次 busy 账 ② 本 server 按需 busy 账 ③ 远端现场（MAA.exe / MuMuVMMHeadless），并把槽位映射回脚本/启用用户 |
| `ondemand_preflight` | 只读 | `target` / `skip_screen` / `timeout` | 复用 `tools/mas_preflight.py`，返回 `PREFLIGHT=GO/NO-GO` + 失败项 |
| `dispatch_queue` | 写（默认预演） | `name` / `apply` / `force` / `force_conflict` / `note` | 队列名 → `POST /api/dispatch/start`；24 条班次队列走 `mas_dispatch` 完整闸门 |
| `dispatch_script` | 写（默认预演） | `name` / `apply` / … | 脚本名 → 脚本 uid 直发 |
| `dispatch_account` | 写（默认预演） | `name` / `apply` / … | 账号名 → 所属脚本（MAA 目录/实例）→ 内部建 `临时-按需-*` 单脚本项队列 → 发车 |
| `stop_task` | 写（默认预演） | `task_uid` / `apply` | `POST /api/dispatch/stop`；**只认 `task_uid`**（拒 `ALL`、拒队列 uid、台账外 fail-closed） |

闸门复用（不重写）：REST 可达 fail-closed、单飞锁（与 cron 调度器共用 `runtime/mas_dispatch.lock`）、
busy 窗口资源冲突、守卫窗 05:00–05:40 / 19:20–20:20、`NO_GO` 口令。
自有状态：审计 `runtime/mas_ondemand_log.jsonl`（每次调用一行，含拒绝）、台账 `runtime/mas_ondemand_mcp_state.json`
（busy 窗口默认 `MAAORCH_ONDEMAND_HOLD_MIN=180` 分钟）。**台账文件名与 CLI 版 `tools/mas_ondemand.py`
（卡 t_78c0c0d0）的 `runtime/mas_ondemand_state.json` 刻意错开**——两者 schema 不同，同名会互相覆盖；
统一账本留作后续卡（见 §9 的 hotspot 记录口径）。

## 3. 调用样例

```jsonc
// ① 查在跑（只看，不写）
{"name": "list_running", "arguments": {"mask_accounts": true}}
//  → maa_slots[{dir, pids, scripts[], users_enabled[]}] / emulators_up[] / tracked_not_live[] / guard_window

// ② 预演发车（默认就是预演，apply 缺失 = false）
{"name": "dispatch_queue", "arguments": {"name": "日常定时3"}}
//  → {"ok": true, "dry_run": true, "refused": null, "summary": "DRY 日常定时3 would_dispatch"}

// ③ 真发车（唯一会动的调用；成功回 taskId = task_uid，交给 stop_task）
{"name": "dispatch_script", "arguments": {"name": "明日方舟-官-33", "apply": true, "note": "手法一漏发补跑"}}

// ④ 停任务（只认 task_uid）
{"name": "stop_task", "arguments": {"task_uid": "6f1c…", "apply": true}}
```

## 4. 验证矩阵（本轮实测，全部只读 / dry-run）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `python tools/mas_ondemand_mcp.py --selftest` | `OK (15/15)`，exit 0 |
| 2 | `python tools/mas_ondemand_mcp.py --check` | `OK`；`reachable=true / queues=39 / scripts=54`；`dry-run dispatch_queue(日常定时1)={"ok":true,"dry_run":true,"refused":null,…}` |
| 3 | `hermes mcp test mas-ondemand` | `✓ Connected (2398ms)` / `✓ Tools discovered: 6`（Hermes 侧 stdio 真实拉起） |
| 4 | `python tests/ondemand_mcp_probe.py` | `PROBE: OK (13/13)`，真 MCP 协议；日志见 §5 |
| 5 | `python -m pytest tests/ -q` | `344 passed`（含本卡新增 3 项） |

## 5. 探针日志（节选，2026-09-28 06:09）

```
mas-ondemand MCP 现场探针 @2026-09-28 06:09:13
  tools/list 白名单                         PASS
  list_running 可达                        PASS
  list_running 给出在跑槽位                    PASS
      emulators_up=['3','4','7','13','14','17','24','36','39','43','53','58'] slots=12 live_not_tracked=[]
      e:\maa-multi\maa_1     pids=['31356'] scripts=['明日方舟-官-23','明日方舟-官-13','明日方舟-官-37','明日方舟-官-27','l-1-酸酸酸酸']
      e:\maa-multi\maa_10    pids=['19796'] scripts=['明日方舟-官-33','明日方舟-b-7','明日方舟-b-12','明日方舟-台-1','l-4-Buling']
  list_running 槽位→脚本映射可解                 PASS
  dispatch_queue dry-run                 PASS
  dispatch_queue 未知队列拒绝                  PASS          # refused=unknown_queue + known_queues 提示
  dispatch_script dry-run                PASS
  dispatch_script 未知脚本拒绝                 PASS          # refused=unknown_script
  dispatch_account dry-run               PASS
      account plan: refused=conflict_inflight              # 目标脚本当日在跑 → 冲突守护生效（预期）
  stop_task 拒 ALL                        PASS          # refused=refuse_all
  stop_task 拒非 UUID                      PASS          # refused=not_a_uid
  stop_task 台账外 fail-closed              PASS          # refused=unknown_task_uid
  ondemand_preflight 返回结论                PASS
      preflight=NO-GO failing=['slot_idle'] reasons=['槽位忙：E:\MAA-Multi\MAA_1 在跑(pid 31356)']
PROBE: OK (13/13)
```

## 6. 配置改动（改前备份 + diff）

- 备份：`C:\Users\xiach\AppData\Local\hermes\profiles\mas\config.yaml.bak-ondemand-20260928-055725`
- 写入方式：`hermes config set`（ruamel 保注释写入；未手改文件）
- diff（相对备份，纯新增）：

```yaml
# platform_toolsets.cron
+    - mas-ondemand
# mcp_servers
+  mas-ondemand:
+    command: C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe
+    args:
+      - D:\1-2\MAAOrch\tools\mas_ondemand_mcp.py
+    env:
+      PYTHONUTF8: '1'
+      PYTHONUNBUFFERED: '1'
+    tools:
+      include:            # 注册期硬白名单，只放这 6 个
+        - list_running
+        - dispatch_queue
+        - dispatch_script
+        - dispatch_account
+        - stop_task
+        - ondemand_preflight
+    enabled: true
+    timeout: 900
```

回读校验：`hermes config get mcp_servers.mas-ondemand` / `hermes config get platform_toolsets.cron` /
`hermes mcp list`（显示 `mas-ondemand · 6 selected · enabled`）。

## 7. 计数与刷新时效

- 工具面总数 **27 → 33**（monitor 10 / daily 6 / dispatch 4 / observe 5 / triage 2 / **ondemand 6**）。
  计数断言（`docs/MCP_TOOL_ROADMAP.md` §8.3/§9 口径）：巡检报告应报 `MCP_API_TOOL_COUNT=33`。
- 新 server 属"增"，网关 reconciler 处理增删；**本轮未重启网关**（避免打断同批 kanban 运行，
  网关 PID 29400 同时是本批派发器宿主）。若网关内 `builtin` 运行仍看不到 ⇒ 按 `HERMES_MAS_HANDOVER.md` §12.1
  用 `hermes gateway restart` 后复核；kanban worker / 新会话是独立进程，立即可见（`hermes mcp test` 已实测）。

## 8. 边界与未做

- 未发任何真车、未停任何任务、未改 AUTO-MAS 队列/脚本、未重启侧车与网关。
- `dispatch_account` 的"会跑"口径 = AUTO-MAS `app/task/general/manager.py:151-159`（`Status` 启用且 `RemainedDay≠0`）。
- `stop_task` 的 `task_uid` 来自发车回执（AUTO-MAS 任务 `uid`），**不是**队列 uid；台账外的 uid 一律 fail-closed。
- 回滚：`cp config.yaml.bak-ondemand-20260928-055725 config.yaml`（或 `hermes config set` 改回 5 项 cron 列表 /
  删 `mcp_servers.mas-ondemand`）；代码侧删 3 个新文件即可，无数据库/远端残留。
