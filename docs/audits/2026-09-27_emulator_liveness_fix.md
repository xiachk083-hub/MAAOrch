# 2026-09-27 模拟器判活/关闭修复（close-fix + 理智药并入）

卡片：t_02210a15 ｜ 主机：MRFZ-0000 (100.79.173.69) ｜ 应用：E:\AUTO-MAS ｜ 完成：2026-09-28 04:1x
审计同伴文档：docs/audits/2026-09-27_idle_emulator_rootcause.md（根因证据）

## 0. 结论摘要

- 根因修复已上线：`close()` 判活由不可信的 `MuMuManager info -v` 改为
  「VMM 进程（`--comment MuMuPlayer-12.0-<idx>`）或 adb 可连」，关闭动作无条件尝试 + 3 次重试 + 以
  「VMM 进程消失」为成功判据；保留免重启回滚开关。
- 理智药修复（t_52ebdda4 交接）搭同一部署窗口并入（constants.py L206 + AutoProxy.py L278 两处单行）。
- 重启：**由宿主每日计划重启自然完成**（04:00:01 计划重启 → 04:01:34 新侧车 15124），补丁随之生效；
  未人工 kill。重启瞬间在飞 2 个 catch-up 任务（q5/q7）被孤儿化（见 §5，归 t_564593ae 处置）。
- 动态验证：04:09 对实例 13 实测「发送关闭命令 → 设备13已关闭（2.1s）→ VMM 进程消失」；
  二次关闭走「未在运行, 跳过关闭」优雅路径。旧行为（23:47「关闭模拟器失败/未在线」）已被替换。

## 1. 根因（详见 rootcause 审计）

- `close()` 前置用 `MuMuManager info -v <idx>` 判活；本机对**任何在跑实例都假报 `is_process_started=false`**
  ⇒ 直接 return，「`control shutdown` 从未发出」；无重试、无兜底（`ForceKillOnClose=false`）、调用方不查返回值。
- 6 日口径：关闭意图 502 次 / 被拦 548 次 / 真正执行≈个位数 ⇒ 空转实例 24 台累积。

## 2. 改动内容（diff 全量预览见工作区 close-fix.diff）

| 文件 | 改动 | 部署后 sha256 |
|---|---|---|
| `app/utils/emulator/mumu.py` | +`import re`；新增 `_find_vmm_pids/_is_vmm_running/_is_adb_connectable`；重写 `close()`（可靠判活+无条件尝试+3 重试 10/20s+30s 复核窗口+返回码检查+日志）；回滚开关 `MUMU_RELIABLE_CLOSE` / `config/mumu_close_legacy.flag` | `3FC40DE9DF4D3899…` |
| `app/utils/constants.py` | `UseExpiringMedicine` L206 False→True（理智药并入） | `030EBBD0E39101D2…` |
| `app/task/MAA/AutoProxy.py` | L278 `UseExpiringMedicine` False→True（理智药并入；L279 保持 False） | `0F94B53B50445600…` |

未做项：AutoProxy L831 显式赋值（t_52ebdda4 可选项③）——属模板托管面之外、非本卡授权范围，决定不做，记录在此。

## 3. 部署记录（2026-09-28 02:19–02:26，全部"只改文件、不动进程"）

- 上传三件套（分块 base64 → `WriteAllBytes/Append`，逐块 ack + 终读哈希三连 OK）；
  远端 `py_compile` EXIT=0；上传后哈希回读 == 补丁版（3/3）。
- 备份（首轮 xcopy 步骤失败，已改上传通道补做 + 哈希校验；工具已修复为"备份校验失败即 ABORT"）：
  - `app/utils/emulator/mumu.py.bak-closefix-20260928-021922`  (sha256 `F4DD19B06A5EE4AE…` = 改前原版)
  - `app/utils/constants.py.bak-medicine-20260928-021922`     (sha256 `F4199F21E211C365…`)
  - `app/task/MAA/AutoProxy.py.bak-medicine-20260928-021922`  (sha256 `42D8BDFCA9D35FD0…`)
- 已核实 `main.py` 的 `uvicorn.Config(...)` 无 reload ⇒ 落盘对在跑侧车零影响（新码只在下次进程启动生效）。

## 4. 重启记录（非人工：宿主计划重启）

- 宿主存在**每日计划重启**：事件日志 id=1074 `shutdown.exe … 重启 … NT AUTHORITY\SYSTEM`，
  历史可见 09-26 19:00 / 09-27 04:00、19:00 / 09-28 04:00（即 04:00 与 19:00 各一次）。
