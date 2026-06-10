"""MainWindow poll/smart_tick/health logic — extracted from main_window.py."""
from __future__ import annotations
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def do_poll(mw: Any) -> None:
    """Periodic poll (every 2s)."""
    mw.maint.poll()
    if hasattr(mw, "launch_queue"):
        lq = mw.launch_queue
        ac = lq.active_count
        qc = lq.pending_count
        if ac:
            mw._qsb.setText(f"\u25B6{ac}" + (f"  \u23F3{qc}" if qc else ""))
        elif qc:
            mw._qsb.setText(f"\u23F3{qc}")
        else:
            mw._qsb.setText("")
        now = int(time.time())
        if now % 30 == 0:
            total = len(mw.accounts)
            errors = sum(1 for a in mw.accounts if a.get("consecutive_failures", 0) >= 6)
            mw._log(f"[状态] 运行中: {ac}/{total} | 队列: {qc} | 错误: {errors}")


def do_smart_tick(mw: Any) -> None:
    """Smart scheduler tick (every 1 minute). Delegates task conditions to decide()."""
    sg = mw.config.get("smart_global", {})
    if not sg.get("enabled", False) or not hasattr(mw, "launch_queue"):
        return
    now = datetime.now()
    minute_key = now.strftime("%H:%M")
    if getattr(mw, "_last_smart_minute", "") == minute_key:
        return
    mw._last_smart_minute = minute_key
    from services.smart_scheduler import decide
    count = 0
    skipped_no_cfg = 0
    for a in mw.accounts:
        aid = a.get("id", "")
        if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
            skipped_no_cfg += 1
            continue
        if mw.launch_queue.is_queued(aid):
            continue
        running = mw.launch_queue.is_running(aid) or (getattr(mw, "runner", None) and mw.runner.is_running(aid))
        if running:
            if not a.get("smart_pending", False):
                a["smart_pending"] = True
            continue
        last_error = a.get("smart_last_error", 0)
        if last_error and time.time() - last_error < 300:
            continue
        tasks = decide(a, sg)
        if tasks:
            plan_txt = ",".join(tasks)
            a["smart_plan"] = plan_txt
            mw.launch_queue.enqueue(aid, "schedule", priority=1)
            count += 1
    if count:
        mw._log(f"\U0001f9e0 智能调度: {count} 个账号已入队" + (f" ({skipped_no_cfg} 个缺配置跳过)" if skipped_no_cfg else ""))
        if mw.launch_queue.is_paused:
            mw.launch_queue.resume()
    else:
        reasons = [f"{skipped_no_cfg} 个缺配置"] if skipped_no_cfg else []
        reasons.append("体力不足 / 暂无任务")
        mw._log(f"\U0001f9e0 智能调度: 暂无账号需要调度（{'，'.join(reasons)}）")


def do_health_check(mw: Any) -> None:
    """Background health check on startup."""
    try:
        from services.health_check import run_health_check
        report = run_health_check(mw.ctx)
        n = report.error_count + report.warn_count
        if n:
            mw._log(f"\u26a0 环境检测: {report.error_count} 个错误, {report.warn_count} 个警告")
        else:
            mw._log("\u2705 环境检测: 全部正常")
    except Exception as e:
        mw._log(f"环境检测失败: {e}")


def show_health_dialog(mw: Any) -> None:
    """Open health check dialog."""
    from services.health_check import run_health_check, show_health_dialog as _shd
    report = run_health_check(mw.ctx)
    _shd(mw, report)


def show_todo(mw: Any) -> None:
    """Show configuration todo dialog."""
    issues = []
    if not mw.config.get("maa_version", ""):
        issues.append(("系统", "未下载 MAA，请将 MAA 放到 maa/source/"))
    for a in mw.accounts:
        aid = a.get("id", "")
        name = a.get("name", "").strip() or aid[:6]
        if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
            issues.append((name, "未配置 ADB 或模拟器"))
        if mw.config.get("smart_global", {}).get("enabled", False):
            if not a.get("smart_stage", ""):
                issues.append((name, "智能模式开启但未设默认关卡"))
    if not issues:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(mw, "\U0001f4cb 配置待办", "所有账号配置齐全，暂无待办项。")
        return
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
    d = QDialog(mw)
    d.setWindowTitle(f"\U0001f4cb 配置待办 ({len(issues)})")
    d.setMinimumSize(500, 350)
    l = QVBoxLayout(d)
    l.addWidget(QLabel("以下账号存在未完成的配置项："))
    for acct, issue in issues:
        l.addWidget(QLabel(f"  \u26a0 {acct} \u2014 {issue}"))
    btn = QPushButton("知道了")
    btn.clicked.connect(d.accept)
    l.addWidget(btn)
    d.exec()
