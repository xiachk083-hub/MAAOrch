"""通用版「封锁线最小割」：给任意 stageId，算出**最少挡哪几格**能封住全部官方路线。

为什么要有通用版（2026-09-21）：CE-6 那份 `ce6_cut_check.py` 是写死那一关的；换关（如 BI-EX-1）就得重写 ✗。
本工具把做法固化下来：
  1. 从 MAA 的 Arknights-Tile-Pos 读地图：可通行 = heightType==0 且非 tile_forbidden；可部署 = buildableType!=0
  2. 从 `data/_level_<stage>.json` 读官方 routes（含 checkpoints），逐条 A* 还原**真实踩过的格子**
  3. 顶点割网络：每格 v_in→v_out（容量 1 若可部署，否则 INF），相邻格 8 邻边 INF，
     SUPER_SOURCE→各路线起点、各路线终点→SUPER_SINK（均 INF）
  4. Dinic 求最大流 ⇒ 割集 = **最少需要几个人、站哪几格**
  5. 逐路线验证：每条路线是否真的被割到（不允许"算出来封住、实际漏"）

用法:
    python tools/stage_cut.py --stage act14side_ex01
    python tools/stage_cut.py --stage wk_melee_6 --block 2,2 2,3     # 也可只验算给定落点
"""
from __future__ import annotations

import argparse
import glob
import heapq
import json
import pathlib
from collections import deque

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
TILEPOS = [r"D:\Xiach\M-A\MAA\*\resource\Arknights-Tile-Pos",
           str(PROJ / "services" / "maa" / "*" / "resource" / "Arknights-Tile-Pos")]


def load_tiles(stage: str):
    for pat in TILEPOS:
        for d in glob.glob(pat):
            for f in glob.glob(str(pathlib.Path(d) / f"{stage}*.json")):
                if "#f#" not in f:
                    return json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
    raise SystemExit("✗ 找不到 Tile-Pos：%s" % stage)


class Dinic:
    def __init__(self, n):
        self.n = n
        self.g = [[] for _ in range(n)]

    def add(self, u, v, cap):
        self.g[u].append([v, cap, len(self.g[v])])
        self.g[v].append([u, 0, len(self.g[u]) - 1])

    def bfs(self, s, t):
        self.lv = [-1] * self.n
        self.lv[s] = 0
        q = deque([s])
        while q:
            u = q.popleft()
            for v, c, _ in self.g[u]:
                if c > 0 and self.lv[v] < 0:
                    self.lv[v] = self.lv[u] + 1
                    q.append(v)
        return self.lv[t] >= 0

    def dfs(self, u, t, f):
        if u == t:
            return f
        while self.it[u] < len(self.g[u]):
            e = self.g[u][self.it[u]]
            v, c = e[0], e[1]
            if c > 0 and self.lv[v] == self.lv[u] + 1:
                d = self.dfs(v, t, min(f, c))
                if d > 0:
                    e[1] -= d
                    self.g[v][e[2]][1] += d
                    return d
            self.it[u] += 1
        return 0

    def maxflow(self, s, t):
        flow = 0
        INF = 10 ** 9
        while self.bfs(s, t):
            self.it = [0] * self.n
            while True:
                f = self.dfs(s, t, INF)
                if f == 0:
                    break
                flow += f
        return flow


def astar(walk, start, goal, W, H):
    if start == goal:
        return [start]
    if start not in walk or goal not in walk:
        return None
    pq = [(0.0, start)]
    g = {start: 0.0}
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
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = cx + dx, cy + dy
                if (nx, ny) not in walk:
                    continue
                ng = g[cur] + (1.4142 if dx and dy else 1.0)
                if ng < g.get((nx, ny), 1e18):
                    g[(nx, ny)] = ng
                    prev[(nx, ny)] = cur
                    heapq.heappush(pq, (ng + abs(nx - goal[0]) + abs(ny - goal[1]), (nx, ny)))
    return None


def line_tiles(a, b):
    """两格之间的直线经过的格子（给**飞行**路线用：飞行单位无视地形，走直线）。"""
    (x0, y0), (x1, y1) = a, b
    n = max(abs(x1 - x0), abs(y1 - y0), 1)
    out = []
    for k in range(n + 1):
        p = (int(round(x0 + (x1 - x0) * k / n)), int(round(y0 + (y1 - y0) * k / n)))
        if not out or out[-1] != p:
            out.append(p)
    return out


