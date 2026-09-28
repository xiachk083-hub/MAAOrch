# host_health.py —— 主机健康巡检：CPU / 内存 / 磁盘 / 进程 / 温度 + 趋势 + 预警

> 主机 `MRFZ-0000`（100.79.173.69）24×7 跑模拟器（机队 54 槽；任务设定 ~12 台在跑），**硬件/资源异常会直接打断班次**。
> 本工具补上「**一根一行的取数线**」：一轮采齐关键指标 → 固定格式一行 `HOST_HEALTH=` → 按阈值出 WARN/CRIT → 趋势取自本机历史。
>
> 文件：`tools/host_health.py` ｜ 历史：`runtime/host_health.jsonl` ｜ 状态（--notify 用）：`runtime/host_health_state.json`
> 基线实测证据：`docs/audits/2026-09-28_host_health_baseline.txt`
>
> **只读约束**：远端只跑一条只读 PowerShell（CIM 查询 / Get-Process / adb devices），不留文件、不动配置、**不装第三方监控软件**；本机只追加 jsonl（`--no-log` 可关）。

**与姊妹工具的分工**（各有盲区，互补不重复）：

| 工具 | 只管 | 输出 |
|---|---|---|
| `host_watchdog.py` | 活没活（ICMP + REST 36163 口；连续 3 次失败发信，间隔 5 分钟） | 通知 / `runtime/watchdog.log` |
| `host_perf.py` | 内存专项告警（阈值 `watchdog.ini [perf] free_mem_alert_mb`，带 MB/分钟趋势） | `runtime/host_perf.log` |
| `storage_waterline.py` | 存储水位深度体检（**预算视角**：还能不能建实例） | 水位表 / `--json` |
| **host_health.py（本文）** | **趋势 + 温度 + 进程 + 一行取数**（**运行红线视角**："马上会不会出事"） | 末行 `HOST_HEALTH=` |

## 1. 用法

```bash
python tools/host_health.py                # 详表（含磁盘/内存趋势 + 进程窗口）+ 末行 HOST_HEALTH=
python tools/host_health.py --check        # 紧凑（巡检 cron 用），末行 HOST_HEALTH=
python tools/host_health.py --json         # 机器可读（含 line 字段）
python tools/host_health.py --selftest     # 纯本地逻辑自检（不连网）
```

选项：`--host HOST`｜`--key KEY`｜`--timeout S`（ssh 超时，默认 90）｜`--window-h H`（趋势窗口，默认 6）｜`--no-log`｜`--history PATH`｜`--notify`
配置：主机/内存阈值读 `runtime/watchdog.ini`（`[host] host`、`[perf] free_mem_alert_mb`）；env 覆盖 `MAAORCH_MAS_HOST` / `MAA_SSH_KEY`；默认 key `~/.ssh/maaorch_target`。

**退出码**：`0` 全绿 ｜ `2` 有 WARN ｜ `3` 有 CRIT ｜ `1` 取数失败（ssh 不通 / 输出不可解析 ⇒ 行内 `ssh=down`、`status=DOWN`）。与 `storage_waterline.py`/`mas_switch_gate.py` 同语义。

实测样例（2026-09-28 09:48，当前基线）：

```
HOST_HEALTH=cpu=21%,mem=27.1%,diskC=29.7G,diskE=147.4G,emu=3,maa=2,ssh=ok,temp=na,memFree=47630M,rateE=na,alerts=diskC_low,status=WARN
```

## 2. 采集项与来源（全部只读）

| 指标 | 来源（远端单条只读 PowerShell） | 备注 |
|---|---|---|
| CPU | `Get-Counter '\Processor(_Total)\% Processor Time'` 单采样；计数器坏 ⇒ 回退 `Win32_Processor.LoadPercentage` 平均 | 单采样，噪声大，只做 WARN |
| 内存 | `Win32_OperatingSystem`（TotalVisibleMemorySize / FreePhysicalMemory） | 已用%由两者算出 |
| 磁盘 C:/E: | `Win32_LogicalDisk`（FreeSpace / Size） | GB 保留 1 位 |
| 模拟器进程数 | `Get-Process MuMuVMMHeadless` | **只认 VMM 进程**：`MuMuManager info -v` 状态面在主机上假死（全实例报 `is_process_started=false`）；死活判定三件套=VMM 进程→监听口→`adb devices`（MAS_EMULATORS §1-3）。NxDevice/NxMain 计数另入 JSON |
| MAA 进程数 | `Get-Process MAA` | 全主机计数（谁在飞看进程命令行） |
| adb 在线设备 | `E:\MuMu Player 12\nx_main\adb.exe devices` | 路径 Test-Path 存在才跑；缺失则 `adb=-` |
| 温度 | ① `MSAcpi_ThermalZoneTemperature`（root/wmi）② `root/OpenHardwareMonitor` 命名空间 | **2026-09-28 实测主机两路都不可用**（0x8004100c 类不受支持 / 0x8004100e 命名空间无效）⇒ `temp=na`；na 不告警（未知不当通过、也不当故障）。要温度需装第三方探针——与「不装第三方监控软件」约束冲突，需用户拍板 |
| SSH 可达性 | ssh 本身成功与否 + `HH-BEGIN/END` 哨兵 | 不通 ⇒ 整行降级 `ssh=down` |
| 系统信息 | `Win32_OperatingSystem`（CSName / LastBootUpTime → uptime_h） | 详表头行 |
| 时间锚 | 主机钟 `TS` | 与调度机钟对不上时以主机钟为准 |

