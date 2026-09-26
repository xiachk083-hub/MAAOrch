#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作业排序器 —— 把"选作业"从**热度排序**升级成 **P(3星) 排序**（2026-09-24 按用户指导实现）

用户给的迭代训练总指导落地成四个模块，全在这个文件里：
  ① 账号快照  `runtime/account_snapshot.json`（从 MAA OperBox 生成的精英化/等级/潜能；
     ⚠️ MAA 读不到专精/模组 ⇒ 快照里标 `mastery_known: false`，专精/模组一律按"未知"处理，
     不作为硬门槛，只进"配方匹配分"当风险项）
  ② 作业配方  `runtime/op_recipes.jsonl`（每份作业一条：自备需求 / 助战需求 / 标签）
  ③ 战绩账本  `runtime/axis_outcomes.jsonl`（event_sweep 写：op_id / 关卡 / 结果 / 借入）
  ④ 关卡画像  `runtime/stage_profiles.json`（同关成功组 vs 失败组，逐特征算区分度）

排序（P(3星) 的可用近似）:
    score = 0.30×配方匹配 + 0.35×本号历史 + 0.15×相似迁移 + 0.05×作者热度 + 0.15×探索加成
    · 硬门槛：自备缺口 ≥2 ⇒ 直接踢掉；缺口 ==1 ⇒ 可打（看借入匹配）
    · 样本 <3 时把"本号历史"权重压到历史的 1/3（防过拟合），差额给探索
输出 `runtime/axis_rank.json` = {"<stage>|<want>": [op_id...]}，供 `event_sweep.mjs` 直接读。

用法:
    python tools/axis_rank.py --build          # 只重建账号快照 + 配方
    python tools/axis_rank.py --rank           # 重建并输出排序
    python tools/axis_rank.py --profiles       # 只算关卡画像
    python tools/axis_rank.py --explain act39side_ex07
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import pathlib
import re
from collections import defaultdict

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
RUNTIME = ROOT / "runtime"
SNAPSHOT = RUNTIME / "account_snapshot.json"
RECIPES = RUNTIME / "op_recipes.jsonl"
OUTCOMES = RUNTIME / "axis_outcomes.jsonl"
PROFILES = RUNTIME / "stage_profiles.json"
RANK = RUNTIME / "axis_rank.json"
SCREEN_DIR = ROOT / "data" / "copilot" / "screening"
PLAN_FILES = [
    RUNTIME / "reports" / "plan_epex.json",
    RUNTIME / "reports" / "plan_leex.json",
    RUNTIME / "reports" / "plan_fixstars.json",
    RUNTIME / "reports" / "plan_1520b.json",
]

W = {"recipe": 0.30, "history": 0.35, "transfer": 0.15, "heat": 0.05, "explore": 0.15}
MIN_SAMPLES = 3          # 样本 < 3 压低历史权重
C_EXPLORE = 0.6          # 探索系数（final = score + c*sqrt(ln N / n)）


