"""Type-safe shared context for Web UI mode, replacing `type('MW',(),{})`."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

from services.runner import AccountRunner
from services.launch_queue import LaunchQueue
from app.service_context import ServiceContext


@dataclass
class WebContext:
    """Shared context passed to API handlers, replacing the magic `type('MW',(),{})()` object.
    
    All fields are required and typed — no more `getattr(mw, 'xx', None)`.
    New features that need access to shared state must add a field here.
    """
    runner: AccountRunner
    launch_queue: LaunchQueue
    config: dict
    accounts: list
    ctx: ServiceContext
    warehouse: list = field(default_factory=list)
    _proc_status: set = field(default_factory=set)
    _proc_start_times: dict = field(default_factory=dict)
    _notifications: list = field(default_factory=list)
    _ai_insights: list = field(default_factory=list)
    _oplog: list = field(default_factory=list)
    _res_samples: list = field(default_factory=list)
    _gantt_events: list = field(default_factory=list)
    _save_gantt: Callable[[], None] = lambda: None
    _log: Callable[[str], None] = lambda msg: None
