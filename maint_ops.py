from __future__ import annotations
import time, json, urllib.request
from pathlib import Path
from datetime import datetime
from typing import Any
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPixmap, QPainter, QColor, QBrush, QPolygonF, QIcon
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox, QApplication, QSystemTrayIcon, QMenu, QFileDialog
from utils import parse_maa_version, get_platform_key, _version_tuple, make_id
from dialogs import ScheduleDialog, SettingsDialog
from updater import UpdateCheckThread, UpdateDialog
from schedule_thread import ScheduleThread
from callbacks import ServiceContext


class MaintService:
    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def dl_maa(self, row: int) -> None:
        a = self.ctx.accounts[row]

        def oc(r):
            if not r.get("ok"):
                return
            tag = r["tag"]
            info = r["assets"].get(get_platform_key())
            if not info:
                return
            d = Path(__file__).parent / "accounts" / a["id"] / "MAA"
            d.mkdir(parents=True, exist_ok=True)
            dlg = UpdateDialog(self.ctx._mw, tag, info, str(d))
            if dlg.exec() != QDialog.Accepted:
                return
            exe = None
            for p in d.rglob("MAA.exe"):
                exe = p
                break
            if not exe:
                return
            e = {"id": make_id(), "path": str(exe), "args": [], "cwd": "", "env": {}, "maa_type": "maa", "maa_version": tag, "account_ref": a["id"], "launch_mode": "gui", "task_pipeline": "startup,fight,recruit,infrast,mall,award", "guard_enabled": True, "guard_max_restart": 3, "guard_capture_log": False}
            self.ctx.warehouse.append(e)
            self.ctx.save()
            self.ctx.show_dashboard(row)
            self.ctx.inject_config(e, a)
            self.ctx.launch_program(e)

        t = UpdateCheckThread()
        t.result_ready.connect(oc)
        self.ctx.update_thread = t
        t.start()

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
        self.ctx.update_thread = t
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
        if self.ctx.config.get("schedule", {}).get("enabled"):
            self.ctx.schedule_thread = ScheduleThread(self.ctx.config)
            self.ctx.schedule_thread.trigger.connect(self.ctx.start_pipeline)
            self.ctx.schedule_thread.start()

    def sch(self) -> None:
        d = ScheduleDialog(self.ctx._mw, self.ctx.config.get("schedule", {}))
        if d.exec() == QDialog.Accepted:
            self.ctx.config["schedule"] = d.r
            self.ctx.save()
        if self.ctx.schedule_thread:
            self.ctx.schedule_thread.stop_thread()
        if d.r.get("enabled"):
            self.ctx.schedule_thread = ScheduleThread(self.ctx.config)
            self.ctx.schedule_thread.trigger.connect(self.ctx.start_pipeline)
            self.ctx.schedule_thread.start()

    def settings(self) -> None:
        old_port = self.ctx.config.get("api_port", 19999)
        old_token = self.ctx.config.get("api_token", "")
        d = SettingsDialog(self.ctx._mw, self.ctx.config)
        if d.exec() == QDialog.Accepted:
            self.ctx.set_theme(self.ctx.config.get("appearance_mode", "Dark"))
            self.ctx.save()
            if self.ctx.config.get("api_port", 19999) != old_port or self.ctx.config.get("api_token", "") != old_token:
                self.ctx.restart_api_server()




