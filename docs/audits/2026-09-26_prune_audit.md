# MAAOrch 清理批次 独立审计报告

- 审计对象：`docs/audits/2026-09-26_prune_manifest.json`（252 个文件 / 35,022 行，6 个分区）
- 审计基线：仓库 HEAD = `e71c14e`（"archive: pre-prune snapshot (2026-09-26)"），回滚 tag `pre-prune-20260926` 存在且指向该提交
- 审计时刻的现场状态：**252/252 已从工作区消失**；`git status --porcelain` 显示 **243 个 " D"**（已跟踪文件被删），另有 **9 个被删文件本就不在 git 里**（见 §6）
- 审计员权限：只读。未删除任何文件、未执行任何 git 写操作；本文件是本次审计唯一写入。
- 审计方法：与 Lead 的 "import-closure 推导 + 清单比对" 完全不同的**反向引用法**（先固定删除集合 D，再从存活文件反查引用），外加 **Python AST 导入解析** 作为第二种独立实现互相印证。

---

## 1. 结论

> ## ⚠️ 不安全（有条件放行）
>
> **核心链路（`main_web.pyw` 的导入闭包，54 个模块）完整无损，没有一条导入指向被删文件；`network/`、`services/ai_agent/`、`tools/` 的包结构与 `__init__.py` 均未被打断。**
>
> 但清单删穿了 **4 个仍然存活、且有明确入口的引用面**：
>
> 1. **`tools/emu_maa_tool/server/` 整包失效** —— 保留的 `main.py` / `state.py` 仍 import 被删的 5 个模块（`account_api` / `emu_api` / `maa_api` / `auth` / `oplog`），服务启动即 `ImportError`。
> 2. **`tools/event_sweep.mjs`（live 主工具）** 在运行中 spawn 被删的 `tools/tap.py` 与 `tools/story_skip.py`。
> 3. **`server/maaorch-web.mjs`（Web GUI 后端）** 的 `POST /api/account/toggle` 路由 spawn 被删的 `tools/auto_mas_toggle_user.py`。
> 4. **3 个 .bat 入口**（`tools/hook.bat`、`tools/导出账号信息.bat`、`tools/emu_maa_tool/start.bat`）直接调用被删脚本。
>
> 另有：`tools/read_nodes.py`（保留）在 `--write` 路径 import 被删的 `batch_run`；`ui/*.html` 被删但 `ui/control_server.py` 依赖它们（**可自愈**，属有条件的低风险）。
>
> **处置：需先处理 §5 的 R1–R5 再判定清理完成；gen_html 分区（R6）与文档漂移（R8）可放行。**

---

## 2. 方法与独立性（为什么"删除已完成"不影响结论）

Lead 的方法是"从 `main_web.pyw` 求导入闭包 + 文档提及，再减去 subprocess/importlib 字符串路径"。本审计**反向做**：

| # | 方法 | 说明 |
|---|------|------|
| M1 | 固定删除集 D | 从 manifest 读出 252 条路径，正斜杠归一化为集合 D（此集合与文件是否已删无关 ⇒ 结论对"删除进行中"同样成立） |
| M2 | 反向 token 扫描 | 遍历所有**不在 D 里**的存活文件（跳过 `runtime/` `data/` `debug/` `.git/` `node_modules/` `services/maa/` `__pycache__/`），逐个文本文件按 basename / 去扩展名 stem / 点分模块路径三种键匹配 D |
| M3 | 正则捕获全量兜底 | 对全部存活文件（≤400KB、非二进制、跳过 maa_instances/vendor）用"被删 .py basename 的单词边界正则"整仓扫一遍，防止漏掉 .txt/.log/.json/no-extension 等 |
| M4 | 字符串路径扫描 | 专扫 `subprocess` / `os.system` / `spec_from_file_location` / `sys.path.insert` 附近的带引号路径，以及 `.bat/.cmd/.ps1/.sh` 里的 `python <path>` |
| M5 | **Python AST 导入解析（独立第二实现）** | 用 `ast.parse` 取全部 live .py 的 `Import`/`ImportFrom` 节点（含函数内延迟导入），按真实 `sys.path` 语义（importer 目录逐级上溯）解析；不能解析且能对上 D 的即为断链 |
| M6 | 目录/包完整性 | 以 D 为驱动分组目录，判定"删后是否还有存活 .py""`__init__.py` 是否被删" |
| M7 | git 可恢复性 | `git ls-tree -r HEAD` 对照 D，确认哪些被删文件可从 tag 恢复 |

