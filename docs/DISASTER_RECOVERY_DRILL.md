# 从零恢复演练（RTO/RPO 实测 + 重建 SOP）

> 建：2026-09-28 ｜ 任务 `t_d3d472ac`（"从零恢复演练：拿配置快照在临时环境重建，验证 RTO/RPO"）｜ 执行：mas
> 快照源：`runtime/snapshots/20260928_095643__pre-drill`（675 文件 / 8,459,564 B，含 1 敏感件 store 副本）
> 配套：工具 `tools/mas_config_snapshot.py`（X3 卡 `t_3c8438a7`）＋ 校验器 `tools/mas_dr_verify.py`（本卡新增）＋ 手册 `docs/MAS_EMERGENCY_RUNBOOK.md`
> 本次演练**未触碰生产配置、未重启侧车**；一切写盘落在本地临时副本与主机 `backups\_dr_rto_*` 临时区，演习后已清并与主机复核。

---

## 0. 底线结论（TL;DR）

**「主机挂了 / AUTO-MAS 崩了，多久能恢复？」——配置层纯机器时间 < 2 分钟；加上校验、预检、发车，实测链路 5 分钟内闭环。**

| 环节 | 实测 | 判据（机检末行） |
| --- | --- | --- |
| 本地从零恢复（临时副本，674 文件） | **80 s** | `RESTORE=DONE ok=674 failed=0` |
| 幂等复验（再跑一遍，无写入） | **2 s** | `RESTORE=DONE ok=0 skipped=1`（1=敏感件无本地 store） |
| DR 产物校验（哈希/JSON/注入/补丁指纹） | **2 s** | `DR_VERIFY=OK`（唯一 WARN 见 §4.5） |
| 远程（主机）从零恢复 675 文件 | **18 s**（修复后） | `RESTORE=DONE ok=675 failed=0` |
| 上述远程恢复（修复前） | **FAIL**：300 s 超时、0 文件 | 见 §4.1（已定位并修复） |
| 发车验证（骰子@maa_5/idx23） | 任务 **32 s** 创建、MAA.exe **≤2.5 min** 拉起 | app.log `开始运行任务` + `MAA.exe` 进程 |
| 总 RTO（配置层+发车链路） | **≤ 5 min 机器时间**（不含重装程序本体/人工决策，见 §4.4） | —— |

**RPO**：「丢多少」= 快照频率决定。当前**无定时快照**（RPO 不可控）→ 建议见 §5：4 次/日 + 变更前快照 ⇒ 常态 RPO ≤ 7.5 h、变更窗口 RPO ≈ 0，单次成本 30 s / 8 MB。

**本次演练的主要收获是失败点**：找到 1 个真事故级缺陷（远程上传通道随机死锁，两地独立复现，现已修复）＋ 2 个物料缺口 ＋ 1 个实例故障（今日唯一未跑号）。

---

## 1. 演练设定与范围

- **场景**：生产主机（AutoMAS 宿主 `100.79.173.69` / `E:\AUTO-MAS`）不可用，需在新环境/重装后，用配置快照把「60 个号 + 队列/计划表/用户配置/补丁」贴回去并恢复发车。
- **拓扑**：

```
[快照仓库 D:\1-2\MAAOrch\runtime\snapshots]  ←(只读快照)→  [宿主 E:\AUTO-MAS]（生产）
          │ restore                                        │ 同机 store：backups\cfgsnap_<ts>\
          ▼                                                ▼
  本地临时副本（--to …\_drills\…\root）        主机临时区（--remote-root …\_dr_rto_*）
          │                                                │
          └──────────► 校验（mas_dr_verify）◄──────────────┘
                                │
                                ▼
                  预检（mas_preflight）→ 发车（mas_ondemand）→ 观察 MAA 起跑
```

