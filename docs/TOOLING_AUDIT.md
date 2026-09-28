# 工具审计与收拢：六个大脚本的契约 / 一致性 / 异常 / 冗余审查

- 卡：kanban `t_1353bef3`（dsh-lead 创建）
- 审计时间：2026-09-28 09:12–09:35（本机 D:\1-2\MAAOrch）
- 审计员：profile `mas`（kanban worker）
- 约束遵守：**只读 + --help/--selftest/dry-run**；未 `--apply`、未发车、未改配置、未改任何脚本。
  本轮唯一的写动作 = 六个工具自己的正常审计/产出落盘（见附录 B「副作用声明」）。
- 证据目录：`docs/audits/2026-09-28_tooling_audit/`（33 个探针输出 + `_probe_table.md` + `_probe_summary.json` + 探针脚本）
- 第二轮复核（09:45–10:10，attempt-1 崩溃后的重跑）：抽样对照源码复核关键论断（行数 / git 状态 / P1-1 / P1-3 / P2-5 / P2-6 / 两处 CLI 收口）**全部一致**；补齐探针脚本与静态扫描副本入库（`_static_scans/`；AST 复扫 JSON sha256 与首轮一致）、刷新 §6 重复度计数（10:01 复扫）、修正 P2-5/P2-9/mcp except 行号表述、补 P1-2 的现场并发证据与租约接入路径（改动处标「第二轮/补记」）。
- 审计时的环境：REST 可达（`code=200`）；机队有在跑任务（MAA_6 `MAA.exe` pid 1800、fz-maa l-1 pid 35688），因此 G3 的拒绝路径天然被实测到（不是构造的）。

评分口径：**可用性** = 现在能不能用、异常路径是否可靠；**一致性** = 跨工具的 CLI/退出码/审计契约是否统一；**风险** = 在「每天用」的前提下还残留多少会误导人或损坏状态的可能。
A = 无阻塞问题；B = 小问题；C = 有明确需要修的契约缺口。

## 1. 结论摘要

| 工具 | 规模 | 可用性 | 一致性 | 风险 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| mas_run_profile.py | 2454 行 / 122 KB | A | B | B | 闸门/还原/审计最完整；两处收口问题：幽灵 `--dry-run`、跨族单飞盲区 |
| mas_ondemand.py | 1979 行 / 104 KB | A | A- | B+ | 单飞最严（5 类冲突 + 共锁 + 共享账本）；dry-run 被拒也 rc=2（与 probe_farm 不一致） |
| mas_plan_builder.py | 1578 行 / 86 KB | A | B | A- | 复用 run_profile 校验做得好；缺退出码文档、计划文件分钟粒度可被同分钟覆盖 |
| mas_ondemand_mcp.py | 1540 行 / 85 KB | A- | C+ | B+ | MCP 面与 CLI 共账共锁；CLI 面没有 argparse：`--help`/未知参数会静默起 stdio server |
| mas_probe_farm.py | 1433 行 / 74 KB | A | B- | B | 判定/锁/台账都对；`--json` 主路径失效、内层被拒外层仍 rc=0 会误导自动化 |
| stage_capability.py | 989 行 / 49 KB | A | C | C+ | 六者唯一「默认写盘 + 无锁 + 非原子写」；下游读者（probe_farm/plan_builder）会受到污染风险 |

合计 **9973 行**（其中 selftest 套件 ≈1600 行，16%）。六个文件在 git 里**全部未入库**（`?? tools/…`），无回滚基线。

**六条必须处理的结论**（详单见 §5）：

1. `mas_run_profile.py` 文档教的 `--dry-run` 参数不存在，实测 rc=2（P1-1）。
2. run_profile/probe_farm 与 ondemand/班次/MCP **互不可见**：各自锁各自账，跨族发车只靠「现场 MAA.exe」兜底，存在发车空档窗口（P1-2）。
3. `stage_capability.py` 三处**非原子写且无锁**，`probe_farm` 更新完就立刻读它 —— 半截 JSON 会导致能力误判（P1-3）。
4. 六个工具未入库（P1-4，建议随修复卡一次 commit）。
5. `mas_probe_farm.py --json` 对 dry-run/`--list`/`--status` 无效（仅 selftest 与 apply 收尾摘要生效）；且 dry-run 内层计划被拒时外层仍 rc=0（P2-5/6）。
6. selftest 失败退出码三套并存（3 / 2 / 1），`mas_ondemand_mcp.py` 的 CLI 面拒绝一切未知参数的处理（P2-7/9）。

## 2. 方法与范围

