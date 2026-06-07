"""MAA instance pool management tab."""
from __future__ import annotations
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QFormLayout, QSpinBox,
)


def build_maa_panel(mw: Any) -> QWidget:
    mw.maa_v = QWidget()
    vl = QVBoxLayout(mw.maa_v)
    vl.setContentsMargins(12, 10, 12, 6)
    vl.setSpacing(4)

    # Header
    hdr = QHBoxLayout()
    hdr.addWidget(QLabel("📦 MAA 实例池", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))
    dl_btn = QPushButton("⬇ 下载 / 更新")
    dl_btn.setObjectName("startBtn")
    dl_btn.clicked.connect(lambda: mw.maint.dl_maa_all())
    hdr.addWidget(dl_btn)
    hdr.addStretch()
    vl.addLayout(hdr)

    # Status info
    info_group = QGroupBox("实例信息")
    info_form = QFormLayout(info_group)
    mw._maa_version_lbl = QLabel(mw.config.get("maa_version", "未安装"))
    info_form.addRow("已安装版本:", mw._maa_version_lbl)

    parallel_row = QHBoxLayout()
    mw._maa_parallel_sp = QSpinBox()
    mw._maa_parallel_sp.setRange(1, 10)
    mw._maa_parallel_sp.setValue(mw.config.get("parallel_max", 1))
    mw._maa_parallel_sp.valueChanged.connect(lambda v: (mw.config.update({"parallel_max": v}), mw._save()))
    parallel_row.addWidget(mw._maa_parallel_sp)
    parallel_row.addWidget(QLabel(" 个并行实例"))
    parallel_row.addStretch()
    info_form.addRow("并行上限:", parallel_row)

    vl.addWidget(info_group)

    # Instance table
    vl.addWidget(QLabel("实例状态", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    tbl = QTableWidget(0, 4)
    tbl.setHorizontalHeaderLabels(["实例#", "状态", "PID", "配置文件"])
    tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
    tbl.setColumnWidth(0, 60)
    tbl.setColumnWidth(1, 70)
    tbl.setColumnWidth(2, 80)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(28)
    tbl.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    mw._maa_tbl = tbl
    vl.addWidget(tbl, 1)

    refresh_btn = QPushButton("🔄 刷新状态")
    refresh_btn.clicked.connect(lambda: refresh_maa_panel(mw))
    vl.addWidget(refresh_btn)

    refresh_maa_panel(mw)
    return mw.maa_v


def refresh_maa_panel(mw: Any) -> None:
    if not hasattr(mw, "_maa_tbl"):
        return
    # Update version label
    mw._maa_version_lbl.setText(mw.config.get("maa_version", "未安装"))
    ver = mw.config.get("maa_version", "")
    max_created = mw.config.get("maa_instances", 0)
    max_n = mw.config.get("parallel_max", 1)
    pool = Path(__file__).parent.parent / "maa" / "instances"
    tbl = mw._maa_tbl
    tbl.setRowCount(max_n)
    import subprocess, os

    for i in range(1, max_n + 1):
        inst = pool / str(i)
        exe = inst / "MAA.exe"
        exists = exe.exists()
        pid = ""
        running = False
        pid_file = inst / ".pid"
        if pid_file.exists():
            try:
                pid = pid_file.read_text().strip()
                r = subprocess.run(
                    ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                    capture_output=True, text=True, timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                running = str(pid) in r.stdout
                if not running:
                    pid = ""
                    pid_file.unlink(missing_ok=True)
            except:
                pid_file.unlink(missing_ok=True)

        tbl.setItem(i - 1, 0, QTableWidgetItem(f"#{i}"))
        if not exists and i <= max_created:
            tbl.setItem(i - 1, 1, QTableWidgetItem("未创建"))
        elif not exists:
            tbl.setItem(i - 1, 1, QTableWidgetItem("按需创建"))
        elif running:
            tbl.setItem(i - 1, 1, QTableWidgetItem("▶ 运行中"))
        else:
            tbl.setItem(i - 1, 1, QTableWidgetItem("⏸ 空闲"))
        tbl.setItem(i - 1, 2, QTableWidgetItem(pid if exists else ""))
        cfg = str(inst / "config") if exists else ""
        tbl.setItem(i - 1, 3, QTableWidgetItem(cfg))
