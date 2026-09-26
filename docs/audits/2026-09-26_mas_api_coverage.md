# MAS API 覆盖审计 —— Hermes 能否/如何接手 AUTO-MAS

> 任务 **task-22** ｜ 2026-09-26 ｜ 执行 mcp-ops ｜ **只读分析：未改任何代码**
> 定位前提：MAAOrch = Harness/Hermes 与 MAA、MAS(AUTO-MAS) 之间的**粘合剂**；前端已决定整体退役，Node 只留最小运维页 `/status`。

**一句话结论：推荐方案 A（扩 MAAOrch 的 `mas_*` MCP 工具）。方案 B（Hermes 直连 @:36163`）在 MAS 控制面**没有任何鉴权**的前提下，等于把整条产线的控制面交给「能到那个网段的人」；若一定要并存，只能是「只读 B + 网络层 ACL」，绝不能接写。**

---

## 0. 结论摘要

| # | 结论 | 依据 |
|---|---|---|
| 1 | MAS 控制面共 **20 个端点**（**全部是 POST**）；MAAOrch 侧 20/20 都在用（8 个 CLI 脚本 + 1 个服务层 + 1 个已退役前端） | §2 |
| 2 | Hermes 经 MCP 能触达的 MAS 端点只有 **1 个**：`POST /api/history/search`（且只能拿到聚合后的结果） | §3 |
| 3 | **缺口 = 19 个端点**，其中最高危的是真发车 `POST /api/dispatch/start` 与改账号挂起态 `POST /api/scripts/user/update` | §3.2 |
| 4 | `100.79.173.69:36163` **无任何鉴权头**：全部调用方只发 `Content-Type`；已退役的浏览器前端也是裸调 | §4.1 |
| 5 | **实测**：本机 → `:36163` 的 TCP **OPEN**（Tailscale 段内可达） | §4.3 |
| 6 | 推荐 **A**；**C 的窄化版**（A + 只读 B + ACL）仅在用户明确要求时考虑；**反对 B 的写路径** | §5、§6 |
| 7 | **定时调度是两层**：第一层 = MAS 主机内队列定时表（tools/auto_mas_queue_times.py，141 行，经 API 写）；第二层 = MAAOrch 决策循环（tools/scheduler.py，785 行）—— **第二层当前无任何运行入口** | §11.1 |
| 8 | ⚠️ **第二层的完成判据已经死了**：`data/daily_state/*.json` 全部 16 个文件停在 **2026-09-18 06:37:18**，且仓库内**不存在写入方** | §11.3 |

---

## 1. 方法与证据边界

- 结论全部来自**读代码**（代码是唯一事实源）。行号为 2026-09-26 15:5x 的本机物理行号，定位请以**符号/端点字符串**检索为主。
  - 复现端点面：`rg -n "/api/(scripts|queue|history|dispatch|info)/" tools services ui/web/auto_mas.js`
- **唯一一次网络动作**：TCP connect（SYN）可达性探测 —— 不发任何 HTTP 请求、不读不写任何远端状态。
- **未做**（有意）：向 `:36163` 发任何请求（那是产线控制面，需用户批准）；未在 MAS 主机上执行 `netstat`。
- 凡代码无法确定的，一律标 **【未知，需实测】**，不猜。

---

## 2. MAS API 面全清单（20 个端点，全部 POST）

调用风格：资源/动词（`get/add/update/delete`），请求体传 `null` 表示「取全部」（`{"scriptId": null}`、`{"queueId": null}`）→ 通用 CRUD 控制器风格。

| # | 端点 | 用途 | MAAOrch 调用点（file:line） | 读/写 | MCP 暴露 |
|---|---|---|---|---|---|
| 1 | `POST /api/info/version` | 控制面存活探针（判 `.code == 200`） | tools/auto_mas_toggle_user.py:196 | 读 | ❌ |
| 2 | `POST /api/scripts/get` | 脚本(配置)清单 / 账号→脚本映射 | tools/scheduler.py:557、tools/auto_mas_rerun.py:52、tools/auto_mas_flatten.py:47、tools/auto_mas_toggle_user.py:83、tools/auto_mas_export.py:37、ui/web/auto_mas.js:63,236 | 读 | ❌ |
| 3 | `POST /api/scripts/update` | 改脚本 `Info.Path`（配平用） | tools/auto_mas_flatten.py:177 | **写** | ❌ |
| 4 | `POST /api/scripts/user/get` | 用户明细 `{data:{uid:{Info}}}` | tools/scheduler.py:560、auto_mas_rerun.py:54、auto_mas_flatten.py:52、auto_mas_toggle_user.py:86,113,185、auto_mas_export.py:48、ui/web/auto_mas.js:69,240 | 读 | ❌ |
| 5 | `POST /api/scripts/user/update` | 改用户 `Info`（**挂起/启用** = `Info.Status`） | tools/auto_mas_toggle_user.py:118 | **写** | ❌ |
| 6 | `POST /api/queue/get` | 队列列表 | auto_mas_rerun.py:127、auto_mas_flatten.py:66、auto_mas_add_slots.py:48、auto_mas_queue_times.py:18 | 读 | ❌ |
| 7 | `POST /api/queue/add` | 建队列 | auto_mas_rerun.py:212、auto_mas_add_slots.py:71 | **写** | ❌ |
| 8 | `POST /api/queue/update` | 改队列（`Info.TimeEnabled` 等） | auto_mas_rerun.py:213、auto_mas_add_slots.py:72、auto_mas_queue_times.py:116 | **写** | ❌ |
| 9 | `POST /api/queue/delete` | 删队列 | auto_mas_rerun.py:133 | **写** | ❌ |
| 10 | `POST /api/queue/item/get` | 队列项列表 | auto_mas_flatten.py:74 | 读 | ❌ |
| 11 | `POST /api/queue/item/add` | 加队列项 | auto_mas_rerun.py:217、auto_mas_flatten.py:189 | **写** | ❌ |
| 12 | `POST /api/queue/item/update` | 改队列项（挂脚本/启用） | auto_mas_rerun.py:219、auto_mas_flatten.py:192 | **写** | ❌ |
| 13 | `POST /api/queue/item/delete` | 删队列项 | auto_mas_flatten.py:187 | **写** | ❌ |
| 14 | `POST /api/queue/time/get` | 班次时间列表 | auto_mas_queue_times.py:91,132 | 读 | ❌ |
| 15 | `POST /api/queue/time/add` | 加班次 | auto_mas_add_slots.py:77、auto_mas_queue_times.py:96,106 | **写** | ❌ |
| 16 | `POST /api/queue/time/update` | 改班次 | auto_mas_add_slots.py:81、auto_mas_queue_times.py:100,110 | **写** | ❌ |
| 17 | `POST /api/queue/time/delete` | 删班次 | auto_mas_queue_times.py:93 | **写** | ❌ |
| 18 | `POST /api/history/search` | 历史（`mode=DAILY`，按天） | **services/mas_l3.py:109**、tools/scheduler.py:417、tools/auto_mas_archiver.py:69、ui/web/auto_mas.js:199 | 读 | **✅ 部分**（`mas_history`/`mas_status`，聚合后） |
| 19 | `POST /api/history/data` | 单次运行详情（掉落/理智/基建） | tools/auto_mas_archiver.py:152、ui/web/auto_mas.js:256 | 读 | ❌ |
| 20 | `POST /api/dispatch/start` | **真发车** `{taskId:<脚本id>, mode:"AutoProxy"}` | tools/scheduler.py:592（dry-run 分支见 :590） | **写** | ❌ |

**统一调用封装（都是无鉴权）**：services/mas_l3.py:97-102（`_api()`）、tools/scheduler.py:542-555、tools/auto_mas_rerun.py:33-37、tools/auto_mas_flatten.py:35-39、tools/auto_mas_add_slots.py:26-30、tools/auto_mas_queue_times.py:32-36、tools/auto_mas_toggle_user.py:49-53、tools/auto_mas_export.py:26-30、tools/auto_mas_archiver.py:34-38、ui/web/auto_mas.js:3-11。

**响应信封**（从调用点反推）：
- 列表 `/api/scripts/get` → 顶层 `index`（tools/scheduler.py:558 `sc.get("index")`）+ `data`（tools/auto_mas_flatten.py:52）
- 单对象 `/api/scripts/user/get` → `data[uid].Info`（tools/auto_mas_toggle_user.py:113）
- 存活 `/api/info/version` → `code`（tools/auto_mas_toggle_user.py:196）
- 【未知，需实测】错误信封是否统一、是否有分页、GET 是否也被接受（代码里**全部**用 POST）

### 2.1 非 API 的 MAS 能力（同为「Hermes 摸不到」的一部分）

| 能力 | 实现 | 调用点 | MCP 暴露 |
|---|---|---|---|
| 主机实时快照（在飞 MAA / 盘位 / 队列进度 / 失败号 / 未跑号 / 磁盘） | SSH 远程跑 `E:/_mas_watch_data.py` | tools/mas_watch.py:34-37 → services/mas_l3.py:61-92 | ✅ `mas_snapshot` |
| **账号清单**（服 / 模拟器 index / ADB 口 / 切换关键字） | SSH 读 `ScriptConfig.json` + `MuMuManager.exe` | tools/mas_accounts.py:48-50 | ❌ |
| 终端看板（掉落/剿灭/耗时/失败） | SSH/SFTP 自读 `MAS_ROOT=E:\AUTO-MAS` | tools/mas_dashboard.py:27-29、tools/mas_dash.sh:14 | ❌ |

---

## 3. 覆盖矩阵与缺口

### 3.1 现有 7 个 `mas_*` MCP 工具实际打到哪

| MCP 工具（network/mcp_server.py） | 后端 | 实质到达 |
|---|---|---|
| `mas_snapshot` :242 | mas_adapter.snapshot → mas_l3.host_snapshot → **SSH** | 非 API |
| `mas_status` :250 | SSH 快照 + `/api/history/search` | 端点 18 |
| `mas_history` :258 | `/api/history/search` | **端点 18（唯一直连端点）** |
| `mas_ledger` :266 | 本地 `runtime/mas_ledger.jsonl` | 非 MAS |
| `mas_rerun_state` :273 | 进程内内存 | 非 MAS |
| `mas_rerun` :280（写，`dry_run=True`） | CLI `tools/auto_mas_rerun.py --apply` → 内部打到端点 **2,4,6,7,8,11,12,20** | 语义受限：**只能补跑快照里的失败号（≤8）** |
| `mas_cleanup` :290（写，`dry_run=True`） | CLI `--cleanup` → 端点 **6,9** | 语义受限：**只能删临时补跑队列** |

> 关键精确化：Hermes **并非完全不能写 MAS** —— `mas_rerun` 已经能经 CLI 间接触发端点 20 发车。缺的不是「写能力」，而是**动词表达力**：它无法指定「发哪个脚本」、无法挂起/启用账号、无法动班次与队列结构、无法看单次运行明细。

### 3.2 缺口清单：MAAOrch 会用、Hermes 摸不到（19 个端点）

| 缺口 | 端点 | 现实后果 |
|---|---|---|
| 真发车 | 20 `/api/dispatch/start` | Hermes 不能按意图发车，只能"补跑失败号"这一条窄路 |
| 账号挂起/启用 | 5 `/api/scripts/user/update` | 对不上 POSITIONING G2 的 `toggle_account` 缺口 |
| 班次/槽位 | 14-17 `/api/queue/time/*` | 对不上 POSITIONING 的 `set_slots` 缺口 |
| 队列结构增删改 | 7,8,9,11,12,13 `queue[/item]/*` | agent 不能建/删/改队列，只能靠 CLI 一次性动作 |
| 账号与脚本清单 | 2,3,4 `/api/scripts*` | agent 不知道「有哪些号、叫什么、挂在哪个脚本」——**这是所有动词的前置条件** |
| 单次运行明细 | 19 `/api/history/data` | 只有聚合历史，拿不到掉落/理智/基建逐次明细 |
| 控制面存活 | 1 `/api/info/version` | 无「MAS 起来了没」的直接探针（现靠 SSH 间接推断） |
| 账号清单（非 API） | SSH `ScriptConfig.json` | 同上，且改挂起态前必须先读到 `Info.Status` |

---

## 4. 鉴权与暴露面（**本节是决策的核心**）

### 4.1 代码证据：MAS 控制面没有任何鉴权

所有 MAS 调用方的请求头**只有** `Content-Type: application/json`：
- services/mas_l3.py:97-102（`_api()`，headers 仅 Content-Type）
- tools/scheduler.py:548、:594
- tools/auto_mas_flatten.py:39、auto_mas_add_slots.py:30、auto_mas_queue_times.py:36、auto_mas_toggle_user.py:53、auto_mas_export.py:30、auto_mas_archiver.py:38、auto_mas_rerun.py:37
- ui/web/auto_mas.js:3-11（**已退役前端**：浏览器 `fetch(AUTO_MAS_HOST + path)`，同样只带 Content-Type）

反向验证：`rg -n "Authorization|Bearer|x-api-key|apiKey" tools/auto_mas_*.py tools/scheduler.py services/mas_l3.py ui/web/auto_mas.js` → **0 命中**（tools/ 下其他 token/Bearer 命中全是 Discord Bot、DeepSeek AI key、MAAOrch 自己的 `x-manager-token`/`x-agent-token`，与 MAS 无关）。

### 4.2 36163 不是 localhost-only（浏览器直连证据）

ui/web/auto_mas.js:2 硬编码 `AUTO_MAS_HOST = 'http://100.79.173.69:36163'`，且 :3-11 是**浏览器侧** fetch → 该端口必须对「运行浏览器的机器」可达（MAAOrch 的前端还会在手机/局域网设备上打开，见 tools/gen_push_ui.py:27-40 的 control_token 设计）。也就是说：**Tailscale 段内任意主机都能直连这个控制面**。

### 4.3 可达性实测（只读）

`python -c "import socket;s=socket.socket();s.settimeout(4);print(s.connect_ex(('100.79.173.69',36163)))"`
→ **36163 OPEN**（2026-09-26，本机 = MAAOrch 所在机，Tailscale 段内）。未发送任何 HTTP 请求。

### 4.4 侧面印证：看门狗把 36163 当 MAS 的健康信号

tools/host_watchdog.py:141-149 `probe()` 直接 TCP 探 `host:port`，配置文件 runtime/watchdog.ini:4-5 = `host=100.79.173.69 / port=36163`。注释把两种失败分得很清楚：「机器活着、AUTO-MAS 没起来」vs「主机整个失联」→ 说明该端口**长期、常态**对 Tailscale 段开放。
（该 ini 含通知凭据，按敏感件对待，本文不引用其凭据字段。）

### 4.5 【未知，需实测】清单（不要猜）

| 未知项 | 为什么重要 | 怎么测（**需用户批准**） |
|---|---|---|
| MAS 是否**存在**鉴权机制（只是没启用），还是**根本没有** | 决定 B 能否成立 | 查 AUTO-MAS 的设置/源码/文档；或从**非** Tailscale 源直接 POST 一次 `/api/info/version` 看是否 401 |
| `:36163` 的绑定地址（`0.0.0.0` / `127.0.0.1` / Tailscale IP） | 决定暴露半径 | 在 MAS 主机执行 `netstat -ano | findstr 36163` |
| 是否有代码中未出现的端点（如 `/api/record/*`、`/api/log/*`） | 决定「API 面」是否真的盘全 | 查 AUTO-MAS 项目文档/源码，或做端点枚举（**有风险，需批准**） |
| 错误信封 / 分页 / 并发与速率限制 | 决定工具实现方式 | 单次调用实测 |
| 写操作是否需要"停机窗口"（如改班次是否影响正在跑的号） | 决定 dry_run 之外是否还要时间窗闸门 | 查 AUTO-MAS 文档 + 与现有人工流程核对 |

---

## 5. 三方案评估

| 维度 | **A 扩 `mas_*` MCP 工具** | **B Hermes 直连 36163** | **C 并存** |
|---|---|---|---|
| 事实源归属（MAS 队列/账号/班次真相在主机，POSITIONING.md:75） | ✅ 写仍回远端；读在一个归一化层 | ⚠️ 写仍回远端，但**读的聚合口径要在 Hermes 侧重写**→ 双口径 | ⚠️ 同 B（取决于是哪种并存） |
| 鉴权与暴露面 | ✅ 复用 `_TokenAuth`（network/mcp_server.py:70-97，`hmac.compare_digest`；S1）；**不新增暴露面** | ❌ 控制面无鉴权；再挂一个 MCP server 若不加 token 就是**第二个裸门** | ❌ 除非先给 B 加 ACL/前置 token，否则 C = 把 A 的闸门架在被绕过的一侧 |
| 写操作防护 | ✅ 全部 `dry_run=True` 默认（与 task-17 契约一致，mcp_server.py:14-27） | ❌ MAS 原生长这样：无预演、无拒绝，`/api/dispatch/start` 一点就发车 | ⚠️ 取决于是否禁止 B 的写 |
| 故障隔离 | ✅ `degraded/host_unreachable` 集中判定（services/mas_adapter.py:83-96；POSITIONING S6） | ❌ Hermes 各自处理 SSH/HTTP 差异与超时 | ⚠️ |
| 「粘合剂」定位是否被绕过 | ✅ 仍是唯一门面（services/mas_adapter.py:1-24） | ❌ **绕过**：MAAOrch 的白名单、`mas_ledger` 留痕、事件总线全部失效 | ❌ 部分绕过 |
| 审计/留痕 | ✅ CLI 路径已有三重校验与留痕（如 tools/auto_mas_toggle_user.py:185-201 的 API 复读+落盘校验+存活校验） | ❌ 无留痕 | ⚠️ |
| 实现成本 | 中：adapter 层 + ~14 个工具（预估 300-360 行） | **低：写配置即可**（但要先补鉴权，成本立刻反超） | 中高 |
| 见效速度 | 1-2 轮 | 立刻（若有网段可达 + 用户接受风险） | 中 |

---

## 6. 推荐

**推荐 A。** 四条硬理由：

1. **安全是不对称的**：A 的风险是"多几个带 `dry_run` 的工具"；B 的风险是"整条产线的控制面（含删队列、改脚本、发车）对 Tailscale 段无鉴权开放、无留痕"。MAS 端点在 §4.1 已证无鉴权，且 §4.3 已证可达 —— B 的收益（省一层代码）远小于代价。
2. **口径唯一**：「一次过 / 补跑成功 / 全败」「钉子户」「失败原因 Top」这套判定已经写在 services/mas_l3.py:115-187；B 会让 Hermes 重写一遍，形成 POSITIONING G4 已经警告过的**双事实源/双口径**。
3. **写动词需要"带纪律的包装"**：现有 CLI 不是裸调端点，它带三重校验与锁感知（tools/auto_mas_toggle_user.py:185-201）、"先写盘再发请求"（tools/scheduler.py:583-587）。这些纪律**只能**在 MAAOrch 侧保留。
4. **定位**：B 让 MAAOrch 从「粘合剂」退化成「可选的旁观者」，与 §1 的定位契约冲突。

**B 的唯一可接受形态（= C 的窄化版）**：仅只读端点 + Tailscale ACL 限定源 IP + 明确不用于任何写；并且要先把 B 的入口登记进 POSITIONING §2.4/§5 的暴露面清单（否则下一个审计会把"无鉴权控制面"记成审计遗漏）。即便如此，本文仍不推荐 —— 只读缺口（脚本清单、队列结构、运行明细）用 A 补的成本很低。

**明确反对**：把 `/api/dispatch/start`、`/api/queue/delete`、`/api/scripts/update`、`/api/scripts/user/update` 这四个写端点直接暴露给 Hermes。

---

## 7. 若走 A：要新增的 MCP 工具清单（**本轮不实现**）

命名沿用现有 `mas_` 前缀；全部经 services/mas_adapter.py 门面（mcp_server 不直接 import mas_l3，POSITIONING.md:34 / S4）。**只读工具标【只读】；写工具一律 `dry_run=True` 默认并在 docstring 标注。**

### 7.1 只读（7）

| 工具名 | 代理端点 | 说明 |
|---|---|---|
| `mas_alive` | `POST /api/info/version` | 控制面存活探针（比 SSH 快） |
| `mas_scripts` | `POST /api/scripts/get` | 脚本/配置清单 + 账号→脚本映射（**所有动词的前置**） |
| `mas_script_users` `(script_id)` | `POST /api/scripts/user/get` | 用户明细（`Info.Status` 挂起态、Server、Name） |
| `mas_queues` | `POST /api/queue/get` | 队列列表（含 `Info.TimeEnabled`） |
| `mas_queue_items` `(queue_id)` | `POST /api/queue/item/get` | 队列项（挂在哪个脚本、是否启用） |
| `mas_queue_times` `(queue_id)` | `POST /api/queue/time/get` | 班次时间（05:00 / 19:30 那套） |
| `mas_run_detail` `(json_path)` | `POST /api/history/data` | 单次运行详情（掉落/理智/基建） |

### 7.2 写（7，全部 `dry_run=True` 默认）

| 工具名 | 代理端点 | 危险度 |
|---|---|---|
| `mas_dispatch` `(script_id, mode="AutoProxy", dry_run=True)` | `POST /api/dispatch/start` | **最高危：真发车** |
| `mas_user_enabled` `(script_id, user_id, enabled, dry_run=True)` | `POST /api/scripts/user/update`（前置两次 `user/get` 复读，照 tools/auto_mas_toggle_user.py:113-118 纪律） | 高（改账号可用性） |
| `mas_slot_set` `(queue_id, times, dry_run=True)` | `POST /api/queue/time/add` / `update` + `/api/queue/update` | 高（对应 `set_slots`） |
| `mas_queue_create` `(name, dry_run=True)` | `POST /api/queue/add` + `/api/queue/update` | 中 |
| `mas_queue_delete` `(queue_id, dry_run=True)` | `POST /api/queue/delete` | 高（删结构） |
| `mas_queue_item_set` `(queue_id, item_id, …, dry_run=True)` | `POST /api/queue/item/update` | 中 |
| `mas_script_path_set` `(script_id, path, dry_run=True)` | `POST /api/scripts/update` | 中（改配置指向） |

> 最小集（若不想一次加 14 个）：**只读 `mas_scripts` / `mas_script_users` / `mas_queues` + 写 `mas_dispatch` / `mas_user_enabled`** 这 5 个，即可覆盖 POSITIONING 里 `toggle_account` 与"按意图发车"两个最痛的缺口。

### 7.3 实现分层与行号落点（预估）

- **分层建议**：只读 → 在 services/mas_l3.py 新增直连函数（无副作用，省一次 subprocess）；写 → **优先复用现有 CLI**（它们已带三重校验/锁感知/先写盘再发请求），只有 CLI 不适用的（`dispatch/start` 需要精确 script id）才在 mas_l3 加函数。adapter 门面签名同步扩展。
- **落点**：追加在 network/mcp_server.py 的 MAS 段末尾（当前 `mas_cleanup` 结束于 **network/mcp_server.py:297**）之后 → 新段约 **L299 起**；14 个工具约 300-360 行 → 文件由 581 行增至 **约 940 行**；挂载段（现 L578-581）与「守护审计放行」段（现 L299-355）整体下移。
- ⚠️ **副作用**：这会让 docs/POSITIONING.md §2.4 的 S2/S7 行号再次漂移。§2.4 已改为「符号 + 行号」双写（见 POSITIONING.md:4），**实现后重跑一次行号核对即可**，不要手改数字。

---

## 8. 安全提醒（若用户仍坚持 B，必须先满足）

1. **现状 = 无鉴权 + 网段即权限**：§4.1 已证所有调用只带 `Content-Type`；§4.3 已证 `:36163` 从本机可达；§4.2 已证它**不是 localhost-only**。结论：**谁能到 Tailscale 段 `100.79.173.69`，谁就能删队列、改脚本、改账号挂起态、直接 POST `/api/dispatch/start` 发车**，且**无审计、无 dry_run、无留痕**。
2. **必须先在网络层设闸，而不是靠"没人知道这个口"**。三选一（按推荐顺序）：
   - **Window 防火墙 / Tailscale ACL** 限定源 IP（只允许 MAAOrch 主机与 Hermes 主机）；
   - **前置带 token 的反向代理** —— 本项目已有同构模式可抄：tools/maa_channel/mcp_server.py:1-9（`x-token` 鉴权，token 落 `token.txt`，注释明确"仅限可信网络段/Tailscale，勿暴露公网"）；
   - 若 AUTO-MAS 自带鉴权则启用它（**【未知，需实测】**，见 §4.5）。
3. **凭据卫生**：本章实测涉及 runtime/watchdog.ini（含通知凭据，且已 gitignore，禁止入库）、`tools/mas_accounts.py` 明确剔除 `pass|token|secret|key|cred` 字段（:54）、`ScriptConfig.json` 内含 DPAPI 密文 Password（:21-24）——**任何 MAS 直连方案的文档、日志、MCP 返回体都不得携带这些字段**。
4. **不要把 `:36163` 写进 Hermes 配置就完事**：MCP server 侧也要有 token；否则等于给无鉴权控制面加了一个"友好入口"，暴露面反而更明显。

---

## 9. 复现命令

`@powershell
# 1) 端点面（20 个端点的全部调用点）
rg -n "/api/(scripts|queue|history|dispatch|info)/" tools services ui/web/auto_mas.js

# 2) 鉴权：MAS 调用方是否带鉴权头（预期 0 命中）
rg -n "Authorization|Bearer|x-api-key|apiKey" tools/auto_mas_*.py tools/scheduler.py services/mas_l3.py ui/web/auto_mas.js

# 3) base 与端口（含看门狗的 TCP 健康探针配置）
rg -n "36163|100\.79\.173\.69" services/mas_l3.py tools/scheduler.py runtime/watchdog.ini

# 4) 现有 MCP 的 MAS 工具面（预期 7 个）
rg -n "^    def mas_" network/mcp_server.py

# 5) 可达性（只读 TCP，不发 HTTP）
python -c "import socket;s=socket.socket();s.settimeout(4);print(s.connect_ex(('100.79.173.69',36163)))"
`@

预期：① 命中 20 个端点、② **0 命中**、③ 命中 mas_l3.py:36 / scheduler.py:39 / watchdog.ini:4-5 / 各 CLI 的 API 常量、④ 7 行、⑤ 输出 `0`（= 可达）。

---

## 10. 未决问题（需用户/Lead 拍板）

1. AUTO-MAS **是否存在**鉴权机制？（决定 B 是否**可能**成立）—— 需实测，别猜。
2. `:36163` 的绑定范围？（`netstat` 实测）
3. 走 A 的话，14 个工具一次上，还是先上 §7.2 的 5 个最小集？
4. `mas_run_detail`（`/api/history/data`，掉落明细体积大）要不要进 MCP？还是留在本地存档（tools/auto_mas_archiver.py）？
5. 写端点是否需要**时间窗闸门**（避开正在跑的班次）—— 现有 CLI 只在超时/锁上有纪律，没有"期间禁写"闸门。
6. 若用户坚持 B：先做 **ACL** 还是先做 **token 前置代理**？两者都不做则本报告明确反对。

---

## 11. 追加交付：让 Harness(Hermes) 接手 MAS 定时调度（task-22 追加要求）

> 本轮仍**只出报告不改实现**。Lead 已侦察并给出线索，本节是 mcp-ops 的**独立复核 + 结论**；与 Lead 提供的数字不一致处已标出。

### 11.1 两层结构：已逐条核实

| 层 | 实现 | 核实结果 |
|---|---|---|
| **第一层：MAS 队列定时表** | tools/auto_mas_queue_times.py | ✅ 存在，**141 物理行**（Lead 记的 115 偏小）。9 个队列 UUID 硬编码在 **L19-29** 的 `QUEUES` 块里（uuid 本身在 **L20-28**）✅；默认早班 05:00 / 晚班 19:30、每队 +3min、全 7 天（L4、L72-75）✅；`--dry-run` ✅（L72、L83-85）。**全部经 MAS API 写，不碰文件** ✅：`/api/queue/time/get`(L91)、`/delete`(L93)、`/add`(L96,106)、`/update`(L100,110)、`/api/queue/update`(L116)。流程 = **先清空该队列现有定时项（幂等重置）→ 加早班+晚班 → TimeEnabled=True**（L90-118），并带写后验证（L129-137） |
| **第二层：MAAOrch 决策循环** | tools/scheduler.py | ✅ **785 物理行 / 677 非空行**（与 Lead 一致）。`DEFAULT_TASKS` 在 **L67-74** ✅（event p1 `done_when=None enable=False`；infrast p3 `infrast_done`；stamina p5 `stamina_cleared`；annihilation p7 `annihilation_done`）。`wait_weight=0.5`(L61)、`retry{max:3,delay_min:5}`(L62)、`screenshot{limit_gb:40,buffer_gb:5}`(L63)、`periods{infrast_h:12,stamina_regen_h:10}`(L64)、`RECENT_RUN_MIN=60`(L76) ✅ 全部一致 |

**「无运行入口」独立复核：✅ 属实。** `rg -n "scheduler"`（排除 docs / data）在非文档文件里的命中只有：main_web.pyw:284,299、api_fastapi.py:898,922、`services/*`（全是 **depot / daily_batch / activity** 三个**无关**的调度器）、tools/deploy.py:48,73（部署清单，同样指 services/depot_scheduler.py）。**没有任何 .bat / .ps1 / .mjs / 计划任务引用 tools/scheduler.py**；它只出现在自身 docstring 的"手动跑"说明里（tools/scheduler.py:13-15）。
- 佐证：其状态产物 `data/schedule/`（tasks_UID.json / id_counter.json / config.json / script_map.json）**最后一次写入是 2026-09-18 07:15:49** → 第二层**已经 8 天没跑过**。

> 因此 Lead 的结论成立：**「接手定时」= 把第二层的决策循环搬进 Hermes；MAAOrch 只保留「读状态 + 触发 dispatch」的能力面。**

### 11.2 Q1 —— 需要哪些 MCP 工具（含对 Lead 下限清单的两处修正）

**只读（必备）**

| 工具名 | 代理端点 / 数据源 | 为什么必须 |
|---|---|---|
| `mas_scripts` | `POST /api/scripts/get` | 账号名 → **script id（uuid）** 映射；这是 `dispatch/start` 的 `taskId` 唯一来源（现由 tools/scheduler.py:557-568 建缓存 data/schedule/script_map.json） |
| `mas_script_users` (script_id) | `POST /api/scripts/user/get` | 账号清单 / Server / `Info.Status` |
| `mas_queues` | `POST /api/queue/get` | 层一的队列结构（含 `Info.TimeEnabled`） |
| `mas_queue_times_get` (queue_id) | `POST /api/queue/time/get` | 读回当前班次表（预演与核对） |
| `mas_history` | ✅ **已有**（network/mcp_server.py:258） | 崩溃对账 + "最近落账"去重判据（RECENT_RUN_MIN） |
| `mas_snapshot` | ✅ **已有**（network/mcp_server.py:242，SSH） | 在飞 MAA / 空位 / 磁盘（三道闸的输入） |

**写（`dry_run=True` 默认）**

| 工具名 | 代理端点 | 危险度 |
|---|---|---|
| `mas_dispatch_start` (script_id, mode="AutoProxy", dry_run=True) | `POST /api/dispatch/start` | **最高危：真发车，且不可撤销（见 11.5）** |
| `mas_queue_times_set` (queue_id, times, dry_run=True) | `POST /api/queue/time/`{add,update,delete} `+` `/api/queue/update` | 高：改的是**产线时间表**（层一） |

**⚠️ 修正 1：`mas_dispatch_status` 没有对应的 MAS 端点。**
tools/scheduler.py:424-428 的作者注释写明：**"AUTO-MAS 没有'运行时状态'接口"**（这正是它改用"最近落账时间"当保守判据的原因）。所以"派发状态"只能**合成**，建议改名 `mas_schedule_state`，返回三块：
① 本地状态机快照（data/schedule/tasks_UID.json 的 state / next_run / last_run + id_counter.json）；
② 对账视图（由 `mas_history` 推"最近落账"与"是否有完成记录"）；
③ 在飞视图（`mas_snapshot` 的 MAA 进程 / 空位）。
—— 不要把它包装成"MAS 的某个端点"，否则下一个人会去追一个不存在的 API。

**⚠️ 修正 2：`mas_daily_state` 建议不要照原样暴露** —— 它读的是**本地文件**，且**该文件已死**（见 11.3）。要做就做成"动态判据"，而不是"读死文件"。

**另外两个能力面的硬缺口（交接前必须知道）**
1. **MAS 没有"停止/取消"端点**：§2 的 20 个端点里没有 stop / cancel / kill。→ Hermes **能发车但叫不停**；唯一的"停"是层一/层二自己的时间窗与队列规则，或人工 / SSH。
2. **三道闸 + 肉鸽打断不能丢**：闸①`MAA=0`、②无采样、③`uptime<30min`，以及 **fail-closed 纪律**（docs/SCHEDULER.md:46-55、:102-104 —— 那里记录着一次真实事故：uptime 探测失败被**静默放行**，`--live` 在开机 19 分钟时真杀了 l-1 的 MAA、关了实例 25）。**建议把三道闸留在 MAAOrch 侧**（由 `mas_dispatch_start` 内部强制），Hermes 只做"优先级 / 意图"决策 —— 否则等于让 Hermes 重踩这个坑。

### 11.3 Q2 —— `done_when` 的判据 `data/daily_state/<uid>.json` 谁在写？

**结论：没有写入方。这套判据现在是死的。** 四条独立证据：

1. **仓库内没有任何写入方。** `rg -n "daily_state"` 全仓只命中：tools/scheduler.py:14（docstring）、:38（`DAILY = PROJ / "data" / "daily_state"`）、:67（注释）、:430/:456/:652（**全部是读**：`_load(DAILY / ...)` 与 `DAILY.glob("*.json")`）、docs/SCHEDULER.md:13（只是**消费者**的说明）、以及两份 prune 台账。**没有任何 `_save(DAILY ...)`** —— scheduler.py 自己只写 `data/schedule/`（L493 `_save(tasks_path(uid), doc)`、L115 id_counter）。
2. **写入者文件在树里不存在。** `glob **/daily_state*` → **0 命中**；`tools/daily_state.py` **不存在**。它出现在 docs/audits/2026-09-26_prune_manifest.json:69（tools_root 分区 163 文件）里，**但同一分区也列了仍在树里的 tools/auto_mas_archiver.py:15** → 该 manifest 是**分区台账，不是删除记录**，不能据此说"被归档了"。且 **`tools/_archive/` 根本不存在**（Test-Path = False）→ 归档动作从未执行，所以它也不是"被移走"。
3. **数据本身已经 8 天没动。** `data/daily_state/*.json` 共 **16 个**，**全部 LastWriteTime = 2026-09-18 06:37:18**（最老与最新同一时刻）；内容里的 `date` 字段也是 `2026-09-18`（例：data/daily_state/Gitano.json:17，同文件 :11-13 记录 `stamina_cleared: true` / `infrast_done: true` / `annihilation_done: false`）。
4. **设计文档自己就承认闭环没做完。** docs/SCHEDULER.md:119 只勾了"**stamina 闭环**"，:121 明确把"**其他任务类型（infrast / annihilation / event）的闭环**"列为 ⬜ 未做；全文**从未指明生产者**。

**这意味着什么（交接前必须解决）**：`scan()` 在 tools/scheduler.py:480 用 `st.get(key) is True` 判"已完成"，读的是 **09-18 的旧布尔值**。直接后果：
- `annihilation_done` 永远 false → annihilation 只要有空位就会被反复叫号；
- infrast / stamina 用旧 `last_run`（如 Gitano 的 `05:15:40`）与**今天**的日期拼成 `done_at`（L482-485）→ next_run 按错的时间基准算。
⇒ **把第二层搬进 Hermes 之前，必须先修判据来源**，否则只是把死判据搬个地方。

**建议的修法（两条，推荐第 2 条）**：
1. 恢复写入方：把 daily_state 的产出重建为一个 job（从 `/api/history/search` + `/api/history/data` 解析 sanity / drops / infrast / duration / error_task —— tools/auto_mas_archiver.py:69,152 与 tools/auto_mas_hook.py:52-66 已经把这套解析写好了，可直接复用）。
2. **改成无状态判据（推荐）**：`done_when` 不再读文件，而是**当场从 history 推导** —— "该账号今日的 `index[]` 里有对应任务链的 DONE 记录"。好处：没有生产者就不会烂；Hermes 每次决策都现算，天然增量；也顺手消掉 `daily_state` 这个 8 天没更新的隐藏状态源。

### 11.4 Q3 —— 兜底：Hermes 停摆，产线会不会完全不动？

**不会 —— 前提是保留第一层。而且现在的现实比假设更强：第二层今天就没在跑，产线仍在运转。**

- **第一层是"MAS 主机内"的定时表**：tools/auto_mas_queue_times.py 把班次写进 MAS 队列（`TimeEnabled=True` + 7 天 `Days`，L100-118），由 **AUTO-MAS 自己**到点触发 —— 它**不依赖 MAAOrch、也不依赖 Hermes**。这就是天然安全网。
- **第二层本来就是"补漏器"不是"主力"**：docs/SCHEDULER.md:59-62 是 2026-09-18 用户拍板的设计决定 —— 「**重启后的第一轮仍由 AUTO-MAS 自己的启动队列跑；调度器只负责白天补漏**」「两个系统各管一段」。所以 Hermes 接手的是补漏与对账，**不是产线的心跳**。
- **经验证据**：第二层无运行入口 + 状态停在 09-18（§11.1），而产线照常 —— 说明**实际主力一直是第一层**。

**建议：保留第一层作为安全网，不要关。** 关掉它 = 把"确定性定时"换成"依赖一个可能停摆的 agent"，而它带来的唯一风险（双发车）可以用更便宜的方式解决：
- **双发车互斥**：Hermes 发车前必须判"可能还在跑" —— 现成判据可直接继承 `possibly_running()`（tools/scheduler.py:424-434）+ `RECENT_RUN_MIN=60`（:76）；作者的取舍写得很明白：「**宁可漏掉一轮，也不重复烧理智**」。
- 若用户坚持"层一也交给 Hermes 管"：**必须**同时上一个反向看门狗（Hermes 心跳超时 N 分钟 → MAAOrch 自动把层一恢复/接管），否则就是单点。本报告**不推荐**这条。

### 11.5 Q4 —— `/api/dispatch/start` 不可逆，dry_run 能预演到什么程度？

**能做（真预演，不是假动作）**：
1. **显式复述将要提交的 body**：`{taskId: <解析出来的真实 script uuid>, mode: "AutoProxy"}` —— script id 通过**只读**的 `/api/scripts/get` + `/api/scripts/user/get` 解析（tools/scheduler.py:557-568 同款），所以预演里能给**真 uuid**，不是占位符。
2. **跑完整闸门与判据**：本地取号（`gen_id`，scheduler.py:106-116）、状态机 `scan`（:453-494）、叫号与去重（:663-667）、三道闸与空位（:670）→ 能回答"现在有几个空位、这个号会不会被派"。
3. **先例可循**：scheduler.py:589-590 的 dry 分支就是"只打印、不提交"（打印 `DRY 取号 … → POST /api/dispatch/start {taskId:…, mode:AutoProxy}`）。

**做不到（必须写进 docstring）**：
- **无法预判 MAS 会不会接受**：20 个端点里**没有任何校验 / 试算端点**，只能提交后看返回（scheduler.py:596 读回 `taskId`）。
- **无法回滚，也无法取消**：`dispatch/start` 是单向门；MAS **没有 stop / cancel 端点**（§2）。scheduler.py:583-587 的纪律「**先把 running 写盘，再发请求**」正是作者对"不可撤销"的承认。⇒ `dry_run=False` 一旦下发，只能等它跑完。
- **⚠️ 实现陷阱（新发现）**：现 `dispatch()` 在 dry 判断（:589）**之前**就调了 `gen_id()`（:579），而 `gen_id` 会 `_save` 计数器（:115）→ **连 dry-run 都会消耗编号、写 id_counter.json**。MCP 版 `dry_run` **必须避免这个副作用**（只读号池预览、不 +1），否则"预演"会污染真实编号序列。

**建议的 dry_run 返回契约**（与 task-17 的 `_gate()` 形状一致，network/mcp_server.py:104-124）：
`{ok, dry_run: true, would_call: {method, path, body}, preview: {account, script_id, gates, free_slots, dedup_skipped, next_run}}`

### 11.6 建议的落地顺序（若 Lead / 用户拍板走 A）

1. **先修判据**（§11.3）—— 不做这步，搬过去也是错的。
2. 只读工具：`mas_scripts` / `mas_script_users` / `mas_queues` / `mas_queue_times_get`（+ 已有 `mas_history` / `mas_snapshot`）。
3. **`mas_schedule_state`**（合成视图，§11.2 修正 1）。
4. 写工具：`mas_dispatch_start`（**内部强制三道闸**）、`mas_queue_times_set`；两者都 `dry_run=True` 默认。
5. **不要**把 `mas_daily_state` 当既成事实暴露；要么按 §11.3 第 2 条改成动态判据，要么明确标"数据源已停摆"。
6. 层一保留；只在 §11.4 的互斥判据上做文章。

### 11.7 本节新增的未决问题

7. `data/daily_state` 的**原始写入方是哪个文件**？本报告只能证明"当前工作树里没有"。若 git 侧能查到历史版本 / 删除记录，请 Lead 用 rollback tag `pre-prune-20260926` / archive commit `e71c14e` 核一下 —— 这两个值来自 docs/audits/2026-09-26_prune_manifest.json:3-4。**git 由 Lead 独占，我未执行任何 git 命令。**
8. 层一当前**是否仍在生效**（时间表是否还在 MAS 上、`TimeEnabled` 是否为 true）—— 需一次**只读**的 `POST /api/queue/time/get` 实测（本轮未发任何 HTTP 请求）。
9. Hermes 接手后，层二的**运行频率 / 触发时机**由谁驱动？（现在是"有空位就叫号"，出处 docs/SCHEDULER.md:44：「触发时机是**"有空位"**，不是"时间到了"」—— 先前误记为 tools/scheduler.py:44，已更正）

---

*本报告为只读分析产物；实现（若走 A）需另开任务，且必须在实现后重跑 §9 的核对与 docs/POSITIONING.md 的行号复核。*
