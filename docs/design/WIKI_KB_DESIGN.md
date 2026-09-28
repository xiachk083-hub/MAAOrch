# 知识库设计定稿：Hermes + Obsidian + LLM wiki（MAAOrch 人读导航层 v1）

> 2026-09-27 ｜ 出题 dsh-lead（看板 t_9b7ea8ac，板 maa-parallel-benchmark-20260926）｜ 执行 mas
> **状态：设计定稿（可直接施工）。本文只出文档：未建 vault、未改 Obsidian 配置、未动 `docs/` 以外任何文件。**
> 目标：Hermes「先查知识库再动手」；人读侧在 Obsidian 里 30 秒看懂「这是什么 / 怎么发生的 / 能不能动」。

## 0. 五条决策（TL;DR）

| # | 决策 | 一句话理由 |
|---|------|-----------|
| 1 | **vault 根 = `D:\1-2\MAAOrch-KB`（独立目录，不在公开仓内）** | 远端 `xiachk083-hub/MAAOrch` 是 **public**（`.gitignore:131` 原话），KB 是聚合层、且**可再生** ⇒ 泄密不可逆、丢失可重建 |
| 2 | `_sources/` 用 **junction（/J）** 把 audits/reports/skills/memory 链进来 | 免管理员、Obsidian 能顺着读；**junction 不是权限**，配 RO_DRIFT 清单兜底（§2） |
| 3 | 只引用不复制：**>30 行原文一律只留 `path:lines` 锚点** | 防双份真值漂移；KB 只承载「结论一句话 + 锚点 + 关系」 |
| 4 | 生成链 `tools/wiki_sync.py` **默认 dry-run**、按 `id` 合并、只写 `wiki/` | 幂等 + 并发安全 + 出错不伤源（house 惯例） |
| 5 | 治理接巡检：单行 `WIKI_GOV=…` 进 job `52b88b0ffca0` 第六步报表 | 已验证该 job 每 30m 跑、只有 MCP 工具面、预算 18 次 |

## 1. 分层：机器事实层 ↔ 人读导航层

```
  机器事实层（真值·只读引用 path:lines/sha256）        人读导航层  D:\1-2\MAAOrch-KB
  docs/audits 73 · docs 92 · reports 857            wiki/{entities,incidents,playbooks,
  kanban.db 92卡/47附件/77评 · skills · MEMORY.md      runbooks,changes,tasks}
         │（人写：手记区）        ▲                              │ wiki_search（只读）
         └──────────────────────────┘                             ▼ Hermes 会话 / 巡检
```

| 层 | 内容 | 谁写 | 进 KB 方式 |
|---|---|---|---|
| 机器事实层 | `D:\1-2\MAAOrch\{docs,docs/audits,runtime/reports}`、看板 `kanban.db`、`profiles/mas/skills/**/SKILL.md`、`MEMORY.md` | 任务/工具 | **只引用** |
| 人读导航层 | `wiki/**.md`：实体 / 事故 / 手册 / 运行 / 变更 / 任务 | `wiki_sync.py`（机器块）+ 人工（手记区） | — |
| 检索面 | `wiki_search`（CLI + Hermes 技能 + 只读 MCP 工具） | — | 只读 |

**三条硬规则**：① 证据正文不复制，超 30 行只留锚点；② 冲突时机器层赢，KB 页标 `contradicts:` 进治理清单；③ 看板是「任务真值」、KB 是「知识真值」——KB 不替代看板（状态仍查 `kanban.db`）。

## 2. 落地位置与只读镜像

**选 `D:\1-2\MAAOrch-KB`（独立），否决 `D:\1-2\MAAOrch\kb`（仓内）**：仓内 vault 会被下一次 `git add -A` 带进公开仓（本仓已有先例：`78ad828` 误带 14 文件约 2 万行，`.gitignore:154`），而 KB 内容必然聚合「哪个号在哪台实例跑什么」；反向不对称——KB 丢了可 `wiki_sync --rebuild` 重建，泄了不可逆。代价：失去 GitHub 免费备份 ⇒ KB 自建 `git init`（§9-5）。

