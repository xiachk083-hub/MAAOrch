"""把游戏弄回「目标关卡的详情页」——用于掐局/失败之后自动回到可再跑的状态。

处理三种情况：结算页（点掉）、失败页「任务失败/点击画面以继续」（点掉）、已经在关卡页（不动）。
认不出来时最多按 3 次返回，再认不出就报错。

用法:
    python tools/bi_back_to_stage.py --stage BI-EX-5
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

_eng = None


def ocr(img):
    global _eng
    if _eng is None:
        _eng = RapidOCR()
    res, _ = _eng(img)
    return [(int(sum(q[0] for q in b) / len(b)), int(sum(q[1] for q in b) / len(b)), t)
            for b, t, *_ in (res or [])]


def tap(x, y, w=3.0):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", str(x), str(y)], timeout=30)
    time.sleep(w)


def back(w=3.0):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "keyevent", "4"], timeout=30)
    time.sleep(w)


def shot(name="_nav"):
    p = OUT / f"{name}.png"
    with open(p, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return cv2.imread(str(p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    args = ap.parse_args()
    name = args.stage

    for i in range(8):
        img = shot()
        hits = ocr(img)
        txt = " ".join(t for _, _, t in hits)
        if "开始行动" in txt or "开始突袭" in txt:
            print("✓ 已在 %s 详情页（第 %d 步）" % (name, i))
            if "开始突袭" in txt:
                print("  （当前是突袭模式）")
            return 0
        if "点击画面以继续" in txt or "任务失败" in txt or "行动结束" in txt or "MISSION" in txt:
            print("  [%d] 结算/失败页 ⇒ 点画面继续" % i)
            tap(640, 640, 3.5)
            continue
        if name in txt and ("OPERATION" in txt or "前往" in txt):
            print("  [%d] 在地图上 ⇒ 点 %s 节点" % (i, name))
            node = [(x, y) for x, y, t in hits if name in t]
            if node:
                tap(node[0][0], node[0][1] - 20, 4.0)
                continue
        print("  [%d] 认不出（%s）⇒ 按返回" % (i, txt[:60]))
        back(3.5)
    print("✗ 8 步之内没能回到 %s 详情页，请人工看一眼" % name)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
