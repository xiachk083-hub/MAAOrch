# 独立验证汇总与更正（2026-09-26）

用 6 路「不知情」代理对本次会话的既有结论做对抗性复核。目的不是确认我对，而是**找出我错在哪**。
本文件是**权威更正记录**：与任何既有审计报告冲突时，以本文件为准。

## A. 被证实为正确的

| 结论 | 复核方式 | 证据 |
|---|---|---|
| G3 health 杀进程闸 = **6 处** | 独立计数 | services/runtime_health.py:172,198,247,288,326,353（解析在 :129，_kill_gate :94-108） |
| G5 emit_finished 逐订阅者 try/except | 独立读代码 | services/runner.py:295-304；remove_dispatch :3175-3179（connect-only :3151-3160） |
| main_web.pyw 确实注册完成回调 | 补扫 .pyw（此前只扫 .py 误判） | main_web.pyw:183（另有 :195） |
| bind 缺省 = 127.0.0.1，显式 0.0.0.0 仍生效 | 独立读代码 | models/config_manager.py:18；main_web.pyw:336→:347 |
| 业务路由 = **49 条** | AST/正则双法 | @app. 共 50，:200 是 @app.middleware 不算路由 |
| MCP 工具 = **41 个**（只读 25 + 写 16） | AST 独立计数 | network/mcp_server.py |
| 旧 P0 路由已消失 | 全仓检索 | fname:path / screenshots/file 代码 0 命中；仅注释 api_fastapi.py:2196 |
| 35 个静态 REST 路径 → 不可达 0 | 与 49 路由比对 | /api/queue/{action} 已被 :215 限定 pause|resume |
| 修复后穿越入参全被拒 | 真实 app + 仓库外诱饵 | 6 种入参全 400，leak=False |

## B. 更正（此前说法是错的）

### B1. ★ 漏检一处 P1 路径穿越（最严重）
- **错在哪**：早期发现 P0（GET /api/screenshots/file/.../{fname:path}）后，前端退役删掉了那条路由，
  我**据此认为穿越议题随之解决**，没有检查同族残留。