**M2 与 M5 交叉印证结果一致**：两者独立指认出同一组断链（emu server 5 模块 + `read_nodes → batch_run`），无一方独有命中，说明既没有系统性漏报、也排除了单方法的误报。

**误报控制示例**（已人工排除，未计入风险清单）：
- `import time as _t` 曾命中 `ui/_t.html`（stem 匹配）→ 剔除；
- `infrastructure/maa_core.py:1` 的 "ctypes bridge" 曾命中 `tools/bridge.py` → 剔除；
- `network/api_fastapi.py` 里的 "adb channel"、`tools/scheduler.py` 里的 `data/daily_state`、`tools/combat_model.py` 里的 `out["op_stats"]` → 均为词面巧合，剔除；
- `tools/maa_channel/channel.py`：整仓（含 AST）**无任何存活代码 import 它**，同目录 `mcp_server.py` 不依赖它 → 判定安全。

**方法边界（未覆盖项，供 Lead 判断）**：
- 动态拼接路径（f-string / `"tools/" + name`）无法静态捕获；
- `runtime/`、`data/`、`debug/`、`services/maa/` 按任务约定未纳入扫描（其中的配置/计划任务若引用被删脚本，本报告不覆盖）；
- >400KB 的文本文件与二进制文件未做 basename 兜底扫描；
- 仓库外实体（Windows 计划任务、外部 MCP 客户端配置）不在此仓库证据内，仅在 §5 R8 以文档线索提示。

---

## 3. 误删风险清单

严重度：**P0** = 触发即报错/功能中断；**P1** = 特定路径/按钮才触发；**P2** = 文档或残留物漂移，不影响运行。

### R1 【P0】`tools/emu_maa_tool/server/` 保留文件 import 5 个已删模块 → 服务启动即 ImportError

被删文件：`tools/emu_maa_tool/server/account_api.py`、`emu_api.py`、`maa_api.py`、`auth.py`、`oplog.py`
引用方（均为**存活**文件，位于 `tools/emu_maa_tool/server/`）：

| 引用位置 | 原文（截断 120 字符） |
|---|---|
| `tools/emu_maa_tool/server/main.py:29` | `from . import account_api, emu_api, maa_api, state  # noqa: E402` |
| `tools/emu_maa_tool/server/main.py:30` | `from .auth import verify_token  # noqa: E402` |
| `tools/emu_maa_tool/server/main.py:31` | `from .oplog import recent as oplog_recent  # noqa: E402` |
| `tools/emu_maa_tool/server/main.py:33` | `from server import account_api, emu_api, maa_api, state  # noqa: E402` |
| `tools/emu_maa_tool/server/main.py:34` | `from server.auth import verify_token  # noqa: E402` |
| `tools/emu_maa_tool/server/main.py:35` | `from server.oplog import recent as oplog_recent  # noqa: E402` |
| `tools/emu_maa_tool/server/state.py:16` | `from .account_api import _load as load_accounts` |
| `tools/emu_maa_tool/server/state.py:21` | `from .emu_api import detect_emu_port  # noqa: E402` |

存活入口（证据）：
- `tools/emu_maa_tool/start_server.bat:4`：`python server\main.py 127.0.0.1`
- `tools/emu_maa_tool/start.bat:11`：`... "%~dp0server\main.py" 127.0.0.1 > "%~dp0data\server_run.log" ...`

后果：`python server\main.py` 第 29 行即 `ModuleNotFoundError`；两个 .bat 入口全部失效。

### R2 【P0】`tools/hook.bat` / `tools/导出账号信息.bat` / `tools/emu_maa_tool/start.bat` 直接调用被删脚本

| 引用位置 | 原文 | 被删目标 |
|---|---|---|
| `tools/hook.bat:6` | `python auto_mas_hook.py` | `tools/auto_mas_hook.py` |
| `tools/导出账号信息.bat:7` | `python tools\auto_mas_export.py` | `tools/auto_mas_export.py` |
| `tools/emu_maa_tool/start.bat:16` | `python "%~dp0client\app.py" http://127.0.0.1:18889` | `tools/emu_maa_tool/client/app.py` |

