import sys,json,os,ctypes,time,subprocess,re,shutil,io,socket
import urllib.request,urllib.error,zipfile,tempfile
from pathlib import Path
from datetime import datetime,time as dtime
from collections import defaultdict

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

def is_admin():
    try: return ctypes.windll.shell32.IsUserAnAdmin()!=0
    except: return False
def run_as_admin():
    ctypes.windll.shell32.ShellExecuteW(None,"runas",sys.executable,'"'+'" "'.join(sys.argv)+'"',None,1)
def make_id():
    import uuid; return uuid.uuid4().hex[:8]
def parse_maa_version(path):
    try:
        m=re.search(r'v?(\d+\.\d+\.\d+)',Path(path).parent.name)
        if m: return m.group(0) if m.group(0).startswith('v') else 'v'+m.group(0)
    except: pass
    return None
def get_platform_key():
    import platform; arch=platform.machine().lower()
    return f"win-{'x64' if arch in ('amd64','x86_64') else arch}"
def _version_tuple(v):
    try: return tuple(int(x) for x in v.lstrip('v').split('.'))
    except: return (0,)
def _rmtree_force(path):
    def on_error(func,p,exc_info):
        try: os.chmod(p,0o777); func(p)
        except: pass
    shutil.rmtree(path,onerror=on_error)
def _find_maa_cli():
    import shutil as _s
    for n in ("maa","maa-cli","maa.exe","maa-cli.exe"):
        if _s.which(n): return _s.which(n)
    for d in (Path(os.environ.get("LOCALAPPDATA",""))/"maa-cli",Path(__file__).parent/"maa-cli",Path("C:/Program Files/maa-cli")):
        for n in ("maa.exe","maa-cli.exe"):
            if (d/n).exists(): return str(d/n)
    return None

CONFIG_FILE=Path(__file__).parent/"config.json"
STARTUP_DIR=Path(os.environ['APPDATA'])/'Microsoft'/'Windows'/'Start Menu'/'Programs'/'Startup'

DEFAULT_CONFIG={"version":5,"appearance_mode":"Dark","window_geometry":"960x650","auto_start":False,
    "minimize_to_tray":True,"check_update_on_start":True,    "schedule":{"enabled":False,"type":"daily","time":"08:00","days_of_week":[]},"webhook_url":"",
    "warehouse":[],"groups":[],"accounts":[]}

def migrate_v4_to_v5(data):
    data.setdefault("accounts",[]); data.setdefault("check_update_on_start",True)
    for a in data.get("accounts",[]): a.setdefault("task_settings",{}); a.setdefault("sync_tasks",False); a.setdefault("account_switch",""); a.setdefault("emu_path",""); a.setdefault("emu_launch",False); a.setdefault("emu_wait",30); a.setdefault("emu_add_cmd",""); a.setdefault("emu_instance_index",""); a.setdefault("emu_instance_name",""); a.setdefault("post_action",""); a.setdefault("start_minimized",False); a.setdefault("start_directly",False); a.setdefault("adb_fail_launch_emu",False); a.setdefault("adb_retry",0); a.setdefault("stats",{})
    data.setdefault("webhook_url","")
    for w in data.get("warehouse",[]):
        for k,v in [("maa_type","general"),("maa_version",""),("update_channel","Stable"),
                     ("auto_update",False),("account_ref",""),("launch_mode","gui"),
                     ("task_pipeline",""),("guard_enabled",False),("guard_max_restart",3),
                     ("guard_capture_log",False)]: w.setdefault(k,v)
        w.setdefault("env",w.get("env",{}))
        if w.get("maa_type")=="general" and Path(w.get("path","")).stem.lower() in ("maa","maa.exe"):
            w["maa_type"]="maa"
            if not w["maa_version"]:
                v=parse_maa_version(w.get("path",""))
                if v: w["maa_version"]=v
    data["version"]=5; return data

def load_config():
    try:
        if CONFIG_FILE.exists():
            data=json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            ver=data.get("version",0)
            if ver in (2,3):
                data.setdefault("window_geometry","900x620"); data.setdefault("auto_start",False)
                data.setdefault("minimize_to_tray",True)
                warehouse=[]
                for g in data.get("groups",[]):
                    for p in g.get("programs",[]):
                        pth=p.get("path","")
                        ex=next((w for w in warehouse if w["path"]==pth),None)
                        pid=ex["id"] if ex else make_id()
                        if not ex: warehouse.append({"id":pid,"path":pth,"args":p.get("args",[]),"cwd":p.get("cwd",""),"env":{}})
                        pd=p.get("pre_delay",0); p.clear(); p["ref"]=pid; p["pre_delay"]=pd
                data["warehouse"]=warehouse; data["version"]=4
            if ver==4: data=migrate_v4_to_v5(data)
            if data.get("version",0)>=5:
                # Sanitize adb_address: fix encoding artifacts like "27.0.0.1" -> "127.0.0.1"
                for a in data.get("accounts",[]):
                    raw=a.get("adb_address","")
                    if raw and not raw.startswith("127.0.0.1:"):
                        m=re.search(r':(\d+)$',raw)
                        if m: a["adb_address"]="127.0.0.1:"+m.group(1)
                return data
    except: pass
    return dict(DEFAULT_CONFIG)

def save_config(data):
    try: CONFIG_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception as e:
        try: (Path(__file__).parent/"debug.log").open("a",encoding="utf-8").write(f"[ERR] save_config: {e}\n")
        except: pass

def set_auto_start(enabled):
    bp=STARTUP_DIR/"流水线启动器.bat"
    if enabled: bp.write_text(f'@start "" "{sys.executable}" "{Path(__file__).resolve()}"\n',encoding="utf-8")
    elif bp.exists(): bp.unlink()

DARK_STYLE="""QMainWindow,QDialog{background:#1e1e1e;color:#ccc}QLabel{color:#ccc}
QGroupBox{color:#ccc;border:1px solid #3c3c3c;border-radius:6px;margin-top:10px;padding-top:10px}
QPushButton{background:#333;color:#ccc;border:1px solid #555;border-radius:6px;padding:5px 14px;min-height:26px}
QPushButton:hover{background:#444}QPushButton:disabled{background:#2a2a2a;color:#666}
QPushButton#startBtn{background:#265d33;color:#a0d0a0;border-color:#2b6a3a;font-weight:bold}QPushButton#startBtn:hover{background:#1e5a28;color:#fff}
QPushButton#stopBtn{background:#5d2626;color:#d0a0a0;border-color:#6a2b2b}QPushButton#stopBtn:hover{background:#8e0000;color:#fff}
QPushButton#addProgBtn{background:#26405d;color:#a0c0d0;font-weight:bold;border-color:#2b4a6a}QPushButton#addProgBtn:hover{background:#1f5380;color:#fff}
QPushButton#tabBtn{background:transparent;color:#777;border:none;padding:6px 18px;font-size:13px;border-radius:0}QPushButton#tabBtn:hover{color:#aaa;border-bottom:2px solid #444}
QPushButton#tabBtnActive{background:transparent;color:#ccc;border:none;padding:6px 18px;font-size:13px;border-bottom:2px solid #666;border-radius:0}
QLineEdit,QSpinBox,QComboBox{background:#353535;color:#ccc;border:1px solid #444;border-radius:4px;padding:5px 8px;min-height:24px}
QLineEdit:focus,QSpinBox:focus{border:1px solid #666}
QComboBox::drop-down{background:#333;border:none}QComboBox QAbstractItemView{background:#333;color:#ccc;selection-background-color:#3a7ebf}
QTableWidget{background:#252526;color:#bbb;border:1px solid #333;alternate-background-color:#28282e}
QTableWidget::item{padding:4px 8px}QTableWidget::item:selected{background:#2a3a4a;color:#ddd}
QHeaderView::section{background:#2a2a2a;color:#888;border:none;border-bottom:1px solid #333;padding:6px 8px;font-weight:bold;font-size:11px}
QPlainTextEdit{background:#1a1a1a;color:#aaa;border:1px solid #333;font-family:Consolas;font-size:12px;border-radius:4px}
QMenu{background:#2a2a2a;color:#bbb;border:1px solid #3a3a3a;border-radius:6px;padding:4px}QMenu::item{padding:6px 28px 6px 12px;border-radius:3px}QMenu::item:selected{background:#3a3a3a;color:#ddd}QMenu::separator{height:1px;background:#3a3a3a;margin:4px 8px}
QMenuBar{background:#252526;color:#aaa;border:none}QMenuBar::item{padding:6px 14px}QMenuBar::item:selected{background:#333;color:#ddd;border-radius:4px}
QCheckBox{color:#bbb;spacing:6px}QCheckBox::indicator{width:16px;height:16px;border:2px solid #444;border-radius:3px;background:#333}QCheckBox::indicator:checked{background:#555;border-color:#666}
QProgressBar{border:1px solid #3a3a3a;border-radius:4px;background:#333;color:#aaa;text-align:center;height:18px}QProgressBar::chunk{background:#555;border-radius:3px}
QScrollArea{border:none;background:transparent}
QListWidget{background:#252526;color:#bbb;border:1px solid #333;border-radius:6px}QListWidget::item{padding:6px 10px;border-radius:3px}QListWidget::item:hover{background:#303030}QListWidget::item:selected{background:#2a3a4a;color:#ddd}
QFrame#card{background:#282830;border:1px solid #353535;border-radius:8px;padding:12px;margin-bottom:6px}
QStatusBar{background:#1e1e1e;color:#888;border-top:1px solid #333}QStatusBar QLabel{color:#888}QStatusBar QPushButton{background:transparent;color:#888;border:1px solid #333;border-radius:4px;padding:2px 10px;min-height:22px}QStatusBar QPushButton:hover{color:#ccc;border-color:#555}
QTabWidget::pane{border:1px solid #333;border-top:none;background:#252526}QTabBar::tab{background:#2a2a2a;color:#777;padding:7px 18px;border:1px solid #333;border-bottom:none;margin-right:2px;border-radius:4px 4px 0 0}QTabBar::tab:selected{background:#252526;color:#aaa;font-weight:bold}QTabBar::tab:hover{color:#ccc}
"""

