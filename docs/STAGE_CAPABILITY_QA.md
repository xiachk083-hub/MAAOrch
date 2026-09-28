# 能力台账数据质量抽查：`stage_capability.json` 的 success/failed/unknown 判定是否可信

> 抽查日期：2026-09-28 ｜ 执行：mas（Hermes）｜ 卡片：`t_84711bab`（创建者 = dsh-lead）
> 约束：**只读**。本次未改动 `runtime/reports/stage_capability.json`、`runtime/state/stage_capability_state.json`、`tools/stage_capability.py` 及任何台账/代码；修订建议见 §5，交 lead 决策。
> 全部脚本与原始证据：`docs/audits/2026-09-28_stage_capability_qa/`

---

## 0. 结论摘要（TL;DR）

- **抽样**：14 个账号 × 39 对（账号×关卡）= 28 success / 8 failed / 3 unknown。样本在 61 账号 / 288 行全池上做「失败签名分层 + 固定 seed(20260928)」随机抽取（failed 只有 25 行、unknown 只有 6 行，均匀随机必然欠采样稀缺类，故按签名分层——理由与清单见 `sample_design.txt`/`sample2.json`）。
- **逐对回到原始证据**：从主机 `E:\AUTO-MAS\history\<日期>\<账号>\` 拉了 **133 次尝试的原始 JSON（`maa_result` + `drop_statistics`）+ 完整 gui.log 副本**（`raw/batch_01..05.txt`，552 KB），另对 **20 个关键尝试**单独拉了**未过滤的上下文窗口**（22 段：`context/windows.txt` 8 + `windows2.txt` 10 + `ranges.txt` 3 + `v_full.txt` 3；其中 v 09-07 16:54 是 451 行全文）。
- **独立复核与台账 39/39 完全一致（100%）**：
  - **success 28/28**：每个成功行都有原始 `drop_statistics` 命中（或剿灭链完成），无一例空口成功；
  - **failed 8/8**：全部是「关卡级」失败（静默链错 或 `selected null`），**没有**环境型失败被误标为 `failed`；
  - **unknown 3/3**：包括 1 例大量掉线/超时的号——台账正确地**没有**把它标成 failed（环境失败 ≠ 关卡不可刷）。
- **两类易错点专项**：
  - **① `maa_result=Success!` 但该次没有该关掉落 ⇒ 不能算 success**：抽到 **11 次**此类尝试，**一次都没被当成功证据用**（台账只认 `drop_statistics` 键 / 日志掉落块 / 剿灭链完成）；4 个 unknown 行（`v/LS-6`、`用户_173920298/LS-6`、`寻找勇者的大魔王/LS-6`、`摸摸摸摸鱼/LS-6`）正是这类"跑完没打"的号，判 unknown 而非 success ✅。
  - **② 失败但其实"客户端未就绪"（环境）⇒ 不该标 failed**：25 个 failed 行**全量签名审计** + 8 对原始复核，**未发现**环境型混入（唯一 1 例疑似被原始时序排除，见 §3.2）；环境失败在台账里以 `unknown + impact="env" + failure_types=[fight_error_net]` 表达（如 `河清灯_别寄圆通/CA-5`）。**术语差异 1 处**：台账没有 `failed(env)` 这个状态名 → 见建议 R1。
- **附带发现**（不影响精度结论，影响后续消费/演进）：R2 `stage_not_open` 的读法（含轮换表实测）、R3 选区行 ≠ 实际开打关（v 实证：`selected LS-6`，实际打的是 `wk_kc_5`/LS-5）、R4 EV_CAP 截断实测 0.2%、以及 json↔log 交叉核对**零真实错配**、成功行新鲜度提醒。

---

## 1. 方法（可复现）

### 1.1 抽样设计（确定性、可重放）
脚本 `qa1_sample.py`（分布）→ `qa3_manifest.py`（分层抽样 + 取证清单）；seed=`20260928`。

- failed 行按失败签名分层（仅 `stage_unavailable` / 仅 `stage_not_open` / 混合），每层随机 2–3 行；
- unknown 行：1 个「net 形态」+ 2 个「纯未打成」形态（全取）；
- success：随机补 3 个账号；
- 每个入样账号 2–3 对（先 failed/unknown，再补 success）。
- 结果 **14 账号 / 39 对**（高于卡面下界 10 账号：为把三类都覆盖才扩到 14；`酸酸酸酸` 全池只有 1 行，故只贡献 1 对）。

### 1.2 取证（只读，全部落在 docs/audits）
| 步骤 | 脚本 | 产出 |
|---|---|---|
| 远端 history 侦察 | `qa2_recon.py` | `recon.txt` |
| 原始证据（json 全文 + log 判据过滤行） | `qa4_fetch.py` | `raw/batch_01..05.txt`（133 条，552 KB） |
| 独立复核 / 终表 | `qa6_analyze.py`、`qa11_final.py` | `verify_table.md`、`verify_evidence.md`、`verify_final.{json,md}` |
| 关键尝试未过滤窗口 | `qa8_context.py`、`qa8b_context2.py`、`qa8c_ranges.py`、`qa8d_fullv.py` | `context/windows.txt`(8)、`windows2.txt`(10)、`ranges.txt`(3)、`v_full.txt`(3) |
| 全量 failed/unknown 签名审计 | `qa10_signature.py` | `signature_audit.txt` |
| 文档数字自查/明细 | `qa12..qa17_*.py` | 本文件所有数字均可由它们复算 |

### 1.3 独立判定规则（不引用台账实现）
- **success**：任一尝试的原始 JSON `drop_statistics` 含该关，或原生日志出现 `<关> 掉落统计:` 块（剿灭额外允许 `完成任务: 剿灭作战`）。
- **failed(env)**：该关 Fight 链报错、且同次尝试存在掉线/重连类环境标记 ⇒ 不代表关卡不可刷。
- **failed**：该关 Fight 链报错（无环境标记）或 `GetFightStage … selected null`。
- **unknown**：其余。
- 台账期望映射：success→success；failed→failed；failed(env)→台账**不得**是 failed（应为 unknown+env）。

### 1.4 远端取证的 2 个坑（已解，供后人）
1. `powershell -EncodedCommand` 装不下大脚本（8191 上限）——大脚本必须走「b64 上传落盘 + `-File` 执行」两跳（本项目既有约定）。
2. 生成 PS 脚本时，模板里已带引号的占位符不要再包一层引号（`''GetFightStage|…''` → ParserError）；另外 raw 里 `###ENTRY` 行的 `pair` 字段本身含 `|`，解析要按位置切。

