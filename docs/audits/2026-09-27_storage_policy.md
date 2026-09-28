# 2026-09-27 存储水位与清理策略（AUTO-MAS 主机 + 本机 MAAOrch）

- 任务：t_880f33e1「主机资源与日志水位监控 + 清理策略（只出策略）」
- 数据时间：2026-09-27 20:35–21:15（CST）；全部**只读**取数，本报告不含任何删除动作。
- 复现：`python tools/storage_waterline.py`（本任务新增的只读巡检器，见 §7；同一份数据 90 秒内可复现）
- 主机：`Administrator@100.79.173.69`（MRFZ-0000）；本机：MAAOrch 跑批机（DESKTOP-L1OEMR3）

---

## 0. 一句话结论

**会被撑爆的不是日志，是镜像。** 日志侧全部有界（MAA 内建轮转、AUTO-MAS 周轮转+保留 1 个月），
真正咬空间的是 **MuMu 实例镜像 ≈ 1.59TB / 55 个**（占主机 E: 已用量的 92.5%）：
8 月新建 14 个（+398GB）、9 月新建 7 个（+199GB），而 **E: 现在只剩 143.8GB ≈ 再建 3 个实例**；
主机 C: 只剩 **29.7GB**，而一次「实例导出」会在 `C:\Users\Public\mumu` 落 ~24GB 的 `.mumudata`
（现在那里正躺着 2 个 2025-12 的残件 = 48.95GB）。**先把可回收的 ≈143GB 拿回来（头部两件 91GB：回收站 42GB + 导出残件 49GB），
并给「建实例」加空间闸门**，日志清理只是防增长的小项。

---

## 1. 当前水位表（2026-09-27 21:1x 实测）

### 1.1 磁盘

| 盘 | 总 | 剩余 | 占比 |
|---|---|---|---|
| 主机 C: | 238.5 GB | **29.7 GB** | 12.5% 可用 |
| 主机 E: | 1863 GB | **143.8 GB** | 7.7% 可用 |
| 本机 C: | 510.8 GB | 101.9 GB | 20.0% 可用 |
| 本机 D: | 1024.2 GB | 59.2 GB | 5.8% 可用 |

### 1.2 主机 E: 的大头（1.7TB 已用）

| 路径 | 体积 | 性质 |
|---|---|---|
| `E:\MuMu Player 12\vms` | **1590.8 GB / 55 实例** | MuMu 实例镜像（每个 `data.vdi` ≈ 25–35GB） |
| `E:\$RECYCLE.BIN` | **42.29 GB / 327,195 文件** | 回收站（其中 2025-12 的 26.4GB 是旧垃圾） |
| `E:\maa-alas` | 13.6 GB | `fz-maa` 10.8GB（**活跃**，09-27 18:29 仍在写）+ AzurLaneAutoScript 2.5GB（06-03 后没动）+ MaaEnd 0.26GB（05-08 后没动） |
| `E:\MAA-Multi` | 11.3 GB / 172,608 文件 | 16 个 MAA 目录（12 个在用 + 4 个退役，见 §1.5） |
| `E:\MAALoad` | 8.5 GB | T01–T06 多份装载副本（09-26 05:14 有写，待确认） |
| `E:\XIA` | 5.5 GB | 百度网盘/夸克用户数据（**用户自己的**） |
| `E:\MAA-ALL` | 4.9 GB | 旧 MAA+MAAS 混合目录（最近一次写入 09-22 06:50） |
| `E:\pagefile.sys` | 32 GB | 系统页面文件（别动） |
| `E:\MAA-Test` / `E:\maadl` / MAA 安装包 zip | 0.5 / 0.26 / 0.26 GB | 待确认的旧物 |
| `E:\AUTO-MAS` | 1.27 GB | 程序本体（`.exe` 195MB）+ `history` 210MB + `debug` 25.6MB |

### 1.3 主机 C: 的大头（208.8GB 已用）

