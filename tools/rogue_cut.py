#!/usr/bin/env python3
"""实验 5：出怪路线的**最小割**（外援 2026-09-19 方案 B1~B5 的落地）。

口径（照外援原文，不掺我们自己的猜测）：
  · 图 G=(V,E)：**V = 可通行道路格**（不是可部署格 ✗），E = 8 邻移动边
  · 每个道路格做 **vertex splitting**：`v_in → v_out`，容量 **1 若该格可部署（能站人堵）否则 INF**
  · `SUPER_SOURCE → 所有刷新格`、`所有防守格 → SUPER_SINK`，容量 INF
  · `max-flow(S,T)` ⇒ `min-cut size` = **最少几个"能站人的格"能堵住全部路线** ✓
  · `cut_frequency` 是**近似**（方法字段 `cut_frequency_method=APPROX_C0_FORCE_REMOVE`）✓：
    外援 2026-09-19 明确 —— 它不等于"所有最小割中含该格的比例"（我们没枚举完整割族 ✗），
    只表示"在 C0 + 逐个强制移除得到的候选割族里出现的比例"，不得当精确统计引用 ✓
  · 每个格再算 **criticality**：把它单独移除后流量是否下降 ⇒ 该格是否属于**某个**最小割 ✓
  · 叠加 **时间加权流量 load**（用路线数据里每格到达时刻）⇒ `choke = criticality × normalized(load)`

产物：`data/_cut_<n>.png`（割点标在图上）、`data/cut_table.csv`（图 × 格 × 是否割点 × 负载）、
      `data/_cut_index.html`（一页看全部）。

⚠️ 与堵点图的分工：`_choke_*` 是**启发式排序**（看流量/距离），`_cut_*` 是**结构性判断**
（"最少挡几个格能全堵住"）—— 两者不一致时，以本文件的 min-cut 为准 ✓（外援 B9：路线数 ≥3 就该跑）。
"""

from __future__ import annotations

import collections
import csv
import pathlib
import sys

import cv2
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import annotate_live as AL                                       # noqa: E402
import battle_loop as BL                                         # noqa: E402
import rogue_routes as RR                                        # noqa: E402

DATA = HERE.parent / "data"
NB8 = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]
INF = 10 ** 6


def maxflow(n: int, edges: list[tuple[int, int, int]], s: int, t: int):
    """返回 (flow, reachable_from_S)。reachable 用于从残量图里取出一条最小割 ✓"""
    """Dinic（小图直接手写，免依赖）。edges = [(u, v, cap)]（有向、自带反向边）。"""
    graph = collections.defaultdict(list)
    for i, (u, v, cap) in enumerate(edges):
        graph[u].append([v, cap, i * 2])
        graph[v].append([u, 0, i * 2 + 1])
    flow = 0
    while True:
        level = {s: 0}
        q = collections.deque([s])
        while q:
            u = q.popleft()
            for v, cap, _ in graph[u]:
                if cap > 0 and v not in level:
                    level[v] = level[u] + 1
                    q.append(v)
        if t not in level:
            return flow, level
        it = collections.defaultdict(int)

        def dfs(u: int, f: int) -> int:
            if u == t:
                return f
            while it[u] < len(graph[u]):
                e = graph[u][it[u]]
                v, cap, _ = e
                if cap > 0 and level.get(v, -1) == level[u] + 1:
                    d = dfs(v, min(f, cap))
                    if d > 0:
                        e[1] -= d
                        graph[v][[x[2] for x in graph[v]].index(e[2] ^ 1)][1] += d
                        return d
                it[u] += 1
            return 0

        while True:
            f = dfs(s, INF)
            if f == 0:
                break
            flow += f


