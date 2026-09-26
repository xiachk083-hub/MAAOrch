"""逐段量「场上站了几个人」+「击杀数」—— 用来做失败局的复盘时间线。

为什么（2026-09-21 用户质问：没通关就该看录屏分析）：
    之前的"剩余可放置角色"读数含糊（既能解释成未部署人数、也能解释成付得起费用的人数），
    而**底部卡槽在干员上场后会明显变暗** —— 直接量它最可信。配上击杀数，就能看出一局是
    "人没上去"还是"上去了但打不动"。

用法:
    python tools/ce6_deploy_timeline.py runtime/_ce6/star1.mp4 --from 50 --to 90 --step 5
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

BAR = (600, 1280, 630, 690)     # 底部卡槽带
KILL = (460, 0, 720, 50)        # 顶部中央 x/41（x0,y0,x1,y1）

_engine = None


def ocr(img):
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    res, _ = _engine(img)
    return "".join(t[1] for t in res) if res else ""


def bar_gray(fr):
    x0, x1, y0, y1 = BAR
    return cv2.cvtColor(fr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float, default=1e9)
    ap.add_argument("--step", type=float, default=5.0)
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    base = None
    print("%s（片长 %.1fs）" % (args.video.name, total))
    print("%6s %10s %8s" % ("时刻", "卡槽变暗数", "击杀"))
    t = args.t0
    while t <= min(args.t1, total):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, fr = cap.read()
        if not ok:
            break
        g = bar_gray(fr)
        if base is None:
            base = g.astype(np.int16)      # 开打前/开打瞬间作为基准（所有卡都亮）
            dim = 0
        else:
            # 按列比较：变暗超过阈值的连续区块算一张已上场的卡
            d = base - g.astype(np.int16)
            col = d.mean(axis=0)
            on = col > 18
            segs, start = [], None
            for i, v in enumerate(on):
                if v and start is None:
                    start = i
                elif not v and start is not None:
                    if i - start >= 45:
                        segs.append(i - start)
                    start = None
            dim = len(segs)
        x0, y0, x1, y1 = KILL      # ⚠️ 我第一版按 (x0,x1,y0,y1) 解包，切出空图直接崩 ✗
        crop = cv2.resize(fr[y0:y1, x0:x1], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        kills = ocr(crop).replace(" ", "")
        print("%6.1f %10d %8s" % (t, dim, kills[:12]))
        t += args.step
    cap.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
