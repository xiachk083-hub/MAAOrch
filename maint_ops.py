from __future__ import annotations
import time, json, urllib.request, threading
from pathlib import Path
from datetime import datetime
from typing import Any
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPixmap, QPainter, QColor, QBrush, QPolygonF, QIcon
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox, QApplication, QSystemTrayIcon, QMenu, QFileDialog
from utils import parse_maa_version, get_platform_key, _version_tuple, make_id
from dialogs import ScheduleDialog, SettingsDialog
from updater import UpdateCheckThread, UpdateDialog
from ui.config_cards import refresh_config_cards
from schedule_thread import ScheduleThread
from callbacks import ServiceContext


_INSTANCE_LOCK = threading.Lock()
_inst_init_task = None  # prevent GC of BackgroundTask


def _create_instance(inst: Path, source: Path) -> bool:
    """Create a single MAA instance using junctions for large dirs (fast, no full copy)."""
    import shutil, subprocess as _sp
    if (inst / "MAA.exe").exists():
        return True
    if inst.exists():
        try:
            shutil.rmtree(str(inst))
        except PermissionError:
            # Directory locked — try to work with it as-is
            pass
    inst.mkdir(parents=True, exist_ok=True)
    try:
        for item in source.iterdir():
            if item.is_file():
                shutil.copy2(str(item), str(inst / item.name))
        for sub in ("resource", "externals", "Python"):
            src_sub = source / sub
            dst_sub = inst / sub
            if src_sub.exists() and not dst_sub.exists():
                _sp.run(["cmd", "/c", "mklink", "/J", str(dst_sub), str(src_sub)],
                        capture_output=True, timeout=5)
        # Copy config from source (MAA must have been opened at least once to generate defaults)
        src_config = source / "config"
        dst_config = inst / "config"
        if src_config.exists():
            shutil.copytree(str(src_config), str(dst_config), dirs_exist_ok=True)
        else:
            dst_config.mkdir(exist_ok=True)
        for sub in ("cache", "data", "debug"):
            (inst / sub).mkdir(exist_ok=True)
        return True
    except Exception:
        return False


def _check_source_ready(source: Path) -> bool:
    """Check if source MAA has been initialized (config with $type fields)."""
    gj = source / "config" / "gui.new.json"
    if not gj.exists():
        return False
    try:
        data = json.loads(gj.read_text(encoding="utf-8"))
        tq = data.get("Configurations", {}).get("Default", {}).get("TaskQueue", [])
        return any("$type" in item for item in tq)
    except Exception:
        return False


def _init_maa_source(source: Path) -> bool:
    """Silently launch MAA to generate default config ($type etc.), then kill it."""
    import subprocess, time as _time
    exe = source / "MAA.exe"
    if not exe.exists():
        return False
    try:
        proc = subprocess.Popen([str(exe)], creationflags=subprocess.CREATE_NO_WINDOW)
        gj = source / "config" / "gui.new.json"
        for _ in range(120):
            if gj.exists():
                try:
                    data = json.loads(gj.read_text(encoding="utf-8"))
                    tq = data.get("Configurations", {}).get("Default", {}).get("TaskQueue", [])
                    if any("$type" in item for item in tq):
                        _time.sleep(1)
                        proc.terminate()
                        proc.wait(5)
                        return True
                except Exception:
                    pass
            _time.sleep(1)
        proc.kill()
        proc.wait(3)
        return _check_source_ready(source)
    except Exception:
        return False


def _delete_instances(pool: Path) -> None:
    """Delete all instance directories, skipping running ones."""
    import subprocess as _sp
    for inst_dir in pool.glob("*"):
        if not inst_dir.is_dir():
            continue
        pid_file = inst_dir / ".pid"
        running = False
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                r = _sp.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                            capture_output=True, text=True, timeout=2,
                            creationflags=_sp.CREATE_NO_WINDOW)
                running = str(pid) in r.stdout
            except Exception:
                pass
        if running:
            continue
        try:
            import shutil as _su
            _su.rmtree(str(inst_dir))
        except Exception:
            pass


