"""Config cards grid — quick browse, search, tag filter, edit, enqueue."""
from __future__ import annotations
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QFrame, QGridLayout,
)
from dialogs import AccountDialog

# Tag colors (cycling)
TAG_COLORS = [
    ("#1976d2", "#e3f2fd"), ("#388e3c", "#e8f5e9"), ("#f57c00", "#fff3e0"),
    ("#7b1fa2", "#f3e5f5"), ("#c62828", "#ffebee"), ("#00838f", "#e0f7fa"),
    ("#5d4037", "#efebe9"), ("#4527a0", "#ede7f6"),
]


def _tag_color(tag: str) -> tuple[str, str]:
    i = hash(tag) % len(TAG_COLORS)
    return TAG_COLORS[i]


def build_config_cards(mw: Any) -> QWidget:
    """Build the config cards grid view."""
    mw.cv = QWidget()
    cvl = QVBoxLayout(mw.cv)
    cvl.setContentsMargins(6, 6, 6, 6)
    cvl.setSpacing(4)

    # Search + tag filter row
    top_row = QHBoxLayout()
    mw._card_search = QLineEdit()
    mw._card_search.setPlaceholderText("搜索名称/标签...")
    mw._card_search.setClearButtonEnabled(True)
    mw._card_search.textChanged.connect(lambda t: _rebuild_cards(mw))
    top_row.addWidget(mw._card_search, 1)
    cvl.addLayout(top_row)

    # Tag quick filter row
    mw._card_tag_row = QHBoxLayout()
    mw._card_tag_row.setSpacing(4)
    mw._card_tag_row.addWidget(QLabel("标签:"))
    cvl.addLayout(mw._card_tag_row)

    # Scrollable card grid
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    mw._card_container = QWidget()
    mw._card_grid = QGridLayout(mw._card_container)
    mw._card_grid.setSpacing(6)
    mw._card_grid.setContentsMargins(0, 0, 0, 0)
    scroll.setWidget(mw._card_container)
    cvl.addWidget(scroll, 1)

    _rebuild_cards(mw)
    return mw.cv


def refresh_config_cards(mw: Any) -> None:
    """Refresh card grid (called when accounts change)."""
    _rebuild_cards(mw)


def _rebuild_cards(mw: Any) -> None:
    """Rebuild all cards based on current search filter."""
    if not hasattr(mw, "_card_grid"):
        return

    # Clear existing
    grid = mw._card_grid
    while grid.count():
        item = grid.takeAt(0)
        if item.widget():
            item.widget().setParent(None)

    search = mw._card_search.text().strip().lower() if hasattr(mw, "_card_search") else ""
    active_tag = getattr(mw, "_card_active_tag", "")

    # Rebuild tag filter row
    _rebuild_tag_row(mw)

    # Compute columns: 5-6 based on container width
    col_count = 5
    row = 0
    col = 0
    for i, a in enumerate(mw.accounts):
        name = a.get("name", "").lower()
        tags = a.get("tags", "").lower()
        if search and search not in name and search not in tags:
            continue
        if active_tag and active_tag not in tags:
            continue
        frame = _make_card(mw, a, i)
        grid.addWidget(frame, row, col)
        col += 1
        if col >= col_count:
            col = 0
            row += 1


def _rebuild_tag_row(mw: Any) -> None:
    """Rebuild the quick tag filter buttons."""
    row = mw._card_tag_row
    # Remove old tag buttons (keep the "标签:" label)
    while row.count() > 1:
        item = row.takeAt(1)
        if item.widget():
            item.widget().setParent(None)

    all_tags = set()
    for a in mw.accounts:
        for t in a.get("tags", "").split(","):
            t = t.strip()
            if t:
                all_tags.add(t)

    active = getattr(mw, "_card_active_tag", "")
    all_btn = QPushButton("全部")
    all_btn.setFlat(True)
    all_btn.setStyleSheet("QPushButton{color:#888;border:none;padding:1px 6px;font-size:9pt}QPushButton:hover{color:#fff}" if active else "QPushButton{color:#fff;background:#555;border-radius:4px;padding:1px 6px;font-size:9pt}")
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
    _rebuild_cards(mw)


