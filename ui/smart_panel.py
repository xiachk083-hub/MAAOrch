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
from smart_scheduler import MATERIAL_STAGES, _arknights_now


def build_smart_panel(mw: Any) -> QWidget:
    mw.smart_v = QWidget()
    vl = QVBoxLayout(mw.smart_v)
    vl.setContentsMargins(8, 6, 8, 4)
    vl.setSpacing(3)

    # ── Header ──
    hdr = QHBoxLayout()
    hdr.setSpacing(6)

    mw._queue_toggle_btn = QPushButton("▶ 启动队列")
    mw._queue_toggle_btn.setFixedHeight(24)
    mw._queue_toggle_btn.setStyleSheet("QPushButton{background:#326cf3;color:#fff;border:1px solid #326cf3;border-radius:4px;padding:2px 10px;font-size:9pt}")
    def _toggle_queue():
        lq = getattr(mw, "launch_queue", None)
        if not lq:
            return
        if lq.is_paused:
            lq.resume()
            mw._queue_toggle_btn.setText("⏸ 暂停队列")
            mw._queue_toggle_btn.setStyleSheet("QPushButton{background:#e8a000;color:#fff;border:1px solid #e8a000;border-radius:4px;padding:2px 10px;font-size:9pt}")
            mw._log("队列已启动")
        else:
            lq.pause()
            mw._queue_toggle_btn.setText("▶ 启动队列")
            mw._queue_toggle_btn.setStyleSheet("QPushButton{background:#326cf3;color:#fff;border:1px solid #326cf3;border-radius:4px;padding:2px 10px;font-size:9pt}")
            mw._log("队列已暂停")
    mw._queue_toggle_btn.clicked.connect(_toggle_queue)
    hdr.addWidget(mw._queue_toggle_btn)

    mw._smart_anni_cb = QCheckBox("剿灭")
    mw._smart_anni_cb.setChecked(True)
    hdr.addWidget(mw._smart_anni_cb)

    hdr.addWidget(QLabel("并行:"))
    parallel_sp = QSpinBox()
    parallel_sp.setRange(1, 10)
    parallel_sp.setFixedWidth(50)
    parallel_sp.setValue(mw.config.get("parallel_max", 1))
    parallel_sp.valueChanged.connect(lambda v: mw.config.update({"parallel_max": v}))
    hdr.addWidget(parallel_sp)

    mw._queue_stats = QLabel("")
    mw._queue_stats.setStyleSheet("color:#888;font-size:9pt")
    hdr.addWidget(mw._queue_stats)

    hdr.addStretch()

    mw._smart_enabled_cb = QCheckBox("启用智能调度")
    mw._smart_enabled_cb.setChecked(mw.config.get("smart_global", {}).get("enabled", False))
    mw._smart_enabled_cb.toggled.connect(lambda v: _toggle_smart(mw, v))
    hdr.addWidget(mw._smart_enabled_cb)

    log_btn = QPushButton("📋")
    log_btn.setFixedSize(28, 24)
    log_btn.setToolTip("打开日志窗口")
    log_btn.clicked.connect(lambda: _open_log(mw))
    hdr.addWidget(log_btn)

    settings_btn = QPushButton("⚙")
    settings_btn.setFixedSize(28, 24)
    settings_btn.setToolTip("设置")
    settings_btn.clicked.connect(lambda: _open_settings(mw))
    hdr.addWidget(settings_btn)

    vl.addLayout(hdr)

    # ── Search bar ──
    search_row = QHBoxLayout()
    mw._smart_search = QLineEdit()
    mw._smart_search.setPlaceholderText("🔍 搜索账号...")
    mw._smart_search.textChanged.connect(lambda: _refresh_smart_view(mw))
    search_row.addWidget(mw._smart_search, 1)
    add_btn = QPushButton("+ 添加账号")
    add_btn.setObjectName("startBtn")
    add_btn.clicked.connect(lambda: _add_account(mw))
    search_row.addWidget(add_btn)
    vl.addLayout(search_row)

    # ── Account table ──
    DAYS = ["一", "二", "三", "四", "五", "六", "日"]
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    cols = 6 + len(DAYS)  # check + name + status + stage + anni + days + detail
    tbl = QTableWidget(0, cols)
    tbl.setHorizontalHeaderLabels(["☐", "账号", "状态", "默认关卡", "剿灭"] + DAYS + [""])
    tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    tbl.setColumnWidth(0, 28)
    tbl.setColumnWidth(2, 60)
    tbl.setColumnWidth(3, 80)
    tbl.setColumnWidth(4, 80)
    for i in range(len(DAYS)):
        tbl.setColumnWidth(5 + i, 55)
    tbl.setColumnWidth(5 + len(DAYS), 32)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QAbstractItemView.DoubleClicked)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(26)
    tbl.setSortingEnabled(True)
    tbl.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    tbl.itemChanged.connect(lambda item: _on_cell_edit(mw, item))
    mw._smart_tbl = tbl
    vl.addWidget(tbl, 1)

    # ── Batch operations bar ──
    batch_row = QHBoxLayout()
    batch_row.setSpacing(6)
    mw._batch_edit_btn = QPushButton("批量设置")
    mw._batch_edit_btn.setEnabled(False)
    mw._batch_edit_btn.clicked.connect(lambda: _do_batch_edit(mw))
    batch_row.addWidget(mw._batch_edit_btn)
    mw._batch_enqueue_btn = QPushButton("批量入队")
    mw._batch_enqueue_btn.setEnabled(False)
    mw._batch_enqueue_btn.clicked.connect(lambda: _do_batch_enqueue(mw))
    batch_row.addWidget(mw._batch_enqueue_btn)
    mw._batch_stop_btn = QPushButton("批量停止")
    mw._batch_stop_btn.setEnabled(False)
    mw._batch_stop_btn.clicked.connect(lambda: _do_batch_stop(mw))
    batch_row.addWidget(mw._batch_stop_btn)
    mw._batch_delete_btn = QPushButton("批量删除")
    mw._batch_delete_btn.setEnabled(False)
    mw._batch_delete_btn.clicked.connect(lambda: _do_batch_delete(mw))
    batch_row.addWidget(mw._batch_delete_btn)
    mw._batch_status = QLabel("")
    mw._batch_status.setStyleSheet("color:#888;font-size:8pt")
    batch_row.addWidget(mw._batch_status)
    batch_row.addStretch()
    vl.addLayout(batch_row)

    # ── Log bar ──
    log_bar = QFrame()
    log_bar.setFrameShape(QFrame.StyledPanel)
    log_bar.setStyleSheet("QFrame{background:#1e1e22;border:1px solid #2b2b30;border-radius:4px;padding:2px 4px}")
    log_bar.setFixedHeight(26)
    mw._log_bar_text = QLabel(" 就绪")
    mw._log_bar_text.setStyleSheet("color:#888;font-size:8pt")
    log_bar.setToolTip("点击打开日志窗口")
    log_bar.mousePressEvent = lambda e: _open_log(mw)
    lb_layout = QHBoxLayout(log_bar)
    lb_layout.setContentsMargins(4, 0, 4, 0)
    lb_layout.addWidget(mw._log_bar_text)
    vl.addWidget(log_bar)

    # Initial render
    _rebuild_smart_table(mw)

    # Auto-refresh timer for status column
    mw._smart_refresh_timer = QTimer(mw.smart_v)
    mw._smart_refresh_timer.timeout.connect(lambda: _update_status_column(mw))
    mw._smart_refresh_timer.start(3000)

    return mw.smart_v


