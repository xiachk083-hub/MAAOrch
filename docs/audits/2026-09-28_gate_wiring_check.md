# 核验：闸门/判据已进巡检（SWITCH_GATE / CLIENT_STATE / ERR_SUMMARY / DISPATCH_ALERT / IDENTITY）

- 任务：t_cd7f09f1（mas）· 2026-09-28 00:18–00:5x · **只读**（未改配置、未改提示词、未发车；唯一写入 = 本审计文档 + 同目录一份闸门 JSON 证据）
- 交付要求：取最近 3 轮巡检输出 → 逐项查 5 个标记（出现？格式合规？缺的定位原因）→ SWITCH_GATE 与现场 gui.new.json 抽查（≥3 槽）→ 最小修复清单（先出 diff，不代改）
- 关联卡：t_6cfe7865（mas-observe 接线，00:31 落盘）、t_5c23c432（闸门进巡检）、t_d054ba36（error_daily）、t_c8b36f5e（mas-wiki，blocked）、审计 2026-09-27_switch_gate_two_violations_rootcause.md（MAA_10/12 定因）

---

## 0. 结论摘要

0. **修复后首轮实测已拿到（报告 2026-09-28 00:52:32）**：CLIENT_STATE / ERR_SUMMARY 已从「合规占位」转为**实调真值**——`ERR_SUMMARY=102 条，Top=未知`（与 `runtime/reports/maa_errors_2026-09-27.md` 逐字吻合，独立交叉验证 ✓）、`CLIENT_STATE=未知×3、桌面×1（异常 4）`；IDENTITY/POWER 已实调但**工具侧失败**（fail-closed 正常降级）。**「写了工具但没接上」在 00:31/00:32 两次落盘后已不成立**；剩两个工具级缺陷（F6）+ 提示词文案（F3）。
1. **5 个标记在最近各轮输出中全部出现、格式全部合乎提示词约定**（含规定的 UNKNOWN/UNAVAILABLE 回退串）——「写在提示词里」这一层：通过。
2. 但修复前其中 **3 个标记是「合规占位」而不是真值**，根因两类（审计期间均已被修复/被证实修复生效）：
   - **CLIENT_STATE / IDENTITY（+ POWER）：mas-observe 未接线**（server 脚本已写好且自检通过，但 profile 未注册、cron 允许表未列入）→ 兄弟卡 t_6cfe7865 于 **00:31** 落盘接线；**00:33 起的巡检轮实测已见 5 个 mas-observe 工具**（本审计实证）。
   - **ERR_SUMMARY：mas-triage 的「陈旧注册」**（server 21:57 才加 error_daily；常驻进程自 09-26 21:08 起未重连，进程内工具表停留在 1 个 triage_error）→ 00:32 进程刷新后 **实测已见 2 个工具**（triage_error + error_daily），00:45 实跑返回 199.5s 的真数据。
3. **SWITCH_GATE / DISPATCH_ALERT 是全链路真值**：闸门 4 槽独立抽查 4/4 吻合（VIOLATION:MAA_10,MAA_12）；告警台账 active=[] 与 `DISPATCH_ALERT=无` 一致。
4. 遗留小修（均给 diff，不代改）：① mas-observe 缺 `timeout: 900`、mas-triage 建议补 `timeout: 480`（error_daily 实测 199.5s，逼近 MCP 默认 300s）；② 提示词「设计面」计数与回退文案要随接线更新；③ mas-wiki server 未建（已有卡 t_c8b36f5e）；④ 两个 observe 工具级缺陷（identity_trail 远端拉日志无超时护栏；idle_emulators SSH 上传 WinError 2——后者 t_6cfe7865 已于 00:56 修源码，需再刷一次进程生效）。
5. 另有 1 条观察：00:32:52 与 00:37:23 两个巡检轮同时在飞（并发双跑）——若为人工触发可忽略，建议确认触发源。

---

## 1. 方法与环境快照

