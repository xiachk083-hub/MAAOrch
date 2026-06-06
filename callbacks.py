from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class ServiceContext:
    """Typed context passed to services instead of raw MainWindow reference.

    Callbacks cover log/save/notify/status. Data fields provide shared mutable
    state. Service references allow cross-service calls without direct coupling.
    For widget-level access (parent dialogs, tray icon, window geometry), use
    the _mw reference (kept for gradual migration)."""

    # ── Callbacks ──
    log: Callable[[str], None] = field(default=lambda _: None)
    save: Callable[[], None] = field(default=lambda: None)
    notify: Callable[[str, bool], None] = field(default=lambda _msg, _err: None)
    set_status: Callable[[str], None] = field(default=lambda _: None)
    set_theme: Callable[[str], None] = field(default=lambda _: None)
    show_dashboard: Callable[[int], None] = field(default=lambda _: None)
    inject_config: Callable[[dict, dict], None] = field(default=lambda _w, _a: None)
    launch_program: Callable[[dict], None] = field(default=lambda _: None)
    start_pipeline: Callable[[], None] = field(default=lambda: None)
    restart_api_server: Callable[[], None] = field(default=lambda: None)
    on_account_done: Callable[[str, int, list], None] = field(default=lambda _id, _rc, _tasks: None)

    # ── Shared data ──
    accounts: list[dict] = field(default_factory=list)
    warehouse: list[dict] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    groups: list[dict] = field(default_factory=list)
    emu_status: dict = field(default_factory=dict)
    proc_status: set = field(default_factory=set)
    proc_start_times: dict = field(default_factory=dict)
    running_procs: dict = field(default_factory=dict)
    cli_procs: dict = field(default_factory=dict)

    # ── Service / thread references (cross-service access) ──
    cfg: Any = None
    logs: Any = None
    update_thread: Any = None
    schedule_thread: Any = None
    api_server: Any = None
    emu_monitor: Any = None

    # ── UI escape hatch (gradual migration) ──
    _mw: Any = None
