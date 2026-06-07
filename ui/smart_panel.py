"""Smart scheduling panel — global defaults + per-account overrides."""
from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QFormLayout, QLineEdit, QComboBox,
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

    # Recruit
    recruit_cb = QCheckBox("公招随基建一起跑")
    recruit_cb.setChecked(sg.get("recruit_enabled", True))
    recruit_cb.toggled.connect(lambda v: (_set_global(mw, "recruit_enabled", v), mw._save()))
    gf.addRow("", recruit_cb)

    # Mall
    mall_cb = QCheckBox("信用商店随基建一起跑")
    mall_cb.setChecked(sg.get("mall_enabled", True))
    mall_cb.toggled.connect(lambda v: (_set_global(mw, "mall_enabled", v), mw._save()))
    gf.addRow("", mall_cb)

    # Post action
    post_row = QHBoxLayout()
    post_opts = [("ExitArknights", "关闭游戏"), ("ExitSelf", "关闭MAA"), ("ExitEmulator", "关模拟器")]
    post_cbs = {}
    current_post = sg.get("post_action", "ExitArknights,ExitSelf")
    current_set = set(current_post.split(",")) if current_post else set()

    def _save_post():
        selected = [k for k, cb in post_cbs.items() if cb.isChecked()]
        sg["post_action"] = ",".join(selected) if selected else ""
        mw._save()

    for k, v in post_opts:
        cb = QCheckBox(v)
        cb.setChecked(k in current_set)
        cb.toggled.connect(lambda: _save_post())
        post_cbs[k] = cb
        post_row.addWidget(cb)
    post_row.addStretch()
    gf.addRow("完成后:", post_row)

    vl.addWidget(gb)

    # ── Account overrides table ──
    vl.addWidget(QLabel("账号关卡覆盖（空=继承全局或MAA自行决定）",
                        font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    DAYS = ["一", "二", "三", "四", "五", "六", "日"]
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    cols = 4 + len(DAYS)
    tbl = QTableWidget(0, cols)
    tbl.setHorizontalHeaderLabels(["账号", "默认关卡", "剿灭关卡"] + DAYS + [""])
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    tbl.setColumnWidth(1, 100)
    tbl.setColumnWidth(2, 100)
    for i in range(len(DAYS)):
        tbl.setColumnWidth(3 + i, 70)
    tbl.setColumnWidth(3 + len(DAYS), 36)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(28)
    mw._smart_tbl = tbl

    btn_row = QHBoxLayout()
    run_btn = QPushButton("▶ 立即调度全部")
    run_btn.setObjectName("startBtn")
    run_btn.clicked.connect(lambda: _run_smart_all(mw))
    btn_row.addWidget(run_btn)
    btn_row.addStretch()
    vl.addLayout(btn_row)

    vl.addWidget(tbl, 1)
    vl.addStretch()

    _rebuild_smart_table(mw)
    return mw.smart_v


def _set_global(mw: Any, key: str, value: Any) -> None:
    mw.config.setdefault("smart_global", {})[key] = value


def _run_smart_all(mw: Any) -> None:
    """Trigger immediate smart scheduling check for all accounts."""
    if not mw.config.get("smart_global", {}).get("enabled", False):
        mw._log("智能调度未启用")
        return
    setattr(mw, "_last_smart_minute", "")
    mw._smart_tick()


def _toggle_smart(mw: Any, enabled: bool) -> None:
    sg = mw.config.setdefault("smart_global", {})
    sg["enabled"] = enabled
    mw._save()
    mw._update_todo_badge()
    if enabled:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, lambda: (setattr(mw, "_last_smart_minute", ""), mw._smart_tick()))


def _edit_account_smart(mw: Any, row: int) -> None:
    if row < 0 or row >= len(mw.accounts):
        return
    a = mw.accounts[row]

    from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout
    d = QDialog(mw)
    d.setWindowTitle(f"智能调度 — {a.get('name', '')}")
    d.setMinimumSize(400, 350)
    l = QVBoxLayout(d)
    f = QFormLayout()
    f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

    stage_edit = QLineEdit(a.get("smart_stage", ""))
    stage_edit.setPlaceholderText("空=MAA自行决定")
    f.addRow("默认关卡:", stage_edit)

    anni_combo = QComboBox()
    for opt in ["自动选择", "Annihilation", "Annihilation_1", "Annihilation_2", "Annihilation_3"]:
        anni_combo.addItem(opt, opt if opt != "自动选择" else "")
    current_anni = a.get("smart_annihilation", "")
    idx = anni_combo.findData(current_anni)
    if idx >= 0:
        anni_combo.setCurrentIndex(idx)
    f.addRow("剿灭关卡:", anni_combo)

    day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    day_keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_edits = {}
    for dn, dk in zip(day_names, day_keys):
        de = QLineEdit(a.get(f"smart_{dk}", ""))
        de.setPlaceholderText("空=用默认关卡")
        day_edits[dk] = de
        f.addRow(dn + ":", de)

    l.addLayout(f)

    def _save_one():
        a["smart_stage"] = stage_edit.text().strip()
        a["smart_annihilation"] = anni_combo.currentData()
        for dk in day_keys:
            a[f"smart_{dk}"] = day_edits[dk].text().strip()
        mw._save()
        _rebuild_smart_table(mw)
        d.accept()

    from PySide6.QtWidgets import QPushButton as QBtn
    save_btn = QBtn("保存")
    save_btn.setObjectName("startBtn")
    save_btn.clicked.connect(_save_one)
    l.addWidget(save_btn)
    d.exec()


ANNIHILATION_VALUES = {"自动选择": "", "Annihilation": "Annihilation", "Annihilation_1": "Annihilation_1", "Annihilation_2": "Annihilation_2", "Annihilation_3": "Annihilation_3"}
ANNIHILATION_NAMES = {v: k for k, v in ANNIHILATION_VALUES.items()}


def _rebuild_smart_table(mw: Any) -> None:
    tbl = mw._smart_tbl
    if not tbl:
        return
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_names = ["一", "二", "三", "四", "五", "六", "日"]
    btn_col = 3 + len(DAY_KEYS)
    tbl.setRowCount(len(mw.accounts))
    for i, a in enumerate(mw.accounts):
        tbl.setItem(i, 0, QTableWidgetItem(a.get("name", "")))
        tbl.setItem(i, 1, QTableWidgetItem(a.get("smart_stage", "")))
        anni = a.get("smart_annihilation", "")
        anni_display = ANNIHILATION_NAMES.get(anni, "")
        tbl.setItem(i, 2, QTableWidgetItem(anni_display))
        for j, dk in enumerate(DAY_KEYS):
            tbl.setItem(i, 3 + j, QTableWidgetItem(a.get(f"smart_{dk}", "")))
        edit_btn = QPushButton("✎")
        edit_btn.setFixedSize(28, 28)
        edit_btn.setToolTip("编辑")
        edit_btn.clicked.connect(lambda _, r=i: _edit_account_smart(mw, r))
        ew = QWidget()
        el = QHBoxLayout(ew)
        el.setContentsMargins(0, 0, 0, 0)
        el.setAlignment(Qt.AlignCenter)
        el.addWidget(edit_btn)
        tbl.setCellWidget(i, btn_col, ew)
