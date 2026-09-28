#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤11：最终独立复核（含 json↔log 掉落交叉核对 + 环境感知判定）。

判定规则（独立于台账实现）：
  success  : 任一尝试 原始 json drop_statistics 含该关 或 原生日志含「<关> 掉落统计」块
             （剿灭额外允许「完成任务: 剿灭作战」）
  failed(env): 该关 Fight 链报错 且 同尝试内出现掉线/重连类环境标记（→ 不代表关卡不可刷）
  failed   : 该关 Fight 链报错（无环境标记）或「selected null」型
  unknown  : 其余（从未有成/从未有卡级失败证据）
台账期望映射：success→success；failed→failed；failed(env)→台账应记忆成 unknown+impact=env（不得是 failed）。
"""
import json
import os
import re
import sys
from collections import OrderedDict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
import qa6_analyze as A  # noqa: E402

OUT = HERE
with open(os.path.join(OUT, "sample2.json"), encoding="utf-8") as f:
    pairs = json.load(f)["pairs"]
ledger = {("%s|%s" % (p["account"], p["stage"])): p for p in pairs}

entries = A.parse()
by_pair = OrderedDict()
for e in entries:
    by_pair.setdefault(e["pair"], []).append(e)

results = []
drop_mismatch = []
succ_nodrop = []
for pair, ents in by_pair.items():
    acct, stage = pair.split("|", 1)
    led = ledger.get(pair)
    if led is None:
        continue
    succ_att, notopen_att, err_att = [], [], []
    for e in ents:
        r = A.analyze_attempt(e, stage)
        # json↔log 掉落交叉核对
        log_blocks = []
        for ln in e["lines"]:
            m = A.RE_DROP.match(ln)
            if m:
                log_blocks.append(m.group("st"))
        jd, ld = set(e["drops"]), set(log_blocks)
        if jd != ld:
            drop_mismatch.append({"pair": pair, "day": e["day"], "file": e["file"],
                                  "json": sorted(jd), "log": sorted(ld)})
        # 记录
        is_succ = (stage in jd) or (stage in ld) or (stage == "Annihilation" and r["annih_done"])
        if is_succ:
            succ_att.append((e["day"], e["file"], sorted(jd)))
        elif r["not_open"]:
            notopen_att.append((e["day"], e["file"]))
        else:
            for fe in r["fight_err"]:
                if fe.get("stage") == stage:
                    env = bool(r["env_marks"]) and any(
                        k in "\n".join(r["env_marks"]) for k in ("掉线", "重连", "连接失败", "连接断开", "截图失败"))
                    err_att.append({"day": e["day"], "file": e["file"], "env": env,
                                    "actions": fe.get("actions_in_chain"), "line": fe.get("line"),
                                    "env_lines": [x for x in r["env_marks"] if x][:4]})
        # ① 类：Success! 但无该关任何掉落记录（且该关被选中）
        if e["result"] == "Success!" and not is_succ and (stage in r["sel"]):
            succ_nodrop.append((pair, e["day"], e["file"]))

    if succ_att:
        verdict = "success"
    elif notopen_att:
        verdict = "failed"
    elif err_att:
        verdict = "failed(env)" if all(x["env"] for x in err_att) else "failed"
    else:
        verdict = "unknown"
    # 台账期望
    led_status = led["status"]
    led_impact = led["row_summary"].get("impact", "")
    if verdict == "failed(env)":
        ok = (led_status == "unknown" and led_impact == "env") or led_status == "failed"
    elif verdict == "failed":
        ok = led_status == "failed"
    else:
        ok = led_status == verdict
    results.append({
        "pair": pair, "account": acct, "stage": stage, "ledger": led_status,
        "ledger_impact": led_impact, "verdict": verdict, "ok": ok,
        "n_att": len(ents), "succ_att": succ_att, "notopen_att": notopen_att, "err_att": err_att,
        "ledger_row": led["row_summary"],
    })

summary = {
    "pairs": len(results),
    "ok": sum(1 for r in results if r["ok"]),
    "by_ledger_status": {st: {"n": sum(1 for r in results if r["ledger"] == st),
                              "ok": sum(1 for r in results if r["ledger"] == st and r["ok"])}
                         for st in ("success", "failed", "unknown")},
    "json_vs_log_drop_mismatch": len(drop_mismatch),
    "success_nodrop_attempts_not_used": len(succ_nodrop),
}
with open(os.path.join(OUT, "verify_final.json"), "w", encoding="utf-8") as f:
    json.dump({"summary": summary, "results": results,
               "drop_mismatch": drop_mismatch, "succ_nodrop": succ_nodrop},
              f, ensure_ascii=False, indent=1)

# 终表（人读）
L = []
L.append("# 最终复核表（独立判定 vs 台账）")
L.append("")
L.append("| # | 账号 | 关卡 | 台账 | 独立复核 | 一致 | 证据摘要 |")
L.append("|---|---|---|---|---|---|---|")
for i, r in enumerate(sorted(results, key=lambda x: (x["ledger"], x["account"], x["stage"])), 1):
    ev = ""
    if r["verdict"] == "success":
        ev = "成功 %d/%d 次；例 %s" % (len(r["succ_att"]), r["n_att"],
                                      ", ".join("%s(%s)" % (d, "/".join(dd) if dd else "log块") for d, _, dd in r["succ_att"][:2]))
    elif r["verdict"] == "failed":
        if r["notopen_att"]:
            ev = "selected null ×%d；例 %s" % (len(r["notopen_att"]), r["notopen_att"][0][0])
        else:
            ev = "Fight 链报错(未开打) ×%d；例 %s" % (len(r["err_att"]), r["err_att"][0]["day"])
    elif r["verdict"] == "failed(env)":
        ev = "链报错+环境标记 ×%d；例 %s %s" % (len(r["err_att"]), r["err_att"][0]["day"],
                                             (r["err_att"][0]["env_lines"] or [""])[-1][:60])
    else:
        ev = "无成功记录；%d 次相关尝试" % r["n_att"]
    L.append("| %d | %s | %s | %s%s | %s | %s | %s |" % (
        i, r["account"], r["stage"], r["ledger"],
        ("/env" if r["ledger_impact"] == "env" else ""), r["verdict"], "✅" if r["ok"] else "❌", ev))
L.append("")
L.append("核对摘要：`%s`" % json.dumps(summary, ensure_ascii=False))
L.append("")
L.append("json↔log 掉落不一致（%d 条）" % len(drop_mismatch))
for d in drop_mismatch[:20]:
    L.append("- %s %s/%s: json=%s log=%s" % (d["pair"], d["day"], d["file"], d["json"], d["log"]))
L.append("")
L.append("① 类样本（result=Success! 但该关无任何掉落记录 → 不得算 success；共 %d 条抽到）" % len(succ_nodrop))
for s in succ_nodrop[:20]:
    L.append("- %s %s/%s" % s)
with open(os.path.join(OUT, "verify_final.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print("\n".join(L))
