"""LaunchQueue — unified entry point for all account launches.

All launch sources (manual, schedule, sanity-drive) enqueue here.
The queue ticks every 30s, launching the highest-priority entry
whose conditions are met (emu free, not already running, sufficient sanity).

Never interrupts a running MAA — only idles wait for their turn.
"""
from __future__ import annotations
import heapq
import time
import subprocess
import json
import re
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
import threading

from collections.abc import Callable

from app.service_context import ServiceContext
from models.stats import RunStats
from models.queue_entry import QueueEntry
from infrastructure.logger import Logger

_QUEUE_LOG = Logger("queue")


def _mumu_manager_cli(accounts: list | None = None) -> str | None:
    """MuMu 12 专用: 只返回 MuMuManager.exe 路径。

    绝不返回 mumu-cli.exe（MuMu 6 兼容层）— 在 MuMu 12 上它的
    `--vmindex` 单查索引错位，返回的是别的模拟器的状态/端口：
    - _clean_stale_emus 用它验证模拟器 → 错位结果 is_android_started=False
      → 误判"模拟器崩溃"→ 批量杀 MAA + 关模拟器（2026-08-10 事故）
    - _reclaim_idle_emus 枚举/关闭模拟器同样错位 → 误回收
    找不到 MuMuManager → 返回 None（调用方保守跳过，不判崩溃/不回收）。
    """
    cands = [
        r"E:\MuMu Player 12\nx_main\MuMuManager.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "MuMuPlayer-12.0" / "nx_main" / "MuMuManager.exe"),
    ]
    for c in cands:
        if Path(c).exists():
            return c
    if accounts:
        for a in accounts:
            adb = a.get("adb_path", "")
            if adb:
                cand = str(Path(adb).parent / "MuMuManager.exe")
                if Path(cand).exists():
                    return cand
    return None


# ── 模拟器关闭 — 统一入口转发到 emu_service（2026-08-11 P1 收敛）──
# 锁/冷却/优雅关闭实现已迁移到 services/emu_service.py（单点），
# 此处保留 graceful_emu_shutdown 名称兼容其他文件 import。


