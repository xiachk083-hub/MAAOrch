"""完整判漏：把三个模型合起来，给每份作业的每条出怪分类。

分类（**只有第一类是硬结论**）：
    确定漏  = 在**最有利的一侧**（敌人最慢 ×1.0 的 enemy_speed 口径之外取 ×0.5、
              我方部署最早 M9+产费）**仍然没有任何部署能碰到它这条路** ⇒ 无人可及，必漏。
              **只依赖射程表 + 精英阶段（都有）**，不依赖伤害类型/专精/系数 ⇒ 硬结论。
    守得住  = 有人能碰到，且按"走完覆盖区所受伤害 ≥ 血量"算得出会被打死。
              ⚠️ **依赖伤害类型判定**（26 个干员里 15 个是按职业猜的）⇒ 不是纯硬结论，标了 tag。
    存疑    = 有人能碰到但算不死（或差得不多）⇒ 需实跑/补 DPS/多挡位接力才知道。

⚠️ 与指导里那份伪代码的三处不同（照抄会错分类）：
  1. **不取"最靠前的部署"当挡位** —— 左路的桃金娘不挡右路、医疗不造成伤害、高台不阻挡。
     挡位必须按"能否影响这条路"筛（站在路径上 = 挡；射程盖到路径格 = 打）。
  2. **`hold < gap` 不等于漏** —— 挡位先死只说明敌人被放行，它还要继续走，后面可能有人接。
     所以判据是"走完全程所受伤害够不够杀它"，不是"撑不撑得到下一个来"。
  3. **区间保留在分类里** —— 结论跟着区间走，区间内翻转就标存疑。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import viz_plan as V
import enemy_speed as ES
import cost_model as CM
import combat_model as CB
import duel_model as DM

RANGED = ("CASTER", "MEDIC", "SNIPER", "SUPPORT")


def _lane_cells(stage, raw, routes, start):
    """该路的格集合（BFS 路径；飞行为直线近似）。"""
    for r in raw.get("routes") or []:
        sp = r.get("startPosition") or {}
        if (sp.get("col"), sp.get("row")) == start:
            ep = r.get("endPosition") or {}
            en = (ep.get("col"), ep.get("row"))
            break
    for a, b, p in routes:
        if a == start:
            return set(p) if p else set(ES.line_cells(start, en))
    return set()



def _lane_order(stage, raw, routes, start):
    """该路**按行进顺序**的格列表（BFS 路径；飞行为直线近似）。

    用途：算"敌人从出怪口走到某格用了多久" ⇒ 判断挡位来不来得及站在那格前。
    `_lane_cells` 返回的是集合，没有顺序，不能拿来找下标。
    """
    for a, b, p in routes:
        if a != start:
            continue
        if p:
            return [tuple(c) for c in p]
        for r in raw.get("routes") or []:
            sp = r.get("startPosition") or {}
            if (sp.get("col"), sp.get("row")) == start:
                ep = r.get("endPosition") or {}
                return [tuple(c) for c in ES.line_cells(start, (ep.get("col"), ep.get("row")))]
    return []


def judge_full(stage="act16side_ex02", mults=(1.0, 0.5), masteries=(0, 9), verbose=True):
    opts = CM.load_level_options(stage)
    costs, elites = CM.load_char_costs(), CM.load_elites()
    prof = V.professions()
    W, H, key, bt, routes, waves = V.level(stage)
    raw = __import__("json").loads(
        (V.PROJ / "data" / ("_level_%s.json" % stage)).read_text(encoding="utf-8"))
    jobs, _ = V.load_jobs(V.scan_jobs(stage), prof, stage)
    n2t, rng, ops = V.ranges_payload(jobs)

    # 每条路的格集合 + 该路的出怪（到达时刻按多套系数算）
    lanes = {}
    for w in waves:
        st = tuple(w["start"])
        if st not in lanes:
            lanes[st] = {"cells": _lane_cells(stage, raw, routes, st), "spawns": []}
        cells = lanes[st]["cells"]
        arr = {}
        for m in mults:
            r = ES.calc_arrival(w["t"] or 0, sorted(cells), w.get("key"), mult=m,
                                approx=w.get("approx"))
            arr[m] = r.get("arrival")
        lanes[st]["spawns"].append({"w": w, "arr": arr,
                                    "arr_max": max(v for v in arr.values() if v is not None)
                                    if any(v is not None for v in arr.values()) else None})

    rows = []
    for jb in jobs:
        sk = {o["name"]: o.get("skill") for o in (jb.get("opers") or [])}
        # 多套专精 → 取"部署最早"那一套（对判漏最有利），并保留各地
        per = {}
        for m in masteries:
            dts, _ = CM.simulate_with_pioneers(jb["actions"], opts, costs, elites, sk, mastery=m)
            per[m] = [d for d in dts if d.get("time") is not None]
        best = min(per.values(), key=lambda ds: (ds[0]["time"] if ds else 9e9)) if per else []
        # 部署时刻区间（跨专精）
        span = {}
        for d in best:
            ts = [x["time"] for x in (per[m] for m in masteries) for x in x
                  if x["name"] == d["name"]]
            if ts:
                span[d["name"]] = (min(ts), max(ts))

        dep_acts = [a for a in jb["actions"] if a.get("type") == "Deploy" and a.get("name")]
        for st, ln in lanes.items():
            for s in ln["spawns"]:
                if s["arr_max"] is None:
                    continue
                # 谁能碰到这条路（最早落地时刻）
                touchers = []
                for d, act in zip(best, dep_acts):
                    is_melee = (act.get("profession") not in RANGED)
                    on_path = act.get("location") and tuple(act["location"]) in ln["cells"]
                    cells = V.range_cells(act, n2t, rng, ops, W, H)
                    hit = cells & ln["cells"]
                    if (is_melee and on_path) or hit:
                        touchers.append((d, act, on_path, len(hit)))
                if not touchers:
                    rows.append({"job": jb["title"], "src": jb["src"], "lane": st,
                                 "enemy": s["w"].get("name"), "arr": s["arr"],
                                 "cls": "确定漏", "why": "无人能碰到这条路"})
                    continue
                t0 = min(touchers, key=lambda x: x[0]["time"])
                if t0[0]["time"] > s["arr_max"]:   # 最有利一侧仍赶不上 = 硬结论
                    rows.append({"job": jb["title"], "src": jb["src"], "lane": st,
                                 "enemy": s["w"].get("name"), "arr": s["arr"],
                                 "cls": "确定漏", "why": "最早能碰到的部署 T+%s 仍晚于最晚到门 T+%s"
                                 % (t0[0]["time"], s["arr_max"]), "by": t0[0]["name"]})
                    continue
                # 算能不能打死：覆盖格数 ÷ 最慢速度 × 最佳 DPS（+ 挡位拖延）
                e = CB.load_enemy_combat(s["w"].get("key"))
                vmin = min(v for v in s["arr"].values() if v is not None)   # ×1.0 = 最快到门
                best_dps, who = 0.0, None
                for t, act, on_path, ncell in touchers:
                    if t["time"] > s["arr_max"]:
                        continue
                    kind, _ = CB.damage_kind(act["name"], sk.get(act["name"]))
                    dop = CB.load_operator_stats(act["name"], elites.get(act["name"]),
                                                 (V.load_box().get(act["name"]) or {}).get("level"))
                    if not dop:
                        continue
                    d = CB.dps(dict(dop, damage_kind=kind), e) or 0
                    if d > best_dps:
                        best_dps, who = d, act["name"]
                covered = max(nc for _, _, _, nc in touchers) or 1
                speed_slow = None
                for a, b, p in routes:                       # 最慢速度（暴露时间最长）
                    if a == st:
                        sp = ES.get_speed(s["w"].get("key"), mult=min(mults))
                        speed_slow = sp and sp.get("speed")
                if not e or not best_dps or not speed_slow:
                    rows.append({"job": jb["title"], "src": jb["src"], "lane": st,
                                 "enemy": s["w"].get("name"), "arr": s["arr"],
                                 "cls": "存疑", "why": "有人能碰到但 DPS/速度算不出"})
                    continue
                # —— 主挡位：站在路径上、挡得住、且在敌人走到那格之前落地 ——
                # 用**逐秒决斗**（duel_model）替代原先静态的 min(存活, 击杀)：
                # 静态版把满 buff 从 T+0 就生效（乌尔比安被打成 20.5s），
                # 逐秒版才有"技能 70 秒才开"和"天赋自愈随血线切换"（45.1s）。
                path_list = sorted(ln["cells"])
                primary = None
                for t, act, on_path, ncell in touchers:
                    if not on_path or t["time"] > s["arr_max"]:
                        continue
                    bop = CB.load_operator_stats(act["name"], elites.get(act["name"]),
                                                 (V.load_box().get(act["name"]) or {}).get("level"))
                    if not bop or not (bop.get("block_cnt") or 0):
                        continue
                    # 敌人在该格前落地才挡得住
                    try:
                        idx = [c for c in _lane_order(stage, raw, routes, st)].index(
                            tuple(act["location"]))
                    except (ValueError, TypeError):
                        idx = 0
                    spd = ES.get_speed(s["w"].get("key"), mult=min(mults))
                    eta = (s["w"].get("t") or 0) + (idx / spd["speed"] if spd and spd.get("speed") else 0)
                    if t["time"] <= eta and (primary is None or t["time"] < primary[0]["time"]):
                        primary = (t, act, eta)
                if primary and e:
                    t, act, eta = primary
                    du = DM.duel(s["w"].get("key"), act["name"], t_deploy=t["time"],
                                 t_enter=max(t["time"], eta), skill_idx=sk.get(act["name"]),
                                 mastery=max(masteries))
                    if du.get("result") == "enemy_died":
                        cls, why = "守得住", "逐秒决斗：敌人 T+%s 死（挡位剩 %s/%s 血）" % (
                            du["t_death"], du["our_hp_left"], du["our_hp_max"])
                    elif du.get("result") == "blocker_died":
                        cls, why = "存疑", "逐秒决斗：挡位 T+%s 死、敌人被放行（敌剩 %s 血）⇒ 需接力" % (
                            du["t_death"], du["enemy_hp_left"])
                    else:
                        cls, why = "存疑", "逐秒决斗：%s" % du.get("reason") or "未分胜负"
                    rows.append({"job": jb["title"], "src": jb["src"], "lane": st,
                                 "enemy": s["w"].get("name"), "arr": s["arr"], "cls": cls,
                                 "by": act["name"], "duel": du, "why": why})
                    continue
                # 没有挡位（只有射程覆盖）：退回覆盖算术，明确标注
                if not e or not best_dps or not speed_slow:
                    rows.append({"job": jb["title"], "src": jb["src"], "lane": st,
                                 "enemy": s["w"].get("name"), "arr": s["arr"],
                                 "cls": "存疑", "why": "有人能碰到但 DPS/速度算不出"})
                    continue
                exposure = covered / speed_slow
                dmg = best_dps * exposure
                cls = "守得住" if dmg >= (e.get("hp") or 0) else "存疑"
                rows.append({"job": jb["title"], "src": jb["src"], "lane": st,
                             "enemy": s["w"].get("name"), "arr": s["arr"], "cls": cls,
                             "by": who, "dps": round(best_dps, 1),
                             "exposure": round(exposure, 2), "dmg": round(dmg),
                             "hp": e.get("hp"), "why": "仅射程覆盖：%d格×%.2fs×%.1fDPS=%.0f vs 血 %s"
                             % (covered, exposure, best_dps, dmg, e.get("hp")),
                             "tag": "仅射程覆盖（无挡位）"})
                continue
                rows.append({"job": jb["title"], "src": jb["src"], "lane": st,
                             "enemy": s["w"].get("name"), "arr": s["arr"], "cls": cls,
                             "by": who, "dps": round(best_dps, 1),
                             "exposure": round(exposure, 2), "dmg": round(dmg),
                             "hp": e.get("hp"), "why": "覆盖%d格×%.2fs+挡%.1fs=%.1fs ×%.1fDPS=%.0f vs 血 %s"
                             % (covered, covered / speed_slow, block_hold, exposure,
                                best_dps, dmg, e.get("hp")),
                             "block_hold": round(block_hold, 2),
                             "tag": "依赖伤害类型判定" if cls == "守得住" else None})
    if verbose:
        import collections
        c = collections.Counter(r["cls"] for r in rows)
        print("阶段 %s ｜ 作业 %d 份 ｜ 条目 %d（作业×路线×出怪）" % (stage, len(jobs), len(rows)))
        print("  分类：", dict(c))
        print("  ⚠️ 只有「确定漏」是硬结论（不依赖伤害类型/专精/系数）；"
              "「守得住」依赖伤害类型判定；「存疑」需实跑或多挡位接力")
        print()
        per = collections.defaultdict(collections.Counter)
        for r in rows:
            per[r["src"].split("/")[-1]][r["cls"]] += 1
        order = sorted(per.items(), key=lambda kv: (-kv[1]["确定漏"], kv[0]))
        print("  逐份（按确定漏从多到少）：")
        for f, c in order:
            print("     %-46s 确定漏%-3d 存疑%-3d 守得住%-3d %s"
                  % (f[:46], c["确定漏"], c["存疑"], c["守得住"],
                     "✅ 无确定漏" if c["确定漏"] == 0 else ""))
        clean = [f for f, c in order if c["确定漏"] == 0]
        print()
        print("  **零「确定漏」的作业 %d/%d 份**：%s"
              % (len(clean), len(order), ", ".join(clean)))
        print("  ⇒ 这就是从判漏角度看「没有结构性漏」的那批（其余每份都至少有一条谁也碰不到）")
    return rows


if __name__ == "__main__":
    judge_full()
