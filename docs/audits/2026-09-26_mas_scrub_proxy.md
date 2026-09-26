# MAS 脱敏代理：Hermes 全量控制 MAS 且响应体不泄凭据

> 任务 **task-25** ｜ 2026-09-26 ｜ 执行 health-gater ｜ 落盘 tools/mas_scrub_proxy.py + 本文
> **未改** C:\Users\xiach\AppData\Local\hermes\config.yaml（由 Lead 应用，见 §7/§8）；未执行任何 git 命令；
> 全文与本代理的输出都**不含任何真实凭据值**（只打印字段名与布尔/计数）。

## 0. 一句话结论

新交付一个 **stdio MCP 代理**：对外（Hermes）就是一个 MCP server，工具面与 AUTO-MAS 官方 MCP
**完全一致**（实测 118 个），内部把请求转发给官方 MCP，并在**序列化之前**递归脱敏响应体里命中
password / sklandtoken / token / secret / credential / cookie 的**值**（换成 <scrubbed>，键名与结构保留）。
于是可以**去掉** config 里那把削掉合法脚本操作的 tools.exclude，同时不把账号口令送进 LLM 上下文。

自检结论：python tools/mas_scrub_proxy.py --check → **RESULT: PASS (tools=118)**。
关键差分：同一 scriptId 调官方 MCP 的 get_user_api_scripts_user_get_post，**上游直连**响应里 2 个敏感字段
（其中 1 个非空）；**经代理**后 2/2 全部为占位符，且 Info 的 28 个键集合与非敏感字段一个没少。

## 1. 交付物

| 文件 | SHA256 | 规模 |
|---|---|---|
| tools/mas_scrub_proxy.py | 4C4CDE8AF6C104F113125AED1744828EDA96EDE161A7E89ABDF089F3EDCCD2AD | 24673 B / 485 行 |
| docs/audits/2026-09-26_mas_scrub_proxy.md | 本文 | 接线 + 备份/校验/回滚 + 证据 |

只依赖 mcp 官方 SDK（mcp.server.lowlevel.Server / mcp.server.stdio / mcp.client.streamable_http），
**不依赖 fastmcp**，与仓内其它 MCP 实现解耦。

## 2. 数据流

    Hermes (MCP 客户端)
      --stdio--> tools/mas_scrub_proxy.py  --streamable-http--> http://MAS_HOST:36163/mcp
                   ^ tools/list 原样转发（工具名不变 → Hermes 侧既有用法/提示不受影响）
                   ^ tools/call 参数原样转发（validate_input=False，合法性交上游判定）
                   ^ 响应对象先 scrub、再交给 SDK 序列化   ← "序列化之前脱敏"的落点

## 3. 脱敏规则（对照硬性要求）

| 要求 | 落点（tools/mas_scrub_proxy.py） |
|---|---|
| 递归遍历任意深度 dict/list | scrub() :111 |
| 命中键名的值 → 固定占位符，保留键名与结构 | 同上（命中即整值替换，含容器） |
| content[].text 里内嵌 JSON 字符串：先解析脱敏再回填 | scrub_text() :128（json.loads → scrub → json.dumps） |
| 非 JSON 纯文本 | 只对 "敏感键名": 值 这种 JSON 片段做正则兜底；普通文本原样不动 |
| 结构化/非文本 content | scrub_result() :151 对非文本块走 model_dump → scrub → model_validate |
| structuredContent | scrub_result() :151 单独过一遍 |
| isError 分支 | content/structuredContent 一视同仁过 scrub；isError 本身不改写 |
| 序列化之前 | scrub 发生在返回 types.CallToolResult 之前，SDK 只负责序列化 |
| 留痕但不泄值 | 命中时 stderr 一行：[scrub] tool=<name> redacted=N keys=['Password','SklandToken']（无值、无长度） |

## 4. 配置（环境变量优先，本文件不复写 IP）

