# 无人值守跑队列（Lead 专用启动器）

> 用途：把"演习优先 → 3★ 才真打 → 结算复核 → 账本"这条环**连续跑几小时**，这是冲 100 颗的前提
> （见 `docs/PLAN_100_STONES.md`：日产能 18~20 颗，纯自然约 4 天、配药约 3 天）。
>
> **纪律**：同一模拟器**只能有一个 MAA**。启动前必须确认没有别的 `event_sweep.mjs` / `stage_attempt.py`
> 在跑（脚本会自己检查并拒绝启动）。

## 前置检查（脚本自动做）

1. 常驻服务在 8765 活着（不在就 `Start-Process python tools\maaorch_service.py --port 8765 -WindowStyle Hidden`）；
2. **没有**其它 `event_sweep.mjs` / `stage_attempt.py` 进程；
3. 不碰用户自己的 MAA（emu #0，常驻 PID 由 `02:54` 那类老进程识别 —— 脚本只检查"我们的"脚本进程，不杀任何 MAA）。

## 启动

```powershell
pwsh -File tools\run_campaign.ps1                 # 默认：跑到理智 < 9 或连续 3 次无进展
pwsh -File tools\run_campaign.ps1 -Hours 6        # 最多跑 6 小时
pwsh -File tools\run_campaign.ps1 -MaxUnits 20    # 最多打 20 个单位就停（便于观察）
```

## 关键参数（都由队列/sweep 自己把关）

| 参数 | 值 | 作用 |
|---|---|---|
| `--sanity-min` | 本关理智（队列按 `u["ap"]` 传） | **理智绝不掉到 9~21 以下**；一晚最坏消耗 ≈192（实测核算） |
| 演习优先 | sweep 默认 | 每关最多 2 份轴**演习**（0 理智），**只有 3★ 才真打** |
| `--drill-broken-tries` | 1 | 演习通道整体坏时最多真打 1 份（把最坏浪费从 3× 压到 1×） |
| `no_axis` | 零理智放弃 | 演习全轴不达标 ⇒ 不真打、换下一关 |
| 漏怪 | 判"轴不行" | `--abort-on-leak` 掐局 ⇒ 记 `drill-leak-not3`，**不回退真打** |

## 日志与停机条件

- 队列日志：`runtime/_prime_queue3.log`（本脚本再包一层 `runtime/reports/campaign_<日期>.log`）
- 停机条件（任一命中即停）：① 理智 < 9（队列自己会停）；② 连续 3 轮无进展；③ 达到 `-Hours` 时限；
  ④ 检测到**另一个** sweep/attempt 进程出现（说明有人接管了模拟器）。
- **每停一次都记账**：结束时打印 `runtime/stone_ledger.json` 的持有数与本轮新增，便于人工核对顶栏。
