from __future__ import annotations
from pathlib import Path
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QGroupBox, QSpinBox, QCheckBox, QComboBox, QLineEdit,
    QDialogButtonBox, QWidget)
from infrastructure.task_constants import TASK_NAMES, find_adb, find_mumu_cli, detect_emu_instances
from ui.task_config import open_task_config as _open_task_config

PRESET_MAP = {"MuMu 12":"MuMuEmulator12","MuMu":"MuMu","MuMu 6":"MuMu",
              "雷电 9":"LDPlayer","雷电":"LDPlayer","蓝叠":"BlueStacks",
              "夜神":"Nox","逍遥":"XYAZ"}
DEFAULT_TASKS = {"StartUp", "Award"}
DAILY_TASKS   = {"StartUp", "Award", "Fight", "Recruit", "Infrast", "Mall"}
FULL_TASKS    = {"StartUp", "Award", "Fight", "Recruit", "Infrast", "Mall"}


def open_account_detail(mw: Any, row: int) -> None:
    if row < 0 or row >= len(mw.accounts):
        return
    ac = mw.accounts[row]
    d = QDialog(mw)
    d.setWindowTitle(f"账号详情 — {ac.get('name', '')}")
    d.setMinimumSize(480, 400)
    vl = QVBoxLayout(d)
    vl.setSpacing(6)

    # ── 基本信息 ──
    g1 = QGroupBox("基本信息")
    gl1 = QVBoxLayout(g1)
    gl1.setSpacing(4)

    row1 = QHBoxLayout()
    name_edit = QLineEdit(ac.get("name", ""))
    name_edit.setPlaceholderText("账号名")
    row1.addWidget(QLabel("名称:"))
    row1.addWidget(name_edit, 1)
    client_cb = QComboBox()
    for k, v in {"Official":"官服","Bilibili":"B服","YoStarEN":"国际服",
                  "YoStarJP":"日服","YoStarKR":"韩服","txwy":"繁中"}.items():
        client_cb.addItem(v, k)
    ci = client_cb.findData(ac.get("game_client","Official"))
    if ci >= 0: client_cb.setCurrentIndex(ci)
    row1.addWidget(QLabel("客户端:"))
    row1.addWidget(client_cb)
    gl1.addLayout(row1)

    # Emulator
    emu_row = QHBoxLayout()
    emu_combo = QComboBox()
    emu_combo.setMinimumWidth(180)
    instances = detect_emu_instances()
    cur_idx = ac.get("emu_instance_index", "")
    found = False
    for inst in instances:
        label = f"{inst.get('name','实例')} #{inst['index']}"
        emu_combo.addItem(label, inst["index"])
        if inst["index"] == cur_idx:
            emu_combo.setCurrentIndex(emu_combo.count()-1)
            found = True
    if not found and cur_idx:
        emu_combo.addItem(f"实例 #{cur_idx}", cur_idx)
        emu_combo.setCurrentIndex(emu_combo.count()-1)
    emu_combo.currentIndexChanged.connect(lambda: _on_emu_changed(emu_combo, ac))
    emu_row.addWidget(emu_combo)
    launch_cb = QCheckBox("自动启动")
    launch_cb.setChecked(ac.get("emu_launch", True))
    emu_row.addWidget(launch_cb)
    emu_row.addWidget(QLabel("等待:"))
    wait_sp = QSpinBox()
    wait_sp.setRange(0, 300); wait_sp.setValue(ac.get("emu_wait", 30)); wait_sp.setSuffix(" 秒")
    emu_row.addWidget(wait_sp)
    emu_row.addStretch()
    gl1.addLayout(emu_row)

    # ADB status (read-only)
    addr = ac.get("adb_address", "")
    apath = ac.get("adb_path", "")
    adb_ok = bool(addr and apath)
    adb_label = QLabel(f"ADB: {'✓' if adb_ok else '✗'} {addr or '未设置'}  |  {apath or '未设置'}")
    adb_label.setStyleSheet(f"color:{'#498205' if adb_ok else '#c04040'};font-size:8pt")
    gl1.addWidget(adb_label)
    vl.addWidget(g1)

    # ── 任务 ──
    g2 = QGroupBox("任务")
    gl2 = QVBoxLayout(g2)
    gl2.setSpacing(2)
    btn_row = QHBoxLayout()
    for label, ts in [("基础",DEFAULT_TASKS),("日常",DAILY_TASKS),("全量",FULL_TASKS)]:
        btn = QPushButton(label)
        btn.setFixedHeight(22)
        btn.clicked.connect(lambda _, s=ts: _set_tasks(s))
        btn_row.addWidget(btn)
    btn_row.addStretch()
    gl2.addLayout(btn_row)
    task_row = QHBoxLayout()
    task_cbs: dict[str, QCheckBox] = {}
    progs = [w for w in mw.warehouse if w.get("account_ref") == ac.get("id","")]
    pipeline = progs[0].get("task_pipeline","") if progs else ""
    enabled_tasks = set(t.strip().lower() for t in pipeline.split(",") if t.strip())
    for key, name in TASK_NAMES.items():
        cb = QCheckBox(name)
        cb.setChecked(key in enabled_tasks)
        task_cbs[key] = cb
        task_row.addWidget(cb)
    task_row.addStretch()
    gl2.addLayout(task_row)
    # Task config button
    cfg_btn = QPushButton("任务配置")
    cfg_btn.setFixedHeight(22)
    cfg_btn.clicked.connect(lambda: _open_task_config(mw, ac))
    gl2.addWidget(cfg_btn)
    vl.addWidget(g2)

    # ── 高级设置 (collapsed) ──
    g3 = QGroupBox("高级设置")
    g3.setCheckable(True)
    g3.setChecked(False)
    g3.setStyleSheet("QGroupBox::indicator{width:14px;height:14px}")
    adv_widgets: list[QWidget] = []
    gl3 = QVBoxLayout(g3)
    gl3.setSpacing(4)

    opt_row = QHBoxLayout()
    min_cb = QCheckBox("启动后最小化"); min_cb.setChecked(ac.get("start_minimized",True))
    opt_row.addWidget(min_cb); adv_widgets.append(min_cb)
    dir_cb = QCheckBox("直接运行"); dir_cb.setChecked(ac.get("start_directly",True))
    opt_row.addWidget(dir_cb); adv_widgets.append(dir_cb)
    sync_cb = QCheckBox("启动时同步配置")
    sync_cb.setChecked(progs[0].get("sync_tasks",False) if progs else False)
    opt_row.addWidget(sync_cb); adv_widgets.append(sync_cb)
    # Stamina threshold
    stamina_lbl = QLabel("体力阈值:")
    stamina_lbl.setStyleSheet("color:#999;font-size:8pt")
    opt_row.addWidget(stamina_lbl); adv_widgets.append(stamina_lbl)
    stamina_sp = QSpinBox()
    stamina_sp.setRange(1, 100); stamina_sp.setValue(ac.get("stamina_threshold_pct", 80))
    stamina_sp.setSuffix(" %"); stamina_sp.setFixedWidth(70)
    opt_row.addWidget(stamina_sp); adv_widgets.append(stamina_sp)
    opt_row.addStretch()
    gl3.addLayout(opt_row)

    post_row = QHBoxLayout()
    cur_post = ac.get("post_action","ExitEmulator,ExitSelf")
    post_set = set(cur_post.split(",")) if cur_post else set()
    post_cbs = {}
    for k, v in [("ExitArknights","退出游戏"),("ExitEmulator","关模拟器"),
                  ("ExitSelf","退出MAA"),("BackToAndroidHome","返回主屏")]:
        cb = QCheckBox(v); cb.setChecked(k in post_set); post_cbs[k] = cb; post_row.addWidget(cb); adv_widgets.append(cb)
    post_row.addStretch()
    pl = QLabel("完成后:"); adv_widgets.append(pl); gl3.addWidget(pl)
    gl3.addLayout(post_row)
    g3.toggled.connect(lambda c: [w.setVisible(c) for w in adv_widgets])
    vl.addWidget(g3)

    vl.addStretch()

    # ── Buttons ──
    def _set_tasks(task_set: set[str]):
        for k, cb in task_cbs.items():
            cb.setChecked(k in task_set)

    def _save():
        ac["name"] = name_edit.text().strip()
        ac["game_client"] = client_cb.currentData()
        ac["emu_instance_index"] = emu_combo.currentData() or ""
        ac["emu_launch"] = launch_cb.isChecked()
        ac["emu_wait"] = wait_sp.value()
        ac["start_minimized"] = min_cb.isChecked()
        ac["start_directly"] = dir_cb.isChecked()
        ac["stamina_threshold_pct"] = stamina_sp.value()
        selected = [k for k,cb in post_cbs.items() if cb.isChecked()]
        ac["post_action"] = ",".join(selected) if selected else ""
        if progs:
            enabled = [k for k,cb in task_cbs.items() if cb.isChecked()]
            progs[0]["task_pipeline"] = ",".join(enabled)
            progs[0]["sync_tasks"] = sync_cb.isChecked()
        mw._save()
        from ui.smart_panel import _rebuild_list
        _rebuild_list(mw)
        d.accept()

    del_btn = QPushButton("🗑 删除")
    del_btn.setStyleSheet("QPushButton{color:#e88;border:1px solid #5a2020;border-radius:5px;padding:4px 12px;font-size:9pt}QPushButton:hover{background:#5a2020;color:#fff}")
    def _delete():
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(d,"确认",f"删除「{ac.get('name','')}」?") == QMessageBox.Yes:
            mw.accounts.pop(row)
            mw._save()
            d.accept()
    del_btn.clicked.connect(_delete)

    bb = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
    bb.accepted.connect(_save); bb.rejected.connect(d.reject)
    btn_row2 = QHBoxLayout()
    btn_row2.addWidget(del_btn); btn_row2.addStretch(); btn_row2.addWidget(bb)
    vl.addLayout(btn_row2)

    d.exec()


def _on_emu_changed(combo: QComboBox, ac: dict) -> None:
    idx = combo.currentData()
    if not idx: return
    ac["emu_instance_index"] = idx
    ac["connection_preset"] = "MuMuEmulator12"
    ac["touch_mode"] = "MiniTouch"
    ac["adb_address"] = f"127.0.0.1:{5555 + int(idx) * 2}"
    adb_exe = find_adb()
    if not adb_exe:
        cli = find_mumu_cli()
        if cli:
            cand = Path(cli).parent / "adb.exe"
            if cand.exists(): adb_exe = str(cand)
    if adb_exe: ac["adb_path"] = adb_exe
