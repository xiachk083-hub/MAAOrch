"""把 CE-6 的敌人波次排成一张"到闸口时刻表"——用来判断清怪够不够快、该加谁。

数据来源（全部本地，无需登录）:
    data/_level_wk_melee_6.json      关卡波次（routes / waves）
    data/_character_table_full.json  我方（费用）
    data/_enemy_db.json              敌方数值（血/防/法抗）

为什么（2026-09-20）：交棒报告的教训是"只看静态 DPS 判不出能不能过"，
但反过来——**连静态账都不算就排轴**，就变成今晚这样连跑 4 局都是"打到一半失败"。
所以这里只做一件朴素的事：把每只敌人的出现时刻、到封口格（x=2）的预计时刻列出来，
再按"每只敌人需要多少有效伤害"给出总账，用来决定"加输出还是加阻挡"。

用法:
    python tools/ce6_enemy_plan.py [--cut-x 2] [--speed 1.0]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
LVL = PROJ / "data" / "_level_wk_melee_6.json"
ENEMY = PROJ / "data" / "_enemy_db.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut-x", type=int, default=2, help="封口格所在的列（敌人要走过 x 才会被挡）")
    args = ap.parse_args()

    d = json.loads(LVL.read_text(encoding="utf-8"))
    print("关卡 options:", json.dumps(d.get("options", {}), ensure_ascii=False))
    routes = d.get("routes") or []
    print("routes:", len(routes))

    # 敌人数值库（Key -> {名称/血/防/法抗}）
    db = {}
    if ENEMY.exists():
        for e in json.loads(ENEMY.read_text(encoding="utf-8"))["enemies"]:
            v = e.get("Value") or {}
            db[e.get("Key")] = v

    rows = []
    for ri, r in enumerate(routes):
        motion = r.get("motion") or r.get("Motion") or []
        start = r.get("startPosition") or r.get("start") or {}
        spawns = r.get("spawns") or r.get("Spawns") or []
        for si, s in enumerate(spawns):
            ekey = s.get("enemyKey") or s.get("hiddenGroup") or s.get("enemy")
            t = s.get("spawnTime", s.get("time"))
            cnt = s.get("count", 1)
            rows.append((t, ekey, cnt, ri, start))

    rows.sort(key=lambda x: (x[0] if x[0] is not None else 1e9))
    print("\n== 敌人波次（按出现时刻）==")
    print("%8s %-28s %4s %s" % ("时刻", "敌人", "数量", "数值(血/防/法抗)"))
    agg = collections.Counter()
    for t, ekey, cnt, ri, start in rows:
        v = db.get(ekey) or {}
        hp = v.get("hp") or v.get("maxHp") or v.get("生命")
        df = v.get("def") or v.get("防御")
        res = v.get("magicResistance") or v.get("法抗")
        agg[ekey] += cnt
        print("%8s %-28s %4s %s" % (t, ekey, cnt, "hp=%s def=%s res=%s" % (hp, df, res)))
    if agg:
        print("\n== 合计 ==")
        for k, n in agg.most_common():
            v = db.get(k) or {}
            print("  %-28s x%d  血%s 防%s 法抗%s" % (k, n, v.get("hp"), v.get("def"), v.get("magicResistance")))
        print("  总只数 =", sum(agg.values()))
    else:
        print("\n⚠ 没解析出波次：字段名可能不同，原始 route[0] =",
              json.dumps(routes[0], ensure_ascii=False)[:400] if routes else "（无）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
