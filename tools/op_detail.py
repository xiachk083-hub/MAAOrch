# -*- coding: utf-8 -*-
"""打印干员的天赋/技能原文 + 数值，用于确认机制（免冻/抵抗/解控等）。"""
import json, pathlib, sys

P = pathlib.Path(r"D:\1-2\MAAOrch")
CT = json.loads((P / "data/_character_table_full.json").read_text(encoding="utf-8"))
ST = json.loads((P / "data/_skill_table.json").read_text(encoding="utf-8"))
ST = ST.get("skills", ST)


def txt(x):
    if isinstance(x, dict):
        return x.get("m_value") or x.get("value") or ""
    return x or ""


def op(name):
    for k, v in CT.items():
        if isinstance(v, dict) and v.get("name") == name:
            return k, v
    return None, None


def main(names):
    for name in names:
        k, v = op(name)
        if not v:
            print("== %s 未找到" % name)
            continue
        d = ((v.get("phases") or [{}])[-1].get("attributesKeyFrames") or [{}])[-1].get("data") or {}
        print("== %s (%s/%s) 阻挡%s HP%s 攻%s 防%s 法抗%s 费%s" % (
            name, v.get("profession"), v.get("subProfessionId"), d.get("blockCnt"), d.get("maxHp"),
            d.get("atk"), d.get("def"), d.get("magicResistance"), d.get("cost")))
        for i, t in enumerate(v.get("talents") or []):
            for c in (t.get("candidates") or []):
                pass
                print("   天赋%d %s: %s" % (i + 1, txt(c.get("name")), txt(c.get("description"))))
        for s in (v.get("skills") or []):
            sid = s.get("skillId")
            sk = ST.get(sid) or {}
            levels = sk.get("levels") or []
            print("   技能 %s: %s | %s" % (sid, txt(sk.get("skillName")), " ".join(txt(levels[-1].get("description")) if levels else "")))
        print()


if __name__ == "__main__":
    main(sys.argv[1:] or ["凛御银灰", "银灰", "灵知", "年", "流明"])
