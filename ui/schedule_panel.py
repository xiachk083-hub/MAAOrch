"""Round-robin schedule panel — manage cycle scheduling for all accounts in one view."""
from __future__ import annotations
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QComboBox, QDoubleSpinBox, QSpinBox, QGroupBox,
)
from stats import RunStats


def build_schedule_panel(mw: Any) -> QWidget:
    """Build the round-robin schedule management tab."""
    mw.sv = QWidget()
    svl = QVBoxLayout(mw.sv)
    svl.setContentsMargins(6, 6, 6, 6)
    svl.setSpacing(6)

    # ── Global settings ──
    gs = QGroupBox("全局设置")
    gsl = QHBoxLayout(gs)
    mw._sch_enabled_cb = QCheckBox("启用循环调度")
    mw._sch_enabled_cb.setChecked(mw.config.get("round_robin_enabled", False))
    mw._sch_enabled_cb.toggled.connect(lambda: mw._save())
    gsl.addWidget(mw._sch_enabled_cb)

    gsl.addWidget(QLabel("最大并行:"))
    mw._sch_parallel_sp = QSpinBox()
    mw._sch_parallel_sp.setRange(1, 10)
    mw._sch_parallel_sp.setValue(mw.config.get("parallel_max", 1))
    mw._sch_parallel_sp.valueChanged.connect(lambda: mw._save())
    gsl.addWidget(mw._sch_parallel_sp)
    gsl.addWidget(QLabel("个 MAA"))
    gsl.addStretch()

    save_btn = QPushButton("保存")
    save_btn.clicked.connect(lambda: _save_schedule(mw))
    gsl.addWidget(save_btn)
    svl.addWidget(gs)

    # ── Account table ──
    mw._sch_tbl = QTableWidget(0, 6)
    mw._sch_tbl.setHorizontalHeaderLabels(["账号", "参与", "模式", "间隔(小时)", "最低理智", "下次预计"])
    mw._sch_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw._sch_tbl.setColumnWidth(1, 50)
    mw._sch_tbl.setColumnWidth(2, 70)
    mw._sch_tbl.setColumnWidth(3, 70)
    mw._sch_tbl.setColumnWidth(4, 60)
    mw._sch_tbl.setColumnWidth(5, 100)
    mw._sch_tbl.verticalHeader().setVisible(False)
    mw._sch_tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    svl.addWidget(mw._sch_tbl, 1)

    svl.addWidget(QLabel("提示: 时间模式=跑完后N小时再启 | 体力模式=理智回满再启"))

    return mw.sv


def refresh_schedule_view(mw: Any) -> None:
    """Refresh the schedule table with current account data."""
    if not hasattr(mw, "_sch_tbl"):
        return

    tbl = mw._sch_tbl
    mode_map = {"sanity": "体力回满", "time": "时间"}

    for i, a in enumerate(mw.accounts):
        if tbl.rowCount() <= i:
            tbl.insertRow(i)

        # Name
        tbl.setItem(i, 0, QTableWidgetItem(a.get("name", "")))

        # Enable checkbox
        cb = QCheckBox()
        cb.setChecked(a.get("round_robin", False))
        cb.stateChanged.connect(lambda s, idx=i: _on_cell_changed(mw, idx))
        cw = QWidget()
        cl = QHBoxLayout(cw); cl.setContentsMargins(0, 0, 0, 0); cl.setAlignment(Qt.AlignCenter); cl.addWidget(cb)
        tbl.setCellWidget(i, 1, cw)

        # Mode combo
        cmb = QComboBox()
        cmb.addItems(["体力回满", "时间"])
        cur_mode = a.get("round_robin_mode", "sanity")
        cmb.setCurrentText(mode_map.get(cur_mode, "体力回满"))
        cmb.currentIndexChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 2, cmb)

        # Hours spin
        hsp = QDoubleSpinBox()
        hsp.setRange(0.5, 72)
        hsp.setValue(a.get("round_robin_hours", 0) or 2)
        hsp.setDecimals(1)
        hsp.setSingleStep(0.5)
        hsp.valueChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 3, hsp)

        # Min sanity
        msp = QSpinBox()
        msp.setRange(0, 999)
        msp.setValue(a.get("min_sanity", 0))
        msp.valueChanged.connect(lambda _, idx=i: _on_cell_changed(mw, idx))
        tbl.setCellWidget(i, 4, msp)

        # Next launch estimate
        nxt = _estimate_next(mw, a["id"])
        tbl.setItem(i, 5, QTableWidgetItem(nxt))

    # Remove extra rows
    while tbl.rowCount() > len(mw.accounts):
        tbl.removeRow(tbl.rowCount() - 1)


def _on_cell_changed(mw: Any, idx: int) -> None:
    """Read current table values and save back to account."""
    tbl = mw._sch_tbl
    a = mw.accounts[idx]
    mode_map = {"体力回满": "sanity", "时间": "time"}

    # Checkbox
    cb_w = tbl.cellWidget(idx, 1)
    if cb_w:
        cb = cb_w.findChild(QCheckBox)
        if cb:
            a["round_robin"] = cb.isChecked()

    # Mode
    cmb = tbl.cellWidget(idx, 2)
    if cmb:
        a["round_robin_mode"] = mode_map.get(cmb.currentText(), "sanity")

    # Hours
    hsp = tbl.cellWidget(idx, 3)
    if hsp:
        a["round_robin_hours"] = hsp.value()

    # Min sanity
    msp = tbl.cellWidget(idx, 4)
    if msp:
        a["min_sanity"] = msp.value()


def _save_schedule(mw: Any) -> None:
    """Save global settings and all account round_robin changes."""
    if hasattr(mw, "_sch_enabled_cb"):
        mw.config["round_robin_enabled"] = mw._sch_enabled_cb.isChecked()
    if hasattr(mw, "_sch_parallel_sp"):
        mw.config["parallel_max"] = mw._sch_parallel_sp.value()
    # Read all cell values
    for i in range(len(mw.accounts)):
        _on_cell_changed(mw, i)
    mw._save()
    refresh_schedule_view(mw)


def _estimate_next(mw: Any, aid: str) -> str:
    """Estimate next launch time for an account."""
    try:
        st = RunStats(aid)
        s = st.get_last_sanity()
        if s:
            deficit = s["deficit"]
            from datetime import datetime, timedelta
            nxt = datetime.now() + timedelta(minutes=deficit * 6)
            return nxt.strftime("%m-%d %H:%M")
    except Exception:
        pass
    # Check queue
    if hasattr(mw, "launch_queue"):
        pending = mw.launch_queue.get_next_for(aid)
        if pending and pending != "即将启动":
            return pending
    return "—"