# ── ① 账号快照 ────────────────────────────────────────────────────────────
def latest_operbox() -> pathlib.Path | None:
    rows = sorted((ROOT / "data").glob("operbox*.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return rows[0] if rows else None


def build_snapshot() -> dict:
    p = latest_operbox()
    if p is None:
        raise SystemExit("data/ 下没有 operbox*.json")
    d = json.load(open(p, encoding="utf-8"))
    own = d.get("own") or []
    snap = {
        "source": p.name,
        "mastery_known": False,          # MAA OperBox 读不到专精/模组
        "opers": {
            o["name"]: {
                "elite": o.get("elite"),
                "level": o.get("level"),
                "potential": o.get("potential"),
                "rarity": o.get("rarity"),
                "own": True,
            } for o in own
        },
    }
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    return snap


# ── ② 作业配方解析 ────────────────────────────────────────────────────────
REQ_RE = re.compile(r"^(?P<name>[^\s]+)\s*(?:专(?P<mastery>\d+))?\s*(?:模(?P<module>\d+))?")


def parse_req(text: str) -> dict:
    """把 prts 的 `黍 专10 模1` 拆成 {name, mastery, module}。

    `专10` = 技能等级 10 = 专精三（7 级 + M3）⇒ mastery = max(0, 10-7)；
    `专7` = 未专精 ⇒ 0；`模1` = 模组 1 级。
    """
    m = REQ_RE.match(text.strip())
    if not m:
        return {"name": text.strip(), "mastery": None, "module": None}
    return {"name": m.group("name"),
            "mastery": max(0, int(m.group("mastery")) - 7) if m.group("mastery") else None,
            "module": int(m.group("module")) if m.group("module") else None}


def load_candidates() -> dict[str, list[dict]]:
    """从 screening 缓存 + plan 文件里收集候选（同一 id 只留信息最全的那条）。"""
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    for p in list(SCREEN_DIR.glob("*.json")) + PLAN_FILES:
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        for stage, rows in d.items():
            if not stage.startswith("act") or not isinstance(rows, list):
                continue
            for r in rows:
                if not isinstance(r, dict) or "id" not in r:
                    continue
                cur = out[stage].get(r["id"], {})
                cur.update({k: v for k, v in r.items() if v not in (None, [], "")})
                out[stage][r["id"]] = cur
    return {k: list(v.values()) for k, v in out.items()}


def build_recipes(cands: dict[str, list[dict]]) -> list[dict]:
    """写 op_recipes.jsonl 并**返回配方列表**（排序直接吃它，别再拿原始候选）。"""
    RECIPES.parent.mkdir(parents=True, exist_ok=True)
    made: list[dict] = []
    with open(RECIPES, "w", encoding="utf-8") as f:
        for stage, rows in cands.items():
            for r in rows:
                reqs = [parse_req(x) for x in (r.get("requirements") or [])]
                used = r.get("used") or []
                support = None
                title = r.get("title") or ""
                m = re.search(r"([\u4e00-\u9fa5A-Za-z·]+?)(?:单核|双核|三核)", title)
                if m:
                    support = m.group(1)
                tags = []
                for kw in ("摆完挂机", "挂机", "无要求", "单核", "双核", "速刷", "低配"):
                    if kw in title or kw in str(r.get("details") or ""):
                        tags.append(kw)
                rec = {
                    "op_id": f"{stage}_{r['id']}",
                    "关卡": stage,
                    "难度": r.get("difficulty"),
                    "标题": title,
                    "热度": {"赞": r.get("like"), "浏览": r.get("views"), "星级": r.get("rating")},
                    "自备需求": reqs,
                    "自备干员": used,
                    "助战需求": support,
                    "标签": tags,
                    "缺口声明": r.get("missing") or [],
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                made.append(rec)
    return made


# ── ③ 战绩账本 ────────────────────────────────────────────────────────────
def load_outcomes() -> list[dict]:
    rows = []
    if OUTCOMES.exists():
        for line in OUTCOMES.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def history_stats(outcomes: list[dict]):
    """→ (按 op 的历史, 按 干员 的历史)。op 历史用于"这份轴在本关行不行"，
    干员历史用于"这个号手里这些干员，整体三星率如何"（相似迁移用）。"""
    by_op: dict[str, dict] = defaultdict(lambda: {"n": 0, "good": 0})
    by_stage_ops: dict[str, list[list[str]]] = defaultdict(list)
    for o in outcomes:
        # **口径（2026-09-24 修，Mas 审计 → Hermes 复核）：演习/无效行不进战绩。**
        #   `mode == "drill"` 只烧演习券、不产收益 ⇒ 演习 3★ 不是胜绩；
        #   `valid is False` 没有结算页结论 ⇒ 也不是一次尝试。
        #   胜绩 = 3★ 读数 **或** `stone: true`（源石到账是终局证据；结算页星数判据
        #   曾把亮勋章读成 0★，真通关只留下 stone）。与 `tools/plan_mines.py` 同源。
        if o.get("mode") == "drill" or o.get("valid") is False:
            continue
        key = o.get("op_id") or _axis_key(o)
        if not key:
            continue          # 无 op_id、无 id、无 axis 尾号 ⇒ 归不了轴（**别造 `<stage>_None` 桶**）
        st = by_op[key]
        st["n"] += 1
        if (o.get("stars") or 0) >= 3 or o.get("stone") is True:
            st["good"] += 1
        if o.get("stage") and o.get("opers"):
            by_stage_ops[o["stage"]].append(o["opers"])
    return by_op, by_stage_ops


def p_hist(stat: dict) -> tuple[float, int]:
    """拉普拉斯平滑的通过率 + 样本数。"""
    n = stat.get("n", 0)
    good = stat.get("good", 0)
    return (good + 1) / (n + 2), n


# ── ④ 关卡画像（成功组 vs 失败组，逐特征区分度）────────────────────────────
def norm_features(opers: list[str]) -> set[str]:
    return {o for o in opers if o}


def _axis_key(o: dict) -> str:
    """一行战绩的轴键 `<stage>_<轴号>`。

    **2026-09-24 审计（达芬奇 §3）**：旧写法直接 `f"{stage}_{id}"`，而人写的账本行只带
    `axis`（`prts://48182`）、没有 `id` ⇒ 造出幽灵键 `EA-1_None`，**而且两条不同的轴
    （48182 与 48114）会撞进同一个桶** —— 比幽灵键本身更糟。
    这里与 `runtime/plans/_axis_weights.py` 用**同一句尾号解析**（`id` 缺失时取 `axis` 尾号）。
    仍解析不出来 ⇒ 返回空串，调用方跳过（不许再造 `<stage>_None`）。
    """
    jid = str(o.get("id") or "").strip()
    if not jid:
        m = re.search(r"(\d+)\s*$", str(o.get("axis") or ""))
        jid = m.group(1) if m else ""
    return f"{o.get('stage')}_{jid}" if jid else ""


def build_profiles(outcomes: list[dict], ops_by_op: dict[str, list[str]] | None = None) -> dict:
    ops_by_op = ops_by_op or {}
    by_stage: dict[str, list[dict]] = defaultdict(list)
    for o in outcomes:
        # 口径同 history_stats：演习/无效行不参与关卡画像（它们没有「打不过」的语义）
        if o.get("mode") == "drill" or o.get("valid") is False:
            continue
        if not (o.get('opers') or ops_by_op.get(o.get('op_id'))):
            continue
        by_stage[o['stage']].append(o)
    profiles = {}
    for stage, rows in by_stage.items():
        if len(rows) < MIN_SAMPLES:
            continue
        good = [r for r in rows if (r.get("stars") or 0) >= 3 or r.get("stone") is True]
        bad = [r for r in rows if not ((r.get("stars") or 0) >= 3 or r.get("stone") is True)]
        if not good or not bad:
            continue
        feats = set()
        for r in rows:
            feats |= norm_features(r.get("opers") or ops_by_op.get(r.get("op_id")) or [])
        prof = {}
        for ft in feats:
            ua = sum(1 for r in good if ft in (r.get("opers") or ops_by_op.get(r.get("op_id")) or [])) / len(good)
            ub = sum(1 for r in bad if ft in (r.get("opers") or ops_by_op.get(r.get("op_id")) or [])) / len(bad)
            tot = sum(1 for r in rows if ft in (r.get("opers") or ops_by_op.get(r.get("op_id")) or []))
            support = min(tot / 5, 1.0)
            prof[ft] = round((ua - ub) * support, 3)
        profiles[stage] = {"样本": len(rows), "成功": len(good), "失败": len(bad),
                           "特征权重": dict(sorted(prof.items(), key=lambda kv: -abs(kv[1])))}
    PROFILES.write_text(json.dumps(profiles, ensure_ascii=False, indent=1), encoding="utf-8")
    return profiles


# ── 排序 ─────────────────────────────────────────────────────────────────
def score_axis(rec: dict, snap: dict, hist: dict, profiles: dict, N: int) -> dict:
    stage = rec["关卡"]
    op_id = rec["op_id"]
    own = snap.get("opers", {})
    reqs = rec.get("自备需求") or []

    # 硬门槛：缺口 = 自备需求里本号没有的
    missing = []
    for q in reqs:
        nm = q.get("name")
        if not nm:
            continue
        info = own.get(nm)
        if not info:
            missing.append(nm)
    if len(missing) >= 2:
        return {"op_id": op_id, "score": -9.99, "gate": f"缺口{len(missing)}⇒踢",
                "parts": {}, "missing": missing}

    # 配方匹配分：需求越少越好；带"零缺口/挂机"标签加分；声明缺口扣分
    n_req = len(reqs)
    recipe = 0.35 - 0.10 * n_req
    if "摆完挂机" in (rec.get("标签") or []):
        recipe += 0.10
    recipe -= 0.15 * len([m for m in missing])
    recipe -= 0.10 * len(rec.get("缺口声明") or [])

    # 本号历史：这份轴在这个关卡跑过几次、几次三星
    ph, n_op = p_hist(hist["by_op"].get(op_id, {}))
    w_hist = W["history"] * (1.0 if n_op >= MIN_SAMPLES else 1 / 3)
    hist_part = w_hist * ph

    # 相似迁移：这套干员在本号其它关卡的三星率
    ops = rec.get("自备干员") or []
    rates = []
    for s, groups in hist["by_stage_ops"].items():
        if s == stage:
            continue
        for g in groups:
            inter = len(set(ops) & set(g))
            if inter:
                rates.append(inter)
    transfer = W["transfer"] * (min(1.0, sum(rates) / (3 * max(1, len(ops))))) if rates else 0.0

    # 作者热度（只当小权重兜底）
    like = ((rec.get("热度") or {}).get("赞") or 0)
    heat = W["heat"] * min(1.0, math.log10(like + 1) / 4)

    # 画像：关卡特征权重 × 本轴干员
    prof = (profiles.get(stage) or {}).get("特征权重") or {}
    prof_part = 0.0
    if prof:
        prof_part = W["recipe"] * (sum(prof.get(o, 0) for o in ops) / max(1, len(ops)))

    base = W["recipe"] * recipe + hist_part + transfer + heat + prof_part
    explore = C_EXPLORE * math.sqrt(math.log(max(N, 2)) / (1 + n_op))
    return {"op_id": op_id, "score": round(base + explore, 4), "gate": "ok" + (f"(缺{len(missing)})" if missing else ""),
            "parts": {"recipe": round(recipe, 3), "hist": round(ph, 3), "n_op": n_op,
                      "transfer": round(transfer, 3), "heat": round(heat, 3),
                      "prof": round(prof_part, 3), "explore": round(explore, 3)},
            "missing": missing}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--rank", action="store_true")
    ap.add_argument("--profiles", action="store_true")
    ap.add_argument("--explain", default=None)
    a = ap.parse_args()

    snap = build_snapshot() if (a.build or a.rank or not SNAPSHOT.exists()) else json.load(open(SNAPSHOT, encoding="utf-8"))
    cands = load_candidates()
    recipes = build_recipes(cands) if (a.build or a.rank or not RECIPES.exists()) else [
        json.loads(l) for l in RECIPES.read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.build or a.rank:
        print(f"账号快照 {len(snap['opers'])} 人（mastery_known={snap['mastery_known']}）｜配方 {len(recipes)} 条")
    outcomes = load_outcomes()
    # 老账本条目没有 opers（战绩账本是 01:40 才接上的），用配方表按 op_id 回填干员列表 ——
    # 「关卡画像 / 相似迁移」全靠它，没有它就永远是 0。
    ops_by_op = {rec["op_id"]: (rec.get("自备干员") or []) for rec in recipes}
    for o in outcomes:
        o.setdefault("opers", [])
        if not o["opers"]:
            o["opers"] = ops_by_op.get(o.get("op_id"), [])
    profiles = build_profiles(outcomes, ops_by_op)
    print(f"战绩账本 {len(outcomes)} 条｜关卡画像 {len(profiles)} 关（≥{MIN_SAMPLES} 样本才算）")
    if a.profiles and not a.rank:
        return 0

    h_op, h_stage = history_stats(outcomes)
    hist = {"by_op": h_op, "by_stage_ops": h_stage}
    N = max(1, len(outcomes))
    by_stage: dict[str, list[dict]] = defaultdict(list)
    for rec in recipes:
        by_stage[rec["关卡"]].append(rec)
    ranking: dict[str, list[str]] = {}
    detail: dict[str, list[dict]] = {}
    for stage, rows in by_stage.items():
        scored = [score_axis(r, snap, hist, profiles, N) for r in rows]
        scored.sort(key=lambda s: -s["score"])
        ranking[f"{stage}|普通"] = [s["op_id"] for s in scored if s["score"] > -9]
        detail[stage] = scored
    RANK.write_text(json.dumps(ranking, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"排序已写 {RANK}（{sum(len(v) for v in ranking.values())} 条）")
    if a.explain:
        for s in detail.get(a.explain, [])[:8]:
            print(" ", s["op_id"], s["score"], s["gate"], s["parts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