- **静态**：AST 扫描（CLI 参数与默认值、裸异常/宽异常、except→pass、超长函数、未用导入、跨文件重复定义）。脚本与扫描输出存 `docs/audits/2026-09-28_tooling_audit/_static_scans/`（`tool_audit_ast` / `dup_inventory` / `count_dups` / `unused_imports` / `dump_cli` / `dump_fns` / `check_iface` + stdout/JSON；第二轮复扫的 `tool_audit_ast.json` 与首轮 sha256 一致 = `fda686c9…`）。
- **动态**：33 个只读探针（`--help` ×6、`--selftest` ×6、dry-run/只读命令 ×21），全用文档指定解释器 `Python312`。
  三处 rc≠0 是**设计内拒绝**（G3 资源在跑），另两处是本次抓到的缺陷（见 §5）；全部 33 条汇总见附录 A。
- **没做**：任何真实写动作（`--apply`）、真实 MCP 客户端握手（用 `--check` + 18 例 selftest 覆盖 MCP 面）、对宿主 E:\AUTO-MAS 的写侧验证（超出只读约束）。
- 六工具 selftest 全过：run_profile 29/29、ondemand 30/30、plan_builder 37/37、ondemand_mcp 18/18、probe_farm 23/23、stage_capability 全过。

## 3. 逐工具

### 3.1 mas_run_profile.py（按 profile 一次发车：备份→写→preflight→发车→读回→等完→还原→审计）

- 最长函数：`selftest` 412、`build_plan` 164、`_apply_locked` 163、`validate_profile` 123、`restore_run` 105。
- 异常：0 裸 except；13 处 `except Exception` 全部带 `# noqa: BLE001` 与日志/落账（如 L1305-1310 stop 失败也落 `stop_resp`）；1 处 `except TransportError: pass`（L1315，轮询解耦重试，可接受）。
- 审计：`runtime/mas_run_profile_log.jsonl`（每步 event）+ `runtime/mas_ledger.jsonl`（kind=run_profile / run_profile_restore，带 `LEDGER_LOCK` 120s 陈旧抢占；L189-208）。
- 并发：独占锁 `runtime/mas_run_profile.lock`（L92，陈旧 6h、长跑心跳 `touch`；L244-278）。dry-run 路径**不**持锁（正确，L1559-1566）。
- 闸门：G0 NO_GO / G1 REST / G2 目标（多号脚本必须显式 --all-accounts 或 --disable-others）/ G3 锁 / G4 `mas_preflight.py --script` 必须 GO / G5 读回不符立刻 stop；`try/finally` 强制还原、state 先落盘（L1604-1605）。
- 实测：dry-run rc=0（计划完整，含预期写入步骤与读回断言）；`--status`/`--list-plans`/`--reconcile` rc=0；未知 run_id rc=2（正确）；**文档里的 `--dry-run` 组合 rc=2**（缺陷 P1-1）。
- 遗留问题：跨族单飞盲区（P1-2）；退出码 130/3 未入文档（P3-14）；`--timeout` 类参数 6 个语义各不相同但命名相近（P3-12）。

### 3.2 mas_ondemand.py（按需发车：队列/脚本/单号/唤醒/停/看在跑/清/放行）

- 最长函数：`selftest` 435、`cmd_dispatch` 91、`cmd_wake` 77、`cmd_cleanup` 72。
- 异常：0 裸；13 处宽 except 全带标签；唯一 `except: pass` 是 stdout 编码重设（L1928，无害）。
- 审计：两流 —— 全量调用 `runtime/mas_ondemand_log.jsonl`（含 dry-run/被拒，`src=cli`）+ 真动作 `runtime/mas_dispatch_log.jsonl`（与班次同源，`source=ondemand`）；状态在共享账本 `runtime/mas_ondemand_ledger.json`（CLI/MCP 唯一账本，旧账只读合并）。
- 并发：真写动作持 `runtime/mas_dispatch.lock`（L97 = `md.LOCK`，与 cron/班次/MCP 同一把；陈旧阈值 `MAAORCH_DISPATCH_LOCK_STALE_S` 默认 1500s）。
- 闸门：G0-G5 全；G3 五类冲突（duplicate / resource / shift / slot_busy / slot_warm）——`slot_warm` 专门覆盖「脚本间空档 MAA.exe 消失 1-2 分钟」的假空闲，这是六个工具里最严的单飞实现。
- 实测：`--list-running` rc=0（12 槽位快照 + 账本在场记录正确解析）；`--account` / `--script` / `--wake` dry-run 均 rc=2 —— 三条都是**设计内 G3 拒绝**（与在跑任务资源重叠 / 目录正在跑 MAA），拒绝文案带 gate 名与证据；`--cleanup` dry rc=0（显示 1 条 06:31 的临时队列「将删除」）。
- 遗留问题：dry-run 被拒 rc=2 与 probe_farm 的 rc=0 口径不一（P2-6 的另一面）；CLI 有 wake/cleanup/release，MCP 面没有（有意的权限收敛，但 agent 从 MCP 做事时需回落到 CLI，记 P3）。

### 3.3 mas_plan_builder.py（干员+仓库+能力台账+活动 → 计划草案）

