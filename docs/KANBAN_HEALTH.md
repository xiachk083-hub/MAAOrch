# kanban_health.py —— 看板治理体检：重复卡 / 僵尸卡 / 长期 blocked / 崩溃重派 / 孤儿产物

> 两块板（`maa-parallel-benchmark-20260926` + `arknights-community`）已 180+ 张卡；09-27 一天就出现
> 重复波 12 张（白烧 2,572s）、峰值并发 31、10 次崩溃、批量卡长期挂着没人管。**人工肉眼盯不过来。**
> 本工具把「运维手册里靠人看」的四类病 + 一类数据完整性问题做成**可复跑、可机检、零写入**的体检：
> 只出**逐类清单 + 建议动作**（归档/重派/补注释/合并），**绝不自动处置**（处置权归 lead，见 `KANBAN_DISPATCH_POLICY.md` §4/§6）。
>
> 文件：`tools/kanban_health.py` ｜ 产物：`runtime/reports/kanban_health_<ts>.md` + `.json`（schema `maaorch.kanban_health/1`）
> 实测证据：`docs/audits/` 同批审计（本文 §4 的样例即 2026-09-28 09:47 实跑）

**与姊妹工具的分工**（各有盲区，互补不重复）：

| 工具 | 只管 | 输出 / 动作 |
|---|---|---|
| 内核 `detect_stale_running` / `release_stale_claims`（`kanban_db_dispatch.py`） | 僵尸卡**自动回收**（4h + 心跳断 1h ⇒ 重排队） | 写操作（回收/重派） |
| `hermes kanban diagnostics`（内核 `kanban_diagnostics.py`） | 单卡「可修 distress」信号（repeated_failures / protocol_violation / review_deadlock…） | 逐卡诊断 + 按钮动作 |
| `hermes kanban stats / list / show` | 计数与单卡视角 | 只读查询 |
| **`kanban_health.py`（本文）** | **板级体检**：跨卡聚合（重复对、批量崩溃）+ 五类病检测 + **产物是否真落盘** + 调度器是否停摆 | 清单 + 建议动作（不改卡） |
| `docs/audits/*_kanban_throughput.*`（成本复盘） | 历史成本（调用数/工具耗时/空转） | 事后审计，不实时 |

## 1. 用法

```bash
PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"
$PY312 tools/kanban_health.py                      # 扫全部板 → 写 md+json → 摘要 + 末行 KANBAN_HEALTH=
$PY312 tools/kanban_health.py --board maa-parallel-benchmark-20260926
$PY312 tools/kanban_health.py --line               # 只打一行 KANBAN_HEALTH=（巡检取数）
$PY312 tools/kanban_health.py --json               # 单行 JSON（含 line 字段，不写盘）
$PY312 tools/kanban_health.py --no-write           # 不写盘
$PY312 tools/kanban_health.py --selftest           # 45 例离线自检（合成 DB，不碰真板）
```

选项：`--boards-root P`（默认由 `HERMES_KANBAN_DB` 推导，再退 `%LOCALAPPDATA%\hermes\kanban\boards`）｜
`--zombie-h 4`｜`--hb-gap-min 60`｜`--blocked-h 6`｜`--dup-ratio 0.86`｜`--long-run-min 60`｜
`--board-cap 6`｜`--fail-limit 2`｜`--batch-window-min 20`｜`--extra-root P`（可多次）｜`--limit N`｜`--ts TS`

**退出码**：`0` 全绿 ｜ `2` 有 P2/P3 ｜ `3` 有 P1（需处置）｜ `1` 内部错误 ｜ `4` 一块板都没扫到。
与 `host_health.py` / `mas_switch_gate.py` 同语义。

行内格式（每类计数只数 **P1·P2 需动作项**；P3 留痕只进 `P3=`）：

```
KANBAN_HEALTH=ts=20260928_101813,boards=2,cards=182,running=21,dup=3,zombie=0,blocked_stale=1,fail=1,orphan=0,P1=0,P2=6,P3=18,status=WARN
```

## 2. 检测项（判据 · 阈值 · 证据 · 建议动作）