LIGHT_STYLE="""QMainWindow,QDialog{background:#f0f0f0;color:#333}QLabel{color:#333}
QGroupBox{color:#333;border:1px solid #d5d5d5;border-radius:6px;margin-top:10px;padding-top:10px}
QPushButton{background:#e8e8e8;color:#333;border:1px solid #ccc;border-radius:6px;padding:5px 14px;min-height:26px}
QPushButton:hover{background:#ddd}QPushButton:disabled{background:#eee;color:#999}
QPushButton#startBtn{background:#3a6b4a;color:#fff;border-color:#3a6b4a;font-weight:bold}QPushButton#startBtn:hover{background:#2e543a}
QPushButton#stopBtn{background:#8b4a4a;color:#fff;border-color:#8b4a4a}QPushButton#stopBtn:hover{background:#6b3535}
QPushButton#addProgBtn{background:#4a608b;color:#fff;font-weight:bold;border-color:#4a608b}QPushButton#addProgBtn:hover{background:#354570}
QPushButton#tabBtn{background:transparent;color:#666;border:none;padding:6px 18px;font-size:13px;border-radius:0}QPushButton#tabBtn:hover{color:#333;border-bottom:2px solid #ccc}
QPushButton#tabBtnActive{background:transparent;color:#333;border:none;padding:6px 18px;font-size:13px;border-bottom:2px solid #888;border-radius:0}
QLineEdit,QSpinBox,QComboBox{background:#fff;color:#333;border:1px solid #ccc;padding:5px 8px}
QComboBox:disabled,QSpinBox:disabled{background:#eaeaea;color:#888}
QLineEdit:focus,QSpinBox:focus{border:1px solid #999}
QTableWidget{background:#fff;color:#333;border:1px solid #ddd}QTableWidget::item:selected{background:#e0e8f0;color:#333}
QHeaderView::section{background:#f5f5f5;color:#666;border:none;border-bottom:1px solid #ddd;padding:6px 8px;font-weight:bold}
QPlainTextEdit{background:#fff;color:#333;border:1px solid #ddd;font-family:Consolas;font-size:12px}
QMenu{background:#fff;color:#333;border:1px solid #ddd;border-radius:6px;padding:4px}QMenu::item:selected{background:#e8e8e8;color:#333}
QMenuBar{background:#f5f5f5;color:#555;border:none}QMenuBar::item:selected{background:#e0e0e0;color:#333;border-radius:4px}
QListWidget{background:#fff;color:#333;border:1px solid #ddd;border-radius:6px}QListWidget::item:hover{background:#f0f0f0}QListWidget::item:selected{background:#e0e8f0;color:#333}
QCheckBox{color:#333}QCheckBox::indicator:checked{background:#888;border-color:#888}
QProgressBar{border:1px solid #ddd;border-radius:4px;background:#f5f5f5;color:#555}QProgressBar::chunk{background:#aaa}
QScrollArea{border:none;background:transparent}QScrollBar:vertical{background:transparent;width:8px}QScrollBar::handle:vertical{background:#ccc;border-radius:4px}QScrollBar:horizontal{background:transparent;height:8px}QScrollBar::handle:horizontal{background:#ccc;border-radius:4px}
QSplitter::handle{background:#ddd;width:3px}
QFrame#card{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:12px;margin-bottom:6px}
QStatusBar{background:#f0f0f0;color:#888;border-top:1px solid #ddd}QStatusBar QLabel{color:#888}
QTabWidget::pane{border:1px solid #ddd;background:#fff}QTabBar::tab{background:#eee;color:#777;padding:7px 18px;border:1px solid #ddd;margin-right:2px;border-radius:4px 4px 0 0}QTabBar::tab:selected{background:#fff;color:#333}QTabBar::tab:hover{color:#555}
"""

class UpdateCheckThread(QThread):
    result_ready=Signal(dict)
    def run(self):
        try:
            req=urllib.request.Request("https://api.github.com/repos/MaaAssistantArknights/MaaAssistantArknights/releases/latest",headers={"User-Agent":"MAA-Launcher"})
            with urllib.request.urlopen(req,timeout=15) as r: data=json.loads(r.read().decode())
            tag=data.get("tag_name",""); assets={}
            for a in data.get("assets",[]):
                n=a.get("name","")
                if any(k in n.lower() for k in ("debugsymbol","component")): continue
                if "win-x64" in n and n.endswith(".zip"): assets["win-x64"]={"url":a.get("browser_download_url",""),"size":a.get("size",0),"name":n}
                elif "win-arm64" in n and n.endswith(".zip"): assets["win-arm64"]={"url":a.get("browser_download_url",""),"size":a.get("size",0),"name":n}
            self.result_ready.emit({"ok":True,"tag":tag,"assets":assets})
        except Exception as e: self.result_ready.emit({"ok":False,"error":str(e)})

class DownloadThread(QThread):
    progress=Signal(int,int); status=Signal(str); finished=Signal(bool,str)
    def __init__(self,url,target,name):
        super().__init__(); self.u=url; self.t=target; self.n=name; self.c=False
    def cancel(self): self.c=True
    def run(self):
        tmp=None
        try:
            self.status.emit("下载中...")
            req=urllib.request.Request(self.u,headers={"User-Agent":"MAA-Launcher"})
            with urllib.request.urlopen(req,timeout=600) as r:
                total=r.length or 0; buf=bytearray(); dl=0
                while True:
                    if self.c: self.finished.emit(False,"取消"); return
                    chunk=r.read(65536)
                    if not chunk: break
                    buf.extend(chunk); dl+=len(chunk)
                    if total: self.progress.emit(dl,total)
            self.progress.emit(1,1); self.status.emit("解压...")
            tmp=tempfile.mkdtemp(prefix="maa_")
            with zipfile.ZipFile(io.BytesIO(bytes(buf))) as zf: zf.extractall(tmp)
            tgt=Path(self.t); tgt.mkdir(parents=True,exist_ok=True)
            items=list(Path(tmp).iterdir())
            src=items[0] if len(items)==1 and items[0].is_dir() else Path(tmp)
            for item in Path(src).iterdir():
                dest=tgt/item.name
                if item.is_dir():
                    if dest.exists(): _rmtree_force(str(dest))
                    shutil.copytree(str(item),str(dest))
                else:
                    try: shutil.copy2(str(item),str(dest))
                    except PermissionError:
                        d2=str(dest)+".new"; shutil.copy2(str(item),d2)
                        try: Path(d2).replace(dest)
                        except: shutil.copy2(d2,str(dest)); Path(d2).unlink()
            self.finished.emit(True,"完成")
        except Exception as e: self.finished.emit(False,str(e))
        finally:
            if tmp and Path(tmp).exists(): _rmtree_force(tmp)

class MaacliInstallThread(QThread):
    progress=Signal(str); finished=Signal(bool,str)
    def __init__(self,d): super().__init__(); self.d=d
    def run(self):
        try:
            self.progress.emit("获取版本...")
            req=urllib.request.Request("https://api.github.com/repos/MaaAssistantArknights/maa-cli/releases/latest",headers={"User-Agent":"MAA-Launcher"})
            with urllib.request.urlopen(req,timeout=15) as r: data=json.loads(r.read().decode())
            url=None
            for a in data.get("assets",[]):
                if "windows" in a.get("name","").lower() and "x86_64" in a.get("name","") and a.get("name","").endswith(".zip"):
                    url=a["browser_download_url"]; break
            if not url: self.finished.emit(False,"未找到下载包"); return
            self.progress.emit("下载...")
            req2=urllib.request.Request(url,headers={"User-Agent":"MAA-Launcher"})
            with urllib.request.urlopen(req2,timeout=120) as r: buf=r.read()
            tmp=tempfile.mkdtemp(prefix="cli_")
            with zipfile.ZipFile(io.BytesIO(buf)) as zf: zf.extractall(tmp)
            items=list(Path(tmp).iterdir())
            src=items[0] if len(items)==1 and items[0].is_dir() else Path(tmp)
            d=Path(self.d); d.mkdir(parents=True,exist_ok=True)
            for item in Path(src).iterdir():
                dest=d/item.name
                if item.is_dir():
                    if dest.exists(): _rmtree_force(str(dest))
                    shutil.copytree(str(item),str(dest))
                else: shutil.copy2(str(item),str(dest))
            _rmtree_force(tmp); self.finished.emit(True,"完成")
        except Exception as e: self.finished.emit(False,str(e))

class MaacliInstallDialog(QDialog):
    def __init__(self,p):
        super().__init__(p); self.setWindowTitle("安装 maa-cli"); self.setFixedSize(380,120)
        l=QVBoxLayout(self); l.addWidget(QLabel("正在安装 maa-cli..."))
        self.s=QLabel("准备中..."); l.addWidget(self.s)
        self.b=QProgressBar(); self.b.setRange(0,0); l.addWidget(self.b)
    def start(self,d):
        self.t=MaacliInstallThread(d); self.t.progress.connect(self.s.setText)
        self.t.finished.connect(lambda ok,msg: self.accept() if ok else (QMessageBox.critical(self,"失败",msg),self.reject()))
        self.t.start()

class UpdateDialog(QDialog):
    def __init__(self,p,ver,info,tgt):
        super().__init__(p); self.setWindowTitle("MAA 更新"); self.setFixedSize(420,200); self.i=info; self.t=tgt
        l=QVBoxLayout(self)
        l.addWidget(QLabel(f"版本: {ver}",font=QFont("Microsoft YaHei UI",13,QFont.Bold)))
        l.addWidget(QLabel(f"大小: {info['size']/1024/1024:.1f} MB"))
        self.b=QProgressBar(); self.b.setVisible(False); l.addWidget(self.b)
        self.s=QLabel(""); l.addWidget(self.s)
        bl=QHBoxLayout(); self.d=QPushButton("下载"); self.d.clicked.connect(self._dl); bl.addWidget(self.d)
        bl.addWidget(QPushButton("取消",clicked=self.reject)); l.addLayout(bl)
    def _dl(self):
        self.d.setEnabled(False); self.b.setVisible(True)
        self.t=DownloadThread(self.i["url"],self.t,self.i["name"])
        self.t.progress.connect(lambda c,t:(self.b.setMaximum(t),self.b.setValue(c)))
        self.t.status.connect(self.s.setText)
        self.t.finished.connect(lambda ok,msg: self.accept() if ok else (QMessageBox.critical(self,"失败",msg),self.d.setEnabled(True)))
        self.t.start()

TASK_NAMES={"StartUp":"启动游戏","Fight":"刷关作战","Recruit":"公开招募","Infrast":"基建换班",
    "Mall":"信用商店","Award":"领取奖励","Roguelike":"肉鸽探索","Reclamation":"生息演算","closedown":"关闭游戏"}
TASK_DEFAULTS={
    "StartUp":{"client_type":"Official"},
    "Fight":{"stage":"","medicine":0,"times":99,"use_expiring_medicine":True,"medicine_expire_days":2,"use_stone":False,"stone":0,"enable_times_limit":False,"stage_reset_mode":"Current","annihilation_stage":"Annihilation","use_custom_annihilation":False,"hide_unavailable_stage":False},
    "Recruit":{"select":[3,4,5],"confirm":[3,4,5],"times":4,"refresh":True,"force_refresh":True,"prefer_tag_enabled":True,"preserve_tag_enabled":False,"preserve_tags":"支援机械","level3_time":540,"level4_time":540,"level5_time":540},
    "Infrast":{"mode":"Normal","facilities":["Trade","Mfg","Control","Power","Reception","Office","Dorm"],"drones":"Money","dorm_threshold":30,"dorm_trust_enabled":True,"originium_shard_auto":True,"reception_clue":True,"send_clue":True,"continue_training":False,"filename":""},
    "Mall":{"shopping":True,"credit_fight":False,"visit_friends":True,"first_list":"招聘许可","blacklist":"碳;家具","only_buy_discount":False,"reserve_max_credit":False},
    "Award":{"award":True,"mail":False,"free_gacha":False,"orundum":False,"mining":False,"special_access":False},
    "Roguelike":{"theme":"Sarkaz","mode":0,"difficulty":15,"squad":"","roles":"","core_char":"","start_count":99999,"investment":True,"invest_count":999,"stop_when_level_max":False,"stop_when_deposit_full":False,"use_support":False,"start_with_seed":False,"seed":""},
    "Reclamation":{"theme":"Tales","mode":"ProsperityInSave","tool_to_craft":"","max_craft_count":16,"clear_store":False},
}
EMU_PRESETS=[
    {"name":"MuMu 12(默认)","type":"MuMuEmulator12","ports":[str(16384+i*32) for i in range(100)],"detect":"MuMu 12"},
    {"name":"MuMu 6(默认)","type":"MuMu","ports":["7555"],"detect":"MuMu 6"},
    {"name":"MuMu Pro","type":"MuMuPro","ports":["16384"],"detect":"MuMu 12"},
    {"name":"雷电 9(默认)","type":"LDPlayer","ports":["5555"],"detect":"雷电 9"},
    {"name":"雷电 4","type":"LDPlayer","ports":["5555"],"detect":"雷电 9"},
    {"name":"蓝叠 国际版","type":"BlueStacks","ports":["5555"],"detect":"蓝叠"},
    {"name":"蓝叠 中国版","type":"BlueStacks","ports":["5555"],"detect":"蓝叠"},
    {"name":"夜神","type":"Nox","ports":["62001"],"detect":"夜神"},
    {"name":"逍遥","type":"XYAZ","ports":["21503"],"detect":"逍遥"},
    {"name":"自定义","type":"General","ports":["5555"],"detect":""},
]