## 3. 巡检固定格式（`HOST_HEALTH=` 行）

逗号分隔的 `k=v` 序列。**前六个字段顺序固定**（cpu/mem/diskC/diskE/emu/maa，对齐任务口径），扩展字段只允许往后追加（巡检按前缀摘取时不受影响）。

| 字段 | 示例 | 含义 | 缺数写法 |
|---|---|---|---|
| cpu | `21%` | CPU 单采样 | `-` |
| mem | `27.1%` | 内存已用（1 位小数） | `-` |
| diskC | `29.7G` | C: 剩余 GB | `-` |
| diskE | `147.4G` | E: 剩余 GB | `-` |
| emu | `3` | MuMuVMMHeadless 进程数 | `-` |
| maa | `2` | MAA 进程数 | `-` |
| ssh | `ok` / `down` | 取数通道 | — |
| temp | `na` / `56.5C` | 温度（不可得=na） | `na` |
| memFree | `47630M` | 可用内存 MB | `-` |
| rateE | `na` / `-2.3G/h` | E: 趋势（GB/h；负=缩水） | `na` |
| alerts | `none` / `diskC_low;mem_used` | 告警 code（分号分隔） | `none` |
| status | `OK`/`WARN`/`CRIT`/`DOWN` | 汇总（DOWN=取数失败） | — |

取数失败时整行形态（selftest 固化）：

```
HOST_HEALTH=cpu=-,mem=-,diskC=-,diskE=-,emu=-,maa=-,ssh=down,temp=na,memFree=-,rateE=na,alerts=ssh_down,status=DOWN
```

## 4. 阈值与依据（改阈值 ⇒ 同步改本节 + 脚本顶部 `THR`）

| 项 | WARN | CRIT | 依据 |
|---|---|---|---|
| 内存已用 | ≥85% | ≥90% | 任务口径 mem>90% 报警；64G 机的 VMM 随在跑实例增长，先差 5pp 提醒 |
| 内存可用 | <8192MB | — | 与 `watchdog.ini [perf] free_mem_alert_mb`、`host_perf` 同口径（既有拍板值） |
| CPU | ≥95%（单采样） | — | 跑图高峰高 CPU 属正常，只提示不升级；持续高再查是否叠加并发 |
| C: 剩余 | <35G | <15G | 一次「实例导出/克隆」在 `C:\Users\Public\mumu` 落 ~24GB（storage_policy §0）；低于「导出一次即顶满」的位置即黄。与 `storage_waterline` host_C 同值 |
| E: 剩余 | <60G | <20G | 单实例镜像 ≈29–32GB（storage_policy §0/§4.2）；60G≈「还能装 2 个」的兜底线。**20G = 任务红线**（放不下新实例，日志/截图增长下很快见底） |
| E: 缩水速率 | ≤ -2GB/h | — | 稳态应≈0；小时级持续缩水多为泄漏（debug 截图/回收站/导出残件）。**克隆/导出实例（+30GB 一次性）属预期**——看方向别只看斜率 |
| E: 跌破预期 | 24h 内 <20G | — | 慢速也有份：按当前速率 24h 内会到 CRIT 就提前黄 |
| 温度 | ≥85℃ | ≥92℃ | 桌面 CPU TJmax ~95–105℃，持续 ≥92 触发降频/保护；**仅 WMI 可得时生效**（本机 na，不评估） |
| 进程对 | emu>0 且 adb=0 → 设备链路断；maa>0 且 emu=0 → 模拟器起不来 | — | 取证口径同上（只信 VMM + adb） |
| SSH | — | 不通即 CRIT | 通道断 = 全部下游工具失能 |

**与 storage_waterline 的口径差（不是冲突）**：本工具是**运行红线**（"马上出事没有"），waterline 是**预算水位**（"还能不能建下一个实例"：E<120/60、C<35/15）。本工具 E<60 的 WARN 恰等于 waterline 的 CRIT——两边都在说同一句「该清了」。巡检汇报时两行可并存，别当矛盾。

## 5. 趋势（本地历史）

- 每次运行（除 `--no-log`）向 `runtime/host_health.jsonl` 追加一行：

```
{epoch, ts, host, ssh, cpu_pct, mem_total_mb, mem_free_mb, mem_used_pct,
 disk_gb{C:,E:}, emu, maa, adb, temp_c, temp_src, status, alerts[]}
```

  （文件 >2MB 自动裁剪到后 5000 行；`runtime/` 已 gitignore，数据不入库）
- **速率类**（diskC / diskE / memFree）：窗口内最小二乘斜率；**跨度 <20min 不出数**（防噪声）⇒ 部署后约 20 分钟起 `rateE` 才可能有值。`--window-h` 可调（默认 6h）。
- **进程窗口**：emu/maa/adb 是步进量，报窗口内 min/max/首末（详表「进程趋势」行），不报斜率。
- jq 读法：

