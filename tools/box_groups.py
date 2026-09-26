"""账号分组：按 box 指纹算相似度归组，**同组账号共享匹配结果**（省掉重复的池子排序）。

由来（2026-09-16 用户《作业系统 · 总指导》§七）：账号一多，每个号都把 319 份作业重新算一遍
"缺不缺人/练度够不够"是纯浪费 —— 相似度 0.85 以上的号，能用的作业集合几乎一致。

    data/box_groups.json           ← 分组结果 + 每组代表号 + 组内相似度
    data/rules/group_candidates.json ← 每组每关的候选**顺序**（缓存的只是顺序，不是结论）

⚠️ **缓存只共享"顺序"，绝不共享"结论"**：同组 ≠ 同一个号。作业能不能跑，最后仍要拿**这个号**的
box 逐条核硬需求（缺人/练度/技能槽）。所以 `workspace.candidates_for(group_cache=True)` 拿缓存
排完序后，**每个号照样重新算一遍 missing** —— 缓存省的是"挑哪些作业值得看"，不是"这个号能不能跑"。
把结论也缓存了，就会出现"同组里练度差一点的号被判成能跑"的静默错误。

两个相似度口径：
  · `all`（默认）= 拥有干员集合的 Jaccard —— 按定义就是"两个号有多少人重合"
  · `e2`          = 精二干员集合的 Jaccard —— 满编作业要的是精二，没有精二的人不算数
两类都看，冲突时以 `all` 为准（用户给的阈值 0.85 是挂在"box 相似度"上的）。
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
PROJ = _HERE.parent
sys.path.insert(0, str(_HERE))
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

OUT = PROJ / "data" / "box_groups.json"
CACHE = PROJ / "data" / "rules" / "group_candidates.json"


def fingerprint(box_path) -> dict:
    """box → {name: elite}（只留 own；原始键是 `elite` 不是 `e`）。"""
    d = json.loads(pathlib.Path(box_path).read_text(encoding="utf-8"))
    own = d.get("own") or d.get("own_opers") or (d if isinstance(d, list) else [])
    return {o["name"]: (o.get("elite") or 0) for o in own
            if isinstance(o, dict) and o.get("own") and o.get("name")}


def similarity(a: dict, b: dict, mode: str = "all") -> float:
    """Jaccard：两个 box 的干员集合重合度。**空集返回 1.0 是错的**，这里返回 0。"""
    if mode == "e2":
        a = {n for n, e in a.items() if e >= 2}
        b = {n for n, e in b.items() if e >= 2}
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def group(boxes: dict, threshold: float = 0.85, mode: str = "all") -> list[dict]:
    """贪心归组（与规格一致）：放进**第一个**相似度 > 阈值的组。

    每组记 `rep`（代表号，也是缓存键）与组内**最小**相似度 —— 只报"平均相似"会把
    "组里有一个号只是勉强够线"这件事盖掉，而那个号恰恰是最容易被共享结论坑到的。
    """
    groups: list[dict] = []
    for acct in sorted(boxes):
        fp = boxes[acct]
        placed = False
        for g in groups:
            s = similarity(fp, g["fp"], mode)
            if s > threshold:
                g["accounts"].append(acct)
                g["min_sim"] = min(g["min_sim"], s)
                placed = True
                break
        if not placed:
            groups.append({"rep": acct, "fp": fp, "accounts": [acct], "min_sim": 1.0})
    for g in groups:
        g["id"] = g["rep"]
        g["n"] = len(g["accounts"])
        g["n_ops"] = len(g["fp"])
    return groups


def build(accounts: list[str] | None = None, threshold: float = 0.85, mode: str = "all",
          write: bool = True) -> dict:
    import workspace as WSP
    accts = accounts or WSP.list_accounts()
    boxes, missing = {}, []
    for a in accts:
        p = WSP.box_path(a)
        if p:
            boxes[a] = fingerprint(p)
        else:
            missing.append(a)
    groups = group(boxes, threshold, mode)
    out = {"threshold": threshold, "mode": mode, "n_accounts": len(boxes),
           "groups": groups, "missing_box": missing,
           "pairs": {("%s|%s" % (a, b)): round(similarity(boxes[a], boxes[b], mode), 4)
                     for i, a in enumerate(sorted(boxes)) for b in sorted(boxes)[i + 1:]}}
    if write and groups:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def load() -> dict:
    if not OUT.is_file():
        return {"groups": [], "threshold": 0.85, "mode": "all", "pairs": {}}
    return json.loads(OUT.read_text(encoding="utf-8"))


def group_of(account: str, data: dict | None = None) -> str | None:
    """账号 → 组 id（= 代表号）。没分组时返回 None（调用方应退回逐号自算）。"""
    for g in (data or load()).get("groups") or []:
        if account in g["accounts"]:
            return g["id"]
    return None


def load_cache() -> dict:
    if not CACHE.is_file():
        return {}
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: dict) -> pathlib.Path:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return CACHE


def get_cached_order(stage: str, account: str) -> list[str] | None:
    """这一关有没有可用的缓存顺序（按组）。没有 → None，调用方自己排。"""
    g = group_of(account)
    if not g:
        return None
    return ((load_cache().get(g) or {}).get(stage) or {}).get("order")


def put_cached_order(stage: str, account: str, order: list[str], meta: dict | None = None) -> None:
    g = group_of(account)
    if not g:
        return
    c = load_cache()
    c.setdefault(g, {})[stage] = dict(meta or {}, order=list(order))
    save_cache(c)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="账号分组：box 指纹 + 相似度归组")
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument("--mode", choices=("all", "e2"), default="all")
    ap.add_argument("--account", action="append", default=None, help="只看这些号（可重复）")
    a = ap.parse_args(argv)
    d = build(a.account, a.threshold, a.mode)
    print("账号 %d 个 → %d 组（阈值 %.2f，口径 %s）"
          % (d["n_accounts"], len(d["groups"]), d["threshold"], d["mode"]))
    for g in d["groups"]:
        print("  组 %-10s %d 个号 ｜ %d 人 ｜ 组内最小相似 %.3f ｜ %s"
              % (g["id"], g["n"], g["n_ops"], g["min_sim"], "、".join(g["accounts"])))
    if d.get("pairs"):
        print("  两两相似度：" + "；".join("%s %.3f" % (k, v) for k, v in d["pairs"].items()))
    if d.get("missing_box"):
        print("  ! 没有 box 的账号（进不了组）：%s" % "、".join(d["missing_box"]))
    print("→ %s" % OUT.relative_to(PROJ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
