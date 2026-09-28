#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日刷取建议（只建议不执行）—— 活动期 + 仓库缺口 + 理智 → 今日各号建议。

输入（全部本地只读）：
  runtime/reports/server_events.json          各服活动快照（活动期/在期刷关活动；MAA 表认否）
  runtime/reports/stage_capability.json       账号×关卡 可刷能力台账（success/failed/unknown）
  runtime/reports/accounts/<账号>/{operators,depot}.json  上号识别产物（有 depot 才做「缺口」决策）
  runtime/reports/mas_account_master_*.json   账号名册（最新一份；服/脚本/现计划/启用态）
  runtime/reports/account_health_*.json       （可选）理智溢出/浪费 + 健康 advice（X5 卡产物；缺省→理智未知）
  config/material_water_levels.json           17 材料水位 + 每日 AP 预算
  config/farm_stages.json                     公开掉率表（企鹅物流；无识别数据时的效率兜底）
  data/stage_apcost_v4.json                   关卡计价/类型（MAIN/DAILY/ACTIVITY；判定下线/恒开放）

输出（仅此两处写盘；`--no-write` 可只打印）：
  runtime/reports/daily_advice_<yyyyMMdd>.md     人读（逐号一行）
  runtime/reports/daily_advice_<yyyyMMdd>.json   机读（逐号一行 + 规则引用 + 摘要）

约束：只读 + 写报告。不生成、不改任何 AUTO-MAS/MAA 配置；不发车；不碰 REST/SSH。
执行路径（需用户/lead 批准才做）：`mas_plan_builder.py`（草案）→ `mas_run_profile.py`（发车）。

用法：
  python tools/mas_daily_advice.py                     # 生成当日报告
  python tools/mas_daily_advice.py --date 2026-09-28   # 指定日期
  python tools/mas_daily_advice.py --no-write          # 只打印 md（不写盘）
  python tools/mas_daily_advice.py --health-file <path>  # 指定 account_health JSON
  python tools/mas_daily_advice.py --sanity <账号>=<N>   # 手工理智（可重复）
  python tools/mas_daily_advice.py --selftest          # 离线自测（零触网、零写 runtime）
  python tools/mas_daily_advice.py --json              # 机读单行摘要
退出码：0 成功；2 拒绝（关键输入缺失）；3 selftest 失败；1 未预期错误。
环境变量：MAAORCH_DAILY_ADVICE_ROOT / MAAORCH_DAILY_ADVICE_RUNTIME（隔离/自测用）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import mas_plan_builder as pb  # noqa: E402  复用其装载/台账/候选/活动/理智的定稿逻辑

DEFAULT_RUNTIME = pb.DEFAULT_RUNTIME
TS_FMT = "%Y-%m-%d %H:%M:%S"
SCHEMA = "maaorch.daily_advice/1"

SERVER_SHORT = {"cn_official": "官", "cn_bilibili": "B", "jp": "日", "kr": "韩", "tw": "台", "en": "国际"}
PROBE_UNKNOWN = "unknown ⇒ 需先试探一次"
ROGUE_PREFIX = "l-"

# 规则表（R#）—— 逐号「依据」列引用；措辞即口径，别在别处另造口径
RULES = {
    "R1": "输入与有效性：活动快照 >3 天告警、>7 天拒用（活动判断关闭，维持现值）；台账/名册/识别产物的时间戳见 §3。",
    "R2a": "目标决策（有识别数据）：缺口 = max(0, 水位−仓库)（config/material_water_levels.json vs depot.json）；"
           "按「缺口折算 AP」降序取主材料，候选关取 farm_stages.json 里 AP/个 升序（台账 failed 剔除）。",
    "R2b": "目标决策（无识别数据）：按公开关卡效率表兜底 —— 常驻兜底 1-7（恒开放、全号已证）；"
           "该服有在期刷关活动且 MAA 表认（server_events）→ 建议活动关。",
    "R3": "现配置口径：读名册 plan 的「关/剩」（主关 + 剩余理智回退位）；主关为 ACTIVITY 类型（data/stage_apcost_v4.json）"
          "且该服当前无在期刷关活动时，视为已随活动下线 → 采回退位（无回退位则 1-7）。",
    "R4": "台账闸门（runtime/reports/stage_capability.json）：success=已证；failed=不排（换候选）；"
          "unknown（含不在台账关池）=需先试探一次（MAA 对『代理锁定』的关是静默跳过）。",
    "R5": "活动（server_events.json）：有在期刷关活动 + MAA 表认（maa_stage_activity.has_active_stage_activity）"
          "⇒ 建议活动优先；快照过期/缺失时活动判断关闭。",
    "R6": "理智/次数：每日 AP 预算 = material_water_levels.json 的 ap_budget_per_day（现状 280）；次数估算 = 预算÷关卡 AP；"
          "实际停刷靠理智耗尽（AUTO-MAS 两层无总次数字段）。account_health 缺失 ⇒ 理智未知，只给「到理智耗尽」口径；"
          "有 `advice` 时原样转述进「依据」（X5 口径：等自愈=勿补跑、定因后补跑=先试探）。",
    "R7": "药：默认不吃（MedicineNumb=0）；临期药不单独控制（AUTO-MAS 无 UseExpiringMedicine 字段，模板 true/2 天自动）。",
    "R8": "停用号：不产车，不给刷取建议（本报告仅存档一行）。",
    "R9": "肉鸽线（l-*）：另线口径（docs/MAA_ROGUELIKE_PLAYBOOK.md），不按日常刷关建议。",
    "R10": "本报告只建议：不生成、不改任何配置、不发车；要执行走 mas_plan_builder → mas_run_profile（需批准）。",
}


class Paths(pb.Paths):
    """本工具用到的路径集合（复用 pb.Paths，只加了 health 通配）。"""

    @property
    def reports_dir(self):
        return os.path.join(self.runtime, "reports")

    def health_paths(self) -> list:
        d = os.path.join(self.runtime, "reports")
        if not os.path.isdir(d):
            return []
        return [os.path.join(d, n) for n in sorted(os.listdir(d))
                if n.startswith("account_health_") and n.endswith(".json")]


