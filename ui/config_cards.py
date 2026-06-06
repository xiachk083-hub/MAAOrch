"""Config cards grid — responsive columns, search, tag filter, run status, edit, enqueue."""
from __future__ import annotations
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QFrame, QGridLayout, QSizePolicy,
)
from dialogs import AccountDialog

# Tag colors (cycling)
TAG_COLORS = [
    ("#1976d2", "#e3f2fd"), ("#388e3c", "#e8f5e9"), ("#f57c00", "#fff3e0"),
    ("#7b1fa2", "#f3e5f5"), ("#c62828", "#ffebee"), ("#00838f", "#e0f7fa"),
    ("#5d4037", "#efebe9"), ("#4527a0", "#ede7f6"),
]


def _tag_color(tag: str) -> tuple[str, str]:
    return TAG_COLORS[hash(tag) % len(TAG_COLORS)]


def _card_cols(mw: Any) -> int:
    w = mw.cv.width() - 12  # padding
    if w < 400: return 2
    if w < 600: return 3
    if w < 800: return 4
    if w < 1000: return 5
    return 6


def _card_width(mw: Any) -> int:
    cols = max(1, _card_cols(mw))
    return (mw.cv.width() - 12 - (cols - 1) * 6) // cols


def build_config_cards(mw: Any) -> QWidget:
    """Build responsive config cards grid."""
    mw.cv = QWidget()
    cvl = QVBoxLayout(mw.cv)
    cvl.setContentsMargins(6, 6, 6, 6)
    cvl.setSpacing(2)

    # Search + tag row
    top = QHBoxLayout()
    mw._card_search = QLineEdit()
    mw._card_search.setPlaceholderText("搜索名称/标签...")
    mw._card_search.setClearButtonEnabled(True)
    mw._card_search.textChanged.connect(lambda: _rebuild(mw))
    top.addWidget(mw._card_search, 1)
    count_lbl = QLabel()
    mw._card_count = count_lbl
    mw._card_count.setStyleSheet("color:#888;font-size:9pt")
    top.addWidget(count_lbl)
    cvl.addLayout(top)

    # Tag filter bar
    mw._card_tag_row = QHBoxLayout()
    mw._card_tag_row.setSpacing(3)
    mw._card_tag_row.addWidget(QLabel(""))
    cvl.addLayout(mw._card_tag_row)

    # Card grid
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    mw._card_container = QWidget()
    mw._card_grid = QGridLayout(mw._card_container)
    mw._card_grid.setSpacing(6)
    mw._card_grid.setContentsMargins(0, 0, 0, 0)
    mw._card_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    scroll.setWidget(mw._card_container)
    cvl.addWidget(scroll, 1)

    _rebuild(mw)
    return mw.cv


def refresh_config_cards(mw: Any) -> None:
    _rebuild(mw)


def _rebuild(mw: Any) -> None:
    if not hasattr(mw, "_card_grid"):
        return
    grid = mw._card_grid
    while grid.count():
        w = grid.takeAt(0).widget()
        if w: w.setParent(None)

    search = mw._card_search.text().strip().lower() if hasattr(mw, "_card_search") else ""
    active_tag = getattr(mw, "_card_active_tag", "")
    _rebuild_tag_row(mw)

    col_count = _card_cols(mw)
    cw = max(150, _card_width(mw))
    row, col, shown = 0, 0, 0

    running_ids = set()
    queued_ids = set()
    if hasattr(mw, "launch_queue"):
        running_ids = set(mw.launch_queue._active_emus.values())
        queued_ids = set(e.account_id for e in mw.launch_queue._pending)

    for i, a in enumerate(mw.accounts):
        name = a.get("name", "").lower()
        tags = a.get("tags", "").lower()
        if search and search not in name and search not in tags:
            continue
        if active_tag and active_tag not in tags:
            continue
        is_running = a["id"] in running_ids
        is_queued = a["id"] in queued_ids
        frame = _make_card(mw, a, i, cw, is_running, is_queued)
        grid.addWidget(frame, row, col, Qt.AlignTop)
        col += 1
        if col >= col_count:
            col = 0; row += 1
        shown += 1

    if hasattr(mw, "_card_count"):
        total = len(mw.accounts)
        mw._card_count.setText(f"{shown}/{total}" if shown < total else f"共 {total} 个")


