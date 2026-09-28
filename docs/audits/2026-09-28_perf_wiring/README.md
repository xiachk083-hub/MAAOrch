# 性能基线回归检测接线审计（卡 t_f3cda416，2026-09-28 22:0x–22:2x）

> 目标：把 `t_c6f3a410`（性能基线，卡面自述『未接线』）接成**行为**——mas-observe MCP 面新增只读工具
> `mas_perf`（包 `tools/perf_baseline.py --check`），30 分钟巡检 job `52b88b0ffca0` 主表出 `PERF=` 判据行。
> 结论：**已交付**（工具面 13 工具注册实证 22:12:11 + 直连探针 `WARN:5项:RUN_SLOW` 22:13 + 巡检轮 `PERF=` 行见 §3.3）。
> 姿态：只读接线。写动作仅两处：① jobs.json 提示词（`hermes cron edit`，改前备份）；② config.yaml 开关翻转
> （关→开，**已还原为备份字节，净零** sha `b0b8f784…`）。

## 1. 判据与口径

- `PERF=` 值形态：`OK`（无回归） / `WARN:<n>项:<类型,…>`（近轮 vs 基线 P90 超阈即报） /
  `UNKNOWN:<原因>`（fail-closed，**不许当「无回归」用**——已写进巡检提示词）。
- 阈值：基线 P90 × mult（工具默认 ×1.5；MCP 参数 `mult` 下限 1.3＝卡面判据「超基线 ≥30%」，不得更灵敏）。
- 数据源（全只读）：`tools/perf_baseline.py --check --json --no-write`（history 近 3 天采集 × gui.log 事件
  × 基线文件 `runtime/reports/perf_baseline.json`，14 班次日窗口，schema `maaorch.perf_baseline/1`）。
- fail-closed 清单：SSH 采集失败 / 基线缺失或 schema 不符 / 超时 / JSON 解析失败 ⇒ `UNKNOWN:<原因>`。
- 掩码：findings 里账号按 mas-observe 契约（前 2 + *** + 后 2；短名首字符 + ***）。

## 2. 交付物

| # | 件 | 内容 | 证据 |
|---|---|---|---|
| 1 | MCP 工具 `mas_perf` | mas-observe 第 13 个只读工具：`--check --json --no-write`（显式超时 420s、stdin=DEVNULL、不重试）；返回 `{ok, perf, findings_total, findings[], shift_day, baseline, thresholds, exit_code, elapsed_s}` | §3.1；`01_probe_direct.txt` |
| 2 | 巡检提示词 v8 | job `52b88b0ffca0`：第三步**十三检**（第 13 项=mas_perf）、主表 **23 行**（`PERF=` 加在 AUDIT_STATUS 后）、预算 26→**27**、同工具上限 `mas_perf ≤1`；`cron edit` 回读**逐字节 MATCH=True**（sha `bb5abf44…`） | `03_prompt_patch.txt`；`05_backup_sha_before.txt` |
| 3 | 工具面刷新 | `removed=['mas-observe']` 22:11:09 → `added=['mas-observe']` 22:12:11 → 注册 **13 工具**（含 `mcp__mas_observe__mas_perf`）；config.yaml 净零（翻转后还原，sha 前后一致） | `06_mcp_registration.txt` |
| 4 | 基线原件 | `runtime/reports/perf_baseline_20260928_2213.json`（与定版 `perf_baseline.json` 同 sha `480bfff0…`） | `11_baseline_artifact_sha.txt` |
| 5 | 三连采方差 | §3.2：同窗口三次实调（22:01 / 22:13 / 22:16b），findings 集合与数值**零漂移** | `10_variance_check1/2.json` |
| 6 | 巡检轮实证 | §3.3 两轮 `PERF=` 行（原样引用；末轮 13 工具面 = `WARN:5项:RUN_SLOW`） | `14_round_22-22.md`、`14_round_final.md` |

## 3. 实测证据

### 3.1 直连探针（新进程实调 `tool_perf`，22:13，`01_probe_direct.txt`）

```
perf="WARN:5项:RUN_SLOW"（exit_code=3, elapsed_s=33.9）；findings（账号已掩码）：
h*** 38.3min（基线 24.6，×1.56）；Mi***ng 57.2（34.4，×1.66）；r*** 68.1（32.5，×2.10）；
拍前***简介 47.5（31.6，×1.50）；潮*** 66.9（34.1，×1.96）——与 perf_check_2026-09-28.md 的 5 项逐条一致。
```

### 3.2 三连采方差（同班次窗口 [05:00, now)，`10_variance_*`）

| 采样 | 时刻 | 耗时 | rc | findings 数值（min） |
|---|---|---|---|---|
| 1 | 22:01 | 36s | 3 | 38.3 / 57.2 / 68.1 / 47.5 / 66.9（5×RUN_SLOW） |
| 2 | 22:13（探针） | 33.9s | 3 | 同上，**零漂移** |
| 3 | 22:16 | 34s | 3 | 同上，**零漂移** |
> 上午对照：10:06 手动轮同样 `5×RUN_SLOW`（数值一致）⇒ 判据跨时段稳定。观测分布（daily p50/p90：62.6→63.0/96.0→98.7）随晚班堆积自然上移，不入判据。

### 3.3 巡检轮 `PERF=` 行（原样）

