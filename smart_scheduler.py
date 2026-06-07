from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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
    "龙门币": {"stage": "CE-6", "fallback": "CE-5"},
    "作战记录":{"stage": "LS-6", "fallback": "LS-5"},
}


def get_today_key() -> str:
    return ["mon","tue","wed","thu","fri","sat","sun"][datetime.now().weekday()]


_infrast_fired: set = set()  # tracks "YYYY-MM-DD/04:00" fired per day


def is_infrast_time(now: datetime | None = None, times: list[str] | None = None) -> bool:
    if times is None:
        times = ["04:00", "16:00"]
    if now is None:
        now = datetime.now()
    h, m = now.hour, now.minute
    date_key = now.strftime("%Y-%m-%d")
    for t in times:
        try:
            th, tm = map(int, t.split(":"))
            # Fire within 15-minute window
            if h == th and m >= tm and m < tm + 15:
                _infrast_fired.add(f"{date_key}/{t}")
                return True
            # Catch-up: past the time but not yet fired today
            run_key = f"{date_key}/{t}"
            if run_key not in _infrast_fired:
                target = now.replace(hour=th, minute=tm, second=0, microsecond=0)
                if now > target and (now - target).total_seconds() < 7200:
                    _infrast_fired.add(run_key)
                    return True
        except Exception:
            pass
    return False


def decide(account: dict, global_cfg: dict) -> list[str]:
    tasks: list[str] = []
    now = datetime.now()
    today_key = get_today_key()
    is_monday = now.weekday() == 0

    tasks.append("StartUp")
    tasks.append("Award")

    infrast_times = global_cfg.get("infrast_times", ["04:00", "16:00"])
    is_afternoon = now.hour >= 15
    if is_infrast_time(now, infrast_times):
        if not is_afternoon:
            tasks.append("Depot")
        tasks.append("Infrast")
        if global_cfg.get("recruit_enabled", True):
            tasks.append("Recruit")
        if is_afternoon and global_cfg.get("mall_enabled", True):
            tasks.append("Mall")

    smart_annihilation = account.get("smart_annihilation", "")
    has_annihilation = False
    if is_monday and smart_annihilation and global_cfg.get("annihilation_enabled", True):
        if not _is_annihilation_done_this_week(account["id"]):
            tasks.append("Annihilation")
            has_annihilation = True

    stage = account.get(f"smart_{today_key}", "") or account.get("smart_stage", "")
    threshold = global_cfg.get("threshold", 80)
    has_sanity = _check_sanity_above_threshold(account["id"], threshold)
    materials_enabled = account.get("smart_materials_enabled", True)

    material_stage = _get_material_stage(account, global_cfg) if materials_enabled else None

    want_fight = bool(material_stage) or bool(has_sanity and stage) or has_annihilation
    if want_fight and "Fight" not in tasks and "Annihilation" not in tasks:
        tasks.append("Fight")
    elif has_annihilation and "Fight" not in tasks and stage:
        tasks.append("Fight")

    tasks.append("CloseDown")
    return tasks


def mark_annihilation_done(account_id: str, tasks: list[dict]) -> None:
    """Check if annihilation was completed and mark in stats.json."""
    from pathlib import Path
    import json, datetime
    has_anni = any(
        t.get("name", "").lower() in ("fight", "annihilation")
        and t.get("status") == "完成"
        for t in (tasks or [])
    )
    if not has_anni:
        return
    sp = Path(__file__).parent / "accounts" / account_id / "stats.json"
    try:
        if sp.exists():
            data = json.loads(sp.read_text(encoding="utf-8"))
        else:
            data = {}
        week = datetime.datetime.now().strftime("%Y-W%W")
        data.setdefault("weekly_annihilation", {})
        if data["weekly_annihilation"].get("week") != week:
            # Check via asst.log for actual weekly total
            runs = data.get("runs", [])
            week_total = 0
            for r in runs:
                if r.get("ts", "").startswith(datetime.datetime.now().strftime("%Y")):
                    for tn, ts in r.get("tasks", {}).items():
                        if tn.lower() in ("fight", "annihilation") and ts == "完成":
                            week_total += 1
            if week_total >= 1:
                data["weekly_annihilation"] = {"week": week, "done": True}
                sp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _is_annihilation_done_this_week(account_id: str) -> bool:
    try:
        sp = Path(__file__).parent / "accounts" / account_id / "stats.json"
        if not sp.exists():
            return False
        import json
        data = json.loads(sp.read_text(encoding="utf-8"))
        weekly = data.get("weekly_annihilation", {})
        week = datetime.now().strftime("%Y-W%W")
        return weekly.get("week") == week and weekly.get("done", False)
    except Exception:
        return False


def _check_sanity_above_threshold(account_id: str, threshold: int) -> bool:
    try:
        sp = Path(__file__).parent / "accounts" / account_id / "stats.json"
        if not sp.exists():
            return True  # no data yet → assume sufficient, will collect on first run
        import json
        data = json.loads(sp.read_text(encoding="utf-8"))
        runs = data.get("runs", [])
        for r in reversed(runs):
            s = r.get("sanity")
            if s:
                cur, mx = s.get("current", 0), s.get("max", 1)
                if (cur / mx) * 100 >= threshold:
                    return True
                # Data older than 30 min → stamina likely recovered
                ts = r.get("ts", "")
                if ts:
                    from datetime import datetime
                    try:
                        rtime = datetime.fromisoformat(ts) if "T" in str(ts) else datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - rtime).total_seconds() > 1800:
                            return True
                    except Exception:
                        pass
                break  # recent low sanity, keep checking older records
    except Exception:
        pass
    return True  # on error, launch anyway to collect fresh data


def _has_expiring_medicine(account: dict, global_cfg: dict) -> bool:
    if not global_cfg.get("expiring_medicine", True):
        return False
    return True


def _get_material_stage(account: dict, global_cfg: dict) -> str | None:
    try:
        sp = Path(__file__).parent / "accounts" / account["id"] / "depot.json"
        if not sp.exists():
            return None
        import json
        depot = json.loads(sp.read_text(encoding="utf-8"))
        items = depot.get("items", {})
        materials = global_cfg.get("materials", [])
        sorted_mats = sorted(materials, key=lambda m: m.get("priority", 99))
        for mat in sorted_mats:
            if not mat.get("enabled", False):
                continue
            name = mat["name"]
            cur = items.get(name, 0)
            if cur < mat.get("min", 0):
                ms = MATERIAL_STAGES.get(name)
                if ms:
                    return ms["stage"]
        return None
    except Exception:
        return None


def get_tasks_for_account(account: dict, global_cfg: dict) -> list[str]:
    return decide(account, global_cfg)


def get_material_stage(account: dict, global_cfg: dict) -> str | None:
    return _get_material_stage(account, global_cfg)
