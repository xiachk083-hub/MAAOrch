# 最小闭环：上指定账号 → 读干员与仓库 → 落 JSON（t_c2442fc7 交付记录）

- 日期: 2026-09-28 07:33–07:47（+08:00）
- 目标账号: **y9Flipped**（官服，uid **64323902**，掩码 192****7973）
- 脚本/目录: 明日方舟-官-2-4-47 / `E:\MAA-Multi\MAA_2` / 实例 14（adb 127.0.0.1:5583）
- 交付物: `runtime/reports/accounts/y9Flipped/{operators.json, depot.json, evidence/*, raw/*}`
- 脚本资产: `tmp/boxread/host_boxread.py`（主机侧运行，主机副本 `E:\_hermes_boxread\host_boxread.py`；未提升为 tools/ 正式工具）

## 结论

闭环跑通：**ManualReview 切号到大厅 → asst 子进程直连 MaaCore 读 OperBox+Depot → 落两 JSON（含 uid/证据引用）→ stop 收尾（解锁+关模拟器+配置字节级还原）**。
全程 **未作战/未领奖/未改配置**：MaaCore 本会话只跑了 4 条任务链（StartUp×2、OperBox、Depot，见 `raw/asst_taskchain_counts.txt`），无 Fight/Mall/Recruit/Award 链。

## 证据链（账号归属）

| 环节 | 证据 | 位置 |
|---|---|---|
| 发车（ManualReview） | app.log `开始运行任务: 550fc698…, 模式: ManualReview`（07:33:33） | `raw/applog_wake_stop_evidence.txt` |
| **切号** | gui.log `账号切换 -->> 192****7973`（07:36:17） | `raw/gui_log_switch_evidence.txt` |
| 落地大厅 | gui.log `完成任务: 开始唤醒` / `Completed Task Chain: StartUp`（07:38:46） | 同上 |
| **身份核对** | 大厅截图 OCR `ID:64323902`（左下胸牌）= y9Flipped 台账 uid | `evidence/shot_1_lobby.png`、`raw/lobby_ocr.txt` |
| 干员页实拍 | `shot_2_operbox.png`（等级/稀有度/职业筛选 + 干员名，OCR 命中） | `evidence/` |
| 仓库页实拍 | `shot_3_depot.png`（消耗物品/基础物品/养成材料/特别纪念 tab + 数量） | `evidence/` |
| 读后收尾 | 收尾帧 `shot_5_afterback.png`（回大厅，ID 一致） | `evidence/` |
| 停任务 | app.log `已解锁脚本配置 919c8a3b…`、`设备14已关闭`、`任务结束: 550fc698…`（07:47:05–07:47:28） | `raw/applog_wake_stop_evidence.txt` |
| 配置还原 | `E:\MAA-Multi\MAA_2\config` 与唤醒前快照 **`diff -r` 无差异**（含被注入的 gui.new.json） | 本机验毕（快照 `E:\_hermes_boxread\cfg2_{pre,post}`） |

## 数据摘要

- operators.json: **419** 名已拥有干员（全量 460），字段 name/rarity/elite/level/potential + 原始 id（char_xxx）
- depot.json: **89** 类物品，龙门币 14,819,000 / 合成玉 27,000 等；同时保留 MAA 原始物品 ID 映射

## 与卡片的偏差（照实记录）

1. **步骤 3 的实现方式**：卡片写"用 AUTO-MAS 截图+模板/文字点击进入两页"，改用 **MaaCore 原生 OperBox/Depot 任务**（asst 子进程直连）——这正是父卡勘景 `docs/HERMES_ACCOUNT_INSPECT_PLAN.md` 的推荐主链路（AUTO-MAS 三模式均不带原生识别；窗口点击路实测模糊匹配命中非设备窗）。两个页面均有实拍截图佐证（见上表）。
2. **收尾帧一度停在仓库页**：收尾 StartUp 的 `@ReturnButton` 在仓库页未命中且 max_times=0（core 日志），游戏停在仓库页；随后用 1 次 `adb shell input keyevent 4`（BACK，纯导航）退回大厅并用截图复核（`shot_5_afterback.png`）。stop 后模拟器关闭，设备端状态被清理。
3. **"指定账号"的取法**：目标目录 MAA_2 当刻全队已收尾、但其官服脚本 官-2-4-47 是 **4 用户脚本**（ManualReview 按脚本用户序取首个 = y9Flipped，配置序经 REST 核实）；其余单用户官服目录彼时均被早班占用（12/12 槽位、MAA_6 潮响理智 999 长跑）。切号/大厅双证据确认了实际目标号就是 **y9Flipped**。
4. **簿记副作用（非游戏侧）**：ManualReview 停任务会把该用户 `IfPassCheck` 置 False（UI 红标签"人工排查未通过"，仅展示用）；y9Flipped 当日 `LastProxyDate` 已是当天，`ProxyTimes/RemainedDay` 未被扰动（源码：`app/task/MAA/ManualReview.py:116-118`、`manager.py:447-454`）。**游戏内无任何资源/状态变化**。

## 复用资产

- `tmp/boxread/host_boxread.py` —— 主机侧读数 runner（`--vmindex 14 --maa-dir … --out …`；StartUp→OperBox→Depot→StartUp；截图+raw JSON+state.json；判据 `BOXREAD_DONE`）
- `tmp/boxread/find_emu.ps1` —— VMM 进程→实例 adb 端口发现（实测 idx13/14 可用）
- `tmp/boxread/poll.py` / `watch.sh` —— 班次槽位收尾轮询；`pull.sh` —— 主机 run 目录回传
- `tmp/boxread/finalize.py` —— 身份闸门（大厅 OCR 出 ID 必须等于台账 uid）+ 落盘
- 主机侧已部署: `E:\_hermes_boxread\{host_boxread.py, find_emu.ps1, host_asst_smoke.py}`（Python 3.12.9 + MAA_2/6/13 自带 asst 可用，无需安装任何东西）

## 待办 / 建议

- 若要批量读号（晚班前），把 host_boxread.py 提升为 tools/ 正式工具并加参数化（账号表驱动）；本次按卡片范围只跑通了最小闭环。
- 窗口纪律：唤醒必须在目标目录整队收尾后（本目录脚本锁 + 同目录互杀）；读期间保持 ManualReview 挂起（它等客户端应答，天然占住窗口）。
