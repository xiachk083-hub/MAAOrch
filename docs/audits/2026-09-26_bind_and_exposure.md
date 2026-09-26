# 监听面收紧 + 41 个 MCP 工具破坏性暴露面评估

- 日期：2026-09-26 ｜ 执行：queue-fixer（task-23）｜ 项目：D:\1-2\MAAOrch
- 定位前提：MAAOrch = Harness/Hermes 与 MAA、MAS(AUTO-MAS) 之间的过渡粘合层；前端整体退役，Node 只留最小运维页 /status。
- 行号口径：以下行号均为**本次评估时点**（2026-09-26，未用 git）的文件现状。`network/mcp_server.py` 行号稳定（无他人 scope 占用）；`network/api_fastapi.py` 与 `docs/audits` 正被 task-14 / task-19 并发改动，行号可能漂移（见 §6）。

## 0. 结论（TL;DR）

1. **缺省绑定确为 0.0.0.0，且与既有文档自相矛盾**：`docs/http-api.md:15` 早就写着「监听范围：仅 `127.0.0.1`（可通过 `bind_address` 配置修改）」，但代码缺省是 `0.0.0.0`（`main_web.pyw:332`、`network/api_fastapi.py:3840`、`:1751`），`models/config.json` 里连 `bind_address` 键都没有。**已按任务把缺省收紧为 127.0.0.1（4 处，唯一代码改动）**，显式配置仍可放开（实测见 §2）。
2. **真正的短板不是绑定，而是 `api_token` 允许为空**：`network/api_fastapi.py:218` 的 `if path.startswith("/api/") and token:` 与 `network/mcp_server.py:80` 的 `if not self.token: return await self.app(...)` 两处短路 → token 为空时，REST 全部路由 + 41 个 MCP 工具（含 7 个破坏性写）在 0.0.0.0 上**完全无鉴权**。本机现状 `models/config.json` 的 `api_token` 非空（长度 32，未打印值），所以**今天**不是裸奔；但 `models/config_manager.py:18` 的缺省是 `"api_token":""` —— 新装 / 配置重置 / 换机即裸奔。
3. **41 工具分级：只读 25 / 写 9 / 破坏性写 7**（§3）。破坏性写全部代理既有 REST 写路由；`dry_run=True` 只是**防误触**，不是安全边界 —— 拿到 token（或 token 为空）后多传一个 `dry_run=False` 即真正执行。

## 1. 缺省绑定核实（改动前）

| # | 位置 | 改动前 | 是否影响实际监听 |
|---|---|---|---|
| 1 | `models/config_manager.py:10-35` `DEFAULT_CONFIG` | **无 `bind_address` 键** | 影响新装缺省（`load_config` 无 config.json 时 `return dict(DEFAULT_CONFIG)`，:123） |
| 2 | `main_web.pyw:332` `config.get("bind_address","0.0.0.0")` | `0.0.0.0` | **是**（:343 `uvicorn.Config(..., host=_bind, port=port)` 即实际绑定） |
| 3 | `network/api_fastapi.py:3840` `mw.config.get("bind_address","0.0.0.0")` | `0.0.0.0` | **否（死路径）**：`start_server()` 全仓 grep 只有定义处（:3839），无调用方 |
| 4 | `network/api_fastapi.py:1751` `GET /api/config` 回显 | `0.0.0.0` | 否（只影响回显，会误导使用者以为在听全网） |

实测（改动前）：

```
python -c "import json;d=json.load(open('models/config.json',encoding='utf-8'));print('has_bind_address=', 'bind_address' in d);print('api_port=',d.get('api_port'));print('api_token_len=',len(d.get('api_token','') or ''))"
has_bind_address= False
api_port= 19997
api_token_len= 32
```

同仓既有范式（缺省只听本机）：

| 位置 | 事实 |
|---|---|
| `ui/control_server.py:738-750` | `--host` 缺省 `127.0.0.1`；`--lan` 才等于 `0.0.0.0`；:756 非回环而 token 为空 → 拒绝 |
| `ui/control_server.py:752-759` | 局域网首次自动生成 token（`secrets.token_urlsafe(18)` → `ui/control_token.txt`） |
| `server/maaorch-web.mjs:1092` | Node 看板 `server.listen(PORT, '127.0.0.1')` |
| `server/maaorch-web.mjs:74` | 上游固定 `http://127.0.0.1:19997`（本机自调） |
| `main_web.pyw:53,122,125` | 自带入口全部 `127.0.0.1` |