后果：三个入口 100% 失败（前两个 immediate error；start.bat 即便服务器起来了，桌面窗口也起不来）。`导出账号信息.bat` 还承诺产出 `data/auto_mas_accounts.csv`，现在不会再有。

### R3 【P0】`tools/event_sweep.mjs` 运行时 spawn 两个已删脚本

被删文件：`tools/tap.py`、`tools/story_skip.py`

| 引用位置 | 原文（截断 120 字符） |
|---|---|
| `tools/event_sweep.mjs:588` | `await run(PY, ['tools/tap.py', String(x), String(y), '--wait', String(wait), '--emu', EMU],` |
| `tools/event_sweep.mjs:547` | `STORY.py = spawn(PY, ['tools/story_skip.py', '--minutes', String(RUN_TIMEOUT_MIN)],` |
| `tools/event_sweep.mjs:586` | `*  走 `tools/tap.py`：adb 路径与端口漂移都由它解析，Node 侧不重复那些烂事。 */` |
| `tools/event_sweep.mjs:551` | `log(  🎬 常驻服务不可用 ⇒ 起了 1 个 Python 看门狗兜底（story_skip.py --minutes ${RUN_TIMEOUT_MIN}）)` |
| `tools/event_sweep.mjs:11` | `*     ① 导航：`tools/bi_home.py --stage <代号> --want <难度>`（回大厅 → 终端 → 关卡详情页）` |
| `tools/event_sweep.mjs:108` | `//   `tools/story_skip.py --interval 1.5` 同口径）；tick 是"上一次跑完再排下一次"，` |

后果：
- `tapPy()`（复核三星前必用的"点掉结算页/好友弹层"）每次调用都失败 ⇒ 复核/结算判定链路退化；
- 剧情看门狗兜底分支 spawn 失败：`STORY.py.on('error')` 吞掉错误后仍把 `STORY.mode='py-watchdog'`，Node 侧**静默不再跳剧情**（最隐蔽的一条：不报错、不生效）；
- `bi_home.py` 只在注释/文档里出现，无功能影响。

### R4 【P1】`server/maaorch-web.mjs`（Web GUI 后端）的账号开关路由 spawn 已删脚本

| 引用位置 | 原文 | 被删目标 |
|---|---|---|
| `server/maaorch-web.mjs:877` | `const argv = ['tools/auto_mas_toggle_user.py', '--name', name]` | `tools/auto_mas_toggle_user.py` |

上下文：`POST /api/account/toggle` 分支（:874–:880）。对照同类路由 `:864` / `:870` 用的 `tools/auto_mas_rerun.py` **仍存在**，说明这里不是整体弃用，而是单点漏删保护。
后果：GUI 上该账号开关按钮必失败。旁证：`server/maaorch-web.mjs.bak-before-apk:804` 有同一行（备份文件，未删，不影响）。

### R5 【P1】`tools/read_nodes.py`（保留）在 `--write` 路径 import 被删的 `batch_run`

| 引用位置 | 原文 |
|---|---|
| `tools/read_nodes.py:208` | `    import batch_run as BR`（函数 `write_nodes` 内，延迟导入） |
| `tools/read_nodes.py:21` | `批量执行器在缺坐标时拒绝真跑（见 `batch_run.py`），所以这张表就是"能不能真跑"的开关。` |

被删目标：`tools/batch_run.py`。以 `python tools/read_nodes.py` 运行时 `sys.path[0]=tools/`，该导入原本解析到 `tools/batch_run.py`。

调用方（存活）：`tools/push/auto_push.py:290`（`spec_from_file_location` 加载 read_nodes.py）、`:312`（`subprocess.run([sys.executable, ...read_nodes.py...])`）。这两个调用点只走"读坐标"，**不触发** `write_nodes`，所以 `auto_push.py` 主链路暂不受影响；但 `read_nodes.py --write`（落表闸门，文档 :18 中的用法 ④）**必失败**，而它正是"未经确认的坐标不许落表"的唯一实现。

### R6 【P1，可自愈】`ui/*.html` 被删，但 `ui/control_server.py` 把它们当运行时页面

被删文件：`ui/push_agent.html`、`ui/push_agent_{32822410,394162955,751737728,993305172,squidP}.html`、`ui/mini_{32822410,394162955,751737728,SquidP}.html`、`ui/_t.html`、`ui/_t2.html`

