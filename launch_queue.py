"""LaunchQueue — unified entry point for all account launches.

All launch sources (manual, schedule, sanity-drive) enqueue here.
The queue ticks every 30s, launching the highest-priority entry
whose conditions are met (emu free, not already running, sufficient sanity).

Never interrupts a running MAA — only idles wait for their turn.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal, QTimer

from callbacks import ServiceContext
from stats import RunStats


@dataclass(order=True)
class QueueEntry:
    """A launch request waiting in the queue."""
    sort_key: tuple  # (priority, not_before) — used by heapq
    account_id: str = field(compare=False)
    source: str = field(compare=False)        # "manual" | "schedule" | "sanity"
    not_before: datetime = field(compare=False)

    @staticmethod
    def make(account_id: str, source: str, priority: int = 0,
             not_before: datetime | None = None) -> "QueueEntry":
        nb = not_before or datetime.now()
        return QueueEntry(sort_key=(priority, nb), account_id=account_id,
                          source=source, not_before=nb)


class LaunchQueue(QObject):
    """Manages a priority queue of launch requests. Drives the entire account lifecycle."""

    log_msg = Signal(str)
    launched = Signal(str)         # account_id
    skipped = Signal(str, str)     # account_id, reason

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._pending: list[QueueEntry] = []
        self._active_emus: dict[str, str] = {}  # emu_idx → account_id
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._import_heapq()

    @staticmethod
    def _import_heapq():
        import heapq
        return heapq

    def start(self, interval_sec: int = 30) -> None:
        self._restore()
        self._tick_timer.start(interval_sec * 1000)
        self._tick()

    def stop(self) -> None:
        self._tick_timer.stop()

    # ── Public API ──

    def enqueue(self, account_id: str, source: str = "manual",
                priority: int = 0, not_before: datetime | None = None) -> None:
        """Add an account to the launch queue. Priority: 0=manual, 1=schedule, 2=sanity."""
        # Avoid duplicates — remove existing entry for same account
        self._pending = [e for e in self._pending if e.account_id != account_id]
        entry = QueueEntry.make(account_id, source, priority, not_before)
        heapq = self._import_heapq()
        heapq.heappush(self._pending, entry)
        ac = next((a for a in self.ctx.accounts if a.id == account_id), None)
        name = ac.get("name", account_id) if ac else account_id
        src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
        nb_str = f" → {entry.not_before.strftime('%H:%M')}" if entry.not_before > datetime.now() else ""
        self.log_msg.emit(f"[队列] {name} 入队 ({src_map.get(source, source)}){nb_str}")
        self._save_queue()

    def enqueue_batch(self, source: str = "schedule", priority: int = 1,
                      accounts: list[str] | None = None) -> None:
        """Enqueue multiple accounts at once."""
        if accounts is None:
            accounts = [a.id for a in self.ctx.accounts]
        for aid in accounts:
            self.enqueue(aid, source, priority)

    def dequeue(self, account_id: str) -> None:
        """Remove an account from the queue."""
        self._pending = [e for e in self._pending if e.account_id != account_id]
        self._save_queue()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def active_count(self) -> int:
        return len(self._active_emus)

    def is_queued(self, account_id: str) -> bool:
        return any(e.account_id == account_id for e in self._pending)

    def is_running(self, account_id: str) -> bool:
        return account_id in self._active_emus.values()

    def pending_summary(self) -> str:
        """Short text for status bar: '排队: 小号(理智), 材料号(定时)'."""
        if not self._pending:
            return ""
        parts = []
        src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
        for e in sorted(self._pending, key=lambda x: x.sort_key):
            ac = next((a for a in self.ctx.accounts if a.id == e.account_id), None)
            name = ac.get("name", e.account_id[:6]) if ac else e.account_id[:6]
            parts.append(f"{name}({src_map.get(e.source, e.source)})")
        return "排队: " + ", ".join(parts[:3])

    def get_next_for(self, account_id: str) -> str:
        """Return next launch time for an account (for dashboard display)."""
        for e in self._pending:
            if e.account_id == account_id:
                if e.not_before > datetime.now():
                    return e.not_before.strftime("%m-%d %H:%M")
                return "即将启动"
        return ""

    # ── Lifecycle hooks (called from runner signals) ──

    def on_account_finished(self, account_id: str, exit_code: int, tasks: list | None = None) -> None:
        """An account just finished — release its emulator and schedule sanity re-entry."""
        # Release emulator
        emu_idx = self._get_emu_idx(account_id)
        self._active_emus.pop(emu_idx, None)

        # Sanity-driven: re-enqueue with calculated recovery time
        ac = next((a for a in self.ctx.accounts if a.id == account_id), None)
        if ac and ac.get("sanity_driven", False):
            st = RunStats(account_id)
            s = st.get_last_sanity()
            if s:
                deficit = s["deficit"]
                mins = deficit * 6
                next_at = datetime.now() + timedelta(minutes=mins)
                self.enqueue(account_id, "sanity", priority=2, not_before=next_at)

        # Kick tick to check next in queue
        self._tick()

    # ── Internal ──

    def _tick(self) -> None:
        """Check queue and launch all eligible accounts (parallel across different emus)."""
        now = datetime.now()
        heapq = self._import_heapq()

        to_launch = []
        remaining = []

        while self._pending:
            entry = heapq.heappop(self._pending)

            # ① Already running?
            if self.is_running(entry.account_id):
                self.skipped.emit(entry.account_id, "已在运行")
                continue

            # ② Not yet time? Push back, stop checking this priority level
            if now < entry.not_before:
                remaining.append(entry)
                continue

            # ③ Emulator occupied? Keep in queue
            emu_idx = self._get_emu_idx(entry.account_id)
            if emu_idx and emu_idx in self._active_emus:
                self.skipped.emit(entry.account_id, f"模拟器占用 ({emu_idx})")
                remaining.append(entry)
                continue

            # ④ Sanity check (sanity-driven only)
            if entry.source == "sanity":
                ac = next((a for a in self.ctx.accounts if a.id == entry.account_id), None)
                if ac:
                    st = RunStats(entry.account_id)
                    s = st.get_last_sanity()
                    min_s = ac.get("min_sanity", 0)
                    if s and s.get("current", 0) < min_s:
                        self.skipped.emit(entry.account_id, "理智不足")
                        continue

            to_launch.append(entry)

        # Push back remaining entries
        for entry in remaining:
            heapq.heappush(self._pending, entry)

        # Launch all eligible (emu conflicts resolved: first marks emu busy, second skips)
        for entry in to_launch:
            emu_idx = self._get_emu_idx(entry.account_id)
            if emu_idx and emu_idx in self._active_emus:
                # Already taken by a previous launch in this batch
                heapq.heappush(self._pending, entry)
                continue
            self._active_emus[emu_idx] = entry.account_id
            if hasattr(self.ctx._mw, "runner") and self.ctx._mw.runner:
                self.ctx._mw.runner.launch_by_id(entry.account_id)
            self.launched.emit(entry.account_id)

    def _get_emu_idx(self, account_id: str) -> str:
        for a in self.ctx.accounts:
            if a["id"] == account_id:
                return a.get("emu_instance_index", "")
        return ""

    def _save_queue(self) -> None:
        """Persist queue to config.json."""
        data = []
        for e in self._pending:
            data.append({"account_id": e.account_id, "source": e.source,
                         "priority": e.sort_key[0], "not_before": e.not_before.strftime("%Y-%m-%d %H:%M:%S")})
        self.ctx.config["queue"] = data
        try: self.ctx.save()
        except: pass

    def _restore(self) -> None:
        """Restore queue from config.json on startup."""
        data = self.ctx.config.get("queue", [])
        if not data:
            return
        from datetime import datetime as dt
        heapq = self._import_heapq()
        for d in data:
            try:
                nb = dt.strptime(d["not_before"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                nb = dt.now()
            entry = QueueEntry.make(d["account_id"], d.get("source", "saved"), d.get("priority", 0), nb)
            heapq.heappush(self._pending, entry)
        self.ctx.config["queue"] = []
        self.ctx.log(f"[队列] 从历史恢复 {len(data)} 个等待项")
