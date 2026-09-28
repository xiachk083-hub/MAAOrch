# 发车失败自愈与告警（mas_dispatch 重试 + 记账 + 巡检上报）

日期：2026-09-27（晚班后，21:08–21:35 落盘）
看板：`maa-parallel-benchmark-20260926` / 任务 `t_1b4ce956`
目标：发车失败**不再静默** —— 分类 + 有上限退避重试 + 终态告警台账 + 巡检提示词显式上报。

## 1. 结论

`tools/mas_dispatch.py` 从「一次 POST 失败即静默结束」升级为：

- **失败分类（reason）**：`rest_unreachable` / `http_error` / `bad_response` / `no_taskid` /
  `target_running` / `slot_occupied` / `target_not_found` / `queue_missing` / `unknown_error`
  （分类依据 = 异常类型 + AUTO-MAS 返回体 `code/message` 关键词；实测源：
  `D:/MAS/app/api/dispatch.py` → `task_manager.add_task` 抛 `ValueError`，REST 统一包装
  `{code:500, message:"<ExcType>: <msg>"}`）。
- **可重试类**（`rest_unreachable`、`http_error` 5xx/408/429、`target_running`、`slot_occupied`）
  退避重试：默认 3 次，60/180/300 秒（env 可调）；dry-run 不等待；`retry_budget`（默认 700s）
  用尽则记 `deferred` 交给下一跳（cron 每 3 分钟一跳，追发窗口 30 分钟内自愈）。
- **队名解析并入尝试内**：`queue_map()` 失败（REST 不可达/坏应答）不再直接崩溃，而是按同一套
  分类/重试处理（修复点：旧版 `cmd_reconcile/cmd_queue/cmd_shift` 会在 REST 抖动时抛
  traceback 且不给告警）。
- **终态失败三件套**：`runtime/mas_dispatch_log.jsonl` 每次尝试一行（含 `reason/attempt/terminal/test`）；
  `runtime/mas_dispatch_alerts.json` 台账（active/cleared，按 队列+日期+原因 去重，7 天过期）；
  stdout 一行 `DISPATCH_ALERT=<队列:原因>`。成功自愈时输出 `DISPATCH_ALERT_CLEARED=<队列:原因>`。
- **并发锁**：`runtime/mas_dispatch.lock`（O_EXCL），真发车临界区串行（含重试等待）；
  被占 => 本轮 `refused=concurrent_dispatch` 跳过；陈旧锁（>1500s）自动回收。
- **巡检提示词**（cron job `52b88b0ffca0` `mas-readonly-inspection`）已打补丁：
  第二步读 `list_schedule.dispatch_alerts`，非空在汇报写 `DISPATCH_ALERT=<队列:原因>`（只报告不处置）；
  汇报行预算 12→13 行。
- **MCP 面**（`tools/mas_dispatch_mcp.py`）：`list_schedule` 新增 `dispatch_alerts`/`dispatch_alerts_count`
  （自动排除 test 注入）；`dispatch_due/dispatch_queue` 一律 `retries=0`（巡检补位单发一次，
  分类与告警照常返回，退避重试交给 3 分钟节拍的调度任务，避免工具调用长时间阻塞）；
  队名解析失败不再直接异常抛出。

## 2. 改动文件与备份（受控写入）

| 文件 | 改动 | 改动前备份（= 原样，可整体覆盖回滚） |
| --- | --- | --- |
| `tools/mas_dispatch.py` | 重试/分类/告警/锁/`--json`/`--selftest`/`--alerts`/`--task-id` | `tools/mas_dispatch.py.bak-retryalert-20260927-205715`（sha256 `c8d5834f…4384db`，与 `.bak-prereplace-20260927-210913` 同） |
| `tools/mas_dispatch_mcp.py` | alerts 暴露 + retries=0 + 解析容错 | `tools/mas_dispatch_mcp.py.bak-retryalert-20260927-205715` |
| `tests/test_mas_dispatch_retry.py` | 新增（13 条离线用例） | —（新增文件） |
| cron `jobs.json`（mas profile） | job `52b88b0ffca0` prompt 补 `DISPATCH_ALERT` | `…/profiles/mas/cron/jobs.json.bak-retryalert-20260927-213036` |

改动后哈希：`mas_dispatch.py` = `73a04d5f…`（49,281B，1083 行）；`mas_dispatch_mcp.py` = `e5815024…`（11,933B）；
`jobs.json` prompt len 2254→2671 字符（回读与目标文本逐字节一致）。

