"""Account dashboard builder 鈥?extracted from main_window.py."""
from __future__ import annotations
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
    for i in reversed(range(mw.adl.count())):
        w = mw.adl.itemAt(i).widget()
        if w and w is not mw.ade:
            mw.ade.hide()
            w.setParent(None)


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


def build_account_dashboard(mw: Any, row: int) -> None:
    if row < 0 or row >= len(mw.accounts):
        mw.ade.show()
        # Hide dashboard widgets if any
        if hasattr(mw, "_dash_refs"):
            for r in mw._dash_refs.values():
                if isinstance(r, QWidget):
                    r.hide()
        return

    if hasattr(mw, "_sad_row") and mw._sad_row == row:
        return
    mw._sad_row = row
    progs = [w for w in mw.warehouse if w.get("account_ref") == mw.accounts[row]["id"]]

    # First time 鈥?build skeleton
    if not hasattr(mw, "_dash_refs"):
        cleanup_emu_threads(mw)
        clear_dashboard(mw)
        mw._dash_refs = {}
        _ensure_dashboard(mw, row, progs)
    else:
        mw.ade.hide()
        for r in mw._dash_refs.values():
            if isinstance(r, QWidget):
                r.show()
        _update_dashboard(mw, row, progs)


def _ensure_dashboard(mw: Any, row: int, progs: list[dict]) -> None:
    """Create all dashboard widgets once. Stores refs in mw._dash_refs."""
    _build_header(mw, row)
    _build_maa_card(mw, row, progs)
    _build_emu_card(mw, row)
    _build_adb_card(mw, row)
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
        idx = refs["header_client"].findData(a.get("game_client", "Official"))
        if idx >= 0:
            refs["header_client"].blockSignals(True)
            refs["header_client"].setCurrentIndex(idx)
            refs["header_client"].blockSignals(False)

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


# 鈹€鈹€ Update helpers 鈹€鈹€

