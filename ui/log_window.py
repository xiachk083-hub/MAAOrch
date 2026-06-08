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
    # Load existing logs
    lp = Path(__file__).parent.parent / "debug.log"
    if lp.exists():
        try:
            data = lp.read_text(encoding="utf-8")
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
    clear_btn = QPushButton("🗑 清空")
    clear_btn.clicked.connect(lambda: log_text.clear())
    btn_row.addWidget(clear_btn)
    vl.addLayout(btn_row)

    # Auto-refresh from debug.log
    def _refresh():
        if not d.isVisible():
            return
        try:
            lp2 = Path(__file__).parent.parent / "debug.log"
            if lp2.exists():
                data = lp2.read_text(encoding="utf-8")
                log_text.setPlainText(data)
                if auto_scroll.isChecked():
                    log_text.verticalScrollBar().setValue(log_text.verticalScrollBar().maximum())
        except Exception:
            pass

    timer = QTimer(d)
    timer.timeout.connect(_refresh)
    timer.start(2000)
    d.finished.connect(timer.stop)
    d.exec()
