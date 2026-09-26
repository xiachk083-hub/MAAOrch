#!/usr/bin/env python3
"""敌人位置时间轴 —— 「算」侧的产物：第 N 秒谁在第几格。

为什么需要它：MAA 没有任何敌人检测（2026-09-18 查证：MaaCore.dll 里 enemy/敌人 全 0 命中，
ONNX 只有干员检测/技能就绪/部署朝向三个），满血敌人也**没有血条**（放大实测），
所以"场上有谁、在哪"只能从出怪表推。见 docs/CLEARING_AGENT.md §8c。

输入（全本地、零外呼）：
    data/_level_<stage>.json     出怪表(waves→fragments→actions 里的 SPAWN) + routes
    data/_enemy_db.json          移速 / 血量 / 攻防 / 名字
    tools/viz_plan.level(stage)  BFS 路径（8 邻域）+ WAIT_FOR_SECONDS 罚站 + FLY 直线近似
    tools/enemy_speed.py         移速换算（MULT=1.0 **已实测对账**，别改成 options.moveMultiplier 的 0.5）

接口：
    enemies_at(stage, t)                        → [{enemy_id, enemy_name, grid, route, hp, ...}]
    enemy_path(stage, enemy_id, spawn_index=0)  → [{"t", "grid", "wait_s"}, ...] 逐格（含罚站重复格）
    timeline(stage, dt=0.5)                     → 采样表（用户规格的输出格式）

口径（与 enemy_speed.calc_arrival 同一套，不再各写一份）：
  · 到门时刻 = 步数 / 移速 + 罚站秒数；**走到终点格即下场**（视为漏怪）
  · 罚站发生在"链里上一个途经点"（viz_plan 的 waits_seq），位置不另算
  · **不建模击杀** ⇒ 这是「上限口径」：实际场上数量 ≤ 算出来的（被打死就少）
  · 缺移速/缺路径一律标注 reason，**不猜时间**

用法：
    python tools/enemy_timeline.py --stage act51side_01            # 生成采样表并落盘
    python tools/enemy_timeline.py --all                           # 全部 PA 关
    python tools/enemy_timeline.py --stage act51side_01 --at 37    # 查某一时刻场上敌人
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tools"))

import enemy_speed as ES  # noqa: E402
import viz_plan as V  # noqa: E402

OUT_DIR = PROJ / "data" / "enemy_timeline"
DB = PROJ / "data" / "_enemy_db.json"

_cache: dict = {}
_hp_cache: dict | None = None


def _enemy_attrs(path=None) -> dict:
    """{key: {"hp","name","def","mres","atk"}}（level 0；缺就留 None，不猜）。"""
    global _hp_cache
    if _hp_cache is not None:
        return _hp_cache
    p = pathlib.Path(path) if path else DB
    data = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for e in data.get("enemies") or []:
        k = e.get("Key")
        if not k:
            continue
        for ent in (e.get("Value") or []):
            if (ent.get("level") or 0) != 0:
                continue
            ed = ent.get("enemyData") or {}
            a = ed.get("attributes") or {}
            g = lambda n: (a.get(n) or {}).get("m_value") if isinstance(a.get(n), dict) else None
            out[k] = {"name": (ed.get("name") or {}).get("m_value"),
                      "hp": g("maxHp"), "atk": g("atk"), "def": g("def"),
                      "mres": g("magicResistance"),
                      "motion": (ed.get("motion") or {}).get("m_value"),
                      "life_point_reduce": ed.get("lifePointReduce")}
            break
    _hp_cache = out
    return out


def _routes_of(stage: str):
    """routeIndex → 路线。**必须 all_routes=True**：出怪表的 `routeIndex` 索引的是 raw 列表，
    而 `viz_plan.level()` 默认按起点格去重（PA-1 实测 12 条 → 5 条），
    同一出发点下的不同路线走向能差一倍（(1,0) 那组 11 步 vs 21 步）——
    按起点去重会把路径和罚站张冠李戴（2026-09-19 对账时踩到）。"""
    W, H, key_fn, bt, routes, waves = V.level(stage, all_routes=True)
    raw = json.loads((PROJ / "data" / ("_level_%s.json" % stage)).read_text(encoding="utf-8"))
    idx2route = {}
    for a, b, p in routes:
        if p and getattr(p, "route_index", None) is not None:
            idx2route[p.route_index] = (a, b, p)
    return W, H, raw, idx2route


def _spawns(stage: str, speeds=None) -> list[dict]:
    """出怪表 → 每个具体敌人（count 展开）一条：带 spawn_t / 路径 / 每格时刻 / 到门时刻。"""
    if stage in _cache:
        return _cache[stage]
    W, H, raw, idx2route = _routes_of(stage)
    attrs = _enemy_attrs()
    speeds = speeds if speeds is not None else ES.load_enemy_speeds()
    out = []
    for w in raw.get("waves") or []:
        for fr in w.get("fragments") or []:
            for act in fr.get("actions") or []:
                if act.get("actionType") != "SPAWN":
                    continue
                key = act.get("key")
                cnt = int(act.get("count") or 1)
                pre = float(act.get("preDelay") or 0)
                itv = float(act.get("interval") or 1.0)
                ri = act.get("routeIndex")
                rt = idx2route.get(ri)
                sp = ES.get_speed(key, 0, speeds=speeds)
                for n in range(cnt):
                    t0 = round(pre + n * itv, 2)
                    rec = {"enemy_id": key, "spawn_t": t0, "route": ri,
                           "enemy_name": (sp or {}).get("name") or (attrs.get(key) or {}).get("name"),
                           "hp": (attrs.get(key) or {}).get("hp"),
                           "def": (attrs.get(key) or {}).get("def"),
                           "mres": (attrs.get(key) or {}).get("mres"),
                           "path": None, "cells": None, "arrive": None, "reason": None}
                    if not rt:
                        rec["reason"] = "route_missing"
                        out.append(rec)
                        continue
                    start, end, path = rt
                    rec["start"], rec["end"] = list(start), list(end)
                    rec["approx"] = bool(getattr(path, "approx", False))
                    rec["motion"] = getattr(path, "mode", None) or (sp or {}).get("motion")
                    cv = ES.calc_arrival(t0, path, key, speeds=speeds,
                                         approx=rec["approx"], waits=getattr(path, "waits", 0) or 0)
                    rec["reason"] = cv.get("reason")
                    rec["arrive"] = cv.get("arrival")
                    rec["speed"] = cv.get("speed_used")
                    if rec["arrive"] is None:
                        out.append(rec)
                        continue
                    # 每格到达时刻：走 i 步用 i/speed 秒；罚站按 waited 插在对应格上
                    spd = cv["speed_used"] or 1.0
                    wseq = list(getattr(path, "waits_seq", ()) or ())
                    waited, cells = 0.0, []
                    for i, cell in enumerate(path):
                        cells.append({"i": i, "grid": list(cell),
                                      "t": round(t0 + i / spd + waited, 2)})
                        # 站在这一格时先把"这一格上的罚站"记进后面的格子
                        for wc, ws in list(wseq):
                            if tuple(wc) == tuple(cell):
                                waited += float(ws)
                                wseq.remove((wc, ws))
                                break
                    rec["path"] = [list(c) for c in path]
                    rec["cells"] = cells
                    out.append(rec)
    out.sort(key=lambda r: (r["spawn_t"], r["enemy_id"]))
    _cache[stage] = out
    return out


def _cell_at(rec: dict, t: float) -> list | None:
    """t 时刻在第几格。未出场/已到门 → None。"""
    if rec.get("cells") is None or t < rec["spawn_t"] or t > rec["arrive"]:
        return None
    last = rec["cells"][0]["grid"]
    for c in rec["cells"]:
        if c["t"] <= t:
            last = c["grid"]
        else:
            break
    return list(last)


def enemies_at(stage: str, t: float, include_arrived: bool = False) -> list[dict]:
    """t 秒时场上有哪些敌人、分别在第几格（**不含击杀/漏怪的动态，上限口径**）。"""
    out = []
    for rec in _spawns(stage):
        grid = _cell_at(rec, t)
        if grid is None:
            if include_arrived and rec.get("arrive") is not None and t > rec["arrive"]:
                out.append({k: rec[k] for k in ("enemy_id", "enemy_name", "route", "hp")
                            if k in rec} | {"grid": list(rec["end"]), "state": "leaked"})
            continue
        out.append({"enemy_id": rec["enemy_id"], "enemy_name": rec["enemy_name"],
                    "grid": grid, "route": rec["route"], "hp": rec["hp"],
                    "def": rec["def"], "mres": rec["mres"],
                    "t_spawn": rec["spawn_t"], "t_arrive": rec["arrive"],
                    "state": "on_field"})
    return out


def enemy_path(stage: str, enemy_id: str, spawn_index: int = 0) -> list[dict]:
    """某一只敌人（按 enemy_id + 第几次出场）的逐格时间轴（含罚站重复格）。"""
    hits = [r for r in _spawns(stage) if r["enemy_id"] == enemy_id]
    if not hits:
        return []
    rec = hits[min(spawn_index, len(hits) - 1)]
    return [{"t": c["t"], "grid": c["grid"], "i": c["i"]} for c in (rec["cells"] or [])]


def timeline(stage: str, dt: float = 0.5, max_seconds: float = 600.0) -> dict:
    """采样表：每 dt 秒一行 (t, enemy_id, enemy_name, grid, route, hp)。

    `max_seconds` 是采样上限（默认 600s = 10 分钟）：关卡里用
    `WAIT_FOR_SECONDS = 9999` 表示**原地驻守永不离开**（实测 act51side_08 / ex08 的守卫），
    不设上限会算出近 3 小时的表（40k 行 / 单文件十几 MB）。
    """
    recs = _spawns(stage)
    raw_end = max([r["arrive"] for r in recs if r.get("arrive")] or [0.0])
    end = min(raw_end, float(max_seconds))
    rows, t, n = [], 0.0, 0
    while t <= end + 1e-9:
        for e in enemies_at(stage, t):
            rows.append({"t": round(t, 2), "enemy_id": e["enemy_id"],
                         "enemy_name": e["enemy_name"], "grid": e["grid"],
                         "route": e["route"], "hp": e["hp"]})
            n += 1
        t += dt
    meta = {"stage": stage, "dt": dt, "seconds": round(end, 2), "rows": n,
            "spawns": len(recs), "enemies": sorted({r["enemy_id"] for r in recs}),
            "caveat": "不建模击杀；到门即下场。实际场上数量 ≤ 本表（被打死就少）",
            "missing": [{"enemy_id": r["enemy_id"], "t": r["spawn_t"], "reason": r["reason"]}
                        for r in recs if r.get("arrive") is None]}
    if raw_end > end:
        meta["horizon_capped"] = {"raw_seconds": round(raw_end, 2), "cap": max_seconds,
                                  "why": "9999s 罚站 = 原地驻守永不离开（守卫类），采样按上限截断"}
    return {stage: {"timeline": rows, "meta": meta}}


def main() -> None:
    ap = argparse.ArgumentParser(description="敌人位置时间轴")
    ap.add_argument("--stage", default="act51side_01")
    ap.add_argument("--all", action="store_true", help="把 PA 全量（17 关）都生成")
    ap.add_argument("--dt", type=float, default=0.5)
    ap.add_argument("--at", type=float, default=None, help="只查某一时刻")
    ap.add_argument("--max-seconds", type=float, default=600.0, help="采样上限（秒）")
    args = ap.parse_args()

    stages = ([f"act51side_{i:02d}" for i in range(1, 9)]
              + [f"act51side_ex{i:02d}" for i in range(1, 9)] + ["act51side_tr01"]) \
        if args.all else [args.stage]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for s in stages:
        if args.at is not None:
            es = enemies_at(s, args.at)
            print(f"[{s}] t={args.at}s 场上 {len(es)} 个：")
            for e in es:
                print(f"   {e['enemy_name'] or e['enemy_id']:14s} 格{e['grid']} route{e['route']} "
                      f"hp{e['hp']} (T+{e['t_spawn']} 出 → T+{e['t_arrive']} 到门)")
            continue
        tl = timeline(s, dt=args.dt, max_seconds=args.max_seconds)
        meta = tl[s]["meta"]
        p = OUT_DIR / f"{s}.json"
        p.write_text(json.dumps(tl, ensure_ascii=False), encoding="utf-8")
        miss = meta["missing"]
        print(f"[{s}] 敌人 {len(meta['enemies'])} 种 / 出怪 {meta['spawns']} 只 / "
              f"时长 {meta['seconds']}s / 采样 {meta['rows']} 行 → {p.name}"
              + (f"  ⚠️ 缺 {len(miss)}: {miss[:3]}" if miss else ""))


if __name__ == "__main__":
    main()
