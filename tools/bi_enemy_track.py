"""从录像里量出敌人的**实际轨迹**（不是关卡数据推的），并换算回格子。

做法：冰面地图底色很亮 ⇒ 敌方单位是暗色块。逐帧在战场区找暗色连通块，取质心，
再用「MAA 部署手势标定出来的格子↔像素仿射映射」换算成格子坐标，按时间打印。

用法:
    python tools/bi_enemy_track.py runtime/_ce6/bi5h.mp4 --from 110 --to 160 --step 2 \
        --map-x "84,1,295" --map-y "-8.5,86.5,72.5"
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
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float, default=1e9)
    ap.add_argument("--step", type=float, default=2.0)
    ap.add_argument("--dark", type=int, default=70, help="暗色阈值（V 小于它算单位）")
    ap.add_argument("--min-area", type=int, default=350)
    ap.add_argument("--map-x", default="84,1,295", help="x = a*col + b*row + c")
    ap.add_argument("--map-y", default="-8.5,86.5,72.5", help="y = d*col + e*row + f")
    ap.add_argument("--hud-skip", type=int, default=100, help="顶部多少像素是 HUD，跳过")
    args = ap.parse_args()

    ax, bx, cx = (float(v) for v in args.map_x.split(","))
    ay, by, cy = (float(v) for v in args.map_y.split(","))

    def to_tile(px, py):
        # 解二元一次方程组
        det = ax * by - ay * bx
        if abs(det) < 1e-6:
            return None
        u = px - cx
        v = py - cy
        col = (u * by - v * bx) / det
        row = (ax * v - ay * u) / det
        return col, row

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    t = args.t0
    while t <= args.t1:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, f = cap.read()
        if not ok:
            break
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2]
        m = (v < args.dark).astype(np.uint8) * 255
        m[: args.hud_skip, :] = 0
        m[600:, :] = 0
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
        blobs = []
        for i in range(1, n):
            a = stats[i, 4]
            if a < args.min_area:
                continue
            x, y = cent[i]
            tt = to_tile(x, y)
            if tt is None:
                continue
            col, row = tt
            if not (-1 <= col <= 12 and -1 <= row <= 9):
                continue
            blobs.append((a, x, y, col, row))
        blobs.sort(key=lambda b: -b[0])
        desc = "  ".join("(%.1f,%.1f) 面积%d" % (c, r, a) for a, x, y, c, r in blobs[:6])
        print("t=%6.1fs  %s" % (t, desc or "（没找到暗色块）"))
        t += args.step
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
