"""Queue main view — running status, waiting queue, quick enqueue, history."""
from __future__ import annotations
from typing import Any
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from themes import BTN_DELETE
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QComboBox, QDialog, QGroupBox, QSpinBox,
)

_TABLE_STYLE = "QTableWidget{background:transparent;border:none;font-size:9pt} QTableWidget::item{color:#ccc;padding:1px 6px} QHeaderView::section{color:#888;background:transparent;border:none;border-bottom:1px solid #2b2b30;padding:3px 6px;font-size:9pt;font-weight:bold}"


def _safe_stop(mw: Any, aid: str) -> None:
    try:
        if hasattr(mw, "runner") and mw.runner:
            mw.runner.stop(aid)
    except Exception:
        pass


def _safe_dequeue(mw: Any, aid: str) -> None:
    try:
        if hasattr(mw, "launch_queue"):
            mw.launch_queue.dequeue(aid)
    except Exception:
        pass


def _safe_clear_queue(mw: Any) -> None:
    try:
        if hasattr(mw, "launch_queue") and mw.launch_queue:
            with mw.launch_queue._lock:
                mw.launch_queue._pending.clear()
            mw.launch_queue._save_queue()
    except Exception:
        pass


def build_queue_panel(mw: Any) -> QWidget:
    """Build the queue view."""
    mw.qv = QWidget()
    qvl = QVBoxLayout(mw.qv)
    qvl.setContentsMargins(6, 6, 6, 6)
    qvl.setSpacing(8)

    # ── Quick enqueue bar (no card) ──
    enq_row = QHBoxLayout()
    enq_row.addWidget(QLabel("⚡ 快速入队", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
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

    # ── Parallel limit ──
    pr_row = QHBoxLayout()
    pr_row.addWidget(QLabel("▶ 运行中", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    pr_row.addStretch()
    pr_row.addWidget(QLabel("上限并行:"))
    mw._parallel_sp = QSpinBox()
    mw._parallel_sp.setRange(1, 10)
    mw._parallel_sp.setValue(mw.config.get("parallel_max", 1))
    mw._parallel_sp.setFixedWidth(50)
    mw._parallel_sp.valueChanged.connect(lambda v: (mw.config.update({"parallel_max": v}), mw._save()))
    pr_row.addWidget(mw._parallel_sp)
    qvl.addLayout(pr_row)

    mw._queue_running_tbl = QTableWidget(0, 5)
    mw._queue_running_tbl.setHorizontalHeaderLabels(["账号", "当前任务", "计划", "", "👁"])
    mw._queue_running_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw._queue_running_tbl.setColumnWidth(1, 90)
    mw._queue_running_tbl.setColumnWidth(2, 110)
    mw._queue_running_tbl.setColumnWidth(3, 32)
    mw._queue_running_tbl.setColumnWidth(4, 28)
    mw._queue_running_tbl.verticalHeader().setVisible(False)
    mw._queue_running_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    mw._queue_running_tbl.setShowGrid(False)
    mw._queue_running_tbl.setAlternatingRowColors(True)
    mw._queue_running_tbl.verticalHeader().setDefaultSectionSize(28)
    mw._queue_running_tbl.setStyleSheet(_TABLE_STYLE)
    qvl.addWidget(mw._queue_running_tbl)

    # ── Waiting section ──
    wr = QHBoxLayout()
    wr.addWidget(QLabel("⏳ 等待中", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    wr.addStretch()
    clear_btn = QPushButton("清空")
    clear_btn.setStyleSheet("QPushButton{color:#888;border:1px solid #2b2b30;border-radius:4px;padding:4px 12px;font-size:9pt}QPushButton:hover{color:#e6e6e6;border-color:#555}")
    clear_btn.clicked.connect(lambda: _clear_queue(mw))
    wr.addWidget(clear_btn)
    qvl.addLayout(wr)
    mw._queue_waiting_tbl = QTableWidget(0, 5)
    mw._queue_waiting_tbl.setHorizontalHeaderLabels(["账号", "来源", "预计", "位置", ""])
    mw._queue_waiting_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw._queue_waiting_tbl.setColumnWidth(1, 45)
    mw._queue_waiting_tbl.setColumnWidth(2, 80)
    mw._queue_waiting_tbl.setColumnWidth(3, 55)
    mw._queue_waiting_tbl.setColumnWidth(4, 28)
    mw._queue_waiting_tbl.verticalHeader().setVisible(False)
    mw._queue_waiting_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    mw._queue_waiting_tbl.setShowGrid(False)
    mw._queue_waiting_tbl.setAlternatingRowColors(True)
    mw._queue_waiting_tbl.verticalHeader().setDefaultSectionSize(28)
    mw._queue_waiting_tbl.setStyleSheet(_TABLE_STYLE)
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
    mw._queue_hist_tbl.setShowGrid(False)
    mw._queue_hist_tbl.setAlternatingRowColors(True)
    mw._queue_hist_tbl.verticalHeader().setDefaultSectionSize(28)
    mw._queue_hist_tbl.setStyleSheet(_TABLE_STYLE)
    qvl.addWidget(mw._queue_hist_tbl)
    qvl.addStretch()

    return mw.qv


def build_queue_dialog(mw: Any) -> None:
    """Show a popup dialog with running/queued account status."""
    from PySide6.QtCore import QTimer
    d = QDialog(mw)
    d.setWindowTitle("队列状态")
    d.setMinimumSize(400, 280)
    l = QVBoxLayout(d)

    running_grp = QGroupBox("▶ 运行中")
    rl = QVBoxLayout(running_grp)
    running_tbl = QTableWidget(0, 3)
    running_tbl.setHorizontalHeaderLabels(["账号", "状态", "时长"])
    running_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    running_tbl.setColumnWidth(1, 100)
    running_tbl.setColumnWidth(2, 70)
    running_tbl.verticalHeader().setVisible(False)
    running_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
    rl.addWidget(running_tbl)
    l.addWidget(running_grp)

    queue_grp = QGroupBox("⏳ 排队中")
    ql = QVBoxLayout(queue_grp)
    queue_tbl = QTableWidget(0, 5)
    queue_tbl.setHorizontalHeaderLabels(["账号", "来源", "优先级", "预计启动", ""])
    queue_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    queue_tbl.setColumnWidth(1, 60)
    queue_tbl.setColumnWidth(2, 50)
    queue_tbl.setColumnWidth(3, 100)
    queue_tbl.setColumnWidth(4, 50)
    queue_tbl.verticalHeader().setVisible(False)
    queue_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
    ql.addWidget(queue_tbl)
    l.addWidget(queue_grp)

    def _refresh():
        import time
        running = []
        if hasattr(mw, "runner"):
            for aid in mw.runner.active_ids():
                a = next((x for x in mw.accounts if x["id"] == aid), None)
                name = a["name"] if a else aid[:8]
                t = int(time.time() - mw.runner._start_times.get(aid, 0))
                running.append((name, f"运行中 {t//60}m{t%60}s", ""))
            for pid in list(getattr(mw, "_running_procs", {}).keys()):
                p = mw._running_procs[pid]
                if p.poll() is None:
                    w = next((x for x in mw.warehouse if x["id"] == pid), None)
                    ac = next((x for x in mw.accounts if x["id"] == w.get("account_ref", "")), None) if w else None
                    name = ac["name"] if ac else (__import__("pathlib").Path(w["path"]).stem if w else pid[:8])
                    t = int(time.time() - mw._proc_start_times.get(pid, 0))
                    running.append((name, f"运行中 {t//60}m{t%60}s", ""))
        running_tbl.setRowCount(max(1, len(running)) if running else 1)
        if running:
            for i, (name, status, _) in enumerate(running):
                running_tbl.setItem(i, 0, QTableWidgetItem(name))
                running_tbl.setItem(i, 1, QTableWidgetItem(status))
        else:
            running_tbl.setItem(0, 0, QTableWidgetItem("—"))
            running_tbl.setItem(0, 1, QTableWidgetItem("无"))

        queue = []
        if hasattr(mw, "launch_queue") and mw.launch_queue:
            src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
            now = __import__("datetime").datetime.now()
            with mw.launch_queue._lock:
                pending_snapshot = list(mw.launch_queue._pending)
            for e in sorted(pending_snapshot, key=lambda x: x.sort_key):
                a = next((x for x in mw.accounts if x["id"] == e.account_id), None)
                name = a["name"] if a else e.account_id[:8]
                when = ""
                if e.not_before > now:
                    diff = int((e.not_before - now).total_seconds() / 60)
                    when = e.not_before.strftime("%m-%d %H:%M") if diff > 60 else f"{diff}分钟后"
                else:
                    when = "等待空闲"
                queue.append((name, src_map.get(e.source, e.source), str(e.sort_key[0]), when, e.account_id))
        queue_tbl.setRowCount(max(1, len(queue)) if queue else 1)
        if queue:
            for i, (name, src, pri, when, aid) in enumerate(queue):
                queue_tbl.setItem(i, 0, QTableWidgetItem(name))
                queue_tbl.setItem(i, 1, QTableWidgetItem(src))
                queue_tbl.setItem(i, 2, QTableWidgetItem(pri))
                queue_tbl.setItem(i, 3, QTableWidgetItem(when))
                cancel_btn = QPushButton("✕")
                cancel_btn.setFixedSize(mw._btn_lg, mw._btn_lg)
                cancel_btn.setStyleSheet(BTN_DELETE.format(r=mw._btn_lg // 2))
                cancel_btn.setToolTip("取消排队")
                cancel_btn.clicked.connect(lambda c, a=aid: (mw.launch_queue.dequeue(a), _refresh()))
                cw = QWidget()
                cwl = QHBoxLayout(cw)
                cwl.setContentsMargins(0, 0, 0, 0)
                cwl.setAlignment(Qt.AlignCenter)
                cwl.addWidget(cancel_btn)
                queue_tbl.setCellWidget(i, 4, cw)
        else:
            queue_tbl.setItem(0, 0, QTableWidgetItem("—"))
            queue_tbl.setItem(0, 1, QTableWidgetItem("无"))

    _refresh()
    timer = QTimer(d)
    timer.timeout.connect(_refresh)
    timer.start(2000)
    d.finished.connect(timer.stop)
    d.exec()


_refresh_lock = False

def refresh_queue_view(mw: Any) -> None:
    """Update the queue panel with current state."""
    global _refresh_lock
    if _refresh_lock:
        return
    if not hasattr(mw, "_queue_running_tbl"):
        return

    import time
    _refresh_lock = True
    try:
        now = datetime.now()

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
                plan = a.get("smart_plan", "")
                running.append((a["name"], status_text, plan, aid))

        tbl = mw._queue_running_tbl
        tbl.setRowCount(len(running))
        for i, (name, status, plan, aid) in enumerate(running):
            tbl.setItem(i, 0, QTableWidgetItem(name))
            tbl.setItem(i, 1, QTableWidgetItem(status))
            tbl.setItem(i, 2, QTableWidgetItem(plan))
            stop_btn = QPushButton("✕")
            stop_btn.setFixedSize(28, 28)
            stop_btn.setToolTip("停止")
            stop_btn.setStyleSheet(BTN_DELETE.format(r=14))
            stop_btn.clicked.connect(lambda c, a=aid: (_safe_stop(mw, a), refresh_queue_view(mw)))
            sw = QWidget()
            swl = QHBoxLayout(sw); swl.setContentsMargins(0,0,0,0); swl.setAlignment(Qt.AlignCenter); swl.addWidget(stop_btn)
            tbl.setCellWidget(i, 3, sw)
            eye_btn = QPushButton("👁")
            eye_btn.setFixedSize(28, 28)
            eye_btn.setToolTip("查看账号详情")
            eye_btn.setStyleSheet("QPushButton{background:transparent;color:#888;border:none;font-size:8pt}QPushButton:hover{color:#fff}")
            eye_btn.clicked.connect(lambda c, a=aid: (_jump_to_account(mw, a)))
            sw2 = QWidget()
            swl2 = QHBoxLayout(sw2); swl2.setContentsMargins(0,0,0,0); swl2.setAlignment(Qt.AlignCenter); swl2.addWidget(eye_btn)
            tbl.setCellWidget(i, 4, sw2)

        # Waiting table
        waiting = []
        if hasattr(mw, "launch_queue") and mw.launch_queue:
            src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
            with mw.launch_queue._lock:
                pending_snapshot = list(mw.launch_queue._pending)
            pending = sorted(pending_snapshot, key=lambda x: x.sort_key)
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
            cancel_btn.setFixedSize(28, 28)
            cancel_btn.setToolTip("取消排队")
            cancel_btn.setStyleSheet(BTN_DELETE.format(r=14))
            cancel_btn.clicked.connect(lambda c, a=aid: (_safe_dequeue(mw, a), refresh_queue_view(mw)))
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
                for r in runs[-3:]:
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
    finally:
        _refresh_lock = False


def _rebuild_queue_combo(mw: Any) -> None:
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
    combo = mw._queue_combo
    aid = combo.currentData()
    if not aid:
        text = combo.currentText().strip()
        for a in mw.accounts:
            if a.get("name", "") == text:
                aid = a["id"]
                break
    if aid:
        mw.launch_queue.enqueue(aid, "manual", priority=0)
        mw.launch_queue.tick()
        refresh_queue_view(mw)


def _current_task_name(mw: Any, aid: str) -> str | None:
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


def _jump_to_account(mw: Any, aid: str) -> None:
    mw._sw("accounts")
    for i in range(mw.at.rowCount()):
        it = mw.at.item(i, 0)
        if it and hasattr(it, "_acc_id") and it._acc_id == aid:
            mw.at.setCurrentCell(i, 0)
            break


def _clear_queue(mw: Any) -> None:
    _safe_clear_queue(mw)
    refresh_queue_view(mw)
