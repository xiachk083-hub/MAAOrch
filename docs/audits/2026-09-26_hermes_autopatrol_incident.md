# Hermes 自动巡检事故 + 根因 + agent-time 量化（2026-09-26）

**一句话结论**：交接文档里"已落地并实跑验证"的自动巡检，在**自动路径上一次都没成功过** ——
17:41、18:12、18:52 三次 30 分钟自动 fire 都在 0.2~0.45 秒内崩掉。根因是 Hermes 网关启动代码
`gateway/run.py` 的 `_ensure_windows_gateway_venv_imports()` 会退回加载**仓库内 3.11 遗留 venv**
（`hermes-agent\venv`）并把它的 site-packages 插到 `sys.path[1]`，压过 PM 正式安装的 3.14 环境；
而自动 fire 是**在网关进程内**构造 AIAgent，于是 `import openai` 拿到 3.11 的 pydantic_core、
在 3.14 解释器下必然 `ModuleNotFoundError`。19:05 把该遗留 venv 改名后，网关 `sys.path` 干净。

> 说明：19:00 之前我把根因误判为"启动 shell 的 PYTHONPATH 被污染"，重启网关无效（18:52 仍失败）。
> 那条结论**是错的**，已由 19:01-19:05 的探针证据推翻，本文为更正后的版本。

## 1. 证据时间线

| 时间（+08:00） | 事件 | 证据 |
|---|---|---|
| 16:36:46 | 宿主网关启动（PID 22232，3.14.7） | `logs/gateway-exit-diag.log` `tag=gateway.start` |
| 16:50:57 → 17:10:18 | **手动** direct 巡检完成（20 LLM / 85 工具 / 2,591,635 token / 1160.9 s） | `executions`：`source=direct, pid=3328`（独立进程） |
| 17:41:05 | 自动 fire 失败 0.43 s | `source=builtin, pid=22232`，`ModuleNotFoundError: pydantic_core._pydantic_core` |
| 18:12:05 | 自动 fire 失败 0.21 s | 同上 |
| 18:16:08 | 干净环境重启网关（PID 27488） | `hermes gateway restart` |
| 18:16:45 → 18:21:48 | **手动** direct 巡检完成（303.0 s，6 LLM / 5 工具 / 229,604 token） | 判定"正常"：`ISOLATION=PASS`、今日 2/2 DONE |
| 18:52:33 | 自动 fire **在重启后的网关上再次失败** 0.45 s | `source=builtin, pid=27488` → 证明"重启/PYTHONPATH 污染"假设不成立 |
| 19:01:56 | 探针（临时 `sitecustomize.py`）抓到网关**启动时 sys.path 是干净的** | `sys.path[0]=hermes-agent`、`[1]=<staged 3.14 venv>/Lib/site-packages` |
| 19:02:03 | 同一进程内 `import pydantic` 时 sys.path 已被改写 | `[1]=hermes-agent\venv\Lib\site-packages`（3.11 遗留）、多出 3.11 的 win32/editable finder |
| 19:03:28 | 探针抓到**改写者调用栈** | `site.addsitedir('...\hermes-agent\venv\Lib\site-packages')` ← `gateway/run.py:455` ← `_cmd_run` ← `gateway_command` |
| 19:05:1x | 改名遗留 venv + 重启网关（PID 24988 / 5016） | 探针日志中 `hermes-agent\venv` 出现次数：**0**（改名前：启动即出现） |
| 19:22:33 | 下一次自动 fire（修复验证点） | 见 §5 |

## 2. 根因（一条硬编码退路）

`gateway/run.py:422` `_ensure_windows_gateway_venv_imports()`：

```python
project_root = Path(__file__).resolve().parent.parent      # hermes-agent
candidates = []
if os.environ.get("VIRTUAL_ENV"):
    candidates.append(Path(os.environ["VIRTUAL_ENV"]))     # launcher 给的是 <hermes>\tools
candidates.append(project_root / "venv")                   # ← 退路：仓库内遗留 venv
...
site.addsitedir(site_entry)                                # 处理 .pth（pywin32 等）
sys.path.insert(1 if sys.path[0] == project_entry else 0, site_entry)
```

- launcher（`gateway_windows._build_gateway_argv` → `_resolve_detached_python`）把 `VIRTUAL_ENV` 设成
  `<hermes>\tools`（store python 的上一级目录，**不是 venv**）；该候选没有 `Lib\site-packages` → 跳过；
- 于是退到 `hermes-agent\venv` —— 2026-09-24 用 uv 建的 **3.11 venv**（`pyvenv.cfg: home=.hermes-runtime\python\cpython-3.11...`，
  572 MB，`pydantic_core` 只有 `_pydantic_core.cp311-win_amd64.pyd`），它**还躺在磁盘上**；
