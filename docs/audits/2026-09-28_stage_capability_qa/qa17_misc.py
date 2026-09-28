import json, os
ROOT=r"D:\1-2\MAAOrch"
state=json.load(open(os.path.join(ROOT,"runtime","state","stage_capability_state.json"),encoding="utf-8"))["attempts"]
print("== 河清灯 1-7: result × drops ==")
for k,v in sorted(state.items(), key=lambda kv:(kv[1]["day"],kv[1]["file"])):
    if v["acct"]=="河清灯_别寄圆通" and "1-7" in (v.get("drops") or {}):
        print("  %s %s | result=%s | drops=%s" % (v["day"], v["file"], v["result"], v.get("drops")))
print()
print("== v 所有尝试概览 ==")
for k,v in sorted(state.items(), key=lambda kv:(kv[1]["day"],kv[1]["file"])):
    if v["acct"]=="v":
        print("  %s %s | drops=%s | result=%s" % (v["day"], v["file"], v.get("drops"), v["result"]))
