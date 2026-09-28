#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤1：台账分布与抽样设计（只读）。

产出：
  docs/audits/2026-09-28_stage_capability_qa/distributions.txt
  docs/audits/2026-09-28_stage_capability_qa/sample.json   （抽样清单 + 待取原始证据 manifest）
"""
import json
import os
import random
import sys
from collections import Counter, OrderedDict

ROOT = r"D:\1-2\MAAOrch"
OUT = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa")
os.makedirs(OUT, exist_ok=True)

REPORT = os.path.join(ROOT, "runtime", "reports", "stage_capability.json")
STATE = os.path.join(ROOT, "runtime", "state", "stage_capability_state.json")

with open(REPORT, "r", encoding="utf-8") as f:
    rep = json.load(f)
with open(STATE, "r", encoding="utf-8") as f:
    state = json.load(f)

rows = rep["rows"]
attempts = state["attempts"]

sys.path.insert(0, os.path.join(ROOT, "tools"))
import stage_capability as sc  # noqa: E402

lines = []
p = lines.append

p("== 台账总览 ==")
p("built_at=%s state_built_at=%s window=%s attempts=%d" % (
    rep["built_at"], rep["state_built_at"], json.dumps(rep["window"]), len(attempts)))
p("stats=%s" % json.dumps(rep["stats"], ensure_ascii=False))
p("")

by_status = Counter(r["status"] for r in rows)
p("rows by status: %s" % dict(by_status))

failed = [r for r in rows if r["status"] == "failed"]
unknown = [r for r in rows if r["status"] == "unknown"]
success = [r for r in rows if r["status"] == "success"]

p("")
p("== failed rows (%d) ==" % len(failed))
for r in failed:
    f = r["failures"][0] if r["failures"] else {}
    p("%-22s %-14s fail_n=%d types=%s last_fail=%s day/file=%s/%s line=%r" % (
        r["account"], r["stage"], r["fail_n"], ",".join(r["failure_types"].keys()),
        r["last_failure"], f.get("day"), f.get("file"), (f.get("line") or "")[:110]))

p("")
p("== unknown rows (%d) ==" % len(unknown))
for r in unknown:
    p("%-22s %-14s sel=%d notopen=%d net=%d last_fail=%s types=%s" % (
        r["account"], r["stage"], r["selected_n"], r["not_open_n"], r["net_fail_n"],
        r["last_failure"], ",".join(r["failure_types"].keys())))

p("")
p("= accounts with failed rows: %d" % len({r["account"] for r in failed}))
p("= accounts with unknown rows: %d" % len({r["account"] for r in unknown}))

# ---------------- 抽样（seed 固定，可复现） ----------------
SEED = 20260928
rnd = random.Random(SEED)
acct_all = sorted(rep["accounts"])
acct_fail = sorted({r["account"] for r in failed})
acct_unk = sorted({r["account"] for r in unknown})

# 规则：先保证两种易错类（failed/unknown）被覆盖，再用成功行补足 10 账号。
pick_fail = rnd.sample(acct_fail, min(5, len(acct_fail)))
rest = [a for a in acct_all if a not in pick_fail]
pick_unk = [a for a in rnd.sample(rest, len(rest)) if a in acct_unk][:3] if acct_unk else []
rest2 = [a for a in rest if a not in pick_unk]
need = 10 - len(pick_fail) - len(pick_unk)
pick_suc = rnd.sample(rest2, min(need, len(rest2)))
sample_accounts = pick_fail + pick_unk + pick_suc

manifest = []
sample_rows = []
for a in sample_accounts:
    mine = [r for r in rows if r["account"] == a]
    prio = ([r for r in mine if r["status"] == "failed"] if a in pick_fail else []) + \
           ([r for r in mine if r["status"] == "unknown"] if a in pick_unk else []) + \
           [r for r in mine if r["status"] == "success"]
    want = min(3 if a in pick_fail or a in pick_unk else 3, len(mine))
    chosen, seen = [], set()
    for r in prio:
        if r["stage"] in seen:
            continue
        chosen.append(r)
        seen.add(r["stage"])
        if len(chosen) >= want:
            break
    for r in chosen:
        sample_rows.append(r)

p("")
p("== 抽样 ==")
p("seed=%d" % SEED)
p("accounts(pick_fail)=%s" % pick_fail)
p("accounts(pick_unk)=%s" % pick_unk)
p("accounts(pick_success_fill)=%s" % pick_suc)
p("total accounts=%d pairs=%d status=%s" % (
    len(sample_accounts), len(sample_rows), dict(Counter(r["status"] for r in sample_rows))))

# 每个 pair 找出 state 里所有"该关出现"的尝试（selected/dropped/drop_log/not_open）
pairs = []
for r in sample_rows:
    a, s = r["account"], r["stage"]
    ev_rows = []
    for k in sorted(attempts):
        at = attempts[k]
        if at.get("acct") != a:
            continue
        ev = sc.classify_attempt(at.get("lines") or [], at.get("drops") or [], at.get("result", ""))
        stages = set(ev["selected"]) | set(ev["not_open"]) | set(ev["dropped"]) | set(ev["drop_log"])
        if ev["annih_done"] or (ev["annih_progress"] and sc.ANNIH_STAGE in ev["selected"]):
            stages.add(sc.ANNIH_STAGE)
        if s in stages:
            ev_rows.append({
                "key": k, "day": at["day"], "file": at["file"], "result": at["result"],
                "in_drops": s in ev["dropped"],
                "in_drop_log": s in ev["drop_log"],
                "in_selected": s in ev["selected"],
                "in_not_open": s in ev["not_open"],
                "n_lines": len(at.get("lines") or []),
                "mt": at.get("mt", ""),
            })
    pairs.append({
        "account": a, "stage": s, "status": r["status"],
        "row": {kk: r[kk] for kk in ("success_n", "selected_n", "not_open_n", "last_success",
                                     "last_failure", "fail_n", "net_fail_n", "sources", "impact",
                                     "failure_types")},
        "attempts": ev_rows,
    })

tot = sum(len(x["attempts"]) for x in pairs)
p("pairs=%d 涉及尝试=%d（平均 %.1f）" % (len(pairs), tot, tot / max(1, len(pairs))))
p("")

with open(os.path.join(OUT, "distributions.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

with open(os.path.join(OUT, "sample.json"), "w", encoding="utf-8") as f:
    json.dump({"seed": SEED, "pick_fail": pick_fail, "pick_unk": pick_unk,
               "pick_success_fill": pick_suc, "pairs": pairs}, f, ensure_ascii=False, indent=1)

print("\n".join(lines))
print("wrote", os.path.join(OUT, "sample.json"))