- 最长函数：`build_all` 186、`selftest` 185、`render_md` 145。
- 异常：0 裸；5 处宽 except 带标签；`except ValueError: pass` 是时间解析守卫（L545）。
- 复用：`validate_profile_with_tool()` 直接 import `mas_run_profile.validate_profile`（L763-768），保证「生成的 profile 一定过 run_profile 的校验」——这是六者中最好的复用模式，值得在其他重写里保留。
- 审计：`runtime/mas_plan_builder_log.jsonl`（plan_built / plan_refused / exec_dry_run / exec_apply）。
- 并发：无锁（不必要）；产出文件名 `账号_yyyymmdd_HHMM`（STAMP_FMT L57）→ **同账号同分钟两次运行会互相覆盖**（P3-13，低概率）。
- 实测：`--list-accounts` rc=0；`--account y9Flipped --json --out-dir <证据目录>` rc=0，产出 md+json、`probe_needed` 51 项、warnings 与 run_profile 完全同文（因复用校验器）。
- 遗留问题：没有「退出码」文档段（P3-11）；文档「默认零写」容易被读成「什么都不写」——本地计划文件仍会落 `runtime/reports/plans/`（P3-11）。

### 3.4 mas_ondemand_mcp.py（同能力的 MCP 工具面，6 个工具）

- 最长函数：`selftest` 358、`tool_dispatch_account` 169、`tool_stop_task` 92、`tool_preflight` 79。
- 异常：0 裸；29 处宽 except（标签齐）+ 4 处 `except: pass`：`_child_env` 回灌失败降级（L129-133，退回 `os.environ`）、stop 已知记录回溯（L819，mas_dispatch state 查不到不报）、preflight 目标面解析（L897/L903，queue_map/script_map 查询）——都属「打点/解析失败不拖垮主流程」，可接受但建议统一改成显式 `log()`。
- MCP 面：6 工具（list_running / dispatch_queue / dispatch_script / dispatch_account / stop_task / ondemand_preflight），`apply` 全默认 false；与 CLI **同账本、同审计流、同锁**（selftest 覆盖跨端单飞与共锁）。
- CLI 面（缺陷）：`__main__` 只识别 `--check` / `--selftest`，**其余参数一律忽略并直接启动 stdio server**（L1533-1540）。实测（第二轮复跑，`ondemand_mcp_cli.recheck.txt`）：`--help` 与 `--bogus` 均**无任何输出**、各约 10s 后 rc=0（stdin 关闭即 EOF 退出——未知参数连报错都没有）；stdin 不 EOF 时直接常驻（`--bogus` 12s 未退出被 timeout 强杀，rc=124）。对「先跑 `--help` 看看」的人/脚本是陷阱（P2-9）。
- 实测：`--check` rc=0，一次打印就绪信息 + 内嵌 dry-run（`dispatch_queue(日常定时1) would_dispatch`）——MCP 侧的 dry-run 路径已被验证；`--selftest` 18/18。

### 3.5 mas_probe_farm.py（先试探后开刷，复用 run_profile 骨架）

- 最长函数：`run` 302、`selftest` 171、`judge_probe` 80。
- 异常：0 裸；11 处宽 except 带标签；2 处 `except ValueError: pass` 是日志字段解析守卫（L424/448）。
- 复用：以**子进程黑盒**方式调 `mas_run_profile.py`（`run_rp` L537-559，超时/rc/JSON 摘要）、以模块 import 复用 `ledger_append`/`scrub`/`normalize_stage`（`_rp()`）、以子进程调 `stage_capability.py --update` —— 依赖方向干净（不 import 目标内部函数做写操作）。
- 并发：独占锁 `runtime/mas_probe_farm.lock`（L69，6h+心跳）；内层 run_profile 自带锁与还原。
- 审计：`runtime/mas_probe_farm_log.jsonl` + `runtime/mas_ledger.jsonl`（kind=probe_farm[_refused]）+ `runtime/probe_farm/<run_id>/`（profiles/plan.txt/证据）。
- 实测：dry-run rc=0；台账 y9Flipped×1-7=success ⇒ 决策 farm（正确）；`--list unknown` rc=0（unknown 池解析正确）。
- 缺陷：① `--json` 生效面窄：只有 `--selftest`（L1382）与 **apply 收尾摘要**（`run()` 尾 L1041）走 JSON；**dry-run**（L857-871 提前 return 打计划文本，实测 `--json` 与不带时输出同形）、`--list`（L1083）/`--status`（L1122）全为人读文本（P2-5）；② dry-run 内层 `mas_run_profile` 计划被 G2 拒（rc=2）时，外层仍 rc=0（`summary["rc"]=0`，L869-871）——内层 rc 只进了 `plan.txt`/输出（P2-6）。

### 3.6 stage_capability.py（账号×关卡能力台账，唯一生成者）

