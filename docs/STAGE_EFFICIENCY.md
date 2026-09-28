# stage_efficiency.py —— 关卡效率与材料需求库（「刷哪里划算」数据底垫）

> 给计划生成器（`mas_plan_builder`，见 `docs/MAS_PLAN_BUILDER.md`）和一切「该刷哪个关」的判断
> 提供一份**本地可查、来源可标、能和我们自己实测对上账**的数据：某关多少理智、产什么、
> 每理智产多少、实测与公开对不对得上。
>
> 文件：`tools/stage_efficiency.py`（**只读 + 本地数据生成**；不发起车、不改配置、不抓森空岛）
> 产物：`runtime/reports/stage_efficiency.json`（本工具是**唯一生成者**）
> 　　　`runtime/state/stage_efficiency_measured.json`（实测原始行缓存，可离线重建）
> 自测：`$PY312 tools/stage_efficiency.py --selftest`（离线纯函数，7 组 / 28 断言）

---

## 1. 干什么 / 不干什么

**干**：把「公开掉率（企鹅物流）+ 关卡计价 + MAA 掉落声明 + 我们自己的实测掉率」拼成一张关卡表
和一张材料→推荐关反查表，并按 KPI（每理智产出）排序、标注置信度与不一致。

**不干**（红线）：

* 不发车、不写配置、不碰游戏（全部数据源只读；实测走主机 `E:\AUTO-MAS\history` 只读扫描）。
* 不抓森空岛、不直连企鹅：公开数据只消费本地快照 `data/penguin.db`（采集是 `services/penguin_data.py` 的职责）。
* 不判断活动开放状态（见 §7.2）；不建模龙门币/采购凭证等「固定奖励」（见 §7.4）。

---

## 2. 产物与 schema（`maaorch.stage_efficiency/1`）

`runtime/reports/stage_efficiency.json` 顶层：

| 键 | 内容 |
|---|---|
| `schema` / `built_at` | 版本号（`maaorch.stage_efficiency/1`）/ 构建时间 |
| `sources` | 各数据源 + **抓取时间/时效**（penguin 的 `fetched_at`、实测的 `scanned_at`+窗口；见 §3） |
| `params` | 全部阈值与判据（下表 §4 的口径都落在这里，改代码即改这里） |
| `formulas` | rate / per_ap / eap / 实测把数 四个公式的人读版 |
| `counts` | 关卡数 / 材料数 / 实测关数 / 不一致数 / 伪关剔除数 |
| `stages` | **关卡表**：键=书写 code（磨难 = `<CODE>-HARD`，如 `10-6-HARD`） |
| `materials` | **材料→推荐关反查**：键=物品名，值=按 `per_ap` 降序的候选行 |
| `mismatches` | 实测与公开判定不一致的 (关, 物品) 清单（当前 0 条） |
| `problems` | 构建期的数据源问题（当前空） |

`stages[<code>]` 关键字段：

| 字段 | 说明 |
|---|---|
| `stage_id` / `name` / `zone` / `stage_type` | 游戏 stageId（如 `main_01-07`）/ 关卡名 / 章节区 / MAIN·DAILY·SUB·OTHER |
| `ap` / `ap_source` | 理智消耗 + **它是怎么定出来的**（`apcost_v4.unit_cost` → `maa_stages.json.apCost` → `apcost_v4.stages[sid].ap`） |
| `resident` | 常驻可刷（`main_/tough_/sub_/wk_/pro_` 前缀 + `*_perm` 别传）；活动/限时关为 false 并带 flag |
| `main_drops` / `drops` | 主要掉落（MAA 声明 `NORMAL_DROP`；未声明则回退概率最高 3 个并带 flag）/ 全部掉落（每把 ≥ `min_item_rate`）。每行含 `item_id`/`name`/`item_class`/`rate`/`per_ap`/`eap`（`drops` 另有 `sample`、`declared`） |
| `sample` / `sample_grade` / `confidence` | 公开样本量（该关最大 times）/ 样本档 / **置信度** `high`/`medium`（实测不一致再降一级） |
| `measured` | 实测块（只有 4 关有）：把数、理智、口径来源、逐物品 公开 vs 实测 vs 相对差；`mismatch` 标注 |
| `flags` | 人读注意项（未声明主掉落 / 非常驻 / 实测不一致） |

`materials[<材料名>]` 行：`stage` / `per_ap` / `rate` / `eap` / `ap` / `sample` / `confidence` / `item_class` / `source`（恒为 `penguin_matrix`）/ `note`（主掉落？额外掉落？常驻？⚠ 实测与公开不一致？）。

