# 空转模拟器根因：为什么「关不掉」（关闭失败链 + 责任判定）

> 2026-09-27 23:0x ｜ 执行：mas（kanban `t_fefd4aec`）｜ **全程只读**：未关闭/未启动任何实例、未 kill、未改配置、未发车。
> 代码口径 = **线上运行代码** `E:\AUTO-MAS\app\`（git HEAD `491c40a`，branch release/v5.4.0）：
> `app/utils/emulator/mumu.py`、`app/models/emulator.py` 与本地 `D:\MAS` v5.4.0 **逐字节一致（md5 实测）**；
> `app/task/MAA/AutoProxy.py` 取线上现状（含 09-27 账号切换 v2 补丁，`git diff --stat` = 8+2-）。
> 日志口径：`E:\AUTO-MAS\debug\app.log`（09-22 01:31 起，12.9 MB / 110,516 行；今日 40,881 行）。
> 现场快照：2026-09-27 22:44–23:05（进程/网表/MuMuManager/CPU-内存采样，见 §3/§4）。

## 0. 一句话根因

**AUTO-MAS 关模拟器前先用 `MuMuManager info -v <idx>` 判活，而这台机器上 MuMuManager 的状态面在任何实例上都假报「未启动」**
（实测 4/4 在跑实例 `is_process_started=false`；`MuMuManager adb -v 13` 也报 `-201 vm not running`，而同一时刻 VMM 进程在、`adb 127.0.0.1:16800` 是 `device` 在线）⇒ `close()` 的前置校验直接 `return`，
**`MuMuManager control shutdown` 从未被发出**（日志形态固定：`用户任务结束, 关闭模拟器` → `设备<idx>未在线，当前状态: 1`），
且**无重试、无兜底**（`ForceKillOnClose=false`），调用方也不看返回值 ⇒ 静默失败、实例留机。

即：**不是「关了但没关掉」，而是「根本就没关」——关的动作被不可信的判活前置校验挡在门外。**
（"状态: 1" = `DeviceStatus.OFFLINE`，见 `app/models/emulator.py:27-41`；是**探测结论**，不是关机结果。）

## 1. 证据：今日 app.log 量化（`work/app.log` 全量解析）

### 1.1 逐日事件计数（脚本 `work/analyze_log.py`）

| 日期 | 启动模拟器 | 关闭意图 | **关闭被拦** | 失败异常 | 中止进程 | 任务结束: | 开始代理用户 |
|---|---|---|---|---|---|---|---|
| 09-22 | 142 | 90 | 101 | 0 | 193 | 13 | 103 |
| 09-23 | 217 | 112 | 148 | 0 | 377 | 25 | 124 |
| 09-24 | 161 | 106 | 102 | 0 | 323 | 24 | 106 |
| 09-25 | 165 | 106 | 110 | 0 | 322 | 24 | 106 |
| 09-26 | 92 | 49 | 45 | 0 | 171 | 11 | 62 |
| **09-27** | **100** | **39** | **42** | **0** | **193** | **19** | **59** |
| 6 日合计 | 877 | 502 | **548** | **0** | — | — | — |

- "关闭意图" = `AutoProxy.final_task` 的 `用户任务结束, 关闭模拟器`（远端 AutoProxy.py:1027）；
- "关闭被拦" = `MuMu模拟器管理 | 设备<idx>未在线，当前状态: N`（`mumu.py:343` 的警告）；**N=1 全部**，另有 4（ERROR）共 12 条；
- "失败异常" = `关闭模拟器失败: {e}`（`mumu.py`/调用方）——**6 天 0 条**：关闭链的异常分支从未触发，
  ⇒ 关闭动作的失败 100% 是"被前置校验拦下"这一种形态。

### 1.2 今日 42 条拦截与关闭意图的配对（`work/analysis2.py`）

- 39 个关闭意图里 **35 个在 +180s 内紧跟一条拦截**（其余 4 个无拦截，见 §6 存疑），
  拦截波峰在意图后 **+0s ~ +49s**（`MuMuManager info` 调用耗时，`MaxWaitTime=120`）。
- 拦截覆盖 **20 个不同实例**：idx 24×12、48×3、26×3、27×3、13×2、43×2、16×2、4×2、1×2、46/2/7/39/54/22/55/5/38/60/8 各 1。
- 单个脚本运行会关**两次**（两次都被拦）：用户级 `AutoProxy.final_task` + 脚本级 `MaaManager.final_task`——
  原始日志（14:58 窗口，日-1/idx24）：
  ```
  14:58:15.562 | MAA 自动代理 | 用户任务结束, 关闭模拟器          ← 用户级意图
  14:58:15.986 | MuMu模拟器管理 | 设备24未在线，当前状态: 1      ← 拦截（0.4s）
  14:58:35.878 | MAA 调度器 | MAA 主任务已结束, 开始执行后续操作
  14:58:35.879 | MAA 调度器 | 已解锁脚本配置 3d7ba1e8-...
  14:58:36.303 | MuMu模拟器管理 | 设备24未在线，当前状态: 1      ← 脚本级再拦一次
  ```

### 1.3 任务书点名的 22:05/22:07/22:11 三连（原文）

```
22:05:13.493 | MAA 自动代理 | 用户: 99584ac0-... - MAA进程完成代理任务
22:05:16.577 | 系统服务 | 开始中止进程: E:\MAA-Multi\MAA_11\MAA.exe
22:05:21.986 | MAA 自动代理 | 用户任务结束, 关闭模拟器
22:05:53.145 | MuMu模拟器管理 | 设备38未在线，当前状态: 1
22:05:53.262 | 业务调度 | 任务结束: def79a6c-0eda-406f-86c8-55e68e07c9f2
   （22:06:52 → 22:07:20 设备60 同形态；22:10:54 → 22:11:20 设备8 同形态）