## 2. 代码改动（唯一一项：缺省绑定收紧为 127.0.0.1）

| # | 文件:行 | 改动 |
|---|---|---|
| 1 | `models/config_manager.py:18` | `DEFAULT_CONFIG` 增 `"bind_address":"127.0.0.1"`（+ :16-17 注释说明局域网需显式配置） |
| 2 | `models/config_manager.py:93` | `load_config` v>=5 段 `data.setdefault("bind_address", DEFAULT_CONFIG["bind_address"])` —— 老 config.json 无此键时在运行配置里显式落位（下次 `save_config` 写盘） |
| 3 | `main_web.pyw:336` | `config.get("bind_address","127.0.0.1")`（实际生效路径） |
| 4 | `network/api_fastapi.py:1751` / `:3843` | 回显与 `start_server` 缺省同步为 `127.0.0.1`（消除「代码 0.0.0.0 / 文档 127.0.0.1」自相矛盾） |

行为不变的保证：只改**缺省值**，用户显式写的 `bind_address` 一律优先。

实测证据（真实代码路径，非静态推断）：

```
python -m compileall -q .            -> exit 0（无 error）
python probe_bind.py
start_server(缺省config) -> host = 127.0.0.1        # 空 config 走新缺省
start_server(显式0.0.0.0)  -> host = 0.0.0.0        # 显式配置仍然生效，局域网能力未被删除
DEFAULT_CONFIG[bind_address] = 127.0.0.1
load_config()[bind_address]  = 127.0.0.1
磁盘 config.json 含 bind_address 键 = False          # 未擅自改写用户配置
main_web.pyw:336: config.get('bind_address', '127.0.0.1')   # AST 复核（不执行 .pyw）
```

（`probe_bind.py` 的做法：猴子补丁 `uvicorn.Server` + `api_fastapi.create_app`，直接调用真实 `start_server()` 并读回 `uvicorn.Config.host`。）

**风险与回滚**：本机 `config.json` 无 `bind_address` 键 = 一直靠 0.0.0.0 缺省在跑，升级后只听本机。恢复局域网 = 在 `models/config.json` 写 `"bind_address":"0.0.0.0"` 并重启（同时确认 `api_token` 非空）。
**风险判定：低，故执行代码改动**（而非只出报告）。依据：① 前端已退役，浏览器入口只剩 Node /status（本机 127.0.0.1）；② MCP 挂载点内部自调用走 `127.0.0.1`（`network/mcp_server.py:44`）；③ main_web 自带的浏览器入口与自检全部 127.0.0.1；④ 与 `docs/http-api.md:15` 既有文档一致；⑤ 一行配置即可回滚。

## 3. 41 个 MCP 工具暴露面（source: `network/mcp_server.py`，AST 枚举）

判级：**R**=只读 ｜ **W**=写（可逆/编排级）｜ **D**=破坏性写（杀进程/关模拟器/关 MAA/放行审核）。
「dry_run」= 该工具是否带默认 True 的写闸门。

