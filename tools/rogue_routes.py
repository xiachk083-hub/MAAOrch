#!/usr/bin/env python3
"""肉鸽图：出怪路线 / 到达时刻 / 堵点 —— 全部来自关卡数据（不靠拟合）。

产物（都在 data/ 下）：
  _routes_<n>.png    每张图一张：所有路线折线 + 到达防守点秒数 + 防守点圈
  _choke_<n>.png     每张图一张：按“多少条路线经过 × 到得多早”排出的优先堵点
  routes_table.csv   每行 = 一条(图, 路线)：出怪秒 / 到防守点秒 / 用它的敌人数 / 途经格数

数据链：`data/_level_<图名>.json`（公开源 Kengxxiao/ArknightsGameData 的 roguelike level）
        → `enemy_timeline._spawns()` 给出每条路线的有序格 + 每格到达时刻
        → 投影用 MAA 的 Arknights-Tile-Pos（注意：官方投影对本机肉鸽图约有 10% 缩放偏差 ✗）
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import pathlib
import sys

import cv2
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import annotate_live as AL                                       # noqa: E402
import battle_loop as BL                                         # noqa: E402
import enemy_timeline as ET                                      # noqa: E402

ROOT = HERE.parent
DATA = ROOT / "data"
PAL = [(60, 200, 255), (60, 255, 160), (255, 200, 60), (200, 120, 255),
       (255, 120, 120), (120, 255, 255), (255, 160, 60), (160, 160, 255)]
GAME_LOG = pathlib.Path(r"D:/Xiach/M-A/MAA/MAA-v6.12.2-win-x64/debug/gui.log")


def map_frames() -> dict[str, pathlib.Path]:
    """每张肉鸽图挑一帧“正常帧”（手牌在屏 = 没在拖拽，镜头是基准视角）。"""
    starts = [(l[12:19], l.split("开始战斗: ")[-1].strip()[:20])
              for l in GAME_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
              if len(l) > 21 and l[1:11] == "2026-09-19" and l[12:19] >= "20:42:00" and "开始战斗: " in l]

    def name_at(t: str) -> str | None:
        nm = None
        for st, n in starts:
            if st <= t:
                nm = n
        return nm

    rows = sorted((json.loads(l) for l in (DATA / "samples/rogue/index.jsonl")
                   .read_text(encoding="utf-8").splitlines() if l.strip()), key=lambda r: r["t"])
    best: dict[str, tuple[pathlib.Path, int]] = {}
    for r in rows:
        nm = name_at(r["t"][11:])
        p = pathlib.Path(r["file"])
        if not nm or not p.is_file():
            continue
        if nm not in best or r["cards"] > best[nm][1]:
            best[nm] = (p, r["cards"])
    # **优先"正常帧"（手牌 ≥4 = 没在拖拽）**，但没有时**退而取任何一帧** ✓
    # （2026-09-19：驯兽小屋库里 36 帧全是 cards=0 ⇒ 直接跳过它 ✗；路线图不需要手牌 ✓）
    out, fallback = {}, []
    for k, (p, c) in best.items():
        if c >= 4:
            out[k] = p
        else:
            out[k] = p
            fallback.append(k)
    if fallback:
        print("  （这些图没有手牌帧，用了普通帧：%s）" % "、".join(fallback))
    return out


def chokepoints(L, passages: dict, top: int = 8, adj_weight: float = 0.6,
                spawn_excl: int = 2) -> list:
    """按"能站人 + 别在刷新点门口 + 离防守点近 + 汇流多"排出堵点。

    ⚠️ 2026-09-19 用户第二轮纠正：第一版用 `Σ 敌人数/(1+到达秒/10)` —— **奖励"到得早"**，
    而离刷新点近的格天然就早 ⇒ 排名被拉去**敌人门口** ✗（实测前 8 名里 41/48 个在刷新点 ≤2 步 ✗）。
    现在改成：
      · **排除**"离任一刷新点 ≤ `spawn_excl` 步"的格 ✓（门口不设防 ✓）
      · 每条经过的权重 = `敌人数 / (1 + 离防守点剩余步数/6)` ⇒ **越靠近防守点越值钱** ✓
      · 汇流加成 `×(1 + 0.25×(经过的不同路线数 - 1))` ⇒ 真正的瓶颈 ✓
      · 硬约束不变：候选格必须可部署（`buildable ∈ {1,2,3}`）✓
    若排除后榜为空（短路线图），自动把 `spawn_excl` 降到 1、再降到 0 ✓（并在返回值里带 `relaxed` 标记）。

    passages 的 value 元素 = `(t, n, route_idx, i, route_len)` ✓
    返回 [(score, (col,row), n_blk, n_adj, tag, buildable, height_type), ...]（降序 ✓）
    """
    nb8 = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]

    def _build(excl: int):
        out = []
        for r in range(L.lv.height):
            for c in range(L.lv.width):
                tile = L.lv.at(c, r)
                if not tile or not tile.buildable:            # 放不了人的格不进榜 ✓
                    continue
                blk = passages.get((c, r), [])
                adj = [v for dx, dy in nb8 for v in passages.get((c + dx, r + dy), [])]
                allp = blk + adj
                if not allp:
                    continue
                if excl and min(v[3] for v in allp) <= excl:   # 刷新点门口 ✗
                    continue

                def w(vs):
                    s = 0.0
                    for _t, _n, _rx, _i, _L in vs:
                        to_goal = max(0, _L - 1 - _i)
                        s += _n / (1.0 + to_goal / 6.0)
                    return s

                s_blk, s_adj = w(blk), w(adj)
                n_routes = len({v[2] for v in allp})
                total = (s_blk + adj_weight * s_adj) * (1.0 + 0.25 * (n_routes - 1))
                if total <= 0:
                    continue
                out.append((total, (c, r), len(blk), len(adj),
                            "BLK" if blk else "ADJ", tile.buildable, tile.height_type))
        out.sort(reverse=True)
        return out[:top]

    got = _build(spawn_excl)
    used = spawn_excl
    while len(got) < 3 and used > 0:          # 太短的路线图会被排除干净 ⇒ 逐步放宽（并在打印里说明）
        used -= 1
        got = _build(used)
    if used != spawn_excl:
        print("    （该图路线太短，刷新点排除已放宽到 %d 步 ⇒ 建议数 %d 条）" % (used, len(got)))
    return got


def ensure_level(name: str) -> bool:
    """缺 `data/_level_<图名>.json` 时从公开源补一份（Kengxxiao/ArknightsGameData）。

    路径可从 MAA 的 tile 表文件名推出来：`ro1_n_2_4-obt-roguelike-ro1-level_rogue1_2-4.json`
    → 去掉头段后把前三个 `-` 换成 `/` → `levels/obt/roguelike/ro1/level_rogue1_2-4.json`。
    """
    import urllib.request
    tgt = DATA / ("_level_%s.json" % name)
    if tgt.is_file():
        return True
    from services.ai_agent.percept import ResourcePaths
    ov = json.loads((pathlib.Path(ResourcePaths().tile_pos_dir) / "overview.json")
                    .read_text(encoding="utf-8"))
    hit = sorted((v for v in ov.values() if v.get("name") == name),
                 key=lambda v: 0 if str(v.get("stageId", "")).startswith("ro1_n_") else 1)
    if not hit:
        return False
    rel = "levels/" + hit[0]["filename"].split("-", 1)[1].replace("-", "/", 3)
    for base in ("https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/",
                 "https://gcore.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/"):
        try:
            with urllib.request.urlopen(base + rel, timeout=25) as r:
                data = r.read()
            json.loads(data.decode("utf-8"))
            tgt.write_bytes(data)
            print("  已补下载 %-8s ← %s" % (name, rel))
            return True
        except Exception:                                    # noqa: BLE001
            continue
    return False


def routes_of(stage: str) -> dict:
    """路线号 → {cells, t0, n}（去掉占位路线与没有 cells 的条目）。"""
    out: dict = {}
    for s in ET._spawns(stage):
        if not s.get("cells"):
            continue
        rx = s.get("route")
        t0 = float(s.get("spawn_t") or 0)
        e = out.setdefault(rx, {"cells": s["cells"], "t0": t0, "n": 0})
        e["t0"] = min(e["t0"], t0)
        e["n"] += 1
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = int(args[0]) if args else 99
    if "--fetch-missing" in sys.argv:
        for nm in map_frames():
            ensure_level(nm)
    frames = map_frames()
    print("找到 %d 张图的正常帧" % len(frames))
    table = []
    i = 0
    for nm, f in frames.items():
        try:
            L = BL.Loop(nm, None)
        except Exception as exc:                                 # noqa: BLE001
            print("  跳过 %-8s（%s）" % (nm, str(exc)[:40]))
            continue
        routes = routes_of(nm)
        if not routes:
            print("  跳过 %-8s（没有路线）" % nm)
            continue

        def px(col, row):
            q = L.lv.project(int(col), int(row))
            return None if q is None else (int(q[0] * L.stage_px), int(q[1] * L.stage_px))

        im = cv2.imread(str(f))
        with contextlib.redirect_stdout(io.StringIO()):
            ui, tr, _w, _c = AL.layout_boxes(im)
            cards = AL.measure_cards(im, L, tr)
            grid = AL.measure_grid(im, L)
        if not grid:
            continue
        ui = {k: v for k, v in (ui or {}).items() if "ops" not in k}
        i += 1
        base = AL.render(im, ui, cards, grid, None, layers=("ui", "cards", "grid"), panel=True)
        H, W = base.shape[:2]

        # ① 路线图 + CSV
        canvas = base.copy()
        passages: dict[tuple[int, int], list[tuple[float, int]]] = {}
        for k, (rx, e) in enumerate(sorted(routes.items(), key=lambda kv: kv[1]["t0"])):
            col = PAL[k % len(PAL)]
            pts = [p for p in (px(c["grid"][0], c["grid"][1]) for c in e["cells"]) if p]
            if len(pts) < 2:
                continue
            for a, b in zip(pts, pts[1:]):
                cv2.line(canvas, a, b, col, 3, cv2.LINE_AA)
            cv2.circle(canvas, pts[0], 12, col, -1, cv2.LINE_AA)
            t_end = float(e["cells"][-1].get("t") or 0)
            cv2.putText(canvas, "r%s %.0f>%.0fs x%d" % (rx, e["t0"], t_end, e["n"]),
                        (pts[0][0] - 30, pts[0][1] - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.58, col, 2, cv2.LINE_AA)
            table.append({"idx": i, "map": nm, "route": rx, "spawn_s": round(e["t0"], 1),
                          "arrive_goal_s": round(t_end, 1), "enemies": e["n"], "cells": len(e["cells"])})
            for k_i, c in enumerate(e["cells"]):
                g = tuple(c["grid"])
                passages.setdefault(g, []).append(
                    (float(c.get("t") or 0), e["n"], rx, k_i, len(e["cells"])))
        goals = [(cc, r) for r in range(L.lv.height) for cc in range(L.lv.width)
                 if (t := L.lv.at(cc, r)) and t.is_end]
        for cc, r in goals:
            p = px(cc, r)
            if p:
                cv2.circle(canvas, p, 30, (0, 165, 255), 3, cv2.LINE_AA)
        ends = [(float(e["cells"][-1].get("t") or 0), rx) for rx, e in routes.items()]
        cv2.putText(canvas, "roguelike %s   routes %d   earliest arrival at goal %.0fs (r%d)"
                    % (nm, len(routes), min(ends)[0], min(ends)[1]),
                    (24, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, "one line = one route (ordered cells from level data); r=route idx  t=spawn>goal seconds  x=spawns using it",
                    (24, H - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imwrite(str(DATA / ("_routes_%d.png" % i)), canvas)

        # ② 堵点图（评分逻辑见 chokepoints()：只在能站人的格上排名 ✓）
        choke = base.copy()
        scores = chokepoints(L, passages, top=8)
        for rank, (s, g, nb_, na, tag, bld, hgt) in enumerate(scores[:8], 1):
            p_ = px(g[0], g[1])
            if not p_:
                continue
            col_ring = (40, 40, 235) if tag == "BLK" else (200, 120, 40)
            cv2.circle(choke, p_, 26, col_ring, 3, cv2.LINE_AA)
            cv2.putText(choke, "%d" % rank, (p_[0] - 10, p_[1] + 8), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, col_ring, 2, cv2.LINE_AA)
            kind = "高台" if bld == 2 or (bld == 3 and hgt == 1) else "地面"
            cv2.putText(choke, "%s/%s %.0f" % (tag, kind, s), (p_[0] - 46, p_[1] + 46),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, col_ring, 2, cv2.LINE_AA)
        cv2.putText(choke, "roguelike %s   chokepoints 1..8  (only DEPLOYABLE cells)" % nm,
                    (24, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(choke, "red = BLK (on the route -> blocks)   orange = ADJ (next to route -> attacks)   "
                          "score = sum(enemies/(1+steps_to_goal/6)) x (1+0.25*(routes-1));  spawn-adjacent cells excluded",
                    (24, H - 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imwrite(str(DATA / ("_choke_%d.png" % i)), choke)
        print("  ✓ #%-2d %-8s 路线 %2d ｜ 最早到 %2.0fs ｜ 堵点前 3：%s"
              % (i, nm, len(routes), min(ends)[0],
                 ["%s/%.1f" % (z[4], z[0]) for z in scores[:3]]))
        if i >= limit:
            break

    out = DATA / "routes_table.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=["idx", "map", "route", "spawn_s", "arrive_goal_s", "enemies", "cells"])
        wr.writeheader()
        wr.writerows(table)
    print("✓ %s（%d 行 = 图×路线）" % (out, len(table)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