```bash
# vault 骨架（施工第 1 步执行；本机语法已实测，junction 免管理员）
mkdir "D:\1-2\MAAOrch-KB\wiki"; cd "D:\1-2\MAAOrch-KB"; git init
cmd.exe /c "mklink /J D:\1-2\MAAOrch-KB\_sources\audits  D:\1-2\MAAOrch\docs\audits"
cmd.exe /c "mklink /J D:\1-2\MAAOrch-KB\_sources\reports D:\1-2\MAAOrch\runtime\reports"
cmd.exe /c "mklink /J D:\1-2\MAAOrch-KB\_sources\skills  C:\Users\xiach\AppData\Local\hermes\profiles\mas\skills"
cmd.exe /c "mklink /J D:\1-2\MAAOrch-KB\_sources\memory  C:\Users\xiach\AppData\Local\hermes\profiles\mas\memories"
cmd.exe /c "dir /AL D:\1-2\MAAOrch-KB\_sources"   # 验收：4 行 <JUNCTION>
```

- **坑（实测）**：必须 `cmd.exe /c "mklink …"`。不要用 `cmd //c`——`which cmd` 命中的是 npm shim（`AppData\Roaming\npm\cmd`），`//c` 不被 MSYS 转换，命令不执行且报 "too many arguments"。
- **只读约束（老实说：junction 不是权限，reparse point 透明）**——四条对策：① `_sources/**` 永不入 KB 的 git（`.gitignore` 第一行）；② 页内 `source:` 一律写**仓内规范路径**（`docs/audits/…:12-40`），`_sources/…` 只作阅读便利链；③ `wiki_sync.py --ro-audit` 对 `_sources/**` 记 sha256 清单（`_meta/ro_manifest.json`），**已存在文件字节变化 ⇒ `WIKI_RO_DRIFT`**（`runtime/reports` 惯例只增不改，改即红旗；docs/audits 再与 `git -C D:\1-2\MAAOrch status --porcelain docs/` 交叉核对）；④ `SCHEMA.md`/`00-index.md` 顶部红字「只读镜像，禁止编辑」。
- Obsidian 索引减负：设置 → 文件与链接 → **排除文件**加 `_sources/reports/**`（857 文件含图片，只作锚点来源，不索引），保留 `_sources/audits/**`（73 份 md）可搜索。
- 环境变量（供技能寻址）：`HERMES_HOME\.env` 追加 `WIKI_PATH=D:\1-2\MAAOrch-KB` 与 `OBSIDIAN_VAULT_PATH=D:\1-2\MAAOrch-KB`（llm-wiki / obsidian 两技能均按此约定寻址）。

## 3. 目录树与命名规范

```
D:\1-2\MAAOrch-KB\              ← Obsidian 打开这一层（.obsidian\ 最小配置：核心功能零插件可用）
├─ .gitignore                   ← 必含：_sources/  +  .obsidian/workspace*.json
├─ SCHEMA.md                    ← 标签表 + 命名规范 + 建页阈值（本库「宪法」）
├─ wiki\
│  ├─ 00-index.md               ← 分区目录：每条一行 wikilink + 摘要
│  ├─ entities\{account,script,instance,queue}\
│  ├─ incidents\  playbooks\  runbooks\  changes\
│  ├─ tasks\                    ← 第 7 个顶层目录（看板卡 1:1 摘要页，见下）
│  └─ _meta\                    ← ro_manifest.json / sync_state.json / lint_<date>.md / sync.lock
└─ _sources\                    ← 只读镜像 junction（audits/reports/skills/memory）
```

| 对象 | 文件名（ASCII kebab-case） | `id` |
|---|---|---|
| 账号 | `entities/account/account-official-1-1-7.md` | `account:official-1-1-7` |
| 脚本 | `entities/script/script-official-1-1-7.md` | `script:official-1-1-7` |
| 实例 | `entities/instance/instance-mumu-13.md` | `instance:mumu-13` |
| 队列 | `entities/queue/queue-daily-1.md`（= 日常定时1） | `queue:daily-1` |
| 任务 | `tasks/task-t_9b7ea8ac.md` | `task:t_9b7ea8ac` |
| 事故 | `incidents/incident-2026-09-27-what-class-sanity.md` | `incident:2026-09-27-what-class-sanity` |
| 手册 | `playbooks/playbook-roguelike.md` | `playbook:roguelike` |
| 运行 | `runbooks/runbook-mas-emergency.md` | `runbook:mas-emergency` |
| 变更 | `changes/change-2026-09-27-switch-gate-wiring.md` | `change:2026-09-27-switch-gate-wiring` |

