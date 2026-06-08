"""Account dashboard builder — extracted from main_window.py."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QSpinBox, QCheckBox, QFrame, QMenu, QInputDialog,
)

from task_constants import TASK_NAMES, EMU_PRESETS, CLIENT_TYPES, find_mumu_cli
from utils import _find_maa_cli
from dialogs import TaskSettingsDialog
from ui.config_cards import refresh_config_cards


def clear_dashboard(mw: Any) -> None:
    mw.ade.hide()
    for i in reversed(range(mw.adl.count())):
        w = mw.adl.itemAt(i).widget()
        if w and w is not mw.ade:
            w.setParent(None)
            w.deleteLater()


def cleanup_emu_threads(mw: Any) -> None:
    for attr in ("_t", "_scan_thread", "_refresh_t", "_test_t", "_ss_t", "_stopemu_t"):
        t = getattr(mw.emu, attr, None)
        if t and t.isRunning():
            try:
                t.result.disconnect()
            except Exception:
                pass
            t.terminate()
            t.wait(200)


_dash_building = False

def build_account_dashboard(mw: Any, row: int) -> None:
    global _dash_building
    if _dash_building:
        return
    _dash_building = True
    try:
        if row < 0 or row >= len(mw.accounts):
            mw.ade.show()
            if hasattr(mw, "_dash_refs"):
                for r in mw._dash_refs.values():
                    if isinstance(r, QWidget):
                        r.hide()
            return

        mw._sad_row = row
        progs = [w for w in mw.warehouse if w.get("account_ref") == mw.accounts[row]["id"]]

        cleanup_emu_threads(mw)
        clear_dashboard(mw)
        mw._dash_refs = {}
        _ensure_dashboard(mw, row, progs)
    finally:
        _dash_building = False


def _ensure_dashboard(mw: Any, row: int, progs: list[dict]) -> None:
    """Create all dashboard widgets once. Stores refs in mw._dash_refs."""
    _build_header(mw, row)
    _build_maa_card(mw, row, progs)
    _build_emu_adb_card(mw, row)
    _build_pipeline_card(mw, row, progs)
    _build_launch_card(mw, row)
    _build_action_buttons(mw, row, progs)
    mw.adl.addStretch()


def _update_dashboard(mw: Any, row: int, progs: list[dict]) -> None:
    """Refresh existing dashboard widgets with new account data."""
    refs = mw._dash_refs
    a = mw.accounts[row]

    # Header
    if "header_name" in refs:
        refs["header_name"].blockSignals(True)
        refs["header_name"].setText(a.get("name", ""))
        refs["header_name"].blockSignals(False)
    if "header_client" in refs:
        idx = refs["header_client"].findData(a.get("game_client", ""))
        if idx >= 0:
            refs["header_client"].blockSignals(True)
            refs["header_client"].setCurrentIndex(idx)
            refs["header_client"].blockSignals(False)
    if "adb_switch" in refs:
        refs["adb_switch"].blockSignals(True)
        refs["adb_switch"].setText(a.get("account_switch", ""))
        refs["adb_switch"].blockSignals(False)

    # MAA Status
    _upd_maa_status(mw, a, progs, refs)
    # Emulator
    _upd_emu(mw, a, refs)
    # ADB
    _upd_adb(mw, a, refs)
    # Pipeline
    _upd_pipeline(mw, a, progs, refs)
    # Launch options
    _upd_launch(mw, a, refs)
    # Action buttons
    _upd_actions(mw, row, progs, refs)


# ── Update helpers ──

def _upd_maa_status(mw, a, progs, refs):
    if "maa_version_lbl" not in refs:
        return
    v = progs[0].get("maa_version", "") if progs else ""
    t = int(__import__("time").time() - mw._proc_start_times.get(progs[0]["id"], 0)) if progs and progs[0]["id"] in mw._proc_status else 0
    if t:
        refs["maa_version_lbl"].setText(f"🟢 运行中 ({t // 60}m{t % 60}s)  {v}" if v else f"🟢 运行中 ({t // 60}m{t % 60}s)")
    else:
        refs["maa_version_lbl"].setText(f"已安装 {v}" if v else "已安装")
    if "maa_channel" in refs and progs:
        cur = progs[0].get("update_channel", "Stable")
        refs["maa_channel"].blockSignals(True)
        refs["maa_channel"].setCurrentText(cur)
        refs["maa_channel"].blockSignals(False)
    if "maa_auto_upd" in refs and progs:
        refs["maa_auto_upd"].blockSignals(True)
        refs["maa_auto_upd"].setChecked(progs[0].get("auto_update", mw.config.get("auto_update_maa", True)))
        refs["maa_auto_upd"].blockSignals(False)


def _upd_emu(mw, a, refs):
    if "emu_path" in refs:
        refs["emu_path"].blockSignals(True)
        refs["emu_path"].setText(a.get("emu_path", ""))
        refs["emu_path"].blockSignals(False)
    if "emu_launch_cb" in refs:
        refs["emu_launch_cb"].blockSignals(True)
        refs["emu_launch_cb"].setChecked(a.get("emu_launch", False))
        refs["emu_launch_cb"].blockSignals(False)
    if "emu_wait_sp" in refs:
        refs["emu_wait_sp"].blockSignals(True)
        refs["emu_wait_sp"].setValue(a.get("emu_wait", 30))
        refs["emu_wait_sp"].blockSignals(False)
    # Refresh instance list
    if "emu_inst_sel" in refs:
        mw.emu.refresh_instance_list(refs["emu_inst_sel"], a.get("emu_instance_index", ""), a.get("emu_instance_name", ""))


def _upd_adb(mw, a, refs):
    if "adb_preset" in refs:
        idx = refs["adb_preset"].findData(a.get("connection_preset", ""))
        if idx >= 0:
            refs["adb_preset"].blockSignals(True)
            refs["adb_preset"].setCurrentIndex(idx)
            refs["adb_preset"].blockSignals(False)
    if "adb_path" in refs:
        refs["adb_path"].blockSignals(True)
        refs["adb_path"].setText(a.get("adb_path", ""))
        refs["adb_path"].blockSignals(False)
    if "adb_addr" in refs:
        refs["adb_addr"].blockSignals(True)
        refs["adb_addr"].setText(a.get("adb_address", ""))
        refs["adb_addr"].blockSignals(False)


def _upd_pipeline(mw, a, progs, refs):
    pt = progs[0].get("task_pipeline", "startup,fight,recruit,infrast,mall,award") if progs else ""
    all_tasks = set(t.strip().lower() for t in pt.split(",") if t.strip())
    if "pipeline_cbs" in refs:
        for tk, cb in refs["pipeline_cbs"].items():
            cb.blockSignals(True)
            cb.setChecked(tk.lower() in all_tasks)
            cb.blockSignals(False)
    if "pipeline_mode" in refs and progs:
        refs["pipeline_mode"].blockSignals(True)
        refs["pipeline_mode"].setCurrentText(progs[0].get("launch_mode", "gui"))
        refs["pipeline_mode"].blockSignals(False)
    if "pipeline_sync" in refs:
        refs["pipeline_sync"].blockSignals(True)
        refs["pipeline_sync"].setChecked(a.get("sync_tasks", False))
        refs["pipeline_sync"].blockSignals(False)


def _upd_launch(mw, a, refs):
    for key, attr in [("launch_min", "start_minimized"), ("launch_dir", "start_directly"),
                      ("launch_emu_fail", "adb_fail_launch_emu")]:
        if key in refs:
            refs[key].blockSignals(True)
            refs[key].setChecked(a.get(attr, False))
            refs[key].blockSignals(False)
    if "launch_adb_retry" in refs:
        refs["launch_adb_retry"].blockSignals(True)
        refs["launch_adb_retry"].setValue(a.get("adb_retry", 0))
        refs["launch_adb_retry"].blockSignals(False)


def _upd_actions(mw, row, progs, refs):
    if "action_launch_btn" in refs:
        try: refs["action_launch_btn"].clicked.disconnect()
        except TypeError: pass
        refs["action_launch_btn"].clicked.connect(lambda: mw._la(row))
    if "action_launch_all_btn" in refs:
        try: refs["action_launch_all_btn"].clicked.disconnect()
        except TypeError: pass
        refs["action_launch_all_btn"].clicked.connect(lambda: mw._la_all())
    if "action_update_btn" in refs and progs:
        try: refs["action_update_btn"].clicked.disconnect()
        except TypeError: pass
        refs["action_update_btn"].clicked.connect(lambda: mw.maint.cu_single(progs[0]))


# ── Header card (name + client + account switch) ──

def _build_header(mw: Any, row: int) -> None:
    a = mw.accounts[row]

    hc = QFrame()
    hc.setObjectName("card")
    hcl = QVBoxLayout(hc)
    hcl.setSpacing(6)
    hcl.addWidget(QLabel("👤 账号", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))

    r1 = QHBoxLayout()
    ne = QLineEdit(a.get("name", ""))
    ne.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
    ne.setPlaceholderText("账号名")
    ne.textChanged.connect(lambda t: (a.__setitem__("name", t), mw._save(), mw._ra()))
    r1.addWidget(ne, 1)
    cc = QComboBox()
    for k, v in CLIENT_TYPES.items():
        cc.addItem(v, k)
    idx = cc.findData(a.get("game_client", ""))
    cc.setCurrentIndex(max(0, idx))
    cc.currentIndexChanged.connect(lambda: (a.__setitem__("game_client", cc.currentData()), mw._save()))
    r1.addWidget(cc)
    hcl.addLayout(r1)

    r2 = QHBoxLayout()
    r2.setSpacing(8)
    lb = QLabel("切换账号:")
    lb.setStyleSheet("color:#888;font-size:9pt")
    r2.addWidget(lb)
    sw = QLineEdit(a.get("account_switch", ""))
    sw.setPlaceholderText("123***4567 或 mail@gmail.com，留空禁用")
    sw.textChanged.connect(lambda t: a.update({"account_switch": t}) or mw._save())
    r2.addWidget(sw, 1)
    hcl.addLayout(r2)

    mw.adl.insertWidget(0, hc)
    mw._dash_refs["header_name"] = ne
    mw._dash_refs["header_client"] = cc
    mw._dash_refs["adb_switch"] = sw


# ── MAA Status card ──

def _build_maa_card(mw: Any, row: int, progs: list[dict]) -> None:
    a = mw.accounts[row]
    mc = QFrame()
    mc.setObjectName("card")
    mcl = QVBoxLayout(mc)
    mcl.setSpacing(5)
    mh = QHBoxLayout()
    mh.addWidget(QLabel("📦 MAA 状态", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    mh.addStretch()

    if progs:
        v = progs[0].get("maa_version", "")
        vl = QLabel(f"已安装 {v}" if v else "已绑定")
        if progs[0].get("id") in mw._proc_status:
            t = int(__import__("time").time() - mw._proc_start_times.get(progs[0]["id"], 0))
            vl.setText(f"🟢 运行中 ({t // 60}m{t % 60}s)  {v}" if v else f"🟢 运行中 ({t // 60}m{t % 60}s)")
            vl.setStyleSheet("color:#326cf3;font-weight:bold")
        else:
            vl.setStyleSheet("color:#888;font-weight:bold")
        mh.addWidget(vl)
        mcl.addLayout(mh)

        for p in progs:
            mcl.addWidget(QLabel(f"  ▶ {p.get('path', '?')}"))

        vr = QHBoxLayout()
        vr.addWidget(QLabel("通道:"))
        ch = QComboBox()
        ch.addItems(["Stable", "Beta", "Alpha"])
        cur_ch = progs[0].get("update_channel", "Stable")
        ch.setCurrentText(cur_ch)
        ch.currentTextChanged.connect(lambda t: (progs[0].__setitem__("update_channel", t), mw._save()))
        vr.addWidget(ch)
        vr.addStretch()
        sw_ver = QPushButton("🔄 切换版本")
        sw_ver.clicked.connect(lambda: mw.logs.switch_maa_version(progs[0], ch.currentText()))
        vr.addWidget(sw_ver)
        mcl.addLayout(vr)

        btr = QHBoxLayout()
        launch_btn = QPushButton("▶ 启动 MAA")
        launch_btn.setToolTip("打开 MAA 主界面进行手动配置（不会自动运行任务）")
        launch_btn.clicked.connect(lambda: mw._launch_raw(progs[0]))
        btr.addWidget(launch_btn)
        stats_btn = QPushButton("📊 统计")
        stats_btn.clicked.connect(lambda: mw.logs.show_stats(progs[0]))
        btr.addWidget(stats_btn)
        log_btn = QPushButton("📋 日志")
        log_btn.clicked.connect(lambda: mw.logs.view_log(progs[0]))
        btr.addWidget(log_btn)
        btr.addStretch()
        mcl.addLayout(btr)

        # Resource auto-update indicator
        aur = QHBoxLayout()
        au_cb = QCheckBox("自动更新资源")
        au_cb.setChecked(progs[0].get("auto_update", mw.config.get("auto_update_maa", True)))
        au_cb.setToolTip("检测到新版本时自动下载更新")
        au_cb.toggled.connect(lambda v: (progs[0].__setitem__("auto_update", v), mw._save()))
        aur.addWidget(au_cb)
        aur.addStretch()
        au_lbl = QLabel("已启用" if au_cb.isChecked() else "已禁用")
        au_lbl.setStyleSheet("color:#888" if au_cb.isChecked() else "color:#666")
        au_cb.toggled.connect(lambda v: au_lbl.setText("已启用" if v else "已禁用") or au_lbl.setStyleSheet("color:#888" if v else "color:#666"))
        aur.addWidget(au_lbl)
        mcl.addLayout(aur)

        today = datetime.now().strftime("%Y-%m-%d")
        sd = a.get("stats", {}).get(today, {})
        if sd.get("launches"):
            mcl.addWidget(QLabel(f"  今日: 启动 {sd['launches']} 次"))
    else:
        vl = QLabel("未安装")
        vl.setStyleSheet("color:#888;font-weight:bold")
        mh.addWidget(vl)
        mcl.addLayout(mh)
        dl_row = QHBoxLayout()
        dl_btn = QPushButton("⬇ 下载 MAA")
        dl_btn.setObjectName("addProgBtn")
        dl_btn.clicked.connect(lambda: mw.maint.dl_maa(row))
        dl_row.addWidget(dl_btn)
        dl_row.addWidget(QPushButton("📂 绑定", clicked=lambda: mw.maint.pk_maa(row)))
        dl_row.addStretch()
        mcl.addLayout(dl_row)

    mw.adl.insertWidget(2, mc)
    if progs:
        mw._dash_refs["maa_version_lbl"] = vl
        mw._dash_refs["maa_channel"] = ch
        mw._dash_refs["maa_auto_upd"] = au_cb


# ── Emulator + ADB merged card ──

def _build_emu_adb_card(mw: Any, row: int) -> None:
    a = mw.accounts[row]

    def _lbl(t):
        l = QLabel(t)
        l.setFixedWidth(55)
        l.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return l

    ec = QFrame()
    ec.setObjectName("card")
    ecl = QVBoxLayout(ec)
    ecl.setSpacing(5)
    ecl.addWidget(QLabel("🖥 模拟器 & ADB", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))

    emu_path_edit = QLineEdit(a.get("emu_path", ""))
    adb_p = QLineEdit(a.get("adb_path", ""))
    ae2 = QLineEdit(a.get("adb_address", ""))

    # Row 1: instance selector (auto-detects everything)
    r1 = QHBoxLayout()
    r1.addWidget(_lbl("实例:"))
    ed_sel = QComboBox()
    ed_sel.setMinimumWidth(180)
    combo_saved_idx = a.get("emu_instance_index", "")
    combo_saved_name = a.get("emu_instance_name", "")
    mw.emu.refresh_instance_list(ed_sel, combo_saved_idx, combo_saved_name)

    def _on_ins(i):
        if not ed_sel.currentData(): return
        ins = ed_sel.currentData()
        cli = find_mumu_cli()
        if cli:
            emu_path_edit.setText(str(cli))
            a.__setitem__("emu_path", str(cli))
            a.__setitem__("emu_add_cmd", "")
            from task_constants import find_adb
            adb_exe = find_adb()
            if adb_exe:
                adb_p.setText(adb_exe)
                a.__setitem__("adb_path", adb_exe)
        a.__setitem__("emu_instance_index", ins["index"])
        a.__setitem__("emu_instance_name", ins.get("name", ""))
        if not a.get("touch_mode"):
            a.__setitem__("touch_mode", "MiniTouch")
        if ins.get("adb_port"):
            ae2.setText(f"127.0.0.1:{ins['adb_port']}")
            a.__setitem__("adb_address", ae2.text())
        else:
            addr = mw.emu.auto_detect_adb(ins)
            if addr:
                ae2.setText(addr)
                a.__setitem__("adb_address", addr)
        mw._save()

    ed_sel.currentIndexChanged.connect(_on_ins)
    r1.addWidget(ed_sel, 1)
    r1.addWidget(QPushButton("🔄", clicked=lambda: mw.emu.refresh_instance_list(ed_sel), toolTip="刷新"))
    ecl.addLayout(r1)

    # Row 2: ADB address + ADB path
    r2 = QHBoxLayout()
    r2.addWidget(_lbl("地址:"))
    ae2.setPlaceholderText("127.0.0.1:7555")
    ae2.textChanged.connect(lambda t: a.update({"adb_address": t}) or mw._save())
    r2.addWidget(ae2, 1)
    emu_combo = QComboBox()
    emu_combo.addItem("在线设备", "")
    emu_combo.setMinimumWidth(140)
    emu_combo.currentIndexChanged.connect(lambda i: ae2.setText(emu_combo.currentData()) if emu_combo.currentData() else None)
    r2.addWidget(emu_combo)
    ecl.addLayout(r2)

    # Row 3: ADB path + preset
    r3 = QHBoxLayout()
    r3.addWidget(_lbl("ADB:"))
    adb_p.setPlaceholderText("留空使用默认")
    adb_p.textChanged.connect(lambda t: a.update({"adb_path": t}) or mw._save())
    r3.addWidget(adb_p, 1)
    r3.addWidget(QPushButton("📂", clicked=lambda: mw.emu.browse_adb(adb_p, a)))
    r3.addWidget(_lbl("预设:"))
    emu_sel = QComboBox()
    emu_sel.addItem("— 选择 —", "")
    for ep in EMU_PRESETS:
        emu_sel.addItem(ep["name"], ep["type"])
    idx = emu_sel.findData(a.get("connection_preset", ""))
    if idx >= 0:
        emu_sel.setCurrentIndex(idx)
    def _on_emu_preset(i):
        if 0 < i <= len(EMU_PRESETS):
            a["connection_preset"] = EMU_PRESETS[i - 1]["type"]
            mw._save()
    emu_sel.currentIndexChanged.connect(_on_emu_preset)
    r3.addWidget(emu_sel)
    ecl.addLayout(r3)

    # Row 4: auto-launch + touch mode + actions
    r4 = QHBoxLayout()
    r4.addWidget(_lbl(""))
    cb_oe = QCheckBox("自启模拟器")
    cb_oe.setChecked(a.get("emu_launch", False))
    cb_oe.toggled.connect(lambda v: a.update({"emu_launch": v}) or mw._save())
    r4.addWidget(cb_oe)
    r4.addWidget(QLabel("等待"))
    ws_sp = QSpinBox()
    ws_sp.setRange(0, 300)
    ws_sp.setValue(a.get("emu_wait", 30))
    ws_sp.setSuffix(" 秒")
    ws_sp.valueChanged.connect(lambda v: a.update({"emu_wait": v}) or mw._save())
    r4.addWidget(ws_sp)
    r4.addStretch()
    r4.addWidget(QPushButton("扫端口", clicked=lambda: mw.emu.scan_port(a, emu_path_edit, ae2)))
    r4.addWidget(QPushButton("测试", clicked=lambda: mw.emu.test_adb(a)))
    r4.addWidget(QPushButton("截图", clicked=lambda: mw.emu.screenshot(a)))
    r4.addWidget(QPushButton("关闭", clicked=lambda: mw.emu.stop_emu(a), objectName="stopBtn"))
    ecl.addLayout(r4)

    mw._ast = QLabel("")
    ecl.addWidget(mw._ast)

    mw.adl.insertWidget(3, ec)
    mw._dash_refs["emu_path"] = emu_path_edit
    mw._dash_refs["emu_inst_sel"] = ed_sel
    mw._dash_refs["emu_launch_cb"] = cb_oe
    mw._dash_refs["emu_wait_sp"] = ws_sp
    mw._dash_refs["adb_preset"] = emu_sel
    mw._dash_refs["adb_path"] = adb_p
    mw._dash_refs["adb_addr"] = ae2


# ── Pipeline card ──

def _batch_apply(mw: Any, src_acc: dict, src_progs: list[dict], src_row: int) -> None:
    """Open dialog to apply current account's config to selected accounts."""
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QCheckBox, QPushButton, QLabel
    from PySide6.QtGui import QFont

    d = QDialog(mw)
    d.setWindowTitle("批量应用配置")
    d.setMinimumSize(350, 280)
    l = QVBoxLayout(d)

    l.addWidget(QLabel("选择要应用配置的目标账号：", font=QFont("Microsoft YaHei UI", 11)))
    l.addWidget(QLabel(f"  来源：{src_acc.get('name', '?')}"))
    l.addWidget(QLabel(f"  流水线：{src_progs[0].get('task_pipeline', '无') if src_progs else '无'}"))

    checks = {}
    for i, a in enumerate(mw.accounts):
        if a["id"] == src_acc["id"]:
            continue
        cb = QCheckBox(a.get("name", f"账号{i+1}"))
        cb.setChecked(True)
        checks[a["id"]] = cb
        l.addWidget(cb)

    if not checks:
        l.addWidget(QLabel("（没有其他账号可应用）"))

    def _apply():
        selected = [aid for aid, cb in checks.items() if cb.isChecked()]
        if not selected:
            d.accept()
            return
        src_pipe = src_progs[0].get("task_pipeline", "") if src_progs else ""
        src_settings = src_acc.get("task_settings", {})
        src_sync = src_acc.get("sync_tasks", False)
        for a in mw.accounts:
            if a["id"] not in selected:
                continue
            a["task_settings"] = deepcopy(src_settings)
            a["sync_tasks"] = src_sync
            for w in mw.warehouse:
                if w.get("account_ref") == a["id"]:
                    w["task_pipeline"] = src_pipe
        mw._save()
        mw._log(f"批量应用配置到 {len(selected)} 个账号")
        d.accept()

    btn = QPushButton("应用")
    btn.clicked.connect(_apply)
    l.addWidget(btn)
    d.exec()


