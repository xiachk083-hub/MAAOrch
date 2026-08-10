"""Stats aggregation — rebuild daily / weekly / monthly / yearly / all-time
views from the per-account run records (models/accounts/{id}/stats.json).

Week boundaries follow the annihilation refresh: Monday 04:00 local time
(i.e. the weekly 剿灭 reset, not calendar Monday 00:00).
"""
from __future__ import annotations
import json
import re as _re
from pathlib import Path
from datetime import datetime, timedelta


_STATS_DIR = Path(__file__).parent.parent / "models" / "accounts"


def _load_runs(aid: str) -> list[dict]:
    safe = _re.sub(r'[^\w.-]', '_', aid) or "_"
    p = _STATS_DIR / safe / "stats.json"
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")).get("runs", [])
    except Exception:
        pass
    return []


def _parse_ts(ts: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


def period_range(period: str, ref: datetime | None = None) -> tuple[datetime, datetime]:
    """Return (start, end) for the period containing `ref` (default now).

    day:    [00:00, next 00:00)
    week:   [Monday 04:00, next Monday 04:00)
    month:  [1st 00:00, next month 1st 00:00)
    year:   [Jan 1 00:00, next year Jan 1 00:00)
    all:    [datetime.min, datetime.max)
    """
    ref = ref or datetime.now()
    if period == "day":
        start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if period == "week":
        # Monday 04:00 anchor
        days_since_monday = (ref.weekday()) % 7
        monday = ref.replace(hour=4, minute=0, second=0, microsecond=0) - timedelta(days=days_since_monday)
        if ref < monday:
            monday -= timedelta(days=7)
        return monday, monday + timedelta(days=7)
    if period == "month":
        start = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ny = start.year + (1 if start.month == 12 else 0)
        nm = 1 if start.month == 12 else start.month + 1
        return start, datetime(ny, nm, 1)
    if period == "year":
        start = ref.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, datetime(start.year + 1, 1, 1)
    return datetime.min, datetime.max


def _trend_key(dt: datetime, period: str) -> str:
    if period in ("week", "month", "all"):
        return dt.strftime("%m-%d")
    if period == "year":
        return dt.strftime("%m月")
    return dt.strftime("%H:00")


def aggregate(period: str, ref: str = "", accounts: list[dict] | None = None) -> dict:
    """Aggregate stats for the given period.

    period:   day | week | month | year | all
    ref:      "YYYY-MM-DD" or "YYYY-MM" or "YYYY" (default: now)
    accounts: optional [{id, name}] — fills account names in the output.
    """
    ref_dt = datetime.now()
    if ref:
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                ref_dt = datetime.strptime(ref, fmt)
                break
            except ValueError:
                continue
    start, end = period_range(period, ref_dt)

    summary = {"runs": 0, "accounts": 0, "success_runs": 0, "fail_runs": 0,
               "total_elapsed": 0, "total_drops": 0, "sanity_consumed": 0,
               "annihilation_runs": 0}
    accounts_out: list[dict] = []
    drops_top: dict[str, int] = {}
    task_stats: dict[str, dict] = {}
    trend: dict[str, dict] = {}

    name_map = {a.get("id", ""): a.get("name", "") for a in (accounts or [])}

    ids = sorted(name_map) if name_map else sorted(
        d.name for d in _STATS_DIR.iterdir() if d.is_dir())
    for aid in ids:
        runs = _load_runs(aid)
        if not runs:
            continue
        acct = {"id": aid, "name": name_map.get(aid, aid), "runs": 0, "success": 0,
                "fail": 0, "success_rate": 0.0, "elapsed": 0, "drops": {},
                "sanity": 0, "anni_runs": 0, "tasks": {}}
        for r in runs:
            dt = _parse_ts(r.get("ts", ""))
            if not dt or not (start <= dt < end):
                continue
            acct["runs"] += 1
            summary["runs"] += 1
            ok = r.get("exit_code") == 0 or r.get("status") == "完成"
            if ok:
                acct["success"] += 1
                summary["success_runs"] += 1
            else:
                acct["fail"] += 1
                summary["fail_runs"] += 1
            el = r.get("elapsed", 0) or 0
            acct["elapsed"] += el
            summary["total_elapsed"] += el
            drops = r.get("drops") or {}
            for item, qty in drops.items():
                acct["drops"][item] = acct["drops"].get(item, 0) + qty
                drops_top[item] = drops_top.get(item, 0) + qty
            # sanity consumed: before - after (or deficit when only after known)
            sb = r.get("sanity_before") or {}
            sa = r.get("sanity_after") or r.get("sanity") or {}
            if sb.get("current") is not None and sa.get("current") is not None:
                cost = max(0, int(sb["current"]) - int(sa["current"]))
            elif sa.get("deficit") is not None:
                cost = max(0, int(sa["deficit"]))
            else:
                cost = 0
            acct["sanity"] += cost
            summary["sanity_consumed"] += cost
            stages = r.get("stages") or []
            if any("Annihilation" in str(s) for s in stages):
                acct["anni_runs"] += 1
                summary["annihilation_runs"] += 1
            for tname, status in (r.get("tasks") or {}).items():
                ts_ = task_stats.setdefault(tname, {"done": 0, "fail": 0})
                acct["tasks"].setdefault(tname, {"done": 0, "fail": 0})
                if status == "完成":
                    ts_["done"] += 1
                    acct["tasks"][tname]["done"] += 1
                elif status in ("失败", "超时"):
                    ts_["fail"] += 1
                    acct["tasks"][tname]["fail"] += 1
            tk = _trend_key(dt, period)
            tr = trend.setdefault(tk, {"runs": 0, "drops": 0})
            tr["runs"] += 1
            tr["drops"] += sum(drops.values())
        if acct["runs"]:
            acct["success_rate"] = round(acct["success"] / acct["runs"], 3)
            summary["accounts"] += 1
            accounts_out.append(acct)

    accounts_out.sort(key=lambda a: (-a["runs"], a["name"]))
    summary["success_rate"] = round(
        summary["success_runs"] / summary["runs"], 3) if summary["runs"] else 0.0
    summary["total_drops"] = sum(drops_top.values())

    drops_list = [{"item": k, "count": v} for k, v in
                  sorted(drops_top.items(), key=lambda kv: -kv[1])[:15]]
    task_list = [{"task": k, **v} for k, v in task_stats.items()]

    trend_list = [{"key": k, **v} for k, v in sorted(trend.items())]
    return {
        "ok": True,
        "period": period,
        "ref": ref or ref_dt.strftime("%Y-%m-%d"),
        "range": {"start": start.strftime("%Y-%m-%d %H:%M:%S"),
                  "end": end.strftime("%Y-%m-%d %H:%M:%S")},
        "summary": summary,
        "accounts": accounts_out,
        "drops_top": drops_list,
        "task_stats": task_list,
        "trend": trend_list,
    }


def available_periods(accounts: list[dict] | None = None) -> dict:
    """List periods that actually contain run data (for UI pickers)."""
    days: set[str] = set()
    ids = [a.get("id", "") for a in (accounts or [])] or [
        d.name for d in _STATS_DIR.iterdir() if d.is_dir()]
    for aid in ids:
        for r in _load_runs(aid):
            dt = _parse_ts(r.get("ts", ""))
            if dt:
                days.add(dt.strftime("%Y-%m-%d"))
    weeks: set[str] = set()
    months: set[str] = set()
    years: set[str] = set()
    for d in sorted(days):
        dt = _parse_ts(d)
        if dt:
            s, _ = period_range("week", dt)
            weeks.add(s.strftime("%Y-%m-%d"))
            months.add(dt.strftime("%Y-%m"))
            years.add(dt.strftime("%Y"))
    return {"ok": True,
            "days": sorted(days),
            "weeks": sorted(weeks),
            "months": sorted(months),
            "years": sorted(years)}
