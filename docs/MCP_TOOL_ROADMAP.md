# MCP 工具面扩展建议（新增工具清单 + 草案）

> 2026-09-27 ｜ 执行：mas（kanban **t_cf2dc452**，板 maa-parallel-benchmark-20260926）｜ **只读**：
> 未改任何配置（`platform_toolsets.cron` 的建议**只写在本文件里，未落地**）、未发车、未动实例。
> 取证 = 读源码/配置 + 两条只读实测（`mas_dispatch_mcp.py --check`、`mas_preflight.py --queue 晚班定时1 --skip-screen`）
> + 今日姊妹产物交叉引用（清单见 §7）。相关：`docs/HERMES_MAS_HANDOVER.md`（MCP 接线现状）、
> `docs/audits/2026-09-27_mas_preflight_wiring.md`（preflight 接线三方案）、
> `docs/audits/2026-09-27_mas_failure_taxonomy.md`（失败码 → 处置 → 是否补跑）。

---

## 0. 摘要（TL;DR）

- **现状**：cron 平台有 4 个 MCP server、设计值 **21 个工具**（auto-mas-monitor 10 / maaorch-daily 6 /
  mas-dispatch 4 / mas-triage 1）。今天新增的 5 个 CLI 产物（preflight / report / idle_emulators /
  client_state / identity_trace）**还没有任何 MCP 面** —— 巡检 agent 看得到"谁失败、有没有漏发"，
  看不到"为什么、现场什么样、该不该补、是不是串号"。
- **建议**：新增 **1 个只读 stdio MCP server `mas-observe`**，暴露 5 个只读工具；
  工具总数 21 → 26（§3 给了备选方案对比，推荐单新 server）。
- **`platform_toolsets.cron` 建议追加一行 `- mas-observe`（先不改）**，见 §4 的 YAML 与分阶段方案。
- **三个落地前提**（§5）：① 三个 CLI 的 ssh/adb 子进程补 `stdin=DEVNULL`（其中 `mas_error_triage.py`
  的挂死是已知在案缺陷 t_ae836e86）；② 冻结 5 个 CLI 的 `--json` 契约（产物仍在并行任务里演进）；
  ③ 巡检提示词的工具预算与调用时机同步改（现上限 15 次/轮，见 §4.3）。
- 5 个工具**全部只读**；唯一的"写"是可选的本机 runtime 报告落盘（`mas_report --write`，默认关）。

---

## 1. 现状盘点

### 1.1 现有 MCP 工具面（`profiles/mas/config.yaml` → `platform_toolsets.cron`）

| server | 传输/形态 | 上游 | 收窄层（注册期硬白名单） | 工具（数量） | 性质 |
|---|---|---|---|---|---|
| `auto-mas-monitor` | stdio 代理 `tools/mas_scrub_proxy.py` | AUTO-MAS 官方 MCP `http://100.79.173.69:36163/mcp`（118 工具、无鉴权） | `MAAORCH_MAS_READONLY=1`：只暴露 10 个**精确名**只读工具；写类调用**触达上游之前**即拒（零上游请求） | `get_health_*` / `get_overview_*` / `get_git_version_*` / `get_queues_*` / `get_item_*` / `get_time_set_*` / `get_script_*` / `get_user_*` / `search_history_*` / `get_history_data_*`（10） | 全只读（响应递归脱敏，凭据→`<scrubbed>`） |
| `maaorch-daily` | stdio 代理 `tools/mas_maaorch_daily_proxy.py`（按需拉起 19997 上游） | MAAOrch `http://127.0.0.1:19997/mcp/`（41 工具） | 代理面 10 → `tools.include` 再收窄到 6 | `mas_status` / `mas_snapshot` / `mas_rerun_state` / `account_status` / `queue` / `mas_rerun`（6） | 5 只读 + 1 写（`mas_rerun`，`dry_run=True` 默认预演） |
| `mas-dispatch` | stdio `tools/mas_dispatch_mcp.py` | 本机 `tools/mas_dispatch.py` + `tools/mas_switch_gate.py`（闸门） | 注册期白名单 4 | `list_schedule` / `dispatch_due` / `dispatch_queue` / `switch_gate`（4） | 2 只读 + 2 写（默认预演；`switch_gate` 纯只读） |
| `mas-triage` | stdio `tools/mas_triage_mcp.py` | 本机 `tools/mas_error_triage.py`（REST + ssh 只读） | 注册期 1 | `triage_error`（1） | 只读 |

**设计值合计 21 个工具。** 两条本轮实测（2026-09-27 21:2x）：

```
python tools/mas_dispatch_mcp.py --check
  → OK | tools=list_schedule,dispatch_due,dispatch_queue,switch_gate
  | reachable=True sent_today=24 due_not_sent=[] late=[] drift=[]      # 今日 24/24 已发

python tools/mas_preflight.py --queue 晚班定时1 --skip-screen          # 耗时 29s，只读
  → PREFLIGHT=GO（REST/槽位/实例/闸门 PASS；磁盘 C: 29.7GB WARN；OCR 第 4 项 SKIP）
```

**工具面刷新时效（重要）**：21:05 的巡检轮自报 `MCP_API_TOOL_COUNT=20`、且 `switch_gate` 不可见
（`SWITCH_GATE=UNKNOWN: 本轮工具面未提供 switch_gate`），而同期磁盘上的 `tools/mas_dispatch_mcp.py`
已含 `switch_gate`（`--check` 实测 4 工具）⇒ **新增/修改工具要等新会话或网关重启才进入工具面**
（依据 `docs/HERMES_MAS_HANDOVER.md` §12.1：网关缓存工具面，改完必须 `hermes gateway restart`）。
本文所有"数量"均为配置/源码口径的设计值，落地验收时以 `hermes -p mas mcp test <server>` 复核。

### 1.2 今日产物（本建议的直接输入）

