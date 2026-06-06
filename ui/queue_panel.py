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
    mw._queue_running_tbl = QTableWidget(0, 4)
    mw._queue_running_tbl.setHorizontalHeaderLabels(["账号", "当前任务", "", "👁"])
    mw._queue_running_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw._queue_running_tbl.setColumnWidth(1, 90)
    mw._queue_running_tbl.setColumnWidth(2, 40)
    mw._queue_running_tbl.setColumnWidth(3, 30)
    mw._queue_running_tbl.verticalHeader().setVisible(False)
    mw._queue_running_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    qvl.addWidget(mw._queue_running_tbl)

    # ── Waiting section ──
    wait_lbl = QLabel("⏳ 等待中", font=QFont("Microsoft YaHei UI", 10, QFont.Bold))
    qvl.addWidget(wait_lbl)
    mw._queue_waiting_tbl = QTableWidget(0, 5)
    mw._queue_waiting_tbl.setHorizontalHeaderLabels(["账号", "来源", "预计", "位置", ""])
    mw._queue_waiting_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw._queue_waiting_tbl.setColumnWidth(1, 50)
    mw._queue_waiting_tbl.setColumnWidth(2, 80)
    mw._queue_waiting_tbl.setColumnWidth(3, 50)
    mw._queue_waiting_tbl.setColumnWidth(4, 40)
    mw._queue_waiting_tbl.verticalHeader().setVisible(False)
    mw._queue_waiting_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    qvl.addWidget(mw._queue_waiting_tbl)

    # ── History section ──
    qvl.addWidget(QLabel("📋 最近完成", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    mw._queue_hist_tbl = QTableWidget(0, 3)
    mw._queue_hist_tbl.setHorizontalHeaderLabels(["账号", "任务", "结果"])
    mw._queue_hist_tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    mw._queue_hist_tbl.setColumnWidth(0, 80)
    mw._queue_hist_tbl.setColumnWidth(2, 60)
    mw._queue_hist_tbl.verticalHeader().setVisible(False)
    mw._queue_hist_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    qvl.addWidget(mw._queue_hist_tbl)
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
            idx = next((i for i, x in enumerate(mw.accounts) if x["id"] == aid), 0)
            running.append((a["name"], status_text, aid, idx))

    tbl = mw._queue_running_tbl
    tbl.setRowCount(len(running))
    for i, (name, status, aid, acc_idx) in enumerate(running):
        tbl.setItem(i, 0, QTableWidgetItem(name))
        tbl.setItem(i, 1, QTableWidgetItem(status))
        # Stop button
        stop_btn = QPushButton("✕")
        stop_btn.setFixedSize(24, 24)
        stop_btn.setToolTip("停止")
        stop_btn.setStyleSheet("QPushButton{background:transparent;color:#d32f2f;border:none;font-weight:bold}QPushButton:hover{background:#d32f2f;color:#fff;border-radius:12px}")
        stop_btn.clicked.connect(lambda c, a=aid: (mw.runner.stop(a), refresh_queue_view(mw)))
        sw = QWidget()
        swl = QHBoxLayout(sw); swl.setContentsMargins(0,0,0,0); swl.setAlignment(Qt.AlignCenter); swl.addWidget(stop_btn)
        tbl.setCellWidget(i, 2, sw)
        # Jump to account button
        eye_btn = QPushButton("👁")
        eye_btn.setFixedSize(20, 20)
        eye_btn.setToolTip("查看账号详情")
        eye_btn.setStyleSheet("QPushButton{background:transparent;color:#888;border:none;font-size:8pt}QPushButton:hover{color:#fff}")
        eye_btn.clicked.connect(lambda c, r=acc_idx: (_jump_to_account(mw, r)))
        sw2 = QWidget()
        swl2 = QHBoxLayout(sw2); swl2.setContentsMargins(0,0,0,0); swl2.setAlignment(Qt.AlignCenter); swl2.addWidget(eye_btn)
        tbl.setCellWidget(i, 3, sw2)

    # Waiting table
    waiting = []
    if hasattr(mw, "launch_queue"):
        src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
        pending = sorted(mw.launch_queue._pending, key=lambda x: x.sort_key)
        active_count = mw.launch_queue.active_count
        for pos, e in enumerate(pending):
            a = next((x for x in mw.accounts if x["id"] == e.account_id), None)
            name = a["name"] if a else e.account_id[:8]
            when = ""
            if e.not_before > now:
                diff = int((e.not_before - now).total_seconds() / 60)
                when = f"{diff}分钟后" if diff < 60 else e.not_before.strftime("%H:%M")
            else:
                when = "等待空闲"
            # Estimated wait: positions ahead × avg run time (~20min)
            est = f"#{pos + 1}"
            if active_count > 0:
                est += f" (~{20 * pos}min)"
            waiting.append((name, src_map.get(e.source, e.source), when, est, e.account_id))

    wt = mw._queue_waiting_tbl
    wt.setRowCount(len(waiting))
    for i, (name, src, when, pos, aid) in enumerate(waiting):
        wt.setItem(i, 0, QTableWidgetItem(name))
        wt.setItem(i, 1, QTableWidgetItem(src))
        wt.setItem(i, 2, QTableWidgetItem(when))
        wt.setItem(i, 3, QTableWidgetItem(pos))
        cancel_btn = QPushButton("✕")
        cancel_btn.setFixedSize(24, 24)
        cancel_btn.setToolTip("取消排队")
        cancel_btn.setStyleSheet("QPushButton{background:transparent;color:#888;border:none}QPushButton:hover{background:#d32f2f;color:#fff;border-radius:12px}")
        cancel_btn.clicked.connect(lambda c, a=aid: (mw.launch_queue.dequeue(a), refresh_queue_view(mw)))
        sw = QWidget()
        swl = QHBoxLayout(sw); swl.setContentsMargins(0,0,0,0); swl.setAlignment(Qt.AlignCenter); swl.addWidget(cancel_btn)
        wt.setCellWidget(i, 4, sw)

    # History table
    history = []
    try:
        from stats import RunStats
        for a in mw.accounts:
            st = RunStats(a["id"])
            runs = st._data.get("runs", [])
            for r in runs[-3:]:  # last 3 per account
                tasks_str = ",".join(r.get("tasks", {}).keys())
                done = sum(1 for v in r.get("tasks", {}).values() if v == "完成")
                total = len(r.get("tasks", {}))
                sanity = r.get("sanity", {})
                san_str = f" {sanity.get('current','?')}/{sanity.get('max','?')}" if sanity else ""
                history.append((r.get("ts", "")[-14:], a.get("name", ""), f"{done}/{total}", san_str))
    except Exception:
        pass
    history.sort(key=lambda x: x[0], reverse=True)
    ht = mw._queue_hist_tbl
    ht.setRowCount(min(len(history), 15))
    for i, (ts, name, result, san) in enumerate(history[:15]):
        ht.setItem(i, 0, QTableWidgetItem(name))
        ht.setItem(i, 1, QTableWidgetItem(f"{ts} {result}"))
        ht.setItem(i, 2, QTableWidgetItem(san[:20]))


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


def _jump_to_account(mw: Any, idx: int) -> None:
    """Switch to accounts tab and select the account."""
    mw._sw("accounts")
    if 0 <= idx < len(mw.accounts):
        for i in range(mw.at.rowCount()):
            it = mw.at.item(i, 0)
            if it and hasattr(it, "_acc_id") and it._acc_id == mw.accounts[idx]["id"]:
                mw.at.setCurrentCell(i, 0)
                break
