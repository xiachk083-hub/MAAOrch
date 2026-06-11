from __future__ import annotations
from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QWidget, QGroupBox, QCheckBox, QLineEdit,
    QComboBox, QSpinBox, QDialogButtonBox)
from infrastructure.task_constants import TASK_NAMES


_INFRAST_FACILITIES = ["Trade","Mfg","Control","Power","Reception","Office","Dorm","Processing","Training"]
_DRONE_OPTIONS = [("金钱", "Money"), ("无人机", "Drone"), ("战斗记录", "CombatRecord")]


def open_task_config(mw: Any, ac: dict) -> None:
    d = QDialog(mw)
    d.setWindowTitle(f"任务配置 — {ac.get('name','')}")
    d.setMinimumSize(520, 420)
    vl = QVBoxLayout(d)

    tabs = QTabWidget()
    vl.addWidget(tabs)

    ts = dict(ac.get("task_settings", {}))

    # ── 启动游戏 ──
    w1 = QWidget(); l1 = QVBoxLayout(w1); l1.setSpacing(8)
    sw = QLineEdit(ac.get("account_switch", ""))
    sw.setPlaceholderText("输入账号名用于切换（留空=不切换）")
    l1.addWidget(QLabel("切换账号 (空白=不切换):"))
    l1.addWidget(sw)
    l1.addStretch()
    tabs.addTab(w1, "启动游戏")

    # ── 剿灭作战 ──
    w2 = QWidget(); l2 = QVBoxLayout(w2); l2.setSpacing(8)
    l2.addWidget(QLabel("剿灭关卡:"))
    ann = QComboBox()
    anni_map = {"当期剿灭":"Annihilation","切尔诺伯格":"Chernobog@Annihilation",
                "龙门 外环":"LungmenOutskirts@Annihilation","龙门 市区":"LungmenDowntown@Annihilation",
                "多索雷斯":"DossolesHoliday@Annihilation"}
    current_anni = ac.get("smart_annihilation", "Annihilation")
    found = False
    for display, internal in anni_map.items():
        ann.addItem(display, internal)
        if internal == current_anni:
            ann.setCurrentIndex(ann.count() - 1)
            found = True
    if not found:
        ann.addItem(current_anni, current_anni)
        ann.setCurrentIndex(ann.count() - 1)
    l2.addWidget(ann)
    l2.addStretch()
    tabs.addTab(w2, "剿灭作战")

    # ── 刷关作战 ──
    w3 = QWidget(); l3 = QVBoxLayout(w3); l3.setSpacing(6)
    ft = ts.get("Fight", {})
    fs = QLineEdit(ac.get("fight_stage", ""))
    fs.setPlaceholderText("例如: 1-7")
    l3.addWidget(QLabel("默认关卡:")); l3.addWidget(fs)
    # Medicine: two independent checkboxes
    med_row1 = QHBoxLayout()
    use_med = QCheckBox("吃体力药")
    use_med.setChecked(ft.get("use_medicine", False))
    med_row1.addWidget(use_med)
    exp_med = QCheckBox("优先吃快过期药")
    exp_med.setChecked(ft.get("use_expiring_medicine", False))
    med_row1.addWidget(exp_med)
    med_day = QSpinBox(); med_day.setRange(1, 7); med_day.setValue(ft.get("medicine_expire_days", 2))
    med_day.setSuffix(" 天"); med_day.setFixedWidth(65)
    med_row1.addWidget(QLabel("过期:")); med_row1.addWidget(med_day)
    med_row1.addStretch()
    l3.addLayout(med_row1)
    # Stage reset mode + times
    srm_row = QHBoxLayout()
    srm_row.addWidget(QLabel("关卡重置:"))
    srm = QComboBox()
    srm.addItem("当前关卡", "Current"); srm.addItem("上周", "Last"); srm.addItem("昨日", "Yesterday"); srm.addItem("已通关", "Clear")
    ci_srm = srm.findData(ft.get("stage_reset_mode", "Current"))
    if ci_srm >= 0: srm.setCurrentIndex(ci_srm)
    srm_row.addWidget(srm)
    srm_row.addWidget(QLabel("次数:"))
    times_sp = QSpinBox(); times_sp.setRange(1, 999); times_sp.setValue(ft.get("times", 99))
    srm_row.addWidget(times_sp)
    limit_cb = QCheckBox("限制次数"); limit_cb.setChecked(ft.get("enable_times_limit", False))
    srm_row.addWidget(limit_cb)
    srm_row.addStretch()
    l3.addLayout(srm_row)
    # Drop targeting
    drop_row = QHBoxLayout()
    dt_cb = QCheckBox("指定掉落"); dt_cb.setChecked(ft.get("enable_target_drop", False))
    drop_row.addWidget(dt_cb)
    drop_id = QLineEdit(ft.get("drop_id", ""))
    drop_id.setPlaceholderText("材料名")
    drop_id.setEnabled(dt_cb.isChecked()); dt_cb.toggled.connect(lambda c: drop_id.setEnabled(c))
    drop_row.addWidget(drop_id, 1)
    drop_cnt = QSpinBox(); drop_cnt.setRange(0, 999); drop_cnt.setValue(ft.get("drop_count", 0))
    drop_row.addWidget(QLabel("数量:")); drop_row.addWidget(drop_cnt)
    drop_row.addStretch()
    l3.addLayout(drop_row)
    # Special modes
    spec_row = QHBoxLayout()
    grandet_cb = QCheckBox("搓玉模式"); grandet_cb.setChecked(ft.get("is_dr_grandet", False))
    spec_row.addWidget(grandet_cb)
    act_med_cb = QCheckBox("活动用过期药"); act_med_cb.setChecked(ft.get("use_expire_medicine_for_activity", False))
    spec_row.addWidget(act_med_cb)
    opt_cb = QCheckBox("可选关卡"); opt_cb.setChecked(ft.get("use_optional_stage", False))
    spec_row.addWidget(opt_cb)
    inv_cb = QCheckBox("仓库目标"); inv_cb.setChecked(ft.get("is_inventory_target", False))
    spec_row.addWidget(inv_cb)
    wk_cb = QCheckBox("每周计划"); wk_cb.setChecked(ft.get("use_weekly_schedule", False))
    spec_row.addWidget(wk_cb)
    spec_row.addStretch()
    l3.addLayout(spec_row)
    # Series
    ser_row = QHBoxLayout()
    hide_ser_cb = QCheckBox("隐藏系列"); hide_ser_cb.setChecked(ft.get("hide_series", False))
    ser_row.addWidget(hide_ser_cb)
    ser_row.addWidget(QLabel("系列号:"))
    ser_sp = QSpinBox(); ser_sp.setRange(0, 10); ser_sp.setValue(ft.get("series", 0))
    ser_row.addWidget(ser_sp)
    hide_cb = QCheckBox("隐藏不可用关卡"); hide_cb.setChecked(ft.get("hide_unavailable_stage", False))
    ser_row.addWidget(hide_cb)
    ser_row.addStretch()
    l3.addLayout(ser_row)
    l3.addStretch()
    tabs.addTab(w3, "刷关作战")

    # ── 公开招募 ──
    w4 = QWidget(); l4 = QVBoxLayout(w4); l4.setSpacing(6)
    rt = ts.get("Recruit", {})
    sel = rt.get("select", [3, 4, 5])
    l4.addWidget(QLabel("必选星级:"))
    star_row = QHBoxLayout()
    star_cbs = {}
    for s in [3, 4, 5]:
        cb = QCheckBox(f"{s}★")
        cb.setChecked(s in sel)
        star_cbs[s] = cb
        star_row.addWidget(cb)
    star_row.addStretch()
    l4.addLayout(star_row)
    # Refresh options
    ref_row = QHBoxLayout()
    ref_cb = QCheckBox("刷新"); ref_cb.setChecked(rt.get("refresh", True))
    ref_row.addWidget(ref_cb)
    force_ref = QCheckBox("强制刷新"); force_ref.setChecked(rt.get("force_refresh", True))
    ref_row.addWidget(force_ref)
    pref_tag = QCheckBox("优先标签"); pref_tag.setChecked(rt.get("prefer_tag_enabled", True))
    ref_row.addWidget(pref_tag)
    ref_row.addStretch()
    l4.addLayout(ref_row)
    # Preserve tags
    pt_row = QHBoxLayout()
    pt_en = QCheckBox("保留标签"); pt_en.setChecked(rt.get("preserve_tag_enabled", False))
    pt_row.addWidget(pt_en)
    pt_txt = QLineEdit(rt.get("preserve_tags", "支援机械"))
    pt_txt.setPlaceholderText("标签名")
    pt_txt.setEnabled(pt_en.isChecked())
    pt_en.toggled.connect(lambda c: pt_txt.setEnabled(c))
    pt_row.addWidget(pt_txt, 1)
    # Level time
    lt_sp = QSpinBox(); lt_sp.setRange(60, 1440); lt_sp.setValue(rt.get("level3_time", 540))
    lt_sp.setSuffix(" 分")
    pt_row.addWidget(QLabel("3★时间:")); pt_row.addWidget(lt_sp)
    pt_row.addStretch()
    l4.addLayout(pt_row)
    l4.addStretch()
    tabs.addTab(w4, "公开招募")

    # ── 基建换班 ──
    w5 = QWidget(); l5 = QVBoxLayout(w5); l5.setSpacing(6)
    it = ts.get("Infrast", {})
    mode_cb = QComboBox()
    mode_cb.addItem("常规模式", "Normal"); mode_cb.addItem("队列轮换", "Rotation")
    mi = mode_cb.findData(it.get("mode", "Normal"))
    if mi >= 0: mode_cb.setCurrentIndex(mi)
    l5.addWidget(QLabel("模式:"));
    l5.addWidget(mode_cb)
    # Facilities
    fac_row = QHBoxLayout()
    fac_cbs = {}
    current_fac = it.get("facilities", _INFRAST_FACILITIES)
    for f in _INFRAST_FACILITIES:
        cb = QCheckBox(f)
        cb.setChecked(f in current_fac)
        fac_cbs[f] = cb
        fac_row.addWidget(cb)
    fac_row.addStretch()
    l5.addWidget(QLabel("设施:")); l5.addLayout(fac_row)
    # Drones
    drone_row = QHBoxLayout()
    drone_row.addWidget(QLabel("无人机:"))
    drone_cb = QComboBox()
    for txt, val in _DRONE_OPTIONS:
        drone_cb.addItem(txt, val)
    di = drone_cb.findData(it.get("drones", "Money"))
    if di >= 0: drone_cb.setCurrentIndex(di)
    drone_row.addWidget(drone_cb)
    dorm_sp = QSpinBox(); dorm_sp.setRange(0, 100); dorm_sp.setValue(it.get("dorm_threshold", 30))
    dorm_sp.setSuffix(" %")
    drone_row.addWidget(QLabel("宿舍阈值:")); drone_row.addWidget(dorm_sp)
    drone_row.addStretch()
    l5.addLayout(drone_row)
    # Checkboxes
    infra_row = QHBoxLayout()
    dt_cb = QCheckBox("宿舍信任"); dt_cb.setChecked(it.get("dorm_trust_enabled", True))
    infra_row.addWidget(dt_cb)
    dfs_cb = QCheckBox("过滤未进驻"); dfs_cb.setChecked(it.get("dorm_filter_not_stationed", True))
    infra_row.addWidget(dfs_cb)
    os_cb = QCheckBox("自动搓玉"); os_cb.setChecked(it.get("originium_shard_auto", True))
    infra_row.addWidget(os_cb)
    rmb_cb = QCheckBox("会客留言板"); rmb_cb.setChecked(it.get("reception_message_board", True))
    infra_row.addWidget(rmb_cb)
    rc_cb = QCheckBox("会客线索"); rc_cb.setChecked(it.get("reception_clue", True))
    infra_row.addWidget(rc_cb)
    sc_cb = QCheckBox("送线索"); sc_cb.setChecked(it.get("send_clue", True))
    infra_row.addWidget(sc_cb)
    ct_cb = QCheckBox("继续训练"); ct_cb.setChecked(it.get("continue_training", False))
    infra_row.addWidget(ct_cb)
    infra_row.addStretch()
    l5.addLayout(infra_row)
    l5.addStretch()
    tabs.addTab(w5, "基建换班")

    # ── 信用商店 ──
    w6 = QWidget(); l6 = QVBoxLayout(w6); l6.setSpacing(6)
    mt = ts.get("Mall", {})
    shop_cb = QCheckBox("信用购物"); shop_cb.setChecked(mt.get("shopping", True))
    l6.addWidget(shop_cb)
    bl_row = QHBoxLayout()
    bl_row.addWidget(QLabel("黑名单:"))
    bl_txt = QLineEdit(mt.get("blacklist", "碳;家具;加急许可"))
    bl_txt.setPlaceholderText("用;分隔")
    bl_row.addWidget(bl_txt, 1)
    l6.addLayout(bl_row)
    mall_row = QHBoxLayout()
    cf_cb = QCheckBox("信用战斗"); cf_cb.setChecked(mt.get("credit_fight", False))
    mall_row.addWidget(cf_cb)
    vf_cb = QCheckBox("访问好友"); vf_cb.setChecked(mt.get("visit_friends", True))
    mall_row.addWidget(vf_cb)
    fl_txt = QLineEdit(mt.get("first_list", "招聘许可"))
    fl_txt.setPlaceholderText("优先购买")
    fl_txt.setFixedWidth(100)
    mall_row.addWidget(QLabel("优先:")); mall_row.addWidget(fl_txt)
    mall_row.addStretch()
    l6.addLayout(mall_row)
    disc_row = QHBoxLayout()
    od_cb = QCheckBox("仅买折扣"); od_cb.setChecked(mt.get("only_buy_discount", False))
    disc_row.addWidget(od_cb)
    rm_cb = QCheckBox("保留信用点"); rm_cb.setChecked(mt.get("reserve_max_credit", False))
    disc_row.addWidget(rm_cb)
    sib_cb = QCheckBox("满库存忽略黑名单"); sib_cb.setChecked(mt.get("shopping_ignore_blacklist", False))
    disc_row.addWidget(sib_cb)
    disc_row.addStretch()
    l6.addLayout(disc_row)
    freq_row = QHBoxLayout()
    cfo_cb = QCheckBox("每日信用战"); cfo_cb.setChecked(mt.get("credit_fight_once_a_day", True))
    freq_row.addWidget(cfo_cb)
    vfo_cb = QCheckBox("每日访问好友"); vfo_cb.setChecked(mt.get("visit_friends_once_a_day", False))
    freq_row.addWidget(vfo_cb)
    freq_row.addStretch()
    l6.addLayout(freq_row)
    l6.addStretch()
    tabs.addTab(w6, "信用商店")

    # ── 领取奖励 ──
    w7 = QWidget(); l7 = QVBoxLayout(w7); l7.setSpacing(6)
    at = ts.get("Award", {})
    aw = QCheckBox("领取签到奖励"); aw.setChecked(at.get("award", True)); l7.addWidget(aw)
    ml = QCheckBox("收取邮件"); ml.setChecked(at.get("mail", True)); l7.addWidget(ml)
    fg = QCheckBox("免费单抽 (⚠ 自动抽卡)")
    fg.setChecked(at.get("free_gacha", False)); l7.addWidget(fg)
    oru = QCheckBox("合成玉/矿山"); oru.setChecked(at.get("orundum", True)); l7.addWidget(oru)
    mi_cb = QCheckBox("矿山采集"); mi_cb.setChecked(at.get("mining", False)); l7.addWidget(mi_cb)
    sa_cb = QCheckBox("特殊权限"); sa_cb.setChecked(at.get("special_access", False)); l7.addWidget(sa_cb)
    l7.addStretch()
    tabs.addTab(w7, "领取奖励")

    # ── 肉鸽探索 ──
    w8 = QWidget(); l8 = QVBoxLayout(w8); l8.setSpacing(6)
    rt = ts.get("Roguelike", {})
    theme_row = QHBoxLayout()
    theme_row.addWidget(QLabel("主题:"))
    rl_theme = QComboBox()
    for txt, val in [("傀影", "Phantom"), ("水月", "Mizuki"), ("萨米", "Sami"), ("萨卡兹", "Sarkaz"), ("界园", "JieGarden")]:
        rl_theme.addItem(txt, val)
    ci = rl_theme.findData(rt.get("theme", "Sarkaz"))
    if ci >= 0: rl_theme.setCurrentIndex(ci)
    theme_row.addWidget(rl_theme)
    theme_row.addWidget(QLabel("难度:"))
    rl_diff = QSpinBox(); rl_diff.setRange(0, 15); rl_diff.setValue(rt.get("difficulty", 15))
    theme_row.addWidget(rl_diff)
    theme_row.addWidget(QLabel("模式:"))
    rl_mode = QComboBox()
    rl_mode.addItem("经验", "Exp"); rl_mode.addItem("投资", "Investment")
    ci = rl_mode.findData(rt.get("mode", "Exp"))
    if ci >= 0: rl_mode.setCurrentIndex(ci)
    theme_row.addWidget(rl_mode)
    theme_row.addStretch()
    l8.addLayout(theme_row)
    squad_row = QHBoxLayout()
    squad_row.addWidget(QLabel("分队:"))
    rl_squad = QLineEdit(rt.get("squad", "")); rl_squad.setPlaceholderText("分队名")
    squad_row.addWidget(rl_squad)
    squad_row.addWidget(QLabel("职业:"))
    rl_roles = QLineEdit(rt.get("roles", "")); rl_roles.setPlaceholderText("术师/近卫...")
    squad_row.addWidget(rl_roles)
    squad_row.addWidget(QLabel("核心干员:"))
    rl_core = QLineEdit(rt.get("core_char", "")); rl_core.setPlaceholderText("干员名")
    squad_row.addWidget(rl_core)
    squad_row.addStretch()
    l8.addLayout(squad_row)
    inv_row = QHBoxLayout()
    rl_inv = QCheckBox("投资"); rl_inv.setChecked(rt.get("investment", True))
    inv_row.addWidget(rl_inv)
    rl_inv_cnt = QSpinBox(); rl_inv_cnt.setRange(0, 9999); rl_inv_cnt.setValue(rt.get("invest_count", 999))
    inv_row.addWidget(QLabel("投资次数:")); inv_row.addWidget(rl_inv_cnt)
    rl_stop_max = QCheckBox("满级停"); rl_stop_max.setChecked(rt.get("stop_when_level_max", False))
    inv_row.addWidget(rl_stop_max)
    rl_stop_dep = QCheckBox("满投资停"); rl_stop_dep.setChecked(rt.get("stop_when_deposit_full", False))
    inv_row.addWidget(rl_stop_dep)
    rl_stop_boss = QCheckBox("止步Boss"); rl_stop_boss.setChecked(rt.get("stop_at_final_boss", False))
    inv_row.addWidget(rl_stop_boss)
    inv_row.addStretch()
    l8.addLayout(inv_row)
    adv_row = QHBoxLayout()
    rl_use_sup = QCheckBox("借干员"); rl_use_sup.setChecked(rt.get("use_support", False))
    adv_row.addWidget(rl_use_sup)
    rl_seed_en = QCheckBox("固定种子"); rl_seed_en.setChecked(rt.get("start_with_seed", False))
    adv_row.addWidget(rl_seed_en)
    rl_seed = QLineEdit(rt.get("seed", "")); rl_seed.setPlaceholderText("种子号")
    rl_seed.setEnabled(rl_seed_en.isChecked()); rl_seed_en.toggled.connect(lambda c: rl_seed.setEnabled(c))
    adv_row.addWidget(rl_seed)
    adv_row.addStretch()
    l8.addLayout(adv_row)
    l8.addStretch()
    tabs.addTab(w8, "肉鸽探索")

    # ── 生息演算 ──
    w9 = QWidget(); l9 = QVBoxLayout(w9); l9.setSpacing(6)
    rct = ts.get("Reclamation", {})
    rc_theme_row = QHBoxLayout()
    rc_theme_row.addWidget(QLabel("主题:"))
    rc_theme = QComboBox()
    rc_theme.addItem("沙洲遗闻", "Tales"); rc_theme.addItem("火蓝之心", "FireBlue")
    ci = rc_theme.findData(rct.get("theme", "Tales"))
    if ci >= 0: rc_theme.setCurrentIndex(ci)
    rc_theme_row.addWidget(rc_theme)
    rc_theme_row.addWidget(QLabel("模式:"))
    rc_mode = QComboBox()
    rc_mode.addItem("存档发展", "ProsperityInSave"); rc_mode.addItem("无存档", "ProsperityNoSave")
    ci = rc_mode.findData(rct.get("mode", "ProsperityInSave"))
    if ci >= 0: rc_mode.setCurrentIndex(ci)
    rc_theme_row.addWidget(rc_mode)
    rc_theme_row.addStretch()
    l9.addLayout(rc_theme_row)
    rc_craft_row = QHBoxLayout()
    rc_craft_row.addWidget(QLabel("打造工具:"))
    rc_tool = QLineEdit(rct.get("tool_to_craft", "")); rc_tool.setPlaceholderText("工具名")
    rc_craft_row.addWidget(rc_tool)
    rc_craft_row.addWidget(QLabel("每轮最大:"))
    rc_craft_cnt = QSpinBox(); rc_craft_cnt.setRange(1, 99); rc_craft_cnt.setValue(rct.get("max_craft_count", 16))
    rc_craft_row.addWidget(rc_craft_cnt)
    rc_clr = QCheckBox("清理仓库"); rc_clr.setChecked(rct.get("clear_store", False))
    rc_craft_row.addWidget(rc_clr)
    rc_craft_row.addStretch()
    l9.addLayout(rc_craft_row)
    l9.addStretch()
    tabs.addTab(w9, "生息演算")

    # ── Buttons ──
    def _save():
        ac["account_switch"] = sw.text().strip()
        ac["fight_stage"] = fs.text().strip()
        ac["smart_annihilation"] = ann.currentData() or "Annihilation"
        new_ts = {
            "Fight": {"use_medicine": use_med.isChecked(), "use_expiring_medicine": exp_med.isChecked(),
                      "times": times_sp.value(), "stage_reset_mode": srm.currentData(),
                      "enable_times_limit": limit_cb.isChecked(), "hide_unavailable_stage": hide_cb.isChecked(),
                      "medicine_expire_days": med_day.value(),
                      "enable_target_drop": dt_cb.isChecked(), "drop_id": drop_id.text().strip(),
                      "drop_count": drop_cnt.value(), "is_dr_grandet": grandet_cb.isChecked(),
                      "use_expire_medicine_for_activity": act_med_cb.isChecked(),
                      "use_optional_stage": opt_cb.isChecked(), "is_inventory_target": inv_cb.isChecked(),
                      "use_weekly_schedule": wk_cb.isChecked(), "hide_series": hide_ser_cb.isChecked(),
                      "series": ser_sp.value()},
            "Recruit": {"select": [s for s, c in star_cbs.items() if c.isChecked()] or [3, 4, 5],
                        "confirm": [s for s, c in star_cbs.items() if c.isChecked()] or [3, 4, 5],
                        "refresh": ref_cb.isChecked(), "force_refresh": force_ref.isChecked(),
                        "prefer_tag_enabled": pref_tag.isChecked(),
                        "preserve_tag_enabled": pt_en.isChecked(), "preserve_tags": pt_txt.text().strip(),
                        "level3_time": lt_sp.value(), "level4_time": lt_sp.value(), "level5_time": lt_sp.value()},
            "Infrast": {"mode": mode_cb.currentData(),
                        "facilities": [f for f, c in fac_cbs.items() if c.isChecked()] or _INFRAST_FACILITIES,
                        "drones": drone_cb.currentData(), "dorm_threshold": dorm_sp.value(),
                        "dorm_trust_enabled": dt_cb.isChecked(), "dorm_filter_not_stationed": dfs_cb.isChecked(),
                        "originium_shard_auto": os_cb.isChecked(),
                        "reception_message_board": rmb_cb.isChecked(),
                        "reception_clue": rc_cb.isChecked(), "send_clue": sc_cb.isChecked(),
                        "continue_training": ct_cb.isChecked()},
            "Mall": {"shopping": shop_cb.isChecked(), "blacklist": bl_txt.text().strip(),
                     "credit_fight": cf_cb.isChecked(), "visit_friends": vf_cb.isChecked(),
                     "first_list": fl_txt.text().strip(), "only_buy_discount": od_cb.isChecked(),
                     "reserve_max_credit": rm_cb.isChecked(),
                     "shopping_ignore_blacklist": sib_cb.isChecked(),
                     "credit_fight_once_a_day": cfo_cb.isChecked(),
                     "visit_friends_once_a_day": vfo_cb.isChecked()},
            "Award": {"award": aw.isChecked(), "mail": ml.isChecked(), "free_gacha": fg.isChecked(),
                      "orundum": oru.isChecked(), "mining": mi_cb.isChecked(), "special_access": sa_cb.isChecked()},
            "Roguelike": {"theme": rl_theme.currentData(), "mode": rl_mode.currentData(),
                          "difficulty": rl_diff.value(), "squad": rl_squad.text().strip(),
                          "roles": rl_roles.text().strip(), "core_char": rl_core.text().strip(),
                          "investment": rl_inv.isChecked(), "invest_count": rl_inv_cnt.value(),
                          "stop_when_level_max": rl_stop_max.isChecked(),
                          "stop_when_deposit_full": rl_stop_dep.isChecked(),
                          "stop_at_final_boss": rl_stop_boss.isChecked(),
                          "use_support": rl_use_sup.isChecked(),
                          "start_with_seed": rl_seed_en.isChecked(), "seed": rl_seed.text().strip()},
            "Reclamation": {"theme": rc_theme.currentData(), "mode": rc_mode.currentData(),
                            "tool_to_craft": rc_tool.text().strip(),
                            "max_craft_count": rc_craft_cnt.value(), "clear_store": rc_clr.isChecked()},
        }
        ac["task_settings"] = new_ts
        progs = [w for w in mw.warehouse if w.get("account_ref") == ac.get("id","")]
        if progs:
            progs[0]["sync_tasks"] = True
        mw._save()
        d.accept()

    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(_save); bb.rejected.connect(d.reject)
    vl.addWidget(bb)
    d.exec()
