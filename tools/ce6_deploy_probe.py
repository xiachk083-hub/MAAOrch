"""部署落点核验：对比「部署前/后」两帧，找出新出现的干员立绘质心，再换算成格子。

为什么（2026-09-21）：MAA 日志只证明它**发了拖拽手势**，不证明干员真落到那一格。
这个脚本用画面差分直接看"魔术师到底从哪冒出来"，把位置换算成格子与作业里的 location 对比。
换算用 MAA 实际 swipe 目标拟合出来的仿射映射（见 tools/ce6_map_verify.py 里的样本）。

用法:
    python tools/ce6_deploy_probe.py runtime/_ce6/v36a.mp4 --before 65.5 --after 67.0
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np

# MAA swipe 目标拟合：screen = o + col*dc + row*dr
O = (411.7, 198.0)
DC = (92.0, -8.0)
DR = (-4.5, 82.0)
FIELD = (300, 1100, 90, 620)   # x0,x1,y0,y1 战场大致范围


def tile_to_screen(col, row):
    return (O[0] + col * DC[0] + row * DR[0], O[1] + col * DC[1] + row * DR[1])


def screen_to_tile(sx, sy):
    M = np.array([[DC[0], DR[0]], [DC[1], DR[1]]])
    v = np.array([sx - O[0], sy - O[1]])
    return np.linalg.solve(M, v)


def frame_at(cap, t, fps):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
    ok, fr = cap.read()
    return fr if ok else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--before", type=float, required=True)
    ap.add_argument("--after", type=float, required=True)
    ap.add_argument("--min-area", type=int, default=300)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    a = frame_at(cap, args.before, fps)
    b = frame_at(cap, args.after, fps)
    cap.release()
    if a is None or b is None:
        raise SystemExit("✗ 取帧失败")

    x0, x1, y0, y1 = FIELD
    ga = cv2.cvtColor(a[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.int16)
    gb = cv2.cvtColor(b[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.int16)
    diff = np.abs(gb - ga).astype(np.uint8)
    _, mask = cv2.threshold(diff, 45, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=2)

    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    comps = []
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if area >= args.min_area:
            cx, cy = cent[i][0] + x0, cent[i][1] + y0
            (col, row) = screen_to_tile(cx, cy)
            comps.append((area, cx, cy, col, row))
    comps.sort(reverse=True)

    print("对比 %.1fs → %.1fs：找到 %d 个显著变化区" % (args.before, args.after, len(comps)))
    for area, cx, cy, col, row in comps[:args.top]:
        print("  面积%-7d 屏幕(%.0f,%.0f) -> 格子 col=%.2f row=%.2f" % (area, cx, cy, col, row))

    # 参考：各候选落点的屏幕位置
    print("\n参考（作业里用到的格子 -> 屏幕）:")
    for (c, r) in ((2, 2), (2, 3), (2, 4), (1, 1), (1, 2), (1, 3), (3, 2), (3, 3)):
        sx, sy = tile_to_screen(c, r)
        print("   (%d,%d) -> (%.0f, %.0f)" % (c, r, sx, sy))

    if args.out:
        vis = b.copy()
        cv2.rectangle(vis, (x0, y0), (x1, y1), (0, 255, 255), 1)
        for area, cx, cy, col, row in comps[:args.top]:
            cv2.circle(vis, (int(cx), int(cy)), 18, (0, 0, 255), 2)
            cv2.putText(vis, "%.1f,%.1f" % (col, row), (int(cx) - 30, int(cy) - 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        cv2.imwrite(str(args.out), vis)
        print("\n→ %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
