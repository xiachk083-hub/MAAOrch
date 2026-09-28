# 性能基线（PERF_BASELINE）—— 慢不慢，先看基线

> 2026-09-28 起可用。工具：`tools/perf_baseline.py`（只读统计；不改任何配置）。
> 场景：感觉「今天慢」时，用 `--check` 拿**最近一轮 vs 基线**的量化结论，而不是拍脑袋。

## 1. 它测什么（四条腿）

| 指标 | 口径 | 数据源 |
|---|---|---|
| **A. 单号日常总耗时** | 账号在**一个班次日**内的总耗时（多次尝试求和）；同时保留单轮耗时分布（分成功/失败） | `E:\AUTO-MAS\history\<班次日>\<账号>\*.log` 末时间戳 − 首时间戳（含唤醒+全部任务链；单轮 >6h 丢弃，口径同 `account_health`） |
| **B. MAA 启动 → 首个任务链** | `MaaAssistantArknights GUI started` → 其后第一条 `Start Task Chain`（秒） | `E:\MAA-Multi\MAA_*\debug\gui.log`（+ `gui.bak.log`） |
| **C. 截图用时** | `截图用时 <N>ms。`（INF，单张样本分布）+ `截图用时较长/过长(平均: Nms)`（WRN/ERR 计数） | 同上 |
| **D. 任务链级耗时** | `Start/Completed Task Chain: <链>, Task ID: <N>` 配对（按目录+链+id **栈式**配对：一条 Completed 配它之前最近的未配对 Start） | 同上 |

分组维度：**按服**（Official / Bilibili / YoStarJP / txwy）与**按槽位**（`MAA_1`…`MAA_12`，即 MAA 目录）。
服/槽位来自 REST 名册与 `mas_account_master` 名册；名册外账号归「未知」。

窗口口径与巡检一致：**班次日 [D 05:00, D+1 05:00)**。

## 2. 产出文件

| 文件 | 内容 |
|---|---|
| `runtime/reports/perf_baseline.json` | 基线（schema `maaorch.perf_baseline/1`）：每项指标的 n / P50 / P90（截图另给逐槽位），加 `thresholds` 与 `coverage` |
| `runtime/reports/perf_check_<班次日>.json/.md` | `--check` 的报告：超阈项表 + 窗口观测摘要 + 口径边界 |
| `docs/audits/2026-09-28_perf_baseline.*` | 首次建立时的证据（实跑输出） |

现状（基线窗口 2026-09-15 ~ 09-28，14 班次日；history 1718 单轮 / 680 账号日 / gui 事件 16877）：

| 指标 | P50 | P90 | n |
|---|---|---|---|
| 账号日耗时 | 48.6 min | 69.0 min | 680 |
| 单轮耗时（全部） | 22.2 min | 32.5 min | 1718 |
| 单轮耗时（成功轮） | 25.2 min | 34.2 min | 1171 |
| 启动→首链 | 25 s | 29 s | 1443 |
| 截图 | 311 ms | 380 ms | 641 |
| 链 StartUp | 158 s | 221 s | 1232 |
| 链 Fight | 293 s | 611 s | 1400 |
| 链 Infrast | 689 s | 912 s | 893 |
| 链 Mall | 123 s | 266 s | 868 |
| 链 Recruit | 71 s | 98 s | 859 |
| 链 Award | 35 s | 45 s | 862 |

## 3. 用法

```bash
PY=C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe   # 带 cv2 的那个解释器（本工具无第三方依赖，任何 3.10+ 均可）

# 建立/刷新基线（默认最近 14 班次日；约 40 秒，两次 SSH）
$PY tools/perf_baseline.py
$PY tools/perf_baseline.py --days 30 --summary     # 只打一行 PERF_SUMMARY=
$PY tools/perf_baseline.py --json --no-write       # 看结构化结果

# 回归检测：最近一轮（今天班次日）vs 基线；超阈打印超阈表
$PY tools/perf_baseline.py --check
$PY tools/perf_baseline.py --check --date 2026-09-27      # 指定班次日
$PY tools/perf_baseline.py --check --mult 1.3             # 收紧倍数
$PY tools/perf_baseline.py --check --baseline <路径>      # 用别的基线文件

# 离线自检（不连网；覆盖解析/配对/会话/分位/阈值）
$PY tools/perf_baseline.py --selftest
```

