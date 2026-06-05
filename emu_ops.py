import json,subprocess,re
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox, QFileDialog
from task_constants import (CF,EMU_PRESETS,MUMU_INSTANCE_DIRS,find_mumu_cli,detect_emu_instances)

class EmuService:
    """ADB / emulator operations. Uses self.mw to access MainWindow resources."""
    def __init__(self, mw):
        self.mw = mw

    def refresh_instance_list(self,combo,saved_idx=None,saved_name=None):
        combo.setEnabled(False)
        combo.addItem("⏳ 检测中...","")
        if hasattr(self,'_refresh_t') and self._refresh_t and self._refresh_t.isRunning():
            try: self._refresh_t.result.disconnect()
            except: pass
            self._refresh_t.terminate(); self._refresh_t.wait(200)
        class _T(QThread):
            result=Signal(list)
            def run(s): s.result.emit(detect_emu_instances())
        self._refresh_t=_T()
        def _done(instances):
            try:
                if not hasattr(self,'_sad_row'): return  # window destroyed
                combo.blockSignals(True)
                combo.clear(); combo.addItem(f"— 检测到 {len(instances)} 个实例 —","")
                selected=-1
                for j,ins in enumerate(instances):
                    label=ins['name']; running=ins.get("running",False)
                    ms=self.mw._emu_status.get(ins.get("index",""),{})
                    if ms.get("running"): running=True
                    if running: label="▶ "+label
                    if ins.get("adb_port"): label+=f" (:{ins['adb_port']})"
                    combo.addItem(label,ins)
                    if saved_idx and str(ins.get("index",""))==str(saved_idx): selected=j+1
                if saved_name and not saved_idx:
                    pass  # saved_name handled during _sad build
                if selected>=0: combo.setCurrentIndex(selected)
                combo.blockSignals(False)
                combo.setEnabled(True)
            except RuntimeError: pass
        self._refresh_t.result.connect(_done); self._refresh_t.start()

    def test_adb(self,a):
        ad=a.get("adb_address","")
        if not ad: self.mw._ast.setText("输入地址"); return
        self.mw._ast.setText("测试中...")
        adb=a.get("adb_path","") or "adb"
        if hasattr(self,'_test_t') and self._test_t and self._test_t.isRunning():
            try: self._test_t.result.disconnect()
            except: pass
            self._test_t.terminate(); self._test_t.wait(200)
        class _T(QThread):
            result=Signal(str)
            def run(s):
                try:
                    r=subprocess.run([adb,"connect",ad],capture_output=True,timeout=10,creationflags=CF)
                    out=(r.stdout+r.stderr).decode('utf-8','replace').strip()
                    s.result.emit("✅ 成功" if "connected" in out.lower() or "already" in out.lower() else f"⚠ {out[:80]}")
                except Exception as e: s.result.emit(f"❌ {e}")
        self._test_t=_T(); self._test_t.result.connect(lambda r: self.mw._ast.setText(r)); self._test_t.start()
    def browse_adb(self,le,ac):
        f,_=QFileDialog.getOpenFileName(self.mw,"选择 ADB","","adb.exe (adb.exe);;所有文件 (*.*)")
        if f: le.setText(str(Path(f))); ac["adb_path"]=str(Path(f)); self.mw._save()
    def browse_file(self,le,ac,key):
        f,_=QFileDialog.getOpenFileName(self.mw,"选择文件","","可执行文件 (*.exe);;所有文件 (*.*)")
        if f: le.setText(str(Path(f))); ac[key]=str(Path(f)); self.mw._save()
    def screenshot(self,a):
        addr=a.get("adb_address",""); adb=a.get("adb_path","") or "adb"
        if not addr: return
        self.mw._log(f"截图: {addr}...")
        if hasattr(self,'_ss_t') and self._ss_t and self._ss_t.isRunning():
            try: self._ss_t.result.disconnect()
            except: pass
            self._ss_t.terminate(); self._ss_t.wait(200)
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
        self._ss_t=_T()
        def _on(r):
            if r.startswith("ok|"): self.mw._log(f"截图: {r[3:]}")
            elif r.startswith("fail|"): self.mw._log("截图失败")
            elif r.startswith("err|"): self.mw._log(f"截图失败: {r[4:]}")
        t=_T(); t.result.connect(_on); t.start()
    def stop_emu(self,a):
        emu_idx=a.get("emu_instance_index","")
        if not emu_idx: return
        cli=find_mumu_cli()
        if cli:
            self.mw._log(f"关闭模拟器 #{emu_idx}...")
            if hasattr(self,'_stopemu_t') and self._stopemu_t and self._stopemu_t.isRunning():
                try: self._stopemu_t.result.disconnect()
                except: pass
                self._stopemu_t.terminate(); self._stopemu_t.wait(200)
            class _T(QThread):
                result=Signal(str)
                def run(s):
                    try: subprocess.run([cli,"control","--vmindex",str(emu_idx),"shutdown"],creationflags=CF,timeout=15); s.result.emit("ok")
                    except Exception as e: s.result.emit(str(e))
            self._stopemu_t=_T()
            def _on(r):
                if r=="ok": self.mw._log("模拟器已关闭")
                else: self.mw._log(f"关闭失败: {r}")
            self._stopemu_t.result.connect(_on); self._stopemu_t.start()
    def scan_port(self,a,path_edit,addr_edit):
        """Start emulator, wait, then scan ADB port"""
        emu_idx=a.get("emu_instance_index","")
        if not emu_idx: QMessageBox.information(self.mw,"提示","请先选择模拟器实例"); return
        cli=find_mumu_cli()
        if not cli: QMessageBox.warning(self.mw,"提示","未找到 mumu-cli"); return
        # Prevent double-click: kill existing scan before starting new one
        if hasattr(self,'_t') and self._t and self._t.isRunning():
            try: self._t.result.disconnect()
            except: pass
            self._t.terminate(); self._t.wait(200)
        self.mw._log(f"扫描端口: 实例 #{emu_idx}")
        self.mw.sl.setText("启动模拟器...")
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
                s.result.emit("启动完成，等待开机..."); s.msleep(5000)
                # Step 3: get actual port — try config.json first, then mumu-cli, then predict
                target_port=None
                emu_idx_int = int(s.emu_idx) if str(s.emu_idx).isdigit() else 0
                # Try directory scan (reads config.json, no mumu-cli needed)
                try:
                    for vms_dir in MUMU_INSTANCE_DIRS:
                        if vms_dir.exists():
                            vm = vms_dir / str(s.emu_idx)
                            if vm.is_dir() and (vm/"config.json").exists():
                                cfg=json.loads((vm/"config.json").read_text(encoding="utf-8"))
                                target_port = str(cfg.get("adb_port",""))
                                if target_port and target_port!="0": break
                except: pass
                # Try mumu-cli as fallback
                if not target_port:
                    try:
                        instances=detect_emu_instances()
                        for ins in instances:
                            if str(ins.get("index",""))==str(s.emu_idx) and ins.get("adb_port"):
                                target_port=ins["adb_port"]; break
                    except: pass
                # As last resort, use MuMu 12 formula: port = 16384 + index*32
                if not target_port:
                    target_port = str(16384 + emu_idx_int * 32)
                # Step 4: use port directly — adb connect only, no verification
                addr=f"127.0.0.1:{target_port}"
                try: subprocess.run([s.adb,"connect",addr],capture_output=True,timeout=3,creationflags=CF)
                except: pass
                s.result.emit("__found__"+addr)
        if hasattr(self,'_t') and self._t.isRunning():
            self._t.result.disconnect(); self._t.terminate(); self._t.wait(200)
        self._t=_T(emu_idx,cli,adb)
        def _on_r(r):
            if r.startswith("__found__"):
                addr=r[9:]; addr_edit.setText(addr); a.__setitem__("adb_address",addr); self.mw._save()
                self.mw._log(f"端口: {addr}"); self.mw._sl(f"端口: {addr}")
            elif r.startswith("__err__"):
                self.mw._log(r[8:]); self.mw.sl.setText("就绪")
            else: self.mw.sl.setText(r)
        self._t.result.connect(_on_r)
        self._t.start()
    def scan(self,a,cb):
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
            self._scan_thread.result.disconnect(); self._scan_thread.terminate(); self._scan_thread.wait(200)
        self._scan_thread=_T()
        def _on_results(results):
            cb.clear(); cb.addItem("— 在线设备 —","")
            if not results: cb.addItem("未发现在线设备","")
            else:
                for addr,ok in results:
                    if addr=="__err__":
                        self.mw._log(f"扫描出错: {ok}")
                        cb.addItem(f"扫描出错: {ok}","")
                        continue
                    cb.addItem(f"{addr} {'✅' if ok else '⚠'}",addr)
                cb.setCurrentIndex(1)
            cb.setEnabled(True)
        self._scan_thread.result.connect(_on_results)
        self._scan_thread.start()
