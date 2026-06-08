"""Startup health check — detect environment issues and offer one-click fixes.

Usage:
    report = run_health_check(ctx)
    if report.has_issues:
        show_health_dialog(mw, report)
"""
from __future__ import annotations
import sys, subprocess, shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)


@dataclass
class HealthItem:
    name: str
    status: str          # "ok" / "warn" / "error"
    message: str = ""
    fix: Callable[[], str] | None = None  # returns result message
    fix_label: str = "修复"


@dataclass
class HealthReport:
    items: list[HealthItem] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return any(i.status != "ok" for i in self.items)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.items if i.status == "error")

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.items if i.status == "warn")


STATUS_ICON = {"ok": "✅", "warn": "⚠️", "error": "❌"}


def run_health_check(ctx: Any) -> HealthReport:
    """Run all health checks and return a report."""
    items: list[HealthItem] = []
    cfg = ctx.config
    mw = ctx._mw

    # 1. Python version
    v = sys.version_info
    if v.major >= 3 and v.minor >= 10:
        items.append(HealthItem("Python 版本", "ok", f"{v.major}.{v.minor}.{v.micro}"))
    else:
        items.append(HealthItem("Python 版本", "error", f"{v.major}.{v.minor}.{v.micro}，需要 >= 3.10"))

    # 2. PySide6
    try:
        import PySide6
        items.append(HealthItem("PySide6", "ok", PySide6.__version__))
    except ImportError:
        def _fix_pyside():
            r = subprocess.run([sys.executable, "-m", "pip", "install", "PySide6"],
                               capture_output=True, text=True, timeout=120)
            return "安装成功" if r.returncode == 0 else f"失败: {r.stderr[:200]}"
        items.append(HealthItem("PySide6", "error", "未安装", _fix_pyside, "一键安装"))

    # 3. config.json
    from config import CONFIG_FILE, load_config
    if CONFIG_FILE.exists():
        try:
            d = load_config()
            if d:
                items.append(HealthItem("配置文件", "ok", "config.json 正常"))
            else:
                items.append(HealthItem("配置文件", "error", "config.json 为空或损坏"))
        except Exception as e:
            items.append(HealthItem("配置文件", "error", str(e)))
    else:
        items.append(HealthItem("配置文件", "ok", "新安装，config.json 待创建"))

    # 4. MAA version
    ver = cfg.get("maa_version", "")
    if ver and ver != "installed":
        items.append(HealthItem("MAA 版本", "ok", ver))
    elif ver == "installed":
        items.append(HealthItem("MAA 版本", "warn", "版本号未识别（旧版遗留），请点击修复"))
        def _fix_maa_ver():
            from utils import parse_maa_version
            for d in sorted(Path(__file__).parent.glob("maa/*/MAA.exe")):
                v = parse_maa_version(str(d.parent))
                if v:
                    cfg["maa_version"] = v
                    return f"已更新为 {v}"
            return "未找到 MAA 版本号"
        items.append(HealthItem("MAA 版本", "warn", "installed", _fix_maa_ver, "修复"))
    else:
        def _fix_maa():
            if mw and hasattr(mw, "maint"):
                mw.maint.dl_maa_all()
                return "已启动下载"
            return "无法下载"
        items.append(HealthItem("MAA 版本", "warn", "未下载 MAA", _fix_maa, "下载 MAA"))

    # 5. User MAA source directory
    src_dir = Path(__file__).parent / "maa" / "source"
    if (src_dir / "MAA.exe").exists():
        items.append(HealthItem("MAA 源目录", "ok", "maa/source/ 已就绪"))
    else:
        items.append(HealthItem("MAA 源目录", "warn",
            "请将 MAA 完整目录复制到 maa/source/（含 MAA.exe 及 resource/ 等）"))

    # 6. MAA source config initialization
    ver = cfg.get("maa_version", "")
    if ver and ver != "installed":
        from maint_ops import _check_source_ready, _init_maa_source
        src = Path(__file__).parent / "maa" / ver
        if src.exists() and (src / "MAA.exe").exists():
            if _check_source_ready(src):
                items.append(HealthItem("MAA 源配置", "ok", "已初始化"))
            else:
                def _fix_source_init():
                    ok = _init_maa_source(src)
                    return "初始化成功" if ok else "初始化失败"
                items.append(HealthItem("MAA 源配置", "warn", "需初始化", _fix_source_init, "初始化"))
        else:
            items.append(HealthItem("MAA 源配置", "warn", f"目录 {ver} 不存在"))
    elif ver == "installed":
        items.append(HealthItem("MAA 源配置", "warn", "版本号未识别，无法检测配置"))

    # 6. MAA instances
    pool = Path(__file__).parent / "maa" / "instances"
    instances = list(pool.glob("*/MAA.exe")) if pool.exists() else []
    if instances:
        items.append(HealthItem("MAA 实例", "ok", f"{len(instances)} 个实例"))
    else:
        items.append(HealthItem("MAA 实例", "warn", "无实例（需先下载 MAA）"))

    # 6. Accounts
    total = len(ctx.accounts)
    if total == 0:
        items.append(HealthItem("账号配置", "warn", "未添加账号"))
    else:
        ok_count = sum(1 for a in ctx.accounts if a.get("adb_address", "").strip() or a.get("emu_instance_index", ""))
        items.append(HealthItem("账号配置", "ok" if ok_count == total else "warn",
                                f"{total} 个账号，{ok_count} 个已配置 ADB/模拟器"))

    # 7. ADB
    adb_found = False
    # Check account-specific adb_path
    for a in ctx.accounts:
        p = a.get("adb_path", "")
        if p and Path(p).exists():
            adb_found = True
            break
    # Check system PATH
    if not adb_found:
        adb_found = shutil.which("adb") is not None
    # Check common emulator paths
    if not adb_found:
        common = [
            "C:/Program Files/Nox/bin/nox_adb.exe",
            "C:/Program Files/MuMu/MuMuPlayer/adb.exe",
            "C:/Program Files/BlueStacks_nxt/HD-Player.exe",
        ]
        for p in common:
            if Path(p).exists():
                adb_found = True
                break
    if adb_found:
        items.append(HealthItem("ADB", "ok", "已找到"))
    else:
        items.append(HealthItem("ADB", "warn", "未在 PATH 中找到，可手动指定账号 adb_path"))

    # 8. Backups directory
    bp = Path(__file__).parent / "backups"
    if bp.exists():
        items.append(HealthItem("备份目录", "ok", str(bp)))
    else:
        def _fix_backup():
            bp.mkdir(parents=True, exist_ok=True)
            return f"已创建 {bp}"
        items.append(HealthItem("备份目录", "warn", "不存在", _fix_backup, "创建"))

    # 9. Log writable
    lp = Path(__file__).parent / "debug.log"
    try:
        lp.touch(exist_ok=True)
        items.append(HealthItem("日志文件", "ok", str(lp)))
    except Exception as e:
        items.append(HealthItem("日志文件", "error", f"不可写: {e}"))

    # 10. Pip available (for self-fix)
    pip_ok = shutil.which("pip") is not None or shutil.which("pip3") is not None
    if pip_ok:
        items.append(HealthItem("pip", "ok", "已找到"))
    else:
        items.append(HealthItem("pip", "warn", "未在 PATH 中找到，部分修复不可用"))

    return HealthReport(items)


