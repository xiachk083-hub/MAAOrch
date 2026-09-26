# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"D:\1-2\MAAOrch")
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
print("gaps:", gaps)

# 手动检查: 全部合格关的掉率合计（若 >1 则单关可覆盖）
for m, drops in mat_drops.items():
    print(f"  {m}: 合格关 {drops}")

sol = mp._solve_lp(cand, mat_drops, gaps, name2id, True, snap["named"])
print("sol x:", sol["x"] if sol else None)
print("sol y:", sol["y"] if sol else None)
if sol:
    ok_all = True
    for m, g in gaps.items():
        # y 是成品数(系数1), 直接加
        gain = sum(sol["x"].get(cd, 0) * mat_drops[m].get(cd, 0)
                   for cd in mat_drops.get(m, {})) + sol["y"].get(m, 0)
        status = "OK" if gain >= g - 1e-6 else "VIOLATION!"
        if status != "OK":
            ok_all = False
        print(f"  {m}: gain={gain:.2f} vs gap={g} -> {status}")
    print("ALL OK" if ok_all else "HAS VIOLATION")
