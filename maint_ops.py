import time,json,urllib.request
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt,QTimer,QPointF
from PySide6.QtGui import QPixmap,QPainter,QColor,QBrush,QPolygonF,QIcon
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QMessageBox,QApplication,QSystemTrayIcon,QMenu,QFileDialog
from utils import parse_maa_version, get_platform_key, _version_tuple, make_id
from dialogs import ScheduleDialog, SettingsDialog
from updater import UpdateCheckThread, UpdateDialog
from schedule_thread import ScheduleThread

class MaintService:
    def __init__(self, mw): self.mw = mw

    def dl_maa(self,row):
        a=self.mw.accounts[row]
        def oc(r):
            if not r.get("ok"): return
            tag=r["tag"]; info=r["assets"].get(get_platform_key())
            if not info: return
            d=Path(__file__).parent/"accounts"/a["id"]/"MAA"; d.mkdir(parents=True,exist_ok=True)
            dlg=UpdateDialog(self.mw,tag,info,str(d))
            if dlg.exec()!=QDialog.Accepted: return
            exe=None
            for p in d.rglob("MAA.exe"): exe=p; break
            if not exe: return
            e={"id":make_id(),"path":str(exe),"args":[],"cwd":"","env":{},"maa_type":"maa","maa_version":tag,"account_ref":a["id"],"launch_mode":"gui","task_pipeline":"startup,fight,recruit,infrast,mall,award","guard_enabled":True,"guard_max_restart":3,"guard_capture_log":False}
            self.mw.warehouse.append(e); self.mw._save(); self.mw._sad(row); self.mw._inj(e,a); self.mw._ls(e)
        t=UpdateCheckThread(); t.result_ready.connect(oc); self.mw.update_thread=t; t.start()

    def pk_maa(self,row):
        a=self.mw.accounts[row]; f,_=QFileDialog.getOpenFileName(self.mw,"选择","","MAA (*.exe);;所有文件 (*.*)")
        if not f: return
        p=str(Path(f)); e={"id":make_id(),"path":p,"args":[],"cwd":"","env":{},"maa_type":"maa","maa_version":parse_maa_version(p) or "","account_ref":a["id"],"launch_mode":"gui","task_pipeline":"startup,fight,recruit,infrast,mall,award","guard_enabled":False,"guard_max_restart":3,"guard_capture_log":False}
        self.mw.warehouse.append(e); self.mw._save(); self.mw._sad(row); self.mw._inj(e,a); self.mw._ls(e)

    # Launch

    def poll(self):
        now=time.time()
        for pid in list(self.mw._cli_procs.keys()):
            p=self.mw._cli_procs[pid]
            if p.poll() is not None:
                out=p.stdout.read().decode(errors="replace").strip(); err=p.stderr.read().decode(errors="replace").strip()
                if out: self.mw._log(f"[maa-cli] {out[:500]}")
                if err: self.mw._log(f"[maa-cli] {err[:500]}")
                rc=p.poll(); self.mw._log(f"[maa-cli] 退出码: {rc}"); self.mw._cli_procs.pop(pid,None); self.mw._proc_status.discard(pid)
                self.mw._notify(f"MAA CLI 已退出" if rc==0 else f"MAA CLI 异常退出",rc!=0)
        for pid in list(self.mw._running_procs.keys()):
            p=self.mw._running_procs[pid]
            if p.poll() is not None:
                self.mw._running_procs.pop(pid,None); self.mw._proc_status.discard(pid); self.mw._proc_start_times.pop(pid,None)
                rc=p.poll()
                if rc!=0:
                    self.mw._notify(f"进程异常退出 (code={rc})",True)
                    w=next((x for x in self.mw.warehouse if x["id"]==pid),None)
                    if w and w.get("guard_enabled") and QMessageBox.question(self,"进程退出",f"{Path(w['path']).stem} 异常退出\n是否重启？")==QMessageBox.Yes:
                        self.mw._ls(w)
                # Check MAA log for completion
                w=next((x for x in self.mw.warehouse if x["id"]==pid),None)
                if w:
                    tasks=self.mw.logs.parse_log(w)
                    errs=[t for t in tasks if t.get("status")=="失败"]
                    if errs: self.mw._notify(f"MAA 任务失败: {errs[0].get('name')}",True)
                    elif tasks: self.mw._notify(f"MAA 完成: {len(tasks)} 个任务")
        # Update status label with runtime
        running=[pid for pid in self.mw._proc_status if pid in self.mw._proc_start_times]
        if running:
            elapsed=int(now-self.mw._proc_start_times[running[0]])
            self.mw.sl.setText(f"运行中 ({elapsed//60}m{elapsed%60}s)")
        # Read current task from MAA log
        for wid in list(self.mw._running_procs.keys()):
            w=next((x for x in self.mw.warehouse if x["id"]==wid),None)
            lp=self.mw.logs.asst_log_path(w) if w else None
            if lp and lp.exists():
                try:
                    last=lp.read_text(encoding="utf-8",errors="replace").strip().split("\n")[-3:]
                    for l in last:
                        if "append_task" in l:
                            for k,v in {"StartUp":"唤醒","Fight":"刷关","Recruit":"公招","Infrast":"基建","Mall":"信用","Award":"奖励","Roguelike":"肉鸽","Reclamation":"生息"}.items():
                                if k in l: self.mw.sl.setText(f"MAA: {v}..."); break
                        elif "[ERR]" in l:
                            err=l.split("[ERR]")[-1].strip()[:80]
                            self.mw._log(f"MAA错误: {err}")
                            self.mw._notify(f"MAA: {err}",True)
                        elif "TaskSwitched" in l:
                            self.mw.sl.setText("MAA: 切换任务...")
                except: pass

    def notify(self,msg,is_error=False):
        if hasattr(self.mw,'tray_icon'):
            self.mw.tray_icon.showMessage("流水线启动器",msg,QSystemTrayIcon.Critical if is_error else QSystemTrayIcon.Information,3000)
        # Webhook
        wh=self.mw.config.get("webhook_url","")
        if wh:
            try:
                data=json.dumps({"msg":msg,"type":"error" if is_error else "info","time":datetime.now().isoformat()}).encode()
                req=urllib.request.Request(wh,data=data,headers={"Content-Type":"application/json"},method="POST")
                urllib.request.urlopen(req,timeout=5)
            except Exception as e:
                try: self.mw._log(f"Webhook 失败: {e}")
                except: pass


    def check_updates(self,silent=False):
        items=[w for w in self.mw.warehouse if w.get("maa_type")!="general"]
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
            if silent: self.mw._log(f"MAA {tag} 可用"); return
            if QMessageBox.question(self,"更新",f"更新 {len(ups)} 个?")==QMessageBox.Yes:
                for w,d in ups:
                    dlg=UpdateDialog(self.mw,tag,info,str(d))
                    if dlg.exec()==QDialog.Accepted: w["maa_version"]=tag
                self.mw._save()
        t=UpdateCheckThread(); t.result_ready.connect(oc); self.mw.update_thread=t; t.start()

    def cu_single(self,w):
        if w.get("maa_type")=="general": return
        def oc(r):
            if not r.get("ok"): return
            tag=r["tag"]; info=r["assets"].get(get_platform_key())
            if not info: return
            dlg=UpdateDialog(self,tag,info,str(Path(w["path"]).parent))
            if dlg.exec()==QDialog.Accepted: w["maa_version"]=tag; self.mw._save()
        t=UpdateCheckThread(); t.result_ready.connect(oc); self.mw.update_thread=t; t.start()

    def restore_geometry(self):
        g=self.mw.config.get("window_geometry","")
        if g:
            p=g.split("+")
            try:
                if len(p)==3:
                    wh=p[0].split("x"); w,h,x,y=int(wh[0]),int(wh[1]),int(p[1]),int(p[2])
                    screen=QApplication.primaryScreen().availableGeometry()
                    x=max(0,min(x,screen.width()-100)); y=max(0,min(y,screen.height()-100))
                    w=min(w,screen.width()); h=min(h,screen.height())
                    self.mw.setGeometry(x,y,w,h)
                else: wh=p[0].split("x"); self.mw.resize(int(wh[0]),int(wh[1]))
            except: self.mw.resize(960,650)
        else: self.mw.resize(960,650)

    def setup_tray(self):
        self.mw.tray_icon=QSystemTrayIcon(self.mw); self.mw.tray_icon.setToolTip("流水线启动器")
        pm=QPixmap(64,64); pm.fill(Qt.transparent); p=QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(58,126,191))); p.setPen(Qt.NoPen); p.drawEllipse(4,4,56,56); p.setBrush(QBrush(Qt.white))
        tri=QPolygonF([QPointF(24,18),QPointF(24,46),QPointF(46,32)]); p.drawPolygon(tri); p.end(); ic=QIcon(pm)
        self.mw.setWindowIcon(ic); self.mw.tray_icon.setIcon(ic); m=QMenu(); m.addAction("显示",self.show_tray); m.addAction("退出",QApplication.quit)
        self.mw.tray_icon.setContextMenu(m); self.mw.tray_icon.show()

    def show_tray(self): self.mw.show(); self.restore_geometry(); self.mw.activateWindow()

    def closeEvent(self,e):
        if not self.mw.isMinimized():
            self.mw.config["window_geometry"]=f"{self.mw.width()}x{self.mw.height()}+{self.mw.x()}+{self.mw.y()}"
            self.mw._save()
        if self.mw.config.get("minimize_to_tray",True) and hasattr(self.mw,'tray_icon'): self.mw.hide(); e.ignore()
        else:
            if hasattr(self.mw,'_emu_monitor'): self.mw._emu_monitor.quit(); self.mw._emu_monitor.wait(2000)
            if hasattr(self.mw,'schedule_thread'): self.mw.schedule_thread.quit(); self.mw.schedule_thread.wait(2000)
            if hasattr(self.mw,'_api_server') and self.mw._api_server: self.mw._api_server.stop_server()
            e.accept(); QApplication.quit()

    def start_schedule(self):
        if self.mw.config.get("schedule",{}).get("enabled"):
            self.mw.schedule_thread=ScheduleThread(self.mw.config); self.mw.schedule_thread.trigger.connect(self.mw._start_pipeline); self.mw.schedule_thread.start()

    def sch(self):
        d=ScheduleDialog(self,self.mw.config.get("schedule",{}))
        if d.exec()==QDialog.Accepted: self.mw.config["schedule"]=d.r; self.mw._save()
        if self.mw.schedule_thread: self.mw.schedule_thread.stop_thread()
        if d.r.get("enabled"): self.mw.schedule_thread=ScheduleThread(self.mw.config); self.mw.schedule_thread.trigger.connect(self.mw._start_pipeline); self.mw.schedule_thread.start()

    def settings(self):
        old_port=self.mw.config.get("api_port",19999); old_token=self.mw.config.get("api_token","")
        d=SettingsDialog(self,self.mw.config)
        if d.exec()==QDialog.Accepted:
            self.mw._set_theme(self.mw.config.get("appearance_mode","Dark")); self.mw._save()
            if self.mw.config.get("api_port",19999)!=old_port or self.mw.config.get("api_token","")!=old_token:
                self.mw._start_api_server()




