from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QScrollArea, QWidget, QFrame, QRadioButton)
from infrastructure.task_constants import detect_emu_instances


class EmulatorSelector(QDialog):
    """Searchable, grouped emulator instance selector dialog."""

    selected: dict | None = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择模拟器实例")
        self.setMinimumSize(520, 420)
        self.setModal(True)
        self._instances = detect_emu_instances()
        self._filtered: list[dict] = list(self._instances)
        self._build_ui()

    def _build_ui(self):
        vl = QVBoxLayout(self)
        vl.setContentsMargins(16, 16, 16, 12)
        vl.setSpacing(8)

        hdr = QLabel("选择模拟器实例")
        hdr.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        vl.addWidget(hdr)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索实例名称或地址...")
        self._search.textChanged.connect(self._on_search)
        vl.addWidget(self._search)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._container = QWidget()
        self._container.setObjectName("emuSelectorContainer")
        self._cl = QVBoxLayout(self._container)
        self._cl.setContentsMargins(0, 0, 0, 0)
        self._cl.setSpacing(1)
        self._scroll.setWidget(self._container)
        vl.addWidget(self._scroll, 1)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        self._ok_btn = QPushButton("确定选择")
        self._ok_btn.setObjectName("primaryBtn")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._ok_btn)
        vl.addLayout(btn_row)

        self._render_groups()

    def _render_groups(self):
        # Clear existing
        while self._cl.count():
            item = self._cl.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._filtered:
            empty = QLabel("未检测到模拟器实例")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#666;padding:40px;font-size:10pt")
            self._cl.addWidget(empty)
            return

        # Group by emu type
        groups: dict[str, list[dict]] = {}
        for inst in self._filtered:
            emu_type = inst.get("emu", "Other")
            groups.setdefault(emu_type, []).append(inst)

        self._radio_grp: list[QRadioButton] = []
        for emu_type, insts in groups.items():
            # Group header
            header = QLabel(f"── {emu_type} ({len(insts)}) ──")
            header.setStyleSheet("color:#888;font-size:8pt;padding:6px 0 2px 8px")
            self._cl.addWidget(header)

            for inst in insts:
                self._cl.addWidget(self._make_row(inst))

    def _make_row(self, inst: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("emuRow")
        row.setFixedHeight(38)
        row.setCursor(Qt.PointingHandCursor)
        hl = QHBoxLayout(row)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(8)

        radio = QRadioButton()
        radio.inst_data = inst
        radio.toggled.connect(self._on_selected)
        self._radio_grp.append(radio)
        hl.addWidget(radio)

        name = inst.get("name", f"实例#{inst.get('index','')}")
        nm = QLabel(name)
        nm.setStyleSheet("color:#ddd;font-size:9pt")
        hl.addWidget(nm, 1)

        port = inst.get("adb_port", "")
        addr = f"127.0.0.1:{port}" if port else ""
        ad = QLabel(addr)
        ad.setStyleSheet("color:#888;font-size:8pt")
        ad.setFixedWidth(130)
        hl.addWidget(ad)

        running = inst.get("running", False)
        status = QLabel("▶ 运行中" if running else "⏹ 已停止")
        status.setStyleSheet(
            "color:#498205;font-size:8pt;font-weight:bold" if running
            else "color:#666;font-size:8pt"
        )
        status.setFixedWidth(70)
        hl.addWidget(status)

        # Click row → toggle radio
        row.mousePressEvent = lambda e, r=radio: r.setChecked(True)

        return row

    def _on_search(self, text: str):
        t = text.strip().lower()
        if not t:
            self._filtered = list(self._instances)
        else:
            self._filtered = [
                inst for inst in self._instances
                if t in inst.get("name", "").lower()
                or t in str(inst.get("adb_port", ""))
                or t in inst.get("emu", "").lower()
                or t in str(inst.get("index", ""))
            ]
        self._render_groups()

    def _on_selected(self):
        checked = any(r.isChecked() for r in self._radio_grp)
        self._ok_btn.setEnabled(checked)

    def get_selected(self) -> dict | None:
        for r in self._radio_grp:
            if r.isChecked():
                return r.inst_data
        return None

    @staticmethod
    def select(parent=None) -> dict | None:
        dlg = EmulatorSelector(parent)
        if dlg.exec() == QDialog.Accepted:
            return dlg.get_selected()
        return None
