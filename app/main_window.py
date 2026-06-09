from __future__ import annotations
import sys,json,os,ctypes,time,subprocess,re,shutil,io
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Any
import threading

from infrastructure.utils import (is_admin,run_as_admin,make_id,parse_maa_version,get_platform_key,_version_tuple,_rmtree_force,_find_maa_cli,setup_proxy)

_log_lock = threading.Lock()
from models.config_manager import (CONFIG_FILE,STARTUP_DIR,DEFAULT_CONFIG,migrate_v4_to_v5,load_config,save_config,set_auto_start)
from app.themes import DARK_STYLE, LIGHT_STYLE, NOTEPAPER_STYLE, BTN_DELETE
from services.update_service import UpdateCheckThread,DownloadThread,MaacliInstallThread,MaacliInstallDialog,UpdateDialog
from infrastructure.task_constants import (TASK_NAMES,TASK_DEFAULTS,EMU_PRESETS,MUMU_INSTANCE_DIRS,MUMU_CLI_CANDIDATES,CLIENT_TYPES,CF,find_mumu_cli,detect_emu_instances,EmuMonitor)
from services.emu_service import EmuService
from services.config_injector import ConfigService
from services.log_parser import LogService
from services.instance_pool import MaintService
from ui.dialogs import AccountDialog,TaskSettingsDialog
from network.api_server import ApiServer
from services.pipeline_thread import PipelineThread
from services.schedule_thread import ScheduleThread
from app.service_context import ServiceContext
from models.account import Account
from services.runner import AccountRunner
from services.launch_queue import LaunchQueue
from ui.dashboard import clear_dashboard, cleanup_emu_threads
from ui.log_window import show_log_window
from ui.settings_window import open_settings

try:
    from PySide6.QtCore import Qt,QThread,Signal,QTimer,QPointF,QSize,Slot
    from PySide6.QtGui import QFont,QPixmap,QPainter,QColor,QBrush,QPolygonF,QIcon
    from PySide6.QtWidgets import (
        QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
        QLabel,QPushButton,QListWidget,QListWidgetItem,QTableWidget,
        QTableWidgetItem,QHeaderView,QAbstractItemView,
        QSplitter,QLineEdit,QComboBox,QSpinBox,
        QGroupBox,QFormLayout,QPlainTextEdit,QCheckBox,QDialog,
        QDialogButtonBox,QMessageBox,QFileDialog,QMenu,QSystemTrayIcon,
        QFrame,QProgressBar,QScrollArea,QTabWidget,QInputDialog
    )
except ImportError:
    ctypes.windll.user32.MessageBoxW(0,"pip install PySide6","错误",0); sys.exit(1)



