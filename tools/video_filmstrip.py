"""把整局录像按固定间隔抽帧拼成"胶片"——人眼一眼看完一局，定位崩点。

为什么要有它（2026-09-20 交棒报告实测）：逐帧差分（analyze_run_video.py）能给"第几秒漏了"，
但**答不了"为什么漏"**（谁死了、哪一路空了、费用够不够）。胶片法 = 顺序读帧、每 N 秒拼一格
HUD+战场，一屏看完整局走势。

用法:
    python tools/video_filmstrip.py runtime/_ce6/v23a.mp4 --every 5 --start 40 --end 100 \
        --out runtime/_ce6/v23a_strip.png [--cols 4]
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--every", type=float, default=5.0, help="每隔几秒取一格")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=0.0, help="0 = 到片尾")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit("✗ 打不开视频：%s" % args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    end = args.end or total

    times = []
    t = args.start
    while t <= end:
        times.append(t)
        t += args.every

    tiles = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, fr = cap.read()
        if not ok:
            continue
        fr = cv2.resize(fr, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_AREA)
        cv2.rectangle(fr, (0, 0), (len(str(int(t))) * 12 + 14, 22), (0, 0, 0), -1)
        cv2.putText(fr, "%ds" % int(t), (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        tiles.append(fr)
    cap.release()

    if not tiles:
        raise SystemExit("✗ 没取到帧（start/end 是否超出片长 %.1fs？）" % total)

    h, w = tiles[0].shape[:2]
    rows = []
    for i in range(0, len(tiles), args.cols):
        chunk = tiles[i:i + args.cols]
        while len(chunk) < args.cols:
            chunk.append(np.zeros_like(tiles[0]))
        rows.append(np.hstack(chunk))
    sheet = np.vstack(rows)
    cv2.imwrite(str(args.out), sheet)
    print("✓ %s（片长 %.1fs，%d 格 @ %gs，每格 %dx%d）" % (args.out, total, len(tiles), args.every, w, h))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
