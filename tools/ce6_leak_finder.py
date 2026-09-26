"""从失败录像里定位「第几秒漏的」—— 靠"整屏网格差分 + 事件计数"自动找出生命点图标。

为什么（2026-09-21 用户质问：没通关就该看录屏分析）：
    我之前只在录像上读了击杀/费用/剩余，**始终没定位到生命点图标**（试过猜 ROI，全错），
    所以"第几秒漏、漏哪一路"一直答不出来 —— 复盘就断在这儿。
本工具换个稳妥办法：不再猜位置，而是**把整屏切成小格**，每格统计"变化事件"的次数；
    生命点图标在 3 点掉光的一局里恰好变化 3 次（3→2→1→0），其它动画区要么一直动、要么不动。
    于是"事件数 ≈ 1~4 且位置在左上 HUD 区"的格子就是它。

用法:
    python tools/ce6_leak_finder.py runtime/_ce6/star1.mp4
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np

GRID = (32, 18)          # 把画面切成 32x18 格


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--fps", type=float, default=2.0, help="采样帧率（越小越快）")
    ap.add_argument("--thr", type=float, default=10.0, help="单格差分阈值")
    ap.add_argument("--gap", type=float, default=2.0, help="事件最小间隔（秒）")
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float, default=1e9)
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) / src_fps
    step = max(1, int(src_fps / args.fps))
    gw, gh = GRID

    prev = None
    events: dict[tuple[int, int], list[float]] = {}
    PROC = [0, 0.0]      # [已处理帧数, 观察到的最大单格差分]（调试用）
    # ⚠️ 别用"逐帧 read + idx%step 跳过"那套：实测它会莫名只处理十几帧、差分还接近 0 ✗。
    #    直接按时间 seek 取样，简单可预测。
    step_s = 1.0 / max(0.1, args.fps)
    t = args.t0
    end = min(args.t1, total)
    while t <= end:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * src_fps))
        ok, fr = cap.read()
        if not ok:
            break
        small = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (gw * 8, gh * 8), interpolation=cv2.INTER_AREA)
        cells = small.reshape(gh, 8, gw, 8).mean(axis=(1, 3))
        if prev is not None:
            d = np.abs(cells - prev)
            PROC[0] += 1
            PROC[1] = max(PROC[1], float(d.max()))
            for gy in range(gh):
                for gx in range(gw):
                    v = float(d[gy, gx])
                    if v >= args.thr:
                        ev = events.setdefault((gx, gy), [])
                        if not ev or (t - ev[-1]) >= args.gap:
                            ev.append(round(t, 1))
        prev = cells
        t += step_s
    cap.release()

    print("%s 片长 %.1fs；采到 %d 个有事件的格子（处理帧 %d，最大差分 %.1f）"
          % (args.video.name, total, len(events), PROC[0], PROC[1]))
    cands = [(k, v) for k, v in events.items() if 1 <= len(v) <= 4]
    cands.sort(key=lambda kv: (kv[0][1], kv[0][0]))
    print("\n事件数 1~4 的格子（生命点这类只在特定时刻变化的 UI 就在这里）:")
    bw, bh = 1280 / gw, 720 / gh
    for (gx, gy), ev in cands:
        cx, cy = int((gx + 0.5) * bw), int((gy + 0.5) * bh)
        print("   格(%2d,%2d) 屏幕≈(%4d,%4d)  事件%d次: %s" % (gx, gy, cx, cy, len(ev), ev))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