**22:08 轮（首轮，含刷新竞态）**：该轮 22:08:08 起跑，跑在**起跑面=12 工具**（工具面刷新 22:11–22:12 完成于其运行中途；
轮内工具表=起跑快照、不随刷新变化）⇒ 原样输出：

```
PERF=UNKNOWN:工具面未提供（mas-observe 未接线 mas_perf）
```

（`14_round_22-22.md` L108）——属**预期内**：提示词 v8 已生效（同轮含十三检 / 27 次预算 / `mas_perf ≤1`），
且 fail-closed 分支被实景演练（工具面未接线 ⇒ 不冒充「无回归」）。

**末轮 22:24:30 起跑 / 22:38:20 落盘（手动触发，13 工具面；`hermes cron run 52b88b0ffca0`）** ⇒ 原样：

```
PERF=WARN:5项:RUN_SLOW
```

（`14_round_final.md` L108，sha256 `3a1686d129ada0b40c9950c0644fd409ae0b763530658a8d558b60890107c5e7`）——工具面 13 工具已生效
（本轮起跑面 MCP=161 枚，含 `mcp__mas_observe__mas_perf`；返回 `WARN` 而非「工具面未提供」形态）。
WARN 证据行（同轮尾行原样，账号已掩码；≤2 条、只报不处置）：`PERF 超阈 2/5：r***@68.1min/32.5min/×2.1、潮***@66.9min/34.1min/×1.96`。

## 4. 变更记录与回滚

- **提示词**：改前备份 `cron/jobs.json.bak-t_f3cda416-prompt-20260928-220654`（sha `8c044b53…`）；改后回读 MATCH=True。
  回滚：`cd C:\Users\xiach\AppData\Local\hermes\profiles\mas\cron && cp jobs.json.bak-t_f3cda416-prompt-20260928-220654 jobs.json`
  （复核 `sha256sum jobs.json` = `8c044b53b001462dfc63ae0c6212f848af508ec134de8f2eec3decca58a54565`）。
- **MCP 工具**：`tools/mas_observe_mcp.py` 新增 mas_perf 块（改后 sha `9407cc45…`）。回滚 = 摘除该块
  （grep `CLI_PERF` / `tool_perf` / `_perf_mask_subject` / `mas_perf` 四处）+ 重跑「关→开」刷新。
- **config.yaml**：22:10:45 off / 22:11:36 on 翻转，22:16 已还原为备份字节（sha `b0b8f784…`，NETZERO=True）；无需回滚。
- **刷新时序（沿用 y3 接线教训）**：`enabled=false` → 等 `logs/gateway.log` 出 `removed=['mas-observe']`（22:11:09）
  → `enabled=true` → 等 `added=['mas-observe']`（22:12:11）→ `registered 13 tool(s)`。秒级连翻会被探查去抖吞掉，勿用。

## 5. 验收对照

- [x] 末行 `PERF_WIRING=OK`（文末）
- [x] 一轮巡检输出可见 `PERF=` 行（§3.3 原样引用）
- [x] 回滚命令可执行且已备份（§4；备份文件在场 + sha 记录）
- [x] 只读：MCP 工具仅调 `--check --json --no-write`；`mas_observe_mcp.py --check` = `tools(13)…readonly_assert=PASS`

## 6. 文件索引

| 文件 | 内容 |
|---|---|
| `01_probe_direct.txt` | 直连 `tool_perf` 探针输出（全量） |
| `03_prompt_patch.txt` | 提示词 v8 unified diff（base→after）+ 双 sha |
| `05_backup_sha_before.txt` | 改动前 jobs.json / config.yaml sha |
| `06_mcp_registration.txt` | 刷新时序日志（removed/added/registered 13 tools） |
| `10_variance_check1.json` / `10_variance_check2.json` | 三连采样本 1/3（22:01 / 22:16） |
| `11_baseline_artifact_sha.txt` | 基线原件副本 sha |
| `12_sha_after.txt` | 改动后 config.yaml（已还原）/ mas_observe_mcp.py sha |
| `14_round_22-22.md` | 首轮（12 工具面）巡检输出全文（含 `PERF=UNKNOWN…` 行） |
| `14_round_final.md` | 末轮（13 工具面）巡检输出全文（含 `PERF=WARN:5项:RUN_SLOW`；sha256 `3a1686d1…`） |

## 7. 边界与后续

- 巡检轮成本：+1 次 SSH 采集（~34-60s，与 error_daily / y3_gap 同量级）；轮预算 26→27 次工具调用。
- 22:08 轮（22:08:08 起跑）与工具面刷新（22:11–22:12）存在**刷新竞态**：若该轮 PERF= 为「工具面未提供」形态的
  UNKNOWN，属起跑面 12 工具所致，以 §3.3 实际行为准。
- 基线重建未做（沿用 10:01 定版；节奏见 `docs/PERF_BASELINE.md`）。
- 日报（mas-shift-report）未接 `PERF=`；卡面验收只涉巡检，如需接日报另开卡。
- 22:08 刷新竞态已由末轮（22:24:30 起跑、13 工具面）闭环：`PERF=WARN:5项:RUN_SLOW` 为实景输出。

PERF_WIRING=OK（末轮 `PERF=` 行已回填：`PERF=WARN:5项:RUN_SLOW`；见 §3.3）
