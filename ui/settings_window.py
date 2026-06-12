from __future__ import annotations
from typing import Any
from pathlib import Path
import json
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLabel, QPushButton, QGroupBox, QFormLayout,
                               QSpinBox, QCheckBox, QComboBox, QLineEdit, QFrame,
                               QDialogButtonBox)


def _rebuild_instances(mw: Any) -> None:
    """Force rebuild all MAA instances from existing source."""
    from services.instance_pool import ensure_maa_instances_async
    ensure_maa_instances_async(mw.ctx, force=True)
    mw._log("🔄 实例池重建完成")


def _check_latest(mw: Any) -> None:
    import json as _json, urllib.request as _ur
    try:
        resp = _ur.urlopen(
            "https://api.github.com/repos/MaaAssistantArknights/MaaRelease/releases/latest",
            timeout=5)
        data = _json.loads(resp.read())
        tag = data.get("tag_name", "?")
        mw._log(f"最新 MAA 版本: {tag}")
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, "检查更新", f"最新版本: {tag}")
    except Exception as ex:
        mw._log(f"检查更新失败: {ex}")
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(None, "检查更新", f"检查失败: {ex}")


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
    rebuild_btn.clicked.connect(lambda: _rebuild_instances(mw))
    par_row.addWidget(rebuild_btn)
    gl.addLayout(par_row)

    # Instance status list (compact cards)
    instances = cfg.get("maa_instances", 0)
    pool = Path(__file__).parent.parent / "services" / "maa" / "instances"
    grid = QGridLayout()
    grid.setSpacing(4)
    r_cnt = 0; rd_cnt = 0; row_i = 0; col_i = 0
    for i in range(1, instances + 1):
        inst_dir = pool / str(i)
        exe = inst_dir / "MAA.exe"
        if not exe.exists():
            continue
        pid_file = inst_dir / ".pid"
        running = False
        pid = ""
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                import subprocess as _sp
                r2 = _sp.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                             capture_output=True, text=True, timeout=2,
                             creationflags=_sp.CREATE_NO_WINDOW)
                running = str(pid) in r2.stdout
                pid = str(pid) if running else ""
            except Exception:
                pass
        if running: r_cnt += 1
        else: rd_cnt += 1
        gj = inst_dir / "config" / "gui.new.json"
        cfg_ok = gj.exists()
        # Compact card
        card = QFrame()
        card.setFixedHeight(28)
        card.setStyleSheet("QFrame{background:#222;border:1px solid #2a2a2a;border-radius:4px}")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(6, 0, 6, 0)
        cl.setSpacing(4)
        cl.addWidget(QLabel(f"#{i}"))
        icon = "▶" if running else "❌" if not exe.exists() else "⏹"
        ic = QLabel(icon)
        ic.setStyleSheet("color:#498205;font-weight:bold;font-size:8pt" if running else "color:#888;font-size:8pt")
        cl.addWidget(ic)
        if pid: cl.addWidget(QLabel(pid, styleSheet="color:#888;font-size:7pt"))
        cl.addWidget(QLabel("✅" if cfg_ok else "❌", styleSheet="color:#888;font-size:7pt"))
        cl.addStretch()
        grid.addWidget(card, row_i, col_i)
        col_i += 1
        if col_i >= 2:
            col_i = 0; row_i += 1
    gl.addLayout(grid)
    # Summary
    sum_row = QHBoxLayout()
    sum_row.addWidget(QLabel(f"实例池: {instances} 个"))
    sum_row.addStretch()
    sum_row.addWidget(QLabel(f"▶ {r_cnt}"))
    sum_row.addWidget(QLabel(f"⏹ {rd_cnt}"))
    sum_row.addWidget(QLabel(f"❌ {instances - r_cnt - rd_cnt}"))
    gl.addLayout(sum_row)
    vl.addWidget(g)

    # ── 📦 版本管理 ──
    gv = QGroupBox("📦 版本管理")
    gvl = QVBoxLayout(gv)
    vr = QHBoxLayout()
    vr.addWidget(QLabel(f"当前版本: {cfg.get('maa_version', '未安装')}"))
    vr.addStretch()
    chk = QPushButton("🔍 检查更新")
    chk.clicked.connect(lambda: _check_latest(mw))
    vr.addWidget(chk)
    gvl.addLayout(vr)
    ig = QGridLayout()
    ig.setSpacing(4)
    ri = 0
    for i in range(1, instances + 1):
        inst_dir = pool / str(i)
        exe = inst_dir / "MAA.exe"
        if not exe.exists():
            continue
        fp = inst_dir / "config" / "gui.new.json"
        if not fp.exists():
            fp = inst_dir / "config" / "gui.json"
        ver = "?"
        if fp.exists():
            try:
                ver = json.loads(fp.read_text(encoding='utf-8')).get("VersionUpdate", {}).get("version", "?")
            except Exception:
                pass
        run = "▶" if (inst_dir / ".pid").exists() else "⏹"
        ig.addWidget(QLabel(f"#{i} ✅ v{ver} {run}"), ri, 0)
        ri += 1
    gvl.addLayout(ig)
    vl.addWidget(gv)

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
    cur_path = cfg.get("maa_source_path", "") or _find_maa_source(cfg)
    src_path_edit = QLineEdit(str(cur_path) if cur_path else "")
    src_path_edit.setPlaceholderText("选择 MAA 目录...")
    src_path_edit.setReadOnly(True)
    src_row = QHBoxLayout()
    src_row.addWidget(src_path_edit, 1)
    browse_btn = QPushButton("浏览…")
    browse_btn.clicked.connect(lambda: _browse_maa(src_path_edit, cfg))
    src_row.addWidget(browse_btn)
    src_row.addStretch()
    exe_ok = cur_path and Path(cur_path).joinpath("MAA.exe").exists()
    src_row.addWidget(QLabel("✅" if exe_ok else ""))
    gl4.addLayout(src_row)
    vl.addWidget(g4)

    vl.addStretch()

    # ── Buttons ──
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    def _browse_maa(edit, cfg):
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(d, "选择 MAA 目录", str(edit.text()))
        if folder and Path(folder).joinpath("MAA.exe").exists():
            edit.setText(folder)
            cfg["maa_source_path"] = folder
        elif folder:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(d, "提示", "所选目录未找到 MAA.exe")
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