---

## 2. 抽查表（14 账号 / 39 对）

> 完整机读表：`verify_final.md` / `verify_final.json`（含每对证据摘要）；逐对证据卡：`verify_evidence.md`。

### 2.1 failed 8 对（台账 failed —— 全部独立复核为关卡级失败）

| # | 账号 | 关卡 | 台账 | 独立复核 | 行级统计（30 天全量） | 抽样尝试的原始证据 |
|---|---|---|---|---|---|---|
| 1 | Gitano | SR-5 | failed | failed ✅ | fail_n=70（50 静默 + 20 未开），09-08~09-25 | 4 次 09-08/09-09：开始唤醒 `完成任务` → Fight 链 0 次「开始行动」即 `任务出错` |
| 2 | 皮埃尔商店 | CA-5 | failed | failed ✅ | fail_n=9（全部静默），09-02~09-04 | 09-02 六次 + 09-04 三次同型静默；同窗 1-7 正常刷 |
| 3 | 檀丝江喜 | LS-6 | failed | failed ✅ | fail_n=6（全部静默），仅 09-07 | 09-07 六次同型静默（链起 ~30s 报错，无任何中间日志） |
| 4 | tbNick_h56zy | SR-7 | failed | failed ✅ | fail_n=69（47 静默 + 22 未开），09-08~09-27 | 抽样 4 次（09-08×3、09-14）均静默；同窗 1-7 照常 |
| 5 | 白桑_ | SR-7 | failed | failed ✅ | fail_n=65（47 + 18），09-08~09-27 | 同型静默；同窗 1-7 照常 |
| 6 | 爱尔奎特我老婆 | SR-7 | failed | failed ✅ | fail_n=67（48 + 19），09-08~09-25 | 同型静默 |
| 7 | 拍前看简介 | AT-6 | failed | failed ✅ | fail_n=3（全部 `selected null`），09-01~09-02 | `GetFightStage: from ["AT-6"], selected null` + 「关卡计划内没有当日开放的关卡」 |
| 8 | LEUNG | LS-5 | failed | failed ✅ | fail_n=1（`selected null`），09-28 | `from ["LS-5"], selected null` + 任务跳过（= `MAS_PROBE_FARM.md` §9.1 的试探记录） |

