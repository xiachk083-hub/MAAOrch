# -*- coding: utf-8 -*-
"""全账号干员技能知识表：把干员箱里每个干员的技能原文＋关键数值（技力/持续/黑board）打印/落盘。

用户要求（2026-09-21 原话）：「我指的不是你编队里面各个干员技能 而是这个账号上的所有干员的技能
你漏掉的强力干员和强力技能已经很多了」⇒ 先建立账号级的技能知识，再排轴。

用法:
    python tools/roster_skills.py --rarity 6                  # 全部 6★
    python tools/roster_skills.py --names 玛恩纳,史尔特尔      # 指定干员
    python tools/roster_skills.py --rarity 6 --out docs/ROSTER_SKILLS.md
"""
from __future__ import annotations

import argparse, io, json, pathlib, re, sys

P = pathlib.Path(r"D:\1-2\MAAOrch")
CT = json.loads((P / "data/_character_table_full.json").read_text(encoding="utf-8"))
SK = json.loads((P / "data/_skill_table.json").read_text(encoding="utf-8"))
BOX = {e["name"]: e for e in json.loads((P / "data/operbox_batch/emu2_9036_flat.json").read_text(encoding="utf-8"))}

TAG = re.compile(r"<[^>]+>")


def clean(s) -> str:
    if not isinstance(s, str):
        return ""
    s = TAG.sub("", s)
    s = s.replace("\\n", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()


def skill_lines(sid: str, idx: int) -> str:
    s = SK.get(sid)
    if not s:
        return "  S%d  ?" % idx
    lv = s["levels"][-1]
    sp = lv.get("spData") or {}
    bb = {b["key"]: b.get("value") for b in (lv.get("blackboard") or [])}
    keep = []
    for k in ("atk", "attack@atk_scale", "atk_scale", "attack@max_target", "max_target",
              "attack@times", "times", "duration", "hp_ratio", "block_cnt", "def", "cost",
              "attack@trigger_time", "attack@cnt", "interval", "attack@sluggish", "stun",
              "damage_resistance", "atk_scale_base"):
        if k in bb and bb[k] not in (0.0, None):
            keep.append("%s=%s" % (k, bb[k]))
    desc = clean(lv.get("description"))[:150]
    return "  S%d  %s\n      技力=%s 初动=%s 持续=%s  %s" % (
        idx, desc, sp.get("spCost"), sp.get("initSp"), lv.get("duration"), " ".join(keep[:8]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rarity", type=int, default=6)
    ap.add_argument("--names")
    ap.add_argument("--out")
    a = ap.parse_args()
    want = set(a.names.split(",")) if a.names else None
    lines = []
    for k, v in CT.items():
        if not isinstance(v, dict):
            continue
        name = v.get("name")
        if name not in BOX:
            continue
        if want and name not in want:
            continue
        if not want and v.get("rarity") not in ("TIER_%d" % a.rarity,):
            continue
        e = BOX[name]
        d = ((v.get("phases") or [{}])[-1].get("attributesKeyFrames") or [{}])[-1].get("data") or {}
        head = "== %s  %s/%s  部署位=%s  费=%s  阻挡=%s  HP=%s 攻=%s 防=%s  E%d Lv%d" % (
            name, v.get("profession"), v.get("subProfessionId"), v.get("position"),
            d.get("cost"), d.get("blockCnt"), d.get("maxHp"), d.get("atk"), d.get("def"),
            e.get("elite", 0), e.get("level", 0))
        lines.append(head)
        for t in (v.get("talents") or []):
            cands = t.get("candidates") or []
            if cands:
                lines.append("  天赋 %s: %s" % (clean(cands[-1].get("name")), clean(cands[-1].get("description"))[:130]))
        for i, s in enumerate(v.get("skills") or [], 1):
            lines.append(skill_lines(s.get("skillId"), i))
        lines.append("")
    txt = "\n".join(lines)
    print(txt)
    if a.out:
        p = P / a.out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt, encoding="utf-8")
        print("saved", p, len(lines), "lines", file=sys.stderr)


if __name__ == "__main__":
    main()
