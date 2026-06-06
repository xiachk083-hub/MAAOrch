from __future__ import annotations
import json,subprocess,re
from pathlib import Path
from datetime import datetime
from typing import Any
from PySide6.QtWidgets import QMessageBox, QFileDialog, QComboBox, QLineEdit
from task_constants import (CF,EMU_PRESETS,MUMU_INSTANCE_DIRS,find_mumu_cli,detect_emu_instances)
from background import BackgroundTask
from callbacks import ServiceContext

class EmuService:
    """ADB / emulator operations."""
    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def refresh_instance_list(
        self, combo: QComboBox, saved_idx: str | None = None, saved_name: str | None = None
    ) -> None:
        combo.setEnabled(False)
        combo.addItem("⏳ 检测中...","")
        if hasattr(self,'_refresh_t') and self._refresh_t and self._refresh_t.isRunning():
            try: self._refresh_t.result.disconnect()
            except: pass
            self._refresh_t.terminate(); self._refresh_t.wait(200)
        self._refresh_t=BackgroundTask(detect_emu_instances)
        def _done(instances):
            try:
                if not hasattr(self.ctx._mw,'_sad_row'): return  # window destroyed
                combo.blockSignals(True)
                combo.clear(); combo.addItem(f"— 检测到 {len(instances)} 个实例 —","")
                selected=-1
                for j,ins in enumerate(instances):
                    label=ins['name']; running=ins.get("running",False)
                    ms=self.ctx.emu_status.get(ins.get("index",""),{})
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

    def test_adb(self, a: dict) -> None:
        ad=a.adb_address
        if not ad: self.ctx._mw._ast.setText("输入地址"); return
        self.ctx._mw._ast.setText("测试中...")
        adb=a.get("adb_path","") or "adb"
        if hasattr(self,'_test_t') and self._test_t and self._test_t.isRunning():
            try: self._test_t.result.disconnect()
            except: pass
            self._test_t.terminate(); self._test_t.wait(200)
        def _fn():
            try:
                r=subprocess.run([adb,"connect",ad],capture_output=True,timeout=10,creationflags=CF)
                out=(r.stdout+r.stderr).decode('utf-8','replace').strip()
                return "✅ 成功" if "connected" in out.lower() or "already" in out.lower() else f"⚠ {out[:80]}"
            except Exception as e: return f"❌ {e}"
        self._test_t=BackgroundTask(_fn); self._test_t.result.connect(lambda r: self.ctx._mw._ast.setText(str(r))); self._test_t.start()
    def browse_adb(self, le: QLineEdit, ac: dict) -> None:
        f,_=QFileDialog.getOpenFileName(self.ctx._mw,"选择 ADB","","adb.exe (adb.exe);;所有文件 (*.*)")
        if f: le.setText(str(Path(f))); ac["adb_path"]=str(Path(f)); self.ctx.save()
    def browse_file(self, le: QLineEdit, ac: dict, key: str) -> None:
        f,_=QFileDialog.getOpenFileName(self.ctx._mw,"选择文件","","可执行文件 (*.exe);;所有文件 (*.*)")
        if f: le.setText(str(Path(f))); ac[key]=str(Path(f)); self.ctx.save()
    def screenshot(self, a: dict) -> None:
        addr=a.adb_address; adb=a.get("adb_path","") or "adb"
        if not addr: return
        self.ctx.log(f"截图: {addr}...")
        if hasattr(self,'_ss_t') and self._ss_t and self._ss_t.isRunning():
            try: self._ss_t.result.disconnect()
            except: pass
            self._ss_t.terminate(); self._ss_t.wait(200)
        def _fn():
            try:
                r=subprocess.run([adb,"-s",addr,"exec-out","screencap","-p"],capture_output=True,timeout=10,creationflags=CF)
                if r.returncode==0 and r.stdout:
                    ss_dir=Path(__file__).parent/"screenshots"; ss_dir.mkdir(exist_ok=True)
                    fn=ss_dir/f"MAA_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    fn.write_bytes(r.stdout); return f"ok|{fn.name}"
                else: return "fail|"
            except Exception as e: return f"err|{e}"
        def _on(r):
            s=str(r)
            if s.startswith("ok|"): self.ctx.log(f"截图: {s[3:]}")
            elif s.startswith("fail|"): self.ctx.log("截图失败")
            elif s.startswith("err|"): self.ctx.log(f"截图失败: {s[4:]}")
        self._ss_t=BackgroundTask(_fn); self._ss_t.result.connect(_on); self._ss_t.start()
    def stop_emu(self, a: dict) -> None:
        emu_idx=a.emu_instance_index
        if not emu_idx: return
        cli=find_mumu_cli()
        if cli:
            self.ctx.log(f"关闭模拟器 #{emu_idx}...")
            if hasattr(self,'_stopemu_t') and self._stopemu_t and self._stopemu_t.isRunning():
                try: self._stopemu_t.result.disconnect()
                except: pass
                self._stopemu_t.terminate(); self._stopemu_t.wait(200)
            def _fn():
                try: subprocess.run([cli,"control","--vmindex",str(emu_idx),"shutdown"],creationflags=CF,timeout=15); return "ok"
                except Exception as e: return str(e)
            def _on(r):
                s=str(r)
                if s=="ok": self.ctx.log("模拟器已关闭")
                else: self.ctx.log(f"关闭失败: {s}")
            self._stopemu_t=BackgroundTask(_fn); self._stopemu_t.result.connect(_on); self._stopemu_t.start()
    def scan_port(self, a: dict, path_edit: QLineEdit, addr_edit: QLineEdit) -> None:
        """Start emulator, wait, then scan ADB port."""
        emu_idx=a.emu_instance_index
        if not emu_idx: QMessageBox.information(self.ctx._mw,"提示","请先选择模拟器实例"); return
        cli=find_mumu_cli()
        if not cli: QMessageBox.warning(self.ctx._mw,"提示","未找到 mumu-cli"); return
        if hasattr(self,'_t') and self._t and self._t.isRunning():
            try: self._t.result.disconnect()
            except: pass
            self._t.terminate(); self._t.wait(200)
        self.ctx.log(f"扫描端口: 实例 #{emu_idx}")
        self.ctx._mw.sl.setText("启动模拟器...")
        adb=a.get("adb_path","") or "adb"
        import time as _time
        def _fn():
            try:
                subprocess.run([cli,"control","--vmindex",str(emu_idx),"launch"],creationflags=CF,timeout=15)
            except Exception as e:
                return f"__err__启动失败: {e}"
            _time.sleep(5)
            target_port=None
            emu_idx_int = int(emu_idx) if str(emu_idx).isdigit() else 0
            try:
                for vms_dir in MUMU_INSTANCE_DIRS:
                    if vms_dir.exists():
                        vm = vms_dir / str(emu_idx)
                        if vm.is_dir() and (vm/"config.json").exists():
                            cfg=json.loads((vm/"config.json").read_text(encoding="utf-8"))
                            target_port = str(cfg.get("adb_port",""))
                            if target_port and target_port!="0": break
            except: pass
            if not target_port:
                try:
                    instances=detect_emu_instances()
                    for ins in instances:
                        if str(ins.get("index",""))==str(emu_idx) and ins.get("adb_port"):
                            target_port=ins["adb_port"]; break
                except: pass
            if not target_port:
                target_port = str(16384 + emu_idx_int * 32)
            addr=f"127.0.0.1:{target_port}"
            try: subprocess.run([adb,"connect",addr],capture_output=True,timeout=3,creationflags=CF)
            except: pass
            return "__found__"+addr
        def _on_r(r):
            s=str(r)
            if s.startswith("__found__"):
                addr=s[9:]; addr_edit.setText(addr); a.adb_address = addr; self.ctx.save()
                self.ctx.log(f"端口: {addr}"); self.ctx._mw.sl.setText(f"端口: {addr}")
            elif s.startswith("__err__"):
                self.ctx.log(s[8:]); self.ctx._mw.sl.setText("就绪")
            else: self.ctx._mw.sl.setText(s)
        self.ctx._mw.sl.setText("启动完成，等待开机...")
        self._t=BackgroundTask(_fn); self._t.result.connect(_on_r); self._t.start()
    def scan(self, a: dict, cb: QComboBox) -> None:
        cb.clear(); cb.addItem("扫描中...",""); cb.setEnabled(False)
        adb=a.get("adb_path","") or "adb"
        def _fn():
            results=[]
            try:
                r=subprocess.run([adb,"devices"],capture_output=True,timeout=5,creationflags=CF)
                for m in re.finditer(rb':(\d+)\s+(\S+)',r.stdout):
                    addr="127.0.0.1:"+m.group(1).decode('ascii')
                    st=m.group(2).decode('ascii','replace')
                    if st in ("device","unauthorized","offline"):
                        results.append((addr,st=="device"))
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
            return results
        if hasattr(self,'_scan_thread') and self._scan_thread.isRunning():
            self._scan_thread.result.disconnect(); self._scan_thread.terminate(); self._scan_thread.wait(200)
        def _on_results(results):
            cb.clear(); cb.addItem("— 在线设备 —","")
            if not results: cb.addItem("未发现在线设备","")
            else:
                for addr,ok in results:
                    if addr=="__err__":
                        self.ctx.log(f"扫描出错: {ok}")
                        cb.addItem(f"扫描出错: {ok}","")
                        continue
                    cb.addItem(f"{addr} {'✅' if ok else '⚠'}",addr)
                cb.setCurrentIndex(1)
            cb.setEnabled(True)
        self._scan_thread=BackgroundTask(_fn); self._scan_thread.result.connect(_on_results); self._scan_thread.start()
