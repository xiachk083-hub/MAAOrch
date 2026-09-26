"""在结算页截图上找"横排三连的小图标"（= 星级行）——纯像素统计，不依赖看图。

背景（2026-09-21）：v40b 通关后，结算页的星星读不出 2 颗还是 3 颗。
这个脚本把全屏的"小尺寸明亮连通块"按 y 分组，找**同一行里 3 个尺寸相近、间距均匀**的候选，
并把它们的形状画成 ASCII，让人（或我）判断是不是星星。

用法:
    python tools/ce6_star_row.py runtime/_ce6/v40b_result.png
"""
from __future__ import annotations

import argparse
import itertools
import pathlib

import cv2
import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=pathlib.Path)
    ap.add_argument("--bright", type=int, default=150)
    ap.add_argument("--min-area", type=int, default=60)
    ap.add_argument("--max-area", type=int, default=1200)
    args = ap.parse_args()

    img = cv2.imread(str(args.image))
    if img is None:
        raise SystemExit("read fail: %s" % args.image)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    m = (g > args.bright).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)

    blobs = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        if args.min_area <= a <= args.max_area and 8 <= w <= 70 and 8 <= h <= 70:
            blobs.append((float(cent[i][0]), float(cent[i][1]), a, w, h, i))
    print("候选小图标 %d 个" % len(blobs))

    blobs.sort(key=lambda b: (round(b[1] / 18.0), b[0]))
    for ykey, grp in itertools.groupby(blobs, key=lambda b: round(b[1] / 18.0)):
        grp = list(grp)
        if len(grp) >= 3:
            xs = sorted(b[0] for b in grp)
            gaps = [round(xs[i + 1] - xs[i]) for i in range(len(xs) - 1)]
            print("  y≈%3d 数量%d x=%s 间距=%s" % (ykey * 18, len(grp), [round(x) for x in xs], gaps))

    # 把最像"横排三连"的那一组画成 ASCII
    best = None
    for ykey, grp in itertools.groupby(blobs, key=lambda b: round(b[1] / 18.0)):
        grp = list(grp)
        if len(grp) >= 3:
            areas = [b[2] for b in grp]
            xs = sorted(b[0] for b in grp)
            spread = xs[-1] - xs[0]
            if spread < 400 and max(areas) / max(1, min(areas)) < 3.0:
                best = grp
                break
    if best:
        y0 = int(min(b[1] for b in best)) - 18
        y1 = int(max(b[1] for b in best)) + 18
        x0 = int(min(b[0] for b in best)) - 25
        x1 = int(max(b[0] for b in best)) + 25
        sub = (g[max(0, y0):y1, max(0, x0):x1] > args.bright).astype(np.uint8)
        s = cv2.resize(sub, (max(1, (x1 - x0) // 3), max(1, (y1 - y0) // 3)), interpolation=cv2.INTER_AREA)
        print("\n最像星级行的一组（x=%d..%d y=%d..%d）:" % (x0, x1, y0, y1))
        for r in s:
            print("   " + "".join("#" if c > 0.35 else ("+" if c > 0.12 else ".") for c in r))
    else:
        print("\n未找到类似星级行的三连图标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
