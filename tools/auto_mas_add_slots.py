# -*- coding: utf-8 -*-
"""给 AUTO-MAS 扩/减跑位：确保存在 N 组「日常定时N / 晚班定时N」队列并配好错峰定时。

跑位 = 一组队列（早班 + 晚班）。槽位 N 独占 MAA 运行目录 `E:\\MAA-Multi\\MAA_N`。
时间错峰规则（沿用现有）：早班 05:00 起每槽 +3 分钟，晚班 19:30 起每槽 +3 分钟。

用法：
    python tools/auto_mas_add_slots.py --to 12            # dry-run：只说会建哪几条
    python tools/auto_mas_add_slots.py --to 12 --apply    # 真的建
    （建完记得跑 tools/auto_mas_flatten.py --apply 把账号重新配平到新槽位）
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

API = "http://100.79.173.69:36163"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MORNING = (5, 0)
EVENING = (19, 30)
GAP = 3


def api(path: str, body: dict | None = None, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body if body is not None else {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def hhmm(base: tuple, offset_min: int) -> str:
    t = base[0] * 60 + base[1] + offset_min
    return "%02d:%02d" % (t // 60 % 24, t % 60)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", type=int, required=True, help="目标跑位数（如 12）")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    qs = api("/api/queue/get", {"queueId": None}).get("data") or {}
    names = {(qv.get("Info") or {}).get("Name", ""): qid for qid, qv in qs.items()}

    todo = []
    for n in range(1, args.to + 1):
        for prefix, base in (("日常定时", MORNING), ("晚班定时", EVENING)):
            nm = "%s%d" % (prefix, n)
            if nm not in names:
                todo.append((nm, hhmm(base, (n - 1) * GAP)))

    cur = sum(1 for nm in names if nm.startswith("日常定时"))
    print("当前跑位 %d 组，目标 %d 组" % (cur, args.to))
    if not todo:
        print("无需新建。")
        return
    print("待新建 %d 条队列：" % len(todo))
    for nm, t in todo:
        print("   %-12s 定时 %s" % (nm, t))
    if not args.apply:
        print("\n[dry-run] 加 --apply 生效。")
        return

    for nm, t in todo:
        qid = api("/api/queue/add", {}).get("queueId")
        r = api("/api/queue/update", {"queueId": qid,
                                      "data": {"Info": {"Name": nm, "TimeEnabled": True,
                                                        "StartUpEnabled": False,
                                                        "AfterAccomplish": "NoAction"}}})
        assert r.get("code") == 200, r
        r1 = api("/api/queue/time/add", {"queueId": qid})
        tsid = r1.get("timeSetId") or (r1.get("data") or {}).get("uid") or \
            ((r1.get("index") or [{}])[0].get("uid"))
        assert tsid, "time/add 未返回 id: %s" % r1
        r2 = api("/api/queue/time/update", {"queueId": qid, "timeSetId": str(tsid),
                                            "data": {"Info": {"Enabled": True, "Days": DAYS, "Time": t}}})
        assert r2.get("code") == 200, r2
        print("  ✓ %s (%s) 定时 %s" % (nm, qid[:8], t))

    print("\n完成。接着跑：python tools/auto_mas_flatten.py --apply")


if __name__ == "__main__":
    sys.exit(main())
