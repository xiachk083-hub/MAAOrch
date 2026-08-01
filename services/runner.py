"""Single-account launch → monitor → complete cycle runner."""
from __future__ import annotations
import time, subprocess, re, json, os
from pathlib import Path
from datetime import datetime
from typing import Any

from collections.abc import Callable

from infrastructure.task_constants import find_mumu_cli, CF
from app.service_context import ServiceContext
from models.stats import RunStats


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
        self._log_handles: dict[str, Any] = {}
        self._adb_fail_count: dict[str, int] = {}
        self._adb_restart_count: dict[str, int] = {}
        self._emu_fail_count: dict[str, int] = {}
        from infrastructure.logger import Logger
        self._log = Logger("runner")

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
        # ADB port via mumu-cli info first
        if ac.get("emu_instance_index"):
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

        if not ac.get("adb_address"):
            if ac.get("emu_instance_index"):
                self.emit_log(f"{ac.get('name', aid)} 模拟器 #{ac['emu_instance_index']} ADB 未就绪，跳过")
            else:
                self.emit_log(f"{ac.get('name', aid)} 未配置模拟器索引，跳过")
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
        """Force-stop a running account's MAA process."""
        self._stopping.add(account_id)
        p = self._procs.pop(account_id, None)
        if p and hasattr(p, 'terminate'):
            try: p.terminate(); p.wait(2)
            except: pass
            try: p.kill()
            except: pass
        self._active.pop(account_id, None)
        self.ctx.proc_status.discard(account_id)

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

        # Re-detect ADB port from mumu-cli before each launch (stale formula ports can be wrong)
        if emu_idx:
            cli = find_mumu_cli()
            if cli:
                try:
                    r = subprocess.run([cli, "info", "--vmindex", str(emu_idx)],
                                      capture_output=True, text=True, timeout=5, creationflags=CF,
                                      encoding="utf-8", errors="replace")
                    if r.returncode == 0:
                        data = json.loads(r.stdout)
                        port = data.get("adb_port")
                        if port:
                            ac["adb_address"] = f"127.0.0.1:{port}"
                except:
                    pass  # fallback to existing address (formula or previous detection)

        # Launch emulator
        if emu_idx:
            cli = find_mumu_cli()
            if cli:
                already_running = False
                try:
                    import json as _json
                    r = subprocess.run([cli, "info", "--vmindex", str(emu_idx)],
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
                        subprocess.run([cli, "control", "--vmindex", str(emu_idx), "launch"], creationflags=CF, timeout=15)
                    except Exception as e:
                        self.emit_log(f"启动模拟器失败: {e}")
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

    def _spawn_instance(self, exe: Path, ac: dict, inst_dir: str) -> None:
        aid = ac["id"]
        pid_file = Path(inst_dir) / ".pid"
        try: (Path(inst_dir) / "debug" / "asst.log").write_text("")
        except: pass
        self._log_positions[aid] = 0
        p = subprocess.Popen([str(exe)], shell=False)
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
            return  # handled by _wait_exit
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
                if cli and emu_idx:
                    self.emit_log(f"[进程组] {name} 重启模拟器 #{emu_idx}")
                    subprocess.run([cli, "control", "--vmindex", str(emu_idx), "shutdown"],
                                  timeout=10, capture_output=True, creationflags=CF)
                    subprocess.run([cli, "control", "--vmindex", str(emu_idx), "launch"],
                                  timeout=10, capture_output=True, creationflags=CF)
                return
        # Task completion detection
        ac = self._active.get(aid)
        if ac and hasattr(p, '_inst_path'):
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
                except: pass
            # Stuck detection
            ac = self._active.get(aid)
            if ac:
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
                        if cli:
                            subprocess.run([cli, "control", "--vmindex", str(emu_idx), "shutdown"],
                                          timeout=10, capture_output=True, creationflags=CF)
                    try: p.terminate(); p.wait(3)
                    except: pass
                    self._cleanup(aid, -8, [])
                    return
            # Stuck at startup: MAA running but no tasks logged for 120s
            ac = self._active.get(aid)
            if ac:
                started = self._start_times.get(aid, 0)
                if started and time.time() - started > 120:
                    tasks, _, _ = self._parse_log(aid)
                    if not tasks:
                        self.emit_log(f"⏱ {ac.get('name', aid)} 启动后无任务 ({int(time.time()-started)}s)，清理实例")
                        self._log.warning(f"[卡死] {ac.get('name', aid)} 启动 {int(time.time()-started)}s 无任务")
                        try: p.terminate(); p.wait(3)
                        except: pass
                        self._cleanup(aid, -3, [])
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

    def _cleanup(self, aid: str, exit_code: int, tasks: list[dict], sanity: dict | None = None, drops: dict | None = None) -> None:
        ac = self._active.pop(aid, None)
        old_progs = self._progs.pop(aid, None)
        old_proc = self._procs.pop(aid, None)
        fh = self._log_handles.pop(aid, None)
        if fh and not fh.closed:
            try: fh.close()
            except: pass
        started = self._start_times.pop(aid, None)
        duration = int(time.time() - started) if started else 0
        self._stopping.discard(aid)
        self.ctx.proc_status.discard(aid)

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

        # Kill residual process
        if old_proc and hasattr(old_proc, 'pid'):
            try:
                subprocess.run(["taskkill","/F","/PID",str(old_proc.pid)], capture_output=True, timeout=3,
                              creationflags=subprocess.CREATE_NO_WINDOW)
            except: pass

        # Close emulator on error via ADB
        if is_real_error and ac:
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
