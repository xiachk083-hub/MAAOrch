# MAAOrch 定位契约

> 核查时间 **2026-09-26 15:11**（本机）。本文所有行号/数字来自当次读代码，不引用旧文档结论。
> 行数口径 = 非空行（`Get-Content | Measure-Object -Line`）；文中代码位置引用的是**物理行号**，会随改动漂移，定位请按符号名检索。本仓文件正被并发改动；
> 与本文冲突时**以代码为准并回写本节**。配套：`docs/SINGLE_SOURCE_CONVERGENCE.md`（单点收敛总纲）、
> `docs/MAS_RUNBOOK.md`、`docs/http-api.md`、`docs/STRUCTURE.md`。

## 1. 定位与三方角色

| 角色 | 归属 | 一行职责 | 权威边界 |
|---|---|---|---|
| Harness | 外部（Hermes / DSH agent） | agent 大脑：定目标、排优先级、决定"跑谁 / 跑什么" | 计划与目标（本仓不存权威副本） |
| MAA | 本机 `services/maa/**`（v6.11.1 + instances/1..3） | 单设备游戏自动化引擎：一份 asst 配置 → 一次任务链 | 单账号单次执行结果 |
| MAS = AUTO-MAS | 远端 `100.79.173.69`、`E:\AUTO-MAS`、API `:36163` | 多账号调度器：队列 / 班次 / 多号串跑（已存在，本仓不重写） | 远端队列、账号、班次真相 |
| **MAAOrch** | 本仓（本机） | **粘合剂**：把上面三者接成可被 agent 驱动的系统 | 设备与 MAA 进程状态 |

MAAOrch 明确不做：

- **不做大脑**：不持有计划权威、不做编排决策；编排 = Harness 下发的意图 + 账号自身调度配置。
- **不做 UI 门户**：用户入口由 Node 看板 `server/maaorch-web.mjs` 承接，**端口可配、以启动参数为准**（本机脚本 `tools/maaorch_platform.bat:16`、`tools/maaorch_web_launch.ps1:58` 传 `--port 19999`；mjs:65 内置默认是 19998）（见 `docs/PLATFORM_NODE_MIGRATION.md`）；本仓只出数据与动作。
- **不做研发工作台**：`runtime/_pilot/**`、`tools/*probe*.py`、`debug/**` 是研发/取数脚本，不是平台契约面，不纳入本契约。

## 2. 粘合剂只拥有的四件职责

### 2.1 状态收敛（一个域一个口径）

- MAA：`services/launch_queue.py`（队列唯一写者）、`services/emu_service_v2.py`（模拟器唯一出口）、`services/event_bus.py`（事件唯一出口）。
- MAS：`services/mas_adapter.py`（**168 非空行** / 物理 207 行，**唯一门面**；MCP 只认它的函数，不直接 import mas_l3）→ `services/mas_l3.py`（257 行，快照缓存 45s）→ `tools/mas_watch.py`（242 行，SSH 采集）。
- 对外出口：MCP 的 `status/dashboard/queue/account_status` 与 `mas_status/mas_snapshot`。

### 2.2 命令翻译（意图 → 两个引擎各自的方言）

- MAA：MCP 工具 → `network/mcp_server.py:25 _call_api`（本机 HTTP，带 `api_token`）→ `network/api_fastapi.py` 端点 → services 层。
- MAS：MCP 工具 → 进程内 `services.mas_adapter` → `services.mas_l3` → `tools/auto_mas_*.py` CLI 或远端 `:36163` API。
- Harness 只说动词；SSH / HTTP / CLI / DPAPI / 锁感知一律不越过这一层。

### 2.3 事件归一（一个入口看"发生了什么"）

- 本机：`services/event_bus.py emit()` → `logs/event_bus.jsonl` + 内存环 + SSE；域前缀 `queue/emu/maa/fight/ops`（event_bus.py:9-14）。
- 远端：`runtime/mas_ledger.jsonl`（日报/补跑/清理流水）+ MCP `mas_history`。
- 两端目前**未合流**，见 G3。

### 2.4 安全闸门（唯一的否决权 —— 必须是代码结构，不是调用方自觉）

| # | 结构保证点 | 位置 | 机制 |
|---|---|---|---|
| S1 | MCP 挂载点单点鉴权 | mcp_server.py:55-82 | `_TokenAuth` 包装整个 `/mcp` ASGI app；`x-agent-token` 或 `Authorization: Bearer`，`hmac.compare_digest`，不符直接 401，**在工具可见之前** |
| S2 | 工具白名单 | mcp_server.py:89-260 | 只暴露"调度台也有的操作"；杀进程 / 改配置 / 部署 / 删文件**没有对应工具**，agent 无法通过 MCP 触达 |
| S3 | 写操作复用同一校验层 | `_call_api` → `/api/*` | 不自建旁路；`api_fastapi.py:198-228` 再校验一次 token + 参数校验 |
| S4 | MAS 写路径唯一门面 | mas_adapter.py:172-207 | `rerun()`/`cleanup()` `dry_run=True` 默认；预演分支**根本不 import mas_l3**，物理上碰不到远端 |
| S5 | CLI 落盘需显式开关 | `tools/auto_mas_*.py` | `--apply`（add_slots/flatten/rerun）、`--dry-run` 默认（toggle_user）；不加开关只打印计划 |
| S6 | 降级不抛异常 | mas_adapter.py:83-112 | 主机不可达统一 `{ok:False, degraded:True, degrade_reason}`，不让一端故障升级为整体崩溃 |