| 项 | 值 |
|---|---|
| 巡检 job | `mas-readonly-inspection` id=52b88b0ffca0（every 30m；next_run_at 01:07 附近） |
| 输出目录 | `C:/Users/xiach/AppData/Local/hermes/profiles/mas/cron/output/52b88b0ffca0/` |
| 本轮取帧 | 最近 3 轮输出（22:36:35 / 23:03:05 / 23:43:14）+ 其后两轮邻帧（00:30:07 完成；00:33:59、00:38:29 在飞） |
| 提示词版本 | jobs.json 当前版（4405 字符；与 23:43 轮逐字节一致；报告格式「先 11 行主表」自 23:43 轮起，22:36/23:03 轮为「10 行」旧格式） |
| 工具面真值来源 | ①巡检轮自述（会话转录，state.db）②服务端 `tools/list` 直探（JSON-RPC）③Hermes 日志 `MCP server '<x>' (stdio): registered N tool(s)` ④profile config.yaml / cron jobs.json |
| 一致性抽查来源 | 本地实跑 `tools/mas_switch_gate.py`（REST+ssh）＋ 独立 ssh 直读 4 槽 `gui.new.json` 原始字段（不经闸门代码） |

**巡检提示词约定（当前版）**：

```
SWITCH_GATE=OK | VIOLATION:<槽位列表> | UNKNOWN:<原因>
CLIENT_STATE=<正常|模态:xx|桌面>          （未接线 ⇒ UNKNOWN:<原因>）
ERR_SUMMARY=<n> 条 Top=<类型> | UNKNOWN:… | UNAVAILABLE:工具未加载
DISPATCH_ALERT=<队列:原因，顿号分隔> | 无
IDENTITY=<一致|不一致>                    （未接线 ⇒ UNKNOWN:<原因>）
（相邻）POWER=<空转实例数> 建议=<…>；WIKI_GOV=UNVERIFIED=n STALE=n ORPHAN=n DRIFT=n
```

---

## 2. 逐项核验表（最近 3 轮 + 邻轮）

| 标记 | 22:36:35 | 23:03:05 | 23:43:14 | 邻轮 00:30:07 | 判定 |
|---|---|---|---|---|---|
| `MCP_API_TOOL_COUNT` | `21 | ISOLATION=PASS` | 同 | 同 | 同 | ✅ 格式合规；数值当时 = 10+6+4+1 |
| `SWITCH_GATE` | `VIOLATION:MAA_10,MAA_12` | 同 | 同 | 同 | ✅ **真值**（见 §3.1 / §4） |
| `CLIENT_STATE` | `UNKNOWN:工具面未提供（mas-observe 未接线，见 docs/MCP_TOOL_ROADMAP.md §3-4）` | 同 | 同 | 同 | ⚠️ 占位合规；缺源（未接线，见 §3.2） |
| `ERR_SUMMARY` | `UNAVAILABLE:工具未加载` | 同 | 同 | 同 | ⚠️ 占位合规；缺源（陈旧注册，见 §3.3） |
| `DISPATCH_ALERT` | `无` | 同 | 同 | 同 | ✅ **真值**（台账 active=[]，见 §3.4） |
| `IDENTITY` | `UNKNOWN:工具面未提供（…）` | 同 | 同 | 同 | ⚠️ 占位合规；缺源（同 §3.2） |
| `POWER`（相邻） | `UNKNOWN:工具面未提供（…）（参考：在飞 MAA=2）` | 同 | 同 | 同（在飞=1） | ⚠️ 占位合规；缺源（同 §3.2） |
| `WIKI_GOV`（相邻） | （旧 10 行格式，无此行） | （无） | `UNKNOWN:工具面未提供（mas-wiki 未接线）` | 同 | ⚠️ 缺源（server 未建，见 §3.5） |
| 报告行数/响应格式 | 10 行主表 + 3 行证据 | 同 | 11 行主表 + 3 行证据 | 同 | ✅ 与各轮提示词一致 |
| 证据行自述的工具面 | `工具面 21/22：error_daily 未加载、mas-observe×3（未接线）…` | `缺：error_daily、mas-observe×3、mas_preflight` | `mas-triage×1[triage_error]；未接线：error_daily、mas-observe×3…` | `监控10+日6+调4+定因1（triage 实见1/设计2）` | ✅ 自述与实际工具面一致（缺陷在于工具面本身） |

小结：**没有「标记缺失」或「格式错乱」**；三处 ⚠️ 是「数据源没接上/陈旧」，且巡检按 fail-closed 规定写了回退串——这是正确的降级行为，不是巡检 bug。

