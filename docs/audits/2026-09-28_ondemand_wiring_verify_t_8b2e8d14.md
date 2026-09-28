# mas-ondemand 接线核验（kanban t_8b2e8d14，2026-09-28 09:2x）

> 问题：`tools/mas_ondemand_mcp.py`（t_1aa55dac 交付）**是否真的注册进 mas profile 的
> `mcp_servers` 与 `platform_toolsets.cron`，且可被巡检调用**。
> 姿态：**只读核验**；未改 `config.yaml`、未改巡检提示词、未发真车（唯一写动作 = 工具自身设计的 dry-run 审计行）。
> 结论一句话：**已可用** —— 注册（配置 + Hermes 注册日志 + schema 缓存）与可调用（Hermes MCP 面真实 dry-run 调用）
> 双证齐全，巡检 cron 面 3 连实跑自报 `MCP_API_TOOL_COUNT=33`（含 mas-ondemand 6）；
> 唯一待办 = 巡检提示词三处**文本**更新（§3 diff 草案，交 lead，未执行）。

---

## 1. 现状（逐项实测，全绿）

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| 1 | `mcp_servers` 含新 server | ✅ | `profiles/mas/config.yaml:402-418`；`hermes -p mas config get mcp_servers.mas-ondemand` 原样回读（enabled: true / timeout: 900 / tools.include 6 名） |
| 2 | `platform_toolsets.cron` 含其工具集 | ✅ | `config.yaml:152` `- mas-ondemand`；`hermes -p mas config get platform_toolsets.cron` 末行同 |
| 3 | 变更面 = 纯新增 | ✅ | `diff config.yaml.bak-ondemand-20260928-055725 config.yaml` = +1 行（cron 列表）+16 行（server 块），无其它改动；Hermes 自管快照 `backups/config/config.yaml.good.20260928-060339` 与当前逐字节一致 |
| 4 | 工具清单逐名对齐（6/6） | ✅ | 实现 `_SCHEMAS`（`tools/mas_ondemand_mcp.py:959-1067`）/ `_DISPATCH`（:1069-1076）＝ config `tools.include` ＝ Hermes 注册名：`list_running / dispatch_queue / dispatch_script / dispatch_account / stop_task / ondemand_preflight`；**无缺无多** |
| 5 | Hermes 侧注册日志 | ✅ | `profiles/mas/logs/agent.log(.1)`：`MCP server 'mas-ondemand' (stdio): registered 6 tool(s): mcp__mas_ondemand__…`（首次 06:01:05；本会话 09:04–09:14 每次 spawn 均 6/6） |
| 6 | CLI 视图 / schema 缓存 | ✅ | `hermes mcp list` → `mas-ondemand · 6 selected · ✓ enabled`；`cache/mcp_schema_cache.json:13764` 有 6 工具 schema（fingerprint `7334d292c04f2066`） |
| 7 | `--check` 自检 | ✅ | `python tools/mas_ondemand_mcp.py --check` → `OK`，exit 0；`reachable=true code=200 queues=40 scripts=54`；`dry-run dispatch_queue(日常定时1)=would_dispatch` |
| 8 | **真实调用（经 Hermes MCP 面）** | ✅ | 本会话 `mcp__mas_ondemand__dispatch_queue{name:"日常定时1"}`（apply 缺省=false）→ `{"ok":true,"dry_run":true,"would_dispatch":"日常定时1","via":"mas_dispatch(shift_gate)"}`；审计落盘 `runtime/mas_ondemand_log.jsonl` 09:20:53 pid 44396 |
| 9 | 巡检（cron）面可见 | ✅ | 见下 §2 |

### 1.1 巡检面证据（cron 工具面 = job 解析路径）

- job `52b88b0ffca0`（mas-readonly-inspection）`enabled_toolsets = null` ⇒ 走 `platform_toolsets.cron`
  （优先级：`cron/scheduler.py:487-511`；平台列表点名任一 MCP server 即构成 allowlist：
  `hermes_cli/tools_config.py:692-704`）。