| 变量 | 作用 | 缺省 |
|---|---|---|
| MAAORCH_MAS_MCP_URL | 上游 /mcp 地址 | services/mas_l3.py 的 API 常量 + /mcp |
| MAAORCH_MAS_MCP_TIMEOUT | 单次上游调用超时（秒） | 30 |
| MAAORCH_MAS_SCRUB_KEYS | 追加敏感键名（逗号分隔，大小写不敏感） | 空 |

## 5. 挡得住什么 / 挡不住什么（**这是本代理的边界，别当它是安全边界**）

**挡得住**
- tools/call 响应体里的凭据值：递归 + 内嵌 JSON + structuredContent + isError 分支全覆盖（§3）。
- 键名与结构保留 → 上层解析不会坏（差分实测：Info 28 个键一致、非敏感字段全在）。

**挡不住**
- **调用本身**这个事实：谁能经本代理调用，谁就拿到全量控制权（118 个工具里 44 个写/危险，
  例如 close_api_core_close_post 关后端、脚本配置 CRUD、导入导出/配置分享）。
- **请求方向**：参数原样转发、不脱敏（改口令类写操作里你提交的新口令照样会发出去）。
- 上游日志与网络层：MAS 自己的日志、Tailscale 段内**别人直连 36163** 的流量，本代理管不到。
- 非键值形态的凭据：没有引号/键名的纯文本口令、base64 载荷、工具描述等元数据。
- 暴露面：上游 118 个工具一个不少（只是返回值变干净），没有收窄可调用的动作集合。

**因此仍建议**：Tailscale ACL / Windows 防火墙限定源 IP；尽快给 MAS 前置带 token 的反向代理。

## 6. 自测证据（可复现）

命令：cd D:\1-2\MAAOrch 然后 python tools/mas_scrub_proxy.py --check  → 退出码 **0**

- **[A] 本地脱敏桩（不连网，11 项全 PASS）**：合成 payload 覆盖 递归（/data/u1/Info/Password、
  Nested/Token）、内嵌 JSON text、含 JSON 片段的非 JSON text、structuredContent、isError=True；
  断言「合成密文一个都没出现在输出里」「无敏感键的纯文本原样未动」；另 2 项离线错误契约
  （上游抛错时处理器自己返回结构化错误，不抛到 transport 层）。桩只打印 hit key=Password redacted=True 这类行。
- **[B] stdio 握手（经代理子进程）**：initialize → serverInfo=mas-scrub-proxy；
  tools/list → **118 个工具**；目标工具 get_user_api_scripts_user_get_post 在清单里。
- **[C] 端到端差分断言**（同一 scriptId，上游直连 vs 经代理）：
  - 上游直连：命中敏感字段 2 个（非空 1 个）→ 证明这次检查**不是空转**
  - 经代理：命中 2 个、**已脱敏 2 个**（100% 占位符）
    /data/<uid>/Info/Password  key=Password  is_placeholder=True
    /data/<uid>/Info/SklandToken  key=SklandToken  is_placeholder=True
  - 敏感字段路径集合一致；Info 键集合一致（28）；isError 未被改写
  - 代理子进程 stderr：[scrub] tool=get_user_api_scripts_user_get_post redacted=2 keys=['Password', 'SklandToken']
- **[D] 上游不可达降级**（URL 指死端口 http://127.0.0.1:9/mcp）：
  initialize 仍成功（不拖垮握手）；tools/list **显式报错**（McpError，不静默返回空表）；
  tools/call 返回 isError=True 的结构化错误；**失败后连打两次仍不卡死**。
- **不误伤（负例）**：经代理调无敏感键的工具 get_script_api_scripts_get_post（返回 Info 只有 Name/Path）→
  返回体里 **没有** <scrubbed>、子进程也**没有** [scrub] 行 → 证明脱敏只命中敏感键名，不做全量改写。
- 编译：python -m compileall -q . → **exit=0**。
## 7. Hermes 接线片段（**由 Lead 应用，我不改**）

