"""算 CE-6 的「敌人真实走位」与「能封住所有路的落点集合」—— 排轴前必须先过这一关。

为什么（2026-09-20 深夜的教训）：原报告只给了"最小顶点割 = (1,1)(1,2)(1,3)"，
但我们的轴把主封锁线放在 **x=2**，而后排 (1,3) 放的是**术师（阻挡 0）** ⇒
y=4 那条路（10 个 route）从 (2,4) 斜着进 (1,3)、再进 (0,3) 的门 —— 我们的线**根本没封住它** ✗。
这就是"开局稳、中后段被打穿"最可能的结构性原因。

本脚本做三件事：
  1. 按 mapData/关卡 tiles 建可走图（heightType==0 才是地面；tile_forbidden / 高台不能走）；
  2. 对每条 route（含 checkpoints 折线）跑 A*（四向 + 允许斜走），得到它**真正踩过的格子**；
  3. 给定一组落点，逐 route 判定"是否被封住"，并打印每条路**第一个被谁挡住**（含未挡住 ⇒ 漏）。

用法:
    python tools/ce6_cut_check.py                        # 用当前推荐落点
    python tools/ce6_cut_check.py --block 2,1 2,2 2,3    # 指定落点
"""
from __future__ import annotations

import argparse
import glob
import heapq
import json
import pathlib

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
STAGE = "wk_melee_6"


def load_tiles():
    pats = [r"D:\Xiach\M-A\MAA\*\resource\Arknights-Tile-Pos",
            str(PROJ / "services" / "maa" / "*" / "resource" / "Arknights-Tile-Pos")]
    for pat in pats:
        for d in glob.glob(pat):
            for f in glob.glob(str(pathlib.Path(d) / f"{STAGE}*.json")):
                if "#f#" not in f:
                    return json.loads(pathlib.Path(f).read_text(encoding="utf-8"))["tiles"]
    raise SystemExit("✗ 找不到 Tile-Pos")


def neighbors(x, y, W, H):
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < W and 0 <= ny < H:
            yield nx, ny


def astar(walk, start, goal, W, H):
    """start/goal 是 (x,y)；走不通则返回 None。"""
    if start == goal:
        return [start]
    pq = [(0, start)]
    g = {start: 0}
    prev = {}
    while pq:
        _, cur = heapq.heappop(pq)
        if cur == goal:
            path = [cur]
            while cur in prev:
                cur = prev[cur]
                path.append(cur)
            return list(reversed(path))
        cx, cy = cur
        for nx, ny in neighbors(cx, cy, W, H):
            if not walk.get((nx, ny)):
                continue
            ng = g[cur] + (1.4 if nx != cx and ny != cy else 1.0)
            if ng < g.get((nx, ny), 1e18):
                g[(nx, ny)] = ng
                prev[(nx, ny)] = cur
                heapq.heappush(pq, (ng + abs(nx - goal[0]) + abs(ny - goal[1]), (nx, ny)))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--block", nargs="*", default=["2,1", "2,2", "2,3", "1,3"],
                    help="落点 x,y（默认=推荐封锁线）")
    args = ap.parse_args()
    blocks = {tuple(int(v) for v in b.split(",")) for b in args.block}

    T = load_tiles()
    H, W = len(T), len(T[0])

    def tile(x, y):
        return T[y][x]

    # 可走：地面(heightType==0) 且不是 tile_forbidden
    walk = {}
    for y in range(H):
        for x in range(W):
            c = tile(x, y)
            walk[(x, y)] = c.get("heightType") == 0 and c.get("tileKey") != "tile_forbidden"

    print("可走图（o=可走, X=不可走）  列 0..%d" % (W - 1))
    for y in range(H):
        print(" y=%d " % y + "".join("o" if walk[(x, y)] else "X" for x in range(W)))
    print()

    lvl = json.loads((PROJ / "data" / f"_level_{STAGE}.json").read_text(encoding="utf-8"))
    routes = lvl["routes"]

    # 每个 route 的折线：start -> checkpoints... -> end；end 不可走时退到最近的"门"
    ends = [(x, y) for (x, y), c in ((k, tile(*k)) for k in walk) if c.get("isEnd")]
    print("官方的门(isEnd):", sorted(ends, key=lambda p: (p[1], p[0])))

    def nearest_end(p):
        return min(ends, key=lambda e: abs(e[0] - p[0]) + abs(e[1] - p[1]))

    paths = {}
    for i, r in enumerate(routes):
        s = (r["startPosition"]["col"], r["startPosition"]["row"])
        e = (r["endPosition"]["col"], r["endPosition"]["row"])
        cps = [(c["position"]["col"], c["position"]["row"]) for c in (r.get("checkpoints") or [])]
        way = [s] + cps + [e]
        full, ok = [], True
        for a, b in zip(way, way[1:]):
            if not walk.get(b):
                b = nearest_end(b)
            seg = astar(walk, a, b, W, H)
            if seg is None:
                ok = False
                break
            full += seg if not full else seg[1:]
        if not ok:
            continue
        paths[i] = full

    # 按"起点行"分组汇总，看每条路踩过哪些列
    print("\n== 每条 route 的路径（起点行 -> 门, 关键格子）==")
    for i, p in sorted(paths.items(), key=lambda kv: (kv[1][0][1], kv[1][0][0])):
        s = p[0]
        if s[1] == 0 and s[0] == 0:
            continue                      # 那条占位 dummy route
        hit = [q for q in p if q in blocks]
        first = p.index(hit[0]) if hit else None
        tail = " → ".join("(%d,%d)" % q for q in p if 0 <= q[0] <= 3)
        print("  r%-2d 起点(y=%d,x=%d) 步%2d  %s%s" % (
            i, s[1], s[0], len(p), tail, "   ✓挡住@%s" % (hit[0],) if hit else "   ✗ 没有任何落点在它路上"))

    print("\n== 封锁判定（落点 %s）==" % sorted(blocks))
    leak = []
    for i, p in sorted(paths.items()):
        s = p[0]
        if s[1] == 0 and s[0] == 0:
            continue
        if not any(q in blocks for q in p):
            leak.append(i)
    print("  未封住的 route：%s" % (leak or "无 ✓ 全部封住"))
    if leak:
        for i in leak[:3]:
            print("    r%d 路径尾部：%s" % (i, " → ".join("(%d,%d)" % q for q in paths[i][-8:])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
