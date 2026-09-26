# -*- coding: utf-8 -*-
"""今晚对账：在册启用账号 vs 今晚(>=19:00)历史记录。"""
import json
import urllib.request

API = "http://100.79.173.69:36163"


def api(path, body=None, timeout=60):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def normalize_path(p):
    return (p or "").replace("\\", "/").replace("E:/MAA-Multi/", "")


# 1) 在册启用账号
sc = api("/api/scripts/get", {"scriptId": None})
enabled = {}
for it in sc.get("index", []):
    sid = it["uid"]
    d = sc["data"][sid]
    u = api("/api/scripts/user/get", {"scriptId": sid, "userId": None})
    for uid, uv in (u.get("data") or {}).items():
        if uid == "instances":
            continue
        info = uv.get("Info") or {}
        if info.get("Status") is True:
            enabled[info.get("Name", "?")] = normalize_path(d["Info"].get("Path"))

# 2) 今晚历史
r = api("/api/history/search", {"mode": "DAILY",
                                "start_date": "2026-08-29", "end_date": "2026-08-29"})
data = (r.get("data") or {}).get("2026-08-29") or {}

EVEN, ISSUE = set(), {}
for name, rec in data.items():
    idx = rec.get("index") or []
    even = [i for i in idx if (i.get("date") or "")[11:19] >= "19:00"]
    if even:
        EVEN.add(name)
        sts = [i.get("status") for i in even]
        if any(s == "ERROR" for s in sts):
            ISSUE[name] = {"times": [i.get("date", "")[11:19] for i in even],
                           "status": sts}

print("在册启用账号数:", len(enabled))
missing = sorted(n for n in enabled if n not in EVEN)
print("== 今晚(>=19:00)无任何记录的账号:", len(missing))
for n in missing:
    print("   ", n, enabled[n])
print()
print("== 今晚有记录但含 ERROR 的账号:", len(ISSUE))
for n, v in sorted(ISSUE.items()):
    print("   ", n, "| Status:", v["status"], "| 次数:", v["times"])
print()
print("== 今晚正常完成:", len(EVEN) - len(ISSUE) - len(missing), "个")