| 产物 | 路径 | 出处（kanban） | 状态 |
|---|---|---|---|
| 班前 GO/NO-GO 自检器 | `tools/mas_preflight.py`（+接线建议 `docs/audits/2026-09-27_mas_preflight_wiring.md`） | t_cd11ca49 | running（CLI 已可用，实测 29s） |
| 每日错误聚合 | `tools/maa_error_daily.py`（`--summary` 打印 `ERR_SUMMARY=<n> 条，Top=<类型>`）+ `mas-triage.error_daily`（MCP 只读，见 §2.2 落地注） | t_d054ba36 | 已交付（CLI + MCP；审计 `docs/audits/2026-09-27_maa_error_daily.md`） |
| 空转实例收敛清单 | `tools/mas_idle_emulators.py`（只出清单+命令，不执行） | t_c47b71c5 | running |
| 客户端状态检测 | `tools/client_state_check.py`（`CLIENT_STATE=<…>` + `ACTION=<…>`） | t_65944d5b | running |
| 每轮身份留痕 | `tools/mas_identity_trace.py`（`watch`/`ocr`/`report` 三子命令） | t_6f159f59 | running |
| 发车重试/告警台账 | `tools/mas_dispatch.py`（`DISPATCH_ALERT=` / `runtime/mas_dispatch_alerts.json`） | t_1b4ce956 | running（台账已有 test 记录） |
| 切号注入闸门 | `tools/mas_switch_gate.py` + `mas-dispatch.switch_gate` | t_5c23c432 | running（MCP 面已进代码，巡检面待刷新） |
| 失败定因 | `tools/mas_error_triage.py` + `mas-triage` | 已上线 | 有已知缺陷：ssh 在 MCP stdio 下挂死（t_ae836e86，待修） |
| 调度接管 / 切号审计 / 失败模式库 / 任务模式 | `docs/audits/2026-09-27_{hermes_takes_over_mas_scheduler,maa_account_switch_history,mas_account_switch_chain,mas_account_switch_reset,mas_failure_taxonomy,mas_task_modes,maa_errors_triage,non_official_no_switch}.md` | 多条 | 已交付 |
| 手册类 | `docs/MAA_ROGUELIKE_PLAYBOOK.md` / `MAA_EVENT_FARMING_PLAYBOOK.md` / `MAA_TASK_CONFIG_REFERENCE.md` / `HERMES_MAS_HANDOVER.md` | t_a1cf9f5b / t_b8476841 / t_f270d627 | 已交付/运行中 |

### 1.3 现有工具面的空白（为什么需要这 5 个）

1. **定因链断裂**：巡检能拿到失败名单（`mas_status.today.failed_names` / `search_history`），
   但 `triage_error` 走 ssh 读忙时 `asst.log` 会 180s 超时（今晚实测 `triage_failed`），
   而 `maa_failure_taxonomy.md` §4 已指出应改走 history 小文件轻读 —— 这条轻读路径需要一个
   MCP 工具（即 `mas_report`）。
2. **发车前现场不可见**：`list_schedule` 看得见"到点没发"，看不见"车发出去能不能跑"
   （槽位忙 / 实例失联 / 残留模态 / 注入违规 / 磁盘水位）—— 今晚 MAA_7/8/9 的
   「活动弹窗卡 StartUp 50 连败」与官-15 孤儿态就是典型（`docs/audits/2026-09-27_maa_errors_triage.md`）。
3. **资源看不见**：「跑着但没人用」的实例（空转）、停在桌面/模态的客户端，只有人肉 ssh。
4. **串号不可回归**：切号注入闸门（`switch_gate`）验的是**配置层**应该切谁，**运行层**实际切了
   谁（`gui.log` 账号切换行 + 大厅帧 OCR UID）没有工具面。
5. **班次摘要缺失**：错误、发车告警、班次进度散在三处（history / gui.log / `mas_dispatch_alerts.json`），
   没有一页可读的日摘要。

---

## 2. 新增工具建议（5 个 · 全部只读 · 窄面）

约定：所有工具**只读**；输出账号一律掩码；失败一律结构化 `{ok:false, error:{kind, detail}}`；
"失败语义"里 **fail-closed 原则** = 读不到/判不了 ⇒ 返回 UNKNOWN/未知，**绝不允许**被调用方
解读为"正常"或"GO"。

### 2.1 `mas_preflight` —— 班次前 GO/NO-GO

| 项 | 内容 |
|---|---|
| **目的** | 把"这条队列现在能不能发"变成一行 `PREFLIGHT=GO/NO-GO`：REST 可达 / 目标槽位空闲 / 目标实例可连 / 客户端无残留模态 / 注入配置合规 / 磁盘水位（6 项）。 |
| **后端** | `tools/mas_preflight.py`（t_cd11ca49）。MCP 侧包 CLI（`--json`）。 |
| **入参** | `queue`(string，日常定时N/晚班定时N，N=1..12) 与 `script`(string，如 `明日方舟-官-1-1-7`) **二选一必填**；`skip_screen`(bool，**MCP 默认 true**＝跳过第 4 项截图+OCR，~30s；false=全量 +20~40s)；`min_free_gb`(number=10)/`warn_free_gb`(number=30)；`timeout`(int=240)。 |
| **出参** | `{tool:"mas_preflight", generated_at, api, target:{kind, name, targets, dirs, emus}, checks:{rest, slot_idle, emulator, residual, config_gate, disk}, reasons:[…], preflight:"GO"\|"NO-GO"}`（`--json` 原样输出该对象；包装侧补 `ok/elapsed_s`）；`--json` 时无 `PREFLIGHT=` 行（结构化字段为准）。 |
| **只读或写** | **只读**（不启动/不关闭实例、不改配置、不发车；截图只落本机 `runtime/preflight_shots/`）。 |
| **失败语义** | ① 任一检查 FAIL ⇒ `preflight="NO-GO"`（exit 1）；② 目标无法解析/异常 ⇒ exit 2 + stderr ⇒ MCP 包装映射 `ok=false, preflight="UNKNOWN"`（**等同 NO-GO**，fail-closed）；③ OCR 不可用且未显式 `skip_screen` ⇒ 第 4 项 FAIL（fail-closed）；④ 包装超时 ⇒ `UNKNOWN:timeout`，**不得**返回 GO。 |
| **重叠** | 与 `switch_gate`（第 5 项 = 目标目录的子集判定）、`list_schedule.reachable`（第 1 项）、`client_state_check`（第 4 项的单实例深查版）**部分重叠**。去重口径：同一轮里 preflight 已覆盖目标队列时**不必**再对同目录跑 `switch_gate`；巡检的 `SWITCH_GATE=` 行改由 preflight `checks.config_gate` 提供（或保留 12 目录版供全局校验，二者结论应一致）。 |
| **调用时机** | 巡检轮：`due_not_sent` 非空 / 准备 `dispatch_due(dry_run=False)` **之前**；或独立"班前哨兵"（04:55 / 19:25 批量跑 `--skip-screen`）。不要每轮无条件跑全量。 |
| **成本** | 实测 `--skip-screen` 29s；全量 +截图 OCR 约 40-60s（`docs/audits/2026-09-27_mas_preflight_wiring.md`）。 |

### 2.2 `mas_report` —— 每日错误/班次摘要

