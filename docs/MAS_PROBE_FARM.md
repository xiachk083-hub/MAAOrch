# mas_probe_farm.py —— 先试探后开刷（unknown 先跑 1 次，成功才正式刷 N 次）

> 解决「**不知道这个号能不能刷这个关**」：查能力台账 → （unknown / 修好重试）发一次**最小试探** →
> 判定确实能刷 → 才按请求正式刷 N 次；失败/无定论就停手、归类、留证据。
>
> 文件：`tools/mas_probe_farm.py`（默认 **dry-run**；`--apply` 才真跑）
> 依赖：`tools/stage_capability.py`（能力台账）、`tools/mas_run_profile.py`（发车/读回/还原骨架）、
> `tools/mas_error_triage.py`（失败归类）

---

## 1. 三态决策（严格按台账）

台账 = `stage_capability.can_farm(account, stage)` → `success | failed | unknown`（30 天只读扫描得出）：

| 台账 | 行为 | 例外 |
|---|---|---|
| `success` | **直刷**：跳过试探，直接按 `--times` 正式发车 | `--force-probe` 强制先试探 |
| `failed` | **默认拒绝发车**（rc=2，零写入零发车） | `--retry-after-fix "<修复说明>"` 放行 ⇒ 进试探流程 |
| `unknown` | **进试探流程**（不猜） | —— |

`--retry-after-fix` 的说明会写进审计与台账流水（`runtime/mas_ledger.jsonl`，`kind=probe_farm`），
所以「为什么这次放行一个台账说刷不了的号」永远有据可查。

## 2. 试探（probe）：一次最小任务

用 `mas_run_profile.py` 的骨架发车（备份 → 临时计划表 → StageMode 指向 → preflight → dispatch/start →
L1 读回 → wait → 还原 → audit 都是它的），profile 收窄到最小：

* `stage_plan=[<关>]`（Stage_1..3 置 `-`）
* `series="1"`（**连战倍数**，不是总次数；试探恒 1）
* `medicine={"use": false, "count": <--medicine>}`（默认 **0 = 不吃药**）
* `activity_priority.enabled=false`（不启用活动关优先）
* `keep_remain_sanity_task` **显式声明**：默认 `false`（＝不挂剩余理智条目，`Stage_Remain="-"`），
  `--remain-sanity` 则 `true`（复制当日生效值）
* `restore_after="timeout:<--timeout>s"`（默认 600s；到点走 `dispatch/stop` ⇒ 解锁 + Temp 快照还原）

> 说明：profile 的 `target_drop.enabled=false` 与 `use_expiring=false` 两个键**故意不写**——
> 前者会让 run_profile 打「本次不写该字段」的警告，后者会打「不会写，只读回记录」的警告；
> 不写＝走默认（不启用），读回断言里也没有这两项，语义等价而日志干净。

## 3. 判定：三条证据 + 三态结论

试探后同时看三处（缺一不可的**同口径**）：

1. **新增 history 尝试**：`E:\AUTO-MAS\history\<date>\<account>/*.json` 的 `maa_result`（试探窗口内**文件名差集**，
   不信主机/本地钟差）；同批 `.log` 取判据行（口径复用 `stage_capability.PS_PATTERN` 与 `classify_attempt`）。
2. **该关是否出现**：`drop_statistics` 键 / 副本日志 `<关> 掉落统计` 块 / 剿灭链完成（剿灭不在本工具范围，见 §7）。
3. **`asst.log` 锚点**（`<MAA 目录>\debug\asst.log`）：`append_task Fight {...}` 的 stage/series/medicine、
   `Fight@StartButton1/2`、`Fight@PRTS`、`StageDropsTaskPlugin`、`analyze_sanity_remain/cost`、`FightMissionFailed` 等。

结论：

| 结论 | kind | 后续 |
|---|---|---|
| **success** | `stage_farmed`（掉落命中）/ `annihilation_chain_done` | 记台账 `success`（跑 `stage_capability --update` 并**读回核对**）→ 按 `--times` 正式刷（或 `--probe-only` 收工） |
| **failed**（能力类） | `stage_not_open` / `stage_unavailable` / `proxy_mismatch` | **立即停止**，`mas_error_triage.py` 归类，输出证据；台账更新；**不做第二次尝试** |
| **inconclusive**（环境/状态类） | `fight_error_net`（掉线重连）/ `insufficient_sanity`（理智不够）/ `no_attempt`（没轮到）/ `selected_no_result` / `no_evidence` / `evidence_scan_failed` | 立即停止 + 归类 + 照实报告；**不写能力台账**（不冒充结论） |

