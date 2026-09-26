"""按 HUD 区域读战斗录像：生命值 / 击杀 / 费用，逐时刻打印——用来定位"第几秒漏了几只"。

区域取自 data/battle_ui_layout.json（internal 1280×720）：
  hud.life         [672, 10, 770, 52]
  hud.enemy_count  [478,  8, 608, 54]

用法:
    python tools/bi_hud_watch.py runtime/_ce6/bi3b.mp4 --from 88 --to 180 --step 3
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
from rapidocr_onnxruntime import RapidOCR

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
REGIONS = {"life": (664, 6, 780, 58), "kills": (470, 4, 616, 58), "cost": (1140, 480, 1230, 540)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float, default=1e9)
    ap.add_argument("--step", type=float, default=3.0)
    ap.add_argument("--who", nargs="*", default=["life", "kills"])
    args = ap.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    eng = RapidOCR()
    t = args.t0
    while t <= args.t1:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            break
        parts = []
        for w in args.who:
            x0, y0, x1, y1 = REGIONS[w]
            crop = frame[y0:y1, x0:x1]
            big = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            res, _ = eng(big)
            txt = "".join(x[1] for x in (res or []))
            parts.append("%s=%s" % (w, txt or "?"))
        print("t=%6.1fs  %s" % (t, "   ".join(parts)))
        t += args.step
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
