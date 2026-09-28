#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab：列出若干 pair 的最近尝试（挑"最近失败"做新鲜度复核）。"""
import json
import os
import sys

ROOT = r"D:\1-2\MAAOrch"
sys.path.insert(0, os.path.join(ROOT, "tools"))
import stage_capability as sc  # noqa: E402

with open(os.path.join(ROOT, "runtime", "state", "stage_capability_state.json"), encoding="utf-8") as f:
    attempts = json.load(f)["attempts"]

TARGETS = [("骰子", "SR-7"), ("白桑_", "SR-7"), ("tbNick_h56zy", "SR-7"), ("皮埃尔商店", "CA-5"),
           ("檀丝江喜", "LS-6"), ("v", "LS-6"), ("用户_173920298", "LS-6"), ("寻找勇者的大魔王", "LS-6"),
           ("摸摸摸摸鱼", "LS-6"), ("Raiz", "1-7"), ("帝国猛虎", "SR-8"), ("拍前看简介", "AT-6")]

for a, st in TARGETS:
    print("=" * 90)
    print("### %s / %s" % (a, st))
    rows = []
    for k in sorted(attempts):
        at = attempts[k]
        if at["acct"] != a:
            continue
        ev = sc.classify_attempt(at.get("lines") or [], at.get("drops") or [], at.get("result", ""))
        stages = set(ev["selected"]) | set(ev["not_open"]) | set(ev["dropped"]) | set(ev["drop_log"])
        if st in stages:
            rows.append((at["day"], at["file"], at["result"],
                         "sel" if st in ev["selected"] else "",
                         "open" if st in ev["not_open"] else "",
                         "drop" if st in ev["dropped"] else "",
                         "droplog" if st in ev["drop_log"] else "",
                         [(f["stage"], f["kind"]) for f in ev["fights"]]))
    for r in rows[-6:]:
        print("  %s | %s | result=%r | %s %s %s %s | fights=%s" % r)
    print("  共 %d 条" % len(rows))
