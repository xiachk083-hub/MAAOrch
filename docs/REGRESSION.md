# 回归测试套件（REGRESSION）

> 一张卡交付（kanban `t_19907707`）：把 2026-09-27/28 那批「全靠一次性实测」的修复
> （切号注入 / 判活关模拟器 / 理智药与关卡回退位 / 配置锁定语义 / 补跑闸门 / 台账）锁成
> **可重跑用例**，任何升级或改动都能 5 分钟内验证「没坏」。

## 怎么跑

```bash
# 1) 离线层（默认）：秒级，零网络、零写入 —— 日常改完代码先跑这个
python tools/tests/test_regression.py

# 2) 实机只读层：+ REST + ssh（只读），实测 ~40–60 秒（网络差时几分钟）
python tools/tests/test_regression.py --live

# 3) 机器可读 / 存报告 / 只跑某组
python tools/tests/test_regression.py --live --json
python tools/tests/test_regression.py --live --out runtime/reports/regression.json
python tools/tests/test_regression.py --only test_L2
python tools/tests/test_regression.py --list

# 4) pytest 也可以（live 用例默认 skip；要跑加 MAS_REGRESSION_LIVE=1）
python -m pytest tools/tests/test_regression.py -q
python -m pytest tests/ tools/tests/ -q          # 全仓测试（含既有 345 例）
```

解释器：`C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe`（带 pytest 9.1.1；
工具本身只用标准库，任意 Python 3.10+ 都能跑）。**跑本套件请用这个 Python312**：`mas_run_profile`
的自检里 `pick_python()` 会探测 `python -c "import rapidocr_onnxruntime"`，用别的解释器跑时这条探测
会被自检 monkeypatch 的 `subprocess` 记成第一条 argv ⇒ B6 假红（2026-09-28 实测：3.14 venv 红 /
3.12.10 绿，代码没错）。末行固定输出：

```
REGRESSION=PASS (34 passed, 0 failed, 0 skipped, 30.8s) [live]
```

供 cron / 巡检 grep（与 `SWITCH_GATE=` / `SELFTEST=` 同风格）。退出码 0=PASS，1=有失败。

## 时长实测（2026-09-28 09:36）

| 层 | 用例 | 耗时 | 网络 |
|---|---|---|---|
| 离线 | 29 例 | ~25s | 无 |
| 实机只读（--live） | +5 例 | 31–48s（含离线层） | REST 36163 + ssh 主机 |

一次完整实跑输出（2026-09-28 11:11 刷新，`REGRESSION=PASS (34 passed, 0 failed, 0 skipped, 37.1s) [live]`）：
`docs/audits/2026-09-28_regression_run.txt`（人读）+ `.json`（机读）。

## 覆盖表（用例 → 锁住什么 → 真值出处）

### A 切号注入语义（离线，纯函数）
| 用例 | 锁什么 |
|---|---|
| A1/A2/A3 | 官服 = `AccountName` 掩码（`\d{3}\*{4}\d{4}`）+ `AccountSwitchEnabled=true`；非官服 = `""` + `false`；违例三条全检出 |
| A4 | ClientType 三形态归一（int 0..5 / 枚举名 / 协议别名）；不认识 ⇒ UNKNOWN（fail-closed） |
| A5 | `mas_switch_gate.py --selftest` 全绿 |

规则出处：`tools/mas_switch_gate.py` 头注（v2，2026-09-27）；注入侧 = 宿主 `AutoProxy.py` L766-779 补丁（实机核对见 L2）。
**注意**：我方工具（`mas_run_profile`/`mas_scenario_dispatch` 等）**不碰**这两个字段（D3）——
切号字段是 AutoProxy 注入面的专属职责，混写会造成「谁改了配置」扯不清。

### B AutoProxy 注入的吃药/关卡字段读回（离线，判据实现）
| 用例 | 锁什么 |
|---|---|
| B1 | `parse_gui` 条目分类（理智作战 / 剩余理智 / 剿灭 / 活动关优先；剿灭不得被当主战斗） |
| B2 | 主战斗 5 键 `StagePlan/Series/MedicineCount/UseMedicine/IsEnable` 与用户配置一致 ⇒ 通过；逐键扰动必须报 diff；`"1"`/`0-1` 序列化形态不误报 |
| B3 | 简洁模式硬关项 `EnableTimesLimit/EnableTargetDrop` 被打开必须报 diff |
| B4 | 多号归属（按 `StartUp.AccountName` 掩码）；mtime 时效门；剩余理智条目存在性双向核对 |
| B5 | **实机 golden 样本**（09-28 07:51 真机读回，出处 `docs/audits/2026-09-28_mas_run_profile_live.run.log`）必须命中；`StagePlan` 丢掉回退位 ⇒ 必须不命中 |
| B6 | `mas_run_profile.py --selftest` 全绿（假传输全链路） |

判据实现 = `tools/mas_run_profile.py::match_main / match_readback`；机制口径 = `docs/MAS_RUN_PROFILE.md`。

