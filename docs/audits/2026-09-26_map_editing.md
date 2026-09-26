# 地图编辑 / 标注线 · 现状勘定与最小可用路径

- 项目：D:\1-2\MAAOrch（过渡层/粘合剂：Harness / MAA / MAS 之间）
- 快照：**2026-09-26**（只读分析；本文件是本次唯一新增产物，未改任何代码）
- 方法：读代码 + 实测数据目录（`Get-ChildItem` / `Get-Content`）+ 全仓引用扫描；结论均带 `文件:行号`
- 铁律遵守：未执行任何 git 命令

---

## 0. 一页结论

| 问题 | 结论 |
|---|---|
| 现在有没有"地图编辑"能力？ | **没有。** 没有任何一处能把地图/关卡数据写回并生效的编辑面。 |
| 那"编辑"发生在哪？ | 现有唯一的编辑能力在 **作业（部署计划）** 维度：`tools/viz_plan.py` 的页面可以改干员位置/朝向/动作顺序/换人，并**真的写回**（导出 JSON 收编、或 POST 到 `tools/ws_server.py` 存成版本/cleared/备注）。 |
| 地图数据本身是什么？ | **两份外部数据的本地缓存**：① 关卡逻辑 `data/_level_<stage>.json`（公开镜像 Kengxxiao/ArknightsGameData 的 `levels/<levelId>.json`）；② 地块几何 `Arknights-Tile-Pos`（**MAA 安装目录里的第三方资源**）。二者都是只读输入。 |
| "标注"这条线有产物吗？ | 有，而且有持久化：活动/页面**模板 PNG**（`data/templates/`）、格网体检**登记表**（`data/tilepos_audit.json`）、格网参照（`data/grid_ref_<stage>.npy`）、活动地图**节点坐标**（`data/stage_nodes.json`）。但只有"从截图学一张模板"这一个半自动写入口（`tools/event_templates.py --learn`），没有标注 UI。 |
| 今天要"改地图"实际怎么做？ | 只能**改 Python 常量**（`tools/viz_plan.py:43-53` 的 `STAGE / DEVICE / WALK`）或**手改缓存 JSON**。手改 `data/_level_<stage>.json` 不会被现有代码覆盖（三个写入点都只在文件缺失或 <1000 B 时才写，见 §1.2），但没有任何来源标记/版本/diff/校验。 |
| 成为真编辑器的最大障碍 | 不是 UI，而是**数据层没有"可写的自有副本"** + **坐标口径分散且踩过雷** + **下游有 16 个分析器直接吃同一份 `_level_` 文件**。 |
| 最小可行路径 | 复用 `viz_plan` 的 HTML 层 +**新增 overlay 文件**（`data/map_edits/<stage>.json`）+ 一个唯一写口（`tools/map_overlay.py` 或扩展 `ws_server` 的 `/api/map/save`），三步走：S1 只做地块属性编辑与页面预览（不动下游）→ S2 让 2 个消费端接 merge（无 overlay 时行为与今天完全一致）→ S3 才碰路线/波次。详见 §4。 |

---

## 1. 数据源头：地图与关卡数据从哪来（问题 1）

### 1.1 `viz_plan.level()` 读什么

`tools/viz_plan.py:60-66`：

```python
lf = PROJ / "data" / ("_level_%s.json" % stage)
if not lf.exists():
    raise RuntimeError("缺少关卡文件 %s（先下载该关 level 数据）" % lf)
d = json.load(open(lf, encoding="utf-8"))
T, M = d["mapData"]["tiles"], d["mapData"]["map"]
H, W = len(M), len(M[0])
```

即**全部**地图语义都来自这一个文件：

