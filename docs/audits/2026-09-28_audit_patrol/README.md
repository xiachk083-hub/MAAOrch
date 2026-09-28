# 审计巡检接线（卡 t_537fbff2）· 2026-09-28

**一句话**：把统一审计流的水位判据接进 30 分钟只读巡检（cron job `52b88b0ffca0`），
并把 `runtime/audit/` 纳入存储水位脚本的扫描面。**只读接入**：两个水位工具都永不 rotate/prune。

## 1. 交付物

| # | 交付 | 位置 | 说明 |
|---|---|---|---|
| 1 | 巡检收尾判据（新工具，只读） | `tools/audit_waterline.py` | `AUDIT_STATUS=OK\|WARN\|CRIT` 一行判据；`--json` / `--selftest` / `--audit-dir`；退出码 0/2/3/1 |
| 2 | MCP 工具面接线 | `tools/mas_observe_mcp.py` → `mas-observe.audit_waterline` | 巡检只有 MCP（无 terminal），必须走 MCP；server 工具数 7→9（含并发的 kanban_health） |
| 3 | 巡检 prompt 收尾项 | cron job `52b88b0ffca0` 第三步 `8)` + 主表第 15 行 | prompt 由 `patrol_prompt.after.txt` 应用（`apply_patrol_prompt.py`） |
| 4 | 存储水位扩面 | `tools/storage_waterline.py` | 本地段新增 `runtime/audit/`（live + archive）：体积 / 最老文件天数 / 趋势（MB 每天）；`--runtime DIR` 供演练 |
| 5 | 离线演练 | `tests/audit_patrol_drill.py`、`tests/observe_audit_mcp_probe.py` | 假统一流/假归档 ⇒ 断言 WARN/CRIT 真触发（CLI 面 + MCP 面） |

## 2. 判据与阈值（**同口径**，改一处要同步）

`report_hours` = 统一流当前覆盖的小时数（首/末事件 `ts` 差；无有效 ts 时退化为 live 文件存活时长）。

| 级别 | 条件 | 出处 |
|---|---|---|
| CRIT | live > **25 MiB** | 本卡（8 MiB 轮转阈值的 3 倍还多 ⇒ 轮转坏了/被关了） |
| CRIT | 归档缺失（0 份）而 live > **8 MiB** | 本卡（= `mas_audit.MAX_MB_DEFAULT`，该转没转） |
| WARN | live > **10 MiB** | `docs/MAS_AUDIT_SCHEMA.md` §7.4（= `mas_audit.LEGACY_WARN_BYTES`） |
| WARN | `report_hours ≥ 24` **且** `rotate_now.rotate=True` | 本卡（"≥24h 未轮转"：轮转条件已满足仍未轮转） |
| WARN | `mas_audit.status()` 自身 verdict=WARN | 不降级（任一文件 >10 MiB） |
| OK | 其余 | — |

**为什么 WARN 的 24h 分支要加 `rotate_now.rotate` 条件**：轮转策略是 8 MiB / 7 天（§7.2），
单看"文件活了 24h"会把每个正常文件都报成黄灯；"该转未转"才是可动作信号。
`report_hours` 与 `rotate_now` 都原样出现在 JSON 与判据行里，口径若变可直接在输出里看到差异。

**归档保留期**（90 天）只作提示（storage 段 INFO / audit_waterline `stale_archives_n`），不升级判据：
清理永远是人拍板（§9）。

## 3. 验收对照

| 验收项 | 证据 |
|---|---|
| ① 手工/单跑巡检 ⇒ 输出里有审计水位段；构造超阈场景 ⇒ WARN 真出现 | `03_drill_fake_stream.txt`（A2/A3 WARN、A4 CRIT）、`04_mcp_probe.txt`（MCP 面 WARN/CRIT）、`07_patrol_single_run.txt`（单跑输出含 `AUDIT_STATUS=` 行） |
| ② `storage_waterline.py` 输出含 `runtime/audit/` | `05_storage_waterline_local.txt`（"本机 runtime/audit 审计流…"行） |
| ③ 巡检耗时增量 <1s | `06_timing.txt`：`mas_audit status --json` 229 ms、`audit_waterline --json` 144 ms；MCP 面自报 0.1–0.2 s |
| ④ 不新增渠道、不改其它巡检步骤判据、不改 `mas_audit.py` 写路径 | 本卡 diff 只新增：新工具 + MCP 工具 + prompt 收尾项 + storage 本地段；`tools/mas_audit.py` 零改动 |

## 4. 复现命令（全部离线可跑）

