#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""event_calendar 交叉核对（只读）：日历产物 vs 活动快照 / 已知锚点（docs/SERVER_EVENTS.md）。

用途（可重跑）：每轮快照刷新或工具改动后，验证
  runtime/reports/event_calendar.json 是否**如实**包含：
  ① 快照里落在 14 天窗内的每一条活动起/止（逐服，B 服按官服口径）；
  ② `docs/SERVER_EVENTS.md` 里的人工锚点（锚点随快照失效自动 SKIP，不误报）；
  ③ 周期规则的结构性事实（周切时刻 = 各服本地周一 04:00、全开放日、note 截止抽取）。

用法：
    python tools/event_calendar_xcheck.py            # 人类可读（末行 XCHECK=PASS/FAIL n/n）
    python tools/event_calendar_xcheck.py --json     # 机读（单行 JSON）
    python tools/event_calendar_xcheck.py --calendar <path> --events <path>

退出码：0=全过（含 SKIP）；2=有 FAIL；3=输入缺失/不可读（fail-closed）。
只读：不写任何文件、不碰配置、不发车。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BJT = dt.timezone(dt.timedelta(hours=8))

# 各服 UTC 偏移（与 tools/event_calendar.py SERVERS 保持一致）
OFFSETS = {"cn_official": 8, "cn_bilibili": 8, "jp": 9, "kr": 9, "tw": 8, "en": -7}

# 人工锚点（2026-09-28 快照，对照 docs/SERVER_EVENTS.md §0/§1.1/§1.2/§2/§5）。
# (服, 事件名子串, 字段 start|end, 期望 ts_bjt)。快照里已无该事件 ⇒ SKIP（不误报）。
ANCHORS = [
    ("cn_official", "矢量突破#3", "start", "2026-09-29 16:00"),
    ("cn_official", "资源收集", "start", "2026-09-29 16:00"),
    ("cn_official", "逐影集趣", "end", "2026-09-30 03:59"),
    ("cn_official", "锦枫映月", "end", "2026-09-30 03:59"),
    ("cn_official", "稳态测定", "end", "2026-10-08 03:59"),
    ("jp", "人々", "end", "2026-09-30 02:59"),
    ("kr", "사람들", "end", "2026-09-30 02:59"),
    ("en", "PEOPLE, A PEOPLE", "end", "2026-09-30 18:59"),
]

# note 截止锚点：(服, 标题子串（=事件名·商店/奖励截止，故用事件名）, 期望 ts_bjt)
DEADLINE_ANCHORS = [
    ("cn_official", "矢量突破#3", "2026-10-23 03:59"),
    ("cn_official", "逐影集趣", "2026-10-03 03:59"),
]


def parse_ts(s):
    s = str(s or "").strip().replace("T", " ")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ ](\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if not m:
        return None
    try:
        return dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                           int(m.group(4) or 0), int(m.group(5) or 0))
    except ValueError:
        return None


def fmt(ts):
    return ts.strftime("%Y-%m-%d %H:%M")


