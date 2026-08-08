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
        marks that block instance reuse and show ghost "running" state."""
        try:
            _pool = Path(__file__).parent / "maa" / "instances"
            if not _pool.exists():
                return
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

    def _auto_derive(self, ac: dict) -> None:
        """Auto-fill runtime fields — with mumu-cli ADB port detection."""
        # ADB port via mumu-cli info first — only when no address is set yet.
        # Re-detection overwrote the correct port from detect_emu_instances
        # (MuMu 12 mumu-cli single-query index mismatch returns wrong ports).
        if ac.get("emu_instance_index") and not ac.get("adb_address"):
            try:
                cli = find_mumu_cli()
                if cli:
                    r = subprocess.run([cli, "info", "--vmindex", str(ac["emu_instance_index"])],
                                      capture_output=True, text=True, timeout=5, creationflags=CF,
                                      encoding="utf-8", errors="replace")
                    if r.returncode == 0:
                        data = json.loads(r.stdout)
                        _adb_port = data.get("adb_port")
                        if _adb_port:
                            ac["adb_address"] = f"127.0.0.1:{_adb_port}"
            except: pass
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

    def _get_free_instance(self) -> tuple[int, str] | None:
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
            pid_file = inst_dir / ".pid"
            running = False
            try:
                pid = int(pid_file.read_text().strip())
                running = _pid_exists(pid)
            except Exception:
                pid_file.unlink(missing_ok=True)
            if not running:
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
            self.emit_log(f"{ac.get('name', aid)} 已在运行中")
            return False

        self._auto_derive(ac)

        if not ac.get("adb_address") and not ac.get("emu_instance_index"):
            self.emit_log(f"{ac.get('name', aid)} 未配置模拟器索引和 ADB，跳过")
            return False
        if not ac.get("adb_path") and not ac.get("_connect_only"):
            self.emit_log(f"{ac.get('name', aid)} 未找到 adb.exe，跳过")
            return False
        inst = self._get_free_instance()
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

        # Re-detect ADB port from mumu-cli before each launch — only if empty.
        # Overwriting an existing address breaks MuMu 12 (single-query index
        # mismatch returns a wrong port, e.g. 16992 instead of 16708).
        # Use detect_emu_instances (--vmindex all) which returns correct ports.
        if emu_idx and not ac.get("adb_address"):
            try:
                from infrastructure.task_constants import detect_emu_instances
                for e in detect_emu_instances():
                    if str(e.get("index", "")) == str(emu_idx) and e.get("adb_port"):
                        ac["adb_address"] = f"127.0.0.1:{e['adb_port']}"
                        break
            except Exception:
                pass

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
                    try:
                        subprocess.run([cli, "control", idx_flag, str(emu_idx), "launch"], creationflags=CF, timeout=15)
                    except Exception as e:
                        self.emit_log(f"启动模拟器失败: {e}")
            # Re-detect ADB port AFTER launching — detect_emu_instances only
            # returns running emulators, so a cold start needs a second pass.
            # Retry loop: right after `launch` the Android guest isn't up yet
            # and port detection returns empty/wrong ports (e.g. 16708 vs real
            # 16768). Keep probing until a valid port appears or timeout.
            if emu_idx and not ac.get("adb_address"):
                from infrastructure.task_constants import detect_emu_instances
                wait = int(ac.get("emu_wait", 60))
                deadline = time.time() + wait
                while time.time() < deadline:
                    try:
                        for e in detect_emu_instances():
                            if str(e.get("index", "")) == str(emu_idx) and e.get("adb_port"):
                                ac["adb_address"] = f"127.0.0.1:{e['adb_port']}"
                                break
                        if ac.get("adb_address"):
                            self.emit_log(f"模拟器 #{emu_idx} ADB 端口 {ac['adb_address']}")
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
                self.emit_log(f"等待 Android 开机完成...")
                while time.time() < deadline:
                    try:
                        r = subprocess.run([adb, "-s", addr, "shell", "getprop", "sys.boot_completed"],
                                          capture_output=True, timeout=5, creationflags=CF,
                                          encoding="utf-8", errors="replace")
                        if r.returncode == 0 and r.stdout.strip() == "1":
                            self.emit_log(f"Android 开机完成")
                            break
                    except: pass
                    time.sleep(2)
                else:
                    self.emit_log(f"警告: Android 开机超时")

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
                    self._log.warning(f"[注入] {ac.get('name', aid)} 清理实例残留 MAA PID={_pid}")
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
            self._spawn_instance(exe, ac, inst_dir)
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
                self._cleanup(aid, 0, [])
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
        try: (Path(inst_dir) / "debug" / "asst.log").write_text("")
        except: pass
        self._log_positions[aid] = 0
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
        self.emit_started(aid)

    def _on_process_exit(self, aid: str, p: subprocess.Popen) -> None:
        rc = p.poll()
        tasks, sanity, drops = self._parse_log(aid) if aid in self._active else ([], None, None)
        # MAA exiting within 60s of launch with no completed tasks = startup
        # failure (emulator didn't come up / ADB lost), NOT a normal "done".
        # Otherwise a failed boot is recorded as a successful run and the
        # emulator gets shut down while the queue moves on.
        started = self._start_times.get(aid, 0)
        if rc == 0 and started and time.time() - started < 60 and not tasks:
            self._log.warning(f"[启动失败] {aid} MAA {int(time.time()-started)}s 退出且无任务，按失败处理")
            rc = -12
        # PostActions=ExitSelf may exit with a non-zero code even on NORMAL
        # completion. If tasks were completed, treat it as success regardless.
        if rc not in (0, None) and any(t.get("status") == "完成" for t in tasks):
            self._log.info(f"[完成后] {aid} MAA 退出(rc={rc}) 但任务已完成，按正常完成处理")
            rc = 0
        self._cleanup(aid, rc or 0, tasks, sanity, drops)

    # ── Monitoring ──

    def check_processes(self) -> None:
        """Monitor all running processes. Called by queue tick every 5s."""
        self._check_resources()
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
            self._log.warning(f"[进程] {aid} MAA 进程已退出 (poll={p.poll()})，清理残留")
            self._on_process_exit(aid, p)
            return
        self._update_status(aid)
        # Process pair health check
        info = self._proc_info.get(aid, {})
        emu = info.get("emu", {})
        emu_pid = emu.get("pid")
        ac = self._active.get(aid)
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
                self._log.warning(f"[进程组] {name} 模拟器进程消失 (PID={emu_pid}) 第{fail}次")
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
                            self.emit_log(f"[完成后] {ac.get('name', aid)} 任务全部完成")
                            tasks, sanity, drops = self._parse_log(aid)
                            self._cleanup(aid, 0, tasks, sanity, drops)
                            try: p.terminate(); p.wait(3)
                            except: pass
                            return
                        # Battle FAILURE downgrade: agent battle lost repeatedly
                        # (FightMissionFailed / PrtsErrorConfirm in asst.log) —
                        # account can't clear this stage (wrong team/lv). With a
                        # fallback chain, drop to the next stage instead of
                        # retrying the unwinnable one forever (b-2 hit 63 retries).
                        if (ac.get("_stage_fallback") and aid not in self._stopping
                                and not self._downgrading.get(aid)
                                and ("FightMissionFailed" in new_content
                                     or "PrtsErrorConfirm" in new_content)):
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
                            _st = ac.setdefault("_prts_stall_since", time.time())
                            if time.time() - _st > _prts_min * 60:
                                self.emit_log(f"⚠ {ac.get('name', aid)} 代理作战卡死（PRTS1 超 {_prts_min} 分钟），终止")
                                self._log.warning(f"[代理卡死] {ac.get('name', aid)} PRTS1 卡死")
                                ac.pop("_prts_stall_since", None)
                                try: p.terminate(); p.wait(5)
                                except: pass
                                tasks, sanity, drops = self._parse_log(aid)
                                self._cleanup(aid, -9, tasks, sanity, drops)
                                return
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
                            self._log.warning(f"[卡死] {ac.get('name', aid)} 启动 {int(time.time()-started)}s 无任务")
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
                        self._log.warning(f"[卡死] {ac.get('name', aid)} StartUp 卡加载 {int(time.time()-started)}s")
                        try: p.terminate(); p.wait(3)
                        except: pass
                        self._cleanup(aid, -3, tasks)
                        return
            # ADB keepalive — log only, don't kill MAA
            ac = self._active.get(aid)
            if ac:
                addr = ac.get("adb_address", "")
                adb_path = ac.get("adb_path", "") or "adb"
                if addr:
                    try:
                        r = subprocess.run([adb_path, "-s", addr, "shell", "echo", "ping"],
                                          capture_output=True, timeout=3, creationflags=CF)
                        if r.returncode == 0:
                            self._adb_fail_count.pop(aid, None)
                        else:
                            fail = self._adb_fail_count.get(aid, 0) + 1
                            self._adb_fail_count[aid] = fail
                            subprocess.run([adb_path, "connect", addr], capture_output=True, timeout=3, creationflags=CF)
                            if fail >= 3 and fail % 3 == 0:
                                self.emit_log(f"[ADB] {ac.get('name', aid)} ADB 失联第 {fail} 次")
                    except: pass

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
        inst_dir = getattr(p, "_inst_path", None)
        if not inst_dir:
            return False
        try:
            # Graceful close (WM_CLOSE → MAA releases ADB/minitouch) — hard
            # terminate leaves minitouch residue that crashes the emulator
            # (MuMu "运行异常" popup). Fall back to terminate only if it hangs.
            self._graceful_close(p)
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
        # Re-inject with the next stage — full daily chain (StartUp + Fight +
        # Infrast/Recruit/Mall/Award), not just Fight. Otherwise a downgrade
        # skips the daily tasks entirely ("只有理智没有其他"). Honors skip_daily.
        ac["_stage_override"] = next_stage
        try:
            if ac.get("skip_daily"):
                _full = ["StartUp", "Award"]
            else:
                _full = ["StartUp", "Fight", "Infrast", "Recruit", "Mall", "Award"]
            self.ctx.cfg.inject_smart(_full, ac, str(Path(inst_dir) / "config"))
            self._spawn_instance(Path(inst_dir) / "MAA.exe", ac, inst_dir)
            return True
        except Exception as e:
            self._log.error(f"[降级] {name} 重注入失败: {e}")
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
            # Retention: keep newest 30 runs per account, drop the oldest
            runs = sorted(hist.glob("*.log"))
            while len(runs) > 60:  # 30 runs × 2 files
                try:
                    runs[0].unlink()
                except Exception:
                    pass
                runs = runs[1:]
        except Exception as e:
            self._log.warning(f"[归档] {aid} 日志归档失败: {e}")

    def _cleanup(self, aid: str, exit_code: int, tasks: list[dict], sanity: dict | None = None, drops: dict | None = None) -> None:
        ac = self._active.pop(aid, None)
        old_procs = self._procs.pop(aid, None)
        if old_procs and not isinstance(old_procs, str):
            self._archive_maa_logs(aid, getattr(old_procs, '_inst_path', None))
            # Process is gone — remove .pid/.meta so the instance is cleanly
            # reusable (stale pid risks PID-reuse false positive in
            # _get_free_instance / _launch_for_instance, stale .meta mislabels
            # the instance in dashboard fallbacks).
            try:
                _ip = Path(getattr(old_procs, '_inst_path'))
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
                self._log.warning(f"[任务失败] {name} 失败: {names}")
                self.emit_log(f"[❌] {name} 任务失败: {','.join(names)}")
        is_real_error = exit_code != 0 and exit_code not in (-9, -8) and aid not in self._stopping
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
        if ac and exit_code != 0 and not ac.get("_connect_only"):
            retries = ac.get("_auto_restart_count", 0) + 1
            if retries <= 3:
                ac["_auto_restart_count"] = retries
                self.emit_log(f"🔄 {name} MAA 异常退出(exit={exit_code})，自动重启 (第{retries}/3 次)")
                self._log.warning(f"[自动重启] {name} exit={exit_code} 第{retries}次")
                try:
                    mw = getattr(self.ctx, "_mw", None)
                    lq = getattr(mw, "launch_queue", None)
                    if lq is not None and ac.get("emu_instance_index"):
                        lq.enqueue(aid, "auto", priority=0, slot=ac.get("_slot", ""))
                        lq.tick()
                except Exception as e:
                    self._log.warning(f"[自动重启] {name} 入队失败: {e}")
            else:
                self.emit_log(f"⛔ {name} MAA 异常退出 {retries} 次，停止自动重启")
        elif ac and exit_code == 0:
            ac.pop("_auto_restart_count", None)

        # Save run stats
        if tasks:
            try:
                st = RunStats(aid)
                st.save_run(tasks, sanity, drops)
            except Exception:
                pass

    def _track_stats(self, ac: dict) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        sd = ac.setdefault("stats", {})
        sd.setdefault(today, {"launches": 0, "total_sec": 0})
        sd[today]["launches"] += 1
        self.ctx.save()