def _open_log(mw: Any) -> None:
    from ui.log_window import show_log_window
    show_log_window(mw)


def _open_settings(mw: Any) -> None:
    from ui.settings_window import open_settings
    open_settings(mw)


def _add_account(mw: Any) -> None:
    from account import Account
    a = Account()
    a.name = f"账号{len(mw.accounts) + 1}"
    mw.accounts.append(a)
    mw._save()
    _rebuild_smart_table(mw)
    mw._log(f"已添加账号: {a.name}")


def _on_cell_edit(mw: Any, item: QTableWidgetItem) -> None:
    """Handle in-place cell edits."""
    row = item.row()
    col = item.column()
    if row >= len(mw.accounts):
        return
    a = mw.accounts[row]
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    text = item.text().strip()
    if col == 1:  # 账号名
        a["name"] = text
    elif col == 3:  # 默认关卡
        a["smart_stage"] = text
    elif 5 <= col < 5 + len(DAY_KEYS):  # 一~日
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

    # Update queue stats in header
    ac = lq.active_count
    qc = lq.pending_count
    mw._queue_stats.setText(f"▶{ac}  ⏳{qc}" if ac or qc else "")

    # Update log bar (show last log line)
    try:
        lp = __import__('pathlib').Path(__file__).parent.parent / "debug.log"
        if lp.exists():
            lines = lp.read_text(encoding="utf-8").strip().split("\n")
            if lines:
                last = lines[-1][:100]
                mw._log_bar_text.setText(f" {last}")
    except Exception:
        pass

    for row in range(tbl.rowCount()):
        if row >= len(mw.accounts):
            continue
        a = mw.accounts[row]
        aid = a.get("id", "")
        running = lq.is_running(aid) or (runner and runner.is_running(aid))
        queued = lq.is_queued(aid)
        failures = a.get("consecutive_failures", 0)
        status_text = "▶"
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
        else:
            continue
        item = tbl.item(row, 2)
        if item:
            item.setText(status_text)