| 引用位置 | 原文（截断 120 字符） |
|---|---|
| `ui/control_server.py:40` | `PAGE = PROJ / "ui" / "push_agent.html"` |
| `ui/control_server.py:366` | `    return PAGE if not ACCOUNT else PAGE.with_name(f"push_agent_{ACCOUNT}.html")` |
| `ui/control_server.py:469` | `        page = PROJ / "ui" / f"mini_{ACCOUNT or 'default'}.html"` |
| `ui/control_server.py:721` | `            regenerate_page(sanity)`（页面缺失/过期时现场重跑生成器，:349 `cmd = [sys.executable, str(GENERATOR), ...]`） |
| `ui/control_server.py:723` | `            return f"还没有 {page}，先跑一次 tools/gen_push_ui.py", 404` |
| `tools/gen_push_ui.py:26` | `OUT = PROJ / "ui" / "push_agent.html"`（生成器存活） |
| `tools/gen_mini_ui.py:4` | `python tools/gen_mini_ui.py --account 751737728 --emu 2 --out ui/mini_751737728.html` |
| `tools/gen_mini_ui.py:11` | `判据同 `tools/list_jobs.py`：✅ 缺 0 ｜ ⚠️ 缺 1-2（可能要借）｜ ❌ 缺 3+ 或空壳。`（`list_jobs.py` 已删，仅注释级影响） |

判定：这两个页面是**生成物**，`control_server.py:341-361` 的 `regenerate_page()` 与 `:471-477` 会在缺失时用**存活的** `tools/gen_push_ui.py` / `tools/gen_mini_ui.py` 现场重建 ⇒ **不算硬性误删**。但要注意两点条件：
1. 自愈只覆盖 `/` 与 `/mini` 两个路由；若有人用 `file://` 直开这些 HTML（`control_server.py:5` 的注释场景），页面临时不存在；
2. `ui/_t.html` / `ui/_t2.html` 是探针页，**整仓无任何存活文件引用**，可安全删。

### R7 【P2】.ps1 助手的进程白名单里仍列着已删驱动（无害）

`tools/_kill_my_drivers.ps1:2`：`$mine = 'live_drive|maa_copilot_run|plan_loop|ga_ex_drive|screen_state|Asst\.load'`
其中 `live_drive`（`tools/live_drive.py`）、`ga_ex_drive`（`tools/ga_ex_drive.py`）已被删 ⇒ 该白名单项永远匹配不到（`maa_copilot_run` / `plan_loop` / `screen_state` 仍存活）。**无功能风险**，属清单漂移。

### R8 【P2】文档与 README 指向已删文件（不影响运行，建议同步）

| 引用位置 | 原文（截断 120 字符） | 被删目标 |
|---|---|---|
| `tools/maa_all/README.md:16` | `- 手动启动：`python E:\MAA-ALL\manage_maa.py serve`` | `tools/maa_all/manage_maa.py` |
| `tools/maa_all/README.md:31` | `python manage_maa.py list                 # 副本 + 模拟器一览` | 同上 |
| `tools/maa_all/README.md:47` | `- 服务以计划任务运行：若改过 manage_maa.py，需 任务计划程序里重启 MAA-ALL-GUI（或 schtasks /end + /run）。` | 同上（**提示仓库外有 Windows 计划任务**，见 §5 建议 4） |
| `tools/emu_maa_tool/README.md:15` | `python client/app.py        :: 只开桌面窗口（连 127.0.0.1:18889）` | `tools/emu_maa_tool/client/app.py` |
| `tools/emu_maa_tool/README.md:54` | `python tools/emu_maa_tool/deploy_server.py                :: 部署 + 停用 MAAOrch 队列/回收` | `tools/emu_maa_tool/deploy_server.py` |
| `tools/emu_maa_tool/README.md:59` | `- 客户端连接：`python client/app.py http://100.79.173.69:18889`` | `client/app.py` |
| `tools/emu_maa_tool/start_server.bat:2` | `rem MAAOrch tool server (local mode). Remote deploy uses deploy_server.py.` | `deploy_server.py`（注释） |
| `tools/push/README.md:22` | `python tools/list_jobs.py --stage act54side_ex07 --box data/boxes/box_751737728.json` | `tools/list_jobs.py` |
| `tools/push/README.md:38` | `tools/push/          ← 本目录：推图引擎（auto_push.py）+ 校准（calibrate.py）+ 脱离启动（_spawn.py）` | `tools/push/_spawn.py` |
| `tools/push/README.md:51` | `| **MVP 本体** | tools/scan_box.py / tools/list_jobs.py / tools/run_job.py | 扫 box、筛作业、跑指定作业 |` | `list_jobs.py` |
| `tools/push/README.md:63` | `| list_jobs.py | 筛作业、按缺人数排序 | 不自动选、不自动跑 |` | `list_jobs.py` |
| `docs/audits/20260924_1215_page_recog.md:72` | `...均已修（备份 `tools/scan_map_panels.py.bak-20260924_120609`）：` | `tools/scan_map_panels.py.bak-20260924_120609` |
| `docs/audits/20260924_2022_reward_gate_threshold.md:3` | `...只动 `tools/event_sweep.mjs`、`tools/panel_gate.py`...` | `tools/panel_gate.py.bak`（正文指的是 .py，备份被删属正常） |
| `docs/audits/20260924_2030_reward_gate_threshold.md:3` | 同上 | 同上 |

