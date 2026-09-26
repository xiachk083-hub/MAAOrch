"""定位「第几秒漏了怪」：生命值 HUD 区域只在漏怪时变化，逐秒比对像素即可。

用法:
    python tools/bi_leak_finder.py runtime/_ce6/bi5c.mp4
    python tools/bi_leak_finder.py runtime/_ce6/bi5c.mp4 --show 3    # 把变化前后的生命值区域打成 ASCII

配套 HUD 区域取自 data/battle_ui_layout.json：hud.life container [672,10,770,52]。
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
LIFE = (664, 6, 800, 58)      # x0, y0, x1, y1（比 layout 稍宽松）
KILLS = (470, 4, 660, 58)


def ascii_crop(img, box, w=60):
    x0, y0, x1, y1 = box
    g = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    h = max(6, int(g.shape[0] / g.shape[1] * w * 0.45))
    s = cv2.resize(g, (w, h))
    chars = " .:-=+*#%@"
    return ["".join(chars[min(9, int(v) * 10 // 256)] for v in row) for row in s]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--step", type=float, default=1.0)
    ap.add_argument("--thresh", type=float, default=6.0, help="生命值区域平均像素差阈值")
    ap.add_argument("--show", type=int, default=0, help="每次变化打印前后各 N 个 ASCII 图")
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = n / fps
    print("%s 片长 %.1fs  逐 %.1fs 扫生命值区域 %s" % (args.video.name, dur, args.step, LIFE))

    prev = None
    events = []
    t = 0.0
    while t < dur:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, f = cap.read()
        if not ok:
            break
        x0, y0, x1, y1 = LIFE
        cur = cv2.cvtColor(f[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            d = float(np.abs(cur - prev).mean())
            if d >= args.thresh:
                events.append((t, d, f.copy()))
                print("  ⚠ t=%6.1fs  生命值区域变化 %.1f  ⇒ 疑似漏怪" % (t, d))
        prev = cur
        t += args.step

    print("共 %d 次生命值变化" % len(events))
    if args.show:
        for t, d, f in events[: args.show]:
            print("---- t=%.0fs 变化后画面里的生命值区域 ----" % t)
            for line in ascii_crop(f, LIFE):
                print("   " + line)
            print("     同帧击杀计数区域：")
            for line in ascii_crop(f, KILLS, w=70):
                print("   " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
