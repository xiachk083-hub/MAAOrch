# KB 关系网 · 全量回填（t_698de35d）

日期：2026-09-30 ｜ 执行：mas（win108 本机） ｜ 前置 pilot：`docs/audits/2026-09-30_kb_graph_pilot/README.md`

## 做了什么

在 pilot（t_7f7c031c：wiki_graph.py 生成 311 张 card-t_* 页 + cards-index）基础上，把看板台账（`docs/audits/2026-09-30_kb_done_inventory/cards.jsonl`，311 行）按 suggested_partition 聚合成 **7 张域 MOC 页 + 1 张全库关系图页**，落 `D:\1-2\MAS-KB\_generated\cards\`：

- `moc-cards-entities.md`（110 卡）/ `mechanisms`（25）/ `incidents`（7）/ `playbooks`（75）/ `runbooks`（38）/ `changes`（7）/ `others`（49，仅指向）
- `moc-cards-relations.md` — Mermaid `graph TD`：每分区一个 subgraph（MOC ↔ 前 60 卡），center 页互链 7 MOC。

## 工具与幂等验证

生成器：`docs/audits/2026-09-30_kb_backfill/wgb_backfill.py`（copy 也已在 KB tools 侧？否——归 MAAOrch docs/audits 存放）
判定链（全部实测）：

- `--dry-run` → 计划 8 页，`DRY-RUN=OK`
- `--apply` #1 → `files=9 changed=9` `BACKFILL_APPLY=OK`（8 新页 + 台账 bf_part 字段回写）
- `--apply` #2 → `changed=0`（幂等）
- `wiki_graph.py --check` → `CHECK=OK`，`parse_errors=0`
- `wiki_gov.py` → `ORPHAN=0`（新 MOC/图页全部被 cards-index 与互链可达），`UNVERIFIED=9 / DRIFT=5` 为既有水位，非本任务引入

## 红线遵守

- 只写 `_generated/cards/`（机器区）+ 台账 JSONL 追加字段；人写区零触碰、未提交他人脏文件。
- 提交仅含 8 张新页（`MAS-KB@76ceec4`），KB 库内 CRLF 警告为正常。
- 台账 kb_state 原值未动，仅追加 `bf_part` 字段。

## 后续（不属本卡）

- `WIKI_DRIFT=5`（facts/board、dispatch、lists/audits 等机器清单落后于条目）→ 应走 wiki_sync 收敛，另立卡。
- `UNVERIFIED=9` 条目待 verify 流转，另立卡。