说明：`docs/*.md` 对 **tools/xxx.py 形式路径**的全量扫描命中 **22 条**，全部落在上面这些"文档/备份说明"性质的位置；**没有任何一条是运行路径**。另有 31 条命中位于 `.zcode/plans/`（历史 agent 计划书）与上述 README，同属文档层。`tools/push/README.md:22` 提到的 `tools/scan_box.py` / `run_job.py` **仍存活**，不要连带处理。

### R9 【P2】历史残留物指向已删文件（无功能影响）

| 文件 | 内容 | 说明 |
|---|---|---|
| `.pytest_cache/v/cache/lastfailed:4` | `"debug/scripts/_win_test.py": true` | 被删的 `_win_test.py` 的 pytest 缓存 |
| `logs/agent/deploy_run.log:16` / `deploy_run2.log:19` | `File "D:\1-2\MAAOrch\tools\ai_agent_battle_test.py", line 332, in <module>` | 被删脚本的历史 traceback |
| `tools/__pycache__/*.pyc` | 含 `batch_run.cpython-312.pyc`、`auto_mas_hook.cpython-312.pyc`、`list_jobs.cpython-312.pyc`、`tap.cpython-*.pyc` 等已删模块的字节码 | **单独 .pyc 不能被 import**（Python 只在源码同目录的 `x.pyc` 形式支持无源导入），无功能风险；但会干扰后续 grep/审计，建议一并清理 |
| `tools/maa_channel/deploy_one_liner.txt:1` | 内含 `$b='IyAtKi0...'`（base64 载荷，头部可解码出 maa-channel 说明） | 疑似内嵌 channel 源码的部署一行命令，**可作 channel.py 的潜在恢复源**（见 §5 建议 8） |

---

## 4. 空包 / 失效包风险清单

以 D 为驱动统计（"删后存活"= 当前工作区实际剩余）：