- `site.addsitedir` 会把它的 site-packages 插到 `sys.path` 前面（本例 position 1），并处理它的 `.pth`
  （`pywin32.pth`、`__editable__.hermes_agent-0.21.5.pth`）→ 3.11 的 `openai`/`pydantic` 覆盖 3.14 的；
- 自动 fire 走 `cron/scheduler.py` 的 `source="builtin"` 分支，**在网关进程内** `_construct_cron_agent` →
  `agent_init._init_openai_client` → `import openai` → 3.11 扩展在 3.14 下不可用 → 秒级崩。
- 手动 `hermes cron run` 走独立进程（`source="direct"`），那里只有 `hermes_bootstrap` 的正式激活 → 一直正常，
  这正是"手动能跑、自动必挂"的全部原因，也解释了为什么 16:50 那次"实跑成功"掩盖了故障。

同一网关里的 **ark 两个 cron 任务一直成功**（262 次 `builtin` 完成，含 18:50），因为它们是脚本/监控型任务、
不构造 AIAgent、不 import openai —— 所以"网关内置 cron 坏了"是假象，坏的只是**进程内构造 Agent** 这一条路径。

## 3. 修复

```powershell
# 1) 停网关注（改名会让运行中进程的旧 sys.path 失效，必须先停）
& "$env:LOCALAPPDATA\hermes\bin\hermes.exe" gateway stop
# 2) 把 3.11 遗留 venv 改名归档（可一键改回；PM 本来的设计也是"committed 之后删除它"）
Rename-Item "$env:LOCALAPPDATA\hermes\hermes-agent\venv" venv.pre-pm-3.11.bak
# 3) 正常启动
& "$env:LOCALAPPDATA\hermes\bin\hermes.exe" gateway start
```

修复后网关（PID 24988 / 5016）探针证据：`PYTHONPATH=hermes-agent;hermes-agent;<staged venv>/Lib/site-packages`，
唯一的 `addsitedir` 来自 `pm/environments.activate_dependencies`，全日志 `hermes-agent\venv` 提及数 **0**；
`hermes doctor` 仍报 "Python 3.14.7 / Runtime venv staged (...fd1df0...) (active in this process)"，四个 profile 网关均 running。

**回滚**：`Rename-Item "$env:LOCALAPPDATA\hermes\hermes-agent\venv.pre-pm-3.11.bak" venv`（但那样故障会复现）。

## 4. 为什么不是别的原因（排除项）

| 曾被怀疑 | 结论 | 依据 |
|---|---|---|
| 启动 shell 的 PYTHONPATH 污染 | **否** | 18:16 从干净 shell 重启后 18:52 仍失败；探针显示网关启动时 PYTHONPATH 只有 hermes-agent + staged venv |
| 网关内置 cron 整体坏了 | **否** | ark 两任务 262 次 `builtin` 完成 |
| 手动 `cron run` 与自动是同一路径 | **否** | 前者 `source=direct`（独立进程），后者 `source=builtin`（网关进程内） |
| 提示词/工具预算写错导致失败 | **否** | 失败发生在构造 LLM 客户端阶段，还没到工具调用 |

## 5. 验证

1. **结构性**（已完成）：修复后网关 `sys.path` 不再含 `hermes-agent\venv`（探针 0 命中），
   `hermes doctor` 正常，四个 profile 的网关 running，唯一启用任务仍为 `52b88b0ffca0`。
2. **端到端（已通过）**：修复后的**第一次自动 fire** `2026-09-26T19:23:04+08:00`：
   - `executions`：`source=builtin, status=completed, pid=5016`，agent time **199.8 s**（修复前同类 fire 0.2~0.45 秒即崩）；
   - 账本：3 次 LLM / 4 次工具 / 96,760 token（`state.db` 一对一归并成功 ⇒ agent 真的跑完了）；
   - 巡检产物 `cron/output/52b88b0ffca0/2026-09-26_19-26-24.md`：`MCP_API_TOOL_COUNT=10`、`ISOLATION=PASS`、
     后端 ready、今日 2/2 账号 DONE、结论"疑似批次未结束"（依据：今日仅 2 个账号有记录，且都在 05:56–06:02 刚完成）；
   - 下一次自动 fire：19:56:24（`hermes -p mas cron status`）。
3. **探针语义**：`python tools/mas_agent_time.py --check` 现在输出
   `AUTO_PATH=RECOVERED （最近一次自动 fire ... 成功；窗口内 1 次失败为修复前遗留）` 并**退出码 0** ——
   窗口里仍有修复前的旧失败，但最近一次自动 fire 已成功，探针据此判"已恢复"，不让旧账永远压着新成功。

