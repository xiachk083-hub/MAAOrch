from __future__ import annotations
from typing import Any
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QWidget, QCheckBox, QLineEdit,
    QComboBox, QSpinBox, QDialogButtonBox)

_INFRAST_FACILITIES = ["Trade","Mfg","Control","Power","Reception","Office","Dorm","Processing","Training"]
_DRONE_OPTIONS = [("金钱", "Money"), ("无人机", "Drone"), ("战斗记录", "CombatRecord")]


def open_smart_config(mw: Any) -> None:
    sg = mw.config.get("smart_global", {})
    dts = dict(sg.get("default_task_settings", {}))
    d = QDialog(mw)
    d.setWindowTitle("智能调度任务配置")
    d.setMinimumSize(520, 400)
    vl = QVBoxLayout(d)
    tabs = QTabWidget()
    vl.addWidget(tabs)

    # ── Fight ──
    w1 = QWidget(); l1 = QVBoxLayout(w1); l1.setSpacing(6)
    ft = dts.get("Fight", {})
    med_row = QHBoxLayout()
    use_med = QCheckBox("吃体力药"); use_med.setChecked(ft.get("use_medicine", False))
    med_row.addWidget(use_med)
    exp_med = QCheckBox("优先吃快过期药"); exp_med.setChecked(ft.get("use_expiring_medicine", False))
    med_row.addWidget(exp_med)
    med_day = QSpinBox(); med_day.setRange(1,7); med_day.setValue(ft.get("medicine_expire_days",2)); med_day.setSuffix(" 天"); med_day.setFixedWidth(65)
    med_row.addWidget(QLabel("过期:")); med_row.addWidget(med_day)
    med_row.addStretch()
    l1.addLayout(med_row)
    srm_row = QHBoxLayout()
    srm_row.addWidget(QLabel("关卡重置:"))
    srm = QComboBox(); srm.addItem("当前","Current"); srm.addItem("上周","Last"); srm.addItem("昨日","Yesterday"); srm.addItem("已通关","Clear")
    ci = srm.findData(ft.get("stage_reset_mode","Current")); srm.setCurrentIndex(ci if ci>=0 else 0)
    srm_row.addWidget(srm)
    srm_row.addWidget(QLabel("次数:"))
    ts = QSpinBox(); ts.setRange(1,999); ts.setValue(ft.get("times",99))
    srm_row.addWidget(ts)
    lc = QCheckBox("限制次数"); lc.setChecked(ft.get("enable_times_limit",False))
    srm_row.addWidget(lc)
    srm_row.addStretch()
    l1.addLayout(srm_row)
    spec_row = QHBoxLayout()
    grandet = QCheckBox("搓玉模式"); grandet.setChecked(ft.get("is_dr_grandet",False))
    spec_row.addWidget(grandet)
    act_med = QCheckBox("活动用过期药"); act_med.setChecked(ft.get("use_expire_medicine_for_activity",False))
    spec_row.addWidget(act_med)
    opt = QCheckBox("可选关卡"); opt.setChecked(ft.get("use_optional_stage",False))
    spec_row.addWidget(opt)
    inv = QCheckBox("仓库目标"); inv.setChecked(ft.get("is_inventory_target",False))
    spec_row.addWidget(inv)
    wk = QCheckBox("每周计划"); wk.setChecked(ft.get("use_weekly_schedule",False))
    spec_row.addWidget(wk)
    spec_row.addStretch()
    l1.addLayout(spec_row)
    ser_row = QHBoxLayout()
    hs = QCheckBox("隐藏系列"); hs.setChecked(ft.get("hide_series",False))
    ser_row.addWidget(hs)
    ser_row.addWidget(QLabel("系列号:"))
    ss = QSpinBox(); ss.setRange(0,10); ss.setValue(ft.get("series",0))
    ser_row.addWidget(ss)
    hu = QCheckBox("隐藏不可用关卡"); hu.setChecked(ft.get("hide_unavailable_stage",False))
    ser_row.addWidget(hu)
    ser_row.addStretch()
    l1.addLayout(ser_row)
    l1.addStretch()
    tabs.addTab(w1, "刷关作战")

    # ── Recruit ──
    w2 = QWidget(); l2 = QVBoxLayout(w2); l2.setSpacing(6)
    rt = dts.get("Recruit", {})
    sel = rt.get("select", [3,4,5])
    l2.addWidget(QLabel("必选星级:"))
    sr = QHBoxLayout()
    star_cbs = {}
    for s in [3,4,5]:
        cb = QCheckBox(f"{s}★"); cb.setChecked(s in sel); star_cbs[s] = cb; sr.addWidget(cb)
    sr.addStretch()
    l2.addLayout(sr)
    ref_row = QHBoxLayout()
    ref = QCheckBox("刷新"); ref.setChecked(rt.get("refresh",True)); ref_row.addWidget(ref)
    fref = QCheckBox("强制刷新"); fref.setChecked(rt.get("force_refresh",True)); ref_row.addWidget(fref)
    pref = QCheckBox("优先标签"); pref.setChecked(rt.get("prefer_tag_enabled",True)); ref_row.addWidget(pref)
    ref_row.addStretch()
    l2.addLayout(ref_row)
    pt_row = QHBoxLayout()
    pte = QCheckBox("保留标签"); pte.setChecked(rt.get("preserve_tag_enabled",False))
    pt_row.addWidget(pte)
    ptt = QLineEdit(rt.get("preserve_tags","支援机械"))
    ptt.setPlaceholderText("标签名"); ptt.setEnabled(pte.isChecked())
    pte.toggled.connect(lambda c: ptt.setEnabled(c))
    pt_row.addWidget(ptt,1)
    lt = QSpinBox(); lt.setRange(60,1440); lt.setValue(rt.get("level3_time",540)); lt.setSuffix(" 分")
    pt_row.addWidget(QLabel("3★时间:")); pt_row.addWidget(lt)
    pt_row.addStretch()
    l2.addLayout(pt_row)
    l2.addStretch()
    tabs.addTab(w2, "公开招募")

    # ── Infrast ──
    w3 = QWidget(); l3 = QVBoxLayout(w3); l3.setSpacing(6)
    it = dts.get("Infrast", {})
    mc = QComboBox(); mc.addItem("常规","Normal"); mc.addItem("队列轮换","Rotation")
    mi = mc.findData(it.get("mode","Normal")); mc.setCurrentIndex(mi if mi>=0 else 0)
    l3.addWidget(QLabel("模式:")); l3.addWidget(mc)
    fac_row = QHBoxLayout()
    fac_cbs = {}
    cf = it.get("facilities",_INFRAST_FACILITIES)
    for f in _INFRAST_FACILITIES:
        cb = QCheckBox(f); cb.setChecked(f in cf); fac_cbs[f] = cb; fac_row.addWidget(cb)
    fac_row.addStretch()
    l3.addWidget(QLabel("设施:")); l3.addLayout(fac_row)
    dr_row = QHBoxLayout()
    dr_row.addWidget(QLabel("无人机:"))
    dc = QComboBox()
    for txt, val in _DRONE_OPTIONS: dc.addItem(txt, val)
    di = dc.findData(it.get("drones","Money")); dc.setCurrentIndex(di if di>=0 else 0)
    dr_row.addWidget(dc)
    ds = QSpinBox(); ds.setRange(0,100); ds.setValue(it.get("dorm_threshold",30)); ds.setSuffix(" %")
    dr_row.addWidget(QLabel("宿舍阈值:")); dr_row.addWidget(ds)
    dr_row.addStretch()
    l3.addLayout(dr_row)
    chk_row = QHBoxLayout()
    dt = QCheckBox("宿舍信任"); dt.setChecked(it.get("dorm_trust_enabled",True)); chk_row.addWidget(dt)
    os = QCheckBox("自动搓玉"); os.setChecked(it.get("originium_shard_auto",True)); chk_row.addWidget(os)
    rc = QCheckBox("会客线索"); rc.setChecked(it.get("reception_clue",True)); chk_row.addWidget(rc)
    sc = QCheckBox("送线索"); sc.setChecked(it.get("send_clue",True)); chk_row.addWidget(sc)
    ct = QCheckBox("继续训练"); ct.setChecked(it.get("continue_training",False)); chk_row.addWidget(ct)
    chk_row.addStretch()
    l3.addLayout(chk_row)
    l3.addStretch()
    tabs.addTab(w3, "基建换班")

    # ── Mall ──
    w4 = QWidget(); l4 = QVBoxLayout(w4); l4.setSpacing(6)
    mt = dts.get("Mall", {})
    shop = QCheckBox("信用购物"); shop.setChecked(mt.get("shopping",True)); l4.addWidget(shop)
    bl_row = QHBoxLayout()
    bl_row.addWidget(QLabel("黑名单:"))
    bl = QLineEdit(mt.get("blacklist","碳;家具;加急许可")); bl.setPlaceholderText("用;分隔")
    bl_row.addWidget(bl,1)
    l4.addLayout(bl_row)
    m_row = QHBoxLayout()
    cf2 = QCheckBox("信用战斗"); cf2.setChecked(mt.get("credit_fight",False)); m_row.addWidget(cf2)
    vf = QCheckBox("访问好友"); vf.setChecked(mt.get("visit_friends",True)); m_row.addWidget(vf)
    fl = QLineEdit(mt.get("first_list","招聘许可")); fl.setPlaceholderText("优先"); fl.setFixedWidth(100)
    m_row.addWidget(QLabel("优先:")); m_row.addWidget(fl)
    m_row.addStretch()
    l4.addLayout(m_row)
    d_row = QHBoxLayout()
    od = QCheckBox("仅买折扣"); od.setChecked(mt.get("only_buy_discount",False)); d_row.addWidget(od)
    rm = QCheckBox("保留信用点"); rm.setChecked(mt.get("reserve_max_credit",False)); d_row.addWidget(rm)
    d_row.addStretch()
    l4.addLayout(d_row)
    l4.addStretch()
    tabs.addTab(w4, "信用商店")

    # ── Award ──
    w5 = QWidget(); l5 = QVBoxLayout(w5); l5.setSpacing(6)
    at = dts.get("Award", {})
    aw = QCheckBox("领取签到奖励"); aw.setChecked(at.get("award",True)); l5.addWidget(aw)
    ml = QCheckBox("收取邮件"); ml.setChecked(at.get("mail",True)); l5.addWidget(ml)
    fg = QCheckBox("免费单抽 (⚠)"); fg.setChecked(at.get("free_gacha",False)); l5.addWidget(fg)
    oru = QCheckBox("合成玉/矿山"); oru.setChecked(at.get("orundum",True)); l5.addWidget(oru)
    mi_cb = QCheckBox("矿山采集"); mi_cb.setChecked(at.get("mining",False)); l5.addWidget(mi_cb)
    sa = QCheckBox("特殊权限"); sa.setChecked(at.get("special_access",False)); l5.addWidget(sa)
    l5.addStretch()
    tabs.addTab(w5, "领取奖励")

    def _save():
        nd = {
            "Fight": {"use_medicine": use_med.isChecked(), "use_expiring_medicine": exp_med.isChecked(),
                      "times": ts.value(), "stage_reset_mode": srm.currentData(),
                      "enable_times_limit": lc.isChecked(), "hide_unavailable_stage": hu.isChecked(),
                      "medicine_expire_days": med_day.value(),
                      "hide_series": hs.isChecked(), "series": ss.value(),
                      "is_dr_grandet": grandet.isChecked(), "use_expire_medicine_for_activity": act_med.isChecked(),
                      "use_optional_stage": opt.isChecked(), "is_inventory_target": inv.isChecked(),
                      "use_weekly_schedule": wk.isChecked()},
            "Recruit": {"select": [s for s,c in star_cbs.items() if c.isChecked()] or [3,4,5],
                        "confirm": [s for s,c in star_cbs.items() if c.isChecked()] or [3,4,5],
                        "refresh": ref.isChecked(), "force_refresh": fref.isChecked(),
                        "prefer_tag_enabled": pref.isChecked(),
                        "preserve_tag_enabled": pte.isChecked(), "preserve_tags": ptt.text().strip(),
                        "level3_time": lt.value(), "level4_time": lt.value(), "level5_time": lt.value()},
            "Infrast": {"mode": mc.currentData(),
                        "facilities": [f for f,c in fac_cbs.items() if c.isChecked()] or _INFRAST_FACILITIES,
                        "drones": dc.currentData(), "dorm_threshold": ds.value(),
                        "dorm_trust_enabled": dt.isChecked(), "originium_shard_auto": os.isChecked(),
                        "reception_clue": rc.isChecked(), "send_clue": sc.isChecked(),
                        "continue_training": ct.isChecked()},
            "Mall": {"shopping": shop.isChecked(), "blacklist": bl.text().strip(),
                     "credit_fight": cf2.isChecked(), "visit_friends": vf.isChecked(),
                     "first_list": fl.text().strip(), "only_buy_discount": od.isChecked(),
                     "reserve_max_credit": rm.isChecked()},
            "Award": {"award": aw.isChecked(), "mail": ml.isChecked(), "free_gacha": fg.isChecked(),
                      "orundum": oru.isChecked(), "mining": mi_cb.isChecked(), "special_access": sa.isChecked()},
        }
        sg["default_task_settings"] = nd
        mw.config["smart_global"] = sg
        from models.config_manager import save_config
        save_config(mw.config)
        d.accept()

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(_save); bb.rejected.connect(d.reject)
    vl.addWidget(bb)
    d.exec()