# ---------------------------------------------------------------- 装载
def load_master(paths: Paths, override: str = "") -> dict:
    if override:
        p = override if os.path.isabs(override) else os.path.join(paths.root, override)
    else:
        cands = paths.master_paths()
        if not cands:
            raise pb.Refuse("账号名册缺失：%s/reports/mas_account_master_*.json 不在 —— "
                            "先刷一次名册（资产普查口径），或 --master 指定。" % paths.runtime)
        p = cands[-1]
    doc = pb.load_json(p)
    if not isinstance(doc, dict) or not doc.get("accounts"):
        raise pb.Refuse("账号名册不可读/为空：%s" % p)
    return {"path": p, "doc": doc, "generated_at": str(doc.get("generated_at") or "")}


def load_health(paths: Paths, override: str = "") -> dict:
    """（可选）读 account_health_*.json：理智/理智溢出。宽容解析（schema 未定稿，X5 卡产物）。"""
    p = override or (paths.health_paths()[-1] if paths.health_paths() else "")
    if not p or not os.path.exists(p):
        return {"available": False, "path": "", "accounts": {}, "generated_at": "",
                "note": "account_health 产物缺失（X5/t_61b187a9 未产出）→ 理智未知，按「直到理智耗尽」给建议"}
    doc = pb.load_json(p)
    accs, n_raw = {}, 0
    src = (doc or {}).get("accounts") if isinstance(doc, dict) else None
    if isinstance(src, dict):
        items = [(k, v) for k, v in src.items()]
    elif isinstance(src, list):
        items = [((v.get("account") or v.get("name") or v.get("game_name_cfg")), v)
                 for v in src if isinstance(v, dict)]
    else:
        items = []
    for name, a in items:
        if not name or not isinstance(a, dict):
            continue
        n_raw += 1
        rec = {}
        for k in ("sanity_overflow", "overflow", "wasted_sanity", "sanity_waste_est", "sanity_waste",
                  "overflow_est", "lost_points"):
            v = a.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                rec["overflow"] = int(v)
                break
        for k in ("sanity_last", "sanity", "current_sanity", "last_sanity", "sanity_now"):
            v = a.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                rec["sanity"] = int(v)
                break
        for k in ("state", "hours_full", "hours_since_last_run", "score"):
            if a.get(k) is not None:
                rec[k] = a[k]
        if a.get("flags"):
            rec["flags"] = [str(f) for f in a["flags"] if str(f)]
        av = a.get("advice")
        if av:
            if not isinstance(av, list):
                av = [av]
            av = [str(x) for x in av if str(x)]
            if av:
                rec["advice"] = av[:3]
        if a.get("sanity_full_at"):
            rec["sanity_full_at"] = str(a["sanity_full_at"])
        if rec:
            accs[str(name)] = rec
    ok = bool(accs)
    return {"available": ok, "path": p, "accounts": accs, "raw_n": n_raw,
            "generated_at": str((doc or {}).get("generated_at") or "") if isinstance(doc, dict) else "",
            "note": "" if ok else "account_health 文件在（%s）但没解析出逐号理智字段（schema 待对齐）" % os.path.basename(p)}


def parse_plan(s: str) -> dict:
    """名册 plan 串 → {main, remain, med, mode}。形如 '关:SR-7 / 剩:1-7 / 药:0 / 连战:0 / 模式:Fixed'。"""
    out = {"main": "", "remain": "", "med": "", "mode": "", "raw": s or ""}
    for part in (s or "").split("/"):
        part = part.strip()
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k, v = k.strip(), v.strip()
        if k == "关":
            out["main"] = v
        elif k == "剩":
            out["remain"] = v
        elif k == "药":
            out["med"] = v
        elif k == "模式":
            out["mode"] = v
    return out


def _norm_stage(s: str) -> str:
    s = str(s or "").strip()
    return "" if s in ("-", "—", "None") else s


def effective_stage(main: str, remain: str, ev: dict, apcost: dict) -> tuple:
    """现配置的「当日有效」关：主关 → 若 ACTIVITY 且该服无在期刷关活动则走回退位 → 1-7。"""
    main, remain = _norm_stage(main), _norm_stage(remain)
    if not main:
        return (remain or "1-7"), "主关空"
    ax = pb.apcost_lookup(apcost, main)
    if ax.get("type") == "ACTIVITY" and not ev.get("farming"):
        return (remain or "1-7"), "%s 已下线" % main
    return main, ""


def stage_gate(cap, account: str, stage: str) -> dict:
    info = cap.get(account, stage)
    return {"status": info["status"], "probe": info["status"] != "success",
            "note": pb.cap_note(info), "last_success": info.get("last_success") or ""}


