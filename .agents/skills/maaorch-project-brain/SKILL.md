---
name: maaorch-project-brain
description: "Use when 在 MAAOrch 仓库接手任务（含看板卡）— 先读项目大脑做定位/路由与状态判定，再按专业技能执行。"
version: 1.0.0
author: mas
license: MIT
metadata:
  hermes:
    tags: [maaorch, project-brain, kanban, routing, mas, auto-mas]
    related_skills: [mas-ops-toolbox, auto-mas-host-ops, maaorch-queue-triage, maa-task-config, maa-roguelike-ops, maa-event-farming, maa-cli-ops, wiki-search]
---

# MAAOrch 项目大脑（会话开工技能）

> 本技能是仓库入口的**压缩版**；全文与证据标签 = `docs/HERMES_PROJECT_BRAIN.md`（先读它）。
> 本技能只做四件事：**开工硬序 / 文档路由 / 看板规则 / 验证与学习纪律**。

## When to Use

- 在本仓库（`D:\1-2\MAAOrch`）接手任何任务：新会话第一件事、看板卡开工、用户口述调配（补跑/改刷/发车/肉鸽/活动/停队列/腾实例）。
- 需要判断「某事现在能不能做、用哪个工具、证据够不够」时。

## 会话开工硬序（5 步）

1. **读大脑**：`docs/HERMES_PROJECT_BRAIN.md`（定位/路由/能力状态）；要动哪块再按 §2 路由表读对应专项文档全文。
2. **查实时，不背旧结论**：`git log --oneline -10`（有无推翻旧结论的提交）；按大脑 §4 表「复验方式」一栏复验；端口/实例/在跑 = 现查（`tools/mas_mapping.py` / `mas_ondemand.py --list-running`）。
3. **判断副作用**：只读？还是要发车/写配置/碰实例？有副作用 ⇒ 批准链 + 闸门 + 收尾核验（同目录一任务；守卫窗不发车）。
4. **查重**：工具箱 `docs/TOOLBOX.md` §B（哪件事用哪个工具）与 §E（写新工具前查重）；跨会话先查知识库（技能 `wiki-search`，库在 `D:\1-2\MAS-KB`）。
5. **收尾**：产物落 durable 路径（`docs/`、`runtime/reports/`）；引用机器证据与末行判据；结论回写大脑文档（见「自持学习」）。

## 文档路由（按主题一跳）

| 要什么 | 去哪 |
|---|---|
| 哪件事用哪个工具（含最少命令/禁忌） | `docs/TOOLBOX.md` |
| 业务全景「谁是谁」/节律/判据速查 | `docs/MAS_MASTER_HANDBOOK.md` |
| 出事 / 急停 / 回滚 | `docs/MAS_EMERGENCY_RUNBOOK.md` |
| 建卡 / 派发 / 并发 / 失败处理 | `docs/KANBAN_TASK_TEMPLATE.md` + `docs/KANBAN_DISPATCH_POLICY.md`（体检 `docs/KANBAN_HEALTH.md`） |
| maa-cli（第二驱动，受限） | `docs/MAA_CLI_DEPLOY.md` + 技能 `maa-cli-ops`（使用前重查版本/路径） |
| 场景发车（肉鸽/活动/剿灭；草案双闸） | `docs/MAS_SCENARIO_DISPATCH.md`；肉鸽 `docs/MAA_ROGUELIKE_PLAYBOOK.md`、活动 `docs/MAA_EVENT_FARMING_PLAYBOOK.md` |
| 补跑规则 / 失败定因 / 日志判据 | `docs/MAS_RERUN_POLICY.md`；`docs/MAA_LOG_DICTIONARY.md` |
| 审计与证据口径 | `docs/MAS_AUDIT_SCHEMA.md`；过程证据 `docs/audits/` |
| 端口 / 实例 / 账号清单 | `docs/MAS_PORT_RULES.md` + `docs/MAS_EMULATORS.md`；工具 `mas_mapping.py`（现查，别背公式） |

## 状态判定纪律（引用任何能力前必做）

- 六标签：`IMPLEMENTED / TESTED / DEPLOYED / PROPOSED / UNVERIFIED / DEPRECATED`（定义见大脑 §4.1）。
- 标 `DEPLOYED` 需三证：**实跑证据 + 接线位置 + 回滚方式**；`TESTED` 必须标环境/版本/日期。
- **设计 ≠ 可用**：自测通过 / 干跑 GO / 演练 PASS 都不等于生产可用——引用前看大脑 §4.4 的已确认反例（watchdog 未注册、scenario apply 未跑、activity_switch 无账本、maa-cli 受限、skills-draft 不生效）。
- fail-closed：`UNKNOWN:<原因>` 不是「正常」；判不了就停手报告；不猜、不自动重试、不连环试探。
- 数字（工具数/队列数/版本）一律现查；旧快照（含卡面/文档里的计数）一律不信。

## Kanban 规则（板 `maa-parallel-benchmark-20260926`）

- 建卡七段式；必带 `assignee / priority / parents(仅产物依赖) / idempotency_key(周期卡) / 产出绝对路径 / 验收 / 红线 / 预算`；建卡前查重；**未知 assignee 的卡会永躺 ready**。
- 并发：稳态 4–6 张/波（宿主上限 8、同 profile 6）；碰设备/侧车/发车/写配置 = **全局串行**且避开班次窗（05:00–05:40 / 19:30–20:30）；只读卡可并行但互不写同一文件。
- 重派（禁止裸 promote）：读 `last_failure_error` + 评论 → 修卡/修环境 → **comment 写重试备注** → `promote`。
- 完结：`kanban_complete(artifacts=[绝对路径])`——产物必须已落 durable 盘；scratch 完结即删会被判孤儿。
- 有副作用/高风险卡：`max_retries=1` + `max_runtime` 兜底；worker 上下文跑看板 CLI 需 `env -u HERMES_DELEGATED_CHILD_CONTEXT` 前缀。

## 自持学习（新知识怎么沉淀）

- 过程证据 → `docs/audits/<date>_<slug>.*`；结论/能力状态 → 回写 `docs/HERMES_PROJECT_BRAIN.md` §4 表对应行（改标签必须附新证据：日期+机器+命令/执行记录）。
- 发生状态迁移（PROPOSED→DEPLOYED 等）→ 在大脑 §4.3 追加一行。
- 新工具先查重再动手（TOOLBOX §E：优先扩展现有工具）；落成后更新 `docs/TOOLBOX.md` + 工具文档 + 大脑表。
- 红线：凭据/账号隐私不入文档与技能；不覆盖他人改动；不提交 commit（除非卡面要求）。

---

*仓库入口全文：`docs/HERMES_PROJECT_BRAIN.md`（2026-09-28 建立，任务 `t_1106669c`；本技能随仓库加载，需 mas profile 已 `hermes skills trust D:\1-2\MAAOrch`）。*
