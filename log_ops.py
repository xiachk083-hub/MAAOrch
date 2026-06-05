import re, json
from pathlib import Path
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QLabel, QMessageBox, QHeaderView, QAbstractItemView
from PySide6.QtGui import QColor, QFont
from updater import UpdateCheckThread, UpdateDialog
from dialogs import AccountDialog
from utils import get_platform_key, parse_maa_version

class LogService:
    def __init__(self, mw): self.mw = mw

    def asst_log_path(self,w):
        return Path(w.get("path","")).parent/"debug"/"asst.log"

    def switch_maa_version(self,w,channel):
        """Download and switch to latest version of specified channel"""
        if QMessageBox.question(self.mw,"切换版本",f"将下载最新 {channel} 版 MAA\n并替换当前版本\n\n是否继续？")!=QMessageBox.Yes: return
        self.mw._log(f"切换 MAA 版本: {channel}")
        self.mw.sl.setText(f"下载 {channel} 版...")
        def _on_result(r):
            if not r.get("ok"): QMessageBox.critical(self.mw,"失败",r.get("error","")); self.mw.sl.setText("就绪"); return
            tag=r["tag"]; info=r["assets"].get(get_platform_key())
            if not info: QMessageBox.warning(self.mw,"失败","无可用包"); self.mw.sl.setText("就绪"); return
            dlg=UpdateDialog(self,tag,info,str(Path(w["path"]).parent))
            if dlg.exec()==QDialog.Accepted:
                w["maa_version"]=tag; w["update_channel"]=channel; self.mw._save()
                self.mw._log(f"MAA 已切换至: {tag}")
                # Regenerate config injection
                ac=next((a for a in self.mw.accounts if a["id"]==w.get("account_ref","")),None)
                if ac: self.mw.cfg.inject(w,ac)
            self.mw.sl.setText("就绪")
        t=UpdateCheckThread(); t.result_ready.connect(_on_result); self.mw.update_thread=t; t.start()

    def parse_log(self,w,tail=500):
        lp=self.asst_log_path(w)
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

    def show_stats(self,w):
        tasks=self.parse_log(w)
        if not tasks:
            QMessageBox.information(self.mw,"统计","暂无运行数据\n等待 MAA 执行任务后自动生成")
            return
        d=QDialog(self.mw); d.setWindowTitle("MAA 运行统计"); d.setMinimumSize(400,300)
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

    def view_log(self,w):
        lp=self.asst_log_path(w)
        if not lp.exists(): QMessageBox.information(self.mw,"日志","暂无日志文件"); return
        try: content=lp.read_text(encoding="utf-8",errors="replace")
        except: QMessageBox.information(self.mw,"日志","无法读取日志"); return
        d=QDialog(self.mw); d.setWindowTitle("MAA 日志"); d.setMinimumSize(700,500)
        l=QVBoxLayout(d); te=QPlainTextEdit(); te.setReadOnly(True); te.setPlainText("\n".join(content.split("\n")[-200:]))
        # Scroll to bottom
        te.moveCursor(te.textCursor().End)
        l.addWidget(te); l.addWidget(QPushButton("关闭",clicked=d.accept)); d.exec()