| 项 | 内容 |
|---|---|
| **目的** | 一页日摘要（班次日窗口 `[D 05:00, D+1 05:00)`）：错误按**槽位/账号/类型**聚合（致命/告警/可忽略三档）+ 一行 `ERR_SUMMARY` + 班次/发车摘要（已发/漏发/告警台账）。 |
| **后端** | `tools/maa_error_daily.py`（t_d054ba36；数据源 = history `maa_result` + 同名 `.log` + gui/asst 日志 + app.log + 只读 REST）+ 本机 `runtime/mas_dispatch_{state,alerts}.json` / `mas_dispatch_log.jsonl`（t_1b4ce956，**本地文件读取，不经 ssh**）。 |
| **入参** | `date`(YYYY-MM-DD，默认=今天，<05:00 自动取昨天)；`summary_only`(bool=false，只回 `ERR_SUMMARY` 一行+计数)；`include_shift`(bool=true，附班次/发车摘要)；`write_report`(bool=**false**，true 时落 `runtime/reports/maa_errors_<date>.{md,json}`)；`timeout`(int=420)。 |
| **出参** | `{ok, date, window, err_summary:"ERR_SUMMARY=<n> 条，Top=<类型>", counts:{n, fatal, warn, benign, by_cat, by_sev, top, login_n, escalated}, items:[{slot, account(掩码), ts, src, cat, sev, chain, evidence(≤N)}], hist:{accounts, attempts, ok, fail, rows}, slots:[…], app:{rounds, noise, other}, gui_noise, subtask, shift:{sent_today, due_not_sent, late, alerts:[{queue, reason, ts, terminal}], dispatch_failures_today}, degraded:{rest_ok, ssh_ok}, generated_at}`。 |
| **只读或写** | **只读**（远端全只读；唯一可写 = 可选的本机报告落盘，默认关）。 |
| **失败语义** | ① REST/SSH 不可达 ⇒ `err_summary="ERR_SUMMARY=UNKNOWN:<原因>"` + `degraded` 标志，**不阻断**调用方（仍返回班次块）；② 部分数据源可用 ⇒ 部分结果 + `degraded` 明细；③ 无错误 ⇒ `counts.n=0`（不是失败）；④ exit 2 映射 `ok=false`。 |
| **重叠** | ① `triage_error`：report = 清单+证据（第一层分流），triage = 单账号**链级**定因（第二层深挖）—— `mas_failure_taxonomy.md` §4 建议的"先轻读 history .log 判资源类、再决定是否深挖"正好落在这两个工具的分工上；② `search_history`/`get_history_data`（原始历史，未聚合）；③ `list_schedule`（24 条对账明细；report 只给摘要行与告警，不替代对账）；④ `mas_status.today`（MAAOrch 本机 3 个号口径；report 覆盖远端 60 号）。 |
| **调用时机** | 巡检每轮（`summary_only=true`，1 行）；人工复盘/班次收尾（全量+shift）。 |

> **落地注（2026-09-27 22:0x，t_d054ba36）**：聚合后端 + 一行摘要已先行落地为
> **`mas-triage.error_daily(date?, max_age_s=600, timeout=420, write=true)`**（只读，约 30-60s；
> 返回 `err_summary` + `counts` + 报告路径；不可达 ⇒ `ERR_SUMMARY=UNKNOWN:<原因>`）。
> ⚠️ worker/CLI 进程立即可见；**builtin 巡检面有工具面缓存**——需一次网关级刷新
> （`hermes gateway restart`，或 mas 的 mcp_servers reconcile）才见 `error_daily`（实测见
> `docs/audits/2026-09-27_maa_error_daily.md` §8）。
> 本文 §2.2 的 `mas_report` 全量版（`items[]`/`shift{}`）**尚未建**；班次块当前由
> `mas-dispatch.list_schedule`（含 `dispatch_alerts`）承担，两者不重叠。
> 巡检 job `52b88b0ffca0` 已接 `ERR_SUMMARY` 行（见 `docs/audits/2026-09-27_maa_error_daily.md`）。

### 2.3 `mas_idle_emulators` —— 空转实例收敛清单

| 项 | 内容 |
|---|---|
| **目的** | 班次后扫出"跑着、没有 MAA 在用、也不属于下一班/补跑复用"的 MuMu 实例 → 只出**建议关闭清单 + 建议命令**（等价式：`AUTO-MAS close` / `MuMuManager`），**不执行关闭**。 |
| **后端** | `tools/mas_idle_emulators.py`（t_c47b71c5）。 |
| **入参** | `aggressive`(bool=false，扩展口径：空闲即候选，代价=下班冷启)；`with_fg`(bool=false，附前台包；需与其它 adb 任务错开；≤6 台)；`timeout`(int=240)。 |
| **出参** | `{ok, generated_at, counts:{running, in_use, needed, candidates}, next_shift:{label, last_dispatch}, candidates:[{idx, title, adb, idle_s, scripts}], rows:[…含 rec（在用/可关候选/保留）], suggested_commands:[string], in_use_note:{}}`。 |
| **只读或写** | **只读**（不启动/不关闭/kill/不改配置；默认不碰 adb）。 |
| **失败语义** | ① REST/SSH 不可达 ⇒ `{ok:false, candidates:[], error}`（**不得**给出关闭建议）；② 实例→在用映射判不出来时**保守**（宁多留、不错关）；③ `--with-fg` 查询失败逐台降级"查询失败:<原因>"，不影响主清单。 |
| **重叠** | **现网无重叠**（auto-mas-monitor 的 10 个只读工具不含 emulator 状态面；`mas_snapshot` 是 MAAOrch 本机口径）。与 `mas_preflight` 的实例检查**互补**：preflight = 目标实例"能不能跑"，idle = 全机"该不该留"。 |
| **调用时机** | 班次收尾后（约 05:45 / 20:45）或人工"省资源"时；**不要**在批次运行中用 `aggressive`。 |
| **接口补丁（落地前）** | 目前"建议关闭命令"只在**文本模式**打印（`suggest_close()` 的产物未进 `--json`）⇒ 需要在 CLI 的 `--json` 输出里补 `suggested_commands[]`（或由 MCP 包装侧生成，见 §5.2）。 |

### 2.4 `mas_client_state` —— 客户端状态检测（停在哪 / 有没有挡路弹窗）

