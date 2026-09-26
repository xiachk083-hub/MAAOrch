#!/usr/bin/env python3
"""B 站视频 → 本地文件（攻略视频进"视频→配方"流水线的入口）。

为什么单独一个文件: `bili_fetch.py` 只取元数据/评论/搜索（它自己写了 wbi 签名、
buvid3 预热、Referer 踩坑），**没有取流的命令** —— 而"看攻略视频学打法"需要
真把视频拿到本地。这里复用它已经调通的会话与签名，只补 pagelist → playurl → 下载。

未登录只能拿到 480P/360P（够解析用: UI 布局线性缩放，解析前统一缩回 1280x720）。

用法:
    python tools/bili_pull.py BV1C7ti6yE6P                 # 下来看看多长/什么清晰度
    python tools/bili_pull.py BV1C7ti6yE6P --out D:\\tmp\\sr4.mp4
    python tools/bili_pull.py BV15qtB6zEYJ --max-seconds 60   # 只下前 60 秒
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ / "tools"))

import bili_fetch as B                                            # noqa: E402


def pagelist(bvid: str) -> dict:
    d = B._get(f"{B.API}/x/player/pagelist?bvid={bvid}",
               f"https://www.bilibili.com/video/{bvid}/")
    pages = d.get("data") or []
    if not pages:
        raise SystemExit(f"取不到分P: {d}")
    return pages[0]


def playurl(bvid: str, cid: int, qn: int = 64) -> list[tuple[int, str]]:
    """返回 [(分辨率高, 流地址)]，按清晰度降序（未登录通常最高 480P）。"""
    # playurl 比 search 更容易被 412：先预热 buvid3，失败再退到老接口（不要 wbi）
    try:
        B._warm()
    except Exception as e:
        print(f"预热失败（继续）: {e}", flush=True)
    d = {}
    for tag, make in (
        ("wbi", lambda: f"{B.API}/x/player/wbi/playurl?" + B._sign({
            "bvid": bvid, "cid": cid, "qn": qn, "fnval": 16, "fnver": 0, "fourk": 1})),
        ("legacy", lambda: f"{B.API}/x/player/playurl?bvid={bvid}&cid={cid}"
                           f"&qn={qn}&fnval=0&fnver=0&fourk=0"),
    ):
        t0 = time.time()
        try:
            d = B._get(make(), f"https://www.bilibili.com/video/{bvid}/")
            print(f"  {tag} 接口 OK（{time.time() - t0:.0f}s）", flush=True)
            break
        except SystemExit:
            print(f"  {tag} 接口被拒（{time.time() - t0:.0f}s）", flush=True)
            continue
    data = d.get("data") or {}
    out = []
    for v in (data.get("dash") or {}).get("video") or []:
        out.append((int(v.get("height") or 0), v.get("baseUrl") or v.get("base_url") or ""))
    if not out:
        durl = (data.get("durl") or [])
        out = [(int(data.get("quality") or 0), x.get("url", "")) for x in durl]
    return sorted(out, key=lambda t: -t[0])


def download(url: str, out: pathlib.Path, bvid: str, max_bytes: int | None = None) -> int:
    hdrs = {"Referer": f"https://www.bilibili.com/video/{bvid}/",
            "User-Agent": B.UA, "Range": "bytes=0-"}
    got = 0
    t0 = time.time()
    with B._sess.get(url, headers=hdrs, stream=True, timeout=40) as r:
        r.raise_for_status()
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                if max_bytes and got >= max_bytes:
                    break
    print(f"下载 {out} ｜ {got / 1e6:.1f} MB ｜ {time.time() - t0:.1f}s", flush=True)
    return got


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bvid")
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--qn", type=int, default=64, help="期望清晰度（未登录会被降级）")
    ap.add_argument("--max-seconds", type=float, default=0,
                    help=">0 时只下前 N 秒（按码率估算，够看开局）")
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    page = pagelist(args.bvid)
    dur = int(page.get("duration") or 0)
    print(f"{args.bvid} P{page.get('page')} 「{page.get('part')}」 {dur}s"
          f"（{dur // 60}:{dur % 60:02d}）", flush=True)
    streams = playurl(args.bvid, int(page["cid"]), args.qn)
    if not streams:
        raise SystemExit("取不到流地址（可能被风控，稍后再试）")
    for h, u in streams[:4]:
        print(f"  清晰度 {h}P  {u[:80]}…", flush=True)
    if args.list_only:
        return
    out = args.out or (PROJ / "data" / "bili" / f"{args.bvid}.mp4")
    max_bytes = None
    if args.max_seconds > 0:
        max_bytes = int(args.max_seconds * 250_000)      # ~2Mbps 粗估
    download(streams[0][1], out, args.bvid, max_bytes)


if __name__ == "__main__":
    main()
