# 审计统一：`maaorch.audit.v1` schema · `tools/mas_audit.py` · 轮转与桥接

- 卡：`t_d937f659`「审计日志统一：ledger/dispatch/farm/profile 多套日志的 schema 统一与轮转」｜执行：mas（Hermes）｜日期：2026-09-28
- 产出：**本文件** + `tools/mas_audit.py`（新工具）+ 证据目录 `docs/audits/2026-09-28_mas_audit_tool/`
- 一句话：**写入收敛到一条流**（`runtime/audit/mas_audit.jsonl`，schema `maaorch.audit.v1`），
  **查询多源合并**（统一流 + 归档 + 9 套旧日志 + 看板库，全部只读桥接），**增长交给按大小/天数轮转 + 保留期 + 人执行的清理清单**。
- 边界（本卡约束）：**不改任何现有工具**——接入现有工具 = 另开卡，本文件 §8.2 只给到「文件:行 + 片段」级 patch 建议。
  历史日志**不迁移、不重写、不删除**；`--legacy` 是**读时桥接**（零写入）。

---

## 0. 现状：9 套日志 + 看板库，字段与时间戳都不统一

**实测盘点（2026-09-28 10:19 现取，`tools/mas_audit.py status`）**：

| 流 | 写者（现状） | 字段形态 | 体积/行数 | 桥接覆盖 |
|---|---|---|---|---|
| `runtime/mas_ledger.jsonl` | run_profile / probe_farm / farm_orchestrator / stage_fix / watch / activity_switch | `{kind, ok, run_id, scope, detail(JSON字符串), ts}` | 18.5 KB / 39 | 39/39 |
| `runtime/mas_dispatch_log.jsonl` | mas_dispatch / mas_ondemand（真动作） | **4 代混写**：`{queue,queue_id,resp…}` / `{script,dir,http,resp…}` / `{kind,target_kind,target,mode,ok,code,taskId…}` / …（见 §8.1） | 25.1 KB / 64 | 64/64 |
| `runtime/mas_ondemand_log.jsonl` | mas_ondemand（全量调用，含干跑） | `{kind,target,tool,apply,verdict,target_uid,result{…}}` | 11.8 KB / 35 | 35/35 |
| `runtime/mas_run_profile_log.jsonl` | mas_run_profile | `{event, profile, script, accounts, steps/refusals, ts}` | 48.4 KB / 52 | 52/52 |
| `runtime/mas_probe_farm_log.jsonl` | mas_probe_farm | `{event, account, stage, before, after, rc, verdict}` | 1.7 KB / 6 | 6/6 |
| `runtime/mas_farm_orchestrator_log.jsonl` | mas_farm_orchestrator | `{run_id, event, rc, conclusion}` | 0.3 KB / 2 | 2/2 |
| `runtime/mas_plan_builder_log.jsonl` | mas_plan_builder | `{event, account, script, stage_plan, mode, json, md}` | 8.7 KB / 11 | 11/11 |
| `runtime/mas_config_snapshot_log.jsonl` | mas_config_snapshot | `{action, ok, snapshot, label, files, sensitive, dir…}` | 3.8 KB / 16 | 16/16 |
| `runtime/mas_patch_guard_log.jsonl` | mas_patch_guard | `{ts, kind, patch, file, backup, ok, sha256, fails}` | 3.4 KB / 10 | 10/10 |
| 看板库 `kanban.db`（`task_events` + `task_comments`） | Hermes kanban kernel | `{task_id, kind, payload, created_at}` / `{task_id, author, body}` | 2.2 MiB / 7,400+ / 160+ | 近 2000 事件 + 全部注释 |
| `runtime/reports/farm_run_*.md` | mas_farm_orchestrator | 人读 markdown（无 schema） | 单份 ~1.3 KB | 不桥接（人读件；报告路径可进 `evidence`） |

合计（不含看板与 md）：**≈119 KiB / 235 行**。桥接后**所有行都读得出 v1 语义**（`legacy_skipped=0`，见证据 `query_demo.txt`）。

**因此才不统一**（本卡要解决的问题，都是实测）：

1. **同一件事四种键名**：`kind` / `event` / `tool` / `action`；目标字段在 `target` / `queue` / `script` / `account` 间跳。
2. **时间戳三种形态并存**：`"2026-09-28 08:58:21"`（空格）、`"2026-09-24T20:37:14"`（ISO-T）、`"2026-09-24T20:4x"`（半写坏行，ledger 里真有）——
   ⇒ 按时间检索要么漏、要么把字符串排序当时间排序（`'T' > ' '`，ISO-T 行会冒泡到最前）。
3. **没有 actor 与卡号**：出了事只能靠 `source`/`note` 里手写的中文串猜是谁、哪张卡。
4. **全部 append-only 无界**：9 套都在长，没有任何一套有轮转/保留（存储卡 `t_880f33e1`/`t_2279c784` 已把「本机 runtime 无界」列为待办）。

---

## 1. 交付物

| 项 | 路径 | 性质 |
|---|---|---|
| 统一入口工具 | `tools/mas_audit.py` | 新增：append/query/status/rotate/prune/validate/schema + `--selftest`（19 例离线） |
| schema 定义（本文件） | `docs/MAS_AUDIT_SCHEMA.md` | 新增 |
| 机读 schema | `tools/mas_audit.py schema --json`（快照 `docs/audits/2026-09-28_mas_audit_tool/schema.json`） | 新增 |
| 统一流（live） | `runtime/audit/mas_audit.jsonl` | 新增；本卡首行 = `audit.bootstrap`（`worker:mas`） |
| 归档目录 | `runtime/audit/archive/mas_audit-<YYYYMMDD-HHMMSS>.jsonl.gz` | 轮转产物（按月按需生成） |
| 跨进程锁 | `runtime/audit/mas_audit.lock` | 与 `runtime/mas_ledger.lock` 同款语义（陈旧 120s 可回收） |
| 证据 | `docs/audits/2026-09-28_mas_audit_tool/{selftest.txt,cli_demo.txt,status.txt,status.json,query_demo.txt,schema.json,validate.txt}` | 实跑留档 |

