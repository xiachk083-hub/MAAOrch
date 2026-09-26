# -*- coding: utf-8 -*-
"""早班对账明细"""
import json

d = json.load(open(r"D:/1-2/MAAOrch/tools/_history_0824.json", encoding="utf-8"))
users = d.get("data", {}).get("2026-08-24", {})
sc = json.load(open(r"D:/1-2/MAAOrch/tools/_scriptconfig_dump.json", encoding="utf-8"))

allnames = {}
for sid, v in sc.items():
    if sid == "instances":
        continue
    path = (v.get("Info") or {}).get("Path") or ""
    path = path.replace("E:\\MAA-Multi\\", "").replace("E:/MAA-Multi/", "")
    ud = (v.get("SubConfigsInfo") or {}).get("UserData") or {}
    for uid, entry in ud.items():
        if uid == "instances":
            continue
        info = entry.get("Info") or {}
        if info.get("Status") is True:
            allnames[info.get("Name")] = path

print("在册启用:", len(allnames))
missing = [n for n in allnames if n not in users]
print("无记录:", missing)
print()
for name in sorted(users):
    rec = users[name]
    idx = rec.get("index") or []
    tl = " -> ".join("%s/%s" % (i.get("date", "")[-8:], i.get("status")) for i in idx)
    print("%-18s %-12s %s" % (name[:18], allnames.get(name, "?"), tl))