- 最长函数：`render_md` 108、`classify_attempt` 106、`aggregate` 100。
- 异常：0 裸；仅 1 处宽 except（L836）+ 若干 `(OSError, ValueError)` 窄捕获；没有裸吞。
- **契约与其余五个相反**：写盘是默认行为，dry-run 要 `--no-write`（L963）；`--build/--update` 会直接覆盖 `runtime/state/stage_capability_state.json` + `runtime/reports/stage_capability.json/.md`。
- **非原子写 ×3**：`save_state` L407-410、`_finish` L755-762、`cmd_local` L776-780 都是 `open(path,"w")` 直写；对比 run_profile/plan_builder 的 `save_json` 是 tmp+`os.replace`。**没有任何锁**：两个 `--update` 并发 = 读-改-写互相覆盖；读者（`probe_farm` 更新完立即读、`plan_builder` Capability.load）可能读到半截 JSON。
- 审计：**没有独立审计流**（谁在什么时候重建了能力台账不可追溯；报告内只有 built_at/source 元数据）。
- 实测：`--account`/`--rebuild-local --no-write`/`--check`/`--selftest` 全 rc=0；文档的 import 接口实测可用（`can_farm("y9Flipped","1-7")="success"`，未知账号返回 `unknown` 不猜）。
- 遗留问题：P1-3（原子写+锁）、P3-15（退出码 1 双义：文档「未预期错误」+ selftest 失败）。

## 4. 横向契约矩阵

| 维度 | run_profile | ondemand | plan_builder | ondemand_mcp | probe_farm | stage_capability |
| --- | --- | --- | --- | --- | --- | --- |
| 默认 dry-run | ✅（靠缺省 --apply） | ✅ | ✅（上游动作） | ✅（apply 默认 false） | ✅（另有显式 `--dry-run`） | ❌ 默认写盘，`--no-write` 干跑 |
| `--dry-run` 参数 | ❌（文档却写了 → P1-1） | ❌ | ❌ | n/a | ✅（六者唯一） | ❌ |
| `--json` 机读 | ✅ 全命令 | ✅ 全命令 | ✅ | n/a（MCP 结构化返回） | ⚠️ dry-run/`--list`/`--status` 忽略（P2-5） | ⚠️ 仅 --account/--json 报告 |
| 拒绝 rc（dry-run） | 2 | 2 | 2 | n/a | ⚠️ 0（P2-6） | 2 |
| selftest 失败 rc | 3 | 2 | 3 | 2 | 3 | 1（P2-7 三套） |
| 互斥锁 | mas_run_profile.lock（6h） | mas_dispatch.lock（1500s，与 cron/MCP 共） | 无（无需） | 同 ondemand | mas_probe_farm.lock（6h） | **无**（P1-3） |
| 审计流 | 自有 log + mas_ledger | 调用流 + 事件流 + 共享账本 | 自有 log | 同 ondemand | 自有 log + mas_ledger | **无**（P1-3/7） |
| 危险动作闸门 | G0-G5 + finally 还原 | G0-G5 + 5 类单飞 | 本地写 + 需 --apply 才触上游 | 同 ondemand | NO_GO + 台账 + 内层闸门 | 不触上游（只读扫描） |
| 跨族可见性 | ❌ 不读任何账本（P1-2） | ✅ 读自己族账本+班次 state | n/a | ✅ 同左 | ❌ 同 run_profile | n/a |

## 5. 必须修的清单（按优先级）

> 每一条都可独立开卡；建议修法附在后面。P0 为空：没有正在造成损坏的缺陷。

### P1（本周内修，改动小、收益大）

**P1-1｜`mas_run_profile.py` 幽灵 `--dry-run`**（文档 bug，5 分钟）
- 证据：docstring `mas_run_profile.py:42` 教「`--profile P.json --dry-run --preflight`」；argparse（L2392-2420）无此参数；实测 rc=2 `unrecognized arguments: --dry-run`（证据 `run_profile_docclaim_dryrun_flag.txt`）。
- 影响：照文档操作的 agent 拿到 rc=2，会把「参数不存在」误判成「闸门拒绝/实例被占」，可能重试或改单选错路。
- 建议：**加 `--dry-run` 无操作开关**（与 probe_farm L1371 一致；同时也让 `--apply --dry-run` 组合语义显式），或退回改文档；二者都要顺手补一条 selftest 用例。

