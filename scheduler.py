"""Scheduler engine — sanity-driven auto-launch for accounts."""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any

from PySide6.QtCore import QObject, Signal, QTimer

from callbacks import ServiceContext
from stats import RunStats


class SchedulerEngine(QObject):
    """Checks accounts periodically and auto-launches when sanity is restored.

    Uses RunStats to determine next launch time from last run's sanity data."""

    schedule_ready = Signal(str)  # account_id when ready to launch
    schedule_queued = Signal(str, str)  # account_id, reason (e.g. "模拟器占用")

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._slots: dict[str, dict] = {}  # account_id → {next_at, emu_idx, status}
        self._last_launched: dict[str, datetime] = {}

    def start(self, interval_sec: int = 30) -> None:
        self._tick_timer.start(interval_sec * 1000)
        self._tick()  # immediate first check

    def stop(self) -> None:
        self._tick_timer.stop()

    def on_account_finished(self, account_id: str) -> None:
        """Called when an account completes a run — recalculate next launch time."""
        st = RunStats(account_id)
        s = st.get_last_sanity()
        if s:
            deficit = s["deficit"]
            mins = deficit * 6
            next_at = datetime.now() + timedelta(minutes=mins)
            self._slots[account_id] = {
                "next_at": next_at,
                "emu_idx": self._get_emu_idx(account_id),
                "status": "idle",
                "sanity": s,
            }
            self.ctx.log(f"[调度] {self._get_name(account_id)} → 下次启动 {next_at.strftime('%m-%d %H:%M')}")
        else:
            self._slots.pop(account_id, None)

    def get_next_launch(self, account_id: str) -> datetime | None:
        slot = self._slots.get(account_id)
        return slot["next_at"] if slot else None

    # ── Internal ──

    def _tick(self) -> None:
        now = datetime.now()
        for aid, slot in list(self._slots.items()):
            if slot["status"] != "idle":
                continue
            if now < slot["next_at"]:
                continue
            ac = next((a for a in self.ctx.accounts if a["id"] == aid), None)
            if not ac:
                continue
            # Check if sanity-driven is enabled
            if not ac.get("sanity_driven", False):
                continue
            # Check minimum sanity
            min_s = ac.get("min_sanity", 0)
            s = slot.get("sanity", {})
            if s.get("current", 0) < min_s:
                continue
            # Check if already running
            if hasattr(self.ctx._mw, "runner") and self.ctx._mw.runner:
                if self.ctx._mw.runner.is_running(aid):
                    continue
            # Check emulator is free
            emu_idx = slot.get("emu_idx", "")
            if emu_idx:
                busy = any(
                    s2.get("emu_idx") == emu_idx and s2.get("status") == "busy"
                    for s2 in self._slots.values()
                )
                if busy:
                    slot["status"] = "queued"
                    self.schedule_queued.emit(aid, "模拟器占用")
                    continue
            # Launch
            slot["status"] = "busy"
            self._last_launched[aid] = now
            if hasattr(self.ctx._mw, "runner") and self.ctx._mw.runner:
                self.ctx._mw.runner.launch_by_id(aid)
            self.schedule_ready.emit(aid)

    def _get_emu_idx(self, account_id: str) -> str:
        for a in self.ctx.accounts:
            if a["id"] == account_id:
                return a.get("emu_instance_index", "")
        return ""

    def _get_name(self, account_id: str) -> str:
        for a in self.ctx.accounts:
            if a["id"] == account_id:
                return a.get("name", account_id)
        return account_id
