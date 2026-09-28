# 性能基线 + 回归检测（kanban t_c6f3a410，2026-09-28 10:0x）

> 问题：今天多次感觉「慢」，但**没有基线**，无法判断是否异常。
> 姿态：**只读统计**。未改任何配置（AUTO-MAS / MAA / 队列 / cron 全未动）；
> 远端唯一写动作 = 上传探针脚本 `E:\_hermes_perf_probe.ps1`（与 `maa_error_daily` / `account_health` 同惯例）。
> 一句话结论：基线已建立（14 班次日 / 1718 单轮 / 680 账号日 / gui 事件 16877），
> `--check` 可用且**今天确实抓到 5 个超阈样本**（下 §3.3，逐条对过原始 `.log` 首尾行）。

---

## 1. 交付物

| # | 文件 | 说明 |
|---|---|---|
| 1 | `tools/perf_baseline.py` | 采集 + 基线 + `--check` + `--selftest`（44 KB，只读） |
| 2 | `runtime/reports/perf_baseline.json` | 基线（schema `maaorch.perf_baseline/1`，P50/P90/样本数 + thresholds + coverage） |
| 3 | `docs/PERF_BASELINE.md` | 怎么用（口径表 / 阈值表 / 边界坑 / 维护节奏） |
| 4 | `runtime/reports/perf_check_2026-09-28.{json,md}` | 今天的 `--check` 报告（超阈 5 项） |
| 5 | `docs/audits/2026-09-28_perf_baseline/` | 实跑证据：`selftest.txt` / `run1_baseline.txt` / `run1_check.txt` / `sha256.txt` |

sha256：
```
645c466c780c380ed24364a5aecc94cb950c425eab13b9ee4d13f44cda41588c *tools/perf_baseline.py
480bfff09ce5caf2d0f2576ae3578f48fffddca0c88e2b7b414ae5a92a8e75ac *runtime/reports/perf_baseline.json
```

## 2. 口径（四条腿，与 docs/PERF_BASELINE.md 一致）

| 指标 | 数据源 | 口径 |
|---|---|---|
| A 单号日耗时（按服/槽位） | `E:\AUTO-MAS\history\<班次日>\<账号>\*.log` | 末时间戳−首时间戳（含唤醒+全部链；单轮 >6h 丢弃）；(账号,班次日) 求和 = 当日总耗时 |
| B 启动→首链 | `E:\MAA-Multi\MAA_*\debug\gui.log(+.bak)` | `GUI started` → 其后首条 `Start Task Chain`；>30min 视为非自动化启动（只计数） |
| C 截图用时 | 同上 | `截图用时 <N>ms。`（单张）+ `较长/过长(平均:)`（计数） |
| D 链级耗时 | 同上 | `Start/Completed Task Chain` 按（目录+链+id）栈式配对；未闭合单列 |

窗口 = 班次日 [D 05:00, D+1 05:00)；服/槽位取自 REST 名册 + `mas_account_master`，名册外归「未知」。
采集两条腿：history 复用 `account_health.collect_remote`（血泪定稿的 SSH/PS 底座），
gui 腿是本次新写的只读探针（`[regex]::Matches` 单次扫过 4MiB 日志、gzip+b64 回传；锁定文件用 `FileShare::ReadWrite` 读）。

## 3. 实跑证据

### 3.1 基线（2026-09-15 ~ 09-28，14 班次日；`run1_baseline.txt`）

- 采集：history 单轮 **1718**（坏记录 0）；gui 事件 **16881**（28 文件，截断 0，采集 27.9s）；覆盖 09-15 05:06 → 09-28 09:48。
- 账号日耗时：P50 **48.5** / P90 **69.0** min（n=681）
- 单轮：P50 22.1 / P90 **32.5** min（n=1718；成功轮 P50 25.2 / P90 34.2，n=1171）
- 启动→首链：P50 **25.0** / P90 **29.0** s（n=1443；无链 53）
- 截图：P50 **311** / P90 **380** ms（n=641；较长 WRN 748 条 p50 492ms）
- 链：StartUp 158/221s（n=1232）｜Fight 293/611s（n=1400）｜Infrast 689/912s（n=893）｜Mall 123/266s（n=868）｜Recruit 71/98s（n=859）｜Award 35/45s（n=862）
- 按服（日耗时 P50/P90 min）：Official 47.9/65.2（n=457）｜Bilibili 51.0/69.4（116）｜YoStarJP 54.8/102.7（78）｜txwy 38.8/50.4（21）｜未知 44.7/61.1（8）

### 3.2 `--selftest`（离线，41 项断言全绿，`selftest.txt`）

