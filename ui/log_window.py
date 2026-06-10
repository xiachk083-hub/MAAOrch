from __future__ import annotations
import json
from typing import Any
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QCheckBox,
                                QPushButton, QPlainTextEdit, QLabel)


_LOG_FILE = Path(__file__).parent.parent / "events.log"


def show_log_window(mw: Any) -> None:
    d = QDialog(mw)
    d.setWindowTitle("MAAOrch 日志")
    d.setMinimumSize(600, 400)
    d.resize(700, 450)
    vl = QVBoxLayout(d)
    vl.setContentsMargins(6, 6, 6, 6)

    log_text = QPlainTextEdit()
    log_text.setReadOnly(True)
    log_text.setMaximumBlockCount(2000)
    log_text.setFont(QFont("Consolas", 9))
    _last_pos = 0

    def _read_events(from_pos: int) -> tuple[str, int]:
        """Read new events from events.log since from_pos. Returns (text, new_pos)."""
        if not _LOG_FILE.exists():
            return "", from_pos
        size = _LOG_FILE.stat().st_size
        if size <= from_pos:
            return "", from_pos
        with _LOG_FILE.open("rb") as f:
            f.seek(from_pos)
            raw = f.read(size - from_pos).decode("utf-8", errors="replace")
        lines = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                ts = ev.get("t", "")
                lv = ev.get("l", "INFO")
                src = ev.get("src", "?")
                msg = ev.get("msg", "")
                lines.append(f"[{ts}] [{lv:<5}] [{src}] {msg}")
            except Exception:
                lines.append(line)
        return "\n".join(lines) + ("\n" if lines else ""), size

    def _refresh():
        nonlocal _last_pos
        if not d.isVisible():
            return
        try:
            text, _last_pos = _read_events(_last_pos)
            if text:
                log_text.appendPlainText(text)
                if auto_scroll.isChecked():
                    log_text.verticalScrollBar().setValue(log_text.verticalScrollBar().maximum())
        except Exception:
            pass

    # Initial load
    try:
        text, _last_pos = _read_events(0)
        if text:
            log_text.setPlainText(text)
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
