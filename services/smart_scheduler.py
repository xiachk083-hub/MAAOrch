from __future__ import annotations
import time as _time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _arknights_now() -> datetime:
    return datetime.now() - timedelta(hours=4)

_io_cache: dict[str, tuple[float, Any]] = {}
_io_cache_lock = threading.Lock()
_IO_CACHE_TTL = 30


def _cached_read_json(path: Path) -> Any:
    key = str(path.resolve())
    now = _time.time()
    with _io_cache_lock:
        entry = _io_cache.get(key)
        if entry and now - entry[0] < _IO_CACHE_TTL:
            return entry[1]
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        with _io_cache_lock:
            _io_cache[key] = (now, data)
        return data
    except Exception:
        return None

MATERIAL_STAGES: dict[str, dict[str, str]] = {
    "固源岩": {"stage": "1-7", "fallback": "1-7"},
    "装置":   {"stage": "S3-4", "fallback": "1-7"},
    "聚酸酯": {"stage": "S3-3", "fallback": "1-7"},
    "糖":     {"stage": "S3-1", "fallback": "1-7"},
    "异铁":   {"stage": "S3-2", "fallback": "1-7"},
    "酮凝集": {"stage": "S3-5", "fallback": "1-7"},
    "醇":     {"stage": "4-4",  "fallback": "1-7"},
    "扭转醇": {"stage": "7-9",  "fallback": "1-7"},
    "轻锰矿": {"stage": "7-13", "fallback": "1-7"},
    "研磨石": {"stage": "7-12", "fallback": "1-7"},
    "RMA70":  {"stage": "7-10", "fallback": "1-7"},
    "固源岩组":{"stage": "2-4",  "fallback": "1-7"},
    "龙门币": {"stage": "CE-6", "fallback": "CE-5", "days": ["wed","sat","sun"], "fallback_days": ["mon","tue","thu","fri"]},
    "作战记录":{"stage": "LS-6", "fallback": "LS-5", "days": ["mon","thu","fri","sun"], "fallback_days": ["tue","wed","sat"]},
}


def get_today_key() -> str:
    return ["mon","tue","wed","thu","fri","sat","sun"][_arknights_now().weekday()]


# Per-task fire tracking {"recruit": {"2026-06-10/04:00", ...}}
_fired: dict[str, set] = {}


def _is_task_due(task_key: str, schedule_times: list[str]) -> bool:
    """Check if a task should run at this time window (15-min window + 2h catch-up, once per window per day)."""
    now = datetime.now()
    date_key = now.strftime("%Y-%m-%d")
    h, m = now.hour, now.minute
    fired_set = _fired.setdefault(task_key, set())
    for t in schedule_times:
        try:
            th, tm = map(int, t.split(":"))
            # 15-minute window
            if h == th and m >= tm and m < tm + 15:
                fk = f"{date_key}/{t}"
                if fk not in fired_set:
                    fired_set.add(fk)
                    return True
            # Catch-up within 2h after the window
            rk = f"{date_key}/{t}"
            if rk not in fired_set:
                target = now.replace(hour=th, minute=tm, second=0, microsecond=0)
                if now > target and (now - target).total_seconds() < 7200:
                    fired_set.add(rk)
                    return True
        except Exception:
            pass
    return False


def decide(account: dict, global_cfg: dict) -> list[str]:
    tasks: list[str] = []

    tasks.append("StartUp")
    tasks.append("Award")

    # 基建换班 — 4:00 / 16:00
    infrast_times = global_cfg.get("infrast_times", ["04:00", "16:00"])
    if _is_task_due("infrast", infrast_times):
        tasks.append("Infrast")

    # 公开招募 — 4:00 daily
    if _is_task_due("recruit", ["04:00"]):
        tasks.append("Recruit")

    # 信用商店 — 4:00 daily
    if _is_task_due("mall", ["04:00"]):
        tasks.append("Mall")

    # 剿灭作战 — weekly, not yet done
    smart_annihilation = account.get("smart_annihilation", "")
    has_annihilation = False
    if (smart_annihilation
        and account.get("smart_annihilation_enabled", True)
        and not _is_annihilation_done_this_week(account["id"])):
        tasks.append("Annihilation")
        has_annihilation = True

    # 刷关作战 — stamina threshold
    pct = account.get("stamina_threshold_pct", 80)
    if _check_sanity_above_threshold(account["id"], pct):
        tasks.append("Fight")
    elif account.get("smart_materials_enabled", True):
        ms = _get_material_stage(account, global_cfg)
        if ms:
            tasks.append("Fight")

    return tasks


