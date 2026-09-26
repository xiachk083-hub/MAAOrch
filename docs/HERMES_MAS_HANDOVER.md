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

真实巡检运行（execution `e5215e48c2db4f7386cfce7b7eae9cff`，2026-09-26 17:10:18 完成，20 次 LLM / 79 次工具调用）
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

**层一由 AUTO-MAS 自己触发，不依赖 Hermes/MAAOrch。本次交接不修改、不关闭它。**
Hermes 只做巡检与"是否真的漏了"的判断 —— 它是补漏器，不是主力。

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
