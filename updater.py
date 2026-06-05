from __future__ import annotations
import json,io,os,shutil,time,zipfile,tempfile
import urllib.request
from pathlib import Path
from typing import Any
from PySide6.QtCore import Qt,QThread,Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QMessageBox,QProgressBar)
from utils import get_platform_key,_rmtree_force

class UpdateCheckThread(QThread):
    result_ready = Signal(dict)

    def run(self) -> None:
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
    progress = Signal(int, int)
    status = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, url: str, target: str, name: str) -> None:
        super().__init__(); self.u=url; self.t=target; self.n=name; self.c=False
    def cancel(self) -> None: self.c=True
    def run(self) -> None:
        tmp=None; tmpf=None
        try:
            self.status.emit("下载中...")
            req=urllib.request.Request(self.u,headers={"User-Agent":"MAA-Launcher"})
            with urllib.request.urlopen(req,timeout=600) as r:
                total=r.length or 0; dl=0
                tmpf=tempfile.NamedTemporaryFile(delete=False,suffix=".zip",prefix="maa_")
                while True:
                    if self.c: self.finished.emit(False,"取消"); return
                    chunk=r.read(65536)
                    if not chunk: break
                    tmpf.write(chunk); dl+=len(chunk)
                    if total: self.progress.emit(dl,total)
                tmpf.close()
            self.progress.emit(1,1); self.status.emit("解压...")
            tmp=tempfile.mkdtemp(prefix="maa_")
            with zipfile.ZipFile(tmpf.name) as zf: zf.extractall(tmp)
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
            if tmpf and Path(tmpf.name).exists():
                try: os.unlink(tmpf.name)
                except: pass

class MaacliInstallThread(QThread):
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, d: str) -> None:
        super().__init__(); self.d=d

    def run(self) -> None:
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
    def __init__(self, p: QDialog) -> None:
        super().__init__(p); self.setWindowTitle("安装 maa-cli"); self.setFixedSize(380,120)
        l=QVBoxLayout(self); l.addWidget(QLabel("正在安装 maa-cli..."))
        self.s=QLabel("准备中..."); l.addWidget(self.s)
        self.b=QProgressBar(); self.b.setRange(0,0); l.addWidget(self.b)
    def start(self, d: str) -> None:
        self.t=MaacliInstallThread(d); self.t.progress.connect(self.s.setText)
        self.t.finished.connect(lambda ok,msg: self.accept() if ok else (QMessageBox.critical(self,"失败",msg),self.reject()))
        self.t.start()

class UpdateDialog(QDialog):
    def __init__(self, p: QDialog, ver: str, info: dict, tgt: str) -> None:
        super().__init__(p); self.setWindowTitle("MAA 更新"); self.setFixedSize(420,200); self.i=info; self.t=tgt
        l=QVBoxLayout(self)
        l.addWidget(QLabel(f"版本: {ver}",font=QFont("Microsoft YaHei UI",13,QFont.Bold)))
        l.addWidget(QLabel(f"大小: {info['size']/1024/1024:.1f} MB"))
        self.b=QProgressBar(); self.b.setVisible(False); l.addWidget(self.b)
        self.s=QLabel(""); l.addWidget(self.s)
        bl=QHBoxLayout(); self.d=QPushButton("下载"); self.d.clicked.connect(self._dl); bl.addWidget(self.d)
        bl.addWidget(QPushButton("取消",clicked=self.reject)); l.addLayout(bl)
    def _dl(self) -> None:
        self.d.setEnabled(False); self.b.setVisible(True)
        self.t=DownloadThread(self.i["url"],self.t,self.i["name"])
        self.t.progress.connect(lambda c,t:(self.b.setMaximum(t),self.b.setValue(c)))
        self.t.status.connect(self.s.setText)
        self.t.finished.connect(lambda ok,msg: self.accept() if ok else (QMessageBox.critical(self,"失败",msg),self.d.setEnabled(True)))
        self.t.start()

