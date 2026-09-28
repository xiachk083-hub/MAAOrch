# prompt 增量片段：kanban_health P1/P2 明细（卡 t_4cd871de → 合并方 t_403cd83b）

**状态：未应用**（本卡按 lead 11:45 协调不直接改 prompt；由集成 owner t_403cd83b 复核合并，或由 lead 裁定放弃）。
**基线**：job `52b88b0ffca0` prompt v4（7246 chars / sha256(utf-8) 前16 = `9b41eaa33303614e`；快照 = 同目录 `live_prompt_now.txt`）。
**目的**：落实本卡原文「WARN/ALERT 时列出报告里的 P1/P2 标题（不要全文粘）」的严格式。v4 已具备的部分（主表 `KANBAN_HEALTH=` 行 / 第三步第 9 项 / 预算 23）无需再动。

## 替换（每条必须恰好命中 1 次；不命中就说明基线已变，停手）

### R1 · 硬性预算行的「同工具上限」表

````
old: kanban_health ≤1；
new: kanban_health 行+明细 ≤2；
````

### R2 · 第三步第 9 项（加明细步骤；注意与「调 1 次、别重试」互斥，故一并改写）

````
old: 由 lead/人工处置」。调 1 次、别重试。工具面未提供
new: 由 lead/人工处置」。status 非 OK（WARN/CRIT）⇒ 再调 1 次 `mas_kanban_health(json=True)`，证据行列出 P1/P2 条目标题（≤4 条，`[P2] <标题>` 形式、顿号分隔——只列标题，不粘详情/命令/全文）。行+明细合计 ≤2 次。工具面未提供
````

## 应用口径（合并方执行）

1. 应用前备份 `%LOCALAPPDATA%\hermes\profiles\mas\cron\jobs.json`；替换后
   `hermes -p mas cron edit 52b88b0ffca0 --prompt <new>`（本仓既有做法：`apply_patrol_prompt.py`），然后**逐字节回读比对**（v4 收口同款口径）。
2. 回滚：恢复备份即生效（jobs.json 为唯一真源）。
3. 能力已被探针验证，可放心引用：`mas_kanban_health(json=True)` → `schema=maaorch.kanban_health/1`、`totals.P1` 与 line 一致、
   findings 每条含 `cat/code/sev/title/detail/action/cmd`（证据：本目录 `10_mcp_probe.txt` ④ 组 + `json_peek.py` 实测）。
4. 若 lead 裁定保持 v4 现状（单次调用、去 docs 看清单），本片段直接作废，无需任何动作。
