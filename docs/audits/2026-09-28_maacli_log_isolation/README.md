# maa-cli 并发日志隔离小样验证（t_72ed337a）— 结论：隔离 **不成立**（log loss 形态）

> 2026-09-28 12:22–12:27 CST ｜ 执行：mas（看板 `t_72ed337a`）｜ 机器：**MRFZ-0000（100.79.173.69）主机**
> 版本锚点：**maa-cli 0.7.5 + MaaCore 6.18.0**（`maa.exe version` 实查）
> 证据级别：**TESTED**（真两进程 / 真设备 / 真并发窗口，非 mock）

## 0. 一句话结论

**2 进程同 data 目录并发跑 maa-cli 时，`asst.log` 隔离不成立**：两进程写同一路径，**先占住文件者正常记账**，另一进程在重叠窗口内的日志**全部静默丢失**（不落盘、不缓冲、不回填、无第二文件），直到前者进程退出后才恢复落盘——本样中后者 3 分 18 秒的日志只剩尾部 43 行。
⇒ 「并行基准」不得依赖默认同 data 目录做日志取证；并发时必须逐进程 `MAA_STATE_DIR`（或独立 data 目录）隔离（`MAA_STATE_DIR` 会让 asst.log 跟着搬，SURVEY §3.1）。

## 1. 试验设计（2 进程同 data 目录）

- 靶位（均为**停用号**，沿用 L3 已核干净靶位；冷启→回收，全程未跑游戏任务）：
  - A：idx19 官-5 `v` → `127.0.0.1:16992`，profile `v-official`
  - B：idx37 官-25 `Kaori` → `127.0.0.1:16548`，profile `kaori-official`
- 命令（同一秒级窗口背靠背启动，**不设任何 MAA_* 环境变量** ⇒ 两进程共享默认 `%APPDATA%\loong\maa\data`）：
  - A：`E:\MAA-CLI\bin\maa.exe --batch startup Official -a 127.0.0.1:16992 -p v-official --log-file=E:\MAA-CLI\logs\iso-A-v-idx19.log`
  - B：`E:\MAA-CLI\bin\maa.exe --batch startup Official -a 127.0.0.1:16548 -p kaori-official --log-file=E:\MAA-CLI\logs\iso-B-kaori-idx37.log`
- 判据：asst.log 行归属 = **`[Px<pid>]` 前缀**（MaaCore 每行自带进程号；两进程 pid 已知：A=**32304**、B=**29804**）。
- 基线（启动前）：asst.log = **4170 行 / 590,777 B**（mtime 12:23:08 前）。

## 2. 证据

### 2.1 运行时间线（判据：asst.log Px 归属 + OUT Summary + 进程存活）

| 时刻 | 事件 |
|---|---|
| 12:23:08 | 基线 4170 行；A/B 同秒启动（cmd 壳 37024/40584 → maa.exe **32304/29804**） |
| 12:23:08.920 | asst.log 出现 **Px29804**「MaaCore Process Start / User Dir …\loong\maa\data」 |
| 12:23–12:26 | 文件持续增长（4960→5172→5359→5555 行），**新增行全部是 Px29804**（+46 nopx 为 JSON 续行）；**Px32304 零行落盘** |
| 12:26:28–29 | B 结束（Summary `[StartUp] … (3m 16s) Error`）；B 流末行 12:26:29.261（OcrPack 析构） |
| 12:26:29.962 | **Px32304 首行落盘**（B 退出后 ~0.7s）——其后 3.2 秒的收尾行正常写入 |
| 12:26:32–33 | A 结束（Summary `[StartUp] 12:23:12 - 12:26:32 (3m 20s) Completed`）；A 流末行 12:26:33.178 |
| 12:26:33 | asst.log 终态 952,490 B / 5642 行（**只增不减，旧内容原样保留**） |

### 2.2 全文件行归属（phase4 实测输出）

```
asst.log size=952490 mtime=12:26:33 ; TOTAL_LINES=5642
PX_TOTAL 32304 => 43      # 进程 A（v/idx19）：整个文件里只有 43 行
PX_TOTAL 29804 => 1371    # 进程 B（Kaori/idx37）：完整流
PX_TOTAL 22828 => 1269    # （09-27 23:28 L3 旧流，原样保留 ⇒ 无截断）
PX_TOTAL 1084  => 421     # （09-28 09:51 旧流，原样保留）
MaaCoreProcessStartCount=9 ; HEAD=09-27 22:14（旧内容未被覆盖）
```

