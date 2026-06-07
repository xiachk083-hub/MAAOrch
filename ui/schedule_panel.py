"""Round-robin schedule panel — global daily batch + deficit control for all accounts."""
from __future__ import annotations
from typing import Any
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QLineEdit, QSpinBox,
)
from stats import RunStats


def build_schedule_panel(mw: Any) -> QWidget:
    mw.sv = QWidget()
    svl = QVBoxLayout(mw.sv)
    svl.setContentsMargins(12, 10, 12, 6)
    svl.setSpacing(4)

    # ── Top bar ──
    bar = QHBoxLayout()
    bar.addWidget(QLabel("循环调度", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))

    mw._sch_enabled_cb = QCheckBox("启用")
    mw._sch_enabled_cb.setChecked(bool(mw.config.get("daily_batch_time", "")))
    bar.addWidget(mw._sch_enabled_cb)

    bar.addWidget(QLabel(" 并行数:"))
    mw._sch_parallel_sp = QSpinBox()
    mw._sch_parallel_sp.setRange(1, 10); mw._sch_parallel_sp.setValue(mw.config.get("parallel_max", 1))
    mw._sch_parallel_sp.setFixedWidth(50)
    bar.addWidget(mw._sch_parallel_sp)

    bar.addWidget(QLabel(" 定时:"))
    mw._sch_batch_time = QLineEdit(mw.config.get("daily_batch_time", ""))
    mw._sch_batch_time.setPlaceholderText("04:00")
    mw._sch_batch_time.setFixedWidth(60)
    bar.addWidget(mw._sch_batch_time)

    bar.addWidget(QLabel(" 剩余体力:"))
    mw._sch_deficit_sp = QSpinBox()
    mw._sch_deficit_sp.setRange(0, 999);     mw._sch_deficit_sp.setValue(mw.config.get("deficit", 0))
    mw._sch_deficit_sp.setSuffix(" 回满")
    mw._sch_deficit_sp.setFixedWidth(80)
    bar.addWidget(mw._sch_deficit_sp)

    bar.addStretch()

    save_btn = QPushButton("保存")
    save_btn.setObjectName("startBtn")
    save_btn.clicked.connect(lambda: _save_schedule(mw))
    bar.addWidget(save_btn)
    svl.addLayout(bar)

    # ── Table ──
    tbl = QTableWidget(0, 5)
    tbl.setHorizontalHeaderLabels(["账号", "上次结束", "现在理智", "预计启动", "剩余"])
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    tbl.setColumnWidth(1, 90)
    tbl.setColumnWidth(2, 85)
    tbl.setColumnWidth(3, 110)
    tbl.setColumnWidth(4, 75)
    tbl.verticalHeader().setVisible(False); tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setShowGrid(False); tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(28)
    tbl.setStyleSheet("QTableWidget{background:transparent;border:none;font-size:9pt} QTableWidget::item{color:#ccc;padding:1px 6px} QHeaderView::section{color:#888;background:transparent;border:none;border-bottom:1px solid #2b2b30;padding:3px 6px;font-size:8pt}")
    mw._sch_tbl = tbl
    svl.addWidget(tbl, 1)

    # ── Hint ──
    ft = QLabel(" 剩余体力 0 = 回满启动    |    每日批量到点全部入队    |    跑完自动算恢复时间")
    ft.setStyleSheet("color:#666;font-size:8pt;padding:2px")
    svl.addWidget(ft)

    return mw.sv