**P1-2｜跨族单飞缺口：run_profile/probe_farm ↔ ondemand/班次/MCP**
- 证据：① run_profile 只 import `mas_ondemand` 取 ssh 基建（L307-312），不读 `mas_ondemand_ledger.json` / `mas_dispatch` state；② ondemand 全文无 run_profile 引用；③ `mas_preflight.py` 的槽位检查是**进程态**（L15、L443-463：远端 Win32_Process 里有没有 MAA.exe），不含任何在途账本；④ 本轮实测 ondemand 的 G3 恰恰抓到「wake 后 MAA 还没起/已停任务」的窗口（`resource_inflight`，`mas_ondemand_log.jsonl` 09:20:58）。
- 补记⑤（第二轮）：**并发在本机是常态**——审计窗口内（09:31:58）另一 agent 进程向共享台账 `runtime/mas_ledger.jsonl` 写入 `kind=farm_orchestrator` 记录（`fo_20260928_093134_Raiz`，dry-run 卡在 preflight rc=2）；同层还有第 7 个工具 `mas_farm_orchestrator.py`（自有锁 + 已接入实例租约，本轮六工具之外）。
- 影响：run_profile 发车时若 ondemand/班次刚发过车但 MAA.exe 未起（或处于 ondemand 文档里的 `slot_warm` 空档），G4 会判 GO → 对同一实例双发。反向同理：run_profile 在写配置期间对 ondemand 不可见。
- 建议（择一或组合；第二轮补记：**优先评估 d**）：
  a. 抽 `check_single_flight`/`open_runs`（现于 `mas_ondemand.py:356-388`）进共享模块，run_profile 发车前查同一批来源（ondemand 账本 + `md.load_state()` + 现场进程）；
  b. run_profile 写配置前，把自己的 in-flight 记录落进 `mas_ondemand_ledger.json`（kind=run_profile_hold），ondemand 的 `resource_inflight` 自然覆盖它；
  c. 补一条 selftest：ondemand 在场记录存在时 run_profile 必须拒发。
  d. **（第二轮补记，优先评估）接入现成的实例租约**：`docs/MAS_INSTANCE_LEASE.md` §5.2/§5.4 已给出 ondemand / run_profile / probe_farm 的接入骨架（preflight 通过后、备份配置前 `acquire`；长跑在 `touch()` 心跳处 `renew`；跑完 `release`），`tools/mas_instance_lease.py` 已交付（t_65b69753，含真·多进程互抢自测）。目前唯一消费者是 `mas_farm_orchestrator.py`，六工具零接入 —— 租约是「跨任务/跨工具的实例层占位」，比互读账本更直接覆盖本卡盲区（接入后 a/b 可降为兜底）。

**P1-3｜`stage_capability.py` 非原子写 + 无锁**
- 证据：L407-410 / L755-762 / L776-780 直写三个文件；全文件无锁；同族对比：`mas_run_profile.save_json`（L172-177）与 `mas_plan_builder.save_json`（L95+）都是 tmp+`os.replace`。
- 影响：`probe_farm` 跑 `--update` 后立刻读台账（`mas_probe_farm.py:1063` → `ledger_lookup`），并发窗口内可读到半截 JSON → 能力误判 unknown/failed；两个 agent 同时 `--update` 会互相覆盖 state。
- 建议：三处改 tmp+`os.replace`；`_finish` 的读-改-写加一把 `runtime/stage_capability.lock`（可复用 `md._dispatch_lock` 协议）；顺带把 `--no-write` 语义在 docstring 顶部与 `--build` 帮助里写明（「本工具默认写盘，干跑用 --no-write」）。

**P1-4｜六个工具未入库**
- 证据：`git status` → 六个文件全 `??`（runtime/ 被 ignore 没问题，但 tools/ 里的源码没进 git）。
- 影响：无 diff/回滚/追责基线；这六个又是每天要用的。
- 建议：随修复卡分次 commit（建议按「fix 一起提交对应文件」），避免一次大 commit 掩盖回归。留给 lead 决定是否连带把本批其他新工具一起入库。

### P2（下次窗口修）

**P2-5｜`probe_farm --json` 生效面窄**：flag L1377 说明「JSON 输出（单行）」，实际只有 `--selftest`（L1382）与 apply 收尾摘要（`run()` 尾 L1041）用 JSON；dry-run 分支（L857-871 提前 return 打计划文本；实测 `--json` 与不带时输出同形 → `probe_farm_dryrun_json.txt`）、`cmd_list`（L1083）、`cmd_status`（L1122）全打人读文本。修法：dry-run/`--list`/`--status` 也支持 `--json`（dry-run 输出与 `summary` 同构的摘要），人读文本保留为默认。

**P2-6｜probe_farm 外层 rc 掩盖内层拒绝**：dry-run 内层 run_profile rc=2（G2 多号需显式）时外层 rc=0（证据 `probe_farm_dryrun.txt`、`probe_farm_dryrun_json.txt`）。自动化会把「计划被拒」当「计划成立」。修法：外层 rc 取内层最差 rc（或 ≥2），并在 JSON 里给 `inner_rc`。

**P2-7｜selftest 失败退出码统一**：run_profile/plan_builder/probe_farm=3，ondemand/ondemand_mcp=2，stage_capability=1。修法：统一为 3（与「≥2 表示不正常」兼容，不撞 `--apply` 语义），或全部按「2=输入/环境问题、3=自测失败」写进各 docstring。

**P2-8｜审计 schema/流统一**：现状 6 种记录形状（`event=` / `kind=` / `verdict=`、有无 `ok`）、5 条流 + 全局 `mas_ledger.jsonl`（只收 run_profile/probe_farm）。修法：在公共模块卡里定一份 `{ts, tool, kind, ok, run_id, target, detail}` 最小 schema，各工具逐步替换；`stage_capability` 至少补一条写入审计（谁重建、扫了几次、state 行数）。

