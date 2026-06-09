"""Smart scheduling panel — modern account list (no table widget)."""
from __future__ import annotations
import time
from typing import Any
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QScrollArea, QLineEdit, QFrame, QSizePolicy, QMenu,
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
    # Wrap search + button in a container for perfect alignment
    search_frame = QFrame()
    search_frame.setFixedHeight(32)
    search_frame.setStyleSheet("QFrame{background:#222;border:1px solid #333;border-radius:5px}")
    sf = QHBoxLayout(search_frame)
    sf.setContentsMargins(8, 0, 0, 0)
    sf.setSpacing(0)

    search_icon = QLabel("🔍")
    search_icon.setStyleSheet("color:#555;font-size:9pt;background:transparent;border:none")
    sf.addWidget(search_icon)

    mw._smart_search = QLineEdit()
    mw._smart_search.setPlaceholderText("搜索账号...")
    mw._smart_search.setFixedHeight(30)
    mw._smart_search.setStyleSheet("QLineEdit{background:transparent;color:#ddd;border:none;padding:0 6px;font-size:9pt}")
    mw._smart_search.textChanged.connect(lambda: QTimer.singleShot(0, lambda: _rebuild_list(mw)))
    sf.addWidget(mw._smart_search, 1)

    add_btn = QPushButton("＋添加")
    add_btn.setFixedHeight(30)
    add_btn.setStyleSheet("QPushButton{background:#498205;color:#fff;border:none;border-radius:0 5px 5px 0;padding:0 14px;font-size:9pt;font-weight:bold}")
    add_btn.clicked.connect(lambda: QTimer.singleShot(0, lambda: _add_account(mw)))
    sf.addWidget(add_btn)

    sr.addWidget(search_frame, 1)
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

    mw._smart_filter = ""
    mw._list_rows = []
    mw._set_smart_filter = lambda k: _set_filter(mw, k)
    _rebuild_list(mw)

    timer = QTimer(mw.smart_v)
    timer.timeout.connect(lambda: (_update_status(mw), _update_batch_buttons(mw)))
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
    try:
        st = ft.text().strip().lower() if ft and ft.text() else ""
    except RuntimeError:
        st = ""
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
    _update_batch_buttons(mw)


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
    cb.toggled.connect(lambda _=None: _update_batch_buttons(mw))
    hl.addWidget(cb)

    # Name
    nm = QLabel(a.get("name", ""))
    nm.setObjectName("accountName")
    nm.setStyleSheet("color:#ccc;font-size:9pt;font-weight:500")
    hl.addWidget(nm, 1)
    # Store account id on the row for reliable lookup
    row._account_id = a.get("id", "")

    # Status
    st_lbl = QLabel("")
    st_lbl.setObjectName("statusLabel")
    st_lbl.setFixedWidth(55)
    st_lbl.setAlignment(Qt.AlignCenter)
    _set_status_text(st_lbl, running, queued, fails, mw, a.get("id", ""))
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

    # Left click → detail, right click → menu
    row.mousePressEvent = lambda e, r=mw.accounts.index(a): _open_detail(mw, r)
    row.setContextMenuPolicy(Qt.CustomContextMenu)
    row.customContextMenuRequested.connect(lambda pos, r=mw.accounts.index(a): _show_row_menu(mw, r, row.mapToGlobal(pos)))

    return row


def _set_status_text(lbl: QLabel, running: bool, queued: bool, fails: int, mw: Any, aid: str) -> None:
    if running:
        dur = ""
        rnr = getattr(mw, "runner", None)
        if rnr:
            s = rnr._start_times.get(aid, 0)
            if s: dur = f"{int(time.time()-s)//60}m"
        lbl.setText(f"  ▶{dur}" if dur else "  ▶")
        lbl.setStyleSheet("color:#498205;font-size:8pt;font-weight:bold")
    elif queued:
        lbl.setText("  ⏳")
        lbl.setStyleSheet("color:#e8a000;font-size:8pt")
    elif fails >= 6:
        lbl.setText("  ⏸")
        lbl.setStyleSheet("color:#666;font-size:8pt")
    elif fails:
        lbl.setText(f"  ✕{fails}")
        lbl.setStyleSheet("color:#c04040;font-size:8pt")
    else:
        lbl.setText("")
        lbl.setStyleSheet("color:#555;font-size:8pt")


