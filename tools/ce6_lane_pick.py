"""按「封口成本」挑人：把候选干员的 费用 / 阻挡数 / 攻击 / 血 列出来，按每费阻挡与每费输出排序。

由来（2026-09-20 深夜）：CE-6 三路走廊、`maxCost=99`（一局最多花约 99 费），
且要求**零漏** ⇒ 每个封口格都必须有人**真的挡得住**（阻挡数 ≥ 同时到达的敌人数）。
我前几版把 重岳（阻挡 1 的斗士）当封口主力 ✗ —— 一波猎犬就能从旁边流过去。

用法:
    python tools/ce6_lane_pick.py [--box data/operbox_rescane2.json]
"""
from __future__ import annotations

import argparse
import json
import pathlib

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--box", type=pathlib.Path, default=PROJ / "data/operbox_rescane2.json")
    ap.add_argument("--min-elite", type=int, default=2)
    args = ap.parse_args()

    tab = json.loads((PROJ / "data/_character_table_full.json").read_text(encoding="utf-8"))
    own = json.loads(args.box.read_text(encoding="utf-8"))["own"]
    ownmap = {o["name"]: o for o in own}

    rows = []
    for k, v in tab.items():
        if not isinstance(v, dict):
            continue
        n = v.get("name")
        if n not in ownmap:
            continue
        o = ownmap[n]
        if o["elite"] < args.min_elite:
            continue
        ph = v.get("phases") or []
        if not ph:
            continue
        # 取"当前精英阶段"的数值（更贴近实战）
        idx = min(o["elite"], len(ph) - 1)
        frames = (ph[idx] or {}).get("attributesKeyFrames") or [{}]
        d = (frames[-1] or {}).get("data") or {}
        cost = d.get("cost")
        blk = d.get("blockCnt")
        atk = d.get("atk")
        hp = d.get("maxHp")
        rows.append({
            "name": n, "prof": v.get("profession"), "sub": v.get("subProfessionId"),
            "elite": o["elite"], "level": o["level"], "rarity": o["rarity"],
            "cost": cost, "block": blk, "atk": atk, "hp": hp,
        })

    print("== 能封口的（阻挡 ≥2），按 每费阻挡 排序 ==")
    print("%-12s %-8s %-10s %4s %4s %5s %5s %6s" % ("干员", "职阶", "分支", "费", "阻挡", "攻击", "血", "挡/费"))
    cand = [r for r in rows if (r["block"] or 0) >= 2 and r["cost"]]
    for r in sorted(cand, key=lambda r: (-(r["block"] / r["cost"]), -(r["atk"] or 0))):
        print("%-12s %-8s %-10s %4s %4s %5s %5s %6.2f" % (
            r["name"], r["prof"], r["sub"], r["cost"], r["block"], r["atk"], r["hp"], r["block"] / r["cost"]))

    print("\n== 输出位（阻挡 ≤1 但攻击/技能强），按 攻击/费 排序 ==")
    print("%-12s %-8s %-10s %4s %5s %5s" % ("干员", "职阶", "分支", "费", "攻击", "攻/费"))
    out = [r for r in rows if (r["block"] or 0) <= 1 and r["cost"] and (r["atk"] or 0) >= 400]
    for r in sorted(out, key=lambda r: -(r["atk"] / r["cost"]))[:24]:
        print("%-12s %-8s %-10s %4s %5s %5.1f" % (
            r["name"], r["prof"], r["sub"], r["cost"], r["atk"], r["atk"] / r["cost"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