| 项 | 内容 |
|---|---|
| **目的** | 单台实例（或离线图）判定 `CLIENT_STATE=<正常\|正常:大厅\|正常:标题页\|模态:登录被取消\|模态:更新提示\|模态:公告\|模态:活动页\|模态:退出确认\|模态:账号管理残留\|模态:世界观光卡\|桌面\|未登录\|未知>` + `ACTION=<安全处置建议>`（归一化坐标）。 |
| **后端** | `tools/client_state_check.py`（t_65944d5b；adb screencap 或读图 + RapidOCR；**不点击**）。 |
| **入参** | `serial`(string，如 `127.0.0.1:7555`) / `image`(string，离线图) / `list`(string，批量清单文件) **至少给一个**；`jsonl`(bool=false)；`adb_path`(string，可选)。批量建议 ≤6（一次调用）。 |
| **出参** | 单台 `--json`：`{state, action, evidence:[关键词], source, size}`（**无 `ok` 字段**；`source` = 图片路径或 `adb:<serial>`）；批量 `--list --jsonl`：逐行 `{file, state, evidence, size}` + 末行 `# total=N counts={…}`（批量无 action 字段，包装侧需补）。 |
| **只读或写** | **只读**（只截图/读图 + OCR；不点击、不按键、不改配置）。 |
| **失败语义** | ① adb 不可用/设备 offline ⇒ `ok=false, state="未知"` + reason（**不猜测**）；② OCR 不可用 ⇒ `state="未知"`（fail-closed，**不得**返回"正常"）；③ 未匹配已知画面 ⇒ `未知` + 建议人工看图。 |
| **重叠** | preflight 第 4 项（目标实例"当前画面"一行判定）。去重：**发车场景看 preflight**；**非发车场景 / 需要处置建议 / 单实例深查**用 client_state。 |
| **调用时机** | 发现失败/卡死时对可疑实例深查（今晚的官-15、MAA_8 就是先例）；班次后对"停在桌面/模态"的实例抽样。 |

### 2.5 `mas_identity_trail` —— 每轮身份留痕（切换/登录/UID 核对）

| 项 | 内容 |
|---|---|
| **目的** | 把"串号"变成可回归检测：`gui.log` 的「账号切换 -->> <掩码>」行 + 每轮大厅/标题帧 OCR（`ID:<UID>`/玩家掩码）对照"期望账号 vs 实测账号"，任何一轮错配 = 红色。 |
| **后端** | `tools/mas_identity_trace.py`（t_6f159f59）。**MCP 只暴露 `report`（离线对照）与可选 `ocr`（对已有帧）**；`watch`（长驻抓帧）不进 MCP 面（改用 cron/终端跑）。 |
| **入参** | `since`/`until`(日期或时间)；`targets`(string，`脚本=MAA目录:实例` 分号分隔，默认两个多号脚本)；`ocr_index`(string，可选，帧 OCR 索引)；`frames_manifest`(string，可选)；`out`(string，可选，报告落盘)；`json`(string，**注意**：CLI 的 `--json` 是**输出文件路径**不是开关，包装侧需读回该文件)；`timeout`(int=420)。 |
| **出参** | `{ok, scripts:{<脚本>:{maa_dir, users:[{name(掩码), …}], rounds:[{user, user_name(掩码), start, done, app_completed, switch, observed_mask, observed_uid, evidence, verdict, color}]}}, red_rounds(n), orphans:[…], generated_at}`。 |
| **只读或写** | **只读**（读远端日志/帧；帧只落本机 `runtime/identity_trace/`；不写远端、不发车、不改配置）。 |
| **失败语义** | ① 日志不可达 ⇒ `ok=false` + reason；② 无帧/无 OCR 索引 ⇒ `verdict="uncertain"`（**不得**判绿色）——红/绿/灰三态必须显式区分；③ `red_rounds>0` 仍是 `ok=true`（检测成功），由调用方按红色数处置（CLI exit 1 对应"有红"）。 |
| **重叠** | ① `switch_gate`：**配置层**"应该切谁"（注入校验）vs identity_trail **运行层**"实际切/登录成谁"（行为校验）——**互补成对**，验收与回归时一起用；② `triage_error`（某轮失败定因）；③ `mas_report`（日级聚合）。 |
| **调用时机** | 多号脚本班次后、`SWITCH_GATE=VIOLATION` 或疑似串号时、补丁/上线后的验收。**不要**每轮跑（成本=ssh 拉日志+OCR）。 |

---

## 3. 载体设计：新 server `mas-observe`（推荐）

### 3.1 为什么是"一个只读 server"而不是"塞进现有的"

| 方案 | 形态 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A（推荐）** | 新 stdio server `mas-observe`，5 个只读工具 | 一处新增/一处回滚（`platform_toolsets.cron` 加/删一行）；只读姿态单一可审（代码里无任何写路径）；与写类 server 物理隔离 | 多一个 server 条目 | **推荐** |
| B | 拆进现有：`preflight`→mas-dispatch、`report`→mas-triage、其余 3 个→mas-triage | 复用现有 server | 三处配置改动；mas-dispatch/mas-triage 从"窄写/窄读"变宽；回滚面变大 | 备选 |
| C | 不进 MCP，只保留 CLI（cron/终端跑） | 零新面 | 巡检 agent（无 terminal）永远看不到这 5 类信息 | 不可行 |

### 3.2 server 草案（落地前冻结）

```yaml
  # 只读运维观察面：5 个工具全部包只读 CLI；无任何写路径（不发车/不动实例/不改配置）。
  mas-observe:
    command: C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe
    args:
      - D:\1-2\MAAOrch\tools\mas_observe_mcp.py
    env:
      PYTHONUTF8: '1'
      PYTHONUNBUFFERED: '1'
    # 重型工具（report/identity）建议给足调用超时（MCP 默认 300s）：
    timeout: 900
    enabled: true
```

包装契约（`tools/mas_observe_mcp.py`）：

1. 每个工具 = 一次 `subprocess.run([python312, <CLI>, …], stdin=subprocess.DEVNULL,
   capture_output=True, text=True, timeout=<工具上限>)`；优先 `--json`；解析失败 ⇒
   `{ok:false, error:{kind:"parse_error", raw_head}}`。
2. **`stdin=DEVNULL` 是硬要求**（教训在案：ssh 继承 MCP stdin 管道会挂死到超时，实测 240s→1.5s；
   `mas_error_triage.py` 的挂死即此病，见 t_ae836e86）。
3. 统一错误鞘 `{ok:false, error:{kind: upstream_unreachable|timeout|parse_error|unknown, detail}}`；
   **不重试**（幂等；由巡检下一跳重试）。
4. 每工具显式超时：preflight 120s(skip)/300s(full)、report 420s、idle 240s、client_state 120s、
   identity 420s；server 侧 `timeout: 900` 兜底。
