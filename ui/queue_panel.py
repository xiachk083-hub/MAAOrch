"""Queue main view — the primary interface for launching and monitoring accounts."""
from __future__ import annotations
from typing import Any
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSplitter, QCheckBox, QGroupBox,
)


def build_queue_panel(mw: Any) -> QWidget:
    """Build the queue main view — left: account list, right: queue status."""
    mw.qv = QWidget()
    qvl = QVBoxLayout(mw.qv)
    qvl.setContentsMargins(0, 0, 0, 0)

    sp = QSplitter(Qt.Horizontal)

    # ── Left: account list ──
    left = QWidget()
    left.setMinimumWidth(200)
    ll = QVBoxLayout(left)
    ll.setContentsMargins(4, 4, 4, 4)
    ll.addWidget(QLabel("账号", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))

    # Checkboxes + enqueue
    mw._queue_checks = {}
    cb_widget = QWidget()
    cb_layout = QVBoxLayout(cb_widget)
    cb_layout.setContentsMargins(0, 4, 0, 4)
    cb_layout.setSpacing(2)

    for i, a in enumerate(mw.accounts):
        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(2, 2, 2, 2)
        row_l.setSpacing(4)

        # Status indicator
        status_lbl = QLabel("●")
        status_lbl.setFixedWidth(16)
        status_lbl.setStyleSheet("color:#888")
        mw._queue_checks[a["id"]] = {"status": status_lbl}
        row_l.addWidget(status_lbl)

        cb = QCheckBox(a.get("name", ""))
        row_l.addWidget(cb, 1)

        # Quick launch button per account
        launch_btn = QPushButton("▶")
        launch_btn.setFixedSize(26, 22)
        launch_btn.setStyleSheet("QPushButton{background:#2b7a3a;color:#fff;border:none;border-radius:3px}QPushButton:hover{background:#1e5a28}")
        row_idx = i
        launch_btn.clicked.connect(lambda c, r=row_idx: _quick_launch(mw, r))
        row_l.addWidget(launch_btn)

        cb_layout.addWidget(row_w)

    ll.addWidget(cb_widget, 1)

    # Batch enqueue button
    btn_row = QHBoxLayout()
    select_all_btn = QPushButton("全选")
    select_all_btn.clicked.connect(lambda: _select_all(mw, True))
    btn_row.addWidget(select_all_btn)
    deselect_btn = QPushButton("清空")
    deselect_btn.clicked.connect(lambda: _select_all(mw, False))
    btn_row.addWidget(deselect_btn)
    btn_row.addStretch()
    enqueue_btn = QPushButton("▶ 入队")
    enqueue_btn.setObjectName("startBtn")
    enqueue_btn.setMinimumHeight(30)
    enqueue_btn.clicked.connect(lambda: _batch_enqueue(mw))
    btn_row.addWidget(enqueue_btn)
    ll.addLayout(btn_row)
    sp.addWidget(left)

    # ── Right: queue status ──
    right = QWidget()
    rl = QVBoxLayout(right)
    rl.setContentsMargins(4, 4, 4, 4)

    # Running section
    running_grp = QGroupBox("▶ 运行中")
    rgl = QVBoxLayout(running_grp)
    mw._queue_running_tbl = QTableWidget(0, 4)
    mw._queue_running_tbl.setHorizontalHeaderLabels(["账号", "当前任务", "时长", ""])
    mw._queue_running_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw._queue_running_tbl.setColumnWidth(1, 80)
    mw._queue_running_tbl.setColumnWidth(2, 60)
    mw._queue_running_tbl.setColumnWidth(3, 40)
    mw._queue_running_tbl.verticalHeader().setVisible(False)
    mw._queue_running_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    mw._queue_running_tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
    rgl.addWidget(mw._queue_running_tbl)
    rl.addWidget(running_grp)

    # Queued section
    queue_grp = QGroupBox("⏳ 等待中")
    qgl = QVBoxLayout(queue_grp)
    mw._queue_waiting_tbl = QTableWidget(0, 4)
    mw._queue_waiting_tbl.setHorizontalHeaderLabels(["账号", "来源", "预计", ""])
    mw._queue_waiting_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw._queue_waiting_tbl.setColumnWidth(1, 50)
    mw._queue_waiting_tbl.setColumnWidth(2, 80)
    mw._queue_waiting_tbl.setColumnWidth(3, 40)
    mw._queue_waiting_tbl.verticalHeader().setVisible(False)
    mw._queue_waiting_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    mw._queue_waiting_tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
    qgl.addWidget(mw._queue_waiting_tbl)
    rl.addWidget(queue_grp)

    # History section
    hist_grp = QGroupBox("📋 最近完成")
    hgl = QVBoxLayout(hist_grp)
    mw._queue_hist_lbl = QLabel("暂无记录")
    mw._queue_hist_lbl.setStyleSheet("color:#888")
    hgl.addWidget(mw._queue_hist_lbl)
    rl.addWidget(hist_grp)

    sp.addWidget(right)
    sp.setStretchFactor(1, 1)
    sp.setSizes([240, 700])
    qvl.addWidget(sp, 1)

    return mw.qv