def refresh_schedule_view(mw: Any) -> None:
    if not hasattr(mw, "_sch_tbl"): return
    tbl = mw._sch_tbl
    deficit = mw.config.get("deficit", 0)

    for i, a in enumerate(mw.accounts):
        if tbl.rowCount() <= i: tbl.insertRow(i)

        # Name
        tbl.setItem(i, 0, QTableWidgetItem(a.get("name", "")))

        # Last sanity
        last = _get_last_sanity(a["id"])
        if last:
            cur = last["current"]; mx = last["max"]
            tbl.setItem(i, 1, QTableWidgetItem(f"{cur}/{mx}"))

            # Current sanity (estimated)
            rt = last.get("report_time", "")
            now_cur = _estimate_current(cur, mx, rt)
            tbl.setItem(i, 2, QTableWidgetItem(f"{now_cur}/{mx}"))

            # Recovery / next launch
            d = mx - now_cur
            need = max(0, d - deficit)
            mins = need * 6
            h, m = divmod(mins, 60)
            nxt_dt = datetime.now() + timedelta(minutes=mins)

            if d <= deficit:
                tbl.setItem(i, 3, QTableWidgetItem(nxt_dt.strftime("%m-%d %H:%M")))
                tbl.setItem(i, 4, QTableWidgetItem("✅可启"))
            else:
                tbl.setItem(i, 3, QTableWidgetItem(nxt_dt.strftime("%m-%d %H:%M")))
                tbl.setItem(i, 4, QTableWidgetItem(f"{h}h{m:02d}"))
        else:
            tbl.setItem(i, 1, QTableWidgetItem("—"))
            tbl.setItem(i, 2, QTableWidgetItem("—"))
            tbl.setItem(i, 3, QTableWidgetItem("—"))
            tbl.setItem(i, 4, QTableWidgetItem("—"))

    while tbl.rowCount() > len(mw.accounts):
        tbl.removeRow(tbl.rowCount() - 1)


def _get_last_sanity(aid: str) -> dict | None:
    try:
        st = RunStats(aid)
        runs = st._data.get("runs", [])
        s = st.get_last_sanity()
        if s and not s.get("report_time"):
            # Fallback: use the latest run's ts
            for r in reversed(runs):
                if s.get("current") == r.get("sanity", {}).get("current"):
                    s["report_time"] = r.get("ts", "")
                    break
        return s
    except Exception: return None


def _estimate_current(last_current: int, max_sanity: int, report_time: str) -> int:
    """Estimate current sanity based on recovery rate (1pt/6min)."""
    try:
        if report_time:
            rt = datetime.strptime(report_time[:19], "%Y-%m-%d %H:%M:%S")
            elapsed = (datetime.now() - rt).total_seconds()
            recovered = int(elapsed / 360)  # 6min = 360s per point
            return min(max_sanity, last_current + recovered)
    except Exception: pass
    return last_current


def _save_schedule(mw: Any) -> None:
    enabled = bool(mw._sch_enabled_cb.isChecked()) if hasattr(mw, "_sch_enabled_cb") else False
    batch = mw._sch_batch_time.text().strip() if enabled and hasattr(mw, "_sch_batch_time") else ""
    mw.config["daily_batch_time"] = batch
    if enabled:
        t = batch if batch else mw.config.get("schedule", {}).get("time", "08:00")
        mw.config["schedule"] = {"enabled": True, "type": "daily", "time": t, "days_of_week": []}
    else:
        mw.config["schedule"] = {"enabled": False, "type": "daily", "time": "08:00", "days_of_week": []}
    if hasattr(mw, "_sch_parallel_sp"):
        mw.config["parallel_max"] = mw._sch_parallel_sp.value()
    if hasattr(mw, "_sch_deficit_sp"):
        val = mw._sch_deficit_sp.value()
        for a in mw.accounts:
            if "round_robin_deficit" not in a:
                a["round_robin_deficit"] = val
        mw.config["deficit"] = val
    mw._save()
    if enabled:
        mw.maint._start_schedule_thread()
        # Immediately enqueue runnable accounts
        if hasattr(mw, 'launch_queue'):
            prog_ids = {w.get("account_ref") for w in mw.warehouse if w.get("account_ref")}
            for a in mw.accounts:
                if a.get("id", "") in prog_ids and a.get("emu_instance_index", "") and a.get("adb_address", "").strip():
                    mw.launch_queue.enqueue(a["id"], "schedule", priority=1)
            mw.launch_queue._tick()
    elif hasattr(mw.maint.ctx, "schedule_thread") and mw.maint.ctx.schedule_thread:
        mw.maint.ctx.schedule_thread.stop_thread()
        mw.maint.ctx.schedule_thread = None
    refresh_schedule_view(mw)