5. 工具名与本文一致：`mas_preflight` / `mas_report` / `mas_idle_emulators` / `mas_client_state` /
   `mas_identity_trail`（名字即契约，便于巡检提示词按名书写）。
6. `tools.include` 可选作第二层（阶段化放行用，见 §4.2）。

---

## 4. `platform_toolsets.cron` 的建议变更（**先不改**）

### 4.1 建议 diff（`profiles/mas/config.yaml`，119-150 行区块）

```diff
 platform_toolsets:
   cron:
     - auto-mas-monitor
     - maaorch-daily
     - mas-dispatch
     - mas-triage
+    - mas-observe        # 新增：只读观察面（mas_preflight / mas_report /
+                         # mas_idle_emulators / mas_client_state / mas_identity_trail）
```

语义依据（`hermes_cli/tools_config.py:692-704` `_merge_mcp_servers`）：平台列表里**点名一个
MCP server 即构成 allowlist**；**一个都不点名**时会把**所有已启用 MCP**（含全量 118 工具）
交给该平台 —— 所以新增只能"加名字"，不能"删空"。巡检 job（`52b88b0ffca0`）的
`enabled_toolsets=null` ⇒ 走本平台列表（`cron/scheduler.py:469` `_merge_mcp_into_per_job_toolsets`：
per-job 列表存在时会 merge 全部已启用 MCP server，故**不要**给该 job 单独设列表，免得绕开
这里的收窄）。

### 4.2 分阶段（推荐）

- **阶段 1（先只读摘要）**：`mas-observe` 加进 cron + `tools.include: [mas_preflight, mas_report]`；
  巡检提示词加两行（见 §4.3）。观察 1-2 个班次（成本/时延/误报）。
- **阶段 2（放开深查）**：`tools.include` 增补 `mas_idle_emulators, mas_client_state,
  mas_identity_trail`（或整段删掉 include = 5 个全放）。
- **回滚**：删 `platform_toolsets.cron` 里的 `- mas-observe` 一行 + `hermes gateway restart`
  （30 秒回到现状）；进程级回滚再删 `mcp_servers.mas-observe` 段。

### 4.3 配套的巡检提示词/预算调整（同一批落地）

- 现上限 **15 次工具调用/轮**（`52b88b0ffca0` prompt）。建议上限抬到 **18**，并写明用新工具的硬规则：
  1. 每轮必做：`list_schedule` → `mas_report(summary_only=true)`（替代"只有失败名单没有摘要"）；
  2. `due_not_sent` 非空 或 准备 `dispatch_due(dry_run=False)` 时：`mas_preflight(skip_screen=true)`
     一次，`PREFLIGHT≠GO` ⇒ **不发车**、只告警（fail-closed）；
  3. 第四步定因优先用 `mas_report` 的 `items` 判「仅资源类」，再决定是否深挖 `triage_error ≤2`；
  4. `mas_client_state`：仅对报告里失败的实例 ≤1 次；
  5. `mas_idle_emulators`：仅班次收尾轮（约 05:45/20:45）跑 1 次，且只报告清单、**不关闭**；
  6. `mas_identity_trail`：仅当 `SWITCH_GATE=VIOLATION` 或出现多号脚本失败时跑 1 次（阶段 2 起）。
- 报告样式建议追加一行：`OBS=<preflight 结论> | IDLE=<候选台数> | CLIENT=<异常台数>`。

---

## 5. 落地前提（阻塞项，先修再做 MCP 面）

### 5.1 `stdin=DEVNULL` 补齐（现状实测，2026-09-27 21:2x 源码扫描）

| 文件 | ssh/adb 子进程 | 现状 | 需要 |
|---|---|---|---|
| `tools/mas_preflight.py` | 2 处 | ✅ 已带 `stdin=subprocess.DEVNULL`（L116/121） | — |
| `tools/maa_error_daily.py` | 1 处（`ssh_ps`，L389-406） | ✅ 安全（**机制不同**：`input=payload` 喂脚本占用 stdin，不是 `stdin=DEVNULL`；L404 有注释） | — |
| `tools/mas_switch_gate.py` | 1 处 | ✅ 已带（L172-174） | — |
| `tools/mas_error_triage.py` | 1 处（`ssh_lines`，L82） | ❌ **未带 ⇒ MCP stdio 下挂死**（巡检 `triage_failed` 180s 超时的根因） | 修（= 卡 t_ae836e86） |
| `tools/mas_idle_emulators.py` | 3 处（L146 上传/ L153 执行 / L455 dumpsys） | ❌ 未带（L146 因 `input=` 传脚本不受影响；L153/L455 有风险） | 修 |
| `tools/mas_identity_trace.py` | 3 处（L109/ L115/ L126 ssh 助手） | ❌ 未带 | 修 |
| `tools/client_state_check.py` | 1 处（adb screencap，L178） | ❌ 未带（adb 一般不吃 stdin，仍建议防御性加） | 修 |

口径说明：包装层给 CLI 进程 `stdin=DEVNULL` 能**隔断** MCP 管道（CLI 的 ssh 继承的是 CLI 的 stdin），
但这只覆盖"MCP 包装调用"这一条路径；CLI 还会被 cron 脚本/终端**直接**调用 ⇒ **两层都做**。

### 5.2 接口冻结与补丁（这 5 个 CLI 今天仍在并行任务里演进）

- 冻结项：CLI 参数名、`--json` 输出 schema（键名/类型/枚举值）、退出码语义（0/1/2）。
  建议冻结方式：给每个 CLI 加一条 `--selftest` 契约断言（多数已有），MCP 包装的 `--check`
  跑一遍全部 5 个 CLI 的 `--json` 解析。
- 已知小补丁：`mas_idle_emulators --json` 补 `suggested_commands[]`（§2.3）；
  `mas_identity_trace report --json` 是**文件路径**参数（包装侧读回）；
  `mas_preflight --json` 无 `PREFLIGHT=` 行（以字段为准）；`mas_client_state` 批量输出含
  `# total=` 汇总行（解析时跳过 `#` 行）。
- 文档漂移（**已消解**，21:3x 复核）：`docs/audits/2026-09-27_client_state_detection.md`（21:21 落盘）与
  `docs/audits/2026-09-27_identity_trace.md`（21:29 落盘）均已存在，`client_state_check.py` 的引言引用可解析。

### 5.3 其它红线

- **输出脱敏**：账号一律掩码（沿用 `gui.log` 掩码 / 首尾留字符口径）；`idle/report` 已带
  `SENSITIVE_KEYS` 过滤，包装侧**不得**回传原始 stdout（含口令类键值）。
