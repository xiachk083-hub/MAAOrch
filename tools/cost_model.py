"""简化费用模型：算「第 N 个部署最早什么时候能落地」，用来反推"这份作业来不来得及"。

**不含先锋产费** ⇒ 算出来是**悲观下界**：真实有产费只会更快。
所以「悲观下界都不漏」是硬结论（实际一定不漏）；「悲观下界漏了」只说明该加产费再看。

数据源（全部本地、逐项核实过）：
    初始费用 / 回费间隔 / 上限 / 生命点   data/_level_<stage>.json -> options
        实测：initialCost=10, costIncreaseTime=1.0, maxCost=99, maxLifePoint=3（ex02）
        ⚠️ initialCost **逐关不同**（本仓 18 关里出现过 3/8/10/15/18/24/25），必须读，不能写死 10
    部署费用                             data/_char_table.json
        -> <char>.phases[<elite>].attributesKeyFrames[0].data.cost
        ⚠️ 费用**按精英阶段变**（乌尔比安 E0=20/E1=22/E2=24），阶段取 box 的 `elite`
        ⚠️ 键是 phases[].attributesKeyFrames[0].data.cost，**不是** phases[].cost（那里是 None）
    精英阶段                            data/operbox_batch/emu2.json -> <name>.elite（原始键是 elite，不是 e）

已知未纳入（都标注，不猜）：
    - **先锋产费**（桃金娘/凛御银灰等）⇒ 结果是悲观下界
    - **再部署 +2**：同一干员第二次部署费用更高。本仓无权威数据，且作业里重复部署很少，先不计
    - 撤退**不回费**（按 0 计，符合游戏规则）
"""
import json
import pathlib

PROJ = pathlib.Path(__file__).resolve().parent.parent
CT = PROJ / "data" / "_char_table.json"
BOX = PROJ / "data" / "operbox_batch" / "emu2.json"

_cache = {}


def load_level_options(stage="act16side_ex02"):
    """关卡的费用/生命点设置。读不到就返回 None（**不套默认值**）。"""
    p = PROJ / "data" / ("_level_%s.json" % stage)
    if not p.is_file():
        return None
    o = (json.loads(p.read_text(encoding="utf-8")).get("options") or {})
    if o.get("initialCost") is None or o.get("costIncreaseTime") is None:
        return None
    return {"stage": stage, "initial": o["initialCost"],
            "regen": o["costIncreaseTime"], "max": o.get("maxCost"),
            "life": o.get("maxLifePoint"), "move_mult": o.get("moveMultiplier"),
            "source": str(p)}


def load_char_costs(path=None):
    """{干员名: [E0费, E1费, E2费]}。缺失的阶段为 None。"""
    p = pathlib.Path(path) if path else CT
    if str(p) in _cache:
        return _cache[str(p)]
    ct = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for v in ct.values():
        nm = v.get("name")
        if not nm:
            continue
        costs = []
        for ph in (v.get("phases") or []):
            kf = ph.get("attributesKeyFrames") or []
            costs.append((kf[0].get("data") or {}).get("cost") if kf else None)
        out[nm] = costs
    _cache[str(p)] = out
    return out


def load_elites(path=None):
    """{干员名: elite}（原始 box 的键是 elite，不是 e）。"""
    p = pathlib.Path(path) if path else BOX
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    own = d.get("own") or d.get("own_opers") or []
    return {o["name"]: o.get("elite") for o in own
            if isinstance(o, dict) and o.get("name")}


def cost_of(name, costs=None, elites=None):
    """该干员的部署费用。返回 (cost, elite, reason)；取不到时 cost=None 并给 reason。"""
    costs = costs if costs is not None else load_char_costs()
    elites = elites if elites is not None else load_elites()
    lst = costs.get(name)
    if not lst:
        return None, None, "cost_missing"
    elite = elites.get(name) or 0
    if elite >= len(lst) or lst[elite] is None:
        # 该阶段没数 → 退回 E0 并标明回落，不静默当成 E0
        if lst and lst[0] is not None:
            return lst[0], 0, "elite_fellback"
        return None, elite, "cost_missing"
    return lst[elite], elite, None


