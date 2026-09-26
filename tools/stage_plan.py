"""BI 关卡选点规划器：算出「堵哪几格 / 高台站哪几格」的候选排名。

为什么要有它（2026-09-21 用户要打 BI-EX-2~8）：每关都要重新选点，靠肉眼看地图+数路线太慢，
而且 CE-6/BI-EX-1 的教训是「堵口必须落在自家输出射程内」。这个工具把两件事量化：
  * 每格「经过的敌人总数」（按波次 count 加权，只算 SPAWN，不算 trap）
  * 每个可部署格的「射程内敌人总数」（切比雪夫距离 R，默认 3 ≈ 常规远程射程）

用法:
    python tools/stage_plan.py --stage act14side_ex02
    python tools/stage_plan.py --stage act14side_ex02 --range 4 --top 12
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
sys.path.insert(0, str(PROJ / "tools"))
import stage_cut as sc  # noqa: E402


def enemy_weights(stage: str):
    """routeIndex → {enemyKey: 数量}，以及每路最早出怪时刻。"""
    lvl = json.loads((PROJ / f"data/_level_{stage}.json").read_text(encoding="utf-8"))
    per_route = collections.defaultdict(collections.Counter)
    first_t = {}
    for w in lvl.get("waves") or []:
        base = float(w.get("preDelay") or 0.0)
        for frag in (w.get("fragments") or []):
            t0 = base + float(frag.get("preDelay") or 0.0)
            for a in (frag.get("actions") or []):
                if a.get("actionType") != "SPAWN":
                    continue
                ri = a.get("routeIndex")
                if ri is None:
                    continue
                per_route[ri][a.get("key")] += int(a.get("count") or 1)
                t = t0 + float(a.get("preDelay") or 0.0)
                first_t[ri] = min(first_t.get(ri, 1e9), t)
    return per_route, first_t, lvl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--range", type=int, default=3, help="输出位射程半径（切比雪夫距离），默认 3")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    tp, T, H, W, walk, build, lvl, paths, starts, ends, flip, skipped, air = sc.load_paths(args.stage)
    per_route, first_t, _ = enemy_weights(args.stage)

    # ⚠️ stage_cut 的 build 只含「地面可部署」（算堵口用），高台不在里面；
    #    选输出位必须自己把**高台(heightType==1 且 buildableType!=0)** 也放进来。
    deploy = {(x, y) for y in range(H) for x in range(W)
              if T[y][x].get("buildableType", 0) and T[y][x].get("tileKey") != "tile_forbidden"}
    ground = {q for q in deploy if T[q[1]][q[0]].get("heightType") == 0}
    high = {q for q in deploy if T[q[1]][q[0]].get("heightType") == 1}

    rw = {i: sum(c.values()) for i, c in per_route.items()}
    density = collections.Counter()
    for i, p in paths.items():
        w = rw.get(i, 0)
        if not w:
            continue
        for q in set(p):
            density[q] += w

    print("关卡 %s（%s）%dx%d  行号%s" % (tp.get("code"), tp.get("name"), W, H,
                                        "翻转" if flip else "一致"))
    total = sum(rw.values())
    print("敌人合计 %d；敌人只走 %d 条有效路线中的 %d 条" % (total, len(paths), len([i for i in paths if rw.get(i)])))

    # 每路：从哪个门出发、几只、什么时候
    print("\n== 各路线（门 → 终点，敌人明细）==")
    for i in sorted(paths):
        if not rw.get(i):
            continue
        p = paths[i]
        det = ", ".join("%s×%d" % (k, v) for k, v in per_route[i].most_common())
        flag = " ✈飞行(堵不住)" if i in air else ""
        print("  r%-2d 门%s → 终点%s  共%2d 只  首只@%.0fs  %s%s"
              % (i, p[0], p[-1], rw[i], first_t.get(i, 0), det, flag))

    # 密度图
    print("\n== 每格「经过敌人总数」==")
    print("     " + "".join("%-3d" % x for x in range(W)))
    for y in range(H):
        row = []
        for x in range(W):
            d = density.get((x, y))
            c = T[y][x]
            if c.get("isStart"):
                s = "S"
            elif c.get("isEnd"):
                s = "E"
            elif c.get("heightType") != 0 or c.get("tileKey") == "tile_forbidden":
                s = "H" if c.get("buildableType") else "#"
            elif d:
                s = str(d)
            else:
                s = "."
            row.append("%-3s" % s)
        print("  %2d " % y + "".join(row))
    print("  数字=经过该格的敌人总数；S红门 E蓝门 H高台 #墙 .=无敌人经过")

    # 堵口候选：地面可部署
    gsort = [(density.get(q, 0), q) for q in ground]
    gsort.sort(key=lambda t: (-t[0], t[1][1], t[1][0]))
    print("\n== 堵口候选（地面可部署，按经过敌人排序，前 %d）==" % args.top)
    print("   " + "  ".join("(%d,%d)=%d" % (q[0], q[1], d) for d, q in gsort[:args.top]))

    # 输出位候选：任意可部署（含高台）
    R = args.range
    def coverage(q):
        x0, y0 = q
        return sum(density.get((x, y), 0) for y in range(y0 - R, y0 + R + 1)
                   for x in range(x0 - R, x0 + R + 1))
    allb = [(coverage(q), q) for q in deploy]
    allb.sort(key=lambda t: (-t[0], t[1][1], t[1][0]))
    print("\n== 输出位候选（射程 %d 内敌人数，前 %d；H=高台）==" % (R, args.top))
    for d, q in allb[:args.top]:
        kind = "高台" if q in high else "地面"
        print("   (%2d,%d) %s 覆盖=%d" % (q[0], q[1], kind, d))

    # 组合建议：先取覆盖最高的高台，再取其射程内经过敌人最多的地面格堵口
    print("\n== 建议的「杀伤区」组合（贪心，仅供参考）==")
    hi_rank = [q for d, q in allb if q in high][:4]
    print("   高台（前 4）：%s" % (", ".join("(%d,%d)" % q for q in hi_rank) or "本关没有高台"))
    ref = hi_rank or [q for _, q in gsort[:1]]
    if ref:
        cand = []
        for q in ground:
            near = min(max(abs(q[0] - h[0]), abs(q[1] - h[1])) for h in ref)
            if near <= R:
                cand.append((density.get(q, 0), near, q))
        cand.sort(key=lambda t: (-t[0], t[1]))
        print("   堵口（在高台射程内、按经过敌人排序）：%s"
              % ", ".join("(%d,%d)=%d" % (q[0], q[1], d) for d, _, q in cand[:8]))
        inside = [q for q in deploy if min(max(abs(q[0] - h[0]), abs(q[1] - h[1])) for h in ref) <= R]
        print("   这些高台能罩住的堵口格共 %d 个" % len(inside))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
