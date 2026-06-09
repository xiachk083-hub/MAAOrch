"""Smart scheduling panel — modern account list (no table widget)."""
from __future__ import annotations
import time
from typing import Any
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QScrollArea, QLineEdit, QFrame, QSizePolicy,
)
from services.smart_scheduler import MATERIAL_STAGES, _arknights_now


def build_smart_panel(mw: Any) -> QWidget:
    mw.smart_v = QWidget()
    vl = QVBoxLayout(mw.smart_v)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(0)

    # ── Search bar ──
    sr = QHBoxLayout()
    sr.setContentsMargins(12, 8, 12, 4)
    sr.setSpacing(8)
    mw._smart_search = QLineEdit()
    mw._smart_search.setPlaceholderText("搜索账号...")
    mw._smart_search.textChanged.connect(lambda: _rebuild_list(mw))
    sr.addWidget(mw._smart_search, 1)
    add_btn = QPushButton("＋添加")
    add_btn.setObjectName("startBtn")
    add_btn.setFixedHeight(30)
    add_btn.clicked.connect(lambda: _add_account(mw))
    sr.addWidget(add_btn)
    vl.addLayout(sr)

    # ── List header (must match _make_row column widths) ──
    hdr = QFrame()
    hdr.setFixedHeight(28)
    hl = QHBoxLayout(hdr)
    hl.setContentsMargins(12, 0, 12, 0)
    hl.setSpacing(0)
    # Column definitions: (text, width) — width 0 means stretch
    cols = [("", 28), ("账号", 0), ("状态", 55), ("关卡", 70), ("剿灭", 65)]
    for text, w in cols:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#555;font-size:8pt;font-weight:bold")
        if w == 0:
            hl.addWidget(lbl, 1)
        else:
            lbl.setFixedWidth(w)
            lbl.setAlignment(Qt.AlignCenter)
            hl.addWidget(lbl)
    for d in ["一","二","三","四","五","六","日"]:
        lbl = QLabel(d)
        lbl.setFixedWidth(42)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color:#444;font-size:7pt")
        hl.addWidget(lbl)
    hl.addSpacing(30)
    mw._list_header = hdr
    vl.addWidget(hdr)

    # ── Scrollable account list ──
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setStyleSheet("QScrollArea{background:transparent;border:none}")

    list_w = QWidget()
    list_w.setObjectName("accountList")
    mw._list_layout = QVBoxLayout(list_w)
    mw._list_layout.setContentsMargins(0, 0, 0, 0)
    mw._list_layout.setSpacing(1)
    mw._list_layout.addStretch()
    scroll.setWidget(list_w)
    vl.addWidget(scroll, 1)

    # ── Batch bar ──
    br = QHBoxLayout()
    br.setContentsMargins(12, 4, 12, 4)
    br.setSpacing(8)
    def _batch_handler(mw, action):
        selected = _get_selected(mw)
        if not selected: return
        lq = getattr(mw, "launch_queue", None)
        runner = getattr(mw, "runner", None)
        if action == "enq" and lq:
            for aid in selected: lq.enqueue(aid, "manual")
            lq.tick()
        elif action == "stop" and runner:
            for aid in selected: runner.stop(aid)
        elif action == "del":
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(mw, "确认", f"删除 {len(selected)} 个?") == QMessageBox.Yes:
                mw.accounts[:] = [a for a in mw.accounts if a.get("id") not in selected]
                mw._save()
        elif action == "edit":
            from ui.batch_edit import open_batch_edit
            open_batch_edit(mw, selected)
        _rebuild_list(mw)

    for name, attr, act in [("批量设置","_batch_edit","edit"),("批量入队","_batch_enq","enq"),
                             ("批量停止","_batch_stop","stop"),("批量删除","_batch_del","del")]:
        btn = QPushButton(name)
        btn.setEnabled(False)
        btn.setFixedHeight(26)
        btn.clicked.connect(lambda _, a=act: _batch_handler(mw, a))
        setattr(mw, attr, btn)
        br.addWidget(btn)
    mw._batch_stat = QLabel("")
    mw._batch_stat.setStyleSheet("color:#555;font-size:8pt")
    br.addWidget(mw._batch_stat)
    br.addStretch()
    vl.addLayout(br)

    mw._smart_filter = ""
    mw._list_rows = []
    mw._set_smart_filter = lambda k: _set_filter(mw, k)
    _rebuild_list(mw)

    timer = QTimer(mw.smart_v)
    timer.timeout.connect(lambda: _update_status(mw))
    timer.start(3000)

    return mw.smart_v


def _set_filter(mw: Any, key: str) -> None:
    if mw._smart_filter == key:
        mw._smart_filter = ""
    else:
        mw._smart_filter = key
    _rebuild_list(mw)


def _add_account(mw: Any) -> None:
    from models.account import Account
    a = Account()
    a.name = f"账号{len(mw.accounts)+1}"
    mw.accounts.append(a)
    mw._save()
    _rebuild_list(mw)


