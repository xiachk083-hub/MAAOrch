# skills-draft —— Hermes 技能草稿（未安装）

本目录存放「调配手册」任务（`t_4d8f3ee3`）产出的 **Hermes 技能草稿**。
**草稿不生效**：它们不在 mas profile 的 skills 目录里，任何会话都不会加载——评审通过后再手动安装。

## 内容

| 草稿 | 作用 | 对应全文 |
|---|---|---|
| `mas-playbooks/SKILL.md` | 场景路由技能：用户一句话（补跑/改刷/发肉鸽/插活动/停队列/腾实例）→ 命中剧本 → 命令/验证/回滚/红线（压缩卡） | `docs/HERMES_MAS_PLAYBOOKS.md`（全文，本仓库） |

## 与 t_0f598a66 的衔接

t_0f598a66 把 09-26/27 的踩坑沉淀进了已安装技能 `auto-mas-host-ops`（v1.3.0）及姊妹技能：

- 已安装（mas profile，`skills/autonomous-ai-agents/`）：`auto-mas-host-ops`、`maa-roguelike-ops`、`maa-event-farming`、`maa-task-config`、`maaorch-queue-triage`。
- 本目录的 `mas-playbooks` 是**第六块拼图：场景路由器**——只做「一句话 → 剧本」的路由与红线复述，执行细节一律指向上述技能 + `docs/HERMES_MAS_PLAYBOOKS.md`，不重复、不分叉口径。

## 安装（评审通过后）

```powershell
# mas profile 的定制技能目录（与姊妹技能同目录）
$dst = "$env:LOCALAPPDATA\hermes\profiles\mas\skills\autonomous-ai-agents\mas-playbooks"
Copy-Item -Recurse -Force "D:\1-2\MAAOrch\docs\skills-draft\mas-playbooks" $dst
# 生效方式：新开/重载会话（技能列表按会话加载）；用 skill_view(name='mas-playbooks') 自检
```

安装前建议评审：

1. 读 `mas-playbooks/SKILL.md`，对照 `docs/HERMES_MAS_PLAYBOOKS.md` 抽查 3 条命令字面量（尤其 dry-run 路径与工具名）。
2. 盲测：只看技能、不看全文，回答「用户说『把官-5 补一下』你第一步做什么」——应能给出：守卫窗/三问 C3/`triage_error` → `mas_rerun(dry_run=True)`。
3. 若希望并入 `auto-mas-host-ops` 作为 references 文件而不是新技能，也在评审时定夺（二选一，别两边都放）。

## 变更记录

- 2026-09-28（t_4d8f3ee3）：首版草稿；全文手册同步落盘 `docs/HERMES_MAS_PLAYBOOKS.md`。