### 2b. 修复后首轮实测（2026-09-28 00:52:32，post-t_6cfe7865 接线后首个完成轮）

| 标记 | 实测值（原样摘录） | 判定 |
|---|---|---|
| MCP_API_TOOL_COUNT | `17 | ISOLATION=PASS`，可见面=maaorch-daily:6 + mas-dispatch:4 + **mas-observe:5** + **mas-triage:2**；并如实报「设计面 auto-mas-monitor:10 本轮未见（观察面由 mas-observe:5 承接）」 | ✅ 新面生效 |
| SWITCH_GATE | `VIOLATION:MAA_10,MAA_12`（均 official-switch-not-true） | ✅ 与本审计独立抽查一致 |
| CLIENT_STATE | `未知×3、桌面×1（异常 4：idx1=未知[截图失败]、idx11=桌面、idx13=未知[截图失败]、idx14=未知[超时]）` | ✅ 实调成功 |
| ERR_SUMMARY | `102 条，Top=未知（fatal=64、warn=9、benign=29；login_n=33；escalated=true；窗口 09-27 05:00→09-28 05:00）` | ✅ 实调成功，且与报告文件逐字吻合 |
| DISPATCH_ALERT | `无` | ✅ |
| IDENTITY | `无法判定（mas_identity_trail ok=false：json 读回失败 identity_20260928_005012.json，无轮次证据，fail-closed）` | ⚠️ 已实调、工具侧失败 → F6a |
| POWER | `UNKNOWN:上游不可达（SSH 上传失败 FileNotFoundError[WinError 2]，未给关闭建议）` | ⚠️ 已实调、工具侧失败 → F6b（00:56 已修，待刷进程） |
| WIKI_GOV | `UNKNOWN:工具面未提供（mas-wiki 未接线）` | ⚠️ 同 §3.5（已知） |
| DISK | `C:30G/238G ⚠、E:143G/1863G ⚠` | ✅ 实调 |

结论字段=`疑似批次未结束`（晚班批次在跑：在飞 MAA=5、完成 17/57、待跑 40）。**注意**：本轮的 17 = 27 设计面 − 10（auto-mas-monitor 上游 502 掉线）——17 这个数在 monitor 恢复后会回到 27；F3a 的计数口径应写「全连=27、掉线时按实际」。 

---

## 3. 逐项根因定位

### 3.1 SWITCH_GATE —— 通过（真值）

- 链路：cron → mas-dispatch MCP `switch_gate` → `tools/mas_switch_gate.py`（REST 取脚本/账号元数据 + ssh 读 12 个 `E:/MAA-Multi/MAA_N/config/gui.new.json`）→ 原样回填 `switch_gate` 字段。
- 四轮输出 + 本轮两次 MCP 调用（00:35:38、00:41:41）与本地实跑（00:32 前后，REST=ok；00:43:06 存档，REST=502 回退 DEFAULT_DIRS）**结论一致**：`VIOLATION:MAA_10,MAA_12`。
- 说明：该标记与 09-27 定因文档吻合（masked+false = 轮末还原残留、不动点；清理方案=受控播种，待 lead）。闸门本身工作正常。

### 3.2 CLIENT_STATE / IDENTITY / POWER —— 缺因：mas-observe 未接线（非工具缺失）

- 工具存在且健康：`tools/mas_observe_mcp.py`（5 个只读工具；本轮 `--check` = OK，含 5 个 CLI 存在性 + selftest + `readonly_assert=PASS`）。
- 但截至 23:43/00:30 两轮，profile `config.yaml` **没有 `mcp_servers.mas-observe` 段**，`platform_toolsets.cron` 也没有 `- mas-observe` ⇒ 按 `hermes_cli/tools_config.py:692-704 _merge_mcp_servers` 的允许表语义，cron 工具面里根本不会有它 ⇒ 巡检按约定写 UNKNOWN。
- **修复（已落盘，兄弟卡 t_6cfe7865）**：00:31 config.yaml 新增 server 段 + cron 允许表加一行；00:32 进程刷新后日志出现
 `MCP server 'mas-observe' (stdio): registered 5 tool(s): …mas_preflight, mas_report, mas_idle_emulators, mas_client_state, mas_identity_trail`。