| 路径 | 体积 | 说明 |
|---|---|---|
| `C:\Users\Public\mumu` | **55.7 GB** | 2 × `.mumudata`（25.9 + 23.1GB，**2025-12 的实例导出残件**）+ Photoshop 安装包 5.45GB + 截图散件 |
| `C:\Users\Administrator\Videos\NVIDIA` | 44.0 GB | NVIDIA 桌面录像 DVR.mp4（2025-04 ~ 2025-08，**用户自己的**） |
| `C:\Users\Administrator\.lmstudio` | 16.9 GB | LM Studio 模型 14.8GB（**用户自己的**） |
| `C:\Users\Administrator\AppData\Local\NVIDIA\DXCache` | 5.4 GB | 着色器缓存（**可重建**） |
| `C:\Users\Administrator\AppData\Local\Google\Chrome` | 5.3 GB | 浏览器数据 |
| `C:\Windows` | 17.8 GB | 系统 |
| `C:\ProgramData`（含 NVIDIA Corporation 3.5GB） | 6.0 GB | 驱动包 |
| `C:\$RECYCLE.BIN` | 6.0 GB / 67,940 文件 | 回收站 |
| `C:\Windows\SoftwareDistribution\Download` | ~0 | 当前无更新堆积 |

### 1.4 日志/历史 明细（可清部分都在这）

| 项 | 现值 | 轮转方式（观测/源码） | 上限 |
|---|---|---|---|
| MAA `debug\asst.log` + `asst.bak.log` | 每槽 3–64MB / 64MB | MAA 内建：到 **64.0MB** 轮转，保留 1 份 `*.bak.log`（16 个目录全部吻合） | 每槽 128MB |
| MAA `debug\gui.log` + `gui.bak.log` | 每槽 ~2–4MB / ~4MB | 同上，阈值 ~4MB | 每槽 8MB |
| MAA 错误截图 `debug\{drops,Fight,infrast,interface,other}\*_raw.png` | 合计 **627MB / 774 张**（16 槽） | **无清理**（只按错误事件生成，~1MB/天·槽） | 无 —— 唯一的 MAA 侧无界项 |
| MAA 崩溃转储 `debug\dumps\*.dmp` | 2 个 = **140.7MB**（均在 MAA_12，09-05 的两次崩溃） | 无清理 | 一次性残留，可删 |
| `E:\AUTO-MAS\debug\app.log` | 10.7MB + 6 个周包 zip（各 ~0.7MB） | 源码 `app/utils/logger.py`：`rotation="1 week"`, `retention="1 month"`, `compression="zip"` | ≈15MB（有界 ✅） |
| `E:\AUTO-MAS\debug\frontend.log`(+`.old`) | 0.89MB（+ 10MB 旧件 09-16） | 前端日志；旧件未再增长 | ~11MB |
| `E:\AUTO-MAS\history` | **210.2MB / 42 个日期目录（08-17 起，13712 文件）** | 源码 `app/core/config.py: clean_old_history()`：`Function.HistoryRetentionTime` = **0（永久保留）**；且**只在程序启动时**清理一次 | 无 —— **无界** |
| 本机 `D:\1-2\MAAOrch\runtime` | 15.5 GB / 49,457 文件（其中 png 23,393 张 = 8.9GB；`_ce6` 10.4GB） | 无清理 | 无 —— 本机侧无界 |

### 1.5 实例/目录与业务归属（谁在用、谁是孤儿）

- **MuMu 实例 55 个**：54 个 AUTO-MAS 脚本引用了其中 53 个（`/api/scripts/get` 的 `Emulator.Index` 交叉核对），
  另 5 个为肉鸽机队（`l-1`=25 / `l-2`=28 / `l-4`=31 / `l-5`=34 / `l-7`=52，与 `tools/rogue_fleet_watch.mjs` 的 MACHINES 一致）。
  孤儿/存量：
  - **`idx=44 l-3-青吟巷滑翔花椒`（30.5GB）**：没有脚本引用（AUTO-MAS 无 l-3 脚本、MAAOrch 看守机队不含 l-3）→ **疑似退役实例，待人工确认后走 MuMu 界面删除 ≈ 回收 30GB**。
  - `idx=0 碧蓝航线`（35GB）：非方舟（碧蓝航线），**不是我们的，别碰**。
  - 脚本 `明日方舟-官-28` → 引用的 `idx=51` **在主机上已不存在**（08-29 摘除的遗留引用；若还要跑官-28 需新建实例 ≈ +30GB）。