- 文件名只放 ASCII（跨 git/终端/手机稳定）；**中文名、旧名、缩写进 `aliases`**（「官-1-1-7」「官1抬7」都命中）。
- `id` 是唯一主键：**改名不改 id**，sync 按 id 合并（§6）。
- 为什么加 `tasks/`：看板 92 卡（done 57，22:38 快照）是全库最强的「谁做过什么」索引；`entities` 只管配置对象，卡不混进去。

## 4. Frontmatter schema（精确定义）

| 字段 | 必填 | 取值 / 格式 | 语义 |
|---|---|---|---|
| `id` | ✅ | `type:slug` | 稳定主键，sync 合并键，永不改 |
| `type` | ✅ | `entity\|incident\|playbook\|runbook\|change\|task` | 选模板 |
| `subtype` | entity 必填 | `account\|script\|instance\|queue` | 二级分类 |
| `title` | ✅ | 中文可读名 | 图/搜索展示 |
| `aliases` | | 数组 | 检索命中面（中文名/旧名/缩写） |
| `tags` | ✅ | **只能取 `SCHEMA.md` 标签表的值** | 分类 + Dataview 过滤（防标签蔓延） |
| `source` | ✅ | 数组 `{path, lines?, sha256?}`，≥1 条 | 机器证据锚；**无 source 的断言不许写进页** |
| `task_id` | | `t_xxxxxxxx` | 出处看板卡 |
| `owner` | ✅ | profile 名（`mas` / …） | 复核责任人 |
| `verified_at` | | `YYYY-MM-DD` | **空 = UNVERIFIED**（检索降权） |
| `confidence` | | `high\|medium\|low` | 低 = 降权 |
| `related` | | 数组（其它 id） | 关系图（≥2 条出链） |
| `created` / `updated` | ✅ | 日期 | `updated` 由 sync 维护 |
| `generated` | ✅ | `true\|false` | true = 机器块可改，人手记写进「手记」区 |
| `status` | | incident: `open\|mitigated\|closed`；其余省略 | 生命周期 |

```yaml
---
id: account:official-1-1-7
type: entity
subtype: account
title: 官-1-1-7（明日方舟-官-1-1-7）
aliases: ["官-1-1-7", "官1抬7"]
tags: [account, official, switch-capable, multi-user]
source:
  - path: docs/audits/2026-09-27_mas_account_switch_chain.md
    lines: "12-40"
  - {path: docs/MAS_SCHEDULE_MAP.md, lines: "32-45", sha256: "<易变源/多引用才记>"}
task_id: t_7e69b3d0
owner: mas
verified_at: 2026-09-27
confidence: high
related: [script:official-1-1-7, instance:mumu-13, queue:daily-7]
created: 2026-09-27
updated: 2026-09-27
generated: true
---
```

**Dataview 三例**（全库只此三处依赖插件；每例下方给无插件等价命令）：

```dataview
TABLE type, verified_at, owner FROM "wiki" WHERE !verified_at OR verified_at < date(today) - dur(30 days) SORT verified_at ASC
```
```dataview
LIST FROM "wiki/incidents" WHERE contains(related, "account:official-1-1-7") SORT file.name DESC
```
```dataview
TABLE updated AS 日期, source[0].path AS 证据 FROM "wiki/changes" WHERE contains(related, "instance:mumu-13") SORT updated DESC
```

无插件等价（grep，任何时候可跑）：未验证清单 `rg -l 'verified_at: *(2026-0[1-8]|2025)' D:/1-2/MAAOrch-KB/wiki`；某账号事故 `rg -l 'related:.*account:official-1-1-7' D:/1-2/MAAOrch-KB/wiki/incidents`；某槽位变更史 `rg -l 'related:.*instance:mumu-13' D:/1-2/MAAOrch-KB/wiki/changes`。

## 5. 四套模板骨架（`wiki/_meta/templates/` 存放原文）