def _per_day(budget: float, ap: float):
    try:
        ap = float(ap)
    except (TypeError, ValueError):
        return None
    return int(budget // ap) if ap > 0 else None


def _waste_ap(health: dict):
    """溢出点数（account_health 口径 lost_points；兼容 overflow；缺→'?'）。"""
    v = health.get("lost_points")
    if v is None:
        v = health.get("overflow")
    return v if v is not None else "?"


def r5_short(ev: dict) -> str:
    """R5 的短句（别把整段 verdict 塞进表格）。"""
    if not ev.get("available"):
        return "活动快照缺失（判断关闭）"
    if ev.get("stale"):
        return "活动快照过期（判断关闭）"
    if ev.get("farming"):
        return "在期刷关活动 %d 个" % len(ev["farming"])
    return "无在期刷关活动"


# ---------------------------------------------------------------- 决策
def decide_account(acc: dict, ctx: dict, now: dt.datetime) -> dict:
    name = str(acc.get("game_name_cfg") or acc.get("notes") or "").strip()
    row = {
        "account": name, "script": str(acc.get("script") or ""), "server": str(acc.get("server") or ""),
        "idx": acc.get("idx"), "maadir": str(acc.get("maadir") or ""), "enabled": bool(acc.get("enabled", True)),
    }
    row["server_key"] = pb.server_key_of(row["server"])
    row["server_short"] = SERVER_SHORT.get(row["server_key"], row["server_key"] or "?")
    cur = parse_plan(acc.get("plan") or "")
    row["current"] = {
        "stage": _norm_stage(cur["main"]), "remain": _norm_stage(cur["remain"]), "med": cur["med"],
        "mode": cur["mode"], "plan_table": bool(cur["mode"] and cur["mode"] != "Fixed"), "raw": cur["raw"],
    }
    if not row["enabled"]:
        row.update(kind="na", advice_stage="—", advice_code="", runs="—", medicine="—", probe="—",
                   why="R8 停用（不产车）", rule_refs=["R8"])
        return row
    if str(row["script"]).startswith(ROGUE_PREFIX):
        row.update(kind="na", advice_stage="—", advice_code="", runs="—", medicine="—", probe="—",
                   why="R9 肉鸽机队（l-*）另线：日常刷取不产车", rule_refs=["R9"])
        return row

    ev = ctx["events"].get(row["server_key"]) or {}
    budget = float(ctx["water"].get("ap_budget_per_day") or 280)
    farm_ok = bool(ev.get("farming")) and not ev.get("stale")
    maa_ok = bool((ev.get("maa_stage_activity") or {}).get("has_active_stage_activity")) and not ev.get("stale")
    eff, eff_why = effective_stage(row["current"]["stage"], row["current"]["remain"], ev, ctx["apcost"])
    row["current"]["effective"] = eff
    row["current"]["effective_why"] = eff_why
    health = ctx["health"]["accounts"].get(name) or {}
    if health:
        row["health"] = {k: health[k] for k in ("state", "sanity", "overflow", "hours_full",
                                                "hours_since_last_run", "flags", "advice") if k in health}
    sanity = health.get("sanity")
    if name in ctx["sanity_over"]:
        sanity = ctx["sanity_over"][name]
    hbits = []
    if health.get("state") == "full":
        hbits.append("理智已满%s（溢出≈%s AP）" % (
            (" %sh" % health["hours_full"]) if health.get("hours_full") is not None else "",
            _waste_ap(health)))
    elif sanity:
        hbits.append("上次记录理智 %s" % sanity)
    hb = []
    if health.get("advice"):
        hb.append("；".join(str(x) for x in (health["advice"] or [])[:1]))
    if health.get("flags"):
        hb.append("、".join(str(f) for f in (health["flags"] or [])[:3]))
    if hb:
        hbits.append("健康:" + "；".join(hb))

    products = ctx["products"].get(name)
    rule_refs: list = []

    if products is not None:
        # -------- R2a：有识别数据 → 缺口决策（口径同 mas_plan_builder）
        try:
            targets = pb.compute_targets(products["depot"].get("items") or {}, ctx["water"])
        except pb.Refuse:
            targets = []
        gap_mode = any(t["gap"] > 0 for t in targets)
        top_obj = None
        if not gap_mode and targets:
            topping = pb.topping_choice(targets)
            targets = [dict(topping, topping=True)]
        queue = pb.pick_queue(name, targets, ctx["cap"], ctx["farm"], ctx["apcost"]) if targets else []
        top = queue[0] if queue else None
        best = (top or {}).get("analyzed", {}).get("best") if top else None
        if best and not top.get("no_table"):
            stage = best["stage"]
            gate = stage_gate(ctx["cap"], name, stage)
            rule_refs = ["R2a", "R4", "R6", "R7"]
            if ev.get("available"):
                rule_refs.append("R5")
            per_day = _per_day(budget, best["ap"])
            if gap_mode and top.get("runs"):
                runs = "补%s缺口 %s ≈%s 次 · 其余到理智耗尽" % (top["material"], top["gap"], top["runs"])
            else:
                runs = "到理智耗尽（≈%s 次/天）" % per_day if per_day else "到理智耗尽"
            if health.get("state") == "full":
                runs = "理智已满 · 溢出≈%s AP · %s" % (_waste_ap(health), runs)
            elif sanity and best.get("ap"):
                runs = "本次 ≈%d 次（理智 %d）· " % (int(sanity) // int(best["ap"]), int(sanity)) + runs
                if health.get("overflow"):
                    runs += " · 溢出≈%d AP" % int(health["overflow"])
            alts = [c["stage"] for c in top["analyzed"]["candidates"] if c["stage"] != stage][:2] + ["1-7"]
            why = "R2a %s缺口 %s（%s<%s）· R4 %s · R5 %s · 回退 %s" % (
                top["material"], top["gap"], top.get("owned"), top.get("target"), gate["note"],
                r5_short(ev), "→".join(alts))
            if hbits:
                why += " · " + " · ".join(hbits)
            row.update(
                kind="change" if stage != eff else "maintain",
                advice_stage=("改 " + stage) if stage != eff else ("维持 " + stage),
                advice_code=stage,
                runs=runs,
                medicine="否（0）",
                probe=("否（台账已证）" if not gate["probe"] else
                       ("是（%s 台账 failed）" % stage if gate["status"] == "failed" else "是（%s 未证）" % stage)),
                why=why,
                rule_refs=rule_refs,
            )
            row["advice_fallbacks"] = alts
            return row
        # 没有可评估候选（掉率表里没这个材料）→ 走兜底维护
        eff_why = (eff_why + "；缺口队列为空/无候选表").lstrip("；")

    # -------- R2b：无识别数据（或缺口队列不可用）→ 公开效率表兜底
    if products is None and farm_ok and maa_ok and not row["current"]["plan_table"]:
        ev_name, ev_end = "", ""
        if ev.get("farming"):
            ev_name = str(ev["farming"][0].get("name") or "")
            ev_end = str(ev["farming"][0].get("end") or "")
        stage_txt = "活动关（PA-6/7/8）"
        per_day = _per_day(budget, 21)
        why = "R2b 无识别数据→效率表兜底 · R5 在期刷关活动（%s 至 %s）· R4 活动关未证 · 兜底 1-7" % (
            ev_name or "SideStory", ev_end or "?")
        if hbits:
            why += " · " + " · ".join(hbits)
        row.update(
            kind="activity", advice_stage=stage_txt, advice_code="PA-6/7/8",
            runs="到理智耗尽（≈%s 次/天 @21AP）" % per_day if per_day else "到理智耗尽",
            medicine="否（0）", probe=PROBE_UNKNOWN,
            why=why,
            rule_refs=["R2b", "R4", "R5", "R6", "R7"],
        )
        return row

    # 常驻兜底：维持「现配置有效关」
    gate = stage_gate(ctx["cap"], name, eff) if eff else {"status": "unknown", "note": ""}
    count_ref = {"success": "已证 %s" % (gate.get("last_success") or "?"),
                 "failed": "⚠failed（%s）" % gate["note"], "unknown": "未证"}[gate["status"]]
    per_day = None
    ax = pb.apcost_lookup(ctx["apcost"], eff) if eff else {}
    if ax.get("ap"):
        per_day = _per_day(budget, ax["ap"])
    cur_stage = row["current"]["stage"]
    plan_table = row["current"]["plan_table"]
    if eff == cur_stage and cur_stage:
        stage_txt = "维持 " + eff + ("（计划表）" if plan_table else "")
    elif plan_table and not cur_stage:
        stage_txt = "维持 %s（主关空·计划表）" % eff
    else:
        stage_txt = "维持 %s（%s）" % (eff, (eff_why + "→回退") if eff_why else "回退")
    runs = "到理智耗尽（≈%s 次/天）" % per_day if per_day else "到理智耗尽"
    if health.get("state") == "full":
        runs = "理智已满 · 溢出≈%s AP · %s" % (_waste_ap(health), runs)
    elif sanity and ax.get("ap"):
        runs = "本次 ≈%d 次（理智 %d）· " % (int(sanity) // int(ax["ap"]), int(sanity)) + runs
    why_bits = ["R2a 缺口队列无候选（掉率表缺）→ 兜底维护" if products is not None
                else "R2b 无识别数据（效率表兜底）"]
    if eff != cur_stage or eff_why:
        why_bits.append("R3 %s→%s" % (cur_stage or "主关空", eff))
    else:
        why_bits.append("R3 主关=%s（恒开放）" % eff)
    if plan_table:
        why_bits.append("R3 计划表模式（周轮换，不擅改）")
    why_bits.append("R4 %s" % count_ref)
    if ev.get("available"):
        why_bits.append("R5 %s" % r5_short(ev))
    if plan_table and farm_ok and maa_ok:
        why_bits.append("R5 活动在期（可选：改计划表；未建议）")
    why_bits.extend(hbits)
    rule_refs = ["R2a" if products is not None else "R2b", "R3", "R4", "R6", "R7"]
    if ev.get("available"):
        rule_refs.append("R5")
    row.update(
        kind="maintain", advice_stage=stage_txt, advice_code=eff,
        runs=runs,
        medicine="否（0）", probe=PROBE_UNKNOWN,
        why=" · ".join(why_bits), rule_refs=rule_refs,
    )
    return row


# ---------------------------------------------------------------- 主流程
def build(paths: Paths, args, now: dt.datetime = None) -> dict:
    now = now or dt.datetime.now()
    master = load_master(paths, override=getattr(args, "master", "") or "")
    cap = pb.Capability.load(paths)
    water = pb.load_water_cfg(paths)
    farm = pb.load_farm_stages(paths)
    apcost = pb.load_json(paths.apcost_path, {}) or {}
    health = load_health(paths, override=getattr(args, "health_file", "") or "")
    sanity_over = {}
    for kv in (getattr(args, "sanity", None) or []):
        if "=" not in kv:
            raise pb.Refuse("--sanity 需要 账号=N 形式，实得 %r" % kv)
        k, v = kv.split("=", 1)
        try:
            sanity_over[k.strip()] = int(float(v))
        except ValueError:
            raise pb.Refuse("--sanity %s 的 N 不是数字" % kv) from None

    accounts = [a for a in (master["doc"].get("accounts") or []) if isinstance(a, dict)]
    # 活动快照：每服（roster 出现的 server_key）取一次
    ev_cache, warnings = {}, []
    tokens = sorted({pb.server_key_of(a.get("server")) for a in accounts if pb.server_key_of(a.get("server"))})
    for token in tokens or [""]:
        ev = pb.events_policy(paths, token, now, warn_age=getattr(args, "events_warn_age", 3.0),
                              fail_age=getattr(args, "events_fail_age", 7.0))
        ev_cache[token] = ev
        if not ev.get("available"):
            warnings.append("活动快照缺失：%s（活动判断关闭）" % paths.events_path)
        elif ev.get("stale") and (ev.get("age_days") or 0) > 7:
            warnings.append("活动快照已 %.1f 天（>7）：%s 服拒用活动判断" % (ev["age_days"], token))
    ev_default = pb.events_policy(paths, "", now)
    ev_cache[""] = ev_default
    for k, ev in ev_cache.items():
        if k and not ev.get("server_key"):
            warnings.append("服别未识别：%r（活动判断关闭）" % k)

    products = {}
    acc_dir = os.path.join(paths.runtime, "reports", "accounts")
    if os.path.isdir(acc_dir):
        for nm in os.listdir(acc_dir):
            d = paths.account_dir(nm)
            depot = pb.load_json(os.path.join(d, "depot.json"))
            if isinstance(depot, dict):
                products[nm] = {"depot": depot,
                                "operators": pb.load_json(os.path.join(d, "operators.json")) or {},
                                "dir": d}

    ctx = {"cap": cap, "water": water, "farm": farm, "apcost": apcost, "events": ev_cache,
           "products": products, "health": health, "sanity_over": sanity_over, "_effective": {}}
    rows = [decide_account(a, ctx, now) for a in accounts]

    summary = {
        "total": len(rows),
        "enabled": sum(1 for r in rows if r["enabled"]),
        "disabled": sum(1 for r in rows if not r["enabled"]),
        "rogue": sum(1 for r in rows if str(r["script"]).startswith(ROGUE_PREFIX)),
        "advise_change": sum(1 for r in rows if r.get("kind") == "change"),
        "advise_activity": sum(1 for r in rows if r.get("kind") == "activity"),
        "advise_maintain": sum(1 for r in rows if r.get("kind") == "maintain"),
        "with_products": sorted(products.keys()),
        "unknown_probe": sum(1 for r in rows if r.get("probe") == PROBE_UNKNOWN),
        "health_flagged": sum(1 for r in rows if (r.get("health") or {}).get("flags")
                             or (r.get("health") or {}).get("state") == "full"),
        "health_available": bool(health.get("accounts")),
    }
    inputs = {
        "events": {"path": pb._rel(paths.root, paths.events_path),
                   "generated_at": (ev_default.get("generated_at_bjt") or ""),
                   "age_days": ev_default.get("age_days")},
        "capability": {"path": pb._rel(paths.root, paths.cap_path), "built_at": cap.built_at,
                       "window": cap.window},
        "master": {"path": pb._rel(paths.root, master["path"]), "generated_at": master["generated_at"]},
        "water": {"path": pb._rel(paths.root, paths.water_path), "budget_per_day": water.get("ap_budget_per_day")},
        "farm": {"path": pb._rel(paths.root, paths.farm_path)},
        "apcost": {"path": pb._rel(paths.root, paths.apcost_path)},
        "health": {"path": pb._rel(paths.root, health["path"]) if health["path"] else "",
                   "available": health["available"], "generated_at": health.get("generated_at") or ""},
    }
    ev_summary = []
    for token in tokens:
        ev = ev_cache.get(token) or {}
        sv = (pb.load_json(paths.events_path, {}) or {}).get("servers", {}).get(token) or {}
        ev_summary.append({
            "server": token, "short": SERVER_SHORT.get(token, token),
            "verdict": ev.get("verdict_events") or sv.get("activity_verdict") or ev.get("verdict") or "",
            "farming": [f.get("name") for f in (ev.get("farming") or [])],
            "maa_ok": bool((ev.get("maa_stage_activity") or {}).get("has_active_stage_activity")),
            "upcoming": [f.get("name") for f in (ev.get("upcoming_farming") or [])],
            "stale": ev.get("stale"), "age_days": ev.get("age_days"),
        })
    plan = {
        "schema": SCHEMA, "date": now.strftime("%Y%m%d"), "date_dash": now.strftime("%Y-%m-%d"),
        "generated_at": now.strftime(TS_FMT), "tool": "tools/mas_daily_advice.py",
        "inputs": inputs, "summary": summary, "events": ev_summary, "accounts": rows,
        "warnings": warnings, "rules": RULES,
        "md_path": os.path.join(paths.reports_dir, "daily_advice_%s.md" % now.strftime("%Y%m%d")),
        "json_path": os.path.join(paths.reports_dir, "daily_advice_%s.json" % now.strftime("%Y%m%d")),
    }
    return plan


def render_md(plan: dict) -> str:
    L = []
    sm = plan["summary"]
    L.append("# 每日刷取建议 · %s（只建议不执行）" % plan["date_dash"])
    L.append("")
    L.append("> 生成：%s ｜ 工具：`tools/mas_daily_advice.py` ｜ 约束：只读 + 写报告（零发车、零配置改动，R10）"
             % plan["generated_at"])
    L.append("> 要执行请走 `mas_plan_builder.py`（草案）→ `mas_run_profile.py`（发车，需用户/lead 批准）。")
    L.append("")
    L.append("## 0. 今日一眼")
    L.append("")
    L.append("- 号量：%d（日常口径 %d · 肉鸽线 %d · 停用 %d）；有识别数据 %d 号（%s）"
             % (sm["total"], sm["total"] - sm["rogue"] - sm["disabled"], sm["rogue"], sm["disabled"],
                len(sm["with_products"]), "、".join(sm["with_products"]) or "无"))
    for e in plan["events"]:
        line = "- %s服：%s" % (e["short"], e["verdict"] or "—")
        if e["farming"]:
            line += "；在期刷关：%s" % "、".join(e["farming"])
        line += "；MAA 表认活动关=%s" % ("是" if e["maa_ok"] else "否")
        if e["upcoming"]:
            line += "；下一档：%s" % "、".join(e["upcoming"])
        if e["stale"]:
            line += "（⚠快照 %.1f 天）" % (e["age_days"] or 0)
        L.append(line)
    changes = [r for r in plan["accounts"] if r.get("kind") in ("change", "activity")]
    maintains = [r for r in plan["accounts"] if r.get("kind") == "maintain"]
    L.append("- 建议动作（需批准才执行）：%d 号建议**调整/开活动**；%d 号维持现计划。" % (len(changes), len(maintains)))
    for r in changes:
        L.append("  - %s（%s）：%s —— %s" % (r["account"], r["server_short"], r["advice_stage"], r["why"]))
    if maintains:
        buckets = {}
        for r in maintains:
            buckets.setdefault(r["advice_code"] or "?", []).append(r["account"])
        for stage, names in sorted(buckets.items()):
            L.append("  - 维持 %s：%d 号（%s%s）" % (stage, len(names), "、".join(names[:8]),
                                                    " 等" if len(names) > 8 else ""))
    if not changes:
        L.append("- 今日无需调整：全部维持现计划。")
    L.append("")
    L.append("> `unknown ⇒ 需先试探一次`（R2b/R4）：该号**尚无上号识别数据**（仓库未知）⇒ 目标未经实据校准，"
             "按建议动身前先做一次最小试探/首轮人工复核；有识别数据的号按台账闸门（R4）走。")
    L.append("")
    L.append("## 1. 逐号建议（一行一号）")
    L.append("")
    L.append("| # | 账号 | 服 | 现关 | 建议关卡 | 次数/理智 | 用药 | 需先试探 | 依据 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(plan["accounts"], 1):
        cur = r["current"]
        cur_txt = (cur.get("stage") or "—")
        if cur.get("plan_table"):
            cur_txt += "（计划表）"
        else:
            cur_txt += " +余%s" % (cur.get("remain") or "—")
        L.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            i, r["account"] or "(无名)", r["server_short"], cur_txt, r["advice_stage"], r["runs"],
            r["medicine"], r["probe"], r["why"]))
    L.append("")
    L.append("## 2. 规则与依据（逐条可查；「依据」列的 R# 引用这里）")
    L.append("")
    for k in sorted(RULES, key=lambda x: (int("".join(c for c in x if c.isdigit())), x)):
        L.append("- **%s**：%s" % (k, RULES[k]))
    L.append("")
    L.append("## 3. 输入与数据缺口")
    L.append("")
    for k, v in plan["inputs"].items():
        L.append("- %s：%s" % (k, json.dumps(v, ensure_ascii=False)))
    if plan["warnings"]:
        L.append("")
        L.append("⚠ 告警：")
        for w in plan["warnings"]:
            L.append("  - %s" % w)
    L.append("")
    L.append("数据缺口（补上后本报告自动变准，无需改工具）：")
    L.append("- 上号识别：%d/%d 号（补：docs/HERMES_ACCOUNT_INSPECT_PLAN.md 的口径；产物落 runtime/reports/accounts/<账号>/）"
             % (len(sm["with_products"]), sm["total"]))
    if not plan["inputs"]["health"]["available"]:
        L.append("- account_health（理智溢出）：未产出（X5/t_61b187a9）⇒ 理智列只给「到理智耗尽」口径；产出后自动消费"
                 "（或 --health-file / --sanity 手工喂数）。")
    L.append("")
    L.append("## 4. 复现")
    L.append("")
    L.append("```bash")
    L.append("python tools/mas_daily_advice.py                # 生成当日报告（本文件）")
    L.append("python tools/mas_daily_advice.py --no-write     # 只打印不写盘")
    L.append("python tools/mas_daily_advice.py --selftest     # 离线自测")
    L.append("```")
    L.append("")
    L.append("*本报告由 mas_daily_advice 生成；只读输入、只写本报告（+同名 .json）；未触碰任何 AUTO-MAS/MAA 配置。*")
    return "\n".join(L) + "\n"


def save_report(plan: dict) -> None:
    os.makedirs(os.path.dirname(plan["md_path"]), exist_ok=True)
    with open(plan["md_path"], "w", encoding="utf-8") as f:
        f.write(render_md(plan))
    doc = {k: plan[k] for k in ("schema", "date", "generated_at", "tool", "inputs", "summary",
                                "events", "accounts", "warnings")}
    doc["rules"] = plan["rules"]
    tmp = plan["json_path"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, plan["json_path"])


# ---------------------------------------------------------------- CLI
def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="每日刷取建议（只建议不执行）：活动期 + 仓库缺口 + 理智 → 今日各号建议（只读 + 写报告）")
    ap.add_argument("--date", help="报告日期 YYYY-MM-DD（默认今天；只影响文件命名/标题）")
    ap.add_argument("--master", help="指定名册 JSON（默认取 runtime/reports/mas_account_master_*.json 最新一份）")
    ap.add_argument("--health-file", help="指定 account_health JSON（默认取 runtime/reports/account_health_*.json 最新）")
    ap.add_argument("--sanity", action="append", metavar="账号=N", help="手工理智（可重复；覆盖 health 值）")
    ap.add_argument("--events-warn-age", type=float, default=3.0, help="活动快照超过 N 天告警（默认 3）")
    ap.add_argument("--events-fail-age", type=float, default=7.0, help="活动快照超过 N 天拒用活动判断（默认 7）")
    ap.add_argument("--out-dir", help="输出目录（默认 runtime/reports）")
    ap.add_argument("--no-write", action="store_true", help="只打印 md，不写盘")
    ap.add_argument("--json", action="store_true", help="机读单行摘要（配合 --no-write 亦可用）")
    ap.add_argument("--selftest", action="store_true", help="离线自测（假数据；零触网、零写 runtime）")
    return ap.parse_args(argv)


class _PathsWithOut(Paths):
    def __init__(self, base: Paths, out_dir: str):
        super().__init__(root=base.root, runtime=base.runtime)
        self._out = out_dir

    @property
    def reports_dir(self):
        return self._out


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.selftest:
        rep = selftest()
        if args.json:
            print(json.dumps(rep, ensure_ascii=False))
        else:
            print("SELFTEST=%s (%d/%d)" % ("OK" if rep["ok"] else "FAIL", rep["passed"],
                                           rep["passed"] + rep["failed"]))
            for c in rep["cases"]:
                print("  %s %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"],
                                     "" if c["ok"] else "  ← " + c["detail"]))
        return 0 if rep["ok"] else 3
    paths = Paths(root=(os.environ.get("MAAORCH_DAILY_ADVICE_ROOT") or ROOT),
                  runtime=(os.environ.get("MAAORCH_DAILY_ADVICE_RUNTIME") or DEFAULT_RUNTIME))
    if args.out_dir:
        paths = _PathsWithOut(paths, args.out_dir)
    now = dt.datetime.now()
    if args.date:
        try:
            now = dt.datetime.strptime(args.date, "%Y-%m-%d").replace(
                hour=now.hour, minute=now.minute, second=now.second)
        except ValueError:
            print("拒绝：--date 需要 YYYY-MM-DD", file=sys.stderr)
            return 2
    try:
        plan = build(paths, args, now=now)
    except pb.Refuse as e:
        print("拒绝：%s" % e, file=sys.stderr)
        return 2
    if args.no_write:
        print(render_md(plan))
    else:
        save_report(plan)
        print("=== mas_daily_advice · %s ===" % plan["date_dash"])
        sm = plan["summary"]
        print("- 号量 %d：建议调整 %d · 活动 %d · 维持 %d（肉鸽 %d / 停用 %d）"
              % (sm["total"], sm["advise_change"], sm["advise_activity"], sm["advise_maintain"],
                 sm["rogue"], sm["disabled"]))
        print("- 报告：%s" % pb._rel(paths.root, plan["md_path"]))
        print("- 机读：%s" % pb._rel(paths.root, plan["json_path"]))
        if plan["warnings"]:
            for w in plan["warnings"]:
                print("  ⚠ %s" % w)
    if args.json:
        sm = plan["summary"]
        print(json.dumps({"tool": "mas_daily_advice", "date": plan["date"], "md": plan["md_path"],
                          "summary": sm, "warnings": plan["warnings"],
                          "changes": [{"account": r["account"], "advice": r["advice_stage"],
                                       "probe": r["probe"]}
                                      for r in plan["accounts"] if r.get("kind") in ("change", "activity")]},
                         ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- 自测
def _fixture(root: str, runtime: str, events_age_days: float = 0.1) -> None:
    """合成数据（只为 selftest；只写 tmp 目录）。"""
    R = os.path.join(runtime, "reports")
    os.makedirs(os.path.join(R, "accounts", "TestA"), exist_ok=True)
    pb.save_json(os.path.join(R, "accounts", "TestA", "operators.json"),
                 {"account": "TestA", "uid": "1", "server": "官服", "operators": [], "counts": {"owned": 0}})
    pb.save_json(os.path.join(R, "accounts", "TestA", "depot.json"),
                 {"account": "TestA", "uid": "1", "server": "官服", "fetched_at": "2026-09-28T08:00:00+0800",
                  "items": {"固源岩组": 54, "聚酸酯组": 68, "糖组": 999}})
    os.makedirs(os.path.join(R, "accounts", "TestFail"), exist_ok=True)
    pb.save_json(os.path.join(R, "accounts", "TestFail", "depot.json"),
                 {"account": "TestFail", "uid": "2", "server": "官服", "fetched_at": "2026-09-28T08:00:00+0800",
                  "items": {"固源岩组": 54, "聚酸酯组": 999, "糖组": 999}})
    os.makedirs(os.path.join(R, "accounts", "TestTop"), exist_ok=True)
    pb.save_json(os.path.join(R, "accounts", "TestTop", "depot.json"),
                 {"account": "TestTop", "uid": "3", "server": "官服", "fetched_at": "2026-09-28T08:00:00+0800",
                  "items": {"固源岩组": 999, "聚酸酯组": 140, "糖组": 999}})
    gen = (dt.datetime(2026, 9, 28, 4, 0) - dt.timedelta(days=events_age_days)).strftime("%Y-%m-%d %H:%M:%S")
    ev = {"schema": "maaorch.server_events/1", "generated_at_bjt": gen + "+08:00", "headline": "selftest",
          "servers": {
              "cn_official": {"activity_verdict": "活动期（小活动，非刷关）", "active_events": [
                  {"name": "签到", "type": "签到", "start_bjt": "2026-09-23", "end_bjt": "2026-09-30"}],
                  "upcoming_events": [{"name": "矢量突破#3", "type": "限时活动（爬塔类）", "start_bjt": "2026-09-29 16:00"}],
                  "maa_stage_activity": {"has_active_stage_activity": False}},
              "jp": {"activity_verdict": "在刷活动关", "active_events": [
                  {"name": "人々、我々", "type": "SideStory（刷关）", "start_bjt": "2026-09-16", "end_bjt": "2026-09-30 02:59",
                   "source": "jp-official-4626"}],
                  "upcoming_events": [], "maa_stage_activity": {"has_active_stage_activity": True}}}}
    pb.save_json(os.path.join(R, "server_events.json"), ev)
    cap = {"schema": "maaorch.stage_capability/1", "built_at": "2026-09-28 08:00:00",
           "window": {"days": 30, "start": "2026-08-30", "end": "2026-09-28"}, "stages": ["1-7", "SR-7", "10-6", "2-4"],
           "rows": [
               {"account": "TestA", "stage": "1-7", "status": "success", "last_success": "2026-09-28",
                "success_n": 9, "fail_n": 0, "failure_types": {}},
               {"account": "TestCN", "stage": "1-7", "status": "success", "last_success": "2026-09-28",
                "success_n": 5, "fail_n": 0, "failure_types": {}},
               {"account": "TestJP", "stage": "1-7", "status": "success", "last_success": "2026-09-28",
                "success_n": 5, "fail_n": 0, "failure_types": {}},
               {"account": "TestFail", "stage": "1-7", "status": "success", "last_success": "2026-09-28",
                "success_n": 5, "fail_n": 0, "failure_types": {}},
               {"account": "TestFail", "stage": "10-6", "status": "failed", "last_failure": "2026-09-20",
                "success_n": 0, "fail_n": 2, "failure_types": {"proxy_mismatch": 2}, "impact": "capability"},
               {"account": "TestFail", "stage": "2-4", "status": "success", "last_success": "2026-09-21",
                "success_n": 4, "fail_n": 0, "failure_types": {}},
               {"account": "TestTop", "stage": "1-7", "status": "success", "last_success": "2026-09-28",
                "success_n": 5, "fail_n": 0, "failure_types": {}},
               {"account": "TestOff", "stage": "1-7", "status": "success", "last_success": "2026-09-20",
                "success_n": 3, "fail_n": 0, "failure_types": {}}],
           "unknown": {}}
    pb.save_json(os.path.join(R, "stage_capability.json"), cap)
    plan_cn = "关:SR-7 / 剩:1-7 / 药:0 / 连战:0 / 剿:Close / 模式:Fixed / 基建:Normal"
    pb.save_json(os.path.join(R, "mas_account_master_20260928.json"),
                 {"generated_at": "2026-09-28 08:00:00", "accounts": [
                     {"script": "明日方舟-测试-A", "game_name_cfg": "TestA", "server": "Official", "idx": 1,
                      "maadir": "MAA_1", "plan": plan_cn, "enabled": True},
                     {"script": "明日方舟-测试-CN", "game_name_cfg": "TestCN", "server": "Official", "idx": 2,
                      "maadir": "MAA_1", "plan": plan_cn, "enabled": True},
                     {"script": "明日方舟-测试-JP", "game_name_cfg": "TestJP", "server": "YoStarJP", "idx": 3,
                      "maadir": "MAA_2", "plan": "关:1-7 / 剩:1-7 / 药:0 / 连战:0 / 剿:Close / 模式:Fixed / 基建:Normal",
                      "enabled": True},
                     {"script": "明日方舟-测试-F", "game_name_cfg": "TestFail", "server": "Official", "idx": 4,
                      "maadir": "MAA_3", "plan": plan_cn, "enabled": True},
                     {"script": "明日方舟-测试-T", "game_name_cfg": "TestTop", "server": "Official", "idx": 5,
                      "maadir": "MAA_4", "plan": plan_cn, "enabled": True},
                     {"script": "明日方舟-测试-O", "game_name_cfg": "TestOff", "server": "Official", "idx": 6,
                      "maadir": "MAA_5", "plan": plan_cn, "enabled": False},
                     {"script": "l-1-TestRogue", "game_name_cfg": "TestRogue", "server": "YoStarJP", "idx": 7,
                      "maadir": "MAA_6", "plan": "关:1-7 / 药:0 / 连战:0 / 剿:Close / 模式:Fixed / 基建:Normal",
                      "enabled": True}]})
    pb.save_json(os.path.join(root, "config", "material_water_levels.json"),
                 {"ap_budget_per_day": 280, "materials": {"固源岩组": 100, "聚酸酯组": 100, "糖组": 100}})
    pb.save_json(os.path.join(root, "config", "farm_stages.json"), {
        "固源岩组": [{"stage": "10-6", "ap": 21, "rate": 0.76}, {"stage": "2-4", "ap": 12, "rate": 0.42}],
        "聚酸酯组": [{"stage": "7-4", "ap": 18, "rate": 0.55}],
        "糖组": [{"stage": "2-5", "ap": 12, "rate": 0.37}]})
    pb.save_json(os.path.join(root, "data", "stage_apcost_v4.json"), {
        "_conventions": {"突袭书写约定": "test"},
        "unit_cost": {"10-6|普通": 21, "2-4|普通": 12, "SR-7|普通": 21, "7-4|普通": 18, "2-5|普通": 12, "1-7|普通": 6},
        "stages": {"main_10-06": {"ap": 21, "code": "10-6", "type": "MAIN"},
                   "main_02-04": {"ap": 12, "code": "2-4", "type": "MAIN"},
                   "act54side_07": {"ap": 21, "code": "SR-7", "type": "ACTIVITY"},
                   "main_07-04": {"ap": 18, "code": "7-4", "type": "MAIN"},
                   "main_02-05": {"ap": 12, "code": "2-5", "type": "MAIN"},
                   "main_01-07": {"ap": 6, "code": "1-7", "type": "MAIN"}},
        "code_index": {"10-6": ["main_10-06"], "2-4": ["main_02-04"], "SR-7": ["act54side_07"],
                       "7-4": ["main_07-04"], "2-5": ["main_02-05"], "1-7": ["main_01-07"]}})


def selftest() -> dict:
    import tempfile
    cases = []

    def check(name, ok, detail=""):
        cases.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    tmp = tempfile.mkdtemp(prefix="mas_daily_advice_selftest_")
    root = os.path.join(tmp, "repo")
    runtime = os.path.join(root, "runtime")
    _fixture(root, runtime)
    paths = Paths(root=root, runtime=runtime)
    now = dt.datetime(2026, 9, 28, 12, 0)
    args = parse_args(["--no-write"])

    try:
        plan = build(paths, args, now=now)
    except Exception as e:  # noqa: BLE001
        check("build 生成报告", False, "%s: %s" % (type(e).__name__, e))
        return {"ok": False, "passed": 0, "failed": len(cases), "cases": cases}
    check("build 生成报告", True)
    rows = {r["account"]: r for r in plan["accounts"]}

    # 1) 有识别数据：缺口 → 10-6；台账未证 → 先试探
    r = rows["TestA"]
    check("TestA 建议=改 10-6（固源岩组缺口 46）", r["advice_code"] == "10-6" and "10-6" in r["advice_stage"], r["advice_stage"])
    check("TestA 次数含缺口估算(≈61 次)", "≈61 次" in r["runs"], r["runs"])
    check("TestA 需先试探（10-6 未证）", r["probe"] == "是（10-6 未证）", r["probe"])
    check("TestA 用药=否", r["medicine"] == "否（0）", r["medicine"])
    check("TestA 依据带 R2a/R4", all(x in r["rule_refs"] for x in ("R2a", "R4")), r["rule_refs"])

    # 2) 无识别数据 + 主关已下线 → 维持回退位 1-7，且标 unknown ⇒ 需先试探一次
    r = rows["TestCN"]
    check("TestCN 建议=维持 1-7（SR-7 下线→回退）", r["advice_code"] == "1-7" and "回退" in r["advice_stage"],
          (r["advice_stage"], r["why"]))
    check("TestCN 需先试探=字面 unknown ⇒ 需先试探一次", r["probe"] == PROBE_UNKNOWN, r["probe"])
    check("TestCN 依据含 R3", "R3" in r["rule_refs"], r["rule_refs"])

    # 3) 日服在期刷关 + MAA 认 → 活动关
    r = rows["TestJP"]
    check("TestJP 建议=活动关 PA-6/7/8", r["kind"] == "activity" and "PA-6/7/8" in r["advice_stage"], r["advice_stage"])
    check("TestJP 需先试探=字面 unknown ⇒ 需先试探一次", r["probe"] == PROBE_UNKNOWN, r["probe"])

    # 4) 台账 failed 剔除 → 换 2-4（已证）
    r = rows["TestFail"]
    check("TestFail 10-6 failed 被剔除→建议 2-4", r["advice_code"] == "2-4", (r["advice_stage"], r["why"]))
    check("TestFail 台账已证→否", r["probe"] == "否（台账已证）", r["probe"])

    # 5) 全达标 → 补刀（余量最小=聚酸酯组→7-4），无缺口次数=到耗尽
    r = rows["TestTop"]
    check("TestTop 补刀=7-4", r["advice_code"] == "7-4", r["advice_stage"])
    check("TestTop 次数=到理智耗尽", "到理智耗尽" in r["runs"] and "缺口" not in r["runs"], r["runs"])

    # 6) 停用/肉鸽 → 不给日常建议
    check("TestOff 停用 → —", rows["TestOff"]["advice_stage"] == "—", rows["TestOff"]["advice_stage"])
    check("TestRogue 肉鸽线 → —", rows["TestRogue"]["advice_stage"] == "—", rows["TestRogue"]["advice_stage"])

    # 7) health 文件：理智 240 → 本次 ≈11 次（240//21）；满仓号显示溢出点数不留 ?
    hpath = os.path.join(runtime, "reports", "account_health_20260928.json")
    pb.save_json(hpath, {"schema": "x", "date": "2026-09-28",
                         "accounts": [{"account": "TestA", "sanity": 240, "sanity_overflow": 7,
                                       "flags": ["理智溢出"],
                                       "advice": ["定因后补跑：理智已满 240h（自测）"]},
                                      {"account": "TestTop", "state": "full", "hours_full": 12.0,
                                       "lost_points": 120, "sanity_last": 0}]})
    plan2 = build(paths, args, now=now)
    by2 = {x["account"]: x for x in plan2["accounts"]}
    r = by2["TestA"]
    check("health 消费：本次 ≈11 次（理智 240）", "240" in r["runs"] and "≈11 次" in r["runs"], r["runs"])
    check("health 溢出进列（≈7 AP）", "溢出≈7 AP" in r["runs"], r["runs"])
    check("health advice 原样进依据", "定因后补跑" in r["why"] and "理智溢出" in r["why"], r["why"])
    check("满仓号显示溢出点数（不留 ?）", "溢出≈120 AP" in by2["TestTop"]["runs"], by2["TestTop"]["runs"])
    os.remove(hpath)

    # 8) 活动快照 >7 天 → 日服不再给活动关建议（回落到维持）
    _fixture(root, runtime, events_age_days=9)
    plan3 = build(paths, args, now=now)
    r = {x["account"]: x for x in plan3["accounts"]}["TestJP"]
    check("快照过期 → 活动判断关闭（TestJP 回维持 1-7）", r["kind"] == "maintain", (r["kind"], r["advice_stage"]))
    check("过期告警在列", any("拒用" in w or "缺失" in w for w in plan3["warnings"]), plan3["warnings"])

    # 9) md 渲染冒烟
    md = render_md(plan)
    check("md 含表头/规则/复现", "| # | 账号 | 服 | 现关 | 建议关卡 |" in md and "R2b" in md and "mas_daily_advice" in md)

    # 10) 名册缺失 → Refuse
    os.rename(os.path.join(runtime, "reports", "mas_account_master_20260928.json"),
              os.path.join(runtime, "reports", "mas_account_master_20260928.json.bak"))
    try:
        build(paths, args, now=now)
        check("名册缺失 → 拒绝", False, "没抛 Refuse")
    except pb.Refuse:
        check("名册缺失 → 拒绝", True)
    os.rename(os.path.join(runtime, "reports", "mas_account_master_20260928.json.bak"),
              os.path.join(runtime, "reports", "mas_account_master_20260928.json"))

    passed = sum(1 for c in cases if c["ok"])
    return {"ok": passed == len(cases), "passed": passed, "failed": len(cases) - passed, "cases": cases}


if __name__ == "__main__":
    sys.exit(main())
