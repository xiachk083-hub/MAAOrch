from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QWidget, QGroupBox, QCheckBox, QLineEdit,
    QComboBox, QDialogButtonBox, QMessageBox)
from infrastructure.task_constants import TASK_NAMES


def open_task_config(mw: Any, ac: dict) -> None:
    d = QDialog(mw)
    d.setWindowTitle(f"任务配置 — {ac.get('name','')}")
    d.setMinimumSize(480, 360)
    vl = QVBoxLayout(d)

    tabs = QTabWidget()
    vl.addWidget(tabs)

    ts = dict(ac.get("task_settings", {}))  # make writable copy

    # ── 启动游戏 ──
    w1 = QWidget(); l1 = QVBoxLayout(w1); l1.setSpacing(8)
    sw = QLineEdit(ac.get("account_switch", ""))
    sw.setPlaceholderText("输入账号名用于切换（留空=不切换）")
    l1.addWidget(QLabel("切换账号 (空白=不切换):"))
    l1.addWidget(sw)
    l1.addStretch()
    tabs.addTab(w1, "启动游戏")

    # ── 剿灭作战 ──
    w3 = QWidget(); l3 = QVBoxLayout(w3); l3.setSpacing(8)
    l3.addWidget(QLabel("剿灭关卡:"))
    ann = QComboBox()
    anni_map = {"当期剿灭":"Annihilation","切尔诺伯格":"Chernobog@Annihilation",
                "龙门 外环":"LungmenOutskirts@Annihilation","龙门 市区":"LungmenDowntown@Annihilation",
                "多索雷斯":"DossolesHoliday@Annihilation"}
    current_anni = ac.get("smart_annihilation", "Annihilation")
    # Find which display name matches, or add custom
    found_display = None
    for display, internal in anni_map.items():
        ann.addItem(display, internal)
        if internal == current_anni:
            found_display = display
    if found_display:
        ann.setCurrentText(found_display)
    else:
        ann.addItem(current_anni, current_anni)
        ann.setCurrentIndex(ann.count() - 1)
    l3.addWidget(ann)
    l3.addStretch()
    tabs.addTab(w3, "剿灭作战")

    # ── 刷关作战 ──
    w2 = QWidget(); l2 = QVBoxLayout(w2); l2.setSpacing(8)
    fs = QLineEdit(ac.get("fight_stage", ""))
    fs.setPlaceholderText("例如: 1-7")
    l2.addWidget(QLabel("默认关卡:")); l2.addWidget(fs)
    ft = ts.get("Fight", {})
    exp_med = QCheckBox("优先吃快到期理智药")
    exp_med.setChecked(ft.get("use_expiring_medicine", True))
    l2.addWidget(exp_med)
    l2.addStretch()
    tabs.addTab(w2, "刷关作战")

    # ── 公开招募 ──
    w4 = QWidget(); l4 = QVBoxLayout(w4); l4.setSpacing(8)
    rt = ts.get("Recruit", {})
    sel = rt.get("select", [3, 4, 5])
    l4.addWidget(QLabel("必选星级:"))
    star_row = QHBoxLayout()
    star_cbs = {}
    for s in [3, 4, 5]:
        cb = QCheckBox(f"{s}★")
        cb.setChecked(s in sel)
        star_cbs[s] = cb
        star_row.addWidget(cb)
    star_row.addStretch()
    l4.addLayout(star_row)
    l4.addStretch()
    tabs.addTab(w4, "公开招募")

    # ── 基建换班 ──
    w5 = QWidget(); l5 = QVBoxLayout(w5); l5.setSpacing(8)
    it = ts.get("Infrast", {})
    mode_cb = QComboBox()
    mode_cb.addItem("常规模式", "Normal")
    mode_cb.addItem("队列轮换", "Queued")
    current_mode = it.get("mode", "Normal")
    mi = mode_cb.findData(current_mode)
    if mi >= 0: mode_cb.setCurrentIndex(mi)
    l5.addWidget(QLabel("基建模式:")); l5.addWidget(mode_cb)
    l5.addStretch()
    tabs.addTab(w5, "基建换班")

    # ── 领取奖励 ──
    w6 = QWidget(); l6 = QVBoxLayout(w6); l6.setSpacing(8)
    at = ts.get("Award", {})
    aw = QCheckBox("领取签到奖励"); aw.setChecked(at.get("award", True)); l6.addWidget(aw)
    ml = QCheckBox("收取邮件"); ml.setChecked(at.get("mail", True)); l6.addWidget(ml)
    fg = QCheckBox("免费单抽 (⚠ 自动抽卡，确认已抽过不会重复)")
    fg.setChecked(at.get("free_gacha", False)); l6.addWidget(fg)
    oru = QCheckBox("合成玉/矿山"); oru.setChecked(at.get("orundum", True)); l6.addWidget(oru)
    l6.addStretch()
    tabs.addTab(w6, "领取奖励")

    # ── Buttons ──
    def _save():
        ac["account_switch"] = sw.text().strip()
        ac["fight_stage"] = fs.text().strip()
        ac["smart_annihilation"] = ann.currentData() or "Annihilation"
        # Build task_settings
        new_ts = {}
        # Fight
        new_ts["Fight"] = {"use_expiring_medicine": exp_med.isChecked()}
        # Recruit
        selected_stars = [s for s, cb in star_cbs.items() if cb.isChecked()]
        new_ts["Recruit"] = {"select": selected_stars if selected_stars else [3, 4, 5],
                             "confirm": selected_stars if selected_stars else [3, 4, 5]}
        # Infrast
        new_ts["Infrast"] = {"mode": mode_cb.currentData()}
        # Award
        new_ts["Award"] = {"award": aw.isChecked(), "mail": ml.isChecked(),
                           "free_gacha": fg.isChecked(), "orundum": oru.isChecked()}
        ac["task_settings"] = new_ts
        # Ensure sync_tasks is on
        progs = [w for w in mw.warehouse if w.get("account_ref") == ac.get("id","")]
        if progs:
            progs[0]["sync_tasks"] = True
        mw._save()
        d.accept()

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(_save); bb.rejected.connect(d.reject)
    vl.addWidget(bb)
    d.exec()
