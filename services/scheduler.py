"""Auto-scheduler — timed dispatch and sanity recovery triggers."""
from __future__ import annotations
import time, threading, json
from datetime import datetime
from pathlib import Path
from typing import Any


def start_scheduler(ctx: Any) -> None:
    """Start the background auto-scheduler thread."""
    th = threading.Thread(target=_scheduler_loop, args=(ctx,), daemon=True, name="auto_scheduler")
    th.start()


def _scheduler_loop(ctx: Any) -> None:
    """Every 60s: check timed dispatch + sanity recovery."""
    _last_tick = 0
    _last_daily_check = ""  # today's date, reset each day
    while True:
        time.sleep(60)
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        try:
            _check_timed_dispatch(ctx, now, _last_daily_check)
            if today != _last_daily_check:
                _last_daily_check = today
        except Exception:
            pass
        try:
            _check_sanity_recovery(ctx)
        except Exception:
            pass


def _check_timed_dispatch(ctx: Any, now: datetime, last_check: str) -> None:
    """Check if it's time for a scheduled daily dispatch."""
    mw = getattr(ctx, '_mw', None)
    if not mw:
        return
    lq = getattr(mw, 'launch_queue', None)
    if not lq:
        return
    batch_time = ctx.config.get("daily_batch_time", "08:00")
    if not batch_time:
        return
    try:
        bt = datetime.strptime(batch_time, "%H:%M").time()
    except Exception:
        return
    # Only trigger within 5 minutes of batch_time
    current_minutes = now.hour * 60 + now.minute
    target_minutes = bt.hour * 60 + bt.minute
    if abs(current_minutes - target_minutes) > 5:
        return
    # Only trigger once per day (track by date)
    if last_check == now.strftime("%Y-%m-%d"):
        return
    _run_smart_all(mw, "定时")
    ctx.log(f"[调度] 定时调度触发 ({batch_time})")


def _check_sanity_recovery(ctx: Any) -> None:
    """Check if any accounts have sanity > threshold and enqueue them."""
    mw = getattr(ctx, '_mw', None)
    if not mw:
        return
    lq = getattr(mw, 'launch_queue', None)
    if not lq:
        return
    smart = ctx.config.get("smart_global", {})
    threshold_pct = smart.get("threshold", 80)
    threshold = int(210 * threshold_pct / 100) if threshold_pct > 0 else 999
    _log = ctx.log

    for a in ctx.accounts:
        aid = a.get("id", "")
        if not aid:
            continue
        if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
            continue
        if a.get("suspended", False):
            continue
        if lq.is_queued(aid) or lq.is_running(aid):
            continue
        # Read last sanity from stats.json
        stats_path = Path(__file__).parent.parent / "accounts" / aid / "stats.json"
        if not stats_path.exists():
            continue
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            runs = stats.get("runs", [])
            if not runs:
                continue
            last = runs[0]
            sanity = last.get("sanity", {})
            current = sanity.get("current", 0)
            if current >= threshold:
                from services.dispatch_pool import create_dispatch
                # Build task list
                tasks = ["StartUp", "Fight", "Infrast", "Recruit", "Mall", "Award"]
                if a.get("smart_annihilation", ""):
                    tasks.append("Annihilation")
                a["dispatch_id"] = create_dispatch(tasks)
                lq.enqueue(aid, "sanity", priority=2)
                _log(f"[调度] {a.get('name', aid)} 理智恢复 ({current}/{threshold})，已入队")
        except Exception:
            continue


def _run_smart_all(mw: Any, source: str) -> None:
    """Execute a smart_all dispatch programmatically."""
    lq = getattr(mw, 'launch_queue', None)
    if not lq:
        return
    from services.dispatch_pool import create_dispatch
    tasks = ["StartUp", "Fight", "Infrast", "Recruit", "Mall", "Award"]
    count = 0
    for a in mw.accounts:
        aid = a.get("id", "")
        if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
            continue
        if a.get("suspended", False):
            continue
        if lq.is_queued(aid) or lq.is_running(aid):
            continue
        _tasks = list(tasks)
        if a.get("smart_annihilation", ""):
            if "Annihilation" not in _tasks:
                _tasks.append("Annihilation")
        elif "Annihilation" in _tasks:
            _tasks.remove("Annihilation")
        a["dispatch_id"] = create_dispatch(_tasks)
        lq.enqueue(aid, source, priority=1)
        count += 1
    if count:
        lq.tick()
