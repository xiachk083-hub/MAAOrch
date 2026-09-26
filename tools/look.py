#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一眼看清屏幕 —— **单进程**版快照工具（2026-09-24，为"查得慢"而写）

为什么慢、为什么要有它：
  原来"看一眼屏幕"要串三个进程（`enter_event` → `mt_nav` → `panel_gate`），
  而**每个进程都要重新加载一遍 RapidOCR 模型（约 2~4 秒）**，再加 adb 截屏 ~0.5 秒，
  一次多步检查就是 20~60 秒。本工具把"截屏 + OCR + 面板字段"放在**同一个进程**里做完，
  一次调用 ~2~3 秒。

用法:
    python tools/look.py                 # 打印屏上文字（前 12 条）
    python tools/look.py --gate          # 顺带算面板字段：stars / reward_primes
    python tools/look.py --json          # 机器可读
    python tools/look.py --loop 30       # 盯 30 秒（每 2 秒一帧），用来等页面切换
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
sys.path.insert(0, str(ROOT / "tools"))
from _dev import resolve_dev                      # noqa: E402

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
SHOT = ROOT / "runtime" / "_look.png"
_eng = None


def engine():
    global _eng
    if _eng is None:
        from rapidocr_onnxruntime import RapidOCR
        _eng = RapidOCR()
    return _eng


def grab(dev: str) -> "object":
    import cv2
    with open(SHOT, "wb") as f:
        subprocess.run([ADB, "-s", dev, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return cv2.imread(str(SHOT))


def hits(img):
    res, _ = engine()(img)
    out = []
    for box, text, *_ in (res or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append((int(sum(xs) / len(xs)), int(sum(ys) / len(ys)), text.strip()))
    return out


def stars_of(img) -> int:
    """面板三星：ROI y[60:150] x[980:1260] 里蓝色像素面积 / 1470（与 panel_gate 同口径）。"""
    import cv2
    import numpy as np
    roi = img[60:150, 980:1260]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, np.array([90, 80, 120]), np.array([130, 255, 255]))
    return int(round(blue.sum() / 255 / 490))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="1")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--loop", type=float, default=0, help="盯屏秒数")
    ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args()
    dev = resolve_dev(a.emu)

    t0 = time.time()
    frames = []
    deadline = time.time() + (a.loop or 0)
    while True:
        img = grab(dev)
        hs = hits(img)
        rec = {"texts": [t for _, _, t in hs]}
        if a.gate and img is not None:
            rec["stars"] = stars_of(img)
        frames.append(rec)
        if time.time() >= deadline:
            break
        time.sleep(a.interval)

    cost = time.time() - t0
    last = frames[-1]
    if a.json:
        print(json.dumps({"frames": len(frames), "cost_s": round(cost, 1), **last}, ensure_ascii=False))
    else:
        print(f"[{time.strftime('%H:%M:%S')}] {len(frames)} 帧 / {cost:.1f}s" + (f"  stars={last.get('stars')}" if a.gate else ""))
        print(" | ".join(last["texts"][:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