MCP 工具清单（`@mcp.tool()` 实测 **25** 个，mcp_server.py:91-260）：

- 只读 13（MAA）：`link_map, inspect_account, processes, status, dashboard, queue, account_status, events, stats_dashboard, health, depot_plan, activity_stages, probe_status`
- 写 5（MAA）：`enqueue, dequeue, queue_control, smart_dispatch, probe_stage`
- 只读 5（MAS）：`mas_snapshot, mas_status, mas_history, mas_ledger, mas_rerun_state`
- 写 2（MAS）：`mas_rerun, mas_cleanup`（均 `dry_run=True` 默认）

> 口径澄清：`20 个工具 / MAS 侧 MCP 工具数 = 0` 是 2026-09-26 上午的旧状态。15:10 的改动
> 新增了 `mas_adapter` 门面与 7 个 MAS 工具，MAS 侧已不是 0。

**MCP 接入地址**：`http://127.0.0.1:19997/mcp`（= `models/config.json → api_port` 的实测值 **19997**，2026-09-26 核对；完整片段与鉴权见 §5）。

## 3. 事实源归属表

| 状态 | 权威（唯一写者） | MAAOrch 的角色 | 落地位置 |
|---|---|---|---|
| 设备与 MAA 进程状态 | **MAAOrch（本机）** | 唯一权威 | `services/emu_service_v2.py`、`services/launch_queue.py`、`services/instance_pool.py` |
| MAS 队列 / 账号 / 班次真相 | **MAS 主机**（`E:\AUTO-MAS\config\ScriptConfig.json`、`QueueConfig.json`、`:36163`） | **只能快照 + 缓存**，禁止自建权威 | `services/mas_l3.py`（45s 缓存）、`tools/mas_watch.py`（SSH） |
| 计划与目标（跑谁/跑什么/优先级） | **Harness** | 落成参数，不落成副本 | 账号调度配置（`smart_global`、`slot_priority`） |

**反例（禁止）**：把 MAS 队列在本机镜像成可写表、或让 Harness 直接算班次 → 双事实源，
远端一改本机就错。本机只允许"快照 + `age_s`/`stale` 标记"，写操作一律回远端执行。

## 4. 统一动词表（Harness 意图 → 两个引擎）

| 意图 | MAA 侧落地（模块 / 端点） | MAS 侧落地（脚本 / 配置键） | 现状 |
|---|---|---|---|
| `enqueue` | MCP `enqueue` → `POST /api/queue/enqueue` → launch_queue.py:229 `enqueue()` | `tools/auto_mas_rerun.py`（临时队列 `TAG=临时-补跑`，`--apply`，`QueueConfig` 经 `:36163 /api/queue/add+update`）→ MCP `mas_rerun` | 两侧可用；MAS 仅"补跑"语义 |
| `dequeue` | MCP `dequeue` → `POST /api/queue/dequeue` → launch_queue.py:284 `dequeue()` | **待补**（无单号出队脚本；近似 = `auto_mas_toggle_user.py` 停用用户 `Info.Status`） | MAA ✅ / MAS ❌ |
| `pause` | MCP `queue_control("pause")` → `POST /api/queue/pause` → launch_queue.py:168 `pause()` | **待补**（无全局暂停；近似 = `auto_mas_queue_times.py` 改 `Info.TimeEnabled=False`，只作用于定时层） | MAA ✅ / MAS ❌ |
| `resume` | MCP `queue_control("resume")` → `POST /api/queue/resume` → launch_queue.py:172 `resume()` | **待补**（同上，`TimeEnabled=True`） | MAA ✅ / MAS ❌ |
| `rerun` | 无独立补跑动词；等价 = `smart_dispatch` → `POST /api/action/smart_selected`（api_fastapi.py:3012），任务内容由账号配置决定 | `tools/auto_mas_rerun.py --apply`；services/mas_l3.py:270 `rerun_async()` → MCP `mas_rerun`（默认 dry_run） | 语义不等价，需在 Harness 侧区分 |
| `set_slots` | `POST /api/config {"parallel_max": N}`（api_fastapi.py:2068/2070）→ `services/instance_pool.py:203 ensure_maa_instances_async`（按 `parallel_max+1` 备实例；调用点 api_fastapi.py:2082、3083、3149） | `tools/auto_mas_add_slots.py --to N --apply`（槽位 = `日常定时N`/`晚班定时N`，错峰 05:00 / 19:30 各 +3min）；配平 `tools/auto_mas_flatten.py --apply` | 两侧均**未暴露给 Harness** ❌ |
| `toggle_account` | 账号级 `suspended` 只能全量写 `POST /api/accounts/batch_save`（api_fastapi.py:1692；字段见 1708-1713 挂起审计） | `tools/auto_mas_toggle_user.py --name X [--enable]`（写 `ScriptConfig.json` `Info.Status`，锁感知 + 三重校验） | 两侧均**未暴露** ❌ |
| `probe_stage` | MCP `probe_stage` → `POST /api/probe/stage` → `services/stage_probe.py` | **待补**（MAS 侧无关卡检测；probe 只针对本机模拟器） | MAA ✅ / MAS N/A |

