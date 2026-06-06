from __future__ import annotations
import sys,json,os,ctypes,time,subprocess,re,shutil,io
from pathlib import Path
from datetime import datetime,time as dtime
from collections import defaultdict
from typing import Any
import threading

from utils import (is_admin,run_as_admin,make_id,parse_maa_version,get_platform_key,_version_tuple,_rmtree_force,_find_maa_cli,setup_proxy)
from config import (CONFIG_FILE,STARTUP_DIR,DEFAULT_CONFIG,migrate_v4_to_v5,load_config,save_config,set_auto_start)
from themes import DARK_STYLE, LIGHT_STYLE
from updater import UpdateCheckThread,DownloadThread,MaacliInstallThread,MaacliInstallDialog,UpdateDialog
from task_constants import (TASK_NAMES,TASK_DEFAULTS,EMU_PRESETS,MUMU_INSTANCE_DIRS,MUMU_CLI_CANDIDATES,CLIENT_TYPES,CF,find_mumu_cli,detect_emu_instances,EmuMonitor)
from emu_ops import EmuService
from config_ops import ConfigService
from log_ops import LogService
from maint_ops import MaintService
from dialogs import ScheduleDialog,SettingsDialog,AccountDialog,TaskSettingsDialog
from api_server import ApiServer
from schedule_thread import ScheduleThread
from callbacks import ServiceContext
from account import Account
from runner import AccountRunner
from launch_queue import LaunchQueue
from ui.dashboard import build_account_dashboard, clear_dashboard, cleanup_emu_threads
from ui.accounts_panel import build_accounts_panel
from ui.queue_panel import build_queue_panel, refresh_queue_view
from ui.config_cards import build_config_cards, refresh_config_cards
from ui.schedule_panel import build_schedule_panel, refresh_schedule_view

