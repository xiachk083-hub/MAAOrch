"""Round-robin schedule panel — manage cycle scheduling for all accounts in one view."""
from __future__ import annotations
from typing import Any
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QComboBox, QDoubleSpinBox, QSpinBox, QFrame,
)
from stats import RunStats

_STYLE = "QTableWidget{background:transparent;border:none;font-size:9pt} QTableWidget::item{color:#ccc;padding:2px 6px} QHeaderView::section{color:#888;background:rgba(255,255,255,0.03);border:none;border-bottom:1px solid #333;padding:4px 6px;font-size:8pt;font-weight:bold} QTableWidget QComboBox,QTableWidget QSpinBox,QTableWidget QDoubleSpinBox{color:#ccc;background:transparent;border:1px solid #444;border-radius:3px;padding:2px 4px;font-size:8pt}"


def build_schedule_panel(mw: Any) -> QWidget:
    mw.sv = QWidget()
    svl = QVBoxLayout(mw.sv)
    svl.setContentsMargins(10, 8, 10, 8)
    svl.setSpacing(6)

    # ── Header ──
    hdr = QFrame()
    hdr.setStyleSheet("QFrame{border-bottom:1px solid #333;padding-bottom:6px}")
    h = QHBoxLayout(hdr); h.setContentsMargins(0,0,0,0)

    title = QLabel("⚙ 循环调度")
    title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
    h.addWidget(title)

    sep = QLabel("│"); sep.setStyleSheet("color:#444;font-size:14pt;margin:0 8px")
    h.addWidget(sep)

    h.addWidget(QLabel("并行上限"))
    mw._sch_parallel_sp = QSpinBox()
    mw._sch_parallel_sp.setRange(1, 10); mw._sch_parallel_sp.setValue(mw.config.get("parallel_max", 1))
    mw._sch_parallel_sp.setFixedWidth(44)
    mw._sch_parallel_sp.setStyleSheet("QSpinBox{color:#ccc;background:transparent;border:1px solid #555;border-radius:3px;padding:2px;font-size:9pt}")
    mw._sch_parallel_sp.valueChanged.connect(lambda: mw._save())
    h.addWidget(mw._sch_parallel_sp)

    h.addStretch()

    mw._sch_enabled_cb = QCheckBox("启用")
    mw._sch_enabled_cb.setChecked(mw.config.get("round_robin_enabled", False))
    mw._sch_enabled_cb.toggled.connect(lambda: mw._save())
    h.addWidget(mw._sch_enabled_cb)

    save_btn = QPushButton("保存")
    save_btn.setStyleSheet("QPushButton{background:#2b7a3a;color:#fff;border:none;border-radius:5px;padding:3px 10px;font-size:9pt}QPushButton:hover{background:#1e5a28}")
    save_btn.clicked.connect(lambda: _save_schedule(mw))
    h.addWidget(save_btn)
    svl.addWidget(hdr)

    # ── Info card ──
    info = QFrame()
    info.setStyleSheet("QFrame{background:rgba(255,255,255,0.03);border:1px solid #333;border-radius:6px;padding:6px 10px}")
    il = QHBoxLayout(info); il.setContentsMargins(0,0,0,0)
    il.addWidget(QLabel("💡 时间模式：跑完后重新入队排队    │    体力模式：理智回满自动入队"))
    il.itemAt(0).widget().setStyleSheet("color:#888;font-size:8pt")
    il.addStretch()
    il.addWidget(QLabel(f"上次保存: {datetime.now().strftime('%H:%M')}"))
    il.itemAt(il.count()-1).widget().setStyleSheet("color:#555;font-size:7pt")
    svl.addWidget(info)

    # ── Table ──
    tbl = QTableWidget(0, 5)
    tbl.setHorizontalHeaderLabels(["账号", "模式", "", "最低理智", "预计启动"])
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    tbl.setColumnWidth(1, 75)
    tbl.setColumnWidth(2, 55)
    tbl.setColumnWidth(3, 65)
    tbl.setColumnWidth(4, 95)
    tbl.verticalHeader().setVisible(False); tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setShowGrid(False); tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(30)
    tbl.setStyleSheet(_STYLE + " QTableWidget::item:selected{background:rgba(255,255,255,0.06)}")
    mw._sch_tbl = tbl
    svl.addWidget(tbl, 1)

    return mw.sv


