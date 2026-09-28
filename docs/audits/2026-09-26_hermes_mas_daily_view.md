# Hermes 调用 MAS MCP 查看日常（2026-09-26 22:0x）：实跑记录与复核

> 需求：让 Hermes 调用 MAS 的 MCP 查看今日日常情况。
> 结论：**已实跑，只读，零写操作**；今日 38 个账号全部 DONE、0 失败；晚班批次仍在推进（8 个 MAA 在飞、20 个待跑）。

## 1. 跑的是什么

```powershell
hermes -p mas chat --query-file tmp\hermes_daily_check\prompt.md -Q -t auto-mas-monitor,maaorch-daily
```

- `-t` 显式点名两个 MCP server ⇒ **注册期硬白名单**（`hermes_cli/tools_config.py:692-704` `_merge_mcp_servers`：点名即 allowlist）。
  实测 `agent.log`：`MCP server 'maaorch-daily': registered 6 tool(s)` + `'auto-mas-monitor': registered 10 tool(s)` ⇒ **16 个**；
  `auto-mas-scrubbed`（118 工具/44 写）与 ekko-studio-* **一个都没进来**（不点名时它们会被整体并入，见 config.yaml 注释）。
- 提示词写死只读：禁调 `mas_rerun`（任何 dry_run 取值）、禁写类工具、不补跑、不发车、不读凭据文件，工具预算 ≤6 次。
- 会话：`20260926_215944_2951fc`；产物：`tmp/hermes_daily_check/{prompt.md,run.ps1,run.log}`。

## 2. Hermes 的简报（run.log 原文，未改一字）

```text
【AUTO-MAS 日常巡检 · 2026-09-26】
采集时刻：2026-09-26 22:00:08（工具上报；host 快照 21:59:55，晚班）
今日（源：mcp__maaorch_daily__mas_status(days=1)，口径=MAAOrch 发车台账「today」聚合）：
总账号 38｜完成 DONE 终态 38（一次过 25＋重试后成功 13）｜失败 0｜重试 13｜runs 52／errors 13。
交叉验证（mcp__auto_mas_monitor__search_history_api_history_search_post，mode=DAILY，2026-09-26 单日）：
38 账号共 52 条留痕，末条全为 DONE；13 条 ERROR 均为「MAA 未能正确登录 PRTS」，重试后全部恢复。
失败名单：无。
未完成/未跑 21 个（never_ran 口径）：摸摸摸摸鱼、LAZA、Gitano、hbznn119-日、PAGI、LEUNG、祝你祝我天天开心、
爱尔奎特我老婆、hbznn119-官、评估苹果、喵喵喵喵、睡个大鸡蛋、emmmmmm、石头城修行的深海鳗、Raiz、史蒂芬芬、
酸酸酸酸、舒7ucky、不要加班了、Buling、用户_99606616。
在跑：是。在飞 MAA 8 个；晚班 12 条队列共 57 项（done 37／pending 20）；waiting=20；末笔完成 21:54。
补充：C/E 盘余量 30G/139G 属低水位；PAGI 在 pending 但今晨 06:02 已 DONE（若排到即重复发车）；
hbznn119-官 今日无留痕且不在任何队列。
补漏候选（不执行、不发车）：无失败终态账号；20 个 pending 属正常排队而非漏跑；唯一待核候选为 hbznn119-官。
自检：MCP_API_TOOL_COUNT=16 ／ ISOLATION=PASS（本次全程仅调用只读工具）
调用清单：mas_status(days=1)；get_overview…()；search_history(DAILY,2026-09-26~2026-09-26)
```

## 3. Lead 独立复核（不是复述 Hermes 的话）

复核手段：MAAOrch 自身只读面 `GET 127.0.0.1:19997/api/mas/status?days=1`（本机 token，只读），22:03–22:04。

| Hermes 报的 | 独立复核值 | 判定 |
|---|---|---|
| 今日 38 账号 / DONE 38（一次过 25 + 重试 13） | `today: accounts=38, ok=25, retry=13, fail=0, runs=52, errors=13` | ✅ 逐项一致 |
| 失败名单：无 | `failed_names: []` | ✅ |
| 在飞 MAA 8 | `host.maa_processes=8` | ✅ |
| waiting/pending 20 | `waiting: 20` | ✅ |
| never_ran 21 个（名单见上） | `host.never_ran` 21 项，**名单逐字一致** | ✅ |
| C 30G / E 139G 低水位 | `disk.C.free_gb=30 low=true` / `disk.E.free_gb=139 low=true` | ✅ |
| 未发车（本任务只读） | `rerun: {running:false, accounts:[]}`；`runtime/mas_ledger.jsonl` 最后写入仍是 **2026-09-22** | ✅ 无补跑 |
| ISOLATION=PASS | 本会话 `tool_executor` 仅 4 条：`tool_describe` + `mas_status` + `get_overview` + `search_history`；`mas_rerun` **0 次**；`logs/mcp-stderr.log` 无 mas_rerun 审计行 | ✅ 零写 |