MUMU_INSTANCE_DIRS=[
    Path(os.environ.get("APPDATA",""))/"Netease"/"MuMuPlayer-12.0"/"vms",
    Path(os.environ.get("LOCALAPPDATA",""))/"Netease"/"MuMuPlayer-12.0"/"vms",
    Path("D:/Program Files/Netease/MuMuPlayer-12.0/vms"),
    Path("C:/Program Files/Netease/MuMuPlayer-12.0/vms"),
]

MUMU_CLI_CANDIDATES=[
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\mumu-cli.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\nx_main\mumu-cli.exe",
    r"C:\Program Files\Netease\MuMuPlayer\nx_main\mumu-cli.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\mumu-cli.exe",
    r"D:\Program Files\Netease\MuMuPlayer-12.0\nx_main\mumu-cli.exe",
    r"C:\Program Files (x86)\Netease\MuMuPlayer\nx_main\mumu-cli.exe",
]
# Also check MUMU_CLI_HOME env var for custom installs
if (ev:=os.environ.get("MUMU_CLI_HOME","")):
    MUMU_CLI_CANDIDATES.insert(0,str(Path(ev)/"mumu-cli.exe"))

def find_mumu_cli():
    for c in MUMU_CLI_CANDIDATES:
        if Path(c).exists(): return c
    # Search drives for MuMuPlayer
    for drv in "CDEFGH":
        base=Path(f"{drv}:\\")
        if not base.exists(): continue
        try:
            for d in base.iterdir():
                if d.is_dir() and "mumu" in d.name.lower():
                    for sub in ["nx_main\\mumu-cli.exe","shell\\mumu-cli.exe"]:
                        p=d/sub
                        if p.exists(): return str(p)
        except: pass
    return None

def detect_emu_instances():
    """Detect all emulator instances via mumu-cli or directory scan"""
    instances=[]
    cli=find_mumu_cli()
    if cli:
        try:
            r=subprocess.run([cli,"info","--vmindex","all"],capture_output=True,text=True,timeout=10,creationflags=CF,encoding="utf-8",errors="replace")
            if r.stdout.strip():
                data=json.loads(r.stdout)
                for idx,info in data.items():
                    if isinstance(info,dict):
                        instances.append({
                            "emu":"MuMu","name":info.get("name",idx),
                            "index":idx,"adb_port":str(info.get("adb_port","")),
                            "running":info.get("is_process_started",False) or info.get("is_android_started",False)
                        })
                return instances
        except: pass
    # Fallback: directory scan
    for vms_dir in MUMU_INSTANCE_DIRS:
        if vms_dir.exists():
            for vm in sorted(vms_dir.iterdir()):
                if vm.is_dir() and (vm/"config.json").exists():
                    try:
                        cfg=json.loads((vm/"config.json").read_text(encoding="utf-8"))
                        name=cfg.get("vm_name",vm.name)
                        adb_port=cfg.get("adb_port","")
                        instances.append({"emu":"MuMu 12","name":name,"index":vm.name,"adb_port":str(adb_port),"path":str(vm)})
                    except: pass
            if instances: break
    # MuMu 6 instances
    for base in [Path(os.environ.get("APPDATA",""))/"Netease"/"MuMu",
                 Path("D:/Program Files/Netease/MuMu/emulator/nemu/vms"),
                 Path("C:/Program Files/Netease/MuMu/emulator/nemu/vms")]:
        if base.exists():
            for vm in sorted(base.iterdir()):
                if vm.is_dir(): instances.append({"emu":"MuMu 6","name":vm.name,"index":vm.name,"adb_port":"","path":str(vm)})
            if instances: break
    # LDPlayer instances
    for base in [Path("C:/leidian/LDPlayer9/vms"),Path("D:/leidian/LDPlayer9/vms")]:
        if base.exists():
            for vm in sorted(base.iterdir()):
                if vm.is_dir(): instances.append({"emu":"雷电 9","name":vm.name,"index":vm.name,"adb_port":"","path":str(vm)})
            if instances: break
    return instances
CLIENT_TYPES={"Official":"官服","Bilibili":"B服","YoStarEN":"国际服","YoStarJP":"日服","YoStarKR":"韩服","txwy":"繁中"}
CF=subprocess.CREATE_NO_WINDOW

class EmuMonitor(QThread):
    updated=Signal(list)
    def run(self):
        while True:
            cli=find_mumu_cli()
            if cli:
                try:
                    r=subprocess.run([cli,"info","--vmindex","all"],capture_output=True,text=True,timeout=8,creationflags=CF,encoding="utf-8",errors="replace")
                    if r.stdout.strip():
                        data=json.loads(r.stdout)
                        results=[]
                        for idx,info in data.items():
                            if isinstance(info,dict):
                                results.append({"name":info.get("name",idx),"index":idx,"running":info.get("is_process_started",False) or info.get("is_android_started",False)})
                        self.updated.emit(results)
                except: pass
            time.sleep(30)

class ScheduleDialog(QDialog):
    def __init__(self,p,d):
        super().__init__(p); self.setWindowTitle("定时"); self.setFixedSize(380,280)
        l=QVBoxLayout(self); self.e=QCheckBox("启用"); self.e.setChecked(d.get("enabled",False)); l.addWidget(self.e)
        g=QGroupBox("时间"); gl=QFormLayout(g)
        self.c=QComboBox(); self.c.addItems(["每天","每周"]); self.c.setCurrentText({"daily":"每天","weekly":"每周"}.get(d.get("type","daily"),"每天")); gl.addRow("重复:",self.c)
        self.t=QLineEdit(d.get("time","08:00")); gl.addRow("时间:",self.t); l.addWidget(g)
        dw=QWidget(); dl=QHBoxLayout(dw); dl.addWidget(QLabel("星期:")); self.dc=[]
        for i,dn in enumerate(["一","二","三","四","五","六","日"]):
            cb=QCheckBox(dn); cb.setChecked(i in d.get("days_of_week",[])); self.dc.append(cb); dl.addWidget(cb)
        dl.addStretch(); l.addWidget(dw)
        self._up(); self.e.toggled.connect(self._up); self.c.currentTextChanged.connect(self._up)
        b=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); b.accepted.connect(self._sv); b.rejected.connect(self.reject); l.addWidget(b)
    def _up(self):
        en=self.e.isChecked(); self.c.setEnabled(en); self.t.setEnabled(en)
        for cb in self.dc: cb.setEnabled(en and self.c.currentText()=="每周")
    def _sv(self):
        self.r={"enabled":self.e.isChecked(),"type":{"每天":"daily","每周":"weekly"}.get(self.c.currentText(),"daily"),"time":self.t.text().strip(),"days_of_week":[i for i,cb in enumerate(self.dc) if cb.isChecked()]}; self.accept()

class SettingsDialog(QDialog):
    def __init__(self,p,cfg):
        super().__init__(p); self.setWindowTitle("设置"); self.setMinimumWidth(420); self.c=cfg
        l=QVBoxLayout(self); l.setSpacing(8)
        l.addWidget(QLabel("设置",font=QFont("Microsoft YaHei UI",15,QFont.Bold)))
        # 外观
        g=QGroupBox("外观"); gl=QVBoxLayout(g)
        th=QHBoxLayout(); th.addWidget(QLabel("主题:"))
        self.th=QComboBox(); self.th.addItems(["Dark","Light"]); self.th.setCurrentText(cfg.get("appearance_mode","Dark")); th.addWidget(self.th,1); gl.addLayout(th); l.addWidget(g)
        # 启动
        g2=QGroupBox("启动"); gl2=QVBoxLayout(g2)
        self.auto=QCheckBox("开机自启"); self.auto.setChecked(cfg.get("auto_start",False)); gl2.addWidget(self.auto)
        self.tray=QCheckBox("关闭时最小化到托盘"); self.tray.setChecked(cfg.get("minimize_to_tray",True)); gl2.addWidget(self.tray); l.addWidget(g2)
        # 通知
        g4=QGroupBox("通知"); gl4=QVBoxLayout(g4)
        self.cu=QCheckBox("启动时检查更新"); self.cu.setChecked(cfg.get("check_update_on_start",True)); gl4.addWidget(self.cu)
        wr=QHBoxLayout(); wr.addWidget(QLabel("Webhook:"))
        self.wh=QLineEdit(cfg.get("webhook_url","")); self.wh.setPlaceholderText("企业微信/钉钉/自定义 URL"); wr.addWidget(self.wh,1)
        wh_tb=QPushButton("测试"); wh_tb.clicked.connect(lambda: self._test_webhook(self.wh.text().strip())); wr.addWidget(wh_tb); gl4.addLayout(wr); l.addWidget(g4)
        # 配置
        g3=QGroupBox("配置"); gl3=QHBoxLayout(g3)
        gl3.addWidget(QPushButton("导出",clicked=self._ex)); gl3.addWidget(QPushButton("导入",clicked=self._im)); l.addWidget(g3)
        l.addStretch()
        b=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); b.accepted.connect(self._sv); b.rejected.connect(self.reject); l.addWidget(b)
    def _ex(self):
        fp,_=QFileDialog.getSaveFileName(self,"导出","config.json","JSON (*.json)")
        if fp: Path(fp).write_text(json.dumps({k:v for k,v in self.c.items() if k!="window_geometry"},ensure_ascii=False,indent=2),encoding="utf-8")
    def _im(self):
        fp,_=QFileDialog.getOpenFileName(self,"导入","","JSON (*.json)")
        if fp:
            d=json.loads(Path(fp).read_text(encoding="utf-8"))
            if isinstance(d.get("groups"),list): self.c.update(d); self.c["version"]=5
    def _test_webhook(self,url):
        if not url: QMessageBox.warning(self,"提示","请先输入 Webhook URL"); return
        try:
            req=urllib.request.Request(url,data=json.dumps({"msgtype":"text","text":{"content":"MAAOrch Webhook 测试"}}).encode(),headers={"Content-Type":"application/json"},method="POST")
            urllib.request.urlopen(req,timeout=10)
            QMessageBox.information(self,"成功","Webhook 测试成功")
        except Exception as e: QMessageBox.warning(self,"失败",f"发送失败:\n{e}")
    def _sv(self):
        self.c["appearance_mode"]=self.th.currentText(); self.c["auto_start"]=self.auto.isChecked(); self.c["minimize_to_tray"]=self.tray.isChecked();         self.c["check_update_on_start"]=self.cu.isChecked(); self.c["webhook_url"]=self.wh.text().strip()
        set_auto_start(self.c["auto_start"]); self.accept()

