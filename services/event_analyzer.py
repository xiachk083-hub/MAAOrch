"""活动历史收集与分析（2026-08-11 用户）— 独立于公告收集。

数据源: PRTS Wiki（活动一览 → 详情页 wikitext 的活动时间模板）
回答用户 4 问:
1. 活动持续时间多长？
2. 一个月内活动天数？
3. 活动间重叠多少？
4. 一个月开几个活动？

存储: logs/announcements/events.json（活动名/开始/结束/兑换结束）
"""
from __future__ import annotations
import json
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

DIR = Path(__file__).parent.parent / "logs" / "announcements"
UA = {"User-Agent": "MAAOrch-Analyzer/1.0"}


def _get(url: str, timeout: float = 20) -> str:
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def fetch_event_list() -> list[dict]:
    """活动一览 → 活动名 + 开始时间（近 N 年）。"""
    url = "https://prts.wiki/api.php?action=parse&page=%E6%B4%BB%E5%8A%A8%E4%B8%80%E8%A7%88&prop=text&format=json"
    d = json.loads(_get(url))
    html = d["parse"]["text"]["*"]
    out = []
    # 行: 日期 + 活动链接
    for m in re.finditer(r'<tr>.*?</tr>', html, re.S):
        row = m.group(0)
        dm = re.search(r'(\d{4}-\d\d-\d\d \d\d:\d\d)', row)
        if not dm:
            continue
        lm = re.search(r'title="([^"]+)"[^>]*>([^<]{2,40})<', row)
        name = lm.group(2).strip() if lm else "?"
        out.append({"name": name, "start": dm.group(1)})
    return out


def fetch_event_times(name: str) -> dict:
    """活动详情页 wikitext → 开始/结束/兑换结束时间。"""
    page = urllib.parse.quote(name)
    txt = _get(f"https://prts.wiki/index.php?title={page}&action=raw")
    def _pick(pat):
        m = re.search(pat, txt)
        return m.group(1).strip() if m else ""
    return {
        "name": name,
        "start": _pick(r"开始时间\s*=\s*([^\n|]+)") or _pick(r"活动开始时间\s*=\s*([^\n|]+)"),
        "end": _pick(r"活动结束时间\s*=\s*([^\n|]+)"),
        "exchange_end": _pick(r"兑换结束时间\s*=\s*([^\n|]+)"),
    }


def collect_events(year_start: int = 2026) -> list[dict]:
    """全量收集：活动一览 → 详情时间。存 events.json。"""
    DIR.mkdir(parents=True, exist_ok=True)
    fp = DIR / "events.json"
    old = []
    if fp.exists():
        try:
            old = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            old = []
    old_names = {e.get("name") for e in old}

    items = []
    lst = fetch_event_list()
    # 过滤年份 + 已收集
    todo = [x for x in lst if str(x["start"]).startswith(str(year_start)) and x["name"] not in old_names]
    for x in todo:
        try:
            t = fetch_event_times(x["name"])
            items.append(t)
            print(f"  ✓ {t['name']}: {t['start']} ~ {t['end']}")
        except Exception as e:
            print(f"  ✗ {x['name']}: {e}")
        time.sleep(0.3)
    merged = old + items
    fp.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    return merged


def _parse(ts: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts.strip(), fmt)
        except Exception:
            continue
    return None


def analyze(year: int = 2026) -> dict:
    """分析 events.json → 时长/月天数/重叠/月数量。"""
    fp = DIR / "events.json"
    events = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
    evs = []
    for e in events:
        s, en = _parse(e.get("start", "")), _parse(e.get("end", ""))
        if s and en and s.year == year:
            evs.append({"name": e["name"], "start": s, "end": en})
    evs.sort(key=lambda x: x["start"])
    # 常驻/长期活动（>60 天 = 常驻）单独标注，不算入常规时长统计
    normal = [e for e in evs if (e["end"] - e["start"]).days <= 60]
    permanent = [e for e in evs if (e["end"] - e["start"]).days > 60]
    # 1) 持续时间（常规活动）
    durations = [(en - s).days + 1 for s, en in [(e["start"], e["end"]) for e in normal]]
    # 2) 月度统计
    months = {}
    for e in evs:
        m = e["start"].strftime("%Y-%m")
        months.setdefault(m, {"count": 0, "days": set()})
        months[m]["count"] += 1
        d = e["start"]
        while d <= e["end"]:
            months[m]["days"].add(d.day)
            d += timedelta(days=1)
    # 3) 重叠（活动时间交集天数）
    overlaps = []
    for i in range(len(evs)):
        for j in range(i + 1, len(evs)):
            a, b = evs[i], evs[j]
            ov = min(a["end"], b["end"]) - max(a["start"], b["start"])
            if ov.days >= 0:
                overlaps.append((a["name"], b["name"], ov.days + 1))
    # 4) 月数量
    monthly = {m: v["count"] for m, v in sorted(months.items())}
    return {
        "year": year,
        "total_events": len(evs),
        "normal_events": len(normal),
        "permanent_events": [e["name"] for e in permanent],
        "avg_duration_days": round(sum(durations) / len(durations), 1) if durations else 0,
        "min_duration": min(durations) if durations else 0,
        "max_duration": max(durations) if durations else 0,
        "monthly": monthly,
        "monthly_days": {m: len(v["days"]) for m, v in sorted(months.items())},
        "overlap_pairs": len(overlaps),
        "overlaps": [{"a": a, "b": b, "days": d} for a, b, d in overlaps[:20]],
        "events": [{"name": e["name"], "start": e["start"].strftime("%m-%d"),
                    "end": e["end"].strftime("%m-%d")} for e in evs],
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--collect" in sys.argv:
        n = collect_events()
        print(f"已收集 {len(n)} 个活动")
    print(json.dumps(analyze(), ensure_ascii=False, indent=1))
