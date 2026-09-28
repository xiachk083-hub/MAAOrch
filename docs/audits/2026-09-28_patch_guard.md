# 补丁持久化治理：mas_patch_guard + 巡检接线 + 沙箱自测（升级覆盖补丁的第一发现线）

日期：2026-09-28（上午；实施 09:2x–10:3x）
看板：mas profile 看板 / 任务 `t_cf3946dc`（补丁持久化治理）
目标：AUTO-MAS 升级/重装会**静默覆盖**我们打在源码上的补丁（AutoProxy 切号 v2 + 理智药、constants 理智药、mumu 判活关闭）——
把「补丁还在不在」变成一条可巡检的判据：清单化 + 只读闸门 + 受控重放 + 接进 30 分钟巡检。

## 1. 结论

- 新增 `tools/mas_patch_guard.py`（634 行，32,266B，只读为主）：`--check` 逐条校验 4 个补丁
  （文件存在 + 特征断言 + 哈希）⇒ 末行 `PATCH_GUARD=OK|MISSING:<n>`；**fail-closed**（读不到 = UNKNOWN，
  绝不当作 OK）；退出码 0=OK / 1=MISSING / 2=UNKNOWN；另有 WARN=哈希漂移、STALE=补丁在盘但侧车没重载（不改退出码）。
- 清单 `runtime/patches/manifest.json`（schema `mas-patch-manifest/1`；4 条：`accswitch_v2` / `medicine_autoproxy` /
  `medicine_constants` / `mumu_close_fix`）：每条含 file | checks（contains/not_contains/regex/count 断言）|
  purpose | backups（sha256）| rollback | reapply（payload）| patched_sha256。
  整文件快照：`runtime/patches/payloads/{AutoProxy,constants,mumu}.py.patched`（哈希与清单逐一复核一致）。
- `--reapply <name>`：默认 **dry-run**（打印当前文件断言结果 + diff 摘要 + 执行计划 + 「改完必须重启侧车/变更窗口」
  提醒，零写入）；`--apply` 真重放 = 远端备份 `.bak-patchguard-<ts>`（SHA 一致才继续）→ base64 走 stdin 上传
  （.py 先 py_compile）→ 落位校验 → 回读哈希+断言 ⇒ `REAPPLY=OK`；硬闸 `MAAORCH_PATCH_NO_GO=1`；
  审计台账 `runtime/mas_patch_guard_log.jsonl`。
- 实机现况 **`PATCH_GUARD=OK`**：4/4 在盘、断言全过、0 丢失 0 未知 0 漂移、侧车加载=OK（10:06:48，见 §5）。
- 沙箱自测全 PASS（18 项）：复制件故意改坏 1 字节 ⇒ `PATCH_GUARD=MISSING:2`（同文件另一补丁同时报 hash-drift、
  不误杀）；dry-run 零写入；`--apply` 重放后回读哈希 == 补丁版；生产文件全程只读（前后哈希未变）。
- 巡检接线（工具面 + 提示词）：`tools/mas_dispatch_mcp.py` 新增只读工具 `patch_guard()`
  （白名单 5/5，`--check` OK；复用 run_guard、返回裁剪摘要省预算）；job `52b88b0ffca0` prompt 增
  「现场六检」第 2 项 + 主表 `PATCH_GUARD=…` 行 + 预算/设计面同步（4405→4707 字符，回读逐字节一致）。
  MISSING 口径 = **只报告、指名叫人**（重放命令写进证据行），巡检不自动重放。
- 实跑验证（时序见 §5）：刷新前一轮 `PATCH_GUARD=UNKNOWN:工具面未提供（patch_guard 未接线）`
  （fail-closed，且证据行点名「建议人工确认 t_cf3946dc 落地状态」）→ 关→开刷新 MCP 面 →
  刷新后一轮 `PATCH_GUARD=OK`。
- 附注 1：本 prompt 在 10:2x 被并行任务（t_634e7685 打断源治理）在其上**继续合并**（现场七检 +
  `INTERRUPT_RISK=` 行 + 预算 20）；本条补丁的 item 2 与 `PATCH_GUARD=` 行原样保留，合并后状态已复核。