---

## 2. 统一事件 schema（`maaorch.audit.v1`）

一行 = 一个 JSON 对象（JSONL），**只追加，不重写**。`append` 时强制校验（见 §3）。

### 2.1 字段表

| 字段 | 必填 | 类型 | 说明 / 约束 | 例 |
|---|---|---|---|---|
| `v` | ✅ | int | 恒 `1`（schema 版本） | `1` |
| `ts` | ✅ | str | **本地时间（CST）**，`YYYY-MM-DD HH:MM:SS`（与仓库其它日志同格式，不用 ISO-T） | `2026-09-28 09:58:36` |
| `actor` | ✅ | str | `human:`｜`worker:`｜`tool:`｜`sched:`｜`host:` + 名字（**谁干的**） | `tool:mas_ondemand`、`worker:mas`、`human:xiach`、`sched:cron:2600f6415f16` |
| `action` | ✅ | str | `域.动词`（小写、点分；**干什么**）。域词表见 §4.2；新域只告警不拒绝 | `dispatch.start`、`profile.refuse` |
| `target` | ✅ | str | `kind:id` 或 `-`（**对谁**）。kind 词表见 §4.3 | `account:帝国猛虎`、`dir:E:\MAA-Multi\MAA_6`、`card:t_d937f659`、`-` |
| `result` | ✅ | str | `ok`｜`fail`｜`refused`｜`info`｜`unknown`（**结果**，语义见 §4.4） | `refused` |
| `reason` | ✅ | str | 为什么拒绝/失败；没有就空串（**恒存在**，便于 `jq` 无条件取） | `G3:目标目录正在跑 MAA` |
| `params` | ⭕ | obj | **已脱敏**的输入摘要（≤2000 字符，超长截断留指纹）；空则省略 | `{"mode":"AutoProxy","code":200}` |
| `evidence` | ⭕ | arr[str] | 证据**指针**（文件路径/命令/URL），不放内容；空则省略 | `["runtime/mas_ondemand_log.jsonl"]` |
| `card` | ⭕ | str | 关联看板卡，**形如 `t_[0-9a-f]{6,}`**（不符即拒；不给则省略。CLI 缺省读 env `HERMES_KANBAN_TASK`，worker 场景免手填） | `t_d937f659` |
| `run_id` | ⭕ | str | 工具内 run_id / upstream taskId（跨行串联一次运行） | `pf_20260928_084240_LEUNG` |
| `extra` | ⭕ | obj | 逃生舱（来源标记、形状标记等）；桥接行在此带 `legacy` / `shape` | `{"legacy":"mas_ledger"}` |

> 省略规则：**必填 7 项恒在**（含 `reason`）；`params`/`evidence`/`card`/`run_id`/`extra` **空则整键省略**。
> 追加语义：每行独立成事件；不要修改已落盘行（要更正就再写一条，`reason` 里写明更正关系）。

### 2.2 真实样例（本流首行，原文）

```json
{"v": 1, "ts": "2026-09-28 09:58:36", "actor": "worker:mas", "action": "audit.bootstrap", "target": "file:runtime/audit/mas_audit.jsonl", "result": "ok", "reason": "统一审计入口上线：schema maaorch.audit.v1 首次落盘", "params": {"schema": "maaorch.audit.v1", "legacy_bridge": "--legacy（9 套旧日志只读桥接）"}, "evidence": ["docs/MAS_AUDIT_SCHEMA.md", "tools/mas_audit.py"], "card": "t_d937f659"}
```

### 2.3 与卡面字段的对应（逐项有落点）

| 卡面要求 | 落点 |
|---|---|
| ts | `ts` |
| actor（人 / worker / 工具） | `actor` 前缀 `human:` / `worker:` / `tool:`（+ `sched:` 定时、`host:` 主机侧） |
| action | `action`（`域.动词`，有词表） |
| target | `target`（`kind:id`，有词表） |
| params（脱敏） | `params`（§5 强制脱敏 + 截断） |
| result | `result`（5 值枚举） |
| evidence | `evidence`（指针数组） |
| reason | `reason`（恒在，可空串） |
| 关联卡 | `card`（+ `HERMES_KANBAN_TASK` 自动带） |

---

## 3. 校验：不合规写不进去（append 即拒绝，rc=2）

| 字段 | 规则 | 反例（会被拒） |
|---|---|---|
| `v` | == 1 | `"v": 2` |
| `ts` | 正则 `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$` | ISO-T、只有日期、`20:4x` |
| `actor` | `^(human\|worker\|tool\|sched\|host):[^\s:]+$` | `mas`（没前缀）、`tool:my tool`（带空格） |
| `action` | `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`（≥2 段） | `DispatchStart`、`dispatch`（单段） |
| `target` | `^([a-z][a-z0-9_]*:[^\s]+\|-)$` | `没有冒号`、`Account:甲`（kind 大写） |
| `result` | 5 值枚举 | `success`、`OK` |
| `reason` | 必须是字符串（可空） | 缺键 / 数字 |
| `params` | 对象（否则拒）；内部键值递归脱敏 | `"params": "text"` |
| `evidence` | 字符串数组 | 单个字符串 |
| `card` | 给值时须形如 `t_[0-9a-f]{6,}`（给错即拒；不给则整键省略） | `t_XYZ`（被拒；要记录非卡号对象请用 `target`） |
| 手机号 | `actor/action/target/reason` 里出现未打码 11 位号 = 校验失败 | `打给 13812345678` |
| `action` 域 | 不在 §4.2 词表 ⇒ **stderr 告警**（不拒绝；保证可扩展） | `foo.bar`（告警后写入） |