**P2-9｜`mas_ondemand_mcp.py` CLI 面收口**：加 argparse：`--help`（列出 6 个 MCP 工具与用法）、`--check`、`--selftest`，未知参数报错退出 2；`--version` 可选。不动 stdio server 行为。

### P3（顺手做）

- **P3-10** 文档计数陈旧：`docs/MAS_ONDEMAND.md:47`「27 例」应为 30（同文件 L122 已是 30 例）。其余文档计数核对无误（run_profile 29 ✅、plan_builder 37 ✅、probe_farm 23 ✅、MCP 18 ✅）。
- **P3-11** `plan_builder` 补「退出码」段；「默认零写」改成「**上游**零写；本地计划 `.md/.json` 会写 `runtime/reports/plans/`」。
- **P3-12** `--timeout` 语义 overload：ondemand=ssh 快照 240s、preflight 另有 `--preflight-timeout`；probe_farm=试探等待 600s；stage_capability=SSH 1500s；plan_builder 是 `--rest-timeout`+`--max-wait-seconds`。建议统一后缀（`--ssh-timeout` / `--wait-timeout` / `--scan-timeout`）。
- **P3-13** `plan_builder` 计划文件名分钟粒度（`STAMP_FMT="%Y%m%d_%H%M"`，L57/L1060-1063）→ 同账号同分钟重跑覆盖前次；建议加秒或 run_id 短哈希。
- **P3-14** `run_profile` 退出码文档补 130（Ctrl-C）与「selftest 失败=3」；`--restore-run` 未知 id=2 说明清楚。
- **P3-15** `stage_capability` 退出码 1 双义（文档「未预期错误」+ selftest 失败）→ selftest 失败改用 3 或 2。
- **P3-16** MCP 面缺 wake/cleanup/release（有意收敛）→ 在 `docs/MAS_ONDEMAND.md` 已能看出；建议在 MCP 工具描述里各写一句「这三种动作走 CLI」。

## 6. 重复实现与公共模块建议

**重复度实测**（`tools/*.py` 定义同名顶层函数者计数；下表为**第二轮复扫值**（10:01，209 个文件），复扫输出 `_static_scans/count_dups.stdout.txt`）：

| 函数 | 份数 | 分布（节选） | 目标六工具中的重复 |
| --- | --- | --- | --- |
| `ssh_ps` | **14** | ondemand / run_profile / stage_capability / preflight / scheduler … | ×3 |
| `selftest` | **29** | 几乎全部工具 | ×5（stage_capability 为内联格式，功能上 ×6） |
| `scrub` | 12 | run_profile / plan_builder / probe_farm / farm_orchestrator … | ×3 |
| `load_state` / `save_state` | 10 / 9 | 六工具中 ×3（语义各不相同，同名不同物） | ×3 |
| `_now` | 12 | run_profile / ondemand / ondemand_mcp / plan_builder / probe_farm … | ×5 |
| `post`（REST） | **9** | ondemand / run_profile / preflight / dispatch / maa_error_daily … | ×2 |
| `audit` | 9 | ondemand / run_profile / plan_builder / probe_farm … | ×4 |
| `load_json` / `save_json` | 10 / 6 | run_profile / plan_builder / probe_farm … | 各 ×3 |
| `reachable` | 5 | run_profile / ondemand / preflight / dispatch | ×2 |
| `norm_dir` | 5 | run_profile / ondemand / preflight / dispatch | ×2 |
| `_lock` / `hold_lock` | 3 / 2 | run_profile / ondemand（+instance_lease）；probe_farm（+farm_orchestrator） | ×2 + ×1 |
| `ledger_append` | 4 | run_profile / probe_farm / stage_fix / activity_switch | ×2 |
| `_append_jsonl` | 2 | ondemand / ondemand_mcp | ×2 |
| `_ssh_base` / `_child_env` / `_mask` | 3 / 2 / 3 | 六工具内各 1；其余在 preflight / observe_mcp / identity_trace / maa_error_daily | ×1 |
| `pick_python` | 2 | run_profile / ondemand | ×2 |
| `_rel` | 2 | run_profile / plan_builder | ×2 |
| `_env_int` | 3 | ondemand / dispatch / maa_upgrade_check | ×1 |

（首轮扫描 09:28 / 200 文件 → 第二轮复扫 10:01 / 209 文件：仓库当天新增工具导致绝对值上浮（selftest 23→29、scrub 10→12、_now 10→12、ssh_ps 13→14、load_json 9→10）；六工具内重复列亦按复扫精确化（如 post ×3→×2、_ssh_base 等 ×2→×1）。「应抽公共模块」结论不变。）

**建议抽取（按收益排序，别大爆炸重构）**：