def _rebuild_smart_table(mw: Any) -> None:
    """Rebuild the account table from scratch."""
    tbl = mw._smart_tbl
    if not tbl:
        return
    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_names = ["一", "二", "三", "四", "五", "六", "日"]
    btn_col = 5 + len(DAY_KEYS)
    ANNIHILATION_NAMES = {"": "", "Annihilation": "当期", "Chernobog@Annihilation": "切城",
                           "LungmenOutskirts@Annihilation": "外环", "LungmenDowntown@Annihilation": "市区"}

    # Filter accounts by search
    ft = getattr(mw, "_smart_search", None)
    search_text = ft.text().strip().lower() if ft and ft.text() else ""
    accounts = mw.accounts
    if search_text:
        accounts = [a for a in accounts if search_text in a.get("name", "").lower()
                    or search_text in a.get("game_client", "").lower()
                    or search_text in a.get("tags", "").lower()]

    tbl.setRowCount(len(accounts))
    tbl.blockSignals(True)
    for i, a in enumerate(accounts):
        # Checkbox
        cb = __import__('PySide6').QtWidgets.QCheckBox()
        cb.stateChanged.connect(lambda: _update_batch_buttons(mw))
        cw = QWidget()
        cl = QHBoxLayout(cw)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setAlignment(Qt.AlignCenter)
        cl.addWidget(cb)
        tbl.setCellWidget(i, 0, cw)

        # 账号名
        tbl.setItem(i, 1, QTableWidgetItem(a.get("name", "")))

        # 状态 (placeholder, updated by timer)
        status_item = QTableWidgetItem("")
        status_item.setTextAlignment(Qt.AlignCenter)
        tbl.setItem(i, 2, status_item)

        # 默认关卡
        tbl.setItem(i, 3, QTableWidgetItem(a.get("smart_stage", "")))

        # 剿灭
        anni = a.get("smart_annihilation", "")
        anni_enabled = a.get("smart_annihilation_enabled", True)
        anni_display = ANNIHILATION_NAMES.get(anni, "")
        if anni_display:
            anni_display = ("✔ " if anni_enabled else "✘ ") + anni_display
        anni_item = QTableWidgetItem(anni_display)
        tbl.setItem(i, 4, anni_item)

        # 一~日
        for j, dk in enumerate(DAY_KEYS):
            tbl.setItem(i, 5 + j, QTableWidgetItem(a.get(f"smart_{dk}", "")))

        # Detail button
        detail_btn = QPushButton("✎")
        detail_btn.setFixedSize(26, 26)
        detail_btn.setToolTip("编辑账号详情")
        # Capture the correct account index
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


def _toggle_smart(mw: Any, enabled: bool) -> None:
    sg = mw.config.setdefault("smart_global", {})
    sg["enabled"] = enabled
    mw._save()
    if enabled:
        QTimer.singleShot(500, lambda: (setattr(mw, "_last_smart_minute", ""), mw._smart_tick()))


def _run_smart_all(mw: Any) -> None:
    if not mw.config.get("smart_global", {}).get("enabled", False):
        mw._log("智能调度未启用")
        return
    if hasattr(mw, "launch_queue") and mw.launch_queue:
        with mw.launch_queue._lock:
            mw.launch_queue._pending.clear()
            mw.launch_queue._active_emus.clear()
        mw.launch_queue._save_queue()
    mw._smart_force = True
    setattr(mw, "_last_smart_minute", "")
    mw._smart_tick()
    mw._smart_force = False


def _refresh_smart_view(mw: Any) -> None:
    """Refresh the table (called when search text changes)."""
    _rebuild_smart_table(mw)
