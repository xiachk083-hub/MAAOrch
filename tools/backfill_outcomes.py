#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当天已有战绩从横扫账本回填进 `runtime/axis_outcomes.jsonl`。

为什么要回填：战绩账本是 2026-09-24 01:40 才接上的，之前几小时的三星/二星结果
   只留在 `runtime/reports/event_sweep_*.jsonl` 里 ⇒ 不回填的话，排序器和画像只能
   看到"接上以后"的样本，白白浪费当天的数据。

判定口径与 event_sweep 一致：
    action=done → 面板核实 3 星；action=partial → 1~2 星；no_prime/skip 不记（没打）。
"""
from __future__ import annotations

import glob
import json
import pathlib
import re

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
LEDGER = ROOT / "runtime" / "axis_outcomes.jsonl"


def main() -> int:
    # **重建**：保留线上条目（note != backfill），回填条目重算一遍 —— 早先版本把 partial 记成
    # 1 星，重跑时要能纠正（只靠 append+去重是改不了旧值的）。
    live = []
    if LEDGER.exists():
        for ln in LEDGER.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            o = json.loads(ln)
            if o.get("note") != "backfill":
                live.append(o)
    have = {(o.get("stage"), str(o.get("id"))) for o in live}

    add = []
    for f in glob.glob(str(ROOT / "runtime" / "reports" / "event_sweep_*.jsonl")):
        for ln in open(f, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            stage = r.get("stage")
            if not stage:
                continue
            actions = {"done": 3, "partial": 2}
            if r.get("action") not in actions:
                continue                     # 没真打（⊘/⏸/blocked/error）⇒ 不入账
            ids = [t.get("id") for t in (r.get("tried") or [])]
            m = re.search(r"prts://(\d+)", r.get("result") or "")
            acc = m.group(1) if m else (ids[-1] if ids else None)
            if acc is None:
                continue
            stars = r.get("stars_after")
            if stars is None:
                stars = actions[r["action"]]
            if (stage, str(acc)) in have:
                continue
            have.add((stage, str(acc)))
            add.append({"ts": r.get("ts"), "op_id": f"{stage}_{acc}", "stage": stage,
                        "want": r.get("want"), "id": acc, "stars": stars,
                        "note": "backfill", "opers": []})

    with open(LEDGER, "w", encoding="utf-8") as fh:
        for o in live:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
        for o in add:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"线上 {len(live)} 条 + 回填 {len(add)} 条 = {len(live) + len(add)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
