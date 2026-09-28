# 凭据暴露面审计（CREDENTIAL EXPOSURE AUDIT）

- 任务：`t_97f99a59`（只读审计；审计报告本身不含任何明文凭据）
- 日期：2026-09-28
- 取证引擎：`tools/mas_cred_audit.py` v1.1.2（`--selftest` PASS）
- 证据目录：`docs/audits/2026-09-28_cred_exposure/`
- 规则：本报告只写 **文件 / 行号 / 类型 / 长度 / 是否可逆 / 风险等级**，不写明文值。

---

## 0. 结论摘要（先看这个）

| # | 结论 | 风险 |
|---|------|------|
| F1 | **「已脱敏」副本仍在泄漏完整手机号**：`runtime/snapshots/*/redacted/config/ScriptConfig.json`、`runtime/_buyer_fleet_20260925/ScriptConfig.redacted.json` —— 脱敏器只处理 `Password`，`Id`/`Notes` 字段原样保留完整 11 位手机号（明文直读，100 处/文件） | **高** |
| F2 | 源配置 `E:\AUTO-MAS\config\ScriptConfig.json` 及 9 个 `.bak` 备份：完整手机号 ×108/文件 + DPAPI 密文口令 ×44–46/文件；且其副本已扩散到看板工作区（2 处 ×100）、`tmp/hermes_daily_check`（100）、本地快照 `files/`（100） | **高**（受控但面大；DPAPI 同机同用户可逆） |
| F3 | 看板任务日志=worker 回显落盘：`t_97f99a59.log` 95 条完整手机号 + 7 条空格拆分变体 + 23 条「掩码泄漏」+ 8 条 DPAPI 密文 + 9 条 JWT 形态；`t_c2442fc7.log` 78 条；`t_56ea4c40.log` 8 条 | **高** |
| F4 | `C:\Users\xiach\AppData\Local\hermes\config.yaml:263`：`Authorization: Bearer <32 字符>`（MCP token）明文存放 | 中 |
| F5 | `D:\1-2\MAAOrch\models\config.json:510`：`ai_api_key`（35 字符，已知前缀的 API key），另有 8 个备份副本 | 中（建议轮换） |
| F6 | DSH 会话转写（本地）：bearer 904、prefixed_key 344、phone_full 1,045（2 个会话各 236×2）、cookie_val 112、phone_mask_leaky 26 —— 命令回显/工具结果原样入库 | 中 |
| F7 | 噪声（已判读，非密钥）：MAA gui 日志 `authorization: PenguinID <数字ID>` ×36,471；`logs/web_ui.log` 本地 `?token=`（38 字符）出现在 uvicorn 访问日志 | 低 |
| F8 | 灰区：docs/代码里「掩码不彻底」的手机号形态（可见 8–9 位+2 星）3 处 | 低 |

---

## 1. 审计范围与方法

| 面 | 路径 | 文件数 | 命中 | 备注 |
|----|------|--------|------|------|
| 仓库文本 | `D:\1-2\MAAOrch\**`（docs/tools/services/logs/data/tmp/… 分根） | 34,495 | 267,386 | `logs` 240,490（1221s）；`services` 11,027 |
| 运行时 | `D:\1-2\MAAOrch\runtime\**`（含 snapshots） | 26,609 | 62,558 | skip 581（超限/二进制） |
| Hermes 看板/日志/DB | `%LOCALAPPDATA%\hermes\{profiles,kanban,*.db,logs}` | 784 | 20,045 | 看板 logs + profiles state.db（含 FTS 去重） |
| 大对象补扫 | `state.db`×2、`asst.bak.log`、`rogue_fleet_probe.jsonl` | 4 | 6,000 | 每文件上限 1500 命中，属**下界** |
| 远端 AUTO-MAS | `E:\AUTO-MAS\{config,data,history,debug}`（经 SSH） | 14,698 | 86,737 | history 70,898；config 10,070 |
| DSH 会话转写 | `~\.dsh\sessions\**`（.jsonl.zstd，多帧） | 409 会话目录 / 715 文件 | 586,941 | 9 分片并行；跳过 250（超限/二进制） |

