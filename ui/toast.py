"""Non-modal toast notification — auto-dismiss after 3s."""
from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QHBoxLayout


class Toast(QFrame):
    """Small popup notification, auto-disappears."""

    @staticmethod
    def show(parent: Any, text: str, icon: str = "✅", duration_ms: int = 3000) -> None:
        t = Toast(parent, text, icon, duration_ms)
        t.show()

    def __init__(self, parent: Any, text: str, icon: str = "✅", duration_ms: int = 3000) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("""
            QFrame {
                background: #333;
                border: 1px solid #555;
                border-radius: 8px;
                padding: 10px 16px;
            }
            QLabel { color: #eee; font-size: 9pt; background: transparent; border: none; }
        """)
        hl = QHBoxLayout(self)
        hl.setContentsMargins(12, 8, 12, 8)
        hl.setSpacing(8)
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size:11pt;background:transparent;border:none")
        hl.addWidget(icon_lbl)
        txt = QLabel(text)
        txt.setStyleSheet("background:transparent;border:none")
        hl.addWidget(txt)

        # Position at bottom-right of parent
        self.adjustSize()
        if parent:
            parent_rect = parent.geometry()
            self.move(parent_rect.right() - self.width() - 20,
                      parent_rect.bottom() - self.height() - 40)

        # Fade out and close
        QTimer.singleShot(duration_ms, self._fade_out)

    def _fade_out(self) -> None:
        self.deleteLater()