### 2.2 unknown 3 对（台账 unknown —— 全部独立复核为"无结论"）

| # | 账号 | 关卡 | 台账 | 独立复核 | 行级统计 | 原始证据（为何不该有结论） |
|---|---|---|---|---|---|---|
| 1 | v | LS-6 | unknown | unknown ✅ | attempt_n=2（均 09-07） | 两次「Success! 但 LS-6 没打成」：一次 5 倍理智全给了 **LS-5**（系列回落，R3）、一次理智不足空转 → LS-6 从未开打 |
| 2 | 用户_173920298 | LS-6 | unknown | unknown ✅ | attempt_n=4（含 3 次登录失败） | 09-07 一次 Success! 理智 13/210 < 36 空转；另 3 次 `MAA 未能正确登录 PRTS`（登录级环境） |
| 3 | 河清灯_别寄圆通 | CA-5 | unknown/env | failed(env) ✅ | attempt_n=45 / selected_n=45，net_fail_n=9 | 45 选 9 败（掉线/重连/进程超时/中止），从未拿到 CA-5 掉落；台账**未**标 failed ✅（机制见 §3.2） |

### 2.3 success 28 对（台账 success —— 全部有原始掉落证据）

`Gitano/AT-8·Annihilation`、`LEUNG/1-7·Annihilation`、`tbNick_h56zy/1-7·Annihilation`、`v/AT-4·CE-6`、`寻找勇者的大魔王/AT-4·SR-7·Annihilation`、`拍前看简介/PR-A-2·SR-7`、`檀丝江喜/AT-4·Annihilation`、`河清灯_别寄圆通/1-7·LS-6`、`烤箱/AT-4·LS-6·Annihilation`、`爱尔奎特我老婆/1-7`、`用户_173920298/AT-4·CE-6`、`白桑_/CA-5·Annihilation`、`皮埃尔商店/AT-4·Annihilation`、`酸酸酸酸/1-7`。

- 每对均抽到 ≥1 次原始 `drop_statistics` 命中（次数/日期见 `verify_final.json`）；
- **成功的加冕不受 `maa_result` 拖累**（符合台账"结果面优先"口径），实证：`河清灯_别寄圆通/1-7` 的 26 次持有掉落尝试中，7 次 `MAA 进程超时`、2 次 `MAA 部分任务执行失败`、2 次 `MAA 在完成任务前中止`，其余 `Success!`——台账全部按掉落记 success ✅。

**准确率：39/39 = 100%（success 28/28、failed 8/8、unknown 3/3）。**

---

## 3. 两类易错点专项

### 3.1 ①「`maa_result` 成功但掉落里没有该关 ⇒ 应为 unknown（不是 success）」

抽查中这类尝试抽到 **11 次**（`verify_final.json` → `succ_nodrop`），逐条原始核对（摘例）：

| 尝试（原始文件） | 现场（原始日志） | 台账实际 |
|---|---|---|
| `2026-08-31 LEUNG 明日方舟-官-19-01-16-26.json` | `Start Task Chain: Fight` → `完成任务: 理智作战`（无「开始行动」，理智 3/205） | 未计入（该行 success 来自 08-30/09-12 等有掉落尝试）✅ |
| `2026-09-07 v 明日方舟-官-5-01-41-57.json` | 同上（理智 11/210 空转完成） | 未计入 ✅ |
| `2026-09-07 v 明日方舟-官-5-16-54-50.json` | 实际打的是 **wk_kc_5/LS-5**（`LS-5 掉落统计:` 当前次数 5，-150 理智），选区行却是 LS-6（R3） | `v/LS-6=unknown`、`v/LS-5=success`（掉落归因正确）✅ |
| `2026-09-07 用户_173920298 …-02-52-41.json` | `完成任务: 理智作战`（理智 13/210 < 36，无开打） | 未计入 ✅ |
| `2026-08-31 拍前看简介 …-02-14-16.json`、`2026-08-31 Gitano …-01-10-48.json`（同型 6 例） | 链完成但无该关掉落 | 未计入 ✅ |