**1) Entity**（account 示例；script/instance/queue 同骨架，机器块字段按注替换）：

```markdown
# 官-1-1-7
<!-- SYNC:BEGIN -->
- 服 / 槽位 / 实例 / 脚本：官服 · MAA_7 · idx13 · [[script-official-1-1-7]]
- 切号能力：支持（掩码 masked + AccountSwitchEnabled=true）〔source: docs/audits/2026-09-27_mas_account_switch_chain.md:12-40〕
- 事故 0 起 / 最近变更 无 / 队列归属：[[queue-daily-7]] + [[queue-night-7]]
<!-- SYNC:END -->
## 手记（人写，机器永不触碰）
```

**2) Incident**：`症状 / 首现与时长 / 影响面（账号·槽位·批次）/ 时间线（表格，证据列） / 根因 / 证据（source）/ 处置与残留 / 回归判据（可复跑的命令与期望）/ 未解问题`。

**3) Playbook**：`适用场景 / 前置检查（一票否决项）/ 步骤（编号、命令原文、等待、失败分支）/ 通过判据 / 红线（绝不做什么）/ 回滚（逐步）/ 相关页`。

**4) Change**：`动机 / 改动清单（file:line）/ 验证（命令 + 期望）/ 回滚（逐步，含 .bak 路径）/ 生效时间与观察窗 / 关联卡（task_id）`。

## 6. 生成链：`tools/wiki_sync.py`

| 项 | 设计 |
|---|---|
| 位置 | `D:\1-2\MAAOrch\tools\wiki_sync.py`（随 repo 版本化）；KB 根取 `MAAORCH_KB_PATH`，默认 `D:\1-2\MAAOrch-KB` |
| 输入（全只读） | `docs/audits/*.md`、`docs/*.md` 手册、`runtime/reports/*`、看板 `kanban.db`（`file:…?mode=ro`；或 `sqlite3.exe -readonly`，本机在 `C:\platform-tools-latest-windows\platform-tools\sqlite3.exe`）、`_sources/skills/**/SKILL.md`、`memories/MEMORY.md`、看板 `attachments/<task_id>/` |
| 输出（**只写**） | `D:\1-2\MAAOrch-KB\wiki\**` + `_meta\**`；**绝不写** `_sources/`、绝不写 repo 的 docs/runtime |
| 合并策略 | 页 = frontmatter + `<!-- SYNC:BEGIN/END -->` 机器块 + 人类区。按 `id`：缺页 ⇒ 新建；有页 ⇒ 只重写机器块；人类区永不触碰；来源消失 ⇒ 标 `status: orphaned`（**不删页**） |
| 幂等 | 源头 sha256 记 `_meta/sync_state.json`，无变化跳过；**连跑两次必须字节级一致**（验收判据） |
| 并发保护 | `_meta/sync.lock`（`O_EXCL` + pid + 时间戳；>10min 判 stale 可夺）；每文件 `tmp → os.replace` 原子替换；`--apply` 单写者 |
| 安全阀 | 默认 `--dry-run`（对齐 house 惯例，参照 `mas_dispatch` 预演）；`--apply` 才落盘；`--selftest` 离线断言 |
| 脱敏闸门（fail-closed） | 出页前扫：11 位手机号 ⇒ 掩码；命中 `api_key\|token\|password\|DPAPI\|webhook\|sk-\|ghp_` 的来源**整文件跳过**并记 `WIKI_SCRUB_SKIP=<file>`，不静默、不半截写入 |
| 建页阈值（防灌水） | 满足其一才建页：① 有 `task_id` 且该卡 done 且有产物；② 被 ≥2 页引用；③ 人工指定。`runtime/reports` 默认**不建页**，只作 `source` 锚点 |

## 7. 检索：`wiki_search`（Hermes 侧）

