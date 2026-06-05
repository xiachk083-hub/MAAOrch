"""Groups panel builder — extracted from main_window.py."""
from __future__ import annotations
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter, QFrame,
    QLineEdit, QComboBox, QSpinBox, QGroupBox, QFormLayout,
    QCheckBox, QMenu,
)


def build_groups_panel(mw: Any) -> tuple[QWidget, QPushButton, QPushButton]:
    """Build the groups view (warehouse + group programs). Returns (panel, warehouse_tab_btn, group_tab_btn)."""
    mw.gv = QWidget()
    gvl = QVBoxLayout(mw.gv)
    gvl.setContentsMargins(0, 0, 0, 0)

    sp = QSplitter(Qt.Horizontal)

    # ── Left: group list ──
    left = QWidget()
    left.setMinimumWidth(180)
    ll = QVBoxLayout(left)
    ll.setContentsMargins(0, 0, 0, 0)
    ll.addWidget(QLabel("分组", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))
    mw.gl_ = QListWidget()
    mw.gl_.currentRowChanged.connect(mw._on_group)
    ll.addWidget(mw.gl_)
    br = QHBoxLayout()
    br.addWidget(QPushButton("＋", clicked=mw._add_group))
    db = QPushButton("✕")
    db.setObjectName("stopBtn")
    db.clicked.connect(mw._del_group)
    br.addWidget(db)
    ll.addLayout(br)
    sp.addWidget(left)

    # ── Right: warehouse + group programs ──
    right = QWidget()
    rl = QVBoxLayout(right)
    rl.setContentsMargins(0, 0, 0, 0)

    # Sub-tab bar
    sb = QFrame()
    sh = QHBoxLayout(sb)
    sh.setContentsMargins(0, 0, 0, 4)
    mw.tw = QPushButton("📦 仓库")
    mw.tw.setObjectName("tabBtnActive")
    mw.tw.clicked.connect(lambda: mw._st("warehouse"))
    mw.tg2 = QPushButton("📋 当前组")
    mw.tg2.setObjectName("tabBtn")
    sh.addWidget(mw.tw)
    sh.addWidget(mw.tg2)
    sh.addStretch()
    rl.addWidget(sb)

    # Warehouse table
    mw.wv = QWidget()
    wl = QVBoxLayout(mw.wv)
    wl.setContentsMargins(0, 0, 0, 0)
    ws = QHBoxLayout()
    mw.whs = QLineEdit()
    mw.whs.setPlaceholderText("搜索...")
    mw.whs.textChanged.connect(mw._rw)
    ws.addWidget(mw.whs)
    cbtn = QPushButton("✕")
    cbtn.setFixedWidth(28)
    cbtn.setToolTip("清除搜索")
    cbtn.clicked.connect(lambda: mw.whs.clear())
    ws.addWidget(cbtn)
    ws.addWidget(QPushButton("＋ 添加", clicked=mw._add_wh, objectName="addProgBtn"))
    ws.addWidget(QPushButton("检查更新", clicked=lambda: mw.maint.check_updates()))
    wl.addLayout(ws)

    mw.wt = QTableWidget()
    mw.wt.setColumnCount(4)
    mw.wt.setHorizontalHeaderLabels(["", "名称", "类型", ""])
    mw.wt.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    mw.wt.setColumnWidth(0, 30)
    mw.wt.setColumnWidth(2, 140)
    mw.wt.setColumnWidth(3, 36)
    mw.wt.setSelectionBehavior(QAbstractItemView.SelectRows)
    mw.wt.setEditTriggers(QAbstractItemView.NoEditTriggers)
    mw.wt.verticalHeader().setVisible(False)
    mw.wt.verticalHeader().setDefaultSectionSize(mw._row_h + 4)
    mw.wt.setAlternatingRowColors(True)
    mw.wt.setShowGrid(False)
    mw.wt.setContextMenuPolicy(Qt.CustomContextMenu)
    mw.wt.customContextMenuRequested.connect(mw._wh_menu)
    wl.addWidget(mw.wt)
    mw.wv.hide()
    rl.addWidget(mw.wv)

    # Group programs panel
    mw.gv2 = QWidget()
    gl2 = QVBoxLayout(mw.gv2)
    gl2.setContentsMargins(0, 0, 0, 0)

    mw.gs = QGroupBox("分组设置")
    mw.gs.hide()
    gsf = QFormLayout(mw.gs)
    mw.gn = QLineEdit()
    mw.gn.editingFinished.connect(mw._sv_gn)
    gsf.addRow("组名:", mw.gn)
    mw.gm = QComboBox()
    mw.gm.addItems(["并行", "串行"])
    mw.gm.currentTextChanged.connect(mw._sv_gm)
    gsf.addRow("模式:", mw.gm)
    gl2.addWidget(mw.gs)

    mw.gt = QTableWidget()
    mw.gt.setColumnCount(3)
    mw.gt.setHorizontalHeaderLabels(["名称", "预延迟", ""])
    mw.gt.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    mw.gt.setColumnWidth(1, 70)
    mw.gt.setColumnWidth(2, 30)
    mw.gt.setSelectionBehavior(QAbstractItemView.SelectRows)
    mw.gt.setEditTriggers(QAbstractItemView.NoEditTriggers)
    mw.gt.verticalHeader().setVisible(False)
    mw.gt.verticalHeader().setDefaultSectionSize(mw._row_h + 4)
    mw.gt.setAlternatingRowColors(True)
    mw.gt.setShowGrid(False)
    mw.gt.setContextMenuPolicy(Qt.CustomContextMenu)
    mw.gt.customContextMenuRequested.connect(mw._gt_menu)
    mw.gt.doubleClicked.connect(mw._gt_launch)
    mw.gt.hide()
    gl2.addWidget(mw.gt)

    mw.ph = QLabel("← 选择分组")
    mw.ph.setAlignment(Qt.AlignCenter)
    mw.ph.setStyleSheet("color:#888;font-size:14px")
    gl2.addWidget(mw.ph, 1)
    rl.addWidget(mw.gv2)

    sp.addWidget(right)
    sp.setStretchFactor(1, 1)
    sp.setSizes([220, 740])
    gvl.addWidget(sp, 1)

    return mw.gv, mw.tw, mw.tg2