def _update_status(mw: Any) -> None:
    """Update status labels for all visible rows in real-time."""
    for row_w in mw._list_rows:
        st = row_w.findChild(QLabel, "statusLabel")
        nm = row_w.findChild(QLabel, "accountName")
        if not st or not nm:
            continue
        txt = nm.text().strip()
        # Find matching account
        for a in mw.accounts:
            if a.get("name") == txt:
                aid = a.get("id", "")
                lq = getattr(mw, "launch_queue", None)
                rnr = getattr(mw, "runner", None)
                running = (lq and lq.is_running(aid)) or (rnr and rnr.is_running(aid))
                queued = lq and lq.is_queued(aid)
                fails = a.get("consecutive_failures", 0)
                _set_status_text(st, running, queued, fails, mw, aid)
                break


def _update_batch_buttons(mw: Any) -> None:
    """Update batch button states based on current selections."""
    if not hasattr(mw, '_list_rows'):
        return
    selected = _get_selected(mw)
    n = len(selected)
    for act in ("edit", "enq", "stop", "del"):
        btn = getattr(mw, f"_batch_{act}_btn", None)
        if btn:
            btn.setEnabled(n > 0)
    stat = getattr(mw, "_batch_stat", None)
    if stat:
        stat.setText(f"(已选 {n}/{len(mw.accounts)})" if n else "")


def _get_selected(mw: Any) -> list[str]:
    if not hasattr(mw, '_list_rows'):
        return []
    selected = []
    for row_w in mw._list_rows:
        try:
            cbs = [c for c in row_w.children() if isinstance(c, QCheckBox)]
            if cbs and cbs[0].isChecked():
                aid = getattr(row_w, "_account_id", "")
                if aid:
                    selected.append(aid)
        except RuntimeError:
            continue
    return selected


def _do_batch(mw: Any, action: str) -> None:
    """Batch action handler for main_window bottom bar."""
    selected = _get_selected(mw)
    if not selected:
        return
    lq = getattr(mw, "launch_queue", None)
    runner = getattr(mw, "runner", None)
    if action == "enq" and lq:
        for aid in selected:
            lq.enqueue(aid, "manual")
        lq.tick()
    elif action == "stop" and runner:
        for aid in selected:
            runner.stop(aid)
    elif action == "del":
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(mw, "确认", f"删除 {len(selected)} 个?") == QMessageBox.Yes:
            mw.accounts[:] = [a for a in mw.accounts if a.get("id") not in selected]
            mw._save()
    elif action == "edit":
        from ui.batch_edit import open_batch_edit
        open_batch_edit(mw, selected)
    _rebuild_list(mw)


def _show_row_menu(mw: Any, row: int, pos) -> None:
    """Right-click menu for account row."""
    menu = QMenu()
    edit_a = menu.addAction("✎ 编辑")
    del_a = menu.addAction("🗑 删除")
    action = menu.exec(pos)
    if action == edit_a:
        _open_detail(mw, row)
    elif action == del_a:
        from PySide6.QtWidgets import QMessageBox
        a = mw.accounts[row]
        if QMessageBox.question(mw, "确认", f"删除账号「{a.get('name','')}」?") == QMessageBox.Yes:
            mw.accounts.pop(row)
            mw._save()
            _rebuild_list(mw)


def _open_detail(mw: Any, row: int) -> None:
    from ui.account_detail import open_account_detail
    open_account_detail(mw, row)
    _rebuild_list(mw)
