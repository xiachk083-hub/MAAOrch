"""从录像直接数「场上到底站了几个干员」—— 用底部卡槽的变暗程度，不依赖那个含糊的计数。

为什么（2026-09-21）：战斗 HUD 的「剩余可放置角色：N」既可以解释成"没部署的人数"，
也可以解释成"现在付得起费用的人数" —— 两种解释在我手上的数据里都自洽，害我判断反复。
底部那排干员卡**部署后会明显变暗/褪色**，所以直接量它最可靠：
取开打瞬间与稍后两帧，比较卡槽区域的亮度下降；变暗的卡数 ≈ 已上场人数。

用法:
    python tools/ce6_deployed_count.py runtime/_ce6/v40b.mp4 --t0 72 --t1 100
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np

BAR = (560, 1280, 628, 692)   # x0,x1,y0,y1 —— 底部卡槽带


def frame(cap, t, fps):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
    ok, fr = cap.read()
    return fr if ok else None


def cards(img):
    x0, x1, y0, y1 = BAR
    band = img[y0:y1, x0:x1]
    g = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    # 卡槽通常是亮底；按列亮度切出"卡片"块
    col = g.mean(axis=0)
    thr = max(30.0, float(col.mean()) * 0.75)
    on = col > thr
    segs, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= 40:
                segs.append((start + x0, i + x0))
            start = None
    if start is not None and len(on) - start >= 40:
        segs.append((start + x0, len(on) + x0))
    return segs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--t0", type=float, required=True, help="开打瞬间（所有卡都亮）")
    ap.add_argument("--t1", type=float, required=True, help="稍后（已上场的人卡会变暗）")
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    a, b = frame(cap, args.t0, fps), frame(cap, args.t1, fps)
    cap.release()
    if a is None or b is None:
        raise SystemExit("取帧失败")

    sa, sb = cards(a), cards(b)
    print("%s：t=%.1f 卡块 %d 个；t=%.1f 卡块 %d 个" % (args.video.name, args.t0, len(sa), args.t1, len(sb)))
    x0, x1, y0, y1 = BAR
    ga = cv2.cvtColor(a[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.int16)
    gb = cv2.cvtColor(b[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.int16)
    dim = 0
    for (s, e) in sa:
        i0, i1 = s - x0, e - x0
        d = float(ga[:, i0:i1].mean() - gb[:, i0:i1].mean())
        flag = "变暗(已部署)" if d > 12 else ("无变化" if d > -8 else "变亮")
        if d > 12:
            dim += 1
        print("   卡槽 x=%4d..%4d  亮度变化 %+6.1f  %s" % (s, e, -d, flag))
    print("=> 估算已部署 ≈ %d 人" % dim)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
