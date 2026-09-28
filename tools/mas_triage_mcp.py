# -*- coding: utf-8 -*-
"""mas-triage MCP —— 给 Hermes 的 agent「失败定因 + 每日错误聚合」**只读**工具（2 个工具）。

问题：巡检 agent 只看得到 AUTO-MAS 的笼统结论（"MAA 在完成任务前退出"），分不清
"实例/网络故障（该补跑）"还是"资源类可忽略（如公招没券、招募确认走不下去）"。
本 server 暴露 2 个只读工具：

  triage_error(account)  ->  账号 -> 脚本 / MAA 目录 -> 读该目录 debug/asst.log 的
                              TaskChainError / SubTaskError -> 归类：
                              {kind, severity, reason, action, chains_with_error, evidence[]}
                              例：recruit_confirm_failed（severity=info，资源类，不补跑）
  error_daily(date?)     ->  调 tools/maa_error_daily.py --summary：四源（gui/asst/app.log/
                              history）当日错误聚合，返回巡检用一行摘要
                              `ERR_SUMMARY=<n> 条，Top=<类型>` + 报告路径（约 30-60s）。

写操作：**无**（纯只读；底层 tools/mas_error_triage.py、tools/maa_error_daily.py 均为只读）。
用法：python tools/mas_triage_mcp.py          （stdio MCP server）
      python tools/mas_triage_mcp.py --check  （自检：依赖 + 只读探测）
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mas_error_triage as tri  # noqa: E402

SERVER_NAME = "mas-triage"
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TOOLS_DIR)
_ERRDAILY = os.path.join(_TOOLS_DIR, "maa_error_daily.py")
_REPORTS = os.path.join(_ROOT, "runtime", "reports")

_SCHEMAS = [
    types.Tool(
        name="triage_error",
        description=("【只读】给 AUTO-MAS 里的某个账号做失败定因：从该账号所属脚本的 MAA 目录读 "
                     "debug/asst.log 的 TaskChainError / SubTaskError，判出「哪条任务链出错、"
                     "什么原因、要不要管、要不要补跑」。返回 kind/severity/reason/action + 原始证据行。"
                     "已知分类：recruit_confirm_failed（公招招募确认失败，常见原因招聘许可=0，severity=info，"
                     "资源类可忽略、不补跑）、fight_failed / infrast_failed / startup_failed / unknown_chain。"
                     "account 必须与 AUTO-MAS 里的用户昵称完全一致。"),
        inputSchema={"type": "object",
                     "properties": {"account": {"type": "string", "description": "AUTO-MAS 用户昵称，如 hbznn119-日"}},
                     "required": ["account"], "additionalProperties": False},
    ),
    types.Tool(
        name="error_daily",
        description=("【只读】每日错误聚合一行摘要（巡检用）。调 tools/maa_error_daily.py 对四源"
                     "（history maa_result/同名.log + MAA gui/asst 日志 + AUTO-MAS app.log + 只读 REST）"
                     "做当日聚合，生成 runtime/reports/maa_errors_<date>.{md,json}，返回 "
                     "`ERR_SUMMARY=<n> 条，Top=<类型>` + 致命/告警/可忽略与分类计数 + 报告路径。"
                     "耗时约 30-60s；max_age_s 秒内已有当日报告时直接复用（默认 600，0=强制重扫）。"
                     "数据源不可达时返回 ok=false 与 `ERR_SUMMARY=UNKNOWN:<原因>`（不抛异常，巡检 fail-closed 引用）。"
                     "日期口径：班次日 [D 05:00, D+1 05:00)，date 缺省=今天（本地 <05:00 自动取昨天，覆盖夜班尾巴）。"),
        inputSchema={"type": "object",
                     "properties": {
                         "date": {"type": "string", "description": "YYYY-MM-DD；缺省=今天（本地 <05:00 时取昨天）"},
                         "max_age_s": {"type": "integer", "description": "复用已生成报告的时限（秒），默认 600；0=强制重扫"},
                         "timeout": {"type": "integer", "description": "远端扫描超时（秒），默认 420"},
                         "write": {"type": "boolean", "description": "是否落盘报告（md+json），默认 true"},
                     },
                     "additionalProperties": False},
    ),
]


def _text(payload) -> list:
    return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=1))]


def _eff_date(date_s: str | None) -> str:
    """与 maa_error_daily.resolve_date 同规则：缺省=今天；本地 <05:00 取昨天。"""
    if date_s:
        return date_s
    now = dt.datetime.now()
    d = now.date() if now.hour >= 5 else now.date() - dt.timedelta(days=1)
    return d.isoformat()


def _report_paths(date_iso: str) -> tuple:
    base = os.path.join(_REPORTS, "maa_errors_%s" % date_iso)
    return base + ".md", base + ".json"


def _load_report(date_iso: str) -> dict | None:
    _md, jp = _report_paths(date_iso)
    try:
        with open(jp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _payload_from_report(date_iso: str, data: dict, cached: bool, elapsed: float) -> dict:
    md, jp = _report_paths(date_iso)
    c = data.get("counts") or {}
    return {
        "ok": True,
        "cached": cached,
        "date": date_iso,
        "err_summary": "ERR_SUMMARY=%d 条，Top=%s" % (c.get("n", -1), c.get("top", "?")),
        "window": data.get("window"),
        "counts": c,
        "degraded": {"rest_ok": (data.get("meta") or {}).get("rest_ok")},
        "report": {"md": md, "json": jp, "generated_at": data.get("generated_at")},
        "elapsed_s": round(elapsed, 1),
    }


def error_daily(args: dict) -> dict:
    """error_daily 工具实现：优先复用 max_age_s 内的当日报告，否则全量重扫（只读）。"""
    date_s = str(args.get("date") or "").strip() or None
    if date_s and not re.match(r"^\d{4}-\d{2}-\d{2}$", date_s):
        return {"ok": False, "err_summary": "ERR_SUMMARY=UNKNOWN:bad date %r" % date_s, "date": date_s}
    try:
        max_age_s = int(args.get("max_age_s", 600))
    except (TypeError, ValueError):
        max_age_s = 600
    try:
        timeout = int(args.get("timeout", 420))
    except (TypeError, ValueError):
        timeout = 420
    write = bool(args.get("write", True))
    date_iso = _eff_date(date_s)

    # 1) 复用已生成的当日报告（默认 600s 内）
    _md, jp = _report_paths(date_iso)
    if max_age_s > 0 and os.path.isfile(jp):
        age = time.time() - os.path.getmtime(jp)
        if age <= max_age_s:
            data = _load_report(date_iso)
            if data:
                p = _payload_from_report(date_iso, data, cached=True, elapsed=0.0)
                p["report"]["age_s"] = round(age)
                return p

    # 2) 全量重扫（脚本内部只读；默认落盘报告）
    cmd = [sys.executable, _ERRDAILY]
    if date_s:
        cmd += ["--date", date_s]
    if not write:
        cmd += ["--no-write"]
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=timeout + 90, env=env, cwd=_ROOT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "date": date_iso, "cached": False,
                "err_summary": "ERR_SUMMARY=UNKNOWN:timeout(%ss)" % (timeout + 90),
                "elapsed_s": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "date": date_iso, "cached": False,
                "err_summary": "ERR_SUMMARY=UNKNOWN:%s: %s" % (type(e).__name__, e),
                "elapsed_s": round(time.time() - t0, 1)}
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")

    # 3) 用刚写出的报告作结构化口径；只有本轮机新写盘（mtime ≥ t0-5s）才认
    if os.path.isfile(jp) and os.path.getmtime(jp) >= t0 - 5:
        data = _load_report(date_iso)
        if data and data.get("counts"):
            p = _payload_from_report(date_iso, data, cached=False, elapsed=time.time() - t0)
            p["ok"] = r.returncode == 0
            if not p["ok"]:
                p["stdout_tail"] = out[-400:].strip()
            return p

    # 4) 降级：没有本轮机 json（数据源不可达等）⇒ 从 stdout 抓 ERR_SUMMARY 行
    line = ""
    for ln in out.splitlines():
        if ln.startswith("ERR_SUMMARY="):
            line = ln.strip()
    return {"ok": r.returncode == 0, "date": date_iso, "cached": False,
            "err_summary": line or ("ERR_SUMMARY=UNKNOWN:rc=%s %s" % (r.returncode, err.strip()[:160])),
            "elapsed_s": round(time.time() - t0, 1),
            "stdout_tail": out[-400:].strip()}


async def _main() -> None:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list() -> list:
        return _SCHEMAS

    @server.call_tool()
    async def _call(name: str, arguments: dict | None):
        args = arguments or {}
        if name == "error_daily":
            try:
                return _text(error_daily(args))
            except Exception as e:  # noqa: BLE001
                return _text({"ok": False, "err_summary": "ERR_SUMMARY=UNKNOWN:error_daily_failed %s: %s"
                              % (type(e).__name__, e)})
        if name != "triage_error":
            return _text({"error": "unknown tool: %s" % name})
        account = str(args.get("account", "")).strip()
        if not account:
            return _text({"error": "account required"})
        try:
            return _text(tri.triage(account, None))
        except SystemExit as e:  # 找不到账号
            return _text({"account": account, "kind": "not_found", "reason": str(e)})
        except Exception as e:  # noqa: BLE001
            return _text({"account": account, "kind": "triage_failed",
                          "reason": "%s: %s" % (type(e).__name__, e)})

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def _check() -> int:
    problems = []
    try:
        r = tri.post("/api/scripts/get", {"scriptId": None})
        if r.get("code") != 200:
            problems.append("REST code=%s" % r.get("code"))
        n = len(r.get("data") or {})
    except Exception as e:  # noqa: BLE001
        problems.append("REST %s" % e)
        n = 0
    ed = "missing"
    if os.path.isfile(_ERRDAILY):
        try:
            rr = subprocess.run([sys.executable, _ERRDAILY, "--selftest"],
                                capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
            if rr.returncode == 0 and b"--selftest: OK" in (rr.stdout or b""):
                ed = "selftest=OK"
            else:
                ed = "selftest=FAIL"
                problems.append("error_daily --selftest rc=%s" % rr.returncode)
        except Exception as e:  # noqa: BLE001
            ed = "run failed"
            problems.append("error_daily run: %s" % e)
    else:
        problems.append("maa_error_daily.py not found")
    line = "mas_triage_mcp --check: %s | tools=%s | REST scripts=%d | error_daily=%s" % (
        "FAIL" if problems else "OK", ",".join(t.name for t in _SCHEMAS), n, ed)
    print(line)
    for p in problems:
        print("  - " + p)
    return 2 if problems else 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(_check())
    asyncio.run(_main())
