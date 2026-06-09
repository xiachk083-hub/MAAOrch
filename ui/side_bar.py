"""Sidebar — status filter + quick access."""
from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame)


def build_side_bar(mw: Any) -> QFrame:
    sb = QFrame()
    sb.setObjectName("sideBar")
    sb.setFixedWidth(140)

    vl = QVBoxLayout(sb)
    vl.setContentsMargins(6, 8, 6, 6)
    vl.setSpacing(2)

    # Header
    hdr = QLabel("  总览")
    hdr.setStyleSheet("font-weight:bold;font-size:10pt;color:#666;padding:6px 4px 8px 4px;border-radius:4px")
    hdr.setCursor(Qt.PointingHandCursor)
    hdr.mousePressEvent = lambda e: _filter_click(mw, "")
    mw._overview_header = hdr

    # Status filter items
    status_items = [
        ("▶ 运行中", "running"),
        ("⏳ 排队中", "waiting"),
        ("❌ 错误", "error"),
        ("⏸ 暂停", "paused"),
    ]
    mw._side_labels = {}
    mw._side_frames = {}
    for label, key in status_items:
        frame = QFrame()
        frame.setStyleSheet("QFrame{border:none;border-left:0px solid transparent;padding:0}")
        fl = QHBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(f"  {label}  0")
        lbl.setStyleSheet("color:#666;font-size:9pt;padding:5px 8px;border-radius:4px;background:transparent")
        lbl.setCursor(Qt.PointingHandCursor)
        lbl.mousePressEvent = lambda e, k=key: _filter_click(mw, k)
        fl.addWidget(lbl)
        vl.addWidget(frame)
        mw._side_labels[key] = lbl
        mw._side_frames[key] = frame

    vl.addStretch()

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


def _toggle_smart(mw: Any, enabled: bool) -> None:
    sg = mw.config.setdefault("smart_global", {})
    sg["enabled"] = enabled
    mw._save()
    if enabled:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, lambda: (setattr(mw, "_last_smart_minute", ""), mw._smart_tick()))


def _run_smart_all(mw: Any) -> None:
    if not mw.config.get("smart_global", {}).get("enabled", False):
        mw._log("智能调度未启用")
        return
    if hasattr(mw, "launch_queue") and mw.launch_queue:
        with mw.launch_queue._lock:
            mw.launch_queue._pending.clear()
            mw.launch_queue._active_emus.clear()
        mw.launch_queue._save_queue()
    mw._smart_force = True
    setattr(mw, "_last_smart_minute", "")
    mw._smart_tick()
    mw._smart_force = False


def _filter_click(mw: Any, key: str) -> None:
    """Set filter on smart_panel table."""
    if hasattr(mw, '_set_smart_filter'):
        mw._set_smart_filter(key)
    active = getattr(mw, '_smart_filter', '')

    # Highlight overview header when active (no filter)
    ov = getattr(mw, '_overview_header', None)
    if ov:
        if not active:
            ov.setStyleSheet("font-weight:bold;font-size:10pt;color:#e6e6e6;padding:6px 4px 8px 4px;border-radius:4px;background:#49820515")
        else:
            ov.setStyleSheet("font-weight:bold;font-size:10pt;color:#666;padding:6px 4px 8px 4px;border-radius:4px;background:transparent")

    for k in mw._side_labels:
        lbl = mw._side_labels[k]
        frame = mw._side_frames.get(k)
        if k == active:
            lbl.setStyleSheet("color:#e6e6e6;font-size:9pt;padding:5px 8px;border-radius:4px;background:#49820515")
            if frame:
                frame.setStyleSheet("QFrame{border:none;border-left:4px solid #498205;padding:0;margin-left:2px}")
        else:
            lbl.setStyleSheet("color:#666;font-size:9pt;padding:5px 8px;border-radius:4px;background:transparent")
            if frame:
                frame.setStyleSheet("QFrame{border:none;border-left:0px solid transparent;padding:0}")
