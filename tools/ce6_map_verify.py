"""校验「MAA 的格子 -> 屏幕坐标」映射对不对 —— 用游戏自己画的红门/蓝门做地面真值。

为什么（2026-09-21）：v30/v31 两局都在**开打后约 13 秒**就 3 点掉光失败（猎犬pro 移速1.9，
从出怪口走到门只要约 10 秒）⇒ 现象上像"什么都没挡住"。要么干员没放下去，要么**放错了格子**。
本脚本用纯像素统计（不依赖看图能力）：
  1. 从录像里取一帧战斗画面；
  2. 找出**红色**（出怪口 x=7）与**蓝色**（门 x=0）的色块质心；
  3. 用 MAA 实际点击坐标反推出的仿射映射，把屏幕坐标换算回格子；
  4. 看红色是不是落在 x=7、蓝色是不是落在 x=0 —— 不对就是映射/坐标约定错了。

用法:
    python tools/ce6_map_verify.py runtime/_ce6/v30b.mp4 --t 100
"""
from __future__ import annotations

import argparse
import json
import pathlib

import cv2
import numpy as np

# MAA 实际 swipe 目标（v30b 日志实测）：(col,row) -> screen
MAA_SAMPLES = {
    (1, 1): (500.0, 274.0),
    (1, 2): (494.0, 352.0),
    (2, 2): (586.0, 344.0),
    (2, 3): (583.0, 430.0),
}


def fit_affine():
    """解 screen = origin + col*dcol + row*drow（最小二乘）。"""
    A, bx, by = [], [], []
    for (c, r), (sx, sy) in MAA_SAMPLES.items():
        A.append([1.0, c, r])
        bx.append(sx)
        by.append(sy)
    A = np.array(A)
    coef_x, *_ = np.linalg.lstsq(A, np.array(bx), rcond=None)
    coef_y, *_ = np.linalg.lstsq(A, np.array(by), rcond=None)
    return coef_x, coef_y


def screen_to_tile(coef_x, coef_y, sx, sy):
    """数值反解 (col,row)：screen = [o + c*dc + r*dr] 是非线性耦合，直接网格搜索最近格。"""
    best, ans = 1e18, None
    for c in np.arange(-2, 10, 0.02):
        for r in np.arange(-2, 8, 0.02):
            px = coef_x[0] + coef_x[1] * c + coef_x[2] * r
            py = coef_y[0] + coef_y[1] * c + coef_y[2] * r
            d = (px - sx) ** 2 + (py - sy) ** 2
            if d < best:
                best, ans = d, (c, r)
    return ans, best ** 0.5


def blobs(mask, min_area=40):
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    out = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out.append((cent[i][0], cent[i][1], stats[i, cv2.CC_STAT_AREA]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--t", type=float, default=100.0, help="取第几秒的帧（战斗进行中）")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(args.t * fps))
    ok, fr = cap.read()
    cap.release()
    if not ok:
        raise SystemExit("✗ 取不到帧（t=%.1fs）" % args.t)

    hsv = cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)
    # 红色（出怪口）与蓝色（门）在 HSV 里的两段
    red = ((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170)) & (hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 110)
    blue = (hsv[:, :, 0] > 95) & (hsv[:, :, 0] < 135) & (hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 110)

    cx, cy = fit_affine()
    print("仿射拟合 screen = o + col*dc + row*dr")
    print("  x: o=%.1f dc=%.1f dr=%.1f" % (cx[0], cx[1], cx[2]))
    print("  y: o=%.1f dc=%.1f dr=%.1f" % (cy[0], cy[1], cy[2]))

    for name, mask in (("红(出怪口)", red), ("蓝(门)", blue)):
        bl = blobs(mask, 60)
        bl.sort(key=lambda b: -b[2])
        print("\n== %s：找到 %d 个色块 ==" % (name, len(bl)))
        for x, y, a in bl[:8]:
            (c, r), err = screen_to_tile(cx, cy, x, y) if False else ((0, 0), 0)
            # 用最小二乘系数直接反解（近似：网格搜索太慢，这里用线性反解）
            M = np.array([[cx[1], cx[2]], [cy[1], cy[2]]])
            v = np.array([x - cx[0], y - cy[0]])
            cr = np.linalg.solve(M, v)
            print("   screen(%.0f,%.0f) 面积%-5d -> 格子 col=%.2f row=%.2f" % (x, y, a, cr[0], cr[1]))

    if args.out:
        vis = fr.copy()
        for m, color in ((red, (0, 0, 255)), (blue, (255, 0, 0))):
            vis[m] = color
        cv2.imwrite(str(args.out), vis)
        print("\n→ %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