拒绝样例（实跑见 `cli_demo.txt`）：

```
$ python tools/mas_audit.py append --actor tool:x --action DispatchStart --target - --result ok
拒绝：action 必须是 域.动词（小写，如 dispatch.start）：'DispatchStart'      # rc=2
```

---

## 4. 枚举与词表

### 4.1 actor（5 类）

| 前缀 | 谁 | 用法 |
|---|---|---|
| `human:` | 人 | `human:xiach`（人工发车/人工确认） |
| `worker:` | Kanban/巡检 worker | `worker:mas`（Hermes profile 名；建议带上 `card`） |
| `tool:` | 工具进程 | `tool:mas_dispatch`、`tool:mas_ondemand`（**工具接入用自己的文件名**） |
| `sched:` | 定时器 | `sched:cron:2600f6415f16`（Hermes cron）、`sched:task:DailyRestart0400`（宿主计划任务） |
| `host:` | 主机侧代理 | `host:MRFZ-0000`（远端执行的只读探针/代理） |

### 4.2 action：域.动词（现有域 + 常用动作）

| 域 | 常用动作 | 备注 |
|---|---|---|
| `dispatch` | `.start` `.stop` `.refuse` `.defer` `.queue` `.script` `.preflight` `.wake` | 发车/停止/被拒/延期；`queue`/`script` = 对队列/脚本发起的调用（含干跑） |
| `profile` | `.plan` `.apply` `.refuse` `.readback` `.restore` `.done` | run_profile 八步 |
| `farm` | `.probe` `.run` `.done` `.refuse` | 试探/编排 |
| `stage` | `.plan` `.probe` `.fix` `.ledger_update` | 关卡计划/台账 |
| `account` | `.switch` `.login` `.inspect` `.activity_switch` | 切号/上号读 |
| `config` | `.snapshot` `.restore` `.diff` | 配置手术 |
| `lease` | `.acquire` `.release` `.steal` | 实例租约 |
| `patch` | `.check` `.reapply` `.capture` | AUTO-MAS 补丁 |
| `board` | `.create` `.claimed` `.completed` `.crashed` `.blocked` `.comment` | 看板事件（桥接行统一用这个域） |
| `audit` | `.bootstrap` `.rotate` `.prune` `.migrate` | 审计自身 |
| `run` | `.start` `.end` `.watch` | 通用运行/守护 |
| `manual` | `.note` `.deliver` | 手工说明/交付 |
| `kb` `emu` `queue` `host` `ledger` | 各自 | 词表未列出的新域：**告警 + 允许**（先落地，再补词表） |

### 4.3 target kind

`account:`（账号名）｜`script:`（脚本名/uid）｜`dir:`（MAA 目录，如 `dir:E:\MAA-Multi\MAA_6`）｜`emu:`（实例 idx）｜
`queue:`（队列名）｜`task:`（上游 taskId/UUID）｜`stage:`（关卡码）｜`file:`（文件路径）｜`host:`（主机）｜
`card:`（看板卡）｜`run:`（工具内 run_id）｜`snapshot:`（配置快照 ts）｜`batch:`（批量窗口）｜`job:`（定时/计划任务）｜`-`（无目标，如全局动作）。

（实现里是 `KNOWN_TARGET_KINDS` 白名单：**kind 不在表里 = 校验失败**；要新增 kind 就改这一处 + 本表。）

### 4.4 result：5 值与判据

| 值 | 判据（怎么写） |
|---|---|
| `ok` | 动作完成且达到意图（含「已确认无残留」） |
| `fail` | 动作执行了但**没达到意图**（异常/超时/落空） |
| `refused` | **动作没执行**，被闸门/参数/冲突拦住（零副作用）。碎念：拒绝 ≠ 失败，重试策略不同 |
| `info` | 只是记录（干跑、计划、状态快照）——**别拿它当成功** |
| `unknown` | 结果判不了（信息不足），**明确不猜** |

---

## 5. 脱敏红线（写入前强制，非可选）

1. **凭据类键整键丢弃**（键名含以下子串即丢，不留半截）：
   `password` `passwd` `token` `secret` `cookie` `credential` `skland` `apikey` `api_key` `private_key`
   `sessionid` `session_id` `phone` `mobile` `手机` `auth_token` `authorization`。
2. **字符串值里的 11 位手机号打码**：`13812345678` → `138****5678`（只留首 3+末 4，与知识库/文档同口径）；
   命中未打码手机号的记录**连写都写不进**（§3 校验）。
3. **params 截断**：序列化 >2000 字符 ⇒ 整体替换为 `{"_truncated": true, "_chars": N, "_sha256_12": "…"}`（事件保留、内容降级、可核对指纹）。
4. **禁止入任何字段**（含 `reason`/`evidence`/`extra`）：口令与验证码、token/cookie、全串手机号、
   `E:\AUTO-MAS\config\*.json` 的 DPAPI 密文、私钥、任何 `.env` 内容。要留证据 ⇒ **放指针**（路径/命令），不放值。