**两条 Hermes 提出、本次未独立复核的观察**（需要时再看）：
1. `PAGI` 今晨已 DONE 却仍排在晚班 `晚班定时7` pending ⇒ 排到会当天重复发车（复核可见：它确实在 pending 列表里，但"今晨 06:02 已 DONE"来自 search_history 明细，本次未重读）。
2. `hbznn119-官` 今日零留痕、且不在任何晚班队列的 pending 名单里 ⇒ 疑漏配排程（复核可见：never_ran 含它、12 条晚班队列的 pending 均不含它；但"是否真的漏配"要看 `scripts/user` 配置，本次未读）。

## 4. 开销与边界

- 211.6 s / 5 次 LLM / 4 次工具调用（含 1 次 `tool_describe` 桥接）/ ≈140k token（in 119,069 + out 21,420）。
- `tool_search` 把这 16 个 MCP 工具整体延迟到桥接层（`16 deferred`），所以第一跳是 `tool_describe`。
- **本次没有改任何配置**：`profiles/mas/config.yaml`、`cron/jobs.json` 均未动；命令本身即回滚（再跑一次带 `-t` 即可）。
- 硬边界现状（诚实声明）：本轮的"只读"由三件事共同保证 —— ①`-t` 点名式白名单把 118 工具的 `auto-mas-scrubbed` 挡在注册期之外；
  ②提示词禁调 `mas_rerun`；③`mas_rerun` 上游默认 `dry_run=True`（不显式传 False 不会下发）。
  若要**配置级硬闸**（不靠提示词），需在 `mcp_servers.maaorch-daily.env` 加 `MAAORCH_DAILY_NO_DISPATCH: '1'`（代理侧零上游请求），
  但那会同时禁掉 cron 自动路径的补漏能力，属策略变更，本次未做。

## 5. 追加核对（22:5x–23:0x）：`hbznn119-官` 到底挂在哪个脚本

用户提问：「hbznn119-官 在官-1 里？」

| 轮次 | 执行者 | 手段 | 结论 |
|---|---|---|---|
| 第一轮 22:53 | Hermes（`-t auto-mas-monitor`，只读，4 次调用） | `get_script` + `get_user(scriptId=378f7379…)` | `HBZNN119_GUAN_IN_GUAN1=不在`。官-1-1-7 用户 4 个：汐影月k / 潮响 / tbNick_2l3vf / him；受预算限制未能定位真实归属 |
| Lead 全库扫描 22:5x | Lead（AUTO-MAS REST 只读） | `/api/scripts/get` + `/api/scripts/user/get` × 54 脚本 | **唯一命中：明日方舟-官-28**（scriptUid `cd11a627-8f5c-4693-a2ae-fafcb569f5b0`，Path `E:\MAA-Multi\MAA_8`），且是该脚本**唯一**用户（Status=true、Server=Official、Stage=SR-7）；`hbznn119-日` 在 明日方舟-日-1（`3d7ba1e8…`） |
| 第二轮 22:58 | Hermes（只读，5 次 API 调用） | 三个脚本逐个 `get_user` | `HBZNN119_GUAN_SCRIPT=明日方舟-官-28`，对照表与 Lead 扫描逐项一致 |

**队列侧定位（Lead，REST `/api/queue/item/get` 扫全部 25 条队列的 ScriptId）：**

- **没有任何队列项引用 `官-28`** ⇒ `hbznn119-官` 是「配置在册、但从未排班」，这才是它今日零留痕的原因 —— 不是"漏跑"，是"没排班"。（§3 的观察 2 由此结案，且性质比原判更明确。）
- 引用关系：`官-1-1-7` ← 日常定时6 / 晚班定时6（另加已禁用的 临时-补跑-0923_0044-1）；`日-1` ← 日常定时7 / 晚班定时7。

**衍生风险：** `hbznn119-日` 挂在 `晚班定时7`，今晚连续失败 3 次（21:03 / 21:38 / 21:57，均为「MAA 在完成任务前退出」），目前仍在 pending —— 符合"批次里卡住的账号"特征，建议下一轮巡检重点盯。

产物：`tmp/hermes_daily_check/hbznn119/{prompt.md,prompt2.md,run.log,run2.log}`（两轮均只读，无写操作）。
