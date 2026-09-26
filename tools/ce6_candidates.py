"""列出 CE-6 候选干员的费用/练度，便于挑"能编进队"的替代人选。

用法: python tools/ce6_candidates.py
"""
from __future__ import annotations

import json
import pathlib

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")

COSTS = {
    "桃金娘": 10, "芬": 11, "香草": 10, "翎羽": 11, "夜刀": 11, "极境": 11,
    "讯使": 11, "德克萨斯": 11, "凛冬": 12, "红豆": 11, "清道夫": 12, "推进之王": 12,
    "重岳": 11, "玛恩纳": 12, "仇白": 20, "棘刺": 20, "烛煌": 21, "艾雅法拉": 21,
    "乌尔比安": 24, "泥岩": 36, "塞雷娅": 22, "闪灵": 20, "星熊": 23, "年": 23,
    "蕾缪安": 22, "史尔特尔": 21, "临光": 21, "银灰": 22, "百炼嘉维尔": 22,
}


def main() -> int:
    own = json.load(open(PROJ / "data/operbox_rescane2.json", encoding="utf-8"))["own"]
    by = {o["name"]: o for o in own}
    print("%-12s %5s  %s" % ("干员", "费用", "练度"))
    for n, c in sorted(COSTS.items(), key=lambda kv: kv[1]):
        o = by.get(n)
        info = ("E%d L%d %d★ pot%d" % (o["elite"], o["level"], o["rarity"], o["potential"])) if o else "—— 号上没有"
        print("%-12s %5d  %s" % (n, c, info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
