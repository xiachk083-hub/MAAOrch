import json
from pathlib import Path
import urllib.request
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QMessageBox,
    QCheckBox,QGroupBox,QFormLayout,QComboBox,QLineEdit,QSpinBox,QWidget,
    QDialogButtonBox,QFileDialog,QTabWidget,QScrollArea,QFrame,QInputDialog)
from utils import make_id
from config import set_auto_start
from task_constants import TASK_NAMES,TASK_DEFAULTS,CLIENT_TYPES

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
        ar=QHBoxLayout(); self.au=QCheckBox("自动更新 MAA"); self.au.setChecked(cfg.get("auto_update_maa",True)); ar.addWidget(self.au)
        ar.addWidget(QLabel("间隔")); self.ai=QSpinBox(); self.ai.setRange(1,72); self.ai.setValue(cfg.get("maa_update_interval",6)); self.ai.setSuffix(" 小时"); ar.addWidget(self.ai); ar.addStretch(); gl4.addLayout(ar)
        wr=QHBoxLayout(); wr.addWidget(QLabel("Webhook:"))
        self.wh=QLineEdit(cfg.get("webhook_url","")); self.wh.setPlaceholderText("企业微信/钉钉/自定义 URL"); wr.addWidget(self.wh,1)
        wh_tb=QPushButton("测试"); wh_tb.clicked.connect(lambda: self._test_webhook(self.wh.text().strip())); wr.addWidget(wh_tb); gl4.addLayout(wr); l.addWidget(g4)
        # daigan
        g6=QGroupBox("daigan 联动"); gl6=QVBoxLayout(g6)
        dr=QHBoxLayout(); dr.addWidget(QLabel("地址:"))
        self.daigan_url=QLineEdit(cfg.get("daigan_url","")); self.daigan_url.setPlaceholderText("http://localhost:3456"); dr.addWidget(self.daigan_url,1); gl6.addLayout(dr); l.addWidget(g6)
        # API
        g5=QGroupBox("HTTP API"); gl5=QVBoxLayout(g5)
        apr=QHBoxLayout(); apr.addWidget(QLabel("端口:"))
        self.api_port=QSpinBox(); self.api_port.setRange(1024,65535); self.api_port.setValue(cfg.get("api_port",19999)); apr.addWidget(self.api_port,1); gl5.addLayout(apr)
        atr=QHBoxLayout(); atr.addWidget(QLabel("Token:"))
        self.api_token=QLineEdit(cfg.get("api_token","")); self.api_token.setPlaceholderText("留空不验证"); atr.addWidget(self.api_token,1); gl5.addLayout(atr); l.addWidget(g5)
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
        self.c["appearance_mode"]=self.th.currentText(); self.c["auto_start"]=self.auto.isChecked(); self.c["minimize_to_tray"]=self.tray.isChecked(); self.c["check_update_on_start"]=self.cu.isChecked(); self.c["auto_update_maa"]=self.au.isChecked(); self.c["maa_update_interval"]=self.ai.value(); self.c["webhook_url"]=self.wh.text().strip()
        self.c["api_port"]=self.api_port.value(); self.c["api_token"]=self.api_token.text().strip()
        self.c["daigan_url"]=self.daigan_url.text().strip()
        set_auto_start(self.c["auto_start"]); self.accept()

