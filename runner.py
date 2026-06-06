"""Single-account launch → monitor → complete cycle runner."""
from __future__ import annotations
import time, subprocess, re
from pathlib import Path
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, Signal, QTimer

from task_constants import find_mumu_cli, CF
from callbacks import ServiceContext
from stats import RunStats


class AccountRunner(QObject):
    """Encapsulates single-account launch → monitor → complete lifecycle.

    Signals are the public API — UI / scheduler listens to these,
    never touches internal process tracking directly."""

    log_msg = Signal(str)
    status_msg = Signal(str)
    account_started = Signal(str)          # account_id
    account_finished = Signal(str, int, list)  # account_id, exit_code, tasks
    account_error = Signal(str, str)       # account_id, error_msg

    def __init__(self, ctx: ServiceContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._active: dict[str, dict] = {}         # account_id → account dict
        self._progs: dict[str, list[dict]] = {}     # account_id → bound programs
        self._procs: dict[str, subprocess.Popen] = {}  # account_id → Popen
        self._start_times: dict[str, float] = {}    # account_id → time.time()
        self._stopping: set = set()                 # accounts being stopped

    # ── Public API ──

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
        progs = [w for w in self.ctx.warehouse if w.get("account_ref") == aid]
        if not progs:
            self.log_msg.emit(f"{ac.get('name', aid)} 未绑定程序")
            return False
        self._active[aid] = ac
        self._progs[aid] = progs
        self.log_msg.emit(f"[启动] {ac.get('name', aid)}")
        self._track_stats(ac)
        self._do_launch(ac, progs)
        return True

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

    def _do_launch(self, ac: dict, progs: list[dict]) -> None:
        emu_idx = ac.get("emu_instance_index", "")
        aid = ac["id"]

        # Path A: auto-launch emulator
        if ac.get("emu_launch") and emu_idx:
            cli = find_mumu_cli()
            if cli:
                self.log_msg.emit(f"启动模拟器 #{emu_idx}")
                try:
                    subprocess.run([cli, "control", "--vmindex", str(emu_idx), "launch"], creationflags=CF, timeout=15)
                except Exception as e:
                    self.log_msg.emit(f"启动模拟器失败: {e}")
                self._emu_wait_and_launch(ac, progs, ac.get("emu_wait", 30))
                return

        # Path B: ADB fail → launch emulator
        if ac.get("adb_fail_launch_emu") and emu_idx:
            cli = find_mumu_cli()
            adb = ac.get("adb_path", "") or "adb"
            addr = ac.get("adb_address", "")
            if addr and cli:
                r = subprocess.run([adb, "connect", addr], capture_output=True, text=True, timeout=5, creationflags=CF, encoding="utf-8", errors="replace")
                out = (r.stdout + r.stderr).strip()
                if "connected" in out.lower() or "already" in out.lower():
                    self.log_msg.emit(f"ADB 已连接 {addr}")
                else:
                    self.log_msg.emit(f"ADB 失败，启动模拟器 #{emu_idx}")
                    try:
                        subprocess.run([cli, "control", "--vmindex", str(emu_idx), "launch"], creationflags=CF, timeout=15)
                    except Exception as e:
                        self.log_msg.emit(f"启动模拟器失败: {e}")
                    self._emu_wait_and_launch(ac, progs, ac.get("emu_wait", 30))
                    return

        # Path C: ADB retry
        retry = ac.get("adb_retry", 0)
        if retry > 0 and ac.get("adb_address", ""):
            self._adb_retry_launch(ac, progs, retry)
            return

        # Path D: direct launch
        self._launch_progs(ac, progs)

    def _emu_wait_and_launch(self, ac: dict, progs: list[dict], remaining: int) -> None:
        if remaining > 0:
            self.status_msg.emit(f"等待模拟器 ({remaining}s)...")
            QTimer.singleShot(1000, lambda: self._emu_wait_and_launch(ac, progs, remaining - 1))
        else:
            self.status_msg.emit("就绪")
            adb = ac.get("adb_path", "") or "adb"
            addr = ac.get("adb_address", "")
            if not addr:
                try:
                    r = subprocess.run([adb, "devices"], capture_output=True, timeout=5, creationflags=CF)
                    for m in re.finditer(rb":(\d+)\s+device\b", r.stdout):
                        addr = "127.0.0.1:" + m.group(1).decode("ascii")
                        ac["adb_address"] = addr
                        self.ctx.save()
                        self.log_msg.emit(f"自动检测 ADB: {addr}")
                        break
                except Exception:
                    pass
            if addr:
                self.log_msg.emit(f"连接 ADB: {addr}")
                try:
                    subprocess.run([adb, "connect", addr], capture_output=True, creationflags=CF, timeout=5)
                except Exception as e:
                    self.log_msg.emit(f"ADB 连接失败: {e}")
            self._launch_progs(ac, progs)

    def _adb_retry_launch(self, ac: dict, progs: list[dict], retry: int, attempt: int = 0) -> None:
        if attempt >= retry:
            self.log_msg.emit(f"ADB 重试耗尽 ({retry})")
            self._launch_progs(ac, progs)
            return
        adb = ac.get("adb_path", "") or "adb"
        addr = ac["adb_address"]
        r = subprocess.run([adb, "connect", addr], capture_output=True, text=True, timeout=5, creationflags=CF, encoding="utf-8", errors="replace")
        if "connected" in (r.stdout + r.stderr).lower() or "already" in (r.stdout + r.stderr).lower():
            self.log_msg.emit(f"ADB 重试成功 ({attempt + 1}/{retry})")
            self._launch_progs(ac, progs)
            return
        self.status_msg.emit(f"ADB 重试 ({attempt + 1}/{retry})...")
        QTimer.singleShot(1000, lambda: self._adb_retry_launch(ac, progs, retry, attempt + 1))

    def _launch_progs(self, ac: dict, progs: list[dict]) -> None:
        aid = ac["id"]
        for w in progs:
            try:
                self.ctx.inject_config(w, ac)
                self._spawn(w, ac)
            except Exception as e:
                self.log_msg.emit(f"启动失败: {e}")
                self.account_error.emit(aid, str(e))
                self._cleanup(aid, -1, [])
                return
        self.account_started.emit(aid)

    def _spawn(self, w: dict, ac: dict) -> None:
        aid = ac["id"]
        args = w.get("args", [])
        cwd = w.get("cwd", "") or None
        env = {k: v for k, v in w.get("env", {}).items()} or None
        exe = w["path"]
        lm = w.get("launch_mode", "gui")

        if w.get("account_ref") and lm == "cli":
            self._spawn_cli(w, ac, exe, args, cwd, env)
            return

        kwargs = {"shell": False, "cwd": cwd, "env": env}
        p = subprocess.Popen([exe] + args, **kwargs)
        self._procs[aid] = p
        self._start_times[aid] = time.time()
        self.ctx.proc_status.add(aid)
        self.ctx.proc_status.add(w["id"])     # dashboard uses program ID
        self.ctx.proc_start_times[w["id"]] = time.time()
        self.log_msg.emit(f"✓ 启动 {Path(w['path']).stem} PID={p.pid}")

    def _spawn_cli(self, w: dict, ac: dict, exe: str, args: list, cwd: str | None, env: dict | None) -> None:
        from utils import _find_maa_cli
        from updater import MaacliInstallDialog

        aid = ac["id"]
        cl = _find_maa_cli()
        if not cl:
            d = MaacliInstallDialog(self.ctx._mw)
            d.start(str(Path(__file__).parent / "maa-cli"))
            if d.exec() != __import__("PySide6.QtWidgets").QDialog.Accepted:
                return
            cl = _find_maa_cli()
        if not cl:
            self.log_msg.emit("maa-cli 未安装")
            return
        md = Path(w["path"]).parent
        lc = md / Path(cl).name
        if not lc.exists() or lc.stat().st_mtime < Path(cl).stat().st_mtime:
            import shutil
            shutil.copy2(cl, str(lc))
        tn = self.ctx.cfg.gtc(ac, w)
        if tn:
            env = (env or __import__("os").environ.copy())
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

    def check_processes(self) -> None:
        """Check all running processes for completion. Called by centralized poll timer."""
        for aid in list(self._procs.keys()):
            self._check_one(aid)

    def _check_one(self, aid: str) -> None:
        p = self._procs.get(aid)
        if p is None:
            return
        if p.poll() is None:
            self._update_status(aid)
            return
        rc = p.poll()
        self._procs.pop(aid, None)
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
        if lp and lp.exists():
            try:
                last = lp.read_text(encoding="utf-8", errors="replace").strip().split("\n")[-5:]
                for line in last:
                    # v6: append_callback SubTaskStart
                    if "append_callback" in line and "SubTaskStart" in line:
                        jm = __import__("re").search(r"\{.*\}", line)
                        if jm:
                            try:
                                data = __import__("json").loads(jm.group(0))
                                tc = data.get("taskchain", "")
                                st_map = {"StartUp": "唤醒", "Fight": "刷关", "Recruit": "公招", "Infrast": "基建", "Mall": "信用", "Award": "奖励", "Roguelike": "肉鸽", "Reclamation": "生息", "CloseDown": "关闭"}
                                if tc in st_map:
                                    self.status_msg.emit(f"MAA: {st_map[tc]}...")
                                    return
                            except Exception:
                                pass
                    # v5 fallback: append_task
                    elif "append_task" in line:
                        for k, v in {"StartUp": "唤醒", "Fight": "刷关", "Recruit": "公招", "Infrast": "基建", "Mall": "信用", "Award": "奖励", "Roguelike": "肉鸽", "Reclamation": "生息"}.items():
                            if k in line:
                                self.status_msg.emit(f"MAA: {v}...")
                                return
                    elif "[ERR]" in line:
                        err = line.split("[ERR]")[-1].strip()[:80]
                        self.log_msg.emit(f"MAA错误: {err}")
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
        self._start_times.pop(aid, None)
        self._stopping.discard(aid)
        self.ctx.proc_status.discard(aid)
        # Also remove program IDs from status tracking
        if old_progs:
            for w in old_progs:
                self.ctx.proc_status.discard(w["id"])
                self.ctx.proc_start_times.pop(w["id"], None)

        if ac and exit_code != 0 and exit_code != -9 and aid not in self._stopping:
            self.log_msg.emit(f"{ac.get('name', aid)} 异常退出 (code={exit_code})")
            self.ctx.notify(f"进程异常退出 (code={exit_code})", True)

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

        self.account_finished.emit(aid, exit_code, tasks)
        if msg_parts:
            self.ctx.notify(" | ".join(msg_parts), False)

        # Save run stats
        if tasks:
            try:
                st = RunStats(aid)
                st.save_run(tasks, sanity, drops)
            except Exception:
                pass

        # Rotate MAA log — keep only last 3 runs
        if old_progs:
            try:
                from log_ops import LogService
                lp = Path(old_progs[0].get("path", "")).parent / "debug" / "asst.log"
                LogService.rotate_log(lp)
            except Exception:
                pass

    def _track_stats(self, ac: dict) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        sd = ac.setdefault("stats", {})
        sd.setdefault(today, {"launches": 0, "total_sec": 0})
        sd[today]["launches"] += 1
        self.ctx.save()