扫描器能力：手机号（完整/掩码/空格拆分/ANSI 粘连）、命名字段（password/skland_token/serverchan_key/api key/…）、长 base64/hex 高熵串、JWT、DPAPI 密文、已知前缀 API key、Bearer/PEM/cookie；按 `a`（受控必须存在）/`b`（不该出现）/`c`（灰：受控副本但应治理）分类。

---

## 2. 发现清单（按面）

### 2.1 「已脱敏」副本残留完整手机号（F1，高）
- `runtime\snapshots\20260928_092944__baseline\redacted\config\ScriptConfig.json`：**100** 处 `phone_full`（行 548/558/573/742/…，len=11，明文直读）
- `runtime\snapshots\20260928_095643__pre-drill\redacted\config\ScriptConfig.json`：**100** 处
- `runtime\_buyer_fleet_20260925\ScriptConfig.redacted.json`：**100** 处
- 同一文件中 `Password` 字段已正确显示为 `[已脱敏]` —— 说明脱敏管线「按字段白名单」工作，`Id`/`Notes` 未在名单内。**这是流程缺陷**：任何拿到该「脱敏」文件的人可以直接还原 100 个账号的手机号（= 半数登录凭据）。

### 2.2 源配置与备份扩散（F2，高）
- `E:\AUTO-MAS\config\ScriptConfig.json`：完整手机号 100 + DPAPI 密文口令 44
- 9 个 `ScriptConfig.json.bak_*`（20260831、tw2、flatten、agent、12slot、zcode…）：每份手机号 70–108、DPAPI 44–46
- 扩散副本（同值 `dup_files` 分组）：看板工作区 `t_4ba244b9\ScriptConfig.json`（100）、`t_52ebdda4\_read_users.json`（100）、`tmp\hermes_daily_check\guan1\raw\ScriptConfig.json`（100）、本地快照 `files\config\ScriptConfig.json`（100）、`runtime\_mas_users.txt`（DPAPI 6）
- C 类（受控但应治理）：DPAPI 密文在**同机同用户**下可解 ⇒ 备份面=明文等价面。

### 2.3 看板任务日志（worker 回显）（F3，高）
- `…\kanban\boards\maa-parallel-benchmark-20260926\logs\t_97f99a59.log`：phone_full 95、phone_full_spaced 7、phone_mask_leaky 23、dpapi_blob 8、jwt 9
- `t_c2442fc7.log`：phone_full 78、leaky 2；`t_56ea4c40.log`：leaky 8；`t_4dc0a418.log`：leaky 2
- `phone_mask_partial`：`t_9fc91afc.log` 10、`t_263b584f.log` 4、`t_7d630aa6.log` 2
- 机理：worker 的工具输出（读配置、跑脚本的回显）原样进日志；含 ANSI 转义粘连的 11 位号码（新增 `phone_full_spaced` 变体专门捕获）。

### 2.4 Hermes 配置（F4，中）
- `C:\Users\xiach\AppData\Local\hermes\config.yaml:263`：`Authorization: Bearer <32>`（`mcp_servers.*.headers`，明文直读，cls=a）
- 建议：迁移到环境变量/vault；该文件出现在多个扫描面（profiles 根）里。

### 2.5 MAAOrch 自身凭据（F5，中）
- `D:\1-2\MAAOrch\models\config.json:510`：`ai_api_key`（35 字符，已知前缀，明文直读），`dup_files=9`（含 `.bak-*` 与 `backups\config_*.json`）
- 建议：**轮换**该 key，改由环境变量注入；备份目录加 ACL。

### 2.6 远端 AUTO-MAS（F2 补充）
- `E:\AUTO-MAS\config`：10,070 命中；`history` 70,898；`debug` 1,500（上限截断）
- `phone_full` 1,423（config 100×10 份 + history 内文本）、`dpapi_blob` 503、`jwt` 148、`prefixed_key` 0
- 远端文件名审计：19,310 个文件，0 个文件名含手机号（干净）

