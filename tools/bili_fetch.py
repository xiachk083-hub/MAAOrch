#!/usr/bin/env python3
"""B 站对标参考抓取器 —— 用户常发 B 站视频链接当"对标参考"（见记忆 kelin-ai-axis-video /
agi-stage-clearing-reference），本工具把"视频元数据 / 评论区 / UP 主动态 / 搜索"一次拿全，
省得每次临时拼接口。

为什么要自己拼签名：B 站的 search / space / dynamic 接口现在都要 wbi 签名（w_rid + wts），
且 search 与 space 系接口无 cookie 会 412、空间动态无签名返回 -352。本脚本自动取 nav 里的
img_key/sub_key 算 mixin_key，并用同一 cookie 会话预热（buvid3）。

踩坑记录（两条都踩过，别改回去）：
1. 标准库 urllib 请求这些接口**必被 412**（风控按 `Connection: close` 判非浏览器），
   curl 与 requests 都正常 —— 所以这里用 requests.Session；
2. 带着光秃秃的 `Referer: https://www.bilibili.com/` 请求 api 会被 412，**要么不带 Referer，
   要么带具体页面**（如 `https://www.bilibili.com/video/<bvid>/`）。首页 Referer 实测 412，
   视频页/搜索页 Referer 实测 200。

注意：`https://maa.plus/docs/...` 会 301 到 `docs.maa.plus`；WebFetch 抓不到该站正文，要 curl 后剥 HTML。

用法:
    python tools/bili_fetch.py view BV1jCYn6gEgP
    python tools/bili_fetch.py comments BV1jCYn6gEgP --pages 3
    python tools/bili_fetch.py dynamics 1061593260 --pages 3
    python tools/bili_fetch.py search 谷番 --type bili_user
    python tools/bili_fetch.py search "危机合约 AI" --type video
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import re
import sys
import time
import urllib.parse

import requests

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
API = "https://api.bilibili.com"

# wbi mixin 置换表（B 站固定算法）
MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
         33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
         26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
         20, 34, 44, 52]

_sess = requests.Session()
_sess.headers.update({"User-Agent": UA})  # 刻意不设全局 Referer，见文件头踩坑记录 2
_mixin_key: str | None = None


def _get(url: str, referer: str | None = None) -> dict:
    # 412 是 B 站的突发限流（非鉴权失败），串行连打几个接口就会撞上 —— 退避重试即可
    last = None
    for wait in (0, 2, 6, 15):
        if wait:
            time.sleep(wait)
        try:
            hdrs = {"Referer": referer} if referer else None
            r = _sess.get(url, headers=hdrs, timeout=25)
            if r.status_code == 412:
                last = f"412 {url}"
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = str(e)
    raise SystemExit(f"接口持续被拒（限流或参数问题）: {last}")


def _warm() -> None:
    for u in ("https://www.bilibili.com/", API + "/x/frontend/finger/spi"):
        try:
            _sess.get(u, timeout=20)
        except Exception:
            pass


def _sign(params: dict) -> str:
    """wbi 签名：wts + 参数排序 urlencode + md5(query + mixin_key) → w_rid"""
    global _mixin_key
    if _mixin_key is None:
        _warm()
        nav = _get(API + "/x/web-interface/nav")
        img = nav["data"]["wbi_img"]
        raw = img["img_url"].rsplit("/", 1)[-1].split(".")[0] + \
              img["sub_url"].rsplit("/", 1)[-1].split(".")[0]
        _mixin_key = "".join(raw[i] for i in MIXIN)[:32]
    p = dict(params)
    p["wts"] = int(time.time())
    q = urllib.parse.urlencode(sorted(p.items()), quote_via=urllib.parse.quote)
    p["w_rid"] = hashlib.md5((q + _mixin_key).encode()).hexdigest()
    return urllib.parse.urlencode(sorted(p.items()), quote_via=urllib.parse.quote)


def _ts(v) -> str:
    return dt.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M") if v else ""


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or ""))


def _aid_of(bvid: str) -> tuple[int, dict]:
    d = _get(f"{API}/x/web-interface/view?bvid={bvid}",
             referer=f"https://www.bilibili.com/video/{bvid}/")
    if d.get("code") != 0:
        raise SystemExit(f"取视频信息失败: {d.get('code')} {d.get('message')}")
    return d["data"]["aid"], d["data"]


# ---------- 子命令 ----------

def cmd_view(a) -> None:
    aid, x = _aid_of(a.bvid)
    own = x.get("owner") or {}
    print(f"标题   {x.get('title')}")
    print(f"UP     {own.get('name')}  (mid {own.get('mid')})")
    print(f"发布   {_ts(x.get('pubdate'))}   时长 {x.get('duration')}s")
    st = x.get("stat") or {}
    print(f"数据   播放 {st.get('view')} / 点赞 {st.get('like')} / 收藏 {st.get('favorite')} / 评论 {st.get('reply')}")
    print(f"aid {aid}  cid {x.get('cid')}")
    print("\n--- 简介 ---")
    print(x.get("desc") or "(空)")


def cmd_comments(a) -> None:
    aid, _ = _aid_of(a.bvid)
    offset, total = "", 0
    for _ in range(a.pages):
        url = f"{API}/x/v2/reply/wbi/main?" + _sign({
            "oid": aid, "type": 1, "mode": 3, "plat": 1, "web_location": 1315875,
            "pagination_str": json.dumps({"offset": offset}),
        })
        d = _get(url, f"https://www.bilibili.com/video/{a.bvid}")
        data = d.get("data") or {}
        reps = data.get("replies") or []
        if not reps:
            break
        for r in reps:
            total += 1
            print("=" * 72)
            print(f"{r.get('member', {}).get('uname')} | 赞 {r.get('like')} | 回复 {r.get('rcount')}")
            print(_clean(r.get("content", {}).get("message", "")))
            for s in (r.get("replies") or []):
                print(f"   >> {s.get('member', {}).get('uname')}: {_clean(s.get('content', {}).get('message', ''))}")
        cur = data.get("cursor") or {}
        offset = (cur.get("pagination_reply") or {}).get("next_offset", "")
        if not offset or cur.get("is_end"):
            break
    print(f"\n[共输出 {total} 条主楼]")


def _dyn_text(it: dict) -> str:
    md = (it.get("modules") or {}).get("module_dynamic") or {}
    major = md.get("major") or {}  # 注意：该字段可能是显式 null
    parts = []
    if (md.get("desc") or {}).get("text"):
        parts.append(md["desc"]["text"])
    op = major.get("opus") or {}
    if op:
        parts += [op.get("title") or "", (op.get("summary") or {}).get("text") or ""]
    arch = major.get("archive") or {}
    if arch:
        parts.append(f"[视频] {arch.get('title') or ''}")
    return "\n".join(p for p in parts if p)


def cmd_dynamics(a) -> None:
    offset = ""
    n = 0
    for _ in range(a.pages):
        url = f"{API}/x/polymer/web-dynamic/v1/feed/space?" + _sign({
            "host_mid": a.mid, "offset": offset, "timezone_offset": -480,
            "platform": "web", "features": "itemOpusStyle",
        })
        d = _get(url, f"https://space.bilibili.com/{a.mid}/dynamic")
        data = d.get("data") or {}
        items = data.get("items") or []
        if not items:
            # code 0 但 items 空也是可能的（风控软拒/翻到底），两种情况都报出来便于判读
            if d.get("code") != 0:
                print(f"[接口 code={d.get('code')} {d.get('message')}]")
            elif n == 0:
                print(f"[code 0 但无动态 — offset={offset!r}，可能被风控软拒或该 UP 无公开动态]")
            break
        for it in items:
            n += 1
            mods = it.get("modules") or {}
            au = mods.get("module_author") or {}
            tag = (mods.get("module_tag") or {}).get("text") or ""
            print("=" * 72)
            print(f"{au.get('pub_time')} {tag} | {it.get('type')}")
            print(_dyn_text(it)[:a.width])
        offset = data.get("offset") or ""
        if not data.get("has_more"):
            break
    print(f"\n[共输出 {n} 条动态]")


def cmd_search(a) -> None:
    url = f"{API}/x/web-interface/wbi/search/type?" + _sign({
        "search_type": a.type, "keyword": a.keyword, "page": 1,
    })
    d = _get(url, "https://search.bilibili.com/")
    print(f"code {d.get('code')} {d.get('message')}")
    for r in ((d.get("data") or {}).get("result") or [])[:a.limit]:
        if a.type == "bili_user":
            print(f"UID {r.get('mid')} | {r.get('uname')} | 粉丝 {r.get('fans')}")
            print(f"    {(r.get('usign') or '').strip()[:120]}")
        else:
            print(f"{r.get('bvid')} | {_clean(r.get('title'))} | UP {r.get('author')} | 播放 {r.get('play')} | {_ts(r.get('pubdate'))}")


def main() -> None:
    ap = argparse.ArgumentParser(description="B 站对标参考抓取器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("view", help="视频元数据 + 简介")
    p.add_argument("bvid")
    p.set_defaults(fn=cmd_view)

    p = sub.add_parser("comments", help="评论区（含楼中楼）")
    p.add_argument("bvid")
    p.add_argument("--pages", type=int, default=3)
    p.set_defaults(fn=cmd_comments)

    p = sub.add_parser("dynamics", help="UP 主动态")
    p.add_argument("mid", type=int)
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--width", type=int, default=900, help="每条正文截断长度")
    p.set_defaults(fn=cmd_dynamics)

    p = sub.add_parser("search", help="搜用户/视频")
    p.add_argument("keyword")
    p.add_argument("--type", default="video", choices=["video", "bili_user"])
    p.add_argument("--limit", type=int, default=12)
    p.set_defaults(fn=cmd_search)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
