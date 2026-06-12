"""Single-account launch → monitor → complete cycle runner."""
from __future__ import annotations
import time, subprocess, re, json, os, shutil
from pathlib import Path
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QDialog

from infrastructure.task_constants import find_mumu_cli, CF
from app.service_context import ServiceContext
from models.stats import RunStats


class AccountRunner(QObject):
    """Encapsulates single-account launch → monitor → complete lifecycle.

    Signals are the public API — UI / scheduler listens to these,
    never touches internal process tracking directly."""

    log_msg = Signal(str)
    status_msg = Signal(str)
    account_started = Signal(str)          # account_id
    account_finished = Signal(tuple)  # (account_id, exit_code, tasks)
    account_error = Signal(str, str)       # account_id, error_msg

    # Resource limits (for 16GB systems)
    MAX_TOTAL_MEM_MB = 12288   # 12GB total → pause new launches (leaves 4GB free)
    RESUME_MEM_MB = 6144       # 6GB free → resume launching
    MAX_PROC_MEM_MB = 4096     # 4GB per emulator instance → kill
    MAX_RESTART_PER_MIN = 4    # max restarts per minute per account
    EMU_PROC_NAMES = ("MuMuPlayer.exe", "mumu-vm-", "HD-Player.exe", "Nox.exe", "LDPlayer.exe")

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._active: dict[str, dict] = {}         # account_id → account dict
        self._progs: dict[str, list[dict]] = {}     # account_id → bound programs
        self._procs: dict[str, subprocess.Popen] = {}  # account_id → Popen
        self._start_times: dict[str, float] = {}    # account_id → time.time()
        self._task_start_times: dict[str, float] = {}  # account_id → last task start
        self._stopping: set = set()                 # accounts being stopped
        self._proc_info: dict[str, dict] = {}       # account_id → {mem_mb, cpu_pct, pid}
        self._restart_times: dict[str, list[float]] = {}  # account_id → [timestamps]
        self._overloaded = False                    # true when resource limit hit
        self._log_buffers: dict[str, list[str]] = {}  # account_id → rolling 200 lines
        self._log_positions: dict[str, int] = {}      # account_id → asst.log read position
        self._adb_fail_count: dict[str, int] = {}     # account_id → consecutive ADB ping failures
        self._adb_restart_count: dict[str, int] = {}  # account_id → consecutive ADB-restart cycles
        from infrastructure.logger import Logger
        self._log = Logger("runner")

    # ── Public API ──

    def preflight_check(self, ac: dict, progs: list[dict]) -> list[str]:
        """Run validation checks before launch. Returns list of issues (empty = OK)."""
        issues = []
        name = ac.get("name", ac["id"])

        # ADB address
        if not ac.get("adb_address", "").strip():
            issues.append(f"⚠ {name}: 未填写 ADB 地址")

        # MAA exists
        for w in progs:
            p = w.get("path", "")
            if p and not Path(p).exists():
                issues.append(f"❌ {name}: MAA 程序不存在 — {p}")
            if not p:
                issues.append(f"❌ {name}: 程序路径为空")

        # Emulator instance (only if emu_launch enabled)
        if ac.get("emu_launch") and not ac.get("emu_instance_index", ""):
            issues.append(f"⚠ {name}: 开启了自启模拟器但未选择实例")

        return issues

    def launch(self, row: int) -> bool:
        """Start a single account by index. Returns True if process launched."""
        if row < 0 or row >= len(self.ctx.accounts):
            self.log_msg.emit(f"无效账号索引: {row}")
            return False
        ac = self.ctx.accounts[row]
        aid = ac["id"]
        if aid in self._active:
            self.log_msg.emit(f"{ac.get('name', aid)} 已在运行中")
            return False

        # Validate config before proceeding
        if not ac.get("adb_address") and not ac.get("emu_instance_index"):
            self.log_msg.emit(f"{ac.get('name', aid)} 未配置 ADB 地址和模拟器索引，跳过")
            return False
        if not ac.get("adb_path"):
            from infrastructure.task_constants import find_mumu_cli
            cli = find_mumu_cli()
            if cli:
                from pathlib import Path as _P
                cand = _P(cli).parent / "adb.exe"
                if cand.exists():
                    ac["adb_path"] = str(cand)
            if not ac.get("adb_path"):
                from infrastructure.task_constants import find_adb
                adb_exe = find_adb()
                if adb_exe:
                    ac["adb_path"] = adb_exe
                else:
                    self.log_msg.emit(f"{ac.get('name', aid)} 未找到 adb.exe，跳过")
                    return False

        # Auto-fill ADB address if empty (formula-based, no active detection)
        if not ac.get("adb_address") and ac.get("emu_instance_index"):
            idx = int(ac["emu_instance_index"])
            preset = ac.get("connection_preset", "MuMuPro")
            if preset == "MuMuEmulator12":
                port = 16384 + idx * 32
            elif preset in ("MuMuPro", "MuMu"):
                port = 7555
            elif preset in ("LDPlayer",):
                port = 5555 + idx * 2
            elif preset in ("Nox",):
                port = 62001
            else:
                port = 5555 + idx * 2
            ac["adb_address"] = f"127.0.0.1:{port}"
        ac["touch_mode"] = ac.get("touch_mode", "MiniTouch")

        # Auto-fill ADB path if empty (try mumu-cli sibling first, then find_adb)
        if not ac.get("adb_path"):
            from infrastructure.task_constants import find_mumu_cli
            cli = find_mumu_cli()
            if cli:
                from pathlib import Path as _P
                cand = _P(cli).parent / "adb.exe"
                if cand.exists():
                    ac["adb_path"] = str(cand)
            if not ac.get("adb_path"):
                from infrastructure.task_constants import find_adb
                adb_exe = find_adb()
                if adb_exe:
                    ac["adb_path"] = adb_exe

        # Get free MAA instance
        try:
            from services.instance_pool import MaintService
            # Create temporary MaintService to access instance pool
            inst = self._get_free_instance()
            if not inst:
                self.log_msg.emit(f"{ac.get('name', aid)} 无空闲 MAA 实例")
                return False
        except Exception:
            self.log_msg.emit(f"{ac.get('name', aid)} MAA 实例池不可用")
            return False

        self.log_msg.emit(f"[启动] ADB({ac.get('adb_address','?')}) Emu({ac.get('emu_instance_index','?')}) 实例#{inst[0]}")

        self._active[aid] = ac
        self._progs[aid] = [w for w in self.ctx.warehouse if w.get("account_ref") == aid]
        self.log_msg.emit(f"[启动] {ac.get('name', aid)}")
        self._track_stats(ac)
        self._do_launch(ac, inst)
        return True

    def _get_free_instance(self) -> tuple[int, str] | None:
        """Get a free MAA instance. Checks PID file + already assigned instances."""
        try:
            import psutil as _psutil
            _pid_exists = _psutil.pid_exists
        except ImportError:
            def _pid_exists(pid: int) -> bool:
                try:
                    import subprocess as _sp
                    r = _sp.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                                capture_output=True, text=True, timeout=2,
                                creationflags=_sp.CREATE_NO_WINDOW)
                    return str(pid) in r.stdout
                except Exception:
                    return True  # can't check, assume running
        max_n = self.ctx.config.get("maa_instances", 0)
        pool = Path(__file__).parent / "maa" / "instances"
        for i in range(1, max_n + 1):
            inst_dir = pool / str(i)
            exe = inst_dir / "MAA.exe"
            if not exe.exists():
                continue
            inst_path = str(inst_dir)
            already_used = any(
                (p is not None and getattr(p, "_inst_path", None) == inst_path)
                or (isinstance(p, str) and p == inst_path)
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

    def launch_by_id(self, account_id: str) -> bool:
        """Start an account by ID."""
        for i, a in enumerate(self.ctx.accounts):
            if a["id"] == account_id:
                return self.launch(i)
        return False

    def stop(self, account_id: str) -> None:
        """Force-stop a running account's process."""
        self._stopping.add(account_id)
        p = self._procs.pop(account_id, None)
        if p:
            try:
                p.terminate()
                p.wait(2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            self._cleanup(account_id, -9, [])

    def is_running(self, account_id: str) -> bool:
        return account_id in self._active

    @property
    def active_count(self) -> int:
        return len(self._active)

    def active_ids(self) -> list[str]:
        return list(self._active.keys())

    # ── Internal: dispatch ──

    def _do_launch(self, ac: dict, inst: tuple[int, str]) -> None:
        """Non-blocking launch: runs heavy work in background thread."""
        aid = ac["id"]
        inst_id, inst_dir = inst
        self._procs[aid] = inst_dir  # reserve: store path string as placeholder
        import threading as _th
        _th.Thread(target=self._launch_job, args=(ac, inst), daemon=True).start()

    def _launch_job(self, ac: dict, inst: tuple[int, str]) -> None:
        """Background thread: emulator launch, ADB wait, MAA launch."""
        emu_idx = ac.get("emu_instance_index", "")
        aid = ac["id"]
        inst_id, inst_dir = inst
        self._adb_restart_count.pop(aid, None)  # reset ADB restart count on fresh launch
        _deadline = time.time() + 120  # total timeout: 2 minutes for entire launch

        def _check_deadline(phase: str) -> bool:
            if time.time() > _deadline:
                self.log_msg.emit(f"[超时] {ac.get('name', aid)} {phase} 超时，放弃启动")
                self._cleanup(aid, -2, [])
                return True
            return False

        # Launch emulator if needed
        if ac.get("emu_launch") and emu_idx:
            cli = find_mumu_cli()
            if cli:
                # Check if emulator already running before launching
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
                except Exception:
                    pass
                if already_running:
                    self.log_msg.emit(f"模拟器 #{emu_idx} 已在运行")
                else:
                    self.log_msg.emit(f"启动模拟器 #{emu_idx}")
                    try:
                        subprocess.run([cli, "control", "--vmindex", str(emu_idx), "launch"], creationflags=CF, timeout=15)
                    except Exception as e:
                        self.log_msg.emit(f"启动模拟器失败: {e}")
            # Wait for emulator to be ready: ADB port + Android boot
            adb = ac.get("adb_path", "") or "adb"
            addr = ac.get("adb_address", "")
            if addr:
                wait = int(ac.get("emu_wait", 60))
                deadline = time.time() + wait
                self.log_msg.emit(f"等待模拟器 ADB 连接 (最长 {wait}s)...")
                while time.time() < deadline:
                    try:
                        r = subprocess.run([adb, "connect", addr], capture_output=True, timeout=5, creationflags=CF)
                        if r.returncode == 0 and b"connected" in r.stdout.lower():
                            self.log_msg.emit(f"模拟器 #{emu_idx} ADB 已连接")
                            break
                    except Exception:
                        pass
                    time.sleep(2)
                else:
                    self.log_msg.emit(f"警告: 模拟器 #{emu_idx} ADB 连接超时，继续尝试启动 MAA")
                # Phase 2: wait for Android boot to complete
                self.log_msg.emit(f"等待 Android 开机完成...")
                while time.time() < deadline:
                    try:
                        r = subprocess.run([adb, "-s", addr, "shell", "getprop", "sys.boot_completed"],
                                          capture_output=True, timeout=5, creationflags=CF,
                                          encoding="utf-8", errors="replace")
                        if r.returncode == 0 and r.stdout.strip() == "1":
                            self.log_msg.emit(f"Android 开机完成")
                            break
                    except Exception:
                        pass
                    time.sleep(2)
                else:
                    self.log_msg.emit(f"警告: Android 开机超时，继续尝试启动 MAA")

        if _check_deadline("ADB连接"):
            return

        # ADB server health check (kill zombie servers from other adb.exe)
        adb = ac.get("adb_path", "") or "adb"
        try:
            r = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5, creationflags=CF)
            if "protocol fault" in r.stderr.lower() or "connection reset" in r.stderr.lower():
                self.log_msg.emit("ADB server 异常，重启中...")
                subprocess.run([adb, "kill-server"], capture_output=True, timeout=5, creationflags=CF)
                subprocess.run([adb, "start-server"], capture_output=True, timeout=5, creationflags=CF)
        except:
            pass

        # ADB connection (ensure connected before inject)
        addr = ac.get("adb_address", "")
        if addr:
            for _attempt in range(3):
                try:
                    r = subprocess.run([adb, "connect", addr], capture_output=True, creationflags=CF, timeout=5)
                    if r.returncode == 0:
                        break
                except Exception:
                    pass
                time.sleep(2)
            time.sleep(1)  # stability delay

        if _check_deadline("注入配置"):
            return

        # Inject config and launch
        self._launch_for_instance(ac, inst_dir)

    def _launch_for_instance(self, ac: dict, inst_dir: str) -> None:
        aid = ac["id"]
        smart_enabled = self.ctx.config.get("smart_global", {}).get("enabled", False)
        mode = self.ctx.config.get("schedule_mode", "daily")
        exe = Path(inst_dir) / "MAA.exe"
        config_dir = Path(inst_dir) / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        try:
            if smart_enabled:
                from services.dispatch_pool import get_template, remove_dispatch
                did = ac.get("dispatch_id", "")
                if did:
                    task_list = get_template(did)
                    if task_list is None:
                        task_list = ["StartUp", "Award"]
                elif mode == "roguelike":
                    task_list = ["StartUp", "Roguelike"]
                elif mode == "reclamation":
                    task_list = ["StartUp", "Reclamation"]
                else:
                    from services.smart_scheduler import get_tasks_for_account
                    task_list = get_tasks_for_account(ac, self.ctx.config.get("smart_global", {}))
                plan_txt = ",".join(task_list)
                self.log_msg.emit(f"🧠 智能调度: {plan_txt}")
                self._log.info(f"[注入] {ac.get('name', aid)} smart_plan_raw={ac.get('smart_plan','<空>')} task_list={task_list}")
                self.ctx.cfg.inject_smart(task_list, ac, str(config_dir))
            else:
                self.ctx.cfg.inject_smart(["StartUp", "Award"], ac, str(config_dir))
            self.log_msg.emit(f"注入配置: {config_dir}/")
            self._spawn_instance(exe, ac, inst_dir)
            self._active[aid] = ac  # Mark running
        except Exception as e:
            self.log_msg.emit(f"启动失败: {e}")
            self.account_error.emit(aid, str(e))
            self._cleanup(aid, -1, [])

    def _spawn_instance(self, exe: Path, ac: dict, inst_dir: str) -> None:
        aid = ac["id"]
        pid_file = Path(inst_dir) / ".pid"
        # Clear stale log to prevent AllTasksCompleted false detection on next run
        try: (Path(inst_dir) / "debug" / "asst.log").write_text("")
        except: pass
        self._log_positions[aid] = 0
        p = subprocess.Popen([str(exe)], shell=False)
        p._inst_path = str(Path(inst_dir).resolve())
        self._procs[aid] = p
        self._start_times[aid] = time.time()
        self.ctx.proc_status.add(aid)
        try: pid_file.write_text(str(p.pid))
        except Exception: self.log_msg.emit(f"警告: 无法写入 PID 文件 {pid_file}")
        self.log_msg.emit(f"✓ 启动 MAA PID={p.pid}")
        # Re-apply Infrast Mode after MAA initializes (MAA overwrites gui.new.json on start)
        infra_mode = ac.get("task_settings", {}).get("Infrast", {}).get("mode", "")
        if infra_mode:
            def _rewrite_infra():
                try:
                    import json as _j, time as _t
                    _t.sleep(3)
                    gj = Path(inst_dir) / "config" / "gui.new.json"
                    if not gj.exists(): return
                    d = _j.loads(gj.read_text(encoding="utf-8"))
                    tq = d.get("Configurations", {}).get("Default", {}).get("TaskQueue", [])
                    for item in tq:
                        if item.get("TaskType", "").lower() == "infrast":
                            if item.get("Mode", "") != infra_mode:
                                item["Mode"] = infra_mode
                                gj.write_text(_j.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                                self.log_msg.emit(f"Infrast 模式已设为 {infra_mode}")
                            break
                except Exception:
                    pass
            import threading as _th
            _th.Thread(target=_rewrite_infra, daemon=True).start()
        self.account_started.emit(aid)
        # Notify launch queue that this VM is ready (for serial launch)
        emu_idx = ac.get("emu_instance_index", "")
        if emu_idx and hasattr(self.ctx, '_mw') and hasattr(self.ctx._mw, 'launch_queue'):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.ctx._mw.launch_queue._on_launch_ready(emu_idx))

    def _spawn(self, w: dict, ac: dict) -> None:
        aid = ac["id"]
        args = w.get("args", [])
        lm = w.get("launch_mode", "gui")
        exe = w["path"]

        if w.get("account_ref") and lm == "cli":
            from infrastructure.task_constants import CF
            cwd = w.get("cwd", "") or str(Path(w["path"]).parent)
            env = {k: v for k, v in (w.get("env") or {}).items()} or None
            self._spawn_cli(w, ac, exe, args, cwd, env)
            return

        p = subprocess.Popen([exe] + args, shell=False)
        self._procs[aid] = p
        self._start_times[aid] = time.time()
        self.ctx.proc_status.add(aid)
        self.ctx.proc_status.add(w["id"])     # dashboard uses program ID
        self.ctx.proc_start_times[w["id"]] = time.time()
        self.log_msg.emit(f"✓ 启动 {Path(w['path']).stem} PID={p.pid}")

    def _spawn_cli(self, w: dict, ac: dict, exe: str, args: list, cwd: str | None, env: dict | None) -> None:
        from infrastructure.utils import _find_maa_cli
        from services.update_service import MaacliInstallDialog

        aid = ac["id"]
        cl = _find_maa_cli()
        if not cl:
            d = MaacliInstallDialog(self.ctx._mw)
            d.start(str(Path(__file__).parent / "maa-cli"))
            if d.exec() != QDialog.Accepted:
                return
            cl = _find_maa_cli()
        if not cl:
            self.log_msg.emit("maa-cli 未安装")
            return
        md = Path(w["path"]).parent
        lc = md / Path(cl).name
        if not lc.exists() or lc.stat().st_mtime < Path(cl).stat().st_mtime:
            shutil.copy2(cl, str(lc))
        tn = self.ctx.cfg.gtc(ac, w)
        if tn:
            env = (env or os.environ.copy())
            env["MAA_CONFIG_DIR"] = str(md / "config")
            exe = str(lc)
            args = ["run", tn] + args
            cwd = str(md)
        kwargs = {"shell": False, "cwd": cwd, "env": env, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "creationflags": CF}
        p = subprocess.Popen([exe] + args, **kwargs)
        self._procs[aid] = p
        self._start_times[aid] = time.time()
        self.ctx.proc_status.add(aid)
        self.ctx.proc_status.add(w["id"])
        self.ctx.proc_start_times[w["id"]] = time.time()
        self.log_msg.emit(f"✓ 启动 maa-cli PID={p.pid}")

    # ── Monitoring (called by centralized poll timer) ──

    @property
    def resource_summary(self) -> str:
        """Return a short resource usage string for status bar."""
        try:
            import psutil
            sv = psutil.virtual_memory()
            used_gb = sv.used / 1024 / 1024 / 1024
            warn = " ⚠" if self._overloaded else ""
            n = len(self._procs)
            return f"MEM:{used_gb:.1f}GB/{sv.total/1024/1024/1024:.0f}GB({n}){warn}"
        except ImportError:
            return ""

    def _check_resources(self) -> None:
        """Monitor system memory and emulator/MAA processes to detect overload."""
        try:
            import psutil  # noqa: F811
        except ImportError:
            return
        sv = psutil.virtual_memory()
        free_gb = sv.available / 1024 / 1024 / 1024
        total_mem_used = sv.used / 1024 / 1024

        # Track MAA process memory (for per-process limit)
        now = time.time()
        maa_mem = 0
        for aid in list(self._procs.keys()):
            p = self._procs.get(aid)
            if not p or isinstance(p, str):
                continue
            try:
                pp = psutil.Process(p.pid)
                mem_mb = pp.memory_info().rss / 1024 / 1024
                self._proc_info[aid] = {"mem_mb": mem_mb, "pid": p.pid, "time": now}
                maa_mem += mem_mb
                if mem_mb > self.MAX_PROC_MEM_MB:
                    name = self._active.get(aid, {}).get("name", aid)
                    self.log_msg.emit(f"[资源] {name} 内存超限 ({mem_mb:.0f}MB > {self.MAX_PROC_MEM_MB}MB)")
                    self.stop(aid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._proc_info.pop(aid, None)

        # Detect overload: system free memory < 4GB → pause
        was = self._overloaded
        self._overloaded = free_gb < 4.0
        if self._overloaded != was:
            if self._overloaded:
                self.log_msg.emit(f"[资源] 系统可用内存 {free_gb:.1f}GB 不足，暂停新启动")
            else:
                self.log_msg.emit(f"[资源] 内存已恢复 ({free_gb:.1f}GB 可用)，继续启动")

    def check_processes(self) -> None:
        """Check all running processes for completion. Called by centralized poll timer."""
        self._check_resources()
        for aid in list(self._procs.keys()):
            self._check_one(aid)

    def _check_one(self, aid: str) -> None:
        p = self._procs.get(aid)
        if p is None or isinstance(p, str):
            return
        if p.poll() is None:
            self._update_status(aid)
            # Task completion detection: check log for AllTasksCompleted
            ac = self._active.get(aid)
            if ac and hasattr(p, '_inst_path'):
                lp = Path(p._inst_path) / "debug" / "asst.log"
                if lp.exists():
                    try:
                        current_size = lp.stat().st_size
                        last_pos = self._log_positions.get(aid, 0)
                        self._log.debug(f"[asst.log] {aid}: pos {last_pos} → {current_size}, changed={current_size > last_pos}")
                        if current_size > last_pos:
                            with lp.open("r", encoding="utf-8", errors="replace") as _f:
                                _f.seek(last_pos)
                                new_content = _f.read(current_size - last_pos)
                            self._log_positions[aid] = current_size
                            self._log.debug(f"[asst.log] head={repr(new_content[:200])}")
                            if "AllTasksCompleted" in new_content:
                                self.log_msg.emit(f"[完成后] {ac.get('name', aid)} 任务全部完成 (MAA 将自行退出)")
                                try: p.terminate(); p.wait(5)
                                except: pass
                                try: p.kill()
                                except: pass
                                return
                    except Exception: pass
            # Stuck detection: same task over timeout → kill
            ac = self._active.get(aid)
            if ac:
                timeout = ac.get("stuck_timeout_min", 0)
                if timeout > 0 and aid in self._task_start_times:
                    elapsed = time.time() - self._task_start_times[aid]
                    if elapsed > timeout * 60:
                        self.log_msg.emit(f"⚠ {ac.get('name', aid)} 任务卡死 ({int(elapsed/60)}分钟)，自动重启")
                        self._task_start_times.pop(aid, None)
                        try: p.terminate(); p.wait(5)
                        except: pass
                        try: p.kill()
                        except: pass
                        tasks, sanity, drops = self._parse_log(aid)
                        self._cleanup(aid, -9, tasks, sanity, drops)
                        return
                # Total runtime timeout — re-enqueue at tail
                started = self._start_times.get(aid, 0)
                if started and time.time() - started > 3600:
                    self.log_msg.emit(f"⏱ {ac.get('name', aid)} 运行超时 (>{timeout if timeout else 60}分钟)，重新排队")
                    try: p.terminate(); p.wait(5)
                    except: pass
                    try: p.kill()
                    except: pass
                    tasks, sanity, drops = self._parse_log(aid)
                    self._cleanup(aid, -3, tasks, sanity, drops)
                    return
            # ADB keepalive: ping → reconnect → kill MAA + shutdown emulator
            ac = self._active.get(aid)
            if ac:
                addr = ac.get("adb_address", "")
                adb_path = ac.get("adb_path", "") or "adb"
                emu_idx = ac.get("emu_instance_index", "")
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
                            if fail >= 3:
                                restart_count = self._adb_restart_count.get(aid, 0) + 1
                                self._adb_restart_count[aid] = restart_count
                                self._adb_fail_count.pop(aid, None)
                                if restart_count >= 3:
                                    self.log_msg.emit(f"[ADB] {ac.get('name', aid)} ADB 连续断连 {restart_count} 次，暂停")
                                    try: p.terminate(); p.wait(3)
                                    except: pass
                                    try: p.kill()
                                    except: pass
                                    tasks, sanity, drops = self._parse_log(aid)
                                    self._cleanup(aid, -9, tasks, sanity, drops)
                                else:
                                    self.log_msg.emit(f"[ADB] {ac.get('name', aid)} ADB 断连 (#{restart_count})，关模拟器重启 MAA")
                                    try: p.terminate(); p.wait(3)
                                    except: pass
                                    try: p.kill()
                                    except: pass
                                    if emu_idx:
                                        cli = find_mumu_cli()
                                        if cli:
                                            subprocess.run([cli, "control", "--vmindex", str(emu_idx), "shutdown"],
                                                          capture_output=True, timeout=10, creationflags=CF)
                                    tasks, sanity, drops = self._parse_log(aid)
                                    self._cleanup(aid, -8, tasks, sanity, drops)
                                return
                    except Exception:
                        pass
            return
        rc = p.poll()
        self._procs.pop(aid, None)
        self._task_start_times.pop(aid, None)
        tasks, sanity, drops = self._parse_log(aid)
        self._cleanup(aid, rc, tasks, sanity, drops)

    def _update_status(self, aid: str) -> None:
        """Read asst.log tail for current task name while running."""
        ac = self._active.get(aid)
        if not ac:
            return
        progs = self._progs.get(aid, [])
        if not progs:
            return
        lp = self.ctx.logs.asst_log_path(progs[0]) if self.ctx.logs else None
        name = ac.get("name", aid)
        if lp and lp.exists():
            try:
                with lp.open("rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    read_size = min(400, size)
                    f.seek(size - read_size)
                    tail = f.read(read_size).decode("utf-8", errors="replace")
                # Rolling buffer: keep last 200 lines
                lines = tail.split("\n")
                buf = self._log_buffers.setdefault(aid, [])
                buf.extend(lines)
                if len(buf) > 200:
                    buf[:] = buf[-200:]
                for line in lines:
                    if "append_callback" in line and "SubTaskStart" in line:
                        jm = re.search(r"\{.*\}", line)
                        if jm:
                            try:
                                data = json.loads(jm.group(0))
                                tc = data.get("taskchain", "")
                                st_map = {"StartUp": "唤醒", "Fight": "刷关", "Recruit": "公招", "Infrast": "基建", "Mall": "信用", "Award": "奖励", "Roguelike": "肉鸽", "Reclamation": "生息", "CloseDown": "关闭"}
                                if tc in st_map:
                                    task_name = st_map[tc]
                                    prev = ac.get("_last_task", "")
                                    if task_name != prev:
                                        ac["_last_task"] = task_name
                                        self.log_msg.emit(f"[MAA] {name} 当前任务: {task_name}")
                                    self._task_start_times[aid] = time.time()
                                    self.status_msg.emit(f"MAA: {task_name}...")
                                    return
                            except Exception:
                                pass
                    elif "append_task" in line:
                        for k, v in {"StartUp": "唤醒", "Fight": "刷关", "Recruit": "公招", "Infrast": "基建", "Mall": "信用", "Award": "奖励", "Roguelike": "肉鸽", "Reclamation": "生息"}.items():
                            if k in line:
                                prev = ac.get("_last_task", "")
                                if v != prev:
                                    ac["_last_task"] = v
                                    self.log_msg.emit(f"[MAA] {name} 当前任务: {v}")
                                self.status_msg.emit(f"MAA: {v}...")
                                return
                    elif "[ERR]" in line:
                        err = line.split("[ERR]")[-1].strip()[:80]
                        self.log_msg.emit(f"[MAA] {name} 错误: {err}")
                        if "运行终止" in err or ("重启" in err and "安卓" in err):
                            self.log_msg.emit(f"[MAA] {name} 检测到致命错误，强行终止进程以便重试")
                            p = self._procs.get(aid)
                            if p and not isinstance(p, str):
                                try: p.kill()
                                except: pass
                            return
                    elif "TaskSwitched" in line or "TaskChainCompleted" in line:
                        self.status_msg.emit("MAA: 切换任务...")
            except Exception:
                pass

    def _parse_log(self, aid: str) -> tuple[list[dict], dict | None, dict | None]:
        """Parse asst.log for task results, sanity, drops."""
        ac = self._active.get(aid)
        if not ac:
            return [], None, None
        progs = self._progs.get(aid, [])
        if not progs or not self.ctx.logs:
            return [], None, None
        return self.ctx.logs.parse_log(progs[0])

    # ── Completion ──

    def _cleanup(self, aid: str, exit_code: int, tasks: list[dict], sanity: dict | None = None, drops: dict | None = None) -> None:
        ac = self._active.pop(aid, None)
        old_progs = self._progs.pop(aid, None)
        self._procs.pop(aid, None)
        started = self._start_times.pop(aid, None)
        duration = int(time.time() - started) if started else 0
        self._stopping.discard(aid)
        self.ctx.proc_status.discard(aid)

        name = ac.get("name", aid) if ac else aid
        # MAA handles its own exit via PostActions="6" (ExitEmulator + ExitSelf)
        if exit_code == 0 and ac:
            self._log.debug(f"[完成] {name} MAA 自行退出 (PostActions=6)")
        if ac:
            plan = ac.get("smart_plan", "")
            plan_log = f" 🧠 {plan}" if plan else ""
            self.log_msg.emit(f"[完成] {name} 退出码={exit_code} 耗时={duration//60}m{duration%60}s{plan_log}")
            self._log.info(f"[清理] {name} exit={exit_code} smart_plan当前={plan or '<空>'}")
            mode = self.ctx.config.get("schedule_mode", "daily")
            if exit_code == 0:
                from services.dispatch_pool import remove_dispatch
                remove_dispatch(ac.get("dispatch_id", ""))
                ac["dispatch_id"] = ""
                if not ac.get("_persist_plan") or mode != "daily":
                    ac["smart_plan"] = ""
                ac.pop("_persist_plan", None)

        is_real_error = exit_code != 0 and exit_code not in (-9, -8) and aid not in self._stopping
        if tasks and any(t.get("status") == "完成" for t in tasks):
            is_real_error = False

        # Track consecutive failures
        failures = ac.get("consecutive_failures", 0) if ac else 0
        if is_real_error:
            failures += 1
            if ac:
                ac["consecutive_failures"] = failures
            self.log_msg.emit(f"[账号] {name} 状态: error (exit={exit_code}) 连续失败={failures}")
            self.ctx.notify(f"进程异常退出 (code={exit_code})", True)

            # Collect diagnostic
            self._collect_diagnostic(aid, ac, exit_code)

            # Track restart rate (per minute)
            now = time.time()
            rts = self._restart_times.setdefault(aid, [])
            rts[:] = [t for t in rts if t > now - 60]
            rts.append(now)

            # Check restart rate limit
            if len(rts) > self.MAX_RESTART_PER_MIN:
                self.log_msg.emit(f"[限流] {name} 每分钟重启 {len(rts)} 次，暂停 5 分钟")
                from PySide6.QtCore import QTimer
                QTimer.singleShot(300000, lambda: self._restart_times.pop(aid, None))
            elif failures >= 6:
                self.log_msg.emit(f"[暂停] {name} 连续失败 {failures} 次，暂停 30 分钟")
                self.ctx.notify(f"{name} 连续失败暂停", True)
            # Check global resource overload
            elif self._overloaded:
                self.log_msg.emit(f"[资源] 系统过载，{name} 延迟重试")
            else:
                # Exponential backoff: 5s, 10s, 20s, 40s, 80s... capped at 300s
                delay = min(300, 5 * (2 ** (failures - 1)))
                self.log_msg.emit(f"[重试] {name} {delay}s 后重试 (exp backoff {failures})")

        else:
            if ac:
                ac["consecutive_failures"] = 0
            if exit_code == 0 or exit_code in (-9, -8):
                self.log_msg.emit(f"[账号] {name} 状态: completed (exit={exit_code})")

        # Build notification with sanity info
        msg_parts = []
        if tasks:
            errs = [t for t in tasks if t.get("status") == "失败"]
            done = [t for t in tasks if t.get("status") == "完成"]
            if errs:
                msg_parts.append(f"任务失败: {errs[0].get('name')}")
            elif done:
                msg_parts.append(f"MAA 完成: {len(done)} 个任务")
        if sanity:
            cur, mx = sanity["current"], sanity["max"]
            deficit = mx - cur
            h, m = divmod(deficit * 6, 60)
            msg_parts.append(f"理智 {cur}/{mx}  ({h}h{m:02d}m回满)")

        self.account_finished.emit((aid, exit_code, tasks))
        if msg_parts:
            self.ctx.notify(" | ".join(msg_parts), False)

        # Save run stats
        if tasks:
            try:
                st = RunStats(aid)
                st.save_run(tasks, sanity, drops)
            except Exception as e:
                self.log_msg.emit(f"保存统计失败: {e}")
            try:
                from services.smart_scheduler import mark_annihilation_done
                mark_annihilation_done(aid, tasks)
            except Exception:
                pass

        # Rotate MAA log
        if old_progs:
            try:
                from services.log_parser import LogService
                lp = Path(old_progs[0].get("path", "")).parent / "debug" / "asst.log"
                LogService.rotate_log(lp)
            except Exception:
                pass

        # Push to daigan
        if tasks and ac:
            daigan_url = self.ctx.config.get("daigan_url", "").strip()
            if daigan_url and daigan_url.startswith("https://"):
                try:
                    import urllib.request, json
                    payload = json.dumps({
                        "account_name": ac.get("name", aid),
                        "account_id": aid,
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "tasks": {t["name"]: t["status"] for t in tasks},
                        "drops": drops or {},
                        "sanity": sanity,
                    }).encode("utf-8")
                    req = urllib.request.Request(f"{daigan_url}/api/maa/stats", data=payload,
                                                 headers={"Content-Type": "application/json"}, method="POST")
                    urllib.request.urlopen(req, timeout=5)
                    self.log_msg.emit(f"[daigan] 推送成功 → {daigan_url}")
                except Exception as e:
                    self.log_msg.emit(f"[daigan] 推送失败: {e}")

    def _collect_diagnostic(self, aid: str, ac: dict | None, exit_code: int) -> None:
        """Save diagnostic info (log tail + screenshot) on error."""
        try:
            from datetime import datetime as _dt
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            name = ac.get("name", aid) if ac else aid
            import re as _re
            safe_name = _re.sub(r'[^\w\-_]', '_', str(name))[:64]
            diag_root = Path(__file__).parent / "diagnostics"
            diag_dir = diag_root / f"{safe_name}_{ts}"
            # Cleanup: keep only latest 50 diagnostic dirs
            try:
                all_diags = sorted(diag_root.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in all_diags[50:]:
                    try:
                        import shutil as _su
                        _su.rmtree(str(old))
                    except Exception:
                        pass
            except Exception:
                pass
            diag_dir.mkdir(parents=True, exist_ok=True)
            # asst.log tail
            if ac:
                addr = ac.get("adb_address", "")
                if addr:
                    try:
                        r = subprocess.run(["adb", "-s", addr, "exec-out", "screencap", "-p"],
                                           capture_output=True, timeout=10, creationflags=CF)
                        (diag_dir / "screenshot.png").write_bytes(r.stdout)
                    except Exception:
                        pass
                progs = [w for w in self.ctx.warehouse if w.get("account_ref") == aid]
                if progs:
                    lp = Path(progs[0].get("path", "")).parent / "debug" / "asst.log"
                    if lp.exists():
                        lines = lp.read_text(encoding="utf-8", errors="replace").split("\n")[-100:]
                        (diag_dir / "asst.log").write_text("\n".join(lines), encoding="utf-8")
            # Save rolling buffer (last 200 lines collected while running)
            buf = self._log_buffers.get(aid, [])
            if buf:
                (diag_dir / "asst_tail.log").write_text("\n".join(buf[-200:]), encoding="utf-8")
            (diag_dir / "info.txt").write_text(
                f"aid={aid}\nexit_code={exit_code}\nts={ts}\nconsecutive_failures={ac.get('consecutive_failures', 0) if ac else 0}",
                encoding="utf-8")
            self.log_msg.emit(f"[诊断] {name} 已保存到 {diag_dir}")
        except Exception as e:
            self.log_msg.emit(f"[诊断] 保存失败: {e}")

    def _track_stats(self, ac: dict) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        sd = ac.setdefault("stats", {})
        sd.setdefault(today, {"launches": 0, "total_sec": 0})
        sd[today]["launches"] += 1
        self.ctx.save()