5. 与既有实现的**一致性**：本工具 `scrub()` = `tools/mas_run_profile.py::scrub` 同表 + 手机号值掩码 + 截断；
   兄弟实现（`mas_shift_report` / `mas_probe_farm` / `mas_plan_builder` / `mas_farm_orchestrator` / `mas_scrub_proxy`）语义一致，接入时无需换口径。

---

## 6. 用法

### 6.1 命令表

| 命令 | 作用 | 默认行为 |
|---|---|---|
| `append` | **其它工具/worker/人的唯一写入入口** | 真写（`--dry-run` 只打印） |
| `query` | 统一流 + 归档 ± 旧日志桥接 | 只读 |
| `status` | 水位：统一流/归档/9 套旧日志/看板库 + 阈值判定 | 只读 |
| `rotate` | 轮转检查 | **只报不转**（`--apply` 才转） |
| `prune` | 归档保留期清理 | **dry-run 只列候选**（`--apply` 才删） |
| `validate` | 校验 live/归档每行是否符合 v1 | 只读 |
| `schema` | 打印 v1 JSON Schema | 只读 |
| `--selftest` | 19 例离线自测（零触网，只写临时目录） | — |

### 6.2 常用命令（可直接抄）

```bash
PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"   # 仅 stdlib，默认 python 也能跑

# 写（其它工具接入的等价格式）
$PY312 tools/mas_audit.py append --actor tool:mas_ondemand --action dispatch.start \
    --target account:帝国猛虎 --result ok --card t_xxxxxxx \
    --param mode=AutoProxy --param code=200 --evidence runtime/mas_ondemand_log.jsonl

# 查（统一流）
$PY312 tools/mas_audit.py query --since 2026-09-28 --action 'dispatch.*' --limit 50
$PY312 tools/mas_audit.py query --since 7d --target 'account:*' --result refused,ok --json
$PY312 tools/mas_audit.py query --card t_d937f659 --limit 0 --json

# 查（桥接旧日志 + 看板；只读，不写任何旧文件）
$PY312 tools/mas_audit.py query --legacy --since 7d --limit 30
$PY312 tools/mas_audit.py query --legacy ledger,dispatch,board --action 'farm.*' --json

# 水位 / 轮转 / 保留 / 校验 / schema
$PY312 tools/mas_audit.py status --json
$PY312 tools/mas_audit.py rotate            &&  $PY312 tools/mas_audit.py rotate --apply
$PY312 tools/mas_audit.py prune             &&  $PY312 tools/mas_audit.py prune --apply
$PY312 tools/mas_audit.py validate
$PY312 tools/mas_audit.py schema --json
$PY312 tools/mas_audit.py --selftest
```

**查询语法**：`--action`/`--target`/`--actor`/`--result` 支持 `*` 通配与逗号列表（`dispatch.*,audit.*`）；
`--since/--until` 支持 `2026-09-28`｜`2026-09-28 09:30`｜`09:30`（今天）｜`30m|12h|7d`（相对，**口径 = 现在往回推**）；
坏时间参数**拒绝执行**（rc=2），不静默忽略。默认新→旧、`--limit 50`（`0`=全部）；`--oldest-first` 反序。

### 6.3 退出码 / 末行判据 / 环境变量

| 退出码 | 含义 |
|---|---|
| `0` | 成功（查询无命中也是 0；命中数看末行） |
| `1` | 仅 `query --json --strict-rc` 的「无命中」；`validate` 有坏行 |
| `2` | 拒绝（用法/schema 不合规/时间参数坏/锁超时） |
| `3` | `--selftest` 失败 |

末行判据（机检 grep，与 `SWITCH_GATE=` / `PREFLIGHT=` 同风格）：
`AUDIT=APPENDED|DRY-RUN|ERROR:<原因>`、`AUDIT_QUERY=<n>条 scanned=<行> legacy=<n>`、
`AUDIT_STATUS=OK|WARN|CRIT …`、`AUDIT_ROTATE=NOOP|DRY-RUN|ROTATED:<name>`、
`AUDIT_PRUNE=candidates=<n> kept=<m> apply=<bool>`、`AUDIT_VALIDATE=OK|BAD:<n> …`、`SELFTEST=OK|FAIL (n/n)`。

数据面读不到时（某旧日志缺失/看板库锁住）**不改退出码**，但在 JSON `unreadable[]`、`per_source[].note`
与 stderr 警告里显式可见（**fail-closed：不许把「读不到」当 0**）。

环境变量：`MAAORCH_AUDIT_DIR`（统一流目录，默认 `runtime/audit`）｜`MAAORCH_AUDIT_NO_ROTATE=1`（关自动轮转）｜
`MAAORCH_AUDIT_LEGACY_RUNTIME`（旧日志所在 runtime，自测/异机）｜`HERMES_KANBAN_DB`（看板库，桥接用）｜
`HERMES_KANBAN_TASK`（append 缺省卡号）。

### 6.4 输出示例（真实，摘自 `query_demo.txt`）

```
2026-09-28 10:13:24  info    tool:board                 board.heartbeat       card:t_73ff4db2  卡=t_73ff4db2 kanban
2026-09-28 07:33:57  ok      tool:legacy                dispatch.start        wake:明日方舟-官-2-4-47  卡=- run=550fc698-4ce0… ondemand_log
AUDIT_QUERY=12条 scanned=1 legacy=2395
```

---

## 7. 存储 / 轮转 / 保留（与存储策略卡呼应）

### 7.1 布局

```
runtime/audit/
  mas_audit.jsonl                 # live（唯一写入目标）
  mas_audit.lock                  # 跨进程单飞锁（陈旧 120s 可回收）
  archive/
    mas_audit-20261005-090000.jsonl.gz   # 轮转归档（gzip JSONL，可被 query/validate 直读）
```

