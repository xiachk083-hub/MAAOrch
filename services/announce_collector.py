"""游戏公告收集（2026-08-11 用户: 新收集任务，与日志分开）。

独立于日志体系（logs/announcements/）：
- 来源: 官网首页公告区（当前 HTML 标题级提取；SOURCES 可扩展 —
  后续找到客户端公告 API 后追加）
- 功能: 定时抓取 → 提取公告（标题/链接/时间）→ 存储 → 新公告检测
- API: GET /api/announcements（列表 + 新公告）

存储结构（logs/announcements/announcements.json）:
[{title, url, fetched_at}]
"""
from __future__ import annotations
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

ANNOUNCE_DIR = Path(__file__).parent.parent / "logs" / "announcements"
SOURCES = [
    "https://ak.hypergryph.com/",  # 官网首页（公告区预渲染）
]
FETCH_INTERVAL = 6 * 3600  # 每 6 小时

# 公告标题关键词（提取时过滤噪音）
_TITLE_PAT = re.compile(
    r"([^\"<>]{4,60}?(?:维护公告|恢复说明|活动预告|活动说明|即将开启|版本更新|停服|补偿)[^\"<>]{0,30})"
)


def _fetch(url: str, timeout: float = 15) -> str:
    """抓取页面（官网白名单来源 — 固定 URL，非不可信输入）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "MAAOrch-Announce/1.0"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def extract_announcements(html: str) -> list[dict]:
    """从官网 HTML 提取公告（标题级 — 轮播区预渲染，含维护/活动公告）。"""
    out = []
    seen = set()
    for m in _TITLE_PAT.finditer(html):
        t = m.group(1).strip()
        t = t.rstrip("\\")
        if len(t) >= 6 and t not in seen:
            seen.add(t)
            out.append({"title": t[:80], "url": "", "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return out


def collect_once() -> dict:
    """抓取全部来源 → 合并 → 新公告检测 → 存储。返回本次结果。"""
    ANNOUNCE_DIR.mkdir(parents=True, exist_ok=True)
    fp = ANNOUNCE_DIR / "announcements.json"
    old = []
    if fp.exists():
        try:
            old = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            old = []
    old_titles = {a.get("title") for a in old}

    all_items = []
    for url in SOURCES:
        try:
            html = _fetch(url)
            items = extract_announcements(html)
            for it in items:
                it["source"] = url
            all_items.extend(items)
        except Exception as e:
            all_items.append({"title": f"[抓取失败] {url}: {e}", "url": url,
                              "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")})

    # 去重合并（旧 + 新）
    merged = list(old)
    known = old_titles
    new_items = []
    for it in all_items:
        if it.get("title") and it["title"] not in known:
            merged.insert(0, it)
            known.add(it["title"])
            new_items.append(it)
    merged = merged[:100]  # 保留最近 100 条

    try:
        fp.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return {"total": len(merged), "new": len(new_items),
            "new_titles": [n["title"] for n in new_items][:10]}


def get_announcements() -> dict:
    """当前公告列表 + 统计（API 用）。"""
    fp = ANNOUNCE_DIR / "announcements.json"
    if not fp.exists():
        return {"ok": True, "announcements": [], "total": 0}
    try:
        items = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        items = []
    return {"ok": True, "announcements": items[:50], "total": len(items)}


class AnnounceCollector(threading.Thread):
    """定时公告收集线程（main_web 启动，每 6 小时）。"""

    def __init__(self):
        super().__init__(daemon=True, name="announce_collector")

    def run(self) -> None:
        while True:
            try:
                r = collect_once()
                if r.get("new"):
                    import sys as _sys
                    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                    print(f"[公告] 新公告 {r['new']} 条: {r['new_titles']}")
            except Exception:
                pass
            time.sleep(FETCH_INTERVAL)