- **修复生效实证**：00:33:59 与 00:38:29 两轮的隔离自述均为
 「17 MCP tools visible (6 maaorch-daily + 4 mas-dispatch + **5 mas-observe** + **2 mas-triage**)」，并已实测调用 `mcp__mas_observe__mas_client_state` 等。

### 3.3 ERR_SUMMARY —— 缺因：mas-triage「陈旧注册」（工具没接上的是进程内注册，不是配置）

- 工具可用性：服务端 `tools/list` 直探 = `[triage_error, error_daily]`（2 个，本轮实测）；且 09-27 22:36:32 有一次成功调用实录（33.62s，870 chars）。
- cron 允许表里 mas-triage 一直有；`tools.include` 未设置（=该 server 全部工具应注册）。
- **缺的是常驻进程里的注册快照**：
  - `mas_triage_mcp.py.bak-errdaily-20260927-215731` ⇒ error_daily 于 21:57:31 才加进 server；
  - 日志中 mas-triage 最后一次 `registered 1 tool(s)` = 21:54:26；22:24:47 起才出现 `registered 2 tool(s)`（属其它进程）；
  - 承担 cron 的常驻 Hermes 进程（PID 29400，启动于 **09-26 21:08:18**）在 21:57 之前就与该 server 建立了长连接，`register_mcp_servers()` 对已连接 server 短路（`cron/scheduler.py:1810 _init_cron_mcp_tools` 注释：idempotent / short-circuit）⇒ **该进程的工具表停在 1 个工具**，巡检自然看不到 error_daily。
- **修复（已生效）**：00:31 config 变更引发进程刷新（roadmap §5.3 要求的 gateway 重启路径；或「enabled false→true」强制重连，~1min），00:32:57 起新进程 `registered 2 tool(s)`；00:33/00:38 两轮自述已含「2 mas-triage」，并在 00:35 实测发起 `mcp__mas_triage__error_daily` 调用。
- **修复后实测（真值 + 延迟）**：00:38:29 轮调用的 error_daily 完成于 00:45:01，**单次耗时 199.54s**（对照 09-27 22:36:32 的 33.62s）——慢远端下全量重扫是「分钟级」，**已逼近 MCP 默认 300s 外层上限**（F2 的直接依据）。返回值与本审计独立读到的报告文件 `runtime/reports/maa_errors_2026-09-27.md`（生成 00:45:00）**逐字吻合**：`ERR_SUMMARY=102 条，Top=未知；致命 64 / 告警 9 / 可忽略 29；登录类 33 ≥20 已升级`。
- **耐久规则（建议写进 runbook）**：任何 `mcp_servers` / server 脚本（工具名或工具集）改动 ⇒ 必须强制重连（gateway restart，或 enabled 关→开），否则常驻进程读不到新工具面。

### 3.4 DISPATCH_ALERT —— 通过（真值）

- 链路：`list_schedule` → `tools/mas_dispatch_mcp.py:_alerts_view()` → `runtime/mas_dispatch_alerts.json` 台账 `active`；空 ⇒ 提示词规定写 `无`。
- 当前台账：`{"active": [], "cleared": [], "updated": "2026-09-27 21:20:26"}` ⇒ 四轮 `无` 正确。

### 3.5 WIKI_GOV（相邻，非本卡 5 项之一）—— 缺因：mas-wiki server 未建

- `tools/` 下无 wiki 的 MCP 包装（`wiki_search.py` / `wiki_gov.py` 是 CLI）；提示词条目已就位、显式允许 UNKNOWN，故不算违规。
- 已有卡：t_c8b36f5e「mas-wiki 接线」（blocked）——本审计确认必要性：它是当前唯一「连占位值都拿不到真源」的标记。

---

## 4. SWITCH_GATE 一致性抽查（≥3 槽，独立读值）

方法：①本地实跑 `tools/mas_switch_gate.py --json`（00:32 前后 REST=ok 一次、00:43:06 REST=502 一次，结论集相同）；②**不经闸门代码**，用独立 ssh 脚本直读原始 `gui.new.json` 的 `Gui.RuntimeSettings.ClientType` 与 StartUp 任务的 `AccountName`/`AccountSwitchEnabled`（4 槽）。

