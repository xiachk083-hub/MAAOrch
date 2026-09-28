#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤10：全量 25 failed / 6 unknown 行的签名审计（本地 state，标注需人查的）。

签名维度（每次失败尝试）：
  net      : 该尝试内出现掉线/重连/连接失败等 NET_MARK（→ 环境类，不该判 failed）
  login    : 出现「未能正确登录 PRTS / 任务出错: 开始唤醒 / 未开始监看」类客户端信号
  otherch  : 同尝试内其他链也报错（连锁崩 = 客户端未就绪嫌疑）
  silent   : Fight 链报错且该链 0 次「开始行动」（关卡级静默失败——已抽样的样本全是这种）
  notopen  : selected null 型
"""
import json
import os
import sys
from collections import Counter

ROOT = r"D:\1-2\MAAOrch"
OUT = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import stage_capability as sc  # noqa: E402

with open(os.path.join(ROOT, "runtime", "reports", "stage_capability.json"), encoding="utf-8") as f:
    rep = json.load(f)
with open(os.path.join(ROOT, "runtime", "state", "stage_capability_state.json"), encoding="utf-8") as f:
    attempts = json.load(f)["attempts"]

LOGIN_MARKS = ("未能正确登录", "任务出错: 开始唤醒", "未开始监看", "模拟器启动失败")


def attempt_signature(a):
    lines = a.get("lines") or []
    ev = sc.classify_attempt(lines, a.get("drops") or [], a.get("result", ""))
    txt = "\n".join(lines)
    sig = {
        "net_marks": sum(1 for m in sc.NET_MARKS if m in txt) > 0,
        "login_marks": [m for m in LOGIN_MARKS if m in txt],
        "other_chain_errs": [ch for ch in ev["other_chain_errs"]],
        "n_lines": len(lines),
    }
    return ev, sig


def find_attempt(day, acct, fname):
    for k, v in attempts.items():
        if v.get("day") == day and v.get("acct") == acct and v.get("file") == fname:
            return v
    return None


p = []
p.append("== 全量 failed 行（25）签名审计 ==")
p.append("%-20s %-12s %-9s %-6s %-6s %-24s %s" % ("account", "stage", "type", "net", "login", "other_chain_errs", "day/file"))
flagged = []
for r in [x for x in rep["rows"] if x["status"] == "failed"]:
    for f in r["failures"]:
        at = find_attempt(f["day"], r["account"], f["file"])
        if at is None:
            p.append("%-20s %-12s %-9s --" % (r["account"], r["stage"], f["type"]) + "  (state 里找不到该尝试)")
            continue
        ev, sig = attempt_signature(at)
        flag = ""
        if sig["net_marks"] or sig["login_marks"] or len(sig["other_chain_errs"]) >= 2:
            flag = "  <<< 需人查"
            flagged.append((r["account"], r["stage"], f["type"], f["day"], f["file"],
                            sig["net_marks"], sig["login_marks"], sig["other_chain_errs"], at.get("result")))
        p.append("%-20s %-12s %-9s %-6s %-6s %-24s %s%s" % (
            r["account"], r["stage"], f["type"][:9], sig["net_marks"], bool(sig["login_marks"]),
            ",".join(sig["other_chain_errs"])[:24] or "-", "%s/%s" % (f["day"], f["file"]), flag))

p.append("")
p.append("== 全量 unknown 行（6）成因 ==")
for r in [x for x in rep["rows"] if x["status"] == "unknown"]:
    p.append("--- %s / %s（sel=%d not_open=%d net=%d）" % (
        r["account"], r["stage"], r["selected_n"], r["not_open_n"], r["net_fail_n"]))
    cnt = 0
    for k in sorted(attempts):
        at = attempts[k]
        if at["acct"] != r["account"]:
            continue
        ev = sc.classify_attempt(at.get("lines") or [], at.get("drops") or [], at.get("result", ""))
        stages = set(ev["selected"]) | set(ev["not_open"]) | set(ev["dropped"]) | set(ev["drop_log"])
        if r["stage"] not in stages:
            continue
        cnt += 1
        ev2, sig = attempt_signature(at)
        p.append("    %s | %s | result=%r | sel=%s | net=%s | login=%s | 其他链=%s | fights=%s"
                 % (at["day"], at["file"], at["result"], r["stage"] in ev["selected"],
                    sig["net_marks"], bool(sig["login_marks"]), ",".join(sig["other_chain_errs"]) or "-",
                    [(x["stage"], x["kind"]) for x in ev["fights"]]))
    p.append("    （共 %d 次相关尝试）" % cnt)

p.append("")
p.append("== failed 行类型计数 ==")
p.append(str(Counter(tuple(x["failure_types"].keys()) for x in rep["rows"] if x["status"] == "failed")))
p.append("")
p.append("== 需人查清单（%d）==" % len(flagged))
for x in flagged:
    p.append("  %s" % (x,))

txt = "\n".join(p)
with open(os.path.join(OUT, "signature_audit.txt"), "w", encoding="utf-8") as f:
    f.write(txt + "\n")
print(txt)
