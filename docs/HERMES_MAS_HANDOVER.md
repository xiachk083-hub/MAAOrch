# Hermes 接管 AUTO-MAS：交接契约

日期：2026-09-26 ｜ 状态：**只读巡检已落地并实跑验证**；受控补漏**尚未启用**

## 1. 交接的是什么

把 AUTO-MAS 的**日常巡检与补漏判断**从「人去看 MAAOrch 前端」改成「Hermes 定时自己做」。
MAAOrch 的 REST/MCP **不在本链路里**，Hermes 直接对接 AUTO-MAS 自己的 MCP。

```
Hermes(mas profile, cron)
   → tools/mas_scrub_proxy.py   (stdio 代理：递归脱敏 + 只读硬闸)
      → AUTO-MAS 官方 MCP  http://100.79.173.69:36163/mcp  (118 工具, 无鉴权)
```

## 2. 已落地的接线（mas profile）

配置文件：`C:/Users/xiach/AppData/Local/hermes/profiles/mas/config.yaml`

| 条目 | 用途 | 工具数 |
|---|---|---|
| `auto-mas-scrubbed` | 交互式人工会话：全量控制，但凭据被脱敏 | 118 |
| `auto-mas-monitor` | **无人值守**：`MAAORCH_MAS_READONLY=1` 只读硬闸 | 10 |

**隔离靠配置，不靠提示词**：

```yaml
platform_toolsets:
  cron:
    - auto-mas-monitor
```

依据 `hermes-agent/hermes_cli/tools_config.py:692-704` `_merge_mcp_servers`：
点名一个 MCP server 即构成 allowlist；**不点名时会把所有已启用 MCP 交给该平台**（即全量 118 工具、含 44 个写/危险）。

## 3. 已验证的证据（可复现）

```powershell
hermes -p mas mcp test auto-mas-monitor    # ✓ Connected, Tools discovered: 10
hermes -p mas mcp test auto-mas-scrubbed   # ✓ Connected, Tools discovered: 118
```

真实巡检运行（execution `e5215e48c2db4f7386cfce7b7eae9cff`，2026-09-26 17:10:18 完成；
`state.db` 实测 **20 次 LLM / 85 次工具调用**，交接文档初稿写的 79 已按数据库订正。
**注意：这是一次手动 direct 运行，它证明不了自动路径可用 —— 见 §11**）
的 agent.log 中：`mcp__auto_mas_monitor__*` 被实际调用多次，**`mcp__auto_mas_scrubbed__*` 实际调用 = 0**。

该次运行的报告落在 `profiles/mas/cron/output/52b88b0ffca0/2026-09-26_17-10-18.md`，其中 **Hermes 自己报告**：

```
MCP_API_TOOL_COUNT=10；ISOLATION=PASS（10 个工具全为 get_/search_ 只读查询，
未发现任何 add/update/delete/remove/import/export/close/start/stop/set 写操作工具）
队列总数：25（启用 24 = 日常定时1-12 + 晚班定时1-12；另有 1 个「临时-补跑-0923_0044-1」已禁用、无定时项）
定时项：24 个、全部启用；日常批 05:00/05:03/…/05:33，晚班批 19:30/19:33/…/20:03（各 12 档、间隔 3 分钟）
本次全程只读，未补跑、未发车
```

**这是"Hermes 实际加载并正确使用只读 MCP"的最强证据** —— 隔离自检由 agent 自己在运行中完成并报出。

只读白名单（代理内 10 个精确工具名，非 `get_*` 泛匹配）：
health / git_version / overview / queues / queue item / queue time get / scripts get / scripts user get / history search / history data。

写类调用（`add_`/`update_`/`delete_`/`import_`/`export_`/`close_`/`start_`/`stop_`）**在触达上游之前**即被代理拒绝（零上游调用）。

## 4. 必须保留的东西：MAS 自身的定时（层一）

