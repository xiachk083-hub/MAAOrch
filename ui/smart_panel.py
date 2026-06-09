"""Smart scheduling panel — main account table."""
from __future__ import annotations
import time
from datetime import datetime
from typing import Any
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFrame, QLineEdit,
)
from services.smart_scheduler import MATERIAL_STAGES, _arknights_now


def build_smart_panel(mw: Any) -> QWidget:
    mw.smart_v = QWidget()
    vl = QVBoxLayout(mw.smart_v)
    vl.setContentsMargins(0, 4, 0, 0)
    vl.setSpacing(3)

    # ── Search bar ──
    search_row = QHBoxLayout()
    search_row.setContentsMargins(0, 0, 0, 0)
    search_row.setSpacing(6)
    mw._smart_search = QLineEdit()
    mw._smart_search.setPlaceholderText("搜索账号...")
    mw._smart_search.textChanged.connect(lambda: _rebuild_smart_table(mw))
    search_row.addWidget(mw._smart_search, 1)
    add_btn = QPushButton("+ 添加")
    add_btn.setObjectName("startBtn")
    add_btn.setFixedHeight(28)
    add_btn.clicked.connect(lambda: _add_account(mw))
    search_row.addWidget(add_btn)
    vl.addLayout(search_row)

    # ── Account table ──
    DAYS = ["一", "二", "三", "四", "五", "六", "日"]
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    cols = 6 + len(DAYS)
    tbl = QTableWidget(0, cols)
    tbl.setHorizontalHeaderLabels(["☐", "账号", "状态", "默认", "剿灭"] + DAYS + [""])
    tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    tbl.setColumnWidth(0, 28)
    tbl.setColumnWidth(2, 55)
    tbl.setColumnWidth(3, 65)
    tbl.setColumnWidth(4, 65)
    for i in range(len(DAYS)):
        tbl.setColumnWidth(5 + i, 46)
    tbl.setColumnWidth(5 + len(DAYS), 28)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QAbstractItemView.DoubleClicked)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(28)
    tbl.setSortingEnabled(True)
    tbl.itemChanged.connect(lambda item: _on_cell_edit(mw, item))
    mw._smart_tbl = tbl
    vl.addWidget(tbl, 1)

    # ── Batch bar ──
    batch_row = QHBoxLayout()
    batch_row.setContentsMargins(0, 4, 0, 0)
    batch_row.setSpacing(6)
    for name, attr, handler in [("批量设置", "_batch_edit_btn", _do_batch_edit),
                                 ("批量入队", "_batch_enqueue_btn", _do_batch_enqueue),
                                 ("批量停止", "_batch_stop_btn", _do_batch_stop),
                                 ("批量删除", "_batch_delete_btn", _do_batch_delete)]:
        btn = QPushButton(name)
        btn.setEnabled(False)
        btn.clicked.connect(lambda _, m=mw, h=handler: h(m))
        setattr(mw, attr, btn)
        batch_row.addWidget(btn)
    mw._batch_status = QLabel("")
    mw._batch_status.setStyleSheet("color:#555;font-size:8pt")
    batch_row.addWidget(mw._batch_status)
    batch_row.addStretch()
    vl.addLayout(batch_row)

    # Filter state
    mw._smart_filter = ""

    # Initial render
    _rebuild_smart_table(mw)

    # Auto-refresh timer
    mw._smart_refresh_timer = QTimer(mw.smart_v)
    mw._smart_refresh_timer.timeout.connect(lambda: _update_status_column(mw))
    mw._smart_refresh_timer.start(3000)

    # Expose filter setter for sidebar
    mw._set_smart_filter = lambda key: _set_filter(mw, key)

    return mw.smart_v


def _set_filter(mw: Any, key: str) -> None:
    if mw._smart_filter == key:
        mw._smart_filter = ""
    else:
        mw._smart_filter = key
    _rebuild_smart_table(mw)


def _add_account(mw: Any) -> None:
    from models.account import Account
    a = Account()
    a.name = f"账号{len(mw.accounts) + 1}"
    mw.accounts.append(a)
    mw._save()
    _rebuild_smart_table(mw)
    mw._log(f"已添加账号: {a.name}")


def _on_cell_edit(mw: Any, item: QTableWidgetItem) -> None:
    row = item.row()
    col = item.column()
    if row >= len(mw.accounts):
        return
    a = mw.accounts[row]
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    text = item.text().strip()
    if col == 1:
        a["name"] = text
    elif col == 3:
        a["smart_stage"] = text
    elif 5 <= col < 5 + len(DAY_KEYS):
        dk = DAY_KEYS[col - 5]
        a[f"smart_{dk}"] = text
    else:
        return
    mw._save()


def _do_batch_edit(mw: Any) -> None:
    selected = _get_selected(mw)
    if not selected:
        return
    from ui.batch_edit import open_batch_edit
    open_batch_edit(mw, selected)
    _rebuild_smart_table(mw)


def _do_batch_enqueue(mw: Any) -> None:
    selected = _get_selected(mw)
    if not selected:
        return
    lq = getattr(mw, "launch_queue", None)
    if lq:
        for aid in selected:
            lq.enqueue(aid, "manual", priority=0)
        lq.tick()
        mw._log(f"批量入队: {len(selected)} 个")


def _do_batch_stop(mw: Any) -> None:
    selected = _get_selected(mw)
    if not selected:
        return
    if hasattr(mw, "runner") and mw.runner:
        for aid in selected:
            mw.runner.stop(aid)
    mw._log(f"批量停止: {len(selected)} 个")


