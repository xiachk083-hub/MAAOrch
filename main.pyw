import sys,os,ctypes
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from utils import is_admin, run_as_admin
from main_window import MainWindow

if __name__ == "__main__":
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
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