def show_health_dialog(mw: Any, report: HealthReport | None = None) -> None:
    """Open the health check result dialog."""
    if report is None:
        report = run_health_check(mw.ctx)

    d = QDialog(mw)
    d.setWindowTitle("🔍 环境检测与修复")
    d.setMinimumSize(620, 400)
    d.setStyleSheet(getattr(mw, "styleSheet", lambda: "")() if hasattr(mw, "styleSheet") else "")
    vl = QVBoxLayout(d)
    vl.setSpacing(6)

    title = QLabel("🔍 环境检测与修复")
    title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
    vl.addWidget(title)

    tbl = QTableWidget(len(report.items), 3)
    tbl.setHorizontalHeaderLabels(["状态", "检查项", "操作"])
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setShowGrid(False)
    tbl.setAlternatingRowColors(True)
    tbl.verticalHeader().setDefaultSectionSize(32)

    for i, item in enumerate(report.items):
        icon = STATUS_ICON.get(item.status, "❓")
        tbl.setItem(i, 0, QTableWidgetItem(f"  {icon}"))
        msg = f"{item.name}: {item.message}" if item.message else item.name
        tbl.setItem(i, 1, QTableWidgetItem(f"  {msg}"))
        if item.fix:
            btn = QPushButton(item.fix_label)
            btn.setFixedHeight(26)

            def _do_fix(item=item, btn=btn, row=i):
                btn.setEnabled(False)
                btn.setText("修复中...")
                from PySide6.QtCore import QTimer
                QTimer.singleShot(50, lambda: _apply_fix(d, tbl, row, item, btn))

            btn.clicked.connect(_do_fix)
            tbl.setCellWidget(i, 2, btn)
        else:
            tbl.setItem(i, 2, QTableWidgetItem(""))

    vl.addWidget(tbl, 1)

    # Bottom buttons
    btn_row = QHBoxLayout()
    recheck = QPushButton("🔄 重新检测")
    recheck.clicked.connect(lambda: (_recheck(d, mw)))
    btn_row.addWidget(recheck)
    btn_row.addStretch()
    close_btn = QPushButton("关闭")
    close_btn.clicked.connect(d.accept)
    btn_row.addWidget(close_btn)
    vl.addLayout(btn_row)

    d.exec()


def _apply_fix(dlg: QDialog, tbl: QTableWidget, row: int, item: HealthItem, btn: QPushButton) -> None:
    """Run a fix action and update the table row."""
    try:
        if item.fix:
            result = item.fix()
            item.status = "ok"
            item.message = result
    except Exception as e:
        item.status = "error"
        item.message = str(e)
    # Update row
    icon = STATUS_ICON.get(item.status, "❓")
    tbl.setItem(row, 0, QTableWidgetItem(f"  {icon}"))
    tbl.setItem(row, 1, QTableWidgetItem(f"  {item.name}: {item.message}"))
    tbl.removeCellWidget(row, 2)
    if item.fix and item.status != "ok":
        btn2 = QPushButton(item.fix_label)
        btn2.setFixedHeight(26)
        btn2.clicked.connect(lambda: _apply_fix(dlg, tbl, row, item, btn2))
        tbl.setCellWidget(row, 2, btn2)


def _recheck(dlg: QDialog, mw: Any) -> None:
    """Re-run health check and refresh dialog."""
    report = run_health_check(mw.ctx)
    dlg.accept()
    show_health_dialog(mw, report)