### C 补跑闸门（离线）
| 用例 | 锁什么 |
|---|---|
| C1 | 白名单 `^(日常|晚班)定时([1-9]|1[0-2])$`；拒在任何 IO 之前 |
| C2 | `NO_GO` 一票否决；dry-run 只探活不发车；REST 不可达 ⇒ `upstream_unreachable` |
| C3 | 单飞：同队列当天已发 ⇒ `duplicate_today`；`--force` 才可重发；成功落台账 |
| C4 | 并发锁：被占 ⇒ `concurrent_dispatch`；陈旧锁（> `LOCK_STALE_S`）回收后继续 |
| C5 | `code==200` 必须带 `taskId`（否则 `no_taskid` 不可重试）；坏 uid / 队列不存在 ⇒ 不重试终态 |
| C6 | `mas_dispatch.py --selftest` 全绿 |

### D ScriptConfig 语义（离线，源码/文档级）
| 用例 | 锁什么 |
|---|---|
| D1 | 我方发车工具发给 `/api/dispatch/start` 的 `mode ⊆ {AutoProxy, ManualReview}`，绝不发 ScriptConfig |
| D2 | 文档红线在位：`mas_ondemand.py`「只装配配置不跑链」、`docs/MAS_ONDEMAND.md` ScriptConfig 行、handbook「别用 ScriptConfig 模式等任务链」「不要用 ScriptConfig 验证」 |
| D3 | 写配置的工具不碰切号字段（`mas_run_profile` 只写 7 键；`mas_scenario_dispatch` 显式跳过并记 `skipped_policy`） |

**语义备忘**：ScriptConfig 模式 = 只装配置、不自动跑链（编辑模式）；要跑链必须 AutoProxy/ManualReview。
全仓唯一刻意使用 ScriptConfig 的地方是 `tools/mas_startup_verify.py`（登录核对流程）——
若它的行为发生变化，先回到本组用例确认口径再动。

### E 台账 / 租约 / 快照契约（离线）
| 用例 | 锁什么 |
|---|---|
| E1 | 共享账本 key 口径 `q:/s:/a:/w:`（CLI 与 MCP 共一本账的前提） |
| E2 | 同 `task_uid` 幂等更新（不产 prev 杂物）；新任务顶替且旧记录进 `prev`；乱序旧记录不顶替新记录 |
| E3 | busy 窗口语义（`busy_until` 优先、缺省退 `ts+hold_min`；无 `task_uid` 不算在场；被顶替记录在窗内仍可见） |
| E4 | 旧账（CLI runs / MCP day-key）只读合并 + 指纹幂等；坏账本隔离 `.corrupt-*` 后空账自愈 |
| E5 | 保留策略：>7 天日期段裁掉；`temp_queues` 上限 200 |
| E6 | run_profile 运行台账（租约）：open 记录必留、restored 只留最近 50；坏文件回退空账 |

### F 工具自检汇总（离线）
| 用例 | 锁什么 |
|---|---|
| F1 | `mas_plan_builder`(37) / `mas_probe_farm`(23) / `mas_ondemand`(30) / `mas_preflight` / `mas_rollback_drill`（回滚演练周跑入口，`t_7fa96ea5`：三闸/判据/归档/告警 dry-run，25 例）各自 `--selftest` 全绿 |
| F2 | `mas_mapping`（端口映射，`t_ee50913f`）：`--selftest` 全绿 + 三条铁律（串口用实测值不回退公式 / 基线端口漂移必留痕成 RED / 凭据手机号不落产物）+ md 主表结构 |
| F3 | 「在飞按需」核销判据（`t_a8b4ceb0` 现场复盘，纯函数）：在飞 run 声明的值（指针/payload/它关的 Status）⇒ 核销；未声明的值、孤儿（无活体证据）、已还原 run 的指针 ⇒ 判违例。锁/租约两种活体证据 + 陈旧锁 |

### L 实机只读层（仅 `--live`）
| 用例 | 锁什么 | 只读面 |
|---|---|---|
| L1 | 12 槽 `gui.new.json` 切号注入合规：**违例 ⊆ 已知残留 {MAA_10, MAA_12}**，UNKNOWN 即失败 | REST `/api/scripts/*` + ssh 读 12 个 `gui.new.json` |
| L2 | 侧车三补丁哈希 + 关键标记：`mumu.py`（可靠判活/重试/回滚分支）、`constants.py`+`AutoProxy.py`（理智药 True）、`AutoProxy.py`（切号注入 v2 两分支） | ssh 读 3 个文件（base64 取回，本地校验） |
| L3 | 46 号 SR-* 的 `Stage_1` 回退位（理智药消耗路径）API 与落盘两层一致：`API 46/46；pending 0；bad 0`。**合法态两类**（`t_a8b4ceb0`）：`StageMode=Fixed`，或**在飞按需 run** 自己声明的指针（逐键比对 + 指针解引用 + 活体证据）；孤儿指针/未声明差异仍 FAIL | `mas_stage_fix.py --verify --json`（只读；机读摘要 = `STAGE_FIX_VERIFY_JSON=`）+ 本地两份台账 |
| L4 | 运行台账无**滞留**记录：未还原但有活体证据（run_profile 锁新鲜 / 实例租约在租）= 在飞（放行并提示等收尾）；未还原且无活体证据 = 滞留（FAIL） | `mas_run_profile.py --status` + 锁/租约 |
| L5 | 按需发车共享账本可解析、schema 合规、在窗记录字段完整 | 本地 `runtime/mas_ondemand_ledger.json` |