def load(path, what):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def snapshot_events(ev_doc, key):
    sv = (ev_doc.get("servers") or {}).get(key) or {}
    out = []
    for grp in ("active_events", "upcoming_events", "ended_events"):
        for e in sv.get(grp) or []:
            if isinstance(e, dict):
                out.append(e)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="event_calendar 交叉核对（只读）")
    ap.add_argument("--calendar", default=os.path.join(ROOT, "runtime", "reports", "event_calendar.json"))
    ap.add_argument("--events", default=os.path.join(ROOT, "runtime", "reports", "server_events.json"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cases = []

    def check(name: str, ok: bool, detail: object = "", skip: bool = False):
        cases.append({"name": name, "ok": bool(ok), "skip": bool(skip), "detail": str(detail)[:400]})

    try:
        cal = load(args.calendar, "calendar")
        ev_doc = load(args.events, "events")
    except (OSError, ValueError) as e:
        print("XCHECK=UNKNOWN:输入不可读：%s" % e, file=sys.stderr)
        return 3

    per = cal.get("per_server") or {}
    if not per:
        print("XCHECK=UNKNOWN:日历无 per_server（先跑 tools/event_calendar.py）", file=sys.stderr)
        return 3

    # 通用：快照事件（落在窗内的起/止）必须在日历里逐条出现
    for key, block in per.items():
        days = block.get("days") or []
        if not days:
            continue
        d0 = dt.date.fromisoformat(days[0]["game_date"])
        d1 = dt.date.fromisoformat(days[-1]["game_date"]) + dt.timedelta(days=1)
        evs = snapshot_events(ev_doc, key)
        if not evs and key == "cn_bilibili":
            evs = snapshot_events(ev_doc, "cn_official")       # B 服无独立公告面 -> 官服口径
        entries = [e for d in days for e in d.get("entries") or []]
        miss, hit = [], 0
        for e in evs:
            for field, kind in (("start_bjt", "event_start"), ("end_bjt", "event_end")):
                ts = parse_ts(e.get(field) or "")
                if ts is None or not (d0 <= ts.date() < d1):
                    continue
                good = any(x["kind"] == kind and x.get("event_name") == e.get("name")
                           and x["ts_bjt"] == fmt(ts) for x in entries)
                if good:
                    hit += 1
                else:
                    miss.append("%s%s@%s" % (e.get("name"), "开启" if kind == "event_start" else "结束", fmt(ts)))
        check("窗内快照事件逐条落表（%s）" % key, not miss, "缺失: %s（命中 %d）" % ("；".join(miss), hit) if miss else "命中 %d" % hit,
              skip=not evs)

    # 人工锚点（对照 SERVER_EVENTS.md；快照失效则 SKIP）
    for key, sub, field, want in ANCHORS:
        evs = snapshot_events(ev_doc, key)
        alive = any(sub in (e.get("name") or "") for e in evs)
        if not alive:
            check("锚点 %s/%s %s = %s" % (key, sub, field, want), True, "快照已无该事件 → SKIP", skip=True)
            continue
        kind = "event_start" if field == "start" else "event_end"
        entries = [x for d in (per.get(key, {}).get("days") or []) for x in d.get("entries") or []]
        good = any(x["kind"] == kind and sub in (x.get("event_name") or "") and x["ts_bjt"] == want for x in entries)
        check("锚点 %s/%s %s = %s" % (key, sub, field, want), good,
              [x["ts_bjt"] for x in entries if sub in (x.get("event_name") or "")])

    # note 截止锚点
    dl = cal.get("deadlines") or []
    for key, sub, want in DEADLINE_ANCHORS:
        check("截止锚点 %s/%s = %s" % (key, sub, want),
              any(d.get("server") == key and sub in (d.get("title") or "") and d.get("ts_bjt") == want for d in dl),
              [(d.get("ts_bjt"), d.get("title")) for d in dl if sub in (d.get("title") or "")])

    # 结构性：周切 = 各服本地周一 04:00（BJT = 12 − offset）
    for key, block in per.items():
        off = OFFSETS.get(key)
        if off is None:
            continue
        want_hh = (12 - off) % 24
        bad = []
        for d in block.get("days") or []:
            gd = dt.date.fromisoformat(d["game_date"])
            if gd.weekday() != 0:
                continue
            wr = [e for e in d.get("entries") or [] if e["kind"] == "weekly_reset"]
            if len(wr) != 1 or wr[0]["ts_bjt"] != "%s %02d:00" % (d["game_date"], want_hh):
                bad.append(d["game_date"])
        check("周切结构（%s：本地周一 04:00 = BJT %02d:00）" % (key, want_hh), not bad, "异常日: %s" % bad)

    # 结构性：全开放日（CN 09-29 16:00 起）与基表
    cn = per.get("cn_official", {}).get("days") or []
    by_date = {d["game_date"]: d for d in cn}
    d29 = by_date.get("2026-09-29")
    if d29 and any("资源收集" in (e.get("event_name") or "") for d in cn for e in d.get("entries") or []):
        check("CN 09-29 全开放从 16:00 起（此前按周表）",
              d29.get("resource_open_all") is True and (d29.get("resource_open_all_from_bjt") or "").endswith("16:00")
              and "CE-6" in (d29.get("resource_open_base") or []) and "AP-5" not in (d29.get("resource_open_base") or []),
              (d29.get("resource_open_all_from_bjt"), d29.get("resource_open_base")))
        later = [d for d in cn if d["game_date"] > "2026-09-29"]
        check("CN 09-30 起天天全开放",
              bool(later) and all(d.get("resource_open_all") and not d.get("resource_open_all_from_bjt") for d in later),
              [d["game_date"] for d in later if not d.get("resource_open_all")])
    else:
        check("CN 全开放结构（快照无该事件 → SKIP）", True, "SKIP", skip=True)

    passed = sum(1 for c in cases if c["ok"] and not c["skip"])
    failed = [c for c in cases if not c["ok"]]
    skipped = sum(1 for c in cases if c["skip"])
    rep = {"ok": not failed, "passed": passed, "failed": len(failed), "skipped": skipped,
           "calendar": args.calendar, "events": args.events, "cases": cases}

    if args.json:
        print(json.dumps(rep, ensure_ascii=False))
    else:
        for c in cases:
            tag = "SKIP" if c["skip"] else ("PASS" if c["ok"] else "FAIL")
            print("%s  %s%s" % (tag, c["name"], "" if c["ok"] else "  ← " + c["detail"]))
        print("XCHECK=%s %d/%d（SKIP %d）" % ("PASS" if not failed else "FAIL", passed,
                                              len(cases) - skipped, skipped))
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