- **MAA 目录 16 个**，脚本引用的是 `MAA_1…MAA_12`（12 个，多账号脚本共享：`MAA_6`=官-1-1-7+官-38、`MAA_2`=官-2-4-47+l-2、`MAA_3`=官-3-搓玉、`MAA_12`=+l-7）；
  **`MAA_13`/`MAA_14`/`MAA_25`/`MAA_28` 无任何脚本引用**（最后活动 08-27 / 08-27 / 09-16 / 09-21）→ 退役副本，合计 **2.5GB**，待确认后可删（可重装）。

---

## 2. 增长估算（会不会撑爆）

### 2.1 实测/推导速率

| 增长源 | 速率 | 依据 | 性质 |
|---|---|---|---|
| MuMu 实例镜像 | **+29–32GB / 每新增 1 个实例**；2026-08 新建 14 个 = +398GB；2026-09 新建 7 个 = +199GB | `MuMuManager info -v all` 的 `created_timestamp` 逐月汇总 | **一次性大件**，不是连续增长 |
| MAA `asst.log`（运行期） | 活跃槽 ≈ **15–25MB/天**（MAA_1 54.7MB/2.6d、MAA_3 61MB/3.0d、MAA_6 39MB/0.98d…） | 各槽 `asst.bak.log` mtime（=上次轮转时刻）÷ 当前 `asst.log` 体积 | **有上限**：每槽封顶 136MB（64+64+4+4） |
| MAA 错误截图 | **≈15MB/天（16 槽合计）**；单槽 0.8–2MB/天 | 774 张 / 40 天窗口 | **无界**：+5.7GB/年 |
| AUTO-MAS `history` | **≈5.0MB/天**（42 天 210MB；近 7 天均值 4.94MB/天） | 逐日期目录实测 | **无界**：+1.8GB/年 |
| AUTO-MAS `app.log` | ≈10MB/周 | 源码 1 周轮转 + 1 个月保留 + zip | **有界** ≈15MB |
| 回收站 | 不定期（E: 现 42GB、C: 现 6GB；E: 大头是 2025-12 的 26GB） | 按 mtime 分月统计 | 人工清理项 |
| 实例导出/克隆临时体 | **+24GB / 每次**（落在 `C:\Users\Public\mumu`） | 2 个现存 `.mumudata`（25.89/23.06GB）的命名与位置（机制为推断，见 §8 存疑） | 即用即删（现在留了 9 个月） |

### 2.2 判定

- **E:**：143.8GB 余量 ÷（32GB/实例）= **≈3 个实例**。按 8 月节奏（398GB/月）两周就会顶满 → **红线上是实例数，不是日志**。
- **C:**：29.7GB 余量，**一次实例导出/克隆（~24GB）就可能顶满** → 这是最紧的一处；且 C: 还有回收站 6GB、DXCache 5.4GB 可回收。
- 纯日志侧：即便全速跑一年，MAA 日志侧封顶 ~2.2GB（16 槽 × 136MB），AUTO-MAS 日志 ~15MB；
  只有 `history`（+1.8GB/年）与 MAA 截图（+5.7GB/年）无界，合计 ~7.5GB/年 —— **不构成「撑爆」风险，但值得按下面的策略设保留期**。

---

## 3. 现有保留策略盘点（有没有轮转/清理任务）

| 层面 | 现状 | 结论 |
|---|---|---|
| MAA 日志 | 内建轮转：`asst.log` 64MB→`asst.bak.log`（保 1 份）、`gui.log` ~4MB→`gui.bak.log` | ✅ 有界；**错误截图无清理** |
| AUTO-MAS 日志 | loguru `rotation="1 week"` + `retention="1 month"` + zip（`app/utils/logger.py` L43–54） | ✅ 有界 |
| AUTO-MAS 历史 | `Function.HistoryRetentionTime = 0`（永久保留，实测 `E:\AUTO-MAS\config\Config.json`）；即使设 >0，也只在**程序启动**时清理（`main.py:176`） | ⚠️ 无界 |
| MuMu 实例 | 无任何自动回收 | ⚠️ 空间大头 |
| 主机 Windows 计划任务 | 只有 3 条非微软任务：`AUTO-MAS_AutoStart`、`AUTO-MAS-Boot-Backend`、`DailyRestart0400`（04:00 重启机）——**没有清理任务** | ❌ 无 |
| Hermes cron（mas profile） | 活跃 2 条：`mas-shift-dispatch`（发车）、`mas-readonly-inspection`（30 分钟巡检）——**没有存储巡检** | ❌ 无 |
| MAAOrch 侧 | `tools/` 下无任何 clean/prune/rotate 的存储工具 | ❌ 无 |