判定细节与 `stage_capability` 的时序口径一致：掉线标记**在**「任务出错: 理智作战」之前 ⇒ `fight_error_net`（环境），
之后 ⇒ 关卡/理智类失败（能力）；`not_open` 来自 `GetFightStage` 的「当日不开放」行；代理失误=`代理指挥失误`。

## 4. 正式刷（farm）

只有两条路径会走到这里：台账 `success` 直刷，或试探判定 `success`。

* `--times N` 直译 `SeriesNumb=N`（`0..6` / `-1`，域同 `mas_run_profile.SERIES_OK`）；
  **注意这是「连战倍数」，不是总次数上限**——AUTO-MAS 没有「总次数」字段（`runtime/...` 里一样）。
* `restore_after="run_end"`；`--farm-max-wait`（默认 10800s）作为 `mas_run_profile --max-wait-seconds`。
* 正式刷阶段失败 ⇒ rc=5（**试探结论已记录**，正式刷没跑完）。

## 5. 接口 / 退出码 / 单飞锁

```
python tools/mas_probe_farm.py --account <号> --stage <关> --times <N> \
    [--medicine N] [--force-probe] [--retry-after-fix "说明"] \
    [--dry-run|--apply] [--timeout 600] [--probe-only] [--json]
```

| 选项 | 说明 |
|---|---|
| `--account/--stage` | 必填（`--list/--status/--selftest` 除外） |
| `--times N` | 正式刷的 `SeriesNumb`，域 `0..6/-1`，默认 1 |
| `--medicine N` | 试探与正式刷都用；默认 0（不吃药） |
| `--force-probe` | 台账 `success` 也先试探一次 |
| `--retry-after-fix 说明` | 台账 `failed` 的放行闸（必须带说明，记流水） |
| `--remain-sanity` | 保留剩余理智条目（默认关闭，＝`Stage_Remain="-"`） |
| `--timeout N` | 试探等待上限秒（默认 600） |
| `--probe-only` | 试探成功也**不**进入正式刷（真跑演示/试水用） |
| `--script NAME` | 缺省按账号自动定位脚本；多命中则拒绝（要求显式） |
| `--disable-others/--all-accounts` | 透传给 `mas_run_profile`（多号脚本时才用；**默认不碰其它账号**） |
| `--write-mode plan/fixed` | 透传（默认 plan＝走临时计划表） |
| `--farm-max-wait N` | 正式刷等待上限秒（默认 10800） |
| `--no-ledger-update` | 不跑 `stage_capability --update`（只读演习用） |
| `--capability-json PATH` | 台账报告路径（默认 `runtime/reports/stage_capability.json`） |
| `--dry-run`（默认）/ `--apply` | **默认干跑**；`--apply` 才发车 |
| `--list [unknown\|failed\|success\|all]` | 只读：列台账行 + 账号→脚本归属 |
| `--status` | 只读：本工具最近 5 次记录 + 锁状态 |
| `--selftest` | 23 条离线用例（判定矩阵/计划回滚/锁/取值域） |
| `--json` | 末尾打一行 JSON 汇总（与 `mas_run_profile` 同风味） |

退出码：`0` 期望路径完成 ｜ `2` 拒绝/发车前失败（零残留） ｜ `3` 试探未证明可刷（failed/inconclusive，**已还原**）
｜ `4` **有残留**（还原失败，需人工） ｜ `5` 试探成功但正式刷阶段失败。

单飞锁：`runtime/mas_probe_farm.lock`（>6h 视为过期抢占）；`MAAORCH_PROBE_FARM_NO_GO` /
`MAAORCH_DISPATCH_NO_GO` 任一置位 ⇒ `--apply` 一票否决。

## 6. 安全边界（硬约束）

* 默认 dry-run；真跑只做**一次最小试探**（不重试、不连环试探）。
* **不碰其它账号**：只解析目标账号所在的脚本；多号脚本要求显式 `--disable-others`（那会动别人的 Status，慎用）。
* 不改与本关无关的字段：临时计划表＝「当日生效 7 键全量复制 + 本次差分」；Task 键原样；还原靠 run_profile 的 try/finally。
* 不重启侧车（AUTO-MAS/MAA 进程都不碰；模拟器由 AUTO-MAS 自己按正常流程启停）。
* 失败**绝不自作主张重试**；一次就是一次。
* 还原收尾复核：查本次发起的 run_profile run 是否 `restored` + 临时计划表是否删净（只认自己发起的 run，不误报别人）。

## 7. 已知边界