- 附注 2：`config.yaml` 两处旧注释（「（只有）3 个工具」）未改 —— agent 侧对 config.yaml 有写入保护
  （`Refusing to write to Hermes config file`）；纯文案、不影响行为，要改请用户 `hermes config edit`。

## 2. 改动文件与备份（受控写入）

| 文件 | 改动 | 改动前备份/基线 |
| --- | --- | --- |
| `tools/mas_patch_guard.py` | 新增（sha256 `ce1946942f555500d27e40eaf55b475cbfdafec05ab3f491c79f59b458084e1a`） | —（新文件） |
| `tools/mas_patch_guard_selftest.py` | 新增（sha256 `f1ca7e95b9ee95ada1eee312388026e97f70a6434b628cece549e57ae7f838b9`） | —（新文件） |
| `runtime/patches/manifest.json` | 新增（sha256 `f9b102ca3ad10c0a4ed8f39732a74ced972d718e8ff7a7a6d7411364c27cab7b`） | — |
| `runtime/patches/payloads/*.patched` | 新增 3 份整文件快照（哈希见 §1；与清单一致） | — |
| `tools/mas_dispatch_mcp.py` | 补：docstring 工具表 + `_SCHEMAS` + `_call` 分派 + `_patch_guard()` + `--check` 白名单（sha256 `2652591048767f568735b40c9f5b26dfd129d5487546ee707146a8d111c39d6c`） | `tools/mas_dispatch_mcp.py.bak-patchguard-20260928-094943`（12,188B） |
| cron `jobs.json`（mas profile） | job `52b88b0ffca0` prompt：现场五检→六检 + item 2；主表 11→12 行；预算 18→19；设计面 mas-dispatch 4→5（4405→4707 字符，回读逐字节一致） | `…/profiles/mas/cron/jobs.json.bak-patchguard-20260928-095754` |
| `…/profiles/mas/config.yaml` | 刷新 mas-dispatch MCP 面：`mcp_servers.mas-dispatch.enabled` false→true（终态与备份逐字节一致） | `config.yaml.bak-patchguard-20260928-100049` |
| `docs/audits/2026-09-28_*` | 证据 8 份（见 §5） | — |

## 3. 回滚

```bash
# 工具（新增文件，直接删；无系统依赖）
rm tools/mas_patch_guard.py tools/mas_patch_guard_selftest.py
rm -r runtime/patches/                     # 清单+payload+日志（删前可选留档）
# MCP 面
cp -p tools/mas_dispatch_mcp.py.bak-patchguard-20260928-094943 tools/mas_dispatch_mcp.py
#   ⇒ 再「关→开」mcp_servers.mas-dispatch.enabled 刷新（不刷新则网关仍拿旧工具面）
# 巡检提示词（只回滚补丁段；⚠ 别整文件回拷 —— 会连带撤掉并行任务的合并）
#   本条的补丁段 = 第三步 item 2（patch_guard）+ 主表 PATCH_GUARD 行；手工删这两处即可。
#   （整文件回拷用的备份：…/profiles/mas/cron/jobs.json.bak-patchguard-20260928-095754）
```

（以上均不动主机 100.79.173.69 上的补丁文件与 `.bak-*` 备份。）

## 4. 判据与边界

- fail-closed：读不到文件 = UNKNOWN，绝不当作 OK；真重放前断言失败明细会点名（哪个 contains/regex/count 不过）。
- 边界：模板面（MAA 目录 `gui.new.json` 注入值）不入此清单（轮末会被 Temp 快照还原覆盖）⇒ 走 `mas_switch_gate`；
  config 面（`ScriptConfig.json`/`QueueConfig.json`）不是补丁（升级不覆盖）。
- 升级后重放的风险：整文件覆盖会丢上游改动 ⇒ `--apply` 前必须看 dry-run diff、人工拍板；
  `mumu.py`（改动最大，+8.8KB）优先考虑「把新方法移植到新版文件」而非整文件覆盖。