**退出码**：`0`=成功/无超阈；`3`=有超阈项（便于接告警）；`2`=数据源不可达或基线缺失；`1`=未预期错误。

## 4. 阈值规则（`--check` 怎么判）

默认「超出基线 P90 × **1.5**」。**该号自己的基线优先**（n≥3 时用账号自身的 P90，否则回退全量 P90）：

| 类型 | 对象 | 判据 |
|---|---|---|
| `ACCOUNT_DAY_SLOW` | 单号当日总耗时 | 当日总耗时 > 基线 P90 × 1.5 |
| `RUN_SLOW` | 单轮耗时 | 单轮 > 该号 P90 × 1.5（无自身基线时用全量，32.5 min） |
| `STARTUP_SLOW` | 启动→首链 | 单次启动 > P90 × 1.5（基线上 ~29s，即 >43.5s 报警） |
| `SCREENSHOT_SLOW` | 截图 | 窗口**中位数** > 基线 P90 × 1.5（单张噪点大，用 p50；窗口样本 <3 不判） |
| `CHAIN_SLOW` | 链级耗时 | 窗口该链 p50 > 该链 P90 × 1.5（窗口样本 <2 不判） |

阈值倍数可用 `--mult` 统一覆盖；修 baseline 的 `thresholds` 段则永久生效。基准选择会写进超阈表的「基准」列（自身/全量）。

## 5. 边界与已知坑（读结论前先看这几条）

1. **gui.log 4MiB 轮转**：`gui.log` 只存当前一段、`gui.bak.log` 存上一段 ⇒ 窗口左端可能被轮掉。`coverage.gui.min_ts/max_ts` 如实标注实际采到的范围；分位数只对采到的样本负责。
2. **截图 INF 行是稀疏的**（全队 ~46 条/天，槽位级更少）⇒ 单看某槽位一天的截图 p50 不可靠；`--check` 因此要求窗口 ≥3 张、基线 ≥20 张才判。
3. **启动→首链 >30 分钟不入样**（只计数 `no_chain`/`over_cap`）：人工开 GUI 不打任务的会话会污染这一列。`no_chain` 数量本身也是信号（>0 说明有人工/半途启动）。
4. **基线自参照**：默认窗口包含最近一天，今天的慢样本会轻微抬高基线（4/1718 量级可忽略）。要求严格就用 `--days 30` 并把 `--date` 往前挪一天重建。
5. **单轮耗时含唤醒**（history `.log` 首行→末行），它比「纯战斗时长」长；失败轮通常很短（快速失败），所以 `run.fail` 的 P50 明显低于 `run.ok`。
6. **未闭合链**（只有 Start 没有 Completed，通常是被 kill/中断）单列在 `chain_seconds.unclosed`，不进耗时分布。
7. **凭据红线**：本工具只读 `E:\AUTO-MAS\history` 与 `E:\MAA-Multi\MAA_*\debug\gui*.log`、REST 名册的 Name/Status/Server/Path；不读、不落 `Password`/`SklandToken`/手机号掩码。远端唯一写动作 = 上传探针脚本 `E:\_hermes_perf_probe.ps1`（与 `maa_error_daily`/`account_health` 同惯例）。

## 6. 维护

- **重建节奏**：建议每周/每两周重跑一次 `python tools/perf_baseline.py`（或大改配置、换模拟器、换版本后重建）；`built_at` 太旧时结论只当参考。
- **想让「感觉慢」自动报警**：可在巡检侧加一条只读命令 `python tools/perf_baseline.py --check --summary`（退出码 3 = 超阈）。**本工具自身不做任何调度**，接线与否属巡检侧决定。
- **改判据前先看证据**：`--raw-out <路径>` 落原始采集（recs + gui 事件行），任何数字都可逐条复算；`--selftest` 保证解析与阈值逻辑不静默回归。
