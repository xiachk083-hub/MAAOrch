from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QGroupBox, QLineEdit,
                               QCheckBox, QComboBox, QSpinBox, QMessageBox)


def open_batch_edit(mw: Any, selected: list[str]) -> None:
    if not selected:
        return
    accounts = [a for a in mw.accounts if a.get("id", "") in selected]
    if not accounts:
        return
    total = len(accounts)
    cur = [0]

    d = QDialog(mw)
    d.setWindowTitle("批量编辑")
    d.setMinimumSize(480, 380)
    vl = QVBoxLayout(d)
    vl.setSpacing(6)

    # Nav header
    nav_row = QHBoxLayout()
    prev_btn = QPushButton("◀ 上一页"); prev_btn.setFixedWidth(80)
    nav_info = QLabel(f"1 / {total}")
    nav_info.setAlignment(Qt.AlignCenter)
    nav_info.setStyleSheet("font-weight:bold")
    next_btn = QPushButton("下一页 ▶"); next_btn.setFixedWidth(80)
    nav_row.addWidget(prev_btn); nav_row.addWidget(nav_info, 1); nav_row.addWidget(next_btn)
    vl.addLayout(nav_row)

    # Only-modified hint
    only_modify_cb = QCheckBox("仅修改有值的字段（留空不覆盖原设置）")
    only_modify_cb.setChecked(True)
    vl.addWidget(only_modify_cb)

    # ── Fields (re-created on page flip) ──
    field_container = QVBoxLayout()
    field_container.setSpacing(6)
    vl.addLayout(field_container)

    def _build_fields(ac: dict, container: QVBoxLayout) -> dict:
        """Rebuild the field widgets for the given account. Returns dict of field widgets."""
        # Clear container: remove all items and delete widgets
        def _clear_layout(layout):
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
                elif item.layout():
                    _clear_layout(item.layout())
        _clear_layout(container)

        widgets = {}

        # Account name display
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(f"账号: {ac.get('name', '未命名')}"))
        vm = ac.get("emu_instance_index", "") or "未绑定"
        app = ac.get("game_client", "?")
        name_row.addStretch()
        name_row.addWidget(QLabel(f"VM {vm} · {app}", styleSheet="color:#888;font-size:8pt"))
        container.addLayout(name_row)
        container.addSpacing(4)

        # 默认关卡
        se = QLineEdit(ac.get("smart_stage", ""))
        se.setPlaceholderText("留空不修改")
        sr = QHBoxLayout(); sr.addWidget(QLabel("默认关卡:")); sr.addWidget(se, 1)
        container.addLayout(sr); widgets["smart_stage"] = se

        # 剿灭
        ar = QHBoxLayout()
        ar.addWidget(QLabel("剿灭:"))
        am = QComboBox()
        am.addItems(["不修改", "启用", "禁用"])
        ar.addWidget(am)
        ans = QComboBox()
        ans.addItems(["自动选择", "当期剿灭", "切尔诺伯格", "龙门外环", "龙门市区"])
        ar.addWidget(ans)
        ar.addStretch()
        container.addLayout(ar); widgets["anni_mode"] = am; widgets["anni_stage"] = ans

        # 周计划
        week_row = QHBoxLayout()
        week_row.addWidget(QLabel("周一~周日统一:"))
        we = QLineEdit("")
        we.setPlaceholderText("留空不修改")
        week_row.addWidget(we, 1)
        container.addLayout(week_row); widgets["week_all"] = we

        # 游戏客户端
        cr = QHBoxLayout()
        cr.addWidget(QLabel("客户端:"))
        cc = QComboBox()
        cc.addItems(["不修改", "官服", "B服"])
        cur_client = ac.get("game_client", "Official")
        if cur_client == "Official": cc.setCurrentIndex(1)
        elif cur_client == "Bilibili": cc.setCurrentIndex(2)
        cr.addWidget(cc)
        cr.addStretch()
        container.addLayout(cr); widgets["client"] = cc

        # 完成动作
        pr = QHBoxLayout()
        pr.addWidget(QLabel("完成后:"))
        pcs = {}
        for k, v in [("ExitEmulator", "关模拟器"), ("ExitSelf", "退出MAA")]:
            cb = QCheckBox(v)
            pcs[k] = cb; pr.addWidget(cb)
        pr.addStretch()
        container.addLayout(pr); widgets["post_cbs"] = pcs

        container.addStretch()
        return widgets

    def _load(ac: dict, w: dict) -> None:
        """Load account values into field widgets for editing."""
        se = w.get("smart_stage")
        if se: se.setText(ac.get("smart_stage", ""))
        # Don't pre-fill anni/week/client — those are "apply" not "edit"
        # Post actions: pre-fill
        post_str = ac.get("post_action", "")
        pcs = w.get("post_cbs", {})
        for k, cb in pcs.items():
            cb.setChecked(k in post_str)

    def _save_current() -> None:
        """Save current page's modifications to the account."""
        idx = cur[0]
        if idx < 0 or idx >= total:
            return
        ac = accounts[idx]
        w = field_widgets
        se = w.get("smart_stage")
        if se and se.text().strip():
            ac["smart_stage"] = se.text().strip()
        if se and se.text().strip():
            ac["fight_stage"] = se.text().strip()
        am = w.get("anni_mode")
        ans = w.get("anni_stage")
        if am and am.currentText() == "启用":
            ac["smart_annihilation_enabled"] = True
            stage_map = {"自动选择": "", "当期剿灭": "Annihilation",
                         "切尔诺伯格": "Chernobog@Annihilation",
                         "龙门外环": "LungmenOutskirts@Annihilation",
                         "龙门市区": "LungmenDowntown@Annihilation"}
            ac["smart_annihilation"] = stage_map.get(ans.currentText() if ans else "", "")
        elif am and am.currentText() == "禁用":
            ac["smart_annihilation_enabled"] = False
        we = w.get("week_all")
        if we and we.text().strip():
            for dk in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]:
                ac[f"smart_{dk}"] = we.text().strip()
        cc = w.get("client")
        if cc:
            if cc.currentText() == "官服": ac["game_client"] = "Official"
            elif cc.currentText() == "B服": ac["game_client"] = "Bilibili"
        pcs = w.get("post_cbs", {})
        selected_post = [k for k, cb in pcs.items() if cb.isChecked()]
        if selected_post:
            ac["post_action"] = ",".join(selected_post)

    def _go(idx: int) -> None:
        """Navigate to page idx."""
        if idx < 0 or idx >= total:
            return
        _save_current()
        cur[0] = idx
        ac = accounts[idx]
        w = _build_fields(ac, field_container)
        field_widgets.clear()
        field_widgets.update(w)
        _load(ac, w)
        nav_info.setText(f"{idx + 1} / {total}")
        prev_btn.setEnabled(idx > 0)
        next_btn.setEnabled(idx < total - 1)
        d.setWindowTitle(f"批量编辑 — {ac.get('name', '未命名')}")

    field_widgets = {}
    prev_btn.clicked.connect(lambda: _go(cur[0] - 1))
    next_btn.clicked.connect(lambda: _go(cur[0] + 1))

    # Buttons
    btn_row = QHBoxLayout()
    cancel_btn = QPushButton("取消")
    cancel_btn.clicked.connect(d.reject)
    btn_row.addWidget(cancel_btn)
    btn_row.addStretch()
    save_btn = QPushButton("✓ 保存并关闭")
    save_btn.setObjectName("startBtn")
    def _save_all():
        _save_current()
        mw._save()
        d.accept()
    save_btn.clicked.connect(_save_all)
    btn_row.addWidget(save_btn)
    vl.addLayout(btn_row)

    # Start on first account
    _go(0)
    d.exec()