现状（config.yaml:233-246）：auto-mas 直连 http://100.79.173.69:36163/mcp + tools.exclude
['*_scripts_*','*_webconfig_*']，enabled: true。改成：直连条目 enabled: false（保留做一键回滚），
新增 stdio 条目走本代理。

    # 直连改为停用（保留条目便于回滚）；在 ACL/token 反代落地前不要重新启用
    auto-mas:
      url: http://100.79.173.69:36163/mcp
      tools:
        exclude:
          - '*_scripts_*'
          - '*_webconfig_*'
      enabled: false
    # 新：经本仓脱敏代理（stdio）。工具面与官方一致，响应里的敏感值已被替换
    auto-mas-scrubbed:
      command: C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe
      args:
        - D:\1-2\MAAOrch\tools\mas_scrub_proxy.py
      env:
        MAAORCH_MAS_MCP_URL: http://100.79.173.69:36163/mcp
        PYTHONUTF8: "1"
        PYTHONUNBUFFERED: "1"
      enabled: true

要点：
- **届时可以去掉 tools.exclude** —— 脱敏改由代理侧承担，脚本配置类操作因此恢复；
  我把黑名单留在**已停用**的直连条目上做二次防线（万一有人重新启用直连）。
- **不要同时启用两条**：同一批 118 个工具名会在 Hermes 工具表里重复。
- command 必须是装了 mcp 包的 python（本机实测 C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe）。
- env 不放任何凭据（MAS 无鉴权，不需要）；换机/换端口只改 MAAORCH_MAS_MCP_URL。
- MAS 重操作（导入导出等）可调大 MAAORCH_MAS_MCP_TIMEOUT（缺省 30s）。

## 8. 备份 / 校验 / 回滚（Lead 操作清单）

    # 0) 现状与哈希
    $cfg = "C:\Users\xiach\AppData\Local\hermes\config.yaml"
    (Get-FileHash $cfg -Algorithm SHA256).Hash
    python -c "import yaml;d=yaml.safe_load(open(r'C:\Users\xiach\AppData\Local\hermes\config.yaml',encoding='utf-8'));s=d.get('mcp_servers') or {};print(sorted(s));print('auto-mas.enabled=',s['auto-mas'].get('enabled'))"

    # 1) 备份（沿用该目录既有 .bak 习惯；Lead 已有 config.yaml.bak-maaorch-20260926-160017）
    $bk = "$cfg.bak-t25-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item $cfg $bk -Force; (Get-FileHash $bk -Algorithm SHA256).Hash

    # 2) 合并（用 yaml 读写，别手改缩进）。注意：这里刻意用单行 python -c，
    #    因为 markdown 的 4 空格缩进会让 python - <<'PY' 多行脚本报 IndentationError。
    python -c "import yaml;p=r'C:\Users\xiach\AppData\Local\hermes\config.yaml';d=yaml.safe_load(open(p,encoding='utf-8'));m=d.setdefault('mcp_servers',{});m.get('auto-mas',{}).update({'enabled':False});m['auto-mas-scrubbed']={'command':r'C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe','args':[r'D:\1-2\MAAOrch\tools\mas_scrub_proxy.py'],'env':{'MAAORCH_MAS_MCP_URL':'http://100.79.173.69:36163/mcp','PYTHONUTF8':'1','PYTHONUNBUFFERED':'1'},'enabled':True};yaml.safe_dump(d,open(p,'w',encoding='utf-8'),allow_unicode=True,sort_keys=False);print('written')"

    # 3) 校验
    python -c "import yaml;d=yaml.safe_load(open(r'C:\Users\xiach\AppData\Local\hermes\config.yaml',encoding='utf-8'));s=d['mcp_servers'];print('auto-mas.enabled=',s['auto-mas']['enabled']);print('scrubbed cmd=',s['auto-mas-scrubbed']['command'])"
    python D:\1-2\MAAOrch\tools\mas_scrub_proxy.py --check          # 期望 RESULT: PASS

    # 4) 生效 + 会话内验证（重启 gateway 之后）
    #    a) 让 Hermes 调 get_script_api_scripts_get_post / get_user_api_scripts_user_get_post，
    #       在返回里搜 <scrubbed>：应命中；不要人工去比对真实口令（只看占位符在不在）
    #    b) 确认 script/webconfig 类工具这次没有被 exclude 掉（这正是本次要恢复的能力）

    # 5) 回滚（任选）
    Copy-Item $bk $cfg -Force            # 整文件还原（推荐）
    # 或：把 auto-mas.enabled 改回 true、删掉 auto-mas-scrubbed，再重启 gateway