| # | 工具 | 行 | 级 | dry_run | 代理端点 | 被误用/滥用的最坏后果 |
|---|---|---|---|---|---|---|
| 1 | link_map | 129 | R | – | 端口映射 | 泄露内网 ADB 端口拓扑 |
| 2 | inspect_account | 135 | R | – | 账号现场 | 泄露账号配置 + asst.log |
| 3 | processes | 141 | R | – | 进程枚举 | 泄露进程/命令行（含路径） |
| 4 | status | 147 | R | – | /api/status | 运行态势泄露 |
| 5 | dashboard | 152 | R | – | 节点看板 | 同上 |
| 6 | queue | 157 | R | – | /api/queue | 队列内容泄露 |
| 7 | account_status | 162 | R | – | 账号状态 | 账号/体力信息泄露 |
| 8 | events | 167 | R | – | 事件流 | 历史事件泄露 |
| 9 | stats_dashboard | 172 | R | – | 统计总览 | 使用画像泄露 |
| 10 | health | 177 | R | – | 健康检查 | 服务存活探测（侦察） |
| 11 | depot_plan | 182 | R | – | 材料计划 | 计划泄露 |
| 12 | activity_stages | 187 | R | – | 活动关卡 | 低 |
| 13 | probe_status | 192 | R | – | 检测状态 | 低 |
| 14 | enqueue | 199 | W | ✗ | /api/queue/add | 塞入任意账号任务 → 消耗体力/资源 |
| 15 | dequeue | 208 | W | ✗ | /api/queue/remove | 移出排队任务（不影响在跑） |
| 16 | queue_control | 213 | W | ✗ | pause/resume | **pause 可让整条产线停摆**（可用性） |
| 17 | smart_dispatch | 220 | W | ✗ | /api/dispatch | 下发日常编排 → 真跑图、真消耗体力/理智 |
| 18 | probe_stage | 231 | W | ✗ | /api/probe | 触发检测（占用模拟器） |
| 19 | mas_snapshot | 242 | R | – | MAS SSH 快照 | 远端产线状态泄露 |
| 20 | mas_status | 250 | R | – | MAS 总览 | 同上 |
| 21 | mas_history | 258 | R | – | MAS 历史 | 同上 |
| 22 | mas_ledger | 266 | R | – | 本地留痕 | 同上 |
| 23 | mas_rerun_state | 273 | R | – | 补跑进度 | 同上 |
| 24 | mas_rerun | 280 | W | ✓ | tools/auto_mas_rerun.py | **远端** MAS 建临时队列补跑（最多 8 账号），干扰远端产线 |
| 25 | mas_cleanup | 290 | W | ✓ | --cleanup | 清掉远端临时补跑队列，任务丢失 |
| 26 | kill_audit_history | 305 | R | – | /api/kill_audit/history | 泄露杀进程审计（含证据） |
| 27 | kill_audit_pending | 317 | R | – | /api/kill_audit/pending | 泄露待放行破坏性动作清单 |
| 28 | **kill_audit_decide** | 327 | **D** | ✓ | /api/kill_audit/decide | **放行杀 MAA / 关模拟器 / 回收 / 重启**（唯一人工放行口）。误放行 = 误杀正在跑的账号 → 重启风暴连锁（models/kill_audit.py:5-10）；也可反向全部拦截 → 破坏性动作全部滞留 |
| 29 | emulators | 362 | R | – | /api/emulators | 模拟器清单泄露 |
| 30 | emulator_status | 370 | R | – | /api/emulator/{idx} | 同上 |
| 31 | **emulator_control** | 378 | **D** | ✓ | /api/emulator/{idx}/{action} | `stop/restart` **直接中断该机上所有任务** + 模拟器重启（并发 launch 冲突风险） |
| 32 | **emulator_batch** | 403 | **D** | ✓ | /api/emulator/batch/{action} | **批量** stop/restart：省略 indexes 时 = 全部运行中的模拟器 → 整机队任务全灭（单次最长 15 分钟） |
| 33 | connect_status | 441 | R | – | /api/connect/status | 连接态泄露 |
| 34 | **emu_connect_stop** | 450 | **D** | ✓ | /api/connect/{aid}/stop | 中断该机连接模式 MAA 会话 |
| 35 | **emu_connect_restart** | 469 | **D** | ✓ | /api/connect/{aid}/restart | 中断会话并重入队（mode 可改 daily → 意外开跑日常） |
| 36 | maa_status | 498 | R | – | /api/maa/status | 低 |
| 37 | maa_instances | 506 | R | – | /api/maa/instances | 实例池路径泄露 |
| 38 | account_launch | 514 | W | ✓ | /api/account/{idx}/launch | 拉起 MAA（资源占用；MAA 单实例守卫冲突风险） |
| 39 | **account_stop** | 527 | **D** | ✓ | /api/account/{idx}/stop | **杀该账号 MAA → 当前任务中断**（配合重试策略可能反复重跑） |
| 40 | **maa_stop_all** | 540 | **D** | ✓ | /api/action/stop_all | **停掉全部在跑账号** → 当天进度整体丢失 |
| 41 | maa_launch_source | 559 | W | ✓ | /api/maa/launch_source | 拉起源 MAA.exe GUI 窗口（本机交互被劫持/误触） |

统计：R=25（#1-13、19-23、26-27、29-30、33、36-37）｜W=9（#14-18、24-25、38、41）｜**D=7（#28、31、32、34、35、39、40）**。