try:
    from PySide6.QtCore import Qt,QThread,Signal,QTimer,QPointF,QSize
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
        self.launch_queue._tick()
        self.maint.setup_tray(); self.maint.start_schedule()
        self._proc_timer=QTimer(self); self._proc_timer.timeout.connect(self._poll); self._proc_timer.start(self.POLL_INTERVAL_MS)
        self._emu_monitor=EmuMonitor()
        self._emu_monitor.updated.connect(lambda r: (_safe_update_emu(r)))
        self._emu_monitor.start()

        def _safe_update_emu(r):
            try:
                for x in r: self._emu_status.update({x["index"]:x})
            except Exception:
                pass
        self._api_server=None
        self._start_api_server()
        self._log(f"账号: {len(self.accounts)} | 仓库: {len(self.warehouse)} | 分组: {len(self.groups)}")
        if self.config.get("check_update_on_start",True): QTimer.singleShot(3000,lambda: self.maint.check_updates(True))
        self.maint.start_auto_update_timer()

    def _set_theme(self, m: str) -> None: self.setStyleSheet(DARK_STYLE if m=="Dark" else LIGHT_STYLE)
    def _start_api_server(self) -> None:
        if self._api_server: self._api_server.stop_server(); self._api_server.quit(); self._api_server.wait(1000)
        port=self.config.get("api_port",19999); token=self.config.get("api_token","")
        self._api_server=ApiServer(port,token,self)
        self._api_server.log_msg.connect(lambda m: self._log(m))
        self._api_server.start()
    def _sl(self, msg: str) -> None: self.sl.setText((msg[:100]+"…") if len(msg)>100 else msg)
    def _log(self, msg: str) -> None:
        ts=datetime.now().strftime("%H:%M:%S"); line=f"[{ts}] {msg}"
        if hasattr(self,'log_text'): self.log_text.appendPlainText(line)
        try:
            lp=Path(__file__).parent/"debug.log"
            if lp.exists() and lp.stat().st_size>self.LOG_MAX_BYTES:
                lines=lp.read_text(encoding="utf-8").split("\n")
                lp.write_text("\n".join(lines[-self.LOG_KEEP_LINES:])+"\n",encoding="utf-8")
            with lp.open("a",encoding="utf-8") as f: f.write(line+"\n")
        except Exception:
            try: print(line,file=__import__('sys').stderr)
            except: pass
    def _save(self) -> None:
        # Debounce: coalesce rapid saves within 300ms
        if hasattr(self,'_save_timer') and self._save_timer:
            self._save_timer.stop()
        self._save_timer=QTimer(self); self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._do_save)
        self._save_timer.start(self.SAVE_DEBOUNCE_MS)
    def _do_save(self) -> None:
        # Sanitize adb_address (fix encoding artifacts)
        for a in self.accounts:
            raw=a.get("adb_address","")
            if raw and not raw.startswith("127.0.0.1:"):
                m=re.search(r':(\d+)$',raw)
                if m: a["adb_address"]="127.0.0.1:"+m.group(1)
        # Auto backup config
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
        self.config["groups"]=self.groups; self.config["warehouse"]=self.warehouse; self.config["accounts"]=self.accounts; save_config(self.config)

    def _build_ui(self) -> None:
        c = QWidget()
        self.setCentralWidget(c)
        ml = QVBoxLayout(c)
        ml.setContentsMargins(8, 8, 8, 4)
        ml.setSpacing(4)

        # Top tab bar — modern flat tabs with bottom indicator
        tb = QFrame()
        tb.setStyleSheet("QFrame{background:rgba(255,255,255,0.02);border-bottom:1px solid #333}")
        th = QHBoxLayout(tb)
        th.setContentsMargins(8, 2, 4, 0)
        th.setSpacing(0)
        self.tg = QPushButton("  👤 账号  ")
        self.ta = QPushButton("  ⏳ 队列  ")
        self.tl = QPushButton("  📋 日志  ")
        self.tc = QPushButton("  ⚡ 配置  ")
        self.ts = QPushButton("  ⚙ 调度  ")
        for btn, key in [
            (self.tg, "accounts"),
            (self.ta, "queue"),
            (self.tl, "logs"),
            (self.tc, "config"),
            (self.ts, "schedule"),
        ]:
            btn.setObjectName("tabBtn")
            btn.setFlat(True)
            btn.setStyleSheet("QPushButton{color:#888;border:none;padding:6px 10px;font-size:10pt;border-bottom:2px solid transparent}QPushButton:hover{color:#ddd;background:rgba(255,255,255,0.03)}QPushButton#tabBtnActive{color:#fff;border-bottom:2px solid #5a9}")
            btn.clicked.connect(lambda _, k=key: self._sw(k))
            th.addWidget(btn)
        th.addStretch()
        self.qs = QPushButton("▶ 启动全部")
        self.qs.setObjectName("startBtn")
        self.qs.clicked.connect(lambda: self._la_all())
        th.addWidget(self.qs)
        ml.addWidget(tb)

        # Accounts panel
        build_accounts_panel(self)
        self.av.hide()
        ml.addWidget(self.av, 1)

        # Queue panel
        build_queue_panel(self)
        self.qv.hide()
        ml.addWidget(self.qv, 1)

        # Log panel (full tab)
        self.lv = QWidget()
        lvl = QVBoxLayout(self.lv)
        lvl.setContentsMargins(4, 4, 4, 4)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(2000)
        lvl.addWidget(self.log_text)
        log_btn_row = QHBoxLayout()
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(lambda: self.log_text.clear())
        log_btn_row.addWidget(clear_btn)
        log_btn_row.addStretch()
        lvl.addLayout(log_btn_row)
        self.lv.hide()
        ml.addWidget(self.lv, 1)

        # Config cards panel
        build_config_cards(self)
        self.cv.hide()
        ml.addWidget(self.cv, 1)

        # Schedule panel
        build_schedule_panel(self)
        self.sv.hide()
        ml.addWidget(self.sv, 1)

        # Status bar — clean single line
        sb2 = self.statusBar()
        sb2.setStyleSheet("QStatusBar{background:#111;border-top:1px solid #333;padding:1px 8px;font-size:9pt}")
        self.sl = QLabel(" 就绪")
        self.sl.setStyleSheet("color:#aaa")
        sb2.addWidget(self.sl)
        # Queue summary on right
        self._qsb = QLabel("")
        self._qsb.setStyleSheet("color:#666")
        sb2.addPermanentWidget(self._qsb)

        # Menu bar
        mb = self.menuBar()
        tm = mb.addMenu("工具")
        tm.addAction("定时", self._sch)
        tm.addAction("检查更新", lambda: self.maint.check_updates())
        tm.addAction("设置", self._settings)
        tm.addAction("日志", self._tlog)

        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence("Ctrl+Return"), self, self._start_pipeline)
        QShortcut(QKeySequence("Esc"), self, self._stop_pipeline)

    def _sw(self, tab: str) -> None:
        self._main_tab = tab
        self.av.setVisible(tab == "accounts")
        self.qv.setVisible(tab == "queue")
        self.lv.setVisible(tab == "logs")
        self.cv.setVisible(tab == "config")
        self.sv.setVisible(tab == "schedule")
        for btn, key in [(self.tg, "accounts"), (self.ta, "queue"), (self.tl, "logs"), (self.tc, "config"), (self.ts, "schedule")]:
            btn.setObjectName("tabBtnActive" if tab == key else "tabBtn")
            btn.style().unpolish(btn); btn.style().polish(btn)
        if tab == "accounts":
            self._ra()
            if self.accounts: self.at.setCurrentCell(0, 0)
        elif tab == "queue":
            refresh_queue_view(self)
        elif tab == "logs":
            self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
        elif tab == "config":
            refresh_config_cards(self)
        elif tab == "schedule":
            refresh_schedule_view(self)
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
        self.wt.setRowCount(0); self.wt.setRowCount(len(items))
        sel=self.selected_group_idx; sg=self.groups[sel] if sel is not None and sel<len(self.groups) else None
        assigned=set(r["ref"] for r in sg.get("programs",[])) if sg else set()
        for i,w in enumerate(items):
            cb=QCheckBox(); cb.setChecked(w["id"] in assigned); wid=w["id"]; cb.toggled.connect(lambda c,id=wid: self._tw(id,c))
            wr=QWidget(); wl2=QHBoxLayout(wr); wl2.setContentsMargins(0,0,0,0); wl2.setAlignment(Qt.AlignCenter); wl2.addWidget(cb); self.wt.setCellWidget(i,0,wr)
            self.wt.setItem(i,1,QTableWidgetItem(Path(w["path"]).stem))
            self.wt.setItem(i,2,QTableWidgetItem(f"{w.get('maa_type','general')} {w.get('maa_version','')}".strip()))
            db=QPushButton("✕"); db.setFixedSize(self._btn_lg,self._btn_lg)
            db.setStyleSheet("QPushButton{background:transparent;color:#888;border:none}QPushButton:hover{background:#d32f2f;color:#fff;border-radius:"+str(self._btn_lg//2)+"px}")
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
        self.gt.setRowCount(0)
        if not refs: self.gt.hide(); self.ph.setText("暂无"); self.ph.show(); return
        self.ph.hide(); self.gt.show(); self.gt.setRowCount(len(refs))
        for i,ref in enumerate(refs):
            w=wh.get(ref["ref"],{}); self.gt.setItem(i,0,QTableWidgetItem(Path(w.get("path","?")).stem))
            sp=QSpinBox(); sp.setRange(0,999); sp.setValue(int(ref.get("pre_delay",0))); ri=i; sp.valueChanged.connect(lambda v,r=ri: self._sv_pd(r,v))
            sw=QWidget(); swl2=QHBoxLayout(sw); swl2.setContentsMargins(0,0,0,0); swl2.setAlignment(Qt.AlignCenter); swl2.addWidget(sp); self.gt.setCellWidget(i,1,sw)
            db=QPushButton("✕"); db.setFixedSize(self._btn_lg,self._btn_lg)
            db.setStyleSheet("QPushButton{background:transparent;color:#888;border:none}QPushButton:hover{background:#d32f2f;color:#fff;border-radius:"+str(self._btn_lg//2)+"px}")
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
        self.at.setRowCount(0)
        if not self.accounts: self.ad.setVisible(False); return
        self.ad.setVisible(True)
        search=getattr(self,'asrch',None); filter_text=search.text().strip().lower() if search and search.text() else ""
        visible=[a for a in self.accounts if not filter_text or filter_text in a.get("name", "").lower()]
        self.at.setRowCount(len(visible))
        for i,a in enumerate(visible):
            ni=QTableWidgetItem(a.get("name", "")); ni._acc_id=a["id"]; self.at.setItem(i,0,ni); self.at.setItem(i,1,QTableWidgetItem(a.get("game_client","")))
            lb=QPushButton("▶"); lb.setFixedSize(self._btn_sm,self._btn_sm); lb.setStyleSheet("QPushButton{background:#2b7a3a;color:#fff;border:none;border-radius:3px}QPushButton:hover{background:#1e5a28}")
            orig_idx=self.accounts.index(a); lb.clicked.connect(lambda c,idx=orig_idx: self._la(idx)); lw=QWidget(); lwl2=QHBoxLayout(lw); lwl2.setContentsMargins(0,0,0,0); lwl2.setAlignment(Qt.AlignCenter); lwl2.addWidget(lb); self.at.setCellWidget(i,2,lw)
    def _on_acc_sel(self) -> None:
        sel=self.at.currentRow()
        if sel>=0:
            it=self.at.item(sel,0)
            if it and hasattr(it,'_acc_id'):
                for j,a in enumerate(self.accounts):
                    if a["id"]==it._acc_id: self._sad(j); break
    def _clear_dashboard(self) -> None:
        clear_dashboard(self)

    def _cleanup_emu_threads(self) -> None:
        cleanup_emu_threads(self)

    def _sad(self, row: int) -> None:
        build_account_dashboard(self, row)

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
        self.launch_queue._tick()

    def _la_all(self) -> None:
        """Batch enqueue all accounts with schedule priority."""
        self._log("══ 全部账号入队 ══")
        self.launch_queue.enqueue_batch("manual", priority=0)
        self.launch_queue._tick()

    def _on_account_started(self, aid: str) -> None:
        a = next((x for x in self.accounts if x["id"] == aid), None)
        if a:
            self._sad(self.accounts.index(a))

    def _on_account_finished(self, aid: str, exit_code: int, tasks: list[dict]) -> None:
        a = next((x for x in self.accounts if x["id"] == aid), None)
        if a and self._main_tab == "accounts":
            self._sad(self.accounts.index(a))

    # Legacy launch helpers (kept for pipeline_thread / warehouse quick-launch)
    def _ls(self, w: dict) -> None:
        try:
            args=w.get("args",[]); cwd=w.get("cwd","") or None; env={k:v for k,v in w.get("env",{}).items()} or None; exe=w["path"]; lm=w.get("launch_mode","gui")
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
    def _gtc(self, ac: dict, w: dict) -> str | None: return self.cfg.gtc(ac, w)
    def _inj(self, w: dict, ac: dict) -> None: self.cfg.inject(w, ac)
    # Pipeline
    def _start_pipeline(self) -> None:
        if not self.groups or (self.pipeline_thread and self.pipeline_thread.isRunning()): return
        self.qs.setEnabled(False); self._log("流水线启动")
        # Collect emulators to launch
        to_launch=[]
        launched=set()
        for a in self.accounts:
            emu_idx=a.get("emu_instance_index","")
            if a.get("emu_launch") and emu_idx and emu_idx not in launched:
                cli=find_mumu_cli()
                if cli:
                    to_launch.append((cli,emu_idx,a["name"],a.get("emu_wait", 30)))
                    launched.add(emu_idx)
        def _start_thread():
            self.pipeline_thread=PipelineThread(self.groups,self.warehouse,self.accounts,self)
            self.pipeline_thread.progress.connect(lambda m:(self.sl.setText(m),self._log(m)))
            self.pipeline_thread.program_started.connect(lambda n,ok: self._log(f"启动 {n}" if ok else f"失败 {n}"))
            self.pipeline_thread.finished.connect(lambda s: self.qs.setEnabled(True))
            self.pipeline_thread.start()
        def _launch_next(i=0):
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
        if self.pipeline_thread: self.pipeline_thread.stop()
    def _pause_pipeline(self) -> None:
        if self.pipeline_thread and self.pipeline_thread.isRunning():
            if getattr(self.pipeline_thread, "pause_flag", False):
                self.pipeline_thread.resume(); self._log("流水线已继续")
            else:
                self.pipeline_thread.pause(); self._log("流水线已暂停")

    def _poll(self) -> None:
        self.maint.poll()
        refresh_queue_view(self)
        if hasattr(self, "launch_queue"):
            if self.launch_queue.active_count:
                ac = self.launch_queue.active_count
                qc = self.launch_queue.pending_count
                self._qsb.setText(f"▶{ac}" + (f"  ⏳{qc}" if qc else ""))
            elif self.launch_queue.pending_count:
                self._qsb.setText(f"⏳{self.launch_queue.pending_count}")
            else:
                self._qsb.setText("")
    def _notify(self, msg: str, is_error: bool = False) -> None: self.maint.notify(msg, is_error)
    def _check_updates(self, silent: bool = False) -> None: self.maint.check_updates(silent=False)
    def _cu_single(self, w: dict) -> None: self.maint.cu_single(w)
    def _restore_geometry(self) -> None: self.maint.restore_geometry()
    def _setup_tray(self) -> None: self.maint.setup_tray()
    def _show_tray(self) -> None: self.maint.show_tray()
    def closeEvent(self, e) -> None:
        if not self.isMinimized():
            self.config["window_geometry"]=f"{self.width()}x{self.height()}+{self.x()}+{self.y()}"
        if self.config.get("minimize_to_tray",True) and hasattr(self,'tray_icon') and self.tray_icon:
            self.hide(); e.ignore()
        else:
            self._do_save(); e.accept(); QApplication.quit()
    def _tlog(self) -> None: self._sw("logs")
    def _start_schedule(self) -> None: self.maint.start_schedule()
    def _sch(self) -> None: self.maint.sch()
    def _settings(self) -> None: self.maint.settings()
    def _show_queue_panel(self) -> None:
        """Show a popup panel displaying running/queued account status."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QLabel, QGroupBox
        from PySide6.QtCore import QTimer
        d = QDialog(self)
        d.setWindowTitle("队列状态")
        d.setMinimumSize(400, 280)
        l = QVBoxLayout(d)

        running_grp = QGroupBox("▶ 运行中")
        rl = QVBoxLayout(running_grp)
        running_tbl = QTableWidget(0, 3)
        running_tbl.setHorizontalHeaderLabels(["账号", "状态", "时长"])
        running_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        running_tbl.setColumnWidth(1, 100)
        running_tbl.setColumnWidth(2, 70)
        running_tbl.verticalHeader().setVisible(False)
        running_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        rl.addWidget(running_tbl)
        l.addWidget(running_grp)

        queue_grp = QGroupBox("⏳ 排队中")
        ql = QVBoxLayout(queue_grp)
        queue_tbl = QTableWidget(0, 5)
        queue_tbl.setHorizontalHeaderLabels(["账号", "来源", "优先级", "预计启动", ""])
        queue_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        queue_tbl.setColumnWidth(1, 60)
        queue_tbl.setColumnWidth(2, 50)
        queue_tbl.setColumnWidth(3, 100)
        queue_tbl.setColumnWidth(4, 50)
        queue_tbl.verticalHeader().setVisible(False)
        queue_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        ql.addWidget(queue_tbl)
        l.addWidget(queue_grp)

        def _refresh():
            import time
            # Running
            running = []
            if hasattr(self, "runner"):
                for aid in self.runner.active_ids():
                    a = next((x for x in self.accounts if x["id"] == aid), None)
                    name = a["name"] if a else aid[:8]
                    t = int(time.time() - self.runner._start_times.get(aid, 0))
                    status = f"运行中 {t//60}m{t%60}s"
                    running.append((name, status, ""))
                # Also check pipeline procs
                for pid in list(getattr(self, "_running_procs", {}).keys()):
                    p = self._running_procs[pid]
                    if p.poll() is None:
                        w = next((x for x in self.warehouse if x["id"] == pid), None)
                        ac = next((x for x in self.accounts if x["id"] == w.get("account_ref", "")), None) if w else None
                        name = ac["name"] if ac else (Path(w["path"]).stem if w else pid[:8])
                        t = int(time.time() - self._proc_start_times.get(pid, 0))
                        running.append((name, f"运行中 {t//60}m{t%60}s", ""))
            running_tbl.setRowCount(max(1, len(running)) if running else 1)
            if running:
                for i, (name, status, _) in enumerate(running):
                    running_tbl.setItem(i, 0, QTableWidgetItem(name))
                    running_tbl.setItem(i, 1, QTableWidgetItem(status))
            else:
                running_tbl.setItem(0, 0, QTableWidgetItem("—"))
                running_tbl.setItem(0, 1, QTableWidgetItem("无"))

            # Queue
            queue = []
            if hasattr(self, "launch_queue"):
                src_map = {"manual": "手动", "schedule": "定时", "sanity": "理智"}
                now = __import__("datetime").datetime.now()
                for e in sorted(self.launch_queue._pending, key=lambda x: x.sort_key):
                    a = next((x for x in self.accounts if x["id"] == e.account_id), None)
                    name = a["name"] if a else e.account_id[:8]
                    when = ""
                    if e.not_before > now:
                        diff = int((e.not_before - now).total_seconds() / 60)
                        if diff > 60:
                            when = e.not_before.strftime("%m-%d %H:%M")
                        else:
                            when = f"{diff}分钟后"
                    else:
                        when = "等待空闲"
                    queue.append((name, src_map.get(e.source, e.source), str(e.sort_key[0]), when, e.account_id))
            queue_tbl.setRowCount(max(1, len(queue)) if queue else 1)
            if queue:
                for i, (name, src, pri, when, aid) in enumerate(queue):
                    queue_tbl.setItem(i, 0, QTableWidgetItem(name))
                    queue_tbl.setItem(i, 1, QTableWidgetItem(src))
                    queue_tbl.setItem(i, 2, QTableWidgetItem(pri))
                    queue_tbl.setItem(i, 3, QTableWidgetItem(when))
                    cancel_btn = QPushButton("✕")
                    cancel_btn.setFixedSize(self._btn_lg, self._btn_lg)
                    cancel_btn.setStyleSheet("QPushButton{background:transparent;color:#888;border:none}QPushButton:hover{background:#d32f2f;color:#fff;border-radius:" + str(self._btn_lg // 2) + "px}")
                    cancel_btn.setToolTip("取消排队")
                    cancel_btn.clicked.connect(lambda c, a=aid: (self.launch_queue.dequeue(a), _refresh()))
                    cw = QWidget()
                    cwl = QHBoxLayout(cw)
                    cwl.setContentsMargins(0, 0, 0, 0)
                    cwl.setAlignment(Qt.AlignCenter)
                    cwl.addWidget(cancel_btn)
                    queue_tbl.setCellWidget(i, 4, cw)
            else:
                queue_tbl.setItem(0, 0, QTableWidgetItem("—"))
                queue_tbl.setItem(0, 1, QTableWidgetItem("无"))

        _refresh()
        timer = QTimer(d)
        timer.timeout.connect(_refresh)
        timer.start(2000)
        d.finished.connect(timer.stop)
        d.exec()
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