| # | 类别 | 判据（全部可从 `kanban.db` 复算） | 严重度 | 建议动作（**工具只建议，不执行**） |
|---|---|---|---|---|
| ① | **重复/近重复卡** | 标题 NFKC 归一后 difflib 相似度 ≥ `--dup-ratio`（默认 0.86）或前缀包含（长度比 ≥0.7）；同 `idempotency_key` 非归档卡 = 硬重复 | 活跃对 P1 / 双 done P2 | 保留先建或在跑的那张；另一张要点 comment 汇总后 `archive` |
| ② | **僵尸卡** | running 且 运行时长 ≥ `--zombie-h`（默认 4h）且（心跳 NULL 或断 ≥ `--hb-gap-min`，默认 60min）——与内核 `detect_stale_running` 同判据 | 从未心跳 P1 / 心跳断 P2 | 内核下一 tick 自动回收；10min 未动再查调度器/手动 `reclaim` |
| ②' | **claim 过期未回收** | `claim_expires` 过期 ≥20min 而卡仍 running（claim TTL 15min，活着的心跳会续租） | **P1** | 先查调度器/网关（回收循环没跑）；别只单卡 reclaim |
| ②'' | 长跑卡（活着） | running 墙钟 ≥ `--long-run-min`（默认 60min）且心跳新鲜 | 到 max_runtime 90% P2 / 否则 P3 | 复核 `logs/t_<id>.log` 工具调用数，决定是否拆卡 |
| ③ | **长期 blocked** | blocked ≥ `--blocked-h`（默认 6h）且阻塞后无新注释；`dependency` 且父卡全 done 单独列 | 依赖僵持 P1 / 等人工 P2 / 等上游 P3 | 补「等谁/等什么/怎么解/复查时间」；僵持卡 `unblock`；无 kind 的补 `--kind` |
| ④ | **崩溃/超限** | `gave_up` 未解（failure_limit 落地）｜`consecutive_failures ≥ 2`｜`timed_out`（迭代预算 150 轮 / max_runtime）｜同卡 ≥2 次 crashed｜`block_loop_detected` / `completion_blocked_empty_result` / 协议违规 | 止损/到期 P1；预算超限/反复崩溃 P2 | 修环境/改卡面 → **comment 写重试备注 → 再 promote**（禁止裸 promote，§4.3）；预算用满 = 拆卡 |
| ④' | **批量崩溃聚合** | 同窗口（`--batch-window-min`，默认 20min）同错误家族 ≥3 卡 | P2（成员卡降 P3 留痕） | 先定性（环境/网关抖动），修完再放跑；同因 ≥2 卡升级系统级专项 |
| ⑤ | **孤儿产物** | done/review/archived 卡声明的产物路径（run metadata `artifacts` 等键 + 摘要文本）在盘上不存在 | 真缺 P2 / 摘要弱证据 P3 / 附件丢失 P1 | 补产出并把路径写回卡；产物在别处就 comment 真实位置 |
| ⑥ | **板级态势** | running > `--board-cap`（默认 6，`KANBAN_DISPATCH_POLICY` §7 建议值） | P2 | 新波暂缓（待发卡留在 todo）；已跑的等收口 |

**误报防线（都是实测踩出来的）**：

* 产物路径五态判定：`exists` / `staged`（同名附件兜住）/ `cleaned`（scratch 工作区完成即清理，**预期行为**）/
  `remote`（本机无此盘 —— 如远端主机 `E:\AUTO-MAS\...`：不核验、不计孤儿）/ `missing`（只有这里才报）。
* 展开 `foo.json/.md` 这类「基名 + 多扩展名」缩写；**丢弃** glob/占位（`{a,b}`、`*`、`?`）与省略号（`...`、`…`）token。
* 相对路径解析根：本仓库 + 相邻工程（`MAS-KB` / `arknights-community`）+ 该板 `board.json` 的 `default_workdir` + `--extra-root`。
* 已归档的重复**只登记不告警**（附录列出，避免后人再翻一遍）。
* `no_artifact` 仅当附件/artifacts/交付物键/summary/result **全空**才报。

## 3. 产物

| 文件 | 内容 |
|---|---|
| `runtime/reports/kanban_health_<ts>.md` | 人读报告：TL;DR 表 + 逐类清单（每条含「事实 / 建议 / 命令」）+ 已归档重复附录 + 复算说明 |
| `runtime/reports/kanban_health_<ts>.json` | 机读版（`schema=maaorch.kanban_health/1`）：`boards[].{cards,by_status,findings[],orphan_stats,resolved_dups}`、`totals{P1,P2,P3}`、`line` |

`findings[]` 每条：`cat`（dup/zombie/blocked/fail/orphan/board）、`code`（细类）、`sev`（P1/P2/P3）、
`board`、`ids`、`title`、`detail`（事实）、`action`（建议）、`cmd`（可粘贴命令，可能为空）。

