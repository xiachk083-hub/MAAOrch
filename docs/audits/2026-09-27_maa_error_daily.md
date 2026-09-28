# MAA 每日错误报告聚合（四源 → 一页日报 + 巡检一行摘要）

日期：2026-09-27（晚间，21:2x–22:1x 落盘）
看板：`maa-parallel-benchmark-20260926` / 任务 `t_d054ba36`
目标：把散在四处的 `TaskChainError` / `任务出错` / `可用干员不足` 等错误聚合成**每日一页**，
并给巡检（cron job `52b88b0ffca0`）一个**一行摘要**（`ERR_SUMMARY=<n> 条，Top=<类型>`）。

## 1. 结论

- **CLI**：`tools/maa_error_daily.py`（只读）—— 四源聚合：
  ① `E:\AUTO-MAS\history\<date>\<账号>\*.json` 的 `maa_result`（账号级权威）+ 同名 `.log`（链级证据）；
  ② `E:\MAA-Multi\MAA_*\debug\gui.log`；③ `asst.log`（TaskChainError/SubTaskError）；④ `E:\AUTO-MAS\debug\app.log`；
  外加只读 REST（脚本→目录、uuid→账号）。窗口 = 班次日 `[D 05:00, D+1 05:00)`。
- **产出**：`runtime/reports/maa_errors_<date>.md`（人读一页）+ `.json`（机读）；stdout 末行
  `ERR_SUMMARY=<n> 条，Top=<类型>`。三档口径：**致命 / 告警 / 可忽略**（实测校准：`可用干员不足`
  在基建换班继续跑完 ⇒ 告警；公招资源类 ⇒ 可忽略）。
- **MCP**：`mas-triage` server 新增第 2 个只读工具 `error_daily(date?, max_age_s=600, timeout=420, write=true)`
  —— 返回 `ERR_SUMMARY` 一行 + 致命/告警/可忽略与分类计数 + 报告路径；`max_age_s` 内已有当日报告直接复用
  （默认 600s；巡检 30 分钟节拍下每轮重扫，密集重调零成本）。数据源不可达 ⇒ `ok=false` +
  `ERR_SUMMARY=UNKNOWN:<原因>`（fail-closed，不抛异常）。
- **巡检接线**：job `52b88b0ffca0` 提示词补丁 —— 第三步扩为 5 次（+`error_daily()`），
  汇报增行 `ERR_SUMMARY=<n> 条，Top=<类型>`；预算 15→16 次调用、汇报 13→14 行。
  `err_summary` 行与 threshold 口径（登录类 ≥20 升级告警）随报告一起复用于巡检。

## 2. 改动文件与备份（受控写入）

| 文件 | 改动 | 备份（可整体回滚） |
|---|---|---|
| `tools/maa_error_daily.py` | 新建（只读聚合器，1118 行；`--summary/--json/--no-write/--date/--check/--selftest`） | —（新增文件） |
| `tools/mas_triage_mcp.py` | +`error_daily` 工具（schema/实现/`--check` 扩展） | `tools/mas_triage_mcp.py.bak-errdaily-20260927-215731`（sha256 `55fc0f5b…`） |
| cron `jobs.json`（mas profile） | job `52b88b0ffca0` prompt：+error_daily/ERR_SUMMARY（2671→3033 字符；后由 t_562840d3 全面重构**整合保留**，现 4013 字符 sha `3324071b…`） | `…/profiles/mas/cron/jobs.json.bak-errdaily-20260927-220356` |
| `tmp/hermes_bridge/apply_errdaily.py` + `errdaily_prompt.md` | 补丁脚本与草稿（幂等 + 回读校验） | —（工具件） |

补丁后回读：prompt 2671→3033 字符与目标文本**逐字节一致**（sha256 `3e96a49b…`），
非 prompt 字段（除 tick 状态）零变化。

## 3. 回滚

