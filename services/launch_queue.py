"""LaunchQueue — unified entry point for all account launches.

All launch sources (manual, schedule, sanity-drive) enqueue here.
The queue ticks every 30s, launching the highest-priority entry
whose conditions are met (emu free, not already running, sufficient sanity).

Never interrupts a running MAA — only idles wait for their turn.
"""
from __future__ import annotations
import heapq
from datetime import datetime, timedelta
import threading

from PySide6.QtCore import QObject, Signal, QTimer

from app.service_context import ServiceContext
from models.stats import RunStats
from models.queue_entry import QueueEntry


class LaunchQueue(QObject):
    """Manages a priority queue of launch requests. Drives the entire account lifecycle."""

    log_msg = Signal(str)
    launched = Signal(str)         # account_id
    skipped = Signal(str, str)     # account_id, reason

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._lock = threading.RLock()
        self._pending: list[QueueEntry] = []
        self._active_emus: dict[str, str] = {}  # emu_idx → account_id
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._paused = True  # queue starts paused; user must explicitly start
        self._import_heapq()

    @staticmethod
    def _import_heapq():
        return heapq

    def start(self, interval_sec: int = 30) -> None:
        self._tick_timer.start(interval_sec * 1000)

    def pause(self) -> None:
        """Pause queue processing. Pending items are preserved."""
        self._paused = True

    def resume(self) -> None:
        """Resume queue processing and tick immediately."""
        self._paused = False
        # Tick after a brief delay so UI has time to settle
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, self._tick)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def stop(self) -> None:
        self._tick_timer.stop()

    # ── Public API ──

    def enqueue(self, account_id: str, source: str = "manual",
                priority: int = 0, not_before: datetime | None = None) -> None:
        """Add an account to the launch queue. Priority: 0=manual, 1=schedule, 2=sanity."""
        with self._lock:
            self._pending = [e for e in self._pending if e.account_id != account_id]
            entry = QueueEntry.make(account_id, source, priority, not_before)
            heapq = self._import_heapq()
            heapq.heappush(self._pending, entry)
            self._save_queue()
        ac = next((a for a in self.ctx.accounts if a.id == account_id), None)
        name = ac.get("name", account_id) if ac else account_id
        src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
        nb_str = f" → {entry.not_before.strftime('%H:%M')}" if entry.not_before > datetime.now() else ""
        self.log_msg.emit(f"[队列] {name} 入队 ({src_map.get(source, source)}){nb_str}")

    def enqueue_batch(self, source: str = "schedule", priority: int = 1,
                      accounts: list[str] | None = None) -> None:
        """Enqueue multiple accounts at once."""
        if accounts is None:
            accounts = [a.id for a in self.ctx.accounts]
        for aid in accounts:
            self.enqueue(aid, source, priority)

    def dequeue(self, account_id: str) -> None:
        """Remove an account from the queue."""
        with self._lock:
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

    def on_account_finished(self, data: tuple) -> None:
        """An account just finished — release emulator, enqueue based on deficit."""
        account_id, exit_code, tasks = data
        emu_idx = self._get_emu_key(account_id)
        with self._lock:
            self._active_emus.pop(emu_idx, None)

        ac = next((a for a in self.ctx.accounts if a["id"] == account_id), None)
        if not ac:
            self._tick()
            return
        
        # Timeout (exit -3): re-enqueue at tail
        if exit_code == -3:
            # Kill emulator
            if ac:
                emu_idx = ac.get("emu_instance_index", "")
                if emu_idx:
                    from infrastructure.task_constants import find_mumu_cli
                    cli = find_mumu_cli()
                    if cli:
                        try:
                            import subprocess as _sp
                            _sp.Popen([cli, "control", "--vmindex", str(emu_idx), "quit"],
                                      creationflags=_sp.CREATE_NO_WINDOW)
                        except Exception:
                            pass
            import heapq as _hq
            max_prio = max((e.sort_key[0] for e in self._pending), default=0)
            self.enqueue(account_id, "retry", priority=max_prio + 1)
            self._log(f"⏱ {ac.get('name', account_id)} 超时重排，位置 #{max_prio + 2}")
            self._tick()
            return

        # Round-robin: calculate recovery based on deficit
        deficit_cfg = self.ctx.config.get("deficit") if self.ctx.config.get("deficit") is not None else ac.get("round_robin_deficit", 0)
        if deficit_cfg >= 0:
            st = RunStats(account_id)
            s = st.get_last_sanity()
            if s:
                d = s["max"] - s["current"]  # how many points left until full
                if d <= deficit_cfg:
                    # Already close enough — launch immediately
                    self.enqueue(account_id, "sanity", priority=2, not_before=datetime.now())
                else:
                    need = d - deficit_cfg
                    mins = need * 6
                    next_at = datetime.now() + timedelta(minutes=mins)
                    self.enqueue(account_id, "sanity", priority=2, not_before=next_at)

        self._tick()

    def batch_enqueue_all(self) -> None:
        """Enqueue all accounts for the daily batch run."""
        if not self.ctx.config.get("daily_batch_time", ""):
            return
        for a in self.ctx.accounts:
            self.enqueue(a["id"], "schedule", priority=1)
        self._tick()

    # ── Internal ──

    def tick(self) -> None:
        """Public alias for _tick."""
        self._tick()

    def _tick(self) -> None:
        """Check queue and launch all eligible accounts (parallel across different emus)."""
        if self._paused:
            return
        with self._lock:
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
                emu_idx = self._get_emu_key(entry.account_id)
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

            # Determine which entries to launch (still under lock)
            launch_now = []
            for entry in to_launch:
                max_parallel = self.ctx.config.get("parallel_max", 1)
                if len(self._active_emus) >= max_parallel:
                    heapq.heappush(self._pending, entry)
                    continue
                emu_idx = self._get_emu_key(entry.account_id)
                if emu_idx and emu_idx in self._active_emus:
                    heapq.heappush(self._pending, entry)
                    continue
                self._active_emus[emu_idx] = entry.account_id
                launch_now.append(entry)

        # Launch outside lock to avoid re-entrancy, staggered to prevent UI freeze
        from PySide6.QtCore import QTimer
        for idx, entry in enumerate(launch_now):
            if not any(a["id"] == entry.account_id for a in self.ctx.accounts):
                continue
            QTimer.singleShot(idx * 5000, lambda e=entry: self._do_launch(e))

    def _do_launch(self, entry) -> None:
        """Launch a single queued account."""
        if hasattr(self.ctx._mw, "runner") and self.ctx._mw.runner:
            self.ctx._mw.runner.launch_by_id(entry.account_id)
        self.launched.emit(entry.account_id)

    def _get_emu_key(self, account_id: str) -> str:
        """Return emu instance index, or a unique fallback for no-emu accounts."""
        for a in self.ctx.accounts:
            if a["id"] == account_id:
                idx = a.get("emu_instance_index", "")
                return idx if idx else f"__noemu_{account_id}"
        return f"__unknown_{account_id}"

    def _queue_path(self) -> Path:
        return Path(__file__).parent / "queue.json"

    def _save_queue(self) -> None:
        """Persist queue to queue.json (separate from config.json for performance)."""
        data = []
        for e in self._pending:
            data.append({"account_id": e.account_id, "source": e.source,
                         "priority": e.sort_key[0], "not_before": e.not_before.strftime("%Y-%m-%d %H:%M:%S")})
        try:
            import json
            from infrastructure.utils import atomic_write
            atomic_write(self._queue_path(), json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    def _restore(self) -> None:
        """Restore queue from queue.json on startup."""
        try:
            import json
            qp = self._queue_path()
            if qp.exists():
                data = json.loads(qp.read_text(encoding="utf-8"))
            else:
                data = self.ctx.config.get("queue", [])
        except Exception:
            data = self.ctx.config.get("queue", [])
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
