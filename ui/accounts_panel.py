"""Accounts panel builder — extracted from main_window.py."""
from __future__ import annotations
from typing import Any

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSplitter, QFrame, QLineEdit, QScrollArea,
)


def build_accounts_panel(mw: Any) -> QWidget:
    """Build the accounts view (account table + dashboard area). Returns the panel widget."""
    mw.av = QWidget()
    avl = QVBoxLayout(mw.av)
    avl.setContentsMargins(0, 0, 0, 0)

    asp = QSplitter(Qt.Horizontal)

    # ── Left: account table ──
    al = QWidget()
    al.setMinimumWidth(240)
    al_ = QVBoxLayout(al)
    al_.setContentsMargins(4, 6, 4, 4)
    al_.setSpacing(6)

    th = QHBoxLayout()
    th.addWidget(QLabel("👤 账号", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))
    th.addStretch()
    th.addWidget(QPushButton("＋", clicked=mw._add_acc, objectName="addProgBtn"))
    th.addWidget(QPushButton("✕", clicked=mw._del_acc, objectName="stopBtn"))
    al_.addLayout(th)

    mw.asrch = QLineEdit()
    mw.asrch.setPlaceholderText("🔍 搜索账号...")
    mw.asrch.setClearButtonEnabled(True)
    mw.asrch.textChanged.connect(lambda: mw._ra())
    al_.addWidget(mw.asrch)

    mw.at = QTableWidget()
    mw.at.setColumnCount(3)
    mw.at.setHorizontalHeaderLabels(["名称", "区服", ""])
    mw.at.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw.at.setColumnWidth(1, 70)
    mw.at.setColumnWidth(2, 50)
    mw.at.setSelectionBehavior(QAbstractItemView.SelectRows)
    mw.at.setEditTriggers(QAbstractItemView.NoEditTriggers)
    mw.at.verticalHeader().setVisible(False)
    mw.at.verticalHeader().setDefaultSectionSize(mw._row_h + 4)
    mw.at.setDragEnabled(True)
    mw.at.setDragDropMode(QAbstractItemView.InternalMove)
    mw.at.setDropIndicatorShown(True)
    mw.at.setShowGrid(False)
    mw.at.setAlternatingRowColors(True)
    mw.at.setStyleSheet("QTableWidget{background:transparent;border:none;font-size:9pt} QTableWidget::item{color:#ccc;padding:2px 6px} QHeaderView::section{color:#888;background:transparent;border:none;border-bottom:1px solid #2b2b30;padding:3px 6px;font-size:9pt;font-weight:bold}")

    mw._acc_drop_lock = False

    def _on_acc_drop():
        if mw._acc_drop_lock:
            return
        mw._acc_drop_lock = True
        new_order = []
        id_map = {a["id"]: a for a in mw.accounts}
        for i in range(mw.at.rowCount()):
            it = mw.at.item(i, 0)
            if it and hasattr(it, "_acc_id") and it._acc_id in id_map:
                new_order.append(id_map[it._acc_id])
        if len(new_order) == len(mw.accounts):
            mw.accounts[:] = new_order
            mw._save()
        mw._acc_drop_lock = False

    mw.at.model().rowsMoved.connect(lambda *a: _on_acc_drop())
    mw.at.setContextMenuPolicy(Qt.CustomContextMenu)
    mw.at.customContextMenuRequested.connect(mw._ac_menu)
    mw.at.itemSelectionChanged.connect(mw._on_acc_sel)
    al_.addWidget(mw.at)

    asp.addWidget(al)

    # ── Right: dashboard area ──
    mw.ad = QScrollArea()
    mw.ad.setWidgetResizable(True)
    mw.ad.setFrameShape(QFrame.NoFrame)

    mw.adw = QWidget()
    mw.adl = QVBoxLayout(mw.adw)
    mw.adl.setContentsMargins(12, 4, 12, 12)
    mw.adl.setSpacing(8)

    mw.ade = QLabel("← 选择账号")
    mw.ade.setAlignment(Qt.AlignCenter)
    mw.ade.setStyleSheet("color:#888;font-size:14px")
    mw.adl.addWidget(mw.ade, 1)

    mw.ad.setWidget(mw.adw)
    asp.addWidget(mw.ad)

    asp.setStretchFactor(1, 1)
    asp.setSizes([280, 680])
    avl.addWidget(asp, 1)

    return mw.av