```bash
cp -p tools/mas_triage_mcp.py.bak-errdaily-20260927-215731 tools/mas_triage_mcp.py
cp -p "C:/Users/xiach/AppData/Local/hermes/profiles/mas/cron/jobs.json.bak-errdaily-20260927-220356" \
      "C:/Users/xiach/AppData/Local/hermes/profiles/mas/cron/jobs.json"
# 巡检提示词也可只回退该 job 的 prompt：
# hermes -p mas cron edit 52b88b0ffca0 --prompt "<旧 prompt，见备份>"
# maa_error_daily.py 为新增文件，回滚=删除（无副作用；报告文件保留不影响）
```

## 4. 验证证据（全部实测）

1. `python tools/maa_error_daily.py --selftest` → `OK`（分类器/窗口正则离线用例全绿）。
2. 全量实跑 `--date 2026-09-27` → 29.5s：`history 21 账号 / 65 尝试（失败 41）｜槽位日志 12 目录｜app.log 55 轮`；
   `ERR_SUMMARY=100 条，Top=客户端未就绪`；md/json 双落盘。
3. `tools/mas_triage_mcp.py --check` → `OK | tools=triage_error,error_daily | REST scripts=54 | error_daily=selftest=OK`。
4. `hermes -p mas mcp test mas-triage` → `✓ Connected (3831ms) ✓ Tools discovered: 2`（triage_error / error_daily）。
5. MCP 工具四路径实探：缓存路径 0.0s 复用（`cached:true, age_s:268`）；强制重扫 31.9s（`cached:false`，报告
   `generated_at` 刷新）；坏日期 → 结构化 `UNKNOWN:bad date`；缺省日期 → 缓存命中。
6. 巡检提示词：`apply_errdaily.py` 幂等（已含 `error_daily(` 时安全跳过）、5 处锚点各命中一次、
   回读逐字节一致、字段级校验 OK。
7. 下一轮巡检起连续 3 轮实测（22:13 / 22:36 / 23:03 完成）见 §8 —— tool face 刷新时效的验收点：
   builtin 巡检面缓存未见 `error_daily` 时按设计写 `UNAVAILABLE`（fail-closed 生效，实测两轮）。

## 5. 运维要点

- 人工复跑：`python tools/maa_error_daily.py [--date D]`；只看摘要 `--summary`；机读 `--json`。
- 报告键：`counts.by_cat/by_sev/top/login_n/escalated`、`hist.rows`（逐尝试）、`subtask`（asst 链内细因）、
  `dedupe`（跨源去重）。跨源重复计数是**口径**（history=账号级、gui/asst=槽位级、app=用户级）。
- 登录类升级阈值 `LOGIN_ESCALATE=20`（巡检既有口径，进 `counts.escalated`）。
- MCP 侧改的是 **server 源码**（非 config）：若巡检下一轮报 `ERR_SUMMARY=UNAVAILABLE:工具未加载`，
  说明网关工具面缓存未刷新 ⇒ `hermes gateway restart`（依据 `docs/HERMES_MAS_HANDOVER.md` §12.1）。
- 未动 `config.yaml`（mas-triage 无 `tools.include` 过滤，新工具自动随 server 暴露；其 cron toolset
  注释仍写「只读 1 个工具」，属纯文档性滞后，留待统一更新）。

## 6. 与 `docs/MCP_TOOL_ROADMAP.md` §2.2 的差异说明

- 本卡交付的是**聚合后端 + 一行摘要**，MCP 面落在 `mas-triage.error_daily`（窄面、只读），
  未按 §2.2 建 `mas-observe/mas_report` 全量版（`items[]`/`shift{}` 未做）。
- 班次/发车摘要当前由 `mas-dispatch.list_schedule`（含 `dispatch_alerts`）承担，与本工具**不重叠**；
  未来若按 §2.2 建 `mas_report`，`error_daily` 可作为其 `summary_only` 语义的既成参考实现。

