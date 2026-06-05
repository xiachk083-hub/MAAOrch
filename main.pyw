import sys,os,ctypes,traceback,io
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent))

# ── Error logging ──
CRASH_LOG = Path(__file__).parent / "debug.log"

def _write_crash(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
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
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)

def _qt_message_handler(mode, context, message):
    """Catch Qt warnings and errors."""
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
    _write_crash("═══ MAAOrch 启动 ═══")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