def refresh_schedule_view(mw: Any) -> None:
    if not hasattr(mw, "_sch_tbl"): return
    tbl = mw._sch_tbl

    for i, a in enumerate(mw.accounts):
        if tbl.rowCount() <= i: tbl.insertRow(i)
        enabled = a.get("round_robin", False)

        # ── Name ──
        name_item = QTableWidgetItem(a.get("name", ""))
        if not enabled: name_item.setForeground(Qt.gray)
        tbl.setItem(i, 0, name_item)

        # ── Mode ──
        cmb = QComboBox()
        cmb.addItems(["体力回满", "时间"])
        mode_map = {"sanity": "体力回满", "time": "时间"}
        cmb.setCurrentText(mode_map.get(a.get("round_robin_mode", "sanity"), "体力回满"))
        cmb.setEnabled(enabled)
        cmb.currentIndexChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 1, cmb)

        # ── Time interval ──
        hsp = QDoubleSpinBox()
        hsp.setRange(0.5, 72); hsp.setValue(a.get("round_robin_hours", 0) or 2)
        hsp.setDecimals(1); hsp.setSingleStep(0.5); hsp.setSuffix(" h")
        hsp.setEnabled(enabled)
        hsp.valueChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 2, hsp)

        # ── Min sanity ──
        msp = QSpinBox()
        msp.setRange(0, 999); msp.setValue(a.get("min_sanity", 0))
        msp.setEnabled(enabled)
        msp.valueChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 3, msp)

        # ── Next launch ──
        nxt = _estimate_next(mw, a["id"])
        nxt_lbl = QLabel(nxt)
        nxt_lbl.setStyleSheet(f"color:{'#888' if enabled else '#444'};font-size:8pt")
        nxt_lbl.setAlignment(Qt.AlignCenter)
        tbl.setCellWidget(i, 4, nxt_lbl)

    while tbl.rowCount() > len(mw.accounts):
        tbl.removeRow(tbl.rowCount() - 1)


def _on_cell_changed(mw: Any, idx: int) -> None:
    tbl = mw._sch_tbl; a = mw.accounts[idx]
    enabled = a.get("round_robin", False)
    if cmb := tbl.cellWidget(idx, 1):
        mode_map = {"体力回满": "sanity", "时间": "time"}
        a["round_robin_mode"] = mode_map.get(cmb.currentText(), "sanity")
        cmb.setEnabled(enabled)
    if hsp := tbl.cellWidget(idx, 2):
        a["round_robin_hours"] = hsp.value()
        hsp.setEnabled(enabled)
    if msp := tbl.cellWidget(idx, 3):
        a["min_sanity"] = msp.value()
        msp.setEnabled(enabled)


def _save_schedule(mw: Any) -> None:
    if hasattr(mw, "_sch_enabled_cb"):
        mw.config["round_robin_enabled"] = mw._sch_enabled_cb.isChecked()
    if hasattr(mw, "_sch_parallel_sp"):
        mw.config["parallel_max"] = mw._sch_parallel_sp.value()
    for i in range(len(mw.accounts)):
        a = mw.accounts[i]
        if cb := mw._sch_tbl.cellWidget(i, 0):
            pass  # name, no widget
        a["round_robin"] = a.get("round_robin", False)
        _on_cell_changed(mw, i)
    mw._save()
    refresh_schedule_view(mw)


def _estimate_next(mw: Any, aid: str) -> str:
    try:
        st = RunStats(aid); s = st.get_last_sanity()
        if s:
            nxt = datetime.now() + timedelta(minutes=s["deficit"] * 6)
            diff_h = (nxt - datetime.now()).total_seconds() / 3600
            if diff_h < 1: return f"<1h后"
            if diff_h < 24: return f"{diff_h:.1f}h后"
            return nxt.strftime("%m-%d %H:%M")
    except Exception: pass
    if hasattr(mw, "launch_queue"):
        p = mw.launch_queue.get_next_for(aid)
        if p and p != "即将启动": return p
    return "—"
