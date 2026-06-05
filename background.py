"""Reusable QThread helper — eliminates inline class _T(QThread) boilerplate."""
from PySide6.QtCore import QThread, Signal


class BackgroundTask(QThread):
    """Run a function in background thread. Connect 'result' signal to handle output.
    
    Usage:
        t = BackgroundTask(detect_emu_instances)
        t.result.connect(on_done)
        t.start()
    
    Or with args:
        t = BackgroundTask(lambda: subprocess.run(...))
    """
    result = Signal(object)  # pyright: ignore[reportCallIssue]

    def __init__(self, func):
        super().__init__()
        self._func = func

    def run(self):
        try:
            data = self._func()
            self.result.emit(data)
        except Exception as e:
            self.result.emit(e)
