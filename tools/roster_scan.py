"""按关键词扫本号 391 个干员的天赋/技能 —— 用来"了解这个账号上每个干员适合什么"。

为什么（2026-09-21 用户要求）：「你可以带很多个干员，而且要尝试着了解这个账号上每一个干员的情况，
使用合适的干员」。冰面图（BI-EX-1）最需要的是**抵抗/免疫/解控**类，普通输出位其次，
所以先按关键词把合适的人捞出来，再按练度排序。

用法:
    python tools/roster_scan.py --keys 抵抗 免疫 冻结 寒冷
    python tools/roster_scan.py --keys 阻挡数 --min-elite 2
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")


def clean(s: str) -> str:
    s = re.sub(r"<[^>]*>", "", s or "")
    return s.replace("\\n", " ").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", nargs="+", required=True)
    ap.add_argument("--min-elite", type=int, default=0)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    ct = json.loads((PROJ / "data/_character_table_full.json").read_text(encoding="utf-8"))
    st = json.loads((PROJ / "data/_skill_table.json").read_text(encoding="utf-8"))
    own = {o["name"]: o for o in json.loads((PROJ / "data/operbox_rescane2.json").read_text(encoding="utf-8"))["own"]}

    hits = []
    for k, v in ct.items():
        if not isinstance(v, dict):
            continue
        n = v.get("name")
        if n not in own or own[n]["elite"] < args.min_elite:
            continue
        texts = []
        for t in (v.get("talents") or []):
            for cand in (t.get("candidates") or []):
                texts.append(("天赋", clean(str(cand.get("description")))))
        for i, sk in enumerate(v.get("skills") or [], 1):
            lv = ((st.get(sk.get("skillId")) or {}).get("levels") or [{}])[-1]
            texts.append(("S%d" % i, clean(str(lv.get("description")))))
        for tag, tx in texts:
            for key in args.keys:
                if key in tx:
                    hits.append((own[n]["elite"], own[n]["level"], n, v.get("profession"), tag, tx[:70], key))
                    break

    hits.sort(key=lambda h: (-h[0], -h[1]))
    print("命中 %d 条（关键词 %s）" % (len(hits), args.keys))
    seen = set()
    for elite, lvl, n, prof, tag, tx, key in hits[:args.limit]:
        mark = "" if (n, tag) in seen else ""
        seen.add((n, tag))
        print("  E%d L%-3d %-10s %-8s %-3s [%s] %s" % (elite, lvl, n, prof, tag, key, tx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
