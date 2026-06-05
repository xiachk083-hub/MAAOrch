import time,subprocess
from pathlib import Path
from PySide6.QtCore import QThread, Signal

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
        if hasattr(self,'_api_server') and self._api_server: self._api_server.stop_server()
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
    old_port=self.config.get("api_port",19999); old_token=self.config.get("api_token","")
    d=SettingsDialog(self,self.config)
    if d.exec()==QDialog.Accepted:
        self._set_theme(self.config.get("appearance_mode","Dark")); self._save()
        if self.config.get("api_port",19999)!=old_port or self.config.get("api_token","")!=old_token:
            self._start_api_server()

