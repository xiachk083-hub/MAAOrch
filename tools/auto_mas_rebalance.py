# -*- coding: utf-8 -*-
"""AUTO-MAS 队列均分 + 肉鸽独立队列（2026-08-23 用户指令）。

- 44 个日常账号（Status=true 且非 MAA_10/11/12）蛇形均分到 9 个日常队列
- 每脚本 Path 改绑 MAA_<队列号>；重建 9 队列的 QueueItem
- 3 个肉鸽（l-4/l-6/l-7，MAA_10/11/12）各建 1 个独立队列（临时-肉鸽-*）
  + 配定时 05:30 / 20:00（错开日常高峰）+ TimeEnabled=true

用法：
  python tools/auto_mas_rebalance.py            # dry-run
  python tools/auto_mas_rebalance.py --apply    # 实际执行
"""
import argparse
import json
import sys
import urllib.request

API = "http://100.79.173.69:36163"
ROGUE_PATH = {"MAA_10", "MAA_11", "MAA_12"}
ROGUE_NAMES = {  # 队列名 -> (脚本Path, 定时时间)
    "临时-肉鸽-l-4": ("MAA_10", "05:30"),
    "临时-肉鸽-l-6": ("MAA_11", "05:33"),
    "临时-肉鸽-l-7": ("MAA_12", "05:36"),
}
DAILY_QUEUES = 9


def api(path: str, body: dict, timeout: int = 40) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_accounts(path: str):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    accs, rogues = [], []
    for sid, v in d.items():
        if sid == "instances":
            continue
        info = v.get("Info") or {}
        p = (info.get("Path") or "").replace("E:\\MAA-Multi\\", "").replace("E:/MAA-Multi/", "")
        ud = (v.get("SubConfigsInfo") or {}).get("UserData") or {}
        for uid, entry in ud.items():
            if uid == "instances":
                continue
            uinfo = entry.get("Info") or {}
            row = {"sid": sid, "uid": uid, "name": uinfo.get("Name", "?"), "status": uinfo.get("Status"), "path": p}
            if p in ROGUE_PATH:
                rogues.append(row)
            else:
                accs.append(row)
    return [a for a in accs if a["status"] is True], rogues


def snake_assign(items, n):
    """蛇形分配到 n 个桶"""
    buckets = [[] for _ in range(n)]
    for i, item in enumerate(items):
        idx = i % n
        if (i // n) % 2 == 1:
            idx = n - 1 - idx
        buckets[idx].append(item)
    return buckets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--file", default="tools/_scriptconfig_dump.json")
    args = ap.parse_args()

    accs, rogues = load_accounts(args.file)
    buckets = snake_assign(accs, DAILY_QUEUES)
    print(f"日常启用账号 {len(accs)} → {DAILY_QUEUES} 队列（蛇形）")
    for i, b in enumerate(buckets, 1):
        print(f"  队列{i} ({len(b)}): " + ", ".join(a["name"] for a in b))
    print("肉鸽独立队列:")
    for qname, (p, t) in ROGUE_NAMES.items():
        r = next((x for x in rogues if x["path"] == p), None)
        print(f"  {qname} ({p}): {r['name'] if r else '?'}  定时 {t}")

    if not args.apply:
        print("\n[dry-run] 未执行。加 --apply 生效。")
        return

    # 1) 日常脚本 Path 改绑
    for i, b in enumerate(buckets, 1):
        maa = f"E:\\MAA-Multi\\MAA_{i}"
        for a in b:
            if a["path"] != f"MAA_{i}":
                r = api("/api/scripts/update", {"scriptId": a["sid"], "data": {"Info": {"Path": maa}}})
                assert r.get("code") == 200, f"path {a['name']}: {r}"
                print(f"  Path: {a['name']} -> MAA_{i}")

    # 2) 重建 9 个日常队列 items（先清空）
    q = api("/api/queue/get", {"queueId": None})
    qids = list(q["data"].keys())
    for qid in qids:
        items = api("/api/queue/item/get", {"queueId": qid, "queueItemId": None})
        for it in items.get("index", []):
            api("/api/queue/item/delete", {"queueId": qid, "queueItemId": it["uid"]})
    for i, b in enumerate(buckets, 1):
        qid = qids[i - 1]
        for a in b:
            r1 = api("/api/queue/item/add", {"queueId": qid})
            iid = r1.get("queueItemId") or r1.get("data", {}).get("uid")
            api("/api/queue/item/update", {"queueId": qid, "queueItemId": iid, "data": {"Info": {"ScriptId": a["sid"]}}})
        print(f"  队列{i}: 重建 {len(b)} items")

    # 3) 肉鸽独立队列
    existing = api("/api/queue/get", {"queueId": None})
    for qname, (p, t) in ROGUE_NAMES.items():
        r = next((x for x in rogues if x["path"] == p), None)
        if not r:
            continue
        # 复用已存在的同名队列，否则新建
        qid = None
        for uid, qq in existing.get("data", {}).items():
            if qq.get("Info", {}).get("Name") == qname:
                qid = uid
                break
        if not qid:
            qid = api("/api/queue/add", {}).get("queueId")
            api("/api/queue/update", {"queueId": qid, "data": {"Info": {"Name": qname, "TimeEnabled": True}}})
            # 定时 05:30+3n / 20:30+3n
            for hm in (t, f"20:{t[3:]}"):
                tsid = api("/api/queue/time/add", {"queueId": qid}).get("timeSetId")
                days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                api("/api/queue/time/update", {"queueId": qid, "timeSetId": tsid,
                                               "data": {"Info": {"Enabled": True, "Days": days, "Time": hm}}})
        # 清掉旧 items 再加肉鸽脚本
        for it in api("/api/queue/item/get", {"queueId": qid, "queueItemId": None}).get("index", []):
            api("/api/queue/item/delete", {"queueId": qid, "queueItemId": it["uid"]})
        iid = api("/api/queue/item/add", {"queueId": qid}).get("queueItemId")
        api("/api/queue/item/update", {"queueId": qid, "queueItemId": iid, "data": {"Info": {"ScriptId": r["sid"]}}})
        print(f"  {qname}: item={r['name']} 定时={t}/20:{t[3:]}")

    print("完成")


if __name__ == "__main__":
    sys.exit(main())