- **实际**：**保留**路由 GET /api/depot/plan 的 query 参数 account_id 在 api_fastapi.py:725 直接拼路径、
  :729 只做 exists()。实测 \`account_id=../../..\` 解析到 **D:/1-2/plan.json**（仓库外）。
  后缀固定为 plan.json，故非原 P0 级，但确为任意同后缀文件读取。
- **已修**：拒绝分隔符/NUL/相对段 + resolve() 后断言落在 accounts 目录内（api_fastapi.py:725-737）。
- **已锁**：tests/test_depot_plan_traversal.py（13 项，含"朴素拼接会逃出目录"的向量固定断言）。

### B2. 「写工具 dry_run 默认 True」作为通称是错的
- 41 个工具里写 16 个，其中 **5 个根本没有 dry_run 形参**，一调即下发：
  mcp_server.py:199 enqueue / :208 dequeue / :213 queue_control / :220 smart_dispatch / :231 probe_stage。
- 另 11 个有 dry_run 且**全部默认 True**（:280/:290/:327/:378/:403/:450/:469/:514/:527/:540/:559）。
- 文档 mcp_server.py:23-27 只点名覆盖 9 个工具，**措辞本身是准确的**；错的是我反复转述的那句通称。
- 更正口径：**「11 个带 dry_run 的写工具默认 True；另有 5 个写工具无 dry_run，调用即生效」**。

### B3. 「7 个破坏性工具」是口径依赖的
- 按「中断/杀进程」口径恰为 7：kill_audit_decide / emulator_control / emulator_batch /
  emu_connect_stop / emu_connect_restart / account_stop / maa_stop_all。
- 但 mas_cleanup（services/mas_adapter.py:197-207 **真删**远端临时队列）与
  mas_rerun（:172-194 **真发车**）按「删除/改远端状态」口径应计入 ⇒ **应为 9**。
- 引用该数字时**必须带口径**，否则是误导。

### B4. docs/audits/2026-09-26_doc_drift_cleanup.md:13,102 「未标注的残留漂移 = 0」是错的
- 反例：docs/http-api.md:497 的 GET /api/logs?lines=100 没有失效横幅，而紧邻 :514 /api/maa/log 有（:516）。
- /api/logs 不在 49 条路由内；而同日的 frontend_retire_cleanup.md:126 曾把它正确列为已删端点
  ⇒ **两份 dated 审计互相打架**。
- **已修**：http-api.md:497 补上失效横幅，并注明 Node 门面（19999）另有原生 /api/logs（maaorch-web.mjs:934）。

### B5. docs/audits/2026-09-26_glue_verify.md:152 「本仓无代码给 _finished_callbacks 注册」是错的
- 反例：main_web.pyw:183 / :195。p2 已自纠，**p1 正文未改** —— 读 p1 的人会被误导。

### B6. docs/audits/2026-09-26_bind_and_exposure.md:206 的 H1 引用已失效
- 它引的 api_fastapi.py:1751 handle_get_config 回显已随 GET /api/config 删除而不存在，
  只剩 start_server 缺省（api_fastapi.py:2240）。

### B7. docs/audits/2026-09-26_doc_drift_cleanup.md:61 表述不准
- 称历史缺省 19999「已不再指代本 API」，但**代码缺省仍是 19999**
  （models/config_manager.py:18、main_web.pyw:325）；只有运行值 config.json=19997。

### B8. ★ 我关于「那个变异测试抓不到」的说法**是错的**（自我更正）
- **我此前的说法**：把路由装饰器由 `{name}` 改成 `{account_name}`（函数签名仍 `name`）会导致调用 422，
  而「**存在性断言照样通过**」。
- **实际复核（task-15 独立验证）**：该变异**会被抓住** —— 白名单存的是精确模板 `{name}`，
  改掉即 2 failed。我的结论对**最终测试文件**不成立。
- **真正的漏网反例**（验证方给出）：路径与方法都不变，只把 `def handle_health()` 改成
  `def handle_health(token: str)` —— whitelist 单跑 71 passed、全套 123 passed，
  而 `GET /api/health` 实际返回 **422**。
- **准确表述**：这套测试**能拦结构回归（增删/改名路由），拦不住行为回归（可直接调用的处理器被改成不可用）**。
- 结论不变但理由要改：测试不是重言（删路由**确实**会失败，已用注释掉 :850 的 /api/health 证伪重言假设，3 failed），
  它的问题是**覆盖面**而非**有效性**。

### B9. 测试与本仓的其它确证弱点（均由独立验证给出）
- `tests/test_routes_whitelist.py:280` 的 MCP 可达断言**丢弃了 HTTP method**，
  且 `_norm_path`（:153-156）把 `{}` 折叠 —— 方法与模板层面的差异检测不到。
- `RETIRED_MUST_BE_GONE` 只覆盖被删 91 条中的 **16 条**（其余 75 条删除后无断言保护，可能被悄悄加回来）。
- `test:200` 与 `test:278` 存在**可静默 skip** 的路径。
- 悬挂引用（生产文件指向已删文件）：`ui/control_server.py:690-691` 仍服务于已删的 `ui/index.html`
  （是优雅回退，但现已永久走 404 分支）；`runtime/_t_mas_render.js:4` 读已删的 `ui/web/auto_mas.js`。
- 被删 91 条的全仓差集（扫 780 个代码文件）：**无生产调用方被打破**；真实 404 仅
  `debug/*/_monitor_floating.py` 调已删的 `/api/system/close_popups`（debug 路径，且 debug/ 不受版本控制）。
  注意子串误报：`/api/config` 会命中 `/api/config_backup`。

### B10. ★ 一条**误报**：『只读闸根本没接线』（F1）—— 不成立
- **验证方结论**：查 Hermes 配置只有 auto-mas-scrubbed、无 auto-mas-monitor，故只读设计空转，并称
  docs/audits/2026-09-26_inspection_run_verify.md:9 与 docs/HERMES_MAS_HANDOVER.md:23『文档错了』。
- **我的复核（不采信也不驳回）**：验证方查的是**全局 config.yaml（默认 profile）**；
  我把只读条目接在 **mas profile** 上。实测：
  · profiles/mas/config.yaml 的 mcp_servers 含 **auto-mas-monitor**，其 env **MAAORCH_MAS_READONLY="1"**
  · 同文件 platform_toolsets.cron = **["auto-mas-monitor"]**
  · hermes -p mas mcp test auto-mas-monitor -> ✓ Connected, Tools discovered: **10**
  · 真实巡检运行自报 **MCP_API_TOOL_COUNT=10 / ISOLATION=PASS**
- **裁定：F1 是误报**（查错了 profile），只读闸确实已接线且在运行中生效。
  教训：跨 profile 的配置要指明 profile；验证结论必须能指出**具体文件路径**才算证据。

### B11. 已确认并已修的代理缺陷（F2 / F3 / F7 / F9）
| 编号 | 问题 | 处置 |
|---|---|---|
| F2 | 『拒绝 = 零上游调用』**为假**：SDK 在 tools/call 前无条件预热工具缓存，只读模式隐藏了工具导致永远 cache miss，**每次被拒调用都先向上游发一次 initialize+tools/list**；且拒绝原因被掩盖成『上游不可达』 | **已修**：只读模式缓存上游工具清单 + 启动预热。我端到端复验：被拒调用上游新增 **[]**，返回体为 `readonly mode: tool not allowed / 命中危险动词` |
| F3 | 上游异常消息原样回显（`str(e)`），异常里带敏感键即外泄 | **已修**：错误消息同样过 `scrub_text` |
| F7 | 键名盲区：`pass_word`/`pwd`/`passwd`/`apiKey`/`api_key`/`Authorization`/`auth_key`/`authkey`/`private_key`/`session_id` 均**不在**默认敏感集合，其值原样外泄 | **已修**：并入 DEFAULT_SENSITIVE（代价见下） |
| F9 | `MAAORCH_MAS_READONLY=""` 是 **fail-open 到全量**，而文档宣称『其它任何值 fail-closed』，自相矛盾 | **已修文档**：逐条写明取值语义，明确指出**空串 = 全量**，别把空串当安全默认 |

新增测试 tests/test_mas_proxy_hardening.py（**30 项**，全部针对上述四条的原测试盲区）。

### B12. 代理的已知遗留（未修，明确记录）
- **F4**：`scrub_text` 只处理一层 JSON；`text` 内嵌的**字符串里再套 JSON** 不再下钻，转义引号也能躲过兜底正则。
- **F5**：非文本 content 的 `resource.text` 只按键名递归，内嵌 JSON 不脱敏。
- **F6**：text content 的**额外未知字段**原样穿过（`model_copy` 只改 text，模型 extra='allow'）。
- **F8**（有意取舍）：子串匹配会把 `token_count` 这类**计数键**也脱敏成字符串，打断数字消费方。
  这是安全闸的保守取舍（宁可多脱不可漏脱），用 `MAAORCH_MAS_SCRUB_KEYS` 可覆盖说明。
- **F10**：实际还接受 enable/enabled/disable/disabled（文档原先未列，现已写明）；
  `SENSITIVE` 在导入期固化而 `_kv_re` 每次读 env，两者不一致。
- 重要限定（验证方与我都确认）：**没有任何绕过能让写调用打到上游** ——
  handler 级精确匹配对大小写、首尾空格、相似名、加后缀全部拒绝。F1-F10 里没有数据完整性风险，
  风险集中在**凭据外泄的奇特包装形态**与**语义表述**上。

## C. 方法论教训（这是本次最大的收获）

前面的验证大多是「**复核我刚做完的那件事**」——存在性断言、签名比对、白名单计数。
这类验证**结构上不可能发现"没人划进范围的问题"**：35 个任务里，
**没有任何一条**被指定去审"保留路由的路径拼接安全性"，所以 B1 一直活着。

真正捞到东西的是两种问法：
1. **对抗性**：「试着打穿它」而不是「确认它好用」；
2. **新切面**：「这条同族残留还在吗」而不是「我删掉的那条还在吗」。

## D. 验证回收情况

- ✅ 路径穿越残留（本文件 B1）—— 已修 + 已加回归测试
- ✅ MCP 41 工具面（B2/B3）
- ✅ 既有审计结论抽查（B4-B7）
- ✅ 前端退役 + 测试有效性（B8/B9）
- ⏳ 脱敏代理的对抗性测试（能否绕过脱敏/只读闸）
- ⏳ 遗留缺陷清单核实（rank_service 悬空引用、非原子写、BOM、日期、scratch 文件）