```

### 1.4 实例"一直开着"的直接证据：跨日留机（`work/analysis4.py`）

现在（23:0x）在跑的 25–27 台里，多台的**最后一次启动远在 1–2 天前**，期间 6 天日志内既无成功关闭、也无后续动作：

| idx | 实例名 | 最后启动 | 6日否决 |
|---|---|---|---|
| 50 | 官-20 | **2026-09-25 20:59** | 8 |
| 32 | 台-2 | **2026-09-25 21:27** | 9 |
| 3 | b-9 | 2026-09-26 20:43 | 10 |
| 12 | 日-4 | 2026-09-26 20:05 | 13 |
| 21 | 官-14 | 2026-09-26 20:49 | 9 |
| 45 | 官-13 | 2026-09-26 20:02 | 9 |
| 49 | 官-29 | 2026-09-26 21:48 | 8 |
| 19 / 37 | 官-5 / 官-25 | 6 天日志内**无启动记录** | 10 / 0 |

## 2. 关闭链源码走查（回答任务书问题 1、2）

### 2.1 谁在关（调用点，均不做重试、不检查返回值）

| 调用点 | 行（线上） | 触发条件 |
|---|---|---|
| `AutoProxy.final_task`（用户级收尾） | AutoProxy.py:1017-1033 | ① `check_result == "Pass"`（1019-1020，否则**直接 return，连意图日志都不打**）② `Run.TaskTransitionMethod == "ExitEmulator"`（1026） |
| `MaaManager.final_task`（脚本级收尾） | app/task/MAA/manager.py:191-206 | ① `check_result == "Pass"`（194-196）② mode ∈ {AutoProxy, ManualReview}（202）；**无条件关一次**（204） |
| 异常路径（启动失败 / 任务异常） | AutoProxy.py:518、568 | 用户任务失败后收尾时也尝试关 |

`Run.TaskTransitionMethod` 默认值 = `ExitEmulator`（`app/models/config.py:841-846`）。

### 2.2 怎么关（`app/utils/emulator/mumu.py:339-375`，与本地 v5.4.0 逐字节一致）

```python
async def close(self, idx: str) -> DeviceStatus:
    try:
        status = await self.getStatus(idx)                      # ← 判活（不可信，见 2.3）
        if status not in [DeviceStatus.ONLINE, DeviceStatus.STARTING]:
            logger.warning(f"设备{idx}未在线，当前状态: {status}")  # ← 日志形态；直接 return，不发关机命令
            return status
        result = await ProcessRunner.run_process(self.emulator_path, "control", "-v", idx, "shutdown", ...)
        ...  # 轮询 getStatus 直到 OFFLINE；超时/ERROR 才 raise（→ 调用方打「关闭模拟器失败」，6 天 0 条）
    finally:
        if self.config.get("Info", "ForceKillOnClose"):
            self._force_kill_mumu_processes()                    # ← 兜底通道，但配置为 false ⇒ 永不执行
