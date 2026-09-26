"""给某个 stageId 排出「哪条路、几点出怪、几点到门」——决定开局能慢到什么程度。

用法:
    python tools/stage_arrivals.py --stage act14side_ex01
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import pathlib
import sys

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
sys.path.insert(0, str(PROJ / "tools"))
from stage_cut import astar, load_tiles  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    args = ap.parse_args()

    tp = load_tiles(args.stage)
    T = tp["tiles"]
    H, W = len(T), len(T[0])
    walk = {(x, y) for y in range(H) for x in range(W)
            if T[y][x].get("heightType") == 0 and T[y][x].get("tileKey") != "tile_forbidden"}
    lvl = json.loads((PROJ / f"data/_level_{args.stage}.json").read_text(encoding="utf-8"))
    mul = float((lvl.get("options") or {}).get("moveMultiplier") or 1.0)

    db = json.loads((PROJ / "data/_enemy_db.json").read_text(encoding="utf-8"))["enemies"]
    speed, name = {}, {}
    for e in db:
        v = e.get("Value")
        if isinstance(v, dict) and "enemyData" in v:
            a = (v["enemyData"].get("attributes") or {})
            n = v["enemyData"].get("name")
            def g(k):
                x = a.get(k)
                return x.get("m_value") if isinstance(x, dict) else x
            speed[e["Key"]] = float(g("moveSpeed") or 1.0)
            name[e["Key"]] = n

    def score(flip):
        s = 0
        for r in lvl["routes"]:
            sy, sx = r["startPosition"]["row"], r["startPosition"]["col"]
            sy2 = (H - 1 - sy) if flip else sy
            if 0 <= sy2 < H and 0 <= sx < W and T[sy2][sx].get("isStart"):
                s += 1
        return s

    flip = score(True) > score(False)

    def tr(row, col):
        return (col, (H - 1 - row) if flip else row)

    # 每条路线的长度
    length = {}
    for i, r in enumerate(lvl["routes"]):
        s = tr(r["startPosition"]["row"], r["startPosition"]["col"])
        e = tr(r["endPosition"]["row"], r["endPosition"]["col"])
        cps = [tr(c["position"]["row"], c["position"]["col"]) for c in (r.get("checkpoints") or [])]
        def snap(p):
            if p in walk:
                return p
            return min(walk, key=lambda q: abs(q[0] - p[0]) + abs(q[1] - p[1]))
        way = [snap(s)] + [snap(c) for c in cps] + [snap(e)]
        full = []
        for a, b in zip(way, way[1:]):
            seg = astar(walk, a, b, W, H)
            if seg is None:
                full = None
                break
            full += seg if not full else seg[1:]
        if full:
            length[i] = len(full) - 1

    rows = []
    for frag in lvl["waves"][0]["fragments"]:
        for a in (frag.get("actions") or []):
            if a.get("actionType") != "SPAWN":
                continue
            ri = int(a.get("routeIndex") or 0)
            if ri not in length:
                continue
            key = a.get("key")
            v = speed.get(key, 1.0) * mul
            t0 = float(a.get("preDelay") or 0.0)
            t_gate = t0 + length[ri] / v
            rows.append((round(t_gate, 1), round(t0, 1), name.get(key, key), int(a.get("count") or 1), ri, length[ri]))

    rows.sort()
    print("关卡 %s（%s） moveMultiplier=%.2f  行号解释=%s" % (tp.get("code"), tp.get("name"), mul,
                                                       "翻转" if flip else "一致"))
    print("\n== 最早到门的 14 批敌人 ==")
    print("%9s %8s %-14s %5s %6s %6s" % ("到门@", "出怪@", "敌人", "数量", "路线", "路长"))
    for tg, t0, nm, n, ri, ln in rows[:14]:
        print("%9.1f %8.1f %-14s %5d %6d %6d" % (tg, t0, nm, n, ri, ln))
    cnt = collections.Counter()
    for _, _, nm, n, _, _ in rows:
        cnt[nm] += n
    print("\n== 敌人合计 ==")
    for nm, n in cnt.most_common():
        print("   %-14s %d" % (nm, n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