- **边界（红线，全遵守）**：不碰生产配置；不重启侧车；快照/枚举只读生产；恢复写盘只进 `_dr_rto_*` 临时区（带前缀 guard 删除）；发车用**空闲实例、单号、单次最小**。
- **为什么快照挂在「本机」**：快照仓库在 MAAOrch 机（D:），生产在宿主（E:）——**跨机一份**，宿主纯毁时快照仍在。两机同毁的场景另议（§5 建议）。
- **发车对象选谁**：全队 53 个启用号中当日仅 `hbznn119-官` 未跑（其余已跑）——先试它（补缺口最省），但 `idx51` 实例无 MuMuManager 记录、preflight NO-GO（§4.2）→ 改用单号脚本、实例在跑、槽位空闲的 **`骰子`（官-9，maa_5，idx23）**。

---

## 2. 从零重建 SOP（实跑版）

> 每步附「命令 + 验收判据」。演练即按此序列执行（命令均来自本仓工具，PY312 见 `docs/TOOLBOX.md` §0）。

### Step 0 — 物料前提（⚠ 缺口见 §4.4）

- 同版本 **AUTO-MAS 程序本体**（当前快照**不覆盖**，需另行保留安装介质/版本号）；
- 宿主 SSH 私钥 `~/.ssh/maaorch_target`；本机 MAAOrch 仓库（`tools/`、`runtime/snapshots/`）；
- Python312；**MuMu + 实例布局**与 **E:\MAA-Multi\MAA_*（MAA 本体与目录）**（同样不在快照内，需按 `docs/MAS_EMULATORS.md`/映射表重建）。

### Step 1 — 部署 AUTO-MAS，REST 探活

```bash
curl -s -m 8 -X POST http://100.79.173.69:36163/api/scripts/get -H "Content-Type: application/json" -d '{"scriptId":null}'
```

- 判据：返回 `{"code":200,...}`。502/拒连 = 侧车未起或端口未就位（F1，见手册 §3.1）。
- 实测：探活 3/3 通过（演练中曾一次 502 瞬断自恢复，见 §4.3）。

### Step 2 — 暂停自动发车（防止重建到一半被发车）

```bash
hermes -p mas cron pause 2600f6415f16     # 班次发车 job（恢复时 resume）
$PY312 tools/mas_ondemand.py --list-running   # 确认无在跑槽位/任务
```

- 判据：无「在跑 MAA.exe / 未结束任务」。等重建完毕再 `cron resume`。

### Step 3 — 恢复配置（先看代价，再落盘）

```bash
$PY312 tools/mas_config_snapshot.py --restore 20260928_095643__pre-drill            # dry-run：逐文件显示将覆盖/新建
$PY312 tools/mas_config_snapshot.py --restore 20260928_095643__pre-drill --apply    # 真回滚（生产根自动前置快照+复读校验）
# 演练口径（写临时区，不碰生产）：
$PY312 tools/mas_config_snapshot.py --restore <ts> --apply --to <dir>               # 本地临时副本
$PY312 tools/mas_config_snapshot.py --restore <ts> --apply --remote-root <E:\...\_dr_rto_*>
```

