import sys,json,os,ctypes,time,subprocess,re,shutil,io,socket
import urllib.request,urllib.error,zipfile,tempfile
from pathlib import Path
from datetime import datetime,time as dtime
from collections import defaultdict
from http.server import HTTPServer,BaseHTTPRequestHandler
import threading

from utils import (is_admin,run_as_admin,make_id,parse_maa_version,get_platform_key,_version_tuple,_rmtree_force,_find_maa_cli)
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

# Auto-detect proxy (Clash/v2ray/etc) for GitHub access
def _setup_proxy():
    for p in [os.environ.get("HTTP_PROXY",""),os.environ.get("http_proxy",""),os.environ.get("HTTPS_PROXY",""),os.environ.get("https_proxy","")]:
        if p:
            urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({"http":p,"https":p})))
            return
    for port in [7890,7891,1080,10809,8080]:
        try:
            s=socket.socket(); s.settimeout(0.3); s.connect(("127.0.0.1",port)); s.close()
            p=f"http://127.0.0.1:{port}"
            urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({"http":p,"https":p})))
            return
        except: pass
_setup_proxy()

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
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MAAOrch"); self.setMinimumSize(960,620)
        self.config=load_config()
        self.groups=self.config.get("groups",[]); self.warehouse=self.config.get("warehouse",[])
        self.accounts=self.config.get("accounts",[]); self.selected_group_idx=None
        self.pipeline_thread=None; self.schedule_thread=None; self.update_thread=None
        self._log_expanded=False; self._view_tab="group"; self._main_tab="groups"
        self._running_procs={}; self._proc_status=set(); self._restart_cnt=defaultdict(int); self._cli_procs={}
        self._proc_start_times={}
        fm=self.fontMetrics(); self._row_h=max(28,fm.height()+8); self._btn_sm=max(18,fm.height()+2); self._btn_lg=max(28,int(fm.height()*1.6))
        self._set_theme(self.config.get("appearance_mode","Dark"))
        self.emu = EmuService(self)
        self.cfg = ConfigService(self)
        self.logs = LogService(self)
        self.maint = MaintService(self)
        self._build_ui(); self.maint.restore_geometry(); self._rgl(); self._log("══ 启动 ══")
        self.maint.setup_tray(); self.maint.start_schedule()
        self._proc_timer=QTimer(self); self._proc_timer.timeout.connect(self._poll); self._proc_timer.start(2000)
        self._emu_monitor=EmuMonitor(); self._emu_status={}
        self._emu_monitor.updated.connect(lambda r: [self._emu_status.update({x["index"]:x}) for x in r])
        self._emu_monitor.start()
        self._api_server=None
        self._start_api_server()
        self._log(f"账号: {len(self.accounts)} | 仓库: {len(self.warehouse)} | 分组: {len(self.groups)}")
        if self.config.get("check_update_on_start",True): QTimer.singleShot(3000,lambda: self.maint.check_updates(True))

    def _set_theme(self,m): self.setStyleSheet(DARK_STYLE if m=="Dark" else LIGHT_STYLE)
    def _start_api_server(self):
        if self._api_server: self._api_server.stop_server(); self._api_server.quit(); self._api_server.wait(1000)
        port=self.config.get("api_port",19999); token=self.config.get("api_token","")
        self._api_server=ApiServer(port,token,self)
        self._api_server.log_msg.connect(lambda m: self._log(m))
        self._api_server.start()
    def _sl(self,msg): self.sl.setText((msg[:100]+"…") if len(msg)>100 else msg)
    def _log(self,msg):
        ts=datetime.now().strftime("%H:%M:%S"); line=f"[{ts}] {msg}"
        if hasattr(self,'log_text'): self.log_text.appendPlainText(line)
        try:
            lp=Path(__file__).parent/"debug.log"
            if lp.exists() and lp.stat().st_size>100*1024:
                lines=lp.read_text(encoding="utf-8").split("\n")
                lp.write_text("\n".join(lines[-200:])+"\n",encoding="utf-8")
            with lp.open("a",encoding="utf-8") as f: f.write(line+"\n")
        except Exception:
            try: print(line,file=__import__('sys').stderr)
            except: pass
    def _la_all(self):
        self._log("══ 启动全部账号 ══"); self._log_expanded=True; self.log_text.setFixedHeight(150)
        total=len(self.accounts)
        def _next(idx=0):
            if idx>=total:
                self._log("══ 全部启动完成 ══"); self.maint.notify("全部账号启动完成"); return
            a=self.accounts[idx]; progs=[w for w in self.warehouse if w.get("account_ref")==a["id"]]
            self.sl.setText(f"启动中: {idx+1}/{total}")
            if not progs: self._log(f"跳过: {a['name']} (无绑定)"); QTimer.singleShot(500,lambda: _next(idx+1)); return
            self._la(idx)
            QTimer.singleShot(5000,lambda: _next(idx+1))
        _next()
    def _save(self):
        # Debounce: coalesce rapid saves within 300ms
        if hasattr(self,'_save_timer') and self._save_timer:
            self._save_timer.stop()
        self._save_timer=QTimer(self); self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._do_save)
        self._save_timer.start(300)
    def _do_save(self):
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
            for f in files[10:]: f.unlink()
        except Exception as e:
            try: self._log(f"备份失败: {e}")
            except: pass
        self.config["groups"]=self.groups; self.config["warehouse"]=self.warehouse; self.config["accounts"]=self.accounts; save_config(self.config)

    def _build_ui(self):
        c=QWidget(); self.setCentralWidget(c); ml=QVBoxLayout(c); ml.setContentsMargins(8,8,8,4); ml.setSpacing(4)
        tb=QFrame(); th=QHBoxLayout(tb); th.setContentsMargins(0,0,0,4)
        self.tg=QPushButton("📋 分组"); self.tg.setObjectName("tabBtnActive")
        self.tg.clicked.connect(lambda: self._sw("groups"))
        self.ta=QPushButton("👤 账号"); self.ta.setObjectName("tabBtn")
        self.ta.clicked.connect(lambda: self._sw("accounts"))
        th.addWidget(self.tg); th.addWidget(self.ta); th.addStretch()
        self.qs=QPushButton("▶ 启动流水线"); self.qs.setObjectName("startBtn"); self.qs.clicked.connect(self._start_pipeline); th.addWidget(self.qs); ml.addWidget(tb)

        self.gv=QWidget(); gvl=QVBoxLayout(self.gv); gvl.setContentsMargins(0,0,0,0)
        sp=QSplitter(Qt.Horizontal)
        left=QWidget(); left.setMinimumWidth(180); ll=QVBoxLayout(left); ll.setContentsMargins(0,0,0,0)
        ll.addWidget(QLabel("分组",font=QFont("Microsoft YaHei UI",13,QFont.Bold)))
        self.gl_=QListWidget(); self.gl_.currentRowChanged.connect(self._on_group); ll.addWidget(self.gl_)
        br=QHBoxLayout(); br.addWidget(QPushButton("＋",clicked=self._add_group))
        db=QPushButton("✕"); db.setObjectName("stopBtn"); db.clicked.connect(self._del_group); br.addWidget(db); ll.addLayout(br); sp.addWidget(left)
        right=QWidget(); rl=QVBoxLayout(right); rl.setContentsMargins(0,0,0,0)
        sb=QFrame(); sh=QHBoxLayout(sb); sh.setContentsMargins(0,0,0,4)
        self.tw=QPushButton("📦 仓库"); self.tw.setObjectName("tabBtnActive")
        self.tw.clicked.connect(lambda: self._st("warehouse"))
        self.tg2=QPushButton("📋 当前组"); self.tg2.setObjectName("tabBtn")
        sh.addWidget(self.tw); sh.addWidget(self.tg2); sh.addStretch(); rl.addWidget(sb)

        self.wv=QWidget(); wl=QVBoxLayout(self.wv); wl.setContentsMargins(0,0,0,0)
        ws=QHBoxLayout(); self.whs=QLineEdit(); self.whs.setPlaceholderText("搜索..."); self.whs.textChanged.connect(self._rw); ws.addWidget(self.whs)
        cbtn=QPushButton("✕"); cbtn.setFixedWidth(28); cbtn.setToolTip("清除搜索"); cbtn.clicked.connect(lambda: self.whs.clear()); ws.addWidget(cbtn)
        ws.addWidget(QPushButton("＋ 添加",clicked=self._add_wh,objectName="addProgBtn"))
        ws.addWidget(QPushButton("检查更新",clicked=lambda: self.maint.check_updates())); wl.addLayout(ws)
        self.wt=QTableWidget(); self.wt.setColumnCount(4); self.wt.setHorizontalHeaderLabels(["","名称","类型",""])
        self.wt.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.wt.setColumnWidth(0,30); self.wt.setColumnWidth(2,140); self.wt.setColumnWidth(3,36)
        self.wt.setSelectionBehavior(QAbstractItemView.SelectRows); self.wt.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.wt.verticalHeader().setVisible(False); self.wt.verticalHeader().setDefaultSectionSize(self._row_h+4)
        self.wt.setAlternatingRowColors(True); self.wt.setShowGrid(False)
        self.wt.setContextMenuPolicy(Qt.CustomContextMenu); self.wt.customContextMenuRequested.connect(self._wh_menu); wl.addWidget(self.wt); self.wv.hide(); rl.addWidget(self.wv)

        self.gv2=QWidget(); gl2=QVBoxLayout(self.gv2); gl2.setContentsMargins(0,0,0,0)
        self.gs=QGroupBox("分组设置"); self.gs.hide(); gsf=QFormLayout(self.gs)
        self.gn=QLineEdit(); self.gn.editingFinished.connect(self._sv_gn); gsf.addRow("组名:",self.gn)
        self.gm=QComboBox(); self.gm.addItems(["并行","串行"]); self.gm.currentTextChanged.connect(self._sv_gm); gsf.addRow("模式:",self.gm); gl2.addWidget(self.gs)
        self.gt=QTableWidget(); self.gt.setColumnCount(3); self.gt.setHorizontalHeaderLabels(["名称","预延迟",""])
        self.gt.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch); self.gt.setColumnWidth(1,70); self.gt.setColumnWidth(2,30)
        self.gt.setSelectionBehavior(QAbstractItemView.SelectRows); self.gt.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.gt.verticalHeader().setVisible(False); self.gt.verticalHeader().setDefaultSectionSize(self._row_h+4)
        self.gt.setAlternatingRowColors(True); self.gt.setShowGrid(False)
        self.gt.setContextMenuPolicy(Qt.CustomContextMenu); self.gt.customContextMenuRequested.connect(self._gt_menu)
        self.gt.doubleClicked.connect(self._gt_launch); self.gt.hide(); gl2.addWidget(self.gt)
        self.ph=QLabel("← 选择分组"); self.ph.setAlignment(Qt.AlignCenter); self.ph.setStyleSheet("color:#888;font-size:14px"); gl2.addWidget(self.ph,1); rl.addWidget(self.gv2)
        sp.addWidget(right); sp.setStretchFactor(1,1); sp.setSizes([220,740]); gvl.addWidget(sp,1); ml.addWidget(self.gv,1)
        # Accounts
        self.av=QWidget(); avl=QVBoxLayout(self.av); avl.setContentsMargins(0,0,0,0)
        asp=QSplitter(Qt.Horizontal); al=QWidget(); al.setMinimumWidth(240); al_=QVBoxLayout(al); al_.setContentsMargins(0,0,0,0)
        al_.addWidget(QLabel("账号",font=QFont("Microsoft YaHei UI",13,QFont.Bold)))
        self.asrch=QLineEdit(); self.asrch.setPlaceholderText("搜索账号..."); self.asrch.setClearButtonEnabled(True); self.asrch.textChanged.connect(lambda: self._ra())
        al_.addWidget(self.asrch)
        self.at=QTableWidget(); self.at.setColumnCount(3); self.at.setHorizontalHeaderLabels(["名称","区服",""])
        self.at.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch); self.at.setColumnWidth(1,70); self.at.setColumnWidth(2,50)
        self.at.setSelectionBehavior(QAbstractItemView.SelectRows); self.at.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.at.verticalHeader().setVisible(False); self.at.verticalHeader().setDefaultSectionSize(self._row_h+4)
        self.at.setDragEnabled(True); self.at.setDragDropMode(QAbstractItemView.InternalMove)
        self.at.setDropIndicatorShown(True)
        self._acc_drop_lock=False
        def _on_acc_drop():
            if self._acc_drop_lock: return
            self._acc_drop_lock=True
            # Reorder accounts by table row order, matching by ID
            new_order=[]
            id_map={a["id"]:a for a in self.accounts}
            for i in range(self.at.rowCount()):
                it=self.at.item(i,0)
                if it and hasattr(it,'_acc_id') and it._acc_id in id_map:
                    new_order.append(id_map[it._acc_id])
            if len(new_order)==len(self.accounts):
                self.accounts=new_order; self._save()
            self._acc_drop_lock=False
        self.at.model().rowsMoved.connect(lambda *a: _on_acc_drop())
        self.at.setContextMenuPolicy(Qt.CustomContextMenu); self.at.customContextMenuRequested.connect(self._ac_menu)
        self.at.itemSelectionChanged.connect(self._on_acc_sel); al_.addWidget(self.at)
        ab=QHBoxLayout(); ab.addWidget(QPushButton("＋",clicked=self._add_acc,objectName="addProgBtn"))
        ab.addWidget(QPushButton("✕",clicked=self._del_acc,objectName="stopBtn")); al_.addLayout(ab); asp.addWidget(al)
        self.ad=QScrollArea(); self.ad.setWidgetResizable(True); self.ad.setFrameShape(QFrame.NoFrame)
        self.adw=QWidget(); self.adl=QVBoxLayout(self.adw); self.adl.setContentsMargins(12,4,12,12); self.adl.setSpacing(8)
        self.ade=QLabel("← 选择账号"); self.ade.setAlignment(Qt.AlignCenter); self.ade.setStyleSheet("color:#888;font-size:14px"); self.adl.addWidget(self.ade,1)
        self.ad.setWidget(self.adw); asp.addWidget(self.ad);         asp.setStretchFactor(1,1); asp.setSizes([280,680]); avl.addWidget(asp,1)
        self.av.hide(); ml.addWidget(self.av,1)

        self.log_text=QPlainTextEdit(); self.log_text.setReadOnly(True); self.log_text.setMaximumBlockCount(500); self.log_text.setFixedHeight(0); ml.addWidget(self.log_text)
        sb2=self.statusBar(); self.sl=QLabel(" 就绪"); sb2.addWidget(self.sl)
        self.spb=QPushButton("停止"); self.spb.setObjectName("stopBtn"); self.spb.clicked.connect(self._stop_pipeline); self.spb.setEnabled(False); sb2.addPermanentWidget(self.spb)
        self.pab=QPushButton("暂停"); self.pab.clicked.connect(self._pause_pipeline); self.pab.setEnabled(False); sb2.addPermanentWidget(self.pab)
        self.stb=QPushButton("启动流水线"); self.stb.setObjectName("startBtn"); self.stb.clicked.connect(self._start_pipeline); sb2.addPermanentWidget(self.stb)
        mb=self.menuBar(); tm=mb.addMenu("工具")
        tm.addAction("定时",self._sch); tm.addAction("检查更新",lambda: self.maint.check_updates()); tm.addAction("设置",self._settings); tm.addAction("日志",self._tlog)
        from PySide6.QtGui import QShortcut,QKeySequence
        QShortcut(QKeySequence("Ctrl+Return"),self,self._start_pipeline); QShortcut(QKeySequence("Esc"),self,self._stop_pipeline)
        self._st("group"); self._sw("groups")

    def _sw(self,tab):
        self._main_tab=tab; self.gv.setVisible(tab=="groups"); self.av.setVisible(tab=="accounts")
        if tab=="groups":
            self.tg.setObjectName("tabBtnActive"); self.tg.style().unpolish(self.tg); self.tg.style().polish(self.tg)
            self.ta.setObjectName("tabBtn"); self.ta.style().unpolish(self.ta); self.ta.style().polish(self.ta)
        else:
            self.tg.setObjectName("tabBtn"); self.tg.style().unpolish(self.tg); self.tg.style().polish(self.tg)
            self.ta.setObjectName("tabBtnActive"); self.ta.style().unpolish(self.ta); self.ta.style().polish(self.ta)
            self._ra()
            if self.accounts: self.at.setCurrentCell(0,0)
    def _st(self,tab):
        self._view_tab=tab; is_w=tab=="warehouse"; self.wv.setVisible(is_w); self.gv2.setVisible(not is_w)
        if is_w:
            self.tw.setObjectName("tabBtnActive"); self.tw.style().unpolish(self.tw); self.tw.style().polish(self.tw)
            self.tg2.setObjectName("tabBtn"); self.tg2.style().unpolish(self.tg2); self.tg2.style().polish(self.tg2)
            self._rw()
        else:
            self.tw.setObjectName("tabBtn"); self.tw.style().unpolish(self.tw); self.tw.style().polish(self.tw)
            self.tg2.setObjectName("tabBtnActive"); self.tg2.style().unpolish(self.tg2); self.tg2.style().polish(self.tg2)

    # Warehouse
    def _rw(self):
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
    def _tw(self,wid,c):
        sel=self.selected_group_idx
        if sel is None or sel>=len(self.groups): return
        g=self.groups[sel]
        if c: g["programs"].append({"ref":wid,"pre_delay":0}) if not any(r["ref"]==wid for r in g.get("programs",[])) else None
        else: g["programs"]=[r for r in g.get("programs",[]) if r.get("ref")!=wid]
        self._save(); self._rgl(); self._rw()
    def _add_wh(self):
        fs,_=QFileDialog.getOpenFileNames(self,"选择","","可执行文件 (*.exe);;所有文件 (*.*)"); ex={w["path"] for w in self.warehouse}
        for fp in fs:
            p=str(Path(fp))
            if p not in ex:
                e={"id":make_id(),"path":p,"args":[],"cwd":"","env":{},"maa_type":"general","maa_version":"","account_ref":"","launch_mode":"gui","task_pipeline":"","guard_enabled":False,"guard_max_restart":3,"guard_capture_log":False}
                if Path(p).stem.lower()=="maa": e["maa_type"]="maa"; v=parse_maa_version(p)
                if v: e["maa_version"]=v
                self.warehouse.append(e); ex.add(p)
        self._save(); self._rw()
    def _rm_wh(self,row):
        ft=self.whs.text().lower(); items=[w for w in self.warehouse if ft in Path(w.get("path","")).stem.lower() or not ft]
        if row>=len(items): return
        w=items[row]
        if QMessageBox.question(self,"确认",f"删除 {Path(w['path']).stem}?")==QMessageBox.Yes:
            for g in self.groups: g["programs"]=[r for r in g.get("programs",[]) if r.get("ref")!=w["id"]]
            self.warehouse.remove(w); self._save(); self._rw()
    def _wh_menu(self,pos):
        row=self.wt.rowAt(pos.y()); ft=self.whs.text().lower(); items=[w for w in self.warehouse if ft in Path(w.get("path","")).stem.lower() or not ft]
        if row>=len(items): return
        w=items[row]; m=QMenu(); m.addAction("▶ 启动",lambda: self._ls(w)); m.addAction("⚙ 设置",lambda: self._ed_wh(w))
        if w.get("maa_type")!="general": m.addAction("检查更新",lambda: self.maint.cu_single(w))
        m.addSeparator(); m.addAction("删除",lambda: self._rm_wh(row)); m.exec(self.wt.viewport().mapToGlobal(pos))
    def _ed_wh(self,w):
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
    def _rgt(self):
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
    def _sv_pd(self,r,v):
        sel=self.selected_group_idx
        if sel is not None and sel<len(self.groups) and r<len(self.groups[sel].get("programs",[])): self.groups[sel]["programs"][r]["pre_delay"]=v; self._save()
    def _rm_pg(self,r):
        sel=self.selected_group_idx
        if sel is not None and sel<len(self.groups) and r<len(self.groups[sel].get("programs",[])): self.groups[sel]["programs"].pop(r); self._save(); self._rgt(); self._rgl()
    def _gt_menu(self,pos):
        row=self.gt.rowAt(pos.y())
        if row<0 or self.selected_group_idx is None: return
        refs=self.groups[self.selected_group_idx].get("programs",[])
        if row>=len(refs): return
        w=next((x for x in self.warehouse if x["id"]==refs[row].get("ref")),None)
        if not w: return
        m=QMenu(); m.addAction("▶ 启动",lambda: self._ls(w)); m.addAction("⚙ 设置",lambda: self._ed_wh(w)); m.addAction("从分组移除",lambda: self._rm_pg(row)); m.exec(self.gt.viewport().mapToGlobal(pos))
    def _gt_launch(self):
        row=self.gt.currentRow()
        if row<0 or self.selected_group_idx is None: return
        refs=self.groups[self.selected_group_idx].get("programs",[])
        if row<len(refs): w=next((x for x in self.warehouse if x["id"]==refs[row].get("ref")),None)
        if w: self._ls(w)

    # Groups
    def _rgl(self):
        self.gl_.blockSignals(True); self.gl_.clear()
        for i,g in enumerate(self.groups):
            c=len(g.get("programs",[])); ic="∥" if g.get("mode")=="parallel" else "→"
            rw=QWidget(); rw.setMinimumHeight(self._row_h); rl2=QHBoxLayout(rw); rl2.setContentsMargins(6,2,4,2)
            lb=QLabel(f"#{i+1} {g['name']}  {ic} {c}个"); lb.setAttribute(Qt.WA_TransparentForMouseEvents); rl2.addWidget(lb,1)
            rw.mousePressEvent=lambda e,idx=i: self.gl_.setCurrentRow(idx)
            it=QListWidgetItem(); it.setSizeHint(QSize(0,self._row_h)); self.gl_.addItem(it); self.gl_.setItemWidget(it,rw)
        if self.selected_group_idx is not None and self.selected_group_idx<len(self.groups): self.gl_.setCurrentRow(self.selected_group_idx)
        self.gl_.blockSignals(False)
    def _on_group(self,idx):
        if idx<0: return
        self.selected_group_idx=idx; self._sgd()
    def _sgd(self):
        idx=self.selected_group_idx
        if idx is None or idx>=len(self.groups): self.gs.hide(); self.gt.hide(); self.ph.show(); return
        g=self.groups[idx]; self.gs.show(); self.gn.setText(g.get("name","")); self.gm.setCurrentText("并行" if g.get("mode")=="parallel" else "串行"); self._rgt()
    def _sv_gn(self):
        i=self.selected_group_idx
        if i is not None and i<len(self.groups): self.groups[i]["name"]=self.gn.text() or "未命名"; self._save(); self._rgl()
    def _sv_gm(self):
        i=self.selected_group_idx
        if i is not None and i<len(self.groups): self.groups[i]["mode"]="parallel" if self.gm.currentText()=="并行" else "sequential"; self._save()
    def _add_group(self):
        n=len(self.groups)+1; self.groups.append({"name":f"新分组 {n}","mode":"parallel","post_delay":3,"programs":[]}); self._save(); self._rgl(); self.selected_group_idx=len(self.groups)-1; self.gl_.setCurrentRow(self.selected_group_idx)
    def _del_group(self):
        i=self.selected_group_idx
        if i is not None and i<len(self.groups) and QMessageBox.question(self,"确认",f"删除 {self.groups[i]['name']}?")==QMessageBox.Yes: self.groups.pop(i); self.selected_group_idx=min(i,len(self.groups)-1) if self.groups else None; self._save(); self._rgl()

    # Accounts
    def _ra(self):
        self.at.setRowCount(0)
        if not self.accounts: self.ad.setVisible(False); return
        self.ad.setVisible(True)
        search=getattr(self,'asrch',None); filter_text=search.text().strip().lower() if search and search.text() else ""
        visible=[a for a in self.accounts if not filter_text or filter_text in a.get("name","").lower()]
        self.at.setRowCount(len(visible))
        for i,a in enumerate(visible):
            ni=QTableWidgetItem(a.get("name","")); ni._acc_id=a["id"]; self.at.setItem(i,0,ni); self.at.setItem(i,1,QTableWidgetItem(a.get("game_client","")))
            lb=QPushButton("▶"); lb.setFixedSize(self._btn_sm,self._btn_sm); lb.setStyleSheet("QPushButton{background:#2b7a3a;color:#fff;border:none;border-radius:3px}QPushButton:hover{background:#1e5a28}")
            orig_idx=self.accounts.index(a); lb.clicked.connect(lambda c,idx=orig_idx: self._la(idx)); lw=QWidget(); lwl2=QHBoxLayout(lw); lwl2.setContentsMargins(0,0,0,0); lwl2.setAlignment(Qt.AlignCenter); lwl2.addWidget(lb); self.at.setCellWidget(i,2,lw)
    def _on_acc_sel(self):
        sel=self.at.currentRow()
        if sel>=0:
            it=self.at.item(sel,0)
            if it and hasattr(it,'_acc_id'):
                for j,a in enumerate(self.accounts):
                    if a["id"]==it._acc_id: self._sad(j); break
    def _sad(self,row):
        if hasattr(self,'_sad_row') and self._sad_row==row: return
        self._sad_row=row
        # Stop running threads to avoid signals to destroyed widgets
        for attr in ('_t','_scan_thread','_refresh_t','_test_t','_ss_t','_stopemu_t'):
            if hasattr(self.emu,attr) and getattr(self.emu,attr) and getattr(self.emu,attr).isRunning():
                try: getattr(self.emu,attr).result.disconnect()
                except: pass
                getattr(self.emu,attr).terminate(); getattr(self.emu,attr).wait(200)
        for i in reversed(range(self.adl.count())):
            w=self.adl.itemAt(i).widget()
            if w and w is not self.ade: self.ade.hide(); w.setParent(None)
        if row<0 or row>=len(self.accounts): self.ade.show(); return
        a=self.accounts[row]; progs=[w for w in self.warehouse if w.get("account_ref")==a["id"]]
        tr=QHBoxLayout()
        ne=QLineEdit(a.get("name","")); ne.setFont(QFont("Microsoft YaHei UI",16,QFont.Bold))
        ne.setPlaceholderText("账号名"); ne.textChanged.connect(lambda t: (a.__setitem__("name",t),self._save(),self._ra())); tr.addWidget(ne,1)
        cc=QComboBox()
        for k,v in CLIENT_TYPES.items(): cc.addItem(v,k)
        idx=cc.findData(a.get("game_client","Official")); cc.setCurrentIndex(max(0,idx))
        cc.currentIndexChanged.connect(lambda: (a.__setitem__("game_client",cc.currentData()),self._save())); tr.addWidget(cc)
        tw2=QWidget(); tw2.setLayout(tr); self.adl.insertWidget(0,tw2)

        mc=QFrame(); mc.setObjectName("card"); mcl=QVBoxLayout(mc); mcl.setSpacing(5); mh=QHBoxLayout(); mh.addWidget(QLabel("📦 MAA 状态",font=QFont("Microsoft YaHei UI",10,QFont.Bold))); mh.addStretch()
        if progs:
            v=progs[0].get("maa_version",""); vl=QLabel(f"已安装 {v}" if v else "已绑定")
            if progs[0]["id"] in self._proc_status:
                t=int(time.time()-self._proc_start_times.get(progs[0]["id"],0))
                vl.setText(f"🟢 运行中 ({t//60}m{t%60}s)  {v}" if v else f"🟢 运行中 ({t//60}m{t%60}s)")
                vl.setStyleSheet("color:#8a8;font-weight:bold")
            else: vl.setStyleSheet("color:#8a8;font-weight:bold")
            mh.addWidget(vl); mcl.addLayout(mh)
            for p in progs: mcl.addWidget(QLabel(f"  ▶ {p['path']}"))
            # Version channel selector
            vr=QHBoxLayout(); vr.addWidget(QLabel("通道:"))
            ch=QComboBox(); ch.addItems(["Stable","Beta","Alpha"])
            cur_ch=progs[0].get("update_channel","Stable"); ch.setCurrentText(cur_ch)
            ch.currentTextChanged.connect(lambda t: (progs[0].__setitem__("update_channel",t),self._save()))
            vr.addWidget(ch); vr.addStretch()
            sw_ver=QPushButton("🔄 切换版本"); sw_ver.clicked.connect(lambda: self.logs.switch_maa_version(progs[0],ch.currentText()))
            vr.addWidget(sw_ver); mcl.addLayout(vr)
            # Stats/log buttons
            btr=QHBoxLayout()
            stats_btn=QPushButton("📊 统计"); stats_btn.clicked.connect(lambda: self.logs.show_stats(progs[0])); btr.addWidget(stats_btn)
            log_btn=QPushButton("📋 日志"); log_btn.clicked.connect(lambda: self.logs.view_log(progs[0])); btr.addWidget(log_btn)
            btr.addStretch(); mcl.addLayout(btr)
            # Daily stats
            today=datetime.now().strftime("%Y-%m-%d"); sd=a.get("stats",{}).get(today,{})
            if sd.get("launches"):
                mcl.addWidget(QLabel(f"  今日: 启动 {sd['launches']} 次"))
        else:
            vl=QLabel("未安装"); vl.setStyleSheet("color:#a88;font-weight:bold"); mh.addWidget(vl); mcl.addLayout(mh); mcl.addWidget(QLabel("  点击下方下载或绑定"))
        self.adl.insertWidget(2,mc)

        # 🖥 Emulator card
        ec=QFrame(); ec.setObjectName("card"); ecl=QVBoxLayout(ec); ecl.setSpacing(5)
        ecl.addWidget(QLabel("🖥 模拟器",font=QFont("Microsoft YaHei UI",10,QFont.Bold)))
        def _lbl(t): l=QLabel(t); l.setFixedWidth(55); l.setAlignment(Qt.AlignRight|Qt.AlignVCenter); return l
        # Row: path
        rp=QHBoxLayout(); rp.addWidget(_lbl("启动:"))
        emu_path_edit=QLineEdit(a.get("emu_path","")); emu_path_edit.setPlaceholderText("模拟器启动路径")
        emu_path_edit.textChanged.connect(lambda t: a.update({"emu_path":t}) or self._save()); rp.addWidget(emu_path_edit,1)
        rp.addWidget(QPushButton("📂",clicked=lambda: self._browse_file(emu_path_edit,a,"emu_path"))); ecl.addLayout(rp)
        # Row: instance selector
        ri=QHBoxLayout(); ri.addWidget(_lbl("实例:"))
        ed_sel=QComboBox(); ed_sel.setMinimumWidth(180)
        combo_saved_idx=a.get("emu_instance_index",""); combo_saved_name=a.get("emu_instance_name","")
        # Use async detection to avoid blocking UI during dashboard build
        self._refresh_instance_list_async(ed_sel, combo_saved_idx, combo_saved_name)
        def _on_ins(i):
            if ed_sel.currentData():
                ins=ed_sel.currentData()
                cli=find_mumu_cli()
                if cli:
                    emu_path_edit.setText(str(cli)); a.__setitem__("emu_path",str(cli)); a.__setitem__("emu_add_cmd","")
                a.__setitem__("emu_instance_index",ins["index"])
                a.__setitem__("emu_instance_name",ins.get("name",""))
                if ins.get("adb_port"): ae2.setText(f"127.0.0.1:{ins['adb_port']}"); a.__setitem__("adb_address",ae2.text())
                self._save()
        ed_sel.currentIndexChanged.connect(_on_ins); ri.addWidget(ed_sel,1)
        ri.addWidget(QPushButton("🔄",clicked=lambda: self._refresh_instance_list_async(ed_sel),toolTip="刷新实例列表")); ecl.addLayout(ri)
        # Row: launch options
        rl2=QHBoxLayout(); rl2.addWidget(_lbl(""))
        cb_oe=QCheckBox("自启模拟器"); cb_oe.setChecked(a.get("emu_launch",False))
        cb_oe.setToolTip("启动时自动通过 mumu-cli 启动模拟器")
        cb_oe.toggled.connect(lambda v: a.update({"emu_launch":v}) or self._save()); rl2.addWidget(cb_oe)
        rl2.addWidget(QLabel("等待")); ws_sp=QSpinBox(); ws_sp.setRange(0,300); ws_sp.setValue(a.get("emu_wait",30)); ws_sp.setSuffix(" 秒")
        ws_sp.valueChanged.connect(lambda v: a.update({"emu_wait":v}) or self._save()); rl2.addWidget(ws_sp)
        rl2.addStretch(); rl2.addWidget(QPushButton("🔍 扫端口",clicked=lambda: self._scan_port(a,emu_path_edit,ae2)))
        rl2.addWidget(QPushButton("⏻ 关闭",clicked=lambda: self._stop_emu(a),objectName="stopBtn")); ecl.addLayout(rl2)
        self.adl.insertWidget(3,ec)

        # 📱 ADB card
        cc=QFrame(); cc.setObjectName("card"); ccl=QVBoxLayout(cc); ccl.setSpacing(5)
        ccl.addWidget(QLabel("📱 ADB 连接",font=QFont("Microsoft YaHei UI",10,QFont.Bold)))
        # Row: preset
        rpr=QHBoxLayout(); rpr.addWidget(_lbl("预设:"))
        emu_sel=QComboBox(); emu_sel.addItem("— 选择 —","")
        for ep in EMU_PRESETS: emu_sel.addItem(ep["name"],ep["type"])
        idx=emu_sel.findData(a.get("connection_preset",""))
        if idx>=0: emu_sel.setCurrentIndex(idx)
        def _on_emu(i):
            if i>0 and i<=len(EMU_PRESETS):
                ep=EMU_PRESETS[i-1]; a["connection_preset"]=ep["type"]; self._save()
        emu_sel.currentIndexChanged.connect(_on_emu); rpr.addWidget(emu_sel,1); ccl.addLayout(rpr)
        # Row: ADB path
        rap=QHBoxLayout(); rap.addWidget(_lbl("ADB:"))
        adb_p=QLineEdit(a.get("adb_path","")); adb_p.setPlaceholderText("留空使用默认")
        adb_p.textChanged.connect(lambda t: a.update({"adb_path":t}) or self._save()); rap.addWidget(adb_p,1)
        rap.addWidget(QPushButton("📂",clicked=lambda: self._browse_adb(adb_p,a))); ccl.addLayout(rap)
        # Row: ADB address
        raa=QHBoxLayout(); raa.addWidget(_lbl("地址:"))
        ae2=QLineEdit(a.get("adb_address","")); ae2.setPlaceholderText("127.0.0.1:7555")
        ae2.textChanged.connect(lambda t: a.update({"adb_address":t}) or self._save()); raa.addWidget(ae2,1)
        emu_combo=QComboBox(); emu_combo.addItem("在线设备",""); emu_combo.setMinimumWidth(140)
        emu_combo.currentIndexChanged.connect(lambda i: ae2.setText(emu_combo.currentData()) if emu_combo.currentData() else None); raa.addWidget(emu_combo); ccl.addLayout(raa)
        # Row: account switch
        ras=QHBoxLayout(); ras.addWidget(_lbl("账号:"))
        sw_an=QLineEdit(a.get("account_switch","")); sw_an.setPlaceholderText("如 123***4567 或 mail@gmail.com，留空禁用")
        sw_an.textChanged.connect(lambda t: a.update({"account_switch":t}) or self._save()); ras.addWidget(sw_an,1); ccl.addLayout(ras)
        # Row: action buttons
        ract=QHBoxLayout(); ract.addWidget(_lbl(""))
        dc=QPushButton("🔍 扫描"); dc.clicked.connect(lambda cb=emu_combo: self._scan(a,cb)); ract.addWidget(dc)
        tb2=QPushButton("测试"); tb2.clicked.connect(lambda: self._test_adb(a)); ract.addWidget(tb2)
        ss_btn=QPushButton("📸 截图"); ss_btn.clicked.connect(lambda: self._adb_screenshot(a)); ract.addWidget(ss_btn)
        ract.addStretch(); ccl.addLayout(ract)
        self._ast=QLabel(""); ccl.addWidget(self._ast); self.adl.insertWidget(4,cc)

        tc=QFrame(); tc.setObjectName("card"); tcl=QVBoxLayout(tc); tcl.setSpacing(5)
        tcl.addWidget(QLabel("⚙ 流水线",font=QFont("Microsoft YaHei UI",10,QFont.Bold)))
        pt=progs[0].get("task_pipeline","startup,fight,recruit,infrast,mall,award") if progs else "startup,fight,recruit,infrast,mall,award"
        all_tasks=[t.strip() for t in pt.split(",") if t.strip()]
        tl={k.lower():v for k,v in TASK_NAMES.items()}; tn={v:k for k,v in TASK_NAMES.items()}
        task_cbs={}; tw=QWidget(); twl=QHBoxLayout(tw); twl.setContentsMargins(0,0,0,0); twl.setSpacing(4)
        for tl2 in sorted(set(t.lower() for t in all_tasks)&set(tl.keys()),key=lambda x:list(tl.keys()).index(x)):
            cn=tl[tl2]; tk=tn.get(cn,tl2); cb=QCheckBox(cn); cb.setChecked(tl2 in [t.lower() for t in all_tasks]); task_cbs[tk]=cb; twl.addWidget(cb)
        twl.addStretch(); tcl.addWidget(tw)
        def _up():
            ep=[]; [ep.append(tk) for tk in task_cbs if task_cbs[tk].isChecked()]; [ep.append(t) for t in all_tasks if t.lower() not in tl]
            new_pipe=",".join(ep)
            for p in progs: p["task_pipeline"]=new_pipe; p["launch_mode"]=mc2.currentText()
            self._save()
        for cb in task_cbs.values(): cb.toggled.connect(lambda _: _up())
        mr2=QHBoxLayout(); cfg_btn=QPushButton("⚙ 参数"); ts=a.get("task_settings",{})
        cfg_btn.clicked.connect(lambda: (TaskSettingsDialog(self,ts,progs[0].get("task_pipeline","")).exec(),a.__setitem__("task_settings",ts),self._save())); mr2.addWidget(cfg_btn)
        tmpl_btn=QPushButton("💾 模板")
        tm=QMenu()
        def _sv_tmpl():
            name,ok=QInputDialog.getText(self,"保存模板","名称:",text="日常模式")
            if ok and name:
                a.setdefault("task_templates",{})[name]=dict(ts); a.setdefault("pipe_templates",{})[name]=progs[0].get("task_pipeline","")
                self._save(); self._log(f"模板已保存: {name}")
        def _ld_tmpl(name):
            if name in a.get("task_templates",{}):
                ts.clear(); ts.update(a["task_templates"][name])
                for p in progs: p["task_pipeline"]=a.get("pipe_templates",{}).get(name,"")
                self._save(); self._sad(row)
        for n in a.get("task_templates",{}):
            tm.addAction(f"📂 {n}",lambda n=n: _ld_tmpl(n))
            tm.addAction(f"✕ 删{n}",lambda n=n: (a["task_templates"].pop(n,None),a.get("pipe_templates",{}).pop(n,None),self._save(),self._sad(row)))
        if a.get("task_templates",{}): tm.addSeparator()
        tm.addAction("💾 保存当前...",_sv_tmpl)
        tmpl_btn.setMenu(tm); mr2.addWidget(tmpl_btn)
        sc=QCheckBox("启动时同步"); sc.setChecked(a.get("sync_tasks",False)); sc.toggled.connect(lambda v: (a.__setitem__("sync_tasks",v),self._save())); mr2.addWidget(sc); mr2.addStretch()
        mr2.addWidget(QLabel("模式:")); mc2=QComboBox(); mc2.addItems(["gui","cli"]); mc2.setCurrentText(progs[0].get("launch_mode","gui") if progs else "gui")
        mc2.currentTextChanged.connect(lambda t: _up()); mr2.addWidget(mc2); mr2.addStretch()
        if mc2.currentText()=="cli":
            cl=_find_maa_cli(); l2=QLabel("maa-cli 就绪" if cl else "maa-cli 未安装"); l2.setStyleSheet("color:#8a8" if cl else "color:#a88"); mr2.addWidget(l2)
        mr2.addStretch(); tcl.addLayout(mr2); self.adl.insertWidget(5,tc)

        # 启动选项 + 完成后 (merged)
        oc=QFrame(); oc.setObjectName("card"); ocl=QVBoxLayout(oc); ocl.setSpacing(5)
        ocl.addWidget(QLabel("🔄 启动与完成后",font=QFont("Microsoft YaHei UI",10,QFont.Bold)))
        # Row 1: launch options
        or1=QHBoxLayout(); or1.addWidget(_lbl("启动:"))
        cb_sm=QCheckBox("启动后最小化"); cb_sm.setChecked(a.get("start_minimized",False))
        cb_sm.toggled.connect(lambda v: (a.__setitem__("start_minimized",v),self._save())); or1.addWidget(cb_sm)
        cb_sd=QCheckBox("直接运行"); cb_sd.setChecked(a.get("start_directly",False))
        cb_sd.toggled.connect(lambda v: (a.__setitem__("start_directly",v),self._save())); or1.addWidget(cb_sd)
        cb_ad=QCheckBox("ADB 失败启模拟器"); cb_ad.setChecked(a.get("adb_fail_launch_emu",False))
        cb_ad.toggled.connect(lambda v: (a.__setitem__("adb_fail_launch_emu",v),self._save())); or1.addWidget(cb_ad)
        or1.addWidget(QLabel("ADB 重试")); ar=QSpinBox(); ar.setRange(0,10); ar.setValue(a.get("adb_retry",0)); ar.setSuffix(" 次"); ar.setMaximumWidth(60)
        ar.valueChanged.connect(lambda v: a.update({"adb_retry":v}) or self._save()); or1.addWidget(ar)
        or1.addStretch(); ocl.addLayout(or1)
        # Row 2: post actions
        or2=QHBoxLayout(); or2.addWidget(_lbl("完成后:"))
        post_actions=a.get("post_action","")
        if post_actions and post_actions[0]=="[":
            try: post_actions=",".join(json.loads(post_actions)); a["post_action"]=post_actions
            except: pass
        post_arr=post_actions.split(",") if post_actions else []
        post_cbs={}
        for k,v in [("BackToAndroidHome","返回主屏"),("ExitArknights","退出方舟"),("ExitEmulator","关模拟器"),("ExitSelf","退出MAA")]:
            cb=QCheckBox(v); cb.setChecked(k in post_arr); post_cbs[k]=cb; or2.addWidget(cb)
        or2.addStretch()
        def _save_post():
            acts=[k for k,cb in post_cbs.items() if cb.isChecked()]
            a.__setitem__("post_action",",".join(acts) if acts else "")
            self._save()
        for cb in post_cbs.values(): cb.toggled.connect(lambda _: _save_post())
        ocl.addLayout(or2); self.adl.insertWidget(6,oc)

        bw=QWidget(); bl=QHBoxLayout(bw); bl.setContentsMargins(0,0,0,0)
        if progs:
            lb2=QPushButton("▶ 启动"); lb2.setObjectName("startBtn"); lb2.setMinimumHeight(36); lb2.setFont(QFont("Microsoft YaHei UI",12,QFont.Bold))
            lb2.clicked.connect(lambda: self._la(row)); bl.addWidget(lb2)
            bl.addWidget(QPushButton("▶ 启动全部",clicked=lambda: self._la_all()))
            bl.addWidget(QPushButton("检查更新",clicked=lambda: self.maint.cu_single(progs[0])))
        else:
            dl=QPushButton("⬇ 下载 MAA"); dl.setObjectName("addProgBtn"); dl.setMinimumHeight(36); dl.clicked.connect(lambda: self.maint.dl_maa(row)); bl.addWidget(dl)
            bl.addWidget(QPushButton("📂 绑定",clicked=lambda: self.maint.pk_maa(row)))
        bl.addStretch(); self.adl.insertWidget(8,bw); self.adl.addStretch()

    def _refresh_instance_list_async(self, combo, saved_idx=None, saved_name=None):
        self.emu.refresh_instance_list(combo, saved_idx, saved_name)
    def _test_adb(self, a): self.emu.test_adb(a)
    def _browse_adb(self, le, ac): self.emu.browse_adb(le, ac)
    def _browse_file(self, le, ac, key): self.emu.browse_file(le, ac, key)
    def _adb_screenshot(self, a): self.emu.screenshot(a)
    def _stop_emu(self, a): self.emu.stop_emu(a)
    def _scan_port(self, a, path_edit, addr_edit): self.emu.scan_port(a, path_edit, addr_edit)
    def _maa_asst_log(self, w): return self.logs.maa_asst_log(w)
    def _switch_maa_version(self, w,channel): return self.logs.switch_maa_version(w,channel)
    def _parse_maa_log(self, w,tail=500): return self.logs.parse_maa_log(w,tail=500)
    def _show_maa_stats(self, w): return self.logs.show_maa_stats(w)
    def _view_maa_log(self, w): return self.logs.view_maa_log(w)
    def _scan(self, a, cb): self.emu.scan(a, cb)
    def _add_acc(self):
        d=AccountDialog(self)
        if d.exec()==QDialog.Accepted: self.accounts.append(d.r); self._save(); self._ra()
    def _del_acc(self):
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
    def _ac_menu(self,pos):
        row=self.at.rowAt(pos.y())
        if row<0: return
        it=self.at.item(row,0)
        if not it or not hasattr(it,'_acc_id'): return
        aid=it._acc_id
        for j,a in enumerate(self.accounts):
            if a["id"]==aid: orig=j; break
        else: return
        m=QMenu(); m.addAction("▶ 启动",lambda: self._la(orig)); m.addAction("📤 导出",lambda: self._export_acc(orig)); m.addAction("✕ 删除",lambda: self._del_acc()); m.exec(self.at.viewport().mapToGlobal(pos))
    def _export_acc(self,row):
        if row<0 or row>=len(self.accounts): return
        a=self.accounts[row]
        fp,_=QFileDialog.getSaveFileName(self,"导出账号",f"{a['name']}.json","JSON (*.json)")
        if fp:
            Path(fp).write_text(json.dumps({"name":a.get("name"),"game_client":a.get("game_client"),"adb_path":a.get("adb_path"),"adb_address":a.get("adb_address"),"connection_preset":a.get("connection_preset"),"touch_mode":a.get("touch_mode"),"account_switch":a.get("account_switch"),"emu_instance_index":a.get("emu_instance_index"),"emu_instance_name":a.get("emu_instance_name"),"emu_wait":a.get("emu_wait",30),"task_settings":a.get("task_settings",{}),"post_action":a.get("post_action"),"task_pipeline":(progs[0].get("task_pipeline","") if (progs:=[w for w in self.warehouse if w.get("account_ref")==a["id"]]) else "")},ensure_ascii=False,indent=2),encoding="utf-8")
    def _la(self,row):
        if row<0 or row>=len(self.accounts): return
        a=self.accounts[row]; progs=[w for w in self.warehouse if w.get("account_ref")==a["id"]]
        if not progs: QMessageBox.information(self,"提示","请先下载或绑定"); return
        self._log(f"[启动] {a['name']}")
        if not self._log_expanded: self._tlog()
        # Track stats
        today=datetime.now().strftime("%Y-%m-%d"); sd=a.setdefault("stats",{})
        sd.setdefault(today,{"launches":0,"total_sec":0}); sd[today]["launches"]+=1
        self._save()
        emu_idx=a.get("emu_instance_index","")
        if a.get("emu_launch") and emu_idx:
            cli=find_mumu_cli()
            if cli:
                self._log(f"启动模拟器 #{emu_idx}")
                try: subprocess.run([cli,"control","--vmindex",str(emu_idx),"launch"],creationflags=CF,timeout=15)
                except Exception as e: self._log(f"启动模拟器失败: {e}")
                self._emu_wait_and_launch(progs,a,a.get("emu_wait",30),0)
                return
        # ADB fail → launch emulator
        if a.get("adb_fail_launch_emu") and emu_idx:
            cli=find_mumu_cli()
            adb=a.get("adb_path","") or "adb"; addr=a.get("adb_address","")
            if addr and cli:
                r=subprocess.run([adb,"connect",addr],capture_output=True,text=True,timeout=5,creationflags=CF,encoding="utf-8",errors="replace")
                out=(r.stdout+r.stderr).strip()
                if "connected" in out.lower() or "already" in out.lower():
                    self._log(f"ADB 已连接 {addr}")
                else:
                    self._log(f"ADB 失败，启动模拟器 #{emu_idx}")
                    try: subprocess.run([cli,"control","--vmindex",str(emu_idx),"launch"],creationflags=CF,timeout=15)
                    except Exception as e: self._log(f"启动模拟器失败: {e}")
                    self._emu_wait_and_launch(progs,a,a.get("emu_wait",30),0)
                    return
        # ADB retry
        retry=a.get("adb_retry",0)
        if retry>0 and a.get("adb_address",""):
            adb_path=a.get("adb_path","") or "adb"; addr=a["adb_address"]
            def _try_retry(attempt=0):
                if attempt>=retry:
                    self._log(f"ADB 重试耗尽 ({retry})")
                    _do_launch(); return
                r=subprocess.run([adb_path,"connect",addr],capture_output=True,text=True,timeout=5,creationflags=CF,encoding="utf-8",errors="replace")
                if "connected" in (r.stdout+r.stderr).lower() or "already" in (r.stdout+r.stderr).lower():
                    self._log(f"ADB 重试成功 ({attempt+1}/{retry})")
                    _do_launch(); return
                self.sl.setText(f"ADB 重试 ({attempt+1}/{retry})...")
                QTimer.singleShot(1000,lambda: _try_retry(attempt+1))
            def _do_launch():
                for w in progs:
                    try: self._inj(w,a); self._ls(w)
                    except Exception as e: self._log(f"失败: {e}"); QMessageBox.critical(self,"失败",str(e))
            _try_retry()
            return
        for w in progs:
            try: self._inj(w,a); self._ls(w)
            except Exception as e: self._log(f"失败: {e}"); QMessageBox.critical(self,"失败",str(e))
    def _emu_wait_and_launch(self,progs,a,remaining,step):
        if remaining>0:
            self.sl.setText(f"等待模拟器 ({remaining}s)...")
            QTimer.singleShot(1000,lambda: self._emu_wait_and_launch(progs,a,remaining-1,step+1))
        else:
            self.sl.setText("就绪")
            adb=a.get("adb_path","") or "adb"; addr=a.get("adb_address","")
            # Try auto-detect ADB port via adb devices
            if not addr:
                try:
                    r=subprocess.run([adb,"devices"],capture_output=True,timeout=5,creationflags=CF)
                    for m in re.finditer(rb':(\d+)\s+device\b',r.stdout):
                        addr="127.0.0.1:"+m.group(1).decode('ascii')
                        a["adb_address"]=addr; self._save()
                        self._log(f"自动检测 ADB: {addr}"); break
                except: pass
            if addr:
                self._log(f"连接 ADB: {addr}")
                try: subprocess.run([adb,"connect",addr],capture_output=True,creationflags=CF,timeout=5)
                except Exception as e: self._log(f"ADB 连接失败: {e}")
            for w in progs:
                try: self._inj(w,a); self._ls(w)
                except Exception as e: self._log(f"失败: {e}"); QMessageBox.critical(self,"失败",str(e))
    def _dl_maa(self, row): self.maint.dl_maa(row)
    def _pk_maa(self, row): self.maint.pk_maa(row)
    # Launch
    def _ls(self,w):
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
    def _gtc(self, ac, w): return self.cfg.gtc(ac, w)
    def _inj(self, w, ac): self.cfg.inject(w, ac)
    # Pipeline
    def _start_pipeline(self):
        if not self.groups or (self.pipeline_thread and self.pipeline_thread.isRunning()): return
        self.stb.setEnabled(False); self.qs.setEnabled(False); self.spb.setEnabled(True); self.pab.setEnabled(True); self.pab.setText("暂停"); self._log("流水线启动")
        # Collect emulators to launch
        to_launch=[]
        launched=set()
        for a in self.accounts:
            emu_idx=a.get("emu_instance_index","")
            if a.get("emu_launch") and emu_idx and emu_idx not in launched:
                cli=find_mumu_cli()
                if cli:
                    to_launch.append((cli,emu_idx,a["name"],a.get("emu_wait",30)))
                    launched.add(emu_idx)
        def _start_thread():
            self.pipeline_thread=PipelineThread(self.groups,self.warehouse,self.accounts,self)
            self.pipeline_thread.progress.connect(lambda m:(self.sl.setText(m),self._log(m)))
            self.pipeline_thread.program_started.connect(lambda n,ok: self._log(f"启动 {n}" if ok else f"失败 {n}"))
            self.pipeline_thread.finished.connect(lambda s:(self.stb.setEnabled(True),self.qs.setEnabled(True),self.spb.setEnabled(False),self.pab.setEnabled(False)))
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
    def _stop_pipeline(self):
        if self.pipeline_thread: self.pipeline_thread.stop()
    def _pause_pipeline(self):
        if self.pipeline_thread and self.pipeline_thread.isRunning():
            if self.pab.text()=="暂停":
                self.pipeline_thread.pause(); self.pab.setText("继续"); self._log("流水线已暂停")
            else:
                self.pipeline_thread.resume(); self.pab.setText("暂停"); self._log("流水线已继续")

    from pipeline_thread import PipelineThread

    def _poll(self, ): self.maint.poll()
    def _notify(self, msg, is_error=False): self.maint.notify(msg, is_error)
    def _check_updates(self, silent=False): self.maint.check_updates(silent=False)
    def _cu_single(self, w): self.maint.cu_single(w)
    def _restore_geometry(self, ): self.maint.restore_geometry()
    def _setup_tray(self, ): self.maint.setup_tray()
    def _show_tray(self, ): self.maint.show_tray()
    def closeEvent(self,e):
        if not self.isMinimized():
            self.config["window_geometry"]=f"{self.width()}x{self.height()}+{self.x()}+{self.y()}"
            self._save()
        if self.config.get("minimize_to_tray",True) and hasattr(self,'tray_icon'): self.hide(); e.ignore()
        else:
            if hasattr(self,'_emu_monitor'): self._emu_monitor.quit(); self._emu_monitor.wait(2000)
            if hasattr(self,'schedule_thread'): self.schedule_thread.quit(); self.schedule_thread.wait(2000)
            if hasattr(self,'_api_server') and self._api_server: self._api_server.stop_server()
            e.accept(); QApplication.quit()
    def _tlog(self): self._log_expanded=not self._log_expanded; self.log_text.setFixedHeight(150 if self._log_expanded else 0)
    def _start_schedule(self, ): self.maint.start_schedule()
    def _sch(self, ): self.maint.sch()
    def _settings(self, ): self.maint.settings()
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
