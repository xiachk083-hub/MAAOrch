# -*- coding: utf-8 -*-
"""两波队列:早班 8 队列(05:00 正序,现有)+ 晚班 8 队列(19:30,items 倒序)。
早班队列移除 19:3x 定时,避免晚上正序重复跑。"""
import json
import urllib.request

API = "http://100.79.173.69:36163"
ALL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def api(path, body, timeout=40):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# 读当前队列
q = api("/api/queue/get", {"queueId": None})
queues = q.get("data") or {}
morning = [qid for qid, qq in queues.items() if (qq.get("Info") or {}).get("Name", "").startswith("日常定时")]
morning = sorted(morning, key=lambda qid: int((queues[qid].get("Info") or {}).get("Name", "9").replace("日常定时", "")))
print("早班队列数:", len(morning))

# 队列名->items
def get_items(qid):
    it = api("/api/queue/item/get", {"queueId": qid, "queueItemId": None})
    out = []
    for ii in it.get("index", []):
        detail = api("/api/queue/item/get", {"queueId": qid, "queueItemId": ii["uid"]})
        data = detail.get("data") or {}
        for u2, cfg in data.items():
            out.append((cfg.get("Info") or {}).get("ScriptId"))
            break
    return out

# 1) 早班队列移除 19:3x 定时
for qid in morning:
    ts = api("/api/queue/time/get", {"queueId": qid, "timeSetId": None})
    for tsu, tcfg in (ts.get("data") or {}).items():
        tm = (tcfg.get("Info") or {}).get("Time", "")
        if tm.startswith("19:"):
            r = api("/api/queue/time/delete", {"queueId": qid, "timeSetId": tsu})
            print("早班移除定时", tm, r.get("code"))

# 2) 建晚班队列(items 倒序)
for i, qid in enumerate(morning, 1):
    items = get_items(qid)
    qid2 = api("/api/queue/add", {}).get("queueId")
    api("/api/queue/update", {"queueId": qid2, "data": {"Info": {"Name": "晚班定时%d" % i}}})
    for sid in reversed(items):  # 倒序
        iid = api("/api/queue/item/add", {"queueId": qid2}).get("queueItemId")
        api("/api/queue/item/update", {"queueId": qid2, "queueItemId": iid, "data": {"Info": {"ScriptId": sid}}})
    # 定时 19:30 + 3n
    hm1 = "19:%02d" % (30 + (i - 1) * 3)
    tsu = api("/api/queue/time/add", {"queueId": qid2}).get("timeSetId")
    api("/api/queue/time/update", {"queueId": qid2, "timeSetId": tsu,
                                   "data": {"Info": {"Enabled": True, "Days": ALL_DAYS, "Time": hm1}}})
    print("晚班定时%d: %s (%d 项, 倒序)" % (i, hm1, len(items)))

print("完成")