**结论**：① 规则**完全按预期执行**——成功只认「该关出现在掉落记录里」，`maa_result` 单独不构成成功；且 4 个 unknown 行正是这类号被正确停在 unknown（详见 §4）。

### 3.2 ②「失败但其实环境（客户端未就绪）⇒ 不该是 failed」

- 抽样 8 个 failed 对用**原始日志**复核 + 全部 **25 个 failed 行签名审计**（`signature_audit.txt`）：
  - 掉线/重连标记：**0 行**命中（唯一 1 例见下方时序陷阱，已排除）；
  - 登录级标记（`未能正确登录 PRTS`/`任务出错: 开始唤醒`/`未开始监看`）：**0 行**；
  - 同次尝试其他链连锁报错（≥2 链）：**0 行**。
  - 25 个 failed **全部是关卡级证据**：开始唤醒链全部 `完成任务: 开始唤醒`（客户端就绪），同一次尝试里 `1-7/剩余理智` 照常开打（皮埃尔商店 09-02、骰子 09-08、以七 09-08、tbNick_h56zy 09-08 皆如此）。
- **环境失败在台账里的表达**：`status=unknown` + `failure_types∋fight_error_net` + `impact="env"`（例：`河清灯_别寄圆通/CA-5`，attempt_n=45、net_fail_n=9，无一次该关掉落）。这正是 ② 想要的语义（"可能是环境，别据此说关卡不行"），**唯一差异是状态名**（无 `failed(env)`）→ 建议 R1。
- **反例校核（时序陷阱）**：`檀丝江喜/LS-6 2026-09-07 02-39-10` 一次尝试里**同时**有「模拟器断开,正在尝试重连(×5)」「重连失败,连接断开！」，但其出现在 **LS-6 报错之后**（LS-6 先静默报错 → 剩余理智打 1-7 成功 → 模拟器断开 → 已停止）。台账的 `net_recent` 顺序敏感，正确判成 `stage_unavailable` 而非 net——该边界行为建议固化为回归用例（R8）。

### 3.3 附带发现

1. **json↔log 掉落交叉核对（133 条）：零真实错配**。10 条"不一致"全部是同一种预期差异——剿灭的日志块是 `炎国 掉落统计:`（地图名），JSON 里剿灭不进 `drop_statistics`（台账 `MAP_NAMES` 已正确排除）。→ `.json` 与同名 `.log` 配对（`ChangeExtension`）可信。
2. **选区行 ≠ 实际开打关（R3 证据）**：v 09-07 16:54 全文（451 行）：配置 `Index 1, Type "Fight", Name 理智作战` + `GetFightStage: from ["LS-6"], selected LS-6`；实际 `开始行动 1~5 次, -150理智`（5×30 = LS-5 单价）→ `LS-5 掉落统计`（当前次数 5：龙门币 1800 + 作战记录）→ MAA 上报 Penguin `"stageId":"wk_kc_5"`。**同一关同一天四个号四种形态**：烤箱 09-07 16:01 用**同样的 `["LS-6"]` 列表**正常打 LS-6（`-108理智` = 3×36，`LS-6 掉落统计`）✅；v 侧落打 LS-5；檀丝江喜静默报错；用户/寻找/摸摸空转（理智 < 36）。→ 台账按「账号×关卡」记账是对的，但**选区行不保证等于实际开打关**（机制未定：MAA 系列回落 或 该号 LS-6 不可用导致的关卡导航差异；以证据面为准）。
3. **轮换关实测（供 R2）**：成功尝试按星期分布——CE-6 二/四/六/日（244 次，另有 2 次跨日边界落在三/五）、LS-6 一/三/五（52 次）、CA-5 三/五（74 次）、AT-4 日/一（79 次）、AT-8 日/一（18 次）；SR-5/7/8 在活动开启期内全周均有（SR-7：09-08~09-17 共 294 次 / 24 账号）。
4. **EV_CAP=40 截断**：30 天 5081 次尝试中 **10 次（0.20%）** 判据行触顶（长跑日志，如骰子 09-26 的 612 行日志）。对结论影响可忽略（成功主证据在 JSON），但见 R4。
5. **成功行新鲜度**：257 个 success 行中 143 行 `last_success` ≥12 天前（活动关关停残留，如 SR-7/SR-8/AT-4/AT-8；也有换号换关的 LS-6/CE-6）。消费方必须叠加 `last_success` 新鲜度（R2/R7）。
6. **SR-7 家族（25 failed 里 17 行）**：17 行 / **17 个账号**全部 failed，且与 24 个 SR-7 成功账号**零交集**——即 41 个挂过 SR-7 的号里，24 个能刷（09-08~09-17 共 294 次）、17 个从未能刷（09-08 起就静默失败，关停后转 `not_open`；单行 fail_n 47~70、not_open_n 16~22）。这是**账号级**差异的教科书样本；运维侧 `tools/activity_switch.py --heal-stale` 可收编这批号。