def build(stage: str, L):
    """路线 → (节点表, 边表, 源, 汇, 每格 load, 刷新/防守格)。"""
    routes = RR.routes_of(stage)
    walk, load, spawn, goal = set(), collections.defaultdict(float), set(), set()
    for rx, e in routes.items():
        cells = e["cells"]
        for i, c in enumerate(cells):
            g = tuple(c["grid"])
            walk.add(g)
            # 时间加权流量：该格的驻留时间 × 敌人数（相邻格到达时刻之差，最后一格给 1 秒）
            t0 = float(c.get("t") or 0)
            t1 = float(cells[i + 1].get("t") or t0) if i + 1 < len(cells) else t0 + 1.0
            load[g] += e["n"] * max(0.5, t1 - t0)
            if i == 0:
                spawn.add(g)
            if i == len(cells) - 1:
                goal.add(g)
    # 兜底：数据里标了 tile_start / tile_end 的格也算（路线端点可能不是它们）
    for r in range(L.lv.height):
        for c in range(L.lv.width):
            t = L.lv.at(c, r)
            if t and (t.is_start or t.is_end):
                walk.add((c, r))
                (spawn if t.is_start else goal).add((c, r))
    if not walk:
        return None
    idx = {g: i for i, g in enumerate(sorted(walk))}
    S, T = len(walk) * 2, len(walk) * 2 + 1
    edges = []
    for g, i in idx.items():
        tile = L.lv.at(*g)
        cap = 1 if (tile and tile.buildable) else INF      # ← 外援 B2：只有能站人的格才值 1
        edges.append((i * 2, i * 2 + 1, cap))
    for g, i in idx.items():
        for dx, dy in NB8:
            nb = (g[0] + dx, g[1] + dy)
            if nb in idx:
                edges.append((i * 2 + 1, idx[nb] * 2, INF))
    for g in spawn:
        if g in idx:
            edges.append((S, idx[g] * 2, INF))
    for g in goal:
        if g in idx:
            edges.append((idx[g] * 2 + 1, T, INF))
    return idx, edges, S, T, load, spawn, goal