def refresh_queue_view(mw: Any) -> None:
    """Update the queue panel with current state. Called by poll timer."""
    if not hasattr(mw, "_queue_running_tbl"):
        return

    now = datetime.now()
    import time

    # Update status indicators in account list
    if hasattr(mw, "_queue_checks"):
        for aid, widgets in mw._queue_checks.items():
            if hasattr(mw, "launch_queue") and mw.launch_queue.is_running(aid):
                widgets["status"].setText("▶")
                widgets["status"].setStyleSheet("color:#4a4;font-weight:bold")
            elif hasattr(mw, "launch_queue") and mw.launch_queue.is_queued(aid):
                widgets["status"].setText("⏳")
                widgets["status"].setStyleSheet("color:#c90;font-weight:bold")
            else:
                widgets["status"].setText("●")
                widgets["status"].setStyleSheet("color:#888")

    # Running table
    running = []
    if hasattr(mw, "runner"):
        for aid in mw.runner.active_ids():
            a = next((x for x in mw.accounts if x["id"] == aid), None)
            if not a:
                continue
            t = int(time.time() - mw.runner._start_times.get(aid, 0))
            task = _current_task_name(mw, aid)
            running.append((a["name"], task or "—", f"{t // 60}m{t % 60}s", aid))

    tbl = mw._queue_running_tbl
    tbl.setRowCount(max(1, len(running)) if running else 1)
    if running:
        for i, (name, task, duration, aid) in enumerate(running):
            tbl.setItem(i, 0, QTableWidgetItem(name))
            tbl.setItem(i, 1, QTableWidgetItem(task))
            tbl.setItem(i, 2, QTableWidgetItem(duration))
            stop_btn = QPushButton("✕")
            stop_btn.setFixedSize(22, 22)
            stop_btn.setStyleSheet("QPushButton{background:transparent;color:#d32f2f;border:none;font-weight:bold}QPushButton:hover{background:#d32f2f;color:#fff;border-radius:11px}")
            stop_btn.clicked.connect(lambda c, a=aid: (mw.runner.stop(a), refresh_queue_view(mw)))
            sw = QWidget()
            swl = QHBoxLayout(sw)
            swl.setContentsMargins(0, 0, 0, 0)
            swl.setAlignment(Qt.AlignCenter)
            swl.addWidget(stop_btn)
            tbl.setCellWidget(i, 3, sw)
    else:
        tbl.setItem(0, 0, QTableWidgetItem("—"))
        empty_item = QTableWidgetItem("无")
        empty_item.setForeground(Qt.gray)
        tbl.setItem(0, 1, empty_item)

    # Waiting table
    waiting = []
    if hasattr(mw, "launch_queue"):
        src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
        for e in sorted(mw.launch_queue._pending, key=lambda x: x.sort_key):
            a = next((x for x in mw.accounts if x["id"] == e.account_id), None)
            name = a["name"] if a else e.account_id[:8]
            when = ""
            if e.not_before > now:
                diff = int((e.not_before - now).total_seconds() / 60)
                when = f"{diff}分钟后" if diff < 60 else e.not_before.strftime("%H:%M")
            else:
                when = "等待空闲"
            waiting.append((name, src_map.get(e.source, e.source), when, e.account_id))

    wt = mw._queue_waiting_tbl
    wt.setRowCount(max(1, len(waiting)) if waiting else 1)
    if waiting:
        for i, (name, src, when, aid) in enumerate(waiting):
            wt.setItem(i, 0, QTableWidgetItem(name))
            wt.setItem(i, 1, QTableWidgetItem(src))
            wt.setItem(i, 2, QTableWidgetItem(when))
            cancel_btn = QPushButton("✕")
            cancel_btn.setFixedSize(22, 22)
            cancel_btn.setStyleSheet("QPushButton{background:transparent;color:#888;border:none}QPushButton:hover{background:#d32f2f;color:#fff;border-radius:11px}")
            cancel_btn.clicked.connect(lambda c, a=aid: (mw.launch_queue.dequeue(a), refresh_queue_view(mw)))
            sw = QWidget()
            swl = QHBoxLayout(sw)
            swl.setContentsMargins(0, 0, 0, 0)
            swl.setAlignment(Qt.AlignCenter)
            swl.addWidget(cancel_btn)
            wt.setCellWidget(i, 3, sw)
    else:
        wt.setItem(0, 0, QTableWidgetItem("—"))
        empty_item = QTableWidgetItem("无")
        empty_item.setForeground(Qt.gray)
        wt.setItem(0, 1, empty_item)