### 2.7 DSH 会话转写（F6，中）
- 9 分片全部完成（合并 `dsh_shards.jsonl`，179 MB）：`phone_full 1,045`、`bearer 904`、`prefixed_key 344`、`cookie_val 112`、`phone_mask_leaky 26`、`phone_mask_partial 2`、`dpapi_blob 9`、`key_literal 944`、`tokenish_hi 338,637`、`tokenish_mid 223,452`、`hex_digest 21,125`
- `phone_full` 高度集中：`session-20d1b921` 与 `a0bc2231` 两个会话各 236×2（当前副本 + `.bak-20260923` 备份同值）——即 100 个账号的手机号被完整贴进 2 个 DSH 会话（及其备份），其余会话仅 15–34 条
- `bearer 904` / `prefixed_key 344` / `cookie_val 112`：命令与工具结果里的 `Authorization: Bearer …`、`API_KEY=`、`token=`、`cookie=` 回显（len 20–67 的 b64/hex 串）
- 文件层面：715 文件扫描、250 跳过（>256 MB 或非文本），无 `jwt`
- 结论：转写面 = 「worker 复制的数据 + 回显的凭据」双重泄漏；本地留存可接受，但应做行级脱敏（见第 4 节第 7 条）

### 2.8 噪声与灰区（已判读）
- **F7**：MAA `gui.log`（454 文件）里 `authorization: PenguinID <数字>` 36,471 条 = 企鹅物流上报 ID，非密钥；`logs\web_ui.log:988/989` 的 `?token=<38>` 是本地 127.0.0.1 API 访问日志（cls=b，建议关闭 query 记录）；`jwt` 1,026 条集中在 MAA 侧日志（4/文件），多为上游遥测 token
- **F8**：`docs\MAA_CLI_DEPLOY.md:136`（8 位可见 + 2 星）、`docs\MAS_SCRIPTS_CATALOG.md:151`（9 位可见）、`services\ai_agent\fastshot.py:74`（9 位可见）——「掩码不彻底」形态，结合他处可还原
- 数据文件里的 `key_literal`（`data\_enemy_db.json` 488 等）为游戏数据字段名，非凭据

---

## 3. 分类汇总（a / b / c）

- **a 必须存在（受控）**：`E:\AUTO-MAS\config\ScriptConfig.json`（手机号+DPAPI）、hermes `config.yaml` 的 MCP header
- **b 不该出现（需清理/断言）**：看板任务日志（F3）、docs 灰区（F8）、`web_ui.log` 的 `?token=`、工作区里的配置副本（F2 扩散项）
- **c 灰色（受控但应治理）**：`runtime\snapshots\*\redacted\*`（**名义脱敏实则未脱敏，F1**）、`_buyer_fleet_20260925\*.redacted.json`、`models\config.json` 及其备份、DSH 转写中的命令回显

**可逆性**：手机号=明文直读；DPAPI=同机同用户可逆、跨机不可逆；`mask_leaky`/`mask_partial`=可见位数偏多，结合他处数据可还原；`jwt`/`bearer`/`prefixed_key`=自包含、直读即可用。

---

## 4. 修复清单（按优先级）