1. `tools/mas_infra.py`——REST + JSON + 文本：
   `post`（绕代理 + 退避 + 异常分类）、`reachable`、`fetch_scripts`/`fetch_users`、`resolve_script`、`pick_python`、`norm_dir`/`dkey`、`slug`、`_rel`、`load_json`/`save_json`（原子）、`scrub`、`sha256_json`、`_env_int`、`log`/`_now`。
   首替换：run_profile ↔ ondemand（两份 `post`/`fetch_*` 已在语义上漂移，例如 run_profile 的 `post` 有 `tries=4` 退避、ondemand 的无）。
2. `tools/mas_ssh.py`——`ssh_ps`（13 份里选最稳的一份：现分散在 ondemand 与 `maa_error_daily._ssh_exe/_child_env` 两处血泪修复版）、`read_host_file`/`read_host_tail`、`host_probe`/`host_snapshot`、`_ssh_base`。run_profile 现在借道 `mas_ondemand` 拿 ssh，属反向依赖，应解耦。
3. `tools/mas_locks.py`——统一「O_EXCL + 陈旧抢占 + 心跳 touch」协议（run_profile/probe_farm 自写两份，ondemand 复用 `mas_dispatch._dispatch_lock`）；陈旧阈值参数化（run_profile 6h、dispatch 1500s）。
4. `tools/mas_audit.py`——`audit`/`_append_jsonl`/`ledger_append` + §P2-8 的最小 schema；全局台账 `mas_ledger.jsonl` 收拢所有工具摘要行。
5. `tools/selftest_kit.py`——`case()/ck()` 跑壳 + `SELFTEST=OK (p/q)` 打印 + 失败 rc 映射（23 份重复，且是 P2-7 的根源）。
   （另：现有 `mas_ondemand_ledger.py` 是 6 号卡建的好样板——「共享账本」方向已验证可行，按同模式继续。）

**粗估收益**：六个工具内可直接消除 ≈700–900 行重复代码（约占其 9973 行的 8%），并把「修一处、六个工具受益」的缺陷（如 env 修复、原子写、rc 口径）收敛到单一实现。

## 7. 正面结论（值得保留的做法）

- **闸门与还原**：run_profile 的 G0-G5 + `try/finally` 还原 + state 先落盘 + `--restore-run` 补账，是六者中最完整的失败闭环；probe_farm 的「台账三态决策」把「不猜」落到了代码。
- **单飞**：ondemand 的 5 类冲突判定（含 `slot_warm` 空档）与「共享账本 + 共锁」（CLI/MCP/cron 三方）是全机队最严的一份，应作为标准的参照实现。
- **审计与脱敏**：所有工具都脱敏（`scrub`），有 `LEDGER_LOCK` 的全局台账，`mas_ondemand_log.jsonl` 连干跑/被拒都留痕。
- **自测**：6 套 selftest 共 156+ 用例全过，且都是「假传输、零触网、沙箱 runtime」，可以在任何窗口跑。
- **文档**：4 份工具文档 + 1 份机制规范与实现基本一致（仅 P3-10 一处计数陈旧），关键「别重走」的坑（env/PATH、slot_warm、代理 502）都写进了注释与文档。

## 附录 A：33 个只读探针汇总（rc 与耗时）

探针脚本：`docs/audits/2026-09-28_tooling_audit/run_probes.py` + `build_probe_table.py`（由 txt 重建 `_probe_table.md`/`_probe_summary.json`；Python312；不含任何 `--apply`；每条的完整 stdout/stderr 在同目录同名 `.txt`）。两个脚本原只留在会话 scratch，第二轮已复制进证据目录；第二轮另有两条只读复跑：`run_profile_docclaim_dryrun_flag.recheck.txt`（P1-1 复现 rc=2）、`ondemand_mcp_cli.recheck.txt`（P2-9 复现：`--help`/`--bogus` 均静默 ~10s/rc=0；stdin 不 EOF 则常驻，12s 被强杀 rc=124）。