- **adb/截图错峰**：`client_state` 与 `idle --with-fg` 与跑批抢 adb —— 实测"边跑边截"无碍，
  但建议错峰（班次外/收尾后），`--with-fg` 保持"默认关"。
- **工具面刷新手续**：任何 `config.yaml` / server 改动 ⇒ `hermes gateway restart`（否则
  自动（builtin）路径拿不到新工具面，见 `HERMES_MAS_HANDOVER.md` §12.1 实测）。
- **写入红线不变**：本轮新增的 5 个工具**不涉及任何写**；巡检的写面维持现状（`dispatch_due` /
  `dispatch_queue` / `mas_rerun`，均默认预演且有闸门）。

---

## 6. 验收计划（落地时按此逐条过）

1. **CLI 层**：每个工具 `--check` / `--selftest` 通过；`--json` 可解析；掩码/脱敏抽查。
2. **server 层**：`hermes -p mas mcp test mas-observe`（工具数=5、名字逐一核对）；
   `mas_observe_mcp.py --check`（内部逐 CLI 探测，全只读，零写请求）。
3. **影子轮**：巡检下一轮在"不发车"前提下调用 `mas_report(summary_only=true)` 与
   `mas_preflight(skip_screen=true)` 各 1 次；比对输出与人工口径（今日数据：24/24 已发、
   失败账号、告警台账空）。
4. **回归样例**（用今晚真实案例）：
   - `mas_report`：应能报出 09-27 的错误条目（MAA_7/8/9 的 StartUp 失败、官-6 无 history 的
     孤儿态在 app.log 侧可见）；
   - `mas_client_state`：对官-15 悬置实例 ⇒ `CLIENT_STATE≠正常` + ACTION；
   - `mas_identity_trail`：对官-1-1-7 的 09-27 轮次 ⇒ 对照表无红（与 `t_56ea4c40` 的 E2E 结论一致）。
5. **成本记录**：每工具真实时延/输出体量记入 `docs/audits/`，作为巡检预算（§4.3）的校准输入。

---

## 7. 附录：取证清单

- **配置/源码**：`profiles/mas/config.yaml`（`platform_toolsets` 119-150 行；`mcp_servers` 231-307 行）；
  `tools/mas_scrub_proxy.py`（只读 allowlist 124-135 行）；`tools/mas_maaorch_daily_proxy.py`
  （`EXPOSED_TOOLS` 143-150 行）；`tools/mas_dispatch_mcp.py` / `mas_triage_mcp.py` / `mas_switch_gate.py` /
  `mas_preflight.py` / `maa_error_daily.py` / `mas_idle_emulators.py` / `client_state_check.py` /
  `mas_identity_trace.py`（各文件头 docstring 即接口契约）。
- **Hermes 侧语义**：`hermes-agent/hermes_cli/tools_config.py:692-704`（`_merge_mcp_servers`）、
  `hermes-agent/cron/scheduler.py:469`（`_merge_mcp_into_per_job_toolsets`）、
  `hermes-agent/tools/mcp_tool_registration.py:211-225`（`tools.include` 白名单）。
- **实测**：`python tools/mas_dispatch_mcp.py --check`（4 工具 / sent_today=24）；
  `python tools/mas_preflight.py --queue 晚班定时1 --skip-screen`（29s / PREFLIGHT=GO）；
  `runtime/mas_dispatch_alerts.json`（active=[]）；`runtime/mas_dispatch_log.jsonl`（21:18-21:20 test 记账）。
- **今日报告**：`docs/audits/2026-09-27_mas_failure_taxonomy.md`（§4 巡检改进 8 条 = 本文件 §2.2/§4.3 的直接依据）、
  `2026-09-27_mas_preflight_wiring.md`（接线三方案）、`2026-09-27_maa_errors_triage.md`（弹窗/孤儿态案例）、
  `2026-09-27_non_official_no_switch.md`（22 号永不切号）、`HERMES_MAS_HANDOVER.md` §12.1（网关重启/超时口径）。

> 本文为**草案**：5 个工具的入参/出参以落地时冻结的 CLI 契约为准；`platform_toolsets.cron` 一行未改。

---

## 8. 独立复核记录（t_87409df9，2026-09-27 21:2x–21:3x）

本文件主笔为 t_cf2dc452（同一交付的另一张派发卡，重名任务）；本节是 t_87409df9 的**独立复核**：
逐项复跑/复读源码，结论与差异如下（全部只读；未改配置、未发车、未动实例）。

### 8.1 复跑实测（本轮证据）

| 命令（Python312） | 结果 |
|---|---|
| `mas_dispatch_mcp.py --check` | `OK`；tools=`list_schedule,dispatch_due,dispatch_queue,switch_gate`；reachable=True；sent_today=24；due_not_sent=[]；late=[]；drift=[]；alerts=0（21:29，与 §1.1/§7 一致） |
| `mas_preflight.py --selftest` | `OK（0 失败）`，20 项离线断言全 PASS（21:30） |
| `maa_error_daily.py --selftest` | `OK`（21:30） |
| `client_state_check.py --image runtime/preflight_shots/20260927_213005_idx13_MAA_6.png --json` | exit 0；`{state:"模态:账号管理残留", action:"按 BACK…", evidence:["上次登录"], source:…, size:[1600,900]}` —— 工具可用、只读；schema 以实测为准（修正见 §8.2） |
| `mas_idle_emulators.py --json` | exit 0（约 90s）；`{ok:true, next_shift:{label:"早班(次日)",last_dispatch:"2026-09-28 05:33"}, counts:{running:14,in_use:2,needed:12,candidates:0}, candidates:[], rows:[{idx,pid,title,adb,scripts,dirs,in_use,needed,needed_by,idle_s,idle_src,last_activity}…]}`；**`suggested_commands[]` 确实不在 JSON**（§2.3 补丁项坐实）；rows 实际键比 §2.3 多 `pid/needed/needed_by/idle_src/last_activity` |

**工具面计数三源一致**：设计/注册值 = 10+6+4+1 = **21**（`profiles/mas/cache/mcp_schema_cache.json` 21:17 版，
及本复核会话自身工具面均含 `mcp__mas_dispatch__switch_gate`）；21:05 巡检轮的 20（缺 `switch_gate`）= 网关进程旧缓存，
非配置错误（与 §1.1 结论一致，重启/新会话即追平）。

### 8.2 对本文的修正（三处，已就地改）

