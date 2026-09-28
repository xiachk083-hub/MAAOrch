#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab：查看 state 里某号某关相关尝试的「缓存判据行」（对照原始证据的截断口径）。"""
import json
import os
import sys

ROOT = r"D:\1-2\MAAOrch"
sys.path.insert(0, os.path.join(ROOT, "tools"))
import stage_capability as sc  # noqa: E402

with open(os.path.join(ROOT, "runtime", "state", "stage_capability_state.json"), encoding="utf-8") as f:
    attempts = json.load(f)["attempts"]

targets = [("骰子", "SR-7"), ("LEUNG", "1-7"), ("河清灯_别寄圆通", "CA-5")]
want = {}
for a, s in targets:
    want.setdefault(a, []).append(s)

for k in sorted(attempts):
    at = attempts[k]
    if at["acct"] not in want:
        continue
    ev = sc.classify_attempt(at.get("lines") or [], at.get("drops") or [], at.get("result", ""))
    stages = set(ev["selected"]) | set(ev["not_open"]) | set(ev["dropped"]) | set(ev["drop_log"])
    hit = [s for s in want[at["acct"]] if s in stages]
    if not hit:
        continue
    print("=" * 100)
    print("%s | %s | %s | result=%r | drops=%s" % (at["day"], at["acct"], at["file"], at["result"],
                                                   ",".join(at.get("drops") or [])))
    print("hit=%s selected=%s not_open=%s drop_log=%s dropped=%s annih_done=%s fights=%s"
          % (hit, ev["selected"], ev["not_open"], ev["drop_log"], ev["dropped"], ev["annih_done"],
             [(f["stage"], f["kind"]) for f in ev["fights"]]))
    print("lines(%d):" % len(at.get("lines") or []))
    for ln in (at.get("lines") or []):
        print("   ", ln)
