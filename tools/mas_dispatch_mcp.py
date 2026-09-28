# -*- coding: utf-8 -*-
"""mas-dispatch MCP —— 给 Hermes 的 agent 一个"看班次 / 补位发车"的**窄工具面**。

背景：2026-09-27 起发车已由 Hermes 的调度任务接管（cron job mas-shift-dispatch → tools/mas_dispatch.py）。
但那个任务是无 agent 的脚本任务；agent（巡检员）原来只有只读 MCP，**看得见漏发却补不了**。
本 server 就补这一个缺口：注册期白名单 5 个工具（只读 3 个 + 写 2 个，写动作全部默认预演）。

  list_schedule()                                    只读：班次表 + 今日已发 + 到点未发 + MAS 定时漂移 + 可达性
                                                     + 未消除的发车告警（dispatch_alerts，巡检必须汇报）
  dispatch_due(dry_run=True)                          对账补位发车（等价 mas_dispatch.py --reconcile --apply）
  dispatch_queue(queue, dry_run=True, force=False)    单条发车（默认预演，绝不默认真发）
  switch_gate()                                       只读：切号注入闸门 v2（12 个 MAA 目录的
                                                      Server↔(AccountName, AccountSwitchEnabled) 注入校验）
  patch_guard()                                       只读：补丁持久化闸门（AUTO-MAS 代码补丁是否还在：
                                                      AutoProxy 切号 v2 / 理智药、mumu 判活关闭、constants 理智药）

闸门全部复用 tools/mas_dispatch.py：白名单 / AUTO-MAS 可达 fail-closed / 单飞（当天只发一次）/
MAAORCH_DISPATCH_NO_GO 硬闸 / 必须 code==200 且带 taskId。真发车逐条写 runtime/mas_dispatch_log.jsonl。
发车失败按 tools/mas_dispatch.py 的分类处理：rest_unreachable / http_error / bad_response / no_taskid /
target_running / slot_occupied / target_not_found / queue_missing；前两类与 target_running、slot_occupied
会退避重试（脚本侧默认 3 次 60/180/300s），终态失败写 runtime/mas_dispatch_alerts.json 台账并在 stdout 打
DISPATCH_ALERT=<队列:原因>。**巡检补位调用一律 retries=0**：分类与告警照常返回，退避重试交给 3 分钟节拍的
调度任务（避免 MCP 工具调用长时间阻塞）。
switch_gate 复用 tools/mas_switch_gate.py（纯只读：ssh 读 gui.new.json + REST 取脚本/账号信息）。
patch_guard 复用 tools/mas_patch_guard.py（纯只读：ssh 读补丁文件 + 特征断言/哈希，清单 runtime/patches/manifest.json）。

用法：
  python tools/mas_dispatch_mcp.py            # stdio MCP server（Hermes 用 command+args 拉起）
  python tools/mas_dispatch_mcp.py --check    # 自检：import 闸门 + 只读探测，退出码 0/2
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mas_dispatch as md  # noqa: E402

SERVER_NAME = "mas-dispatch"

_SCHEMAS = [
    types.Tool(
        name="list_schedule",
        description=("只读：Hermes 持有的 24 条班次表（日常定时1-12 05:00..05:33 / 晚班定时1-12 19:30..20:03）、"
                     "今日已发记录、到点但未发的队列（due_not_sent）、超出追发窗口的（late）、"
                     "AUTO-MAS 侧定时项漂移、AUTO-MAS 可达性、未消除的发车告警（dispatch_alerts）。"
                     "巡检时先看这个判断'调度有没有漏'；dispatch_alerts 非空必须在报告里写 DISPATCH_ALERT=<队列:原因>。"),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    types.Tool(
        name="dispatch_due",
        description=("对账补位发车：到点且今天没发过的班次队列，逐条 POST /api/dispatch/start "
                     "{\"taskId\":\"<队列uid>\",\"mode\":\"AutoProxy\"}。dry_run=True（默认）只预演。"
                     "幂等：同队列同一天只发一次（状态文件），超过追发窗口只返回 LATE 不发。"),
        inputSchema={"type": "object",
                     "properties": {"dry_run": {"type": "boolean", "default": True,
                                                "description": "True=只预演（默认）；False=真发车"}},
                     "additionalProperties": False},
    ),
    types.Tool(
        name="dispatch_queue",
        description=("单条班次队列发车（队列名必须是 日常定时N / 晚班定时N，N=1..12）。"
                     "dry_run=True（默认）只预演；force=True 才能忽略当天已发记录。"),
        inputSchema={"type": "object",
                     "properties": {"queue": {"type": "string"},
                                    "dry_run": {"type": "boolean", "default": True},
                                    "force": {"type": "boolean", "default": False}},
                     "required": ["queue"], "additionalProperties": False},
    ),
    types.Tool(
        name="switch_gate",
        description=("只读：切号注入闸门 v2。校验 12 个 MAA 目录 config\\gui.new.json 的账号切换注入是否符合口径："
                     "Server=Official ⇒ AccountName 必须掩码(***\\*\\*\\*\\*) 且 AccountSwitchEnabled=true；"
                     "其它服（Bilibili/YoStarJP/txwy 等）⇒ AccountName 必须为空且开关 false。"
                     "返回 switch_gate=OK / VIOLATION:<槽位列表> / UNKNOWN:<...>（fail-closed）。永不改配置。"),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    types.Tool(
        name="patch_guard",
        description=("只读：补丁持久化闸门。校验我们打在 AUTO-MAS 源码上的补丁是否还在（AutoProxy 切号 v2 + "
                     "理智药、constants 理智药、mumu 判活关闭）：文件存在 + 特征断言 + 哈希（清单 runtime/patches/manifest.json）。"
                     "升级/重装会静默覆盖这些补丁。返回 patch_guard=OK / MISSING:<n> / UNKNOWN:<...>（fail-closed）；"
                     "MISSING ⇒ 报告里写 `PATCH_GUARD=MISSING:<n>` 并在证据行指名叫人处理（重放需批准，不在巡检里自动做）。永不改文件。"),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
]


def _ok(payload: dict) -> list:
    return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=1))]


def _tools_list() -> dict:
    reach, why = md.reachable()
    table = md.schedule()
    state = md.load_state()
    today = dt.date.today().isoformat()
    sent = state.get(today, {})
    try:
        mt = md.mas_timers()
    except Exception:  # noqa: BLE001
        mt = {}
    drift = sorted(n for n, t in table.items() if n in mt and (mt[n]["Time"] != t or mt[n]["Enabled"]))
    now = dt.datetime.now()
    due, late = [], []
    for name, hhmm in table.items():
        if name in sent:
            continue
        hh, mm = hhmm.split(":")
        due_at = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if now < due_at:
            continue
        (late if (now - due_at).total_seconds() > md.GRACE_MIN * 60 else due).append("%s@%s" % (name, hhmm))
    return {"reachable": reach, "detail": why, "now": now.strftime("%Y-%m-%d %H:%M:%S"),
            "scheduled_queues": len(table), "sent_today": sorted(sent.keys()),
            "due_not_sent": sorted(due), "late_beyond_grace": sorted(late),
            "mas_timers_drift_or_enabled": drift, "grace_min": md.GRACE_MIN, "no_go": md.NO_GO,
            "dispatch_alerts": _alerts_view(), "dispatch_alerts_count": len(_alerts_view())}


def _alerts_view() -> list:
    """未消除的发车告警（巡检视图：自动排除 test 注入的记录）。"""
    try:
        out = []
        for a in md.active_alerts():
            out.append({"queue": a.get("queue"), "reason": a.get("reason"), "ts": a.get("ts"),
                        "attempts": a.get("attempts"), "detail": (a.get("detail") or "")[:200]})
        return out
    except Exception:  # noqa: BLE001
        return []


def _reconcile(dry_run: bool) -> dict:
    table = md.schedule()
    now = dt.datetime.now()
    state = md.load_state()
    day = state.setdefault(dt.date.today().isoformat(), {})
    actions = []
    for name in sorted(table, key=lambda n: table[n]):
        if name in day:
            continue
        hh, mm = table[name].split(":")
        due_at = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if now < due_at:
            continue
        if (now - due_at).total_seconds() > md.GRACE_MIN * 60:
            actions.append({"queue": name, "late": True, "scheduled": table[name]})
            continue
        # 巡检补位只发一次（retries=0）：分类/告警照常，退避重试交给 3 分钟节拍的调度任务
        res = md.dispatch(name, "", apply_=not dry_run, st=state, retries=0)
        actions.append({"queue": name, "scheduled": table[name], "result": res})
    return {"dry_run": dry_run, "now": now.strftime("%Y-%m-%d %H:%M:%S"),
            "actions": actions, "count": len(actions)}


async def _call(name: str, args: dict):
    args = args or {}
    if name == "list_schedule":
        return _ok(_tools_list())
    if name == "switch_gate":
        return _ok(_switch_gate())
    if name == "patch_guard":
        return _ok(_patch_guard())
    if name == "dispatch_due":
        return _ok(_reconcile(bool(args.get("dry_run", True))))
    if name == "dispatch_queue":
        q = str(args.get("queue", ""))
        qm = None
        try:
            qm = md.queue_map()
        except Exception:  # noqa: BLE001  REST 不可达 -> 交给 dispatch 内部分类/重试/告警
            qm = None
        if qm is not None and q not in qm:
            return _ok({"ok": False, "refused": "unknown_queue", "queue": q,
                        "hint": "队列名必须是 日常定时N / 晚班定时N（N=1..12）"})
        return _ok(md.dispatch(q, (qm or {}).get(q, ""), apply_=not bool(args.get("dry_run", True)),
                               force=bool(args.get("force", False)), retries=0))
    return _ok({"error": "unknown tool: %s" % name})


def _switch_gate() -> dict:
    """切号注入闸门（复用 tools/mas_switch_gate.py 的 run_gate；纯只读）。

    返回裁剪后的摘要（不含 expected_masks/脚本明细，省巡检的上下文预算）：
    {switch_gate, rest, slots:[{name,server,account,switch,verdict,issues,notes}], violations, unknown}
    """
    try:
        import mas_switch_gate as sg
    except Exception as e:  # noqa: BLE001
        return {"switch_gate": "UNKNOWN:import-failed", "error": str(e)[:200]}
    try:
        res = sg.run_gate()
    except Exception as e:  # noqa: BLE001
        return {"switch_gate": "UNKNOWN:run-failed", "error": str(e)[:900]}
    slots = []
    for s in res.get("slots") or []:
        row = {"name": s.get("name"), "verdict": s.get("verdict"),
               "server": s.get("server"), "account": s.get("account"), "switch": s.get("switch")}
        if s.get("issues"):
            row["issues"] = s["issues"]
        if s.get("notes"):
            row["notes"] = s["notes"]
        slots.append(row)
    return {"switch_gate": res.get("switch_gate"), "generated_at": res.get("generated_at"),
            "rest": res.get("rest"), "slots": slots,
            "violations": res.get("violations"), "unknown": res.get("unknown")}


def _patch_guard() -> dict:
    """补丁持久化闸门（复用 tools/mas_patch_guard.py 的 run_guard；纯只读）。

    返回裁剪后的摘要（不含备份明细/回滚文本，省巡检的上下文预算）：
    {patch_guard, generated_at, ok_count, missing, unknown, warn, loaded,
     patches:[{name, verdict, issues?, notes?}], hint?}
    """
    try:
        import mas_patch_guard as pg
    except Exception as e:  # noqa: BLE001
        return {"patch_guard": "UNKNOWN:import-failed", "error": str(e)[:200]}
    try:
        res = pg.run_guard(pg.load_manifest())
    except Exception as e:  # noqa: BLE001
        return {"patch_guard": "UNKNOWN:run-failed", "error": str(e)[:900]}
    rows = []
    for r in res.get("patches") or []:
        row = {"name": r.get("name"), "verdict": r.get("verdict")}
        if r.get("issues"):
            row["issues"] = r["issues"]
        if r.get("notes"):
            row["notes"] = r["notes"]
        rows.append(row)
    out = {"patch_guard": res.get("patch_guard"), "generated_at": res.get("generated_at"),
           "ok_count": res.get("ok_count"), "missing": res.get("missing"),
           "unknown": res.get("unknown"), "warn": res.get("warn"),
           "loaded": res.get("loaded"), "patches": rows}
    if res.get("missing"):
        out["hint"] = ("重放: python tools/mas_patch_guard.py --reapply <name> --apply"
                       "（需批准；改完必须重启侧车、走变更窗口）")
    return out


async def _main() -> None:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list() -> list:
        return _SCHEMAS

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: dict | None):
        return await _call(tool_name, arguments or {})

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def _check() -> int:
    problems = []
    reach, why = md.reachable()
    if not reach:
        problems.append("AUTO-MAS 不可达: %s" % why)
    if len(md.schedule()) != 24:
        problems.append("班次表数 != 24")
    for t in _SCHEMAS:
        if t.name not in {"list_schedule", "dispatch_due", "dispatch_queue", "switch_gate", "patch_guard"}:
            problems.append("白名单外工具: %s" % t.name)
    snap = _tools_list()
    print("mas_dispatch_mcp --check: %s" % ("FAIL" if problems else "OK"))
    print("  tools=%s" % ",".join(t.name for t in _SCHEMAS))
    print("  reachable=%s sent_today=%d due_not_sent=%s late=%s drift=%s alerts=%d"
          % (snap["reachable"], len(snap["sent_today"]), snap["due_not_sent"],
             snap["late_beyond_grace"], snap["mas_timers_drift_or_enabled"],
             snap["dispatch_alerts_count"]))
    for p in problems:
        print("  - " + p)
    return 2 if problems else 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(_check())
    asyncio.run(_main())