---

## 4. 策略建议（保留天数 / 按体积轮转 / 闸门）

### 4.1 保留期（按对象）

| 对象 | 建议保留 | 手段 |
|---|---|---|
| MAA `asst.log`/`gui.log` | 保持现状（1 份 bak） | 不动 |
| MAA 错误截图 `*_raw.png` | **30 天**（保留每槽最新 ≥20 张兜底） | 定期删 >30 天（§6.2 命令）；实测总量 ~630MB，删老件主要是**防增长** |
| AUTO-MAS `history` | **90 天**（≈450MB 稳态） | 界面上把「历史记录保留时间」设为 90（等价 `Function.HistoryRetentionTime=90`）；生效点是下次程序启动 |
| AUTO-MAS `app.log` | 现状（1 周轮转/1 月保留） | 不动 |
| 实例导出体 `.mumudata` | **即用即删**（导入成功即清） | §6.3 |
| 回收站 | 每月清一次（或 >20GB 即清） | §6.1 |
| 退役 MAA 目录 / 退役实例 | 确认后清理（MAA_13/14/25/28 = 2.5GB；l-3 = 30.5GB） | §6.5/§6.6 |
| 本机 `runtime` | 无界（现 15.5GB，其中 png 8.9GB） | 建议纳入同款巡检；`_ce6/maa_cli` 等活跃目录不动 |

### 4.2 按体积轮转 / 闸门（"再建实例前先看水表"）

- **建实例闸门**：建/克隆实例前要求 `E: free ≥ 45GB`（1.5 × 32GB），且 `C: free ≥ 30GB`（导出体会落 C）；不达就先清 §6 的项。
- **水位红线**（`tools/storage_waterline.py` 已内置）：

| 项 | 黄（WARN） | 红（CRIT） |
|---|---|---|
| 主机 E: 剩余 | < 120GB | < 60GB |
| 主机 C: 剩余 | < 35GB | < 15GB |
| `E:\AUTO-MAS\history` | > 500MB | > 1000MB |
| 单槽 MAA `debug` | > 400MB | > 600MB |
| 回收站（任一） | > 20GB | — |
| `Public\mumu\*.mumudata` | 存在即报 | — |
| 本机 C:/D: 剩余 | < 60GB | < 30GB |

- **巡检接线**：`python tools/storage_waterline.py`（~90 秒，只读，退出码 0/2/3），建议每日一次
  （或并入现有 30 分钟巡检的收尾步骤；独立 cron 更省预算）。本次**不**新建 cron，交用户拍板。

---

## 5. 清理可回收量一览（按性价比排序，全部需人工拍板）

| # | 动作 | 回收 | 风险 |
|---|---|---|---|
| 1 | 清 **E: 回收站** | **42.3GB** | 不可恢复；含 2025-12 的旧垃圾 26GB（先粗看一眼近期文件） |
| 2 | 清 **`C:\Users\Public\mumu\*.mumudata`**（2025-12 残件） | **49.0GB** | 实例本体在 vms 里还在；仅失去 2025-12 的基线备份 |
| 3 | 退役实例 **l-3 (idx 44)** 走 MuMu 界面删除 | **30.5GB** | 需确认 l-3 真退役（看守机队/脚本均不含它） |
| 4 | **Photoshop 安装包 zip** 5.45GB + MAA 安装包 zip（0.26+0.26GB） | 6.0GB | 安装包，可重下 |
| 5 | 清 **NVIDIA DXCache**（着色器缓存） | 5.4GB | 自动重建；首次进游戏略慢 |
| 6 | 清 **C: 回收站** | 6.0GB | 同上 |
| 7 | 退役 **MAA_13/14/25/28** | 2.5GB | 无脚本引用；MAA 可重装 |
| 8 | pip 缓存（`pip cache purge`，两机） | ~1.4GB | 无 |
| 9 | `E:\MAA-ALL`（4.9GB，09-22 后无写） / `E:\MAA-Test`（0.5GB） / `E:\maadl`（0.26GB） | ~5.7GB | 待确认是否还有用（MAALoad 09-26 有写→先别动） |
| — | 用户数据（**不建议代删**）：`Videos\NVIDIA` 44GB、`.lmstudio\models` 14.8GB、`E:\XIA` 5.5GB | 64GB | 用户自己的，问用户 |

