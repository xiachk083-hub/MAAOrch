# 入库基线（GIT BASELINE）—— 口径、判据与纪律

- 任务：`t_1ca5e9ee`（2026-09-28，入库基线治理；执行者 `@mas`）
- 判据工具：`tools/git_baseline_check.py`（只读，末行 `GIT_BASELINE=OK|DRIFT:<n>`）
- 治理批次与逐文件清单：`docs/audits/2026-09-28_git_baseline/commit_log.txt`
- 扫描与复核证据：`docs/audits/2026-09-28_git_baseline/scrub_hits.txt`、`gitignore_review.md`

## 1. 为什么要有这条基线

Hermes 期（2026-09-26～09-28）整条工具链（`tools/mas_*.py`、审计/判据工具、治理文档）曾长期
游离在 git 之外：600+ 文件未跟踪、10+ 文件已改未提交。后果：

1. 多张卡并发改同一批 `tools/mas_*.py` 时，**被覆盖/丢失无法定位**，也没有 diff 可看；
2. 没有任何「回退点」——出错只能靠人肉记忆；
3. 远端 `xiachk083-hub/MAAOrch` 为 public，游离文件里一旦有敏感物，风险不可控。

本卡把未入库源码/文档**分批**落到本地基线（不 push），并交付漂移判据供后续巡检/发车前自检。

## 2. 漂移判据用法

```bash
$PY312 tools/git_baseline_check.py --line        # 默认：末行 GIT_BASELINE=OK|DRIFT:<n>（+前 10 条路径）
$PY312 tools/git_baseline_check.py --json        # 机读对象（counts + items + out_of_scope_count）
$PY312 tools/git_baseline_check.py --selftest    # 离线自测（临时仓库正/反用例）
```

- 限定路径：`tools/ services/ ui/ docs/ infra/ network/ app/`；**其余路径（如 `/tmp/`）不参与判据**。
- 判据：限定路径内「未跟踪数 = 0」且「未提交改动数 = 0」⇒ `OK`；否则 `DRIFT:<n>`（n=两类之和）。
- 只读：纯 `git status --porcelain -uall -z` 子进程（`stdin=DEVNULL` + 显式 timeout），不写文件、不联网。
- 判不出来（仓库异常）⇒ `GIT_BASELINE=ERROR:<...>`，rc=2 —— **fail-closed，不静默 OK**。
- 退出码：0=OK / 1=DRIFT / 2=ERROR / 3=selftest 失败。

## 3. 分批口径（本卡如何切批）

| 批 | 主题 | 口径 |
|----|------|------|
| F0 | `.gitignore` 复核补充 | `/tmp/` 与 `docs/audits/2026-09-28_cred_exposure/`（原始扫描堆）排除，见 `gitignore_review.md` |
| A | `mas_*` 只读/发车/巡检族 | `tools/mas_*.py`（审计判据四件套除外） |
| B | 审计与判据工具 | `mas_audit/mas_cred_audit/mas_dr_verify/mas_rollback_drill` + `audit_waterline/kanban_health/...` 等非 `mas_` 工具与微工具 |
| C | 治理与交接手册 | `docs/` 全量（audits 证据索引在内；cred_exposure 原始堆除外）+ 仓库技能 `.agents/skills/` |
| G | 测试 | 根 `tests/` 与 `tools/tests/`（MCP 探针、回归、演练） |
| D | 已跟踪未提交改动 | 逐文件 `git diff` 复核后 11 个 `M` |
| E | 收尾快照 | 运行中并发卡的第二批产物（首扫因「半成品保护」跳过的项） |

**半成品保护**：首次扫描时 mtime < 15 分钟的 `tools/*.py`（可能在别的卡的编辑窗口内）一律推迟到批 E；
收尾快照仅跳过「提交瞬间仍在写（<120s）」的文件，其余全部落基线。

## 4. 回滚

- 整批回退：`git revert <sha>`（每批一个提交，独立可回退）。
- 查看某批内容：`git show --stat <sha>`；对比：`git diff <sha>^ <sha> -- <path>`。
- **禁止** `git reset --hard` / `git rebase` / `git push --force`。

## 5. 纪律与红线（写进肌肉记忆）

1. **不 `git add -A`**：一律显式路径清单（本卡全部批次的清单见 `commit_log.txt`）。
2. **不 push**：本仓远端为 public；推 `origin` 必须由 lead/用户显式批准（`git push origin main`，本地 `main` 未配 upstream）。
3. **不改写历史、不 rebase、不 `--force`、不删分支。**
4. **敏感物零入库**：入库前过凭证特征扫描（口径见 `scrub_hits.txt`）；命中真实凭据的文件**排除**并留证。
   已排除：`docs/audits/2026-09-28_cred_exposure/`（300MB 原始扫描堆，preview 含真实凭据前缀）→ 人读报告 `docs/CREDENTIAL_EXPOSURE_AUDIT.md` 才入库。
5. **批量提交前复核**：每批 `git status` → `git add <清单>` → `git diff --cached --stat` → commit。
6. **漂移即警**：日常巡检/发车前可跑 `git_baseline_check.py --line`；`DRIFT` 说明有未落基线产物
   （要么补提交，要么按第 4 条排除并写明理由）。接线（巡检 prompt / MCP）归后续卡，本卡只交付判据。

## 6. 已知边界

- 判据只看「是否有漂移」，不判断**内容是否敏感**——敏感面仍需 `tools/mas_cred_audit.py` 周期扫描（见其报告第 4/6 节建议）。
- 并发卡在编辑窗口内产生的文件，按「半成品保护」推迟批次；若最终仍有 DRIFT，以卡面/commit_log 的逐条说明为准。
