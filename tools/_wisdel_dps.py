#!/usr/bin/env python3
"""精算 维什戴尔 S3 对 SR-EX-5 三种敌人的 DPS（含天赋1 残影爆炸）。

2026-09-15 用户质疑："维什戴尔真的打不动吗，你算一下三技能的 dps" —— 上一版我只算了
基础攻击（普通攻击 = 攻击力 − 防御，否则 5% 保底），漏了技能倍率和天赋，结论偏了。
这里按游戏数据精算：面板 → 技能倍率 → 天赋加成 → 每次命中伤害 → 攻速 → DPS。
"""
from __future__ import annotations
import json
import pathlib

PROJ = pathlib.Path(__file__).resolve().parent.parent
CT = json.loads((PROJ / "data" / "_char_table.json").read_text(encoding="utf-8"))
SK = json.loads((PROJ / "data" / "_skill_table.json").read_text(encoding="utf-8"))
NAME2ID = {v["name"]: k for k, v in CT.items() if v.get("name")}

# SR-EX-5 三种敌人（普通难度数值，来自关卡/敌人库）
ENEMIES = {
    "刺溜冰淇淋": dict(hp=20000, df=150, res=0.0, note="普通"),
    "沉默收音机": dict(hp=40000, df=1750, res=0.0, note="高防，弱法术/元素"),
    "不知道钟":   dict(hp=65000, df=99999, res=10.0, note="免物理(防99999)，须阻挡"),
}


def phys(atk: float, df: float) -> float:
    return max(atk - df, atk * 0.05)


def arts(atk: float, res: float) -> float:
    return atk * (1 - res / 100.0)


def panel(name: str, elite: int, level: int) -> dict:
    c = CT[NAME2ID[name]]
    ph = c["phases"][min(elite, len(c["phases"]) - 1)]
    f = ph["attributesKeyFrames"]
    lo, hi = f[0]["data"], f[-1]["data"]
    t = 0.0 if f[-1]["level"] == f[0]["level"] else (level - f[0]["level"]) / (f[-1]["level"] - f[0]["level"])
    return {
        "atk": lo["atk"] + (hi["atk"] - lo["atk"]) * t,
        "interval": lo.get("baseAttackTime") or 1.3,
        "atk_rate": (lo.get("attackSpeed") or 100) / 100.0,
    }


def skill_bl(name: str, idx: int, lv: int) -> dict:
    c = CT[NAME2ID[name]]
    sid = c["skills"][idx]["skillId"]
    levels = SK[sid]["levels"]
    row = levels[min(max(lv, 1), len(levels)) - 1]      # 技能表按月顺序存 1..10 级，无 level 字段
    return {b["key"]: b["value"] for b in (row.get("blackboard") or [])}


def wisdel_s3(level: int) -> dict:
    """维什戴尔 S3「爆裂黎明」：面板×(1+atk)，攻击倍率 attack@atk_scale_3，
    天赋1 精2 对主目标 ×1.15，残影爆炸 160% 攻击力（S3 把发动概率提到 attack@prob）。
    攻击间隔 = 基础间隔 + base_attack_time（S2 是 -0.7 的缩短，S3 是 +2.9 的加长）。"""
    p = panel("维什戴尔", 2, 90)
    bl = skill_bl("维什戴尔", 2, level)
    atk = p["atk"] * (1 + bl["atk"])
    main = atk * 1.15 * bl["attack@atk_scale_3"]
    boom = atk * 1.60 * bl["attack@prob"]
    interval = p["interval"] + bl.get("base_attack_time", 0.0)
    return dict(atk=atk, main=main, boom=boom, interval=interval,
                ammo=bl.get("attack@trigger_time", 0), p=p, bl=bl)


def main() -> None:
    p = panel("维什戴尔", 2, 90)
    print(f"面板（精2 Lv90，数据表基准，未含信赖/模组）：攻 {p['atk']:.0f}，攻击间隔 {p['interval']}s")
    for lv in (7, 10):
        s = wisdel_s3(lv)
        print(f"\n=== 维什戴尔 S3 Lv{lv}"
              f"（攻击力+{s['bl']['atk']:.0%}，攻击倍率 {s['bl']['attack@atk_scale_3']:.0%}，"
              f"残影概率 {s['bl']['attack@prob']:.0%}，间隔 {s['interval']:.1f}s，{s['ammo']:.0f} 发弹药）")
        print(f"    攻击面板 {s['atk']:.0f} → 单次：主目标 {s['main']:.0f} + 残影爆炸 {s['boom']:.0f}")
        for nm, e in ENEMIES.items():
            dh = phys(s["main"], e["df"])
            db = phys(s["boom"], e["df"])
            per = dh + db
            dps = per / s["interval"]
            print(f"    vs {nm:<6}(HP{e['hp']:<6} 防{e['df']:<6} 法抗{e['res']:>4}): "
                  f"命中 {dh:6.0f} + 残影 {db:6.0f} = {per:6.0f}/次 → {dps:6.0f} DPS → 击杀 {e['hp'] / dps:5.0f}s")
        total = (phys(s["main"], 1750) + phys(s["boom"], 1750)) * s["ammo"]
        print(f"    一轮技能总伤（对防1750）≈ {total:.0f}（6 发打完）")

    print("\n=== 对比：仅普通攻击（上一版的算法）===")
    for nm, e in ENEMIES.items():
        d = phys(p["atk"], e["df"])
        print(f"    {nm:<6}: 每次 {d:6.0f} → {d / p['interval']:6.0f} DPS → 击杀 {e['hp'] / (d / p['interval']):6.0f}s")


if __name__ == "__main__":
    main()