| 目录 | 被删 | 删后剩余 | 判定 |
|---|---|---|---|
| **`tools/emu_maa_tool/server/`** | 5 个 .py（account_api / emu_api / maa_api / auth / oplog） | `__init__.py`、`config.py`、`main.py`、`state.py`（4 个 py，包结构仍在） | ❌ **包仍存在但已失效**：`main.py`/`state.py` 的 import 全部断链（R1）。`__init__.py` 存活，故不会出现"命名空间包静默降级"，而是**显式 ImportError**（可诊断性反而更好） |
| **`tools/emu_maa_tool/client/`** | `app.py` | 仅 `token.txt`（+ `__pycache__`） | ❌ **目录内已无任何 .py**，客户端入口消失（R2） |
| **`tools/emu_maa_tool/`** | 顶层 42 个 .py 全部被删 | `README.md`、`start.bat`、`start_server.bat`、`web/`、`data/`、若干 .json/.txt | ⚠️ 顶层已无 Python 代码，只剩两个已失效的启动器（R1/R2）；目录无 `__init__.py`（非包） |
| **`tools/maa_all/`** | `manage_maa.py` | `mmui2.js`、`README.md` | ⚠️ 无 .py 残留、无 `__init__.py`，不构成失效包；但 `mmui2.js`（原由 manage_maa.py 提供 Web GUI）成为**无宿主的孤儿前端**，且 README:47 提示仓库外**计划任务 MAA-ALL-GUI** 仍指向该脚本 ⇒ 外部计划任务会失败 |
| `tools/maa_channel/` | `channel.py` | `mcp_server.py`、`deploy_one_liner.txt`、`__pycache__` | ✅ **安全**：无 `__init__.py`（非包）；AST 与全量扫描均确认 `mcp_server.py` 不 import `channel`，自身自洽 |
| `_stamina_shots/` | 3 个 .py（recognize / recognize2 / summary） | 65 个 .png | ✅ 非包（无 `__init__.py`）、无存活 .py，不产生"失效包"；仅遗留图片数据 |
| `_stamina_shots2/` | 1 个 .py（recognize） | 29 个 .png | ✅ 同上 |
| `depot-plan-compare/` | 7 个 .py 全部被删 | `compare-result.json`、`penguin-cn-matrix.json` | ✅ 非包；仅遗留对比结果 JSON（其中 `compare.py` 曾引用 `models/queue_entry.py`，方向是"被删文件引用存活文件"，不构成断链） |
| `network/` | `copilot_api.py` | `__init__.py`、`api_fastapi.py`、`mcp_server.py` | ✅ **包有效**：AST 确认无任何存活模块 import `copilot_api`（仅 `server/copilot_node.mjs:2`、`server/maaorch-web.mjs:25` 两处**注释**说明"已搬到 Node"） |
| `services/ai_agent/` | `machine.py`、`humanize.py` | 13 个 .py + `__init__.py` | ✅ **包有效**：AST 确认无存活模块 import 二者（`tools/ai_agent_play_plan.py:29`、`tools/ai_navigate.py:10`、`tools/clear_runner.py:130` 引的是 `services.ai_agent.deploy / llm`，均存活） |
| `tools/` | 172 个文件 | 157 个 .py 存活 | ✅ 无 `__init__.py` 依赖问题（`tools/` 非包），仅 §3 的逐点引用需处理 |
| `tools/__pycache__/` | 1 个 .pyc | 仅 .pyc（本就没有 .py） | ✅ 不构成包；见 R9 的清理建议 |
| `ui/` | 12 个 .html | `control_server.py`、`index.html`、`web/`、若干 json | ⚠️ 无 `__init__.py`（非包）；页面靠生成器自愈（R6） |

**结论：不存在"因 `__init__.py` 被删而静默降级为命名空间包"的情况**（被删的 `__init__.py` 数量为 0；唯一进入 D 的 .pyc 在 `tools/__pycache__/`）。真正失效的只有 `tools/emu_maa_tool/server/` 与 `tools/emu_maa_tool/client/`。

---

## 5. 给 Lead 的处置建议

> 通用恢复命令（只读提示，执行由 Lead 决定）：`git checkout pre-prune-20260926 -- <path>`，或 `git show pre-prune-20260926:<path> > <path>`。243/252 个被删文件可从该 tag 恢复（§6）。

**建议 1（必做，二选一）—— 恢复 emu 工具服务**
- 若 `tools/emu_maa_tool/` 仍在用：**从 tag 恢复 5 个文件** `git checkout pre-prune-20260926 -- tools/emu_maa_tool/server/account_api.py tools/emu_maa_tool/server/emu_api.py tools/emu_maa_tool/server/maa_api.py tools/emu_maa_tool/server/auth.py tools/emu_maa_tool/server/oplog.py`（顺带恢复 `tools/emu_maa_tool/client/app.py`、`tools/emu_maa_tool/deploy_server.py`，否则 `start.bat`/`start_server.bat` 仍是坏的）。
- 若该工具已整体废弃：**改引用**，不要留半条命——同批删掉 `tools/emu_maa_tool/server/{main.py,state.py,config.py,__init__.py}`、`start.bat`、`start_server.bat`、`client/token.txt`、顶层 `README.md` 与 `*.json`，并更新 `docs/overview.md` 等文档（注意 `data/` 与 `maa_instances/` 需另行确认）。

**建议 2（必做）—— 修复 `tools/event_sweep.mjs` 的 spawn 断点**
- `tools/tap.py`：**从 tag 恢复**（`git checkout pre-prune-20260926 -- tools/tap.py`）。这是 `event_sweep.mjs:588` 的唯一坐标点击实现，改引用无处可改（等价的 adb 解析逻辑在 Node 侧没有）。
- `tools/story_skip.py`：二选一 —— ① **从 tag 恢复**（`:547` 兜底路径最省事）；② 若确定常驻服务已足够、不需要 Python 兜底，**改 `event_sweep.mjs:544-553`**：删掉 spawn 分支，直接 `log('剧情看门狗不可用：常驻服务未在跑')` 并 `return`，避免 `STORY.mode='py-watchdog'` 造成"静默不跳剧情"。
- 顺带改 `event_sweep.mjs:11` 的 `tools/bi_home.py`（已删）指向现有导航工具，或降级为纯注释。