## 4. 实测样例（2026-09-28 10:18 首跑，两块板 182 卡）

```
KANBAN_HEALTH=ts=20260928_101813,boards=2,cards=182,running=21,dup=3,zombie=0,blocked_stale=1,fail=1,orphan=0,P1=0,P2=6,P3=18,status=WARN
```

当时真实问题（全部人工复核过，不是误报）：

* **重复对 ×3（P2）**：`t_183e9907≈t_597ec823`（数据①）、`t_64097d9a≈t_f662b13c`（能力③）、`t_cf2dc452≈t_87409df9`（能力②）——
  三对都是 09-27 重复波产物、**双 done 没收口**：留一张 + comment 汇总 + archive 另一张即可。
* **长期 blocked ×1（P2）**：`t_c8b36f5e`（mas-wiki 接线）阻塞 11.1h，**无 kind、无注释、无解锁条件** ⇒ 补一条 comment 或重 block 带 `--kind`。
* **批量崩溃 ×18（P2）**：09-28 09:40 一窗内 18 张卡同家族（worker 退出）崩溃后被重派，全部 `consecutive_failures=1`
  ——**再各失败一次就会吃满 failure_limit 自动止损**；这类必须按家族定性（当日为网关/环境抖动）后统一处置，而不是逐卡折腾。
* **板级并发 21 > 6（P2）**：属「重复波叠正波」的残留，新波应立即刹车（待发卡留 todo）。
* 僵尸 0、claim 过期 0、孤儿产物 0（含远端 `E:\` 路径与跨工程相对路径，均已按 §2 的口径排除，无假阳性）。
* 另有 18 条 P3 留痕（批量成员、已归档重复 21 对），供复盘不占 P2。

## 5. 接线（已接线：MCP `mas-observe.mas_kanban_health`）

* **巡检一行**：任何有 terminal/MCP 的巡检跑 `--line`，把末行 `KANBAN_HEALTH=…` 原样摘进报告即可
  （与 `SWITCH_GATE=` / `HOST_HEALTH=` 同模式）。
* 巡检 job（`52b88b0ffca0`，`mas-readonly-inspection`）工具面**只有 MCP** ⇒ 已把本工具包成 MCP 工具
  `mas-observe.mas_kanban_health`（`tools/mas_observe_mcp.py`，卡 t_4cd871de；集成收口 t_403cd83b）：
  巡检 prompt v4 第三步第 9 项 + 主表 `KANBAN_HEALTH=` 行；MCP 面探针 `tests/kanban_health_mcp_probe.py`（15/15）。
  写行口径：默认 `--line` 一行；要 WARN/CRIT 的 P1/P2 条目标题时调 `mas_kanban_health(json=True)`（findings 明细，只列标题）。
* 定时建议：每日一次（如 09:10，避开 05:00–05:40 / 19:30–20:30 窗口）+ 发波前手动跑一次。

## 6. 局限（已知，别当它万能）

* 只在**本机能看到的路径**上核验产物；远端主机的产物一律 `remote` 不核验（要核验需 SSH，本工具刻意不连网）。
* 标题相似度是启发式：**同系列不同对象**（如「资产普查①/②」）不会被判重，但「同一张卡改名重发」可能被判重 —— P1/P2 项都要人过一眼再动手。
* `consecutive_failures` 含 claim 过期回收（内核语义：过期 claim 也是非成功尝试），所以它涨到 1 未必等于「跑挂过」；报告里同时给了
  最后一次失败事件与家族，别只看数字。
* 阈值全部写在本文件顶部 `THR`（改阈值 = 改口径）：改完请同步跑 `--selftest` 与本文 §2 表格。

## 7. 维护

* 自检：`$PY312 tools/kanban_health.py --selftest` → `SELFTEST=PASS (45 checks, 0 failed)`（合成 DB，覆盖五类检测 + 误报防线 + 汇总/行/退出码 + 阈值覆盖）。
* 内核判据若变更（回收阈值、failure_limit、终态协议），先改 `THR` 与 §2 表格，再补一条自检例。
* 相关：`docs/KANBAN_DISPATCH_POLICY.md`（怎么派活）、`docs/KANBAN_TASK_TEMPLATE.md`（写卡模板）、
  `docs/audits/2026-09-27_kanban_task_inventory.md`（全量卡清单快照）。
