# MAS 端口规律与漂移史（权威口径）

> 建：2026-09-28 ｜ 任务 `t_ee50913f` ｜ 性质：**只读实测**（未启动/未关闭实例、未改配置）
> 配套工具：`tools/mas_mapping.py`（`--build/--verify/--port/--lookup`）｜机器可读产物：`runtime/reports/mapping.{json,md}`
> 关联：`docs/MAS_EMULATORS.md`（09-27 实例档案；本文件修正其 §2.1 一处结论）、`docs/MAS_SCHEDULE_MAP.md`（槽位/队列）

## 0. 三句话

1. **端口确实会漂，而且不必「重建实例」**：2026-09-28 实测 idx27 未重建（`extra_config.createTime` 仍是 2026-01-21），
   adb 主口从 **17248 → 17249**（`vm_config.json` 07:57:24 落盘，MAA 07:58:30 跟着连新口）。
   09-27 的档案口径「重启不改口、漂移只在重建时」**至少被这一个反例打破**——执行前现查才算数。
2. **拿口的正确顺序**：在跑 → `adb devices`（主口/副口都会列出来，最省事）；任意（含停着）→ 读该实例
   `vm_config.json` 的 `vm.nat.port_forward.adb.host_port`（实例上持久化的实测值）；批量 → `tools/mas_mapping.py --build`。
   **公式只当基准**（已被 idx27 打破），文档旧值当历史。
3. **两个端口家族都通，但语义不同**：主口（1·A/1·B 族，MAA 实际连的）/ 副口 `5555+2·idx`（运行时存在；
   AUTO-MAS REST 对**停着的**实例报的就是它）。7555 是遗留赃口，别依赖。

## 1. 端口家族全表

| 名称 | 公式 | 存在期 | 谁在用 | 实测（2026-09-28 10:08） |
|---|---|---|---|---|
| 主口（家族 A） | `16384 + 32×idx`（idx ≤ 31） | 持久（实例上） | **MAA 实际连接**（asst.log `"what":"Connected"`）、MAA 槽位 `gui.new.json` 的 Address | 31 台逐台吻合 |
| 主口（家族 B） | `16388 + 32×(idx−32)`（idx ≥ 32）= 旧公式 −1020 | 持久 | 同上 | 24 台逐台吻合 |
| 副口 | `5555 + 2×idx` | 仅运行时 | AUTO-MAS REST 对**停着的**实例的 `adb_address`；天然备用端点 | 55 台逐台吻合 |
| 7555 | — | 偶发 | MuMu 默认 adb 口遗留占用 | 见 §2.4 |

> 例外（写死公式会踩的坑）：**idx27 = 17249**（公式 17248，+1 顺延）；本机（DESKTOP）实例还有
> `emulator-5554 → emulator-5556` 的别名漂移（见 `docs/ROGUE_WATCH.md`）。**逐台核对请用 `--build` 表，别用公式。**

## 2. 实测语义细节（每条都影响「拿哪个口」）

### 2.1 五个来源，谁说了算

| 来源 | 内容 | 可信度 | 时效 |
|---|---|---|---|
| `adb devices`（主机侧 adb server） | 当前在线端点（**主口+副口都列**，state=device/offline） | ★★★ 最强（活体） | 实时 |
| `vms\MuMuPlayer-12.0-<idx>\configs\vm_config.json` → `vm.nat.port_forward.adb.host_port` | 该实例 adb 主口（实例上持久化） | ★★★ 实测持久值 | 每次实例启动可能被重写（见 §3） |
| VMM 进程 + `Get-NetTCPConnection`（`MuMuVMMHeadless --comment MuMuPlayer-12.0-<idx>` 的监听口） | 在跑实例的主口+副口 | ★★★ 活体，但只在跑 | 实时 |
| 公式（家族 A/B） | 期望值 | ★☆ 仅基准 | 已被 idx27 打破 |
| 文档（MAS_EMULATORS.md 等）、槽位 `gui.new.json` 的 Address、asst.log 旧行 | 历史快照 | ★ 仅参考 | 可能过期 |