## 5. 现状差距清单（G1–G6）

- **G1 MAS 写动词覆盖率低**：25 个工具里 MAS 写只有 `mas_rerun`/`mas_cleanup`；`set_slots`/`toggle_account`/`pause`/`resume`/`dequeue` 在 MAS 侧**只有 CLI**，无 adapter 入口、无 MCP 工具。
- **G2 MAA 侧账号级动词缺失**：`toggle_account`（挂起）与 `set_slots` 未暴露；前者必须走全量 `batch_save`（覆盖风险，见 1710 注释），后者改配置会触发实例重建，均不适合 agent 直接裸调。
- **G3 事件归一未覆盖 MAS**：`event_bus` 只装本机事件，`services/mas_l3.py` 对 `event_bus` 引用数 **0**；远端只有 `runtime/mas_ledger.jsonl`。Harness 想知道"远端刚发生什么"只能轮询 `mas_status`。
- **G4 双事实源风险（读取面）**：MAS 真相有 4 条并行读路径 —— `mas_l3.host_snapshot`(SSH)、`mas_l3.history`(`:36163`)、`tools/mas_dashboard.py`（自读 `MAS_ROOT=E:\AUTO-MAS`）、`tools/mas_accounts.py`（现取 `ScriptConfig.json`）。`mas_adapter` 已是唯一门面，但 dashboard/accounts 仍自带聚合口径。
- **G5 端口 / 入口漂移（高危）**：MCP 挂在 Python FastAPI（`app.mount("/mcp")`，2026-09-26 实测 api_fastapi.py:**3807**；G6 插入 13 行后由 3794 漂到此处，**行号会继续漂移，请按 `build_mcp_app` / `app.mount` 符号检索**），实际端口 = `models/config.json → api_port`，**当前实测 19997**；外部入口通常由 Node 看板承接（**端口可配**：脚本传 `--port 19999`，mjs:65 默认 19998）且**不代理 `/mcp`**（`server/maaorch-web.mjs` 无 mcp 关键字）。外部配置写 `19999/mcp` 会打不到 MCP。
- **G6 写默认不一致 + 空 token 不鉴权**：MAA 侧写工具直接执行（无 `dry_run`），MAS 侧默认 `dry_run=True`，调用方要记两套语义；且 `api_token` 为空时 MCP 挂载点（mcp_server.py:65）与 `/api/*`（api_fastapi.py:218）**都不鉴权**，属误配风险。

### 接入片段（Hermes `mcp_servers`）

> **【不要真的去改 `C:\Users\xiach\AppData\Local\hermes\config.yaml`，由用户确认后再动】**
> 下面只是候选片段，未经用户批准不落盘。

```yaml
# C:\Users\xiach\AppData\Local\hermes\config.yaml
mcp_servers:
  maaorch:
    # 端口取 models/config.json 的 api_port。2026-09-26 实测 = 19997；
    # 19999 是历史默认值，现通常由 Node 看板承接（端口可配，以启动参数为准）且不代理 /mcp（见 G5）。
    url: http://127.0.0.1:19997/mcp
    headers:
      Authorization: "Bearer <api_token：models/config.json 的 api_token 字段值>"
    enabled: true
```

备选（若该 Hermes 版本只支持 stdio 形式 —— 现有 6 个条目全是 `command/args/env`）：

```yaml
mcp_servers:
  maaorch:
    command: npx
    args: ["-y", "mcp-remote", "http://127.0.0.1:19997/mcp",
           "--header", "Authorization: Bearer ${MAAORCH_TOKEN}"]
    env:
      MAAORCH_TOKEN: "<api_token>"
    enabled: true
```

- 键名 `url/headers` 为 streamable-http 常规写法，**需按 Hermes 版本文档确认**（本仓无该文件所有权）。
- token 明文入 YAML = 凭据扩散；优先环境变量或占位符，勿提交。
- 若 `api_port` 留空，Python 默认回到 19999（`models/config_manager.py:16`），届时 URL 同步改。
