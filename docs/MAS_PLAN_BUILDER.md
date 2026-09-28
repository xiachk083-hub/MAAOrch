# 刷取计划生成器 · tools/mas_plan_builder.py

把「看到的干员与仓库」变成「**该刷什么**」：读四份本地产物 → 出一份**刷取计划草案**
（`.md` 人读 + `.json` = run profile，可直接执行）。**默认零写、零发车**；执行要显式
`--exec-dry-run`（零写预览）或 `--apply`（真发车，需用户/lead 批准）。

> 一条命令：`python tools/mas_plan_builder.py --account y9Flipped`
> 产出：`runtime/reports/plans/<账号>_<yyyymmdd_HHMM>.md` + 同名 `.json`

---

## 1. 输入（全部本地只读）

| 输入 | 内容 | 出处 |
|---|---|---|
| 上号识别产物 | 干员 box、仓库物品 | `runtime/reports/accounts/<账号>/{operators.json,depot.json}`（见 docs/HERMES_ACCOUNT_INSPECT_PLAN.md） |
| 能力台账 | 账号×关卡 `success/failed` + 关池 | `runtime/reports/stage_capability.json`（tools/stage_capability.py） |
| 活动快照 | 各服在期/待开活动 | `runtime/reports/server_events.json` |
| 材料水位 + AP 预算 | 每种材料的目标量、每天可刷 AP | `config/material_water_levels.json` |
| 候选关掉率 | 材料 → 关卡（企鹅物流掉率） | `config/farm_stages.json` |
| 关卡计价 | `unit_cost["<CODE>\|普通"]` 书写约定 | `data/stage_apcost_v4.json` |
| 字段映射 / 发车机制 | 7 键差分、还原、闸门 | `docs/MAA_TASK_CONFIG_REFERENCE.md`、`docs/MAS_RUN_PROFILE.md` |

> 另有 `tools/stage_efficiency.py` 产出的**关卡效率底垫** `runtime/reports/stage_efficiency.json`
> （552 关的理智/主要掉落/每理智产出/置信度 + 材料→推荐关反查 + 与实测的一致性判定，见
> `docs/STAGE_EFFICIENCY.md`）——**本生成器暂未接线**（候选关仍走 `config/farm_stages.json`）；
> 接线评估时以它为准，不要再自己解析企鹅矩阵。

REST（`http://100.79.173.69:36163`）只在**解析脚本**时被读一次（`/api/scripts/get`、
`/api/scripts/user/get`）：优先用 `runtime/reports/mas_account_master_*.json` 名册快照给出
候选脚本名，再让 REST 复核；REST 不可达则离线兜底并告警。加 `--no-rest` 可完全离线。

## 2. 决策规则（可解释；每条都落进 .md 的「依据 → 结论」链）

1. **缺口** = `max(0, 水位 − 仓库)`；缺口全部为 0 时进入**补刀模式**（取「余量最小」的材料刷）。
2. **候选关** = `config/farm_stages.json[材料]` 按 **AP/个** 升序（`AP ÷ 掉率`）；AP 用
   `data/stage_apcost_v4.json` 的 `unit_cost` 交叉核对（书写约定见该文件的 `_conventions`）。
3. **磨难破平**：同关「磨难（`-HARD`）」与「普通」价差 ≤2% 时**优先普通**——磨难要单独解锁代理权。
4. **能力台账闸门**：`failed` 的关**不排**（含失败类型/日期作为依据）；`unknown` 的关照排但标注
   **「需先试探一次」**（关池外 or 30 天窗口内无记录，可能是没有代理权）；`success` 记为已证。
5. **练度体检（启发式）**：box 档位（E2/E2-60+/E2-80+/6★）对照关卡章节门槛（章 ≤9 无要求；
   章 10–14 需 E2-60+ ≥8；章 ≥15 需 ≥15）。**本地没有「关卡推荐等级」数据源**，这是启发式、
   非官方推荐等级；该号在 ≥ 该章有台账 `success` 时本项告警自动失效（实测优先）。
6. **理智 / 药**：每天可刷次数 = `AP 预算 // 关卡 AP`（预算见水位表，17 材料口径默认 280）；
   `--sanity N` 给当前理智 → 本次约 N÷AP 次（**上号识别产物里没有理智**，缺省只给预算估算）。
   `--medicine N` → `MedicineNumb=N`（默认 0=不吃）。**临期药**：AUTO-MAS 两层没有该键
   （MAA 侧 `UseExpiringMedicine` 随模板/该号 ConfigFile），计划写不了它，真发车只读回记账
   （docs/audits/2026-09-27_mas_medicine_expiry.md §1.1/§2）。
7. **活动期优先**：`server_events.json` 在期事件里 type 含「刷关/SideStory」→
   `activity_priority.enabled=true`（AutoProxy 会插「活动关优先」段）；快照 >3 天告警、>7 天
   **拒绝**据此判断（保持 false）。标「二手源（source=maa-stage-activity）」的事件会点名 caveat。
