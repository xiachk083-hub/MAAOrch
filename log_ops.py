from __future__ import annotations
import re, json
from pathlib import Path
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QLabel, QMessageBox, QHeaderView, QAbstractItemView, QTabWidget, QWidget
from PySide6.QtGui import QColor, QFont, QTextCursor
from updater import UpdateCheckThread, UpdateDialog
from utils import get_platform_key, parse_maa_version
from callbacks import ServiceContext


class LogService:
    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def asst_log_path(self, w: dict) -> Path:
        return Path(w.get("path", "")).parent / "debug" / "asst.log"

    @staticmethod
    def _extract_latest_run(log_path: Path) -> list[str]:
        """Scan asst.log backwards to find the latest run via 'Version v' markers."""
        import os
        CHUNK = 8192
        try:
            fsize = os.path.getsize(str(log_path))
        except OSError:
            return []
        if fsize == 0:
            return []

        with open(str(log_path), "rb") as f:
            buf = b""
            ver_positions = []  # byte offsets of "Version v" lines (from file start)
            offset = fsize

            # Scan backwards in chunks looking for "Version v"
            while offset > 0 and len(ver_positions) < 3:
                read_size = min(CHUNK, offset)
                offset -= read_size
                f.seek(offset)
                chunk = f.read(read_size)
                buf = chunk + buf

                # Find all "Version v" in current buffer
                pos = 0
                while True:
                    idx = buf.find(b"Version v", pos)
                    if idx == -1:
                        break
                    # Find the start of this line (search backwards for \n)
                    line_start = buf.rfind(b"\n", 0, idx)
                    if line_start == -1:
                        line_start = 0
                    else:
                        line_start += 1  # after \n
                    ver_positions.append(offset + line_start)
                    pos = idx + 1

                # Keep only last 2 positions (sorted ascending)
                if len(ver_positions) > 2:
                    ver_positions = sorted(set(ver_positions))[-2:]

            if not ver_positions:
                # Fallback: no Version markers found, read last 2000 lines
                try:
                    return log_path.read_text(encoding="utf-8", errors="replace").strip().split("\n")[-2000:]
                except Exception:
                    return []

            # Sort positions ascending
            ver_positions = sorted(set(ver_positions))

            # The LAST Version marks the start of the most recent run
            run_start = ver_positions[-1]
            run_end = fsize

            # Read only the run section (cap at 10MB to avoid OOM)
            read_size = min(run_end - run_start, 10 * 1024 * 1024)
            f.seek(run_end - read_size if run_end - run_start > read_size else run_start)
            raw = f.read(read_size)
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                return []
            return text.strip().replace("\r\n", "\n").split("\n")

    @staticmethod
    def rotate_log(log_path: Path, keep_runs: int = 3) -> None:
        """Keep only the last N runs in asst.log, truncate older content."""
        import os
        try:
            fsize = os.path.getsize(str(log_path))
        except OSError:
            return
        if fsize < 50000:  # Don't bother if <50KB
            return

        # Scan backwards for Version markers
        CHUNK = 8192
        buf = b""
        ver_positions = []
        offset = fsize

        with open(str(log_path), "rb") as f:
            while offset > 0 and len(ver_positions) < keep_runs + 1:
                read_size = min(CHUNK, offset)
                offset -= read_size
                f.seek(offset)
                buf = f.read(read_size) + buf
                pos = 0
                while True:
                    idx = buf.find(b"Version v", pos)
                    if idx == -1:
                        break
                    line_start = buf.rfind(b"\n", 0, idx) + 1
                    ver_positions.append(offset + line_start)
                    pos = idx + 1
                if len(ver_positions) > keep_runs + 1:
                    ver_positions = sorted(ver_positions)[-(keep_runs + 1):]

            if len(ver_positions) <= keep_runs:
                return  # Not enough runs to rotate

            # Cut point: start of the (N+1)th run from the end
            ver_positions.sort()
            cut = ver_positions[-(keep_runs + 1)]  # Keep last keep_runs runs

            # Read from cut point to end, write back
            f.seek(cut)
            tail = f.read(fsize - cut)
        try:
            with open(str(log_path), "wb") as f:
                f.write(tail.lstrip(b"\n"))
        except Exception:
            pass  # Never crash on rotation failure

    def switch_maa_version(self, w: dict, channel: str) -> None:
        if QMessageBox.question(self.ctx._mw, "切换版本", f"将下载最新 {channel} 版 MAA\n并替换当前版本\n\n是否继续？") != QMessageBox.Yes:
            return
        self.ctx.log(f"切换 MAA 版本: {channel}")
        self.ctx.set_status(f"下载 {channel} 版...")

        def _on_result(r):
            if not r.get("ok"):
                QMessageBox.critical(self.ctx._mw, "失败", r.get("error", ""))
                self.ctx.set_status("就绪")
                return
            tag = r["tag"]
            info = r["assets"].get(get_platform_key())
            if not info:
                QMessageBox.warning(self.ctx._mw, "失败", "无可用包")
                self.ctx.set_status("就绪")
                return
            dlg = UpdateDialog(self.ctx._mw, tag, info, str(Path(w["path"]).parent))
            if dlg.exec() == QDialog.Accepted:
                w["maa_version"] = tag
                w["update_channel"] = channel
                self.ctx.save()
                self.ctx.log(f"MAA 已切换至: {tag}")
                ac = next((a for a in self.ctx.accounts if a["id"] == w.get("account_ref", "")), None)
                if ac and self.ctx.cfg:
                    self.ctx.cfg.inject(w, ac)
            self.ctx.set_status("就绪")

        t = UpdateCheckThread()
        t.result_ready.connect(_on_result)
        self.ctx.update_thread = t
        t.start()

    def parse_log(self, w: dict, tail: int = 2000) -> tuple[list[dict], dict | None, dict | None]:
        lp = self.asst_log_path(w)
        if not lp.exists():
            return [], None, None

        # ── Extract latest run section using Version markers ──
        try:
            lines = self._extract_latest_run(lp)
        except Exception:
            return [], None, None

        tasks = []
        seen_chains: set[str] = set()
        chain_status: dict[str, dict] = {}  # taskchain → task dict
        task_map = {"StartUp": "开始唤醒", "Fight": "刷关作战", "Recruit": "公开招募", "Infrast": "基建换班", "Mall": "信用商店", "Award": "领取奖励", "Roguelike": "肉鸽探索", "Reclamation": "生息演算", "CloseDown": "关闭游戏"}
        # MAA internal chains (not user tasks)
        skip_chains = {"Depot", "OperBox"}
        last_sanity: dict | None = None
        last_drops: dict | None = None

        for line in lines:
            m = re.match(r"\[([^\]]+)\]", line)
            ts = m.group(1) if m else ""

            # ── v6: SubTaskExtraInfo (sanity, drops, status) ──
            if "SubTaskExtraInfo" in line:
                jm = re.search(r"\{.*\}", line)
                if jm:
                    try:
                        data = json.loads(jm.group(0))
                    except Exception:
                        data = {}
                    what = data.get("what", "")
                    if what == "SanityBeforeStage":
                        d = data.get("details", {})
                        if d.get("current_sanity") is not None:
                            last_sanity = {"current": d["current_sanity"], "max": d.get("max_sanity", 0),
                                           "report_time": d.get("report_time", "")}
                    elif what == "StageDrops":
                        stats = data.get("details", {}).get("stats", [])
                        if stats:
                            last_drops = {s["itemName"]: s["quantity"] for s in stats}
                    elif what == "ExceededLimit":
                        tc = data.get("taskchain", "")
                        if tc in chain_status:
                            chain_status[tc]["status"] = "失败"
                            chain_status[tc]["error"] = "超过最大重试次数"

            # ── v6/v5: unified JSON extraction for task start/complete ──
            elif "taskchain" in line:
                # Extract JSON from both formats:
                #   Format A: ...append_callback | SubTaskStart {...}
                #   Format B: [INF] {"taskchain":"Fight",...}
                jm = re.search(r"\{.*\}", line)
                if not jm:
                    continue
                try:
                    data = json.loads(jm.group(0))
                except Exception:
                    continue
                tc = data.get("taskchain", "")

                # AllTasksCompleted — handle before skip_chains filter
                if "AllTasksCompleted" in line:
                    for t in tasks:
                        if t["status"] == "运行中":
                            t["status"] = "完成"
                    continue

                if tc in skip_chains:
                    continue

                # TaskChainCompleted
                if "TaskChainCompleted" in line:
                    if tc in task_map and tc in chain_status:
                        if chain_status[tc]["status"] == "运行中":
                            chain_status[tc]["status"] = "完成"
                    continue

                # Task start — update existing if same chain restarted
                if "SubTaskStart" in line and tc in task_map:
                    if tc in seen_chains:
                        if tc in chain_status:
                            chain_status[tc]["start"] = ts
                            chain_status[tc]["status"] = "运行中"
                            chain_status[tc]["error"] = ""
                    else:
                        seen_chains.add(tc)
                        cur_task = {"name": task_map[tc], "start": ts, "status": "运行中", "drops": "", "error": ""}
                        tasks.append(cur_task)
                        chain_status[tc] = cur_task

            # ── Error detection ──
            elif "[ERR]" in line:
                err_text = line.split("[ERR]")[-1].strip()[:100]
                # Attach to last running task
                for t in reversed(tasks):
                    if t["status"] == "运行中":
                        t["status"] = "失败"
                        t["error"] = err_text
                        break

            # ── v5: TaskSwitched ──
            elif "TaskSwitched" in line:
                for t in reversed(tasks):
                    if t["status"] == "运行中":
                        t["status"] = "完成"
                        break

        return tasks, last_sanity, last_drops

    def show_stats(self, w: dict) -> None:
        """Show run statistics — reads stats.json if available, falls back to live log parse."""
        from stats import RunStats

        # Try stats.json first
        aid = w.get("account_ref", "")
        st = RunStats(aid) if aid else None
        runs = st._data.get("runs", []) if st else []

        d = QDialog(self.ctx._mw)
        d.setWindowTitle("MAA 运行统计")
        d.setMinimumSize(500, 420)
        l = QVBoxLayout(d)

        if runs:
            l.addWidget(QLabel("📊 MAA 运行统计 (历史记录)", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))
            tabs = QTabWidget()
            tabs.addTab(self._stats_table(runs[-15:], "最近"), "最近")
            tabs.addTab(self._stats_table(self._daily_runs(runs), "今日"), "今日")
            tabs.addTab(self._stats_table(self._weekly_runs(runs), "本周"), "本周")
            l.addWidget(tabs)
            # Last run sanity
            s = st.get_last_sanity() if st else None
            if s:
                cur, mx = s.get("current", 0), s.get("max", 1)
            deficit = s.get("deficit", max(0, mx - cur))
                mins = deficit * 6
                h, m = divmod(mins, 60)
                l.addWidget(QLabel(f"💊 上次结束理智: {cur}/{mx}  恢复至满需 {h}h{m:02d}m"))
        else:
            l.addWidget(QLabel("📊 MAA 运行统计 (实时解析)", font=QFont("Microsoft YaHei UI", 13, QFont.Bold)))

        # Live parse fallback / supplement
        tasks, sanity, drops = self.parse_log(w)
        if tasks:
            tw = QTableWidget()
            tw.setColumnCount(3)
            tw.setHorizontalHeaderLabels(["任务", "状态", "详情"])
            tw.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            tw.setColumnWidth(1, 60)
            tw.setColumnWidth(2, 220)
            tw.setRowCount(len(tasks))
            for i, t in enumerate(tasks):
                tw.setItem(i, 0, QTableWidgetItem(t.get("name", "?")))
                sts = t.get("status", "")
                si = QTableWidgetItem(sts)
                if "失败" in sts:
                    si.setForeground(QColor("#a88"))
                elif "完成" in sts:
                    si.setForeground(QColor("#8a8"))
                tw.setItem(i, 1, si)
                tw.setItem(i, 2, QTableWidgetItem((t.get("error", "") or t.get("drops", ""))[:80]))
            tw.verticalHeader().setVisible(False)
            l.addWidget(QLabel("当前运行:"))
            l.addWidget(tw)
            if drops:
                drop_items = sorted(drops.items(), key=lambda x: -x[1])
                drop_text = "  ".join(f"{name}×{qty}" for name, qty in drop_items[:10])
                l.addWidget(QLabel(f"🎒 掉落: {drop_text}"))
            if sanity:
                cur = sanity["current"]
                mx = sanity["max"]
                deficit = mx - cur
                mins = deficit * 6
                h, m = divmod(mins, 60)
                l.addWidget(QLabel(f"💊 理智: {cur}/{mx}  恢复至满需 {h}h{m:02d}m"))
        elif not runs:
            l.addWidget(QLabel("暂无运行数据\n等待 MAA 执行任务后自动生成"))
            l.addStretch()

        # Refresh button
        br = QHBoxLayout()
        refresh_btn = QPushButton("🔄 从日志刷新")
        refresh_btn.clicked.connect(lambda: (d.accept(), self.show_stats(w)))
        br.addWidget(refresh_btn)
        br.addStretch()
        br.addWidget(QPushButton("关闭", clicked=d.accept))
        l.addLayout(br)
        d.exec()

    def _stats_table(self, runs: list[dict], title: str) -> QWidget:
        """Build a scrollable table widget from a list of run dicts."""
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(4, 4, 4, 4)
        tw = QTableWidget()
        tw.setColumnCount(3)
        tw.setHorizontalHeaderLabels(["时间", "任务结果", "掉落/理智"])
        tw.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        tw.setColumnWidth(0, 120)
        tw.setColumnWidth(2, 160)
        tw.setRowCount(len(runs))
        for i, r in enumerate(runs):
            ts = r.get("ts", "")[-16:] if len(r.get("ts", "")) > 16 else r.get("ts", "")
            tw.setItem(i, 0, QTableWidgetItem(ts))
            task_text = "  ".join(f"{'✅' if v == '完成' else '❌' if v == '失败' else '⏳'}{k}" for k, v in r.get("tasks", {}).items())
            tw.setItem(i, 1, QTableWidgetItem(task_text[:120]))
            extra_parts = []
            s = r.get("sanity", {})
            if s:
                extra_parts.append(f"💊{s['current']}/{s['max']}")
            drops = r.get("drops", {})
            if drops:
                top = sorted(drops.items(), key=lambda x: -x[1])[:3]
                extra_parts.append(" ".join(f"{n}×{q}" for n, q in top))
            tw.setItem(i, 2, QTableWidgetItem("  ".join(extra_parts)[:80]))
        tw.verticalHeader().setVisible(False)
        tw.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tw.setSelectionBehavior(QAbstractItemView.SelectRows)
        vl.addWidget(tw)
        count = len(runs)
        vl.addWidget(QLabel(f"  {title}: {count} 次运行"))
        return w

    def _daily_runs(self, runs: list[dict]) -> list[dict]:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        return [r for r in runs if r.get("ts", "").startswith(today)]

    def _weekly_runs(self, runs: list[dict]) -> list[dict]:
        from datetime import datetime, timedelta
        today = datetime.now()
        start = today - timedelta(days=today.weekday())
        return [r for r in runs if r.get("ts", "") >= start.strftime("%Y-%m-%d")]

    def view_log(self, w: dict) -> None:
        lp = self.asst_log_path(w)
        if not lp.exists():
            QMessageBox.information(self.ctx._mw, "日志", "暂无日志文件")
            return
        try:
            content = lp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            QMessageBox.information(self.ctx._mw, "日志", "无法读取日志")
            return
        d = QDialog(self.ctx._mw)
        d.setWindowTitle("MAA 日志")
        d.setMinimumSize(700, 500)
        l = QVBoxLayout(d)
        te = QPlainTextEdit()
        te.setReadOnly(True)
        te.setPlainText("\n".join(content.split("\n")[-200:]))
        te.moveCursor(QTextCursor.End)
        l.addWidget(te)
        l.addWidget(QPushButton("关闭", clicked=d.accept))
        d.exec()