---

## 4. 全量对照（25 failed / 6 unknown）

- **25 failed 行**：8 行（本次抽样）原始日志逐条复核 ✅；其余 17 行用 state 内部判据行 + 签名指标全量过筛（§3.2），无环境型反例。失败类型分布：`stage_unavailable` 独有 4 行、`stage_not_open` 独有 3 行、两者混合 18 行（其中 1 行另含 `fight_error_net`）。
- **6 unknown 行**成因（全部核对过行级统计/证据）：
  | 行 | 行级统计 | 成因 |
  |---|---|---|
  | `v/LS-6`、`寻找勇者的大魔王/LS-6`、`摸摸摸摸鱼/LS-6` | 各 attempt_n=2、selected_n=2（均 09-07） | 链完成但无开打（理智 < 36 空转；v 那次侧落到 LS-5）→ 从未取得 LS-6 证据 |
  | `用户_173920298/LS-6` | attempt_n=4 | 同上 + 3 次登录级失败（`MAA 未能正确登录 PRTS`） |
  | `河清灯_别寄圆通/CA-5` | attempt_n=45、net_fail_n=9 | 45 选 9 败全为掉线/超时型（env），无该关掉落 → unknown+env ✅ |
  | `Raiz/1-7` | attempt_n=1 | 唯一 1 次尝试：Fight 链前 MAA 进程重启（`MAA 在完成任务前退出`），从未开打 |

---

## 5. 规则修订建议（交 lead；本卡未改任何代码/台账）

**R1（术语，文档层必做/代码层可选）**：把"环境型失败"的既有映射写进 `stage_capability.py` docstring / `judgement` 元信息与 `STAGE_CAPABILITY.md`：`failed(env)` ≙ `status=unknown` + `failure_types∋fight_error_net` + `impact="env"`；给下游示例：`can_farm()=="unknown"` 且 `can_farm_detail()["impact"]=="env"` ⇒ 先试探后开刷，别当"这号刷不了"。（可选：`can_farm_detail` 加派生字段 `env_fail: bool`，不动 status 语义。）

**R2（`stage_not_open` 的读法）**：活动/轮换关关停后，行会停在 `failed`（仅 stage_not_open）。文档已注"日粒度"，建议**渲染层**改文案为「当日不可选（活动/日轮换关）」并交叉引用 `docs/MAS_PROBE_FARM.md` §9.1 的教训（"`stage_not_open` 要读成『今天打不了』，不是『这号不行』"）；可附本次实测的轮换星期（§3.3-3）。注意：MAA 的可用性表存在缓存滞后风险（探针记录：LS-5 在 09-28 周一被即判 not_open，而 09-07 周一有成功记录），故 `not_open` 只代表「MAA 当日判定不可选」。

