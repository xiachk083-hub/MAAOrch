"""Sidebar — status filter + quick access."""
from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QRadioButton, QButtonGroup)
from services.dispatch_pool import create_dispatch, remove_dispatch


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
        (" 运行中", "running"),
        (" 排队中", "waiting"),
        (" 错误", "error"),
        (" 暂停", "paused"),
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

    # Schedule mode selector
    mode_sep = QFrame()
    mode_sep.setFrameShape(QFrame.HLine); mode_sep.setStyleSheet("color:#333;margin:4px 0")
    vl.addWidget(mode_sep)
    mode_lbl = QLabel("  调度模式")
    mode_lbl.setStyleSheet("color:#888;font-size:8pt;padding:2px 4px")
    vl.addWidget(mode_lbl)
    mode_group = QButtonGroup(sb)
    mode_btns = {}
    for txt, key in [("日常", "daily"), ("肉鸽", "roguelike"), ("生息", "reclamation")]:
        rb = QRadioButton(f"  {txt}")
        rb.setStyleSheet("color:#aaa;font-size:8pt;padding:2px 6px")
        if mw.config.get("schedule_mode", "daily") == key:
            rb.setChecked(True)
        mode_group.addButton(rb, {"daily":0,"roguelike":1,"reclamation":2}[key])
        vl.addWidget(rb)
        mode_btns[key] = rb
    def _on_mode_changed(btn_id: int):
        mode_map = {0:"daily", 1:"roguelike", 2:"reclamation"}
        new_mode = mode_map.get(btn_id, "daily")
        mw.config["schedule_mode"] = new_mode
        mw._save()
        mw._log(f"[模式] 切换为 {new_mode}")
        # Trigger smart scheduler if enabled
        sg = mw.config.get("smart_global", {})
        if sg.get("enabled", False):
            QTimer.singleShot(500, lambda: (setattr(mw, "_last_smart_minute", ""), mw._smart_tick()))
    mode_group.idClicked.connect(_on_mode_changed)

    mw._mode_group = mode_group
    mw._mode_btns = mode_btns

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
        mw._side_labels["running"].setText(f"  运行中  {running}")
        mw._side_labels["waiting"].setText(f"  排队中  {waiting}")
        mw._side_labels["error"].setText(f"  错误  {errors}")
        mw._side_labels["paused"].setText(f"  暂停  {paused}")

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


def _run_smart_all(mw: Any, include_anni: bool = True) -> None:
    if hasattr(mw, "launch_queue") and mw.launch_queue:
        with mw.launch_queue._lock:
            mw.launch_queue._pending.clear()
            mw.launch_queue._active_emus.clear()
        mw.launch_queue._save_queue()
    mode = mw.config.get("schedule_mode", "daily")
    if mode == "daily":
        tasks = ["StartUp"]
        if include_anni:
            tasks.append("Annihilation")
        tasks.extend(["Fight", "Infrast", "Recruit", "Mall", "Award"])
    elif mode == "roguelike":
        tasks = ["StartUp", "Roguelike"]
    elif mode == "reclamation":
        tasks = ["StartUp", "Reclamation"]
    else:
        tasks = ["StartUp", "Award"]
    plan = ",".join(tasks)
    label_map = {"daily":"日常", "roguelike":"肉鸽", "reclamation":"生息"}
    count = 0
    for a in mw.accounts:
        aid = a.get("id", "")
        if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
            continue
        if mw.launch_queue.is_queued(aid) or mw.launch_queue.is_running(aid):
            continue
        task_list = tasks
        a["dispatch_id"] = create_dispatch(task_list)
        mw.launch_queue.enqueue(aid, "force", priority=0)
        count += 1
    if count:
        mw._log(f"▶ {label_map.get(mode, '')}调度: {count} 个账号已入队")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, mw.launch_queue.tick)


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
