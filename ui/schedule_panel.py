"""Round-robin schedule panel — manage cycle scheduling for all accounts in one view."""
from __future__ import annotations
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QComboBox, QDoubleSpinBox, QSpinBox,
)
from stats import RunStats


def build_schedule_panel(mw: Any) -> QWidget:
    """Build the round-robin schedule management tab."""
    mw.sv = QWidget()
    svl = QVBoxLayout(mw.sv)
    svl.setContentsMargins(8, 8, 8, 8)
    svl.setSpacing(8)

    # ── Header bar ──
    hdr = QHBoxLayout()
    hdr.addWidget(QLabel("⚙ 循环调度", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))
    hdr.addStretch()

    mw._sch_enabled_cb = QCheckBox(" 启用")
    mw._sch_enabled_cb.setChecked(mw.config.get("round_robin_enabled", False))
    mw._sch_enabled_cb.toggled.connect(lambda: mw._save())
    hdr.addWidget(mw._sch_enabled_cb)

    hdr.addWidget(QLabel("  最大并行:"))
    mw._sch_parallel_sp = QSpinBox()
    mw._sch_parallel_sp.setRange(1, 10)
    mw._sch_parallel_sp.setValue(mw.config.get("parallel_max", 1))
    mw._sch_parallel_sp.setFixedWidth(50)
    mw._sch_parallel_sp.valueChanged.connect(lambda: mw._save())
    hdr.addWidget(mw._sch_parallel_sp)
    hdr.addWidget(QLabel(" 个 "))
    hdr.addSpacing(8)

    save_btn = QPushButton(" 保存 ")
    save_btn.setStyleSheet("QPushButton{background:#2b7a3a;color:#fff;border:none;border-radius:5px;padding:4px 12px;font-size:9pt}QPushButton:hover{background:#1e5a28}")
    save_btn.clicked.connect(lambda: _save_schedule(mw))
    hdr.addWidget(save_btn)
    svl.addLayout(hdr)

    # ── Table ──
    tbl = QTableWidget(0, 6)
    tbl.setHorizontalHeaderLabels(["账号", "", "模式", "间隔", "最低理智", "下次预计"])
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    tbl.setColumnWidth(1, 30)
    tbl.setColumnWidth(2, 80)
    tbl.setColumnWidth(3, 70)
    tbl.setColumnWidth(4, 70)
    tbl.setColumnWidth(5, 90)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(32)
    tbl.setStyleSheet("QTableWidget{background:transparent;border:none} QTableWidget::item{color:#ccc;padding:2px 6px} QHeaderView::section{color:#888;background:transparent;border:none;border-bottom:1px solid #333;padding:4px 6px;font-size:9pt;font-weight:bold}")
    mw._sch_tbl = tbl
    svl.addWidget(tbl, 1)

    # ── Hint ──
    hint = QLabel("时间模式=跑完 N 小时后重新入队  |  体力模式=理智回满自动入队")
    hint.setStyleSheet("color:#666;font-size:8pt;padding:2px 6px")
    svl.addWidget(hint)

    return mw.sv


def refresh_schedule_view(mw: Any) -> None:
    if not hasattr(mw, "_sch_tbl"):
        return
    tbl = mw._sch_tbl
    mode_map = {"sanity": "体力回满", "time": "时间"}

    for i, a in enumerate(mw.accounts):
        if tbl.rowCount() <= i:
            tbl.insertRow(i)

        tbl.setItem(i, 0, QTableWidgetItem(a.get("name", "")))

        # ── Enable ──
        cb = QCheckBox()
        cb.setChecked(a.get("round_robin", False))
        cb.stateChanged.connect(lambda s, idx=i: _on_cell_changed(mw, idx))
        w = QWidget()
        l = QHBoxLayout(w); l.setContentsMargins(0,0,0,0); l.setAlignment(Qt.AlignCenter); l.addWidget(cb)
        tbl.setCellWidget(i, 1, w)

        # ── Mode ──
        cmb = QComboBox()
        cmb.addItems(["体力回满", "时间"])
        cmb.setCurrentText(mode_map.get(a.get("round_robin_mode", "sanity"), "体力回满"))
        cmb.setStyleSheet("QComboBox{color:#ccc;background:transparent;border:1px solid #555;border-radius:3px;padding:1px 4px;font-size:8pt} QComboBox:hover{border-color:#888}")
        cmb.currentIndexChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 2, cmb)

        # ── Hours ──
        hsp = QDoubleSpinBox()
        hsp.setRange(0.5, 72); hsp.setValue(a.get("round_robin_hours", 0) or 2)
        hsp.setDecimals(1); hsp.setSingleStep(0.5); hsp.setSuffix("h")
        hsp.setStyleSheet("QDoubleSpinBox{color:#ccc;background:transparent;border:1px solid #555;border-radius:3px;padding:1px 4px;font-size:8pt}")
        hsp.valueChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 3, hsp)

        # ── Min sanity ──
        msp = QSpinBox()
        msp.setRange(0, 999); msp.setValue(a.get("min_sanity", 0))
        msp.setStyleSheet("QSpinBox{color:#ccc;background:transparent;border:1px solid #555;border-radius:3px;padding:1px 4px;font-size:8pt}")
        msp.valueChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 4, msp)

        # ── Next ──
        nxt = QLabel(_estimate_next(mw, a["id"]))
        nxt.setStyleSheet("color:#888;font-size:8pt")
        nxt.setAlignment(Qt.AlignCenter)
        tbl.setCellWidget(i, 5, nxt)

    while tbl.rowCount() > len(mw.accounts):
        tbl.removeRow(tbl.rowCount() - 1)


def _on_cell_changed(mw: Any, idx: int) -> None:
    tbl = mw._sch_tbl; a = mw.accounts[idx]
    mode_map = {"体力回满": "sanity", "时间": "time"}

    if w := tbl.cellWidget(idx, 1):
        if cb := w.findChild(QCheckBox): a["round_robin"] = cb.isChecked()
    if cmb := tbl.cellWidget(idx, 2):
        a["round_robin_mode"] = mode_map.get(cmb.currentText(), "sanity")
    if hsp := tbl.cellWidget(idx, 3):
        a["round_robin_hours"] = hsp.value()
    if msp := tbl.cellWidget(idx, 4):
        a["min_sanity"] = msp.value()


def _save_schedule(mw: Any) -> None:
    if hasattr(mw, "_sch_enabled_cb"):
        mw.config["round_robin_enabled"] = mw._sch_enabled_cb.isChecked()
    if hasattr(mw, "_sch_parallel_sp"):
        mw.config["parallel_max"] = mw._sch_parallel_sp.value()
    for i in range(len(mw.accounts)):
        _on_cell_changed(mw, i)
    mw._save()
    refresh_schedule_view(mw)


def _estimate_next(mw: Any, aid: str) -> str:
    try:
        st = RunStats(aid); s = st.get_last_sanity()
        if s:
            from datetime import datetime, timedelta
            nxt = datetime.now() + timedelta(minutes=s["deficit"] * 6)
            return nxt.strftime("%m-%d %H:%M")
    except Exception:
        pass
    if hasattr(mw, "launch_queue"):
        p = mw.launch_queue.get_next_for(aid)
        if p and p != "即将启动": return p
    return "—"
