"""游戏公告收集（2026-08-11 用户: 新收集任务，与日志分开）。

独立于日志体系（logs/announcements/）：
- 来源: 官网公告 API（/api/news?page=N&pageSize=M — 全类型全抓，
  tab 分类保留；HTML 提取作 fallback）
- 功能: 定时抓取 → 全量公告（cid 唯一标识）→ 存储 → 新公告检测
- API: GET /api/announcements（列表 + 新公告）
"""
from __future__ import annotations
import json
import re
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ANNOUNCE_DIR = Path(__file__).parent.parent / "logs" / "announcements"
API_URL = "https://ak.hypergryph.com/api/news?page={page}&pageSize=100"
HTML_URL = "https://ak.hypergryph.com/"  # fallback（HTML 标题级）
FETCH_INTERVAL = 6 * 3600  # 每 6 小时

# HTML fallback 的标题关键词
_TITLE_PAT = re.compile(
    r"([^\"<>]{4,60}?(?:维护公告|恢复说明|活动预告|活动说明|即将开启|版本更新|停服|补偿|征集)[^\"<>]{0,30})"
)


def _fetch(url: str, timeout: float = 15) -> str:
    """抓取（官网固定 URL — 白名单来源，非不可信输入）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "MAAOrch-Announce/1.0"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def fetch_from_api() -> list[dict]:
    """官网公告 API 全量抓取（pageSize=100，全类型不分 tab）。"""
    items = []
    for page in (1, 2):  # 最多翻 2 页（100/页 — 足够）
        try:
            d = json.loads(_fetch(API_URL.format(page=page)))
            lst = (d.get("data") or {}).get("list") or []
            if not lst:
                break
            for x in lst:
                ts = x.get("displayTime") or 0
                try:
                    date = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d") if ts else ""
                except Exception:
                    date = ""
                items.append({
                    "cid": str(x.get("cid", "")),
                    "title": (x.get("title") or "")[:100],
                    "tab": str(x.get("tab", "0")),
                    "date": date,
                    "url": f"https://ak.hypergryph.com/news/{x.get('cid')}.html",
                    "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
            if len(lst) < 100:
                break
        except Exception:
            break
    return items


def extract_announcements(html: str) -> list[dict]:
    """HTML fallback 提取（API 不可用时用 — 标题级）。"""
    out = []
    seen = set()
    for m in _TITLE_PAT.finditer(html):
        t = m.group(1).strip().rstrip("\\")
        if len(t) >= 6 and t not in seen:
            seen.add(t)
            out.append({"cid": "", "title": t[:80], "tab": "?", "date": "",
                        "url": "", "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return out


def collect_once() -> dict:
    """抓取 → 新公告检测（cid 对比）→ 存储。返回本次结果。"""
    ANNOUNCE_DIR.mkdir(parents=True, exist_ok=True)
    fp = ANNOUNCE_DIR / "announcements.json"
    old = []
    if fp.exists():
        try:
            old = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            old = []
    old_cids = {a.get("cid") for a in old if a.get("cid")}

    items = fetch_from_api()
    if not items:  # API 失败 → HTML fallback
        try:
            items = extract_announcements(_fetch(HTML_URL))
        except Exception:
            items = []

    # API 是权威来源：成功时移除旧 HTML 残留（无 cid 条目）
    if items and items[0].get("cid"):
        old = [a for a in old if a.get("cid")]
    merged = list(old)
    known = old_cids
    new_items = []
    for it in items:
        key = it.get("cid") or it.get("title")
        if key and key not in known:
            merged.insert(0, it)
            known.add(key)
            new_items.append(it)
    merged = merged[:200]  # 保留最近 200 条

    try:
        fp.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return {"total": len(merged), "new": len(new_items),
            "new_titles": [n.get("title", "") for n in new_items][:10]}


def get_announcements() -> dict:
    """当前公告列表 + 统计（API 用）。"""
    fp = ANNOUNCE_DIR / "announcements.json"
    if not fp.exists():
        return {"ok": True, "announcements": [], "total": 0}
    try:
        items = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        items = []
    return {"ok": True, "announcements": items[:100], "total": len(items)}


class AnnounceCollector(threading.Thread):
    """定时公告收集线程（main_web 启动，每 6 小时）。"""

    def __init__(self):
        super().__init__(daemon=True, name="announce_collector")

    def run(self) -> None:
        while True:
            try:
                r = collect_once()
                if r.get("new"):
                    print(f"[公告] 新公告 {r['new']} 条: {r['new_titles']}")
            except Exception:
                pass
            time.sleep(FETCH_INTERVAL)
