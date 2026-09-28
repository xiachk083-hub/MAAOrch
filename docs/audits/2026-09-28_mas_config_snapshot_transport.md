# 调查：drill 阶段2 卡死 —— Windows sshd 大 stdin 载荷随机死锁（2026-09-28）

结论先行：`mas_config_snapshot.py` 旧的「上传走 ssh stdin 单次大 payload」在**本机↔AUTO-MAS 宿主**这条
链路上会**随机死锁**（与载荷大小正相关），是首版 `--drill` 阶段2 卡死（120/686 文件落盘后无输出 6.5min、
直至 worker 被回收）的根因。已改为 **HTTP 拉取通道（主）+ stdin 小分片重试（兜底）**，drill 全绿。

## 1. 现象与现场

- 首版 drill（`--against 20260928_092944__baseline`，09:32:34 起跑）：阶段1 全过；09:34:13 进入阶段2 后
  **再无任何输出**，09:40:57 进程被杀（退出码 `1073807364`=DBG_TERMINATE_PROCESS）。日志：
  `runtime/snapshots/_drill_run.log`（崩溃版，保留）。
- 主机侧现场：scratch `E:\AUTO-MAS\backups\_cfgsnap_drill_20260928_093234__drill` 只落了 **120 文件
  （60+60，两批）**；主机时钟比本机**慢 20s**（对比 `Get-Date` 实测），此前把批时间戳看串了 19 秒。
- 主机遗留 **38 个僵尸 `powershell.exe`**（全部为同款 wrapper 进程，CreationTime 横跨 09:33→10:20，
  即历次卡死残留）；`sshd` 常驻仅 1 个。

## 2. 判定实验（本机 Python 直调同一 wrapper）

实验脚本均用工具自身的 wrapper（`ssh -i … host powershell -NoProfile -EncodedCommand <wrapper>`，
payload 走 stdin），逐条计时：

| 实验 | 结果 |
| --- | --- |
| 1.6MB payload（空转脚本只回显长度） | **0/16 成功**（全部 20-40s 超时；超时后主机侧 wrapper 进程永久卡死） |
| 256KB | 3/6（另 3 次卡死） |
| 128KB | 4/6 |
| 64KB | 5/6（首轮 sweep 曾 6/6 全卡，重测又恢复——随机性明显） |
| 4KB | 6/6 成功 |
| 脚本本体无 payload（script-only 调用） | 上百次全部正常（快照枚举/哈希/回滚清点都走这条路） |

排除项（全部无效）：
- 换 Windows 自带 `C:\Windows\System32\OpenSSH\ssh.exe`：**4KB 就卡**（该客户端与本宿主 sshd 组合更糟）。
- stdin 改从**文件**喂入（`stdin=open(f,'rb')`，管道→文件句柄）：1.6MB `0/10`，无改善。
- 卡死定位（marker 实验）：远端脚本体**第一行都没执行**（marker 文件未创建）→ 卡在 wrapper 读 stdin
  （`[Console]::In.ReadToEnd()`）之前/之中，即**数据根本没送达子进程**。
- `scp`/`sftp`：宿主未启用 SFTP 子系统（`scp: Connection closed`），不可用。

## 3. 修复（已落地于 `tools/mas_config_snapshot.py`）

**HTTP 拉取通道（主）** —— `remote_pull_zip()`：
1. 本机把 `{目标绝对路径: bytes}` 打成 zip（条目 `f/NNNN` + `manifest.json` 内嵌 `target/sha256/size`；
   Python `zipfile`，无外部依赖）；
2. 本机起一次性 HTTP 服务（绑「对主机方向的本机出口 IP」（UDP connect 探测，tailnet 场景
   `100.111.209.108`）、随机端口、随机 token 路径，非授权路径 404；`log_message` 静默）；
3. 主机 `Invoke-WebRequest` 拉 zip → `Expand-Archive` 到 scratch → 逐文件
   `Copy → .cfgsnap.tmp → Move-Item -Force`（原子）→ `Get-FileHash` 比对内嵌 sha；
4. 回执 `W|<目标>|ok|<sha>` / `E|…`，与旧通道同格式；通道级失败抛 `TransportError`。

**stdin 小分片（兜底）** —— `remote_write_stdin()`：片 ≤48KB 原始（base64 后 ≈64KB）、每片 ≤4 次尝试、
单次尝试超时 30s；仅在 HTTP 通道不可用/有失败项时自动接管（可用 `MAAORCH_SNAPSHOT_PULL=0` 强制）。

**通道实测**（修复后）：
- `remote_write` 直测：3 文件/1.06MB，**2.5s**，逐文件哈希与远端复读**一致**，清理 OK；
- drill 阶段2：685 文件/7.98MB → zip 1.91MB → 主机解包落盘 + 686/686 校验，**≈11s**；
- 兜底通道（强制 `=0`）：6 文件 **2.2s** 全过（小分片策略生效）。

## 4. 运维备注

- 卡死过的调用会在主机留**僵尸 powershell**（每卡一次 +1）。清理口径：只杀命令行含
  `-EncodedCommand` 且命中 wrapper 标记的进程，逐 pid 核对后再处置（本机侧用 python + CIM 筛，
  勿用通配杀，避免误伤 AUTO-MAS 自身进程）。
- 若未来再遇「回滚/物化挂住 >2min 无输出」，先看日志有没有 `HTTP 拉取通道：N 文件 / X MB` 行；
  没有则该跑的是旧路径或强制回退了，按 `docs/MAS_CONFIG_SNAPSHOT.md` §7 排障。
- 本调查遗留的主机测试文件（`_probe_marker*.txt`、`_http_dl*.bin`、`_scp_probe\`）与本地临时探针脚本
  **已清理**；主机 `_cfgsnap_drill_probe_*` 与崩溃遗留 `_cfgsnap_drill_20260928_093234__drill`
  由 drill「阶段0 残留自清」（卡 t_6fa92a0a）与本次清理共同清零。