def _make_card(mw: Any, a: dict, idx: int) -> QFrame:
    """Add a single card for an account."""
    frame = QFrame()
    frame.setObjectName("configCard")
    frame.setFixedWidth(175)
    frame.setStyleSheet("QFrame#configCard{border:1px solid #444;border-radius:6px;padding:8px;background:rgba(255,255,255,0.03)}QFrame#configCard:hover{border-color:#666;background:rgba(255,255,255,0.05)}")

    fl = QVBoxLayout(frame)
    fl.setContentsMargins(6, 6, 6, 6)
    fl.setSpacing(2)

    # Name
    name_lbl = QLabel(a.get("name", ""))
    name_lbl.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
    fl.addWidget(name_lbl)

    # Client
    client_map = {"Official": "官服", "Bilibili": "B服", "YoStarEN": "国际服", "YoStarJP": "日服", "YoStarKR": "韩服", "txwy": "繁中"}
    fl.addWidget(QLabel(client_map.get(a.get("game_client", ""), a.get("game_client", ""))))
    fl.addSpacing(2)

    # ADB
    adb = a.get("adb_address", "")
    if adb:
        fl.addWidget(QLabel(f"📱 {adb}"))
    else:
        lbl = QLabel("⚠ 未配置 ADB")
        lbl.setStyleSheet("color:#a88")
        fl.addWidget(lbl)

    # Emulator
    emu = a.get("emu_instance_index", "")
    ename = a.get("emu_instance_name", "")
    if emu:
        fl.addWidget(QLabel(f"🖥 {ename or ('MuMu #'+emu)}"))
    else:
        lbl = QLabel("⚠ 未配置模拟器")
        lbl.setStyleSheet("color:#a88")
        fl.addWidget(lbl)

    # Pipeline
    progs = [w for w in mw.warehouse if w.get("account_ref") == a["id"]]
    pipe = progs[0].get("task_pipeline", "") if progs else ""
    if pipe:
        tasks = [t.strip() for t in pipe.split(",") if t.strip()][:4]
        fl.addWidget(QLabel(f"⚙ {'·'.join(tasks)}"))
    else:
        fl.addWidget(QLabel("⚙ 无"))

    # Sanity-driven
    if a.get("sanity_driven"):
        fl.addWidget(QLabel("💊 理智驱动"))

    # Tags
    tags = a.get("tags", "")
    if tags:
        tag_row = QHBoxLayout()
        tag_row.setSpacing(2)
        for t in tags.split(","):
            t = t.strip()
            if not t:
                continue
            bg, fg = _tag_color(t)
            tl = QLabel(t)
            tl.setStyleSheet(f"background:{bg};color:#fff;border-radius:3px;padding:0 4px;font-size:8pt")
            tag_row.addWidget(tl)
        tag_row.addStretch()
        fl.addLayout(tag_row)

    fl.addSpacing(4)

    # Buttons
    btn_row = QHBoxLayout()
    btn_row.setSpacing(4)
    edit_btn = QPushButton("✏️")
    edit_btn.setFixedSize(28, 24)
    edit_btn.setToolTip("编辑")
    edit_btn.clicked.connect(lambda c, i=idx: _edit_account(mw, i))
    btn_row.addWidget(edit_btn)

    launch_btn = QPushButton("▶")
    launch_btn.setFixedSize(28, 24)
    launch_btn.setToolTip("加入队列")
    launch_btn.setStyleSheet("QPushButton{background:#2b7a3a;color:#fff;border:none;border-radius:3px}QPushButton:hover{background:#1e5a28}")
    launch_btn.clicked.connect(lambda c, i=idx: _enqueue_card(mw, i))
    btn_row.addWidget(launch_btn)
    btn_row.addStretch()
    fl.addLayout(btn_row)

    return frame


def _edit_account(mw: Any, idx: int) -> None:
    """Open AccountDialog to edit an account."""
    if idx < 0 or idx >= len(mw.accounts):
        return
    a = mw.accounts[idx]
    d = AccountDialog(mw, a)
    if d.exec() == 1:  # QDialog.Accepted
        mw.accounts[idx].update(d.r)
        mw._save()
        mw._ra()
        _rebuild_cards(mw)


def _enqueue_card(mw: Any, idx: int) -> None:
    """Enqueue an account from card."""
    if idx < 0 or idx >= len(mw.accounts):
        return
    aid = mw.accounts[idx]["id"]
    mw.launch_queue.enqueue(aid, "manual", priority=0)
    mw.launch_queue._tick()