class AccountDialog(QDialog):
    def __init__(self,p,acc=None):
        super().__init__(p); self.setWindowTitle("编辑账号" if acc else "新建账号"); self.setFixedSize(500,460); self.a=acc or {}
        l=QVBoxLayout(self); l.setSpacing(6); l.addWidget(QLabel("MAA 账号配置",font=QFont("Microsoft YaHei UI",14,QFont.Bold)))
        f=QFormLayout(); f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow); f.setVerticalSpacing(8)
        s=QLabel("基本信息"); s.setStyleSheet("font-weight:bold;border-bottom:1px solid #555"); f.addRow(s)
        self.n=QLineEdit(self.a.get("name","")); self.n.setPlaceholderText("例如: 官服大号"); f.addRow("账号名:",self.n)
        self.c=QComboBox()
        for k,v in CLIENT_TYPES.items(): self.c.addItem(v,k)
        idx=self.c.findData(self.a.get("game_client","Official")); self.c.setCurrentIndex(max(0,idx)); f.addRow("区服:",self.c)
        s2=QLabel("连接设置"); s2.setStyleSheet("font-weight:bold;border-bottom:1px solid #555;margin-top:8px"); f.addRow(s2)
        self.adb=QLineEdit(self.a.get("adb_path","")); self.adb.setPlaceholderText("留空使用默认 ADB"); f.addRow("ADB 路径:",self.adb)
        self.adr=QLineEdit(self.a.get("adb_address","")); self.adr.setPlaceholderText("例如: 127.0.0.1:7555"); f.addRow("连接地址:",self.adr)
        self.pc=QComboBox(); self.pc.addItems(["— 无 —","MuMuPro","PlayCover","Waydroid"]); self.pc.setCurrentText(self.a.get("connection_preset") or "— 无 —"); f.addRow("预设:",self.pc)
        self.tc=QComboBox(); self.tc.addItems(["ADB","MiniTouch","MaaTouch"]); self.tc.setCurrentText(self.a.get("touch_mode","ADB")); f.addRow("触控:",self.tc)
        s3=QLabel("默认任务"); s3.setStyleSheet("font-weight:bold;border-bottom:1px solid #555;margin-top:8px"); f.addRow(s3)
        self.tk={}; kw=QWidget(); kl=QHBoxLayout(kw); kl.setContentsMargins(0,0,0,0)
        for k,v in TASK_NAMES.items():
            if k=="closedown": continue
            cb=QCheckBox(v); cb.setChecked(k in self.a.get("tasks",["StartUp","Fight"])); self.tk[k]=cb; kl.addWidget(cb)
        kl.addStretch(); f.addRow("任务:",kw)
        self.fs=QLineEdit(self.a.get("fight_stage","")); self.fs.setPlaceholderText("关卡，如 1-7"); f.addRow("关卡:",self.fs)
        l.addLayout(f); l.addStretch()
        b=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); b.accepted.connect(self._save); b.rejected.connect(self.reject); l.addWidget(b)
    def _save(self):
        p=self.pc.currentText()
        if p=="— 无 —": p=""
        self.r={"id":self.a.get("id",make_id()),"name":self.n.text().strip() or "未命名","game_client":self.c.currentData(),"adb_path":self.adb.text().strip(),"adb_address":self.adr.text().strip(),"connection_preset":p,"touch_mode":self.tc.currentText(),"tasks":[t for t,cb in self.tk.items() if cb.isChecked()],"fight_stage":self.fs.text().strip(),"task_settings":self.a.get("task_settings",{}),"sync_tasks":self.a.get("sync_tasks",False),"account_switch":self.a.get("account_switch",""),"emu_path":self.a.get("emu_path",""),"emu_launch":self.a.get("emu_launch",False),"emu_wait":self.a.get("emu_wait",60)}; self.accept()

