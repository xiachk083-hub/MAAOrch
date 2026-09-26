"""从录像里量「格子 ↔ 屏幕像素」映射：部署时游戏会高亮射程预览，高亮块的网格就是格子。

用法:
    python tools/bi_grid_probe.py runtime/_ce6/bi3b.mp4 --ref 114 --times 117 118 119
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--ref", type=float, required=True, help="参考帧（无高亮）")
    ap.add_argument("--times", nargs="*", type=float, required=True)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    def grab(t):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, f = cap.read()
        return f if ok else None

    ref = grab(args.ref)
    if ref is None:
        raise SystemExit("读不到参考帧")
    for t in args.times:
        f = grab(t)
        if f is None:
            print("t=%.1f 读不到" % t)
            continue
        d = cv2.absdiff(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY))
        mask = (d > 25).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
        blobs = [(stats[i, 4], stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3]) for i in range(1, n)
                 if stats[i, 4] > 400]
        blobs.sort(reverse=True)
        print("t=%6.1fs  变化块 %d 个（面积>400）：" % (t, len(blobs)))
        for area, x, y, w, h in blobs[:12]:
            print("    面积%6d  框(x=%4d y=%4d w=%3d h=%3d)  中心(%4d,%4d)"
                  % (area, x, y, w, h, x + w // 2, y + h // 2))
        if args.save:
            out = PROJ / "runtime" / "_ce6" / "frames"
            out.mkdir(exist_ok=True)
            cv2.imwrite(str(out / ("%s_t%03d.png" % (args.video.stem, int(t)))), f)
            cv2.imwrite(str(out / ("%s_t%03d_diff.png" % (args.video.stem, int(t)))), mask)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
