"""Single-account launch → monitor → complete cycle runner."""
from __future__ import annotations
import time, subprocess, re, json, os, threading
from pathlib import Path
from datetime import datetime
from typing import Any

from collections.abc import Callable

from infrastructure.task_constants import find_mumu_cli, CF
from app.service_context import ServiceContext
from models.stats import RunStats

_STAGE_CHECKS_PATH = Path(__file__).parent.parent / "models" / "stage_checks.json"


def _close_mumu_popups():
    """Close MuMu error popup windows (manual API use only)."""
    try:
        import ctypes, ctypes.wintypes, time as _tt
        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010
        enum_windows = user32.EnumWindows
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        closed = set()

        def _is_popup(hwnd):
            if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
                return False
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            return w < 500 and h < 300

        def _close_hwnd(hwnd, method="close"):
            try: user32.PostMessageW(hwnd, WM_CLOSE if method == "close" else 0x0001, 0, 0)
            except: pass
            closed.add(hwnd)

        def _find_and_click_ignore(hwnd):
            for text in ("忽略", "Ignore", "确定", "OK"):
                child = user32.FindWindowExW(hwnd, None, None, text)
                if child:
                    user32.PostMessageW(child, 0x00F5, 0, 0)
                    _tt.sleep(0.1)
                    if not user32.IsWindowVisible(hwnd):
                        return True
                child = user32.FindWindowExW(hwnd, None, "Button", text)
                if child:
                    user32.PostMessageW(child, 0x00F5, 0, 0)
                    _tt.sleep(0.1)
                    if not user32.IsWindowVisible(hwnd):
                        return True
            return False

        def callback(hwnd, lparam):
            length = user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            title = buf.value
            if not user32.IsWindowVisible(hwnd):
                return True
            is_error = False
            if title == 'MuMuNxDevice':
                if _is_popup(hwnd):
                    is_error = True
            elif '异常' in title and _is_popup(hwnd):
                is_error = True
            elif 'CrashReporter' in title or '崩溃' in title:
                # MuMu 崩溃报告弹窗（关闭模拟器时可能出现）— 直接关闭
                if hwnd not in closed:
                    _close_hwnd(hwnd, "close")
                    closed.add(hwnd)
                return True
            elif '运行任务' in title or '确定要退出' in title:
                # MAA 确认退出弹窗（ConfirmExit: "MAA 正在运行任务 确定要退出吗?"）
                # — 我们的优雅关闭（WM_CLOSE）触发它，模态卡住 MAA 直到用户确认。
                # 自动点"是"（Yes）→ 优雅关闭完成 → MAA 正常退出（不留弹窗、不强杀）。
                if hwnd not in closed:
                    clicked = False
                    for text in ("是(Y)", "是(&Y)", "是", "Yes", "&Yes", "确定"):
                        child = user32.FindWindowExW(hwnd, None, "Button", text)
                        if child:
                            user32.PostMessageW(child, 0x00F5, 0, 0)  # BM_CLICK
                            clicked = True
                            _tt.sleep(0.2)
                            break
                    if not clicked:
                        _close_hwnd(hwnd, "close")
                    closed.add(hwnd)
                return True
            if is_error:
                if hwnd not in closed and not _find_and_click_ignore(hwnd):
                    _close_hwnd(hwnd, "close")
                    _tt.sleep(0.3)
                    if user32.IsWindowVisible(hwnd):
                        _close_hwnd(hwnd, "destroy")
            return True

        enum_windows(enum_proc(callback), 0)
    except Exception:
        pass