def _quick_launch(mw: Any, row: int) -> None:
    """Single account quick launch from queue panel."""
    if row < 0 or row >= len(mw.accounts):
        return
    aid = mw.accounts[row]["id"]
    mw.launch_queue.enqueue(aid, "manual", priority=0)
    mw.launch_queue._tick()


def _select_all(mw: Any, checked: bool) -> None:
    """Toggle all account checkboxes."""
    if hasattr(mw, "_queue_checks"):
        for aid, widgets in mw._queue_checks.items():
            for w in mw.qv.findChildren(QCheckBox):
                if w.text() == next((a.get("name", "") for a in mw.accounts if a["id"] == aid), ""):
                    w.setChecked(checked)
                    break


def _batch_enqueue(mw: Any) -> None:
    """Enqueue all checked accounts."""
    if not hasattr(mw, "_queue_checks"):
        return
    count = 0
    for a in mw.accounts:
        for cb in mw.qv.findChildren(QCheckBox):
            if cb.text() == a.get("name", "") and cb.isChecked():
                mw.launch_queue.enqueue(a["id"], "manual", priority=0)
                count += 1
                break
    mw._log(f"[队列] 批量入队 {count} 个账号")
    if count:
        mw.launch_queue._tick()
    refresh_queue_view(mw)


def _current_task_name(mw: Any, aid: str) -> str | None:
    """Read asst.log tail for current task name."""
    try:
        progs = mw.runner._progs.get(aid, [])
        if progs:
            lp = mw.logs.asst_log_path(progs[0])
            if lp.exists():
                last = lp.read_text(encoding="utf-8", errors="replace").strip().split("\n")[-5:]
                for line in last:
                    if "SubTaskStart" in line and "taskchain" in line:
                        import re, json
                        jm = re.search(r"\{.*\}", line)
                        if jm:
                            d = json.loads(jm.group(0))
                            tc = d.get("taskchain", "")
                            st = {"StartUp": "唤醒", "Fight": "刷关", "Recruit": "公招", "Infrast": "基建", "Mall": "信用", "Award": "奖励", "Roguelike": "肉鸽", "Reclamation": "生息"}
                            if tc in st:
                                return st[tc]
    except Exception:
        pass
    return None
