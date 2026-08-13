from __future__ import annotations
import time as _time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from infrastructure.logger import Logger

_SCHED_LOG = Logger("scheduler")


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


def _get_material_stage(account: dict, global_cfg: dict) -> str | None:
    try:
        # 快照在 models/accounts/<id>/depot.json 且库存键是 named
        # （旧代码 services/accounts + items 键恒取不到 — 2026-08-13 修复）
        sp = (Path(__file__).parent.parent / "models" / "accounts"
              / account["id"] / "depot.json")
        if not sp.exists():
            return None
        depot = _cached_read_json(sp)
        if depot is None:
            return None
        items = depot.get("named", {})
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
