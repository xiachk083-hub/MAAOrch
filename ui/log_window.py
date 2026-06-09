from __future__ import annotations
from typing import Any
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QCheckBox,
                               QPushButton, QPlainTextEdit, QLabel)


def show_log_window(mw: Any) -> None:
    d = QDialog(mw)
    d.setWindowTitle("📋 MAAOrch 日志")
    d.setMinimumSize(600, 400)
    d.resize(700, 450)
    vl = QVBoxLayout(d)
    vl.setContentsMargins(6, 6, 6, 6)

    log_text = QPlainTextEdit()
    log_text.setReadOnly(True)
    log_text.setMaximumBlockCount(2000)
    log_text.setFont(QFont("Consolas", 9))
    # Auto-refresh from debug.log (append only)
    _last_pos = 0

    def _refresh():
        nonlocal _last_pos
        if not d.isVisible():
            return
        try:
            lp2 = Path(__file__).parent.parent / "debug.log"
            if not lp2.exists():
                return
            size = lp2.stat().st_size
            if size <= _last_pos:
                return
            with lp2.open("rb") as f:
                f.seek(_last_pos)
                new_data = f.read(size - _last_pos).decode("utf-8", errors="replace")
                _last_pos = size
                if new_data:
                    log_text.appendPlainText(new_data)
                    if auto_scroll.isChecked():
                        log_text.verticalScrollBar().setValue(log_text.verticalScrollBar().maximum())
        except Exception:
            pass

    # Initial load
    try:
        lp_initial = Path(__file__).parent.parent / "debug.log"
        if lp_initial.exists():
            _last_pos = lp_initial.stat().st_size
            if _last_pos < 50000:
                # Small file: load entirely
                data = lp_initial.read_text(encoding="utf-8")
                log_text.setPlainText(data)
            else:
                # Large file: load last 50000 bytes
                with lp_initial.open("rb") as f:
                    f.seek(max(0, _last_pos - 50000))
                    data = f.read().decode("utf-8", errors="replace")
                log_text.setPlainText(data)
            log_text.verticalScrollBar().setValue(log_text.verticalScrollBar().maximum())
    except Exception:
        pass

    vl.addWidget(log_text, 1)

    btn_row = QHBoxLayout()
    auto_scroll = QCheckBox("自动滚动")
    auto_scroll.setChecked(True)
    btn_row.addWidget(auto_scroll)
    btn_row.addStretch()
    clear_btn = QPushButton("清空")
    clear_btn.clicked.connect(lambda: log_text.clear())
    btn_row.addWidget(clear_btn)
    vl.addLayout(btn_row)

    timer = QTimer(d)
    timer.timeout.connect(_refresh)
    timer.start(2000)
    d.finished.connect(timer.stop)
    d.exec()
