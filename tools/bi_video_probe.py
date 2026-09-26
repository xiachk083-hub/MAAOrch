"""从跑局录像里按时间点抓帧、OCR 顶部 HUD，看「第几秒漏了几只」。

用法:
    python tools/bi_video_probe.py runtime/_ce6/bi3a.mp4 --times 100 110 120 ... [--save]
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
from rapidocr_onnxruntime import RapidOCR

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--times", nargs="*", type=float, required=True)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--hud-only", action="store_true", default=True, help="只看顶部 HUD 条")
    args = ap.parse_args()
    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    eng = RapidOCR()
    out = PROJ / "runtime" / "_ce6" / "frames"
    out.mkdir(exist_ok=True)
    for t in args.times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            print("t=%.0fs 读不到帧" % t)
            continue
        img = frame[0:110, :] if args.hud_only else frame
        big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        res, _ = eng(big)
        txt = " ｜ ".join(x[1] for x in (res or []))
        print("t=%5.0fs  %s" % (t, txt or "（HUD 没读到字）"))
        if args.save:
            cv2.imwrite(str(out / ("%s_t%03d.png" % (args.video.stem, int(t)))), frame)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
