#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤3：分层抽样 + 取证清单（只读，不联网）。

分层理由：failed(25行)/unknown(6行) 命中率低，均匀随机必然欠采样正是要查的两类；
故对 failed 按"失败签名"分层、unknown 按形态分层，success 随机补足，保证 10 账号 × 2-3 关卡。

产出：
  docs/audits/2026-09-28_stage_capability_qa/sample2.json   （分层后的抽样 + 每对证据尝试）
  docs/audits/2026-09-28_stage_capability_qa/manifest.json  （待取原始证据的清单）
  docs/audits/2026-09-28_stage_capability_qa/global_checks.txt （本地全局对照统计）
"""
import json
import os
import random
import sys
from collections import Counter, OrderedDict

ROOT = r"D:\1-2\MAAOrch"
OUT = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import stage_capability as sc  # noqa: E402

with open(os.path.join(ROOT, "runtime", "reports", "stage_capability.json"), encoding="utf-8") as f:
    rep = json.load(f)
with open(os.path.join(ROOT, "runtime", "state", "stage_capability_state.json"), encoding="utf-8") as f:
    state = json.load(f)
attempts = state["attempts"]
rows = rep["rows"]

SEED = 20260928
rnd = random.Random(SEED)

failed = [r for r in rows if r["status"] == "failed"]
unknown = [r for r in rows if r["status"] == "unknown"]
success = [r for r in rows if r["status"] == "success"]

out_lines = []
p = out_lines.append

# ---------------- 分层 ----------------
def sig(r):
    return tuple(r["failure_types"].keys())

strata = OrderedDict()
strata["failed:仅 stage_unavailable"] = [r for r in failed if sig(r) == ("stage_unavailable",)]
strata["failed:仅 stage_not_open"] = [r for r in failed if sig(r) == ("stage_not_open",)]
strata["failed:混合(含 stage_unavailable)"] = [r for r in failed if "stage_unavailable" in sig(r) and len(sig(r)) > 1]

p("== 分层统计 ==")
for k, v in strata.items():
    p("%-32s %d 行：%s" % (k, len(v), "; ".join("%s/%s" % (r["account"], r["stage"]) for r in v)))

picks = []          # (reason, row)
pick = OrderedDict()
for k in ("failed:仅 stage_unavailable", "failed:仅 stage_not_open"):
    for r in rnd.sample(strata[k], min(2, len(strata[k]))):
        picks.append((k, r))
mix = strata["failed:混合(含 stage_unavailable)"]
for r in rnd.sample(mix, min(3, len(mix))):
    picks.append(("failed:混合", r))
# 混合层补一个 SR-5（Gitano 是唯一 SR-5）
sr5 = [r for r in failed if r["stage"] == "SR-5"]
if sr5:
    picks.append(("failed:SR-5 特例", sr5[0]))

# unknown 层：强制 net 形态 1 个 + 其余随机 2 个
unk_net = [r for r in unknown if r["net_fail_n"]]
unk_plain = [r for r in unknown if not r["net_fail_n"]]
for r in unk_net[:1] + rnd.sample(unk_plain, min(2, len(unk_plain))):
    picks.append(("unknown", r))

# ---------------- 组织为账号集 ----------------
chosen = OrderedDict()      # account -> {"rows": [row], "reason": str}
for reason, r in picks:
    e = chosen.setdefault(r["account"], {"rows": [], "reason": []})
    e["rows"].append(r)
    if reason not in e["reason"]:
        e["reason"].append(reason)

# success 补足：随机 3 个账号（不在已选集合里）
rest_accts = sorted({r["account"] for r in success} - set(chosen))
for a in rnd.sample(rest_accts, min(3, len(rest_accts))):
    chosen[a] = {"rows": [], "reason": ["success(随机补足)"]}

# 每账号 2-3 对：先放 failed/unknown，再用该号 success 补到 2-3（同 stage 不重复）
for a, e in chosen.items():
    mine_suc = [r for r in success if r["account"] == a]
    rnd.shuffle(mine_suc)
    have = {r["stage"] for r in e["rows"]}
    for target in (3, 2):        # 先补到 3；若不足再退到 2
        for r in mine_suc:
            if len(e["rows"]) >= target:
                break
            if r["stage"] in have:
                continue
            e["rows"].append(r)
            have.add(r["stage"])

p("")
p("== 抽样账号 %d 个 ==" % len(chosen))
for a, e in chosen.items():
    p("%-22s [%s] %s" % (a, ",".join(e["reason"]),
                         "; ".join("%s(%s)" % (r["stage"], r["status"]) for r in e["rows"])))

# ---------------- 每对的证据尝试 ----------------
def ev_of(a, s):
    """该号所有与该关相关的尝试（原始 state 行）。"""
    res = []
    for k in sorted(attempts):
        at = attempts[k]
        if at.get("acct") != a:
            continue
        ev = sc.classify_attempt(at.get("lines") or [], at.get("drops") or [], at.get("result", ""))
        stages = set(ev["selected"]) | set(ev["not_open"]) | set(ev["dropped"]) | set(ev["drop_log"])
        if ev["annih_done"] or (ev["annih_progress"] and sc.ANNIH_STAGE in ev["selected"]):
            stages.add(sc.ANNIH_STAGE)
        if s in stages:
            res.append({
                "key": k, "day": at["day"], "file": at["file"], "result": at["result"],
                "in_drops": s in ev["dropped"], "in_drop_log": s in ev["drop_log"],
                "in_selected": s in ev["selected"], "in_not_open": s in ev["not_open"],
                "n_lines": len(at.get("lines") or []), "mt": at.get("mt", ""),
            })
    return res

def pick_mid(seq, n=3, rnd=None):
    if len(seq) <= n:
        return list(seq)
    idx = sorted({0, len(seq) - 1, len(seq) // 2, rnd.randrange(len(seq)) if rnd else 0})
    return [seq[i] for i in idx[:n]]

pairs = []
manifest = []
for a, e in chosen.items():
    for r in e["rows"]:
        s = r["stage"]
        evs = ev_of(a, s)
        st = r["status"]
        sel = OrderedDict()
        def add(att, why):
            k = att["key"]
            if k not in sel:
                sel[k] = dict(att, why=[])

        if st == "success":
            succ = [x for x in evs if x["in_drops"] or x["in_drop_log"]]
            for x in pick_mid(succ, 3, rnd):
                add(x, "success证据")
            no_drop = [x for x in evs if (x["in_selected"] or x["in_not_open"])
                       and not (x["in_drops"] or x["in_drop_log"])]
            for x in pick_mid(no_drop, 2, rnd):
                add(x, "对照:选中无掉落(查①)")
        elif st == "failed":
            fev = []
            for f in (r["failures"] or []):
                for x in evs:
                    if x["day"] == f.get("day") and x["file"] == f.get("file"):
                        fev.append(x)
            for x in fev[:3]:
                add(x, "failed证据(%s)" % ",".join((r["failure_types"] or {}).keys()))
            rest = [x for x in evs if x["in_selected"] or x["in_not_open"]]
            for x in pick_mid(rest, 2, rnd):
                add(x, "对照:失败同类")
        else:
            selx = [x for x in evs if x["in_selected"] or x["in_not_open"] or x["in_drops"]]
            for x in pick_mid(selx, 3, rnd):
                add(x, "unknown证据")

        for x in sel.values():
            x["why"] = "; ".join(sorted(set(x.get("why") or [])))
        pairs.append({"account": a, "stage": s, "status": st,
                      "row_summary": {kk: r[kk] for kk in ("success_n", "selected_n", "not_open_n",
                                                           "last_success", "last_failure", "fail_n",
                                                           "net_fail_n", "sources", "impact", "failure_types")},
                      "n_attempts_total": len(evs), "sel": list(sel.values())})
        for x in sel.values():
            manifest.append({"day": x["day"], "acct": a, "file": x["file"],
                             "pair": "%s|%s" % (a, s), "pair_status": st, "why": x["why"]})

p("")
p("== 取证清单 ==")
p("pairs=%d，取证尝试=%d，去重文件=%d" % (len(pairs), len(manifest),
                                    len({(m["day"], m["acct"], m["file"]) for m in manifest})))

# ---------------- 本地全局对照 ----------------
g = []
g.append("== 全局对照（本地 state，仅统计）==")
src_count = Counter(tuple(r["sources"]) for r in success)
g.append("success rows 来源组合：%s" % dict(src_count))
only_log = [r for r in success if r["sources"] == ["fight_log_drop"]]
g.append("仅靠 fight_log_drop 判 success 的行（%d）：%s" % (
    len(only_log), "; ".join("%s/%s" % (r["account"], r["stage"]) for r in only_log[:40])))
only_drop = [r for r in success if r["sources"] == ["drop_statistics"]]
g.append("仅靠 drop_statistics 的行（%d）" % len(only_drop))
# 尝试级：result=Success! 但该关无掉落 —— 这类尝试不应给 success
succ_nodrop = []
for k in attempts:
    at = attempts[k]
    if at.get("result", "") != "Success!":
        continue
    ev = sc.classify_attempt(at.get("lines") or [], at.get("drops") or [])
    if ev["selected"] and not (ev["dropped"] or ev["drop_log"]):
        succ_nodrop.append((at["day"], at["acct"], at["file"], ",".join(ev["selected"])))
g.append("尝试级：result=Success! 且选中过关卡但该次无任何掉落记录（%d 例，前20）：%s"
         % (len(succ_nodrop), " | ".join("%s/%s/%s[%s]" % x for x in succ_nodrop[:20])))
# failed 行里 last_failure 晚于 last_success 的比例
fa = [r for r in failed if r["success_n"] == 0]
g.append("failed 行中 0 次成功的（真没刷成过）：%d / %d" % (len(fa), len(failed)))
# unknown 行的 selected_n 分布
g.append("unknown 行 selected_n：%s" % "; ".join("%s/%s=%d" % (r["account"], r["stage"], r["selected_n"]) for r in unknown))

with open(os.path.join(OUT, "sample2.json"), "w", encoding="utf-8") as f:
    json.dump({"seed": SEED, "chosen": {a: {"reason": e["reason"],
                                            "rows": ["%s(%s)" % (r["stage"], r["status"]) for r in e["rows"]]}
                                        for a, e in chosen.items()},
               "pairs": pairs}, f, ensure_ascii=False, indent=1)
with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump({"seed": SEED, "entries": manifest}, f, ensure_ascii=False, indent=1)
with open(os.path.join(OUT, "global_checks.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(g) + "\n")
with open(os.path.join(OUT, "sample_design.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(out_lines) + "\n")

print("\n".join(out_lines))
print()
print("\n".join(g))
