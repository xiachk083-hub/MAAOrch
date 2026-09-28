#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤13：为文档补统计（EV_CAP 截断面 / 成功行新鲜度 / 关卡×状态矩阵）。"""
import datetime as dt
import json
import os
import sys
from collections import Counter, OrderedDict

ROOT = r"D:\1-2\MAAOrch"
OUT = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import stage_capability as sc  # noqa: E402

state = json.load(open(os.path.join(ROOT, "runtime", "state", "stage_capability_state.json"), encoding="utf-8"))
rep = json.load(open(os.path.join(ROOT, "runtime", "reports", "stage_capability.json"), encoding="utf-8"))
attempts = state["attempts"]

# a) EV_CAP
cap = [k for k, v in attempts.items() if len(v.get("lines") or []) >= sc.EV_CAP]
print("attempts=%d, 达到 EV_CAP(%d) 的=%d (%.2f%%)" % (len(attempts), sc.EV_CAP, len(cap), 100.0 * len(cap) / len(attempts)))
by_day = Counter(attempts[k]["day"] for k in cap)
print("  按日 top:", by_day.most_common(8))

# b) 成功行新鲜度（距今 = 2026-09-28 - last_success）
TODAY = dt.date(2026, 9, 28)
fresh = Counter()
stale = []
for r in rep["rows"]:
    if r["status"] != "success" or not r["last_success"]:
        continue
    d = (TODAY - dt.date(*map(int, r["last_success"].split("-")))).days
    fresh[d // 3 * 3] += 1
    if d >= 12:
        stale.append((r["account"], r["stage"], r["last_success"], d))
print("成功行 last_success 距今天数分桶（3 天粒度）：", dict(sorted(fresh.items())))
print("成功行 last_success ≥12 天前（%d）：%s" % (len(stale), "; ".join("%s/%s(%s,%d天)" % x for x in stale[:30])))

# c) 关卡 × 状态
mat = OrderedDict()
for r in rep["rows"]:
    mat.setdefault(r["stage"], Counter())[r["status"]] += 1
print("关卡×状态：")
for st, c in mat.items():
    print("  %-14s %s" % (st, dict(c)))

# d) failed 行的失败日跨度
for r in rep["rows"]:
    if r["status"] == "failed":
        pass
span = [(r["account"], r["stage"], r["failures"][0]["day"] if r["failures"] else "", r["last_failure"])
        for r in rep["rows"] if r["status"] == "failed"]
print("failed 行 首次/最近失败日（前 30）：")
for x in span[:30]:
    print("  %s/%s %s .. %s" % x)