- **本样新增区（行 4170 之后）**：1472 行 = Px29804×1371 + Px32304×43 + nopx×46 + 旧流尾×12。
- **A 的 3 分 18 秒并发窗口日志（按 B 同速率估计 ≈1300 行）在磁盘上不存在**：唯一 asst.log 里只有 43 行；`debug\` 目录无第二个日志文件；`loong\maa` 与 `E:\MAA-CLI` 下运行窗口（12:23–12:27:40）内被写过的文件清单中**没有任何其它 asst 日志**（仅 asst.log 本体 + 本卡自己的取证件）。
- **写合并窗口**（行 1414–1444 摘录，`[2]` 段）：Px29804 的析构尾与 Px32304 的恢复写**同文件相邻拼贴**——即两进程日志最终确实混在**同一个文件**里（前段只有 B、后段只有 A 尾，中间无时间交错区）。

### 2.3 机制推断与未核项

- 运行时行为：**后到/被拒的一方整窗口静默丢日志**（错误被吞，无报错、无缓冲回填），持有方进程退出后立即恢复写入 ⇒ 与 MaaCore Logger 对 `asst.log` 的**单文件句柄占用（share 模式）**一致。
- **源码未核**：`raw.githubusercontent.com/…/MaaAssistantArknights/v6.18.0/src/MaaCore/Utils/Logger.cpp` 404（tag/路径未命中），未取得打开模式/错误处理分支的源码级证据；上述机制为**运行时行为推断**。⇒ 复验方式：取 MaaCore 源码 `Utils/Logger.cpp` 的 open 调用与错误分支，或对 open 失败路径做打点复测。
- 「先占者」是竞态（本样 = B 先占）：**胜负与任务成败无关**（A 任务 Completed 却丢了日志；B 任务 Error 却日志完整）。

### 2.4 收尾（干净，与开跑前同态）

```
VMM19_37=down ; maa-cli procs remaining: 0 ; adb 16992/16548 随实例关机下线
（实例 19/37 开跑前本就是关的；未触碰 AUTO-MAS 侧车、未改生产配置、未动任何在产实例/肉鸽机队）
```

## 3. 边界

1. **判据**：本样只回答「同 data 目录 2 进程并发时 asst.log 是否互串」——答案为**不成立（丢日志形态）**；不涉及 gui.log/记账/切号语义（maa-cli 本来就没有）。
2. **并发基准的可用抓手**（既有文档口径，本卡未重复复验）：逐进程 `MAA_STATE_DIR` ⇒ asst.log 各自独立（SURVEY §3.1）；本样旁证：两进程各自的 `--log-file`（CLI 自有日志）互不干扰。
3. **未放大边界**：结论仅覆盖 0.7.5 + 6.18.0、同用户同机、2 进程；≥3 进程 / 跨用户 / 其它版本需另测（预期同族风险更大：丢日志面 = 进程数−1）。
4. **失败方在本样中的附加现象**：B（日志完整方）任务 Summary = `Error`（其自身链问题，与日志隔离无关）；不影响本结论。
5. **取证复现命令**（主机侧，只读）：`Get-Content $env:APPDATA\loong\maa\data\debug\asst.log` 后按 `\[Px(\d+)\]` 分组计数；对照进程启动时刻与各自 `--log-file`。

## 4. 产物

- 本 README（结论+边界）
- `phase3_raw.txt` / `phase4_raw.txt`（原始命令输出：watch+证据 与 全文件归属）
- 主机侧残留（后续可复取）：`E:\MAA-CLI\logs\iso-{A,B}*`、`iso_evidence.txt`、`iso_baseline.txt`、`iso_ports.txt`（**asst.log 本体未动、未截断**）
- 台账口径回写：`docs/MAA_CLI_DEPLOY.md` §5/§8.1；`docs/HERMES_PROJECT_BRAIN.md` §4-20 行

## 5. 约束遵守声明

只读卡：未写生产配置、未改队列、未碰 AUTO-MAS 侧车、未 kill 任何 MAA；真跑仅限两台**停用号**实例的 `startup`（进大厅即止），跑后实例已关机回收；工具调用 ≤60（本卡 30 余次）；运行窗口 12:22–12:27 不在任何班次窗口内。
