"""MAA instance pool management tab."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QFormLayout, QSpinBox,
)

from ui.rebuild_dialog import RebuildDialog


def build_maa_panel(mw: Any) -> QWidget:
    mw.maa_v = QWidget()
    vl = QVBoxLayout(mw.maa_v)
    vl.setContentsMargins(12, 10, 12, 6)
    vl.setSpacing(4)

    # Header
    hdr = QHBoxLayout()
    hdr.addWidget(QLabel("📦 MAA 实例池", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))
    dl_btn = QPushButton("⬇ 下载 / 更新")
    dl_btn.setObjectName("startBtn")
    dl_btn.clicked.connect(lambda: mw.maint.dl_maa_all())
    hdr.addWidget(dl_btn)
    rebuild_btn = QPushButton("🔄 重建实例")
    rebuild_btn.setObjectName("startBtn")
    rebuild_btn.clicked.connect(lambda: _rebuild_instances(mw))
    hdr.addWidget(rebuild_btn)
    hdr.addStretch()
    vl.addLayout(hdr)

    # Status info
    info_group = QGroupBox("实例信息")
    info_form = QFormLayout(info_group)
    mw._maa_version_lbl = QLabel(mw.config.get("maa_version", "未安装"))
    info_form.addRow("已安装版本:", mw._maa_version_lbl)

    parallel_row = QHBoxLayout()
    mw._maa_parallel_sp = QSpinBox()
    mw._maa_parallel_sp.setRange(1, 10)
    mw._maa_parallel_sp.setValue(mw.config.get("parallel_max", 1))
    mw._maa_parallel_sp.valueChanged.connect(lambda v: (mw.config.update({"parallel_max": v}), mw._save(), refresh_maa_panel(mw)))
    parallel_row.addWidget(mw._maa_parallel_sp)
    parallel_row.addWidget(QLabel(" 个并行实例"))
    parallel_row.addStretch()
    info_form.addRow("并行上限:", parallel_row)

    vl.addWidget(info_group)

    # Instance table
    vl.addWidget(QLabel("实例状态", font=QFont("Microsoft YaHei UI", 10, QFont.Bold)))
    tbl = QTableWidget(0, 5)
    tbl.setHorizontalHeaderLabels(["实例#", "状态", "配置", "PID", "配置文件"])
    tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
    tbl.setColumnWidth(0, 60)
    tbl.setColumnWidth(1, 70)
    tbl.setColumnWidth(2, 50)
    tbl.setColumnWidth(3, 80)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(28)
    tbl.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    mw._maa_tbl = tbl
    vl.addWidget(tbl, 1)

    refresh_btn = QPushButton("🔄 刷新状态")
    refresh_btn.clicked.connect(lambda: refresh_maa_panel(mw))
    vl.addWidget(refresh_btn)

    refresh_maa_panel(mw)
    return mw.maa_v


def refresh_maa_panel(mw: Any) -> None:
    if not hasattr(mw, "_maa_tbl"):
        return
    mw._maa_version_lbl.setText(mw.config.get("maa_version", "未安装"))
    ver = mw.config.get("maa_version", "")
    max_created = mw.config.get("maa_instances", 0)
    max_n = mw.config.get("parallel_max", 1) + 1
    pool = Path(__file__).parent.parent / "maa" / "instances"
    tbl = mw._maa_tbl
    tbl.setRowCount(max_n)
    import subprocess, os, time

    # Check if source MAA has config ready (cached, max once per 10s)
    source_ok = False
    src = Path(__file__).parent.parent / "maa" / ver if ver else None
    if src and src.exists():
        cache_key = "_maa_source_ok_cache"
        cache_ts = "_maa_source_cache_time"
        now = time.time()
        if getattr(mw, cache_ts, 0) + 10 > now:
            source_ok = getattr(mw, cache_key, False)
        else:
            from maint_ops import _check_source_ready
            source_ok = _check_source_ready(src)
            setattr(mw, cache_key, source_ok)
            setattr(mw, cache_ts, now)

    for i in range(1, max_n + 1):
        inst = pool / str(i)
        exe = inst / "MAA.exe"
        exists = exe.exists()
        pid = ""
        running = False
        pid_file = inst / ".pid"
        if pid_file.exists():
            try:
                import psutil
                pid = pid_file.read_text().strip()
                running = psutil.pid_exists(int(pid))
                running = str(pid) in r.stdout
                if not running:
                    pid = ""
                    pid_file.unlink(missing_ok=True)
            except:
                pid_file.unlink(missing_ok=True)

        tbl.setItem(i - 1, 0, QTableWidgetItem(f"#{i}"))
        if not exists and i <= max_created:
            tbl.setItem(i - 1, 1, QTableWidgetItem("未创建"))
        elif not exists:
            tbl.setItem(i - 1, 1, QTableWidgetItem("按需创建"))
        elif running:
            tbl.setItem(i - 1, 1, QTableWidgetItem("▶ 运行中"))
        else:
            tbl.setItem(i - 1, 1, QTableWidgetItem("⏸ 空闲"))

        # Config status
        if exists:
            gj = inst / "config" / "gui.new.json"
            if gj.exists():
                try:
                    d = json.loads(gj.read_text(encoding="utf-8"))
                    tq = d.get("Configurations", {}).get("Default", {}).get("TaskQueue", [])
                    cfg_ok = any("$type" in item for item in tq)
                except Exception:
                    cfg_ok = False
            else:
                cfg_ok = False
            tbl.setItem(i - 1, 2, QTableWidgetItem("✅" if cfg_ok else "⚠"))
        else:
            tbl.setItem(i - 1, 2, QTableWidgetItem(""))

        tbl.setItem(i - 1, 3, QTableWidgetItem(pid if exists else ""))
        cfg = str(inst / "config") if exists else ""
        tbl.setItem(i - 1, 4, QTableWidgetItem(cfg))


def _rebuild_instances(mw: Any) -> None:
    """Rebuild all MAA instances with progress dialog."""
    # Clean up old references
    for attr in ("_rebuild_dlg", "_rebuild_timer", "_rebuild_task"):
        old = getattr(mw, attr, None)
        if old is not None:
            try:
                if hasattr(old, "stop"):
                    old.stop()
            except Exception:
                pass

    from maint_ops import ensure_maa_instances_async
    desired = mw.config.get("parallel_max", 1) + 1
    dlg = RebuildDialog(mw, desired)
    dlg.show()
    mw._rebuild_dlg = dlg

    progress = {"current": 0, "total": desired}
    timer = QTimer(dlg)
    timer.timeout.connect(lambda: dlg.update(progress["current"], progress["total"]))
    timer.start(200)
    mw._rebuild_timer = timer

    def progress_cb(current, total):
        progress["current"] = current
        progress["total"] = total

    from background import BackgroundTask

    def _rebuild():
        ensure_maa_instances_async(mw.ctx, force=True, progress_cb=progress_cb, sync=True)

    def _done():
        timer.stop()
        dlg.close()
        mw._rebuild_dlg = None
        mw._rebuild_timer = None
        mw._rebuild_task = None
        refresh_maa_panel(mw)

    t = BackgroundTask(_rebuild)
    t.finished.connect(_done)
    mw._rebuild_task = t
    t.start()