def ensure_maa_instances_async(ctx) -> None:
    """Pre-create all MAA instances up to parallel_max + 1 in background.
    Auto-initializes source MAA and handles version changes."""
    global _inst_init_task
    src = _find_maa_source()
    if not src:
        return
    desired = ctx.config.get("parallel_max", 1) + 1
    pool = Path(__file__).parent / "maa" / "instances"

    # Version check — rebuild if MAA version changed
    current_ver = ctx.config.get("maa_version", "")
    built_ver = ctx.config.get("maa_instances_version", "")
    ver_changed = current_ver and built_ver and current_ver != built_ver

    if ver_changed:
        _delete_instances(pool)
        with _INSTANCE_LOCK:
            ctx.config["maa_instances"] = 0

    # Ensure source MAA config is initialized (outside lock, may take up to 120s)
    if not _check_source_ready(src):
        ctx.log("[MAA] 正在初始化 MAA 默认配置...")
        if not _init_maa_source(src):
            ctx.log("[MAA] MAA 初始化失败，部分功能可能不可用")
            return

    with _INSTANCE_LOCK:
        existing_ok = sum(1 for i in range(1, desired + 1) if (pool / str(i) / "MAA.exe").exists())
        if existing_ok >= desired and not ver_changed:
            ctx.config["maa_instances"] = desired
            ctx.save()
            return

    from background import BackgroundTask

    def _init():
        pool = Path(__file__).parent / "maa" / "instances"
        pool.mkdir(parents=True, exist_ok=True)
        created = 0
        for i in range(1, desired + 1):
            source = src if i == 1 else (pool / "1")
            if _create_instance(pool / str(i), source):
                created = i
            else:
                break
        return created

    def _on_init_result(actual):
        with _INSTANCE_LOCK:
            ctx.config["maa_instances"] = max(actual, ctx.config.get("maa_instances", 0))
            if actual >= desired:
                ctx.config["maa_instances_version"] = ctx.config.get("maa_version", "")
            ctx.save()

    def _on_finished():
        global _inst_init_task
        _inst_init_task = None

    with _INSTANCE_LOCK:
        t = BackgroundTask(_init)
        t.result.connect(_on_init_result)
        t.finished.connect(_on_finished)
        _inst_init_task = t
        t.start()


def _ensure_instance_n(ctx, n: int) -> bool:
    """Lazily create instance #n (n >= 2) if it doesn't exist yet.
    Called when parallel_max is increased at runtime beyond existing instances."""
    pool = Path(__file__).parent / "maa" / "instances"
    inst = pool / str(n)
    if (inst / "MAA.exe").exists():
        return True
    src = pool / "1"
    if not (src / "MAA.exe").exists():
        return False
    return _create_instance(inst, src)


def _find_maa_source() -> Path | None:
    """Find MAA executable source for instance pool creation.
    Priority: maa/source/ (user-managed) → maa/v*/ (versioned) → accounts/*/ (legacy)."""
    root = Path(__file__).parent
    ver = root / "maa"
    # 1. User-managed source
    src = ver / "source"
    if (src / "MAA.exe").exists():
        return src
    # 2. Versioned download directories
    for d in sorted(ver.iterdir()) if ver.exists() else []:
        if d.name == "source":
            continue
        exe = d / "MAA.exe"
        if exe.exists():
            return exe.parent
    # 3. Legacy account directories
    import glob
    maas = list(root.glob("accounts/*/MAA/MAA.exe"))
    if maas:
        return maas[0].parent
    return None


def _trigger_batch(svc):
    """Daily batch: enqueue all accounts."""
    lq = getattr(svc.ctx._mw, "launch_queue", None)
    if lq:
        lq.batch_enqueue_all()
        svc.ctx.log("[调度] 每日批量入队")


