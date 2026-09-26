# 不携带游戏内数据（架构纪律）

**要求（用户 2026-09-24）**：项目默认**不带任何游戏内数据** —— 游戏会更新、会加活动，
所以不能依赖"某一版本的游戏数据快照"，必须**免维护**。

## 一、哪些东西属于"游戏内数据"（不许随项目发布/依赖）

| 曾经依赖的东西 | 为什么是负担 | 改成 |
|---|---|---|
| `data/_stage_table.json`（关卡表：代号/理智/难度） | 游戏一更新就过期，要重新导出 | **运行时从画面读**：地图上的关卡代号（OCR）+ 面板上的"开始行动 −N" |
| 手填的节点坐标 `data/nav_nodes.json` | 新活动就要我重新读图填表 | **`/nodes` 六边形自动检出**（颜色+形状），坐标不再手填 |
| 活动列表 `EVENTS`（写死的 act 代号/名称） | 新活动要改代码 | 网格页**自动裁卡** → 模板库；act 代号由**进图后地图代号反查** |
| `data/copilot/screening/*.json`（作业站缓存） | 只是缓存，可随时重建 | 保留（缓存不是数据源） |

## 二、允许留在仓库里的

- **代码**（流程、判据、服务）
- **UI 模板**：`prime_badge.png`（源石徽章）等**界面元素** —— 界面比关卡表稳定得多，
  且一旦变了，闸门会报"读不到徽章"，属于**可观测的失败**而不是静默出错
- **学习产物**：`runtime/learned/*.json`、`data/templates/events/*.png`、`runtime/axis_outcomes.jsonl`
  —— 这些都标注为"运行时生成"，删掉也能自动重建

## 三、运行时的自发现链路（免维护的关键）

```
曲谱/完成进度页  →  自动裁出所有卡片（固定网格几何）→ 我（视觉）认名 → 模板库
      ↓
用模板进活动      →  地图上 OCR 读关卡代号（EA-1 / PV-EX-3 …）→ **落库 runtime/learned/stages_<前缀>.json**
      ↓                                     （学到的关卡数 < 应有数 ⇒ 告警，不静默用残缺清单）
/nodes            →  六边形自动检出 → 节点中心即点击目标（不查坐标表）
      ↓                                  （key 一律带前缀：`ST-1` → `EA-ST-1`，落库前校验）
/panel            →  徽章(prime_badge) 判"有没有源石" + 读理智消耗（写进 stages 文件的 ap）
      ↓
打完后 /panel 复核 + 大厅源石数核对 → 落账本（runtime/axis_outcomes.jsonl）
      ↓
队列展开单位      → **learned 优先**（stages_<前缀>.json）→ 读不到才退回 _stage_table.json，并在日志写明来源
```

## 四、当前迁移状态（诚实标注）

| 环节 | 状态 |
|---|---|
| 卡片模板库（14 张）+ 自动裁卡脚本 | ✅ 已免维护（`runtime/_cardharvest.py`） |
| `/nodes` 六边形自动检出 | ✅ 已实现（EP/CV 实测有效），**尚未接进 `nav_map`** |
| `tools/discover_stages.py` 运行时读关卡 | ✅ 已实现并落库（`--save` 写 `runtime/learned/stages_<前缀>.json`；支持 `--shot` 离线重放与 `--expect` 告警） |
| 队列展开单位 | ✅ **已迁移**（2026-09-24）：`stage_costs()/build_units()` **learned 优先**、旧表兜底；日志首行打印`单位来源：learned(N 个前缀) + 旧表补 M 条`；learned 关卡数 < 应有数时**告警** |
| 进图后自动学关卡清单 | ✅ **已接**：`_prime_queue3.maybe_learn_stages()` 在"进活动成功"后调用（带 12h 节流 + 240s 超时；只在缺失/太旧/上次有告警时跑，不会每步 spawn） |
| `nodes_*.json` 的 key 少前缀 | ✅ **已修**：`learn_map.normalize_prefix()` 统一补前缀 + 落库前校验 + 清理旧的无前缀 key（实测 `ST-1` → `EA-ST-1`；`--fix-prefixes` 可原地修已有库） |
| `EVENTS` 里写死 act 代号 | ⚠️ **仍待迁移**：改为按卡片模板名匹配 + 进图后反查代号 |

## 五、还剩哪些地方依赖版本快照（2026-09-24 现状）

| 依赖 | 文件（行号） | 性质 | 影响 |
|---|---|---|---|
| `data/_stage_table.json` | `tools/plan_mines.py:71`（兜底）、`runtime/_prime_queue3.py:91`（兜底 + **突袭档**）、`tools/prime_plan.mjs:27`、`tools/push_schedule.py:23`、`tools/push_driver.py:112`、`tools/workspace.py:115`、`runtime/plans/_story_mines_build.py:35` | 19.6MB 版本快照 | 队列/排产已能只靠 learned 跑（见自证 ④）；但**突袭档**目前只能从旧表或"面板学到 hard 标记"得知，旧表被拿走后只剩普通档 |
| 关卡地图/波次数据 `data/_level_<stage>.json` | `tools/axis_viz.py:33`、`stage_cut.py:148`、`stage_arrivals.py:30`、`stage_value.py:65`、`stage_plan.py:27`、`job_dp_check.py:43`、`ce6_cut_check.py:99`、`ce6_spawn_order.py:22` | 关卡静态数据快照（路线/波次/初始费） | 轴合法性校验、DP 可行性、落点可部署性判断仍靠它；屏幕上看不到路线与波次 ⇒ **最难迁移的一块** |
| 干员/技能/敌人表 | `data/_character_table_full.json`、`_skill_table.json`、`_enemy_db.json`、`_enemy_handbook.json`（`job_dp_check.py:18-19`、`axis_viz.py`、`cost_model.py` 等） | 版本快照 | 费用/技能/敌人属性推断；干员盒（OperBox）只能替代"持有与练度"那一部分 |
| 作业站缓存 `data/copilot/**` | 多处 | 缓存（可重建） | 允许保留 |

**验收证据**（可复跑）：
- 自证脚本 `runtime/plans/_task6_tests.py`（20 项断言全绿）→ 日志 `runtime/reports/task6_selftest.log`
- 旧表不可读时队列仍列出该活动单位 → `runtime/reports/task6_no_table_test.log`（`单位来源：learned(1 个前缀) + 旧表补 0 条｜learned 覆盖 8/8｜EP-EX-1..8 全部列出`）
- 旧表在时不回归（195 个单位，与改动前一致）→ `runtime/reports/task6_table_present_test.log`
- 残缺清单告警 → `runtime/reports/task6_learned_warning_test.log`（`⚠ EA: learned 只有 1 关 < 应有 16 关 ⇒ 清单残缺，需要重学`）

> **关于"把 `_stage_table.json` 临时改名"这条验收**：本轮**没有**改名字面执行 —— 因为当时 Lead 正在跑
> `tools/stage_attempt.py`（PID 29248）、fixer-1 的脚本也在跑，改名会打断他们正在进行的真机验证。
> 改用**等价且非破坏**的做法：`MAAORCH_TABLE=<不存在的路径>` 让代码走"旧表不可读"的**同一条分支**
> （`except` 里只用 learned），证据见上；等模拟器空下来可随时用 `runtime/plans/_task6_rename_test.py`
> 做一次字面改名验证（脚本已写好：改名 → 跑 `--dry --no-save-state` → finally 里无条件改回）。