## 7. 工具面刷新机制（实测，重要）

MCP server 的**源码**改动**不需要** `hermes gateway restart`——工具面按**会话启动时**取：

- 21:05 巡检轮（会话起于 20:46:54）：`MCP_API_TOOL_COUNT=20`，mas-dispatch 3 工具（switch_gate
  代码 21:23 才合并，符合）；
- 21:35 巡检轮（会话起于 21:35:54）：mas-dispatch **4** 工具，`switch_gate` 调用成功
  （21:52:36 完成，2066 字符）⇒ 新会话拿到的就是磁盘上的最新代码；
- 22:14:38 `mcp-stderr.log` 实测：mas-triage server 被新会话**重新拉起**（各 server 按会话
  spawn/list，非网关级长驻）。

⇒ 22:00 后改的 `error_daily` 会在**下一轮巡检会话**（预计 22:44 起）进入工具面，无需重启网关。
（§12.1 的「必须重启」仅适用于 `config.yaml` 的 platform_toolsets/MCP server 清单变更。）

## 8. 端到端实测（22:03 补丁后 3 轮巡检）——修正 §7 的乐观结论

两套解析路径**不同**，必须分开记：

1. **worker/CLI 进程（新进程）**：立即见 2 工具并可用。
   - `…22:14:40 tools.mcp_tool: MCP server 'mas-triage' registered 2 tool(s): triage_error, error_daily`（多次复现）；
   - 22:36:32 另一卡（t_ae836086）的 worker 直接调用 `mcp__mas_triage__error_daily completed (33.62s, 870 chars)`
     ✅，并当场把报告刷新到 `generated_at=22:36:32`（`ERR_SUMMARY=102 条，Top=未知`）。
2. **builtin 巡检轮（网关进程内会话）**：工具面**缓存**，未见 error_daily——两轮各自枚举：
   - 22:36:35 输出：`工具面=21`、`ERR_SUMMARY=UNAVAILABLE:工具未加载`；
   - 23:03:05 输出：`…triage1〔triage_error〕；缺：error_daily（未加载）…`、`UNAVAILABLE` 同前。
   fail-closed 兜底（prompt 内的 UNAVAILABLE 分支）**按设计生效** ✅（没有误报、没有猜）。

**结论/待办（operator 动作）**：源码级新工具进 **builtin** 巡检面需要一次网关级刷新：
`hermes gateway restart`（§12.1 口径）——或 mas 的 `mcp_servers.mas-triage` 发生一次 config
reconcile（参考 21:41 对 mas-dispatch 的 removed→added 事件，刷新后开关即进巡检面）。
本卡**未自行重启网关**（网关 29400 同时承载看板调度器与多个在跑 worker，重启会打断它们）。
在刷新之前，巡检输出固定写 `ERR_SUMMARY=UNAVAILABLE:工具未加载`（一行、可识别、不误导）。

## 9. 提示词衔接（与 t_562840d3 的无缝交接）

- 22:03:56 本卡补丁（3033 字符 sha `3e96a49b…`，备份见 §2）→ 22:12 起 t_562840d3 以
  **含本补丁的最新 jobs.json** 为底稿做全面重构（其备份 `jobs.json.bak-inspect-rebuild-20260927-221207`、
  `…-final-20260927-224152`）。
- 现 prompt 逐项复检仍含：`error_daily()`、`ERR_SUMMARY=<n> 条 Top=<类型>`、`UNAVAILABLE:工具未加载` 兜底
  ——两卡衔接**零回滚**（本卡先留接口交接评论，见 t_562840d3 评论区）。
  版本快照：22:51 复核 4013 字符 sha `3324071b…`；23:16 复检 4405 字符 sha `15aa315b…`
  （t_562840d3 收尾小改；`error_daily(` / `ERR_SUMMARY=` / `UNAVAILABLE` 标记均在）。
