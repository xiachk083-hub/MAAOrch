from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QCheckBox)
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl


def check_onboarding(mw) -> None:
    if mw.config.get("onboarding_done"):
        return
    d = QDialog(mw)
    d.setWindowTitle("欢迎使用 MAAOrch")
    d.setFixedSize(440, 340)
    vl = QVBoxLayout(d)
    vl.setSpacing(12)

    title = QLabel("欢迎使用 MAAOrch")
    title.setStyleSheet("font-size:16pt;font-weight:bold;color:#498205")
    title.setAlignment(Qt.AlignCenter)
    vl.addWidget(title)

    desc = QLabel("以下步骤将帮助你快速上手")
    desc.setAlignment(Qt.AlignCenter)
    desc.setStyleSheet("color:#888;font-size:9pt")
    vl.addWidget(desc)

    s1 = QLabel("1. 将 MAA 放入 maa/source/ 目录")
    s1.setStyleSheet("font-size:10pt")
    s1_btn = QPushButton("打开文件夹")
    s1_btn.setFixedHeight(24)
    def _open_maa_dir():
        p = Path(__file__).parent.parent / "maa" / "source"
        p.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
    s1_btn.clicked.connect(_open_maa_dir)
    s1_row = QHBoxLayout()
    s1_row.addWidget(s1, 1)
    s1_row.addWidget(s1_btn)
    vl.addLayout(s1_row)

    s2 = QLabel("2. 创建你的第一个账号")
    s2.setStyleSheet("font-size:10pt")
    s2_btn = QPushButton("创建账号")
    s2_btn.setFixedHeight(24)
    def _create_account():
        from ui.create_account import CreateAccountDialog
        dlg = CreateAccountDialog(mw)
        dlg.exec()
    s2_btn.clicked.connect(_create_account)
    s2_row = QHBoxLayout()
    s2_row.addWidget(s2, 1)
    s2_row.addWidget(s2_btn)
    vl.addLayout(s2_row)

    s3 = QLabel("3. 配置智能调度或手动启动")
    s3.setStyleSheet("font-size:10pt")
    vl.addWidget(s3)

    vl.addStretch()

    cb = QCheckBox("不再显示")
    vl.addWidget(cb)

    btn = QPushButton("开始使用")
    btn.setObjectName("startBtn")
    btn.setFixedHeight(30)
    def _done():
        mw.config["onboarding_done"] = True
        mw._save()
        d.accept()
    btn.clicked.connect(_done)
    vl.addWidget(btn)

    d.exec()