def _build_pipeline_card(mw: Any, row: int, progs: list[dict]) -> None:
    a = mw.accounts[row]

    smart_enabled = mw.config.get("smart_global", {}).get("enabled", False)

    tc = QFrame()
    tc.setObjectName("card")
    tcl = QVBoxLayout(tc)
    tcl.setSpacing(5)

    if smart_enabled:
        tcl.addWidget(QLabel("🧠 智能调度已启用", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
        tcl.addWidget(QLabel("任务由程序按时间/体力/材料自动决策", styleSheet="color:#888;font-size:9pt"))
        stage = a.get("smart_stage", "") or "（MAA自行决定）"
        tcl.addWidget(QLabel(f"默认关卡: {stage}", styleSheet="color:#888;font-size:9pt"))
        mw.adl.insertWidget(4, tc)
        return

    tcl.addWidget(QLabel("⚙ 流水线", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))

    pt = progs[0].get("task_pipeline", "startup,fight,recruit,infrast,mall,award") if progs else "startup,fight,recruit,infrast,mall,award"
    all_tasks = [t.strip() for t in pt.split(",") if t.strip()]
    tl = {k.lower(): v for k, v in TASK_NAMES.items()}
    tn = {v: k for k, v in TASK_NAMES.items()}

    task_cbs = {}
    tw = QWidget()
    twl = QHBoxLayout(tw)
    twl.setContentsMargins(0, 0, 0, 0)
    twl.setSpacing(4)
    for tl2 in sorted(set(t.lower() for t in all_tasks) & set(tl.keys()), key=lambda x: list(tl.keys()).index(x)):
        cn = tl[tl2]
        tk = tn.get(cn, tl2)
        cb = QCheckBox(cn)
        cb.setChecked(tl2 in [t.lower() for t in all_tasks])
        task_cbs[tk] = cb
        twl.addWidget(cb)
    twl.addStretch()
    tcl.addWidget(tw)

    mc2_ref = []  # mutable container for mode combo

    def _up():
        ep = []
        for tk in task_cbs:
            if task_cbs[tk].isChecked():
                ep.append(tk)
        for t in all_tasks:
            if t.lower() not in tl:
                ep.append(t)
        new_pipe = ",".join(ep)
        for p in progs:
            p["task_pipeline"] = new_pipe
            if mc2_ref:
                p["launch_mode"] = mc2_ref[0].currentText()
        mw._save()

    for cb in task_cbs.values():
        cb.toggled.connect(lambda _: _up())

    mr2 = QHBoxLayout()
    cfg_btn = QPushButton("⚙ 参数")
    ts = a.get("task_settings", {})

    def _cfg_clicked():
        TaskSettingsDialog(mw, ts, progs[0].get("task_pipeline", "")).exec()
        a.__setitem__("task_settings", ts)
        mw._save()

    cfg_btn.clicked.connect(lambda: _cfg_clicked())
    mr2.addWidget(cfg_btn)

    tmpl_btn = QPushButton("💾 模板")

    def _show_tmpl_menu():
        tm = QMenu(tmpl_btn)
        for n in a.get("task_templates", {}):
            act = tm.addAction(f"📂 {n}")
            act.triggered.connect(lambda _, name=n: _ld_tmpl(name))
            act2 = tm.addAction(f"✕ 删{n}")
            act2.triggered.connect(lambda _, name=n: (a["task_templates"].pop(name, None), a.get("pipe_templates", {}).pop(name, None), mw._save(), build_account_dashboard(mw, row)))
        if a.get("task_templates", {}):
            tm.addSeparator()
        act = tm.addAction("💾 保存当前...")
        act.triggered.connect(lambda: _sv_tmpl())
        tm.addSeparator()
        act = tm.addAction("📋 批量应用当前配置到...")
        act.triggered.connect(lambda: _batch_apply(mw, a, progs, row))
        tm.exec(tmpl_btn.mapToGlobal(tmpl_btn.rect().bottomLeft()))

    tmpl_btn.clicked.connect(lambda: _show_tmpl_menu())

    def _sv_tmpl():
        name, ok = QInputDialog.getText(mw, "保存模板", "名称:", text="日常模式")
        if ok and name:
            a.setdefault("task_templates", {})[name] = dict(ts)
            a.setdefault("pipe_templates", {})[name] = progs[0].get("task_pipeline", "")
            mw._save()
            mw._log(f"模板已保存: {name}")

    def _ld_tmpl(name):
        if name in a.get("task_templates", {}):
            a["task_settings"] = dict(a["task_templates"][name])
            for p in progs:
                p["task_pipeline"] = a.get("pipe_templates", {}).get(name, "")
            mw._save()
            build_account_dashboard(mw, row)

    mr2.addWidget(tmpl_btn)

    sc = QCheckBox("启动时同步")
    sc.setChecked(a.get("sync_tasks", False))
    sc.toggled.connect(lambda v: (a.__setitem__("sync_tasks", v), mw._save()))
    mr2.addWidget(sc)
    mr2.addStretch()

    mr2.addWidget(QLabel("模式:"))
    mc2 = QComboBox()
    mc2.addItems(["gui", "cli"])
    mc2.setCurrentText(progs[0].get("launch_mode", "gui") if progs else "gui")
    mc2.currentTextChanged.connect(lambda t: _up())
    mr2.addWidget(mc2)
    mr2.addStretch()
    mc2_ref.append(mc2)

    if mc2.currentText() == "cli":
        cl = _find_maa_cli()
        l2 = QLabel("maa-cli 就绪" if cl else "maa-cli 未安装")
        l2.setStyleSheet("color:#888" if cl else "color:#666")
        mr2.addWidget(l2)
    mr2.addStretch()
    tcl.addLayout(mr2)

    mw.adl.insertWidget(5, tc)
    mw._dash_refs["pipeline_cbs"] = task_cbs
    mw._dash_refs["pipeline_mode"] = mc2
    mw._dash_refs["pipeline_sync"] = sc


# ── Launch & post-actions card ──

def _build_launch_card(mw: Any, row: int) -> None:
    a = mw.accounts[row]

    def _lbl(t):
        l = QLabel(t)
        l.setFixedWidth(55)
        l.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return l

    oc = QFrame()
    oc.setObjectName("card")
    ocl = QVBoxLayout(oc)
    ocl.setSpacing(5)
    ocl.addWidget(QLabel("🔄 启动与完成后", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))

    or1 = QHBoxLayout()
    or1.addWidget(_lbl("启动:"))

    cb_sm = QCheckBox("启动后最小化")
    cb_sm.setChecked(a.get("start_minimized", False))
    cb_sm.toggled.connect(lambda v: (a.__setitem__("start_minimized", v), mw._save()))
    or1.addWidget(cb_sm)

    cb_sd = QCheckBox("直接运行")
    cb_sd.setChecked(a.get("start_directly", False))
    cb_sd.toggled.connect(lambda v: (a.__setitem__("start_directly", v), mw._save()))
    or1.addWidget(cb_sd)

    cb_ad = QCheckBox("ADB 失败启模拟器")
    cb_ad.setChecked(a.get("adb_fail_launch_emu", False))
    cb_ad.toggled.connect(lambda v: (a.__setitem__("adb_fail_launch_emu", v), mw._save()))
    or1.addWidget(cb_ad)

    or1.addWidget(QLabel("ADB 重试"))
    ar = QSpinBox()
    ar.setRange(0, 10)
    ar.setValue(a.get("adb_retry", 0))
    ar.setSuffix(" 次")
    ar.setMaximumWidth(60)
    ar.valueChanged.connect(lambda v: a.update({"adb_retry": v}) or mw._save())
    or1.addWidget(ar)
    or1.addStretch()
    ocl.addLayout(or1)

    or2 = QHBoxLayout()
    or2.addWidget(_lbl("完成后:"))
    post_actions = a.get("post_action", "")
    if post_actions and post_actions[0] == "[":
        try:
            post_actions = ",".join(__import__("json").loads(post_actions))
            a["post_action"] = post_actions
        except Exception:
            pass
    post_arr = post_actions.split(",") if post_actions else []
    post_cbs = {}

    for k, v in [("BackToAndroidHome", "返回主屏"), ("ExitArknights", "退出方舟"), ("ExitEmulator", "关模拟器"), ("ExitSelf", "退出MAA")]:
        cb = QCheckBox(v)
        cb.setChecked(k in post_arr)
        post_cbs[k] = cb
        or2.addWidget(cb)
    or2.addStretch()

    def _save_post():
        acts = [k for k, cb in post_cbs.items() if cb.isChecked()]
        a.__setitem__("post_action", ",".join(acts) if acts else "")
        mw._save()

    for cb in post_cbs.values():
        cb.toggled.connect(lambda _: _save_post())
    ocl.addLayout(or2)

    mw.adl.insertWidget(6, oc)
    mw._dash_refs["launch_min"] = cb_sm
    mw._dash_refs["launch_dir"] = cb_sd
    mw._dash_refs["launch_emu_fail"] = cb_ad
    mw._dash_refs["launch_adb_retry"] = ar


# ── Action buttons ──

def _build_action_buttons(mw: Any, row: int, progs: list[dict]) -> None:
    a = mw.accounts[row]

    # ── Config summary bar ──
    summary_parts = []
    if a.get("adb_address", ""):
        summary_parts.append(f"📱 {a['adb_address']}")
    if a.get("emu_instance_index", ""):
        summary_parts.append(f"🖥 MuMu#{a['emu_instance_index']}")
    if a.get("sanity_driven", False):
        summary_parts.append("💊 理智驱动")
    if a.get("emu_launch"):
        summary_parts.append("🖥 自启模拟器")
    if a.get("start_minimized", False):
        summary_parts.append("📐 最小化")
    if a.get("start_directly", False):
        summary_parts.append("⚡ 直接运行")
    pipe = progs[0].get("task_pipeline", "") if progs else ""
    if pipe:
        name_map = {"startup":"唤醒","fight":"刷关","recruit":"公招","infrast":"基建","mall":"信用","award":"奖励","roguelike":"肉鸽","reclamation":"生息","closedown":"关闭"}
        tasks = [name_map.get(t.strip().lower(), t.strip()) for t in pipe.split(",") if t.strip()][:5]
        summary_parts.append(f"⚙ {'·'.join(tasks)}")
    if summary_parts:
        summary_text = "  │  ".join(summary_parts)
        summary_lbl = QLabel(summary_text)
        summary_lbl.setStyleSheet("color:#888;padding:4px 0;font-size:10pt")
        summary_lbl.setWordWrap(True)
        mw.adl.addWidget(summary_lbl)

    if progs:
        bw = QWidget()
        bl = QHBoxLayout(bw)
        bl.setContentsMargins(0, 0, 0, 0)
        upd_btn = QPushButton("检查更新", clicked=lambda: mw.maint.cu_single(progs[0]))
        mw._dash_refs["action_update_btn"] = upd_btn
        bl.addWidget(upd_btn)
        bl.addStretch()
        mw.adl.addWidget(bw)
