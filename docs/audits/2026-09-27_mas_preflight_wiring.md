# 班前自检接入建议（只出建议 —— 未改动任何调度/巡检文件）

2026-09-27 · 产物 = `tools/mas_preflight.py`（`--queue <名>` / `--script <名>`；输出 `PREFLIGHT=GO/NO-GO`
+ 逐项明细；`--json`；退出码 0/1/2）。

运行（必须用 Python312 —— OCR 用 rapidocr）：

    C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe D:\1-2\MAAOrch\tools\mas_preflight.py --queue 晚班定时1

    # 巡检/对账等高频场景：跳过截图 OCR（~20s）
    ... mas_preflight.py --queue 晚班定时1 --skip-screen

检查项 = REST 可达 / 目标 MAA 槽位空闲 / 目标模拟器实例可 adb（未开只判「可 launch」，不启动）/
客户端无残留模式（截图+OCR）/ 注入配置合规（复用 mas_switch_gate）/ 磁盘水位。
只读：不发车、不启停实例、不改配置；截图落 `runtime/preflight_shots/`。

## 方案 A（推荐）：调度闸门 —— mas_dispatch.py 真发车前调用

位置：`tools/mas_dispatch.py` 的 `dispatch()`，`reachable()` 通过之后、`POST /api/dispatch/start` 之前。

最小改动（示意，三步）：

```python
# dispatch() 内，reachable() 之后：
ok_pf, why_pf = preflight_go(name)          # subprocess 调 mas_preflight --queue <name> --json（一次 ~40-60s）
if not ok_pf and not force:
    return {"queue": name, "ok": False, "refused": "preflight_no_go", "detail": why_pf}
```

要点：

- 触发条件天然受限：只在「到点且今天没发过」时才调用 ⇒ 每队列每天 ≤1 次；3 分钟对账周期装得下。
- 默认 fail-closed（NO-GO 拒绝发车）；人工绕过沿用现有 `--force`（仍记审计）。
- 把 preflight 的 `reasons` 一并写进 `runtime/mas_dispatch_log.jsonl`（一行即可）。
- 若嫌 40-60s 慢：对账路径可用 `--skip-screen`，全量（含截图 OCR）留在发车那一次。

## 方案 B：巡检面（cron job 52b88b0ffca0，无 terminal）

巡检 job 只有 MCP 工具面，直接跑不了 `mas_preflight.py`。两条路：

1. 仿 `tools/mas_dispatch_mcp.py` / `mas_triage_mcp.py` 增加只读包装 `mas_preflight_mcp.py`，
   在巡检提示词里加一行「对下一跳队列跑 preflight，输出一行 `PREFLIGHT=...`」；
2. 或由有 terminal 的巡检变体按轮执行 `--skip-screen` 版。

建议：巡检每轮只跑 `--skip-screen`（REST+槽位+实例+闸门+磁盘）；截图 OCR 只在真发车路径跑，
避免与跑批抢 adb/截图（实测边跑边截无碍，但省一份开销）。

## 方案 C：班前哨兵（独立 cron，可选）

发车前 5 分钟（如 04:55 / 19:25）对「本班 12 条队列」批量跑 `--skip-screen`，
汇总一张 PREFLIGHT 报告（哪条 NO-GO、哪个槽位忙/实例失联/闸门违规）——把问题暴露在
05:00 第一批发车之前，而不是发不出去才发现。

## 已知偏差 / 注意事项

- 第 3 项不走题目里的 `MuMuManager adb --vmindex <idx> connect`：实测它在**健康实例**上也会
  误报 `-201 vm not running`（状态面假死）。改为 VMM 进程 → 监听端口 → `adb get-state`（+boot_completed）。
- 第 4 项关键词判据（来源 kb_maaerr / 实例 13 实锤）：
  `登录被取消` = FAIL（残留模态会让 StartUp 必然失败）；`更新/维护/网络/公告` = WARN；
  标题页按钮类（`查看公告` 等）已排除；前台=启动器（停在桌面）= WARN。
- 磁盘阈值默认 FAIL<10GB、WARN<30GB（当前 C: ≈29.7GB 将稳定报 WARN，可 `--min-free-gb/--warn-free-gb` 调）。
- OCR 依赖 Python312+rapidocr：用纯 python 跑且未 `--skip-screen` 时第 4 项 fail-closed FAIL（有明确提示）。
- 并发：截图默认落 `runtime/preflight_shots/`（时间戳命名，可并跑）。部署当天已有其它并行 worker 在跑本工具
  （出现非本任务发起的 idx13 截图）。若多 agent 长期并发调用，建议后续给截图目录加 `--shot-dir` 参数隔离。
