#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""速度基准：把"看屏幕"这条链路的每一段拆开量一遍，找出还能砍哪里。

量四件事（都跑在**同一个进程**里，避免冷启动噪声）：
  1. adb 截屏：`screencap -p`（PNG，要解码）vs `screencap`（raw RGBA，直接 frombuffer）
  2. OCR：全屏 vs 局部 ROI（y 60..150 头部 / 报酬区）
  3. 纯像素判据（星星/徽章模板）—— 理论上最快的那条路
  4. MAA 自带的截图通道（asst.get_image，如果这个版本有）—— 走 socket，通常比 adb 快一个量级

用法: python tools/bench_look.py --emu 1
"""
from __future__ import annotations

import argparse
import pathlib
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
sys.path.insert(0, str(ROOT / "tools"))
from _dev import resolve_dev  # noqa: E402

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
PNG = ROOT / "runtime" / "_bench.png"


def timeit(fn, n=5, warm=1):
    for _ in range(warm):
        fn()
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(ts), 1), [round(x, 1) for x in ts]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="1")
    a = ap.parse_args()
    dev = resolve_dev(a.emu)
    import cv2
    import numpy as np

    # 1. 截屏两条路
    def shot_png():
        with open(PNG, "wb") as f:
            subprocess.run([ADB, "-s", dev, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
        return cv2.imread(str(PNG))

    def shot_raw():
        r = subprocess.run([ADB, "-s", dev, "exec-out", "screencap"], stdout=subprocess.PIPE, timeout=60)
        arr = np.frombuffer(r.stdout, dtype=np.uint8)
        return arr[16:].reshape((720, 1280, 4)) if arr.size >= 16 + 720 * 1280 * 4 else None

    png_ms, _ = timeit(shot_png)
    raw_ms, _ = timeit(shot_raw)
    print(f"截屏  PNG(exec-out -p)+imread : {png_ms} ms")
    print(f"截屏  raw RGBA + frombuffer  : {raw_ms} ms")

    img = shot_png()
    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR()

    full_ms, _ = timeit(lambda: eng(img), n=3)
    roi_head = img[60:150, 980:1260]
    roi_head_ms, _ = timeit(lambda: eng(roi_head), n=3)
    roi_reward = img[620:1280 if img.shape[0] > 1280 else img.shape[0] - 1, 430:860]
    roi_reward_ms, _ = timeit(lambda: eng(roi_reward), n=3)
    print(f"OCR   全屏 1280x720          : {full_ms} ms")
    print(f"OCR   头部 ROI 280x90        : {roi_head_ms} ms")
    print(f"OCR   报酬区 ROI 430x~660    : {roi_reward_ms} ms")

    def pixels():
        hsv = cv2.cvtColor(img[60:150, 980:1260], cv2.COLOR_BGR2HSV)
        blue = cv2.inRange(hsv, np.array([90, 80, 120]), np.array([130, 255, 255]))
        return int(blue.sum())
    px_ms, _ = timeit(pixels, n=10)
    tpl = ROOT / "data" / "templates" / "prime_badge.png"
    if tpl.exists():
        g = cv2.cvtColor(img[600:720, 400:900], cv2.COLOR_BGR2GRAY)
        t = cv2.imread(str(tpl), cv2.IMREAD_GRAYSCALE)

        def match():
            r = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
            return float(r.max())
        mt_ms, _ = timeit(match, n=10)
        print(f"像素  星星蓝色面积           : {px_ms} ms")
        print(f"模板  徽章匹配               : {mt_ms} ms")

    # 4. MAA 自带通道
    try:
        sys.path.insert(0, str(ROOT / "services" / "maa" / "instances" / "1" / "Python"))
        import asst  # type: ignore
        names = [n for n in dir(asst.Asst) if "image" in n.lower() or "shot" in n.lower()]
        print(f"MAA   asst 版本里和截图有关的方法: {names}")
    except Exception as e:                                   # noqa: BLE001
        print(f"MAA   asst 导入失败：{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