实测基线（2026-09-26）：
- 队列 **25** 个（其中 1 个无定时项）
- 定时项 **24** 个，**全部 `Enabled: true`**
- 两个班次、各 12 个、间隔 3 分钟：**早班 05:00 → 05:33**，**晚班 19:30 → 20:03**

> ⚠️ **2026-09-27 已被推翻（用户决定）**：层一不再由 AUTO-MAS 触发，**改由 Hermes 的 cron 发车**。
> AUTO-MAS 侧 24 条定时项已全部 `Enabled=false`（Days/Time 保留，可一键回滚）。
> 变更记录、时刻表、闸门、回滚命令见 `docs/audits/2026-09-27_hermes_takes_over_mas_scheduler.md`。
> Hermes 从此既是调度员也是巡检员（相对 AUTO-MAS 多的那层：发车前后体检、fail-closed、留痕）。

## 5. 分阶段接管

| 阶段 | 内容 | 状态 |
|---|---|---|
| **① 只读巡检** | 每 30 分钟读健康/总览/队列/定时/历史，产出简报 | **已落地并实跑** |
| **② 告警** | 同 ①，但仅在异常或疑似漏跑时输出（静默即正常） | 待启用 |
| **③ 受控补漏** | 有确凿证据时补跑**单个**目标 | **未启用**（见 §7） |

回滚：`hermes -p mas cron pause 52b88b0ffca0`（停止巡检）；`hermes -p mas cron remove 52b88b0ffca0`（移除）。
配置回滚：`profiles/mas/config.yaml.bak-*` 各阶段备份。

## 6. 硬边界（任何阶段都不许越过）

1. **不补跑**，除非满足 §7 全部条件。
2. 绝不调用：关闭后端、改脚本/账号配置、清队列、改定时（`queue/time/*` 写操作）、删账号、导入导出。
3. **运行态未知 ⇒ 一律不补跑**（宁可漏一轮，也不重复烧理智）。
4. 不读取 `models/config.json`、`auth.json`、`.env` 等凭据文件。
5. 凭据字段即使被读到也必须是 `<scrubbed>`；发现真实口令即视为故障并停止。

## 7. 受控补漏的准入条件（尚未启用，需人确认后才开）

同时满足才可补跑**一个**目标：
1. 该队列今日在 MAS 原定时里 `Enabled`；
2. `/api/history/search` 中**今日确实没有**对应的完成记录；
3. 当前没有该队列/该机器的任务在跑（无并发）；
4. 距原定时已过 ≥ 60 分钟（避开 MAS 正在发车的窗口）；
5. 单轮只补一个目标，且写入幂等记录（同一目标当日不重复补）。

不满足任一条 ⇒ 只输出「候选 + 证据」，不发车。

## 8. 可直接放进 Hermes cron 的中文任务提示词

见当前已建任务 `52b88b0ffca0`（`hermes -p mas cron list` 可查看）。要点：
只读、先做隔离自检、报告 `MCP_API_TOOL_COUNT` 与 `ISOLATION=PASS/FAILED`、
只输出补漏候选不发车。

## 9. 诚实声明：边界在哪里

**提示词不是安全边界。** 真正的边界是：
- 代理侧 `READONLY_ALLOWED` 精确白名单（写调用零上游触达）；
- `platform_toolsets.cron` 只放行 `auto-mas-monitor`。

但**挡不住**：MAS 端点本身无鉴权（Tailscale 段内任何人可直连 36163）、
调用"发生"这一事实、请求参数内容、上游日志。

## 9.5 受控补漏的两道保险（实测校准）

1. **判据器 `tools/mas_catchup_check.py` 只提议、不发车**：源码只允许 6 个只读端点，
   `api()` 对白名单外路径直接抛错；测试断言源码不含任何写端点。
