#!/usr/bin/env python3
"""按「地图 choke + 敌人数值 + 本号练度」自动生成打法（GA-8 那套的通用版）。

思路（2026-09-15 GA-8 实战验证过）:
    1. 找 choke：离蓝门(终点)最近的可部署地面格 —— 几路汇聚型关卡堵这儿最省人；
    2. 找输出位：choke 周围 3 格内的高台（远程）+ 地面（近战）；
    3. 按数值派活：物理打高法抗/低防，法术打高防/低抗（用 stage_value 的敌人数据现算）；
    4. 从号里挑人：先锋(产费)→choke，重装→第二线，物理/法术输出→就近输出位，医疗→高台。

用法:
    python tools/derive_plan.py --stage act16side_03 --operbox data/operbox_pasted_0915e.json
    python tools/derive_plan.py --all-ga --operbox data/operbox_pasted_0915e.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "tools"))
import stage_value as SV  # noqa: E402
import job_meta as JM     # noqa: E402  （生成器默认值单一来源）

GA_STAGES = ([f"act16side_{i:02d}" for i in range(1, 9)] + ["act16side_tr01"]
             + [f"act16side_ex{i:02d}" for i in range(1, 9)])
GROUND, HIGH = 1, 2


def cells(tiles):
    for r, row in enumerate(tiles):
        for c, cell in enumerate(row):
            yield c, r, cell


def pick_positions(tiles):
    """返回 (choke, 备用地面位[], 高台位[]) —— 按到蓝门的距离排序"""
    ends = [(c, r) for c, r, cell in cells(tiles) if cell.get("isEnd")]
    ground = [(c, r) for c, r, cell in cells(tiles) if cell.get("buildableType") == GROUND
              and cell.get("heightType") == 0]
    high = [(c, r) for c, r, cell in cells(tiles) if cell.get("buildableType") == HIGH]
    if not ends or not ground:
        return None, [], []
    def d(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])
    choke = min(ground, key=lambda g: min(d(g, e) for e in ends))
    others = sorted([g for g in ground if g != choke], key=lambda g: d(g, choke))
    highs = sorted([h for h in high if d(h, choke) <= 3.0], key=lambda h: d(h, choke))
    return choke, others[:3], highs[:4]


def choose_ops(box: dict, enemies: dict) -> dict[str, list[dict]]:
    """按职业与数值挑人：先锋 / 重装 / 物理输出 / 法术输出 / 医疗"""
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for nm, o in box.items():
        if nm not in SV.N2I:
            continue
        prof = SV.CT[SV.N2I[nm]]["profession"]
        p = SV.panel_at(nm, o["elite"], o["level"])
        kind, idx, ab_, scale, idelta = SV.dmg_kind(nm)
        atk = p["atk"] * (1 + ab_)
        interval = max(0.3, p["interval"] + idelta)
        dps = max((SV.hit(atk * scale, e, kind) / interval) for e in enemies.values()) if enemies else 0
        rec = {"name": nm, "lv": f"精{o['elite']}Lv{o['level']}", "kind": kind,
               "dps": round(dps), "score": (o["elite"], o["level"])}
        if prof == "PIONEER":
            out["先锋"].append(rec)
        elif prof == "TANK":
            out["重装"].append(rec)
        elif prof == "MEDIC":
            out["医疗"].append(rec)
        elif kind == "物理":
            out["物理"].append(rec)
        else:
            out["法术"].append(rec)
    for k in out:
        out[k].sort(key=lambda r: (-r["dps"] if k in ("物理", "法术") else 0, -r["score"][0], -r["score"][1]))
    return out


def build(stage: str, box: dict, out_dir: pathlib.Path) -> dict | None:
    tile = SV.find_tile_file(stage)
    if not tile:
        return None
    meta = json.loads(tile.read_text(encoding="utf-8"))
    level = SV.load_level(meta["levelId"], stage)
    enemies = {}
    for w in level.get("waves") or []:
        for fr in (w.get("fragments") or []):
            for a in (fr.get("actions") or []):
                if str(a.get("actionType", "")).upper() != "SPAWN":
                    continue
                lv = (SV.ENEMIES_BY_KEY.get(a["key"]) or [{}])[0].get("enemyData") or {}
                ab = lv.get("attributes") or {}
                enemies[a["key"]] = {"name": SV.unwrap(lv.get("name")) or a["key"],
                                     "hp": SV.unwrap(ab.get("maxHp")) or 0,
                                     "df": SV.unwrap(ab.get("def")) or 0,
                                     "res": SV.unwrap(ab.get("magicResistance")) or 0}
    choke, grounds, highs = pick_positions(meta["tiles"])
    if not choke:
        return None
    pool = choose_ops(box, enemies)

    def first(kind, skip=()):
        for r in pool.get(kind, []):
            if r["name"] not in skip:
                return r
        return None

    plan: list[tuple[dict, tuple[int, int], str]] = []
    used: set[tuple[int, int]] = set()

    def free(cands: list, fallback: list) -> tuple[int, int] | None:
        """取一个没被占的位（同格放两人一定失败 —— 2026-09-15 生成器首版踩过）"""
        for c in list(cands) + list(fallback):
            if c not in used:
                used.add(c)
                return c
        return None
    cost = first("先锋")
    tank = first("重装", skip=(cost["name"],) if cost else ())
    phys = [r for r in pool.get("物理", []) if r["name"] not in {x[0]["name"] for x in plan if x[0]}][:2]
    arts = pool.get("法术", [])[:3]
    heal = first("医疗")
    if cost:
        used.add(choke)
        plan.append((cost, choke, "Down"))
    if tank:
        pos = free(grounds, [choke])
        if pos:
            plan.append((tank, pos, "Down"))
    for i, r in enumerate(phys[:2]):
        pos = free(grounds, [choke])
        if pos:
            plan.append((r, pos, "Left"))
    for i, r in enumerate(arts[:2]):
        pos = free(highs, [])
        if pos:
            plan.append((r, pos, "Down" if i == 0 else "Left"))
    if heal:
        pos = free(highs, grounds)
        if pos:
            plan.append((heal, pos, "Left"))

    seen = set()
    actions = [{"type": "SpeedUp"}]
    opers = []
    for rec, pos, direction in plan:
        if rec["name"] in seen or len(opers) >= 9:
            continue
        seen.add(rec["name"])
        opers.append({"name": rec["name"], "skill": 3, "skill_usage": 1})
        actions.append({"type": "Deploy", "name": rec["name"],
                        "location": [pos[0], pos[1]], "direction": direction})
    actions.append({"type": "SkillDaemon"})
    job = {"version": JM.GEN_JOB_VERSION, "stage_name": stage,
           "minimum_required": JM.GEN_MINIMUM_REQUIRED,   # 单一来源，见 tools/job_meta.py
           "doc": {"title": f"{stage}〔本地推导·堵门多核〕",
                   "details": "按地图 choke + 敌人数值 + 本号练度自动生成（同 GA-8 打法）。真机先演习。"},
           "opers": opers, "groups": [], "actions": actions}
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{stage}_derived.json"
    p.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"stage": stage, "name": meta.get("name"), "file": str(p), "choke": choke,
            "grounds": grounds, "highs": highs,
            "plan": [{"op": r["name"], "lv": r["lv"], "kind": r["kind"], "dps": r["dps"],
                      "pos": list(pos), "dir": d} for r, pos, d in plan]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage")
    ap.add_argument("--all-ga", action="store_true")
    ap.add_argument("--operbox", type=pathlib.Path, default=PROJ / "data" / "operbox_pasted_0915e.json")
    ap.add_argument("--out-dir", type=pathlib.Path, default=PROJ / "data" / "copilot" / "ga_derived")
    args = ap.parse_args()
    box = {o["name"]: o for o in json.loads(args.operbox.read_text(encoding="utf-8")) if o.get("own")}
    stages = GA_STAGES if args.all_ga else [args.stage]
    results = []
    for st in filter(None, stages):
        try:
            r = build(st, box, args.out_dir)
        except SystemExit as e:
            print(f"{st}: 取关卡文件失败 {e}")
            continue
        if not r:
            print(f"{st}: 没有地图/choke，跳过")
            continue
        results.append(r)
        print(f"\n=== {st} {r['name']} choke=[{r['choke'][0]},{r['choke'][1]}] "
              f"备选地面{r['grounds']} 高台{r['highs']}")
        for x in r["plan"]:
            print(f"    {x['op']:<10}{x['lv']:<10}{x['kind']:<4}{x['dps']:>5} DPS  →  [{x['pos'][0]},{x['pos'][1]}] {x['dir']}")
        print(f"    → {pathlib.Path(r['file']).name}")
    (PROJ / "data" / "ga_derived_plans.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