| 用途 | 字段 | 代码 |
|---|---|---|
| 格类型 | `mapData.tiles[M[y][x]].tileKey` | `viz_plan.py:74-75` |
| 可部署性 | `…buildableType` | `viz_plan.py:77-78` |
| 可走集合 | 常量 `WALK`（**不在数据里，在代码里**） | `viz_plan.py:48-53` |
| 敌人路线 | `routes[].startPosition/endPosition/checkpoints/motionMode` | `viz_plan.py:127-190` |
| 出怪波次 | `waves[].fragments[].actions[]`（`actionType=="SPAWN"`） | `viz_plan.py:205-244` |
| 关卡选项（初始费/回费） | 同文件 → `cost_model.load_level_options` | `viz_plan.py:539`、`cost_model.py:31-33` |
| 敌人移速 | `data/_enemy_db.json` | `viz_plan.py:192-204` |
| 干员射程 | `services/maa/instances/2/resource/battle_data.json` | `viz_plan.py:300` |
| 练度 box | `data/operbox_batch/emu2.json` | `viz_plan.py:248` |
| 角色表 | `data/_char_table.json` | `viz_plan.py:390` |

**结论：不是自己标的。** 是官方关卡数据的镜像 + MAA 资源。

### 1.2 这两份数据的"写权"在谁手里（关键）

| 数据 | 来源 | 本仓的写入点 | 实测行为 |
|---|---|---|---|
| `data/_level_<stage>.json` | `https://…jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/levels/<levelId>.json`（`tools/stage_value.py:39-41`；`tools/rogue_routes.py:156-157`） | `stage_value.py:63-72`、`workspace.py:322-345`、`rogue_routes.py:138-164` | **三处都只在"文件不存在或 <1000 B"时才写**（`stage_value.py:66-67`、`workspace.py:329-330`、`rogue_routes.py:146-147`）⇒ 手改**不会被自动覆盖**，但删掉/换机即重拉，且无版本、无 diff、无"这份是人改的"标记 |
| `Arknights-Tile-Pos/<stageId>*.json`（`overview.json` + 逐关） | **MAA 安装目录**（第三方资源） | 本仓**没有任何写入点**（只有读） | 路径散在三处硬编码：`tools/stage_cut.py:26-27`、`tools/own_axis_job.py:32-34`、`tools/ce6_cut_check.py:30-31`；另有一个集中入口 `services/ai_agent/percept.py:226-227`（`ResourcePaths.tile_pos_dir`）与 `services/ai_agent/tile_map.py:204`、`services/stage_probe.py:191` |

实测数据目录：`data/_level_*.json` **104 个**（含肉鸽图 `_level_从众效应.json` 等中文名）。

### 1.3 坐标：两套约定并存（编辑器的头号雷）

- `viz_plan.py:69-75`：关卡 `mapData.map` 的 **y 直接索引** `M[y][x]`（2026-09-16 用 MAA Tile-Pos 定案）；但同一文件里 `routes[].startPosition.row` 的行序**相反**，要 `H-1-row`（`viz_plan.py:118-124`，判据 334/338 = 99%）。
- `own_axis_job.py:10-13`：作业落点必须用 **Tile-Pos 索引**（4220 个真落点实测 99.5% vs 74.5%），落错格 = 整份作业白跑。
- `stage_cut.py:255`：还要在输出里解释"行号是否上下翻转"。

**任何地图编辑器都必须先固定一套坐标契约并把换算写成一个函数**，否则编辑出来的数据在 `own_axis_job`（落点校验）与 `viz_plan`（渲染）之间会静默错位。

---

## 2. 现在到底有没有"编辑"能力（问题 2）

### 2.1 唯一真有编辑的是 `tools/viz_plan.py`，但编辑对象是**作业**

写回路径（两条都真实存在）：

1. **导出 → 收编**：页面 `exportJob()`（`viz_plan.py:1186-1196`）下载 `<stage>__<作业名>__<日期>.json`；命令行 `viz_plan.py --ingest`（`:679-706`）把它收进 `data/copilot/<stage>_edited/`，而该目录在池子扫描里排第 ② 位（`:355-388`）⇒ 下次渲染就会带上。
2. **页面直接存**：`wsPost()`（`:1213-1214`）POST 到 `tools/ws_server.py`（端口 8898），按钮 = 保存为 cleared / 保存为新版本 / 保存备注（`:1364-1366`），落点 `data/workspaces/<账号>/<活动>/<关卡>/{cleared.json,notes.md,versions/}`（`ws_server.py:11-21`，实测 386 文件 / 6.26 MB）。