## 失败了怎么办（逐类 triage）

| 失败 | 先查什么 |
|---|---|
| A 组 | 有人改了切号口径或闸门规则？对照 `docs/audits/2026-09-27_switch_gate_two_violations_rootcause.md`（口径 v3：判定只增不改）。改规则必须同步改本套件，并说明理由 |
| B 组 | 读回判据（`match_main`）被改？或 golden 样本过期？golden 样本换新前先确认「旧样本为什么不再成立」 |
| C 组 | 闸门/白名单/单飞语义被改（这类改动最容易把「静默双发」放回来） |
| D 组 | 文档红线被删（先看是重构了表述还是删了约束）；发车 mode 出现第三种 = 停下来查 |
| E 组 | 账本 schema / 保留策略被改（CLI 与 MCP 两端要同步） |
| F 组 | 某个工具 `--selftest` 红：先看该文件 mtime —— 多卡同仓并行改动时，工具文件正被替换会瞬时红，等它落地后复跑；落地后仍红 = 真坏，按该工具自己的用例表逐项定位 |
| L1 | 出现**新**违例槽位 = 注入补丁可能被覆盖/回滚 → 立刻查 L2 与宿主 `AutoProxy.py`；UNKNOWN = 读不到（fail-closed，别当通过） |
| L2 哈希漂移 | 侧车文件被改过。先按 markers 与审计文档复核行为，确认无误后**更新本文件顶部 `PINS`**；已知迁移窗口内可临时 `MAS_REGRESSION_HASH_DRIFT=allow` 放行 |
| L3 | 先看 `未核销=` 那几号：空（只有 `在飞核销=`）= 采样窗口撞上按需运行，等收尾后复跑；非空 = 谁的差异没被在飞 run 声明（查 `runtime/run_profile_state.json` / `instance_leases.json`）。`pending>0`（非在飞号）= 内存值还没落盘（等一轮再跑；**重启侧车前必须先 flush**）；`bad>0` = 回退位被改回（理智药消耗路径又断了） |
| L4 | 报滞留（未还原 + 无锁/无租约）= 崩溃残留：`mas_run_profile.py --reconcile`（先 dry-run 看它说「任务已结束」再 `--apply`）；提示里「在飞 N 条」= 正常，等收尾复跑 |
| L5 | 账本坏了（先看有没有 `.corrupt-*`）；schema 版本变了要同步 E 组 |

## 维护

- **`PINS`（L2）**：三个侧车补丁文件的 sha256，硬比对。任何改动都要人工复核后更新，
  更新时在提交信息里写清「改了什么、为什么」。这是「防补丁被覆盖后无声回归」的主闸。
- **`KNOWN_GATE_VIOLATIONS`（L1）**：已知残留违例白名单（MAA_10/MAA_12，还原不动点）。
  受控播种清理执行后应变为空 —— 白名单只允许缩小，不允许为「新出现」的槽位开后门。
- **golden 样本（B5）**：换样本时保留旧样本的失败断言（B5 的第二个断言），别把「不命中」路径删掉。
- 环境变量：`MAS_REGRESSION_LIVE=1`（等价 `--live`）、`MAS_REGRESSION_TIMEOUT`（子进程超时，默认 420s）、
  `MAS_REGRESSION_HASH_DRIFT=allow`（L2 哈希漂移降级为 WARN）、`MAAORCH_SSH_KEY` / `MAAORCH_SSH_HOST`。

## 约束（红线）

- 默认（离线层）**零写入、零网络**：`runtime/` 一律被临时目录顶替（monkeypatch），不碰真实台账。
- `--live` 层**只读**：不发车、不改任何配置、不写远端文件、不 kill 进程；唯一写入 = `--out` 指定的报告文件。
- 需要写操作的验证（播种清理、改配置、发车）**不在本套件内**，按各自卡片的「需人工批准」流程走。
- **在飞按需运行不算违规**（`t_a8b4ceb0` 定因）：L3/L4 用「未还原记录 + 活体证据（`runtime/mas_run_profile.lock`
  新鲜 / `runtime/instance_leases.json` 该实例在租）」把「在飞中间态」与「滞留残留」分开 —— 中间态放行并打印提示，
  残留照红。判据纯函数在 `test_F3_inflight_pointer_accounting` 里锁住，改动它 = 改闸门口径，必须显式过目。

## 建议接线（可选，未实施）

- 巡检 cron（job `52b88b0ffca0`）或每日复盘加一条：`python tools/tests/test_regression.py --live`，
  只 grep 末行 `REGRESSION=`；失败即告警。注意与 04:00 / 19:00 宿主计划重启窗口错峰（重启会打断在飞任务，
  但本套件只读、不依赖在飞任务，撞上最多是 L1 读到中途态）。
- 任何改 `tools/` 下这几个工具的提交，先跑离线层（~28s）再提交。