def _upd_maa_status(mw, a, progs, refs):
    if "maa_version_lbl" not in refs:
        return
    v = progs[0].get("maa_version", "") if progs else ""
    t = int(__import__("time").time() - mw._proc_start_times.get(progs[0]["id"], 0)) if progs and progs[0]["id"] in mw._proc_status else 0
    if t:
        refs["maa_version_lbl"].setText(f"馃煝 杩愯涓?({t // 60}m{t % 60}s)  {v}" if v else f"馃煝 杩愯涓?({t // 60}m{t % 60}s)")
    else:
        refs["maa_version_lbl"].setText(f"宸插畨瑁?{v}" if v else "宸插畨瑁?)
    if "maa_channel" in refs and progs:
        cur = progs[0].get("update_channel", "Stable")
        refs["maa_channel"].blockSignals(True)
        refs["maa_channel"].setCurrentText(cur)
        refs["maa_channel"].blockSignals(False)
    if "maa_auto_upd" in refs and progs:
        refs["maa_auto_upd"].blockSignals(True)
        refs["maa_auto_upd"].setChecked(progs[0].get("auto_update", mw.config.get("auto_update_maa", True)))
        refs["maa_auto_upd"].blockSignals(False)
    if "maa_sanity_cb" in refs:
        refs["maa_sanity_cb"].blockSignals(True)
        refs["maa_sanity_cb"].setChecked(a.get("sanity_driven",False))
        refs["maa_sanity_cb"].blockSignals(False)


def _upd_emu(mw, a, refs):
    if "emu_path" in refs:
        refs["emu_path"].blockSignals(True)
        refs["emu_path"].setText(a.get("emu_path", ""))
        refs["emu_path"].blockSignals(False)
    if "emu_launch_cb" in refs:
        refs["emu_launch_cb"].blockSignals(True)
        refs["emu_launch_cb"].setChecked(a.get("emu_launch",False))
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
    if "adb_switch" in refs:
        refs["adb_switch"].blockSignals(True)
        refs["adb_switch"].setText(a.get("account_switch", ""))
        refs["adb_switch"].blockSignals(False)


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
        refs["pipeline_sync"].setChecked(a.get("sync_tasks",False))
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
    # Update launch button click target
    if "action_launch_btn" in refs:
        refs["action_launch_btn"].clicked.disconnect()
        refs["action_launch_btn"].clicked.connect(lambda: mw._la(row))
    if "action_launch_all_btn" in refs:
        refs["action_launch_all_btn"].clicked.disconnect()
        refs["action_launch_all_btn"].clicked.connect(lambda: mw._la_all())
    if "action_update_btn" in refs and progs:
        refs["action_update_btn"].clicked.disconnect()
        refs["action_update_btn"].clicked.connect(lambda: mw.maint.cu_single(progs[0]))
    _build_adb_card(mw, row)
    _build_pipeline_card(mw, row, progs)
    _build_launch_card(mw, row)
    _build_action_buttons(mw, row, progs)
    mw.adl.addStretch()


# 鈹€鈹€ Header (name + client) 鈹€鈹€

def _build_header(mw: Any, row: int) -> None:
    a = mw.accounts[row]
    tr = QHBoxLayout()
    ne = QLineEdit(a.get("name", ""))
    ne.setFont(QFont("Microsoft YaHei UI", 16, QFont.Bold))
    ne.setPlaceholderText("璐﹀彿鍚?)
    ne.textChanged.connect(lambda t: (a.__setitem__("name", t), mw._save(), mw._ra()))
    tr.addWidget(ne, 1)
    cc = QComboBox()
    for k, v in CLIENT_TYPES.items():
        cc.addItem(v, k)
    idx = cc.findData(a.get("game_client", "Official"))
    cc.setCurrentIndex(max(0, idx))
    cc.currentIndexChanged.connect(lambda: (a.__setitem__("game_client", cc.currentData()), mw._save()))
    tr.addWidget(cc)
    tw = QWidget()
    tw.setLayout(tr)
    mw.adl.insertWidget(0, tw)
    mw._dash_refs["header_name"] = ne
    mw._dash_refs["header_client"] = cc


# 鈹€鈹€ MAA Status card 鈹€鈹€

def _build_maa_card(mw: Any, row: int, progs: list[dict]) -> None:
    a = mw.accounts[row]
    mc = QFrame()
    mc.setObjectName("card")
    mcl = QVBoxLayout(mc)
    mcl.setSpacing(5)
    mh = QHBoxLayout()
    mh.addWidget(QLabel("馃摝 MAA 鐘舵€?, font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    mh.addStretch()

    if progs:
        v = progs[0].get("maa_version", "")
        vl = QLabel(f"宸插畨瑁?{v}" if v else "宸茬粦瀹?)
        if progs[0]["id"] in mw._proc_status:
            t = int(__import__("time").time() - mw._proc_start_times.get(progs[0]["id"], 0))
            vl.setText(f"馃煝 杩愯涓?({t // 60}m{t % 60}s)  {v}" if v else f"馃煝 杩愯涓?({t // 60}m{t % 60}s)")
            vl.setStyleSheet("color:#8a8;font-weight:bold")
        else:
            vl.setStyleSheet("color:#8a8;font-weight:bold")
        mh.addWidget(vl)
        mcl.addLayout(mh)

        for p in progs:
            mcl.addWidget(QLabel(f"  鈻?{p['path']}"))

        vr = QHBoxLayout()
        vr.addWidget(QLabel("閫氶亾:"))
        ch = QComboBox()
        ch.addItems(["Stable", "Beta", "Alpha"])
        cur_ch = progs[0].get("update_channel", "Stable")
        ch.setCurrentText(cur_ch)
        ch.currentTextChanged.connect(lambda t: (progs[0].__setitem__("update_channel", t), mw._save()))
        vr.addWidget(ch)
        vr.addStretch()
        sw_ver = QPushButton("馃攧 鍒囨崲鐗堟湰")
        sw_ver.clicked.connect(lambda: mw.logs.switch_maa_version(progs[0], ch.currentText()))
        vr.addWidget(sw_ver)
        mcl.addLayout(vr)

        btr = QHBoxLayout()
        stats_btn = QPushButton("馃搳 缁熻")
        stats_btn.clicked.connect(lambda: mw.logs.show_stats(progs[0]))
        btr.addWidget(stats_btn)
        log_btn = QPushButton("馃搵 鏃ュ織")
        log_btn.clicked.connect(lambda: mw.logs.view_log(progs[0]))
        btr.addWidget(log_btn)
        btr.addStretch()
        mcl.addLayout(btr)

        # Resource auto-update indicator
        aur = QHBoxLayout()
        au_cb = QCheckBox("鑷姩鏇存柊璧勬簮")
        au_cb.setChecked(progs[0].get("auto_update", mw.config.get("auto_update_maa", True)))
        au_cb.setToolTip("妫€娴嬪埌鏂扮増鏈椂鑷姩涓嬭浇鏇存柊")
        au_cb.toggled.connect(lambda v: (progs[0].__setitem__("auto_update", v), mw._save()))
        aur.addWidget(au_cb)
        aur.addStretch()
        au_lbl = QLabel("宸插惎鐢? if au_cb.isChecked() else "宸茬鐢?)
        au_lbl.setStyleSheet("color:#8a8" if au_cb.isChecked() else "color:#a88")
        au_cb.toggled.connect(lambda v: au_lbl.setText("宸插惎鐢? if v else "宸茬鐢?) or au_lbl.setStyleSheet("color:#8a8" if v else "color:#a88"))
        aur.addWidget(au_lbl)
        mcl.addLayout(aur)

        # Sanity-driven auto-launch toggle
        sdr = QHBoxLayout()
        sd_cb = QCheckBox("鐞嗘櫤鍥炴弧鑷姩鍚姩")
        sd_cb.setChecked(a.get("sanity_driven",False))
        sd_cb.setToolTip("涓婁竴杞埛瀹屽悗锛岀瓑鐞嗘櫤鍥炴弧鑷姩鍐嶅惎鍔?)
        sd_cb.toggled.connect(lambda v: (a.__setitem__("sanity_driven", v), mw._save()))
        sdr.addWidget(sd_cb)
        # Show next launch time if available
        lq = getattr(mw, "launch_queue", None)
        if lq:
            nxt = lq.get_next_for(a["id"])
            if nxt:
                if nxt == "鍗冲皢鍚姩":
                    sdr.addWidget(QLabel("鈫?鍗冲皢鍚姩"))
                else:
                    sdr.addWidget(QLabel(f"鈫?{nxt}"))
                sdr.addStretch()
            else:
                sdr.addStretch()
        else:
            sdr.addStretch()
        sd_lbl = QLabel("宸插惎鐢? if sd_cb.isChecked() else "宸茬鐢?)
        sd_lbl.setStyleSheet("color:#8a8" if sd_cb.isChecked() else "color:#a88")
        sd_cb.toggled.connect(lambda v: sd_lbl.setText("宸插惎鐢? if v else "宸茬鐢?) or sd_lbl.setStyleSheet("color:#8a8" if v else "color:#a88"))
        sdr.addWidget(sd_lbl)
        mcl.addLayout(sdr)

        today = datetime.now().strftime("%Y-%m-%d")
        sd = a.get("stats", {}).get(today, {})
        if sd.get("launches"):
            mcl.addWidget(QLabel(f"  浠婃棩: 鍚姩 {sd['launches']} 娆?))
    else:
        vl = QLabel("鏈畨瑁?)
        vl.setStyleSheet("color:#a88;font-weight:bold")
        mh.addWidget(vl)
        mcl.addLayout(mh)
        mcl.addWidget(QLabel("  鐐瑰嚮涓嬫柟涓嬭浇鎴栫粦瀹?))

    mw.adl.insertWidget(2, mc)
    if progs:
        mw._dash_refs["maa_version_lbl"] = vl
        mw._dash_refs["maa_channel"] = ch
        mw._dash_refs["maa_auto_upd"] = au_cb
        mw._dash_refs["maa_sanity_cb"] = sd_cb


# 鈹€鈹€ Emulator card 鈹€鈹€

def _build_emu_card(mw: Any, row: int) -> None:
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
    ecl.addWidget(QLabel("馃枼 妯℃嫙鍣?, font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))

    rp = QHBoxLayout()
    rp.addWidget(_lbl("鍚姩:"))
    emu_path_edit = QLineEdit(a.get("emu_path", ""))
    emu_path_edit.setPlaceholderText("妯℃嫙鍣ㄥ惎鍔ㄨ矾寰?)
    emu_path_edit.textChanged.connect(lambda t: a.update({"emu_path": t}) or mw._save())
    rp.addWidget(emu_path_edit, 1)
    rp.addWidget(QPushButton("馃搨", clicked=lambda: mw.emu.browse_file(emu_path_edit, a, "emu_path")))
    ecl.addLayout(rp)

    ri = QHBoxLayout()
    ri.addWidget(_lbl("瀹炰緥:"))
    ed_sel = QComboBox()
    ed_sel.setMinimumWidth(180)
    combo_saved_idx = a.get("emu_instance_index", "")
    combo_saved_name = a.get("emu_instance_name", "")
    mw.emu.refresh_instance_list(ed_sel, combo_saved_idx, combo_saved_name)

    ae2_ref = []  # mutable container for the address line edit created in ADB card

    def _on_ins(i):
        if ed_sel.currentData():
            ins = ed_sel.currentData()
            cli = find_mumu_cli()
            if cli:
                emu_path_edit.setText(str(cli))
                a.__setitem__("emu_path", str(cli))
                a.__setitem__("emu_add_cmd", "")
            a.__setitem__("emu_instance_index", ins["index"])
            a.__setitem__("emu_instance_name", ins.get("name", ""))
            if ins.get("adb_port") and ae2_ref:
                ae2_ref[0].setText(f"127.0.0.1:{ins['adb_port']}")
                a.__setitem__("adb_address", ae2_ref[0].text())
            mw._save()

    ed_sel.currentIndexChanged.connect(_on_ins)
    ri.addWidget(ed_sel, 1)
    ri.addWidget(QPushButton("馃攧", clicked=lambda: mw.emu.refresh_instance_list(ed_sel), toolTip="鍒锋柊瀹炰緥鍒楄〃"))
    ecl.addLayout(ri)

    rl2 = QHBoxLayout()
    rl2.addWidget(_lbl(""))
    cb_oe = QCheckBox("鑷惎妯℃嫙鍣?)
    cb_oe.setChecked(a.get("emu_launch",False))
    cb_oe.setToolTip("鍚姩鏃惰嚜鍔ㄩ€氳繃 mumu-cli 鍚姩妯℃嫙鍣?)
    cb_oe.toggled.connect(lambda v: a.update({"emu_launch": v}) or mw._save())
    rl2.addWidget(cb_oe)
    rl2.addWidget(QLabel("绛夊緟"))
    ws_sp = QSpinBox()
    ws_sp.setRange(0, 300)
    ws_sp.setValue(a.get("emu_wait", 30))
    ws_sp.setSuffix(" 绉?)
    ws_sp.valueChanged.connect(lambda v: a.update({"emu_wait": v}) or mw._save())
    rl2.addWidget(ws_sp)
    rl2.addStretch()
    rl2.addWidget(QPushButton("馃攳 鎵鍙?, clicked=lambda: mw.emu.scan_port(a, emu_path_edit, ae2_ref[0] if ae2_ref else None)))
    rl2.addWidget(QPushButton("鈴?鍏抽棴", clicked=lambda: mw.emu.stop_emu(a), objectName="stopBtn"))
    ecl.addLayout(rl2)

    mw.adl.insertWidget(3, ec)
    mw._dash_refs["emu_path"] = emu_path_edit
    mw._dash_refs["emu_inst_sel"] = ed_sel
    mw._dash_refs["emu_launch_cb"] = cb_oe
    mw._dash_refs["emu_wait_sp"] = ws_sp

    # Store ae2_ref for later population by emulator card
    mw._emu_ae2_ref = ae2_ref


# 鈹€鈹€ ADB card 鈹€鈹€

def _build_adb_card(mw: Any, row: int) -> None:
    a = mw.accounts[row]

    def _lbl(t):
        l = QLabel(t)
        l.setFixedWidth(55)
        l.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return l

    cc = QFrame()
    cc.setObjectName("card")
    ccl = QVBoxLayout(cc)
    ccl.setSpacing(5)
    ccl.addWidget(QLabel("馃摫 ADB 杩炴帴", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))

    rpr = QHBoxLayout()
    rpr.addWidget(_lbl("棰勮:"))
    emu_sel = QComboBox()
    emu_sel.addItem("鈥?閫夋嫨 鈥?, "")
    for ep in EMU_PRESETS:
        emu_sel.addItem(ep["name"], ep["type"])
    idx = emu_sel.findData(a.get("connection_preset", ""))
    if idx >= 0:
        emu_sel.setCurrentIndex(idx)

    def _on_emu(i):
        if i > 0 and i <= len(EMU_PRESETS):
            ep = EMU_PRESETS[i - 1]
            a["connection_preset"] = ep["type"]
            mw._save()

    emu_sel.currentIndexChanged.connect(_on_emu)
    rpr.addWidget(emu_sel, 1)
    ccl.addLayout(rpr)

    rap = QHBoxLayout()
    rap.addWidget(_lbl("ADB:"))
    adb_p = QLineEdit(a.get("adb_path", ""))
    adb_p.setPlaceholderText("鐣欑┖浣跨敤榛樿")
    adb_p.textChanged.connect(lambda t: a.update({"adb_path": t}) or mw._save())
    rap.addWidget(adb_p, 1)
    rap.addWidget(QPushButton("馃搨", clicked=lambda: mw.emu.browse_adb(adb_p, a)))
    ccl.addLayout(rap)

    raa = QHBoxLayout()
    raa.addWidget(_lbl("鍦板潃:"))
    ae2 = QLineEdit(a.get("adb_address", ""))
    ae2.setPlaceholderText("127.0.0.1:7555")
    ae2.textChanged.connect(lambda t: a.update({"adb_address": t}) or mw._save())
    raa.addWidget(ae2, 1)
    emu_combo = QComboBox()
    emu_combo.addItem("鍦ㄧ嚎璁惧", "")
    emu_combo.setMinimumWidth(140)
    emu_combo.currentIndexChanged.connect(lambda i: ae2.setText(emu_combo.currentData()) if emu_combo.currentData() else None)
    raa.addWidget(emu_combo)
    ccl.addLayout(raa)

    ras = QHBoxLayout()
    ras.addWidget(_lbl("璐﹀彿:"))
    sw_an = QLineEdit(a.get("account_switch", ""))
    sw_an.setPlaceholderText("濡?123***4567 鎴?mail@gmail.com锛岀暀绌虹鐢?)
    sw_an.textChanged.connect(lambda t: a.update({"account_switch": t}) or mw._save())
    ras.addWidget(sw_an, 1)
    ccl.addLayout(ras)

    ract = QHBoxLayout()
    ract.addWidget(_lbl(""))
    dc = QPushButton("馃攳 鎵弿")
    dc.clicked.connect(lambda cb=emu_combo: mw.emu.scan(a, cb))
    ract.addWidget(dc)
    tb2 = QPushButton("娴嬭瘯")
    tb2.clicked.connect(lambda: mw.emu.test_adb(a))
    ract.addWidget(tb2)
    ss_btn = QPushButton("馃摳 鎴浘")
    ss_btn.clicked.connect(lambda: mw.emu.screenshot(a))
    ract.addWidget(ss_btn)
    ract.addStretch()
    ccl.addLayout(ract)

    mw._ast = QLabel("")
    ccl.addWidget(mw._ast)

    mw.adl.insertWidget(4, cc)
    mw._dash_refs["adb_preset"] = emu_sel
    mw._dash_refs["adb_path"] = adb_p
    mw._dash_refs["adb_addr"] = ae2
    mw._dash_refs["adb_switch"] = sw_an

    # Pass ae2 reference back to emu card via the stored ref
    if hasattr(mw, "_emu_ae2_ref"):
        mw._emu_ae2_ref.append(ae2)


# 鈹€鈹€ Pipeline card 鈹€鈹€

def _batch_apply(mw: Any, src_acc: dict, src_progs: list[dict], src_row: int) -> None:
    """Open dialog to apply current account's config to selected accounts."""
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QCheckBox, QPushButton, QLabel
    from PySide6.QtGui import QFont

    d = QDialog(mw)
    d.setWindowTitle("鎵归噺搴旂敤閰嶇疆")
    d.setMinimumSize(350, 280)
    l = QVBoxLayout(d)

    l.addWidget(QLabel("閫夋嫨瑕佸簲鐢ㄩ厤缃殑鐩爣璐﹀彿锛?, font=QFont("Microsoft YaHei UI", 11)))
    l.addWidget(QLabel(f"  鏉ユ簮锛歿src_acc.get('name', '?')}"))
    l.addWidget(QLabel(f"  娴佹按绾匡細{src_progs[0].get('task_pipeline', '鏃?) if src_progs else '鏃?}"))

    checks = {}
    for i, a in enumerate(mw.accounts):
        if a["id"] == src_acc["id"]:
            continue
        cb = QCheckBox(a.get("name", f"璐﹀彿{i+1}"))
        cb.setChecked(True)
        checks[a["id"]] = cb
        l.addWidget(cb)

    if not checks:
        l.addWidget(QLabel("锛堟病鏈夊叾浠栬处鍙峰彲搴旂敤锛?))

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
            a["task_settings"] = dict(src_settings)
            a["sync_tasks"] = src_sync
            for w in mw.warehouse:
                if w.get("account_ref") == a["id"]:
                    w["task_pipeline"] = src_pipe
        mw._save()
        mw._log(f"鎵归噺搴旂敤閰嶇疆鍒?{len(selected)} 涓处鍙?)
        d.accept()

    btn = QPushButton("搴旂敤")
    btn.clicked.connect(_apply)
    l.addWidget(btn)
    d.exec()


def _build_pipeline_card(mw: Any, row: int, progs: list[dict]) -> None:
    a = mw.accounts[row]

    tc = QFrame()
    tc.setObjectName("card")
    tcl = QVBoxLayout(tc)
    tcl.setSpacing(5)
    tcl.addWidget(QLabel("鈿?娴佹按绾?, font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))

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
    cfg_btn = QPushButton("鈿?鍙傛暟")
    ts = a.get("task_settings", {})

    def _cfg_clicked():
        TaskSettingsDialog(mw, ts, progs[0].get("task_pipeline", "")).exec()
        a.__setitem__("task_settings", ts)
        mw._save()

    cfg_btn.clicked.connect(lambda: _cfg_clicked())
    mr2.addWidget(cfg_btn)

    tmpl_btn = QPushButton("馃捑 妯℃澘")
    tm = QMenu()

    def _sv_tmpl():
        name, ok = QInputDialog.getText(mw, "淇濆瓨妯℃澘", "鍚嶇О:", text="鏃ュ父妯″紡")
        if ok and name:
            a.setdefault("task_templates", {})[name] = dict(ts)
            a.setdefault("pipe_templates", {})[name] = progs[0].get("task_pipeline", "")
            mw._save()
            mw._log(f"妯℃澘宸蹭繚瀛? {name}")

    def _ld_tmpl(name):
        if name in a.get("task_templates", {}):
            ts.clear()
            ts.update(a["task_templates"][name])
            for p in progs:
                p["task_pipeline"] = a.get("pipe_templates", {}).get(name, "")
            mw._save()
            build_account_dashboard(mw, row)

    for n in a.get("task_templates", {}):
        tm.addAction(f"馃搨 {n}", lambda n=n: _ld_tmpl(n))
        tm.addAction(f"鉁?鍒爗n}", lambda n=n: (a["task_templates"].pop(n, None), a.get("pipe_templates", {}).pop(n, None), mw._save(), build_account_dashboard(mw, row)))
    if a.get("task_templates", {}):
        tm.addSeparator()
    tm.addAction("馃捑 淇濆瓨褰撳墠...", _sv_tmpl)
    tm.addSeparator()
    tm.addAction("馃搵 鎵归噺搴旂敤褰撳墠閰嶇疆鍒?..", lambda: _batch_apply(mw, a, progs, row))
    tmpl_btn.setMenu(tm)
    mr2.addWidget(tmpl_btn)

    sc = QCheckBox("鍚姩鏃跺悓姝?)
    sc.setChecked(a.get("sync_tasks",False))
    sc.toggled.connect(lambda v: (a.__setitem__("sync_tasks", v), mw._save()))
    mr2.addWidget(sc)
    mr2.addStretch()

    mr2.addWidget(QLabel("妯″紡:"))
    mc2 = QComboBox()
    mc2.addItems(["gui", "cli"])
    mc2.setCurrentText(progs[0].get("launch_mode", "gui") if progs else "gui")
    mc2.currentTextChanged.connect(lambda t: _up())
    mr2.addWidget(mc2)
    mr2.addStretch()
    mc2_ref.append(mc2)

    if mc2.currentText() == "cli":
        cl = _find_maa_cli()
        l2 = QLabel("maa-cli 灏辩华" if cl else "maa-cli 鏈畨瑁?)
        l2.setStyleSheet("color:#8a8" if cl else "color:#a88")
        mr2.addWidget(l2)
    mr2.addStretch()
    tcl.addLayout(mr2)

    mw.adl.insertWidget(5, tc)
    mw._dash_refs["pipeline_cbs"] = task_cbs
    mw._dash_refs["pipeline_mode"] = mc2
    mw._dash_refs["pipeline_sync"] = sc


# 鈹€鈹€ Launch & post-actions card 鈹€鈹€

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
    ocl.addWidget(QLabel("馃攧 鍚姩涓庡畬鎴愬悗", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))

    or1 = QHBoxLayout()
    or1.addWidget(_lbl("鍚姩:"))

    cb_sm = QCheckBox("鍚姩鍚庢渶灏忓寲")
    cb_sm.setChecked(a.get("start_minimized",False))
    cb_sm.toggled.connect(lambda v: (a.__setitem__("start_minimized", v), mw._save()))
    or1.addWidget(cb_sm)

    cb_sd = QCheckBox("鐩存帴杩愯")
    cb_sd.setChecked(a.get("start_directly",False))
    cb_sd.toggled.connect(lambda v: (a.__setitem__("start_directly", v), mw._save()))
    or1.addWidget(cb_sd)

    cb_ad = QCheckBox("ADB 澶辫触鍚ā鎷熷櫒")
    cb_ad.setChecked(a.adb_fail_launch_emu)
    cb_ad.toggled.connect(lambda v: (a.__setitem__("adb_fail_launch_emu", v), mw._save()))
    or1.addWidget(cb_ad)

    or1.addWidget(QLabel("ADB 閲嶈瘯"))
    ar = QSpinBox()
    ar.setRange(0, 10)
    ar.setValue(a.get("adb_retry", 0))
    ar.setSuffix(" 娆?)
    ar.setMaximumWidth(60)
    ar.valueChanged.connect(lambda v: a.update({"adb_retry": v}) or mw._save())
    or1.addWidget(ar)
    or1.addStretch()
    ocl.addLayout(or1)

    or2 = QHBoxLayout()
    or2.addWidget(_lbl("瀹屾垚鍚?"))
    post_actions = a.get("post_action", "")
    if post_actions and post_actions[0] == "[":
        try:
            post_actions = ",".join(__import__("json").loads(post_actions))
            a["post_action"] = post_actions
        except Exception:
            pass
    post_arr = post_actions.split(",") if post_actions else []
    post_cbs = {}

    for k, v in [("BackToAndroidHome", "杩斿洖涓诲睆"), ("ExitArknights", "閫€鍑烘柟鑸?), ("ExitEmulator", "鍏虫ā鎷熷櫒"), ("ExitSelf", "閫€鍑篗AA")]:
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


# 鈹€鈹€ Action buttons 鈹€鈹€

def _build_action_buttons(mw: Any, row: int, progs: list[dict]) -> None:
    a = mw.accounts[row]

    # 鈹€鈹€ Config summary bar 鈹€鈹€
    summary_parts = []
    if a.get("adb_address", ""):
        summary_parts.append(f"馃摫 {a['adb_address']}")
    if a.get("emu_instance_index", ""):
        summary_parts.append(f"馃枼 MuMu#{a['emu_instance_index']}")
    if a.get("sanity_driven",False):
        summary_parts.append("馃拪 鐞嗘櫤椹卞姩")
    if a.get("emu_launch"):
        summary_parts.append("馃枼 鑷惎妯℃嫙鍣?)
    if a.get("start_minimized",False):
        summary_parts.append("馃搻 鏈€灏忓寲")
    if a.get("start_directly",False):
        summary_parts.append("鈿?鐩存帴杩愯")
    pipe = progs[0].get("task_pipeline", "") if progs else ""
    if pipe:
        name_map = {"startup":"鍞ら啋","fight":"鍒峰叧","recruit":"鍏嫑","infrast":"鍩哄缓","mall":"淇＄敤","award":"濂栧姳","roguelike":"鑲夐附","reclamation":"鐢熸伅","closedown":"鍏抽棴"}
        tasks = [name_map.get(t.strip().lower(), t.strip()) for t in pipe.split(",") if t.strip()][:5]
        summary_parts.append(f"鈿?{'路'.join(tasks)}")
    if summary_parts:
        summary_text = "  鈹? ".join(summary_parts)
        summary_lbl = QLabel(summary_text)
        summary_lbl.setStyleSheet("color:#888;padding:4px 0;font-size:10pt")
        summary_lbl.setWordWrap(True)
        mw.adl.addWidget(summary_lbl)

    # 鈹€鈹€ Buttons 鈹€鈹€
    bw = QWidget()
    bl = QHBoxLayout(bw)
    bl.setContentsMargins(0, 0, 0, 0)

    if progs:
        lb2 = QPushButton("鈻?鍔犲叆闃熷垪")
        lb2.setObjectName("startBtn")
        lb2.setMinimumHeight(36)
        lb2.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        lb2.setToolTip("鍏ラ槦鍚庣珛鍒诲惎鍔紙鑻ユā鎷熷櫒绌洪棽锛?)
        lb2.clicked.connect(lambda: (mw.launch_queue.enqueue(a["id"], "manual", priority=0), mw.launch_queue._tick(), refresh_config_cards(mw)))
        mw._dash_refs["action_launch_btn"] = lb2
        bl.addWidget(lb2)
        launch_all_btn = QPushButton("鈻?鍏ㄩ儴鍏ラ槦", clicked=lambda: mw._la_all())
        mw._dash_refs["action_launch_all_btn"] = launch_all_btn
        bl.addWidget(launch_all_btn)
        upd_btn = QPushButton("妫€鏌ユ洿鏂?, clicked=lambda: mw.maint.cu_single(progs[0]))
        mw._dash_refs["action_update_btn"] = upd_btn
        bl.addWidget(upd_btn)
    else:
        dl = QPushButton("猬?涓嬭浇 MAA")
        dl.setObjectName("addProgBtn")
        dl.setMinimumHeight(36)
        dl.clicked.connect(lambda: mw.maint.dl_maa(row))
        bl.addWidget(dl)
        bl.addWidget(QPushButton("馃搨 缁戝畾", clicked=lambda: mw.maint.pk_maa(row)))

    bl.addStretch()
    mw.adl.addWidget(bw)

