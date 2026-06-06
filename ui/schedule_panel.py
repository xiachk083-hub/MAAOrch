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

    bar.addWidget(QLabel(" 并行"))
    mw._sch_parallel_sp = QSpinBox()
    mw._sch_parallel_sp.setRange(1, 10); mw._sch_parallel_sp.setValue(mw.config.get("parallel_max", 1))
    mw._sch_parallel_sp.setFixedWidth(40)
    bar.addWidget(mw._sch_parallel_sp)

    bar.addWidget(QLabel(" 每日批量:"))
    mw._sch_batch_time = QLineEdit(mw.config.get("daily_batch_time", ""))
    mw._sch_batch_time.setPlaceholderText("04:00")
    mw._sch_batch_time.setFixedWidth(50)
    mw._sch_batch_time.setStyleSheet("QLineEdit{color:#ccc;background:transparent;border:1px solid #555;border-radius:3px;padding:2px 4px;font-size:9pt}")
    bar.addWidget(mw._sch_batch_time)

    bar.addWidget(QLabel(" 距满差"))
    mw._sch_deficit_sp = QSpinBox()
    mw._sch_deficit_sp.setRange(0, 999); mw._sch_deficit_sp.setValue(0)
    mw._sch_deficit_sp.setSuffix(" 点")
    mw._sch_deficit_sp.setFixedWidth(60)
    bar.addWidget(mw._sch_deficit_sp)

    bar.addStretch()

    save_btn = QPushButton("保存")
    save_btn.setStyleSheet("QPushButton{background:#2b7a3a;color:#fff;border:none;border-radius:4px;padding:2px 10px;font-size:9pt}QPushButton:hover{background:#1e5a28}")
    save_btn.clicked.connect(lambda: _save_schedule(mw))
    bar.addWidget(save_btn)
    svl.addLayout(bar)

    # ── Table ──
    tbl = QTableWidget(0, 4)
    tbl.setHorizontalHeaderLabels(["账号", "上次结束理智", "恢复情况", "预计启动"])
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    tbl.setColumnWidth(1, 100)
    tbl.setColumnWidth(2, 80)
    tbl.setColumnWidth(3, 90)
    tbl.verticalHeader().setVisible(False); tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setShowGrid(False); tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(28)
    tbl.setStyleSheet("QTableWidget{background:transparent;border:none;font-size:9pt} QTableWidget::item{color:#ccc;padding:1px 6px} QHeaderView::section{color:#777;background:transparent;border:none;border-bottom:1px solid #333;padding:3px 6px;font-size:8pt}")
    mw._sch_tbl = tbl
    svl.addWidget(tbl, 1)

    # ── Hint ──
    ft = QLabel(" 距满差 0 = 回满启动    |    每日批量到点全部入队    |    跑完自动算恢复时间")
    ft.setStyleSheet("color:#555;font-size:7pt;padding:2px")
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
            tbl.setItem(i, 1, QTableWidgetItem(f"{last['current']}/{last['max']}"))
            # Recovery status
            d = last["max"] - last["current"]
            if d <= deficit:
                tbl.setItem(i, 2, QTableWidgetItem("✅ 可启动"))
            else:
                need = d - deficit
                mins = need * 6
                h, m = divmod(mins, 60)
                tbl.setItem(i, 2, QTableWidgetItem(f"恢复中 {h}h{m:02d}"))
            # Next estimate
            nxt = _estimate_next(mw, a["id"])
            tbl.setItem(i, 3, QTableWidgetItem(nxt))
        else:
            tbl.setItem(i, 1, QTableWidgetItem("—"))
            tbl.setItem(i, 2, QTableWidgetItem("暂无数据"))
            tbl.setItem(i, 3, QTableWidgetItem("—"))

    while tbl.rowCount() > len(mw.accounts):
        tbl.removeRow(tbl.rowCount() - 1)


def _get_last_sanity(aid: str) -> dict | None:
    try:
        st = RunStats(aid); return st.get_last_sanity()
    except Exception: return None


def _estimate_next(mw: Any, aid: str) -> str:
    s = _get_last_sanity(aid)
    if not s: return "—"
    deficit = mw.config.get("deficit", 0) if hasattr(mw, "config") else 0
    d = max(0, (s["max"] - s["current"]) - deficit)
    mins = d * 6
    nxt = datetime.now() + timedelta(minutes=mins)
    diff_h = (nxt - datetime.now()).total_seconds() / 3600
    if diff_h < 1: return " <1h后"
    if diff_h < 24: return f" {diff_h:.0f}h后"
    return nxt.strftime("%m-%d %H:%M")


def _save_schedule(mw: Any) -> None:
    if hasattr(mw, "_sch_enabled_cb"):
        if not mw._sch_enabled_cb.isChecked():
            mw.config["daily_batch_time"] = ""
        elif hasattr(mw, "_sch_batch_time"):
            mw.config["daily_batch_time"] = mw._sch_batch_time.text().strip()
    if hasattr(mw, "_sch_parallel_sp"):
        mw.config["parallel_max"] = mw._sch_parallel_sp.value()
    if hasattr(mw, "_sch_deficit_sp"):
        for a in mw.accounts:
            a["round_robin_deficit"] = mw._sch_deficit_sp.value()
        mw.config["deficit"] = mw._sch_deficit_sp.value()
    mw._save()
    refresh_schedule_view(mw)
