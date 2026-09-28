import json, os
ROOT=r"D:\1-2\MAAOrch"
rep=json.load(open(os.path.join(ROOT,"runtime","reports","stage_capability.json"),encoding="utf-8"))
state=json.load(open(os.path.join(ROOT,"runtime","state","stage_capability_state.json"),encoding="utf-8"))["attempts"]
k0=next(iter(state)); print("attempt keys:", sorted(state[k0].keys()))
print("== SR-7 failed 行: not_open_n / fail_n / failures[type@day] ==")
for r in rep["rows"]:
    if r["stage"]=="SR-7" and r["status"]=="failed":
        fs=r.get("failures") or []
        print("  %-16s not_open_n=%-3s fail_n=%-3s | %s" % (r["account"], r.get("not_open_n"), r.get("fail_n"),
              " ".join("%s@%s" % (f.get("type"), f.get("day")) for f in fs[:5])))
print()
print("== 寻找勇者的大魔王 / 摸摸摸摸鱼 的 LS-6 尝试（state 全量）==")
for k,v in sorted(state.items()):
    if v["acct"] in ("寻找勇者的大魔王","摸摸摸摸鱼"):
        stg=(v.get("sel") or [{}])
        if "LS-6" in (v.get("stages") or []) or "LS-6" in (v.get("sel") or []) or "LS-6" in (v.get("drops") or {}):
            print("  %s %s | %s | drops=%s | sel=%s" % (v["acct"], v["day"], v["file"], v.get("drops"), v.get("sel")))
            for ln in v.get("lines") or []:
                print("     |", ln)