```

- **判据语义**：`getStatus` → `get_device_info` = `MuMuManager.exe info -v <idx>`（mumu.py:420-442、539-552）；
  `_get_status_from_data`（435-442）：`is_android_started=True→ONLINE(0)`，否则 `is_process_started=True→STARTING(2)`，否则 **`OFFLINE(1)`**。
- **失败后不重试**：`close()` 无循环重试；被拦即返回；上层也不重试、不校验返回值。
- **无兜底**：`EmulatorConfig.json`（`E:\AUTO-MAS\config\EmulatorConfig.json` 实读）：
  `Path=E:/MuMu Player 12/nx_main/MuMuManager.exe`、`MaxWaitTime=120`、**`ForceKillOnClose=false`**。
- ⇒ 判别链上**唯一的出口就是** `MuMuManager control -v <idx> shutdown`（`control -h` 已核实存在 `shutdown` 子命令）。

### 2.3 为什么判活不可信（背景 + 现场复核）

- 已知：`MuMuManager info` 假死（本项目 09-26 起多处实测；`adb -v N connect` 恒 `-201`）。
- 另有一层"设计如此"：**MAA 自己也不会关模拟器**——`TaskTransitionMethod→PostActions` 映射表
  （`app/utils/constants.py:134-138`）`NoAction→"8"`、`ExitGame→"9"`、**`ExitEmulator→"9"`**（不含 ExitEmulator 位 4）；
  线上实读各 MAA 目录 `config/gui.new.json` 的 `Gui.PostActions` = `"ExitArknights, ExitSelf"`（MAA_1…MAA_12 全部如此）。
  ⇒ 全链路里"关模拟器"的**唯一执行者**就是上面那个被拦的 `close()`。

### 2.4 问题 2 的正面回答

`设备N未在线，当前状态: 1` 是**「先探测再关」的前置校验**，口径 = 设备离线（OFFLINE=1）；
它**把关闭动作挡住了**（关机命令根本没执行），不是"执行了但无效"。探测本身是错的（§3 现场证据）。

## 3. 对照实验（2026-09-27 23:0x，只读；未执行任何关闭）

挑 4 台**当前空闲**实例（idx 3 / 13 / 24 / 29 = b-9 / 官-1 / 日-1 / 官-15），三口径对照：

| 口径 | idx 13（官-1）实测 | 其余 3 台 |
|---|---|---|
| `MuMuManager.exe info -v 13` | `{"index":"13","name":"明日方舟-官-1","info_source":"local","is_android_started":false,"is_process_started":false,"error_code":0}` | 全部同形（false/false） |
| VMM 进程（可靠判据） | **在跑**：`MuMuVMMHeadless.exe` pid 14620，命令行含 `--comment MuMuPlayer-12.0-13`；另有 `MuMuNxDevice.exe` | 全部在跑（25 台进程普查） |
| adb | **在线**：`adb devices` 中 `127.0.0.1:16800  device` | — |
| 旁证 | `MuMuManager.exe adb -v 13` → `{"errcode":-201,"errmsg":"vm not running, can not connect adb !!"}` | — |

⇒ 同一实例：**MuMuManager 说没跑 / VMM 进程在 / adb 在线**。这与 `close()` 被拦时看到的信号完全同源。

**如果要手动关闭（本次未执行，需 lead 批准）**，确切命令二选一：

```bat
:: ① 直控（已核实 control 子命令存在：control -v <idx> shutdown）
"E:\MuMu Player 12\nx_main\MuMuManager.exe" control -v 13 shutdown

:: ② 走 AUTO-MAS 等价界面操作（REST，等价于点界面的「关闭」）
curl -s -X POST http://100.79.173.69:36163/api/emulator/operate -H "Content-Type: application/json" ^
  -d "{\"emulatorId\":\"6e09a6bb-3126-412e-8412-9c90fb0c4499\",\"operate\":\"close\",\"index\":\"13\"}"
