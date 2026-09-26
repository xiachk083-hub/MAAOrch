# -*- coding: utf-8 -*-
"""l-3 加入肉鸽(日服): 脚本(模拟器44,MAA_14) + 用户(日服肉鸽专用) + 队列 临时-肉鸽-l-3(05:42/20:42)。"""
import json
import urllib.request

API = "http://100.79.173.69:36163"
ALL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def api(path, body, timeout=60):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


s = api("/api/scripts/add", {"type": "MAA"})
print("add script:", s.get("code"), s.get("message", ""))
sid = s.get("scriptId") or (list((s.get("data") or {}).keys())[0] if s.get("data") else None)
print("sid:", sid)

api("/api/scripts/update", {"scriptId": sid, "data": {
    "Info": {"Name": "l-3-青吟巷滑翔花椒", "Path": "E:/MAA-Multi/MAA_14"},
    "Emulator": {"Id": "6e09a6bb-3126-412e-8412-9c90fb0c4499", "Index": "44"},
    "Run": {"TaskTransitionMethod": "ExitEmulator", "RunTimesLimit": 3},
}})
print("script config done")

u = api("/api/scripts/user/add", {"scriptId": sid})
uid = u.get("userId") or (list((u.get("data") or {}).keys())[0] if u.get("data") else None)
print("uid:", uid)

api("/api/scripts/user/update", {"scriptId": sid, "userId": uid, "data": {
    "Info": {"Name": "l-3-青吟巷滑翔花椒", "Server": "YoStarJP", "Status": True,
             "Stage": "1-7", "StageMode": "Fixed", "Annihilation": "Close", "Mode": "简洁"},
    "Task": {"IfStartUp": True, "IfFight": False, "IfInfrast": False, "IfRecruit": False,
             "IfMall": False, "IfAward": False, "IfRoguelike": True, "IfReclamation": False},
}})
print("user config done")

q = api("/api/queue/add", {}).get("queueId")
api("/api/queue/update", {"queueId": q, "data": {"Info": {"Name": "临时-肉鸽-l-3", "TimeEnabled": True}}})
iid = api("/api/queue/item/add", {"queueId": q}).get("queueItemId")
api("/api/queue/item/update", {"queueId": q, "queueItemId": iid, "data": {"Info": {"ScriptId": sid}}})
for hm in ("05:42", "20:42"):
    tsid = api("/api/queue/time/add", {"queueId": q}).get("timeSetId")
    api("/api/queue/time/update", {"queueId": q, "timeSetId": tsid,
                                   "data": {"Info": {"Enabled": True, "Days": ALL_DAYS, "Time": hm}}})
print("queue 临时-肉鸽-l-3 done (05:42/20:42)")
print("ALL DONE sid=%s uid=%s qid=%s" % (sid, uid, q))
