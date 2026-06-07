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


def is_monday() -> bool:
    return datetime.now().weekday() == 0


def is_infrast_time(now: datetime | None = None, times: list[str] | None = None) -> bool:
    if times is None:
        times = ["04:00", "16:00"]
    if now is None:
        now = datetime.now()
    h, m = now.hour, now.minute
    for t in times:
        try:
            th, tm = map(int, t.split(":"))
            if h == th and m >= tm and m < tm + 15:
                return True
        except Exception:
            pass
    return False


def decide(account: dict, global_cfg: dict) -> list[str]:
    tasks: list[str] = []
    now = datetime.now()
    today_key = get_today_key()
    is_monday = now.weekday() == 0

    tasks.append("Award")

    infrast_times = global_cfg.get("infrast_times", ["04:00", "16:00"])
    if is_infrast_time(now, infrast_times):
        tasks.append("Infrast")
        tasks.append("Recruit")
        if global_cfg.get("mall_enabled", True):
            tasks.append("Mall")

    smart_annihilation = account.get("smart_annihilation", "")
    if is_monday and smart_annihilation and global_cfg.get("annihilation_enabled", True):
        if not _is_annihilation_done_this_week(account["id"]):
            tasks.append("Fight")

    stage = account.get(f"smart_{today_key}", "") or account.get("smart_stage", "")
    threshold = global_cfg.get("threshold", 80)
    has_sanity = _check_sanity_above_threshold(account["id"], threshold)
    has_expiring_med = _has_expiring_medicine(account, global_cfg)
    materials_enabled = account.get("smart_materials_enabled", True)

    material_stage = _get_material_stage(account, global_cfg) if materials_enabled else None

    if material_stage:
        if "Fight" not in tasks:
            tasks.append("Fight")
    elif has_sanity or has_expiring_med:
        if stage and "Fight" not in tasks:
            tasks.append("Fight")

    tasks.append("CloseDown")
    return tasks


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
            return False
        import json
        data = json.loads(sp.read_text(encoding="utf-8"))
        runs = data.get("runs", [])
        for r in reversed(runs):
            s = r.get("sanity")
            if s:
                cur, mx = s.get("current", 0), s.get("max", 1)
                return (cur / mx) * 100 >= threshold
    except Exception:
        pass
    return False


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
