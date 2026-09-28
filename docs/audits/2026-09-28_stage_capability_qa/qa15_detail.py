import json, os, sys
from collections import Counter, defaultdict
ROOT=r"D:\1-2\MAAOrch"
OUT=os.path.join(ROOT,"docs","audits","2026-09-28_stage_capability_qa")
rep=json.load(open(os.path.join(ROOT,"runtime","reports","stage_capability.json"),encoding="utf-8"))
state=json.load(open(os.path.join(ROOT,"runtime","state","stage_capability_state.json"),encoding="utf-8"))["attempts"]

print("== 河清灯/CA-5 行全文 ==")
for r in rep["rows"]:
    if r["account"]=="河清灯_别寄圆通" and r["stage"]=="CA-5":
        print(json.dumps(r, ensure_ascii=False, indent=1))

print("== SR-7 failed 行的失败日/类型时序（抽样 8 行里 4 行 + 其他行的 failures 摘录）==")
for r in rep["rows"]:
    if r["stage"]=="SR-7" and r["status"]=="failed":
        fs=r.get("failures") or []
        print("  %-16s %s" % (r["account"], " | ".join("%s:%s" % (f.get("day"), "+".join(sorted(f.get("types") or {}))) for f in fs[:6])))

print("== 寻找勇者的大魔王 / 摸摸摸摸鱼 的 LS-6 尝试 ==")
for k,v in sorted(state.items(), key=lambda kv:(kv[1]["acct"],kv[1]["day"],kv[1]["file"])):
    if v["acct"] in ("寻找勇者的大魔王","摸摸摸摸鱼") and v["act"]=="LS-6":
        print("  %s %s | %s | drops=%s | lines=%d" % (v["acct"],v["day"],v["file"],v.get("drops"),len(v.get("lines") or [])))
        for ln in v.get("lines") or []:
            print("     |", ln)