- **CLI**（只读）：`python tools/wiki_search.py --q <kw> [--tag X] [--type incident] [--entity official-1-1-7] [--limit 8] [--json]`。
- **排序**：`id` 精确 > `title/aliases` > `tags` > `source.path` > 正文；`verified_at` 空/超期 或 `confidence: low` ⇒ **降权**，结果行标 `[UNVERIFIED]`。
- **输出行**：`path:line  title  (type · verified_at · 命中字段)`。
- **退出码**：0=有命中 / 1=无命中 / 2=KB 不可读（fail-closed：**不许**把 2 当「查无此事」）。
- **技能**：`profiles/mas/skills/note-taking/wiki-search/SKILL.md`；三条纪律：① 动手前先搜 ≥1 次；② 命中即引用 `path:line`，不重复调查；③ 无命中要明说「知识库无记录」——**禁止凭记忆编**。
- **第二个入口**：只读 MCP 工具（挂 `mas-observe` 或独立 `mas-wiki` server），给巡检等无 terminal 的上下文用；接线遵守 `docs/MCP_TOOL_ROADMAP.md` §3 模式（`stdin=DEVNULL`、白名单、`{ok:false,error:{kind}}` 错误鞘）。环境变量见 §2 末。

## 8. 治理：`WIKI_GOV` 如何接进巡检

- **工具**：`tools/wiki_gov.py --json`（只读）→ `{pages, unverified, orphan, stale, ro_drift, scrub_skip, top:[…]}` + 单行 `WIKI_GOV=UNVERIFIED=n ORPHAN=n STALE=n DRIFT=n`。
- **定义**：`UNVERIFIED` = `verified_at` 空或超阈值（incident/entity 30 天；playbook/runbook/change 90 天）；`ORPHAN` = 无入链且不在 `00-index`；`STALE` = `source` 文件 sha256/mtime 已变而页 `updated` 更早（来源已变、页未复核）；`DRIFT` = §2 只读镜像异常。
- **复核节奏**：① 巡检每轮只读指标（零成本）；② 每周复盘卡清零（UNVERIFIED → 逐条复核或降级；ORPHAN → 补链或归档）；③ 每班次后 `wiki_sync --apply` 增量（可 cron 落 `runtime/reports/wiki_gov_<date>.md`）。
- **接巡检**：job `52b88b0ffca0`（每 30m、工具面只有 MCP、预算 18 次）第六步报表加第 11 行 `WIKI_GOV=…`；**方案 A（推荐）** 经 MCP 只读工具，**方案 B（过渡）** 工具面未提供时写 `UNKNOWN:工具面未提供`（与现协议 fail-closed 一致，不许顶替）。**红线上限**：`STALE>20` 或 `ORPHAN>10` ⇒ 建 P2 卡清理，不许静默累积。

## 9. 风险与红线（逐条对策）

| # | 风险 | 后果 | 对策 |
|---|---|---|---|
| 1 | **幻觉入库**（LLM 自由发挥写进页） | 假事实固化成「官方结论」 | 页必须有 `source[]`；sync 只做「摘录 + 锚点」不自由生成；人工页走 review 卡；`confidence: low` 降权 |
| 2 | 并发覆盖 / 半截写入 | 人手记被机器清掉 | lock + SYNC 块 + `os.replace` 原子写 + 默认 dry-run（§6） |
| 3 | **密钥外泄** | 不可逆（仓为 public） | KB 在公开仓之外（决策 1）；脱敏闸门 fail-closed；KB 自身 git **不设远端**（或私有）；预提交钩子再扫 `sk-\|ghp_\|webhook\|DPAPI` |
| 4 | Obsidian 插件依赖 | 插件缺失 ⇒ 视图全废 | 核心功能（阅读/链接/搜索）零插件可用；仅 3 条 Dataview 且各配 grep 等价（§4） |
| 5 | 备份缺失 | KB 丢失 | KB 自建 `git init` + 每班次自动 commit；**KB 可再生**（`wiki_sync --rebuild` 从机器层重放）；证据本体在 repo/看板附件 |
| 6 | 只读镜像被写穿 | 改坏仓内审计 | RO_DRIFT 清单 + 仓内 `git status` 交叉核对 + 命名/文档约定（§2） |
| 7 | 索引爆炸 | Obsidian 卡顿 | `_sources/reports/**` 排除索引；建页阈值挡掉 857 份 reports（§6） |

## 10. 三步施工计划与验收