**可即刻回收合计（1–8）≈ 143GB** —— 相当于把主机 E: 的余量翻倍、C: 翻倍还多。

---

## 6. 安全清理命令清单（主机 PowerShell；先 `-WhatIf` 再执行）

> 通用前提：不要在发车/跑图时段做（晚班 19:30–22:30、早班 05:00–06:30 避开）；删除一律先 `-WhatIf` 看清单。

### 6.1 回收站
```powershell
# 先看体积（只读）
Get-ChildItem 'E:\$RECYCLE.BIN' -Recurse -Force -File | Measure-Object -Sum Length
# 清空（不可恢复）
Clear-RecycleBin -DriveLetter E -Force
Clear-RecycleBin -DriveLetter C -Force
```

### 6.2 MAA 老错误截图（>30 天）
```powershell
Get-ChildItem 'E:\MAA-Multi' -Directory | ForEach-Object {
  $p = Join-Path $_.FullName 'debug'
  Get-ChildItem $p -Recurse -File -Filter '*_raw.png' |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) }
} | Remove-Item -WhatIf        # 确认清单后再去掉 -WhatIf
```

### 6.3 实例导出残件（C: 49GB）
```powershell
Get-ChildItem 'C:\Users\Public\mumu' -File -Filter '*.mumudata' |
  Select-Object Name, @{n='GB';e={[math]::Round($_.Length/1GB,2)}}, LastWriteTime
Remove-Item 'C:\Users\Public\mumu\*.mumudata' -WhatIf   # 确认（不建议留着 9 个月前的基线）后去掉 -WhatIf
```

### 6.4 安装包 / 缓存
```powershell
Remove-Item 'C:\Users\Public\mumu\Adobe Photoshop 2026 v27.0.zip' -WhatIf
Remove-Item 'E:\MAA-v6.18.0-win-x64.zip','E:\M9A.zip','E:\maadl\MAA-v6.16.8-win-x64.zip' -WhatIf
Remove-Item 'C:\Users\Administrator\AppData\Local\NVIDIA\DXCache' -Recurse -WhatIf
python -m pip cache purge
```

### 6.5 退役 MAA 目录（先压缩归档或直接删）
```powershell
# 归档（推荐，先留一手；E: 空间够）
Compress-Archive 'E:\MAA-Multi\MAA_13','E:\MAA-Multi\MAA_14','E:\MAA-Multi\MAA_25','E:\MAA-Multi\MAA_28' 'E:\_archive\maa_retired_20260927.zip'
# 或直接删除（MAA 可重装）
Remove-Item 'E:\MAA-Multi\MAA_13','E:\MAA-Multi\MAA_14','E:\MAA-Multi\MAA_25','E:\MAA-Multi\MAA_28' -Recurse -WhatIf
```

### 6.6 退役实例 l-3（idx 44，~30GB）
**不要手删 `vms\MuMuPlayer-12.0-44` 目录**（MuMu 的实例注册表会残留、且易误伤）。走正规流程：

1. MuMu 多开器界面 → 选中 `l-3-青吟巷滑翔花椒` → 删除实例；或
2. 用 MuMuManager 现查子命令后再执行（命令因版本而异，**先看 help**）：
```powershell
& 'E:\MuMu Player 12\nx_main\MuMuManager.exe' --help
```
删除前确认：该实例不在 `tools/rogue_fleet_watch.mjs` 的 MACHINES（l-1/2/4/5/7）里、且 `/api/scripts/get` 无任何脚本引用 idx 44。

