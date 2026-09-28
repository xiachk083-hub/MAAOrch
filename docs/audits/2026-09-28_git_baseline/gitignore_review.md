# .gitignore 复核结论（2026-09-28，t_1ca5e9ee）

方法：`git status --porcelain -uall` 全量 + `git check-ignore -v` 逐项验证 + 本次入库清单实际过滤效果。

## 1. 已覆盖项（复核通过，不动）

| 面 | 规则 | 验证 |
|----|------|------|
| 日志 | `*.log` `logs/` `debug.log` `events*.log` | `logs/web_ui.log` 等 check-ignore 命中 |
| 凭据/账号 | `models/config.json*` `models/accounts/` `accounts*.json` `ScriptConfig.json`（任意层级） `ui/control_token.txt` `runtime/watchdog.ini` | 敏感件全部落在忽略面 |
| 备份副本 | `*.bak-*` `models/accounts/` | 防 `.bak` 绕过（原 P0 教训） |
| 运行产物 | `runtime/` `data/` `__pycache__/` `*.pyc` | 整目录 |
| 二进制/媒体 | `*.png *.jpg *.jpeg *.pptx *.log *.apk` | 本轮验证：`docs/audits/shots/.../*.png` 被 144 行规则挡住（2.1MB 未入清单） |
| 灰区/地雷 | `untitled-project/` `_trash/` `_sanity_check/` `_anni_check/` `.opencode/` `.playwright-mcp/` `docs/audits/snapshot/` | 均命中；未再出现游离目录 |
| 运行日志类 | `stderr.txt` `stdout.txt` `asst.log` `queue.json` `services/*.json` | 命中 |

## 2. 本次补入（新增两条）

1. **`/tmp/`（根级 scratch）** —— 实测 506 个未跟踪文件：探针脚本（`_probe_*.py`）、取证件
   （OCR 文本、轮询输出）、从主机拉回的 **原始 MAA 配置副本**（`tmp/boxread/pulled/cfg/*.json`、
   `tmp/hermes_daily_check/guan1/raw/MAA6_gui*.json` —— 含账号文本面，按 `ScriptConfig.json` 同口径不得入库）。
   性质：过程材料，不是项目内容；不忽略 = 下一次 `git add -A` 的确定性污染面。
   备注：`tmp/boxread/host_boxread.py`（主机侧 boxread，待提升为 `tools/` 正式工具的候选）仍在磁盘原位，
   不因忽略而丢失；提升动作走后续卡（copy → `tools/` → 正常入库）。
2. **`docs/audits/2026-09-28_cred_exposure/`（凭据审计原始扫描堆）** —— 300MB+：
   `dsh_shards*.jsonl`（DSH 会话转写扫描，179MB）、`repo.jsonl` 73MB、`remote.jsonl` 21MB 等；
   `preview` 字段抽检含**真实凭据前缀**（`eyJ…` JWT、`AQAAANCM…` DPAPI、手机号类条目 1079 条非空 preview）。
   属证据层面的敏感面，**禁止入库**；人读脱敏报告 `docs/CREDENTIAL_EXPOSURE_AUDIT.md`（本卡已入库）
   才是对外口径。与 2026-09-26 先例（`docs/audits/snapshot/` 审计冻结副本不得入库）同规则。

编辑落点：`.gitignore` 末尾「2026-09-28 入库基线复核」节（提交 F0，`git revert` 可撤）。

## 3. 复核未采纳项（说明）

- `docs/audits/2026-09-28_*/`（其余审计目录，共 11 个）**保留入库**：属治理证据（md/txt/json 小件），
  正是本卡要落基线的内容；仅 cred_exposure 原始堆因敏感+体积排除。
- `tests/`、`tools/tests/`、`.agents/`（仓库技能）**保留入库**：真实源码/技能，不在忽略面。
- `runtime/` 已整体忽略：其中 `runtime/snapshots/*/redacted/*` 存在「名义脱敏」缺陷（见 cred 审计 F1），
  已由该审计单独立案，本卡不动（防凭据扩散面变大）。

## 4. 敏感复核结论（与 `scrub_hits.txt` 对应）

- HIGH 命中 164 → 复核后 **163 条为假阳性/自测夹具**：
  - 154 条为 `cred_exposure/*.jsonl|tsv` 中文件名子串 `ta🐍sk-<hex>`（"task-" 含 "sk-"）——这些文件已整目录排除，不入库；
  - 8 条 phone 命中为自测夹具/示例（`13412345276`、`18519883616`、`13800000000`、`13812345678`，
    均为掩码函数/审计 schema 的构造输入；`13812345678` 是文档声明的掩码示例口径）；
  - 1 条 `eyJ` 为 `tools/mas_cred_audit.py` 自测合成样本（拼接构造，非真实 JWT）。
- 真实敏感面（1 项）：`cred_exposure` 原始扫描堆 —— **排除入库 + 已补 .gitignore**（见 §2.2）。

## 5. 给 lead 的待批项

- 上述两条补入若与你口径冲突：`git revert <F0 sha>` 即可撤销，或卡面回复改法。
- `tools/mas_cred_audit.py` 自测用「拼接法」避免明文手机号（`"1"+"3"+...`），其它工具自测直接写 11 位夹具号
  （本次已复核为构造值）——是否统一口径为**后续卡**（本卡红线：不改 tools/*.py 业务逻辑）。
