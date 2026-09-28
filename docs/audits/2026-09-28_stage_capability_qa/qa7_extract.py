#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab：从 raw/batch_*.txt 中抽取指定条目的 log 过滤行（带序号），供人工判读。"""
import os
import sys

ROOT = r"D:\1-2\MAAOrch"
RAW = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa", "raw")

want = []
for a in sys.argv[1:]:
    day, acct, file = a.split("::")
    want.append((day, acct, file))

for bn in sorted(os.listdir(RAW)):
    if not bn.startswith("batch_") or not bn.endswith(".txt"):
        continue
    with open(os.path.join(RAW, bn), "r", encoding="utf-8") as f:
        txt = f.read().replace("\r\n", "\n").replace("\r", "")
    cur = None
    mode = None
    n = 0
    for line in txt.split("\n"):
        if line.startswith("###ENTRY|"):
            parts = line.split("|")
            cur = (parts[1], parts[2], parts[3])
            n = 0
            if cur in want:
                print("\n" + "=" * 100)
                print("### %s / %s / %s   [%s]" % (parts[1], parts[2], parts[3], parts[5] if len(parts) > 5 else ""))
                want_this = True
            else:
                want_this = False
        elif line == "##FILTER":
            mode = "f"
        elif line in ("##HEAD", "##TAIL", "###END"):
            mode = None
        elif line.startswith("###"):
            mode = None
        elif want_this and mode == "f":
            n += 1
            print("%4d| %s" % (n, line))
