# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"D:\1-2\MAAOrch")
import numpy as np
from scipy.optimize import linprog
from services import material_planner as mp

snap = json.loads(open(r"D:\1-2\MAAOrch\data\depot_snapshots\本机0号_20260813_044448.json",
                       encoding="utf-8").read())
cfg = json.loads(open(r"D:\1-2\MAAOrch\config\material_water_levels.json",
                      encoding="utf-8").read())
name2id = mp._name2id()
farm = mp.load_farm_stages()
dm = mp.load_drop_matrix()

cand = {}
for m, stages in farm.items():
    for s in stages:
        code = s["stage"]
        if code in dm:
            cand.setdefault(code, {"ap": dm[code]["ap"]})
mat_drops = {}
for m, stages in farm.items():
    ok = {}
    for s in stages:
        code = s["stage"]
        rate = dm.get(code, {}).get("drops", {}).get(name2id.get(m, ""), 0)
        if rate > 0 and code in cand:
            ok[code] = rate
    if ok:
        mat_drops[m] = ok

gaps = {}
for m, lv in cfg["materials"].items():
    g = max(0, lv - snap["named"].get(m, 0))
    if g > 0:
        gaps[m] = g

mat_names = list(gaps.keys())
codes = sorted(cand.keys())
craft_names = [m for m in mat_names if m in mp.CRAFT_RECIPES]
n_stage, n_craft = len(codes), len(craft_names)
print("n_stage:", n_stage, "n_craft:", n_craft)

c = np.array([cand[cd]["ap"] for cd in codes] + [0.0] * n_craft)
A_ub, b_ub = [], []
for m in mat_names:
    drops = mat_drops.get(m, {})
    row = [drops.get(cd, 0.0) for cd in codes]
    if m in craft_names:
        ci = craft_names.index(m)
        row += [mp.CRAFT_RECIPES[m]["ratio"] if j == ci else 0.0 for j in range(n_craft)]
    else:
        row += [0.0] * n_craft
    # 打印固源岩组和异铁组的约束行
    if m in ("固源岩组", "异铁组", "装置"):
        print(f"\n{m} 约束行 (非零项):")
        for j, v in enumerate(row):
            if v != 0:
                kind = "stage" if j < n_stage else "craft"
                nm = codes[j] if kind == "stage" else craft_names[j - n_stage]
                print(f"   {nm}: {v:.4f}")
        print(f"   b_ub = {-gaps[m]}")
    A_ub.append([-v for v in row])
    b_ub.append(-gaps[m])
bounds = [(0, None)] * n_stage
for m in craft_names:
    t1 = mp.CRAFT_RECIPES[m]["t1"]
    bounds.append((0, max(0, int(snap["named"].get(t1, 0)) // mp.CRAFT_RECIPES[m]["ratio"])))
print("\nbounds:", bounds)

res = linprog(c, A_ub=np.array(A_ub), b_ub=np.array(b_ub), bounds=bounds, method="highs")
print("success:", res.success, "message:", res.message)
if res.success:
    x = res.x
    print("x[10-6-HARD]:", x[codes.index("10-6-HARD")])
    for i, m in enumerate(mat_names):
        gain = sum(x[j] * mat_drops[m].get(cd, 0) for j, cd in enumerate(codes)) + x[n_stage + craft_names.index(m)] if m in craft_names else 0
        print(f"  {m}: gain={gain:.2f} gap={gaps[m]}")