| 步 | 做什么 | 验收（可复跑命令 + 期望） |
|---|---|---|
| **① 骨架**（~30min） | 建 `D:\1-2\MAAOrch-KB`（`wiki/`、`_sources/`、`git init`、`.gitignore`）、4 条 junction、`SCHEMA.md`、`wiki/00-index.md`、`.obsidian` 最小配置 | `cmd.exe /c "dir /AL D:\1-2\MAAOrch-KB\_sources"` ⇒ 4 行 `<JUNCTION>`；Obsidian 打开无报错；`git -C D:\1-2\MAAOrch status --porcelain` ⇒ **无新增/无改动**（证明未碰仓）；KB `git status` 干净 |
| **② 生成链**（~2–3h） | 写 `tools/wiki_sync.py`（含 `--selftest`/`--dry-run`/`--ro-audit`）+ 4 套模板 + 首批试灌：1 账号 / 1 脚本 / 1 实例 / 1 队列 / 1 incident / 1 playbook / 1 change（各带真 source） | `--selftest` PASS；`--apply` 后**再跑一次 `--apply` ⇒ `git -C KB diff --stat` 为空**（幂等）；暗桩测试：fixture 里塞 `api_key` 假值 ⇒ 报 `WIKI_SCRUB_SKIP` 且该源未落页；全程 `WIKI_RO_DRIFT=0` |
| **③ 检索 + 治理**（~1–2h） | 写 `tools/wiki_search.py` + 技能 `wiki-search`；写 `tools/wiki_gov.py`；巡检接 `WIKI_GOV=` 行（方案 A/B） | `wiki_search --q 官-1-1-7` ⇒ ≥3 条带 `path:line`，且含一条 `[UNVERIFIED]` 降权样例；`wiki_gov` 单行出现在巡检/日报告；植入 1 条过期页 ⇒ `STALE≥1`；KB 删掉后 `wiki_sync --rebuild` 能重建骨架 |

- 每步独立可回滚：①=删目录 + `rmdir` 4 条 junction；②③=删工具文件（KB 不受影响）；巡检行=改回提示词（备份 + 回读，按 house 规程）；施工卡纪律：只读/写边界写进卡面，动 `config.yaml`/巡检提示词必须带备份与回读。

## 11. 本文取证（2026-09-27 22:38 快照，均可复算）

- 计数：`docs/audits/*.md`=73、`docs/`=92 项（含本文新建 `design/`）、`runtime/reports`=857 文件（352 md）；看板 `kanban.db`=92 卡（done 57 / archived 15 / running 9 / todo 7 / blocked 4）+ attachments 47 + comments 77 + events 3338 + runs 96。
- 位置事实：看板库 `…\hermes\kanban\boards\maa-parallel-benchmark-20260926\kanban.db`（本轮以只读 `mode=ro` 读取）；附件在 `…\boards\<slug>\attachments\<task_id>\`；`sqlite3.exe` 存在于 `C:\platform-tools-latest-windows\platform-tools\`；本机**未安装 Obsidian**（无 `AppData\Roaming\obsidian`、无 `Program Files\Obsidian`）⇒ 当前没有可被误碰的现存 vault。
- 公开仓与黑名单：`.gitignore:131`（远端 public）、`:139`+`:121`（`runtime/` 与 `runtime/reports/` 均不入库）、`:154`（`78ad828` 误带事故）；junction 实测：`cmd.exe /c "mklink /J <link> <target>"` 成功、`dir /AL` 显示 `<JUNCTION>`、可穿透读取（scratch 验证后已 `rmdir` 清理）。
- 巡检事实：job `52b88b0ffca0`（`mas-readonly-inspection`，every 30m，deliver=local，工具面只有 MCP，预算 18 次，第六步固定 10 行报表）；发车 job `2600f6415f16`；`WIKI_GOV=` 行为本轮设计项（**尚未落地**）。相关依据：`docs/KANBAN_DISPATCH_POLICY.md`（§5.1 三数据源、§9.3 产出落 docs 或 runtime/reports）、`docs/MCP_TOOL_ROADMAP.md`（§3-4 只读 server 接线与 `stdin=DEVNULL`）、`docs/MAS_SCHEDULE_MAP.md`（槽位/队列实体来源）。

> 边界重申：本文**未创建 vault、未建 junction、未改任何配置或生产文件**；§2/§10 的命令是给施工卡的处方，不是已执行动作。