- 本次 04:00:01 计划重启 → 旧侧车 33976（03:xx 仍在跑 catch-up）随机器停止；
  **04:01:34 新侧车 15124 自动拉起**，加载的即为已落盘补丁（02:19 起在盘）。
- 04:00:31 前后日志：`获取模拟器 23 信息失败`（关机态），随后 04:01:36 v5.4.0 启动横幅 —— 无我方操作成分。
- ⇒ 原定"人工排空窗口重启"步骤被该计划重启**提前且等效完成**：未人工 kill、未打断可挽回的完整轮次
  （唯一损失 = 恰在 04:00 在飞的 2 个 catch-up 任务，见 §5）。
- 侧车纪元：33976（19:54）→ 15124（04:01:34，含补丁）。

## 5. 验证证据（双向）

### 静态
- 远端三文件哈希 == 本地补丁版（重启后复核仍为 PATCHED，未回退）。
- 重启后 `py_compile` 通过、`mas_stage_fix --verify` = `API 46/46 通过；落盘待 flush 0；落盘异常 0`
  （t_52ebdda4 的 46 号关卡回退位未被重启回滚）。
- `FLAG=False`（未触发回滚开关）。

### 动态（04:09，实例 13，REST `/api/emulator/operate` → 同一 `close()` 代码路径）
```
04:09:33.926 | INFO    | MuMu模拟器管理 | 发送关闭命令: 设备13 (尝试 1/3)
04:09:36.076 | SUCCESS | MuMu模拟器管理 | 设备13已关闭
04:09:43.758 | INFO    | MuMu模拟器管理 | 设备13未在运行(VMM 进程与 adb 均不可用), 跳过关闭
```
- 关闭耗时 2.1s（VMM 进程 12156 消失，轮询 9s 内确认）；二次关闭=优雅跳过。
- 对照旧行为（同日志，23:47）：`关闭模拟器失败: 模拟器 11 关闭超时, 当前状态码: 0` / 历史 `设备X未在线，当前状态: 1`。

## 6. 计量（改前后对比与预期）

| 口径 | 改前（09-27 单日 / 6 日） | 改后预期 |
|---|---|---|
| 「用户任务结束, 关闭模拟器」意图 | 39 / 502 | 持平（不变） |
| 「设备X未在线，当前状态: 1」拦截 | 42 / 548 | → **0**（该形态仅存于旧 close 前置路径） |
| 真正发出 shutdown | ≈4 / 个位数 | ≈ 全部意图（跳过仅剩"确实已关"的合法分支） |
| 新日志形态 | — | 「发送关闭命令」→「已关闭」/「未在运行…跳过」/「关闭模拟器失败(重试3次)」 |

- **对现有空转实例**：04:00 计划重启已把实例清零（VMM=0，天然"清账"）；此后每次用户任务结束都会真正关闭
  其实例，空转实例不再累积；下一次观察窗口 = 05:00 早班起各轮次收尾（日志新增「已关闭」行即达成）。
- 观察建议：09-28 白天任一轮收尾后 `grep -c "设备.*已关闭" app.log` 增量 + `未在线` 数量趋零。

## 7. 回滚

1. **免重启即时回退**（首选）：`New-Item E:\AUTO-MAS\config\mumu_close_legacy.flag`
   → `close()` 下一轮起走旧逻辑（无需重启；验证后删除该文件即恢复）。
2. 全量回滚（含药水）：用 `.bak-*` 三件套覆盖回原文件后重启侧车：
   - `mumu.py.bak-closefix-20260928-021922` → `mumu.py`
   - `constants.py.bak-medicine-20260928-021922` → `constants.py`
   - `AutoProxy.py.bak-medicine-20260928-021922` → `AutoProxy.py`
3. 亦可仅置 `MUMU_RELIABLE_CLOSE = False`（需重启）。

## 8. 遗留与发现（与本次修复相关）

- **04:00/19:00 每日计划重启**：解释了此前多起"孤儿化"事件的另一种来源；夜间 catch-up 需规避该时刻。
- 本轮 04:00 重启孤儿化：q5（51d7a9de，用户"骰子" 重试中）/ q7（36126c69，用户"评估苹果" 重试中），
  即 09-27 回填各差 1 号；已通报 t_564593ae（可由早班自然覆盖）。
- 03:56:12 旧侧车日志出现 `final_task → push_notification → send_mail` `TimeoutError: timed out` traceback
  （通知邮件超时；进程未倒，属既有问题，未在本次范围内）。
- 验证期间为避免干扰未动 MAA/队列配置；实例 13 测试后状态 = 已关闭（净零）。
