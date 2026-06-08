from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QGroupBox, QLineEdit,
                               QCheckBox, QComboBox, QDialogButtonBox)


def open_batch_edit(mw: Any, selected: list[str]) -> None:
    if not selected:
        return
    d = QDialog(mw)
    d.setWindowTitle(f"批量设置 — 已选 {len(selected)} 个账号")
    d.setMinimumSize(380, 280)
    vl = QVBoxLayout(d)
    vl.setSpacing(6)

    vl.addWidget(QLabel("只填要修改的字段，留空=不修改",
                        font=QFont("Microsoft YaHei UI", 9)))
    vl.addSpacing(4)

    # 默认关卡
    stage_edit = QLineEdit()
    stage_edit.setPlaceholderText("留空不修改")
    stage_row = QHBoxLayout()
    stage_row.addWidget(QLabel("默认关卡:"))
    stage_row.addWidget(stage_edit, 1)
    vl.addLayout(stage_row)

    # 剿灭
    anni_row = QHBoxLayout()
    anni_row.addWidget(QLabel("剿灭:"))
    anni_mode = QComboBox()
    anni_mode.addItems(["不修改", "启用", "禁用"])
    anni_row.addWidget(anni_mode)
    anni_stage = QComboBox()
    anni_stage.addItems(["自动选择", "当期剿灭", "切尔诺伯格", "龙门外环", "龙门市区"])
    anni_row.addWidget(anni_stage)
    anni_row.addStretch()
    vl.addLayout(anni_row)

    # 周批量
    week_row = QHBoxLayout()
    week_row.addWidget(QLabel("周一~周日:"))
    week_edit = QLineEdit()
    week_edit.setPlaceholderText("统一设为...")
    week_row.addWidget(week_edit, 1)
    week_btn = QPushButton("应用到全部")
    week_row.addWidget(week_btn)
    vl.addLayout(week_row)

    # 完成后
    post_row = QHBoxLayout()
    post_row.addWidget(QLabel("完成后:"))
    post_cbs = {}
    for k, v in [("ExitArknights", "退出游戏"), ("ExitSelf", "退出MAA"),
                  ("ExitEmulator", "关模拟器")]:
        cb = QCheckBox(v)
        post_cbs[k] = cb
        post_row.addWidget(cb)
    post_row.addStretch()
    vl.addLayout(post_row)

    # 客户端
    client_row = QHBoxLayout()
    client_row.addWidget(QLabel("客户端:"))
    client_cb = QComboBox()
    client_cb.addItems(["不修改", "官服", "B服"])
    client_row.addWidget(client_cb)
    client_row.addStretch()
    vl.addLayout(client_row)

    vl.addStretch()

    def _apply():
        stage = stage_edit.text().strip()
        anni_mode_val = anni_mode.currentText()
        anni_stage_val = anni_stage.currentText()
        week_val = week_edit.text().strip()
        selected_post = [k for k, cb in post_cbs.items() if cb.isChecked()]
        client_val = client_cb.currentText()

        for a in mw.accounts:
            if a.get("id", "") not in selected:
                continue
            if stage:
                a["smart_stage"] = stage
            if anni_mode_val == "启用":
                a["smart_annihilation_enabled"] = True
                a["smart_annihilation"] = {"自动选择": "", "当期剿灭": "Annihilation",
                                            "切尔诺伯格": "Chernobog@Annihilation",
                                            "龙门外环": "LungmenOutskirts@Annihilation",
                                            "龙门市区": "LungmenDowntown@Annihilation"}.get(anni_stage_val, "")
            elif anni_mode_val == "禁用":
                a["smart_annihilation_enabled"] = False
            if week_val:
                for dk in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]:
                    a[f"smart_{dk}"] = week_val
            if selected_post:
                a["post_action"] = ",".join(selected_post)
            if client_val == "官服":
                a["game_client"] = "Official"
            elif client_val == "B服":
                a["game_client"] = "Bilibili"
        mw._save()
        d.accept()

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(_apply)
    bb.rejected.connect(d.reject)
    vl.addWidget(bb)

    d.exec()