def _do_batch_delete(mw: Any) -> None:
    selected = _get_selected(mw)
    if not selected:
        return
    from PySide6.QtWidgets import QMessageBox
    if QMessageBox.question(mw, "确认", f"删除 {len(selected)} 个账号?") != QMessageBox.Yes:
        return
    mw.accounts[:] = [a for a in mw.accounts if a.get("id", "") not in selected]
    mw._save()
    _rebuild_smart_table(mw)
    mw._log(f"已删除 {len(selected)} 个账号")


def _get_selected(mw: Any) -> list[str]:
    selected = []
    tbl = mw._smart_tbl
    for row in range(tbl.rowCount()):
        cb = tbl.cellWidget(row, 0)
        if cb and cb.isChecked():
            if row < len(mw.accounts):
                selected.append(mw.accounts[row].get("id", ""))
    return selected


def _update_batch_buttons(mw: Any) -> None:
    selected = _get_selected(mw)
    n = len(selected)
    for btn in [mw._batch_edit_btn, mw._batch_enqueue_btn,
                mw._batch_stop_btn, mw._batch_delete_btn]:
        btn.setEnabled(n > 0)
    mw._batch_status.setText(f"(已选 {n}/{len(mw.accounts)})" if n else "")


def _update_status_column(mw: Any) -> None:
    """Refresh the status column for all accounts."""
    tbl = mw._smart_tbl
    if not tbl or not hasattr(mw, "launch_queue"):
        return
    lq = mw.launch_queue
    runner = getattr(mw, "runner", None)

    for row in range(tbl.rowCount()):
        if row >= len(mw.accounts):
            continue
        a = mw.accounts[row]
        aid = a.get("id", "")
        running = lq.is_running(aid) or (runner and runner.is_running(aid))
        queued = lq.is_queued(aid)
        failures = a.get("consecutive_failures", 0)
        status_text = ""
        if running:
            started = runner._start_times.get(aid, 0) if runner else 0
            dur = int(time.time() - started) // 60 if started else 0
            status_text = f"▶{dur}m" if dur else "▶"
        elif queued:
            status_text = "⏳"
        elif failures >= 6:
            status_text = "⏸30m"
        elif failures:
            status_text = f"❌×{failures}"
        item = tbl.item(row, 2)
        if item:
            item.setText(status_text)


def _rebuild_smart_table(mw: Any) -> None:
    tbl = mw._smart_tbl
    if not tbl:
        return
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    btn_col = 5 + len(DAY_KEYS)

    # Filter accounts
    ft = getattr(mw, "_smart_search", None)
    search_text = ft.text().strip().lower() if ft and ft.text() else ""
    filter_key = getattr(mw, "_smart_filter", "")

    def _match_filter(a: Any) -> bool:
        if not filter_key:
            return True
        aid = a.get("id", "")
        lq = getattr(mw, "launch_queue", None)
        runner = getattr(mw, "runner", None)
        if filter_key == "running":
            return (lq and lq.is_running(aid)) or (runner and runner.is_running(aid))
        elif filter_key == "waiting":
            return lq and lq.is_queued(aid)
        elif filter_key == "error":
            f = a.get("consecutive_failures", 0)
            return 1 <= f < 6
        elif filter_key == "paused":
            return a.get("consecutive_failures", 0) >= 6
        return True

    accounts = [a for a in mw.accounts
                if (not search_text or search_text in a.get("name", "").lower()
                    or search_text in a.get("game_client", "").lower())
                and _match_filter(a)]

    tbl.setRowCount(len(accounts))
    tbl.blockSignals(True)
    for i, a in enumerate(accounts):
        # Checkbox
        cb = QCheckBox()
        cb.stateChanged.connect(lambda: _update_batch_buttons(mw))
        cw = QWidget()
        cl = QHBoxLayout(cw)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setAlignment(Qt.AlignCenter)
        cl.addWidget(cb)
        tbl.setCellWidget(i, 0, cw)
        # 账号名
        tbl.setItem(i, 1, QTableWidgetItem(a.get("name", "")))
        # 状态
        tbl.setItem(i, 2, QTableWidgetItem(""))
        # 默认关卡
        tbl.setItem(i, 3, QTableWidgetItem(a.get("smart_stage", "")))
        # 剿灭
        anni = a.get("smart_annihilation", "")
        anni_enabled = a.get("smart_annihilation_enabled", True)
        anni_display = {"": "", "Annihilation": "当期",
                        "Chernobog@Annihilation": "切城",
                        "LungmenOutskirts@Annihilation": "外环",
                        "LungmenDowntown@Annihilation": "市区"}.get(anni, "")
        if anni_display:
            anni_display = ("✔ " if anni_enabled else "✘ ") + anni_display
        tbl.setItem(i, 4, QTableWidgetItem(anni_display))
        # 一~日
        for j, dk in enumerate(DAY_KEYS):
            tbl.setItem(i, 5 + j, QTableWidgetItem(a.get(f"smart_{dk}", "")))
        # Detail button
        detail_btn = QPushButton("✎")
        detail_btn.setFixedSize(24, 24)
        detail_btn.setToolTip("编辑账号详情")
        orig_idx = mw.accounts.index(a)
        detail_btn.clicked.connect(lambda _, r=orig_idx: _open_detail(mw, r))
        dw = QWidget()
        dl = QHBoxLayout(dw)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setAlignment(Qt.AlignCenter)
        dl.addWidget(detail_btn)
        tbl.setCellWidget(i, btn_col, dw)

    tbl.blockSignals(False)
    _update_status_column(mw)


def _open_detail(mw: Any, row: int) -> None:
    from ui.account_detail import open_account_detail
    open_account_detail(mw, row)
    _rebuild_smart_table(mw)
