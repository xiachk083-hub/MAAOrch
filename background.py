"""Reusable QThread helper — eliminates inline class _T(QThread) boilerplate."""
import sys
from collections.abc import Callable
from typing import Any
from PySide6.QtCore import QThread, Signal


class BackgroundTask(QThread):
    """Run a function in background thread. Connect 'result' signal to handle output."""
    result = Signal(object)  # pyright: ignore[reportCallIssue]

    def __init__(self, func: Callable[[], Any]) -> None:
        super().__init__()
        self._func = func

    def run(self):
        try:
            data = self._func()
            self.result.emit(data)
        except Exception as e:
            sys.excepthook(type(e), e, e.__traceback__)