---

## 3. 数据源（含抓取时间与时效）

| # | 源 | 位置 | 时效（写入本库的信息） |
|---|---|---|---|
| 1 | 公开掉率：企鹅物流 Penguin Stats matrix(CN) | `data/penguin.db`（SQLite 快照） | 采集器 `services/penguin_data.py` **每日 03:30** 拉 `matrix?show_closed_zones=false&server=CN`（penguin-stats.io → .cn 兜底）。本库记录 `snapshot_id`、`fetched_at`、`rows`。**关闭/未开放的区不在此快照**（别传 `_perm` 在） |
| 2 | MAA 资源：掉落声明 | `services/maa/source/resource/stages.json`（617 关，`dropInfos[].dropType`：NORMAL_DROP/EXTRA_DROP/FURNITURE）、`item_index.json`（463 物品，含 `classifyType`） | 仓库内快照（mtime 2026-08-12）。**这是旧快照**：不含此后新开活动关的 code（见 §7.3） |
| 3 | 关卡计价 v4 | `data/stage_apcost_v4.json` | `unit_cost["<CODE>|普通"]` 优先，其次 MAA `apCost`，再次 `stages[sid].ap`。每行记录用了哪一级（`ap_source`） |
| 4 | 游戏 stage_table | `data/_stage_table.json`（3549 条） | 关卡名/zoneId/apCost 的权威来源；mtime 2026-09-16 |
| 5 | 我们自己的实测 | 主机 `100.79.173.69` `E:\AUTO-MAS\history\<日期>\<账号>\*.json` 的 `drop_statistics`（+ 同名 `.log` 的「开始行动 -N理智」） | **SSH 只读**扫描最近 N 天（默认 30）；原始行落盘 `runtime/state/stage_efficiency_measured.json`，本库记录 `scanned_at` + `window`（起止日期）+ `attempts` |

> 复现性：给定 `data/penguin.db` + MAA 资源 + 实测缓存，`--build --no-measured` 全离线重建（零联网）。

---

## 4. 口径（定义）

### 4.1 公开侧

* **rate**（每把期望掉落）= `quantity / times`（企鹅物流矩阵原义）。
* **per_ap**（每理智产出）= `rate / ap` —— 全库排序主键，**越大越划算**。
* **eap**（期望 AP/个）= `ap / rate`。
* **主要掉落** = MAA 资源声明 `NORMAL_DROP` 的物品；**47 关** MAA 未声明（CE/AP/S 系列等），回退为该关 `per_ap` 前 3 并带 flag「MAA 资源未声明主掉落」。
* **常驻** = `stageId` 前缀 `main_/tough_/sub_/wk_/pro_` 或后缀 `_perm`（别传）。非常驻只带 flag，**本库不判断开放状态**。
* **收录阈值**：关卡 drops 每把 ≥ `min_item_rate`(0.005)；材料反查每把 ≥ 0.02；公开样本 < `min_sample`(500) 的行不收；`rate > 5` 的行不收（剔除异常/固定奖励类）。
* **置信度**：`high` = 样本 ≥ 5000（417 关），否则 `medium`（135 关）；实测判定不一致再降一级（`low` 只可能由降级产生）。
* **伪关剔除**：`stage_id` 以 `randomMaterial` 开头的是企鹅对「随机物资补给箱开箱结果」的记账，不是关卡（`ap=99` 是假的），全库剔除（当前剔 25 条）。

### 4.2 实测侧（交叉校验用）

* **把数（分母）双口径**：
  * 主线/活动关：`runs = 龙门币 / 12 / ap`（龙门币奖励 = 12×理智）；先过名称归一（`龍門幣`→`龙门币`）。
  * 资源本（`CE-/LS-/CA-/AP-/SK-/PR-`）：龙门币折算不成立，改用副本日志「开始行动 a~b 次, -N理智」按 `GetFightStage selected` 归属、区间去重累计。
  * **回退**：LMD 口径把数 < 日志把数一半（坏样本：日志跑了 149 把而龙门币只记 10）时弃用 LMD，改用日志。当前 320/2578 次尝试走了回退。
