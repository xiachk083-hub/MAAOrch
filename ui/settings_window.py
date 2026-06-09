from __future__ import annotations
from typing import Any
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QGroupBox, QFormLayout, QSpinBox,
                               QCheckBox, QComboBox, QLineEdit, QTableWidget,
                               QTableWidgetItem, QHeaderView, QAbstractItemView,
                               QDialogButtonBox)


def open_settings(mw: Any) -> None:
    d = QDialog(mw)
    d.setWindowTitle("设置")
    d.setMinimumSize(560, 480)
    vl = QVBoxLayout(d)
    vl.setSpacing(8)

    cfg = mw.config

    # ── MAA 实例 ──
    g = QGroupBox("MAA 实例")
    gl = QVBoxLayout(g)
    ver_row = QHBoxLayout()
    ver_row.addWidget(QLabel(f"版本: {cfg.get('maa_version', '未安装')}"))
    ver_row.addStretch()
    dl_btn = QPushButton("⚡更新")
    dl_btn.clicked.connect(lambda: mw.maint.dl_maa_all())
    ver_row.addWidget(dl_btn)
    gl.addLayout(ver_row)

    par_row = QHBoxLayout()
    par_row.addWidget(QLabel("并行上限:"))
    parallel_sp = QSpinBox()
    parallel_sp.setRange(1, 10)
    parallel_sp.setValue(cfg.get("parallel_max", 1))
    parallel_sp.valueChanged.connect(lambda v: cfg.update({"parallel_max": v}))
    par_row.addWidget(parallel_sp)
    par_row.addWidget(QLabel("个实例"))
    par_row.addStretch()
    rebuild_btn = QPushButton("🔄 重建实例")
    rebuild_btn.clicked.connect(lambda: mw.maint.dl_maa_all())
    par_row.addWidget(rebuild_btn)
    gl.addLayout(par_row)

    # Instance status table (compact)
    tbl = QTableWidget(0, 4)
    tbl.setHorizontalHeaderLabels(["#", "状态", "配置", "PID"])
    tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
    tbl.setColumnWidth(0, 30)
    tbl.setColumnWidth(1, 60)
    tbl.setColumnWidth(3, 70)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(24)
    instances = cfg.get("maa_instances", 0)
    pool = Path(__file__).parent.parent / "maa" / "instances"
    for i in range(1, instances + 1):
        r = tbl.rowCount()
        tbl.insertRow(r)
        tbl.setItem(r, 0, QTableWidgetItem(str(i)))
        inst_dir = pool / str(i)
        exe = inst_dir / "MAA.exe"
        if not exe.exists():
            tbl.setItem(r, 1, QTableWidgetItem("未创建"))
            continue
        pid_file = inst_dir / ".pid"
        running = False
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                import subprocess
                r2 = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                                    capture_output=True, text=True, timeout=2,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
                running = str(pid) in r2.stdout
                tbl.setItem(r, 3, QTableWidgetItem(str(pid) if running else ""))
            except Exception:
                pass
        tbl.setItem(r, 1, QTableWidgetItem("▶ 运行中" if running else "⏹ 就绪"))
        gj = inst_dir / "config" / "gui.new.json"
        cfg_ok = gj.exists()
        tbl.setItem(r, 2, QTableWidgetItem("✅" if cfg_ok else "❌"))
    tbl.setMaximumHeight(min(instances, 5) * 26 + 28)
    gl.addWidget(tbl)
    vl.addWidget(g)

    # ── 外观 ──
    g2 = QGroupBox("外观")
    gl2 = QFormLayout(g2)
    theme_cb = QComboBox()
    theme_cb.addItems(["Dark", "Light", "Notepaper"])
    theme_cb.setCurrentText(cfg.get("appearance_mode", "Dark"))
    gl2.addRow("主题:", theme_cb)
    tray_cb = QCheckBox("启动时最小化到托盘")
    tray_cb.setChecked(cfg.get("minimize_to_tray", True))
    gl2.addRow("", tray_cb)
    vl.addWidget(g2)

    # ── 网络 ──
    g3 = QGroupBox("网络")
    gl3 = QFormLayout(g3)
    api_port_sp = QSpinBox()
    api_port_sp.setRange(1024, 65535)
    api_port_sp.setValue(cfg.get("api_port", 19999))
    gl3.addRow("API 端口:", api_port_sp)
    api_token_edit = QLineEdit(cfg.get("api_token", ""))
    api_token_edit.setPlaceholderText("留空不验证")
    gl3.addRow("Token:", api_token_edit)

    update_row = QHBoxLayout()
    update_cb = QCheckBox("启动时检查")
    update_cb.setChecked(cfg.get("check_update_on_start", True))
    update_row.addWidget(update_cb)
    auto_dl_cb = QCheckBox("自动下载")
    auto_dl_cb.setChecked(cfg.get("auto_update_maa", True))
    update_row.addWidget(auto_dl_cb)
    update_row.addStretch()
    gl3.addRow("更新检查:", update_row)

    webhook_edit = QLineEdit(cfg.get("webhook_url", ""))
    webhook_edit.setPlaceholderText("企业微信/钉钉/自定义 URL")
    gl3.addRow("Webhook:", webhook_edit)
    vl.addWidget(g3)

    # ── MAA 源目录 ──
    g4 = QGroupBox("MAA 源目录")
    gl4 = QVBoxLayout(g4)
    from services.instance_pool import _find_maa_source
    maa_path = _find_maa_source()
    src_dir = maa_path if maa_path else (Path(__file__).parent.parent / "services" / "maa" / "source")
    src_row = QHBoxLayout()
    src_row.addWidget(QLabel(str(src_dir)))
    open_btn = QPushButton("📂 打开目录")
    open_btn.clicked.connect(lambda: __import__('os').startfile(str(src_dir)))
    src_row.addWidget(open_btn)
    src_row.addStretch()
    exe_ok = src_dir and (src_dir / "MAA.exe").exists()
    src_row.addWidget(QLabel("✅ 已就绪" if exe_ok else "❌ 未找到 MAA.exe"))
    gl4.addLayout(src_row)
    vl.addWidget(g4)

    vl.addStretch()

    # ── Buttons ──
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    def _save():
        cfg["appearance_mode"] = theme_cb.currentText()
        cfg["minimize_to_tray"] = tray_cb.isChecked()
        cfg["api_port"] = api_port_sp.value()
        cfg["api_token"] = api_token_edit.text().strip()
        cfg["check_update_on_start"] = update_cb.isChecked()
        cfg["auto_update_maa"] = auto_dl_cb.isChecked()
        cfg["webhook_url"] = webhook_edit.text().strip()
        mw._set_theme(cfg.get("appearance_mode", "Dark"))
        mw._save()
        d.accept()
    bb.accepted.connect(_save)
    bb.rejected.connect(d.reject)
    vl.addWidget(bb)

    d.exec()