class AccountRunner:
    """Encapsulates single-account launch → monitor → complete lifecycle."""

    # MuMu 12 can't handle concurrent `control launch` (2nd instance fails to
    # boot, ADB never comes up). Serialize emulator launches globally; MAA
    # tasks still run in parallel afterwards.
    _EMU_LAUNCH_LOCK = threading.Lock()

    MAX_TOTAL_MEM_MB = 12288
    RESUME_MEM_MB = 6144
    MAX_PROC_MEM_MB = 4096
    MAX_RESTART_PER_MIN = 4

    def __init__(self, ctx: ServiceContext) -> None:
        # Callback lists (replaces Qt Signals for PySide6-free operation)
        self._log_msg_callbacks: list[Callable[[str], None]] = []
        self._status_msg_callbacks: list[Callable[[str], None]] = []
        self._started_callbacks: list[Callable[[str], None]] = []
        self._finished_callbacks: list[Callable[[tuple], None]] = []
        self._error_callbacks: list[Callable[[str, str], None]] = []
        self.ctx = ctx
        self._active: dict[str, dict] = {}
        self._progs: dict[str, list[dict]] = {}
        self._procs: dict[str, Any] = {}
        self._inst_reserved: dict[str, str] = {}  # aid -> inst_path（分配即预留，防并发同实例）
        self._start_times: dict[str, float] = {}
        self._task_start_times: dict[str, float] = {}
        self._stopping: set = set()
        self._proc_info: dict[str, dict] = {}
        self._restart_times: dict[str, list[float]] = {}
        self._overloaded = False
        self._log_buffers: dict[str, list[str]] = {}
        self._log_positions: dict[str, int] = {}
        self._gui_log_positions: dict[str, int] = {}
        self._log_handles: dict[str, Any] = {}
        self._adb_fail_count: dict[str, int] = {}
        self._adb_restart_count: dict[str, int] = {}
        self._adb_check_ts: float = 0.0
        self._done_flags: dict[str, bool] = {}  # 完成检测标记（归 0 竞态防护）
        self._watchers: dict = {}  # aid → LogWatcher（日志事件流）
        self._err_windows: dict = {}  # aid -> [ts]（连续子任务错误窗口）
        self._emu_fail_count: dict[str, int] = {}
        self._core_instances: dict[str, Any] = {}  # aid -> MaaCore instance (direct drive)
        self._core_tasks: dict[str, list[dict]] = {}  # aid -> [{name,status}]
        self._downgrading: dict[str, bool] = {}  # aid -> downgrade in progress
        from infrastructure.logger import Logger
        self._log = Logger("runner")
        self._clean_orphan_marks()

    def _clean_orphan_marks(self) -> None:
        """Startup sweep: clear .pid/.meta whose process no longer exists.
        Orphan MAA processes (crashed / killed without cleanup) leave stale
        marks that block instance reuse and show ghost "running" state.
        Also dedup: the same live PID copied across multiple instances
        (instance pool copy pollution, 2026-08-10) must keep only one mark."""
        try:
            _pool = Path(__file__).parent / "maa" / "instances"
            if not _pool.exists():
                return
            pid_instances: dict[int, list] = {}
            for inst_dir in _pool.glob("*"):
                pf = inst_dir / ".pid"
                if not pf.exists():
                    continue
                try:
                    pid = int(pf.read_text().strip())
                except Exception:
                    pf.unlink(missing_ok=True)
                    (inst_dir / ".meta").unlink(missing_ok=True)
                    continue
                try:
                    import psutil as _pu
                    alive = _pu.pid_exists(pid)
                except ImportError:
                    r = subprocess.run(["tasklist", "/NH", "/FI", f"PID eq {pid}"],
                                       capture_output=True, text=True, timeout=3,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                    alive = str(pid) in r.stdout
                if not alive:
                    pf.unlink(missing_ok=True)
                    (inst_dir / ".meta").unlink(missing_ok=True)
                    self._log.info(f"[孤儿清理] inst {inst_dir.name}: pid {pid} 不存在，清标记")
                    continue
                # 活 PID 去重：同一 PID 只允许一个实例持有（其余是复制污染）
                pid_instances.setdefault(pid, []).append(inst_dir)
            for pid, dirs in pid_instances.items():
                for extra in dirs[1:]:
                    (extra / ".pid").unlink(missing_ok=True)
                    (extra / ".meta").unlink(missing_ok=True)
                    self._log.warn(f"[孤儿清理] inst {extra.name}: pid {pid} 重复标记（复制污染），清理")
        except Exception:
            pass

    # ── Signal replacements (Qt-free callbacks) ──

    def emit_log(self, msg: str) -> None:
        for cb in self._log_msg_callbacks: cb(msg)
    def emit_status(self, msg: str) -> None:
        for cb in self._status_msg_callbacks: cb(msg)
    def emit_started(self, aid: str) -> None:
        for cb in self._started_callbacks: cb(aid)
    def emit_finished(self, data: tuple) -> None:
        for cb in self._finished_callbacks: cb(data)
    def emit_error(self, aid: str, err: str) -> None:
        for cb in self._error_callbacks: cb(aid, err)

    # ── Public API ──

    def is_running(self, account_id: str) -> bool:
        return account_id in self._active

    @property
    def active_count(self) -> int:
        return len(self._active)

    def active_ids(self) -> list[str]:
        return list(self._active.keys())

    def _detect_emu_address(self, emu_idx) -> str:
        """Get the current ADB address for an emulator — direct detection.

        The emulator is the source of truth for its port: MuMuManager
        single-query returns the real value. Formula ports (16384+idx*32)
        drift after emulator restarts, and mumu-cli index-mismatches on
        MuMu 12 — both are banned here.
        """
        from infrastructure.task_constants import detect_emu_adb
        return detect_emu_adb(emu_idx)

    def _auto_derive(self, ac: dict) -> None:
        """Auto-fill runtime fields — with emulator ADB port detection."""
        # ADB port via MuMuManager (--vmindex all). Single-query mumu-cli
        # index mismatch returns wrong ports (16992 vs 16708), so we always
        # use the all-instances query here.
        if ac.get("emu_instance_index") and not ac.get("adb_address"):
            addr = self._detect_emu_address(ac["emu_instance_index"])
            if addr:
                ac["adb_address"] = addr
        if not ac.get("adb_path"):
            cli = find_mumu_cli()
            if cli:
                cand = Path(cli).parent / "adb.exe"
                if cand.exists():
                    ac["adb_path"] = str(cand)
        if not ac.get("adb_path"):
            # MuMu 12 has no mumu-cli — fall back to drive-wide adb search
            # (covers nx_main/MuMuManager installs). Without this, adb_path stays
            # empty and the code falls back to bare "adb" (system PATH) → connect fails.
            from infrastructure.task_constants import find_adb
            adb = find_adb()
            if adb:
                ac["adb_path"] = adb
        ac.setdefault("connection_preset", "MuMuEmulator12")
        ac.setdefault("touch_mode", "MiniTouch")
        ac.setdefault("post_action", "ExitEmulator,ExitSelf")

    def _get_free_instance(self, aid: str) -> tuple[int, str] | None:
        """Get a free MAA instance directory."""
        try:
            import psutil as _psutil
            _pid_exists = _psutil.pid_exists
        except ImportError:
            def _pid_exists(pid: int) -> bool:
                try:
                    r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                                      capture_output=True, text=True, timeout=2,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
                    return str(pid) in r.stdout
                except Exception:
                    return True
        max_n = max(self.ctx.config.get("maa_instances", 9), self.ctx.config.get("parallel_max", 1))
        pool = Path(__file__).parent / "maa" / "instances"
        for i in range(1, max_n + 1):
            inst_dir = pool / str(i)
            exe = inst_dir / "MAA.exe"
            if not exe.exists():
                continue
            inst_path = str(inst_dir)
            already_used = any(
                p is not None and (
                    (isinstance(p, str) and p == inst_path) or
                    (getattr(p, "_inst_path", None) == inst_path)
                )
                for p in self._procs.values()
            )
            if already_used:
                continue
            # 并发预留: _procs[aid] 要到 _spawn_instance（MAA 启动后）才写入，
            # 中间有 30-60s（模拟器启动）窗口 — 两个并发启动会拿到同一实例，
            # 后启动的 _launch_for_instance 会把先启动的 MAA taskkill（5s 退出，
            # 2026-08-10 反复"启动失败 MAA 5s 退出"）。分配即预留。
            if inst_path in self._inst_reserved.values():
                continue
            pid_file = inst_dir / ".pid"
            running = False
            try:
                pid = int(pid_file.read_text().strip())
                running = _pid_exists(pid)
            except Exception:
                pid_file.unlink(missing_ok=True)
            if not running:
                # 僵尸残留防线: .pid 缺失/进程已死，但实例目录仍有活动（旧 MAA
                # 被杀未死透 / 刚启动）→ 视为占用。否则新账号分配该实例 →
                # MAA 启动撞 "Existing instance" → 2s 退出 → asst.log 停滞 →
                # "日志停滞"判定 → 误关模拟器（2026-08-10 连锁根因）。
                try:
                    _al = inst_dir / "debug" / "asst.log"
                    if _al.exists() and time.time() - _al.stat().st_mtime < 120:
                        continue
                except Exception:
                    pass
                self._inst_reserved[aid] = inst_path
                return (i, str(inst_dir))
        return None

    def launch(self, row: int) -> bool:
        if row < 0 or row >= len(self.ctx.accounts):
            self.emit_log(f"无效账号索引: {row}")
            return False
        return self._launch_account(self.ctx.accounts[row])

    def _launch_account(self, ac: dict) -> bool:
        aid = ac["id"]
        if aid in self._active:
            # Stale mark: MAA process may have died without _cleanup (crash/kill
            # raced the exit handler). If the process is really gone, clear the
            # mark and proceed — otherwise every launch returns "已在运行中"
            # and the queue loops forever (launch → fail → requeue).
            if not self._has_real_process(aid):
                self._log.warn(f"[残留清理] {ac.get('name', aid)} _active 标记但进程已死，清理后重试")
                self._cleanup(aid, -1, [])
            else:
                self.emit_log(f"{ac.get('name', aid)} 已在运行中")
                return False

        self._auto_derive(ac)

        if not ac.get("adb_address") and not ac.get("emu_instance_index"):
            self.emit_log(f"{ac.get('name', aid)} 未配置模拟器索引和 ADB，跳过")
            return False
        if not ac.get("adb_path") and not ac.get("_connect_only"):
            self.emit_log(f"{ac.get('name', aid)} 未找到 adb.exe，跳过")
            return False
        inst = self._get_free_instance(aid)
        if not inst:
            from services.maa_download import _is_source_ready
            _src = Path(__file__).parent / "maa" / "source"
            if not _is_source_ready(_src):
                self.emit_log(f"{ac.get('name', aid)} MAA 未就绪（首次下载中），任务留在队列等待")
                return False
            self.emit_log(f"{ac.get('name', aid)} 无空闲 MAA 实例")
            return False

        self.emit_log(f"[启动] ADB({ac.get('adb_address','?')}) Emu({ac.get('emu_instance_index','?')}) 实例#{inst[0]}")
        self._active[aid] = ac
        self._progs[aid] = [w for w in self.ctx.warehouse if w.get("account_ref") == aid]
        self.emit_log(f"[启动] {ac.get('name', aid)}")
        self._track_stats(ac)
        self._do_launch(ac, inst)
        return True

    def launch_by_id(self, account_id: str) -> bool:
        for i, a in enumerate(self.ctx.accounts):
            if a["id"] == account_id:
                return self.launch(i)
        # Connect-only temp accounts live in ctx._mw.connect_accounts
        try:
            conn = getattr(getattr(self.ctx, "_mw", None), "connect_accounts", None)
            if conn:
                for a in conn:
                    if a.get("id") == account_id:
                        return self._launch_account(a)
        except Exception:
            pass
        return False

    def stop(self, account_id: str) -> None:
        """Stop a running account's MAA process — gracefully (WM_CLOSE) first,
        so MAA releases its ADB connection and touch services before exit.
        Hard-killing (TerminateProcess) mid-connection corrupts the emulator's
        ADB/touch state (MuMu shows '运行异常' afterwards)."""
        self._stopping.add(account_id)
        # Stop MaaCore direct-drive instance if present (no GUI process)
        core = self._core_instances.pop(account_id, None)
        if core:
            try:
                core.stop()
            except Exception:
                pass
            try:
                core.destroy()
            except Exception:
                pass
            self.emit_log(f"已停止 Core 直连任务")
        p = self._procs.pop(account_id, None)
        if p and hasattr(p, 'pid'):
            try:
                from models.account_state import AccountState
                AccountState(account_id).on_stopped("手动停止")
            except Exception:
                pass
            self._graceful_close(p)
            try:
                p.wait(5)  # MAA usually exits <1s after WM_CLOSE
            except Exception:
                pass
            if p.poll() is None:  # still alive → fall back to hard kill
                try: p.terminate(); p.wait(2)
                except: pass
                try: p.kill()
                except: pass
        self._active.pop(account_id, None)
        self.ctx.proc_status.discard(account_id)
        self._release_emu_mark(account_id)
        # Remove .pid/.meta so the instance isn't treated as occupied/stale on
        # the next launch (stop() doesn't go through _cleanup). Without this,
        # a stopped task leaves a stale .pid that `_get_free_instance` skips
        # (PID reuse false-positive) and .meta that mislabels the instance.
        try:
            if p is not None and getattr(p, "_inst_path", None):
                _ip = Path(p._inst_path)
                (_ip / ".pid").unlink(missing_ok=True)
                (_ip / ".meta").unlink(missing_ok=True)
        except Exception:
            pass

    def _release_emu_mark(self, account_id: str) -> None:
        """Release queue-side occupancy marks immediately so a relaunch isn't
        blocked by the 150s stale-cleaner (covers stop, crash, and cleanup paths)."""
        try:
            lq = getattr(getattr(self.ctx, "_mw", None), "launch_queue", None)
            if lq:
                key = lq._get_emu_key(account_id)
                with lq._lock:
                    lq._active_emus.pop(key, None)
                    lq._active_emus_ts.pop(key, None)
        except Exception:
            pass

    def _graceful_close(self, proc) -> bool:
        """Send WM_CLOSE to the process's main window → MAA's OnClose runs
        Bootstrapper.Shutdown() which releases ADB/touch resources cleanly."""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            # 64 位 HWND 必须显式声明参数类型 — 否则 ctypes 按 32 位 int 截断
            # HWND → OverflowError → EnumWindows 回调中断 → 找不到窗口 →
            # WM_CLOSE 发不出去 → MAA 无法优雅关闭（2026-08-10 发现）。
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            found = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def _cb(hwnd, _lp):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == proc.pid and user32.IsWindowVisible(hwnd):
                    found.append(hwnd)
                    return False  # found main window → stop enumerating
                return True

            user32.EnumWindows(_cb, 0)
            if found:
                user32.PostMessageW(found[0], 0x0010, 0, 0)  # WM_CLOSE
                return True
        except Exception:
            pass
        return False

    def _find_emu_pid(self, addr: str) -> int | None:
        if not addr:
            return None
        try:
            host, port_str = addr.rsplit(":", 1)
            port = int(port_str)
            import psutil as _ps
            for conn in _ps.net_connections():
                if conn.status == "LISTEN" and conn.laddr.port == port:
                    return conn.pid
        except Exception:
            pass
        return None

    # ── Launch pipeline ──

    def _has_real_process(self, aid: str) -> bool:
        """Check if account has a running MAA process (not a dead Popen or string)."""
        p = self._procs.get(aid)
        if p is None or isinstance(p, str):
            return False
        if not hasattr(p, 'pid'):
            return False
        try:
            return p.poll() is None  # None = still running
        except:
            return False

    def _do_launch(self, ac: dict, inst: tuple[int, str]) -> None:
        aid = ac["id"]
        inst_id, inst_dir = inst
        self._procs[aid] = inst_dir
        # Write .meta before MAA starts so log endpoint can find it
        try:
            (Path(inst_dir) / ".meta").write_text(f"{aid}|{ac.get('name', aid)}", encoding="utf-8")
        except:
            pass
        import threading as _th
        try:
            _th.Thread(target=self._launch_job, args=(ac, inst), daemon=True).start()
        except Exception as e:
            self._log.error(f"[启动] {ac.get('name', aid)} 线程创建失败: {e}")
            self._cleanup(aid, -1, [])

    def _launch_job(self, ac: dict, inst: tuple[int, str]) -> None:
        aid = ac["id"]
        _deadline = time.time() + 120

        def _check_deadline(phase: str) -> bool:
            if time.time() > _deadline:
                self.emit_log(f"[超时] {ac.get('name', aid)} {phase} 超时，放弃启动")
                self._cleanup(aid, -2, [])
                return True
            return False

        try:
            self._launch_job_body(ac, inst, _check_deadline)
        except Exception as e:
            self._log.error(f"[崩溃] {ac.get('name', aid)} launcher 异常: {e}")
            self.emit_log(f"[崩溃] launcher 异常: {e}")
            self._cleanup(aid, -999, [])

    def _launch_job_body(self, ac: dict, inst: tuple[int, str], _check_deadline) -> None:
        emu_idx = ac.get("emu_instance_index", "")
        aid = ac["id"]
        inst_id, inst_dir = inst
        self._adb_restart_count.pop(aid, None)

        # Re-detect ADB port before EVERY launch. Emulator restarts shift the
        # ADB port (MuMu 12, e.g. 16416 -> 16417), so a cached adb_address can
        # be stale and `adb connect` fails forever. Always refresh from
        # MuMuManager — the emulator is the source of truth for its port.
        if emu_idx:
            addr = self._detect_emu_address(emu_idx)
            if addr:
                ac["adb_address"] = addr

        # Launch emulator
        if emu_idx:
            cli = find_mumu_cli()
            if cli is None and ac.get("adb_path"):
                # MuMu 12: no mumu-cli — MuMuManager.exe lives next to adb.exe
                try:
                    cand = Path(ac["adb_path"]).parent / "MuMuManager.exe"
                    if cand.exists():
                        cli = str(cand)
                except Exception:
                    pass
            if cli:
                # Serialize emulator launch: MuMu 12 fails when 2+ `control
                # launch` run concurrently (2nd instance never boots).
                with self._EMU_LAUNCH_LOCK:
                    # CLI syntax differs: mumu-cli uses --vmindex, MuMuManager -v
                    use_mm = "MuMuManager" in cli
                    idx_flag = "-v" if use_mm else "--vmindex"
                    already_running = False
                    try:
                        import json as _json
                        r = subprocess.run([cli, "info", idx_flag, str(emu_idx)],
                                          capture_output=True, text=True, timeout=5, creationflags=CF,
                                          encoding="utf-8", errors="replace")
                        if r.returncode == 0:
                            data = _json.loads(r.stdout)
                            if data.get("is_android_started") or data.get("is_process_started"):
                                already_running = True
                    except: pass
                    if already_running:
                        self.emit_log(f"模拟器 #{emu_idx} 已在运行")
                    else:
                        self.emit_log(f"启动模拟器 #{emu_idx}")
                    # 记录系统启动的模拟器（回收只关系统拉的；用户手动开的
                    # （MuMu 管理器）不在记录 → 永不回收 — 2026-08-11 用户:
                    # 手动启动的模拟器也被关掉）
                    try:
                        from services.emu_service import mark_system_started
                        mark_system_started(emu_idx)
                    except Exception:
                        pass
                    try:
                        subprocess.run([cli, "control", idx_flag, str(emu_idx), "launch"], creationflags=CF, timeout=15)
                    except Exception as e:
                        self.emit_log(f"启动模拟器失败: {e}")
            # Re-detect ADB port AFTER launching — detect_emu_instances only
            # returns running emulators, so a cold start needs a second pass.
            # Retry loop: right after `launch` the Android guest isn't up yet
            # and port detection returns empty/wrong ports (e.g. 16708 vs real
            # 16768). Keep probing until a valid port appears or timeout.
            # UNCONDITIONAL: even if ac["adb_address"] is cached, the emulator
            # may have been restarted since (port +1 drift on MuMu 12) — the
            # cached value is stale and MAA would connect to the wrong port
            # (2026-08-10: b-2/b-3 injected 16416/16448 while real ports were
            # 16417/16449 → MAA connect fail).
            if emu_idx:
                from infrastructure.task_constants import detect_emu_instances
                wait = int(ac.get("emu_wait", 60))
                deadline = time.time() + wait
                _prev = ac.get("adb_address", "")
                while time.time() < deadline:
                    try:
                        for e in detect_emu_instances():
                            if str(e.get("index", "")) == str(emu_idx) and e.get("adb_port"):
                                ac["adb_address"] = f"127.0.0.1:{e['adb_port']}"
                                break
                        if ac.get("adb_address"):
                            _changed = "" if ac["adb_address"] == _prev else f"（{_prev} → {ac['adb_address']}）"
                            self.emit_log(f"模拟器 #{emu_idx} ADB 端口 {ac['adb_address']}{_changed}")
                            break
                    except Exception:
                        pass
                    time.sleep(5)
                if not ac.get("adb_address"):
                    self.emit_log(f"警告: 模拟器 #{emu_idx} 端口探测超时")
            adb = ac.get("adb_path", "") or "adb"
            addr = ac.get("adb_address", "")
            if addr:
                wait = int(ac.get("emu_wait", 60))
                deadline = time.time() + wait
                self.emit_log(f"等待模拟器 ADB 连接 (最长 {wait}s)...")
                while time.time() < deadline:
                    try:
                        r = subprocess.run([adb, "connect", addr], capture_output=True, timeout=5, creationflags=CF)
                        if r.returncode == 0 and b"connected" in r.stdout.lower():
                            self.emit_log(f"模拟器 #{emu_idx} ADB 已连接")
                            break
                    except: pass
                    time.sleep(2)
                else:
                    self.emit_log(f"警告: 模拟器 #{emu_idx} ADB 连接超时")
                    # Port drift fix: MuMu 12 emulator restarts can shift the
                    # ADB port (e.g. 16416 -> 16417). The cached adb_address is
                    # then stale and `adb connect` fails forever. Clear it and
                    # re-detect from MuMuManager (--vmindex all gives correct ports).
                    if emu_idx:
                        ac.pop("adb_address", None)
                        new_addr = self._detect_emu_address(emu_idx)
                        if new_addr:
                            ac["adb_address"] = new_addr
                            self.emit_log(f"端口漂移修复: 模拟器 #{emu_idx} → {new_addr}")
                        addr = ac.get("adb_address", "")
                self.emit_log(f"等待 Android 开机完成...")
                # boot_completed 独立等待（不 share 端口探测的 deadline）—
                # 模拟器冷启动在 50 台多开负载下 boot 可能 60-120s。MAA 必须
                # 在 Android 完全启动后 spawn，否则连半启动系统 → 连接失败/
                # 失联/假画面空转（2026-08-11 用户指出: MAA 应在模拟器完全
                # 启动后再启动）。
                _boot_dl = time.time() + int(ac.get("boot_wait", 90))
                _booted = False
                while time.time() < _boot_dl:
                    try:
                        r = subprocess.run([adb, "-s", addr, "shell", "getprop", "sys.boot_completed"],
                                          capture_output=True, timeout=5, creationflags=CF,
                                          encoding="utf-8", errors="replace")
                        if r.returncode == 0 and r.stdout.strip() == "1":
                            self.emit_log(f"Android 开机完成")
                            _booted = True
                            break
                    except Exception:
                        pass
                    time.sleep(2)
                if not _booted:
                    # boot 未完成 → 放弃本次启动（同超时路径: 清理标记 →
                    # 队列重试）。绝不带半启动系统 spawn MAA。
                    # ⚠️ 2026-08-11: 仅放弃不够 — 模拟器进程活着但 Android
                    # 卡死（崩溃重启后状态损坏）时，重试发现进程在 → 不重启
                    # 模拟器 → 永远等 boot → 死循环。放弃前重启模拟器
                    # （shutdown+launch 彻底重置），下次重试 boot 成功率高。
                    try:
                        from infrastructure.task_constants import find_mumu_cli as _find_cli, cli_flag as _cflag
                        _cli = _find_cli()
                        if _cli and emu_idx:
                            subprocess.run([_cli, "control", _cflag(_cli), str(emu_idx), "shutdown"],
                                           capture_output=True, timeout=15, creationflags=CF)
                            time.sleep(3)
                            subprocess.run([_cli, "control", _cflag(_cli), str(emu_idx), "launch"],
                                           capture_output=True, timeout=15, creationflags=CF)
                            self.emit_log(f"🔄 Android 开机超时，已重启模拟器 #{emu_idx}")
                    except Exception:
                        pass
                    self.emit_log(f"⚠️ Android 开机超时（{int(ac.get('boot_wait', 90))}s），放弃本次启动")
                    self._cleanup(aid, -2, [])
                    return

        if _check_deadline("ADB连接"):
            return

        addr = ac.get("adb_address", "")
        if addr:
            for _attempt in range(3):
                try:
                    r = subprocess.run([adb, "connect", addr], capture_output=True, creationflags=CF, timeout=5)
                    if r.returncode == 0:
                        break
                except: pass
                time.sleep(2)
            time.sleep(1)

        if _check_deadline("注入配置"):
            return

        self._launch_for_instance(ac, inst_dir)

    def _launch_for_instance(self, ac: dict, inst_dir: str) -> None:
        aid = ac["id"]
        mode = self.ctx.config.get("schedule_mode", "daily")
        exe = Path(inst_dir) / "MAA.exe"
        config_dir = Path(inst_dir) / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        # Kill any stale MAA still holding this instance dir BEFORE injecting —
        # a running MAA overwrites the injected config (StagePlan etc.) with its
        # own in-memory values on save, so the new launch reads stale data.
        try:
            pf = Path(inst_dir) / ".pid"
            if pf.exists():
                _pid = int(pf.read_text().strip())
                _r = subprocess.run(["tasklist", "/NH", "/FI", f"PID eq {_pid}"],
                                    capture_output=True, text=True, timeout=3,
                                    creationflags=CF)
                if str(_pid) in _r.stdout:
                    self._log.warn(f"[注入] {ac.get('name', aid)} 清理实例残留 MAA PID={_pid}")
                    subprocess.run(["taskkill", "/F", "/PID", str(_pid)],
                                   capture_output=True, timeout=5, creationflags=CF)
                    time.sleep(2)
        except Exception:
            pass
        try:
            # Unified dispatch path: use slot-based dispatch_id
            from services.dispatch_pool import get_template
            _slot = ac.get("_slot", "")
            _did_key = f"_dispatch_{_slot}" if _slot else "dispatch_id"
            did = ac.get(_did_key, "") or ac.get("dispatch_id", "")
            task_list = get_template(did) if did else None
            if task_list is None:
                if mode == "roguelike":
                    task_list = ["StartUp", "Roguelike"]
                elif mode == "reclamation":
                    task_list = ["StartUp", "Reclamation"]
                elif ac.get("skip_daily"):
                    # Skip farming AND daily tasks — sanity already exhausted,
                    # just claim awards (Award/Mail/Orundum/Mining/SpecialAccess).
                    task_list = ["StartUp", "Award"]
                else:
                    task_list = ["StartUp", "Fight", "Infrast", "Recruit", "Mall", "Award"]
            # Per-account annihilation (overrides dispatch template)
            if ac.get("smart_annihilation", ""):
                if "Annihilation" not in task_list:
                    task_list.append("Annihilation")
            else:
                if "Annihilation" in task_list:
                    task_list.remove("Annihilation")
            plan_txt = ",".join(task_list)
            self.emit_log(f"🧠 智能调度: {plan_txt}")
            self._log.info(f"[注入] {ac.get('name', aid)} task_list={task_list}")
            self.ctx.cfg.inject_smart(task_list, ac, str(config_dir))
            self.emit_log(f"注入配置: {config_dir}/")
            try:
                from services.log_watcher import record_event
                record_event(aid, "launch", f"task_list={plan_txt}")
            except Exception:
                pass
            # 防孤儿 MAA: 启动等待期间（模拟器冷启动/ADB 探测）队列可能已释放
            # 本账号标记（_clean_stale_emus/超时清理）— 此刻再 spawn 就是孤儿
            # （进程活着但无人跟踪 → 占实例不释放 → 无空闲 MAA 实例连环）。
            # 实测 2026-08-10: 9 个 MAA 活着但队列 active 只有 4。
            try:
                _lq = getattr(getattr(self.ctx, "_mw", None), "launch_queue", None)
                if _lq is not None and ac.get("emu_instance_index"):
                    _occ = _lq._active_emus.get(str(ac.get("emu_instance_index")))
                    if _occ != aid:
                        self.emit_log(f"↩ {ac.get('name', aid)} 启动被队列取消（标记已释放），放弃 spawn")
                        self._inst_reserved.pop(aid, None)  # 释放预留，防泄漏
                        return
            except Exception:
                pass
            # 防僵尸 MAA: cleanup 会移除 _procs 占位（_do_launch 写入的实例
            # 目录字符串）— 若占位已不在（期间被清理过），再 spawn 就是无主
            # 进程（cleanup 对字符串占位不杀进程 → MAA 活着没人管 →
            # zombie_maa 累积占满实例 — 2026-08-10 实测 5 个僵尸）。
            if self._procs.get(aid) is not inst_dir:
                self.emit_log(f"↩ {ac.get('name', aid)} 启动已被清理（占位移除），放弃 spawn")
                self._inst_reserved.pop(aid, None)  # 释放预留，防泄漏
                return
            self._spawn_instance(exe, ac, inst_dir)
            try:
                from models.account_state import AccountState
                AccountState(aid).on_login()
            except Exception:
                pass
            self._active[aid] = ac
        except Exception as e:
            self._log.error(f"[启动] {ac.get('name', aid)} 启动失败: {e}")
            self.emit_log(f"启动失败: {e}")
            self.emit_error(aid, str(e))
            self._cleanup(aid, -1, [])

    def _launch_core_daily(self, ac: dict, inst_dir: str) -> bool:
        """Drive MaaCore directly for connect-mode daily tasks (no MAA GUI).
        Appends StartUp/Infrast/Recruit/Mall/Award, connects via ADB, starts,
        and reports progress via callbacks. Returns True if Core is usable."""
        aid = ac["id"]
        try:
            from infrastructure import maa_core
            if not maa_core.is_loaded():
                return False
            addr = ac.get("adb_address", "")
            adb_path = ac.get("adb_path", "")
            if not addr or not adb_path:
                self.emit_log(f"{ac.get('name', aid)} ADB 信息不完整，无法 Core 直连")
                return False
            client = ac.get("game_client", "") or "Official"
            connect_cfg = (ac.get("connection_preset") or "MuMuEmulator12")
            connect_cfg = {"MuMuPro": "MuMuEmulator12"}.get(connect_cfg, connect_cfg)

            from services.dispatch_pool import get_template
            _did_key = f"_dispatch_{ac.get('_slot','')}" if ac.get("_slot") else "dispatch_id"
            task_list = get_template(ac.get(_did_key, "")) or ["StartUp", "Infrast", "Recruit", "Mall", "Award"]

            # Task params per MAA Core integration spec
            tasks = []
            for t in task_list:
                if t == "StartUp":
                    tasks.append(("StartUp", {"enable": True, "client_type": client,
                                              "start_game_enabled": True}))
                elif t == "Infrast":
                    tasks.append(("Infrast", {"enable": True, "mode": 0,
                                              "facility": ["Mfg", "Trade", "Power", "Control",
                                                           "Reception", "Office", "Dorm"],
                                              "drones": "Money", "dorm_threshold": 0.3,
                                              "dorm_trust_enabled": True,
                                              "dorm_filter_not_stationed": True,
                                              "originium_shard_auto": True}))
                elif t == "Recruit":
                    tasks.append(("Recruit", {"enable": True, "refresh": True,
                                              "select": [5, 4, 3], "confirm": [5, 4, 3],
                                              "times": 4}))
                elif t == "Mall":
                    tasks.append(("Mall", {"enable": True, "shopping": True,
                                           "credit_fight": False, "visit_friends": True,
                                           "blacklist": ["碳", "家具", "加急许可"]}))
                elif t == "Award":
                    tasks.append(("Award", {"enable": True, "award": True, "mail": True,
                                            "free_gacha": False, "orundum": True}))
                elif t == "Fight":
                    stage = self._pick_daily_stage(ac)
                    tasks.append(("Fight", {"enable": True, "stage": stage, "times": 99,
                                            "medicine": 0, "stone": 0}))
                # Roguelike / Reclamation / Annihilation: skip in daily connect mode

            if not tasks:
                return False

            state = {"finished": False}
            self._core_tasks[aid] = [{"name": n, "status": "排队"} for n, _ in tasks]

            def _cb(msg: int, details: str, _arg=None):
                self._core_callback(aid, msg, details)

            inst = maa_core.create_instance(_cb)
            if not inst:
                return False
            self._core_instances[aid] = inst
            self._active[aid] = ac
            self._start_times[aid] = time.time()

            # Connect
            self.emit_log(f"🔌 Core 直连 ADB({addr}) client={client}")
            ok = inst.connect(adb_path, addr, connect_cfg)
            if not ok:
                self.emit_log(f"{ac.get('name', aid)} Core 连接失败")
                inst.destroy()
                self._core_instances.pop(aid, None)
                self._cleanup(aid, -1, [])
                return False

            # Append tasks
            for name, params in tasks:
                tid = inst.append_task(name, params)
                if tid <= 0:
                    self.emit_log(f"任务添加失败: {name}")
            self.emit_log(f"▶ Core 任务启动: {','.join(n for n, _ in tasks)}")
            inst.start()
            return True
        except Exception as e:
            self._log.error(f"[Core] {ac.get('name', aid)} 直连异常: {e}")
            self.emit_log(f"Core 直连异常: {e}")
            return False

    def _pick_daily_stage(self, ac: dict) -> str:
        """Pick a fight stage for daily connect mode (default 1-7)."""
        stage = ac.get("fight_default", "") or ac.get("smart_stage", "")
        return stage or "1-7"

    def _core_callback(self, aid: str, msg: int, details: str) -> None:
        """Handle MaaCore callbacks (runs on Core's callback thread)."""
        from infrastructure import maa_core as mc
        name = ac.get("name", aid) if (ac := self._active.get(aid)) else aid
        try:
            if msg == mc.MSG_CONNECTION_INFO:
                try:
                    d = json.loads(details)
                    if not d.get("what") == "ConnectSuccess":
                        self.emit_log(f"[Core] {name} 连接状态: {d.get('what','?')}")
                except Exception:
                    pass
            elif msg == mc.MSG_TASK_CHAIN_START:
                try:
                    d = json.loads(details)
                    t = d.get("taskchain", "")
                    self._update_core_task(aid, t, "运行中")
                    self.emit_log(f"▶ {name} 任务开始: {t}")
                except Exception:
                    pass
            elif msg == mc.MSG_TASK_CHAIN_COMPLETED:
                try:
                    d = json.loads(details)
                    t = d.get("taskchain", "")
                    self._update_core_task(aid, t, "完成")
                    self.emit_log(f"✅ {name} 任务完成: {t}")
                except Exception:
                    pass
            elif msg == mc.MSG_TASK_CHAIN_ERROR:
                try:
                    d = json.loads(details)
                    t = d.get("taskchain", "")
                    self._update_core_task(aid, t, "失败")
                    self.emit_log(f"❌ {name} 任务失败: {t}")
                except Exception:
                    pass
            elif msg == mc.MSG_ALL_TASKS_COMPLETED:
                self.emit_log(f"🏁 {name} 全部任务完成")
                inst = self._core_instances.get(aid)
                if inst:
                    try:
                        inst.destroy()
                    except Exception:
                        pass
                self._core_instances.pop(aid, None)
                tasks = list(self._core_tasks.get(aid) or [])
                self._cleanup(aid, 0, tasks)
        except Exception as e:
            self._log.error(f"[Core回调] {name} 处理异常: {e}")

    def _update_core_task(self, aid: str, taskname: str, status: str) -> None:
        tasks = self._core_tasks.get(aid)
        if not tasks:
            return
        for t in tasks:
            if t["name"].lower() == taskname.lower():
                t["status"] = status
                break

    def _spawn_instance(self, exe: Path, ac: dict, inst_dir: str) -> None:
        aid = ac["id"]
        pid_file = Path(inst_dir) / ".pid"
        # asst.log 清空（MAA 句柄占用可能失败 → 失败则记录当前位置，只解析
        # 本次启动后的新日志 — 否则读到上个账号的残留 → 误判降级/完成）
        _al = Path(inst_dir) / "debug" / "asst.log"
        try:
            _al.write_text("")
            self._log_positions[aid] = 0
        except Exception:
            self._log_positions[aid] = _al.stat().st_size if _al.exists() else 0
        # gui.log 不清空（累积）— 记录当前位置，增量读取只看到本次启动的
        # 新内容。"关卡无效"判定（1052-1065）读 gui.log 增量，位置为 0 会
        # 读到上个账号的旧日志（含"添加任务失败"）→ 1 秒误判降级。
        try:
            _gl = Path(inst_dir) / "debug" / "gui.log"
            self._gui_log_positions[aid] = _gl.stat().st_size if _gl.exists() else 0
        except Exception:
            self._gui_log_positions[aid] = 0
        # MAA resolves .\config\gui.json relative to the process working dir.
        # We MUST run with cwd=instance dir or MAA misses the injected config
        # (RunDirectly=False → never auto-starts). Use resolved real path —
        # junction (unresolved) cwd crashed MAA.exe with E_FAIL.
        real_dir = str(Path(inst_dir).resolve())
        p = subprocess.Popen([str(exe)], shell=False, cwd=real_dir)
        p._inst_path = str(Path(inst_dir).resolve())
        self._procs[aid] = p
        self._start_times[aid] = time.time()
        self.ctx.proc_status.add(aid)
        try: pid_file.write_text(str(p.pid))
        except: pass
        self.emit_log(f"✓ 启动 MAA PID={p.pid}")
        import threading as _th
        def _wait_exit():
            try: p.wait()
            except: pass
            # Direct cleanup — QTimer.singleShot from daemon threads may not fire
            self._on_process_exit(aid, p)
        _th.Thread(target=_wait_exit, daemon=True).start()
        # 日志监控线程（事件驱动完成/失败检测 — 2026-08-11 P1 替代 5s 轮询）
        try:
            from services.log_watcher import LogWatcher
            _w = LogWatcher(inst_dir, aid, self._on_log_event, name=ac.get("name", aid))
            _w.start()
            self._watchers[aid] = _w
        except Exception:
            pass
        # 启动后窗口检查（独立线程，不阻塞）— MAA 配置崩溃时 asst.log 为空，
        # 窗口标题（{{ ErrorCongratulations }} / JsonReaderException）是唯一
        # 诊断线索（2026-08-10 配置崩溃事故：日志全空只有窗口能看出来）。
        _th.Thread(target=self._check_startup_window,
                   args=(aid, ac.get("name", aid)), daemon=True).start()
        self.emit_started(aid)

    def _check_startup_window(self, aid: str, name: str) -> None:
        """MAA 启动 5s 后枚举窗口标题，找异常窗口（错误/异常/崩溃）并记录。"""
        try:
            import time as _tt
            _tt.sleep(5)
            import ctypes
            user32 = ctypes.windll.user32
            found = []

            def _cb(hwnd, _):
                try:
                    ln = user32.GetWindowTextLengthW(hwnd)
                    if ln and ln < 300:
                        buf = ctypes.create_unicode_buffer(ln + 1)
                        user32.GetWindowTextW(hwnd, buf, ln + 1)
                        t = buf.value
                        if t and any(k in t for k in ("Error", "Exception", "错误", "异常", "崩溃")):
                            found.append(t)
                except Exception:
                    pass
                return True

            PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            user32.EnumWindows(PROC(_cb), 0)
            for t in found:
                self._log.warn(f"[MAA窗口] {name} 启动后异常窗口: {t[:100]}")
            if found:
                self._log.warn(f"[MAA窗口] {name} 共 {len(found)} 个异常窗口 — 启动/配置异常，asst.log 可能为空")
        except Exception:
            pass

    def _finish_completed(self, aid: str, ac: dict, p) -> None:
        """完成收尾（AllTasksCompleted）— 轮询检测与 LogWatcher 事件共用。
        完成标记 _done_flags 防 _wait_exit 抢先 cleanup 的竞态（2026-08-11）。
        """
        try:
            self._done_flags[aid] = True
        except Exception:
            pass
        self.emit_log(f"[完成后] {ac.get('name', aid)} 任务全部完成")
        tasks, sanity, drops = self._parse_log(aid)
        _cur = (sanity or {}).get("current")
        _mx = (sanity or {}).get("max") or 1
        _stop_threshold = int(ac.get("stop_sanity_pct", 30) or 30)
        _done_pct = (_cur / _mx * 100) if _cur is not None else 0
        _final = _done_pct <= _stop_threshold
        try:
            p.terminate()
            try:
                p.wait(2)
            except Exception:
                pass
            if p.poll() is None:
                # 立即强杀（terminate 后 MAA 未死会残留僵尸 — 2026-08-11
                # 实测完成轮产生 7 个僵尸 MAA 占实例）
                subprocess.run(["taskkill", "/F", "/PID", str(p.pid)],
                               capture_output=True, timeout=5, creationflags=CF)
        except Exception:
            pass
        self._cleanup(aid, 0, tasks, sanity, drops)
        if not _final and not ac.get("_connect_only"):
            try:
                mw = getattr(self.ctx, "_mw", None)
                lq = getattr(mw, "launch_queue", None)
                if lq is not None and ac.get("emu_instance_index"):
                    self.emit_log(f"↻ {ac.get('name', aid)} 一轮完成（体力 {_done_pct:.0f}%），自动续跑")
                    lq.enqueue(aid, "auto", priority=0, slot=ac.get("_slot", ""))
                    lq.tick()
            except Exception:
                pass

    def _on_log_event(self, event: str, aid: str, line: str) -> None:
        """LogWatcher 事件回调（事件驱动，<0.2s 响应 — 2026-08-11 P1）。"""
        if event == "completed":
            ac = self._active.get(aid)
            p = self._procs.get(aid)
            if not ac or not isinstance(p, subprocess.Popen) or p.poll() is not None:
                return  # 已清理/已结束 — 防重复触发
            self._finish_completed(aid, ac, p)
        elif event == "subtask_error":
            # 连续子任务错误（游戏崩溃/流程异常 → MAA 反复报错，2026-08-11
            # 用户: MAA 日志会报任务错误）— 2 分钟内 ≥3 次 + asst.log 停滞
            # （MAA 无进展）才判定异常。启动早期（游戏加载）的正常识别
            # 重试也会报 SubTaskError 但日志持续写入 → 不误杀
            # （2026-08-11 日-2: 3m45s 被误杀重启 — 用户观察"挺正常的"）。
            try:
                win = self._err_windows.get(aid, [])
                now = time.time()
                win = [t for t in win if now - t < 120]
                win.append(now)
                self._err_windows[aid] = win
                if len(win) >= 3:
                    # 日志活性：MAA 还在重试（日志写）→ 正常，重置窗口
                    p = self._procs.get(aid)
                    _stalled = False
                    try:
                        if isinstance(p, subprocess.Popen):
                            _al = Path(getattr(p, "_inst_path", "")) / "debug" / "asst.log"
                            if _al.exists():
                                _stalled = time.time() - _al.stat().st_mtime > 60
                    except Exception:
                        pass
                    if not _stalled:
                        self._err_windows[aid] = []
                        return
                    self._err_windows.pop(aid, None)
                    ac = self._active.get(aid)
                    if ac and isinstance(p, subprocess.Popen) and p.poll() is None:
                        self.emit_log(f"⚠ {ac.get('name', aid)} 连续子任务错误且日志停滞（游戏异常/崩溃），重启 MAA")
                        try:
                            p.terminate(); p.wait(3)
                        except Exception:
                            pass
                        tasks, sanity, drops = self._parse_log(aid)
                        self._cleanup(aid, -9, tasks, sanity, drops)
            except Exception:
                pass
        elif event == "battle_failed":
            # 作战失败降级触发（FightMissionFailed）— 与 _check_one 的判定
            # 共用防重（_downgrading 标志）
            ac = self._active.get(aid)
            p = self._procs.get(aid)
            if not ac or not isinstance(p, subprocess.Popen):
                return
            if (ac.get("_stage_fallback") and aid not in self._stopping
                    and not self._downgrading.get(aid)):
                try:
                    self._downgrading[aid] = True
                    self._downgrade_stage(aid, ac, p)
                finally:
                    self._downgrading.pop(aid, None)
        # exceeded: 重试耗尽 — 由 _check_one 的 ExceededLimit 判定处理（暂不迁移）

    def _on_process_exit(self, aid: str, p: subprocess.Popen) -> None:
        # Re-entrancy guard: only the CURRENT process object may clean up.
        # A stale exit callback from a superseded process (downgrade restart
        # killed the old MAA and spawned a new one) must NOT clean up — it
        # would wipe the new process's state mid-run (orphan MAA + queue
        # mark released while the new process is still farming).
        if self._procs.get(aid) is not p:
            return
        rc = p.poll()
        tasks, sanity, drops = self._parse_log(aid) if aid in self._active else ([], None, None)
        # MAA exiting within 60s of launch with no completed tasks = startup
        # failure (emulator didn't come up / ADB lost), NOT a normal "done".
        # Otherwise a failed boot is recorded as a successful run and the
        # emulator gets shut down while the queue moves on.
        started = self._start_times.get(aid, 0)
        if rc == 0 and started and time.time() - started < 60 and not tasks:
            self._log.warn(f"[启动失败] {aid} MAA {int(time.time()-started)}s 退出且无任务，按失败处理")
            rc = -12
        # MAA 自行退出但任务链未完成（自动更新重启/资源更新/外部中断）:
        # rc=0 + 有任务还在"运行中" → 不是正常完成 → 按异常处理（自动重启
        # 重试任务）。否则被当作"刷完" → 关模拟器 → 任务丢失（2026-08-10
        # 用户指出: 队伍健康时 MAA 退出应该重试而不是结束）。
        if rc == 0 and any(t.get("status") == "运行中" for t in tasks):
            self._log.warn(f"[未完成退出] {aid} MAA 退出但任务未完成（更新/中断），按异常重试")
            rc = -13
        # PostActions=ExitSelf may exit with a non-zero code even on NORMAL
        # completion. If tasks were completed, treat it as success regardless.
        # _done_flags: 完成检测（AllTasksCompleted 增量）terminate 的 MAA 会被
        # _wait_exit 抢先走这里（rc=1），增量检测的 cleanup(0) 因 _procs 已清
        # 落空 → 完成变 exit=1 → 模拟器不按"完成"关闭（2026-08-11 b-2/b-9）。
        if (self._done_flags.pop(aid, False)
                or (rc not in (0, None) and any(t.get("status") == "完成" for t in tasks))):
            self._log.info(f"[完成后] {aid} MAA 退出(rc={rc}) 但任务已完成，按正常完成处理")
            rc = 0
        self._cleanup(aid, rc or 0, tasks, sanity, drops)

    # ── Monitoring ──

    def check_processes(self) -> None:
        """Monitor all running processes. Called by queue tick every 5s."""
        self._check_resources()
        # 不再做全局弹窗扫描 — 2026-08-10 用户指出：窗口只要动一下（位置/尺寸
        # 变化）就可能被识别为弹窗处理，误伤正常窗口。弹窗只应在我们自己的
        # 主动关闭流程内处理（_downgrade_stage 的 WM_CLOSE 闭环、stop_all、
        # /api/system/close_popups 手动端点），不做无人值守的全窗口扫描。
        for aid in list(self._procs.keys()):
            self._check_one(aid)

    def _check_resources(self) -> None:
        """Monitor system memory and process resource usage."""
        try:
            import psutil
        except ImportError:
            return
        sv = psutil.virtual_memory()
        free_gb = sv.available / 1024 / 1024 / 1024
        now = time.time()
        for aid in list(self._procs.keys()):
            p = self._procs.get(aid)
            info = self._proc_info.setdefault(aid, {"maa": {}, "emu": {}})
            if p and not isinstance(p, str) and hasattr(p, 'pid'):
                try:
                    pp = psutil.Process(p.pid)
                    mem_mb = pp.memory_info().rss / 1024 / 1024
                    cpu_pct = pp.cpu_percent(interval=0.05)
                    info["maa"] = {"mem_mb": mem_mb, "cpu_pct": cpu_pct, "pid": p.pid, "time": now}
                    if mem_mb > self.MAX_PROC_MEM_MB:
                        name = self._active.get(aid, {}).get("name", aid)
                        self.emit_log(f"[资源] {name} 内存超限 ({mem_mb:.0f}MB)")
                        self.stop(aid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    info["maa"] = {}
            else:
                info["maa"] = {}
            ac = self._active.get(aid)
            if ac:
                addr = ac.get("adb_address", "")
                epid = self._find_emu_pid(addr)
                if epid:
                    try:
                        ep = psutil.Process(epid)
                        emu_mem = ep.memory_info().rss / 1024 / 1024
                        emu_cpu = ep.cpu_percent(interval=0.05)
                        info["emu"] = {"mem_mb": emu_mem, "cpu_pct": emu_cpu, "pid": epid, "time": now, "name": ep.name()}
                    except:
                        info["emu"] = {}
                else:
                    info["emu"] = {}
        was = self._overloaded
        self._overloaded = free_gb < 1.0
        if self._overloaded != was:
            self.emit_log(f"[资源] 内存 {'不足' if self._overloaded else '已恢复'} ({free_gb:.1f}GB)")

    def _check_one(self, aid: str) -> None:
        p = self._procs.get(aid)
        if p is None or isinstance(p, str):
            return
        if not hasattr(p, 'poll'):
            return
        if p.poll() is not None:
            # Process died — cleanup stale marks immediately (fallback if the
            # _wait_exit thread is gone). Otherwise a dead MAA blocks relaunch
            # with a stale "already running" mark indefinitely.
            self._log.warn(f"[进程] {aid} MAA 进程已退出 (poll={p.poll()})，清理残留")
            self._on_process_exit(aid, p)
            return
        self._update_status(aid)
        # Process pair health check
        info = self._proc_info.get(aid, {})
        emu = info.get("emu", {})
        emu_pid = emu.get("pid")
        ac = self._active.get(aid)
        # MuMuManager 真实验证模拟器进程在跑 — _find_emu_pid 按 ADB 端口查
        # 监听进程，模拟器死后残留的 ADB 端口会误判"模拟器还在" → MAA 在
        # 假画面空转、无人处理（2026-08-10 官-33: MAA 活着 + 模拟器4进程没了
        # + asst.log 活跃（残留端口截图）→ 卡死到永远）。
        if ac and aid not in self._stopping:
            emu_idx = ac.get("emu_instance_index", "")
            if emu_idx:
                cli = find_mumu_cli()
                if cli is None and ac.get("adb_path"):
                    try:
                        cand = Path(ac["adb_path"]).parent / "MuMuManager.exe"
                        if cand.exists():
                            cli = str(cand)
                    except Exception:
                        pass
                if cli:
                    idx_flag = "-v" if "MuMuManager" in cli else "--vmindex"
                    try:
                        r = subprocess.run([cli, "info", idx_flag, str(emu_idx)],
                                           capture_output=True, text=True, timeout=5,
                                           creationflags=CF, encoding="utf-8", errors="replace")
                        if r.returncode == 0:
                            d = json.loads(r.stdout.lstrip("\ufeff").strip())
                            # bool 防御：MuMuManager 错误返回（errcode≠0）无
                            # is_* 字段 → None → 误判"失联"→ 杀 MAA + 关模拟器
                            # （2026-08-10 实测 3 台同时误判）。无法确认 → 跳过。
                            _pa = d.get("is_android_started"); _pp = d.get("is_process_started")
                            if isinstance(_pa, bool) and isinstance(_pp, bool) and not (_pa or _pp):
                                name = ac.get("name", aid)
                                self._log.warn(f"[模拟器失联] {name} 模拟器#{emu_idx} 进程不在（MAA 空转），杀 MAA 恢复")
                                try:
                                    p.terminate()
                                    p.wait(3)
                                except Exception:
                                    pass
                                tasks, sanity, drops = self._parse_log(aid)
                                self._cleanup(aid, -8, tasks, sanity, drops)
                                return
                    except Exception:
                        pass
        if ac and emu_pid and aid not in self._stopping:
            name = ac.get("name", aid)
            try:
                import psutil as _pu
                ep = _pu.Process(emu_pid)
                if ep.status() == _pu.STATUS_ZOMBIE:
                    raise _pu.NoSuchProcess(emu_pid)
            except Exception:
                fail = self._emu_fail_count.get(aid, 0) + 1
                self._emu_fail_count[aid] = fail
                self._log.warn(f"[进程组] {name} 模拟器进程消失 (PID={emu_pid}) 第{fail}次")
                p = self._procs.get(aid)
                if fail >= 3 or (p and hasattr(p, 'poll') and p.poll() is not None):
                    self._emu_fail_count.pop(aid, None)
                    if p and not isinstance(p, str):
                        try: p.terminate(); p.wait(3)
                        except: pass
                    tasks, sanity, drops = self._parse_log(aid)
                    self._cleanup(aid, -8, tasks, sanity, drops)
                    return
                emu_idx = ac.get("emu_instance_index", "")
                cli = find_mumu_cli()
                if cli is None and ac.get("adb_path"):
                    try:
                        cand = Path(ac["adb_path"]).parent / "MuMuManager.exe"
                        if cand.exists():
                            cli = str(cand)
                    except Exception:
                        pass
                if cli and emu_idx:
                    idx_flag = "-v" if "MuMuManager" in cli else "--vmindex"
                    self.emit_log(f"[进程组] {name} 重启模拟器 #{emu_idx}")
                    # 统一优雅关闭再重启（直接 shutdown 留 VMM 残留）
                    try:
                        from services.launch_queue import graceful_emu_shutdown
                        graceful_emu_shutdown(cli, emu_idx, ac.get("adb_path", ""), ac.get("adb_address", ""))
                    except Exception:
                        subprocess.run([cli, "control", idx_flag, str(emu_idx), "shutdown"],
                                      timeout=10, capture_output=True, creationflags=CF)
                    subprocess.run([cli, "control", idx_flag, str(emu_idx), "launch"],
                                  timeout=10, capture_output=True, creationflags=CF)
                return
        # Task completion detection
        ac = self._active.get(aid)
        if ac and hasattr(p, '_inst_path'):
            # Per-account stage downgrade — Fight rejected as invalid stage
            # ("理智作战: ... 添加任务失败" in gui.log). Checked every tick,
            # independent of asst.log activity (MAA may hang with a dead asst.log).
            if (ac.get("_stage_fallback") and aid not in self._stopping
                    and not self._downgrading.get(aid)):
                glp = Path(p._inst_path) / "debug" / "gui.log"
                if glp.exists():
                    try:
                        gsize = glp.stat().st_size
                        gpos = self._gui_log_positions.get(aid, 0)
                        if gsize >= gpos:
                            with glp.open("r", encoding="utf-8", errors="replace") as gf:
                                gf.seek(gpos)
                                gnew = gf.read(gsize - gpos)
                            self._gui_log_positions[aid] = gsize
                            if ("添加任务失败" in gnew and "理智" in gnew) or \
                               ("selected null" in gnew and "FightStage" in gnew) or \
                               "配置无效" in gnew:
                                self.emit_log(f"⬇ {ac.get('name', aid)} Fight 关卡无效，触发降级")
                                self._downgrading[aid] = True
                                try:
                                    self._downgrade_stage(aid, ac, p)
                                finally:
                                    self._downgrading.pop(aid, None)
                                return
                    except Exception:
                        pass
            lp = Path(p._inst_path) / "debug" / "asst.log"
            if lp.exists():
                try:
                    current_size = lp.stat().st_size
                    last_pos = self._log_positions.get(aid, 0)
                    if current_size > last_pos:
                        with lp.open("r", encoding="utf-8", errors="replace") as _f:
                            _f.seek(last_pos)
                            new_content = _f.read(current_size - last_pos)
                        self._log_positions[aid] = current_size
                        if "AllTasksCompleted" in new_content:
                            self._finish_completed(aid, ac, p)
                            return
                        # Battle FAILURE downgrade: agent battle lost repeatedly
                        # (FightMissionFailed / PrtsErrorConfirm in asst.log) —
                        # account can't clear this stage (wrong team/lv). With a
                        # fallback chain, drop to the next stage instead of
                        # retrying the unwinnable one forever (b-2 hit 63 retries).
                        if (ac.get("_stage_fallback") and aid not in self._stopping
                                and not self._downgrading.get(aid)
                                and ("FightMissionFailed" in new_content
                                     or "PrtsErrorConfirm" in new_content)
                                and "FightBegin" in new_content):
                            # FightBegin 确认真的在 Fight 阶段 — PrtsErrorConfirm 在
                            # StartUp 阶段也识别（StartUp@PrtsErrorConfirm），游戏未
                            # 启动/加载界面误匹配会导致"关卡刷不了"误降级（连关卡都
                            # 没进）→ WM_CLOSE → "确认退出"弹窗。只在 Fight 阶段降级。
                            _fc = new_content.count("FightMissionFailed") + new_content.count("PrtsErrorConfirm")
                            if _fc >= 2:  # 2+ failure frames in one read = real loss
                                self.emit_log(f"⬇ {ac.get('name', aid)} 作战失败，触发降级")
                                self._downgrading[aid] = True
                                try:
                                    self._downgrade_stage(aid, ac, p)
                                finally:
                                    self._downgrading.pop(aid, None)
                                return
                        # Per-account stage downgrade: Fight task failed (nav/unlock)
                        # while a fallback chain exists → record + retry next stage.
                        if ac.get("_stage_fallback") and aid not in self._stopping and "TaskChainCompleted" in new_content:
                            self._maybe_downgrade(aid, p)
                            if self._downgrading.get(aid):
                                return
                except: pass
            # Tail-scan completion check: the incremental read above misses
            # AllTasksCompleted when asst.log stops growing right after it is
            # written (current_size == last_pos → never re-read). Scan the file
            # tail every tick as a reliable fallback.
            if ac and not ac.get("_connect_only"):
                try:
                    lp = Path(p._inst_path) / "debug" / "asst.log"
                    if lp.exists():
                        _sz = lp.stat().st_size
                        if _sz > 0:
                            with lp.open("r", encoding="utf-8", errors="replace") as _f:
                                _f.seek(max(0, _sz - 20000))
                                _tail = _f.read()
                            if "AllTasksCompleted" in _tail:
                                self.emit_log(f"[完成后] {ac.get('name', aid)} 任务全部完成（尾部检测）")
                                self._log.info(f"[完成后] {ac.get('name', aid)} 尾部检测到完成")
                                tasks, sanity, drops = self._parse_log(aid)
                                self._cleanup(aid, 0, tasks, sanity, drops)
                                try: p.terminate(); p.wait(3)
                                except: pass
                                return
                except Exception:
                    pass
            # Stuck detection
            ac = self._active.get(aid)
            if ac:
                # PRTS (agent battle) stall: MAA clicks "开始行动", game enters
                # agent-battle mode (Fight@PRTS1) but the battle NEVER settles —
                # sanity stays flat (no real cost) while asst.log keeps writing
                # (screencap noise) so the silence-based check can't catch it.
                # Mark first sighting, kill after `prts_stall_min` (default 10).
                _prts_min = int(ac.get("prts_stall_min", 10) or 10)
                try:
                    _p = Path(getattr(p, '_inst_path', "")) / "debug" / "asst.log"
                    if _p.exists():
                        with _p.open("r", encoding="utf-8", errors="replace") as _f:
                            _tail = _f.read()[-200000:]
                        if "Fight@PRTS1" in _tail and not ac.get("_connect_only"):
                            # 卡死 = PRTS1 持续 + asst.log 停滞（画面无进展）。
                            # 正常长刷关（代理作战 10+ 分钟）也有 PRTS1 但日志
                            # 持续写入 — 只看关键词会误杀（2026-08-11 日-2/
                            # 日-3: 13 分钟一轮被误判卡死 → 反复重启永远跑不完）。
                            try:
                                _stall = time.time() - _p.stat().st_mtime > 60
                            except Exception:
                                _stall = False
                            if _stall:
                                _st = ac.setdefault("_prts_stall_since", time.time())
                                if time.time() - _st > _prts_min * 60:
                                    self.emit_log(f"⚠ {ac.get('name', aid)} 代理作战卡死（PRTS1 超 {_prts_min} 分钟且日志停滞），终止")
                                    self._log.warn(f"[代理卡死] {ac.get('name', aid)} PRTS1 卡死")
                                    ac.pop("_prts_stall_since", None)
                                    try: p.terminate(); p.wait(5)
                                    except: pass
                                    tasks, sanity, drops = self._parse_log(aid)
                                    self._cleanup(aid, -9, tasks, sanity, drops)
                                    return
                            else:
                                ac.pop("_prts_stall_since", None)  # 日志活跃 = 正常刷关，重置
                        else:
                            ac.pop("_prts_stall_since", None)
                except Exception:
                    pass
                timeout = ac.get("stuck_timeout_min", 0)
                if timeout > 0 and aid in self._task_start_times:
                    elapsed = time.time() - self._task_start_times[aid]
                    if elapsed > timeout * 60:
                        self.emit_log(f"⚠ {ac.get('name', aid)} 任务卡死 ({int(elapsed/60)}分钟)")
                        self._task_start_times.pop(aid, None)
                        try: p.terminate(); p.wait(5)
                        except: pass
                        tasks, sanity, drops = self._parse_log(aid)
                        self._cleanup(aid, -9, tasks, sanity, drops)
                        return
                started = self._start_times.get(aid, 0)
                if started and time.time() - started > 3600:
                    self.emit_log(f"⏱ {ac.get('name', aid)} 运行超时")
                    try: p.terminate(); p.wait(5)
                    except: pass
                    tasks, sanity, drops = self._parse_log(aid)
                    self._cleanup(aid, -3, tasks, sanity, drops)
                    return
            # Error threshold: ≥3 consecutive errors → kill MAA + restart emulator
            if ac and aid not in self._stopping:
                err_count = ac.get("_err_count", 0)
                if err_count >= 3:
                    self.emit_log(f"[错误] {ac.get('name', aid)} 连续 {err_count} 次异常，清理")
                    emu_idx = ac.get("emu_instance_index", "")
                    if emu_idx:
                        cli = find_mumu_cli()
                        if cli is None and ac.get("adb_path"):
                            try:
                                cand = Path(ac["adb_path"]).parent / "MuMuManager.exe"
                                if cand.exists():
                                    cli = str(cand)
                            except Exception:
                                pass
                        if cli:
                            idx_flag = "-v" if "MuMuManager" in cli else "--vmindex"
                            # 统一优雅关闭（直接 shutdown 留 VMM 残留）
                            try:
                                from services.launch_queue import graceful_emu_shutdown
                                graceful_emu_shutdown(cli, emu_idx, ac.get("adb_path", ""), ac.get("adb_address", ""))
                            except Exception:
                                subprocess.run([cli, "control", idx_flag, str(emu_idx), "shutdown"],
                                              timeout=10, capture_output=True, creationflags=CF)
                    try: p.terminate(); p.wait(3)
                    except: pass
                    self._cleanup(aid, -8, [])
                    return
            # Stuck at startup / idle hang cleanup:
            # - daily/schedule: no tasks 120s after launch → stuck, kill
            # - daily/schedule: tasks done but MAA hangs (asst.log silent 5min) → release instance
            # - connect mode (no tasks): hang is NORMAL (manual use) → never kill
            ac = self._active.get(aid)
            if ac:
                started = self._start_times.get(aid, 0)
                is_connect = bool(ac.get("_connect_only"))
                # Cold-start grace: emulator + game can take 3-5min to load
                # (especially after MuMu restart). Only kill when MAA is
                # actually idle — asst.log silent for 60s means stuck.
                _no_task_timeout = 300 if not is_connect else 99999
                if started and time.time() - started > _no_task_timeout and not is_connect:
                    tasks, _, _ = self._parse_log(aid)
                    if not tasks:
                        # MAA still actively probing (asst.log fresh) → wait more
                        _inst_path = getattr(p, '_inst_path', None)
                        _fresh = False
                        if _inst_path:
                            try:
                                _fresh = time.time() - (Path(_inst_path) / "debug" / "asst.log").stat().st_mtime < 60
                            except Exception:
                                pass
                        if not _fresh:
                            self.emit_log(f"⏱ {ac.get('name', aid)} 启动后无任务 ({int(time.time()-started)}s)，清理实例")
                            self._log.warn(f"[卡死] {ac.get('name', aid)} 启动 {int(time.time()-started)}s 无任务")
                            try: p.terminate(); p.wait(3)
                            except: pass
                            self._cleanup(aid, -3, [])
                            return
                    # Tasks exist → MAA likely finished and is idling. asst.log keeps
                    # getting written while tasks run; 5min silence = done/hung.
                    _inst_path = getattr(p, '_inst_path', None)
                    _idle = 9999.0
                    if _inst_path:
                        try:
                            _idle = time.time() - (Path(_inst_path) / "debug" / "asst.log").stat().st_mtime
                        except Exception:
                            pass
                    if _idle > 300:
                        self.emit_log(f"⏱ {ac.get('name', aid)} 任务完成挂起 (日志静止 {int(_idle)}s)，清理实例")
                        try: p.terminate(); p.wait(3)
                        except: pass
                        self._cleanup(aid, 0, tasks)
                        return
                    # StartUp stuck: game loading hangs (LoadingIcon loop) — asst.log
                    # keeps being written so the silence check never fires. If StartUp
                    # is still "运行中" 10min after launch → kill + release instance.
                    startup = next((t for t in tasks if t.get("TaskType", "").lower() == "startup"), None)
                    if startup and startup.get("status") == "运行中" and time.time() - started > 600:
                        self.emit_log(f"⏱ {ac.get('name', aid)} StartUp 卡加载超时 ({int(time.time()-started)}s)，清理实例")
                        self._log.warn(f"[卡死] {ac.get('name', aid)} StartUp 卡加载 {int(time.time()-started)}s")
                        try: p.terminate(); p.wait(3)
                        except: pass
                        self._cleanup(aid, -3, tasks)
                        return
            # ADB keepalive — log only, don't kill MAA。
            # ⚠️ 原实现每 5s 逐账号 `adb -s <addr> shell echo ping` — adb server
            # 是串行的，10 台并发 ping + MAA 自身操作 → server 排队超时（3s）
            # → 误报"ADB 失联" + connect 重连干扰 MAA 连接 → MAA 连接抖动
            # 退出（2026-08-11 B 服轮: 10 台同时失联误报 + 多个 ADB 关机）。
            # 改: 每 30s 一次 `adb devices` 批量检查（1 次调用查全部设备），
            # 不在设备列表才计数/重连。
            ac = self._active.get(aid)
            if ac and time.time() - self._adb_check_ts > 30:
                self._adb_check_ts = time.time()
                try:
                    from infrastructure.task_constants import find_adb
                    adb_path = find_adb() or "adb"
                except Exception:
                    adb_path = "adb"
                try:
                    r = subprocess.run([adb_path, "devices"], capture_output=True,
                                       timeout=5, creationflags=CF, encoding="utf-8", errors="replace")
                    known = {l.split()[0] for l in r.stdout.splitlines()[1:]
                             if l.strip() and "device" in l and "offline" not in l}
                except Exception:
                    known = set()
                for _aid in list(self._active.keys()):
                    _ac = self._active.get(_aid)
                    _addr = (_ac or {}).get("adb_address", "")
                    if not _addr:
                        continue
                    if _addr in known:
                        self._adb_fail_count.pop(_aid, None)
                    else:
                        fail = self._adb_fail_count.get(_aid, 0) + 1
                        self._adb_fail_count[_aid] = fail
                        subprocess.run([adb_path, "connect", _addr], capture_output=True,
                                       timeout=3, creationflags=CF)
                        if fail >= 3 and fail % 3 == 0:
                            self.emit_log(f"[ADB] {(_ac or {}).get('name', _aid)} ADB 失联第 {fail} 次")

    def _update_status(self, aid: str) -> None:
        """Read asst.log tail for current task name."""
        ac = self._active.get(aid)
        if not ac:
            return
        p = self._procs.get(aid)
        inst_path = getattr(p, '_inst_path', None) if p and not isinstance(p, str) else None
        if not inst_path:
            return
        lp = Path(inst_path) / "debug" / "asst.log"
        name = ac.get("name", aid)
        if lp and lp.exists():
            try:
                fh = self._log_handles.get(aid)
                if fh is None or fh.closed:
                    fh = lp.open("rb")
                    self._log_handles[aid] = fh
                fh.seek(0, 2)
                size = fh.tell()
                read_size = min(400, size)
                fh.seek(size - read_size)
                tail = fh.read(read_size).decode("utf-8", errors="replace")
                for line in tail.split("\n"):
                    if "append_callback" in line and "SubTaskStart" in line:
                        jm = re.search(r"\{.*\}", line)
                        if jm:
                            try:
                                data = json.loads(jm.group(0))
                                tc = data.get("taskchain", "")
                                st_map = {"StartUp": "唤醒", "Fight": "刷关", "Recruit": "公招", "Infrast": "基建", "Mall": "信用", "Award": "奖励", "Roguelike": "肉鸽", "Reclamation": "生息"}
                                if tc in st_map:
                                    task_name = st_map[tc]
                                    prev = ac.get("_last_task", "")
                                    if task_name != prev:
                                        ac["_last_task"] = task_name
                                        self.emit_log(f"[MAA] {name} 当前任务: {task_name}")
                                    self._task_start_times[aid] = time.time()
                                    return
                            except: pass
                    elif "[ERR]" in line:
                        err = line.split("[ERR]")[-1].strip()[:80]
                        self.emit_log(f"[MAA] {name} 错误: {err}")
                        ac["_err_count"] = ac.get("_err_count", 0) + 1
                        if "运行终止" in err or ("重启" in err and "安卓" in err):
                            p = self._procs.get(aid)
                            if p and not isinstance(p, str):
                                try: p.kill()
                                except: pass
                            return
            except: pass

    def is_task_running(self, aid: str) -> bool:
        """MAA 当前是否有任务在运行 — asst.log 生命周期推断（2026-08-11 用户:
        进程活着 ≠ 任务在跑；MAA 完成/点停止后停留界面，按钮状态才是真值。
        日志推断：最后一个 TaskStart 之后有无闭合的 TaskChainCompleted/
        AllTasksCompleted — 有未闭合 = 任务运行中。零依赖，比 UI Automation 可靠）。"""
        p = self._procs.get(aid)
        if not isinstance(p, subprocess.Popen) or p.poll() is not None:
            return False
        inst = getattr(p, "_inst_path", None)
        if not inst:
            return False
        return self._infer_task_running(inst)

    @staticmethod
    def _infer_task_running(inst_path: str) -> bool:
        """从实例目录推断任务是否运行中（is_task_running 与手动 MAA 扫描共用）。"""
        try:
            al = Path(inst_path) / "debug" / "asst.log"
            if not al.exists():
                return False
            tail = al.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
            last_start = -1
            last_end = -1
            for i, ln in enumerate(tail):
                if "TaskStart" in ln:
                    last_start = i
                elif "TaskChainCompleted" in ln or "AllTasksCompleted" in ln:
                    last_end = i
            return last_start > last_end
        except Exception:
            return False

    def scan_maa_instances(self) -> list[dict]:
        """扫描所有运行中的 MAA.exe（含手动启动的）— 检测每个是否正在执行
        任务（2026-08-11 用户: 手动启动的 MAA 也要能检测运行状态）。
        系统管理的走 _procs；手动开的通过进程路径找实例目录 → asst.log 推断。"""
        results = []
        managed_pids = set()
        # 1) 系统管理的
        for aid, p in list(self._procs.items()):
            if isinstance(p, subprocess.Popen) and p.poll() is None:
                managed_pids.add(str(p.pid))
                inst = getattr(p, "_inst_path", None) or ""
                results.append({
                    "pid": p.pid, "aid": aid[:8], "managed": True,
                    "running": self._infer_task_running(inst),
                    "task": (self._active.get(aid) or {}).get("_last_task", ""),
                })
        # 2) 手动开的（tasklist 找 MAA.exe，不在系统管理的）
        try:
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq MAA.exe", "/FO", "CSV"],
                               capture_output=True, text=True, timeout=10,
                               creationflags=CF, errors="replace")
            pids = []
            for ln in r.stdout.splitlines()[1:]:
                parts = ln.split('","')
                if len(parts) > 1:
                    pid = parts[1].strip('"')
                    if pid.isdigit():
                        pids.append(pid)
            for pid in pids:
                if pid in managed_pids:
                    continue
                inst = self._find_inst_by_pid(pid)
                results.append({
                    "pid": pid, "aid": f"手动:{pid}", "managed": False,
                    "running": self._infer_task_running(inst) if inst else False,
                    "task": "",
                })
        except Exception:
            pass
        return results

    def _find_inst_by_pid(self, pid: str) -> str:
        """按进程 PID 找 MAA 实例目录（进程路径 → instances/N）。"""
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).Path"],
                               capture_output=True, text=True, timeout=10,
                               creationflags=CF, errors="replace")
            path = (r.stdout or "").strip()
            if path and "instances" in path:
                return str(Path(path).parent)
        except Exception:
            pass
        return ""

    def _parse_log(self, aid: str) -> tuple[list[dict], dict | None, dict | None]:
        """Parse asst.log for task results, sanity, drops."""
        ac = self._active.get(aid)
        if not ac:
            return [], None, None
        p = self._procs.get(aid)
        inst_path = getattr(p, '_inst_path', None) if p and not isinstance(p, str) else None
        if inst_path and self.ctx.logs:
            w = {"path": str(Path(inst_path) / "MAA.exe")}
            return self.ctx.logs.parse_log(w)
        progs = self._progs.get(aid, [])
        if not progs or not self.ctx.logs:
            return [], None, None
        return self.ctx.logs.parse_log(progs[0])

    # ── Completion ──

    def _record_stage_check(self, aid: str, stage: str, reason: str) -> None:
        """Persist a per-account stage availability check to models/stage_checks.json."""
        try:
            import json as _json
            from infrastructure.utils import atomic_write
            p = _STAGE_CHECKS_PATH
            d = {}
            if p.exists():
                try:
                    d = _json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    d = {}
            entries = d.get(aid, [])
            entries.insert(0, {"stage": stage, "reason": reason, "ts": time.time()})
            d[aid] = entries[:20]
            atomic_write(p, _json.dumps(d, ensure_ascii=False))
        except Exception:
            pass

    def _downgrade_stage(self, aid: str, ac: dict, p) -> bool:
        """Fight failed (unlocked/navigation) → record check, retry next stage.
        Returns True if a downgrade/restart was issued."""
        fallback = ac.get("_stage_fallback") or []
        cur_stage = ac.get("_stage_current", "")
        if not fallback:
            return False
        next_stage = fallback[0]
        ac["_stage_fallback"] = fallback[1:]
        if cur_stage:
            self._record_stage_check(aid, cur_stage, "nav_fail")
        name = ac.get("name", aid)
        self.emit_log(f"⬇ {name} 关卡「{cur_stage or '?'}」不可刷，降级到「{next_stage}」")
        self._log.info(f"[降级] {name}: {cur_stage} → {next_stage}")
        # 记录到 AccountState（降级原因可见）
        try:
            from models.account_state import AccountState
            AccountState(aid).on_use(f"降级: {cur_stage or '?'} → {next_stage}")
        except Exception:
            pass
        # 治本: 把刷不了的关卡从 stages 移除并持久化 —
        # 否则每次重启注入又重新包含它，每次都触发降级（每次降级
        # 优雅关闭 MAA → WM_CLOSE → "确认退出"弹窗卡死循环）。
        try:
            _stages = ac.get("stages") or []
            if cur_stage and cur_stage in _stages:
                _stages.remove(cur_stage)
                ac["stages"] = _stages
                try:
                    from models.config_manager import save_config
                    save_config(self.ctx.config)
                except Exception:
                    pass
                self._log.info(f"[降级] {name} 已从 stages 移除 {cur_stage}（防循环）")
        except Exception:
            pass
        inst_dir = getattr(p, "_inst_path", None)
        if not inst_dir:
            return False
        try:
            # Supersede the old process FIRST: while we close it, its _wait_exit
            # thread wakes up and calls _on_process_exit. If _procs[aid] still
            # points at the old Popen, that callback cleans up (pops _active and
            # the queue mark) while the NEW process is about to start — leaving
            # an orphan MAA and a lost queue slot. Replacing the entry with a
            # string placeholder makes `_procs.get(aid) is not p` true, so the
            # stale callback returns early.
            self._procs[aid] = inst_dir
            # Graceful close (WM_CLOSE → MAA releases ADB/minitouch) — hard
            # terminate leaves minitouch residue that crashes the emulator
            # (MuMu "运行异常" popup). Fall back to terminate only if it hangs.
            self._graceful_close(p)
            # 优雅关闭触发 MAA "确认退出"弹窗（任务在跑）— 自动点"是"让它退出
            for _ in range(4):
                try:
                    _close_mumu_popups()
                except Exception:
                    pass
                if p.poll() is not None:
                    break
                time.sleep(1.5)
            p.wait(5)
            if p.poll() is None:
                p.terminate()
                p.wait(3)
            if p.poll() is None:
                # Still alive — hard kill. A leftover MAA becomes an orphan
                # holding the instance slot (9 instances for 8 parallel).
                subprocess.run(["taskkill", "/F", "/PID", str(p.pid)],
                               capture_output=True, timeout=5, creationflags=CF)
        except Exception:
            pass
        # Re-inject with the next stage — but NOT in-place spawn. The old MAA's
        # Shutdown sequence takes 5-7s (confirm dialog → release ADB/minitouch);
        # spawning the new MAA on the SAME instance while the old one is still
        # exiting triggers MAA's single-instance guard ("Existing instance
        # window activated by a secondary launch") → new MAA exits rc=0 in 5s
        # (→ -12 → previously ADB-shutdown the emulator, mass emulator closes,
        # 2026-08-10). Release the slot and re-enqueue — the queue's full
        # startup path (instance check + stale cleanup + 20s interval) avoids
        # the race entirely.
        ac["_stage_override"] = next_stage
        try:
            self._procs.pop(aid, None)
            self._active.pop(aid, None)
            self._release_emu_mark(aid)
            mw = getattr(self.ctx, "_mw", None)
            lq = getattr(mw, "launch_queue", None)
            if lq is not None:
                lq.enqueue(aid, "auto", priority=0, slot=ac.get("_slot", ""))
                lq.tick()
            self.emit_log(f"↩ {name} 降级「{next_stage}」完成，回队列重排")
            return True
        except Exception as e:
            self._log.error(f"[降级] {name} 重排失败: {e}")
            return False

    def _maybe_downgrade(self, aid: str, p) -> None:
        """Check if the Fight chain failed and a fallback stage exists.
        Low sanity stops the downgrade chain (all stages unplayable)."""
        if self._downgrading.get(aid):
            return
        ac = self._active.get(aid)
        if not ac or ac.get("_connect_only"):
            return
        tasks, sanity, _ = self._parse_log(aid)
        # 登录守卫: StartUp 未完成（游戏都没进）→ 任务"失败"是环境问题
        # （登录卡住/断连/被杀），不是关卡问题 — 连关卡都没进怎么可能知道
        # 它刷不了？只有登录成功（StartUp 完成）才能判定关卡能否刷。
        # （2026-08-10 用户指出: 没登录就降级是误判 — 与昨晚 PrtsErrorConfirm
        # 在 StartUp 阶段误匹配同一类问题）
        startup = next((t for t in tasks if t.get("TaskType", "").lower() == "startup"), None)
        if not startup or startup.get("status") != "完成":
            return
        fight = next((t for t in tasks if t.get("TaskType", "").lower() == "fight"), None)
        if fight:
            if fight.get("status") != "失败":
                return
            if fight.get("fight_finished"):
                return  # finished=true = 次数刷完(正常), 非失败
        else:
            # Fight never started (invalid stage → AsstAppendTask rejected) but the
            # queue moved on to later chains → treat as stage failure → downgrade.
            later = [t for t in tasks if t.get("TaskType", "").lower() in ("infrast", "recruit", "mall", "award")]
            if not later:
                return
            cur_stage = ac.get("_stage_current", "")
            if not cur_stage:
                return
        # Sanity gate: if sanity is very low, all stages are unplayable → stop
        cur = (sanity or {}).get("current")
        if cur is not None and cur < 20:
            ac["_stage_fallback"] = []
            self.emit_log(f"⏱ {ac.get('name', aid)} 理智不足({cur})，停止关卡降级")
            return
        self._downgrading[aid] = True
        try:
            self._downgrade_stage(aid, ac, p)
        finally:
            self._downgrading.pop(aid, None)

    def _archive_maa_logs(self, aid: str, inst_path: str | None) -> None:
        """Archive MAA asst.log + gui.log after a run ends (any exit path).
        Kept under logs/maa_history/{aid}/ — asst.log is wiped on every launch,
        so without archiving past runs are unrecoverable."""
        if not inst_path:
            return
        try:
            from datetime import datetime as _dt
            hist = Path(__file__).parent.parent / "logs" / "maa_history" / str(aid)
            hist.mkdir(parents=True, exist_ok=True)
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            dbg = Path(inst_path) / "debug"
            saved = 0
            for src_name, dst_name in (("asst.log", "asst"), ("gui.log", "gui")):
                src = dbg / src_name
                if src.exists() and src.stat().st_size > 0:
                    dst = hist / f"{ts}_{dst_name}.log"
                    try:
                        import shutil as _su
                        _su.copy2(str(src), str(dst))
                        saved += 1
                    except Exception:
                        pass
            if saved:
                self._log.info(f"[归档] {aid} MAA 日志 → logs/maa_history/{aid}/{ts}_*.log")
            # 运行级样本（2026-08-11 用户）: 归档同时生成结构化摘要 —
            # 该次运行的关键事件/结果，供训练与"日志模式→动作"规则提炼。
            try:
                import json as _j2
                _sdir = Path(__file__).parent.parent / "logs" / "log_samples"
                _sdir.mkdir(parents=True, exist_ok=True)
                _summary = {"ts": ts, "aid": aid[:8], "events": []}
                _al = dbg / "asst.log"
                if _al.exists():
                    for _ln in _al.read_text(encoding="utf-8", errors="replace").splitlines():
                        _t = _ln.strip()
                        if any(k in _t for k in ("AllTasksCompleted", "TaskChainCompleted",
                                                 "FightMissionFailed", "PrtsErrorConfirm",
                                                 "ExceededLimit", "FightBegin", "SanityBeforeStage")):
                            _summary["events"].append(_t[:300])
                with open(_sdir / f"{aid[:8]}.jsonl", "a", encoding="utf-8") as _f:
                    _f.write(_j2.dumps(_summary, ensure_ascii=False) + "\n")
            except Exception:
                pass
            # Retention: keep newest 100 runs per account, drop the oldest
            # （2026-08-11 用户: 收集优先于识别 — 现场原始日志是分析底，
            # 30 次太短会丢现场。磁盘 2TB 足够，延到 100 次）
            runs = sorted(hist.glob("*.log"))
            while len(runs) > 200:  # 100 runs × 2 files
                try:
                    runs[0].unlink()
                except Exception:
                    pass
                runs = runs[1:]
        except Exception as e:
            self._log.warn(f"[归档] {aid} 日志归档失败: {e}")

    def _cleanup(self, aid: str, exit_code: int, tasks: list[dict], sanity: dict | None = None, drops: dict | None = None) -> None:
        self._inst_reserved.pop(aid, None)
        # 停止日志监控线程
        try:
            _w = self._watchers.pop(aid, None)
            if _w:
                _w.stop()
        except Exception:
            pass
        ac = self._active.pop(aid, None)
        old_procs = self._procs.pop(aid, None)
        # inst_dir: Popen 的 _inst_path，或占位符路径本身（_do_launch/降级
        # 把 _procs[aid] 设为 inst_dir 字符串防竞态 — 2026-08-11 僵尸根因：
        # 字符串路径下杀逻辑被 isinstance 检查跳过 → MAA 残留占实例）。
        inst_dir = old_procs if isinstance(old_procs, str) else getattr(old_procs, '_inst_path', None)
        if old_procs:
            self._archive_maa_logs(aid, inst_dir)
            # 确保 MAA 进程真的死了才删 .pid/.meta — 否则僵尸 MAA 占着实例
            # （无 .pid 但进程活着）→ 后续账号分配到该实例 → MAA 启动撞
            # "Existing instance window activated by a secondary launch" →
            # 2 秒退出 → 被"日志停滞"判定关模拟器 → "一出来就被关掉"
            # （2026-08-10 实例 6 连续 12 次 secondary 根因）。
            if isinstance(old_procs, subprocess.Popen):
                try:
                    if old_procs.poll() is None:
                        old_procs.terminate()
                        try: old_procs.wait(3)
                        except: pass
                    if old_procs.poll() is None:
                        subprocess.run(["taskkill", "/F", "/PID", str(old_procs.pid)],
                                       capture_output=True, timeout=5, creationflags=CF)
                        time.sleep(1)
                except Exception:
                    pass
            elif inst_dir:
                # 占位符路径：实例 .pid 里可能有真 MAA 进程（启动后异常 /
                # 降级中断，_procs 未更新为 Popen）— 按 .pid 强杀。
                try:
                    _pf = Path(inst_dir) / ".pid"
                    if _pf.exists():
                        _zpid = int(_pf.read_text(encoding="utf-8").strip().split()[0])
                        _zalive = False
                        try:
                            import psutil as _ps
                            _zpr = _ps.Process(_zpid)
                            _zalive = _zpr.is_running() and ("MAA" in (_zpr.name() or "").upper())
                        except Exception:
                            pass
                        if _zalive:
                            subprocess.run(["taskkill", "/F", "/PID", str(_zpid)],
                                           capture_output=True, timeout=5, creationflags=CF)
                            time.sleep(1)
                except Exception:
                    pass
            # Process is gone — remove .pid/.meta so the instance is cleanly
            # reusable (stale pid risks PID-reuse false positive in
            # _get_free_instance / _launch_for_instance, stale .meta mislabels
            # the instance in dashboard fallbacks).
            if inst_dir:
                try:
                    _ip = Path(inst_dir)
                    (_ip / ".pid").unlink(missing_ok=True)
                    (_ip / ".meta").unlink(missing_ok=True)
                except Exception:
                    pass
        old_progs = self._progs.pop(aid, None)
        fh = self._log_handles.pop(aid, None)
        if fh and not fh.closed:
            try: fh.close()
            except: pass
        started = self._start_times.pop(aid, None)
        duration = int(time.time() - started) if started else 0
        self._stopping.discard(aid)
        self.ctx.proc_status.discard(aid)
        # Release queue mark on ANY exit (crash/kill included) — stale marks
        # blocked relaunches for up to 150s before this fix.
        self._release_emu_mark(aid)

        try:
            from services.log_watcher import record_event
            record_event(aid, "exit", f"exit_code={exit_code} elapsed={duration}s")
        except Exception:
            pass
        name = ac.get("name", aid) if ac else aid
        if ac and ac.get("_connect_only"):
            # Connect-only temp account: release dispatch + remove from connect list
            try:
                from services.dispatch_pool import remove_dispatch
                remove_dispatch(ac.get("_dispatch_connect", ""))
                ac["_dispatch_connect"] = ""
                conn = getattr(getattr(self.ctx, "_mw", None), "connect_accounts", None)
                if conn:
                    try: conn.remove(ac)
                    except ValueError: pass
            except Exception:
                pass
        # Close the account's emulator ONLY on normal completion (exit==0).
        # On abnormal exit (connection lost etc.) the auto-restart re-enqueues
        # the account and needs the emulator still alive — closing it here
        # caused a death loop (close → restart can't connect → fail → close).
        # One-to-one binding means completed accounts leave their emulator
        # running forever otherwise — that's what this closes. Connect-only
        # (manual use) never closes.
        if ac and exit_code == 0 and not ac.get("_connect_only"):
            try:
                emu_idx = ac.get("emu_instance_index", "")
                if emu_idx:
                    cli = find_mumu_cli()
                    if cli is None and ac.get("adb_path"):
                        cand = Path(ac["adb_path"]).parent / "MuMuManager.exe"
                        if cand.exists():
                            cli = str(cand)
                    if cli:
                        idx_flag = "-v" if "MuMuManager" in cli else "--vmindex"
                        self.emit_log(f"[完成] {name} 关闭模拟器 #{emu_idx}")
                        # 统一优雅关闭（adb reboot -p → 等退出 → 兜底）—
                        # 直接 shutdown 留 VMM 残留（用户 2026-08-10）
                        try:
                            from services.launch_queue import graceful_emu_shutdown
                            graceful_emu_shutdown(cli, emu_idx, ac.get("adb_path", ""), ac.get("adb_address", ""))
                        except Exception:
                            subprocess.run([cli, "control", idx_flag, str(emu_idx), "shutdown"],
                                          timeout=10, capture_output=True, creationflags=CF)
            except Exception:
                pass
        if exit_code == 0 and ac:
            self._log.debug(f"[完成] {name} MAA 退出 (exit=0)")
        if ac:
            plan = ac.get("smart_plan", "")
            plan_log = f" 🧠 {plan}" if plan else ""
            self.emit_log(f"[完成] {name} 退出码={exit_code} 耗时={duration//60}m{duration%60}s{plan_log}")
            self._log.info(f"[清理] {name} exit={exit_code}")
            from services.dispatch_pool import remove_dispatch
            remove_dispatch(ac.get("dispatch_id", ""))
            ac["dispatch_id"] = ""
            mode = self.ctx.config.get("schedule_mode", "daily")
            if exit_code == 0:
                if not ac.get("_persist_plan") or mode != "daily":
                    ac["smart_plan"] = ""
                ac.pop("_persist_plan", None)

        if exit_code in (0, -9) and tasks:
            for t in tasks:
                if t.get("status") == "运行中":
                    t["status"] = "完成"

        if exit_code == 0 and tasks:
            failed = [t for t in tasks if t.get("status") == "失败"]
            completed = [t for t in tasks if t.get("status") == "完成"]
            if failed and not completed:
                names = [t["name"] for t in failed]
                exit_code = -11
                self._log.warn(f"[任务失败] {name} 失败: {names}")
                self.emit_log(f"[❌] {name} 任务失败: {','.join(names)}")
        # -12 = startup failure (MAA exited <60s with no tasks — e.g. a
        # SECONDARY launch of the same instance dir: MAA's single-instance
        # guard makes the 2nd process exit rc=0). The emulator may belong to
        # the OTHER account's still-running MAA — shutting it down here would
        # kill the wrong account (2026-08-10: mass emulator closes).
        # -13 = unfinished exit (MAA self-update/interrupt with tasks running) —
        # emulator is fine, retry reuses it.
        is_real_error = exit_code != 0 and exit_code not in (-9, -8, -12, -13) and aid not in self._stopping
        if tasks and any(t.get("status") == "完成" for t in tasks):
            is_real_error = False
        # Connect-only mode: MAA exits on its own (no tasks, GUI idle) — that's
        # normal, never treat as an error or shut down the emulator.
        if ac and ac.get("_connect_only"):
            is_real_error = False

        # Kill residual process — old_procs (was `old_proc`, a NameError that
        # left orphan MAA.exe processes behind).
        if old_procs and not isinstance(old_procs, str) and hasattr(old_procs, 'pid'):
            try:
                if old_procs.poll() is None:
                    old_procs.terminate()
                    try: old_procs.wait(3)
                    except: pass
                if old_procs.poll() is None:
                    subprocess.run(["taskkill","/F","/PID",str(old_procs.pid)], capture_output=True, timeout=3,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
            except: pass

        # Close emulator on error via ADB — but NOT when auto-restart will
        # re-enqueue (the restarted MAA needs the emulator alive; closing it
        # here loops: close → restart can't connect → fail → close again).
        if is_real_error and ac and not ac.get("_auto_restart_count"):
            emu_idx = ac.get("emu_instance_index", "")
            addr = ac.get("adb_address", "")
            adb_path = ac.get("adb_path", "") or "adb"
            if addr:
                try:
                    subprocess.run([adb_path, "-s", addr, "shell", "reboot", "-p"],
                                  capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                    self._log.info(f"[关闭] {name} ADB 关机")
                except: pass

        self.emit_finished((aid, exit_code, tasks))
        if ac:
            ac.pop("_persist_plan", None)

        # Auto-recovery: MAA exited abnormally (externally killed / crash) →
        # re-enqueue to continue the run. Not for user stops (stop() pops _active
        # first, so ac is None here), connect-only, or normal completes.
        if ac and exit_code != 0 and not ac.get("_connect_only") and not self._done_flags.get(aid):
            # _done_flags: 完成收尾（AllTasksCompleted）terminate 的 MAA 会以
            # exit=-3 退出 — 若无此检查会被判"异常退出"→ 自动重启 → 已刷完
            # 的账号重刷一遍（2026-08-11 官-41 刷完 14m49s 又被重入队）
            retries = ac.get("_auto_restart_count", 0) + 1
            if retries <= 3:
                ac["_auto_restart_count"] = retries
                self.emit_log(f"🔄 {name} MAA 异常退出(exit={exit_code})，自动重启 (第{retries}/3 次)")
                self._log.warn(f"[自动重启] {name} exit={exit_code} 第{retries}次")
                try:
                    mw = getattr(self.ctx, "_mw", None)
                    lq = getattr(mw, "launch_queue", None)
                    if lq is not None and ac.get("emu_instance_index"):
                        lq.enqueue(aid, "auto", priority=0, slot=ac.get("_slot", ""))
                        lq.tick()
                except Exception as e:
                    self._log.warn(f"[自动重启] {name} 入队失败: {e}")
            else:
                self.emit_log(f"⛔ {name} MAA 异常退出 {retries} 次，停止自动重启")
                # 反复启动失败（模拟器/游戏起不来）→ 挂起账号，停止队列空转
                # 循环（force 条目失败 → requeue → 又启动 → 又失败，无限循环，
                # 2026-08-10 官-2/emu14 模拟器起不来）。挂起可逆（手动处理
                # 模拟器后解除）。
                try:
                    ac["suspended"] = True
                    from models.config_manager import save_config
                    save_config(self.ctx.config)
                    self.emit_log(f"🚫 {name} 反复启动失败 {retries} 次，已挂起（需人工检查模拟器）")
                except Exception:
                    pass
        elif ac and exit_code == 0:
            ac.pop("_auto_restart_count", None)

        # Save run stats — record EVERY run (success, failure, timeout, startup
        # failure) so daily/weekly/monthly/yearly stats are complete. tasks may
        # be [] on early exits; status comes from exit_code.
        try:
            st = RunStats(aid)
            st.save_run(tasks, sanity, drops,
                        exit_code=exit_code,
                        elapsed=duration,
                        status=("完成" if exit_code == 0 else "失败"))
        except Exception as e:
            self._log.warn(f"[统计] {name} save_run 失败: {e}")
        # Account state — reliable usage/login/completion/sanity record
        try:
            from models.account_state import AccountState
            _st = AccountState(aid)
            _st.on_complete(exit_code, ("完成" if exit_code == 0 else "失败"),
                            sanity, drops)
        except Exception as e:
            self._log.warn(f"[状态] {name} AccountState 失败: {e}")

    def _track_stats(self, ac: dict) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        sd = ac.setdefault("stats", {})
        sd.setdefault(today, {"launches": 0, "total_sec": 0})
        sd[today]["launches"] += 1
        self.ctx.save()