**MAA 侧的两个「证据面」要分清**：
`gui.new.json` 的 `Gui.ConnectSettings.Address` = 最近一次**注入**值（可能是上一轮的号，不代表当前）；
`debug\asst.log` 的 `"what":"Connected"` 行 = MAA **真的连上过**的口（最强运行证据，带时间戳）。
`tools/mas_mapping.py --build` 两个都收进槽位表（主表 §2）。

### 2.2 AUTO-MAS REST 的 adb_address 语义（2026-09-28 实测）

`POST /api/emulator/status` 的 `data.<emuId>.<idx>.adb_address`：
**在跑的实例报主口、停着的实例报副口**（当次实测：2 台在跑=主口，53 台停着=副口；此后固定为
「要么主口要么副口」，两族之外才需警觉）。它的 `status` 字段（恒 1）与 MuMuManager 的
`is_process_started`（恒 false）都**不可用于判死活**——死活只信 VMM 进程 + `adb devices`。

### 2.3 「设备N未在线」/`device offline` 噪音

关实例瞬间会打印「设备N未在线，当前状态:1」与 `adb.exe: device offline`；`adb devices` 里也会短期
残留 `offline` 条目（甚至实例删了还在）。**不是漂移、不是故障**——别照着旧条目去连。判据：
同一端口连续成簇失败 + 实例确在跑，才升级处理。

### 2.4 偶发 7555

同一时刻只有一台实例监听 7555（MuMu 默认 adb 口的遗留占用）。`MAA_6` 的历史 asst.log 里出现过 7555 连接。
**勿依赖、勿手连**：排除它，用该实例的主口/副口。

## 3. 漂移史（留证）

| 时间 | 对象 | 事实 | 证据（可复跑） |
|---|---|---|---|
| ≤2026-09-27 | idx24（日-1） | 记录冲突：`5603` 与 `17152` 并列，曾被判「17152 已失效」 | 实为副口/主口**并存**，见 `docs/MAS_EMULATORS.md` §2.2 |
| 2026-09-27 06:38–06:43 | idx27（官-12，槽位 MAA_12） | MAA 连 **127.0.0.1:17248** | `MAA_12\debug\asst.log`（`"address":"127.0.0.1:17248"`） |
| **2026-09-28 07:57:24** | idx27 | `vm_config.json` 重写，adb 主口=**17249**；`extra_config.createTime` 仍是 2026-01-21（**未重建**） | `vms\MuMuPlayer-12.0-27\configs\vm_config.json` 的 mtime + `extra_config.json` |
| **2026-09-28 07:58:30** | idx27 | MAA 首连 **127.0.0.1:17249**（同一 `uuid`=同一 MAA 配置，跟随新口） | `MAA_12\debug\asst.log` |
| 2026-09-27 21:26 档案 | idx27 | `docs/MAS_EMULATORS.md` 写 17248（公式吻合） | 该文档汇总表 idx27 行 |
| 2026-09-28 10:08 | 全机队 | 55 台逐台实测：仅 idx27 与公式不符（17249）；其余 54 台与家族 A/B 完全吻合 | `runtime/reports/mapping.md` §3（公式列 ✗ 只出现在 27） |
| 本机（DESKTOP） | 实例 #0 | `emulator-5554 → emulator-5556` 别名口漂移（同实例换口，5554 上什么都没了） | `docs/ROGUE_WATCH.md`（含复核命令） |

**机制（假设，未证伪）**：主口由 MuMu 在实例**启动时**选定并持久化；首选值=家族公式值，若该口被占用
（其他进程/上一会话残留）则顺延（+1）。「同一实例重启不改口」是**常态而非保证**。
下次遇到 +1 类漂移，可顺手留证：`netstat -ano | findstr 1724`（旧的 17248 被谁占着）。

## 4. 怎么最快拿到正确端口（速查）

按「你要什么」选：