```

**待验证假设**：`control shutdown` 与 `info` 属同一 API 族，**是否真的能关**未实测（合规未执行）；
现有旁证偏正面——今晚 05:14/05:30/11:31/12:02 四个"判活通过"的关闭之后，同实例 1–2 分钟后出现冷启动特征
（`adb.exe: device offline`、启动耗时变长），提示**命令本身有效，只是几乎永远走不到**。
建议由 lead 批准后做 **1 台**实例的手工关闭对照（先截 adb/VMM 底证，再执行，再复核）。

## 4. 成本量化（22:44–23:05 快照，只读采样）

- 主机：**36 核 / 63.8 GB**；**可用物理内存 0.6 GB**；系统 CPU 采样 **42.8 / 45.8 / 44.4 %**；提交内存 95.8 GB（空闲 20.9）。
- 在跑实例：**27 台**（VMM 进程普查；21:26 为 14 台、20:32 为 12 台——晚班还在往上加）。
- 单台空转成本（23 台 VMM 族合计实测）：
  - `MuMuVMMHeadless`：合计 4146 MB / 23 台 ⇒ **≈180 MB/台**；CPU 合计 **25.6 %（36 核口径，≈9.2 逻辑核）**；
  - `MuMuNxDevice`：合计 3286 MB / 23 台 ⇒ **≈143 MB/台**；
  - ⇒ **≈0.32 GB + CPU 若干 / 台**；逐台 CPU 采样（VMM）：0 %×10 台、0.3–0.4 %×6、2.6–6.0 %×6（空转里也有 1–2 台在吃核）。
- 口径换算：
  - **12 台（任务书口径）** ≈ **3.9 GB RAM + ~10 % 整机 CPU**；
  - **当前 25–27 台** ≈ **8.0–8.6 GB RAM + ~13 % 整机 CPU**（仅 VMM 族，未计 MAA/adb/侧车）。
- 时间维度：晚班 22:30 收尾 → 次日 05:00 早班，**空转 ≈6.5 h；加上白天班次间隙，多数实例每天空转 ≥13 h**；
  更差的样本：7 台已连续 24 h+、2 台已 49 h+ 未关（§1.4），2 台更早遗留。
- 附带代价：内存只剩 0.6 GB ⇒ MuMu/系统的内存回收与冷启动行为都更不稳定（今晚多次 `device offline` 即发生在 0.6 GB 水位的窗口里）。

## 5. 责任占比（"没关掉"拆三类 + 证据）

### (a) AUTO-MAS 关闭失败（MuMu 状态/API 问题）——**机制性主因**

- 形态：`close()` 被判活挡住，关机命令从未发出；无重试、无兜底。
- 量：**今日 42 次 / 20 台；6 日 548 次 / 502 次意图**；且对**长期在跑的实例 100% 复现**（§3 四台实测、§1.4 跨日样本）。
- 现仍在跑、归因 (a)（最后关闭尝试被拦）的实例：**12 台**——idx 3/12/16/21/24/27/32/45/46/49/50/58。

### (b) 任务被孤儿化（侧车重启）导致收尾从未跑到

- 今日两次侧车重启：**05:34:25**、**19:54:14**（19:53:32 死亡 → 19:54:14 新进程，无优雅退出/traceback）。
- 19:54:14 那次：6 台已跑完实例的收尾被吞（官-23/官-2-4-47/官-3/官-41/官-9/官-1-1-7 = idx 11/14/15/56/23/13），
  **连"关闭意图"都没打**；官-15（idx29）中途重试被吞。见 t_aaacd03c 报告 §四·6（同源取证，勿重复采集）。
- 量化：今日 **59 次"开始代理用户" vs 39 个关闭意图**，20 次用户任务收尾没走到（`final_task` 的 `check_result != "Pass"` 早退 + 重启孤儿化）。
- 现仍在跑、归因 (b) 的实例：**8 台**——idx 11/13/14/15/20/23/29/56（最后启动都在 19:30–19:53，之后无任何关闭尝试）。

### (c) 设计如此（留机复用）

1. **同一脚本一次轮内逐用户关/开**：每个用户结束都尝试关（`TaskTransitionMethod=ExitEmulator` 默认值），
   下一个用户立刻 `开始启动模拟器` 复用（如 idx13 今日 12 次、idx24 今日 22 次）——只要"关"没生效，就表现为同一实例整天热着；
2. **同一实例跨班次复用**：队列链实测（t_aaacd03c `queue_chains.txt`）**每个脚本同时挂在 `日常定时N` 与 `晚班定时N`**，
   实例是长期驻留资源 ⇒ 班次之间（08:00–19:30）本就"开着不关"；
3. **MAA 侧永不关**：`ExitEmulator→"9"` 映射 + 线上 `Gui.PostActions="ExitArknights, ExitSelf"`（§2.3）⇒ 组件分工上只有 AUTO-MAS 会关。
- 现仍在跑、归因 (c)/待查的实例：**5 台**——idx 10/19/33/36/37（其中 19/37 连 6 日启动记录都没有）；
  另有 idx 25/28 是 fz-maa 肉鸽机（**在使用**，勿动）。

### 占比一览（23:0x，25 台空闲在跑）

| 归因 | 台数 | 占比 | 备注 |
|---|---|---|---|
| (a) 关闭被拦（假死判活+无重试/兜底） | 12 | 48 % | 机制性问题，改代码可根治 |
| (b) 收尾未跑到（重启孤儿化 / check 未过） | 8 | 32 % | 与侧车重启纪律（班次窗口内禁重启）相关 |
| (c) 设计留机 / 更早遗留 | 5 | 20 % | 需要"班次后收敛"策略来兜；19/37 更早遗留 |
| 合计 | 25 | 100 % | 另有 2 台肉鸽机在使用 |

> 三类不是互斥的：一台实例一天里可能既被拦 (a)、又被重启吞过 (b)；上表按"它现在为什么还开着"的最近一次有效归因。

## 6. 处置选项（只出方案，不执行；均含风险/回滚/是否需重启侧车）

### 选项 ① 修 AUTO-MAS 关闭逻辑（根治，动代码）

- **改动点**（建议最小改法，任选组合）：
  1. `mumu.py:420-442` 增加**可靠判活**：先按 `MuMuVMMHeadless` 进程（命令行 `--comment MuMuPlayer-12.0-<idx>`）判"是否在跑"，
     MuMuManager 结论只作辅助；在跑 ⇒ 继续执行 `shutdown`；
  2. 对"被拦"路径加**有限重试**（如 3 次 × 30s，且重试前用进程判据复核）；
  3. 关后**复核**用进程消失（而非 `info`）作为成功判据；
  4. 可选：把 `EmulatorConfig.Info.ForceKillOnClose` 置 true（强力清残留进程）——**不建议**默认开（会误杀同机其它实例的判定链未隔离）。
- **影响面**：`getStatus` 被 `open/setVisible/close` 共用 ⇒ 改判活会同时影响启动路径（"假死"判定反转后，
  `open()` 会更常走"已在线直接返回"，反而减少无谓 launch —— 需回归验证）；全部 MAA 脚本运行路径。
- **风险**：误关"正在启动中"的实例（把 STARTING 判成在跑再去关）；与 MAA 侧并发（同目录互杀）叠加时更难归因。
- **回滚**：改前 `copy /y <文件> <文件>.bak-closefix-<ts>`，反向拷贝即可；**需重启侧车**才生效
  （Python 模块启动时导入），**且必须在班次窗口外**（05:00–08:00 / 19:30–23:00 禁重启，否则吞任务——今晚 19:54 已是教训）。
- **验收**：改后 1 个轮内同一实例连续两次 `用户任务结束, 关闭模拟器` 后应看到 VMM 进程消失；`app.log` 不再出现
  `设备N未在线，当前状态: 1` 伴随关闭意图。

### 选项 ② 班次后统一收敛（零改代码，最快见效）

- **改动点**：新增 Hermes cron（如 `30 23 * * *` 与 `45 4 * * *`，都在班次窗口外），调用
  `D:\1-2\MAAOrch\tools\mas_idle_emulators.py --aggressive`（t_c47b71c5 已交付，只读、默认不碰 adb），
  白名单只关"无 MAA 在用 ∧ 不属于下一班/补跑要用"的实例；判活/复用 **以 VMM 进程为准**（勿信 MuMuManager）。
  可与巡检 job `52b88b0ffca0` 复用同一份清单（`--json`）。
- **影响面**：只影响"下一班不用"的空闲实例；关闭命令即 §3-①（同一 API 族）。
- **风险**：清单口径写错会关掉下一班要用的实例（冷却 = 下次冷启，可能触发 `device offline` 失败链——t_aaacd03c 实测）；
  `shutdown` 是否真能关**待 §3 手工对照验证**后再放量。
- **回滚**：`hermes -p mas cron pause <job>`（或 `delete`）；**不需要重启侧车**。
- **建议**：作为**第一优先级**落地（不动 AUTO-MAS、可灰度：先只关 2–3 台"隔夜遗留"实例）。

### 选项 ③ MAA `PostActions` 加 `ExitEmulator`（=13）

- **改动点**：`app/utils/constants.py:134-138` 把 `ExitEmulator` 映射由 `"9"` 改为 `"13"`（1+4+8），**或**仅对指定脚本试验；
  改动后 MAA 会在结尾位自关模拟器。**需重启侧车**（窗口外）。
- **影响面**：全部 MAA 脚本（若全量改）；每轮多一次模拟器关闭（多 1 次冷启动风险）。
- **风险**：MAA 关模拟器走的是**同一 MuMu API 族**（MuMuManager/adb），很可能与 (a) **同样失败**；
  且 MAA 与 AUTO-MAS 同时持有模拟器状态时，行为更难归因（谁关的、什么时候关的）。
- **回滚**：反向改回 `"9"` + 重启侧车；**需要重启侧车**。
- **建议**：**先做 1 个脚本的灰度实验**（含"是否真关掉"验证），再谈全量。

### 落地顺序建议

1. **手工 1 台对照**（lead 批准）→ 证明 `control shutdown` 有效性（10 分钟，决定 ② 是否可用）；
2. **② 班次后收敛 cron**（低风险、立刻回收资源；先灰度 2–3 台）；
3. **① 修关闭逻辑**（根治；窗口外改 + 重启侧车；与"班次窗口禁重启"纪律配套）；
4. **③ MAA PostActions 灰度**（可选，验证价值低于 ①②）。

## 7. 证据文件与存疑

- 证据（本卡 workspace `C:\Users\xiach\AppData\Local\hermes\kanban\boards\maa-parallel-benchmark-20260926\workspaces\t_fefd4aec\work\`）：
  `app.log`（12.9 MB 全量拉取）、`analyze_log.py/.out`（逐日计数）、`correlate.py/.out`（意图×拦截配对）、
  `analysis2.py/.out`（逐意图归因 + 原始窗口）、`analysis3.py/.out`、`analysis4.py/.out`（跨日留机）、
  `make_table.py/table.md`（在跑实例逐台判定）、`probe_vmm.txt`（VMM 进程 + CPU/内存采样）、
  `probe_all.out`（`info -v all` + idx→实例名全表）、`probe_instances.ps1`（进程普查 + `control -h`）、
  `probe_final.ps1`（adb devices / `adb -v 13` / MAA 进程 / 在跑清单）、`probe_postactions.ps1`（各 MAA 目录 PostActions）、
  `run_remote_ps.py`（只读远端探针封装）。
- **存疑 1**：今晚 idx 5（b-12）/ 8（b-10）/ 38（日-6）在**没有任何 AUTO-MAS 关闭记录**的情况下停了；
  其中 idx 8/22/34/60 留有 `crashpad_handler.exe` 残迹（疑似崩溃/硬杀），idx 5/38 无残迹（疑似正常关闭）。
  机制未证（候选：MuMu 侧自管理、人工、未记录路径）。**不影响主结论**，但说明"实例会不会自己消失"不可假设。
- **存疑 2**：今日 4 个关闭意图（05:14:46 idx13、05:30:58 idx14、11:31:29 idx13、12:02:21 idx13）无对应拦截，
  且其后 1–2 分钟同实例出现冷启动特征（`device offline`）⇒ 推断这 4 次 `shutdown` 真执行了且**可能有效**；
  与 (a) 的"100% 假死"并不矛盾——MuMuManager 判活是**间歇性**正确（`info_source: local`，状态缓存），
  但长期在跑实例稳定落到"假死"分支。
- **存疑 3**：`adb devices` 现列 41 条（含 `emulator-5576/5580` 陈旧 transport；t_aaacd03c 已提示可 `adb disconnect`），
  条数多于在跑实例数，adb 端口径不能单独用于清点。