def load_paths(stage: str):
    """读地图 + 官方路线，返回 (tp, T, H, W, walk, build, lvl, paths, starts, ends, flip, skipped, air)。

    供 stage_cut.py 与 stage_plan.py 共用（同一份实现，别各自抄一份）。
    air = 飞行路线（出怪口是空中格）的索引集合：它们**不能靠堵口拦住**，算割时要排除。
    """
    tp = load_tiles(stage)
    T = tp["tiles"]
    H, W = len(T), len(T[0])

    def ok(x, y):
        c = T[y][x]
        return c.get("heightType") == 0 and c.get("tileKey") != "tile_forbidden"

    walk = {(x, y) for y in range(H) for x in range(W) if ok(x, y)}
    build = {(x, y) for (x, y) in walk if T[y][x].get("buildableType", 0)}
    lvl = json.loads((PROJ / f"data/_level_{stage}.json").read_text(encoding="utf-8"))

    def score(flip: bool) -> int:
        s = 0
        for r in lvl["routes"]:
            sy, sx = r["startPosition"]["row"], r["startPosition"]["col"]
            ey, ex = r["endPosition"]["row"], r["endPosition"]["col"]
            sy2 = (H - 1 - sy) if flip else sy
            ey2 = (H - 1 - ey) if flip else ey
            if 0 <= sy2 < H and 0 <= sx < W and T[sy2][sx].get("isStart"):
                s += 1
            if 0 <= ey2 < H and 0 <= ex < W and T[ey2][ex].get("isEnd"):
                s += 2
        return s

    flip = score(True) > score(False)

    def tr(row, col):
        return (col, (H - 1 - row) if flip else row)

    starts, ends, paths, skipped, air = set(), set(), {}, [], set()
    for i, r in enumerate(lvl["routes"]):
        s = tr(r["startPosition"]["row"], r["startPosition"]["col"])
        e = tr(r["endPosition"]["row"], r["endPosition"]["col"])
        cps = [tr(c["position"]["row"], c["position"]["col"]) for c in (r.get("checkpoints") or [])]
        # ⚠️ 2026-09-21 实测坑：官方 routes 里混着**退化路线**（起止点相同、或端点根本不在红门/蓝门上，
        #    它们是给非敌人实体用的）。不滤掉的话 SUPER_SOURCE→起点→终点→SINK 会走通一条 INF 边，
        #    最大流变成 1000000+ ⇒ "最小割 1000002 格" 这种鬼结果，逐路线验算也会失败（BI-EX-2/4/5 都中招）。
        def near_marker(p, key):
            """端点允许 1 格偏差：它自己或 8 邻域里有该标记就返回那个格。
            **不要求可通行**——飞行单位的出怪口就在空中格上（如 BI-EX-2 的无人机 (10,1)）。"""
            if not (0 <= p[0] < W and 0 <= p[1] < H):
                return None
            cand = [p] + [(p[0] + dx, p[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            for q in cand:
                if 0 <= q[0] < W and 0 <= q[1] < H and T[q[1]][q[0]].get(key):
                    return q
            return None

        s2 = near_marker(s, "isStart")
        e2 = near_marker(e, "isEnd")
        if s2 is None or e2 is None:
            skipped.append((i, "端点不在红/蓝门 (%s→%s)" % (s, e)))
            continue
        if s2 == e2:
            skipped.append((i, "退化（起止同格 %s）" % (s2,)))
            continue

        def snap(p):
            if p in walk:
                return p
            best, bd = None, 1e9
            for q in walk:
                d = abs(q[0] - p[0]) + abs(q[1] - p[1])
                if d < bd:
                    best, bd = q, d
            return best

        is_air = (s2 not in walk) or (e2 not in walk)
        full = []
        good = True
        if is_air:
            way = [s2] + list(cps) + [e2]
            for a, b in zip(way, way[1:]):
                seg = line_tiles(a, b)
                full += seg if not full else seg[1:]
        else:
            way = [s2] + [snap(c) for c in cps] + [e2]
        full = []
        good = True
        for a, b in zip(way, way[1:]):
            seg = astar(walk, a, b, W, H)
            if seg is None:
                good = False
                break
            full += seg if not full else seg[1:]
        if not good or not full:
            # 走不通 ⇒ 多半是"空中出怪口"（格子在图上算可通行、但被墙孤立，如 BI-EX-2 的无人机 (10,1)）
            # 退化成直线路径并标成飞行路线，否则这条线的敌人会从密度图里凭空消失。
            way = [s2] + list(cps) + [e2]
            full = []
            for a, b in zip(way, way[1:]):
                seg = line_tiles(a, b)
                full += seg if not full else seg[1:]
            if not full:
                skipped.append((i, "画不出路径 (%s→%s)" % (s2, e2)))
                continue
            is_air = True
        paths[i] = full
        if is_air:
            air.add(i)
        starts.add(full[0])
        ends.add(full[-1])
    return tp, T, H, W, walk, build, lvl, paths, starts, ends, flip, skipped, air


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--block", nargs="*", default=None, help="只验算给定落点 x,y ...（不给则算最小割）")
    ap.add_argument("--no-ice", action="store_true", help="把冰面格当成不能站人（避开寒冷/冻结）")
    ap.add_argument("--show-routes", action="store_true")
    ap.add_argument("--map", action="store_true", help="打印地图（红/蓝门、近战位、高台、冰面）")
    args = ap.parse_args()

    tp, T, H, W, walk, build, lvl, paths, starts, ends, flip, skipped, air = load_paths(args.stage)
    print("关卡 %s（%s）地图 %dx%d" % (tp.get("code"), tp.get("name"), W, H))
    print("行号解释：%s" % ("上下翻转" if flip else "与 Tile-Pos 一致"))

    if args.no_ice:
        ice = {(x, y) for (x, y) in build if T[y][x].get("tileKey") == "tile_icestr"}
        build -= ice
        print("（--no-ice：排除 %d 个冰面格 %s）" % (len(ice), sorted(ice)))

    print("有效路线 %d 条；起点 %s；终点 %s" % (len(paths), sorted(starts), sorted(ends)))
    if air:
        print("其中**飞行**路线 %d 条（%s）：无视地形、堵不住，只能靠远程打死"
              % (len(air), sorted(air)))
    if skipped:
        print("过滤掉的无效路线 %d 条：%s" % (len(skipped), "; ".join("r%d %s" % s for s in skipped[:6])))
    if args.map:
        print("\n地图（%d 列 × %d 行；列=x 行=y，y 从上到下）：" % (W, H))
        print("     " + "".join(str(x % 10) for x in range(W)))
        from collections import Counter
        cnt = Counter(p[0] for p in paths.values())
        for y in range(H):
            row = []
            for x in range(W):
                c = T[y][x]
                k = c.get("tileKey")
                ht = c.get("heightType", 0)
                bt = c.get("buildableType", 0)
                if c.get("isStart"):
                    ch = "S"
                elif c.get("isEnd"):
                    ch = "E"
                elif ht == 0:
                    ch = {0: ".", 1: "X", 2: "o", 3: "*"}.get(bt, "?")
                elif ht == 1:
                    ch = "H" if bt else "^"          # H=高台(只能放远程) ^=高台不可部署
                else:
                    ch = "#"                          # 墙/不可通行
                if k == "tile_forbidden":
                    ch = "#"
                elif k == "tile_icestr" and ch not in "#SE":
                    ch = ch.lower() if ch in "XO*" else ("h" if ch == "H" else "~")
                row.append(ch)
            print("  %2d " % y + "".join(row))
        print("  图例：S=红门(出怪) E=蓝门(终点) X=地面近战位 o=地面远程位 *=两者 "
              "H=高台(只放远程) ^=高台不可部署 .=地面不可部署 #=不可通行 小写=冰面(寒冷)")
        print("  各红门出发的路线数：%s" % dict(sorted(cnt.items(), key=lambda t: (t[0][1], t[0][0]))))
    if args.show_routes:
        for i, p in sorted(paths.items()):
            print("  r%-2d %s" % (i, " → ".join("(%d,%d)" % q for q in p)))

    if args.block:
        blocks = {tuple(int(v) for v in b.split(",")) for b in args.block}
        miss = [i for i, p in paths.items() if i not in air and not any(q in blocks for q in p)]
        miss_air = sorted(i for i in air if i in paths)
        print("\n给定落点 %s ⇒ 未封住 %s" % (sorted(blocks), miss or "无 ✓"))
        if miss_air:
            print("   （飞行路线 %s 堵不住，必须远程打死）" % miss_air)
        return 0

    # ---- 最小顶点割（飞行路线不参与：堵口对飞行无效）----
    ground_starts = {paths[i][0] for i in paths if i not in air}
    ground_ends = {paths[i][-1] for i in paths if i not in air}
    if not ground_starts:
        print("\n（没有地面路线，无法算堵口割）")
        return 0
    idx = {}
    for (x, y) in walk:
        idx[(x, y)] = len(idx)
    n = len(idx) * 2 + 2
    S, Tn = n - 2, n - 1
    din = Dinic(n)
    INF = 10 ** 6
    for (x, y), i in idx.items():
        cap = 1 if (x, y) in build else INF
        din.add(2 * i, 2 * i + 1, cap)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                q = (x + dx, y + dy)
                if q in idx:
                    din.add(2 * i + 1, 2 * idx[q], INF)
    for p in ground_starts:
        din.add(S, 2 * idx[p], INF)
    for p in ground_ends:
        din.add(2 * idx[p] + 1, Tn, INF)

    flow = din.maxflow(S, Tn)
    # 割集 = 从 S 可达的集合里、跨到不可达的"入→出"饱和边
    din.bfs(S, Tn)
    cut = []
    for (x, y), i in idx.items():
        if din.lv[2 * i] >= 0 and din.lv[2 * i + 1] < 0:
            cut.append((x, y))
    cut = sorted(cut, key=lambda p: (p[1], p[0]))
    print("\n最小顶点割 = %d 格：%s" % (flow, ["(%d,%d)" % c for c in cut]))
    miss = [i for i, p in paths.items() if i not in air and not any(q in cut for q in p)]
    print("用该割验算：未封住的路线 %s" % (miss or "无 ✓ 全部封住"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