1. §2.4 出参：单台 JSON **无 `ok` 字段**，键是 `source`（非 `serial|file`）；批量 `--jsonl` 行为 `{file,state,evidence,size}`、**无 action**，末尾另有 `# total=…` 行。
2. §5.1 `maa_error_daily.py` 行：安全，但机制是 `input=payload` 喂脚本（占用 stdin），**不是** `stdin=DEVNULL`（grep 实测 DEVNULL=0）。
3. §5.2 文档漂移项：两份 audit 均已落盘（21:21 / 21:29），该条**已消解**。

### 8.3 补充建议（一条）

把"工具面计数"做成断言：巡检报告已含 `MCP_API_TOOL_COUNT` —— 落地 `mas-observe` 后预期 **26**；
若仍报 21/20 ⇒ 网关未重启或 discovery 缓存未刷新（本轮"20 vs 21"即首条回归样例）。

## 9. 落地记录（2026-09-28，t_6cfe7865）

- **已接线**：`mas-observe`（5 工具）+ `mas-triage.error_daily` 已进网关工具面并随每轮巡检调用；
  工具面总数 **27**（monitor10 / daily6 / dispatch4 / observe5 / triage2），01:34 巡检实跑 `ISOLATION=PASS`。
- **§8.3 计数断言修正**：落地后预期应为 **27**（原写 26 = 未计 triage 第二工具；auto-mas-monitor 上游掉线时退为 17，属上游 flappy，非接线问题）。
- **强制重连**：reconciler 只处理增删、不刷新已连接 server 的 schema；改工具 schema/代码后须
  `hermes -p mas config set mcp_servers.<X>.enabled false → true`（~1min 生效）。
- env 自愈 / identity 性能两处修复与完整验证矩阵见 `docs/audits/2026-09-28_mas_observe_wiring_t_6cfe7865.md`。
- 本 §4.2 分阶段方案（阶段 1 只放开 2 工具）**未采用**：5 工具与第 3-5 步一次性放开，
  实测预算 13/18 次调用，走观察期。

---

## 10. 落地记录（2026-09-28，t_1aa55dac：按需发车面 `mas-ondemand`）

补齐本文件 §0 指出的第二类缺口：此前 5 个 server 只会"按班次补位 + 只读观察"，缺**脚本级/单号级按需发车**
与**停任务 / 查在跑**。本卡新增第 6 个 server，工具面 27 → **33**。

- **server**：`mas-ondemand` = stdio `tools/mas_ondemand_mcp.py`，6 工具，**写动作默认 `apply=false`（预演）**：
  - 只读 2：`list_running`（Hermes 班次账 + 本 server 按需账 + 远端现场，三源合并）/ `ondemand_preflight`（复用 `tools/mas_preflight.py`）
  - 写 4：`dispatch_queue`（队列名）/ `dispatch_script`（脚本名）/ `dispatch_account`（账号名 → 内部建 `临时-按需-*` 单脚本项队列）/ `stop_task`（只认 `task_uid`，拒 `ALL` 与队列 uid）
- **闸门复用而非重写**：REST 可达 fail-closed、单飞锁（与 cron 调度器共用 `runtime/mas_dispatch.lock`）、
  busy 窗口资源冲突、守卫窗 05:00–05:40 / 19:20–20:20、`NO_GO` 口令，全部走 `tools/mas_dispatch.py` 既有实现；
  本 server 自己只落两处状态：审计 `runtime/mas_ondemand_log.jsonl`（含被拒调用）与按需台账
  `runtime/mas_ondemand_mcp_state.json`（busy 窗口默认 180min，`MAAORCH_ONDEMAND_HOLD_MIN` 可覆盖）。
  **注意**：CLI 版 `tools/mas_ondemand.py`（卡 t_78c0c0d0）用的是 `runtime/mas_ondemand_state.json` +
  `runtime/mas_ondemand.lock`——两边 schema 不同，**刻意错开文件名**（同名会互相覆盖）；统一账本/锁见后续卡。
  → **t_4ddb3728 已统一**：两端同一份 `runtime/mas_ondemand_ledger.json`（`tools/mas_ondemand_ledger.py`，
  旧账首次读入自动合并）+ 同一把 `runtime/mas_dispatch.lock`；真动作事件另写
  `runtime/mas_dispatch_log.jsonl`（`source:"ondemand"`, `src:"cli"|"mcp"`），`mas_ondemand_log.jsonl`
  收全量调用行（含 dry-run/被拒）。细节见 `docs/MAS_ONDEMAND.md` §5/§10。
- **配置**：`mcp_servers.mas-ondemand`（`tools.include` 收窄到上述 6 名 = 注册期硬白名单）+
  `platform_toolsets.cron` 追加 `- mas-ondemand`。改前备份 `profiles/mas/config.yaml.bak-ondemand-20260928-055725`；
  diff = +17 行（server 块）+ 1 行（cron 列表项），既有注释零丢失。
- **计数断言口径**：§8.3 / §9 的 27 更新为 **33**（monitor10 / daily6 / dispatch4 / observe5 / triage2 / ondemand6）。
  新 server 属"增"，reconciler 处理增删；若网关内 `builtin` 运行仍报 ≤27，按 §12.1 重启网关后复核。
- **实测（全部只读 / dry-run）**：
  - `mas_ondemand_mcp.py --selftest` → `OK (15/15)`；`--check` → `OK`（`reachable=true / queues=39 / scripts=54`，
    `dry-run dispatch_queue(日常定时1)={"ok":true,"dry_run":true,"refused":null,…}`）
  - `hermes mcp test mas-ondemand` → `✓ Connected (2398ms)` / `✓ Tools discovered: 6`
  - `tests/ondemand_mcp_probe.py`（真 stdio MCP 协议，13 项断言）→ `PROBE OK (13/13)`：含
    `dispatch_script` 在跑冲突守护（`refused=conflict_inflight`）、`stop_task` 拒 `ALL`/队列 uid、
    `ondemand_preflight` 对在跑槽位给 `NO_GO`；`pytest tests/` 344 passed。
- **回滚**：`cp config.yaml.bak-ondemand-20260928-055725 config.yaml`（或 `hermes -p mas config set` 改回
  cron 5 项 / 删 `mcp_servers.mas-ondemand`）；AUTO-MAS 侧无需任何改动。

## 11. 落地记录（2026-09-28，t_cf3946dc：补丁持久化闸门 `patch_guard`）

补「AUTO-MAS 升级/重装**静默覆盖**我们的补丁」这类低频但致命的发现线。`mas-dispatch` 只读工具 4 → 5，
工具面 33 → **34**（monitor10 / daily6 / dispatch**5** / observe5 / triage2 / ondemand6）。