覆盖：分位数插值 / PS 行解析 / 会话切分（正常·无链·超 cap）/ 链**栈式**配对（含跨会话残留 Start 不被错认）/
gui 聚合 / history 按服槽位分组与 only_day / 阈值触发与不触发 / 自身基线优先 / 样本不足不判 / 基线装配 schema。
> 其中「跨会话未闭合 Start」这条是本轮写的**真坑**：先写成 FIFO 配对，自检直接抓到「上轮被 kill 的 Start 会让下轮 CD 配出 3600s 假耗时」⇒ 改栈式。

### 3.3 今天的 `--check`（`run1_check.txt`；退出码 3）

窗口 [2026-09-28 05:00, 10:01)；**超阈 5 项，全部 RUN_SLOW**：

| 对象 | 实测 | 基线 P90（基准） | 倍率 | 独立复核（原始 `.log` 首→尾） |
|---|---|---|---|---|
| him | 38.3 min | 24.6（自身） | ×1.56 | 09:13:25 → 09:51:42 = **38.3 min** ✅ |
| MikuKong | 57.2 min | 34.4（自身） | ×1.66 | 07:21:27 → 08:18:37 = **57.2 min** ✅ |
| rzzz | 68.1 min | 32.5（自身） | ×2.10 | 05:49:00 → 06:57:08 = **68.1 min** ✅ |
| 拍前看简介 | 47.5 min | 31.6（自身） | ×1.50 | 06:18:53 → 07:06:23 = **47.5 min** ✅ |
| 潮响 | 66.9 min | 34.1（自身） | ×1.96 | 06:39:37 → 07:46:29 = **66.9 min** ✅ |

- 复核方式：不经工具，直接 SSH 读 `E:\AUTO-MAS\history\2026-09-28\<账号>\*.log` 的 HEAD/TAIL 行（脚本见会话 scratch；5/5 与工具数字**秒级一致**）。
- 未超阈的观测一并保留（日报口径）：当日账号总耗时 n=52 p50=37.9 / p90=61.9 min（基线 48.5/69.0）；
  启动→首链 n=134 p50=27.0 / p90=30.7 s；截图 n=36 p50=295ms；链 p50：Fight 118s、Infrast 733s、StartUp 193s。
  ⇒ 今天**没有**「全队变慢」（各 p50 都在基线内），慢的是**个别号的长尾单轮**：均为同一形态 —— 前几次尝试短失败（`任务出错: 开始唤醒`）后重试成功，最后一次长跑（`him`/`烤箱`/`rzzz`/`拍前看简介` 都是 3 次尝试）。
- 昨天（09-27）满日窗口复跑（`--date 2026-09-27 --no-write`）同样给出 6 项 RUN_SLOW、退出码 3，说明判据不是只对今天敏感。

## 4. 已知边界（写进 docs 了，此处只列要点）

1. gui.log 4MiB 轮转 ⇒ 窗口左端可能被轮掉；`coverage` 如实标注实际覆盖（本基线 09-15 起步，够 14 天）。
2. 截图 INF 行稀疏（全队 ~46 条/天）⇒ 判据要求窗口 ≥3 / 基线 ≥20 张。
3. 基线自参照：窗口含今天（4/1718 量级，可忽略；严格场景 `--days 30` + 前移 `--date` 重建）。
4. 阈值默认 ×1.5、可用 `--mult` 覆盖；`较真` 时可先收紧到 1.3 看噪声。

## 5. 我**没**碰的东西

- 未改任何配置：`config/*`、AUTO-MAS 侧配置、队列、定时任务（cron/AUTO-MAS 全未动）。
- 未改其它工具；`docs/MAS_MASTER_HANDBOOK.md` 只加了 2 行索引（附录 A 文档地图 + 工具行）。
- 未改 `runtime/reports/*` 里别人的报告；新增的只有 `perf_baseline.json` / `perf_check_2026-09-28.*`。
- 未在远端跑任何 MAA/模拟器动作；SSH 全为读操作 + 探针脚本文件落盘。

## 6. 未做 / 待定（都不在卡面范围内）

- **未接线巡检**：`--check` 目前手动跑。若要每班自动报，需在巡检侧加一行只读命令（退出码 3 = 超阈）——属巡检提示词改动，留待 lead 决定。
- **未纳入 `tools/tests/test_regression.py` 套件**：该套件是另一张卡的产物，动它会牵动其覆盖计数文档；`--selftest` 已能独立守住解析/阈值逻辑，接线是 1 行的事，按需再加。
- **`no_chain` 53 次启动**（GUI 起来但没跑任务）未定因：疑似人工开 GUI；如需追查，按目录+时刻可从 gui 事件直接列。
