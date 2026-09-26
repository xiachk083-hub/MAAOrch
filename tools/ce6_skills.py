"""按干员名列出三个技能的**描述**（本地 _skill_table.json），用来挑「这关该带哪个技能」。

为什么（2026-09-21 用户指出）：「干员不一定是三技能最厉害，你要看他具体哪个合适」——
我此前对所有干员一律写 `skill: 3`，结果 **伊内丝 S3 的第一次部署只放影哨、人不上场**
（游戏数据原文：首次部署时不消耗部署费用，放置一个影哨后离场并立刻刷新再部署时间）⇒
她那一路等于一直是空的。教训：**排轴前先读技能描述**，别默认三技能。

用法:
    python tools/ce6_skills.py 伊内丝 涤火杰西卡 年 塞雷娅 棘刺
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")


def clean(s: str) -> str:
    """去掉游戏数据里的富文本标签，读起来清爽些。"""
    s = re.sub(r"<[^>]*>", "", s or "")
    s = s.replace("\\n", " ")
    return s.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("opers", nargs="+")
    args = ap.parse_args()

    ct = json.loads((PROJ / "data/_character_table_full.json").read_text(encoding="utf-8"))
    st = json.loads((PROJ / "data/_skill_table.json").read_text(encoding="utf-8"))
    by_name = {v["name"]: v for v in ct.values() if isinstance(v, dict) and v.get("name")}

    for name in args.opers:
        v = by_name.get(name)
        if not v:
            print("✗ 角色表里没有：%s" % name)
            continue
        print("\n== %s（%s / %s）" % (name, v.get("profession"), v.get("subProfessionId")))
        for i, sk in enumerate(v.get("skills") or [], 1):
            sid = sk.get("skillId")
            s = st.get(sid) or {}
            lv = (s.get("levels") or [{}])[-1]
            print("   S%d  %s" % (i, clean(str(lv.get("description")))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
