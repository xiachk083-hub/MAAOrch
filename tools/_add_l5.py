# -*- coding: utf-8 -*-
"""l-5 加入肉鸽:复制 MAA_13 -> 建脚本(模拟器34) -> 建用户(肉鸽专用) -> 建队列 临时-肉鸽-l-5(05:39/20:39)。"""
import json
import subprocess
import sys
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


# 0) 复制 MAA_12 -> MAA_13(robocopy)
r = subprocess.run(
    ["robocopy", r"E:\MAA-Multi\MAA_12", r"E:\MAA-Multi\MAA_13", "/E", "/R:1", "/W:1", "/NFL", "/NDL"],
    capture_output=True, text=True,
)
print("robocopy rc:", r.returncode, "(0-7 正常)")

# 1) 建脚本
s = api("/api/scripts/add", {"type": "MAA"})
print("add script:", s.get("code"), s.get("message", ""))
sid = s.get("scriptId") or s.get("data", {}).get("uid")
print("sid:", sid)

# 2) 脚本配置
api("/api/scripts/update", {"scriptId": sid, "data": {
    "Info": {"Name": "l-5-古茶七心红", "Path": "E:/MAA-Multi/MAA_13"},
    "Emulator": {"Id": "6e09a6bb-3126-412e-8412-9c90fb0c4499", "Index": "34"},
    "Run": {"TaskTransitionMethod": "ExitEmulator", "RunTimesLimit": 3},
}})
print("script config done")

# 3) 建用户
u = api("/api/scripts/user/add", {"scriptId": sid})
print("add user:", u.get("code"), u.get("message", ""))
uid = u.get("userId") or (list((u.get("data") or {}).keys())[0] if u.get("data") else None)
print("uid:", uid)

# 4) 用户配置(肉鸽专用)
api("/api/scripts/user/update", {"scriptId": sid, "userId": uid, "data": {
    "Info": {"Name": "l-5-古茶七心红", "Server": "Official", "Status": True,
             "Stage": "1-7", "StageMode": "Fixed", "Annihilation": "Close", "Mode": "简洁"},
    "Task": {"IfStartUp": True, "IfFight": False, "IfInfrast": False, "IfRecruit": False,
             "IfMall": False, "IfAward": False, "IfRoguelike": True, "IfReclamation": False},
}})
print("user config done")

# 5) 队列口子
q = api("/api/queue/add", {}).get("queueId")
api("/api/queue/update", {"queueId": q, "data": {"Info": {"Name": "临时-肉鸽-l-5", "TimeEnabled": True}}})
iid = api("/api/queue/item/add", {"queueId": q}).get("queueItemId")
api("/api/queue/item/update", {"queueId": q, "queueItemId": iid, "data": {"Info": {"ScriptId": sid}}})
for hm in ("05:39", "20:39"):
    tsid = api("/api/queue/time/add", {"queueId": q}).get("timeSetId")
    api("/api/queue/time/update", {"queueId": q, "timeSetId": tsid,
                                   "data": {"Info": {"Enabled": True, "Days": ALL_DAYS, "Time": hm}}})
print("queue 临时-肉鸽-l-5 done (05:39/20:39)")
print("ALL DONE sid=%s uid=%s qid=%s" % (sid, uid, q))
