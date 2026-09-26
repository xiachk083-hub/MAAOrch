"""干员攻击范围计算器：把「站哪格 + 朝哪」能覆盖到哪些格子算出来。

为什么（2026-09-21 打 BI-EX-4~8）：堵口必须落在自家输出的射程内，但光看地图猜射程很容易翻车
（BI-EX-1 那次失败就是堵口跑到射程外）。本地 `_character_table_full.json` 里有 rangeId（如 "3-9"）
但没有射程表本身，所以从同一个镜像拉 `ranges.json` 缓存到 data/_ranges.json。

用法:
    python tools/atk_range.py --oper 蕾缪安 --at 2,5 --dir Down
    python tools/atk_range.py --oper 艾雅法拉 --at 3,3 --dir Right --skill 3
    python tools/atk_range.py --range-id 3-9 --dir Right            # 直接查某个 rangeId
"""
from __future__ import annotations

import argparse
import json
import pathlib
import urllib.request

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
CACHE = PROJ / "data" / "_ranges.json"
CT = json.loads((PROJ / "data" / "_character_table_full.json").read_text(encoding="utf-8"))
SK = json.loads((PROJ / "data" / "_skill_table.json").read_text(encoding="utf-8"))
UA = {"User-Agent": "Mozilla/5.0"}
BASES = ["https://ghproxy.net/https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/",
         "https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/",
         "https://gcore.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/"]
DIRS = {"Right": (0, 0), "Up": (1, 0), "Left": (2, 0), "Down": (3, 0)}


def load_ranges() -> dict:
    if CACHE.exists() and CACHE.stat().st_size > 1000:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    for base in BASES:
        try:
            d = json.loads(urllib.request.urlopen(
                urllib.request.Request(base + "excel/range_table.json", headers=UA), timeout=90).read())
            CACHE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            print("（已缓存 data/_ranges.json，%d 条）" % len(d))
            return d
        except Exception as e:
            print("  ! %s: %s" % (base.split('/')[2], e))
    raise SystemExit("取不到射程表")


def by_name(name: str):
    for k, v in CT.items():
        if isinstance(v, dict) and v.get("name") == name:
            return k, v
    raise SystemExit("账号数据里没有干员：%s" % name)


def range_id_for(name: str, skill: int | None) -> str:
    cid, v = by_name(name)
    rid = ((v.get("phases") or [{}])[-1] or {}).get("rangeId")
    if skill:
        sk = (v.get("skills") or [])[skill - 1]
        tbl = SK.get(sk["skillId"]) or {}
        lv = (tbl.get("levels") or [{}])[-1]
        rid = lv.get("rangeId") or rid
        print("（%s S%d 范围 rangeId=%s）" % (name, skill, rid))
    return rid


def rotate(cells, direction: str):
    """ranges 表里的格子是按「朝右」定义的：row 是相对行（上为 1? 实测为 0 基准），col 向右为 +。"""
    k = DIRS[direction][0]
    out = []
    for c in cells:
        r, col = c["row"], c["col"]
        for _ in range(k):
            r, col = col, -r          # 顺时针 90°
        out.append((col, -r))          # 转成 (dx, dy)：dy 向下为正（与地图 y 一致）
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oper")
    ap.add_argument("--skill", type=int, default=None)
    ap.add_argument("--range-id")
    ap.add_argument("--at", help="x,y")
    ap.add_argument("--dir", default="Right", choices=list(DIRS))
    args = ap.parse_args()

    rid = args.range_id or range_id_for(args.oper, args.skill)
    rg = load_ranges()
    entry = rg.get(rid)
    if not entry:
        raise SystemExit("射程表里没有 rangeId=%s" % rid)
    cells = rotate(entry["grids"], args.dir)
    print("rangeId=%s  朝%s  格子数 %d" % (rid, args.dir, len(cells)))
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    print("  相对坐标 dx∈[%d,%d] dy∈[%d,%d]" % (min(xs), max(xs), min(ys), max(ys)))
    if args.at:
        x0, y0 = (int(t) for t in args.at.split(","))
        ab = {(x0 + dx, y0 + dy) for dx, dy in cells}
        print("  站在 (%d,%d) 能打到：%s" % (x0, y0, sorted(ab, key=lambda p: (p[1], p[0]))))
        # 简易示意
        minx, maxx = min(p[0] for p in ab), max(p[0] for p in ab)
        miny, maxy = min(p[1] for p in ab), max(p[1] for p in ab)
        for y in range(miny, maxy + 1):
            print("    %2d " % y + "".join("*" if (x, y) in ab else ("@" if (x, y) == (x0, y0) else ".")
                                           for x in range(minx, maxx + 1)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
