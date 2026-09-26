"""看一眼当前屏并把 OCR 文本（带坐标）打出来——导航/找按钮时的通用小工具。

用法:
    python tools/bi_peek.py [--keys 确认 开始行动 突袭] [--scale 1.4]
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess

import cv2
from rapidocr_onnxruntime import RapidOCR

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
OUT = PROJ / "runtime" / "_ce6"
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
DEV = "127.0.0.1:16416"   # #1（官服）


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", nargs="*", default=None)
    ap.add_argument("--scale", type=float, default=1.4)
    args = ap.parse_args()

    p = OUT / "_peek.png"
    with open(p, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    img = cv2.imread(str(p))
    big = cv2.resize(img, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_CUBIC)
    res, _ = RapidOCR()(big)
    rows = []
    for b, t, *_ in (res or []):
        if len(t.strip()) <= 1:
            continue
        x = int(sum(q[0] for q in b) / len(b) / args.scale)
        y = int(sum(q[1] for q in b) / len(b) / args.scale)
        if args.keys and not any(k in t for k in args.keys):
            continue
        rows.append((y, x, t))
    for y, x, t in sorted(rows):
        print("%4d %4d  %s" % (y, x, t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
