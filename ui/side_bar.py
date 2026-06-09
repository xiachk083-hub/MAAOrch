"""Sidebar — status filter + quick access."""
from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QFrame)


def build_side_bar(mw: Any) -> QFrame:
    sb = QFrame()
    sb.setObjectName("sideBar")
    sb.setFixedWidth(140)

    vl = QVBoxLayout(sb)
    vl.setContentsMargins(6, 8, 6, 6)
    vl.setSpacing(2)

    # Header
    hdr = QLabel("  总览")
    hdr.setStyleSheet("font-weight:bold;font-size:10pt;color:#666;padding:6px 4px 8px 4px")
    vl.addWidget(hdr)

    # Status filter items
    status_items = [
        ("▶ 运行中", "running"),
        ("⏳ 排队中", "waiting"),
        ("❌ 错误", "error"),
        ("⏸ 暂停", "paused"),
    ]
    mw._side_labels = {}
    for label, key in status_items:
        lbl = QLabel(f"  {label}  0")
        lbl.setStyleSheet("color:#666;font-size:9pt;padding:5px 8px;border-radius:4px")
        lbl.setCursor(Qt.PointingHandCursor)
        lbl.mousePressEvent = lambda e, k=key: _filter_click(mw, k)
        vl.addWidget(lbl)
        mw._side_labels[key] = lbl

    vl.addStretch()

    # Quick access
    for icon, text, mod in [("📋", "日志", "log_window"), ("⚙", "设置", "settings_window")]:
        lbl = QLabel(f"  {icon} {text}")
        lbl.setStyleSheet("color:#555;font-size:9pt;padding:5px 8px;border-radius:4px")
        lbl.setCursor(Qt.PointingHandCursor)
        def _open(e=None, m=mod):
            if m == "log_window":
                from ui.log_window import show_log_window
                show_log_window(mw)
            else:
                from ui.settings_window import open_settings
                open_settings(mw)
        lbl.mousePressEvent = _open
        vl.addWidget(lbl)

    # Refresh timer for counts
    def _refresh_counts():
        if not mw._side_labels:
            return
        runner = getattr(mw, "runner", None)
        lq = getattr(mw, "launch_queue", None)
        running = len(runner._active) if runner else 0
        waiting = len(lq._pending) if lq else 0
        errors = sum(1 for a in mw.accounts if a.get("consecutive_failures", 0) >= 1 and a.get("consecutive_failures", 0) < 6)
        paused = sum(1 for a in mw.accounts if a.get("consecutive_failures", 0) >= 6)
        mw._side_labels["running"].setText(f"  ▶ 运行中  {running}")
        mw._side_labels["waiting"].setText(f"  ⏳ 排队中  {waiting}")
        mw._side_labels["error"].setText(f"  ❌ 错误  {errors}")
        mw._side_labels["paused"].setText(f"  ⏸ 暂停  {paused}")

    timer = QTimer(sb)
    timer.timeout.connect(_refresh_counts)
    timer.start(3000)
    _refresh_counts()

    return sb


def _filter_click(mw: Any, key: str) -> None:
    """Set filter on smart_panel table."""
    if hasattr(mw, '_set_smart_filter'):
        mw._set_smart_filter(key)
    for k, lbl in mw._side_labels.items():
        if k == key:
            lbl.setStyleSheet("color:#498205;font-weight:bold;font-size:9pt;padding:5px 8px;border-radius:4px;background:#49820515")
        else:
            lbl.setStyleSheet("color:#666;font-size:9pt;padding:5px 8px;border-radius:4px")
