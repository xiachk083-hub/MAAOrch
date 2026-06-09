from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QGroupBox, QFormLayout, QSpinBox,
                               QCheckBox, QComboBox, QLineEdit, QDialogButtonBox)
from infrastructure.task_constants import EMU_PRESETS, TASK_NAMES, find_mumu_cli, detect_emu_instances


def open_account_detail(mw: Any, row: int) -> None:
    if row < 0 or row >= len(mw.accounts):
        return
    ac = mw.accounts[row]
    d = QDialog(mw)
    d.setWindowTitle(f"账号详情 — {ac.get('name', '')}")
    d.setMinimumSize(440, 360)
    vl = QVBoxLayout(d)
    vl.setSpacing(6)

    # ── 基本信息 ──
    name_row = QHBoxLayout()
    name_edit = QLineEdit(ac.get("name", ""))
    name_edit.setPlaceholderText("账号名")
    name_row.addWidget(QLabel("账号名:"))
    name_row.addWidget(name_edit, 1)
    client_cb = QComboBox()
    for k, v in {"Official": "官服", "Bilibili": "B服", "YoStarEN": "国际服",
                  "YoStarJP": "日服", "YoStarKR": "韩服", "txwy": "繁中"}.items():
        client_cb.addItem(v, k)
    idx = client_cb.findData(ac.get("game_client", "Official"))
    if idx >= 0:
        client_cb.setCurrentIndex(idx)
    name_row.addWidget(QLabel("客户端:"))
    name_row.addWidget(client_cb)
    vl.addLayout(name_row)

    # ── 连接 ──
    g1 = QGroupBox("连接")
    gl1 = QFormLayout(g1)
    gl1.setSpacing(4)

    emu_row = QHBoxLayout()
    emu_combo = QComboBox()
    emu_combo.setMinimumWidth(180)
    # Populate emulator instances
    instances = detect_emu_instances()
    current_idx = ac.get("emu_instance_index", "")
    found = False
    for inst in instances:
        label = f"MuMu12 #{inst['index']} - {inst.get('name', '')}"
        emu_combo.addItem(label, inst["index"])
        if inst["index"] == current_idx:
            emu_combo.setCurrentIndex(emu_combo.count() - 1)
            found = True
    if not found and current_idx:
        emu_combo.addItem(f"MuMu12 #{current_idx}", current_idx)
        emu_combo.setCurrentIndex(emu_combo.count() - 1)
    emu_combo.currentIndexChanged.connect(lambda: _on_emu_changed(emu_combo, ac))
    emu_row.addWidget(emu_combo)
    launch_cb = QCheckBox("自启")
    launch_cb.setChecked(ac.get("emu_launch", False))
    emu_row.addWidget(launch_cb)
    emu_row.addWidget(QLabel("等待:"))
    wait_sp = QSpinBox()
    wait_sp.setRange(0, 300)
    wait_sp.setValue(ac.get("emu_wait", 30))
    wait_sp.setSuffix(" 秒")
    emu_row.addWidget(wait_sp)
    emu_row.addStretch()
    gl1.addRow("模拟器:", emu_row)

    cp = ac.get('connection_preset', '')
    preset_name = '通用'
    for p in EMU_PRESETS:
        if isinstance(p, dict) and p.get('key', '') == cp:
            preset_name = p.get('name', cp)
            break
    preset_label = QLabel(f"连接预设: {preset_name} (自动)")
    touch_label = QLabel(f"触摸: {ac.get('touch_mode', 'MiniTouch')} (自动)")
    gl1.addRow("", preset_label)
    gl1.addRow("", touch_label)
    vl.addWidget(g1)

    # ── 启动 ──
    g2 = QGroupBox("启动")
    gl2 = QVBoxLayout(g2)
    gl2.setSpacing(4)
    opt_row = QHBoxLayout()
    min_cb = QCheckBox("启动后最小化")
    min_cb.setChecked(ac.get("start_minimized", False))
    opt_row.addWidget(min_cb)
    dir_cb = QCheckBox("直接运行")
    dir_cb.setChecked(ac.get("start_directly", False))
    opt_row.addWidget(dir_cb)
    emu_fail_cb = QCheckBox("ADB失败启模拟器")
    emu_fail_cb.setChecked(ac.get("adb_fail_launch_emu", False))
    opt_row.addWidget(emu_fail_cb)
    opt_row.addStretch()
    gl2.addLayout(opt_row)

    post_row = QHBoxLayout()
    current_post = ac.get("post_action", "ExitArknights,ExitSelf")
    post_set = set(current_post.split(",")) if current_post else set()
    post_cbs = {}
    for k, v in [("ExitArknights", "退出游戏"), ("ExitSelf", "退出MAA"),
                  ("ExitEmulator", "关模拟器"), ("BackToAndroidHome", "返回主屏")]:
        cb = QCheckBox(v)
        cb.setChecked(k in post_set)
        post_cbs[k] = cb
        post_row.addWidget(cb)
    post_row.addStretch()
    gl2.addWidget(QLabel("完成后:"))
    gl2.addLayout(post_row)
    vl.addWidget(g2)

    # ── 任务 (仅非智能调度时可用) ──
    smart_enabled = mw.config.get("smart_global", {}).get("enabled", False)
    if not smart_enabled:
        g3 = QGroupBox("任务")
        gl3 = QVBoxLayout(g3)
        gl3.setSpacing(4)
        task_row = QHBoxLayout()
        task_cbs = {}
        progs = [w for w in mw.warehouse if w.get("account_ref") == ac.get("id", "")]
        pipeline = progs[0].get("task_pipeline", "") if progs else ""
        enabled_tasks = set(t.strip().lower() for t in pipeline.split(",") if t.strip())
        for task_key, task_name in TASK_NAMES.items():
            cb = QCheckBox(task_name)
            cb.setChecked(task_key in enabled_tasks)
            task_cbs[task_key] = cb
            task_row.addWidget(cb)
        task_row.addStretch()
        gl3.addLayout(task_row)
        sync_cb = QCheckBox("启动时同步")
        sync_cb.setChecked(progs[0].get("sync_tasks", False) if progs else False)
        gl3.addWidget(sync_cb)
        vl.addWidget(g3)

    vl.addStretch()

    # ── Buttons ──
    def _save():
        ac["name"] = name_edit.text().strip()
        ac["game_client"] = client_cb.currentData()
        ac["emu_instance_index"] = emu_combo.currentData() or ""
        ac["emu_launch"] = launch_cb.isChecked()
        ac["emu_wait"] = wait_sp.value()
        ac["start_minimized"] = min_cb.isChecked()
        ac["start_directly"] = dir_cb.isChecked()
        ac["adb_fail_launch_emu"] = emu_fail_cb.isChecked()
        selected_post = [k for k, cb in post_cbs.items() if cb.isChecked()]
        ac["post_action"] = ",".join(selected_post) if selected_post else ""
        if not smart_enabled and progs:
            enabled = [k for k, cb in task_cbs.items() if cb.isChecked()]
            progs[0]["task_pipeline"] = ",".join(enabled)
            progs[0]["sync_tasks"] = sync_cb.isChecked()
        mw._save()
        d.accept()

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(_save)
    bb.rejected.connect(d.reject)
    vl.addWidget(bb)

    d.exec()


def _on_emu_changed(combo: QComboBox, ac: dict) -> None:
    idx = combo.currentData()
    if not idx:
        return
    from infrastructure.task_constants import MUMU_INSTANCE_DIRS
    port = 5555 + int(idx) * 2
    ac["adb_address"] = f"127.0.0.1:{port}"
    ac["connection_preset"] = "MuMuPro"
    ac["touch_mode"] = "MiniTouch"
    for d in MUMU_INSTANCE_DIRS:
        cfg_file = Path(d) / idx / "config.json"
        if cfg_file.exists():
            try:
                import json
                cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
                adb_path = Path(d).parent / "adb.exe"
                if adb_path.exists():
                    ac["adb_path"] = str(adb_path)
            except Exception:
                pass
            break
