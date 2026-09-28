# -*- coding: utf-8 -*-
"""AUTO-MAS 定时开关 —— Hermes 接管发车后用来关/开 AUTO-MAS 自带定时。

背景：2026-09-27 起，24 条班次（日常定时1-12 @05:00..05:33、晚班定时1-12 @19:30..20:03）
由 Hermes 的 cron 任务 mas-shift-dispatch 触发 tools/mas_dispatch.py 发车；AUTO-MAS 自己的定时
必须 Enabled=false，否则同一队列会被发两次。本工具只改 Enabled，**原样保留 Days/Time**，
所以 --enable 就是一条命令的回滚。

用法：
  python tools/mas_timers.py --list
  python tools/mas_timers.py --disable [--only daily|evening]
  python tools/mas_timers.py --enable  [--only daily|evening]
环境变量：MAAORCH_MAS_API（默认 http://100.79.173.69:36163）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
PREFIXES = {"daily": "日常", "evening": "晚班"}


def post(path: str, body: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(body if body is not None else {}).encode("utf-8")
    req = urllib.request.Request(API + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def queues() -> dict:
    r = post("/api/queue/get", {"queueId": None})
    return {((v or {}).get("Info") or {}).get("Name"): uid
            for uid, v in (r.get("data") or {}).items()
            if ((v or {}).get("Info") or {}).get("Name", "").startswith(("日常定时", "晚班定时"))}


def time_sets(queue_id: str) -> dict:
    t = post("/api/queue/time/get", {"queueId": queue_id})
    return {tsid: ((v or {}).get("Info") or {}) for tsid, v in (t.get("data") or {}).items()}


def set_enabled(only: str | None, enabled: bool) -> int:
    changed, failed = [], []
    for name, uid in sorted(queues().items()):
        if only and not name.startswith(PREFIXES[only]):
            continue
        for tsid, info in time_sets(uid).items():
            if bool(info.get("Enabled")) == enabled:
                continue
            body = {"queueId": uid, "timeSetId": tsid,
                    "data": {"Info": {"Enabled": enabled,
                                      "Days": info.get("Days") or [],
                                      "Time": info.get("Time")}}}
            try:
                r = post("/api/queue/time/update", body)
                (changed if r.get("code") == 200 else failed).append("%s@%s" % (name, info.get("Time")))
            except Exception as e:  # noqa: BLE001
                failed.append("%s(%s: %s)" % (name, type(e).__name__, e))
    print("%s: changed=%d %s" % ("disable" if not enabled else "enable", len(changed), ", ".join(changed[:30])))
    if failed:
        print("FAILED: " + ", ".join(failed))
    return 1 if failed else 0


def show() -> int:
    for name, uid in sorted(queues().items()):
        ts = time_sets(uid)
        desc = ", ".join("%s Enabled=%s" % (i.get("Time"), i.get("Enabled")) for i in ts.values())
        print("  %-12s %s" % (name, desc or "(无定时项)"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--disable", action="store_true")
    ap.add_argument("--enable", action="store_true")
    ap.add_argument("--only", choices=["daily", "evening"])
    a = ap.parse_args()
    if a.list:
        return show()
    if a.disable:
        return set_enabled(a.only, False)
    if a.enable:
        return set_enabled(a.only, True)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
