# 2026-09-26 对抗性裁决：tools/mas_scrub_proxy.py（task 目标：打穿它）

**结论**：常规响应体的凭据能挡住；但"拒绝 = 零上游调用"**不成立**（每次拒绝都先打一次上游握手），脱敏有 6 条实证绕过；只读闸在**当前真实部署里根本没接线**。
挡得住：深嵌套 / structuredContent / isError / 键名大小写变体 / content[].text 是 JSON 对象 / 非 JSON 文本里的 `"敏感键": "值"` 标量片段 / 精确名匹配（大小写、首尾空格、相似名、加后缀全部拒绝，handler 级零上游）。
挡不住：上游异常回显、JSON-in-JSON-string、非文本 content 内嵌文本、content 额外字段、键名盲区、非 JSON 文本里数组/对象值。

**复现通用形式**（不新增文件，stdin 喂脚本；全程离线，不连 MAS）
```
cd D:\1-2\MAAOrch
$code = @' <片段> '@; $code | python -
```
片段用 importlib 按路径加载 tools/mas_scrub_proxy.py，下面每条给可复现命令（SYNTH-* 是合成值）。

## 发现（按严重度）

**F1【高·部署】只读闸未接线，实际不生效**。Hermes 配置里只有 `auto-mas-scrubbed`（全量控制，enabled=True，env 仅 URL/UTF8/UNBUFFERED，**无 MAAORCH_MAS_READONLY**），**不存在 `auto-mas-monitor`** 条目；全仓 grep `MAAORCH_MAS_READONLY` 只出现在 docs/tests/tools，没有任何配置写它。
复现：`python -c "import yaml;d=yaml.safe_load(open(r'C:\Users\xiach\AppData\Local\hermes\config.yaml',encoding='utf-8'));print([k for k in (d.get('mcp_servers') or {}) if 'mas' in k])"` → `['auto-mas','auto-mas-scrubbed']`
**文档错了**：docs/audits/2026-09-26_inspection_run_verify.md:9 与 docs/HERMES_MAS_HANDOVER.md:23 声称 `auto-mas-monitor` + `MAAORCH_MAS_READONLY=1` 已存在 —— 反例即上面的命令输出。

**F2【高·语义】"在触达上游之前拒绝（零上游调用）"为假**。mcp SDK 在 `tools/call` 里**无条件**先调 `_get_cached_tool_definition`（site-packages/mcp/server/lowlevel/server.py:531），缓存 miss 时回调 **我们的** `list_tools` handler（server.py:487-490）→ `_upstream_list_tools()`。只读模式把隐藏工具从 tools/list 过滤掉，所以隐藏名**永远** cache miss → **每次被拒的调用都先发一次上游 initialize+tools/list**。
证据（本地计数 socket 当 `MAAORCH_MAS_MCP_URL`，stdlib 客户端经代理）：initialize 后上游请求数=0；每次 `call_tool`（写类/隐藏 get/大小写变体/白名单读）后上游新增 `['initialize']`，客户端拿到的是 `MAS MCP 上游不可达（...）` 而**不是** readonly 拒绝体。上游存活时 handler 仍会拒绝（无写外泄），但：① 上游挂时拒绝原因被连接错误替换；② 每次拒绝都产生真实上游流量。
**测试为何漏掉**：tests/test_mas_proxy_readonly.py:15 的"网络绊线"只绊 `_upstream_call_tool`，且用例直接调 `proxy._handle_call_tool`，绕过了 server.py:531 这一层。修法：只读模式启动时覆盖实例方法 `server._get_cached_tool_definition = async lambda name: None`（validate_input=False 用不到返回值）。

**F3【中·脱敏】上游异常回显不脱敏**。mas_scrub_proxy.py:336-341 把 `str(e)` 原样塞进返回体，不经 scrub。
复现：`python -c "import importlib.util as u,asyncio;sp=u.spec_from_file_location('p','tools/mas_scrub_proxy.py');m=u.module_from_spec(sp);sp.loader.exec_module(m);m._upstream_call_tool=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('{\"Password\":\"SYNTH-A1\"}'));r=asyncio.run(m._handle_call_tool('get_script_api_scripts_get_post',{}));print('SYNTH-A1' in r.content[0].text)"` → `True`
（实证：本地桩异常消息里的 Password/SklandToken 原样出现在返回体）