def graceful_emu_shutdown(cli: str, emu_idx, adb_path: str = "", addr: str = "",
                          wait: int = 90, log=None) -> bool:
    """转发到 emu_service.graceful_shutdown（统一实现，防并发二次关闭）。"""
    from services.emu_service import graceful_shutdown
    return graceful_shutdown(cli, emu_idx, adb_path, addr, wait,
                             log=log or (lambda m: _QUEUE_LOG.info(m)))


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
        self._recover_count: dict[str, int] = {}  # aid → 30min 窗口内 recover 次数
        self._recover_ts: dict[str, float] = {}  # aid → 上次 recover 时间
        self._paused = True  # queue starts paused; user must explicitly start
        self._last_launch_time: float = 0  # timestamp of last successful launch (60s interval)
        # 运行超时（任务级卡死防护）: 单次运行超过该秒数 → 判定卡死 → 重置。
        # 可配置 max_run_minutes（默认 180 分钟 — 正常一轮日常+剿灭远小于此）。
        self._max_run_sec: int = int(self.ctx.config.get("max_run_minutes", 180) or 180) * 60
        # 日志停滞检测: asst.log 超过该秒数无更新 → 判定卡死（比超时敏感）。
        # 默认 10 分钟 — 正常运行时 MAA 每 1-5s 写日志。
        self._log_stall_sec: int = int(self.ctx.config.get("log_stall_minutes", 10) or 10) * 60
        # 重试卡死: 同一任务 cur_retry 超过该值（日志活跃但无进展）→ 判定卡死。
        # 默认 60 次（约 60s+ 无进展）。
        self._retry_stall: int = int(self.ctx.config.get("retry_stall", 60) or 60)
        # 连续兜底释放计数（防无限重试循环）: 账号连续兜底释放 N 次 → 挂起。
        self._stale_release_count: dict[str, int] = {}
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
        self._tick_lock = threading.Lock()
        self._last_save_ts: float = 0.0  # 队列落盘节流
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
                    self.tick()  # 走 tick() 统一防重入锁
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
        # Close emulators — 统一优雅关闭（adb reboot -p → 等退出 → 兜底），
        # 直接 shutdown 会留 VMM 残留（用户 2026-08-10: 必须解决错误关闭）
        cli = _mumu_manager_cli(self.ctx.accounts)
        if cli:
            try:
                for a in self.ctx.accounts:
                    emu = a.get("emu_instance_index", "")
                    if emu:
                        graceful_emu_shutdown(cli, emu, a.get("adb_path", ""), a.get("adb_address", ""))
            except Exception:
                try:
                    _sp.run([cli, "control", "-v", "all", "shutdown"],
                            capture_output=True, timeout=30, creationflags=_sp.CREATE_NO_WINDOW)
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
        if exit_code == 0:
            self._stale_release_count.pop(account_id, None)
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
                cli = _mumu_manager_cli(self.ctx.accounts)
                if cli:
                    self.ctx.log(f"[ADB] {ac.get('name', account_id)} 重启模拟器 #{emu_idx}")
                    import subprocess as _sp
                    # 统一优雅关闭再重启（直接 shutdown 会留 VMM 残留）
                    _ac4 = next((a for a in self.ctx.accounts if a.get("id") == account_id), None)
                    _adb4 = _ac4.get("adb_path", "") if _ac4 else ""
                    _addr4 = _ac4.get("adb_address", "") if _ac4 else ""
                    graceful_emu_shutdown(cli, emu_idx, _adb4, _addr4)
                    _sp.Popen([cli, "control", "-v", str(emu_idx), "launch"],
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
                    cli = _mumu_manager_cli(self.ctx.accounts)
                    if cli:
                        import subprocess as _sp
                        self.ctx.log(f"[重启] {ac.get('name', account_id)} 连续失败 {failures} 次，重启模拟器 #{emu_idx}")
                        _ac5 = next((a for a in self.ctx.accounts if a.get("id") == account_id), None)
                        _adb5 = _ac5.get("adb_path", "") if _ac5 else ""
                        _addr5 = _ac5.get("adb_address", "") if _ac5 else ""
                        graceful_emu_shutdown(cli, emu_idx, _adb5, _addr5)
                        _sp.Popen([cli, "control", "-v", str(emu_idx), "launch"], creationflags=_sp.CREATE_NO_WINDOW)
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
        """Public alias for _tick — 防双 tick 竞争（2026-08-11: enqueue 手动
        tick 与 _bg_tick 并发 → 一个启动的条目被另一个判定"模拟器占用"推回
        pending → 队列原地踏步：launch_now=1 → 1s 后 launch_now=0 pending 复原）。
        非阻塞锁：已有 tick 在跑则本次跳过（bg_tick 5s 后自然再跑）。"""
        if not self._tick_lock.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            self._tick_lock.release()

    def _tick(self) -> None:
        """Check queue and launch all eligible accounts (parallel across different emus)."""
        # 暂停只停止"启动新任务"— 运行中账号的监控/完成收尾必须继续。
        # 暂停直接 return 会导致 check_processes 停摆：MAA 刷完（AllTasksCompleted）
        # 检测不到 → 不退出不释放实例/模拟器（2026-08-10 实测：队列暂停后
        # 21:16/21:19 两个账号完成但 MAA 一直挂着）。
        try:
            r = getattr(self.ctx, '_mw', None)
            if r: r.runner.check_processes()
        except: pass
        if self._paused:
            return
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

                # ①.5 Suspended accounts never launch — consume the entry
                # (removed from queue; re-enqueue manually after unsuspending).
                # Without this, a suspended account's force entry stays in the
                # queue forever and gets launched on every tick (2026-08-10:
                # 官-2/官-41 模拟器起不来 — 挂起后条目还在 pending)。
                _susp_ac = next((a for a in self.ctx.accounts if a.get("id") == entry.account_id), None)
                if _susp_ac and _susp_ac.get("suspended"):
                    _QUEUE_LOG.warn(f"移除挂起账号 {entry.account_id[:8]} 的队列条目")
                    self.emit_skipped(entry.account_id, "账号挂起")
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

                # ④b Enough-sanity skip (sanity-driven only): if the account's
                # sanity is already high enough to fully regen within
                # `fight_sanity_hours` (default 12h, 10pt/h), it doesn't need an
                # automatic recovery run today. Entry gate only — once launched,
                # farming runs to exhaustion.
                # Sanity is read from the latest archived asst.log (RunStats is
                # unreliable — save_run never fired when MAA hung in round 2).
                # NOTE: only gates source="sanity" — force/manual/auto entries
                # ALWAYS run. A full-sanity skip on a force/auto entry would
                # deadlock: skipped → no run → archive never updates → skipped
                # forever (sanity only updates on the last run's result).
                # Annihilation accounts must burn sanity even when full.
                if entry.source == "sanity":
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
        # 队列状态节流落盘（30s 一次，含 active — 崩溃/重启恢复用）
        try:
            if time.time() - self._last_save_ts > 30:
                self._last_save_ts = time.time()
                self._save_queue()
        except Exception:
            pass
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
            # 启动宽限：MAA 在 Android boot 完成后才 spawn（boot_wait 90s）—
            # 宽限期内 real=False（MAA 未 spawn）是正常等待，不是死标记。
            # 此前无宽限 → 每 5s 释放启动标记 → 队列循环（pending 永远不动，
            # 2026-08-11 实测：launch 后 1s 标记被释放，原地踏步）。
            if ts and time.time() - ts < 100:
                continue
            real = runner._has_real_process(aid)
            # `real` only checks the MAA process — an emulator that crashed
            # (VMMHeadless gone) leaves MAA alive but useless (ADB lost).
            # Also verify the emulator is actually running via MuMuManager.
            if real:
                try:
                    _ac = next((a for a in self.ctx.accounts if a.get("id") == aid), None)
                    if _ac and _ac.get("emu_instance_index"):
                        # 只用 MuMuManager.exe — find_mumu_cli 可能返回 mumu-cli
                        # （MuMu 6 兼容层）→ --vmindex 索引错位 → 误判"模拟器崩溃"
                        # → 批量杀 MAA（2026-08-10 事故根因）。
                        cli = _mumu_manager_cli(self.ctx.accounts)
                        if cli:
                            r = subprocess.run([cli, "info", "-v", str(_ac["emu_instance_index"])],
                                              capture_output=True, text=True, timeout=5,
                                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                              encoding="utf-8", errors="replace")
                            if r.returncode == 0:
                                try:
                                    _d = json.loads(r.stdout.lstrip("\ufeff").strip())
                                except Exception:
                                    # 查询结果无法解析 — 无法确认模拟器状态，
                                    # 保守跳过（宁可漏判也不误杀）。
                                    _d = None
                                if _d is not None:
                                    # 防御 MuMuManager 错误返回（errcode≠0 时
                                    # JSON 无 is_*_started 字段 → None → 误判
                                    # "崩溃" → 批量杀 MAA/关模拟器 — 2026-08-10
                                    # 实测 3 台同时"崩溃"实为查询故障）。key 必须
                                    # 是 bool 才可信，无法确认 → 保守跳过。
                                    _pa = _d.get("is_android_started")
                                    _pp = _d.get("is_process_started")
                                    if not (isinstance(_pa, bool) and isinstance(_pp, bool)):
                                        continue
                                    if _pa or _pp:
                                        continue
                                    # 启动宽限: recover 刚 launch 过（60s 内）→
                                    # 模拟器正在启动（VMM 进程 10-30s 才起来）→
                                    # 不判崩溃。否则每次 launch 后立即被判"崩溃"
                                    # → shutdown 打断启动 → 永远起不来 → 反复
                                    # shutdown/launch → MuMu 崩溃报告刷屏
                                    # （2026-08-10 用户: "崩溃日志都干出来了"）。
                                    if time.time() - self._recover_ts.get(aid, 0) < 60:
                                        continue
                                    real = False
                                    _QUEUE_LOG.warn(f"模拟器 #{_ac['emu_instance_index']} 已崩溃（{_ac.get('name','?')}）")
                                    self._recover_account(aid, emu_idx, cli, _flag,
                                                          f"模拟器崩溃（{_ac.get('name','?')}）")
                except Exception:
                    pass
            _QUEUE_LOG.debug(f"清洁检查: {emu_idx}={aid[:8]} ts={int(now-ts)}s ago real={real}")
            # ── 运行超时: MAA 活着但单次运行超时 ──
            # 任务级死循环（如 PRTS 误匹配卡死）检测不到进程/模拟器异常 —
            # 进程活着、模拟器正常、ADB 通，但任务永远不推进。超时即判定
            # 卡死 → 完整处理链（杀 MAA + 关模拟器 + 重启）→ 自动恢复。
            if real and ts > 0 and now - ts > self._max_run_sec:
                _QUEUE_LOG.warn(f"运行超时: {aid[:8]} 已运行 {int((now-ts)//60)} 分钟（判定卡死，重置）")
                self._recover_account(aid, emu_idx, self._recover_cli(),
                                      self._recover_flag(), "运行超时（任务级卡死）")
                continue
            # ── 日志停滞检测: asst.log 长时间无更新 = 卡死 ──
            # 比运行超时敏感得多: 正常运行时 MAA 每 1-5s 写日志（截图/识别），
            # 卡死（连接挂起/任务死循环）日志就停更。10 分钟无更新即判定
            # 卡死 → 立即恢复（不等 180 分钟超时）。
            if real and ts > 0:
                try:
                    _inst2 = getattr(runner, "_procs", {}).get(aid)
                    _ip2 = getattr(_inst2, "_inst_path", None)
                    if isinstance(_inst2, str):
                        _ip2 = _inst2
                    if _ip2:
                        _al = Path(_ip2) / "debug" / "asst.log"
                        if _al.exists():
                            _age = now - _al.stat().st_mtime
                            if _age > self._log_stall_sec:
                                _QUEUE_LOG.warn(f"日志停滞: {aid[:8]} asst.log {int(_age//60)} 分钟无更新（判定卡死，重置）")
                                self._recover_account(aid, emu_idx, self._recover_cli(),
                                                      self._recover_flag(), "日志停滞（卡死）")
                                continue
                            # ── 重试卡死检测: 日志活跃但同一任务无限重试 ──
                            # 如 StartUpBegin 重试 37 次（游戏没启动）、PRTS1 循环。
                            # asst.log 尾部 "cur_retry":N 持续增长 = 无进展卡死。
                            try:
                                _tail = _al.read_text(encoding="utf-8", errors="replace")[-4000:]
                                _mm = re.search(r'"cur_retry":\s*(\d+)', _tail)
                                if _mm and int(_mm.group(1)) > self._retry_stall:
                                    _QUEUE_LOG.warn(f"重试卡死: {aid[:8]} 同一任务重试 {_mm.group(1)} 次无进展（判定卡死，重置）")
                                    self._recover_account(aid, emu_idx, self._recover_cli(),
                                                          self._recover_flag(), "重试卡死（任务无进展）")
                                    continue
                            except Exception:
                                pass
                except Exception:
                    pass
            if ts == 0:
                # Timestamp missing (legacy residue or abnormal write path).
                # If the process is dead, release now — otherwise permanently
                # skipping here blocks the emulator key forever.
                if not real:
                    _QUEUE_LOG.warn(f"释放无时间戳残留 _active_emus[{emu_idx}]={aid[:8]} (进程已死)")
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
                    self._requeue_if_valid(aid, emu_idx)
                else:
                    self._active_emus_ts[emu_idx] = now  # 补时间戳，交给 150s 超时逻辑
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
                self._requeue_if_valid(aid, emu_idx)

        # ── 空闲模拟器回收 ──
        # Emulators are the biggest resource hog (RAM/CPU each). An account
        # that is neither running nor queued doesn't need its emulator up —
        # runner._launch_job_body re-launches it on demand (with a global
        # launch lock for MuMu 12 concurrency). This keeps a big account farm
        # from pinning N emulators while only a few accounts are active.
        self._reclaim_idle_emus()

    def _reclaim_idle_emus(self) -> None:
        """Shutdown emulators whose accounts are neither running nor queued."""
        try:
            import json as _json
            # 只用 MuMuManager.exe（mumu-cli 在 MuMu 12 上 --vmindex 索引错位，
            # 枚举/关闭都会作用到错误的模拟器 — 见 _mumu_manager_cli 注释）
            cli = _mumu_manager_cli(self.ctx.accounts)
            if not cli:
                return
            idx_flag = "-v"
            # Only accounts actually running keep their emulator. Queued ones
            # don't — runner._launch_job_body relaunches the emulator on demand
            # (with a global launch lock). Keeping queued emulators up used to
            # pin N emulators while the queue was long, and after a project
            # restart every previously-running emulator stayed up forever.
            active_ids = set(self._active_emus.values())
            emu2aid: dict[str, str] = {}
            for a in self.ctx.accounts:
                ei = a.get("emu_instance_index", "")
                if ei:
                    emu2aid[str(ei)] = a["id"]
            # 连接模式临时账号（内存 mw.connect_accounts，不在 ctx.accounts）—
            # 不补进来则 aid=None → 跳过下方全部保护 → 连接页手动操作的模拟器
            # 被回收误关（MAA 还活着但映射不到 aid — 用户: "MAA 还开着，
            # 模拟器自己关掉了"）。正式账号优先（setdefault 不覆盖）。
            _conn = getattr(getattr(self.ctx, "_mw", None), "connect_accounts", None) or []
            for a in _conn:
                ei = a.get("emu_instance_index", "")
                if ei:
                    emu2aid.setdefault(str(ei), a["id"])
            r = subprocess.run([cli, "info", idx_flag, "all"],
                               capture_output=True, text=True, timeout=8,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               encoding="utf-8", errors="replace")
            data = _json.loads(r.stdout.lstrip("\ufeff").strip())
            # 补系统启动标记（重启/入队后模拟器归属恢复 — 2026-08-11 堆积
            # 根因: main_web 启动时补一次，但那次重启队列为空 → 后来入队的
            # 账号对应模拟器无标记 → 回收永不关 → 8 台空闲堆积）。
            # 每次回收 tick 补: 运行中 + 账号在 active/pending → 标记。
            try:
                from services.emu_service import mark_system_started as _mss
                for _idx, _info in data.items():
                    _aid = emu2aid.get(str(_idx))
                    if _aid and (_aid in active_ids or
                                 any(e.account_id == _aid for e in self._pending)):
                        _mss(str(_idx))
            except Exception:
                pass
            for idx, info in data.items():
                if not info.get("is_process_started"):
                    continue
                aid = emu2aid.get(str(idx))
                if aid is None:
                    # 无账号映射 = 用户手动开的 / 别人的工作区（l- 系列）—
                    # 一律不回收。此前无条件回收导致: 手动开的模拟器 30 秒后
                    # 被关（2026-08-10 实测）、l- 系列（fz-maa）被误关（隔离
                    # 只在 detect_emu_instances 过滤，回收用 info all 不过滤）。
                    nm = str(info.get("name", ""))
                    if any(nm.startswith(p) for p in ("l-", "碧蓝航线")):
                        continue
                    _QUEUE_LOG.info(f"回收跳过 模拟器#{idx} (无账号映射，保留)")
                    continue
                if aid in active_ids:
                    continue  # 在跑/排队/启动中 — 保留
                # 关闭冷却（5 分钟）— 刚被优雅关闭/回收关闭过的模拟器进程可能
                # 还在退出，再次关闭=二次关闭（2026-08-11 用户观察: 正常关闭
                # 后一段时间又关一次）。
                from services.emu_service import recently_closed as _rc_fn
                if _rc_fn(idx):
                    _QUEUE_LOG.info(f"回收跳过 模拟器#{idx} (关闭冷却期内)")
                    continue
                # 手动/API 启动保护期（10 分钟）— 用户手动开的模拟器（模拟器
                # 管理页/批量启动）不立刻回收，否则手动操作被反复打断
                # （2026-08-10 实测: API 启动后 30s 被回收）。
                _mts = getattr(getattr(self.ctx, "_mw", None), "manual_emu_started", None)
                if _mts:
                    _t = _mts.get(str(idx))
                    if _t and time.time() - _t < 600:
                        _QUEUE_LOG.info(f"回收跳过 模拟器#{idx} (手动启动保护期内)")
                        continue
                # 优雅关闭进行中 → 跳过（回收的直接 shutdown 绕过 graceful 锁，
                # 会与进行中的优雅关闭并发 → 二次关闭。2026-08-11 实测:
                # 02:41:20 回收 shutdown 打断 02:40:53 的优雅关闭）。
                from services.emu_service import lock_busy as _lb_fn
                if _lb_fn(idx):
                    _QUEUE_LOG.info(f"回收跳过 模拟器#{idx} (优雅关闭进行中)")
                    continue
                # 排队中的账号保留模拟器（限时）— 降级回队列/失败重试的账号
                # 马上要重新启动，回收会导致反复开关（2026-08-10 用户: 降级后
                # 模拟器被关 → 又要重新拉起）。但长队列下"无条件保留"会让
                # 闲置模拟器全开着（每台 2-4GB，卡机 — 2026-08-10 用户: 开
                # 这么多多余的模拟器有点卡）→ 入队超过 30 分钟（一轮任务+
                # 重试周期内不回收；超 30 分钟 = 队列长期不动的遗留）才回收，
                # 轮到启动时 _launch_job_body 自动拉起。
                # ⚠️ 曾用 5 分钟 — 实测启动失败重试的账号（20s 间隔重试）
                # 模拟器被回收 → 每次重试都冷启动 → 启动风暴 → 失联/挂起
                # （2026-08-11 B 服轮: #22 被回收后 b-12/b-4 连环失败）。
                if aid and any(e.account_id == aid for e in self._pending):
                    _recent = any(
                        (datetime.now() - e.not_before).total_seconds() < 1800
                        for e in self._pending if e.account_id == aid)
                    if _recent:
                        continue
                    _QUEUE_LOG.info(f"回收跳过转回收 模拟器#{idx} (排队超 30 分钟，回收省资源)")
                # 非系统启动的模拟器 = 用户在 MuMu 管理器手动开的 → 永不回收
                # （系统只回收自己拉起的。2026-08-11 用户: 手动启动的模拟器
                # 也被关掉 — 回收把用户手动开的当闲置关了）。
                from services.emu_service import is_system_started as _iss_fn
                if not _iss_fn(idx):
                    _QUEUE_LOG.info(f"回收跳过 模拟器#{idx} (非系统启动，用户手动开的)")
                    continue
                # MAA 进程还活着（降级优雅关闭中/账号过渡期）→ 保留模拟器 —
                # 否则关掉模拟器后 MAA 失联卡住，且进程活着不触发自动重启
                # （用户: "MAA 还开着，模拟器自己关掉了，之后也没有重启"）。
                if aid:
                    try:
                        _runner = getattr(getattr(self.ctx, '_mw', None), 'runner', None)
                        if _runner and _runner._has_real_process(aid):
                            continue
                    except Exception:
                        pass
                _QUEUE_LOG.info(f"回收空闲模拟器 #{idx} (无运行任务)")
                # 闲置模拟器 = 无 MAA/无任务 = 无游戏在跑 → 直接 MuMuManager
                # shutdown 是正常关闭（用户手动关闭模拟器就是它），不会弹
                # MuMu 崩溃报告（崩溃报告只在"游戏运行中被关"时出现）。
                # 此前用 adb reboot 流程：闲置模拟器不在 adb server 设备列表
                # （adb -s 无 connect 直接失败，未检查返回值）→ 90s 超时 →
                # shutdown 兜底强关 → 反而弹"运行异常"（2026-08-10 实测
                # #13/#21/#28 100% 失败）。闲置场景直接关，不绕道。
                from services.emu_service import direct_shutdown as _ds_fn, set_direct_adb
                # 注册 ADB（force-stop 游戏用 — MAA 完成后的前台游戏强关会出错）
                try:
                    _ac3 = next((a for a in self.ctx.accounts if a.get("id") == aid), None)
                    if _ac3:
                        set_direct_adb(idx, _ac3.get("adb_path", ""), _ac3.get("adb_address", ""))
                except Exception:
                    pass
                _ds_fn(cli, idx, idx_flag, log=lambda m: _QUEUE_LOG.info(m))
        except Exception as ex:
            _QUEUE_LOG.debug(f"空闲模拟器回收跳过: {ex}")

    def _recover_cli(self) -> str:
        """Find emulator CLI — MuMuManager.exe ONLY (mumu-cli misindexes on MuMu 12)."""
        return _mumu_manager_cli(self.ctx.accounts) or ""

    def _recover_flag(self) -> str:
        return "-v"

    def _recover_account(self, aid: str, emu_idx: str, cli: str, flag: str, reason: str) -> None:
        """完整恢复链（原地处理，账号保持"运行中"占位）:
          1. 杀空转 MAA（Popen pid 或 .pid 文件）
          2. shutdown 模拟器（彻底重置）
          3. 立即重新 launch 模拟器（原地恢复 — 不等队列）
          之后 runner._wait_exit 检测 MAA 死 → cleanup → on_account_finished
          释放标记 → 自动重启（priority 0 立即启动）→ 复用已就绪的模拟器 → 无缝恢复。
        """
        # 恢复次数上限: 模拟器物理起不来（Android 损坏/游戏崩）时 recover 会
        # 无限循环（每 tick 判定崩溃 → 杀/重启 → 又判崩溃）。同一账号在
        # 30 分钟内 recover 超过阈值 → 挂起 + 告警，停止空转（2026-08-10
        # 官-41/emu56、官-19/emu33 同类 — 需人工处理模拟器）。
        try:
            _ac = next((a for a in self.ctx.accounts if a.get("id") == aid), None)
            _now = time.time()
            if _now - self._recover_ts.get(aid, 0) > 1800:
                self._recover_count[aid] = 0  # 时间窗口重置（防长期累积误挂）
            self._recover_ts[aid] = _now
            n = self._recover_count.get(aid, 0) + 1
            self._recover_count[aid] = n
            _max = int(self.ctx.config.get("max_recover_attempts", 5) or 5)
            if n >= _max:
                if _ac:
                    _ac["suspended"] = True
                    try:
                        from models.config_manager import save_config
                        save_config(self.ctx.config)
                    except Exception:
                        pass
                _QUEUE_LOG.warn(f"账号 {aid[:8]} 恢复失败 {n} 次，自动挂起（模拟器/游戏起不来，需人工处理）")
                return
        except Exception:
            pass
        runner = getattr(getattr(self.ctx, '_mw', None), 'runner', None)
        # 1. 杀 MAA
        try:
            _inst = getattr(runner, "_procs", {}).get(aid)
            _pid = getattr(_inst, "pid", None)
            if not _pid and isinstance(_inst, str):
                # 字符串占位 = 实例路径（_downgrade_stage 替换后）→ 读 .pid 文件
                try:
                    _pid = int((Path(_inst) / ".pid").read_text().strip())
                except Exception:
                    _pid = None
            if _pid:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(_pid)],
                               capture_output=True, timeout=8,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                _QUEUE_LOG.warn(f"已杀 MAA pid={_pid}（{reason}）")
        except Exception:
            pass
        # 2. shutdown 模拟器 — 统一优雅关闭（adb reboot -p → 等退出 → 兜底）
        # 直接 shutdown 会留 VMM 残留（用户 2026-08-10: 必须解决错误关闭）
        if cli and emu_idx:
            try:
                _ac3 = next((a for a in self.ctx.accounts if a.get("id") == aid), None)
                _adb3 = _ac3.get("adb_path", "") if _ac3 else ""
                _addr3 = _ac3.get("adb_address", "") if _ac3 else ""
                graceful_emu_shutdown(cli, emu_idx, _adb3, _addr3)
            except Exception:
                try:
                    subprocess.run([cli, "control", flag, str(emu_idx), "shutdown"],
                                   capture_output=True, timeout=15,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except Exception:
                    pass
                _QUEUE_LOG.warn(f"已关闭模拟器 #{emu_idx}（{reason}）")
            except Exception:
                pass
        # 3. 原地重启模拟器（保持运行标记 — 无缝恢复）
        if cli and emu_idx:
            try:
                subprocess.run([cli, "control", flag, str(emu_idx), "launch"],
                               capture_output=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                _QUEUE_LOG.warn(f"已原地重启模拟器 #{emu_idx}（{reason} — 等待自动恢复）")
            except Exception:
                pass
        # 注意: 不释放 _active_emus 标记 —— 账号保持"运行中"占位。
        # cleanup 的 on_account_finished 会在 MAA 死后自然释放，
        # 自动重启链立即接管（priority 0）。
        # 记录到 AccountState（卡死/异常处理可见）
        try:
            from models.account_state import AccountState
            AccountState(aid).on_stuck(reason)
        except Exception:
            pass

    def _requeue_if_valid(self, aid: str, emu_idx: str) -> None:
        """重新入队被兜底释放的账号（不能丢 — 自动恢复）。

        150s 超时/无时间戳释放标记后，账号既不 running 也不 queued 就消失了
        （恢复链断裂）。这里把它放回队列（auto 优先级），下个 tick 立即重启。
        挂起账号 / 无模拟器账号不重新入队。

        防无限循环: 连续兜底释放超过阈值（默认 5 次）→ 挂起账号 + 告警 —
        反复启动即死的账号（如模拟器起不来）不能无限重试占资源。
        """
        try:
            _ac = next((a for a in self.ctx.accounts if a.get("id") == aid), None)
            if not _ac or not _ac.get("emu_instance_index") or _ac.get("suspended"):
                return
            if self.is_queued(aid) or self.is_running(aid):
                return
            n = self._stale_release_count.get(aid, 0) + 1
            self._stale_release_count[aid] = n
            _max = int(self.ctx.config.get("max_stale_releases", 5) or 5)
            if n >= _max:
                _ac["suspended"] = True
                try:
                    from models.config_manager import save_config
                    save_config(self.ctx.config)
                except Exception:
                    pass
                _QUEUE_LOG.warn(f"⛔ {_ac.get('name', aid)} 连续兜底释放 {n} 次（模拟器/账号反复启动即死），已挂起 — 请检查")
                return
            self.enqueue(aid, "auto", priority=0)
            _QUEUE_LOG.warn(f"重新入队 {aid[:8]}（兜底释放后恢复，第{n}次）")
        except Exception as ex:
            _QUEUE_LOG.debug(f"重新入队失败 {aid[:8]}: {ex}")

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
        """Persist queue to queue.json (separate from config.json for performance).
        包含 active 账号（运行中）— 崩溃/重启后 _restore 重新入队重跑，
        否则 active 账号丢失（2026-08-11 实测重启后反复手动补队列）。"""
        data = []
        for e in self._pending:
            data.append({"account_id": e.account_id, "source": e.source,
                         "priority": e.sort_key[0], "not_before": e.not_before.strftime("%Y-%m-%d %H:%M:%S"),
                         "persist_plan": e.persist_plan, "active": False})
        for emu_idx, aid in list(self._active_emus.items()):
            ts = self._active_emus_ts.get(emu_idx, 0)
            nb = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else time.strftime("%Y-%m-%d %H:%M:%S")
            data.append({"account_id": aid, "source": "active", "priority": 0,
                         "not_before": nb, "persist_plan": False, "active": True})
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
        try:
            self._save_queue()
        except Exception:
            pass
        if data:
            self._tick()