| # | 探针 | 命令（省略 python.exe 前缀） | rc | 秒 |
| --- | --- | --- | --- | --- |
| 1 | help_ondemand | `mas_ondemand.py --help` | 0 | 1.0 |
| 2 | help_ondemand_mcp | `mas_ondemand_mcp.py --help` | 0 | 10.0 |
| 3 | help_plan_builder | `mas_plan_builder.py --help` | 0 | 0.5 |
| 4 | help_probe_farm | `mas_probe_farm.py --help` | 0 | 1.3 |
| 5 | help_run_profile | `mas_run_profile.py --help` | 0 | 1.1 |
| 6 | help_stage_capability | `stage_capability.py --help` | 0 | 0.8 |
| 7 | ondemand_cleanup_dry | `mas_ondemand.py --cleanup --json` | 0 | 0.8 |
| 8 | ondemand_dryrun_account | `mas_ondemand.py --account y9Flipped --json` | 2 | 5.1 |
| 9 | ondemand_dryrun_script | `mas_ondemand.py --script 明日方舟-官-1-1-7 --json` | 2 | 2.2 |
| 10 | ondemand_list_running | `mas_ondemand.py --list-running --json` | 0 | 2.3 |
| 11 | ondemand_mcp_check | `mas_ondemand_mcp.py --check` | 0 | 10.4 |
| 12 | ondemand_wake_dry | `mas_ondemand.py --wake 明日方舟-官-1-1-7 --json` | 2 | 2.4 |
| 13 | plan_builder_dryrun | `mas_plan_builder.py --account y9Flipped --json --out-dir <证据目录>/pb_out` | 0 | 1.3 |
| 14 | plan_builder_list_accounts | `mas_plan_builder.py --list-accounts` | 0 | 0.6 |
| 15 | probe_farm_dryrun | `mas_probe_farm.py --account y9Flipped --stage 1-7 --times 2` | 0 | 3.9 |
| 16 | probe_farm_dryrun_json | 同上 + `--json` | 0 | 8.5 |
| 17 | probe_farm_list_unknown | `mas_probe_farm.py --list unknown` | 0 | 3.4 |
| 18 | run_profile_docclaim_dryrun_flag | `mas_run_profile.py --profile runtime/templates/run_profile.example.json --dry-run` | **2** | 0.6 |
| 19 | run_profile_dryrun | `mas_run_profile.py --profile runtime/templates/run_profile.example.json` | 0 | 1.4 |
| 20 | run_profile_dryrun_json | 同上 + `--json` | 0 | 2.1 |
| 21 | run_profile_list_plans | `mas_run_profile.py --list-plans` | 0 | 1.7 |
| 22 | run_profile_reconcile_dry | `mas_run_profile.py --reconcile --json` | 0 | 0.7 |
| 23 | run_profile_restore_run_bogus | `mas_run_profile.py --restore-run rp_nonexistent --json` | 2 | 0.6 |
| 24 | run_profile_status | `mas_run_profile.py --status --json` | 0 | 1.1 |
| 25 | selftest_ondemand | `mas_ondemand.py --selftest` | 0 | 1.7 |
| 26 | selftest_ondemand_mcp | `mas_ondemand_mcp.py --selftest` | 0 | 16.2 |
| 27 | selftest_plan_builder | `mas_plan_builder.py --selftest` | 0 | 1.3 |
| 28 | selftest_probe_farm | `mas_probe_farm.py --selftest` | 0 | 0.8 |
| 29 | selftest_run_profile | `mas_run_profile.py --selftest` | 0 | 24.7 |
| 30 | selftest_stage_capability | `stage_capability.py --selftest` | 0 | 0.6 |
| 31 | stagecap_account | `stage_capability.py --account y9Flipped --json` | 0 | 0.5 |
| 32 | stagecap_check | `stage_capability.py --check` | 0 | 7.7 |
| 33 | stagecap_rebuild_local_nowrite | `stage_capability.py --rebuild-local --no-write` | 0 | 0.8 |

rc≠0 的五条：8/9/12 = 设计内 G3 拒绝（在跑任务资源重叠 / 目录有 MAA.exe）；18 = P1-1 缺陷；23 = 未知 run_id（预期 2）。

## 附录 B：副作用声明（本次审计产生的一切写动作）

- `runtime/mas_run_profile_log.jsonl`、`runtime/mas_ondemand_log.jsonl`、`runtime/mas_plan_builder_log.jsonl`、`runtime/mas_run_profile_state.json`? —— 除最后一项外均为各工具的**审计追加行**（dry-run/被拒也记），属设计行为。
- `runtime/probe_farm/pf_*/`：probe_farm dry-run 的证据目录（profiles + plan.txt）。
- `docs/audits/2026-09-28_tooling_audit/`：本报告与探针证据（新建）。
- 未发生：上游写请求（无 `--apply`）、配置改动、队列增删、任务启停、git 提交。
- 环境噪声：probe_farm dry-run 会在运行时执行 `stage_capability --update`（子进程）——若执行了，会正常改写能力台账（本次输出未见其执行痕迹；如需绝对零写，加 `--no-ledger-update`）。

## 附录 C：本次未覆盖（留给后续）

- MCP stdio 真实客户端握手与 6 个 MCP 工具的逐条调用（本轮只做到 `--check` + 内嵌 dry-run + 18 例 selftest）。
- 与 `mas_run_profile --apply` 的真实发车路径（属写侧；另有 09-28 08:42 的实机记录可参考 `docs/audits/2026-09-28_mas_run_profile_live.*`）。
- `mas_preflight.py`（36 KB）、`mas_dispatch.py`（88 KB）等本次未列名的工具——它们与这六个同属「每天用」层，建议下一轮扩审。
- `mas_farm_orchestrator.py`（66 KB）与实例租约（P1-2 补记）——下一轮建议以「六工具接入租约后的跨族互斥」为验收对象（双进程抢同实例实测）。
