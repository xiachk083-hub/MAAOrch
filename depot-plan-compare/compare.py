# -*- coding: utf-8 -*-
"""MAAOrch 现状(贪心) vs arkntools 式(LP 全局最小理智) 刷图规划实测对比。

数据:
  - data/stage.json          arknights-toolbox-data 关卡表 (stageId -> {code, cost})
  - data/loc_item.json       arknights-toolbox-data 物品中文名 (itemId -> name)
  - penguin-cn-matrix.json   企鹅物流 CN 掉落矩阵 (实时拉取)
  - MAAOrch config/farm_stages.json / material_water_levels.json / 仓库快照

对比维度:
  1. 同缺口下: 贪心每材料 top1 关 vs LP 全局最小理智(副产品互相抵扣)
  2. 排除关卡后重推: 验证 arkntools 式"不刷某关自动换关"的效果
"""
import json
import math
import sys
import pathlib
from collections import defaultdict

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
MAA = ROOT.parent

# ── 17 目标材料 (与 MAAOrch 水位配置一致) ──
TARGETS = ["固源岩组", "糖组", "聚酸酯组", "异铁组", "酮凝集组", "装置",
           "扭转醇", "轻锰矿", "研磨石", "RMA70-12", "凝胶", "炽合金",
           "晶体元件", "半自然溶剂", "化合切削液", "环烃聚质", "转质盐组"]

# MAAOrch 合成兜底配方 (t1 -> t2, ratio 个 t1 合 1 个 t2)
CRAFT = {
    "固源岩组": {"t1": "固源岩", "ratio": 5, "gold": 200},
    "糖组":     {"t1": "糖",     "ratio": 4, "gold": 200},
    "聚酸酯组":  {"t1": "聚酸酯",  "ratio": 4, "gold": 200},
    "异铁组":   {"t1": "异铁",   "ratio": 4, "gold": 200},
    "酮凝集组": {"t1": "酮凝集",  "ratio": 4, "gold": 200},
    "装置":    {"t1": "破损装置", "ratio": 3, "gold": 100},
}