**R3（选区行 ≠ 实际开打关）**：实证 v 09-07 16:54（`selected LS-6` → 实际 `wk_kc_5`；同系列另一号同日同列表正常打 LS-6）。成功侧无影响（掉落权威）；**失败侧有理论风险**：一次"系列回落后的战斗失败"会被归到选区上的关（例如把 LS-5 的问题记成 LS-6）。建议：F_UNAVAIL 归类前加一条保守规则——若同次尝试的掉落记录里出现**同系列**关卡（前缀如 `LS-`/`CE-`/`CA-`/`AP-`），则降级为 `unknown`（宁可无结论，不给错误结论）。本次样本未触发（失败均在开打前），可低优先级排期。

**R4（EV_CAP=40）**：实测 0.2%（10/5081）触顶，当前影响可忽略；若未来纳入肉鸽等长任务，建议「按关/按链分段截取」或提高上限（只影响判据行缓存，不影响 JSON 主证据）。

**R5（QA 固化）**：把「分层抽样 + 原始证据复核」脚本固化为 `tools/tests/` 下的可选离线 QA（默认不联网；SSH 取证显式开启），并在 `docs/REGRESSION.md` 加入口；建议台账大改后或每月跑一次（SSH 成本 ~1–2 分钟 / 100+ 尝试）。

**R6（交叉核对白名单）**：QA 脚本的 json↔log 掉落核对把「剿灭地图名（炎国/乌萨斯/龙门…）」记为预期差异（台账 `MAP_NAMES` 已如此），减少下次误报。

**R7（成功证据留痕）**：success 行现只记 `last_success`；建议留 `success_files`（最近 1–3 个），未来追溯不用再翻 state。

**R8（回归用例）**：把 §3.2 的时序陷阱（`檀丝江喜/LS-6 09-07 02-39-10`：环境标记在链错之后 ⇒ 仍判 stage_unavailable）做成 `--selftest` 离线用例，防"顺序不敏感"回归。

---

## 6. 局限

1. **样本面**：账号 14/61（23%）；failed 覆盖 8/25（32%）、unknown 3/6（50%）；其余 failed 行只做签名级审计（state 40 行截断内），未逐条拉原始日志——但签名探针正是为 ② 类误判设计的，且 8 个抽样行验证了"签名 ↔ 原始结论"一致。
2. **时间窗**：2026-08-30 ~ 2026-09-28（台账窗口内）；窗口外未看。
3. **归因边界**：MAA 内部"为什么进不去某关"（未解锁/系列回落/识别差异）**无法从日志直读**，本报告只给证据面结论（对台账口径足够：它只声称"能不能刷"）。R3 的机制标注为"未定"。
4. **未评估面**：`--update` 增量流程在"同次尝试被重扫/改写"下的状态一致性（属其他卡范畴）。

---

## 7. 复现入口

```bash
PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"
cd D:/1-2/MAAOrch
$PY312 docs/audits/2026-09-28_stage_capability_qa/qa1_sample.py     # 分布（不联网）
$PY312 docs/audits/2026-09-28_stage_capability_qa/qa3_manifest.py   # 分层抽样+清单（不联网）
$PY312 docs/audits/2026-09-28_stage_capability_qa/qa4_fetch.py      # 原始证据（SSH 只读）
$PY312 docs/audits/2026-09-28_stage_capability_qa/qa11_final.py     # 独立复核+终表（本地）
$PY312 docs/audits/2026-09-28_stage_capability_qa/qa10_signature.py # 25 failed/6 unknown 签名审计
```

产物清单（均在 `docs/audits/2026-09-28_stage_capability_qa/`）：
`sample_design.txt`、`sample2.json`、`manifest.json`、`recon.txt`、`raw/batch_01..05.txt`（133 条）、`verify_table.md`、`verify_evidence.md`、`verify_final.{json,md}`、`context/windows.txt`、`context/windows2.txt`、`context/ranges.txt`、`context/v_full.txt`、`signature_audit.txt`、`peek_state.txt`、`global_checks.txt`。