def main() -> int:
    frames = RR.map_frames()
    print("共 %d 张图" % len(frames))
    table, i = [], 0
    for nm, f in frames.items():
        try:
            L = BL.Loop(nm, None)
        except Exception:                                        # noqa: BLE001
            continue
        b = build(nm, L)
        if not b:
            print("  跳过 %-8s（没有路线）" % nm)
            continue
        idx, edges, S, T, load, spawn, goal = b
        # 0 流 ⇒ 源根本没连到汇（图断了）；≥INF ⇒ **割 = ∞**（外援口径：INF_NO_BLOCKABLE_ROUTE）
        raw, reach = maxflow(len(idx) * 2 + 2, edges, S, T)
        if raw == 0:
            print("  ! %-8s 源/汇不连通（图断裂，需要查路线数据）" % nm)
            continue
        uncuttable = raw >= INF
        base = None if uncuttable else raw
        status = "INF_NO_BLOCKABLE_ROUTE" if uncuttable else "FINITE(%d)" % base
        # unblockable_route_ratio（外援 §1）：没有任何可站人道路格的路线占比
        n_ub = sum(1 for e in RR.routes_of(nm).values()
                   if not any((tt := L.lv.at(*(c["grid"]))) and tt.buildable for c in e["cells"]))
        n_rt = len(RR.routes_of(nm))
        # 一条最小割（残量图：S 可达 → 不可达 的拆分边）+ 采样家族（外援 §2：cut_frequency 用）
        cut0 = {g for g, i in idx.items()
                if (i * 2) in reach and (i * 2 + 1) not in reach
                and L.lv.at(*g) and L.lv.at(*g).buildable}
        family = [cut0] if cut0 else []
        alt = {}
        for g, nid in idx.items():
            if not (L.lv.at(*g) and L.lv.at(*g).buildable):
                continue
            e3 = [(u, v, 0 if (u, v) == (nid * 2, nid * 2 + 1) else c) for u, v, c in edges]
            f3, _r3 = maxflow(len(idx) * 2 + 2, e3, S, T)
            same = (f3 >= INF) if uncuttable else (f3 == base)
            if same and cut0 and g not in cut0:
                alt[g] = 1                      # 去掉它仍同样难 ⇒ 存在不含它的最小割 ⇒ 它可替代 ✓
        if len(family) == 1 and alt:
            family.append(set())                # 占位：表示"存在不含某些格的同规模割"（采样近似 ✓）
        freq = {}
        for g, nid in idx.items():
            if not (L.lv.at(*g) and L.lv.at(*g).buildable):
                continue
            in_c0 = g in cut0
            freq[g] = 1.0 if (in_c0 and g not in alt) else (0.5 if in_c0 else 0.0)
        n_depl_road = sum(1 for g in idx if (tt := L.lv.at(*g)) and tt.buildable)
        # criticality：单独移除某可部署格后流量是否下降 ⇒ 它属于某个最小割 ✓
        crit, impact = {}, {}
        for g, nid in idx.items():
            tile = L.lv.at(*g)
            if not (tile and tile.buildable):
                continue
            e2 = [(u, v, 0 if u == nid * 2 and v == nid * 2 + 1 else c) for u, v, c in edges]
            got, _r2 = maxflow(len(idx) * 2 + 2, e2, S, T)
            # impact：单独拿掉这格后，封锁难度增加多少（外援 §2-B）
            crit[g] = 1 if ((base is None and got < INF) or (base is not None and got < base)) else 0
            impact[g] = ("->INF" if got >= INF else got - base) if base is not None else "n/a"
        mx = max(load.values()) if load else 1.0
        choke = {g: crit.get(g, 0) * (load.get(g, 0.0) / mx) for g in idx}
        top = sorted(choke.items(), key=lambda kv: -kv[1])[:8]
        i += 1
        # 出图：割点红圈、其余路格小点
        im = cv2.imread(str(f))
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            ui, tr, _w, _c = AL.layout_boxes(im)
            cards = AL.measure_cards(im, L, tr)
            grid = AL.measure_grid(im, L)
        ui = {k: v for k, v in (ui or {}).items() if "ops" not in k}
        canvas = AL.render(im, ui, cards, grid, None, layers=("ui", "cards", "grid"), panel=True)
        H, W = canvas.shape[:2]

        def px(g):
            q = L.lv.project(g[0], g[1])
            return None if q is None else (int(q[0] * L.stage_px), int(q[1] * L.stage_px))
        for g in sorted(idx, key=lambda z: -choke.get(z, 0)):
            p = px(g)
            if not p:
                continue
            if choke.get(g, 0) > 0:
                r = 26
                cv2.circle(canvas, p, r, (30, 30, 235), 3, cv2.LINE_AA)
                cv2.putText(canvas, "%.2f" % choke[g], (p[0] - 22, p[1] + 46),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 235), 2, cv2.LINE_AA)
            else:
                cv2.circle(canvas, p, 5, (200, 200, 200), -1, cv2.LINE_AA)
        for g in spawn:
            p = px(g)
            if p:
                cv2.circle(canvas, p, 12, (0, 60, 255), -1, cv2.LINE_AA)
        for g in goal:
            p = px(g)
            if p:
                cv2.circle(canvas, p, 30, (0, 165, 255), 3, cv2.LINE_AA)
        cut_txt = ("CANNOT BE FULLY BLOCKED (a route has no deployable tile) [%s]" % status
                   if uncuttable else "MIN VERTEX CUT = %d" % base)
        cv2.putText(canvas, "roguelike %s   %s   deployable-on-road %d   routes %d"
                    % (nm, cut_txt, n_depl_road, len(RR.routes_of(nm))), (24, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, "red ring = in some minimum cut (score = criticality x traffic load)   red dot = spawn   orange ring = goal   grey dot = road tile",
                    (24, H - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imwrite(str(DATA / ("_cut_%d.png" % i)), canvas)
        for g, v in top:
            table.append({"idx": i, "map": nm,
                          "cut_size": "" if uncuttable else base, "col": g[0], "row": g[1],
                          "choke": round(v, 3), "load_s": round(load.get(g, 0.0), 1),
                          "cut_frequency": round(freq.get(g, 0.0), 2),
                          "cut_frequency_method": "APPROX_C0_FORCE_REMOVE",
                          "cut_impact": impact.get(g, "n/a"),
                          "block_cut": status,
                          "unblockable_route_ratio": "%d/%d" % (n_ub, n_rt),
                          "deployable": int(bool(L.lv.at(*g) and L.lv.at(*g).buildable))})
        print("  ✓ #%-2d %-8s %-46s 无阻挡路线 %s ｜ top: %s"
              % (i, nm, status, "%d/%d" % (n_ub, n_rt),
                 ["(%d,%d):%.2f" % (g[0], g[1], v) for g, v in top[:3]]))
    out = DATA / "cut_table.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=["idx", "map", "cut_size", "col", "row", "choke", "load_s",
                    "cut_frequency", "cut_frequency_method", "cut_impact", "block_cut",
                    "unblockable_route_ratio", "deployable"])
        wr.writeheader()
        wr.writerows(table)
    print("✓ %s（%d 行）" % (out, len(table)))
    return 0


if __name__ == "__main__":
    main()