def mark_annihilation_done(account_id: str, tasks: list[dict]) -> None:
    import json, datetime
    has_anni = any(
        t.get("name", "").lower() in ("fight", "annihilation")
        and t.get("status") == "完成"
        for t in (tasks or [])
    )
    if not has_anni:
        return
    import re as _re
    safe_id = _re.sub(r'[^\w.\-]', '_', account_id)
    sp = Path(__file__).parent / "accounts" / safe_id / "stats.json"
    try:
        if sp.exists():
            data = json.loads(sp.read_text(encoding="utf-8"))
        else:
            data = {}
        week = datetime.datetime.now().strftime("%Y-W%W")
        data.setdefault("weekly_annihilation", {})
        if data["weekly_annihilation"].get("week") != week:
            runs = data.get("runs", [])
            week_total = 0
            for r in runs:
                if r.get("ts", "").startswith(datetime.datetime.now().strftime("%Y")):
                    for tn, ts in r.get("tasks", {}).items():
                        if tn.lower() in ("fight", "annihilation") and ts == "完成":
                            week_total += 1
            if week_total >= 1:
                data["weekly_annihilation"] = {"week": week, "done": True}
                from infrastructure.utils import atomic_write
                atomic_write(sp, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _is_annihilation_done_this_week(account_id: str) -> bool:
    try:
        sp = Path(__file__).parent / "accounts" / account_id / "stats.json"
        if not sp.exists():
            return False
        data = _cached_read_json(sp)
        if data is None:
            return False
        weekly = data.get("weekly_annihilation", {})
        week = datetime.now().strftime("%Y-W%W")
        return weekly.get("week") == week and weekly.get("done", False)
    except Exception:
        return False


def _check_sanity_above_threshold(account_id: str, threshold: int) -> bool:
    try:
        sp = Path(__file__).parent / "accounts" / account_id / "stats.json"
        if not sp.exists():
            return True
        data = _cached_read_json(sp)
        if data is None:
            return True
        runs = data.get("runs", [])
        for r in reversed(runs):
            s = r.get("sanity")
            if s:
                cur, mx = s.get("current", 0), s.get("max", 1)
                if (cur / mx) * 100 >= threshold:
                    return True
                ts = r.get("ts", "")
                if ts:
                    from datetime import datetime
                    try:
                        rtime = datetime.fromisoformat(ts) if "T" in str(ts) else datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - rtime).total_seconds() > 1800:
                            return True
                    except Exception:
                        pass
                break
    except Exception:
        pass
    return True


def _get_material_stage(account: dict, global_cfg: dict) -> str | None:
    try:
        sp = Path(__file__).parent / "accounts" / account["id"] / "depot.json"
        if not sp.exists():
            return None
        depot = _cached_read_json(sp)
        if depot is None:
            return None
        items = depot.get("items", {})
        materials = global_cfg.get("materials", [])
        sorted_mats = sorted(materials, key=lambda m: m.get("priority", 99))
        today_day = get_today_key()
        for mat in sorted_mats:
            if not mat.get("enabled", False):
                continue
            name = mat["name"]
            cur = items.get(name, 0)
            if cur < mat.get("min", 0):
                ms = MATERIAL_STAGES.get(name)
                if not ms:
                    continue
                days = ms.get("days")
                if days and today_day not in days:
                    fb_days = ms.get("fallback_days")
                    if fb_days and today_day in fb_days and ms.get("fallback"):
                        return ms["fallback"]
                    continue
                return ms["stage"]
        return None
    except Exception:
        return None


def get_tasks_for_account(account: dict, global_cfg: dict) -> list[str]:
    return decide(account, global_cfg)


def get_material_stage(account: dict, global_cfg: dict) -> str | None:
    return _get_material_stage(account, global_cfg)