def load_json(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def build_stage_code_map():
    """stage.json -> {stageId: {code, cost}} (normal+event+retro 全表)"""
    st = load_json(DATA / "stage.json")
    m = {}
    for bucket in ("normal", "event", "retro"):
        for zone, stages in (st.get(bucket) or {}).items():
            for sid, info in stages.items():
                m[sid] = {"code": info["code"], "cost": info["cost"]}
    return m


def build_item_name_map():
    li = load_json(DATA / "loc_item.json")
    # loc_item 是 {itemId: 简称}; 需要完整名用于匹配快照 named
    return li


def build_drop_table(matrix, stage_map):
    """企鹅矩阵 -> {code: {itemId: 期望掉落/次, cost, sampleNum}}
    普通+磨难合并(arkntools 做法), _rep 剔除, 未知 stageId 跳过。"""
    drop = defaultdict(lambda: defaultdict(float))
    sample = defaultdict(int)
    cost_map = {}
    skipped = 0
    for e in matrix:
        sid = e.get("stageId", "")
        base = sid.replace("_rep", "")
        info = stage_map.get(base)
        if not info:
            skipped += 1
            continue
        code = info["code"]
        # 磨难与普通合并统计
        if sid.startswith("tough_"):
            main_sid = sid.replace("tough", "main")
            if main_sid in stage_map:
                continue  # 由 main 条目合并（见下）
        qty = e.get("quantity", 0)
        times = e.get("times", 0)
        if qty == 0 or times == 0:
            continue
        drop[code][e["itemId"]] += qty / times
        sample[code] = max(sample[code], times)
        cost_map[code] = info["cost"]
    # 合并磨难: tough_ 条目并入 main_ 条目(其 stageId 非 main 前缀时单独处理)
    return drop, sample, cost_map, skipped


def main():
    stage_map = build_stage_code_map()
    print(f"[stage] {len(stage_map)} 个 stageId")

    matrix = load_json(ROOT / "penguin-cn-matrix.json")
    if isinstance(matrix, dict):
        matrix = matrix["matrix"]
    print(f"[penguin] {len(matrix)} 条矩阵")

    drop, sample, cost_map, skipped = build_drop_table(matrix, stage_map)
    print(f"[drop] {len(drop)} 个关卡有掉落数据 (跳过未知 stageId {skipped} 条)")

    li = load_json(DATA / "loc_item.json")
    name2id = {v: k for k, v in li.items()}
    # 快照里的全名 -> 工具箱简称 并不一一对应; 用快照 named 直接匹配材料名
    snap = load_json(MAA / "data" / "depot_snapshots" / "本机0号_20260813_044448.json")
    counts = snap["named"]
    cfg = load_json(MAA / "config" / "material_water_levels.json")
    levels = cfg["materials"]
    budget = int(cfg.get("ap_budget_per_day", 280))

    gaps = {m: max(0, levels[m] - counts.get(m, 0)) for m in TARGETS}
    gaps = {m: g for m, g in gaps.items() if g > 0}
    print(f"[gaps] {len(gaps)} 个材料低于水位: " +
          ", ".join(f"{m}-{g}" for m, g in sorted(gaps.items(), key=lambda x: -x[1])))

    # 材料名 -> itemId: 以 MAA item_index.json 为权威 (企鹅/工具箱同体系)
    maa_items = load_json(MAA / "services" / "maa" / "source" / "resource" / "item_index.json")
    maa_name2id = {v["name"]: k for k, v in maa_items.items()}
    NAME_TO_ID = {m: maa_name2id[m] for m in TARGETS}
    NAME_TO_ID.update({v["t1"]: maa_name2id[v["t1"]] for v in CRAFT.values()})
    id2name = {v: k for k, v in NAME_TO_ID.items()}

    # 企鹅矩阵 -> 每关的 17 材料期望掉落向量
    stage_drops = {}  # code -> {itemId: exp/次}
    for code, drops in drop.items():
        vec = {}
        for iid, exp in drops.items():
            if iid in id2name and id2name[iid] in gaps:
                vec[iid] = exp
        if vec:
            stage_drops[code] = vec

    # ── 方案 A: MAAOrch 现状 (贪心 top1, farm_stages.json) ──
    farm = load_json(MAA / "config" / "farm_stages.json")
    plan_a = []
    for m, g in gaps.items():
        stages = farm.get(m) or []
        if not stages:
            plan_a.append({"name": m, "gap": g, "stage": None, "ap": 0,
                           "rounds": 0, "note": "无备选关"})
            continue
        s = stages[0]
        code = s["stage"].replace("-HARD", "")
        ap = s.get("ap") or cost_map.get(code, 18)
        rate = s["rate"]
        rounds = math.ceil(g / rate)
        plan_a.append({"name": m, "gap": g, "stage": code, "ap": ap,
                       "rate": rate, "rounds": rounds, "ap_cost": rounds * ap})
    total_a = sum(p["ap_cost"] for p in plan_a if p.get("ap_cost"))
    print(f"\n=== 方案 A: MAAOrch 现状(贪心 top1) ===")
    print(f"{'材料':<8}{'缺':>5}  {'关':<8}{'掉率':>6}{'次':>5}{'AP':>6}")
    for p in plan_a:
        if p.get("ap_cost"):
            print(f"{p['name']:<8}{p['gap']:>5}  {p['stage']:<8}"
                  f"{p['rate']:>6.3f}{p['rounds']:>5}{p['ap_cost']:>6}")
    print(f"总计 AP: {total_a}")

    # ── 方案 B: LP 全局最小理智 (副产品互相抵扣) ──
    import numpy as np
    from scipy.optimize import linprog

    def solve_lp(gap_dict, exclude=(), allow_craft=True):
        """min Σ cost_s·x_s  s.t. ∀m Σ_s drop_{s,m}·x_s + craft_y_m ≥ gap_m"""
        names = list(gap_dict.keys())
        codes = sorted(c for c in stage_drops if c not in exclude)
        # 变量: 每关 x_s + 每可合成材料 y_m
        craft_names = [m for m in names if m in CRAFT] if allow_craft else []
        n_stage, n_craft = len(codes), len(craft_names)
        n = n_stage + n_craft
        c = np.array([cost_map.get(cd, 18) for cd in codes] + [0.0] * n_craft)
        A_ub, b_ub = [], []
        A_eq, b_eq = [], []
        for i, m in enumerate(names):
            row = [stage_drops.get(cd, {}).get(NAME_TO_ID[m], 0.0) for cd in codes]
            if m in craft_names:
                ci = craft_names.index(m)
                # 合成变量系数=1（1 个成品补 1 个缺口）；ratio 只用于库存上限
                row += [1.0 if j == ci else 0.0 for j in range(n_craft)]
            else:
                row += [0.0] * n_craft
            # Σ drop·x + y ≥ gap  =>  -Σ drop·x - y ≤ -gap
            A_ub.append([-v for v in row])
            b_ub.append(-gap_dict[m])
        bounds = [(0, None)] * n_stage
        for k, m in enumerate(craft_names):
            t1 = CRAFT[m]["t1"]
            have = counts.get(t1, 0)
            ub = have // CRAFT[m]["ratio"]
            bounds.append((0, ub))  # 合成受 t1 库存限制
        res = linprog(c, A_ub=np.array(A_ub), b_ub=np.array(b_ub),
                      bounds=bounds, method="highs")
        if not res.success:
            return None
        x = res.x
        chosen = []
        for j, cd in enumerate(codes):
            if x[j] > 1e-9:
                chosen.append((cd, x[j]))
        crafts = []
        for k, m in enumerate(craft_names):
            if x[n_stage + k] > 1e-9:
                crafts.append((m, x[n_stage + k]))
        # ceil 化后重算实际 AP
        ap_real = 0
        for cd, v in chosen:
            ap_real += math.ceil(v) * cost_map.get(cd, 18)
        return {"cost_lp": res.fun, "cost_ceil": ap_real, "stages": chosen,
                "crafts": crafts}

    # 场景 1: 无排除
    lp1 = solve_lp(gaps)
    print(f"\n=== 方案 B: LP 全局最小理智 (副产品抵扣) ===")
    if lp1:
        print(f"LP 理论最小 AP: {lp1['cost_lp']:.1f}  (ceil 后 {lp1['cost_ceil']})")
        for cd, v in sorted(lp1["stages"], key=lambda t: -t[1]):
            mats = " + ".join(
                f"{id2name[iid]}{stage_drops[cd][iid]*v:+.1f}" if iid in id2name else iid
                for iid in stage_drops[cd])
            print(f"  {cd:<8} × {math.ceil(v)} 次 ({v:.2f})  {cost_map.get(cd,18)} AP/次  "
                  f"→ 期望 {mats}")
        if lp1["crafts"]:
            print("  合成补齐:")
            for m, v in lp1["crafts"]:
                print(f"    {m} × {math.ceil(v)} (需 {CRAFT[m]['t1']} "
                      f"{math.ceil(v)*CRAFT[m]['ratio']})")
    saving = (total_a - lp1["cost_ceil"]) / total_a * 100 if lp1 else 0
    print(f"\nLP vs 贪心: {total_a} AP → {lp1['cost_ceil'] if lp1 else '-'} AP "
          f"(省 {saving:.1f}%)" if lp1 else "")

    # ── 方案 B': 纯刷不合成 (与 MAAOrch 同前提, 仅副产品+全局选关) ──
    lp_nc = solve_lp(gaps, allow_craft=False)
    if lp_nc:
        print(f"\n=== 方案 B': LP 纯刷(不合成, 副产品抵扣) ===")
        print(f"LP 理论最小 AP: {lp_nc['cost_lp']:.1f} (ceil {lp_nc['cost_ceil']})")
        for cd, v in sorted(lp_nc["stages"], key=lambda t: -t[1]):
            print(f"  {cd:<8} × {math.ceil(v)} 次 ({v:.2f})  {cost_map.get(cd,18)} AP/次")
        s2 = (total_a - lp_nc["cost_ceil"]) / total_a * 100
        print(f"同前提 LP vs 贪心: 省 {s2:.1f}%")

    # ── 场景 2: 排除关卡后重推 ──
    print(f"\n=== 场景 2: 排除关卡自动重推 ===")
    # 排除 LP 方案里刷得最多的 2 个关 (模拟"这两关刷不了")
    top2 = [cd for cd, _ in sorted(lp1["stages"], key=lambda t: -t[1])[:2]]
    lp2 = solve_lp(gaps, exclude=set(top2))
    if lp2:
        print(f"排除 {top2}: LP 理论 {lp2['cost_lp']:.1f} AP (ceil {lp2['cost_ceil']}), "
              f"比原案多 {lp2['cost_ceil'] - lp1['cost_ceil']} AP")
        for cd, v in sorted(lp2["stages"], key=lambda t: -t[1]):
            print(f"  {cd:<8} × {math.ceil(v)} 次 ({v:.2f})  {cost_map.get(cd,18)} AP/次")
        if lp2["crafts"]:
            for m, v in lp2["crafts"]:
                print(f"  合成: {m} × {math.ceil(v)}")
    # 排除贪心方案里 AP 最大的关
    top_a = max((p for p in plan_a if p.get("ap_cost")), key=lambda p: p["ap_cost"])
    lp3 = solve_lp(gaps, exclude={top_a["stage"]})
    if lp3:
        print(f"\n排除贪心首选 {top_a['stage']} (AP 最大): LP 重推 {lp3['cost_ceil']} AP "
              f"(原贪心 {total_a}, LP 原案 {lp1['cost_ceil']})")
        for cd, v in sorted(lp3["stages"], key=lambda t: -t[1])[:6]:
            print(f"  {cd:<8} × {math.ceil(v)} 次 ({v:.2f})")

    # ── 掉率核对: farm_stages.json vs 企鹅矩阵 ──
    print(f"\n=== 掉率核对: MAAOrch farm_stages.json top1 vs 企鹅矩阵 ===")
    for m in TARGETS:
        stages = farm.get(m) or []
        if not stages:
            continue
        s = stages[0]
        code = s["stage"].replace("-HARD", "")
        p = drop.get(code, {}).get(NAME_TO_ID.get(m, ""), None)
        mark = "✓" if p is not None and abs(p - s["rate"]) < 0.02 else "✗"
        print(f"  {m:<8} {s['stage']:<12} MAAOrch={s['rate']:.3f} "
              f"企鹅={p if p is not None else 'N/A'} {mark}")

    # ── 落盘 ──
    out = {"gaps": gaps, "plan_a": plan_a, "total_a_ap": total_a,
           "lp1": {k: (v if k != "stages" else [[c, round(x, 3)] for c, x in v])
                   for k, v in lp1.items()} if lp1 else None,
           "excluded": top2,
           "lp2": {k: (v if k != "stages" else [[c, round(x, 3)] for c, x in v])
                   for k, v in lp2.items()} if lp2 else None}
    (ROOT / "compare-result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已存: {ROOT / 'compare-result.json'}")


if __name__ == "__main__":
    main()