2. **批次闸（2026-09-26 实测教训）**：MAS **没有运行态接口**，批次卡住时队列会被静默堵死。
   实测当天：05:56 起跑、只完成 2/52、最后活动 06:27，此后 11 小时零活动。
   此时逐条判据会算出 **56 个假候选**；加上「今日有记录账号数 < 历史基线 × 50% ⇒ 判批次未结束、
   一律禁止补跑」的全局闸后，同一数据下候选降为 **0**，与巡检员的独立判断一致。

## 10. 已知缺口

1. **MAS MCP 无鉴权** —— 建议加 ACL 或前置 token 反代（未做）。
2. `search_history` 单次返回 30 万字符级 —— 巡检提示词需限定日期范围，否则浪费 token。
3. **`get_queues` 经 MCP 实际不可用**（MAS 侧 schema 限制，非本交接可修）：
   空 arguments → MAS 当无 body → 422 `Field required`；传 `queueId: ""` 或任意非 UUID →
   500 `badly formed hexadecimal UUID string`。REST 传 `queueId: null` 可正常返回 25 个队列，
   但 MCP 生成的 schema 把 queueId 定成非空 string，表达不了 null。
   ⇒ 巡检提示词不要依赖「队列总数」；需要队列清单请走 REST（`tools/mas_catchup_check.py` 就是走 REST）。
4. 历史遗留的判据文件 `data/daily_state/*.json` 已 8 天未更新 —— **不要**把它当作"今日是否完成"的依据，一律以 `/api/history/search` 的当日记录为准。
5. 层二（`tools/scheduler.py`）本就无运行入口、已 8 天未跑；本次不恢复它。
6. **自动路径曾长期假死（2026-09-26 发现并修复）** —— 见 §11。

## 11. 自动路径事故 + 根因 + agent-time 量化（2026-09-26 追加）

- **自动巡检在修复前一次都没成功过**：17:41:05、18:12:05、18:52:33 三次 30 分钟自动 fire 在 0.2~0.45 秒内崩掉，
  报 `RuntimeError: Failed to initialize OpenAI client: No module named 'pydantic_core._pydantic_core'`。
- **根因（19:03 临时探针抓到调用栈）**：`gateway/run.py:422 _ensure_windows_gateway_venv_imports()` 为让 detached
  网关看到 venv 包做了两级退路 —— 先试 `VIRTUAL_ENV`（launcher 给的是 `<hermes>\tools`，**不是 venv**，跳过），
  再退到 `<repo>/venv`：也就是 2026-09-24 建的 **3.11 uv 遗留 venv（572 MB，仍躺在磁盘上）**，用 `site.addsitedir()`
  把它的 site-packages 插到 `sys.path[1]`，压过 PM 正式安装的 3.14 环境。自动 fire 是**网关进程内**执行
  （`executions.source=builtin` → `cron/scheduler.py:2522 run_job` → `_construct_cron_agent` →
  `agent_init._init_openai_client`），于是 `import openai` 拿到 3.11 的 pydantic_core，在 3.14 解释器下必崩；
  手动 `hermes cron run` 走独立进程（`source=direct`，经 `hermes.exe` 干净启动）所以一直正常 —— 这就是
  "手动能跑、自动必挂"的全部原因，也说明 §3 那次"实跑验证"验证的是 MCP 隔离，**不是自动触发**。
  旁证：同一网关里 ark 的两个 cron 任务 262 次 `builtin` 全部成功 —— 它们是脚本型任务，不构造 AIAgent。
  （更正：19:00 前我曾判断为"启动 shell 的 PYTHONPATH 污染"，重启网关后 18:52 仍失败，该结论已被推翻。）
- **修复**：停网关 → `Rename-Item hermes-agent\venv venv.pre-pm-3.11.bak` → 启动网关。修复后探针显示网关
  `sys.path` 里 `hermes-agent\venv` 命中数为 **0**，`hermes doctor` 正常，四个 profile 网关 running。
  回滚 = 把目录名改回 `venv`（但故障会复现）。