## 3. 回滚

```bash
# 代码
cp -p tools/mas_dispatch.py.bak-retryalert-20260927-205715 tools/mas_dispatch.py
cp -p tools/mas_dispatch_mcp.py.bak-retryalert-20260927-205715 tools/mas_dispatch_mcp.py
# 巡检提示词（恢复旧 prompt；或直接回拷备份 jobs.json 后让调度器重载）
cp -p "C:/Users/xiach/AppData/Local/hermes/profiles/mas/cron/jobs.json.bak-retryalert-20260927-213036" \
      "C:/Users/xiach/AppData/Local/hermes/profiles/mas/cron/jobs.json"
# 台账/锁（可留可删，互不依赖）
rm -f runtime/mas_dispatch_alerts.json runtime/mas_dispatch.lock
```

回滚不影响 `runtime/mas_dispatch_state.json`（发放记录）与 `mas_dispatch_log.jsonl`（追加日志）。

## 4. 验证证据（全部实测）

1. `python tools/mas_dispatch.py --selftest` → `SELFTEST=OK (17/17)`（含：首次成功 / dry-run 不发 POST /
   解析抖动重试复原 / 解析持续失败告警 / 已在运行重试 / 槽位被占告警后自愈 / 无 taskId / 坏应答 /
   找不到队列 / 告警去重 / 锁被占与陈旧锁回收 / 预算 defer / test 隔离与回收）。
2. `pytest tests/test_mas_dispatch_retry.py -q` → `13 passed`（离线：桩传输 + tmp runtime + sleep 打桩）；
   `pytest tests/ -q --ignore=tests/test_mas_agent_time.py` → `313 passed`（全库回归不红）。
3. dry-run：`--queue 晚班定时9` → `DRY  晚班定时9 would_dispatch`；
   `--reconcile --json` → `{"action":"reconcile","apply":false,...,"count":0,...}`（全发过时零动作）。
4. 坏 taskId 实测（不真发车）：`--queue 日常定时1 --apply --force --task-id retryalert-selftest-bad-id`
   → `FAIL … reason=target_not_found detail="code=500 ValueError: badly formed hexadecimal UUID string"
   attempts=1 alert=日常定时1:target_not_found` + stdout `DISPATCH_ALERT=日常定时1:target_not_found`。
5. 重试路径实测（指向 127.0.0.1:9 不可达 + `--retries 2 --retry-delays 1,1`）→ 3 次尝试、
   日志 `attempt=1/2/3 retries_used=0/1/2 terminal=true`、终态 `rest_unreachable`、告警一行；14s 完成。
6. `--alerts`（巡检视图）看不到 test 注入；`--alerts --all` 可见 `[test]`；
   `--clear-test-alerts` → `test alerts cleared: 2`，reconcile 后台账 `active=[]`。
7. `tools/mas_dispatch_mcp.py --check` → OK（reachable=True sent_today=24 due=[] late=[] drift=[] alerts=0）。

## 5. 运维要点

- 调参（env，cron 任务继承）：`MAAORCH_DISPATCH_RETRIES` / `MAAORCH_DISPATCH_RETRY_DELAYS` /
  `MAAORCH_DISPATCH_RETRY_BUDGET_S` / `MAAORCH_DISPATCH_LOCK_STALE_S`；`MAAORCH_DISPATCH_NO_GO=1` 禁发车。
- 日常自查：`python tools/mas_dispatch.py --check`（含台账/锁状态行）、`--alerts`。
- 巡检合同：`list_schedule.dispatch_alerts` 非空 ⇒ 报告行 `DISPATCH_ALERT=<队列:原因>`。
- 下一跳窗口：调度任务 `mas-shift-dispatch`（job `2600f6415f16`）**次日 05:00** 起跑新代码；
  巡检（30m 间隔）下一轮即读新 prompt/新字段。

## 6. 遗留 / 下一步（不在本卡范围）

- 告警台账只有「读」面（巡检/`--alerts`）；如需推送（Telegram/Webhook）另行接 `deliver`，本卡未做。
- `http_error` 目前映射到 `http_error` 类名但仅在 5xx/408/429 可重试；如遇 AUTO-MAS 侧 4xx 语义变化需再校准。
- 双胞胎卡 `t_570f1faa`（同题）已 archived；两卡若都曾落盘，以本文件哈希为准。