class AccountDialog(QDialog):
    def __init__(self,p,acc=None):
        super().__init__(p); self.setWindowTitle("编辑账号" if acc else "新建账号"); self.setMinimumSize(540,520); self.a=acc or {}
        l=QVBoxLayout(self); l.setContentsMargins(12,12,12,8); l.setSpacing(0)
        hdr=QHBoxLayout(); hdr.addWidget(QLabel("MAA 账号配置",font=QFont("Microsoft YaHei UI",14,QFont.Bold))); hdr.addStretch(); l.addLayout(hdr)
        l.addSpacing(6)

        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        sw=QWidget(); f=QFormLayout(sw); f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow); f.setSpacing(4)
        f.setContentsMargins(0,4,0,0)

        # ── 基本信息 ──
        f.addRow(QWidget(), _sec("基本信息"))
        self.n=QLineEdit(self.a.get("name","")); self.n.setPlaceholderText("例如: 官服大号"); f.addRow(_lbl("账号名"),self.n)
        self.c=QComboBox()
        for k,v in CLIENT_TYPES.items(): self.c.addItem(v,k)
        idx=self.c.findData(self.a.get("game_client","Official")); self.c.setCurrentIndex(max(0,idx)); f.addRow(_lbl("区服"),self.c)
        self.tags=QLineEdit(self.a.get("tags","")); self.tags.setPlaceholderText("逗号分隔，如 日常,材料,肉鸽"); f.addRow(_lbl("标签"),self.tags)
        self.fs=QLineEdit(self.a.get("fight_stage","")); self.fs.setPlaceholderText("关卡，如 1-7"); f.addRow(_lbl("关卡"),self.fs)

        # ── 连接 ──
        f.addRow(QWidget(), _sec("连接设置"))
        self.adr=QLineEdit(self.a.get("adb_address","")); self.adr.setPlaceholderText("例如: 127.0.0.1:7555"); f.addRow(_lbl("ADB 地址"),self.adr)
        self.adb=QLineEdit(self.a.get("adb_path","")); self.adb.setPlaceholderText("留空使用默认 ADB"); f.addRow(_lbl("ADB 路径"),self.adb)
        self.pc=QComboBox(); self.pc.addItems(["— 无 —","MuMuPro","PlayCover","Waydroid"]); self.pc.setCurrentText(self.a.get("connection_preset") or "— 无 —"); f.addRow(_lbl("预设"),self.pc)
        self.tc=QComboBox(); self.tc.addItems(["ADB","MiniTouch","MaaTouch"]); self.tc.setCurrentText(self.a.get("touch_mode","ADB")); f.addRow(_lbl("触控"),self.tc)
        self.sw_an=QLineEdit(self.a.get("account_switch","")); self.sw_an.setPlaceholderText("如 手机号或邮箱，留空不切换"); f.addRow(_lbl("账号切换"),self.sw_an)

        # ── 模拟器 ──
        f.addRow(QWidget(), _sec("模拟器"))
        self.emu_path=QLineEdit(self.a.get("emu_path","")); self.emu_path.setPlaceholderText("留空自动检测"); f.addRow(_lbl("启动路径"),self.emu_path)
        emu_row=QHBoxLayout(); emu_row.setSpacing(8)
        self.emu_launch_cb=QCheckBox("自启模拟器"); self.emu_launch_cb.setChecked(self.a.get("emu_launch",False))
        emu_row.addWidget(self.emu_launch_cb)
        emu_row.addWidget(QLabel("等待")); self.emu_wait_sp=QSpinBox(); self.emu_wait_sp.setRange(0,300); self.emu_wait_sp.setValue(self.a.get("emu_wait",30)); self.emu_wait_sp.setSuffix(" 秒  "); emu_row.addWidget(self.emu_wait_sp)
        emu_row.addStretch(); f.addRow(_lbl(""),emu_row)

        # ── 任务 ──
        f.addRow(QWidget(), _sec("默认任务"))
        self.tk={}; kw=QWidget(); kl=QHBoxLayout(kw); kl.setContentsMargins(0,0,0,0); kl.setSpacing(6)
        for k,v in TASK_NAMES.items():
            if k=="closedown": continue
            cb=QCheckBox(v); cb.setChecked(k in self.a.get("tasks",["StartUp","Fight"])); self.tk[k]=cb; kl.addWidget(cb)
        kl.addStretch(); f.addRow(_lbl(""),kw)

        # ── 启动选项 ──
        f.addRow(QWidget(), _sec("启动选项"))
        opts1=QHBoxLayout(); opts1.setSpacing(8)
        self.sm_cb=QCheckBox("最小化启动"); self.sm_cb.setChecked(self.a.get("start_minimized",False)); opts1.addWidget(self.sm_cb)
        self.sd_cb=QCheckBox("直接运行"); self.sd_cb.setChecked(self.a.get("start_directly",False)); opts1.addWidget(self.sd_cb)
        self.adb_fail_cb=QCheckBox("ADB失败启模拟器"); self.adb_fail_cb.setChecked(self.a.get("adb_fail_launch_emu",False)); opts1.addWidget(self.adb_fail_cb)
        opts1.addStretch(); f.addRow(_lbl(""),opts1)
        opts2=QHBoxLayout(); opts2.setSpacing(8)
        self.sync_cb=QCheckBox("启动时同步配置"); self.sync_cb.setChecked(self.a.get("sync_tasks",False)); opts2.addWidget(self.sync_cb)
        self.sanity_cb=QCheckBox("理智回满自动启动"); self.sanity_cb.setChecked(self.a.get("sanity_driven",False)); opts2.addWidget(self.sanity_cb)
        opts2.addStretch(); f.addRow(_lbl(""),opts2)

        scroll.setWidget(sw); l.addWidget(scroll,1)
        b=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); b.accepted.connect(self._save); b.rejected.connect(self.reject); l.addWidget(b)
        self.setStyleSheet("QScrollArea{background:transparent} QDialog{background:rgba(30,30,30,240)}")

        scroll.setWidget(sw); l.addWidget(scroll,1)
        b=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); b.accepted.connect(self._save); b.rejected.connect(self.reject); l.addWidget(b)
        self.setStyleSheet("QScrollArea{background:transparent} QFrame#configCard{background:transparent}")
    def _save(self):
        p=self.pc.currentText()
        if p=="— 无 —": p=""
        self.r={"id":self.a.get("id",make_id()),"name":self.n.text().strip() or "未命名","game_client":self.c.currentData(),"adb_path":self.adb.text().strip(),"adb_address":self.adr.text().strip(),"connection_preset":p,"touch_mode":self.tc.currentText(),"tasks":[t for t,cb in self.tk.items() if cb.isChecked()],"fight_stage":self.fs.text().strip(),"task_settings":self.a.get("task_settings",{}),"sync_tasks":self.sync_cb.isChecked(),"account_switch":self.sw_an.text().strip(),"emu_path":self.emu_path.text().strip(),"emu_launch":self.emu_launch_cb.isChecked(),"emu_wait":self.emu_wait_sp.value(),"start_minimized":self.sm_cb.isChecked(),"start_directly":self.sd_cb.isChecked(),"adb_fail_launch_emu":self.adb_fail_cb.isChecked(),"sanity_driven":self.sanity_cb.isChecked(),"tags":self.tags.text().strip()}; self.accept()


def _sec(title: str) -> QLabel:
    s=QLabel(title); s.setStyleSheet("font-weight:bold;color:#aaa;border-bottom:1px solid #444;margin-top:8px;padding-bottom:2px")
    return s

def _lbl(text: str) -> QLabel:
    l=QLabel(text); l.setFixedWidth(60); l.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
    l.setStyleSheet("color:#aaa")
    return l

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