```bash
# 最近 8 行：E 盘余量 + 进程数
tail -8 runtime/host_health.jsonl | jq -r '[.ts, .disk_gb["E:"], .emu, .maa] | @tsv'
# 近 N 行区间内 E 盘平均缩水速率（GB/h；>0 才在增长）
tail -20 runtime/host_health.jsonl | jq -s 'if length>1 then ((.[-1].disk_gb["E:"] - .[0].disk_gb["E:"]) / ((.[-1].epoch - .[0].epoch)/3600)) else empty end'
```

## 6. 处置建议（按 `alerts` 里的 code）

| code | 先做（只读） | 处置 | 参照 |
|---|---|---|---|
| `diskC_low` | `python tools/storage_waterline.py --json` 看大头 | 可回收：`C:\Users\Public\mumu\*.mumudata` 49G、C: 回收站 6G、DXCache 5.4G、Photoshop 包 5.45G（≈66G）。清理先 `-WhatIf`、**避开班次窗口 05:00–08:00 / 19:30–23:00** | storage_policy §5/§6 |
| `diskE_low` / `diskE_rate` / `diskE_eta` | 同上 + E: 明细 | E: 回收站 42.3G、退役实例 l-3 30.5G、退役 MAA_13/14/25/28 2.5G（≈75G）。**克隆/导出中属预期**，等完成再看 | storage_policy §5/§6 |
| `mem_used` / `mem_free` | `runtime/host_perf.log` 看 MB/分钟趋势与「还有多久跌破」 | 跑完的号越多 VMM 越大；持续恶化排维护窗口重启（04:00/19:00 计划重启前后） | host_perf / MAS_EMERGENCY_RUNBOOK |
| `cpu_high` | 核对 VMM 实例数（`emu`）与在飞 MAA | 无人为叠加的持续高 CPU ⇒ 查实例/客户端假死（F3） | runbook §3.3 |
| `temp_high` | —— | 清灰/风道/环境温度，降载；本机恒 na，此告警暂不会出现 | 本文 §2 |
| `emu_adb` | 「死活三件套」逐级：VMM 进程 → 监听口 → `adb devices` | 别信 `MuMuManager info -v`；重连/重启该实例（走 MuMu 流程） | MAS_EMULATORS §1-3 |
| `maa_no_emu` | AUTO-MAS 侧车状态 | REST 挂 → F1；侧车假死 → F2（查 `app.log` 尾） | runbook §0.1 / §3.1-3.2 |
| `ssh_down` | 本机 ping / REST 0.1-A、0.1-B | 网络/主机故障；`host_watchdog` 连续 3 次失败会发信（5 分钟/次），此处只是巡检侧同报 | runbook §0.1 |

> 纪律：本工具**只取数与报警**。判因/动手按上表走对应文书，不要凭 `HOST_HEALTH=` 一行就下结论。

## 7. 接线（当前未接；留给巡检/看板线拍板）

- 30 分钟巡检（cron `52b88b0ffca0`）的固定格式已有 `DISK=` / `POWER=` 行；建议并列追加一行 `HOST_HEALTH=…`（或把 `alerts=` 折进 DISK 行）。**本卡未改 cron**（改 cron 提示词属巡检线）。
- 独立 cron 亦可：每 30 分钟 `--check`；注意「deliver=local 没人看」（G11）——需要人看到就走告警通道。
- `--notify`（默认关）：CRIT 发一封、恢复发一封，60 分钟重提醒；通道复用 `host_watchdog` 的 `watchdog.ini`（email / serverchan / telegram / dingtalk）。

## 8. 自测与基线（2026-09-28）

- `--selftest`：**36/36 PASS**（解析 / 阈值 / 趋势斜率 / 进程窗口 / 固定格式 / ssh-down 行）。
- 实跑 `--check`（09:48，rc=2）：`cpu=19%,mem=27.2%,diskC=29.7G,diskE=147.4G,emu=3,maa=2,ssh=ok,temp=na,memFree=47597M,rateE=na,alerts=diskC_low,status=WARN`。
  **当前唯一黄灯 = `diskC_low`**（C: 29.7G < 35G）；E: 147.4G 距 60G 黄线尚远；内存 27% 健康。
- 温度来源核查：ACPI 热区类不受支持（0x8004100c）、OHM 命名空间不存在（0x8004100e）⇒ `temp=na` 属实。
- 原始证据（selftest 全量 + check + json）：`docs/audits/2026-09-28_host_health_baseline.txt`。

## 9. 维护

- 阈值集中在 `tools/host_health.py` 顶部 `THR`；改动必须同步本文 §4（文档-实现一致性核验会查）。
- `HOST_HEALTH=` 行是**接口**：前六个字段顺序不可变，扩展只追加。
- 历史只在本地 `runtime/`；`--history` / `--no-log` 供测试。
- 巡检若接入：摘行模板 `HOST_HEALTH=<整行原样>`，末行永远是该行（详表/紧凑/JSON 三种模式都保证）。
