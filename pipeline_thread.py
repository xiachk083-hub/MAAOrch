import time,subprocess
from pathlib import Path
from PySide6.QtCore import QThread, Signal

class PipelineThread(QThread):
    progress=Signal(str); program_started=Signal(str,bool); finished=Signal(bool)
    def __init__(self,groups,warehouse,accounts,parent=None):
        super().__init__(); self.groups=groups; self.warehouse={w["id"]:w for w in warehouse}
        self.accounts={a["id"]:a for a in accounts}; self.stop_flag=False; self.pause_flag=False; self.mw=parent
        self._running: list[subprocess.Popen] = []
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
                try: self.mw.cfg.inject_for_thread(w,self.accounts[ac])
                except: pass
            proc = subprocess.Popen([p]+w.get("args",[]),shell=False,cwd=w.get("cwd","") or None)
            self._running.append(proc)
            self.program_started.emit(n,True)
        except Exception as e: self.program_started.emit(f"{n}:{e}",False)
    def _sleep(self,s):
        for _ in range(int(s*10)):
            if self.stop_flag: break
            while self.pause_flag and not self.stop_flag: self.msleep(200)
            self._running = [p for p in self._running if p.poll() is None]
            time.sleep(0.1)
    def stop(self):
        self.stop_flag=True
        for proc in self._running:
            try: proc.terminate()
            except: pass
        self._running.clear()
    def pause(self): self.pause_flag=True
    def resume(self): self.pause_flag=False
