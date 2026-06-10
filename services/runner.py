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

        # Auto-fill ADB address if empty but emu instance is set
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
                getattr(p, "_inst_path", None) == inst_path
                for p in self._procs.values()
            )
            if already_used:
                continue
            pid_file = inst_dir / ".pid"
            running = False
            if pid_file.exists():
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
        emu_idx = ac.get("emu_instance_index", "")
        aid = ac["id"]
        inst_id, inst_dir = inst

        # Launch emulator if needed
        if ac.get("emu_launch") and emu_idx:
            cli = find_mumu_cli()
            if cli:
                self.log_msg.emit(f"启动模拟器 #{emu_idx}")
                try:
                    subprocess.run([cli, "control", "--vmindex", str(emu_idx), "launch"], creationflags=CF, timeout=15)
                except Exception as e:
                    self.log_msg.emit(f"启动模拟器失败: {e}")

        # ADB connection
        adb = ac.get("adb_path", "") or "adb"
        addr = ac.get("adb_address", "")
        if addr:
            try: subprocess.run([adb, "connect", addr], capture_output=True, creationflags=CF, timeout=5)
            except Exception as e: self.log_msg.emit(f"ADB 连接失败 {addr}: {e}")

        # Inject config and launch
        self._launch_for_instance(ac, inst_dir)

    def _launch_for_instance(self, ac: dict, inst_dir: str) -> None:
        aid = ac["id"]
        smart_enabled = self.ctx.config.get("smart_global", {}).get("enabled", False)
        exe = Path(inst_dir) / "MAA.exe"
        config_dir = Path(inst_dir) / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        try:
            if smart_enabled:
                plan_txt = ac.get("smart_plan", "")
                if plan_txt:
                    task_list = plan_txt.split(",")
                else:
                    from services.smart_scheduler import get_tasks_for_account
                    task_list = get_tasks_for_account(ac, self.ctx.config.get("smart_global", {}))
                    plan_txt = ",".join(task_list)
                    ac["smart_plan"] = plan_txt
                self.log_msg.emit(f"🧠 智能调度: {plan_txt}")
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
        p = subprocess.Popen([str(exe)], shell=False)
        p._inst_path = str(Path(inst_dir).resolve())  # track instance for _get_free_instance
        self._procs[aid] = p
        self._start_times[aid] = time.time()
        self.ctx.proc_status.add(aid)
        try: pid_file.write_text(str(p.pid))
        except: pass
        self.log_msg.emit(f"✓ 启动 MAA PID={p.pid}")
        self.account_started.emit(aid)

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
            if not p:
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
        if p is None:
            return
        if p.poll() is None:
            self._update_status(aid)
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
                            if p:
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
        started = self._start_times.pop(aid, None)
        duration = int(time.time() - started) if started else 0
        self._stopping.discard(aid)
        self.ctx.proc_status.discard(aid)

        name = ac.get("name", aid) if ac else aid
        if ac:
            plan = ac.get("smart_plan", "")
            plan_log = f" 🧠 {plan}" if plan else ""
            self.log_msg.emit(f"[完成] {name} 退出码={exit_code} 耗时={duration//60}m{duration%60}s{plan_log}")
            ac["smart_plan"] = ""

        is_real_error = exit_code != 0 and exit_code != -9 and aid not in self._stopping
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
                from PySide6.QtCore import QTimer
                from datetime import datetime, timedelta
                q = getattr(self.ctx._mw, "launch_queue", None)
                if q and ac:
                    QTimer.singleShot(60000, lambda a=aid: q.enqueue(a, "schedule", priority=1,
                                        not_before=datetime.now() + timedelta(seconds=60)))
            else:
                # Exponential backoff: 5s, 10s, 20s, 40s, 80s... capped at 300s
                delay = min(300, 5 * (2 ** (failures - 1)))
                self.log_msg.emit(f"[重试] {name} {delay}s 后重试 (exp backoff {failures})")
                from PySide6.QtCore import QTimer
                from datetime import datetime, timedelta
                q = getattr(self.ctx._mw, "launch_queue", None)
                if q and ac:
                    QTimer.singleShot(delay * 1000, lambda a=aid: q.enqueue(a, "schedule", priority=1,
                                        not_before=datetime.now() + timedelta(seconds=delay)))

        else:
            if ac:
                ac["consecutive_failures"] = 0
            if exit_code == 0 or exit_code == -9:
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
            if daigan_url:
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
            diag_dir = Path(__file__).parent / "diagnostics" / f"{name}_{ts}"
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
