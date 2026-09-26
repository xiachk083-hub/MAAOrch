# -*- coding: utf-8 -*-
"""队列缩减 9→8:45 个启用日常号蛇形均分 8 队,Path 重绑 MAA_1~8,重建队列 items,队列9 停用。"""
import json
import urllib.request

API = "http://100.79.173.69:36163"
ROGUE_PATHS = {"MAA_10", "MAA_11", "MAA_12"}


def api(path, body, timeout=40):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


d = json.load(open(r"D:/1-2/MAAOrch/tools/_scriptconfig_dump.json", encoding="utf-8"))
accs = []
for sid, v in d.items():
    if sid == "instances":
        continue
    path = (v.get("Info") or {}).get("Path") or ""
    path = path.replace("E:\\MAA-Multi\\", "").replace("E:/MAA-Multi/", "")
    ud = (v.get("SubConfigsInfo") or {}).get("UserData") or {}
    for uid, entry in ud.items():
        if uid == "instances":
            continue
        uinfo = entry.get("Info") or {}
        if uinfo.get("Status") is True and path not in ROGUE_PATHS:
            accs.append((uinfo.get("Name"), sid, uid, path))
accs.sort(key=lambda x: x[0])
print("启用日常号:", len(accs))

# 蛇形分配到 8 队
N = 8
buckets = [[] for _ in range(N)]
for i, a in enumerate(accs):
    idx = i % N
    if (i // N) % 2 == 1:
        idx = N - 1 - idx
    buckets[idx].append(a)
for i, b in enumerate(buckets, 1):
    print("  队列%d (%d): %s" % (i, len(b), ", ".join(a[0] for a in b)))

# 1) Path 重绑 MAA_1~8(仅改了的目标)
for i, b in enumerate(buckets, 1):
    maa = "E:\\MAA-Multi\\MAA_%d" % i
    for name, sid, uid, cur in b:
        if cur != "MAA_%d" % i:
            r = api("/api/scripts/update", {"scriptId": sid, "data": {"Info": {"Path": maa}}})
            print("  Path: %s -> MAA_%d  %s" % (name, i, r.get("code")))

# 2) 队列读 id 列表
q = api("/api/queue/get", {"queueId": None})
qids = list(q["data"].keys())
info_by_qid = {qid: (q["data"][qid].get("Info") or {}).get("Name") for qid in qids}
print("队列:", {v[:8]: k[:8] for k, v in info_by_qid.items()})

# 3) 重建 1~8 队列 items(先清空)
for uqid, qname in info_by_qid.items():
    if not (qname or "").startswith("日常定时"):
        continue
    for it in api("/api/queue/item/get", {"queueId": uqid, "queueItemId": None}).get("index", []):
        api("/api/queue/item/delete", {"queueId": uqid, "queueItemId": it["uid"]})
# 按名称排序对应 1~8
daily = sorted([(qname, uqid) for uqid, qname in info_by_qid.items() if (qname or "").startswith("日常定时")],
               key=lambda x: int(x[0].replace("日常定时", "")))
for i, (qname, uqid) in enumerate(daily[:8], 1):
    for name, sid, uid, cur in buckets[i - 1]:
        iid = api("/api/queue/item/add", {"queueId": uqid}).get("queueItemId")
        api("/api/queue/item/update", {"queueId": uqid, "queueItemId": iid,
                                       "data": {"Info": {"ScriptId": sid}}})
    print("  队列%d 重建 %d items" % (i, len(buckets[i - 1])))

# 4) 队列 9 停用(有第 9 个日常队列时)
if len(daily) > 8:
    qname9, uqid9 = daily[8]
    # 清空 + 停时间
    for it in api("/api/queue/item/get", {"queueId": uqid9, "queueItemId": None}).get("index", []):
        api("/api/queue/item/delete", {"queueId": uqid9, "queueItemId": it["uid"]})
    api("/api/queue/update", {"queueId": uqid9, "data": {"Info": {"TimeEnabled": False, "StartUpEnabled": False}}})
    print("  队列9(%s) 停用+清空" % qname9)

print("完成")
