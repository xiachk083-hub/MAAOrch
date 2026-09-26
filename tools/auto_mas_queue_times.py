# -*- coding: utf-8 -*-
"""AUTO-MAS 队列定时项配置 — 每队列一天两轮（早班+晚班，错峰启动）。

默认: 早班 05:00 起每队 +3 分钟, 晚班 19:30 起每队 +3 分钟, 全 7 天。
用法:
    python tools/auto_mas_queue_times.py --dry-run   # 只打印计划不改
    python tools/auto_mas_queue_times.py             # 实配
    python tools/auto_mas_queue_times.py --morning 06:00 --evening 20:00 --gap 5   # 自定义
"""
import argparse
import json
import sys
import urllib.request

HOST = "http://100.79.173.69:36163"
ALL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# 队列1~9 的 uid（来自 /api/queue/get，2026-08-20 实查）
QUEUES = [
    ("队列1", "613f6094-a3bc-4f08-bc57-4a3e31bf7597"),
    ("队列2", "62ad979c-e754-4662-bd41-bb6e5ef7d845"),
    ("队列3", "906ad3dc-4f62-4cab-99c5-6fb7b7c22acf"),
    ("队列4", "3ae71a5e-fad8-496c-877b-920f5d48aace"),
    ("队列5", "15dda9a2-f0d0-4ff7-8d9c-1a11829c1732"),
    ("队列6", "425a4917-9bbf-4af2-8e52-a14aa54c6133"),
    ("队列7", "18ae7c1e-6245-490e-abc9-c35f6c890502"),
    ("队列8", "bb6db695-dd9d-43d1-9a22-cca5b8dbdf2d"),
    ("队列9", "25f3fa7d-79e3-4514-b49d-08c96b3080b3"),
]


def api(path, body):
    req = urllib.request.Request(
        HOST + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def add_minutes(hhmm, minutes):
    h, m = map(int, hhmm.split(":"))
    total = h * 60 + m + minutes
    return "%02d:%02d" % (total // 60 % 24, total % 60)


def parse_time(v):
    h, m = map(int, v.split(":"))
    return h * 60 + m


def build_schedule(morning, evening, gap):
    plan = []
    for i, (name, qid) in enumerate(QUEUES):
        plan.append((name, qid, add_minutes(morning, i * gap), add_minutes(evening, i * gap)))
    return plan


def get_tsid(r):
    v = r.get("timeSetId") or (r.get("data") or {}).get("uid")
    if v is None and r.get("index"):
        v = r["index"][0].get("uid")
    if isinstance(v, dict):
        v = v.get("uid")
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不改任何配置")
    ap.add_argument("--morning", default="05:00", help="早班首队时间 HH:MM")
    ap.add_argument("--evening", default="19:30", help="晚班首队时间 HH:MM")
    ap.add_argument("--gap", type=int, default=3, help="队列间错峰分钟数")
    args = ap.parse_args()

    plan = build_schedule(args.morning, args.evening, args.gap)
    print("计划: 早班 %s 起 / 晚班 %s 起 / 每队错 %d 分钟 / 全 7 天" % (args.morning, args.evening, args.gap))
    for name, qid, am, pm in plan:
        print("  %s: 早 %s  晚 %s" % (name, am, pm))

    if args.dry_run:
        print("\n[dry-run] 未做任何修改。")
        return

    ok, fail = 0, 0
    for name, qid, am, pm in plan:
        try:
            # 1. 清掉该队列现有定时项（幂等重置）
            old = api("/api/queue/time/get", {"queueId": qid})
            for uid in (old.get("index") or []):
                api("/api/queue/time/delete", {"queueId": qid, "timeSetId": uid["uid"]})

            # 2. 早班
            r1 = api("/api/queue/time/add", {"queueId": qid})
            ts1 = get_tsid(r1)
            if not ts1:
                raise RuntimeError("time/add 未返回 timeSetId: %s" % json.dumps(r1, ensure_ascii=False)[:200])
            api("/api/queue/time/update", {
                "queueId": qid, "timeSetId": str(ts1),
                "data": {"Info": {"Enabled": True, "Days": ALL_DAYS, "Time": am}},
            })

            # 3. 晚班
            r2 = api("/api/queue/time/add", {"queueId": qid})
            ts2 = get_tsid(r2)
            if not ts2:
                raise RuntimeError("time/add 未返回 timeSetId: %s" % json.dumps(r2, ensure_ascii=False)[:200])
            api("/api/queue/time/update", {
                "queueId": qid, "timeSetId": str(ts2),
                "data": {"Info": {"Enabled": True, "Days": ALL_DAYS, "Time": pm}},
            })

            # 4. 打开队列 TimeEnabled
            api("/api/queue/update", {"queueId": qid, "data": {
                "Info": {"Name": name, "TimeEnabled": True, "StartUpEnabled": False,
                         "AfterAccomplish": "NoAction"}}})

            print("  OK %s: 早 %s 晚 %s" % (name, am, pm))
            ok += 1
        except Exception as e:
            print("  FAIL %s: %s" % (name, e))
            fail += 1

    print("\n完成: %d 成功, %d 失败" % (ok, fail))

    # 验证
    print("\n=== 验证 ===")
    for name, qid, am, pm in plan:
        try:
            t = api("/api/queue/time/get", {"queueId": qid})
            items = t.get("data", {})
            times = sorted(v["Info"]["Time"] for v in items.values() if v.get("Info"))
            print("  %s: TimeEnabled? 定时项=%d %s" % (name, len(times), times))
        except Exception as e:
            print("  %s: 验证失败 %s" % (name, e))


if __name__ == "__main__":
    main()