* **名称归一**（台/日/国际服）：`龍門幣/聲望/名声`、`初級源岩`→固源岩、`源岩鉱`→源岩、`純金`→赤金、`入門/上級/中級作戦記録`、繁中 `基礎/高級/中級作戰記錄` 等（表在 `tools/stage_efficiency.py::NAME_ALIASES`，**每一条都经「每把掉率 vs 公开」实证核对**才登记）。`家具`/`Unknown` 直接计为忽略非材料。
* **不认识的物品名**一律丢弃并计数（`qc.unknown_names` / `dropped_unknown_rows`），**不猜**（`known_names` = MAA item_index 全名）。
* 实测块里 `LMD/声望` 不进物品表（只当分母/忽略）。

### 4.3 不一致判定

同 (关, 物品)：`|实测 rate − 公开 rate| / 公开 rate ≥ 0.30` 且实测把数 ≥ 20 → 标 `"实测与公开数据不一致"`，
写进 `stages[code].measured.items[*].mismatch` + `mismatches[]` + flag，并把该关置信度降一级；
偏差大但样本 < 20 把 → 只写「样本不足，不判定」。

---

## 5. 用法

```
$PY312 tools/stage_efficiency.py --build                    # 重建（默认用实测缓存，缺缓存才 SSH 扫）
$PY312 tools/stage_efficiency.py --build --measured         # 强制重扫实测（SSH 只读，30 天窗）
$PY312 tools/stage_efficiency.py --build --no-measured      # 全离线（不碰实测）
$PY312 tools/stage_efficiency.py --query 固源岩组            # 材料 → 推荐关（含实测对照）
$PY312 tools/stage_efficiency.py --stage 1-7                # 单关详情
$PY312 tools/stage_efficiency.py --list-materials           # 库内全部材料
$PY312 tools/stage_efficiency.py --json [--stage 1-7]       # 机读输出（单行 JSON）
$PY312 tools/stage_efficiency.py --selftest                 # 离线自测
$PY312 tools/stage_efficiency.py --check                    # SSH 连通性（--measured 前置检查）
```

* `--days N`（实测窗口，默认 30）、`--scan-timeout`（SSH 扫描超时，默认 1500s）、`--out`（换输出路径，便于干跑）。
* 退出码：`0` 成功；`2` 数据源不可用（无 penguin 快照 / `--check` 失败 / 库为空）；`1` 未预期错误。
* 环境变量：`MAAORCH_SSH_KEY` / `MAAORCH_SSH_HOST`（同 `stage_capability`、`maa_error_daily` 一套）。
* 消费者（如 `mas_plan_builder`）**读 JSON 不要跑脚本**：`stages`/`materials`/`mismatches` 三块足够，
  `sources.*.fetched_at` 与 `built_at` 用于时效判断（建议 > 7 天先重建，与活动快照同思路）。

---

## 6. 当前快照与交叉校验结果（2026-09-28 构建）

* 关卡 **552**（MAIN 352 / DAILY 32 / SUB 31 / 别传 `_perm` 137）+ 材料 **90**；伪关剔除 25。
* 置信度：high 417 / medium 135。样本档：≥5000 → 417 关。
* 实测：主机 history 30 天窗（2026-08-30 ~ 09-28）**2885 次尝试**；其中可定把数并入库 4 关；
  822 次尝试不可测（无龙门币也无副本日志）；忽略非材料 1283 件；未知名 0 条。
* LMD 口径 vs 日志口径的交叉核对：2545 次可比，1998 次一致（78.5%）。
* **不一致：0 条**（1-7 的源岩异常在分母回退后回归正常，见下）。

### 6.1 头号样本 1-7（41336 把，LMD 口径，2553 次尝试）

| 物品 | 实测/把 | 公开/把 | 相对差 |
|---|---|---|---|
| 固源岩 | 1.1871 | 1.245 | −4.7% |
| 基础作战记录 | 1.1839 | 1.240 | −4.5% |
| 源岩 | 0.1138 | 0.1187 | −4.1% |
| 赤金 | 0.0855 | 0.0898 | −4.7% |
| 酯原料 | 0.0578 | 0.0588 | −1.7% |
| 代糖 | 0.0544 | 0.0588 | −7.5% |
| 双酮 | 0.0466 | 0.0468 | −0.4% |
| 异铁碎片 | 0.0441 | 0.0468 | −5.8% |
| 破损装置 | 0.0319 | 0.0348 | −8.4% |

结论：**全项在 ±9% 内，呈系统性小幅偏低（见 §7.5）**，无一项触发不一致判定。

### 6.2 其余实测关

