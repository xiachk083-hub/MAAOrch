# MAS 配置快照与一键回滚（mas_config_snapshot.py）

> 对象：`E:\AUTO-MAS` 的**配置面**——`config\*.json`（ScriptConfig/PlanConfig/QueueConfig…）、
> `data\**`（每脚本/每用户 MAA 配置目录）、以及**关键补丁文件**（`app\task\MAA\AutoProxy.py`、
> `app\utils\emulator\mumu.py`、`app\utils\constants.py`，另有「带 `.bak*` 兄弟文件」的自动发现）。
> 工具：`tools/mas_config_snapshot.py`（纯标准库；本机 python 直跑）。
> 目的：**定期快照 + 差异定位 + 一键回滚 + 漂移检测 + 恢复演练**，避免一次误操作 / MAA 升级把
> 60 个号的配置搞乱后无从回退。

---

## 1. 命令速查

```bash
python tools/mas_config_snapshot.py                        # 默认动作=快照（远程生产只读读取 → 存本机）
python tools/mas_config_snapshot.py --label pre-change     # 变更前快照（约定：动配置前先跑）
python tools/mas_config_snapshot.py --list [--json]        # 列出快照（files/大小/敏感数/store）
python tools/mas_config_snapshot.py --diff A B [--only G]  # 两次快照差异（文件级 + JSON 字段级）
python tools/mas_config_snapshot.py --drift [--against TS] # 当前生产 vs 快照（只读哈希扫描）
python tools/mas_config_snapshot.py --restore TS           # 回滚 dry-run：逐文件显示将覆盖什么
python tools/mas_config_snapshot.py --restore TS --apply   # 真回滚（生产根自动前置快照 + 复读校验）
python tools/mas_config_snapshot.py --restore TS --apply --to <dir>          # 本地临时副本（演练）
python tools/mas_config_snapshot.py --restore TS --apply --remote-root <dir> # 远程临时副本（演练）
python tools/mas_config_snapshot.py --drill [--against TS] # 恢复演练（本地临时副本 + 主机 scratch）
python tools/mas_config_snapshot.py --selftest             # 纯离线自检（35 例，不连网）
```

退出码：`0=OK；1=--drift 发现漂移；2=传输/读取失败；3=回滚有失败项；4=自检/演练失败；5=被闸门拒绝`。

机器可读末行（巡检/脚本接线的锚点）：
`SNAPSHOT=OK ts=… files=… sensitive=… store=…`（快照）·
`SNAPSHOT_DRIFT=OK` / `SNAPSHOT_DRIFT=DRIFT:n`（漂移）·
`DRILL=PASS|FAIL drill=<ts> evidence=<path>`（演练）·
`RESTORE=DONE ok=… failed=… skipped=…` / `RESTORE=FAIL …`（回滚）。

## 2. 产出物布局