```
# ① 单实例现查（推荐；vm_config + VMM + adb 三证，2~8s）
python tools/mas_mapping.py --port 27          # → 127.0.0.1:17249

# ② 手工（不想跑脚本）：在跑实例一条命令，主口/副口都在里面
ssh <host> '"E:\MuMu Player 12\shell\adb.exe" devices'

# ③ 停着的实例：读它自己的 vm_config（一条 ssh；路径 E:\MuMu Player 12\vms\MuMuPlayer-12.0-<idx>\configs\vm_config.json）
#    看不到 adb 段就找 "host_port"（在 vm.nat.port_forward.adb 下）

# ④ 批量/全机队 + 校验（30s，只读）
python tools/mas_mapping.py --build           # → runtime/reports/mapping.md（54 行主表）
python tools/mas_mapping.py --verify          # → MAPPING_VERIFY=OK|DRIFT:<n>（末行判据）

# ⑤ 脚本里要用（离线，不联网）：读上次建的表
python -c "import sys;sys.path.insert(0,'tools');import mas_mapping as m;print(m.serial_for(idx=27)['serial'])"
```

**不要做**（每条都有实锤教训）：
- 按公式写死口（idx27 已反例）；拿文档里的旧口直接连（09-27 的 17248 已失效）。
- 信 `MuMuManager info -v` 的 `is_process_started`（全机队恒 false）、`MuMuManager adb -v N --cmd connect`
  （恒 -201）、AUTO-MAS `/api/emulator/status` 的 `status`（恒 1）。
- 手动去连 7555 或 `adb devices` 里的 `offline` 残留条目。
- 把「槽位记忆地址」（`gui.new.json` Address）当成当前要用的口——它是**上一轮**注入值；
  要「MAA 真连过哪个口」去看 `asst.log` 的 `Connected` 行。

## 5. 复现命令（采集面，全部只读）

```
# 逐台主口（PowerShell；块必须一行——见下节坑）
ssh <host> 'powershell -NoProfile -Command "Get-ChildItem ''E:\MuMu Player 12\vms'' -Directory | ForEach-Object { $m=[regex]::Match($_.Name,''MuMuPlayer-12\.0-(\d+)$''); if($m.Success){ $c=Join-Path $_.FullName ''configs\vm_config.json''; if(Test-Path $c){ $j=Get-Content $c -Raw | ConvertFrom-Json; Write-Output ($m.Groups[1].Value + '' '' + $j.vm.nat.port_forward.adb.host_port) } } }"'

# 在跑实例 VMM 监听口
ssh <host> 'powershell -NoProfile -Command "$v=@(Get-CimInstance Win32_Process -Filter \"Name=''MuMuVMMHeadless.exe''\"); foreach($p in $v){ if($p.CommandLine -match ''MuMuPlayer-12\.0-(\d+)''){ $i=$matches[1]; $ps=@(Get-NetTCPConnection -State Listen -EA SilentlyContinue | Where-Object {$_.OwningProcess -eq $p.ProcessId} | ForEach-Object {$_.LocalPort} | Sort-Object -Unique); Write-Output ($i + '' '' + ($ps -join '','')) } }"'

# 实测汇总（本文件数据来源，一条命令）
python tools/mas_mapping.py --build --json
```

**两个跨 SSH 的坑（写给下一个 agent）**：
1. 经 ssh 用 `powershell -NoProfile -Command -`（stdin 喂脚本）时，**以 `{` 结尾的行开出的多行块会被静默吞掉**
   （rc=0、零输出）。要么把块写在一行，要么用 `-EncodedCommand`（UTF-16LE base64）——`tools/mas_mapping.py`
   两种写法都做了（见其 `ssh_ps()`）。
2. 调用 ssh 的子进程必须 `stdin=DEVNULL`，否则会挂在调用方管道上（MCP stdio 场景实测）。

## 6. 与工具/文档的接口

- 表与校验：`tools/mas_mapping.py --build/--verify/--port/--lookup/--selftest`；产物 `runtime/reports/mapping.{json,md}`
  （schema `maaorch.mapping/1`；含 findings/changes，可被巡检直接读）。
- 库接口（供其它工具 import）：`fetch_live()` / `build_doc()` / `load_mapping()` / `serial_for(idx=|script=|slot=)` /
  `live_instance(idx)` / `expected_port(idx)`。例：preflight 想拿口 → `serial_for(idx=n)`（离线）或 `live_instance(n)`（现查）。
- 巡检可 grep 的末行：`MAPPING_VERIFY=OK|DRIFT:<n>|UNKNOWN`（rc 0/1/2）。

---

*本文件由只读实测生成（2026-09-28 10:0x–10:2x，中国标准时间）：55 实例 / 54 脚本 / 16 槽位 / 40 队列。*