- **工具**（`tools/mas_patch_guard.py`，634 行，只读为主）：`--check` / `--json` / `--selftest` /
  `--reapply <name>`（**默认 dry-run**）/ `--capture [--apply]`。清单 `runtime/patches/manifest.json`
  （4 条：AutoProxy 切号 v2、理智药 ×2、mumu 判活关闭；含特征断言 / 备份 sha256 / 回滚 / 重放方式），
  整文件快照 `runtime/patches/payloads/*.patched`。判据 fail-closed；末行 `PATCH_GUARD=OK|MISSING:<n>`
  （另有 WARN=哈希漂移、STALE=侧车未重载，均不改退出码）。
- **MCP**：`mas_dispatch_mcp.patch_guard()`（复用 run_guard；裁剪摘要 + MISSING 时附重放 hint）；`--check` 白名单 5 名。
- **巡检**：job `52b88b0ffca0` 第三步「现场六检」第 2 项 + 主表 `PATCH_GUARD=…` 行
  （在 t_634e7685 合并为七检 / 13 行后原样保留）。
- **实测**：`--check` → `PATCH_GUARD=OK`（4/4、漂移 0、侧车加载=OK）；沙箱自测 18/18（改坏 1 字节 ⇒ `MISSING:2`、
  dry-run 零写入、`--apply` 回读一致、生产只读）；`mas_dispatch_mcp.py --check` → `OK | tools=…,switch_gate,patch_guard`；
  `hermes mcp test mas-dispatch` → `✓ Connected` / `✓ Tools discovered: 5`（含 patch_guard）。
- **刷新口径（修订 §8.3）**：改工具面后「关→开」`mcp_servers.<srv>.enabled` 即刷新（10:18:07 off → 10:19:50 on，
  config 终态与备份逐字节一致），不必重启网关；刷新前一轮巡检实报 `patch_guard 未接线`，刷新后一轮（10:57 builtin）
  工具面含 `mas-dispatch 5`、实调 `patch_guard()` 返回 `OK`（4/4、loaded=OK）—— 端到端闭环。
- **坑（取证时务记）**：`hermes cron run` 是**阻塞**的，且该轮 execution 的 claim 挂在调用方进程上 ——
  调用方被超时杀 ⇒ 该轮记 `unknown`（`Scheduler restarted after this execution's owner exited…`；当日两例，无副作用）。
  要端到端证据就等 builtin 定时轮。
- **回滚**：删 `tools/mas_patch_guard*.py` 与 `runtime/patches/`；`cp tools/mas_dispatch_mcp.py.bak-patchguard-20260928-094943`
  覆盖；prompt 只删补丁段（**别整文件回拷**，会连带撤掉并行任务的合并）。审计全文 `docs/audits/2026-09-28_patch_guard.md`。

## 12. 落地记录（2026-09-28，t_403cd83b：`event_calendar` 到点提醒接线）

把 `tools/event_calendar.py`（周期事件日历，t_5f2e36b2 交付）的 `UPCOMING=` 接进两个消费面 + 巡检提示词。

- **mas-observe**：新增第 7 个只读工具 `event_calendar(days=14, window_hours=48, timeout=60)` → subprocess
  `tools/event_calendar.py --check`（stdin=DEVNULL、超时 60s、零写盘），返回 `{ok, event_cal, upcoming, event_advice, exit_code, elapsed_s}`；
  数据不可用 ⇒ `ok=false` + `upcoming="UNKNOWN:<原因>"`（fail-closed，**不许当「无事件」用**）。
  实测：`--check` → `tools(7)=…,event_calendar` + `readonly_assert=PASS`；`hermes mcp test mas-observe` → `✓ Tools discovered: 7`。
- **班次日报**：`tools/mas_shift_report.py` 第 9 节「到点提醒（周期事件）」原样落三行 + 附源表行；失败不阻塞日报
  （缺工具/超时/无输出 ⇒ `UPCOMING=UNKNOWN:<原因>`）。`--selftest` 25→31 项、`--check` 子工具 4→5。
- **巡检**：job `52b88b0ffca0` 主表 13 → **14 行**（+`UPCOMING=…`），第三步「现场三行」→「现场四行」，预算 20→21，
  同工具上限 +`event_calendar ≤1`；未接线文案固定 `UNKNOWN:工具面未提供（mas-observe 未接线 event_calendar）`。
- **刷新**：按 §9 口径「关→开」`mcp_servers.mas-observe.enabled`（config 终态哈希与改前一致）；builtin 巡检轮实证见审计。
- **计数**：observe 5→7（本卡含依序合入的 `mas_interrupt_guard`）⇒ 工具面 34 → **36**；
  `docs/GLOSSARY.md` §206/§236 的「33」是早间快照，以本节及各「落地记录」为准。
- 审计全文（含 fail-closed 演练、报告/hashes）：`docs/audits/2026-09-28_event_calendar_wiring.{txt,json}`；
  接线状态表（生活文档）：`docs/EVENT_CALENDAR.md` §4。

### 12.1 集成收口（同日 11:1x，lead 11:10 指令：本卡任 MCP + prompt 集成 owner）

- 并行卡先后合入 `mas_kanban_health`（t_4cd871de / t_67915887，`KANBAN_HEALTH=`）与 `audit_waterline`（t_537fbff2，`AUDIT_STATUS=`）⇒
  mas-observe **9 工具**；本卡在其文件写入后核对 `--check`（`tools(9)=…`、readonly_assert=PASS）与**直调真值**，随后接管收口。
- prompt 收口为 **v4**（12,008 B / sha256 `9b41eaa3…`）：主表 **16 行**（新增 `KANBAN_HEALTH=`）、第三步 **现场九检**（第 8/9 项 = audit_waterline / mas_kanban_health）、
  预算 22 → **23**、同工具上限 +`kanban_health ≤1`；`cron edit` 后回读**逐字节比对 MATCH=True**（回滚备份 `jobs.json.bak-t403-prompt2-111624`）。
- 最终刷新：「关→开」`mcp_servers.mas-observe.enabled`（config.yaml sha256 `86ef0322…` 前后一致）→ `hermes mcp test mas-observe`
  ⇒ `✓ Connected` / `✓ Tools discovered: 9`（含 event_calendar / mas_kanban_health / audit_waterline）。
- 权责：**MCP 与 prompt 只由本卡（集成 owner）写**；t_4cd871de / t_537fbff2 只交 CLI/探针/演练（卡面 comment 205/206 已留言）。