- 判据：末行 `RESTORE=DONE ok=<n> failed=0`；任何 failed 项先查哈希来源（store/本机）。
- 实测：本地 674 文件 **80 s**（含写盘+复读校验）；幂等复验 **2 s**；远程 675 文件 **18 s**（修复后）。
- 敏感件口径：`config\ScriptConfig.json` 全保只走**主机同机 store**（`backups\cfgsnap_<ts>\`），本地只有脱敏版——恢复时由 store 直取（指针见 manifest `remote_copy`）。

### Step 4 — 校验恢复产物（本卡新增校验器）

```bash
$PY312 tools/mas_dr_verify.py --snapshot runtime/snapshots/<ts> --root <恢复落点> [--json] [--report out.md]
```

- 查三件事：**manifest 完整性/哈希一致**、**JSON 可解析**、**切号注入组合合规**（官=masked + `AccountSwitchEnabled=true`，非官=empty + false）+ 补丁指纹。
- 判据：末行 `DR_VERIFY=OK|WARN|FAIL`。`WARN` 只允许「生产侧本就存在的坏文件」一类。
- 实测：674/674 哈希全对；注入组合 53(空)+48(用户_N)+6(掩码)；唯一 WARN=生产侧 13.6 KB 全 NUL 文件被如实还原（§4.5，正面素材）。

### Step 5 — 发车前预检

```bash
$PY312 tools/mas_preflight.py --script <目标脚本> --json      # 全量含截图+OCR（约 +20~40s）
```

- 判据：`PREFLIGHT=GO`。NO-GO 停手（本次 hbznn119-官 即被此闸拦下，§4.2）。

### Step 6 — 发车验证（空闲实例、单号、最小）

```bash
$PY312 tools/mas_ondemand.py --account 骰子            # 干跑：解析+闸门+计划
$PY312 tools/mas_ondemand.py --account 骰子 --apply    # 真发车（自动建 临时-按需-* 队列）
```

- 验收（三看）：① app.log 出「创建任务 → 开始运行任务」（本次 apply 后 32 s）；② 目标目录出现 `MAA.exe`（本次 ≤2.5 min，`E:\MAA-Multi\MAA_5`）；③ 收尾后 `history\2026-09-28\<账号>\` 落目录（本轮未等到收尾，时间窗见 §3）。
- 收尾：`--cleanup` 删临时队列（busy 守卫会拦到任务结束，之后 `--release` 再 `--cleanup`，见 `docs/MAS_ONDEMAND.md` §6）。

### Step 7 — 恢复班次

```bash
hermes -p mas cron resume 2600f6415f16
$PY312 tools/mas_dispatch.py --reconcile        # 对账（dry-run）→ 需要时 --apply
```

---

## 3. RTO/RPO 实测明细

### 3.1 RTO 分解（全部为本次实测）

| # | 步骤 | 耗时 | 备注 |
| --- | --- | --- | --- |
| 1 | 快照枚举+拉取（宿主→本机） | 30 s | 675 文件 / 8.1 MB（09:56:43→09:57:12） |
| 2 | 本地恢复（674 文件→临时副本） | 80 s | 逐文件计划+写盘+复读哈希 |
| 3 | 幂等复验 | 2 s | 无新写入时零代价 |
| 4 | DR 校验（mas_dr_verify） | 2 s | 674/674 + 注入合规 |
| 5 | 远程恢复（修复后） | **18 s** | 675/675；修复前同命令 300 s 超时失败（§4.1） |
| 6 | 发车：apply→任务创建/开始运行 | 32 s | 含 preflight、建临时队列、REST dispatch |
| 7 | 发车：→  MAA.exe 拉起 | ≤2.5 min | 10:44:44 开始运行 → 10:47:07 已见进程 |
| — | **合计（机器时间）** | **≤ 5 min** | 不含重装程序本体与人工决策 |

**诚实边界**：本演练验证的是「配置层从零恢复 + 发车链路」。真正的主机灾后重建还含 **Step 0/1（装 AUTO-MAS、MuMu、MAA 多开目录）**——未演练（无同型备用机/安装介质未登记，§4.4），那部分取决于人工操作，不入此表。

### 3.2 RPO 与建议（摘要，理由见 §5）

| 现状 | 建议 |
| --- | --- |
| 无定时快照；快照靠人工/变更前顺手 → RPO 不可控（上次快照=上次人工动作） | 定时 **4 次/日**（04:30 / 12:00 / 17:00 / 23:30）+ **变更前必打**（`--label pre-change`） |
| 行为变更（补丁/计划表/队列）无强制快照 | 变更清单配 `--label`；巡检 `--drift` 只读探针透传日报 |
| 全天成本几乎为零 | 30 s / 8 MB × 4 ≈ 2 min 机器时间/日 |

最大暴露窗：04:30→12:00 = 7.5 h；含变更前快照后，**计划的变更 RPO≈0**。

---

## 4. 失败点与缺陷（本演练核心产出）

### 4.1 【真事故级·已修复】远程上传通道随机死锁

- **症状**：`--restore … --remote-root` 在**上传阶段挂死**——ssh 300 s 超时、主机侧 **0 文件落盘**、stdout 全空。
- **两次独立复现**：① X3 首版 drill（09:34，120/686 文件后卡死，worker 被回收）；② 本卡工具级直跑（10:05，300 s 超时 0 文件）。
- **本卡探针**：60 文件/批的合成上传**随机**死锁（约 1.0 s 成功 或 整批挂死；10 文件批全过、真实首 60 批却过）——与「内容」无关、与「大小/随机性」相关。
- **主机侧铁证**：每次超时的尝试在主机留下一只**卡死 powershell**（我的 5 次超时 ↔ 恰好 5 只 10:21–10:26 常驻进程，CPU≈0.2 空转；X3 调查中同类僵尸累积 38 只）。
- **兜底实测（本卡 workaround）**：批级重试 3 次 + stderr 抓取 + 断点续跑 → 12 批用了 15 次尝试**全部救回**，674 文件累计 ≈ **12 min**（其中 ~8 成时间耗在 60 s/次的超时等待）。**重试能救，但慢**。
- **根因（X3 定位）**：Windows sshd 对**大 stdin 载荷**随机死锁（64 KB≈5/6 成功，256 KB≈3/6，1.6 MB≈0/16；卡死时远端脚本体一行都不执行）——详见 `docs/audits/2026-09-28_mas_config_snapshot_transport.md`。
- **修复**：上传改走 **HTTP 拉取通道**（本机一次性 HTTP 服务 + 主机 `Invoke-WebRequest` 拉 zip；8 MB≈5 s），stdin 降级为小分片兜底。
- **修复后复验（本卡）**：同快照、同命令、独立重跑 → **675/675、18 s、`RESTORE=DONE ok=675 failed=0`**，临时区清理+主机复核通过。
- **经验**：远程写盘类操作要有①可重试②抓 stderr③断点续跑三件套；「挂死 0 文件」优先怀疑传输通道而不是内容。

### 4.2 【已收尾·定论（09-28）】hbznn119-官（idx51）无法发车

- 现象：`mas_ondemand --account hbznn119-官` 干跑 **NO-GO**：`idx51(实例 idx 在 MuMuManager 无有效记录（不保证能 launch）)`；preflight 不过项=emulator。
- **定论（t_43320903，11:24）：需人工重建实例（或人工拍板注销）**。VM 是真删除、非改名/迁移——MuMu 注册表（`info -v 51` → `-200 player index not found`；全表现存 55 台，缺 9/35/42/47/51/59）与 `vms` 目录**双无** idx51，注册表 55 个 name 中无「明日方舟-官-28」迁移痕迹，本机无 `.mumudata` 导出可导入；agent 侧红线内（不碰其它实例/不重启侧车/不抢在跑资源）无安全修复路径。
- **现状与纪律**：脚本「明日方舟-官-28」不在任何队列（09-27 起主动摘除）⇒ 不会自动发车、每天不会爆失败；但**任何一次把它排进队列/按脚本单发 = 全程失败**——preflight 的 NO-GO 是 fail-closed 的正确行为，不是待修 bug。
- 人工两条路：**A 复活**（MuMu 建 1 台新实例 → 装客户端并登录 hbznn119-官 或 import `.mumudata` → 改脚本 `Emulator.Index` 到新 idx → §A 三件事走通 → 发一次最小单次）；**B 注销**（删脚本配置或停用账号，同官-5/官-25 先例）。注意新建实例拿到的是**空号段最小 idx**（9→35→…），「重建回 51」不现实，方向是「拿新 idx 改脚本」。
- 过程留档：误触 `MuMuManager.exe create` 真建 idx9 → 当场 `delete -v 9` 回滚并复核（净影响 0）。全文见收尾报告。
- 证据与全文：`runtime/snapshots/_drills/20260928_0955__fromzero/t_43320903_closure.md`（+ `idx51_registry_probe.json`）；上游：t_d3d472ac 母卡 + 收尾卡 t_43320903。

### 4.3 【观测·待归因】演练窗口内 REST 502 瞬断

- 10:40 一次干跑遇到 `HTTP Error 502: Bad Gateway`；<1 min 后 3/3 探活恢复；主机侧同期（10:40:05）出现 `main.py` 新进程、app.log 刷新——**疑似侧车重启窗口**，触发源未定位（非本演练动作；本卡未重启任何东西）。
- 建议：作为 F1/F2 复盘输入（谁在 10:40 重启了侧车？看门狗/计划任务/其它 worker 动作面留痕）。演练结论不受影响。

### 4.4 【物料缺口】快照不覆盖的东西（真·从零的硬缺口）

| 缺口 | 说明 | 建议 |
| --- | --- | --- |
| AUTO-MAS **程序本体/版本** | 快照只收 `data\**`(662)+`config\*.json`(10)+关键补丁 `app\**`(3)=675 | 登记版本号+安装介质/安装包路径（进手册或快照字段），否则「同版本重装」无法判定 |
| `E:\MAA-Multi\MAA_*`（MAA 本体与目录配置） | 不在快照内 | 至少把 MAA 目录的关键 config 纳入快照扩展，或写一页重建清单 |
| MuMu 实例布局（idx↔名称↔端口） | 有文档（`docs/MAS_EMULATORS.md`/映射表）但非快照 | 文档已够用；建议映射表出问题时以 `mas_mapping.py --build` 现取 |

### 4.5 【正面】保真度与安全口径

- **生产侧坏文件如实还原**：`data\c3f07722…\Temp\gui.new.json` 是 13.6 KB 全 NUL 的生产侧损坏文件——快照收录、恢复后哈希仍一致（校验器如实报 WARN）→ 证「保真」而非「修数据」；同时也提示该坏文件至今没被清理。
- **敏感件不出主机**：`ScriptConfig.json`（215,271 B，sha `59749a16…`）全程只见于主机 store 与恢复落点，本机库内只有脱敏版。
- **幂等**：对已恢复的落点重跑 restore = 零写入（2 s）。

---

## 5. 快照频率与保留建议（RPO 设计）

1. **定时 4 次/日：`04:30 / 12:00 / 17:00 / 23:30`**——理由：避开宿主 04:00/19:00 计划重启与 05:00–08:00 / 19:30–23:00 班次；最大间隔 7.5 h。
   成本实测 30 s / 8 MB，四次/日对宿主几乎无感。（X3 `MAS_CONFIG_SNAPSHOT.md` §6 已给「12:00/23:30 类空闲点」同口径，本表在其上加 04:30/17:00 补夜窗与午窗。）
2. **变更前必打**（`--label pre-change`）：任何补丁/计划表/队列/用户配置手术前——把「变更类 RPO」压到 ≈0。与 `docs/ROLLBACK_DRILL.md` 变更清单配套。
3. **巡检只读探针**：每班次/每 3 h `--drift`（末行 `SNAPSHOT_DRIFT=`，漂移即给建议回滚行）——接入点见 `MAS_CONFIG_SNAPSHOT.md` §6，每日报透传。
4. **保留/归档**：本机默认 `--keep 30` ≈ 7.5 天（4 次/日口径）；建议每周挑一份归档到第二块盘/NAS（两机同毁场景的唯一对冲），并在半年一次的人工演练里走一遍「从归档冷启动」。
5. **下次演练待补**：把「重装 AUTO-MAS + MuMu + MAA 多开」纳入（需要备用机或维护窗），补齐 §3.1 表里唯一没实测的环节；顺带验证 §4.4 缺口是否已补。

---

## 6. 证据索引

- 演练目录：`runtime/snapshots/_drills/20260928_0955__fromzero/`
  - `timings.txt`（各环节秒数）、`restore_local.log`（本地恢复全量回执）、`restore_remote.log`（修复前失败原始输出）、`remote_retry_run{1,2,3}.log`（兜底重试全时间线）、`host_peek_during_hang.txt`（主机侧卡死证据）、`dr_verify_local.{log,txt}`（校验器输出）、`drift.log`、`snapshot_predrill.log`
- 校验器：`tools/mas_dr_verify.py`（`--selftest` 可离线复跑）
- 快照工具与通道口径：`docs/MAS_CONFIG_SNAPSHOT.md`（§4 传输通道）、`docs/audits/2026-09-28_mas_config_snapshot_transport.md`（死锁调查全文）
- 发车证据：app.log `90c71b09-bffe-4121-9e5f-ca69804cf422`（manual_task，骰子，10:44:44 创建 → **11:01:28 任务结束**，history `maa_result: Success!`）、临时队列 `临时-按需-20260928_1045-1`（id `301aa51e…`，**11:09:59 已 `--cleanup` 删除 code=200**，同批清掉 0631 残留 `ae9ce6f8…`；复核：dry-run「无候选」/ REST 两 id 0 命中）
- 收尾证据（t_43320903）：`runtime/snapshots/_drills/20260928_0955__fromzero/t_43320903_closure.md`（idx51 定论 + 队列清理回执 + MuMuManager `create` 误触回滚留档）、同目录 `idx51_registry_probe.json`（`info -v all` 原始件）
- 审计流：`runtime/mas_config_snapshot_log.jsonl`（restore/快照动作）、`runtime/mas_ondemand_log.jsonl` / `runtime/mas_dispatch_log.jsonl`（发车）

## 7. 复现手册（可直接粘贴）

```bash
PY312="C:/Users/xiach/AppData/Local/Programs/Python/Python312/python.exe"
# 1) 打锚点快照（若需要）
$PY312 tools/mas_config_snapshot.py --label pre-drill
# 2) 本地从零恢复（临时副本）
$PY312 tools/mas_config_snapshot.py --restore <ts> --apply --to runtime/snapshots/_drills/<run>/root
# 3) 校验
$PY312 tools/mas_dr_verify.py --snapshot runtime/snapshots/<ts> --root runtime/snapshots/_drills/<run>/root --report out.txt
# 4) 远程从零恢复（临时区）→ 清理
$PY312 tools/mas_config_snapshot.py --restore <ts> --apply --remote-root 'E:\AUTO-MAS\backups\_dr_rto_<ts>'
#    清理远程临时区：用工具原语 remote_rmdir(paths, guard)（带前缀 guard，CLI 无独立子命令；三行脚本调用即可，
#    本演练参考 tmp/dr_drill/rm_scratch.py：paths=[r'E:\AUTO-MAS\backups\_dr_rto_<ts>']; mcs.remote_rmdir(paths, r'E:\AUTO-MAS\backups\_dr_rto_')）
#    删后复核：Test-Path 应为 False
# 5) 发车验证（空闲单号）
$PY312 tools/mas_ondemand.py --account <账号>          # 干跑 GO
$PY312 tools/mas_ondemand.py --account <账号> --apply  # 真发车
# 6) 收尾
$PY312 tools/mas_ondemand.py --cleanup --apply         # 临时队列（busy 会被守卫拦）
```

## 8. 变更记录

- 2026-09-28 首版（t_d3d472ac）：建立 SOP + RTO/RPO 实测 + 失败点四则；新增 `tools/mas_dr_verify.py`。
- 2026-09-28 收尾（t_43320903）：§4.2 从「待收尾」改为**定论**（idx51 需人工重建/注销，依据见收尾报告）；§6 补队列清理回执与收尾证据；骰子临时队列已删、0631 残留同批清掉。