页面能编辑什么（逐条）：点地图放干员并做"能不能放"校验（`viz_plan.py:1022-1029` + `cellDeploy`）、再点干员切朝向（`:1016-1018`）、动作序列拖动排序/上下移（`:1068-1075`）、换替代干员（`:1084` → `wsReplaceOp`）、实时重算冲突与费用判漏（`:1138-1149`）。编辑态另有 localStorage 暂存（`:1094-1104`）。

**地图本身在整条链里只被读**：`level()` 只 `json.load`，没有任何写回。

### 2.2 其余"标注"工具逐个判定

| 工具 | 读 | 写 | 判定 |
|---|---|---|---|
| `tools/viz_plan.py` | `data/_level_<stage>.json` + box + 作业池 | `data/visualization/<stage>.html`（`:632-677`）、`data/copilot/<stage>_edited/`（`:679-706`）、workspace 版本（经 ws_server） | **作业编辑器 + 地图只读可视化**（地图不可编辑） |
| `tools/annotate_live.py` | 实时帧/固定图（`:545`）、`data/tilepos_audit.json` 三态闸（`:538-541`） | `data/annotated_*.png`（`:647`）、`data/grid_ref_<stage>.npy`（`:569-586`）、`--json` 一份测量 JSON（`:650`） | **验收物渲染器**（画给人看"我认出了什么"，`:3`），非编辑器 |
| `tools/annotate_view.py` | 模拟器帧/固定图 | 只有 `--out` 落一张缓存帧（`:193`）；`/anchor` 的参照帧**只在进程内存**（`:473-481`） | **只读查看器**：HTTP 只有 GET（`:459-486`），无任何磁盘标注写回 |
| `tools/tilepos_audit.py` | 绿高亮帧（`_mask`）+ 关卡数据 | **`data/tilepos_audit.json`**（登记表，`:333-347`） | **标定体检 + 三态闸登记**：`trusted()`(`:238-255`) 被 `annotate_live.py:538-541`、`battle_loop.py:432-433`、`battle_sampler.py:58,233`、`pit_check.py:282-303` 消费 ⇒ 这是**唯一有下游约束力的地图几何证据表** |
| `tools/stage_boxes.py` | 模拟器截图（经 8765 `/shot`，`:197`）+ 识别 | 只 `print` JSON（`:213-227`） | **一次性识别探针**，无持久化 |
| `tools/ui_layout.py` | 无 | 无 | **静态布局真相表**：`LAYOUT`(`:27`)、`NOMINAL`(`:98`)、`fit_affine`(`:106`)、节点树(`:190-226`)、`ground_truth`(`:247`)；被 `annotate_live`/`annotate_view` 消费 |
| `tools/event_templates.py` | 一屏截图 + 人手给的框 | **`data/templates/events/<活动名>.png`**（`:28-43`） | **半个标注写入口**（"学一张卡面"），是这条线上唯一的模板落盘工具 |
| `tools/read_nodes.py` | 活动地图截图 | **`data/stage_nodes.json`**（`:1`） | **活动地图节点坐标提取**——最接近"产出地图数据"的一步，但只被 `batch_run.py:43` 消费 |
| `tools/crop_template.ps1` | 一张图 | 裁一块存模板（`param($Image,$Out,$X,$Y,$W,$H,$Scale)`） | 手工裁剪小工具（全仓仅被本文档提及） |

### 2.3 定性

> **"地图编辑"目前是"作业编辑 + 地图可视化 + 人工标注物"三件事的混合叫法。**
> 真正可编辑、可保存、可版本化的只有**作业（部署计划）**；地图（地块/路线/出怪）是**只读的官方数据缓存**；"标注"这一侧有持久化产物，但写入口基本是"截一张图 + 手给框 + 一次性脚本"，没有标注 UI。