### 7.2 阈值（与存储策略卡同口径）

| 参数 | 默认 | 依据 |
|---|---|---|
| 体积轮转 `max_mb` | **8 MiB** | 存储卡 §4.2「本机 runtime 无界、建议纳入同款巡检」；8 MiB 保证单文件可 `grep`/`less` 不卡 |
| 天数轮转 `max_days` | **7 天** | 与宿主机 04:00/19:00 重启窗口解耦；一周一份归档便于按周取证 |
| 归档保留 `retention_days` | **90 天** | 对齐存储卡 §4.1 对 `E:\AUTO-MAS\history` 的 90 天建议（同为「运行审计」性质） |
| 保底份数 `keep_min` | **3 份** | 防止误配 `--days 0` 把证据清空 |
| 参数上限 `MAX_PARAM_CHARS` | 2000 字符 | 单行 ≤ ~2 KB 目标；超长走截断+指纹 |

**轮转时机**：① `append` 时自动检查（可用 `MAAORCH_AUDIT_NO_ROTATE=1` 关闭）；② 手动 `rotate --apply`。
轮转 = live → `archive/*.jsonl.gz`（**先写归档、后删 live**，不丢行）；**保留期清理只由人执行**（`prune --apply`）。

### 7.3 稳态估算（算给你看）

- 事件速率：桥接实测 2,405 行 / 约 2 周（含看板事件）⇒ **~170 行/天**；单行 0.5–1.5 KB（看板 payload 偏大）。
- ⇒ **≈0.1–0.25 MB/天**（≈3–7 MB/月）。8 MiB 体积阈值 ≈ **1–3 个月**才自然触发一次；
  **7 天年龄阈值会先触发**：每份归档 ~0.7–1.7 MB，52 份/年 ≈ **35–80 MB/年毛量**；
  90 天保留 ≈ **13 份 ≈ 10–20 MB 稳态**。
- 对比存储卡结论（「日志侧不构成撑爆风险，但 `history` +1.8 GB/年、MAA 截图 +5.7 GB/年 无界」）：
  本流是**新增的第 10 个「有界」流**，量级比 history 小一个数量级，**不改变存储卡的红线判断**。

### 7.4 巡检接线（**已落地 2026-09-28**，卡 t_537fbff2）

- 只读取数：`python tools/mas_audit.py status --json`（<1s，无网络），**已并入**现有 30 分钟巡检
  （cron job `52b88b0ffca0` 第三步 `8)`，经 MCP 工具 `mas-observe.audit_waterline` 取数）；
  `AUDIT_STATUS=WARN` 的触发点 = 任一**统一流**文件或旧流文件 >10 MiB。
- 判据封装在 `tools/audit_waterline.py`（只读；OK/WARN/CRIT：`live>10MiB`⇒WARN、
  `live>25MiB` 或 归档缺失而 `live>8MiB`⇒CRIT、覆盖 ≥24h 且该转未转⇒WARN）。
  WARN/CRIT **只报告**；`rotate --apply` / `prune --apply` 永远人工。
- **已落地**：`tools/storage_waterline.py` 覆盖范围加入 `runtime/audit/`（live + archive：体积/最老文件天数/趋势）。
- 证据与回滚：`docs/audits/2026-09-28_audit_patrol/README.md`；离线演练 `tests/audit_patrol_drill.py`。

---

## 8. 迁移建议：**读时桥接**（已实现）+ **新写走统一入口**（待接线卡）

### 8.1 桥接映射（只读，`query --legacy`；实现 = `tools/mas_audit.py` 适配器）

| 源 | 形态（实测几代） | → action | → target | → result | 备注 |
|---|---|---|---|---|---|
| `mas_ledger` | `kind` 域（run_profile/probe_farm/farm_orchestrator/stage_fix/watch/activity_switch…） | 按 kind 映射到 `profile.apply`/`farm.probe`/`farm.run`/`stage.fix`/`run.watch`…；未知 kind ⇒ `ledger.<kind>` | `run:<run_id>` 或 `-` | `ok`↓`fail` | `detail`（JSON 串）解析后进 `params.detail`，`conclusion/verdict` 提取为 `reason` |
| `mas_dispatch_log` ① 班次发车 | `{queue, queue_id, resp{code,message}, taskId}` | `dispatch.start` | `queue:<名>` | `ok`↓`fail` | `reason` = resp.message；`note`/`source` 留 params |
| `mas_dispatch_log` ④ 手工/卡片发车 | `{script, dir, http, resp, source:"t_xxx gap_fill_q4"}` | `dispatch.start` | `dir:<MAA_x>` | `http`/`resp.code==200` 判定 | **从 `source` 抽卡号**进 `card`（`t_564593ae`） |
| `mas_ondemand_log` ② 调用行 | `{tool: dispatch_queue\|dispatch_script\|stop_task\|ondemand_preflight, kind: queue\|script\|缺, apply, verdict}` | `dispatch.queue`/`.script`/`.stop`/`.preflight` | `queue:`/`script:`/`task:` | `refused`（verdict 含 refus/unknown）→ `info`（干跑）→ `ok`/`fail` | `verdict` 进 `reason`；`dry_run` 进 params |
| `mas_dispatch/ondemand` ③ 事件行 | `{kind: dispatch_ok\|dispatch_refused\|stop_ok\|temp_queue_created\|release…}` | `dispatch.start`/`.refuse`/`.stop`/`.temp_queue_created` | `<target_kind>:<target>`（缺省 `task:`） | kind+ok 判定 | `mode/code/taskId/apply/force/preflight/resources` 进 params，`taskId` 进 `run_id` |
| `mas_run_profile_log` | `{event: plan_dry_run\|plan_refused\|…, script, accounts, refusals}` | `profile.<event>`（有词表映射） | `script:<脚本>` | `refused`/`info`（干跑）/`ok` | `refusals` 进 params |
| `mas_probe_farm_log` | `{event, account, stage, before, after, rc, verdict}` | `farm.done`/`stage.ledger_update`/`farm.refuse` | `account:<号>` | `rc==0`⇒`ok` | `before/after`（台账变更）进 params |
| `mas_farm_orchestrator_log` | `{run_id, event, rc, conclusion}` | `farm.<event>` | `run:<run_id>` | `rc==0`⇒`ok`，`reason`=conclusion | |
| `mas_plan_builder_log` | `{event, account, script, stage_plan, mode, json, md}` | `stage.<event>` | `account:<号>` | `ok` | 计划产物路径进 params（可查「哪号哪关」） |
| `mas_config_snapshot_log` | `{action: snapshot\|restore, ok, snapshot, files…}` | `config.<action>` | `snapshot:<ts>` | `ok`↓`fail` | |
| `mas_patch_guard_log` | `{kind: reapply, patch, file, backup, sha256, fails}` | `patch.<kind>` | `file:<路径>` | `ok`↓`fail` | |
| 看板库 `kanban.db` | `task_events`（kind=created/claimed/spawned/heartbeat/completed/crashed/blocked/comment…）+ `task_comments` | `board.<kind>`（如 `board.completed`、`board.comment`） | `card:<task_id>` | `completed`⇒`ok`，`crashed/timed_out`⇒`fail`，`blocked`⇒`refused`，其余`info` | **只读打开**（`mode=ro`），近 2000 事件+全部注释；锁住/缺失 ⇒ `unreadable[]` 明示，不当 0 |

