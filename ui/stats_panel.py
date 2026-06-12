"""Per-account run statistics dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QGroupBox, QWidget,
)

from models.stats import RunStats


def show_stats_dialog(parent: QWidget | None, account_id: str) -> None:
    """Open a modal stats dialog for the given account."""
    d = QDialog(parent)
    d.setWindowTitle("运行统计")
    d.setMinimumSize(500, 420)
    d.resize(520, 460)

    stats = RunStats(account_id)

    l = QVBoxLayout(d)
    l.setContentsMargins(12, 12, 12, 8)
    l.setSpacing(8)

    # ── Header ──
    hdr = QLabel(f"账号统计  —  {account_id}")
    hdr.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
    l.addWidget(hdr)

    # ── 1. Today's summary ──
    daily = stats.get_daily()
    gb1 = QGroupBox("今日总计")
    gb1.setStyleSheet("QGroupBox{font-weight:bold;color:#888;border:1px solid #2b2b30;border-radius:4px;margin-top:8px;padding-top:14px}")
    gb1_layout = QVBoxLayout(gb1)
    gb1_layout.setSpacing(2)

    gb1_layout.addWidget(QLabel(f"运行次数: {daily['runs']}"))
    if daily["drops"]:
        drops_text = "  ".join(f"{item} ×{qty}" for item, qty in daily["drops"].items())
        gb1_layout.addWidget(QLabel(f"掉落物:   {drops_text}"))
    l.addWidget(gb1)

    # ── 2. Last 10 runs table ──
    gb2 = QGroupBox("最近 10 次运行")
    gb2.setStyleSheet("QGroupBox{font-weight:bold;color:#888;border:1px solid #2b2b30;border-radius:4px;margin-top:8px;padding-top:14px}")
    gb2_layout = QVBoxLayout(gb2)
    gb2_layout.setContentsMargins(4, 16, 4, 4)
    gb2_layout.setSpacing(0)

    table = QTableWidget()
    table.setColumnCount(3)
    table.setHorizontalHeaderLabels(["时间", "任务状态", "掉落"])
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.verticalHeader().hide()
    table.setEditTriggers(QTableWidget.NoEditTriggers)

    all_runs = stats._data.get("runs", [])[-10:]
    table.setRowCount(len(all_runs))
    for i, r in enumerate(reversed(all_runs)):
        ts = r.get("ts", "")[5:19]  # MM-DD HH:MM:SS
        table.setItem(i, 0, QTableWidgetItem(ts))

        tasks_str = "  ".join(
            f"{name}:{status}" for name, status in r.get("tasks", {}).items()
        )
        table.setItem(i, 1, QTableWidgetItem(tasks_str))

        drops_str = "  ".join(
            f"{item}×{qty}" for item, qty in r.get("drops", {}).items()
        ) or "—"
        table.setItem(i, 2, QTableWidgetItem(drops_str))

    gb2_layout.addWidget(table)
    l.addWidget(gb2, 1)

    # ── 3. Sanity history ──
    gb3 = QGroupBox("理智历史")
    gb3.setStyleSheet("QGroupBox{font-weight:bold;color:#888;border:1px solid #2b2b30;border-radius:4px;margin-top:8px;padding-top:14px}")
    gb3_layout = QVBoxLayout(gb3)
    gb3_layout.setSpacing(2)

    sanity_snapshots = []
    for r in reversed(stats._data.get("runs", [])):
        if "sanity" in r:
            sanity_snapshots.append(r["sanity"])
            if len(sanity_snapshots) >= 5:
                break

    if sanity_snapshots:
        for s in sanity_snapshots:
            cur = s.get("current", "?")
            mx = s.get("max", "?")
            deficit = s.get("deficit", "?")
            rt = s.get("report_time", "")[5:19] if s.get("report_time") else "—"
            gb3_layout.addWidget(QLabel(f"  {rt}  —  当前 {cur} / 上限 {mx}  (缺口 {deficit})"))
    else:
        gb3_layout.addWidget(QLabel("  暂无数据"))

    l.addWidget(gb3)

    d.exec()
