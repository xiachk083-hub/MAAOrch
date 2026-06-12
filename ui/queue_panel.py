"""Detailed queue view dialog."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QGroupBox, QWidget,
)


def open_queue_dialog(parent: QWidget | None, launch_queue: Any, accounts: list[dict]) -> None:
    d = QDialog(parent)
    d.setWindowTitle("队列详情")
    d.setMinimumSize(500, 420)
    d.resize(520, 460)

    l = QVBoxLayout(d)
    l.setContentsMargins(12, 12, 12, 8)
    l.setSpacing(8)

    # ── Header ──
    hdr = QLabel("启动队列")
    hdr.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
    l.addWidget(hdr)

    # ── 1. 队列状态 ──
    status_text = "已暂停" if launch_queue.is_paused else "运行中"
    total = launch_queue.pending_count + launch_queue.active_count
    gb1 = QGroupBox(f"队列状态  —  {status_text}    排队 {launch_queue.pending_count}  /  运行 {launch_queue.active_count}  /  总计 {total}")
    gb1.setStyleSheet("QGroupBox{font-weight:bold;color:#888;border:1px solid #2b2b30;border-radius:4px;margin-top:8px;padding-top:14px}")
    gb1_layout = QVBoxLayout(gb1)
    gb1_layout.setSpacing(2)

    status_lbl = QLabel(f"{'⏸' if launch_queue.is_paused else '▶'} 队列{'已暂停' if launch_queue.is_paused else '运行中'}  |  排队 {launch_queue.pending_count}  运行 {launch_queue.active_count}  总计 {total}")
    gb1_layout.addWidget(status_lbl)
    l.addWidget(gb1)

    # ── 2. 排队列表 ──
    gb2 = QGroupBox("排队列表")
    gb2.setStyleSheet("QGroupBox{font-weight:bold;color:#888;border:1px solid #2b2b30;border-radius:4px;margin-top:8px;padding-top:14px}")
    gb2_layout = QVBoxLayout(gb2)
    gb2_layout.setContentsMargins(4, 16, 4, 4)
    gb2_layout.setSpacing(0)

    table = QTableWidget()
    table.setColumnCount(5)
    table.setHorizontalHeaderLabels(["序号", "账号名", "来源", "预计时间", "优先级"])
    table.horizontalHeader().setStretchLastSection(False)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.verticalHeader().hide()
    table.setEditTriggers(QTableWidget.NoEditTriggers)

    src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智", "retry": "重试", "force": "强制", "saved": "恢复"}
    pending = sorted(launch_queue._pending, key=lambda e: e.sort_key)
    table.setRowCount(len(pending))

    for i, entry in enumerate(pending):
        table.setItem(i, 0, QTableWidgetItem(str(i + 1)))

        ac = next((a for a in accounts if a["id"] == entry.account_id), None)
        name = ac.get("name", entry.account_id[:8]) if ac else entry.account_id[:8]
        table.setItem(i, 1, QTableWidgetItem(name))

        table.setItem(i, 2, QTableWidgetItem(src_map.get(entry.source, entry.source)))

        nb_str = entry.not_before.strftime("%H:%M") if entry.not_before > datetime.now() else "立即"
        table.setItem(i, 3, QTableWidgetItem(nb_str))

        table.setItem(i, 4, QTableWidgetItem(str(entry.sort_key[0])))

    gb2_layout.addWidget(table)
    l.addWidget(gb2, 1)

    # ── 3. 运行中 ──
    gb3 = QGroupBox("运行中")
    gb3.setStyleSheet("QGroupBox{font-weight:bold;color:#888;border:1px solid #2b2b30;border-radius:4px;margin-top:8px;padding-top:14px}")
    gb3_layout = QVBoxLayout(gb3)
    gb3_layout.setSpacing(2)

    if launch_queue._active_emus:
        for emu_idx, aid in launch_queue._active_emus.items():
            ac = next((a for a in accounts if a["id"] == aid), None)
            name = ac.get("name", aid[:8]) if ac else aid[:8]
            gb3_layout.addWidget(QLabel(f"  🖥 模拟器 {emu_idx}  →  {name}"))
    else:
        gb3_layout.addWidget(QLabel("  无"))

    l.addWidget(gb3)

    d.exec()