**建议 3（必做）—— 修 `server/maaorch-web.mjs` 账号开关**
- `tools/auto_mas_toggle_user.py`：**从 tag 恢复**（GUI 路由 :877 唯一实现）；或**改引用**到替代实现（同文件 :864/:870 用的 `tools/auto_mas_rerun.py` 是存活同类，可评估是否能加 toggle 语义），并同步改 `ui/web/auto_mas.js` 的按钮文案/接口。
- 另：`network/copilot_api.py` 无需恢复（已由 `server/copilot_node.mjs` 取代，仅注释提及）。

**建议 4（必做）—— `read_nodes.py` 的 `batch_run` 依赖**
- `tools/read_nodes.py:208`：`batch_run.set_node()` 与 `batch_run.NODES_FILE` 是落表的唯一写入口。二选一 —— ① **从 tag 恢复** `tools/batch_run.py`（若整个"批量执行器"仍在计划内）；② **改引用**：把 `write_nodes()` 改为直接写 `batch_run.NODES_FILE` 对应的数据文件（需先在 `tools/read_nodes.py` 里内联该路径常量），否则 `read_nodes.py --write` 这条"人工确认闸"永久不可用。

**建议 5（必做）—— 两个 .bat 入口**
- `tools/hook.bat`：**恢复** `tools/auto_mas_hook.py`（AUTO-MAS 完成钩子是常驻服务，替代实现不在本仓）；若该钩子已废弃，**改 `hook.bat`** 删除或改指向。
- `tools/导出账号信息.bat`：**恢复** `tools/auto_mas_export.py`，或改 `导出账号信息.bat:7` 指向替代导出脚本（`tools/auto_mas_rerun.py`/`auto_mas_stats.py` 需先确认能力）。

**建议 6（可放行）—— gen_html 分区**
- `ui/push_agent*.html`、`ui/mini_*.html`：**无需恢复**，`ui/control_server.py:341-361`/`:471-477` 会调用存活的生成器自动重建。若想避免首次访问的 200~500ms 生成延迟，可**手动预生成一次**（`python tools/gen_push_ui.py` / `python tools/gen_mini_ui.py --account <UID> --emu <N> --out ui/mini_<UID>.html`），而**不是**从 tag 拉旧页面（旧页面对应旧接口，`control_server.py:725-727` 明确要求每次重传以防"按钮静默失效"）。
- `ui/_t.html` / `ui/_t2.html` / `ui/web/index.html.bak-20260921`：**确认安全，无需动作**（无任何引用）。

**建议 7（文档同步，低优先）**
- 更新 `tools/maa_all/README.md`（:8/:16/:31-38/:47）、`tools/push/README.md`（:22/:38/:51/:63）、`tools/emu_maa_tool/README.md`（:15/:54/:59）。**特别检查仓库外**：`tools/maa_all/README.md:47` 明示 `manage_maa.py` 以 **Windows 计划任务 MAA-ALL-GUI** 运行——若该计划任务还在启用状态，需 `schtasks /change` 停用或恢复脚本，否则每次触发都会失败（此项在仓库外，本审计无法验证）。

**建议 8（收尾清理，低优先）**
- 清掉 `tools/__pycache__/` 里对应已删模块的 .pyc（batch_run、tap、list_jobs、auto_mas_hook 等）；清 `.pytest_cache/v/cache/lastfailed` 的相关条目。
- `tools/_kill_my_drivers.ps1:2` 的白名单去掉 `live_drive|ga_ex_drive`（或保留，无副作用）。
- `tools/maa_channel/deploy_one_liner.txt` 疑似内嵌 `channel.py` 源码（base64）：若确认 `channel.py` 确需删除，可顺带删除或保留为恢复源；**当前判定 channel.py 删除本身安全**。

---

## 6. git 可恢复性（供恢复操作参考）