* **剿灭不支持**：`--stage Annihilation`/`剿灭` 直接拒绝——剿灭不走 7 键里的关卡字段（`Info.Annihilation`），
  本工具不碰该字段。要试剿灭请走 `mas_run_profile` 的对应路线。
* `Series` 语义＝连战倍数（见 §4）。
* 判定依赖**主机 history 的文件名差集**；同秒内两次写入理论上会漏（实际不会：文件名含时刻+序号）。
* `--times` 只影响正式刷；试探恒 `Series=1`。
* 真相仍以台账为准：本工具只在**试探成功**时补写 `success`，失败/无定论时只归类留证；能力类失败才落 `failed`。

## 8. 自测

```
python tools/mas_probe_farm.py --selftest     # SELFTEST=OK (23/23)
```

覆盖：三态决策（含 `--force-probe`/`--retry-after-fix`/报告缺失）、两种 profile 的关键字段、判定矩阵
（掉落命中/副本日志命中/未开放/选中没开打/代理失误/掉线/理智不足/无新增/剿灭链完成）、asst 锚点归总与时窗过滤、
`--times` 取值域、日志行前缀剥除、锁文件语义。

## 9. 实测记录

见 §9.1（2026-09-28 真试探）。

### 9.1 2026-09-28 真试探：LEUNG × LS-5（MAA_5 / 明日方舟-官-19）—— 结论 `failed/stage_not_open`

一次真最小试探（`--apply --probe-only`），六步链全走通：

| 步 | 结果 | 证据 |
|---|---|---|
| 台账判定 | `unknown`（报告 built_at 06:19:38；该号×关不在 30 天行里，在全池里 `tried=false`） | `docs/audits/…live.summary.json` → `ledger` |
| 发车 | dispatch/start，task_uid `e863a52c-01ae-4a3b-b430-084ee6c7894b`，run_id `rp_20260928_084243_--19` | run_profile `probe` 段；`probe_run.log` |
| 读回校验 | **L1 ok**（19 次轮询满足）：payload `{MedicineNumb:0, SeriesNumb:"1", Stage:"LS-5", Stage_1..3:"-", Stage_Remain:"-"}`；L2 首条 append_task 是账号自身剿灭 `LungmenOutskirts@Annihilation`（§6-8 预期内） | `…live.summary.json` → `probe.summary.readback/l2` |
| 结果判定 | **failed / stage_not_open**：`GetFightStage: from ["LS-5"], selected null` + `关卡计划内没有当日开放的关卡` + `理智作战: 理智作战 任务跳过`（2 条新增尝试：一条剿灭链 `Success!`、一条理智链被跳过） | `…live.evidence.json`、`…live.attempt_lines.txt` |
| 台账更新 | `stage_capability --update --update-days 1` → 该号×关 `unknown ⇒ failed`（`not_open_n=1`，证据行落库，report built_at 08:58:21） | `…live.summary.json` → `ledger_update` |
| 还原证据 | run_profile `restore.status=restored`（reason `wait_timeout`，608s）；收尾复核 `open_runs=[] temp_plans=[]`；**REST 独立复核**：7 键与试探前逐字段一致（`Stage=SR-7 Stage_1=1-7 Stage_Remain=1-7 SeriesNumb="0"`）、计划表只剩 5 张真表、无 `临时-*` 残留、锁已释放 | `…live.pre.json` vs `…live.post.json` |

失败归类（`mas_error_triage.py`）：无任务链级错误（`chains_with_error=[]`）；基建链有 3 条 `SubTaskError`
（`InfrastOperListTabSkillUnClicked`，warn/信息性，与本次试探无关）。

**教训（重要）**：`stage_not_open` 对**日轮换/活动关**要读成「**今天**打不了」，不是「这号不行」——
LS-5 在 09-07（周一）有成功记录、今天（周一）却被 MAA 判为不在当日开放列表（MAA 的
`StageActivityV2.json` 缓存 + 本地周表口径，`NotModified` 于 09-21）。挑真试探目标时先反查
「**今天有成功记录**的关」（`stage_capability.json` 的 `rows[].last_success == 今天`），目前 30 天窗口里
恒可用的只有 **1-7**（30/30 天）与 **Annihilation**（剿灭另有专用字段，本工具拒绝）。挑 unknown 号×关时
优先 `1-7`（成本 6 理智）或当日有成功记录的关，别拿轮换关去试。

失败后的处理：**按约束不做第二次尝试**（`失败绝不自作主张重试`）。要复核本行结论，等 LS-5 再次开放时
用 `--force-probe` 或 `--retry-after-fix "LS-5 已开放"` 再试一次即可（工具会按新证据重算行）。