### 6.7 **不要删**（红线清单）

- `E:\AUTO-MAS\config\*.json`（**含 DPAPI 密文，误删 = 全账号凭据丢失**）与它们的 `.bak_*`（体积小，是回滚底牌）
- `E:\AUTO-MAS\data\`（运行时源配置，被整包覆盖，删了会丢当前注入状态）
- `E:\MAA-Multi\MAA_1…MAA_12` 下的 `resource/`、`externals/`、`*.dll`（MAA 本体，删了要重装）
- 正在写的日志：`asst.log`/`gui.log` **和 `asst.bak.log`**（bak 是上一轮唯一日志，查「昨晚为什么失败」要用）
- 活动实例目录：`vms\MuMuPlayer-12.0-{1..8,10..34,36..58,60}` 里被脚本引用的（§1.5 表），尤其 `idx=0 碧蓝航线`
- `E:\maa-alas\fz-maa`（肉鸽机队工作区，**活跃**）、`E:\XIA`、`C:\Users\Administrator\Videos\NVIDIA`、`.lmstudio\models`（用户数据）
- `E:\pagefile.sys`、`C:\Windows`、`C:\ProgramData`（系统）
- 本机 `D:\1-2\MAAOrch\runtime\` 活跃工作目录（`_ce6`、`maa_cli`、`plans`、`fleet_cases`…）——要清也只清其中 >30 天的散图，别整目录删

---

## 7. 只读巡检器：`tools/storage_waterline.py`

```bash
python tools/storage_waterline.py             # 主机+本机全量（~90s，只读）
python tools/storage_waterline.py --json      # 机器可读
python tools/storage_waterline.py --local-only
```
- 只跑查询（CIM 磁盘、目录统计、`MuMuManager info -v all` 计数），**不删除任何文件**。
- 阈值集中在文件头（见 §4.2）；退出码 **0=全绿 / 2=有黄灯 / 3=有红灯 / 1=取数失败** —— 可直接给 cron 判读。
- 覆盖：两机磁盘、回收站、`E:\MAA-Multi` 总量、`history`/`debug`、16 槽 debug+截图年龄、vms 实例数与总量、`.mumudata` 残留、**「还能再建几个实例」换算**。
- 本次首跑结果 = **WARN**（本机 D: 59.2GB、主机 C: 29.7GB、E: 回收站 42.3GB、mumudata 残留 49GB）。

---

## 8. 方法与存疑（诚实标注）

- 数据为 **2026-09-27 20:35–21:15** 的只读快照；主机侧命令经 ssh（`Administrator@100.79.173.69`）+ 只读 PS 探针，本机侧本地统计。
- 「实例导出体落 `C:\Users\Public\mumu`」是据**文件名/位置/体积（2 个 24GB 的 `<实例名>.mumudata`）**推断的机制，未实测克隆流程；建实例前按 §4.2 闸门留足 C: 空间即可规避。
- `asst.log` 日增量由「上次轮转时刻（bak mtime）÷ 当前体积」推导，含空闲时段，属**下界估计**（真实运行期速率更高，但受 64MB 轮转封顶）。
- 退役清单（l-3、MAA_13/14/25/28、MAA-ALL、MAA-Test、MAALoad、AzurLaneAutoScript、MaaEnd）基于「无脚本引用 + 最后写入时间」，**删除前仍需人工确认**（例如 MAALoad 09-26 05:14 还有写入，AzurLaneAutoScript 可能还有人偶发使用）。
- 官-28 引用的实例 `idx=51` 已不存在（现存孤儿引用）；若恢复官-28 = 新建实例 +≈30GB，请先过 §4.2 闸门。
- 本次取数在主机 `C:\Windows\Temp\` 留了 12 个小探针脚本（`mas_probe*.ps1`、`mas_storage_probe.ps1`，合计 ~60KB）；按任务约束**未删除**，它们会被系统临时清理自然回收。
- 本报告与巡检器均不做任何删除；§6 的命令由人执行（建议在 `runtime/reports/` 留执行记录）。
