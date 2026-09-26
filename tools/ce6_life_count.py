"""数「生命点还剩几个」—— 左上角三个盾形图标（纯颜色统计，不依赖看图）。

为什么（2026-09-21）：CE-6 必须零漏，但一直搞不清"第几秒漏的、漏了几个"。生命点是图形不是文字，
OCR 读不到；但它们是**亮青色/蓝色**的小方块，和深色背景差异极大 ⇒ 数色块数量即可。
用法:
    python tools/ce6_life_count.py runtime/_ce6/v34a.mp4 --from 15 --to 90 --step 1
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np

# 左上角生命点区域（1280x720）：先给宽一点，再靠颜色筛
ROI = (0, 300, 0, 80)   # x0,x1,y0,y1


def count_life(fr):
    x0, x1, y0, y1 = ROI
    sub = fr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    # 亮青/蓝（生命点图标）；同时要求足够亮，避免误抓暗色 UI
    mask = ((h > 80) & (h < 140) & (s > 60) & (v > 120)).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    blobs = [(int(stats[i, cv2.CC_STAT_AREA]), float(cent[i][0]), float(cent[i][1]))
             for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 60]
    return blobs, mask


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--from", dest="t0", type=float, default=10.0)
    ap.add_argument("--to", dest="t1", type=float, default=100.0)
    ap.add_argument("--step", type=float, default=1.0)
    ap.add_argument("--dump", type=pathlib.Path, default=None, help="把某个时刻的掩码存图，便于核对 ROI")
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    print("片长 %.1fs，fps %.1f" % (total, fps))

    rows = []
    t = args.t0
    while t <= min(args.t1, total):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, fr = cap.read()
        if not ok:
            break
        blobs, mask = count_life(fr)
        rows.append((t, len(blobs), sum(b[0] for b in blobs)))
        t += args.step
    cap.release()

    # 打印计数序列（只在变化时换行，便于一眼看趋势）
    prev = None
    for t, n, area in rows:
        mark = "  <== 变化" if prev is not None and n != prev else ""
        if prev is None or n != prev or t % 5 == 0:
            print("  t=%5.1fs  色块数=%d  总面积=%d%s" % (t, n, area, mark))
        prev = n
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
