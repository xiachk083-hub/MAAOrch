#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""t_84711bab 步骤6：独立复核（原始证据 -> 结论），与台账逐对对照。

输入：raw/batch_*.txt（qa4 取回）
输出：verify.json（机读）+ verify_table.md（人读表）+ verify_evidence.md（逐对证据卡）
"""
import json
import os
import re
import sys
from collections import OrderedDict

ROOT = r"D:\1-2\MAAOrch"
OUT = os.path.join(ROOT, "docs", "audits", "2026-09-28_stage_capability_qa")
RAW = os.path.join(OUT, "raw")

RE_PREFIX = re.compile(r"^\[\d{4}-\d{2}-\d{2} [\d:.]+\]\[\w+\]\[[^\]]*\]\s*")
RE_LTP = re.compile(r"^<\d+>\s*")
RE_SEL = re.compile(r"^GetFightStage: from \[(?P<lst>[^\]]*)\], selected (?P<sel>.*)$")
RE_DROP = re.compile(r"^(?P<st>\S+)\s+掉落统计\s*:?\s*$")
RE_CHAINERR = re.compile(r"^任务出错[:：]\s*(?P<chain>\S+)\s*$")
RE_CHAINSTART = re.compile(r"^Start Task Chain: (?P<chain>\w+)")
RE_ACTION = re.compile(r"^开始行动 ")
ENV_MARKS = ("重连", "掉线", "连接失败", "连接断开", "检查连接设置", "模拟器", "截图失败", "客户端",
             "未就绪", "CloseAnno", "唤醒", "登录", "重试", "超时", "冻结")


def s(line):
    ln = RE_PREFIX.sub("", line.strip())
    ln = RE_LTP.sub("", ln)
    return ln.strip()


def parse():
    entries = []
    cur = None
    mode = None
    for bn in sorted(os.listdir(RAW)):
        if not bn.startswith("batch_") or not bn.endswith(".txt"):
            continue
        with open(os.path.join(RAW, bn), "r", encoding="utf-8") as f:
            txt = f.read()
        txt = txt.replace("\r\n", "\n").replace("\r", "")
        for line in txt.split("\n"):
            if line.startswith("###ENTRY|"):
                if cur:
                    entries.append(cur)
                # 注意：pair 里本身含 "|"（account|stage），故按位置解析
                parts = line.split("|")
                day, acct, file = parts[1], parts[2], parts[3]
                pair = parts[4] + "|" + parts[5]
                why = "|".join(parts[6:])
                cur = {"day": day, "acct": acct, "file": file, "pair": pair, "why": why,
                       "json_txt": [], "log_lines": [], "log_n": 0, "log_bytes": 0,
                       "head": [], "tail": [], "batch": bn}
                mode = "json"
            elif cur is None:
                continue
            elif line == "###JSON":
                mode = "json"
            elif line == "###JSON_END":
                mode = None
            elif line.startswith("###LOG|"):
                mode = "head"
                parts = line.split("|")
                for pt in parts[1:]:
                    if pt.startswith("lines="):
                        cur["log_n"] = int(pt[6:])
                    elif pt.startswith("bytes="):
                        cur["log_bytes"] = int(pt[6:])
            elif line == "##HEAD":
                mode = "head"
            elif line == "##FILTER":
                mode = "filter"
            elif line == "##TAIL":
                mode = "tail"
            elif line == "###END":
                entries.append(cur)
                cur = None
                mode = None
            else:
                if mode == "json":
                    cur["json_txt"].append(line)
                elif mode == "head":
                    cur["head"].append(line)
                elif mode == "filter":
                    cur["log_lines"].append(line)
                elif mode == "tail":
                    cur["tail"].append(line)
    if cur:
        entries.append(cur)
    for e in entries:
        jt = "\n".join(e["json_txt"]).strip()
        e["result"], e["drops"], e["sanity"] = "", [], ""
        if jt and not jt.startswith("(JSON"):
            try:
                j = json.loads(jt)
                e["result"] = str(j.get("maa_result", ""))
                e["drops"] = sorted((j.get("drop_statistics") or {}).keys())
                e["sanity"] = j.get("sanity", "")
            except Exception as ex:  # noqa: BLE001
                e["json_err"] = str(ex)
        e["lines"] = [s(x) for x in e["log_lines"]]
    return entries


def analyze_attempt(a, stage):
    """独立复核一次尝试：证据 + 上下文判定。"""
    r = {"result": a["result"], "drops": a["drops"], "log_n": a["log_n"],
         "drop_evidence": stage in a["drops"], "drop_log": False, "not_open": False,
         "annih_done": False, "fight_err": [], "actions": 0, "chain_map": [],
         "env_marks": [], "other_chain_errs": [], "sel": [], "not_open_from": "",
         "last_actions_before_err": None}
    sel_list, fight_idx, cur_stage = [], -1, None
    actions_in_chain = 0
    ERR = None
    for ln in a["lines"]:
        m = RE_SEL.match(ln)
        if m:
            st = m.group("sel").strip()
            r["sel"].append(st)
            if st not in ("null", ""):
                sel_list.append(st)
            else:
                lst = [x.strip().strip('"') for x in (m.group("lst") or "").split(",") if x.strip()]
                if stage in lst:
                    r["not_open"] = True
                    r["not_open_from"] = ln
            continue
        m = RE_DROP.match(ln)
        if m and m.group("st") == stage:
            r["drop_log"] = True
            continue
        if "完成任务: 剿灭作战" in ln:
            r["annih_done"] = True
            continue
        m = RE_CHAINSTART.match(ln)
        if m and m.group("chain") == "Fight":
            if "Start Task Chain" in ln:
                fight_idx += 1
                cur_stage = sel_list[fight_idx] if fight_idx < len(sel_list) else (sel_list[-1] if sel_list else None)
                r["chain_map"].append(cur_stage)
                actions_in_chain = 0
                ERR = None
            continue
        if RE_ACTION.match(ln):
            r["actions"] += 1
            actions_in_chain += 1
            continue
        m = RE_CHAINERR.match(ln)
        if m:
            if m.group("chain") == "理智作战":
                r["fight_err"].append({"stage": cur_stage, "actions_in_chain": actions_in_chain,
                                       "line": ln, "ctx_env": None})
                ERR = r["fight_err"][-1]
            else:
                r["other_chain_errs"].append(m.group("chain"))
            continue
        if "代理指挥失误" in ln:
            r["fight_err"].append({"stage": cur_stage, "kind": "proxy", "line": ln})
            continue
    # 环境标记（仅取与 Fight 报错同一尝试的上下文——整次扫描即可，人工复核时再看时序）
    for ln in a["lines"]:
        for mk in ENV_MARKS:
            if mk in ln and ln not in r["env_marks"]:
                r["env_marks"].append(ln)
                break
    return r


def main():
    entries = parse()
    by_pair = OrderedDict()
    for e in entries:
        by_pair.setdefault(e["pair"], []).append(e)
    with open(os.path.join(OUT, "sample2.json"), encoding="utf-8") as f:
        pairs = json.load(f)["pairs"]
    ledger = {("%s|%s" % (p["account"], p["stage"])): p for p in pairs}

    results, cards = [], []
    for pair, ents in by_pair.items():
        acct, stage = pair.split("|", 1)
        led = ledger.get(pair)
        if led is None:
            continue
        per = []
        for e in ents:
            r = analyze_attempt(e, stage)
            r.update({"day": e["day"], "file": e["file"], "why": e["why"]})
            per.append(r)
        # 独立结论
        succ = [r for r in per if r["drop_evidence"] or r["drop_log"]
                or (stage == "Annihilation" and r["annih_done"])]
        not_open = [r for r in per if r["not_open"]]
        fight_err = []
        for r in per:
            for fe in r["fight_err"]:
                if fe.get("stage") == stage:
                    fight_err.append(dict(fe, day=r["day"], file=r["file"],
                                          env=r["env_marks"][:3], other=r["other_chain_errs"][:3]))
        verdict = "success" if succ else ("failed" if (not_open or fight_err) else "unknown")
        results.append({
            "pair": pair, "account": acct, "stage": stage, "ledger": led["status"],
            "verdict": verdict, "n_evidence": len(per),
            "n_succ": len(succ), "n_not_open": len(not_open), "n_fight_err": len(fight_err),
            "ledger_row": led["row_summary"],
            "succ_att": [(r["day"], r["file"]) for r in succ],
            "not_open_att": [(r["day"], r["file"], r["not_open_from"]) for r in not_open],
            "fight_err_att": fight_err,
        })
        # 证据卡
        card = ["## %s ｜ 台账=%s ｜ 独立复核=%s" % (pair, led["status"], verdict)]
        card.append("- 台账行：`%s`" % json.dumps(led["row_summary"], ensure_ascii=False))
        card.append("- 抽样尝试 %d 条：" % len(per))
        for r in per:
            card.append("  - `%s / %s`（%s）result=`%s` drops=%s log=%d行 选中=%s 掉落证据=%s 掉落日志=%s 未开放=%s actions=%d 链映射=%s"
                        % (r["day"], r["file"], r["why"], r["result"], ",".join(r["drops"]) or "-",
                           r["log_n"], ",".join(r["sel"]) or "-", r["drop_evidence"], r["drop_log"],
                           r["not_open"], r["actions"], r["chain_map"]))
            if r["fight_err"]:
                card.append("    - fight_err: %s" % json.dumps(r["fight_err"], ensure_ascii=False))
            if r["env_marks"]:
                card.append("    - env_marks: %s" % " / ".join(r["env_marks"][:6]))
            if r["other_chain_errs"]:
                card.append("    - 其他链报错: %s" % ",".join(r["other_chain_errs"]))
        cards.append("\n".join(card))

    match = sum(1 for r in results if r["ledger"] == r["verdict"])
    summary = {
        "checked_pairs": len(results),
        "match": match,
        "mismatch": len(results) - match,
        "by_status": {},
    }
    for st in ("success", "failed", "unknown"):
        sub = [r for r in results if r["ledger"] == st]
        summary["by_status"][st] = {
            "n": len(sub), "match": sum(1 for r in sub if r["verdict"] == st)}
    with open(os.path.join(OUT, "verify.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "verify_evidence.md"), "w", encoding="utf-8") as f:
        f.write("# 逐对证据卡\n\n" + "\n\n---\n\n".join(cards) + "\n")
    # 人读表
    L = ["| 账号 | 关卡 | 台账 | 复核 | 抽样/成功/未开/链错 | 备注 |", "|---|---|---|---|---|---|"]
    for r in results:
        note = ""
        if r["ledger"] != r["verdict"]:
            note = "**不一致**"
        elif r["ledger"] == "failed":
            note = "链错证据 " + ("; ".join("%s/%s" % (a["day"], a["file"]) for a in r["fight_err_att"][:2]) or "-")
        elif r["ledger"] == "success":
            note = "落 " + ("; ".join("%s" % (a[0]) for a in r["succ_att"][:3]) or "-")
        else:
            note = "无成功证据"
        L.append("| %s | %s | %s | %s | %d/%d/%d/%d | %s |" % (
            r["account"], r["stage"], r["ledger"], r["verdict"], r["n_evidence"], r["n_succ"],
            r["n_not_open"], r["n_fight_err"], note))
    with open(os.path.join(OUT, "verify_table.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("\n".join(L))


if __name__ == "__main__":
    main()