注意：跑 --check 的 [B]/[C] 需要 MAS 开着；MAS 关着时 [A]/[D] 仍会跑，退出码为 1。

## 9. 实测补充：仓内硬编码会误导后来人（Lead 要求写进报告）

| 事实（2026-09-26 实测） | 仓内现状 | 误导点 |
|---|---|---|
| /api/queue/get 实返 **25** 个队列 | tools/auto_mas_queue_times.py:19-29 硬编码 9 队（队列1..9） | 按 9 队写循环会漏配 16 个队列 |
| /api/scripts/get 实返 **54** 个脚本 | tools/scheduler.py 另有 data/schedule/script_map.json 缓存 | 任何"脚本数量"假设都不可靠，必须动态读 |
| /api/queue/time/get 每队列实测 **1** 个班次（05:00） | tools/auto_mas_queue_times.py 默认计划 早 05:00 + 晚 19:30 | 别假设现状是两班；改班次前先读现状 |

只读复现（REST）：

    python -c "import json,urllib.request as u;b='http://100.79.173.69:36163';P=lambda p,d:json.loads(u.urlopen(u.Request(b+p,data=json.dumps(d).encode(),headers={'Content-Type':'application/json'},method='POST')).read().decode());print('queues',len(P('/api/queue/get',{'queueId':None}).get('data') or {}));print('scripts',len(P('/api/scripts/get',{'scriptId':None}).get('data') or {}))"

## 10. 未做 / 边界

- **未改 hermes/config.yaml、未重启 gateway**（§7/§8 交 Lead 应用）。
- 代理自身**无鉴权、无审计文件**：stdio 的权限 = 启动它的宿主进程的权限；防护靠"谁能改宿主 config"
  （只在命中时向 stderr 打字段名，不打值）。
- 只挡**响应**；请求参数原样转发（见 §5）。
- **未做上游会话复用**：每次请求独立握手（约多 1-2 次 RTT，换"不串会话、不卡死"）；嫌慢可加缓存，需另评审。
- resources / prompts 面未转发（AUTO-MAS MCP 只提供 tools）。
- --check 会在**本进程内**短暂持有未脱敏响应用于差分对比，但只输出字段名/布尔；
  不要把 --check 进程的内存/调试器输出外传（命令输出本身不含值）。
- 不做"值形态"启发式脱敏（例如扫描任意 6-8 位字符串）：会误伤，明确不做。

## 11. 复现命令汇总

    cd D:\1-2\MAAOrch
    python -m compileall -q .                        # 期望 exit=0
    python tools\mas_scrub_proxy.py --check          # 期望 RESULT: PASS (tools=118)
    python tools\mas_scrub_proxy.py                  # 直跑 = stdio server（等 MCP 客户端连）
    # 指定上游 / 超时
    $env:MAAORCH_MAS_MCP_URL="http://<mas-host>:36163/mcp"; $env:MAAORCH_MAS_MCP_TIMEOUT="60"; python tools\mas_scrub_proxy.py --check

---

*本文与代理实现均不含真实凭据值；复核"是否真的挡住了"只需跑 §6 的 --check（它自己做差分断言）。*