**F4【中·脱敏】JSON-in-JSON-string 与"text 本身是 JSON 字符串"漏**。scrub_text:242 只在 `json.loads` 结果是 dict/list 时递归；值是字符串时不再下钻；转义引号 `\"Password\"` 又躲过 _kv_re:176。
复现：`python -c "import importlib.util as u,json;sp=u.spec_from_file_location('p','tools/mas_scrub_proxy.py');m=u.module_from_spec(sp);sp.loader.exec_module(m);import mcp.types as t;inner=json.dumps({'Info':{'Password':'SYNTH-A1'}});r=t.CallToolResult(content=[t.TextContent(type='text',text=json.dumps({'data':inner}))]);print('SYNTH-A1' in json.dumps(m.scrub_result(r).content[0].model_dump()))"` → `True`
**文档错了**：:13 声称覆盖"content[].text 内嵌的 JSON 字符串"，只覆盖一层，两层即漏。

**F5【中·脱敏】非文本 content 的内嵌 JSON 文本不脱敏**。scrub_result:266 对非 text 块只按"键名"递归，`resource.text` 整串不动。
复现：把 `types.EmbeddedResource(type='resource', resource=types.TextResourceContents(uri='mas://x', mimeType='application/json', text=json.dumps({'Password':'SYNTH-C3'})))` 交给 `scrub_result` → 返回值仍在（同理 BlobResourceContents 的 base64，文档 :20 已承认）。

**F6【中·脱敏】text content 的额外（未知）字段原样穿过**。:262-263 走 `model_copy(update={'text':...})` 只改 text；mcp.types 模型 `extra='allow'`，上游 content 块里的任意多余字段被完整保留。
复现：`python -c "import importlib.util as u,json;sp=u.spec_from_file_location('p','tools/mas_scrub_proxy.py');m=u.module_from_spec(sp);sp.loader.exec_module(m);import mcp.types as t;r=t.CallToolResult.model_validate({'content':[{'type':'text','text':'ok','Password':'SYNTH-A1'}]});print('SYNTH-A1' in json.dumps(m.scrub_result(r).content[0].model_dump()))"` → `True`（实测保留的额外键：Password、token）

**F7【中·脱敏】键名盲区**。DEFAULT_SENSITIVE:70 只有 password/sklandtoken/token/secret/credential/cookie（子串匹配）；`pass_word`、`pwd`、`apiKey`、`Authorization`、`auth_key` 原样返回（合成值 A/E/F 全部外泄）；`accessToken` 因含 token 才被命中。

**F8【低·正确性】过度脱敏改类型**。`token_count: 42` → `'<scrubbed>'`（int→str），_kv_re:176 兜底把数字也换成带引号字符串；`secret_count` 同样中招。会打断把该字段当数字用的消费方。

**F9【低·开关】空值 fail-open，且文档自相矛盾**。:138-139 `raw is None or not strip()` → False（全量）；实测 `MAAORCH_MAS_READONLY=''` 时写类工具被**原样转发**（upstream_calls=1）。而 :30 写"其它任何值 fail-closed"，:91 写"未设置/空 -> False"——两句冲突。配置模板漏值即静默降级为全量。

**F10【低】其它**：:96-97 实际接受 enable/enabled/disable/disabled（文档 :28 未列）；SENSITIVE 在 import 期固化（:80）而 _kv_re 每次读 env（:173），运行期改 MAAORCH_MAS_SCRUB_KEYS 会让 dict 路径与文本路径判定不一致；非 JSON 文本里敏感键的值是数组/对象时不脱敏（_kv_re 只匹配字符串/数字/bool/null），Unicode 转义键名 `\u0050assword` 也漏。

## 对四个问题的直接回答
1. 常规形态**挡得住**（深嵌套、structuredContent、isError、大小写、text 是 JSON 对象、标量片段）；非常规形态**挡不住**：F3/F4/F5/F6/F7。
2. 有绕过路径，共 6 条：F2（零上游承诺假）、F3、F4、F5、F6、F7；最小复现已逐条给出。无一条能让**写调用**打到上游（只读 handler 的精确匹配是有效的），F2 只是把"零上游"证伪并把拒绝原因吞掉。
3. 环境变量：1/true/yes/on/enable/enabled → 只读；0/false/no/off/disable/disabled → 全量；其它垃圾值 → 只读（fail-closed ✅）；**空串/全空白 → 全量（fail-open ❌，F9）**。
4. 结论：**能挡住常规凭据外泄，但不是安全边界**——奇特包装（二次编码、非文本块、额外字段、异常回显）仍会把明文凭据送进 agent 上下文；且 F1 说明这道闸目前压根没开。

## 未覆盖 / 待验（不写结论）
- result 级 `_meta`：scrub_result:272 直接透传 `meta`，未做有效验证（我的探针未确认构造是否生效），勿引用。
- tests/test_mas_proxy_readonly.py 38 项全绿（`python -m pytest tests/test_mas_proxy_readonly.py -q` → 38 passed），未覆盖上面 F2-F7 的任何一条。
- 未触碰任何真实 MAS；未执行 git；本报告是本次唯一写入的文件。
