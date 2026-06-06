"""Round-robin schedule panel — clean, minimal management view."""
from __future__ import annotations
from typing import Any
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QComboBox, QDoubleSpinBox, QSpinBox,
)
from stats import RunStats


def build_schedule_panel(mw: Any) -> QWidget:
    mw.sv = QWidget()
    svl = QVBoxLayout(mw.sv)
    svl.setContentsMargins(12, 10, 12, 6)
    svl.setSpacing(4)

    # ── Top bar: title + controls in one clean line ──
    bar = QHBoxLayout()
    bar.addWidget(QLabel("循环调度", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))

    mw._sch_enabled_cb = QCheckBox("启用")
    mw._sch_enabled_cb.setChecked(mw.config.get("round_robin_enabled", False))
    mw._sch_enabled_cb.toggled.connect(lambda: mw._save())
    bar.addWidget(mw._sch_enabled_cb)

    bar.addWidget(QLabel(" 并行"))
    mw._sch_parallel_sp = QSpinBox()
    mw._sch_parallel_sp.setRange(1, 10); mw._sch_parallel_sp.setValue(mw.config.get("parallel_max", 1))
    mw._sch_parallel_sp.setFixedWidth(40)
    mw._sch_parallel_sp.valueChanged.connect(lambda: mw._save())
    bar.addWidget(mw._sch_parallel_sp)

    bar.addStretch()

    save_btn = QPushButton("保存")
    save_btn.setStyleSheet("QPushButton{background:#2b7a3a;color:#fff;border:none;border-radius:4px;padding:2px 10px;font-size:9pt}QPushButton:hover{background:#1e5a28}")
    save_btn.clicked.connect(lambda: _save_schedule(mw))
    bar.addWidget(save_btn)
    svl.addLayout(bar)

    # ── Table ──
    tbl = QTableWidget(0, 6)
    tbl.setHorizontalHeaderLabels(["账号", "", "模式", "间隔", "最低理智", "预计启动"])
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    tbl.setColumnWidth(1, 28)
    tbl.setColumnWidth(2, 80)
    tbl.setColumnWidth(3, 55)
    tbl.setColumnWidth(4, 65)
    tbl.setColumnWidth(5, 90)
    tbl.verticalHeader().setVisible(False); tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setShowGrid(False); tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(28)
    tbl.setStyleSheet("QTableWidget{background:transparent;border:none;font-size:9pt} QTableWidget::item{color:#ccc;padding:1px 6px} QHeaderView::section{color:#777;background:transparent;border:none;border-bottom:1px solid #333;padding:3px 6px;font-size:8pt}")
    mw._sch_tbl = tbl
    svl.addWidget(tbl, 1)

    # ── Footer hint ──
    ft = QLabel(" 时间模式 = 跑完后 N 小时重新入队    |    体力模式 = 理智回满自动入队")
    ft.setStyleSheet("color:#555;font-size:7pt;padding:2px")
    svl.addWidget(ft)

    return mw.sv


