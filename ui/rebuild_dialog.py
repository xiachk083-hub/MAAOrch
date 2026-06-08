"""MAA instance rebuild dialog with progress bar."""
from __future__ import annotations
from typing import Any, Callable
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
)


class RebuildDialog(QDialog):
    def __init__(self, parent: Any, total: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("重建实例")
        self.setFixedSize(380, 160)
        self._cancelled = False
        vl = QVBoxLayout(self)
        vl.setSpacing(10)

        vl.addWidget(QLabel("正在重建 MAA 实例池...",
                            font=QFont("Microsoft YaHei UI", 12, QFont.Bold)))

        self._bar = QProgressBar()
        self._bar.setMinimum(0)
        self._bar.setMaximum(total)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        self._bar.setFixedHeight(24)
        vl.addWidget(self._bar)

        self._info = QLabel(f"实例 0/{total}")
        self._info.setStyleSheet("color:#888")
        vl.addWidget(self._info)

        btn_row = QHBoxLayout()
        self._bg_btn = QPushButton("后台运行")
        self._bg_btn.clicked.connect(self._go_background)
        btn_row.addStretch()
        btn_row.addWidget(self._bg_btn)
        vl.addLayout(btn_row)

    def update(self, current: int, total: int) -> None:
        if self._cancelled:
            return
        self._bar.setValue(current)
        self._info.setText(f"实例 {current}/{total}")
        self._bar.setFormat(f"{current}/{total}")

    def _go_background(self) -> None:
        self._cancelled = True
        self.hide()