class MaintService:
    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def _bind_maa(self, exe: str, version: str, account_id: str) -> None:
        """Create a warehouse entry binding an account to an MAA exe."""
        e = {"id": make_id(), "path": str(exe), "args": [], "cwd": "", "env": {},
             "maa_type": "maa", "maa_version": version, "account_ref": account_id,
             "launch_mode": "gui", "task_pipeline": "startup,fight,recruit,infrast,mall,award",
             "guard_enabled": True, "guard_max_restart": 3, "guard_capture_log": False}
        # Replace existing binding for this account
        self.ctx.warehouse[:] = [w for w in self.ctx.warehouse if w.get("account_ref") != account_id]
        self.ctx.warehouse.append(e)

    def dl_maa(self, row: int) -> None:
        a = self.ctx.accounts[row]
        def oc(r):
            if not r.get("ok"): return
            tag = r["tag"]; info = r["assets"].get(get_platform_key())
            if not info: return
            d = Path(__file__).parent / "accounts" / a["id"] / "MAA"
            d.mkdir(parents=True, exist_ok=True)
            dlg = UpdateDialog(self.ctx._mw, tag, info, str(d))
            if dlg.exec() != QDialog.Accepted: return
            exe = next(iter(d.rglob("MAA.exe")), None)
            if not exe: return
            self._bind_maa(exe, tag, a["id"])
            self.ctx.save(); self.ctx.show_dashboard(row)
            refresh_config_cards(self.ctx._mw)
            self.ctx.inject_config(self.ctx.warehouse[-1], a)
        t = UpdateCheckThread(); t.result_ready.connect(oc)
        self._replace_update_thread(t); t.start()

    def dl_maa_all(self) -> None:
        """Download MAA once, create instance pool."""
        def oc(r):
            if not r.get("ok"): return
            tag = r["tag"]; info = r["assets"].get(get_platform_key())
            if not info: return
            d = Path(__file__).parent / "maa" / tag
            d.mkdir(parents=True, exist_ok=True)
            dlg = UpdateDialog(self.ctx._mw, tag, info, str(d))
            if dlg.exec() != QDialog.Accepted: return
            exe = next(iter(d.rglob("MAA.exe")), None)
            if not exe: return
            self.ctx.config["maa_version"] = tag
            self.ctx.save()
            ensure_maa_instances_async(self.ctx)
            self.ctx.log(f"MAA {tag} 已下载，实例池就绪 ({self.ctx.config.get('parallel_max',1)} 个)")
        t = UpdateCheckThread(); t.result_ready.connect(oc)
        self._replace_update_thread(t); t.start()

    def pk_maa(self, row: int) -> None:
        a = self.ctx.accounts[row]
        f, _ = QFileDialog.getOpenFileName(self.ctx._mw, "选择", "", "MAA (*.exe);;所有文件 (*.*)")
        if not f:
            return
        p = str(Path(f))
        e = {"id": make_id(), "path": p, "args": [], "cwd": "", "env": {}, "maa_type": "maa", "maa_version": parse_maa_version(p) or "", "account_ref": a["id"], "launch_mode": "gui", "task_pipeline": "startup,fight,recruit,infrast,mall,award", "guard_enabled": False, "guard_max_restart": 3, "guard_capture_log": False}
        self.ctx.warehouse.append(e)
        self.ctx.save()
        self.ctx.show_dashboard(row)
        refresh_config_cards(self.ctx._mw)
        self.ctx.inject_config(e, a)
        self.ctx.launch_program(e)

    def poll(self) -> None:
        now = time.time()
        # CLI process monitoring (legacy, not managed by runner)
        for pid in list(self.ctx.cli_procs.keys()):
            p = self.ctx.cli_procs[pid]
            if p.poll() is not None:
                out = p.stdout.read().decode(errors="replace").strip()
                err = p.stderr.read().decode(errors="replace").strip()
                if out:
                    self.ctx.log(f"[maa-cli] {out[:500]}")
                if err:
                    self.ctx.log(f"[maa-cli] {err[:500]}")
                rc = p.poll()
                self.ctx.log(f"[maa-cli] 退出码: {rc}")
                self.ctx.cli_procs.pop(pid, None)
                self.ctx.proc_status.discard(pid)
                self.ctx.notify("MAA CLI 已退出" if rc == 0 else "MAA CLI 异常退出", rc != 0)

        # GUI process monitoring → delegate to runner
        if hasattr(self.ctx._mw, "runner") and self.ctx._mw.runner:
            self.ctx._mw.runner.check_processes()

        # Status bar: show running accounts
        running = [pid for pid in self.ctx.proc_status if pid in self.ctx.proc_start_times]
        if running:
            elapsed = int(now - self.ctx.proc_start_times[running[0]])
            self.ctx.set_status(f"运行中 ({elapsed // 60}m{elapsed % 60}s)")

    def notify(self, msg: str, is_error: bool = False) -> None:
        mw = self.ctx._mw
        if mw and hasattr(mw, "tray_icon"):
            mw.tray_icon.showMessage("流水线启动器", msg, QSystemTrayIcon.Critical if is_error else QSystemTrayIcon.Information, 3000)
        wh = self.ctx.config.get("webhook_url", "")
        if wh:
            try:
                data = json.dumps({"msg": msg, "type": "error" if is_error else "info", "time": datetime.now().isoformat()}).encode()
                req = urllib.request.Request(wh, data=data, headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                try:
                    self.ctx.log(f"Webhook 失败: {e}")
                except Exception:
                    pass

    def auto_check_updates(self) -> None:
        """Periodic silent MAA update check (notification only, no auto-download dialog)."""
        try:
            self._do_auto_check()
        except Exception as e:
            try: self.ctx.log(f"自动检查更新失败: {e}")
            except: pass

    def _do_auto_check(self) -> None:
        if not self.ctx.config.get("auto_update_maa", True):
            return
        items = [w for w in self.ctx.warehouse if w.get("maa_type") != "general"]
        if not items:
            return

        def oc(r):
            if not r.get("ok"):
                return
            tag = r["tag"]
            info = r["assets"].get(get_platform_key())
            if not info:
                return
            ups = [w for w in items if _version_tuple(w.get("maa_version", "")) < _version_tuple(tag) and w.get("auto_update", True)]
            if not ups:
                return
            names = ", ".join(Path(w["path"]).stem for w in ups[:3])
            more = f" +{len(ups)-3}" if len(ups) > 3 else ""
            self.ctx.log(f"检测到资源更新: MAA {tag} ({len(ups)} 个: {names}{more})")
            self.ctx.notify(f"MAA {tag} 可用 ({len(ups)} 个待更新)", False)

        t = UpdateCheckThread()
        t.result_ready.connect(oc)
        self.ctx.update_thread = t
        t.start()

    def start_auto_update_timer(self) -> None:
        """Start periodic MAA update check (runs every N hours)."""
        from PySide6.QtCore import QTimer
        interval_h = self.ctx.config.get("maa_update_interval", 6)
        self._auto_update_timer = QTimer(self.ctx._mw)
        self._auto_update_timer.timeout.connect(self.auto_check_updates)
        self._auto_update_timer.start(interval_h * 3600 * 1000)
        # Also check once after 30s on startup
        QTimer.singleShot(30000, self.auto_check_updates)

    def _replace_update_thread(self, t):
        old = getattr(self.ctx, "update_thread", None)
        if old:
            try: old.result_ready.disconnect()
            except: pass
        self.ctx.update_thread = t

    def check_updates(self, silent: bool = False) -> None:
        items = [w for w in self.ctx.warehouse if w.get("maa_type") != "general"]
        if not items:
            if not silent:
                QMessageBox.information(self.ctx._mw, "提示", "无 MAA 程序")
            return

        def oc(r):
            if not r.get("ok"):
                if not silent:
                    QMessageBox.warning(self.ctx._mw, "失败", r.get("error", ""))
                return
            tag = r["tag"]
            info = r["assets"].get(get_platform_key())
            if not info:
                return
            ups = [(w, Path(w["path"]).parent) for w in items if _version_tuple(w.get("maa_version", "")) < _version_tuple(tag)]
            if not ups:
                if not silent:
                    QMessageBox.information(self.ctx._mw, "提示", f"已是最新 {tag}")
                return
            if silent:
                self.ctx.log(f"MAA {tag} 可用")
                return
            if QMessageBox.question(self.ctx._mw, "更新", f"更新 {len(ups)} 个?") == QMessageBox.Yes:
                for w, d in ups:
                    dlg = UpdateDialog(self.ctx._mw, tag, info, str(d))
                    if dlg.exec() == QDialog.Accepted:
                        w["maa_version"] = tag
                self.ctx.save()

        t = UpdateCheckThread()
        t.result_ready.connect(oc)
        self._replace_update_thread(t)
        t.start()

    def cu_single(self, w: dict) -> None:
        if w.get("maa_type") == "general":
            return

        def oc(r):
            if not r.get("ok"):
                return
            tag = r["tag"]
            info = r["assets"].get(get_platform_key())
            if not info:
                return
            dlg = UpdateDialog(self.ctx._mw, tag, info, str(Path(w["path"]).parent))
            if dlg.exec() == QDialog.Accepted:
                w["maa_version"] = tag
                self.ctx.save()

        t = UpdateCheckThread()
        t.result_ready.connect(oc)
        self.ctx.update_thread = t
        t.start()

    def restore_geometry(self) -> None:
        mw = self.ctx._mw
        g = self.ctx.config.get("window_geometry", "")
        if g:
            p = g.split("+")
            try:
                if len(p) == 3:
                    wh = p[0].split("x")
                    w, h, x, y = int(wh[0]), int(wh[1]), int(p[1]), int(p[2])
                    screen = QApplication.primaryScreen().availableGeometry()
                    x = max(0, min(x, screen.width() - 100))
                    y = max(0, min(y, screen.height() - 100))
                    w = min(w, screen.width())
                    h = min(h, screen.height())
                    mw.setGeometry(x, y, w, h)
                else:
                    wh = p[0].split("x")
                    if len(wh) >= 2:
                        mw.resize(int(wh[0]), int(wh[1]))
            except Exception:
                mw.resize(960, 650)
        else:
            mw.resize(960, 650)

    def setup_tray(self) -> None:
        mw = self.ctx._mw
        mw.tray_icon = QSystemTrayIcon(mw)
        mw.tray_icon.setToolTip("流水线启动器")
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(58, 126, 191)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, 56, 56)
        p.setBrush(QBrush(Qt.white))
        tri = QPolygonF([QPointF(24, 18), QPointF(24, 46), QPointF(46, 32)])
        p.drawPolygon(tri)
        p.end()
        ic = QIcon(pm)
        mw.setWindowIcon(ic)
        mw.tray_icon.setIcon(ic)
        m = QMenu()
        m.addAction("显示", self.show_tray)
        m.addAction("退出", self._quit_app)
        mw.tray_icon.setContextMenu(m)
        mw.tray_icon.show()

    def show_tray(self) -> None:
        mw = self.ctx._mw
        mw.show()
        self.restore_geometry()
        mw.activateWindow()

    def _quit_app(self) -> None:
        """Save config and quit (called from tray menu)."""
        mw = self.ctx._mw
        if mw and hasattr(mw, "_do_save"):
            mw._do_save()
        QApplication.quit()

    def start_schedule(self) -> None:
        has_batch = bool(self.ctx.config.get("daily_batch_time", ""))
        sched = self.ctx.config.get("schedule", {})
        if sched.get("enabled") or has_batch:
            self._ensure_schedule_config(has_batch, sched)
            self._start_schedule_thread()

    def _ensure_schedule_config(self, has_batch: bool, sched: dict) -> None:
        if not sched.get("enabled") and has_batch:
            bt = self.ctx.config.get("daily_batch_time", "")
            self.ctx.config["schedule"] = {"enabled": True, "type": "daily", "time": bt, "days_of_week": []}

    def _start_schedule_thread(self) -> None:
        if self.ctx.schedule_thread and self.ctx.schedule_thread.isRunning():
            return  # config changes propagate automatically via shared dict
        self.ctx.schedule_thread = ScheduleThread(self.ctx.config)
        self.ctx.schedule_thread.trigger.connect(self.ctx.start_pipeline)
        self.ctx.schedule_thread.batch_trigger.connect(lambda: _trigger_batch(self))
        self.ctx.schedule_thread.start()

    def sch(self) -> None:
        d = ScheduleDialog(self.ctx._mw, self.ctx.config.get("schedule", {}))
        ok = d.exec() == QDialog.Accepted
        if ok:
            self.ctx.config["schedule"] = d.r
            self.ctx.save()
        cfg = d.r if ok else self.ctx.config.get("schedule", {})
        if cfg.get("enabled"):
            self.ctx.config["schedule"] = cfg
            self._start_schedule_thread()
        elif self.ctx.schedule_thread:
            self.ctx.schedule_thread.stop_thread()
            self.ctx.schedule_thread = None

    def settings(self) -> None:
        old_port = self.ctx.config.get("api_port", 19999)
        old_token = self.ctx.config.get("api_token", "")
        d = SettingsDialog(self.ctx._mw, self.ctx.config)
        if d.exec() == QDialog.Accepted:
            self.ctx.set_theme(self.ctx.config.get("appearance_mode", "Dark"))
            self.ctx.save()
            if self.ctx.config.get("api_port", 19999) != old_port or self.ctx.config.get("api_token", "") != old_token:
                self.ctx.restart_api_server()

    def check_orch_update(self) -> None:
        """Check for MAAOrch self-update, download and replace if newer."""
        from updater import OrchUpdateCheckThread
        from utils import _version_tuple

        def _on_result(r):
            if not r.get("ok"):
                QMessageBox.information(self.ctx._mw, "检查更新", f"检查失败: {r.get('error', '')}")
                return
            tag = r["tag"]
            new_ver = _version_tuple(tag)
            # Get current version
            try:
                cur_ver = _version_tuple(self.ctx._mw.VERSION)
            except Exception:
                cur_ver = (0,)
            if new_ver <= cur_ver:
                QMessageBox.information(self.ctx._mw, "检查更新", f"已是最新版本 {tag}")
                return

            # Show update dialog
            msg = f"MAAOrch {tag} 可用\n当前版本: {self.ctx._mw.VERSION}\n\n是否下载更新？\n\n下载后会自动替换并重启"
            if QMessageBox.question(self.ctx._mw, "更新", msg) != QMessageBox.Yes:
                return

            # Download + generate replace script
            self.ctx.log(f"下载 MAAOrch {tag}...")
            import tempfile, shutil, zipfile, os as _os
            try:
                # Find a download URL (prefer zipball)
                dl_url = r.get("html_url", "") + "/archive/refs/tags/" + tag + ".zip"
                if not r.get("html_url"):
                    dl_url = f"https://github.com/xiachk083-hub/MAAOrch/archive/refs/tags/{tag}.zip"

                tmp = tempfile.mkdtemp()
                tmpf = Path(tmp) / "update.zip"
                self.ctx.log(f"下载中: {dl_url[:60]}...")
                req = urllib.request.Request(dl_url, headers={"User-Agent": "MAAOrch-Updater"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    total = int(resp.headers.get("Content-Length", 0))
                    data = bytearray()
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        data.extend(chunk)
                        if total:
                            pct = len(data) * 100 // total
                            self.ctx.log(f"下载中: {pct}% ({len(data)//1024//1024}MB/{total//1024//1024}MB)")
                    tmpf.write_bytes(data)

                # Extract to _update/
                root = Path(__file__).parent
                update_dir = root / "_update"
                if update_dir.exists():
                    shutil.rmtree(str(update_dir))
                update_dir.mkdir()
                with zipfile.ZipFile(str(tmpf)) as zf:
                    # GitHub zip wraps in a folder like MAAOrch-1.1.0/
                    for member in zf.namelist():
                        p = Path(member)
                        if any(part == ".." for part in p.parts) or p.is_absolute():
                            raise ValueError(f"zip slip: {member}")
                        # Strip the top-level folder
                        parts = member.split("/", 1)
                        if len(parts) < 2:
                            continue
                        target_path = update_dir / parts[1]
                        if member.endswith("/"):
                            target_path.mkdir(parents=True, exist_ok=True)
                        else:
                            target_path.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(member) as src, open(str(target_path), "wb") as dst:
                                dst.write(src.read())

                # Generate replace.bat
                bat = root / "replace.bat"
                bat.write_text(
                    '@echo off\r\n'
                    'taskkill /f /fi "WINDOWTITLE eq MAAOrch" 2>nul\r\n'
                    'timeout /t 3 /nobreak >nul\r\n'
                    'xcopy /E /Y "%~dp0_update\\*" "%~dp0" >nul\r\n'
                    'rmdir /S /Q "%~dp0_update"\r\n'
                    'start "" python "%~dp0main.pyw"\r\n'
                    'del "%~dp0replace.bat"\r\n'
                )
                shutil.rmtree(tmp)

                self.ctx.log(f"更新准备完成，重启中...")
                import subprocess as _sp
                _sp.Popen(str(bat), shell=True, creationflags=0x08000000)  # CREATE_NO_WINDOW
                self.ctx._mw.close()
            except Exception as e:
                self.ctx.log(f"下载更新失败: {e}")
                QMessageBox.warning(self.ctx._mw, "更新失败", f"下载或解压失败:\n{e}")

        t = OrchUpdateCheckThread()
        t.result_ready.connect(_on_result)
        t.start()




