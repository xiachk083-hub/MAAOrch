"""逐格追踪敌人实际轨迹：用「格子↔像素」映射，把每一格里有没有单位/多暗打出来。

不用模板：敌方单位在冰面地图上是明显的暗色块，而且**比地图纹理会随时间移动**——
所以我同时算「暗度」和「与前几帧的变化量」，两者都高的格子就是有单位在走。

用法:
    python tools/bi_track_tiles.py runtime/_ce6/bi5j.mp4 --from 130 --to 165 --step 2 \
        --tiles "1,6 2,6 3,6 4,6 5,6 6,6 7,6 8,6 9,6 1,5 1,4 1,3 1,2 1,1"
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
# bi5h/bi5j 用 MAA 部署手势标定出来的映射（三点验证）：x=84c+1r+295, y=-8.5c+86.5r+72.5
A = (84.0, 1.0, 295.0)
B = (-8.5, 86.5, 72.5)


def tile_px(col, row):
    return A[0] * col + A[1] * row + A[2], B[0] * col + B[1] * row + B[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--from", dest="t0", type=float, required=True)
    ap.add_argument("--to", dest="t1", type=float, required=True)
    ap.add_argument("--step", type=float, default=2.0)
    ap.add_argument("--tiles", required=True, help='"c,r c,r ..."')
    ap.add_argument("--half", type=int, default=26, help="取格子中心周围 ±half 像素")
    args = ap.parse_args()

    tiles = []
    for tok in args.tiles.split():
        c, r = (int(v) for v in tok.split(","))
        tiles.append((c, r))

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    prev = {}
    t = args.t0
    print("时刻      " + "  ".join("%d,%d" % (c, r) for c, r in tiles))
    while t <= args.t1:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        marks = []
        for c, r in tiles:
            x, y = tile_px(c, r)
            x, y = int(x), int(y)
            if not (0 <= x < 1280 and 60 <= y < 600):
                marks.append("  --  ")
                continue
            patch = g[y - args.half:y + args.half, x - args.half:x + args.half]
            dark = float((patch < 70).mean())          # 暗色占比 = 有单位
            key = (c, r)
            chg = 0.0
            if key in prev:
                chg = float(np.abs(patch - prev[key]).mean())
            prev[key] = patch
            tag = "##" if (dark > 0.06 and chg > 12) else ("++" if dark > 0.06 else ("::" if chg > 20 else " ."))
            marks.append("%s%2d" % (tag, int(dark * 100)))
        print("t=%5.0fs  " % t + " ".join(marks))
        t += args.step
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