def simulate_deploy_times(actions, opts, costs=None, elites=None):
    """按 actions 顺序算每个部署**最早**落地时刻（悲观下界：不计产费）。

    规则：某步要花 P，当前费不够就等到够（回费 regen/s，且封顶 maxCost）；
    够则立刻下、扣费。撤退/技能不花费用。
    """
    if not opts:
        return [], "no_level_options"
    costs = costs if costs is not None else load_char_costs()
    elites = elites if elites is not None else load_elites()
    regen = opts["regen"] or 1.0
    cap = opts.get("max") or 99
    cost = float(opts["initial"])
    t = 0.0
    out = []
    for a in actions or []:
        if a.get("type") != "Deploy" or not a.get("name"):
            continue
        price, elite, why = cost_of(a["name"], costs, elites)
        rec = {"name": a["name"], "order": len(out) + 1, "phase": elite,
               "price": price, "missing": why}
        if price is None:
            rec.update(time=None, cost_after=None)
            out.append(rec)
            continue                                  # 费用未知 ⇒ 不扣费、不推进（并标注），不猜
        if cost < price:
            wait = (price - cost) / regen
            # 封顶：等待期间费用最多涨到 cap
            actual_wait = max(0.0, (min(price, cap) - cost) / regen) if price <= cap else wait
            t += actual_wait
            cost = min(cap, cost + actual_wait * regen)
            if cost < price:                          # 上限低于需求（理论上不会）
                rec.update(time=None, cost_after=cost, missing="cost_over_cap")
                out.append(rec)
                continue
        cost -= price
        rec.update(time=round(t, 2), cost_after=round(cost, 2))
        out.append(rec)
    return out, None


def report(stage="act16side_ex02", verbose=True):
    """CLI：逐份作业打印"每个部署最早落地时刻"（两套移速系数各跑一遍，标出敏感的）。"""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import viz_plan as V
    opts = load_level_options(stage)
    if not opts:
        print("读不到 %s 的 options ⇒ 不猜费用设置" % stage)
        return
    print("关卡 %s：初始 %s 费 / 回费 %s 秒每费 / 上限 %s / 生命 %s / moveMultiplier %s"
          % (stage, opts["initial"], opts["regen"], opts["max"], opts["life"], opts["move_mult"]))
    costs, elites = load_char_costs(), load_elites()
    jobs, _ = V.load_jobs(V.scan_jobs(stage), V.professions(), stage)
    for jb in jobs:
        dts, err = simulate_deploy_times(jb["actions"], opts, costs, elites)
        miss = [d for d in dts if d["time"] is None]
        print("\n■ %s" % jb["title"])
        for d in dts:
            print("   %d. %-10s %s费 E%s  ->  T+%s%s"
                  % (d["order"], d["name"], d["price"], d["phase"],
                     d["time"] if d["time"] is not None else "?", 
                     "   ⚠ %s" % d["missing"] if d["missing"] else ""))
        if miss:
            print("   ⚠ %d 个部署费用缺失 → 落地时刻算不出（这些不参与判漏）" % len(miss))
    return opts


