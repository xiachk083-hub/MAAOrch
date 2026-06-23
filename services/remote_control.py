"""MAA v6 remote control protocol — getTask / reportStatus handlers.

MAA polls getTask every 1s for tasks to execute.
MAA calls reportStatus when a task completes.
"""
from __future__ import annotations
import time, uuid, json
from typing import Any

from app.service_context import ServiceContext

# ── Remote control state ──
_devices: dict[str, dict] = {}
_account_tasks: dict[str, list[dict]] = {}

# ── Remote control task types ──
# Maps smart_scheduler task names → remote control types
_SMART_TO_RC = {
    "StartUp":     "LinkStart-WakeUp",
    "Fight":       "LinkStart-Combat",
    "Infrast":     "LinkStart-Base",
    "Recruit":     "LinkStart-Recruiting",
    "Mall":        "LinkStart-Mall",
    "Award":       "LinkStart-Mission",
    "Roguelike":   "LinkStart-AutoRoguelike",
    "Reclamation": "LinkStart-Reclamation",
    "Annihilation":"LinkStart-Combat",  # MAA handles annihilation inside combat
}


def register_device(device_id: str, account_id: str, user: str = "") -> None:
    _devices[device_id] = {"account_id": account_id, "user": user,
                           "ready_tasks": [], "active_task": None,
                           "last_seen": time.time(), "connected": True}
    if account_id not in _account_tasks:
        _account_tasks[account_id] = []


def unregister_device(device_id: str) -> None:
    _devices.pop(device_id, None)


def get_device(device_id: str) -> dict | None:
    return _devices.get(device_id)


def set_ready_tasks(account_id: str, ctx: ServiceContext) -> int:
    """Generate tasks via smart scheduler and set them as ready for getTask."""
    ac = next((a for a in ctx.accounts if a.get("id") == account_id), None)
    if not ac:
        return 0
    
    # Call smart scheduler to get task list
    mode = ctx.config.get("schedule_mode", "daily")
    tasks = []
    
    if mode == "daily":
        from services.smart_scheduler import get_tasks_for_account
        smart_tasks = get_tasks_for_account(ac, ctx.config.get("smart_global", {}))
    elif mode == "roguelike":
        smart_tasks = ["StartUp", "Roguelike"]
    elif mode == "reclamation":
        smart_tasks = ["StartUp", "Reclamation"]
    else:
        smart_tasks = ["StartUp", "Fight", "Infrast", "Recruit", "Mall", "Award"]
    
    # Convert to remote control task types
    rc_tasks = []
    for t in smart_tasks:
        rc_type = _SMART_TO_RC.get(t)
        if rc_type:
            rc_tasks.append({
                "id": uuid.uuid4().hex[:32],
                "type": rc_type,
            })
    
    _account_tasks[account_id] = rc_tasks
    return len(rc_tasks)


def get_pending_tasks(account_id: str) -> list[dict]:
    return _account_tasks.pop(account_id, [])


def record_result(account_id: str, task_id: str, status: str, payload: str = "") -> dict:
    return {"account_id": account_id, "task_id": task_id,
            "status": status, "payload": payload, "ts": time.time()}


def handle_get_task(user: str, device: str) -> list[dict]:
    dev = _devices.get(device)
    if dev is None:
        _devices[device] = {"account_id": user, "user": user,
                            "ready_tasks": [], "active_task": None,
                            "last_seen": time.time(), "connected": True}
        dev = _devices[device]
    dev["last_seen"] = time.time()
    account_id = user or dev.get("account_id", "")
    dev["account_id"] = account_id
    
    if account_id in _account_tasks and _account_tasks[account_id]:
        tasks = _account_tasks[account_id]
        _account_tasks[account_id] = []
        dev["active_task"] = tasks[0].get("id") if tasks else None
        return tasks
    return []


def handle_report_status(user: str, device: str, task_id: str, status: str, payload: str) -> dict:
    dev = _devices.get(device)
    result = {"account_id": dev.get("account_id", "") if dev else user,
              "task_id": task_id, "status": status, "payload": payload, "ts": time.time()}
    if dev:
        dev["active_task"] = None
        dev["last_seen"] = time.time()
    return result


def get_connected_devices() -> list[dict]:
    now = time.time()
    result = []
    for device_id, dev in list(_devices.items()):
        if now - dev.get("last_seen", 0) > 30:
            dev["connected"] = False
        result.append({"device_id": device_id, "account_id": dev.get("account_id", ""),
                       "connected": dev.get("connected", False),
                       "last_seen": dev.get("last_seen", 0),
                       "active_task": dev.get("active_task")})
    return result
