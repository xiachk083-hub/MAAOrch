#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤14：文档数字自查（对着 verify_final.json / 台账 / state 核对我写进 QA 文档的每个数字）。"""
import json
import os
import sys
from collections import Counter

ROOT = r"D:\1-2\MAAOrch"
OUT = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa")

rep = json.load(open(os.path.join(ROOT, "runtime", "reports", "stage_capability.json"), encoding="utf-8"))
vf = json.load(open(os.path.join(OUT, "verify_final.json"), encoding="utf-8"))

# 1) 台账总量
st = Counter(r["status"] for r in rep["rows"])
print("台账行数 %d = %s" % (len(rep["rows"]), dict(st)))

# 2) SR-7 failed 行 & 账号
sr7 = [r for r in rep["rows"] if r["stage"] == "SR-7" and r["status"] == "failed"]
print("SR-7 failed: %d 行 / %d 账号" % (len(sr7), len({r["account"] for r in sr7})))

# 2b) SR-7 success 行 & 该关成功尝试总数（state 侧）
sr7s = [r for r in rep["rows"] if r["stage"] == "SR-7" and r["status"] == "success"]
print("SR-7 success 行: %d" % len(sr7s))
state = json.load(open(os.path.join(ROOT, "runtime", "state", "stage_capability_state.json"), encoding="utf-8"))
att = state["attempts"]
sr7_ok = [(k, v) for k, v in att.items() if "SR-7" in (v.get("drops") or {})]
sr7_ok = sorted(sr7_ok, key=lambda kv: (kv[1]["day"], kv[1]["acct"]))
print("SR-7 成功尝试: %d 次, 日期 %s ~ %s, 账号 %d 个" % (
    len(sr7_ok), sr7_ok[0][1]["day"] if sr7_ok else "-", sr7_ok[-1][1]["day"] if sr7_ok else "-",
    len({v["acct"] for _, v in sr7_ok})))
print("  按日:", dict(Counter(v["day"] for _, v in sr7_ok)))

# 3) 河清灯 CA-5 行明细
for r in rep["rows"]:
    if r["account"] == "河清灯_别寄圆通" and r["stage"] == "CA-5":
        print("河清灯/CA-5 行:", json.dumps({k: r[k] for k in (
            "status", "last_success", "last_failure", "success_n", "fail_n", "net_fail_n",
            "impact", "failure_types", "fail_after_success") if k in r}, ensure_ascii=False))
        # 选中次数从 failures 计数
        print("  failures 条数:", len(r.get("failures") or []), " selects:", r.get("selects"),
              " sel_n:", r.get("sel_n"))
        break

# 4) 抽样 pairs 汇总（供文档 success 名单）
print("抽样: %d 对 = %s" % (len(vf["results"]), dict(Counter(x["ledger"] for x in vf["results"]))))
succ = sorted((x["account"], x["stage"]) for x in vf["results"] if x["ledger"] == "success")
print("success 名单(%d): %s" % (len(succ), "; ".join("%s/%s" % x for x in succ)))
print("failed 名单(%d): %s" % (
    sum(1 for x in vf["results"] if x["ledger"] == "failed"),
    "; ".join("%s/%s" % (x["account"], x["stage"]) for x in vf["results"] if x["ledger"] == "failed")))
print("unknown 名单: %s" % "; ".join("%s/%s" % (x["account"], x["stage"]) for x in vf["results"] if x["ledger"] == "unknown"))

# 5) 尝试总览（state）
print("state attempts=%d, 日期 %s ~ %s" % (
    len(att), min(v["day"] for v in att.values()), max(v["day"] for v in att.values())))

# 6) unknown 行全集
print("unknown 行全集:", "; ".join("%s/%s" % (r["account"], r["stage"]) for r in rep["rows"] if r["status"] == "unknown"))
