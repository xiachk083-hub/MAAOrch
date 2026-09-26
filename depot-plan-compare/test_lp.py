# -*- coding: utf-8 -*-
"""LP 规划多场景测试: greedy vs LP vs LP纯刷 vs chapter 约束。"""
import sys, json
sys.path.insert(0, r"D:\1-2\MAAOrch")
from services import material_planner as mp

snap = json.loads(open(r"D:\1-2\MAAOrch\data\depot_snapshots\本机0号_20260813_044448.json",
                       encoding="utf-8").read())
cfg = json.loads(open(r"D:\1-2\MAAOrch\config\material_water_levels.json",
                      encoding="utf-8").read())


def show(tag, p):
    print(f"--- {tag} ---")
    print(f"  mode={p.get('plan_mode', 'greedy')} total_ap={p.get('total_ap')} "
          f"fills={len(p.get('gap_fills', []))} locked={len(p.get('locked', []))} "
          f"days={len(p.get('days', []))}")
    for f in p.get("gap_fills", []):
        src = " + ".join(f"{x['stage']}x{x['rounds']}" for x in f.get("farm", [])) or "合成"
        cr = f" + 合{f['craft']['num']}" if f.get("craft") else ""
        print(f"  {f['name']}: {src}{cr} (gain {f['expected_gain']}, {f['ap_cost']} AP)")
    for lk in p.get("locked", []):
        print(f"  LOCKED {lk['name']}: {lk.get('lock_reason', '')} "
              f"craft={bool(lk.get('craft'))}")


cfg1 = dict(cfg); cfg1["plan_mode"] = "greedy"
show("greedy (旧)", mp.plan(snap, cfg1, budget=280))

cfg2 = dict(cfg); cfg2["plan_mode"] = "lp"
show("LP 默认(合成+副产品)", mp.plan(snap, cfg2, budget=280))

cfg3 = dict(cfg); cfg3["plan_mode"] = "lp"; cfg3["allow_craft"] = False
show("LP 纯刷(副产品)", mp.plan(snap, cfg3, budget=280))

show("LP chapter=5 (10-6 不可刷)", mp.plan(snap, cfg2, budget=280, chapter=5))
show("LP chapter=2", mp.plan(snap, cfg2, budget=280, chapter=2))