**MCP 有意未暴露但 REST 仍存在**（绑定收紧后落在同一条鉴权边界内，需一并看待）：
- `network/api_fastapi.py:2462-2477` `POST /api/system/kill_maa` → 直接 `taskkill /F /PID|/IM MAA.exe`
- `network/api_fastapi.py:2480` `POST /api/system/restart`（replace.bat：taskkill → xcopy → 重启）
- `network/api_fastapi.py:2065` `POST /api/screenshots/{aid}/delete` → `shutil.rmtree`（路径来自请求体，仅拼 `screenshots/` 前缀）
- `network/api_fastapi.py:2068-2084` `POST /api/config` → **可远程改 `bind_address` 与 `api_token`**（:2072-2073）并 `save_config` 落盘

## 4. 威胁模型与最坏后果

| 场景 | 前提 | 最坏后果 |
|---|---|---|
| A. 空 token + 0.0.0.0（新装缺省组合） | 缺省配置原样部署（`models/config_manager.py:18` token 缺省 `""`） | 局域网任意主机**无鉴权**调用全部 REST + 41 工具：`maa_stop_all` + `emulator_batch(stop)` 一键清场；`kill_audit_decide` 放行误杀；`POST /api/config` 把 `bind_address` 改成 0.0.0.0 并持久化（重启后仍暴露）；`/api/system/kill_maa` 直接杀进程 |
| B. token 泄露 | token 被 MCP 客户端、日志、或 `GET /api/config` 明文回显（`network/api_fastapi.py:1751-1752` 把 `bind_address` 与 `api_token` 一起返回）带走 | 同 A，但需先能访问 19997（收紧缺省后 = 必须先已在局域网可达或本机） |
| C. 本机其它进程 / 低权限用户 | 无 | 收紧为 127.0.0.1 **不构成鉴权**：loopback 对同机所有进程开放。此时 `api_token` 是唯一防线；token 为空则等于无防线 |
| D. 浏览器侧放大 | `network/api_fastapi.py:190-196` `allow_origins=["*"]` + `allow_credentials=True` | 任意网页可对 127.0.0.1:19997 发起跨域请求（CORS 通配 + 凭证）；若 token 存在但被前端存放，可被诱导请求带走 |

MCP 挂载现状：缺省挂载（`network/api_fastapi.py:3795` `mcp_mount_enabled` 缺省 `True`）→ `network/api_fastapi.py:3807` `app.mount("/mcp", ...)`，鉴权由 `network/mcp_server.py:70-97` `_TokenAuth` 承担，**低 token 为空时直接放行**（`mcp_server.py:80`）。`/mcp` 不以 `/api/` 开头，因此 `api_fastapi.py:218` 那道中间件不覆盖它 —— 两条腿都靠同一个可空 token。

## 5. `api_token` 为空时的最小加固建议（**只建议，未实现，等 Lead 拍板**）

范式来源：`ui/control_server.py:752-759`（自动生成 + 落盘 + 打印）与 `:756`（非回环且无 token → 拒绝）。

```python
# main_web.pyw：读取 config 之后、uvicorn.Config(:347) 之前
import secrets
_tok = (config.get("api_token") or "").strip()
if not _tok:
    _tok = secrets.token_urlsafe(24)
    config["api_token"] = _tok
    from models.config_manager import save_config
    save_config(config)                      # 落盘，避免每次重启换 token
    _LOG.info(f"[安全] api_token 为空 → 已自动生成并落盘（前 8 位 {_tok[:8]}…）")
# 硬闸（范式 ui/control_server.py:756）：非回环绑定 + 无 token = 拒绝启动
if _bind not in ("127.0.0.1", "localhost", "::1") and not _tok:
    raise SystemExit(f"[安全] bind_address={_bind} 但 api_token 为空 —— 拒绝启动")
```

配套（同批拍板）：`network/api_fastapi.py:218` 与 `network/mcp_server.py:80` 的「空 token 放行」同步改成「空 token 一律 401」（可选保留 `/api/maa_remote` 的 127.0.0.1 回环豁免，见 `api_fastapi.py:216`）。
注意点：① 自动生成后会写 `config.json`（明文本就存 token，不新增暴露面，但会产生一次用户配置变更）；② 打印完整 token 会进日志 → 建议只打印前缀、完整值写 `ui/api_token.txt`（control_server 是文件范式）；③ 既有自动化（Node 看板 `--token`、外部脚本、Hermes）在首次生成后需同步取值，否则 401 —— 迁移策略需 Lead 确认。

## 6. 残留风险与未做项（明确边界）