- HEAD = `e71c14e`，tag `pre-prune-20260926` 指向同一提交；`git ls-tree -r HEAD` 共 818 个跟踪文件。
- **243/252** 个被删文件在 tag 里存在 ⇒ 可直接恢复。
- **9/252 不在 git 里，无法从 tag 恢复**（均为备份/字节码，均无引用，判定可接受）：

| 不可恢复文件 | 评估 |
|---|---|
| `tools/__pycache__/audit_done_units.py.cpython-312.pyc` | 字节码残留，无引用 |
| `tools/audit_done_units.py.bak-ark-20260924_200858` | .bak 备份 |
| `tools/enter_by_template.py.bak-20260924_103227` | .bak 备份 |
| `tools/enter_by_template.py.bak-ark-20260924_145841` | .bak 备份 |
| `tools/enter_by_template.py.bak-ark2-20260924_145958` | .bak 备份 |
| `tools/enter_by_template.py.bak-ark3-20260924_150408` | .bak 备份 |
| `tools/scan_map_panels.py.bak-20260924_120609` | .bak 备份（仅 `docs/audits/20260924_1215_page_recog.md:72` 提及） |
| `tools/stage_attempt.py.bak-mas-20260924_1833` | .bak 备份 |
| `ui/web/index.html.bak-20260921` | .bak 备份（无引用） |

⚠️ 注意：`tools/audit_done_units.py`（备份的**同名主文件**）、以及 `tools/enter_by_template.py`、`tools/stage_attempt.py`、`tools/scan_map_panels.py`、`tools/panel_gate.py` 均**未在删除清单内**（只删了备份），本审计确认它们存活，且其引用者（`docs/audits/*`）都只是文档——**不要误以为这些主文件也被删了**。

---

## 7. 判定为"确认安全"的高危候选（避免 Lead 重复排查）

| 被删文件 | 为何看起来危险 | 独立验证结论 |
|---|---|---|
| `network/copilot_api.py` | 名字出现在 `server/*.mjs` | 两处均为**注释**（:2、:25），说明路由已迁到 Node；AST 无存活模块 import 它 ✅ |
| `services/ai_agent/machine.py`、`humanize.py` | `manager/manager.py` 曾被列为引用者 | `manager/manager.py` 的 "machine" 只出现在第 3、819 行英文注释（"this machine"）✅；AST 无引用 |
| `tools/maa_channel/channel.py` | 同目录 `mcp_server.py` 存活 | `mcp_server.py` 仅用 `subprocess`/`base64`/`FastMCP`，不 import channel ✅ |
| `tools/observe_job.py`、`tools/batch_run.py`、`tools/bi_home.py`、`tools/daily_state.py`、`tools/op_stats.py` 等文档命中项 | 出现在 docs 或同名词 | `daily_state` 命中的是数据目录 `data/daily_state`；`op_stats` 命中的是字典键 `out["op_stats"]`；`observe_job.py` 仅在 2 篇 design doc 提及 ✅ |
| `ui/_t.html` | 曾 200+ 命中 | 全部来自 `import time as _t` 别名（误报）✅ 真引用数 0 |
| `tools/bridge.py` | `infrastructure/maa_core.py` 命中 | 命中内容是模块 docstring 里的英文 "ctypes bridge" ✅ |
| core_dead 分区其余项、root_junk 全部（depot-plan-compare / _stamina_shots*） | — | 无存活代码引用 ✅ |

---

## 8. 可复现说明

审计脚本以只读方式运行，逻辑为：
1. 解析 manifest → 集合 D（252 条，正斜杠归一化）；
2. 递归枚举仓库（跳过 `runtime/ data/ debug/ .git/ node_modules/ services/maa/ __pycache__/`）→ 存活文件集；
3. 对每个存活文本文件做 basename / stem / 点分模块路径三键匹配 + 带引号路径正则匹配；
4. `ast.parse` 全部存活 .py，取 `Import`/`ImportFrom`（含函数内），按 importer 目录逐级上溯解析，未解析且命中 D 者报为断链；
5. 以 D 驱动按目录分组，统计删后存活 .py 与 `__init__.py` 存活情况；
6. `git ls-tree -r HEAD` 对照 D，识别不可从 tag 恢复的 9 个文件。

审计过程中**未修改、未删除任何文件**，未执行任何 git 写操作（仅 `git tag -l` / `git log` / `git ls-tree` / `git status` / `git rev-parse` 等只读命令）。
