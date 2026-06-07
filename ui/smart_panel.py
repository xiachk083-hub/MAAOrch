"""Smart scheduling panel — global defaults + per-account overrides."""
from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QFormLayout, QLineEdit,
)
from smart_scheduler import MATERIAL_STAGES


def build_smart_panel(mw: Any) -> QWidget:
    mw.smart_v = QWidget()
    vl = QVBoxLayout(mw.smart_v)
    vl.setContentsMargins(12, 10, 12, 6)
    vl.setSpacing(4)

    # ── Header: enable toggle ──
    hdr = QHBoxLayout()
    hdr.addWidget(QLabel("🧠 智能调度", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))
    mw._smart_enabled_cb = QCheckBox("启用智能调度")
    mw._smart_enabled_cb.setChecked(mw.config.get("smart_global", {}).get("enabled", False))
    mw._smart_enabled_cb.toggled.connect(lambda v: _toggle_smart(mw, v))
    hdr.addWidget(mw._smart_enabled_cb)
    hdr.addStretch()
    vl.addLayout(hdr)

    # ── Global defaults ──
    gb = QGroupBox("全局默认设置")
    gf = QFormLayout(gb)
    gf.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

    sg = mw.config.setdefault("smart_global", {})

    # Threshold
    th_sp = QSpinBox(); th_sp.setRange(10, 100); th_sp.setSuffix(" %")
    th_sp.setValue(sg.get("threshold", 80))
    th_sp.valueChanged.connect(lambda v: (_set_global(mw, "threshold", v), mw._save()))
    gf.addRow("体力阈值:", th_sp)

    # Expiring medicine
    med_row = QHBoxLayout()
    med_cb = QCheckBox("使用即将过期理智药")
    med_cb.setChecked(sg.get("expiring_medicine", True))
    med_cb.toggled.connect(lambda v: (_set_global(mw, "expiring_medicine", v), mw._save()))
    med_row.addWidget(med_cb)
    med_days = QSpinBox(); med_days.setRange(1, 7); med_days.setSuffix(" 天")
    med_days.setValue(sg.get("medicine_days", 2))
    med_days.valueChanged.connect(lambda v: (_set_global(mw, "medicine_days", v), mw._save()))
    med_row.addWidget(QLabel("过期前")); med_row.addWidget(med_days); med_row.addStretch()
    gf.addRow("", med_row)

    # Annihilation
    anni_cb = QCheckBox("每周自动跑剿灭")
    anni_cb.setChecked(sg.get("annihilation_enabled", True))
    anni_cb.toggled.connect(lambda v: (_set_global(mw, "annihilation_enabled", v), mw._save()))
    gf.addRow("", anni_cb)

    # Infrast times
    infra_row = QHBoxLayout()
    infra_t1 = QLineEdit(sg.get("infrast_times", ["04:00", "16:00"])[0] if sg.get("infrast_times") else "04:00")
    infra_t1.setFixedWidth(55)
    infra_t2 = QLineEdit(sg.get("infrast_times", ["04:00", "16:00"])[1] if len(sg.get("infrast_times", [])) > 1 else "16:00")
    infra_t2.setFixedWidth(55)

    def _save_infra_times():
        sg["infrast_times"] = [infra_t1.text().strip(), infra_t2.text().strip()]
        mw._save()

    infra_t1.editingFinished.connect(_save_infra_times)
    infra_t2.editingFinished.connect(_save_infra_times)
    infra_row.addWidget(infra_t1); infra_row.addWidget(QLabel("  ")); infra_row.addWidget(infra_t2); infra_row.addStretch()
    gf.addRow("基建班次:", infra_row)

    # Mall
    mall_cb = QCheckBox("信用商店随基建一起跑")
    mall_cb.setChecked(sg.get("mall_enabled", True))
    mall_cb.toggled.connect(lambda v: (_set_global(mw, "mall_enabled", v), mw._save()))
    gf.addRow("", mall_cb)

    # Post action
    pa = QLineEdit(sg.get("post_action", "ExitArknights,ExitSelf"))
    pa.editingFinished.connect(lambda: (_set_global(mw, "post_action", pa.text().strip()), mw._save()))
    gf.addRow("跑完关MAA:", pa)

    vl.addWidget(gb)

    # ── Account overrides table ──
    vl.addWidget(QLabel("账号关卡覆盖（空=继承全局或MAA自行决定）",
                        font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    DAYS = ["一", "二", "三", "四", "五", "六", "日"]
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    cols = 3 + len(DAYS)
    tbl = QTableWidget(0, cols)
    tbl.setHorizontalHeaderLabels(["账号", "默认关卡", "剿灭关卡"] + DAYS)
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    tbl.setColumnWidth(1, 100)
    tbl.setColumnWidth(2, 100)
    for i in range(len(DAYS)):
        tbl.setColumnWidth(3 + i, 70)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QAbstractItemView.DoubleClicked)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(28)
    mw._smart_tbl = tbl

    save_btn = QPushButton("保存全部")
    save_btn.setObjectName("startBtn")
    save_btn.clicked.connect(lambda: _save_smart_overrides(mw))

    vl.addWidget(tbl, 1)
    vl.addWidget(save_btn)

    _rebuild_smart_table(mw)
    return mw.smart_v


def _set_global(mw: Any, key: str, value: Any) -> None:
    mw.config.setdefault("smart_global", {})[key] = value


def _toggle_smart(mw: Any, enabled: bool) -> None:
    sg = mw.config.setdefault("smart_global", {})
    sg["enabled"] = enabled
    mw._save()
    mw._update_todo_badge()


def _rebuild_smart_table(mw: Any) -> None:
    tbl = mw._smart_tbl
    if not tbl:
        return
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    tbl.setRowCount(len(mw.accounts))
    for i, a in enumerate(mw.accounts):
        # Account name
        tbl.setItem(i, 0, QTableWidgetItem(a.get("name", "")))
        # Default stage
        item1 = QTableWidgetItem(a.get("smart_stage", ""))
        tbl.setItem(i, 1, item1)
        # Annihilation stage
        item2 = QTableWidgetItem(a.get("smart_annihilation", ""))
        tbl.setItem(i, 2, item2)
        # Day overrides
        for j, dk in enumerate(DAY_KEYS):
            item = QTableWidgetItem(a.get(f"smart_{dk}", ""))
            tbl.setItem(i, 3 + j, item)


def _save_smart_overrides(mw: Any) -> None:
    tbl = mw._smart_tbl
    if not tbl:
        return
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    for i in range(min(tbl.rowCount(), len(mw.accounts))):
        a = mw.accounts[i]
        it1 = tbl.item(i, 1)
        if it1:
            a["smart_stage"] = it1.text().strip()
        it2 = tbl.item(i, 2)
        if it2:
            a["smart_annihilation"] = it2.text().strip()
        for j, dk in enumerate(DAY_KEYS):
            it = tbl.item(i, 3 + j)
            if it:
                a[f"smart_{dk}"] = it.text().strip()
    mw._save()
    mw._log(f"智能调度配置已保存 ({len(mw.accounts)} 个账号)")