- **3 连实跑自报（真身证据）**：
  - `06:50:26` 轮：`MCP_API_TOOL_COUNT=33`，证据行「工具面 33（=设计面 22 + mas-observe 5 + mas-ondemand 6；禁用项=无）」
  - `08:12:58` 轮：`MCP_API_TOOL_COUNT=33 | ISOLATION=PASS（明细10/6/4/5/6/2；禁用项=未出现）`
  - `08:56:51` 轮：`MCP_API_TOOL_COUNT=33`，「工具面实际=33（设计22+mas-observe5+mas-ondemand6；无禁用项，**mas-ondemand 写工具未使用**）」
  - （对照：`06:09:29` 轮还是 `27` —— 切换发生在 06:09→06:50 之间。）
- **刷新时效**：配置写入 05:57/06:03 → 06:50 轮即 33；期间 `logs/gateway.log` **无重启记录**
  （上次停/起为 09-27 05:08）⇒ 本次「增 server」由 reconciler / 每轮解析拾取，**不需要手动重启网关**。
  （09-26 的旧硬事实「改 MCP 必重启网关」不适用于本次增项；改**已连 server 的 schema/代码**仍需
  `enabled false→true` 强制重连，见 roadmap §9。）

## 2. 差距（3 处文本面 + 2 处文档漂移；接线面零缺陷）

- **G1 巡检提示词「设计面」计数陈旧**：第一步仍写「设计面：auto-mas-monitor 10 / maaorch-daily 6 /
  mas-dispatch 4 / mas-triage 2」（=22），实际 33。每轮巡检都要自行调和这 11 个差值（3 连均如此），
  计数从「断言」退化为「报数」。历史：`docs/audits/2026-09-28_gate_wiring_check.md:179-183` 已起草过
  observe 版 diff 但未落地；本卡把 ondemand 一并补上。
- **G2 铁律写动作清单未覆盖新写工具**：cron 面现已多出 `dispatch_script / dispatch_account / stop_task`
  （均默认 dry-run，但 `apply=true` 即真写）。提示词禁止清单只列到 `smart_dispatch`，未点名这三个
  ⇒ 「可见即默认可调」的灰区（08:56 轮已自我克制「未使用」，但这是运气不是闸门）。
- **G3 第二步/第三步回退文案陈旧**：第三步 5) 的 UNKNOWN 写法「（mas-observe **未接线**，见
  docs/MCP_TOOL_ROADMAP.md §3-4）」与事实不符（observe 于 01:34 接线）。
- **D1 文档漂移（不影响发车）**：`docs/MCP_TOOL_ROADMAP.md:368`（§8.3）仍写「预期 **26**」（应 33）；
  `docs/MAS_SHIFT_PLAYBOOK.md:142` 写「设计面 **21** 个」（应 33）。
- **命名备注（无需改）**：卡面示例写 `preflight`，实际注册名 = `ondemand_preflight`
  （`mcp__mas_ondemand__ondemand_preflight`），与实现/config 一致，登记以防后人误查。

## 3. diff 草案（**未执行**，交 lead 批准；config.yaml 无需改动）

对象：cron job `52b88b0ffca0` 的 `prompt` 字段（`profiles/mas/cron/jobs.json`）。三处**纯文本替换**：

**A. 第一步「设计面」计数**（把 22 → 33 并写成可对表断言）
```diff
- （设计面：auto-mas-monitor 10 / maaorch-daily 6 / mas-dispatch 4 / mas-triage 2；工具面刷新可能滞后，如实报）
+ （设计面=33：auto-mas-monitor 10 / maaorch-daily 6 / mas-dispatch 4 / mas-triage 2 / mas-observe 5 / mas-ondemand 6；
+  工具面刷新可能滞后，如实报；计数≠33 ⇒ 记 `TOOLFACE_DRIFT=<差值>:<缺失server>`，上游掉线（monitor 502 等）注明缺失面，不许静默）
```