---

## 3. 数据闭环（问题 3）：标注 → 存哪 → 谁消费

**链 A｜页面/活动模板（截图标注 → 识别）**
`crop_template.ps1` 或 `event_templates.py --learn`（`:28-43`） → `data/templates/pages/*.png`（17 个）、`data/templates/events/*.png`（27 个）+ `events/index.json`（人工维护） → 消费：`tools/pages.py:6`、`tools/maaorch_service.py:253`（`page_of()` 页面指纹）、`tools/enter_by_template.py`、`tools/event_sweep.mjs:293`（id→slug 反查）、`tools/main_nav.py:233`（`prime_badge.png` 报酬 ROI）。模板目录实测 61 文件 / 3.82 MB。

**链 B｜格网体检（观测 → 三态闸 → 下游硬约束）**
绿高亮帧 → `tilepos_audit.py --stage …` → `data/tilepos_audit.json`（2,843 B） → 消费：`annotate_live.stage_trusted()`、`battle_loop.py:432-433`、`battle_sampler.py:58,233`、`pit_check.py:295-303`（回归断言）。**这是唯一"标注结果反过来决定代码行为"的闭环。**

**链 C｜格网参照（标定 → 只自用）**
`annotate_live.py --save-grid-ref / --register-stage`（`:569-586`） → `data/grid_ref_<stage>.npy`（实测仅 2 个：act51side_01/05） → 消费：**只有 `annotate_live` 自己**做漂移检测（`:506-524`）。`battle_loop` 有 `map_fingerprint()`(`:286`) 但不读这个 npy ⇒ 一条断头的标定链。

**链 D｜活动地图节点（一次性提取 → 单一消费者）**
`read_nodes.py` → `data/stage_nodes.json` → `batch_run.py:43`（全仓唯一消费者）。

**链 E｜作业编辑闭环（唯一完整闭环）**
viz_plan 页面编辑 → 导出 JSON →（`--ingest` 或 ws_server POST）→ `data/copilot/<stage>_edited/` 或 `data/workspaces/…/{cleared.json, versions/}` → `versions.py` 版本链 → `rules_db.py` 聚合到 `knowledge/edits.json` → `feedback.py` 反哺生成下一版 → 回到 viz_plan 渲染。

**地图数据（`_level_` / Tile-Pos）不在任何闭环里 —— 它只有"读"，没有"写"回环。**

---

## 4. 要成为真正的"地图编辑器"，缺什么（问题 4）

### 4.1 七个缺口（按阻断强度排序）

| # | 缺口 | 证据 |
|---|---|---|
| G1 | **没有可写的自有地图层**：`_level_` 是外部镜像缓存，Tile-Pos 是 MAA 安装目录里的第三方资源，两者都没有本仓的"覆盖层"概念 | §1.2 |
| G2 | **地块属性无编辑面**：可走集合 `WALK` 与装置 `DEVICE` 硬编码在 `viz_plan.py:43-53`（注释自己写着"装置几何是逐关的、从 level 数据推不出来，所以按关登记"） | `viz_plan.py:44-47` |
| G3 | **路线/出怪无编辑面**：`routes`/`waves` 只被解析渲染，页面没有写入口 | `viz_plan.py:116-245` |
| G4 | **坐标口径分散且踩过雷**：`viz_plan.py:69-75`（M[y][x]）/ `:118-124`（H-1-row）/ `own_axis_job.py:10-13`（Tile-Pos 索引）/ `stage_cut.py:255`（翻转解释） | §1.3 |
| G5 | **没有写接口**：唯一写服务 `ws_server.py` 的写范围被**刻意**限制在 `data/workspaces/`（`:18-21` "严格限定…一个监听局域网的写接口，宁可拒得难看"） | `ws_server.py:11-21` |
| G6 | **没有校验/版本/回滚**：地图改动无 diff、无版本链、无"与人改前对照"；`tilepos_audit` 三态闸只覆盖"格网几何对不对"，不覆盖"地块语义改得对不对" | `tilepos_audit.py:333-347` |
| G7 | **下游影响面大**：`data/_level_<stage>.json` 被 **16 个 tools 文件**直接读（axis_viz / battle_loop / ce6_cut_check / ce6_spawn_order / cost_model / enemy_speed / enemy_timeline / job_dp_check / job_judge / rogue_routes / stage_arrivals / stage_cut / stage_plan / stage_value / viz_plan / workspace），另有 `session` 侧间接依赖；改一处语义要保证 16 处同时成立 | 全仓 `_level_` 扫描 |