def _rebuild_tag_row(mw: Any) -> None:
    row = mw._card_tag_row
    while row.count() > 1:
        w = row.takeAt(1).widget()
        if w: w.setParent(None)

    all_tags: set[str] = set()
    for a in mw.accounts:
        for t in a.get("tags", "").split(","):
            t = t.strip()
            if t: all_tags.add(t)

    if not all_tags:
        row.itemAt(0).widget().setText("")
        return
    row.itemAt(0).widget().setText("标签:")

    active = getattr(mw, "_card_active_tag", "")
    all_btn = QPushButton("全部")
    all_btn.setFlat(True)
    base = "QPushButton{padding:1px 6px;font-size:9pt}"
    all_btn.setStyleSheet(f"{base}QPushButton{{color:#fff;background:#555;border-radius:4px}}" if not active else f"{base}QPushButton{{color:#888;border:none}}QPushButton:hover{{color:#fff}}")
    all_btn.clicked.connect(lambda: _filter_tag(mw, ""))
    row.addWidget(all_btn)

    for tag in sorted(all_tags):
        bg, _ = _tag_color(tag)
        btn = QPushButton(tag)
        btn.setFlat(True)
        if tag == active:
            btn.setStyleSheet(f"QPushButton{{color:#fff;background:{bg};border-radius:4px;padding:1px 6px;font-size:9pt}}")
        else:
            btn.setStyleSheet(f"QPushButton{{color:{bg};border:1px solid {bg};border-radius:4px;padding:1px 6px;font-size:9pt}}QPushButton:hover{{background:{bg};color:#fff}}")
        btn.clicked.connect(lambda c, t=tag: _filter_tag(mw, t))
        row.addWidget(btn)


def _filter_tag(mw: Any, tag: str) -> None:
    mw._card_active_tag = tag
    _rebuild(mw)