| 关 | 把数 | 口径 | 实测 vs 公开 |
|---|---|---|---|
| LS-6 | 44 | 日志 | 高级作战记录 3.636/把 vs 4.0（−9.1%）；中级 1.818 vs 2.0（−9.1%） |
| PR-A-2 | 10 | LMD | 重装芯片组 0.6 vs 0.5（+20%）；医疗芯片组 0.4 vs 0.5（−20%）；样本 < 20 把，不判定 |
| CE-6 | 29 | 日志 | 只有龙门币产出的关：物品表为空（龙门币只当分母），仅证明「把数可测」 |

---

## 7. 局限（必读）

1. **公开数据是快照**：一切以 `sources.penguin.fetched_at` 为准；活动期数据每天都在动，
   关库/开库由 `services/penguin_data.py` 下一次采集体现。本库只是「读取+拼装」。
2. **不判断开放状态**：`resident=false` 只提示「可能是限时活动关」，不代表此刻关闭/开放；要发车仍走
   `stage_capability`（B4）与 `mas_probe_farm`（B5）。别传 `_perm` 记常驻（依据：企鹅快照 `show_closed_zones=false`
   仍含这些关、MAA 资源也收录）。
3. **MAA 资源快照旧**（2026-08-12）：不含之后新活动关的 code（实测里出现过的 `SR-5/SR-7/SR-8`、`AT-4/AT-6/AT-8`
   等在本库外，企鹅快照无这些区、无价、无法交叉校验）；等 MAA 资源更新+重跑新快照后它们会自然进库。
4. **龙门币/采购凭证类固定产出不在表内**：CE/AP 本的「产出」是确定性奖励（不在企鹅矩阵），
   所以 CE-2~6、AP-3~5 的「主要掉落」列的是活动期物资箱（CONSUME，27 关有此现象），看龙门币收益请另用 CE 本常识；
   `item_class=CONSUME` 的行都是随机物资箱/过期消耗品，不是精英化/专精材料。
5. **LMD 折算分母有系统性小偏差**：`runs=LMD/12/ap` 会把掉率整体压低 ~4~9%（实测 LMD 略少于理论值，或把数略多）；
   所以「与公开差几个点」属正常噪声，**判定不一看的是 ≥30%**。资源本不用此口径。
6. **实测覆盖率低**：30 天窗里车队主要刷 1-7 + 活动关，只有 4 关有实测块；活动关（SR/AT 等）测了也交叉校验不了（见 §7.3）。
   822 次尝试不可测（日志与龙门币都缺）。
7. **置信度 ≠ 最优性**：高置信度只说明样本量大；「多材料综合收益」「紧迫度」「仓库缺口」不在本库（那是 plan_builder 的事）。
8. **不含剿灭/肉鸽**：剿灭（Annihilation）不在 MAA `stages.json` 的 7 键体系里，本库无其条目；肉鸽无掉落概念。
9. **主掉落回退是「概率最高」不是「官方声明」**：47 关（CE/AP/S4/10-8 等）MAA 未声明 `NORMAL_DROP`，看 `flags` 分辨。
10. **别传/主线同样的关有双 code 变体**（如 `10-6` vs `10-6-HARD` 磨难）：磨难按 `<CODE>-HARD` 独立成行，别混用。

---

## 8. 维护

* **何时重建**：① `data/penguin.db` 有新快照（每日 03:30 后）且要刷新结论时；② 活动开启/关闭后；
   ③ MAA 资源（`services/maa/source/resource/*.json`）更新后；④ 想换实测窗口时（`--days`）。
* **命令**：常规 `--build`（吃缓存）；换窗口/要新数据 `--build --measured`；纯公开数据 `--build --no-measured`。
* **加名称别名**（台/日/国际服新物品）：改 `NAME_ALIASES` —— **必须先用缓存跑「每把掉率 vs 公开」核对**（例：
  日语 `初級源岩` 每把 1.2401 ≈ 固源岩公开 1.245 → 才能登记为 `固源岩`；乱猜会把甲物品记到乙头上）。
* **改阈值**（`MIN_SAMPLE`/`MISMATCH_REL`/`MIN_ITEM_RATE`…）：改常量即生效，并会写进产物 `params`；
  改完先跑 `--selftest`（7 组 28 断言含阈值边界/分母回退/伪关/_perm 用例）。
* **别绕过去**：新工具要读「刷哪里划算」的数据请直接读本 JSON；不要再各写一份企鹅矩阵解析
  （与 §E 查重原则一致，能力↔工具表已登记 `stage_efficiency`）。