def refresh_schedule_view(mw: Any) -> None:
    if not hasattr(mw, "_sch_tbl"): return
    tbl = mw._sch_tbl
    for i, a in enumerate(mw.accounts):
        if tbl.rowCount() <= i: tbl.insertRow(i)
        on = a.get("round_robin", False)
        alpha = 1.0 if on else 0.45
        c = f"rgba({int(204*alpha)},{int(204*alpha)},{int(204*alpha)},{alpha})"

        # Name
        it = QTableWidgetItem(a.get("name", ""))
        if not on: it.setForeground(Qt.gray)
        tbl.setItem(i, 0, it)

        # ── Checkbox ──
        cb = QCheckBox()
        cb.setChecked(on)
        cb.stateChanged.connect(lambda s, idx=i: _on_cell_changed(mw, idx))
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(0,0,0,0); l.setAlignment(Qt.AlignCenter); l.addWidget(cb)
        tbl.setCellWidget(i, 1, w)

        # Mode
        cmb = QComboBox(); cmb.addItems(["体力回满", "时间"])
        cmb.setCurrentText({"sanity":"体力回满","time":"时间"}.get(a.get("round_robin_mode","sanity"),"体力回满"))
        cmb.setEnabled(on); cmb.setStyleSheet(f"QComboBox{{color:{c};background:transparent;border:1px solid #444;border-radius:3px;padding:1px 4px;font-size:8pt}} QComboBox:hover{{border-color:#666}}")
        cmb.currentIndexChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 2, cmb)

        # Hours
        hsp = QDoubleSpinBox(); hsp.setRange(0.5,72); hsp.setValue(a.get("round_robin_hours",0) or 2)
        hsp.setDecimals(1); hsp.setSingleStep(0.5); hsp.setSuffix("h")
        hsp.setEnabled(on); hsp.setStyleSheet(f"QDoubleSpinBox{{color:{c};background:transparent;border:1px solid #444;border-radius:3px;padding:1px 4px;font-size:8pt}} QDoubleSpinBox:hover{{border-color:#666}}")
        hsp.valueChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 3, hsp)

        # Min sanity
        msp = QSpinBox(); msp.setRange(0,999); msp.setValue(a.get("min_sanity",0))
        msp.setEnabled(on); msp.setStyleSheet(f"QSpinBox{{color:{c};background:transparent;border:1px solid #444;border-radius:3px;padding:1px 4px;font-size:8pt}} QSpinBox:hover{{border-color:#666}}")
        msp.valueChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 4, msp)

        # Next
        nxt = _estimate_next(mw, a["id"])
        nl = QLabel(nxt)
        nl.setStyleSheet(f"color:#{'888' if on else '555'};font-size:8pt")
        nl.setAlignment(Qt.AlignCenter)
        tbl.setCellWidget(i, 5, nl)

    while tbl.rowCount() > len(mw.accounts):
        tbl.removeRow(tbl.rowCount() - 1)


def _on_cell_changed(mw: Any, idx: int) -> None:
    tbl = mw._sch_tbl; a = mw.accounts[idx]
    # Checkbox
    if w := tbl.cellWidget(idx, 1):
        if cb := w.findChild(QCheckBox):
            a["round_robin"] = cb.isChecked()
    on = a.get("round_robin", False)
    if cmb := tbl.cellWidget(idx, 2):
        a["round_robin_mode"] = {"体力回满":"sanity","时间":"time"}.get(cmb.currentText(),"sanity")
        cmb.setEnabled(on)
    if hsp := tbl.cellWidget(idx, 3): a["round_robin_hours"] = hsp.value(); hsp.setEnabled(on)
    if msp := tbl.cellWidget(idx, 4): a["min_sanity"] = msp.value(); msp.setEnabled(on)


def _save_schedule(mw: Any) -> None:
    if hasattr(mw, "_sch_enabled_cb"): mw.config["round_robin_enabled"] = mw._sch_enabled_cb.isChecked()
    if hasattr(mw, "_sch_parallel_sp"): mw.config["parallel_max"] = mw._sch_parallel_sp.value()
    for i in range(len(mw.accounts)): _on_cell_changed(mw, i)
    mw._save(); refresh_schedule_view(mw)


def _estimate_next(mw: Any, aid: str) -> str:
    try:
        st = RunStats(aid); s = st.get_last_sanity()
        if s:
            d = (datetime.now() + timedelta(minutes=s["deficit"] * 6) - datetime.now()).total_seconds() / 3600
            if d < 1: return " <1h"
            if d < 24: return f" {d:.0f}h后"
            return (datetime.now() + timedelta(minutes=s["deficit"] * 6)).strftime("%m-%d %H:%M")
    except Exception: pass
    if hasattr(mw, "launch_queue"):
        p = mw.launch_queue.get_next_for(aid)
        if p and p != "即将启动": return p
    return "—"
