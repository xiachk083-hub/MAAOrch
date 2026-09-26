#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看作业（copilot）内容：干员、技能/模组要求、是否借助战 —— 用来挑"最可能三星"的轴。

用法:
    python tools/inspect_jobs.py 48114 48182 48215
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")


def main() -> int:
    ids = sys.argv[1:] or ["48114", "48182", "48215"]
    for jid in ids:
        p = ROOT / "data" / "copilot" / "fallback_jobs" / f"job_{jid}.json"
        if not p.exists():
            print(f"--- job_{jid}: 缺文件 {p}")
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        ops = d.get("opers") or []
        print(f"--- job_{jid}  title={d.get('title')}  干员数={len(ops)}")
        for o in ops:
            print("    %-14s skill=%-3s module=%-3s elite=%s lvl=%s 替=%s" % (
                str(o.get("name"))[:14], o.get("skill"), o.get("module"),
                o.get("elite"), o.get("level"), o.get("alternate")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
