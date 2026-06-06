"""Queue main view — running status, waiting queue, quick enqueue, history."""
from __future__ import annotations
from typing import Any
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSplitter, QComboBox,
)


def build_queue_panel(mw: Any) -> QWidget:
    """Build the queue view."""
    mw.qv = QWidget()
    qvl = QVBoxLayout(mw.qv)
    qvl.setContentsMargins(6, 6, 6, 6)
    qvl.setSpacing(8)

    # ── Quick enqueue bar ──
    enq_row = QHBoxLayout()
    enq_row.addWidget(QLabel("快速入队:", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    mw._queue_combo = QComboBox()
    mw._queue_combo.setEditable(True)
    mw._queue_combo.setMinimumWidth(160)
    mw._queue_combo.setPlaceholderText("搜索账号名...")
    _rebuild_queue_combo(mw)
    mw._queue_combo.lineEdit().textChanged.connect(lambda t: _on_search(mw, t))
    enq_row.addWidget(mw._queue_combo, 1)
    enq_btn = QPushButton("▶ 入队")
    enq_btn.setObjectName("startBtn")
    enq_btn.clicked.connect(lambda: _enqueue_from_combo(mw))
    enq_row.addWidget(enq_btn)
    qvl.addLayout(enq_row)

    # ── Running section ──
    running_lbl = QLabel("▶ 运行中", font=QFont("Microsoft YaHei UI", 10, QFont.Bold))
    qvl.addWidget(running_lbl)
    mw._queue_running_tbl = QTableWidget(0, 3)
    mw._queue_running_tbl.setHorizontalHeaderLabels(["账号", "当前任务", ""])
    mw._queue_running_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw._queue_running_tbl.setColumnWidth(1, 80)
    mw._queue_running_tbl.setColumnWidth(2, 40)
    mw._queue_running_tbl.verticalHeader().setVisible(False)
    mw._queue_running_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    qvl.addWidget(mw._queue_running_tbl)

    # ── Waiting section ──
    wait_lbl = QLabel("⏳ 等待中", font=QFont("Microsoft YaHei UI", 10, QFont.Bold))
    qvl.addWidget(wait_lbl)
    mw._queue_waiting_tbl = QTableWidget(0, 4)
    mw._queue_waiting_tbl.setHorizontalHeaderLabels(["账号", "来源", "预计", ""])
    mw._queue_waiting_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw._queue_waiting_tbl.setColumnWidth(1, 50)
    mw._queue_waiting_tbl.setColumnWidth(2, 80)
    mw._queue_waiting_tbl.setColumnWidth(3, 40)
    mw._queue_waiting_tbl.verticalHeader().setVisible(False)
    mw._queue_waiting_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    qvl.addWidget(mw._queue_waiting_tbl)

    # ── History section ──
    qvl.addWidget(QLabel("📋 最近完成", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    mw._queue_hist_lbl = QLabel("暂无记录")
    mw._queue_hist_lbl.setStyleSheet("color:#888;padding:4px")
    mw._queue_hist_lbl.setWordWrap(True)
    qvl.addWidget(mw._queue_hist_lbl)
    qvl.addStretch()

    return mw.qv


def refresh_queue_view(mw: Any) -> None:
    """Update the queue panel with current state."""
    if not hasattr(mw, "_queue_running_tbl"):
        return

    import time
    now = datetime.now()

    # Rebuild combo on refresh (account list may have changed)
    if hasattr(mw, "_queue_combo") and not mw._queue_combo.hasFocus():
        _rebuild_queue_combo(mw)

    # Running table
    running = []
    if hasattr(mw, "runner"):
        for aid in mw.runner.active_ids():
            a = next((x for x in mw.accounts if x["id"] == aid), None)
            if not a:
                continue
            t = int(time.time() - mw.runner._start_times.get(aid, 0))
            task = _current_task_name(mw, aid)
            status_text = f"{task or '—'} ({t // 60}m{t % 60}s)"
            running.append((a["name"], status_text, aid))

    tbl = mw._queue_running_tbl
    tbl.setRowCount(len(running))
    for i, (name, status, aid) in enumerate(running):
        tbl.setItem(i, 0, QTableWidgetItem(name))
        tbl.setItem(i, 1, QTableWidgetItem(status))
        stop_btn = QPushButton("✕")
        stop_btn.setFixedSize(24, 24)
        stop_btn.setToolTip("停止")
        stop_btn.setStyleSheet("QPushButton{background:transparent;color:#d32f2f;border:none;font-weight:bold}QPushButton:hover{background:#d32f2f;color:#fff;border-radius:12px}")
        stop_btn.clicked.connect(lambda c, a=aid: (mw.runner.stop(a), refresh_queue_view(mw)))
        sw = QWidget()
        swl = QHBoxLayout(sw)
        swl.setContentsMargins(0, 0, 0, 0)
        swl.setAlignment(Qt.AlignCenter)
        swl.addWidget(stop_btn)
        tbl.setCellWidget(i, 2, sw)

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
    wt.setRowCount(len(waiting))
    for i, (name, src, when, aid) in enumerate(waiting):
        wt.setItem(i, 0, QTableWidgetItem(name))
        wt.setItem(i, 1, QTableWidgetItem(src))
        wt.setItem(i, 2, QTableWidgetItem(when))
        cancel_btn = QPushButton("✕")
        cancel_btn.setFixedSize(24, 24)
        cancel_btn.setToolTip("取消排队")
        cancel_btn.setStyleSheet("QPushButton{background:transparent;color:#888;border:none}QPushButton:hover{background:#d32f2f;color:#fff;border-radius:12px}")
        cancel_btn.clicked.connect(lambda c, a=aid: (mw.launch_queue.dequeue(a), refresh_queue_view(mw)))
        sw = QWidget()
        swl = QHBoxLayout(sw)
        swl.setContentsMargins(0, 0, 0, 0)
        swl.setAlignment(Qt.AlignCenter)
        swl.addWidget(cancel_btn)
        wt.setCellWidget(i, 3, sw)


def _rebuild_queue_combo(mw: Any) -> None:
    """Rebuild the search combo with all account names."""
    if not hasattr(mw, "_queue_combo"):
        return
    cur = mw._queue_combo.currentText()
    mw._queue_combo.blockSignals(True)
    mw._queue_combo.clear()
    for a in mw.accounts:
        status = ""
        if hasattr(mw, "launch_queue"):
            if mw.launch_queue.is_running(a["id"]):
                status = "▶"
            elif mw.launch_queue.is_queued(a["id"]):
                status = "⏳"
        mw._queue_combo.addItem(f"{status} {a.get('name', '')}  [{a.get('game_client', '')}]", a["id"])
    mw._queue_combo.setCurrentText(cur)
    mw._queue_combo.blockSignals(False)


def _on_search(mw: Any, text: str) -> None:
    """Filter the combo dropdown based on search text."""
    combo = mw._queue_combo
    combo.blockSignals(True)
    combo.clear()
    ft = text.strip().lower()
    for a in mw.accounts:
        if ft and ft not in a.get("name", "").lower() and ft not in a.get("game_client", "").lower():
            continue
        status = ""
        if hasattr(mw, "launch_queue"):
            if mw.launch_queue.is_running(a["id"]):
                status = "▶"
            elif mw.launch_queue.is_queued(a["id"]):
                status = "⏳"
        combo.addItem(f"{status} {a.get('name', '')}  [{a.get('game_client', '')}]", a["id"])
    if combo.count() > 0:
        combo.showPopup()
    combo.blockSignals(False)


def _enqueue_from_combo(mw: Any) -> None:
    """Enqueue the selected account from the search combo."""
    combo = mw._queue_combo
    aid = combo.currentData()
    if not aid:
        # Try by name
        text = combo.currentText().strip()
        for a in mw.accounts:
            if a.get("name", "") == text:
                aid = a["id"]
                break
    if aid:
        mw.launch_queue.enqueue(aid, "manual", priority=0)
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