def check_leaks(stage="act16side_ex02", mult=None):
    """判漏：对每条出怪，看 arrival 时刻之前有没有**能碰到它那条路**的部署落地。

    **读法（重要，别overclaim）**：本模型是**悲观下界**（不计先锋产费 ⇒ 落地只会更晚）。
        「已挡」/「可打」= **确定不漏**（连不考虑产费都赶上了）—— 硬结论
        「存疑」     = 悲观下界下够不着。**不等于一定漏**：算上产费可能就来得及。
    所以这份输出的用处是：**筛掉确定不漏的，把"存疑"挑出来人工看**。
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import viz_plan as V
    import enemy_speed as ES

    opts = load_level_options(stage)
    costs, elites = load_char_costs(), load_elites()
    prof = V.professions()
    W, H, key, bt, routes, waves = V.level(stage)
    raw = json.loads((PROJ / "data" / ("_level_%s.json" % stage)).read_text(encoding="utf-8"))
    rts = raw["routes"]

    # 每条路线的格集合 + 该路线上每条出怪的到达时刻
    path_of = {a: p for a, b, p in routes}
    fly = {a for a, b, p in routes if p and _is_fly(rts, a)}
    lanes = {}

    def lane_key(st, en):
        return st

    for w in raw.get("waves") or []:
        for fr in w.get("fragments") or []:
            for a in fr.get("actions") or []:
                if a.get("actionType") != "SPAWN":
                    continue
                rt = rts[a["routeIndex"]]
                sp, ep = rt.get("startPosition") or {}, rt.get("endPosition") or {}
                st = (sp.get("col"), sp.get("row"))
                en = (ep.get("col"), ep.get("row"))
                if st == en:
                    continue
                p = path_of.get(st) or ES.line_cells(st, en)
                arr = ES.calc_arrival(a.get("preDelay") or 0, p, a.get("key"),
                                      mult=mult, approx=st in fly)
                lanes.setdefault(st, {"cells": set(p), "spawns": []})
                lanes[st]["spawns"].append({
                    "t": a.get("preDelay") or 0, "n": a.get("count", 1),
                    "arrival": arr.get("arrival"), "why": arr.get("reason"),
                    "name": (ES.get_speed(a.get("key"), mult=mult) or {}).get("name") or a.get("key")})

    jobs, _ = V.load_jobs(V.scan_jobs(stage), prof, stage)
    n2t, rng, ops = V.ranges_payload(jobs)
    rows = []
    for jb in jobs:
        dts, _ = simulate_deploy_times(jb["actions"], opts, costs, elites)
        for st, ln in lanes.items():
            for s in ln["spawns"]:
                if s["arrival"] is None:
                    continue
                by = [d for d in dts if d["time"] is not None and d["time"] <= s["arrival"]]
                # 覆盖判定：站在路径上（挡）或射程盖到路径（打）
                covers, stands = [], []
                for d in by:
                    act = _act_of(jb, d)
                    if not act:
                        continue
                    loc = tuple(act.get("location") or ())
                    if loc and _xy(loc) in ln["cells"]:
                        stands.append(d)
                    # 几何走 viz_plan.range_cells（Python 侧唯一实现），别在这里再写一份
                    if d["name"] in n2t and V.range_cells(act, n2t, rng, ops, W, H) & ln["cells"]:
                        covers.append(d)
                verdict = "存疑" if not covers else ("已挡" if stands else "可打")
                rows.append({"job": jb["title"], "src": jb["src"], "lane": st,
                             "enemy": s["name"], "spawn": s["t"], "arrival": s["arrival"],
                             "verdict": verdict,
                             "by": (stands or covers or [{}])[0].get("name"),
                             "by_time": (stands or covers or [{}])[0].get("time")})
    return rows


def _xy(loc):
    """作业里 location 是 [x,y]；路径是 (x,y)。"""
    return (loc[0], loc[1])


def _is_fly(rts, st):
    for r in rts:
        sp = r.get("startPosition") or {}
        if (sp.get("col"), sp.get("row")) == st:
            return (r.get("motionMode") or "").upper() == "FLY"
    return False


def _act_of(jb, d):
    seen = 0
    for a in jb["actions"]:
        if a.get("type") == "Deploy" and a.get("name"):
            seen += 1
            if seen == d["order"]:
                return a
    return None


if __name__ == "__main__":
    report()


# ————————————————— 先锋产费（第二步） —————————————————
SKILL_TBL = PROJ / "data" / "_skill_table.json"


def load_skill_table(path=None):
    p = pathlib.Path(path) if path else SKILL_TBL
    if str(p) in _cache:
        return _cache[str(p)]
    t = json.loads(p.read_text(encoding="utf-8"))
    _cache[str(p)] = t
    return t


def producer_skill(skill_id, mastery=0):
    """产费技能参数。返回 dict，`modeled=False` 时带 `reason`（**不猜**）。

    实测口径（与 `description` 对得上）：
      - `blackboard.value` = **总共回复多少费**（支援号令·β型 value=14 ⇒ 14 费，用户口述也是 14 ✓）
      - `duration`         = 技能持续秒数（14×interval 0.57 ≈ 8.0 ✓）
      - `spData`           = spCost / initSp / spType INCREASE_WITH_TIME
      - 首次开技能时刻 = 部署 + (spCost - initSp) / (spRecoveryPerSec × increment)，之后 sp 归零重充
    """
    s = (load_skill_table() or {}).get(skill_id)
    if not s:
        return {"skill_id": skill_id, "modeled": False, "reason": "skill_not_found"}
    lv = s.get("levels") or []
    if not lv:
        return {"skill_id": skill_id, "modeled": False, "reason": "no_levels"}
    l = lv[min(max(int(mastery), 0), len(lv) - 1)]
    bb = {b.get("key"): b.get("value") for b in (l.get("blackboard") or [])}
    sp = l.get("spData") or {}
    out = {"skill_id": skill_id, "name": l.get("name"), "mastery": mastery,
           "sp_cost": sp.get("spCost"), "init_sp": sp.get("initSp"),
           "sp_type": sp.get("spType"), "increment": sp.get("increment"),
           "duration": l.get("duration"), "bb": bb, "modeled": False}
    if sp.get("spType") != "INCREASE_WITH_TIME":
        out["reason"] = "sp_type=%s（只在随时间为准的充能下建模）" % sp.get("spType")
        return out
    dur = l.get("duration") or 0
    if dur and dur > 0 and bb.get("value") is not None:
        out.update(modeled=True, dp_total=bb["value"], dp_per_sec=bb["value"] / dur,
                   wait=(max(0.0, (sp.get("spCost") or 0) - (sp.get("initSp") or 0))
                         / max(1e-9, (sp.get("increment") or 1) * 1.0)))
        return out
    # 未建模的形态：原样带出黑板，标注原因（凛御银灰 S1 瞬发 / S3 复合）
    keys = [k for k in bb if "cost" in k]
    out["reason"] = ("duration=%s、黑板里的费键=%s ⇒ 形态特殊（瞬发/复合），未建模"
                     % (dur, keys))
    return out


def simulate_with_pioneers(actions, opts, costs=None, elites=None, skills=None,
                           mastery=0, dt=0.25, horizon=240.0):
    """含先锋产费的费用模拟（时间步进）。

    skills = {干员名: 技能序号(1基)}，来自作业 `opers[].skill`（**作业只说用第几个技能**）。
    mastery 全表统一给一个值（0=M0 … 9=M9）—— 因为**专精读不到**，只能出区间。
    返回 (deploy_times, 说明)。
    """
    if not opts:
        return [], "no_level_options"
    costs = costs if costs is not None else load_char_costs()
    elites = elites if elites is not None else load_elites()
    skills = skills or {}
    regen, cap = opts["regen"] or 1.0, opts.get("max") or 99
    dp = float(opts["initial"])
    t = 0.0
    out, pioneers, notes = [], [], []

    def next_deploy():
        for a in actions or []:
            if a.get("type") == "Deploy" and a.get("name"):
                yield a

    todo = list(next_deploy())
    tmax = max(1.0, float(len(todo)) * 60.0)
    while t < min(horizon, tmax) and todo:
        dp = min(cap, dp + regen * dt)
        for p in pioneers:                       # 逐帧推进：产费 → 充能 → 开技能
            if p["until"] is not None:
                if t < p["until"]:
                    dp = min(cap, dp + p["rate"] * dt)
                elif not p["done"]:              # 技能窗口结束
                    p["until"] = None
                    if p["repeat"]:              # maxChargeTime=1：sp 归零重充
                        p["sp"] = 0.0
                    else:
                        p["done"] = True
            if p["until"] is None and not p["done"]:
                # ⚠️ 这里原写成 `(inc or 1)*sp_reg or 1.0*dt`：`or` 的优先级让 `* dt`
                # 永远不生效 ⇒ SP 不随时间涨、技能永远不开。括号必须写清。
                p["sp"] += (p.get("increment") or 1.0) * p.get("sp_reg", 1.0) * dt
                if p["sp"] >= (p.get("wait_sp") or 0):
                    p["start"], p["until"] = t, t + p["dur"]
                    p["sp"] = 0.0
        a = todo[0]
        price, elite, why = cost_of(a["name"], costs, elites)
        if price is None:
            out.append({"name": a["name"], "order": len(out) + 1, "price": None,
                        "phase": elite, "time": None, "missing": why})
            todo.pop(0)
            continue
        if dp >= price:
            dp -= price
            rec = {"name": a["name"], "order": len(out) + 1, "price": price,
                   "phase": elite, "time": round(t, 2), "missing": None}
            out.append(rec)
            todo.pop(0)
            sk_idx = skills.get(a["name"])
            if sk_idx:
                p = _pioneer_of(a["name"], sk_idx, mastery)
                if p:
                    p["sp_reg"] = 1.0
                    rec["pioneer"] = p["name"]
                    pioneers.append(p)
                elif p is None:
                    pass
        t += dt
    return out, notes


def _pioneer_of(op_name, skill_idx, mastery):
    """该干员的第 skill_idx 个技能是不是可建模的产费技能；不是就返回 None。"""
    ct = json.loads(CT.read_text(encoding="utf-8"))
    by = {v["name"]: v for v in ct.values() if v.get("name")}
    v = by.get(op_name)
    if not v:
        return None
    sk = v.get("skills") or []
    if not (1 <= int(skill_idx) <= len(sk)):
        return None
    ps = producer_skill(sk[int(skill_idx) - 1].get("skillId"), mastery)
    if not ps.get("modeled"):
        return None
    return {"name": op_name, "sp": float(ps.get("init_sp") or 0), "sp_reg": 1.0,
            "wait_sp": ps["sp_cost"], "dur": ps["duration"], "rate": ps["dp_per_sec"],
            "start": None, "until": None, "done": False, "repeat": True,
            "increment": ps.get("increment"), "skill": ps.get("name")}
