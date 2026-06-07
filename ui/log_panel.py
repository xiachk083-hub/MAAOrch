"""Log panel builder — extracted from main_window.py."""
from __future__ import annotations
from typing import Any
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit)


def build_log_panel(mw: Any) -> QWidget:
    mw.lv = QWidget()
    lvl = QVBoxLayout(mw.lv)
    lvl.setContentsMargins(4, 4, 4, 4)
    lvl.setSpacing(4)
    lvl.addWidget(QLabel("📋 运行日志", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    mw.log_text = QPlainTextEdit()
    mw.log_text.setReadOnly(True)
    mw.log_text.setMaximumBlockCount(2000)
    lvl.addWidget(mw.log_text, 1)
    log_btn_row = QHBoxLayout()
    clear_btn = QPushButton("清空")
    clear_btn.clicked.connect(lambda: mw.log_text.clear())
    log_btn_row.addWidget(clear_btn)
    log_btn_row.addStretch()
    lvl.addLayout(log_btn_row)
    mw.lv.hide()
    return mw.lv