- **残留风险**：① `hermes update`/重装若重新生成仓库内 `venv`，故障复发 —— 用探针第一时间发现；
  ② `gateway-service\Hermes_Gateway.cmd` 仍会把继承的 `%PYTHONPATH%` 追加进长命进程；
  ③ `.hermes-runtime\python\cpython-3.11...` 等 PM 迁移残留可另行清理。
- **验证通过（19:23:04 首次自动 fire）**：`source=builtin, status=completed`，agent time **199.8 s**，
  3 次 LLM / 4 次工具 / 96,760 token；巡检产物 `cron/output/52b88b0ffca0/2026-09-26_19-26-24.md` 给出
  `MCP_API_TOOL_COUNT=10`、`ISOLATION=PASS`、后端 ready、今日 2/2 DONE。修复前同类 fire 0.2~0.45 秒即崩。
- **量化与探针**：`tools/mas_agent_time.py`（只读，`mode=ro` 打开 Hermes sqlite）；`--check` 退出码 0/3/4 判自动路径健康
  （最近一次自动 fire 成功而窗口内仍有旧失败时报 `AUTO_PATH=RECOVERED`，退出码 0）；
  正常模式产出 `docs/audits/<date>_mas_agent_time.{md,json}`；测试 `tests/test_mas_agent_time.py`（13 项）。
- **agent-time 基线**（30 分钟节拍 ⇒ 48 次/天）：

  | 口径 | 单次 agent time | 占用 | agent time/天 | token/天 |
  |---|---|---|---|---|
  | 提示词收紧前（16:50 样本，手动） | 1160.9 s | 64.5% | 15.48 h | 124.4 M |
  | 收紧后（18:16 样本，≤6 次工具预算） | 303.0 s | 16.8% | 4.04 h | 11.0 M |

- **Hermes 侧只保留 mas 这一个定时任务**（`52b88b0ffca0`，every 30m）：不新增探针 cron、不加第二个巡检任务；
  探针只在人工/看板按需调用。
- 全过程证据链：`docs/audits/2026-09-26_hermes_autopatrol_incident.md`。

## 12. Hermes 调用 MAA 清日常（2026-09-26 追加）

- **MAA 本家没有 MCP、也没有对外 HTTP 面** ⇒ 只能经调度层：Hermes → MAAOrch MCP（`127.0.0.1:19997/mcp/`，41 工具）→ `mas_rerun` → 远端 AUTO-MAS → `MAA.exe`。证据见 `docs/audits/2026-09-26_hermes_clear_daily.md` §1。
- **已接线**：`profiles\mas\config.yaml` 新增 `mcp_servers.maaorch-daily`（`tools.include` 只放行 6 个工具：`mas_status, mas_snapshot, mas_rerun_state, account_status, queue, mas_rerun`），
  `platform_toolsets.cron` 追加该 server；唯一任务 `52b88b0ffca0` 的提示词改为「只读巡检 + 清日常补漏」（1358 字）。
- **为什么是 `mas_rerun` 而不是 `smart_dispatch`**：后者无 `dry_run`（一调即下发）且只覆盖 MAAOrch 本机 3 个账号；产线 54 个账号在远端 AUTO-MAS，只有 `mas_rerun` 能补跑（原生 `dry_run=True` 预演、单飞锁、单批 ≤8）。
- **发车准入**（提示词硬条件，全部满足才动）：不在守卫窗（05:00–05:40 / 19:20–20:20）、今日确有失败账号、当前无批次在跑；且必须先 `dry_run=True` 预演再确认。
- **依赖**：MAAOrch Python 上游需在跑（不常驻是设计；不可达时 fail-closed 只报错不发车）。启动 `tools\start_python_upstream.bat`。
- **回滚**：见 `docs/audits/2026-09-26_hermes_clear_daily.md` §7（改 `tools.include` 即可 30 秒退回纯只读）。
### 12.1 两条运维硬事实（2026-09-26 21:0x 实测，必须遵守）