### 4.2 最小可用路径（建议）

**原则：不改真源，加一层 overlay；先可编辑"看得见的格子"，再谈路线与波次。**

**S1（1–2 天，零下游风险）：地块属性编辑 + 页内预览**
- 新数据文件 `data/map_edits/<stage>.json`，建议 schema：
  ```json
  { "stage": "act16side_ex02", "base": "_level_act16side_ex02.json",
    "tiles": { "5,3": {"walk": true, "deploy": true, "high": false, "note": "装置格"} },
    "device": {"cells": [[5,3]], "must_stand": [[5,2],[5,4]], "label": "冰淇淋机"},
    "note": "为什么改、依据哪一帧" }
  ```
- 新模块 `tools/map_overlay.py`：`load(stage)` / `merge(stage, lv)` / `save(stage, patch, expect_sha)` —— **唯一写口**，写前校验：格子在界内、`base` 哈希未变、与 Tile-Pos 的 `buildableType/heightType` 对账（不一致只警告不拦）。
- `viz_plan` 里复用**已有交互模式**：地图 click handler（`viz_plan.py:1022-1029`）+ `wsPost`（`:1213-1214`）→ 新增"选中地块 → 切 walk/deploy/high"与"标装置格"，只改页面内部状态与 overlay 文件，**不改变任何下游**。
- 判定标准：页面能标出 `[5,3]` 装置格并在刷新后保留；`python -m compileall -q .` 0 error。

**S2（2–3 天）：让消费端吃 overlay（保持可退化）**
- 在 `viz_plan.level()`（`:60-66`）与另外**最多 2 个**消费端（建议 `enemy_timeline.py:85`、`stage_cut.py:148`）插入 "read base → apply overlay" 一步；**没有 overlay 文件时行为必须与今天逐字节一致**（这就是验收）。
- 顺手把 `WALK`/`DEVICE` 从 `viz_plan.py:43-53` 迁到 overlay（默认值仍写在这两个常量里，overlay 只做覆盖）。

**S3（高风险，先设计后动手）：路线 / 出怪 diff 编辑**
- 只允许**覆盖** `routes[].checkpoints`、`WAIT_FOR_SECONDS`、起终点，并**导出 diff 而非整份覆写**；因为 `job_judge`/`stage_logic`/`enemy_timeline` 的判漏完全建立在路线与到达时刻上（`viz_plan.py:202-245` 注释里记着 3 次因路线口径造成的 bug）。

**不建议做的两件事**
1. **直接编辑 `data/_level_<stage>.json`**：技术上今天可行（不会被覆盖，§1.2），但它没有来源标记、没有 diff、没有版本，且与"别的关是从镜像拉的"形成同构不同义 —— 一旦迁移/清理就会丢。若确实要走这条，至少落一个 `<stage>.json.orig` 备份 + 一份 `<stage>.edited.json` 说明。
2. **编辑 `Arknights-Tile-Pos`**：那是 MAA 的资源目录，MAA 升级即覆盖，且 `own_axis_job`/`stage_cut` 靠它做落点校验 —— 改了会让"作业落点合法性"这条判据失去意义。

**零新代码的备选（如果目标只是"标注"）**
把"地图编辑"降级定义成**标注层**：`data/map_notes/<stage>.md`（人写的依据）+ `data/map_marks/<stage>.json`（人标的点/框/格），只在 viz_plan 页面显示与冲突提示里用。它满足"能在地图上留东西给人看"，但**不改变任何行为**——这也是今天 `ui_layout` + `annotate_*` 已经处在的位置。