统一口径（所有桥接行强制）：`actor=tool:legacy`；**时间戳归一**为空格形态（ISO-T/缺秒/毫秒 → `YYYY-MM-DD HH:MM:SS`，
认不出的原样保留并计数 `legacy_ts_bad`）；`_origin` = 来源标记（`legacy:<文件名>` / `unified:<文件名>`）；
认不出的行**跳过并计数**（`legacy_skipped`），**不编语义**。

### 8.2 新写一律走统一入口：**现有工具 patch 建议**（本卡不实施，接线请另开卡）

**接入 = 在既有审计函数里加一次调用**（幂等、可失败不影响主流程用 `safe_append`）：

```python
# tools/mas_audit.py 提供（tools/ 在同一目录时直接 import）：
from mas_audit import append, safe_append

ok, res = safe_append(                        # 返回 (True, 落盘行) 或 (False, {"error": ...})
    actor="tool:mas_ondemand", action="dispatch.start", target="account:帝国猛虎",
    result="ok", reason=rec.get("reason", ""),
    params={"mode": rec["mode"], "code": rec["code"], "taskId": rec["taskId"]},
    evidence=["runtime/mas_ondemand_log.jsonl"],
    card=os.environ.get("HERMES_KANBAN_TASK", ""), run_id=rec.get("taskId", ""))

# 严格版：append(...) 校验不过就抛 AuditError（参数错就该炸）；safe_append 永不抛、只返回 False。
# 自动轮转：append 默认会顺带检查轮转（设 MAAORCH_AUDIT_NO_ROTATE=1 或 do_rotate=False 可关）。
```

逐工具建议（**文件:行 = 2026-09-28 实况**；~~均只给建议，未改~~）→ **已完成接线（2026-09-28，卡 `t_a190ca4b`）**：
下方 11 行的落点均已按本节写法落地（旁路双写、失败静默），落点列已**逐行标注「已接 2026-09-28」**；
验收证据见 `docs/audits/2026-09-28_audit_wire/README.md`，复核（run 2，同日 11:2x–11:3x）见
`docs/audits/2026-09-28_audit_wire/recheck2/`；隔离与映射细节见 §8.5。

| 工具 | 落点 | 建议写法 |
|---|---|---|
| `tools/mas_run_profile.py` | `audit()` `:182`；`ledger_append()` `:191` **已接 2026-09-28** | 台账 `rec` 同源映射：`action="profile."+str(rec.get("event","apply"))`、`target="script:"+rec["script"]`、`result="refused" if rec.get("ok") is False else "ok"`、`run_id=rec["run_id"]`、`evidence=["runtime/mas_ledger.jsonl"]` |
| `tools/mas_probe_farm.py` | `ledger_append()` `:123`、`audit()` `:128` **已接 2026-09-28** | `action="farm.probe"`、`target="account:<号>"`、`params={stage,verdict,probe_rc,farm_rc}`、`run_id` 同源 |
| `tools/mas_farm_orchestrator.py` | `audit()` `:480`、`deps_ledger_append()` `:412` **已接 2026-09-28** | `action="farm."+event`、`target="run:"+run_id`、`reason=conclusion`、`evidence=[报告 md 路径]`（把 `farm_run_*.md` 串进证据链） |
| `tools/mas_plan_builder.py` | `audit()` `:170` **已接 2026-09-28** | `action="stage.plan"`、`target="account:<号>"`、`params={mode,stage_plan,json,md}` |
| `tools/mas_dispatch.py` | `audit()` `:273` **已接 2026-09-28** | kind→action 映射同 §8.1 ③；`target=k+":"+t`；`reason/detail` 透传；**此处写入的即「真动作」权威行** |
| `tools/mas_ondemand.py` | `audit()` `:319`（两处 `_append_jsonl` 之后） **已接 2026-09-28** | 同 ③/② 映射；`source/src` 保留 |
| `tools/mas_config_snapshot.py` | `audit()` `:136` **已接 2026-09-28** | `action="config."+rec["action"]`、`target="snapshot:"+rec["snapshot"]` |
| `tools/mas_patch_guard.py` | `_ledger()` `:520` **已接 2026-09-28** | `action="patch."+rec["kind"]`、`target="file:"+rec["file"]`、`params={patch,sha256,fails}` |
| `tools/mas_watch.py` | `log_event()` `:42` **已接 2026-09-28** | `action="run.watch"`、`target="-"`、`params=kw`（watch 事件字段杂，params 里给 kind 子类） |
| `tools/mas_stage_fix.py` | `ledger_append()` `:318` **已接 2026-09-28** | `action="stage.fix"`、`target="account:<号>"` |
| `tools/activity_switch.py` | `ledger_append()` `:189` **已接 2026-09-28** | `action="account.activity_switch"` |
| MCP 面（`mas_observe_mcp` / `mas_dispatch_mcp` / `mas_ondemand_mcp` / `mas_triage_mcp`） | 各自 `audit()`/转发函数 | **MCP 调 CLI 工具 ⇒ 自动继承**；仅 MCP 独有动作（如 `triage_error`、`mas_client_state`）用 `actor="worker:<profile>"` 单独 append；工具面改动需新会话/网关重启才可见 |

