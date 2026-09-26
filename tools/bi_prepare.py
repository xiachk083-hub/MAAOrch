"""把游戏弄回「BI-EX-1 关卡详情页 + 突袭已开」——跑局前的准备工作。

为什么要它（2026-09-21）：`stage_attempt.py` 假定游戏已停在关卡页；而一局跑完后会停在结算页，
关卡列表里 BI-EX-1 节点的位置每屏还会变。这份脚本用 OCR 找节点位置、再确认突袭开关状态。

用法:
    python tools/bi_prepare.py [--stage-text BI-EX-1]
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import time

import cv2
from rapidocr_onnxruntime import RapidOCR

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
OUT = PROJ / "runtime" / "_ce6"
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
DEV = "127.0.0.1:16416"   # #1（官服）
TOGGLE = (677, 643)      # 「突袭模式 / 普通难度」按钮

_engine = None


def ocr(img):
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    res, _ = _engine(img)
    return res or []


def tap(x, y, wait=3.0):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", str(x), str(y)], timeout=30)
    time.sleep(wait)


def shot(name):
    p = OUT / f"{name}.png"
    with open(p, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return cv2.imread(str(p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage-text", default="BI-EX-1")
    args = ap.parse_args()

    # 1) 连续点几下把结算页/弹层消掉
    for _ in range(2):
        tap(640, 640, wait=2.5)

    # 2) 找关卡节点（rapidocr 返回 [四点多边形, 文本, 分数]，取框中心）
    img = shot("_bi_prep1")
    hits = []
    for box, text, *_ in ocr(img):
        if args.stage_text in text:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            hits.append((int(sum(xs) / len(xs)), int(sum(ys) / len(ys)), text))
    if not hits:
        print("✗ 这一屏没看到 %s（也许已经在关卡详情页，或还在结算页）" % args.stage_text)
    else:
        x, y, text = hits[0]
        print("找到 %s 于 (%d,%d) ⇒ 点进去" % (text, x, y))
        tap(x, y, wait=4.0)

    # 3) 确认突袭开关
    img = shot("_bi_prep2")
    txt = " ".join(t[1] for t in ocr(img))
    if "开始突袭" in txt or "普通难度" in txt:
        print("✓ 已在突袭状态（按钮=%s）" % ("普通难度" if "普通难度" in txt else "开始突袭"))
        return 0
    if "突袭模式" in txt:
        print("… 当前是普通难度 ⇒ 点突袭开关")
        tap(*TOGGLE, wait=3.0)
        img = shot("_bi_prep3")
        txt = " ".join(t[1] for t in ocr(img))
        ok = ("开始突袭" in txt) or ("普通难度" in txt)
        print("%s 突袭=%s" % ("✓" if ok else "✗", ok))
        return 0 if ok else 1
    print("? 认不出这一屏（关键词都没出现）⇒ 请人工看一眼")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
