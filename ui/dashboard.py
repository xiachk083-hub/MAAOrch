"""Account dashboard builder — extracted from main_window.py."""
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
    if hasattr(mw, "_sad_row") and mw._sad_row == row:
        return
    mw._sad_row = row
    cleanup_emu_threads(mw)
    clear_dashboard(mw)

    if row < 0 or row >= len(mw.accounts):
        mw.ade.show()
        return

    progs = [w for w in mw.warehouse if w.get("account_ref") == mw.accounts[row]["id"]]

    _build_header(mw, row)
    _build_maa_card(mw, row, progs)
    _build_emu_card(mw, row)
    _build_adb_card(mw, row)
    _build_pipeline_card(mw, row, progs)
    _build_launch_card(mw, row)
    _build_action_buttons(mw, row, progs)
    mw.adl.addStretch()


# ── Header (name + client) ──

def _build_header(mw: Any, row: int) -> None:
    a = mw.accounts[row]
    tr = QHBoxLayout()
    ne = QLineEdit(a.get("name", ""))
    ne.setFont(QFont("Microsoft YaHei UI", 16, QFont.Bold))
    ne.setPlaceholderText("账号名")
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
        if progs[0]["id"] in mw._proc_status:
            t = int(__import__("time").time() - mw._proc_start_times.get(progs[0]["id"], 0))
            vl.setText(f"🟢 运行中 ({t // 60}m{t % 60}s)  {v}" if v else f"🟢 运行中 ({t // 60}m{t % 60}s)")
            vl.setStyleSheet("color:#8a8;font-weight:bold")
        else:
            vl.setStyleSheet("color:#8a8;font-weight:bold")
        mh.addWidget(vl)
        mcl.addLayout(mh)

        for p in progs:
            mcl.addWidget(QLabel(f"  ▶ {p['path']}"))

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
        stats_btn = QPushButton("📊 统计")
        stats_btn.clicked.connect(lambda: mw.logs.show_stats(progs[0]))
        btr.addWidget(stats_btn)
        log_btn = QPushButton("📋 日志")
        log_btn.clicked.connect(lambda: mw.logs.view_log(progs[0]))
        btr.addWidget(log_btn)
        btr.addStretch()
        mcl.addLayout(btr)

        today = datetime.now().strftime("%Y-%m-%d")
        sd = a.get("stats", {}).get(today, {})
        if sd.get("launches"):
            mcl.addWidget(QLabel(f"  今日: 启动 {sd['launches']} 次"))
    else:
        vl = QLabel("未安装")
        vl.setStyleSheet("color:#a88;font-weight:bold")
        mh.addWidget(vl)
        mcl.addLayout(mh)
        mcl.addWidget(QLabel("  点击下方下载或绑定"))

    mw.adl.insertWidget(2, mc)


# ── Emulator card ──

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
    ecl.addWidget(QLabel("🖥 模拟器", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))

    rp = QHBoxLayout()
    rp.addWidget(_lbl("启动:"))
    emu_path_edit = QLineEdit(a.get("emu_path", ""))
    emu_path_edit.setPlaceholderText("模拟器启动路径")
    emu_path_edit.textChanged.connect(lambda t: a.update({"emu_path": t}) or mw._save())
    rp.addWidget(emu_path_edit, 1)
    rp.addWidget(QPushButton("📂", clicked=lambda: mw.emu.browse_file(emu_path_edit, a, "emu_path")))
    ecl.addLayout(rp)

    ri = QHBoxLayout()
    ri.addWidget(_lbl("实例:"))
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
    ri.addWidget(QPushButton("🔄", clicked=lambda: mw.emu.refresh_instance_list(ed_sel), toolTip="刷新实例列表"))
    ecl.addLayout(ri)

    rl2 = QHBoxLayout()
    rl2.addWidget(_lbl(""))
    cb_oe = QCheckBox("自启模拟器")
    cb_oe.setChecked(a.get("emu_launch", False))
    cb_oe.setToolTip("启动时自动通过 mumu-cli 启动模拟器")
    cb_oe.toggled.connect(lambda v: a.update({"emu_launch": v}) or mw._save())
    rl2.addWidget(cb_oe)
    rl2.addWidget(QLabel("等待"))
    ws_sp = QSpinBox()
    ws_sp.setRange(0, 300)
    ws_sp.setValue(a.get("emu_wait", 30))
    ws_sp.setSuffix(" 秒")
    ws_sp.valueChanged.connect(lambda v: a.update({"emu_wait": v}) or mw._save())
    rl2.addWidget(ws_sp)
    rl2.addStretch()
    rl2.addWidget(QPushButton("🔍 扫端口", clicked=lambda: mw.emu.scan_port(a, emu_path_edit, ae2_ref[0] if ae2_ref else None)))
    rl2.addWidget(QPushButton("⏻ 关闭", clicked=lambda: mw.emu.stop_emu(a), objectName="stopBtn"))
    ecl.addLayout(rl2)

    mw.adl.insertWidget(3, ec)

    # Store ae2_ref for later population by emulator card
    mw._emu_ae2_ref = ae2_ref