**接线验收口径（写进后续卡）**：跑 1 次对应工具 ⇒ `mas_audit.py query --since 1h --action '<域>.*'` 能看到行；
`validate` 仍 `AUDIT_VALIDATE=OK`；旧流仍照写（**双写期**，见 §8.4）。

> **首个真实接入者（场外，非本卡改动）**：`tools/stage_capability.py` 已按本节方向落地（`safe_append(actor="tool:stage_capability",
> action="stage.rebuild", …)`，写盘后旁路记录、失败不打断主流程）；2026-09-28 10:23:21 产出统一流**首条工具级事件**——
> 即「§8.2 的写法在真工具上跑通」的现成样例，接线卡可直接照它抄。

### 8.3 为什么不做物理迁移（判读）

1. 旧写者**仍在跑**（发车/巡检/夜班），物理迁移要么改它们（= 接线卡）、要么形成两版历史，收益不抵风险。
2. 看板库属 Hermes 内核（`kanban.db`），**只读桥接**是唯一安全的隔离方式；搬进本仓库反而要同步写。
3. 「读时桥接」已把「统一查询」这个**核心痛点**解决（0 跳过），历史不需要搬家；归档策略只对**新写**生效，
   旧流按各自保留策略（存储卡 §4）走。

### 8.4 双写期判读口径（重要）

- 接线后**同一事件会在两处出现**（旧流 + 统一流），且 `--legacy` 合并查询会**同时**返回两份（实测：`stop_ok` 在
  `mas_dispatch_log` 与 `mas_ondemand_log` 各一条，`_origin` 已标出来源）。
- 判读规则：**同一时间窗内不要对两处计数相加**；按 `_origin` 取一处即可（建议以 `unified:` 为准，`legacy:` 只读历史）。
- 桥接行统一带 `extra.legacy` / `extra.shape`（`shift-send`/`manual-send`/`ondemand-call`/`event`），
  一眼能看出这行是「哪一代形态」来的。

---

### 8.5 接线落地细节（2026-09-28，卡 `t_a190ca4b`）

1. **旁路双写**：各工具旧写出口（`audit()` / `ledger_append()` / `_ledger()` / `log_event()`）末尾加钩子，
   旧流写出行为**一行未改**；统一事件由 `mas_audit.safe_append(...)` 追加，**永不抛**（外加各工具
   `_unified_guard` 兜底）⇒ 审计失败绝不打断主流程。
2. **卡片透传**：所有新行都带 `card=os.environ.get("HERMES_KANBAN_TASK","")`。
3. **台账分派**：`runtime/mas_ledger.jsonl` 是多工具共写者，统一流按 `kind` 分派
   （`mas_run_profile.py::_LEDGER_ACTOR_ACTION` 精确表 → `_ledger_family_of()` 家族前缀 → `tool:ledger`/`ledger.<kind>`），
   与桥接口径 `mas_audit._ad_ledger` 的 amap 同表（单测逐 kind 对照：`map_unit.py`，`MAP_UNIT=OK bad=0`）。
   精确表已含 P1-2 新 kind `run_profile_refused`→`profile.refuse`。
4. **自测/异机隔离**：新增 `mas_audit.stream_for_runtime(runtime_dir)` —— 工具 runtime 被
   `MAAORCH_*_RUNTIME` 重定向时，统一流跟随到 `<runtime>/audit/mas_audit.jsonl`，**自测不再灌生产流**
   （实测 1015→1015 / 1028→1028 / 1032→1032）。`MAAORCH_AUDIT_DIR` 优先级更高（显式指定即用）。
   例外：`mas_patch_guard --selftest` 按设计对宿主 `E:\AUTO-MAS\_patchguard_selftest\` 真写并落账。
5. **接线窗口内的自测行**（约 10:47–11:06，隔离补丁生效前）已按红线**保留不删**，并补一条更正事件
   （`11:19:55 worker:mas manual.note`）；读生产事件按 `evidence` 路径过滤 `%TEMP%\mas_*_selftest_*`。
6. **验收证据**：`docs/audits/2026-09-28_audit_wire/`（总表 README、隔离 harness + 产物 `iso/`、
   映射单测、9 工具 selftest + 回归输出、6 域 query 输出）；复核（run 2，P1-2 改动之后重跑）见 `recheck2/`
   —— `selftests2` / `harness2` / `map_unit2` / `regression2` / `watch_real` / `run_profile_real` / `query2` / `validate2`：
   全绿且生产流零污染（仅 patch_guard 沙箱 2 行 + 两次真写路径各 1 行）。

---

## 9. 清理命令清单（**本次未执行**；人工拍板后照抄）

> 通用前提（引用存储卡 §6 通用前提）：避开发车时段（早班 05:00–06:30、晚班 19:30–22:30）；
> 一律**先看清单再删**（工具默认就是 dry-run）。

```bash
# ① 看统一流水位（只读）
$PY312 tools/mas_audit.py status