| 槽 | ClientType（直读） | AccountName（直读） | switch（直读） | 闸门判定 | 口径期望 | 吻合 |
|---|---|---|---|---|---|---|
| MAA_1 | Official | 166****9553（掩码） | true | OK | masked+true | ✅ |
| MAA_10 | Official | 186****6119（掩码） | **false** | **VIOLATION**（official-switch-not-true） | masked+true | ✅ |
| MAA_11 | Bilibili | （空） | false | OK | empty+false | ✅ |
| MAA_12 | Official | 177****2151（掩码） | **false** | **VIOLATION**（official-switch-not-true） | masked+true | ✅ |

- 4/4 吻合；违例细节与 09-27 定因文档一致（两槽均 masked+false，掩码∈各自槽 expected_masks；成因=轮末还原残留）。
- mtime 佐证陈旧：MAA_10 `2026-09-27 05:56:42`、MAA_12 `2026-09-26 06:13:09`（与「不动点」结论一致）。
- REST 波动说明：巡检两次调用处于 `rest=down(HTTP 502)`（走 DEFAULT_DIRS 回退，notes=`no-rest-meta`）；本地实跑两次中一次 `rest=ok(脚本54/槽12)`。**两种模式下违例集合一致**，仅元数据丰富度不同。
- 证据存档：`docs/audits/2026-09-28_gate_wiring_check_switch_gate.json`（本目录，00:43:06 全量 12 槽 JSON）。

---

## 5. 最小修复清单（先出 diff，不代改）

### F1【已落盘·请 lead 确认后闭环】mas-observe 接线（config）

- 改哪里：`profiles/mas/config.yaml`（**已于 00:31 由 t_6cfe7865 落盘**）：
  - `mcp_servers` 新增 `mas-observe`（command/args/env/enabled）；
  - `platform_toolsets.cron` 追加 `- mas-observe`。
- 验证（只读）：`hermes -p mas mcp test mas-observe`（工具数=5）＋ 下一轮巡检报告 `CLIENT_STATE/POWER/IDENTITY` 出真值。**已现场验证**：00:33/00:38 两轮隔离自述见到 5 个工具；00:52:32 首轮报告 CLIENT_STATE 已出真值（IDENTITY/POWER 见 F6 两处工具级缺陷）。

### F2【待办·配置 2 处小 diff】补 per-server 超时（防外层 300s 截断）

依据：`tools/mcp_tool_common.py:44 _resolve_tool_timeout` —— `mcp_servers.<name>.timeout` > `timeouts.mcp.tool_call` > 默认 **300s**；本 profile 未设 `timeouts:`。而 mas-observe 内层预算：`mas_report` 420s、`mas_identity_trail` 420s；mas-triage 的 `error_daily` 远端扫描默认 420s。**外 300s < 内 420s ⇒ 慢远端时会被外层截断**（实测：00:38:29 轮 error_daily 单次 199.54s，已吃掉默认预算的 2/3；22:36 轮同工具 33.6s——同工具波动 6×）。

```diff
   mas-observe:
     command: C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe
     args:
       - D:\1-2\MAAOrch\tools\mas_observe_mcp.py
+    # roadmap §3.2 草案要求：report/identity 内层 420s，需 900s 兜底
+    timeout: 900
     env:
       PYTHONUTF8: '1'
```

```diff
   mas-triage:
     command: C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe
     args:
       - D:\1-2\MAAOrch\tools\mas_triage_mcp.py
+    # error_daily 内层默认 420s；不给足会被外层 300s 截断
+    timeout: 480
     env:
       PYTHONUTF8: '1'
```

- 验证：`hermes -p mas mcp test mas-triage`；下一轮巡检 `error_daily` 返回 `ERR_SUMMARY=<n> 条 Top=…`（而非 300s 截断）；`mas_report/mas_identity_trail` 不再出现 timeout 错误串。

### F3【待办·提示词 2 处 diff】随接线更新（受控写入：备份+逐字节回读，沿用 t_2e740057 做法）

a) 「设计面」计数（第一步）：

