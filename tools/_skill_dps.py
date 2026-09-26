#!/usr/bin/env python3
"""用「面板 × 技能倍率 × 天赋」算候选干员对 SR-EX-5 三种敌人的 DPS。

教训（2026-09-15）：第一版只算普攻（攻击力−防御），把维什戴尔 S3 的 6 倍多倍率漏掉，
结论偏得离谱（说她 16 DPS）。这里统一按技能 blackboard 精算，法术/物理分开处理：
    物理：max(攻击 − 防御, 攻击×5%)
    法术：攻击 × (1 − 法抗/100)
"""
from __future__ import annotations
import json
import pathlib

PROJ = pathlib.Path(__file__).resolve().parent.parent
CT = json.loads((PROJ / "data" / "_char_table.json").read_text(encoding="utf-8"))
SK = json.loads((PROJ / "data" / "_skill_table.json").read_text(encoding="utf-8"))
N2I = {v["name"]: k for k, v in CT.items() if v.get("name")}

ENEMIES = {
    "刺溜冰淇淋": dict(hp=20000, df=150, res=0.0),
    "沉默收音机": dict(hp=40000, df=1750, res=0.0),
    "不知道钟":   dict(hp=65000, df=99999, res=10.0),
}
# 伤害类型：职业只是默认，技能文案会改写（2026-09-15 实测纠正）
#   结城理/遥：技能写的是法术伤害；酒神：技能是元素伤害（这关的"弱点"之一）
DMG_TYPE = {"维什戴尔":"物理","逻各斯":"法术","结城理":"法术","圣聆初雪":"法术",
            "酒神":"元素","遥":"法术","阿斯卡纶":"物理"}
ARTS_PROF = {"CASTER"}   # 缺省：术师=法术


def panel_at(name: str, elite: int, level: int) -> dict:
    c = CT[N2I[name]]
    ph = c["phases"][min(elite, len(c["phases"]) - 1)]
    f = ph["attributesKeyFrames"]
    lo, hi = f[0]["data"], f[-1]["data"]
    t = 0.0 if f[-1]["level"] == f[0]["level"] else (level - f[0]["level"]) / (f[-1]["level"] - f[0]["level"])
    return {"atk": lo["atk"] + (hi["atk"] - lo["atk"]) * t,
            "interval": lo.get("baseAttackTime") or 1.3,
            "prof": c["profession"], "sub": c["subProfessionId"]}


def bl_of(name: str, skill_idx: int, lv: int) -> dict:
    sid = CT[N2I[name]]["skills"][skill_idx]["skillId"]
    levels = SK[sid]["levels"]
    row = levels[min(max(lv, 1), len(levels)) - 1]
    return {b["key"]: b["value"] for b in (row.get("blackboard") or [])}


def guess_multipliers(bl: dict) -> tuple[float, float, float, int]:
    """从 blackboard 里猜：(攻击力加成, 攻击倍率, 攻击间隔增量, 攻击段数)"""
    atk_buff = next((v for k, v in bl.items() if k == "atk"), 0.0) or 0.0
    atk_scale = next((v for k, v in bl.items() if "atk_scale" in k and "display" not in k), 1.0) or 1.0
    interval_d = next((v for k, v in bl.items() if k == "base_attack_time"), 0.0) or 0.0
    times = 1
    for k, v in bl.items():
        if k in ("times", "attack@times", "cnt", "max_times") and isinstance(v, (int, float)) and 1 < v <= 10:
            times = int(v)
    return float(atk_buff), float(atk_scale), float(interval_d), times


def dmg(atk: float, e: dict, kind: str) -> float:
    """kind ∈ 物理 / 法术 / 元素。元素伤害不吃防御也不吃法抗（只吃元素抗性，本关为 0）。"""
    if kind in ("法术", "元素"):
        return atk * (1 - e["res"] / 100.0)
    return max(atk - e["df"], atk * 0.05)


def main() -> None:
    cands = [("维什戴尔", 2, 2), ("逻各斯", 2, 2), ("结城理", 2, 2), ("圣聆初雪", 2, 2),
             ("酒神", 2, 2), ("遥", 2, 2), ("阿斯卡纶", 2, 2)]
    box = {o["name"]: o for o in json.loads((PROJ / "data" / "operbox_pasted_0915f.json").read_text(encoding="utf-8"))}
    print(f"{'干员':<10}{'职业':<9}{'技能':<14}{'攻击面板':<9}{'倍率':<7}{'间隔':<6}对三种敌人的 DPS（技能7 / 专三）")
    for name, _elite, idx in cands:
        own = box.get(name) or {}
        if not own.get("own"):
            print(f"{name:<10}本号没有")
            continue
        el, lvl = own["elite"], own["level"]
        p = panel_at(name, el, lvl)
        kind = DMG_TYPE.get(name) or ("法术" if p["prof"] in ARTS_PROF else "物理")
        is_arts = kind in ("法术", "元素")
        c = CT[N2I[name]]
        sid = c["skills"][idx]["skillId"] if idx < len(c["skills"]) else None
        sname = (SK[sid]["levels"][0].get("name") if sid else "—") or "—"
        row = []
        for lv in (7, 10):
            bl = bl_of(name, idx, lv) if sid else {}
            ab, asc, idelta, times = guess_multipliers(bl)
            atk = p["atk"] * (1 + ab)
            interval = max(0.2, p["interval"] + idelta)
            if name == "维什戴尔":
                # 天赋1（精2）：主目标×1.15 + 残影爆炸 160%攻击力（S3 把概率提到 attack@prob）；
                # 两段各自减防（合起来只减一次会把防御少算一半）
                comps = [atk * 1.15 * asc, atk * 1.60 * (bl.get("attack@prob") or 0)]
                dp = [sum(dmg(c, e, kind) for c in comps) / interval for e in ENEMIES.values()]
            else:
                hits = atk * asc * times
                dp = [dmg(hits, e, kind) / interval for e in ENEMIES.values()]
            row.append((atk, asc * times, interval, dp))
        a7, a10 = row[0], row[1]
        tag = kind
        print(f"{name:<10}{p['prof'][:8]:<9}{sname[:12]:<14}{p['atk']:<9.0f}{a10[1]:<7.1f}{a10[2]:<6.1f}"
              f"[{tag}] " + " | ".join(f"{k}: {v:.0f}/{w:.0f}" for (k, _), v, w in zip(ENEMIES.items(), a7[3], a10[3])))


if __name__ == "__main__":
    main()
