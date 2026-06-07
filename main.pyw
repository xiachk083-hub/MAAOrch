import sys,os,ctypes,traceback,io
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent))

# ── Error logging ──
CRASH_LOG = Path(__file__).parent / "debug.log"
_LOG_LOCK = __import__('threading').Lock()

def _write_crash(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _LOG_LOCK:
            with CRASH_LOG.open("a", encoding="utf-8") as f:
                f.write(f"\n[{ts}] [CRASH]\n{msg}\n")
    except Exception:
        pass

def _global_excepthook(exc_type, exc_value, exc_tb):
    """Catch all unhandled Python exceptions and log them."""
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _write_crash(msg)
    try:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "异常崩溃", f"未捕获的异常:\n\n{exc_value}\n\n详情请查看 debug.log")
    except Exception:
        sys.__stderr__.write(f"[CRASH] {msg}\n")
    sys.__excepthook__(exc_type, exc_value, exc_tb)

def _qt_message_handler(mode, context, message):
    """Only log Qt critical/fatal errors."""
    if mode > 2:
        _write_crash(f"Qt[{mode}]: {message}")

def _thread_excepthook(args):
    """Catch unhandled exceptions in QThreads."""
    msg = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    _write_crash(f"Thread crash:\n{msg}")

sys.excepthook = _global_excepthook

from utils import is_admin, run_as_admin, setup_proxy
from main_window import MainWindow

def main():
    if not is_admin() and "--no-elevate" not in sys.argv:
        run_as_admin()
        sys.exit(0)

    import ctypes as _ct
    hwnd = _ct.windll.user32.FindWindowW(None, "MAAOrch")
    if hwnd:
        _ct.windll.user32.ShowWindow(hwnd, 9)
        _ct.windll.user32.SetForegroundWindow(hwnd)
        sys.exit(0)

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import qInstallMessageHandler
    qInstallMessageHandler(_qt_message_handler)
    import threading
    threading.excepthook = _thread_excepthook

    setup_proxy()  # Auto-detect proxy before any network calls

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)

    def _cleanup_threads():
        threads = []
        for attr in ("_emu_monitor", "_api_server"):
            t = getattr(win, attr, None)
            if t:
                threads.append(t)
        for t in (getattr(win, "pipeline_thread", None), getattr(win, "update_thread", None)):
            if t:
                threads.append(t)
        if hasattr(win, "ctx") and win.ctx:
            st = win.ctx.schedule_thread
            if st:
                threads.append(st)
        if hasattr(win, "emu"):
            for attr in ("_t", "_scan_thread", "_refresh_t", "_test_t", "_ss_t", "_stopemu_t"):
                t = getattr(win.emu, attr, None)
                if t and t.isRunning():
                    threads.append(t)
        # Signal all threads to stop first, then wait in parallel
        for t in threads:
            try:
                if hasattr(t, "stop_server"): t.stop_server()
                elif hasattr(t, "stop_thread"): t.stop_thread()
                elif hasattr(t, "stop_monitor"): t.stop_monitor()
                else: t.quit()
            except Exception:
                pass
        # Wait with short timeout — don't block shutdown
        for t in threads:
            try: t.wait(1000)
            except Exception:
                pass

    win = MainWindow()
    app.aboutToQuit.connect(_cleanup_threads)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