- 生效条件：重放后**必须重启侧车**（Python 模块启动时导入、不热更）；班次窗口 05:00–08:00 / 19:30–23:00
  内禁止重启侧车（会吞在飞任务 ⇒ 孤儿化）。

## 5. 证据（实跑）

- 实机 check（10:06:48）：`补丁闸门: 4 条, OK 4, 丢失 0, 未知 0, 漂移 0, 侧车加载=OK` / `PATCH_GUARD=OK` / rc=0
  → `docs/audits/2026-09-28_patch_guard_check.txt`
- 本地规则自检：13/13 PASS（12 条断言用例 + manifest schema/4 补丁/payload 齐全）
  → `docs/audits/2026-09-28_patch_guard_selftest_local.txt`
- 沙箱自测（主机沙箱内改坏副本；生产只读）：18/18 PASS → `docs/audits/2026-09-28_patch_guard_selftest.txt`
- MCP：`mas_dispatch_mcp --check: OK`，`tools=list_schedule,dispatch_due,dispatch_queue,switch_gate,patch_guard`
  → `docs/audits/2026-09-28_patch_guard_mcp_check.txt`
- 巡检 prompt 前后对照：`docs/audits/2026-09-28_inspect_prompt.before.txt` / `.after.txt`（4405→4707 字符）
- 巡检实跑时序：
  1. 10:00:52–10:16:49（刷新前）：报告第 4 行 `PATCH_GUARD=UNKNOWN:工具面未提供（patch_guard 未接线）`；
     证据行「…PATCH_GUARD 未接线无法核查，建议人工确认 t_cf3946dc 落地状态」= 新格式与点名机制落地 ✓
     （`cron/output/52b88b0ffca0/2026-09-28_10-16-49.md`；session `cron_52b88b0ffca0_20260928_100053`）
  2. 10:18:07 `enabled=false` → 10:19:50 `enabled=true`（刷新；config.yaml 终态与备份 diff 为空）
  3. 10:57:52–11:10:18 一轮 builtin（execution `47bb2651235949a7baf52d765aaadba9`）跑通闭环：
     工具面已含 `mas-dispatch 5`（现场共 38 个 MCP 工具；`hermes mcp test mas-dispatch` 亦验证
     `✓ Tools discovered: 5` 含 patch_guard）；实调 `patch_guard()` →
     `{"patch_guard":"OK","ok_count":4,"missing":[],"unknown":[],"warn":[],"loaded":"OK"}`（11:02:18，4/4 在盘）
     ⇒ 报告主表第 4 行 **`PATCH_GUARD=OK`** ✓（11 点轮报告副本：`2026-09-28_patch_guard_inspect_report.md`；
     session `cron_52b88b0ffca0_20260928_105752`）
- 注：10:21:21 的一轮 direct（本次会话发起的 `cron run`）因发起 CLI 被超时中止而 orphan；10:27:18
  调度器自动重试的一轮也随 sweep 记 orphan —— 两轮均无副作用、无报告（executions 记
  `Scheduler restarted after this execution's owner exited before a durable terminal state…`；
  `hermes cron doctor` 无 issue）。（教训：`hermes cron run` 是**阻塞**的、claim 挂在调用方进程上，
  别用短超时的前台命令发起；端到端取证等 builtin 定时轮。）

## 6. 维护

- 改补丁 ⇒ 改完 `python tools/mas_patch_guard.py --capture --apply` 重录基线（payload + 哈希），
  并把新备份名/回滚命令写进 manifest；再跑 `--selftest`（本地）与 `tools/mas_patch_guard_selftest.py`（沙箱）。
- 新补丁入册：manifest `patches` 追加一条（file/checks/backups/rollback/reapply/payload）。
- 巡检读取：MCP `patch_guard()` 一轮 ≤1 次；MISSING 只报告不处置。
- 工具面刷新口诀：改 `mas_dispatch_mcp.py` 后「关→开」`mcp_servers.mas-dispatch.enabled`（~1 分钟生效），
  否则巡检拿到旧工具面（本轮实测：刷新前 `patch_guard 未接线`）。
