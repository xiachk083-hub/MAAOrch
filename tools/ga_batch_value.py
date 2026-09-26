#!/usr/bin/env python3
"""GA 系列批量数值体检 —— 对每一关跑一遍「地图 + 波次 + 敌人数值 + 本号输出账」。

用法: python tools/ga_batch_value.py [--operbox data/operbox_pasted_0915e.json]
产出: 屏幕上每关一行的体检表 + data/ga_batch_value.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "tools"))

import stage_value as SV  # noqa: E402

STAGES = ([f"act16side_{i:02d}" for i in range(1, 9)] + ["act16side_tr01"]
          + [f"act16side_ex{i:02d}" for i in range(1, 9)])


def stage_report(stage: str, box: dict) -> dict:
    tile = SV.find_tile_file(stage)
    if not tile:
        return {"stage": stage, "error": "no tile file"}
    meta = json.loads(tile.read_text(encoding="utf-8"))
    level = SV.load_level(meta["levelId"], stage)

    spawns = collections.Counter()
    waves = 0
    for w in level.get("waves") or []:
        acts = [a for fr in (w.get("fragments") or []) for a in (fr.get("actions") or [])
                if str(a.get("actionType", "")).upper() == "SPAWN"]
        if acts:
            waves += 1
        for a in acts:
            spawns[a.get("key")] += a.get("count") or 1

    enemies = {}
    total_hp = 0
    for k, n in spawns.items():
        lv = (SV.ENEMIES_BY_KEY.get(k) or [{}])[0].get("enemyData") or {}
        ab = lv.get("attributes") or {}
        e = {"name": SV.unwrap(lv.get("name")) or k, "n": n,
             "hp": SV.unwrap(ab.get("maxHp")) or 0, "atk": SV.unwrap(ab.get("atk")) or 0,
             "df": SV.unwrap(ab.get("def")) or 0, "res": SV.unwrap(ab.get("magicResistance")) or 0,
             "spd": SV.unwrap(ab.get("moveSpeed")), "bat": SV.unwrap(ab.get("baseAttackTime")),
             "desc": (SV.unwrap(lv.get("description")) or "")[:60]}
        enemies[k] = e
        total_hp += e["hp"] * n

    # 本号最强输出 vs 最硬的怪 / 最抗法术的怪
    best = []
    for nm, o in box.items():
        if nm not in SV.N2I:
            continue
        p = SV.panel_at(nm, o["elite"], o["level"])
        kind, idx, ab_, scale, idelta = SV.dmg_kind(nm)
        atk = p["atk"] * (1 + ab_)
        interval = max(0.3, p["interval"] + idelta)
        dps = {k: SV.hit(atk * scale, e, kind) / interval for k, e in enemies.items()}
        best.append({"op": nm, "kind": kind, "lv": f"精{o['elite']}Lv{o['level']}", "dps": dps,
                     "max": max(dps.values()) if dps else 0})
    best.sort(key=lambda r: -r["max"])
    return {"stage": stage, "name": meta.get("name"), "waves": waves,
            "enemies": enemies, "total_hp": total_hp, "best": best[:8],
            "size": [len(meta["tiles"][0]), len(meta["tiles"])]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--operbox", type=pathlib.Path,
                    default=PROJ / "data" / "operbox_pasted_0915e.json")
    args = ap.parse_args()
    box = {o["name"]: o for o in json.loads(args.operbox.read_text(encoding="utf-8")) if o.get("own")}

    out = {}
    print(f"账号：{args.operbox.name}（{len(box)} 名持有）\n")
    for st in STAGES:
        try:
            r = stage_report(st, box)
        except SystemExit as e:
            print(f"{st:<18} 取关卡文件失败：{e}")
            continue
        if r.get("error"):
            print(f"{st:<18} {r['error']}")
            continue
        out[st] = r
        es = " | ".join(f"{e['name']}×{e['n']}(HP{e['hp']}/防{e['df']}/抗{e['res']:g})"
                        for e in r["enemies"].values())
        top = r["best"][0] if r["best"] else None
        print(f"{st:<18}{r['name']:<8} {r['size'][0]}x{r['size'][1]} {r['waves']}波 "
              f"总血{r['total_hp']:<7} 最强输出 {top['op']}({top['kind']},{top['lv']}) {top['max']:.0f} DPS")
        print(f"      敌人：{es}")
        # 每家最强输出对最硬那个怪的表现
        hardest = max(r["enemies"].values(), key=lambda e: e["hp"])
        line = " ".join(f"{b['op']}:{b['dps'].get([k for k,v in r['enemies'].items() if v is hardest][0],0):.0f}"
                        for b in r["best"][:5])
        print(f"      对最硬「{hardest['name']}」(HP{hardest['hp']}/防{hardest['df']}/抗{hardest['res']:g})：{line}")
    (PROJ / "data" / "ga_batch_value.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n明细：{PROJ / 'data' / 'ga_batch_value.json'}")


if __name__ == "__main__":
    main()