```bash
PY312=C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe
$PY312 tools/audit_waterline.py                 # 真机判据（文本 + 末行 AUDIT_STATUS=）
$PY312 tools/audit_waterline.py --selftest      # 6 场景自测（假流，tempfile）
$PY312 tests/audit_patrol_drill.py              # A1–A4 + B1–B3 演练（DRILL=PASS 7/7）
$PY312 tests/observe_audit_mcp_probe.py         # MCP 面探针（MCP_PROBE=PASS 6/6）
$PY312 tools/storage_waterline.py --local-only  # 含 runtime/audit 段
```

## 5. 回滚

1. **巡检 prompt**（唯一动到"调度面"的地方）：`jobs.json` 动手前已备份
   `%LOCALAPPDATA%\hermes\profiles\mas\cron\jobs.json.bak-auditpatrol-20260928-105816`；
   恢复即回滚（jobs.json 是唯一真源，ticker 下一拍即生效）。等价命令：
   `hermes -p mas cron edit 52b88b0ffca0 --prompt "<patrol_prompt.before.txt 内容>"`
   （旧稿留档 `patrol_prompt.before.txt`，本目录可复现）。
2. **MCP 工具**：删掉 `mas_observe_mcp.py` 里 `audit_waterline` 的 schema/分支/`_KNOWN_TOOLS` 项
   即回滚（或把该 server 从 `platform_toolsets.cron` 摘掉，代价=整个只读观察面没了）。
   注意：巡检 prompt 里已写"工具面未提供 ⇒ `AUDIT_STATUS=UNKNOWN:…`"，缺工具不会让巡检报假绿。
3. **storage_waterline**：`--runtime` 参数与 `audit_stats()` 段可独立删除；其它段未动。
4. **新工具文件**：`tools/audit_waterline.py` 无副作用，删除即消失（另有 MCP 侧引用，见 2）。

## 6. 注意 / 边界

- **热点文件**：`tools/mas_observe_mcp.py`、`docs/TOOLBOX.md`、`tools/tests/test_regression.py` 今日有其它卡
  在并发编辑（本卡动手时 `mas_observe_mcp.py` 正被 kanban_health 卡改）。本卡对该 server 只做**加法**
  （新 schema + 新分支 + 白名单项），已跑 `--check` 全绿（9 工具）。
  **未把本判据加进 `tools/tests/test_regression.py`** 就是为了避开这个热点——离线回归由
  `tests/audit_patrol_drill.py` 独立承担（后续如要并入回归套件，加一条读 `aw.evaluate()` 的纯函数用例即可）。
- **不改 `tools/mas_audit.py`**：判据全在本卡新增的包装层；`mas_audit.status()` 只读取数。
  唯一继承副作用：轮转到期时 dry-run 判定会确保 `archive/` 目录存在（幂等，不搬数据）。
- `storage_waterline.py --local-only` 的 rc=2 来自**既有**的本机 D: 低水位 WARN（59.8GB < 60GB），
  与本卡改动无关（见 05 文件末行）。
- 巡检写行的纪律：WARN/CRIT **只报告/告警**；`rotate --apply` / `prune --apply` 一律人工。

## 7. 交接与冻结（lead 协调 11:10 / 11:12）

- **本卡已冻结** `tools/mas_observe_mcp.py` 与巡检 prompt 的编辑；共享文件的**集成 owner = t_403cd83b**
  （它负责 event_calendar 的 `UPCOMING=` + 合并 kanban_health/audit_waterline + 最终刷新/实调）。
- **验收缺口定位**（lead 复核 11:10 builtin 报告「主表 14 行、无 `AUDIT_STATUS=`」）：见 `07_patrol_builtin_report_note.txt`。
  该 tick **10:57:54 起跑**，用旧 prompt；本卡 prompt 应用时刻 **11:00:44**（晚 3 分钟）。
  同报告 `MCP_API_TOOL_COUNT=38`（10+6+5+2+**9**+6）⇒ mas-observe 已 9 工具 ⇒ `audit_waterline` 在当轮工具面里就绪。
  ⇒ 剩余动作 = 「一轮带新 prompt 的巡检」：下一拍 tick（约 11:27）自动覆盖，或由 t_403cd83b 实调一次。
- **给 t_403cd83b 的集成件**：`integration/patrol_prompt.patch`（本卡 prompt 增量 diff）、`patrol_prompt.after.txt`
  （现版原文，已在 jobs.json 生效）、`apply_patrol_prompt.py`（应用脚本，含"读出即校对"）。
  ⚠ 若用 10:35 的 prompt 快照重放，会抹掉 `8)` 与主表 `AUDIT_STATUS` 行。
- 本卡**未改** `tools/tests/test_regression.py`（并发热点）；离线回归由 `tests/audit_patrol_drill.py` +
  `tests/observe_audit_mcp_probe.py` 独立承担。