def _rebuild_list(mw: Any) -> None:
    layout = mw._list_layout
    # Remove existing rows (keep stretch at end)
    while layout.count() > 1:
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    ft = getattr(mw, "_smart_search", None)
    st = ft.text().strip().lower() if ft and ft.text() else ""
    fk = getattr(mw, "_smart_filter", "")
    mw._list_rows = []

    for a in mw.accounts:
        name = a.get("name", "")
        if st and st not in name.lower():
            continue
        aid = a.get("id", "")
        lq = getattr(mw, "launch_queue", None)
        runner = getattr(mw, "runner", None)
        running = (lq and lq.is_running(aid)) or (runner and runner.is_running(aid))
        queued = lq and lq.is_queued(aid)
        fails = a.get("consecutive_failures", 0)

        if fk == "running" and not running: continue
        if fk == "waiting" and not queued: continue
        if fk == "error" and not (1 <= fails < 6): continue
        if fk == "paused" and fails < 6: continue

        row = _make_row(mw, a, running, queued, fails)
        layout.insertWidget(layout.count() - 1, row)
        mw._list_rows.append(row)

    _update_status(mw)


def _make_row(mw: Any, a: dict, running: bool, queued: bool, fails: int) -> QFrame:
    row = QFrame()
    row.setObjectName("accountRow")
    row.setFixedHeight(34)
    row.setCursor(Qt.PointingHandCursor)

    hl = QHBoxLayout(row)
    hl.setContentsMargins(12, 0, 12, 0)
    hl.setSpacing(0)

    # Checkbox
    cb = QCheckBox()
    cb.setFixedWidth(28)
    cb.setStyleSheet("QCheckBox::indicator{width:14px;height:14px}")
    hl.addWidget(cb)

    # Name
    nm = QLabel(a.get("name", ""))
    nm.setStyleSheet("color:#ccc;font-size:9pt;font-weight:500")
    hl.addWidget(nm, 1)

    # Status
    if running:
        dur = ""
        runner = getattr(mw, "runner", None)
        if runner:
            s = runner._start_times.get(a.get("id", ""), 0)
            if s: dur = f"{int(time.time()-s)//60}m"
        st_lbl = QLabel(f"  ▶{dur}" if dur else "  ▶")
        st_lbl.setStyleSheet("color:#498205;font-size:8pt;font-weight:bold")
    elif queued:
        st_lbl = QLabel("  ⏳")
        st_lbl.setStyleSheet("color:#e8a000;font-size:8pt")
    elif fails >= 6:
        st_lbl = QLabel("  ⏸")
        st_lbl.setStyleSheet("color:#666;font-size:8pt")
    elif fails:
        st_lbl = QLabel(f"  ✕{fails}")
        st_lbl.setStyleSheet("color:#c04040;font-size:8pt")
    else:
        st_lbl = QLabel("")
        st_lbl.setStyleSheet("color:#555;font-size:8pt")
    st_lbl.setFixedWidth(55)
    st_lbl.setAlignment(Qt.AlignCenter)
    hl.addWidget(st_lbl)

    # Stage
    stage = a.get("smart_stage", "")
    sg = QLabel(stage if stage else "—")
    sg.setFixedWidth(70)
    sg.setAlignment(Qt.AlignCenter)
    sg.setStyleSheet("color:#888;font-size:8pt")
    hl.addWidget(sg)

    # Anni
    anni = a.get("smart_annihilation", "")
    ae = a.get("smart_annihilation_enabled", True)
    ad = {"":"—","Annihilation":"当期","Chernobog@Annihilation":"切城",
          "LungmenOutskirts@Annihilation":"外环","LungmenDowntown@Annihilation":"市区"}.get(anni, "")
    an = QLabel(ad if ae else f"✘{ad}" if ad else "—")
    an.setFixedWidth(65)
    an.setAlignment(Qt.AlignCenter)
    an.setStyleSheet("color:#888;font-size:8pt")
    hl.addWidget(an)

    # Days
    for dk in ["mon","tue","wed","thu","fri","sat","sun"]:
        v = a.get(f"smart_{dk}", "")
        dl = QLabel(v if v else "")
        dl.setFixedWidth(42)
        dl.setAlignment(Qt.AlignCenter)
        dl.setStyleSheet("color:#666;font-size:8pt")
        hl.addWidget(dl)

    # Detail button (in 30px container to align with header)
    dw = QWidget()
    dw.setFixedWidth(30)
    dl = QHBoxLayout(dw)
    dl.setContentsMargins(0, 0, 0, 0)
    dl.setAlignment(Qt.AlignCenter)
    dt = QPushButton("✎")
    dt.setFixedSize(20, 20)
    dt.setStyleSheet("QPushButton{background:transparent;color:#555;border:none;border-radius:3px;font-size:8pt}")
    dt.clicked.connect(lambda _, r=mw.accounts.index(a): _open_detail(mw, r))
    dl.addWidget(dt)
    hl.addWidget(dw)

    # Hover effect
    row.mousePressEvent = lambda e, r=mw.accounts.index(a): _open_detail(mw, r)

    return row


def _update_status(mw: Any) -> None:
    pass  # Status is set on rebuild; real-time updates can be added later


def _get_selected(mw: Any) -> list[str]:
    selected = []
    for row_w in mw._list_rows:
        cb = row_w.findChild(QCheckBox)
        if cb and cb.isChecked():
            # Find name label to get account
            for lbl in row_w.findChildren(QLabel):
                txt = lbl.text().strip()
                if txt and not any(c in txt for c in "▶⏳✕⏸—"):
                    for a in mw.accounts:
                        if a.get("name") == txt:
                            selected.append(a.get("id", ""))
                            break
                    break
    return selected


def _open_detail(mw: Any, row: int) -> None:
    from ui.account_detail import open_account_detail
    open_account_detail(mw, row)
    _rebuild_list(mw)