| 位置 | 内容 |
| --- | --- |
| 本机 `runtime/snapshots/<ts>/` | `manifest.json`（文件清单：path/kind/size/mtime/sha256/sensitive/local/redacted/cmp_sha256/remote_copy）+ `files/…`（非敏感文件全保副本）+ `redacted/…`（敏感文件脱敏副本） |
| 本机 `runtime/snapshots/_drills/<ts>__drill/` | 演练目录：`root/`（本地临时副本）+ `snapB/`（改坏后快照）+ `evidence.{txt,json}` |
| 主机 `E:\AUTO-MAS\backups\cfgsnap_<ts>\` | **敏感文件全保 store**（ScriptConfig.json 等，密文不出主机；同机 `Copy-Item` + 逐文件哈希校验 + 内嵌 `MANIFEST.json`） |
| 本机 `runtime/mas_config_snapshot_log.jsonl` | 审计流（每次动作一行；无凭据值），`mas_audit.py` 的 `snapshot` 源 |
| 主机 `E:\AUTO-MAS\backups\_cfgsnap_drill_*` | 演练用 scratch（演练末自动清理；崩溃残留由下次 drill 的「阶段0 残留自清」扫掉） |

命名：`<ts> = YYYYmmdd_HHMMSS[__label]`；`--restore/--diff/--drift/--drill` 的 `<ts>` 支持前缀
匹配与 `latest`。保留数：本机快照/主机 store 默认 `--keep 30`，演练目录 10（`DRILL_KEEP`）。

## 3. 安全口径（红线）

1. **敏感文件不出主机**：`config\ScriptConfig.json` 等（键名精确集合 `Password/SklandToken/ServerChanKey/Notify_CustomWebhooks` + 正则 `(pass|token|secret|cookie)`）永不写本机全保，只留脱敏副本；全保在主机 store 内。脱敏后仍出现长 base64 则**拒绝写盘**（fail-closed）。
2. **回滚默认 dry-run**：不加 `--apply` 只打印计划；`--apply` 到生产根时自动**前置快照**（pre-restore），失败即拒（fail-closed）；`MAAORCH_SNAPSHOT_NO_GO=1` 为硬闸。
3. **写盘原子**：主机侧 `*.cfgsnap.tmp` + `Move-Item -Force`；本地 `.cfgsnap.tmp` + `os.replace`；回滚后**复读哈希**逐文件校验。
4. **锁**：快照/回滚/drill 互斥（`runtime/mas_config_snapshot.lock`，锁内记 pid）；崩溃残留锁的处置与「锁主存活判定」加固见 `docs/ROLLBACK_DRILL.md` §3-F1。
5. **生产只读**：默认动作只读生产；一切写操作落在本机快照目录、主机 `backups\`（`cfgsnap_*` / `_cfgsnap_drill_*` 前缀，删除带 guard 前缀校验）。

## 4. 传输通道（2026-09-28 实测定稿）

**教训（真事故级）**：Windows sshd 对**大 stdin 载荷会随机死锁**——远端 powershell 卡在读取 stdin，
脚本体一行都没执行（marker 不落盘），且每次卡死主机侧留一个僵尸 powershell 进程（一次调查中累积到 38 个）。
实测成功率：payload 64KB ≈ 5/6、128KB ≈ 4/6、256KB ≈ 3/6、1.6MB ≈ 0/16（与大小正相关、随机发生；
本机换用 Windows 自带 ssh 客户端、stdin 改从文件喂入均无改善；主机无 SFTP 子系统，scp 不可用）。
首版 drill 正是卡死在阶段2第二批（120/686 文件落盘后），把整个 run 拖死。

**现行口径**：

| 通道 | 机制 | 用途 |
| --- | --- | --- |
| **HTTP 拉取（主）** | 本机起一次性 HTTP 服务（绑「本机对主机可见 IP」、随机端口、随机 token 路径），主机 `Invoke-WebRequest` 拉 zip → `Expand-Archive` → 逐文件 `Copy → Move` + 哈希校验 | 一切上传（回滚物化、store MANIFEST）。实测：单文件 8MB ≈ 5s；drill 阶段2 的 685 文件 / 7.98MB（zip 1.91MB）≈ 11s |
| stdin 小分片（兜底） | 每片 ≤48KB 原始（base64 后 ≈64KB）、每片最多 4 次尝试、单次尝试超时 30s | 仅当 HTTP 通道不可用/有失败项时自动接管（慢但能收敛） |

配套 env：`MAAORCH_SNAPSHOT_PULL=0` 强制走 stdin 兜底；`MAAORCH_SNAPSHOT_PULL_HOST=<ip>`
手动指定回连地址（默认用「对主机方向」的本机出口 IP，tailnet 场景即 100.111.209.108）。
下载方向（主机→本机，`remote_fetch`）走 stdout，未受影响。

## 5. 恢复演练（--drill）

一次 `--drill` 的完整闭环（全程不碰生产；2026-09-28 实跑 **PASS**，12 步，≈50s）：

```
阶段0  主机 _cfgsnap_drill_* 残留自清（崩溃残留顺手扫掉）
阶段1a 快照 → 本地临时副本 root/（686 文件物化 + 哈希校验；敏感文件按设计跳过）
阶段1b 改坏 5 类：JSON 字段 ×2 / 补丁文件追加 / 删文件 / 仅碰 mtime
阶段1c 对改坏后的树做快照 B → --diff 逐字段定位（改动 3 / 删除 1 / 字段报告 3）
阶段1d dry-run 计划 → 真回滚（本地）→ 逐文件哈希复读
阶段2a 主机 scratch：plan（686 新建）→ HTTP 拉取通道物化 686（含敏感文件走 store 同机复制）
阶段2b 再改坏 2 个（AutoProxy.py + ScriptConfig.json）→ 二次回滚 → 清理 scratch
```

证据：`runtime/snapshots/_drills/<ts>__drill/evidence.{txt,json}`（复制归档到
`docs/audits/2026-09-28_mas_config_snapshot_drill.{txt,json}`）；审计流 `action=drill`。
重跑：`python tools/mas_config_snapshot.py --drill --against 20260928_092944__baseline`。

## 6. 与 mas_dispatch / 巡检的接入点

1. **变更前（纪律）**：动配置（计划表/队列/用户配置/补丁）前先 `--label pre-change` 打一枪；
   与 `docs/ROLLBACK_DRILL.md` 的「变更清单」配套使用。
2. **巡检（只读探针，推荐每班次/每 3 小时）**：
   ```bash
   python tools/mas_config_snapshot.py --drift --json   # rc: 0=无漂移 / 1=有漂移 / 2=扫描失败
   ```
   末行 `SNAPSHOT_DRIFT=OK|DRIFT:n`；漂移时工具直接给出
   `建议回滚: python tools/mas_config_snapshot.py --restore <ts>`——巡检只需把该行透传给日报。
   对补丁文件漂移工具会额外提示「可能被 MAA 升级覆盖 → 回滚补丁 + 重启侧车」。
   注意：`data\*\Temp\**` 默认不算漂移（`--include-temp` 可含）；缓存类 `data\cache\sra_*_activity.json`
   属正常演进，8 处漂移里常年占 5 处（见本日 drift 实测）。
3. **回滚（人工确认后）**：`--restore <ts>`（dry-run 看代价）→ `--apply`（自动前置快照）。
   本地演练可加 `--to <dir>`；远程演练 `--remote-root <E:\...\_rbdrill\d>`。
4. **定时**：宿主每日 04:00/19:00 计划重启、班次窗口 05:00-08:00 / 19:30-23:00——定时快照建议
   排在 **12:00 / 23:30** 一类空闲点（或并入既有 cron 链，别撞重启窗口）。
5. **审计**：本工具动作已并入 `tools/mas_audit.py`（`snapshot` 源、`config.<action>` 目标）。

## 7. 排障

| 症状 | 处置 |
| --- | --- |
| `已有快照/回滚在跑` | 查 `runtime/mas_config_snapshot.lock` 内 pid 死活（`tasklist /FI "PID eq <pid>"`）。该死 → 删锁重跑；锁自愈加固见 `docs/ROLLBACK_DRILL.md` §3-F1 |
| 回滚/物化卡住 >2min 无输出 | 旧版 stdin 大载荷死锁征象；确认走的是 HTTP 通道（日志行 `HTTP 拉取通道：N 文件 / X MB`）；`MAAORCH_SNAPSHOT_PULL=0` 会强制回退到慢通道，非必要别开 |
| 主机出现僵尸 `powershell`（卡死残留） | 逐 pid 核对命令行含 `-EncodedCommand` 且为 wrapper 再处置；历史事故与清理方法见 `docs/audits/2026-09-28_mas_config_snapshot_transport.md` |
| 本地 `--to` 回滚报「敏感文件无本地全保副本」 | 设计行为：ScriptConfig 只能走主机 store（`--remote-root` 演练或真回滚） |
| `SNAPSHOT_DRIFT=UNKNOWN reason=scan-failed` | ssh 通道挂了；先跑 `--selftest` 与一次 `--list` 确认基础连通 |

## 8. 相关文档

- `docs/ROLLBACK_DRILL.md` — 11 行变更清单的回滚矩阵 + 回滚卡片（含锁残留事故 F1）
- `docs/audits/2026-09-28_mas_config_snapshot_drill.{txt,json}` — 本工具 `--drill` 首份全绿证据
- `docs/audits/2026-09-28_mas_config_snapshot_transport.md` — stdin 死锁调查 + HTTP 拉取通道定稿
- `docs/TOOLBOX.md` §备份/回滚 — 工具箱索引