1. **改完 `config.yaml` 的 `platform_toolsets` / MCP server 后必须重启网关** —— 网关进程会缓存，`builtin`（自动）运行拿不到新工具面。实测：重启前 20:43 的 `builtin` 运行报「工具集未包含 mas_status / mas_rerun」，`direct`（`hermes cron run`，新进程）却正常；`hermes gateway restart` 后 20:54:57 的 `builtin` 运行报告 `MCP_API_TOOL_COUNT=20（auto-mas-monitor 10 + maaorch-daily 10）`。
2. **MAAOrch Python 上游（19997）不常驻，也不需要常驻** —— 平台设计如此（空转 ~47% CPU）。Hermes 的 `maaorch-daily` 已是 **stdio 代理形态**（`tools/mas_maaorch_daily_proxy.py`），调用时按需拉起上游（首次 ≤45 s；MCP 工具调用超时默认 300 s）。实测：上游被杀（`listen19997=0`）后，21:24:11 的 `builtin` 运行照常完成（21:28:34），运行后 `listen19997=1`。上游彻底起不来时代理结构化报错、Hermes 侧 fail-closed（只报后端不可达，不发车）。

### 12.2 成本（新提示词，自动路径实测）

- 20:54:57 `builtin`：**374.2 s / 4 次 LLM / 5 次工具 / 217,266 token**（30 分钟节拍 ⇒ 约 5 h/天、10.4 M token/天），与「只读巡检」收紧后基线（303 s）同量级。
- 报告示例（20:54:57）：`MCP_API_TOOL_COUNT=20`、`ISOLATION=PASS`、今日 20 个账号全部 DONE（16 一次过 + 4 重试成功）、`CLEAR_PREVIEW=未预演（today.failed_names 为空）`、`CLEAR_DISPATCH=未发车（无失败账号可补，且晚班批次仍在运行）`。

## 13. 队列发车的补发职责边界（2026-09-27 加固落地）

> 来源：卡 t_a53348ec（调度器加固）。完整证据/回滚见 `docs/audits/2026-09-27_dispatch_hardening.md`。

- **命令面**（`tools/mas_dispatch.py`，全部默认 dry-run）：`--check-schedule`（SHIFT_TABLE ↔ AUTO-MAS
  只读一致性校验）、`--busy`（busy 窗口 + 未关闭 late 标记）、`--release <队列> --apply`
  （人工确认对方任务已结束后清 busy 窗口）、`--dismiss-late <队列> --apply --note …`（放弃补发）、
  `--force-conflict`（显式放行资源冲突，记账 `conflict_overridden`）。
- **边界规则（硬）**：
  1. **队列级发车/补发** 只归 `mas_dispatch`：超追发窗口（>30min）**不自动补**，只记 `late` 标记 +
     `late_handoff` 台账告警；补发必须显式 `--queue <队列> --apply`（成功自动关标记+清告警），
     或显式 `--dismiss-late` 放弃。
  2. **账号级补跑** 只归巡检 `mas_rerun`（见 §12），与队列级动作正交；巡检的 MCP `dispatch_due`/
     `dispatch_queue` 对超窗队列只报 `late: true`，不发车。
  3. 同一队列同一班次**一天只发一次**（跨进程锁内单飞）⇒ 两侧即使同时动作也不会双发同一队列。
  4. 同资源（MAA 目录/脚本/实例）在 busy 窗内被其它队列占用时，发车**拒绝**且零上游写请求
     （`conflict_inflight` + 台账告警）；是否并行只由人工决定（`--release` / `--force-conflict`）。
- **巡检提示词建议追加一行**（可选，不强制）：`DISPATCH_ALERT` 里若出现 `:conflict_inflight` 或
  `:late_handoff`，只报告 + 给候选，不自行发车，等人工决定。
