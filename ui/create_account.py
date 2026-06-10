from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QGroupBox, QCheckBox, QDialogButtonBox, QWidget)
from infrastructure.task_constants import TASK_NAMES, find_adb, find_mumu_cli
from models.account import Account
from ui.emu_selector import EmulatorSelector
from pathlib import Path


PRESET_MAP = {"MuMu 12":"MuMuEmulator12","MuMu":"MuMu","MuMu 6":"MuMu",
              "雷电 9":"LDPlayer","雷电":"LDPlayer","蓝叠":"BlueStacks",
              "夜神":"Nox","逍遥":"XYAZ"}

DEFAULT_TASKS = {"StartUp", "Award"}
DAILY_TASKS   = {"StartUp", "Award", "Fight", "Recruit", "Infrast", "Mall"}
FULL_TASKS    = {"StartUp", "Award", "Fight", "Recruit", "Infrast",
                 "Mall", "Roguelike", "Reclamation"}


def generate_name(inst: dict, all_accounts: list) -> str:
    base = inst.get("name", f"实例#{inst.get('index','')}")
    same_emu = [a for a in all_accounts
                if a.get("emu_instance_index") == inst.get("index")
                and a.get("connection_preset") == PRESET_MAP.get(inst.get("emu",""), "General")]
    if same_emu:
        return f"{base}#{inst.get('index','')}"
    return base


class CreateAccountDialog(QDialog):
    def __init__(self, mw: Any):
        super().__init__(mw)
        self.setWindowTitle("新建账号")
        self.setMinimumSize(460, 340)
        self._mw = mw
        self._inst: dict | None = None
        self._build_ui()

    def _build_ui(self):
        vl = QVBoxLayout(self)
        vl.setContentsMargins(16, 16, 16, 12)
        vl.setSpacing(8)

        hdr = QLabel("新建账号")
        hdr.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        vl.addWidget(hdr)

        # ── Simulator selection ──
        emu_row = QHBoxLayout()
        self._emu_label = QLabel("未选择")
        self._emu_label.setStyleSheet("color:#888")
        emu_row.addWidget(QLabel("模拟器:"))
        emu_row.addWidget(self._emu_label, 1)
        sel_btn = QPushButton("选择...")
        sel_btn.clicked.connect(self._pick_emu)
        emu_row.addWidget(sel_btn)
        vl.addLayout(emu_row)

        # ── Name ──
        nrow = QHBoxLayout()
        self._name = QLineEdit()
        self._name.setPlaceholderText("留空自动生成")
        nrow.addWidget(QLabel("名称:"))
        nrow.addWidget(self._name, 1)
        vl.addLayout(nrow)

        # ── ADB info (read-only summary) ──
        self._adb_info = QLabel("")
        self._adb_info.setStyleSheet("color:#666;font-size:8pt;padding:0 0 0 6px")
        vl.addWidget(self._adb_info)

        # ── Tasks ──
        g = QGroupBox("任务")
        gl = QVBoxLayout(g)
        gl.setSpacing(2)
        btn_row = QHBoxLayout()
        for label, task_set in [("基础", DEFAULT_TASKS), ("日常", DAILY_TASKS), ("全量", FULL_TASKS)]:
            btn = QPushButton(label)
            btn.setFixedHeight(22)
            btn.clicked.connect(lambda _, s=task_set: self._set_tasks(s))
            btn_row.addWidget(btn)
        btn_row.addStretch()
        gl.addLayout(btn_row)

        trow = QHBoxLayout()
        self._task_cbs: dict[str, QCheckBox] = {}
        for key, name in TASK_NAMES.items():
            cb = QCheckBox(name)
            cb.setChecked(key in DEFAULT_TASKS)
            self._task_cbs[key] = cb
            trow.addWidget(cb)
        trow.addStretch()
        gl.addLayout(trow)
        vl.addWidget(g)

        # ── Post-action ──
        prow = QHBoxLayout()
        self._post_cbs: dict[str, QCheckBox] = {}
        for k, v in [("ExitArknights","退出游戏"),("ExitEmulator","关模拟器"),("ExitSelf","退出MAA")]:
            cb = QCheckBox(v)
            cb.setChecked(True)
            self._post_cbs[k] = cb
            prow.addWidget(cb)
        prow.addStretch()
        vl.addWidget(QLabel("完成后:"))
        vl.addLayout(prow)

        vl.addStretch()

        # ── Buttons ──
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        vl.addWidget(bb)

    def _pick_emu(self):
        inst = EmulatorSelector.select(self)
        if inst:
            self._inst = inst
            self._emu_label.setText(inst.get("name", f"实例#{inst.get('index','')}"))
            self._emu_label.setStyleSheet("color:#ddd")
            # Auto-generate name
            all_accs = getattr(self._mw, "accounts", [])
            self._name.setText(generate_name(inst, all_accs))
            # Show ADB info
            port = inst.get("adb_port", "")
            addr = f"127.0.0.1:{port}" if port else "127.0.0.1:5555"
            self._adb_info.setText(f"ADB: {addr}")

    def _set_tasks(self, task_set: set[str]):
        for key, cb in self._task_cbs.items():
            cb.setChecked(key in task_set)

    def _save(self):
        inst = self._inst
        if not inst:
            self._emu_label.setStyleSheet("color:#e04040")
            self._emu_label.setText("请先选择模拟器")
            return

        a = Account()
        a.name = self._name.text().strip() or generate_name(inst, getattr(self._mw, "accounts", []))
        a.emu_instance_index = inst["index"]
        a.connection_preset = PRESET_MAP.get(inst.get("emu", ""), "General")
        a.touch_mode = "MiniTouch"
        port = inst.get("adb_port", "")
        if port:
            a.adb_address = f"127.0.0.1:{port}"
        else:
            a.adb_address = f"127.0.0.1:{5555 + int(inst.get('index',0)) * 2}"

        # Find adb
        adb_exe = find_adb()
        if not adb_exe:
            cli = find_mumu_cli()
            if cli:
                cand = Path(cli).parent / "adb.exe"
                if cand.exists():
                    adb_exe = str(cand)
        if adb_exe:
            a.adb_path = adb_exe

        # Tasks → pipeline
        enabled = [k for k, cb in self._task_cbs.items() if cb.isChecked()]
        a.task_pipeline = ",".join(enabled)

        # Post-action
        selected = [k for k, cb in self._post_cbs.items() if cb.isChecked()]
        a.post_action = ",".join(selected) if selected else ""

        self._mw.accounts.append(a)
        self._mw._save()
        from ui.smart_panel import _rebuild_list
        _rebuild_list(self._mw)
        self._mw._log(f"已添加账号: {a.name}")
        self.accept()