8. **stage_plan（≤4 位）** = 主刷 → 同材料备选×2 → 下一材料最优关 → **尾位 1-7**（恒开放；
   MAA 只取第一个「当日开放」者出战，所以 1-7 常态不参与，只在候选全不可用/被锁时兜底，
   避免整轮「关卡计划内没有当日开放的关卡」跳过理智作战）。

## 3. 产出

`runtime/reports/plans/<账号>_<时间>.md` —— 人读草案，9 节：
0 结论（主刷/回退位/用药/活动/停刷条件/待试探）· 1 输入与出处 · 2 材料缺口表 ·
3 **依据 → 结论链（逐条带出处）** · 4 候选关表（含被剔除的 failed 关 + 缺口估算次数/AP）·
5 练度体检（启发式，注明口径）· 6 理智/药/活动 · 7 执行（run profile 命令 + 排序理由 + 字段口径）·
8 停刷条件 · 9 风险/未验证。

同名 `.json` —— **字段与 tools/mas_run_profile.py 的 profile schema 对齐**，生成时就用
run profile 的校验器验过（生成器自检 rc=0 才落盘），可直接执行：`stage_plan` / `medicine` /
`activity_priority` / `series`（缺省不写，沿用该号当期值）/ `restore_after` /
`keep_remain_sanity_task` / `target` + 下划线注释块 `_plan_builder`（决策元数据：队列、缺口、
推断依据、探针清单、输入指纹）。

## 4. 用法

```bash
# 生成草案（默认，零写）
python tools/mas_plan_builder.py --account y9Flipped

# 只排指定材料 / 改水位（显式指定会强制进入计划）
python tools/mas_plan_builder.py --account y9Flipped --materials 固源岩组,异铁组
python tools/mas_plan_builder.py --account y9Flipped --material 固源岩组=200

# 带当前理智 / 吃药 / 连战倍数 / 超时盒
python tools/mas_plan_builder.py --account y9Flipped --sanity 210 --medicine 2 --series 1 \
  --restore-after timeout:3600

# 只用台账已证的关（更保守；unknown 会被剔）
python tools/mas_plan_builder.py --account y9Flipped --prefer proven

# 执行入口：先零写预览（走一遍 run profile 全路径，不写任何东西）
python tools/mas_plan_builder.py --account y9Flipped --exec-dry-run
python tools/mas_plan_builder.py --account y9Flipped --exec-dry-run --exec-preflight   # 顺带跑只读 preflight

# 真发车（需用户/lead 批准；多号脚本必须显式二选一）
python tools/mas_plan_builder.py --account y9Flipped --apply --disable-others
python tools/mas_plan_builder.py --account y9Flipped --apply --all-accounts
```

其它开关：`--script <脚本名>`（跳过解析）· `--out-dir` · `--list-accounts` ·
`--events-warn-age/--events-fail-age` · `--skip-capability`（不推荐）· `--no-tail-1-7` ·
`--prefer best|proven` · `--json`（机读单行）· `--selftest`。

**闸门**：`--apply` 前一票否决 `MAAORCH_PLAN_BUILDER_NO_GO=1`（rc=2，不调 run profile）；
`--apply` 也转发 run profile 自己的 fail-closed 闸门（多号脚本、preflight NO-GO、scripts 锁）。
`--exec-dry-run/--apply` 的每次调用都落 `runtime/mas_plan_builder_log.jsonl`（审计）。

## 5. 自测与证据

- `python tools/mas_plan_builder.py --selftest` → **SELFTEST=OK (37/37)**：假数据夹具，零触网、
  零写 `runtime/`；覆盖缺口/排序/磨难破平/台账剔除与探针/练度/理智与预算/活动（含二手源、
  过期快照）/1-7 尾位/写盘回读再校验/NO_GO 一票否决/命令构造等。
- 实机（不着车）：`docs/audits/2026-09-28_mas_plan_builder.selftest.txt`、
  `docs/audits/2026-09-28_mas_plan_builder.plan.{md,json}`（y9Flipped 真实草案）、
  `docs/audits/2026-09-28_mas_plan_builder.dryrun.txt`（dry-run 全路径：多号脚本 fail-closed
  rc=2 → 加 `--disable-others` 后 rc=0、零写、预计写 7 键 + 还原）。

## 6. 边界 / 未验证（按实记录，别当已证）

- **练度门槛是启发式的**（§2.5）：本地无「关卡推荐等级」数据源；只作提示，不作判据。
- **理智不在上号识别产物里**：不给 `--sanity` 就只有预算估算，以游戏内实际为准。
- **「刷够 N 次」不是可用字段**：AUTO-MAS 两层无总次数字段（简洁模式 `EnableTimesLimit` 硬关），
  §4 的「≈N 次」只是缺口估算；停刷靠 MAA 自然结束（理智耗尽）。
- **一轮 run profile 只刷一个关**（stage_plan 只取第一个当日开放者）：多材料的另一批材料要另起一轮。
- **磨难（`-HARD`）关**：默认让位给普通关（§2.3）；普通被锁时才会轮到它。
- **议事记录**：计划只产出文件；**不写任何 AUTO-MAS 配置、不发车**（除非 `--apply` 且被批准）。