---

## 5. 硬核心 vs 可后补（问题 5）

**硬核心（这条线的骨架，不可删/不可停）**
1. `tools/viz_plan.py`（1464 行）—— 唯一的"地图渲染 + 作业编辑 + 写回"三合一入口；且它 import `enemy_speed/cost_model/duel_model` 提供到达时刻与判漏。
2. `tools/ws_server.py`（212 行）—— 唯一的写接口 + 路径安全校验（`data/workspaces/` 限定）；扩展地图写接口应长在它旁边，而不是另起一个无校验的服务。
3. `tools/workspace.py` + `tools/versions.py` —— 版本链/落盘/回滚语义；"编辑"要可回滚就得复用它们。
4. `tools/tilepos_audit.py`（352 行）—— 唯一有下游约束力的地图几何真相点（`annotate_live`/`battle_loop`/`battle_sampler`/`pit_check` 都委托它）。地图编辑器的"校验层"必须与它对齐口径。
5. `tools/ui_layout.py`（484 行）—— 1280×720 逻辑画布布局表 + 锚点拟合；所有标注框都建立在它上面。
6. `tools/annotate_live.py`（679 行）—— 验收 PNG + 格网参照 + 三态闸消费方。
7. 数据基座：`data/_level_*.json`（104 个）、MAA `Arknights-Tile-Pos`、`data/_enemy_db.json`、`data/templates/`。

**可后补 / 可替代**
- `tools/annotate_view.py`（502 行）：纯查看器，与 `annotate_live` 的 overlay 能力重叠，地图编辑器做完它基本被吸收。
- `tools/stage_boxes.py`（236 行）：一次性识别探针，无持久化。
- `tools/read_nodes.py` + `data/stage_nodes.json`：只被 `batch_run.py` 消费。
- `tools/event_templates.py`：只在 event_sweep 链用；但它的 `--learn` 是"标注写入口"的现成模板，编辑器可借鉴其交互（给框 → 存图）。
- `tools/crop_template.ps1`：手工小工具，可被编辑器内建裁剪替代。
- 与"地图编辑"无关但常被混为一谈：`enter_by_template.py`/`main_nav.py`/`mt_nav.py`/`fastnav.py`/`nav_map.py` 是**导航执行**（消费模板），不是标注/编辑。

---

## 6. 复现命令

```powershell
cd D:\1-2\MAAOrch
python tools/viz_plan.py --stage act16side_ex02      # 生成 data/visualization/act16side_ex02.html
python tools/ws_server.py                            # 127.0.0.1:8898，页面"保存为 cleared/新版本"
python tools/annotate_live.py --img data/_live_now.png --out data/annotated_tight.png --json data/_anno.json
python tools/annotate_view.py --port 8930 --stage act51side_01      # 只读查看器（仅 GET）
python tools/tilepos_audit.py --all                  # 体检 → data/tilepos_audit.json
python tools/event_templates.py --match runtime/_grid_now.png       # 列当前屏能认出的活动卡
Get-ChildItem data -Filter "_level_*.json" | Measure-Object         # 104 个
Get-ChildItem data\templates -Recurse -File | Measure-Object        # 61 个 / 3.82 MB
```

---

## 7. 附：本次未采信的"看起来像地图编辑"的东西

- `roguelike_annotation_deck.pptx`（根目录 2.9 MB）、`arknights-map-main_15-11.png`：全仓代码/配置/启动器**零引用**（`grep` 0 命中），是孤立产物，不参与任何链路。
- `data/visualization/*.html`（5 个 + `_check.js`）：viz_plan 的**生成物**，时间戳停在 2026-09-16～09-21，不是数据源。
- `tools/annotate_view.py --anchor`：参照帧只存进程内存（`:473-481`），**刷新即失**，不要误当成"标注已保存"。