# ② 轮转（需要时）：先看判定，再执行
$PY312 tools/mas_audit.py rotate             # 只报
$PY312 tools/mas_audit.py rotate --apply     # 归档 → runtime/audit/archive/*.jsonl.gz

# ③ 保留期清理（默认 90 天、至少留最新 3 份）：先列候选，再删
$PY312 tools/mas_audit.py prune              # dry-run：只列候选
$PY312 tools/mas_audit.py prune --apply      # 人确认后执行（只删 archive/ 内归档）
```

PowerShell 手工等价（不进工具时的兜底；**同样先 -WhatIf/先看清单**）：

```powershell
# 归档 > 90 天：先看清单
Get-ChildItem 'D:\1-2\MAAOrch\runtime\audit\archive' -File |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-90) } |
  Select-Object Name, @{n='KB';e={[math]::Round($_.Length/1KB)}}, LastWriteTime
# 人工确认后（保留最新 3 份）：
#   Remove-Item <上面列出的文件>
```

**红线**：① 不删 `runtime/audit/mas_audit.jsonl`（live，正在写）；② 不删/不改 9 套旧日志与看板库
（仍在写，且 `--legacy` 依赖它们读历史）；③ `archive/` 之外的任何东西都不属本工具职责；
④ 打码/截断过的记录不得「补全」——要补就再写一条更正事件。

---

## 10. 验收与复现

- **自检**：`tools/mas_audit.py --selftest` ⇒ `SELFTEST=OK (19/19)`（证据 `selftest.txt`；含拒绝路径、脱敏、轮转、保留、桥接 4 形态、时间归一）。
- **实跑证据**（全部在本卡执行日留档）：
  `docs/audits/2026-09-28_mas_audit_tool/{selftest.txt, cli_demo.txt, status.txt, status.json, query_demo.txt, schema.json, validate.txt}`。
- **复现 5 条命令**：
  1. `$PY312 tools/mas_audit.py --selftest`
  2. `$PY312 tools/mas_audit.py status --json`
  3. `$PY312 tools/mas_audit.py query --legacy --limit 0 --json`（看 `counts.legacy_skipped=0`）
  4. `$PY312 tools/mas_audit.py validate`（`AUDIT_VALIDATE=OK`）
  5. `$PY312 tools/mas_audit.py schema --json`
- **已知边界（诚实标注）**：
  1. 看板桥接只取**近 2000 事件 + 全部注释**（`LIMIT`），不是全库 7,400+；要全量历史再调大（`_board_rows(limit=…)`）。
  2. 桥接是**读时**转换（无物化），大时间窗查询会比只查统一流慢（实测 2,405 行 <1s；看板库 >10 万行时需评估）。
  3. `--limit 0`（全量）在 `--legacy` 下会把 2,000+ 行一次性吐出，机器读请配 `--json` 并自行裁剪字段。
  4. 自动轮转默认开（`append` 内），要严格「零自动写」的场合设 `MAAORCH_AUDIT_NO_ROTATE=1`。

---

## 11. 附录

### 附录 A：桥接「认不出的行」处理口径

| 情况 | 处理 | 可见性 |
|---|---|---|
| 旧日志坏 JSON 行 | 跳过 | `legacy_skipped` + `per_source.scanned/rows` 差值 |
| 旧日志字段形态未收录（如未来第 5 代） | 跳过（`return None`） | 同上；**不编语义** |
| 时间戳认不出（如 `2026-09-24T20:4x`） | **保留原样**写入桥接行 | `legacy_ts_bad` 计数 |
| 看板库缺失/被锁 | 该源 0 行 | `unreadable[]` + stderr 警告 |
| 目标旧文件不存在 | 该源 0 行并注明 | `per_source[].note`（fail-closed 明示） |

### 附录 B：机读 schema

`tools/mas_audit.py schema --json` 输出 draft-07 JSON Schema（`title: maaorch.audit.v1`，
`required: [v, ts, actor, action, target, result, reason]`，`additionalProperties: false`）。
快照：`docs/audits/2026-09-28_mas_audit_tool/schema.json`。自检里的「schema 与样例自洽」用例保证
样例不会漂出 schema。

### 附录 C：后续卡建议（**不在本卡范围**）

1. **接线卡**：「现有工具接入统一审计」（按 §8.2 表逐个落地 + 接线验收口径 + 双写期观测）。
2. **巡检卡**：把 `mas_audit.py status`（或 `validate`）并入 30 分钟只读巡检的收尾步骤；
   `storage_waterline.py` 覆盖 `runtime/audit/`。
3. **保留策略扩面**：老流（`mas_ledger` 等）是否也做轮转——**需先有接线（双写）**，否则会切断唯一历史。

*生成：2026-09-28 10:2x CST · mas（Hermes / 卡 t_d937f659）· 本卡零改现有工具、零删历史、零触网主机。*