## 6. agent-time 量化

口径：`agent_time = executions.finished_at - started_at`（该次 fire 的进程存活时长）；
`llm_calls / tool_calls / token` 取自 `state.db.sessions` 中 `id` 以 `cron_` 开头的会话，按开始时间一对一就近对齐；
失败 fire 是秒级空转、没有 session，**不参与投影**（避免均值被稀释）；running 既不算成功也不算失败。

| 运行 | 路径 | 结果 | agent time | LLM | 工具 | token |
|---|---|---|---|---|---|---|
| 2026-09-26 16:50:57 | direct（手动） | completed | 1160.9 s | 20 | 85 | 2,591,635 |
| 2026-09-26 17:41:05 | builtin（自动） | failed | 0.4 s | — | — | — |
| 2026-09-26 18:12:05 | builtin（自动） | failed | 0.2 s | — | — | — |
| 2026-09-26 18:16:45 | direct（手动） | completed | 303.0 s | 6 | 5 | 229,604 |
| 2026-09-26 18:52:33 | builtin（自动） | failed | 0.4 s | — | — | — |

按 30 分钟节拍（48 次/天）投影：

| 口径 | 单次 agent time | 占用 | agent time/天 | token/天 |
|---|---|---|---|---|
| 提示词收紧前（16:50 样本） | 1160.9 s | 64.5% 全天 | 15.48 h | 124.4 M |
| 收紧后（18:16 样本，≤6 次工具硬预算） | 303.0 s | 16.8% 全天 | 4.04 h | 11.0 M |

收紧提示词的收益：agent time −73.9%、工具调用 −94.1%（85→5）、token −91.1%。
这也是"要不要维持 48 次/天"的决策依据：当前口径下巡检约占全天 17% 的 agent 时间。

## 7. 定时任务现状（2026-09-26 19:05 核查）

| profile | job | 节拍 | 状态 |
|---|---|---|---|
| mas | `52b88b0ffca0` mas-readonly-inspection | every 30m | **启用（本次修复对象，Hermes 侧唯一要用的定时任务）** |
| mas | 其余 5 个一次性任务（rogue-*/dm-*/g28-*） | once | `enabled=False`（历史遗留，未动） |
| ark | `d458d725a481` 明日方舟蹲饼、`3761e3c08a82` 待审批提醒 | every 30m / 15m | 启用（不属于本次交接，未动） |
| 汉字 / 采购员 | — | — | 无任务 |

## 8. 新增工具：`tools/mas_agent_time.py`（只读 + 探针）

```bash
python tools/mas_agent_time.py                 # 打印摘要并写 docs/audits/<date>_mas_agent_time.{md,json}
python tools/mas_agent_time.py --json          # 只打印 JSON
python tools/mas_agent_time.py --check         # 探针：HEALTHY/RECOVERED(0) / FAILING(3) / UNKNOWN(4)
python tools/mas_agent_time.py --no-write      # 不落盘
```

测试：`python -m pytest tests/test_mas_agent_time.py -q` —— 13 项通过。覆盖 agent_time 口径、投影、
失败样本不稀释、running 不算失败、builtin 健康判定、一对一归并（失败 fire 不得认领下一次成功运行的账本）、
只读写死（`mode=ro` + 源码不含写 SQL）、探针退出码。全部 mas 相关测试：94 passed。

## 9. 复现命令

```powershell
# 1) 自动路径健康探针
python tools/mas_agent_time.py --check; "EXIT=$LASTEXITCODE"

# 2) 最近几次 fire 的 source/pid/结果（builtin=网关进程内，direct=独立进程）
$py = "$env:LOCALAPPDATA\hermes\tools\python-3.14.7+20260901-win32-x64\python.exe"
& $py -c "import sqlite3;c=sqlite3.connect(r'C:\Users\xiach\AppData\Local\hermes\profiles\mas\cron\executions.db');[print(r) for r in c.execute(\"select status,source,pid,started_at,finished_at from executions where job_id='52b88b0ffca0' order by rowid desc limit 5\")]"

# 3) 本次根因的自查（网关进程内是否又被塞进仓库内遗留 venv）
Select-String -Path "$env:LOCALAPPDATA\hermes\logs\gateway-stdio.log" -Pattern 'hermes-agent\\venv\\Lib' | Select-Object -Last 3
```

关联：`docs/HERMES_MAS_HANDOVER.md`（交接契约）、`tools/mas_catchup_check.py`（补漏判据，只提议不发车）。
