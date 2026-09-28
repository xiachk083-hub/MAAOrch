import json, os, datetime as dt
from collections import defaultdict
ROOT=r"D:\1-2\MAAOrch"
state=json.load(open(os.path.join(ROOT,"runtime","state","stage_capability_state.json"),encoding="utf-8"))["attempts"]
WD="一二三四五六日"
agg=defaultdict(lambda: defaultdict(int))
for k,v in state.items():
    d=dt.date(*map(int,v["day"].split("-"))); wd=WD[d.weekday()]
    for stg in (v.get("drops") or {}):
        if stg in ("LS-6","LS-5","CE-6","CA-5","AP-5","AT-4","AT-6","AT-8","SR-5","SR-7","SR-8"):
            agg[stg][wd]+=1
print("== 各资源/活动关 成功尝试 按星期分布 ==")
for stg in sorted(agg):
    tot=sum(agg[stg].values())
    print("  %-5s 共%-4d %s" % (stg, tot, dict(agg[stg])))
print()
print("== 烤箱 LS-6 相关尝试（看配置列表） ==")
for k,v in sorted(state.items(), key=lambda kv:(kv[1]["day"],kv[1]["file"])):
    if v["acct"]=="烤箱":
        hit=[ln for ln in (v.get("lines") or []) if "GetFightStage" in ln or "掉落统计" in ln or "开始行动" in ln]
        if any("LS-6" in x or "LS-6" in str(v.get("drops")) for x in hit) or "LS-6" in (v.get("drops") or {}):
            print("  %s %s drops=%s" % (v["day"], v["file"], v.get("drops")))
            for ln in hit: print("     |", ln)