1. **修脱敏器（F1，立即）**：`tools/redact_scriptconfig.py` 改为**全字段**扫描替换（任何 `1[3-9]\d{9}` 形状一律替换，不依赖字段白名单），并对 `Id`/`Notes`/`Name` 等文本字段做完整覆盖；加 selftest 断言「输出文件不得再出现完整手机号」；重生成 `runtime\snapshots\*\redacted\` 与 `_buyer_fleet_20260925\ScriptConfig.redacted.json`。
2. **看板日志（F3）**：对 `kanban\boards\*\logs\*.log` 做一次「保结构替换值」的批量脱敏；worker 写日志前过 `mas_cred_audit.scan_line`，命中 `HIGH_KINDS` 即替换为 `<masked:kind:len>`。
3. **凭据轮换（F5）**：轮换 `ai_api_key`（建议改环境变量注入）；`models\config.json` 备份收敛（保留最新 + 最早一份）。
4. **hermes config.yaml（F4）**：MCP Authorization 改环境变量/vault 引用；文件 ACL 收紧到当前用户。
5. **AUTO-MAS config 备份面（F2）**：`.bak_*` 收敛为「最新 2 份 + 每周归档」，目录 ACL 限 Administrator/SYSTEM；工作区/临时目录里的 `ScriptConfig.json`、`_read_users.json` 用后即清（或改为只读引用）。
6. **工具接线（防复发）**：把 `tools/mas_cred_audit.py --fail-on phone_full,phone_mask_leaky,dpapi_blob,prefixed_key,bearer,jwt --root …` 接入日常巡检（与 `WIKI_GOV` / 巡检 job 并列）；对 `docs/`、`runtime/`、kanban 日志周期性跑，非零退出即报。
7. **DSH 转写（F6）**：本地保留（系统需要），但建议在写转写前对 `Authorization`/`API_KEY`/`PASSWORD` 等命令回显做行级脱敏（同样复用 `scan_line`）。

---

## 5. 「以后怎么防」（一条主规则 + 两条配套）

> **主规则：任何把命令输出 / 配置内容 / 工具回显写进持久面（日志、看板注释、文档、转写文件）的路径，写入前必须过一次 `mas_cred_audit.scan_line` 过滤；命中高信号 kind（手机号完整/掩码泄漏、DPAPI、Bearer/JWT/API key）时替换为 `<masked:kind:len>` 再落盘。新增此类写入点时必须声明并接入该过滤器。**

配套：
1. **脱敏器 fail-closed**：任何「脱敏/红acted」产出，必须带 selftest 断言（输出中不得出现 `1[3-9]\d{9}`、不得出现命中 kind），否则该流程视为未完成。
2. **同名副本即风险**：禁止把含凭据的配置整体复制进工作区/临时目录/快照；需要副本时复制「已过脱敏器且有断言」的版本。

---

## 6. 证据索引与复现

```
docs/audits/2026-09-28_cred_exposure/
  repo.jsonl / repo.tsv            # 仓库文本（34495 文件，267386 命中）
  repo_runtime.jsonl / .tsv        # runtime/**（26609 文件）
  hermes.jsonl / .tsv              # Hermes 面 v1（旧版引擎）
  hermes_v2.jsonl / .tsv           # Hermes 面 v2（ANSI/空格变体，权威）
  big.jsonl / .tsv                 # state.db×2 + asst.bak.log + rogue_fleet_probe.jsonl
  remote.jsonl / .tsv              # E:\AUTO-MAS（SSH 远端扫描）
  dsh_shards/dsh_0..8.{jsonl,tsv,log}   # DSH 会话 9 分片（6/9 已完成）
  *.run.log                        # 每次扫描的逐根计时与汇总 JSON
```

复现（单面示例）：
```
python tools/mas_cred_audit.py --selftest
python tools/mas_cred_audit.py --root D:/1-2/MAAOrch/docs=doc --jsonl out.jsonl --summary out.tsv
```
远端：`ssh -i ~/.ssh/maaorch_target Administrator@100.79.173.69` + stdin/base64 上传扫描器（见 scratch `remote_scan.cmd`、`mk_upload.py`）。

工程备注（本轮修好并验证的引擎缺陷）：`_dg` 摘要不落盘（双保险 pop）、selftest 样例、zstd 多帧转写读取（py3.12 `zstandard` / py3.14 标准库）、FTS 影子表去重、ANSI 粘连号码（`phone_full_spaced`）、空格拆分号码、长行关键词预筛（性能）、`--fail-on` 退出码。

---

## 7. 未完成项（诚实声明）

- `big.jsonl` 的 4 个文件各被 1500 命中上限截断（cap），属**下界**；如需全量可提高 `--max-per-file` 重扫（`mas/state.db` 319 MB 单文件约 5 分钟）。
- DSH 面跳过 250 个文件（>256 MB 或非文本）——如需覆盖可提高 `--max-bytes` 或先转码。
- 本报告未包含任何明文凭据；如需逐条核对，请用证据 JSONL（只含 file/line/kind/len/可逆性）。