# ── ADB card ──

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
    ccl.addWidget(QLabel("📱 ADB 连接", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))

    rpr = QHBoxLayout()
    rpr.addWidget(_lbl("预设:"))
    emu_sel = QComboBox()
    emu_sel.addItem("— 选择 —", "")
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
    adb_p.setPlaceholderText("留空使用默认")
    adb_p.textChanged.connect(lambda t: a.update({"adb_path": t}) or mw._save())
    rap.addWidget(adb_p, 1)
    rap.addWidget(QPushButton("📂", clicked=lambda: mw.emu.browse_adb(adb_p, a)))
    ccl.addLayout(rap)

    raa = QHBoxLayout()
    raa.addWidget(_lbl("地址:"))
    ae2 = QLineEdit(a.get("adb_address", ""))
    ae2.setPlaceholderText("127.0.0.1:7555")
    ae2.textChanged.connect(lambda t: a.update({"adb_address": t}) or mw._save())
    raa.addWidget(ae2, 1)
    emu_combo = QComboBox()
    emu_combo.addItem("在线设备", "")
    emu_combo.setMinimumWidth(140)
    emu_combo.currentIndexChanged.connect(lambda i: ae2.setText(emu_combo.currentData()) if emu_combo.currentData() else None)
    raa.addWidget(emu_combo)
    ccl.addLayout(raa)

    ras = QHBoxLayout()
    ras.addWidget(_lbl("账号:"))
    sw_an = QLineEdit(a.get("account_switch", ""))
    sw_an.setPlaceholderText("如 123***4567 或 mail@gmail.com，留空禁用")
    sw_an.textChanged.connect(lambda t: a.update({"account_switch": t}) or mw._save())
    ras.addWidget(sw_an, 1)
    ccl.addLayout(ras)

    ract = QHBoxLayout()
    ract.addWidget(_lbl(""))
    dc = QPushButton("🔍 扫描")
    dc.clicked.connect(lambda cb=emu_combo: mw.emu.scan(a, cb))
    ract.addWidget(dc)
    tb2 = QPushButton("测试")
    tb2.clicked.connect(lambda: mw.emu.test_adb(a))
    ract.addWidget(tb2)
    ss_btn = QPushButton("📸 截图")
    ss_btn.clicked.connect(lambda: mw.emu.screenshot(a))
    ract.addWidget(ss_btn)
    ract.addStretch()
    ccl.addLayout(ract)

    mw._ast = QLabel("")
    ccl.addWidget(mw._ast)

    mw.adl.insertWidget(4, cc)

    # Pass ae2 reference back to emu card via the stored ref
    if hasattr(mw, "_emu_ae2_ref"):
        mw._emu_ae2_ref.append(ae2)


# ── Pipeline card ──

def _build_pipeline_card(mw: Any, row: int, progs: list[dict]) -> None:
    a = mw.accounts[row]

    tc = QFrame()
    tc.setObjectName("card")
    tcl = QVBoxLayout(tc)
    tcl.setSpacing(5)
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
    tm = QMenu()

    def _sv_tmpl():
        name, ok = QInputDialog.getText(mw, "保存模板", "名称:", text="日常模式")
        if ok and name:
            a.setdefault("task_templates", {})[name] = dict(ts)
            a.setdefault("pipe_templates", {})[name] = progs[0].get("task_pipeline", "")
            mw._save()
            mw._log(f"模板已保存: {name}")

    def _ld_tmpl(name):
        if name in a.get("task_templates", {}):
            ts.clear()
            ts.update(a["task_templates"][name])
            for p in progs:
                p["task_pipeline"] = a.get("pipe_templates", {}).get(name, "")
            mw._save()
            build_account_dashboard(mw, row)

    for n in a.get("task_templates", {}):
        tm.addAction(f"📂 {n}", lambda n=n: _ld_tmpl(n))
        tm.addAction(f"✕ 删{n}", lambda n=n: (a["task_templates"].pop(n, None), a.get("pipe_templates", {}).pop(n, None), mw._save(), build_account_dashboard(mw, row)))
    if a.get("task_templates", {}):
        tm.addSeparator()
    tm.addAction("💾 保存当前...", _sv_tmpl)
    tmpl_btn.setMenu(tm)
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
        l2.setStyleSheet("color:#8a8" if cl else "color:#a88")
        mr2.addWidget(l2)
    mr2.addStretch()
    tcl.addLayout(mr2)

    mw.adl.insertWidget(5, tc)


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


# ── Action buttons ──

def _build_action_buttons(mw: Any, row: int, progs: list[dict]) -> None:
    bw = QWidget()
    bl = QHBoxLayout(bw)
    bl.setContentsMargins(0, 0, 0, 0)

    if progs:
        lb2 = QPushButton("▶ 启动")
        lb2.setObjectName("startBtn")
        lb2.setMinimumHeight(36)
        lb2.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        lb2.clicked.connect(lambda: mw._la(row))
        bl.addWidget(lb2)
        bl.addWidget(QPushButton("▶ 启动全部", clicked=lambda: mw._la_all()))
        bl.addWidget(QPushButton("检查更新", clicked=lambda: mw.maint.cu_single(progs[0])))
    else:
        dl = QPushButton("⬇ 下载 MAA")
        dl.setObjectName("addProgBtn")
        dl.setMinimumHeight(36)
        dl.clicked.connect(lambda: mw.maint.dl_maa(row))
        bl.addWidget(dl)
        bl.addWidget(QPushButton("📂 绑定", clicked=lambda: mw.maint.pk_maa(row)))

    bl.addStretch()
    mw.adl.addWidget(bw)