def _make_card(mw: Any, a: dict, idx: int, width: int, running: bool, queued: bool) -> QFrame:
    frame = QFrame()
    frame.setObjectName("configCard")
    frame.setFixedWidth(width)
    frame.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)

    # Border color for run status
    accent = "#4a4" if running else ("#c90" if queued else "#444")
    frame.setStyleSheet(f"""
        QFrame#configCard{{
            border:2px solid {accent};
            border-radius:8px;
            background:rgba(255,255,255,0.04);
        }}
        QFrame#configCard:hover{{
            border-color:#888;
            background:rgba(255,255,255,0.07);
        }}
    """)

    fl = QVBoxLayout(frame)
    fl.setContentsMargins(8, 6, 8, 6)
    fl.setSpacing(2)

    # ── Name row ──
    name_row = QHBoxLayout()
    name_row.setSpacing(5)
    name_lbl = QLabel(a.get("name", ""))
    name_lbl.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
    name_lbl.setStyleSheet("color:#ddd")
    name_row.addWidget(name_lbl, 1)
    fl.addLayout(name_row)

    # ── Meta line: client · ADB ──
    client_map = {"Official":"官服","Bilibili":"B服","YoStarEN":"国际服","YoStarJP":"日服","YoStarKR":"韩服","txwy":"繁中"}
    fl.addWidget(QLabel(client_map.get(a.get("game_client",""), a.get("game_client",""))))

    # ── ADB ──
    adb = a.get("adb_address", "")
    adb_lbl = QLabel(f"📱 {adb}" if adb else "⚠ 未配置ADB")
    adb_lbl.setStyleSheet("color:#999;font-size:8pt")
    fl.addWidget(adb_lbl)

    # ── Emulator ──
    emu = a.get("emu_instance_index", "")
    if emu:
        ename = a.get("emu_instance_name", emu)
        emu_lbl = QLabel(f"🖥 {ename}")
        emu_lbl.setStyleSheet("color:#999;font-size:8pt")
        fl.addWidget(emu_lbl)
    elif a.get("emu_launch"):
        emu_lbl = QLabel("🖥 自启")
        emu_lbl.setStyleSheet("color:#999;font-size:8pt")
        fl.addWidget(emu_lbl)

    # ── Pipeline ──
    progs = [w for w in mw.warehouse if w.get("account_ref") == a["id"]]
    pipe = progs[0].get("task_pipeline", "") if progs else ""
    if pipe:
        name_map = {"startup":"唤醒","fight":"刷关","recruit":"公招","infrast":"基建","mall":"信用","award":"奖励","roguelike":"肉鸽","reclamation":"生息","closedown":"关闭"}
        tasks = [name_map.get(t.strip().lower(), t.strip()) for t in pipe.split(",") if t.strip()][:4]
        pipe_lbl = QLabel(f"⚙ {','.join(tasks)}")
        pipe_lbl.setStyleSheet("color:#aaa;font-size:8pt")
        fl.addWidget(pipe_lbl)

    # ── Badges row: sanity + tags ──
    has_badges = bool(tags := a.get("tags", "")) or a.get("sanity_driven")
    if has_badges:
        badge_row = QHBoxLayout()
        badge_row.setSpacing(3)
        if a.get("sanity_driven"):
            s_badge = QLabel("💊")
            s_badge.setToolTip("理智驱动已启用")
            s_badge.setStyleSheet("font-size:8pt")
            badge_row.addWidget(s_badge)
        for t in tags.split(","):
            t = t.strip()
            if not t: continue
            bg, _ = _tag_color(t)
            tl = QLabel(t)
            tl.setStyleSheet(f"background:{bg};color:#fff;border-radius:3px;padding:1px 5px;font-size:7pt")
            badge_row.addWidget(tl)
        badge_row.addStretch()
        fl.addLayout(badge_row)

    # ── Buttons ──
    btn_row = QHBoxLayout()
    btn_row.setSpacing(2)
    btn_row.addStretch()
    edit_btn = QPushButton("  ✏️  ")
    edit_btn.setToolTip("编辑")
    edit_btn.setStyleSheet("QPushButton{background:transparent;color:#888;border:none;font-size:9pt;padding:2px 6px}QPushButton:hover{color:#fff;background:rgba(255,255,255,0.1);border-radius:4px}")
    edit_btn.clicked.connect(lambda c, i=idx: _edit(mw, i))
    btn_row.addWidget(edit_btn)

    launch_btn = QPushButton("  ▶ 入队  ")
    launch_btn.setToolTip("加入队列")
    launch_btn.setStyleSheet("QPushButton{background:#2b7a3a;color:#fff;border:none;border-radius:5px;font-size:9pt;padding:2px 8px}QPushButton:hover{background:#1e5a28}")
    launch_btn.clicked.connect(lambda c, i=idx: _enqueue(mw, i))
    btn_row.addWidget(launch_btn)
    btn_row.addStretch()
    fl.addLayout(btn_row)

    return frame


def _edit(mw: Any, idx: int) -> None:
    if idx < 0 or idx >= len(mw.accounts):
        return
    a = mw.accounts[idx]
    d = AccountDialog(mw, a)
    if d.exec() == 1:
        mw.accounts[idx].update(d.r)
        mw._save()
        mw._ra()
        _rebuild(mw)


def _enqueue(mw: Any, idx: int) -> None:
    if idx < 0 or idx >= len(mw.accounts):
        return
    mw.launch_queue.enqueue(mw.accounts[idx]["id"], "manual", priority=0)
    mw.launch_queue._tick()
