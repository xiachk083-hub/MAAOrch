import json, os, sys
ROOT=r"D:\1-2\MAAOrch"; sys.path.insert(0, os.path.join(ROOT,"tools"))
import stage_capability as sc
st=json.load(open(os.path.join(ROOT,"runtime","state","stage_capability_state.json"),encoding="utf-8"))["attempts"]
for k in sorted(st):
    a=st[k]
    if a["acct"]=="檀丝江喜" and a["file"]=="明日方舟-官-14-02-39-10.json":
        print("== ", a["day"], a["file"], a["result"])
        for ln in a["lines"]: print("  |", ln)