1. **未实现** token 自动生成 / 空 token 拒绝（§5，等拍板）。
2. **未改** CORS 通配（`network/api_fastapi.py:190-196`）。
3. **未改** `server/maaorch-web.mjs:681` 的显示缺省 `'0.0.0.0'`（非本任务 scope；Node 实际监听 127.0.0.1，见 `server/maaorch-web.mjs:1092`）。
4. **未改** `ui/web/app.js:623`（显示缺省 0.0.0.0）与 `:3276`（提交缺省 127.0.0.1）的不一致 —— 前端正在退役（task-14）。
5. **并发冲突提示**：`network/api_fastapi.py` 同属 task-14（前端退役删路由，in_progress）；`docs/audits` 同属 task-19。本报告 §3 的行号以 `network/mcp_server.py` 为准（未被他人占用）；§1/§2 的 `api_fastapi.py` 行号若被删路由挪动，以符号名（`handle_get_config` / `start_server` / `_TokenAuth` / `handle_save_config`）检索。
6. **任务数会变**：task-22（mcp-ops，pending）要扩 MAS API 覆盖 → 工具数可能 >41，本表需随之刷新。
7. **本报告的 41 是时点快照**：AST 枚举 41 个 `@mcp.tool()`（`network/mcp_server.py`），与任务描述的「18 → 41」一致。

## 7. 可复现命令

```powershell
cd D:\1-2\MAAOrch

# 1) 工具清单（AST，不执行模块）
python -c "import ast;src=open(r'network\mcp_server.py',encoding='utf-8').read();t=ast.parse(src);n=[x for x in ast.walk(t) if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and any('mcp.tool' in ast.unparse(d) for d in x.decorator_list)];print(len(n));[print(i+1,x.lineno,x.name) for i,x in enumerate(n)]"

# 2) 缺省绑定核实
Select-String -Path main_web.pyw,network\api_fastapi.py,models\config_manager.py -Pattern 'bind_address'
python -c "import json;d=json.load(open('models/config.json',encoding='utf-8'));print('has_bind_address=', 'bind_address' in d)"

# 3) 收紧后的真实代码路径实测（probe_bind.py，见 §2 输出）
python %TEMP%\maorch_task23\probe_bind.py

# 4) 编译无 error
python -m compileall -q . ; echo "exit=$LASTEXITCODE"   # 实测 exit=0
```

## 8. 本次改动文件清单（供 Lead 终审）

| 文件 | 改动 |
|---|---|
| `main_web.pyw:336` | 缺省绑定 `0.0.0.0` → `127.0.0.1`（+ 注释） |
| `models/config_manager.py:16-18`、`:93` | `DEFAULT_CONFIG` 增 `bind_address:"127.0.0.1"`；`load_config` 显式 `setdefault` |
| `network/api_fastapi.py:1751`、`:3843` | 回显与 `start_server` 缺省同步 `127.0.0.1` |
| `docs/audits/2026-09-26_bind_and_exposure.md` | 本报告 |

未触碰：`services/runner.py`、`services/launch_queue.py`、`services/runtime_health.py`（他人 scope）；未执行任何 git 命令。

## 9. 冻结声明与复检清单（`network/api_fastapi.py` 并发写）

`network/api_fastapi.py` 与 task-14（frontend-retirer）scope 冲突，**本任务自 2026-09-26 起对该文件冻结，不再写入**。本任务只在该文件留下了 2 个 hunk，请在 task-14 完成后按下列内容复检（若被整文件重写覆盖，请重新施加）：

| hunk | 位置 | 精确内容 |
|---|---|---|
| H1 | `network/api_fastapi.py:1751`（`handle_get_config` 内） | `"bind_address": cfg.get("bind_address", "127.0.0.1"),  # ★ 2026-09-26 与 main_web.pyw:332 同缺省`（原 `"0.0.0.0"`） |
| H2 | `network/api_fastapi.py:3839-3843`（`start_server` 内） | 3 行注释 + `bind = mw.config.get("bind_address", "127.0.0.1") if hasattr(mw, 'config') else "127.0.0.1"`（原两处 `"0.0.0.0"`） |

复检命令（改后必须仍输出 127.0.0.1）：
```powershell
Select-String -Path network\api_fastapi.py -Pattern 'bind_address", "127\.0\.0\.1"|default\(\"bind_address\"\)|bind_address", \"127'
```
注：H1/H2 都是**缺省值**，`start_server` 当前无调用方（§1 #3），所以即使 H2 丢失也不造成实际监听回退；**真正决定监听的是 `main_web.pyw:336`**（不在冲突 scope 内）。
