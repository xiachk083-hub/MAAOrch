"""LaunchQueue — unified entry point for all account launches.

All launch sources (manual, schedule, sanity-drive) enqueue here.
The queue ticks every 30s, launching the highest-priority entry
whose conditions are met (emu free, not already running, sufficient sanity).

Never interrupts a running MAA — only idles wait for their turn.
"""
from __future__ import annotations
import heapq
import time
from datetime import datetime, timedelta
from pathlib import Path
import threading

from collections.abc import Callable

from app.service_context import ServiceContext
from models.stats import RunStats
from models.queue_entry import QueueEntry
from infrastructure.logger import Logger

_QUEUE_LOG = Logger("queue")


class LaunchQueue:
    """Manages a priority queue of launch requests. Drives the entire account lifecycle."""

    def __init__(self, ctx: ServiceContext) -> None:
        # Callback lists (replaces Qt Signals)
        self._log_msg_callbacks: list[Callable[[str], None]] = []
        self._launched_callbacks: list[Callable[[str], None]] = []
        self._skipped_callbacks: list[Callable[[str, str], None]] = []
        self.ctx = ctx
        self._lock = threading.RLock()
        self._pending: list[QueueEntry] = []
        self._active_emus: dict[str, str] = {}  # emu_idx → account_id
        self._active_emus_ts: dict[str, float] = {}  # emu_idx → when added
        self._paused = True  # queue starts paused; user must explicitly start
        self._last_launch_time: float = 0  # timestamp of last successful launch (60s interval)
        self._bg_tick_started = False
        self._import_heapq()

    @staticmethod
    def _import_heapq():
        return heapq

    def start(self, interval_sec: int = 5) -> None:
        # Clear stale state from previous process
        self._active_emus.clear()
        self._active_emus_ts.clear()
        self._last_launch_time = 0
        # Guard against duplicate _bg_tick threads
        if self._bg_tick_started:
            return
        self._bg_tick_started = True
        import threading as _th
        def _bg_tick():
            while True:
                import time as _t
                _t.sleep(interval_sec)
                try:
                    self._clean_stale_emus()
                    self._tick()
                except Exception as ex:
                    _QUEUE_LOG.error(f"_bg_tick 异常: {ex}")
        _th.Thread(target=_bg_tick, daemon=True, name="queue_bg_tick").start()

    def pause(self) -> None:
        """Pause queue processing. Pending items are preserved."""
        self._paused = True

    def resume(self) -> None:
        """Resume queue processing and tick immediately."""
        self._paused = False
        self._clean_stale_emus()
        self._tick()

    @property
    def is_paused(self) -> bool:
        return self._paused

    def stop(self) -> None:
        pass

    def stop_all(self) -> int:
        """Stop all running accounts, clear queue, close emulators."""
        count = 0
        # Try runner.stop for tracked accounts first
        for aid in list(self._active_emus.values()):
            if hasattr(self.ctx._mw, "runner") and self.ctx._mw.runner:
                self.ctx._mw.runner.stop(aid)
                count += 1
        self._active_emus.clear()
        self._active_emus_ts.clear()
        self._pending.clear()
        # Kill ALL MAA.exe processes
        import subprocess as _sp
        _sp.run(["wmic","process","where","name='MAA.exe'","delete"],
                capture_output=True, timeout=15, creationflags=_sp.CREATE_NO_WINDOW)
        _sp.run(["taskkill","/F","/IM","MAA.exe","/T"],
                capture_output=True, timeout=10, creationflags=_sp.CREATE_NO_WINDOW)
        # Close emulators via mumu-cli
        _sp.run([r"E:\MuMu Player 12\nx_main\mumu-cli.exe", "control", "--vmindex", "all", "shutdown"],
                capture_output=True, timeout=30, creationflags=_sp.CREATE_NO_WINDOW)
        try:
            # Fallback: iterate accounts individually
            for a in self.ctx.accounts:
                emu = a.get("emu_instance_index", "")
                if emu:
                    _sp.run([r"E:\MuMu Player 12\nx_main\mumu-cli.exe", "control", "--vmindex", str(emu), "shutdown"],
                           capture_output=True, timeout=10, creationflags=_sp.CREATE_NO_WINDOW)
        except: pass
        # Close popups
        # Close emulators via mumu-cli
        from infrastructure.task_constants import find_mumu_cli
        cli = find_mumu_cli()
        if cli:
            for a in self.ctx.accounts:
                emu = a.get("emu_instance_index", "")
                if emu:
                    try:
                        _sp.run([cli, "control", "--vmindex", str(emu), "shutdown"],
                               capture_output=True, timeout=10, creationflags=_sp.CREATE_NO_WINDOW)
                    except: pass
        # Close popups
        try:
            from services.runner import _close_mumu_popups
            _close_mumu_popups()
        except: pass
        # Clean up .pid/.meta files
        for _inst in Path(__file__).parent.glob("maa/instances/*/"):
            (_inst / ".pid").unlink(missing_ok=True)
            (_inst / ".meta").unlink(missing_ok=True)
        return count

    # ── Public API ──

    def enqueue(self, account_id: str, source: str = "manual",
                priority: int = 0, not_before: datetime | None = None,
                persist_plan: bool = False, slot: str = "") -> None:
        """Add an account to the launch queue. slot=maintenance/fight/annihilation."""
        # Reject if this account already has a running MAA process
        if self._has_running_process(account_id):
            ac = next((a for a in self.ctx.accounts if a.get("id") == account_id), None)
            name = ac.get("name", account_id) if ac else account_id
            self.ctx.log(f"[队列] {name} 已在运行中，跳过入队")
            return
        with self._lock:
            # Remove only same-slot pending entries (allow different slots)
            self._pending = [e for e in self._pending if not (e.account_id == account_id and e.slot == slot)]
            entry = QueueEntry.make(account_id, source, priority, not_before, persist_plan, slot)
            heapq = self._import_heapq()
            heapq.heappush(self._pending, entry)
            self._save_queue()
        ac = next((a for a in self.ctx.accounts if a.get("id") == account_id), None)
        name = ac.get("name", account_id) if ac else account_id
        src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
        nb_str = f" → {entry.not_before.strftime('%H:%M')}" if entry.not_before > datetime.now() else ""
        self.emit_log_msg(f"[队列] {name} 入队 ({src_map.get(source, source)}){nb_str}")

    def enqueue_batch(self, source: str = "schedule", priority: int = 1,
                      accounts: list[str] | None = None) -> None:
        """Enqueue multiple accounts at once."""
        if accounts is None:
            accounts = [a.get("id") for a in self.ctx.accounts if a.get("id")]
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

    def is_queued(self, account_id: str, slot: str = "") -> bool:
        if slot:
            return any(e.account_id == account_id and e.slot == slot for e in self._pending)
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
            ac = next((a for a in self.ctx.accounts if a.get("id") == e.account_id), None)
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
            old = self._active_emus.pop(emu_idx, None)
            self._active_emus_ts.pop(emu_idx, None)
            if old != account_id:
                return  # already processed (guard against double-trigger)

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

    def on_maa_task_finished(self, account_id: str, status: str) -> None:
        """Called when MAA reports task completion via reportStatus.
        Does NOT release the slot — that happens when MAA.exe exits (on_account_finished)."""
        ac = next((a for a in self.ctx.accounts if a["id"] == account_id), None)
        if not ac:
            return
        if status == "FAILED":
            failures = ac.get("consecutive_failures", 0)
            ac["consecutive_failures"] = failures + 1
        else:
            ac["consecutive_failures"] = 0
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
        try:
            r = getattr(self.ctx, '_mw', None)
            if r: r.runner.check_processes()
        except: pass
        _QUEUE_LOG.info(f"_tick: start pending={len(self._pending)} paused={self._paused} overloaded={getattr(getattr(getattr(self.ctx,'_mw',None),'runner',None),'_overloaded',None)}")
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
                    self.emit_skipped(entry.account_id, "已在运行")
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
                    self.emit_skipped(entry.account_id, f"模拟器 {emu_idx} 被 {occupant_name} 占用")
                    remaining.append(entry)
                    continue

                # ④ Sanity check (sanity-driven only)
                if entry.source == "sanity":
                    ac = next((a for a in self.ctx.accounts if a.get("id") == entry.account_id), None)
                    if ac:
                        st = RunStats(entry.account_id)
                        s = st.get_last_sanity()
                        min_s = ac.get("min_sanity", 0)
                        if s and s.get("current", 0) < min_s:
                            _QUEUE_LOG.debug(f"跳过 {entry.account_id}: 理智不足 ({s.get('current',0)}/{s.get('max',1)})")
                            self.emit_skipped(entry.account_id, "理智不足")
                            continue

                # ④b Enough-sanity skip (all sources): if the account's sanity is
                # already high enough to fully regen within `fight_sanity_hours`
                # (default 12h, 10pt/h), it doesn't need a farming run today.
                # Entry gate only — once launched, farming runs to exhaustion.
                # Sanity is read from the latest archived asst.log (RunStats is
                # unreliable — save_run never fired when MAA hung in round 2).
                try:
                    _ac = next((a for a in self.ctx.accounts if a.get("id") == entry.account_id), None)
                    if _ac and not _ac.get("_connect_only"):
                        _cur, _max = self._last_archived_sanity(entry.account_id)
                        if _cur is not None and _max:
                            _hours = float(_ac.get("fight_sanity_hours", 12) or 12)
                            _enough = int(_max - _hours * 10)
                            if _cur >= _enough:
                                _QUEUE_LOG.debug(f"跳过 {entry.account_id}: 理智充足 ({_cur}/{_max} ≥ {_enough})")
                                self.emit_skipped(entry.account_id, f"理智充足 {_cur}/{_max}")
                                continue
                except Exception:
                    pass

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

            # Determine which entries to launch
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
                # Launch interval — prevent burst launches
                if time.time() - self._last_launch_time < 20:
                    _QUEUE_LOG.debug(f"跳过 {entry.account_id}: 启动间隔 (上次: {int(time.time()-self._last_launch_time)}s前)")
                    heapq.heappush(self._pending, entry)
                    continue
                self._active_emus[emu_idx] = entry.account_id
                self._active_emus_ts[emu_idx] = time.time()
                self._last_launch_time = time.time()
                launch_now.append(entry)
                # Push back remaining to_launch entries (serial launch: only one per tick)
                idx = to_launch.index(entry)
                for remaining_entry in to_launch[idx + 1:]:
                    heapq.heappush(self._pending, remaining_entry)
                break

        # Launch outside lock
        import threading as _th
        for idx, entry in enumerate(launch_now):
            if not any(a["id"] == entry.account_id for a in self.ctx.accounts):
                # Connect-only temp accounts live in ctx._mw.connect_accounts
                try:
                    conn = getattr(getattr(self.ctx, "_mw", None), "connect_accounts", None)
                    if not conn or not any(a.get("id") == entry.account_id for a in conn):
                        continue
                except Exception:
                    continue
            _th.Timer(max(0.1, idx * 20.0), lambda e=entry: self._do_launch(e)).start()
        _QUEUE_LOG.info(f"_tick: launch_now={len(launch_now)} pending={len(self._pending)} active_emus={len(self._active_emus)}")
        self._clean_stale_emus()

    def _do_launch(self, entry) -> None:
        """Launch a single queued account."""
        found = None
        for a in self.ctx.accounts:
            if a["id"] == entry.account_id:
                found = a
                break
        if found is None:
            try:
                conn = getattr(getattr(self.ctx, "_mw", None), "connect_accounts", None)
                if conn:
                    for a in conn:
                        if a.get("id") == entry.account_id:
                            found = a
                            break
            except Exception:
                pass
        if found is not None:
            found["_persist_plan"] = entry.persist_plan
            found["_slot"] = entry.slot  # pass slot to runner
        ok = False
        if hasattr(self.ctx._mw, "runner") and self.ctx._mw.runner:
            ok = self.ctx._mw.runner.launch_by_id(entry.account_id)
        if ok:
            self.emit_launched(entry.account_id)
        elif hasattr(self.ctx._mw, "runner"):
            # Runner exists but launch failed → release and push back
            emu_idx = self._get_emu_key(entry.account_id)
            with self._lock:
                self._active_emus.pop(emu_idx, None)
                self._active_emus_ts.pop(emu_idx, None)
            heapq.heappush(self._pending, entry)
            self.ctx.log(f"[队列] {entry.account_id[:6]} 启动失败，放回队列等待重试")
        else:
            # No runner context (test mode) → optimistically mark as launched
            self.emit_launched(entry.account_id)

    def _clean_stale_emus(self) -> None:
        """Release _active_emus entries whose launcher threads died without cleanup."""
        runner = getattr(getattr(self.ctx, '_mw', None), 'runner', None)
        if not runner:
            _QUEUE_LOG.warn(f"清洁跳过: runner={runner}")
            return
        now = time.time()
        for emu_idx, aid in list(self._active_emus.items()):
            ts = self._active_emus_ts.get(emu_idx, 0)
            real = runner._has_real_process(aid)
            _QUEUE_LOG.debug(f"清洁检查: {emu_idx}={aid[:8]} ts={int(now-ts)}s ago real={real}")
            if ts == 0:
                _QUEUE_LOG.warn(f"清洁跳过(ts=0): {emu_idx}")
                continue
            if now - ts > 150 and not real:
                _QUEUE_LOG.warn(f"释放残留 _active_emus[{emu_idx}]={aid[:8]} (挂起 {int(now-ts)}s)")
                with self._lock:
                    self._active_emus.pop(emu_idx, None)
                    self._active_emus_ts.pop(emu_idx, None)
                from services.dispatch_pool import remove_dispatch
                for a in self.ctx.accounts:
                    if a["id"] == aid:
                        remove_dispatch(a.get("dispatch_id", ""))
                        a["dispatch_id"] = ""
                        a["smart_plan"] = ""
                        break

    def emit_log_msg(self, msg: str) -> None:
        for cb in self._log_msg_callbacks: cb(msg)
    def emit_launched(self, aid: str) -> None:
        for cb in self._launched_callbacks: cb(aid)
    def emit_skipped(self, aid: str, reason: str) -> None:
        for cb in self._skipped_callbacks: cb(aid, reason)

    def _get_emu_key(self, account_id: str) -> str:
        """Return emu instance index, or a unique fallback for no-emu accounts."""
        for a in self.ctx.accounts:
            if a["id"] == account_id:
                idx = a.get("emu_instance_index", "")
                return idx if idx else f"__noemu_{account_id}"
        # Connect-only temp accounts live in ctx._mw.connect_accounts
        try:
            conn = getattr(getattr(self.ctx, "_mw", None), "connect_accounts", None)
            if conn:
                for a in conn:
                    if a.get("id") == account_id:
                        idx = a.get("emu_instance_index", "")
                        return idx if idx else f"__noemu_{account_id}"
        except Exception:
            pass
        return f"__unknown_{account_id}"

    def _queue_path(self) -> Path:
        return Path(__file__).parent / "queue.json"

    def _last_archived_sanity(self, account_id: str) -> tuple[int | None, int | None]:
        """Read the last recorded sanity (current, max) from the newest archived
        asst.log for this account. Returns (None, None) if no archive exists."""
        try:
            hist = Path(__file__).parent.parent / "logs" / "maa_history" / str(account_id)
            if not hist.exists():
                return None, None
            files = sorted(hist.glob("*_asst.log"), key=lambda p: p.stat().st_mtime)
            if not files:
                return None, None
            import re
            cur = mx = None
            with files[-1].open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.search(r"Current Sanity:\s*(\d+)\s*,\s*Max Sanity:\s*(\d+)", line)
                    if m:
                        cur, mx = int(m.group(1)), int(m.group(2))
            return cur, mx
        except Exception:
            return None, None

    def _has_running_process(self, account_id: str) -> bool:
        """Check if this account already has a running MAA process (via .pid/.meta)."""
        try:
            for _inst in Path(__file__).parent.glob("maa/instances/*/"):
                pf = _inst / ".pid"
                mf = _inst / ".meta"
                if pf.exists() and mf.exists():
                    meta = mf.read_text().strip()
                    if "|" in meta and meta.split("|", 1)[0] == account_id:
                        pid = int(pf.read_text().strip())
                        import subprocess as _sp
                        r = _sp.run(["tasklist","/NH","/FI",f"PID eq {pid}"],
                                   capture_output=True, text=True, timeout=3,
                                   creationflags=_sp.CREATE_NO_WINDOW)
                        if str(pid) in r.stdout:
                            return True
        except: pass
        return False

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
        known_ids = {a.get("id") for a in self.ctx.accounts if a.get("id")}
        for d in data:
            aid = d.get("account_id", "")
            if aid and known_ids and aid not in known_ids:
                continue  # skip stale entries for deleted accounts
            try:
                nb = dt.strptime(d["not_before"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                nb = dt.now()
            entry = QueueEntry.make(aid, d.get("source", "saved"),
                                    d.get("priority", 0), nb,
                                    persist_plan=d.get("persist_plan", False))
            heapq.heappush(self._pending, entry)
        self.ctx.config["queue"] = []
        self.ctx.log(f"[队列] 从历史恢复 {len(data)} 个等待项")
        if data:
            self._tick()
