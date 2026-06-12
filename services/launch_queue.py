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
from infrastructure.logger import Logger

_QUEUE_LOG = Logger("queue")


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
        self._booting_emus: set[str] = set()  # VMs currently being started (serial launch)
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

    def stop_all(self) -> int:
        """Stop all running accounts and shut down emulators immediately. Returns count."""
        count = 0
        emus_to_stop = set()
        for aid in list(self._active_emus.values()):
            if hasattr(self.ctx._mw, "runner") and self.ctx._mw.runner:
                self.ctx._mw.runner.stop(aid)
                count += 1
            for a in self.ctx.accounts:
                if a.get("id") == aid:
                    emu = a.get("emu_instance_index", "")
                    if emu:
                        emus_to_stop.add(emu)
                    break
        self._active_emus.clear()
        # Shut down emulators
        if emus_to_stop:
            from infrastructure.task_constants import find_mumu_cli
            cli = find_mumu_cli()
            if cli:
                import subprocess as _sp
                for emu_idx in emus_to_stop:
                    _sp.Popen([cli, "control", "--vmindex", str(emu_idx), "shutdown"],
                             creationflags=_sp.CREATE_NO_WINDOW)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, self._tick)
        return count

    # ── Public API ──

    def enqueue(self, account_id: str, source: str = "manual",
                priority: int = 0, not_before: datetime | None = None,
                persist_plan: bool = False) -> None:
        """Add an account to the launch queue. Priority: 0=manual, 1=schedule, 2=sanity."""
        with self._lock:
            self._pending = [e for e in self._pending if e.account_id != account_id]
            entry = QueueEntry.make(account_id, source, priority, not_before, persist_plan)
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
            import heapq as _hq
            max_prio = max((e.sort_key[0] for e in self._pending), default=0)
            self.enqueue(account_id, "retry", priority=max_prio + 1,
                        persist_plan=ac.get("_persist_plan", False))
            self.ctx.log(f"⏱ {ac.get('name', account_id)} 超时重排，位置 #{max_prio + 2}")
            self._tick()
            return

        # ADB disconnect (exit -8): restart emulator + re-launch, no backoff
        if exit_code == -8:
            emu_idx = ac.get("emu_instance_index", "")
            if emu_idx:
                from infrastructure.task_constants import find_mumu_cli
                cli = find_mumu_cli()
                if cli:
                    self.ctx.log(f"[ADB] {ac.get('name', account_id)} 重启模拟器 #{emu_idx}")
                    import subprocess as _sp
                    _sp.Popen([cli, "control", "--vmindex", str(emu_idx), "launch"],
                             creationflags=_sp.CREATE_NO_WINDOW)
            import heapq as _hq
            max_prio = max((e.sort_key[0] for e in self._pending), default=0)
            self.enqueue(account_id, "retry", priority=max_prio + 1,
                        persist_plan=ac.get("_persist_plan", False))
            self.ctx.log(f"[ADB] {ac.get('name', account_id)} 模拟器已重启，重排")
            self._tick()
            return

        # Retry on error: exponential backoff, re-enqueue at tail
        if exit_code != 0 and exit_code not in (-9, -8) and ac:
            failures = ac.get("consecutive_failures", 0)
            # Restart emulator on repeated failures (3+)
            if failures >= 3:
                emu_idx = ac.get("emu_instance_index", "")
                if emu_idx:
                    from infrastructure.task_constants import find_mumu_cli
                    cli = find_mumu_cli()
                    if cli:
                        import subprocess as _sp
                        self.ctx.log(f"[重启] {ac.get('name', account_id)} 连续失败 {failures} 次，重启模拟器 #{emu_idx}")
                        _sp.Popen([cli, "control", "--vmindex", str(emu_idx), "shutdown"], creationflags=_sp.CREATE_NO_WINDOW)
                        _sp.Popen([cli, "control", "--vmindex", str(emu_idx), "launch"], creationflags=_sp.CREATE_NO_WINDOW)
            delay = min(300, 5 * (2 ** (failures - 1))) if failures > 0 else 5
            from datetime import datetime, timedelta
            max_prio = max((e.sort_key[0] for e in self._pending), default=0)
            self.enqueue(account_id, "retry", priority=max_prio + 1,
                        not_before=datetime.now() + timedelta(seconds=delay),
                        persist_plan=ac.get("_persist_plan", False))
            self.ctx.log(f"[重试] {ac.get('name', account_id)} {delay}s 后重试 (exp backoff {failures})")
            self._tick()
            return

        # Normal completion with persist_plan → re-enqueue immediately (daily mode only)
        if exit_code == 0 and ac.get("_persist_plan") and self.ctx.config.get("schedule_mode", "daily") == "daily":
            import heapq as _hq
            ac["smart_plan"] = ac.get("smart_plan", "")  # keep plan
            max_prio = max((e.sort_key[0] for e in self._pending), default=0)
            self.enqueue(account_id, "force", priority=max_prio + 1, persist_plan=True)
            self.ctx.log(f"[继续] {ac.get('name', account_id)} 强制任务完成，继续下一轮")
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
        _QUEUE_LOG.info(f"_tick: start pending={len(self._pending)}")
        with self._lock:
            now = datetime.now()
            heapq = self._import_heapq()

            to_launch = []
            remaining = []

            while self._pending:
                entry = heapq.heappop(self._pending)

                # ① Already running?
                if self.is_running(entry.account_id):
                    _QUEUE_LOG.debug(f"跳过 {entry.account_id}: 已在运行")
                    self.skipped.emit(entry.account_id, "已在运行")
                    continue

                # ② Not yet time? Push back, stop checking this priority level
                if now < entry.not_before:
                    _QUEUE_LOG.debug(f"延迟 {entry.account_id}: 未到时间 ({entry.not_before})")
                    remaining.append(entry)
                    continue

                # ③ Emulator occupied? Keep in queue
                emu_idx = self._get_emu_key(entry.account_id)
                if emu_idx and emu_idx in self._active_emus:
                    occupant = self._active_emus[emu_idx]
                    occupant_name = occupant[:8]
                    for a in self.ctx.accounts:
                        if a.get("id") == occupant:
                            occupant_name = a.get("name", occupant[:8])
                            break
                    _QUEUE_LOG.debug(f"跳过 {entry.account_id}: 模拟器 {emu_idx} 被 {occupant_name} 占用")
                    self.skipped.emit(entry.account_id, f"模拟器 {emu_idx} 被 {occupant_name} 占用")
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
                            _QUEUE_LOG.debug(f"跳过 {entry.account_id}: 理智不足 ({s.get('current',0)}/{s.get('max',1)})")
                            self.skipped.emit(entry.account_id, "理智不足")
                            continue

                to_launch.append(entry)

            # Push back remaining entries
            for entry in remaining:
                heapq.heappush(self._pending, entry)

            _QUEUE_LOG.info(f"_tick: pop done pending={len(self._pending)} to_launch={len(to_launch)} remaining={len(remaining)}")

            # Resource overloaded? Push back all to_launch entries, don't start anything new
            if hasattr(self.ctx, '_mw') and hasattr(self.ctx._mw, 'runner'):
                if self.ctx._mw.runner._overloaded:
                    _QUEUE_LOG.info(f"_tick: 过载保护 to_launch={len(to_launch)} pending_before={len(self._pending)}")
                    for entry in to_launch:
                        heapq.heappush(self._pending, entry)
                    _QUEUE_LOG.info(f"_tick: 过载保护已推回 pending_after={len(self._pending)}")
                    return

            # Determine which entries to launch (still under lock)
            launch_now = []
            for entry in to_launch:
                max_parallel = self.ctx.config.get("parallel_max", 1)
                if len(self._active_emus) >= max_parallel:
                    heapq.heappush(self._pending, entry)
                    continue
                emu_idx = self._get_emu_key(entry.account_id)
                if emu_idx and emu_idx in self._active_emus:
                    occupant = self._active_emus[emu_idx]
                    occupant_name = occupant[:8]
                    for a in self.ctx.accounts:
                        if a.get("id") == occupant:
                            occupant_name = a.get("name", occupant[:8])
                            break
                    _QUEUE_LOG.debug(f"跳过 {entry.account_id}: 模拟器 {emu_idx} 被 {occupant_name} 占用")
                    heapq.heappush(self._pending, entry)
                    continue
                if emu_idx and emu_idx in self._booting_emus:
                    _QUEUE_LOG.debug(f"跳过 {entry.account_id}: 模拟器 {emu_idx} 正在启动")
                    heapq.heappush(self._pending, entry)
                    continue
                self._active_emus[emu_idx] = entry.account_id
                self._booting_emus.add(emu_idx)
                launch_now.append(entry)
                # Push back remaining to_launch entries (serial launch: only one per tick)
                idx = to_launch.index(entry)
                for remaining_entry in to_launch[idx + 1:]:
                    heapq.heappush(self._pending, remaining_entry)
                break

        # Launch outside lock to avoid re-entrancy, staggered to prevent UI freeze
        from PySide6.QtCore import QTimer
        for idx, entry in enumerate(launch_now):
            if not any(a["id"] == entry.account_id for a in self.ctx.accounts):
                continue
            QTimer.singleShot(idx * 5000, lambda e=entry: self._do_launch(e))
        _QUEUE_LOG.info(f"_tick: launch_now={len(launch_now)} pending={len(self._pending)} active_emus={len(self._active_emus)}")

    def _on_launch_ready(self, emu_idx: str) -> None:
        """Called when a VM has finished booting (ADB ready + MAA launched).
        Removes from booting set and triggers next tick for serial launch."""
        self._booting_emus.discard(emu_idx)
        # Only trigger next tick if no other VM is still booting (prevents mass concurrent launch)
        if self._booting_emus:
            return
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._tick)

    def _do_launch(self, entry) -> None:
        """Launch a single queued account."""
        # Store persist_plan on account so cleanup knows to keep smart_plan
        for a in self.ctx.accounts:
            if a["id"] == entry.account_id:
                a["_persist_plan"] = entry.persist_plan
                break
        ok = False
        if hasattr(self.ctx._mw, "runner") and self.ctx._mw.runner:
            ok = self.ctx._mw.runner.launch_by_id(entry.account_id)
        if ok:
            self.launched.emit(entry.account_id)
        else:
            # Launch failed → release resources and push back to queue
            emu_idx = self._get_emu_key(entry.account_id)
            self._active_emus.pop(emu_idx, None)
            self._booting_emus.discard(emu_idx)
            heapq.heappush(self._pending, entry)
            self.ctx.log(f"[队列] {entry.account_id[:6]} 启动失败，放回队列等待重试")

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
                         "priority": e.sort_key[0], "not_before": e.not_before.strftime("%Y-%m-%d %H:%M:%S"),
                         "persist_plan": e.persist_plan})
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
            entry = QueueEntry.make(d["account_id"], d.get("source", "saved"),
                                    d.get("priority", 0), nb,
                                    persist_plan=d.get("persist_plan", False))
            heapq.heappush(self._pending, entry)
        self.ctx.config["queue"] = []
        self.ctx.log(f"[队列] 从历史恢复 {len(data)} 个等待项")
        if data:
            self._tick()