class MainWindow(QMainWindow):
    # ── Constants ──
    VERSION = "1.2.0"
    POLL_INTERVAL_MS = 2000
    SAVE_DEBOUNCE_MS = 300
    LOG_MAX_BYTES = 100 * 1024
    LOG_KEEP_LINES = 200
    BACKUP_MAX_COUNT = 5
    EMU_WAIT_DEFAULT = 30
    ADB_RETRY_DEFAULT = 0
    API_DEFAULT_PORT = 19999

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MAAOrch"); self.setMinimumSize(960,620)
        self.config=load_config()
        self.groups=self.config.get("groups",[]); self.warehouse=self.config.get("warehouse",[])
        self.accounts=self.config.get("accounts",[]); self.selected_group_idx=None
        self.pipeline_thread=None; self.schedule_thread=None; self.update_thread=None
        self._main_tab="accounts"
        self._running_procs={}; self._proc_status=set(); self._restart_cnt=defaultdict(int); self._cli_procs={}
        self._proc_start_times={}; self._emu_status={}
        fm=self.fontMetrics(); self._row_h=max(28,fm.height()+8); self._btn_sm=max(18,fm.height()+2); self._btn_lg=max(28,int(fm.height()*1.6))
        self._set_theme(self.config.get("appearance_mode","Dark"))
        self.ctx = ServiceContext(
            log=self._log, save=self._save, notify=self._notify,
            set_status=self._sl, set_theme=self._set_theme,
            show_dashboard=self._sad, inject_config=self._inj,
            launch_program=self._ls, start_pipeline=self._start_pipeline,
            restart_api_server=self._start_api_server,
            accounts=self.accounts, warehouse=self.warehouse,
            config=self.config, groups=self.groups,
            emu_status=self._emu_status,
            proc_status=self._proc_status,
            proc_start_times=self._proc_start_times,
            running_procs=self._running_procs,
            cli_procs=self._cli_procs,
            _mw=self,
        )
        self.emu = EmuService(self.ctx)
        self.cfg = ConfigService(self.ctx)
        self.logs = LogService(self.ctx)
        self.maint = MaintService(self.ctx)
        self.ctx.cfg = self.cfg
        self.ctx.logs = self.logs
        # ── Account runner ──
        self.runner = AccountRunner(self.ctx)
        self.runner.log_msg.connect(lambda m: self._log(m))
        self.runner.status_msg.connect(lambda m: self._sl(m))
        self.runner.account_started.connect(self._on_account_started)
        self.runner.account_finished.connect(self._on_account_finished)
        self.runner.account_error.connect(lambda aid, err: self._log(f"❌ {err}"))
        self.ctx.on_account_done = self.runner.check_processes
        # ── Launch queue (unified entry for all launch sources) ──
        self.launch_queue = LaunchQueue(self.ctx)
        self.launch_queue.log_msg.connect(lambda m: self._log(m))
        self.runner.account_finished.connect(self.launch_queue.on_account_finished)
        self.launch_queue.start()
        self._build_ui(); self.maint.restore_geometry(); self._log("══ 启动 ══")
        self._sw("accounts")
        self.launch_queue._restore()
        self.launch_queue.tick()
        self.maint.setup_tray(); self.maint.start_schedule()
        # Auto-init MAA instance pool from existing account installations
        raw_ver = self.config.get("maa_version", "")
        if not raw_ver or raw_ver == "installed":
            maas = sorted(Path(__file__).parent.glob("maa/source/MAA.exe"))
            if not maas:
                maas = sorted(Path(__file__).parent.glob("maa/v*/MAA.exe"))
            if not maas:
                maas = sorted(Path(__file__).parent.glob("accounts/*/MAA/MAA.exe"))
            if maas:
                from infrastructure.utils import parse_maa_version
                v = parse_maa_version(str(maas[0]))
                if v:
                    self.config["maa_version"] = v
            if not self.config.get("maa_version", "") or self.config.get("maa_version", "") == "installed":
                self.config["maa_version"] = ""
            self.config["maa_instances"] = 0
        from services.instance_pool import ensure_maa_instances_async
        ensure_maa_instances_async(self.ctx)
        self._proc_timer=QTimer(self); self._proc_timer.timeout.connect(self._poll); self._proc_timer.start(self.POLL_INTERVAL_MS)
        def _safe_update_emu(r):
            try:
                for x in r: self._emu_status.update({x["index"]:x})
            except Exception:
                pass

        self._emu_monitor=EmuMonitor()
        self._emu_monitor.updated.connect(_safe_update_emu)
        self._emu_monitor.start()
        self._api_server=None
        self._start_api_server()
        self._log(f"账号: {len(self.accounts)} | 仓库: {len(self.warehouse)} | 分组: {len(self.groups)}")
        if self.config.get("check_update_on_start",True): QTimer.singleShot(3000,lambda: self.maint.check_updates(True))
        self.maint.start_auto_update_timer()
        QTimer.singleShot(5000, self._health_check)
        # DWM dark title bar (Windows 10/11 dark mode)
        try:
            hwnd = int(self.winId())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            dark_mode = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(dark_mode), ctypes.sizeof(dark_mode))
        except Exception:
            pass

    def _set_theme(self, m: str) -> None:
        style = {"Dark": DARK_STYLE, "Light": LIGHT_STYLE, "Notepaper": NOTEPAPER_STYLE}.get(m, DARK_STYLE)
        self.setStyleSheet(style)
    def _start_api_server(self) -> None:
        if self._api_server: self._api_server.stop_server(); self._api_server.quit(); self._api_server.wait(1000)
        port=self.config.get("api_port",19999); token=self.config.get("api_token","")
        self._api_server=ApiServer(port,token,self)
        self._api_server.log_msg.connect(lambda m: self._log(m))
        self._api_server.start()
    def _sl(self, msg: str) -> None: self.sl.setText((msg[:100]+"…") if len(msg)>100 else msg)
    def _log(self, msg: str) -> None:
        ts=datetime.now().strftime("%H:%M:%S"); line=f"[{ts}] {msg}"
        if hasattr(self,'log_text') and self.log_text: self.log_text.appendPlainText(line)
        # Update status bar log line (last 120 chars)
        if hasattr(self, '_log_lbl') and self._log_lbl:
            self._log_lbl.setText(f"  {line[-120:]}")
        try:
            with _log_lock:
                lp=Path(__file__).parent/"debug.log"
                if lp.exists() and lp.stat().st_size>self.LOG_MAX_BYTES:
                    # Truncate efficiently: seek near end, find newline, truncate
                    data=lp.read_bytes()
                    mid=len(data)//2
                    idx=data.find(b"\n", mid)
                    if idx>0:
                        lp.write_bytes(data[idx+1:])
                    else:
                        lp.write_text("", encoding="utf-8")
                with lp.open("a",encoding="utf-8") as f: f.write(line+"\n")
        except Exception:
            try: print(line,file=__import__('sys').stderr)
            except: pass
    def _save(self) -> None:
        if hasattr(self,'_save_timer') and self._save_timer:
            self._save_timer.stop()
        else:
            self._save_timer=QTimer(self); self._save_timer.setSingleShot(True)
            self._save_timer.timeout.connect(self._do_save)
        self._save_timer.start(self.SAVE_DEBOUNCE_MS)
    def _do_save(self) -> None:
        # Sanitize adb_address (fix encoding artifacts like 27.0.0.1 → 127.0.0.1)
        for a in self.accounts:
            raw=a.get("adb_address","")
            if raw:
                m=re.match(r'^2?7\.0\.0\.1:(\d+)$',raw)
                if m: a["adb_address"]="127.0.0.1:"+m.group(1)
        self.config["groups"]=self.groups; self.config["warehouse"]=self.warehouse; self.config["accounts"]=self.accounts; save_config(self.config)
        # Auto backup AFTER save so backup captures the new state
        try:
            bp=Path(__file__).parent/"backups"; bp.mkdir(exist_ok=True)
            src=Path(__file__).parent/"config.json"
            if src.exists():
                dst=bp/f"config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                if not dst.exists(): shutil.copy2(str(src),str(dst))
            files=sorted(bp.glob("config_*.json"),key=lambda x:x.stat().st_mtime,reverse=True)
            for f in files[self.BACKUP_MAX_COUNT:]: f.unlink()
        except Exception as e:
            try: self._log(f"备份失败: {e}")
            except: pass

    def _build_ui(self) -> None:
        c = QWidget()
        self.setCentralWidget(c)
        ml = QVBoxLayout(c)
        ml.setContentsMargins(8, 4, 8, 4)
        ml.setSpacing(2)

        # Toolbar
        tb = QHBoxLayout()
        tb.setContentsMargins(4, 2, 4, 2)
        tb.setSpacing(4)
        self._sidebar_btn = QPushButton("☰")
        self._sidebar_btn.setObjectName("iconBtn")
        self._sidebar_btn.setFixedSize(28, 26)
        self._sidebar_btn.clicked.connect(self._toggle_sidebar)
        tb.addWidget(self._sidebar_btn)
        title_lbl = QLabel("MAAOrch")
        title_lbl.setStyleSheet("font-weight:600;font-size:13pt;color:#555")
        tb.addWidget(title_lbl)
        tb.addStretch()
        self._toolbar_launch_btn = QPushButton("▶ 启动队列")
        self._toolbar_launch_btn.setObjectName("startBtn")
        self._toolbar_launch_btn.setFixedHeight(28)
        self._toolbar_launch_btn.clicked.connect(self._on_toolbar_launch)
        tb.addWidget(self._toolbar_launch_btn)
        ml.addLayout(tb)

        # Main content area: sidebar + smart panel
        content_row = QHBoxLayout()
        content_row.setSpacing(4)
        content_row.setContentsMargins(0, 0, 0, 0)

        # Sidebar
        from ui.side_bar import build_side_bar
        self._side_bar = build_side_bar(self)
        content_row.addWidget(self._side_bar)

        # Smart scheduling panel (main view)
        from ui.smart_panel import build_smart_panel
        build_smart_panel(self)
        content_row.addWidget(self.smart_v, 1)

        ml.addLayout(content_row, 1)

        # ── Bottom bar: smart controls + batch operations ──
        bb = QHBoxLayout()
        bb.setContentsMargins(8, 4, 8, 4)
        bb.setSpacing(8)
        bb.setAlignment(Qt.AlignVCenter)

        from ui.smart_panel import _do_batch
        from ui.side_bar import _toggle_smart, _run_smart_all

        smart_btn = QPushButton("☐ 智能调度")
        smart_btn.setCheckable(True)
        smart_btn.setChecked(self.config.get("smart_global", {}).get("enabled", False))
        smart_btn.setFixedHeight(26)
        smart_btn.toggled.connect(lambda v: _toggle_smart(self, v))
        smart_btn.toggled.connect(lambda v: (
            smart_btn.setText("☑ 智能调度" if v else "☐ 智能调度"),
            smart_btn.setStyleSheet("color:#498205;font-weight:bold" if v else "")
        ))
        bb.addWidget(smart_btn)

        run_btn = QPushButton("▶ 立即调度全部")
        run_btn.setObjectName("startBtn")
        run_btn.setFixedHeight(26)
        run_btn.clicked.connect(lambda: _run_smart_all(self))
        bb.addWidget(run_btn)

        bb.addStretch()

        for name, act in [("批量设置","edit"),("批量入队","enq"),
                          ("批量停止","stop"),("批量删除","del")]:
            btn = QPushButton(name)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda _, a=act: _do_batch(self, a))
            bb.addWidget(btn)

        bb.addStretch()
        ml.addLayout(bb)

        # Status bar — log line + queue stats + action buttons
        sb2 = self.statusBar()
        self._log_lbl = QLabel(" 就绪")
        self._log_lbl.setStyleSheet("color:#888;font-size:8pt")
        sb2.addWidget(self._log_lbl, 1)
        self.sl = QLabel("")
        self.sl.setStyleSheet("color:#888;font-size:8pt")
        sb2.addPermanentWidget(self.sl)
        self._qsb = QLabel("")
        sb2.addPermanentWidget(self._qsb)
        # Settings button
        set_btn = QPushButton("⚙")
        set_btn.setObjectName("iconBtn")
        set_btn.setFixedSize(22, 20)
        set_btn.setToolTip("设置")
        set_btn.clicked.connect(lambda: open_settings(self))
        sb2.addPermanentWidget(set_btn)
        # Log button
        log_btn = QPushButton("📋")
        log_btn.setObjectName("iconBtn")
        log_btn.setFixedSize(22, 20)
        log_btn.setToolTip("日志")
        log_btn.clicked.connect(lambda: show_log_window(self))
        sb2.addPermanentWidget(log_btn)

        # Menu bar
        mb = self.menuBar()
        tm = mb.addMenu("工具")
        tm.addAction("检查更新", lambda: self.maint.check_updates())
        tm.addAction("检查 MAAOrch 更新", lambda: self.maint.check_orch_update())
        tm.addSeparator()
        tm.addAction("🔍 环境检测与修复", lambda: self._health_dialog())
        tm.addAction("设置", lambda: open_settings(self))
        tm.addAction("日志", lambda: show_log_window(self))
        tm.addSeparator()
        tm.addAction("退出", self.maint._quit_app)

        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence("Ctrl+Return"), self, self._start_pipeline)
        QShortcut(QKeySequence("Esc"), self, self._stop_pipeline)

    def _sw(self, tab: str) -> None:
        self._main_tab = tab
    def _st(self, tab: str) -> None:
        self._view_tab=tab; is_w=tab=="warehouse"; self.wv.setVisible(is_w); self.gv2.setVisible(not is_w)
        if is_w:
            self.tw.setObjectName("tabBtnActive"); self.tw.style().unpolish(self.tw); self.tw.style().polish(self.tw)
            self.tg2.setObjectName("tabBtn"); self.tg2.style().unpolish(self.tg2); self.tg2.style().polish(self.tg2)
            self._rw()
        else:
            self.tw.setObjectName("tabBtn"); self.tw.style().unpolish(self.tw); self.tw.style().polish(self.tw)
            self.tg2.setObjectName("tabBtnActive"); self.tg2.style().unpolish(self.tg2); self.tg2.style().polish(self.tg2)

    # Warehouse
    def _rw(self) -> None:
        ft=self.whs.text().lower(); items=[w for w in self.warehouse if ft in Path(w.get("path","")).stem.lower() or not ft]
        # Check if actually changed to avoid unnecessary rebuild
        cache_key = (ft, len(items), tuple(w["id"] for w in items))
        if getattr(self, "_rw_cache", None) == cache_key:
            return
        self._rw_cache = cache_key
        old = self.wt.rowCount()
        if old != len(items):
            self.wt.setRowCount(len(items))
        sel=self.selected_group_idx; sg=self.groups[sel] if sel is not None and sel<len(self.groups) else None
        assigned=set(r["ref"] for r in sg.get("programs",[])) if sg else set()
        for i,w in enumerate(items):
            cb=QCheckBox(); cb.setChecked(w["id"] in assigned); wid=w["id"]; cb.toggled.connect(lambda c,id=wid: self._tw(id,c))
            wr=QWidget(); wl2=QHBoxLayout(wr); wl2.setContentsMargins(0,0,0,0); wl2.setAlignment(Qt.AlignCenter); wl2.addWidget(cb); self.wt.setCellWidget(i,0,wr)
            self.wt.setItem(i,1,QTableWidgetItem(Path(w["path"]).stem))
            self.wt.setItem(i,2,QTableWidgetItem(f"{w.get('maa_type','general')} {w.get('maa_version','')}".strip()))
            db=QPushButton("✕"); db.setFixedSize(self._btn_lg,self._btn_lg)
            db.setStyleSheet(BTN_DELETE.format(r=self._btn_lg // 2))
            ri=i; db.clicked.connect(lambda c,r=ri: self._rm_wh(r))
            dw=QWidget(); dwl2=QHBoxLayout(dw); dwl2.setContentsMargins(0,0,0,0); dwl2.setAlignment(Qt.AlignCenter); dwl2.addWidget(db); self.wt.setCellWidget(i,3,dw)
    def _tw(self, wid: str, c: bool) -> None:
        sel=self.selected_group_idx
        if sel is None or sel>=len(self.groups): return
        g=self.groups[sel]
        if c: g["programs"].append({"ref":wid,"pre_delay":0}) if not any(r["ref"]==wid for r in g.get("programs",[])) else None
        else: g["programs"]=[r for r in g.get("programs",[]) if r.get("ref")!=wid]
        self._save(); self._rgl(); self._rw()
    def _add_wh(self) -> None:
        fs,_=QFileDialog.getOpenFileNames(self,"选择","","可执行文件 (*.exe);;所有文件 (*.*)"); ex={w["path"] for w in self.warehouse}
        for fp in fs:
            p=str(Path(fp))
            if p not in ex:
                e={"id":make_id(),"path":p,"args":[],"cwd":"","env":{},"maa_type":"general","maa_version":"","account_ref":"","launch_mode":"gui","task_pipeline":"","guard_enabled":False,"guard_max_restart":3,"guard_capture_log":False}
                v=None
                if Path(p).stem.lower()=="maa": e["maa_type"]="maa"; v=parse_maa_version(p)
                if v: e["maa_version"]=v
                self.warehouse.append(e); ex.add(p)
        self._save(); self._rw()
    def _rm_wh(self, row: int) -> None:
        ft=self.whs.text().lower(); items=[w for w in self.warehouse if ft in Path(w.get("path","")).stem.lower() or not ft]
        if row>=len(items): return
        w=items[row]
        if QMessageBox.question(self,"确认",f"删除 {Path(w['path']).stem}?")==QMessageBox.Yes:
            for g in self.groups: g["programs"]=[r for r in g.get("programs",[]) if r.get("ref")!=w["id"]]
            self.warehouse.remove(w); self._save(); self._rw()
    def _wh_menu(self, pos) -> None:
        row=self.wt.rowAt(pos.y()); ft=self.whs.text().lower(); items=[w for w in self.warehouse if ft in Path(w.get("path","")).stem.lower() or not ft]
        if row>=len(items): return
        w=items[row]; m=QMenu(); m.addAction("▶ 启动",lambda: self._ls(w)); m.addAction("⚙ 设置",lambda: self._ed_wh(w))
        if w.get("maa_type")!="general": m.addAction("检查更新",lambda: self.maint.cu_single(w))
        m.addSeparator(); m.addAction("删除",lambda: self._rm_wh(row)); m.exec(self.wt.viewport().mapToGlobal(pos))
    def _ed_wh(self, w: dict) -> None:
        d=QDialog(self); d.setWindowTitle("程序设置"); d.setFixedSize(450,350); l=QVBoxLayout(d)
        g1=QGroupBox("基本"); fl1=QFormLayout(g1); ae=QLineEdit(" ".join(w.get("args",[]))); fl1.addRow("参数:",ae); l.addWidget(g1)
        g2=QGroupBox("MAA"); fl2=QFormLayout(g2); fl2.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        mt=QComboBox(); mt.addItems(["general","maa","maa-cli"]); mt.setCurrentText(w.get("maa_type","general")); fl2.addRow("类型:",mt)
        lm=QComboBox(); lm.addItems(["gui","cli"]); lm.setCurrentText(w.get("launch_mode","gui")); fl2.addRow("启动:",lm)
        ac=QComboBox(); ac.addItem("—","")
        for a in self.accounts: ac.addItem(a.get("name",a["id"]),a["id"])
        i=ac.findData(w.get("account_ref",""))
        if i>=0: ac.setCurrentIndex(i)
        fl2.addRow("账号:",ac); pe=QLineEdit(w.get("task_pipeline","")); pe.setPlaceholderText("startup,fight,..."); fl2.addRow("流水线:",pe); l.addWidget(g2)
        g3=QGroupBox("守护"); fl3=QFormLayout(g3); gc=QCheckBox("崩溃重启"); gc.setChecked(w.get("guard_enabled",False)); fl3.addRow(gc); l.addWidget(g3)
        b=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        b.accepted.connect(lambda: (w.update({"args":ae.text().split() if ae.text().strip() else [],"maa_type":mt.currentText(),"launch_mode":lm.currentText(),"account_ref":ac.currentData(),"task_pipeline":pe.text().strip(),"guard_enabled":gc.isChecked()}),self._save(),d.accept()))
        b.rejected.connect(d.reject); l.addWidget(b); d.exec()

    # Group table
    def _rgt(self) -> None:
        sel=self.selected_group_idx
        if sel is None or sel>=len(self.groups): self.gt.hide(); self.ph.show(); return
        g=self.groups[sel]; wh={w["id"]:w for w in self.warehouse}; refs=g.get("programs",[])
        old = self.gt.rowCount()
        if old != len(refs):
            self.gt.setRowCount(len(refs))
        if not refs: self.gt.hide(); self.ph.setText("暂无"); self.ph.show(); return
        self.ph.hide(); self.gt.show()
        for i,ref in enumerate(refs):
            w=wh.get(ref["ref"],{}); self.gt.setItem(i,0,QTableWidgetItem(Path(w.get("path","?")).stem))
            sp=QSpinBox(); sp.setRange(0,999); sp.setValue(int(ref.get("pre_delay",0))); ri=i; sp.valueChanged.connect(lambda v,r=ri: self._sv_pd(r,v))
            sw=QWidget(); swl2=QHBoxLayout(sw); swl2.setContentsMargins(0,0,0,0); swl2.setAlignment(Qt.AlignCenter); swl2.addWidget(sp); self.gt.setCellWidget(i,1,sw)
            db=QPushButton("✕"); db.setFixedSize(self._btn_lg,self._btn_lg)
            db.setStyleSheet("QPushButton{background:transparent;color:#888;border:none}QPushButton:hover{background:#326cf3;color:#fff;border-radius:"+str(self._btn_lg//2)+"px}")
            ri2=i; db.clicked.connect(lambda c,r=ri2: self._rm_pg(r)); dw=QWidget(); dwl2=QHBoxLayout(dw); dwl2.setContentsMargins(0,0,0,0); dwl2.setAlignment(Qt.AlignCenter); dwl2.addWidget(db); self.gt.setCellWidget(i,2,dw)
    def _sv_pd(self, r: int, v: int) -> None:
        sel=self.selected_group_idx
        if sel is not None and sel<len(self.groups) and r<len(self.groups[sel].get("programs",[])): self.groups[sel]["programs"][r]["pre_delay"]=v; self._save()
    def _rm_pg(self, r: int) -> None:
        sel=self.selected_group_idx
        if sel is not None and sel<len(self.groups) and r<len(self.groups[sel].get("programs",[])): self.groups[sel]["programs"].pop(r); self._save(); self._rgt(); self._rgl()
    def _gt_menu(self, pos) -> None:
        row=self.gt.rowAt(pos.y())
        if row<0 or self.selected_group_idx is None: return
        refs=self.groups[self.selected_group_idx].get("programs",[])
        if row>=len(refs): return
        w=next((x for x in self.warehouse if x["id"]==refs[row].get("ref")),None)
        if not w: return
        m=QMenu(); m.addAction("▶ 启动",lambda: self._ls(w)); m.addAction("⚙ 设置",lambda: self._ed_wh(w)); m.addAction("从分组移除",lambda: self._rm_pg(row)); m.exec(self.gt.viewport().mapToGlobal(pos))
    def _gt_launch(self) -> None:
        row=self.gt.currentRow()
        if row<0 or self.selected_group_idx is None: return
        refs=self.groups[self.selected_group_idx].get("programs",[])
        if row<len(refs): w=next((x for x in self.warehouse if x["id"]==refs[row].get("ref")),None)
        if w: self._ls(w)

    # Groups
    def _rgl(self) -> None:
        self.gl_.blockSignals(True); self.gl_.clear()
        for i,g in enumerate(self.groups):
            c=len(g.get("programs",[])); ic="∥" if g.get("mode")=="parallel" else "→"
            rw=QWidget(); rw.setMinimumHeight(self._row_h); rl2=QHBoxLayout(rw); rl2.setContentsMargins(6,2,4,2)
            lb=QLabel(f"#{i+1} {g['name']}  {ic} {c}个"); lb.setAttribute(Qt.WA_TransparentForMouseEvents); rl2.addWidget(lb,1)
            rw.mousePressEvent=lambda e,idx=i: self.gl_.setCurrentRow(idx)
            it=QListWidgetItem(); it.setSizeHint(QSize(0,self._row_h)); self.gl_.addItem(it); self.gl_.setItemWidget(it,rw)
        if self.selected_group_idx is not None and self.selected_group_idx<len(self.groups): self.gl_.setCurrentRow(self.selected_group_idx)
        self.gl_.blockSignals(False)
    def _on_group(self, idx: int) -> None:
        if idx<0: return
        self.selected_group_idx=idx; self._sgd()
    def _sgd(self) -> None:
        idx=self.selected_group_idx
        if idx is None or idx>=len(self.groups): self.gs.hide(); self.gt.hide(); self.ph.show(); return
        g=self.groups[idx]; self.gs.show(); self.gn.setText(g.get("name","")); self.gm.setCurrentText("并行" if g.get("mode")=="parallel" else "串行"); self._rgt()
    def _sv_gn(self) -> None:
        i=self.selected_group_idx
        if i is not None and i<len(self.groups): self.groups[i]["name"]=self.gn.text() or "未命名"; self._save(); self._rgl()
    def _sv_gm(self) -> None:
        i=self.selected_group_idx
        if i is not None and i<len(self.groups): self.groups[i]["mode"]="parallel" if self.gm.currentText()=="并行" else "sequential"; self._save()
    def _add_group(self) -> None:
        n=len(self.groups)+1; self.groups.append({"name":f"新分组 {n}","mode":"parallel","post_delay":3,"programs":[]}); self._save(); self._rgl(); self.selected_group_idx=len(self.groups)-1; self.gl_.setCurrentRow(self.selected_group_idx)
    def _del_group(self) -> None:
        i=self.selected_group_idx
        if i is not None and i<len(self.groups) and QMessageBox.question(self,"确认",f"删除 {self.groups[i]['name']}?")==QMessageBox.Yes: self.groups.pop(i); self.selected_group_idx=min(i,len(self.groups)-1) if self.groups else None; self._save(); self._rgl()

    # Accounts
    def _ra(self) -> None:
        if not self.accounts: self.ad.setVisible(False); return
        self.ad.setVisible(True)
        search=getattr(self,'asrch',None); filter_text=search.text().strip().lower() if search and search.text() else ""
        visible=[a for a in self.accounts if not filter_text or filter_text in a.get("name", "").lower()]
        old = self.at.rowCount()
        if old != len(visible):
            self.at.setRowCount(len(visible))
        for i,a in enumerate(visible):
            ni=QTableWidgetItem(a.get("name", "")); ni._acc_id=a["id"]; self.at.setItem(i,0,ni); self.at.setItem(i,1,QTableWidgetItem(a.get("game_client","")))
    def _on_acc_sel(self) -> None:
        sel=self.at.currentRow()
        if sel>=0:
            it=self.at.item(sel,0)
            if it and hasattr(it,'_acc_id'):
                for j,a in enumerate(self.accounts):
                    if a["id"]==it._acc_id: self._sad(j); break
    def _toggle_sidebar(self) -> None:
        if hasattr(self, '_side_bar') and self._side_bar:
            visible = not self._side_bar.isVisible()
            self._side_bar.setVisible(visible)

    def _on_toolbar_launch(self) -> None:
        if hasattr(self, 'launch_queue') and self.launch_queue:
            if self.launch_queue.is_paused:
                self.launch_queue.resume()
                self._toolbar_launch_btn.setText("⏸ 暂停队列")
            else:
                self.launch_queue.pause()
                self._toolbar_launch_btn.setText("▶ 启动队列")

    def _clear_dashboard(self) -> None:
        clear_dashboard(self)

    def _cleanup_emu_threads(self) -> None:
        cleanup_emu_threads(self)

    def _sad(self, row: int) -> None:
        from ui.account_detail import open_account_detail
        open_account_detail(self, row)

    def _refresh_instance_list_async(self, combo, saved_idx: str | None = None, saved_name: str | None = None) -> None:
        self.emu.refresh_instance_list(combo, saved_idx, saved_name)
    def _test_adb(self, a: dict) -> None: self.emu.test_adb(a)
    def _browse_adb(self, le, ac: dict) -> None: self.emu.browse_adb(le, ac)
    def _browse_file(self, le, ac: dict, key: str) -> None: self.emu.browse_file(le, ac, key)
    def _adb_screenshot(self, a: dict) -> None: self.emu.screenshot(a)
    def _stop_emu(self, a: dict) -> None: self.emu.stop_emu(a)
    def _scan_port(self, a: dict, path_edit, addr_edit) -> None: self.emu.scan_port(a, path_edit, addr_edit)
    def _maa_asst_log(self, w: dict) -> Path: return self.logs.maa_asst_log(w)
    def _switch_maa_version(self, w: dict, channel: str) -> None: return self.logs.switch_maa_version(w,channel)
    def _parse_maa_log(self, w: dict, tail: int = 500) -> list[dict]: return self.logs.parse_maa_log(w,tail=500)
    def _show_maa_stats(self, w: dict) -> None: return self.logs.show_maa_stats(w)
    def _view_maa_log(self, w: dict) -> None: return self.logs.view_maa_log(w)
    def _scan(self, a: dict, cb) -> None: self.emu.scan(a, cb)
    def _add_acc(self) -> None:
        d=AccountDialog(self)
        if d.exec()==QDialog.Accepted: self.accounts.append(Account.from_dict(d.r)); self._save(); self._ra()
    def _del_acc(self) -> None:
        row=self.at.currentRow()
        if row<0: return
        it=self.at.item(row,0)
        if not it or not hasattr(it,'_acc_id'): return
        aid=it._acc_id
        for j,a in enumerate(self.accounts):
            if a["id"]==aid:
                if QMessageBox.question(self,"确认",f"删除 {a['name']}?")==QMessageBox.Yes:
                    for w in self.warehouse:
                        if w.get("account_ref")==a["id"]: w["account_ref"]=""
                    self.accounts.pop(j); self._save(); self._ra()
                return
    def _ac_menu(self, pos) -> None:
        row=self.at.rowAt(pos.y())
        if row<0: return
        it=self.at.item(row,0)
        if not it or not hasattr(it,'_acc_id'): return
        aid=it._acc_id
        for j,a in enumerate(self.accounts):
            if a["id"]==aid: orig=j; break
        else: return
        m=QMenu(); m.addAction("▶ 启动",lambda: self._la(orig)); m.addAction("📤 导出",lambda: self._export_acc(orig)); m.addAction("✕ 删除",lambda: self._del_acc()); m.exec(self.at.viewport().mapToGlobal(pos))
    def _export_acc(self, row: int) -> None:
        if row<0 or row>=len(self.accounts): return
        a=self.accounts[row]
        fp,_=QFileDialog.getSaveFileName(self,"导出账号",f"{a['name']}.json","JSON (*.json)")
        if fp:
            Path(fp).write_text(json.dumps({"name":a.get("name"),"game_client":a.get("game_client"),"adb_path":a.get("adb_path"),"adb_address":a.get("adb_address"),"connection_preset":a.get("connection_preset"),"touch_mode":a.get("touch_mode"),"account_switch":a.get("account_switch"),"emu_instance_index":a.get("emu_instance_index"),"emu_instance_name":a.get("emu_instance_name"),"emu_wait":a.get("emu_wait", 30),"task_settings":a.get("task_settings",{}),"post_action":a.get("post_action"),"task_pipeline":(progs[0].get("task_pipeline","") if (progs:=[w for w in self.warehouse if w.get("account_ref")==a["id"]]) else "")},ensure_ascii=False,indent=2),encoding="utf-8")
    def _la(self, row: int) -> None:
        """Manual single-account launch → enqueue with highest priority."""
        if row < 0 or row >= len(self.accounts):
            return
        aid = self.accounts[row]["id"]
        self.launch_queue.enqueue(aid, "manual", priority=0)
        self.launch_queue.tick()

    def _la_all(self) -> None:
        """Batch enqueue all accounts with schedule priority."""
        self._log("══ 全部账号入队 ══")
        self.launch_queue.enqueue_batch("manual", priority=0)
        self.launch_queue.tick()

    @Slot(str)
    def _on_account_started(self, aid: str) -> None:
        a = next((x for x in self.accounts if x["id"] == aid), None)
        if a:
            self._sad(self.accounts.index(a))

    def _on_account_finished(self, data: tuple) -> None:
        aid, exit_code, tasks = data
        a = next((x for x in self.accounts if x["id"] == aid), None)
        if a:
            import time as _time
            if exit_code != 0 and not tasks:
                a["smart_last_error"] = _time.time()
            else:
                a["smart_last_error"] = 0
            if a.get("smart_pending", False):
                a["smart_pending"] = False
                self._log(f"🧠 {a.get('name', aid)} 到点补跑")
                self.launch_queue.enqueue(aid, "schedule", priority=1)
                self.launch_queue.tick()
            if self._main_tab == "accounts":
                self._sad(self.accounts.index(a))

    # Legacy launch helpers (kept for pipeline_thread / warehouse quick-launch)
    def _ls(self, w: dict) -> None:
        try:
            args=w.get("args",[]); cwd=w.get("cwd","") or None; env={k:v for k,v in (w.get("env") or {}).items()} or None; exe=w["path"]; lm=w.get("launch_mode","gui")
            if w.get("account_ref") and lm=="cli":
                ac=next((a for a in self.accounts if a["id"]==w["account_ref"]),None)
                if ac:
                    cl=_find_maa_cli()
                    if not cl:
                        d=MaacliInstallDialog(self); d.start(str(Path(__file__).parent/"maa-cli"))
                        if d.exec()!=QDialog.Accepted: return; cl=_find_maa_cli()
                    if cl:
                        md=Path(w["path"]).parent; lc=md/Path(cl).name
                        if not lc.exists() or lc.stat().st_mtime<Path(cl).stat().st_mtime: shutil.copy2(cl,str(lc))
                        tn=self._gtc(ac,w)
                        if tn: env=(env or os.environ.copy()); env["MAA_CONFIG_DIR"]=str(md/"config"); exe=str(lc); args=["run",tn]+args; cwd=str(md)
            kwargs={"shell":False,"cwd":cwd,"env":env}
            if lm=="cli": kwargs["stdout"]=subprocess.PIPE; kwargs["stderr"]=subprocess.PIPE; kwargs["creationflags"]=CF
            p=subprocess.Popen([exe]+args,**kwargs); self._proc_status.add(w["id"])
            self._proc_start_times[w["id"]]=time.time()
            if lm=="cli": self._cli_procs[w["id"]]=p
            else: self._running_procs[w["id"]]=p
            self._log(f"✓ 启动 {Path(w['path']).stem} PID={p.pid}")
        except Exception as e: self._log(f"❌ 失败: {e}"); QMessageBox.critical(self,"失败",str(e))
    def _launch_raw(self, w: dict) -> None:
        """Launch MAA GUI (no auto-run) for manual config."""
        exe=w.get("path","")
        if not exe: return
        md=Path(exe).parent/"config"; gj=md/"gui.json"
        if gj.exists():
            try:
                d=json.loads(gj.read_text(encoding="utf-8"))
                cfg=d.setdefault("Configurations",{}).setdefault("Default",{}).setdefault("Start",{})
                if cfg.get("RunDirectly","")=="True":
                    cfg["RunDirectly"]="False"
                    gj.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
            except: pass
        try:
            subprocess.Popen([exe],creationflags=CF)
            self._log("已启动 MAA 主程序")
        except Exception as e: self._log(f"❌ 启动失败: {e}")
    def _gtc(self, ac: dict, w: dict) -> str | None: return self.cfg.gtc(ac, w)
    def _inj(self, w: dict, ac: dict) -> None: self.cfg.inject(w, ac)
    # Pipeline
    def _start_pipeline(self) -> None:
        if getattr(self, "_pipeline_launching", False):
            return
        if not self.groups:
            if hasattr(self, 'launch_queue'):
                n = sum(1 for a in self.accounts if a.get("id", "") in {
                    w.get("account_ref") for w in self.warehouse if w.get("account_ref")}
                    and a.get("emu_instance_index", "") and a.get("adb_address", "").strip())
                self._log(f"无流水线分组，跳过 {n} 个账号的自动启动" if n else "无流水线分组")
            return
        if self.pipeline_thread and self.pipeline_thread.isRunning(): return
        self._pipeline_launching = True
        self._log("流水线启动")
        # Collect emulators to launch
        to_launch=[]
        launched=set()
        cli=find_mumu_cli()
        for a in self.accounts:
            emu_idx=a.get("emu_instance_index","")
            if a.get("emu_launch") and emu_idx and emu_idx not in launched and cli:
                to_launch.append((cli,emu_idx,a["name"],a.get("emu_wait", 30)))
                launched.add(emu_idx)
        def _start_thread():
            self._pipeline_launching = False
            old = getattr(self, "pipeline_thread", None)
            if old:
                try: old.progress.disconnect(); old.program_started.disconnect(); old.finished.disconnect()
                except: pass
            self.pipeline_thread=PipelineThread(self.groups,self.warehouse,self.accounts,self.cfg,self)
            self.pipeline_thread.progress.connect(lambda m:(self.sl.setText(m),self._log(m)))
            self.pipeline_thread.program_started.connect(lambda n,ok: self._log(f"启动 {n}" if ok else f"失败 {n}"))
            self.pipeline_thread.finished.connect(lambda s: None)
            self.pipeline_thread.start()
        def _launch_next(i=0):
            if not self.isVisible(): return
            if i>=len(to_launch): _start_thread(); return
            cli,emu_idx,name,wait=to_launch[i]
            self._log(f"启动模拟器 #{emu_idx} ({name})")
            try: subprocess.run([cli,"control","--vmindex",str(emu_idx),"launch"],creationflags=CF,timeout=15)
            except Exception as e: self._log(f"启动失败: {e}")
            self.sl.setText(f"模拟器 {i+1}/{len(to_launch)}")
            QTimer.singleShot(max(500,int(wait*1000//len(to_launch))),lambda i=i: _launch_next(i+1))
        if to_launch: _launch_next()
        else: _start_thread()
    def _stop_pipeline(self) -> None:
        if self.pipeline_thread and self.pipeline_thread.isRunning():
            self.pipeline_thread.stop()
    def _pause_pipeline(self) -> None:
        if self.pipeline_thread and self.pipeline_thread.isRunning():
            if getattr(self.pipeline_thread, "pause_flag", False):
                self.pipeline_thread.resume(); self._log("流水线已继续")
            else:
                self.pipeline_thread.pause(); self._log("流水线已暂停")

    def _poll(self) -> None:
        self.maint.poll()
        if hasattr(self, "launch_queue"):
            lq = self.launch_queue
            ac = lq.active_count
            qc = lq.pending_count
            if ac:
                self._qsb.setText(f"▶{ac}" + (f"  ⏳{qc}" if qc else ""))
            elif qc:
                self._qsb.setText(f"⏳{qc}")
            else:
                self._qsb.setText("")
            # Status overview log (every 30 seconds)
            now = int(__import__("time").time())
            if now % 30 == 0:
                total = len(self.accounts)
                errors = sum(1 for a in self.accounts if a.get("consecutive_failures", 0) >= 6)
                self._log(f"[状态] 运行中: {ac}/{total} | 队列: {qc} | 错误: {errors}")
    def _smart_tick(self) -> None:
        sg = self.config.get("smart_global", {})
        if not sg.get("enabled", False) or not hasattr(self, "launch_queue"):
            return
        now = datetime.now()
        minute_key = now.strftime("%H:%M")
        if getattr(self, "_last_smart_minute", "") == minute_key:
            return
        self._last_smart_minute = minute_key
        from services.smart_scheduler import get_tasks_for_account, is_infrast_time, _check_sanity_above_threshold, _get_material_stage
        infrast_times = sg.get("infrast_times", ["04:00", "16:00"])
        is_time_trigger = is_infrast_time(now, infrast_times)
        count = 0
        skipped_no_cfg = 0
        for a in self.accounts:
            aid = a.get("id", "")
            if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
                skipped_no_cfg += 1
                continue
            if self.launch_queue.is_queued(aid):
                continue
            running = self.launch_queue.is_running(aid) or (getattr(self, "runner", None) and self.runner.is_running(aid))
            if running:
                if is_time_trigger and not a.get("smart_pending", False):
                    a["smart_pending"] = True
                continue
            last_error = a.get("smart_last_error", 0)
            if last_error and time.time() - last_error < 300 and not getattr(self, "_smart_force", False):
                continue
            should_launch = False
            if is_time_trigger or getattr(self, "_smart_force", False):
                should_launch = True
            else:
                threshold = sg.get("threshold", 80)
                if _check_sanity_above_threshold(aid, threshold):
                    should_launch = True
                elif a.get("smart_materials_enabled", True):
                    mat_stage = _get_material_stage(a, sg)
                    if mat_stage:
                        should_launch = True
            if should_launch:
                if getattr(self, "_smart_force", False):
                    tasks = ["StartUp", "Award", "Fight", "Infrast", "Recruit", "Mall", "CloseDown"]
                    if (hasattr(self, "_smart_anni_cb") and self._smart_anni_cb.isChecked()
                        and a.get("smart_annihilation_enabled", True) and a.get("smart_annihilation", "")):
                        tasks.insert(2, "Annihilation")
                else:
                    tasks = get_tasks_for_account(a, sg)
                if tasks:
                    plan_txt = ",".join(tasks)
                    a["smart_plan"] = plan_txt
                    self.launch_queue.enqueue(aid, "schedule", priority=1)
                    count += 1
        if count:
            self._log(f"🧠 智能调度: {count} 个账号已入队" + (f" ({skipped_no_cfg}个缺配置跳过)" if skipped_no_cfg else ""))
            self.launch_queue.tick()
        else:
            reasons = []
            if skipped_no_cfg:
                reasons.append(f"{skipped_no_cfg}个缺配置")
            reasons.append("体力不足/无任务到达")
            self._log("🧠 智能调度: 暂无账号需要调度（" + "，".join(reasons) + "）")
    def _notify(self, msg: str, is_error: bool = False) -> None: self.maint.notify(msg, is_error)
    def _cu_single(self, w: dict) -> None: self.maint.cu_single(w)
    def _restore_geometry(self) -> None: self.maint.restore_geometry()
    def _setup_tray(self) -> None: self.maint.setup_tray()
    def _show_tray(self) -> None: self.maint.show_tray()
    def _show_todo(self) -> None:
        issues = []
        if not self.config.get("maa_version", ""):
            issues.append(("系统", "未下载 MAA，请点击账号页的 ⬇ 批量MAA 下载"))
        for a in self.accounts:
            aid = a.get("id", "")
            name = a.get("name", "").strip() or aid[:6]
            if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
                issues.append((name, "未配置 ADB 地址或模拟器实例"))
            if self.config.get("smart_global", {}).get("enabled", False):
                if not a.get("smart_stage", ""):
                    issues.append((name, "智能模式开启但未设默认关卡"))
        self._update_todo_badge(len(issues))
        if not issues:
            sg = self.config.get("smart_global", {})
            if sg.get("enabled", False):
                for a in self.accounts:
                    materials_enabled = a.get("smart_materials_enabled", True)
                    if materials_enabled:
                        from pathlib import Path
                        dp = Path(__file__).parent / "accounts" / a.get("id", "") / "depot.json"
                        if not dp.exists():
                            issues.append((a.get("name", "?"), "材料监控已开启但未跑过仓库识别，等待下次 04:00 Depot"))
            if not issues:
                QMessageBox.information(self, "📋 配置待办", "所有账号配置齐全，暂无待办项。")
                return
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
        d = QDialog(self)
        d.setWindowTitle(f"📋 配置待办 ({len(issues)})")
        d.setMinimumSize(500, 350)
        l = QVBoxLayout(d)
        l.addWidget(QLabel("以下账号存在未完成的配置项："))
        for acct, issue in issues:
            l.addWidget(QLabel(f"  ⚠ {acct} — {issue}"))
        btn = QPushButton("知道了")
        btn.clicked.connect(d.accept)
        l.addWidget(btn)
        d.exec()

    def _health_check(self) -> None:
        """Background health check on startup."""
        try:
            from services.health_check import run_health_check
            report = run_health_check(self.ctx)
            n = report.error_count + report.warn_count
            if n:
                self._log(f"⚠ 环境检测: {report.error_count} 个错误, {report.warn_count} 个警告")
            else:
                self._log("✅ 环境检测: 全部正常")
        except Exception as e:
            self._log(f"环境检测失败: {e}")

    def _health_dialog(self) -> None:
        """Open health check dialog."""
        from services.health_check import run_health_check, show_health_dialog
        report = run_health_check(self.ctx)
        show_health_dialog(self, report)

    def _update_todo_badge(self, count: int = -1) -> None:
        if count < 0:
            count = 0
            for a in self.accounts:
                if not a.get("adb_address", "").strip() and not a.get("emu_instance_index", ""):
                    count += 1
                    continue
                progs = [w for w in self.warehouse if w.get("account_ref") == a.get("id", "")]
                if not progs:
                    count += 1
            if self.config.get("smart_global", {}).get("enabled", False):
                for a in self.accounts:
                    if not a.get("smart_stage", ""):
                        count += 1
                for a in self.accounts:
                    if a.get("smart_materials_enabled", True):
                        dp = Path(__file__).parent / "accounts" / a.get("id", "") / "depot.json"
                        if not dp.exists():
                            count += 1
        pass  # badge removed with tab bar

    def closeEvent(self, e) -> None:
        if not self.isMinimized():
            g=self.geometry(); self.config["window_geometry"]=f"{g.width()}x{g.height()}+{g.x()}+{g.y()}"
        if self.config.get("minimize_to_tray",True) and hasattr(self,'tray_icon') and self.tray_icon:
            self.hide(); e.ignore()
        else:
            self._do_save(); e.accept(); QApplication.quit()
    def _tlog(self) -> None: show_log_window(self)
    def _settings(self) -> None: open_settings(self)
if __name__=="__main__":
    if not is_admin() and "--no-elevate" not in sys.argv:
        run_as_admin(); sys.exit(0)
    # Single instance: find existing window and activate it
    import ctypes as _ct
    hwnd=_ct.windll.user32.FindWindowW(None,"MAAOrch")
    if hwnd:
        _ct.windll.user32.ShowWindow(hwnd,9); _ct.windll.user32.SetForegroundWindow(hwnd)
        sys.exit(0)
    app=QApplication(sys.argv); app.setStyle("Fusion"); app.setQuitOnLastWindowClosed(False)
    win=MainWindow(); win.show(); sys.exit(app.exec())