**B. 铁律·写动作禁止清单**（补齐三个新写工具）
```diff
- 其余写操作一律禁止：account_stop / maa_stop_all / maa_launch_source / queue_control / dequeue / enqueue /
- emulator_* / kill_audit_decide / mas_cleanup / smart_dispatch。
+ 其余写操作一律禁止：account_stop / maa_stop_all / maa_launch_source / queue_control / dequeue / enqueue /
+ emulator_* / kill_audit_decide / mas_cleanup / smart_dispatch /
+ dispatch_script / dispatch_account / stop_task（mas-ondemand 三个写工具在面内可见：只准 dry-run 预演，禁 apply=true）。
```

**C. 第三步 5) 回退文案**
```diff
- 未列出 ⇒ 写 `UNKNOWN:工具面未提供（mas-observe 未接线，见 docs/MCP_TOOL_ROADMAP.md §3-4）`；
+ 未列出 ⇒ 写 `UNKNOWN:工具面未提供（mas-observe 已接线，见 docs/MCP_TOOL_ROADMAP.md §9/§10；缺 observe 5 时按 TOOLFACE_DRIFT 记）`；
```

可选 **D. 文档**：roadmap:368 `26` → `33`；MAS_SHIFT_PLAYBOOK:142 `21` → `33`。

- **执行路径（批后）**：备份 `cron/jobs.json` → `hermes -p mas cron edit 52b88b0ffca0 --prompt "<新 prompt>"`
  （或 cronjob 工具 update）；**回读校验**：`hermes -p mas cron list` / 直读 jobs.json 三处文本 +
  下一轮（≤30min）巡检首行应为 `MCP_API_TOOL_COUNT=33` 且不再出现「设计面 22」调和语。
- **回滚**：恢复 jobs.json 备份（或重放旧 prompt 三行）。
- **config.yaml：无 diff** —— 现状即正确姿态（§1），本卡未改、也不建议改。

## 4. 风险

- **R1（需 lead 决策，低）**：A+B 落地后，巡检即无「停跑飞的按需车」授权路径（`stop_task(apply=true)`
  被禁）。建议：本轮先禁（保持「只两个写动作」不变量），若需要应急停车 ⇒ 另开卡定义触发条件与台账要求。
- **R2（中）**：33 是「全连」口径；上游 auto-mas-monitor 掉线时历史出现 17/20/21 的降级。A 中
  `TOOLFACE_DRIFT` 写法即为防这种正常掉线被误判 `ISOLATION_FAILED`；若不采纳该写法，硬断言会让
  flappy 上游变成巡检误报源。
- **R3（低）**：本卡只证「注册 + 可调用」；「巡检**会**调用」（例：现场三源在跑清单改用 `list_running`、
  补位前 `ondemand_preflight`）需要 G1-G3 同一批改提示词，属后续卡。
- **R4（低）**：`mcp_schema_cache.json` 为**每 server 一条**（6 工具整包），非逐工具缓存 —— 不影响，
  每次 spawn 都实测 6/6 注册（§1 #5）。

## 5. 复现命令（只读，可重跑）

```bash
hermes -p mas mcp list                                   # mas-ondemand 6 selected ✓
hermes -p mas config get mcp_servers.mas-ondemand        # server 块
hermes -p mas config get platform_toolsets.cron          # 末行 - mas-ondemand
C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe tools/mas_ondemand_mcp.py --check
grep "mas-ondemand" "C:/Users/xiach/AppData/Local/hermes/profiles/mas/logs/agent.log" | tail -3
# 巡检面：读最近轮次
grep -h "MCP_API_TOOL_COUNT=" .../cron/output/52b88b0ffca0/2026-09-28_0*.md | tail -3      # 应为 33
tail -1 runtime/mas_ondemand_log.jsonl                    # dry-run 审计行
```