```diff
- 设计面：auto-mas-monitor 10 / maaorch-daily 6 / mas-dispatch 4 / mas-triage 2
+ 设计面：auto-mas-monitor 10 / maaorch-daily 6 / mas-dispatch 4 / mas-triage 2 / mas-observe 5
+ （全连=27；上游 502 时 auto-mas-monitor×10 会掉线，属已知，如实报）
```

b) 现场三行回退文案（mas-observe 已接线，原措辞会误导）：

```diff
- UNKNOWN:工具面未提供（mas-observe 未接线，见 docs/MCP_TOOL_ROADMAP.md §3-4）
+ UNKNOWN:工具面未提供（mas-observe 未连接/未注册，见 docs/MCP_TOOL_ROADMAP.md §3-4）
```

- 验证：下一轮报告首行 `MCP_API_TOOL_COUNT` 在全连时应=27；回退文案只在该工具确实不可见时出现。

### F4【已有卡·无需新建】mas-wiki 接线 = t_c8b36f5e（blocked）；完成后 `WIKI_GOV` 转真值。

### F5【观察·可选】巡检轮并发

00:32:52 与 00:37:23 两轮同时在飞（各自独立 session）。job 有「already running — skipping」防重（22:06:54 日志可见），双跑疑为人工/旁路触发。若非人工，建议核查触发源（防双跑重工具 error_daily 与重复补位）。

### F6【待办·工具级 2 处】mas-observe 两个工具在当前远端状态下失败（00:52 首轮实测暴露）

**F6a · `mas_identity_trail` 远端拉日志无超时护栏（本审计已复现）**
- 现象（巡检 00:52）：`ok=false`，`parse_error: json 读回失败: FileNotFoundError identity_20260928_005012.json`（CLI 非 0/2 退出且未写 json；`runtime/observe/` 下确无该文件）。
- 复现（本审计，仅写 scratch）：
  `python tools/mas_identity_trace.py report --since 2026-09-27 --json <scratch>/identity_test.json --outdir <scratch>/identity_trace_test`
  → 打印 `pull gui.log MAA_6 … pull gui.log MAA_2 … pull app.log …` 后**挂起 >220s**（被外层 timeout 杀掉，rc=124，json 未生成）。
- 根因：`mas_identity_trace.py` report 的「pull（远端取日志）」步骤没有逐步骤超时/快速失败；`tools/mas_observe_mcp.py:800-807` 只把 rc==2 归为 `upstream_unreachable`，其余非 0/无 json 一律落成 `parse_error`（错误分类过粗，掩盖真因）。
- 建议最小改法：① CLI 侧给每个 pull 加超时 + 失败即退（或支持 `--logs/--applog` 本地源优先）；② wrapper 对 rc∉{0,2} 返回独立 kind（如 `cli_exit_rc`）并带 rc/耗时；③（可选）`_run` 的 timeout 默认与工具 schema 对齐。
- 验证：远端恢复时调一次 → `ok=true` 且 json 落盘；远端坏时 → 快速返回 `upstream_unreachable`/`cli_exit_rc`（≤30s），不挂起。

**F6b · `mas_idle_emulators` SSH 上传 WinError 2（t_6cfe7865 已于 00:56 修源码）**
- 现象（巡检 00:52）：`POWER=UNKNOWN:上游不可达（SSH 上传失败 FileNotFoundError[WinError 2]，未给关闭建议）`。
- 根因（源码内已注明，`tools/mas_observe_mcp.py:229-298`）：MCP stdio server 被 SDK 以极简 env 拉起，加之 registry 环境大小写双键（`'PATH'` vs `'Path'`）——子进程实际生效的是 registry 用户版 Path（**不含 `System32\OpenSSH`**）⇒ 裸名 `ssh` 直接 WinError 2。
- 现状：**该文件 00:56 已被编辑修复**（env 键名大写归一 + PATH 继承/registry 两段合并 + `System32\OpenSSH` 前置）。**注意**：cron 侧 MCP server 进程是更早拉起的旧代码实例，源码改动需**再刷一次进程**（gateway restart）才生效——与 §3.3 同一条「陈旧进程」规则。
- 验证：下一次巡检 `POWER=` 出「空转实例数 建议=…」真值（或至少不再报 WinError 2）；辅以 `hermes -p mas mcp test mas-observe` + 一次实调。

---

## 6. 时间线证据（关键日志/文件）

