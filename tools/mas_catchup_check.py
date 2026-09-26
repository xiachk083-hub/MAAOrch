#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""受控补漏的【只读】判据检查器 —— 只输出候选与证据，绝不发车。

定位：Hermes 交接契约 docs/HERMES_MAS_HANDOVER.md §7 的五条准入判据，落到代码里。
它是**决策辅助**，不是执行器：本文件不含任何写端点，且 tests/test_mas_catchup_check.py
会断言这一点（防止将来被改成会发车）。

用法：
    python tools/mas_catchup_check.py                 # 今天，输出候选
    python tools/mas_catchup_check.py --json
    python tools/mas_catchup_check.py --date 2026-09-26 --grace-minutes 60

铁律：
  - 只用只读端点（/api/queue/*、/api/scripts/*、/api/history/search）；
  - 读入即丢弃 password/token/secret 类键（沿用 tools/mas_accounts.py:22-24 的约定）；
  - 任何一条准入判据无法证明 ⇒ 不产出候选（宁可漏一轮，不重复烧理智）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

SENSITIVE_KEYS = ("password", "token", "sklandtoken", "secret", "credential", "cookie")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# 只读端点白名单：本文件**只能**出现这些路径。
READ_ENDPOINTS = (
    "/api/queue/get",
    "/api/queue/time/get",
    "/api/queue/item/get",
    "/api/scripts/get",
    "/api/scripts/user/get",
    "/api/history/search",
)


def base_url() -> str:
    for key in ("MAAORCH_MAS_API", "MAAORCH_MAS_MCP_URL", "MAS_API"):
        v = os.environ.get(key)
        if v:
            return v.rstrip("/").removesuffix("/mcp")
    try:
        from services import mas_l3  # type: ignore
        return str(mas_l3.API).rstrip("/")
    except Exception:
        return "http://100.79.173.69:36163"


def _scrub(obj):
    """读入即丢弃敏感键，避免凭据进入本进程的输出/日志。"""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()
                if not any(s in str(k).lower() for s in SENSITIVE_KEYS)}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


class UpstreamError(RuntimeError):
    pass


def api(path: str, body: dict, timeout: int = 40):
    if path not in READ_ENDPOINTS:
        raise UpstreamError("非只读端点被拒绝: %s" % path)
    req = urllib.request.Request(base_url() + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise UpstreamError("%s -> HTTP %s" % (path, e.code)) from e
    except Exception as e:
        raise UpstreamError("%s -> %s" % (path, str(e)[:120])) from e
    return _scrub(json.loads(raw))


# ── 模型装配（只读） ────────────────────────────────────────────────────

def load_schedule() -> list:
    """把 (队列 → 定时项) × (队列项 → 脚本 → 账号) 摊平成 [{account, queue, time, days, enabled}]。"""
    queues = api("/api/queue/get", {}).get("index") or []
    scripts = {s.get("uid"): s for s in (api("/api/scripts/get", {}).get("index") or [])}
    rows = []
    for q in queues:
        qid = q.get("uid")
        ts = (api("/api/queue/time/get", {"queueId": qid}).get("data") or {})
        items = (api("/api/queue/item/get", {"queueId": qid}).get("data") or {})
        accounts = []
        for it in items.values():
            sid = (it.get("Info") or {}).get("ScriptId")
            if not sid or sid not in scripts:
                continue
            users = (api("/api/scripts/user/get", {"scriptId": sid, "userId": None}).get("data") or {})
            for uname, uv in users.items():
                if uname == "instances":
                    continue
                nm = ((uv or {}).get("Info") or {}).get("Name")
                if nm:
                    accounts.append(nm)
        for tv in ts.values():
            info = tv.get("Info") or {}
            for acct in accounts:
                rows.append({"account": acct, "queue": qid,
                             "time": info.get("Time"), "days": list(info.get("Days") or []),
                             "enabled": bool(info.get("Enabled"))})
    return rows


def load_today_history(day: dt.date) -> dict:
    """{账号: [{'date': 'YYYY-MM-DD HH:MM:SS', 'status': 'DONE'|...}]}"""
    d = day.isoformat()
    resp = api("/api/history/search", {"mode": "DAILY", "start_date": d, "end_date": d})
    out = {}
    for _day, per_acct in (resp.get("data") or {}).items():
        if not isinstance(per_acct, dict):
            continue
        for acct, rec in per_acct.items():
            rows = []
            for e in ((rec or {}).get("index") or []):
                rows.append({"date": e.get("date"), "status": (e.get("status") or "").upper()})
            out.setdefault(acct, []).extend(rows)
    return out


# ── 判据（纯函数，便于离线测试） ────────────────────────────────────────

def _parse(ts: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(ts, fmt)
        except Exception:
            continue
    return None


def evaluate(schedule: list, history: dict, now: dt.datetime,
             grace_minutes: int = 60, recent_minutes: int = 60) -> list:
    """五条准入判据。任一条不能证明 ⇒ eligible=False 且 reason 指明。"""
    today = now.date()
    results = []
    for row in schedule:
        acct, tstr = row.get("account"), row.get("time")
        reasons = []
        # ① 今日启用
        if not row.get("enabled"):
            reasons.append("定时项未启用")
        elif row.get("days") and WEEKDAYS[now.weekday()] not in row["days"]:
            reasons.append("今日不在该定时项的执行日")
        # ② 到点且已过 grace
        hm = None
        if tstr and ":" in str(tstr):
            h, m = str(tstr).split(":")[:2]
            hm = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
            if now < hm + dt.timedelta(minutes=grace_minutes):
                reasons.append("距原定时不足 %d 分钟" % grace_minutes)
        else:
            reasons.append("定时时间不可解析")
        recs = history.get(acct) or []
        done_today = [r for r in recs if str(r.get("date", "")).startswith(today.isoformat())
                      and r.get("status") == "DONE"]
        # ③ 今日无完成记录
        if done_today:
            reasons.append("今日已有 DONE 记录")
        # ④ 近期无活动（避免与正在进行的运行冲突）
        recent = [r for r in recs if (_parse(str(r.get("date"))) or dt.datetime.min) >= now - dt.timedelta(minutes=recent_minutes)]
        if recent:
            reasons.append("近 %d 分钟内有记录，可能仍在运行" % recent_minutes)
        results.append({
            "account": acct, "queue": row.get("queue"), "time": tstr,
            "eligible": not reasons,
            "reason": "通过全部判据" if not reasons else "；".join(reasons),
            "evidence": {"scheduled": tstr, "enabled": row.get("enabled"),
                         "done_today": len(done_today), "records_recent": len(recent),
                         "records_today": len(recs)},
        })
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="受控补漏只读判据检查器（只输出候选，不发车）")
    ap.add_argument("--date", help="检查日期 YYYY-MM-DD，缺省今天")
    ap.add_argument("--grace-minutes", type=int, default=60)
    ap.add_argument("--recent-minutes", type=int, default=60)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only-eligible", action="store_true")
    args = ap.parse_args(argv)

    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    now = dt.datetime.now()
    try:
        schedule = load_schedule()
        history = load_today_history(day)
    except UpstreamError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False) if args.json
              else "[错误] 上游不可用: %s" % e)
        return 2
    rows = evaluate(schedule, history, now, args.grace_minutes, args.recent_minutes)
    cands = [r for r in rows if r["eligible"]]
    if args.only_eligible:
        rows = cands
    if args.json:
        print(json.dumps({"ok": True, "date": day.isoformat(), "checked": len(rows),
                          "candidates": len(cands), "rows": rows}, ensure_ascii=False, indent=1))
    else:
        print("检查日期 %s ｜ 账号-定时组合 %d ｜ 补漏候选 %d（本工具只输出候选，不会发车）"
              % (day.isoformat(), len(rows), len(cands)))
        for r in rows:
            flag = "候选" if r["eligible"] else "跳过"
            print("  [%s] %s @%s  %s" % (flag, r["account"], r["time"], r["reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