class TaskSettingsDialog(QDialog):
    def __init__(self,p,settings,pipe):
        super().__init__(p); self.setWindowTitle("任务参数配置"); self.setMinimumSize(580,480)
        self.s=settings; self.pipe=[t.strip().lower() for t in pipe.split(",") if t.strip()] if pipe else []
        l=QVBoxLayout(self); tabs=QTabWidget(); self._editors={}
        dl={k.lower():k for k in TASK_DEFAULTS}
        for tl in sorted(set(self.pipe)&set(dl.keys()),key=lambda x:list(dl.keys()).index(x)):
            tk=dl[tl]
            sw=QScrollArea(); sw.setWidgetResizable(True); sw.setFrameShape(QFrame.NoFrame)
            w=QWidget(); fl=QFormLayout(w); fl.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            ts=self.s.get(tk,TASK_DEFAULTS[tk].copy()); eds={}
            if tk=="StartUp":
                cb=QComboBox()
                for k,v in CLIENT_TYPES.items(): cb.addItem(v,k)
                idx=cb.findData(ts.get("client_type","Official")); cb.setCurrentIndex(max(0,idx)); fl.addRow("客户端:",cb); eds["client_type"]=cb
            elif tk=="Fight":
                fe=QLineEdit(ts.get("stage","")); fe.setPlaceholderText("关卡，如 1-7"); fl.addRow("关卡:",fe); eds["stage"]=fe
                ms=QSpinBox(); ms.setRange(0,999); ms.setValue(ts.get("medicine",0)); fl.addRow("理智药:",ms); eds["medicine"]=ms
                tm=QSpinBox(); tm.setRange(1,9999); tm.setValue(ts.get("times",99)); fl.addRow("次数上限:",tm); eds["times"]=tm
                for lb,ky in [("使用将过期药","use_expiring_medicine"),("使用源石","use_stone"),("次数限制","enable_times_limit"),("自定义剿灭","use_custom_annihilation"),("隐藏不可用关卡","hide_unavailable_stage")]:
                    cb=QCheckBox(lb); cb.setChecked(ts.get(ky,False)); fl.addRow(cb); eds[ky]=cb
                    if ky=="use_stone":
                        sc=QSpinBox(); sc.setRange(0,999); sc.setValue(ts.get("stone",0)); fl.addRow("源石数:",sc); eds["stone"]=sc
                ed=QSpinBox(); ed.setRange(1,7); ed.setValue(ts.get("medicine_expire_days",2)); fl.addRow("过期天数:",ed); eds["medicine_expire_days"]=ed
                sr=QComboBox(); sr.addItems(["当前关卡","忽略"]); sr.setCurrentIndex(0 if ts.get("stage_reset_mode","Current")=="Current" else 1); fl.addRow("关卡重置:",sr); eds["stage_reset_mode"]=sr
                an=QLineEdit(ts.get("annihilation_stage","Annihilation")); fl.addRow("剿灭关卡:",an); eds["annihilation_stage"]=an
            elif tk=="Recruit":
                for lb,ky in [("选择 3/4/5 星","select"),("确认 3/4/5 星","confirm")]:
                    rw=QWidget(); rwl=QHBoxLayout(rw); rwl.setContentsMargins(0,0,0,0)
                    for lv,ln in [(3,"3星"),(4,"4星"),(5,"5星")]:
                        cb=QCheckBox(ln); cb.setChecked(lv in ts.get(ky,[3,4,5])); rwl.addWidget(cb); eds[f"{ky}{lv}"]=cb
                    fl.addRow(lb,rw)
                rt=QSpinBox(); rt.setRange(1,99); rt.setValue(ts.get("times",4)); fl.addRow("次数:",rt); eds["times"]=rt
                for lb,ky in [("自动刷新","refresh"),("强制刷新3星","force_refresh"),("首选标签","prefer_tag_enabled"),("保留词条","preserve_tag_enabled")]:
                    cb=QCheckBox(lb); cb.setChecked(ts.get(ky,False)); fl.addRow(cb); eds[ky]=cb
                pt=QLineEdit(ts.get("preserve_tags","支援机械")); fl.addRow("保留词条:",pt); eds["preserve_tags"]=pt
                for lv,ln in [(3,"3星时间"),(4,"4星时间"),(5,"5星时间")]:
                    sp=QSpinBox(); sp.setRange(60,540); sp.setSingleStep(60); sp.setValue(ts.get(f"level{lv}_time",540)); fl.addRow(ln,sp); eds[f"level{lv}_time"]=sp
            elif tk=="Infrast":
                fw=QWidget(); fwl=QHBoxLayout(fw); fwl.setContentsMargins(0,0,0,0)
                fm={"Trade":"贸易","Mfg":"制造","Control":"控制","Power":"发电","Reception":"会客","Office":"办公","Dorm":"宿舍"}
                for f in fm: cb=QCheckBox(fm[f]); cb.setChecked(f in ts.get("facilities",list(fm.keys()))); fwl.addWidget(cb); eds[f"fac_{f}"]=cb
                fl.addRow("设施:",fw)
                for lb,ky,opts in [("无人机:",("drones","Money"),["赤金","合成玉","不使用"]),("模式:",("mode","Normal"),["默认","轮换","自定义"])]:
                    mb=QComboBox(); mb.addItems(opts); mb.setCurrentIndex(0); fl.addRow(lb,mb); eds[ky]=mb
                dt=QSpinBox(); dt.setRange(0,100); dt.setValue(ts.get("dorm_threshold",30)); fl.addRow("宿舍阈值:",dt); eds["dorm_threshold"]=dt
                for lb,ky in [("宿舍信赖","dorm_trust_enabled"),("自动补碎石","originium_shard_auto"),("线索交流","reception_clue"),("传递线索","send_clue"),("继续训练","continue_training")]:
                    cb=QCheckBox(lb); cb.setChecked(ts.get(ky,False)); fl.addRow(cb); eds[ky]=cb
                fn=QLineEdit(ts.get("filename","")); fn.setPlaceholderText("自定义基建计划"); fl.addRow("计划文件:",fn); eds["filename"]=fn
            elif tk=="Mall":
                for lb,ky in [("信用购物","shopping"),("信用作战","credit_fight"),("访问好友","visit_friends"),("只买折扣","only_buy_discount"),("保留信用","reserve_max_credit")]:
                    cb=QCheckBox(lb); cb.setChecked(ts.get(ky,False)); fl.addRow(cb); eds[ky]=cb
                for lb,ky,ph in [("优先购买:","first_list","招聘许可"),("黑名单:","blacklist","碳;家具")]:
                    le=QLineEdit(ts.get(ky,"")); le.setPlaceholderText(ph); fl.addRow(lb,le); eds[ky]=le
            elif tk=="Award":
                for lb,ky in [("每日奖励","award"),("邮件","mail"),("免费抽卡","free_gacha"),("合成玉","orundum"),("挖矿","mining"),("特殊通道","special_access")]:
                    cb=QCheckBox(lb); cb.setChecked(ts.get(ky,False)); fl.addRow(cb); eds[ky]=cb
            elif tk=="Roguelike":
                th=QComboBox(); themes=[("Sarkaz","萨卡兹"),("Sami","萨米"),("Mizuki","水月"),("Phantom","傀影"),("JieGarden","界园")]
                for tv,tn in themes: th.addItem(tn,tv); th.setCurrentIndex(max(0,[i for i,(tv,tn) in enumerate(themes) if tv==ts.get("theme","Sarkaz")][0]))
                fl.addRow("主题:",th); eds["theme"]=th
                md=QComboBox(); md.addItems(["刷等级","刷源石锭"]); md.setCurrentIndex(ts.get("mode",0)); fl.addRow("模式:",md); eds["mode"]=md
                sd=QSpinBox(); sd.setRange(0,15); sd.setValue(min(ts.get("difficulty",15),15)); fl.addRow("难度:",sd); eds["difficulty"]=sd
                for lb,ky in [("分队:",("squad","")),("职业:",("roles","")),("核心干员:",("core_char",""))]:
                    le=QLineEdit(ts.get(ky[0],ky[1])); fl.addRow(lb,le); eds[ky[0]]=le
                st=QSpinBox(); st.setRange(1,99999); st.setValue(ts.get("start_count",99999)); fl.addRow("次数:",st); eds["start_count"]=st
                for lb,ky,sc in [("投资源石锭","investment","invest_count"),("满级停止","stop_when_level_max",None),("存满停止","stop_when_deposit_full",None),("使用助战","use_support",None),("指定种子","start_with_seed","seed")]:
                    cb=QCheckBox(lb); cb.setChecked(ts.get(ky,False)); fl.addRow(cb); eds[ky]=cb
                    if sc=="invest_count":
                        iv=QSpinBox(); iv.setRange(0,999); iv.setValue(ts.get("invest_count",999)); fl.addRow("投资次数:",iv); eds["invest_count"]=iv
                    elif sc=="seed":
                        se=QLineEdit(ts.get("seed","")); fl.addRow("种子:",se); eds["seed"]=se
            elif tk=="Reclamation":
                for lb,ky,opts in [("主题:",("theme","Tales"),["Tales"]),("模式:",("mode","ProsperityInSave"),["存档内繁荣"])]:
                    mb=QComboBox(); mb.addItems(opts); mb.setCurrentIndex(0); fl.addRow(lb,mb); eds[ky]=mb
                tc=QLineEdit(ts.get("tool_to_craft","")); fl.addRow("制造:",tc); eds["tool_to_craft"]=tc
                mc=QSpinBox(); mc.setRange(0,99); mc.setValue(ts.get("max_craft_count",16)); fl.addRow("制造上限:",mc); eds["max_craft_count"]=mc
                cb=QCheckBox("清理商店"); cb.setChecked(ts.get("clear_store",False)); fl.addRow(cb); eds["clear_store"]=cb
            self._editors[tk]=eds; sw.setWidget(w); tabs.addTab(sw,TASK_NAMES.get(tk,tk))
        l.addWidget(tabs)
        b=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); b.accepted.connect(lambda: (self._save(),self.accept())); b.rejected.connect(self.reject); l.addWidget(b)
    def _save(self):
        for t,eds in self._editors.items():
            ts=self.s.get(t,TASK_DEFAULTS[t].copy())
            def chk(k): v=eds.get(k); return v.isChecked() if hasattr(v,'isChecked') else bool(v)
            if t=="StartUp": ts["client_type"]=eds["client_type"].currentData()
            elif t=="Fight":
                ts["stage"]=eds["stage"].text().strip(); ts["medicine"]=eds["medicine"].value(); ts["times"]=eds["times"].value()
                ts["use_expiring_medicine"]=chk("use_expiring_medicine"); ts["medicine_expire_days"]=eds["medicine_expire_days"].value()
                ts["use_stone"]=chk("use_stone"); ts["stone"]=eds["stone"].value(); ts["enable_times_limit"]=chk("enable_times_limit")
                ts["stage_reset_mode"]=["Current","Ignore"][eds["stage_reset_mode"].currentIndex()]; ts["annihilation_stage"]=eds["annihilation_stage"].text().strip()
                ts["use_custom_annihilation"]=chk("use_custom_annihilation"); ts["hide_unavailable_stage"]=chk("hide_unavailable_stage")
            elif t=="Recruit":
                for ky in ["select","confirm"]:
                    ts[ky]=[lv for lv in [3,4,5] if chk(f"{ky}{lv}")]
                ts["times"]=eds["times"].value()
                for ky in ["refresh","force_refresh","prefer_tag_enabled","preserve_tag_enabled"]: ts[ky]=chk(ky)
                ts["preserve_tags"]=eds["preserve_tags"].text().strip()
                for lv in [3,4,5]: ts[f"level{lv}_time"]=eds[f"level{lv}_time"].value()
            elif t=="Infrast":
                fm={"Trade":"贸易","Mfg":"制造","Control":"控制","Power":"发电","Reception":"会客","Office":"办公","Dorm":"宿舍"}
                ts["facilities"]=[f for f in fm if chk(f"fac_{f}")]
                ts["drones"]=["Money","Orundum","None"][eds["drones"].currentIndex()]; ts["mode"]=["Normal","Rotation","Custom"][eds["mode"].currentIndex()]
                ts["dorm_threshold"]=eds["dorm_threshold"].value()
                for ky in ["dorm_trust_enabled","originium_shard_auto","reception_clue","send_clue","continue_training"]: ts[ky]=chk(ky)
                ts["filename"]=eds["filename"].text().strip()
            elif t=="Mall":
                for ky in ["shopping","credit_fight","visit_friends","only_buy_discount","reserve_max_credit"]: ts[ky]=chk(ky)
                for ky in ["first_list","blacklist"]: ts[ky]=eds[ky].text().strip()
            elif t=="Award":
                for ky in ["award","mail","free_gacha","orundum","mining","special_access"]: ts[ky]=chk(ky)
            elif t=="Roguelike":
                ts["theme"]=eds["theme"].currentData(); ts["mode"]=eds["mode"].currentIndex(); ts["difficulty"]=eds["difficulty"].value()
                for ky in ["squad","roles","core_char"]: ts[ky]=eds[ky].text().strip()
                ts["start_count"]=eds["start_count"].value(); ts["investment"]=chk("investment"); ts["invest_count"]=eds["invest_count"].value()
                for ky in ["stop_when_level_max","stop_when_deposit_full","use_support","start_with_seed"]: ts[ky]=chk(ky)
                ts["seed"]=eds["seed"].text().strip()
            elif t=="Reclamation":
                ts["theme"]=eds["theme"].currentText(); ts["mode"]=eds["mode"].currentText()
                ts["tool_to_craft"]=eds["tool_to_craft"].text().strip(); ts["max_craft_count"]=eds["max_craft_count"].value(); ts["clear_store"]=chk("clear_store")
            self.s[t]=ts

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
        self._build_ui(); self._restore_geometry(); self._rgl(); self._log("══ 启动 ══")
        self._setup_tray(); self._start_schedule()
        self._proc_timer=QTimer(self); self._proc_timer.timeout.connect(self._poll); self._proc_timer.start(2000)
        self._emu_monitor=EmuMonitor(); self._emu_status={}
        self._emu_monitor.updated.connect(lambda r: [self._emu_status.update({x["index"]:x}) for x in r])
        self._emu_monitor.start()
        if self.config.get("check_update_on_start",True): QTimer.singleShot(3000,lambda: self._check_updates(True))

    def _set_theme(self,m): self.setStyleSheet(DARK_STYLE if m=="Dark" else LIGHT_STYLE)
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
                self._log("══ 全部启动完成 ══"); self._notify("全部账号启动完成"); return
            a=self.accounts[idx]; progs=[w for w in self.warehouse if w.get("account_ref")==a["id"]]
            self.sl.setText(f"启动中: {idx+1}/{total}")
            if not progs: self._log(f"跳过: {a['name']} (无绑定)"); QTimer.singleShot(500,lambda: _next(idx+1)); return
            self._la(idx)
            QTimer.singleShot(5000,lambda: _next(idx+1))
        _next()
    def _save(self):
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
        ws.addWidget(QPushButton("检查更新",clicked=lambda: self._check_updates())); wl.addLayout(ws)
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
        tm.addAction("定时",self._sch); tm.addAction("检查更新",lambda: self._check_updates()); tm.addAction("设置",self._settings); tm.addAction("日志",self._tlog)
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
        if w.get("maa_type")!="general": m.addAction("检查更新",lambda: self._cu_single(w))
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
        if hasattr(self,'_sad_row') and self._sad_row==row: return  # same account, skip rebuild
        self._sad_row=row
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
            sw_ver=QPushButton("🔄 切换版本"); sw_ver.clicked.connect(lambda: self._switch_maa_version(progs[0],ch.currentText()))
            vr.addWidget(sw_ver); mcl.addLayout(vr)
            # Stats/log buttons
            btr=QHBoxLayout()
            stats_btn=QPushButton("📊 统计"); stats_btn.clicked.connect(lambda: self._show_maa_stats(progs[0])); btr.addWidget(stats_btn)
            log_btn=QPushButton("📋 日志"); log_btn.clicked.connect(lambda: self._view_maa_log(progs[0])); btr.addWidget(log_btn)
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
        def _refresh_instances(combo=None):
            instances=detect_emu_instances()
            if combo is None: return instances
            saved_idx=a.get("emu_instance_index","")
            saved_name=a.get("emu_instance_name","")
            combo.clear(); combo.addItem(f"— 检测到 {len(instances)} 个实例 —","")
            selected=-1
            for j,ins in enumerate(instances):
                label=ins['name']; running=ins.get("running",False)
                ms=self._emu_status.get(ins.get("index",""),{})
                if ms.get("running"): running=True
                if running: label="▶ "+label
                if ins.get("adb_port"): label+=f" (:{ins['adb_port']})"
                combo.addItem(label,ins)
                if saved_idx and str(ins.get("index",""))==str(saved_idx): selected=j+1
            if saved_name and not saved_idx:
                emu_path_edit.setText(saved_name); emu_path_edit.setToolTip(f"上次: {saved_name}")
            if selected>=0: combo.setCurrentIndex(selected)
        ed_sel=QComboBox(); ed_sel.setMinimumWidth(180)
        _refresh_instances(ed_sel)
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
            bl.addWidget(QPushButton("检查更新",clicked=lambda: self._cu_single(progs[0])))
        else:
            dl=QPushButton("⬇ 下载 MAA"); dl.setObjectName("addProgBtn"); dl.setMinimumHeight(36); dl.clicked.connect(lambda: self._dl_maa(row)); bl.addWidget(dl)
            bl.addWidget(QPushButton("📂 绑定",clicked=lambda: self._pk_maa(row)))
        bl.addStretch(); self.adl.insertWidget(8,bw); self.adl.addStretch()

    def _refresh_instance_list_async(self,combo):
        combo.setEnabled(False)
        class _T(QThread):
            result=Signal(list)
            def run(s): s.result.emit(detect_emu_instances())
        t=_T()
        def _done(instances):
            saved_idx=None; saved_name=None
            combo.clear(); combo.addItem(f"— 检测到 {len(instances)} 个实例 —","")
            for j,ins in enumerate(instances):
                label=ins['name']; running=ins.get("running",False)
                if running: label="▶ "+label
                if ins.get("adb_port"): label+=f" (:{ins['adb_port']})"
                combo.addItem(label,ins)
            combo.setEnabled(True)
        t.result.connect(_done); t.start()

    def _test_adb(self,a):
        ad=a.get("adb_address","")
        if not ad: self._ast.setText("输入地址"); return
        self._ast.setText("测试中...")
        adb=a.get("adb_path","") or "adb"
        class _T(QThread):
            result=Signal(str)
            def run(s):
                try:
                    r=subprocess.run([adb,"connect",ad],capture_output=True,timeout=10,creationflags=CF)
                    out=(r.stdout+r.stderr).decode('utf-8','replace').strip()
                    s.result.emit("✅ 成功" if "connected" in out.lower() or "already" in out.lower() else f"⚠ {out[:80]}")
                except Exception as e: s.result.emit(f"❌ {e}")
        t=_T(); t.result.connect(lambda r: self._ast.setText(r)); t.start()
    def _browse_adb(self,le,ac):
        f,_=QFileDialog.getOpenFileName(self,"选择 ADB","","adb.exe (adb.exe);;所有文件 (*.*)")
        if f: le.setText(str(Path(f))); ac["adb_path"]=str(Path(f)); self._save()
    def _browse_file(self,le,ac,key):
        f,_=QFileDialog.getOpenFileName(self,"选择文件","","可执行文件 (*.exe);;所有文件 (*.*)")
        if f: le.setText(str(Path(f))); ac[key]=str(Path(f)); self._save()
    def _adb_screenshot(self,a):
        addr=a.get("adb_address",""); adb=a.get("adb_path","") or "adb"
        if not addr: return
        self._log(f"截图: {addr}...")
        class _T(QThread):
            result=Signal(str)
            def run(s):
                try:
                    r=subprocess.run([adb,"-s",addr,"exec-out","screencap","-p"],capture_output=True,timeout=10,creationflags=CF)
                    if r.returncode==0 and r.stdout:
                        ss_dir=Path(__file__).parent/"screenshots"; ss_dir.mkdir(exist_ok=True)
                        fn=ss_dir/f"MAA_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        fn.write_bytes(r.stdout); s.result.emit(f"ok|{fn.name}")
                    else: s.result.emit("fail|")
                except Exception as e: s.result.emit(f"err|{e}")
        def _on(r):
            if r.startswith("ok|"): self._log(f"截图: {r[3:]}")
            elif r.startswith("fail|"): self._log("截图失败")
            elif r.startswith("err|"): self._log(f"截图失败: {r[4:]}")
        t=_T(); t.result.connect(_on); t.start()
    def _stop_emu(self,a):
        emu_idx=a.get("emu_instance_index","")
        if not emu_idx: return
        cli=find_mumu_cli()
        if cli:
            self._log(f"关闭模拟器 #{emu_idx}...")
            class _T(QThread):
                result=Signal(str)
                def run(s):
                    try: subprocess.run([cli,"control","--vmindex",str(emu_idx),"shutdown"],creationflags=CF,timeout=15); s.result.emit("ok")
                    except Exception as e: s.result.emit(str(e))
            def _on(r):
                if r=="ok": self._log("模拟器已关闭")
                else: self._log(f"关闭失败: {r}")
            t=_T(); t.result.connect(_on); t.start()
    def _scan_port(self,a,path_edit,addr_edit):
        """Start emulator, wait, then scan ADB port"""
        emu_idx=a.get("emu_instance_index","")
        if not emu_idx: QMessageBox.information(self,"提示","请先选择模拟器实例"); return
        cli=find_mumu_cli()
        if not cli: QMessageBox.warning(self,"提示","未找到 mumu-cli"); return
        self._log(f"扫描端口: 实例 #{emu_idx}")
        self.sl.setText("启动模拟器...")
        adb=a.get("adb_path","") or "adb"
        class _T(QThread):
            result=Signal(str)
            def __init__(s,emu_idx,cli_path,adb_path):
                super().__init__(); s.emu_idx=emu_idx; s.cli=cli_path; s.adb=adb_path
            def run(s):
                # Step 1: launch emulator
                try:
                    subprocess.run([s.cli,"control","--vmindex",str(s.emu_idx),"launch"],creationflags=CF,timeout=15)
                except Exception as e:
                    s.result.emit(f"__err__启动失败: {e}"); return
                # Step 2: wait for boot (background sleep)
                s.result.emit("启动完成，等待端口..."); s.msleep(3000)
                # Step 3: get actual port from instance data
                target_port=None
                try:
                    instances=detect_emu_instances()
                    for ins in instances:
                        if str(ins.get("index",""))==str(s.emu_idx) and ins.get("adb_port"):
                            target_port=ins["adb_port"]; break
                except: pass
                # Step 4: wait for port
                if target_port:
                    addr=f"127.0.0.1:{target_port}"
                    for remaining in range(40,0,-1):
                        s.result.emit(f"等待端口 {target_port} ({remaining})...")
                        try:
                            r=subprocess.run([s.adb,"-s",addr,"shell","echo","ok"],capture_output=True,timeout=3,creationflags=CF)
                            if r.returncode==0 and b"ok" in r.stdout:
                                s.result.emit("__found__"+addr); return
                        except: pass
                        s.msleep(2000)
                else:
                    for remaining in range(20,0,-1):
                        s.result.emit(f"扫描端口 ({remaining})...")
                        for p in ["7555","5555","62001","21503"]:
                            try: subprocess.run([s.adb,"connect",f"127.0.0.1:{p}"],capture_output=True,timeout=1,creationflags=CF)
                            except: pass
                        r=subprocess.run([s.adb,"devices"],capture_output=True,timeout=5,creationflags=CF)
                        for m in re.finditer(rb':(\d+)\s+device\b',r.stdout):
                            addr="127.0.0.1:"+m.group(1).decode('ascii')
                            s.result.emit("__found__"+addr); return
                        s.msleep(2000)
                s.result.emit("__timeout__")
        if hasattr(self,'_t') and self._t.isRunning():
            self._t.result.disconnect(); self._t.quit(); self._t.wait(1000)
        self._t=_T(emu_idx,cli,adb)
        def _on_r(r):
            if r.startswith("__found__"):
                addr=r[9:]; addr_edit.setText(addr); a.__setitem__("adb_address",addr); self._save()
                self._log(f"端口: {addr}"); self._sl(f"端口: {addr}")
            elif r.startswith("__err__"):
                self._log(r[8:]); self.sl.setText("就绪")
            elif r=="__timeout__":
                self.sl.setText("扫描超时"); self._log("扫描端口超时")
            else: self.sl.setText(r)
        self._t.result.connect(_on_r)
        self._t.start()
    def _maa_asst_log(self,w):
        return Path(w.get("path","")).parent/"debug"/"asst.log"
    def _switch_maa_version(self,w,channel):
        """Download and switch to latest version of specified channel"""
        if QMessageBox.question(self,"切换版本",f"将下载最新 {channel} 版 MAA\n并替换当前版本\n\n是否继续？")!=QMessageBox.Yes: return
        self._log(f"切换 MAA 版本: {channel}")
        self.sl.setText(f"下载 {channel} 版...")
        def _on_result(r):
            if not r.get("ok"): QMessageBox.critical(self,"失败",r.get("error","")); self.sl.setText("就绪"); return
            tag=r["tag"]; info=r["assets"].get(get_platform_key())
            if not info: QMessageBox.warning(self,"失败","无可用包"); self.sl.setText("就绪"); return
            dlg=UpdateDialog(self,tag,info,str(Path(w["path"]).parent))
            if dlg.exec()==QDialog.Accepted:
                w["maa_version"]=tag; w["update_channel"]=channel; self._save()
                self._log(f"MAA 已切换至: {tag}")
                # Regenerate config injection
                ac=next((a for a in self.accounts if a["id"]==w.get("account_ref","")),None)
                if ac: self._inj(w,ac)
            self.sl.setText("就绪")
        t=UpdateCheckThread(); t.result_ready.connect(_on_result); self.update_thread=t; t.start()
    def _parse_maa_log(self,w,tail=500):
        lp=self._maa_asst_log(w)
        if not lp.exists(): return []
        try:
            lines=lp.read_text(encoding="utf-8",errors="replace").strip().split("\n")[-tail:]
        except: return []
        tasks=[]; cur_task=None
        task_map={"StartUp":"开始唤醒","Fight":"刷关作战","Recruit":"公开招募","Infrast":"基建换班","Mall":"信用商店","Award":"领取奖励","Roguelike":"肉鸽探索","Reclamation":"生息演算","CloseDown":"关闭游戏"}
        for line in lines:
            m=re.match(r'\[([^\]]+)\].*',line)
            ts=m.group(1) if m else ""
            if "append_task" in line:
                for k,v in task_map.items():
                    if k in line:
                        cur_task={"name":v,"start":ts,"status":"运行中","drops":"","error":""}; tasks.append(cur_task); break
            elif "[ERR]" in line and cur_task:
                cur_task["status"]="失败"; cur_task["error"]=line.split("[ERR]")[-1].strip()[:100]
            elif "TaskSwitched" in line and cur_task:
                cur_task["status"]="完成"
            elif "StageDrops" in line and cur_task:
                drops=re.findall(r'\b(\S+?)\s*[xX×]\s*(\d+)',line)
                if drops: cur_task["drops"]=",".join(f"{d[0]}x{d[1]}" for d in drops[-5:])
        return tasks
    def _show_maa_stats(self,w):
        tasks=self._parse_maa_log(w)
        if not tasks:
            QMessageBox.information(self,"统计","暂无运行数据\n等待 MAA 执行任务后自动生成")
            return
        d=QDialog(self); d.setWindowTitle("MAA 运行统计"); d.setMinimumSize(400,300)
        l=QVBoxLayout(d); l.addWidget(QLabel(f"📊 MAA 运行统计 ({len(tasks)} 个任务)",font=QFont("Microsoft YaHei UI",13,QFont.Bold)))
        tw=QTableWidget(); tw.setColumnCount(3); tw.setHorizontalHeaderLabels(["任务","状态","详情"])
        tw.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch); tw.setColumnWidth(1,60); tw.setColumnWidth(2,200)
        tw.setRowCount(len(tasks))
        for i,t in enumerate(tasks):
            tw.setItem(i,0,QTableWidgetItem(t.get("name","?")))
            st=t.get("status",""); si=QTableWidgetItem(st)
            if "失败" in st: si.setForeground(QColor("#a88"))
            elif "完成" in st: si.setForeground(QColor("#8a8"))
            tw.setItem(i,1,si)
            detail=t.get("drops","") or t.get("error","")
            tw.setItem(i,2,QTableWidgetItem(detail))
        tw.verticalHeader().setVisible(False)
        l.addWidget(tw); l.addWidget(QPushButton("关闭",clicked=d.accept)); d.exec()
    def _view_maa_log(self,w):
        lp=self._maa_asst_log(w)
        if not lp.exists(): QMessageBox.information(self,"日志","暂无日志文件"); return
        try: content=lp.read_text(encoding="utf-8",errors="replace")
        except: QMessageBox.information(self,"日志","无法读取日志"); return
        d=QDialog(self); d.setWindowTitle("MAA 日志"); d.setMinimumSize(700,500)
        l=QVBoxLayout(d); te=QPlainTextEdit(); te.setReadOnly(True); te.setPlainText("\n".join(content.split("\n")[-200:]))
        # Scroll to bottom
        te.moveCursor(te.textCursor().End)
        l.addWidget(te); l.addWidget(QPushButton("关闭",clicked=d.accept)); d.exec()
    def _scan(self,a,cb):
        cb.clear(); cb.addItem("扫描中...",""); cb.setEnabled(False)
        adb=a.get("adb_path","") or "adb"
        class _T(QThread):
            result=Signal(list)
            def run(s):
                results=[]
                try:
                    r=subprocess.run([adb,"devices"],capture_output=True,timeout=5,creationflags=CF)
                    for m in re.finditer(rb':(\d+)\s+(\S+)',r.stdout):
                        addr="127.0.0.1:"+m.group(1).decode('ascii')
                        st=m.group(2).decode('ascii','replace')
                        if st in ("device","unauthorized","offline"):
                            results.append((addr,st=="device"))
                    # If nothing online, probe candidate ports and re-scan
                    if not any(ok for _,ok in results):
                        for ep in EMU_PRESETS:
                            for p in ep["ports"]:
                                try: subprocess.run([adb,"connect",f"127.0.0.1:{p}"],capture_output=True,timeout=0.3,creationflags=CF)
                                except: pass
                        r=subprocess.run([adb,"devices"],capture_output=True,timeout=5,creationflags=CF)
                        for m in re.finditer(rb':(\d+)\s+(\S+)',r.stdout):
                            addr="127.0.0.1:"+m.group(1).decode('ascii')
                            st=m.group(2).decode('ascii','replace')
                            if st in ("device","unauthorized","offline"):
                                results.append((addr,st=="device"))
                except Exception as e:
                    results.append(("__err__",str(e)))
                s.result.emit(results)
        if hasattr(self,'_scan_thread') and self._scan_thread.isRunning():
            self._scan_thread.result.disconnect(); self._scan_thread.quit(); self._scan_thread.wait(1000)
        self._scan_thread=_T()
        def _on_results(results):
            cb.clear(); cb.addItem("— 在线设备 —","")
            if not results: cb.addItem("未发现在线设备","")
            else:
                for addr,ok in results:
                    if addr=="__err__":
                        self._log(f"扫描出错: {ok}")
                        cb.addItem(f"扫描出错: {ok}","")
                        continue
                    cb.addItem(f"{addr} {'✅' if ok else '⚠'}",addr)
                cb.setCurrentIndex(1)
            cb.setEnabled(True)
        self._scan_thread.result.connect(_on_results)
        self._scan_thread.start()
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
                    except: pass
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
                except: pass
            for w in progs:
                try: self._inj(w,a); self._ls(w)
                except Exception as e: self._log(f"失败: {e}"); QMessageBox.critical(self,"失败",str(e))
    def _dl_maa(self,row):
        a=self.accounts[row]
        def oc(r):
            if not r.get("ok"): return
            tag=r["tag"]; info=r["assets"].get(get_platform_key())
            if not info: return
            d=Path(__file__).parent/"accounts"/a["id"]/"MAA"; d.mkdir(parents=True,exist_ok=True)
            dlg=UpdateDialog(self,tag,info,str(d))
            if dlg.exec()!=QDialog.Accepted: return
            exe=None
            for p in d.rglob("MAA.exe"): exe=p; break
            if not exe: return
            e={"id":make_id(),"path":str(exe),"args":[],"cwd":"","env":{},"maa_type":"maa","maa_version":tag,"account_ref":a["id"],"launch_mode":"gui","task_pipeline":"startup,fight,recruit,infrast,mall,award","guard_enabled":True,"guard_max_restart":3,"guard_capture_log":False}
            self.warehouse.append(e); self._save(); self._sad(row); self._inj(e,a); self._ls(e)
        t=UpdateCheckThread(); t.result_ready.connect(oc); self.update_thread=t; t.start()
    def _pk_maa(self,row):
        a=self.accounts[row]; f,_=QFileDialog.getOpenFileName(self,"选择","","MAA (*.exe);;所有文件 (*.*)")
        if not f: return
        p=str(Path(f)); e={"id":make_id(),"path":p,"args":[],"cwd":"","env":{},"maa_type":"maa","maa_version":parse_maa_version(p) or "","account_ref":a["id"],"launch_mode":"gui","task_pipeline":"startup,fight,recruit,infrast,mall,award","guard_enabled":False,"guard_max_restart":3,"guard_capture_log":False}
        self.warehouse.append(e); self._save(); self._sad(row); self._inj(e,a); self._ls(e)

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
    def _gtc(self,ac,w):
        pl=w.get("task_pipeline","")
        if not pl: return None
        ts=[t.strip() for t in pl.split(",") if t.strip()]
        if not ts: return None
        md=Path(w["path"]).parent; td=md/"config"/"tasks"; td.mkdir(parents=True,exist_ok=True); ls=[]
        for t in ts:
            tl=t.lower()
            if tl=="startup": ls.extend(["[[tasks]]",'type="StartUp"',"[tasks.params]",f'client_type="{ac.get("game_client","Official")}"',"start_game_enabled=true"])
            elif tl=="fight": s=ac.get("fight_stage",""); ls.extend(["[[tasks]]",'type="Fight"'])
            if s: ls.extend(["[tasks.params]",f'stage="{s}"'])
            elif tl=="recruit": ls.extend(["[[tasks]]",'type="Recruit"',"[tasks.params]","refresh=true","select=[3,4,5]","confirm=[3,4,5]","times=4"])
            elif tl=="infrast": ls.extend(["[[tasks]]",'type="Infrast"',"[tasks.params]","mode=0",'facility=["Trade","Reception","Mfg","Control","Power","Office","Dorm"]',"dorm_trust_enabled=true"])
            elif tl=="mall": ls.extend(["[[tasks]]",'type="Mall"',"[tasks.params]","shopping=true"])
            elif tl=="award": ls.extend(["[[tasks]]",'type="Award"'])
            elif tl=="roguelike": ls.extend(["[[tasks]]",'type="Roguelike"',"[tasks.params]",'theme="Sarkaz"',"mode=0"])
            elif tl=="reclamation": ls.extend(["[[tasks]]",'type="Reclamation"',"[tasks.params]",'theme="Tales"'])
            elif tl=="closedown": ls.extend(["[[tasks]]",'type="CloseDown"'])
            ls.append("")
        (td/"daily.toml").write_text("\n".join(ls),encoding="utf-8")
        pd=md/"config"/"profiles"; pd.mkdir(parents=True,exist_ok=True); pls=["[connection]"]
        if ac.get("adb_address"): pls.append(f'address="{ac["adb_address"]}"')
        if ac.get("adb_path"): pls.append(f'adb_path="{ac["adb_path"].replace(chr(92),chr(92)+chr(92))}"')
        if ac.get("connection_preset"): pls.append(f'preset="{ac["connection_preset"]}"')
        pls.extend(["[instance_options]",f'touch_mode="{ac.get("touch_mode","ADB")}"']); (pd/"default.toml").write_text("\n".join(pls)+"\n",encoding="utf-8")
        return "daily"
    def _inj(self,w,ac):
        p=w.get("path",""); md=Path(p).parent if p else None
        if not md or not md.exists(): return
        cd=md/"config"; cd.mkdir(parents=True,exist_ok=True); pl=w.get("task_pipeline",""); ptasks=[t.strip().lower() for t in pl.split(",") if t.strip()] if pl else []
        def _wcfg(fn):
            gj=cd/fn; d={}
            if gj.exists():
                try: d=json.loads(gj.read_text(encoding="utf-8"))
                except: d={}
            d.setdefault("Configurations",{}).setdefault("Default",{}); d.setdefault("Current","Default"); d.setdefault("Global",{}); c=d["Configurations"]["Default"]
            if ac.get("adb_address"): c["Connect.Address"]=ac["adb_address"]
            if ac.get("adb_path"): c["Connect.AdbPath"]=ac["adb_path"]
            pr=ac.get("connection_preset",""); to=ac.get("touch_mode","")
            if pr: c["Connect.ConnectConfig"]={"MuMuPro":"MuMuEmulator12"}.get(pr,pr)
            if to: c["Connect.TouchMode"]={"MiniTouch":"minitouch","MaaTouch":"maatouch","ADB":"adb"}.get(to,"adb")
            c["Connect.AdbReplaced"]="True"; c["Connect.AutoDetect"]="False"; c["Connect.AlwaysAutoDetect"]="False"
            if ac.get("game_client"): c["Start.ClientType"]=ac["game_client"]
            sw=ac.get("account_switch","")
            if sw: c["Start.RunDirectly"]="False"; c["Start.StartGame"]="True"
            else: c["Start.RunDirectly"]="True"; c["Start.StartGame"]="True"
            # Start options
            if ac.get("start_minimized"): d.setdefault("Global",{})["GUI.MinimizeToTray"]="True"
            if ac.get("start_directly"): c["Start.RunDirectly"]="True"
            if ac.get("post_action"): c["MainFunction.PostActions"]='"'+ac["post_action"]+'"'
            if ac.get("adb_retry",0)>0: c["Connect.RetryOnDisconnected"]="True"
            # Emulator: unchecked = MAA handles, checked = we handle
            if ac.get("emu_instance_index","") and not ac.get("emu_launch"):
                cli=find_mumu_cli()
                if cli:
                    c["Start.EmulatorPath"]=str(cli)
                    c["Start.EmulatorAddCommand"]=f'control --vmindex {ac["emu_instance_index"]} launch'
                    c["Start.OpenEmulatorAfterLaunch"]="True"
                    if ac.get("emu_wait"): c["Start.EmulatorWaitSeconds"]=str(ac["emu_wait"])
            # Account switch in TaskQueue
            sw=ac.get("account_switch","")
            if sw and "TaskQueue" in c:
                for item in c["TaskQueue"]:
                    if item.get("TaskType","").lower()=="startup": item["AccountName"]=sw; break
            if ac.get("sync_tasks",False):
                ts=ac.get("task_settings",{})
                if ptasks and "TaskQueue" in c:
                    tq=c["TaskQueue"]
                    for item in tq:
                        tt=item.get("TaskType","").lower()
                        if tt in ptasks:
                            item["IsEnable"]=True
                            if tt in ts:
                                st=ts[tt]
                                if tt=="fight":
                                    if st.get("stage"): item["StagePlan"]=[st["stage"]]
                                    if "medicine" in st: item["UseMedicine"]=st["medicine"]>0; item["MedicineCount"]=st["medicine"]
                                elif tt=="recruit":
                                    if "select" in st: item["Level3Choose"]=3 in st["select"]; item["Level4Choose"]=4 in st["select"]; item["Level5Choose"]=5 in st["select"]
                                    if "confirm" in st: item["Confirm"]=st["confirm"]
                                    if "times" in st: item["MaxTimes"]=st["times"]
                                elif tt=="infrast":
                                    if "facilities" in st: item["RoomList"]=[{"Room":f} for f in st["facilities"]]
                                    if "drones" in st: item["UsesOfDrones"]=st["drones"]
                                elif tt=="mall":
                                    if "shopping" in st: item["Shopping"]=st["shopping"]
                                    if "blacklist" in st: item["BlackList"]=st["blacklist"]
                                elif tt=="award":
                                    if "award" in st: item["Award"]=st["award"]
                                    if "mail" in st: item["Mail"]=st["mail"]
                                elif tt=="roguelike":
                                    if "theme" in st: item["Theme"]=st["theme"]
                                    if "mode" in st: item["Mode"]="Exp" if st["mode"]==0 else "Investment"
                                elif tt=="reclamation":
                                    if "theme" in st: item["Theme"]=st["theme"]
                        else: item["IsEnable"]=False
                    c["TaskQueue"]=tq
            gj.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
        _wcfg("gui.json"); _wcfg("gui.new.json")

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

    class PipelineThread(QThread):
        progress=Signal(str); program_started=Signal(str,bool); finished=Signal(bool)
        def __init__(self,groups,warehouse,accounts,parent=None):
            super().__init__(); self.groups=groups; self.warehouse={w["id"]:w for w in warehouse}
            self.accounts={a["id"]:a for a in accounts}; self.stop_flag=False; self.pause_flag=False; self.mw=parent
        def run(self):
            for g in self.groups:
                if self.stop_flag: break
                while self.pause_flag and not self.stop_flag: self.msleep(200)
                if self.stop_flag: break
                refs=g.get("programs",[])
                if not refs: continue
                self.progress.emit(f"执行: {g.get('name','')}")
                if g.get("mode")=="parallel":
                    for ref in refs:
                        if self.stop_flag: break; self._l(ref)
                else:
                    for ref in refs:
                        if self.stop_flag: break; self._sleep(ref.get("pre_delay",0))
                        if self.stop_flag: break; self._l(ref)
                self._sleep(g.get("post_delay",3))
            self.finished.emit(self.stop_flag)
        def _l(self,ref):
            w=self.warehouse.get(ref.get("ref",""),{}); p=w.get("path",""); n=Path(p).stem
            try:
                ac=w.get("account_ref","")
                if ac and ac in self.accounts and self.mw:
                    try: self.mw._inj(w,self.accounts[ac])
                    except: pass
                subprocess.Popen([p]+w.get("args",[]),shell=False,cwd=w.get("cwd","") or None); self.program_started.emit(n,True)
            except Exception as e: self.program_started.emit(f"{n}:{e}",False)
        def _sleep(self,s):
            for _ in range(int(s*10)):
                if self.stop_flag: break
                while self.pause_flag and not self.stop_flag: self.msleep(200)
                time.sleep(0.1)
        def stop(self): self.stop_flag=True
        def pause(self): self.pause_flag=True
        def resume(self): self.pause_flag=False

    def _poll(self):
        now=time.time()
        for pid in list(self._cli_procs.keys()):
            p=self._cli_procs[pid]
            if p.poll() is not None:
                out=p.stdout.read().decode(errors="replace").strip(); err=p.stderr.read().decode(errors="replace").strip()
                if out: self._log(f"[maa-cli] {out[:500]}")
                if err: self._log(f"[maa-cli] {err[:500]}")
                rc=p.poll(); self._log(f"[maa-cli] 退出码: {rc}"); self._cli_procs.pop(pid,None); self._proc_status.discard(pid)
                self._notify(f"MAA CLI 已退出" if rc==0 else f"MAA CLI 异常退出",rc!=0)
        for pid in list(self._running_procs.keys()):
            p=self._running_procs[pid]
            if p.poll() is not None:
                self._running_procs.pop(pid,None); self._proc_status.discard(pid); self._proc_start_times.pop(pid,None)
                rc=p.poll()
                if rc!=0:
                    self._notify(f"进程异常退出 (code={rc})",True)
                    w=next((x for x in self.warehouse if x["id"]==pid),None)
                    if w and w.get("guard_enabled") and QMessageBox.question(self,"进程退出",f"{Path(w['path']).stem} 异常退出\n是否重启？")==QMessageBox.Yes:
                        self._ls(w)
                # Check MAA log for completion
                w=next((x for x in self.warehouse if x["id"]==pid),None)
                if w:
                    tasks=self._parse_maa_log(w)
                    errs=[t for t in tasks if t.get("status")=="失败"]
                    if errs: self._notify(f"MAA 任务失败: {errs[0].get('name')}",True)
                    elif tasks: self._notify(f"MAA 完成: {len(tasks)} 个任务")
        # Update status label with runtime
        running=[pid for pid in self._proc_status if pid in self._proc_start_times]
        if running:
            elapsed=int(now-self._proc_start_times[running[0]])
            self.sl.setText(f"运行中 ({elapsed//60}m{elapsed%60}s)")
        # Read current task from MAA log
        for wid in list(self._running_procs.keys()):
            w=next((x for x in self.warehouse if x["id"]==wid),None)
            lp=self._maa_asst_log(w) if w else None
            if lp and lp.exists():
                try:
                    last=lp.read_text(encoding="utf-8",errors="replace").strip().split("\n")[-3:]
                    for l in last:
                        if "append_task" in l:
                            for k,v in {"StartUp":"唤醒","Fight":"刷关","Recruit":"公招","Infrast":"基建","Mall":"信用","Award":"奖励","Roguelike":"肉鸽","Reclamation":"生息"}.items():
                                if k in l: self.sl.setText(f"MAA: {v}..."); break
                        elif "[ERR]" in l:
                            err=l.split("[ERR]")[-1].strip()[:80]
                            self._log(f"MAA错误: {err}")
                            self._notify(f"MAA: {err}",True)
                        elif "TaskSwitched" in l:
                            self.sl.setText("MAA: 切换任务...")
                except: pass
    def _notify(self,msg,is_error=False):
        if hasattr(self,'tray_icon'):
            self.tray_icon.showMessage("流水线启动器",msg,QSystemTrayIcon.Critical if is_error else QSystemTrayIcon.Information,3000)
        # Webhook
        wh=self.config.get("webhook_url","")
        if wh:
            try:
                data=json.dumps({"msg":msg,"type":"error" if is_error else "info","time":datetime.now().isoformat()}).encode()
                req=urllib.request.Request(wh,data=data,headers={"Content-Type":"application/json"},method="POST")
                urllib.request.urlopen(req,timeout=5)
            except Exception as e:
                try: self._log(f"Webhook 失败: {e}")
                except: pass

    def _check_updates(self,silent=False):
        items=[w for w in self.warehouse if w.get("maa_type")!="general"]
        if not items:
            if not silent: QMessageBox.information(self,"提示","无 MAA 程序"); return
        def oc(r):
            if not r.get("ok"):
                if not silent: QMessageBox.warning(self,"失败",r.get("error","")); return
            tag=r["tag"]; info=r["assets"].get(get_platform_key())
            if not info: return
            ups=[(w,Path(w["path"]).parent) for w in items if _version_tuple(w.get("maa_version",""))<_version_tuple(tag)]
            if not ups:
                if not silent: QMessageBox.information(self,"提示",f"已是最新 {tag}"); return
            if silent: self._log(f"MAA {tag} 可用"); return
            if QMessageBox.question(self,"更新",f"更新 {len(ups)} 个?")==QMessageBox.Yes:
                for w,d in ups:
                    dlg=UpdateDialog(self,tag,info,str(d))
                    if dlg.exec()==QDialog.Accepted: w["maa_version"]=tag
                self._save()
        t=UpdateCheckThread(); t.result_ready.connect(oc); self.update_thread=t; t.start()
    def _cu_single(self,w):
        if w.get("maa_type")=="general": return
        def oc(r):
            if not r.get("ok"): return
            tag=r["tag"]; info=r["assets"].get(get_platform_key())
            if not info: return
            dlg=UpdateDialog(self,tag,info,str(Path(w["path"]).parent))
            if dlg.exec()==QDialog.Accepted: w["maa_version"]=tag; self._save()
        t=UpdateCheckThread(); t.result_ready.connect(oc); self.update_thread=t; t.start()
    def _restore_geometry(self):
        g=self.config.get("window_geometry","")
        if g:
            p=g.split("+")
            try:
                if len(p)==3:
                    wh=p[0].split("x"); w,h,x,y=int(wh[0]),int(wh[1]),int(p[1]),int(p[2])
                    screen=QApplication.primaryScreen().availableGeometry()
                    x=max(0,min(x,screen.width()-100)); y=max(0,min(y,screen.height()-100))
                    w=min(w,screen.width()); h=min(h,screen.height())
                    self.setGeometry(x,y,w,h)
                else: wh=p[0].split("x"); self.resize(int(wh[0]),int(wh[1]))
            except: self.resize(960,650)
        else: self.resize(960,650)
    def _setup_tray(self):
        self.tray_icon=QSystemTrayIcon(self); self.tray_icon.setToolTip("流水线启动器")
        pm=QPixmap(64,64); pm.fill(Qt.transparent); p=QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(58,126,191))); p.setPen(Qt.NoPen); p.drawEllipse(4,4,56,56); p.setBrush(QBrush(Qt.white))
        tri=QPolygonF([QPointF(24,18),QPointF(24,46),QPointF(46,32)]); p.drawPolygon(tri); p.end(); ic=QIcon(pm)
        self.setWindowIcon(ic); self.tray_icon.setIcon(ic); m=QMenu(); m.addAction("显示",self._show_tray); m.addAction("退出",QApplication.quit)
        self.tray_icon.setContextMenu(m); self.tray_icon.show()
    def _show_tray(self): self.show(); self._restore_geometry(); self.activateWindow()
    def closeEvent(self,e):
        if not self.isMinimized():
            self.config["window_geometry"]=f"{self.width()}x{self.height()}+{self.x()}+{self.y()}"
            self._save()
        if self.config.get("minimize_to_tray",True) and hasattr(self,'tray_icon'): self.hide(); e.ignore()
        else:
            if hasattr(self,'_emu_monitor'): self._emu_monitor.quit(); self._emu_monitor.wait(2000)
            if hasattr(self,'schedule_thread'): self.schedule_thread.quit(); self.schedule_thread.wait(2000)
            e.accept(); QApplication.quit()
    def _tlog(self): self._log_expanded=not self._log_expanded; self.log_text.setFixedHeight(150 if self._log_expanded else 0)
    def _start_schedule(self):
        if self.config.get("schedule",{}).get("enabled"):
            self.schedule_thread=ScheduleThread(self.config); self.schedule_thread.trigger.connect(self._start_pipeline); self.schedule_thread.start()
    def _sch(self):
        d=ScheduleDialog(self,self.config.get("schedule",{}))
        if d.exec()==QDialog.Accepted: self.config["schedule"]=d.r; self._save()
        if self.schedule_thread: self.schedule_thread.stop_thread()
        if d.r.get("enabled"): self.schedule_thread=ScheduleThread(self.config); self.schedule_thread.trigger.connect(self._start_pipeline); self.schedule_thread.start()
    def _settings(self):
        d=SettingsDialog(self,self.config)
        if d.exec()==QDialog.Accepted: self._set_theme(self.config.get("appearance_mode","Dark")); self._save()

class ScheduleThread(QThread):
    trigger=Signal()
    def __init__(self,c): super().__init__(); self.c=c; self._r=True
    def run(self):
        while self._r:
            s=self.c.get("schedule",{})
            if s.get("enabled"):
                n=datetime.now()
                try: tg=dtime.fromisoformat(s.get("time","08:00"))
                except: tg=dtime(8,0)
                if n.hour==tg.hour and n.minute==tg.minute and n.second<30:
                    if s.get("type")=="daily" or (s.get("type")=="weekly" and n.weekday() in s.get("days_of_week",[])): self.trigger.emit(); time.sleep(61)
            time.sleep(15)
    def stop_thread(self): self._r=False

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