```
09-26 21:08:18  Hermes 常驻进程（PID 29400）启动                     [procstart 探针]
09-27 21:54:26  日志：mas-triage registered 1 tool(s)（最后一次）    [agent.log]
09-27 21:57:31  mas_triage_mcp.py.bak-errdaily-… ⇒ error_daily 入 server
09-27 22:24:47+ 其它进程 registered 2 tool(s)（与常驻进程无关）      [agent.log]
09-27 22:36:32  error_daily 成功调用实录（33.62s, 870 chars）        [agent.log]
09-28 00:20:26  本审计直探：tools/list 实得 2（triage）/5（observe）；schema cache 回写
09-28 00:31     config.yaml 变更（mcp_servers.mas-observe + cron 列表）
09-28 00:32:01  日志：mas-observe registered 5 tool(s)               [agent.log]
09-28 00:32:57  日志：mas-triage registered 2 tool(s)（新进程）      [agent.log]
09-28 00:33:59  巡检轮 003252 自述：17 tools = daily6+dispatch4+observe5+triage2
09-28 00:35:38  巡检 switch_gate 返回：rest=ok(54/12)、VIOLATION:MAA_10,MAA_12
09-28 00:38:26  discovery 汇总：17 tool(s) from 4 server(s)；7 failed
                （auto-mas-scrubbed/CancelledError、auto-mas-monitor/CancelledError、
                  ekko-studio-×5 FileNotFoundError）——上游 502 与 Ekko 路径失效，均为旁路
09-28 00:43:06  闸门存档 JSON：12 槽、VIOLATION:MAA_10,MAA_12（REST=502 回退模式）
09-28 00:45:01  error_daily 完成（199.54s）→ runtime/reports/maa_errors_2026-09-27.{md,json} 落盘
09-28 00:52:32  修复后首轮报告落盘（17 工具；CLIENT_STATE/ERR_SUMMARY 真值；IDENTITY/POWER 工具级失败）
09-28 00:56     兄弟卡 t_6cfe7865 修 tools/mas_observe_mcp.py 的 env/PATH（ssh WinError 2）⇒ 需再刷进程生效
```

---

## 7. 复跑命令（只读）

```bash
# 闸门（详细/紧凑/机器可读）
python D:/1-2/MAAOrch/tools/mas_switch_gate.py
python D:/1-2/MAAOrch/tools/mas_switch_gate.py --check
python D:/1-2/MAAOrch/tools/mas_switch_gate.py --json

# observe / triage 自检
python D:/1-2/MAAOrch/tools/mas_observe_mcp.py --check     # 期望：tools(5)=… readonly_assert=PASS
python D:/1-2/MAAOrch/tools/mas_triage_mcp.py --check

# Hermes 侧工具面
hermes -p mas mcp test mas-observe
hermes -p mas mcp test mas-triage
hermes -p mas mcp ls

# 巡检输出（最近几轮）
ls -lt C:/Users/xiach/AppData/Local/hermes/profiles/mas/cron/output/52b88b0ffca0/ | head
```

---

## 8. 边界与后续

1. 本审计不代改任何配置/提示词/发车（F2/F3 均已给 diff，等待 lead 拍板）。F1（接线）现场验证已完成：00:52:32 首轮报告 CLIENT_STATE/ERR_SUMMARY 转真值、工具面 5+2 可见——可据此闭环；剩 F6 两处工具级修复的验证（F6b 需再刷一次进程）。
2. 「陈旧注册」类问题的根治（可选，另卡）：在 runbook/巡检提示词加一条「report 里 MCP_API_TOOL_COUNT 与设计面计数不符 ⇒ 记 TOOLFACE_DRIFT=<差值>」——把"工具面漂移"显式暴露，避免再次静默降级。 (本轮 22:36–00:30 的 `21/22` 与「triage 实见1/设计2」其实就是这个信号，巡检已经上报了，只是当时无人判读。)
3. 违规两槽的清理（MAA_10/MAA_12 masked+false）仍按 09-27 定因文档 §6 的受控播种方案执行（本卡不涉及）。
4. 建议把「MCP 变更 ⇒ 强制重连/刷进程」写进 runbook（本次审计中它同时解释了 error_daily 缺工具与 F6b 修复的生效条件）。
